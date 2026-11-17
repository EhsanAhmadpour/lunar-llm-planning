"""The "LLM translates, classical planner plans" pipeline (condition nl-translate).

This is the pattern of E et al. (2026) in Section 3 of the report: the LLM turns a natural-language
mission into a formal model, and a deterministic component (here the classical planner pyperplan)
does the planning. The LLM never writes the plan.

For one model reply, `check_translation` does, in order:
  1. take the PDDL problem from the last ```pddl block of the reply;
  2. read it with pyperplan's parser and check every fact against the domain (known predicate,
     right number of arguments, declared objects of the right type) -- pyperplan itself silently
     accepts unknown predicates and wrong arities, so we check those ourselves;
  3. compare its facts with the hand-written problem file: which init/goal facts are missing or extra
     (only for the tables; the model is never told what the true facts are);
  4. write the problem back out in a canonical order (objects, init and goal facts sorted) and run the
     classical planner Fast Downward on it (src/fd.py; 30 s per configuration). Because of the canonical
     order, a perfect translation gives the planner exactly the same input as the hand-written file;
  5. check the planner's plan with our validator against the TRUE (hand-written) problem -- the
     equivalent of E et al.'s simulator, which checks a schedule against the real mission.

The result has the same keys as validate_text (valid, error_category, error_message, plan_length, ...)
so the runner and the analysis treat both conditions alike, plus translation details.
error_message is the sentence sent back to the model in a feedback round:
    could not be read     -> category "syntax"
    planner found no plan -> "unsolvable" (proved) or "timeout"
    plan fails on the real mission -> the validator's category (terrain, battery, ...)

Planner results are cached in results/translate_cache/<sha256 of reply>.json, so re-running the analysis
gives the same numbers on any machine and never re-plans.
"""
import hashlib
import json
import re
import tempfile
from pathlib import Path

from . import fd
from .pddl_utils import ROOT
from .validate import _is_subtype, load_task, validate_text

PLANNER_TIMEOUT_S = 30   # per Fast Downward configuration (two configurations: at most about 60 s)
_PDDL_FENCE = re.compile(r"```[ \t]*(?:pddl|lisp|scheme)?[ \t]*\r?\n(.*?)(?:```|\Z)", re.I | re.S)


def extract_problem(text):
    """Problem text from the last fenced block that contains '(define', else from the bare reply."""
    blocks = [b for b in _PDDL_FENCE.findall(text or "") if "(define" in b.lower()]
    if blocks:
        return blocks[-1]
    i = (text or "").lower().find("(define")
    return text[i:] if i >= 0 else None


def _facts(problem):
    init = {(a.name, *[s[0] for s in a.signature]) for a in problem.initial_state}
    goal = {(a.name, *[s[0] for s in a.signature]) for a in problem.goal}
    return init, goal


def _fmt(f):
    return "(" + " ".join(f) + ")"


def lint(domain, problem):
    """First fact that pyperplan accepted but that does not fit the domain, as a sentence (or None)."""
    for where, atoms in (("init", problem.initial_state), ("goal", problem.goal)):
        for a in atoms:
            args = [s[0] for s in a.signature]
            pred = domain.predicates.get(a.name)
            if pred is None:
                return (f"in the {where}, ({' '.join([a.name, *args])}) uses the unknown predicate '{a.name}'; "
                        "the domain's predicates are " + ", ".join(sorted(domain.predicates)))
            if len(args) != len(pred.signature):
                return (f"in the {where}, ({' '.join([a.name, *args])}) has {len(args)} arguments, but "
                        f"'{a.name}' takes {len(pred.signature)}")
            for arg, (pname, ptypes) in zip(args, pred.signature):
                otype = problem.objects.get(arg)
                if otype is None:
                    return f"in the {where}, object '{arg}' is used but not declared in :objects"
                if not any(_is_subtype(otype, t) for t in ptypes):
                    return (f"in the {where}, ({' '.join([a.name, *args])}): object '{arg}' has type "
                            f"{otype.name}, but '{a.name}' needs {' or '.join(t.name for t in ptypes)} there")
    return None


def _key(name):
    m = re.match(r"(.*?)(\d+)$", name)
    return (m.group(1), int(m.group(2))) if m else (name, -1)


def canonical_pddl(problem):
    """The problem as PDDL text with objects and facts in a fixed (sorted) order."""
    objs = sorted(problem.objects.items(), key=lambda kv: (kv[1].name, _key(kv[0])))
    init, goal = _facts(problem)
    srt = lambda fs: sorted(fs, key=lambda f: (f[0], [_key(x) for x in f[1:]]))
    lines = ["(define (problem canonical)", "  (:domain lunar)", "  (:objects"]
    lines += [f"    {o} - {t.name}" for o, t in objs] + ["  )", "  (:init"]
    lines += [f"    {_fmt(f)}" for f in srt(init)] + ["  )", "  (:goal (and"]
    lines += [f"    {_fmt(f)}" for f in srt(goal)] + ["  )))"]
    return "\n".join(lines) + "\n"


def solve_canonical(domain_path, problem_path, timeout=PLANNER_TIMEOUT_S):
    """Fast Downward on the canonical copy of a problem file (also used for the non-LLM baseline)."""
    _, problem = load_task(domain_path, problem_path)
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "canonical.pddl"
        path.write_text(canonical_pddl(problem))
        return fd.solve(domain_path, path, timeout)


def _result(**kw):
    base = dict(valid=False, goal_reached=False, first_error_step=None, error_category=None, error_message=None,
                plan_length=0, unmet_goals=[], steps_executed=0, failed_preconditions=[],
                parsed=False, exact=False, missing_init=[], extra_init=[], missing_goal=[], extra_goal=[],
                planner_status=None, planner_time_s=None, plan=None)
    base.update(kw)
    return base


def _diff(cand, true_problem):
    ti, tg = _facts(true_problem)
    ci, cg = cand
    return dict(missing_init=sorted(map(_fmt, ti - ci)), extra_init=sorted(map(_fmt, ci - ti)),
                missing_goal=sorted(map(_fmt, tg - cg)), extra_goal=sorted(map(_fmt, cg - tg)),
                exact=(ci == ti and cg == tg))


def check_translation(domain_path, true_problem_path, reply_text, cache_dir=None):
    cache_dir = Path(cache_dir) if cache_dir else ROOT / "results" / "translate_cache"
    key = hashlib.sha256(("|".join([Path(true_problem_path).name, reply_text or ""])).encode()).hexdigest()
    cache = cache_dir / f"{key}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    res = _check(domain_path, true_problem_path, reply_text)
    cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_suffix(".tmp")
    tmp.write_text(json.dumps(res, indent=1) + "\n")
    tmp.replace(cache)
    return res


def _check(domain_path, true_problem_path, reply_text):
    _, true_problem = load_task(domain_path, true_problem_path)
    text = extract_problem(reply_text)
    if text is None:
        return _result(error_category="no_plan",
                       error_message="No problem file found: expected a fenced block that starts with ```pddl "
                                     "and contains (define (problem ...) ...).")
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "problem.pddl"
        path.write_text(text)
        try:
            domain, problem = load_task(domain_path, path)
        except Exception as exc:  # pyperplan raises several exception types on bad input
            msg = str(exc).strip().strip("'\"").removeprefix("Error:").strip() or type(exc).__name__
            return _result(error_category="syntax", error_message=f"The planner could not read your problem file: {msg}")
        bad = lint(domain, problem)
        diff = _diff(_facts(problem), true_problem)
        if bad:
            return _result(error_category="syntax", parsed=True, error_message=f"Your problem file is not valid for the domain: {bad}.", **diff)
        res = solve_canonical(domain_path, path)
    status, t = res["status"], round(res["time_s"], 3)
    if status == "unsolvable":
        return _result(error_category="unsolvable", parsed=True, planner_status=status, planner_time_s=t,
                       error_message="A classical planner proved that the goal cannot be reached from the initial "
                                     "state in your problem file.", **diff)
    if status != "solved":
        return _result(error_category="timeout" if status == "timeout" else "syntax", parsed=True, planner_status=status,
                       planner_time_s=t, error_message="A classical planner found no plan for your problem file "
                       "within the time limit." if status == "timeout" else
                       f"The planner failed on your problem file: {res.get('error', status)}", **diff)
    plan = res["plan"]
    v = validate_text(domain_path, true_problem_path, "\n".join(plan))
    out = _result(**{**v, "parsed": True, "planner_status": status, "planner_time_s": t, "plan": plan, **diff})
    if not v["valid"]:
        out["error_message"] = ("A classical planner found a plan for your problem file, but the plan failed when "
                                f"it was checked against the real mission: {v['error_message']}")
    return out

"""Our own step-by-step STRIPS plan validator (the most important file).

Usage:
    python -m src.validate domain.pddl problem.pddl plan.txt [--mapping map.json]

What it does, in plain English:
  1. Reads the domain and problem with pyperplan's PDDL parser.
  2. Pulls action lines like "(move rover1 w1 w2 b3 b2)" out of the plan text.
  3. Executes them one by one on the initial state. At each step it checks
     every precondition; if any is false, the plan is invalid at that step.
  4. If every step runs, it checks that the goal holds.

It returns a dict:
  valid            True only if all steps run AND the goal is reached
  goal_reached     True if the goal holds after all steps
  first_error_step 1-based number of the first failing step (None if no step
                   failed, e.g. valid plans, or goal_not_reached, or when no
                   plan could be found in the text)
  error_category   None | syntax | terrain | battery | communication |
                   equipment | state | goal_not_reached
  error_message    VAL-style sentence, in the names used by the files given
  plan_length      number of steps parsed from the text
  unmet_goals      goals not true in the state reached just BEFORE the first
                   failing step (or after the last step if nothing failed)
  steps_executed   how many steps ran successfully
  failed_preconditions  list of the false preconditions at the failing step

Category of the FIRST failing step. If several preconditions fail in the same
step, the category is the highest-priority one:
    syntax > terrain > battery > communication > equipment > state
Mystery instances: pass the mapping (auto-detected for domain/mystery.pddl) so
predicates can be classified by their real meaning. Messages stay in mystery
names, exactly what the LLM saw.
"""
import argparse
import json
import re
import sys
from functools import lru_cache
from pathlib import Path

from pyperplan.pddl.parser import Parser

PRIORITY = ["syntax", "terrain", "battery", "communication", "equipment", "state"]
# standard predicate name -> category when its precondition is false
PREDICATE_CATEGORY = {
    "can-traverse": "terrain",
    "battery": "battery", "next-lower": "battery", "below-full": "battery", "full-level": "battery",
    "charge-at": "battery",   # recharging with a charge that is not (or no longer) at that waypoint
    "comm-visible": "communication",
    "has-sampler": "equipment", "has-camera": "equipment",
}  # everything else (at, store-empty, holding-*, sample-site, image-target, sunlit) -> "state"


# --------------------------------------------------------------------------
# Plan parsing
# --------------------------------------------------------------------------
_PLAN_FENCE = re.compile(r"```[ \t]*plan[ \t]*\r?\n(.*?)(?:```|\Z)", re.I | re.S)
_PREFIX = re.compile(
    r"^(?:[\s>*\-•+`_]+"                       # spaces, bullets, markdown marks
    r"|(?:step\s*)?\d+(?:\.\d+)?\s*[.):]\**(?=[\s(]|$)\s*)",  # "1.", "2)", "Step 3:", "0.000:"
    re.I,
)
_ACTION = re.compile(r"^\(\s*([^\s()]*)([^()]*)\)")


def extract_plan_block(text):
    """If a ```plan fenced block exists use only its content (the last one)."""
    blocks = _PLAN_FENCE.findall(text)
    return blocks[-1] if blocks else text


def parse_plan(text):
    """Return a list of steps: dicts with raw, action, args, malformed.

    A step is any line which (after removing numbering/bullets/markdown) starts
    with '('. Text after the closing ')' on that line is ignored.
    """
    steps = []
    for line in extract_plan_block(text).splitlines():
        s = line.strip()
        while True:
            m = _PREFIX.match(s)
            if not m or not m.group(0):
                break
            s = s[m.end():]
        # One action per line: text after the closing ')' (comments etc.) is ignored.
        if s.startswith("("):
            m = _ACTION.match(s)
            if not m:
                steps.append(dict(raw=s, action=None, args=[], malformed=True))
                continue
            action = m.group(1).lower()
            args = m.group(2).lower().split()
            steps.append(dict(raw=f"{action} {' '.join(args)}".strip(), action=action, args=args, malformed=False))
    return steps


# --------------------------------------------------------------------------
# Task loading
# --------------------------------------------------------------------------
@lru_cache(maxsize=64)
def _load(domain_path, problem_path, _mtimes):
    parser = Parser(domain_path, problem_path)
    domain = parser.parse_domain()
    problem = parser.parse_problem(domain)
    return domain, problem


def load_task(domain_path, problem_path):
    d, p = str(domain_path), str(problem_path)
    return _load(d, p, (Path(d).stat().st_mtime_ns, Path(p).stat().st_mtime_ns))


def _atom(pred):
    """Predicate object from pyperplan -> hashable ground/lifted atom (name, args)."""
    return (pred.name, tuple(s[0] for s in pred.signature))


def _is_subtype(t, target):
    while t is not None:
        if t is target or getattr(t, "name", t) == getattr(target, "name", target):
            return True
        t = getattr(t, "parent", None)
    return False


def _fmt_atom(name, args):
    return "(" + " ".join([name, *args]) + ")"


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------
def _result(**kw):
    base = dict(valid=False, goal_reached=False, first_error_step=None, error_category=None,
                error_message=None, plan_length=0, unmet_goals=[], steps_executed=0,
                failed_preconditions=[])
    base.update(kw)
    return base


def validate_text(domain_path, problem_path, plan_text, mapping=None):
    """Validate plan text against a domain/problem. See module docstring."""
    domain, problem = load_task(domain_path, problem_path)
    inv_pred = {}
    if mapping:
        inv_pred = {myst: std for std, myst in mapping["predicates"].items()}

    def category_of(pred_name):
        return PREDICATE_CATEGORY.get(inv_pred.get(pred_name, pred_name), "state")

    state = {_atom(p) for p in problem.initial_state}
    goals = [_atom(g) for g in problem.goal]

    def unmet():
        return [_fmt_atom(n, a) for n, a in goals if (n, a) not in state]

    steps = parse_plan(plan_text)
    if not steps:
        return _result(error_category="syntax", plan_length=0, unmet_goals=unmet(),
                       error_message="No plan found: expected lines of the form (action arg1 arg2 ...)")

    action_names = sorted(domain.actions)
    for i, st in enumerate(steps, start=1):
        def syntax_error(msg):
            return _result(error_category="syntax", first_error_step=i, plan_length=len(steps),
                           steps_executed=i - 1, unmet_goals=unmet(),
                           error_message=f"Step {i} ({st['raw']}): {msg}")
        if st["malformed"]:
            return _result(error_category="syntax", first_error_step=i, plan_length=len(steps),
                           steps_executed=i - 1, unmet_goals=unmet(),
                           error_message=f"Step {i}: could not read '{st['raw']}' as (action arg1 arg2 ...)")
        act = domain.actions.get(st["action"])
        if act is None:
            return syntax_error(f"unknown action '{st['action']}'; the available actions are "
                                + ", ".join(action_names))
        params = act.signature
        if len(st["args"]) != len(params):
            return syntax_error(f"action '{act.name}' takes {len(params)} arguments "
                                f"({' '.join(p[0] for p in params)}) but {len(st['args'])} were given")
        binding = {}
        for (pname, ptypes), arg in zip(params, st["args"]):
            if arg not in problem.objects:
                return syntax_error(f"unknown object '{arg}'")
            otype = problem.objects[arg]
            if not any(_is_subtype(otype, t) for t in ptypes):
                want = " or ".join(t.name for t in ptypes)
                return syntax_error(f"object '{arg}' has type {otype.name}, but parameter {pname} "
                                    f"of '{act.name}' requires type {want}")
            binding[pname] = arg

        failed = []
        for pre in act.precondition:
            name, vars_ = _atom(pre)
            ground = (name, tuple(binding[v] for v in vars_))
            if ground not in state:
                failed.append(ground)
        if failed:
            cats = [category_of(n) for n, _ in failed]
            cat = min(cats, key=PRIORITY.index)
            texts = [_fmt_atom(n, a) for n, a in failed]
            if len(texts) == 1:
                msg = f"precondition {texts[0]} is false"
            else:
                msg = "preconditions " + ", ".join(texts) + " are false"
            return _result(error_category=cat, first_error_step=i, plan_length=len(steps),
                           steps_executed=i - 1, unmet_goals=unmet(), failed_preconditions=texts,
                           error_message=f"Step {i} ({st['raw']}): {msg}")
        # Apply effects: delete first, then add (standard STRIPS semantics).
        for pred in act.effect.dellist:
            n, vs = _atom(pred)
            state.discard((n, tuple(binding[v] for v in vs)))
        for pred in act.effect.addlist:
            n, vs = _atom(pred)
            state.add((n, tuple(binding[v] for v in vs)))

    missing = unmet()
    if missing:
        return _result(error_category="goal_not_reached", plan_length=len(steps),
                       steps_executed=len(steps), unmet_goals=missing,
                       error_message=(f"All {len(steps)} steps are executable, but the goal is not reached; "
                                      "unmet goals: " + ", ".join(missing)))
    return _result(valid=True, goal_reached=True, plan_length=len(steps), steps_executed=len(steps))


def default_mapping_for(domain_path):
    """Mystery domains come with a mapping file next to them."""
    p = Path(domain_path)
    cand = p.with_name("mystery_mapping.json")
    if p.name == "mystery.pddl" and cand.exists():
        return json.loads(cand.read_text())
    return None


def validate(domain_path, problem_path, plan, mapping=None):
    """Like validate_text but `plan` may be a path to a plan file or plan text."""
    if mapping is None:
        mapping = default_mapping_for(domain_path)
    elif isinstance(mapping, (str, Path)):
        mapping = json.loads(Path(mapping).read_text())
    if isinstance(plan, Path):
        plan = plan.read_text()
    return validate_text(domain_path, problem_path, plan, mapping)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Validate a plan (own STRIPS simulator).")
    ap.add_argument("domain")
    ap.add_argument("problem")
    ap.add_argument("planfile")
    ap.add_argument("--mapping", help="mystery mapping JSON (auto-detected for domain/mystery.pddl)")
    a = ap.parse_args(argv)
    res = validate(a.domain, a.problem, Path(a.planfile), a.mapping)
    print(json.dumps(res, indent=2))
    return 0 if res["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())

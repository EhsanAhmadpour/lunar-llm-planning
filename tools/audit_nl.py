"""Independent audit of the plain-English runs (nl-plan, nl-translate).

Usage:  python tools/audit_nl.py

1. Every plan that decided a verdict (the model's plan for nl-plan, Fast Downward's plan for nl-translate)
   is re-executed with pyperplan's own grounded operators on the hand-written problem; the verdict must
   match our validator's.
2. For nl-translate, the fact comparison is recomputed with a second, independent reader: a few lines of
   regular expressions over the model's PDDL text (no pyperplan), and must give the same exact / not exact.
3. Every feedback message must be the checker's sentence for the previous reply (conversation integrity).
"""
import json, re, sys, collections
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pyperplan.pddl.parser import Parser          # noqa: E402
from pyperplan import grounding                   # noqa: E402
from src.validate import parse_plan               # noqa: E402
from src.run_llm import list_instances, validate_response   # noqa: E402
from src.translate import extract_problem         # noqa: E402

DOM = ROOT / "domain" / "lunar.pddl"
_tasks = {}


def grounded(name):
    if name not in _tasks:
        p = Parser(str(DOM), str(ROOT / "problems" / "standard" / f"{name}.pddl"))
        d = p.parse_domain(); pr = p.parse_problem(d)
        t = grounding.ground(pr, remove_statics_from_initial_state=False, remove_irrelevant_operators=False)
        _tasks[name] = (t, {o.name: o for o in t.operators})
    return _tasks[name]


def indep_valid(name, plan_text):
    t, ops = grounded(name); s = t.initial_state; steps = parse_plan(plan_text)
    if not steps:
        return False
    for st in steps:
        op = ops.get("(" + st["raw"] + ")")
        if op is None or not op.applicable(s):
            return False
        s = op.apply(s)
    return t.goal_reached(s)


def regex_facts(text):
    """init and goal fact sets read with regular expressions only."""
    text = re.sub(r";[^\n]*", "", text.lower())
    i, g = text.find("(:init"), text.find("(:goal")
    atoms = lambda s: {tuple(a.split()) for a in re.findall(r"\(\s*([a-z][\w-]*(?:\s+[\w-]+)*)\s*\)", s)} - {("and",)}
    return atoms(text[i + 6:g]), atoms(text[g + 6:])


true_facts = {n: regex_facts((ROOT / "problems" / "standard" / f"{n}.pddl").read_text()) for n in
              [i.name for i in list_instances(["standard"])]}
insts = {(i.variant, i.name): i for i in list_instances(["nl-plan", "nl-translate"])}
count, bad = collections.Counter(), []
for f in sorted((ROOT / "results" / "raw").glob("*/nl-*/*/rep*_round*.json")):
    r = json.loads(f.read_text()); inst = insts[(r["variant"], r["problem"])]
    res = validate_response(inst, r["response_text"], ROOT / "results")
    count[r["variant"], "replies"] += 1
    if r["variant"] == "nl-plan":
        ok = indep_valid(r["problem"], r["response_text"])
    else:
        ok = indep_valid(r["problem"], "\n".join(res["plan"])) if res.get("plan") else False
        txt = extract_problem(r["response_text"])
        if res.get("parsed"):
            count["nl-translate", "fact comparisons"] += 1
            if (regex_facts(txt) == true_facts[r["problem"]]) != res["exact"]:
                bad.append(("exact mismatch", str(f)))
    if ok != res["valid"]:
        bad.append(("validity mismatch", str(f), res["valid"], ok))
    k = r["round"]
    if k > 0:
        prev = json.loads((f.parent / f"rep{r['rep']}_round{k - 1}.json").read_text())
        pres = validate_response(inst, prev["response_text"], ROOT / "results")
        m = r["messages"]
        if len(m) != 2 * k + 1 or m[-2]["content"] != prev["response_text"] or pres["error_message"] not in m[-1]["content"]:
            bad.append(("feedback mismatch", str(f)))
print(dict(count))
print("disagreements:", len(bad)); [print(" ", b) for b in bad[:10]]
sys.exit(1 if bad else 0)

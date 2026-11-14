"""Validator tests: error categories, messages, plan parsing."""
import pytest

from conftest import ROOT, TINY_PLAN
from src.obfuscate import translate_plan
from src.run_llm import list_instances
from src.validate import load_task, parse_plan, validate, validate_text, default_mapping_for

VARIANTS = ["standard", "mystery"]


def run(tiny, variant, plan_std):
    domain, problem, tr = tiny[variant]
    return validate(domain, problem, tr(plan_std))


# ---------------------------------------------------------------- tiny: pure categories
def test_tiny_valid_plan(tiny):
    for v in VARIANTS:
        r = run(tiny, v, TINY_PLAN)
        assert r["valid"] and r["goal_reached"] and r["error_category"] is None
        assert r["plan_length"] == 8 and r["unmet_goals"] == [] and r["first_error_step"] is None


@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.parametrize("plan,category,step", [
    ("(fly rover1 w1 w2)", "syntax", 1),
    ("(move rover1 w1 w2 b3)", "syntax", 1),                       # wrong number of args
    ("(move rover1 w1 mars b3 b2)", "syntax", 1),                  # unknown object
    ("(move w1 w1 w2 b3 b2)", "syntax", 1),                        # wrong type
    ("(move rover1 w1 w4 b3 b2)", "terrain", 1),
    ("(move rover1 w1 w2 b1 b0)", "battery", 1),                   # battery is not at b1
])
def test_tiny_pure_categories(tiny, variant, plan, category, step):
    r = run(tiny, variant, plan)
    assert r["error_category"] == category and r["first_error_step"] == step and not r["valid"]


@pytest.mark.parametrize("variant", VARIANTS)
def test_tiny_each_other_category(tiny, variant):
    # battery: recharge when already full -> (below-full b3) false
    pre = "(move rover1 w1 w2 b3 b2)\n"
    r = run(tiny, variant, "(recharge rover1 w1 b3 b3)")
    assert r["error_category"] == "battery"
    # communication: at w3 (not comm-visible) with the item in hand
    plan = ("(move rover1 w1 w2 b3 b2)\n(move rover1 w2 w3 b2 b1)\n(take-image rover1 w3)\n"
            "(send-image rover1 w3 w3)")
    r = run(tiny, variant, plan)
    assert r["error_category"] == "communication" and r["first_error_step"] == 4
    # equipment is covered on a medium instance below; state: not at the image target
    r = run(tiny, variant, pre + "(take-image rover1 w3)")
    assert r["error_category"] == "state" and r["first_error_step"] == 2
    # goal not reached: executable but incomplete
    r = run(tiny, variant, "\n".join(TINY_PLAN.splitlines()[:7]))
    assert r["error_category"] == "goal_not_reached" and r["first_error_step"] is None
    assert len(r["unmet_goals"]) == 1 and r["unmet_goals"][0].startswith("(")


def test_exact_message_standard(tiny):
    r = run(tiny, "standard", "(move rover1 w1 w4 b3 b2)")
    assert r["error_message"] == ("Step 1 (move rover1 w1 w4 b3 b2): "
                                  "precondition (can-traverse rover1 w1 w4) is false")


def test_message_lists_all_failed_preconditions_and_priority(tiny):
    # Not holding anything, and w1 is not comm-visible: state + communication fail -> communication.
    r = run(tiny, "standard", "(send-sample rover1 w3 w1)")
    assert r["error_category"] == "communication"
    assert "(holding-sample rover1 w3)" in r["error_message"] and "(comm-visible w1)" in r["error_message"]
    assert r["error_message"].startswith("Step 1 (send-sample rover1 w3 w1): preconditions ")
    # terrain beats battery: wrong battery level AND blocked edge
    r = run(tiny, "standard", "(move rover1 w1 w4 b1 b0)")
    assert r["error_category"] == "terrain" and len(r["failed_preconditions"]) == 2
    # battery beats communication/state
    r = run(tiny, "standard", "(recharge rover1 w2 b3 b3)")  # not at w2, w2 not sunlit, level full
    assert r["error_category"] == "battery"
    # equipment beats state is covered in the medium test


def test_first_failing_step_decides(tiny):
    plan = "(move rover1 w1 w2 b3 b2)\n(move rover1 w2 w4 b2 b1)\n(fly a b)"
    r = run(tiny, "standard", plan)
    assert r["first_error_step"] == 2 and r["error_category"] == "terrain" and r["steps_executed"] == 1
    assert r["plan_length"] == 3


def test_no_plan_is_syntax(tiny):
    r = run(tiny, "standard", "I could not find a plan, sorry.")
    assert r["error_category"] == "syntax" and r["plan_length"] == 0 and not r["valid"]
    assert "No plan" in r["error_message"]


def test_effects_are_applied_correctly(tiny):
    # recharge at w3 only works after the battery dropped; after collecting, sample is held -> second collect fails on store-empty
    plan = ("(move rover1 w1 w2 b3 b2)\n(move rover1 w2 w3 b2 b1)\n(collect-sample rover1 w3 b1 b0)\n"
            "(recharge rover1 w3 b0 b3)\n(collect-sample rover1 w3 b3 b2)")
    r = run(tiny, "standard", plan)
    assert r["error_category"] == "state" and "(store-empty rover1)" in r["error_message"]


# ---------------------------------------------------------------- parser tolerance
@pytest.mark.parametrize("text", [
    "(move rover1 w1 w2 b3 b2)",
    "1. (move rover1 w1 w2 b3 b2)",
    "2) (MOVE Rover1 W1 W2 B3 B2)",
    "- (move rover1 w1 w2 b3 b2)",
    "* (move rover1 w1 w2 b3 b2)",
    "Step 3: (move rover1 w1 w2 b3 b2)",
    "0.000: (move rover1 w1 w2 b3 b2)  ; comment",
    "**1.** (move rover1 w1 w2 b3 b2)",
    "`(move rover1 w1 w2 b3 b2)`",
    "   (  move   rover1  w1 w2 b3   b2 )   ",
    "```\n(move rover1 w1 w2 b3 b2)\n```",
    "Here is the plan:\n```plan\n(move rover1 w1 w2 b3 b2)\n```\nHope it helps!",
])
def test_parse_tolerance(text):
    steps = parse_plan(text)
    assert len(steps) == 1 and steps[0]["action"] == "move"
    assert steps[0]["args"] == ["rover1", "w1", "w2", "b3", "b2"]


def test_plan_fence_wins_over_other_text():
    text = ("Draft: (move a b c d e)\n```plan\n(take-image rover1 w3)\n```\nthen\n```pddl\n(fly x)\n```")
    steps = parse_plan(text)
    assert [s["action"] for s in steps] == ["take-image"]


def test_last_plan_fence_is_used():
    text = "```plan\n(a b)\n```\nOops:\n```plan\n(c d)\n```"
    assert [s["action"] for s in parse_plan(text)] == ["c"]


def test_parse_ignores_prose_and_numbers():
    text = "Plan\n1. (move rover1 w1 w2 b3 b2)\nthen something\n2. (take-image rover1 w3)"
    assert [s["action"] for s in parse_plan(text)] == ["move", "take-image"]


def test_malformed_line_is_syntax_error(tiny):
    r = run(tiny, "standard", "(move rover1 w1 w2 b3 b2\n")
    assert r["error_category"] == "syntax" and r["first_error_step"] == 1


# ---------------------------------------------------------------- real instances
def _plan_in_std_names(inst_problem):
    return (ROOT / "problems" / "reference_plans" / f"{inst_problem}.plan").read_text()


@pytest.mark.parametrize("inst", list_instances(), ids=lambda i: f"{i.variant}-{i.name}")
def test_reference_plans_valid(inst):
    suffix = ".plan" if inst.variant == "standard" else ".mystery.plan"
    plan = (ROOT / "problems" / "reference_plans" / f"{inst.name}{suffix}").read_text()
    r = validate(inst.domain, inst.problem, plan)
    assert r["valid"], r["error_message"]


def _atoms(inst):
    domain, problem = load_task(inst.domain, inst.problem)
    return {(p.name, tuple(s[0] for s in p.signature)) for p in problem.initial_state}


def build_bad_plans(std_problem):
    """Hand-made bad plans in STANDARD names, one per category, derived from the problem facts."""
    from src.validate import load_task as lt
    dom = ROOT / "domain" / "lunar.pddl"
    prob = ROOT / "problems" / "standard" / f"{std_problem}.pddl"
    _, p = lt(dom, prob)
    init = {(x.name, tuple(s[0] for s in x.signature)) for x in p.initial_state}
    rovers = sorted(n for n, t in p.objects.items() if t.name == "rover")
    wps = sorted((n for n, t in p.objects.items() if t.name == "waypoint"), key=lambda s: int(s[1:]))
    top = [a for n, a in init if n == "full-level"][0][0]
    lower = [a[1] for n, a in init if n == "next-lower" and a[0] == top][0]
    base = "w1"
    plans = {"syntax": "(fly rover1 w1 w2)"}
    # terrain: rover cannot drive base -> some waypoint
    # (terrain outranks state, so the rover need not even be at the start waypoint)
    r_t, a_t, w_t = next((r, a, w) for r in rovers for a in wps for w in wps
                         if a != w and ("can-traverse", (r, a, w)) not in init)
    plans["terrain"] = f"(move {r_t} {a_t} {w_t} {top} {lower})"
    # battery: wrong level pair on an otherwise legal move
    r_b, w_b = next((r, w) for r in rovers for w in wps if ("can-traverse", (r, base, w)) in init)
    plans["battery"] = f"(move {r_b} {base} {w_b} b0 b0)"
    # communication (+ state failures): send from a waypoint that is not comm-visible
    w_nc = next(w for w in wps if ("comm-visible", (w,)) not in init)
    plans["communication"] = f"(send-image {rovers[0]} {wps[1]} {w_nc})"
    # state only: take a picture where there is no image target (rover has a camera)
    r_c = next((r for r in rovers if ("has-camera", (r,)) in init), None)
    plans["state"] = f"(take-image {r_c} {base})"
    # equipment (+ state): rover without a camera tries to take a picture
    r_nc = next((r for r in rovers if ("has-camera", (r,)) not in init), None)
    r_ns = next((r for r in rovers if ("has-sampler", (r,)) not in init), None)
    if r_nc:
        plans["equipment"] = f"(take-image {r_nc} {base})"
    elif r_ns:  # or: rover without a sampler tries to collect a sample
        plans["equipment"] = f"(collect-sample {r_ns} {base} {top} {lower})"
    # goal not reached: only the first reference step
    ref = (ROOT / "problems" / "reference_plans" / f"{std_problem}.plan").read_text().splitlines()
    plans["goal_not_reached"] = ref[0]
    return plans


@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.parametrize("problem", ["p01_small", "p03_medium", "p05_large"])
def test_bad_plan_per_category_on_real_instances(problem, variant, mapping):
    plans = build_bad_plans(problem)
    inst = next(i for i in list_instances() if i.name == problem and i.variant == variant)
    if problem != "p01_small":
        assert "equipment" in plans, "medium/large instances must have a rover lacking an instrument"
    for category, plan in plans.items():
        text = plan if variant == "standard" else _to_mystery(plan, mapping)
        r = validate(inst.domain, inst.problem, text)
        assert r["error_category"] == category, (category, plan, r["error_message"])


def _to_mystery(plan, mapping):
    return translate_plan(plan, mapping, to="mystery")


def test_mystery_messages_use_mystery_names(mapping):
    inst = next(i for i in list_instances() if i.variant == "mystery" and i.name == "p01_small")
    r = validate(inst.domain, inst.problem, "(move rover1 w1 w2 b3 b2)")
    assert "unknown" in r["error_message"]  # standard names are not valid objects in the mystery files
    assert r["error_category"] == "syntax"


def _optimal_reference_problems():
    import json
    metas = [json.loads(f.read_text()) for f in sorted((ROOT / "problems" / "standard").glob("*.json"))]
    return [m["name"] for m in metas if m["reference"]["optimal"]]


@pytest.mark.parametrize("name", _optimal_reference_problems())
def test_dropping_any_step_of_an_optimal_plan_breaks_it(name):
    """Reference plans marked optimal in the metadata have no redundant step."""
    inst = next(i for i in list_instances(["standard"]) if i.name == name)
    steps = (ROOT / "problems" / "reference_plans" / f"{name}.plan").read_text().splitlines()
    for k in range(len(steps)):
        r = validate_text(inst.domain, inst.problem, "\n".join(steps[:k] + steps[k + 1:]))
        assert not r["valid"], f"dropping step {k + 1} still validates"

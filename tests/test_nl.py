"""Tests for the plain-English conditions (nl-plan, nl-translate). No network; runs the planner."""
import json
from pathlib import Path

import pytest

from conftest import ROOT
from test_runner import GOOD_PLAN, MODEL, FakePost, FakeResp, make_ctx
from src import describe as D
from src.run_llm import list_instances, render_direct, run_episode
from src.translate import canonical_pddl, check_translation, extract_problem
from src.validate import load_task

DOMAIN = ROOT / "domain" / "lunar.pddl"
P01 = ROOT / "problems" / "standard" / "p01_small.pddl"
TRUE_P01 = P01.read_text()


def wrap(text):
    return "Here is the file.\n```pddl\n" + text + "\n```"


@pytest.mark.parametrize("problem", sorted((ROOT / "problems" / "standard").glob("p*.pddl")), ids=lambda p: p.stem)
def test_briefing_is_complete_and_committed(problem):
    text = D.describe(problem)
    assert (ROOT / "problems" / "nl" / f"{problem.stem}.txt").read_text() == text   # committed = generated
    assert D.read_back(text) == D.facts_of(problem)                                # no fact lost or added
    assert "(" not in text.split("\n", 1)[1].replace("(empty)", "").replace("(full)", "").replace("(not back)", "") \
        .replace("(c", "")  # no PDDL syntax leaks into the prose (only the charge-name lists use brackets)


def test_nl_instances_are_checked_against_the_standard_problems():
    for v in ("nl-plan", "nl-translate"):
        insts = list_instances([v])
        assert [i.name for i in insts] == [f"p0{k}_{s}" for k, s in
                                           zip(range(1, 7), ["small", "small", "medium", "medium", "large", "large"])]
        assert all(i.problem.parent.name == "standard" and i.domain.name == "lunar.pddl" for i in insts)


def test_nl_prompts_contain_the_briefing_not_the_pddl_problem():
    for v, marker in (("nl-plan", "```plan"), ("nl-translate", "```pddl")):
        inst = list_instances([v], ["p01_small"])[0]
        prompt = render_direct(inst)
        assert "MISSION BRIEFING p01_small" in prompt and "(define (domain lunar)" in prompt
        assert "(define (problem" not in prompt and "(:init" not in prompt and marker in prompt


def test_exact_translation_gives_valid_plan(tmp_path):
    r = check_translation(DOMAIN, P01, wrap(TRUE_P01), tmp_path)
    assert r["valid"] and r["exact"] and r["parsed"] and r["planner_status"] == "solved" and r["plan_length"] > 0
    assert list(tmp_path.glob("*.json"))      # cached


def test_canonical_form_ignores_order_and_names():
    shuffled = TRUE_P01.replace("(problem p01_small)", "(problem other)").replace(
        "(sunlit w2)\n    (sunlit w5)", "(sunlit w5)\n    (sunlit w2)")
    _, a = load_task(DOMAIN, P01)
    p = Path(__file__).parent / "fixtures" / "_tmp_shuffled.pddl"
    p.write_text(shuffled)
    try:
        _, b = load_task(DOMAIN, p)
        assert canonical_pddl(a) == canonical_pddl(b)
    finally:
        p.unlink()


def test_harmless_extra_fact_not_exact_but_valid(tmp_path):
    r = check_translation(DOMAIN, P01, wrap(TRUE_P01.replace("(sunlit w2)", "(sunlit w2) (sample-site w1)")), tmp_path)
    assert r["valid"] and not r["exact"] and r["extra_init"] == ["(sample-site w1)"] and not r["missing_init"]


def test_invented_route_fails_on_real_mission(tmp_path):
    # a direct route w1 -> w5 that does not exist: the planner uses it, the real mission rejects it
    txt = TRUE_P01.replace("(can-traverse rover1 w1 w2)", "(can-traverse rover1 w1 w2) (can-traverse rover1 w1 w5)"
                           " (can-traverse rover1 w5 w1) (can-traverse rover1 w1 w7) (can-traverse rover1 w7 w1)")
    r = check_translation(DOMAIN, P01, wrap(txt), tmp_path)
    assert not r["valid"] and r["error_category"] == "terrain"
    assert "checked against the real mission" in r["error_message"] and "can-traverse" in r["error_message"]


def test_missing_goal_is_caught(tmp_path):
    r = check_translation(DOMAIN, P01, wrap(TRUE_P01.replace("(sample-sent w5) ", "")), tmp_path)
    assert not r["valid"] and r["error_category"] == "goal_not_reached" and r["missing_goal"] == ["(sample-sent w5)"]


@pytest.mark.parametrize("bad,category,phrase", [
    ("(sunlit w2)", "(sunny w2)", "unknown predicate 'sunny'"),
    ("(sunlit w2)", "(sunlit w2 w3)", "has 2 arguments"),
    ("(sunlit w2)", "(sunlit rover1)", "has type rover"),
    ("(sunlit w2)", "(sunlit w99)", "w99"),
])
def test_unreadable_or_ill_typed_problem_is_syntax(tmp_path, bad, category, phrase):
    r = check_translation(DOMAIN, P01, wrap(TRUE_P01.replace(bad, category)), tmp_path)
    assert r["error_category"] == "syntax" and phrase in r["error_message"] and not r["valid"]


def test_no_problem_in_reply(tmp_path):
    r = check_translation(DOMAIN, P01, "I cannot do that.", tmp_path)
    assert r["error_category"] == "no_plan" and not r["valid"]
    assert extract_problem("```pddl\n(define (problem x))\n```\n```pddl\n(define (problem y))\n```") == "(define (problem y))\n"


def test_translate_episode_feedback_loop(tmp_path):
    inst = list_instances(["nl-translate"], ["p01_small"])[0]
    bad = wrap(TRUE_P01.replace("(sunlit w2)", "(sunny w2)"))
    post = FakePost([FakeResp(text=bad), FakeResp(text=wrap(TRUE_P01))])
    ctx = make_ctx(tmp_path, post)
    assert run_episode(ctx, MODEL, inst, 0) == 2
    d = tmp_path / "results" / "raw" / "m1" / "nl-translate" / "p01_small"
    r1 = json.loads((d / "rep0_round1.json").read_text())
    fb = r1["messages"][-1]["content"]
    assert "unknown predicate 'sunny'" in fb and "```pddl" in fb and "```plan" not in fb
    assert list((tmp_path / "results" / "translate_cache").glob("*.json"))


def test_nl_plan_episode_uses_plan_validator(tmp_path):
    inst = list_instances(["nl-plan"], ["p01_small"])[0]
    post = FakePost([FakeResp(text=GOOD_PLAN)])
    assert run_episode(make_ctx(tmp_path, post), MODEL, inst, 0) == 1

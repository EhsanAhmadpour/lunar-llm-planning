"""Runner tests with a mocked HTTP layer (no network)."""
import json
from pathlib import Path

import pytest
import requests
import yaml

from conftest import ROOT
from src import run_llm as R
from src import manual as M
from src.run_llm import (ApiError, BudgetExceeded, Context, FatalApiError, SpendTracker, list_instances,
                         pending_round, run_episode, worst_case_cost)

SECRET = "sk-or-test-SECRET-123"
GOOD_PLAN = "```plan\n" + (ROOT / "problems" / "reference_plans" / "p01_small.plan").read_text() + "```"
BAD_PLAN = "```plan\n(fly rover1 w1 w2)\n```"
BAD_PLAN2 = "```plan\n(move rover1 w1 w1 b3 b2)\n```"

MODEL = dict(id="vendor/model", label="m1", tier="cheap", repeats=1, price_in=1.0, price_out=2.0,
             max_tokens=500, provider="openrouter")
EXP = dict(temperature=0, request_timeout_s=5, max_retries=2, budget_usd=9.0, max_feedback_rounds=3)


class FakeResp:
    def __init__(self, status=200, text="", cost=0.001, with_cost=True):
        self.status_code = status
        self._text, self._cost, self._with_cost = text, cost, with_cost

    def json(self):
        usage = {"prompt_tokens": 1000, "completion_tokens": 100}
        if self._with_cost:
            usage["cost"] = self._cost
        return {"choices": [{"message": {"content": self._text}}], "usage": usage, "model": "vendor/model-2026"}


class FakePost:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, headers=None, json=None, timeout=None):
        self.calls.append(dict(url=url, headers=headers, json=json, timeout=timeout))
        if not self.responses:
            raise AssertionError("unexpected extra API call")
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def make_ctx(tmp_path, post, budget=9.0, max_rounds=3):
    exp = dict(EXP, budget_usd=budget)
    return Context(results_dir=tmp_path / "results", exp=exp, max_rounds=max_rounds, post=post,
                   sleep=lambda s: None, api_key=SECRET, tracker=SpendTracker(tmp_path / "results", budget),
                   log=lambda *a: None)


INST = next(i for i in list_instances(["standard"], ["p01_small"]))


def test_feedback_loop_stops_at_first_valid(tmp_path):
    post = FakePost([FakeResp(text=BAD_PLAN), FakeResp(text=BAD_PLAN2), FakeResp(text=GOOD_PLAN)])
    ctx = make_ctx(tmp_path, post)
    assert run_episode(ctx, MODEL, INST, 0) == 3
    d = tmp_path / "results" / "raw" / "m1" / "standard" / "p01_small"
    assert sorted(p.name for p in d.iterdir()) == ["rep0_round0.json", "rep0_round1.json", "rep0_round2.json"]
    r1 = json.loads((d / "rep0_round1.json").read_text())
    assert [m["role"] for m in r1["messages"]] == ["user", "assistant", "user"]
    assert "unknown action 'fly'" in r1["messages"][-1]["content"]          # verifier message is fed back
    r2 = json.loads((d / "rep0_round2.json").read_text())
    assert len(r2["messages"]) == 5 and r2["cost_usd"] == 0.001 and r2["cost_source"] == "openrouter"
    assert r2["returned_model"] == "vendor/model-2026" and r2["model_id"] == "vendor/model"
    # request details
    sent = post.calls[0]["json"]
    assert sent["usage"] == {"include": True} and sent["temperature"] == 0 and sent["max_tokens"] == 500
    assert post.calls[0]["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert post.calls[0]["timeout"] == 5
    assert "```plan" in sent["messages"][0]["content"] and "(define (domain lunar)" in sent["messages"][0]["content"]
    assert ctx.tracker.total == pytest.approx(0.003)
    assert json.loads((tmp_path / "results" / "spend.json").read_text())["total_usd"] == pytest.approx(0.003)


def test_valid_at_round0_makes_single_call(tmp_path):
    post = FakePost([FakeResp(text=GOOD_PLAN)])
    assert run_episode(make_ctx(tmp_path, post), MODEL, INST, 0) == 1


def test_gives_up_after_max_rounds(tmp_path):
    post = FakePost([FakeResp(text=BAD_PLAN)] * 4)
    assert run_episode(make_ctx(tmp_path, post), MODEL, INST, 0) == 4   # round 0 + 3 feedback rounds
    assert not post.responses


def test_resume_skips_saved_calls(tmp_path):
    post = FakePost([FakeResp(text=BAD_PLAN), FakeResp(text=GOOD_PLAN)])
    ctx = make_ctx(tmp_path, post)
    run_episode(ctx, MODEL, INST, 0)
    spent = ctx.tracker.total
    # A new run (fresh tracker, a post that fails on any call) must make zero calls
    post2 = FakePost([])
    ctx2 = make_ctx(tmp_path, post2)
    assert run_episode(ctx2, MODEL, INST, 0) == 0
    assert ctx2.tracker.total == pytest.approx(spent)
    # Interrupted episode: delete round 1 -> only round 1 is requested again
    (tmp_path / "results" / "raw" / "m1" / "standard" / "p01_small" / "rep0_round1.json").unlink()
    post3 = FakePost([FakeResp(text=GOOD_PLAN)])
    assert run_episode(make_ctx(tmp_path, post3), MODEL, INST, 0) == 1
    assert len(post3.calls[0]["json"]["messages"]) == 3


def test_budget_guard_stops_before_exceeding(tmp_path):
    ctx0 = make_ctx(tmp_path, FakePost([]))
    first = [{"role": "user", "content": R.render_direct(INST)}]
    w1 = worst_case_cost(MODEL, first)
    budget = w1 + 0.001          # first call allowed; after it spends 0.001, the 2nd worst case is larger
    post = FakePost([FakeResp(text=BAD_PLAN, cost=0.001), FakeResp(text=GOOD_PLAN)])
    ctx = make_ctx(tmp_path, post, budget=budget)
    with pytest.raises(BudgetExceeded):
        run_episode(ctx, MODEL, INST, 0)
    assert len(post.calls) == 1 and ctx.tracker.total <= budget


def test_budget_guard_blocks_first_call(tmp_path):
    post = FakePost([])
    ctx = make_ctx(tmp_path, post, budget=0.0001)
    with pytest.raises(BudgetExceeded):
        run_episode(ctx, MODEL, INST, 0)
    assert not post.calls and not (tmp_path / "results" / "raw").exists()


def test_cost_computed_from_prices_when_missing(tmp_path):
    post = FakePost([FakeResp(text=GOOD_PLAN, with_cost=False)])
    run_episode(make_ctx(tmp_path, post), MODEL, INST, 0)
    rec = json.loads(next((tmp_path / "results" / "raw").rglob("*.json")).read_text())
    assert rec["cost_source"] == "computed"
    assert rec["cost_usd"] == pytest.approx((1000 * 1.0 + 100 * 2.0) / 1e6)


def test_retry_on_429_and_5xx_then_success(tmp_path):
    sleeps = []
    post = FakePost([FakeResp(status=429), FakeResp(status=503), FakeResp(text=GOOD_PLAN)])
    ctx = make_ctx(tmp_path, post)
    ctx.sleep = sleeps.append
    run_episode(ctx, MODEL, INST, 0)
    assert len(post.calls) == 3 and sleeps == [1, 2]       # exponential backoff


def test_retry_on_timeout_then_give_up(tmp_path):
    post = FakePost([requests.Timeout()] * 3)
    ctx = make_ctx(tmp_path, post)
    with pytest.raises(ApiError):
        run_episode(ctx, MODEL, INST, 0)
    assert len(post.calls) == 3 and not (tmp_path / "results" / "raw").exists()


def test_fatal_on_401(tmp_path):
    with pytest.raises(FatalApiError):
        run_episode(make_ctx(tmp_path, FakePost([FakeResp(status=401)])), MODEL, INST, 0)


def test_api_key_never_saved(tmp_path):
    post = FakePost([FakeResp(text=BAD_PLAN), FakeResp(text=GOOD_PLAN)])
    run_episode(make_ctx(tmp_path, post), MODEL, INST, 0)
    assert post.calls[0]["headers"]["Authorization"] == f"Bearer {SECRET}"
    for f in (tmp_path / "results").rglob("*"):
        if f.is_file():
            assert SECRET not in f.read_text()


def test_api_key_from_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    (tmp_path / ".env").write_text('OPENROUTER_API_KEY="abc123"\n')
    assert R.get_api_key(tmp_path) == "abc123"
    monkeypatch.setenv("OPENROUTER_API_KEY", "fromenv")
    assert R.get_api_key(tmp_path) == "fromenv"


def test_spend_rebuilt_from_raw_when_spend_json_missing(tmp_path):
    run_episode(make_ctx(tmp_path, FakePost([FakeResp(text=BAD_PLAN), FakeResp(text=GOOD_PLAN)])), MODEL, INST, 0)
    (tmp_path / "results" / "spend.json").unlink()
    assert SpendTracker(tmp_path / "results", 9).total == pytest.approx(0.002)


def test_estimate_runs_and_is_sane():
    exp, models = R.load_config()
    instances = list_instances()
    rows, worst, expected = R.estimate(models, instances, exp)
    assert rows and 0 < expected < worst
    lines = []
    R.print_estimate(rows, worst, expected, exp["budget_usd"], out=lines.append)
    assert any("TOTAL" in l for l in lines)


def test_cli_estimate_needs_no_key(monkeypatch, capsys):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert R.main(["--estimate"]) == 0
    assert "TOTAL" in capsys.readouterr().out


def test_cli_smoke_uses_cheapest_model_round0_only(tmp_path, monkeypatch, capsys):
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "experiment.yaml").write_text(yaml.safe_dump(dict(EXP)))
    (cfg / "models.yaml").write_text(yaml.safe_dump({"models": [
        dict(MODEL, label="pricey", price_in=5, price_out=5), dict(MODEL, label="cheap", price_in=0.1, price_out=0.1)]}))
    post = FakePost([FakeResp(text=BAD_PLAN)])
    monkeypatch.setattr(R.requests, "post", post)
    monkeypatch.setenv("OPENROUTER_API_KEY", SECRET)
    rc = R.main(["--smoke", "--config-dir", str(cfg), "--results-dir", str(tmp_path / "res")])
    assert rc == 0 and len(post.calls) == 1
    files = [p.relative_to(tmp_path / "res" / "raw").as_posix() for p in (tmp_path / "res" / "raw").rglob("*.json")]
    assert files == ["cheap/standard/p01_small/rep0_round0.json"]
    assert SECRET not in capsys.readouterr().out


def test_cli_budget_stop_returns_2(tmp_path, monkeypatch, capsys):
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "experiment.yaml").write_text(yaml.safe_dump(dict(EXP, budget_usd=0.0001)))
    (cfg / "models.yaml").write_text(yaml.safe_dump({"models": [MODEL]}))
    monkeypatch.setattr(R.requests, "post", FakePost([]))
    monkeypatch.setenv("OPENROUTER_API_KEY", SECRET)
    assert R.main(["--config-dir", str(cfg), "--results-dir", str(tmp_path / "res")]) == 2
    assert "budget" in capsys.readouterr().out


# ---------------------------------------------------------------- manual provider
def test_manual_export_import_roundtrip(tmp_path):
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "experiment.yaml").write_text(yaml.safe_dump(dict(EXP, problems=["p01_small"], variants=["standard"])))
    (cfg / "models.yaml").write_text(yaml.safe_dump({"models": [dict(MODEL, label="hand", provider="manual")]}))
    base = ["--model", "hand", "--config-dir", str(cfg), "--results-dir", str(tmp_path / "res")]
    assert M.main(["export", *base]) == 0
    d = tmp_path / "res" / "manual" / "hand" / "standard" / "p01_small" / "rep0_round0"
    assert "```plan" in (d / "prompt.txt").read_text()
    (d / "response.txt").write_text(BAD_PLAN)
    assert M.main(["import", *base]) == 0
    rec = json.loads((tmp_path / "res" / "raw" / "hand" / "standard" / "p01_small" / "rep0_round0.json").read_text())
    assert rec["provider"] == "manual" and rec["response_text"] == BAD_PLAN and rec["cost_usd"] == 0.0
    # next export gives the feedback prompt for round 1
    M.main(["export", *base])
    d1 = tmp_path / "res" / "manual" / "hand" / "standard" / "p01_small" / "rep0_round1"
    assert "unknown action 'fly'" in (d1 / "prompt.txt").read_text()
    (d1 / "response.txt").write_text(GOOD_PLAN)
    M.main(["import", *base])
    M.main(["export", *base])
    assert not (tmp_path / "res" / "manual" / "hand" / "standard" / "p01_small" / "rep0_round2").exists()
    # the OpenRouter runner never touches manual models
    assert R.run_all(make_ctx(tmp_path, FakePost([])), [dict(MODEL, provider="manual")], [INST]) == 0

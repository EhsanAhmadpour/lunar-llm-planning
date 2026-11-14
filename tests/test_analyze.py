"""Analysis must work on empty, partial and mocked results."""
import csv
import json
from pathlib import Path

from conftest import ROOT
from src import analyze as A
from test_runner import (BAD_PLAN, GOOD_PLAN, INST, MODEL, FakePost, FakeResp, make_ctx)
from src.run_llm import run_episode


def test_analyze_on_empty_results(tmp_path):
    (tmp_path / "config").mkdir()
    assert A.main(root=tmp_path) == 0
    assert (tmp_path / "figures" / "validity.png").exists()
    assert "no data yet" in (tmp_path / "results" / "tables.md").read_text()
    assert (tmp_path / "results" / "summary.csv").read_text().startswith("model,variant,size")


def test_analyze_real_repo_with_empty_results_dir(tmp_path):
    assert A.main(root=ROOT, results_dir=tmp_path / "r", figures_dir=tmp_path / "f") == 0


def test_analyze_partial_and_full_results(tmp_path):
    res = tmp_path / "results"
    # episode 1: invalid at round 0, valid at round 1; episode 2 (rep1): still unfinished (invalid, 1 of 4 rounds)
    ctx = make_ctx(tmp_path, FakePost([FakeResp(text=BAD_PLAN), FakeResp(text=GOOD_PLAN),
                                       FakeResp(text=BAD_PLAN)]), budget=9.0)
    run_episode(ctx, MODEL, INST, 0)
    ctx.max_rounds = 0          # run only round 0 of rep1 -> unfinished episode
    run_episode(ctx, MODEL, INST, 1)
    assert A.main(root=ROOT, results_dir=res, figures_dir=tmp_path / "fig") == 0
    rows = list(csv.DictReader((res / "summary.csv").open()))
    allrow = next(r for r in rows if r["size"] == "all")
    assert allrow["n_episodes"] == "2" and allrow["pct_valid_round0"] == "0.0"
    assert allrow["n_finished"] == "1" and allrow["pct_valid_with_verifier"] == "100.0"
    assert allrow["mean_rounds_to_valid"] == "1.0" and allrow["mean_length_ratio"] == "1.0"
    cats = list(csv.DictReader((res / "error_categories.csv").open()))[0]
    assert cats["syntax"] == "2" and cats["n_invalid_round0"] == "2"
    assert "T1" in (res / "tables.md").read_text()
    assert (tmp_path / "fig" / "validity.png").stat().st_size > 1000

"""Thin wrapper around the Fast Downward planner (used by the nl-translate pipeline).

Why not pyperplan here: pyperplan's greedy search is very sensitive to the order of facts and to
Python's hash seed. On a perfect copy of p05_large it finds a plan in 2 s with PYTHONHASHSEED=0 and
times out after 60 s with other seeds. A correct LLM translation must not fail by bad luck, so the
pipeline uses Fast Downward with the SAME algorithm as pyperplan's baseline (greedy best-first search
with the FF heuristic), plus LAMA; see PORTFOLIO.

Fast Downward comes as a compiled binary inside the pip package `up-fast-downward`
(requirements.txt). Exit codes: https://www.fast-downward.org/ExitCodes
"""
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# A small portfolio, tried in order until one finds a plan: each single configuration fails on one of
# our 18 tasks within 60 s (LAMA on p06_large, eager greedy hFF on p05_large), together they solve all.
PORTFOLIO = [
    ("lama-first", ["--alias", "lama-first"]),
    ("eager-greedy-ff", ["--evaluator", "h=ff()", "--search", "eager_greedy([h])"]),
]
UNSOLVABLE = {10, 11, 12}        # proved unsolvable (translator or search) / search space exhausted
TIMEOUT = {23}
INPUT_ERROR = {30, 31, 32, 33, 34, 35, 36, 37}


def driver():
    # find the package folder without importing it (importing needs the unified-planning library)
    import importlib.util
    spec = importlib.util.find_spec("up_fast_downward")
    if spec is None:
        raise RuntimeError("Fast Downward is missing: pip install -r requirements.txt")
    return Path(spec.origin).parent / "downward" / "fast-downward.py"


def solve(domain, problem, timeout=60):
    """Try the portfolio in order, `timeout` seconds per configuration.
    Returns dict(status, plan, time_s, error, config); status: solved | unsolvable | timeout | error.
    'unsolvable' (proved) ends the portfolio at once; time_s is the total over the configurations tried."""
    total, last = 0.0, None
    for name, args in PORTFOLIO:
        res = _run(domain, problem, args, timeout)
        total += res["time_s"]
        res.update(time_s=total, config=name)
        if res["status"] in ("solved", "unsolvable", "error"):
            return res
        last = res
    return last


def _run(domain, problem, search_args, timeout):
    with tempfile.TemporaryDirectory() as work:
        cmd = [sys.executable, str(driver()), *(search_args[:2] if search_args[0] == "--alias" else []),
               "--overall-time-limit", f"{int(timeout)}s",
               str(Path(domain).resolve()), str(Path(problem).resolve()),
               *([] if search_args[0] == "--alias" else search_args)]
        t0 = time.time()
        try:
            proc = subprocess.run(cmd, cwd=work, capture_output=True, text=True, timeout=timeout + 30)
        except subprocess.TimeoutExpired:
            return dict(status="timeout", plan=None, time_s=time.time() - t0, error=None)
        elapsed = time.time() - t0
        rc = proc.returncode
        plan_file = Path(work) / "sas_plan"
        if rc == 0 and plan_file.exists():
            plan = [ln.strip() for ln in plan_file.read_text().splitlines() if ln.strip().startswith("(")]
            return dict(status="solved", plan=plan, time_s=elapsed, error=None)
        if rc in UNSOLVABLE:
            return dict(status="unsolvable", plan=None, time_s=elapsed, error=None)
        if rc in TIMEOUT:
            return dict(status="timeout", plan=None, time_s=elapsed, error=None)
        tail = [ln for ln in (proc.stdout + proc.stderr).splitlines() if ln.strip()][-3:]
        return dict(status="error", plan=None, time_s=elapsed, error=f"exit code {rc}: " + " | ".join(tail))

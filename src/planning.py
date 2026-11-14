"""Thin wrapper around pyperplan with a hard time limit.

pyperplan has no built-in timeout, so each search runs in a child process that
we kill when time is up.
"""
import multiprocessing as mp
import os
import sys
import time

from pyperplan.planner import HEURISTICS, SEARCHES, search_plan


def _worker(conn, domain, problem, search, heuristic):
    try:
        heur = HEURISTICS[heuristic] if heuristic else None
        plan = search_plan(domain, problem, SEARCHES[search], heur)
        conn.send(("ok", None if plan is None else [op.name for op in plan]))
    except Exception as exc:  # report, never crash the parent
        conn.send(("error", repr(exc)))
    finally:
        conn.close()


def run_search(domain, problem, search, heuristic, timeout):
    """Run one pyperplan search.

    Returns dict(status, plan, time_s) where status is one of
    'solved', 'unsolvable', 'timeout', 'error'.
    """
    ctx = mp.get_context("fork") if "fork" in mp.get_all_start_methods() else mp.get_context()
    parent, child = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_worker, args=(child, str(domain), str(problem), search, heuristic))
    t0 = time.time()
    proc.start()
    child.close()
    result = None
    if parent.poll(timeout):
        try:
            result = parent.recv()
        except EOFError:
            result = ("error", "worker died")
    elapsed = time.time() - t0
    if proc.is_alive():
        proc.terminate()
    proc.join()
    if result is None:
        return {"status": "timeout", "plan": None, "time_s": elapsed}
    kind, payload = result
    if kind == "error":
        return {"status": "error", "plan": None, "time_s": elapsed, "error": payload}
    if payload is None:
        return {"status": "unsolvable", "plan": None, "time_s": elapsed}
    return {"status": "solved", "plan": payload, "time_s": elapsed}


def solve_best(domain, problem, optimal_timeout=120, fallback_timeout=600):
    """Optimal plan (A* + LM-cut) if it finishes in time, else GBFS + hFF.

    Returns dict(status, plan, time_s, search, heuristic, optimal).
    """
    res = run_search(domain, problem, "astar", "lmcut", optimal_timeout)
    if res["status"] in ("solved", "unsolvable"):
        res.update(search="astar", heuristic="lmcut", optimal=res["status"] == "solved")
        return res
    # time_s is the time of the search that produced the plan; the failed
    # optimal attempt is reported separately.
    res = run_search(domain, problem, "gbf", "hff", fallback_timeout)
    res.update(search="gbf", heuristic="hff", optimal=False, optimal_timeout_s=optimal_timeout)
    return res


def ensure_fixed_hash_seed():
    """Make pyperplan's search order reproducible.

    pyperplan keeps states in Python sets, whose order depends on Python's
    per-process string-hash randomisation. For satisficing searches (GBFS) that
    can change WHICH plan is found between runs. We therefore re-start the
    current command once with PYTHONHASHSEED=0.
    """
    if os.environ.get("PYTHONHASHSEED") != "0" and hasattr(sys, "orig_argv"):
        env = dict(os.environ, PYTHONHASHSEED="0")
        os.execve(sys.executable, sys.orig_argv, env)

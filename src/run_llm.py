"""Run LLMs on the lunar planning instances through OpenRouter.

Usage examples (see README.md):
    python -m src.run_llm --estimate            # cost table, no API calls
    python -m src.run_llm --smoke               # cheapest model, 1 small problem, round 0
    python -m src.run_llm                       # the full experiment
    python -m src.run_llm --models cheap-a --variants standard --problems p01_small

What happens for each (model, instance, repeat) -- one "episode":
    round 0   the direct-planning prompt (prompts/direct.txt)
    round 1-3 only while the plan is invalid: we send the validator's error
              message (prompts/feedback.txt) in the SAME conversation
    stop at the first valid plan.
So one run gives both "direct" (round 0) and "with verifier" (rounds <= 3).

Safety:
  * Every request/response is saved to results/raw/... ; re-running skips
    anything already saved, so you never pay twice.
  * results/spend.json keeps a running cost total; before each call we
    estimate its worst-case cost and stop if total + worst case > budget.
  * The API key comes from the environment (OPENROUTER_API_KEY) or a local
    .env file. It is never printed or saved.
"""
import argparse
import json
import math
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

from .pddl_utils import NL_VARIANTS, ROOT, briefing_for, domain_for, problem_for, read_text, strip_comments
from .translate import check_translation
from .validate import validate_text, default_mapping_for

API_URL = "https://openrouter.ai/api/v1/chat/completions"
FEEDBACK_TOKENS_GUESS = 150      # size of a feedback message, for cost estimates
EXPECTED_ROUNDS = 1.6            # --estimate: average number of calls per episode
EXPECTED_OUT_FRACTION = 0.5      # --estimate: replies use half of max_tokens on average


class BudgetExceeded(Exception):
    pass


class ApiError(Exception):
    """A call failed after retries; the episode is skipped."""


class FatalApiError(Exception):
    """Bad key / no credit: no point continuing."""


class ModelUnavailable(Exception):
    """This model id or its settings were rejected (HTTP 400/404): skip the model, keep going."""


# --------------------------------------------------------------------------
# Config and instances
# --------------------------------------------------------------------------
@dataclass
class Instance:
    variant: str      # standard | mystery | rovers | nl-plan | nl-translate
    name: str         # e.g. p01_small
    size: str         # small | medium | large
    domain: Path
    problem: Path     # the PDDL problem replies are checked against (for nl-*: problems/standard/...)


def load_yaml(path):
    return yaml.safe_load(Path(path).read_text()) or {}


def load_config(config_dir=None):
    config_dir = Path(config_dir) if config_dir else ROOT / "config"
    exp = load_yaml(config_dir / "experiment.yaml")
    models = load_yaml(config_dir / "models.yaml").get("models", []) or []
    exp.setdefault("budget_usd", 9.0)
    exp.setdefault("max_feedback_rounds", 3)
    exp.setdefault("temperature", 0)
    exp.setdefault("problems", "all")
    exp.setdefault("variants", ["standard", "mystery"])
    exp.setdefault("request_timeout_s", 120)
    exp.setdefault("max_retries", 5)
    for m in models:
        m.setdefault("provider", "openrouter")
        m.setdefault("repeats", 1)
        m.setdefault("max_tokens", 2000)
        m.setdefault("price_in", 0.0)
        m.setdefault("price_out", 0.0)
    return exp, models


def list_instances(variants=("standard", "mystery"), problems="all", root=ROOT):
    root = Path(root)
    out = []
    for variant in variants:
        domain = domain_for(variant, root)
        if variant in NL_VARIANTS:   # one instance per briefing; checked against the standard problem
            names = sorted(p.stem for p in (root / "problems" / "nl").glob("p[0-9]*.txt"))
        else:
            names = sorted(p.stem for p in (root / "problems" / variant).glob("[pr][0-9]*.pddl"))
        for name in names:
            if problems != "all" and name not in problems:
                continue
            out.append(Instance(variant, name, name.split("_")[-1], domain, problem_for(variant, name, root)))
    return out


def select_models(models, only=None):
    if only:
        wanted = set(only)
        models = [m for m in models if m["label"] in wanted or m["id"] in wanted]
    return models


# --------------------------------------------------------------------------
# Prompts
# --------------------------------------------------------------------------
PROMPTS = {"nl-plan": ("nl_plan.txt", "feedback.txt"), "nl-translate": ("nl_translate.txt", "nl_translate_feedback.txt")}


def render_direct(inst, root=ROOT):
    first, _ = PROMPTS.get(inst.variant, ("direct.txt", None))
    tpl = read_text(Path(root) / "prompts" / first)
    out = tpl.replace("{{DOMAIN}}", strip_comments(read_text(inst.domain)).strip())
    if inst.variant in NL_VARIANTS:
        return out.replace("{{BRIEFING}}", read_text(briefing_for(inst.name, root)).strip())
    return out.replace("{{PROBLEM}}", strip_comments(read_text(inst.problem)).strip())


def render_feedback(validation, root=ROOT, inst=None):
    _, fb = PROMPTS.get(getattr(inst, "variant", None), (None, "feedback.txt"))
    tpl = read_text(Path(root) / "prompts" / fb)
    return tpl.replace("{{REPORT}}", validation["error_message"] or "The plan is invalid.")


def est_tokens(messages):
    """Rough token count: characters / 3 (PDDL is symbol-heavy, so this errs on the high side)."""
    return math.ceil(sum(len(m["content"]) for m in messages) / 3)


# --------------------------------------------------------------------------
# Spend tracking and budget guard
# --------------------------------------------------------------------------
class SpendTracker:
    def __init__(self, results_dir, budget_usd):
        self.path = Path(results_dir) / "spend.json"
        self.raw = Path(results_dir) / "raw"
        self.budget = float(budget_usd)
        self.lock = threading.Lock()   # several models may run at the same time (--parallel)
        self.reserved = 0.0            # worst-case cost of the calls that are in flight right now
        if self.path.exists():
            data = json.loads(self.path.read_text())
            self.total = float(data.get("total_usd", 0.0))
            self.calls = int(data.get("n_calls", 0))
        else:  # rebuild from saved responses
            self.total, self.calls = 0.0, 0
            for f in self.raw.rglob("*.json") if self.raw.exists() else []:
                try:
                    self.total += float(json.loads(f.read_text()).get("cost_usd") or 0.0)
                    self.calls += 1
                except (ValueError, OSError):
                    pass

    def check(self, worst_case):
        if self.total + worst_case > self.budget:
            raise BudgetExceeded(
                f"Stopping: spent ${self.total:.4f} + worst case for the next call ${worst_case:.4f} "
                f"would exceed the budget ${self.budget:.2f}. Raise budget_usd in config/experiment.yaml "
                f"to continue; saved results are kept and the run can be resumed.")

    def reserve(self, worst_case):
        """Budget check for one call that is about to start. Calls already in flight are
        counted at their worst case, so parallel models can never overshoot the budget together."""
        with self.lock:
            self.check(worst_case + self.reserved)
            self.reserved += worst_case

    def release(self, worst_case):
        with self.lock:
            self.reserved = max(0.0, self.reserved - worst_case)

    def add(self, cost):
        with self.lock:
            self.total += float(cost)
            self.calls += 1
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(
                {"total_usd": round(self.total, 6), "n_calls": self.calls, "budget_usd": self.budget},
                indent=2) + "\n")


def worst_case_cost(model, messages):
    return (est_tokens(messages) * model["price_in"] + model["max_tokens"] * model["price_out"]) / 1e6


# --------------------------------------------------------------------------
# API key and HTTP call
# --------------------------------------------------------------------------
def get_api_key(root=ROOT):
    key = os.environ.get("OPENROUTER_API_KEY")
    if key:
        return key.strip()
    env = Path(root) / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line.startswith("OPENROUTER_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def call_openrouter(model, messages, api_key, temperature, timeout, max_retries,
                    post=None, sleep=time.sleep):
    """One chat-completion call with retries. Returns dict(text, usage, cost, cost_source, returned_model)."""
    post = post or requests.post
    payload = {
        "model": model["id"], "messages": messages, "temperature": temperature,
        "max_tokens": model["max_tokens"], "usage": {"include": True},
    }
    if model.get("reasoning") is not None:
        # optional per-model setting from models.yaml, passed to OpenRouter as-is,
        # e.g. {effort: low} or {enabled: false}. Thinking tokens are billed as output
        # and count towards max_tokens.
        payload["reasoning"] = model["reasoning"]
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    last = None
    for attempt in range(max_retries + 1):
        try:
            resp = post(API_URL, headers=headers, json=payload, timeout=timeout)
        except (requests.Timeout, requests.ConnectionError) as exc:
            last = f"network error: {type(exc).__name__}"
        else:
            code = resp.status_code
            if code == 200:
                try:
                    data = resp.json()
                    text = data["choices"][0]["message"].get("content") or ""
                except (ValueError, KeyError, IndexError, TypeError):
                    raise ApiError("unexpected response body from OpenRouter")
                usage = data.get("usage") or {}
                pt, ct = usage.get("prompt_tokens"), usage.get("completion_tokens")
                if pt is None:
                    pt = est_tokens(messages)
                if ct is None:
                    ct = math.ceil(len(text) / 4)
                if usage.get("cost") is not None:
                    cost, src = float(usage["cost"]), "openrouter"
                else:
                    cost, src = (pt * model["price_in"] + ct * model["price_out"]) / 1e6, "computed"
                return dict(text=text, usage={"prompt_tokens": pt, "completion_tokens": ct},
                            cost=cost, cost_source=src, returned_model=data.get("model"))
            if code in (400, 404):
                raise ModelUnavailable(f"HTTP {code} from OpenRouter for model '{model['id']}' "
                                       f"(wrong id, or a setting such as 'reasoning' is not supported)")
            if code in (401, 402, 403):
                raise FatalApiError(f"HTTP {code} from OpenRouter (check API key and credit)")
            last = f"HTTP {code}"
            if code != 429 and code < 500:
                raise ApiError(last)
        if attempt < max_retries:
            sleep(min(60, 2 ** attempt))
    raise ApiError(f"giving up after {max_retries + 1} attempts ({last})")


# --------------------------------------------------------------------------
# Episodes
# --------------------------------------------------------------------------
@dataclass
class Context:
    results_dir: Path
    exp: dict
    root: Path = ROOT
    max_rounds: int = 3
    post: object = None
    sleep: object = time.sleep
    api_key: str = None
    tracker: SpendTracker = None
    log: object = print


def raw_path(ctx, model, inst, rep, rnd):
    return (Path(ctx.results_dir) / "raw" / model["label"] / inst.variant / inst.name
            / f"rep{rep}_round{rnd}.json")


def validate_response(inst, text, results_dir=None):
    """Check one reply. nl-translate replies are PDDL problems: they go through the planner
    pipeline in src/translate.py; every other reply is a plan for our validator."""
    if inst.variant == "nl-translate":
        cache = Path(results_dir) / "translate_cache" if results_dir else None
        return check_translation(inst.domain, inst.problem, text, cache)
    return validate_text(inst.domain, inst.problem, text, _mapping(inst))


def _mapping(inst):
    return default_mapping_for(inst.domain)


def pending_round(ctx, model, inst, rep):
    """Walk the saved rounds of an episode.

    Returns None if the episode is finished (valid plan found or all rounds
    used), else (round_number, messages_to_send) for the next missing round.
    """
    messages = None
    for rnd in range(ctx.max_rounds + 1):
        path = raw_path(ctx, model, inst, rep, rnd)
        if path.exists():
            rec = json.loads(path.read_text())
            res = validate_response(inst, rec["response_text"], ctx.results_dir)
            if res["valid"] or rnd == ctx.max_rounds:
                return None
            messages = rec["messages"] + [
                {"role": "assistant", "content": rec["response_text"]},
                {"role": "user", "content": render_feedback(res, ctx.root, inst)},
            ]
            continue
        if rnd == 0:
            messages = [{"role": "user", "content": render_direct(inst, ctx.root)}]
        return rnd, messages
    return None


def save_record(ctx, model, inst, rep, rnd, messages, text, usage, cost, cost_source,
                returned_model, provider="openrouter"):
    rec = dict(
        model_label=model["label"], model_id=model["id"], returned_model=returned_model,
        variant=inst.variant, problem=inst.name, rep=rep, round=rnd, provider=provider,
        messages=messages, response_text=text, usage=usage, cost_usd=cost, cost_source=cost_source,
        timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    path = raw_path(ctx, model, inst, rep, rnd)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, indent=2) + "\n")
    tmp.replace(path)  # atomic: a half-written file never counts as saved
    return rec


def run_episode(ctx, model, inst, rep):
    """Run all missing rounds of one episode. Returns number of API calls made."""
    calls = 0
    while True:
        pend = pending_round(ctx, model, inst, rep)
        if pend is None:
            return calls
        rnd, messages = pend
        worst = worst_case_cost(model, messages)
        ctx.tracker.reserve(worst)
        try:
            out = call_openrouter(model, messages, ctx.api_key, ctx.exp["temperature"],
                                  ctx.exp["request_timeout_s"], ctx.exp["max_retries"],
                                  post=ctx.post, sleep=ctx.sleep)
            save_record(ctx, model, inst, rep, rnd, messages, out["text"], out["usage"], out["cost"],
                        out["cost_source"], out["returned_model"])
            ctx.tracker.add(out["cost"])
        finally:
            ctx.tracker.release(worst)
        calls += 1
        res = validate_response(inst, out["text"], ctx.results_dir)
        ctx.log(f"  {model['label']} {inst.variant}/{inst.name} rep{rep} round{rnd}: "
                f"{'VALID' if res['valid'] else res['error_category']}  "
                f"(cost ${out['cost']:.4f}, total ${ctx.tracker.total:.4f})")


def run_model(ctx, model, instances, repeats_override=None, stop=None):
    """All episodes of one model. Returns the number of API calls made."""
    n_calls = failures = 0
    reps = repeats_override or model["repeats"]
    for inst, rep in [(i, r) for i in instances for r in range(reps)]:
        if stop is not None and stop.is_set():   # another model hit the budget or a fatal error
            break
        try:
            n_calls += run_episode(ctx, model, inst, rep)
        except ModelUnavailable as exc:
            if n_calls == 0:      # rejected before any call worked: wrong id or unsupported setting
                ctx.log(f"  SKIPPING MODEL {model['label']}: {exc}")
                break
            # the model works, so this request itself was refused (e.g. the conversation no longer
            # fits an old model's small context window): skip only this episode
            ctx.log(f"  skipped {model['label']} {inst.variant}/{inst.name} rep{rep}: {exc}")
        except ApiError as exc:
            failures += 1
            ctx.log(f"  skipped {model['label']} {inst.variant}/{inst.name} rep{rep}: {exc}")
            if failures >= 5:
                ctx.log(f"  SKIPPING MODEL {model['label']}: 5 episodes failed in a row")
                break
        else:
            failures = 0
    return n_calls


def run_all(ctx, models, instances, repeats_override=None, parallel=1):
    """Run every OpenRouter model. parallel > 1 runs that many MODELS at the same time
    (each model still does its own calls one after the other)."""
    models = [m for m in models if m["provider"] == "openrouter"]
    if parallel <= 1 or len(models) <= 1:
        return sum(run_model(ctx, m, instances, repeats_override) for m in models)
    stop = threading.Event()

    def work(model):
        try:
            return run_model(ctx, model, instances, repeats_override, stop)
        except (BudgetExceeded, FatalApiError):
            stop.set()
            raise

    with ThreadPoolExecutor(max_workers=parallel) as pool:
        futures = [pool.submit(work, m) for m in models]
    errors = [f.exception() for f in futures if f.exception() is not None]
    if errors:
        raise errors[0]
    return sum(f.result() for f in futures)


# --------------------------------------------------------------------------
# Cost estimate
# --------------------------------------------------------------------------
def estimate(models, instances, exp, root=ROOT):
    """Return (rows, total_worst, total_expected); no API calls."""
    rounds = exp["max_feedback_rounds"]
    p0s = [est_tokens([{"content": render_direct(i, root)}]) for i in instances]
    rows = []
    for m in models:
        if m["provider"] != "openrouter":
            continue
        episodes = len(instances) * m["repeats"]
        worst = expected = 0.0
        for p0 in p0s:
            for _ in range(m["repeats"]):
                for j in range(rounds + 1):  # worst: every round, every reply at max_tokens
                    p = p0 + j * (m["max_tokens"] + FEEDBACK_TOKENS_GUESS)
                    worst += (p * m["price_in"] + m["max_tokens"] * m["price_out"]) / 1e6
                extra = EXPECTED_ROUNDS - 1
                p_in = p0 * 1 + extra * (p0 + EXPECTED_OUT_FRACTION * m["max_tokens"] + FEEDBACK_TOKENS_GUESS)
                p_out = EXPECTED_ROUNDS * EXPECTED_OUT_FRACTION * m["max_tokens"]
                expected += (p_in * m["price_in"] + p_out * m["price_out"]) / 1e6
        rows.append(dict(label=m["label"], id=m["id"], tier=m.get("tier", ""), episodes=episodes,
                         worst=worst, expected=expected))
    return rows, sum(r["worst"] for r in rows), sum(r["expected"] for r in rows)


def print_estimate(rows, total_worst, total_expected, budget, out=print):
    out(f"{'model':<16}{'tier':<8}{'episodes':>9}{'worst-case $':>14}{'expected $':>12}")
    for r in rows:
        out(f"{r['label']:<16}{r['tier']:<8}{r['episodes']:>9}{r['worst']:>14.2f}{r['expected']:>12.2f}")
    out(f"{'TOTAL':<22}{'':>9}{total_worst:>14.2f}{total_expected:>12.2f}")
    out(f"Budget cap: ${budget:.2f}. Worst case = every episode uses all rounds and every reply hits "
        f"max_tokens; expected = {EXPECTED_ROUNDS} calls per episode, replies use "
        f"{int(EXPECTED_OUT_FRACTION * 100)}% of max_tokens. Prices come from config/models.yaml "
        f"(checked on the date noted there).")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def add_filter_args(ap):
    ap.add_argument("--models", help="comma-separated model labels (or ids)")
    ap.add_argument("--problems", help="comma-separated problem names, e.g. p01_small,p03_medium")
    ap.add_argument("--variants", help="comma-separated: standard,mystery,rovers,nl-plan,nl-translate")
    ap.add_argument("--config-dir", default=None)
    ap.add_argument("--results-dir", default=None)


def apply_filters(args, exp, models):
    models = select_models(models, args.models.split(",") if args.models else None)
    problems = args.problems.split(",") if args.problems else exp["problems"]
    variants = args.variants.split(",") if args.variants else exp["variants"]
    return models, list_instances(variants, problems)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Run LLM planning experiments via OpenRouter.")
    add_filter_args(ap)
    ap.add_argument("--estimate", action="store_true", help="print cost estimate, no API calls")
    ap.add_argument("--smoke", action="store_true", help="cheapest model, 1 small problem, round 0 only")
    ap.add_argument("--parallel", type=int, default=1, help="how many models to run at the same time")
    args = ap.parse_args(argv)
    exp, models = load_config(args.config_dir)
    models, instances = apply_filters(args, exp, models)
    results_dir = Path(args.results_dir) if args.results_dir else ROOT / "results"

    if args.estimate:
        rows, w, e = estimate(models, instances, exp)
        print_estimate(rows, w, e, exp["budget_usd"])
        return 0

    models = [m for m in models if m["provider"] == "openrouter"]
    if not models:
        print("No OpenRouter models selected (check config/models.yaml and --models).")
        return 1
    max_rounds = exp["max_feedback_rounds"]
    repeats = None
    if args.smoke:
        models = [min(models, key=lambda m: m["price_in"] + m["price_out"])]
        smalls = [i for i in instances if i.size == "small" and i.variant == "standard"] or instances
        instances, max_rounds, repeats = smalls[:1], 0, 1
        print(f"Smoke test: model {models[0]['label']}, problem {instances[0].name}, round 0 only.")
    key = get_api_key()
    if not key:
        print("OPENROUTER_API_KEY is not set (environment variable or .env file).")
        return 1
    ctx = Context(results_dir=results_dir, exp=exp, max_rounds=max_rounds, api_key=key,
                  tracker=SpendTracker(results_dir, exp["budget_usd"]))
    try:
        n = run_all(ctx, models, instances, repeats, parallel=args.parallel)
    except BudgetExceeded as exc:
        print(str(exc))
        return 2
    except FatalApiError as exc:
        print(f"Fatal API error: {exc}")
        return 3
    print(f"Done: {n} API calls; total spend so far ${ctx.tracker.total:.4f} of ${exp['budget_usd']:.2f}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

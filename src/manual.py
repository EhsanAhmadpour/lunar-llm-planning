"""Run a model BY HAND (for models that are not on OpenRouter).

Two steps, repeated until nothing is pending:

  python -m src.manual export --model my-label
      Writes each pending prompt to
      results/manual/<label>/<variant>/<problem>/rep<k>_round<j>/prompt.txt
      (plus messages.json = the whole conversation so far).
      Paste prompt.txt into the chat tool of your choice. For round >= 1 paste
      it as the NEXT message of the SAME chat as the previous round.

  (save the model's reply as response.txt in the same folder)

  python -m src.manual import --model my-label
      Reads every response.txt, validates it, and stores it in the same
      results/raw/... format the API runner uses. Run `export` again to get
      the feedback prompts for plans that were not valid yet.

The model must exist in config/models.yaml (use `provider: manual`).
"""
import argparse
import json
import sys
from pathlib import Path

from .pddl_utils import ROOT
from . import run_llm as R


def manual_dir(ctx, model, inst, rep, rnd):
    return (Path(ctx.results_dir) / "manual" / model["label"] / inst.variant / inst.name
            / f"rep{rep}_round{rnd}")


def _setup(args):
    exp, models = R.load_config(args.config_dir)
    models, instances = R.apply_filters(args, exp, models)
    models = [m for m in models if m["label"] == args.model or m["id"] == args.model]
    if not models:
        raise SystemExit(f"Model {args.model!r} not found in config/models.yaml")
    results_dir = Path(args.results_dir) if args.results_dir else ROOT / "results"
    ctx = R.Context(results_dir=results_dir, exp=exp, max_rounds=exp["max_feedback_rounds"])
    return ctx, models[0], instances


def export_prompts(ctx, model, instances):
    """Write prompt.txt for every episode's next missing round. Returns list of dirs."""
    out = []
    for inst in instances:
        for rep in range(model["repeats"]):
            pend = R.pending_round(ctx, model, inst, rep)
            if pend is None:
                continue
            rnd, messages = pend
            d = manual_dir(ctx, model, inst, rep, rnd)
            d.mkdir(parents=True, exist_ok=True)
            (d / "prompt.txt").write_text(messages[-1]["content"])
            (d / "messages.json").write_text(json.dumps(messages, indent=2) + "\n")
            out.append(d)
    return out


def import_responses(ctx, model, instances):
    """Turn response.txt files into raw records. Returns number imported."""
    n = 0
    for inst in instances:
        for rep in range(model["repeats"]):
            pend = R.pending_round(ctx, model, inst, rep)
            if pend is None:
                continue
            rnd, messages = pend
            d = manual_dir(ctx, model, inst, rep, rnd)
            resp = d / "response.txt"
            if not resp.exists() or not resp.read_text().strip():
                continue
            R.save_record(ctx, model, inst, rep, rnd, messages, resp.read_text(),
                          {"prompt_tokens": R.est_tokens(messages), "completion_tokens": None},
                          0.0, "manual", None, provider="manual")
            n += 1
    return n


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["export", "import"])
    ap.add_argument("--model", required=True, help="model label from config/models.yaml")
    R.add_filter_args(ap)
    args = ap.parse_args(argv)
    ctx, model, instances = _setup(args)
    if args.action == "export":
        dirs = export_prompts(ctx, model, instances)
        for d in dirs:
            print(f"prompt written: {d / 'prompt.txt'}")
        print(f"{len(dirs)} pending prompt(s).")
    else:
        n = import_responses(ctx, model, instances)
        print(f"imported {n} response(s). Run `export` again for follow-up (feedback) prompts.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

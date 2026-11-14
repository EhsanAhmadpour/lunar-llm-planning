"""Turn saved LLM results into tables and a figure.

Usage:  python -m src.analyze

Reads  results/raw/**.json (+ results/baseline.csv if present)
Writes results/summary.csv, results/error_categories.csv, results/tables.md,
       figures/validity.png
Works on empty or partial results (it just prints empty tables).

Definitions
  episode          one (model, variant, problem, repeat) run: round 0 plus any feedback rounds
  direct           the plan from round 0 (no verifier feedback)
  with verifier    valid at ANY round <= max_feedback_rounds. Only FINISHED episodes count
                   (valid at some round, or all rounds used); unfinished ones are left out.
  rounds to valid  0 if the direct plan was valid, 1 after one feedback message, ...
  length ratio     length of the final valid plan / length of the reference plan
"""
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from .pddl_utils import ROOT
from .validate import validate_text, default_mapping_for

CATEGORIES = ["syntax", "terrain", "battery", "communication", "equipment", "state", "goal_not_reached"]
SIZES = ["small", "medium", "large"]
_FNAME = re.compile(r"rep(\d+)_round(\d+)\.json$")


def load_episodes(results_dir, root=ROOT, max_rounds=3):
    """Return list of episode dicts built from results/raw."""
    raw = Path(results_dir) / "raw"
    groups = defaultdict(dict)
    if raw.exists():
        for f in raw.rglob("rep*_round*.json"):
            m = _FNAME.search(f.name)
            if not m:
                continue
            try:
                rec = json.loads(f.read_text())
            except ValueError:
                continue
            groups[(rec["model_label"], rec["variant"], rec["problem"], int(m.group(1)))][int(m.group(2))] = rec
    episodes = []
    for (label, variant, problem, rep), rounds in sorted(groups.items()):
        domain = Path(root) / "domain" / ("lunar.pddl" if variant == "standard" else "mystery.pddl")
        problem_file = Path(root) / "problems" / variant / f"{problem}.pddl"
        if not problem_file.exists() or 0 not in rounds:
            continue
        mapping = default_mapping_for(domain)
        results = {}
        for r in sorted(rounds):
            if r > max_rounds:
                continue
            results[r] = validate_text(domain, problem_file, rounds[r]["response_text"], mapping)
        solved = next((r for r in sorted(results) if results[r]["valid"]), None)
        finished = solved is not None or len(results) == max_rounds + 1
        episodes.append(dict(
            model=label, variant=variant, problem=problem, size=problem.split("_")[-1], rep=rep,
            direct=results[0], solved_round=solved, finished=finished,
            final_length=results[solved]["plan_length"] if solved is not None else None,
            cost=sum(float(rounds[r].get("cost_usd") or 0) for r in rounds)))
    return episodes


def reference_lengths(results_dir, root=ROOT):
    lengths = {}
    base = Path(results_dir) / "baseline.csv"
    if base.exists():
        for row in csv.DictReader(base.open()):
            if row["variant"] == "standard" and row["plan_length"]:
                lengths[row["problem"]] = int(row["plan_length"])
    for j in (Path(root) / "problems" / "standard").glob("*.json"):
        meta = json.loads(j.read_text())
        lengths.setdefault(meta["name"], meta["reference"]["plan_length"])
    return lengths


def _pct(a, b):
    return round(100.0 * a / b, 1) if b else None


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 2) if xs else None


def t1_rows(episodes, ref_len):
    rows = []
    keys = sorted({(e["model"], e["variant"]) for e in episodes})
    for model, variant in keys:
        for size in ["all"] + SIZES:
            es = [e for e in episodes if e["model"] == model and e["variant"] == variant
                  and (size == "all" or e["size"] == size)]
            if not es:
                continue
            fin = [e for e in es if e["finished"]]
            solved = [e for e in fin if e["solved_round"] is not None]
            ratios = [e["final_length"] / ref_len[e["problem"]] for e in solved if e["problem"] in ref_len]
            rows.append(dict(
                model=model, variant=variant, size=size,
                n_episodes=len(es), pct_valid_round0=_pct(sum(e["direct"]["valid"] for e in es), len(es)),
                n_finished=len(fin), pct_valid_with_verifier=_pct(len(solved), len(fin)),
                mean_rounds_to_valid=_mean([e["solved_round"] for e in solved]),
                mean_length_ratio=_mean(ratios)))
    return rows


def t2_rows(episodes):
    rows = []
    for model, variant in sorted({(e["model"], e["variant"]) for e in episodes}):
        bad = [e for e in episodes if e["model"] == model and e["variant"] == variant and not e["direct"]["valid"]]
        c = Counter(e["direct"]["error_category"] for e in bad)
        row = dict(model=model, variant=variant, n_invalid_round0=len(bad))
        for cat in CATEGORIES:
            row[cat] = c.get(cat, 0)
        rows.append(row)
    return rows


def md_table(rows, cols):
    if not rows:
        return "_no data yet_\n"
    out = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for r in rows:
        out.append("| " + " | ".join("" if r[c] is None else str(r[c]) for c in cols) + " |")
    return "\n".join(out) + "\n"


def write_csv(path, rows, cols):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def make_figure(rows, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch

    fig, ax = plt.subplots(figsize=(9, 5))
    all_rows = {(r["model"], r["variant"]): r for r in rows if r["size"] == "all"}
    models = sorted({m for m, _ in all_rows})
    if not models:
        ax.text(0.5, 0.5, "no results yet", ha="center", va="center", transform=ax.transAxes, fontsize=14)
        ax.set_axis_off()
    else:
        colors = {"standard": "#2a6fb0", "mystery": "#d9822b"}
        width = 0.2
        for i, model in enumerate(models):
            for k, (variant, field, hatch) in enumerate([
                    ("standard", "pct_valid_round0", "//"), ("standard", "pct_valid_with_verifier", ""),
                    ("mystery", "pct_valid_round0", "//"), ("mystery", "pct_valid_with_verifier", "")]):
                r = all_rows.get((model, variant))
                val = r[field] if r and r[field] is not None else 0
                x = i + (k - 1.5) * width
                ax.bar(x, val, width * 0.92, color=colors[variant], hatch=hatch,
                       alpha=0.55 if hatch else 1.0, edgecolor="white")
                ax.text(x, val + 1, f"{val:.0f}", ha="center", va="bottom", fontsize=8)
        ax.set_xticks(range(len(models)))
        ax.set_xticklabels(models, fontsize=10)
        ax.set_ylim(0, 112)
        ax.set_ylabel("% of runs with a valid plan")
        ax.set_title("Valid plans: direct vs with verifier feedback (standard vs mystery names)")
        ax.legend(handles=[
            Patch(facecolor=colors["standard"], alpha=0.55, hatch="//", edgecolor="white", label="standard, direct"),
            Patch(facecolor=colors["standard"], label="standard, with verifier"),
            Patch(facecolor=colors["mystery"], alpha=0.55, hatch="//", edgecolor="white", label="mystery, direct"),
            Patch(facecolor=colors["mystery"], label="mystery, with verifier")],
            loc="upper center", ncol=2, fontsize=9, frameon=False, bbox_to_anchor=(0.5, -0.1))
        ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150)
    plt.close(fig)


def baseline_md(results_dir):
    base = Path(results_dir) / "baseline.csv"
    if not base.exists():
        return "_run `python -m src.run_baseline` first_\n"
    rows = list(csv.DictReader(base.open()))
    return md_table(rows, ["variant", "problem", "plan_length", "optimal", "time_s", "valid"])


def main(argv=None, root=ROOT, results_dir=None, figures_dir=None):
    root = Path(root)
    results_dir = Path(results_dir) if results_dir else root / "results"
    figures_dir = Path(figures_dir) if figures_dir else root / "figures"
    max_rounds = 3
    try:
        import yaml
        max_rounds = yaml.safe_load((root / "config" / "experiment.yaml").read_text()).get("max_feedback_rounds", 3)
    except (OSError, AttributeError):
        pass
    episodes = load_episodes(results_dir, root, max_rounds)
    ref_len = reference_lengths(results_dir, root)
    t1 = t1_rows(episodes, ref_len)
    t2 = t2_rows(episodes)
    results_dir.mkdir(parents=True, exist_ok=True)
    t1_cols = ["model", "variant", "size", "n_episodes", "pct_valid_round0", "n_finished",
               "pct_valid_with_verifier", "mean_rounds_to_valid", "mean_length_ratio"]
    t2_cols = ["model", "variant", "n_invalid_round0"] + CATEGORIES
    write_csv(results_dir / "summary.csv", t1, t1_cols)
    write_csv(results_dir / "error_categories.csv", t2, t2_cols)
    md = ["# Results tables (generated by src/analyze.py)\n",
          f"Episodes analysed: {len(episodes)}. Max feedback rounds: {max_rounds}.\n",
          "## T1. Validity per model and variant (all sizes)\n",
          md_table([r for r in t1 if r["size"] == "all"], t1_cols),
          "## T1 by problem size\n", md_table([r for r in t1 if r["size"] != "all"], t1_cols),
          "## T2. First-error category at round 0 (counts among invalid direct plans)\n",
          md_table(t2, t2_cols),
          "## Baseline (pyperplan reference plans)\n", baseline_md(results_dir)]
    (results_dir / "tables.md").write_text("\n".join(md))
    make_figure(t1, figures_dir / "validity.png")
    print(f"analysed {len(episodes)} episodes -> results/summary.csv, results/tables.md, figures/validity.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())

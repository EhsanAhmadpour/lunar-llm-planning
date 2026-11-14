"""Run pyperplan on all 12 instances (standard + mystery) and validate with OUR validator.

Usage:  python -m src.run_baseline

Every plan must be valid (exit code 1 otherwise). Writes results/baseline.csv.
Search method per problem is the one recorded in the problem's JSON metadata
(A* + LM-cut if it was optimal, else GBFS + hFF), so standard and mystery
variants are solved the same way.
"""
import csv
import json
import sys

from .pddl_utils import ROOT
from .planning import ensure_fixed_hash_seed, run_search
from .run_llm import list_instances
from .validate import validate_text, default_mapping_for

FIELDS = ["variant", "problem", "size", "search", "heuristic", "optimal", "plan_length", "time_s",
          "valid", "error"]


def main():
    ensure_fixed_hash_seed()
    out_dir = ROOT / "results"
    out_dir.mkdir(exist_ok=True)
    rows, all_valid = [], True
    for inst in list_instances():
        meta = json.loads((ROOT / "problems" / "standard" / f"{inst.name}.json").read_text())["reference"]
        res = run_search(inst.domain, inst.problem, meta["search"], meta["heuristic"], 900)
        row = dict(variant=inst.variant, problem=inst.name, size=inst.size, search=meta["search"],
                   heuristic=meta["heuristic"], optimal=meta["optimal"], plan_length="", time_s=round(res["time_s"], 3),
                   valid=False, error="")
        if res["status"] == "solved":
            v = validate_text(inst.domain, inst.problem, "\n".join(res["plan"]), default_mapping_for(inst.domain))
            row.update(plan_length=len(res["plan"]), valid=v["valid"], error=v["error_message"] or "")
        else:
            row["error"] = f"planner status: {res['status']}"
        all_valid &= bool(row["valid"])
        rows.append(row)
        print(f"{inst.variant:<9}{inst.name:<12}len={row['plan_length']!s:<4}optimal={row['optimal']!s:<6}"
              f"{row['time_s']:>8}s valid={row['valid']}")
    with open(out_dir / "baseline.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    if not all_valid:
        print("ERROR: some baseline plans are not valid")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

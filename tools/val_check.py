"""Cross-check our validator against VAL (https://github.com/KCL-Planning/VAL).

Usage:  python tools/val_check.py /path/to/Validate
        (build VAL first: git clone https://github.com/KCL-Planning/VAL; mkdir build; cd build;
         cmake ..; make Validate  -> build/bin/Validate)

For every reference plan and every saved model reply (results/raw and pilot rounds 4-5, which use
the current problem files) it extracts the plan exactly as our validator does, runs VAL on it and
compares VAL's verdict (valid / not valid) with ours. Writes results/val_crosscheck.csv.
"""
import csv, json, subprocess, sys, tempfile, collections
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src.validate import validate_text, default_mapping_for, parse_plan  # noqa: E402
from src.pddl_utils import domain_for  # noqa: E402


def val_says_valid(val, domain, problem, plan_text):
    steps = parse_plan(plan_text)
    with tempfile.NamedTemporaryFile("w", suffix=".plan", delete=False) as f:
        f.write("".join(f"({s['raw']})\n" for s in steps if not s["malformed"]))
        if any(s["malformed"] for s in steps):
            f.write("(this-line-could-not-be-read)\n")
    try:
        out = subprocess.run([val, str(domain), str(problem), f.name], capture_output=True, text=True, timeout=60).stdout
    except subprocess.TimeoutExpired:
        return None, "timeout"
    finally:
        Path(f.name).unlink(missing_ok=True)
    return ("Plan valid" in out), out.strip().splitlines()[-1][:80] if out.strip() else ""


def main():
    val = sys.argv[1]
    rows, tally = [], collections.Counter()
    def check(kind, label, variant, problem, text, where):
        dom = domain_for(variant); prob = ROOT / "problems" / variant / f"{problem}.pddl"
        ours = validate_text(dom, prob, text, default_mapping_for(dom))["valid"]
        theirs, note = val_says_valid(val, dom, prob, text)
        agree = theirs is not None and ours == theirs
        tally[(kind, "agree" if agree else "DISAGREE")] += 1
        tally[(kind, "valid" if ours else "invalid")] += 1
        rows.append(dict(kind=kind, model=label, variant=variant, problem=problem, file=where, ours_valid=ours, val_valid=theirs, agree=agree, val_note="" if agree else note))
    for variant in ("standard", "mystery", "rovers"):
        for prob in sorted((ROOT / "problems" / variant).glob("*.pddl")):
            suffix = ".mystery.plan" if variant == "mystery" else ".plan"
            ref = ROOT / "problems" / "reference_plans" / f"{prob.stem}{suffix}"
            if ref.exists():
                check("reference plan", "planner", variant, prob.stem, ref.read_text(), ref.name)
    roots = [("model reply", ROOT / "results" / "raw"), ("pilot reply", ROOT / "pilot" / "round4" / "raw"), ("pilot reply", ROOT / "pilot" / "round5" / "raw")]
    for kind, root in roots:
        for f in sorted(root.rglob("rep*_round*.json")):
            r = json.loads(f.read_text())
            check(kind, r["model_label"], r["variant"], r["problem"], r["response_text"], str(f.relative_to(ROOT)))
    out = ROOT / "results" / "val_crosscheck.csv"
    with open(out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    for k in sorted(tally): print(k, tally[k])
    bad = [r for r in rows if not r["agree"]]
    print(f"{len(rows)} plans checked, {len(bad)} disagreements; details in {out.relative_to(ROOT)}")
    for r in bad[:10]: print("  ", r["file"], "ours:", r["ours_valid"], "VAL:", r["val_valid"], r["val_note"])
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

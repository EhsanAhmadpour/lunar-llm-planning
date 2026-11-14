"""Generate the six standard lunar-rover problems (2 per size), deterministically.

Usage:  python -m src.generate_problems [--seed 2026] [--optimal-timeout 120]

How it works (plain English):
  1. For each problem we draw a random lunar map with a seeded random number
     generator, so re-running gives byte-identical files.
  2. We CHECK the map against the experiment's requirements (see check_* below).
     If a check fails we draw a new map (next 'attempt') until all pass.
  3. We ask pyperplan for a reference plan and save it with metadata.
"""
import argparse
import json
import random
from pathlib import Path

from .pddl_utils import ROOT, plan_to_text
from .planning import ensure_fixed_hash_seed, run_search, solve_best

# size -> (rovers, waypoints, goals, battery levels)
SIZES = {
    "small": dict(rovers=1, waypoints=5, goals=2, levels=4),
    "medium": dict(rovers=2, waypoints=8, goals=4, levels=5),
    "large": dict(rovers=3, waypoints=12, goals=6, levels=5),
}
# (problem number, size) in the order written to disk
PLAN_OF_PROBLEMS = [(1, "small"), (2, "small"), (3, "medium"), (4, "medium"), (5, "large"), (6, "large")]


def make_instance(size, rng):
    """Draw one random instance as a plain dict (no checking yet)."""
    cfg = SIZES[size]
    n = cfg["waypoints"]
    wps = [f"w{i}" for i in range(1, n + 1)]
    rovers = [f"rover{i}" for i in range(1, cfg["rovers"] + 1)]
    levels = [f"b{i}" for i in range(cfg["levels"])]
    base = "w1"

    # Undirected "terrain graph": a random spanning tree plus a few extra edges.
    edges = set()
    for i in range(1, n):
        j = rng.randrange(0, i)
        edges.add((j, i))
    extra = max(2, n // 2)
    tries = 0
    while extra > 0 and tries < 200:
        tries += 1
        a, b = sorted(rng.sample(range(n), 2))
        if (a, b) not in edges:
            edges.add((a, b))
            extra -= 1

    # Per-rover traversability: each edge is two-way, one-way, or blocked for that rover.
    traverse = {r: set() for r in rovers}
    for r in rovers:
        for a, b in sorted(edges):
            x = rng.random()
            if x < 0.65:
                traverse[r] |= {(wps[a], wps[b]), (wps[b], wps[a])}
            elif x < 0.85:
                pair = (wps[a], wps[b]) if rng.random() < 0.5 else (wps[b], wps[a])
                traverse[r].add(pair)
            # else: blocked for this rover

    # Equipment.
    if cfg["rovers"] == 1:
        equip = {rovers[0]: {"sampler", "camera"}}
    else:
        options = [{"sampler"}, {"camera"}, {"sampler", "camera"}]
        while True:
            equip = {r: set(rng.choice(options)) for r in rovers}
            union = set().union(*equip.values())
            some_missing = any(len(e) < 2 for e in equip.values())
            if union == {"sampler", "camera"} and some_missing:
                break

    # Sunlit and comm-visible waypoints: strict minorities, chosen independently.
    k_minor = (n - 1) // 2  # strictly fewer than n/2
    sunlit = set(rng.sample(wps, rng.randint(1, max(1, min(k_minor, 2 if n <= 5 else 3)))))
    comm = set(rng.sample(wps, rng.randint(1, max(1, min(k_minor, 2 if n <= 5 else 3)))))

    # Goals: half samples, half images, at distinct non-base waypoints.
    n_goals = cfg["goals"]
    n_samples = n_goals // 2
    n_images = n_goals - n_samples
    candidates = [w for w in wps if w != base]
    rng.shuffle(candidates)
    sample_goals = sorted(candidates[:n_samples], key=wps.index)
    image_goals = sorted(candidates[n_samples:n_samples + n_images], key=wps.index)
    rest = candidates[n_samples + n_images:]
    sample_sites = list(sample_goals)
    image_targets = list(image_goals)
    if len(rest) >= 2:  # one decoy site of each kind (not a goal)
        sample_sites.append(rest[0])
        image_targets.append(rest[1])
    return dict(
        size=size, wps=wps, rovers=rovers, levels=levels, base=base,
        traverse={r: sorted(v, key=lambda p: (wps.index(p[0]), wps.index(p[1]))) for r, v in traverse.items()},
        equip={r: sorted(v) for r, v in equip.items()},
        sunlit=sorted(sunlit, key=wps.index), comm=sorted(comm, key=wps.index),
        sample_sites=sorted(sample_sites, key=wps.index), image_targets=sorted(image_targets, key=wps.index),
        sample_goals=sample_goals, image_goals=image_goals,
    )


def check_structure(inst):
    """Checks (c), (d), (e) that do not need a planner. Returns list of failure strings."""
    fails = []
    n = len(inst["wps"])
    if not (0 < len(inst["comm"]) and 2 * len(inst["comm"]) < n):
        fails.append("comm-visible waypoints are not a (non-empty) minority")
    if not (0 < len(inst["sunlit"]) and 2 * len(inst["sunlit"]) < n):
        fails.append("sunlit waypoints are not a (non-empty) minority")
    if not terrain_matters(inst):
        fails.append("terrain: no edge is blocked/one-way for some rover")
    if len(inst["rovers"]) > 1:
        if not any(len(e) < 2 for e in inst["equip"].values()):
            fails.append("every rover has every instrument")
    return fails


def goal_feasible(inst, rover, site, kind):
    """Fast pre-check (no planner): can `rover`, alone, start at base with a full
    battery, collect/photograph at `site` and then reach a comm-visible waypoint?
    Rovers never interact, so a goal that no single rover can do makes the whole
    problem unsolvable. Used to skip hopeless maps before calling the planner."""
    if kind == "sample" and "sampler" not in inst["equip"][rover]:
        return False
    if kind == "image" and "camera" not in inst["equip"][rover]:
        return False
    top = len(inst["levels"]) - 1
    adj = {}
    for a, b in inst["traverse"][rover]:
        adj.setdefault(a, []).append(b)
    start = (inst["base"], top, 0)  # (waypoint, battery level index, have item?)
    seen, todo = {start}, [start]
    while todo:
        w, lvl, have = todo.pop()
        if have and w in inst["comm"]:
            return True
        nxt = []
        if lvl > 0:
            nxt += [(b, lvl - 1, have) for b in adj.get(w, [])]
        if w in inst["sunlit"] and lvl < top:
            nxt.append((w, top, have))
        if not have and w == site:
            if kind == "image":
                nxt.append((w, lvl, 1))
            elif lvl > 0:
                nxt.append((w, lvl - 1, 1))
        for st in nxt:
            if st not in seen:
                seen.add(st)
                todo.append(st)
    return False


def check_feasible(inst):
    fails = []
    goals = [(w, "sample") for w in inst["sample_goals"]] + [(w, "image") for w in inst["image_goals"]]
    for w, kind in goals:
        if not any(goal_feasible(inst, r, w, kind) for r in inst["rovers"]):
            fails.append(f"no rover can do goal {kind}@{w}")
    return fails


def terrain_matters(inst):
    """(d) True if some ordered pair (a,b) is traversable by some rover (or in the
    reverse direction by the same rover) but is NOT traversable by some rover.
    This means the can-traverse constraint can actually bite."""
    union = set()
    for r, pairs in inst["traverse"].items():
        for a, b in pairs:
            union |= {(a, b), (b, a)}
    for r, pairs in inst["traverse"].items():
        if union - set(pairs):
            return True
    return False


def to_pddl(inst, name, seed, attempt):
    lines = [
        f"; Lunar rover problem {name} (size: {inst['size']}), generated by src/generate_problems.py",
        f"; seed={seed} attempt={attempt}",
        f"(define (problem {name})",
        "  (:domain lunar)",
        "  (:objects",
        "    " + " ".join(inst["rovers"]) + " - rover",
        "    " + " ".join(inst["wps"]) + " - waypoint",
        "    " + " ".join(inst["levels"]) + " - level)",
        "  (:init",
    ]
    L = lines.append
    for r in inst["rovers"]:
        L(f"    (at {r} {inst['base']}) (battery {r} {inst['levels'][-1]}) (store-empty {r})")
        for e in inst["equip"][r]:
            L(f"    (has-{e} {r})")
        for a, b in inst["traverse"][r]:
            L(f"    (can-traverse {r} {a} {b})")
    lv = inst["levels"]
    for i in range(1, len(lv)):
        L(f"    (next-lower {lv[i]} {lv[i - 1]})")
    for i in range(len(lv) - 1):
        L(f"    (below-full {lv[i]})")
    L(f"    (full-level {lv[-1]})")
    for w in inst["sunlit"]:
        L(f"    (sunlit {w})")
    for w in inst["comm"]:
        L(f"    (comm-visible {w})")
    for w in inst["sample_sites"]:
        L(f"    (sample-site {w})")
    for w in inst["image_targets"]:
        L(f"    (image-target {w})")
    L("  )")
    goals = [f"(sample-sent {w})" for w in inst["sample_goals"]] + [f"(image-sent {w})" for w in inst["image_goals"]]
    L("  (:goal (and " + " ".join(goals) + ")))")
    return "\n".join(lines) + "\n"


def generate_one(num, size, base_seed, domain_path, out_dir, optimal_timeout, max_attempts=400, log=print):
    name = f"p{num:02d}_{size}"
    for attempt in range(max_attempts):
        seed = base_seed * 1000 + num * 100000 + attempt  # unique, deterministic
        rng = random.Random(seed)
        inst = make_instance(size, rng)
        fails = check_structure(inst) or check_feasible(inst)
        if fails:
            continue
        pddl_path = out_dir / f"{name}.pddl"
        pddl_path.write_text(to_pddl(inst, name, base_seed, attempt))
        # (a) quick solvability test with a fast satisficing search.
        quick = run_search(domain_path, pddl_path, "gbf", "hff", 30)
        if quick["status"] != "solved":
            log(f"  {name} attempt {attempt}: rejected (not solvable / too slow for GBFS)")
            continue
        best = solve_best(domain_path, pddl_path, optimal_timeout=optimal_timeout)
        if best["status"] != "solved":
            log(f"  {name} attempt {attempt}: rejected (planner failed)")
            continue
        plan = best["plan"]
        # (b) the battery must be binding: reference plan has a recharge.
        if not any(step.startswith("(recharge ") for step in plan):
            log(f"  {name} attempt {attempt}: rejected (reference plan has no recharge)")
            continue
        meta = dict(
            name=name, size=size, seed_base=base_seed, attempt=attempt,
            rovers=len(inst["rovers"]), waypoints=len(inst["wps"]), goals=len(inst["sample_goals"]) + len(inst["image_goals"]),
            battery_levels=len(inst["levels"]),
            sunlit=inst["sunlit"], comm_visible=inst["comm"], equipment=inst["equip"],
            sample_goals=inst["sample_goals"], image_goals=inst["image_goals"],
            checks=dict(solvable=True, reference_plan_has_recharge=True, comm_minority=True,
                        sunlit_minority=True, terrain_matters=True,
                        not_all_rovers_have_all_instruments=(len(inst["rovers"]) > 1) or "n/a (1 rover)"),
            reference=dict(search=best["search"], heuristic=best["heuristic"], optimal=best["optimal"],
                           plan_length=len(plan), time_s=round(best["time_s"], 3),
                           recharges=sum(s.startswith("(recharge ") for s in plan),
                           optimal_search_timeout_s=best.get("optimal_timeout_s")),
        )
        return name, meta, plan
    raise RuntimeError(f"could not generate {name} in {max_attempts} attempts")


def main(argv=None):
    ensure_fixed_hash_seed()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--optimal-timeout", type=float, default=120)
    args = ap.parse_args(argv)
    domain = ROOT / "domain" / "lunar.pddl"
    out = ROOT / "problems" / "standard"
    refs = ROOT / "problems" / "reference_plans"
    out.mkdir(parents=True, exist_ok=True)
    refs.mkdir(parents=True, exist_ok=True)
    for num, size in PLAN_OF_PROBLEMS:
        name, meta, plan = generate_one(num, size, args.seed, domain, out, args.optimal_timeout)
        (out / f"{name}.json").write_text(json.dumps(meta, indent=2) + "\n")
        (refs / f"{name}.plan").write_text(plan_to_text(plan))
        r = meta["reference"]
        print(f"{name}: attempt {meta['attempt']}, plan length {r['plan_length']} "
              f"({r['search']}/{r['heuristic']}, optimal={r['optimal']}, {r['time_s']}s)")


if __name__ == "__main__":
    main()

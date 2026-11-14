"""Generate the six standard lunar-rover problems (2 per size), deterministically.

Usage:  python -m src.generate_problems [--seed 2026] [--optimal-timeout 120]

How it works (plain English):
  1. For each problem we draw a random lunar map with a seeded random number
     generator, so re-running gives byte-identical files.
  2. We CHECK the map against the experiment's requirements (see check_* below).
     If a check fails we draw a new map (next 'attempt') until all pass.
  3. We work out a tight CHARGE BUDGET (how many recharges each sunlit waypoint
     offers): start generous, solve, keep only the charges that plan used, then
     keep removing one charge at a time while the planner can still find a plan.
     The last plan found is proof that the final budget is enough.
  4. We ask pyperplan for a reference plan and save it with metadata.

History: the problem sets were made harder twice after a free pilot showed they
were too easy (see pilot/README.md). The earlier profiles ("v1", "v2": no charge
budget) are kept below for the record; their generator code is in git history.
"""
import argparse
import json
import random
import tempfile
from collections import Counter
from pathlib import Path

from .pddl_utils import ROOT, plan_to_text
from .planning import ensure_fixed_hash_seed, run_search, solve_best

# Difficulty profiles. For each size:
#   rovers, waypoints, goals, levels   the size of the problem (levels = battery levels, b0 = empty)
#   two_way, one_way                   chance that a terrain edge is two-way / one-way for a rover
#                                      (the rest is blocked for that rover)
#   extra_div                          extra edges on top of the spanning tree = max(2, waypoints // extra_div)
#   minor_cap                          at most this many sunlit waypoints, and this many comm-visible ones
#   min_recharges, min_len             the reference plan must contain at least this many recharges / steps
#   charge_slack                       spare charges added on top of the tight budget (0 = no spare at all)
PROFILES = {
    # v1 (pilot round 1) and v2 (pilot rounds 2-3): unlimited recharging. Record only.
    "v1": {
        "small": dict(rovers=1, waypoints=5, goals=2, levels=4, two_way=0.65, one_way=0.20, extra_div=2,
                      minor_cap=2, min_recharges=1, min_len=0),
        "medium": dict(rovers=2, waypoints=8, goals=4, levels=5, two_way=0.65, one_way=0.20, extra_div=2,
                       minor_cap=3, min_recharges=1, min_len=0),
        "large": dict(rovers=3, waypoints=12, goals=6, levels=5, two_way=0.65, one_way=0.20, extra_div=2,
                      minor_cap=3, min_recharges=1, min_len=0),
    },
    "v2": {
        "small": dict(rovers=1, waypoints=7, goals=4, levels=4, two_way=0.50, one_way=0.30, extra_div=3,
                      minor_cap=2, min_recharges=3, min_len=18),
        "medium": dict(rovers=2, waypoints=11, goals=7, levels=4, two_way=0.50, one_way=0.30, extra_div=3,
                       minor_cap=2, min_recharges=5, min_len=36),
        "large": dict(rovers=3, waypoints=14, goals=9, levels=4, two_way=0.50, one_way=0.30, extra_div=2,
                      minor_cap=4, min_recharges=7, min_len=50),
    },
    # v3 (current): same maps as v2 in spirit, plus a tight budget of recharges.
    "v3": {
        "small": dict(rovers=1, waypoints=7, goals=4, levels=4, two_way=0.50, one_way=0.30, extra_div=3,
                      minor_cap=2, min_recharges=3, min_len=16, charge_slack=0),
        "medium": dict(rovers=2, waypoints=11, goals=7, levels=4, two_way=0.50, one_way=0.30, extra_div=3,
                       minor_cap=2, min_recharges=5, min_len=32, charge_slack=0),
        "large": dict(rovers=3, waypoints=14, goals=9, levels=4, two_way=0.50, one_way=0.30, extra_div=2,
                      minor_cap=4, min_recharges=6, min_len=45, charge_slack=0),
    },
}
DEFAULT_PROFILE = "v3"
SIZES = PROFILES[DEFAULT_PROFILE]
GENEROUS_CHARGES = 8      # charges per sunlit waypoint in the first, unconstrained solve
SHRINK_TIMEOUT_S = 5      # time limit for each "can we do it with one charge less?" solve
# (problem number, size) in the order written to disk
PLAN_OF_PROBLEMS = [(1, "small"), (2, "small"), (3, "medium"), (4, "medium"), (5, "large"), (6, "large")]


def make_instance(size, rng, profile=DEFAULT_PROFILE):
    """Draw one random instance as a plain dict (no checking yet)."""
    cfg = PROFILES[profile][size]
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
    extra = max(2, n // cfg["extra_div"])
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
            if x < cfg["two_way"]:
                traverse[r] |= {(wps[a], wps[b]), (wps[b], wps[a])}
            elif x < cfg["two_way"] + cfg["one_way"]:
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
    sunlit = set(rng.sample(wps, rng.randint(1, max(1, min(k_minor, cfg["minor_cap"])))))
    comm = set(rng.sample(wps, rng.randint(1, max(1, min(k_minor, cfg["minor_cap"])))))

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


def charge_names(charges, sunlit):
    """{waypoint: n} -> list of (charge name, waypoint), numbered c1, c2, ... in waypoint order."""
    out = []
    for w in sunlit:
        for _ in range(charges.get(w, 0)):
            out.append((f"c{len(out) + 1}", w))
    return out


def recharges_per_waypoint(plan):
    """Count the recharge steps of a plan per waypoint: (recharge rover WAYPOINT level full charge)."""
    return Counter(step.strip("()").split()[2] for step in plan if step.startswith("(recharge "))


def to_pddl(inst, name, seed, attempt, charges=None):
    charges = inst.get("charges", {}) if charges is None else charges
    tokens = charge_names(charges, inst["sunlit"])
    lines = [
        f"; Lunar rover problem {name} (size: {inst['size']}), generated by src/generate_problems.py",
        f"; seed={seed} attempt={attempt}",
        f"(define (problem {name})",
        "  (:domain lunar)",
        "  (:objects",
        "    " + " ".join(inst["rovers"]) + " - rover",
        "    " + " ".join(inst["wps"]) + " - waypoint",
        "    " + " ".join(inst["levels"]) + " - level" + ("" if tokens else ")"),
        *(["    " + " ".join(c for c, _ in tokens) + " - charge)"] if tokens else []),
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
    for c, w in tokens:
        L(f"    (charge-at {c} {w})")
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


def generate_one(num, size, base_seed, domain_path, out_dir, optimal_timeout, max_attempts=300000, log=print,
                 profile=DEFAULT_PROFILE):
    name = f"p{num:02d}_{size}"
    cfg = PROFILES[profile][size]

    def hard_enough(plan):
        return len(plan) >= cfg["min_len"] and any(st.startswith("(recharge ") for st in plan)
    for attempt in range(max_attempts):
        seed = base_seed * 1000 + num * 100000 + attempt  # unique, deterministic
        rng = random.Random(seed)
        inst = make_instance(size, rng, profile)
        fails = check_structure(inst) or check_feasible(inst)
        if fails:
            continue
        pddl_path = out_dir / f"{name}.pddl"
        scratch = Path(tempfile.gettempdir()) / f"lunar_gen_{name}.pddl"

        def solve(charges, timeout):
            scratch.write_text(to_pddl(inst, name, base_seed, attempt, charges))
            res = run_search(domain_path, scratch, "gbf", "hff", timeout)
            return res["plan"] if res["status"] == "solved" else None

        # (a) quick solvability test with plenty of charges everywhere.
        plan = solve({w: GENEROUS_CHARGES for w in inst["sunlit"]}, 30)
        if plan is None:
            log(f"  {name} attempt {attempt}: rejected (not solvable / too slow for GBFS)")
            continue
        if not hard_enough(plan):   # cheap filter before the slower steps
            continue
        # (b) tight charge budget: keep only what the plan used, then try to remove one more
        #     charge at a time. Every accepted budget comes with a plan that proves it is enough.
        charges = dict(recharges_per_waypoint(plan))
        for _ in range(60):
            for w in sorted(charges, key=lambda x: (-charges[x], inst["wps"].index(x))):
                if charges[w] == 0:
                    continue
                smaller = solve(dict(charges, **{w: charges[w] - 1}), SHRINK_TIMEOUT_S)
                if smaller is not None:
                    plan, charges = smaller, dict(recharges_per_waypoint(smaller))
                    break
            else:
                break
        tight_total = sum(charges.values())
        for _ in range(cfg.get("charge_slack", 0)):
            w = rng.choice(inst["sunlit"])
            charges[w] = charges.get(w, 0) + 1
        # a sunlit waypoint without any charge would be useless: it is simply not sunlit
        inst["sunlit"] = [w for w in inst["sunlit"] if charges.get(w, 0) > 0]
        inst["charges"] = {w: charges[w] for w in inst["sunlit"]}
        if tight_total < cfg["min_recharges"] or check_structure(inst):
            log(f"  {name} attempt {attempt}: rejected (only {tight_total} recharges needed)")
            continue
        pddl_path.write_text(to_pddl(inst, name, base_seed, attempt))
        best = solve_best(domain_path, pddl_path, optimal_timeout=optimal_timeout)
        if best["status"] != "solved":
            log(f"  {name} attempt {attempt}: rejected (planner failed on the final file)")
            continue
        plan = best["plan"]
        if not hard_enough(plan):
            log(f"  {name} attempt {attempt}: rejected (reference plan too easy: {len(plan)} steps)")
            continue
        meta = dict(
            name=name, size=size, profile=profile, seed_base=base_seed, attempt=attempt,
            rovers=len(inst["rovers"]), waypoints=len(inst["wps"]), goals=len(inst["sample_goals"]) + len(inst["image_goals"]),
            battery_levels=len(inst["levels"]),
            sunlit=inst["sunlit"], charges=inst["charges"], charges_total=sum(inst["charges"].values()),
            charge_slack=cfg.get("charge_slack", 0), comm_visible=inst["comm"], equipment=inst["equip"],
            sample_goals=inst["sample_goals"], image_goals=inst["image_goals"],
            checks=dict(solvable=True, reference_plan_has_recharge=True,
                        charge_budget="tight for the greedy planner (not proven minimal)", comm_minority=True,
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
    ap.add_argument("--difficulty", choices=[DEFAULT_PROFILE], default=DEFAULT_PROFILE,
                    help="only the current profile can be generated; older ones are in git history")
    ap.add_argument("--only", help="comma-separated problem names to (re)generate, e.g. p03_medium")
    args = ap.parse_args(argv)
    only = set(args.only.split(",")) if args.only else None
    domain = ROOT / "domain" / "lunar.pddl"
    out = ROOT / "problems" / "standard"
    refs = ROOT / "problems" / "reference_plans"
    out.mkdir(parents=True, exist_ok=True)
    refs.mkdir(parents=True, exist_ok=True)
    for num, size in PLAN_OF_PROBLEMS:
        if only and f"p{num:02d}_{size}" not in only:
            continue
        name, meta, plan = generate_one(num, size, args.seed, domain, out, args.optimal_timeout,
                                        profile=args.difficulty)
        (out / f"{name}.json").write_text(json.dumps(meta, indent=2) + "\n")
        (refs / f"{name}.plan").write_text(plan_to_text(plan))
        r = meta["reference"]
        print(f"{name}: attempt {meta['attempt']}, plan length {r['plan_length']} "
              f"({r['search']}/{r['heuristic']}, optimal={r['optimal']}, {r['time_s']}s)")


if __name__ == "__main__":
    main()

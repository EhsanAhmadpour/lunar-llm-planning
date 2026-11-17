"""Write each lunar mission as a plain-English operator briefing (problems/nl/*.txt).

Usage:  python -m src.describe            # (re)writes problems/nl/p01_small.txt ... p06_large.txt
        python -m src.describe --check    # only checks that every briefing reads back to its PDDL problem

Why: Section 3 of the report describes systems (E et al. 2026, LLM-KGFP) in which an LLM receives a
mission in natural language and turns it into a structured model or a plan. The original experiment
always gave the model the finished PDDL problem. The briefings let us test the step before that:
starting from English, either
  * nl-translate: the LLM writes the PDDL problem and a classical planner (pyperplan) finds the plan
                  (the "LLM translates, solver plans" pattern of E et al.), or
  * nl-plan:      the LLM writes the plan itself (the "LLM plans, the rover checks" pattern of LLM-KGFP).

A briefing states every fact of the PDDL problem in prose, and nothing else. Object names (rover1, w3,
b2, c4) are kept, as an operator would use IDs, so plans and translations can be checked against the
original problem file without guessing names. `read_back` parses a briefing back into PDDL facts; the
tests use it to prove that no fact was lost or added (it is NOT given to any model).
"""
import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

from .pddl_utils import ROOT
from .validate import load_task

NUMBER_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight",
                9: "nine", 10: "ten", 11: "eleven", 12: "twelve", 13: "thirteen", 14: "fourteen"}


def _key(name):
    """Natural sort: w2 before w10."""
    m = re.match(r"([a-z]+)(\d+)$", name)
    return (m.group(1), int(m.group(2))) if m else (name, 0)


def _list(items):
    items = list(items)
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def facts_of(problem_path, domain_path=ROOT / "domain" / "lunar.pddl"):
    """(objects by type, set of init facts as tuples, set of goal facts as tuples)."""
    _, prob = load_task(domain_path, problem_path)
    objs = defaultdict(list)
    for o, t in prob.objects.items():
        objs[t.name].append(o)
    for t in objs:
        objs[t].sort(key=_key)
    init = {(a.name, *[s[0] for s in a.signature]) for a in prob.initial_state}
    goal = {(a.name, *[s[0] for s in a.signature]) for a in prob.goal}
    return dict(objs), init, goal


def describe(problem_path):
    objs, init, goal = facts_of(problem_path)
    name = Path(problem_path).stem
    rovers, wps, levels = objs["rover"], objs["waypoint"], objs["level"]
    by = defaultdict(list)
    for f in sorted(init, key=lambda f: [_key(x) for x in f[1:]]):
        by[f[0]].append(f[1:])

    # battery levels: order them with the next-lower chain
    full = by["full-level"][0][0]
    lower = {a: b for a, b in by["next-lower"]}
    chain = [full]
    while chain[-1] in lower:
        chain.append(lower[chain[-1]])
    chain.reverse()                      # empty ... full
    below = {l for (l,) in by["below-full"]}
    assert below == set(chain[:-1]) and set(chain) == set(levels), f"{name}: unusual battery levels"

    n_r = len(rovers)
    out = [f"MISSION BRIEFING {name}", ""]
    team = "one rover" if n_r == 1 else f"{NUMBER_WORDS[n_r]} rovers"
    out.append(f"Team: {team}, {_list(rovers)}. The map has {NUMBER_WORDS[len(wps)]} waypoints, "
               f"{wps[0]} to {wps[-1]}.")
    for r in rovers:
        tools = [t for t, p in (("a camera", "has-camera"), ("a soil sampler", "has-sampler")) if (p, r) in init]
        start = next(w for rr, w in by["at"] if rr == r)
        lvl = next(l for rr, l in by["battery"] if rr == r)
        store = "its sample store is empty" if ("store-empty", r) in init else "its sample store is full"
        carry = f"carries {_list(tools)}" if tools else "carries no instruments"
        out.append(f"- {r} {carry}. It starts at {start} with battery level {lvl}, and {store}.")
    out.append("")

    out.append("Driving. Routes are rover-specific, and only the routes listed here exist.")
    edges = defaultdict(set)
    for r, a, b in by["can-traverse"]:
        edges[r].add((a, b))
    for r in rovers:
        e = edges[r]
        two = sorted({tuple(sorted((a, b), key=_key)) for a, b in e if (b, a) in e},
                     key=lambda p: (_key(p[0]), _key(p[1])))
        one = sorted([(a, b) for a, b in e if (b, a) not in e], key=lambda p: (_key(p[0]), _key(p[1])))
        line = f"- {r} can drive both ways on {_list([f'{a}-{b}' for a, b in two])}" if two else f"- {r} has no two-way routes"
        if one:
            line += f"; and one way only on {_list([f'{a}->{b}' for a, b in one])} (not back)"
        out.append(line + ".")
    out.append("")

    out.append(f"Battery. Every rover's battery has {NUMBER_WORDS[len(chain)]} levels: "
               + ", ".join(f"{l}" + (" (empty)" if i == 0 else " (full)" if l == full else "")
                           for i, l in enumerate(chain))
               + ". Each drive and each sample collection uses one level, so a rover at "
               f"{chain[0]} can do neither. Taking or sending an image, and sending a sample, costs nothing.")
    sun = [w for (w,) in by["sunlit"]]
    charges = defaultdict(list)
    for c, w in by["charge-at"]:
        charges[w].append(c)
    for w in charges:
        charges[w].sort(key=_key)
    ch_parts = []
    for w in sorted(charges, key=_key):
        cs = charges[w]
        ch_parts.append(f"{w} has {NUMBER_WORDS[len(cs)]} ({_list(cs)})")
    out.append(f"Sunlight. Only {_list(sun)} {'is' if len(sun) == 1 else 'are'} sunlit. A rover at a sunlit "
               "waypoint can recharge straight to full, but only while that waypoint still has a recharge left, "
               "and each recharge uses one up for good. Available recharges: " + "; ".join(ch_parts)
               + (". There are none anywhere else." if True else "") )
    comm = [w for (w,) in by["comm-visible"]]
    out.append(f"Communication. Data can be sent to Earth only from {_list(comm)}.")
    samples = [w for (w,) in by["sample-site"]]
    images = [w for (w,) in by["image-target"]]
    out.append(f"Science sites. Soil samples can be collected at {_list(samples)}. Image targets are {_list(images)}. "
               "A rover can hold one sample at a time; sending it to Earth empties the store again.")
    out.append("")

    sg = sorted([f[1] for f in goal if f[0] == "sample-sent"], key=_key)
    ig = sorted([f[1] for f in goal if f[0] == "image-sent"], key=_key)
    assert len(sg) + len(ig) == len(goal), f"{name}: unexpected goal type"
    parts = []
    if sg:
        parts.append(f"the soil samples from {_list(sg)}")
    if ig:
        parts.append(f"images of {_list(ig)}")
    out.append(f"Mission goal. Get {' and '.join(parts)} sent to Earth. Any rover may do any part it is equipped for.")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------
# Read a briefing back into facts (tests only; proves the briefing is complete)
# --------------------------------------------------------------------------
def read_back(text):
    W = r"[a-z]+\d+"
    init, goal, objs = set(), set(), defaultdict(set)
    m = re.search(r"Team: [^,]+, (.+?)\. The map has \w+ waypoints, w(\d+) to w(\d+)\.", text)
    objs["rover"] = set(re.findall(W, m.group(1)))
    objs["waypoint"] = {f"w{i}" for i in range(int(m.group(2)), int(m.group(3)) + 1)}
    for r, rest in re.findall(rf"^- ({W}) carries (.+?)\. It starts", text, re.M):
        if "camera" in rest:
            init.add(("has-camera", r))
        if "sampler" in rest:
            init.add(("has-sampler", r))
    for r, w, l, st in re.findall(rf"^- ({W}) carries .+?\. It starts at ({W}) with battery level ({W}), and its sample store is (\w+)\.", text, re.M):
        init |= {("at", r, w), ("battery", r, l)}
        if st == "empty":
            init.add(("store-empty", r))
    for r, body in re.findall(rf"^- ({W}) (?:can drive both ways on|has no two-way routes)(.*)\.$", text, re.M):
        two, _, one = body.partition("; and one way only on")
        for a, b in re.findall(rf"({W})-({W})(?!>)", two):
            init |= {("can-traverse", r, a, b), ("can-traverse", r, b, a)}
        for a, b in re.findall(rf"({W})->({W})", one):
            init.add(("can-traverse", r, a, b))
    bat = re.search(r"Battery\. Every rover's battery has \w+ levels: (.+?)\. Each drive", text).group(1)
    chain = re.findall(W, bat)
    objs["level"] = set(chain)
    init |= {("next-lower", chain[i + 1], chain[i]) for i in range(len(chain) - 1)}
    init |= {("below-full", l) for l in chain[:-1]} | {("full-level", chain[-1])}
    sun = re.search(r"Sunlight\. Only (.+?) (?:is|are) sunlit", text).group(1)
    init |= {("sunlit", w) for w in re.findall(W, sun)}
    for w, cs in re.findall(rf"({W}) has \w+ \(([^)]*)\)", text):
        for c in re.findall(W, cs):
            init.add(("charge-at", c, w))
            objs["charge"].add(c)
    comm = re.search(r"Data can be sent to Earth only from (.+?)\.", text).group(1)
    init |= {("comm-visible", w) for w in re.findall(W, comm)}
    init |= {("sample-site", w) for w in re.findall(W, re.search(r"Soil samples can be collected at (.+?)\.", text).group(1))}
    init |= {("image-target", w) for w in re.findall(W, re.search(r"Image targets are (.+?)\.", text).group(1))}
    g = re.search(r"Mission goal\. Get (.+?) sent to Earth", text).group(1)
    sm = re.search(r"soil samples from (.+?)(?: and images|$)", g)
    im = re.search(r"images of (.+)$", g)
    if sm:
        goal |= {("sample-sent", w) for w in re.findall(W, sm.group(1))}
    if im:
        goal |= {("image-sent", w) for w in re.findall(W, im.group(1))}
    return {k: sorted(v, key=_key) for k, v in objs.items()}, init, goal


def nl_dir(root=ROOT):
    return Path(root) / "problems" / "nl"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    out_dir = nl_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    ok = True
    for p in sorted((ROOT / "problems" / "standard").glob("p*.pddl")):
        text = describe(p)
        target = out_dir / f"{p.stem}.txt"
        if a.check:
            text = target.read_text()
        else:
            target.write_text(text)
        same = read_back(text) == facts_of(p)
        ok &= same
        print(f"{p.stem}: {len(text.split())} words, reads back to the PDDL problem: {same}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

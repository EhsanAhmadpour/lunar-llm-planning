"""Mystery mapping and problem-generator tests."""
import json
import random
import re

import pytest

from conftest import ROOT
from src import generate_problems as G
from src.obfuscate import load_mapping, translate_plan, rewrite
from src.pddl_utils import read_text, strip_comments
from src.run_llm import list_instances
from src.validate import validate, load_task


def test_mystery_roundtrip_validates_on_standard(mapping):
    for std in sorted((ROOT / "problems" / "standard").glob("p*.pddl")):
        myst_plan = (ROOT / "problems" / "reference_plans" / f"{std.stem}.mystery.plan").read_text()
        back = translate_plan(myst_plan, mapping, to="standard")
        assert back == (ROOT / "problems" / "reference_plans" / f"{std.stem}.plan").read_text()
        assert validate(ROOT / "domain" / "lunar.pddl", std, back)["valid"]


def test_no_standard_name_survives_in_mystery_files(mapping):
    names = set()
    for sec in ("types", "predicates", "actions", "objects"):
        names |= set(mapping[sec])
    files = [ROOT / "domain" / "mystery.pddl", *(ROOT / "problems" / "mystery").glob("*.pddl")]
    for f in files:
        words = set(re.findall(r"[a-z][a-z0-9_\-]*", f.read_text()))
        assert not (words & names), (f, words & names)
        assert ";" not in f.read_text()


def test_mapping_is_injective_and_pronounceable(mapping):
    vals = [v for sec in mapping.values() for v in sec.values()]
    assert len(vals) == len(set(vals))
    assert all(re.fullmatch(r"[a-z]+", v) for v in vals)


def test_same_structure(mapping):
    d1, p1 = load_task(ROOT / "domain" / "lunar.pddl", ROOT / "problems" / "standard" / "p03_medium.pddl")
    d2, p2 = load_task(ROOT / "domain" / "mystery.pddl", ROOT / "problems" / "mystery" / "p03_medium.pddl")
    assert len(d1.actions) == len(d2.actions) and len(d1.predicates) == len(d2.predicates)
    for a, m in mapping["actions"].items():
        sig1 = [t for _, ts in d1.actions[a].signature for t in [mapping["types"][ts[0].name]]]
        sig2 = [ts[0].name for _, ts in d2.actions[m].signature]
        assert sig1 == sig2
        pre1 = [(mapping["predicates"][x.name], [s[0] for s in x.signature]) for x in d1.actions[a].precondition]
        pre2 = [(x.name, [s[0] for s in x.signature]) for x in d2.actions[m].precondition]
        # same predicates in the same order with the same parameter positions
        idx1 = [(n, [[v for v, _ in d1.actions[a].signature].index(v) for v in vs]) for (n, vs) in
                [(mapping["predicates"][x.name], [s[0] for s in x.signature]) for x in d1.actions[a].precondition]]
        idx2 = [(n, [[v for v, _ in d2.actions[m].signature].index(v) for v in vs]) for (n, vs) in pre2]
        assert idx1 == idx2
    assert len(p1.initial_state) == len(p2.initial_state)
    assert len(p1.goal) == len(p2.goal)


def test_translate_unknown_tokens_untouched(mapping):
    assert translate_plan("(foo bar)", mapping, "standard") == "(foo bar)"


# ---------------------------------------------------------------- generator
def test_generator_is_deterministic(tmp_path):
    a = G.to_pddl(G.make_instance("medium", random.Random(5)), "x", 1, 0)
    b = G.to_pddl(G.make_instance("medium", random.Random(5)), "x", 1, 0)
    assert a == b


@pytest.mark.parametrize("meta_file", sorted((ROOT / "problems" / "standard").glob("*.json")), ids=lambda p: p.stem)
def test_generated_problem_requirements(meta_file):
    meta = json.loads(meta_file.read_text())
    size = meta["size"]
    cfg = G.PROFILES[meta.get("profile", "v1")][size]
    assert (meta["rovers"], meta["waypoints"], meta["goals"], meta["battery_levels"]) == (
        cfg["rovers"], cfg["waypoints"], cfg["goals"], cfg["levels"])
    n = meta["waypoints"]
    assert 0 < len(meta["comm_visible"]) and 2 * len(meta["comm_visible"]) < n   # (c)
    assert 0 < len(meta["sunlit"]) and 2 * len(meta["sunlit"]) < n               # (c)
    assert meta["reference"]["recharges"] >= max(1, cfg["min_recharges"])         # (b)
    assert meta["reference"]["plan_length"] >= cfg["min_len"]
    ref = read_text(ROOT / "problems" / "reference_plans" / f"{meta['name']}.plan")
    assert ref.count("(recharge ") == meta["reference"]["recharges"]
    if cfg["rovers"] > 1:                                                         # (e)
        assert any(len(e) < 2 for e in meta["equipment"].values())
        assert set().union(*map(set, meta["equipment"].values())) == {"sampler", "camera"}
    # (d) terrain: parse can-traverse facts and check it is not "everything traversable"
    _, p = load_task(ROOT / "domain" / "lunar.pddl", ROOT / "problems" / "standard" / f"{meta['name']}.pddl")
    ct = {(x.signature[0][0], x.signature[1][0], x.signature[2][0]) for x in p.initial_state if x.name == "can-traverse"}
    rovers = [o for o, t in p.objects.items() if t.name == "rover"]
    wps = [o for o, t in p.objects.items() if t.name == "waypoint"]
    union = {(a, b) for _, a, b in ct} | {(b, a) for _, a, b in ct}
    assert any(union - {(a, b) for r2, a, b in ct if r2 == r} for r in rovers)
    if meta["reference"]["optimal"] is False:
        assert meta["reference"]["search"] == "gbf"


def test_all_twelve_instances_present():
    assert len(list_instances()) == 12

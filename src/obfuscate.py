"""Build the 'mystery' variant: same logic, meaningless names.

Usage:  python -m src.obfuscate

Every action, predicate, type, object and variable name (and the domain and
problem names) is replaced by a made-up pronounceable word such as 'zorp'.
The replacement is deterministic (fixed seed) and consistent across the domain
and all problems. The mapping is saved to domain/mystery_mapping.json so we can
(1) classify validator errors by their real meaning and (2) translate plans
back to standard names.
"""
import json
import random
import re
from pathlib import Path

from pyperplan.pddl.parser import Parser

from .pddl_utils import ROOT, plan_to_text, read_text, strip_comments

PDDL_KEYWORDS = {"define", "domain", "problem", "and", "not", "object", "either"}
CONSONANTS = "bdfgklmnprstvz"
VOWELS = "aeiou"
MAPPING_SECTIONS = ["types", "predicates", "actions", "objects", "variables", "domain", "problems"]
IDENT = re.compile(r"(:)?(\?)?([A-Za-z][A-Za-z0-9_\-]*)")


class TokenFactory:
    """Deterministic supply of unique pronounceable words."""

    def __init__(self, seed, forbidden):
        self.rng = random.Random(seed)
        self.used = set(forbidden) | PDDL_KEYWORDS

    def new(self):
        while True:
            n_syll = self.rng.choice([2, 2, 3])
            word = "".join(self.rng.choice(CONSONANTS) + self.rng.choice(VOWELS) for _ in range(n_syll))
            if self.rng.random() < 0.5:
                word += self.rng.choice(CONSONANTS)
            if word not in self.used:
                self.used.add(word)
                return word


def collect_names(domain_path, problem_paths):
    """Find every identifier that must be renamed, grouped by role."""
    dom_text = strip_comments(read_text(domain_path))
    pars = Parser(str(domain_path))
    dom = pars.parse_domain()
    names = {
        "types": sorted(t for t in dom.types if t != "object"),
        "predicates": sorted(dom.predicates),
        "actions": sorted(dom.actions),
        "domain": [dom.name],
        "objects": [],
        "problems": [],
    }
    objs = set()
    problems = []
    variables = set(re.findall(r"\?([a-z][\w\-]*)", dom_text.lower()))
    for pp in problem_paths:
        text = strip_comments(read_text(pp))
        problems.append(re.search(r"\(\s*problem\s+(\S+?)\s*\)", text, re.I).group(1).lower())
        p = Parser(str(domain_path), str(pp))
        prob = p.parse_problem(p.parse_domain())
        objs |= set(prob.objects)
        variables |= set(re.findall(r"\?([a-z][\w\-]*)", text.lower()))
    names["objects"] = sorted(objs)
    names["problems"] = problems
    names["variables"] = sorted(variables)
    return names


def build_mapping(names, seed=7):
    all_std = {x for v in names.values() for x in v}
    factory = TokenFactory(seed, all_std)
    mapping = {}
    for section in MAPPING_SECTIONS:
        mapping[section] = {}
        for std in names[section]:
            mapping[section][std] = factory.new()
    return mapping


def rewrite(text, mapping):
    """Rename every identifier in (comment-free) PDDL text using the mapping."""
    flat = {}
    for section in MAPPING_SECTIONS:
        for k, v in mapping[section].items():
            if k in flat and section != "variables":
                raise ValueError(f"name {k!r} used in two roles")
            flat[(section == "variables", k)] = v
    def sub(m):
        colon, qmark, word = m.group(1), m.group(2), m.group(3)
        if colon:                     # PDDL keyword such as :types or :strips
            return m.group(0)
        w = word.lower()
        if qmark:
            return "?" + flat[(True, w)]
        if w in PDDL_KEYWORDS:
            return w
        if (False, w) not in flat:
            raise KeyError(f"identifier {word!r} has no mystery name")
        return flat[(False, w)]
    return IDENT.sub(sub, text.lower())


def load_mapping(path=None):
    path = Path(path) if path else ROOT / "domain" / "mystery_mapping.json"
    return json.loads(path.read_text())


def translate_plan(plan_text, mapping, to="standard"):
    """Translate plan text between name spaces ('standard' or 'mystery').

    Only action names and object names are translated; layout is preserved.
    """
    table = {}
    for section in ("actions", "objects"):
        for std, myst in mapping[section].items():
            if to == "standard":
                table[myst] = std
            else:
                table[std] = myst
    def sub(m):
        return table.get(m.group(0).lower(), m.group(0))
    return re.sub(r"[A-Za-z][A-Za-z0-9_\-]*", sub, plan_text)


def main():
    dom = ROOT / "domain" / "lunar.pddl"
    std_dir = ROOT / "problems" / "standard"
    my_dir = ROOT / "problems" / "mystery"
    refs = ROOT / "problems" / "reference_plans"
    my_dir.mkdir(parents=True, exist_ok=True)
    problems = sorted(std_dir.glob("p*.pddl"))
    names = collect_names(dom, problems)
    mapping = build_mapping(names)
    (ROOT / "domain" / "mystery_mapping.json").write_text(json.dumps(mapping, indent=2) + "\n")
    (ROOT / "domain" / "mystery.pddl").write_text(rewrite(strip_comments(read_text(dom)), mapping))
    for p in problems:
        (my_dir / p.name).write_text(rewrite(strip_comments(read_text(p)), mapping))
        ref = refs / f"{p.stem}.plan"
        if ref.exists():
            out = translate_plan(read_text(ref), mapping, to="mystery")
            (refs / f"{p.stem}.mystery.plan").write_text(out)
    print(f"wrote domain/mystery.pddl, domain/mystery_mapping.json and {len(problems)} problems in {my_dir}")


if __name__ == "__main__":
    main()

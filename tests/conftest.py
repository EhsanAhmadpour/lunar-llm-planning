import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.obfuscate import load_mapping, rewrite  # noqa: E402
from src.pddl_utils import read_text, strip_comments  # noqa: E402

TINY_PLAN = """(move rover1 w1 w2 b3 b2)
(move rover1 w2 w3 b2 b1)
(take-image rover1 w3)
(collect-sample rover1 w3 b1 b0)
(recharge rover1 w3 b0 b3 c2)
(move rover1 w3 w2 b3 b2)
(send-sample rover1 w3 w2)
(send-image rover1 w3 w2)
"""


@pytest.fixture(scope="session")
def mapping():
    return load_mapping()


@pytest.fixture(scope="session")
def tiny(tmp_path_factory, mapping):
    """(domain, problem, plan_translator) for the tiny problem in standard and mystery variants."""
    from src.obfuscate import translate_plan
    d = tmp_path_factory.mktemp("tiny")
    std_prob = ROOT / "tests" / "fixtures" / "tiny.pddl"
    # Mystery copy: the validator auto-detects the mapping only for domain/mystery.pddl,
    # so we use the real mystery domain and a rewritten tiny problem.
    (d / "tiny_mystery.pddl").write_text(rewrite(strip_comments(read_text(std_prob)).replace("(problem tiny)", "(problem p01_small)"), mapping))
    return {
        "standard": (ROOT / "domain" / "lunar.pddl", std_prob, lambda t: t),
        "mystery": (ROOT / "domain" / "mystery.pddl", d / "tiny_mystery.pddl",
                    lambda t: translate_plan(t, mapping, to="mystery")),
    }


def all_instances():
    from src.run_llm import list_instances
    return list_instances()

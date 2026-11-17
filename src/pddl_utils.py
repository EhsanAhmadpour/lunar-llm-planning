"""Small helpers for reading PDDL files and plans. No planning logic here."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def strip_comments(text: str) -> str:
    """Remove ';' comments and blank-line runs from PDDL text.

    We strip comments before showing PDDL to an LLM so the only difference
    between the 'standard' and 'mystery' variants is the names.
    """
    lines = []
    for line in text.splitlines():
        line = line.split(";", 1)[0].rstrip()
        lines.append(line)
    out = "\n".join(lines)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip() + "\n"


def read_text(path) -> str:
    return Path(path).read_text(encoding="utf-8")


def plan_to_text(plan_lines) -> str:
    """Join a list like ['(move r1 w1 w2 b3 b2)', ...] into plan-file text."""
    return "\n".join(plan_lines) + "\n"


# Which domain file belongs to which variant (folder under problems/).
#   standard, mystery : our lunar domain (generated problems)
#   rovers            : the classic IPC "Rovers" domain, tasks taken unchanged from the
#                       IPC 2023 Learning Track test set (see problems/rovers/README.md)
#   nl-plan,          : the same six lunar missions as "standard", but the model gets a plain-English
#   nl-translate        briefing (problems/nl/*.txt) instead of the PDDL problem. nl-plan: the model
#                       writes the plan; nl-translate: the model writes the PDDL problem and pyperplan
#                       plans (src/translate.py). Both are checked against problems/standard/*.pddl.
DOMAIN_FILES = {"standard": "lunar.pddl", "mystery": "mystery.pddl", "rovers": "rovers.pddl",
                "nl-plan": "lunar.pddl", "nl-translate": "lunar.pddl"}
NL_VARIANTS = ("nl-plan", "nl-translate")


def domain_for(variant, root=ROOT):
    return Path(root) / "domain" / DOMAIN_FILES[variant]


def problem_for(variant, name, root=ROOT):
    """The PDDL problem a reply is checked against (for nl-* the hand-written standard problem)."""
    folder = "standard" if variant in NL_VARIANTS else variant
    return Path(root) / "problems" / folder / f"{name}.pddl"


def briefing_for(name, root=ROOT):
    return Path(root) / "problems" / "nl" / f"{name}.txt"

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

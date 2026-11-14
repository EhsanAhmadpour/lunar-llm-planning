import sys, json
from pathlib import Path
sys.path.insert(0, "/home/claude/lunar-llm-planning")
from src.validate import validate_text, default_mapping_for
root = Path("/home/claude/lunar-llm-planning")
rd = root / "pilot" / sys.argv[1] / "raw"
for f in sorted(rd.rglob("rep*_round*.json")):
    r = json.loads(f.read_text())
    dom = root / "domain" / ("lunar.pddl" if r["variant"] == "standard" else "mystery.pddl")
    v = validate_text(dom, root / "problems" / r["variant"] / (r["problem"] + ".pddl"), r["response_text"], default_mapping_for(dom))
    print(f'{r["model_label"]:13}{r["variant"]:9}{r["problem"]:12} round{r["round"]} len={v["plan_length"]:<3} ', "VALID" if v["valid"] else f'{v["error_category"]}: {v["error_message"]}')

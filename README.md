# Lunar rover planning: how reliable are LLMs, and how much does a verifier help?

This repository contains everything needed to run (and re-run) a small experiment for a university course.

**The question.** If we give a large language model (LLM) a planning problem written in PDDL (a standard
planning language) and ask for a plan, how often is the plan actually correct? And if we add an
*external plan verifier* that tells the model exactly which step is wrong, how much better does it get?

The test world is a small "lunar rover" domain: rovers drive between waypoints, have a limited battery,
can only recharge in sunlight, and can only send data to Earth from places that see Earth.

You do **not** need to be a planning expert. Every script has a plain-English description at the top.

## Research questions

* **RQ1 - direct planning.** How often is the *first* plan an LLM writes valid? When it is not, what kind of
  mistake is it (broken syntax, battery, terrain, communication, equipment, or other state errors, or a plan
  that runs but does not reach the goal)? Is the model worse when all names are replaced by meaningless words
  (the *mystery* variant), which tests reasoning versus recognising familiar words?
* **RQ2 - gain from a verifier.** If the model gets the verifier's error message and may retry (up to 3
  times), how many more problems end up solved, and how many retries does it need?

## The domain (domain/lunar.pddl)

Plain STRIPS with types: no numbers, no negative preconditions. Battery is a chain of discrete *levels*
(`b0` = empty ... top level = full) linked by `next-lower`.

| Action | What it does | Must hold before |
|---|---|---|
| `move r from to l1 l2` | drive, costs one battery level | rover at `from`; `can-traverse r from to` (terrain); battery is `l1`; `l2` is one level below `l1` (so impossible at the lowest level) |
| `recharge r w l lfull c` | jump to full battery, **uses up charge `c`** | rover at `w`; `w` is sunlit; charge `c` is at `w` (`charge-at c w`); battery is `l`; `l` is below full; `lfull` is the full level |
| `collect-sample r w l1 l2` | pick up a soil sample, costs one battery level | rover at `w`; `w` is a sample site; rover has a sampler; its sample store is empty; battery `l1`, `l2` one below |
| `take-image r w` | photograph a target | rover at `w`; `w` is an image target; rover has a camera |
| `send-sample r site w` | transmit a held sample to Earth | rover holds the sample from `site`; rover at `w`; `w` is comm-visible |
| `send-image r target w` | transmit a held image to Earth | rover holds the image of `target`; rover at `w`; `w` is comm-visible |

Goals are lists of `(sample-sent site)` and `(image-sent target)`.
Rovers never interact with each other. In the problems only a minority of waypoints are sunlit and a
minority can see Earth, some edges are blocked or one-way for some rovers, and in the medium and large
problems not every rover has both instruments. Every reference plan needs at least one recharge, so the
battery really matters.

Recharging is a limited resource: each sunlit waypoint offers a fixed number of `charge` objects and every
recharge deletes one, so the whole mission has a tight budget of recharges.

Instances (generated, seeded): 2 each of **small** (1 rover, 7 waypoints, 4 goals), **medium** (2 rovers,
11 waypoints, 7 goals) and **large** (3 rovers, 14 waypoints, 9 goals), all with 4 battery levels. Reference
plans have 19 to 59 steps. This is the third, hardest version of the set; `pilot/README.md` tells why.
The **mystery** variant renames every action, predicate, type, object and variable to made-up words
(for example `move` becomes a word like `zorp`) but keeps the logic identical.

**Second benchmark: IPC Rovers.** Because our own missions proved easy for current models, the experiment
also runs six tasks of the classic *Rovers* domain, copied unchanged from the International Planning
Competition 2023 Learning Track (`domain/rovers.pddl`, `problems/rovers/`, see the README there). They are
longer (reference plans of 33 to 127 steps) and are used in published LLM-planning papers. It is a third
"variant" called `rovers`; it has no battery and no mystery copy.

**Thinking on or off.** `config/models.yaml` runs the same cheap models twice: without thinking (`-direct`,
the model must write the plan immediately) and with thinking (`-think`).

## Folder map

```
domain/            lunar.pddl (standard), mystery.pddl + mystery_mapping.json (renamed copy and the name table)
problems/standard/ p01_small.pddl ... p06_large.pddl (+ .json metadata: seed, checks, reference-plan info)
problems/mystery/  the same six problems with mystery names
problems/rovers/   six IPC Rovers tasks (r01_small ... r06_large) + metadata
pilot/             the free pilot rounds that shaped the problem sets (read pilot/README.md)
problems/reference_plans/  plans found by pyperplan (standard and *.mystery.plan)
prompts/           direct.txt (first prompt), feedback.txt (verifier message)
config/            models.yaml (which models, prices), experiment.yaml (budget, rounds, ...)
src/               generate_problems.py, obfuscate.py, validate.py, run_baseline.py, run_llm.py, manual.py, analyze.py
tests/             pytest tests (no network needed)
results/           raw/ (every request+response), spend.json, baseline.csv, summary.csv, tables.md
figures/           validity.png
runs/request.yaml  asks the GitHub workflow to run something (see below)
```

## Run it locally in 5 commands

```bash
pip install -r requirements.txt                 # 1. install (Python 3.10+)
python -m src.generate_problems && python -m src.obfuscate   # 2. (optional) regenerate problems; they are already committed
python -m src.run_baseline                      # 3. pyperplan solves all 18 instances; our validator must accept them all
python -m src.run_llm --estimate                # 4. see the cost table (no API calls); then: --smoke, then the full run
python -m src.analyze                           # 5. tables (results/tables.md, summary.csv) and figures/validity.png
```

Before the real run, put your key in the environment or in a `.env` file (it is git-ignored):
`OPENROUTER_API_KEY=...`, edit `config/models.yaml` (see below), then:

```bash
python -m src.run_llm --smoke      # one cheap call: checks key and model id
python -m src.run_llm              # the full experiment (resumable)
pytest                             # all tests
```

Validate any plan yourself: `python -m src.validate domain/lunar.pddl problems/standard/p01_small.pddl myplan.txt`
(for mystery files the name table is picked up automatically).

## How a run works

For each *model x problem x variant x repeat*: round 0 sends the PDDL and asks for a plan in a ```` ```plan ```` block.
If our validator says it is invalid, rounds 1-3 send the validator's sentence (for example
`Step 4 (move rover1 w2 w5 b1 b0): precondition (can-traverse rover1 w2 w5) is false`) in the same conversation and ask
for a corrected full plan. It stops at the first valid plan. So **one run gives both numbers**: "direct" = valid at
round 0, "with verifier" = valid at any round up to 3.

## The budget cap

* `config/experiment.yaml: budget_usd` (default 9.0) is a hard cap.
* `results/spend.json` is a running total. Cost is taken from OpenRouter's usage accounting when present, otherwise
  computed from the prices in `models.yaml`.
* Before **every** call the runner estimates its worst-case cost (prompt size / 4 tokens times the input price, plus
  `max_tokens` times the output price). If total + worst case would exceed the budget it stops cleanly (exit code 2).
* Every response is saved in `results/raw/`; re-running skips saved calls, so you never pay twice. To continue after a
  budget stop, raise the budget and run the same command again.
* `python -m src.run_llm --estimate` prints worst-case and expected cost per model first.

## Choosing models, rerunning one model

Edit `config/models.yaml`. **The model ids and prices in it are placeholders**: check each id and price on
https://openrouter.ai/models first. Then, for example:

```bash
python -m src.run_llm --models cheap-a                          # one model (label from models.yaml)
python -m src.run_llm --models cheap-a --variants standard --problems p01_small,p03_medium
```

To rerun a model from scratch delete `results/raw/<label>/` (and fix `results/spend.json` or delete it, it is rebuilt
from the saved files). Models that are not on OpenRouter can be run by hand:
set `provider: manual` in `models.yaml`, then `python -m src.manual export --model <label>` writes prompts to
`results/manual/<label>/...`; paste each into a chat tool, save the reply as `response.txt` next to it (feedback prompts
go in the *same* chat), and run `python -m src.manual import --model <label>`; repeat `export` until nothing is pending.

## Running on GitHub Actions

Editing and pushing `runs/request.yaml` (`mode: estimate | smoke | full`, optional `models`) starts
`.github/workflows/run.yml`, which runs the baseline, the runner and the analysis, then commits `results/` and
`figures/`. Add your key as the repository secret `OPENROUTER_API_KEY`. The key is never printed or saved.

## Limitations (please mention them in the report)

* **Own validator, not VAL.** `src/validate.py` is a small STRIPS simulator built on pyperplan's PDDL parser. It is
  tested against pyperplan's own plans but has not been cross-checked with VAL. Type mismatches (e.g. a waypoint where
  a rover is expected) are reported as `syntax` errors.
* **Small problem set** (6 problems, 2 per size, 12 with mystery). Percentages from a few dozen runs have wide error bars.
* **Temperature 0 is not deterministic** for many hosted models, and some models ignore it; repeats help only a little.
  Reasoning models may need a larger `max_tokens`.
* **Simplifications:** the communication windows are static (a waypoint always sees Earth or never does), battery is a few
  discrete levels, sunlight is static, there is no time. Rovers do not interact.
* **Reference plans** are optimal (A* with LM-cut) only where it finished within 120 s; otherwise they come from greedy
  search (GBFS + hFF) and are not necessarily shortest (the metadata says which). The "length ratio" in the tables is
  relative to these plans. Greedy search results can differ between Python hash seeds, so the scripts pin
  `PYTHONHASHSEED=0` and the committed plans are the reference.
* The "terrain matters" and "battery is binding" requirements are checked structurally (blocked/one-way edges exist;
  the reference plan contains a recharge), not proven for each individual goal.
* Prompt wording influences results; the prompts are in `prompts/` and are the same for all models.

# Free pilot (no API cost)

Before spending money, the missions were tried on two Claude models run as sub-agents of a Claude
session: `pilot-weak` (Claude Haiku) and `pilot-strong` (Claude Opus). Each got exactly the prompt in
`prompts/direct.txt` and was told to answer from its own reasoning with no tools. Note that these
sub-agents can think before answering, so they are stronger than a plain API call that must output
only the plan.

Each round folder has `raw/` (every prompt and answer, same format as `results/raw/`), `manual/`
(the prompt/response text files) and `problems/` (the exact problem files used).

| round | problem set | names | result (valid on the first try) |
|---|---|---|---|
| round1 | v1: 5/8/12 waypoints, 2/4/6 goals | standard | 5 of 6 (weak model failed medium, fixed after 1 feedback) -> too easy |
| round2 | v2: 7/11/14 waypoints, 4/7/9 goals, 4 battery levels, sparser terrain | standard | 6 of 6 -> still too easy |
| round3 | v2 | mystery | 6 of 6 -> still too easy |

## Notes from the pause on 14 Nov 2026 (all four points below are done, see the update at the end)

Next hardening step, **v3**: recharging becomes a limited resource. Every recharge uses up one
`charge` object that sits at a sunlit waypoint (`charge-at`), and the generator gives each problem a
tight charge budget. Done so far: domain, validator, tests, generator code; `p01_small`, `p02_small`
and `p04_medium` are regenerated for v3.

**Not finished - the repository is inconsistent until this is done:**
1. regenerate `p03_medium`, `p05_large`, `p06_large` (`python -m src.generate_problems --only <name>`;
   slow, 10+ minutes each) - the committed files are still v2 and do not match the new domain;
2. `python -m src.obfuscate`, `python -m src.run_baseline`, `pytest`;
3. pilot round 4 on the v3 problems; update the main README for the v3 domain;
4. then: real OpenRouter models and prices in `config/models.yaml`, cost estimate, approval, runs.

`domain_v1_v2/` holds the domain files used in rounds 1-3. `tools/` holds the two helper scripts used
to pass prompts to the sub-agents and check the answers.

## Update: round 4 and the decision to stop hardening

| round | problem set | names | result (valid on the first try) |
|---|---|---|---|
| round4 | v3: v2-sized maps plus a tight budget of recharges (p01_small, p04_medium, p05_large) | standard | 6 of 6 |

All six v3 problems are now generated and the repository is consistent again (baseline valid on all
12 instances, tests pass). Conclusion of the pilot: Claude models that may think before answering
solve every mission we could generate (23 of 24 first-try plans valid over four rounds), so making
the maps harder is not what separates success from failure. The paid run therefore compares the
same cheap models WITHOUT thinking ("-direct") and WITH thinking ("-think"); see config/models.yaml.

## Round 5: IPC Rovers (second benchmark)

To get longer, externally recognised tasks we added six IPC Rovers tasks (problems/rovers/). Pilot on the
two largest: `pilot-weak` on r05_large (reference 101 steps) and r06_large (127 steps), `pilot-strong` on
r06_large. Result: 3 of 3 valid on the first try (plans of 97 to 120 steps).

Overall pilot: 26 of 27 first-try plans valid. Thinking Claude models are at the ceiling on everything
tried, so the paid run focuses on thinking OFF versus ON and on other model families.

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

## Status when the work was paused (14 Nov 2026) - WORK IN PROGRESS

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

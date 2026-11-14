# IPC Rovers tasks (second benchmark)

`domain/rovers.pddl` and the six tasks here are copied UNCHANGED from the test set of the
International Planning Competition 2023 Learning Track (https://github.com/ipc2023-learning/benchmarks,
folder `rovers/`; the domain comes from https://github.com/AI-Planning/pddl-generators). Only the file
names differ; each `.json` file says which original file a task is (`source`).

Why: our own lunar missions turned out to be easy for current models (see pilot/README.md). Rovers is
the standard planetary-rover benchmark used in LLM-planning papers, so results can be compared with the
literature, and its tasks get much longer. Sizes here are by reference-plan length:
small ~33 steps, medium 49-70, large 101-127. There is no battery in this domain and no mystery variant.

Run only these: `python -m src.run_llm --variants rovers`.

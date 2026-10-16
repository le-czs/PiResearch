# FrontierAgent × DeepResearch Bench II

The integration keeps hidden grading data out of the agent workspace, exports
FrontierAgent answers as the official `idx-N.md` layout, and runs the repository's
DeepResearch Bench II evaluator plus score aggregation.

## Validate the setup

FrontierAgent reads DeepSeek settings from the existing
`nvidia-aiq/deploy/.env`. Put the search credential in `FrontierAgent/.env`:

```bash
SERPER_API_KEY=your-key
```

```bash
./run_frontier_benchmark.sh --check
```

## One-question smoke run

```bash
RUN_NAME=frontier_smoke LIMIT=1 ./run_frontier_benchmark.sh --allow-partial
```

## Full resumable run

```bash
RUN_NAME=frontier_drb2 ./run_frontier_benchmark.sh
```

Run the same command again after an interruption. Generation skips trials with
valid `result.json` files, and judging skips `(model, idx)` pairs already present
in `judge_results.jsonl`.

The default is `agent_team_report` with profile `drb2_deepseek_v2`: plan,
two research passes, then an independent report. The fast single-agent path
is still available:

```bash
RUN_NAME=frontier_fast PIPELINE=stateful-react-agent PROFILE=drb2_deepseek_v2 \
  ./run_frontier_benchmark.sh
```

Start with generation concurrency 1. FrontierAgent uses Serper for search and a
team run can consume many searches per question.

For a plumbing-only smoke test that does not use a search key, set `WEB=0`.
That score is only a connectivity check and is not benchmark-comparable.

Useful modes:

```bash
./run_frontier_benchmark.sh --prepare-only
./run_frontier_benchmark.sh --generate-only
./run_frontier_benchmark.sh --score-only --allow-partial
```

Outputs are written below `runs/$RUN_NAME/`:

- `generation/trials/<idx>/result.json`: resumable FrontierAgent result
- `reports/<model>/idx-<idx>.md`: evaluator input
- `judge_results.jsonl`: resumable rubric verdicts
- `scores_*.csv`: dimension-level results
- `summary.txt`: headline scores

The bundled DeepSeek judge is sufficient for development feedback. Its number is
not directly comparable with an official leaderboard score produced by the
benchmark's required judge model.

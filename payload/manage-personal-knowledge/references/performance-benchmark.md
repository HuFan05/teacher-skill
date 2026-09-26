# Search Performance Benchmark

## What The Two Layers Measure

Use the local layer to measure deterministic programs without a model in the loop. It runs the configured status check, Vault retrieval, general PDF search, and paper-location PDF search as separate read-only child processes. Each event records wall time from a monotonic clock, exit status, output bytes, result count, and a redacted command. The report gives the pipeline wall time, the inclusive sum of all child processes, and per-step median and P90.

Use the model layer to launch a fresh non-interactive host-agent process around the complete search prompt. Supply that process as `--agent-command`, a JSON array of argv tokens containing the `{model}` placeholder; the process reads the prompt on standard input and writes one JSON event per line on standard output, using the `turn.started`, `item.started`, `item.completed`, `turn.completed`, `turn.failed`, and `error` event vocabulary. A host whose CLI emits another event format needs a receiver-local adapter that re-emits these events. It records process startup, turn duration, tool intervals, first completed agent message, shutdown, retries, and token usage when the CLI exposes it. The outer process measures total response time, so the model cannot omit or revise its own latency.

`model_orchestration_residual_ms` is the turn wall time minus the union of tool intervals. It includes service queueing, network transfer, model inference, agent decisions, and event serialization. The event stream does not expose pure server-side inference time, so do not rename this field to “model compute time.”

## Local Script Experiment

Run at least one warmup and three measured repetitions. Use the same query and aliases for serial and parallel runs.

```powershell
python "<SKILL_ROOT>\scripts\benchmark_search.py" local `
  --query "<fixed query>" `
  --alias "<fixed alias>" `
  --mode serial `
  --warmups 1 `
  --runs 5
```

Repeat with `--mode parallel` only after the serial baseline. Do not infer a model bottleneck from this layer; it intentionally excludes model and host-agent tool-dispatch time.

The default steps are `status`, `vault`, `library`, and `paper`. Repeat `--step` to isolate selected components. Use `--keep-output` only for local debugging because it stores search output in the benchmark directory.

## Complete Model Experiment

Put the exact task in a UTF-8 prompt file. Keep the prompt, attached image, corpus state, service tier, reasoning effort, sandbox, and repetition count identical across models; put every setting except the model into the fixed `--agent-command` template so that only `{model}` differs. The runner alternates model order between repetitions to reduce first-run and time-of-day bias.

```powershell
python "<SKILL_ROOT>\scripts\benchmark_search.py" model `
  --prompt-file "<fixed-prompt.txt>" `
  --agent-command '["<host-agent-cli>", "<non-interactive-json-args>", "--model", "{model}"]' `
  --model "<model-a>" `
  --model "<model-b>" `
  --runs 3
```

The template should start an ephemeral, read-only session of the host agent; the runner substitutes only the model and fails closed when the template lacks `{model}`. Network access and normal model usage are still required. A failed or timed-out run remains in the report instead of being silently retried outside the declared repetition count. The attempted wall-time statistic includes failures; the successful wall-time statistic excludes them. Keep both when a model or client is unstable.

Raw host-agent output is not saved by default. Compact traces retain event types, tool or script labels, durations, exit states, usage, and short error summaries. `--keep-raw-events` stores complete model and tool output and therefore needs the same privacy treatment as the original search.

## Decision Rules

- If direct scripts are fast but complete runs are slow, first reduce model/tool round trips or use a faster controller model.
- If one direct script dominates the local median, optimize that script before changing models.
- If `status` dominates, separate a cheap per-search health check from full OCR, integration, and registry diagnostics.
- If tool intervals dominate only in the complete run, compare the matching direct script time with its host-agent tool interval; the difference is launch, sandbox, and dispatch overhead.
- Compare answer quality separately. A model wins only if it meets the same source and page-evidence requirements; latency alone is not sufficient.

Use medians for the main comparison and P90 for tail latency. Three runs are a minimum smoke comparison; five to ten runs are better when differences are small. Do not mix warm-cache and cold-cache results in one statistic.

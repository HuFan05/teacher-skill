from __future__ import annotations

import concurrent.futures
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import re
import statistics
import subprocess
import sys
import threading
import time
import uuid
from typing import Any, Iterable, Sequence

from .config import get_state_dir
from .integrations import runtime_skills_root


SCHEMA_VERSION = 1
TOOL_TYPE_MARKERS = ("command", "tool", "mcp", "web_search", "browser")


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def percentile_nearest_rank(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    rank = max(1, math.ceil((percentile / 100.0) * len(ordered)))
    return ordered[rank - 1]


def metric_stats(values: Sequence[float]) -> dict[str, float | int | None]:
    if not values:
        return {
            "count": 0,
            "min_ms": None,
            "median_ms": None,
            "mean_ms": None,
            "p90_ms": None,
            "max_ms": None,
        }
    numbers = [float(value) for value in values]
    return {
        "count": len(numbers),
        "min_ms": round(min(numbers), 3),
        "median_ms": round(statistics.median(numbers), 3),
        "mean_ms": round(statistics.fmean(numbers), 3),
        "p90_ms": round(float(percentile_nearest_rank(numbers, 90) or 0.0), 3),
        "max_ms": round(max(numbers), 3),
    }


def interval_union_ms(intervals: Iterable[tuple[float, float]]) -> float:
    normalized = sorted((max(0.0, start), max(0.0, end)) for start, end in intervals if end >= start)
    if not normalized:
        return 0.0
    total = 0.0
    current_start, current_end = normalized[0]
    for start, end in normalized[1:]:
        if start <= current_end:
            current_end = max(current_end, end)
        else:
            total += current_end - current_start
            current_start, current_end = start, end
    return total + current_end - current_start


def default_output_root() -> Path:
    return get_state_dir() / "benchmarks"


def create_run_dir(output_root: Path, kind: str) -> Path:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    target = output_root.expanduser().resolve() / f"{stamp}-{kind}-{uuid.uuid4().hex[:8]}"
    target.mkdir(parents=True, exist_ok=False)
    return target


def write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def append_jsonl(path: Path, payload: object) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


def _first_number(payload: object, keys: set[str]) -> int | None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in keys and isinstance(value, int) and not isinstance(value, bool):
                return value
        for value in payload.values():
            found = _first_number(value, keys)
            if found is not None:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = _first_number(value, keys)
            if found is not None:
                return found
    return None


def result_count(payload: object) -> int | None:
    if not isinstance(payload, dict):
        return None
    for key in ("results", "hits", "candidates", "expanded_notes", "grouped_results"):
        value = payload.get(key)
        if isinstance(value, list):
            return len(value)
    return _first_number(payload, {"result_count", "candidate_count", "grouped_note_count", "expanded_count"})


def _redacted_command(command: Sequence[str]) -> list[str]:
    redacted: list[str] = []
    hide_next = False
    for token in command:
        if hide_next:
            redacted.append("<redacted>")
            hide_next = False
            continue
        redacted.append(token)
        if token in {"--query", "--alias"}:
            hide_next = True
    return redacted


def run_timed_command(
    label: str,
    command: Sequence[str],
    *,
    timeout_seconds: float,
    keep_output: bool = False,
    phase: str = "measured",
    repetition: int = 1,
) -> dict[str, object]:
    started_utc = utc_now()
    started = time.perf_counter_ns()
    timed_out = False
    try:
        completed = subprocess.run(
            list(command),
            capture_output=True,
            text=False,
            timeout=timeout_seconds,
            check=False,
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
        )
        return_code = completed.returncode
        stdout = completed.stdout or b""
        stderr = completed.stderr or b""
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        return_code = None
        stdout = exc.stdout or b""
        stderr = exc.stderr or b""
    ended = time.perf_counter_ns()
    parsed: object | None = None
    if stdout:
        try:
            parsed = json.loads(stdout.decode("utf-8-sig", errors="replace"))
        except json.JSONDecodeError:
            parsed = None
    event: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "category": "script",
        "phase": phase,
        "repetition": repetition,
        "label": label,
        "started_at_utc": started_utc,
        "duration_ms": round((ended - started) / 1_000_000.0, 3),
        "return_code": return_code,
        "timed_out": timed_out,
        "stdout_bytes": len(stdout),
        "stderr_bytes": len(stderr),
        "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
        "command": _redacted_command(command),
        "json_ok": parsed.get("ok") if isinstance(parsed, dict) else None,
        "json_status": parsed.get("status") if isinstance(parsed, dict) else None,
        "result_count": result_count(parsed),
    }
    if keep_output:
        event["stdout"] = stdout.decode("utf-8-sig", errors="replace")
        event["stderr"] = stderr.decode("utf-8-sig", errors="replace")
    return event


def resolve_obsidian_script(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    return runtime_skills_root() / "obsidian-vault-notes" / "scripts" / "recall_notes.py"


def build_local_steps(
    *,
    query: str,
    aliases: Sequence[str],
    selected: Sequence[str],
    manage_kb_script: Path,
    obsidian_script: Path,
    python_executable: str,
    limit: int,
) -> list[tuple[str, list[str]]]:
    steps: list[tuple[str, list[str]]] = []
    if "status" in selected:
        steps.append(("status", [python_executable, str(manage_kb_script), "status", "--json"]))
    if "vault" in selected:
        steps.append(("vault", [python_executable, str(obsidian_script), "--query", query]))
    if "library" in selected:
        command = [
            python_executable,
            str(manage_kb_script),
            "library-search",
            "--query",
            query,
            "--limit",
            str(limit),
            "--json",
        ]
        for alias in aliases:
            command.extend(["--alias", alias])
        steps.append(("library", command))
    if "paper" in selected:
        command = [
            python_executable,
            str(manage_kb_script),
            "paper-search",
            "--query",
            query,
            "--limit",
            str(limit),
            "--json",
        ]
        for alias in aliases:
            command.extend(["--alias", alias])
        steps.append(("paper", command))
    return steps


def _execute_local_repetition(
    steps: Sequence[tuple[str, Sequence[str]]],
    *,
    mode: str,
    timeout_seconds: float,
    keep_output: bool,
    phase: str,
    repetition: int,
) -> tuple[float, list[dict[str, object]]]:
    started = time.perf_counter_ns()
    if mode == "parallel":
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(steps) or 1) as pool:
            futures = [
                pool.submit(
                    run_timed_command,
                    label,
                    command,
                    timeout_seconds=timeout_seconds,
                    keep_output=keep_output,
                    phase=phase,
                    repetition=repetition,
                )
                for label, command in steps
            ]
            events = [future.result() for future in futures]
    else:
        events = [
            run_timed_command(
                label,
                command,
                timeout_seconds=timeout_seconds,
                keep_output=keep_output,
                phase=phase,
                repetition=repetition,
            )
            for label, command in steps
        ]
    wall_ms = (time.perf_counter_ns() - started) / 1_000_000.0
    return round(wall_ms, 3), events


def summarize_local(
    *,
    query: str,
    aliases: Sequence[str],
    mode: str,
    warmups: int,
    runs: int,
    repetitions: Sequence[dict[str, object]],
) -> dict[str, object]:
    measured = [item for item in repetitions if item["phase"] == "measured"]
    labels = sorted(
        {str(event["label"]) for item in measured for event in item["events"]},
        key=str.casefold,
    )
    per_step: dict[str, object] = {}
    for label in labels:
        events = [event for item in measured for event in item["events"] if event["label"] == label]
        per_step[label] = {
            "duration": metric_stats([float(event["duration_ms"]) for event in events]),
            "failures": sum(
                1
                for event in events
                if event["timed_out"] or event["return_code"] != 0 or event.get("json_ok") is False
            ),
            "median_stdout_bytes": (
                round(statistics.median(int(event["stdout_bytes"]) for event in events), 1) if events else None
            ),
            "result_counts": [event.get("result_count") for event in events],
        }
    script_sums = [sum(float(event["duration_ms"]) for event in item["events"]) for item in measured]
    wall_values = [float(item["wall_ms"]) for item in measured]
    bottleneck = None
    if per_step:
        bottleneck = max(
            per_step,
            key=lambda label: float(per_step[label]["duration"]["median_ms"] or 0.0),
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "local-script-benchmark",
        "created_at_utc": utc_now(),
        "query_sha256": sha256_text(query),
        "alias_count": len(aliases),
        "mode": mode,
        "warmups": warmups,
        "runs": runs,
        "pipeline_wall": metric_stats(wall_values),
        "all_child_processes_inclusive": metric_stats(script_sums),
        "per_step": per_step,
        "slowest_step_by_median": bottleneck,
        "failed_events": sum(int(value["failures"]) for value in per_step.values()),
    }


def render_local_report(summary: dict[str, object]) -> str:
    wall = summary["pipeline_wall"]
    child = summary["all_child_processes_inclusive"]
    lines = [
        "# 本地知识库脚本性能报告",
        "",
        f"- 运行方式：{summary['mode']}",
        f"- 正式重复：{summary['runs']}次；预热：{summary['warmups']}次",
        f"- 查询指纹：`{summary['query_sha256']}`",
        f"- 整条脚本流水线中位数：{wall['median_ms']} ms",
        f"- 所有子进程耗时之和中位数：{child['median_ms']} ms",
        f"- 按中位数最慢的步骤：{summary['slowest_step_by_median']}",
        f"- 失败事件：{summary['failed_events']}",
        "",
        "## 分步骤",
        "",
        "| 步骤 | 中位数(ms) | P90(ms) | 最小(ms) | 最大(ms) | 失败数 | 中位输出字节 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for label, payload in summary["per_step"].items():
        duration = payload["duration"]
        lines.append(
            f"| {label} | {duration['median_ms']} | {duration['p90_ms']} | {duration['min_ms']} | "
            f"{duration['max_ms']} | {payload['failures']} | {payload['median_stdout_bytes']} |"
        )
    lines.extend(
        [
            "",
            "## 解释范围",
            "",
            "这里的脚本时间由外层进程用单调时钟测量，包含 Python 启动、脚本本身及脚本启动的子进程。它不包含宿主代理生成下一步指令、工具调度和网络等待。并行方式下，子进程耗时之和可以大于流水线墙钟时间，这是正常现象。查询正文默认不写入报告，只保存 SHA-256 指纹。",
            "",
        ]
    )
    return "\n".join(lines)


def run_local_benchmark(args: Any) -> dict[str, object]:
    script_root = Path(__file__).resolve().parents[1]
    manage_kb_script = Path(args.manage_kb).expanduser().resolve() if args.manage_kb else script_root / "manage_kb.py"
    obsidian_script = resolve_obsidian_script(args.obsidian_script)
    for required in (manage_kb_script, obsidian_script):
        if not required.is_file():
            raise FileNotFoundError(required)
    selected = args.step or ["status", "vault", "library", "paper"]
    steps = build_local_steps(
        query=args.query,
        aliases=args.alias,
        selected=selected,
        manage_kb_script=manage_kb_script,
        obsidian_script=obsidian_script,
        python_executable=args.python,
        limit=args.limit,
    )
    run_dir = create_run_dir(Path(args.output_dir), "local")
    events_path = run_dir / "events.jsonl"
    repetitions: list[dict[str, object]] = []
    schedule = [("warmup", index + 1) for index in range(args.warmups)] + [
        ("measured", index + 1) for index in range(args.runs)
    ]
    for phase, repetition in schedule:
        wall_ms, events = _execute_local_repetition(
            steps,
            mode=args.mode,
            timeout_seconds=args.timeout,
            keep_output=args.keep_output,
            phase=phase,
            repetition=repetition,
        )
        item = {"phase": phase, "repetition": repetition, "wall_ms": wall_ms, "events": events}
        repetitions.append(item)
        for event in events:
            append_jsonl(events_path, event)
    summary = summarize_local(
        query=args.query,
        aliases=args.alias,
        mode=args.mode,
        warmups=args.warmups,
        runs=args.runs,
        repetitions=repetitions,
    )
    summary["run_dir"] = str(run_dir)
    write_json(run_dir / "summary.json", summary)
    (run_dir / "report.md").write_text(render_local_report(summary), encoding="utf-8")
    return summary


def _item_type(event: dict[str, object]) -> str:
    item = event.get("item")
    return str(item.get("type", "")) if isinstance(item, dict) else ""


def _item_id(event: dict[str, object]) -> str:
    item = event.get("item")
    if isinstance(item, dict):
        return str(item.get("id") or item.get("call_id") or "")
    return ""


def _is_tool_item(item_type: str) -> bool:
    folded = item_type.casefold()
    return any(marker in folded for marker in TOOL_TYPE_MARKERS)


def _script_labels(value: object) -> list[str]:
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    matches = re.findall(r"(?i)([A-Za-z0-9_.-]+\.py)\b", text)
    return list(dict.fromkeys(matches))


def event_label(event: dict[str, object]) -> str | None:
    item = event.get("item")
    if not isinstance(item, dict):
        return None
    scripts = _script_labels(item)
    if scripts:
        return "+".join(scripts)
    for key in ("name", "tool_name", "server"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    item_type = str(item.get("type") or "")
    return item_type or None


def compact_agent_event(record: dict[str, object]) -> dict[str, object]:
    compact: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "at_ms": record["at_ms"],
        "stream": record["stream"],
    }
    event = record.get("event")
    if not isinstance(event, dict):
        compact["line_sha256"] = sha256_text(str(record.get("raw", "")))
        return compact
    compact["type"] = event.get("type")
    item_type = _item_type(event)
    if item_type:
        compact["item_type"] = item_type
        compact["item_id"] = _item_id(event)
        compact["label"] = event_label(event)
        item = event.get("item")
        if isinstance(item, dict):
            for key in ("status", "exit_code"):
                if key in item:
                    compact[key] = item[key]
    if event.get("type") in {"turn.completed", "turn.failed"}:
        if "usage" in event:
            compact["usage"] = event["usage"]
        if "error" in event:
            compact["error"] = event["error"]
    if event.get("type") == "error":
        message = str(event.get("message") or "")
        compact["error_message"] = message[:500]
    return compact


def analyze_agent_records(records: Sequence[dict[str, object]], process_wall_ms: float, return_code: int | None) -> dict[str, object]:
    stdout_events = [record for record in records if record.get("stream") == "stdout" and isinstance(record.get("event"), dict)]
    turn_start = next(
        (float(record["at_ms"]) for record in stdout_events if record["event"].get("type") == "turn.started"),
        None,
    )
    turn_end = next(
        (
            float(record["at_ms"])
            for record in reversed(stdout_events)
            if record["event"].get("type") in {"turn.completed", "turn.failed"}
        ),
        None,
    )
    starts: dict[str, tuple[float, str, str]] = {}
    tool_events: list[dict[str, object]] = []
    first_agent_message = None
    usage = None
    for record in stdout_events:
        event = record["event"]
        event_type = str(event.get("type") or "")
        item_type = _item_type(event)
        item_id = _item_id(event)
        at_ms = float(record["at_ms"])
        if event_type == "item.started" and item_id:
            starts[item_id] = (at_ms, item_type, event_label(event) or item_type or "unknown")
        elif event_type == "item.completed":
            if item_type == "agent_message" and first_agent_message is None:
                first_agent_message = at_ms
            if item_id in starts:
                start_ms, started_type, label = starts.pop(item_id)
                effective_type = item_type or started_type
                if _is_tool_item(effective_type):
                    tool_events.append(
                        {
                            "item_id": item_id,
                            "item_type": effective_type,
                            "label": label,
                            "start_ms": round(start_ms, 3),
                            "end_ms": round(at_ms, 3),
                            "duration_ms": round(max(0.0, at_ms - start_ms), 3),
                        }
                    )
        if event_type == "turn.completed" and event.get("usage") is not None:
            usage = event.get("usage")
    tool_union = interval_union_ms((float(item["start_ms"]), float(item["end_ms"])) for item in tool_events)
    tool_sum = sum(float(item["duration_ms"]) for item in tool_events)
    turn_wall = max(0.0, turn_end - turn_start) if turn_start is not None and turn_end is not None else None
    residual = max(0.0, turn_wall - tool_union) if turn_wall is not None else None
    per_label: dict[str, list[float]] = {}
    for item in tool_events:
        per_label.setdefault(str(item["label"]), []).append(float(item["duration_ms"]))
    error_events = [record["event"] for record in stdout_events if record["event"].get("type") == "error"]
    final_error = None
    for record in reversed(stdout_events):
        event = record["event"]
        if event.get("type") == "turn.failed":
            error = event.get("error")
            final_error = str(error.get("message") if isinstance(error, dict) else error)[:500]
            break
        if event.get("type") == "error":
            final_error = str(event.get("message") or "")[:500]
            break
    return {
        "return_code": return_code,
        "process_wall_ms": round(process_wall_ms, 3),
        "startup_until_turn_ms": round(turn_start, 3) if turn_start is not None else None,
        "turn_wall_ms": round(turn_wall, 3) if turn_wall is not None else None,
        "tool_union_ms": round(tool_union, 3),
        "tool_inclusive_sum_ms": round(tool_sum, 3),
        "model_orchestration_residual_ms": round(residual, 3) if residual is not None else None,
        "first_agent_message_after_turn_ms": (
            round(first_agent_message - turn_start, 3)
            if first_agent_message is not None and turn_start is not None
            else None
        ),
        "shutdown_after_turn_ms": (
            round(max(0.0, process_wall_ms - turn_end), 3) if turn_end is not None else None
        ),
        "tool_events": tool_events,
        "per_tool": {label: metric_stats(values) for label, values in sorted(per_label.items())},
        "error_event_count": len(error_events),
        "reconnect_event_count": sum(
            1 for event in error_events if "reconnect" in str(event.get("message") or "").casefold()
        ),
        "final_error": final_error,
        "usage": usage,
    }


AGENT_COMMAND_MODEL_PLACEHOLDER = "{model}"


def build_agent_command(template: Sequence[str], model: str) -> list[str]:
    """Substitute one model into a fixed host-agent command template.

    The template is the complete non-interactive host-agent command as argv
    tokens.  Every setting other than the model (sandbox, service tier,
    reasoning effort, attached images) stays in the template, so all compared
    models run under byte-identical settings.
    """

    if (
        not isinstance(template, (list, tuple))
        or not template
        or not all(isinstance(token, str) and token for token in template)
    ):
        raise ValueError("The agent command must be a nonempty list of nonempty strings")
    if not any(AGENT_COMMAND_MODEL_PLACEHOLDER in token for token in template):
        raise ValueError("The agent command must contain the {model} placeholder")
    if not isinstance(model, str) or not model.strip():
        raise ValueError("Each compared model must be a nonblank name")
    return [token.replace(AGENT_COMMAND_MODEL_PLACEHOLDER, model) for token in template]


def parse_agent_command(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError("The agent command must be a JSON array of argv strings") from exc
    if not isinstance(parsed, list):
        raise ValueError("The agent command must be a JSON array of argv strings")
    build_agent_command(parsed, "probe")
    return [str(token) for token in parsed]


def run_agent_process(
    *,
    command_template: Sequence[str],
    prompt: str,
    model: str,
    working_dir: Path,
    timeout_seconds: float,
    keep_raw_events: bool,
    trace_path: Path,
) -> dict[str, object]:
    """Run one host-agent turn and time it from outside the model process.

    The command reads the prompt on standard input and writes one JSON event
    per line on standard output using the ``turn.started``, ``item.started``,
    ``item.completed``, ``turn.completed``, ``turn.failed`` and ``error``
    event vocabulary.  A host with another event format needs a receiver-local
    adapter that re-emits these events.
    """

    command = build_agent_command(command_template, model)
    started = time.perf_counter()
    process = subprocess.Popen(
        command,
        cwd=working_dir,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
    )
    assert process.stdin is not None
    process.stdin.write(prompt)
    process.stdin.close()
    output_queue: queue.Queue[tuple[str, str] | tuple[str, None]] = queue.Queue()

    def reader(name: str, stream: Any) -> None:
        try:
            for line in iter(stream.readline, ""):
                output_queue.put((name, line.rstrip("\r\n")))
        finally:
            output_queue.put((name, None))

    assert process.stdout is not None and process.stderr is not None
    threads = [
        threading.Thread(target=reader, args=("stdout", process.stdout), daemon=True),
        threading.Thread(target=reader, args=("stderr", process.stderr), daemon=True),
    ]
    for thread in threads:
        thread.start()
    records: list[dict[str, object]] = []
    closed: set[str] = set()
    timed_out = False
    deadline = started + timeout_seconds
    drain_deadline: float | None = None
    while len(closed) < 2:
        now = time.perf_counter()
        if process.poll() is not None and drain_deadline is None:
            drain_deadline = now + 2.0
        if now >= deadline and process.poll() is None:
            timed_out = True
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            drain_deadline = time.perf_counter() + 2.0
        if drain_deadline is not None and now >= drain_deadline:
            break
        try:
            stream_name, line = output_queue.get(timeout=0.1)
        except queue.Empty:
            continue
        if line is None:
            closed.add(stream_name)
            continue
        at_ms = (time.perf_counter() - started) * 1000.0
        parsed = None
        if stream_name == "stdout":
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                parsed = None
        record: dict[str, object] = {"stream": stream_name, "at_ms": round(at_ms, 3), "raw": line}
        if isinstance(parsed, dict):
            record["event"] = parsed
        records.append(record)
        append_jsonl(trace_path, record if keep_raw_events else compact_agent_event(record))
    if len(closed) == 2:
        for thread in threads:
            thread.join(timeout=1)
    if process.poll() is None:
        process.kill()
    return_code = process.wait(timeout=5)
    process_wall_ms = (time.perf_counter() - started) * 1000.0
    for stream in (process.stdout, process.stderr):
        try:
            stream.close()
        except OSError:
            pass
    analysis = analyze_agent_records(records, process_wall_ms, return_code)
    analysis.update(
        {
            "model": model,
            "timed_out": timed_out,
            "stdout_bytes": sum(len(str(record.get("raw", "")).encode("utf-8")) for record in records if record["stream"] == "stdout"),
            "stderr_bytes": sum(len(str(record.get("raw", "")).encode("utf-8")) for record in records if record["stream"] == "stderr"),
        }
    )
    return analysis


def summarize_models(runs: Sequence[dict[str, object]], *, prompt: str, warmups: int, repetitions: int) -> dict[str, object]:
    models = sorted({str(item["model"]) for item in runs}, key=str.casefold)
    per_model: dict[str, object] = {}
    for model in models:
        measured = [item for item in runs if item["model"] == model and item["phase"] == "measured"]
        successful = [item for item in measured if item["return_code"] == 0 and not item["timed_out"]]

        def values(key: str) -> list[float]:
            return [float(item[key]) for item in successful if item.get(key) is not None]

        per_model[model] = {
            "successful_runs": len(successful),
            "failed_runs": len(measured) - len(successful),
            "attempted_process_wall": metric_stats(
                [float(item["process_wall_ms"]) for item in measured if item.get("process_wall_ms") is not None]
            ),
            "process_wall": metric_stats(values("process_wall_ms")),
            "turn_wall": metric_stats(values("turn_wall_ms")),
            "tool_union": metric_stats(values("tool_union_ms")),
            "model_orchestration_residual": metric_stats(values("model_orchestration_residual_ms")),
            "first_agent_message": metric_stats(values("first_agent_message_after_turn_ms")),
            "reconnect_events": sum(int(item["reconnect_event_count"]) for item in measured),
            "failure_reasons": sorted(
                {
                    ("timeout" if item.get("timed_out") else str(item.get("final_error") or "nonzero_exit"))
                    for item in measured
                    if item.get("return_code") != 0 or item.get("timed_out")
                }
            ),
        }
    fastest = None
    eligible = {
        model: payload
        for model, payload in per_model.items()
        if payload["process_wall"]["median_ms"] is not None
    }
    if eligible:
        fastest = min(eligible, key=lambda model: float(eligible[model]["process_wall"]["median_ms"]))
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "agent-end-to-end-benchmark",
        "created_at_utc": utc_now(),
        "prompt_sha256": sha256_text(prompt),
        "warmups_per_model": warmups,
        "runs_per_model": repetitions,
        "per_model": per_model,
        "fastest_model_by_process_median": fastest,
    }


def render_model_report(summary: dict[str, object]) -> str:
    lines = [
        "# 宿主代理端到端搜索性能报告",
        "",
        f"- 提示词指纹：`{summary['prompt_sha256']}`",
        f"- 每个模型正式重复：{summary['runs_per_model']}次；预热：{summary['warmups_per_model']}次",
        f"- 端到端中位数最快的模型：{summary['fastest_model_by_process_median'] or '无有效样本'}",
        "",
        "| 模型 | 成功/失败 | 所有尝试中位数(ms) | 成功进程中位数(ms) | 模型与编排余量中位数(ms) | 工具占用中位数(ms) | 重连 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for model, payload in summary["per_model"].items():
        lines.append(
            f"| {model} | {payload['successful_runs']}/{payload['failed_runs']} | "
            f"{payload['attempted_process_wall']['median_ms']} | {payload['process_wall']['median_ms']} | "
            f"{payload['model_orchestration_residual']['median_ms']} | {payload['tool_union']['median_ms']} | "
            f"{payload['reconnect_events']} |"
        )
        if payload["failure_reasons"]:
            lines.append(f"\n- `{model}`失败原因：{'；'.join(payload['failure_reasons'])}")
    lines.extend(
        [
            "",
            "## 指标口径",
            "",
            "进程总时长从启动宿主代理命令开始，到进程退出为止。工具占用使用事件流中工具开始与完成事件的时间区间并集，避免并行工具重复计时。模型与编排余量等于一次 turn 的墙钟时间减去工具区间并集，包含服务排队、网络传输、模型推理、代理决定下一步和事件序列化；事件流没有暴露服务端纯推理计时，因此不能把这个余量全部解释成模型算力时间。首次完整回答指 turn 开始到第一个完成的 agent message，不是首 token 延迟。",
            "",
            "默认事件日志只保留事件类型、脚本名、时长、退出状态和错误摘要，不保存提示词、搜索摘录或最终回答。只有显式使用`--keep-raw-events`时才保存原始事件。",
            "",
        ]
    )
    return "\n".join(lines)


def run_model_benchmark(args: Any) -> dict[str, object]:
    prompt_path = Path(args.prompt_file).expanduser().resolve()
    prompt = prompt_path.read_text(encoding="utf-8-sig")
    command_template = parse_agent_command(args.agent_command)
    for model in args.model:
        build_agent_command(command_template, model)
    working_dir = Path(args.working_dir).expanduser().resolve()
    if not working_dir.is_dir():
        raise NotADirectoryError(working_dir)
    run_dir = create_run_dir(Path(args.output_dir), "models")
    runs: list[dict[str, object]] = []
    schedule = [("warmup", index + 1) for index in range(args.warmups)] + [
        ("measured", index + 1) for index in range(args.runs)
    ]
    for sequence_index, (phase, repetition) in enumerate(schedule):
        ordered_models = list(args.model)
        if sequence_index % 2 == 1:
            ordered_models.reverse()
        for order_index, model in enumerate(ordered_models, start=1):
            trace = run_dir / f"{model.replace('/', '_')}-{phase}-{repetition}.jsonl"
            result = run_agent_process(
                command_template=command_template,
                prompt=prompt,
                model=model,
                working_dir=working_dir,
                timeout_seconds=args.timeout,
                keep_raw_events=args.keep_raw_events,
                trace_path=trace,
            )
            result.update(
                {
                    "phase": phase,
                    "repetition": repetition,
                    "order_in_repetition": order_index,
                    "trace": str(trace),
                }
            )
            runs.append(result)
    summary = summarize_models(runs, prompt=prompt, warmups=args.warmups, repetitions=args.runs)
    summary["run_dir"] = str(run_dir)
    summary["runs"] = runs
    write_json(run_dir / "summary.json", summary)
    (run_dir / "report.md").write_text(render_model_report(summary), encoding="utf-8")
    return summary


__all__ = [
    "SCHEMA_VERSION",
    "analyze_agent_records",
    "build_agent_command",
    "build_local_steps",
    "event_label",
    "interval_union_ms",
    "metric_stats",
    "percentile_nearest_rank",
    "render_local_report",
    "render_model_report",
    "run_agent_process",
    "run_local_benchmark",
    "run_model_benchmark",
    "run_timed_command",
    "summarize_local",
    "summarize_models",
]

from __future__ import annotations

import argparse
import gzip
import hashlib
import http.client
import json
import os
import re
import subprocess
import time
import urllib.parse
from pathlib import Path


SAFE_NAME = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


def _target_hash(command: list[str]) -> str | None:
    for value in command:
        candidate = Path(value)
        if candidate.suffix.lower() in {".py", ".ps1", ".js", ".ts", ".mjs", ".cjs"} and candidate.is_file():
            try:
                return hashlib.sha256(candidate.read_bytes()).hexdigest()
            except OSError:
                return None
    return None


def _send(skill: str, catalog: str, phase: str, command: list[str], start: int, end: int) -> None:
    try:
        endpoint = urllib.parse.urlsplit(
            os.environ.get(
                "SKILL_OBSERVER_PHASE_ENDPOINT",
                "http://127.0.0.1:4318/v1/phase-batches",
            )
        )
        if endpoint.scheme != "http" or endpoint.hostname not in {"127.0.0.1", "localhost"} or not endpoint.port:
            return
        operation = next(
            (
                Path(value).stem
                for value in command
                if Path(value).suffix.lower() in {".py", ".ps1", ".js", ".ts", ".mjs", ".cjs"}
                and SAFE_NAME.fullmatch(Path(value).stem)
            ),
            "process",
        )
        payload = {
            "skill_name": skill,
            "thread_key": os.environ.get("SKILL_OBSERVER_THREAD_ID"),
            "catalog_version": catalog,
            "script_sha256": _target_hash(command),
            "intervals": [
                {
                    "phase_code": phase,
                    "started_at_ns": start,
                    "ended_at_ns": max(start, end),
                    "fields": {"operation": operation},
                }
            ],
        }
        body = gzip.compress(json.dumps(payload, separators=(",", ":")).encode("utf-8"), compresslevel=1, mtime=0)
        connection = http.client.HTTPConnection(endpoint.hostname, endpoint.port, timeout=0.05)
        target = endpoint.path or "/"
        if endpoint.query:
            target += f"?{endpoint.query}"
        connection.request(
            "POST",
            target,
            body=body,
            headers={"Content-Type": "application/json", "Content-Encoding": "gzip"},
        )
        response = connection.getresponse()
        response.read(1)
        connection.close()
    except Exception:
        return


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skill", required=True)
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--phase", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("a command is required after --")
    start = time.time_ns()
    try:
        completed = subprocess.run(command, check=False)
        return int(completed.returncode)
    finally:
        _send(args.skill, args.catalog, args.phase, command, start, time.time_ns())


if __name__ == "__main__":
    raise SystemExit(main())

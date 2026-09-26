from __future__ import annotations

import atexit
import functools
import gzip
import hashlib
import json
import os
import re
import socket
import time
import urllib.parse
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, TypeVar


try:
    import ctypes

    _WINMM = ctypes.WinDLL("winmm") if os.name == "nt" else None
except (ImportError, OSError):
    _WINMM = None


F = TypeVar("F", bound=Callable[..., Any])
SAFE_NUMERIC_FIELDS = {
    "file_count",
    "candidate_count",
    "byte_count",
    "harness_call_count",
    "result_count",
    "change_count",
    "warning_count",
    "error_count",
}
SAFE_NAME_FIELDS = {"mode", "operation"}
SAFE_BOOLEAN_FIELDS = {"write"}
SAFE_NAME = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


def _post_phase_payload(
    endpoint: str,
    payload: dict[str, Any],
    timeout_seconds: float,
) -> bool:
    parsed = urllib.parse.urlsplit(endpoint)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost"}
        or not parsed.port
    ):
        return False
    raw_body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = gzip.compress(raw_body, compresslevel=1, mtime=0)
    path = parsed.path or "/"
    if parsed.query:
        path += f"?{parsed.query}"
    request = (
        f"POST {path} HTTP/1.1\r\n"
        f"Host: {parsed.hostname}:{parsed.port}\r\n"
        "Content-Type: application/json\r\n"
        "Content-Encoding: gzip\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode("ascii") + body
    timer_resolution_active = False
    try:
        if _WINMM is not None:
            timer_resolution_active = _WINMM.timeBeginPeriod(1) == 0
        with socket.create_connection(
            (parsed.hostname, parsed.port),
            timeout=timeout_seconds,
        ) as connection:
            connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            connection.settimeout(timeout_seconds)
            connection.sendall(request)
            connection.shutdown(socket.SHUT_WR)
            connection.setblocking(False)
            response = bytearray()
            deadline = time.perf_counter() + timeout_seconds
            while b"\r\n\r\n" not in response and len(response) < 4096:
                try:
                    block = connection.recv(1024)
                except BlockingIOError:
                    if time.perf_counter() >= deadline:
                        return False
                    continue
                if not block:
                    break
                response.extend(block)
        return bytes(response).startswith((b"HTTP/1.0 200", b"HTTP/1.1 200"))
    except OSError:
        return False
    finally:
        if timer_resolution_active:
            _WINMM.timeEndPeriod(1)


def _script_sha256() -> str | None:
    try:
        return hashlib.sha256(Path(os.path.abspath(os.sys.argv[0])).read_bytes()).hexdigest()
    except OSError:
        return None


class Recorder:
    """Fail-open process-local recorder; sends at most one phase batch at exit."""

    def __init__(self) -> None:
        self.intervals: list[dict[str, Any]] = []
        self.sent = False
        self.script_sha256 = _script_sha256()
        atexit.register(self.flush)

    @contextmanager
    def phase(self, code: str, **fields: Any) -> Iterator[dict[str, Any]]:
        start = time.time_ns()
        try:
            yield fields
        finally:
            end = time.time_ns()
            safe_fields = {
                key: value
                for key, value in fields.items()
                if key in SAFE_NUMERIC_FIELDS
                and isinstance(value, int)
                and not isinstance(value, bool)
                and value >= 0
            }
            safe_fields.update(
                {
                    key: value
                    for key, value in fields.items()
                    if key in SAFE_NAME_FIELDS
                    and isinstance(value, str)
                    and SAFE_NAME.fullmatch(value)
                }
            )
            safe_fields.update(
                {
                    key: value
                    for key, value in fields.items()
                    if key in SAFE_BOOLEAN_FIELDS and isinstance(value, bool)
                }
            )
            self.intervals.append(
                {
                    "phase_code": code,
                    "started_at_ns": start,
                    "ended_at_ns": max(start, end),
                    "fields": safe_fields,
                }
            )

    def flush(self) -> bool:
        if self.sent or not self.intervals:
            return True
        payload = {
            "skill_name": "obsidian-vault-notes",
            "thread_key": os.environ.get("SKILL_OBSERVER_THREAD_ID"),
            "catalog_version": "obsidian-vault-notes/v1",
            "script_sha256": self.script_sha256,
            "intervals": self.intervals,
        }
        endpoint = os.environ.get(
            "SKILL_OBSERVER_PHASE_ENDPOINT",
            "http://127.0.0.1:4318/v1/phase-batches",
        )
        delivered = _post_phase_payload(endpoint, payload, 0.05)
        self.sent = delivered
        return delivered


RECORDER = Recorder()


def observed(code: str) -> Callable[[F], F]:
    def decorate(function: F) -> F:
        @functools.wraps(function)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            with RECORDER.phase(code):
                return function(*args, **kwargs)

        return wrapped  # type: ignore[return-value]

    return decorate


def phase(code: str, **fields: Any) -> Any:
    return RECORDER.phase(code, **fields)


def flush() -> bool:
    return RECORDER.flush()

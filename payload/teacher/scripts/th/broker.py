"""Closed operation broker.

This module is the second half of full control. The first half is the closed
draft channel: a model may only return enumerated fields. This half answers the
next question — what may a model *do*?

The answer is: nothing directly. A model cannot run a process, open a file or
reach the network. It may only **ask**, using a closed operation vocabulary, and
this Skill decides. Every execution is bounded, confined to the contract's
declared roots or network policy, and recorded as a receipt. A refused request
has no effect.

    the model proposes      -> this Skill validates against declared scope
    this Skill executes    -> with a timeout, a byte ceiling and a receipt
    the model receives      -> bounded values plus bounded excerpts, as data
    the user never sees     -> raw output; only verified quotes and closed
                               values reach a surface

Every operation is read-only. There is no method here that takes a command line
from the model: `run_declared` names an entry in the contract's allow list; it
cannot introduce one.
"""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from . import constants as C
from .model import digest, utc_now

# --------------------------------------------------------------------------
# Closed vocabulary
# --------------------------------------------------------------------------
OP_HASH = "hash_file"
OP_STAT = "stat_file"
OP_COUNT_LINES = "count_lines"
OP_RUN = "run_declared"
OP_SEARCH_LOCAL = "search_local"
OP_READ_SECTION = "read_section"
OP_SEARCH_LIBRARY = "search_library"
OP_READ_LIBRARY = "read_library"
OP_ARXIV = "arxiv_search"
OP_FETCH = "fetch_url"

# The four inspection operations return bounded values only.
INSPECTIONS = (OP_HASH, OP_STAT, OP_COUNT_LINES, OP_RUN)
# The retrieval operations return bounded excerpts for the model's packet.
RETRIEVALS = (OP_SEARCH_LOCAL, OP_READ_SECTION, OP_SEARCH_LIBRARY, OP_READ_LIBRARY, OP_ARXIV, OP_FETCH)
NETWORK_OPERATIONS = (OP_ARXIV, OP_FETCH)

OPERATIONS = INSPECTIONS + RETRIEVALS

# Required argument names per operation. A request carrying any other key is
# refused rather than ignored, so a model cannot smuggle a payload past the
# validator by naming a field this Skill happens not to read.
OPERATION_ARGUMENTS: dict[str, tuple[str, ...]] = {
    OP_HASH: ("relative_path",),
    OP_STAT: ("relative_path",),
    OP_COUNT_LINES: ("relative_path",),
    OP_RUN: ("name", "arguments"),
    OP_SEARCH_LOCAL: ("query",),
    OP_READ_SECTION: ("section_id",),
    OP_SEARCH_LIBRARY: ("query",),
    OP_READ_LIBRARY: ("item_id",),
    OP_ARXIV: ("query",),
    OP_FETCH: ("url",),
}

MAX_QUERY_CHARS = 200
MAX_URL_CHARS = 500
TOKEN_ID_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")

MAX_OUTPUT_BYTES = 8192
EXCERPT_TAIL_CHARS = 3000
MAX_EXCERPT_CHARS_RETRIEVAL = 3000
MAX_FILE_BYTES = 64 * 1024 * 1024
DEFAULT_TIMEOUT = 20.0
MAX_TIMEOUT = 120.0

# Shell metacharacters. `subprocess` is called with `shell=False` and an argv
# list, so these are already inert — a `;` arrives as a literal argument. They
# are refused anyway, for two reasons: an argument containing them is almost
# always an attempt rather than a real parameter, and a second layer means the
# property survives a future edit that accidentally reintroduces a shell.
SHELL_METACHARACTERS = (";", "|", "&", "`", "$", "<", ">", "(", ")", "{", "}", "*", "?", "!", "\\", '"', "'", "~")

ERR_OPERATION_UNKNOWN = "operation_unknown"
ERR_OPERATION_ARGUMENTS = "operation_arguments_invalid"
ERR_OPERATION_OUTSIDE_SCOPE = "operation_outside_scope"
ERR_OPERATION_NOT_DECLARED = "operation_not_declared"
ERR_OPERATION_TOO_LARGE = "operation_input_too_large"
ERR_OPERATION_TIMEOUT = "operation_timeout"
ERR_OPERATION_FAILED = "operation_failed"

OPERATION_ERRORS = (
    ERR_OPERATION_UNKNOWN,
    ERR_OPERATION_ARGUMENTS,
    ERR_OPERATION_OUTSIDE_SCOPE,
    ERR_OPERATION_NOT_DECLARED,
    ERR_OPERATION_TOO_LARGE,
    ERR_OPERATION_TIMEOUT,
    ERR_OPERATION_FAILED,
)


class OperationRefused(RuntimeError):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


# --------------------------------------------------------------------------
# Request and result
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class OperationRequest:
    operation: str
    arguments: Mapping[str, str | list[str]]

    def canonical(self) -> dict[str, Any]:
        return {"operation": self.operation, "arguments": dict(self.arguments)}


def validate_request(raw: Any) -> OperationRequest:
    """Shape only. Scope and declaration are checked at execution time."""

    if raw is None:
        raise OperationRefused(ERR_OPERATION_UNKNOWN, "no request")
    if not isinstance(raw, dict):
        raise OperationRefused(ERR_OPERATION_ARGUMENTS, "request must be an object")
    if set(raw) != {"operation", "arguments"}:
        raise OperationRefused(
            ERR_OPERATION_ARGUMENTS, f"request keys must be exactly operation and arguments, got {sorted(raw)}"
        )
    operation = raw["operation"]
    if operation not in OPERATIONS:
        raise OperationRefused(ERR_OPERATION_UNKNOWN, str(operation))
    arguments = raw["arguments"]
    if not isinstance(arguments, dict):
        raise OperationRefused(ERR_OPERATION_ARGUMENTS, "arguments must be an object")
    allowed = set(OPERATION_ARGUMENTS[operation])
    extra = set(arguments) - allowed
    if extra:
        raise OperationRefused(ERR_OPERATION_ARGUMENTS, f"undeclared arguments: {sorted(extra)}")
    missing = allowed - set(arguments)
    if missing:
        raise OperationRefused(ERR_OPERATION_ARGUMENTS, f"missing arguments: {sorted(missing)}")

    if operation == OP_RUN:
        name = arguments["name"]
        argv = arguments["arguments"]
        if not isinstance(name, str) or not name:
            raise OperationRefused(ERR_OPERATION_ARGUMENTS, "name must be a non-empty string")
        if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
            raise OperationRefused(ERR_OPERATION_ARGUMENTS, "arguments must be a list of strings")
        if len(argv) > 32:
            raise OperationRefused(ERR_OPERATION_ARGUMENTS, "too many arguments")
        for item in argv:
            if len(item) > 512 or any(char in item for char in ("\x00", "\n", "\r")):
                raise OperationRefused(ERR_OPERATION_ARGUMENTS, "argument is not a single plain token")
            # A leading dash is allowed (a flag), but a metacharacter is not.
            if any(char in item for char in SHELL_METACHARACTERS):
                raise OperationRefused(ERR_OPERATION_ARGUMENTS, "argument carries a shell metacharacter")
        return OperationRequest(operation, {"name": name, "arguments": tuple(argv)})

    if operation in (OP_SEARCH_LOCAL, OP_SEARCH_LIBRARY, OP_ARXIV):
        query = arguments["query"]
        if not isinstance(query, str) or not query.strip() or len(query) > MAX_QUERY_CHARS:
            raise OperationRefused(ERR_OPERATION_ARGUMENTS, "query must be a short non-empty string")
        if any(char in query for char in ("\x00", "\n", "\r")):
            raise OperationRefused(ERR_OPERATION_ARGUMENTS, "query must be one line")
        return OperationRequest(operation, {"query": query.strip()})
    if operation in (OP_READ_SECTION, OP_READ_LIBRARY):
        key = "section_id" if operation == OP_READ_SECTION else "item_id"
        value = arguments[key]
        if not isinstance(value, str) or not value or len(value) > 96 or not set(value) <= TOKEN_ID_CHARS:
            raise OperationRefused(ERR_OPERATION_ARGUMENTS, f"{key} must be an identifier")
        return OperationRequest(operation, {key: value})
    if operation == OP_FETCH:
        url = arguments["url"]
        if not isinstance(url, str) or not url.startswith(("https://", "http://")) or len(url) > MAX_URL_CHARS:
            raise OperationRefused(ERR_OPERATION_ARGUMENTS, "url must be an http(s) URL")
        if any(char in url for char in ("\x00", "\n", "\r", " ")):
            raise OperationRefused(ERR_OPERATION_ARGUMENTS, "url must be one token")
        return OperationRequest(operation, {"url": url})

    relative = arguments["relative_path"]
    if not isinstance(relative, str) or not relative.strip():
        raise OperationRefused(ERR_OPERATION_ARGUMENTS, "relative_path must be a non-empty string")
    if len(relative) > 1024:
        raise OperationRefused(ERR_OPERATION_ARGUMENTS, "relative_path is too long")
    return OperationRequest(operation, {"relative_path": relative})


# --------------------------------------------------------------------------
# Scope
# --------------------------------------------------------------------------
def confine(root: Path, relative_path: str) -> Path:
    """Resolve inside a root, or refuse.

    A symlink pointing outside the root is refused too, because `resolve()`
    follows it before the containment test.
    """

    root_resolved = Path(root).expanduser().resolve()
    candidate = (root_resolved / relative_path).resolve()
    if candidate != root_resolved and root_resolved not in candidate.parents:
        raise OperationRefused(ERR_OPERATION_OUTSIDE_SCOPE, relative_path)
    return candidate


# --------------------------------------------------------------------------
# Broker
# --------------------------------------------------------------------------
Executor = Callable[[list[str], Path, float], tuple[int, str, str]]


def subprocess_executor(command: list[str], cwd: Path, timeout: float) -> tuple[int, str, str]:
    completed = subprocess.run(
        command,
        cwd=str(cwd),
        shell=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return completed.returncode, completed.stdout, completed.stderr


class Broker:
    """Executes declared operations. Read-only by construction.

    The contract supplies the roots and the command allow list. Neither can be
    widened by anything a model says, and neither is stored here: like the store,
    the broker holds no standing capability it was not handed per call.
    """

    def __init__(
        self,
        *,
        read_roots: tuple[Path, ...],
        declared_commands: Mapping[str, tuple[str, ...]] | None = None,
        executor: Executor | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        store: Any = None,
        network: Mapping[str, Any] | None = None,
        fetcher: Any = None,
    ) -> None:
        # `store` serves the retrieval index and the project library; `network`
        # is the policy for the two network operations; `fetcher` replaces the
        # web module in tests. None of them can be widened by a model.
        self.store = store
        self.network = dict(network or {"enabled": False})
        self.fetcher = fetcher
        self.read_roots = tuple(Path(root).expanduser().resolve() for root in read_roots)
        self.declared_commands = {
            name: tuple(argv) for name, argv in (declared_commands or {}).items()
        }
        self.executor = executor or subprocess_executor
        self.timeout = min(max(timeout, 0.1), MAX_TIMEOUT)

    @classmethod
    def from_contract(
        cls,
        contract: Mapping[str, Any],
        *,
        executor: Executor | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> "Broker":
        scope = contract.get("scope", {}) if isinstance(contract, dict) else {}
        delegation = contract.get("delegation", {}) if isinstance(contract, dict) else {}
        roots = scope.get("read_roots") or scope.get("roots") or []
        declared = delegation.get("commands") or {}
        normalized: dict[str, tuple[str, ...]] = {}
        if isinstance(declared, dict):
            for name, argv in declared.items():
                if isinstance(argv, (list, tuple)) and argv and all(isinstance(item, str) for item in argv):
                    normalized[str(name)] = tuple(str(item) for item in argv)
        return cls(
            read_roots=tuple(Path(root) for root in roots),
            declared_commands=normalized,
            executor=executor,
            timeout=timeout,
            network=contract.get("network") if isinstance(contract, dict) else None,
        )

    # -- execution --------------------------------------------------------
    def execute(self, request: OperationRequest) -> dict[str, Any]:
        """Run one validated request and return a bounded, receipted result."""

        started = utc_now()
        if request.operation == OP_RUN:
            result = self._run_declared(request)
        elif request.operation in RETRIEVALS:
            result = self._retrieve(request)
        else:
            result = self._inspect(request)
        result.setdefault("excerpts", [])
        result["receipt"] = {
            "schema": "th-operation-receipt/v1",
            "operation": request.operation,
            "request_sha256": digest(request.canonical()),
            "result_sha256": digest(result.get("value")),
            "started_at": started,
            "finished_at": utc_now(),
            "executed_by": "Skill",
        }
        return result

    # -- read-only inspections -------------------------------------------
    def _first_root(self) -> Path:
        if not self.read_roots:
            raise OperationRefused(ERR_OPERATION_OUTSIDE_SCOPE, "no declared read root")
        return self.read_roots[0]

    def _inspect(self, request: OperationRequest) -> dict[str, Any]:
        relative = str(request.arguments["relative_path"])
        target = None
        for root in self.read_roots:
            try:
                target = confine(root, relative)
                break
            except OperationRefused:
                continue
        if target is None:
            raise OperationRefused(ERR_OPERATION_OUTSIDE_SCOPE, relative)
        if not target.is_file():
            raise OperationRefused(ERR_OPERATION_FAILED, "not a regular file")

        size = target.stat().st_size
        if size > MAX_FILE_BYTES:
            raise OperationRefused(ERR_OPERATION_TOO_LARGE, f"{size} bytes")

        if request.operation == OP_STAT:
            stat = target.stat()
            return {
                "operation": OP_STAT,
                "value": {"bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns},
                "bounded": True,
            }

        data = target.read_bytes()
        if request.operation == OP_HASH:
            return {
                "operation": OP_HASH,
                "value": {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)},
                "bounded": True,
            }
        if request.operation == OP_COUNT_LINES:
            return {
                "operation": OP_COUNT_LINES,
                "value": {"lines": data.count(b"\n"), "bytes": len(data)},
                "bounded": True,
            }
        raise OperationRefused(ERR_OPERATION_UNKNOWN, request.operation)

    # -- declared commands ------------------------------------------------
    def _run_declared(self, request: OperationRequest) -> dict[str, Any]:
        name = str(request.arguments["name"])
        argv = tuple(request.arguments["arguments"])
        if name not in self.declared_commands:
            # The model names an entry; it cannot supply the command.
            raise OperationRefused(ERR_OPERATION_NOT_DECLARED, name)
        command = list(self.declared_commands[name]) + list(argv)
        if not self.read_roots:
            raise OperationRefused(ERR_OPERATION_OUTSIDE_SCOPE, "no declared working root")
        cwd = self._first_root()
        try:
            returncode, stdout, _stderr = self.executor(command, cwd, self.timeout)
        except subprocess.TimeoutExpired as exc:
            raise OperationRefused(ERR_OPERATION_TIMEOUT, name) from exc
        except OSError as exc:
            raise OperationRefused(ERR_OPERATION_FAILED, name) from exc

        # Output is bounded and summarised. Raw stdout never enters the packet,
        # because raw output is untrusted text and the packet is a surface.
        encoded = stdout.encode("utf-8", errors="replace")
        truncated = len(encoded) > MAX_OUTPUT_BYTES
        # The value stays closed. The model additionally receives the tail of
        # the output as an excerpt, because explaining a check requires seeing
        # it; a surface never receives it.
        tail = stdout[-EXCERPT_TAIL_CHARS:]
        return {
            "operation": OP_RUN,
            "value": {
                "name": name,
                "returncode": returncode,
                "stdout_bytes": len(encoded),
                "stdout_sha256": hashlib.sha256(encoded).hexdigest(),
                "truncated": truncated,
            },
            "bounded": True,
            "excerpts": [
                {
                    "kind": "executed_check",
                    "locator": f"run:{name}:returncode={returncode}",
                    "text": tail,
                    "returncode": returncode,
                }
            ],
            "note": "raw output never reaches a surface; the model receives a bounded tail as data",
        }

    # -- retrieval --------------------------------------------------------
    def _retrieve(self, request: OperationRequest) -> dict[str, Any]:
        from . import retrieval, web

        op = request.operation
        args = request.arguments
        if op in NETWORK_OPERATIONS:
            try:
                if op == OP_ARXIV:
                    found = (self.fetcher.arxiv_search if self.fetcher else web.arxiv_search)(
                        str(args["query"]), self.network
                    )
                    excerpts = [
                        {
                            "kind": "retrieved_source",
                            "locator": entry["url"] or f"arXiv:{entry['id']}",
                            "text": f"{entry['title']} ({entry['published']}; {', '.join(entry['authors'])})\n{entry['summary']}",
                        }
                        for entry in found["entries"]
                    ]
                    return {
                        "operation": op,
                        "value": {"results": len(found["entries"]), "total_matches": found["total_matches"],
                                  "omitted": found["omitted"]},
                        "bounded": True,
                        "excerpts": excerpts,
                    }
                fetched = (self.fetcher.fetch_url if self.fetcher else web.fetch_url)(
                    str(args["url"]), self.network, max_chars=MAX_EXCERPT_CHARS_RETRIEVAL
                )
                return {
                    "operation": op,
                    "value": {"chars": fetched["chars"], "truncated": fetched["truncated"]},
                    "bounded": True,
                    "excerpts": [{"kind": "retrieved_source", "locator": fetched["url"], "text": fetched["text"]}],
                }
            except web.WebRefused as exc:
                raise OperationRefused(exc.code if exc.code in OPERATION_ERRORS else ERR_OPERATION_FAILED, op) from exc
        if self.store is None:
            raise OperationRefused(ERR_OPERATION_OUTSIDE_SCOPE, "no store for retrieval")
        try:
            if op == OP_SEARCH_LOCAL:
                found = retrieval.search_sections(self.store, str(args["query"]))
                return {
                    "operation": op,
                    "value": {"candidates": found["candidates"], "total_matches": found["total_matches"],
                              "omitted": found["omitted"], "indexed_files": found["indexed_files"]},
                    "bounded": True,
                }
            if op == OP_SEARCH_LIBRARY:
                found = retrieval.search_library(self.store, str(args["query"]))
                return {
                    "operation": op,
                    "value": {"candidates": found["candidates"], "total_matches": found["total_matches"],
                              "omitted": found["omitted"], "library_items": found["library_items"]},
                    "bounded": True,
                }
            if op == OP_READ_SECTION:
                section = retrieval.read_section(self.store, str(args["section_id"]))
            else:
                section = retrieval.library_item(self.store, str(args["item_id"]))
        except retrieval.RetrievalError as exc:
            raise OperationRefused(ERR_OPERATION_FAILED, op) from exc
        excerpts = []
        if section["text"]:
            excerpts.append({
                "kind": "retrieved_source",
                "locator": f"{section['path']}#{section['heading']}",
                "text": section["text"],
            })
        return {
            "operation": op,
            "value": {"state": section["state"], "truncated": bool(section.get("truncated"))},
            "bounded": True,
            "excerpts": excerpts,
        }

    # -- introspection ----------------------------------------------------
    def available(self) -> dict[str, Any]:
        return {
            "operations": list(OPERATIONS),
            "network_enabled": bool(self.network.get("enabled")),
            "retrieval_available": self.store is not None,
            "declared_commands": sorted(self.declared_commands),
            "read_roots": [str(root) for root in self.read_roots],
            "limits": {"output_bytes": MAX_OUTPUT_BYTES, "file_bytes": MAX_FILE_BYTES, "timeout": self.timeout},
        }

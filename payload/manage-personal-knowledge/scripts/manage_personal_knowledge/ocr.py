"""OCR readiness checks and an isolated, single-PDF OCR runner.

This module does not install dependencies, update the library index, or write to
the configured library.  Its output locations are explicit and overwrite is
always refused.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any, Mapping, Sequence


REQUIRED_OCRMYPDF_VERSION = "17.8.0"
REQUIRED_LANGUAGES = ("eng", "chi_sim", "chi_tra", "osd")
REQUIRED_TESSERACT_CONFIGS = ("configs/hocr",)
DEFAULT_TESSERACT = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def discover_ocr_tools(
    state_dir: str | Path,
    *,
    tesseract_path: str | Path | None = None,
) -> dict[str, Any]:
    """Resolve the pre-provisioned OCR tool locations without installing them."""

    state = Path(state_dir).expanduser().resolve()
    tools_root = state / "tools"
    ocrmypdf_root = tools_root / "ocrmypdf"
    venv_root = ocrmypdf_root / ".venv"
    if os.name == "nt":
        venv_python = venv_root / "Scripts" / "python.exe"
        venv_bin = venv_root / "Scripts"
    else:
        venv_python = venv_root / "bin" / "python"
        venv_bin = venv_root / "bin"
    tesseract = Path(tesseract_path or DEFAULT_TESSERACT).expanduser().resolve()
    tessdata = tools_root / "tessdata"
    return {
        "state_dir": str(state),
        "tool_root": str(tools_root),
        "ocrmypdf_root": str(ocrmypdf_root),
        "venv_root": str(venv_root),
        "venv_python": str(venv_python),
        "venv_bin": str(venv_bin),
        "tesseract": str(tesseract),
        "tessdata": str(tessdata),
    }


def _ocr_environment(paths: Mapping[str, str]) -> dict[str, str]:
    environment = os.environ.copy()
    path_entries = [str(paths["venv_bin"]), str(Path(paths["tesseract"]).parent)]
    existing_path = environment.get("PATH", "")
    environment["PATH"] = os.pathsep.join(path_entries + ([existing_path] if existing_path else []))
    environment["TESSDATA_PREFIX"] = str(paths["tessdata"])
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment.setdefault("PYTHONUTF8", "1")
    return environment


def _probe(
    command: Sequence[str],
    *,
    environment: Mapping[str, str],
) -> dict[str, Any]:
    safe_command = [str(part) for part in command]
    try:
        completed = subprocess.run(
            safe_command,
            env=dict(environment),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        return {
            "ok": False,
            "command": safe_command,
            "returncode": None,
            "stdout": "",
            "stderr": str(exc),
            "error": {"code": "launch_failed", "type": type(exc).__name__, "message": str(exc)},
        }
    return {
        "ok": completed.returncode == 0,
        "command": safe_command,
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
        "error": None
        if completed.returncode == 0
        else {
            "code": "probe_failed",
            "message": completed.stderr.strip() or completed.stdout.strip() or "Probe failed.",
        },
    }


def _parse_tesseract_languages(output: str) -> list[str]:
    languages: list[str] = []
    for line in output.splitlines():
        value = line.strip()
        if not value or value.startswith("List of available languages"):
            continue
        if re.fullmatch(r"[A-Za-z0-9_\-]+", value):
            languages.append(value)
    return sorted(set(languages))


def _probe_output(probe: Mapping[str, Any]) -> str:
    """Return probe text even when a CLI writes normal output to stderr."""

    stdout = str(probe.get("stdout") or "").strip()
    stderr = str(probe.get("stderr") or "").strip()
    return stdout or stderr


def _semantic_version(output: str) -> str:
    match = re.search(r"(?<!\d)(\d+\.\d+\.\d+)(?!\d)", output)
    return match.group(1) if match else ""


def preflight_ocr(
    state_dir: str | Path,
    *,
    tesseract_path: str | Path | None = None,
) -> dict[str, Any]:
    """Verify the pinned OCRmyPDF environment and required language data."""

    paths = discover_ocr_tools(state_dir, tesseract_path=tesseract_path)
    diagnostics: list[dict[str, Any]] = []
    required_files = {
        "venv_python": Path(paths["venv_python"]),
        "tesseract": Path(paths["tesseract"]),
        "tessdata": Path(paths["tessdata"]),
    }
    for relative in REQUIRED_TESSERACT_CONFIGS:
        required_files[f"tessdata/{relative}"] = Path(paths["tessdata"]) / Path(relative)
    for component, path in required_files.items():
        exists = path.is_dir() if component == "tessdata" else path.is_file()
        if not exists:
            diagnostics.append(
                {
                    "level": "error",
                    "code": "missing_dependency",
                    "component": component,
                    "path": str(path),
                    "message": f"Required OCR component is missing: {component}",
                }
            )
    if diagnostics:
        return {
            "ok": False,
            "status": "missing_dependencies",
            "paths": paths,
            "versions": {},
            "languages": {"required": list(REQUIRED_LANGUAGES), "available": [], "missing": list(REQUIRED_LANGUAGES)},
            "tesseract_configs": {
                "required": list(REQUIRED_TESSERACT_CONFIGS),
                "missing": [
                    relative
                    for relative in REQUIRED_TESSERACT_CONFIGS
                    if not (Path(paths["tessdata"]) / Path(relative)).is_file()
                ],
            },
            "checks": {},
            "diagnostics": diagnostics,
        }

    environment = _ocr_environment(paths)
    probes = {
        "ocrmypdf": _probe(
            [paths["venv_python"], "-B", "-m", "ocrmypdf", "--version"],
            environment=environment,
        ),
        "pypdfium2": _probe(
            [
                paths["venv_python"],
                "-B",
                "-c",
                "import importlib.metadata as m; print(m.version('pypdfium2'))",
            ],
            environment=environment,
        ),
        "tesseract_version": _probe([paths["tesseract"], "--version"], environment=environment),
        "tesseract_languages": _probe([paths["tesseract"], "--list-langs"], environment=environment),
    }
    for component, probe in probes.items():
        if not probe["ok"]:
            diagnostics.append(
                {
                    "level": "error",
                    "code": "dependency_probe_failed",
                    "component": component,
                    "message": probe["error"]["message"] if probe["error"] else "Probe failed.",
                }
            )
    ocrmypdf_version = (
        _semantic_version(_probe_output(probes["ocrmypdf"])) if probes["ocrmypdf"]["ok"] else ""
    )
    if probes["ocrmypdf"]["ok"] and not ocrmypdf_version:
        diagnostics.append(
            {
                "level": "error",
                "code": "unreadable_ocrmypdf_version",
                "component": "ocrmypdf",
                "message": "OCRmyPDF returned success without a readable semantic version.",
            }
        )
    if ocrmypdf_version and ocrmypdf_version != REQUIRED_OCRMYPDF_VERSION:
        diagnostics.append(
            {
                "level": "error",
                "code": "unexpected_ocrmypdf_version",
                "component": "ocrmypdf",
                "expected": REQUIRED_OCRMYPDF_VERSION,
                "actual": ocrmypdf_version,
                "message": "OCRmyPDF does not match the pinned version.",
            }
        )
    available_languages = (
        _parse_tesseract_languages(
            "\n".join(
                value
                for value in (
                    probes["tesseract_languages"]["stdout"],
                    probes["tesseract_languages"]["stderr"],
                )
                if value
            )
        )
        if probes["tesseract_languages"]["ok"]
        else []
    )
    missing_languages = [language for language in REQUIRED_LANGUAGES if language not in available_languages]
    for language in missing_languages:
        diagnostics.append(
            {
                "level": "error",
                "code": "missing_language",
                "component": "tessdata",
                "language": language,
                "message": f"Required Tesseract language is missing: {language}",
            }
        )
    ok = not any(item["level"] == "error" for item in diagnostics)
    return {
        "ok": ok,
        "status": "ready" if ok else "not_ready",
        "paths": paths,
        "versions": {
            "ocrmypdf": ocrmypdf_version or None,
            "pypdfium2": _probe_output(probes["pypdfium2"]) or None,
            "tesseract": _probe_output(probes["tesseract_version"]).splitlines()[0]
            if _probe_output(probes["tesseract_version"])
            else None,
        },
        "languages": {
            "required": list(REQUIRED_LANGUAGES),
            "available": available_languages,
            "missing": missing_languages,
        },
        "tesseract_configs": {
            "required": list(REQUIRED_TESSERACT_CONFIGS),
            "missing": [],
        },
        "checks": probes,
        "diagnostics": diagnostics,
    }


def _request_error(code: str, message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "invalid_request",
        "command": [],
        "returncode": None,
        "error": {"code": code, "message": message},
        "diagnostics": [],
    }


def run_ocr_one(
    input_pdf: str | Path,
    output_pdf: str | Path,
    sidecar_path: str | Path,
    state_dir: str | Path,
    *,
    languages: str = "eng+chi_sim",
    library_root: str | Path | None = None,
    tesseract_path: str | Path | None = None,
    verify_preflight: bool = True,
) -> dict[str, Any]:
    """OCR one PDF to explicit non-library output paths without overwrite."""

    source = Path(input_pdf).expanduser().resolve()
    output = Path(output_pdf).expanduser().resolve()
    sidecar = Path(sidecar_path).expanduser().resolve()
    if not source.is_file() or source.suffix.casefold() != ".pdf":
        return _request_error("invalid_input", "Input must be an existing PDF file.")
    if output.suffix.casefold() != ".pdf":
        return _request_error("invalid_output", "Output must use a .pdf extension.")
    if source == output:
        return _request_error("input_equals_output", "OCR output must not overwrite the input PDF.")
    if sidecar in {source, output}:
        return _request_error("unsafe_sidecar", "Sidecar must be separate from input and output files.")
    if output.exists() or sidecar.exists():
        return _request_error("output_exists", "Refusing to overwrite an existing output or sidecar.")
    if not output.parent.is_dir() or not sidecar.parent.is_dir():
        return _request_error("missing_output_directory", "Output and sidecar parent directories must already exist.")
    if library_root is not None:
        library = Path(library_root).expanduser().resolve()
        if _is_within(output, library) or _is_within(sidecar, library):
            return _request_error("library_write_refused", "OCR outputs must be outside the configured library.")
    requested_languages = [item.strip() for item in languages.split("+") if item.strip()]
    if not requested_languages:
        return _request_error("invalid_languages", "At least one OCR language is required.")

    preflight = preflight_ocr(state_dir, tesseract_path=tesseract_path)
    if verify_preflight and not preflight["ok"]:
        return {
            "ok": False,
            "status": "preflight_failed",
            "command": [],
            "returncode": None,
            "error": {"code": "ocr_not_ready", "message": "OCR dependencies failed preflight."},
            "preflight": preflight,
            "diagnostics": preflight["diagnostics"],
        }
    available = set(preflight.get("languages", {}).get("available", []))
    missing_requested = [item for item in requested_languages if item not in available]
    if verify_preflight and missing_requested:
        return _request_error(
            "requested_language_missing",
            f"Requested OCR languages are unavailable: {', '.join(missing_requested)}",
        )

    paths = preflight.get("paths") or discover_ocr_tools(state_dir, tesseract_path=tesseract_path)
    environment = _ocr_environment(paths)
    command = [
        paths["venv_python"],
        "-B",
        "-m",
        "ocrmypdf",
        "--skip-text",
        "--rotate-pages",
        "--output-type",
        "pdf",
        "--language",
        "+".join(requested_languages),
        "--sidecar",
        str(sidecar),
        str(source),
        str(output),
    ]
    probe = _probe(command, environment=environment)
    ok = probe["ok"] and output.is_file() and sidecar.is_file()
    diagnostics: list[dict[str, Any]] = []
    if probe["ok"] and not ok:
        diagnostics.append(
            {
                "level": "error",
                "code": "missing_ocr_outputs",
                "message": "OCRmyPDF returned success but did not create both requested outputs.",
            }
        )
    elif not probe["ok"]:
        diagnostics.append(
            {
                "level": "error",
                "code": "ocr_process_failed",
                "message": probe["error"]["message"] if probe["error"] else "OCR process failed.",
            }
        )
    return {
        "ok": ok,
        "status": "completed" if ok else "failed",
        "input": str(source),
        "output": str(output),
        "sidecar": str(sidecar),
        "languages": requested_languages,
        "command": command,
        "returncode": probe["returncode"],
        "error": None if ok else probe["error"] or {"code": "missing_ocr_outputs", "message": diagnostics[0]["message"]},
        "diagnostics": diagnostics,
    }

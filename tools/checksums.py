"""Compute checksums.sha256 and the aggregate inventory hash.

    python tools/checksums.py            # verify against the committed file
    python tools/checksums.py --write    # regenerate

The aggregate hash is taken over sorted `relative_path NUL file_sha256 LF`
records, so two trees agree only if their names and their bytes agree.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "checksums.sha256"
SUMMARY = ROOT / "checksums.aggregate.json"

EXCLUDED_DIRS = {".git", "__pycache__", ".pytest_cache", "work", "node_modules", ".venv", "venv", ".workbuddy"}
EXCLUDED_NAMES = {"checksums.sha256", "checksums.aggregate.json", ".DS_Store"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".sqlite3", ".sqlite3-wal", ".sqlite3-shm"}


def files() -> list[Path]:
    out: list[Path] = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        if any(part in EXCLUDED_DIRS for part in path.relative_to(ROOT).parts):
            continue
        if path.name in EXCLUDED_NAMES or path.suffix.casefold() in EXCLUDED_SUFFIXES:
            continue
        out.append(path)
    return out


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def aggregate(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: str(item.relative_to(ROOT))):
        digest.update(str(path.relative_to(ROOT)).encode("utf-8"))
        digest.update(b"\x00")
        digest.update(file_sha256(path).encode("ascii"))
        digest.update(b"\x0a")
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="regenerate instead of verifying")
    args = parser.parse_args(argv)

    paths = files()
    lines = [
        f"{file_sha256(path)}  {path.relative_to(ROOT).as_posix()}"
        for path in sorted(paths, key=lambda item: str(item.relative_to(ROOT)))
    ]
    body = "\n".join(lines) + "\n"
    total = aggregate(paths)

    if args.write:
        MANIFEST.write_text(body, encoding="utf-8")
        SUMMARY.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "file_count": len(paths),
                    "aggregate_source_sha256": total,
                    "algorithm": "sha256 of sorted relative_path NUL file_sha256 LF records",
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"wrote {MANIFEST.name}: {len(paths)} files")
        print(f"aggregate_source_sha256 {total}")
        return 0

    if not MANIFEST.is_file():
        print(f"{MANIFEST.name} is missing; run with --write")
        return 2

    recorded = MANIFEST.read_text(encoding="utf-8").strip().splitlines()
    expected = {line.split("  ", 1)[1]: line.split("  ", 1)[0] for line in recorded if line.strip()}
    actual = {path.relative_to(ROOT).as_posix(): file_sha256(path) for path in paths}

    missing = sorted(set(expected) - set(actual))
    added = sorted(set(actual) - set(expected))
    changed = sorted(name for name in set(expected) & set(actual) if expected[name] != actual[name])

    recorded_total = None
    if SUMMARY.is_file():
        recorded_total = json.loads(SUMMARY.read_text(encoding="utf-8")).get("aggregate_source_sha256")

    if not missing and not added and not changed and recorded_total == total:
        print(f"verified: {len(paths)} files unchanged")
        print(f"aggregate_source_sha256 {total}")
        return 0

    if missing:
        print("missing:")
        for name in missing:
            print(f"  {name}")
    if added:
        print("added:")
        for name in added:
            print(f"  {name}")
    if changed:
        print("changed:")
        for name in changed:
            print(f"  {name}")
    if recorded_total != total:
        print(f"aggregate changed: {recorded_total} -> {total}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

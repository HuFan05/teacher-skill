#!/usr/bin/env python3
"""Read-only narrow Git audit for an Obsidian Vault target."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from _observer import flush as flush_observer
from _observer import phase


def run_git(repo: Path, arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *arguments],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
        shell=False,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--path", action="append", required=True)
    args = parser.parse_args(argv)
    repo = Path(args.repo).expanduser().resolve()
    pathspec = ["--", *args.path]

    with phase("obsidian.git.precheck"):
        top = run_git(repo, ["rev-parse", "--show-toplevel"])
        status = run_git(repo, ["status", "--short", *pathspec])
    with phase("obsidian.git.diff"):
        unstaged = run_git(repo, ["diff", "--numstat", *pathspec])
        staged = run_git(repo, ["diff", "--cached", "--numstat", *pathspec])
    with phase("obsidian.git.diff_check"):
        diff_check = run_git(repo, ["diff", "--check", *pathspec])
        staged_check = run_git(repo, ["diff", "--cached", "--check", *pathspec])

    commands = [top, status, unstaged, staged, diff_check, staged_check]
    payload = {
        "schema_version": 1,
        "ok": all(item.returncode == 0 for item in commands),
        "tracked_change_count": len(
            [line for line in status.stdout.splitlines() if line.strip()]
        ),
        "unstaged_file_count": len(
            [line for line in unstaged.stdout.splitlines() if line.strip()]
        ),
        "staged_file_count": len(
            [line for line in staged.stdout.splitlines() if line.strip()]
        ),
        "diff_check_error_count": len(
            [
                line
                for output in (diff_check.stdout, staged_check.stdout)
                for line in output.splitlines()
                if line.strip()
            ]
        ),
        "exit_codes": {
            "precheck": max(top.returncode, status.returncode),
            "diff": max(unstaged.returncode, staged.returncode),
            "diff_check": max(diff_check.returncode, staged_check.returncode),
        },
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    flush_observer()
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

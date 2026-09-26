"""Bounded discovery for a personal knowledge root.

Discovery is deliberately read-only.  It never searches above or beside the
supplied root, and it never chooses or persists a candidate on the user's
behalf.
"""

from __future__ import annotations

import os
from collections import defaultdict
from pathlib import Path
from typing import Iterable


_IGNORED_DIRECTORY_NAMES = {".git", "__pycache__"}
_EXACT_LIBRARY_NAMES = {
    "books",
    "digital library",
    "ebooks",
    "library",
    "pdf library",
    "pdfs",
    "图书馆",
    "数字图书馆",
    "电子书",
    "电子图书馆",
}
_LIBRARY_NAME_HINTS = ("book", "ebook", "library", "pdf", "书库", "图书馆", "电子书")


class DiscoveryError(ValueError):
    """Raised when discovery cannot safely inspect the supplied root."""


def _resolved_directory(path: os.PathLike[str] | str) -> Path:
    candidate = Path(path).expanduser()
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise DiscoveryError(f"Knowledge root does not exist or cannot be resolved: {candidate}") from exc
    if not resolved.is_dir():
        raise DiscoveryError(f"Knowledge root is not a directory: {resolved}")
    return resolved


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _walk_error(warnings: list[str]):
    def record(error: OSError) -> None:
        filename = getattr(error, "filename", None)
        warnings.append(f"unreadable-directory: {filename or error}")

    return record



class _ScanBudget:
    def __init__(self, directories, entries):
        if not 1 <= directories <= 100000 or not 1 <= entries <= 1000000:
            raise DiscoveryError("Invalid discovery scan limit")
        self.max_directories=directories; self.max_entries=entries
        self.directories=0; self.entries=0; self.incomplete=False


def _bounded_walk(root, warnings, budget):
    pending=[root]
    while pending:
        if budget.directories>=budget.max_directories or budget.entries>=budget.max_entries:
            budget.incomplete=True;return
        current=pending.pop();budget.directories+=1
        directories=[];files=[]
        try:
            with os.scandir(current) as iterator:
                while budget.entries<budget.max_entries:
                    try:entry=next(iterator)
                    except StopIteration:break
                    budget.entries+=1
                    child=Path(entry.path)
                    if child.is_symlink() or (hasattr(child,"is_junction") and child.is_junction()):continue
                    resolved=child.resolve(strict=False)
                    if not _is_within(resolved,root):continue
                    if entry.is_dir(follow_symlinks=False):directories.append(entry.name)
                    elif entry.is_file(follow_symlinks=False):files.append(entry.name)
                else:budget.incomplete=True
        except OSError:
            warnings.append("unreadable-directory");budget.incomplete=True
        yield str(current),directories,files
        if budget.entries>=budget.max_entries:return
        # The caller may prune this exact list after yield, as with top-down os.walk.
        pending.extend(current/name for name in reversed(directories))


def _find_vaults(root: Path, warnings: list[str], budget) -> list[Path]:
    vaults: set[Path] = set()
    for current, directory_names, _ in _bounded_walk(root, warnings, budget):
        current_path = Path(current).resolve(strict=False)
        if not _is_within(current_path, root):
            directory_names[:] = []
            continue

        # Never traverse external symlink directories or implementation data.
        kept: list[str] = []
        for name in directory_names:
            child = Path(current, name)
            if name in _IGNORED_DIRECTORY_NAMES or child.is_symlink():
                continue
            kept.append(name)
        directory_names[:] = kept

        if ".obsidian" in directory_names:
            marker = current_path / ".obsidian"
            try:
                marker_resolved = marker.resolve(strict=True)
            except (OSError, RuntimeError):
                continue
            if marker_resolved.is_dir() and _is_within(marker_resolved, root):
                vaults.add(current_path)
            # The marker contains no useful discovery targets.
            directory_names.remove(".obsidian")
    return sorted(vaults, key=lambda item: item.relative_to(root).as_posix().casefold())


def _normalised_name(path: Path) -> str:
    return " ".join(path.name.casefold().replace("_", " ").replace("-", " ").split())


def _name_hint(path: Path) -> tuple[int, str | None]:
    name = _normalised_name(path)
    if name in _EXACT_LIBRARY_NAMES:
        return 100_000, "exact-library-name"
    if any(hint in name for hint in _LIBRARY_NAME_HINTS):
        return 50_000, "library-name-hint"
    return 0, None


def _inside_any(path: Path, roots: Iterable[Path]) -> bool:
    return any(_is_within(path, root) for root in roots)


def _scan_pdfs(
    root: Path, vaults: list[Path], warnings: list[str], budget
) -> tuple[dict[Path, int], dict[Path, int], int]:
    direct_counts: dict[Path, int] = defaultdict(int)
    recursive_counts: dict[Path, int] = defaultdict(int)
    total = 0

    for current, directory_names, file_names in _bounded_walk(root, warnings, budget):
        current_path = Path(current).resolve(strict=False)
        if not _is_within(current_path, root) or _inside_any(current_path, vaults):
            directory_names[:] = []
            continue

        kept: list[str] = []
        for name in directory_names:
            child = Path(current, name)
            if name in _IGNORED_DIRECTORY_NAMES or name == ".obsidian" or child.is_symlink():
                continue
            child_resolved = child.resolve(strict=False)
            if _is_within(child_resolved, root) and not _inside_any(child_resolved, vaults):
                kept.append(name)
        directory_names[:] = kept

        count = 0
        for name in file_names:
            if not name.casefold().endswith(".pdf"):
                continue
            pdf_path = Path(current, name)
            if pdf_path.is_symlink():
                continue
            count += 1
        if not count:
            continue

        direct_counts[current_path] += count
        total += count
        ancestor = current_path
        while True:
            recursive_counts[ancestor] += count
            if ancestor == root:
                break
            ancestor = ancestor.parent

    return dict(direct_counts), dict(recursive_counts), total


def _candidate_directories(
    root: Path, direct_counts: dict[Path, int], recursive_counts: dict[Path, int]
) -> set[Path]:
    candidates: set[Path] = set()

    if direct_counts.get(root, 0):
        candidates.add(root)

    # Every PDF-bearing top-level branch is visible to the user even when its
    # name is opaque.  This is what makes two independent shelves ambiguous
    # instead of silently choosing the larger one.
    for child, count in recursive_counts.items():
        if child.parent == root and count:
            candidates.add(child)

    hinted = [path for path, count in recursive_counts.items() if count and _name_hint(path)[0]]
    for path in sorted(hinted, key=lambda item: len(item.relative_to(root).parts)):
        if path == root:
            candidates.add(path)
            continue
        hinted_ancestor = next(
            (candidate for candidate in candidates if candidate != path and _is_within(path, candidate) and _name_hint(candidate)[0]),
            None,
        )
        if hinted_ancestor is not None:
            continue

        # Replace an opaque container when the named descendant accounts for
        # every PDF in that container; otherwise expose both for confirmation.
        for ancestor in list(candidates):
            if (
                ancestor != root
                and _is_within(path, ancestor)
                and not _name_hint(ancestor)[0]
                and recursive_counts.get(ancestor) == recursive_counts.get(path)
            ):
                candidates.remove(ancestor)
        candidates.add(path)

    return candidates


def discover_sources(knowledge_root: os.PathLike[str] | str, *, max_directories: int = 5000, max_entries: int = 50000) -> dict[str, object]:
    """Return JSON-serializable Vault and PDF-library candidates under *root*.

    A candidate is evidence only.  ``confirmation_required`` is always true so
    callers cannot treat an apparently unique result as permission to persist
    it.
    """

    budget = _ScanBudget(max_directories, max_entries)
    root = _resolved_directory(knowledge_root)
    warnings: list[str] = []
    vaults = _find_vaults(root, warnings, budget)
    vault_scan_complete = not budget.incomplete
    if vault_scan_complete:
        direct_counts, recursive_counts, total_pdfs = _scan_pdfs(root, vaults, warnings, budget)
    else:
        # Unknown Vault subtrees must not be reported as PDF-library candidates.
        direct_counts, recursive_counts, total_pdfs = {}, {}, 0
    scan_complete = not budget.incomplete
    library_paths = _candidate_directories(root, direct_counts, recursive_counts)

    vault_candidates = [
        {
            "kind": "obsidian-vault",
            "path": str(path),
            "relative_path": path.relative_to(root).as_posix() or ".",
        }
        for path in vaults
    ]

    unsorted_libraries: list[dict[str, object]] = []
    for path in library_paths:
        name_score, name_reason = _name_hint(path)
        depth = len(path.relative_to(root).parts)
        pdf_count = recursive_counts.get(path, 0)
        direct_count = direct_counts.get(path, 0)
        score = name_score + pdf_count * 100 + direct_count * 20 + (2_000 if depth == 1 else 0) - depth * 10
        reasons = [f"contains-{pdf_count}-pdfs"]
        if name_reason:
            reasons.insert(0, name_reason)
        if direct_count:
            reasons.append(f"{direct_count}-pdfs-directly-in-directory")
        unsorted_libraries.append(
            {
                "kind": "pdf-library",
                "path": str(path),
                "relative_path": path.relative_to(root).as_posix() or ".",
                "pdf_count": pdf_count,
                "direct_pdf_count": direct_count,
                "score": score,
                "reasons": reasons,
            }
        )

    library_candidates = sorted(
        unsorted_libraries,
        key=lambda item: (-int(item["score"]), str(item["relative_path"]).casefold()),
    )
    for rank, candidate in enumerate(library_candidates, start=1):
        candidate["rank"] = rank

    return {
        "knowledge_root": str(root),
        "confirmation_required": True,
        "vault_candidates": vault_candidates,
        "library_candidates": library_candidates,
        "counts": {
            "vault_candidates": len(vault_candidates),
            "library_candidates": len(library_candidates),
            "pdf_files_excluding_vaults": total_pdfs,
        },
        "ambiguous": {
            "vault": not scan_complete or len(vault_candidates) != 1,
            "library": not scan_complete or len(library_candidates) != 1,
        },
        "warnings": sorted(set(warnings)),
        "scan_complete": scan_complete,
        "counts_complete": scan_complete,
        "scan": {"directories": budget.directories, "entries": budget.entries,
                 "max_directories": max_directories, "max_entries": max_entries},
        "stop_reason": "complete" if scan_complete else "scan_incomplete",
    }


__all__ = ["DiscoveryError", "discover_sources"]

"""Derive a local map and one verified handoff from a stable project snapshot."""
from __future__ import annotations

import crs_temp

import ctypes
import os
from pathlib import Path
import sys
import tempfile
import time

from crs_model import CRSError, canonical
from crs_store import ordinary, file_digest
from crs_exchange import export_bundle, render_map


def _publish(source, destination):
    """Rename a directory without replacing even an empty concurrent destination."""
    if os.name == 'nt':
        os.rename(source, destination)
        return
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform.startswith('linux') and hasattr(libc, 'renameat2'):
        rename = libc.renameat2
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        result = rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1)
    elif sys.platform == 'darwin' and hasattr(libc, 'renamex_np'):
        rename = libc.renamex_np
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        result = rename(os.fsencode(source), os.fsencode(destination), 4)
    else:
        raise CRSError('finish_atomic_publish_unavailable', 'This platform lacks non-replacing directory publication; use map and export separately.')
    if result:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(destination))


def finish(store, output, max_asset_bytes=460000000, expected_head=None, formula_replacements=None, *, contract=None, plan=None, expected_plan=None):
    from crs_exchange import _delivery_receipt
    recovery_output = [Path(output).absolute() / 'reviewed-handoff.zip']
    try:
        with store._lock():
            return _finish_locked(store, output, max_asset_bytes, expected_head,
                                  formula_replacements, contract=contract, plan=plan, expected_plan=expected_plan, _recovery_output=recovery_output)
    except Exception as error:
        error.delivery_receipt = _delivery_receipt(Path(output).absolute() / Path(recovery_output[0]).name, expected_plan=expected_plan)
        raise


def _finish_locked(store, output, max_asset_bytes=460000000, expected_head=None, formula_replacements=None, *, contract=None, plan=None, expected_plan=None, _recovery_output=None):
    """Read-only project finish. Does not review or execute research evidence."""
    started = time.monotonic()
    from crs_unique import external_target, require_unique, project_scope
    output = external_target(store, output)
    project_content = {'performed': False, 'scope': 'whole_project_history', 'reason': 'Separate explicitly requested audit; not an ordinary delivery prerequisite.'}
    ordinary(output)
    if any(p.is_symlink() and not crs_temp.system_alias(p) for p in (output, *output.parents)):
        raise CRSError('finish_link_path', 'Use ordinary output directories.')
    if not output.parent.is_dir():
        raise CRSError('finish_output_parent', 'The output parent directory must already exist.')
    output = output.parent.resolve(strict=True) / output.name
    if output.exists():
        raise CRSError('finish_output_exists', 'Choose a new output directory; existing output is never replaced.')
    if output.is_relative_to(store.root) or store.root.is_relative_to(output):
        raise CRSError('finish_output_overlap', 'Write derived delivery files beside the formal research project.')
    head = store.head()
    if expected_head is not None and head != expected_head:
        raise CRSError('head_conflict', 'The project changed since the expected snapshot.')

    def unchanged():
        if store.head() != head:
            raise CRSError('finish_snapshot_changed', 'The project changed during finish. Retry on its current snapshot; no delivery was published.')

    audit = {'performed': False, 'scope': 'whole_project_history',
             'reason': 'The exported reviewed projection is verified below; unrelated history is not audited.'}
    snapshot = store.snapshot(head)
    records, reviews = store.records(snapshot), store.reviews(snapshot)
    unchanged()
    audit_seconds = time.monotonic() - started
    links = {}

    def object_link(sha):
        if sha not in links:
            try:
                path = store.locate(sha)
            except CRSError as error:
                if error.code != 'asset_missing':
                    raise
                links[sha] = None
            else:
                try:
                    links[sha] = Path(os.path.relpath(path, output)).as_posix()
                except ValueError:
                    links[sha] = path.as_posix()
        return links[sha]

    with crs_temp.TemporaryDirectory(prefix='.crs-finish-', dir=output.parent) as temporary:
        scratch = Path(temporary)
        export_started = time.monotonic()
        # export_bundle verifies its exact temporary ZIP before non-overwriting
        # publication. Reuse that bound result; do not verify the same ZIP twice.
        from crs_exchange import _export_bundle_locked, _delivery_receipt
        exported = _export_bundle_locked(store, scratch / 'reviewed-handoff.zip', max_asset_bytes, contract=contract, plan=plan, expected_plan=expected_plan, _recovery_output=_recovery_output)
        unchanged()
        if exported['source_snapshot'] != head:
            raise CRSError('finish_snapshot_changed', 'Export did not use the captured project snapshot.')
        shareable=Path(exported['output']).name
        exported = dict(exported, output=str(output / shareable))
        exported['delivery_receipt'] = _delivery_receipt(output / shareable, exported, terminal='success')
        export_seconds = time.monotonic() - export_started
        map_started = time.monotonic()
        from crs_formula import Presentation
        presentation = Presentation(formula_replacements)
        body = render_map(records, reviews, snapshot['selected'], snapshot['title'],
                          presentation=presentation, object_path=object_link, origins=snapshot['origins'],
                          excluded_records=snapshot.get('excluded_records', []),
                          objective=snapshot['objective'], objective_binding=snapshot.get('objective_binding'))
        from crs_obsidian import write_map
        map_result = write_map(store, scratch / 'map.md', body, head, links)
        map_seconds = time.monotonic() - map_started
        readme = ('# Local research finish\n\n'
                  'This directory is a local archive view. map.md includes all local research history, '
                  'including records that have not been reviewed, and links to the original project.\n\n'
                  'Share only the reviewed-handoff.zip or complete reviewed-handoff.parts set named in the receipt. Its own README, map, evidence inventory and portable '
                  'tools describe the reviewed projection and its limitations. Unavailable or large '
                  'referenced evidence remains explicitly identified there.\n\n'
                  'receipt.json binds the captured snapshot, explicit audit scope and exact export verification. '
                  'Finish does not review research claims, authenticate contributors or replay evidence. '
                  'The research project remains the durable source; this local map is not a backup.\n\n'
                  'Only scratch created by this invocation is cleaned on ordinary completion or failure. '
                  'Existing reproduction directories must be closed by their owner after preserving evidence. '
                  'An uncatchable process termination can leave owned scratch; inspect it before removal.\n')
        (scratch / 'README.md').write_text(readme, encoding='utf-8', newline='\n')
        receipt = {
            'schema': 'crs-finish/v1', 'snapshot': head, 'status': 'finished',
            'project_mutated': False, 'evidence_replayed': False,
            'formula_check': map_result['formula_check'],
            'audit': audit, 'export': exported, 'delivery_receipt':exported['delivery_receipt'], 'export_verification_reused': True,
            'local_map_includes_unreviewed': True, 'local_map_asset_bytes_verified': False, 'shareable_file': shareable,
            'file_hashes': {name: file_digest(scratch / name)[0] for name in ('map.md', 'README.md')},
            'timing_seconds': {'audit': audit_seconds, 'map': map_seconds, 'export_and_verify': export_seconds},
            'cleanup_scope': 'Only this invocation owns its temporary directory; successful rename leaves no scratch path.',
        }
        artifact=scratch/shareable
        if artifact.is_file():receipt['file_hashes'][shareable]=file_digest(artifact)[0]
        else:receipt['file_hashes'][shareable+'/parts.json']=file_digest(artifact/'parts.json')[0]
        receipt['project_content_uniqueness'] = project_content
        (scratch / 'receipt.json').write_bytes(canonical(receipt))
        require_unique(scratch)
        # The public finish entry holds the same store lock throughout.
        from contextlib import nullcontext
        with nullcontext():
            unchanged()
            ordinary(output.parent)
            if output.exists() or output.is_symlink():
                raise CRSError('finish_output_exists', 'Another writer created the destination. Its content is retained.')
            try:
                _publish(scratch, output)
            except OSError as error:
                raise CRSError('finish_publish_failed', 'Could not publish without replacing an existing destination.', {'errno': error.errno}) from error
    return {**receipt, 'output': str(output), 'temporary_directory_removed': not scratch.exists(),
            'elapsed_seconds': time.monotonic() - started}

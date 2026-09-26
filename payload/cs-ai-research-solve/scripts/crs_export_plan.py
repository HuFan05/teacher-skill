"""Explicit material coverage and immutable exchange plans.

Declarations describe intended use; they do not grant research review.
Verification and planning never execute supplied research code.
"""
from __future__ import annotations

import crs_temp

import ast
import re
import sys
from pathlib import Path

from crs_model import CRSError, canonical, digest

CONTRACT_SCHEMA = 'crs-delivery-contract/v1'
PLAN_SCHEMA = 'crs-delivery-plan/v1'
SCOPES = {'reviewed_exchange', 'offline_reproduction'}
ROLES = {'code', 'configuration', 'input', 'certificate', 'optional'}
HASH = re.compile(r'^[0-9a-f]{64}$')
IDENTIFIER = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$')


def fail(code, message, details=None):
    raise CRSError(code, message, details or {})


def closed(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
        fail('delivery_contract_invalid', 'Delivery metadata has missing or unknown fields.', {'field': label})
    return value


def texts(values, label, nonempty=False):
    if (not isinstance(values, list) or (nonempty and not values)
            or not all(isinstance(x, str) and x.strip() for x in values)):
        fail('delivery_contract_invalid', 'Expected an explicit string list.', {'field': label})


def safe_name(value):
    from crs_exchange import _filename
    if not isinstance(value, str):
        fail('delivery_contract_invalid', 'Material names must be portable relative filenames.')
    _filename(value)
    if value.casefold().startswith(('.crs-', 'tools/')) or value.casefold() in {'stdout', 'stderr'}:
        fail('delivery_contract_invalid', 'Material name conflicts with a reserved delivery name.')
    return value


def validate_contract(contract, records=None, specs=None):
    """Validate declarations without inventing missing dependencies or evidence."""
    from crs_exchange import _portable_text
    closed(contract, {'schema', 'scope', 'groups', 'limitations'}, 'contract')
    if contract['schema'] != CONTRACT_SCHEMA or contract['scope'] not in SCOPES:
        fail('delivery_contract_invalid', 'Unsupported delivery contract or scope.')
    texts(contract['limitations'], 'limitations')
    if not isinstance(contract['groups'], list) or len(contract['groups']) > 1000:
        fail('delivery_contract_invalid', 'Material groups must be a bounded list.')
    if contract['scope'] == 'offline_reproduction' and not contract['groups']:
        fail('delivery_groups_required', 'Offline reproduction needs explicit material groups.')
    ids = set()
    for group in contract['groups']:
        closed(group, {'id', 'records', 'files', 'entrypoint', 'argv', 'expected_outputs',
                       'environment', 'unresolved_dependencies'}, 'group')
        gid = group['id']
        if not isinstance(gid, str) or not IDENTIFIER.fullmatch(gid) or gid in ids:
            fail('delivery_contract_invalid', 'Material group identifiers must be unique.')
        ids.add(gid)
        texts(group['records'], 'records', nonempty=True)
        if len(set(group['records'])) != len(group['records']) or any(not HASH.fullmatch(x) for x in group['records']):
            fail('delivery_contract_invalid', 'Group records must be unique exact revisions.')
        if records is not None and set(group['records']) - set(records):
            fail('delivery_record_scope', 'A material group references research outside the reviewed export selection.', {'group': gid})
        if not isinstance(group['files'], dict) or not group['files'] or len(group['files']) > 20000:
            fail('delivery_contract_invalid', 'Each group needs a bounded explicit material map.')
        names = set()
        owners = ({a['sha256'] for revision in group['records'] for a in records[revision]['evidence']}
                  if records is not None else None)
        for name, member in group['files'].items():
            safe_name(name)
            if name.casefold() in names:
                fail('delivery_contract_invalid', 'Case-insensitive material name collision.', {'group': gid})
            names.add(name.casefold())
            closed(member, {'sha256', 'role'}, 'member')
            if not isinstance(member['sha256'], str) or not HASH.fullmatch(member['sha256']) or member['role'] not in ROLES:
                fail('delivery_contract_invalid', 'Invalid material identity or role.', {'group': gid})
            if specs is not None and member['sha256'] not in specs:
                fail('delivery_unreviewed_asset', 'Material declarations cannot introduce unreviewed extra evidence.', {'group': gid, 'sha256': member['sha256']})
            if records is not None:
                if member['sha256'] not in owners:
                    fail('delivery_material_binding', 'Each material must be evidence of a declared group record.', {'group': gid, 'sha256': member['sha256']})
        # No member may require a directory where another member is a file.
        for name in names:
            parents = Path(name).parents
            if any(p.as_posix().casefold() in names for p in parents if p.as_posix() != '.'):
                fail('delivery_contract_invalid', 'Material file/directory collision.', {'group': gid})
        if group['entrypoint'] is not None:
            safe_name(group['entrypoint'])
            if group['entrypoint'] not in group['files'] or group['files'][group['entrypoint']]['role'] != 'code':
                fail('delivery_entrypoint_missing', 'The entrypoint must be an explicitly supplied code member.', {'group': gid})
        texts(group['argv'], 'argv')
        if bool(group['argv']) != bool(group['entrypoint']):
            fail('delivery_contract_invalid', 'Entrypoint and invocation must be declared together.', {'group': gid})
        if group['argv'] and group['entrypoint'] not in group['argv']:
            fail('delivery_contract_invalid', 'Invocation must explicitly name its declared entrypoint.', {'group': gid})
        if not isinstance(group['expected_outputs'], dict):
            fail('delivery_contract_invalid', 'Expected outputs must map relative names to byte identities.')
        for name, sha in group['expected_outputs'].items():
            safe_name(name)
            if name.casefold() in names or not isinstance(sha, str) or not HASH.fullmatch(sha):
                fail('delivery_contract_invalid', 'Expected outputs must be new files with exact byte identities.', {'group': gid})
        combined = list(group['files']) + list(group['expected_outputs'])
        folded = [name.casefold() for name in combined]
        if len(set(folded)) != len(folded) or any(
                parent.as_posix().casefold() in folded
                for name in folded for parent in Path(name).parents if parent.as_posix() != '.'):
            fail('delivery_contract_invalid', 'Input and output files must share a collision-free portable namespace.', {'group': gid})
        environment = closed(group['environment'], {'runtime', 'version', 'platform', 'packages'}, 'environment')
        if any(not isinstance(environment[k], str) or not environment[k].strip() for k in ('runtime', 'version', 'platform')):
            fail('delivery_contract_invalid', 'Runtime, version and platform must be explicit.')
        texts(environment['packages'], 'environment.packages')
        texts(group['unresolved_dependencies'], 'unresolved_dependencies')
    _portable_text(contract)
    return contract


def mandatory_assets(contract):
    if contract is None:
        return set()
    return {item['sha256'] for group in contract['groups'] for item in group['files'].values() if item['role'] != 'optional'}


def material_status(contract, assets, records=None, paths=None):
    """Availability is recomputed from package members, never trusted from a label."""
    if contract is None:
        return {'scope': 'reviewed_exchange', 'groups_declared': False, 'groups': [],
                'material_groups_complete': False, 'replay_performed': False,
                'offline_reproduction_verified': False, 'research_authority_added': False}
    validate_contract(contract, records)
    rows = []
    for group in contract['groups']:
        missing = []
        for name, member in group['files'].items():
            asset = assets.get(member['sha256'])
            if member['role'] != 'optional' and (asset is None or asset.get('availability') != 'included'):
                missing.append({'name': name, 'sha256': member['sha256']})
        unknown = list(group['unresolved_dependencies'])
        if not group['argv']:
            unknown.append('No executable reproduction route is declared.')
        if not group['expected_outputs']:
            unknown.append('No exact result check is declared; process exit alone is insufficient.')
        issues = python_dependency_findings(group, paths) if paths is not None and group['environment']['runtime'].casefold() == 'python' else []
        rows.append({'id': group['id'], 'missing': missing, 'unknown': unknown,
                     'required_materials_included':not missing,
                     'dependency_findings': issues,
                     'dependency_analysis_performed': paths is not None and group['environment']['runtime'].casefold() == 'python',
                     'dependency_analysis_scope': 'Declared Python sources only; static inspection is not a complete dynamic dependency proof.',
                     'declared_materials_complete': not missing and not unknown and not issues,
                     'environment': group['environment'], 'replay_performed': False})
    return {'scope': contract['scope'], 'groups_declared': True, 'groups': rows,
            'required_materials_included':bool(rows) and all(r['required_materials_included'] for r in rows),
            'material_groups_complete': bool(rows) and all(r['declared_materials_complete'] for r in rows),
            'replay_performed': False, 'offline_reproduction_verified': False,
            'research_authority_added': False}


def python_dependency_findings(group, paths):
    """Conservative local leads, not proof of complete dynamic dependency closure.

Only explicitly listed Python members are parsed. No import is executed and no
unlisted local directory is searched. Declared packages remain environment
requirements, not bundled byte evidence.
"""
    from crs_exchange import _ArchiveMember

    findings = []
    modules = set()
    included_names = {name for name, member in group['files'].items() if member['sha256'] in paths}
    for name in included_names:
        p = Path(name)
        if p.suffix == '.py':
            parts = p.with_suffix('').parts
            modules.add('.'.join(parts[:-1] if parts[-1] == '__init__' else parts))
    environment_modules = {p.split('==', 1)[0].split('>=', 1)[0].replace('-', '_') for p in group['environment']['packages']}
    standard = set(getattr(sys, 'stdlib_module_names', ())) | set(sys.builtin_module_names)
    for name, member in group['files'].items():
        if Path(name).suffix != '.py' or member['sha256'] not in paths:
            continue
        material = paths[member['sha256']]
        source_path = material if isinstance(material, _ArchiveMember) else Path(material)
        if source_path.stat().st_size > 8 * 1024 * 1024:
            findings.append({'member': name, 'code': 'python_source_analysis_limit'})
            continue
        try:
            source = source_path.read_text(encoding='utf-8-sig')
            tree = ast.parse(source)
        except (UnicodeError, SyntaxError):
            findings.append({'member': name, 'code': 'python_source_unparsed'})
            continue
        path_constructors = {'Path'}
        for imported_node in ast.walk(tree):
            if isinstance(imported_node, ast.ImportFrom) and imported_node.module == 'pathlib':
                path_constructors.update(alias.asname or alias.name for alias in imported_node.names if alias.name == 'Path')
        declared_paths = included_names | set(group['expected_outputs'])
        for node in ast.walk(tree):
            imported = []
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    parents = Path(name).parent.parts
                    base = parents[:len(parents) - node.level + 1]
                    imported = ['.'.join((*base, node.module or alias.name)) for alias in node.names]
                elif node.module:
                    imported = [node.module]
            elif isinstance(node, ast.Call):
                func = node.func
                filesystem_call = (isinstance(func, ast.Name) and func.id in path_constructors | {'open'}) or (isinstance(func, ast.Attribute) and func.attr == 'Path')
                if filesystem_call:
                    if len(node.args) >= 1 and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                        candidate = node.args[0].value.replace('\\', '/')
                        if candidate not in declared_paths:
                            findings.append({'member': name, 'line': node.lineno, 'code': 'file_dependency_undeclared'})
                    else:
                        findings.append({'member': name, 'line': node.lineno, 'code': 'dynamic_file_dependency_unresolved'})
                if ((isinstance(func, ast.Name) and func.id in {'eval', 'exec'})
                        or (isinstance(func, ast.Attribute) and func.attr in {'glob', 'rglob', 'walk', 'listdir', 'scandir'})):
                    findings.append({'member': name, 'line': node.lineno, 'code': 'dynamic_dependency_enumeration_unresolved'})
                dynamic = (isinstance(func, ast.Name) and func.id == '__import__') or (isinstance(func, ast.Attribute) and func.attr == 'import_module')
                if dynamic:
                    if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                        imported = [node.args[0].value]
                    else:
                        findings.append({'member': name, 'line': node.lineno, 'code': 'dynamic_import_unresolved'})
            for module in imported:
                if module in modules or module.split('.')[0] in standard | environment_modules:
                    continue
                if any(m.startswith(module + '.') for m in modules):
                    continue
                findings.append({'member': name, 'line': node.lineno, 'code': 'python_dependency_undeclared', 'module': module})
    return findings


def toolset_sha256():
    from crs_exchange import TOOL_NAMES
    directory = Path(__file__).parent
    return digest(canonical({name: digest((directory / name).read_bytes()) for name in TOOL_NAMES}))


def plan_summary(plan):
    return {'schema': plan['schema'], 'snapshot': plan['source_snapshot'],
            'plan_sha256': digest(canonical(plan)), 'scope': plan['scope'],
            'readiness': plan['readiness'], 'record_count': len(plan['record_hashes']),
            'included_assets': sum(a['include'] for a in plan['assets']),
            'referenced_assets': sum(not a['include'] for a in plan['assets']),
            'finding_count': len(plan['findings']), 'findings_preview': plan['findings'][:20],
            'findings_complete': len(plan['findings']) <= 20,
            'byte_verification_performed': plan['readiness'] == 'ready_for_packaging', 'replay_performed': False}


def create_plan(store, max_asset_bytes=460000000, max_zip_bytes=512000000,
                priorities=None, portable_history=None, contract=None):
    """Choose all material actions before any selected asset byte is consumed.

    Snapshot record/review bytes are necessarily read to establish review scope.
    Content checks for included assets are explicitly pending, not implied by a
    successful metadata plan. Omitted referenced assets are not opened.
    """
    from crs_exchange import (_export_projection, _export_preflight, _evidence_spec,
                              _integer, _object_name)
    from crs_parts import rank_assets, LIMIT
    _integer(max_asset_bytes, 'max_asset_bytes')
    if type(max_zip_bytes) is not int or not 1024 <= max_zip_bytes <= LIMIT:
        fail('export_part_limit', 'ZIP limit must be 1,024..512,000,000 bytes.')
    view = _export_projection(store, portable_history)
    head, snapshot, records, reviews, states, selected, excluded_rows, excluded, review_hashes = view
    preflight = _export_preflight(head, snapshot, records, reviews, states, selected,
                                 excluded_rows, excluded, max_asset_bytes, priorities)
    specs = _evidence_spec(records)
    if contract is not None:
        validate_contract(contract, records, specs)
    reports = {r['report'] for r in reviews}
    required = (set(specs) | reports | {s for r in reviews for s in r['materials']}) - set(excluded)
    structural = set(records) | set(review_hashes)
    forced = reports | mandatory_assets(contract)
    ranking = rank_assets(records, reviews, required, specs, priorities)
    ranking.sort(key=lambda row: row['sha256'] not in forced)
    findings = list(preflight['findings'])
    assets = []
    spent = 0
    locations = None
    for ranked in ranking:
        sha = ranked['sha256']
        if sha in structural:
            continue
        item = specs.get(sha)
        size = item['bytes'] if item else None
        reason = 'selected'
        # A reviewed omission can be decided using metadata without testing the
        # current machine's availability or consuming the referenced bytes.
        has_reference = item is not None and bool(item['summary']) and bool(item['locator'])
        include = size is None or (size <= max_asset_bytes - spent and (sha in forced or size <= max_zip_bytes - 4096))
        if not include:
            reason = 'asset_budget'
        if include or sha in forced or not has_reference:
            try:
                source = store.locate(sha, locations=locations)
                actual_size = source.stat().st_size
                if size is not None and actual_size != size:
                    findings.append({'code': 'exchange_asset_size_conflict', 'sha256': sha})
                size = actual_size if size is None else size
                include = size <= max_asset_bytes - spent and (sha in forced or size <= max_zip_bytes - 4096)
                if not include:
                    reason = 'asset_budget'
            except CRSError as error:
                if error.code != 'asset_missing':
                    raise
                include = False
                reason = 'not_locally_available'
                if size is None:
                    size = 0
        if not include and sha in forced:
            findings.append({'code': 'delivery_required_material_missing' if reason == 'not_locally_available' else 'delivery_required_material_budget', 'sha256': sha, 'bytes': size})
        if not include and not has_reference:
            findings.append({'code': 'exchange_asset_reference_missing', 'sha256': sha})
        assets.append({'sha256': sha, 'bytes': size, 'include': include, 'reason': reason,
                       'required': sha in forced, 'content_verification': 'pending' if include else 'not_requested'})
        if include:
            spent += size
    material = material_status(contract, {a['sha256']: {'availability': 'included' if a['include'] else 'referenced'} for a in assets}, records)
    if contract is not None and contract['scope'] == 'offline_reproduction':
        for group in material['groups']:
            if group['unknown']:
                findings.append({'code': 'delivery_dependencies_unknown', 'group': group['id'], 'unknown_count': len(group['unknown'])})
    plan = {'schema': PLAN_SCHEMA, 'source_snapshot': head, 'toolset_sha256': toolset_sha256(),
            'scope': contract['scope'] if contract else 'reviewed_exchange',
            'record_hashes': sorted(records), 'review_hashes': sorted(review_hashes),
            'assets': assets, 'ranking': ranking, 'contract': contract,
            'portable_history': portable_history, 'priorities': priorities,
            'asset_budget': max_asset_bytes, 'max_zip_bytes': max_zip_bytes,
            'findings': findings, 'readiness': 'metadata_blocked' if findings else 'metadata_checked',
            'material_coverage': material, 'asset_bytes_selected': spent,
            'remaining_checks': ['included asset content and privacy', 'dynamic dependency assessment',
                                 'final archive capacity and byte integrity', 'recipient replay if requested'],
            'review_authority_changed': False}
    if store.head() != head:
        fail('delivery_snapshot_changed', 'Source changed during delivery planning; no execution plan was accepted.')
    return plan, view


def validate_plan(plan):
    keys = {'schema','source_snapshot','toolset_sha256','scope','record_hashes','review_hashes',
            'assets','ranking','contract','portable_history','priorities','asset_budget','max_zip_bytes',
            'findings','readiness','material_coverage','asset_bytes_selected','remaining_checks','review_authority_changed'}
    if 'previous_plan_sha256' in plan:
        keys.add('previous_plan_sha256')
        previous = plan['previous_plan_sha256']
        if previous is not None and (not isinstance(previous, str) or not HASH.fullmatch(previous)):
            fail('delivery_plan_invalid', 'Previous-plan identity must be a SHA-256 value or null.')
    closed(plan, keys, 'plan')
    if plan['schema'] != PLAN_SCHEMA or plan['scope'] not in SCOPES:
        fail('delivery_plan_invalid', 'Unsupported delivery plan.')
    for key in ('source_snapshot', 'toolset_sha256'):
        if not isinstance(plan[key], str) or not HASH.fullmatch(plan[key]):
            fail('delivery_plan_invalid', 'Plan identity fields must be SHA-256 values.')
    if plan['contract'] is not None:
        validate_contract(plan['contract'])
    if plan['scope'] != (plan['contract']['scope'] if plan['contract'] else 'reviewed_exchange'):
        fail('delivery_plan_invalid', 'Requested scope and material contract disagree.')
    if plan['review_authority_changed'] is not False:
        fail('delivery_plan_invalid', 'Delivery cannot change review authority.')
    from crs_exchange import _integer, MAX_REFERENCED_BYTES
    from crs_parts import LIMIT
    _integer(plan['asset_budget'], 'plan.asset_budget')
    _integer(plan['asset_bytes_selected'], 'plan.asset_bytes_selected')
    if type(plan['max_zip_bytes']) is not int or not 1024 <= plan['max_zip_bytes'] <= LIMIT:
        fail('delivery_plan_invalid', 'Invalid planned archive capacity.')
    for key in ('record_hashes', 'review_hashes'):
        values = plan[key]
        if not isinstance(values, list) or any(not isinstance(x, str) or not HASH.fullmatch(x) for x in values) or values != sorted(set(values)):
            fail('delivery_plan_invalid', 'Plan research identities must be sorted unique hashes.')
    if not isinstance(plan['findings'], list) or not all(isinstance(x, dict) for x in plan['findings']):
        fail('delivery_plan_invalid', 'Plan findings must be explicit diagnostic rows.')
    if plan['readiness'] not in {'metadata_blocked', 'metadata_checked', 'content_blocked', 'ready_for_packaging'}:
        fail('delivery_plan_invalid', 'Unknown plan readiness.')
    texts(plan['remaining_checks'], 'remaining_checks')
    if not isinstance(plan['assets'], list):
        fail('delivery_plan_invalid', 'Plan assets must be an explicit list.')
    seen = set()
    for asset in plan['assets']:
        closed(asset, {'sha256','bytes','include','reason','required','content_verification'}, 'plan.asset')
        if (not isinstance(asset['sha256'], str) or not HASH.fullmatch(asset['sha256']) or asset['sha256'] in seen
                or type(asset['bytes']) is not int or not 0 <= asset['bytes'] <= MAX_REFERENCED_BYTES or type(asset['include']) is not bool
                or type(asset['required']) is not bool):
            fail('delivery_plan_invalid', 'Invalid or repeated planned asset.')
        seen.add(asset['sha256'])
        allowed = {'pending', 'verified_at_planning'} if asset['include'] else {'not_requested', 'reference_after_privacy_check', 'reference_after_container_check'}
        if asset['content_verification'] not in allowed:
            fail('delivery_plan_invalid', 'Invalid planned asset verification status.')
    selected_bytes = sum(a['bytes'] for a in plan['assets'] if a['include'])
    if selected_bytes != plan['asset_bytes_selected'] or selected_bytes > plan['asset_budget']:
        fail('delivery_plan_invalid', 'Plan byte totals and budget disagree.')
    if plan['readiness'] == 'ready_for_packaging' and (plan['findings'] or any(a['include'] and a['content_verification'] != 'verified_at_planning' for a in plan['assets'])):
        fail('delivery_plan_invalid', 'A ready plan requires completed selected-content checks.')
    return plan


def prepare_recursive_materials(plan, paths, specs):
    """Check selected immutable materials before costly final packaging.

    An optional rejected container contributes to consumed scan limits but never
    to the accepted-content index. Required conflicts cannot become references.
    Final generated views/manifest/namespace still require combined verification.
    """
    import tempfile, zipfile, tarfile, lzma
    from crs_unique import Scanner
    from crs_exchange import TOOL_NAMES, README
    occupied = {h: 'object' for h in plan['record_hashes'] + plan['review_hashes']}
    occupied.update({digest((Path(__file__).parent / n).read_bytes()): 'tool' for n in TOOL_NAMES})
    occupied[digest(README.encode('utf-8'))] = 'view'
    consumed_bytes = consumed_members = 0
    findings = []
    with crs_temp.TemporaryDirectory(prefix='crs-plan-containers-') as scratch:
        for item in sorted(plan['assets'], key=lambda a: not a['required']):
            sha = item['sha256']
            if not item['include'] or sha not in paths:
                continue
            scan = Scanner(max_bytes=max(1, 64*1024**3-consumed_bytes),
                           max_members=max(1, 1000000-consumed_members), max_depth=16)
            issue = None
            try:
                if consumed_bytes >= 64*1024**3 or consumed_members >= 1000000:
                    fail('content_resource_limit', 'Recursive planning scan budget is exhausted.')
                scan.file(paths[sha], sha, scratch)
            except (CRSError, OSError, ValueError, EOFError, RuntimeError, zipfile.BadZipFile, tarfile.TarError, lzma.LZMAError) as error:
                issue = getattr(error, 'code', 'content_container_invalid')
            consumed_bytes += scan.bytes
            consumed_members += scan.members
            conflict = bool(scan.duplicates) or any(
                h in occupied and not (h == sha and occupied[h] == 'object')
                for h in scan.hashes)
            if issue or conflict:
                spec = specs.get(sha)
                if not item['required'] and spec and spec['summary'] and spec['locator']:
                    item.update(include=False, reason=issue or 'recursive_content_duplicate',
                                content_verification='reference_after_container_check')
                    paths.pop(sha, None)
                else:
                    findings.append({'code':'delivery_recursive_material_conflict', 'sha256':sha,
                                     'cause':issue or 'recursive_content_duplicate'})
                continue
            for h in scan.hashes:
                occupied.setdefault(h, 'material')
    return findings


def prepare_plan(store, plan, view):
    """Finish content selection before publishing an executable plan.

    This runs only after cheap metadata blockers are collected. It checks all
    chosen material, records independent content failures together, and never
    changes research review. Exact source bytes are rechecked at packing.
    """
    from crs_exchange import _evidence_spec, _portable_file
    if plan['findings']:
        return plan
    plan = __import__('copy').deepcopy(plan)
    specs = _evidence_spec(view[2])
    paths = {}
    findings = []
    for item in plan['assets']:
        if not item['include']:
            continue
        sha = item['sha256']
        try:
            path = store.resolve(sha)
            if path.stat().st_size != item['bytes']:
                fail('exchange_asset_size_conflict', 'Material changed after metadata planning.')
            try:
                _portable_file(path)
            except CRSError as error:
                spec = specs.get(sha)
                if error.code == 'exchange_local_path' and not item['required'] and spec and spec['summary'] and spec['locator']:
                    item.update(include=False, reason='machine_local_paths', content_verification='reference_after_privacy_check')
                    continue
                raise
            paths[sha] = path
            item['content_verification'] = 'verified_at_planning'
        except CRSError as error:
            findings.append({'code': error.code, 'sha256': sha})
    findings.extend(prepare_recursive_materials(plan, paths, specs))
    contract = plan['contract']
    plan['material_coverage'] = material_status(contract, {a['sha256']: {'availability': 'included' if a['include'] else 'referenced'} for a in plan['assets']}, view[2], paths)
    if contract and contract['scope'] == 'offline_reproduction':
        for row in plan['material_coverage']['groups']:
            findings.extend(dict(issue, group=row['id']) for issue in row['dependency_findings'])
    plan['asset_bytes_selected'] = sum(a['bytes'] for a in plan['assets'] if a['include'])
    plan['findings'] = findings
    plan['readiness'] = 'content_blocked' if findings else 'ready_for_packaging'
    if store.head() != plan['source_snapshot']:
        fail('delivery_snapshot_changed', 'Source changed during content preparation; no plan was published.')
    return plan


def write_plan(store, output, **options):
    from crs_unique import external_target
    from crs_exchange import _no_links
    output = external_target(store, Path(output).absolute())
    _no_links(output)
    if output.suffix.lower() != '.json' or not output.parent.is_dir():
        fail('delivery_plan_output', 'Use a new external JSON file in an existing directory.')
    plan, view = create_plan(store, **options)
    plan = prepare_plan(store, plan, view)
    with output.open('xb') as handle:
        handle.write(canonical(plan))
    return {**plan_summary(plan), 'ok': plan['readiness'] == 'ready_for_packaging', 'report_written': True, 'output': str(output)}


def refresh_plan(store, output, previous, expected_previous):
    """Carry declared delivery choices into a newly verified snapshot plan.

    Previous byte/review/readiness results are never reused as current evidence.
    No source object, review or original plan is mutated. A changed or foreign
    objective/record context is checked by the ordinary source projection.
    """
    import copy
    from crs_unique import external_target
    from crs_exchange import _no_links
    validate_plan(previous)
    previous_sha = digest(canonical(previous))
    if expected_previous != previous_sha:
        fail('delivery_plan_hash', 'Refresh requires the exact prior plan SHA-256.')
    prior_snapshot = store.snapshot(previous['source_snapshot'])
    current_snapshot = store.snapshot()
    if prior_snapshot['project_id'] != current_snapshot['project_id']:
        fail('delivery_plan_foreign_project', 'A previous plan must belong to this project identity.')
    history = copy.deepcopy(previous['portable_history'])
    if history is not None:
        history['snapshot'] = store.head()
    output = external_target(store, Path(output).absolute())
    _no_links(output)
    if output.suffix.lower() != '.json' or not output.parent.is_dir():
        fail('delivery_plan_output', 'Use a new external JSON file in an existing directory.')
    plan, view = create_plan(store, previous['asset_budget'], previous['max_zip_bytes'],
                             copy.deepcopy(previous['priorities']), history,
                             copy.deepcopy(previous['contract']))
    plan = prepare_plan(store, plan, view)
    plan['previous_plan_sha256'] = previous_sha
    with output.open('xb') as handle:
        handle.write(canonical(plan))
    return {**plan_summary(plan), 'ok':plan['readiness'] == 'ready_for_packaging',
            'report_written':True, 'output':str(output),
            'previous_plan_sha256':previous_sha,
            'source_snapshot_changed':plan['source_snapshot'] != previous['source_snapshot'],
            'added_record_count':len(set(plan['record_hashes']) - set(previous['record_hashes'])),
            'removed_record_count':len(set(previous['record_hashes']) - set(plan['record_hashes'])),
            'prior_verification_reused':False, 'source_mutated':False}

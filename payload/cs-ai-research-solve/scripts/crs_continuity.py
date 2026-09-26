"""Snapshot-bound continuation screening and disposition checks, not claim verification."""
from __future__ import annotations

from collections import defaultdict, deque
import heapq
from pathlib import Path
import re

from crs_model import CRSError, canonical, digest, validate_record

RELATIONS = {'previous', 'extends', 'premise', 'input', 'term', 'background', 'corrects', 'supersedes'}
FIELDS = ('title', 'statement', 'scope', 'action', 'feedback', 'assumptions', 'limitations', 'reopen')
PLAN_KEYS = {'schema', 'project_id', 'snapshot', 'since', 'scope', 'seeds', 'incoming', 'input_sha256', 'screening_limit', 'record_revisions', 'pairs', 'screening'}
PAIR_KEYS = {'key', 'a', 'b', 'signals', 'existing_links'}
RECORD_DECISION_KEYS = {'revision', 'disposition', 'reason', 'read_revisions'}
PAIR_DECISION_KEYS = {'a', 'b', 'decision', 'successor', 'predecessor', 'relation', 'stored_revision', 'basis', 'reason', 'evidence_revisions'}


def fail(code, message, details=None):
    raise CRSError('continuity_' + code, message, details or {})


def exact(value, keys, label):
    if not isinstance(value, dict) or set(value) != keys:
        fail('schema_invalid', 'Use the closed continuation schema.', {'object': label})


def text(value, label):
    if not isinstance(value, str) or not value.strip():
        fail('reason_missing', 'Supply a substantive nonempty explanation.', {'field': label})


def unique(value, label):
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value) or len(set(value)) != len(value):
        fail('list_invalid', 'Expected a unique list of revision strings.', {'field': label})
    return set(value)


def pair_key(a, b):
    return digest(canonical(sorted((a, b))))


def links(records):
    result = []
    for successor, row in sorted(records.items()):
        for dep in row['dependencies']:
            result.append({'successor': successor, 'predecessor': dep['revision'],
                           'relation': dep['relation'], 'reason': dep['reason']})
        if row['previous']:
            result.append({'successor': successor, 'predecessor': row['previous'],
                           'relation': 'previous', 'reason': 'Explicit revision history of the same research identity.'})
        correction = row['correction']
        if correction and not any(d['revision'] == correction['target'] and d['relation'] == 'corrects' for d in row['dependencies']):
            result.append({'successor': successor, 'predecessor': correction['target'],
                           'relation': 'corrects', 'reason': correction['reason']})
    return result


def revision_order(rows, known):
    """Order explicit revision chains without choosing a branch by arrival order."""
    incoming = {digest(canonical(row)): row for row in rows}
    union = {**known, **incoming}
    children, degree, roots = defaultdict(list), {}, defaultdict(list)
    for sha, row in incoming.items():
        prior = row['previous']
        if prior in union and union[prior]['id'] != row['id']:
            fail('revision_identity_mismatch', 'The previous revision belongs to another identity.')
        degree[sha] = int(prior in incoming)
        if prior:
            children[prior].append(sha)
        if prior not in incoming:
            roots[row['id']].append(sha)
    ready = sorted(sha for sha in incoming if not degree[sha])
    heapq.heapify(ready)
    ordered = []
    while ready:
        sha = heapq.heappop(ready)
        ordered.append(incoming[sha])
        for child in children[sha]:
            degree[child] -= 1
            if not degree[child]:
                heapq.heappush(ready, child)
    if len(ordered) != len(incoming):
        fail('revision_cycle', 'Revision history contains a cycle; inspect the records before ingestion.')
    forks = {prior for prior, group in children.items() if len(group) > 1}
    ambiguous = {identity for identity, group in roots.items() if len(group) > 1}
    return ordered, forks, ambiguous


def graph(records):
    """Preserve every edge; a partial ordering never disguises cycles as false results."""
    edges = links(records)
    parents, children = defaultdict(set), defaultdict(set)
    for edge in edges:
        a, b = edge['predecessor'], edge['successor']
        if edge['relation'] != 'background' and a in records and b in records:
            parents[b].add(a)
            children[a].add(b)
    degree = {sha: len(parents[sha]) for sha in records}
    ready = [sha for sha, count in degree.items() if not count]
    heapq.heapify(ready)
    order = []
    while ready:
        sha = heapq.heappop(ready)
        order.append(sha)
        for child in sorted(children[sha]):
            degree[child] -= 1
            if degree[child] == 0:
                heapq.heappush(ready, child)
    unresolved = sorted(set(records) - set(order))
    return {'order': order + unresolved, 'order_unresolved': unresolved, 'edges': edges,
            'missing_references': sorted({e['predecessor'] for e in edges if e['predecessor'] not in records}),
            'ordering_basis': 'Explicit non-background relations; arrival time and title do not establish succession.'}


def is_ancestor(store, older, newer):
    seen = set()
    while newer and newer not in seen:
        if newer == older:
            return True
        seen.add(newer)
        newer = store.snapshot(newer).get('parent')
    return False


def tokens(row):
    # Local retrieval only. Neither lexical agreement nor token absence is a verdict.
    value = ' '.join(' '.join(row[k]) if isinstance(row[k], list) else row[k] for k in FIELDS).casefold()
    words = set(re.findall(r'[a-z][a-z0-9_]{2,}', value))
    for run in re.findall(r'[\u3400-\u9fff]+', value):
        words.update(run[i:i+2] for i in range(len(run)-1))
    return words - {'the', 'and', 'for', 'with', 'this', 'that', 'from', 'result', 'proof', 'experiment', 'method'}


def make_plan(store, snapshot=None, since=None, scope='all', seeds=(), screening_limit=12):
    head = snapshot or store.head()
    snap = store.snapshot(head)
    records = store.records(snap)
    if scope not in {'all', 'affected'} or type(screening_limit) is not int or not 1 <= screening_limit <= 100:
        fail('scope_invalid', 'Use all or affected scope and a screening limit between 1 and 100.')
    seed_set = unique(list(seeds), 'seeds')
    if not seed_set.issubset(records):
        fail('seed_missing', 'Every seed must be a stored exact revision.')
    incoming = set()
    if since:
        if not is_ancestor(store, since, head):
            fail('baseline_unrelated', 'The intake baseline must be an ancestor of this project snapshot.')
        before = store.snapshot(since)
        incoming = set(records) - set(before['record_hashes'])
        seed_set.update(incoming)
        for identity in set(before['selected']) | set(snap['selected']):
            old, new = before['selected'].get(identity), snap['selected'].get(identity)
            if old != new:
                seed_set.update(sha for sha in (old, new) if sha in records)
    if scope == 'affected' and not seed_set:
        fail('seeds_required', 'Affected checks need changed records or explicit seeds; use all for an initial history check.')
    explicit = links(records)
    adjacent = defaultdict(set)
    explicit_pairs = defaultdict(list)
    for edge in explicit:
        a, b = edge['predecessor'], edge['successor']
        if a in records and b in records and a != b:
            adjacent[a].add(b); adjacent[b].add(a)
            explicit_pairs[tuple(sorted((a, b)))].append(edge)
    inverted, terms, identities = defaultdict(set), {}, defaultdict(set)
    for sha, row in records.items():
        terms[sha] = tokens(row)
        identities[row['id']].add(sha)
        for word in terms[sha]:
            inverted[word].add(sha)
    ranked, omitted = {}, {}
    for sha in sorted(records):
        scores = defaultdict(int)
        for word in terms[sha]:
            # Common terms do not cause quadratic corpus-wide enumeration.
            if len(inverted[word]) <= max(20, len(records)//10):
                for other in inverted[word]:
                    if other != sha:
                        scores[other] += 1
        same_id = identities[records[sha]['id']] - {sha}
        candidates = sorted(scores, key=lambda other: (-scores[other], other))
        ranked[sha] = set(candidates[:screening_limit]) | same_id
        omitted[sha] = max(0, len(candidates)-screening_limit)
    focus = set(records) if scope == 'all' else set(seed_set)
    if scope == 'affected':
        for seed in sorted(seed_set):
            focus.update(ranked[seed])
        queue = deque(sorted(focus))
        while queue:
            for other in sorted(adjacent[queue.popleft()] - focus):
                focus.add(other); queue.append(other)
    pairs = {}
    for sha in sorted(focus):
        for other in sorted(ranked[sha] | adjacent[sha]):
            a, b = sorted((sha, other))
            key = pair_key(a, b)
            signals = []
            if (a, b) in explicit_pairs:
                signals.append('existing_relation_requires_content_check')
            if records[a]['id'] == records[b]['id']:
                signals.append('same_identity')
            if terms[a] & terms[b]:
                signals.append('lexical_candidate_only')
            pairs[key] = {'key': key, 'a': a, 'b': b, 'signals': signals,
                          'existing_links': explicit_pairs.get((a, b), [])}
    return {'schema': 'crs-continuity-plan/v1', 'project_id': snap['project_id'],
            'snapshot': head, 'since': since, 'scope': scope, 'seeds': sorted(seed_set),
            'incoming': sorted(incoming), 'input_sha256': digest(canonical(sorted(incoming))),
            'screening_limit': screening_limit, 'record_revisions': sorted(focus),
            'pairs': [pairs[key] for key in sorted(pairs)],
            'screening': {'method': 'bounded_local_lexical_and_explicit_links',
                          'omitted_ranked_candidates': sum(omitted[sha] for sha in focus),
                          'semantic_completeness_proven': False,
                          'prior_full_history_check_verified': False}}


def validate_plan(store, plan):
    exact(plan, PLAN_KEYS, 'plan')
    if plan['schema'] != 'crs-continuity-plan/v1':
        fail('schema_invalid', 'Unsupported continuation plan schema.')
    expected = make_plan(store, plan['snapshot'], plan['since'], plan['scope'], plan['seeds'], plan['screening_limit'])
    if canonical(plan) != canonical(expected):
        fail('plan_changed', 'The plan does not match its exact snapshot and deterministic screening. Generate a fresh plan; do not trim its coverage.')
    return expected


def decisions_template(plan):
    return {'schema': 'crs-continuity-decisions/v1', 'plan_sha256': digest(canonical(plan)),
            'records': [{'revision': sha, 'disposition': 'pending', 'reason': 'Not yet inspected.', 'read_revisions': []}
                        for sha in plan['record_revisions']],
            'pairs': [{'a': pair['a'], 'b': pair['b'], 'decision': 'pending', 'successor': None,
                       'predecessor': None, 'relation': None, 'stored_revision': None,
                       'basis': 'unresolved', 'reason': 'Read the exact records before deciding.', 'evidence_revisions': []}
                      for pair in plan['pairs']]}


def descendant(records, actual, original):
    seen = set()
    while actual in records and actual not in seen:
        if actual == original:
            return True
        seen.add(actual)
        prior = records[actual]['previous']
        if prior not in records or records[prior]['id'] != records[actual]['id']:
            break
        actual = prior
    return False


def check(store, plan, decisions, head=None):
    # Both supported API and CLI callers own the same bounded invocation scope.
    with store.read_io():
        return _check(store, plan, decisions, head)


def _check(store, plan, decisions, head=None):
    validate_plan(store, plan)
    exact(decisions, {'schema', 'plan_sha256', 'records', 'pairs'}, 'decisions')
    if decisions['schema'] != 'crs-continuity-decisions/v1' or decisions['plan_sha256'] != digest(canonical(plan)):
        fail('decision_binding', 'Decisions must bind the exact unmodified plan.')
    head = head or store.head()
    snap = store.snapshot(head)
    if snap['project_id'] != plan['project_id'] or not is_ancestor(store, plan['snapshot'], head):
        fail('snapshot_unrelated', 'The checked snapshot must descend from the planned project snapshot.')
    records = store.records(snap)
    old_records = store.records(store.snapshot(plan['snapshot']))
    if not set(old_records).issubset(records):
        fail('originals_missing', 'Original records must remain stored.')
    if not isinstance(decisions['records'], list) or not isinstance(decisions['pairs'], list):
        fail('schema_invalid', 'Record and pair dispositions must be lists.')
    seen, pending_records, checked_records, no_match = set(), [], set(), set()
    for row in decisions['records']:
        exact(row, RECORD_DECISION_KEYS, 'record disposition')
        sha = row['revision']
        if not isinstance(sha, str) or sha not in plan['record_revisions'] or sha in seen:
            fail('record_coverage', 'Every scoped revision needs exactly one disposition.')
        seen.add(sha); text(row['reason'], 'record reason')
        reads = unique(row['read_revisions'], 'read_revisions')
        if not reads.issubset(records):
            fail('read_missing', 'Reported reads must resolve to stored revisions.')
        if row['disposition'] not in {'assessed', 'no_match', 'pending'}:
            fail('disposition_invalid', 'Use assessed, no_match within the checked scope, or pending.')
        if row['disposition'] == 'pending':
            pending_records.append(sha)
        elif sha not in reads:
            fail('exact_read_required', 'An assessed record must declare its exact record read; software cannot authenticate that reading.')
        else:
            checked_records.add(sha)
            if row['disposition'] == 'no_match':
                no_match.add(sha)
    if seen != set(plan['record_revisions']):
        fail('record_coverage', 'Some scoped records have no disposition.')
    seen_pairs, pending_pairs, disputed, confirmed = set(), [], [], []
    existing_pair_keys = {p['key'] for p in plan['pairs'] if p['existing_links']}
    current_edges = links(records)
    for row in decisions['pairs']:
        exact(row, PAIR_DECISION_KEYS, 'pair disposition')
        a, b = row['a'], row['b']
        if not isinstance(a, str) or not isinstance(b, str) or a == b or a not in old_records or b not in old_records or not ({a, b} & seen):
            fail('pair_outside_scope', 'Pairs must use two distinct planned-snapshot records and touch the checked scope.')
        key = pair_key(a, b)
        if key in seen_pairs:
            fail('pair_duplicate', 'Each pair must have exactly one disposition.')
        seen_pairs.add(key); text(row['reason'], 'pair reason')
        evidence = unique(row['evidence_revisions'], 'evidence_revisions')
        if not evidence.issubset(records):
            fail('evidence_missing', 'Evidence record revisions must be stored and reopenable.')
        verdict = row['decision']
        if verdict not in {'confirmed', 'unrelated', 'pending', 'incorrect'}:
            fail('disposition_invalid', 'Use confirmed, unrelated, pending, or incorrect.')
        if verdict == 'unrelated' and key in existing_pair_keys:
            fail('existing_relation_disputed', 'An existing relation cannot silently become unrelated. Mark it incorrect or pending and preserve its correction context.')
        if verdict == 'pending':
            pending_pairs.append(key)
        elif not {a, b}.issubset(evidence):
            fail('pair_evidence_required', 'A decided pair must identify both exact source revisions as inspected evidence.')
        if verdict != 'confirmed':
            if any(row[k] is not None for k in ('successor', 'predecessor', 'relation', 'stored_revision')) or row['basis'] != 'unresolved':
                fail('unconfirmed_edge', 'Unconfirmed decisions cannot publish an inferred edge; use null relation fields.')
            if verdict == 'incorrect':
                disputed.append(key)
            continue
        successor, predecessor, relation, stored = (row[k] for k in ('successor', 'predecessor', 'relation', 'stored_revision'))
        if {successor, predecessor} != {a, b} or relation not in RELATIONS:
            fail('relation_invalid', 'A confirmed relation must orient the exact pair and use an existing relation type.')
        if not isinstance(stored, str) or not descendant(records, stored, successor):
            fail('stored_revision_invalid', 'The relationship must be in the successor or its explicit same-identity revision chain.')
        if stored not in evidence:
            fail('stored_read_required', 'Inspect and cite the exact revision carrying the saved relationship.')
        if {a, b} & no_match:
            fail('contradictory_disposition', 'A record in a confirmed pair cannot also claim no matching relationship.')
        if not any(e['successor'] == stored and e['predecessor'] == predecessor and e['relation'] == relation for e in current_edges):
            fail('relation_not_saved', 'A confirmed relationship is not stored. Save an explicit record revision through ingest, then recheck.')
        matching_dependencies = [d for d in records[stored]['dependencies'] if d['revision'] == predecessor and d['relation'] == relation]
        if matching_dependencies and not any(d['id'] == records[predecessor]['id'] for d in matching_dependencies):
            fail('dependency_identity_mismatch', 'The stored dependency identity does not match its exact target revision.')
        allowed_basis = {'previous': {'revision_history'}, 'background': {'thematic'},
                         'premise': {'research_dependency'}, 'input': {'research_dependency'}, 'term': {'research_dependency'}}
        if row['basis'] not in allowed_basis.get(relation, {'documented_development', 'research_extension'}):
            fail('basis_invalid', 'Distinguish documented historical development from research applicability and thematic similarity.')
        if relation == 'previous' and records[stored]['id'] != records[predecessor]['id']:
            fail('revision_identity_mismatch', 'Revision history requires the same research identity.')
        confirmed.append({'successor': stored, 'predecessor': predecessor, 'relation': relation, 'basis': row['basis']})
    required_pairs = {p['key'] for p in plan['pairs']}
    if not required_pairs.issubset(seen_pairs):
        fail('pair_coverage', 'Some suggested or existing relationships have no disposition.')
    topo = graph(records)
    new_revisions = set(records)-set(old_records)
    assessed_supplements = set()
    for edge in confirmed:
        sha = edge['successor']
        if sha not in new_revisions:
            continue
        row = records[sha]
        prior = row['previous']
        if prior in old_records and all(row[k] == old_records[prior][k] for k in row if k not in {'previous', 'dependencies'}):
            old_dependencies = {canonical(d) for d in old_records[prior]['dependencies']}
            added = [d for d in row['dependencies'] if canonical(d) not in old_dependencies]
            if all(any(e['successor'] == sha and e['predecessor'] == d['revision'] and e['relation'] == d['relation'] for e in confirmed) for d in added):
                assessed_supplements.add(sha)
    additional_pending = sorted(new_revisions-assessed_supplements)
    pending_reasons = bool(pending_records or pending_pairs or disputed or additional_pending or topo['missing_references'] or topo['order_unresolved'])
    if head != store.head():
        fail('head_changed', 'Project HEAD changed during the check; rerun against the current snapshot.')
    return {'schema': 'crs-continuity-receipt/v1', 'project_id': snap['project_id'], 'snapshot': head,
            'plan_sha256': digest(canonical(plan)), 'decisions_sha256': digest(canonical(decisions)),
            'scope': plan['scope'], 'materials_saved': True,
            'continuity_status': 'completed_with_pending' if pending_reasons else 'completed_for_declared_scope',
            'scoped_records': len(seen), 'assessed_records': len(checked_records), 'decided_pairs': len(seen_pairs),
            'confirmed_relationships': confirmed, 'pending_records': pending_records, 'pending_pairs': pending_pairs,
            'disputed_pairs': disputed, 'missing_references': topo['missing_references'],
            'order_unresolved': topo['order_unresolved'], 'screening': plan['screening'],
            'semantic_judgment_authenticated': False, 'research_review_performed': False,
            'new_revisions_after_plan': sorted(new_revisions), 'additional_records_pending': additional_pending}


def write_external(store, path, value):
    from crs_store import ordinary
    target = ordinary(Path(path).absolute())
    if target.resolve().is_relative_to(store.root.resolve()):
        fail('output_inside_project', 'Write continuation plans and receipts outside the formal library.')
    with target.open('x', encoding='utf-8', newline='\n') as handle:
        handle.write(canonical(value).decode('utf-8') + '\n')
    return {'path': str(target), 'sha256': digest(target.read_bytes())}


def intake_notice(result, before, project):
    """Operational save success is separate from the still-needed semantic check."""
    result = dict(result)
    result['continuation'] = {'materials_saved': True, 'status': 'pending', 'baseline_snapshot': before,
                              'next_action': 'Generate a continuation plan; inspect new-to-old, within-batch, and historical relationships.',
                              'plan_command': ['python', '-B', str(Path(__file__).with_name('crs.py').absolute()), 'continuity-plan', str(project), '--since', before, '--out', 'FRESH_EXTERNAL_PLAN.json']}
    return result

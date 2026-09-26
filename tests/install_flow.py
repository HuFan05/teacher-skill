"""Real-platform lifecycle checks for the Teacher Skill suite installer.

Run: python -B tests/install_flow.py all <new-external-workspace>
     python -B tests/install_flow.py <phase> <workspace>    (phases build on earlier ones)

Every path lives in the external workspace and contains spaces and non-ASCII
characters. HOME and the platform user directories are redirected into the
workspace, so real agent Skill roots and real user configuration are never read
or written, and no network, privilege or package manager is used. Logs remain in
the workspace; only phase names, status and totals are printed. The package must
be byte-identical afterwards.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import unittest.mock
import warnings

PACKAGE = Path(__file__).resolve().parents[1]
INSTALLER = PACKAGE / 'install.py'
SYNTHETIC_KEY = 'sk-synthetic-never-stored-0123456789'


def inventory(*roots):
    result = {}
    for root in roots:
        root = Path(root)
        if root.is_file():
            result[str(root)] = hashlib.sha256(root.read_bytes()).hexdigest()
        elif root.is_dir():
            for path in root.rglob('*'):
                if path.is_file():
                    result[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def last_json(text):
    return json.loads(text.strip().splitlines()[-1])


def main():
    mode, workspace = sys.argv[1:]
    work = Path(workspace).resolve()
    if work == PACKAGE or PACKAGE in work.parents or work in PACKAGE.parents:
        raise SystemExit('The workspace must be outside the package.')
    work.mkdir(parents=True, exist_ok=True)
    logs = work / 'logs'
    logs.mkdir(exist_ok=True)
    manifest = json.loads((PACKAGE / 'package-manifest.json').read_text(encoding='utf-8'))
    names = [item['name'] for item in manifest['payloads']]

    target = work / 'skills space 中文'
    state = work / 'local state 状态'
    cfg = work / 'configuration 配置' / 'manage-personal-knowledge.json'
    obsidian_cfg = cfg.with_name('obsidian-vault-notes.json')
    kstate = work / 'knowledge state 知识'
    tdir = work / 'teacher config 教师'
    tcfg = tdir / 'config.json'
    kb = work / 'knowledge space 中文'
    project = work / 'research sample 研究'
    (kb / 'Notes' / '.obsidian').mkdir(parents=True, exist_ok=True)
    (kb / 'PDF Library').mkdir(parents=True, exist_ok=True)
    (kb / 'PDF Library' / 'synthetic.pdf').write_bytes(b'%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n')

    env = os.environ.copy()
    for key in list(env):
        if key.startswith(('MPK_', 'OBSIDIAN_', 'CODEX_', 'CLAUDE_', 'TEACHER_', 'TH_')) or key == 'PYTHONPATH':
            env.pop(key, None)
    home = work / 'home 家'
    home.mkdir(exist_ok=True)
    for key in ('HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'XDG_CONFIG_HOME', 'XDG_STATE_HOME', 'TMP', 'TEMP', 'TMPDIR'):
        loc = home / key.lower()
        loc.mkdir(exist_ok=True)
        env[key] = str(loc)
    env.update(PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
    env['PATH'] = str(Path(sys.executable).parent) + os.pathsep + env.get('PATH', '')
    fake_home = Path(env['USERPROFILE'] if os.name == 'nt' else env['HOME'])

    base = [sys.executable, '-B', str(INSTALLER), '--agent', 'generic', '--target-root', str(target), '--config', str(cfg),
            '--state-dir', str(state), '--knowledge-state-dir', str(kstate), '--teacher-config-dir', str(tdir)]
    knowledge = ['--knowledge-root', str(kb), '--vault', 'Notes', '--library', 'PDF Library']
    teacher = ['--read-root', str(kb), '--network', 'off']
    setup = knowledge + teacher + ['--yes']
    counts = {'commands': 0, 'assertions': 0}

    def check(condition, label):
        counts['assertions'] += 1
        if not condition:
            raise AssertionError(label)

    def run(label, extra, expected=0, command=None, cwd=None, extra_env=None):
        child_env = {**env, **(extra_env or {})}
        result = subprocess.run(command or base + extra, cwd=cwd or work, env=child_env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=300)
        (logs / (label + '.txt')).write_bytes(result.stdout)
        counts['commands'] += 1
        check(result.returncode == expected, (label, result.returncode, expected))
        return result.stdout.decode('utf-8', 'replace')

    def configs():
        return inventory(cfg.parent, tdir, kstate, state / 'configuration-state.json')

    def load_installer():
        spec = importlib.util.spec_from_file_location('teacher_installer', INSTALLER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    phases = {}

    def phase(name, fn):
        fn()
        phases[name] = 'passed'
        print(json.dumps({'phase': name, 'status': 'passed'}), flush=True)
        (work / 'summary.json').write_text(json.dumps({'os': platform.system(), 'architecture': platform.machine(),
                                                       'python': platform.python_version(), 'phases': phases,
                                                       **counts}, indent=2), encoding='utf-8')

    initial = inventory(PACKAGE)

    def preflight():
        for path in (target, state, cfg, kstate, tdir, kb, project, fake_home):
            check(' ' in str(path) and any(ord(ch) > 127 for ch in str(path)), ('path needs space and non-ASCII', str(path)))
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            for path in PACKAGE.rglob('*.py'):
                compile(path.read_bytes(), str(path), 'exec')
        report = last_json(run('dry_run', ['--dry-run'] + setup))
        check(report['status'] == 'dry-run' and len(report['targets']) == len(names), 'dry-run lists every payload')
        check(report['configuration']['knowledge_root'] == 'configure' and report['configuration']['teacher_skill'] == 'configure', 'dry-run plan')
        check(not target.exists() and not cfg.exists() and not tdir.exists() and not state.exists(), 'dry-run wrote nothing')
        tampered = work / 'tampered copy 篡改'
        if tampered.exists():
            shutil.rmtree(tampered)
        shutil.copytree(PACKAGE, tampered, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        probe = tampered / 'payload' / 'teacher' / 'SKILL.md'
        probe.write_bytes(probe.read_bytes() + b'\nsynthetic tamper\n')
        tampered_run = [sys.executable, '-B', str(tampered / 'install.py'), *base[3:], '--dry-run']
        run('checksum_mismatch', [], 50, command=tampered_run)
        probe.write_bytes((PACKAGE / 'payload' / 'teacher' / 'SKILL.md').read_bytes())
        (tampered / 'payload' / 'teacher' / 'unlisted 未列出.txt').write_text('synthetic', encoding='utf-8')
        run('checksum_unlisted', [], 50, command=tampered_run)
        shutil.rmtree(tampered)
        if os.name == 'nt':
            # Avoid a shell-built command; only a trusted wrapper with no mutation.
            run('wrapper', [], command=[os.environ.get('COMSPEC', 'cmd.exe'), '/d', '/c', 'INSTALL_WINDOWS.cmd', *base[3:], '--dry-run'], cwd=PACKAGE)
        else:
            check(b'\r' not in (PACKAGE / 'install.sh').read_bytes(), 'install.sh has LF line endings')
            run('wrapper', [], command=['sh', str(PACKAGE / 'install.sh'), *base[3:], '--dry-run'])
        for label, extra in (('secret_rejected', ['--api-key', SYNTHETIC_KEY]), ('secret_rejected_equals', ['--api-key=' + SYNTHETIC_KEY]),
                             ('token_rejected', ['--token', SYNTHETIC_KEY])):
            check(SYNTHETIC_KEY not in run(label, ['--dry-run'] + extra, 2), label + ' does not echo the value')
        run('read_root_missing', ['--dry-run', '--read-root', str(work / 'missing 缺失')], 2)
        run('generic_requires_root', [], 2, command=[sys.executable, '-B', str(INSTALLER), '--agent', 'generic', '--dry-run'])
        for agent, folder in (('codex', '.codex'), ('claude', '.claude')):
            report = last_json(run('native_' + agent, [], command=[sys.executable, '-B', str(INSTALLER), '--agent', agent, '--dry-run']))
            check(all(Path(item).parent == (fake_home / folder / 'skills').resolve() for item in report['targets']), agent + ' default root')
        codex_home = work / 'codex home 代码'
        report = last_json(run('native_codex_home', [], command=[sys.executable, '-B', str(INSTALLER), '--agent', 'codex', '--dry-run'],
                               extra_env={'CODEX_HOME': str(codex_home)}))
        check(all(Path(item).parent == (codex_home / 'skills').resolve() for item in report['targets']), 'CODEX_HOME honoured')
        claude_home = work / 'claude home 代码'
        report = last_json(run('native_claude_home', [], command=[sys.executable, '-B', str(INSTALLER), '--agent', 'claude', '--dry-run'],
                               extra_env={'CLAUDE_CONFIG_DIR': str(claude_home)}))
        check(all(Path(item).parent == (claude_home / 'skills').resolve() for item in report['targets']), 'CLAUDE_CONFIG_DIR honoured')
        check(not (fake_home / '.codex').exists() and not (fake_home / '.claude').exists() and not codex_home.exists(), 'dry-runs wrote nothing')

    def default_install():
        root = fake_home / '.claude' / 'skills'
        native = [sys.executable, '-B', str(INSTALLER), '--agent', 'claude']
        report = last_json(run('default_install', [], command=native + ['--yes']))
        check(report['status'] == 'installed' and sorted(report['targets']) == sorted(names), 'default-root install')
        check(all(Path(path).parent == root.resolve() for path in report['targets'].values()), 'Claude Code default root')
        check(report['configuration']['knowledge_root'] == 'not_configured', 'no knowledge root required')
        check(report['configuration']['teacher_skill'] == 'not_configured', 'no teacher configuration without options')
        check(not Path(report['configuration']['knowledge_config']).exists(), 'no knowledge config written')
        check(not Path(report['configuration']['teacher_config']).exists(), 'no teacher config written')
        teacher_py = root / 'teacher' / 'scripts' / 'teacher.py'
        status = json.loads(run('default_teacher_status', [], command=[sys.executable, '-B', str(teacher_py), 'status']))
        check(isinstance(status, dict), 'the Skill runs without a Vault or a model endpoint')
        run('default_doctor', [], command=native + ['--doctor'])
        run('default_protected', [], 21, command=native + ['--yes'])
        report = last_json(run('default_uninstall', [], command=native + ['--uninstall', '--yes']))
        check(report['status'] == 'uninstalled' and not any(root.iterdir()), 'default-root uninstall')
        check(all(Path(path).is_dir() for path in report['backups'].values()), 'default-root backups kept')

    def clean_install():
        check(not target.exists() or not list(target.iterdir()), 'fresh target root')
        report = last_json(run('clean_install', setup))
        check(report['status'] == 'installed' and sorted(report['targets']) == sorted(names), 'all payloads installed')
        check(report['configuration']['knowledge_root'] == 'healthy' and report['configuration']['teacher_skill'] == 'configured', 'both configured')
        check(set(report['configuration']['runtime_environment']) == {'MPK_CONFIG_PATH', 'MPK_STATE_DIR', 'TEACHER_CONFIG_DIR'}, 'runtime environment listed')
        check(cfg.is_file() and obsidian_cfg.is_file(), 'knowledge and Obsidian configuration written')
        written = json.loads(tcfg.read_text(encoding='utf-8'))
        check(str(kb.resolve()) in written['read_roots'], 'teacher read root written')
        check(written['network']['enabled'] is False, 'teacher network policy written')
        check(all(SYNTHETIC_KEY.encode() not in path.read_bytes() for path in work.rglob('*') if path.is_file()), 'no key stored or echoed anywhere')
        check(not any(kb.rglob('*.sqlite*')), 'no knowledge indexing during setup')
        check((state / 'configuration-state.json').is_file() and (state / 'INSTALL_REPORT.json').is_file(), 'marker and report written')
        crs = target / 'cs-ai-research-solve' / 'scripts' / 'crs.py'
        run('crs_init', [], command=[sys.executable, '-B', str(crs), 'init', str(project), '--title', 'Synthetic portability check', '--receive'])
        run('crs_status', [], command=[sys.executable, '-B', str(crs), 'status', str(project)])
        shown = json.loads(run('teacher_show', [], command=[sys.executable, '-B', str(target / 'teacher' / 'scripts' / 'teacher.py'), 'setup', '--show'],
                               extra_env={'TEACHER_CONFIG_DIR': str(tdir)}))
        check(str(kb.resolve()) in shown['read_roots'] and shown['network']['enabled'] is False, 'installed Skill reads the configuration')

    def doctor():
        before = inventory(target)
        managed = configs()
        report = last_json(run('doctor', ['--doctor']))
        check(report['status'] == 'healthy' and report['configuration']['knowledge_root'] == 'healthy', 'doctor healthy')
        check(inventory(target) == before and configs() == managed, 'doctor is read-only')

    def protected_replace():
        before = inventory(target)
        managed = configs()
        run('protected_replace', setup, 21)
        check(inventory(target) == before and configs() == managed, 'protected replacement changed nothing')
        report = last_json(run('replace', setup + ['--replace']))
        check(sorted(report['backups']) == sorted(names), 'every previous target backed up')
        check(all(Path(path).is_dir() for path in report['backups'].values()), 'backups exist outside the scan root')

    def rollback():
        module = load_installer()
        saved = inventory(target)
        managed = configs()

        def fail_after_write(manifest, args, *rest, **kwargs):
            config = Path(args.config)
            teacher_config = Path(args.teacher_config_dir) / 'config.json'
            for path in (config, config.with_name('obsidian-vault-notes.json'), teacher_config):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('synthetic failed update', encoding='utf-8')
            raise module.InstallError('synthetic caught failure')

        def attempt(argv):
            with unittest.mock.patch.object(sys, 'argv', argv):
                args = module.parse_args()
            with unittest.mock.patch.dict(os.environ, env, clear=True), \
                    unittest.mock.patch.object(module, 'run_setup_and_validation', fail_after_write):
                try:
                    module.install(module.read_manifest(), args)
                except module.InstallError:
                    pass
                else:
                    raise AssertionError('expected injected failure')

        attempt(['install.py', *base[3:], *setup, '--replace'])
        check(inventory(target) == saved, 'every target restored after a failed update')
        check(configs() == managed, 'all three configurations restored after a failed update')
        check(not list(target.glob('.teacher.install-*')), 'no staging directory left')
        fresh = work / 'fresh root 新'
        fresh_cfg = work / 'fresh config 新' / 'manage-personal-knowledge.json'
        fresh_teacher = work / 'fresh teacher 新'
        attempt(['install.py', '--agent', 'generic', '--target-root', str(fresh), '--config', str(fresh_cfg), '--state-dir', str(work / 'fresh state 新'),
                 '--knowledge-state-dir', str(work / 'fresh knowledge 新'), '--teacher-config-dir', str(fresh_teacher), *setup])
        check(not any(fresh.iterdir()), 'failed first install leaves no target')
        check(not inventory(fresh_cfg.parent, fresh_teacher), 'failed first install leaves no configuration')
        check(inventory(target) == saved and configs() == managed, 'existing installation untouched by the fresh-root failure')
        original = tcfg.read_bytes()
        tcfg.write_bytes(b'{ synthetic unreadable configuration')
        corrupt = configs()
        run('real_failed_update', setup + ['--replace'], 50)
        check(inventory(target) == saved and configs() == corrupt, 'real setup failure restored targets and configuration byte-exact')
        tcfg.write_bytes(original)
        check(configs() == managed, 'configuration back to the reviewed state')
        (work / 'failed_update_rollback.txt').write_text(
            'Injected failure after replacing all targets and writing three configuration files; a failed first install; '
            'a real teacher.py setup failure after replacement. Every target and configuration restored exactly.\n', encoding='utf-8')

    def debug():
        cfg.unlink()
        run('missing_knowledge_config', ['--doctor'], 2)
        run('repair_knowledge', knowledge + ['--yes', '--setup'])
        check(cfg.is_file(), 'knowledge configuration repaired')
        tcfg.unlink()
        run('missing_teacher_config', ['--doctor'], 2)
        run('repair_teacher', teacher + ['--yes', '--setup'])
        check(str(kb.resolve()) in json.loads(tcfg.read_text(encoding='utf-8'))['read_roots'], 'teacher configuration repaired')
        run('nothing_to_configure', ['--setup', '--yes'], 2)
        probe = target / 'teacher' / 'scripts' / 'teacher.py'
        saved = probe.read_bytes()
        probe.write_bytes(saved + b'\n# synthetic drift\n')
        run('drift_changed', ['--doctor'], 51)
        probe.write_bytes(saved)
        extra = target / 'obsidian-vault-notes' / 'synthetic extra 额外.md'
        extra.write_text('synthetic', encoding='utf-8')
        run('drift_extra', ['--doctor'], 51)
        extra.unlink()
        removed = target / 'pdf-paper-search' / 'SKILL.md'
        kept = removed.read_bytes()
        removed.unlink()
        run('drift_missing', ['--doctor'], 51)
        removed.write_bytes(kept)
        run('doctor_after_debug', ['--doctor'])

    def retest():
        run('post_debug_retest', setup + ['--replace'])
        run('post_debug_doctor', ['--doctor'])

    def uninstall():
        managed = inventory(cfg.parent, tdir, kstate)
        report = last_json(run('uninstall', ['--uninstall', '--yes']))
        check(report['status'] == 'uninstalled' and not list(target.iterdir()), 'all targets removed from discovery')
        check(inventory(cfg.parent, tdir, kstate) == managed, 'configuration preserved')
        check((state / 'backups').is_dir() and all(Path(path).is_dir() for path in report['backups'].values()), 'backups preserved')

    def purge():
        run('reinstall_for_purge', setup)
        store = state / 'teacher.sqlite3'
        store.write_bytes(b'synthetic research store')
        run('purge_requires_yes', ['--uninstall', '--purge-local-state'], 2)
        check(all((target / name).is_dir() for name in names), 'purge without --yes changed nothing')
        report = last_json(run('purge', ['--uninstall', '--purge-local-state', '--yes']))
        check(report['status'] == 'uninstalled-and-purged' and not list(target.iterdir()), 'targets removed')
        check(not cfg.exists() and not obsidian_cfg.exists() and not tcfg.exists() and not kstate.exists(), 'configuration and knowledge state purged')
        check(not (state / 'backups').exists() and not (state / 'INSTALL_REPORT.json').exists(), 'backups and report purged')
        check(store.read_bytes() == b'synthetic research store' and str(store) in report['preserved'], 'research store preserved')
        check(inventory(PACKAGE) == initial, 'package unchanged')

    operations = [('preflight', preflight), ('default_install', default_install), ('clean_install', clean_install), ('doctor', doctor),
                  ('protected_replace', protected_replace), ('failed_update_rollback', rollback), ('debug', debug),
                  ('post_debug_retest', retest), ('uninstall', uninstall), ('purge', purge)]
    for name, fn in operations:
        if mode in ('all', name):
            phase(name, fn)
    check(inventory(PACKAGE) == initial, 'package unchanged')
    print(json.dumps({'result': 'passed', 'phases': len(phases), **counts}), flush=True)


if __name__ == '__main__':
    main()

"""Real CLI process/admission and backup bytes, without a live database."""
from argparse import Namespace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from aikey import index_commands, index_rebuild
from test_index_rebuild import NEW, setup


def command(*args, cwd):
    source = Path(__file__).resolve().parents[1]/'src'
    return subprocess.run([sys.executable, '-m', 'aikey.index_rebuild', *args],
        cwd=cwd, env={'PATH': os.environ.get('PATH', ''), 'PYTHONPATH': str(source),
                      'PYTHONDONTWRITEBYTECODE': '1'},
        capture_output=True, text=True, timeout=10)


def test_actual_module_cli_from_unrelated_cwd_exposes_migration_and_refuses_missing_admission(tmp_path):
    help_result = command('--help', cwd=tmp_path)
    assert help_result.returncode == 0
    for verb in ('export', 'stage', 'qualify', 'cutover', 'readback', 'rollback'):
        assert verb in help_result.stdout
    missing = command('stage', '--state-dir', str(tmp_path), cwd=tmp_path)
    assert missing.returncode == 2
    value = json.loads(missing.stdout)
    assert value == {'error': 'index_migration_refused_or_uncertain',
                     'category': 'IndexCommandError', 'retry_automatically': False}
    assert not list(tmp_path.iterdir()) and str(tmp_path) not in missing.stdout


def test_status_and_plan_do_not_require_database_or_native_credentials(tmp_path):
    setup(tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.iterdir()}
    status = command('status', '--state-dir', str(tmp_path), cwd=tmp_path)
    plan = command('plan', '--state-dir', str(tmp_path), cwd=tmp_path)
    assert status.returncode == plan.returncode == 0
    assert json.loads(status.stdout) == []
    assert json.loads(plan.stdout)['live']['pinned']
    assert {p: p.read_bytes() for p in tmp_path.iterdir()} == before


def backup_fixture(tmp_path):
    state = tmp_path/'state'
    state.mkdir(mode=0o700)
    store = setup(state)
    run = index_rebuild.Rebuild(store, state, target_revision=NEW,
                                target_source='http://127.0.0.1:8181')
    backups = tmp_path/'backups'
    backups.mkdir(mode=0o700)
    dump = backups/'search-20261103T120000.dump'
    raw = b'fictional-only-backup-bytes'
    dump.write_bytes(raw)
    dump.chmod(0o600)
    data = {'dump': dump.name, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
            'profile': json.loads((state/'search-profile.json').read_text()),
            'counts': {'embeddings': 5, 'tables': {'ramDetections': 5}},
            'verified': {'restore_counts_match': True}}
    manifest = dump.with_suffix('.json')
    manifest.write_text(json.dumps(data))
    manifest.chmod(0o600)
    return Namespace(backups=backups), run, dump, manifest, data


def test_cutover_backup_rehashes_same_verified_bytes_and_profile(tmp_path):
    args, run, dump, manifest, _ = backup_fixture(tmp_path)
    before = [p.read_bytes() for p in (dump, manifest)]
    result = index_commands._backup(args, run)
    assert result['verified'] and result['dump_sha256_rechecked']
    assert result['counts']['embeddings'] == 5
    assert [p.read_bytes() for p in (dump, manifest)] == before


@pytest.mark.parametrize('mutation', ['bytes', 'size', 'profile', 'unverified', 'counts',
                                      'old', 'symlink', 'fifo', 'hardlink', 'mode'])
def test_cutover_never_trusts_changed_unsafe_or_unverified_dump(tmp_path, mutation):
    args, run, dump, manifest, data = backup_fixture(tmp_path)
    if mutation == 'bytes':
        dump.write_bytes(b'X'*len(dump.read_bytes()))
    elif mutation == 'size':
        data['bytes'] += 1
    elif mutation == 'profile':
        data['profile']['source'] = 'http://127.0.0.1:9999'
    elif mutation == 'unverified':
        data['verified']['restore_counts_match'] = False
    elif mutation == 'counts':
        data['counts']['embeddings'] = 4
    elif mutation == 'old':
        past = time.time()-7200
        os.utime(manifest, (past, past))
    elif mutation in {'symlink', 'fifo', 'hardlink'}:
        outside = tmp_path/'retained-dump'
        dump.rename(outside)
        if mutation == 'symlink':
            dump.symlink_to(outside)
        elif mutation == 'fifo':
            os.mkfifo(dump, 0o600)
        else:
            os.link(outside, dump)
    elif mutation == 'mode':
        dump.chmod(0o644)
    if mutation in {'size', 'profile', 'unverified', 'counts'}:
        manifest.write_text(json.dumps(data))
    with pytest.raises((index_commands.IndexCommandError, OSError)):
        index_commands._backup(args, run)
    assert not run.directory.exists()


def test_unsafe_database_transport_cannot_downgrade_tls_or_use_password_argv(tmp_path, monkeypatch):
    import psycopg
    calls = []
    monkeypatch.setattr(psycopg, 'connect', lambda **kwargs: calls.append(kwargs))
    secret = tmp_path/'password'
    secret.write_text('fictional-database-password\n')
    secret.chmod(0o600)
    args = Namespace(database_name='unifi-protect', database_user='unifi-protect',
        database_socket=None, database_host='127.0.0.1', database_port=5432,
        database_ca=tmp_path/'public-ca', database_password_file=secret)
    connect = index_commands._connection(args)
    assert not calls
    connect()
    assert calls[0]['sslmode'] == 'verify-full'
    assert calls[0]['password'] == 'fictional-database-password'
    args.database_host = '8.8.8.8'
    with pytest.raises(index_commands.IndexCommandError):
        index_commands._connection(args)
    assert len(calls) == 1

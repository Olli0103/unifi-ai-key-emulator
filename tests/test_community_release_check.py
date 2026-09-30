"""Release checks fail closed on missing rights and prohibited public material."""
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import zipfile

import pytest

# The gate is a source-tree tool, deliberately outside runtime device code.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from community_release_check import (  # noqa: E402
    REQUIRED, artifact_issues, evidence_issues, main, member_issues, source_snapshot,
)


def evidence(status='needs_evidence'):
    return {'schema': 'community-release-evidence/1',
            'checks': {key: {'status': status} for key in REQUIRED}}


def test_audit_cannot_grant_release_or_hide_missing_rights():
    record = evidence()
    assert evidence_issues(record, 'digest', audit_only=True) == set()
    issues = evidence_issues(record, 'digest', audit_only=False)
    assert 'needs_evidence:firmware_authorization' in issues
    assert 'needs_evidence:development_statement' in issues
    assert 'unreviewed_source_snapshot' in issues


def test_verified_claim_needs_reviewer_reference_and_matching_source():
    record = evidence('verified')
    assert 'unsubstantiated_verification' in evidence_issues(record, 'digest', audit_only=True)
    for item in record['checks'].values():
        item.update(reviewer='test-reviewer', evidence_ref='synthetic-review-record')
    record['reviewed_snapshot_sha256'] = 'digest'
    assert not evidence_issues(record, 'digest', audit_only=False)
    assert evidence_issues(record, 'changed', audit_only=False) == {'unreviewed_source_snapshot'}
    del record['checks']['legal_review']
    assert evidence_issues(record, 'digest', audit_only=True) == {'invalid_evidence_checks'}


@pytest.mark.parametrize('name', ['package/state/config.json', '../escape', '/escape',
                                    'package/firmware.deb', 'package/model.onnx',
                                    'package/private/record.txt', 'package/frame.jpg',
                                    'package/.env.local', 'package/device.key', 'package/vendor.tar.gz', 'package/model.tflite'])
def test_prohibited_archive_members(name):
    assert 'prohibited_member' in member_issues(name)


def test_payload_check_catches_renamed_binary_key_and_link():
    assert 'binary_payload' in member_issues('package/innocent.txt', b'\x7fELFsynthetic')
    assert 'private_key' in member_issues('package/data.txt',
                                        b'-----BEGIN PRIVATE KEY-----\nsynthetic')
    assert 'nonregular_member' in member_issues('package/link', regular=False)
    assert not member_issues('package/docs/contract.md', b'independent interface description')


def test_sdist_and_wheel_contents_are_checked(tmp_path):
    path = tmp_path / 'package.tar.gz'
    with tarfile.open(path, 'w:gz') as archive:
        member = tarfile.TarInfo('package/model.pth')
        member.size = 4
        archive.addfile(member, io.BytesIO(b'test'))
        link = tarfile.TarInfo('package/link')
        link.type = tarfile.SYMTYPE
        link.linkname = '/private/source'
        archive.addfile(link)
    assert artifact_issues(path) == {'prohibited_member', 'nonregular_member'}
    wheel = tmp_path / 'package.whl'
    with zipfile.ZipFile(wheel, 'w') as archive:
        archive.writestr('package/firmware.txt', b'\x7fELFsynthetic')
    assert artifact_issues(wheel) == {'binary_payload'}


def test_readiness_cli_blocks_even_when_automated_audit_passes(tmp_path, capsys):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    record_path = tmp_path / 'docs/legal/release-evidence.json'
    record_path.parent.mkdir(parents=True)
    record_path.write_text(json.dumps(evidence()))
    (tmp_path / 'source.py').write_text('value = 1\n')
    subprocess.run(['git', 'add', '.'], cwd=tmp_path, check=True)
    assert main(['--root', str(tmp_path), '--audit-only']) == 0
    assert json.loads(capsys.readouterr().out)['release_approved'] is False
    assert main(['--root', str(tmp_path)]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result['release_approved'] is False
    assert 'no_active_project_license' in result['issues']
    before = source_snapshot(tmp_path, ['source.py'])
    (tmp_path / 'source.py').write_text('value = 2\n')
    assert source_snapshot(tmp_path, ['source.py']) != before


def test_invalid_evidence_type_is_rejected():
    assert evidence_issues([], 'digest', audit_only=True) == {'invalid_evidence_schema'}


def test_source_links_are_hashed_without_reading_target(tmp_path):
    target = tmp_path / 'private-content'
    target.write_bytes(b'synthetic-private')
    link = tmp_path / 'link'
    link.symlink_to(target)
    before = source_snapshot(tmp_path, ['link'])
    target.write_bytes(b'changed-private-content')
    assert source_snapshot(tmp_path, ['link']) == before


def test_empty_license_and_dirty_inputs_cannot_pass_readiness(tmp_path, capsys):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    p = tmp_path / 'docs/legal/release-evidence.json'
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps(evidence()))
    (tmp_path / 'LICENSE').write_text('')
    subprocess.run(['git', 'add', '.'], cwd=tmp_path, check=True)
    assert main(['--root', str(tmp_path)]) == 2
    result = json.loads(capsys.readouterr().out)
    assert 'empty_project_license' in result['issues']
    assert 'uncommitted_release_inputs' in result['issues']

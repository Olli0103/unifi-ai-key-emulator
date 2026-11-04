"""Catalogue local immutable images with pinned Syft; never grant release approval.

Only CycloneDX and sanitized subject/count receipts are public outputs. Syft's
native intermediate includes image configuration/history and stays temporary.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
import tomllib
import urllib.request

from container_install_check import CheckError, digest, git_context, load, publish, read, require

VERSION = '1.56.0'
SYFT_COMMIT = '173055c39e9752f7b450ea404cd1801695277426'
ARCHIVES = {
    'amd64': '1c2493cfcf52d3367d6cb25780d9190c111c2219c34c820574c43e8bae15c528',
    'arm64': 'ace61b9397ae28189c674ae332d67a8074dafd71e87724dfb8b22412d8253bb5',
}
IMAGE_ID = re.compile(r'sha256:[0-9a-f]{64}')
RECEIPT = 'aikey-image-inventory/1'
LIMIT = 32 * 1024 * 1024
OFFLINE_CONFIG = '''check-for-app-update: false
parallelism: 2
cache:
  dir: ''
  ttl: 0s
license:
  content: none
golang:
  search-local-mod-cache-licenses: false
  search-local-vendor-licenses: false
  search-remote-licenses: false
  use-packages-lib: false
java:
  use-network: false
  use-maven-local-repository: false
javascript:
  search-remote-licenses: false
python:
  search-remote-licenses: false
cpp:
  vcpkg-allow-git-clone: false
package:
  nested-archive-max-depth: 0
  nested-archive-max-memory-bytes: 67108864
  nested-archive-max-disk-bytes: 0
'''


def json_bytes(raw):
    # Same strict JSON contract as the installation coordinator, with a larger
    # bounded input for whole-image inventories. Never echo producer payloads.
    from container_install_check import _constant, _pairs
    result = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_constant)
    require(isinstance(result, dict), 'image_inventory_object_required')
    return result


def json_file(path):
    return json_bytes(read(path, LIMIT))


def install(directory, architecture):
    require(architecture in ARCHIVES, 'image_inventory_architecture_refused')
    directory = Path(directory)
    directory.mkdir(mode=0o700)  # No overwrite/reuse of a previous attempt.
    name = f'syft_{VERSION}_linux_{architecture}.tar.gz'
    url = f'https://github.com/anchore/syft/releases/download/v{VERSION}/{name}'
    with urllib.request.urlopen(url, timeout=60) as response:
        archive = response.read(100 * 1024 * 1024 + 1)
    require(len(archive) <= 100 * 1024 * 1024 and digest(archive) == ARCHIVES[architecture],
            'image_inventory_tool_archive_mismatch')
    # No extractall, links, paths, or second download between verification/use.
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as source:
        members = [member for member in source.getmembers() if member.name == 'syft']
        require(len(members) == 1 and members[0].isfile() and 0 < members[0].size <= 150 * 1024 * 1024,
                'image_inventory_tool_member_refused')
        payload = source.extractfile(members[0]).read(150 * 1024 * 1024 + 1)
        require(len(payload) == members[0].size, 'image_inventory_tool_member_refused')
    target = directory / 'syft'
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o700)
    with os.fdopen(fd, 'wb') as output:
        output.write(payload)
        output.flush()
        os.fsync(output.fileno())
    publish(directory / 'tool.json', {'version': VERSION, 'architecture': architecture,
            'archive_sha256': ARCHIVES[architecture], 'binary_sha256': digest(payload)})


def command(args, *, env=None, limit=LIMIT, timeout=300):
    # Spool output, then read with a bound. The enclosing disposable CI runner
    # and job deadline remain the hard disk/process containment boundary.
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        result = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=output, stderr=errors,
                                timeout=timeout, env=env, check=False)
        require(result.returncode == 0, 'image_inventory_producer_failed')
        output.seek(0)
        raw = output.read(limit + 1)
    require(len(raw) <= limit, 'image_inventory_producer_too_large')
    return raw


def subject(inspected, revision, architecture, nonce, profile):
    git_context('fixture/validation', revision, nonce)
    require(architecture in ARCHIVES and nonce.endswith('-' + architecture)
            and profile in ('aikey', 'aiport'), 'image_inventory_context_refused')
    require(isinstance(inspected, list) and len(inspected) == 1 and isinstance(inspected[0], dict),
            'image_inventory_inspection_refused')
    image = inspected[0]
    require(isinstance(image.get('Id'), str) and IMAGE_ID.fullmatch(image['Id']),
            'image_inventory_image_id_refused')
    config = image.get('Config', {})
    labels = config.get('Labels', {})
    require(image.get('Os') == 'linux' and image.get('Architecture') == architecture
            and config.get('User') == '10001:10001'
            and labels.get('org.opencontainers.image.revision') == revision
            and labels.get('org.local-aikey.install-run') == nonce
            and labels.get('org.local-aikey.install-profile') == profile,
            'image_inventory_subject_mismatch')
    return {'image_id': image['Id'], 'revision': revision, 'architecture': architecture,
            'nonce': nonce, 'profile': profile}


def normalized(name):
    require(isinstance(name, str) and re.fullmatch(r'[A-Za-z0-9_.+-]{1,160}', name),
            'image_inventory_package_name_refused')
    return re.sub(r'[-_.]+', '-', name).lower()


def expected_python(root):
    root = Path(root)
    result = {'local-aikey': tomllib.loads(read(root / 'pyproject.toml').decode())['project']['version']}
    for name in ('requirements-runtime.lock', 'requirements-build.lock'):
        for line in read(root / name).decode().splitlines():
            if not line.strip() or line.startswith('#'):
                continue
            match = re.fullmatch(r'([A-Za-z0-9_.-]+)==([A-Za-z0-9.+!-]+)', line)
            require(match is not None, 'image_inventory_lock_refused')
            key, version = normalized(match[1]), match[2]
            require(key not in result, 'image_inventory_duplicate_lock_entry')
            result[key] = version
    return result


def validate(native, cdx, identity, expected):
    source = native['source']
    metadata = source['metadata']
    require(source.get('type') == 'image' and metadata.get('imageID') == identity['image_id']
            and metadata.get('architecture') == identity['architecture'] and metadata.get('os') == 'linux',
            'image_inventory_scan_subject_mismatch')
    labels = metadata['labels']
    require(labels.get('org.opencontainers.image.revision') == identity['revision']
            and labels.get('org.local-aikey.install-run') == identity['nonce']
            and labels.get('org.local-aikey.install-profile') == identity['profile'],
            'image_inventory_scan_labels_mismatch')
    # Verify the content-addressed config bytes too, not just scanner strings.
    config_bytes = base64.b64decode(metadata['config'], validate=True)
    config = json_bytes(config_bytes)
    require('sha256:' + digest(config_bytes) == identity['image_id']
            and config['architecture'] == identity['architecture'] and config['os'] == 'linux'
            and config['config']['Labels'] == labels, 'image_inventory_config_mismatch')
    descriptor = native['descriptor']
    require(descriptor.get('name') == 'syft' and descriptor.get('version') == VERSION
            and descriptor['configuration']['search']['scope'] == 'squashed'
            and native['schema']['version'] == '16.1.12', 'image_inventory_scanner_refused')
    require(cdx.get('bomFormat') == 'CycloneDX' and cdx.get('specVersion') == '1.7'
            and type(cdx.get('version')) is int and cdx['version'] == 1,
            'image_inventory_cyclonedx_refused')
    component = cdx['metadata']['component']
    tools = cdx['metadata']['tools']['components']
    require(component.get('type') == 'container' and component.get('version') == source['version']
            and component.get('name') == source['name']
            and len(tools) == 1 and tools[0].get('name') == 'syft' and tools[0].get('version') == VERSION,
            'image_inventory_cyclonedx_subject_mismatch')
    artifacts, all_components = native['artifacts'], cdx['components']
    require(isinstance(artifacts, list) and isinstance(all_components, list)
            and 1 <= len(artifacts) <= 10000 and len(all_components) <= 100000,
            'image_inventory_packages_refused')
    # Syft 1.56 also emits OS/file components, not just package artifacts.
    # Match those against their distinct native producers rather than silently
    # dropping unknown components or treating files as installed packages.
    distro = native['distro']
    systems = [row for row in all_components if row.get('type') == 'operating-system']
    require(distro.get('id') == 'debian' and isinstance(distro.get('versionID'), str)
            and 0 < len(distro['versionID']) <= 64 and len(systems) == 1
            and systems[0].get('name') == distro['id'] and systems[0].get('version') == distro['versionID'],
            'image_inventory_os_formats_disagree')
    files = {row['id']: row for row in native.get('files', [])}
    seen = set()
    components = []
    for row in all_components:
        kind = row.get('type')
        reference = row.get('bom-ref')
        require(isinstance(reference, str) and 0 < len(reference) <= 8192 and reference not in seen,
                'image_inventory_component_reference_refused')
        seen.add(reference)
        if kind == 'file':
            original = files.get(reference)
            require(isinstance(original, dict) and original['location']['path'] == row.get('name'),
                    'image_inventory_file_formats_disagree')
            hashes = {item['algorithm']: item['value'] for item in original.get('digests', [])}
            cdx_hashes = {item['alg']: item['content'] for item in row.get('hashes', [])}
            require(isinstance(hashes.get('sha256'), str)
                    and re.fullmatch('[0-9a-f]{64}', hashes['sha256'])
                    and cdx_hashes.get('SHA-256') == hashes['sha256'], 'image_inventory_file_formats_disagree')
        elif kind in ('library', 'application', 'framework'):
            components.append(row)
        else:
            require(kind == 'operating-system', 'image_inventory_component_type_refused')
    def key(row, origin):
        values = tuple(row.get(field) for field in ('name', 'version', 'purl'))
        name, version, purl = values
        valid = (all(isinstance(value, str) and 0 < len(value) <= 4096 for value in (name, version))
                 and (purl is None or isinstance(purl, str)
                      and (purl == '' or purl.startswith('pkg:') and len(purl) <= 4096)))
        if not valid:
            error = CheckError('image_inventory_component_identity_refused')
            # Fixed field/type/length facts only, never the offending payload,
            # component name, paths or image environment/configuration.
            error.inventory_context = {'format': origin, 'fields': {
                field: {'type': type(value).__name__, 'length': len(value) if isinstance(value, str) else None}
                for field, value in zip(('name', 'version', 'purl'), values)}}
            raise error
        # PURL is optional in both formats. Syft emits an empty native string
        # but omits the empty CycloneDX field. Preserve this as unknown, never
        # invent a URL or discard a package to force agreement.
        return name, version, purl or None
    def native_key(row):
        licenses = tuple(sorted(item.get('spdxExpression') or item['value'] for item in row.get('licenses', [])))
        return key(row, 'native'), licenses
    def cdx_key(row):
        licenses = tuple(sorted(item.get('expression') or item.get('license', {}).get('id')
                                or item['license']['name'] for item in row.get('licenses', [])))
        return key(row, 'cyclonedx'), licenses
    require(Counter(map(native_key, artifacts)) == Counter(map(cdx_key, components)),
            'image_inventory_formats_disagree')
    counts = Counter()
    python = {}
    deb = set()
    unknown = 0
    without_purl = 0
    for row in artifacts:
        require(row.get('type') in {'deb', 'python', 'binary'}, 'image_inventory_unreviewed_package_type')
        counts[row['type']] += 1
        if not row.get('licenses') or not any(item.get('spdxExpression') for item in row['licenses']):
            unknown += 1
        if not row.get('purl'):
            without_purl += 1
        if row['type'] == 'python':
            python.setdefault(normalized(row['name']), set()).add(row['version'])
        if row['type'] == 'deb':
            deb.add(row['name'])
    require(all(python.get(name) == {version} for name, version in expected.items()),
            'image_inventory_runtime_missing_or_mismatched')
    require({'ffmpeg', 'openssl', 'ca-certificates'} <= deb, 'image_inventory_os_runtime_incomplete')
    return {'schema': RECEIPT, **identity, 'scanner': 'syft', 'scanner_version': VERSION,
            'package_count': len(artifacts), 'package_types': dict(sorted(counts.items())),
            'operating_system': {'id': distro['id'], 'version': distro['versionID']},
            'packages_without_spdx_expression': unknown, 'runtime_lock_packages_verified': len(expected),
            'packages_without_purl': without_purl,
            'licenses_reviewed': False, 'release_approved': False}


def collect(args):
    git_context(args.repository, args.revision, args.nonce)
    expected = expected_python(args.source)
    tool = Path(args.tool)
    proof = load(tool.parent / 'tool.json')
    require(proof == {'version': VERSION, 'architecture': args.architecture,
            'archive_sha256': ARCHIVES[args.architecture],
            'binary_sha256': digest(read(tool, 150 * 1024 * 1024))}, 'image_inventory_tool_changed')
    # Only our known CI tag is inspected; only the returned immutable ID is
    # scanned. Explicit docker: forbids fallback to a remote registry.
    image = f'local-aikey-install:{args.nonce}-{args.profile}'
    raw = command(['docker', 'image', 'inspect', image], limit=1024 * 1024, timeout=30)
    identity = subject(json.loads(raw), args.revision, args.architecture, args.nonce, args.profile)
    env = {key: os.environ[key] for key in ('PATH', 'HOME', 'TMPDIR') if key in os.environ}
    env['SYFT_CHECK_FOR_APP_UPDATE'] = 'false'
    version = json_bytes(command([str(tool), 'version', '-o', 'json'], env=env, limit=65536, timeout=15))
    require(version.get('version') == VERSION and version.get('gitCommit') == SYFT_COMMIT,
            'image_inventory_tool_version_mismatch')
    with tempfile.TemporaryDirectory(prefix='image-inventory-') as temporary:
        directory = Path(temporary)
        config = directory / 'syft.yaml'
        config.write_text(OFFLINE_CONFIG)
        native, cdx = directory / 'native.json', directory / 'bom.json'
        command([str(tool), 'scan', '--config', str(config), '--quiet', '--scope', 'squashed',
                 '--output', 'syft-json=' + str(native), '--output', 'cyclonedx-json=' + str(cdx),
                 'docker:' + identity['image_id']], env=env, limit=65536)
        public = json_file(cdx)
        receipt = validate(json_file(native), public, identity, expected)
        target = Path(args.results) / (args.profile + '-sbom.cdx.json')
        # Publish full standard metadata only after validating both outputs.
        raw = read(cdx, LIMIT)
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        require(read(target, LIMIT) == raw, 'image_inventory_publication_uncertain')
        publish(Path(args.results) / (args.profile + '-inventory.json'), receipt | {'sbom_sha256': digest(raw)})


def finalize(results, revision, architecture, nonce):
    git_context('fixture/validation', revision, nonce)
    require(architecture in ARCHIVES and nonce.endswith('-' + architecture), 'image_inventory_context_refused')
    results = Path(results)
    install_summary = load(results / 'summary.json')
    from container_install_check import summary
    require(install_summary == summary(results, revision, architecture), 'image_inventory_install_not_complete')
    records = {}
    for profile in ('aikey', 'aiport'):
        value = load(results / (profile + '-inventory.json'))
        fields = {'schema', 'image_id', 'revision', 'architecture', 'nonce', 'profile', 'scanner', 'scanner_version',
                  'package_count', 'package_types', 'packages_without_spdx_expression',
                  'runtime_lock_packages_verified', 'licenses_reviewed', 'release_approved', 'sbom_sha256',
                  'operating_system', 'packages_without_purl'}
        require(set(value) == fields and value['schema'] == RECEIPT and value['revision'] == revision
                and value['architecture'] == architecture and value['nonce'] == nonce and value['profile'] == profile
                and IMAGE_ID.fullmatch(value['image_id']) and value['scanner'] == 'syft'
                and value['scanner_version'] == VERSION and value['licenses_reviewed'] is False
                and value['release_approved'] is False
                and set(value['operating_system']) == {'id', 'version'}
                and value['operating_system']['id'] == 'debian'
                and isinstance(value['operating_system']['version'], str)
                and 0 < len(value['operating_system']['version']) <= 64, 'image_inventory_saved_record_refused')
        counts = value['package_types']
        require(isinstance(counts, dict) and set(counts) <= {'deb', 'python', 'binary'}
                and {'deb', 'python'} <= set(counts)
                and all(type(count) is int and 0 < count <= 10000 for count in counts.values())
                and type(value['package_count']) is int and value['package_count'] == sum(counts.values())
                and type(value['packages_without_spdx_expression']) is int
                and 0 <= value['packages_without_spdx_expression'] <= value['package_count']
                and type(value['packages_without_purl']) is int
                and 0 <= value['packages_without_purl'] <= value['package_count']
                and type(value['runtime_lock_packages_verified']) is int
                and 0 < value['runtime_lock_packages_verified'] <= counts['python'], 'image_inventory_saved_counts_refused')
        raw = read(results / (profile + '-sbom.cdx.json'), LIMIT)
        require(digest(raw) == value['sbom_sha256'], 'image_inventory_saved_sbom_changed')
        records[profile] = value
    require(records['aikey']['image_id'] != records['aiport']['image_id'], 'image_inventory_cross_profile_image')
    return {'schema': 'aikey-image-inventory-summary/1', 'revision': revision, 'architecture': architecture,
            'nonce': nonce, 'profiles': records, 'install_cases_finished': install_summary['cases_finished'],
            'cleanup_verified': True, 'licenses_reviewed': False, 'release_approved': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    setup = sub.add_parser('install')
    setup.add_argument('--directory', type=Path, required=True)
    collect_parser = sub.add_parser('collect')
    collect_parser.add_argument('--source', type=Path, required=True)
    collect_parser.add_argument('--tool', type=Path, required=True)
    collect_parser.add_argument('--profile', choices=('aikey', 'aiport'), required=True)
    collect_parser.add_argument('--repository', required=True)
    finish = sub.add_parser('finalize')
    finish.add_argument('--output', type=Path, required=True)
    for item in (setup, collect_parser, finish):
        item.add_argument('--architecture', choices=tuple(ARCHIVES), required=True)
    for item in (collect_parser, finish):
        item.add_argument('--results', type=Path, required=True)
        item.add_argument('--revision', required=True)
        item.add_argument('--nonce', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'install':
            install(args.directory, args.architecture)
        elif args.command == 'collect':
            collect(args)
        else:
            publish(args.output, finalize(args.results, args.revision, args.architecture, args.nonce))
        return 0
    except (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError, RuntimeError,
            subprocess.SubprocessError) as error:
        # No raw producer errors, image configuration, private paths or keys.
        code = str(error) if isinstance(error, CheckError) else 'image_inventory_refused'
        print(json.dumps({'schema': RECEIPT, 'passed': False, 'error': code,
                          'stage': args.command, 'failure_type': type(error).__name__,
                          'context': getattr(error, 'inventory_context', None),
                          'release_approved': False}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

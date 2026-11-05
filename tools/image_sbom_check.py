"""Catalogue local immutable images with pinned Syft; never grant release approval.

Only CycloneDX, selected package-license evidence and sanitized subject/count
receipts are public outputs. Native image configuration/history stays temporary.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter
from copy import deepcopy
from datetime import datetime
import io
import json
import os
from pathlib import Path
import posixpath
import re
import subprocess
import tarfile
import tempfile
import tomllib
import urllib.request
from uuid import UUID

from container_install_check import CheckError, digest, git_context, load, publish, read, require

VERSION = '1.56.0'
SYFT_COMMIT = '173055c39e9752f7b450ea404cd1801695277426'
ARCHIVES = {
    'amd64': '1c2493cfcf52d3367d6cb25780d9190c111c2219c34c820574c43e8bae15c528',
    'arm64': 'ace61b9397ae28189c674ae332d67a8074dafd71e87724dfb8b22412d8253bb5',
}
IMAGE_ID = re.compile(r'sha256:[0-9a-f]{64}')
RECEIPT = 'aikey-image-inventory/2'
LICENSE_REVIEW = 'aikey-image-license-review/2'
LIMIT = 32 * 1024 * 1024
NOTICE_LIMIT = 2 * 1024 * 1024
NOTICE_GLOBS = ['/app/LICENSE', '/usr/local/LICENSE',
                '/usr/share/doc/**/copyright', '/usr/share/common-licenses/*'] + [
    '/usr/local/lib/python*/site-packages/**/' + name + suffix
    for name in ('[Ll][Ii][Cc][Ee][Nn][Ss][Ee]', '[Ll][Ii][Cc][Ee][Nn][Cc][Ee]',
                 '[Nn][Oo][Tt][Ii][Cc][Ee]', '[Cc][Oo][Pp][Yy][Ii][Nn][Gg]',
                 '[Cc][Oo][Pp][Yy][Rr][Ii][Gg][Hh][Tt]')
    for suffix in ('', '.*')
]
OFFLINE_CONFIG = '''check-for-app-update: false
parallelism: 2
cache:
  dir: ''
  ttl: 0s
license:
  content: all
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
# Zero disables Syft's silent size-based omission. Our bounded report reader
# and per-file limit refuse oversized evidence instead of publishing a subset.
OFFLINE_CONFIG += 'file:\n  content:\n    skip-files-above-size: 0\n    globs:\n' + ''.join(
    '      - ' + json.dumps(pattern) + '\n' for pattern in NOTICE_GLOBS)


def notice_path(path):
    if (not isinstance(path, str) or len(path) > 4096 or not path.startswith('/')
            or any(part in ('', '.', '..') for part in path[1:].split('/'))
            or any(ord(char) < 32 for char in path)):
        return False
    return bool(path in ('/app/LICENSE', '/usr/local/LICENSE')
                or re.fullmatch(r'/usr/share/doc/(?:[^/]+/)+copyright', path)
                or re.fullmatch(r'/usr/share/common-licenses/[^/]+', path)
                or re.fullmatch(r'/usr/local/lib/python[0-9.]+/site-packages/(?:[^/]+/)*'
                                r'(?i:licen[sc]e|notice|copying|copyright)(?:\.[^/]+)?', path))


def validate_notices(rows):
    require(isinstance(rows, list) and len(rows) <= 10000, 'image_inventory_notice_files_refused')
    seen = set()
    total = 0
    for row in rows:
        require(isinstance(row, dict) and set(row) == {'path', 'sha256', 'size', 'base64'}
                and notice_path(row['path']) and row['path'] not in seen
                and type(row['size']) is int and 0 <= row['size'] <= NOTICE_LIMIT
                and isinstance(row['base64'], str) and len(row['base64']) <= (NOTICE_LIMIT + 2) // 3 * 4,
                'image_inventory_notice_files_refused')
        try:
            raw = base64.b64decode(row['base64'], validate=True)
        except ValueError:
            raise CheckError('image_inventory_notice_encoding_refused') from None
        require(base64.b64encode(raw).decode() == row['base64'] and len(raw) == row['size']
                and digest(raw) == row['sha256'], 'image_inventory_notice_bytes_changed')
        total += len(raw)
        require(total <= LIMIT, 'image_inventory_notice_files_too_large')
        seen.add(row['path'])


def notice_files(native):
    rows, expected = [], set()
    # Installed Python RECORD entries independently expose missing captures.
    # Do not invent required notices for packages whose metadata lists none.
    for artifact in native['artifacts']:
        metadata = artifact.get('metadata', {})
        root = metadata.get('sitePackagesRootPath')
        if artifact.get('type') == 'python' and isinstance(root, str):
            for entry in metadata.get('files', []):
                if isinstance(entry.get('path'), str):
                    path = posixpath.normpath(posixpath.join(root, entry['path']))
                    if notice_path(path):
                        expected.add(path)
    for item in native.get('files', []):
        path = item.get('location', {}).get('path')
        if notice_path(path):
            expected.add(path)
        if 'contents' not in item:
            continue
        content = item['contents']
        require(notice_path(path) and isinstance(content, str)
                and len(content) <= (NOTICE_LIMIT + 2) // 3 * 4, 'image_inventory_notice_files_refused')
        try:
            raw = base64.b64decode(content, validate=True)
        except ValueError:
            raise CheckError('image_inventory_notice_encoding_refused') from None
        hashes = {entry['algorithm']: entry['value'] for entry in item.get('digests', [])}
        require('sha256' not in hashes or hashes['sha256'] == digest(raw),
                'image_inventory_notice_bytes_changed')
        rows.append({'path': path, 'sha256': digest(raw), 'size': len(raw), 'base64': content})
    validate_notices(rows)
    require(expected == {row['path'] for row in rows}, 'image_inventory_notice_content_missing')
    return sorted(rows, key=lambda row: row['path'])


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


def conversion_equal(observed, converted):
    """Use Syft's own license/format conversion, not a home-made SPDX parser.

    Only its documented newly generated UUID/timestamp may differ. Package,
    license-expression grouping, URLs, files, dependencies and all other
    metadata remain in this comparison.
    """
    values = []
    for original in (observed, converted):
        value = deepcopy(original)
        serial = value.pop('serialNumber')
        require(isinstance(serial, str) and str(UUID(serial)).lower() == serial.removeprefix('urn:uuid:')
                and serial.startswith('urn:uuid:'), 'image_inventory_bom_serial_refused')
        timestamp = datetime.fromisoformat(value['metadata'].pop('timestamp'))
        require(timestamp.tzinfo is not None, 'image_inventory_bom_timestamp_refused')
        values.append(value)
    require(values[0] == values[1], 'image_inventory_conversion_disagrees')


def validate(native, cdx, identity, expected, *, converted):
    conversion_equal(cdx, converted)
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
    require(Counter(key(row, 'native') for row in artifacts) == Counter(key(row, 'cyclonedx') for row in components),
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
            'conversion_verified': True, 'licenses_reviewed': False, 'release_approved': False}


def license_review(native, identity, *, native_sha256, sbom_sha256):
    """Keep exact available texts without publishing native image configuration.

    Syft 1.56's CycloneDX conversion omits license contents even when the
    scanner retained them. This companion is evidence, never a legal verdict.
    Declared SPDX names can still have no original text; retain that distinction.
    """
    require(native['descriptor']['configuration'].get('licenses', {}).get('include-content') == 'all',
            'image_inventory_license_content_disabled')
    texts, packages = {}, []
    for artifact in native['artifacts']:
        licenses = artifact.get('licenses', [])
        require(isinstance(licenses, list) and len(licenses) <= 256,
                'image_inventory_license_records_refused')
        records = []
        for item in licenses:
            require(isinstance(item, dict), 'image_inventory_license_records_refused')
            value, expression = item.get('value'), item.get('spdxExpression', '')
            kind, content = item.get('type'), item.get('contents')
            require(isinstance(value, str) and 0 < len(value) <= 65536
                    and isinstance(expression, str) and len(expression) <= 65536
                    and kind in {'declared', 'concluded'}
                    and (content is None or isinstance(content, str) and 0 < len(content.encode()) <= LIMIT),
                    'image_inventory_license_records_refused')
            text_hash = digest(content.encode()) if content is not None else None
            if value.startswith('sha256:'):
                require(text_hash is not None and value == 'sha256:' + text_hash,
                        'image_inventory_license_text_missing_or_changed')
            if text_hash is not None:
                texts[text_hash] = content
            records.append({'value': value, 'spdx_expression': expression,
                            'type': kind, 'text_sha256': text_hash})
        packages.append({'name': artifact['name'], 'version': artifact['version'],
                         'type': artifact['type'], 'purl': artifact.get('purl') or None,
                         'licenses': records})
    result = {'schema': LICENSE_REVIEW, **identity, 'native_report_sha256': native_sha256,
              'sbom_sha256': sbom_sha256, 'packages': packages, 'texts': texts,
              'notice_files': notice_files(native),
              'licenses_reviewed': False, 'release_approved': False}
    require(len(json.dumps(result, ensure_ascii=True).encode()) <= LIMIT,
            'image_inventory_license_review_too_large')
    return result


def read_license_review(path, receipt):
    raw = read(path, LIMIT)
    require(digest(raw) == receipt['license_review_sha256'], 'image_inventory_saved_licenses_changed')
    value = json_bytes(raw)
    subject = ('image_id', 'revision', 'architecture', 'nonce', 'profile')
    require(set(value) == {'schema', *subject, 'native_report_sha256', 'sbom_sha256',
                          'packages', 'texts', 'notice_files', 'licenses_reviewed', 'release_approved'}
            and value['schema'] == LICENSE_REVIEW and value['licenses_reviewed'] is False
            and value['release_approved'] is False and value['sbom_sha256'] == receipt['sbom_sha256']
            and all(value[key] == receipt[key] for key in subject)
            and re.fullmatch('[0-9a-f]{64}', value['native_report_sha256'])
            and len(value['packages']) == receipt['package_count']
            and type(receipt['license_texts_retained']) is int
            and len(value['texts']) == receipt['license_texts_retained']
            and type(receipt['license_text_bytes']) is int
            and sum(len(text.encode()) for text in value['texts'].values()) == receipt['license_text_bytes']
            and all(digest(text.encode()) == sha for sha, text in value['texts'].items()),
            'image_inventory_saved_license_review_refused')
    references = set()
    for package in value['packages']:
        require(set(package) == {'name', 'version', 'type', 'purl', 'licenses'},
                'image_inventory_saved_license_review_refused')
        for item in package['licenses']:
            require(set(item) == {'value', 'spdx_expression', 'type', 'text_sha256'}
                    and item['type'] in {'declared', 'concluded'},
                    'image_inventory_saved_license_review_refused')
            sha = item['text_sha256']
            if sha is not None:
                require(sha in value['texts'], 'image_inventory_license_reference_missing')
                references.add(sha)
            if item['value'].startswith('sha256:'):
                require(sha is not None and item['value'] == 'sha256:' + sha,
                        'image_inventory_license_text_missing_or_changed')
    require(references == set(value['texts']), 'image_inventory_license_text_unreferenced')
    validate_notices(value['notice_files'])
    return value


def publish_catalog(path, raw):
    """Create-only bounded inventory bytes; small control receipts keep their own limit."""
    require(isinstance(raw, bytes) and 0 < len(raw) <= LIMIT, 'image_inventory_publication_too_large')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as output:
        output.write(raw)
        output.flush()
        os.fsync(output.fileno())
    fd = os.open(Path(path).parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    require(read(path, LIMIT) == raw, 'image_inventory_publication_uncertain')


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
        converted = directory / 'converted.json'
        # The pinned converter handles SPDX expression grouping and URL/name
        # representation. It reads only the existing native report, not images,
        # and must reproduce the full public report except fresh UUID/time.
        command([str(tool), 'convert', str(native), '--config', str(config), '--quiet',
                 '--output', 'cyclonedx-json=' + str(converted)], env=env, limit=65536, timeout=60)
        public, native_data = json_file(cdx), json_file(native)
        receipt = validate(native_data, public, identity, expected, converted=json_file(converted))
        target = Path(args.results) / (args.profile + '-sbom.cdx.json')
        raw = read(cdx, LIMIT)
        review = license_review(native_data, identity, native_sha256=digest(read(native, LIMIT)),
                                sbom_sha256=digest(raw))
        # Publish full standard metadata only after validating both outputs.
        publish_catalog(target, raw)
        review_path = Path(args.results) / (args.profile + '-licenses.json')
        publish_catalog(review_path, (json.dumps(review, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode())
        require(json_file(review_path) == review, 'image_inventory_publication_uncertain')
        publish(Path(args.results) / (args.profile + '-inventory.json'), receipt | {
            'sbom_sha256': digest(raw), 'license_review_sha256': digest(read(review_path, LIMIT)),
            'license_texts_retained': len(review['texts']),
            'license_text_bytes': sum(len(text.encode()) for text in review['texts'].values())})


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
                  'operating_system', 'packages_without_purl', 'conversion_verified',
                  'license_review_sha256', 'license_texts_retained', 'license_text_bytes'}
        require(set(value) == fields and value['schema'] == RECEIPT and value['revision'] == revision
                and value['architecture'] == architecture and value['nonce'] == nonce and value['profile'] == profile
                and IMAGE_ID.fullmatch(value['image_id']) and value['scanner'] == 'syft'
                and value['scanner_version'] == VERSION and value['licenses_reviewed'] is False
                and value['release_approved'] is False
                and value['conversion_verified'] is True
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
        read_license_review(results / (profile + '-licenses.json'), value)
        records[profile] = value
    require(records['aikey']['image_id'] != records['aiport']['image_id'], 'image_inventory_cross_profile_image')
    return {'schema': 'aikey-image-inventory-summary/2', 'revision': revision, 'architecture': architecture,
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

"""Explicit operator commands for the bounded migration library.

Never stops/starts a service, edits controller/device config or grants a camera.
A participating stopped runtime and a verified backup are required for swaps.
Credentials are read privately and kept out of argv/output. Unknown commit or
native outcomes are not retried or automatically rolled back.
"""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import stat
import time

from .index_authority import IndexAuthority
from .index_native import NativeIndexSession
from .index_readback import load_readback_plan
from .index_rebuild import ClipEmbedder, PgStore, Rebuild, _latest_backup
from .index_source_export import export_crop_archive
from .index_sources import ObjectCropArchive


class IndexCommandError(RuntimeError):
    pass


def _require(args, *names):
    if any(getattr(args, name, None) is None for name in names):
        raise IndexCommandError('Required migration admission options are missing')


def _connection(args):
    try:
        import psycopg
    except ImportError:
        raise IndexCommandError('Install the optional database dependency') from None
    options = {'dbname': args.database_name, 'user': args.database_user,
               'connect_timeout': 5, 'application_name': 'aikey-index-migration',
               'options': '-c statement_timeout=15000 -c lock_timeout=5000'}
    if args.database_socket is not None:
        if not args.database_socket.is_absolute() or args.database_host is not None:
            raise IndexCommandError('Supply one explicit database transport')
        options['host'] = str(args.database_socket)
    else:
        _require(args, 'database_host', 'database_ca', 'database_password_file')
        try:
            address = ipaddress.ip_address(args.database_host)
            if not address.is_private or not 1 <= args.database_port <= 65535:
                raise ValueError
        except ValueError:
            raise IndexCommandError('The database transport must be local and TLS verified') from None
        from .database import _read_secret
        options.update(host=str(address), port=args.database_port, sslmode='verify-full',
                       sslrootcert=str(args.database_ca), password=_read_secret(args.database_password_file))
    return lambda: psycopg.connect(**options)


def _config(args):
    _require(args, 'config')
    from .config import load_startup_config
    config, _ = load_startup_config(args.config)
    if Path(config['runtime']['state_dir']).resolve() != args.state_dir.resolve():
        raise IndexCommandError('The selected Key config does not belong to this state directory')
    if getattr(args, 'space', 'clip-basic-v1') == 'e5-session-v1':
        from .index_sources import _ID
        continuous = config.get('worker', {}).get('continuous')
        cameras = continuous.get('camera_ids') if isinstance(continuous, dict) else None
        if (not isinstance(cameras, list) or not cameras or len(cameras) > 256
                or any(not isinstance(x, str) or not _ID.fullmatch(x) for x in cameras)
                or len(set(cameras)) != len(cameras)):
            raise IndexCommandError('Session source export requires the existing explicit caption camera scope')
        return config, frozenset(cameras)
    from .clip import validate_find_anything_config
    finding = validate_find_anything_config(config.get('find_anything'))
    if not finding['index_camera_ids']:
        raise IndexCommandError('No existing camera is authorized for Find Anything indexing')
    return config, frozenset(finding['index_camera_ids'])


def _native(args):
    _require(args, 'native_host', 'native_username_file', 'native_password_file',
             'native_trust_file', 'native_cert_file')
    return NativeIndexSession(args.native_host, username_file=args.native_username_file,
        password_file=args.native_password_file, trust_file=args.native_trust_file,
        cert_file=args.native_cert_file)


def _plan(args):
    _require(args, 'readback_plan')
    value = Rebuild._read(args.readback_plan)
    return load_readback_plan(value)


def _source(args, store, cameras):
    _require(args, 'source_archive', 'source_manifest_sha256')
    source_class = ObjectCropArchive
    if getattr(args, 'space', 'clip-basic-v1') == 'e5-session-v1':
        from .index_sessions import SessionTextArchive
        source_class = SessionTextArchive
    return source_class(args.source_archive, manifest_sha256=args.source_manifest_sha256,
                              authorized_cameras=cameras, association=store.source_association)


def _backup(args, run, *, clock=time.monotonic):
    """Rehash exact previously scratch-verified bytes; not a new restore drill."""
    _require(args, 'backups')
    now = time.time()
    summary = _latest_backup(args.backups, run.store.live_counts()['rows'], now, space=run.profile_kind)
    if (not summary['verified'] or summary['matches_live'] is not True
            or summary['reason'] or summary['age_s'] > 3600):
        raise IndexCommandError('A fresh scratch-verified matching backup is required')
    directory = os.open(args.backups, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        value, _ = Rebuild._record(summary['latest'], dir_fd=directory)
        if isinstance(value, dict) and run.profile_kind == 'e5-session-v1' and 'session_profile' in value:
            selected_profile = value['session_profile']
        else:
            selected_profile = value.get('profile') if isinstance(value, dict) else None
        if (not isinstance(value, dict) or not isinstance(value.get('sha256'), str)
                or len(value['sha256']) != 64 or type(value.get('bytes')) is not int
                or not 0 < value['bytes'] <= 64*1024**3
                or not isinstance(selected_profile, dict)
                or selected_profile.get('revision') != run.journal['from_revision']):
            raise IndexCommandError('The verified backup has no matching prior encoder profile')
        previous = run._read(run.live_profile)
        if previous == run.target_profile and run._read(run.directory/'previous-profile.json') is not None:
            previous = run._read(run.directory / 'previous-profile.json')
        if previous != selected_profile:
            raise IndexCommandError('The verified backup encoder identity differs from this generation')
        # The read-only summary validated the exact matching dump basename.
        if value.get('dump') != summary['latest'][:-5]+'.dump':
            raise IndexCommandError('The verified backup changed during admission')
        descriptor = os.open(value['dump'], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=directory)
        with os.fdopen(descriptor, 'rb') as stream:
            before = os.fstat(stream.fileno())
            if (not stat.S_ISREG(before.st_mode) or before.st_size != value['bytes'] or before.st_nlink != 1
                    or before.st_uid != os.geteuid() or before.st_mode & 0o077):
                raise IndexCommandError('The verified backup dump is unavailable or changed')
            digest, deadline = hashlib.sha256(), clock()+300
            while block := stream.read(1024*1024):
                if clock() >= deadline:
                    raise IndexCommandError('The backup digest-check deadline expired')
                digest.update(block)
            after = os.fstat(stream.fileno())
        if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) !=
                (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                or digest.hexdigest() != value['sha256']):
            raise IndexCommandError('The verified backup dump digest changed')
        # Re-read the exact manifest; another verify/replacement must be reconciled.
        latest, _ = Rebuild._record(summary['latest'], dir_fd=directory)
        if latest != value:
            raise IndexCommandError('The backup manifest changed during admission')
        return {'verified': True, 'counts': value['counts'], 'dump_sha256_rechecked': True}
    finally:
        os.close(directory)


async def run(args):
    # Validate argument-dependent authority before any database/native/model call.
    from .encoding_identity import clip_encoding, TEXT_ENCODING
    text_space = getattr(args, 'space', 'clip-basic-v1') == 'e5-session-v1'
    policy = getattr(args, 'target_text_policy', 'literal')
    if text_space and policy != 'literal':
        raise IndexCommandError('CLIP text policy cannot be used for E5')
    encoding = TEXT_ENCODING if text_space else clip_encoding(policy)
    if args.command == 'export':
        _require(args, 'output')
        _, cameras = _config(args)
        if not text_space:
            native = _native(args)
    else:
        _require(args, 'target_revision', 'target_server')
        from .clip import _local_server
        from .index_text_encoder import text_endpoint
        if text_space:
            target_source = text_endpoint(args.target_server)
        else:
            target_source = args.target_server
        if not text_space and not _local_server(args.target_server):
            raise IndexCommandError('The target paired encoder must be an explicit local server')
        if args.command in {'stage', 'cutover'}:
            _, cameras = _config(args)
            _require(args, 'source_archive', 'source_manifest_sha256')
        if args.command in {'qualify', 'cutover', 'readback'}:
            plan = _plan(args)
        if args.command == 'readback':
            _require(args, 'runtime_url')
            native = _native(args)
    from .index_session_store import SessionPgStore
    from .index_text_rebuild import SessionRebuild
    from .index_text_encoder import TextEmbedder
    store = (SessionPgStore if text_space else PgStore)(_connection(args))
    if args.command == 'export' and text_space:
        from .index_sessions import export_session_archive
        return export_session_archive(args.output, store.source_rows(), authorized_cameras=cameras)
    if args.command == 'export':
        # No model or live-index write; terminal receipt follows logout acknowledgement.
        async with native:
            receipt = await export_crop_archive(args.output, store.source_rows(), native,
                authorized_cameras=cameras, max_bytes=args.max_export_bytes, timeout=args.timeout)
        return dict(receipt, logout_acknowledged=native.logout_acknowledged)
    generation = getattr(args, 'generation', None)
    if args.command == 'stage' and generation is None:
        generation = hashlib.sha256((args.target_revision+'\0'+target_source+'\0'
            +args.source_manifest_sha256+'\0'+args.space+'\0'+json.dumps(
                encoding, sort_keys=True)).encode()).hexdigest()[:12]
    migration = (SessionRebuild if text_space else Rebuild)(store,
        args.state_dir/'session-search' if text_space else args.state_dir,
        target_revision=args.target_revision, target_source=target_source, generation=generation,
        **({} if text_space else {"target_encoding": encoding}))
    if args.command == 'verify':
        return migration.verify()
    if args.command in {'stage', 'qualify'}:
        encoder = (TextEmbedder if text_space else ClipEmbedder)(target_source, args.target_revision,
            **({} if text_space else {"encoding_identity": encoding}))
        try:
            if args.command == 'qualify':
                return await migration.qualify(encoder, plan)
            with _source(args, store, cameras) as source:
                return dict(await migration.stage(encoder, source, batch=args.batch,
                                             max_batches=args.max_batches), generation=migration.tag)
        finally:
            await encoder.close()
    if args.command == 'cutover':
        with IndexAuthority(args.state_dir) as authority:
            backup = _backup(args, migration)
            with _source(args, store, cameras) as source:
                return migration.cutover(backup=backup, key_quiesced=authority.check,
                                          source=source, readback_plan=plan)
    if args.command == 'rollback':
        with IndexAuthority(args.state_dir) as authority:
            previous_server = getattr(args, 'previous_server', None)
            if previous_server is None:
                return await migration.rollback(key_quiesced=authority.check)
            _, cameras = _config(args)
            previous = migration._read(migration.directory/'previous-profile.json')
            if not isinstance(previous, dict):
                raise IndexCommandError('The retained previous encoder profile is unavailable')
            from .encoding_identity import UPGRADABLE_ENCODING
            encoding = {k: previous.get(k) for k in UPGRADABLE_ENCODING}
            encoder = (TextEmbedder if text_space else ClipEmbedder)(previous_server,
                previous['revision'], encoding_identity=encoding)
            try:
                with _source(args, store, cameras) as source:
                    return await migration.rollback(key_quiesced=authority.check,
                                                      previous_embedder=encoder, source=source)
            finally:
                await encoder.close()
    if args.command == 'readback':
        from .admin_runtime import RuntimeObserver
        observer = RuntimeObserver({'key': ('aikey', args.state_dir)}, {'key': args.runtime_url})
        async with native:
            await migration.confirm_native(plan, native,
                                            runtime=lambda: (observer.read_session_identity('key') if text_space
                                                             else observer.read_search_identity('key')))
        return migration.finalize_native(logout_acknowledged=native.logout_acknowledged)
    raise IndexCommandError('The migration command is unsupported')


def add_arguments(parser):
    parser.add_argument('--config', type=Path, help='Existing Key config; camera scope is never expanded')
    parser.add_argument('--database-socket', type=Path)
    parser.add_argument('--database-host')
    parser.add_argument('--database-port', type=int, default=5432)
    parser.add_argument('--database-name', choices=['unifi-protect', 'aikey_rebuild_test'], default='unifi-protect')
    parser.add_argument('--database-user', choices=['unifi-protect', 'postgres'], default='unifi-protect')
    parser.add_argument('--database-ca', type=Path)
    parser.add_argument('--database-password-file', type=Path)
    parser.add_argument('--target-revision')
    parser.add_argument('--target-server')
    from .encoding_identity import CLIP_TEXT_POLICIES
    parser.add_argument('--target-text-policy', choices=CLIP_TEXT_POLICIES, default='literal')
    parser.add_argument('--source-archive', type=Path)
    parser.add_argument('--source-manifest-sha256')
    parser.add_argument('--readback-plan', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--batch', type=int, default=32)
    parser.add_argument('--max-batches', type=int)
    parser.add_argument('--max-export-bytes', type=int, default=512*1024*1024)
    parser.add_argument('--timeout', type=int, default=600)
    parser.add_argument('--native-host')
    parser.add_argument('--native-username-file', type=Path)
    parser.add_argument('--native-password-file', type=Path)
    parser.add_argument('--native-trust-file', type=Path)
    parser.add_argument('--native-cert-file', type=Path)
    parser.add_argument('--previous-server', help='Explicit retained old encoder for newly indexed rows during rollback')
    parser.add_argument('--runtime-url', help='Explicit pinned Key HTTPS health endpoint')


def execute(args):
    try:
        result = asyncio.run(run(args))
    except Exception as exc:
        # No raw exception/driver/native/provider response or path in CLI output.
        print(json.dumps({'error': 'index_migration_refused_or_uncertain',
                          'category': type(exc).__name__, 'retry_automatically': False}))
        return 2
    print(json.dumps(result, allow_nan=False, sort_keys=True))
    return 0

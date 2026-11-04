"""Durable NAS apply intent. Uncertain daemon effects fence all later starts.

No data restore, deletion, automatic adoption, or service-name rollback.
"""
from contextlib import ExitStack
from copy import deepcopy
import hashlib
import json
import os
import re
from pathlib import Path
import secrets
import time

from . import host_activation as activation
from .aiport_config_store import AiPortConfigurationStore
from .config import atomic_private
from .host_control_store import writer, private_directory
from .private_files import read_private
from .protocol import _json_object

SCHEMA = 'aikey-nas-reconcile/1'


def encode(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)+'\n').encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def publish(directory, receipt):
    from .aiport_nas_reconcile import ReconcileError
    raw = encode(receipt)
    atomic_private(directory / 'nas-reconcile.json', raw)
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    if read_private(directory / 'nas-reconcile.json', 262144) != raw:
        raise ReconcileError('nas_intent_publication_uncertain')


def read(directory):
    from .aiport_nas_reconcile import ReconcileError
    try:
        value = _json_object(read_private(directory / 'nas-reconcile.json', 262144))
    except FileNotFoundError:
        return None
    if (set(value) != {'schema', 'operation', 'revision', 'phase', 'before', 'actions', 'failure'}
            or value['schema'] != SCHEMA
            or value['phase'] not in ('prepared', 'completed', 'uncertain', 'rolled_back', 'reconciled')
            or not isinstance(value['operation'], str) or not re.fullmatch('[0-9a-f]{32}', value['operation'])
            or not isinstance(value['revision'], str) or not re.fullmatch('[0-9a-f]{64}', value['revision'])
            or value['failure'] not in (None, 'operation_failed')
            or not isinstance(value['before'], dict) or not isinstance(value['actions'], list)
            or len(value['actions']) > 48):
        raise ReconcileError('nas_intent_invalid')
    from .host_runtime_command import checked_effect
    for name, row in value['before'].items():
        if (not isinstance(name, str) or not re.fullmatch('aiport_slot_(?:[1-9]|1[0-6])', name)
                or not isinstance(row, dict) or row.get('Service') != name
                or row.get('State') not in ('running', 'created', 'exited')
                or row.get('State') == 'running' and row.get('Readiness') not in ('connected', 'awaiting_adoption')
                or not isinstance(row.get('ID'), str) or not re.fullmatch('[0-9a-fA-F]{12,64}', row['ID'])):
            raise ReconcileError('nas_intent_invalid')
    for action in value['actions']:
        if (not isinstance(action, dict) or set(action) != {'name', 'action', 'state', 'id', 'effect'}
                or not isinstance(action['name'], str)
                or not re.fullmatch('aiport_slot_(?:[1-9]|1[0-6])', action['name'])
                or action['action'] not in ('create', 'start', 'stop') or action['state'] not in ('requested', 'observed')
                or action['id'] is not None and (not isinstance(action['id'], str)
                    or not re.fullmatch('[0-9a-fA-F]{12,64}', action['id']))
                or action['state'] == 'observed' and action['id'] is None):
            raise ReconcileError('nas_intent_invalid')
        checked_effect(action['effect'])
        if (action['effect']['operation'] != value['operation']
                or action['effect']['action'] != 'nas_' + action['action'] + '_' + action['name'].removeprefix('aiport_slot_')):
            raise ReconcileError('nas_intent_invalid')
    return value


def execute(manifest_path, manifest, states, *, host_state, verify, run=None,
            pause=time.sleep, settle=False, timeout=600):
    from . import aiport_nas_reconcile as nas
    from .apple_supervisor import _run as guarded
    if not callable(verify) or not 30 <= timeout <= 1800:
        raise nas.ReconcileError('nas_verified_inputs_required')
    directory = private_directory(host_state)
    roots = [Path(volume['source']).resolve() for service in manifest['services'].values()
             for volume in service['volumes']]
    if any(directory == root or directory in root.parents or root in directory.parents for root in roots):
        raise nas.ReconcileError('nas_authority_must_be_outside_guest_mounts')
    deadline = time.monotonic() + timeout
    with writer(directory) as (_, bound), ExitStack() as locks:
        for state in sorted(states.values()):
            locks.enter_context(AiPortConfigurationStore(Path(state)/'config.json',
                runtime_state_dir=Path('/state'))._locked(allow_host_pending=True))
        verify()
        configs = {name: digest(read_private(Path(state)/'config.json', 1024*1024))
                   for name, state in states.items()}
        from .aiport_nas_compose import _REQUIRED_STATE
        identities = {str(slot): {name: digest(read_private(Path(state)/name, 1024*1024))
                      for name in _REQUIRED_STATE if name != 'config.json'}
                      for slot, state in states.items()}
        revision = digest(encode({'manifest': manifest, 'configs': configs, 'identities': identities}))
        old = read(directory)
        if settle:
            if old is None or old['revision'] != revision:
                raise nas.ReconcileError('nas_original_intent_required')
        elif old is not None and old['phase'] in ('prepared', 'uncertain'):
            raise nas.ReconcileError('nas_pending_reconciliation_required')
        for state in states.values():
            pending = activation.read(Path(state)/'config.json')
            if pending and pending['phase'] == 'prepared' and (
                    not settle or old is None or pending['operation'] != old['operation']):
                raise nas.ReconcileError('nas_pending_activation_required')

        def current():
            bound()
            verify()
            if any(digest(read_private(Path(state)/'config.json', 1024*1024)) != configs[slot]
                   for slot, state in states.items()):
                raise nas.ReconcileError('nas_configuration_changed')
            if any(digest(read_private(Path(states[int(slot)])/name, 1024*1024)) != expected
                   for slot, files in identities.items() for name, expected in files.items()):
                raise nas.ReconcileError('nas_identity_changed')

        if old is not None and not settle:
            archive = directory / ('nas-intent-' + old['operation'] + '.json')
            raw_old = encode(old)
            if archive.exists():
                if read_private(archive, 262144) != raw_old:
                    raise nas.ReconcileError('nas_prior_intent_archive_changed')
            else:
                atomic_private(archive, raw_old)
        receipt = old if settle else {'schema': SCHEMA, 'operation': secrets.token_hex(16),
            'revision': revision, 'phase': 'prepared', 'before': {}, 'actions': [], 'failure': None}
        recipe = deepcopy(manifest)
        # All starts consume this exact private recipe, not the caller's mutable
        # pathname. Dollar interpolation syntax is refused before invoking Compose.
        for service in recipe['services'].values():
            service['labels'] = {'org.aikey.operation': receipt['operation']}
        recipe_path = directory / ('nas-recipe-' + receipt['operation'] + '.json')
        recipe_raw = encode(recipe)
        if b'$' in recipe_raw:
            raise nas.ReconcileError('nas_compose_interpolation_refused')
        if settle:
            if read_private(recipe_path, 2*1024*1024) != recipe_raw:
                raise nas.ReconcileError('nas_original_recipe_required')
        else:
            atomic_private(recipe_path, recipe_raw)

        def command(argv, limit=15, effect=None):
            bound()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise nas.ReconcileError('nas_reconcile_deadline')
            if read_private(recipe_path, 2*1024*1024) != recipe_raw:
                raise nas.ReconcileError('nas_recipe_changed')
            if run is not None:
                return run(argv, min(limit, remaining))
            code, output = guarded(['/usr/bin/docker', '--host', 'unix:///var/run/docker.sock', *argv[1:]], lease_fd=bound.lease_fd,
                root_fd=bound.root_fd if effect else None, effect=effect, timeout=min(limit, remaining))
            if code:
                raise nas.ReconcileError('nas_docker_command_failed')
            return output

        project = manifest['name']
        services = sorted(manifest['services'])
        def compose(*args):
            return nas._compose(recipe_path, project, *args)
        def rows():
            return nas._rows(command(compose('ps', '--all', '--format', 'json')), services)
        network = nas._network_name(manifest)
        command(compose('config', '--quiet'))
        nas._verify_external_network(command, manifest)
        health_network = nas._verify_health_network(command, manifest)
        def inspect(name, row):
            nas._inspect(command, row, manifest['services'][name], network, health_network)
        def health(name):
            row = rows().get(name)
            if not row or row['State'] != 'running':
                raise nas.ReconcileError('nas_health_container_not_running')
            actions = [a for a in receipt['actions'] if a['name'] == name]
            if actions:
                owned(name, row, actions[-1])
            elif name in before and row['ID'] != before[name]['ID']:
                raise nas.ReconcileError('nas_health_container_changed')
            inspect(name, row)
            def by_id(argv, limit):
                index = argv.index('exec')
                return command(['docker', 'exec', row['ID'], *argv[index+3:]], limit)
            return nas._health(by_id, recipe_path, name,
                states[int(name.removeprefix('aiport_slot_'))], project)
        before = rows()
        for name, row in before.items():
            inspect(name, row)
        def preserved():
            observed = rows()
            for name, original in receipt['before'].items():
                if original['State'] == 'running':
                    row = observed.get(name)
                    if not row or row['ID'] != original['ID'] or row['State'] != 'running':
                        raise nas.ReconcileError('nas_prior_runtime_changed')
                    inspect(name, row)
                    status = nas._readiness(health(name))
                    if status == 'controller_disconnected' or original.get('Readiness') == 'connected' and status != 'connected':
                        raise nas.ReconcileError('nas_prior_runtime_disconnected')

        def reservation(slot, phase):
            return {'schema': activation.SCHEMA, 'operation': receipt['operation'],
                'authority': digest(str(directory).encode()), 'revision': revision,
                'configuration_revision': configs[slot], 'phase': phase}
        def finish():
            current()
            for slot, state in states.items():
                value = activation.read(Path(state)/'config.json')
                if value and value['phase'] == 'prepared':
                    if value != reservation(slot, 'prepared'):
                        raise nas.ReconcileError('nas_reservation_owner_changed')
                    activation.publish(Path(state)/'config.json', reservation(slot, 'settled'))

        def owned(name, row, action):
            if action['id'] is not None and row['ID'] != action['id']:
                raise nas.ReconcileError('nas_original_container_required')
            inspect(name, row)
            original = receipt['before'].get(name)
            if original:
                if row['ID'] != original['ID']:
                    raise nas.ReconcileError('nas_original_container_required')
            else:
                labels = _json_object(command(['docker', 'inspect', '--format',
                    '{{json .Config.Labels}}', row['ID']]).encode())
                if labels.get('org.aikey.operation') != receipt['operation']:
                    raise nas.ReconcileError('nas_started_container_not_owned')

        def mutate(name, kind, argv, container_id=None):
            from .host_runtime_command import argv_digest
            current()
            info = os.fstat(bound.root_fd)
            effect = {'operation': receipt['operation'],
                'action': 'nas_' + kind + '_' + name.removeprefix('aiport_slot_'),
                'authority': digest(str(directory).encode()),
                'authority_device': info.st_dev, 'authority_inode': info.st_ino,
                'argv_sha256': argv_digest(['/usr/bin/docker', '--host', 'unix:///var/run/docker.sock', *argv[1:]])}
            action = {'name': name, 'action': kind, 'state': 'requested',
                'id': container_id, 'effect': effect}
            receipt['actions'].append(action)
            publish(directory, receipt)
            command(argv, 30 if kind == 'stop' else 90, effect)
            observed = rows()
            row = observed.get(name)
            if not row or (row['State'] == 'running') != (kind == 'start'):
                raise nas.ReconcileError('nas_mutation_postcondition_missing')
            owned(name, row, action)
            action.update(state='observed', id=row['ID'])
            publish(directory, receipt)
            return action

        if settle:
            from .host_runtime_command import read_effect
            preserved()
            for action in receipt['actions']:
                row = before.get(action['name'])
                if not row:
                    raise nas.ReconcileError('nas_absence_cannot_settle_late_request')
                owned(action['name'], row, action)
                if action['state'] != 'observed':
                    effect = read_effect(directory, action['effect'])
                    if (effect is None or not effect['command_terminal']
                            or (row['State'] == 'running') != (action['action'] == 'start')):
                        raise nas.ReconcileError('nas_terminal_effect_and_postcondition_required')
            # Observe health of all currently running owned instances too.
            for name, row in before.items():
                if row['State'] == 'running' and nas._readiness(health(name)) == 'controller_disconnected':
                    raise nas.ReconcileError('nas_runtime_disconnected')
            receipt['phase'] = 'reconciled'
            publish(directory, receipt)
            finish()
            return {'mode': 'reconciled', 'daemon_mutations': 0}

        receipt['before'] = before
        running = [name for name, row in before.items() if row['State'] == 'running']
        pending = [name for name in services if name not in running]
        for name in running:
            before[name]['Readiness'] = nas._readiness(health(name))
        preserved()
        if not pending:
            return {'mode': 'applied', 'preserved': running, 'started': []}
        # Only new starts require the durable startup-byte reservation contract.
        # Existing older images are observed, never silently upgraded.
        for image in {manifest['services'][name]['image'] for name in pending}:
            labels = _json_object(command(['docker', 'image', 'inspect', '--format',
                '{{json .Config.Labels}}', image]).encode())
            if labels.get(activation.IMAGE_LABEL) != '1':
                raise nas.ReconcileError('nas_image_missing_activation_contract')
        publish(directory, receipt)
        operations_complete = False
        statuses = {}
        try:
            for name in pending:
                current()
                preserved()
                slot = int(name.removeprefix('aiport_slot_'))
                activation.publish(Path(states[slot])/'config.json', reservation(slot, 'prepared'))
                latest = rows()
                original = before.get(name)
                row = latest.get(name)
                if (row is None) != (original is None) or row is not None and (
                        row['ID'] != original['ID'] or row['State'] != original['State']):
                    raise nas.ReconcileError('nas_target_changed_before_mutation')
                if row is None:
                    created = mutate(name, 'create', compose('create', '--no-recreate',
                        '--no-build', '--pull', 'never', name))
                    container_id = created['id']
                else:
                    inspect(name, row)
                    container_id = row['ID']
                mutate(name, 'start', ['docker', 'start', container_id], container_id)
                for attempt in range(nas._HEALTH_ATTEMPTS):
                    try:
                        statuses[name] = nas._readiness(health(name))
                        if statuses[name] == 'controller_disconnected':
                            raise nas.ReconcileError('nas_started_runtime_disconnected')
                        break
                    except nas.ReconcileError:
                        if attempt == nas._HEALTH_ATTEMPTS - 1:
                            raise
                        pause(nas._HEALTH_PAUSE_SECONDS)
                preserved()
            current()
            operations_complete = True
            receipt['phase'] = 'completed'
            publish(directory, receipt)
            finish()
            return {'mode': 'applied', 'preserved': running, 'started': pending, 'readiness': statuses}
        except BaseException:
            # SIGKILL retains prepared intent; never guess after a lost ACK.
            receipt['phase'] = 'uncertain'
            receipt['failure'] = 'operation_failed'
            publish(directory, receipt)
            if not operations_complete and all(action['state'] == 'observed' for action in receipt['actions']):
                try:
                    for action in list(reversed(receipt['actions'])):
                        if action['action'] != 'start':
                            continue
                        row = rows().get(action['name'])
                        if not row:
                            raise nas.ReconcileError('nas_rollback_container_missing')
                        owned(action['name'], row, action)
                        if row['State'] == 'running':
                            mutate(action['name'], 'stop',
                                ['docker', 'stop', '--time', '10', row['ID']], row['ID'])
                    preserved()
                    receipt['phase'] = 'rolled_back'
                    publish(directory, receipt)
                    finish()
                except BaseException:
                    receipt['phase'] = 'uncertain'
                    publish(directory, receipt)
            raise

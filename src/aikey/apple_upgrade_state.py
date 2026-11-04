"""Private durable upgrade intent, shared by the actor and supervisor fence.

This records authority and requested/acknowledged/observed transitions. It does
not infer absence from a failed command or turn a retained failure into retry
permission. Callers hold the shared host writer while publishing transitions.
"""
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import re
import secrets

from .config import atomic_private
from .private_files import read_private as _private
from .host_control_store import private_directory
from .protocol import _json_object

FILE = 'upgrade.json'
SCHEMA = 'aikey-apple-upgrade/1'
NORMAL_TERMINAL = {'committed', 'rolled_back'}
TERMINAL = NORMAL_TERMINAL | {'reconciled'}
PHASES = TERMINAL | {'prepared', 'switching', 'rolling_back', 'uncertain'}
ACTIONS = {'hold', 'stop_old', 'run_new', 'create_new', 'start_new', 'stop_new',
           'delete_new', 'restart_old', 'pin_new', 'pin_old', 'ready_new', 'ready_old', 'new_absent'}
FAILURES = {None, 'operation_failed', 'rollback_incomplete', 'publication_uncertain', 'interrupted_or_unsettled'}
HASH = re.compile('[0-9a-f]{64}\\Z')
TOKEN = re.compile('[0-9a-f]{32}\\Z')
NAME = re.compile('[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\\Z')
LIMIT = 65536
RECOVERY_ACTIONS = {'hold', 'stop_new', 'delete_new', 'restart_old', 'ready_old', 'pin_old', 'new_absent'}
FAILURE_CODES = {'upgrade_outcome_uncertain', 'hold_failed', 'stop_failed', 'stop_outcome_uncertain',
    'candidate_readiness_unverified', 'upgrade_configuration_changed',
    'run_outcome_uncertain', 'rollback_outcome_uncertain', 'rollback_not_ready',
    'upgrade_registry_changed', 'upgrade_unreviewed_command', 'upgrade_command_outcome_unavailable',
    'upgrade_hold_not_observed', 'upgrade_pin_not_observed', 'host_command_nonzero_exit',
    'host_command_owner_lost', 'host_command_cancelled', 'host_command_timeout', 'host_command_output_limit',
    'host_command_exec_failed', 'host_command_output_refused', 'host_command_observation_failed',
    'host_command_guardian_deadline', 'host_command_effect_receipt_unavailable',
    'host_command_effect_context_refused', 'host_command_protocol_refused'}


class UpgradeStateError(ValueError):
    pass


def _require(condition):
    if not condition:
        raise UpgradeStateError('upgrade_intent_unavailable')


def _text(value, size=4096):
    return isinstance(value, str) and 0 < len(value) <= size and '\x00' not in value


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def checked(value):
    _require(isinstance(value, dict) and set(value) == {
        'schema', 'operation', 'authority', 'authority_device', 'authority_inode',
        'phase', 'plan', 'configuration_revision', 'registry_sha256', 'registry_before', 'actions', 'failure',
        'failure_code', 'failure_exit_code', 'recovery_actions', 'recovery_attempts', 'recovery_from', 'reconciliation'})
    _require(value['schema'] == SCHEMA and isinstance(value['operation'], str)
             and TOKEN.fullmatch(value['operation']) and value['phase'] in PHASES
             and isinstance(value['authority'], str) and HASH.fullmatch(value['authority'])
             and type(value['authority_device']) is int and value['authority_device'] >= 0
             and type(value['authority_inode']) is int and value['authority_inode'] > 0
             and isinstance(value['registry_sha256'], str) and HASH.fullmatch(value['registry_sha256'])
             and value['failure'] in FAILURES)
    _require((value['failure_code'] is None or value['failure_code'] in FAILURE_CODES)
             and (value['failure_exit_code'] is None or type(value['failure_exit_code']) is int
                  and -255 <= value['failure_exit_code'] <= 255))
    _require(value['configuration_revision'] is None or isinstance(value['configuration_revision'], str)
             and HASH.fullmatch(value['configuration_revision']))
    plan = value['plan']
    _require(isinstance(plan, dict) and set(plan) == {
        'service', 'old', 'new', 'image', 'argv', 'configuration_sha256', 'image_environment', 'image_workdir'})
    _require(all(isinstance(plan[key], str) and NAME.fullmatch(plan[key]) for key in ('service', 'old', 'new'))
             and plan['old'] != plan['new'] and _text(plan['image'], 256)
             and isinstance(plan['configuration_sha256'], str) and HASH.fullmatch(plan['configuration_sha256'])
             and isinstance(plan['argv'], list) and 4 <= len(plan['argv']) <= 256
             and all(_text(item) for item in plan['argv'])
             and isinstance(plan['image_environment'], list) and len(plan['image_environment']) <= 128
             and all(_text(item) for item in plan['image_environment'])
             and _text(plan['image_workdir']))
    _require(plan['argv'][:3] == ['/usr/local/bin/container', 'run', '-d']
             and plan['argv'][3:5] == ['--name', plan['new']])
    registry = value['registry_before']
    _require(isinstance(registry, dict) and type(registry.get('schema')) is int and registry['schema'] == 1
             and isinstance(registry.get('services'), list) and 1 <= len(registry['services']) <= 64)
    from .apple_supervisor import _service
    services = [_service(row) for row in registry['services']]
    _require(len({row.name for row in services}) == len(services)
             and len({row.container for row in services}) == len(services))
    selected = [row for row in services if row.name == plan['service']]
    _require(len(selected) == 1 and selected[0].container == plan['old'])
    _require(isinstance(value['actions'], list) and len(value['actions']) <= 24)
    for action in value['actions']:
        _require(isinstance(action, dict) and set(action) == {'action', 'state'}
                 and action['action'] in ACTIONS and action['state'] in {'requested', 'acknowledged', 'observed', 'failed'}
                 and (action['state'] != 'failed' or action['action'] == 'ready_new'))
    _require(len({row['action'] for row in value['actions']}) == len(value['actions']))
    if value['phase'] in NORMAL_TERMINAL:
        _require(value['actions'] and all(row['state'] == 'observed'
                 or value['phase'] == 'rolled_back' and row == {'action': 'ready_new', 'state': 'failed'}
                 for row in value['actions']))
        actions = {row['action'] for row in value['actions']}
        if value['phase'] == 'committed':
            _require({'hold', 'stop_old', 'ready_new', 'pin_new'} <= actions
                     and ('run_new' in actions or {'create_new', 'start_new'} <= actions)
                     and not actions & {'stop_new', 'delete_new', 'restart_old', 'pin_old'})
        else:
            _require({'hold', 'stop_old', 'restart_old', 'ready_old', 'pin_old', 'new_absent'} <= actions)
            if actions & {'run_new', 'start_new'}:
                _require({'stop_new', 'delete_new'} <= actions)
    _require(type(value['recovery_attempts']) is int and 0 <= value['recovery_attempts'] <= 3
             and (value['recovery_from'] is None or value['recovery_from'] in PHASES - TERMINAL)
             and isinstance(value['recovery_actions'], list) and len(value['recovery_actions']) <= 12)
    for action in value['recovery_actions']:
        _require(isinstance(action, dict) and set(action) == {'action', 'state'}
                 and action['action'] in RECOVERY_ACTIONS
                 and action['state'] in {'requested', 'acknowledged', 'observed'})
    _require(len({row['action'] for row in value['recovery_actions']}) == len(value['recovery_actions']))
    if value['recovery_attempts'] == 0:
        _require(value['recovery_from'] is None and not value['recovery_actions'])
    else:
        _require(value['recovery_from'] is not None and value['failure'] is not None)
    proof = value['reconciliation']
    if value['phase'] == 'reconciled':
        _require(isinstance(proof, dict) and set(proof) == {
            'outcome', 'old', 'new', 'readiness', 'old_configuration_sha256', 'registry_sha256',
            'guest_state_restored', 'original_failure_retained'})
        _require(proof['outcome'] == 'rolled_back' and proof['old'] == 'running' and proof['new'] == 'absent'
                 and proof['readiness'] == 'verified' and proof['old_configuration_sha256'] == plan['configuration_sha256']
                 and isinstance(proof['registry_sha256'], str) and HASH.fullmatch(proof['registry_sha256'])
                 and proof['guest_state_restored'] is False and proof['original_failure_retained'] is True
                 and value['recovery_attempts'] > 0)
        _require(all(row['state'] == 'observed' for row in value['recovery_actions'])
                 and {'hold', 'ready_old', 'pin_old', 'new_absent'} <= {row['action'] for row in value['recovery_actions']})
    else:
        _require(proof is None)
    _require(len(encoded(value)) <= LIMIT)
    return value


def read(directory):
    path = Path(directory) / FILE
    try:
        raw = _private(path, LIMIT)
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError, RuntimeError):
        raise UpgradeStateError('upgrade_intent_unavailable') from None
    try:
        value = checked(_json_object(raw))
        root = private_directory(directory)
        info = root.lstat()
        _require(value['authority'] == hashlib.sha256(str(root).encode()).hexdigest()
                 and (value['authority_device'], value['authority_inode']) == (info.st_dev, info.st_ino))
        return value
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise UpgradeStateError('upgrade_intent_unavailable') from None


def publish(directory, value, bound):
    checked(value)
    bound()
    previous = read(directory)
    if previous is not None and previous['phase'] not in TERMINAL:
        if (previous['operation'] != value['operation'] or previous['authority'] != value['authority']
                or previous['authority_device'] != value['authority_device']
                or previous['authority_inode'] != value['authority_inode']
                or previous['plan'] != value['plan']
                or previous['configuration_revision'] != value['configuration_revision']
                or previous['registry_sha256'] != value['registry_sha256']
                or previous['registry_before'] != value['registry_before']):
            raise UpgradeStateError('upgrade_intent_owned_elsewhere')
    atomic_private(Path(directory) / FILE, encoded(value))
    bound()
    if read(directory) != value:
        raise UpgradeStateError('upgrade_intent_publication_uncertain')


def pending(directory):
    value = read(directory)
    return value if value is not None and value['phase'] not in TERMINAL else None


def prepare(directory, planned, registry_raw, bound, *, configuration_revision=None):
    directory = private_directory(directory)
    previous = read(directory)
    if previous is not None and previous['phase'] not in TERMINAL:
        raise UpgradeStateError('upgrade_needs_original_recovery')
    if previous is not None:
        # A later attempt must not erase the terminal predecessor. Archive the
        # exact private bytes durably before replacing the current receipt.
        history = directory / 'upgrade-history'
        history.mkdir(mode=0o700, exist_ok=True)
        private_directory(history)
        raw = _private(directory / FILE, LIMIT)
        archived = history / (previous['operation'] + '.json')
        try:
            descriptor = os.open(archived, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            if _private(archived, LIMIT) != raw:
                raise UpgradeStateError('upgrade_history_changed') from None
        else:
            with os.fdopen(descriptor, 'wb') as output:
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())
        descriptor = os.open(history, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        bound()
    registry = _json_object(registry_raw)
    # Normalize tuple/list representation before durable comparison/read-back.
    plan = _json_object(encoded(asdict(planned)))
    info = directory.lstat()
    value = {'schema': SCHEMA, 'operation': secrets.token_hex(16), 'phase': 'prepared',
             'authority': hashlib.sha256(str(directory).encode()).hexdigest(),
             'authority_device': info.st_dev, 'authority_inode': info.st_ino,
             'plan': plan, 'registry_sha256': hashlib.sha256(registry_raw).hexdigest(),
             'configuration_revision': configuration_revision,
             'registry_before': registry, 'actions': [], 'failure': None,
             'failure_code': None, 'failure_exit_code': None,
             'recovery_actions': [], 'recovery_attempts': 0, 'recovery_from': None, 'reconciliation': None}
    publish(directory, value, bound)
    return value


def configuration_path(value):
    service = next(row for row in value['registry_before']['services'] if row['name'] == value['plan']['service'])
    return Path(service['state_source']) / 'config.json'


def reserve_configuration(value, *, settle=False):
    """Caller owns both host and existing profile configuration mutexes."""
    from . import host_activation as activation
    if value['configuration_revision'] is None:
        return  # Explicit synthetic/application adapters only, not native CLI.
    config = configuration_path(value)
    if hashlib.sha256(_private(config, 1024 * 1024)).hexdigest() != value['configuration_revision']:
        raise UpgradeStateError('upgrade_configuration_changed')
    expected = {'schema': activation.SCHEMA, 'operation': value['operation'],
                'authority': value['authority'], 'revision': hashlib.sha256(encoded(value['plan'])).hexdigest(),
                'configuration_revision': value['configuration_revision'], 'phase': 'prepared'}
    previous = activation.read(config)
    if previous is None and value['actions']:
        raise UpgradeStateError('upgrade_configuration_reservation_unavailable')
    if previous is not None and previous['phase'] == 'prepared' and previous != expected:
        raise UpgradeStateError('upgrade_configuration_owned_elsewhere')
    if (previous is not None and previous['phase'] == 'settled'
            and previous != dict(expected, phase='settled') and value['actions']):
        raise UpgradeStateError('upgrade_configuration_owned_elsewhere')
    if settle:
        if previous not in (expected, dict(expected, phase='settled')):
            raise UpgradeStateError('upgrade_configuration_reservation_unavailable')
        activation.publish(config, dict(expected, phase='settled'))
    else:
        activation.publish(config, expected)


def transition(directory, value, action, state, bound, *, reconciled=False):
    _require(value['phase'] not in TERMINAL and action in ACTIONS
             and state in {'requested', 'acknowledged', 'observed', 'failed'})
    previous = next((row for row in value['actions'] if row['action'] == action), None)
    order = {'requested': 0, 'acknowledged': 1, 'observed': 2, 'failed': 3}
    if previous is None:
        _require(state == 'requested')
        value['actions'].append({'action': action, 'state': state})
    else:
        _require(state == 'failed' and action == 'ready_new' and previous['state'] == 'requested'
                 or state != 'failed' and order[state] == order[previous['state']] + 1
                 or reconciled is True and previous['state'] == 'requested' and state == 'observed')
        previous['state'] = state
    value['phase'] = 'rolling_back' if action in {'stop_new', 'delete_new', 'restart_old', 'pin_old', 'ready_old', 'new_absent'} else 'switching'
    publish(directory, value, bound)


def finish(directory, value, phase, failure, bound, *, failure_code=None, failure_exit_code=None):
    _require(phase in NORMAL_TERMINAL | {'uncertain'} and failure in FAILURES)
    value['phase'], value['failure'] = phase, value['failure'] or failure
    value['failure_code'] = value['failure_code'] or failure_code
    if value['failure_exit_code'] is None:
        value['failure_exit_code'] = failure_exit_code
    publish(directory, value, bound)


def begin_recovery(directory, value, bound):
    _require(value['phase'] not in TERMINAL)
    if value['recovery_attempts'] >= 3:
        raise UpgradeStateError('upgrade_recovery_budget_exhausted')
    if value['recovery_from'] is None:
        value['recovery_from'] = value['phase']
    value['recovery_attempts'] += 1
    value['failure'] = value['failure'] or 'interrupted_or_unsettled'
    value['phase'] = 'rolling_back'
    publish(directory, value, bound)


def recovery_step(directory, value, action, state, bound):
    _require(value['phase'] not in TERMINAL and value['recovery_attempts'] > 0 and action in RECOVERY_ACTIONS
             and state in {'requested', 'acknowledged', 'observed'})
    previous = next((row for row in value['recovery_actions'] if row['action'] == action), None)
    order = {'requested': 0, 'acknowledged': 1, 'observed': 2}
    if previous is None:
        _require(state == 'requested')
        value['recovery_actions'].append({'action': action, 'state': state})
    else:
        _require(state == previous['state'] == 'requested'
                 or order[state] > order[previous['state']])
        # Observed can resolve a lost acknowledgement, without claiming that
        # the original process recorded/received that acknowledgement.
        previous['state'] = state
    publish(directory, value, bound)


def reconciled(directory, value, proof, bound):
    _require(value['phase'] not in TERMINAL and value['recovery_attempts'] > 0)
    value['phase'], value['reconciliation'] = 'reconciled', proof
    publish(directory, value, bound)

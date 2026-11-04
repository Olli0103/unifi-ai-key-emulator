"""Explicit rollback of the original fenced host operation, never argv replay.

Missing command receipts remain unknown. This is reconciliation of the local
CLI protocol, not proof that an arbitrary daemon implements cancellation.
"""
from copy import deepcopy
from contextlib import ExitStack
import hashlib

from . import apple_supervisor as supervisor, apple_upgrade_state as journal
from .private_files import read_private as _private
from .host_runtime_command import argv_digest, read_effect


def _argv(value, action):
    from .apple_upgrade import CONTAINER, OWNER_LABEL
    plan = value['plan']
    if action == 'run_new':
        result = list(plan['argv'])
        result[3:3] = ['--label', OWNER_LABEL + '=' + value['operation']]
        return result  # Digest comparison only; this stored argv is NEVER executed.
    return [CONTAINER, {'stop_old': 'stop', 'stop_new': 'stop', 'delete_new': 'delete',
                        'restart_old': 'start'}[action],
            plan['old'] if action in {'stop_old', 'restart_old'} else plan['new']]


def _context(value, action, argv):
    result = {key: value[key] for key in ('operation', 'authority', 'authority_device', 'authority_inode')}
    return dict(result, action=action, argv_sha256=argv_digest(argv))


def _settled_commands(directory, value):
    """A pending RPC needs an exact positive terminal/no-spawn receipt."""
    for collection, recovering in (('actions', False), ('recovery_actions', True)):
        for row in value[collection]:
            action = row['action']
            if row['state'] != 'requested' or action not in {
                    'stop_old', 'run_new', 'stop_new', 'delete_new', 'restart_old'}:
                continue
            names = ([f'recover_{action}_{attempt}' for attempt in range(1, value['recovery_attempts'] + 1)]
                     if recovering else [action])
            receipt = None
            for name in names:
                found = read_effect(directory, _context(value, name, _argv(value, action)))
                if found is not None:
                    receipt = found
            if receipt is None or receipt['spawned'] and (receipt['exit_code'] != 0 or receipt['error'] is not None):
                return False
    return True


def recover(state_dir, service_name, *, ready, run=supervisor._run,
            sleep=None, clock=None, ready_timeout=180, expected_operation=None):
    """Recover only this original operation, preserving its failure and guest data."""
    import time
    from .apple_upgrade import Plan, _listing, _old_state, _new_state, _wait, UpgradeError
    sleep, clock = sleep or time.sleep, clock or time.monotonic
    native = run is supervisor._run
    with supervisor.authority(state_dir) as (directory, bound), ExitStack() as scope:
        value = journal.pending(directory)
        if value is None:
            return 'no_pending_upgrade'
        if value['plan']['service'] != service_name:
            return 'recovery_service_mismatch'
        if expected_operation is not None and value['operation'] != expected_operation:
            return 'recovery_operation_changed'
        if native and value['configuration_revision'] is None:
            return 'recovery_configuration_unbound'
        if value['configuration_revision'] is not None:
            from .host_control_store import configuration_writer
            scope.enter_context(configuration_writer(journal.configuration_path(value)))
            if hashlib.sha256(_private(journal.configuration_path(value), 1024 * 1024)).hexdigest() != value['configuration_revision']:
                return 'recovery_configuration_changed'
        run = supervisor.leased_runner(run, bound)
        saved = value['plan']
        planned = Plan(**dict(saved, argv=tuple(saved['argv']), image_environment=tuple(saved['image_environment'])))
        original = value['registry_before']
        current = supervisor._read_object(directory / supervisor.SPEC_FILE)
        # The only permitted alternative is our exact pin to the candidate.
        alternative = deepcopy(original)
        old_pin = next(row for row in original['services'] if row['name'] == service_name)
        new_pin = dict(old_pin, container=planned.new, image=planned.image)
        alternative['services'] = [row for row in original['services'] if row['name'] != service_name] + [new_pin]
        restored = deepcopy(original)
        restored['services'] = [row for row in original['services'] if row['name'] != service_name] + [old_pin]
        if current not in (original, alternative, restored):
            return 'recovery_registry_changed'
        registry_hash = hashlib.sha256(_private(directory / supervisor.SPEC_FILE, 65536)).hexdigest()

        def guard():
            bound()
            if value['configuration_revision'] is not None and hashlib.sha256(
                    _private(journal.configuration_path(value), 1024 * 1024)).hexdigest() != value['configuration_revision']:
                raise UpgradeError('upgrade_configuration_changed')
            if hashlib.sha256(_private(directory / supervisor.SPEC_FILE, 65536)).hexdigest() != registry_hash:
                raise UpgradeError('upgrade_registry_changed')

        def step(action, state):
            guard()
            previous = next((row for row in value['recovery_actions'] if row['action'] == action), None)
            if previous is not None and previous['state'] == state == 'observed':
                return
            journal.recovery_step(directory, value, action, state, bound)

        def observed(action):
            if not any(row['action'] == action for row in value['recovery_actions']):
                step(action, 'requested')
            step(action, 'observed')

        def command(action):
            guard()
            previous = next((row for row in value['recovery_actions'] if row['action'] == action), None)
            if previous is not None and previous['state'] == 'observed':
                raise UpgradeError('recovery_observed_state_changed')
            step(action, 'requested')
            argv = _argv(value, action)  # Fixed verbs and validated names, no arbitrary argv replay.
            if hasattr(run, 'with_effect'):
                result = run.with_effect(argv, _context(value, f"recover_{action}_{value['recovery_attempts']}", argv))
            else:
                result = run(argv)
            if not isinstance(result, tuple) or len(result) != 2 or type(result[0]) is not int or result[0] != 0:
                raise UpgradeError('recovery_command_uncertain')
            step(action, 'acknowledged')

        try:
            if not _settled_commands(directory, value):
                return 'recovery_command_unsettled'
            rows = _listing(run)
            if not (_old_state(rows, planned, 'running') or _old_state(rows, planned, 'stopped')):
                return 'recovery_old_configuration_unavailable'
            supervisor._outside_guests(directory,
                [source for row in rows.values() for source in supervisor._observed(row)['state_sources']])
            if planned.new in rows and not any(_new_state(rows, planned, state, operation=value['operation'])
                                               for state in ('running', 'stopped')):
                return 'recovery_candidate_not_owned'
            journal.begin_recovery(directory, value, bound)
            journal.reserve_configuration(value)
            previous = next((row for row in value['recovery_actions'] if row['action'] == 'hold'), None)
            if previous is None or previous['state'] != 'observed':
                if previous is None:
                    step('hold', 'requested')
                supervisor._hold_locked(directory, service_name, 15)
                observed('hold')
            if planned.new in rows:
                if not _old_state(rows, planned, 'stopped'):
                    return 'recovery_identity_conflict'
                if _new_state(rows, planned, 'running', operation=value['operation']):
                    command('stop_new')
                    rows = _listing(run)
                    if not _new_state(rows, planned, 'stopped', operation=value['operation']):
                        return 'recovery_stop_unobserved'
                    observed('stop_new')
                command('delete_new')
                rows = _listing(run)
                if rows is None or planned.new in rows:
                    return 'recovery_delete_unobserved'
                observed('delete_new')
            else:
                # A crash between an effect and the next journal write may be
                # resolved by fresh positive absence, but never by a failed read.
                for action in ('stop_new', 'delete_new'):
                    if any(row['action'] == action for row in value['recovery_actions']):
                        observed(action)
            observed('new_absent')
            rows = _listing(run)
            if rows is None or planned.new in rows:
                return 'recovery_absence_unavailable'
            if _old_state(rows, planned, 'stopped'):
                command('restart_old')
                rows = _listing(run)
            if not _old_state(rows, planned, 'running') or planned.new in rows:
                return 'recovery_restart_unobserved'
            if any(row['action'] == 'restart_old' for row in value['recovery_actions']):
                observed('restart_old')
            previous = next((row for row in value['recovery_actions'] if row['action'] == 'ready_old'), None)
            if previous is None:
                step('ready_old', 'requested')
            if not _wait(ready, ready_timeout, sleep, clock, 2):
                return 'recovery_not_ready'
            rows = _listing(run)
            if not _old_state(rows, planned, 'running') or planned.new in rows:
                return 'recovery_final_observation_unavailable'
            observed('ready_old')
            guard()
            previous = next((row for row in value['recovery_actions'] if row['action'] == 'pin_old'), None)
            if previous is None:
                step('pin_old', 'requested')
            supervisor._pin_locked(directory, service_name, planned.old, run=run)
            expected = deepcopy(original)
            expected['services'] = [row for row in original['services'] if row['name'] != service_name] + [old_pin]
            if supervisor._read_object(directory / supervisor.SPEC_FILE) != expected:
                return 'recovery_pin_unobserved'
            if service_name in supervisor._read_object(directory / supervisor.RUNTIME_FILE).get('holds', {}):
                return 'recovery_pin_unobserved'
            registry_hash = hashlib.sha256(_private(directory / supervisor.SPEC_FILE, 65536)).hexdigest()
            observed('pin_old')
            journal.reserve_configuration(value, settle=True)
            journal.reconciled(directory, value, {
                'outcome': 'rolled_back', 'old': 'running', 'new': 'absent', 'readiness': 'verified',
                'old_configuration_sha256': planned.configuration_sha256, 'registry_sha256': registry_hash,
                'guest_state_restored': False, 'original_failure_retained': True}, bound)
            return 'recovered_old'
        except (OSError, ValueError, TypeError, KeyError, RuntimeError):
            # Retain the last durable requested/observed action and the fence.
            return 'recovery_outcome_uncertain'

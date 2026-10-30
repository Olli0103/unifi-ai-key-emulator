"""Provider/model edits cannot silently remove unrelated saved detection policy."""
import copy
import json
import socket

import aiohttp
from aiohttp.test_utils import TestClient, TestServer
import pytest
from yarl import URL

from aikey.admin_security import AdminSecurity
from aikey.aiport_config_store import AiPortConfigurationError
from aikey.control_site import ControlSite, _COOKIE
from test_aiport_config_store import CAMERAS, fixture, settings
from test_control_site import fixture as site_fixture


def configured(tmp_path, policy):
    store, path, raw = fixture(tmp_path)
    selected = settings(tmp_path)
    key = tmp_path / 'openai-key'
    key.write_text('synthetic-policy-provider-key\n')
    key.chmod(0o600)
    provider_fields = {'provider', 'model', 'base_url', 'allow_remote',
                       'allow_insecure_http', 'max_output_tokens', 'api_key_file'}
    raw['live_pool_detector'] = {
        'inference_backend': 'vision_api',
        'provider_config': {name: value for name, value in selected.items() if name in provider_fields},
        'threshold': .3, 'smart_types': ['person'], 'max_events_per_hour': 9,
    }
    raw['paired_streams'][0]['camera_mac'] = CAMERAS[0].lower()
    if policy in ('camera_thresholds', 'both'):
        raw['live_pool_detector']['camera_thresholds'] = {CAMERAS[0].lower(): .77, CAMERAS[1]: .91}
    if policy in ('score_server', 'both'):
        raw['live_pool_detector']['score_server'] = 'http://127.0.0.1:8179/'
    path.write_text(json.dumps(raw, indent=2) + '\n')
    path.chmod(0o600)
    return store, path, raw


@pytest.mark.parametrize('policy', ['camera_thresholds', 'score_server', 'both'])
@pytest.mark.parametrize('operation', ['provider', 'role_model'])
def test_edits_preserve_non_form_policy_raw_camera_spelling_and_exact_undo(
    tmp_path, monkeypatch, policy, operation
):
    store, path, raw = configured(tmp_path, policy)
    before = path.read_bytes()
    snapshot = store.snapshot()
    monkeypatch.setattr(socket.socket, 'connect', lambda *args: pytest.fail('unexpected model/score call'))
    expected = copy.deepcopy(raw)
    model = 'synthetic-policy-replacement-model'
    if operation == 'provider':
        selected = {**settings(tmp_path), 'model': model}
        preview = store.preview(snapshot.revision, selected)
        assert path.read_bytes() == before and not store.history_dir.exists()
        after = store.apply(snapshot.revision, selected)
        assert after.revision == preview.resulting_revision
        detector = expected['live_pool_detector']
        detector['provider_config']['model'] = model
        detector['threshold'] = selected['threshold']
        detector['smart_types'] = selected['smart_types']
        detector['max_requests_per_hour'] = selected['max_requests_per_hour']
        del detector['max_events_per_hour']  # explicitly retired legacy cap, not a new limit
    else:
        after = store.apply_role_model(snapshot.revision, 'detection', model)
        expected['live_pool_detector']['provider_config']['model'] = model
    assert json.loads(path.read_bytes()) == expected
    assert store.provider_rollback_status()['available'] is True
    store.rollback_provider(after.revision)
    assert path.read_bytes() == before


@pytest.mark.parametrize('field,value', [('camera_thresholds', {}),
                                       ('score_server', 'http://unrelated.invalid/')])
def test_provider_form_cannot_inject_unmanaged_fields(tmp_path, field, value):
    store, path, _ = configured(tmp_path, 'both')
    before = path.read_bytes()
    selected = {**settings(tmp_path), field: value}
    with pytest.raises(AiPortConfigurationError, match='settings_incomplete'):
        store.preview(store.snapshot().revision, selected)
    assert path.read_bytes() == before and not store.history_dir.exists()


async def test_authenticated_form_save_keeps_existing_policy_and_other_profiles(tmp_path):
    for name in ('key-fixture', 'slot-1', 'slot-2'):
        (tmp_path / name).mkdir(mode=0o700)
    key_config, _ = site_fixture(tmp_path / 'key-fixture')
    _, port_config, raw = configured(tmp_path / 'slot-1', 'both')
    _, other_config, _ = configured(tmp_path / 'slot-2', 'both')
    original = port_config.read_bytes()
    other = other_config.read_bytes()
    key_before = key_config.read_bytes()
    password = 'synthetic-policy-admin-password'
    site = ControlSite(key_config, signing_key=b's' * 32,
                       password_record=AdminSecurity.create_password_record(password),
                       port=8765,
                       aiport_instances={'slot-1': port_config, 'slot-2': other_config})
    server = TestServer(site.app(), host='127.0.0.1')
    await server.start_server()
    site.origin = f'http://127.0.0.1:{server.port}'
    site.security = AdminSecurity(b's' * 32, site.origin, allow_loopback_http=True)
    client = TestClient(server, cookie_jar=aiohttp.CookieJar(unsafe=True))
    await client.start_server()
    try:
        headers = {'Origin': site.origin}
        login = await client.post('/login', data={'password': password},
                                  headers=headers, allow_redirects=False)
        assert login.status == 303
        await login.read()
        cookie = client.session.cookie_jar.filter_cookies(URL(site.origin))[_COOKIE].value
        fields = {'csrf': site.security.csrf_token(cookie), 'profile': 'aiport:slot-1',
                  'revision': site.aiports['aiport:slot-1'].snapshot().revision,
                  'provider': 'ollama', 'model': 'synthetic-local-vision',
                  'base_url': 'http://127.0.0.1:11434', 'max_output_tokens': '128',
                  'threshold': '0.8', 'smart_types': 'person', 'max_requests_per_hour': ''}
        rejected = await client.post('/provider', data={**fields, 'csrf': 'wrong'},
                                     headers=headers, allow_redirects=False)
        assert rejected.status == 403
        await rejected.read()
        assert port_config.read_bytes() == original
        response = await client.post('/provider', data=fields,
                                     headers=headers, allow_redirects=False)
        assert response.status == 303
        await response.read()
        saved = json.loads(port_config.read_bytes())
        for field in ('camera_thresholds', 'score_server'):
            assert saved['live_pool_detector'][field] == raw['live_pool_detector'][field]
        assert saved['paired_streams'] == raw['paired_streams']
        assert key_config.read_bytes() == key_before and other_config.read_bytes() == other
        page = await (await client.get('/')).text()
        assert 'score-server routing are kept; this form does not edit them' in page
        assert 'synthetic-policy-provider-key' not in page and password not in page
        assert '127.0.0.1:8179' not in page  # no new score-server control/secret disclosure
    finally:
        await client.close()

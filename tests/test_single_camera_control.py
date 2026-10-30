"""A valid one-camera runtime must have the same operator controls as a pool."""

from html.parser import HTMLParser
import json

import pytest

from test_control_site_secrets import signed_in


class ProviderForms(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'form':
            self.current = {} if attrs.get('action') == '/provider' else None
            if self.current is not None:
                self.rows.append(self.current)
        if tag == 'input' and self.current is not None and attrs.get('type') == 'hidden':
            self.current[attrs['name']] = attrs.get('value', '')

    def handle_endtag(self, tag):
        if tag == 'form':
            self.current = None


@pytest.mark.parametrize('profile', ['aiport', 'aiport:nas-one'])
async def test_single_camera_form_save_and_exact_undo_keep_other_identity_and_pairing(tmp_path, profile):
    site, server, client, _, key = await signed_in(tmp_path)
    try:
        port = site.aiports['aiport']
        raw = json.loads(port.path.read_text())
        raw['paired_streams'] = raw['paired_streams'][:1]
        port.path.write_text(json.dumps(raw) + '\n')
        original = port.path.read_bytes()
        key_before = key.read_bytes()
        if profile != 'aiport':
            site.aiports[profile] = site.aiports.pop('aiport')
        assert port.snapshot().camera_count == 1
        page = await (await client.get('/')).text()
        forms = ProviderForms()
        forms.feed(page)
        one, = [row for row in forms.rows if row['profile'] == profile]
        assert '1 configured camera slot.' in page
        assert 'at least two streams' not in page and 'No camera pool' not in page
        fields = {**one, 'provider': 'ollama', 'model': 'single-camera-fixture',
                  'base_url': 'http://127.0.0.1:11434', 'max_output_tokens': '128',
                  'threshold': '0.8', 'smart_types': 'person', 'max_requests_per_hour': ''}
        response = await client.post('/provider', data=fields,
            headers={'Origin': site.origin}, allow_redirects=False)
        assert response.status == 303
        after = json.loads(port.path.read_text())
        assert after['paired_streams'] == raw['paired_streams']
        assert {k: v for k, v in after.items() if k != 'live_pool_detector'} == {
            k: v for k, v in raw.items() if k != 'live_pool_detector'}
        assert key.read_bytes() == key_before
        assert port.provider_rollback_status()['available']
        response = await client.post('/provider/rollback', data={
            'profile': profile, 'revision': port.snapshot().revision, 'csrf': one['csrf']},
            headers={'Origin': site.origin}, allow_redirects=False)
        assert response.status == 303
        assert port.path.read_bytes() == original and key.read_bytes() == key_before
        assert not port.provider_rollback_status()['available']
    finally:
        await client.close()
        await server.close()

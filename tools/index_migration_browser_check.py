"""Owned synthetic dual-space migration UI; no database, encoder or controller."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO/'src'))
from aiohttp.test_utils import TestServer  # noqa: E402
from aikey.admin_security import AdminSecurity  # noqa: E402
from aikey.config import initialize  # noqa: E402
from aikey.control_site import ControlSite  # noqa: E402
from aikey.embedding_profile import _with_fingerprint  # noqa: E402
from aikey.encoding_identity import CLIP_ENCODING, TEXT_ENCODING  # noqa: E402


async def scenario(output, name, viewport, executable):
    from playwright.async_api import async_playwright, expect
    with tempfile.TemporaryDirectory(prefix='fictional-index-ui-', dir=output) as temporary:
        root = Path(temporary)
        key = root/'key/config.json'
        initialize(key, key.parent, controller_host='127.0.0.1')
        before_config = key.read_bytes()
        state = root/'search'
        old, new, tag = 'a'*64, 'b'*64, 'b'*12
        profiles, journals = [], []
        for directory, kind, model, width, encoding in [
            (state, 'clip-basic-v1', 'clip-ViT-L-14', 768, CLIP_ENCODING),
            (state/'session-search', 'e5-session-v1', 'multilingual-e5-small', 384, TEXT_ENCODING)]:
            directory.mkdir(parents=True, mode=0o700, exist_ok=True)
            profile = directory/'search-profile.json'
            profile.write_text(json.dumps(_with_fingerprint({'profile':kind, 'model':model,
                'dimensions':width, 'backend':'http' if width == 384 else 'local-clip-onnx',
                'source':'http://127.0.0.1:9', 'revision':old, **encoding})))
            profiles.append((profile, profile.read_bytes()))
            journal = directory/'index-rebuild'/tag/'journal.json'
            journal.parent.mkdir(parents=True, mode=0o700)
            journal.write_text(json.dumps({'schema':1, 'space':kind, 'generation_id':tag,
                'revision':new, 'from_revision':old, 'state':'staging',
                'counts':{'embedded':1, 'no_source':0}, 'last_error':None}))
            journals.append(journal)
        async def visual_rows():
            return 2
        async def session_rows():
            return 3
        password = 'fictional migration browser passphrase'
        site = ControlSite(key, signing_key=b'x'*32, port=8765,
            password_record=AdminSecurity.create_password_record(password), search_state_dir=state,
            search_live_rows=visual_rows, search_session_live_rows=session_rows,
            audit_log=root/'fictional-audit.jsonl')
        server = TestServer(site.app(), host='127.0.0.1')
        await server.start_server()
        site.origin = str(server.make_url('')).rstrip('/')
        site.security = AdminSecurity(b'x'*32, site.origin, allow_loopback_http=True)
        foreign, errors, posts = [], [], []
        try:
            async with async_playwright() as driver:
                context = await driver.chromium.launch_persistent_context(
                    user_data_dir=str(root/'browser-profile'), executable_path=executable,
                    chromium_sandbox=True, headless=True, viewport=viewport,
                    is_mobile=name == 'mobile', device_scale_factor=1,
                    args=['--disable-background-networking', '--disable-component-update',
                          '--disable-sync', '--no-first-run'])
                try:
                    async def owned(route):
                        parsed = urlsplit(route.request.url)
                        if f'{parsed.scheme}://{parsed.netloc}' != site.origin:
                            foreign.append('blocked')
                            await route.abort()
                        else:
                            await route.continue_()
                    await context.route('**/*', owned)
                    page = await context.new_page()
                    page.on('pageerror', lambda e: errors.append(type(e).__name__))
                    page.on('request', lambda r: posts.append(urlsplit(r.url).path) if r.method == 'POST' else None)
                    await page.goto(site.origin+'/index-migration')
                    await page.get_by_label('Password', exact=True).fill(password)
                    await page.get_by_role('button', name='Sign in', exact=True).click()
                    await page.goto(site.origin+'/index-migration')
                    await expect(page.get_by_text('Selected index: clip-basic-v1', exact=True)).to_be_visible()
                    await expect(page.get_by_text('50.0% of 2', exact=False)).to_be_visible()
                    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth+1')
                    await page.screenshot(path=str(output/(name+'-visual.png')))
                    old_visual = journals[0].read_bytes()
                    await page.get_by_role('link', name='Session text E5 index', exact=True).click()
                    await expect(page.get_by_text('Selected index: e5-session-v1', exact=True)).to_be_visible()
                    await expect(page.get_by_text('33.3% of 3', exact=False)).to_be_visible()
                    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth+1')
                    await page.screenshot(path=str(output/(name+'-session.png')))
                    assert key.read_bytes() == before_config
                    assert all(path.read_bytes() == raw for path, raw in profiles)
                    assert posts == ['/login']
                    # Native required checkbox prevents an accidental unconfirmed retirement.
                    await page.get_by_role('button', name='Cancel staged migration', exact=True).click()
                    assert json.loads(journals[1].read_text())['state'] == 'staging'
                    await page.get_by_role('checkbox').focus()
                    await page.keyboard.press('Space')
                    await expect(page.get_by_role('checkbox')).to_be_checked()
                    await page.get_by_role('button', name='Cancel staged migration', exact=True).click()
                    await expect(page.get_by_text('cancelled', exact=True)).to_be_visible()
                    assert 'space=e5-session-v1' in page.url
                    assert json.loads(journals[1].read_text())['state'] == 'cancelled'
                    assert journals[0].read_bytes() == old_visual
                    assert all(path.read_bytes() == raw for path, raw in profiles)
                    assert key.read_bytes() == before_config
                    await page.screenshot(path=str(output/(name+'-cancelled.png')))
                    assert not foreign and not errors and posts == ['/login','/index-migration/cancel']
                    assert (root/'fictional-audit.jsonl').is_file()
                    result = {'space_navigation':True, 'separate_row_counts':True,
                        'read_only_gets':True, 'unchecked_confirmation_refused':True,
                        'keyboard_confirmed_session_cancel':True, 'visual_generation_preserved':True,
                        'profiles_and_config_exact':True, 'no_document_horizontal_overflow':True,
                        'unowned_requests':len(foreign), 'page_errors':len(errors),
                        'visual_review':'not_performed_by_runner'}
                finally:
                    await context.close()
        finally:
            await server.close()
    assert not root.exists()
    return dict(result, owned_profiles_and_server_collected=True)


async def main(args):
    args.output.mkdir(parents=True, mode=0o700, exist_ok=True)
    def hashes():
        return {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((REPO/'src/aikey').glob('*.py'))}
    before = hashes()
    results = {}
    for name, viewport in [('desktop', {'width':1366,'height':1000}), ('mobile', {'width':390,'height':844})]:
        results[name] = await scenario(args.output, name, viewport, args.browser_executable)
    assert hashes() == before
    (args.output/'report.json').write_text(json.dumps({'source_module_hashes':before,
        'source_unchanged':True, 'scenarios':results}, indent=2)+'\n')
    print(json.dumps({'desktop':'passed','mobile':'passed','real_service_actions':0}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--browser-executable', required=True)
    parser.add_argument('--output', type=Path, required=True)
    asyncio.run(main(parser.parse_args()))

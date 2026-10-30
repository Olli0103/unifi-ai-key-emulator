"""Real isolated Chromium against fictional loopback fixtures, never live state.

Install the optional browser extra; CI uses its installed Chrome in a fresh profile.
--browser-executable can select an existing local Chrome without its user profile.
"""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import socket
import ssl
import sys
import tempfile
import time
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from aiohttp import web  # noqa: E402
from aikey.admin_maintenance import Bridge, prepare, work_once  # noqa: E402
from aikey.admin_security import AdminSecurity  # noqa: E402
from aikey.config import atomic_private, initialize  # noqa: E402
from aikey.control_site import ControlSite  # noqa: E402
from aikey.tls import ensure_identity_certificate  # noqa: E402

PASSWORD = " ".join(("fictional", "browser", "only", "passphrase"))
CANARY = "fictional-host-input-not-in-browser"
CODE = '''import json,pathlib,sys
v=json.load(sys.stdin)
with pathlib.Path(v["effect"]).open("a") as f:f.write("one-effect\\n")
print(json.dumps({"schema":"aikey-maintenance-result/1","outcome":"succeeded"}))
'''


def source_digests():
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((REPO / "src/aikey").glob("*.py"))}


async def scenario(output, name, viewport, executable):
    from playwright.async_api import async_playwright, expect
    temporary = tempfile.TemporaryDirectory(prefix="owned-browser-", dir=output)
    root = Path(temporary.name)
    before = source_digests()
    state = root / "state"
    state.mkdir(mode=0o700)
    atomic_private(state / "identity.json", '{"mac":"02000000F0A1"}\n')
    config = state / "config.json"
    initialize(config, state, controller_host="127.0.0.1")
    atomic_private(state / "controller-ca.pem", (state / "device.crt").read_bytes())
    config_bytes = config.read_bytes()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    port_state = root / "port"
    port_state.mkdir(mode=0o700)
    certificate, _ = ensure_identity_certificate(port_state, "02000000F0B2")
    atomic_private(port_state / "controller-ca.pem", certificate.read_bytes())
    provider_key = "FICTIONAL-BROWSER-PROVIDER-CREDENTIAL"
    atomic_private(port_state / "provider-key", provider_key)
    port_config = port_state / "config.json"
    atomic_private(port_config, json.dumps({
        "controller_ip": "192.168.10.1", "device_ip": "192.168.10.20", "mac": "02000000F0B2",
        "controller_pin": hashlib.sha256(ssl.PEM_cert_to_DER_cert(certificate.read_text())).hexdigest(),
        "firmware_version": "5.1.12",
        "paired_streams": [{"camera_mac": "02000000F0C3", "source_ip": "192.168.10.1",
                            "ffmpeg_path": sys.executable}],
        "live_pool_detector": {"inference_backend": "vision_api", "threshold": .8,
            "smart_types": ["person"], "provider_config": {"provider": "openai-compatible",
                "model": "fictional-detector", "base_url": origin + "/fixture-provider/v1",
                "api_key_file": str(port_state / "provider-key")}}}) + "\n")
    port_bytes = port_config.read_bytes()
    fixture_requests = []
    async def fixture_provider(request):
        body = await request.json()
        assert request.headers.get("Authorization") == "Bearer " + provider_key
        assert not any(k in request.headers for k in ("Cookie", "x-ident", "Camera-MAC"))
        assert body["model"] == "fictional-detector"
        fixture_requests.append(request.path)
        if name == "desktop":
            return web.json_response({"error": "fictional-private-provider-body"}, status=503)
        return web.json_response({"choices": [{"finish_reason": "stop",
            "message": {"content": "NOT-THE-EIGHT-CHARACTER-CHALLENGE"}}]})
    site = ControlSite(config, port_config, signing_key=b"b" * 32,
        password_record=AdminSecurity.create_password_record(PASSWORD), port=port,
        audit_log=root / "audit.jsonl")
    script = root / "approved.py"
    script.write_text(CODE)
    host, bridge = root / "host", root / "bridge"
    operation = prepare(host, bridge, script=script,
        inputs={"effect": str(root / "effect.txt"), "canary": CANARY},
        kind="backup", profile="aikey", revision=hashlib.sha256(config_bytes).hexdigest(),
        timeout=5, expected_effect="private_backup_export", guard_files=[str(config)],
        purpose="device_file_backup")
    site.maintenance = Bridge(bridge)
    app = site.app()
    async def icon(_):
        return web.Response(status=204)
    app.router.add_get("/favicon.ico", icon)
    app.router.add_post("/fixture-provider/v1/chat/completions", fixture_provider)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    listener = web.SockSite(runner, sock)
    await listener.start()
    unowned, console, page_errors = [], [], []
    posts = []
    provider_posts = []
    outside_referrer = []
    outside_seen = asyncio.Event()
    context = None
    browser = None
    report = None
    try:
        async with async_playwright() as driver:
            context = await driver.chromium.launch_persistent_context(
                user_data_dir=str(root / "browser-profile"), executable_path=executable,
                headless=True, chromium_sandbox=True, viewport=viewport,
                is_mobile=name == "mobile", device_scale_factor=1,
                args=["--disable-background-networking", "--disable-component-update",
                      "--disable-sync", "--no-first-run"])
            browser = context.browser
            async def route(request_route):
                parsed = urlsplit(request_route.request.url)
                if request_route.request.url == "https://fictional-outside.invalid/check":
                    outside_referrer.append((await request_route.request.all_headers()).get("referer"))
                    outside_seen.set()
                    await request_route.abort()
                elif f"{parsed.scheme}://{parsed.netloc}" != origin:
                    unowned.append(request_route.request.url)
                    await request_route.abort()
                else:
                    await request_route.continue_()
            await context.route("**/*", route)
            page = await context.new_page()
            page.on("console", lambda message: console.append(message.text))
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.on("request", lambda request: posts.append(request.url)
                    if request.method == "POST" and request.url == origin + "/maintenance" else None)
            page.on("request", lambda request: provider_posts.append(request.url)
                    if request.method == "POST" and request.url == origin + "/provider" else None)
            await page.goto(origin + "/maintenance")
            await expect(page.get_by_role("heading", name="Administrator sign in", exact=True)).to_be_visible()
            await page.get_by_label("Password", exact=True).fill(PASSWORD)
            async with page.expect_response(lambda response: response.url == origin + "/login"
                                            and response.request.method == "POST") as login_response:
                await page.get_by_role("button", name="Sign in", exact=True).click()
            login_result = await login_response.value
            assert login_result.status == 303, {"status": login_result.status,
                "origin": (await login_result.request.all_headers()).get("origin"),
                "audit": (root / "audit.jsonl").read_text()}
            assert (await login_result.request.all_headers()).get("origin") == origin
            await expect(page).to_have_url(origin + "/")
            profile = page.locator("form[action='/provider']").filter(
                has=page.locator("input[name='profile'][value='aiport']"))
            await expect(profile).to_be_visible()
            await expect(page.get_by_text("1 configured camera slot.", exact=False)).to_be_visible()
            await profile.locator("input[name='model']").fill("")
            await profile.get_by_role("button", name="Save provider settings").click()
            assert not provider_posts and port_config.read_bytes() == port_bytes
            await profile.locator("input[name='model']").fill("fictional-provider-change")
            await profile.get_by_role("button", name="Save provider settings").click()
            await expect(page).to_have_url(origin + "/?saved=1")
            changed = json.loads(port_config.read_text())
            original = json.loads(port_bytes)
            expected = json.loads(port_bytes)
            expected["live_pool_detector"]["provider_config"]["model"] = "fictional-provider-change"
            # The store may materialize its typed provider defaults; identity/streams
            # and the existing credential reference must still be exact.
            assert {k: v for k, v in changed.items() if k != "live_pool_detector"} == {
                k: v for k, v in original.items() if k != "live_pool_detector"}
            assert changed["live_pool_detector"]["provider_config"]["api_key_file"] == (
                original["live_pool_detector"]["provider_config"]["api_key_file"])
            assert len(provider_posts) == 1 and not fixture_requests
            undo = page.locator("form[action='/provider/rollback']").filter(
                has=page.locator("input[name='profile'][value='aiport']"))
            await undo.get_by_role("button").click()
            await expect(page).to_have_url(origin + "/?rolledback=1")
            assert port_config.read_bytes() == port_bytes and config.read_bytes() == config_bytes
            role = page.locator("form[action='/role-model-preview']").filter(
                has=page.locator("input[name='profile'][value='aiport']")).filter(
                has=page.locator("input[name='role'][value='detection']"))
            await role.locator("input[name='model']").fill("invalid model spaces")
            await role.get_by_role("button").click()
            await expect(page.get_by_role("heading", name="Model preview unavailable", exact=True)).to_be_visible()
            assert port_config.read_bytes() == port_bytes and not fixture_requests
            await page.goto(origin + "/")
            role = page.locator("form[action='/role-model-preview']").filter(
                has=page.locator("input[name='profile'][value='aiport']")).filter(
                has=page.locator("input[name='role'][value='detection']"))
            await role.locator("input[name='model']").fill("fictional-role-change")
            await role.get_by_role("button").click()
            await expect(page.get_by_role("heading", name="Preview model change", exact=True)).to_be_visible()
            assert port_config.read_bytes() == port_bytes and not fixture_requests
            await page.get_by_role("button", name="Confirm model change", exact=True).click()
            await expect(page).to_have_url(origin + "/?saved=1")
            expected["live_pool_detector"]["provider_config"]["model"] = "fictional-role-change"
            assert json.loads(port_config.read_text()) == expected
            undo = page.locator("form[action='/provider/rollback']").filter(
                has=page.locator("input[name='profile'][value='aiport']"))
            await undo.get_by_role("button").click()
            await expect(page).to_have_url(origin + "/?rolledback=1")
            assert port_config.read_bytes() == port_bytes and not fixture_requests
            probe = page.locator("form[action='/provider-probe']").filter(
                has=page.locator("input[name='profile'][value='aiport']")).filter(
                has=page.locator("input[name='role'][value='detection']"))
            await probe.get_by_role("button").click()
            assert not fixture_requests  # Browser-required separate cost acknowledgement.
            await probe.get_by_role("checkbox").check()
            await probe.get_by_role("button").click()
            title = "Synthetic test unavailable" if name == "desktop" else "Synthetic vision test"
            await expect(page.get_by_role("heading", name=title, exact=True)).to_be_visible()
            expected_message = ("no automatic retry occurred" if name == "desktop" else
                                "generated image answer did not match")
            await expect(page.get_by_text(expected_message, exact=False)).to_be_visible()
            assert fixture_requests == ["/fixture-provider/v1/chat/completions"]
            assert port_config.read_bytes() == port_bytes and config.read_bytes() == config_bytes
            for private in (provider_key, "fictional-private-provider-body", str(root)):
                assert private not in await page.content()
            assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
            await page.screenshot(path=str(output / (name + "-provider-result.png")), full_page=True)
            await page.goto(origin + "/maintenance")
            await expect(page.get_by_role("heading", name="Maintenance", exact=True)).to_be_visible()
            await expect(page.get_by_text("Submission does not start a host worker", exact=False)).to_be_visible()
            assert await page.locator("form[action='/maintenance']").count() == 1
            assert operation in await page.content()
            assert CANARY not in await page.content() and PASSWORD not in await page.content()
            assert not list((bridge / "requests").glob("*.json"))
            assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
            await page.screenshot(path=str(output / (name + "-ready.png")), full_page=True)
            button = page.get_by_role("button", name="Submit back up device files", exact=True)
            checkbox = page.get_by_role("checkbox", name="I reviewed this one-use operation and its scope.")
            await button.click()
            assert not await checkbox.evaluate("element => element.checkValidity()")
            assert not posts and not list((bridge / "requests").glob("*.json"))
            await checkbox.focus()
            await page.keyboard.press("Space")
            await expect(checkbox).to_be_checked()
            await page.keyboard.press("Tab")
            await expect(button).to_be_focused()
            await page.keyboard.press("Enter")
            await expect(page.get_by_role("heading", name="Request result")).to_be_visible()
            assert page.url.startswith(origin + "/maintenance/requests/")
            await expect(page.get_by_role("status")).to_have_text("No host result reported")
            assert len(posts) == 1 and len(list((bridge / "requests").glob("*.json"))) == 1
            assert not (root / "effect.txt").exists()
            assert await asyncio.to_thread(work_once, host, bridge) == {"handled": 1}
            await page.reload()
            await expect(page.get_by_role("status")).to_have_text("Host reported success")
            assert (root / "effect.txt").read_text() == "one-effect\n"
            await page.goto(origin + "/maintenance")
            assert await page.locator("form[action='/maintenance']").count() == 0
            await expect(page.get_by_text("Already submitted.", exact=False)).to_be_visible()
            assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
            assert await page.evaluate("localStorage.length") == 0
            assert await page.evaluate("sessionStorage.length") == 0
            await page.screenshot(path=str(output / (name + "-completed.png")), full_page=True)
            site.security._clock = lambda: time.time() + 86400
            await page.goto(origin + "/maintenance")
            await expect(page.get_by_role("heading", name="Administrator sign in", exact=True)).to_be_visible()
            assert len(posts) == 1 and not unowned and not page_errors
            assert all(CANARY not in message and PASSWORD not in message for message in console)
            assert config.read_bytes() == config_bytes and port_config.read_bytes() == port_bytes
            assert source_digests() == before
            assert all(provider_key not in message and "fictional-private-provider-body" not in message
                       for message in console)
            report = {"viewport": viewport, "browser_version": browser.version,
                "login_and_expiry_in_browser": True, "required_confirmation_prevented_post": True,
                "keyboard_confirmation_and_submit": True, "admitted_requests": len(posts),
                "fixture_host_effects": 1, "one_use_result_read_without_reexecution": True,
                "horizontal_overflow": False, "local_or_session_storage_entries": 0,
                "unowned_page_requests": len(unowned), "page_errors": len(page_errors),
                "configuration_and_source_bytes_unchanged": True, "real_device_provider_or_model_calls": 0,
                "single_camera_provider_form_available": True,
                "required_provider_input_prevented_post": True,
                "provider_save_and_exact_rollback": True,
                "invalid_model_preview_refused_without_save_or_provider_call": True,
                "role_preview_confirm_and_exact_rollback": True,
                "synthetic_fixture_provider_calls": len(fixture_requests),
                "provider_result": "http_503_no_retry" if name == "desktop" else "synthetic_answer_mismatch",
                "synthetic_cost_acknowledgement_required": True}
            # Intercept before transport: verify privacy without contacting an outside host.
            await page.evaluate("""() => {const a=document.createElement('a');
                a.href='https://fictional-outside.invalid/check'; document.body.append(a); a.click();}""")
            await asyncio.wait_for(outside_seen.wait(), timeout=5)
            assert outside_referrer == [None]
            report["cross_origin_referrer_suppressed_before_transport"] = True
            await context.close()
            context = None
            assert not browser.is_connected()
    finally:
        # async_playwright already tears down its owned browser on exceptional exit.
        # Do not mask that original failure with a second close after driver shutdown.
        try:
            await runner.cleanup()
        finally:
            sock.close()
            temporary.cleanup()
    assert not root.exists()
    report["owned_fixture_profile_server_collected"] = True
    return report


async def run(args):
    args.output.mkdir(mode=0o700, parents=True, exist_ok=True)
    results = {}
    for name, viewport in [("desktop", {"width": 1200, "height": 900}),
                           ("mobile", {"width": 390, "height": 844})]:
        results[name] = await scenario(args.output, name, viewport, args.browser_executable)
    result = {"scope": "fictional loopback browser E2E only, not native/mobile-client acceptance",
              "scenarios": results, "source_modules": source_digests(), "visual_review": "not_performed_by_runner"}
    (args.output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"browser_scenarios_passed": len(results), "real_device_model_provider_calls": 0,
                      "owned_fixture_profiles_collected": True, "screenshot_visual_review": "pending"}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--browser-executable", help="Optional installed Chrome; a fresh isolated profile is mandatory")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()

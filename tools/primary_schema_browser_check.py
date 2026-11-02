"""Fictional saved primary-schema choice, never live inference or camera access."""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import ssl
import sys
import tempfile
import time
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from aiohttp.test_utils import TestServer  # noqa: E402
from aikey.admin_security import AdminSecurity  # noqa: E402
from aikey.config import initialize  # noqa: E402
from aikey.control_site import ControlSite  # noqa: E402
from aikey.tls import ensure_identity_certificate  # noqa: E402


async def scenario(output, name, viewport, executable):
    from playwright.async_api import async_playwright, expect

    with tempfile.TemporaryDirectory(prefix="owned-primary-schema-", dir=output) as temporary:
        root = Path(temporary)
        key = root / "key/config.json"
        initialize(key, key.parent, controller_host="127.0.0.1")
        key_before = key.read_bytes()
        port = root / "port/config.json"
        port.parent.mkdir(mode=0o700)
        cert, _ = ensure_identity_certificate(port.parent, "020000000011")
        ca = port.parent / "controller-ca.pem"
        ca.write_bytes(cert.read_bytes())
        ca.chmod(0o600)
        raw = {"controller_ip": "192.168.10.1", "device_ip": "192.168.10.20",
            "mac": "020000000011", "firmware_version": "5.1.12",
            "controller_pin": hashlib.sha256(ssl.PEM_cert_to_DER_cert(cert.read_text())).hexdigest(),
            "paired_streams": [{"camera_mac": "020000000012", "source_ip": "192.168.10.1",
                                "ffmpeg_path": sys.executable}],
            "live_pool_detector": {"inference_backend": "vision_api", "threshold": .8,
                "smart_types": ["person"], "provider_config": {"provider": "ollama",
                    "model": "fictional-vision", "base_url": "http://127.0.0.1:9",
                    "max_output_tokens": 256, "allow_remote": False, "allow_insecure_http": False}}}
        port.write_text(json.dumps(raw) + "\n")
        port.chmod(0o600)
        password = "fictional-browser-only-passphrase"
        site = ControlSite(key, port, signing_key=b"z" * 32,
            password_record=AdminSecurity.create_password_record(password), port=8765,
            audit_log=root / "audit.jsonl")
        server = TestServer(site.app(), host="127.0.0.1")
        foreign, errors, posts = [], [], []
        try:
            await server.start_server()
            site.origin = str(server.make_url("/")).rstrip("/")
            site.security = AdminSecurity(b"z" * 32, site.origin, allow_loopback_http=True)
            async with async_playwright() as driver:
                context = await driver.chromium.launch_persistent_context(
                    user_data_dir=str(root / "browser-profile"), executable_path=executable,
                    headless=True, chromium_sandbox=True, viewport=viewport,
                    is_mobile=name == "mobile", device_scale_factor=1,
                    args=["--disable-background-networking", "--disable-component-update",
                          "--disable-sync", "--no-first-run"])
                try:
                    async def route(route):
                        parsed = urlsplit(route.request.url)
                        if f"{parsed.scheme}://{parsed.netloc}" != site.origin:
                            foreign.append(route.request.url)
                            await route.abort()
                        else:
                            await route.continue_()
                    await context.route("**/*", route)
                    page = await context.new_page()
                    page.on("pageerror", lambda e: errors.append(str(e)))
                    page.on("request", lambda r: posts.append(urlsplit(r.url).path)
                            if r.method == "POST" else None)
                    await page.goto(site.origin)
                    await page.get_by_label("Password", exact=True).fill(password)
                    await page.get_by_role("button", name="Sign in", exact=True).click()
                    section = page.locator("section").filter(
                        has=page.get_by_role("heading", name="AI Port", exact=True))
                    choice = section.get_by_role("checkbox", name="Require a JSON schema", exact=False)
                    await expect(choice).not_to_be_checked()
                    assert "structured_output" not in json.loads(port.read_bytes())["live_pool_detector"]
                    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                    await section.screenshot(path=str(output / (name + "-schema-off.png")))
                    await choice.check()
                    await section.get_by_role("button", name="Save provider settings", exact=True).focus()
                    await page.keyboard.press("Enter")
                    await expect(choice).to_be_checked()
                    assert json.loads(port.read_bytes())["live_pool_detector"]["structured_output"] is True
                    await section.screenshot(path=str(output / (name + "-schema-on.png")))
                    # Provider/model edits carry the displayed capability choice.
                    await section.get_by_label("Model ID", exact=True).fill("fictional-replacement")
                    await section.get_by_role("button", name="Save provider settings", exact=True).click()
                    await expect(choice).to_be_checked()
                    assert json.loads(port.read_bytes())["live_pool_detector"]["structured_output"] is True
                    await choice.uncheck()
                    await section.get_by_role("button", name="Save provider settings", exact=True).click()
                    await expect(choice).not_to_be_checked()
                    after = json.loads(port.read_bytes())
                    changed = after["live_pool_detector"]
                    assert changed.pop("structured_output") is False
                    assert changed["provider_config"].pop("model") == "fictional-replacement"
                    raw["live_pool_detector"]["provider_config"].pop("model")
                    assert after == raw and key.read_bytes() == key_before
                    assert site.audit.failures == 0
                    assert sum(r["action"] == "provider_save" and r["result"] == "saved"
                               for r in site.audit.recent()) == 3
                    assert not errors and not foreign and posts == ["/login", "/provider", "/provider", "/provider"]
                    assert await page.evaluate("localStorage.length + sessionStorage.length") == 0
                    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                    await section.screenshot(path=str(output / (name + "-schema-disabled.png")))
                    site.security._clock = lambda: time.time() + 86400
                    await page.reload()
                    await expect(page.get_by_role("heading", name="Administrator sign in")).to_be_visible()
                    result = {"viewport": viewport, "browser_version": context.browser.version,
                        "legacy_default_off": True, "explicit_toggle_saved": True,
                        "provider_edit_preserves_choice": True, "explicit_disable_saved": True,
                        "keyboard_save": True, "other_profile_and_policy_preserved": True,
                        "private_audit_enabled_and_recorded": True, "session_expiry_refused": True,
                        "foreign_requests": 0, "real_provider_model_media_or_processor_actions": 0,
                        "horizontal_overflow": False, "visual_review": "not_performed_by_runner"}
                finally:
                    await context.close()
        finally:
            await server.close()
    assert not root.exists()
    return {**result, "owned_resources_collected": True}


async def main(args):
    args.output.mkdir(mode=0o700, parents=True, exist_ok=True)
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted((REPO / "src/aikey").glob("*.py"))}
    results = {}
    for name, viewport in [("desktop", {"width": 1200, "height": 900}),
                           ("mobile", {"width": 390, "height": 844})]:
        results[name] = await scenario(args.output, name, viewport, args.browser_executable)
    after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted((REPO / "src/aikey").glob("*.py"))}
    assert before == after
    (args.output / "result.json").write_text(json.dumps(
        {"scenarios": results, "source_modules": before}, indent=2) + "\n")
    print(json.dumps({"desktop_mobile": "passed", "owned_resources_collected": True}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--browser-executable")
    asyncio.run(main(parser.parse_args()))

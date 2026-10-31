"""Fictional saved-policy browser flow; no processor/provider/controller starts."""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
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


async def scenario(output, name, viewport, executable):
    from playwright.async_api import async_playwright, expect
    with tempfile.TemporaryDirectory(prefix="owned-caption-policy-", dir=output) as temporary:
        root = Path(temporary)
        path = root / "key/config.json"
        initialize(path, path.parent, controller_host="127.0.0.1")
        raw = json.loads(path.read_bytes())
        raw["runtime"]["state_dir"] = "/state"
        raw["controller"]["protect_version"] = "7.3.74"
        raw["worker"].update(callback_mode="enabled", request_mp4_exports=True,
            continuous={"enabled": True, "api_key_file": "/state/CANARY-inventory-key",
                "web_trust_file": "/state/CANARY-controller-trust", "web_cert_file": "/state/CANARY-cert",
                "refresh_seconds": 60, "camera_models": ["UVC G5 Flex"], "camera_ids": ["a" * 24]})
        raw["inference"] = {"provider": "ollama", "model": "fictional-cost-caption",
                            "base_url": "http://127.0.0.1:9", "allow_insecure_http": True}
        path.write_text(json.dumps(raw) + "\n")
        path.chmod(0o600)
        journal = path.parent / "caption-budget.json"
        journal.write_text("CANARY-fictional-journal-must-stay-identical")
        journal.chmod(0o600)
        original = path.read_bytes()
        password = "fictional caption policy browser passphrase"
        site = ControlSite(path, None, signing_key=b"c" * 32,
            password_record=AdminSecurity.create_password_record(password), port=8765)
        site.aikey.snapshot()
        server = TestServer(site.app(), host="127.0.0.1")
        await server.start_server()
        site.origin = str(server.make_url("/")).rstrip("/")
        site.security = AdminSecurity(b"c" * 32, site.origin, allow_loopback_http=True)
        posts, errors, unowned = [], [], []
        try:
            async with async_playwright() as driver:
                context = await driver.chromium.launch_persistent_context(
                    user_data_dir=str(root / "browser"), executable_path=executable,
                    headless=True, chromium_sandbox=True, viewport=viewport,
                    is_mobile=name == "mobile", device_scale_factor=1,
                    args=["--disable-background-networking", "--disable-component-update",
                          "--disable-sync", "--no-first-run"])
                try:
                    async def route(route):
                        parsed = urlsplit(route.request.url)
                        if f"{parsed.scheme}://{parsed.netloc}" != site.origin:
                            unowned.append(route.request.url)
                            await route.abort()
                        else:
                            await route.continue_()
                    await context.route("**/*", route)
                    page = await context.new_page()
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.on("request", lambda r: posts.append(urlsplit(r.url).path)
                            if r.method == "POST" else None)
                    await page.goto(site.origin + "/caption-policy")
                    await expect(page.get_by_role("heading", name="Administrator sign in")).to_be_visible()
                    await page.get_by_label("Password", exact=True).fill(password)
                    async with page.expect_navigation():
                        await page.get_by_role("button", name="Sign in", exact=True).click()
                    await page.get_by_role("link", name="Caption cost policy", exact=True).click()
                    await expect(page.get_by_role("heading", name="Caption cost policy", exact=True)).to_be_visible()
                    choice = page.get_by_label("Proposed saved policy", exact=True)
                    assert await choice.input_value() == ""
                    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                    await page.screenshot(path=str(output / (name + "-caption-policy.png")), full_page=True)
                    await choice.select_option("unmetered")
                    async with page.expect_navigation():
                        await page.get_by_role("button", name="Preview caption policy", exact=True).click()
                    await expect(page.get_by_role("heading", name="Review caption policy", exact=True)).to_be_visible()
                    await expect(page.get_by_text("Nothing has been saved.", exact=True)).to_be_visible()
                    assert path.read_bytes() == original
                    confirm = page.get_by_role("button", name="Confirm saved caption policy", exact=True)
                    # Native form validity must not turn an unchecked waiver into a POST.
                    await confirm.click()
                    assert posts == ["/login", "/caption-policy/preview"]
                    await page.screenshot(path=str(output / (name + "-review-bypass.png")), full_page=True)
                    consent = page.get_by_role("checkbox", name="Save this reviewed policy only", exact=False)
                    await consent.focus()
                    await page.keyboard.press("Space")
                    cost = page.get_by_role("checkbox", name="I explicitly accept bypassing", exact=False)
                    await cost.focus()
                    await page.keyboard.press("Space")
                    async with page.expect_navigation():
                        await confirm.press("Enter")
                    await expect(page.get_by_role("heading", name="Caption policy saved", exact=True)).to_be_visible()
                    saved = json.loads(path.read_bytes())
                    assert saved["worker"]["continuous"].pop("unmetered") is True and saved == raw
                    assert journal.read_text() == "CANARY-fictional-journal-must-stay-identical"
                    assert "CANARY" not in await page.content() and password not in await page.content()
                    await page.get_by_role("link", name="Read saved policy", exact=True).click()
                    await page.get_by_label("Proposed saved policy", exact=True).select_option("metered")
                    async with page.expect_navigation():
                        await page.get_by_role("button", name="Preview caption policy", exact=True).click()
                    assert await page.get_by_role("checkbox").count() == 1
                    await page.get_by_role("checkbox").check()
                    async with page.expect_navigation():
                        await page.get_by_role("button", name="Confirm saved caption policy", exact=True).click()
                    await expect(page.get_by_role("heading", name="Caption policy saved", exact=True)).to_be_visible()
                    assert json.loads(path.read_bytes()) == raw
                    assert not site.aikey.provider_rollback_status()["available"]
                    assert not site.admin_api.caption_policy_control.grants
                    assert "CANARY" not in await page.content() and password not in await page.content()
                    assert await page.evaluate("localStorage.length + sessionStorage.length") == 0
                    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                    await page.screenshot(path=str(output / (name + "-saved-metered.png")), full_page=True)
                    site.security._clock = lambda: time.time() + 86400
                    await page.goto(site.origin + "/caption-policy")
                    await expect(page.get_by_role("heading", name="Administrator sign in")).to_be_visible()
                    assert posts == ["/login", "/caption-policy/preview", "/caption-policy/apply",
                                     "/caption-policy/preview", "/caption-policy/apply"]
                    assert not errors and not unowned
                    result = {"viewport": viewport, "browser_version": context.browser.version,
                        "no_implicit_mode_or_cost_waiver": True, "preview_no_write": True,
                        "keyboard_confirmed_bypass": True, "metered_return_explicitly_confirmed": True,
                        "only_one_flag_changed": True, "journal_bytes_preserved": True,
                        "provider_undo_not_available": True, "session_expiry_refused": True,
                        "browser_storage_or_private_echo": False, "foreign_requests": 0,
                        "actual_processor_model_controller_or_media_operations": 0,
                        "body_horizontal_overflow": False, "visual_review": "not_performed_by_runner"}
                finally:
                    await context.close()
        finally:
            await server.close()
    assert not root.exists()
    return {**result, "owned_profiles_and_servers_collected": True}


async def main(args):
    args.output.mkdir(mode=0o700, parents=True, exist_ok=True)
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted((REPO / "src/aikey").glob("*.py"))}
    results = {}
    for name, viewport in [("desktop", {"width": 1200, "height": 900}),
                           ("mobile", {"width": 390, "height": 844})]:
        results[name] = await scenario(args.output, name, viewport, args.browser_executable)
    assert before == {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in sorted((REPO / "src/aikey").glob("*.py"))}
    (args.output / "result.json").write_text(json.dumps(
        {"scenarios": results, "source_modules": before}, indent=2) + "\n")
    print(json.dumps({"desktop_mobile_keyboard": "passed", "owned_resources_collected": True}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--browser-executable")
    asyncio.run(main(parser.parse_args()))

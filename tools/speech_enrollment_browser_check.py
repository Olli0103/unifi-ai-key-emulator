"""Real isolated Chrome on fictional audio consent; never a real microphone/API."""

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
    with tempfile.TemporaryDirectory(prefix="owned-speech-enrollment-", dir=output) as temporary:
        root = Path(temporary)
        config = root / "config.json"
        initialize(config, root, controller_host="127.0.0.1")
        before = config.read_bytes()
        password, key = "fictional speech setup password", "FICTIONAL-SPEECH-KEY-ONLY"
        site = ControlSite(config, signing_key=b"e" * 32,
            password_record=AdminSecurity.create_password_record(password), port=8765)
        observed_name = ["Fictional hall <not markup>"]
        reads, posts, errors, unowned = [], [], [], []
        async def inventory():
            reads.append(True)
            return {"schema": "aikey-camera-preflight/1", "protect_version": "7.3.74",
                "fetched_at": int(time.time()), "cameras": [
                    {"id": c * 24, "mac": "02000000F00" + str(i), "name": title,
                     "model": "Fictional camera", "state": state, "smart_detect_types": [],
                     "audio_detect_types": ["alrmSpeak"]}
                    for i, (c, title, state) in enumerate([
                        ("a", observed_name[0], "CONNECTED"),
                        ("b", "Fictional outside", "CONNECTED"),
                        ("c", "Fictional offline", "DISCONNECTED")], 1)]}
        site.inventory_loader = inventory
        admin = TestServer(site.app(), host="127.0.0.1")
        try:
            await admin.start_server()
            site.origin = str(admin.make_url("/")).rstrip("/")
            site.security = AdminSecurity(b"e" * 32, site.origin, allow_loopback_http=True)
            async with async_playwright() as driver:
                context = await driver.chromium.launch_persistent_context(
                    user_data_dir=str(root / "browser-profile"), executable_path=executable,
                    headless=True, chromium_sandbox=True, viewport=viewport,
                    is_mobile=name == "mobile", device_scale_factor=1,
                    args=["--disable-background-networking", "--disable-component-update",
                          "--disable-sync", "--no-first-run"])
                try:
                    async def route(route):
                        p = urlsplit(route.request.url)
                        if f"{p.scheme}://{p.netloc}" != site.origin:
                            unowned.append(route.request.url)
                            await route.abort()
                        else:
                            await route.continue_()
                    await context.route("**/*", route)
                    page = await context.new_page()
                    page.on("pageerror", lambda e: errors.append(str(e)))
                    page.on("request", lambda r: posts.append(urlsplit(r.url).path)
                            if r.method == "POST" else None)
                    await page.goto(site.origin + "/speech-enrollment")
                    await page.get_by_label("Password", exact=True).fill(password)
                    await page.get_by_role("button", name="Sign in", exact=True).click()
                    await page.get_by_role("link", name="Set up speech transcription", exact=True).click()
                    await expect(page.get_by_role("heading", name="Set up speech transcription")).to_be_visible()
                    assert await page.locator("input[name='camera']").count() == 2
                    assert await page.locator("input[name='camera']:checked").count() == 0
                    assert await page.get_by_text("Fictional offline", exact=False).count() == 0
                    assert await page.locator("not,script,img").count() == 0
                    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                    await page.screenshot(path=str(output / (name + "-speech-setup.png")), full_page=True)

                    async def fill_setup():
                        await page.get_by_label("Allow audio from Fictional hall <not markup>", exact=True).check()
                        await page.get_by_label("Speech provider", exact=True).select_option("openai" if name == "mobile" else "openai-compatible")
                        await page.get_by_label("Speech API base URL", exact=True).fill(
                            "https://api.openai.com/v1" if name == "mobile" else "http://127.0.0.1:8178/v1")
                        await page.get_by_label("Speech model", exact=True).fill("fictional-whisper")
                        await page.get_by_label("Language (optional two-letter code)", exact=True).fill("de")
                        await page.get_by_role("button", name="Preview speech setup", exact=True).click()
                        await expect(page.get_by_role("heading", name="Confirm speech transcription")).to_be_visible()

                    async def acknowledge():
                        await page.get_by_label("New separate speech API key", exact=True).fill(key)
                        await page.get_by_role("checkbox", name="I authorize audio", exact=False).check()
                        await page.get_by_role("checkbox", name="I understand speech", exact=False).check()

                    await fill_setup()
                    assert config.read_bytes() == before and not list(root.glob("speech-key-*"))
                    assert "a" * 24 not in await page.content() and "02000000F001" not in await page.content()
                    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                    await page.screenshot(path=str(output / (name + "-speech-confirm.png")), full_page=True)
                    await acknowledge()
                    observed_name[0] = "Changed microphone location"
                    await page.get_by_role("button", name="Save initial speech permissions", exact=True).click()
                    await expect(page.get_by_role("heading", name="Speech setup not saved")).to_be_visible()
                    assert config.read_bytes() == before and not list(root.glob("speech-key-*"))
                    assert key not in await page.content()
                    observed_name[0] = "Fictional hall <not markup>"
                    site._inventory_snapshot = None  # Owned fixture, no product source replacement.
                    await page.get_by_role("link", name="Speech setup", exact=True).click()
                    await fill_setup()
                    await acknowledge()
                    button = page.get_by_role("button", name="Save initial speech permissions", exact=True)
                    await button.focus()
                    await page.keyboard.press("Enter")
                    await expect(page.get_by_role("heading", name="Local AI processor")).to_be_visible()
                    after = json.loads(config.read_text())
                    speech = after.pop("speech_to_text")
                    assert after == json.loads(before) and speech["camera_ids"] == ["a" * 24]
                    stored_key = root / Path(speech["api_key_file"]).name
                    assert stored_key.read_text().strip() == key and stored_key.stat().st_mode & 0o777 == 0o600
                    assert key not in await page.content() and "speech-key-" not in await page.content()
                    await page.goto(site.origin + "/speech-enrollment")
                    assert await page.locator("form").count() == 0
                    await page.screenshot(path=str(output / (name + "-speech-already-configured.png")), full_page=True)
                    assert await page.evaluate("localStorage.length + sessionStorage.length") == 0
                    site.security._clock = lambda: time.time() + 86400
                    await page.reload()
                    await expect(page.get_by_role("heading", name="Administrator sign in")).to_be_visible()
                    assert not errors and not unowned
                    assert posts == ["/login", "/speech-enrollment/preview", "/speech-enrollment/apply",
                                     "/speech-enrollment/preview", "/speech-enrollment/apply"]
                    result = {"viewport": viewport, "browser_version": context.browser.version,
                        "no_preselected_cameras": True, "escaped_named_choice": True,
                        "separate_confirmation_and_credential": True, "changed_camera_refused": True,
                        "successful_saved_scope_only": True, "keyboard_confirmation": True,
                        "existing_role_not_reenrolled": True, "expired_session_refused": True,
                        "unowned_requests": 0, "real_controller_model_or_media_calls": 0,
                        "body_horizontal_overflow": False, "visual_review": "not_performed_by_runner"}
                finally:
                    await context.close()
        finally:
            await admin.close()
    assert not root.exists()
    return {**result, "owned_profiles_servers_and_fictional_keys_collected": True}


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

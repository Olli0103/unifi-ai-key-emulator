"""Fictional microphone additions: desktop/mobile/keyboard, no real audio or model calls."""

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


async def capture(page, path):
    await page.evaluate("document.activeElement.blur(); scrollTo(0, 0)")
    await page.evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    assert (await page.get_by_role("heading", level=1).bounding_box())["y"] >= 19
    await page.screenshot(path=str(path), full_page=True)


async def scenario(output, name, viewport, executable):
    from playwright.async_api import async_playwright, expect

    with tempfile.TemporaryDirectory(prefix="owned-speech-camera-", dir=output) as temporary:
        root = Path(temporary)
        path = root / "key/config.json"
        initialize(path, path.parent, controller_host="127.0.0.1")
        raw = json.loads(path.read_bytes())
        raw["runtime"]["state_dir"] = "/state"
        raw["controller"]["protect_version"] = "7.3.74"
        raw["speech_to_text"] = {"provider": "openai-compatible", "model": "fictional-whisper",
            "base_url": "http://127.0.0.1:9/v1", "camera_ids": ["a" * 24], "language": "de"}
        path.write_text(json.dumps(raw) + "\n")
        path.chmod(0o600)
        before = path.read_bytes()
        password = "fictional-browser-passphrase"
        site = ControlSite(path, signing_key=b"s" * 32,
            password_record=AdminSecurity.create_password_record(password), port=8765)
        observed_name = ["Fictional patio <not markup>"]
        async def inventory():
            return {"schema": "aikey-camera-preflight/1", "protect_version": "7.3.74",
                "fetched_at": int(time.time()), "cameras": [
                    {"id": c * 24, "mac": "02000000F00" + str(i), "name": title,
                     "model": "Fictional camera", "state": state, "smart_detect_types": [],
                     "audio_detect_types": ["alrmSpeak"]}
                    for i, (c, title, state) in enumerate([
                        ("a", "Fictional already saved", "CONNECTED"),
                        ("b", observed_name[0], "CONNECTED"),
                        ("c", "Fictional offline", "DISCONNECTED")], 1)]}
        site.inventory_loader = inventory
        server = TestServer(site.app(), host="127.0.0.1")
        errors, foreign, posts = [], [], []
        try:
            await server.start_server()
            site.origin = str(server.make_url("/")).rstrip("/")
            site.security = AdminSecurity(b"s" * 32, site.origin, allow_loopback_http=True)
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
                    await page.get_by_role("link", name="Add speech cameras", exact=True).click()
                    await expect(page.get_by_role("heading", name="Add speech cameras")).to_be_visible()
                    assert await page.locator("input[name='add_camera']").count() == 1
                    assert await page.locator("input[name='add_camera']:checked").count() == 0
                    assert await page.locator("not,script,img").count() == 0
                    assert "b" * 24 not in await page.content()
                    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                    await capture(page, output / (name + "-speech-cameras.png"))

                    async def review():
                        await page.locator("input[name='add_camera']").check()
                        await page.get_by_role("button", name="Preview speech additions", exact=True).click()
                        await expect(page.get_by_role("heading", name="Review speech additions")).to_be_visible()
                        assert path.read_bytes() == before
                        assert await page.locator("input[type='checkbox']:checked").count() == 0
                        assert await page.get_by_text("Audio destination:", exact=False).count() == 1
                        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")

                    async def acknowledge():
                        for key in ("acknowledge_audio", "acknowledge_cost", "confirm"):
                            await page.locator("input[name='" + key + "']").check()

                    await review()
                    await capture(page, output / (name + "-speech-review.png"))
                    await acknowledge()
                    observed_name[0] = "A different microphone location"
                    await page.get_by_role("button", name="Confirm speech additions", exact=True).click()
                    await expect(page.get_by_role("heading", name="Speech cameras not added")).to_be_visible()
                    assert path.read_bytes() == before
                    observed_name[0] = "Fictional patio <not markup>"
                    site._inventory_snapshot = None
                    await page.get_by_role("link", name="Add speech cameras", exact=True).click()
                    await review()
                    await acknowledge()
                    await page.get_by_role("button", name="Confirm speech additions", exact=True).focus()
                    await page.keyboard.press("Enter")
                    await expect(page.get_by_role("heading", name="Saved audio permissions")).to_be_visible()
                    expected = json.loads(before)
                    expected["speech_to_text"]["camera_ids"] += ["b" * 24]
                    assert json.loads(path.read_bytes()) == expected
                    await capture(page, output / (name + "-speech-saved.png"))
                    assert await page.evaluate("localStorage.length + sessionStorage.length") == 0
                    assert not list(path.parent.glob("speech-key-*"))
                    site.security._clock = lambda: time.time() + 86400
                    await page.goto(site.origin + "/speech-camera-enrollment")
                    await expect(page.get_by_role("heading", name="Administrator sign in")).to_be_visible()
                    assert not foreign and not errors
                    assert posts == ["/login", "/speech-camera-enrollment/preview", "/speech-camera-enrollment/apply",
                                     "/speech-camera-enrollment/preview", "/speech-camera-enrollment/apply"]
                    result = {"viewport": viewport, "browser_version": context.browser.version,
                        "unchecked_camera_and_consents": True, "escaped_named_choice": True,
                        "changed_microphone_refused": True, "saved_additions_only": True,
                        "provider_and_audio_limits_preserved": True, "keyboard_confirmation": True,
                        "expired_session_refused": True, "foreign_requests": 0,
                        "real_audio_provider_controller_or_media_requests": 0,
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

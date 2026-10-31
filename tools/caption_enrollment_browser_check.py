"""Fictional explicit caption camera addition browser flow; no processor/provider/controller starts."""

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


async def capture(page, target):
    await page.evaluate("document.activeElement.blur(); scrollTo(0, 0)")
    await page.evaluate(
        "new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
    )
    assert (await page.get_by_role("heading", level=1).bounding_box())["y"] >= 19
    assert await page.evaluate(
        "scrollY === 0 && (!visualViewport || visualViewport.offsetTop === 0)"
    )
    await page.screenshot(path=str(target), full_page=True)


async def scenario(output, name, viewport, executable):
    from playwright.async_api import async_playwright, expect

    with tempfile.TemporaryDirectory(prefix="owned-caption-enrollment-", dir=output) as temporary:
        root = Path(temporary)
        path = root / "key/config.json"
        initialize(path, path.parent, controller_host="127.0.0.1")
        raw = json.loads(path.read_bytes())
        raw["runtime"]["state_dir"] = "/state"
        raw["controller"]["protect_version"] = "7.3.74"
        raw["worker"].update(
            callback_mode="enabled",
            request_mp4_exports=True,
            continuous={
                "enabled": True,
                "api_key_file": "/state/CANARY-inventory-key",
                "web_trust_file": "/state/CANARY-controller-trust",
                "web_cert_file": "/state/CANARY-cert",
                "refresh_seconds": 60,
                "camera_models": ["UVC G5 Flex"],
                "camera_ids": ["a" * 24, "b" * 24],
                "unmetered": True,
            },
        )
        raw["inference"] = {
            "provider": "ollama",
            "model": "fictional-cost-caption",
            "base_url": "http://127.0.0.1:9",
            "allow_insecure_http": True,
        }
        path.write_text(json.dumps(raw) + "\n")
        path.chmod(0o600)
        journal = path.parent / "caption-budget.json"
        journal.write_text("CANARY-fictional-journal-must-stay-identical")
        journal.chmod(0o600)
        original = path.read_bytes()
        password = "fictional caption policy browser passphrase"
        site = ControlSite(
            path,
            None,
            signing_key=b"c" * 32,
            password_record=AdminSecurity.create_password_record(password),
            port=8765,
        )
        site.aikey.snapshot()

        async def inventory():
            return {
                "schema": "aikey-camera-preflight/1",
                "protect_version": "7.3.74",
                "fetched_at": int(time.time()),
                "cameras": [
                    {
                        "id": c * 24,
                        "mac": "02000000000" + str(i),
                        "name": title,
                        "model": "UVC G5 Flex",
                        "state": "CONNECTED",
                        "smart_detect_types": ["person"],
                        "audio_detect_types": [],
                    }
                    for i, (c, title) in enumerate(
                        [
                            ("a", "Fictional office"),
                            ("b", "Fictional garden"),
                            ("c", "Fictional addition"),
                        ],
                        1,
                    )
                ],
            }

        site.inventory_loader = inventory
        server = TestServer(site.app(), host="127.0.0.1")
        await server.start_server()
        site.origin = str(server.make_url("/")).rstrip("/")
        site.security = AdminSecurity(b"c" * 32, site.origin, allow_loopback_http=True)
        posts, errors, unowned = [], [], []
        try:
            async with async_playwright() as driver:
                context = await driver.chromium.launch_persistent_context(
                    user_data_dir=str(root / "browser"),
                    executable_path=executable,
                    headless=True,
                    chromium_sandbox=True,
                    viewport=viewport,
                    is_mobile=name == "mobile",
                    device_scale_factor=1,
                    args=[
                        "--disable-background-networking",
                        "--disable-component-update",
                        "--disable-sync",
                        "--no-first-run",
                    ],
                )
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
                    page.on(
                        "request",
                        lambda r: (
                            posts.append(urlsplit(r.url).path) if r.method == "POST" else None
                        ),
                    )
                    await page.goto(site.origin + "/caption-camera-enrollment")
                    await expect(
                        page.get_by_role("heading", name="Administrator sign in")
                    ).to_be_visible()
                    await page.get_by_label("Password", exact=True).fill(password)
                    async with page.expect_navigation():
                        await page.get_by_role("button", name="Sign in", exact=True).click()
                    await page.get_by_role("link", name="Add caption cameras", exact=True).click()
                    await expect(
                        page.get_by_role("heading", name="Add caption cameras", exact=True)
                    ).to_be_visible()
                    choice = page.get_by_role(
                        "checkbox", name="Fictional addition (UVC G5 Flex)", exact=True
                    )
                    assert not await choice.is_checked()
                    assert await page.get_by_role("checkbox").count() == 1
                    content = await page.content()
                    assert all(c * 24 not in content for c in "abc")
                    assert "CANARY" not in content and password not in content
                    assert await page.evaluate(
                        "document.documentElement.scrollWidth <= innerWidth + 1"
                    )
                    await capture(page, output / (name + "-caption-additions.png"))
                    await choice.focus()
                    await page.keyboard.press("Space")
                    async with page.expect_navigation():
                        await page.get_by_role(
                            "button", name="Preview caption additions", exact=True
                        ).click()
                    await expect(
                        page.get_by_role("heading", name="Review caption additions", exact=True)
                    ).to_be_visible()
                    assert path.read_bytes() == original
                    assert "Fictional addition" in await page.content()
                    assert "Fictional garden" not in await page.content()
                    assert await page.get_by_role("checkbox").count() == 3
                    for box in await page.get_by_role("checkbox").all():
                        assert not await box.is_checked()
                    await capture(page, output / (name + "-review-additions.png"))
                    confirm = page.get_by_role(
                        "button", name="Confirm caption additions", exact=True
                    )
                    await confirm.click()
                    assert posts == ["/login", "/caption-camera-enrollment/preview"]
                    processing = page.get_by_role(
                        "checkbox", name="Allow automatic processing", exact=False
                    )
                    await processing.focus()
                    await page.keyboard.press("Space")
                    await confirm.click()
                    assert posts == ["/login", "/caption-camera-enrollment/preview"]
                    bypass = page.get_by_role(
                        "checkbox", name="Use the existing local-unmetered override", exact=False
                    )
                    await bypass.focus()
                    await page.keyboard.press("Space")
                    approval = page.get_by_role(
                        "checkbox", name="Save only these reviewed", exact=False
                    )
                    await approval.focus()
                    await page.keyboard.press("Space")
                    async with page.expect_navigation():
                        await confirm.press("Enter")
                    await expect(
                        page.get_by_role("heading", name="Caption additions saved", exact=True)
                    ).to_be_visible()
                    expected = json.loads(json.dumps(raw))
                    expected["worker"]["continuous"]["camera_ids"] += ["c" * 24]
                    assert json.loads(path.read_bytes()) == expected
                    assert journal.read_text() == "CANARY-fictional-journal-must-stay-identical"
                    assert not site.aikey.provider_rollback_status()["available"]
                    assert not site.admin_api.caption_camera_enrollment.grants
                    assert (
                        "CANARY" not in await page.content()
                        and password not in await page.content()
                    )
                    assert await page.evaluate("localStorage.length + sessionStorage.length") == 0
                    assert await page.evaluate(
                        "document.documentElement.scrollWidth <= innerWidth + 1"
                    )
                    await capture(page, output / (name + "-saved-additions.png"))
                    await page.get_by_role(
                        "link", name="Read saved caption cameras", exact=True
                    ).click()
                    await expect(
                        page.get_by_role("checkbox", name="Fictional addition", exact=True)
                    ).to_be_checked()
                    assert await page.get_by_role("checkbox").count() == 3
                    site.security._clock = lambda: time.time() + 86400
                    await page.goto(site.origin + "/caption-camera-enrollment")
                    await expect(
                        page.get_by_role("heading", name="Administrator sign in")
                    ).to_be_visible()
                    assert posts == [
                        "/login",
                        "/caption-camera-enrollment/preview",
                        "/caption-camera-enrollment/apply",
                    ]
                    assert not errors and not unowned
                    result = {
                        "viewport": viewport,
                        "browser_version": context.browser.version,
                        "eligible_named_addition_only": True,
                        "preview_no_write": True,
                        "keyboard_confirmed_addition": True,
                        "processing_and_bypass_separately_confirmed": True,
                        "only_saved_caption_scope_changed": True,
                        "journal_bytes_preserved": True,
                        "provider_undo_not_available": True,
                        "session_expiry_refused": True,
                        "browser_storage_or_private_echo": False,
                        "foreign_requests": 0,
                        "actual_processor_model_controller_or_media_operations": 0,
                        "body_horizontal_overflow": False,
                        "visual_review": "not_performed_by_runner",
                    }
                finally:
                    await context.close()
        finally:
            await server.close()
    assert not root.exists()
    return {**result, "owned_profiles_and_servers_collected": True}


async def main(args):
    args.output.mkdir(mode=0o700, parents=True, exist_ok=True)
    before = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted((REPO / "src/aikey").glob("*.py"))
    }
    results = {}
    for name, viewport in [
        ("desktop", {"width": 1200, "height": 900}),
        ("mobile", {"width": 390, "height": 844}),
    ]:
        results[name] = await scenario(args.output, name, viewport, args.browser_executable)
    assert before == {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted((REPO / "src/aikey").glob("*.py"))
    }
    (args.output / "result.json").write_text(
        json.dumps({"scenarios": results, "source_modules": before}, indent=2) + "\n"
    )
    print(json.dumps({"desktop_mobile_keyboard": "passed", "owned_resources_collected": True}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--browser-executable")
    asyncio.run(main(parser.parse_args()))

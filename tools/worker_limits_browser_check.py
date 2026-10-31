"""Disposable real-browser processing-limits flow; no live configuration."""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
from aiohttp.test_utils import TestServer  # noqa: E402
from aikey.admin_security import AdminSecurity  # noqa: E402
from aikey.config import initialize  # noqa: E402
from aikey.control_site import ControlSite  # noqa: E402


async def scenario(output, name, viewport, executable):
    from playwright.async_api import async_playwright, expect

    with tempfile.TemporaryDirectory(prefix="owned-worker-limits-", dir=output) as temporary:
        root = Path(temporary)
        path = root / "key/config.json"
        initialize(path, path.parent, controller_host="127.0.0.1")
        raw = json.loads(path.read_bytes())
        raw["worker"].update(max_concurrency=4, inference_concurrency=1)
        path.write_text(json.dumps(raw) + "\n")
        path.chmod(0o600)
        original = path.read_bytes()
        password = "fictional disposable browser passphrase"
        site = ControlSite(
            path,
            None,
            signing_key=b"c" * 32,
            password_record=AdminSecurity.create_password_record(password),
            port=8765,
        )
        server = TestServer(site.app(), host="127.0.0.1")
        await server.start_server()
        site.origin = str(server.make_url("/")).rstrip("/")
        site.security = AdminSecurity(b"c" * 32, site.origin, allow_loopback_http=True)
        unowned, errors = [], []
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
                    page.on("pageerror", lambda e: errors.append(str(e)))
                    await page.goto(site.origin + "/worker-limits")
                    await page.get_by_label("Password", exact=True).fill(password)
                    await page.get_by_role("button", name="Sign in", exact=True).click()
                    await page.goto(site.origin + "/worker-limits")
                    await expect(page.get_by_role("heading", level=1)).to_have_text(
                        "AI Key processing limits"
                    )
                    for field in [
                        "max_queue",
                        "max_concurrency",
                        "inference_concurrency",
                        "timeout_s",
                        "caption_timeout_s",
                        "speech_timeout_s",
                    ]:
                        assert (
                            await page.locator(f"[name={field}]").get_attribute("required")
                            is not None
                        )
                    await page.screenshot(path=str(output / f"{name}-limits.png"), full_page=True)
                    assert not await page.evaluate("document.body.scrollWidth > innerWidth")
                    await page.get_by_label("Queued jobs", exact=True).fill("16")
                    await page.get_by_role(
                        "button", name="Preview processing limits", exact=True
                    ).focus()
                    await page.keyboard.press("Enter")
                    await expect(page.get_by_role("heading", level=1)).to_have_text(
                        "Review processing limits"
                    )
                    assert path.read_bytes() == original
                    checkboxes = page.get_by_role("checkbox")
                    assert await checkboxes.count() == 2
                    assert (
                        not await checkboxes.nth(0).is_checked()
                        and not await checkboxes.nth(1).is_checked()
                    )
                    await page.screenshot(path=str(output / f"{name}-review.png"), full_page=True)
                    await page.get_by_role(
                        "button", name="Confirm saved processing limits", exact=True
                    ).click()
                    await expect(page.get_by_role("heading", level=1)).to_have_text(
                        "Review processing limits"
                    )
                    assert path.read_bytes() == original
                    for i in [0, 1]:
                        await checkboxes.nth(i).focus()
                        await page.keyboard.press("Space")
                    await page.get_by_role(
                        "button", name="Confirm saved processing limits", exact=True
                    ).focus()
                    await page.keyboard.press("Enter")
                    await expect(page.get_by_role("heading", level=1)).to_have_text(
                        "Processing limits saved"
                    )
                    changed = json.loads(path.read_bytes())
                    assert changed["worker"].pop("max_queue") == 16
                    before = json.loads(original)
                    before["worker"].pop("max_queue")
                    assert changed == before
                    await page.screenshot(path=str(output / f"{name}-saved.png"), full_page=True)
                    assert not errors and not unowned
                    assert await page.evaluate("localStorage.length + sessionStorage.length") == 0
                    return {
                        "viewport": viewport,
                        "browser_version": context.browser.version,
                        "keyboard_preview_and_confirmation": True,
                        "two_unchecked_acknowledgements": True,
                        "native_required_consent_refusal": True,
                        "only_saved_queue_changed": True,
                        "preview_no_write": True,
                        "foreign_requests": 0,
                        "real_model_controller_processor_or_media_actions": 0,
                        "visual_review": "not_performed_by_runner",
                        "owned_browser_and_server_collected": True,
                    }
                finally:
                    await context.close()
        finally:
            await server.close()


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--executable", default="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    before = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (REPO / "src/aikey").glob("*.py")
    }
    result = {"scenarios": {}}
    for name, viewport in [
        ("desktop", {"width": 1200, "height": 900}),
        ("mobile", {"width": 390, "height": 844}),
    ]:
        result["scenarios"][name] = await scenario(args.output, name, viewport, args.executable)
    assert before == {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (REPO / "src/aikey").glob("*.py")
    }
    result["module_hashes"] = before
    (args.output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                "status": "passed",
                "scenarios": list(result["scenarios"]),
                "source_modules": len(before),
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(main())

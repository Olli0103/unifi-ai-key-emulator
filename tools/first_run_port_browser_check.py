"""Fictional fresh Port preparation in sandboxed Chrome; never real device access."""

import argparse
import asyncio
import hashlib
import io
import json
from pathlib import Path
import ssl
import sys
import tempfile
from urllib.parse import urlsplit

from PIL import Image

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from aiohttp.test_utils import TestServer  # noqa: E402
from aikey.admin_security import AdminSecurity  # noqa: E402
from aikey.aiport_deployment import plan_ai_ports  # noqa: E402
from aikey.first_run_port_site import FirstRunPortSite  # noqa: E402
from aikey.tls import ensure_identity_certificate  # noqa: E402


async def capture(page, path, *, keep_focus=False):
    await page.wait_for_load_state("load")
    # Navigation can expose the heading before the init-script DOM event fires.
    # Ensure the actual saved pixels are labeled, not just the fixture metadata.
    await page.evaluate("""() => {
        let b=document.getElementById('fictional-port-banner');
        if(!b){b=document.createElement('div');b.id='fictional-port-banner';document.body.prepend(b);}
        b.setAttribute('role','banner');
        b.textContent='FICTIONAL OFFLINE TEST — no real cameras or controller';
        b.style='padding:12px;background:#fff2b3;color:#17202a;font-weight:bold';
    }""")
    assert await page.locator("#fictional-port-banner").is_visible()
    for control in await page.locator("input:not([type=hidden]):not([type=checkbox]),button").all():
        assert (await control.bounding_box())["height"] >= 44
    for checkbox in await page.locator("input[type=checkbox]").all():
        assert (await checkbox.locator("..").bounding_box())["height"] >= 44
    if not keep_focus:
        await page.evaluate("document.activeElement.blur()")
    await page.evaluate("scrollTo(0, 0)")
    await page.evaluate("new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    before = await page.locator("#fictional-port-banner").bounding_box()
    assert before["y"] >= 0 and before["height"] >= 40
    # DOM visibility does not prove Chrome's saved compositor surface is fresh.
    # Bound the capture retry and verify actual decoded fixture-banner pixels.
    for _ in range(2):
        pixels = await page.screenshot(full_page=True)
        with Image.open(io.BytesIO(pixels)) as saved:
            marker = saved.convert("RGB").getpixel((int(before["x"]) + 4, int(before["y"]) + 4))
        if marker == (255, 242, 179):
            break
    else:
        raise AssertionError("Saved screenshot lacks the fictional fixture banner")
    path.write_bytes(pixels)
    after = await page.locator("#fictional-port-banner").bounding_box()
    assert before == after


async def scenario(output, name, viewport, executable):
    from playwright.async_api import async_playwright, expect

    with tempfile.TemporaryDirectory(prefix="owned-fictional-port-", dir=output) as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        controller = root / "fictional-controller"
        controller.mkdir(mode=0o700)
        cert, _ = ensure_identity_certificate(controller, "02000000ABCD")
        pin = hashlib.sha256(ssl.PEM_cert_to_DER_cert(cert.read_text())).hexdigest()
        plan = plan_ai_ports(
            {
                "schema": "aikey-camera-preflight/1",
                "cameras": [
                    {
                        "id": str(i) * 24,
                        "model": "UVC G3 Instant",
                        "state": "CONNECTED",
                        "processing_class": "legacy_ingress_needed",
                    }
                    for i in (1, 2)
                ],
            },
            device_ips=["192.168.20.20"],
            ai_key_ip="192.168.20.21",
        )
        plan_path = root / "fictional-reviewed-plan.json"
        plan_path.write_text(json.dumps(plan))
        plan_path.chmod(0o600)
        states = root / "fictional-new-ports"
        states.mkdir(mode=0o700)
        directory = states / "slot-1"
        password = "fictional-browser-only-passphrase"
        site = FirstRunPortSite(
            directory,
            plan=plan_path,
            slot=1,
            controller_cert_file=cert,
            signing_key=b"f" * 32,
            password_record=AdminSecurity.create_password_record(password),
            port=8765,
            audit_log=root / "fictional-audit.jsonl",
        )
        server = TestServer(site.app(), host="127.0.0.1")
        foreign, errors, posts = [], [], []
        try:
            await server.start_server()
            site.origin = str(server.make_url("/")).rstrip("/")
            site.security = AdminSecurity(b"f" * 32, site.origin, allow_loopback_http=True)
            async with async_playwright() as driver:
                context = await driver.chromium.launch_persistent_context(
                    user_data_dir=str(root / "fictional-browser-profile"),
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

                    async def route(request_route):
                        parsed = urlsplit(request_route.request.url)
                        if f"{parsed.scheme}://{parsed.netloc}" != site.origin:
                            foreign.append(request_route.request.url)
                            await request_route.abort()
                        else:
                            await request_route.continue_()

                    await context.route("**/*", route)
                    await context.add_init_script("""addEventListener('DOMContentLoaded', () => {
                        if(document.getElementById('fictional-port-banner'))return;
                        const b=document.createElement('div');b.id='fictional-port-banner';
                        b.setAttribute('role','banner');
                        b.textContent='FICTIONAL OFFLINE TEST — no real cameras or controller';
                        b.style='padding:12px;background:#fff2b3;color:#17202a;font-weight:bold';
                        document.body.prepend(b);
                    });""")
                    page = await context.new_page()
                    page.on("pageerror", lambda e: errors.append(str(e)))
                    page.on(
                        "request",
                        lambda r: (
                            posts.append(urlsplit(r.url).path) if r.method == "POST" else None
                        ),
                    )
                    await page.goto(site.origin)
                    await page.get_by_label("Password", exact=True).fill(password)
                    await page.get_by_role("button", name="Sign in", exact=True).click()
                    await expect(
                        page.get_by_role("heading", name="Prepare one fresh AI Port")
                    ).to_be_visible()
                    assert await page.locator("input[type='checkbox']:checked").count() == 0
                    assert not directory.exists()

                    async def fill(fingerprint):
                        await page.get_by_label("Controller private IPv4", exact=True).fill(
                            "192.168.20.1"
                        )
                        await page.get_by_label(
                            "Independently verified controller SHA-256 fingerprint", exact=True
                        ).fill(fingerprint)
                        await page.locator("input[name='acknowledge_trust']").check()
                        await page.locator("input[name='acknowledge_preparation']").check()

                    if name == "desktop":
                        await capture(page, output / "desktop-port-setup.png")
                        await fill("0" * 64)
                        await page.get_by_role(
                            "button", name="Prepare one new private AI Port profile"
                        ).click()
                        await expect(
                            page.get_by_role("heading", name="Preparation not verified")
                        ).to_be_visible()
                        assert not directory.exists()
                        await page.goto(site.origin)
                        await fill(pin)
                        plan_path.write_bytes(plan_path.read_bytes() + b"\n")
                        await page.get_by_role(
                            "button", name="Prepare one new private AI Port profile"
                        ).click()
                        await expect(
                            page.get_by_role("heading", name="Selected inputs changed")
                        ).to_be_visible()
                        assert not directory.exists()
                        await page.goto(site.origin)
                        await fill(pin)
                        await page.get_by_role(
                            "button", name="Prepare one new private AI Port profile"
                        ).click()
                    elif name == "keyboard":
                        await page.get_by_label("Controller private IPv4", exact=True).focus()
                        await page.keyboard.type("192.168.20.1")
                        await page.keyboard.press("Tab")
                        await page.keyboard.type(pin)
                        for _ in range(2):
                            await page.keyboard.press("Tab")
                            await page.keyboard.press("Space")
                        await page.keyboard.press("Tab")
                        await expect(
                            page.get_by_role(
                                "button", name="Prepare one new private AI Port profile"
                            )
                        ).to_be_focused()
                        await capture(page, output / "keyboard-port-setup.png", keep_focus=True)
                        await page.keyboard.press("Enter")
                    else:
                        await capture(page, output / "mobile-port-setup.png")
                        await fill(pin)
                        await page.get_by_role(
                            "button", name="Prepare one new private AI Port profile"
                        ).click()
                    await expect(
                        page.get_by_role("heading", name="Fresh AI Port files prepared")
                    ).to_be_visible()
                    raw = json.loads((directory / "config.json").read_text())
                    assert set(raw) == {
                        "controller_ip",
                        "device_ip",
                        "controller_pin",
                        "firmware_version",
                        "mac",
                    }
                    assert raw["controller_pin"] == pin and raw["device_ip"] == "192.168.20.20"
                    assert all(p.stat().st_mode & 0o777 == 0o600 for p in directory.iterdir())
                    key = (directory / "device.key").read_text()
                    assert key not in await page.content()
                    assert await page.evaluate("localStorage.length + sessionStorage.length") == 0
                    await capture(page, output / (name + "-port-prepared.png"))
                    before = {p.name: p.read_bytes() for p in directory.iterdir()}
                    await page.get_by_role("button", name="Sign out", exact=True).click()
                    await expect(
                        page.get_by_role("heading", name="Administrator sign in")
                    ).to_be_visible()
                    assert before == {p.name: p.read_bytes() for p in directory.iterdir()}
                    assert not foreign and not errors
                    assert set(posts) <= {"/login", "/create", "/logout"}
                    expected_posts = (
                        ["/login"] + ["/create"] * (3 if name == "desktop" else 1) + ["/logout"]
                    )
                    assert posts == expected_posts
                    result = {
                        "viewport": viewport,
                        "browser_version": context.browser.version,
                        "fixture_banner_test_only": True,
                        "unchecked_consents_initially": True,
                        "wrong_pin_and_stale_plan_refused": name == "desktop",
                        "keyboard_tab_space_enter": name == "keyboard",
                        "fresh_passive_files_only": True,
                        "keys_not_in_dom_or_storage": True,
                        "post_csrf_logout": True,
                        "page_foreign_requests": 0,
                        "inference_or_adoption_requested": False,
                        "new_form_targets_at_least_44px": True,
                        "horizontal_overflow": False,
                        "visual_review": "not_performed_by_runner",
                    }
                finally:
                    await context.close()
        finally:
            await server.close()
    assert not root.exists()
    return {**result, "owned_resources_collected": True}


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
        ("keyboard", {"width": 1200, "height": 900}),
    ]:
        results[name] = await scenario(args.output, name, viewport, args.browser_executable)
    after = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted((REPO / "src/aikey").glob("*.py"))
    }
    assert before == after
    (args.output / "result.json").write_text(
        json.dumps({"scenarios": results, "source_modules": before}, indent=2) + "\n"
    )
    print(json.dumps({"desktop_mobile_keyboard": "passed", "owned_resources_collected": True}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--browser-executable")
    asyncio.run(main(parser.parse_args()))

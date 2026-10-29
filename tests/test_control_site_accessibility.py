"""Static semantics and palette checks, not browser/assistive-technology acceptance."""

from html.parser import HTMLParser
import re

from aiohttp import web
import pytest

from aikey.control_demo import demo_page
from aikey.control_site import _page, _STYLE
from test_control_site_catalog import configured


class Structure(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.mains = []
        self.ids = []
        self.links = []
        self.controls = []
        self.labels = set()
        self.tabindices = []
        self.references = []
    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if attrs.get("id"):
            self.ids.append(attrs["id"])
        if attrs.get("aria-labelledby"):
            self.references.extend(attrs["aria-labelledby"].split())
        if tag == "main":
            self.mains.append(attrs)
        if tag == "a":
            self.links.append(attrs)
        if tag == "label" and attrs.get("for"):
            self.labels.add(attrs["for"])
        if "tabindex" in attrs:
            self.tabindices.append(int(attrs["tabindex"]))
        if tag in {"input", "select", "textarea"} and attrs.get("type") != "hidden":
            self.controls.append((attrs, any(ancestor == "label" for ancestor in self.stack)))
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self.stack.append(tag)
    def handle_endtag(self, tag):
        if tag in self.stack:
            self.stack = self.stack[:len(self.stack) - 1 - self.stack[::-1].index(tag)]


def parsed(markup):
    result = Structure()
    result.feed(markup)
    return result


def assert_landmark(markup):
    page = parsed(markup)
    assert len(page.mains) == 1
    assert len(page.ids) == len(set(page.ids))
    assert all(reference in page.ids for reference in page.references)
    target = page.mains[0].get("id")
    assert target and any(link.get("href") == "#" + target for link in page.links)
    assert not any(index > 0 for index in page.tabindices)
    return page


def test_shared_admin_renderer_has_single_focusable_main_and_skip_link():
    page = assert_landmark(_page("Synthetic fixture", "<h1>Synthetic fixture</h1>").text)
    assert page.mains[0].get("tabindex") == "-1"
    assert page.links[0]["href"] == "#" + page.mains[0]["id"]


def test_synthetic_demo_keeps_one_main_and_unique_ids():
    assert_landmark(demo_page().text)


async def test_real_signed_in_markup_names_controls_and_changes_no_settings(tmp_path):
    seen = []
    async def endpoint(request):
        seen.append(request.path)
        return web.json_response({"segments": []})
    upstream, site, server, client, cookie, path = await configured(tmp_path, endpoint)
    try:
        before = path.read_bytes(), site.aiport.path.read_bytes()
        for route in ("/", "/data-retention"):
            response = await client.get(route)
            assert response.status == 200
            markup = await response.text()
            page = assert_landmark(markup)
            for attrs, implicit in page.controls:
                assert (implicit or attrs.get("id") in page.labels
                        or attrs.get("aria-label", "").strip()
                        or attrs.get("aria-labelledby", "").strip()), attrs.get("name")
        assert seen == [] and before == (path.read_bytes(), site.aiport.path.read_bytes())
    finally:
        await client.close()
        await server.close()
        await upstream.close()


def color(selector, property):
    rule = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", _STYLE)
    assert rule, selector
    declaration = re.search(r"(?:^|;)\s*" + re.escape(property) + r"\s*:\s*([^;]+)", rule[1])
    assert declaration, (selector, property)
    value = re.search(r"#[0-9a-fA-F]{6}\b|\bwhite\b", declaration[1])
    assert value, (selector, property)
    return "#ffffff" if value[0] == "white" else value[0]


def contrast(a, b):
    def luminance(value):
        components = [int(value[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in components]
        return sum(v * weight for v, weight in zip(linear, (.2126, .7152, .0722)))
    low, high = sorted((luminance(a), luminance(b)))
    return (high + .05) / (low + .05)


@pytest.mark.parametrize("selector", [":root", ".muted", ".error", ".notice"])
@pytest.mark.parametrize("background", ["#ffffff", "#f4f7fb"])
def test_declared_normal_text_palette_meets_aa_contrast(selector, background):
    assert contrast(color(selector, "color"), background) >= 4.5


def test_declared_button_text_and_input_boundary_contrast():
    assert contrast(color("button", "color"), color("button", "background")) >= 4.5
    assert contrast(color("input, select", "border"), "#ffffff") >= 3


def test_keyboard_focus_palette_is_distinguishable_from_both_surfaces():
    for background in ("#ffffff", "#f4f7fb"):
        assert contrast(color(":focus-visible", "outline"), background) >= 3

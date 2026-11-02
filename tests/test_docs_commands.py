"""Project CLI examples parse and local doc file targets exist (#25).

Installed console entries come from pyproject, including their declared callable.
This does not execute shell/Docker/tool scripts or check anchors/external URLs.
Commands are parsed by the real argparse parsers and stopped right after
parsing, so nothing is executed, no file is written and no network is used.
"""

import argparse
import importlib
from pathlib import Path
import re
import shlex
import tomllib

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCS = sorted([*ROOT.glob("*.md"), *(ROOT / "docs").rglob("*.md")])
_LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)")
_BLOCK = re.compile(r"```[a-z]*\n(.*?)```", re.S)
_INLINE = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")
# Keep discovery aligned with the actual installed console scripts, not a hand list.
_ENTRY = {name: target.split(":", 1) for name, target in
          tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["scripts"].items()}


class _Parsed(Exception):
    """Raised instead of running a command once its arguments parsed."""


class _Refused(Exception):
    """Raised instead of argparse's error exit, carrying its message."""


def _links():
    for doc in DOCS:
        for target in _LINK.findall(doc.read_text()):
            if re.match(r"[a-z][a-z0-9+.-]*:", target) or target.startswith("#"):
                continue
            yield doc, target


def _snippets(text):
    for block in _BLOCK.findall(text):
        for line in block.replace("\\\n", " ").splitlines():
            yield "block", line
    for span in _INLINE.findall(_BLOCK.sub("", text)):
        # Inline references may show optional groups and placeholders.
        span = re.sub(r"\[[^\]]*\]", " ", span)
        yield "inline", re.sub(r"<[^>]+>", "PLACEHOLDER", span)


def _commands():
    for doc in DOCS:
        for kind, line in _snippets(doc.read_text()):
            line = re.split(r"\s(?:\||&&|;|>|2>)\s?", line.strip())[0]
            try:
                words = shlex.split(line)
            except ValueError:
                continue
            while words and re.fullmatch(r"[A-Z_][A-Z0-9_]*=.*", words[0]):
                words = words[1:]                                       # env assignments
            if not words:
                continue
            program = Path(words[0]).name
            if program in _ENTRY:
                yield doc, kind, line, *_ENTRY[program], words[1:]
            elif (re.fullmatch(r"python3?(\.\d+)?", program) and words[1:2] == ["-m"]
                  and len(words) > 2 and words[2].startswith("aikey.")):
                yield doc, kind, line, words[2], "main", words[3:]


def _typed_examples(argv):
    # These documented numeric placeholders are not real host settings. Substitute
    # only their exact option/value pair for argparse; unknown values still fail.
    examples = {("--uid", "NAS_NONROOT_UID"): "1000",
                ("--gid", "NAS_NONROOT_GID"): "1000",
                ("--slot", "SLOT_NUMBER"): "1"}
    return [examples.get((argv[i - 1] if i else "", value), value)
            for i, value in enumerate(argv)]


LINKS = list(_links())
COMMANDS = list(_commands())


def test_the_docs_contain_links_and_commands_to_check():
    assert len(LINKS) >= 10 and len(COMMANDS) >= 10


@pytest.mark.parametrize("doc,target", LINKS,
                         ids=[f"{d.relative_to(ROOT)}->{t}" for d, t in LINKS])
def test_relative_doc_links_resolve(doc, target):
    path = target.split("#", 1)[0]
    assert path, target
    assert (doc.parent / path).exists(), f"{doc.relative_to(ROOT)} links to missing {target}"


@pytest.mark.parametrize("doc,kind,line,module,function,argv", COMMANDS,
                         ids=[f"{d.relative_to(ROOT)}:{k}:{m}:{' '.join(a[:1])}"
                              for d, k, _, m, _f, a in COMMANDS])
def test_documented_commands_parse(monkeypatch, doc, kind, line, module, function, argv):
    original = argparse.ArgumentParser.parse_args

    def parse_then_stop(self, args=None, namespace=None):
        original(self, args, namespace)
        raise _Parsed

    def refuse(self, message):
        raise _Refused(message)
    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", parse_then_stop)
    monkeypatch.setattr(argparse.ArgumentParser, "error", refuse)
    main = getattr(importlib.import_module(module), function)
    try:
        main(_typed_examples(argv))
    except _Parsed:
        return
    except _Refused as exc:
        # Existing inline references can omit required arguments. A bare command
        # can also have a required mutually-exclusive input group; no flag-bearing
        # snippet receives that extra exception.
        if kind == "inline" and str(exc).startswith("the following arguments are required"):
            return
        if (kind == "inline" and not argv
                and str(exc).startswith("one of the arguments ")
                and str(exc).endswith(" is required")):
            return
        pytest.fail(f"{doc.relative_to(ROOT)}: `{line}` does not parse: {exc}")
    pytest.fail(f"{doc.relative_to(ROOT)}: `{line}` never reached argument parsing")


@pytest.mark.parametrize("module,argv", [
    ("aikey.cli", ["no-such-command"]),
    ("aikey.cli", ["lab", "--no-such-flag"]),
    ("aikey.search_backup", ["prune", "--out", "x", "--keep", "many"]),
])
def test_the_checker_catches_a_wrong_command(monkeypatch, module, argv):
    def refuse(self, message):
        raise _Refused(message)
    monkeypatch.setattr(argparse.ArgumentParser, "error", refuse)
    with pytest.raises(_Refused):
        importlib.import_module(module).main(argv)


def test_the_checker_catches_a_missing_link(tmp_path):
    doc = tmp_path / "page.md"
    doc.write_text("See [the guide](missing-guide.md) and [site](https://example.org).")
    targets = [t for t in _LINK.findall(doc.read_text()) if not t.startswith("https:")]
    assert targets == ["missing-guide.md"] and not (doc.parent / targets[0]).exists()


def test_every_installed_console_entry_is_discovered_in_documentation(tmp_path, monkeypatch):
    declared = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["scripts"]
    doc = tmp_path / "commands.md"
    doc.write_text("\n".join("`" + name + " --fictional-invalid-option`" for name in declared))
    monkeypatch.setitem(globals(), "DOCS", [doc])
    found = list(_commands())
    assert {line.split()[0] for _doc, _kind, line, *_ in found} == set(declared)


def test_newly_covered_port_example_reaches_real_parser(monkeypatch, tmp_path):
    doc = tmp_path / "commands.md"
    doc.write_text("```sh\nlocal-aiport-plan --inventory fictional-not-read.json "
                   "--fictional-invalid-option\n```\n")
    monkeypatch.setitem(globals(), "DOCS", [doc])
    found = list(_commands())
    assert len(found) == 1
    assert found[0][3] == "aikey.aiport_deployment"
    def refuse(self, message):
        raise _Refused(message)
    monkeypatch.setattr(argparse.ArgumentParser, "error", refuse)
    with pytest.raises(_Refused, match="unrecognized arguments: --fictional-invalid-option"):
        importlib.import_module(found[0][3]).main(found[0][-1])


def test_typed_examples_are_exact_option_bound_operands_only():
    assert _typed_examples(["--uid", "NAS_NONROOT_UID", "--gid", "NAS_NONROOT_GID",
                            "--slot", "SLOT_NUMBER"]) == ["--uid", "1000", "--gid", "1000", "--slot", "1"]
    for value in (["NAS_NONROOT_UID"], ["--slot", "NAS_NONROOT_UID"],
                  ["--uid", "NOT_A_NUMBER"], ["--fictional-flag", "SLOT_NUMBER"]):
        assert _typed_examples(value) == value

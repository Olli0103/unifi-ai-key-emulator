"""Every documented command parses and every relative doc link resolves (#25).

Commands are parsed by the real argparse parsers and stopped right after
parsing, so nothing is executed, no file is written and no network is used.
"""

import argparse
import importlib
from pathlib import Path
import re
import shlex

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCS = sorted([*ROOT.glob("*.md"), *(ROOT / "docs").rglob("*.md")])
_LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)")
_BLOCK = re.compile(r"```[a-z]*\n(.*?)```", re.S)
_INLINE = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")
_ENTRY = {"local-aikey": "aikey.cli"}


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
                yield doc, kind, line, _ENTRY[program], words[1:]
            elif (re.fullmatch(r"python3?(\.\d+)?", program) and words[1:2] == ["-m"]
                  and len(words) > 2 and words[2].startswith("aikey.")):
                yield doc, kind, line, words[2], words[3:]


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


@pytest.mark.parametrize("doc,kind,line,module,argv", COMMANDS,
                         ids=[f"{d.relative_to(ROOT)}:{k}:{m}:{' '.join(a[:1])}"
                              for d, k, _, m, a in COMMANDS])
def test_documented_commands_parse(monkeypatch, doc, kind, line, module, argv):
    original = argparse.ArgumentParser.parse_args

    def parse_then_stop(self, args=None, namespace=None):
        original(self, args, namespace)
        raise _Parsed

    def refuse(self, message):
        raise _Refused(message)
    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", parse_then_stop)
    monkeypatch.setattr(argparse.ArgumentParser, "error", refuse)
    main = getattr(importlib.import_module(module), "main")
    try:
        main(argv)
    except _Parsed:
        return
    except _Refused as exc:
        # A prose reference need not list required arguments; anything else is wrong.
        if kind == "inline" and str(exc).startswith("the following arguments are required"):
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

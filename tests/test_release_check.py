"""Reproducible, credentials-free release artifacts (#16). Synthetic archives only."""

import importlib.util
import io
import json
import tarfile
import zipfile
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "release_check", Path(__file__).resolve().parent.parent / "tools" / "release_check.py")
release = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(release)

EPOCH = 1_790_000_000
KEY_BLOCK = "-----BEGIN " + "PRIVATE KEY-----\nsynthetic\n"


def sdist(path, files, *, mtime=1, uname="builder-account", uid=501):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(f"pkg-0.1/{name}")
            info.size, info.mtime, info.uname, info.uid = len(data), mtime, uname, uid
            archive.addfile(info, io.BytesIO(data))
    path.write_bytes(buffer.getvalue())


def wheel(path, files):
    with zipfile.ZipFile(path, "w") as archive:
        for name, text in files.items():
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            archive.writestr(info, text)


FILES = {"README.md": "hello\n", "src/pkg/__init__.py": "VALUE = 1\n", "tests/test_x.py": "def test(): pass\n"}


def dist(root, files=FILES, **kwargs):
    root.mkdir(parents=True)
    sdist(root / "pkg-0.1.tar.gz", files, **kwargs)
    wheel(root / "pkg-0.1-py3-none-any.whl", {"pkg/__init__.py": files["src/pkg/__init__.py"]})
    return root


def test_identical_content_built_at_different_times_by_different_accounts_is_byte_identical(tmp_path):
    first = release.verify(dist(tmp_path / "a", mtime=111, uname="alice", uid=501), epoch=EPOCH, forbidden=[])
    second = release.verify(dist(tmp_path / "b", mtime=999, uname="bob", uid=502), epoch=EPOCH, forbidden=[])
    assert release.compare(first, second) == []
    assert first["artifacts"] == second["artifacts"]
    with tarfile.open(tmp_path / "a" / "pkg-0.1.tar.gz") as archive:
        members = archive.getmembers()
    assert [m.name for m in members] == sorted(m.name for m in members)
    assert all((m.mtime, m.uid, m.uname) == (EPOCH, 0, "") for m in members)
    assert (tmp_path / "a" / "pkg-0.1.tar.gz").read_bytes()[4:8] == b"\0\0\0\0"   # no gzip time


def test_a_changed_file_is_a_manifest_difference_naming_the_member(tmp_path):
    base = release.verify(dist(tmp_path / "a"), epoch=EPOCH, forbidden=[])
    changed = dict(FILES, **{"README.md": "hello, changed\n"})
    other = release.verify(dist(tmp_path / "b", changed), epoch=EPOCH, forbidden=[])
    assert release.compare(base, other) == ["artifact:pkg-0.1.tar.gz", "member:pkg-0.1.tar.gz:pkg-0.1/README.md"]


@pytest.mark.parametrize("files,forbid,code", [
    (dict(FILES, **{"docs/x.md": "built in /private/build-host/checkout-7\n"}), ["/private/build-host/checkout-7"],
     "build_environment_path_or_account"),
    (dict(FILES, **{"docs/x.md": "author: builder-7f3\n"}), ["builder-7f3"], "build_environment_path_or_account"),
    (dict(FILES, **{"docs/x.md": KEY_BLOCK}), [], "private_key_block"),
    (dict(FILES, **{"state/device-state.json": "{}"}), [], "private_member"),
    (dict(FILES, **{"deployment/device.key": "x"}), [], "private_member"),
])
def test_leaks_are_refused(tmp_path, files, forbid, code):
    with pytest.raises(release.ReleaseError) as refused:
        release.verify(dist(tmp_path / "a", files), epoch=EPOCH, forbidden=forbid)
    assert code in json.loads(str(refused.value))["pkg-0.1.tar.gz"]


def test_an_unnormalized_sdist_reports_the_builder_account(tmp_path):
    root = dist(tmp_path / "a", uname="builder-account", uid=501)
    assert "builder_account_in_archive" in release.leaks(root / "pkg-0.1.tar.gz", [])
    release.normalize_sdist(root / "pkg-0.1.tar.gz", EPOCH)
    assert release.leaks(root / "pkg-0.1.tar.gz", []) == []


def test_cli_verify_and_compare(tmp_path, capsys):
    dist(tmp_path / "a")
    dist(tmp_path / "b", mtime=5, uname="someone")
    for name in ("a", "b"):
        assert release.main(["verify", str(tmp_path / name), "--epoch", str(EPOCH),
                             "--manifest-out", str(tmp_path / f"{name}.json")]) == 0
    assert release.main(["compare", str(tmp_path / "a.json"), str(tmp_path / "b.json")]) == 0
    capsys.readouterr()
    dist(tmp_path / "c", dict(FILES, **{"README.md": "different\n"}))
    release.main(["verify", str(tmp_path / "c"), "--epoch", str(EPOCH), "--manifest-out", str(tmp_path / "c.json")])
    assert release.main(["compare", str(tmp_path / "a.json"), str(tmp_path / "c.json")]) == 1
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["reproducible"] is False

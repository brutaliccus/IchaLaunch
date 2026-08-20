"""Headless smoke tests for IchaLaunch core (no GUI)."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

# Ensure project root on path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from ichalaunch.addons.github import load_catalog, parse_github_url
from ichalaunch.core.filesystem import is_protected_path, update_dlls_txt, write_dlls_txt, read_dlls_txt
from ichalaunch.mods.installer import load_mod_catalog, detect_actual_state


def test_catalogs():
    mods = load_mod_catalog()
    assert len(mods) >= 20, mods
    addons = load_catalog()
    assert len(addons) >= 500, len(addons)
    print(f"OK catalogs: {len(mods)} mods, {len(addons)} addons")


def test_github_parse():
    assert parse_github_url("https://github.com/shagu/ShaguTweaks") == ("shagu", "ShaguTweaks")
    assert parse_github_url("https://github.com/shagu/ShaguTweaks.git") == ("shagu", "ShaguTweaks")
    assert parse_github_url("not-a-url") is None
    print("OK github parse")


def test_protected():
    assert is_protected_path(r"C:\Program Files\Ravencraft")
    assert is_protected_path(r"C:\Users\x\Desktop\game")
    assert not is_protected_path(r"C:\Games\Ravencraft")
    print("OK protected paths")


def test_dlls_txt():
    with tempfile.TemporaryDirectory() as td:
        game = Path(td)
        write_dlls_txt(game, ["a.dll"])
        update_dlls_txt(game, add=["b.dll"], remove=["a.dll"])
        assert read_dlls_txt(game) == ["b.dll"]
    print("OK dlls.txt")


def test_detect_state():
    with tempfile.TemporaryDirectory() as td:
        game = Path(td)
        (game / "nampower.dll").write_bytes(b"x")
        (game / "WDB").write_text("")
        state = detect_actual_state(game)
        assert state["nampower"] is True
        assert state["wdb_block"] is True
        assert state["superwow"] is False
    print("OK detect state")


def main():
    test_catalogs()
    test_github_parse()
    test_protected()
    test_dlls_txt()
    test_detect_state()
    print("\nAll smoke tests passed.")


if __name__ == "__main__":
    main()

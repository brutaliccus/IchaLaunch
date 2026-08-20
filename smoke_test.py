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


def test_multi_folder_pack_grouping():
    from ichalaunch.core.detect import (
        group_multi_folder_addons,
        merge_addon_meta,
        resolve_catalog_entry,
    )

    cat, kind = resolve_catalog_entry("Bongos_ActionBar", include_mods=False)
    assert kind == "prefix", kind
    assert cat and (cat.get("folder") or cat.get("name") or "").lower() == "bongos"
    meta = merge_addon_meta("Bongos_ActionBar", {}, cat, match_kind="prefix")
    assert meta["name"] == "Bongos_ActionBar", meta["name"]
    assert "bongos" in (meta.get("url") or "").lower() or "bongos" in (meta.get("repository") or "").lower()

    cat_root, kind_root = resolve_catalog_entry("Bongos", include_mods=False)
    assert kind_root == "exact"
    root_meta = merge_addon_meta("Bongos", {}, cat_root, match_kind="exact")
    assert root_meta["name"] == "Bongos"

    merged = {
        "Bongos": root_meta,
        "Bongos_ActionBar": meta,
        "Bongos_XP": merge_addon_meta(
            "Bongos_XP", {}, cat, match_kind="prefix"
        ),
    }
    grouped = group_multi_folder_addons(merged)
    assert grouped["Bongos"].get("folders") and len(grouped["Bongos"]["folders"]) == 3
    assert grouped["Bongos_ActionBar"].get("managed_by") == "Bongos"
    assert grouped["Bongos_ActionBar"]["name"] == "Bongos_ActionBar"
    assert grouped["Bongos"]["name"] == "Bongos"

    # Separate catalog entries must not collapse (ShaguTweaks vs ShaguTweaks-extras)
    st, st_kind = resolve_catalog_entry("ShaguTweaks", include_mods=False)
    ste, ste_kind = resolve_catalog_entry("ShaguTweaks-extras", include_mods=False)
    assert st_kind == "exact" and ste_kind == "exact"
    separate = {
        "ShaguTweaks": merge_addon_meta("ShaguTweaks", {}, st, match_kind="exact"),
        "ShaguTweaks-extras": merge_addon_meta("ShaguTweaks-extras", {}, ste, match_kind="exact"),
    }
    sep_grouped = group_multi_folder_addons(separate)
    assert "managed_by" not in sep_grouped["ShaguTweaks-extras"]
    assert "folders" not in sep_grouped.get("ShaguTweaks", {})
    print("OK multi-folder pack grouping")


def main():
    test_catalogs()
    test_github_parse()
    test_protected()
    test_dlls_txt()
    test_detect_state()
    test_multi_folder_pack_grouping()
    print("\nAll smoke tests passed.")


if __name__ == "__main__":
    main()

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


def test_discover_game_path_near_launcher():
    from ichalaunch.game.launcher import discover_game_path_near_launcher

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        wow_dir = root / "Game"
        wow_dir.mkdir()
        (wow_dir / "WoW.exe").write_bytes(b"MZ")
        nested = wow_dir / "IchaLaunch"
        nested.mkdir()
        # Simulate launcher living in Game/IchaLaunch/
        old = Path.cwd()
        try:
            import os

            os.chdir(nested)
            found = discover_game_path_near_launcher()
            assert found is not None
            assert found.resolve() == wow_dir.resolve()
        finally:
            os.chdir(old)
    print("OK discover game path near launcher")


def test_addons_path_defaults():
    from ichalaunch.config.settings import Settings

    s = Settings()
    # Use an in-memory-ish path without wiping user's real settings file:
    # exercise helpers via a temporary Settings instance methods only.
    default = s.default_addons_path_for(r"D:\Games\RavenCraft")
    assert default.replace("/", "\\").endswith(r"Interface\AddOns") or default.endswith(
        "Interface/AddOns"
    ), default
    assert "RavenCraft" in default

    old_game = s.game_path
    old_addons = s.addons_path
    try:
        s.game_path = r"D:\Games\ClientA"
        assert s.addons_path.replace("\\", "/").endswith("Interface/AddOns")
        assert "ClientA" in s.addons_path
        # Custom override should stick when game path changes
        s.addons_path = r"E:\Custom\AddOns"
        s.game_path = r"D:\Games\ClientB"
        assert s.addons_path.replace("\\", "/") == "E:/Custom/AddOns" or s.addons_path == r"E:\Custom\AddOns"
        s.reset_addons_path_to_default()
        assert "ClientB" in s.addons_path
    finally:
        s.game_path = old_game
        s.addons_path = old_addons
    print("OK addons path defaults")


def test_status_progress_bytes():
    from ichalaunch.core.process import StatusProgress, download_bytes_cb

    statuses: list[str] = []
    pcts: list[int] = []
    p = StatusProgress(statuses.append, pcts.append)
    p("Downloading pack…")
    assert pcts[-1] == -1
    cb = download_bytes_cb(p)
    assert cb is not None
    cb(42, 100)
    assert pcts[-1] == 42
    assert "42%" in statuses[-1]
    cb(50, 0)  # unknown total → indeterminate
    assert pcts[-1] == -1
    assert download_bytes_cb(None) is None
    assert download_bytes_cb(lambda m: None) is None
    print("OK status progress bytes")


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


def test_read_git_origin_url():
    import tempfile
    from pathlib import Path

    from ichalaunch.core.detect import read_git_origin_url

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        assert read_git_origin_url(root) is None

        git = root / ".git"
        git.mkdir()
        (git / "config").write_text(
            "[core]\n\trepositoryformatversion = 0\n"
            '[remote "origin"]\n'
            "\turl = https://github.com/USS-Enterprise-Guild/1701-Random-Mount.git\n"
            "\tfetch = +refs/heads/*:refs/remotes/origin/*\n",
            encoding="utf-8",
        )
        assert (
            read_git_origin_url(root)
            == "https://github.com/USS-Enterprise-Guild/1701-Random-Mount"
        )

        (git / "config").write_text(
            '[remote "origin"]\n'
            "\turl = git@github.com:shagu/ShaguTweaks.git\n",
            encoding="utf-8",
        )
        assert read_git_origin_url(root) == "https://github.com/shagu/ShaguTweaks"

        # Existing zip-style folder without .git stays None
        bare = root / "BareAddon"
        bare.mkdir()
        assert read_git_origin_url(bare) is None
    print("OK read_git_origin_url")


def test_sanitize_filename():
    from ichalaunch.core.filesystem import sanitize_filename

    assert sanitize_filename('vanillafixes-1.5.3.zip') == "vanillafixes-1.5.3.zip"
    assert sanitize_filename('"vanillafixes-1.5.3.zip"') == "vanillafixes-1.5.3.zip"
    assert sanitize_filename("vanillafixes-1.5.3.zip\n") == "vanillafixes-1.5.3.zip"
    assert sanitize_filename("vanillafixes-1.5.3.zip\r\n") == "vanillafixes-1.5.3.zip"
    assert "*" not in sanitize_filename("bad*name?.zip")
    assert sanitize_filename("") == "download.bin"
    assert sanitize_filename('attachment; filename="pack.zip"') == "pack.zip"
    print("OK sanitize filename")


def test_robust_rmtree_readonly_git_pack():
    """Addon reinstall must clear Windows read-only bits under leftover .git trees."""
    import os
    import stat

    from ichalaunch.core.filesystem import robust_rmtree, safe_remove

    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "IchaTaunt"
        pack = root / ".git" / "objects" / "pack"
        pack.mkdir(parents=True)
        idx = pack / "pack-5c013da7e4e1ddcca1d841ae2654929d8e3e5f3f.idx"
        idx.write_bytes(b"fake-idx")
        os.chmod(idx, stat.S_IREAD)
        assert not (idx.stat().st_mode & stat.S_IWRITE)
        safe_remove(root)
        assert not root.exists()
    # Error message mentions .git when removal still fails (message helper path)
    from ichalaunch.core.filesystem import _remove_error_message

    msg = _remove_error_message(
        Path(r"C:\Games\RavenCraft\Interface\AddOns\IchaTaunt\.git\objects\pack\x.idx"),
        OSError(5, "Access is denied"),
    )
    assert ".git" in msg.lower()
    assert "manually" in msg.lower()
    print("OK robust rmtree readonly git pack")


def test_vanillafixes_zip_in_memory():
    """Windows Defender may quarantine vanillafixes-*.zip on disk; memory extract must work."""
    import tempfile

    from ichalaunch.mods.installer import _download_source, get_mod
    from ichalaunch.core.filesystem import extract_zip

    mod = get_mod("vanillafixes")
    assert mod and mod["source"]["asset_not_contains"] == "dxvk"
    source = dict(mod["source"])
    with tempfile.TemporaryDirectory(prefix="ichalaunch_") as tmp:
        work = Path(tmp)
        artifact = _download_source(source, work, None)
        assert isinstance(artifact, (bytes, bytearray)), type(artifact)
        assert artifact[:2] == b"PK"
        # Disk write of this zip is often blocked on Windows — prove memory path works
        root = extract_zip(artifact, work / "extract")
        names = {p.name.lower() for p in root.rglob("*") if p.is_file()}
        assert "vanillafixes.exe" in names, names
        assert "vfpatcher.dll" in names, names
        # Confirm on-disk zip would be the failure mode we fixed
        bad = work / "vanillafixes-1.5.3.zip"
        try:
            bad.write_bytes(artifact)
            try:
                with open(bad, "rb") as f:
                    f.read(4)
                disk_ok = True
            except OSError:
                disk_ok = False
        except OSError:
            disk_ok = False
        print(f"OK vanillafixes in-memory extract (disk zip readable={disk_ok})")


def main():
    test_catalogs()
    test_github_parse()
    test_protected()
    test_dlls_txt()
    test_detect_state()
    test_discover_game_path_near_launcher()
    test_addons_path_defaults()
    test_status_progress_bytes()
    test_multi_folder_pack_grouping()
    test_read_git_origin_url()
    test_sanitize_filename()
    test_robust_rmtree_readonly_git_pack()
    test_vanillafixes_zip_in_memory()
    print("\nAll smoke tests passed.")


if __name__ == "__main__":
    main()

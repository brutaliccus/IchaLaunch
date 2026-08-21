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
    assert parse_github_url("https://github.com/shagu/ShaguTweaks") == (
        "shagu",
        "ShaguTweaks",
        None,
    )
    assert parse_github_url("https://github.com/shagu/ShaguTweaks.git") == (
        "shagu",
        "ShaguTweaks",
        None,
    )
    tagged = parse_github_url(
        "https://github.com/The-Kludge-Bureau/Bagshui/releases/tag/1.5.16"
    )
    assert tagged is not None
    assert tagged.owner == "The-Kludge-Bureau"
    assert tagged.repo == "Bagshui"
    assert tagged.tag == "1.5.16"
    dl = parse_github_url(
        "https://github.com/The-Kludge-Bureau/Bagshui/releases/download/1.5.16/Bagshui.zip"
    )
    assert dl is not None and dl.tag == "1.5.16"
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

    from ichalaunch.core.detect import merge_addon_meta, overlay_git_origin, read_git_origin_url

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

        # .git origin must beat catalog / preloaded settings URLs
        origin = read_git_origin_url(root)
        merged = merge_addon_meta(
            "ShaguTweaks",
            prev={"url": "https://github.com/wrong/catalog-preload", "repository": "wrong/catalog-preload"},
            cat={"repo": "wrong/from-catalog", "name": "ShaguTweaks"},
            git_origin=origin,
        )
        assert merged["url"] == "https://github.com/shagu/ShaguTweaks"
        assert merged["repository"] == "shagu/ShaguTweaks"

        # Zip install (no git_origin): catalog / prev still used
        zip_meta = merge_addon_meta(
            "BareAddon",
            prev={},
            cat={"repo": "https://github.com/owner/BareAddon", "name": "BareAddon"},
            git_origin=None,
        )
        assert zip_meta["url"] == "https://github.com/owner/BareAddon"
        assert zip_meta["repository"] == "owner/BareAddon"

        overlaid_ok = overlay_git_origin(
            root.name,
            {"url": "https://github.com/wrong/x", "repository": "wrong/x"},
            addons_dir=root.parent,
        )
        assert overlaid_ok["url"] == "https://github.com/shagu/ShaguTweaks"
        assert overlaid_ok["repository"] == "shagu/ShaguTweaks"

        bare_overlaid = overlay_git_origin(
            "BareAddon",
            {"url": "https://github.com/owner/BareAddon", "repository": "owner/BareAddon"},
            addons_dir=root,
        )
        assert bare_overlaid["url"] == "https://github.com/owner/BareAddon"
        assert bare_overlaid["repository"] == "owner/BareAddon"
    print("OK read_git_origin_url")


def test_write_git_origin():
    """Zip/catalog install must leave a .git origin that the update checker reads."""
    from ichalaunch.core.detect import overlay_git_origin, read_git_origin_url, write_git_origin
    from ichalaunch.core.filesystem import is_protected_path

    with tempfile.TemporaryDirectory() as tmp:
        addon = Path(tmp) / "ShaguTweaks"
        addon.mkdir()
        toc = addon / "ShaguTweaks.toc"
        toc.write_text("## Title: ShaguTweaks\n", encoding="utf-8")

        assert not is_protected_path(addon)
        write_git_origin(addon, "https://github.com/shagu/ShaguTweaks")
        assert read_git_origin_url(addon) == "https://github.com/shagu/ShaguTweaks"
        assert (addon / ".git" / "config").is_file()
        assert toc.is_file()

        # Same origin (with/without .git) must not wipe addon files
        write_git_origin(addon, "https://github.com/shagu/ShaguTweaks.git")
        assert read_git_origin_url(addon) == "https://github.com/shagu/ShaguTweaks"
        assert toc.read_text(encoding="utf-8").startswith("## Title:")

        # Different repo: replace .git only, no prompt, keep addon files
        write_git_origin(addon, "https://github.com/other/ShaguTweaks")
        assert read_git_origin_url(addon) == "https://github.com/other/ShaguTweaks"
        assert toc.is_file()
        cfg = (addon / ".git" / "config").read_text(encoding="utf-8")
        assert "other/ShaguTweaks" in cfg
        assert "shagu/ShaguTweaks" not in cfg

        overlaid = overlay_git_origin(
            "ShaguTweaks",
            {"url": "https://github.com/shagu/ShaguTweaks", "repository": "shagu/ShaguTweaks"},
            addons_dir=addon.parent,
        )
        assert overlaid["url"] == "https://github.com/other/ShaguTweaks"
        assert overlaid["repository"] == "other/ShaguTweaks"
    print("OK write_git_origin")


def test_addon_loadstate():
    from ichalaunch.addons.loadstate import (
        UNLOADED_SIBLING,
        addon_disk_path,
        addon_is_loaded,
        set_addon_loaded,
    )

    with tempfile.TemporaryDirectory() as tmp:
        iface = Path(tmp) / "Interface"
        addons = iface / "AddOns"
        unloaded = iface / UNLOADED_SIBLING
        pack = addons / "FooPack"
        child = addons / "FooPack_Bar"
        pack.mkdir(parents=True)
        child.mkdir()
        (pack / "FooPack.toc").write_text("## Title: Foo\n", encoding="utf-8")
        (child / "FooPack_Bar.toc").write_text("## Title: Bar\n", encoding="utf-8")
        installed = {
            "FooPack": {"folders": ["FooPack", "FooPack_Bar"], "loaded": True},
            "FooPack_Bar": {"managed_by": "FooPack", "loaded": True},
        }
        set_addon_loaded(
            "FooPack",
            False,
            addons_dir=addons,
            unloaded_dir=unloaded,
            installed=installed,
        )
        assert not (addons / "FooPack").exists()
        assert (unloaded / "FooPack" / "FooPack.toc").is_file()
        assert (unloaded / "FooPack_Bar" / "FooPack_Bar.toc").is_file()
        assert installed["FooPack"]["loaded"] is False
        assert addon_disk_path("FooPack", addons_dir=addons, unloaded_dir=unloaded) == (
            unloaded / "FooPack"
        )
        assert not addon_is_loaded("FooPack", addons_dir=addons)

        set_addon_loaded(
            "FooPack",
            True,
            addons_dir=addons,
            unloaded_dir=unloaded,
            installed=installed,
        )
        assert (addons / "FooPack" / "FooPack.toc").is_file()
        assert (addons / "FooPack_Bar").is_dir()
        assert installed["FooPack"]["loaded"] is True
    print("OK addon loadstate")


def test_robust_move_tree_and_lock_message():
    import os

    from ichalaunch.addons.loadstate import (
        GAME_LOCK_MESSAGE,
        GENERIC_LOCK_MESSAGE,
        addon_move_error_text,
    )
    from ichalaunch.core.filesystem import robust_move_tree

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        src = root / "AddOns" / "Foo"
        dest_parent = root / "AddOnsUnloaded"
        dest = dest_parent / "Foo"
        src.mkdir(parents=True)
        (src / "Foo.toc").write_text("## Title: Foo\n", encoding="utf-8")
        leftover = dest
        leftover.mkdir(parents=True)
        (leftover / "stale.txt").write_text("old", encoding="utf-8")
        used = robust_move_tree(src, dest)
        assert used in ("rename", "shutil.move", "copytree")
        assert dest.is_dir()
        assert (dest / "Foo.toc").is_file()
        assert not src.exists()
        assert not (dest / "stale.txt").exists()

        src2 = root / "AddOns" / "Bar"
        dest2 = dest_parent / "Bar"
        src2.mkdir(parents=True)
        (src2 / "Bar.toc").write_text("## Title: Bar\n", encoding="utf-8")
        real_rename = os.rename

        def deny_rename(a, b):
            raise OSError(5, "Access is denied")

        os.rename = deny_rename
        try:
            used = robust_move_tree(src2, dest2)
        finally:
            os.rename = real_rename
        assert used in ("shutil.move", "copytree")
        assert (dest2 / "Bar.toc").is_file()
        assert not src2.exists()

    denied = PermissionError(13, "Access is denied")
    denied.winerror = 5  # type: ignore[attr-defined]
    import ichalaunch.addons.loadstate as ls

    orig_wow = ls.wow_exe_running
    ls.wow_exe_running = lambda: True
    try:
        assert addon_move_error_text(denied) == GAME_LOCK_MESSAGE
    finally:
        ls.wow_exe_running = orig_wow
    ls.wow_exe_running = lambda: False
    try:
        text = addon_move_error_text(denied)
        assert text == GENERIC_LOCK_MESSAGE
        assert "WinError" not in text
        assert "Access is denied" not in text
    finally:
        ls.wow_exe_running = orig_wow
    print("OK robust move tree and lock message")


def test_repair_missing_addon_git():
    """Update-check pass must write missing .git from known repo and emit status."""
    from ichalaunch.addons.github import GIT_REPAIR_STATUS, repair_missing_addon_git_origins
    from ichalaunch.core.detect import read_git_origin_url
    from ichalaunch.core.filesystem import is_protected_path

    assert GIT_REPAIR_STATUS == "Adding missing git folder structure..."

    class Capture:
        def __init__(self) -> None:
            self.msgs: list[str] = []

        def __call__(self, msg: str) -> None:
            self.msgs.append(msg)

        def on_count(self, done: int, total: int, msg: str | None = None) -> None:
            if msg:
                self.msgs.append(msg)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        missing = root / "NeedsGit"
        missing.mkdir()
        (missing / "NeedsGit.toc").write_text("## Title: NeedsGit\n", encoding="utf-8")

        already = root / "HasGit"
        already.mkdir()
        (already / "HasGit.toc").write_text("## Title: HasGit\n", encoding="utf-8")
        (already / ".git").mkdir()
        (already / ".git" / "config").write_text(
            '[remote "origin"]\n\turl = https://github.com/keep/HasGit.git\n',
            encoding="utf-8",
        )

        skipped_never = root / "NeverGit"
        skipped_never.mkdir()
        (skipped_never / "NeverGit.toc").write_text("## Title: NeverGit\n", encoding="utf-8")

        skipped_norepo = root / "NoRepo"
        skipped_norepo.mkdir()
        (skipped_norepo / "NoRepo.toc").write_text("## Title: NoRepo\n", encoding="utf-8")

        installed = {
            "NeedsGit": {
                "url": "https://github.com/owner/NeedsGit",
                "repository": "owner/NeedsGit",
            },
            "HasGit": {
                "url": "https://github.com/other/HasGit",
                "repository": "other/HasGit",
            },
            "NeverGit": {
                "url": "https://github.com/owner/NeverGit",
                "never_update": True,
            },
            "NoRepo": {"source": "detected"},
        }

        progress = Capture()
        n = repair_missing_addon_git_origins(
            progress,
            addons_dir=root,
            installed=installed,
        )
        assert n == 1
        assert progress.msgs == [GIT_REPAIR_STATUS]
        assert read_git_origin_url(missing) == "https://github.com/owner/NeedsGit"
        # Existing .git left alone (not overwritten by settings url)
        assert read_git_origin_url(already) == "https://github.com/keep/HasGit"
        assert not (skipped_never / ".git").exists()
        assert not (skipped_norepo / ".git").exists()

        # Second pass: nothing to repair, do not re-emit status
        progress2 = Capture()
        n2 = repair_missing_addon_git_origins(
            progress2,
            addons_dir=root,
            installed=installed,
        )
        assert n2 == 0
        assert progress2.msgs == []

        # Catalog repo is enough when settings have no url/repository
        catalog_addon = root / "ShaguTweaks"
        catalog_addon.mkdir()
        (catalog_addon / "ShaguTweaks.toc").write_text("## Title: ShaguTweaks\n", encoding="utf-8")
        n_cat = repair_missing_addon_git_origins(
            None,
            addons_dir=root,
            installed={"ShaguTweaks": {"source": "detected"}},
        )
        assert n_cat == 1
        assert read_git_origin_url(catalog_addon) == "https://github.com/shagu/ShaguTweaks"

        # Protected locations (Desktop / Documents / …) must not get a .git write
        prot_root = root / "Desktop"
        prot = prot_root / "ProtAddon"
        prot.mkdir(parents=True)
        (prot / "ProtAddon.toc").write_text("## Title: Prot\n", encoding="utf-8")
        assert is_protected_path(prot)
        n_prot = repair_missing_addon_git_origins(
            None,
            addons_dir=prot_root,
            installed={
                "ProtAddon": {
                    "url": "https://github.com/owner/ProtAddon",
                    "repository": "owner/ProtAddon",
                },
            },
        )
        assert n_prot == 0
        assert not (prot / ".git").exists()
    print("OK repair_missing_addon_git")


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
    test_write_git_origin()
    test_addon_loadstate()
    test_robust_move_tree_and_lock_message()
    test_repair_missing_addon_git()
    test_sanitize_filename()
    test_robust_rmtree_readonly_git_pack()
    test_vanillafixes_zip_in_memory()
    print("\nAll smoke tests passed.")


if __name__ == "__main__":
    main()

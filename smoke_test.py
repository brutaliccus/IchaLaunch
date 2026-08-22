"""Headless smoke tests for IchaLaunch core (no GUI)."""

from __future__ import annotations

import json
import sys
import tempfile
import time
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
    from ichalaunch.core.filesystem import (
        clear_fs_caches,
        is_lock_or_av_error,
        name_present,
        parse_dlls_txt_text,
        sha256_file,
    )

    clear_fs_caches()
    with tempfile.TemporaryDirectory() as td:
        game = Path(td)
        write_dlls_txt(game, ["a.dll"])
        update_dlls_txt(game, add=["b.dll"], remove=["a.dll"])
        assert read_dlls_txt(game) == ["b.dll"]

        # Comments, blanks, inline comments, quotes — never crash
        (game / "dlls.txt").write_text(
            "# Managed\n\n  \n# vanillahelpers.dll\n"
            "vanillahelpers.dll\n\"Nampower.dll\"  # keep\n",
            encoding="utf-8",
        )
        names = read_dlls_txt(game)
        assert "vanillahelpers.dll" in names
        assert "Nampower.dll" in names
        assert parse_dlls_txt_text("# only comment\n\n") == []

        # Commenting out removes from the active list (preserves the line)
        (game / "dlls.txt").write_text(
            "# vanillahelpers.dll\nNampower.dll\n", encoding="utf-8"
        )
        assert read_dlls_txt(game) == ["Nampower.dll"]
        update_dlls_txt(game, add=["SuperWoWhook.dll"])
        text = (game / "dlls.txt").read_text(encoding="utf-8")
        assert "# vanillahelpers.dll" in text
        assert "SuperWoWhook.dll" in read_dlls_txt(game)

        # .ichalaunch/dlls.txt is also parsed
        meta = game / ".ichalaunch"
        meta.mkdir()
        (game / "dlls.txt").unlink()
        (meta / "dlls.txt").write_text("# x\nVanillaHelpers.dll\n", encoding="utf-8")
        assert read_dlls_txt(game) == ["VanillaHelpers.dll"]

        # Case-insensitive presence via listdir (does not LoadLibrary)
        (game / "VanillaHelpers.dll").write_bytes(b"MZ")
        clear_fs_caches()
        assert name_present(game, "vanillahelpers.dll")
        assert name_present(game, "VanillaHelpers.dll")
        assert not name_present(game, "missing.dll")

        locked = OSError(22, "virus")
        locked.winerror = 225  # type: ignore[attr-defined]
        assert is_lock_or_av_error(locked)
        share = OSError(13, "share")
        share.winerror = 32  # type: ignore[attr-defined]
        assert is_lock_or_av_error(share)

        digest = sha256_file(game / "VanillaHelpers.dll")
        assert digest is not None and len(digest) == 64
        assert sha256_file(game / "nope.dll") is None
    print("OK dlls.txt")


def test_detect_state():
    from ichalaunch.core.filesystem import clear_fs_caches

    with tempfile.TemporaryDirectory() as td:
        game = Path(td)
        (game / "nampower.dll").write_bytes(b"x")
        (game / "WDB").write_text("")
        (game / "vanillahelpers.dll").write_bytes(b"MZ")
        clear_fs_caches()
        state = detect_actual_state(game)
        assert state["nampower"] is True
        assert state["wdb_block"] is True
        assert state["superwow"] is False
        assert state["vanilla_helpers"] is True
    print("OK detect state")


def test_apply_desired_state_guard():
    from ichalaunch.mods import installer as inst

    inst._APPLY_IN_PROGRESS = True
    try:
        out = inst.apply_desired_state()
        assert out and "already running" in out[0]
    finally:
        inst._APPLY_IN_PROGRESS = False
    print("OK apply desired state guard")


def test_mod_remove_desired_state():
    """Uncheck + Apply removes the patch file; rescan never re-checks the box.

    Regression test for the Darker Nights loop: desired off → apply → actual off
    immediately (no stale listing-cache nag), rescan keeps the checkbox off, and
    files shared with another enabled mod are kept.
    """
    from ichalaunch.config.settings import settings as s
    from ichalaunch.core.detect import sync_desired_mods_from_disk
    from ichalaunch.core.filesystem import clear_fs_caches
    from ichalaunch.mods.installer import (
        apply_desired_state,
        plan_changes,
        remove_mod,
    )

    keys = (
        "desired_mods",
        "user_set_mods",
        "installed_mods",
        "user_mods",
        "game_path",
        "addons_path",
    )
    saved = {k: s.get(k) for k in keys}
    try:
        with tempfile.TemporaryDirectory() as td:
            game = Path(td)
            (game / "WoW.exe").write_bytes(b"MZ")
            data = game / "Data"
            data.mkdir()
            mpq = data / "patch-N.mpq"
            mpq.write_bytes(b"MPQ")
            s.set("game_path", str(game))
            s.set("addons_path", "")
            s.set("desired_mods", {})
            s.set("user_set_mods", [])
            s.set("installed_mods", {})
            s.set("user_mods", [])
            clear_fs_caches()

            # First run / no desired set: detected state seeds the checkbox on.
            desired = sync_desired_mods_from_disk()
            assert desired.get("darker_nights") is True

            # User unchecks Darker Nights — an explicit choice.
            s.set_desired_mod("darker_nights", False)
            assert "darker_nights" in s.user_set_mods
            plan = plan_changes()
            assert any(
                c["action"] == "remove" and c["id"] == "darker_nights" for c in plan
            ), plan

            out = apply_desired_state()
            assert "- darker_nights" in out, out
            assert not mpq.exists()

            # Immediately after apply (inside the 4s listing-cache TTL) the plan
            # must be clean — this is what drives the "unapplied changes" nag.
            assert plan_changes() == [], plan_changes()

            # Rescan syncs actual but must not flip the user's choice back on.
            desired = sync_desired_mods_from_disk()
            assert desired.get("darker_nights") is False
            assert detect_actual_state(game).get("darker_nights") is False

            # Even if the file reappears (manual copy), desired stays off.
            mpq.write_bytes(b"MPQ")
            clear_fs_caches()
            desired = sync_desired_mods_from_disk()
            assert desired.get("darker_nights") is False

            # Shared ownership: the same MPQ owned by another enabled mod is kept.
            shared_mpq = data / "patch-Z.mpq"
            shared_mpq.write_bytes(b"MPQ")
            base = {
                "kind": "mpq_file",
                "destination": "Data/patch-Z.mpq",
                "detect": {"data_mpq": ["patch-Z.mpq"]},
            }
            s.set(
                "user_mods",
                [
                    {"id": "test_shared_a", "name": "Shared A", **base},
                    {"id": "test_shared_b", "name": "Shared B", **base},
                ],
            )
            s.set("desired_mods", {"test_shared_a": False, "test_shared_b": True})
            remove_mod("test_shared_a")
            assert shared_mpq.exists(), "shared MPQ must be kept for enabled mod"
            s.set("desired_mods", {"test_shared_a": False, "test_shared_b": False})
            remove_mod("test_shared_b")
            assert not shared_mpq.exists()
    finally:
        for k in keys:
            s.set(k, saved[k])
        clear_fs_caches()
    print("OK mod removal desired-state loop")


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
    from ichalaunch.core.process import (
        StatusProgress,
        download_bytes_cb,
        resolve_download_total,
        status_only,
    )

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
    p.set_status("still downloading")
    assert statuses[-1] == "still downloading"
    assert pcts[-1] == -1  # set_status must not change percent
    status_only(p, "still downloading (status_only)")
    assert statuses[-1] == "still downloading (status_only)"
    assert pcts[-1] == -1
    p.on_count(37, 100, "Downloading in browser… 37%")
    assert pcts[-1] == 37
    assert "37%" in statuses[-1]
    status_only(p, "Extracting…")
    assert pcts[-1] == 37  # status_only keeps determinate %
    assert statuses[-1] == "Extracting…"
    p.on_count(50, 100, "Downloading in browser… 50%")
    assert pcts[-1] == 50
    assert -1 not in pcts[-2:]  # on_count stays determinate
    assert download_bytes_cb(None) is None
    assert download_bytes_cb(lambda m: None) is None
    assert resolve_download_total({"Content-Length": "4096"}) == 4096
    assert resolve_download_total({}, known_total=1024) == 1024
    assert resolve_download_total({"Content-Length": "0"}, known_total=2048) == 2048
    assert resolve_download_total({}) == 0
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


def test_copied_addon_update_compare():
    """Copied addons without install SHA must not count as outdated vs GitHub tip."""
    from ichalaunch.addons.github import should_report_addon_update
    from ichalaunch.core.detect import read_addon_toc_version, read_local_git_head_sha

    # Empty local commit vs remote SHA used to mark every copied addon out of date.
    assert should_report_addon_update(local_commit="", remote_commit="abc1234def") is False
    assert should_report_addon_update(local_commit="", remote_commit="", local_version="", remote_version="1.2.3") is False
    assert should_report_addon_update(local_version="1.2.3", remote_version="1.2.3") is False
    assert should_report_addon_update(local_version="1.2.4", remote_version="1.2.3") is False
    assert should_report_addon_update(local_version="1.2.3", remote_version="v1.2.3") is False
    assert should_report_addon_update(local_version="1.2.3", remote_version="1.3.0") is True
    assert should_report_addon_update(local_commit="abc1234", remote_commit="abc1234") is False
    assert should_report_addon_update(local_commit="abc1234ffff", remote_commit="abc1234") is False
    assert should_report_addon_update(local_commit="abc1234", remote_commit="def5678") is True

    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / "ShaguTweaks"
        folder.mkdir()
        (folder / "ShaguTweaks.toc").write_text(
            "## Interface: 11200\n## Title: ShaguTweaks\n## Version: 1.5.16\n",
            encoding="utf-8",
        )
        assert read_addon_toc_version(folder) == "1.5.16"
        assert read_local_git_head_sha(folder) is None

        # Stub .git from origin repair has HEAD ref but no commit object.
        git_dir = folder / ".git"
        git_dir.mkdir()
        (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        (git_dir / "config").write_text(
            '[remote "origin"]\n\turl = https://github.com/shagu/ShaguTweaks.git\n',
            encoding="utf-8",
        )
        assert read_local_git_head_sha(folder) is None

        ref_dir = git_dir / "refs" / "heads"
        ref_dir.mkdir(parents=True)
        (ref_dir / "main").write_text("abcdef1234567890abcdef1234567890abcdef12\n", encoding="utf-8")
        assert read_local_git_head_sha(folder) == "abcdef1234567890abcdef1234567890abcdef12"

    print("OK copied addon update compare")


def test_unauth_scan_budget_queue():
    """Unauthenticated scan budget is 60 API calls/hour; status/queue math is stable."""
    from ichalaunch.addons import github as gh

    remaining, reset_in, start, used = gh.compute_unauth_budget(
        window_start=1_000.0,
        window_used=0,
        now=1_000.0,
        budget=60,
        window_sec=3600,
    )
    assert remaining == 60 and reset_in == 3600 and used == 0

    remaining, reset_in, start, used = gh.compute_unauth_budget(
        window_start=1_000.0,
        window_used=60,
        now=1_000.0 + 600,
        budget=60,
        window_sec=3600,
    )
    assert remaining == 0 and used == 60
    assert 2900 <= reset_in <= 3000

    # Hour elapsed → full budget again
    remaining, reset_in, start, used = gh.compute_unauth_budget(
        window_start=1_000.0,
        window_used=60,
        now=1_000.0 + 3600,
        budget=60,
        window_sec=3600,
    )
    assert remaining == 60 and reset_in == 0 and used == 0

    status = gh.format_queued_scan_status(60, 240, 47 * 60)
    assert status == "Scanning addons… 60/240 (queued; resumes in ~47 min)"
    assert "resuming" in gh.format_queued_scan_status(10, 100, 0)

    # Consume gate: without token, 61st call raises budget error
    prev_token = gh.settings.get("github_token")
    prev_queue = gh.settings.get("addon_update_scan_queue")
    try:
        gh.settings.set("github_token", "")
        now = time.time()
        gh._budget_window_start = now
        gh._budget_window_used = 59
        gh._consume_api_budget()
        assert gh._budget_window_used == 60
        raised = False
        try:
            gh._consume_api_budget()
        except gh.GitHubBudgetExhaustedError:
            raised = True
        assert raised

        # With token: no artificial gate
        gh.settings.set("github_token", "ghp_test_token")
        gh._budget_window_used = 60
        gh._consume_api_budget()  # must not raise
    finally:
        gh.settings.set("github_token", prev_token or "")
        gh.settings.set("addon_update_scan_queue", prev_queue)
        gh._budget_window_start = None
        gh._budget_window_used = 0

    print("OK unauth scan budget queue")


def test_auto_scan_cooldown_setting():
    from ichalaunch.config.settings import (
        AUTO_SCAN_COOLDOWN_MINUTES_DEFAULT,
        clamp_auto_scan_cooldown_minutes,
        format_auto_scan_cooldown_label,
        settings,
    )

    assert clamp_auto_scan_cooldown_minutes(60) == 60
    assert clamp_auto_scan_cooldown_minutes(1) == 15
    assert clamp_auto_scan_cooldown_minutes(10_000) == 24 * 60
    assert clamp_auto_scan_cooldown_minutes(22) == 15 or clamp_auto_scan_cooldown_minutes(22) == 30
    assert format_auto_scan_cooldown_label(60) == "1 hour"
    assert format_auto_scan_cooldown_label(120) == "2 hours"
    assert format_auto_scan_cooldown_label(15) == "15 min"
    assert format_auto_scan_cooldown_label(90) == "1.5 hours"

    prev = settings.get("auto_scan_cooldown_minutes")
    try:
        settings.set_auto_scan_cooldown_minutes(180)
        assert settings.auto_scan_cooldown_minutes() == 180
        assert settings.auto_scan_cooldown_sec() == 180 * 60
        settings.set_auto_scan_cooldown_minutes(AUTO_SCAN_COOLDOWN_MINUTES_DEFAULT)
        assert settings.auto_scan_cooldown_minutes() == 60
    finally:
        if prev is None:
            settings.set("auto_scan_cooldown_minutes", AUTO_SCAN_COOLDOWN_MINUTES_DEFAULT)
        else:
            settings.set("auto_scan_cooldown_minutes", prev)

    print("OK auto scan cooldown setting")


def test_bagshui_catalog_pin():
    """Bagshui is pinned to the 1.12 tag and never auto-updates to 3.3.5."""
    from ichalaunch.addons.github import (
        addon_ignores_updates,
        addon_skips_updates,
        catalog_locks_updates,
        catalog_pin_tag,
        parse_github_url,
    )

    bag = next(
        (e for e in load_catalog() if (e.get("folder") or e.get("name")) == "Bagshui"),
        None,
    )
    assert bag is not None, "Bagshui missing from addons.json"
    assert bag.get("pin_release") == "1.5.16"
    assert bag.get("updates") is False
    parsed = parse_github_url(str(bag.get("repo") or ""))
    assert parsed is not None
    assert parsed.owner == "The-Kludge-Bureau"
    assert parsed.repo == "Bagshui"
    assert parsed.tag == "1.5.16"
    assert catalog_pin_tag(bag) == "1.5.16"
    assert catalog_locks_updates(bag) is True
    # Already-installed copy with no tag / never_update still locked via catalog
    assert addon_ignores_updates(bag, "Bagshui", {}) is True
    assert addon_skips_updates("Bagshui", {}) is True
    assert addon_skips_updates(
        "Bagshui",
        {"url": "https://github.com/The-Kludge-Bureau/Bagshui", "repository": "The-Kludge-Bureau/Bagshui"},
    ) is True
    # Generic catalog helpers: unpinned addons still update
    shagu = next(
        (e for e in load_catalog() if (e.get("folder") or "") == "ShaguTweaks"),
        None,
    )
    assert shagu is not None
    assert catalog_pin_tag(shagu) == ""
    assert catalog_locks_updates(shagu) is False
    assert addon_skips_updates("ShaguTweaks", {}) is False
    assert catalog_locks_updates({"repo": "https://github.com/owner/repo", "updates": False}) is True
    assert catalog_locks_updates({"repo": "https://github.com/owner/repo", "ignore_updates": True}) is True
    assert catalog_pin_tag({"repo": "https://github.com/owner/repo/releases/tag/v2.0.0"}) == "v2.0.0"
    print("OK Bagshui catalog pin 1.5.16")


def test_never_update_persists():
    """never_update must survive merge/sync and settings.json save/load."""
    import ichalaunch.config.settings as settings_mod
    from ichalaunch.addons.github import (
        addon_ignores_updates,
        addon_skips_updates,
        catalog_locks_updates,
        repair_missing_addon_git_origins,
    )
    from ichalaunch.config.settings import Settings
    from ichalaunch.core.detect import merge_addon_meta, resolve_catalog_entry

    cat, kind = resolve_catalog_entry("Bagshui", include_mods=False)
    assert kind == "exact" and cat is not None
    assert catalog_locks_updates(cat) is True

    # First disk scan (empty settings) still stamps the catalog pin.
    scanned = merge_addon_meta("Bagshui", {}, cat, match_kind="exact")
    assert scanned.get("never_update") is True

    # User lock on an unpinned addon must not be dropped by the meta whitelist.
    kept = merge_addon_meta(
        "ShaguTweaks",
        {"never_update": True, "source": "github", "loaded": True},
        None,
        match_kind="exact",
    )
    assert kept.get("never_update") is True
    assert kept.get("loaded") is True

    with tempfile.TemporaryDirectory() as td:
        fake = Path(td) / "settings.json"
        orig_path = settings_mod.settings_path
        settings_mod.settings_path = lambda: fake
        try:
            s = Settings()
            s.set_installed_addon("Bagshui", {"source": "detected", "name": "Bagshui"})
            assert s.installed_addons["Bagshui"].get("never_update") is True
            # Incoming write that omits the flag must not wipe it.
            s.set_installed_addon("Bagshui", {"loaded": True})
            assert s.installed_addons["Bagshui"].get("never_update") is True
            s.set_installed_addon(
                "CustomLock",
                {"source": "detected", "never_update": True, "name": "CustomLock"},
            )
            assert fake.is_file()
            raw = json.loads(fake.read_text(encoding="utf-8"))
            assert raw["installed_addons"]["Bagshui"]["never_update"] is True
            assert raw["installed_addons"]["CustomLock"]["never_update"] is True

            reloaded = Settings()
            bag_meta = reloaded.installed_addons["Bagshui"]
            assert bag_meta.get("never_update") is True
            assert addon_ignores_updates(cat, "Bagshui", bag_meta) is True
            assert addon_skips_updates("Bagshui", bag_meta) is True
            assert reloaded.is_addon_never_update("Bagshui") is True
            assert reloaded.installed_addons["CustomLock"].get("never_update") is True
        finally:
            settings_mod.settings_path = orig_path

        # Catalog pin skips .git repair even when settings lost never_update.
        bag_dir = Path(td) / "Bagshui"
        bag_dir.mkdir()
        (bag_dir / "Bagshui.toc").write_text("## Title: Bagshui\n", encoding="utf-8")
        n_bag = repair_missing_addon_git_origins(
            None,
            addons_dir=Path(td),
            installed={"Bagshui": {"source": "detected"}},
        )
        assert n_bag == 0
        assert not (bag_dir / ".git").exists()

    print("OK never_update persists across save/load")


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


def test_client_zip_mirrors_and_gofile_parse():
    from ichalaunch.game.launcher import (
        CLIENT_ZIP_MIRRORS,
        GAME_DOWNLOAD_URL,
        GOFILE_EXPECTED_SIZE,
        GOFILE_FILE_ID,
        GOFILE_FILE_NAME,
        GOFILE_MD5,
        GOFILE_STORE,
        VIKINGFILE_ZIP_URL,
        gofile_content_id,
        gofile_file_link_from_payload,
    )

    assert "gofile.io/d/zrTbjjv1" in GAME_DOWNLOAD_URL
    assert GOFILE_FILE_ID == "179cd45c-2ab4-4301-9f98-dcedbff07d07"
    assert GOFILE_FILE_NAME == "twmoa_1181.zip"
    assert GOFILE_STORE == "store-na-phx-4"
    assert GOFILE_EXPECTED_SIZE == 9_829_040_584
    assert GOFILE_MD5 == "b65fb26b56d09e3d45cb72b130a79080"
    assert CLIENT_ZIP_MIRRORS[0] == GAME_DOWNLOAD_URL
    assert VIKINGFILE_ZIP_URL in CLIENT_ZIP_MIRRORS
    assert gofile_content_id(GAME_DOWNLOAD_URL) == "zrTbjjv1"
    assert gofile_content_id("https://gofile.io/d/zrTbjjv1?foo=1") == "zrTbjjv1"
    assert gofile_content_id("https://vikingfile.com/d/x") is None

    payload = {
        "type": "folder",
        "children": {
            "aaa": {
                "type": "file",
                "name": "readme.txt",
                "size": 12,
                "link": "https://store-1.gofile.io/download/web/aaa/readme.txt",
            },
            "bbb": {
                "type": "file",
                "name": "twmoa_1181.zip",
                "size": 100,
                "link": "https://store-9.gofile.io/download/web/bbb/twmoa_1181.zip",
                "directLink": "https://store-9.gofile.io/download/direct/bbb/twmoa_1181.zip",
            },
        },
    }
    url, name = gofile_file_link_from_payload(payload)
    assert name == "twmoa_1181.zip"
    assert url.endswith("twmoa_1181.zip")
    assert "direct" in url
    print("OK client zip mirrors / gofile parse")


def test_find_wow_exe_dir_and_extract():
    import zipfile

    from ichalaunch.core.filesystem import extract_zip
    from ichalaunch.game.client_install import wow_exe_here
    from ichalaunch.game.launcher import commit_game_home, find_wow_exe_dir

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        assert find_wow_exe_dir(root) is None
        (root / "WoW.exe").write_bytes(b"MZ")
        assert find_wow_exe_dir(root).resolve() == root.resolve()
        assert wow_exe_here(root).resolve() == root.resolve()

    with tempfile.TemporaryDirectory() as td:
        picked = Path(td)
        home = picked / "RavenCraft"
        home.mkdir()
        (home / "WoW.exe").write_bytes(b"MZ")
        assert wow_exe_here(picked).resolve() == home.resolve()

    with tempfile.TemporaryDirectory() as td:
        picked = Path(td)
        nested = picked / "other" / "deep"
        nested.mkdir(parents=True)
        (nested / "WoW.exe").write_bytes(b"MZ")
        assert wow_exe_here(picked) is None
        assert find_wow_exe_dir(picked).resolve() == nested.resolve()

    with tempfile.TemporaryDirectory() as td:
        inner = Path(td) / "twmoa_1181"
        inner.mkdir()
        (inner / "WoW.exe").write_bytes(b"MZ")
        found = find_wow_exe_dir(Path(td))
        assert found is not None
        assert found.resolve() == inner.resolve()

    with tempfile.TemporaryDirectory() as td:
        dest = Path(td) / "home"
        dest.mkdir()
        zpath = Path(td) / "client.zip"
        with zipfile.ZipFile(zpath, "w") as zf:
            zf.writestr("twmoa_1181/WoW.exe", b"MZ")
            zf.writestr("twmoa_1181/Data/dummy.mpq", b"x")
        extracted = extract_zip(zpath, dest)
        wow_dir = find_wow_exe_dir(extracted) or find_wow_exe_dir(dest)
        assert wow_dir is not None
        assert (wow_dir / "WoW.exe").is_file()
        assert wow_dir.name == "twmoa_1181"

    from ichalaunch.core.process import StatusProgress

    with tempfile.TemporaryDirectory() as td:
        dest = Path(td) / "home"
        dest.mkdir()
        zpath = Path(td) / "client.zip"
        with zipfile.ZipFile(zpath, "w") as zf:
            zf.writestr("a.bin", b"A" * 50)
            zf.writestr("b.bin", b"B" * 50)
        statuses: list[str] = []
        pcts: list[int] = []
        prog = StatusProgress(statuses.append, pcts.append)
        extract_zip(zpath, dest, progress=prog)
        assert pcts
        assert pcts[0] == 0
        assert pcts[-1] == 100
        assert all(p >= 0 for p in pcts), pcts
        assert any("Extracting" in s for s in statuses)

    from ichalaunch.config.settings import settings as s

    old_game = s.game_path
    old_addons = s.addons_path
    try:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "GameRoot"
            home.mkdir()
            (home / "WoW.exe").write_bytes(b"MZ")
            committed = commit_game_home(home)
            assert Path(s.game_path).resolve() == committed.resolve()
            addons = Path(s.resolved_addons_path())
            assert addons.is_dir()
            assert addons == committed / "Interface" / "AddOns"
    finally:
        s.game_path = old_game
        s.addons_path = old_addons
    print("OK find WoW.exe / extract / commit game home")


def test_browser_zip_watch_and_install_from_zip():
    import zipfile

    from ichalaunch.config.settings import settings as s
    from ichalaunch.game.client_install import (
        GOFILE_FILE_NAME,
        find_complete_client_zip,
        install_client,
        wait_for_browser_zip,
        zip_looks_complete,
    )

    payload = b"PK" + (b"\x00" * 80)
    with tempfile.TemporaryDirectory() as td:
        folder = Path(td)
        partial = folder / f"{GOFILE_FILE_NAME}.crdownload"
        partial.write_bytes(payload)
        assert find_complete_client_zip(dirs=[folder], expected_size=len(payload)) is None
        assert not zip_looks_complete(partial, expected_size=len(payload))

        zpath = folder / GOFILE_FILE_NAME
        zpath.write_bytes(payload)
        found = find_complete_client_zip(dirs=[folder], expected_size=len(payload))
        assert found is not None
        assert found.resolve() == zpath.resolve()
        assert zip_looks_complete(zpath, expected_size=len(payload))

        empty = folder / "empty"
        empty.mkdir()
        assert (
            wait_for_browser_zip(
                dirs=[empty],
                timeout_sec=1,
                poll_sec=0.1,
                expected_size=len(payload),
            )
            is None
        )
        waited = wait_for_browser_zip(
            dirs=[folder],
            timeout_sec=2,
            poll_sec=0.1,
            expected_size=len(payload),
        )
        assert waited is not None
        assert waited.resolve() == zpath.resolve()

    from ichalaunch.core.process import StatusProgress
    from ichalaunch.game.client_install import (
        _is_partial_name,
        _partial_downloads,
        _report_partial_progress,
        client_watch_dirs,
    )

    assert _is_partial_name("Unconfirmed 12345.crdownload", GOFILE_FILE_NAME)
    assert _is_partial_name(f"{GOFILE_FILE_NAME}.partial", GOFILE_FILE_NAME)
    assert _is_partial_name(f"{GOFILE_FILE_NAME}.crdownload", GOFILE_FILE_NAME)
    assert not _is_partial_name(GOFILE_FILE_NAME, GOFILE_FILE_NAME)

    watch = client_watch_dirs()
    assert watch
    joined = " ".join(str(p).lower() for p in watch)
    assert "download" in joined or "desktop" in joined

    with tempfile.TemporaryDirectory() as td:
        folder = Path(td)
        expected = 1_000_000
        unconf = folder / "Unconfirmed 809132.crdownload"
        unconf.write_bytes(b"a" * 370_000)
        found = _partial_downloads(folder, GOFILE_FILE_NAME, expected)
        assert any(p.name.startswith("Unconfirmed") for p in found)

        edge = folder / f"{GOFILE_FILE_NAME}.partial"
        edge.write_bytes(b"b" * 50_000)
        found = _partial_downloads(folder, GOFILE_FILE_NAME, expected)
        assert any(p.name.endswith(".partial") for p in found)

        statuses: list[str] = []
        pcts: list[int] = []
        prog = StatusProgress(statuses.append, pcts.append)
        _report_partial_progress(prog, unconf, expected)
        assert pcts[-1] == 37
        assert -1 not in pcts
        assert "Downloading in browser" in statuses[-1]
        assert "37%" in statuses[-1]

        wait_status: list[str] = []
        wait_pcts: list[int] = []
        waiter = StatusProgress(wait_status.append, wait_pcts.append)
        assert (
            wait_for_browser_zip(
                waiter,
                dirs=[folder],
                timeout_sec=1,
                poll_sec=0.2,
                expected_size=expected,
            )
            is None
        )
        assert wait_pcts[0] == -1  # initial "Waiting for download…"
        determinate = [x for x in wait_pcts[1:] if x >= 0]
        assert determinate, wait_pcts
        assert all(x >= 0 for x in wait_pcts[1:]), wait_pcts
        assert any("Downloading in browser" in s for s in wait_status)

    old_game = s.game_path
    old_addons = s.addons_path
    try:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            dest = root / "Game"
            dest.mkdir()
            zpath = root / GOFILE_FILE_NAME
            with zipfile.ZipFile(zpath, "w") as zf:
                zf.writestr("twmoa_1181/WoW.exe", b"MZ")
            assert zpath.stat().st_size >= 64
            game = install_client(dest, zip_path=zpath, cleanup_watch_dirs=[])
            assert game is not None
            game_p = Path(game)
            assert game_p.name == "RavenCraft"
            assert game_p.parent.resolve() == dest.resolve()
            assert (game_p / "WoW.exe").is_file()
            assert not (dest / "twmoa_1181").exists()
            assert not zpath.exists()
            assert Path(s.game_path).resolve() == game_p.resolve()
            assert Path(s.resolved_addons_path()) == game_p / "Interface" / "AddOns"

            dest_rc = root / "RavenCraft"
            dest_rc.mkdir()
            z2 = root / "nested.zip"
            with zipfile.ZipFile(z2, "w") as zf:
                zf.writestr(
                    "179cd45c-aaaa-4bbb-8ccc-ddddeeeeffff/"
                    "abcd1234-aaaa-4bbb-8ccc-ddddeeeeffff/WoW.exe",
                    b"MZ",
                )
                zf.writestr(
                    "179cd45c-aaaa-4bbb-8ccc-ddddeeeeffff/"
                    "abcd1234-aaaa-4bbb-8ccc-ddddeeeeffff/Data/dummy.mpq",
                    b"x",
                )
            game2 = install_client(dest_rc, zip_path=z2, cleanup_watch_dirs=[])
            assert game2 is not None
            game2_p = Path(game2)
            assert game2_p.resolve() == dest_rc.resolve()
            assert (dest_rc / "WoW.exe").is_file()
            assert (dest_rc / "Data" / "dummy.mpq").is_file()
            assert not (dest_rc / "179cd45c-aaaa-4bbb-8ccc-ddddeeeeffff").exists()
            assert not z2.exists()
    finally:
        s.game_path = old_game
        s.addons_path = old_addons
    print("OK browser zip watch / install from zip")


def test_cleanup_client_zip():
    from ichalaunch.game.client_install import (
        GOFILE_FILE_NAME,
        cleanup_client_zip,
    )

    with tempfile.TemporaryDirectory() as td:
        dest = Path(td) / "Game"
        home = dest / "RavenCraft"
        staging = dest / ".ichalaunch"
        watch = Path(td) / "Downloads"
        home.mkdir(parents=True)
        staging.mkdir(parents=True)
        watch.mkdir()
        (home / "WoW.exe").write_bytes(b"MZ")
        extracted = watch / GOFILE_FILE_NAME
        extracted.write_bytes(b"PK" + b"\x00" * 80)
        leftover = watch / f"{GOFILE_FILE_NAME}.crdownload"
        leftover.write_bytes(b"x")
        staged = staging / GOFILE_FILE_NAME
        staged.write_bytes(b"PK" + b"\x00" * 80)
        other = watch / "other-mod.zip"
        other.write_bytes(b"PK" + b"\x00" * 80)
        cleanup_client_zip(dest, extracted, watch_dirs=[watch])
        assert not extracted.exists()
        assert not leftover.exists()
        assert not staged.exists()
        assert other.exists()
        assert home.is_dir()
        assert (home / "WoW.exe").is_file()
    print("OK cleanup client zip leftovers")


def test_zip_url_from_html():
    from ichalaunch.core.process import zip_url_from_html

    html = """
    <html><a href="https://zo.vikingfile.com/download/abc/twmoa_1181.zip?md5=x">dl</a></html>
    """
    url = zip_url_from_html(html, "https://vikingfile.com/d/tnQwCPOJDA/twmoa_1181.zip")
    assert url is not None
    assert url.endswith(".zip") or ".zip?" in url
    assert zip_url_from_html("<html>no file</html>", "https://example.com/") is None
    print("OK zip url from html")


def main():
    test_catalogs()
    test_github_parse()
    test_protected()
    test_dlls_txt()
    test_detect_state()
    test_apply_desired_state_guard()
    test_mod_remove_desired_state()
    test_discover_game_path_near_launcher()
    test_addons_path_defaults()
    test_status_progress_bytes()
    test_multi_folder_pack_grouping()
    test_read_git_origin_url()
    test_write_git_origin()
    test_addon_loadstate()
    test_robust_move_tree_and_lock_message()
    test_repair_missing_addon_git()
    test_copied_addon_update_compare()
    test_unauth_scan_budget_queue()
    test_auto_scan_cooldown_setting()
    test_bagshui_catalog_pin()
    test_never_update_persists()
    test_sanitize_filename()
    test_robust_rmtree_readonly_git_pack()
    test_vanillafixes_zip_in_memory()
    test_client_zip_mirrors_and_gofile_parse()
    test_find_wow_exe_dir_and_extract()
    test_browser_zip_watch_and_install_from_zip()
    test_cleanup_client_zip()
    test_zip_url_from_html()
    print("\nAll smoke tests passed.")


if __name__ == "__main__":
    main()

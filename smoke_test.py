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
        mirror_dlls_txt_updates,
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

        # remove-only on a missing file must not create an empty dlls.txt
        (game / "dlls.txt").unlink(missing_ok=True)
        update_dlls_txt(game, remove=["ghost.dll"])
        assert not (game / "dlls.txt").exists()

        # remove must not wipe the list when the file cannot be read
        (game / "dlls.txt").write_text("keepme.dll\n", encoding="utf-8")
        original_read = Path.read_text

        def _fail_dlls_read(self, *args, **kwargs):
            if self.name.lower() == "dlls.txt":
                raise OSError(13, "locked")
            return original_read(self, *args, **kwargs)

        Path.read_text = _fail_dlls_read  # type: ignore[method-assign]
        try:
            update_dlls_txt(game, remove=["gone.dll"])
        finally:
            Path.read_text = original_read  # type: ignore[method-assign]
        assert (game / "dlls.txt").read_text(encoding="utf-8") == "keepme.dll\n"

        # Mirror updates into .ichalaunch/dlls.txt when that copy exists
        meta = game / ".ichalaunch"
        meta.mkdir(exist_ok=True)
        (meta / "dlls.txt").write_text("old.dll\n", encoding="utf-8")
        mirror_dlls_txt_updates(game, add=["new.dll"], remove=["old.dll"])
        assert "new.dll" in (meta / "dlls.txt").read_text(encoding="utf-8")
        assert "old.dll" not in read_dlls_txt(game)

        from ichalaunch.core.filesystem import validate_pe_binary

        good = game / "good.dll"
        good.write_bytes(b"MZ" + b"\0" * 2048)
        validate_pe_binary(good, min_size=1024)
        bad = game / "bad.dll"
        bad.write_bytes(b"xx")
        try:
            validate_pe_binary(bad)
            raise AssertionError("expected validate_pe_binary to fail")
        except OSError:
            pass
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
        assert state["vanilla_tweaks"] is False

        glue = game / "Data" / "Interface" / "GlueXML"
        glue.mkdir(parents=True)
        (glue / "AutoLogin.lua").write_text("-- autologin")
        clear_fs_caches()
        state = detect_actual_state(game)
        assert state["auto_login"] is True, state
    print("OK detect state")


def test_vanilla_tweaks_disable_clears_pending():
    """Stock WoW-OriginalBackup.exe must not keep Apply glowing after disable.

    RavenCraft/Turtle ships a backup identical to WoW.exe. Detecting "installed"
    from backup *presence* made uncheck+Apply forever pending (remove could not
    delete the stock backup, so actual stayed True).
    """
    from ichalaunch.config.settings import settings as s
    from ichalaunch.core.filesystem import clear_fs_caches
    from ichalaunch.mods.installer import apply_desired_state, plan_changes, remove_mod

    keys = (
        "desired_mods",
        "user_set_mods",
        "installed_mods",
        "user_mods",
        "game_path",
        "addons_path",
    )
    saved = {k: s.get(k) for k in keys}
    stock = b"MZ" + b"\0" * 64
    patched = b"MZ" + b"\x01" * 64
    try:
        with tempfile.TemporaryDirectory() as td:
            game = Path(td)
            (game / "WoW.exe").write_bytes(stock)
            (game / "WoW-OriginalBackup.exe").write_bytes(stock)
            s.set("game_path", str(game))
            s.set("addons_path", "")
            s.set("desired_mods", {"vanilla_tweaks": False})
            s.set("user_set_mods", ["vanilla_tweaks"])
            s.set("installed_mods", {})
            s.set("user_mods", [])
            clear_fs_caches()

            # Stock client: backup exists but matches WoW.exe → not applied.
            assert detect_actual_state(game).get("vanilla_tweaks") is False
            assert not any(c.get("id") == "vanilla_tweaks" for c in plan_changes())

            # Enable is pending until the exe actually differs from the backup.
            s.set_desired_mod("vanilla_tweaks", True)
            assert any(
                c["action"] == "install" and c["id"] == "vanilla_tweaks"
                for c in plan_changes()
            ), plan_changes()

            # Simulate a successful apply (byte-patch WoW.exe, keep stock backup).
            (game / "WoW.exe").write_bytes(patched)
            clear_fs_caches()
            assert detect_actual_state(game).get("vanilla_tweaks") is True
            assert not any(c.get("id") == "vanilla_tweaks" for c in plan_changes())

            # Disable → Apply pending until revert.
            s.set_desired_mod("vanilla_tweaks", False)
            assert any(
                c["action"] == "remove" and c["id"] == "vanilla_tweaks"
                for c in plan_changes()
            ), plan_changes()

            out = apply_desired_state()
            assert any("vanilla_tweaks" in line for line in out), out
            assert (game / "WoW.exe").read_bytes() == stock
            assert (game / "WoW-OriginalBackup.exe").is_file()
            assert detect_actual_state(game).get("vanilla_tweaks") is False
            # This is what turns off the Apply glow.
            assert not any(c.get("id") == "vanilla_tweaks" for c in plan_changes())
            assert plan_changes() == [], plan_changes()

            # Identical stock files: disable must not plan a remove (no glow).
            s.set_desired_mod("vanilla_tweaks", False)
            assert plan_changes() == [], plan_changes()
            remove_mod("vanilla_tweaks")
            assert (game / "WoW.exe").read_bytes() == stock
    finally:
        for k in keys:
            s.set(k, saved[k])
        clear_fs_caches()
    print("OK vanilla tweaks disable clears pending")


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
            assert desired.get("hd_patch_n") is True

            # User unchecks Reforged Patch-N — an explicit choice.
            s.set_desired_mod("hd_patch_n", False)
            assert "hd_patch_n" in s.user_set_mods
            plan = plan_changes()
            assert any(
                c["action"] == "remove" and c["id"] == "hd_patch_n" for c in plan
            ), plan

            out = apply_desired_state()
            assert "- hd_patch_n" in out, out
            assert not mpq.exists()

            # Immediately after apply (inside the 4s listing-cache TTL) the plan
            # must be clean — this is what drives the "unapplied changes" nag.
            assert plan_changes() == [], plan_changes()

            # Rescan syncs actual but must not flip the user's choice back on.
            desired = sync_desired_mods_from_disk()
            assert desired.get("hd_patch_n") is False
            assert detect_actual_state(game).get("hd_patch_n") is False

            # Even if the file reappears (manual copy), desired stays off.
            mpq.write_bytes(b"MPQ")
            clear_fs_caches()
            desired = sync_desired_mods_from_disk()
            assert desired.get("hd_patch_n") is False

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


def test_stock_patch9_not_owned_by_pretty_night_sky():
    """Official Data/patch-9.mpq must never be detected, planned, or deleted."""
    from ichalaunch.config.settings import settings as s
    from ichalaunch.core.detect import sync_desired_mods_from_disk
    from ichalaunch.core.filesystem import clear_fs_caches
    from ichalaunch.mods.installer import (
        _mod_owned_paths,
        apply_desired_state,
        apply_mod_toggle,
        detect_actual_state,
        get_mod,
        is_stock_data_mpq,
        plan_changes,
        remove_mod,
        stage_mpq_before_data,
    )

    assert is_stock_data_mpq("Data/patch-9.mpq")
    assert is_stock_data_mpq("patch.mpq")
    assert is_stock_data_mpq("PATCH-2.MPQ")
    assert not is_stock_data_mpq("Data/patch-Y.mpq")
    assert not is_stock_data_mpq("Data/patch-Z.mpq")
    assert not is_stock_data_mpq("Data/patch-A.mpq")

    sky = get_mod("pretty_night_sky")
    fog = get_mod("fog_pushback")
    assert sky is not None and fog is not None
    assert (sky.get("destination") or "").replace("\\", "/").lower() == "data/patch-z.mpq"
    assert (sky.get("source") or {}).get("filename", "").lower() == "patch-z.mpq"
    assert (fog.get("destination") or "").replace("\\", "/").lower() == "data/patch-y.mpq"
    assert "fog_pushback" not in (sky.get("conflicts") or [])
    assert "pretty_night_sky" not in (fog.get("conflicts") or [])
    assert not is_stock_data_mpq(sky.get("destination") or "")
    owned = _mod_owned_paths(sky)
    assert not any("patch-9.mpq" in p for p in owned), owned
    assert not any(p.endswith("patch-y.mpq") for p in owned), owned
    assert any(p.endswith("patch-z.mpq") for p in owned), owned

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
            stock = data / "patch-9.mpq"
            stock.write_bytes(b"OFFICIAL-PATCH-9")
            s.set("game_path", str(game))
            s.set("addons_path", "")
            s.set("desired_mods", {})
            s.set("user_set_mods", [])
            s.set("installed_mods", {})
            s.set("user_mods", [])
            clear_fs_caches()

            actual = detect_actual_state(game)
            assert actual.get("pretty_night_sky") is False

            desired = sync_desired_mods_from_disk()
            assert desired.get("pretty_night_sky") is not True

            apply_mod_toggle("pretty_night_sky", True)
            apply_mod_toggle("fog_pushback", True)
            assert s.desired_mods.get("fog_pushback") is True
            assert s.desired_mods.get("pretty_night_sky") is True

            s.set("desired_mods", {})
            s.set("user_set_mods", [])
            s.set_desired_mod("pretty_night_sky", False)
            plan = plan_changes()
            assert not any(
                c.get("id") == "pretty_night_sky" and c.get("action") == "remove"
                for c in plan
            ), plan

            out = apply_desired_state()
            assert stock.is_file()
            assert stock.read_bytes() == b"OFFICIAL-PATCH-9"
            assert not any(
                isinstance(ln, str) and "pretty_night_sky" in ln and ln.startswith("- ")
                for ln in out
            ), out

            remove_mod("pretty_night_sky")
            assert stock.is_file(), "remove_mod must not delete official patch-9.mpq"
            assert stock.read_bytes() == b"OFFICIAL-PATCH-9"

            work = game / "_stage"
            work.mkdir()
            downloaded = work / "patch-9.mpq"
            downloaded.write_bytes(b"NIGHT-SKY")
            staged = stage_mpq_before_data(downloaded, "Data/patch-Z.mpq", work)
            assert staged.name.lower() == "patch-z.mpq"
            assert staged.read_bytes() == b"NIGHT-SKY"
            assert not downloaded.exists()
            assert not (data / "patch-Z.mpq").exists()
            try:
                stage_mpq_before_data(work / "x.mpq", "Data/patch-9.mpq", work)
                raise AssertionError("stock dest must be rejected")
            except RuntimeError:
                pass
    finally:
        for k in keys:
            s.set(k, saved[k])
        clear_fs_caches()
    print("OK stock patch-9.mpq not owned by pretty night sky")


def test_stock_patch9_collision_migration():
    """Auto-seeded Pretty Night Sky desired/install records are dropped once."""
    from ichalaunch.config.settings import migrate_stock_patch9_collision

    seeded = {
        "desired_mods": {"pretty_night_sky": True, "vanillafixes": True},
        "user_set_mods": [],
        "installed_mods": {"pretty_night_sky": {"backfilled": True}},
    }
    assert migrate_stock_patch9_collision(seeded) is True
    assert "pretty_night_sky" not in seeded["desired_mods"]
    assert "pretty_night_sky" not in seeded["installed_mods"]
    assert seeded["desired_mods"]["vanillafixes"] is True
    assert seeded["stock_patch9_collision_migrated_v1"] is True
    assert migrate_stock_patch9_collision(seeded) is False

    explicit = {
        "desired_mods": {"pretty_night_sky": True},
        "user_set_mods": ["pretty_night_sky"],
        "installed_mods": {"pretty_night_sky": {"installed_at": "2024-01-01"}},
    }
    assert migrate_stock_patch9_collision(explicit) is True
    assert explicit["desired_mods"]["pretty_night_sky"] is True
    assert "pretty_night_sky" not in explicit["installed_mods"]
    print("OK stock patch-9 collision migration")


def test_catalog_mpq_letters_unique():
    """Letter destinations are unique except known HD L/T variants."""
    share_ok = {
        frozenset({"hd_patch_l", "hd_patch_l_less_thicc"}),
        frozenset({"hd_patch_t", "hd_patch_t_ultra"}),
    }
    dest_owners: dict[str, list[str]] = {}
    for mod in load_mod_catalog():
        dest = (mod.get("destination") or "").replace("\\", "/").lower()
        if not dest.endswith(".mpq") or not mod.get("id"):
            continue
        dest_owners.setdefault(dest, []).append(str(mod["id"]))
    for dest, ids in dest_owners.items():
        if len(ids) < 2:
            continue
        assert frozenset(ids) in share_ok, (dest, ids)
    print("OK catalog MPQ letter destinations unique")


def test_pretty_night_sky_migrates_off_fog_y():
    """Leftover night-sky Y is renamed to Z; Fog Pushback's Y is never stolen."""
    from ichalaunch.config.settings import settings as s
    from ichalaunch.core.filesystem import clear_fs_caches
    from ichalaunch.mods.installer import (
        detect_actual_state,
        looks_like_pretty_night_sky_mpq,
        migrate_legacy_pretty_night_sky_y,
    )

    sky_bytes = b"MPQ\x1a" + b"\0" * 16 + b"Environments\\Stars\\stars.blp"
    fog_bytes = b"MPQ\x1a" + b"\0" * 16 + b"DBFilesClient\\Light.dbc"
    unknown = b"MPQ\x1a" + b"\0" * 16 + b"unknown-payload"

    keys = ("desired_mods", "user_set_mods", "installed_mods", "user_mods", "game_path", "addons_path")
    saved = {k: s.get(k) for k in keys}
    try:
        with tempfile.TemporaryDirectory() as td:
            game = Path(td)
            (game / "WoW.exe").write_bytes(b"MZ")
            data = game / "Data"
            data.mkdir()
            s.set("game_path", str(game))
            s.set("addons_path", "")
            s.set("desired_mods", {})
            s.set("user_set_mods", [])
            s.set("installed_mods", {})
            s.set("user_mods", [])
            clear_fs_caches()

            y = data / "patch-Y.mpq"
            y.write_bytes(sky_bytes)
            assert looks_like_pretty_night_sky_mpq(y)
            actual = detect_actual_state(game)
            assert not y.exists()
            z = data / "patch-Z.mpq"
            assert z.is_file() and z.read_bytes() == sky_bytes
            assert actual.get("pretty_night_sky") is True
            assert actual.get("fog_pushback") is not True

            z.unlink()
            y.write_bytes(fog_bytes)
            clear_fs_caches()
            assert not looks_like_pretty_night_sky_mpq(y)
            assert migrate_legacy_pretty_night_sky_y(game) is False
            actual = detect_actual_state(game)
            assert y.is_file() and y.read_bytes() == fog_bytes
            assert actual.get("fog_pushback") is True
            assert actual.get("pretty_night_sky") is not True

            y.write_bytes(unknown)
            clear_fs_caches()
            assert migrate_legacy_pretty_night_sky_y(game) is False
            assert y.is_file()

            z.write_bytes(b"MPQZ")
            y.write_bytes(sky_bytes)
            clear_fs_caches()
            assert migrate_legacy_pretty_night_sky_y(game) is False
            assert y.is_file() and y.read_bytes() == sky_bytes
            assert z.read_bytes() == b"MPQZ"
    finally:
        for k in keys:
            s.set(k, saved[k])
        clear_fs_caches()
    print("OK pretty night sky Y to Z migrate leaves fog Y alone")


def test_stock_patch9_reacquire_detect():
    """Missing or undersized official patch-9 offers reacquire; healthy size does not."""
    from ichalaunch.mods import installer as installer_mod
    from ichalaunch.mods.stock_patch import (
        STOCK_PATCH9_EXPECTED_SIZE,
        STOCK_PATCH9_MIN_BYTES,
        classify_stock_patch9,
        inspect_stock_patch9,
        patch9_url_from_index_html,
        reacquire_stock_patch9,
        resolve_stock_patch9_url,
        should_offer_stock_patch9_reacquire,
        stock_patch9_size_floor,
    )

    assert classify_stock_patch9(False, 0) == "missing"
    assert classify_stock_patch9(True, 1024) == "too_small"
    assert classify_stock_patch9(True, STOCK_PATCH9_MIN_BYTES - 1) == "too_small"
    assert classify_stock_patch9(True, STOCK_PATCH9_MIN_BYTES) == "ok"
    assert classify_stock_patch9(True, STOCK_PATCH9_EXPECTED_SIZE) == "ok"
    assert classify_stock_patch9(True, STOCK_PATCH9_EXPECTED_SIZE + 4096) == "ok"
    floor = stock_patch9_size_floor()
    assert 400 * 1024 * 1024 <= floor <= STOCK_PATCH9_EXPECTED_SIZE

    html = (
        '<a href="patch-9.mpq">patch-9.mpq</a> 483.2 MB'
    )
    url = patch9_url_from_index_html(html, "https://share.ichasarmory.quest/")
    assert url == "https://share.ichasarmory.quest/patch-9.mpq"
    evil = patch9_url_from_index_html(
        '<a href="https://evil.example/patch-9.mpq">x</a>',
        "https://share.ichasarmory.quest/",
    )
    assert evil is None
    fallback = resolve_stock_patch9_url(html="<html>no file here</html>")
    assert fallback == "https://share.ichasarmory.quest/patch-9.mpq"

    orig_dl = installer_mod._download_source
    try:
        with tempfile.TemporaryDirectory() as td:
            game = Path(td)
            (game / "WoW.exe").write_bytes(b"MZ")
            data = game / "Data"
            data.mkdir()

            missing = inspect_stock_patch9(game)
            assert missing.state == "missing"
            assert should_offer_stock_patch9_reacquire(missing)

            stub = data / "patch-9.mpq"
            stub.write_bytes(b"MPQ" + b"\0" * 200)
            tiny = inspect_stock_patch9(game)
            assert tiny.state == "too_small"
            assert should_offer_stock_patch9_reacquire(tiny)

            healthy_bytes = b"MPQ" + b"H" * 500
            stub.write_bytes(healthy_bytes)
            ok = inspect_stock_patch9(game, expected_size=400, min_bytes=400)
            assert ok.state == "ok"
            assert not should_offer_stock_patch9_reacquire(ok)

            def fake_dl(source, work, progress=None):
                assert "share.ichasarmory.quest" in str(source.get("url") or "")
                out = Path(work) / "patch-9.mpq"
                out.write_bytes(b"MPQ" + b"N" * 500)
                return out

            installer_mod._download_source = fake_dl
            stub.write_bytes(b"STUB")
            dest = reacquire_stock_patch9(
                game, expected_size=400, min_bytes=400,
                download_url="https://share.ichasarmory.quest/patch-9.mpq",
            )
            assert dest.is_file()
            assert dest.read_bytes().startswith(b"MPQ")
            assert dest.stat().st_size > 400
            assert stub.read_bytes() != b"STUB"

            try:
                reacquire_stock_patch9(
                    game, expected_size=400, min_bytes=400,
                    download_url="https://share.ichasarmory.quest/patch-9.mpq",
                )
                raise AssertionError("must not clobber a healthy-sized patch-9")
            except RuntimeError as exc:
                assert "complete" in str(exc).lower()
            assert dest.read_bytes().startswith(b"MPQ")
    finally:
        installer_mod._download_source = orig_dl
    print("OK stock patch-9 reacquire detect")


def test_darker_nights_migration():
    """Legacy darker_nights settings migrate to hd_patch_n on load."""
    from ichalaunch.config.settings import migrate_legacy_mod_ids

    on = {
        "desired_mods": {"darker_nights": True, "vanillafixes": True},
        "user_set_mods": ["darker_nights"],
        "installed_mods": {"darker_nights": {"installed_at": "2024-01-01"}},
    }
    migrate_legacy_mod_ids(on)
    assert on["desired_mods"]["hd_patch_n"] is True
    assert "darker_nights" not in on["desired_mods"]
    assert on["user_set_mods"] == ["hd_patch_n"]
    assert "hd_patch_n" in on["installed_mods"]
    assert "darker_nights" not in on["installed_mods"]

    off = {
        "desired_mods": {"darker_nights": False},
        "user_set_mods": ["darker_nights"],
        "installed_mods": {},
    }
    migrate_legacy_mod_ids(off)
    assert off["desired_mods"]["hd_patch_n"] is False
    assert "darker_nights" not in off["desired_mods"]
    assert off["user_set_mods"] == ["hd_patch_n"]

    detected = {"desired_mods": {"darker_nights": True}, "user_set_mods": [], "installed_mods": {}}
    migrate_legacy_mod_ids(detected)
    assert detected["desired_mods"] == {"hd_patch_n": True}
    assert detected["user_set_mods"] == []
    print("OK darker nights migration")


def test_mod_toggle_resolution():
    """HD patch deps/conflicts auto-enable companions and disable dependents."""
    from ichalaunch.config.settings import settings as s
    from ichalaunch.mods.installer import apply_mod_toggle, resolve_mod_toggle

    keys = ("desired_mods", "user_set_mods")
    saved = {k: s.get(k) for k in keys}
    try:
        s.set("desired_mods", {})
        s.set("user_set_mods", [])
        env = resolve_mod_toggle("hd_patch_b", True)
        assert env.get("hd_patch_d") and env.get("hd_patch_e") and env.get("vanilla_helpers")
        apply_mod_toggle("hd_patch_l", True)
        swap_l = resolve_mod_toggle("hd_patch_l_less_thicc", True)
        assert swap_l.get("hd_patch_l") is False and swap_l.get("hd_patch_l_less_thicc") is True
        off = resolve_mod_toggle("hd_patch_a", False)
        assert off.get("hd_patch_a") is False and off.get("hd_patch_l") is False
        apply_mod_toggle("hd_patch_t_ultra", True)
        apply_mod_toggle("hd_patch_u", True)
        swap = resolve_mod_toggle("hd_patch_t", True)
        assert swap.get("hd_patch_t_ultra") is False and swap.get("hd_patch_u") is False
        apply_mod_toggle("vanillafixes", True)
        vf_dxvk = resolve_mod_toggle("dxvk", True)
        assert vf_dxvk.get("vanillafixes") is False and vf_dxvk.get("dxvk") is True
        apply_mod_toggle("dxvk", True)
        dxvk_vf = resolve_mod_toggle("vanillafixes", True)
        assert dxvk_vf.get("dxvk") is False and dxvk_vf.get("vanillafixes") is True
    finally:
        for k in keys:
            s.set(k, saved[k])
    print("OK mod toggle deps/conflicts")


def test_mod_author_labels():
    from ichalaunch.ui.widgets.common import mod_author

    vf = {"id": "vanillafixes", "source": {"repo": "hannesmann/vanillafixes"}}
    assert mod_author(vf) == "hannesmann"
    hd = {"id": "hd_patch_a", "category": "HD Graphics"}
    assert mod_author(hd) == "Project Reforged"
    explicit = {"id": "x", "author": "Custom Author"}
    assert mod_author(explicit) == "Custom Author"
    print("OK mod author labels")


def test_vanillafixes_dxvk_reconcile():
    from ichalaunch.config.settings import settings as s
    from ichalaunch.mods.installer import (
        apply_vanillafixes_dxvk_choice,
        plan_changes,
        reconcile_vanillafixes_dxvk,
        vanillafixes_dxvk_both_enabled,
    )

    keys = ("desired_mods", "user_set_mods", "game_path", "addons_path")
    saved = {k: s.get(k) for k in keys}
    try:
        both = {"vanillafixes": True, "dxvk": True}
        assert vanillafixes_dxvk_both_enabled(both)
        fixed = reconcile_vanillafixes_dxvk(
            both, actual={"vanillafixes": True, "dxvk": True}
        )
        assert fixed.get("dxvk") and not fixed.get("vanillafixes")
        fixed2 = reconcile_vanillafixes_dxvk(both, prefer="vanillafixes")
        assert fixed2.get("vanillafixes") and not fixed2.get("dxvk")

        with tempfile.TemporaryDirectory() as td:
            game = Path(td)
            (game / "WoW.exe").write_bytes(b"MZ")
            s.set("game_path", str(game))
            s.set("addons_path", "")
            s.set("desired_mods", {"vanillafixes": True, "dxvk": True})
            s.set("user_set_mods", [])
            plan = plan_changes()
            install_ids = [c["id"] for c in plan if c.get("action") == "install"]
            assert install_ids.count("vanillafixes") + install_ids.count("dxvk") == 1

        s.set("desired_mods", {"vanillafixes": True, "dxvk": True})
        changes = apply_vanillafixes_dxvk_choice("vanillafixes")
        assert s.desired_mods.get("vanillafixes")
        assert not s.desired_mods.get("dxvk")
        assert changes.get("dxvk") is False
    finally:
        for k in keys:
            s.set(k, saved[k])
    print("OK vanillafixes dxvk reconcile")


def test_dxvk_detect_plan_clean():
    """DXVK on disk must not leave a phantom vanillafixes remove in plan_changes."""
    from ichalaunch.config.settings import settings as s
    from ichalaunch.core.filesystem import clear_fs_caches
    from ichalaunch.mods.installer import detect_actual_state, plan_changes

    keys = ("game_path", "addons_path", "desired_mods", "user_set_mods")
    saved = {k: s.get(k) for k in keys}
    try:
        with tempfile.TemporaryDirectory() as td:
            game = Path(td)
            (game / "WoW.exe").write_bytes(b"MZ")
            (game / "VanillaFixes.exe").write_bytes(b"MZ")
            (game / "d3d9.dll").write_bytes(b"MZ")
            (game / "dxvk.conf").write_text("d3d9.enlargeHardwareCursor = 2\n", encoding="utf-8")
            s.set("game_path", str(game))
            s.set("addons_path", "")
            s.set("desired_mods", {"vanillafixes": False, "dxvk": True})
            s.set("user_set_mods", ["dxvk"])
            clear_fs_caches()

            actual = detect_actual_state(game)
            assert actual.get("dxvk") is True, actual
            assert not actual.get("vanillafixes"), actual
            assert plan_changes() == [], plan_changes()
    finally:
        for k in keys:
            s.set(k, saved[k])
        clear_fs_caches()
    print("OK dxvk detect plan clean")


def test_hd_patch_lt_exclusive_planning():
    """Shared patch-L/T MPQs must not plan phantom removes for unselected variants."""
    from ichalaunch.config.settings import settings as s
    from ichalaunch.core.filesystem import clear_fs_caches
    from ichalaunch.mods.installer import detect_actual_state, plan_changes

    keys = ("game_path", "addons_path", "desired_mods", "user_set_mods", "installed_mods")
    saved = {k: s.get(k) for k in keys}
    try:
        with tempfile.TemporaryDirectory() as td:
            game = Path(td)
            data = game / "Data"
            data.mkdir()
            (game / "WoW.exe").write_bytes(b"MZ")
            (data / "patch-L.mpq").write_bytes(b"mpq")
            (data / "patch-T.mpq").write_bytes(b"mpq")
            s.set("game_path", str(game))
            s.set("addons_path", "")
            clear_fs_caches()

            def assert_lt_clean(desired: dict[str, bool]) -> None:
                s.set("desired_mods", desired)
                s.set("user_set_mods", [mid for mid, on in desired.items() if on])
                s.set("installed_mods", {})
                actual = detect_actual_state(game)
                if desired.get("hd_patch_l"):
                    assert actual.get("hd_patch_l") and not actual.get("hd_patch_l_less_thicc"), actual
                if desired.get("hd_patch_l_less_thicc"):
                    assert actual.get("hd_patch_l_less_thicc") and not actual.get("hd_patch_l"), actual
                if desired.get("hd_patch_t"):
                    assert actual.get("hd_patch_t") and not actual.get("hd_patch_t_ultra"), actual
                if desired.get("hd_patch_t_ultra"):
                    assert actual.get("hd_patch_t_ultra") and not actual.get("hd_patch_t"), actual
                remove_ids = {c["id"] for c in plan_changes() if c.get("action") == "remove"}
                assert "hd_patch_l" not in remove_ids or not desired.get("hd_patch_l"), remove_ids
                assert "hd_patch_l_less_thicc" not in remove_ids or not desired.get(
                    "hd_patch_l_less_thicc"
                ), remove_ids
                assert "hd_patch_t" not in remove_ids or not desired.get("hd_patch_t"), remove_ids
                assert "hd_patch_t_ultra" not in remove_ids or not desired.get(
                    "hd_patch_t_ultra"
                ), remove_ids
                if desired.get("hd_patch_l"):
                    assert "hd_patch_l_less_thicc" not in remove_ids, remove_ids
                if desired.get("hd_patch_l_less_thicc"):
                    assert "hd_patch_l" not in remove_ids, remove_ids
                if desired.get("hd_patch_t"):
                    assert "hd_patch_t_ultra" not in remove_ids, remove_ids
                if desired.get("hd_patch_t_ultra"):
                    assert "hd_patch_t" not in remove_ids, remove_ids

            assert_lt_clean({"hd_patch_l": True, "hd_patch_t_ultra": True})
            assert_lt_clean({"hd_patch_l_less_thicc": True, "hd_patch_t": True})
    finally:
        for k in keys:
            s.set(k, saved[k])
        clear_fs_caches()
    print("OK hd patch L/T exclusive planning")


def test_hd_patch_exclusive_variant_swap():
    """Switching L/T MPQ siblings must plan reinstall, not a silent no-op."""
    from ichalaunch.config.settings import settings as s
    from ichalaunch.core.filesystem import clear_fs_caches
    from ichalaunch.mods.installer import apply_mod_toggle, plan_changes

    keys = ("game_path", "addons_path", "desired_mods", "user_set_mods", "installed_mods")
    saved = {k: s.get(k) for k in keys}
    try:
        with tempfile.TemporaryDirectory() as td:
            game = Path(td)
            data = game / "Data"
            data.mkdir()
            (game / "WoW.exe").write_bytes(b"MZ")
            (data / "patch-L.mpq").write_bytes(b"regular")
            (data / "patch-T.mpq").write_bytes(b"regular")
            (data / "patch-A.mpq").write_bytes(b"a")
            s.set("game_path", str(game))
            s.set("addons_path", "")
            clear_fs_caches()

            def plan_install_ids() -> set[str]:
                return {c["id"] for c in plan_changes() if c.get("action") == "install"}

            s.set(
                "installed_mods",
                {"hd_patch_l": {"url": "regular"}, "hd_patch_t": {"url": "regular"}},
            )
            s.set(
                "desired_mods",
                {"hd_patch_l": True, "hd_patch_t": True, "hd_patch_a": True, "vanilla_helpers": True},
            )
            apply_mod_toggle("hd_patch_l_less_thicc", True)
            assert s.desired_mods.get("hd_patch_l_less_thicc")
            assert not s.desired_mods.get("hd_patch_l")
            assert "hd_patch_l_less_thicc" in plan_install_ids()
            assert "hd_patch_l" not in {
                c["id"] for c in plan_changes() if c.get("action") == "remove"
            }

            s.set("installed_mods", {"hd_patch_t_ultra": {"url": "ultra"}})
            s.set("desired_mods", {"hd_patch_t_ultra": True, "hd_patch_a": True, "vanilla_helpers": True})
            apply_mod_toggle("hd_patch_t", True)
            assert s.desired_mods.get("hd_patch_t")
            assert not s.desired_mods.get("hd_patch_t_ultra")
            assert "hd_patch_t" in plan_install_ids()
            assert "hd_patch_t_ultra" not in {
                c["id"] for c in plan_changes() if c.get("action") == "remove"
            }

            # Detection must reflect the recorded variant, not desired wish.
            s.set("installed_mods", {"hd_patch_l": {"variant_id": "hd_patch_l"}})
            s.set(
                "desired_mods",
                {"hd_patch_l_less_thicc": True, "hd_patch_a": True, "vanilla_helpers": True},
            )
            from ichalaunch.mods.installer import detect_actual_state

            actual = detect_actual_state(game)
            assert actual.get("hd_patch_l") and not actual.get("hd_patch_l_less_thicc"), actual
            assert "hd_patch_l_less_thicc" in plan_install_ids()
    finally:
        for k in keys:
            s.set(k, saved[k])
        clear_fs_caches()
    print("OK hd patch exclusive variant swap")


def test_hd_patch_both_desired_reconciled():
    """Stale desired_mods with both L/T siblings must reconcile to one each."""
    from ichalaunch.config.settings import settings as s
    from ichalaunch.core.filesystem import clear_fs_caches
    from ichalaunch.mods.installer import plan_changes, reconcile_exclusive_desired_mods

    keys = ("game_path", "addons_path", "desired_mods", "user_set_mods", "installed_mods")
    saved = {k: s.get(k) for k in keys}
    try:
        with tempfile.TemporaryDirectory() as td:
            game = Path(td)
            data = game / "Data"
            data.mkdir()
            (game / "WoW.exe").write_bytes(b"MZ")
            (data / "patch-L.mpq").write_bytes(b"regular")
            (data / "patch-T.mpq").write_bytes(b"ultra")
            s.set("game_path", str(game))
            s.set("addons_path", "")
            s.set("installed_mods", {"hd_patch_l": {}, "hd_patch_t_ultra": {}})
            s.set(
                "desired_mods",
                {
                    "hd_patch_l": True,
                    "hd_patch_l_less_thicc": True,
                    "hd_patch_t": True,
                    "hd_patch_t_ultra": True,
                    "hd_patch_a": True,
                    "vanilla_helpers": True,
                },
            )
            s.set("user_set_mods", [])
            clear_fs_caches()

            fixed = reconcile_exclusive_desired_mods(s.desired_mods, actual={"hd_patch_l": True, "hd_patch_t_ultra": True})
            assert fixed.get("hd_patch_l") and not fixed.get("hd_patch_l_less_thicc"), fixed
            assert fixed.get("hd_patch_t_ultra") and not fixed.get("hd_patch_t"), fixed

            s.set("desired_mods", {
                "hd_patch_l": True,
                "hd_patch_l_less_thicc": True,
                "hd_patch_t": True,
                "hd_patch_t_ultra": True,
                "hd_patch_a": True,
                "vanilla_helpers": True,
            })
            plan_changes()
            d = s.desired_mods
            assert d.get("hd_patch_l") and not d.get("hd_patch_l_less_thicc"), d
            assert d.get("hd_patch_t_ultra") and not d.get("hd_patch_t"), d
            remove_ids = {c["id"] for c in plan_changes() if c.get("action") == "remove"}
            assert "hd_patch_l" not in remove_ids, remove_ids
            assert "hd_patch_t_ultra" not in remove_ids, remove_ids
    finally:
        for k in keys:
            s.set(k, saved[k])
        clear_fs_caches()
    print("OK hd patch both desired reconciled")


def test_backfill_installed_mods_on_detect():
    """Detect/update scan backfills installed_mods for on-disk mods missing records."""
    from ichalaunch.config.settings import settings as s
    from ichalaunch.core.filesystem import clear_fs_caches
    from ichalaunch.mods.installer import apply_mod_toggle, detect_actual_state, plan_changes

    keys = ("game_path", "addons_path", "desired_mods", "user_set_mods", "installed_mods")
    saved = {k: s.get(k) for k in keys}
    try:
        with tempfile.TemporaryDirectory() as td:
            game = Path(td)
            data = game / "Data"
            data.mkdir()
            (game / "WoW.exe").write_bytes(b"MZ")
            (data / "patch-L.mpq").write_bytes(b"regular")
            (data / "patch-A.mpq").write_bytes(b"a")
            s.set("game_path", str(game))
            s.set("addons_path", "")
            s.set("installed_mods", {})
            s.set(
                "desired_mods",
                {"hd_patch_l": True, "hd_patch_a": True, "vanilla_helpers": True},
            )
            clear_fs_caches()

            detect_actual_state(game)
            rec = s.installed_mods.get("hd_patch_l") or {}
            assert rec.get("variant_id") == "hd_patch_l", rec
            assert "hd_patch_l_less_thicc" not in s.installed_mods

            apply_mod_toggle("hd_patch_l_less_thicc", True)
            install_ids = {c["id"] for c in plan_changes() if c.get("action") == "install"}
            assert "hd_patch_l_less_thicc" in install_ids, install_ids
    finally:
        for k in keys:
            s.set(k, saved[k])
        clear_fs_caches()
    print("OK backfill installed mods on detect")


def test_resolve_launch_exe():
    from ichalaunch.config.settings import settings as s
    from ichalaunch.game.launcher import launch_exe_note, resolve_launch_exe

    keys = ("game_path", "vanillafixes_enabled")
    saved = {k: s.get(k) for k in keys}
    try:
        with tempfile.TemporaryDirectory() as td:
            game = Path(td)
            wow = game / "WoW.exe"
            vf = game / "VanillaFixes.exe"
            wow.write_bytes(b"MZ")
            s.set("game_path", str(game))
            s.set("vanillafixes_enabled", True)

            assert resolve_launch_exe(game) == wow
            assert launch_exe_note(game, wow) == "VanillaFixes.exe not found in game folder"

            vf.write_bytes(b"MZ")
            assert resolve_launch_exe(game) == vf
            assert launch_exe_note(game, vf) is None

            s.set("vanillafixes_enabled", False)
            assert resolve_launch_exe(game) == wow
            assert launch_exe_note(game, wow) == "launch through VanillaFixes disabled in Settings"
    finally:
        for k, v in saved.items():
            s.set(k, v)
    print("OK resolve_launch_exe")


def test_vf_mode_labels():
    from ichalaunch.core.filesystem import clear_fs_caches
    from ichalaunch.game.launcher import (
        detect_vf_disk_mode,
        launch_mode_label_for_exe,
        vf_disk_hint_line,
        vf_mode_display,
    )

    assert vf_mode_display("dxvk") == "VanillaFixes + DXVK (Vulkan)"
    assert vf_mode_display("vanillafixes") == "VanillaFixes (standard)"
    assert vf_mode_display("none") == "none"

    with tempfile.TemporaryDirectory() as td:
        game = Path(td)
        clear_fs_caches()
        assert detect_vf_disk_mode(game) == "none"
        (game / "VanillaFixes.exe").write_bytes(b"MZ")
        clear_fs_caches()
        assert detect_vf_disk_mode(game) == "vanillafixes"
        (game / "d3d9.dll").write_bytes(b"x")
        (game / "dxvk.conf").write_text("x", encoding="utf-8")
        clear_fs_caches()
        assert detect_vf_disk_mode(game) == "dxvk"
        vf = game / "VanillaFixes.exe"
        assert launch_mode_label_for_exe(game, vf) == "VanillaFixes + DXVK (Vulkan)"
        (game / "dxvk.conf").unlink()
        clear_fs_caches()
        assert launch_mode_label_for_exe(game, vf) == "VanillaFixes (standard)"
        assert launch_mode_label_for_exe(game, game / "WoW.exe") == "WoW.exe (direct)"
        hints = vf_disk_hint_line(game)
        assert "d3d9.dll present" in hints
        assert "dxvk.conf absent" in hints
    print("OK vf mode labels")


def test_vf_dxvk_roundtrip_plan_clean():
    """VF → DXVK → VF toggles with apply must end with an empty plan."""
    from ichalaunch.config.settings import settings as s
    from ichalaunch.core.filesystem import clear_fs_caches
    from ichalaunch.mods.installer import (
        apply_desired_state,
        apply_mod_toggle,
        detect_actual_state,
        plan_changes,
    )

    keys = (
        "game_path",
        "addons_path",
        "desired_mods",
        "user_set_mods",
        "installed_mods",
        "vanillafixes_enabled",
    )
    saved = {k: s.get(k) for k in keys}
    try:
        with tempfile.TemporaryDirectory() as td:
            game = Path(td)
            (game / "WoW.exe").write_bytes(b"MZ")
            s.set("game_path", str(game))
            s.set("addons_path", "")
            s.set("installed_mods", {})
            clear_fs_caches()

            s.set("desired_mods", {"vanillafixes": True})
            s.set("user_set_mods", ["vanillafixes"])
            apply_desired_state()
            assert plan_changes() == [], plan_changes()

            apply_mod_toggle("dxvk", True)
            apply_desired_state()
            assert plan_changes() == [], plan_changes()
            actual = detect_actual_state(game)
            assert actual.get("dxvk") and not actual.get("vanillafixes"), actual

            apply_mod_toggle("vanillafixes", True)
            apply_desired_state()
            actual = detect_actual_state(game)
            assert actual.get("vanillafixes") and not actual.get("dxvk"), actual
            assert not (game / "d3d9.dll").exists()
            assert not (game / "dxvk.conf").exists()
            assert plan_changes() == [], plan_changes()
    finally:
        for k in keys:
            s.set(k, saved[k])
        clear_fs_caches()
    print("OK vf dxvk roundtrip plan clean")


def test_vf_dxvk_roundtrip_simulated_plan_clean():
    """Simulated disk: DXVK artifacts must be removed when switching back to VF."""
    from ichalaunch.config.settings import settings as s
    from ichalaunch.core.filesystem import clear_fs_caches
    from ichalaunch.mods.installer import (
        apply_desired_state,
        apply_mod_toggle,
        detect_actual_state,
        plan_changes,
        remove_mod,
    )

    keys = (
        "game_path",
        "addons_path",
        "desired_mods",
        "user_set_mods",
        "installed_mods",
        "vanillafixes_enabled",
    )
    saved = {k: s.get(k) for k in keys}
    try:
        with tempfile.TemporaryDirectory() as td:
            game = Path(td)
            (game / "WoW.exe").write_bytes(b"MZ")
            s.set("game_path", str(game))
            s.set("addons_path", "")
            clear_fs_caches()

            (game / "VanillaFixes.exe").write_bytes(b"VF")
            (game / "VfPatcher.dll").write_bytes(b"dll")
            s.set("desired_mods", {"vanillafixes": True})
            s.set("user_set_mods", ["vanillafixes"])
            s.set("installed_mods", {"vanillafixes": {"name": "VanillaFixes"}})
            assert plan_changes() == [], plan_changes()

            apply_mod_toggle("dxvk", True)
            (game / "d3d9.dll").write_bytes(b"dxvk")
            (game / "dxvk.conf").write_text("x", encoding="utf-8")
            s.set("installed_mods", {"dxvk": {"name": "DXVK"}})
            clear_fs_caches()
            assert plan_changes() == [], plan_changes()

            apply_mod_toggle("vanillafixes", True)
            plan = plan_changes()
            install_ids = {c["id"] for c in plan if c.get("action") == "install"}
            remove_ids = {c["id"] for c in plan if c.get("action") == "remove"}
            assert "vanillafixes" in install_ids, plan
            assert "dxvk" in remove_ids, plan

            for ch in plan:
                if ch.get("action") == "remove":
                    remove_mod(ch["id"])
            (game / "VanillaFixes.exe").write_bytes(b"VF-new")
            clear_fs_caches()
            actual = detect_actual_state(game)
            assert actual.get("vanillafixes") and not actual.get("dxvk"), actual
            assert plan_changes() == [], plan_changes()
    finally:
        for k in keys:
            s.set(k, saved[k])
        clear_fs_caches()
    print("OK vf dxvk roundtrip simulated plan clean")


def test_dxvk_switch_keeps_vanillafixes_exe():
    """Switching VF → DXVK must not delete VanillaFixes.exe (DXVK bundle needs it)."""
    from ichalaunch.config.settings import settings as s
    from ichalaunch.game.launcher import launch_exe_note, resolve_launch_exe
    from ichalaunch.mods.installer import (
        apply_desired_state,
        apply_mod_toggle,
        detect_actual_state,
        plan_changes,
    )

    keys = (
        "game_path",
        "addons_path",
        "desired_mods",
        "user_set_mods",
        "vanillafixes_enabled",
    )
    saved = {k: s.get(k) for k in keys}
    try:
        with tempfile.TemporaryDirectory() as td:
            game = Path(td)
            (game / "WoW.exe").write_bytes(b"MZ")
            s.set("game_path", str(game))
            s.set("addons_path", "")
            s.set("desired_mods", {"vanillafixes": True})
            s.set("user_set_mods", [])
            apply_desired_state()
            assert (game / "VanillaFixes.exe").is_file()

            apply_mod_toggle("dxvk", True)
            assert not s.desired_mods.get("vanillafixes")
            assert s.desired_mods.get("dxvk")
            plan = plan_changes()
            assert any(c.get("id") == "dxvk" and c.get("action") == "install" for c in plan)
            out = apply_desired_state()
            assert "+ dxvk" in out
            assert (game / "VanillaFixes.exe").is_file(), out
            assert (game / "d3d9.dll").is_file(), out
            assert (game / "dxvk.conf").is_file(), out
            actual = detect_actual_state(game)
            assert actual.get("dxvk") is True, actual
            assert not actual.get("vanillafixes"), actual
            assert plan_changes() == [], plan_changes()
            vf = resolve_launch_exe(game)
            assert vf.name.lower() == "vanillafixes.exe"
            assert launch_exe_note(game, vf) is None

            # Prior VF on disk, user only wants DXVK — remove step must keep VF.exe.
            from ichalaunch.core.filesystem import clear_fs_caches

            (game / "VanillaFixes.exe").unlink()
            (game / "VfPatcher.dll").unlink(missing_ok=True)
            (game / "d3d9.dll").unlink(missing_ok=True)
            (game / "dxvk.conf").unlink(missing_ok=True)
            (game / "VanillaFixes.exe").write_bytes(b"MZ")
            (game / "VfPatcher.dll").write_bytes(b"dll")
            s.set("desired_mods", {"vanillafixes": False, "dxvk": True})
            s.set("user_set_mods", ["dxvk"])
            clear_fs_caches()
            apply_desired_state()
            assert (game / "VanillaFixes.exe").is_file()
            assert (game / "d3d9.dll").is_file()
    finally:
        for k in keys:
            s.set(k, saved[k])
    print("OK dxvk switch keeps vanillafixes exe")


def test_detect_game_ravencraft_subfolder():
    from ichalaunch.config.settings import settings as s
    from ichalaunch.game.launcher import detect_game

    keys = ("game_path",)
    saved = {k: s.get(k) for k in keys}
    try:
        with tempfile.TemporaryDirectory() as td:
            parent = Path(td) / "Games"
            rc = parent / "RavenCraft"
            rc.mkdir(parents=True)
            (rc / "WoW.exe").write_bytes(b"MZ")
            s.set("game_path", str(parent))
            assert detect_game().resolve() == rc.resolve()
    finally:
        for k in keys:
            s.set(k, saved[k])
    print("OK detect game ravencraft subfolder")


def test_assess_dxvk_gpu():
    from ichalaunch.core import gpu_compat

    orig = gpu_compat.query_gpu_names
    try:
        gpu_compat.query_gpu_names = lambda: ("Intel(R) HD Graphics 4000",)
        if hasattr(gpu_compat.query_gpu_names, "cache_clear"):
            gpu_compat.query_gpu_names.cache_clear()
        level, names, msg = gpu_compat.assess_dxvk_gpu()
        assert level == "bad"
        assert names and "Intel" in names[0]
        gpu_compat.query_gpu_names = lambda: ("NVIDIA GeForce RTX 3070",)
        level2, _, msg2 = gpu_compat.assess_dxvk_gpu()
        assert level2 == "ok"
        assert "NVIDIA" in msg2
    finally:
        gpu_compat.query_gpu_names = orig
        if hasattr(orig, "cache_clear"):
            orig.cache_clear()
    print("OK assess dxvk gpu")


def test_addon_fork_version_labels():
    from ichalaunch.ui.widgets.common import addon_fork_label, addon_version_label

    entry = {
        "repo": "https://github.com/McPewPew/MinimapButtonBag",
        "pin_release": "2.1.0",
    }
    assert addon_fork_label(entry) == "McPewPew/MinimapButtonBag"
    archived = {
        "repo": "https://github.com/olduser/MinimapButtonBag",
        "archived": True,
    }
    assert addon_fork_label(archived) == "olduser/MinimapButtonBag (archived)"
    assert addon_version_label(entry) == "v2.1.0"
    meta = {"version": "1.2.3"}
    assert addon_version_label(entry, meta) == "v1.2.3"
    print("OK addon fork version labels")


def test_addon_github_browse_helpers():
    from ichalaunch.addons.github import (
        addon_install_url_for_choice,
        catalog_fork_entries,
        clear_github_browse_cache,
        fork_entry_from_repo_url,
        parse_entry_owner_repo,
        sort_fork_entries,
    )

    bag = {
        "repo": "https://github.com/The-Kludge-Bureau/Bagshui/releases/tag/1.5.16",
        "pin_release": "1.5.16",
        "forks": [
            {
                "label": "NiclasEriksen",
                "repo": "https://github.com/NiclasEriksen/Bagshui",
            },
        ],
    }
    forks = catalog_fork_entries(bag)
    assert len(forks) == 2
    assert parse_entry_owner_repo(bag) == ("The-Kludge-Bureau", "Bagshui")
    fe = fork_entry_from_repo_url("https://github.com/shagu/ShaguTweaks")
    assert fe["owner"] == "shagu" and fe["repo_name"] == "ShaguTweaks"
    url = addon_install_url_for_choice(fe, "1.2.3")
    assert url.endswith("/releases/tag/1.2.3")
    ordered = sort_fork_entries(
        [
            {"label": "zeta/archived", "archived": True},
            {"label": "alpha/active"},
            {"label": "beta/archived", "archived": True},
        ]
    )
    assert [f["label"] for f in ordered] == [
        "alpha/active",
        "beta/archived",
        "zeta/archived",
    ]
    clear_github_browse_cache()
    print("OK addon github browse helpers")


def test_plan_changes_hd_env_set_no_recursion():
    """HD environment set B/D/E has circular deps — plan_changes must not recurse."""
    from ichalaunch.config.settings import settings as s
    from ichalaunch.mods.installer import apply_mod_toggle, plan_changes

    keys = ("desired_mods", "user_set_mods", "game_path", "addons_path")
    saved = {k: s.get(k) for k in keys}
    try:
        with tempfile.TemporaryDirectory() as td:
            game = Path(td)
            (game / "WoW.exe").write_bytes(b"MZ")
            s.set("game_path", str(game))
            s.set("addons_path", "")
            s.set("desired_mods", {})
            s.set("user_set_mods", [])
            apply_mod_toggle("hd_patch_b", True)
            assert s.desired_mods.get("hd_patch_d") and s.desired_mods.get("hd_patch_e")
            plan = plan_changes()
            install_ids = [c["id"] for c in plan if c.get("action") == "install"]
            assert "vanilla_helpers" in install_ids
            assert "hd_patch_b" in install_ids
            assert install_ids.index("vanilla_helpers") < install_ids.index("hd_patch_b")
    finally:
        for k in keys:
            s.set(k, saved[k])
    print("OK plan_changes HD env set no recursion")


def test_vanilla_helpers_hd_dependency():
    """HD patches require VanillaHelpers desired, planned install, and blocked disable."""
    from ichalaunch.config.settings import settings as s
    from ichalaunch.core.detect import sync_desired_mods_from_disk
    from ichalaunch.core.filesystem import clear_fs_caches
    from ichalaunch.mods.installer import (
        apply_mod_toggle,
        enforce_vanilla_helpers_for_hd_desired,
        plan_changes,
        resolve_mod_toggle,
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
        s.set("desired_mods", {})
        s.set("user_set_mods", [])
        apply_mod_toggle("hd_patch_a", True)
        assert s.desired_mods.get("vanilla_helpers") is True

        blocked = resolve_mod_toggle("vanilla_helpers", False)
        assert blocked == {}

        with tempfile.TemporaryDirectory() as td:
            game = Path(td)
            (game / "WoW.exe").write_bytes(b"MZ")
            data = game / "Data"
            data.mkdir()
            (data / "patch-A.mpq").write_bytes(b"MPQ")
            s.set("game_path", str(game))
            s.set("addons_path", "")
            s.set("desired_mods", {"hd_patch_a": True})
            s.set("user_set_mods", [])
            clear_fs_caches()

            desired = sync_desired_mods_from_disk()
            assert desired.get("hd_patch_a") is True
            assert desired.get("vanilla_helpers") is True

            plan = plan_changes()
            assert any(
                c["action"] == "install" and c["id"] == "vanilla_helpers" for c in plan
            ), plan
            assert not any(
                c["action"] == "install" and c["id"] == "hd_patch_a" for c in plan
            ), plan

            (data / "patch-A.mpq").unlink(missing_ok=True)
            clear_fs_caches()
            plan_both = plan_changes()
            install_ids = [
                c["id"] for c in plan_both if c.get("action") == "install"
            ]
            assert "vanilla_helpers" in install_ids and "hd_patch_a" in install_ids
            assert install_ids.index("vanilla_helpers") < install_ids.index("hd_patch_a")

            enforced = enforce_vanilla_helpers_for_hd_desired({"hd_patch_c": True})
            assert enforced.get("vanilla_helpers") is True
    finally:
        for k in keys:
            s.set(k, saved[k])
        clear_fs_caches()
    print("OK vanilla helpers HD dependency")


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


def test_git_refs_and_tip_index():
    """Upload-pack / Atom parsers and catalog tip-index lookup stay off REST."""
    from ichalaunch.addons.git_refs import (
        newest_version_tag,
        parse_atom_commit_sha,
        parse_atom_release_tag,
        parse_ls_remote,
        parse_upload_pack_refs,
    )
    from ichalaunch.addons.tip_index import (
        clear_tip_index_cache,
        lookup_latest_tag,
        lookup_tip,
        normalize_index,
        repo_entry_from_refs,
    )
    from ichalaunch.addons import tip_index as tips

    # pkt-line advertisement (protocol v1)
    def _pkt(payload: bytes) -> bytes:
        return f"{len(payload) + 4:04x}".encode("ascii") + payload

    blob = b"".join(
        [
            _pkt(b"# service=git-upload-pack\n"),
            b"0000",
            _pkt(
                b"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa HEAD\0"
                b"symref=HEAD:refs/heads/master agent=git/github\n"
            ),
            _pkt(b"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa refs/heads/master\n"),
            _pkt(b"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb refs/heads/dev\n"),
            _pkt(b"cccccccccccccccccccccccccccccccccccccccc refs/tags/v1.2.0\n"),
            _pkt(b"dddddddddddddddddddddddddddddddddddddddd refs/tags/v1.2.0^{}\n"),
            _pkt(b"eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee refs/tags/v1.3.0\n"),
            b"0000",
        ]
    )
    refs = parse_upload_pack_refs(blob)
    assert refs.default_branch == "master"
    assert refs.head_sha.startswith("aaaa")
    assert refs.tip_sha("master").startswith("aaaa")
    assert refs.tip_sha("dev").startswith("bbbb")
    assert refs.tip_sha("v1.2.0").startswith("dddd")  # peeled
    assert newest_version_tag(refs.tags) == "v1.3.0"

    ls = parse_ls_remote(
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\tHEAD\n"
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\trefs/heads/master\n"
    )
    assert ls.head_sha.startswith("aaaa")
    assert ls.default_branch == "master"

    atom = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>tag:github.com,2008:Grit::Commit/b2f6df84a93a4ce6adbe1fd8f0372454795151f1</id>
    <link rel="alternate" href="https://github.com/shagu/pfUI/commit/b2f6df84a93a4ce6adbe1fd8f0372454795151f1"/>
  </entry>
</feed>"""
    assert parse_atom_commit_sha(atom) == "b2f6df84a93a4ce6adbe1fd8f0372454795151f1"
    rel = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>v2.0.1</title>
    <link rel="alternate" href="https://github.com/foo/bar/releases/tag/v2.0.1"/>
  </entry>
</feed>"""
    assert parse_atom_release_tag(rel) == "v2.0.1"

    index = normalize_index(
        {
            "generated_at": "2026-08-23T00:00:00Z",
            "repos": {
                "shagu/pfui": repo_entry_from_refs(refs),
            },
        }
    )
    prev = tips._loaded
    try:
        tips._loaded = (0.0, index)
        hit = lookup_tip("shagu", "pfUI")
        assert hit is not None
        assert hit[0].startswith("aaaa")
        assert hit[1] == "master"
        assert lookup_tip("shagu", "pfUI", "dev") is None  # not stored in compact index
        assert lookup_latest_tag("Shagu", "pfUI") == "v1.3.0"
        assert lookup_tip("nope", "missing") is None
    finally:
        tips._loaded = prev
        if prev is None:
            clear_tip_index_cache()

    print("OK git refs and tip index")


def test_mod_catalog_repos_in_tip_index_builder():
    """mods.json GitHub sources are included when building the catalog index."""
    import tools.build_addon_tips as builder

    addon = builder._catalog_repos()
    mod = builder._mod_catalog_repos()
    merged = builder._merge_repos(addon, mod)
    assert len(mod) >= 5
    assert len(merged) >= len(addon)
    keys = {f"{o.lower()}/{n.lower()}" for o, n in merged}
    assert "hannesmann/vanillafixes" in keys
    assert "balakethelock/superwow" in keys
    print("OK mod catalog repos in tip index builder")


def test_mod_remote_identity_uses_tip_index():
    """Client mod release checks prefer the shared tip index over REST."""
    from ichalaunch.addons import tip_index as tips
    from ichalaunch.addons.tip_index import clear_tip_index_cache, normalize_index
    from ichalaunch.mods.installer import _remote_identity

    index = normalize_index(
        {
            "generated_at": "2026-08-23T00:00:00Z",
            "repos": {
                "hannesmann/vanillafixes": {
                    "default_branch": "master",
                    "sha": "a" * 40,
                    "branches": {"master": "a" * 40},
                    "latest_tag": "v9.9.9",
                }
            },
        }
    )
    prev = tips._loaded
    try:
        tips._loaded = (0.0, index)
        ident = _remote_identity(
            {"type": "github_release_latest", "repo": "hannesmann/vanillafixes"}
        )
        assert ident is not None
        assert ident["key"] == "v9.9.9"
        assert ident["tag"] == "v9.9.9"
        catalog = _remote_identity(
            {"type": "github_release_latest", "repo": "hannesmann/vanillafixes"},
            catalog_only=True,
        )
        assert catalog is not None and catalog["key"] == "v9.9.9"
        missing = _remote_identity(
            {"type": "github_release_latest", "repo": "nope/missing"},
            catalog_only=True,
        )
        assert missing is None
    finally:
        tips._loaded = prev
        if prev is None:
            clear_tip_index_cache()
    print("OK mod remote identity uses tip index")


def test_addon_toc_folder_name_required():
    """Disk scan and install roots require folder name == .toc name."""
    import tempfile
    from pathlib import Path

    from ichalaunch.core.detect import _classify_toc_dir
    from ichalaunch.core.filesystem import (
        canonical_addon_folder_name,
        matching_toc_path,
        resolve_install_addon_roots,
    )

    with tempfile.TemporaryDirectory() as tmp:
        addons = Path(tmp) / "AddOns"
        good = addons / "Atlas-CFM"
        good.mkdir(parents=True)
        (good / "Atlas-CFM.toc").write_text("## Title: Atlas-CFM\n", encoding="utf-8")
        bad = addons / "BrokenAddon"
        bad.mkdir()
        (bad / "WrongName.toc").write_text("## Title: Wrong\n", encoding="utf-8")
        extract = Path(tmp) / "Atlas-TW"
        extract.mkdir()
        (extract / "Atlas-CFM.toc").write_text("## Title: Atlas-CFM\n", encoding="utf-8")
        tw = addons / "Atlas-TW"
        tw.mkdir()
        (tw / "Atlas-CFM.toc").write_text("## Title: Atlas-CFM\n", encoding="utf-8")

        assert matching_toc_path(good) is not None
        assert matching_toc_path(bad) is None
        assert matching_toc_path(extract) is None
        assert canonical_addon_folder_name(extract) == "Atlas-CFM"
        valid, mismatched = _classify_toc_dir(addons, skip_blizzard=True)
        assert "Atlas-CFM" in valid
        assert "Atlas-TW" not in valid
        tw_mis = next(m for m in mismatched if m.current_name == "Atlas-TW")
        assert tw_mis.toc_stem == "Atlas-CFM"
        assert tw_mis.toc_name == "Atlas-CFM.toc"
        assert any(m.current_name == "BrokenAddon" for m in mismatched)
        pairs = resolve_install_addon_roots(extract)
        assert pairs == [(extract, "Atlas-CFM")]
    print("OK addon toc folder name required")


def test_addon_toc_folder_rename():
    """Rename helper, decline-as-skip, and dest-exists collision (no Qt)."""
    import tempfile
    from pathlib import Path

    from ichalaunch.config.settings import settings
    from ichalaunch.core.detect import _classify_toc_dir
    from ichalaunch.core.filesystem import (
        clear_pending_toc_mismatches,
        describe_toc_mismatch,
        matching_toc_path,
        place_install_addon_root,
        rename_addon_folder_to_toc,
        toc_mismatch_prompt_text,
    )

    clear_pending_toc_mismatches()
    prev_addons = settings.installed_addons
    try:
        with tempfile.TemporaryDirectory() as tmp:
            addons = Path(tmp) / "AddOns"
            addons.mkdir()

            # Prompt: folder is wrong; .toc stem is the required folder name.
            copy = toc_mismatch_prompt_text("Atlas-TW", "Atlas-CFM.toc")
            assert "Folder is Atlas-TW" in copy
            assert "Atlas-CFM.toc" in copy
            assert "Rename folder to Atlas-CFM" in copy
            assert "folder name to match the .toc" in copy

            # Case-only difference is already a match — no prompt/rename.
            cased = addons / "CaseAddon"
            cased.mkdir()
            (cased / "caseaddon.toc").write_text("## Title: Case\n", encoding="utf-8")
            assert matching_toc_path(cased) is not None
            assert describe_toc_mismatch(cased) is None
            assert rename_addon_folder_to_toc(cased).status == "already_match"

            # Rename folder Atlas-TW → Atlas-CFM; the .toc file keeps its name.
            atlas = addons / "Atlas-TW"
            atlas.mkdir()
            (atlas / "Atlas-CFM.toc").write_text("## Title: Atlas-CFM\n", encoding="utf-8")
            settings.set("installed_addons", {"Atlas-TW": {"name": "Atlas-TW", "source": "github"}})
            mismatch = describe_toc_mismatch(atlas)
            assert mismatch is not None
            assert mismatch.current_name == "Atlas-TW"
            assert mismatch.toc_stem == "Atlas-CFM"
            assert mismatch.toc_name == "Atlas-CFM.toc"
            assert mismatch.can_rename
            outcome = rename_addon_folder_to_toc(atlas, mismatch.toc_stem)
            assert outcome.status == "renamed"
            assert outcome.new_name == "Atlas-CFM"
            dest = addons / "Atlas-CFM"
            assert dest.is_dir()
            assert not (addons / "Atlas-TW").exists()
            assert (dest / "Atlas-CFM.toc").is_file()
            assert not (dest / "Atlas-TW.toc").exists()
            assert matching_toc_path(dest) is not None
            assert "Atlas-CFM" in settings.installed_addons
            assert "Atlas-TW" not in settings.installed_addons
            assert settings.installed_addons["Atlas-CFM"]["source"] == "github"

            # A swapped caller stem (folder name) still destines to the .toc stem.
            swapped = addons / "WrongFolder"
            swapped.mkdir()
            (swapped / "RightName.toc").write_text("## Title: Right\n", encoding="utf-8")
            swapped_out = rename_addon_folder_to_toc(swapped, "WrongFolder")
            assert swapped_out.status == "renamed"
            assert swapped_out.new_name == "RightName"
            assert (addons / "RightName" / "RightName.toc").is_file()
            assert not (addons / "WrongFolder").exists()
            assert not (addons / "RightName" / "WrongFolder.toc").exists()

            # Declining (leaving the folder) keeps it unmatched — not a valid addon.
            leftover = addons / "BrokenAddon"
            leftover.mkdir()
            (leftover / "WrongName.toc").write_text("## Title: Wrong\n", encoding="utf-8")
            valid, mismatched = _classify_toc_dir(addons, skip_blizzard=True)
            assert "Atlas-CFM" in valid
            assert "BrokenAddon" not in valid
            assert any(m.current_name == "BrokenAddon" and m.can_rename for m in mismatched)
            assert leftover.is_dir()
            assert (leftover / "WrongName.toc").is_file()

            # Collision: do not overwrite an existing dest folder.
            collide_src = addons / "OldName"
            collide_src.mkdir()
            (collide_src / "Taken.toc").write_text("## Title: Taken\n", encoding="utf-8")
            taken = addons / "Taken"
            taken.mkdir()
            (taken / "Taken.toc").write_text("## Title: Taken\n", encoding="utf-8")
            (taken / "keep-me.txt").write_text("safe", encoding="utf-8")
            collision = rename_addon_folder_to_toc(collide_src, "Taken")
            assert collision.status == "collision"
            assert collide_src.is_dir()
            assert (collide_src / "Taken.toc").is_file()
            assert (taken / "keep-me.txt").read_text(encoding="utf-8") == "safe"

            # Install dest is the .toc stem even when the extract/catalog name differs.
            extract = Path(tmp) / "extract" / "Atlas-TW"
            extract.mkdir(parents=True)
            (extract / "Atlas-CFM.toc").write_text("## Title: Atlas-CFM\n", encoding="utf-8")
            install_into = Path(tmp) / "InstallAddOns"
            placed, pending = place_install_addon_root(extract, install_into, "Atlas-TW")
            assert placed == "Atlas-CFM"
            assert pending is None
            dest_install = install_into / "Atlas-CFM"
            assert dest_install.is_dir()
            assert not (install_into / "Atlas-TW").exists()
            assert (dest_install / "Atlas-CFM.toc").is_file()
            assert not (dest_install / "Atlas-TW.toc").exists()
            assert matching_toc_path(dest_install) is not None
    finally:
        settings.set("installed_addons", prev_addons)
        clear_pending_toc_mismatches()
    print("OK addon toc folder rename")


def test_addon_update_check_uses_catalog_index_only():
    """Bulk addon checks compare the tip index and never probe GitHub per addon."""
    from ichalaunch.addons import github as gh
    from ichalaunch.addons import tip_index as tips
    from ichalaunch.addons.tip_index import clear_tip_index_cache, normalize_index
    from ichalaunch.config.settings import settings

    index = normalize_index(
        {
            "generated_at": "2026-08-23T00:00:00Z",
            "repos": {
                "shagu/pfui": {
                    "default_branch": "master",
                    "sha": "b" * 40,
                    "branches": {"master": "b" * 40},
                    "latest_tag": "v2.0.0",
                }
            },
        }
    )
    prev_loaded = tips._loaded
    prev_addons = settings.installed_addons
    orig_tip = gh.github_remote_tip
    orig_tag = gh.github_latest_version_tag
    orig_refresh = tips.refresh_tip_index

    def boom(*_a, **_k):
        raise AssertionError("per-addon GitHub probe should not run")

    def fake_refresh(*, force: bool = False):
        return index

    try:
        tips._loaded = (0.0, index)
        tips.refresh_tip_index = fake_refresh
        gh.github_remote_tip = boom
        gh.github_latest_version_tag = boom
        settings.set(
            "installed_addons",
            {
                "pfUI": {
                    "repository": "shagu/pfUI",
                    "url": "https://github.com/shagu/pfUI",
                    "installed_commit": "a" * 40,
                    "branch": "master",
                }
            },
        )
        result = gh.check_addon_updates()
        assert result.queued is False
        assert result.checked_count == 1
        assert len(result.updates) == 1
        assert result.updates[0]["folder"] == "pfUI"
    finally:
        gh.github_remote_tip = orig_tip
        gh.github_latest_version_tag = orig_tag
        tips.refresh_tip_index = orig_refresh
        settings.set("installed_addons", prev_addons)
        tips._loaded = prev_loaded
        if prev_loaded is None:
            clear_tip_index_cache()
    print("OK addon update check uses catalog index only")


def test_git_refs_live_optional():
    """Live upload-pack against a public repo — skip if GitHub is unreachable."""
    from ichalaunch.addons.git_refs import fetch_upload_pack_refs, clear_git_refs_cache
    from ichalaunch.addons.github import github_remote_tip

    clear_git_refs_cache()
    try:
        refs = fetch_upload_pack_refs("shagu", "pfUI", timeout=12)
    except Exception as exc:  # noqa: BLE001
        print(f"SKIP git refs live: {exc}")
        return
    if refs is None or not refs.head_sha:
        print("SKIP git refs live: no advertisement")
        return
    assert len(refs.head_sha) >= 40
    assert refs.default_branch
    tip = github_remote_tip("shagu", "pfUI", refs.default_branch)
    assert str(tip.get("sha") or "") == refs.head_sha
    print(f"OK git refs live ({refs.default_branch} {refs.head_sha[:10]})")


def test_github_token_not_sent_to_third_party_readme_hosts():
    """README image fetches must not attach the GitHub token to foreign or HTTP URLs."""
    from ichalaunch.addons import github as G

    assert G.may_send_github_token("https://api.github.com/repos/o/r") is True
    assert G.may_send_github_token("https://raw.githubusercontent.com/o/r/main/c.png") is True
    assert G.may_send_github_token("https://user-images.githubusercontent.com/1/x.png") is True
    assert G.may_send_github_token("https://objects.githubusercontent.com/x") is True
    assert G.may_send_github_token("https://github.com/o/r/releases/download/v1/a.exe") is True
    assert G.may_send_github_token("http://raw.githubusercontent.com/o/r/main/c.png") is False
    assert G.may_send_github_token("https://third-party.example/a.png") is False
    assert G.may_send_github_token("http://plaintext.example/b.png") is False
    assert G.may_send_github_token("https://evil.github.io/x.png") is False
    assert G.may_send_github_token("https://raw.githubusercontent.com.evil.example/x") is False
    assert G.may_send_github_token("") is False

    prev_token = G.settings.get("github_token")
    orig_get = G.requests.get
    seen: list[tuple[str, str | None]] = []

    class _Resp:
        status_code = 404
        headers = {"Content-Type": "text/plain"}

        def iter_content(self, **kw):
            return iter(())

        def close(self):
            pass

        @property
        def content(self):
            return b""

    def _fake_get(url, headers=None, **kw):
        seen.append((url, (headers or {}).get("Authorization")))
        return _Resp()

    try:
        G.settings.set("github_token", "ghp_TESTTOKEN")
        assert "Authorization" not in G.github_headers("")
        assert "Authorization" not in G.github_headers("https://third-party.example/a.png")
        assert "Authorization" not in G.github_headers("http://api.github.com/repos/o/r")
        assert G.github_headers("https://api.github.com/repos/o/r").get("Authorization") == (
            "Bearer ghp_TESTTOKEN"
        )
        G.requests.get = _fake_get
        with tempfile.TemporaryDirectory() as td:
            G.localize_readme_images(
                "![a](https://third-party.example/a.png)\n"
                "![b](http://plaintext.example/b.png)\n"
                "![c](https://raw.githubusercontent.com/o/r/main/c.png)\n",
                cache_dir=Path(td),
            )
    finally:
        G.requests.get = orig_get
        G.settings.set("github_token", prev_token or "")

    by_host = {url.split("/")[2]: auth for url, auth in seen}
    assert by_host["third-party.example"] is None
    assert by_host["plaintext.example"] is None
    assert by_host["raw.githubusercontent.com"] == "Bearer ghp_TESTTOKEN"
    print("OK github token not sent to third-party README hosts")


def test_github_bad_token_retries_without_auth():
    """Invalid stored tokens must not break public repo API calls."""
    import requests

    from ichalaunch.addons import github as G

    prev_token = G.settings.get("github_token")
    orig_get = G.requests.get
    calls: list[tuple[str, str | None]] = []

    class _Resp:
        def __init__(self, status_code: int, body: str = "{}"):
            self.status_code = status_code
            self.headers = {"Content-Type": "application/json"}
            self.text = body
            self._body = body

        def json(self):
            return json.loads(self._body)

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError(f"{self.status_code}", response=self)

        def close(self):
            pass

    def _fake_get(url, headers=None, **kw):
        auth = (headers or {}).get("Authorization")
        calls.append((url, auth))
        if auth:
            return _Resp(401)
        return _Resp(
            200,
            '{"tag_name":"1.0.0","assets":[]}',
        )

    try:
        G.settings.set("github_token", "ghp_invalid_token")
        G._token_rejected_pending = False
        G.requests.get = _fake_get
        r = G.github_get("https://api.github.com/repos/hannesmann/vanillafixes/releases/latest")
        assert r.status_code == 200
        assert len(calls) == 2
        assert calls[0][1] == "Bearer ghp_invalid_token"
        assert calls[1][1] is None
        assert G.take_github_token_warning() == G.GITHUB_TOKEN_REJECTED_MSG
    finally:
        G.requests.get = orig_get
        G.settings.set("github_token", prev_token or "")
        G._token_rejected_pending = False

    print("OK github bad token retries without auth")


def test_auto_scan_cooldown_setting():
    from ichalaunch.config.settings import AUTO_SCAN_COOLDOWN_MINUTES, AUTO_SCAN_COOLDOWN_SEC, settings
    from ichalaunch.core.self_update import LAUNCHER_RELEASE_CACHE_SEC
    from ichalaunch.ui import main_window as mw

    assert AUTO_SCAN_COOLDOWN_MINUTES == 15
    assert AUTO_SCAN_COOLDOWN_SEC == 15 * 60
    assert settings.auto_scan_cooldown_minutes() == 15
    assert settings.auto_scan_cooldown_sec() == 15 * 60
    assert mw._PERIODIC_UPDATE_MS == 15 * 60 * 1000
    assert LAUNCHER_RELEASE_CACHE_SEC == 15 * 60
    print("OK auto scan cooldown is hardcoded 15 min")


def test_auto_scan_cooldown_persists_to_disk():
    """Settings page no longer exposes a cooldown slider; interval stays 15 min."""
    import sys

    from PySide6.QtWidgets import QApplication

    import ichalaunch.ui.pages.settings as settings_page_mod
    from ichalaunch.config.settings import settings

    app = QApplication.instance() or QApplication(sys.argv)
    page = settings_page_mod.SettingsPage()
    assert not hasattr(page, "cooldown_slider")
    assert settings.auto_scan_cooldown_minutes() == 15
    print("OK auto scan cooldown has no settings slider")


def test_addon_startup_token_gating():
    """Addon startup scans require token or explicit addon opt-in; migration clears legacy."""
    from ichalaunch.config.settings import (
        Settings,
        migrate_addon_no_token_startup,
        settings,
    )

    # Migration: no token → disable addon startup flag once
    legacy = {
        "check_updates_on_startup": True,
        "check_addon_updates_on_startup": True,
        "github_token": "",
    }
    assert migrate_addon_no_token_startup(legacy) is True
    assert legacy["check_addon_updates_on_startup"] is False
    assert legacy["addon_no_token_startup_migrated_v1"] is True
    assert migrate_addon_no_token_startup(legacy) is False

    with_token = {
        "check_addon_updates_on_startup": True,
        "github_token": "ghp_test",
    }
    assert migrate_addon_no_token_startup(with_token) is False
    assert with_token["check_addon_updates_on_startup"] is True

    # Startup gate follows the unified checkbox — token is no longer required.
    s = Settings()
    s._data["check_updates_on_startup"] = True
    s._data["check_addon_updates_on_startup"] = False
    assert s.should_startup_check_addons(has_token=True) is True
    assert s.should_startup_check_addons(has_token=False) is True

    s._data["check_updates_on_startup"] = False
    s._data["check_addon_updates_on_startup"] = True
    assert s.should_startup_check_addons(has_token=False) is False
    assert s.should_startup_check_addons(has_token=True) is False

    prev = {
        "check_updates_on_startup": settings.check_updates_on_startup(),
        "check_mod_updates_on_startup": settings.check_mod_updates_on_startup(),
        "check_addon_updates_on_startup": settings.check_addon_updates_on_startup(),
    }
    try:
        settings.set_check_updates_on_startup(True)
        assert settings.check_updates_on_startup() is True
        assert settings.check_mod_updates_on_startup() is True
        assert settings.check_addon_updates_on_startup() is True
        settings.set_check_updates_on_startup(False)
        assert settings.check_addon_updates_on_startup() is False
    finally:
        settings._data.update(prev)
        settings.save()

    print("OK addon startup token gating")


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


def test_install_clears_readonly_data_mpqs():
    """Data/ paths clear read-only on install; root WoW.exe, DLLs, and dlls.txt stay untouched."""
    import os
    import stat

    from ichalaunch.core.filesystem import copy_file_tolerant, ensure_data_writable, update_dlls_txt
    from ichalaunch.mods.installer import _install_copy

    with tempfile.TemporaryDirectory() as td:
        game = Path(td)
        data = game / "Data"
        data.mkdir()
        src_mpq = data / "patch-src.mpq"
        dest_mpq = data / "patch-A.mpq"
        src_mpq.write_bytes(b"mpq-bytes")
        dest_mpq.write_bytes(b"old")
        os.chmod(dest_mpq, stat.S_IREAD)
        assert not (dest_mpq.stat().st_mode & stat.S_IWRITE)

        _install_copy(src_mpq, dest_mpq, game_path=game)
        assert dest_mpq.read_bytes() == b"mpq-bytes"
        assert dest_mpq.stat().st_mode & stat.S_IWRITE

        glue = data / "Interface" / "GlueXML"
        glue.mkdir(parents=True)
        glue_src = glue / "AutoLogin-src.lua"
        glue_dest = glue / "AutoLogin.lua"
        glue_src.write_text("-- lua", encoding="utf-8")
        glue_dest.write_text("-- old", encoding="utf-8")
        os.chmod(glue_dest, stat.S_IREAD)
        _install_copy(glue_src, glue_dest, game_path=game)
        assert glue_dest.stat().st_mode & stat.S_IWRITE

        src_dll = game / "nampower-src.dll"
        dest_dll = game / "nampower.dll"
        src_dll.write_bytes(b"dll")
        dest_dll.write_bytes(b"old")
        os.chmod(dest_dll, stat.S_IREAD)
        copy_file_tolerant(src_dll, dest_dll)  # read-only dest may block overwrite on Windows
        ensure_data_writable(dest_dll, game)
        assert not (dest_dll.stat().st_mode & stat.S_IWRITE)

        wow_src = game / "WoW-src.exe"
        wow = game / "WoW.exe"
        wow_src.write_bytes(b"exe")
        wow.write_bytes(b"old")
        os.chmod(wow, stat.S_IREAD)
        try:
            _install_copy(wow_src, wow, game_path=game)
        except OSError:
            pass
        ensure_data_writable(wow, game)
        assert not (wow.stat().st_mode & stat.S_IWRITE)

        dlls = game / "dlls.txt"
        dlls.write_text("# old\nold.dll\n", encoding="utf-8")
        os.chmod(dlls, stat.S_IREAD)
        update_dlls_txt(game, add=["nampower.dll"])
        assert not (dlls.stat().st_mode & stat.S_IWRITE)

        outside = game / "outside.txt"
        outside.write_text("x", encoding="utf-8")
        os.chmod(outside, stat.S_IREAD)
        ensure_data_writable(outside, game)
        assert not (outside.stat().st_mode & stat.S_IWRITE)

        ensure_data_writable(game / "missing-file.bin", game)  # must not raise
    print("OK install clears readonly Data files only")


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


def test_vanillafixes_preserves_dlls_txt():
    """Installing/updating VanillaFixes must not replace the user's dlls.txt."""
    import tempfile

    from ichalaunch.config.settings import settings as s
    from ichalaunch.mods.installer import install_mod

    keys = ("desired_mods", "user_set_mods", "installed_mods", "user_mods", "game_path", "addons_path")
    saved = {k: s.get(k) for k in keys}
    try:
        with tempfile.TemporaryDirectory() as td:
            game = Path(td)
            (game / "WoW.exe").write_bytes(b"MZ")
            preserved = (
                "UnitXP_SP3.dll\nVanillaHelpers.dll\n# manual keep\nCustomMod.dll\n"
            )
            (game / "dlls.txt").write_text(preserved, encoding="utf-8")
            s.set("game_path", str(game))
            s.set("addons_path", "")
            install_mod("vanillafixes")
            text = (game / "dlls.txt").read_text(encoding="utf-8")
            assert "UnitXP_SP3.dll" in text, text
            assert "VanillaHelpers.dll" in text, text
            assert "CustomMod.dll" in text, text
            assert "# manual keep" in text, text
            assert (game / "VanillaFixes.exe").is_file()
    finally:
        for k in keys:
            s.set(k, saved[k])
    print("OK vanillafixes preserves dlls.txt")


def test_apply_desired_state_restores_dlls_txt():
    """Apply after a template overwrite should re-add DLLs for desired mods."""
    import tempfile

    from ichalaunch.config.settings import settings as s
    from ichalaunch.core.filesystem import clear_fs_caches
    from ichalaunch.mods.installer import apply_desired_state

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
            (game / "nampower.dll").write_bytes(b"MZ")
            (game / "dlls.txt").write_text("nampower.dll\n", encoding="utf-8")
            s.set("game_path", str(game))
            s.set("addons_path", "")
            s.set("desired_mods", {"nampower": True})
            s.set("user_set_mods", ["nampower"])
            clear_fs_caches()
            # Simulate VanillaFixes zip shipping a bare template without nampower.
            (game / "dlls.txt").write_text(
                "# template\nSuperWoWhook.dll\n", encoding="utf-8"
            )
            out = apply_desired_state()
            assert "nampower.dll" in (game / "dlls.txt").read_text(encoding="utf-8"), out
    finally:
        for k in keys:
            s.set(k, saved[k])
        clear_fs_caches()
    print("OK apply desired state restores dlls.txt")


def test_prepare_for_launch_syncs_dlls_txt():
    """Pre-launch should add missing and remove stale catalog DLL lines."""
    import tempfile

    from ichalaunch.config.settings import settings as s
    from ichalaunch.core.filesystem import clear_fs_caches
    from ichalaunch.mods.installer import prepare_for_launch

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
            (game / "nampower.dll").write_bytes(b"MZ")
            (game / "dlls.txt").write_text(
                "SuperWoWhook.dll\n# manual keep\nCustomMod.dll\n", encoding="utf-8"
            )
            s.set("game_path", str(game))
            s.set("addons_path", "")
            s.set("desired_mods", {"nampower": True, "superwow": False})
            s.set("user_set_mods", ["nampower"])
            clear_fs_caches()
            result = prepare_for_launch(game)
            text = (game / "dlls.txt").read_text(encoding="utf-8")
            assert "nampower.dll" in text, text
            assert "SuperWoWhook.dll" not in text, text
            assert "CustomMod.dll" in text, text
            assert any("nampower.dll" in f for f in result.fixes), result.fixes
            assert any("SuperWoWhook.dll" in f for f in result.fixes), result.fixes
    finally:
        for k in keys:
            s.set(k, saved[k])
        clear_fs_caches()
    print("OK prepare_for_launch syncs dlls.txt")


def test_prepare_for_launch_clears_data_readonly():
    """Pre-launch should retroactively clear read-only on enabled Data/ mod files."""
    import os
    import stat
    import tempfile

    from ichalaunch.config.settings import settings as s
    from ichalaunch.core.filesystem import clear_fs_caches
    from ichalaunch.mods.installer import prepare_for_launch

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
            mpq = game / "Data" / "patch-A.mpq"
            mpq.parent.mkdir(parents=True)
            mpq.write_bytes(b"mpq")
            os.chmod(mpq, stat.S_IREAD)
            assert not (mpq.stat().st_mode & stat.S_IWRITE)
            s.set("game_path", str(game))
            s.set("addons_path", "")
            s.set("desired_mods", {"hd_patch_a": True, "vanilla_helpers": True})
            s.set("user_set_mods", ["hd_patch_a", "vanilla_helpers"])
            clear_fs_caches()
            result = prepare_for_launch(game)
            assert mpq.stat().st_mode & stat.S_IWRITE
            assert any("patch-a.mpq" in f.lower() for f in result.fixes), result.fixes
    finally:
        for k in keys:
            s.set(k, saved[k])
        clear_fs_caches()
    print("OK prepare_for_launch clears Data read-only")


def test_plan_missing_installs_dxvk():
    """Desired DXVK with missing VanillaFixes.exe should plan a reinstall before launch."""
    from ichalaunch.config.settings import settings as s
    from ichalaunch.core.filesystem import clear_fs_caches
    from ichalaunch.mods.installer import (
        detect_actual_state,
        plan_changes,
        plan_missing_installs,
    )

    keys = (
        "game_path",
        "addons_path",
        "desired_mods",
        "user_set_mods",
        "installed_mods",
        "vanillafixes_enabled",
    )
    saved = {k: s.get(k) for k in keys}
    try:
        with tempfile.TemporaryDirectory() as td:
            game = Path(td)
            (game / "WoW.exe").write_bytes(b"MZ")
            s.set("game_path", str(game))
            s.set("addons_path", "")
            s.set("desired_mods", {"dxvk": True, "vanillafixes": False})
            s.set("user_set_mods", ["dxvk"])
            s.set("vanillafixes_enabled", True)
            clear_fs_caches()

            actual = detect_actual_state(game)
            assert not actual.get("dxvk"), actual
            missing = plan_missing_installs()
            assert any(ch.get("id") == "dxvk" for ch in missing), missing
            assert not any(ch.get("action") == "remove" for ch in plan_changes()), plan_changes()

            # Partial DXVK files still count as missing — repair should reinstall.
            (game / "d3d9.dll").write_bytes(b"MZ")
            (game / "dxvk.conf").write_text("d3d9.enlargeHardwareCursor = 2\n", encoding="utf-8")
            clear_fs_caches()
            actual2 = detect_actual_state(game)
            assert not actual2.get("dxvk"), actual2
            missing2 = plan_missing_installs()
            assert any(ch.get("id") == "dxvk" for ch in missing2), missing2
    finally:
        for k in keys:
            s.set(k, saved[k])
        clear_fs_caches()
    print("OK plan_missing_installs dxvk")


def test_play_prep_plans_remove():
    """Disabled mod with file on disk should plan remove before PLAY sync."""
    from ichalaunch.config.settings import settings as s
    from ichalaunch.core.filesystem import clear_fs_caches
    from ichalaunch.mods.installer import (
        ensure_desired_mods_synced,
        plan_sync_changes,
    )

    keys = (
        "desired_mods",
        "user_set_mods",
        "installed_mods",
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
            s.set("desired_mods", {"hd_patch_n": False})
            s.set("user_set_mods", ["hd_patch_n"])
            s.set("installed_mods", {})
            clear_fs_caches()

            sync = plan_sync_changes()
            assert any(
                ch["action"] == "remove" and ch["id"] == "hd_patch_n" for ch in sync
            ), sync

            out = ensure_desired_mods_synced()
            assert "- hd_patch_n" in out, out
            assert not mpq.exists()
            assert plan_sync_changes() == [], plan_sync_changes()
    finally:
        for k in keys:
            s.set(k, saved[k])
        clear_fs_caches()
    print("OK play prep plans remove")


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


def test_settle_existing_alphanumeric_folder():
    """Regression: picking an existing WoW home must not delete it during settle."""
    from ichalaunch.game import client_install as ci

    with tempfile.TemporaryDirectory() as td:
        picked = Path(td) / "RavenCraftClient"
        picked.mkdir()
        (picked / "Interface" / "AddOns").mkdir(parents=True)
        (picked / "Data").mkdir()
        (picked / "WoW.exe").write_bytes(b"MZ" + b"\0" * 200)
        (picked / "Data" / "patch.MPQ").write_bytes(b"MPQ\x1a" + b"\0" * 500)
        before = len(list(picked.rglob("*")))

        assert ci._is_wrapper_name(picked.name) is True
        assert ci.should_settle_existing(picked, picked) is False

        try:
            ci.settle_ravencraft_home(picked, picked)
        except Exception:
            pass

        after = len(list(picked.rglob("*"))) if picked.exists() else 0
        assert after == before, f"game directory destroyed: before={before} after={after}"
        assert (picked / "WoW.exe").is_file()
    print("OK settle existing alphanumeric folder")


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


def test_game_permissions_scan_and_fix():
    """Scan/fix detects read-only Data/ and restores write access; WoW.exe is ignored."""
    import os
    import stat

    from ichalaunch.core.filesystem import (
        fix_game_permissions,
        iter_game_permission_targets,
        scan_game_permissions,
    )

    with tempfile.TemporaryDirectory() as td:
        game = Path(td) / "RavenCraft"
        game.mkdir()
        (game / "WoW.exe").write_bytes(b"MZ")
        for name in ("Data", "WTF", "Interface"):
            (game / name).mkdir()
        targets = iter_game_permission_targets(game)
        assert (game / "WoW.exe") not in targets
        assert game in targets
        assert game / "Data" in targets

        scan = scan_game_permissions(game)
        assert not scan.has_issues, scan.issues

        data = game / "Data"
        os.chmod(data, stat.S_IREAD)
        scan = scan_game_permissions(game)
        assert scan.has_issues
        assert any(i.kind == "readonly" and i.rel == "Data" for i in scan.issues), scan.issues

        # Read-only WoW.exe must not trigger permission warnings.
        os.chmod(data, stat.S_IWRITE)
        wow = game / "WoW.exe"
        os.chmod(wow, stat.S_IREAD)
        scan_wow = scan_game_permissions(game)
        assert not scan_wow.has_issues, scan_wow.issues

        os.chmod(data, stat.S_IREAD)
        fix = fix_game_permissions(game)
        assert fix.fixes
        assert data.stat().st_mode & stat.S_IWRITE
        scan2 = scan_game_permissions(game)
        assert not scan2.has_issues, scan2.issues
    print("OK game permissions scan/fix")


def test_game_permissions_protected_path():
    """Protected locations skip auto-fix and advise moving the folder."""
    import os
    import stat

    from ichalaunch.core.filesystem import (
        fix_game_permissions,
        scan_game_permissions,
    )

    with tempfile.TemporaryDirectory() as td:
        # Path segment contains "downloads" (is_protected_path substring match).
        game = Path(td) / "my_downloads_backup" / "RavenCraft"
        game.mkdir(parents=True)
        (game / "WoW.exe").write_bytes(b"MZ")
        (game / "Data").mkdir()
        os.chmod(game / "Data", stat.S_IREAD)

        scan = scan_game_permissions(game)
        assert scan.protected_path
        assert scan.has_issues
        assert not scan.can_auto_fix
        assert "Move the entire game folder" in scan.user_message()
        assert not scan.needs_elevation

        fix = fix_game_permissions(game)
        assert not fix.fixes
        assert any("restricted location" in w.lower() for w in fix.warnings)
    print("OK game permissions protected path")


def test_settings_paths_survive_load_cycle():
    """game_path and addons_path must survive load → migration → save → reload."""
    import ichalaunch.config.settings as settings_mod
    from ichalaunch.config.settings import Settings, migrate_addon_no_token_startup

    with tempfile.TemporaryDirectory() as td:
        fake = Path(td) / "settings.json"
        orig_path = settings_mod.settings_path
        settings_mod.settings_path = lambda: fake
        try:
            fake.write_text(
                json.dumps(
                    {
                        "game_path": r"D:\Games\RavenCraft",
                        "addons_path": r"E:\Custom\AddOns",
                        "check_addon_updates_on_startup": True,
                        "github_token": "",
                        "desired_mods": {"darker_nights": True},
                        "user_set_mods": ["darker_nights"],
                    }
                ),
                encoding="utf-8",
            )
            s = Settings()
            assert s.game_path.replace("\\", "/") == "D:/Games/RavenCraft"
            assert s.addons_path.replace("\\", "/") == "E:/Custom/AddOns"
            assert s.desired_mods.get("hd_patch_n") is True

            # Simulate a routine settings write (e.g. desired_mods reconcile).
            s.set("desired_mods", dict(s.desired_mods))
            assert migrate_addon_no_token_startup(s._data) is False

            reloaded = Settings()
            assert reloaded.game_path.replace("\\", "/") == "D:/Games/RavenCraft"
            assert reloaded.addons_path.replace("\\", "/") == "E:/Custom/AddOns"

            # Accidental empty writes must not wipe saved paths.
            reloaded.set("game_path", "")
            reloaded.game_path = ""
            assert reloaded.game_path.replace("\\", "/") == "D:/Games/RavenCraft"
        finally:
            settings_mod.settings_path = orig_path
    print("OK settings paths survive load cycle")


def test_settings_paths_recover_from_backup():
    """Corrupt settings.json should fall back to the last good .bak copy."""
    import ichalaunch.config.settings as settings_mod
    from ichalaunch.config.settings import Settings, _settings_backup_path

    with tempfile.TemporaryDirectory() as td:
        fake = Path(td) / "settings.json"
        bak = _settings_backup_path(fake)
        orig_path = settings_mod.settings_path
        settings_mod.settings_path = lambda: fake
        try:
            good = {
                "game_path": r"D:\Games\Saved",
                "addons_path": r"D:\Games\Saved\Interface\AddOns",
            }
            fake.write_text(json.dumps(good), encoding="utf-8")
            s = Settings()
            s.save()
            assert bak.is_file()
            fake.write_text("{not valid json", encoding="utf-8")

            recovered = Settings()
            assert recovered.game_path.replace("\\", "/") == "D:/Games/Saved"
            assert "Saved" in recovered.addons_path
        finally:
            settings_mod.settings_path = orig_path
    print("OK settings paths recover from backup")


def test_launcher_release_cache():
    from ichalaunch.config.settings import settings
    from ichalaunch.core.self_update import (
        LauncherReleaseInfo,
        check_latest_launcher_release,
        launcher_release_info_from_dict,
        launcher_release_info_to_dict,
        read_cached_launcher_release,
        store_cached_launcher_release,
    )
    from inspect import signature

    assert "progress" in signature(check_latest_launcher_release).parameters

    info = LauncherReleaseInfo(
        tag="v9.9.9",
        version="9.9.9",
        name="Test",
        asset_name="IchaLaunch.exe",
        download_url="https://example.com/IchaLaunch.exe",
        update_available=True,
    )
    restored = launcher_release_info_from_dict(launcher_release_info_to_dict(info))
    assert restored is not None and restored.version == "9.9.9"

    old_ts = settings.get("last_launcher_release_check")
    old_cache = settings.get("cached_launcher_release")
    try:
        store_cached_launcher_release(info)
        hit = read_cached_launcher_release(max_age_sec=3600, local_version="1.0.0")
        assert hit is not None and hit.update_available
    finally:
        settings._data["last_launcher_release_check"] = old_ts
        settings._data["cached_launcher_release"] = old_cache
        settings.save()
    print("OK launcher release cache")


def test_dll_injection_mod_detection():
    from ichalaunch.mods.client_mod_hints import is_dll_injection_mod

    assert is_dll_injection_mod({"kind": "dll_file", "dlls_txt": {"add": ["x.dll"]}})
    assert is_dll_injection_mod({"kind": "dll_bundle"})
    assert is_dll_injection_mod({"kind": "dxvk_cursor"})
    assert is_dll_injection_mod({"kind": "mpq_file", "dlls_txt": {"add": ["hook.dll"]}})
    assert not is_dll_injection_mod({"kind": "mpq_file"})
    assert not is_dll_injection_mod({"kind": "exe_patch"})
    assert not is_dll_injection_mod(None)
    print("OK dll injection mod detection")


def test_superwow_issue_detection():
    import tempfile

    from ichalaunch.config.settings import settings as s
    from ichalaunch.core.filesystem import clear_fs_caches
    from ichalaunch.mods.superwow_support import detect_superwow_issues

    keys = ("desired_mods", "game_path", "addons_path")
    saved = {k: s.get(k) for k in keys}
    try:
        with tempfile.TemporaryDirectory() as td:
            game = Path(td)
            (game / "WoW.exe").write_bytes(b"MZ")
            addons = game / "Interface" / "AddOns"
            addons.mkdir(parents=True)
            (addons / "SuperAPI").mkdir()
            (game / "SuperWoWhook.dll").write_bytes(b"MZ" + b"\0" * 64)
            (game / "dlls.txt").write_text("SuperWoWhook.dll\n", encoding="utf-8")
            s.set("game_path", str(game))
            s.set("addons_path", "")
            s.set("desired_mods", {"superwow": False})
            clear_fs_caches()
            issues = detect_superwow_issues(game)
            codes = {i.code for i in issues}
            assert "stale_hook" in codes
            assert "stale_superapi" in codes
            assert "stale_dlls_txt" in codes

            s.set("desired_mods", {"superwow": True})
            (game / "SuperWoWhook.dll").write_bytes(b"xx")
            clear_fs_caches()
            issues = detect_superwow_issues(game)
            assert any(i.code == "corrupt_hook" for i in issues)
    finally:
        for k in keys:
            s.set(k, saved[k])
        clear_fs_caches()
    print("OK superwow issue detection")


def test_themed_dialog_flags_and_close():
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QWidget

    from ichalaunch.ui.widgets.dialogs import (
        ThemedDialog,
        _themed_dialog_flags,
        close_open_themed_dialogs,
    )

    app = QApplication.instance() or QApplication([])
    root = QWidget()
    flags = _themed_dialog_flags()
    assert not (int(flags) & int(Qt.WindowType.WindowStaysOnTopHint))
    dlg = ThemedDialog(root, "Test", "Body")
    assert not (int(dlg.windowFlags()) & int(Qt.WindowType.WindowStaysOnTopHint))
    dlg.show()
    assert dlg.isVisible()
    close_open_themed_dialogs(root)
    assert not dlg.isVisible()
    print("OK themed dialog flags and close")


def test_dll_security_dialog_dont_show_again_is_themed_checkbox():
    """Don't show this again must be ThemeCheckBox — QCheckBox indicator is invisible."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QWidget

    from ichalaunch.ui.widgets.dialogs import DllSecurityExclusionDialog
    from ichalaunch.ui.widgets.theme_checkbox import ThemeCheckBox

    app = QApplication.instance() or QApplication([])
    root = QWidget()
    dlg = DllSecurityExclusionDialog(root, "Add game folder to Windows Security", "Body")
    cb = dlg._dont_show
    assert isinstance(cb, ThemeCheckBox)
    assert cb.isEnabled()
    assert cb.isCheckable()
    assert cb.cursor().shape() == Qt.CursorShape.PointingHandCursor
    assert not dlg.dismissed_permanently()
    cb.click()
    assert dlg.dismissed_permanently()
    print("OK dll security dialog don't-show-again is themed checkbox")


def test_mpq_patch_warning_dialog_and_persist():
    """HD / patch-*.mpq enable warning is themed; Don't show again persists."""
    import json
    import tempfile
    from pathlib import Path

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QWidget

    from ichalaunch.mods.client_mod_hints import (
        MPQ_PATCH_WARNING_TEXT,
        is_mpq_patch_mod,
        should_show_mpq_patch_warning,
    )
    from ichalaunch.mods.installer import get_mod
    from ichalaunch.ui.widgets.dialogs import MpqPatchWarningDialog
    from ichalaunch.ui.widgets.theme_checkbox import ThemeCheckBox

    app = QApplication.instance() or QApplication([])
    assert is_mpq_patch_mod(get_mod("hd_patch_l"))
    assert is_mpq_patch_mod(get_mod("hd_patch_t"))
    assert is_mpq_patch_mod(get_mod("raid_visuals"))
    assert is_mpq_patch_mod(get_mod("pretty_night_sky"))
    assert not is_mpq_patch_mod(get_mod("vanillafixes"))
    assert should_show_mpq_patch_warning(get_mod("hd_patch_n"), enabled=True, dismissed=False)
    assert not should_show_mpq_patch_warning(get_mod("hd_patch_n"), enabled=False, dismissed=False)
    assert not should_show_mpq_patch_warning(get_mod("hd_patch_n"), enabled=True, dismissed=True)

    root = QWidget()
    dlg = MpqPatchWarningDialog(root, "MPQ patch warning", MPQ_PATCH_WARNING_TEXT)
    assert isinstance(dlg._dont_show, ThemeCheckBox)
    assert "Don't show again" in dlg._dont_show.text()
    assert dlg._dont_show.cursor().shape() == Qt.CursorShape.PointingHandCursor
    assert not dlg.dismissed_permanently()
    dlg._dont_show.click()
    assert dlg.dismissed_permanently()

    import ichalaunch.config.settings as settings_mod
    from ichalaunch.config.settings import Settings

    with tempfile.TemporaryDirectory() as td:
        fake = Path(td) / "settings.json"
        orig_path = settings_mod.settings_path
        orig_singleton = settings_mod.settings
        settings_mod.settings_path = lambda: fake
        try:
            settings_mod.settings = Settings()
            settings_mod.settings.set("dismissed_mpq_patch_warning", True)
            assert json.loads(fake.read_text(encoding="utf-8"))["dismissed_mpq_patch_warning"] is True
            settings_mod.settings = Settings()
            assert settings_mod.settings.get("dismissed_mpq_patch_warning") is True
            assert not should_show_mpq_patch_warning(
                get_mod("raid_visuals"),
                enabled=True,
                dismissed=bool(settings_mod.settings.get("dismissed_mpq_patch_warning")),
            )
        finally:
            settings_mod.settings_path = orig_path
            settings_mod.settings = orig_singleton
    print("OK mpq patch warning dialog and persist")


def test_update_launch_button_is_square_and_pulses():
    from PySide6.QtWidgets import QApplication, QWidget

    from ichalaunch.ui.widgets import launch_button
    from ichalaunch.ui.widgets.launch_button import LaunchButton, UpdateLaunchButton

    app = QApplication.instance() or QApplication([])
    arrow = launch_button._up_stream_arrow()
    assert not arrow.isNull(), "UI-MicroStream-Yellow up-arrow failed to load"
    glow = launch_button._check_button_glow()
    assert not glow.isNull(), "CheckButtonGlow failed to load"
    gc = glow.toImage()
    # Pad-only crop: chamfered corners stay soft (not an alpha≥140 box).
    for x, y in ((0, 0), (gc.width() - 1, 0), (0, gc.height() - 1), (gc.width() - 1, gc.height() - 1)):
        assert gc.pixelColor(x, y).alpha() < 40, f"boxy glow corner at {x},{y}"
    play = LaunchButton("PLAY")
    assert play.size().width() == 200
    assert play.size().height() == 56
    host = QWidget()
    btn = UpdateLaunchButton(host)
    assert btn.size().width() == btn.size().height()
    # Inner hole is 32px of a 46px halo → ~80px when the hole matches the 56 plate.
    assert 72 <= btn.size().width() <= 84
    assert not btn._glow.isNull()
    assert btn._glow.width() == btn.size().width()
    chrome = btn._chrome_rect()
    assert chrome.width() == 56
    assert chrome.height() == 56
    pad = chrome.x()
    assert pad >= 8
    # Bright ring sits just outside the plate (covered when scaled to 62).
    ring = gc.pixelColor(gc.width() // 2, max(0, pad - 2))
    assert ring.alpha() >= 80, f"glow ring missing outside plate (alpha={ring.alpha()})"
    assert btn.isHidden()
    btn.set_pending(True)
    assert not btn.isHidden()
    assert btn._pulse_timer.isActive()
    btn.set_pending(False)
    assert btn.isHidden()
    assert not btn._pulse_timer.isActive()
    print("OK update launch button is square and pulses")


def test_launch_button_down_plate_is_click_only():
    """PLAY / REGISTER / UPDATE use Down chrome only while pressed, not on hover."""
    from PySide6.QtWidgets import QApplication

    from ichalaunch.ui.widgets.launch_button import LaunchButton, UpdateLaunchButton

    app = QApplication.instance() or QApplication([])
    play = LaunchButton("PLAY")
    reg = LaunchButton("REGISTER HERE")
    upd = UpdateLaunchButton()
    for btn in (play, reg, upd):
        assert not hasattr(btn, "_paint_gold_border")
        idle = btn._pick_chrome()
        assert idle is btn._chrome
        btn.setDown(True)
        assert btn._pick_chrome() is btn._chrome_pressed
        btn.setDown(False)
        assert btn._pick_chrome() is btn._chrome
    print("OK launch button Down plate is click-only")


def test_worker_survives_ref_drop_in_result_slot():
    """Dropping the only named ref inside done/fail must not destroy a live QThread.

    Regression: startup update-check slots set ``self._*_worker = None`` while
    the Worker thread could still be unwinding run(). When that attribute held
    the last Python reference, the C++ QThread was destroyed mid-run — a Qt
    fatal error that killed the process with no traceback (users saw the app
    close 1-2s after opening). MainWindow._track_worker must keep each worker
    alive until the thread has really finished.
    """
    from PySide6.QtCore import QCoreApplication, QDeadlineTimer
    from PySide6.QtWidgets import QApplication

    from ichalaunch.ui.main_window import MainWindow, Worker

    app = QApplication.instance() or QApplication([])

    class Harness:
        def __init__(self):
            self._live_workers: set = set()

        _track_worker = MainWindow._track_worker
        _release_worker = MainWindow._release_worker

    harness = Harness()

    def _boom(progress=None):
        raise RuntimeError("simulated GitHub failure")

    def _fine(progress=None):
        return "ok"

    for fn in (_boom, _fine, _boom, _fine):
        holder = {}

        def _drop(_arg=None):
            # Mimics ``self._launcher_update_worker = None`` in done/fail.
            holder.clear()

        worker = Worker(fn)
        worker.failed.connect(_drop)
        worker.finished_ok.connect(_drop)
        holder["w"] = worker
        harness._track_worker(worker)
        worker.start()
        deadline = QDeadlineTimer(5000)
        while harness._live_workers and not deadline.hasExpired():
            app.processEvents()
        assert not holder, "result slot should have dropped its reference"
        assert not harness._live_workers, "tracker should release finished workers"

    print("OK worker survives ref drop in result slot")


def test_main_worker_ref_cleared_after_release():
    """Regression: _release_worker must clear self._worker before deleteLater.

    v1.2.2 kept workers alive via _live_workers but left self._worker pointing
    at the freed C++ object after the first _busy job, so the next install hit
    RuntimeError in _busy / _periodic_update_check.
    """
    from PySide6.QtCore import QDeadlineTimer
    from PySide6.QtWidgets import QApplication

    from ichalaunch.ui.main_window import MainWindow, Worker, _safe_worker_running

    app = QApplication.instance() or QApplication([])

    class Harness:
        def __init__(self):
            self._worker = None
            self._live_workers: set = set()

        _track_worker = MainWindow._track_worker
        _release_worker = MainWindow._release_worker
        _worker_busy = MainWindow._worker_busy

    harness = Harness()

    def _work(progress=None):
        return "ok"

    for _ in range(2):
        worker = Worker(_work)
        harness._worker = worker
        harness._track_worker(worker)
        worker.start()
        deadline = QDeadlineTimer(5000)
        while harness._live_workers and not deadline.hasExpired():
            app.processEvents()
        assert harness._worker is None, "main worker ref must clear on release"
        assert not harness._worker_busy(), "released worker must not read as busy"
        assert not _safe_worker_running(worker), "deleted worker must not read as running"

    print("OK main worker ref cleared after release")


def test_auto_update_sequence_is_launcher_addons_client():
    """Startup/periodic auto scans run launcher → addons → client, one at a time."""
    import time

    from PySide6.QtCore import QDeadlineTimer
    from PySide6.QtWidgets import QApplication

    from ichalaunch.config.settings import settings
    from ichalaunch.ui.main_window import (
        MainWindow,
        Worker,
        _AUTO_UPDATE_STEPS,
        _call_when_worker_idle,
    )

    assert _AUTO_UPDATE_STEPS == ("launcher", "addons", "client")

    app = QApplication.instance() or QApplication([])

    immediate = []
    _call_when_worker_idle(None, lambda: immediate.append("now"))
    assert immediate == ["now"]

    later = []

    def _slow(progress=None):
        time.sleep(0.04)
        return "ok"

    worker = Worker(_slow)
    worker.start()
    _call_when_worker_idle(worker, lambda: later.append("after"))
    assert later == [], "callback must wait for a running worker"
    deadline = QDeadlineTimer(5000)
    while not later and not deadline.hasExpired():
        app.processEvents()
    assert later == ["after"]

    order: list[str] = []
    running: list[str] = []

    class Harness:
        def __init__(self):
            self._auto_update_seq_active = False
            self._auto_update_seq_catalogs = False
            self._auto_update_seq_periodic = False
            self._launcher_update_worker = None
            self._update_worker = None
            self._mod_update_worker = None

        _start_auto_update_sequence = MainWindow._start_auto_update_sequence
        _advance_auto_update_sequence = MainWindow._advance_auto_update_sequence
        _finish_auto_update_sequence = MainWindow._finish_auto_update_sequence

        def _spawn(self, name: str, attr: str) -> None:
            assert name not in running
            assert not running, f"{name} started while {running} still running"
            order.append(name)
            running.append(name)

            def _work(progress=None):
                time.sleep(0.03)
                return name

            w = Worker(_work)

            def _clear(_arg=None):
                if name in running:
                    running.remove(name)

            w.finished_ok.connect(_clear)
            w.failed.connect(_clear)
            setattr(self, attr, w)
            w.start()

        def _check_launcher_update(self, silent: bool = False) -> None:
            self._spawn("launcher", "_launcher_update_worker")

        def _check_updates(
            self, silent: bool = False, periodic: bool = False, force: bool = False
        ) -> None:
            self._spawn("addons", "_update_worker")

        def _check_mod_updates(
            self, silent: bool = False, periodic: bool = False, force: bool = False
        ) -> None:
            self._spawn("client", "_mod_update_worker")

    prev = settings.check_updates_on_startup()
    try:
        settings._data["check_updates_on_startup"] = True
        harness = Harness()
        harness._start_auto_update_sequence(include_catalogs=True, periodic=True)
        wait = QDeadlineTimer(5000)
        while harness._auto_update_seq_active and not wait.hasExpired():
            app.processEvents()
        assert order == ["launcher", "addons", "client"]
        assert not harness._auto_update_seq_active
        assert not running

        order.clear()
        skipped = Harness()
        skipped._start_auto_update_sequence(include_catalogs=False, periodic=False)
        wait = QDeadlineTimer(5000)
        while skipped._auto_update_seq_active and not wait.hasExpired():
            app.processEvents()
        assert order == ["launcher"]
        assert not skipped._auto_update_seq_active
    finally:
        settings._data["check_updates_on_startup"] = prev

    print("OK auto update sequence is launcher -> addons -> client")


def test_loading_bar_reserves_update_button_slot():
    from PySide6.QtWidgets import QApplication

    from ichalaunch.ui.widgets.loading_bar import ThemeLoadingBar

    app = QApplication.instance() or QApplication([])
    bar = ThemeLoadingBar()
    assert bar.minimumWidth() == 320
    assert bar.maximumWidth() == 880
    bar.reserve_trailing(56 + 8)
    assert bar.minimumWidth() == 220
    assert bar.maximumWidth() == 880 - 64
    bar.reserve_trailing(0)
    assert bar.minimumWidth() == 320
    assert bar.maximumWidth() == 880
    print("OK loading bar reserves update button slot")


def test_launch_buttons_use_glue_panel_chrome():
    """PLAY / UPDATE / REGISTER use purple glue-panel art with a gold underline."""
    from PySide6.QtWidgets import QApplication

    from ichalaunch.ui.widgets.glue_panel_button import launch_glue_chrome
    from ichalaunch.ui.widgets.launch_button import LaunchButton, UpdateLaunchButton

    app = QApplication.instance() or QApplication([])
    play = LaunchButton("PLAY")
    assert play._chrome is not None and not play._chrome.isNull()
    reg = LaunchButton("REGISTER HERE")
    assert reg._chrome is not None and not reg._chrome.isNull()
    upd = UpdateLaunchButton()
    assert upd._chrome is not None and not upd._chrome.isNull()
    # Visible plate is a 56×56 square (not a tall rectangle inside a square pixmap).
    assert upd._chrome.width() == 56
    assert upd._chrome.height() == 56
    wide = launch_glue_chrome(pressed=False)
    assert not wide.isNull()
    assert wide.width() > wide.height()
    sq = launch_glue_chrome(pressed=False, square=True)
    simg = sq.toImage()
    min_x, min_y, max_x, max_y = 56, 56, -1, -1
    for y in range(simg.height()):
        for x in range(simg.width()):
            if simg.pixelColor(x, y).alpha() >= 24:
                min_x = min(min_x, x)
                min_y = min(min_y, y)
                max_x = max(max_x, x)
                max_y = max(max_y, y)
    vis_w = max_x - min_x + 1
    vis_h = max_y - min_y + 1
    assert vis_w >= 52 and vis_h >= 52, f"visible plate too small {vis_w}x{vis_h}"
    assert abs(vis_w - vis_h) <= 3, f"visible plate not square {vis_w}x{vis_h}"
    mid = simg.height() // 2

    def _is_purple_fill(x: int) -> bool:
        c = simg.pixelColor(x, mid)
        return c.alpha() >= 16 and 240 <= c.hue() <= 300 and c.saturation() >= 80

    left_metal = sum(1 for x in range(8) if not _is_purple_fill(x))
    right_metal = sum(1 for x in range(simg.width() - 8, simg.width()) if not _is_purple_fill(x))
    assert left_metal >= 6, "UPDATE chrome missing left metal frame"
    assert right_metal >= 6, "UPDATE chrome missing right metal frame"

    chrome = launch_glue_chrome(pressed=False)
    assert not chrome.isNull()
    img = chrome.toImage()
    purple = gold = 0
    for y in range(0, img.height(), 2):
        for x in range(0, img.width(), 2):
            c = img.pixelColor(x, y)
            if c.alpha() < 200:
                continue
            h = c.hue()
            if 240 <= h <= 300 and c.saturation() >= 60:
                purple += 1
            # Soft underline: muted warm gold blended into the fill (not #F1C22D).
            if (
                18 <= h <= 55
                and c.saturation() >= 40
                and c.value() >= 80
                and c.red() > c.blue() + 20
            ):
                gold += 1
    assert purple > 200, "glue launch chrome missing purple fill"
    assert gold > 5, "glue launch chrome missing gold underline"
    print("OK launch buttons use glue-panel chrome")


def test_options_cog_uses_wow_art():
    from PySide6.QtWidgets import QApplication

    from ichalaunch.core.paths import theme_file
    from ichalaunch.ui.widgets.common import OptionsCogButton, _options_cog_pixmap

    app = QApplication.instance() or QApplication([])
    assert theme_file("UI-OptionsButton.PNG").is_file()
    icon = _options_cog_pixmap()
    assert not icon.isNull()
    btn = OptionsCogButton()
    assert btn.size().width() == 28
    assert btn.size().height() == 28
    assert not btn._icon.isNull()
    print("OK addons settings cog uses UI-OptionsButton art")


def test_pass_remove_uses_wow_art():
    from PySide6.QtWidgets import QApplication

    from ichalaunch.core.paths import theme_file
    from ichalaunch.ui.widgets.common import PassRemoveButton, _pass_icon_pixmap
    from ichalaunch.ui.widgets.glue_panel_button import glue_row_square_chrome

    app = QApplication.instance() or QApplication([])
    assert theme_file("UI-GroupLoot-Pass-Up.PNG").is_file()
    assert theme_file("UI-GroupLoot-Pass-Down.PNG").is_file()
    up = _pass_icon_pixmap(pressed=False)
    down = _pass_icon_pixmap(pressed=True)
    assert not up.isNull()
    assert not down.isNull()
    chrome = glue_row_square_chrome(pressed=False, side=28)
    assert not chrome.isNull()
    assert chrome.width() == 28
    assert chrome.height() == 28
    btn = PassRemoveButton()
    assert btn.size().width() == 28
    assert btn.size().height() == 28
    print("OK addon remove uses GroupLoot Pass art on square glue chrome")


def test_nav_tab_update_alert_badge():
    """Folder tabs use the bundled Adventure Guide alert when updates are pending."""
    from PySide6.QtWidgets import QApplication

    from ichalaunch.core.paths import theme_file
    from ichalaunch.ui.main_window import NavTabButton
    from ichalaunch.ui.widgets.update_alert_badge import TAB_ALERT_NAME, TAB_ALERT_PX, update_alert_badge_pixmap

    app = QApplication.instance() or QApplication([])
    assert theme_file(TAB_ALERT_NAME).is_file()
    btn = NavTabButton("HOME")
    btn.resize(120, 44)
    pm = update_alert_badge_pixmap()
    assert not pm.isNull()
    assert 0 < pm.width() <= TAB_ALERT_PX
    assert 0 < pm.height() <= TAB_ALERT_PX
    btn.set_badge_visible(True)
    assert btn._badge is True
    btn.set_badge_visible(True)  # idempotent
    btn.set_badge_visible(False)
    assert btn._badge is False
    print("OK nav tab update alert badge")


def test_nav_tab_glue_floor_chrome():
    """Top nav tabs use floor-tinted Glue-Panel art, not purple action plates."""
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QApplication

    from ichalaunch.ui.main_window import _FLOOR_BASE, _TAB_ART_SHIFT_Y, NavTabButton
    from ichalaunch.ui.widgets.glue_panel_button import GLUE_FLOOR_TINT, glue_floor_chrome_pixmap

    app = QApplication.instance() or QApplication([])
    del app
    assert GLUE_FLOOR_TINT.name() == "#181315"
    assert _FLOOR_BASE.name() == "#181315"
    assert _TAB_ART_SHIFT_Y == 7
    pm = glue_floor_chrome_pixmap(pressed=False, shade="idle")
    assert not pm.isNull()
    img = pm.toImage()
    c = QColor.fromRgba(img.pixel(img.width() // 2, img.height() // 2))
    # Idle fill maps onto the ContentPanel floor; must not be PLAY purple.
    assert abs(c.red() - 24) <= 10
    assert abs(c.green() - 19) <= 10
    assert abs(c.blue() - 21) <= 10
    hue = c.hue()
    assert hue < 0 or hue <= 20 or hue >= 320
    btn = NavTabButton("HOME")
    btn.resize(120, 42)
    btn.set_badge_visible(True)
    btn.grab()
    print("OK nav tab glue floor chrome")


def test_client_cat_nav_update_alert_badge():
    """Client category sub-tabs show per-category pending update/apply badges."""
    from PySide6.QtWidgets import QApplication

    from ichalaunch.ui.pages.client import ClientPage
    from ichalaunch.ui.widgets.update_alert_badge import TAB_ALERT_NAME, TAB_ALERT_PX, update_alert_badge_pixmap
    from ichalaunch.core.paths import theme_file

    app = QApplication.instance() or QApplication([])
    assert theme_file(TAB_ALERT_NAME).is_file()
    pm = update_alert_badge_pixmap()
    assert not pm.isNull()
    assert 0 < pm.width() <= TAB_ALERT_PX

    page = ClientPage()
    assert page.cat_btns, "expected at least one category button"
    btn = page.cat_btns[0]
    btn.set_badge_visible(True)
    assert btn._badge is True
    btn.set_badge_visible(False)
    assert btn._badge is False

    # Pending update routes to the mod's category tab.
    page._pending_updates = {"vanillafixes": {"id": "vanillafixes", "local": "1", "remote": "2"}}
    cats = page._categories_with_pending_badge()
    assert "Performance & Fixes" in cats

    page._pending_updates = {}
    page._apply_pending = False
    page._refresh_cat_badges()
    assert not any(b._badge for b in page.cat_btns)
    print("OK client category nav update alert badge")


def test_chrome_buttons_clear_metal_tr():
    from ichalaunch.core.paths import theme_file
    from ichalaunch.ui import main_window as mw

    assert mw._CHROME_BTN_INSET_X >= mw._METAL_CORNER_DRAW
    assert mw._CHROME_BTN_INSET_Y >= mw._METAL_EDGE_DRAW
    assert mw._METAL_FLOOR_OUTSET >= 1
    src = Path(mw.__file__).read_text(encoding="utf-8")
    assert "_metal_underfill_path" in src
    assert "_FRAME_STROKE" not in src
    assert "painter.drawPath(path)" not in src
    assert "_progress_slot" in src
    assert "SideCornersOverlay" not in src
    assert "left_corners.png" not in src
    assert "class PortraitPlayFrame" in src
    assert "WA_TransparentForMouseEvents" in src
    for name in (
        mw._PORTRAIT_EDGE_BOTTOM_NAME,
        mw._PORTRAIT_EDGE_LEFT_NAME,
        mw._PORTRAIT_EDGE_RIGHT_NAME,
        mw._PORTRAIT_CORNER_BL_NAME,
        mw._PORTRAIT_CORNER_BR_NAME,
    ):
        assert name in src
        assert theme_file(name).is_file(), name
    print("OK minimize/close clear metal TR/rail; portrait overlay crops present")


def test_play_stays_right_when_progress_hidden():
    """An expanding slot — not the bar itself — keeps PLAY pinned right."""
    from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QSizePolicy, QWidget

    app = QApplication.instance() or QApplication([])
    host = QWidget()
    host.resize(800, 80)
    lay = QHBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    status = QLabel("Ready")
    slot = QWidget()
    slot.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    slot_l = QHBoxLayout(slot)
    slot_l.setContentsMargins(0, 0, 0, 0)
    bar = QWidget()
    bar.setFixedSize(240, 32)
    slot_l.addWidget(bar)
    play = QWidget()
    play.setFixedSize(200, 56)
    grip = QWidget()
    grip.setFixedSize(16, 16)
    lay.addWidget(status)
    lay.addWidget(slot, 1)
    lay.addWidget(play)
    lay.addWidget(grip)
    host.show()
    app.processEvents()
    play_x = play.x()
    bar.hide()
    lay.activate()
    app.processEvents()
    assert abs(play.x() - play_x) <= 2, f"PLAY shifted from {play_x} to {play.x()}"
    host.hide()
    print("OK PLAY stays right-aligned when progress is hidden")


def test_client_exe_probe_is_case_insensitive():
    """3.3.5-era clients ship "Wow.exe"; 1.12-era ship "WoW.exe".

    On Windows both spellings reach the same file, so a literal check passed
    there and made half the clients invisible on Linux.
    """
    from ichalaunch.core.filesystem import resolve_ci
    from ichalaunch.game.launcher import has_wow_exe, wow_exe_in

    for spelling in ("WoW.exe", "Wow.exe", "WOW.EXE"):
        with tempfile.TemporaryDirectory() as td:
            game = Path(td)
            (game / spelling).write_bytes(b"MZ")
            assert has_wow_exe(game), f"{spelling} not found"
            found = wow_exe_in(game)
            assert found is not None
            # Windows returns the requested spelling when the exact path exists
            # (NTFS is case-insensitive). Linux must report the on-disk name.
            if sys.platform == "win32":
                assert found.name.lower() == "wow.exe"
            else:
                assert found.name == spelling

    with tempfile.TemporaryDirectory() as td:
        game = Path(td)
        (game / "NotAGame.exe").write_bytes(b"MZ")
        assert not has_wow_exe(game)
        assert wow_exe_in(game) is None

    # resolve_ci: exact hit, case-corrected hit, and a genuine miss.
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        (base / "Data").mkdir()
        (base / "Data" / "patch-A.mpq").write_bytes(b"mpq")
        assert resolve_ci(base, "Data/patch-A.mpq") is not None
        found = resolve_ci(base, "data/patch-a.mpq")
        assert found is not None
        if sys.platform == "win32":
            assert found.name.lower() == "patch-a.mpq"
        else:
            assert found.name == "patch-A.mpq"
        assert resolve_ci(base, "data/absent.mpq") is None

    print("OK client exe probe is case-insensitive")


def test_linux_proton_launch_resolution():
    """Proton discovery, pin-by-default, and command assembly.

    Uses a stub settings object throughout: resolving a build PINS it, and a
    test must never write into the user's real configuration.
    """
    if sys.platform == "win32":
        print("OK linux proton launch resolution (skipped on Windows)")
        return

    import os

    from ichalaunch.game import proton

    class _Stub:
        def __init__(self, d):
            self.d = dict(d)

        def get(self, k, default=None):
            return self.d.get(k, default)

        def set(self, k, v):
            self.d[k] = v

    real = proton.settings
    try:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            tools = root / "compatibilitytools.d"
            for name in ("GE-Proton9-20", "GE-Proton10-34", "Proton-GE Latest", "notatool"):
                (tools / name).mkdir(parents=True)
            for name in ("GE-Proton9-20", "GE-Proton10-34", "Proton-GE Latest"):
                (tools / name / "toolmanifest.vdf").write_text("x")

            proton.settings = _Stub({"linux_proton_path": "", "linux_wineprefix": "",
                                     "linux_use_latest_proton": False, "linux_umu_path": ""})
            os.environ["STEAM_EXTRA_COMPAT_TOOLS_PATHS"] = str(tools)
            try:
                builds = [b.name for b in proton.discover_proton_builds()
                          if str(b).startswith(str(tools))]
            finally:
                os.environ.pop("STEAM_EXTRA_COMPAT_TOOLS_PATHS", None)

            # A directory without a manifest is not a Proton build.
            assert "notatool" not in builds, builds
            # Numeric names sort newest-first; a digit-less name never wins
            # automatic selection, because it is a moving target.
            assert builds[0] == "GE-Proton10-34", builds
            assert builds.index("GE-Proton9-20") < builds.index("Proton-GE Latest"), builds

            # Pinning: the resolved build is written back and then honoured.
            stub = proton.settings
            stub.set("linux_proton_path", str(tools / "GE-Proton9-20"))
            assert proton.resolve_proton_path().name == "GE-Proton9-20"

            # A missing umu-run is a clear error, not a traceback.
            stub.set("linux_umu_path", str(root / "no-such-umu"))
            try:
                proton.build_launch_command(root / "WoW.exe", root)
                raise AssertionError("expected FileNotFoundError")
            except FileNotFoundError as exc:
                assert "umu-run" in str(exc), str(exc)
    finally:
        proton.settings = real

    print("OK linux proton launch resolution")


def main():
    import ichalaunch.config.settings as settings_mod

    real_path_fn = settings_mod.settings_path
    with tempfile.TemporaryDirectory() as td:
        isolated = Path(td) / "settings.json"
        settings_mod.settings_path = lambda: isolated
        settings_mod.settings.load()
        try:
            _run_smoke_tests()
        finally:
            settings_mod.settings_path = real_path_fn
            settings_mod.settings.load()


def _run_smoke_tests():
    test_catalogs()
    test_github_parse()
    test_protected()
    test_dlls_txt()
    test_detect_state()
    test_vanilla_tweaks_disable_clears_pending()
    test_apply_desired_state_guard()
    test_mod_remove_desired_state()
    test_stock_patch9_not_owned_by_pretty_night_sky()
    test_stock_patch9_collision_migration()
    test_catalog_mpq_letters_unique()
    test_pretty_night_sky_migrates_off_fog_y()
    test_stock_patch9_reacquire_detect()
    test_darker_nights_migration()
    test_mod_toggle_resolution()
    test_mod_author_labels()
    test_vanillafixes_dxvk_reconcile()
    test_dxvk_detect_plan_clean()
    test_hd_patch_lt_exclusive_planning()
    test_hd_patch_exclusive_variant_swap()
    test_hd_patch_both_desired_reconciled()
    test_backfill_installed_mods_on_detect()
    test_resolve_launch_exe()
    test_vf_mode_labels()
    test_vf_dxvk_roundtrip_simulated_plan_clean()
    test_vf_dxvk_roundtrip_plan_clean()
    test_dxvk_switch_keeps_vanillafixes_exe()
    test_detect_game_ravencraft_subfolder()
    test_assess_dxvk_gpu()
    test_addon_fork_version_labels()
    test_addon_github_browse_helpers()
    test_plan_changes_hd_env_set_no_recursion()
    test_vanilla_helpers_hd_dependency()
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
    test_git_refs_and_tip_index()
    test_mod_catalog_repos_in_tip_index_builder()
    test_mod_remote_identity_uses_tip_index()
    test_addon_toc_folder_name_required()
    test_addon_toc_folder_rename()
    test_addon_update_check_uses_catalog_index_only()
    test_git_refs_live_optional()
    test_github_token_not_sent_to_third_party_readme_hosts()
    test_github_bad_token_retries_without_auth()
    test_auto_scan_cooldown_setting()
    test_auto_scan_cooldown_persists_to_disk()
    test_addon_startup_token_gating()
    test_settings_paths_survive_load_cycle()
    test_settings_paths_recover_from_backup()
    test_bagshui_catalog_pin()
    test_never_update_persists()
    test_sanitize_filename()
    test_robust_rmtree_readonly_git_pack()
    test_install_clears_readonly_data_mpqs()
    test_vanillafixes_zip_in_memory()
    test_vanillafixes_preserves_dlls_txt()
    test_apply_desired_state_restores_dlls_txt()
    test_prepare_for_launch_syncs_dlls_txt()
    test_client_exe_probe_is_case_insensitive()
    test_linux_proton_launch_resolution()
    test_prepare_for_launch_clears_data_readonly()
    test_plan_missing_installs_dxvk()
    test_play_prep_plans_remove()
    test_client_zip_mirrors_and_gofile_parse()
    test_find_wow_exe_dir_and_extract()
    test_settle_existing_alphanumeric_folder()
    test_browser_zip_watch_and_install_from_zip()
    test_cleanup_client_zip()
    test_zip_url_from_html()
    test_game_permissions_scan_and_fix()
    test_game_permissions_protected_path()
    test_launcher_release_cache()
    test_dll_injection_mod_detection()
    test_superwow_issue_detection()
    test_themed_dialog_flags_and_close()
    test_dll_security_dialog_dont_show_again_is_themed_checkbox()
    test_mpq_patch_warning_dialog_and_persist()
    test_update_launch_button_is_square_and_pulses()
    test_launch_button_down_plate_is_click_only()
    test_worker_survives_ref_drop_in_result_slot()
    test_main_worker_ref_cleared_after_release()
    test_auto_update_sequence_is_launcher_addons_client()
    test_loading_bar_reserves_update_button_slot()
    test_launch_buttons_use_glue_panel_chrome()
    test_options_cog_uses_wow_art()
    test_pass_remove_uses_wow_art()
    test_nav_tab_update_alert_badge()
    test_nav_tab_glue_floor_chrome()
    test_client_cat_nav_update_alert_badge()
    test_chrome_buttons_clear_metal_tr()
    test_play_stays_right_when_progress_hidden()
    print("\nAll smoke tests passed.")


if __name__ == "__main__":
    main()

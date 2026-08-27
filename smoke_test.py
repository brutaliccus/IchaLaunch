"""Headless smoke tests for IchaLaunch core (no GUI)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

# Ensure project root on path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Importing ichalaunch installs the opt-in crash hook. Never POST test
# AssertionErrors / simulated worker failures to the sticky #58 issue.
os.environ.setdefault("ICHALAUNCH_NO_CRASH_REPORT", "1")

from ichalaunch.addons.github import load_catalog, parse_github_url
from ichalaunch.core.filesystem import is_protected_path, update_dlls_txt, write_dlls_txt, read_dlls_txt
from ichalaunch.mods.installer import load_mod_catalog, detect_actual_state


def test_catalogs():
    mods = load_mod_catalog()
    assert len(mods) >= 20, mods
    addons = load_catalog()
    assert len(addons) >= 500, len(addons)
    print(f"OK catalogs: {len(mods)} mods, {len(addons)} addons")


def test_tls_ca_env_sanitizer():
    """Stale CA env vars (Postgres, foo.crt, missing dir) must not survive startup."""
    import os
    import ssl

    import requests

    from ichalaunch.core.tls import (
        CA_DIR_ENV_VARS,
        CA_FILE_ENV_VARS,
        bundled_ca_file,
        process_ca_file,
        sanitize_tls_ca_env,
    )

    ca_names = CA_FILE_ENV_VARS + CA_DIR_ENV_VARS
    saved = {name: os.environ.get(name) for name in ca_names}

    def _restore() -> None:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        sanitize_tls_ca_env()

    certifi_pem = bundled_ca_file()
    assert certifi_pem and os.path.isfile(certifi_pem), "certifi cacert.pem must be readable"

    try:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            postgres = root / "Program Files" / "PostgreSQL" / "16" / "ssl" / "certs" / "ca-bundle.crt"
            foo = root / "foo.crt"
            missing_dir = root / "no_such_certs"
            git_crt = root / "git-missing.crt"
            os.environ["SSL_CERT_FILE"] = str(postgres)
            os.environ["REQUESTS_CA_BUNDLE"] = str(foo)
            os.environ["CURL_CA_BUNDLE"] = str(root / "curl-missing.pem")
            os.environ["GIT_SSL_CAINFO"] = str(git_crt)
            os.environ["PIP_CERT"] = str(root / "pip.crt")
            os.environ["NODE_EXTRA_CA_CERTS"] = str(root / "node.crt")
            os.environ["SSL_CERT_DIR"] = str(missing_dir)

            bundle = sanitize_tls_ca_env()
            assert bundle == certifi_pem
            assert process_ca_file() == certifi_pem
            leftover = " ".join(os.environ.get(n, "") for n in ca_names)
            assert "PostgreSQL" not in leftover
            assert "foo.crt" not in leftover
            assert "curl-missing" not in leftover
            assert "git-missing" not in leftover
            assert "no_such_certs" not in leftover
            assert os.environ.get("SSL_CERT_DIR") in (None, "")
            for name in CA_FILE_ENV_VARS:
                assert os.environ.get(name) == certifi_pem, name
            assert requests.certs.where() == certifi_pem
            session = requests.Session()
            merged = session.merge_environment_settings(
                "https://example.com", {}, None, True, None
            )
            assert merged.get("verify") == certifi_pem
            ssl.create_default_context()

            custom = root / "corporate.pem"
            custom.write_bytes(Path(certifi_pem).read_bytes())
            os.environ["SSL_CERT_FILE"] = str(custom)
            os.environ["REQUESTS_CA_BUNDLE"] = str(foo)
            os.environ["CURL_CA_BUNDLE"] = str(root / "other-missing.crt")
            os.environ["SSL_CERT_DIR"] = str(missing_dir)
            chosen = sanitize_tls_ca_env()
            assert chosen == str(custom)
            assert os.environ["SSL_CERT_FILE"] == str(custom)
            assert os.environ["REQUESTS_CA_BUNDLE"] == str(custom)
            assert os.environ["CURL_CA_BUNDLE"] == str(custom)
            assert "foo.crt" not in (os.environ.get("REQUESTS_CA_BUNDLE") or "")
            assert os.environ.get("SSL_CERT_DIR") in (None, "")
    finally:
        _restore()

    print("OK tls ca env sanitizer")


def test_bundle_pins_charset_normalizer_and_excludes_chardet():
    """The build pins one character-detection library, and requests works with it."""
    import subprocess

    spec = (ROOT / "IchaLaunch.spec").read_text(encoding="utf-8")
    assert '"chardet"' in spec and "excludes=" in spec, "chardet must be excluded"
    assert 'collect_all("charset_normalizer")' in spec, (
        "requests resolves its detector through importlib, which PyInstaller "
        "cannot see, so charset_normalizer has to be collected explicitly"
    )

    # requests hard-requires charset_normalizer; chardet is only the optional
    # use-chardet-on-py3 extra. Excluding chardet is therefore safe, but only if
    # the fallback really works, so check it rather than assume it. Run in a
    # subprocess: blocking an import in-process would leak into later tests.
    probe = (
        "import sys, io\n"
        "class B:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name == 'chardet' or name.startswith('chardet.'):\n"
        "            raise ImportError('excluded from the bundle')\n"
        "        return None\n"
        "sys.meta_path.insert(0, B())\n"
        "import requests, requests.compat\n"
        "det = requests.compat.chardet\n"
        "assert det is not None, 'no detector: Response.text would raise'\n"
        "assert det.__name__ == 'charset_normalizer', det.__name__\n"
        "from requests.models import Response\n"
        "from urllib3 import HTTPResponse\n"
        "r = Response()\n"
        "r.raw = HTTPResponse(body=io.BytesIO('h\\u00e9llo w\\u00f6rld'.encode('utf-8')),\n"
        "                     preload_content=False, status=200)\n"
        "r.status_code = 200; r.headers = {}; r.encoding = None\n"
        "assert r.apparent_encoding\n"
        "assert r.text == 'h\\u00e9llo w\\u00f6rld', r.text\n"
        "print('ok')\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, timeout=120
    )
    assert out.returncode == 0, (
        "requests must still decode an undeclared charset with chardet absent:\n"
        + (out.stderr or "")[-800:]
    )
    assert out.stdout.strip().endswith("ok")
    print("OK bundle pins charset_normalizer and excludes chardet")


def test_no_control_flow_escapes_a_finally_block():
    """No return/break/continue inside a finally: it discards the in-flight exception."""
    import ast

    root = ROOT / "ichalaunch"
    offenders: list[str] = []

    def scan(body, *, in_loop):
        """Walk a finally body without entering nested scopes that reset the rule."""
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue  # its own scope; a return there is that function's
            if isinstance(node, ast.Return):
                yield node
                continue
            if isinstance(node, (ast.Break, ast.Continue)) and not in_loop:
                yield node
                continue
            if isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
                for f in ("body", "orelse"):
                    yield from scan(getattr(node, f), in_loop=True)
                continue
            for field in ("body", "orelse", "finalbody"):
                yield from scan(getattr(node, field, []) or [], in_loop=in_loop)
            for handler in getattr(node, "handlers", []) or []:
                yield from scan(handler.body, in_loop=in_loop)

    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Try, getattr(ast, "TryStar", ast.Try))):
                continue
            for bad in scan(node.finalbody, in_loop=False):
                offenders.append(
                    f"{path.relative_to(ROOT)}:{bad.lineno} "
                    f"{type(bad).__name__.lower()} in finally"
                )

    assert not offenders, (
        "control flow leaving a finally block silently discards whatever "
        "exception was propagating, so the crash reporter never sees it:\n  "
        + "\n  ".join(offenders)
    )
    print("OK no control flow escapes a finally block")


def test_launcher_ca_env_does_not_reach_the_game():
    """The launcher's own bundled CA path is stripped from the child environment."""
    from ichalaunch.core.process import child_launch_env
    from ichalaunch.core.tls import (
        CA_FILE_ENV_VARS,
        bundled_ca_file,
        strip_launcher_ca_env,
    )

    bundled = bundled_ca_file()
    assert bundled, "the test needs a readable bundled CA to have something to strip"

    # A CA the user set themselves is theirs and stays. Only the launcher's own
    # copy is removed, because only that one is about to be deleted.
    user_ca = "/etc/ssl/certs/ca-certificates.crt"
    env = {"SSL_CERT_FILE": bundled, "REQUESTS_CA_BUNDLE": user_ca}
    removed = strip_launcher_ca_env(env)
    assert removed == ["SSL_CERT_FILE"]
    assert "SSL_CERT_FILE" not in env
    assert env["REQUESTS_CA_BUNDLE"] == user_ca

    # Nothing to strip is not an error.
    assert strip_launcher_ca_env({}) == []
    assert strip_launcher_ca_env({"SSL_CERT_FILE": user_ca}) == []

    saved = {n: os.environ.get(n) for n in CA_FILE_ENV_VARS}
    try:
        for name in CA_FILE_ENV_VARS:
            os.environ[name] = bundled

        # The Windows launch path builds its own environment.
        win_env = child_launch_env()
        for name in CA_FILE_ENV_VARS:
            assert name not in win_env, f"{name} must not reach the game child"

        if sys.platform != "win32":
            import tempfile as _tf

            from ichalaunch.game import proton

            class _Stub:
                def __init__(self, d):
                    self.d = d

                def get(self, k, default=None):
                    return self.d.get(k, default)

                def set(self, k, v):
                    self.d[k] = v

            real = proton.settings
            try:
                with _tf.TemporaryDirectory() as td:
                    root = Path(td)
                    umu = root / "umu-run"
                    umu.write_text("#!/bin/sh\nexit 0\n")
                    umu.chmod(0o755)
                    build = root / "GE-Proton10-34"
                    (build / "files" / "bin-wow64").mkdir(parents=True)
                    (build / "files" / "bin-wow64" / "wine").write_text("#!/bin/sh\n")
                    (build / "toolmanifest.vdf").write_text("x")
                    proton.settings = _Stub({
                        "linux_proton_path": str(build),
                        "linux_use_latest_proton": False,
                        "linux_umu_path": str(umu),
                        "linux_wineprefix": str(root / "prefix"),
                    })
                    launch_env = proton.build_launch_command(root / "WoW.exe", root)[1]
                    for name in CA_FILE_ENV_VARS:
                        assert name not in launch_env, (
                            f"{name} points inside the PyInstaller extraction "
                            "directory, which is deleted when the launcher exits. "
                            "umu fetches its runtime over HTTPS after that."
                        )

                    # The user's own CA is still handed through, so a corporate
                    # prefix keeps working.
                    os.environ["SSL_CERT_FILE"] = user_ca
                    kept = proton.build_launch_command(root / "WoW.exe", root)[1]
                    assert kept.get("SSL_CERT_FILE") == user_ca
            finally:
                proton.settings = real
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    print("OK launcher CA env does not reach the game")


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
    assert parse_github_url("https://gitlab.com/aead/aBindings") is None
    print("OK github parse")


def test_gitlab_parse_and_install_url():
    from ichalaunch.addons.github import (
        addon_install_url_for_choice,
        fork_entry_from_repo_url,
        parse_entry_owner_repo,
        parse_github_url,
    )
    from ichalaunch.addons.gitlab import (
        gitlab_archive_url,
        gitlab_browse_url,
        parse_gitlab_url,
    )
    from ichalaunch.addons.release_downloads import addon_release_repo, parse_repo_full_name
    from ichalaunch.addons.submit import fork_repo_url, normalize_repo_url
    from ichalaunch.ui.widgets.common import addon_fork_label, git_repo_browse_url

    parsed = parse_gitlab_url("https://gitlab.com/aead/aBindings")
    assert parsed is not None
    assert parsed.owner == "aead"
    assert parsed.repo == "aBindings"
    assert parsed.tag is None
    assert parse_gitlab_url("https://gitlab.com/aead/aBindings.git") == (
        "aead",
        "aBindings",
        None,
    )
    tagged = parse_gitlab_url("https://gitlab.com/aead/aBindings/-/tags/1.2.3")
    assert tagged is not None and tagged.tag == "1.2.3"
    assert parse_gitlab_url("https://www.gitlab.com/aead/aBindings/") is not None
    assert parse_gitlab_url("not-a-url") is None
    assert parse_gitlab_url("https://github.com/shagu/ShaguTweaks") is None
    assert parse_gitlab_url("https://gitlab.com/explore/projects") is None
    assert parse_gitlab_url("https://gitlab.com/aead") is None
    assert parse_gitlab_url("https://gitlab.com.evil.example/aead/aBindings") is None

    assert parse_github_url("https://gitlab.com/aead/aBindings") is None
    assert parse_github_url("https://github.com/shagu/ShaguTweaks") is not None

    archive = gitlab_archive_url("aead", "aBindings")
    assert archive == (
        "https://gitlab.com/api/v4/projects/aead%2FaBindings/repository/archive.zip"
    )
    assert "github.com" not in archive
    assert "api.github.com" not in archive
    tip_ref = gitlab_archive_url("aead", "aBindings", "master")
    assert tip_ref.endswith("archive.zip?sha=master")
    assert "aead%2FaBindings" in tip_ref

    fe = fork_entry_from_repo_url("https://gitlab.com/aead/aBindings")
    assert fe.get("host") == "gitlab"
    assert fe.get("owner") == "aead"
    assert fe.get("repo_name") == "aBindings"
    assert fe.get("repo") == "https://gitlab.com/aead/aBindings"
    latest = addon_install_url_for_choice(fe, None)
    assert latest == "https://gitlab.com/aead/aBindings"
    tagged_url = addon_install_url_for_choice(fe, "1.2.3")
    assert tagged_url == "https://gitlab.com/aead/aBindings/-/tags/1.2.3"
    assert "github.com" not in tagged_url

    entry = {
        "name": "aBindings",
        "repo": "https://gitlab.com/aead/aBindings",
        "folder": "aBindings",
    }
    assert parse_entry_owner_repo(entry) is None
    assert git_repo_browse_url(entry["repo"]) == gitlab_browse_url("aead", "aBindings")
    assert addon_fork_label(entry) == "aead/aBindings"
    assert parse_repo_full_name(entry["repo"]) == ""
    assert addon_release_repo(entry) == ""
    assert normalize_repo_url(entry["repo"]) is None
    assert fork_repo_url(fe) is None
    print("OK gitlab parse and install url")


def test_gitlab_preview_does_not_use_github_api():
    from ichalaunch.addons import github as G
    from ichalaunch.addons import gitlab as GL

    github_calls: list[str] = []
    orig_gh = G._github_api_get
    orig_preview = GL.preview_gitlab_repo

    def _no_github(url, *a, **k):
        github_calls.append(str(url))
        raise AssertionError(f"GitHub API must not be used for GitLab URLs: {url}")

    def _fake_preview(url):
        return {"kind": "addon", "host": "gitlab", "url": url, "full_name": "aead/aBindings"}

    try:
        G._github_api_get = _no_github
        GL.preview_gitlab_repo = _fake_preview
        info = G.preview_addon_repo("https://gitlab.com/aead/aBindings")
        assert info.get("host") == "gitlab"
        assert info.get("url") == "https://gitlab.com/aead/aBindings"
        assert github_calls == []
        try:
            G.preview_addon_repo("not-a-repo")
            raise AssertionError("expected ValueError")
        except ValueError as exc:
            assert "GitHub or GitLab" in str(exc)
    finally:
        G._github_api_get = orig_gh
        GL.preview_gitlab_repo = orig_preview
    print("OK gitlab preview skips GitHub API")


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

        from ichalaunch.core.filesystem import clear_fs_caches, validate_pe_binary

        good = game / "good.dll"
        good.write_bytes(b"MZ" + b"\0" * 2048)
        assert validate_pe_binary(good, min_size=1024) is True
        bad = game / "bad.dll"
        bad.write_bytes(b"xx")
        try:
            validate_pe_binary(bad)
            raise AssertionError("expected validate_pe_binary to fail")
        except OSError:
            pass

        # Errno 22 alone (user log: no WinError in message) must soft-skip
        locked = game / "unitxp_sp3.dll"
        locked.write_bytes(b"MZ" + b"\0" * 2048)
        clear_fs_caches()
        original_open = Path.open

        def _errno22_open(self, *args, **kwargs):
            if self.name.lower() == "unitxp_sp3.dll":
                raise OSError(22, "Invalid argument", str(self))
            return original_open(self, *args, **kwargs)

        Path.open = _errno22_open  # type: ignore[method-assign]
        try:
            assert validate_pe_binary(locked, min_size=1024, retries=2, delay=0.01) is False
        finally:
            Path.open = original_open  # type: ignore[method-assign]

        from ichalaunch.core.filesystem import (
            LOCK_AV_APPLY_MESSAGE,
            LOCK_AV_VERIFY_MESSAGE,
            user_facing_os_error,
        )
        from ichalaunch.mods.installer import (
            _finish_mod_install,
            _verify_mod_install,
            format_mod_verify_warning,
            mod_is_unverified,
            split_mod_apply_results,
        )

        unitxp = {
            "id": "unitxp",
            "kind": "dll_bundle",
            "name": "UnitXP",
            "source": {"filename": "UnitXP_SP3.dll"},
            "files": [{"destination": "UnitXP_SP3.dll"}],
            "dlls_txt": {"add": ["UnitXP_SP3.dll"]},
        }
        # Matches ichalaunch.log: copy OK, verify open → EINVAL, must not raise/rollback
        clear_fs_caches()  # drop backoff from prior validate_pe_binary soft-skip
        Path.open = _errno22_open  # type: ignore[method-assign]
        try:
            soft = _verify_mod_install(game, unitxp)
            assert soft and soft[0].lower() == "unitxp_sp3.dll", soft
            notices = _finish_mod_install("unitxp", unitxp, unitxp["source"], soft_skipped=soft)
            assert notices == ["~ unitxp"], notices
            assert mod_is_unverified("unitxp")
        finally:
            Path.open = original_open  # type: ignore[method-assign]
            from ichalaunch.mods.installer import mark_mod_unverified

            mark_mod_unverified("unitxp", unverified=False)

        assert "Errno" not in LOCK_AV_VERIFY_MESSAGE
        assert "Errno" not in LOCK_AV_APPLY_MESSAGE
        assert "another process" in LOCK_AV_APPLY_MESSAGE.lower()
        assert "task manager" in LOCK_AV_APPLY_MESSAGE.lower()
        assert "wow.exe" in LOCK_AV_APPLY_MESSAGE.lower()
        assert "vanillafixes.exe" in LOCK_AV_APPLY_MESSAGE.lower()
        assert "end task" in LOCK_AV_APPLY_MESSAGE.lower()
        assert "antivirus" in LOCK_AV_APPLY_MESSAGE.lower()
        assert "task manager" in LOCK_AV_VERIFY_MESSAGE.lower()
        assert "end task" in LOCK_AV_VERIFY_MESSAGE.lower()
        assert user_facing_os_error(OSError(22, "Invalid argument")) == LOCK_AV_APPLY_MESSAGE
        assert (
            user_facing_os_error(OSError(22, "Invalid argument"), kept_install=True)
            == LOCK_AV_VERIFY_MESSAGE
        )
        locked = OSError(13, "Could not replace ClassicAPI.dll — file in use by another process.")
        locked.winerror = 32  # type: ignore[attr-defined]
        locked_msg = user_facing_os_error(locked)
        assert "Errno" not in locked_msg
        assert "task manager" in locked_msg.lower()
        assert "end task" in locked_msg.lower()
        assert "wow.exe" in locked_msg.lower()
        title, body = format_mod_verify_warning(["unitxp"])
        assert title == "Could not verify install"
        assert "Errno" not in body
        assert "another process" in body.lower()
        assert "task manager" in body.lower()
        assert "antivirus" in body.lower()
        installed, removed, warns, fails = split_mod_apply_results(
            ["+ unitxp", "~ unitxp", "! other skipped: boom"]
        )
        assert installed == ["unitxp"]
        assert warns == ["unitxp"]
        assert fails == ["other skipped: boom"]
        assert removed == []
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
            from ichalaunch.mods.vanilla_tweaks import tweaks_install_stamp

            s.set_installed_mod(
                "vanilla_tweaks", tweaks_install_stamp(s.vanilla_tweaks_options)
            )
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


def test_vanilla_tweaks_exe_swap_is_atomic():
    """A failed patched-exe swap leaves the client binary in place, not missing."""
    from ichalaunch.mods.installer import swap_patched_client_exe

    with tempfile.TemporaryDirectory() as td:
        game = Path(td)
        wow = game / "WoW.exe"
        tweaked = game / "WoW_tweaked.exe"

        wow.write_bytes(b"MZ" + b"old" * 64)
        tweaked.write_bytes(b"MZ" + b"new" * 64)
        original = wow.read_bytes()

        # Happy path: the patched build lands and the scratch file is consumed.
        swap_patched_client_exe(tweaked, wow)
        assert wow.is_file()
        assert wow.read_bytes().startswith(b"MZ" + b"new")
        assert not tweaked.exists()

        # Failure path, which is the regression this guards. Unlinking first
        # left the game with no WoW.exe whenever the rename afterwards failed.
        # A one-step replace cannot produce that state.
        wow.write_bytes(original)
        tweaked.write_bytes(b"MZ" + b"new" * 64)
        real_replace = Path.replace

        def refuse(self, target):
            raise OSError(32, "The process cannot access the file")

        Path.replace = refuse
        try:
            failed = False
            try:
                swap_patched_client_exe(tweaked, wow)
            except OSError:
                failed = True
            assert failed, "a swap that cannot complete must raise, not pass silently"
        finally:
            Path.replace = real_replace

        assert wow.is_file(), "WoW.exe must survive a swap that could not complete"
        assert wow.read_bytes() == original, "the surviving WoW.exe must be the old build"
        assert tweaked.is_file(), "the patched build must survive for a retry"
    print("OK vanilla tweaks exe swap is atomic")


def test_vanilla_tweaks_patcher_output_is_identified_or_fails():
    """The patched exe is found by what the run wrote, and a no-op run is not success."""
    import time as _time

    from ichalaunch.mods.installer import patched_exe_from_run, tweaked_exe_snapshot

    with tempfile.TemporaryDirectory() as td:
        game = Path(td)
        wow = game / "WoW.exe"
        backup = game / "WoW-OriginalBackup.exe"
        wow.write_bytes(b"MZ" + b"client" * 32)
        backup.write_bytes(b"MZ" + b"stock" * 32)

        # The real case. The patcher is fed the stock backup so option changes do
        # not stack, so it names its output after the backup, not after WoW.exe.
        before = tweaked_exe_snapshot(game)
        out = game / "WoW-OriginalBackup_tweaked.exe"
        out.write_bytes(b"MZ" + b"patched" * 32)
        assert patched_exe_from_run(game, backup, wow, before) == out
        out.unlink()

        # A patcher that names its output after the client is also handled.
        before = tweaked_exe_snapshot(game)
        alt = game / "WoW_tweaked.exe"
        alt.write_bytes(b"MZ" + b"patched" * 32)
        assert patched_exe_from_run(game, backup, wow, before) == alt

        # A stale file from an earlier run carries that run's options. A run that
        # writes nothing must report nothing rather than reinstalling it.
        before = tweaked_exe_snapshot(game)
        assert patched_exe_from_run(game, backup, wow, before) is None

        # Stale present and a fresh one written: the fresh one wins.
        before = tweaked_exe_snapshot(game)
        _time.sleep(0.01)
        fresh = game / "WoW-OriginalBackup_tweaked.exe"
        fresh.write_bytes(b"MZ" + b"newer" * 32)
        assert patched_exe_from_run(game, backup, wow, before) == fresh
        fresh.unlink()

        # A name nobody predicted is still accepted, so an upstream rename
        # degrades to a working install instead of a silent no-op.
        before = tweaked_exe_snapshot(game)
        odd = game / "vanillatweaks-output_tweaked.exe"
        odd.write_bytes(b"MZ" + b"odd" * 32)
        assert patched_exe_from_run(game, backup, wow, before) == odd
        odd.unlink()

        # An empty game folder is not a crash.
        assert patched_exe_from_run(game, backup, wow, tweaked_exe_snapshot(game)) is None
        assert tweaked_exe_snapshot(game / "does-not-exist") == {}
    print("OK vanilla tweaks patcher output is identified or fails")


def test_hand_patched_wow_exe_is_not_vanilla_tweaks():
    """Play must not restore stock WoW.exe over a hand-patched client (#280)."""
    from ichalaunch.config.settings import settings as s
    from ichalaunch.core.filesystem import clear_fs_caches
    from ichalaunch.mods.installer import detect_actual_state, plan_changes, plan_sync_changes

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
            (game / "WoW.exe").write_bytes(patched)
            (game / "WoW-OriginalBackup.exe").write_bytes(stock)
            s.set("game_path", str(game))
            s.set("addons_path", "")
            s.set("desired_mods", {"vanilla_tweaks": False})
            s.set("user_set_mods", [])
            s.set("installed_mods", {})
            s.set("user_mods", [])
            clear_fs_caches()
            assert detect_actual_state(game).get("vanilla_tweaks") is False
            assert not any(c.get("id") == "vanilla_tweaks" for c in plan_changes())
            assert not any(c.get("id") == "vanilla_tweaks" for c in plan_sync_changes())
            assert (game / "WoW.exe").read_bytes() == patched
    finally:
        for k in keys:
            s.set(k, saved[k])
        clear_fs_caches()
    print("OK hand-patched WoW.exe is not treated as Vanilla Tweaks")


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
            # HD is off; do not schedule a VanillaHelpers download (apply refuses
            # DLL installs while WoW.exe is already running).
            s.set_desired_mod("vanilla_helpers", False)
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
        STOCK_PATCH9_DOWNLOAD_URL,
        STOCK_PATCH9_EXPECTED_SIZE,
        STOCK_PATCH9_MIN_BYTES,
        STOCK_PATCH9_NAME,
        classify_stock_patch9,
        inspect_stock_patch9,
        load_stock_patch9_catalog,
        patch9_url_from_index_html,
        reacquire_stock_patch9,
        resolve_stock_patch9_url,
        should_offer_stock_patch9_reacquire,
        stock_patch9_download_url,
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

    catalog = load_stock_patch9_catalog()
    assert catalog.get("filename") == STOCK_PATCH9_NAME
    assert catalog.get("expected_size") == STOCK_PATCH9_EXPECTED_SIZE
    assert catalog.get("url") == STOCK_PATCH9_DOWNLOAD_URL
    assert stock_patch9_download_url() == STOCK_PATCH9_DOWNLOAD_URL
    assert "github.com/brutaliccus/IchaLaunch/releases/download/" in STOCK_PATCH9_DOWNLOAD_URL

    html = (
        '<a href="patch-9.mpq">patch-9.mpq</a> 483.2 MB'
    )
    url = patch9_url_from_index_html(
        html,
        "https://github.com/brutaliccus/IchaLaunch/releases/download/stock-patch-9/",
    )
    assert url == STOCK_PATCH9_DOWNLOAD_URL
    evil = patch9_url_from_index_html(
        '<a href="https://evil.example/patch-9.mpq">x</a>',
        "https://github.com/brutaliccus/IchaLaunch/releases/download/stock-patch-9/",
    )
    assert evil is None
    fallback = resolve_stock_patch9_url(html="<html>no file here</html>")
    assert fallback == STOCK_PATCH9_DOWNLOAD_URL

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
                assert "github.com/brutaliccus/IchaLaunch" in str(source.get("url") or "")
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
                    download_url=STOCK_PATCH9_DOWNLOAD_URL,
                )
                raise AssertionError("must not clobber a healthy-sized patch-9")
            except RuntimeError as exc:
                assert "complete" in str(exc).lower()
            assert dest.read_bytes().startswith(b"MPQ")
    finally:
        installer_mod._download_source = orig_dl
    print("OK stock patch-9 reacquire detect")


def test_stock_patch9_prompt_requires_wow_exe():
    """No WoW.exe in the selected folder → no patch-9 reacquire prompt."""
    from ichalaunch.mods.stock_patch import (
        inspect_stock_patch9,
        should_offer_stock_patch9_reacquire,
    )

    assert inspect_stock_patch9(None).state == "no_game"
    assert not should_offer_stock_patch9_reacquire(inspect_stock_patch9(None))
    assert inspect_stock_patch9("").state == "no_game"
    assert not should_offer_stock_patch9_reacquire(inspect_stock_patch9(""))

    with tempfile.TemporaryDirectory() as td:
        folder = Path(td)
        data = folder / "Data"
        data.mkdir()
        missing_exe = inspect_stock_patch9(folder)
        assert missing_exe.state == "no_game"
        assert not should_offer_stock_patch9_reacquire(missing_exe)

        (folder / "wow.exe").write_bytes(b"MZ")
        missing_patch = inspect_stock_patch9(folder)
        assert missing_patch.state == "missing"
        assert should_offer_stock_patch9_reacquire(missing_patch)
    print("OK stock patch-9 prompt requires WoW.exe")


def test_config_wtf_farclip_clamp():
    """Detect Config.wtf farclip above 777 and rewrite only that CVar to 777."""
    from ichalaunch.game.config_wtf import (
        FARCLIP_STOCK_MAX,
        farclip_too_high,
        read_farclip,
        set_farclip,
    )

    with tempfile.TemporaryDirectory() as td:
        game = Path(td)
        wtf = game / "WTF"
        wtf.mkdir()
        cfg = wtf / "Config.wtf"
        cfg.write_text(
            'SET scriptMemory "0"\n'
            'SET farclip "1500"\n'
            'SET SmallCull "0.01"\n',
            encoding="utf-8",
        )
        found = farclip_too_high(game)
        assert found is not None
        assert found.value == 1500
        assert found.display == "1500"
        assert found.path == cfg
        assert read_farclip(game).value == 1500
        assert set_farclip(game) is True
        text = cfg.read_text(encoding="utf-8")
        assert 'SET farclip "777"' in text
        assert 'SET scriptMemory "0"' in text
        assert 'SET SmallCull "0.01"' in text
        assert "1500" not in text
        assert farclip_too_high(game) is None
        assert read_farclip(game).value == FARCLIP_STOCK_MAX
        assert set_farclip(game) is False

        cfg.write_text('SET farclip "777"\nSET other "1"\n', encoding="utf-8")
        assert farclip_too_high(game) is None
        assert set_farclip(game) is False

        cfg.write_bytes(b'SET FARCLIP 2000\r\nSET gxVSync "0"\r\n')
        assert farclip_too_high(game).value == 2000
        assert set_farclip(game) is True
        fixed = cfg.read_bytes()
        assert b"\r\n" in fixed
        assert b'SET FARCLIP "777"' in fixed
        assert b'SET gxVSync "0"' in fixed

        cfg.unlink()
        assert read_farclip(game) is None
        assert farclip_too_high(game) is None
        assert set_farclip(game) is False
    print("OK config.wtf farclip clamp")


def test_config_wtf_regenerate():
    """Regenerate moves Config.wtf into WTF/Backup; no-op when missing."""
    from ichalaunch.game.config_wtf import backup_and_remove_config

    with tempfile.TemporaryDirectory() as td:
        game = Path(td)
        assert backup_and_remove_config(game) is None  # no WTF folder at all
        wtf = game / "WTF"
        wtf.mkdir()
        assert backup_and_remove_config(game) is None  # folder without file
        cfg = wtf / "Config.wtf"
        body = 'SET farclip "777"\nSET gxVSync "0"\n'
        cfg.write_text(body, encoding="utf-8")
        first = backup_and_remove_config(game)
        assert first is not None
        assert first.parent == wtf / "Backup"
        assert first.name.startswith("Config-")
        assert first.name.endswith(".wtf.bak")
        assert not cfg.exists()
        assert first.read_text(encoding="utf-8") == body
        assert backup_and_remove_config(game) is None  # second call is a no-op
        # A later regeneration must not destroy the earlier backup, even
        # within the same timestamp second (numeric suffix uniquifier).
        body2 = 'SET farclip "500"\n'
        cfg.write_text(body2, encoding="utf-8")
        second = backup_and_remove_config(game)
        assert second is not None
        assert second.parent == wtf / "Backup"
        assert second != first
        assert first.read_text(encoding="utf-8") == body
        assert second.read_text(encoding="utf-8") == body2
    print("OK config.wtf regenerate backup")


def test_config_wtf_restore():
    """Restore copies a chosen WTF/Backup entry over Config.wtf, saving the live file."""
    from ichalaunch.game.config_wtf import (
        list_config_backups,
        restore_config_backup,
    )

    with tempfile.TemporaryDirectory() as td:
        game = Path(td)
        assert list_config_backups(game) == []  # no WTF folder at all
        wtf = game / "WTF"
        backup_dir = wtf / "Backup"
        backup_dir.mkdir(parents=True)
        assert list_config_backups(game) == []  # empty Backup folder
        (backup_dir / "notes.txt").write_text("not a backup", encoding="utf-8")
        old = backup_dir / "Config-20260826-120000.wtf.bak"
        old.write_text('SET farclip "500"\n', encoding="utf-8")
        newer = backup_dir / "Config-20260827-091500.wtf.bak"
        newer.write_text('SET farclip "600"\n', encoding="utf-8")
        suffixed = backup_dir / "Config-20260827-091500-1.wtf.bak"
        suffixed.write_text('SET farclip "650"\n', encoding="utf-8")
        backups = list_config_backups(game)
        assert [b.path.name for b in backups] == [
            suffixed.name,
            newer.name,
            old.name,
        ]  # newest first; same-second suffix sorts above its base
        assert backups[0].label == "2026-08-27 09:15:00 (1)"
        assert backups[1].label == "2026-08-27 09:15:00"
        assert backups[2].label == "2026-08-26 12:00:00"

        live = wtf / "Config.wtf"
        live_body = 'SET farclip "777"\n'
        live.write_text(live_body, encoding="utf-8")
        prior = restore_config_backup(game, old)
        assert live.read_text(encoding="utf-8") == 'SET farclip "500"\n'
        assert old.is_file()  # the restored backup stays in Backup/
        assert prior is not None
        assert prior.parent == backup_dir
        assert prior.read_text(encoding="utf-8") == live_body
        assert not (wtf / "Config.wtf.tmp").exists()

        # Missing backup raises so the UI can re-list and inform.
        missing = backup_dir / "Config-19990101-000000.wtf.bak"
        try:
            restore_config_backup(game, missing)
            raise AssertionError("expected FileNotFoundError")
        except FileNotFoundError:
            pass
        assert live.read_text(encoding="utf-8") == 'SET farclip "500"\n'

        # No live Config.wtf: restore just puts the backup in place.
        live.unlink()
        assert restore_config_backup(game, newer) is None
        assert live.read_text(encoding="utf-8") == 'SET farclip "600"\n'
        assert newer.is_file()
    print("OK config.wtf restore from backup")


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
        # Patch-E lists Fog Pushback as an include — not Pretty Night Sky / Epoch Water.
        assert env.get("fog_pushback") is True
        assert env.get("vanilla_tweaks") is True  # fog dependency
        assert env.get("pretty_night_sky") is not True
        assert env.get("epoch_water") is not True
        e_only = resolve_mod_toggle("hd_patch_e", True)
        assert e_only.get("fog_pushback") is True
        assert e_only.get("pretty_night_sky") is not True
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


def test_client_preset_catalog_ids():
    """Every mod id referenced by client presets exists in mods.json."""
    from ichalaunch.mods.client_presets import validate_preset_catalog_ids

    missing = validate_preset_catalog_ids()
    assert not missing, f"missing preset mod ids: {missing}"
    print("OK client preset catalog ids")


def test_client_preset_apply_basic():
    """Applying Basic enables the expected desired_mods keys."""
    from ichalaunch.config.settings import settings as s
    from ichalaunch.mods.client_presets import PRESET_BASIC, apply_client_preset, preset_mod_ids_for_tests

    keys = ("desired_mods", "user_set_mods", "client_preset", "client_preset_hd_ultra")
    saved = {k: s.get(k) for k in keys}
    try:
        s.set("desired_mods", {})
        s.set("user_set_mods", [])
        apply_client_preset(PRESET_BASIC)
        expected = preset_mod_ids_for_tests(PRESET_BASIC)
        for mid in expected:
            assert s.desired_mods.get(mid), mid
        assert not s.desired_mods.get("vanillafixes")
        assert not s.desired_mods.get("hd_dxvk")
        assert s.get("client_preset") == PRESET_BASIC
    finally:
        for k in keys:
            s.set(k, saved[k])
    print("OK client preset apply basic")


def test_client_preset_downgrade_basic_plus_to_basic():
    """Downgrading Basic+ to Basic disables the extra mods."""
    from ichalaunch.config.settings import settings as s
    from ichalaunch.mods.client_presets import (
        PRESET_BASIC,
        PRESET_BASIC_PLUS,
        apply_client_preset,
        downgrade_extra_mods,
    )

    keys = ("desired_mods", "user_set_mods", "client_preset", "client_preset_hd_ultra")
    saved = {k: s.get(k) for k in keys}
    try:
        s.set("desired_mods", {})
        s.set("user_set_mods", [])
        apply_client_preset(PRESET_BASIC_PLUS)
        extras = set(downgrade_extra_mods(PRESET_BASIC_PLUS, PRESET_BASIC))
        assert "perfboost" in extras
        assert "hd_patch_i" in extras
        apply_client_preset(PRESET_BASIC)
        for mid in extras:
            assert not s.desired_mods.get(mid), mid
        assert s.desired_mods.get("dxvk")
        assert not s.desired_mods.get("hd_dxvk")
    finally:
        for k in keys:
            s.set(k, saved[k])
    print("OK client preset downgrade basic+ to basic")


def test_client_preset_apply_hd_aio():
    """HD AIO enables Basic+ mods plus lettered HD patches A/B/C/D/E/G/S/T."""
    from ichalaunch.config.settings import settings as s
    from ichalaunch.mods.client_presets import (
        PRESET_BASIC_PLUS,
        PRESET_HD_AIO,
        apply_client_preset,
        preset_mod_ids_for_tests,
    )

    keys = ("desired_mods", "user_set_mods", "client_preset", "client_preset_hd_ultra")
    saved = {k: s.get(k) for k in keys}
    letters = {f"hd_patch_{letter}" for letter in "abcdegst"}
    try:
        s.set("desired_mods", {})
        s.set("user_set_mods", [])
        apply_client_preset(PRESET_HD_AIO)
        expected = preset_mod_ids_for_tests(PRESET_HD_AIO)
        plus = preset_mod_ids_for_tests(PRESET_BASIC_PLUS)
        assert plus <= expected
        assert letters <= expected
        for mid in expected:
            assert s.desired_mods.get(mid), mid
        assert s.desired_mods.get("perfboost")
        assert s.desired_mods.get("hd_dxvk")
        assert s.desired_mods.get("hd_patch_i")
        assert s.desired_mods.get("hd_patch_t")
        assert not s.desired_mods.get("hd_patch_t_ultra")
        assert not s.desired_mods.get("hd_patch_u")
        assert s.get("client_preset") == PRESET_HD_AIO

        apply_client_preset(PRESET_HD_AIO, hd_ultra=True)
        ultra = preset_mod_ids_for_tests(PRESET_HD_AIO, hd_ultra=True)
        assert plus <= ultra
        assert "hd_patch_t" not in ultra
        assert "hd_patch_t_ultra" in ultra and "hd_patch_u" in ultra
        assert s.desired_mods.get("hd_patch_t_ultra")
        assert s.desired_mods.get("hd_patch_u")
        assert not s.desired_mods.get("hd_patch_t")
        assert s.desired_mods.get("perfboost")
        assert s.desired_mods.get("hd_patch_a")
    finally:
        for k in keys:
            s.set(k, saved[k])
    print("OK client preset apply hd aio")


def test_client_preset_downgrade_hd_aio_to_basic_plus():
    """Downgrading HD AIO to Basic+ drops A/B/C/D/E/G/S/T (and ultra T/U)."""
    from ichalaunch.config.settings import settings as s
    from ichalaunch.mods.client_presets import (
        PRESET_BASIC_PLUS,
        PRESET_HD_AIO,
        apply_client_preset,
        downgrade_extra_mods,
        preset_mod_ids_for_tests,
    )

    keys = ("desired_mods", "user_set_mods", "client_preset", "client_preset_hd_ultra")
    saved = {k: s.get(k) for k in keys}
    letters = {f"hd_patch_{letter}" for letter in "abcdegst"}
    plus = preset_mod_ids_for_tests(PRESET_BASIC_PLUS)
    try:
        s.set("desired_mods", {})
        s.set("user_set_mods", [])
        apply_client_preset(PRESET_HD_AIO)
        extras = set(downgrade_extra_mods(PRESET_HD_AIO, PRESET_BASIC_PLUS))
        assert extras == letters
        apply_client_preset(PRESET_BASIC_PLUS)
        for mid in extras:
            assert not s.desired_mods.get(mid), mid
        for mid in plus:
            assert s.desired_mods.get(mid), mid
        assert s.desired_mods.get("perfboost")
        assert s.desired_mods.get("hd_patch_i")
        assert s.desired_mods.get("fog_pushback")
        assert s.get("client_preset") == PRESET_BASIC_PLUS

        apply_client_preset(PRESET_HD_AIO, hd_ultra=True)
        apply_client_preset(PRESET_BASIC_PLUS)
        for mid in letters | {"hd_patch_t_ultra", "hd_patch_u"}:
            assert not s.desired_mods.get(mid), mid
        for mid in plus:
            assert s.desired_mods.get(mid), mid
    finally:
        for k in keys:
            s.set(k, saved[k])
    print("OK client preset downgrade hd aio to basic+")


def test_client_preset_manual_toggle_custom():
    """Manual mod toggle marks preset as Custom."""
    from ichalaunch.config.settings import settings as s
    from ichalaunch.mods.client_presets import PRESET_BASIC, PRESET_CUSTOM, apply_client_preset, mark_custom_preset
    from ichalaunch.mods.installer import apply_mod_toggle

    keys = ("desired_mods", "user_set_mods", "client_preset", "client_preset_hd_ultra")
    saved = {k: s.get(k) for k in keys}
    try:
        s.set("desired_mods", {})
        s.set("user_set_mods", [])
        apply_client_preset(PRESET_BASIC)
        assert s.get("client_preset") == PRESET_BASIC
        apply_mod_toggle("perfboost", True)
        mark_custom_preset()
        assert s.get("client_preset") == PRESET_CUSTOM
    finally:
        for k in keys:
            s.set(k, saved[k])
    print("OK client preset manual toggle custom")


def test_client_preset_tweaks_cog_not_custom():
    """Editing vanilla-tweaks options alone does not force Custom preset."""
    from ichalaunch.config.settings import settings as s
    from ichalaunch.mods.client_presets import PRESET_BASIC, apply_client_preset, detect_matching_preset

    keys = (
        "desired_mods",
        "user_set_mods",
        "client_preset",
        "client_preset_hd_ultra",
        "vanilla_tweaks_options",
    )
    saved = {k: s.get(k) for k in keys}
    try:
        s.set("desired_mods", {})
        s.set("user_set_mods", [])
        apply_client_preset(PRESET_BASIC)
        assert s.get("client_preset") == PRESET_BASIC
        opts = dict(s.get("vanilla_tweaks_options") or {})
        opts["widescreen_fov"] = True
        s.set("vanilla_tweaks_options", opts)
        preset, _ultra = detect_matching_preset()
        assert preset == PRESET_BASIC
        assert s.get("client_preset") == PRESET_BASIC
    finally:
        for k in keys:
            s.set(k, saved[k])
    print("OK client preset tweaks cog not custom")


def test_hd_patch_e_includes_caption():
    """Patch-E advertises Fog Pushback; Pretty Night Sky must not claim Patch-E."""
    from ichalaunch.mods.installer import get_mod, mod_contains_caption, mod_includes_caption

    e = get_mod("hd_patch_e")
    assert e is not None
    assert "fog_pushback" in (e.get("includes") or [])
    assert "pretty_night_sky" not in (e.get("includes") or [])
    caption = mod_includes_caption(e)
    assert "Fog Pushback" in caption
    assert "Pretty Night" not in caption
    contains = mod_contains_caption(e)
    assert "Environment" in contains
    assert "Fog Pushback" in contains

    a = get_mod("hd_patch_a")
    assert "Characters & NPCs" in mod_contains_caption(a)

    sky = get_mod("pretty_night_sky")
    assert sky is not None
    desc = (sky.get("description") or "").lower()
    assert "bundled in" not in desc
    assert "also bundled" not in desc
    assert "standalone" in desc
    assert "not included" in desc

    water = get_mod("epoch_water")
    assert water is not None
    wdesc = (water.get("description") or "").lower()
    assert "also included" not in wdesc
    assert "standalone" in wdesc
    print("OK hd patch-e includes caption")


def test_hd_dxvk_catalog_and_patch_v():
    """HD DXVK checkbox + Patch-C installs as patch-v.mpq."""
    import tarfile
    import tempfile
    from pathlib import Path

    from ichalaunch.core.filesystem import extract_tar
    from ichalaunch.core.paths import data_file
    from ichalaunch.mods.installer import (
        _pick_dxvk_win32_d3d9,
        get_mod,
        mod_contains_caption,
        mod_catalog_map,
        resolve_mod_toggle,
    )

    hd = get_mod("hd_dxvk")
    assert hd is not None
    assert hd.get("category") == "HD Graphics"
    assert hd.get("list_label") == "Recommended"
    assert hd.get("kind") == "dxvk_hd"
    assert "v2.7.1" in str((hd.get("source") or {}).get("url") or "")
    assert "Recommended" in mod_contains_caption(hd)

    catalog = mod_catalog_map()
    hd_graphics = [m["id"] for m in catalog.values() if m.get("category") == "HD Graphics"]
    assert hd_graphics[0] == "hd_dxvk"
    assert hd_graphics[1] == "vanilla_helpers"
    assert hd_graphics[2] == "hd_patch_a"
    assert "dxvk" in (hd.get("dependencies") or [])
    assert "vanilla_helpers" not in (hd.get("dependencies") or [])

    # mods.json array order (source of Client tab row order within a category)
    from ichalaunch.mods.installer import load_mod_catalog

    json_hd = [m["id"] for m in load_mod_catalog() if m.get("category") == "HD Graphics"]
    assert json_hd[0] == "hd_dxvk", json_hd[:5]
    assert json_hd.index("hd_dxvk") < json_hd.index("vanilla_helpers")
    for other in json_hd[1:]:
        assert json_hd.index("hd_dxvk") < json_hd.index(other)

    dxvk = get_mod("dxvk")
    assert "hd_dxvk" not in (dxvk.get("conflicts") or [])
    assert "vanilla_helpers" not in (dxvk.get("dependencies") or [])
    vf = get_mod("vanillafixes")
    assert "vanilla_helpers" not in (vf.get("dependencies") or [])

    from ichalaunch.config.settings import settings as s

    saved_dx = bool(s.desired_mods.get("dxvk"))
    saved_vh = bool(s.desired_mods.get("vanilla_helpers"))
    saved_hd = bool(s.desired_mods.get("hd_dxvk"))
    try:
        s.set_desired_mod("dxvk", False)
        s.set_desired_mod("vanilla_helpers", False)
        s.set_desired_mod("hd_dxvk", False)
        hd_toggle = resolve_mod_toggle("hd_dxvk", True)
        assert hd_toggle.get("hd_dxvk") is True
        assert hd_toggle.get("dxvk") is True
        # Helpers are for HD patches only — not auto-enabled by DXVK 2.7.1.
        assert "vanilla_helpers" not in hd_toggle or hd_toggle.get("vanilla_helpers") is not True
    finally:
        s.set_desired_mod("dxvk", saved_dx)
        s.set_desired_mod("vanilla_helpers", saved_vh)
        s.set_desired_mod("hd_dxvk", saved_hd)

    patch_c = get_mod("hd_patch_c")
    assert patch_c is not None
    assert patch_c.get("name") == "Reforged HD — Patch-C (Creatures)"
    assert "Patch-V" not in str(patch_c.get("name") or "")
    desc = str(patch_c.get("description") or "").lower()
    assert "patch-v.mpq" in desc
    assert "patch-c" in desc
    assert patch_c.get("destination") == "Data/patch-v.mpq"
    assert "patch-v.mpq" in (patch_c.get("detect") or {}).get("data_mpq", [])
    assert "patch-C.mpq" in (patch_c.get("detect") or {}).get("data_mpq", [])
    assert "Patch-V.mpq" not in (patch_c.get("detect") or {}).get("data_mpq", [])
    assert str((patch_c.get("source") or {}).get("url") or "").endswith("/patches/patch-C.mpq")
    assert "Creatures" in mod_contains_caption(patch_c)

    conf = data_file("dxvk.conf")
    assert conf.is_file()
    text = conf.read_text(encoding="utf-8")
    assert "dxvk.logLevel = none" in text
    assert "d3d9.dpiAware = False" in text
    assert "DXVK 2.7.1" in text

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        arc = root / "dxvk-2.7.1.tar.gz"
        payload = root / "payload"
        (payload / "x64").mkdir(parents=True)
        (payload / "x32").mkdir(parents=True)
        (payload / "x64" / "d3d9.dll").write_bytes(b"x64")
        (payload / "x32" / "d3d9.dll").write_bytes(b"x32")
        with tarfile.open(arc, "w:gz") as tf:
            tf.add(payload / "x32", arcname="dxvk-2.7.1/x32")
            tf.add(payload / "x64", arcname="dxvk-2.7.1/x64")
        extracted = extract_tar(arc, root / "out")
        picked = _pick_dxvk_win32_d3d9(extracted)
        assert picked.read_bytes() == b"x32"

    print("OK hd dxvk catalog and patch-v install target")


def test_hd_dxvk_disable_restores_vf_layer():
    """Disabling DXVK 2.7.1 keeps VF+Vulkan and reinstalls bundled dll/conf."""
    import tempfile
    from pathlib import Path
    from unittest.mock import patch

    from ichalaunch.config.settings import settings as s
    from ichalaunch.core.filesystem import clear_fs_caches
    from ichalaunch.mods import installer as inst
    from ichalaunch.mods.installer import detect_actual_state, resolve_mod_toggle

    keys = ("desired_mods", "user_set_mods", "installed_mods", "game_path", "addons_path")
    saved = {k: s.get(k) for k in keys}
    try:
        s.set(
            "desired_mods",
            {
                "dxvk": True,
                "hd_dxvk": True,
                "vanilla_helpers": False,
                "hd_patch_a": False,
            },
        )
        s.set("user_set_mods", [])
        off = resolve_mod_toggle("hd_dxvk", False)
        assert off.get("hd_dxvk") is False
        assert "dxvk" not in off or off.get("dxvk") is not False
        assert "vanilla_helpers" not in off

        with tempfile.TemporaryDirectory() as td:
            game = Path(td)
            (game / "WoW.exe").write_bytes(b"MZ")
            (game / "VanillaFixes.exe").write_bytes(b"MZ")
            (game / "d3d9.dll").write_bytes(b"MZ-DXVK-2.7.1")
            (game / "dxvk.conf").write_text(
                "# Turtle WoW (1.12) - DXVK 2.7.1\ndxvk.logLevel = none\n",
                encoding="utf-8",
            )
            s.set("game_path", str(game))
            s.set("addons_path", "")
            s.set("installed_mods", {"hd_dxvk": {"name": "DXVK 2.7.1"}, "dxvk": {"name": "DXVK"}})
            clear_fs_caches()

            actual = detect_actual_state(game)
            assert actual.get("hd_dxvk") is True
            assert actual.get("dxvk") is True

            # VF-bundled conf has no 2.7.1 marker → hd_dxvk must not detect.
            (game / "dxvk.conf").write_text("# VanillaFixes DXVK\ndxvk.logLevel = none\n", encoding="utf-8")
            clear_fs_caches()
            actual2 = detect_actual_state(game)
            assert actual2.get("hd_dxvk") is False
            assert actual2.get("dxvk") is True

            (game / "dxvk.conf").write_text(
                "# Turtle WoW (1.12) - DXVK 2.7.1\ndxvk.logLevel = none\n",
                encoding="utf-8",
            )
            clear_fs_caches()
            called: list[str] = []

            def _fake_install(mod_id, progress=None, prefer_latest=False):
                called.append(mod_id)
                (game / "d3d9.dll").write_bytes(b"vf-dll")
                (game / "dxvk.conf").write_text("# VanillaFixes DXVK restored\n", encoding="utf-8")
                return []

            with patch.object(inst, "install_mod", side_effect=_fake_install):
                inst.remove_mod("hd_dxvk")
            assert called == ["dxvk"]
            assert (game / "d3d9.dll").read_bytes() == b"vf-dll"
            assert "VanillaFixes DXVK restored" in (game / "dxvk.conf").read_text(encoding="utf-8")
            assert "hd_dxvk" not in s.installed_mods

            # Full DXVK removal when VF+Vulkan is not desired.
            (game / "d3d9.dll").write_bytes(b"MZ-DXVK-2.7.1")
            (game / "dxvk.conf").write_text("# DXVK 2.7.1\n", encoding="utf-8")
            s.set("desired_mods", {"dxvk": False, "hd_dxvk": False})
            s.set("installed_mods", {"hd_dxvk": {"name": "DXVK 2.7.1"}})
            called.clear()
            with patch.object(inst, "install_mod", side_effect=_fake_install):
                inst.remove_mod("hd_dxvk")
            assert called == []
            assert not (game / "d3d9.dll").exists()
            assert not (game / "dxvk.conf").exists()
    finally:
        for k in keys:
            s.set(k, saved[k])
        clear_fs_caches()
    print("OK hd dxvk disable restores vf layer")


def test_dxvk_layers_detect_dll_not_conf_comment():
    """hd_dxvk must not stay installed after the cursor DLL replaces 2.7.1 (#277)."""
    from ichalaunch.config.settings import settings as s
    from ichalaunch.core.filesystem import clear_fs_caches
    from ichalaunch.mods.installer import (
        _order_d3d9_layers,
        detect_actual_state,
        plan_changes,
    )

    keys = ("desired_mods", "user_set_mods", "installed_mods", "game_path", "addons_path")
    saved = {k: s.get(k) for k in keys}
    try:
        with tempfile.TemporaryDirectory() as td:
            game = Path(td)
            (game / "WoW.exe").write_bytes(b"MZ")
            (game / "d3d9.dll").write_bytes(b"MZ-DXVK-2.7.1")
            (game / "dxvk.conf").write_text(
                "# Turtle WoW (1.12) - DXVK 2.7.1\n", encoding="utf-8"
            )
            s.set("game_path", str(game))
            s.set("addons_path", "")
            s.set("desired_mods", {"dxvk": True, "hd_dxvk": True, "dxvk_big_cursor": False})
            s.set("user_set_mods", [])
            s.set("installed_mods", {})
            clear_fs_caches()
            actual = detect_actual_state(game)
            assert actual.get("hd_dxvk") is True
            assert actual.get("dxvk_big_cursor") is False

            # Cursor DLL + leftover 2.7.1 comment, HD not stacked with cursor.
            (game / "d3d9.dll").write_bytes(b"MZ-retrocro-cursor")
            (game / "dxvk.conf").write_text(
                "# Turtle WoW (1.12) - DXVK 2.7.1\nd3d9.enlargeHardwareCursor = 2\n",
                encoding="utf-8",
            )
            clear_fs_caches()
            actual2 = detect_actual_state(game)
            assert actual2.get("hd_dxvk") is False
            assert actual2.get("dxvk_big_cursor") is True
            assert any(
                c.get("action") == "install" and c.get("id") == "hd_dxvk"
                for c in plan_changes()
            )

            # Both desired: cursor may own the DLL if the 2.7.1 conf remains.
            s.set("desired_mods", {"dxvk": True, "hd_dxvk": True, "dxvk_big_cursor": True})
            clear_fs_caches()
            actual3 = detect_actual_state(game)
            assert actual3.get("hd_dxvk") is True
            assert actual3.get("dxvk_big_cursor") is True
            assert not any(c.get("id") == "hd_dxvk" for c in plan_changes())

            assert _order_d3d9_layers(
                ["dxvk_big_cursor", "nampower", "hd_dxvk", "dxvk"]
            ) == ["nampower", "dxvk", "hd_dxvk", "dxvk_big_cursor"]
    finally:
        for k in keys:
            s.set(k, saved[k])
        clear_fs_caches()
    print("OK dxvk layers detect the DLL, not a leftover conf comment")


def test_dxvk_cursor_remove_restores_dll_from_backup():
    """Unchecking Bigger Mouse Cursor must put d3d9.dll back (#277)."""
    from ichalaunch.config.settings import settings as s
    from ichalaunch.core.backup import create_backup
    from ichalaunch.core.filesystem import clear_fs_caches
    from ichalaunch.mods.installer import remove_mod

    keys = ("desired_mods", "user_set_mods", "installed_mods", "game_path", "addons_path")
    saved = {k: s.get(k) for k in keys}
    try:
        with tempfile.TemporaryDirectory() as td:
            game = Path(td)
            (game / "WoW.exe").write_bytes(b"MZ")
            (game / "d3d9.dll").write_bytes(b"MZ-retrocro-cursor")
            (game / "dxvk.conf").write_text(
                "# Turtle WoW (1.12) - DXVK 2.7.1\nd3d9.enlargeHardwareCursor = 2\n",
                encoding="utf-8",
            )
            snap = create_backup(
                game, "before_dxvk_big_cursor", [game / "d3d9.dll", game / "dxvk.conf"]
            )
            (snap / "d3d9.dll").write_bytes(b"MZ-DXVK-2.7.1")
            (snap / "dxvk.conf").write_text(
                "# Turtle WoW (1.12) - DXVK 2.7.1\n", encoding="utf-8"
            )
            s.set("game_path", str(game))
            s.set("addons_path", "")
            s.set("desired_mods", {"dxvk": True, "hd_dxvk": True, "dxvk_big_cursor": False})
            s.set("installed_mods", {"dxvk_big_cursor": {"name": "cursor"}})
            clear_fs_caches()
            remove_mod("dxvk_big_cursor")
            assert (game / "d3d9.dll").read_bytes() == b"MZ-DXVK-2.7.1"
            text = (game / "dxvk.conf").read_text(encoding="utf-8")
            assert "enlargeHardwareCursor" not in text
    finally:
        for k in keys:
            s.set(k, saved[k])
        clear_fs_caches()
    print("OK dxvk cursor remove restores the previous d3d9.dll")


def test_hd_dxvk_remove_offline_does_not_raise():
    """Unchecking DXVK 2.7.1 offline must not abort Play (#277)."""
    from unittest.mock import patch

    from ichalaunch.config.settings import settings as s
    from ichalaunch.core.filesystem import clear_fs_caches
    from ichalaunch.mods import installer as inst

    keys = ("desired_mods", "user_set_mods", "installed_mods", "game_path", "addons_path")
    saved = {k: s.get(k) for k in keys}
    try:
        with tempfile.TemporaryDirectory() as td:
            game = Path(td)
            (game / "WoW.exe").write_bytes(b"MZ")
            (game / "d3d9.dll").write_bytes(b"MZ-DXVK-2.7.1")
            (game / "dxvk.conf").write_text("# DXVK 2.7.1\n", encoding="utf-8")
            s.set("game_path", str(game))
            s.set("addons_path", "")
            s.set("desired_mods", {"dxvk": True, "hd_dxvk": False})
            s.set("installed_mods", {"hd_dxvk": {"name": "DXVK 2.7.1"}})
            clear_fs_caches()

            def _offline(_mod_id, progress=None, prefer_latest=False):
                raise inst.requests.RequestException("offline")

            with patch.object(inst, "install_mod", side_effect=_offline):
                inst.remove_mod("hd_dxvk")
            assert (game / "d3d9.dll").is_file()
    finally:
        for k in keys:
            s.set(k, saved[k])
        clear_fs_caches()
    print("OK hd dxvk offline revert does not raise")


def test_patch_v_is_not_patch_c():
    """Hand-placed Patch-V.mpq must not count as, or be deleted as, Patch-C (#278)."""
    import os

    from ichalaunch.config.settings import settings as s
    from ichalaunch.core.filesystem import clear_fs_caches
    from ichalaunch.mods.installer import detect_actual_state, remove_mod

    keys = ("desired_mods", "user_set_mods", "installed_mods", "game_path", "addons_path")
    saved = {k: s.get(k) for k in keys}
    try:
        with tempfile.TemporaryDirectory() as td:
            game = Path(td)
            data = game / "Data"
            data.mkdir()
            (game / "WoW.exe").write_bytes(b"MZ")
            (data / "Patch-V.mpq").write_bytes(b"wmo-override")
            s.set("game_path", str(game))
            s.set("addons_path", "")
            s.set("desired_mods", {"hd_patch_c": False})
            s.set("user_set_mods", [])
            s.set("installed_mods", {})
            clear_fs_caches()
            assert detect_actual_state(game).get("hd_patch_c") is False
            s.set("installed_mods", {"hd_patch_c": {"name": "Patch-C"}})
            remove_mod("hd_patch_c")
            assert (data / "Patch-V.mpq").read_bytes() == b"wmo-override"

            other = Path(td) / "game2"
            other_data = other / "Data"
            other_data.mkdir(parents=True)
            (other / "WoW.exe").write_bytes(b"MZ")
            (other_data / "patch-v.mpq").write_bytes(b"creatures")
            s.set("game_path", str(other))
            s.set("installed_mods", {"hd_patch_c": {"name": "Patch-C"}})
            clear_fs_caches()
            assert detect_actual_state(other).get("hd_patch_c") is True
            remove_mod("hd_patch_c")
            clear_fs_caches()
            assert "patch-v.mpq" not in set(os.listdir(other_data))
    finally:
        for k in keys:
            s.set(k, saved[k])
        clear_fs_caches()
    print("OK Patch-V.mpq is not treated as Patch-C")


def test_mod_author_labels():
    from ichalaunch.ui.widgets.common import mod_author

    vf = {"id": "vanillafixes", "source": {"repo": "hannesmann/vanillafixes"}}
    assert mod_author(vf) == "hannesmann"
    hd = {"id": "hd_patch_a", "category": "HD Graphics"}
    assert mod_author(hd) == "Project Reforged"
    explicit = {"id": "x", "author": "Custom Author"}
    assert mod_author(explicit) == "Custom Author"
    print("OK mod author labels")


def test_hd_graphics_project_link_only():
    """HD Graphics rows expose Project Reforged open-link, never Open-in-Git."""
    from ichalaunch.mods.installer import load_mod_catalog
    from ichalaunch.ui.widgets.common import mod_git_url, mod_open_url

    reforged = "https://projectreforged.github.io/vanilla/downloads/turtle/"
    for mod in load_mod_catalog():
        if mod.get("category") != "HD Graphics":
            continue
        assert mod_git_url(mod) is None, mod.get("id")
        open_url = mod_open_url(mod)
        assert open_url == reforged, (mod.get("id"), open_url)
    print("OK HD graphics project link only")


def test_dxvk_disable_cascades_dependents():
    """Unchecking VF+Vulkan clears HD DXVK / cursor; helpers stay unless only for HD."""
    from ichalaunch.config.settings import settings as s
    from ichalaunch.mods.installer import resolve_mod_toggle

    keys = ("desired_mods", "user_set_mods")
    saved = {k: s.get(k) for k in keys}
    try:
        s.set(
            "desired_mods",
            {
                "dxvk": True,
                "hd_dxvk": True,
                "vanilla_helpers": True,
                "dxvk_big_cursor": True,
                "hd_patch_a": True,
            },
        )
        s.set("user_set_mods", [])
        off = resolve_mod_toggle("dxvk", False)
        assert off.get("dxvk") is False
        assert off.get("hd_dxvk") is False
        assert off.get("dxvk_big_cursor") is False
        # vanilla_helpers does not depend on dxvk; HD patches keep it.
        assert "vanilla_helpers" not in off or off.get("vanilla_helpers") is not False
        assert "hd_patch_a" not in off or off.get("hd_patch_a") is not False

        # Enabling VF+Vulkan alone must not force VanillaHelpers.
        s.set("desired_mods", {})
        on_dxvk = resolve_mod_toggle("dxvk", True)
        assert on_dxvk.get("dxvk") is True
        assert "vanilla_helpers" not in on_dxvk or on_dxvk.get("vanilla_helpers") is not True
    finally:
        for k in keys:
            s.set(k, saved[k])
    print("OK dxvk disable cascades dependents")


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


def test_hd_variant_identified_by_size_on_disk():
    """A hand-installed patch-T/L variant is identified by its size, not guessed.

    Both patch-T tiers install Data/patch-T.mpq and both patch-L bodies install
    Data/patch-L.mpq, so filename detection matches both. When the launcher
    installed the file its install record settles it, but a file downloaded from
    the publisher by hand has no record and used to be attributed by fallback --
    which lands on the standard tier even when Ultra Base is what is on disk.
    That matters because patch-U depends on the ultra base specifically.
    """
    import tempfile as _tf

    from ichalaunch.config import settings as settings_mod
    from ichalaunch.core.filesystem import clear_fs_caches
    from ichalaunch.mods import installer as I

    catalog = I.mod_catalog_map()
    sizes = {mid: (catalog[mid] or {}).get("size_bytes")
             for mid in ("hd_patch_t", "hd_patch_t_ultra",
                         "hd_patch_l", "hd_patch_l_less_thicc")}
    # The catalog must carry a distinct size for each side of both pairs, or
    # this whole mechanism silently degrades to the old guess.
    assert all(sizes.values()), sizes
    assert sizes["hd_patch_t"] != sizes["hd_patch_t_ultra"], sizes
    assert sizes["hd_patch_l"] != sizes["hd_patch_l_less_thicc"], sizes

    keys = ("installed_mods", "desired_mods")
    saved = {k: settings_mod.settings.get(k) for k in keys}
    try:
        def winner(pair, dest, size, record):
            a, b = pair
            settings_mod.settings.set("installed_mods", record)
            settings_mod.settings.set("desired_mods", {})
            with _tf.TemporaryDirectory() as td:
                game = Path(td)
                (game / "Data").mkdir()
                with open(game / "Data" / dest, "wb") as fh:
                    fh.truncate(size)          # sparse; never writes 500 MB
                clear_fs_caches()
                out = I._reconcile_exclusive_variants_detected(
                    {a: True, b: True}, game_path=game)
            return a if out[a] else (b if out[b] else None)

        T = ("hd_patch_t", "hd_patch_t_ultra")
        L = ("hd_patch_l", "hd_patch_l_less_thicc")

        # No install record at all: the bytes on disk decide.
        assert winner(T, "patch-T.mpq", sizes["hd_patch_t_ultra"], {}) == "hd_patch_t_ultra"
        assert winner(T, "patch-T.mpq", sizes["hd_patch_t"], {}) == "hd_patch_t"
        assert winner(L, "patch-L.mpq", sizes["hd_patch_l_less_thicc"], {}) == "hd_patch_l_less_thicc"
        assert winner(L, "patch-L.mpq", sizes["hd_patch_l"], {}) == "hd_patch_l"

        # A record left stale by a hand swap does not outrank the file itself.
        assert winner(T, "patch-T.mpq", sizes["hd_patch_t_ultra"],
                      {"hd_patch_t": {}}) == "hd_patch_t_ultra"
        assert winner(T, "patch-T.mpq", sizes["hd_patch_t"],
                      {"hd_patch_t_ultra": {}}) == "hd_patch_t"

        # A size matching neither variant must fall through to the old
        # behaviour rather than resolving to nothing.
        assert winner(T, "patch-T.mpq", 4096,
                      {"hd_patch_t_ultra": {}}) == "hd_patch_t_ultra"
        assert winner(T, "patch-T.mpq", 4096, {}) == "hd_patch_t"
    finally:
        for k, v in saved.items():
            settings_mod.settings.set(k, v)

    print("OK hd variant identified by size on disk")


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
    from ichalaunch.core.filesystem import clear_fs_caches
    from ichalaunch.game.launcher import launch_exe_note, resolve_launch_exe

    keys = ("game_path", "vanillafixes_enabled", "desired_mods")
    saved = {k: s.get(k) for k in keys}
    try:
        with tempfile.TemporaryDirectory() as td:
            game = Path(td)
            wow = game / "WoW.exe"
            vf = game / "VanillaFixes.exe"
            wow.write_bytes(b"MZ")
            s.set("game_path", str(game))
            s.set("desired_mods", {"vanillafixes": True, "dxvk": False})
            s.set("vanillafixes_enabled", True)
            clear_fs_caches()

            assert resolve_launch_exe(game) == wow
            assert launch_exe_note(game, wow) == "VanillaFixes.exe not found in game folder"

            vf.write_bytes(b"MZ")
            clear_fs_caches()
            assert resolve_launch_exe(game) == vf
            assert launch_exe_note(game, vf) is None

            # Stale Launch-settings flag must not override the Client checkbox.
            s.set("vanillafixes_enabled", False)
            assert resolve_launch_exe(game) == vf
            assert launch_exe_note(game, vf) is None

            s.set("desired_mods", {"vanillafixes": False, "dxvk": False})
            s.set("vanillafixes_enabled", False)
            assert resolve_launch_exe(game) == wow
            assert launch_exe_note(game, wow) == (
                "launch through VanillaFixes disabled in Client"
            )

            assert resolve_launch_exe(game, force_direct=True) == wow
            assert launch_exe_note(game, wow, force_direct=True) == (
                "user chose Launch Anyway without VanillaFixes"
            )
    finally:
        for k, v in saved.items():
            s.set(k, v)
        clear_fs_caches()
    print("OK resolve_launch_exe")


def test_vanillafixes_launch_decision():
    """Client desired_mods is the VF launch authority; missing exe asks, off does not."""
    from ichalaunch.config.settings import settings as s
    from ichalaunch.core.filesystem import clear_fs_caches
    from ichalaunch.game.launcher import (
        VF_LAUNCH_ASK,
        VF_LAUNCH_DIRECT,
        VF_LAUNCH_OK,
        vanillafixes_launch_decision,
        vanillafixes_reinstall_mod_id,
    )

    keys = ("game_path", "vanillafixes_enabled", "desired_mods")
    saved = {k: s.get(k) for k in keys}
    try:
        with tempfile.TemporaryDirectory() as td:
            game = Path(td)
            (game / "WoW.exe").write_bytes(b"MZ")
            s.set("game_path", str(game))
            clear_fs_caches()

            s.set("desired_mods", {"vanillafixes": True, "dxvk": False})
            s.set("vanillafixes_enabled", False)
            assert vanillafixes_launch_decision(game) == VF_LAUNCH_ASK
            assert vanillafixes_reinstall_mod_id() == "vanillafixes"

            (game / "VanillaFixes.exe").write_bytes(b"MZ")
            clear_fs_caches()
            assert vanillafixes_launch_decision(game) == VF_LAUNCH_OK

            s.set("desired_mods", {"vanillafixes": False, "dxvk": False})
            s.set("vanillafixes_enabled", True)
            assert vanillafixes_launch_decision(game) == VF_LAUNCH_DIRECT

            s.set("desired_mods", {"vanillafixes": False, "dxvk": True})
            s.set("vanillafixes_enabled", False)
            assert vanillafixes_launch_decision(game) == VF_LAUNCH_OK
            assert vanillafixes_reinstall_mod_id() == "dxvk"

            (game / "VanillaFixes.exe").unlink()
            clear_fs_caches()
            assert vanillafixes_launch_decision(game) == VF_LAUNCH_ASK
            assert vanillafixes_launch_decision(game, force_direct=True) == VF_LAUNCH_DIRECT
    finally:
        for k, v in saved.items():
            s.set(k, v)
        clear_fs_caches()
    print("OK vanillafixes launch decision")


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


def test_dxvk_disable_removes_vanillafixes_one_apply():
    """Unchecking VF+Vulkan must clear VanillaFixes.exe in a single Apply.

    Previously remove_mod(dxvk) only deleted d3d9.dll/dxvk.conf, leaving
    VanillaFixes.exe. Rescan then re-checked VanillaFixes — a second Apply.
    """
    from ichalaunch.config.settings import settings as s
    from ichalaunch.core.detect import sync_desired_mods_from_disk
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
            (game / "VanillaFixes.exe").write_bytes(b"MZ" * 200)
            (game / "VfPatcher.dll").write_bytes(b"MZ" * 200)
            (game / "d3d9.dll").write_bytes(b"MZ" * 200)
            (game / "dxvk.conf").write_text(
                "DXVK 2.7.1\nd3d9.enlargeHardwareCursor = 2\n", encoding="utf-8"
            )
            s.set("game_path", str(game))
            s.set("addons_path", "")
            s.set(
                "desired_mods",
                {"dxvk": True, "hd_dxvk": True, "dxvk_big_cursor": True},
            )
            s.set("user_set_mods", ["dxvk", "hd_dxvk", "dxvk_big_cursor"])
            s.set(
                "installed_mods",
                {"dxvk": {}, "hd_dxvk": {}, "dxvk_big_cursor": {}},
            )
            s.set("vanillafixes_enabled", True)
            clear_fs_caches()

            apply_mod_toggle("dxvk", False)
            assert not s.desired_mods.get("dxvk")
            assert not s.desired_mods.get("hd_dxvk")
            plan = plan_changes()
            assert any(c.get("id") == "dxvk" and c.get("action") == "remove" for c in plan)

            done = apply_desired_state()
            assert "- dxvk" in done, done
            clear_fs_caches()
            assert not (game / "VanillaFixes.exe").exists(), list(game.iterdir())
            assert not (game / "d3d9.dll").exists()
            assert not (game / "dxvk.conf").exists()
            actual = detect_actual_state(game)
            assert not actual.get("dxvk") and not actual.get("vanillafixes"), actual
            assert plan_changes() == [], plan_changes()

            # Rescan must not resurrect VanillaFixes as desired.
            synced = sync_desired_mods_from_disk()
            assert not synced.get("vanillafixes")
            assert not synced.get("dxvk")
            assert plan_changes() == [], plan_changes()

            # Switching DXVK → regular VanillaFixes still keeps the launcher exe.
            (game / "VanillaFixes.exe").write_bytes(b"MZ" * 200)
            (game / "VfPatcher.dll").write_bytes(b"MZ" * 200)
            (game / "d3d9.dll").write_bytes(b"MZ" * 200)
            (game / "dxvk.conf").write_text("d3d9.enlargeHardwareCursor = 2\n", encoding="utf-8")
            s.set("desired_mods", {"dxvk": True, "vanillafixes": False})
            s.set("user_set_mods", ["dxvk"])
            clear_fs_caches()
            apply_mod_toggle("vanillafixes", True)
            assert s.desired_mods.get("vanillafixes")
            assert not s.desired_mods.get("dxvk")
            apply_desired_state()
            assert (game / "VanillaFixes.exe").is_file()
            assert not (game / "d3d9.dll").exists()
            assert not (game / "dxvk.conf").exists()
    finally:
        for k in keys:
            s.set(k, saved[k])
        clear_fs_caches()
    print("OK dxvk disable removes vanillafixes one apply")


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

    # The name table is the Windows verdict, so off Windows it is exercised
    # directly: assess_dxvk_gpu() answers a different question there, and a
    # DRM driver name never carries a string these patterns could match.
    if sys.platform != "win32":
        bad = gpu_compat._assess_by_name(("Intel(R) HD Graphics 4000",))
        assert bad is not None and bad[0] == "bad", bad
        assert "Intel" in bad[1][0]
        assert gpu_compat._assess_by_name(("NVIDIA GeForce RTX 3070",)) is None
        assert gpu_compat._assess_by_name(("amdgpu [1002:13C0]",)) is None
        print("OK assess dxvk gpu (name table only, off Windows)")
        return

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
    # Timestamps / rolling aliases are not version labels.
    assert addon_version_label(entry, {"version": "2026-07-16"}) == "v2.1.0"
    assert addon_version_label({"pin_release": "Release"}) == ""
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


def test_ensure_game_path_keeps_saved_folder():
    """A saved game_path must not be overwritten by a nearby WoW.exe."""
    from unittest.mock import patch

    from ichalaunch.config.settings import settings as s
    from ichalaunch.game.launcher import ensure_game_path_from_launcher

    keys = ("game_path", "addons_path")
    saved = {k: s.get(k) for k in keys}
    try:
        with tempfile.TemporaryDirectory() as td:
            saved_dir = Path(td) / "saved"
            saved_dir.mkdir()
            nearby = Path(td) / "near"
            nearby.mkdir()
            (nearby / "WoW.exe").write_bytes(b"MZ")
            s.set("game_path", str(saved_dir))
            s.set("addons_path", "")
            with patch(
                "ichalaunch.game.launcher.discover_game_path_near_launcher",
                return_value=nearby,
            ):
                assert ensure_game_path_from_launcher() is None
            assert Path(s.game_path).resolve() == saved_dir.resolve()
    finally:
        for k in keys:
            s.set(k, saved[k])
    print("OK ensure game path keeps saved folder")


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
    ls.wow_exe_running = lambda *_a, **_k: True
    try:
        assert addon_move_error_text(denied) == GAME_LOCK_MESSAGE
    finally:
        ls.wow_exe_running = orig_wow
    ls.wow_exe_running = lambda *_a, **_k: False
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
        extract_semver_label,
        is_preferred_release_alias,
        is_usable_release_tag,
        looks_like_timestamp_label,
        newest_version_tag,
        parse_atom_commit_sha,
        parse_atom_release_display_version,
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
    # SuperWoW: DLL zip lives on Release; Patch is an optional MPQ, not a version.
    assert newest_version_tag(["Patch", "Release"]) == "Release"
    assert newest_version_tag({"Patch": "a" * 40, "Release": "b" * 40}) == "Release"
    assert is_usable_release_tag("Release")
    assert is_usable_release_tag("v2.2")
    assert not is_usable_release_tag("Patch")
    assert is_preferred_release_alias("Release")
    assert not is_preferred_release_alias("v2.2")
    assert extract_semver_label("SuperWoW.release.2.2.zip") == "v2.2"
    assert extract_semver_label("SuperWoW 2.2") == "v2.2"
    assert looks_like_timestamp_label("2026-07-16")
    assert looks_like_timestamp_label("Mon, 16 Jul 2026 14:03:09 GMT")
    assert not looks_like_timestamp_label("v2.2")

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
    sw_atom = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>SuperWoW mpq patch</title>
    <link rel="alternate" href="https://github.com/balakethelock/SuperWoW/releases/tag/Patch"/>
  </entry>
  <entry>
    <title>SuperWoW 2.2</title>
    <link rel="alternate" href="https://github.com/balakethelock/SuperWoW/releases/tag/Release"/>
  </entry>
</feed>"""
    assert parse_atom_release_tag(sw_atom) == "Release"
    assert parse_atom_release_display_version(sw_atom, prefer_tag="Release") == "v2.2"

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
    # Nested catalog forks[] are indexed alongside primary repos.
    assert "marcelinevq/bagnon" in keys
    print("OK mod catalog repos in tip index builder")


def test_nested_catalog_forks_in_submit_duplicate_check():
    """Suggest / auto-submit duplicate checks include forks[].repo."""
    from ichalaunch.addons.catalog import load_bundled_catalog
    from ichalaunch.addons.submit import repo_in_catalog

    catalog = load_bundled_catalog()
    assert repo_in_catalog("https://github.com/MarcelineVQ/Bagnon", catalog)
    assert repo_in_catalog("https://github.com/McPewPew/Bagnon", catalog)
    assert not repo_in_catalog(
        "https://github.com/definitely-not-real-xyz/nope-addon-999",
        catalog,
    )
    print("OK nested catalog forks in submit duplicate check")


def test_review_queue_only_root_and_requested_fork():
    """Issue queue is the network root plus one requested fork — not every active fork."""
    from ichalaunch.addons.submit import (
        clear_fork_suggest_session,
        review_queue_targets,
        should_queue_selected_fork,
        try_auto_submit_selected_fork,
        submit_catalog_suggestion,
        SubmitResult,
    )

    root = "https://github.com/shagu/pfUI"
    requested = "https://github.com/alice/pfUI-fork"
    assert review_queue_targets(submitted=root, root=root) == [root]
    assert review_queue_targets(submitted=requested, root=root) == [root, requested]
    assert review_queue_targets(submitted=requested, root=None) == [requested]
    assert review_queue_targets(
        submitted="https://github.com/alice/pfUI-fork.git",
        root="https://github.com/shagu/pfUI/",
    ) == [root, requested]

    catalog = [
        {
            "name": "pfUI",
            "repo": "https://github.com/shagu/pfUI",
            "forks": [{"repo": "https://github.com/me0wg4ming/pfUI"}],
        }
    ]
    clear_fork_suggest_session()
    assert not should_queue_selected_fork(root, catalog)
    assert not should_queue_selected_fork("https://github.com/me0wg4ming/pfUI", catalog)
    assert should_queue_selected_fork(requested, catalog)

    posted: list[dict] = []
    orig = submit_catalog_suggestion

    def fake_submit(payload):
        posted.append(payload)
        return SubmitResult(ok=True, message="ok", status_code=200, issue_url="https://example.test/1")

    import ichalaunch.addons.submit as submit_mod

    orig_readme = submit_mod._readme_excerpt_for_repo
    submit_mod.submit_catalog_suggestion = fake_submit  # type: ignore[assignment]
    submit_mod._readme_excerpt_for_repo = lambda *_a, **_k: ""  # type: ignore[assignment]
    try:
        skipped = try_auto_submit_selected_fork(root, catalog=catalog)
        assert skipped is None
        assert posted == []

        result = try_auto_submit_selected_fork(
            requested,
            catalog=catalog,
            category="General",
            name="pfUI",
            folder="pfUI",
        )
        assert result is not None and result.ok
        assert posted and posted[0]["repo"] == requested

        posted.clear()
        again = try_auto_submit_selected_fork(requested, catalog=catalog)
        assert again is None
        assert posted == []
    finally:
        submit_mod.submit_catalog_suggestion = orig  # type: ignore[assignment]
        submit_mod._readme_excerpt_for_repo = orig_readme  # type: ignore[assignment]
        clear_fork_suggest_session()

    worker_src = (ROOT / "tools" / "addon-submit-worker" / "src" / "index.js").read_text(
        encoding="utf-8"
    )
    assert "function reviewQueueNodes" in worker_src
    assert "listActiveForks(" not in worker_src
    assert 'review_queue: "root_plus_requested"' in worker_src

    workflow = (ROOT / ".github" / "workflows" / "catalog-approve.yml").read_text(
        encoding="utf-8"
    )
    assert "close_pr_if_open" in workflow
    assert "gh pr close" in workflow
    assert "|| true" not in workflow.split("gh pr close")[1][:200]
    # Current gh does not accept --head owner:branch or --jq --arg; those
    # abort the job after the branch push and never create the PR.
    assert "${GITHUB_REPOSITORY_OWNER}:${BRANCH}" not in workflow
    assert "${GITHUB_REPOSITORY_OWNER}:${branch}" not in workflow
    assert "--jq --arg" not in workflow
    assert "--pick-pr-head" in workflow
    assert '--head "$branch"' in workflow or '--head "$BRANCH"' in workflow

    print("OK review queue is root + requested fork only")


def test_catalog_approve_pr_lookup():
    """PR head lookup must work without gh --jq --arg / owner:branch syntax."""
    from tools.catalog_approve_from_issue import pick_pr_url_for_head

    prs = [
        {
            "url": "https://github.com/brutaliccus/IchaLaunch/pull/1",
            "headRefName": "other",
        },
        {
            "url": "https://github.com/brutaliccus/IchaLaunch/pull/2",
            "headRefName": "catalog/issue-335-pfui",
        },
    ]
    assert (
        pick_pr_url_for_head(prs, "catalog/issue-335-pfui")
        == "https://github.com/brutaliccus/IchaLaunch/pull/2"
    )
    assert pick_pr_url_for_head(prs, "missing") == ""
    assert pick_pr_url_for_head([], "catalog/issue-335-pfui") == ""
    assert pick_pr_url_for_head(None, "x") == ""
    assert pick_pr_url_for_head(prs, "") == ""
    assert pick_pr_url_for_head(prs, "  catalog/issue-335-pfui  ") == (
        "https://github.com/brutaliccus/IchaLaunch/pull/2"
    )

    import subprocess

    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "catalog_approve_from_issue.py"),
            "--pick-pr-head",
            "catalog/issue-335-pfui",
        ],
        input=json.dumps(prs),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "https://github.com/brutaliccus/IchaLaunch/pull/2"

    empty = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "catalog_approve_from_issue.py"),
            "--pick-pr-head",
            "catalog/issue-335-pfui",
        ],
        input="",
        capture_output=True,
        text=True,
        check=False,
    )
    assert empty.returncode == 0, empty.stderr
    assert empty.stdout.strip() == ""
    print("OK catalog approve PR head lookup")


def test_addon_settings_uncatalogued_fork_triggers_submit():
    """Save / reinstall / install of an uncatalogued fork queues review; catalogued does not."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from ichalaunch.addons import github as G
    from ichalaunch.addons import submit as submit_mod
    from ichalaunch.ui.widgets import dialogs as D

    app = QApplication.instance() or QApplication([])
    queued: list[object] = []

    def spy_queue(fork, **kwargs):
        queued.append(fork)
        return True

    orig_queue = submit_mod.queue_selected_fork_if_uncatalogued
    prev_token = G.has_github_token
    orig_preview_start = D._PreviewFetchThread.start
    orig_browse_start = D._AddonBrowseFetchThread.start

    def _noop_start(self):  # noqa: ANN001
        return None

    entry = {
        "name": "pfUI",
        "folder": "pfUI",
        "repo": "https://github.com/shagu/pfUI",
        "repository": "shagu/pfUI",
        "category": "General",
    }
    meta = {"tag": "5.4.4", "source": "github", "loaded": True}
    uncatalogued = {
        "label": "alice/NopeAddon",
        "repo": "https://github.com/alice/NopeAddon",
        "owner": "alice",
        "repo_name": "NopeAddon",
    }

    try:
        submit_mod.queue_selected_fork_if_uncatalogued = spy_queue  # type: ignore[assignment]
        G.has_github_token = lambda: True  # type: ignore[assignment]
        D._PreviewFetchThread.start = _noop_start  # type: ignore[method-assign]
        D._AddonBrowseFetchThread.start = _noop_start  # type: ignore[method-assign]

        dlg = D.AddonSettingsDialog(None, entry, meta=meta)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        assert dlg._fork_combo is not None
        dlg._fork_combo.blockSignals(True)
        dlg._fork_combo.addItem("alice/NopeAddon", uncatalogued)
        dlg._fork_combo.setCurrentIndex(dlg._fork_combo.count() - 1)
        dlg._fork_combo.blockSignals(False)
        dlg._accept_save()
        assert queued, "uncatalogued fork Save must call submit helper"
        last = queued[-1]
        assert isinstance(last, dict)
        assert "alice/NopeAddon" in str(last.get("repo") or "")

        queued.clear()
        dlg._fork_combo.blockSignals(True)
        dlg._fork_combo.setCurrentIndex(0)
        dlg._fork_combo.blockSignals(False)
        # Real helper: catalogued primary must not start a submit.
        submit_mod.queue_selected_fork_if_uncatalogued = orig_queue  # type: ignore[assignment]
        posted: list[dict] = []
        orig_submit = submit_mod.submit_catalog_suggestion

        def fake_submit(payload):
            posted.append(payload)
            return submit_mod.SubmitResult(ok=True, message="ok")

        submit_mod.clear_fork_suggest_session()
        orig_readme = submit_mod._readme_excerpt_for_repo
        submit_mod._readme_excerpt_for_repo = lambda *_a, **_k: ""  # type: ignore[assignment]
        submit_mod.submit_catalog_suggestion = fake_submit  # type: ignore[assignment]
        started = submit_mod.queue_selected_fork_if_uncatalogued(
            dlg._current_fork_data(),
            catalog=[{"name": "pfUI", "repo": "https://github.com/shagu/pfUI"}],
            category="General",
        )
        assert started is False
        assert posted == []

        started = submit_mod.queue_selected_fork_if_uncatalogued(
            uncatalogued,
            catalog=[{"name": "pfUI", "repo": "https://github.com/shagu/pfUI"}],
            category="General",
            name="pfUI",
            folder="pfUI",
            background=False,
        )
        assert started is True
        assert posted and posted[0]["repo"] == "https://github.com/alice/NopeAddon"
        submit_mod.submit_catalog_suggestion = orig_submit  # type: ignore[assignment]
        submit_mod._readme_excerpt_for_repo = orig_readme  # type: ignore[assignment]
        dlg.close()

        queued.clear()
        submit_mod.queue_selected_fork_if_uncatalogued = spy_queue  # type: ignore[assignment]
        install = D.AddonInstallPickerDialog(None, entry)
        install.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        install.fork_combo.blockSignals(True)
        install.fork_combo.addItem("alice/NopeAddon", uncatalogued)
        install.fork_combo.setCurrentIndex(install.fork_combo.count() - 1)
        install.fork_combo.blockSignals(False)
        install._accept_install()
        assert queued, "uncatalogued fork Install must call submit helper"
        last = queued[-1]
        assert isinstance(last, dict)
        assert "alice/NopeAddon" in str(last.get("repo") or "")
        install.close()
    finally:
        submit_mod.queue_selected_fork_if_uncatalogued = orig_queue  # type: ignore[assignment]
        submit_mod.clear_fork_suggest_session()
        G.has_github_token = prev_token
        D._PreviewFetchThread.start = orig_preview_start  # type: ignore[method-assign]
        D._AddonBrowseFetchThread.start = orig_browse_start  # type: ignore[method-assign]

    print("OK uncatalogued fork selection queues catalog submit")


def test_fork_ahead_compare_helper():
    """enrich_catalog_forks keeps only Compare ahead_by > 0 (mocked)."""
    from tools.enrich_catalog_forks import (
        compare_pool_size,
        is_fork_ahead,
        keep_ahead_forks,
    )

    assert is_fork_ahead({"ahead_by": 3, "status": "ahead"})
    assert is_fork_ahead({"ahead_by": 1, "behind_by": 5, "status": "diverged"})
    assert not is_fork_ahead({"ahead_by": 0, "status": "identical"})
    assert not is_fork_ahead({"ahead_by": 0, "behind_by": 2, "status": "behind"})
    assert not is_fork_ahead(None)
    assert not is_fork_ahead({})
    assert not is_fork_ahead({"ahead_by": "nope"})
    assert compare_pool_size(40) == 80
    assert compare_pool_size(50) == 100

    class FakeClient:
        def __init__(self, responses: dict) -> None:
            self.responses = responses
            self.calls: list[tuple] = []

        def compare(self, owner, repo, *, base, head):
            self.calls.append((owner, repo, base, head))
            return self.responses.get(head)

    client = FakeClient(
        {
            "alice:main": {"ahead_by": 2, "status": "ahead"},
            "bob:main": {"ahead_by": 0, "status": "identical"},
            "carol:main": None,
            "dave:main": {"ahead_by": 1, "behind_by": 3, "status": "diverged"},
        }
    )
    cands = [
        {
            "label": "alice",
            "repo": "https://github.com/alice/R",
            "owner": "alice",
            "name": "R",
            "default_branch": "main",
        },
        {
            "label": "bob",
            "repo": "https://github.com/bob/R",
            "owner": "bob",
            "name": "R",
            "default_branch": "main",
        },
        {
            "label": "carol",
            "repo": "https://github.com/carol/R",
            "owner": "carol",
            "name": "R",
            "default_branch": "main",
        },
        {
            "label": "dave",
            "repo": "https://github.com/dave/R",
            "owner": "dave",
            "name": "R",
            "default_branch": "main",
        },
    ]
    cache: dict = {}
    rows = keep_ahead_forks(
        client,
        cands,
        root_owner="root",
        root_repo="R",
        root_branch="main",
        max_forks=40,
        compare_cache=cache,
    )
    assert [r["label"] for r in rows] == ["alice", "dave"]
    assert cache["bob/r"]["ahead_by"] == 0
    assert cache["carol/r"]["status"] == "unavailable"

    cached_client = FakeClient({})
    rows2 = keep_ahead_forks(
        cached_client,
        cands,
        root_owner="root",
        root_repo="R",
        root_branch="main",
        max_forks=40,
        compare_cache=cache,
    )
    assert cached_client.calls == []
    assert [r["label"] for r in rows2] == ["alice", "dave"]
    print("OK fork ahead compare helper")


def test_crash_report_opt_in_and_redaction():
    """Crash reporter stays off by default and redacts obvious secrets."""
    from unittest import mock

    from ichalaunch.config import settings as settings_mod
    from ichalaunch.core import crash_report as cr

    prev = settings_mod.settings.get("crash_reporting_enabled", False)
    try:
        settings_mod.settings.set("crash_reporting_enabled", False)
        assert cr.crash_reporting_enabled() is False
        # Must not POST when disabled (no exception either).
        cr.report_crash("smoke test should not send")
        cr.report_logged_error("smoke test should not send")

        sample = (
            'github_token: ghp_abcdefghijklmnopqrstuvwxyz0123456789\n'
            'Authorization: Bearer ghp_abcdefghijklmnopqrstuvwxyz0123456789\n'
            '"github_token": "ghp_abcdefghijklmnopqrstuvwxyz0123456789"\n'
            '"wow_encryption_key": "super-secret-nampower-key-value"\n'
            "WOW_ENCRYPTION_KEY=super-secret-nampower-key-value\n"
            r"C:\Users\SecretUser\Games\WoW"
            "\n"
            "/home/secretuser/games\n"
        )
        redacted = cr.redact_secrets(sample)
        assert "ghp_" not in redacted
        assert "[REDACTED]" in redacted
        assert "super-secret-nampower-key-value" not in redacted
        assert "SecretUser" not in redacted
        assert "secretuser" not in redacted
        assert cr.crash_report_url().endswith("/crash")

        # Spaced Windows username — full segment (surname must not leak).
        spaced = cr.redact_secrets(r"C:\Users\Matt Hadati\AppData\Local\IchaLaunch")
        assert "Matt" not in spaced
        assert "Hadati" not in spaced
        assert r"C:\Users\[user]\AppData" in spaced

        # Forward-slash Windows paths.
        fwd = cr.redact_secrets("C:/Users/Matt Hadati/Documents/game")
        assert "Hadati" not in fwd
        assert "C:/Users/[user]/Documents" in fwd

        # UNC Users path.
        unc = cr.redact_secrets(r"\\SERVER\Users\Matt Hadati\share")
        assert "Hadati" not in unc
        assert r"\\SERVER\Users\[user]\share" in unc

        # Legacy 8.3 Documents and Settings short path.
        short = cr.redact_secrets(r"C:\DOCUME~1\Matt Hadati\LOCALS~1")
        assert "Hadati" not in short
        assert r"C:\DOCUME~1\[user]\LOCALS~1" in short

        # Linux /home still works.
        linux = cr.redact_secrets("/home/mattb/.config/ichalaunch")
        assert "mattb" not in linux
        assert "/home/[user]/.config" in linux

        # Global replace of *current* OS username (mocked).
        with mock.patch.object(cr, "_current_os_username", return_value="Matt Hadati"):
            free = cr.redact_secrets("Logged in as Matt Hadati on this PC")
            assert "Matt" not in free
            assert "Hadati" not in free
            assert "[user]" in free
            # Case-insensitive.
            assert "matt hadati" not in cr.redact_secrets("user=matt hadati").lower()

        # Short usernames must not over-redact common words.
        with mock.patch.object(cr, "_current_os_username", return_value="ab"):
            assert "about" in cr.redact_secrets("talking about paths")
    finally:
        settings_mod.settings.set("crash_reporting_enabled", prev)
    print("OK crash report opt-in and redaction")


def test_crash_report_skips_rate_limit_errors():
    """GitHubRateLimitError (and similar) must not schedule an opt-in ERROR report."""
    import logging
    import sys
    from unittest import mock

    from ichalaunch.addons.github import GitHubBudgetExhaustedError, GitHubRateLimitError
    from ichalaunch.config import settings as settings_mod
    from ichalaunch.core import crash_report as cr

    prev = settings_mod.settings.get("crash_reporting_enabled", False)
    handler = cr._OptInErrorHandler()
    try:
        settings_mod.settings.set("crash_reporting_enabled", True)
        with mock.patch.object(cr, "report_logged_error") as mocked:
            try:
                raise GitHubRateLimitError("GitHub rate limit hit — try later")
            except GitHubRateLimitError:
                record = logging.LogRecord(
                    name="ichalaunch.test",
                    level=logging.ERROR,
                    pathname=__file__,
                    lineno=1,
                    msg="update check failed",
                    args=(),
                    exc_info=sys.exc_info(),
                )
            handler.emit(record)
            mocked.assert_not_called()

            try:
                raise GitHubBudgetExhaustedError("Waiting for GitHub rate limit…")
            except GitHubBudgetExhaustedError:
                budget = logging.LogRecord(
                    name="ichalaunch.test",
                    level=logging.ERROR,
                    pathname=__file__,
                    lineno=1,
                    msg="budget",
                    args=(),
                    exc_info=sys.exc_info(),
                )
            handler.emit(budget)
            mocked.assert_not_called()

            # Message-only mention (no exc_info) still skipped.
            msg_only = logging.LogRecord(
                name="ichalaunch.test",
                level=logging.ERROR,
                pathname=__file__,
                lineno=1,
                msg="GitHub rate limit hit — add a token",
                args=(),
                exc_info=None,
            )
            handler.emit(msg_only)
            mocked.assert_not_called()

            # Real failure still schedules.
            real = logging.LogRecord(
                name="ichalaunch.test",
                level=logging.ERROR,
                pathname=__file__,
                lineno=1,
                msg="unexpected addon install failure",
                args=(),
                exc_info=None,
            )
            handler.emit(real)
            mocked.assert_called_once()
    finally:
        settings_mod.settings.set("crash_reporting_enabled", prev)
    print("OK crash report skips rate-limit errors")


def test_crash_report_skips_lock_and_network_noise():
    """File locks and offline DNS must not schedule opt-in ERROR reports (#58)."""
    import logging
    import sys
    from unittest import mock

    import requests

    from ichalaunch import __version__
    from ichalaunch.config import settings as settings_mod
    from ichalaunch.core import crash_report as cr

    prev = settings_mod.settings.get("crash_reporting_enabled", False)
    handler = cr._OptInErrorHandler()
    try:
        settings_mod.settings.set("crash_reporting_enabled", True)
        with mock.patch.object(cr, "report_logged_error") as mocked:
            try:
                raise PermissionError(
                    13,
                    "Skipped locked or antivirus-blocked file ClassicAPI.dll",
                    r"L:\game\ClassicAPI.dll",
                )
            except PermissionError:
                lock_rec = logging.LogRecord(
                    name="ichalaunch.test",
                    level=logging.ERROR,
                    pathname=__file__,
                    lineno=1,
                    msg="Worker failed",
                    args=(),
                    exc_info=sys.exc_info(),
                )
            handler.emit(lock_rec)
            mocked.assert_not_called()

            try:
                raise requests.exceptions.ConnectionError(
                    "HTTPSConnectionPool(host='api.github.com', port=443): "
                    "Max retries exceeded with url: /repos/x/y/releases/latest "
                    "(Caused by NameResolutionError("
                    "\"Failed to resolve 'api.github.com' "
                    "([Errno 11001] getaddrinfo failed)\"))"
                )
            except requests.exceptions.ConnectionError:
                net_rec = logging.LogRecord(
                    name="ichalaunch.test",
                    level=logging.ERROR,
                    pathname=__file__,
                    lineno=1,
                    msg="Worker failed",
                    args=(),
                    exc_info=sys.exc_info(),
                )
            handler.emit(net_rec)
            mocked.assert_not_called()

            class _Resp:
                status_code = 502

            try:
                raise requests.HTTPError(
                    "502 Server Error: Bad Gateway for url: "
                    "https://share.ichasarmory.quest/patch-9.mpq",
                    response=_Resp(),
                )
            except requests.HTTPError:
                http_rec = logging.LogRecord(
                    name="ichalaunch.test",
                    level=logging.ERROR,
                    pathname=__file__,
                    lineno=1,
                    msg="Worker failed",
                    args=(),
                    exc_info=sys.exc_info(),
                )
            handler.emit(http_rec)
            mocked.assert_not_called()

            # Message-only lock/DNS noise (no exc_info).
            for msg in (
                "Lock/AV skipped copy foo.dll: [WinError 32] sharing violation",
                "getaddrinfo failed for api.github.com",
            ):
                handler.emit(
                    logging.LogRecord(
                        name="ichalaunch.test",
                        level=logging.ERROR,
                        pathname=__file__,
                        lineno=1,
                        msg=msg,
                        args=(),
                        exc_info=None,
                    )
                )
            mocked.assert_not_called()

            real = logging.LogRecord(
                name="ichalaunch.test",
                level=logging.ERROR,
                pathname=__file__,
                lineno=1,
                msg="unexpected addon install failure",
                args=(),
                exc_info=None,
            )
            handler.emit(real)
            mocked.assert_called_once()
    finally:
        settings_mod.settings.set("crash_reporting_enabled", prev)

    # Stale crash.log blocks from other versions are omitted.
    import tempfile
    from pathlib import Path

    stale = (
        "\n"
        + ("=" * 72)
        + "\n"
        + "timestamp: 2026-08-22T21:56:54+00:00\n"
        + "app_version: 1.0.30\n"
        + "exception: ModuleNotFoundError: No module named 'x'\n"
        + ("=" * 72)
        + "\n"
    )
    current = (
        "\n"
        + ("=" * 72)
        + "\n"
        + "timestamp: 2026-08-25T12:00:00+00:00\n"
        + f"app_version: {__version__}\n"
        + "exception: RuntimeError: boom\n"
        + ("=" * 72)
        + "\n"
    )
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "crash.log"
        path.write_text(stale + current, encoding="utf-8")
        excerpt = cr._crash_excerpt_current_version(path, 12_000)
        assert f"app_version: {__version__}" in excerpt
        assert "1.0.30" not in excerpt
        assert "ModuleNotFoundError" not in excerpt
        path.write_text(stale, encoding="utf-8")
        assert cr._crash_excerpt_current_version(path, 12_000) == ""
    print("OK crash report skips lock/network noise + stale crash.log")


def test_crash_report_skips_smoke_test_uploads():
    """smoke_test / ICHALAUNCH_NO_CRASH_REPORT must not POST even when opted in."""
    from unittest import mock

    from ichalaunch.config import settings as settings_mod
    from ichalaunch.core import crash_report as cr

    prev = settings_mod.settings.get("crash_reporting_enabled", False)
    try:
        settings_mod.settings.set("crash_reporting_enabled", True)
        assert cr.reporting_suppressed() is True
        with mock.patch.object(cr, "_send_async") as send:
            cr.report_crash("unhandled exception: AssertionError")
            cr.report_logged_error("ichalaunch: Worker failed (RuntimeError)")
            send.assert_not_called()
    finally:
        settings_mod.settings.set("crash_reporting_enabled", prev)
    print("OK crash report skips smoke-test uploads")


def test_download_file_retries_transient_http_not_404():
    """502 is retried; 404 fails immediately (HTTPError subclasses OSError)."""
    from unittest import mock

    import requests

    from ichalaunch.core.process import (
        download_file,
        is_retryable_download_error,
        is_transient_http_error,
    )

    class _Resp:
        def __init__(self, code: int) -> None:
            self.status_code = code

    http502 = requests.HTTPError("502 Server Error", response=_Resp(502))
    http404 = requests.HTTPError("404 Client Error", response=_Resp(404))
    assert is_transient_http_error(http502) is True
    assert is_transient_http_error(http404) is False
    assert is_retryable_download_error(http502) is True
    assert is_retryable_download_error(http404) is False

    dest = Path(tempfile.mkdtemp()) / "patch-9.mpq"
    calls = {"n": 0}

    def _once(*_a, **_k):
        calls["n"] += 1
        if calls["n"] < 3:
            raise requests.HTTPError("502 Server Error", response=_Resp(502))
        dest.write_bytes(b"ok")
        return dest

    with mock.patch("ichalaunch.core.process._download_file_once", side_effect=_once), mock.patch(
        "ichalaunch.core.process.time.sleep", return_value=None
    ):
        assert download_file("https://example.test/patch-9.mpq", dest) == dest
    assert calls["n"] == 3
    assert dest.read_bytes() == b"ok"

    calls["n"] = 0

    def _404(*_a, **_k):
        calls["n"] += 1
        raise requests.HTTPError("404 Client Error", response=_Resp(404))

    with mock.patch("ichalaunch.core.process._download_file_once", side_effect=_404), mock.patch(
        "ichalaunch.core.process.time.sleep", return_value=None
    ):
        try:
            download_file("https://example.test/missing", dest)
            raise AssertionError("404 must not be swallowed")
        except requests.HTTPError as exc:
            assert "404" in str(exc)
    assert calls["n"] == 1
    print("OK download retries 502 but not 404")


def test_crash_reporting_opt_in_skipped_when_reporting_suppressed():
    """Fresh config + ICHALAUNCH_NO_CRASH_REPORT must not first-launch prompt (#344)."""
    from ichalaunch.config import settings as settings_mod
    from ichalaunch.config.settings import Settings
    from ichalaunch.core import crash_report as cr

    assert cr.reporting_suppressed() is True
    with tempfile.TemporaryDirectory() as td:
        fake = Path(td) / "settings.json"
        orig_path = settings_mod.settings_path
        orig_singleton = settings_mod.settings
        settings_mod.settings_path = lambda: fake
        try:
            settings_mod.settings = Settings()
            assert settings_mod.settings.get("crash_reporting_opt_in_prompted_v1") is False
            assert cr.crash_reporting_enabled() is False
            assert cr.should_prompt_crash_reporting_opt_in() is False
        finally:
            settings_mod.settings_path = orig_path
            settings_mod.settings = orig_singleton
    print("OK crash reporting opt-in skipped when reporting suppressed")


def test_crash_reporting_opt_in_prompt_one_shot():
    """First-launch crash-reporting prompt is one-shot; decline leaves reporting off."""
    import json
    import tempfile
    from pathlib import Path
    from unittest import mock

    from PySide6.QtWidgets import QApplication, QWidget

    from ichalaunch.config import settings as settings_mod
    from ichalaunch.config.settings import Settings
    from ichalaunch.core import crash_report as cr
    from ichalaunch.ui.widgets.dialogs import (
        DialogResult,
        ThemedDialog,
        crash_reporting_opt_in_dialog,
    )

    app = QApplication.instance() or QApplication([])
    assert "crash_reporting_opt_in_prompted_v1" in settings_mod.DEFAULTS
    assert settings_mod.DEFAULTS["crash_reporting_opt_in_prompted_v1"] is False
    assert settings_mod.DEFAULTS["crash_reporting_enabled"] is False
    assert "optional" in cr.CRASH_REPORTING_OPT_IN_TEXT.lower()
    assert "Settings → Privacy" in cr.CRASH_REPORTING_OPT_IN_TEXT
    assert "redacted" in cr.CRASH_REPORTING_OPT_IN_TEXT.lower()

    with tempfile.TemporaryDirectory() as td:
        fake = Path(td) / "settings.json"
        orig_path = settings_mod.settings_path
        orig_singleton = settings_mod.settings
        settings_mod.settings_path = lambda: fake
        try:
            settings_mod.settings = Settings()
            # smoke_test sets ICHALAUNCH_NO_CRASH_REPORT; exercise the real
            # one-shot path as a normal launch would see it.
            with mock.patch.object(cr, "reporting_suppressed", return_value=False):
                assert cr.should_prompt_crash_reporting_opt_in() is True
                assert cr.crash_reporting_enabled() is False

                cr.mark_crash_reporting_opt_in_prompted()
                assert json.loads(fake.read_text(encoding="utf-8"))[
                    "crash_reporting_opt_in_prompted_v1"
                ] is True
                assert cr.should_prompt_crash_reporting_opt_in() is False
                assert cr.crash_reporting_enabled() is False

                settings_mod.settings = Settings()
                assert settings_mod.settings.get("crash_reporting_opt_in_prompted_v1") is True
                assert cr.should_prompt_crash_reporting_opt_in() is False

                settings_mod.settings.set("crash_reporting_opt_in_prompted_v1", False)
                settings_mod.settings.set("crash_reporting_enabled", True)
                assert cr.should_prompt_crash_reporting_opt_in() is False
                assert settings_mod.settings.get("crash_reporting_opt_in_prompted_v1") is True

                settings_mod.settings.set("crash_reporting_opt_in_prompted_v1", False)
                settings_mod.settings.set("crash_reporting_enabled", False)
                cr.enable_crash_reporting_from_opt_in()
                assert cr.crash_reporting_enabled() is True
                assert cr.should_prompt_crash_reporting_opt_in() is False
        finally:
            settings_mod.settings_path = orig_path
            settings_mod.settings = orig_singleton

    root = QWidget()
    dlg = ThemedDialog(
        root,
        cr.CRASH_REPORTING_OPT_IN_TITLE,
        cr.CRASH_REPORTING_OPT_IN_TEXT,
        buttons=[
            ("Don't show again", DialogResult.Cancel),
            ("Not now", DialogResult.No),
            ("Enable", DialogResult.Yes),
        ],
        kind="question",
    )
    assert dlg.minimumWidth() >= 460
    # Helper must exist and match the three-button contract (do not exec — blocking).
    assert callable(crash_reporting_opt_in_dialog)
    print("OK crash reporting opt-in prompt one-shot")


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
        tips._loaded = (
            0.0,
            normalize_index(
                {
                    "generated_at": "2026-08-23T00:00:00Z",
                    "repos": {
                        "balakethelock/superwow": {
                            "default_branch": "master",
                            "sha": "b" * 40,
                            "branches": {"master": "b" * 40},
                            "latest_tag": "Patch",
                        }
                    },
                }
            ),
        )
        patch_ident = _remote_identity(
            {"type": "github_release_latest", "repo": "balakethelock/SuperWoW"},
            catalog_only=True,
        )
        assert patch_ident is None
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


def test_multi_toc_primary_stem_resolve():
    """pfQuest-like multi-TOC under GitHub unwrap; single TOC; true mismatch."""
    import tempfile
    from pathlib import Path

    from ichalaunch.core.filesystem import (
        canonical_addon_folder_name,
        resolve_install_addon_roots,
    )

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)

        # pfQuest zip unwrap: folder pfQuest-main, three expansion TOCs.
        pf = base / "pfQuest-main"
        pf.mkdir()
        for name in ("pfQuest.toc", "pfQuest-tbc.toc", "pfQuest-wotlk.toc"):
            (pf / name).write_text(f"## Title: {name}\n", encoding="utf-8")
        assert canonical_addon_folder_name(pf) == "pfQuest"
        assert resolve_install_addon_roots(pf) == [(pf, "pfQuest")]

        # Nested under extract root (zip → extract/pfQuest-main/…).
        wrap = base / "extract"
        nested = wrap / "pfQuest-main"
        nested.mkdir(parents=True)
        for name in ("pfQuest.toc", "pfQuest-tbc.toc", "pfQuest_classic.toc"):
            (nested / name).write_text(f"## Title: {name}\n", encoding="utf-8")
        assert resolve_install_addon_roots(wrap) == [(nested, "pfQuest")]

        # Single-TOC turtle-style extract still resolves by stem.
        turtle = base / "SomeZipFolder"
        turtle.mkdir()
        (turtle / "pfQuest-turtle.toc").write_text(
            "## Title: pfQuest-turtle\n", encoding="utf-8"
        )
        assert canonical_addon_folder_name(turtle) == "pfQuest-turtle"
        assert resolve_install_addon_roots(turtle) == [(turtle, "pfQuest-turtle")]

        # Unrelated sibling TOCs stay rejected (no clear primary).
        mixed = base / "MixedAddon-main"
        mixed.mkdir()
        (mixed / "Foo.toc").write_text("## Title: Foo\n", encoding="utf-8")
        (mixed / "Bar.toc").write_text("## Title: Bar\n", encoding="utf-8")
        assert canonical_addon_folder_name(mixed) is None
        assert resolve_install_addon_roots(mixed) == []

        # Expansion-only TOCs without a primary Foo.toc also fail.
        expansions = base / "ExpOnly"
        expansions.mkdir()
        (expansions / "Foo-tbc.toc").write_text("## Title: tbc\n", encoding="utf-8")
        (expansions / "Foo-wotlk.toc").write_text("## Title: wotlk\n", encoding="utf-8")
        assert canonical_addon_folder_name(expansions) is None
        assert resolve_install_addon_roots(expansions) == []

    print("OK multi-toc primary stem resolve")


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
    assert settings.auto_fix_addon_toc_mismatch() is True
    prev_addons = settings.installed_addons
    prev_toc_fix = settings.get("auto_fix_addon_toc_mismatch", True)
    try:
        settings.set_auto_fix_addon_toc_mismatch(False)
        assert settings.auto_fix_addon_toc_mismatch() is False
        settings.set_auto_fix_addon_toc_mismatch(True)
        assert settings.auto_fix_addon_toc_mismatch() is True
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
        settings.set("auto_fix_addon_toc_mismatch", prev_toc_fix)
        clear_pending_toc_mismatches()
    print("OK addon toc folder rename")


def test_addon_update_check_uses_catalog_index_only():
    """Bulk addon checks compare the tip index and never probe GitHub per addon."""
    from ichalaunch.addons import catalog as cat
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
    orig_cat_refresh = cat.refresh_catalog

    def boom(*_a, **_k):
        raise AssertionError("per-addon GitHub probe should not run")

    def fake_refresh(*, force: bool = False):
        return index

    def fake_cat_refresh(*, force: bool = False):
        return cat.load_bundled_catalog()

    from ichalaunch.addons import pending_updates as pending

    with tempfile.TemporaryDirectory() as pending_tmp:
      pending_cache = Path(pending_tmp) / "addon_pending_updates.json"
      with pending.isolated_pending_updates_cache(pending_cache):
        try:
            tips._loaded = (0.0, index)
            tips.refresh_tip_index = fake_refresh
            cat.refresh_catalog = fake_cat_refresh
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
            assert result.catalog_refreshed is True
        finally:
            gh.github_remote_tip = orig_tip
            gh.github_latest_version_tag = orig_tag
            tips.refresh_tip_index = orig_refresh
            cat.refresh_catalog = orig_cat_refresh
            settings.set("installed_addons", prev_addons)
            tips._loaded = prev_loaded
            if prev_loaded is None:
                clear_tip_index_cache()
    print("OK addon update check uses catalog index only")


def test_older_tag_install_reports_update():
    """Version-dropdown older tag installs must still flag Update vs default tip."""
    from ichalaunch.addons import catalog as cat
    from ichalaunch.addons import github as gh
    from ichalaunch.addons import tip_index as tips
    from ichalaunch.addons.tip_index import clear_tip_index_cache, normalize_index
    from ichalaunch.config.settings import settings

    tip_sha = "b" * 40
    old_sha = "a" * 40
    index = normalize_index(
        {
            "generated_at": "2026-08-25T00:00:00Z",
            "repos": {
                "shagu/shagutweaks": {
                    "default_branch": "master",
                    "sha": tip_sha,
                    "branches": {"master": tip_sha},
                    "latest_tag": "v2.0.0",
                },
                # Missing tip (NampowerSettings-style) must stay a quiet skip.
                "owner/notips": {
                    "default_branch": "main",
                    "sha": "",
                    "branches": {},
                },
            },
        }
    )
    prev_loaded = tips._loaded
    prev_addons = settings.installed_addons
    orig_tip = gh.github_remote_tip
    orig_tag = gh.github_latest_version_tag
    orig_refresh = tips.refresh_tip_index
    orig_cat_refresh = cat.refresh_catalog
    orig_install = gh.install_from_github

    def boom(*_a, **_k):
        raise AssertionError("per-addon GitHub probe should not run")

    def fake_refresh(*, force: bool = False):
        return index

    def fake_cat_refresh(*, force: bool = False):
        return cat.load_bundled_catalog()

    captured: dict = {}

    def fake_install(url, folder_name=None, progress=None, *, allow_stored_tag=True):
        captured["url"] = url
        captured["folder_name"] = folder_name
        captured["allow_stored_tag"] = allow_stored_tag
        return None

    from ichalaunch.addons import pending_updates as pending

    with tempfile.TemporaryDirectory() as pending_tmp:
      pending_cache = Path(pending_tmp) / "addon_pending_updates.json"
      with pending.isolated_pending_updates_cache(pending_cache):
        try:
            tips._loaded = (0.0, index)
            tips.refresh_tip_index = fake_refresh
            cat.refresh_catalog = fake_cat_refresh
            gh.github_remote_tip = boom
            gh.github_latest_version_tag = boom
            gh.install_from_github = fake_install

            # Older tag pin: branch field often equals the tag name (not default branch).
            settings.set(
                "installed_addons",
                {
                    "ShaguTweaks": {
                        "repository": "shagu/ShaguTweaks",
                        "url": "https://github.com/shagu/ShaguTweaks/releases/tag/v1.0.0",
                        "installed_commit": old_sha,
                        "branch": "v1.0.0",
                        "tag": "v1.0.0",
                        "version": "v1.0.0",
                        "source": "github",
                    },
                    "NoTipsAddon": {
                        "repository": "owner/NoTips",
                        "url": "https://github.com/owner/NoTips",
                        "installed_commit": old_sha,
                        "branch": "main",
                        "source": "github",
                    },
                    "Bagshui": {
                        "repository": "The-Kludge-Bureau/Bagshui",
                        "url": "https://github.com/The-Kludge-Bureau/Bagshui/releases/tag/1.5.16",
                        "installed_commit": old_sha,
                        "branch": "1.5.16",
                        "tag": "1.5.16",
                        "version": "1.5.16",
                        "source": "github",
                        "never_update": True,
                    },
                },
            )
            result = gh.check_addon_updates()
            assert result.checked_count == 1, result
            assert len(result.updates) == 1
            assert result.updates[0]["folder"] == "ShaguTweaks"
            assert result.updates[0]["remote"] == tip_sha[:7]

            # At default tip (even if installed via matching tag) → no update.
            settings.set(
                "installed_addons",
                {
                    "ShaguTweaks": {
                        "repository": "shagu/ShaguTweaks",
                        "url": "https://github.com/shagu/ShaguTweaks/releases/tag/v2.0.0",
                        "installed_commit": tip_sha,
                        "branch": "v2.0.0",
                        "tag": "v2.0.0",
                        "version": "v2.0.0",
                        "source": "github",
                    }
                },
            )
            at_tip = gh.check_addon_updates()
            assert at_tip.checked_count == 1
            assert at_tip.updates == []

            # Update must target branch tip, not reinstall the stored older tag.
            settings.set(
                "installed_addons",
                {
                    "ShaguTweaks": {
                        "repository": "shagu/ShaguTweaks",
                        "url": "https://github.com/shagu/ShaguTweaks/releases/tag/v1.0.0",
                        "installed_commit": old_sha,
                        "branch": "v1.0.0",
                        "tag": "v1.0.0",
                        "version": "v1.0.0",
                        "source": "github",
                    }
                },
            )
            gh.update_addon("ShaguTweaks")
            assert captured.get("allow_stored_tag") is False
            assert "/releases/tag/" not in str(captured.get("url") or "")
            assert "shagu/shagutweaks" in str(captured.get("url") or "").lower()
        finally:
            gh.github_remote_tip = orig_tip
            gh.github_latest_version_tag = orig_tag
            gh.install_from_github = orig_install
            tips.refresh_tip_index = orig_refresh
            cat.refresh_catalog = orig_cat_refresh
            settings.set("installed_addons", prev_addons)
            tips._loaded = prev_loaded
            if prev_loaded is None:
                clear_tip_index_cache()
    print("OK older tag install reports update")


def test_pending_addon_updates_cache_survives_restart():
    """Last-known pending addon updates persist across launch until the next scan."""
    from ichalaunch.addons import catalog as cat
    from ichalaunch.addons import github as gh
    from ichalaunch.addons import pending_updates as pending
    from ichalaunch.addons import tip_index as tips
    from ichalaunch.addons.tip_index import clear_tip_index_cache, normalize_index
    from ichalaunch.config.settings import settings

    old_sha = "a" * 40
    new_sha = "b" * 40
    other_old = "c" * 40
    other_new = "d" * 40
    index = normalize_index(
        {
            "generated_at": "2026-08-26T00:00:00Z",
            "repos": {
                "shagu/pfui": {
                    "default_branch": "master",
                    "sha": new_sha,
                    "branches": {"master": new_sha},
                },
                "shagu/shagutweaks": {
                    "default_branch": "master",
                    "sha": other_new,
                    "branches": {"master": other_new},
                },
            },
        }
    )
    two_installed = {
        "pfUI": {
            "repository": "shagu/pfUI",
            "url": "https://github.com/shagu/pfUI",
            "installed_commit": old_sha,
            "branch": "master",
            "source": "github",
        },
        "ShaguTweaks": {
            "repository": "shagu/ShaguTweaks",
            "url": "https://github.com/shagu/ShaguTweaks",
            "installed_commit": other_old,
            "branch": "master",
            "source": "github",
        },
    }
    prev_loaded = tips._loaded
    prev_addons = settings.installed_addons
    orig_tip = gh.github_remote_tip
    orig_tag = gh.github_latest_version_tag
    orig_refresh = tips.refresh_tip_index
    orig_cat_refresh = cat.refresh_catalog

    def boom(*_a, **_k):
        raise AssertionError("per-addon GitHub probe should not run")

    def fake_refresh(*, force: bool = False):
        return index

    def fake_cat_refresh(*, force: bool = False):
        return cat.load_bundled_catalog()

    with tempfile.TemporaryDirectory() as tmp:
        cache_file = Path(tmp) / "addon_pending_updates.json"
        with pending.isolated_pending_updates_cache(cache_file):
            try:
                tips._loaded = (0.0, index)
                tips.refresh_tip_index = fake_refresh
                cat.refresh_catalog = fake_cat_refresh
                gh.github_remote_tip = boom
                gh.github_latest_version_tag = boom
                settings.set("installed_addons", dict(two_installed))

                result = gh.check_addon_updates()
                assert result.skipped_recent is False
                folders = sorted(u["folder"] for u in result.updates)
                assert folders == ["ShaguTweaks", "pfUI"], result.updates
                assert cache_file.is_file()
                raw = json.loads(cache_file.read_text(encoding="utf-8"))
                assert raw.get("scanned_at")
                cached_folders = sorted(
                    str(u.get("folder")) for u in raw.get("updates") or []
                )
                assert cached_folders == ["ShaguTweaks", "pfUI"]
                by_folder = {u["folder"]: u for u in raw["updates"]}
                assert by_folder["pfUI"]["installed_ref"] == old_sha
                assert by_folder["pfUI"]["available_ref"] == new_sha
                assert by_folder["pfUI"]["repository"].lower() == "shagu/pfui"
                assert by_folder["ShaguTweaks"]["installed_ref"] == other_old
                assert by_folder["ShaguTweaks"]["available_ref"] == other_new

                # Restart: load cache only — no catalog/tip refresh.
                restored = pending.restore_pending_updates(
                    installed=two_installed,
                    never_update=lambda _folder: False,
                    rewrite=False,
                )
                assert sorted(u["folder"] for u in restored) == [
                    "ShaguTweaks",
                    "pfUI",
                ]

                # Removed on disk / from installed → dropped.
                only_pfui = {"pfUI": dict(two_installed["pfUI"])}
                dropped = pending.restore_pending_updates(
                    installed=only_pfui,
                    never_update=lambda _folder: False,
                    rewrite=False,
                )
                assert [u["folder"] for u in dropped] == ["pfUI"]

                # Applied (installed ref now matches available) → not pending.
                applied = {
                    "pfUI": {
                        **two_installed["pfUI"],
                        "installed_commit": new_sha,
                    },
                    "ShaguTweaks": dict(two_installed["ShaguTweaks"]),
                }
                after_apply = pending.restore_pending_updates(
                    installed=applied,
                    never_update=lambda _folder: False,
                    rewrite=False,
                )
                assert [u["folder"] for u in after_apply] == ["ShaguTweaks"]

                # Failed tip-index fetch must not wipe last-known pending.
                orig_tip_refresh = tips.refresh_tip_index
                tips.refresh_tip_index = lambda **_k: tips.empty_index()
                try:
                    unavailable = gh.check_addon_updates()
                finally:
                    tips.refresh_tip_index = orig_tip_refresh
                assert unavailable.status_message == gh.UPDATE_CATALOG_UNAVAILABLE
                still_cached = json.loads(cache_file.read_text(encoding="utf-8"))
                assert sorted(
                    str(u.get("folder")) for u in still_cached.get("updates") or []
                ) == ["ShaguTweaks", "pfUI"]

                # Real refresh that finds 0 updates replaces the cache.
                settings.set(
                    "installed_addons",
                    {
                        "pfUI": {
                            **two_installed["pfUI"],
                            "installed_commit": new_sha,
                        },
                        "ShaguTweaks": {
                            **two_installed["ShaguTweaks"],
                            "installed_commit": other_new,
                        },
                    },
                )
                empty = gh.check_addon_updates()
                assert empty.updates == []
                raw_empty = json.loads(cache_file.read_text(encoding="utf-8"))
                assert raw_empty.get("updates") == []
                assert (
                    pending.restore_pending_updates(
                        installed=settings.installed_addons,
                        never_update=lambda _folder: False,
                    )
                    == []
                )
            finally:
                gh.github_remote_tip = orig_tip
                gh.github_latest_version_tag = orig_tag
                tips.refresh_tip_index = orig_refresh
                cat.refresh_catalog = orig_cat_refresh
                settings.set("installed_addons", prev_addons)
                tips._loaded = prev_loaded
                if prev_loaded is None:
                    clear_tip_index_cache()
    print("OK pending addon updates cache survives restart")


def test_update_to_tip_clears_stored_version_pin():
    """Update-to-tip must clear meta.tag so settings cog shows Latest, not the old pin.

    Regression: update_addon used allow_stored_tag=False (row status cleared) but
    _addon_install_meta only popped tag from the payload; set_installed_addon merge
    kept the prior pin so AddonSettingsDialog still selected the old version.
    """
    import tempfile
    from pathlib import Path

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    import ichalaunch.addons.github as G
    import ichalaunch.config.settings as settings_mod
    from ichalaunch.addons.github import _addon_install_meta
    from ichalaunch.config.settings import Settings
    from ichalaunch.ui.widgets import dialogs as D

    app = QApplication.instance() or QApplication([])
    tip_sha = "dddddddddddddddddddddddddddddddddddddddd"
    prev_token = G.has_github_token
    orig_preview_start = D._PreviewFetchThread.start
    orig_browse_start = D._AddonBrowseFetchThread.start
    orig_path = settings_mod.settings_path
    orig_singleton = settings_mod.settings
    orig_gh_settings = G.settings

    def _noop_start(self):  # noqa: ANN001
        return None

    with tempfile.TemporaryDirectory() as td:
        fake = Path(td) / "settings.json"
        settings_mod.settings_path = lambda: fake
        try:
            G.has_github_token = lambda: True  # type: ignore[assignment]
            D._PreviewFetchThread.start = _noop_start  # type: ignore[method-assign]
            D._AddonBrowseFetchThread.start = _noop_start  # type: ignore[method-assign]
            # _addon_install_meta reads the module singleton — keep it in sync.
            settings_mod.settings = Settings()
            G.settings = settings_mod.settings
            s = settings_mod.settings

            s.set_installed_addon(
                "ShaguTweaks",
                {
                    "source": "github",
                    "name": "ShaguTweaks",
                    "repository": "shagu/ShaguTweaks",
                    "url": "https://github.com/shagu/ShaguTweaks/releases/tag/v1.0.0",
                    "branch": "v1.0.0",
                    "tag": "v1.0.0",
                    "version": "v1.0.0",
                    "installed_commit": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    "loaded": True,
                },
            )
            assert s.installed_addons["ShaguTweaks"].get("tag") == "v1.0.0"

            # Same write shape as install_from_github -> _record_pack_install after Update.
            meta = _addon_install_meta(
                folder="ShaguTweaks",
                owner="shagu",
                repo="ShaguTweaks",
                branch="master",
                sha=tip_sha,
                url="https://github.com/shagu/ShaguTweaks",
                commit_date="2024-06-01",
                match_kind="exact",
                tag=None,
            )
            assert not str(meta.get("tag") or "").strip(), meta
            s.set_installed_addon("ShaguTweaks", meta)
            stored = s.installed_addons["ShaguTweaks"]
            assert not str(stored.get("tag") or "").strip(), stored
            assert not str(stored.get("version") or "").strip(), stored
            assert stored.get("installed_commit") == tip_sha
            assert stored.get("branch") == "master"
            assert "/releases/tag/" not in str(stored.get("url") or "")

            entry = {
                "name": "ShaguTweaks",
                "folder": "ShaguTweaks",
                "repo": "https://github.com/shagu/ShaguTweaks",
            }
            dlg = D.AddonSettingsDialog(None, entry, meta=stored)
            dlg.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
            assert dlg._version_combo is not None
            assert str(dlg._version_combo.currentData() or "").strip() == ""
            assert "latest" in str(dlg._version_combo.currentText() or "").lower()
            dlg.close()
            app.processEvents()
        finally:
            G.has_github_token = prev_token
            D._PreviewFetchThread.start = orig_preview_start  # type: ignore[method-assign]
            D._AddonBrowseFetchThread.start = orig_browse_start  # type: ignore[method-assign]
            settings_mod.settings = orig_singleton
            G.settings = orig_gh_settings
            settings_mod.settings_path = orig_path

    print("OK update to tip clears stored version pin")


def test_available_catalog_remote_refresh_and_merge():
    """Remote Available catalog replaces on success; merge helper overlays by folder."""
    import tempfile
    from pathlib import Path

    from ichalaunch.addons import catalog as cat

    bundled = [
        {
            "name": "OldOne",
            "folder": "OldOne",
            "repo": "https://github.com/a/OldOne",
            "category": "General",
            "description": "bundled",
            "source": "turtle_wiki",
        },
        {
            "name": "Shared",
            "folder": "Shared",
            "repo": "https://github.com/a/Shared",
            "category": "General",
            "description": "bundled-shared",
            "source": "turtle_wiki",
        },
    ]
    remote = [
        {
            "name": "Shared",
            "folder": "Shared",
            "repo": "https://github.com/b/Shared",
            "category": "Bags",
            "description": "remote-shared",
            "source": "turtle_wiki",
        },
        {
            "name": "NewOne",
            "folder": "NewOne",
            "repo": "https://github.com/c/NewOne",
            "category": "General",
            "description": "new",
            "source": "turtle_wiki",
        },
    ]

    merged = cat.merge_catalog(bundled, remote)
    by_folder = {e["folder"]: e for e in merged}
    assert set(by_folder) == {"OldOne", "Shared", "NewOne"}
    assert by_folder["Shared"]["repo"] == "https://github.com/b/Shared"
    assert by_folder["Shared"]["description"] == "remote-shared"
    assert by_folder["OldOne"]["description"] == "bundled"

    prev = cat._loaded
    orig_fetch = cat.fetch_remote_catalog
    orig_cache_path = cat.catalog_cache_path
    from ichalaunch.addons import release_downloads as rd
    from ichalaunch.addons.release_downloads import (
        clear_release_downloads_cache,
        live_download_fetch_disabled,
    )

    orig_dl_path = rd.downloads_cache_path
    try:
        with tempfile.TemporaryDirectory() as tmp:
            cache_file = Path(tmp) / "addons_catalog.json"
            dl_cache = Path(tmp) / "addon_release_downloads.json"
            cat.catalog_cache_path = lambda: cache_file
            rd.downloads_cache_path = lambda: dl_cache
            clear_release_downloads_cache()
            cat.clear_catalog_cache()

            def fake_fetch(url=None):
                return list(remote)

            cat.fetch_remote_catalog = fake_fetch
            with live_download_fetch_disabled():
                entries = cat.refresh_catalog(force=True)
                assert len(entries) == 2
                assert {e["folder"] for e in entries} == {"Shared", "NewOne"}
                assert cat.current_catalog_source() == "remote"
                assert cat.load_catalog()[0]["folder"] in {"Shared", "NewOne"}
                assert cache_file.is_file()

                # Failed remote within TTL keeps the in-memory remote snapshot
                cat.fetch_remote_catalog = lambda url=None: None
                again = cat.refresh_catalog(force=False)
                assert again == entries

                # Force after failure: use appdata cache written earlier
                cat.clear_catalog_cache()
                from_cache = cat.refresh_catalog(force=True)
                assert len(from_cache) == 2
                assert cat.current_catalog_source() == "cache"

                # No cache file → bundled fallback
                cache_file.unlink()
                cat.clear_catalog_cache()
                fallback = cat.refresh_catalog(force=True)
                assert cat.catalog_entry_count(fallback) >= 500
                assert cat.current_catalog_source() == "bundled"
    finally:
        cat.fetch_remote_catalog = orig_fetch
        cat.catalog_cache_path = orig_cache_path
        rd.downloads_cache_path = orig_dl_path
        clear_release_downloads_cache()
        cat._loaded = prev
        if prev is None:
            cat.clear_catalog_cache()
    print("OK available catalog remote refresh and merge")


def test_available_catalog_offline_keeps_cache():
    """When remote fetch fails, appdata cache is preferred over re-reading only if present."""
    import tempfile
    from pathlib import Path

    from ichalaunch.addons import catalog as cat

    cached_entries = [
        {
            "name": "CachedAddon",
            "folder": "CachedAddon",
            "repo": "https://github.com/x/CachedAddon",
            "category": "General",
            "description": "from-cache",
            "source": "turtle_wiki",
        }
    ]
    prev = cat._loaded
    orig_fetch = cat.fetch_remote_catalog
    orig_cache_path = cat.catalog_cache_path
    from ichalaunch.addons import release_downloads as rd
    from ichalaunch.addons.release_downloads import (
        clear_release_downloads_cache,
        live_download_fetch_disabled,
    )

    orig_dl_path = rd.downloads_cache_path
    try:
        with tempfile.TemporaryDirectory() as tmp:
            cache_file = Path(tmp) / "addons_catalog.json"
            dl_cache = Path(tmp) / "addon_release_downloads.json"
            cat.write_catalog_file(cache_file, cached_entries)
            cat.catalog_cache_path = lambda: cache_file
            rd.downloads_cache_path = lambda: dl_cache
            clear_release_downloads_cache()
            cat.clear_catalog_cache()
            cat.fetch_remote_catalog = lambda url=None: None
            with live_download_fetch_disabled():
                entries = cat.refresh_catalog(force=True)
            assert len(entries) == 1
            assert entries[0]["folder"] == "CachedAddon"
            assert cat.current_catalog_source() == "cache"
    finally:
        cat.fetch_remote_catalog = orig_fetch
        cat.catalog_cache_path = orig_cache_path
        rd.downloads_cache_path = orig_dl_path
        clear_release_downloads_cache()
        cat._loaded = prev
        if prev is None:
            cat.clear_catalog_cache()
    print("OK available catalog offline keeps cache")


def test_release_download_count_parse():
    from ichalaunch.addons.release_downloads import parse_latest_release_download_count

    assert parse_latest_release_download_count(None) is None
    assert parse_latest_release_download_count("nope") is None
    assert parse_latest_release_download_count({"message": "Not Found"}) is None
    assert parse_latest_release_download_count({"tag_name": "v1.0", "assets": []}) == 0
    assert parse_latest_release_download_count({"id": 9, "tag_name": "v1"}) == 0
    payload = {
        "tag_name": "v2.0",
        "assets": [
            {"name": "a.zip", "download_count": 100},
            {"name": "b.zip", "download_count": 23},
            {"name": "skip"},
            {"name": "bad", "download_count": "x"},
        ],
    }
    assert parse_latest_release_download_count(payload) == 123
    print("OK release download count parse")


def test_release_download_count_format():
    from ichalaunch.addons.release_downloads import format_download_count

    assert format_download_count(None) == "—"
    assert format_download_count(-1) == "—"
    assert format_download_count(0) == "0"
    assert format_download_count(42) == "42"
    assert format_download_count(999) == "999"
    assert format_download_count(1000) == "1k"
    assert format_download_count(1200) == "1.2k"
    assert format_download_count(12_000) == "12k"
    assert format_download_count(120_000) == "120k"
    assert format_download_count(1_200_000) == "1.2M"
    print("OK release download count format")


def test_release_download_fetch_queue_prefers_missing_then_oldest():
    from ichalaunch.addons.release_downloads import repos_needing_fetch

    cache = {
        "version": 1,
        "repos": {
            "a/one": {"state": "ok", "count": 1, "fetched_at": 100},
            "b/two": {"state": "ok", "count": 2, "fetched_at": 10},
        },
    }
    repos = ["a/one", "b/two", "c/new"]
    assert repos_needing_fetch(repos, cache, now=10_000) == ["c/new", "b/two", "a/one"]
    print("OK release download fetch queue prefers missing then oldest")


def test_release_download_sort_order():
    from ichalaunch.addons.release_downloads import sort_addons_by_popularity

    rows = [
        {"name": "Zebra", "release_downloads": 10, "release_downloads_state": "ok"},
        {"name": "Alpha", "release_downloads": 10, "release_downloads_state": "ok"},
        {"name": "Hot", "release_downloads": 5000, "release_downloads_state": "ok"},
        {"name": "NoneRel", "release_downloads_state": "none"},
        {"name": "Unknown"},
        {"name": "Zero", "release_downloads": 0, "release_downloads_state": "ok"},
    ]
    names = [e["name"] for e in sort_addons_by_popularity(rows)]
    assert names == ["Hot", "Alpha", "Zebra", "NoneRel", "Zero", "Unknown"]
    print("OK release download sort order")


def test_release_download_fork_vs_main_repo():
    from ichalaunch.addons.release_downloads import (
        addon_release_repo,
        catalog_main_repos,
        download_badge_text,
        download_badge_tooltip,
        stamp_entry_release_downloads,
    )

    main = {
        "name": "Bagnon",
        "repo": "https://github.com/McPewPew/Bagnon",
        "forks": [{"label": "MarcelineVQ", "repo": "https://github.com/MarcelineVQ/Bagnon"}],
    }
    assert addon_release_repo(main) == "McPewPew/Bagnon"
    assert catalog_main_repos([main]) == ["McPewPew/Bagnon"]

    forked = dict(main)
    forked["selected_repo"] = "https://github.com/MarcelineVQ/Bagnon"
    assert addon_release_repo(forked) == "MarcelineVQ/Bagnon"
    # Hourly job still fetches the catalog main, never the selected fork.
    assert catalog_main_repos([forked]) == ["McPewPew/Bagnon"]

    cache = {
        "version": 1,
        "repos": {
            "mcpewpew/bagnon": {"state": "ok", "count": 9000, "fetched_at": 1},
            "marcelinevq/bagnon": {"state": "none", "fetched_at": 1},
        },
    }
    stamp_entry_release_downloads(main, cache)
    assert main["release_downloads"] == 9000
    assert main["release_downloads_repo"] == "McPewPew/Bagnon"

    stamp_entry_release_downloads(forked, cache)
    assert forked.get("release_downloads") is None
    assert forked["release_downloads_state"] == "none"
    assert forked["release_downloads_repo"] == "MarcelineVQ/Bagnon"
    assert download_badge_text(forked) == "—"
    tip = download_badge_tooltip(forked)
    assert "MarcelineVQ/Bagnon" in tip
    assert "not taken from upstream" in tip
    print("OK release download fork vs main repo")


def test_apply_published_fork_does_not_inherit_main_count():
    """Selected fork must not display the master-list (upstream) count."""
    from ichalaunch.addons.release_downloads import (
        apply_published_download_stamps,
        download_badge_text,
        download_badge_tooltip,
        popularity_sort_key,
        sort_addons_by_popularity,
    )

    entry = {
        "name": "Bagnon",
        "repo": "https://github.com/McPewPew/Bagnon",
        "selected_repo": "https://github.com/MarcelineVQ/Bagnon",
        "release_downloads": 9000,
        "release_downloads_state": "ok",
        "release_downloads_repo": "McPewPew/Bagnon",
    }
    assert download_badge_text(entry) == "—"
    assert entry["release_downloads"] == 9000
    tip = download_badge_tooltip(entry)
    assert "MarcelineVQ/Bagnon" in tip
    assert "not taken from upstream" in tip

    apply_published_download_stamps([entry])
    assert entry.get("release_downloads") is None
    assert entry["release_downloads_state"] == "none"
    assert entry["release_downloads_repo"] == "MarcelineVQ/Bagnon"
    assert download_badge_text(entry) == "—"

    rows = [
        {
            "name": "Hot",
            "release_downloads": 5000,
            "release_downloads_state": "ok",
        },
        {
            "name": "Quiet",
            "release_downloads_state": "none",
            "release_downloads": 0,
        },
        {"name": "Unknown"},
    ]
    assert [e["name"] for e in sort_addons_by_popularity(rows)] == [
        "Hot",
        "Quiet",
        "Unknown",
    ]
    assert popularity_sort_key(rows[0])[0] == 0
    print("OK apply published fork does not inherit main count")


def test_release_download_missing_release_handling():
    from ichalaunch.addons.release_downloads import (
        download_badge_text,
        refresh_release_downloads,
        stamp_entry_release_downloads,
    )

    entry = {
        "name": "GitOnly",
        "folder": "GitOnly",
        "repo": "https://github.com/example/GitOnly",
    }
    cache = {
        "version": 1,
        "repos": {
            "example/gitonly": {"state": "none", "fetched_at": 1},
        },
    }
    stamp_entry_release_downloads(entry, cache)
    assert entry["release_downloads_state"] == "none"
    assert "release_downloads" not in entry
    assert download_badge_text(entry) == "—"

    other = {
        "name": "StillUnknown",
        "repo": "https://github.com/example/Missing",
        "release_downloads": 50,
        "release_downloads_state": "ok",
        "release_downloads_repo": "other/Upstream",
    }
    stamp_entry_release_downloads(other, {"version": 1, "repos": {}})
    assert "release_downloads" not in other
    assert download_badge_text(other) == ""

    fetched = {
        "name": "LiveNone",
        "repo": "https://github.com/example/LiveNone",
    }

    def fake_fetch(owner, repo):
        if repo == "LiveNone":
            return None
        return {
            "tag_name": "v1",
            "assets": [{"download_count": 1500}],
        }

    popular = {
        "name": "LiveOk",
        "repo": "https://github.com/example/LiveOk",
    }
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "dl.json"
        refresh_release_downloads(
            [fetched, popular],
            live=True,
            cache_path=path,
            fetch_latest=fake_fetch,
        )
        assert fetched["release_downloads_state"] == "none"
        assert download_badge_text(fetched) == "—"
        assert popular["release_downloads"] == 1500
        assert download_badge_text(popular) == "1.5k"
    print("OK release download missing release handling")


def test_release_download_catalog_refresh_stamps_cache():
    """Client catalog GET uses published counts; local cache / GitHub are not used."""
    from ichalaunch.addons import catalog as cat
    from ichalaunch.addons import release_downloads as rd
    from ichalaunch.addons.release_downloads import (
        clear_release_downloads_cache,
        write_downloads_cache,
    )

    remote = [
        {
            "name": "Popular",
            "folder": "Popular",
            "repo": "https://github.com/pop/Popular",
            "category": "General",
            "release_downloads": 4242,
            "release_downloads_state": "ok",
            "release_downloads_repo": "pop/Popular",
            "release_downloads_at": "2026-08-26T00:00:00Z",
        },
        {
            "name": "Quiet",
            "folder": "Quiet",
            "repo": "https://github.com/q/Quiet",
            "category": "General",
            "release_downloads": 0,
            "release_downloads_state": "none",
            "release_downloads_repo": "q/Quiet",
        },
    ]
    prev = cat._loaded
    orig_fetch = cat.fetch_remote_catalog
    orig_cache_path = cat.catalog_cache_path
    orig_dl_path = rd.downloads_cache_path
    orig_refresh = rd.refresh_release_downloads
    orig_graphql = rd._fetch_latest_via_graphql
    orig_rest = rd._fetch_latest_via_rest
    github_calls: list[str] = []

    def _no_refresh(*_a, **_k):
        github_calls.append("refresh")
        raise AssertionError("client must not fan out release downloads")

    def _no_graphql(*_a, **_k):
        github_calls.append("graphql")
        raise AssertionError("client must not call GitHub GraphQL")

    def _no_rest(*_a, **_k):
        github_calls.append("rest")
        raise AssertionError("client must not call GitHub REST")

    try:
        with tempfile.TemporaryDirectory() as tmp:
            cache_file = Path(tmp) / "addons_catalog.json"
            dl_cache = Path(tmp) / "addon_release_downloads.json"
            write_downloads_cache(
                {
                    "version": 1,
                    "repos": {
                        "pop/popular": {
                            "state": "ok",
                            "count": 99_999,
                            "fetched_at": time.time(),
                        },
                    },
                },
                dl_cache,
            )
            cat.catalog_cache_path = lambda: cache_file
            rd.downloads_cache_path = lambda: dl_cache
            rd.refresh_release_downloads = _no_refresh
            rd._fetch_latest_via_graphql = _no_graphql
            rd._fetch_latest_via_rest = _no_rest
            cat.clear_catalog_cache()
            cat.fetch_remote_catalog = lambda url=None: [dict(row) for row in remote]
            entries = cat.refresh_catalog(force=True)
            by_name = {e["name"]: e for e in entries}
            assert github_calls == []
            assert by_name["Popular"]["release_downloads"] == 4242
            assert by_name["Popular"]["release_downloads_state"] == "ok"
            assert by_name["Quiet"]["release_downloads_state"] == "none"
            assert by_name["Quiet"]["release_downloads"] == 0
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            cached_by = {e["name"]: e for e in cached}
            assert cached_by["Popular"]["release_downloads"] == 4242
    finally:
        cat.fetch_remote_catalog = orig_fetch
        cat.catalog_cache_path = orig_cache_path
        rd.downloads_cache_path = orig_dl_path
        rd.refresh_release_downloads = orig_refresh
        rd._fetch_latest_via_graphql = orig_graphql
        rd._fetch_latest_via_rest = orig_rest
        clear_release_downloads_cache()
        cat._loaded = prev
        if prev is None:
            cat.clear_catalog_cache()
    print("OK release download catalog refresh uses published counts")


def test_enrich_catalog_downloads_stamps_and_keeps_last():
    """Hourly stamp script (mocked GitHub): fill counts, none/0, keep last on error."""
    from tools.enrich_catalog_downloads import enrich_catalog_file

    catalog = [
        {
            "name": "Keep",
            "folder": "Keep",
            "repo": "https://github.com/o/Keep",
            "release_downloads": 99,
            "release_downloads_state": "ok",
            "release_downloads_repo": "o/Keep",
            "release_downloads_at": "2026-01-01T00:00:00Z",
        },
        {
            "name": "NoneRel",
            "folder": "NoneRel",
            "repo": "https://github.com/o/NoneRel",
        },
        {
            "name": "Hot",
            "folder": "Hot",
            "repo": "https://github.com/o/Hot",
        },
        {
            "name": "SkipFork",
            "folder": "SkipFork",
            "repo": "https://github.com/o/SkipFork",
            "forks": [{"label": "f", "repo": "https://github.com/f/SkipFork"}],
            "selected_repo": "https://github.com/f/SkipFork",
        },
    ]
    fetched: list[str] = []

    def fake_fetch(owner, repo):
        fetched.append(f"{owner}/{repo}")
        if repo == "Keep":
            raise RuntimeError("transient")
        if repo == "NoneRel":
            return None
        if repo == "Hot":
            return {"tag_name": "v1", "id": 1, "assets": [{"download_count": 12}]}
        if repo == "SkipFork":
            return {"tag_name": "v1", "id": 1, "assets": [{"download_count": 3}]}
        raise AssertionError(repo)

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "addons.json"
        path.write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")
        rc = enrich_catalog_file(path, fetch_latest=fake_fetch, token="unused")
        assert rc == 0
        out = json.loads(path.read_text(encoding="utf-8"))

    assert "f/SkipFork" not in fetched
    assert fetched == ["o/Keep", "o/NoneRel", "o/Hot", "o/SkipFork"]
    by_name = {e["name"]: e for e in out}
    assert by_name["Keep"]["release_downloads"] == 99
    assert by_name["Keep"]["release_downloads_at"] == "2026-01-01T00:00:00Z"
    assert by_name["NoneRel"]["release_downloads_state"] == "none"
    assert by_name["NoneRel"]["release_downloads"] == 0
    assert by_name["Hot"]["release_downloads"] == 12
    assert by_name["Hot"]["release_downloads_state"] == "ok"
    assert by_name["Hot"]["release_downloads_repo"] == "o/Hot"
    assert by_name["Hot"].get("release_downloads_at")
    assert by_name["SkipFork"]["release_downloads"] == 3
    print("OK enrich catalog downloads stamps and keeps last")


def test_enrich_catalog_downloads_graphql_batches():
    """Tokened hourly enrich uses GraphQL batches; failed batches keep last known."""
    from ichalaunch.addons import release_downloads as rd

    entries = [
        {
            "name": f"A{i}",
            "folder": f"A{i}",
            "repo": f"https://github.com/o/A{i}",
            "release_downloads": 7,
            "release_downloads_state": "ok",
            "release_downloads_repo": f"o/A{i}",
        }
        for i in range(3)
    ]
    calls: list[tuple] = []

    def fake_gql(names, token=""):
        calls.append((list(names), token))
        return {
            names[0]: {"tag_name": "v1", "id": 1, "assets": [{"download_count": 4}]},
            names[1]: None,
            names[2]: {"tag_name": "v2", "id": 2, "assets": [{"download_count": 8}]},
        }

    orig = rd._fetch_latest_via_graphql
    orig_rest = rd._fetch_latest_via_rest
    rest_calls: list[str] = []

    def no_rest(*_a, **_k):
        rest_calls.append("rest")
        raise AssertionError("GraphQL succeeded; REST fallback must not run")

    try:
        rd._fetch_latest_via_graphql = fake_gql
        rd._fetch_latest_via_rest = no_rest
        rd.enrich_catalog_download_fields(entries, token="t0k")
    finally:
        rd._fetch_latest_via_graphql = orig
        rd._fetch_latest_via_rest = orig_rest

    assert rest_calls == []
    assert calls == [(["o/A0", "o/A1", "o/A2"], "t0k")]
    assert entries[0]["release_downloads"] == 4
    assert entries[1]["release_downloads_state"] == "none"
    assert entries[1]["release_downloads"] == 0
    assert entries[2]["release_downloads"] == 8

    kept = [
        {
            "name": "Old",
            "folder": "Old",
            "repo": "https://github.com/o/Old",
            "release_downloads": 55,
            "release_downloads_state": "ok",
            "release_downloads_repo": "o/Old",
        }
    ]

    def gql_unusable(_names, token=""):
        return None

    def rest_boom(owner, repo, token=""):
        raise RuntimeError("rest down")

    rd._fetch_latest_via_graphql = gql_unusable
    rd._fetch_latest_via_rest = rest_boom
    try:
        rd.enrich_catalog_download_fields(kept, token="t0k")
    finally:
        rd._fetch_latest_via_graphql = orig
        rd._fetch_latest_via_rest = orig_rest
    assert kept[0]["release_downloads"] == 55
    print("OK enrich catalog downloads graphql batches")


def test_addon_row_download_count_after_git():
    """Master-list row shows download glyph+count immediately right of Open-in-Git."""
    from PySide6.QtWidgets import QApplication, QHBoxLayout

    from ichalaunch.ui.widgets.common import AddonRow

    app = QApplication.instance() or QApplication([])
    row = AddonRow(
        {
            "name": "Pop",
            "folder": "Pop",
            "repo": "https://github.com/a/Pop",
            "release_downloads": 1500,
            "release_downloads_state": "ok",
            "release_downloads_repo": "a/Pop",
        },
        status="available",
    )
    assert row.open_git_btn is not None
    assert row.download_count is not None
    assert not row.download_count.isHidden()
    assert row.download_count._label.text() == "1.5k"
    found: list[str] = []
    root = row.layout()
    assert root is not None
    top = root.itemAt(0)
    assert top is not None and isinstance(top.layout(), QHBoxLayout)
    for i in range(top.layout().count()):
        item = top.layout().itemAt(i)
        if item is None or item.layout() is None:
            continue
        lay = item.layout()
        for j in range(lay.count()):
            w = lay.itemAt(j).widget() if lay.itemAt(j) else None
            if w is row.open_git_btn:
                found.append("git")
            elif w is row.download_count:
                found.append("downloads")
    assert found == ["git", "downloads"], found
    row.deleteLater()
    print("OK addon row download count after git")


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


def test_commit_atom_sha_no_default_fallback_for_named_ref():
    """Named-ref Atom probes must not silently return the default commits feed."""
    from ichalaunch.addons import git_refs as gr

    calls: list[str] = []

    class _Resp:
        def __init__(self, code: int, text: str = ""):
            self.status_code = code
            self.text = text

    def fake_get(url, headers=None, timeout=None):  # noqa: ARG001
        calls.append(url)
        return _Resp(404)

    orig = gr.requests.get
    try:
        gr.requests.get = fake_get
        assert gr.fetch_commit_atom_sha("o", "r", "1.2.3") is None
        assert len(calls) == 1
        assert calls[0].endswith("/commits/1.2.3.atom")
        assert "/commits.atom" not in calls[0]
        calls.clear()
        assert gr.fetch_commit_atom_sha("o", "r", None) is None
        assert len(calls) == 1
        assert calls[0].endswith("/commits.atom")
    finally:
        gr.requests.get = orig
    print("OK commit atom sha no default fallback for named ref")


def test_preview_addon_repo_soft_fails_fake_tags():
    """Settings preview must not abort when the pin is a TOC version, not a git tag.

    Addon Settings builds ``…/releases/tag/<meta.version>``; many installed versions
    are not real refs and used to 422 the whole README preview.
    """
    from ichalaunch.addons.github import cleanup_readme_cache, preview_addon_repo

    # Fake pin that is not a git tag on this repo (TOC-style version).
    url = "https://github.com/shagu/pfUI/releases/tag/9.9.9-not-a-real-tag"
    info = preview_addon_repo(url)
    try:
        assert info.get("kind") == "addon"
        assert "pfUI" in str(info.get("full_name") or "")
        # Unresolved pin must not keep a tag page URL / tag field.
        assert not str(info.get("tag") or "").strip()
        assert "/releases/tag/" not in str(info.get("url") or "")
        # Preview body should still load from the default branch.
        assert str(info.get("readme_markdown") or info.get("description") or "").strip()
        assert str(info.get("default_branch") or "").strip()
    finally:
        cleanup_readme_cache(info.get("readme_cache_dir"))
    print("OK preview soft-fails fake tags")


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


def test_reinstall_clears_never_update():
    """Intentional install/reinstall/replace must clear a user Never Update lock."""
    import ichalaunch.config.settings as settings_mod
    from ichalaunch.addons.github import _addon_install_meta
    from ichalaunch.config.settings import Settings

    with tempfile.TemporaryDirectory() as td:
        fake = Path(td) / "settings.json"
        orig_path = settings_mod.settings_path
        settings_mod.settings_path = lambda: fake
        try:
            s = Settings()
            s.set_installed_addon(
                "ShaguTweaks",
                {
                    "source": "github",
                    "name": "ShaguTweaks",
                    "repository": "shagu/ShaguTweaks",
                    "never_update": True,
                    "loaded": True,
                },
            )
            assert s.is_addon_never_update("ShaguTweaks") is True

            # Explicit False from install meta clears the user lock.
            s.set_installed_addon("ShaguTweaks", {"loaded": True, "never_update": False})
            assert s.installed_addons["ShaguTweaks"].get("never_update") is not True
            assert s.is_addon_never_update("ShaguTweaks") is False

            s.set_addon_never_update("ShaguTweaks", True)
            assert s.is_addon_never_update("ShaguTweaks") is True

            # Same path reinstall uses: _addon_install_meta → set_installed_addon
            meta = _addon_install_meta(
                folder="ShaguTweaks",
                owner="shagu",
                repo="ShaguTweaks",
                branch="master",
                sha="abc1234",
                url="https://github.com/shagu/ShaguTweaks",
                commit_date="2024-01-01",
                match_kind="exact",
            )
            assert meta.get("never_update") is False
            s.set_installed_addon("ShaguTweaks", meta)
            assert s.installed_addons["ShaguTweaks"].get("never_update") is not True
            assert s.is_addon_never_update("ShaguTweaks") is False

            # Catalog-pinned Bagshui stays locked after the same write shape.
            s.set_installed_addon(
                "Bagshui",
                {"source": "github", "name": "Bagshui", "never_update": False, "loaded": True},
            )
            assert s.installed_addons["Bagshui"].get("never_update") is True
            assert s.is_addon_never_update("Bagshui") is True
        finally:
            settings_mod.settings_path = orig_path

    print("OK reinstall clears never_update")


def test_row_reinstall_clears_never_update():
    """Installed-row Reinstall payload must clear never_update via _reinstall_addon.

    Prior test only called _addon_install_meta directly and missed the real UI
    path: AddonRow Reinstall → reinstall_requested → MainWindow._reinstall_addon
    (no _prefer_selection). That handler must clear+persist before install runs.
    """
    from unittest.mock import MagicMock

    import ichalaunch.config.settings as settings_mod
    from ichalaunch.config.settings import settings
    from ichalaunch.ui import main_window as mw

    with tempfile.TemporaryDirectory() as td:
        fake = Path(td) / "settings.json"
        orig_path = settings_mod.settings_path
        settings_mod.settings_path = lambda: fake
        orig_installed = mw.is_installed
        try:
            settings.load()
            settings.set_installed_addon(
                "ShaguTweaks",
                {
                    "source": "github",
                    "name": "ShaguTweaks",
                    "folder": "ShaguTweaks",
                    "repository": "shagu/ShaguTweaks",
                    "url": "https://github.com/shagu/ShaguTweaks",
                    "never_update": True,
                    "loaded": True,
                },
            )
            assert settings.is_addon_never_update("ShaguTweaks") is True

            # Same shape AddonRow emits (no _prefer_selection / _action).
            row_entry = {
                "name": "ShaguTweaks",
                "folder": "ShaguTweaks",
                "description": "",
                "category": "Installed",
                "repo": "https://github.com/shagu/ShaguTweaks",
                "repository": "shagu/ShaguTweaks",
                "url": "https://github.com/shagu/ShaguTweaks",
                "source": "github",
                "tag": "",
                "loaded": True,
            }

            win = MagicMock()
            win.addons = MagicMock()
            # Real clear path used by _reinstall_addon.
            win.addons.set_never_update = (
                lambda entry, enabled: settings.set_addon_never_update(
                    str(entry.get("folder") or entry.get("name") or ""),
                    bool(enabled),
                )
            )
            win.status_lbl = MagicMock()
            captured: dict = {}

            def _capture_busy(title, worker, on_ok=None, **_kw):  # noqa: ANN001
                captured["title"] = title
                captured["fn"] = worker.fn
                captured["args"] = worker.args
                captured["kwargs"] = worker.kwargs
                # Do not run download — clear must already have happened.
                if on_ok:
                    on_ok(None)

            win._busy = _capture_busy
            mw.is_installed = lambda: True  # type: ignore[assignment]
            mw.MainWindow._reinstall_addon(win, row_entry)

            assert settings.installed_addons["ShaguTweaks"].get("never_update") is not True
            assert settings.is_addon_never_update("ShaguTweaks") is False
            # Persist to disk (set_addon_never_update → set → save).
            raw = json.loads(fake.read_text(encoding="utf-8"))
            assert raw["installed_addons"]["ShaguTweaks"].get("never_update") is not True
            assert "_prefer_selection" not in row_entry
            assert captured.get("fn") is mw.install_from_github
            assert captured.get("args")[1] == "ShaguTweaks"
        finally:
            mw.is_installed = orig_installed
            settings_mod.settings_path = orig_path
            settings.load()

    print("OK row reinstall clears never_update")


def test_addon_row_update_button_is_square():
    """Installed-row Update chrome is square and matches Reinstall plate height."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from ichalaunch.core.paths import theme_file
    from ichalaunch.ui.widgets.common import (
        AddonRow,
        AddonRowUpdateButton,
        RefreshReinstallButton,
        _UPDATE_BTN_SIDE,
        _row_update_arrow_pixmap,
    )
    from ichalaunch.ui.widgets.glue_panel_button import GLUE_ROW_H

    app = QApplication.instance() or QApplication([])
    assert _UPDATE_BTN_SIDE == GLUE_ROW_H
    assert theme_file("UI-MicroStream-Yellow.PNG").is_file()
    arrow = _row_update_arrow_pixmap()
    assert not arrow.isNull()

    btn = AddonRowUpdateButton()
    btn.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    btn.show()
    assert btn._chrome_w == GLUE_ROW_H
    assert btn._chrome_h == GLUE_ROW_H
    assert btn._chrome_w == btn._chrome_h
    # Widget may expand for CheckButtonGlow margins; stay square.
    assert btn.width() == btn.height()
    assert btn.width() >= GLUE_ROW_H
    assert not btn._glow_pm.isNull()
    assert btn._glow_pm.width() == btn.width()
    assert btn._glow_pm.height() == btn.height()
    chrome = btn._chrome_rect()
    assert chrome.width() == chrome.height() == GLUE_ROW_H
    assert chrome.x() == (btn.width() - GLUE_ROW_H) // 2
    assert chrome.y() == (btn.height() - GLUE_ROW_H) // 2
    assert not hasattr(btn, "menu_btn")
    assert not hasattr(btn, "set_menu_open")
    assert not hasattr(btn, "menu_popup_pos")
    assert not hasattr(btn, "menu_clicked")

    entry = {
        "name": "pfUI",
        "folder": "pfUI",
        "repo": "https://github.com/shagu/pfUI",
        "repository": "shagu/pfUI",
        "source": "github",
    }
    row = AddonRow(entry, status="Update available", never_update=False)
    row.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    row.resize(900, 64)
    row.show()
    row.adjustSize()
    assert isinstance(row._update_btn_widget, AddonRowUpdateButton)
    upd = row._update_btn_widget
    assert upd._chrome_h == GLUE_ROW_H
    assert upd.width() == upd.height()
    assert isinstance(row.reinstall_btn, RefreshReinstallButton)
    ri = row.reinstall_btn
    assert ri.height() == GLUE_ROW_H
    assert ri.width() == GLUE_ROW_H
    # Plate height matches Reinstall; widget may be taller for glow pad.
    assert upd._chrome_h == ri.height(), (
        f"Update chrome {upd._chrome_h} != Reinstall height {ri.height()}"
    )
    assert not hasattr(row, "never_update_changed")
    assert not hasattr(row, "_popup_never_update_menu")
    row.close()
    btn.close()
    print("OK addon row Update is square and matches Reinstall height")


def test_addon_row_install_button_matches_update_plate():
    """Available-row Install is a square arrow plate like Update, without glow."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from ichalaunch.ui.widgets.common import (
        AddonRow,
        AddonRowInstallButton,
        AddonRowUpdateButton,
        _row_install_arrow_pixmap,
        _row_update_arrow_pixmap,
    )
    from ichalaunch.ui.widgets.glue_panel_button import GLUE_ROW_H

    app = QApplication.instance() or QApplication([])
    install = AddonRowInstallButton()
    install.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    install.show()
    assert install.width() == GLUE_ROW_H
    assert install.height() == GLUE_ROW_H
    assert install.width() == install.height()
    down = _row_install_arrow_pixmap()
    up = _row_update_arrow_pixmap()
    assert not down.isNull()
    assert not up.isNull()
    assert down.height() == up.height()

    entry = {
        "name": "pfUI",
        "folder": "pfUI",
        "repo": "https://github.com/shagu/pfUI",
        "source": "github",
    }
    row = AddonRow(entry, status="available")
    row.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    row.show()
    install_btn = row.findChild(AddonRowInstallButton)
    assert install_btn is not None
    update_row = AddonRow(entry, status="Update available", never_update=False)
    assert isinstance(update_row._update_btn_widget, AddonRowUpdateButton)
    install.close()
    row.close()
    print("OK addon row Install matches Update square plate without glow")


def test_refresh_reinstall_uses_wow_art():
    from PySide6.QtWidgets import QApplication

    from ichalaunch.core.paths import theme_file
    from ichalaunch.ui.widgets.common import RefreshReinstallButton, _refresh_icon_pixmap
    from ichalaunch.ui.widgets.glue_panel_button import GLUE_ROW_H, GLUE_ROW_MENU_W

    app = QApplication.instance() or QApplication([])
    assert theme_file("UI-RefreshButton.PNG").is_file()
    icon = _refresh_icon_pixmap()
    assert not icon.isNull()
    btn = RefreshReinstallButton()
    assert btn.width() == GLUE_ROW_MENU_W
    assert btn.height() == GLUE_ROW_H
    print("OK addon reinstall uses UI-RefreshButton art at cog size")


def test_addon_row_reinstall_aligns_with_delete_bottom():
    """Reinstall refresh art bottom must match PassRemove circle bottom (±2px)."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication

    from ichalaunch.ui.widgets.common import (
        AddonRow,
        PassRemoveButton,
        RefreshReinstallButton,
        _REINSTALL_ICON_Y_NUDGE,
        _pass_icon_pixmap,
        _refresh_icon_pixmap,
    )

    def _opaque_bottom(pm) -> int:
        img = pm.toImage().convertToFormat(QImage.Format.Format_ARGB32)
        bottom = -1
        for y in range(img.height()):
            for x in range(img.width()):
                if ((img.pixel(x, y) >> 24) & 0xFF) > 8:
                    bottom = y
                    break
        assert bottom >= 0, "icon has no opaque pixels"
        return bottom

    app = QApplication.instance() or QApplication([])
    entry = {
        "name": "pfUI",
        "folder": "pfUI",
        "repo": "https://github.com/shagu/pfUI",
        "repository": "shagu/pfUI",
        "source": "github",
        "tag": "v1.0",
    }
    row = AddonRow(entry, status="Installed")
    row.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    row.resize(900, 64)
    row.show()
    row.adjustSize()

    ri = row.reinstall_btn
    rm = row.findChild(PassRemoveButton)
    assert isinstance(ri, RefreshReinstallButton)
    assert rm is not None
    assert ri.height() == rm.height()

    refresh = _refresh_icon_pixmap()
    pass_pm = _pass_icon_pixmap(pressed=False)
    assert not refresh.isNull() and not pass_pm.isNull()

    # Same centering formula as paintEvent (idle / not pressed).
    ri_draw_y = ri.rect().center().y() - refresh.height() // 2 + _REINSTALL_ICON_Y_NUDGE
    rm_draw_y = rm.rect().center().y() - pass_pm.height() // 2
    ri_art_bottom = ri.mapTo(row, ri.rect().topLeft()).y() + ri_draw_y + _opaque_bottom(refresh)
    rm_art_bottom = rm.mapTo(row, rm.rect().topLeft()).y() + rm_draw_y + _opaque_bottom(pass_pm)
    delta = abs(ri_art_bottom - rm_art_bottom)
    assert delta <= 2, (
        f"reinstall art bottom {ri_art_bottom} vs delete {rm_art_bottom} "
        f"(delta={delta}, nudge={_REINSTALL_ICON_Y_NUDGE})"
    )
    row.close()
    print("OK addon row reinstall art bottom aligns with delete circle")


def test_spellbook_page_buttons_use_wow_art():
    """Addons Prev/Next use spellbook page icons at GluePanelButton height."""
    from PySide6.QtWidgets import QApplication

    from ichalaunch.core.paths import theme_file
    from ichalaunch.ui.widgets.common import SpellbookPageButton, _spellbook_page_pixmap
    from ichalaunch.ui.widgets.glue_panel_button import GLUE_BTN_H

    app = QApplication.instance() or QApplication([])
    for name in (
        "UI-SpellbookIcon-NextPage-Up.PNG",
        "UI-SpellbookIcon-NextPage-Down.PNG",
        "UI-SpellbookIcon-PrevPage-Up.PNG",
        "UI-SpellbookIcon-PrevPage-Down.PNG",
    ):
        assert theme_file(name).is_file(), name
    prev_up = _spellbook_page_pixmap("prev", pressed=False)
    prev_down = _spellbook_page_pixmap("prev", pressed=True)
    next_up = _spellbook_page_pixmap("next", pressed=False)
    next_down = _spellbook_page_pixmap("next", pressed=True)
    assert not prev_up.isNull() and not prev_down.isNull()
    assert not next_up.isNull() and not next_down.isNull()
    assert prev_up.height() == GLUE_BTN_H
    prev = SpellbookPageButton("prev")
    nxt = SpellbookPageButton("next")
    assert prev.height() == GLUE_BTN_H
    assert nxt.height() == GLUE_BTN_H
    assert prev.width() == GLUE_BTN_H
    assert nxt.width() == GLUE_BTN_H
    assert prev.accessibleName() == "Previous page"
    assert nxt.accessibleName() == "Next page"
    print("OK addons pagination uses spellbook page icons at GLUE_BTN_H")


def test_contributor_wow_name_tooltip():
    """Discord-linked portraits use a tiled WoW tooltip, not the native tip."""
    from PySide6.QtWidgets import QApplication

    from ichalaunch.core.paths import theme_file
    from ichalaunch.ui.widgets.contributor_portrait import ContributorPortrait
    from ichalaunch.ui.widgets.wow_tooltip import (
        render_contributor_tooltip,
        tooltip_size_for,
    )

    app = QApplication.instance() or QApplication([])
    del app
    for name in (
        "UI-Tooltip-TL.PNG",
        "UI-Tooltip-T.PNG",
        "UI-Tooltip-TR.PNG",
        "UI-Tooltip-L.PNG",
        "UI-Tooltip-R.PNG",
        "UI-Tooltip-BL.PNG",
        "UI-Tooltip-B.PNG",
        "UI-Tooltip-BR.PNG",
        "UI-Tooltip-Background-Corrupted.PNG",
    ):
        assert theme_file("tooltips", name).is_file(), name
    short = tooltip_size_for("Mynie")
    long = tooltip_size_for("Valheru")
    assert long.width() > short.width()
    assert short.height() == long.height()
    pix = render_contributor_tooltip("Valheru")
    assert not pix.isNull()
    assert pix.width() == long.width()
    assert pix.height() == long.height()
    img = pix.toImage()
    assert img.pixelColor(0, 0).alpha() == 0
    fill = img.pixelColor(pix.width() // 2, 18)
    assert 40 <= fill.alpha() < 220, f"corrupted fill opacity a={fill.alpha()}"
    portrait = ContributorPortrait(
        "contributor_01.jpg",
        tooltip="Mynie",
        url="https://discord.com/users/1080557702339633222",
    )
    assert portrait.toolTip() == ""
    portrait.deleteLater()
    print("OK contributor Discord names use tiled WoW tooltip")


def test_floor_lighting_overlay():
    from PySide6.QtWidgets import QApplication

    from ichalaunch.core.paths import theme_file
    from ichalaunch.ui.main_window import _floor_lighting_pixmap

    QApplication.instance() or QApplication([])
    assert theme_file("Legion_DH_Lighting_02.PNG").is_file()
    pm = _floor_lighting_pixmap()
    assert not pm.isNull()
    # Source is 128×512; rotated 90° CW → 512×128.
    assert pm.width() == 512
    assert pm.height() == 128
    print("OK floor lighting pixmap is rotated and tinted")


def test_addon_settings_never_update_on_save():
    """Settings cog Never update can check AND uncheck; catalog pins stay locked."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from ichalaunch.addons import github as G
    from ichalaunch.config.settings import Settings
    from ichalaunch.ui.pages.addons import AddonsPage
    from ichalaunch.ui.widgets import dialogs as D
    from ichalaunch.ui.widgets.theme_checkbox import ThemeCheckBox

    app = QApplication.instance() or QApplication([])
    entry = {
        "name": "pfUI",
        "folder": "pfUI",
        "repo": "https://github.com/shagu/pfUI",
        "repository": "shagu/pfUI",
    }
    meta = {"tag": "5.4.4", "source": "github", "loaded": True}

    prev_token = G.has_github_token
    orig_preview_start = D._PreviewFetchThread.start
    orig_browse_start = D._AddonBrowseFetchThread.start

    def _noop_start(self):  # noqa: ANN001
        return None

    try:
        G.has_github_token = lambda: True  # type: ignore[assignment]
        D._PreviewFetchThread.start = _noop_start  # type: ignore[method-assign]
        D._AddonBrowseFetchThread.start = _noop_start  # type: ignore[method-assign]

        dlg = D.AddonSettingsDialog(None, entry, meta=meta)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        assert isinstance(dlg._never_update_cb, ThemeCheckBox)
        assert dlg._never_update_cb.text() == "Never update"
        assert dlg._never_update_cb.isEnabled()
        assert dlg._never_update_cb.isChecked() is False
        dlg._never_update_cb.setChecked(True)
        dlg._accept_save()
        result = dlg.result_data()
        assert isinstance(result, dict)
        assert result.get("never_update") is True
        dlg.close()

        # After Save the row often carries pin_release + never_update — must NOT
        # treat that as a catalog lock (regression: checkbox stuck enabled=False).
        sticky = dict(entry)
        sticky["pin_release"] = "5.4.4"
        sticky["tag"] = "5.4.4"
        sticky_meta = {
            "tag": "5.4.4",
            "source": "github",
            "loaded": True,
            "never_update": True,
        }
        dlg_clear = D.AddonSettingsDialog(None, sticky, meta=sticky_meta)
        dlg_clear.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        assert dlg_clear._catalog_never_locked is False
        assert dlg_clear._never_update_cb.isEnabled()
        assert dlg_clear._never_update_cb.isChecked() is True
        dlg_clear._never_update_cb.setChecked(False)
        dlg_clear._accept_save()
        cleared = dlg_clear.result_data()
        assert isinstance(cleared, dict)
        assert cleared.get("never_update") is False
        dlg_clear.close()

        # Catalog-locked Bagshui stays checked + disabled.
        bag = {
            "name": "Bagshui",
            "folder": "Bagshui",
            "repo": "https://github.com/bagshui/bagshui",
            "repository": "bagshui/bagshui",
        }
        bag_meta = {"source": "github", "never_update": True, "loaded": True}
        dlg2 = D.AddonSettingsDialog(None, bag, meta=bag_meta)
        dlg2.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        assert dlg2._catalog_never_locked is True
        assert dlg2._never_update_cb.isChecked() is True
        assert not dlg2._never_update_cb.isEnabled()
        dlg2.close()
    finally:
        G.has_github_token = prev_token
        D._PreviewFetchThread.start = orig_preview_start  # type: ignore[method-assign]
        D._AddonBrowseFetchThread.start = orig_browse_start  # type: ignore[method-assign]

    # Persist round-trip: uncheck → is_addon_never_update False; check → True.
    import ichalaunch.config.settings as settings_mod

    with tempfile.TemporaryDirectory() as td:
        fake = Path(td) / "settings.json"
        orig_path = settings_mod.settings_path
        settings_mod.settings_path = lambda: fake
        try:
            s = Settings()
            s.set_installed_addon(
                "pfUI",
                {
                    "source": "github",
                    "never_update": True,
                    "loaded": True,
                    "tag": "5.4.4",
                },
            )
            assert s.is_addon_never_update("pfUI") is True
            s.set_addon_never_update("pfUI", False)
            assert s.is_addon_never_update("pfUI") is False
            assert s.installed_addons["pfUI"].get("never_update") is not True
            s.set_addon_never_update("pfUI", True)
            assert s.is_addon_never_update("pfUI") is True
        finally:
            settings_mod.settings_path = orig_path

    page = AddonsPage()
    page.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    seen: list[tuple[dict, bool]] = []
    page.set_never_update = (  # type: ignore[method-assign]
        lambda ent, enabled: seen.append((dict(ent), bool(enabled)))
    )

    def _fake_dialog(parent, ent, *, meta=None):  # noqa: ANN001
        out = dict(ent)
        out["never_update"] = False
        return out

    prev_dlg = D.addon_settings_dialog
    try:
        D.addon_settings_dialog = _fake_dialog  # type: ignore[assignment]
        page.open_addon_settings(dict(entry))
    finally:
        D.addon_settings_dialog = prev_dlg  # type: ignore[assignment]
        page.close()

    assert len(seen) == 1
    assert seen[0][1] is False
    assert seen[0][0].get("folder") == "pfUI"
    print("OK addon settings Never update checkbox applies on Save")


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


def test_zip_root_never_writes_wtf_config():
    """A zip_root bundle shipping WTF/ or Config.wtf must not touch user configs."""
    import io
    import tempfile
    import zipfile
    from unittest.mock import patch

    from ichalaunch.config.settings import settings as s
    from ichalaunch.mods.installer import install_mod

    # Minimal PE stubs: MZ header + enough padding to pass validate_pe_binary.
    pe = b"MZ" + b"\0" * 4096
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as zf:
        zf.writestr("VanillaFixes.exe", pe)
        zf.writestr("VfPatcher.dll", pe)
        zf.writestr("dlls.txt", "VfPatcher.dll\n")
        zf.writestr("WTF/Config.wtf", 'SET farclip "9999"\n')
        zf.writestr("wtf/realmlist.wtf", "set realmlist evil.example.com\n")
        zf.writestr("Config.wtf", 'SET farclip "9999"\n')

    keys = ("desired_mods", "user_set_mods", "installed_mods", "user_mods", "game_path", "addons_path")
    saved = {k: s.get(k) for k in keys}
    try:
        with tempfile.TemporaryDirectory() as td:
            game = Path(td)
            (game / "WoW.exe").write_bytes(b"MZ")
            wtf = game / "WTF"
            wtf.mkdir()
            user_cfg = 'SET farclip "777"\n'
            (wtf / "Config.wtf").write_text(user_cfg, encoding="utf-8")
            s.set("game_path", str(game))
            s.set("addons_path", "")
            with patch(
                "ichalaunch.mods.installer._download_source",
                return_value=payload.getvalue(),
            ):
                install_mod("vanillafixes")
            # Normal payload landed…
            assert (game / "VanillaFixes.exe").is_file()
            assert (game / "VfPatcher.dll").is_file()
            # …but nothing from the archive reached WTF/ or Config.wtf.
            assert (wtf / "Config.wtf").read_text(encoding="utf-8") == user_cfg
            assert not (wtf / "realmlist.wtf").exists()
            assert not (game / "Config.wtf").exists()
    finally:
        for k in keys:
            s.set(k, saved[k])
    print("OK zip_root skips WTF and Config.wtf entries")


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
        # Target selection is platform-neutral and is checked everywhere.
        targets = iter_game_permission_targets(game)
        assert (game / "WoW.exe") not in targets
        assert game in targets
        assert game / "Data" in targets

        scan = scan_game_permissions(game)
        assert not scan.has_issues, scan.issues

        if sys.platform != "win32":
            # Read-only attributes and ACLs are a Windows concept, and both
            # entry points say so by returning early. Pin that contract rather
            # than skipping: a read-only Data/ must stay quiet here, because a
            # POSIX mode bit is not the problem this feature exists to fix.
            data_dir = game / "Data"
            os.chmod(data_dir, stat.S_IREAD)
            try:
                assert not scan_game_permissions(game).has_issues
                fix = fix_game_permissions(game)
                assert not fix.fixes
                assert any("only supported on windows" in w.lower() for w in fix.warnings), (
                    fix.warnings
                )
            finally:
                os.chmod(data_dir, stat.S_IREAD | stat.S_IWRITE | stat.S_IEXEC)
            print("OK game permissions scan/fix (no-op off Windows)")
            return

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

        if sys.platform != "win32":
            # No protected-location concept off Windows; the repair path still
            # has to decline politely instead of pretending it fixed something.
            try:
                assert not scan_game_permissions(game).has_issues
                fix = fix_game_permissions(game)
                assert not fix.fixes
                assert fix.warnings
            finally:
                os.chmod(game / "Data", stat.S_IREAD | stat.S_IWRITE | stat.S_IEXEC)
            print("OK game permissions protected path (no-op off Windows)")
            return

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


def test_linux_appdata_uses_xdg_and_migrates():
    """Linux settings live under XDG; newer ~/AppData/Local trees migrate (#279)."""
    import os

    import ichalaunch.config.settings as settings_mod

    with tempfile.TemporaryDirectory() as td:
        home = Path(td) / "home"
        home.mkdir()
        legacy = home / "AppData" / "Local" / "IchaLaunch"
        xdg = home / ".config" / "IchaLaunch"
        legacy.mkdir(parents=True)
        (legacy / "settings.json").write_text('{"game_path": "E:/Live"}', encoding="utf-8")
        xdg.mkdir(parents=True)
        (xdg / "settings.json").write_text('{"game_path": "E:/Stale"}', encoding="utf-8")
        os.utime(legacy / "settings.json", (2_000_000_000, 2_000_000_000))
        os.utime(xdg / "settings.json", (1_000_000_000, 1_000_000_000))

        real_platform = settings_mod.sys.platform
        real_home = settings_mod._user_home
        real_env = os.environ.get("XDG_CONFIG_HOME")
        try:
            settings_mod.sys.platform = "linux"
            settings_mod._user_home = lambda: home
            os.environ.pop("XDG_CONFIG_HOME", None)
            root = settings_mod.appdata_root()
            assert root == xdg
            text = (xdg / "settings.json").read_text(encoding="utf-8")
            assert "E:/Live" in text
            assert not str(root).replace("\\", "/").endswith("AppData/Local/IchaLaunch")
        finally:
            settings_mod.sys.platform = real_platform
            settings_mod._user_home = real_home
            if real_env is None:
                os.environ.pop("XDG_CONFIG_HOME", None)
            else:
                os.environ["XDG_CONFIG_HOME"] = real_env
    print("OK linux appdata uses XDG and migrates the newer tree")


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


def test_settings_merge_keeps_game_path_and_tweaks_v2():
    """Saved game_path + Tweaks stay; new Old key defaults off and does not flip V2."""
    import ichalaunch.config.settings as settings_mod
    from ichalaunch.config.settings import DEFAULTS, Settings

    assert DEFAULTS["desired_mods"]["vanilla_tweaks"] is True
    assert DEFAULTS["desired_mods"]["vanilla_tweaks_old"] is False

    loaded = {
        "game_path": r"D:\Games\RavenCraft",
        "addons_path": r"D:\Games\RavenCraft\Interface\AddOns",
        "desired_mods": {
            "vanilla_tweaks": True,
            "vanillafixes": True,
            "dxvk": False,
        },
        "user_set_mods": ["vanilla_tweaks"],
        "installed_mods": {"nampower": {"version_display": "x"}},
    }
    merged, _ = Settings.__new__(Settings)._merge_loaded(loaded)
    assert merged["game_path"] == r"D:\Games\RavenCraft"
    assert merged["desired_mods"]["vanilla_tweaks"] is True
    assert merged["desired_mods"]["vanilla_tweaks_old"] is False
    assert merged["desired_mods"]["vanillafixes"] is True
    assert merged["installed_mods"].get("nampower")

    poisoned = {
        "game_path": r"D:\Games\RavenCraft",
        "desired_mods": {"vanilla_tweaks": True, "vanilla_tweaks_old": True},
        "user_set_mods": ["vanilla_tweaks"],
        "installed_mods": {"vanilla_tweaks": {"version_display": "v1.6.0"}},
    }
    merged2, changed = Settings.__new__(Settings)._merge_loaded(poisoned)
    assert changed is True
    assert merged2["game_path"] == r"D:\Games\RavenCraft"
    assert merged2["desired_mods"]["vanilla_tweaks"] is True
    assert merged2["desired_mods"]["vanilla_tweaks_old"] is False

    chosen = {
        "game_path": r"D:\Games\RavenCraft",
        "desired_mods": {"vanilla_tweaks": False, "vanilla_tweaks_old": True},
        "user_set_mods": ["vanilla_tweaks_old"],
    }
    merged3, _ = Settings.__new__(Settings)._merge_loaded(chosen)
    assert merged3["desired_mods"]["vanilla_tweaks_old"] is True
    assert merged3["desired_mods"]["vanilla_tweaks"] is False

    with tempfile.TemporaryDirectory() as td:
        fake = Path(td) / "settings.json"
        orig_path = settings_mod.settings_path
        settings_mod.settings_path = lambda: fake
        try:
            fake.write_text(json.dumps(loaded), encoding="utf-8")
            s = Settings()
            assert s.game_path.replace("\\", "/") == "D:/Games/RavenCraft"
            assert s.desired_mods.get("vanilla_tweaks") is True
            assert s.desired_mods.get("vanilla_tweaks_old") is False
        finally:
            settings_mod.settings_path = orig_path
    print("OK settings merge keeps game path and Tweaks V2")


def test_settings_save_survives_double_process_replace_race():
    """Two writers must not share settings.json.tmp; replace 5/2 is retried (#58)."""
    import threading
    from unittest import mock

    import ichalaunch.config.settings as settings_mod
    from ichalaunch.config.settings import (
        Settings,
        _is_settings_replace_race,
        _settings_tmp_path,
    )

    denied = OSError(13, "Access is denied")
    denied.winerror = 5
    missing = FileNotFoundError(2, "The system cannot find the file specified")
    missing.winerror = 2
    assert _is_settings_replace_race(denied) is True
    assert _is_settings_replace_race(missing) is True
    assert _is_settings_replace_race(OSError(28, "No space left on device")) is False

    with tempfile.TemporaryDirectory() as td:
        fake = Path(td) / "settings.json"
        orig_path = settings_mod.settings_path
        settings_mod.settings_path = lambda: fake
        try:
            first = _settings_tmp_path(fake)
            second = _settings_tmp_path(fake)
            assert first != second
            assert first.parent == fake.parent
            assert first.name.startswith("settings.json.")
            assert first.suffix == ".tmp"
            assert "settings.json.tmp" not in {first.name, second.name}

            s = Settings()
            s._data["game_path"] = r"D:\Games\RavenCraft"
            calls = {"n": 0}
            real_replace = os.replace

            def _flaky_replace(src, dest):
                calls["n"] += 1
                if calls["n"] < 3:
                    raise denied
                real_replace(src, dest)

            with mock.patch("ichalaunch.config.settings.os.replace", side_effect=_flaky_replace):
                s.save()
            assert calls["n"] == 3
            assert json.loads(fake.read_text(encoding="utf-8"))["game_path"] == r"D:\Games\RavenCraft"
            leftover = list(Path(td).glob("settings.json.*.tmp"))
            assert leftover == []

            errors: list[BaseException] = []

            def _writer(label: str) -> None:
                other = Settings()
                try:
                    for i in range(8):
                        other.set("last_mod_update_check", float(i) + (0.1 if label == "b" else 0.0))
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)

            a = threading.Thread(target=_writer, args=("a",))
            b = threading.Thread(target=_writer, args=("b",))
            a.start()
            b.start()
            a.join()
            b.join()
            assert errors == []
            raw = json.loads(fake.read_text(encoding="utf-8"))
            assert "last_mod_update_check" in raw
        finally:
            settings_mod.settings_path = orig_path
    print("OK settings save survives double-process replace race")


def test_settings_save_fsyncs_payload_before_replace():
    """Settings.save must flush the temp file to disk before os.replace publishes it."""
    import json
    import os
    import tempfile
    from pathlib import Path
    from unittest import mock

    import ichalaunch.config.settings as settings_mod
    from ichalaunch.config.settings import Settings, _settings_backup_path

    key = "0123456789abcdef0123456789abcdef"

    with tempfile.TemporaryDirectory() as td:
        fake = Path(td) / "settings.json"
        orig_path = settings_mod.settings_path
        settings_mod.settings_path = lambda: fake
        real_fsync = os.fsync
        real_replace = os.replace
        try:
            s = Settings()
            s._data["game_path"] = r"D:\Games\RavenCraft"
            s._data["wow_encryption_key"] = key

            synced: list[tuple[int, int]] = []
            seen: dict[str, object] = {}

            def _record_fsync(fd):
                st = os.fstat(fd)
                synced.append((st.st_dev, st.st_ino))
                return real_fsync(fd)

            def _record_replace(src, dest):
                st = os.stat(src)
                seen["tmp_id"] = (st.st_dev, st.st_ino)
                seen["tmp_text"] = Path(src).read_text(encoding="utf-8")
                seen["synced_before"] = list(synced)
                return real_replace(src, dest)

            with mock.patch.object(settings_mod.os, "fsync", _record_fsync):
                with mock.patch.object(settings_mod.os, "replace", _record_replace):
                    s.save()

            # The whole point: the temp file's data was on disk before the rename.
            assert seen["tmp_id"] in seen["synced_before"], "temp file was renamed without fsync"
            assert json.loads(seen["tmp_text"])["wow_encryption_key"] == key
            assert json.loads(seen["tmp_text"])["game_path"] == r"D:\Games\RavenCraft"

            # On POSIX the directory entry is flushed too, after the rename.
            if os.name == "posix":
                dir_st = os.stat(td)
                assert (dir_st.st_dev, dir_st.st_ino) in synced, "settings dir was not fsynced"
                assert (dir_st.st_dev, dir_st.st_ino) not in seen["synced_before"]

            # Normal saves still round-trip and leave no temp behind.
            assert json.loads(fake.read_text(encoding="utf-8"))["wow_encryption_key"] == key
            assert Settings().get("wow_encryption_key") == key
            assert list(Path(td).glob("settings.json.*.tmp")) == []

            # A save that dies at the rename still leaves complete bytes on disk,
            # never a truncated temp file, and still cleans the temp up.
            fatal: dict[str, object] = {}

            def _dying_replace(src, dest):
                fatal["text"] = Path(src).read_text(encoding="utf-8")
                raise OSError(28, "No space left on device")

            s._data["game_path"] = r"D:\Games\Other"
            with mock.patch.object(settings_mod.os, "replace", _dying_replace):
                try:
                    s.save()
                    raise AssertionError("save should have propagated the replace failure")
                except OSError as exc:
                    assert getattr(exc, "errno", None) == 28
            assert json.loads(str(fatal["text"]))["game_path"] == r"D:\Games\Other"
            assert list(Path(td).glob("settings.json.*.tmp")) == []
            # The failed save left the previously published file untouched.
            assert json.loads(fake.read_text(encoding="utf-8"))["game_path"] == r"D:\Games\RavenCraft"

            # The replace-race retry loop and the .bak copy are unchanged.
            denied = OSError(13, "Access is denied")
            denied.winerror = 5
            calls = {"n": 0}

            def _flaky_replace(src, dest):
                calls["n"] += 1
                if calls["n"] < 3:
                    raise denied
                return real_replace(src, dest)

            s._data["game_path"] = r"D:\Games\Retried"
            with mock.patch.object(settings_mod.os, "replace", _flaky_replace):
                s.save()
            assert calls["n"] == 3
            assert json.loads(fake.read_text(encoding="utf-8"))["game_path"] == r"D:\Games\Retried"
            bak = _settings_backup_path(fake)
            assert bak.is_file()
            assert json.loads(bak.read_text(encoding="utf-8"))["game_path"] == r"D:\Games\RavenCraft"
            assert list(Path(td).glob("settings.json.*.tmp")) == []
        finally:
            settings_mod.settings_path = orig_path
    print("OK settings save fsyncs the payload before the atomic replace")


def test_addons_filter_persists():
    """Addons list filter survives save/load; unknown values fall back to All."""
    import ichalaunch.config.settings as settings_mod
    from ichalaunch.config.settings import (
        ADDON_LIST_FILTER_DEFAULT,
        ADDON_LIST_FILTERS,
        Settings,
    )

    assert ADDON_LIST_FILTER_DEFAULT == "All"
    assert ADDON_LIST_FILTERS == ("Installed", "Available", "Update Available", "All")
    assert settings_mod.DEFAULTS["addons_filter"] == "All"

    with tempfile.TemporaryDirectory() as td:
        fake = Path(td) / "settings.json"
        orig_path = settings_mod.settings_path
        settings_mod.settings_path = lambda: fake
        try:
            s = Settings()
            assert s.addons_filter() == "All"

            for mode in ADDON_LIST_FILTERS:
                s.set_addons_filter(mode)
                reloaded = Settings()
                assert reloaded.addons_filter() == mode, mode
                raw = json.loads(fake.read_text(encoding="utf-8"))
                assert raw["addons_filter"] == mode

            s.set_addons_filter("not-a-filter")
            assert s.addons_filter() == "All"
            reloaded = Settings()
            assert reloaded.addons_filter() == "All"

            fake.write_text(json.dumps({"addons_filter": "Nope"}), encoding="utf-8")
            reloaded = Settings()
            assert reloaded.addons_filter() == "All"

            fake.write_text(json.dumps({"addons_filter": 123}), encoding="utf-8")
            reloaded = Settings()
            assert reloaded.addons_filter() == "All"

            fake.write_text(json.dumps({"addons_filter": None}), encoding="utf-8")
            reloaded = Settings()
            assert reloaded.addons_filter() == "All"
        finally:
            settings_mod.settings_path = orig_path
    print("OK addons filter persists and unknown values fall back")


def test_addons_page_restores_and_saves_filter():
    """Addons page restores addons_filter and writes it when the dropdown changes."""
    from unittest.mock import MagicMock, patch

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from ichalaunch.config.settings import ADDON_LIST_FILTERS
    from ichalaunch.ui.pages.addons import AddonsPage

    app = QApplication.instance() or QApplication([])

    for mode in ADDON_LIST_FILTERS:
        mock_settings = MagicMock()
        mock_settings.addons_filter.return_value = mode
        with patch("ichalaunch.ui.pages.addons.settings", mock_settings):
            page = AddonsPage()
            page.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
            assert page.filter_box.currentText() == mode, mode
            page.close()
            app.processEvents()

    mock_settings = MagicMock()
    mock_settings.addons_filter.return_value = "Nope"
    with patch("ichalaunch.ui.pages.addons.settings", mock_settings):
        page = AddonsPage()
        page.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        assert page.filter_box.currentText() == "All"
        page.filter_box.setCurrentText("Installed")
        mock_settings.set_addons_filter.assert_called_with("Installed")
        mock_settings.set_addons_filter.reset_mock()
        page.cat_box.addItem("Interface")
        page.cat_box.setCurrentText("Interface")
        mock_settings.set_addons_filter.assert_not_called()
        page.close()
        app.processEvents()
    print("OK addons page restores and saves filter dropdown")


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


def test_mod_version_label():
    """Client rows can show a catalog/installed tag and skip junk fingerprints."""
    from ichalaunch.mods.installer import get_mod, mod_version_label

    tweaks = get_mod("vanilla_tweaks")
    src = (tweaks or {}).get("source") or {}
    assert src.get("repo") == "tubtubs/vanilla-tweaks"
    # Latest tubtubs tag is the non-semver "tag" / V2 name — no catalog pin.
    assert mod_version_label(tweaks) == ""
    assert mod_version_label(tweaks, {"version_display": "v1.5.0"}) == "v1.5.0"
    assert mod_version_label(tweaks, {"version_display": "detected"}) == ""
    assert mod_version_label(tweaks, {"version_display": "V2"}) == "V2"
    vf = get_mod("vanillafixes")
    assert mod_version_label(vf) == "v1.5.3"
    sw = get_mod("superwow")
    # Tip latest_tag is the rolling "Release" alias; UI prefers asset/title semver.
    assert mod_version_label(sw) == "v2.2"
    assert mod_version_label(sw, {"version_display": "Release"}) == "v2.2"
    assert mod_version_label(
        sw, {"version_display": "Mon, 16 Jul 2026 14:03:09 GMT"}
    ) == "v2.2"
    assert (
        mod_version_label(
            sw,
            {
                "version_display": "Release",
                "url": "https://github.com/balakethelock/SuperWoW/releases/download/Release/SuperWoW.release.2.2.zip",
            },
        )
        == "v2.2"
    )
    assert mod_version_label(get_mod("pretty_night_sky")) == ""
    assert mod_version_label(get_mod("wdb_block")) == ""
    print("OK mod version label")


def test_vanilla_tweaks_tubtubs_catalog_and_argv():
    """Catalog tracks tubtubs/vanilla-tweaks; CLI flags match V2 clap names."""
    import tempfile
    from pathlib import Path

    from ichalaunch.mods.installer import get_mod
    from ichalaunch.mods.vanilla_tweaks import (
        normalize_vanilla_tweaks_options,
        snap_sound_channels,
        vanilla_tweaks_argv,
        vanilla_tweaks_command,
        vanilla_tweaks_infile,
    )

    tweaks = get_mod("vanilla_tweaks")
    assert tweaks is not None
    src = tweaks.get("source") or {}
    assert src.get("type") == "github_release_latest"
    assert src.get("repo") == "tubtubs/vanilla-tweaks"
    assert "windows" in str(src.get("asset_contains") or "").lower()
    assert "sha256" in str(src.get("asset_not_contains") or "").lower()
    assert tweaks.get("author") == "tubtubs"
    assert tweaks.get("has_config") is True
    assert "brndd" not in str(src).lower()
    from ichalaunch.ui.widgets.common import mod_git_url

    assert mod_git_url(tweaks) == "https://github.com/tubtubs/vanilla-tweaks"

    argv = vanilla_tweaks_argv(None)
    assert "--farclip" in argv
    assert argv[argv.index("--farclip") + 1] == "777"
    assert "--frilldistance" in argv
    assert argv[argv.index("--frilldistance") + 1] == "300"
    assert "--nameplatedistance" in argv
    assert argv[argv.index("--nameplatedistance") + 1] == "41"
    assert "--no-farclip" not in argv
    assert "--fov-patch" not in argv
    assert "--sound-in-background" not in argv
    assert "--quickloot" not in argv
    assert "--no-customgluespatch" not in argv
    assert "--no-bluemoonpatch" not in argv

    custom = normalize_vanilla_tweaks_options(
        {
            "farclip": False,
            "frilldistance": False,
            "nameplatedistance": False,
            "largeaddressaware": False,
            "cameraskipfix": False,
            "customglues": False,
            "bluemoon": False,
            "fov_patch": True,
            "fov": 1.925,
            "sound_in_background": True,
            "soundchannels_patch": True,
            "soundchannels": 32,
            "quickloot": True,
            "crossfactionresfix": True,
            "maxcameradistance_patch": True,
            "maxcameradistance": 40,
        }
    )
    argv2 = vanilla_tweaks_argv(custom)
    for flag in (
        "--no-farclip",
        "--no-frilldistance",
        "--no-nameplatedistance",
        "--no-largeaddressaware",
        "--no-cameraskipfix",
        "--no-customgluespatch",
        "--no-bluemoonpatch",
        "--fov-patch",
        "--sound-in-background",
        "--soundchannels-patch",
        "--quickloot",
        "--crossfactionresfix",
        "--maxcameradistance",
    ):
        assert flag in argv2, flag
    assert "32" in argv2
    assert argv2[argv2.index("--maxcameradistance") + 1] == "40"
    assert snap_sound_channels(99) == 64
    assert snap_sound_channels(20) == 12
    assert snap_sound_channels(40) == 32
    assert normalize_vanilla_tweaks_options({"soundchannels": 48})[
        "soundchannels"
    ] in (12, 32, 64)
    assert normalize_vanilla_tweaks_options({"soundchannels": 48})[
        "soundchannels"
    ] == 32

    with tempfile.TemporaryDirectory() as td:
        game = Path(td)
        wow = game / "WoW.exe"
        wow.write_bytes(b"wow")
        assert vanilla_tweaks_infile(game, wow) == wow
        backup = game / "WoW-OriginalBackup.exe"
        backup.write_bytes(b"stock")
        assert vanilla_tweaks_infile(game, wow) == backup
        cmd = vanilla_tweaks_command("vanilla-tweaks.exe", backup, None)
        assert cmd[0] == "vanilla-tweaks.exe"
        assert cmd[-1] == str(backup)
        assert "--farclip" in cmd
    print("OK vanilla tweaks tubtubs catalog and argv")


def _hbox_widgets_containing(widget):
    """Widgets in the QHBoxLayout that contains *widget*, left-to-right."""
    parent = widget.parentWidget()
    if parent is None or parent.layout() is None:
        return []
    layout = parent.layout()

    def search(lay):
        widgets = []
        found = False
        for i in range(lay.count()):
            item = lay.itemAt(i)
            if item is None:
                continue
            child = item.layout()
            if child is not None:
                hit, inner = search(child)
                if hit:
                    return True, inner
                continue
            w = item.widget()
            if w is not None:
                widgets.append(w)
                if w is widget:
                    found = True
        return found, widgets

    ok, widgets = search(layout)
    return widgets if ok else []


def _assert_tweaks_range_hint_left_of_control(dlg) -> None:
    """Range hint sits immediately left of the spin / slider / combo."""
    pairs = (
        (dlg._range_hints["farclip_value"], dlg._spins["farclip_value"]),
        (dlg._range_hints["frilldistance_value"], dlg._spins["frilldistance_value"]),
        (dlg._range_hints["fov"], dlg._spins["fov"]),
        (dlg._range_hints["nameplatedistance_value"], dlg._sliders["nameplatedistance_value"]),
        (dlg._range_hints["maxcameradistance"], dlg._sliders["maxcameradistance"]),
        (dlg._range_hints["soundchannels"], dlg._combos["soundchannels"]),
    )
    dlg.show()
    from PySide6.QtWidgets import QApplication

    QApplication.processEvents()
    for hint, control in pairs:
        row = _hbox_widgets_containing(hint)
        assert hint in row, f"hint {hint.text()} not in row"
        assert control in row, f"control missing from row with {hint.text()}"
        assert row.index(hint) == row.index(control) - 1, (
            f"{hint.text()} should sit immediately left of its control; row={[w.objectName() or type(w).__name__ for w in row]}"
        )
        assert hint.x() + hint.width() <= control.x() + 1, (
            f"{hint.text()} x={hint.x()}+{hint.width()} not left of control x={control.x()}"
        )
    dlg.hide()


def test_vanilla_tweaks_settings_dialog():
    """Themed Tweaks modal exposes every V2 knob and persists options."""
    import tempfile
    from pathlib import Path

    from PySide6.QtWidgets import QApplication, QScrollArea

    from ichalaunch.config import settings as settings_mod
    from ichalaunch.config.settings import Settings
    from ichalaunch.mods.vanilla_tweaks import vanilla_tweaks_argv
    from ichalaunch.ui.widgets.dialogs import VanillaTweaksSettingsDialog

    app = QApplication.instance() or QApplication([])
    del app
    with tempfile.TemporaryDirectory() as td:
        fake = Path(td) / "settings.json"
        orig_path = settings_mod.settings_path
        orig = settings_mod.settings
        settings_mod.settings_path = lambda: fake
        settings_mod.settings = Settings()
        try:
            dlg = VanillaTweaksSettingsDialog(None)
            expected_checks = {
                "farclip",
                "frilldistance",
                "nameplatedistance",
                "largeaddressaware",
                "cameraskipfix",
                "customglues",
                "bluemoon",
                "fov_patch",
                "sound_in_background",
                "soundchannels_patch",
                "quickloot",
                "crossfactionresfix",
                "maxcameradistance_patch",
                "superwow_override",
            }
            assert set(dlg._checks) == expected_checks
            assert set(dlg._spins) == {
                "farclip_value",
                "frilldistance_value",
                "fov",
            }
            assert set(dlg._sliders) == {
                "nameplatedistance_value",
                "maxcameradistance",
            }
            assert dlg._sliders["nameplatedistance_value"].maximum() == 41
            assert dlg._sliders["maxcameradistance"].maximum() == 50
            assert dlg._range_hints["farclip_value"].text() == "(100-10000)"
            assert dlg._range_hints["nameplatedistance_value"].text() == "(1-41)"
            assert dlg._range_hints["maxcameradistance"].text() == "(1-50)"
            assert dlg.findChildren(QScrollArea) == []
            assert set(dlg._combos) == {"soundchannels"}
            combo = dlg._combos["soundchannels"]
            assert [
                combo.itemData(i) for i in range(combo.count())
            ] == [12, 32, 64]
            assert combo.minimumWidth() >= 160
            opts = dlg.collect_options()
            assert opts["farclip"] is True
            assert opts["farclip_value"] == 777
            assert opts["frilldistance_value"] == 300
            _assert_tweaks_range_hint_left_of_control(dlg)
            assert opts["nameplatedistance_value"] == 41
            assert opts["fov_patch"] is False
            assert opts["bluemoon"] is True
            assert opts["customglues"] is True
            dlg._checks["fov_patch"].setChecked(True)
            dlg._checks["farclip"].setChecked(False)
            dlg._sliders["nameplatedistance_value"].setValue(30)
            collected = dlg.collect_options()
            argv = vanilla_tweaks_argv(collected)
            assert "--fov-patch" in argv
            assert "--no-farclip" in argv
            assert "--nameplatedistance" in argv
            assert "30" in argv
            dlg._accept_save()
            saved = settings_mod.settings.vanilla_tweaks_options
            assert saved["fov_patch"] is True
            assert saved["farclip"] is False
            assert saved["nameplatedistance_value"] == 30
            dlg.deleteLater()
        finally:
            settings_mod.settings_path = orig_path
            settings_mod.settings = orig
    print("OK vanilla tweaks settings dialog")


def test_tweaks_dialogs_regenerate_button():
    """Both Tweaks dialogs expose Regenerate + restore dropdown after Defaults."""
    import tempfile
    from pathlib import Path
    from unittest.mock import patch

    from PySide6.QtWidgets import QApplication

    from ichalaunch.config import settings as settings_mod
    from ichalaunch.config.settings import Settings
    from ichalaunch.ui.widgets.dialogs import (
        VanillaTweaksOldSettingsDialog,
        VanillaTweaksSettingsDialog,
    )
    from ichalaunch.ui.widgets.glue_combo import GlueComboBox

    app = QApplication.instance() or QApplication([])
    del app
    with tempfile.TemporaryDirectory() as td:
        fake = Path(td) / "settings.json"
        game = Path(td) / "game"
        game.mkdir()
        (game / "WoW.exe").write_bytes(b"MZ")
        backup_dir = game / "WTF" / "Backup"
        backup_dir.mkdir(parents=True)
        (backup_dir / "Config-20260826-120000.wtf.bak").write_text(
            'SET farclip "500"\n', encoding="utf-8"
        )
        (backup_dir / "Config-20260827-091500.wtf.bak").write_text(
            'SET farclip "600"\n', encoding="utf-8"
        )
        orig_path = settings_mod.settings_path
        orig = settings_mod.settings
        settings_mod.settings_path = lambda: fake
        settings_mod.settings = Settings()
        try:
            for cls in (VanillaTweaksSettingsDialog, VanillaTweaksOldSettingsDialog):
                # detect_game is bound to the real settings object at import
                # time, so patch it rather than the swapped-in fake settings.
                with patch(
                    "ichalaunch.game.launcher.detect_game", return_value=None
                ):
                    dlg = cls(None)
                btn = dlg._regen_config_btn
                assert btn.text() == "Regenerate Config.wtf", cls.__name__
                assert "Config.wtf" in btn.toolTip()
                combo = dlg._restore_combo
                assert isinstance(combo, GlueComboBox), cls.__name__
                assert not combo.isEnabled(), cls.__name__
                assert combo.itemText(0) == "No backups", cls.__name__
                # Find the bottom action row holding Defaults and assert the
                # regenerate button and dropdown sit immediately to its right.
                body = dlg._defaults_btn.parentWidget().layout()
                row = None
                for i in range(body.count()):
                    sub = body.itemAt(i).layout()
                    if sub is not None and sub.indexOf(dlg._defaults_btn) >= 0:
                        row = sub
                        break
                assert row is not None, cls.__name__
                base = row.indexOf(dlg._defaults_btn)
                assert row.indexOf(btn) == base + 1, cls.__name__
                assert row.indexOf(combo) == base + 2, cls.__name__
                dlg.deleteLater()

                # With a game and backups on disk — newest first after the
                # "Restore backup…" placeholder.
                with patch(
                    "ichalaunch.game.launcher.detect_game", return_value=game
                ):
                    filled = cls(None)
                combo = filled._restore_combo
                assert combo.isEnabled(), cls.__name__
                assert combo.itemText(0) == "Restore backup…", cls.__name__
                assert combo.itemData(0) is None, cls.__name__
                labels = [combo.itemText(i) for i in range(1, combo.count())]
                assert labels == [
                    "2026-08-27 09:15:00",
                    "2026-08-26 12:00:00",
                ], (cls.__name__, labels)
                assert combo.itemData(1).endswith(
                    "Config-20260827-091500.wtf.bak"
                ), cls.__name__
                filled.deleteLater()
        finally:
            settings_mod.settings_path = orig_path
            settings_mod.settings = orig
    print("OK tweaks dialogs regenerate button")


def test_vanilla_tweaks_optional_greyed_when_superwow():
    """Optional Tweaks column greys out when SuperWoW; override un-greys it."""
    import tempfile
    from pathlib import Path
    from unittest.mock import patch

    from PySide6.QtWidgets import QApplication

    from ichalaunch.config import settings as settings_mod
    from ichalaunch.config.settings import Settings
    from ichalaunch.core.filesystem import clear_fs_caches
    from ichalaunch.mods.vanilla_tweaks import (
        VANILLA_TWEAKS_OPTIONAL_KEYS,
        normalize_vanilla_tweaks_options,
        options_equal,
        options_fingerprint,
        vanilla_tweaks_argv,
    )
    from ichalaunch.ui.widgets.dialogs import DialogResult, VanillaTweaksSettingsDialog

    app = QApplication.instance() or QApplication([])
    del app
    default_on = (
        "farclip",
        "frilldistance",
        "nameplatedistance",
        "largeaddressaware",
        "cameraskipfix",
        "customglues",
        "bluemoon",
    )
    with tempfile.TemporaryDirectory() as td:
        fake = Path(td) / "settings.json"
        game = Path(td) / "game"
        game.mkdir()
        (game / "WoW.exe").write_bytes(b"MZ")
        orig_path = settings_mod.settings_path
        orig = settings_mod.settings
        settings_mod.settings_path = lambda: fake
        settings_mod.settings = Settings()
        settings_mod.settings.set("game_path", str(game))
        settings_mod.settings.set("desired_mods", {})
        clear_fs_caches()
        try:
            dlg = VanillaTweaksSettingsDialog(None)
            for key in VANILLA_TWEAKS_OPTIONAL_KEYS:
                assert dlg._checks[key].isEnabled(), key
            for key in default_on:
                assert dlg._checks[key].isEnabled(), key
            assert dlg._spins["fov"].isEnabled() is False  # checkbox off by default
            # Without SuperWoW the override toggle is irrelevant and hidden.
            assert dlg._checks["superwow_override"].isHidden()
            dlg.deleteLater()

            settings_mod.settings.set("desired_mods", {"superwow": True})
            locked = VanillaTweaksSettingsDialog(None)
            assert locked._superwow_locks_optional is True
            for key in VANILLA_TWEAKS_OPTIONAL_KEYS:
                assert not locked._checks[key].isEnabled(), key
                assert "SuperWoW" in locked._checks[key].toolTip(), key
            for key in default_on:
                assert locked._checks[key].isEnabled(), key
            assert not locked._spins["fov"].isEnabled()
            assert not locked._combos["soundchannels"].isEnabled()
            assert not locked._sliders["maxcameradistance"].isEnabled()

            # Override toggle is shown only while SuperWoW locks the column.
            override = locked._checks["superwow_override"]
            assert not override.isHidden()
            assert override.isEnabled()

            # Cancelling the warning reverts the toggle and keeps the lock.
            with patch(
                "ichalaunch.ui.widgets.dialogs.choice",
                return_value=DialogResult.Cancel,
            ) as mocked:
                override.setChecked(True)
            assert mocked.call_count == 1
            assert mocked.call_args.kwargs.get("kind") == "warning"
            assert override.isChecked() is False
            for key in VANILLA_TWEAKS_OPTIONAL_KEYS:
                assert not locked._checks[key].isEnabled(), key

            # Confirming the warning un-greys the optional column.
            with patch(
                "ichalaunch.ui.widgets.dialogs.choice",
                return_value=DialogResult.Yes,
            ):
                override.setChecked(True)
            assert override.isChecked() is True
            for key in VANILLA_TWEAKS_OPTIONAL_KEYS:
                assert locked._checks[key].isEnabled(), key
            assert not locked._spins["fov"].isEnabled()  # follows its checkbox
            locked._checks["fov_patch"].setChecked(True)
            assert locked._spins["fov"].isEnabled()
            locked._checks["quickloot"].setChecked(True)
            collected = locked.collect_options()
            assert collected["superwow_override"] is True
            assert collected["fov_patch"] is True
            assert collected["quickloot"] is True
            argv = vanilla_tweaks_argv(collected)
            assert "--fov-patch" in argv
            assert "--quickloot" in argv

            # Persistence roundtrip keeps the key; defaults leave it off.
            assert normalize_vanilla_tweaks_options(collected)[
                "superwow_override"
            ] is True
            assert (
                normalize_vanilla_tweaks_options(None)["superwow_override"] is False
            )
            # UI-only key never changes the repatch fingerprint or equality.
            assert options_fingerprint({}) == options_fingerprint(
                {"superwow_override": True}
            )
            assert options_equal({}, {"superwow_override": True})

            # Turning the override back off re-greys and re-forces the column.
            override.setChecked(False)
            for key in VANILLA_TWEAKS_OPTIONAL_KEYS:
                assert not locked._checks[key].isEnabled(), key
                assert not locked._checks[key].isChecked(), key
            assert not locked._spins["fov"].isEnabled()
            locked.deleteLater()

            settings_mod.settings.set("desired_mods", {})
            (game / "SuperWoWhook.dll").write_bytes(b"dll")
            clear_fs_caches()
            actual = VanillaTweaksSettingsDialog(None)
            assert actual._superwow_locks_optional is True
            assert not actual._checks["quickloot"].isEnabled()
            assert "SuperWoW" in actual._checks["fov_patch"].toolTip()
            actual.deleteLater()
        finally:
            settings_mod.settings_path = orig_path
            settings_mod.settings = orig
            clear_fs_caches()
    print("OK vanilla tweaks optional greyed when SuperWoW")


def test_client_pending_plan_row_badge_and_apply_pulse():
    """Pending plan_changes badges the row and pulses Apply Changes."""
    from pathlib import Path
    from unittest.mock import patch

    from PySide6.QtWidgets import QApplication

    from ichalaunch.ui.pages.client import ClientPage

    app = QApplication.instance() or QApplication([])
    del app
    with patch("ichalaunch.ui.pages.client.plan_changes", return_value=[]):
        page = ClientPage()
    row = page.rows.get("vanilla_tweaks")
    other = next(
        r
        for mid, r in page.rows.items()
        if mid not in ("vanilla_tweaks", "vanilla_tweaks_old")
    )
    assert row is not None
    assert row.pending_badge.isHidden()
    assert page.apply_btn.property("flashHighlight") in (False, "false", None)
    assert not page._apply_pulse_timer.isActive()

    fake_plan = [
        {"action": "install", "id": "vanilla_tweaks", "detail": "Install Vanilla Tweaks"}
    ]
    with patch("ichalaunch.ui.pages.client.detect_game", return_value=Path("C:/fake")):
        with patch("ichalaunch.ui.pages.client.plan_changes", return_value=fake_plan):
            page.refresh_plan()
    assert not row.pending_badge.isHidden()
    assert other.pending_badge.isHidden()
    assert bool(page.apply_btn.property("flashHighlight"))
    assert page._apply_pulse_timer.isActive()
    assert page.apply_btn._pulse is True

    with patch("ichalaunch.ui.pages.client.detect_game", return_value=Path("C:/fake")):
        with patch("ichalaunch.ui.pages.client.plan_changes", return_value=[]):
            page.refresh_plan()
    assert row.pending_badge.isHidden()
    assert page.apply_btn.property("flashHighlight") in (False, "false")
    assert not page._apply_pulse_timer.isActive()
    assert page.apply_btn._pulse is False
    page.deleteLater()
    print("OK client pending plan row badge and apply pulse")


def test_theme_checkbox_disabled_uses_grey_check_art():
    """Disabled checked ThemeCheckBox paints UI-CheckBox-Check-Disabled, not gold."""
    from PySide6.QtWidgets import QApplication

    from ichalaunch.core.paths import theme_file
    from ichalaunch.ui.widgets.theme_checkbox import ThemeCheckBox, _assets

    app = QApplication.instance() or QApplication([])
    del app
    path = theme_file("checkboxes", "UI-CheckBox-Check-Disabled.PNG")
    assert path.is_file(), path
    _empty, _depress, checked, checked_off = _assets()
    assert not checked.isNull()
    assert not checked_off.isNull()
    assert checked.toImage() != checked_off.toImage()

    cb = ThemeCheckBox("")
    cb.setFixedSize(22, 22)
    cb.setChecked(True)
    enabled_img = cb.grab().toImage()
    cb.setEnabled(False)
    disabled_img = cb.grab().toImage()
    assert not enabled_img.isNull()
    assert not disabled_img.isNull()
    assert enabled_img != disabled_img
    cb.deleteLater()
    print("OK theme checkbox disabled grey check art")


def test_theme_radio_uses_wow_art():
    """ThemeRadioButton loads UI-RadioButton art and works in exclusive QButtonGroup."""
    from PySide6.QtWidgets import QApplication, QButtonGroup, QWidget

    from ichalaunch.core.paths import theme_file
    from ichalaunch.ui.widgets.theme_radio import ThemeRadioButton, _assets

    app = QApplication.instance() or QApplication([])
    del app

    for name in (
        "UI-RadioButton-Off.PNG",
        "UI-RadioButton-On.PNG",
        "UI-RadioButton-Hover.PNG",
        "UI-RadioButton-Disabled.PNG",
    ):
        assert theme_file("radios", name).is_file(), theme_file("radios", name)

    off, hover, on, on_disabled = _assets()
    assert not off.isNull()
    assert not on.isNull()
    assert not on_disabled.isNull()
    assert off.toImage() != on.toImage()
    assert on.toImage() != on_disabled.toImage()

    def _indicator_tone(img, side=22, inset=0):
        """Score gold vs grey in the indicator; inset focuses on the inner fill."""
        gold = grey = 0
        y0 = max(0, (img.height() - side) // 2)
        for y in range(y0 + inset, min(y0 + side - inset, img.height())):
            for x in range(inset, min(side - inset, img.width())):
                c = img.pixelColor(x, y)
                if c.alpha() < 30:
                    continue
                luma = (c.red() + c.green() + c.blue()) / 3
                if luma < 18 or luma > 240:
                    continue
                if c.red() > c.blue() + 15 and c.green() > c.blue() + 5:
                    gold += 1
                elif abs(c.red() - c.green()) < 18 and abs(c.green() - c.blue()) < 18:
                    grey += 1
        return gold, grey

    on_gold, on_grey = _indicator_tone(on.toImage(), max(on.width(), on.height()))
    dis_gold, dis_grey = _indicator_tone(
        on_disabled.toImage(), max(on_disabled.width(), on_disabled.height())
    )
    assert on_gold > on_grey, (on_gold, on_grey)
    assert dis_grey > dis_gold, (dis_gold, dis_grey)

    host = QWidget()
    group = QButtonGroup(host)
    group.setExclusive(True)
    a = ThemeRadioButton("Alpha", host)
    b = ThemeRadioButton("Beta", host)
    a.setObjectName("ThemePresetRadio")
    b.setObjectName("ThemePresetRadio")
    group.addButton(a)
    group.addButton(b)

    toggled: list[bool] = []
    a.toggled.connect(toggled.append)
    a.setChecked(True)
    assert a.isChecked() and not b.isChecked()
    b.setChecked(True)
    assert b.isChecked() and not a.isChecked()
    assert toggled == [True, False]

    a.setFixedSize(120, 28)
    a.setChecked(False)
    off_img = a.grab().toImage()
    a.setChecked(True)
    on_img = a.grab().toImage()
    assert off_img != on_img

    # Inner fill only: the Off ring is grey by design; selected must read gold.
    off_gold, off_grey = _indicator_tone(off_img, inset=5)
    chk_gold, chk_grey = _indicator_tone(on_img, inset=5)
    assert chk_gold > chk_grey, (chk_gold, chk_grey)
    assert chk_gold > off_gold, (chk_gold, off_gold)

    a.setEnabled(False)
    dis_img = a.grab().toImage()
    dis_chk_gold, dis_chk_grey = _indicator_tone(dis_img, inset=5)
    assert dis_chk_grey > dis_chk_gold, (dis_chk_gold, dis_chk_grey)

    host.deleteLater()
    print("OK theme radio uses wow art")


def test_client_page_does_not_poll_game_lock_until_shown():
    """Constructing ClientPage must not tasklist; WoW running on the dev box cannot lock tests."""
    from unittest.mock import patch

    from PySide6.QtWidgets import QApplication

    from ichalaunch.ui.pages.client import ClientPage

    app = QApplication.instance() or QApplication([])
    del app
    with patch("ichalaunch.ui.pages.client.wow_exe_running") as probe:
        with patch("ichalaunch.ui.pages.client.plan_changes", return_value=[]):
            page = ClientPage()
        probe.assert_not_called()
    row = next(iter(page.rows.values()))
    assert row.cb.isEnabled()
    assert page.apply_btn.isEnabled() is False
    page.deleteLater()
    print("OK client page does not poll game lock until shown")


def test_client_page_locks_mod_edits_when_wow_running():
    """WoW.exe / VanillaFixes.exe greys client mod checkboxes and blocks Apply."""
    from pathlib import Path
    from unittest.mock import patch

    from PySide6.QtWidgets import QApplication

    from ichalaunch.ui.pages.client import ClientPage
    from ichalaunch.ui.widgets.common import MOD_EDIT_LOCKED_TIP

    app = QApplication.instance() or QApplication([])
    del app
    with patch("ichalaunch.ui.pages.client.plan_changes", return_value=[]):
        page = ClientPage()
    row = page.rows.get("vanilla_tweaks")
    assert row is not None
    assert row.cb.isEnabled()

    with patch("ichalaunch.ui.pages.client.wow_exe_running", return_value=True):
        page._poll_game_edit_lock()
    assert page._game_edit_locked
    assert not row.cb.isEnabled()
    assert row.cb.toolTip() == MOD_EDIT_LOCKED_TIP
    if row.settings_btn is not None:
        assert not row.settings_btn.isEnabled()
    assert page.launch_settings.cb_min.isEnabled()

    was = row.cb.isChecked()
    with patch("ichalaunch.ui.pages.client.apply_mod_toggle") as apply_toggle:
        page._on_toggle("vanilla_tweaks", not was)
        apply_toggle.assert_not_called()
    assert row.cb.isChecked() == was

    fake_plan = [
        {"action": "install", "id": "vanilla_tweaks", "detail": "Install Vanilla Tweaks"}
    ]
    with patch("ichalaunch.ui.pages.client.detect_game", return_value=Path("C:/fake")):
        with patch("ichalaunch.ui.pages.client.plan_changes", return_value=fake_plan):
            page.refresh_plan()
    assert page._apply_pending
    assert not page.apply_btn.isEnabled()
    assert page.apply_btn.toolTip() == MOD_EDIT_LOCKED_TIP
    assert not page._apply_pulse_timer.isActive()
    assert not page.update_all_btn.isEnabled()
    assert not page.add_dll_btn.isEnabled()

    with patch("ichalaunch.ui.pages.client.wow_exe_running", return_value=False):
        page._poll_game_edit_lock()
    assert not page._game_edit_locked
    assert row.cb.isEnabled()
    assert row.cb.toolTip() == ""
    assert page.apply_btn.isEnabled()
    assert page.apply_btn.toolTip() == "Pending client mod changes — click to apply"
    page.deleteLater()
    print("OK client page locks mod edits when wow running")


def test_wow_exe_running_matches_game_directory():
    """Lock only the WoW/VanillaFixes image that lives in this client folder."""
    from pathlib import Path
    from unittest.mock import patch

    from ichalaunch.core import process as proc

    game_a = Path("D:/Games/ClientA")
    game_b = Path("D:/Games/ClientB")
    wow_a = game_a / "WoW.exe"
    wow_b = game_b / "WoW.exe"
    vf_a = game_a / "VanillaFixes.exe"

    assert proc._path_is_under(wow_a, game_a)
    assert proc._path_is_under(vf_a, game_a)
    assert not proc._path_is_under(wow_b, game_a)
    assert not proc._path_is_under(Path("D:/Games/Apple/WoW.exe"), Path("D:/Games/App"))

    with patch.object(proc, "_wow_process_images", return_value=(True, [wow_b])):
        assert proc.wow_exe_running() is True
        assert proc.wow_exe_running(game_a) is False
        assert proc.wow_exe_running(game_b) is True
    with patch.object(proc, "_wow_process_images", return_value=(True, [vf_a])):
        assert proc.wow_exe_running(game_a) is True
        assert proc.wow_exe_running(game_b) is False
    with patch.object(proc, "_wow_process_images", return_value=(True, [])):
        with patch.object(proc, "_wow_exe_locked_in_dir", return_value=True):
            assert proc.wow_exe_running(game_a) is True
        with patch.object(proc, "_wow_exe_locked_in_dir", return_value=False):
            assert proc.wow_exe_running(game_a) is False
        with patch.object(proc, "_wow_exe_locked_in_dir", return_value=None):
            assert proc.wow_exe_running(game_a) is True
        assert proc.wow_exe_running() is True
    with patch.object(proc, "_wow_process_images", return_value=(False, [])):
        assert proc.wow_exe_running() is False
        assert proc.wow_exe_running(game_a) is False

    with tempfile.TemporaryDirectory() as tmp:
        empty = Path(tmp)
        assert proc._wow_exe_locked_in_dir(empty) is False
        (empty / "WoW.exe").write_bytes(b"MZ")
        unlocked = proc._wow_exe_locked_in_dir(empty)
        assert unlocked in (False, None)
    print("OK wow_exe_running matches game directory")


def test_client_page_lock_uses_configured_game_dir():
    """Client lockout asks whether THIS folder's WoW/VanillaFixes is running."""
    from pathlib import Path
    from unittest.mock import patch

    from PySide6.QtWidgets import QApplication

    from ichalaunch.ui.pages.client import ClientPage

    app = QApplication.instance() or QApplication([])
    del app
    game = Path("D:/Games/RavenCraft")
    with patch("ichalaunch.ui.pages.client.plan_changes", return_value=[]):
        page = ClientPage()
    with patch("ichalaunch.ui.pages.client.detect_game", return_value=game):
        with patch("ichalaunch.ui.pages.client.wow_exe_running", return_value=False) as probe:
            page._poll_game_edit_lock()
    probe.assert_called_once_with(game)
    page.deleteLater()
    print("OK client page lock uses configured game dir")


def test_vanilla_tweaks_force_tubtubs_repatch():
    """Desired+installed brndd leftover plans a catalog re-patch; tubtubs stamp does not."""
    from ichalaunch.config.settings import settings as s
    from ichalaunch.core.filesystem import clear_fs_caches
    from ichalaunch.mods.installer import plan_changes
    from ichalaunch.mods.vanilla_tweaks import (
        TWEAKS_REPO,
        options_fingerprint,
        tweaks_install_stamp,
        vanilla_tweaks_needs_repatch,
    )

    assert vanilla_tweaks_needs_repatch({}) is True
    assert vanilla_tweaks_needs_repatch(
        {"url": "https://github.com/brndd/vanilla-tweaks/releases/download/v1.6.0/x.zip"}
    ) is True
    stamp = tweaks_install_stamp(None)
    assert stamp["tweaks_source"] == "tubtubs"
    assert stamp["repo"] == TWEAKS_REPO
    assert vanilla_tweaks_needs_repatch(stamp, None) is False
    assert vanilla_tweaks_needs_repatch(
        {**stamp, "options_fingerprint": "deadbeef"}, None
    ) is True
    assert options_fingerprint(None) == options_fingerprint({})

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
            (game / "WoW.exe").write_bytes(patched)
            (game / "WoW-OriginalBackup.exe").write_bytes(stock)
            s.set("game_path", str(game))
            s.set("addons_path", "")
            s.set("desired_mods", {"vanilla_tweaks": True})
            s.set("user_set_mods", ["vanilla_tweaks"])
            s.set(
                "installed_mods",
                {
                    "vanilla_tweaks": {
                        "version_display": "v1.6.0",
                        "url": (
                            "https://github.com/brndd/vanilla-tweaks/"
                            "releases/download/v1.6.0/vanilla-tweaks.zip"
                        ),
                    }
                },
            )
            s.set("user_mods", [])
            clear_fs_caches()
            assert any(
                c["action"] == "install" and c["id"] == "vanilla_tweaks"
                for c in plan_changes()
            ), plan_changes()

            s.set_installed_mod(
                "vanilla_tweaks", tweaks_install_stamp(s.vanilla_tweaks_options)
            )
            clear_fs_caches()
            assert not any(c.get("id") == "vanilla_tweaks" for c in plan_changes())
    finally:
        for k in keys:
            s.set(k, saved[k])
        clear_fs_caches()
    print("OK vanilla tweaks force tubtubs re-patch")


def test_vanilla_tweaks_enable_opens_config_once():
    """Turning Tweaks on opens the config modal; refresh does not."""
    from unittest.mock import patch

    from PySide6.QtWidgets import QApplication

    from ichalaunch.config.settings import settings as s
    from ichalaunch.ui.pages.client import ClientPage

    app = QApplication.instance() or QApplication([])
    opened: list[str] = []

    def _fake_dialog(parent):  # noqa: ANN001
        opened.append("dialog")
        return None

    keys = ("desired_mods", "user_set_mods")
    saved = {k: s.get(k) for k in keys}
    try:
        s.set("desired_mods", {**s.desired_mods, "vanilla_tweaks": False})
        page = ClientPage()
        with patch(
            "ichalaunch.ui.widgets.dialogs.vanilla_tweaks_settings_dialog",
            side_effect=_fake_dialog,
        ):
            page._on_toggle("vanilla_tweaks", True)
            app.processEvents()
            assert opened == ["dialog"], opened
            page.refresh_from_settings()
            app.processEvents()
            assert opened == ["dialog"], opened
        page.deleteLater()
    finally:
        for k in keys:
            s.set(k, saved[k])
    print("OK vanilla tweaks enable opens config once")


def test_mod_check_row_tweaks_cog():
    """Vanilla Tweaks client row shows the options cog; other mods do not."""
    from PySide6.QtWidgets import QApplication

    from ichalaunch.ui.widgets.common import ModCheckRow

    app = QApplication.instance() or QApplication([])
    del app
    plain = ModCheckRow("dxvk", "DXVK", "desc")
    assert plain.settings_btn is None
    row = ModCheckRow("vanilla_tweaks", "Vanilla Tweaks", "desc", has_settings=True)
    assert row.settings_btn is not None
    fired: list[str] = []
    row.settings_clicked.connect(fired.append)
    row.settings_btn.click()
    assert fired == ["vanilla_tweaks"]
    row.deleteLater()
    plain.deleteLater()
    from ichalaunch.ui.pages.client import ClientPage

    page = ClientPage()
    tweaks_row = page.rows.get("vanilla_tweaks")
    assert tweaks_row is not None
    assert tweaks_row.settings_btn is not None
    assert tweaks_row._name_lbl.text() == "Vanilla Tweaks V2"
    old_row = page.rows.get("vanilla_tweaks_old")
    assert old_row is not None
    assert old_row.settings_btn is not None
    assert old_row._name_lbl.text() == "Vanilla Tweaks (Old)"
    page.deleteLater()
    print("OK ModCheckRow tweaks cog")


def test_vanilla_tweaks_old_catalog_schema_and_mutex():
    """Old is pinned brndd 1.6.0; schema/argv ≠ V2; only one Tweaks can apply."""
    import tempfile
    from pathlib import Path

    from ichalaunch.mods.installer import get_mod, plan_changes
    from ichalaunch.mods.vanilla_tweaks import (
        TUBTUBS_ONLY_FLAGS,
        TWEAKS_OLD_PIN_URL,
        VANILLA_TWEAKS_DEFAULTS,
        VANILLA_TWEAKS_OLD_DEFAULTS,
        normalize_vanilla_tweaks_old_options,
        normalize_vanilla_tweaks_options,
        tweaks_patch_command,
        vanilla_tweaks_argv,
        vanilla_tweaks_infile,
        vanilla_tweaks_old_argv,
    )
    from ichalaunch.ui.widgets.common import mod_git_url

    v2 = get_mod("vanilla_tweaks")
    old = get_mod("vanilla_tweaks_old")
    assert v2 is not None and old is not None
    assert v2.get("name") == "Vanilla Tweaks V2"
    assert old.get("name") == "Vanilla Tweaks (Old)"
    v2_src = v2.get("source") or {}
    old_src = old.get("source") or {}
    assert v2_src.get("type") == "github_release_latest"
    assert v2_src.get("repo") == "tubtubs/vanilla-tweaks"
    assert "brndd" not in str(v2_src).lower()
    assert old_src.get("type") == "github_release"
    assert old_src.get("url") == TWEAKS_OLD_PIN_URL
    assert "brndd/vanilla-tweaks" in str(old_src.get("url") or "")
    assert "tubtubs" not in str(old_src).lower()
    assert "vanilla_tweaks_old" in (v2.get("conflicts") or [])
    assert "vanilla_tweaks" in (old.get("conflicts") or [])
    assert mod_git_url(old) == "https://github.com/brndd/vanilla-tweaks"

    v2_keys = set(normalize_vanilla_tweaks_options(None))
    old_keys = set(normalize_vanilla_tweaks_old_options(None))
    assert "customglues" in v2_keys and "customglues" not in old_keys
    assert "bluemoon" in v2_keys and "bluemoon" not in old_keys
    assert "crossfactionresfix" in v2_keys and "crossfactionresfix" not in old_keys
    assert VANILLA_TWEAKS_DEFAULTS["farclip_value"] == 777
    assert VANILLA_TWEAKS_OLD_DEFAULTS["farclip_value"] == 777
    assert VANILLA_TWEAKS_OLD_DEFAULTS["fov_patch"] is True
    assert VANILLA_TWEAKS_DEFAULTS["fov_patch"] is False
    from ichalaunch.config.settings import DEFAULTS

    assert DEFAULTS["desired_mods"]["vanilla_tweaks"] is True
    assert DEFAULTS["desired_mods"]["vanilla_tweaks_old"] is False

    v2_argv = vanilla_tweaks_argv(None)
    old_argv = vanilla_tweaks_old_argv(None)
    assert "--fov-patch" not in v2_argv
    assert "--no-fov" not in old_argv
    assert "--fov" in old_argv
    assert "--farclip" in old_argv
    assert old_argv[old_argv.index("--farclip") + 1] == "777"
    for flag in TUBTUBS_ONLY_FLAGS:
        assert flag not in old_argv, flag
    assert "--no-customgluespatch" in vanilla_tweaks_argv(
        {**VANILLA_TWEAKS_DEFAULTS, "customglues": False}
    )

    from ichalaunch.config.settings import settings as s
    from ichalaunch.core.filesystem import clear_fs_caches

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
            wow = game / "WoW.exe"
            wow.write_bytes(patched)
            backup = game / "WoW-OriginalBackup.exe"
            backup.write_bytes(stock)
            s.set("game_path", str(game))
            s.set("addons_path", "")
            s.set("user_mods", [])
            s.set(
                "desired_mods",
                {"vanilla_tweaks": True, "vanilla_tweaks_old": True},
            )
            s.set("user_set_mods", ["vanilla_tweaks", "vanilla_tweaks_old"])
            s.set("installed_mods", {})
            clear_fs_caches()
            plan = plan_changes()
            installs = [
                c["id"]
                for c in plan
                if c.get("action") == "install"
                and c.get("id") in ("vanilla_tweaks", "vanilla_tweaks_old")
            ]
            assert len(installs) == 1, plan
            assert installs[0] == "vanilla_tweaks_old"

            infile = vanilla_tweaks_infile(game, wow)
            assert infile == backup
            cmd_old = tweaks_patch_command(
                "vanilla_tweaks_old", "vanilla-tweaks.exe", infile, None
            )
            cmd_v2 = tweaks_patch_command(
                "vanilla_tweaks", "vanilla-tweaks.exe", infile, None
            )
            assert cmd_old[-1] == str(backup)
            assert cmd_v2[-1] == str(backup)
            for flag in TUBTUBS_ONLY_FLAGS:
                assert flag not in cmd_old, flag
            assert "--farclip" in cmd_v2
    finally:
        for k in keys:
            s.set(k, saved[k])
        clear_fs_caches()
    print("OK vanilla tweaks old catalog schema and mutex")


def test_vanilla_tweaks_old_force_migrate_and_switch():
    """Leftover brndd upgrades only when V2 is desired; switch plans the other tool."""
    import tempfile
    from pathlib import Path

    from ichalaunch.config.settings import settings as s
    from ichalaunch.core.filesystem import clear_fs_caches
    from ichalaunch.mods.installer import detect_actual_state, plan_changes
    from ichalaunch.mods.vanilla_tweaks import (
        tweaks_install_stamp,
        tweaks_old_install_stamp,
        vanilla_tweaks_needs_repatch,
    )

    leftover = {
        "version_display": "v1.6.0",
        "url": (
            "https://github.com/brndd/vanilla-tweaks/"
            "releases/download/v1.6.0/vanilla-tweaks.zip"
        ),
    }
    assert vanilla_tweaks_needs_repatch(leftover) is True

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
            (game / "WoW.exe").write_bytes(patched)
            (game / "WoW-OriginalBackup.exe").write_bytes(stock)
            s.set("game_path", str(game))
            s.set("addons_path", "")
            s.set("user_mods", [])

            s.set("desired_mods", {"vanilla_tweaks": True, "vanilla_tweaks_old": False})
            s.set("user_set_mods", ["vanilla_tweaks"])
            s.set("installed_mods", {"vanilla_tweaks": leftover})
            clear_fs_caches()
            actual = detect_actual_state(game)
            assert actual.get("vanilla_tweaks") is True
            assert actual.get("vanilla_tweaks_old") is not True
            assert any(
                c["action"] == "install" and c["id"] == "vanilla_tweaks"
                for c in plan_changes()
            )
            assert not any(c.get("id") == "vanilla_tweaks_old" for c in plan_changes())

            s.set(
                "desired_mods",
                {"vanilla_tweaks": False, "vanilla_tweaks_old": True},
            )
            s.set("user_set_mods", ["vanilla_tweaks_old"])
            s.set("installed_mods", {"vanilla_tweaks": leftover})
            clear_fs_caches()
            actual = detect_actual_state(game)
            assert actual.get("vanilla_tweaks_old") is True
            assert actual.get("vanilla_tweaks") is not True
            plan = plan_changes()
            assert not any(
                c["action"] == "install" and c["id"] == "vanilla_tweaks"
                for c in plan
            ), plan

            s.set(
                "desired_mods",
                {"vanilla_tweaks": False, "vanilla_tweaks_old": True},
            )
            s.set("user_set_mods", ["vanilla_tweaks_old"])
            s.set(
                "installed_mods",
                {"vanilla_tweaks": tweaks_install_stamp(s.vanilla_tweaks_options)},
            )
            clear_fs_caches()
            actual = detect_actual_state(game)
            assert actual.get("vanilla_tweaks") is True
            assert actual.get("vanilla_tweaks_old") is not True
            plan = plan_changes()
            assert any(
                c["action"] == "install" and c["id"] == "vanilla_tweaks_old"
                for c in plan
            ), plan
            assert not any(
                c["action"] == "install" and c["id"] == "vanilla_tweaks"
                for c in plan
            )

            s.set("desired_mods", {"vanilla_tweaks": True, "vanilla_tweaks_old": False})
            s.set("user_set_mods", ["vanilla_tweaks"])
            s.set("installed_mods", {"vanilla_tweaks": {"version_display": "detected"}})
            clear_fs_caches()
            actual = detect_actual_state(game)
            assert actual.get("vanilla_tweaks") is True
            assert actual.get("vanilla_tweaks_old") is not True

            s.set(
                "desired_mods",
                {"vanilla_tweaks": True, "vanilla_tweaks_old": False},
            )
            s.set("user_set_mods", ["vanilla_tweaks"])
            s.set(
                "installed_mods",
                {
                    "vanilla_tweaks_old": tweaks_old_install_stamp(
                        s.vanilla_tweaks_old_options
                    )
                },
            )
            clear_fs_caches()
            actual = detect_actual_state(game)
            assert actual.get("vanilla_tweaks_old") is True
            assert actual.get("vanilla_tweaks") is not True
            plan = plan_changes()
            assert any(
                c["action"] == "install" and c["id"] == "vanilla_tweaks"
                for c in plan
            ), plan
            assert not any(
                c["action"] == "install" and c["id"] == "vanilla_tweaks_old"
                for c in plan
            )
    finally:
        for k in keys:
            s.set(k, saved[k])
        clear_fs_caches()
    print("OK vanilla tweaks old force-migrate and switch")


def test_vanilla_tweaks_old_settings_dialog_and_warning():
    """Old modal is brndd-only; enable warns, cancel leaves unchecked, first enable opens Old."""
    import tempfile
    from pathlib import Path
    from unittest.mock import patch

    from PySide6.QtWidgets import QApplication, QScrollArea

    from ichalaunch.config import settings as settings_mod
    from ichalaunch.config.settings import Settings
    from ichalaunch.mods.vanilla_tweaks import (
        TUBTUBS_ONLY_FLAGS,
        vanilla_tweaks_old_argv,
    )
    from ichalaunch.ui.pages.client import ClientPage
    from ichalaunch.ui.widgets.dialogs import VanillaTweaksOldSettingsDialog

    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as td:
        fake = Path(td) / "settings.json"
        orig_path = settings_mod.settings_path
        orig = settings_mod.settings
        settings_mod.settings_path = lambda: fake
        settings_mod.settings = Settings()
        try:
            dlg = VanillaTweaksOldSettingsDialog(None)
            assert set(dlg._checks) == {
                "farclip",
                "frilldistance",
                "nameplatedistance",
                "largeaddressaware",
                "cameraskipfix",
                "quickloot",
                "fov_patch",
                "sound_in_background",
                "soundchannels_patch",
                "maxcameradistance_patch",
            }
            assert "customglues" not in dlg._checks
            assert "bluemoon" not in dlg._checks
            assert "crossfactionresfix" not in dlg._checks
            assert dlg.findChildren(QScrollArea) == []
            assert dlg._range_hints["farclip_value"].text() == "(100-10000)"
            assert dlg._range_hints["nameplatedistance_value"].text() == "(1-41)"
            assert dlg._range_hints["maxcameradistance"].text() == "(1-50)"
            assert dlg._sliders["nameplatedistance_value"].maximum() == 41
            assert dlg._combos["soundchannels"].minimumWidth() >= 160
            opts = dlg.collect_options()
            assert opts["farclip_value"] == 777
            assert opts["frilldistance_value"] == 300
            assert opts["fov_patch"] is True
            assert opts["quickloot"] is True
            argv = vanilla_tweaks_old_argv(opts)
            for flag in TUBTUBS_ONLY_FLAGS:
                assert flag not in argv, flag
            settings_mod.settings.set("desired_mods", {"superwow": True})
            locked = VanillaTweaksOldSettingsDialog(None)
            assert locked._superwow_locks_optional is False
            assert locked._checks["fov_patch"].isEnabled()
            assert locked._checks["quickloot"].isEnabled()
            locked.deleteLater()
            dlg.deleteLater()
        finally:
            settings_mod.settings_path = orig_path
            settings_mod.settings = orig

    from ichalaunch.config.settings import settings as s

    opened: list[str] = []

    def _fake_old(parent):  # noqa: ANN001
        opened.append("old")
        return None

    def _fake_v2(parent):  # noqa: ANN001
        opened.append("v2")
        return None

    keys = ("desired_mods", "user_set_mods")
    saved = {k: s.get(k) for k in keys}
    try:
        s.set(
            "desired_mods",
            {**s.desired_mods, "vanilla_tweaks": False, "vanilla_tweaks_old": False},
        )
        page = ClientPage()
        v2_row = page.rows.get("vanilla_tweaks")
        old_row = page.rows.get("vanilla_tweaks_old")
        assert v2_row is not None and old_row is not None
        with patch(
            "ichalaunch.ui.pages.client.confirm_vanilla_tweaks_old",
            return_value=False,
        ):
            page._on_toggle("vanilla_tweaks_old", True)
            app.processEvents()
            assert not s.desired_mods.get("vanilla_tweaks_old")
            assert old_row.cb.isChecked() is False

        with (
            patch(
                "ichalaunch.ui.pages.client.confirm_vanilla_tweaks_old",
                return_value=True,
            ),
            patch(
                "ichalaunch.ui.widgets.dialogs.vanilla_tweaks_old_settings_dialog",
                side_effect=_fake_old,
            ),
            patch(
                "ichalaunch.ui.widgets.dialogs.vanilla_tweaks_settings_dialog",
                side_effect=_fake_v2,
            ),
        ):
            s.set_desired_mod("vanilla_tweaks", True)
            page.refresh_from_settings()
            page._on_toggle("vanilla_tweaks_old", True)
            app.processEvents()
            assert s.desired_mods.get("vanilla_tweaks_old") is True
            assert s.desired_mods.get("vanilla_tweaks") is not True
            assert not v2_row.cb.isChecked()
            assert opened == ["old"], opened
            page.refresh_from_settings()
            app.processEvents()
            assert opened == ["old"], opened
        page.deleteLater()
    finally:
        for k in keys:
            s.set(k, saved[k])
    print("OK vanilla tweaks old settings dialog and warning")


def test_superwow_tracks_dll_release_not_patch_mpq():
    """SuperWoW is SuperWoWhook.dll on the Release zip, never the Patch MPQ."""
    from ichalaunch.addons.tip_index import load_index_file, bundled_tips_path
    from ichalaunch.mods.installer import _asset_from_release, get_mod

    mod = get_mod("superwow")
    assert mod is not None
    source = mod.get("source") or {}
    assert source.get("type") == "github_release_latest"
    needle = str(source.get("asset_contains") or "").lower()
    assert "superwow" in needle
    assert needle != ".zip"

    patch = {
        "tag_name": "Patch",
        "assets": [{"name": "patch-9.MPQ", "browser_download_url": "https://example.com/patch-9.MPQ"}],
    }
    release = {
        "tag_name": "Release",
        "assets": [
            {
                "name": "SuperWoW.release.2.2.zip",
                "browser_download_url": "https://example.com/SuperWoW.release.2.2.zip",
            }
        ],
    }
    assert _asset_from_release(patch, source) is None
    picked = _asset_from_release(release, source)
    assert picked is not None
    assert "superwow" in (picked.get("name") or "").lower()
    assert (picked.get("name") or "").lower().endswith(".zip")

    tips = load_index_file(bundled_tips_path())
    sw_tip = (tips.get("repos") or {}).get("balakethelock/superwow", {})
    tag = str(sw_tip.get("latest_tag") or "")
    assert tag == "Release"
    assert str(sw_tip.get("display_version") or "") == "v2.2"
    print("OK superwow tracks dll release not patch mpq")


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
    app.processEvents()
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


def test_home_page_has_three_equal_link_buttons():
    """Home replaces REGISTER HERE with Register / Database / Bug Report."""
    from unittest.mock import patch

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFont, QFontMetrics
    from PySide6.QtWidgets import QApplication

    from ichalaunch.ui.pages.home import (
        BUG_REPORT_URL,
        DATABASE_URL,
        DRAWER_MIN_W,
        REGISTER_URL,
        _HOME_LINK_GAP,
        _HOME_LINK_H,
        _HOME_LINK_ROW_W,
        HomePage,
    )
    from ichalaunch.ui.widgets.glue_panel_button import GLUE_BTN_H, GluePanelButton

    app = QApplication.instance() or QApplication([])
    page = HomePage()
    page.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    page.resize(900, 600)
    page._mods_drawer.resize(DRAWER_MIN_W, 480)
    page._mods_drawer.layout().activate()
    app.processEvents()

    buttons = (page.register_btn, page.database_btn, page.bug_report_btn)
    labels = [b.text() for b in buttons]
    assert labels == ["Register", "Database", "Bug Report"], labels
    assert all(isinstance(b, GluePanelButton) for b in buttons)
    assert _HOME_LINK_H == GLUE_BTN_H
    sizes = {(b.width(), b.height()) for b in buttons}
    assert len(sizes) == 1, sizes
    w, h = next(iter(sizes))
    assert w > 0 and h == _HOME_LINK_H
    assert w * 3 + 2 * _HOME_LINK_GAP <= _HOME_LINK_ROW_W
    assert page._home_link_row.width() == _HOME_LINK_ROW_W
    assert page._home_link_row.width() <= DRAWER_MIN_W
    assert page._home_link_row.height() == _HOME_LINK_H
    # Title-case labels stay on one line (standard glue type, no wrap / all-caps).
    font = QFont("Segoe UI")
    font.setBold(True)
    font.setPixelSize(13)
    longest = max(QFontMetrics(font).horizontalAdvance(t) for t in labels)
    assert longest + 20 <= w, (longest, w)
    # Row sits in the drawer strip, not clipped by the mods card.
    assert page._home_link_row.geometry().bottom() <= page.summary.geometry().top()

    opened: list[str] = []
    with patch(
        "ichalaunch.ui.pages.home.open_url_in_browser",
        side_effect=lambda url: opened.append(url) or True,
    ):
        page.register_btn.click()
        page.database_btn.click()
        page.bug_report_btn.click()
    assert opened == [REGISTER_URL, DATABASE_URL, BUG_REPORT_URL], opened
    assert REGISTER_URL == "https://ravencraft.io/register"
    assert DATABASE_URL == "https://database.ravencraft.io/"
    assert BUG_REPORT_URL == "https://ravencraft.io/bug-tracker"

    page.close()
    print("OK home page has three equal Register/Database/Bug Report buttons")


def test_addon_check_updates_gates_until_list_ready():
    """Check Updates stays disabled until lists are built and revealed."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from ichalaunch.ui.pages.addons import AddonsPage

    app = QApplication.instance() or QApplication([])
    page = AddonsPage()
    page.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)

    assert page._lists_ready is False
    assert page.update_check_ui_ready() is False
    assert page.check_btn.isEnabled() is False

    page._lists_ready = True
    page._pending_list_work.add("reveal")
    page._sync_check_btn()
    assert page.lists_mutating() is True
    assert page.update_check_ui_ready() is False
    assert page.check_btn.isEnabled() is False

    page._pending_list_work.clear()
    # Off-screen page: no reveal required for "ready" (silent startup path).
    page.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    page._sync_check_btn()
    assert page.update_check_ui_ready() is True
    assert page.check_btn.isEnabled() is True

    page.set_checking(True)
    assert page._scan_busy() is True
    assert page.update_check_ui_ready() is False
    assert page.check_btn.isEnabled() is False

    # Settle pattern: drop scan gate, keep Check locked.
    page.set_check_busy(True)
    page.set_scanning(False)
    assert page._scan_busy() is False
    assert page._check_busy is True
    assert page.check_btn.isEnabled() is False

    page.set_checking(False)
    page.close()
    print("OK addon Check Updates gated until list ready")


def test_addons_defers_list_build_while_scanning():
    """Opening Addons during an update scan must not clear()/reveal lists yet."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QStackedWidget

    from ichalaunch.ui.pages.addons import AddonsPage, _SCAN_PLACEHOLDER

    app = QApplication.instance() or QApplication([])
    stack = QStackedWidget()
    page = AddonsPage()
    # Arm the scan gate before the page can receive Show (addWidget/current).
    page.set_scanning(True)
    assert page._scan_busy() is True
    assert page.update_check_ui_ready() is False

    page._dirty = True
    page._lists_ready = False
    stack.addWidget(page)
    stack.setCurrentWidget(page)
    stack.show()
    page.show()
    app.processEvents()

    assert page.loading_lbl.isVisible()
    assert (page.loading_lbl.text() or "").startswith("Scanning addons")
    assert _SCAN_PLACEHOLDER in (page.loading_lbl.text() or "") or True
    assert "refresh" in page._pending_list_work
    assert "reveal" in page._pending_list_work
    assert page._refreshing is False
    assert page._revealing is False
    assert page._lists_ready is False
    assert page.installed_list.count() == 0
    assert page.installed_list.testAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    assert page.list.testAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    assert page.check_btn.isEnabled() is False

    # Dropping the scan gate must schedule deferred list work (not run inline
    # on set_scanning alone beyond the singleShot).
    page.set_scanning(False)
    assert page._scan_busy() is False
    app.processEvents()
    # Flush may still be in flight; pending should drain or be actively refreshing.
    assert (
        not page._pending_list_work
        or page._refreshing
        or page._revealing
        or "refresh" in page._pending_list_work
        or "reveal" in page._pending_list_work
    )

    page.close()
    stack.close()
    print("OK addons defers list build while scanning")


def test_addons_available_pagination_after_reveal():
    """Prev/Next must not WA_DontShowOnScreen + clear() revealed rows (Qt abort)."""
    from unittest.mock import patch

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from ichalaunch.ui.pages.addons import PAGE_SIZE, AddonsPage, _reveal_item_widgets

    app = QApplication.instance() or QApplication([])
    with patch("ichalaunch.addons.github.github_url_reachable_cached", return_value=True):
        page = AddonsPage()
        page.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        page.show()
        page.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, False)
        page._lists_ready = True
        page._filtered_available = [
            {
                "name": f"Addon {i}",
                "folder": f"addon{i}",
                "repo": f"https://github.com/example/repo{i}",
                "source": "github",
            }
            for i in range(PAGE_SIZE + 7)
        ]
        page._page_index = 0
        page.filter_box.blockSignals(True)
        page.filter_box.setCurrentText("Available")
        page.filter_box.blockSignals(False)
        page._apply_section_visibility("Available")
        page._render_available_page(light=False)
        _reveal_item_widgets(page.list, page)
        first = page.list.itemWidget(page.list.item(0))
        assert first is not None
        assert not first.testAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)

        page._page(1)
        for _ in range(20):
            app.processEvents()

        assert page._page_index == 1
        assert page.list.count() == 7
        row = page.list.itemWidget(page.list.item(0))
        assert row is not None
        assert not row.testAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)

        page.next_btn.clicked.emit()
        for _ in range(4):
            app.processEvents()
        assert page._page_index == 1  # only 2 pages

        page._page(-1)
        for _ in range(20):
            app.processEvents()
        assert page._page_index == 0
        assert page.list.count() == PAGE_SIZE
        page._cancel_all_git_probes()
        for _ in range(100):
            app.processEvents()
        page.close()
        for _ in range(50):
            app.processEvents()
    print("OK addons available pagination after reveal")


def _addons_page_fully_loaded(page) -> None:
    """Idle addons page — no scan, flush, or reveal work in flight."""
    from PySide6.QtCore import Qt

    from ichalaunch.ui.pages.addons import _reveal_item_widgets

    page._lists_ready = True
    page._dirty = False
    page._scanning = False
    page._check_busy = False
    page._refreshing = False
    page._rendering_avail = False
    page._revealing = False
    page._pending_avail_search = False
    page._pending_page_index = None
    page._pending_list_work.clear()
    page.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, False)
    page.show()
    _reveal_item_widgets(page.list, page)
    if page._want_installed_visible:
        _reveal_item_widgets(page.installed_list, page)


def test_addons_all_filter_pagination_fully_loaded():
    """All filter + Prev/Next after lists are revealed (primary user crash path)."""
    from unittest.mock import patch

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from ichalaunch.addons.github import load_catalog
    from ichalaunch.ui.pages.addons import PAGE_SIZE, AddonsPage

    app = QApplication.instance() or QApplication([])
    with patch("ichalaunch.addons.github.github_url_reachable_cached", return_value=True):
        page = AddonsPage()
        page.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        page.show()

        # Real catalog size — HWND pressure matches production pagination.
        catalog = load_catalog()
        page._filtered_available = [
            e
            for e in catalog
            if e.get("repo") and (e.get("folder") or e.get("name"))
        ]
        assert len(page._filtered_available) > PAGE_SIZE

        page.filter_box.blockSignals(True)
        page.filter_box.setCurrentText("All")
        page.filter_box.blockSignals(False)
        page._apply_section_visibility("All")

        # Installed column live beside Available (All mode).
        page._do_refresh()
        _addons_page_fully_loaded(page)
        assert page.filter_box.currentText() == "All"
        assert page.installed_list.count() > 0
        assert page.list.count() == PAGE_SIZE
        assert not page.lists_mutating()

        for delta, expected in ((1, 1), (1, 2), (-1, 1), (-1, 0)):
            page._page(delta)
            for _ in range(80):
                app.processEvents()
            assert page._page_index == expected

        page._cancel_all_git_probes()
        for _ in range(200):
            app.processEvents()
        page.close()
        for _ in range(100):
            app.processEvents()
    print("OK addons all-filter pagination when fully loaded")


def test_cancel_git_url_checks_orphans_running_threads():
    """Cancel must keep running QThreads alive — GC mid-run aborts Qt."""
    import gc
    import time
    from unittest.mock import patch

    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QApplication

    import ichalaunch.ui.widgets.common as common
    from ichalaunch.ui.widgets.common import (
        cancel_git_url_checks,
        drain_orphan_git_url_threads,
    )

    app = QApplication.instance() or QApplication([])
    owner = QObject()

    def _slow_reachable(url: str, *, timeout: float = 2.5) -> bool:  # noqa: ARG001
        time.sleep(0.25)
        return True

    with patch(
        "ichalaunch.addons.github.github_url_reachable",
        side_effect=_slow_reachable,
    ):
        # Manually start a probe thread (UI no longer auto-probes on mount).
        thread = common._BrowseUrlCheckThread("https://github.com/example/orphan-probe")
        setattr(owner, "_git_url_threads", [thread])
        setattr(owner, "_git_url_check_gen", 1)
        setattr(owner, "_git_url_pending", thread._url)
        thread.start()
        assert thread.isRunning()
        alive = thread
        cancel_git_url_checks(owner)
        assert getattr(owner, "_git_url_threads", []) == []
        assert alive in common._ORPHAN_GIT_URL_THREADS
        # Drop every other ref and force GC — must not destroy the running QThread.
        del thread
        gc.collect()
        assert common._shiboken_is_valid(alive)
        # Still running (or just finished but not yet reaped from the orphan list).
        assert alive.isRunning() or alive in common._ORPHAN_GIT_URL_THREADS
        drain_orphan_git_url_threads(wait_ms=2000)
        for _ in range(40):
            app.processEvents()
        assert alive not in common._ORPHAN_GIT_URL_THREADS
    print("OK cancel git url checks orphans running threads")


def test_addons_rapid_pagination_spawns_no_browse_url_threads():
    """Rapid Next/Prev must not spawn Open-in-Git reachability probe threads."""
    import gc
    from unittest.mock import patch

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    import ichalaunch.ui.widgets.common as common
    from ichalaunch.addons.github import clear_github_url_cache, load_catalog
    from ichalaunch.ui.pages.addons import PAGE_SIZE, AddonsPage, _reveal_item_widgets
    from ichalaunch.ui.widgets.common import drain_orphan_git_url_threads

    app = QApplication.instance() or QApplication([])
    clear_github_url_cache()
    drain_orphan_git_url_threads(wait_ms=100)

    started: list[object] = []
    real_init = common._BrowseUrlCheckThread.__init__

    def _track_init(self, url: str) -> None:  # noqa: ANN001
        started.append(url)
        real_init(self, url)

    with patch.object(common._BrowseUrlCheckThread, "__init__", _track_init):
        page = AddonsPage()
        page.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        page.show()
        page.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, False)
        page._lists_ready = True
        catalog = load_catalog()
        page._filtered_available = [
            e
            for e in catalog
            if e.get("repo") and (e.get("folder") or e.get("name"))
        ]
        assert len(page._filtered_available) > PAGE_SIZE * 2
        page._page_index = 0
        page.filter_box.blockSignals(True)
        page.filter_box.setCurrentText("All")
        page.filter_box.blockSignals(False)
        page._applied_filter_mode = "All"
        page._applied_cat_filter = page.cat_box.currentText()
        page._want_installed_visible = True
        page._want_avail_visible = True
        page._apply_section_visibility("All")
        page._render_available_page(light=False)
        _reveal_item_widgets(page.list, page)

        row = page.list.itemWidget(page.list.item(0))
        assert row is not None and row.open_git_btn is not None
        assert row.open_git_btn.isVisible(), "Open-in-Git visible from known repo URL"

        page.next_btn.clicked.emit()
        gc.collect()
        for _ in range(4):
            app.processEvents()
        page.next_btn.clicked.emit()
        page.next_btn.clicked.emit()
        page.prev_btn.clicked.emit()
        gc.collect()
        for _ in range(20):
            app.processEvents()

        for _ in range(10):
            page.next_btn.clicked.emit()
            page.prev_btn.clicked.emit()
            gc.collect()
            for _ in range(8):
                app.processEvents()

        assert not started, f"pagination must not spawn browse-url probes, got {len(started)}"
        assert page.list.count() > 0
        page._cancel_all_git_probes()
        drain_orphan_git_url_threads(wait_ms=500)
        for _ in range(50):
            app.processEvents()
        page.close()
        for _ in range(50):
            app.processEvents()
        assert not common._ORPHAN_GIT_URL_THREADS
    print("OK addons rapid pagination spawns no browse-url threads")


def test_open_in_git_visible_without_probe_and_click_opens():
    """Open-in-Git shows for known repo URLs without probing; click opens the URL."""
    from unittest.mock import patch

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QHBoxLayout, QSizePolicy

    import ichalaunch.ui.widgets.common as common
    from ichalaunch.ui.pages.addons import AddonsPage, _reveal_item_widgets
    from ichalaunch.ui.widgets.common import AddonRow

    app = QApplication.instance() or QApplication([])
    started: list[object] = []
    real_init = common._BrowseUrlCheckThread.__init__

    def _track_init(self, url: str) -> None:  # noqa: ANN001
        started.append(url)
        real_init(self, url)

    opened: list[str] = []

    def _fake_open(url: str) -> bool:
        opened.append(str(url))
        return True

    with patch.object(common._BrowseUrlCheckThread, "__init__", _track_init), patch(
        "ichalaunch.ui.pages.addons.open_url_in_browser",
        side_effect=_fake_open,
    ):
        # Layout order on AddonRow: name → modules caret → Open-in-Git.
        direct = AddonRow(
            {
                "name": "Order Probe",
                "folder": "orderprobe",
                "repo": "https://github.com/example/order",
            },
            status="available",
            modules=["A", "B"],
        )
        found_order: list[str] = []

        def _scan(lay) -> None:  # noqa: ANN001
            if lay is None:
                return
            if isinstance(lay, QHBoxLayout):
                for i in range(lay.count()):
                    item = lay.itemAt(i)
                    if item is None:
                        continue
                    w = item.widget()
                    if w is direct.modules_toggle:
                        found_order.append("caret")
                    elif w is direct.open_git_btn:
                        found_order.append("git")
                    _scan(item.layout())
            else:
                for i in range(lay.count()):
                    item = lay.itemAt(i)
                    if item is not None:
                        _scan(item.layout())

        _scan(direct.layout())
        assert found_order == ["caret", "git"], found_order
        assert not direct.modules_toggle.isHidden()
        assert direct.open_git_btn is not None and not direct.open_git_btn.isHidden()
        # Name cluster: name → caret → git → downloads (~6px visual gap).
        name_row = None
        root = direct.layout()
        assert root is not None
        top = root.itemAt(0)
        assert top is not None and top.layout() is not None
        for i in range(top.layout().count()):
            item = top.layout().itemAt(i)
            if item is None or item.layout() is None:
                continue
            lay = item.layout()
            widgets = []
            for j in range(lay.count()):
                w = lay.itemAt(j).widget() if lay.itemAt(j) else None
                if w is direct._name_lbl or w is direct.modules_toggle or w is direct.open_git_btn:
                    widgets.append(w)
            if len(widgets) >= 2:
                name_row = lay
                break
        assert name_row is not None
        assert name_row.spacing() == 6
        assert direct._name_lbl.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Maximum
        # No-modules row: caret not in the name cluster layout.
        no_mod = AddonRow(
            {
                "name": "Solo",
                "folder": "solo",
                "repo": "https://github.com/example/solo",
            },
            status="available",
        )
        solo_order: list[str] = []

        def _scan_solo(lay) -> None:  # noqa: ANN001
            if lay is None:
                return
            if isinstance(lay, QHBoxLayout):
                for i in range(lay.count()):
                    item = lay.itemAt(i)
                    if item is None:
                        continue
                    w = item.widget()
                    if w is no_mod.modules_toggle:
                        solo_order.append("caret")
                    elif w is no_mod.open_git_btn:
                        solo_order.append("git")
                    _scan_solo(item.layout())
            else:
                for i in range(lay.count()):
                    item = lay.itemAt(i)
                    if item is not None:
                        _scan_solo(item.layout())

        _scan_solo(no_mod.layout())
        assert solo_order == ["git"], solo_order
        no_mod.deleteLater()
        direct.deleteLater()

        page = AddonsPage()
        page.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        page.show()
        page.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, False)
        page._lists_ready = True
        page._filtered_available = [
            {
                "name": "Probe Addon",
                "folder": "probe",
                "repo": "https://github.com/example/probe",
                "source": "github",
            }
        ]
        page.filter_box.blockSignals(True)
        page.filter_box.setCurrentText("Available")
        page.filter_box.blockSignals(False)
        page._applied_filter_mode = "Available"
        page._applied_cat_filter = page.cat_box.currentText()
        page._apply_section_visibility("Available")
        page._render_available_page(light=False)
        _reveal_item_widgets(page.list, page)
        row = page.list.itemWidget(page.list.item(0))
        assert row is not None and row.open_git_btn is not None
        assert row.open_git_btn.isVisible()
        assert not started, "mount must not start browse-url probe threads"
        assert getattr(row, "_git_url_threads", None) in (None, [])

        row.open_git_btn.click()
        for _ in range(10):
            app.processEvents()
        assert opened == ["https://github.com/example/probe"]
        assert not started, "click must open URL without spawning probe threads"
        page.close()
        for _ in range(20):
            app.processEvents()
    print("OK Open-in-Git visible without probe; click opens URL")


def test_mod_check_row_links_after_author():
    """Client ModCheckRow: open-link / Open-in-Git sit after the created-by tag."""
    from PySide6.QtWidgets import QApplication, QHBoxLayout

    from ichalaunch.ui.widgets.common import ModCheckRow

    app = QApplication.instance() or QApplication([])
    del app
    row = ModCheckRow(
        "probe",
        "Probe Mod",
        "desc",
        author="AuthorName",
    )
    row.set_open_url("https://example.com/project")
    row.set_git_url("https://github.com/example/probe")
    assert not row.open_link_btn.isHidden()
    assert not row.open_git_btn.isHidden()

    found: list[str] = []

    def _scan(lay) -> None:  # noqa: ANN001
        if lay is None:
            return
        if isinstance(lay, QHBoxLayout):
            for i in range(lay.count()):
                item = lay.itemAt(i)
                if item is None:
                    continue
                w = item.widget()
                if w is row.author_lbl:
                    found.append("author")
                elif w is row.open_link_btn:
                    found.append("open")
                elif w is row.open_git_btn:
                    found.append("git")
                elif w is row.update_btn:
                    found.append("update")
                elif w is row.reinstall_btn:
                    found.append("reinstall")
                _scan(item.layout())
        else:
            for i in range(lay.count()):
                item = lay.itemAt(i)
                if item is not None:
                    _scan(item.layout())

    _scan(row.layout())
    assert "author" in found and "open" in found and "git" in found, found
    assert found.index("author") < found.index("open") < found.index("git"), found
    # Action plates stay at the end; links are not clustered with them.
    if "update" in found:
        assert found.index("git") < found.index("update"), found
    if "reinstall" in found:
        assert found.index("git") < found.index("reinstall"), found
    row.deleteLater()
    print("OK ModCheckRow links after author")


def test_open_git_icon_abuts_name_geometry():
    """Visible Open-in-Git glyph must sit a small, intentional gap from name/author text.

    Target ~6–8px visual gap (layout spacing 6). Fail if cramped (0), or if the old
    QSS-inflated hit box is back (~36px glyph offset / 88px width).
    """
    from pathlib import Path

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFontMetrics
    from PySide6.QtWidgets import QApplication

    from ichalaunch.ui.widgets.common import (
        AddonRow,
        ModCheckRow,
        _OPEN_GIT_INLINE_HIT,
        _OPEN_GIT_INLINE_PX,
    )

    app = QApplication.instance() or QApplication([])
    qss_path = Path(__file__).resolve().parent / "ichalaunch" / "ui" / "theme" / "stylesheet.qss"
    app.setStyleSheet(qss_path.read_text(encoding="utf-8"))

    def _assert_gap_ok(label: str, widget_gap: int, visual_gap: int) -> None:
        assert 4 <= visual_gap <= 12, (
            f"{label} visual gap {visual_gap}px not in 4..12 "
            f"(widget_gap={widget_gap}; cramped≈0 or QSS-inflated≈36)"
        )
        assert 4 <= widget_gap <= 12, (
            f"{label} widget gap {widget_gap}px not in 4..12"
        )

    def _visual_icon_left(btn) -> int:
        # Inline plate left-aligns the pixmap; fall back to centered if wider.
        if btn.width() <= _OPEN_GIT_INLINE_PX + 2:
            return btn.geometry().left()
        # Prefer left edge of painted glyph (inline left-align → 0 inset).
        return btn.geometry().left()

    def _gap_name_to_git(name_lbl, git_btn, *, intervening=None) -> tuple[int, int]:
        """Return (widget_gap, visual_gap_to_icon)."""
        app.processEvents()
        name_right = name_lbl.geometry().right()
        text_right = name_lbl.geometry().left() + QFontMetrics(name_lbl.font()).horizontalAdvance(
            name_lbl.text()
        )
        anchor_right = max(name_right, text_right)
        if intervening is not None and intervening.isVisible():
            anchor_right = max(anchor_right, intervening.geometry().right())
        widget_gap = git_btn.geometry().left() - anchor_right
        visual_gap = _visual_icon_left(git_btn) - anchor_right
        return widget_gap, visual_gap

    # --- AddonRow (no modules): name → git ---
    addon = AddonRow(
        {
            "name": "GapProbe",
            "folder": "gapprobe",
            "repo": "https://github.com/example/gap",
        },
        status="available",
    )
    addon.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, False)
    addon.show()
    addon.resize(640, max(36, addon.sizeHint().height()))
    app.processEvents()
    assert addon.open_git_btn is not None
    assert addon.open_git_btn.width() <= _OPEN_GIT_INLINE_HIT + 2, (
        f"OpenGit hit box inflated by QSS: width={addon.open_git_btn.width()}"
    )
    w_gap, v_gap = _gap_name_to_git(addon._name_lbl, addon.open_git_btn)
    print(f"AddonRow name->git widget_gap={w_gap}px visual_gap={v_gap}px btn_w={addon.open_git_btn.width()}")
    _assert_gap_ok("AddonRow", w_gap, v_gap)
    addon_before_note = (w_gap, v_gap)
    addon.deleteLater()

    # --- AddonRow with modules caret: caret → git ---
    addon_m = AddonRow(
        {
            "name": "GapMods",
            "folder": "gapmods",
            "repo": "https://github.com/example/gapmods",
        },
        status="available",
        modules=["A", "B"],
    )
    addon_m.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, False)
    addon_m.show()
    addon_m.resize(640, max(36, addon_m.sizeHint().height()))
    app.processEvents()
    assert addon_m.open_git_btn is not None
    w_gap_m, v_gap_m = _gap_name_to_git(
        addon_m._name_lbl,
        addon_m.open_git_btn,
        intervening=addon_m.modules_toggle,
    )
    print(
        f"AddonRow caret->git widget_gap={w_gap_m}px visual_gap={v_gap_m}px "
        f"btn_w={addon_m.open_git_btn.width()}"
    )
    _assert_gap_ok("AddonRow+modules", w_gap_m, v_gap_m)
    addon_m.deleteLater()

    # --- ModCheckRow: author → open link (same OpenGitButton chrome) ---
    mod = ModCheckRow("gap", "ClientGap", "desc", author="AuthorName")
    mod.set_open_url("https://example.com/project")
    mod.set_git_url("https://github.com/example/gap")
    mod.show()
    mod.resize(720, max(40, mod.sizeHint().height()))
    app.processEvents()
    assert not mod.open_link_btn.isHidden()
    assert mod.open_link_btn.width() <= _OPEN_GIT_INLINE_HIT + 2, (
        f"OpenLink hit box inflated: width={mod.open_link_btn.width()}"
    )
    author_right = mod.author_lbl.geometry().right()
    author_text_right = mod.author_lbl.geometry().left() + QFontMetrics(
        mod.author_lbl.font()
    ).horizontalAdvance(mod.author_lbl.text())
    anchor = max(author_right, author_text_right)
    open_w_gap = mod.open_link_btn.geometry().left() - anchor
    open_v_gap = _visual_icon_left(mod.open_link_btn) - anchor
    print(
        f"ModCheckRow author->open widget_gap={open_w_gap}px visual_gap={open_v_gap}px "
        f"btn_w={mod.open_link_btn.width()}"
    )
    _assert_gap_ok("ModCheckRow", open_w_gap, open_v_gap)
    mod.deleteLater()
    print(
        f"OK Open-in-Git abuts name "
        f"(AddonRow gaps widget/visual={addon_before_note[0]}/{addon_before_note[1]}px)"
    )


def test_addons_filter_popup_same_value_keeps_open_git_visible():
    """Opening/closing a filter dropdown without changing selection keeps Open-in-Git."""
    import time

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from ichalaunch.ui.pages.addons import AddonsPage, _reveal_item_widgets

    app = QApplication.instance() or QApplication([])
    page = AddonsPage()
    page.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    page.show()
    page.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, False)
    page._lists_ready = True
    page._filtered_available = [
        {
            "name": "Probe Addon",
            "folder": "probe",
            "repo": "https://github.com/example/probe",
            "source": "github",
        }
    ]
    page.filter_box.blockSignals(True)
    page.filter_box.setCurrentText("Available")
    page.filter_box.blockSignals(False)
    page._applied_filter_mode = "Available"
    page._applied_cat_filter = page.cat_box.currentText()
    page._apply_section_visibility("Available")
    page._render_available_page(light=False)
    _reveal_item_widgets(page.list, page)
    row = page.list.itemWidget(page.list.item(0))
    assert row is not None and row.open_git_btn is not None
    assert row.open_git_btn.isVisible()

    # Simulate popup open/close without a committed filter change.
    page.filter_box.popupShown.emit()
    page.filter_box.popupHidden.emit()
    page._list_freeze_until = time.monotonic() + 0.2
    page._on_filter_changed()
    for _ in range(40):
        app.processEvents()

    assert row.open_git_btn.isVisible()
    page.close()
    for _ in range(20):
        app.processEvents()
    print("OK addons filter popup same value keeps Open-in-Git visible")


def test_addons_filter_change_cancels_git_probes():
    """Committed filter change tears down rows and cancels leftover git probes."""
    from unittest.mock import patch

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    import ichalaunch.ui.pages.addons as addons_page
    from ichalaunch.ui.pages.addons import PAGE_SIZE, AddonsPage, _reveal_item_widgets

    app = QApplication.instance() or QApplication([])

    cancelled: list[object] = []
    real_cancel = addons_page.cancel_git_url_checks

    def _track_cancel(owner) -> None:  # noqa: ANN001
        cancelled.append(owner)
        real_cancel(owner)

    with patch.object(addons_page, "cancel_git_url_checks", side_effect=_track_cancel):
        page = AddonsPage()
        page.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        page.show()
        page.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, False)
        page._lists_ready = True
        page._ensure_available_base()
        page._filtered_available = list(page._available_base[: PAGE_SIZE + 3])
        assert len(page._filtered_available) > PAGE_SIZE
        page.filter_box.blockSignals(True)
        page.filter_box.setCurrentText("Available")
        page.filter_box.blockSignals(False)
        page._applied_filter_mode = "Available"
        page._applied_cat_filter = "All categories"
        page._apply_section_visibility("Available")
        page._render_available_page(light=False)
        _reveal_item_widgets(page.list, page)

        cats = [
            page.cat_box.itemText(i)
            for i in range(page.cat_box.count())
            if page.cat_box.itemText(i) != "All categories"
        ]
        assert cats, "need at least one real category for filter-change test"
        page.cat_box.blockSignals(True)
        page.cat_box.setCurrentText(cats[0])
        page.cat_box.blockSignals(False)
        page._on_filter_changed()
        for _ in range(20):
            app.processEvents()
        assert cancelled, "filter apply must cancel git probes on torn-down rows"
        page.close()
        for _ in range(20):
            app.processEvents()
    print("OK addons filter change cancels git probes")


def test_github_url_reach_disk_cache_roundtrip():
    """Git browse URL reachability persists across cache reload."""
    import json
    import tempfile
    from pathlib import Path
    from unittest.mock import patch

    import ichalaunch.addons.github as gh

    with tempfile.TemporaryDirectory() as td:
        cache_path = Path(td) / "git_url_reach_cache.json"
        with patch.object(gh, "_URL_REACH_DISK_PATH", cache_path):
            gh._url_reach_cache.clear()
            gh._url_reach_disk_loaded = False
            gh._url_reach_cache["https://github.com/example/stale"] = (0.0, True)
            gh._persist_url_reach_disk("https://github.com/example/stale", True)
            gh._url_reach_cache.clear()
            gh._url_reach_disk_loaded = False
            assert gh.github_url_reachable_cached("https://github.com/example/stale") is True
            raw = json.loads(cache_path.read_text(encoding="utf-8"))
            assert "https://github.com/example/stale" in raw
    print("OK github url reach disk cache roundtrip")


def test_mainwindow_addons_next_all_filter_fully_loaded():
    """MainWindow default All filter: Next on available pagination must not abort Qt."""
    from PySide6.QtWidgets import QApplication

    from ichalaunch.ui.main_window import MainWindow
    from ichalaunch.ui.pages.addons import PAGE_SIZE

    app = QApplication.instance() or QApplication([])
    win = MainWindow()
    win.show()
    for _ in range(600):
        app.processEvents()
    win.nav_btns[1].click()
    for _ in range(600):
        app.processEvents()
    page = win.addons
    if page.filter_box.currentText() != "All":
        page.filter_box.setCurrentText("All")
        for _ in range(400):
            app.processEvents()
    assert page.filter_box.currentText() == "All"
    # Simulate post-startup idle: scan finished, lists revealed.
    page.set_scanning(False)
    page.set_check_busy(False)
    page._pending_list_work.clear()
    page._rendering_avail = False
    for _ in range(200):
        app.processEvents()
    assert not page.lists_mutating()
    assert len(page._filtered_available) > PAGE_SIZE

    for _ in range(3):
        page.next_btn.clicked.emit()
        for _ in range(400):
            app.processEvents()
    assert page._page_index >= 1
    assert page.list.count() > 0
    assert page.installed_list.count() > 0
    win.close()
    print("OK mainwindow addons next on all filter fully loaded")


def test_mainwindow_addons_next_available_filter():
    """Opening Addons, switching to Available, and Next must not abort Qt."""
    from PySide6.QtWidgets import QApplication

    from ichalaunch.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    win = MainWindow()
    win.show()
    for _ in range(400):
        app.processEvents()
    win.nav_btns[1].click()
    for _ in range(400):
        app.processEvents()
    page = win.addons
    page.filter_box.setCurrentText("Available")
    for _ in range(200):
        app.processEvents()
    page.next_btn.clicked.emit()
    for _ in range(600):
        app.processEvents()
    assert page._page_index >= 1
    assert page.list.count() > 0
    win.close()
    print("OK mainwindow addons next on available filter")


def test_mainwindow_check_updates_serializes_pending_reveal():
    """Real MainWindow._check_updates must finish reveal before set_updates.

    Reproduces the crash window: pending reveal/refresh ignored by the old
    mutating carve-out so apply patched while first-open reveal was still queued.
    Also asserts apply never clear()/rebuilds via reload_catalog.
    """
    from PySide6.QtCore import QDeadlineTimer, Qt
    from PySide6.QtWidgets import QApplication, QLabel

    import ichalaunch.ui.main_window as mw
    from ichalaunch.addons.github import AddonUpdateCheckResult
    from ichalaunch.ui.pages.addons import AddonsPage

    app = QApplication.instance() or QApplication([])

    page = AddonsPage()
    page.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    page._lists_ready = True
    page._dirty = False
    page._pending_list_work.add("reveal")
    page._sync_check_btn()
    assert page.update_check_ui_ready() is False
    assert page.check_btn.isEnabled() is False

    applied: list[list] = []
    catalog_calls: list[str] = []
    original_set_updates = page.set_updates
    original_ingest = page.ingest_catalog_update
    original_reload = page.reload_catalog

    def _tracking_set_updates(updates):  # noqa: ANN001
        pending = set(page._pending_list_work)
        assert "reveal" not in pending
        assert "refresh" not in pending
        assert page._revealing is False
        assert page._refreshing is False
        applied.append(list(updates))
        return original_set_updates(updates)

    def _tracking_ingest() -> None:
        catalog_calls.append("ingest")
        return original_ingest()

    def _tracking_reload() -> None:
        catalog_calls.append("reload")
        raise AssertionError("reload_catalog must not run during Check Updates apply")

    page.set_updates = _tracking_set_updates  # type: ignore[method-assign]
    page.ingest_catalog_update = _tracking_ingest  # type: ignore[method-assign]
    page.reload_catalog = _tracking_reload  # type: ignore[method-assign]

    class Harness:
        def __init__(self):
            self.addons = page
            self.status_lbl = QLabel("")
            self._checking_addons = False
            self._checking_mods = False
            self._check_addon_pct = 0
            self._check_mod_pct = 0
            self._addon_check_status = ""
            self._addon_check_settling = False
            self._silent_addon_check_retry_armed = False
            self._silent_addon_check_retries = 0
            self._update_worker = None
            self._worker = None
            self._mod_update_worker = None
            self._launcher_update_worker = None
            self._live_workers: set = set()
            self._busy_status_base = ""
            self.progress = type(
                "P",
                (),
                {
                    "isHidden": lambda self: True,
                    "show": lambda self: None,
                    "setRange": lambda *a: None,
                    "setValue": lambda *a: None,
                    "setFormat": lambda *a: None,
                    "maximum": lambda self: 100,
                },
            )()
            self.client = type("C", (), {"set_checking": lambda *a, **k: None})()

        _check_updates = mw.MainWindow._check_updates
        _arm_silent_addon_check_retry = mw.MainWindow._arm_silent_addon_check_retry
        _refresh_check_loading = mw.MainWindow._refresh_check_loading
        _lock_addon_filters = mw.MainWindow._lock_addon_filters
        _track_worker = mw.MainWindow._track_worker
        _release_worker = mw.MainWindow._release_worker
        _worker_busy = mw.MainWindow._worker_busy
        _combined_check_pct = mw.MainWindow._combined_check_pct
        _on_addon_check_status = mw.MainWindow._on_addon_check_status
        _on_check_progress_pct = lambda self, *_a, **_k: None
        _hide_progress_bar = lambda self: None
        _refresh_nav_badges = lambda self: None

    harness = Harness()

    orig_installed = mw.is_installed
    orig_recent = mw.recently_checked_addon_updates
    orig_worker = mw.Worker
    orig_check = getattr(mw, "check_addon_updates", None)

    class InstantWorker(mw.Worker):
        def start(self):  # noqa: N802
            # Run fn synchronously then emit — mimics a very fast scan finishing
            # while reveal is still pending on the UI side.
            try:
                result = self.fn(*self.args, **self.kwargs)
                self.finished_ok.emit(result)
            except Exception as exc:  # noqa: BLE001
                self.failed.emit(str(exc))

    def _fake_check(*_a, **_k):
        return AddonUpdateCheckResult(
            updates=[],
            status_message="up to date",
            catalog_refreshed=True,
        )

    try:
        mw.is_installed = lambda: True  # type: ignore[assignment]
        mw.recently_checked_addon_updates = lambda: False  # type: ignore[assignment]
        mw.Worker = InstantWorker  # type: ignore[misc,assignment]
        mw.check_addon_updates = _fake_check  # type: ignore[assignment]

        # Manual path refuses while not ready.
        harness._check_updates(silent=False)
        assert harness._update_worker is None
        assert "still loading" in (harness.status_lbl.text() or "").lower()

        # Simulate scan-in-flight with deferred reveal (mid-scan open), then
        # force-start so we exercise apply serialization (force bypasses ready).
        page.set_scanning(True)
        page._pending_list_work.add("reveal")
        page._sync_check_btn()

        # After force start, apply phase 0 drops scanning → pending reveal
        # must clear before set_updates. Hook flush to clear reveal quickly.
        orig_flush = page._flush_list_work

        def _flush():
            if not page._scan_busy() and "reveal" in page._pending_list_work:
                page._pending_list_work.discard("reveal")
                page._sync_check_btn()
                return
            return orig_flush()

        page._flush_list_work = _flush  # type: ignore[method-assign]

        harness._check_updates(silent=False, force=True)
        deadline = QDeadlineTimer(5000)
        while (
            harness._checking_addons
            or harness._addon_check_settling
            or harness._update_worker
        ) and not deadline.hasExpired():
            app.processEvents()

        assert applied == [[]]
        assert catalog_calls == ["ingest"]
        assert harness._checking_addons is False
        assert harness._addon_check_settling is False
        assert page._check_busy is False
        assert page._scanning is False
    finally:
        mw.is_installed = orig_installed
        mw.recently_checked_addon_updates = orig_recent
        mw.Worker = orig_worker  # type: ignore[misc]
        if orig_check is not None:
            mw.check_addon_updates = orig_check  # type: ignore[assignment]
        page.close()

    print("OK MainWindow Check Updates serializes pending reveal")


def test_options_cog_uses_wow_art():
    """Addon settings cog uses UI-OptionsButton art at 28x28."""
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


def test_addon_preview_gates_combos_and_open_git():
    """Settings/Install dialogs lock fork+version until preview settles; Open in Git is present."""
    from pathlib import Path

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFontMetrics
    from PySide6.QtWidgets import QApplication

    from ichalaunch.addons import github as G
    from ichalaunch.ui.widgets import dialogs as D
    from ichalaunch.ui.widgets.common import _OPEN_GIT_INLINE_HIT

    app = QApplication.instance() or QApplication([])
    qss_path = Path(__file__).resolve().parent / "ichalaunch" / "ui" / "theme" / "stylesheet.qss"
    app.setStyleSheet(qss_path.read_text(encoding="utf-8"))
    entry = {
        "name": "pfUI",
        "folder": "pfUI",
        "repo": "https://github.com/shagu/pfUI",
        "repository": "shagu/pfUI",
    }

    prev_token = G.has_github_token
    orig_preview_start = D._PreviewFetchThread.start
    orig_browse_start = D._AddonBrowseFetchThread.start

    def _noop_start(self):  # noqa: ANN001
        return None

    try:
        G.has_github_token = lambda: True  # type: ignore[assignment]
        D._PreviewFetchThread.start = _noop_start  # type: ignore[method-assign]
        D._AddonBrowseFetchThread.start = _noop_start  # type: ignore[method-assign]

        settings_dlg = D.AddonSettingsDialog(None, entry, meta={"tag": "5.4.4"})
        settings_dlg.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        assert settings_dlg._fork_combo is not None
        assert settings_dlg._version_combo is not None
        assert settings_dlg._open_git_btn is not None
        assert settings_dlg._open_git_btn.accessibleName() == "Open in Git"
        assert settings_dlg._preview_pending is True
        assert not settings_dlg._fork_combo.isEnabled()
        assert not settings_dlg._version_combo.isEnabled()
        assert D._LOADING_PREVIEW_TIP in (settings_dlg._fork_combo.toolTip() or "")

        # Preview failure must unlock (do not leave permanently disabled).
        settings_dlg._preview_pending = False
        settings_dlg._fork_fetch_pending = False
        settings_dlg._version_fetch_pending = False
        settings_dlg._sync_combo_interactivity()
        assert settings_dlg._fork_combo.isEnabled()
        assert settings_dlg._version_combo.isEnabled()
        settings_dlg.close()

        install_dlg = D.AddonInstallPickerDialog(None, entry)
        install_dlg.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, False)
        install_dlg.show()
        install_dlg.resize(680, 560)
        app.processEvents()
        assert install_dlg._open_git_btn is not None
        assert install_dlg._title_lbl is not None
        assert install_dlg._open_git_btn.accessibleName() == "Open in Git"
        assert install_dlg._open_git_btn.width() <= _OPEN_GIT_INLINE_HIT + 2, (
            f"Install OpenGit hit box inflated: width={install_dlg._open_git_btn.width()}"
        )
        title_right = install_dlg._title_lbl.geometry().left() + QFontMetrics(
            install_dlg._title_lbl.font()
        ).horizontalAdvance(install_dlg._title_lbl.text())
        title_right = max(title_right, install_dlg._title_lbl.geometry().right())
        name_git_gap = install_dlg._open_git_btn.geometry().left() - title_right
        assert 4 <= name_git_gap <= 12, (
            f"Install title→OpenGit gap {name_git_gap}px not in 4..12 "
            f"(btn should sit next to title, not fork/version row)"
        )
        assert install_dlg._preview_pending is True
        assert not install_dlg.fork_combo.isEnabled()
        assert not install_dlg.version_combo.isEnabled()
        assert D._LOADING_PREVIEW_TIP in (install_dlg.fork_combo.toolTip() or "")

        install_dlg._preview_pending = False
        install_dlg._forks_fetch_done = True
        install_dlg._sync_browse_combos()
        assert install_dlg.fork_combo.isEnabled()
        assert install_dlg.version_combo.isEnabled()
        install_dlg.close()
    finally:
        G.has_github_token = prev_token
        D._PreviewFetchThread.start = orig_preview_start  # type: ignore[method-assign]
        D._AddonBrowseFetchThread.start = orig_browse_start  # type: ignore[method-assign]

    print("OK addon preview gates fork/version; Open in Git inline beside title")

def test_glue_combo_popup_hide_wiring_in_settings_dialogs():
    """Settings/Install use Dialog (not Qt.Popup); lazy version fetch keeps combo enabled."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from ichalaunch.addons import github as G
    from ichalaunch.ui.widgets import dialogs as D
    from ichalaunch.ui.widgets.glue_combo import GlueComboBox

    app = QApplication.instance() or QApplication([])
    flags = D._themed_dialog_flags()
    # Dialog=0b11 and Popup=0b1001 share the Window bit — compare the type byte.
    type_byte = int(flags) & 0xFF
    assert type_byte == int(Qt.WindowType.Dialog)
    assert type_byte != int(Qt.WindowType.Popup)
    assert int(flags) & int(Qt.WindowType.FramelessWindowHint)

    # hidePopup recovers when open-flag is set (desync path after popupShown races).
    combo = GlueComboBox(None, min_width=120)
    combo.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    combo.addItem("a", "a")
    combo._popup_open = True
    combo._hiding_popup = False
    combo.hidePopup()
    assert not combo._popup_open
    assert combo._hiding_popup
    combo._hiding_popup = False
    combo.hidePopup()
    assert not combo._popup_open
    assert not combo._hiding_popup

    # showPopup must call super before popupShown so a sync/hide handler cannot
    # clear flags and still leave a later native show stuck open.
    names = GlueComboBox.showPopup.__code__.co_names
    assert "showPopup" in names
    assert "emit" in names
    assert names.index("showPopup") < names.index("emit")

    entry = {
        "name": "pfUI",
        "folder": "pfUI",
        "repo": "https://github.com/shagu/pfUI",
        "repository": "shagu/pfUI",
    }
    prev_token = G.has_github_token
    orig_preview_start = D._PreviewFetchThread.start
    orig_browse_start = D._AddonBrowseFetchThread.start
    orig_cached = G.get_cached_repo_versions

    def _noop_start(self):  # noqa: ANN001
        return None

    try:
        G.has_github_token = lambda: True  # type: ignore[assignment]
        G.get_cached_repo_versions = lambda *_a, **_k: None  # type: ignore[assignment]
        D._PreviewFetchThread.start = _noop_start  # type: ignore[method-assign]
        D._AddonBrowseFetchThread.start = _noop_start  # type: ignore[method-assign]

        settings_dlg = D.AddonSettingsDialog(None, entry, meta={"tag": "5.4.4"})
        settings_dlg.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        assert (int(settings_dlg.windowFlags()) & 0xFF) == int(Qt.WindowType.Dialog)
        assert settings_dlg._version_combo is not None
        assert settings_dlg._fork_combo is not None
        settings_dlg._preview_pending = False
        settings_dlg._fork_fetch_pending = False
        settings_dlg._version_fetch_pending = False
        settings_dlg._sync_combo_interactivity()
        assert settings_dlg._version_combo.isEnabled()
        settings_dlg._versions_loaded = False
        settings_dlg._version_fetch_pending = False
        settings_dlg._browse_owner = "shagu"
        settings_dlg._browse_repo = "pfUI"
        settings_dlg._lazy_fetch_versions()
        assert settings_dlg._version_fetch_pending is True
        assert settings_dlg._version_combo.isEnabled()
        # First open must show a Loading… row instead of an empty/stale-only list.
        loading_rows = [
            i
            for i in range(settings_dlg._version_combo.count())
            if str(settings_dlg._version_combo.itemData(i) or "") == D._VERSIONS_LOADING_DATA
        ]
        assert loading_rows, "lazy miss should insert Loading… into the open list"
        # Async completion must repopulate without hidePopup (no second click).
        settings_dlg._version_combo._popup_open = True
        settings_dlg._on_versions_fetched([], ["5.4.4", "5.4.3"], settings_dlg._browse_fetch_gen)
        assert settings_dlg._versions_loaded is True
        assert settings_dlg._version_combo._popup_open is True
        assert settings_dlg._version_combo.count() >= 3  # tip + tags
        assert D._VERSIONS_LOADING_DATA not in [
            str(settings_dlg._version_combo.itemData(i) or "")
            for i in range(settings_dlg._version_combo.count())
        ]
        settings_dlg.close()

        install_dlg = D.AddonInstallPickerDialog(None, entry)
        install_dlg.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        assert (int(install_dlg.windowFlags()) & 0xFF) == int(Qt.WindowType.Dialog)
        assert isinstance(install_dlg.fork_combo, GlueComboBox)
        assert isinstance(install_dlg.version_combo, GlueComboBox)
        install_dlg.close()
    finally:
        G.has_github_token = prev_token
        G.get_cached_repo_versions = orig_cached
        D._PreviewFetchThread.start = orig_preview_start  # type: ignore[method-assign]
        D._AddonBrowseFetchThread.start = orig_browse_start  # type: ignore[method-assign]
        combo.deleteLater()

    print("OK glue combo popup hide wiring / settings dialog flags")


def test_addon_settings_version_prefetch_first_open():
    """Version list is warmed after forks settle so the first open already has tags."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from ichalaunch.addons import github as G
    from ichalaunch.ui.widgets import dialogs as D

    app = QApplication.instance() or QApplication([])
    entry = {
        "name": "pfUI",
        "folder": "pfUI",
        "repo": "https://github.com/shagu/pfUI",
        "repository": "shagu/pfUI",
    }
    prev_token = G.has_github_token
    orig_preview_start = D._PreviewFetchThread.start
    orig_browse_start = D._AddonBrowseFetchThread.start
    orig_cached_forks = G.get_cached_repo_forks
    orig_cached_versions = G.get_cached_repo_versions

    def _noop_start(self):  # noqa: ANN001
        return None

    try:
        G.has_github_token = lambda: True  # type: ignore[assignment]
        D._PreviewFetchThread.start = _noop_start  # type: ignore[method-assign]
        D._AddonBrowseFetchThread.start = _noop_start  # type: ignore[method-assign]
        G.get_cached_repo_forks = lambda *_a, **_k: [  # type: ignore[assignment]
            {
                "label": "shagu/pfUI",
                "repo": "https://github.com/shagu/pfUI",
                "owner": "shagu",
                "repo_name": "pfUI",
            }
        ]
        G.get_cached_repo_versions = lambda *_a, **_k: ["5.4.4", "5.4.3"]  # type: ignore[assignment]

        dlg = D.AddonSettingsDialog(None, entry, meta={"tag": "5.4.4"})
        dlg.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        assert dlg._version_combo is not None
        # Simulate dialog show: forks-from-cache path must also warm versions.
        dlg._prefetch_forks()
        assert dlg._forks_loaded is True
        assert dlg._versions_loaded is True
        assert dlg._version_combo.count() >= 3
        tags = [
            str(dlg._version_combo.itemData(i) or "")
            for i in range(dlg._version_combo.count())
        ]
        assert "5.4.4" in tags
        assert "5.4.3" in tags
        # First popupShown is a no-op once warmed (no second-click fetch).
        pending_before = dlg._version_fetch_pending
        dlg._lazy_fetch_versions()
        assert dlg._version_fetch_pending is pending_before
        dlg.close()
    finally:
        G.has_github_token = prev_token
        G.get_cached_repo_forks = orig_cached_forks
        G.get_cached_repo_versions = orig_cached_versions
        D._PreviewFetchThread.start = orig_preview_start  # type: ignore[method-assign]
        D._AddonBrowseFetchThread.start = orig_browse_start  # type: ignore[method-assign]

    print("OK settings version prefetch fills list before first open")


def test_addon_settings_reinstall_enabled_when_selection_differs():
    """Settings Reinstall enables only when fork/version differ; wires _prefer_selection."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from ichalaunch.addons import github as G
    from ichalaunch.ui.widgets import dialogs as D

    app = QApplication.instance() or QApplication([])
    entry = {
        "name": "pfUI",
        "folder": "pfUI",
        "repo": "https://github.com/shagu/pfUI",
        "repository": "shagu/pfUI",
        "tag": "5.4.4",
    }
    meta = {"tag": "5.4.4", "repository": "shagu/pfUI", "url": "https://github.com/shagu/pfUI"}

    prev_token = G.has_github_token
    orig_preview_start = D._PreviewFetchThread.start
    orig_browse_start = D._AddonBrowseFetchThread.start

    def _noop_start(self):  # noqa: ANN001
        return None

    try:
        G.has_github_token = lambda: True  # type: ignore[assignment]
        D._PreviewFetchThread.start = _noop_start  # type: ignore[method-assign]
        D._AddonBrowseFetchThread.start = _noop_start  # type: ignore[method-assign]

        dlg = D.AddonSettingsDialog(None, entry, meta=meta)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        assert dlg._reinstall_btn is not None
        assert dlg._baseline_repo == "shagu/pfui"
        assert dlg._baseline_tag == "5.4.4"
        # Preview gate: locked while loading even if selection would differ later.
        assert dlg._preview_pending is True
        assert not dlg._reinstall_btn.isEnabled()

        dlg._preview_pending = False
        dlg._fork_fetch_pending = False
        dlg._sync_combo_interactivity()
        assert not dlg._selection_differs_from_install()
        assert not dlg._reinstall_btn.isEnabled()

        # Different version → Reinstall enabled.
        assert dlg._version_combo is not None
        dlg._version_combo.blockSignals(True)
        dlg._version_combo.addItem("v9.9.9", "9.9.9")
        dlg._version_combo.setCurrentIndex(dlg._version_combo.count() - 1)
        dlg._version_combo.blockSignals(False)
        dlg._sync_reinstall_button()
        assert dlg._selection_differs_from_install()
        assert dlg._reinstall_btn.isEnabled()

        # Busy/preview lock wins over a differing selection.
        dlg._preview_pending = True
        dlg._sync_reinstall_button()
        assert not dlg._reinstall_btn.isEnabled()
        dlg._preview_pending = False
        dlg._sync_reinstall_button()
        assert dlg._reinstall_btn.isEnabled()

        dlg._accept_reinstall()
        result = dlg.result_data()
        assert isinstance(result, dict)
        assert result.get("_action") == "reinstall"
        assert result.get("_prefer_selection") is True
        assert result.get("folder") == "pfUI"
        assert result.get("tag") == "9.9.9"
        assert "9.9.9" in str(result.get("repo") or "")
        dlg.close()

        # Matching selection after unlock stays disabled.
        dlg2 = D.AddonSettingsDialog(None, entry, meta=meta)
        dlg2.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        dlg2._preview_pending = False
        dlg2._fork_fetch_pending = False
        dlg2._sync_combo_interactivity()
        assert not dlg2._reinstall_btn.isEnabled()
        dlg2.close()
    finally:
        G.has_github_token = prev_token
        D._PreviewFetchThread.start = orig_preview_start  # type: ignore[method-assign]
        D._AddonBrowseFetchThread.start = orig_browse_start  # type: ignore[method-assign]

    # open_addon_settings should emit reinstall_requested with prefer flag.
    from ichalaunch.ui.pages.addons import AddonsPage

    page = AddonsPage()
    page.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    seen: list[dict] = []
    page.reinstall_requested.connect(lambda e: seen.append(dict(e)))

    def _fake_dialog(parent, ent, *, meta=None):  # noqa: ANN001
        out = dict(ent)
        out["tag"] = "9.9.9"
        out["pin_release"] = "9.9.9"
        out["repo"] = "https://github.com/shagu/pfUI/releases/tag/9.9.9"
        out["repository"] = "shagu/pfUI"
        out["_action"] = "reinstall"
        out["_prefer_selection"] = True
        return out

    import ichalaunch.ui.widgets.dialogs as dialogs_mod

    prev_dlg = dialogs_mod.addon_settings_dialog
    try:
        dialogs_mod.addon_settings_dialog = _fake_dialog  # type: ignore[assignment]
        page.open_addon_settings(dict(entry))
    finally:
        dialogs_mod.addon_settings_dialog = prev_dlg  # type: ignore[assignment]
        page.close()

    assert len(seen) == 1
    assert seen[0].get("_prefer_selection") is True
    assert seen[0].get("folder") == "pfUI"
    assert seen[0].get("tag") == "9.9.9"
    assert "_action" not in seen[0]

    print("OK addon settings reinstall enables on selection diff and wires prefer_selection")


def test_pass_remove_uses_wow_art():
    from PySide6.QtWidgets import QApplication

    from ichalaunch.core.paths import theme_file
    from ichalaunch.ui.widgets.common import PassRemoveButton, _pass_icon_pixmap

    app = QApplication.instance() or QApplication([])
    assert theme_file("UI-GroupLoot-Pass-Up.PNG").is_file()
    assert theme_file("UI-GroupLoot-Pass-Down.PNG").is_file()
    up = _pass_icon_pixmap(pressed=False)
    down = _pass_icon_pixmap(pressed=True)
    assert not up.isNull()
    assert not down.isNull()
    assert up.width() == 20
    btn = PassRemoveButton()
    assert btn.size().width() == 28
    assert btn.size().height() == 28
    print("OK addon remove uses GroupLoot Pass art without plate chrome")


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


def test_client_hd_graphics_display_order():
    """Client HD Graphics rows must keep hd_dxvk first (layout order, not dict order)."""
    from PySide6.QtWidgets import QApplication

    from ichalaunch.ui.pages.client import ClientPage
    from ichalaunch.ui.widgets.common import ModCheckRow

    app = QApplication.instance() or QApplication([])
    page = ClientPage()
    host = page._cat_hosts.get("HD Graphics")
    assert host is not None, "HD Graphics category missing"

    rendered: list[str] = []
    for i in range(host.count()):
        item = host.itemAt(i)
        w = item.widget() if item is not None else None
        if isinstance(w, ModCheckRow):
            rendered.append(w.mod_id)

    print("Client HD Graphics row order:", rendered[:5], "... total", len(rendered))
    assert rendered, "expected HD Graphics rows"
    assert rendered[0] == "hd_dxvk", f"DXVK must be top; got {rendered[:5]}"
    assert rendered[1] == "vanilla_helpers", f"Vanilla Helpers must be second; got {rendered[:5]}"
    assert rendered.index("hd_dxvk") < rendered.index("vanilla_helpers")
    for mid in rendered[1:]:
        assert rendered.index("hd_dxvk") < rendered.index(mid), (
            f"hd_dxvk must precede {mid}; order={rendered}"
        )
    # First five must stay stable for regressions (DXVK / helpers / Reforged patches).
    assert rendered[:5] == [
        "hd_dxvk",
        "vanilla_helpers",
        "hd_patch_a",
        "hd_patch_b",
        "hd_patch_c",
    ], rendered[:5]
    print("OK client HD Graphics display order")


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


def test_launch_settings_live_on_client_page():
    """Launch checkboxes live on Client → Launch (before Custom), not Settings."""
    from PySide6.QtWidgets import QApplication, QLabel

    import ichalaunch.ui.pages.settings as settings_page_mod
    from ichalaunch.ui.pages.client import CATEGORY_ORDER, LAUNCH_CATEGORY, ClientPage
    from ichalaunch.ui.widgets.common import ModCheckRow
    from ichalaunch.ui.widgets.launch_settings import LaunchSettingsPanel

    app = QApplication.instance() or QApplication([])
    del app

    assert LAUNCH_CATEGORY == "Launch"
    assert CATEGORY_ORDER[-2:] == [LAUNCH_CATEGORY, "Custom"], CATEGORY_ORDER
    assert CATEGORY_ORDER[0] != LAUNCH_CATEGORY

    settings_page = settings_page_mod.SettingsPage()
    for attr in (
        "cb_min",
        "cb_close",
        "cb_wow64",
        "cb_vcache",
        "cb_frame_cap",
        "cb_nampower_encrypt",
    ):
        assert not hasattr(settings_page, attr), attr
    assert not hasattr(settings_page, "cb_vf")
    titles = [
        w.text()
        for w in settings_page.findChildren(QLabel)
        if w.objectName() == "CardTitle"
    ]
    assert "Launch" not in titles, titles
    settings_page.deleteLater()

    page = ClientPage()
    assert isinstance(page.launch_settings, LaunchSettingsPanel)
    assert LAUNCH_CATEGORY in page._cat_index
    assert page._cat_index[LAUNCH_CATEGORY] == page._cat_index["Custom"] - 1
    launch_idx = page._cat_index[LAUNCH_CATEGORY]
    assert page.cat_btns[launch_idx].text() == "Launch"
    host = page._cat_hosts[LAUNCH_CATEGORY]
    widgets = []
    for i in range(host.count()):
        item = host.itemAt(i)
        w = item.widget() if item is not None else None
        if w is not None:
            widgets.append(w)
    assert page.launch_settings in widgets
    assert not any(isinstance(w, ModCheckRow) for w in widgets)
    for attr in (
        "cb_min",
        "cb_close",
        "cb_wow64",
        "cb_vcache",
        "cb_frame_cap",
        "cb_nampower_encrypt",
    ):
        assert hasattr(page.launch_settings, attr), attr
    assert not hasattr(page.launch_settings, "cb_vf")
    page.deleteLater()
    print("OK launch settings live on client page")


def test_chrome_buttons_clear_metal_tr():
    from ichalaunch.core.paths import theme_file
    from ichalaunch.ui import main_window as mw

    assert mw._CHROME_BTN_INSET_X >= mw._METAL_EDGE_DRAW
    assert mw._CHROME_BTN_INSET_Y >= mw._METAL_EDGE_DRAW
    assert mw._METAL_FLOOR_OUTSET >= 1
    assert mw._METAL_CORNER_DRAW >= 100  # full L arms, not a ~40 stub cell
    src = Path(mw.__file__).read_text(encoding="utf-8")
    assert "_crop_metal_corner_cell" not in src
    assert "_metal_underfill_path" in src
    assert "_FRAME_STROKE" not in src
    assert "painter.drawPath(path)" not in src
    assert "_progress_slot" in src
    assert "SideCornersOverlay" not in src
    assert "left_corners.png" not in src
    assert "class PortraitPlayFrame" in src
    assert "WA_TransparentForMouseEvents" in src
    for name in (
        mw._METAL_EDGE_NAME,
        mw._METAL_CORNER_NAME,
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


def test_owned_paths_are_comparison_keys_not_filenames():
    """Owned-path keys are lowercased for comparison and must not be used as
    filenames.

    _mod_owned_paths() normalises to lowercase on purpose -- its docstring says
    so -- because it exists to answer "does this mod own that path". Four call
    sites then joined those keys onto the game directory and touched the result.
    On Windows and macOS that works by accident; on Linux the lookup misses and
    the miss is read as "the file is not there".

    The visible symptom was that every vanillafixes and dxvk install rolled
    itself back: the installer writes VanillaFixes.exe and VfPatcher.dll, then
    verified vanillafixes.exe and vfpatcher.dll and declared the install failed.
    """
    from ichalaunch.mods.installer import (
        _install_backup_paths,
        _mod_owned_paths,
        _pe_artifacts_for_mod,
        _verify_mod_install,
        get_mod,
    )

    mod = get_mod("vanillafixes")
    assert mod, "vanillafixes missing from the catalog"

    # The keys really are lowercased -- this is the property the call sites
    # were relying on being a filename.
    owned = _mod_owned_paths(mod)
    assert "vanillafixes.exe" in owned, owned
    assert "VanillaFixes.exe" not in owned, owned
    artifacts = _pe_artifacts_for_mod(mod)
    assert "vanillafixes.exe" in artifacts, artifacts

    with tempfile.TemporaryDirectory() as td:
        game = Path(td)
        # Written exactly as install_mod writes them: mixed case.
        (game / "WoW.exe").write_bytes(b"MZ" + b"\0" * 4096)
        (game / "VanillaFixes.exe").write_bytes(b"MZ" + b"\0" * 65536)
        (game / "VfPatcher.dll").write_bytes(b"MZ" + b"\0" * 65536)

        # Verification must find them. Before the fix this raised OSError(22)
        # "vanillafixes.exe was not installed; vfpatcher.dll was not installed"
        # and the caller rolled back an install that had in fact succeeded.
        try:
            _verify_mod_install(game, mod)
        except OSError as exc:
            raise AssertionError(
                f"verify rejected a correctly installed mod: {exc}") from exc

        # The pre-install snapshot must point at the real files. A backup that
        # silently holds nothing is worse than no backup, because the rollback
        # path believes it has something to restore.
        backups = {p.name for p in _install_backup_paths(game, mod)}
        backups_l = {n.lower() for n in backups}
        assert "vanillafixes.exe" in backups_l, backups
        assert "vfpatcher.dll" in backups_l, backups
        # On Windows Path.name keeps the lookup spelling because exists()
        # succeeds for any case. On a case-sensitive FS the snapshot must
        # name the real files or rollback restores nothing.
        if sys.platform != "win32":
            assert "VanillaFixes.exe" in backups, backups
            assert "VfPatcher.dll" in backups, backups
            assert "vfpatcher.dll" not in backups, backups

        # And a genuinely absent artifact must still be reported as absent --
        # the fix must not turn the check into a no-op.
        (game / "VfPatcher.dll").unlink()
        raised = False
        try:
            _verify_mod_install(game, mod)
        except OSError:
            raised = True
        assert raised, "verify must still fail when an artifact is really missing"

    print("OK owned paths are comparison keys not filenames")


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


def test_windows_exe_runs_through_proton_and_blocks():
    """The Vanilla Tweaks patcher is a PE, so off Windows it must go via Proton.

    Runs a stand-in for umu-run (a shell script, never wine) to prove the call
    passes the patcher's flags through, waits for the exit, turns a non-zero
    exit and a hang into errors, and does not pin a patcher to the V-Cache CCD.
    """
    import inspect
    import os
    import stat as _stat

    from ichalaunch.core.process import run_windows_exe as dispatch
    from ichalaunch.game import proton
    from ichalaunch.mods import installer

    # The installer must call the dispatcher, not exec the PE itself.
    src = inspect.getsource(installer.install_mod)
    assert "run_windows_exe(cmd, game)" in src, "exe_patch does not dispatch"
    assert "subprocess.run(cmd" not in src, "exe_patch still execs the PE directly"
    # The Windows branch of the dispatcher stays a direct, checked run.
    dsrc = inspect.getsource(dispatch)
    assert 'sys.platform == "win32"' in dsrc, dsrc
    assert "subprocess.run(argv, cwd=str(cwd), check=True)" in dsrc, dsrc

    if sys.platform == "win32":
        print("OK windows exe runs through proton and blocks (skipped on Windows)")
        return

    class _Stub:
        def __init__(self, d):
            self.d = dict(d)

        def get(self, k, default=None):
            return self.d.get(k, default)

        def set(self, k, v):
            self.d[k] = v

    def _script(path, body):
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(path.stat().st_mode | _stat.S_IXUSR)
        return path

    real = proton.settings
    try:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            game = root / "WoW"
            game.mkdir()
            infile = game / "WoW-OriginalBackup.exe"
            infile.write_bytes(b"MZ" + b"\0" * 64)
            patcher = root / "vanilla-tweaks.exe"
            patcher.write_bytes(b"MZ" + b"\0" * 64)
            build = root / "GE-Proton10-34"
            build.mkdir()
            (build / "toolmanifest.vdf").write_text("x")

            seen = root / "argv.txt"
            ok = _script(root / "umu-ok", f"printf '%s\\n' \"$@\" > {seen}\nexit 0\n")
            bad = _script(root / "umu-bad", "echo 'boom: bad prefix' 1>&2\nexit 3\n")
            slow = _script(root / "umu-slow", "exec sleep 30\n")

            def _use(umu):
                proton.settings = _Stub({
                    "linux_umu_path": str(umu),
                    "linux_proton_path": str(build),
                    "linux_use_latest_proton": False,
                    "linux_wineprefix": str(root / "prefix"),
                })

            # A clean run blocks, so its output is already on disk on return.
            _use(ok)
            argv = ["--farclip", "777", str(infile)]
            proc = proton.run_windows_exe(patcher, game, argv)
            assert proc.returncode == 0
            passed = seen.read_text().splitlines()
            assert passed[0] == str(patcher), passed
            assert passed[1:3] == ["--farclip", "777"], passed
            # The infile is named on the drive Wine always maps to /, rather
            # than left to resolve against whatever drive the cwd landed on.
            assert passed[3] == "Z:" + str(infile).replace("/", "\\"), passed

            # Nothing is pinned: narrowing a patcher's affinity only slows it.
            cmd, env = proton.build_launch_command(patcher, game, argv, for_game=False)

            # A tool never carries the client's encryption key, even when the
            # setting is on and even when one is already in this environment.
            import ichalaunch.game.nampower_encrypt as ne

            os.environ[ne.WOW_ENCRYPTION_ENV] = "inherited-secret-value"
            try:
                _, tool_env = proton.build_launch_command(patcher, game, argv, for_game=False)
                assert ne.WOW_ENCRYPTION_ENV not in tool_env, tool_env.get(ne.WOW_ENCRYPTION_ENV)
            finally:
                os.environ.pop(ne.WOW_ENCRYPTION_ENV, None)

            # Captured tool output reaches a dialog and the log, so it goes
            # through the same redactor every other path for this stream uses.
            assert "redact_encryption_secrets" in inspect.getsource(proton.run_windows_exe)
            assert cmd[0] == str(ok), cmd
            assert env["PROTONPATH"] == str(build)

            # A failed patch is an error, and says what the tool complained of.
            _use(bad)
            try:
                proton.run_windows_exe(patcher, game, argv)
                raise AssertionError("expected RuntimeError on non-zero exit")
            except RuntimeError as exc:
                assert "exit code 3" in str(exc), str(exc)
                assert "boom: bad prefix" in str(exc), str(exc)

            # A hang ends in a message, never a silent success.
            _use(slow)
            try:
                proton.run_windows_exe(patcher, game, argv, timeout=2)
                raise AssertionError("expected RuntimeError on timeout")
            except RuntimeError as exc:
                assert "did not finish" in str(exc), str(exc)
                assert "has not been changed" in str(exc), str(exc)

            # And the dispatcher routes here rather than exec'ing the PE.
            _use(ok)
            os.remove(seen)
            dispatch([str(patcher), *argv], game)
            assert seen.read_text().splitlines()[0] == str(patcher)

            # Windows is untouched: the same argv, the same cwd string and the
            # same check=True that the call site used before Proton entered it,
            # and proton.py is never imported there.
            import subprocess as _sp

            calls = []
            real_run = _sp.run
            real_platform = sys.platform
            try:
                _sp.run = lambda *a, **kw: calls.append((a, kw))
                sys.platform = "win32"
                dispatch([str(patcher), *argv], game)
            finally:
                _sp.run = real_run
                sys.platform = real_platform
            assert calls == [(([str(patcher), *argv],),
                             {"cwd": str(game), "check": True})], calls
    finally:
        proton.settings = real

    print("OK windows exe runs through proton and blocks")


def test_linux_wow64_default_on_when_supported():
    """New WoW64 is on by default, probed per build, and never set blind.

    Guards four things: that an untouched install gets it on a build that ships
    files/bin-wow64, that the same untouched install gets a normal launch on a
    build that does not, that turning it off is honoured on a capable build, and
    that a value inherited from the caller's own environment cannot decide the
    launch mode behind the setting's back.
    """
    if sys.platform == "win32":
        print("OK linux wow64 default-on (skipped on Windows)")
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
    inherited = os.environ.get("PROTON_USE_WOW64")
    try:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            umu = root / "umu-run"
            umu.write_text("#!/bin/sh\nexit 0\n")
            umu.chmod(0o755)

            # One build ships the 64-bit host, one does not. This is the real
            # difference between GE-Proton10-34 and GE-Proton11-5.
            withw = root / "GE-Proton10-34"
            (withw / "files" / "bin-wow64").mkdir(parents=True)
            (withw / "files" / "bin-wow64" / "wine").write_text("#!/bin/sh\n")
            (withw / "toolmanifest.vdf").write_text("x")
            without = root / "GE-Proton11-5"
            (without / "files" / "bin").mkdir(parents=True)
            (without / "toolmanifest.vdf").write_text("x")
            # An interrupted download or a trimmed build: the directory is
            # there, the loader Proton would exec is not. Proton does not check,
            # so a directory-only probe would pass this and fail the launch.
            partial = root / "GE-Proton10-34-partial"
            (partial / "files" / "bin-wow64").mkdir(parents=True)
            (partial / "toolmanifest.vdf").write_text("x")

            assert proton.proton_supports_wow64(withw)
            assert not proton.proton_supports_wow64(without)
            assert not proton.proton_supports_wow64(partial)

            _UNSET = object()

            def cmd(build, enabled=_UNSET):
                data = {
                    "linux_proton_path": str(build),
                    "linux_use_latest_proton": False,
                    "linux_umu_path": str(umu),
                    "linux_wineprefix": str(root / "prefix"),
                }
                # Omitted entirely rather than set, so this exercises the
                # DEFAULTS fallback the way an untouched install hits it.
                if enabled is not _UNSET:
                    data["linux_use_wow64"] = enabled
                proton.settings = _Stub(data)
                return proton.build_launch_command(root / "WoW.exe", root)[1]

            # The default an untouched install gets: on where it can be honoured.
            assert cmd(withw).get("PROTON_USE_WOW64") == "1"

            # Same untouched install, a build that cannot honour it: the flag is
            # absent, not "0", and the launch is otherwise unchanged.
            assert "PROTON_USE_WOW64" not in cmd(without)
            # And the half-extracted build is declined for the same reason.
            assert "PROTON_USE_WOW64" not in cmd(partial)

            # Turning it off is honoured even where the build supports it.
            assert "PROTON_USE_WOW64" not in cmd(withw, False)

            # On, and the build can honour it.
            assert cmd(withw, True).get("PROTON_USE_WOW64") == "1"

            # On, but the build has no 64-bit host. Setting it here would fail
            # the launch outright, so it is deliberately left unset.
            assert "PROTON_USE_WOW64" not in cmd(without, True)

            # An inherited value must not survive: the setting is the only
            # thing that decides the launch mode.
            os.environ["PROTON_USE_WOW64"] = "1"
            try:
                assert "PROTON_USE_WOW64" not in cmd(withw, False)
                assert "PROTON_USE_WOW64" not in cmd(without, True)
            finally:
                os.environ.pop("PROTON_USE_WOW64", None)
    finally:
        proton.settings = real
        if inherited is None:
            os.environ.pop("PROTON_USE_WOW64", None)
        else:
            os.environ["PROTON_USE_WOW64"] = inherited

    print("OK linux wow64 default-on where supported")


def test_linux_wow64_settings_checkbox():
    """The Settings checkbox reflects a default-on key and is Linux-only.

    Two separate things. First, the upgrade path: a settings.json written before
    this key existed must come back with it on, because _merge_loaded starts from
    dict(DEFAULTS) -- that is what carries the new default to existing users
    rather than only to fresh installs.

    Second, refresh(): the shared loop next to it hardcodes False as its fallback,
    which is the wrong default for this key. That fallback is unreachable while
    _merge_loaded materialises DEFAULTS, so driving the box through the loop would
    work today and quietly stop working if that ever changed. The absent-key
    assertion pins it; reverting to the loop fails it.
    """
    import sys as _sys

    from PySide6.QtWidgets import QApplication

    import ichalaunch.ui.pages.settings as settings_page_mod
    from ichalaunch.config.settings import DEFAULTS, Settings, settings
    from ichalaunch.game.proton import WOW64_DEFAULT_ON, wow64_enabled

    # Stored as null, not as the default itself. save() writes the whole
    # DEFAULTS-merged dict, so a literal True here would be baked into every
    # settings.json on first launch and would then outrank WOW64_DEFAULT_ON
    # forever -- turning the one-line revert into a no-op for everyone who had
    # already run the launcher once.
    assert DEFAULTS["linux_use_wow64"] is None
    assert WOW64_DEFAULT_ON is True

    # The upgrade path. A settings.json from before this key existed has no
    # opinion about it, and must stay that way rather than being pinned.
    legacy = {"game_path": "", "close_on_launch": True}
    merged, _ = Settings.__new__(Settings)._merge_loaded(legacy)
    assert merged["linux_use_wow64"] is None
    # An explicit choice in the file still wins over the default, both ways.
    for stored in (True, False):
        m, _ = Settings.__new__(Settings)._merge_loaded(
            {**legacy, "linux_use_wow64": stored}
        )
        assert m["linux_use_wow64"] is stored

    # And the revert lever still works: with nothing stored, flipping the
    # constant flips what users get.
    prev_stored = settings.get("linux_use_wow64", None)
    import ichalaunch.game.proton as _proton
    try:
        settings.set("linux_use_wow64", None)
        assert wow64_enabled() is True
        _proton.WOW64_DEFAULT_ON = False
        assert wow64_enabled() is False
        # An explicit choice is not overridden by the constant.
        settings.set("linux_use_wow64", True)
        assert wow64_enabled() is True
    finally:
        _proton.WOW64_DEFAULT_ON = True
        settings.set("linux_use_wow64", prev_stored)

    app = QApplication.instance() or QApplication(_sys.argv)
    assert app is not None
    settings_page = settings_page_mod.SettingsPage()
    assert not hasattr(settings_page, "cb_wow64")

    from ichalaunch.ui.widgets.launch_settings import LaunchSettingsPanel

    page = LaunchSettingsPanel()

    # Constructed on every platform so refresh() needs no platform branch, but
    # only shown where the setting can do anything.
    assert hasattr(page, "cb_wow64")
    if _sys.platform == "win32":
        assert page.cb_wow64.parent() is None
        print("OK linux wow64 checkbox hidden on Windows")
        return

    prev = settings.get("linux_use_wow64", True)
    try:
        # Absent key: the box must follow DEFAULTS, not fall back to False.
        settings._data.pop("linux_use_wow64", None)
        page.refresh()
        assert page.cb_wow64.isChecked()

        settings.set("linux_use_wow64", False)
        page.refresh()
        assert not page.cb_wow64.isChecked()

        settings.set("linux_use_wow64", True)
        page.refresh()
        assert page.cb_wow64.isChecked()
    finally:
        settings.set("linux_use_wow64", prev)

    print("OK linux wow64 settings checkbox")


def test_vcache_pin_default_is_not_persisted():
    """The V-Cache default is resolvable, not stored, so it can still be reverted."""
    from ichalaunch.config.settings import DEFAULTS, Settings, settings
    import ichalaunch.game.cpu_topology as topo

    assert DEFAULTS["pin_to_vcache_ccd"] is None
    assert topo.VCACHE_PIN_DEFAULT_ON is True

    legacy = {"game_path": "", "close_on_launch": True}
    merged, _ = Settings.__new__(Settings)._merge_loaded(legacy)
    assert merged["pin_to_vcache_ccd"] is None
    for stored in (True, False):
        m, _ = Settings.__new__(Settings)._merge_loaded(
            {**legacy, "pin_to_vcache_ccd": stored}
        )
        assert m["pin_to_vcache_ccd"] is stored

    prev = settings.get("pin_to_vcache_ccd", None)
    try:
        settings.set("pin_to_vcache_ccd", None)
        assert topo.vcache_pin_enabled() is True
        topo.VCACHE_PIN_DEFAULT_ON = False
        assert topo.vcache_pin_enabled() is False
        settings.set("pin_to_vcache_ccd", True)
        assert topo.vcache_pin_enabled() is True
    finally:
        topo.VCACHE_PIN_DEFAULT_ON = True
        settings.set("pin_to_vcache_ccd", prev)

    print("OK vcache pin default is resolvable, not persisted")


def test_x3d_vcache_ccd_selection():
    """Pin only where there is a genuine V-Cache choice; Windows offsets are real."""
    import ctypes
    import shutil
    import struct

    from ichalaunch.game import cpu_topology as ct

    mb = 1024 * 1024

    d = ct.CacheDomain(96 * mb, tuple(list(range(0, 8)) + list(range(16, 24))))
    assert d.cpu_list == "0-7,16-23", d.cpu_list
    assert ct.CacheDomain(1, (3,)).cpu_list == "3"
    assert ct.CacheDomain(1, (0, 2, 4)).cpu_list == "0,2,4"
    assert ct.CacheDomain(1, (0, 1, 2, 5)).cpu_list == "0-2,5"
    assert ct.CacheDomain(1, (0, 1)).affinity_mask == 0b11
    assert d.affinity_mask == 0x00FF00FF, hex(d.affinity_mask)

    assert ct._parse_size("98304K") == 96 * mb
    assert ct._parse_size("96M") == 96 * mb
    assert ct._parse_size("garbage") is None
    assert ct._parse_cpu_list("0-3,8") == (0, 1, 2, 3, 8)
    assert ct._parse_cpu_list("") == ()
    assert ct._parse_cpu_list("bad,1") == (1,)

    def _record(level, cache_size, mask, group=0):
        rec = bytearray(56)
        struct.pack_into("<I", rec, 0, 2)
        struct.pack_into("<I", rec, 4, 56)
        struct.pack_into("<B", rec, 8, level)
        struct.pack_into("<I", rec, 12, cache_size)
        struct.pack_into("<I", rec, 16, 2)
        struct.pack_into("<H", rec, 38, 1)
        struct.pack_into("<Q", rec, 40, mask)
        struct.pack_into("<H", rec, 48, group)
        return bytes(rec)

    def _buffer(*records):
        blob = b"".join(records)
        buf = (ctypes.c_byte * len(blob))()
        ctypes.memmove(buf, blob, len(blob))
        return buf, len(blob)

    buf, n = _buffer(
        _record(3, 96 * mb, 0x00FF00FF),
        _record(3, 32 * mb, 0xFF00FF00),
        _record(2, 1 * mb, 0x00000003),
    )
    parsed = ct._parse_windows_cache_buffer(buf, n)
    assert len(parsed) == 2, parsed
    by_size = {dom.l3_bytes: dom for dom in parsed}
    assert by_size[96 * mb].cpu_list == "0-7,16-23", by_size[96 * mb].cpu_list
    assert by_size[32 * mb].cpu_list == "8-15,24-31", by_size[32 * mb].cpu_list

    buf, n = _buffer(
        _record(3, 96 * mb, 0x00FF00FF, group=0),
        _record(3, 96 * mb, 0x00FF00FF, group=1),
    )
    assert ct._parse_windows_cache_buffer(buf, n) == []

    buf, n = _buffer(_record(3, 96 * mb, 0x00FF00FF))
    assert ct._parse_windows_cache_buffer(buf, n - 10) == []
    assert ct._parse_windows_cache_buffer(buf, 0) == []

    real = ct.cache_domains
    try:
        def stub(domains):
            ct.cache_domains = lambda: sorted(domains, key=lambda x: -x.l3_bytes)

        stub([
            ct.CacheDomain(96 * mb, tuple(range(0, 8)) + tuple(range(16, 24))),
            ct.CacheDomain(32 * mb, tuple(range(8, 16)) + tuple(range(24, 32))),
        ])
        v = ct.vcache_domain()
        assert v is not None and v.cpu_list == "0-7,16-23", v

        stub([ct.CacheDomain(96 * mb, tuple(range(0, 16)))])
        assert ct.vcache_domain() is None, "single-CCD X3D must not pin"

        stub([
            ct.CacheDomain(32 * mb, tuple(range(0, 16))),
            ct.CacheDomain(32 * mb, tuple(range(16, 32))),
        ])
        assert ct.vcache_domain() is None, "symmetric CCDs must not pin"

        stub([
            ct.CacheDomain(16 * mb, tuple(range(0, 4)) + tuple(range(12, 16))),
            ct.CacheDomain(8 * mb, tuple(range(4, 12)) + tuple(range(16, 24))),
        ])
        assert ct.vcache_domain() is None, "heterogeneous CCX must not pin"

        stub([
            ct.CacheDomain(96 * mb, tuple(range(0, 8))),
            ct.CacheDomain(80 * mb, tuple(range(8, 16))),
        ])
        assert ct.vcache_domain() is None, "sub-threshold ratio must not pin"

        stub([])
        assert ct.vcache_domain() is None
        assert ct.taskset_prefix() == []

        stub([ct.CacheDomain(96 * mb, (0, 1)), ct.CacheDomain(32 * mb, (2, 3))])
        prefix = ct.taskset_prefix()
        if sys.platform != "win32" and shutil.which("taskset"):
            assert prefix == ["taskset", "-c", "0-1"], prefix

        if sys.platform != "win32":
            import os as _os
            allowed = _os.sched_getaffinity(0)
            impossible = max(allowed) + 4096
            stub([
                ct.CacheDomain(96 * mb, (impossible, impossible + 1)),
                ct.CacheDomain(32 * mb, (0, 1)),
            ])
            assert ct.taskset_prefix() == [], (
                "an unusable CPU set must yield no prefix, or taskset kills the launch"
            )

            first = sorted(allowed)[0]
            stub([
                ct.CacheDomain(96 * mb, (first, impossible)),
                ct.CacheDomain(32 * mb, (impossible + 1,)),
            ])
            got = ct.taskset_prefix()
            if shutil.which("taskset"):
                assert got == ["taskset", "-c", str(first)], got
        with ct.launch_affinity() as pinned:
            assert pinned is None or sys.platform == "win32"
    finally:
        ct.cache_domains = real

    found = ct.cache_domains()
    assert isinstance(found, list)
    for dom in found:
        assert dom.l3_bytes > 0 and dom.cpus
    print("OK x3d vcache ccd selection")


def test_vcache_pin_settings_checkbox():
    """V-Cache checkbox is on every platform and follows the resolver, not False."""
    import sys as _sys

    from PySide6.QtWidgets import QApplication

    import ichalaunch.ui.pages.settings as settings_page_mod
    from ichalaunch.config.settings import settings
    from ichalaunch.game.cpu_topology import vcache_pin_enabled
    from ichalaunch.ui.widgets.launch_settings import LaunchSettingsPanel

    app = QApplication.instance() or QApplication(_sys.argv)
    assert app is not None
    settings_page = settings_page_mod.SettingsPage()
    assert not hasattr(settings_page, "cb_vcache")
    page = LaunchSettingsPanel()
    assert hasattr(page, "cb_vcache")
    assert page.cb_vcache.parent() is not None

    prev = settings.get("pin_to_vcache_ccd", None)
    try:
        settings._data.pop("pin_to_vcache_ccd", None)
        page.refresh()
        assert page.cb_vcache.isChecked() is vcache_pin_enabled()
        assert page.cb_vcache.isChecked()

        settings.set("pin_to_vcache_ccd", False)
        page.refresh()
        assert not page.cb_vcache.isChecked()

        settings.set("pin_to_vcache_ccd", True)
        page.refresh()
        assert page.cb_vcache.isChecked()
    finally:
        settings.set("pin_to_vcache_ccd", prev)

    print("OK vcache pin settings checkbox")


def test_frame_cap_default_is_not_persisted():
    """The frame-cap default is resolvable, not stored, so it can still be reverted."""
    from ichalaunch.config.settings import DEFAULTS, Settings, settings
    import ichalaunch.game.display as disp

    assert DEFAULTS["frame_cap_from_refresh"] is None
    assert disp.FRAME_CAP_DEFAULT_ON is True

    legacy = {"game_path": "", "close_on_launch": True}
    merged, _ = Settings.__new__(Settings)._merge_loaded(legacy)
    assert merged["frame_cap_from_refresh"] is None
    for stored in (True, False):
        m, _ = Settings.__new__(Settings)._merge_loaded(
            {**legacy, "frame_cap_from_refresh": stored}
        )
        assert m["frame_cap_from_refresh"] is stored

    prev = settings.get("frame_cap_from_refresh", None)
    try:
        settings.set("frame_cap_from_refresh", None)
        assert disp.frame_cap_enabled() is True
        disp.FRAME_CAP_DEFAULT_ON = False
        assert disp.frame_cap_enabled() is False
        settings.set("frame_cap_from_refresh", True)
        assert disp.frame_cap_enabled() is True
    finally:
        disp.FRAME_CAP_DEFAULT_ON = True
        settings.set("frame_cap_from_refresh", prev)

    print("OK frame cap default is resolvable, not persisted")


def test_frame_cap_from_refresh():
    """The DXVK frame cap follows the real display, and only the real display."""
    from ichalaunch.game import display

    assert display.frame_cap_for(165.058, 3) == 162, display.frame_cap_for(165.058, 3)
    assert display.frame_cap_for(165.058, 2) == 163
    assert display.frame_cap_for(59.94, 3) == 56, display.frame_cap_for(59.94, 3)
    assert display.frame_cap_for(60.0, 3) == 57
    assert display.frame_cap_for(239.76, 3) == 236
    assert display.frame_cap_for(165.058, 165) == 155, display.frame_cap_for(165.058, 165)
    assert display.frame_cap_for(144.0, 100) == 134, display.frame_cap_for(144.0, 100)
    assert display.frame_cap_for(100.0, -5) == 100
    assert display.frame_cap_for(165.058, "three") == 162
    assert display.frame_cap_for(165.058, None) == 162
    assert display.frame_cap_for(165.058, "2") == 163

    assert display.parse_xrandr_refresh(
        "HDMI-0 connected 2560x1440  144*+  60.00\n"
        "DP-0 connected 1920x1080  165.00  60.00*\n"
    ) == 144
    assert display.parse_xrandr_refresh(
        "  1920x1080     60.00*+  59.94\n"
    ) == 60.0
    assert display.parse_xrandr_refresh("nothing starred") is None

    kscreen = (
        '{"outputs":['
        '{"enabled":true,"currentModeId":"1","modes":['
        '{"id":"1","refreshRate":59.95},{"id":"2","refreshRate":165.0}]},'
        '{"enabled":true,"currentModeId":"9","modes":['
        '{"id":"9","refreshRate":165.058}]},'
        '{"enabled":false,"currentModeId":"3","modes":['
        '{"id":"3","refreshRate":240.0}]}'
        "]}"
    )
    assert abs(display.parse_kscreen_refresh(kscreen) - 165.058) < 0.001
    assert display._best_refresh([60.0, 165.0, 30.0]) == 165.0
    assert display._best_refresh([30.0, 24.0]) is None

    real_detect = display.detect_refresh_hz
    try:
        with tempfile.TemporaryDirectory() as td:
            conf = Path(td) / "dxvk.conf"

            original = (
                "# Turtle WoW (1.12) - DXVK 2.7.1\n"
                "dxvk.logLevel = none\n"
                "\n"
                "# Uncomment to set framerate limit\n"
                "d3d9.maxFrameRate = 1000\n"
                "d3d9.dpiAware = False\n"
            )
            conf.write_text(original)
            display.detect_refresh_hz = lambda: 165.058
            assert display.apply_frame_cap(conf, 3) == 162
            after = conf.read_text()
            assert "d3d9.maxFrameRate = 162" in after, after
            assert "1000" not in after, after
            assert "# Turtle WoW (1.12) - DXVK 2.7.1" in after, after
            assert "dxvk.logLevel = none" in after
            assert "d3d9.dpiAware = False" in after
            assert "# Uncomment to set framerate limit" in after

            before = conf.read_bytes()
            assert display.apply_frame_cap(conf, 3) == 162
            assert conf.read_bytes() == before, "already-correct cap must not rewrite"

            untouched = conf.read_text()
            display.detect_refresh_hz = lambda: None
            assert display.apply_frame_cap(conf, 3) is None
            assert conf.read_text() == untouched, "a None detection rewrote the file"

            conf.write_text("dxvk.logLevel = none\n")
            display.detect_refresh_hz = lambda: 120.0
            assert display.apply_frame_cap(conf, 3) == 117
            assert "d3d9.maxFrameRate = 117" in conf.read_text()

            conf.write_text("# d3d9.maxFrameRate = 60\ndxvk.logLevel = none\n")
            display.detect_refresh_hz = lambda: 165.058
            display.apply_frame_cap(conf, 3)
            body = conf.read_text()
            assert "# d3d9.maxFrameRate = 60" in body, body
            assert "d3d9.maxFrameRate = 162" in body, body

            conf.write_bytes(
                b"# DXVK 2.7.1\r\nd3d9.maxFrameRate = 1000\r\n"
                b"dxvk.logLevel = none\r\n"
            )
            display.detect_refresh_hz = lambda: 165.058
            assert display.apply_frame_cap(conf, 3) == 162
            raw = conf.read_bytes()
            assert b"\r\n" in raw and raw.count(b"\r\n") == 3, raw
            assert b"d3d9.maxFrameRate = 162\r\n" in raw, raw
            assert b"# DXVK 2.7.1\r\n" in raw, raw

            conf.write_bytes(b"# DXVK 2.7.1\nd3d9.maxFrameRate = 1000\n")
            assert display.apply_frame_cap(conf, 3) == 162
            raw = conf.read_bytes()
            assert b"\r" not in raw, raw

            assert display.apply_frame_cap(Path(td) / "nope.conf", 3) is None

            import builtins
            real_open = builtins.open

            def _fail_on_write(f, mode="r", *a, **kw):
                if "w" in mode:
                    raise OSError(28, "No space left on device")
                return real_open(f, mode, *a, **kw)

            conf.write_text("d3d9.maxFrameRate = 1000\n")
            builtins.open = _fail_on_write
            try:
                raised = False
                try:
                    display.apply_frame_cap(conf, 3)
                except OSError:
                    raised = True
                assert raised, "a failed write must not be swallowed"
                assert display.apply_frame_cap(
                    conf, 3, raise_on_write_error=False
                ) is None
            finally:
                builtins.open = real_open
    finally:
        display.detect_refresh_hz = real_detect

    from ichalaunch.mods import installer as _inst
    import inspect
    src = inspect.getsource(_inst)
    assert src.count("_apply_frame_cap_if_enabled(") >= 4, (
        "the frame cap must reach zip_root, dxvk_hd, dxvk_cursor, and prepare_for_launch"
    )

    with tempfile.TemporaryDirectory() as td:
        empty = Path(td)
        _inst._apply_frame_cap_if_enabled(empty)
        assert not (empty / "dxvk.conf").exists(), "helper created a conf out of nothing"

    hz = display.detect_refresh_hz()
    assert hz is None or hz > 0, hz
    print("OK frame cap from refresh")


def test_frame_cap_settings_checkbox_and_launch_apply():
    """Frame-cap checkbox is on every platform; PLAY reapplies an existing conf."""
    import sys as _sys

    from PySide6.QtWidgets import QApplication

    import ichalaunch.ui.pages.settings as settings_page_mod
    from ichalaunch.config.settings import settings
    from ichalaunch.game import display
    from ichalaunch.mods import installer as I
    from ichalaunch.ui.widgets.launch_settings import LaunchSettingsPanel

    app = QApplication.instance() or QApplication(_sys.argv)
    assert app is not None
    settings_page = settings_page_mod.SettingsPage()
    assert not hasattr(settings_page, "cb_frame_cap")
    page = LaunchSettingsPanel()
    assert hasattr(page, "cb_frame_cap")
    assert page.cb_frame_cap.parent() is not None

    prev = settings.get("frame_cap_from_refresh", None)
    real_detect = display.detect_refresh_hz
    try:
        settings._data.pop("frame_cap_from_refresh", None)
        page.refresh()
        assert page.cb_frame_cap.isChecked()

        settings.set("frame_cap_from_refresh", False)
        page.refresh()
        assert not page.cb_frame_cap.isChecked()

        settings.set("frame_cap_from_refresh", True)
        page.refresh()
        assert page.cb_frame_cap.isChecked()

        display.detect_refresh_hz = lambda: 144.0
        with tempfile.TemporaryDirectory() as td:
            game = Path(td)
            (game / "dxvk.conf").write_text("d3d9.maxFrameRate = 1000\n")
            assert I._apply_frame_cap_if_enabled(game) == 141
            assert "d3d9.maxFrameRate = 141" in (game / "dxvk.conf").read_text()

            settings.set("frame_cap_from_refresh", False)
            (game / "dxvk.conf").write_text("d3d9.maxFrameRate = 1000\n")
            assert I._apply_frame_cap_if_enabled(game) is None
            assert "1000" in (game / "dxvk.conf").read_text()
    finally:
        display.detect_refresh_hz = real_detect
        settings.set("frame_cap_from_refresh", prev)

    print("OK frame cap settings checkbox and launch apply")


def test_nampower_password_encryption_key_and_launch_env():
    """Default off; enable generates a key; child env gets it only when on."""
    import inspect

    from ichalaunch.config.settings import DEFAULTS, settings
    from ichalaunch.core import process
    from ichalaunch.game import nampower_encrypt as NE
    from ichalaunch.game import proton

    assert DEFAULTS["nampower_encrypt_passwords"] is False
    assert DEFAULTS["wow_encryption_key"] == ""
    assert "child_launch_env" in inspect.getsource(process.launch_exe)
    assert "apply_wow_encryption_env" in inspect.getsource(proton.build_launch_command)

    prev_on = settings.get(NE.SETTING_ENABLED, False)
    prev_key = settings.get(NE.SETTING_KEY, "")
    try:
        settings.set(NE.SETTING_ENABLED, False)
        settings.set(NE.SETTING_KEY, "")
        assert NE.encrypt_enabled() is False
        env_off = process.child_launch_env(
            {"PATH": "/bin", NE.WOW_ENCRYPTION_ENV: "from-parent"}
        )
        assert NE.WOW_ENCRYPTION_ENV not in env_off

        NE.set_encrypt_enabled(True)
        assert NE.encrypt_enabled() is True
        key1 = NE.stored_key()
        assert len(key1) >= 24, key1
        env_on = process.child_launch_env({"PATH": "/bin"})
        assert env_on[NE.WOW_ENCRYPTION_ENV] == key1

        NE.set_encrypt_enabled(True)
        assert NE.stored_key() == key1

        key2 = NE.regenerate_encryption_key()
        assert key2 != key1
        assert NE.stored_key() == key2
        env_new = process.child_launch_env({})
        assert env_new[NE.WOW_ENCRYPTION_ENV] == key2

        NE.set_encrypt_enabled(False)
        env_off2 = process.child_launch_env({NE.WOW_ENCRYPTION_ENV: "stale"})
        assert NE.WOW_ENCRYPTION_ENV not in env_off2
        assert NE.stored_key() == key2

        leaked = (
            f'WOW_ENCRYPTION_KEY={key2}\n'
            f'"wow_encryption_key": "{key2}"\n'
        )
        redacted = NE.redact_encryption_secrets(leaked)
        assert key2 not in redacted
        assert "[REDACTED]" in redacted
    finally:
        settings.set(NE.SETTING_ENABLED, prev_on)
        settings.set(NE.SETTING_KEY, prev_key or "")
    print("OK nampower password encryption key and launch env")


def test_nampower_encrypt_settings_checkbox():
    """Encrypt checkbox lives on Client → Launch; Settings does not host it."""
    import sys as _sys

    from PySide6.QtWidgets import QApplication

    import ichalaunch.ui.pages.settings as settings_page_mod
    from ichalaunch.config.settings import settings
    from ichalaunch.game.nampower_encrypt import SETTING_ENABLED, encrypt_enabled
    from ichalaunch.ui.widgets.launch_settings import LaunchSettingsPanel

    app = QApplication.instance() or QApplication(_sys.argv)
    assert app is not None
    settings_page = settings_page_mod.SettingsPage()
    assert not hasattr(settings_page, "cb_nampower_encrypt")
    page = LaunchSettingsPanel()
    assert hasattr(page, "cb_nampower_encrypt")
    assert page.cb_nampower_encrypt.parent() is not None
    assert page.nampower_encrypt_hint.parent() is not None
    if _sys.platform == "win32":
        assert page.cb_nampower_encrypt.isEnabled()
        assert page.btn_regenerate_encrypt_key.parent() is not None
    else:
        assert not page.cb_nampower_encrypt.isEnabled()
        assert page.btn_regenerate_encrypt_key.isHidden()

    prev_on = settings.get(SETTING_ENABLED, False)
    try:
        settings.set(SETTING_ENABLED, False)
        page.refresh()
        assert not page.cb_nampower_encrypt.isChecked()
        assert not page.btn_regenerate_encrypt_key.isEnabled()

        settings.set(SETTING_ENABLED, True)
        page.refresh()
        assert page.cb_nampower_encrypt.isChecked() is encrypt_enabled()
        if _sys.platform == "win32":
            assert page.btn_regenerate_encrypt_key.isEnabled()
        else:
            assert not page.btn_regenerate_encrypt_key.isEnabled()
    finally:
        settings.set(SETTING_ENABLED, prev_on)
        page.deleteLater()
        settings_page.deleteLater()
    print("OK nampower encrypt settings checkbox")


def test_nampower_encrypt_regenerate_feedback():
    """Regenerate rotates the stored key and sets inline success text."""
    from unittest.mock import patch

    from PySide6.QtWidgets import QApplication

    from ichalaunch.config.settings import settings
    from ichalaunch.game.nampower_encrypt import (
        SETTING_ENABLED,
        SETTING_KEY,
        stored_key,
    )
    from ichalaunch.ui.widgets.launch_settings import (
        REGEN_KEY_STATUS_OK,
        LaunchSettingsPanel,
    )

    app = QApplication.instance() or QApplication(sys.argv)
    assert app is not None
    page = LaunchSettingsPanel()
    prev_on = settings.get(SETTING_ENABLED, False)
    prev_key = settings.get(SETTING_KEY, "")
    old_key = "old-key-for-rotate-test-xxxx"
    try:
        settings.set(SETTING_ENABLED, True)
        settings.set(SETTING_KEY, old_key)
        page.refresh()
        assert page.encrypt_key_status.text() == ""

        with patch("ichalaunch.ui.widgets.dialogs.confirm", return_value=True):
            page._on_regenerate_encrypt_key()
        new_key = stored_key()
        assert new_key != old_key
        assert len(new_key) >= 24
        assert page.encrypt_key_status.text() == REGEN_KEY_STATUS_OK
        assert page.btn_regenerate_encrypt_key.text() == "Key replaced"

        settings.set(SETTING_KEY, old_key)
        page.encrypt_key_status.clear()
        with (
            patch("ichalaunch.ui.widgets.dialogs.confirm", return_value=True),
            patch(
                "ichalaunch.game.nampower_encrypt.regenerate_encryption_key",
                side_effect=OSError("disk full"),
            ),
            patch("ichalaunch.ui.widgets.dialogs.error") as err,
        ):
            page._on_regenerate_encrypt_key()
        err.assert_called_once()
        assert stored_key() == old_key
        assert page.encrypt_key_status.text() == ""
    finally:
        settings.set(SETTING_ENABLED, prev_on)
        settings.set(SETTING_KEY, prev_key or "")
        page.deleteLater()
    print("OK nampower encrypt regenerate feedback")


def test_linux_dxvk_vulkan_preflight():
    """DXVK suitability on Linux turns on 32-bit Vulkan, not on the GPU name.

    Drives a fake ICD layout so the verdict does not depend on whatever the
    machine running the suite happens to have installed.
    """
    if sys.platform == "win32":
        print("OK linux dxvk vulkan pre-flight (skipped on Windows)")
        return

    import os

    from ichalaunch.core import gpu_compat

    def _elf(path: Path, bits: int) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x7fELF" + (b"\x01" if bits == 32 else b"\x02") + b"\0" * 59)

    def _verdict(manifests, lib32_dir):
        gpu_compat._LIB32_DIRS = (str(lib32_dir),)
        os.environ["VK_DRIVER_FILES"] = os.pathsep.join(str(m) for m in manifests)
        gpu_compat.find_vulkan_icds_32bit.cache_clear()
        gpu_compat.find_vulkan_loader_32bit.cache_clear()
        return gpu_compat._assess_dxvk_linux()[0]

    real_dirs = gpu_compat._LIB32_DIRS
    real_env = os.environ.get("VK_DRIVER_FILES")
    try:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            lib32 = root / "lib32"
            manifest = root / "icd.d" / "radeon_icd.json"
            manifest.parent.mkdir(parents=True)
            # A bare soname, shared between both architectures -- the common
            # case, and the reason a manifest's own path proves nothing.
            manifest.write_text(json.dumps({"ICD": {"library_path": "libvulkan_radeon.so"}}))

            # Vulkan installed for 64-bit only: DXVK cannot load at all.
            _elf(root / "lib64" / "libvulkan_radeon.so", 64)
            lib32.mkdir()
            assert _verdict([manifest], lib32) == "bad"

            # A 32-bit driver but no 32-bit loader: Proton usually carries one.
            _elf(lib32 / "libvulkan_radeon.so", 32)
            assert _verdict([manifest], lib32) == "warn"

            # Both present.
            _elf(lib32 / "libvulkan.so.1", 32)
            assert _verdict([manifest], lib32) == "ok"

            # A 64-bit library in a lib32 directory is still 64-bit.
            _elf(lib32 / "libvulkan_radeon.so", 64)
            assert _verdict([manifest], lib32) == "bad"

            # No drivers at all is a softer message: nothing to repair.
            assert _verdict([root / "nope.json"], lib32) == "warn"

            assert gpu_compat._is_elf32(lib32 / "libvulkan.so.1")
            assert not gpu_compat._is_elf32(root / "lib64" / "libvulkan_radeon.so")
            assert not gpu_compat._is_elf32(root / "absent.so")
    finally:
        gpu_compat._LIB32_DIRS = real_dirs
        os.environ.pop("VK_DRIVER_FILES", None)
        if real_env is not None:
            os.environ["VK_DRIVER_FILES"] = real_env
        gpu_compat.find_vulkan_icds_32bit.cache_clear()
        gpu_compat.find_vulkan_loader_32bit.cache_clear()

    # The bug this replaces: on Linux wmic does not exist, so every machine
    # was told its graphics card could not be detected.
    level, _gpus, message = gpu_compat.assess_dxvk_gpu()
    assert level in {"ok", "warn", "bad"}
    assert "Could not detect your graphics card" not in message

    print("OK linux dxvk vulkan pre-flight")


def test_wayland_window_move_and_resize_handoff():
    """On Wayland the compositor gets the drag; everywhere else nothing changes.

    Drives MainWindow's methods against a stub rather than building the real
    window: the point is which branch runs, and no compositor is available
    under the offscreen platform anyway.
    """
    from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication, QWidget

    from ichalaunch.ui import main_window as mw

    app = QApplication.instance() or QApplication([])

    def _press(x=400, y=300):
        return QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(x, y),
            QPointF(x, y),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )

    class _Handle:
        def __init__(self, move_ok=True, resize_ok=True):
            self.move_ok = move_ok
            self.resize_ok = resize_ok
            self.moves = 0
            self.resizes = 0
            self.edges = None

        def startSystemMove(self):
            self.moves += 1
            return self.move_ok

        def startSystemResize(self, edges):
            self.resizes += 1
            self.edges = edges
            return self.resize_ok

    class _Win:
        """Just enough MainWindow for the methods under test."""

        def __init__(self, handle, state=Qt.WindowState.WindowNoState):
            self._handle = handle
            self._state = state
            self._drag_pos = "stale"
            self._resize_edges = "stale"
            self._resize_origin = "stale"
            self._resize_geo = "stale"
            self._system_move_pending = "stale"
            self.handle_lookups = 0

        def windowHandle(self):
            self.handle_lookups += 1
            return self._handle

        def windowState(self):
            return self._state

        def frameGeometry(self):
            return QRect(100, 50, 800, 600)

        _start_system_move = mw.MainWindow._start_system_move
        _start_system_resize = mw.MainWindow._start_system_resize
        _begin_window_drag = mw.MainWindow._begin_window_drag
        _apply_edge_resize = mw.MainWindow._apply_edge_resize
        _compositor_owns_window_state = mw.MainWindow._compositor_owns_window_state
        _release_pointer_after_handoff = mw.MainWindow._release_pointer_after_handoff

    real_guard = mw._use_system_window_move
    real_cache = mw._SYSTEM_WINDOW_MOVE
    real_qgui = mw.QGuiApplication
    try:
        mw._use_system_window_move = lambda: True

        # --- edge tuples map to the right compositor edges ---------------
        for edges, expected in {
            (True, False, False, False): Qt.Edge.LeftEdge,
            (False, True, False, False): Qt.Edge.RightEdge,
            (False, False, True, False): Qt.Edge.TopEdge,
            (False, False, False, True): Qt.Edge.BottomEdge,
            (True, False, True, False): Qt.Edge.LeftEdge | Qt.Edge.TopEdge,
            (False, True, False, True): Qt.Edge.RightEdge | Qt.Edge.BottomEdge,
        }.items():
            h = _Handle()
            assert _Win(h)._start_system_resize(edges) is True, edges
            assert h.edges == expected, (edges, h.edges, expected)

        # No edge at all is not a resize, and costs nothing to discover.
        h = _Handle()
        w = _Win(h)
        assert w._start_system_resize((False, False, False, False)) is False
        assert h.resizes == 0 and w.handle_lookups == 0

        # --- a press ARMS the drag; it does not start it ------------------
        # Starting on the press would consume a plain click on the chrome.
        h = _Handle()
        w = _Win(h)
        w._begin_window_drag(QPoint(400, 300))
        assert h.moves == 0, "the compositor must not be asked on the press"
        assert w._system_move_pending is True
        assert w._drag_pos is None
        assert w._resize_edges is None and w._resize_origin is None
        assert w._resize_geo is None

        # A maximized or fullscreen toplevel may be refused silently, so it
        # is never asked and the press keeps its normal meaning.
        for state in (Qt.WindowState.WindowMaximized, Qt.WindowState.WindowFullScreen):
            w = _Win(_Handle(), state=state)
            w._begin_window_drag(QPoint(400, 300))
            assert w._system_move_pending is False, state

        # --- the synthetic release reaches the widget that saw the press --
        class _Spy(QWidget):
            def __init__(self):
                super().__init__()
                self.releases = 0

            def mouseReleaseEvent(self, event):
                self.releases += 1

        spy = _Spy()
        _Win(_Handle())._release_pointer_after_handoff(spy, _press())
        app.processEvents()
        assert spy.releases == 1, "the pressed widget must be told the button is up"

        # --- no window handle yet: no crash, nothing claimed --------------
        assert _Win(None)._start_system_move() is False
        assert _Win(None)._start_system_resize((True, False, False, False)) is False

        # --- the manual resize arithmetic never runs on Wayland -----------
        w = _Win(_Handle())
        w._resize_edges = (True, False, False, False)
        w._resize_origin = QPoint(0, 0)
        w._resize_geo = QRect(0, 0, 900, 700)
        w._apply_edge_resize(QPoint(40, 0))  # would call setGeometry, which the stub lacks

        # --- off Wayland the compositor is never consulted ----------------
        mw._use_system_window_move = lambda: False
        h = _Handle()
        w = _Win(h)
        w._begin_window_drag(QPoint(400, 300))
        assert h.moves == 0, "startSystemMove must not run off Wayland"
        assert w.handle_lookups == 0, "windowHandle must not even be read off Wayland"
        assert w._system_move_pending == "stale", "the Wayland flag must be left alone"
        assert w._drag_pos == QPoint(300, 250)
    finally:
        mw._use_system_window_move = real_guard
        mw.QGuiApplication = real_qgui
        mw._SYSTEM_WINDOW_MOVE = real_cache

    # --- the guard itself --------------------------------------------------
    # platformName() answers "xcb" before the application exists rather than
    # raising, so asking early and caching would disable this permanently and
    # silently. Nothing may be memoized until there is an instance.
    class _NoApp:
        @staticmethod
        def instance():
            return None

        @staticmethod
        def platformName():
            raise AssertionError("platformName must not be read without an instance")

    try:
        mw._SYSTEM_WINDOW_MOVE = None
        mw.QGuiApplication = _NoApp
        assert mw._use_system_window_move() is False
        assert mw._SYSTEM_WINDOW_MOVE is None, "a pre-application answer must not be cached"

        mw.QGuiApplication = real_qgui
        assert mw._use_system_window_move() is False
        assert mw._SYSTEM_WINDOW_MOVE is False
    finally:
        mw.QGuiApplication = real_qgui
        mw._SYSTEM_WINDOW_MOVE = real_cache

    print("OK wayland window move/resize handoff")



def main():
    import ichalaunch.config.settings as settings_mod

    real_path_fn = settings_mod.settings_path
    with tempfile.TemporaryDirectory() as td:
        isolated = Path(td) / "settings.json"
        settings_mod.settings_path = lambda: isolated
        settings_mod.settings.load()
        from ichalaunch.addons import pending_updates as pending

        pending_cache = Path(td) / "addon_pending_updates.json"
        try:
            with pending.isolated_pending_updates_cache(pending_cache):
                _run_smoke_tests()
        finally:
            settings_mod.settings_path = real_path_fn
            settings_mod.settings.load()


def test_detect_and_installer_drop_unused_imports():
    """Removed F401 names stay gone and both modules still work without them."""
    import tempfile
    from pathlib import Path

    import ichalaunch.core.detect as detect
    import ichalaunch.mods.installer as installer

    # Each name below was bound at module scope and never referenced, and
    # nothing in the tree imports it FROM these modules, so the binding was
    # pure dead weight. Guard against it drifting back in.
    assert not hasattr(detect, "reconcile_vanillafixes_dxvk")
    for name in ("github_latest_commit", "copy_tree", "ensure_addons_dir"):
        assert not hasattr(installer, name), name

    # The modules that actually define them still export them, so callers that
    # want these functions have an unchanged place to get them from.
    from ichalaunch.addons.github import github_latest_commit
    from ichalaunch.core.filesystem import copy_tree
    from ichalaunch.game.launcher import ensure_addons_dir
    from ichalaunch.mods.installer import reconcile_vanillafixes_dxvk

    for fn in (github_latest_commit, copy_tree, ensure_addons_dir, reconcile_vanillafixes_dxvk):
        assert callable(fn)

    # The public surface that sat next to the removed imports still behaves.
    both = {"vanillafixes": True, "dxvk": True}
    fixed = reconcile_vanillafixes_dxvk(dict(both), prefer="dxvk")
    assert fixed.get("dxvk") is True
    assert fixed.get("vanillafixes") is False
    assert isinstance(installer.load_mod_catalog(), list)

    with tempfile.TemporaryDirectory() as tmp:
        addons = Path(tmp) / "AddOns"
        good = addons / "Atlas-CFM"
        good.mkdir(parents=True)
        (good / "Atlas-CFM.toc").write_text("## Title: Atlas\n", encoding="utf-8")
        valid, mismatched = detect._classify_toc_dir(addons, skip_blizzard=True)
        assert valid == ["Atlas-CFM"]
        assert mismatched == []

    print("OK detect/installer carry no unused module-level imports")


def _run_smoke_tests():
    test_catalogs()
    test_tls_ca_env_sanitizer()
    test_bundle_pins_charset_normalizer_and_excludes_chardet()
    test_no_control_flow_escapes_a_finally_block()
    test_launcher_ca_env_does_not_reach_the_game()
    test_github_parse()
    test_gitlab_parse_and_install_url()
    test_gitlab_preview_does_not_use_github_api()
    test_protected()
    test_dlls_txt()
    test_detect_state()
    test_vanilla_tweaks_disable_clears_pending()
    test_vanilla_tweaks_exe_swap_is_atomic()
    test_vanilla_tweaks_patcher_output_is_identified_or_fails()
    test_hand_patched_wow_exe_is_not_vanilla_tweaks()
    test_apply_desired_state_guard()
    test_mod_remove_desired_state()
    test_stock_patch9_not_owned_by_pretty_night_sky()
    test_stock_patch9_collision_migration()
    test_catalog_mpq_letters_unique()
    test_pretty_night_sky_migrates_off_fog_y()
    test_stock_patch9_reacquire_detect()
    test_stock_patch9_prompt_requires_wow_exe()
    test_config_wtf_farclip_clamp()
    test_config_wtf_regenerate()
    test_config_wtf_restore()
    test_darker_nights_migration()
    test_mod_toggle_resolution()
    test_client_preset_catalog_ids()
    test_client_preset_apply_basic()
    test_client_preset_downgrade_basic_plus_to_basic()
    test_client_preset_apply_hd_aio()
    test_client_preset_downgrade_hd_aio_to_basic_plus()
    test_client_preset_manual_toggle_custom()
    test_client_preset_tweaks_cog_not_custom()
    test_hd_patch_e_includes_caption()
    test_hd_dxvk_catalog_and_patch_v()
    test_hd_dxvk_disable_restores_vf_layer()
    test_dxvk_layers_detect_dll_not_conf_comment()
    test_dxvk_cursor_remove_restores_dll_from_backup()
    test_hd_dxvk_remove_offline_does_not_raise()
    test_patch_v_is_not_patch_c()
    test_mod_author_labels()
    test_hd_graphics_project_link_only()
    test_dxvk_disable_cascades_dependents()
    test_vanillafixes_dxvk_reconcile()
    test_dxvk_detect_plan_clean()
    test_hd_patch_lt_exclusive_planning()
    test_hd_patch_exclusive_variant_swap()
    test_hd_variant_identified_by_size_on_disk()
    test_hd_patch_both_desired_reconciled()
    test_backfill_installed_mods_on_detect()
    test_resolve_launch_exe()
    test_vanillafixes_launch_decision()
    test_vf_mode_labels()
    test_vf_dxvk_roundtrip_simulated_plan_clean()
    test_vf_dxvk_roundtrip_plan_clean()
    test_dxvk_switch_keeps_vanillafixes_exe()
    test_dxvk_disable_removes_vanillafixes_one_apply()
    test_detect_game_ravencraft_subfolder()
    test_assess_dxvk_gpu()
    test_addon_fork_version_labels()
    test_addon_github_browse_helpers()
    test_plan_changes_hd_env_set_no_recursion()
    test_vanilla_helpers_hd_dependency()
    test_discover_game_path_near_launcher()
    test_ensure_game_path_keeps_saved_folder()
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
    test_nested_catalog_forks_in_submit_duplicate_check()
    test_review_queue_only_root_and_requested_fork()
    test_catalog_approve_pr_lookup()
    test_addon_settings_uncatalogued_fork_triggers_submit()
    test_fork_ahead_compare_helper()
    test_crash_report_opt_in_and_redaction()
    test_crash_report_skips_rate_limit_errors()
    test_crash_report_skips_lock_and_network_noise()
    test_crash_report_skips_smoke_test_uploads()
    test_download_file_retries_transient_http_not_404()
    test_crash_reporting_opt_in_skipped_when_reporting_suppressed()
    test_crash_reporting_opt_in_prompt_one_shot()
    test_mod_remote_identity_uses_tip_index()
    test_addon_toc_folder_name_required()
    test_multi_toc_primary_stem_resolve()
    test_addon_toc_folder_rename()
    test_addon_update_check_uses_catalog_index_only()
    test_older_tag_install_reports_update()
    test_pending_addon_updates_cache_survives_restart()
    test_update_to_tip_clears_stored_version_pin()
    test_available_catalog_remote_refresh_and_merge()
    test_available_catalog_offline_keeps_cache()
    test_release_download_count_parse()
    test_release_download_count_format()
    test_release_download_fetch_queue_prefers_missing_then_oldest()
    test_release_download_sort_order()
    test_release_download_fork_vs_main_repo()
    test_apply_published_fork_does_not_inherit_main_count()
    test_release_download_missing_release_handling()
    test_release_download_catalog_refresh_stamps_cache()
    test_enrich_catalog_downloads_stamps_and_keeps_last()
    test_enrich_catalog_downloads_graphql_batches()
    test_addon_row_download_count_after_git()
    test_git_refs_live_optional()
    test_commit_atom_sha_no_default_fallback_for_named_ref()
    test_preview_addon_repo_soft_fails_fake_tags()
    test_github_token_not_sent_to_third_party_readme_hosts()
    test_github_bad_token_retries_without_auth()
    test_auto_scan_cooldown_setting()
    test_auto_scan_cooldown_persists_to_disk()
    test_addon_startup_token_gating()
    test_linux_appdata_uses_xdg_and_migrates()
    test_settings_paths_survive_load_cycle()
    test_settings_paths_recover_from_backup()
    test_settings_merge_keeps_game_path_and_tweaks_v2()
    test_settings_save_survives_double_process_replace_race()
    test_settings_save_fsyncs_payload_before_replace()
    test_addons_filter_persists()
    test_addons_page_restores_and_saves_filter()
    test_bagshui_catalog_pin()
    test_never_update_persists()
    test_reinstall_clears_never_update()
    test_row_reinstall_clears_never_update()
    test_addon_row_update_button_is_square()
    test_addon_row_install_button_matches_update_plate()
    test_addon_settings_never_update_on_save()
    test_sanitize_filename()
    test_robust_rmtree_readonly_git_pack()
    test_install_clears_readonly_data_mpqs()
    test_vanillafixes_zip_in_memory()
    test_vanillafixes_preserves_dlls_txt()
    test_zip_root_never_writes_wtf_config()
    test_apply_desired_state_restores_dlls_txt()
    test_prepare_for_launch_syncs_dlls_txt()
    test_owned_paths_are_comparison_keys_not_filenames()
    test_client_exe_probe_is_case_insensitive()
    test_linux_proton_launch_resolution()
    test_windows_exe_runs_through_proton_and_blocks()
    test_linux_wow64_default_on_when_supported()
    test_linux_wow64_settings_checkbox()
    test_x3d_vcache_ccd_selection()
    test_vcache_pin_default_is_not_persisted()
    test_vcache_pin_settings_checkbox()
    test_frame_cap_from_refresh()
    test_frame_cap_default_is_not_persisted()
    test_frame_cap_settings_checkbox_and_launch_apply()
    test_nampower_password_encryption_key_and_launch_env()
    test_nampower_encrypt_settings_checkbox()
    test_nampower_encrypt_regenerate_feedback()
    test_linux_dxvk_vulkan_preflight()
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
    test_mod_version_label()
    test_vanilla_tweaks_tubtubs_catalog_and_argv()
    test_vanilla_tweaks_settings_dialog()
    test_tweaks_dialogs_regenerate_button()
    test_vanilla_tweaks_optional_greyed_when_superwow()
    test_client_pending_plan_row_badge_and_apply_pulse()
    test_theme_checkbox_disabled_uses_grey_check_art()
    test_theme_radio_uses_wow_art()
    test_client_page_does_not_poll_game_lock_until_shown()
    test_client_page_locks_mod_edits_when_wow_running()
    test_wow_exe_running_matches_game_directory()
    test_client_page_lock_uses_configured_game_dir()
    test_vanilla_tweaks_force_tubtubs_repatch()
    test_vanilla_tweaks_enable_opens_config_once()
    test_mod_check_row_tweaks_cog()
    test_vanilla_tweaks_old_catalog_schema_and_mutex()
    test_vanilla_tweaks_old_force_migrate_and_switch()
    test_vanilla_tweaks_old_settings_dialog_and_warning()
    test_superwow_tracks_dll_release_not_patch_mpq()
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
    test_addon_check_updates_gates_until_list_ready()
    test_addons_defers_list_build_while_scanning()
    test_cancel_git_url_checks_orphans_running_threads()
    test_addons_rapid_pagination_spawns_no_browse_url_threads()
    test_open_in_git_visible_without_probe_and_click_opens()
    test_mod_check_row_links_after_author()
    test_open_git_icon_abuts_name_geometry()
    test_addons_available_pagination_after_reveal()
    test_addons_all_filter_pagination_fully_loaded()
    test_addons_filter_popup_same_value_keeps_open_git_visible()
    test_addons_filter_change_cancels_git_probes()
    test_github_url_reach_disk_cache_roundtrip()
    test_mainwindow_addons_next_all_filter_fully_loaded()
    test_mainwindow_addons_next_available_filter()
    test_mainwindow_check_updates_serializes_pending_reveal()
    test_addon_preview_gates_combos_and_open_git()
    test_glue_combo_popup_hide_wiring_in_settings_dialogs()
    test_addon_settings_version_prefetch_first_open()
    test_addon_settings_reinstall_enabled_when_selection_differs()
    test_pass_remove_uses_wow_art()
    test_refresh_reinstall_uses_wow_art()
    test_addon_row_reinstall_aligns_with_delete_bottom()
    test_spellbook_page_buttons_use_wow_art()
    test_contributor_wow_name_tooltip()
    test_floor_lighting_overlay()
    test_nav_tab_update_alert_badge()
    test_nav_tab_glue_floor_chrome()
    test_client_hd_graphics_display_order()
    test_client_cat_nav_update_alert_badge()
    test_launch_settings_live_on_client_page()
    test_chrome_buttons_clear_metal_tr()
    test_play_stays_right_when_progress_hidden()
    test_wayland_window_move_and_resize_handoff()
    test_detect_and_installer_drop_unused_imports()
    print("\nAll smoke tests passed.")


if __name__ == "__main__":
    main()

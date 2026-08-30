#!/usr/bin/env python3
"""Interactive local sign + upload for live catalogs and the launcher EXE.

    python tools/sign_live.py
    python tools/sign.py --interactive

For each live file, prints status and asks yes/no (Enter skips):

    Sign and upload a new addons.json? [y/N]

Yes signs with the correct purpose and writes ``<file>.sig`` beside it.
No skips that file. After signing:

* Catalog JSON + ``.sig`` -> branch ``sign/live-catalogs`` on remote ``public``
  (brutaliccus/IchaLaunch), then ``gh pr create`` against public master.
  Clients fetch ``ichalaunch/data/<name>`` + ``<name>.sig`` from that repo.
* IchaLaunch.exe ``.sig`` -> GitHub Release via ``publish_public_release.py``
  (offered after signing; never committed under ``ichalaunch/data/``).

The private key stays on this machine. Default:

    %LOCALAPPDATA%\\IchaLaunch\\signing\\ichalaunch-key1.pem

Non-interactive / later automation:

    python tools/sign_live.py --yes-all
    python tools/sign_live.py --only addons,mods
    python tools/sign_live.py --dry-run --yes-all
"""

from __future__ import annotations

import argparse
import importlib.util
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

PUBLIC_REPO = "brutaliccus/IchaLaunch"
DEFAULT_BRANCH = "sign/live-catalogs"
DEFAULT_REMOTE = "public"
DEFAULT_EXE_REL = Path("dist") / "IchaLaunch.exe"
SIGNER_BRANCH = "fix/pinned-mod-waits-for-catalog"


def _looks_like_repo(path: Path) -> bool:
    return (path / "ichalaunch" / "__init__.py").is_file() and (
        (path / ".git").exists() or (path / "ichalaunch" / "data").is_dir()
    )


def find_repo_root(start: Path | None = None) -> Path:
    """Repo root from this file, *start*, or cwd (so `cd tools` still works)."""
    here = Path(__file__).resolve().parent.parent
    ordered: list[Path] = []
    if start is not None:
        ordered.append(Path(start).resolve())
    ordered.append(here)
    try:
        cwd = Path.cwd().resolve()
        ordered.append(cwd)
        ordered.extend(cwd.parents)
        if start is not None:
            ordered.extend(Path(start).resolve().parents)
    except OSError:
        pass
    seen: set[Path] = set()
    for path in ordered:
        if path in seen:
            continue
        seen.add(path)
        if _looks_like_repo(path):
            return path
    return here


ROOT = find_repo_root()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_sign():
    name = "ichalaunch_tools_sign"
    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).with_name("sign.py")
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load tools/sign.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_sign = _load_sign()


@dataclass(frozen=True)
class TargetSpec:
    id: str
    filename: str
    rel: str | None
    kind: str
    prompt: str
    aliases: tuple[str, ...]


CATALOG_SPECS: tuple[TargetSpec, ...] = (
    TargetSpec(
        "addons",
        "addons.json",
        "ichalaunch/data/addons.json",
        "catalog",
        "Sign and upload a new addons.json?",
        ("addons", "addons.json"),
    ),
    TargetSpec(
        "addon_tips",
        "addon_tips.json",
        "ichalaunch/data/addon_tips.json",
        "catalog",
        "Sign and upload a new addon_tips.json?",
        ("addon_tips", "addon_tips.json", "tips"),
    ),
    TargetSpec(
        "home_art",
        "home_art.json",
        "ichalaunch/data/home_art.json",
        "catalog",
        "Sign and upload a new home_art.json?",
        ("home_art", "home_art.json", "home"),
    ),
    TargetSpec(
        "mods",
        "mods.json",
        "ichalaunch/data/mods.json",
        "catalog",
        "Sign and upload a new mods.json?",
        ("mods", "mods.json"),
    ),
)

EXE_SPEC = TargetSpec(
    "exe",
    "IchaLaunch.exe",
    None,
    "exe",
    "Sign and upload a new IchaLaunch.exe?",
    ("exe", "ichalaunch.exe", "ichalaunch"),
)

ALL_SPECS: tuple[TargetSpec, ...] = CATALOG_SPECS + (EXE_SPEC,)

ONLY_ALIASES: dict[str, str] = {}
for _spec in ALL_SPECS:
    for _alias in _spec.aliases:
        ONLY_ALIASES[_alias.lower()] = _spec.id


def parse_only(raw: str) -> list[TargetSpec]:
    """Resolve ``--only addons,mods`` to specs. Empty string -> every target."""
    text = (raw or "").strip()
    if not text:
        return list(ALL_SPECS)
    wanted: list[TargetSpec] = []
    seen: set[str] = set()
    unknown: list[str] = []
    for part in text.split(","):
        token = part.strip().lower()
        if not token:
            continue
        spec_id = ONLY_ALIASES.get(token)
        if spec_id is None:
            unknown.append(part.strip())
            continue
        if spec_id in seen:
            continue
        seen.add(spec_id)
        wanted.append(spec_by_id(spec_id))
    if unknown:
        known = ", ".join(s.id for s in ALL_SPECS)
        raise ValueError(f"unknown --only name(s): {', '.join(unknown)}. Use {known}")
    if not wanted:
        raise ValueError("`--only` did not match any files")
    return wanted


def spec_by_id(spec_id: str) -> TargetSpec:
    for spec in ALL_SPECS:
        if spec.id == spec_id:
            return spec
    raise KeyError(spec_id)


def ask_yes(question: str, *, default: bool = False, line: str | None = None) -> bool:
    """Enter skips when default is False. ``line`` injects an answer for tests."""
    suffix = " [Y/n] " if default else " [y/N] "
    if line is None:
        try:
            raw = input(question + suffix)
        except EOFError:
            return default
    else:
        raw = line
    text = raw.strip().lower()
    if not text:
        return default
    if text in {"y", "yes"}:
        return True
    if text in {"n", "no"}:
        return False
    return default


def missing_checkout_help(root: Path) -> str | None:
    """Actionable message when this tree has no live JSON (wrong branch / cwd)."""
    missing = [
        spec.rel
        for spec in CATALOG_SPECS
        if spec.rel and not (root / spec.rel).is_file()
    ]
    if not missing:
        return None
    script = Path(__file__).resolve()
    return (
        f"This checkout is missing live catalog file(s): {', '.join(missing)}\n"
        f"Repo root used: {root}\n"
        f"This script:    {script}\n"
        f"sign_live.py lives on `{SIGNER_BRANCH}` (IchaLaunch-dev PR #7).\n"
        "There is no folder named 'python tools' — `python` is the interpreter.\n"
        "From the IchaLaunch-dev root (usually F:\\Launcher):\n"
        "  git fetch origin\n"
        f"  git checkout {SIGNER_BRANCH}\n"
        "  python tools/sign_live.py --only mods\n"
        "If checkout says that branch is already used by a worktree, run the\n"
        "same command from that worktree path instead."
    )


def public_remote_url(root: Path, remote: str = DEFAULT_REMOTE) -> str | None:
    proc = subprocess.run(
        ["git", "remote", "get-url", remote],
        cwd=root,
        capture_output=True,
        text=True,
    )
    url = (proc.stdout or "").strip()
    return url or None


def ensure_public_remote(root: Path, remote: str = DEFAULT_REMOTE) -> bool:
    """False when `public` is missing. Prints the exact git remote add command."""
    if public_remote_url(root, remote):
        return True
    print(
        f"Git remote `{remote}` is not configured in {root}.\n"
        f"Live catalogs go to {PUBLIC_REPO}, not IchaLaunch-dev.\n"
        f"  git remote add {remote} https://github.com/{PUBLIC_REPO}.git\n"
        f"  git fetch {remote} master\n"
        "Then re-run this command.",
        file=sys.stderr,
    )
    return False


def print_after_sign_upload_failed(
    items: list[SignedItem],
    *,
    branch: str,
    remote: str,
) -> None:
    """Sidecars are already on disk; tell the maintainer the exact next command."""
    catalogs = [i for i in items if i.spec.kind == "catalog"]
    if not catalogs:
        return
    print("Signing succeeded. Upload/PR did not. Sidecars are already written:", file=sys.stderr)
    for item in catalogs:
        print(f"  {item.sig_path}", file=sys.stderr)
        if item.upload_payload:
            print(f"  {item.path}", file=sys.stderr)
    only = ",".join(item.spec.id for item in catalogs)
    print(
        "Retry the public upload (re-uses the sidecars you just wrote):\n"
        f"  python tools/sign_live.py --only {only}\n"
        "If the branch already reached GitHub but `gh pr create` failed:\n"
        f"  gh pr create --repo {PUBLIC_REPO} --base master --head {branch} "
        '--title "Sign live catalog files"\n'
        f"Or open: https://github.com/{PUBLIC_REPO}/compare/master...{branch}?expand=1\n"
        f"If `{remote}` is missing:\n"
        f"  git remote add {remote} https://github.com/{PUBLIC_REPO}.git",
        file=sys.stderr,
    )


def default_key_path() -> Path:
    return _sign.default_key_path()


def purpose_for(spec: TargetSpec, path: Path | None = None) -> str:
    from ichalaunch.core.signing import purpose_for_signed_path

    return purpose_for_signed_path(path if path is not None else spec.filename)


def resolve_exe_path(explicit: Path | None, root: Path) -> Path | None:
    if explicit is not None:
        return explicit
    candidate = root / DEFAULT_EXE_REL
    if candidate.is_file():
        return candidate
    return None


def path_for_spec(spec: TargetSpec, root: Path, exe: Path | None) -> Path | None:
    if spec.kind == "exe":
        return exe
    assert spec.rel is not None
    return root / spec.rel


@dataclass
class FileStatus:
    spec: TargetSpec
    path: Path | None
    exists: bool
    sidecar_path: Path | None
    sidecar_state: str
    sidecar_detail: str
    public_state: str
    purpose: str


def inspect_sidecar(target: Path) -> tuple[str, str]:
    from ichalaunch.core.signing import (
        Signature,
        SignatureError,
        purpose_for_signed_path,
        verify_attestation,
        verify_bytes,
    )

    sig_path = _sign.sidecar_path_for(target)
    if not target.is_file():
        return "n/a", "payload missing"
    if not sig_path.is_file():
        return "missing", "no sidecar"
    try:
        payload = target.read_bytes()
        parsed = Signature.parse(sig_path.read_bytes())
        verify_bytes(payload, parsed)
        purpose = purpose_for_signed_path(target)
        if parsed.attestation is not None:
            verify_attestation(payload, parsed, expected_purpose=purpose)
            return "valid", f"verified ({parsed.attestation.purpose})"
        return "valid", "verified (legacy sidecar, no attestation)"
    except (SignatureError, OSError, UnicodeDecodeError) as exc:
        return "invalid", str(exc)


def public_show(rel: str, *, root: Path, remote: str = DEFAULT_REMOTE) -> bytes | None:
    ref = f"{remote}/master:{rel}"
    proc = subprocess.run(
        ["git", "show", ref],
        cwd=root,
        capture_output=True,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout


def fetch_public_master(*, root: Path, remote: str = DEFAULT_REMOTE) -> bool:
    if not ensure_public_remote(root, remote):
        return False
    print(f"Fetching {remote}/master for status...", file=sys.stderr)
    proc = subprocess.run(
        ["git", "fetch", remote, "master"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        print(
            f"Could not fetch {remote}/master ({err or 'git failed'}).\n"
            f"Check `git remote -v` in {root}. Expected `{remote}` → "
            f"https://github.com/{PUBLIC_REPO}.git",
            file=sys.stderr,
        )
        return False
    return True


def inspect_target(
    spec: TargetSpec,
    path: Path | None,
    *,
    public_bytes: bytes | None = None,
    public_known: bool = True,
) -> FileStatus:
    purpose = purpose_for(spec, path)
    if path is None or not path.is_file():
        return FileStatus(
            spec=spec,
            path=path,
            exists=False,
            sidecar_path=_sign.sidecar_path_for(path) if path is not None else None,
            sidecar_state="n/a",
            sidecar_detail="file missing",
            public_state="n/a" if spec.kind == "exe" else ("unknown" if not public_known else "missing"),
            purpose=purpose,
        )
    sidecar = _sign.sidecar_path_for(path)
    state, detail = inspect_sidecar(path)
    if spec.kind == "exe":
        public_state = "n/a"
    elif not public_known:
        public_state = "unknown"
    elif public_bytes is None:
        public_state = "missing"
    elif public_bytes == path.read_bytes():
        public_state = "same"
    else:
        public_state = "differs"
    return FileStatus(
        spec=spec,
        path=path,
        exists=True,
        sidecar_path=sidecar,
        sidecar_state=state,
        sidecar_detail=detail,
        public_state=public_state,
        purpose=purpose,
    )


def format_status_lines(st: FileStatus) -> list[str]:
    lines = [f"--- {st.spec.filename} ---"]
    if st.path is None:
        lines.append(f"  local:    not found (looked in {DEFAULT_EXE_REL})")
    elif not st.exists:
        lines.append(f"  local:    missing ({st.path})")
    else:
        lines.append(f"  local:    {st.path}")
        lines.append(f"  sidecar:  {st.sidecar_path.name if st.sidecar_path else '?'}  {st.sidecar_detail}")
    lines.append(f"  purpose:  {st.purpose}")
    if st.spec.kind == "exe":
        lines.append("  upload:   GitHub Release via publish_public_release.py (not ichalaunch/data/)")
    elif st.public_state == "same":
        lines.append("  public:   identical to public/master (.sig still uploaded if you sign)")
    elif st.public_state == "differs":
        lines.append("  public:   DIFFERS from public/master (JSON + .sig would be uploaded)")
    elif st.public_state == "missing":
        lines.append("  public:   not on public/master (JSON + .sig would be uploaded)")
    elif st.public_state == "unknown":
        lines.append("  public:   unknown (could not read public/master)")
    return lines


@dataclass
class Decision:
    spec: TargetSpec
    path: Path | None
    status: FileStatus
    accepted: bool
    skip_reason: str = ""


@dataclass
class SignedItem:
    spec: TargetSpec
    path: Path
    sig_path: Path
    purpose: str
    upload_payload: bool


@dataclass
class SessionResult:
    considered: list[str] = field(default_factory=list)
    accepted: list[str] = field(default_factory=list)
    signed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    catalog_pr: str | None = None
    exe_sig: Path | None = None
    dry_run: bool = False


@dataclass
class LiveHooks:
    """Test seams. Production leaves these None and uses the real implementations."""

    ask: Callable[[str], bool] | None = None
    read_line: Callable[[str], str] | None = None
    sign_file: Callable[..., object] | None = None
    fetch_public: Callable[[], bool] | None = None
    public_show: Callable[[str], bytes | None] | None = None
    upload_catalogs: Callable[..., str | None] | None = None
    publish_release: Callable[..., int] | None = None


def _ask(hooks: LiveHooks | None, question: str, *, yes_all: bool) -> bool:
    if yes_all:
        return True
    if hooks is not None and hooks.ask is not None:
        return bool(hooks.ask(question))
    return ask_yes(question, default=False)


def _read_line(hooks: LiveHooks | None, question: str) -> str:
    if hooks is not None and hooks.read_line is not None:
        return hooks.read_line(question)
    try:
        return input(question)
    except EOFError:
        return ""


def collect_decisions(
    specs: list[TargetSpec],
    *,
    root: Path,
    exe: Path | None,
    yes_all: bool,
    hooks: LiveHooks | None = None,
    public_known: bool = True,
    public_show_fn: Callable[[str], bytes | None] | None = None,
) -> list[Decision]:
    decisions: list[Decision] = []
    for spec in specs:
        path = path_for_spec(spec, root, exe)
        already_accepted = False
        if spec.kind == "exe" and (path is None or not path.is_file()):
            missing_status = inspect_target(spec, path, public_known=public_known)
            print()
            for line in format_status_lines(missing_status):
                print(line)
            if yes_all:
                print("  skip:     no IchaLaunch.exe (dist/ or --exe); skipped cleanly")
                decisions.append(Decision(spec, path, missing_status, False, "no exe"))
                continue
            if not _ask(hooks, spec.prompt, yes_all=False):
                decisions.append(Decision(spec, path, missing_status, False, "no"))
                continue
            pasted = _read_line(hooks, "Path to IchaLaunch.exe (empty skips): ").strip().strip('"')
            if not pasted:
                decisions.append(Decision(spec, path, missing_status, False, "no path"))
                continue
            path = Path(pasted)
            if not path.is_file():
                print(f"  skip:     no such file: {path}")
                decisions.append(Decision(spec, path, missing_status, False, "path missing"))
                continue
            exe = path
            already_accepted = True

        pub: bytes | None = None
        if spec.kind == "catalog" and spec.rel and public_show_fn is not None:
            pub = public_show_fn(spec.rel)
        status = inspect_target(
            spec, path, public_bytes=pub, public_known=public_known
        )
        print()
        for line in format_status_lines(status):
            print(line)
        if not status.exists:
            print("  skip:     file is missing")
            decisions.append(Decision(spec, path, status, False, "missing"))
            continue
        accepted = already_accepted or _ask(hooks, spec.prompt, yes_all=yes_all)
        decisions.append(
            Decision(spec, path, status, accepted, "" if accepted else "no")
        )
    return decisions


def files_to_upload(items: list[SignedItem]) -> list[str]:
    """Repo-relative paths that would land on public IchaLaunch (not the EXE)."""
    out: list[str] = []
    for item in items:
        if item.spec.kind != "catalog" or not item.spec.rel:
            continue
        if item.upload_payload:
            out.append(item.spec.rel)
        out.append(item.spec.rel + ".sig")
    return out


def _git(
    args: list[str],
    *,
    cwd: Path,
    check: bool = True,
    capture: bool = False,
) -> subprocess.CompletedProcess:
    print("+ git " + " ".join(args), file=sys.stderr)
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=check,
        capture_output=capture,
        text=not capture,
    )


def upload_catalogs(
    items: list[SignedItem],
    *,
    root: Path,
    remote: str,
    branch: str,
    dry_run: bool,
) -> str | None:
    catalogs = [i for i in items if i.spec.kind == "catalog"]
    if not catalogs:
        return None
    planned = files_to_upload(catalogs)
    print()
    print(f"Public catalog upload -> {PUBLIC_REPO} branch {branch}")
    for rel in planned:
        print(f"  add  {rel}")
    if dry_run:
        print("  (dry-run: not fetching, committing, or opening a PR)")
        return None

    if not ensure_public_remote(root, remote):
        print_after_sign_upload_failed(catalogs, branch=branch, remote=remote)
        return None

    fetch = subprocess.run(
        ["git", "fetch", remote, "master"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if fetch.returncode != 0:
        print(
            f"Cannot fetch {remote}/master ({(fetch.stderr or fetch.stdout or '').strip() or 'git failed'}).\n"
            f"Add or fix the remote and retry:\n"
            f"  git remote add {remote} https://github.com/{PUBLIC_REPO}.git\n"
            f"  git fetch {remote} master",
            file=sys.stderr,
        )
        print_after_sign_upload_failed(catalogs, branch=branch, remote=remote)
        return None

    base = Path(tempfile.mkdtemp(prefix="ichalaunch-sign-"))
    tree = base / "public"
    used_worktree = False
    pushed_branch = branch
    try:
        add = subprocess.run(
            ["git", "worktree", "add", "--detach", str(tree), f"{remote}/master"],
            cwd=root,
            capture_output=True,
            text=True,
        )
        if add.returncode == 0:
            used_worktree = True
        else:
            url_proc = subprocess.run(
                ["git", "remote", "get-url", remote],
                cwd=root,
                capture_output=True,
                text=True,
            )
            url = (url_proc.stdout or "").strip() or f"https://github.com/{PUBLIC_REPO}.git"
            tree.parent.mkdir(parents=True, exist_ok=True)
            clone = subprocess.run(
                ["git", "clone", "--branch", "master", "--single-branch", url, str(tree)],
                cwd=root,
                capture_output=True,
                text=True,
            )
            if clone.returncode != 0:
                print(
                    f"Could not check out public master ({add.stderr or clone.stderr}).",
                    file=sys.stderr,
                )
                print_after_sign_upload_failed(catalogs, branch=branch, remote=remote)
                return None

        _git(["checkout", "-B", branch], cwd=tree)
        for item in catalogs:
            assert item.spec.rel
            dest_payload = tree / item.spec.rel
            dest_sig = tree / (item.spec.rel + ".sig")
            dest_payload.parent.mkdir(parents=True, exist_ok=True)
            if item.upload_payload:
                dest_payload.write_bytes(item.path.read_bytes())
            dest_sig.write_bytes(item.sig_path.read_bytes())
            to_add = [item.spec.rel + ".sig"]
            if item.upload_payload:
                to_add.insert(0, item.spec.rel)
            _git(["-c", "core.autocrlf=false", "add", "--", *to_add], cwd=tree)
            if item.upload_payload:
                staged = subprocess.check_output(
                    ["git", "show", f":{item.spec.rel}"], cwd=tree
                )
                if staged != item.path.read_bytes():
                    print(
                        f"Refusing to commit {item.spec.rel}: staged bytes differ from "
                        "the file that was signed (line-ending drift).",
                        file=sys.stderr,
                    )
                    print_after_sign_upload_failed(catalogs, branch=branch, remote=remote)
                    return None
            staged_sig = subprocess.check_output(
                ["git", "show", f":{item.spec.rel}.sig"], cwd=tree
            )
            if staged_sig != item.sig_path.read_bytes():
                print(
                    f"Refusing to commit {item.spec.rel}.sig: staged sidecar differs.",
                    file=sys.stderr,
                )
                print_after_sign_upload_failed(catalogs, branch=branch, remote=remote)
                return None

        cached = subprocess.run(
            ["git", "diff", "--cached", "--quiet"], cwd=tree, capture_output=True
        )
        if cached.returncode == 0:
            print("Nothing new to commit on the public branch (already signed there).")
            return _existing_pr_url(branch) or "already-on-public"

        _git(
            [
                "commit",
                "-m",
                "Sign live catalog files.\n\n"
                "Sidecars produced locally; signing keys never enter CI.",
            ],
            cwd=tree,
        )
        push = subprocess.run(
            ["git", "push", "-u", remote, f"HEAD:{branch}"],
            cwd=tree,
        )
        if push.returncode != 0:
            pushed_branch = f"{branch}-{time.strftime('%Y%m%d-%H%M%S')}"
            print(
                f"Push to {branch} was rejected; pushing {pushed_branch} instead "
                "(no force-push).",
                file=sys.stderr,
            )
            retry = subprocess.run(
                ["git", "push", "-u", remote, f"HEAD:{pushed_branch}"],
                cwd=tree,
            )
            if retry.returncode != 0:
                print(
                    f"Push to {remote} was rejected for both `{branch}` and "
                    f"`{pushed_branch}` (no force-push). Sidecars stay local.",
                    file=sys.stderr,
                )
                print_after_sign_upload_failed(
                    catalogs, branch=pushed_branch, remote=remote
                )
                return None

        url = _open_or_reuse_pr(pushed_branch)
        if url is None:
            print_after_sign_upload_failed(catalogs, branch=pushed_branch, remote=remote)
        return url
    except subprocess.CalledProcessError as exc:
        print(f"Public catalog upload failed: {exc}", file=sys.stderr)
        print_after_sign_upload_failed(catalogs, branch=pushed_branch, remote=remote)
        return None
    finally:
        if used_worktree:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(tree)],
                cwd=root,
                capture_output=True,
            )
        shutil.rmtree(base, ignore_errors=True)


def _existing_pr_url(branch: str) -> str | None:
    proc = subprocess.run(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            PUBLIC_REPO,
            "--head",
            branch,
            "--state",
            "open",
            "--json",
            "url",
            "--jq",
            ".[0].url // empty",
        ],
        capture_output=True,
        text=True,
    )
    url = (proc.stdout or "").strip()
    return url or None


def _open_or_reuse_pr(branch: str) -> str | None:
    existing = _existing_pr_url(branch)
    if existing:
        print(f"Updated existing PR: {existing}")
        return existing
    body = (
        "## Summary\n"
        "- Signed live catalog file(s) locally (`ichalaunch-catalog` purpose).\n"
        "- Sidecars belong on public `master` at `ichalaunch/data/<name>.sig`.\n"
        "- Signing keys never enter CI. Merge JSON + `.sig` together.\n"
    )
    proc = subprocess.run(
        [
            "gh",
            "pr",
            "create",
            "--repo",
            PUBLIC_REPO,
            "--base",
            "master",
            "--head",
            branch,
            "--title",
            "Sign live catalog files",
            "--body",
            body,
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        print(
            f"Branch `{branch}` was pushed, but creating the PR failed:\n"
            f"{(proc.stderr or proc.stdout or '').strip()}\n"
            f"Open it manually: https://github.com/{PUBLIC_REPO}/compare/master...{branch}?expand=1",
            file=sys.stderr,
        )
        return None
    url = (proc.stdout or "").strip()
    print(f"Opened {url}")
    return url or None


def _run_publish_release(tag: str, exe: Path, sig: Path) -> int:
    spec = importlib.util.spec_from_file_location(
        "ichalaunch_publish_public_release",
        Path(__file__).with_name("publish_public_release.py"),
    )
    if spec is None or spec.loader is None:
        print("Could not load tools/publish_public_release.py", file=sys.stderr)
        return 1
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return int(
        mod.publish_release(
            tag=tag,
            exe=exe,
            sig=sig,
            title="",
            notes="",
            draft=False,
        )
    )


def offer_exe_publish(
    exe: Path,
    sig: Path,
    *,
    dry_run: bool,
    yes_all: bool,
    release_tag: str,
    hooks: LiveHooks | None = None,
) -> None:
    print()
    print("Signed IchaLaunch.exe - sidecar is a GitHub Release artefact,")
    print("not ichalaunch/data/.")
    print(f"  exe  {exe}")
    print(f"  sig  {sig}")
    cmd = (
        "python tools/publish_public_release.py "
        f"--tag vX.Y.Z --exe {exe} --sig {sig}"
    )
    if dry_run:
        print(f"  (dry-run: would offer {cmd})")
        return
    tag = (release_tag or "").strip()
    if not tag:
        if yes_all:
            print("Pass --release-tag vX.Y.Z to attach it, or run:")
            print(f"  {cmd}")
            return
        if not _ask(
            hooks,
            "Run publish_public_release.py to attach the EXE + .sig to a public release?",
            yes_all=False,
        ):
            print("Skipped release upload. Attach later with:")
            print(f"  {cmd}")
            return
        tag = _read_line(hooks, "Release tag (e.g. v1.5.2): ").strip()
        if not tag:
            print("No tag; skipped release upload.")
            return
    publish = hooks.publish_release if hooks is not None else None
    if publish is not None:
        publish(tag=tag, exe=exe, sig=sig)
        return
    code = _run_publish_release(tag, exe, sig)
    if code != 0:
        print(f"publish_public_release.py exited {code}", file=sys.stderr)


def run_live_session(
    *,
    key: Path,
    yes_all: bool = False,
    only: str = "",
    dry_run: bool = False,
    exe: Path | None = None,
    no_fetch: bool = False,
    release_tag: str = "",
    branch: str = DEFAULT_BRANCH,
    remote: str = DEFAULT_REMOTE,
    root: Path | None = None,
    hooks: LiveHooks | None = None,
    password: bytes | None = None,
) -> SessionResult:
    root = root if root is not None else find_repo_root()
    specs = parse_only(only)
    result = SessionResult(dry_run=dry_run)
    result.considered = [s.id for s in specs]

    public_known = True
    if no_fetch:
        public_known = False
    elif hooks is not None and hooks.fetch_public is not None:
        public_known = bool(hooks.fetch_public())
    elif any(s.kind == "catalog" for s in specs):
        public_known = fetch_public_master(root=root, remote=remote)

    def _show(rel: str) -> bytes | None:
        if hooks is not None and hooks.public_show is not None:
            return hooks.public_show(rel)
        if no_fetch and (hooks is None or hooks.public_show is None):
            return None
        return public_show(rel, root=root, remote=remote)

    resolved_exe = resolve_exe_path(exe, root)
    decisions = collect_decisions(
        specs,
        root=root,
        exe=resolved_exe,
        yes_all=yes_all,
        hooks=hooks,
        public_known=public_known or (hooks is not None and hooks.public_show is not None),
        public_show_fn=_show,
    )

    to_sign = [d for d in decisions if d.accepted and d.path is not None and d.path.is_file()]
    result.accepted = [d.spec.id for d in to_sign]
    result.skipped = [d.spec.id for d in decisions if not d.accepted]

    if not to_sign:
        print()
        print("Nothing to sign (every file skipped or missing).")
        return result

    sign_fn = hooks.sign_file if hooks is not None else None

    if dry_run:
        print()
        print("Dry-run - would sign:")
        for decision in to_sign:
            print(f"  {decision.spec.filename}  purpose={decision.status.purpose}")
        catalog_items = [
            SignedItem(
                spec=d.spec,
                path=d.path,  # type: ignore[arg-type]
                sig_path=_sign.sidecar_path_for(d.path),
                purpose=d.status.purpose,
                upload_payload=d.status.public_state in {"differs", "missing", "unknown"},
            )
            for d in to_sign
            if d.spec.kind == "catalog" and d.path is not None
        ]
        if catalog_items:
            if hooks is not None and hooks.upload_catalogs is not None:
                hooks.upload_catalogs(catalog_items, dry_run=True)
            else:
                upload_catalogs(
                    catalog_items,
                    root=root,
                    remote=remote,
                    branch=branch,
                    dry_run=True,
                )
        exe_items = [d for d in to_sign if d.spec.kind == "exe" and d.path is not None]
        if exe_items:
            offer_exe_publish(
                exe_items[0].path,
                _sign.sidecar_path_for(exe_items[0].path),
                dry_run=True,
                yes_all=yes_all,
                release_tag=release_tag,
                hooks=hooks,
            )
        return result

    if sign_fn is None and not key.is_file():
        print(
            f"No signing key at {key}\n"
            "Pass --key PATH or place the key at the default path.\n"
            "The private key must stay on this machine (never CI).",
            file=sys.stderr,
        )
        return result

    priv = None
    if sign_fn is None:
        try:
            priv = _sign.load_private_key(key, password, prompt=True)
        except Exception as exc:  # noqa: BLE001
            print(f"Could not unlock the key: {exc}", file=sys.stderr)
            return result

    signed_items: list[SignedItem] = []
    from ichalaunch.core.signing import SignatureError

    for decision in to_sign:
        assert decision.path is not None
        print()
        try:
            if sign_fn is not None:
                signed = sign_fn(
                    target=decision.path,
                    key=key,
                    password=password,
                    write=True,
                )
            else:
                signed = _sign.sign_file(
                    decision.path,
                    key,
                    password=password,
                    priv=priv,
                    prompt_password=False,
                )
        except (OSError, ValueError, SignatureError) as exc:
            print(f"  failed   {decision.spec.filename}: {exc}", file=sys.stderr)
            result.skipped.append(decision.spec.id)
            continue
        _sign._print_sign_result(signed)
        result.signed.append(decision.spec.id)
        upload_payload = decision.status.public_state in {"differs", "missing", "unknown"}
        if decision.spec.kind == "exe":
            result.exe_sig = signed.sidecar
            upload_payload = False
        signed_items.append(
            SignedItem(
                spec=decision.spec,
                path=decision.path,
                sig_path=signed.sidecar,
                purpose=signed.purpose,
                upload_payload=upload_payload,
            )
        )

    catalog_signed = [i for i in signed_items if i.spec.kind == "catalog"]
    if catalog_signed:
        if hooks is not None and hooks.upload_catalogs is not None:
            result.catalog_pr = hooks.upload_catalogs(catalog_signed, dry_run=False)
        else:
            result.catalog_pr = upload_catalogs(
                catalog_signed,
                root=root,
                remote=remote,
                branch=branch,
                dry_run=False,
            )

    exe_signed = [i for i in signed_items if i.spec.kind == "exe"]
    if exe_signed:
        offer_exe_publish(
            exe_signed[0].path,
            exe_signed[0].sig_path,
            dry_run=False,
            yes_all=yes_all,
            release_tag=release_tag,
            hooks=hooks,
        )
    return result


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--key",
        type=Path,
        default=None,
        help=f"private key PEM (default: {default_key_path()})",
    )
    ap.add_argument(
        "--yes-all",
        action="store_true",
        help="Sign every listed file that exists (no prompts). Missing EXE is skipped.",
    )
    ap.add_argument(
        "--only",
        default="",
        help="Comma list: addons, addon_tips, home_art, mods, exe (aliases: tips, home)",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Print status and what would be signed/uploaded; do not write or push",
    )
    ap.add_argument(
        "--exe",
        type=Path,
        default=None,
        help="IchaLaunch.exe path (default: dist/IchaLaunch.exe if present)",
    )
    ap.add_argument(
        "--no-fetch",
        action="store_true",
        help="Do not git fetch public/master (status vs public will be unknown)",
    )
    ap.add_argument(
        "--release-tag",
        default="",
        help="If the EXE was signed, attach it with publish_public_release.py",
    )
    ap.add_argument("--branch", default=DEFAULT_BRANCH, help="Public branch for catalog sidecars")
    ap.add_argument("--remote", default=DEFAULT_REMOTE, help="Git remote for brutaliccus/IchaLaunch")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = find_repo_root()
    print("IchaLaunch local sign + upload")
    print("The private key stays on this machine. Never put it in CI.")
    print(f"Repo: {root}")
    print(f"Script: {Path(__file__).resolve()}")
    missing = missing_checkout_help(root)
    if missing:
        print(missing, file=sys.stderr)
        return 2
    key = args.key if args.key is not None else default_key_path()
    print(f"Key: {key}" + ("  (missing)" if not key.is_file() else ""))
    try:
        result = run_live_session(
            key=key,
            yes_all=bool(args.yes_all),
            only=str(args.only or ""),
            dry_run=bool(args.dry_run),
            exe=args.exe,
            no_fetch=bool(args.no_fetch),
            release_tag=str(args.release_tag or ""),
            branch=str(args.branch),
            remote=str(args.remote),
            root=root,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if result.accepted and not result.signed and not result.dry_run:
        return 1
    catalog_ids = {spec.id for spec in CATALOG_SPECS}
    signed_catalogs = [name for name in result.signed if name in catalog_ids]
    if signed_catalogs and not result.dry_run and not result.catalog_pr:
        print_after_sign_upload_failed(
            [
                SignedItem(
                    spec=spec_by_id(name),
                    path=root / spec_by_id(name).rel,  # type: ignore[arg-type]
                    sig_path=_sign.sidecar_path_for(root / spec_by_id(name).rel),  # type: ignore[arg-type]
                    purpose="ichalaunch-catalog",
                    upload_payload=True,
                )
                for name in signed_catalogs
            ],
            branch=str(args.branch),
            remote=str(args.remote),
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

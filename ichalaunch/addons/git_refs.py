"""Git ref discovery and Atom fallbacks — not the GitHub REST API.

Used to read branch tips / tags without spending the 60/hour REST budget.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any
from xml.etree import ElementTree as ET

import requests

from ichalaunch.core.logging_setup import log

GIT_HTTP_UA = {
    "User-Agent": "git/2.46.0 (IchaLaunch)",
    "Accept": "*/*",
}
ATOM_UA = {
    "User-Agent": "IchaLaunch/0.1",
    "Accept": "application/atom+xml, application/xml, text/xml, */*",
}
_SHA_RE = re.compile(r"^[0-9a-f]{40,64}$", re.I)
_PKT_SHA_RE = re.compile(rb"^([0-9a-f]{40,64})\s+(\S+)")
_LS_REMOTE_RE = re.compile(r"^([0-9a-f]{40,64})\s+(\S+)\s*$", re.I)
_COMMIT_IN_URL_RE = re.compile(r"/commit/([0-9a-f]{7,64})", re.I)
_COMMIT_IN_ID_RE = re.compile(r"(?:Commit|commit)/([0-9a-f]{7,64})", re.I)
_RELEASE_TAG_RE = re.compile(r"/releases/tag/([^/#?]+)", re.I)
_ATOM_NS = {"a": "http://www.w3.org/2005/Atom"}

_FETCH_TIMEOUT_SEC = 12
_MAX_ADVERTISEMENT_BYTES = 2 * 1024 * 1024

# owner/repo lower -> GitRefs
_refs_cache: dict[str, GitRefs] = {}


@dataclass
class GitRefs:
    """Advertised refs for a GitHub repo (HEAD, branches, peeled tags)."""

    head_sha: str = ""
    default_branch: str = ""
    branches: dict[str, str] = field(default_factory=dict)
    tags: dict[str, str] = field(default_factory=dict)

    def tip_sha(self, ref: str | None = None) -> str:
        """SHA for a branch, tag, or HEAD when *ref* is empty."""
        name = (ref or "").strip()
        if not name:
            return self.head_sha
        if name in self.branches:
            return self.branches[name]
        if name in self.tags:
            return self.tags[name]
        short = name.split("/")[-1]
        if short in self.branches:
            return self.branches[short]
        if short in self.tags:
            return self.tags[short]
        if self.default_branch and name == self.default_branch:
            return self.head_sha
        return ""


def repo_cache_key(owner: str, repo: str) -> str:
    return f"{owner.strip().lower()}/{repo.strip().lower()}"


def clear_git_refs_cache() -> None:
    _refs_cache.clear()


def cached_git_refs(owner: str, repo: str) -> GitRefs | None:
    return _refs_cache.get(repo_cache_key(owner, repo))


def store_git_refs(owner: str, repo: str, refs: GitRefs) -> GitRefs:
    _refs_cache[repo_cache_key(owner, repo)] = refs
    return refs


def iter_pkt_lines(data: bytes) -> list[bytes]:
    """Split a git pkt-line advertisement into payloads (flush packets omitted)."""
    out: list[bytes] = []
    i = 0
    n = len(data)
    while i + 4 <= n:
        try:
            length = int(data[i : i + 4], 16)
        except ValueError:
            break
        if length == 0:
            i += 4
            continue
        if length < 4 or i + length > n:
            break
        out.append(data[i + 4 : i + length].rstrip(b"\n"))
        i += length
    return out


def _short_ref(name: str) -> str:
    if name.startswith("refs/heads/"):
        return name[11:]
    if name.startswith("refs/tags/"):
        return name[10:]
    return name


def parse_upload_pack_refs(data: bytes) -> GitRefs:
    """Parse a protocol-v1 ``git-upload-pack`` ref advertisement."""
    refs = GitRefs()
    peeled: dict[str, str] = {}
    for raw in iter_pkt_lines(data):
        if raw.startswith(b"#"):
            continue
        nul = raw.find(b"\0")
        line = raw[:nul] if nul >= 0 else raw
        caps = raw[nul + 1 :].decode("utf-8", "replace") if nul >= 0 else ""
        m = _PKT_SHA_RE.match(line)
        if not m:
            continue
        sha = m.group(1).decode("ascii")
        name = m.group(2).decode("utf-8", "replace")
        if name == "HEAD":
            refs.head_sha = sha
            for part in caps.split():
                if part.startswith("symref=HEAD:"):
                    target = part.split(":", 1)[1].strip()
                    refs.default_branch = _short_ref(target)
            continue
        if name.endswith("^{}"):
            peeled[_short_ref(name[:-3])] = sha
            continue
        if name.startswith("refs/heads/"):
            refs.branches[_short_ref(name)] = sha
        elif name.startswith("refs/tags/"):
            refs.tags[_short_ref(name)] = sha
    for tag, sha in peeled.items():
        refs.tags[tag] = sha
    if not refs.default_branch and refs.head_sha:
        for branch, sha in refs.branches.items():
            if sha == refs.head_sha:
                refs.default_branch = branch
                break
    if not refs.head_sha and refs.default_branch:
        refs.head_sha = refs.branches.get(refs.default_branch, "")
    if not refs.default_branch and "main" in refs.branches:
        refs.default_branch = "main"
        if not refs.head_sha:
            refs.head_sha = refs.branches["main"]
    elif not refs.default_branch and "master" in refs.branches:
        refs.default_branch = "master"
        if not refs.head_sha:
            refs.head_sha = refs.branches["master"]
    return refs


def parse_ls_remote(text: str) -> GitRefs:
    """Parse ``git ls-remote`` stdout."""
    blob = b""
    for line in (text or "").splitlines():
        m = _LS_REMOTE_RE.match(line.strip())
        if not m:
            continue
        sha, name = m.group(1), m.group(2)
        # Fake a pkt-line-shaped record for the shared parser.
        payload = f"{sha} {name}\n".encode("utf-8")
        blob += f"{len(payload) + 4:04x}".encode("ascii") + payload
    return parse_upload_pack_refs(blob)


def fetch_upload_pack_refs(owner: str, repo: str, *, timeout: float = _FETCH_TIMEOUT_SEC) -> GitRefs | None:
    """GET GitHub smart-HTTP ref advertisement (protocol v1, no REST quota)."""
    url = f"https://github.com/{owner}/{repo}.git/info/refs"
    try:
        r = requests.get(
            url,
            params={"service": "git-upload-pack"},
            headers=GIT_HTTP_UA,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        log.debug("upload-pack failed for %s/%s: %s", owner, repo, exc)
        return None
    if r.status_code != 200 or not r.content:
        log.debug("upload-pack HTTP %s for %s/%s", r.status_code, owner, repo)
        return None
    data = r.content[:_MAX_ADVERTISEMENT_BYTES]
    refs = parse_upload_pack_refs(data)
    if not refs.head_sha and not refs.branches:
        return None
    return refs


def fetch_ls_remote_refs(owner: str, repo: str, *, timeout: float = _FETCH_TIMEOUT_SEC) -> GitRefs | None:
    """``git ls-remote`` fallback when the HTTP advertisement is blocked."""
    git = shutil.which("git")
    if not git:
        return None
    remote = f"https://github.com/{owner}/{repo}.git"
    kwargs: dict[str, Any] = {
        "capture_output": True,
        "text": True,
        "timeout": timeout,
        "check": False,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run([git, "ls-remote", remote], **kwargs)
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("ls-remote failed for %s/%s: %s", owner, repo, exc)
        return None
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        return None
    refs = parse_ls_remote(proc.stdout)
    if not refs.head_sha and not refs.branches:
        return None
    return refs


def fetch_git_refs(owner: str, repo: str, *, use_cache: bool = True) -> GitRefs | None:
    """Resolve refs via upload-pack, then git ls-remote. Cached per process."""
    if use_cache:
        hit = cached_git_refs(owner, repo)
        if hit is not None:
            return hit
    refs = fetch_upload_pack_refs(owner, repo)
    if refs is None:
        refs = fetch_ls_remote_refs(owner, repo)
    if refs is None:
        return None
    return store_git_refs(owner, repo, refs)


def parse_atom_commit_sha(xml_text: str) -> str | None:
    """First commit SHA from a GitHub commits Atom feed."""
    text = (xml_text or "").strip()
    if not text:
        return None
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None
    entries = root.findall("a:entry", _ATOM_NS) or root.findall("entry")
    if not entries:
        return None
    entry = entries[0]
    for node in (
        entry.find("a:id", _ATOM_NS),
        entry.find("id"),
        entry.find("a:link", _ATOM_NS),
        entry.find("link"),
    ):
        if node is None:
            continue
        raw = (node.get("href") or (node.text or "")).strip()
        m = _COMMIT_IN_URL_RE.search(raw) or _COMMIT_IN_ID_RE.search(raw)
        if m and len(m.group(1)) >= 7:
            return m.group(1).lower()
    return None


def _atom_entry_release_tag(entry: ET.Element) -> str:
    """Release tag from one Atom entry (link/id preferred over title)."""
    from urllib.parse import unquote

    for node in (
        entry.find("a:link", _ATOM_NS),
        entry.find("link"),
        entry.find("a:id", _ATOM_NS),
        entry.find("id"),
    ):
        if node is None:
            continue
        raw = (node.get("href") or (node.text or "")).strip()
        m = _RELEASE_TAG_RE.search(raw)
        if not m:
            continue
        try:
            return unquote(m.group(1)).strip()
        except Exception:  # noqa: BLE001
            return m.group(1).strip()
    return ""


def _atom_entry_title(entry: ET.Element) -> str:
    for node in (entry.find("a:title", _ATOM_NS), entry.find("title")):
        if node is not None and (node.text or "").strip():
            return (node.text or "").strip()
    return ""


def iter_atom_release_entries(xml_text: str) -> list[tuple[str, str]]:
    """``(tag_name, title)`` pairs from a GitHub releases Atom feed (document order)."""
    text = (xml_text or "").strip()
    if not text:
        return []
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []
    entries = root.findall("a:entry", _ATOM_NS) or root.findall("entry")
    out: list[tuple[str, str]] = []
    for entry in entries:
        tag = _atom_entry_release_tag(entry)
        title = _atom_entry_title(entry)
        if tag or title:
            out.append((tag, title))
    return out


def parse_atom_release_tag(xml_text: str) -> str | None:
    """First usable release tag from a GitHub releases Atom feed.

    Skips junk side tags (e.g. SuperWoW ``Patch``) even when they appear first.
    """
    for tag, title in iter_atom_release_entries(xml_text):
        if tag and is_usable_release_tag(tag):
            return tag
        if not tag and title:
            # Title-only feeds: accept a semver-ish first token, not prose.
            token = title.split()[0].strip()
            if token and is_usable_release_tag(token) and not is_preferred_release_alias(token):
                return token
    return None


def parse_atom_release_display_version(
    xml_text: str, *, prefer_tag: str | None = None
) -> str:
    """Semver label from Atom titles/tags (e.g. ``SuperWoW 2.2`` → ``v2.2``)."""
    wanted = (prefer_tag or "").strip().lower()
    for tag, title in iter_atom_release_entries(xml_text):
        if wanted and tag.lower() != wanted:
            continue
        if tag and tag.lower() in _JUNK_RELEASE_TAGS:
            continue
        for raw in (title, tag):
            label = extract_semver_label(raw)
            if label:
                return label
        if wanted:
            break
    if wanted:
        # Prefer-tag miss: still try any usable non-junk entry.
        return parse_atom_release_display_version(xml_text, prefer_tag=None)
    return ""


def fetch_releases_atom_display_version(
    owner: str,
    repo: str,
    *,
    prefer_tag: str | None = None,
    timeout: float = _FETCH_TIMEOUT_SEC,
) -> str:
    """Display version from ``releases.atom`` titles (no REST)."""
    url = f"https://github.com/{owner}/{repo}/releases.atom"
    try:
        r = requests.get(url, headers=ATOM_UA, timeout=timeout)
    except requests.RequestException as exc:
        log.debug("releases atom display failed for %s: %s", url, exc)
        return ""
    if r.status_code != 200:
        return ""
    return parse_atom_release_display_version(r.text, prefer_tag=prefer_tag)

def fetch_commit_atom_sha(
    owner: str,
    repo: str,
    branch: str | None = None,
    *,
    timeout: float = _FETCH_TIMEOUT_SEC,
) -> str | None:
    """Latest commit SHA from ``commits/{branch}.atom`` (not REST).

    When *branch* is set, only that feed is tried — never fall back to the
    default ``commits.atom``, or a missing TOC/version pin would look resolved.
    """
    wanted = (branch or "").strip()
    paths = [f"commits/{wanted}"] if wanted else ["commits"]
    for path in paths:
        url = f"https://github.com/{owner}/{repo}/{path}.atom"
        try:
            r = requests.get(url, headers=ATOM_UA, timeout=timeout)
        except requests.RequestException as exc:
            log.debug("commits atom failed for %s: %s", url, exc)
            continue
        if r.status_code != 200:
            continue
        sha = parse_atom_commit_sha(r.text)
        if sha:
            return sha
    return None


def fetch_releases_atom_tag(
    owner: str,
    repo: str,
    *,
    timeout: float = _FETCH_TIMEOUT_SEC,
) -> str | None:
    """Latest release tag from ``releases.atom`` (not REST)."""
    url = f"https://github.com/{owner}/{repo}/releases.atom"
    try:
        r = requests.get(url, headers=ATOM_UA, timeout=timeout)
    except requests.RequestException as exc:
        log.debug("releases atom failed for %s: %s", url, exc)
        return None
    if r.status_code != 200:
        return None
    return parse_atom_release_tag(r.text)


# SuperWoW publishes the DLL zip on ``Release`` and an optional MPQ on ``Patch``.
# ``Patch`` is not a SuperWoW version — never treat it as latest.
_PREFERRED_RELEASE_TAGS = frozenset({"release", "latest", "stable"})
_JUNK_RELEASE_TAGS = frozenset({"patch", "assets", "mpq", "textures"})
# Dotted semver in filenames / titles: SuperWoW.release.2.2.zip, "SuperWoW 2.2".
# Allow `.` / `-` / `_` separators before the version (common in asset names).
_SEMVER_IN_TEXT_RE = re.compile(
    r"(?i)(?:^|[^0-9])v?(\d+(?:\.\d+){1,3})(?![0-9])"
)
# Labels that look like dates / HTTP stamps, not release versions.
_TIMESTAMP_LABEL_RE = re.compile(
    r"(?ix)^(?:"
    r"\d{4}-\d{2}-\d{2}(?:[tT ][\d:.+-zZ]*)?"  # ISO date / datetime
    r"|[a-z]{3},\s+\d{1,2}\s+[a-z]{3}\s+\d{4}\b"  # HTTP Last-Modified
    r"|\d{6}_\d{4}"  # YYMMDD_HHMM style stamps
    r")"
)


def version_key(tag: str) -> tuple[int, ...]:
    """Numeric tuple for comparing git tags (``v1.2.3`` → ``(1, 2, 3)``)."""
    cleaned = (tag or "").strip().lstrip("vV")
    parts: list[int] = []
    for piece in cleaned.split("."):
        m = re.match(r"(\d+)", piece)
        parts.append(int(m.group(1)) if m else 0)
    return tuple(parts) if parts else (0,)


def is_version_tag(tag: str) -> bool:
    return bool(re.search(r"\d", (tag or "").strip()))


def is_preferred_release_alias(tag: str) -> bool:
    """True for rolling aliases (Release/latest/stable) — usable for tracking, not UI."""
    return (tag or "").strip().lower() in _PREFERRED_RELEASE_TAGS


def looks_like_timestamp_label(text: str) -> bool:
    """True for ISO dates, HTTP Last-Modified stamps, and similar non-version labels."""
    raw = str(text or "").strip().strip('"')
    if not raw:
        return False
    if _TIMESTAMP_LABEL_RE.match(raw):
        return True
    if re.search(r"(?i)\b(?:GMT|UTC)\b", raw) and re.search(r"\d{4}", raw):
        return True
    return False


def extract_semver_label(text: str) -> str:
    """Pull ``vX.Y[.Z…]`` from a filename or release title; empty when none found."""
    m = _SEMVER_IN_TEXT_RE.search(str(text or ""))
    if not m:
        return ""
    return f"v{m.group(1)}"


def is_usable_release_tag(tag: str) -> bool:
    """True for semver-like tags or a main Release/latest/stable alias."""
    name = (tag or "").strip()
    if not name or name.endswith("^{}"):
        return False
    if name.lower() in _JUNK_RELEASE_TAGS:
        return False
    if looks_like_timestamp_label(name):
        return False
    if is_version_tag(name):
        return True
    return is_preferred_release_alias(name)


def newest_version_tag(tags: dict[str, str] | list[str]) -> str:
    names = list(tags.keys()) if isinstance(tags, dict) else [str(t) for t in tags]
    names = [n for n in names if n and not n.endswith("^{}")]
    if not names:
        return ""
    versioned = [
        n
        for n in names
        if is_version_tag(n) and is_usable_release_tag(n) and not is_preferred_release_alias(n)
    ]
    if versioned:
        return max(versioned, key=version_key)
    preferred = [n for n in names if is_preferred_release_alias(n)]
    if preferred:
        rank = {"release": 0, "stable": 1, "latest": 2}
        return min(preferred, key=lambda n: (rank.get(n.lower(), 9), n.lower()))
    useful = [n for n in names if is_usable_release_tag(n)]
    return useful[0] if useful else ""

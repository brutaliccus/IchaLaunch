"""Public GitLab.com addon install / preview helpers (unauthenticated)."""

from __future__ import annotations

import json
import re
from typing import Any, NamedTuple
from urllib.parse import quote, unquote, urlparse

import requests

from ichalaunch.core.logging_setup import log

UA = {"User-Agent": "IchaLaunch/0.1", "Accept": "application/json"}
_GITLAB_API = "https://gitlab.com/api/v4"
_TIMEOUT_SEC = 20

# First path segment on gitlab.com that is not a user/group namespace.
_RESERVED_OWNERS = frozenset(
    {
        "-",
        "admin",
        "api",
        "dashboard",
        "explore",
        "groups",
        "help",
        "oauth",
        "projects",
        "signin",
        "signup",
        "users",
    }
)

_REPO_RE = re.compile(
    r"https?://(?:www\.)?gitlab\.com/([^/]+)/([^/#?]+?)(?:\.git)?(?:/|$)",
    re.I,
)

_browse_tags_cache: dict[str, list[str]] = {}


class ParsedGitLabUrl(NamedTuple):
    """owner/repo plus optional tag / branch / SHA from common GitLab browse URLs."""

    owner: str
    repo: str
    tag: str | None = None


def parse_gitlab_url(url: str) -> ParsedGitLabUrl | None:
    """Parse gitlab.com owner/repo, optionally a tag/ref.

    Supports:
    - ``https://gitlab.com/owner/repo``
    - ``https://gitlab.com/owner/repo.git``
    - ``https://gitlab.com/owner/repo/-/tags/1.2.3``
    - ``https://gitlab.com/owner/repo/-/releases/1.2.3``
    - ``https://gitlab.com/owner/repo/-/archive/REF/repo-REF.zip``
    - ``https://gitlab.com/owner/repo/-/tree/branch``
    """
    raw = (url or "").strip()
    if not raw:
        return None
    try:
        host = (urlparse(raw).hostname or "").lower().rstrip(".")
    except ValueError:
        return None
    if host not in {"gitlab.com", "www.gitlab.com"}:
        return None
    match = _REPO_RE.match(raw)
    if not match:
        return None
    owner, repo = match.group(1), match.group(2)
    if not owner or not repo:
        return None
    if owner.lower() in _RESERVED_OWNERS or repo.lower() in _RESERVED_OWNERS:
        return None
    if "/" in owner or "/" in repo:
        return None
    tag: str | None = None
    for pattern in (
        r"/-/tags/([^/#?]+)",
        r"/-/releases/([^/#?]+)",
        r"/-/archive/([^/]+)/",
        r"/-/tree/([^/#?]+)",
        r"/-/commit/([0-9a-f]{7,64})",
    ):
        found = re.search(pattern, raw, re.I)
        if found:
            tag = found.group(1)
            break
    if tag:
        try:
            tag = unquote(tag)
        except Exception:  # noqa: BLE001
            pass
        tag = tag.strip().rstrip("/") or None
        if tag and tag.lower() == "permalink":
            tag = None
    return ParsedGitLabUrl(owner, repo, tag)


def gitlab_browse_url(owner: str, repo: str) -> str:
    return f"https://gitlab.com/{owner}/{repo}"


def gitlab_tag_page_url(owner: str, repo: str, tag: str) -> str:
    return f"https://gitlab.com/{owner}/{repo}/-/tags/{tag}"


def gitlab_project_id(owner: str, repo: str) -> str:
    """URL-encoded ``owner/repo`` for ``/projects/:id``."""
    return quote(f"{owner}/{repo}", safe="")


def gitlab_archive_url(owner: str, repo: str, ref: str | None = None) -> str:
    """Public archive zip. Empty *ref* uses the project's default branch."""
    base = (
        f"{_GITLAB_API}/projects/{gitlab_project_id(owner, repo)}"
        "/repository/archive.zip"
    )
    if ref:
        return f"{base}?sha={quote(str(ref), safe='')}"
    return base


def gitlab_raw_file_url(owner: str, repo: str, branch: str, path: str) -> str:
    rel = (path or "").lstrip("/")
    return f"https://gitlab.com/{owner}/{repo}/-/raw/{branch}/{rel}"


def _repo_cache_key(owner: str, repo: str) -> str:
    return f"{owner.strip().lower()}/{repo.strip().lower()}"


def clear_gitlab_browse_cache() -> None:
    _browse_tags_cache.clear()


def gitlab_headers() -> dict[str, str]:
    """Unauthenticated GitLab headers — never attach a GitHub token."""
    return dict(UA)


def gitlab_get(url: str, *, timeout: int = _TIMEOUT_SEC, **kwargs: Any) -> requests.Response:
    """GET a GitLab.com API URL without GitHub credentials."""
    headers = dict(kwargs.pop("headers", None) or {})
    headers.update(gitlab_headers())
    return requests.get(url, headers=headers, timeout=timeout, **kwargs)


def gitlab_project(owner: str, repo: str) -> dict[str, Any]:
    r = gitlab_get(f"{_GITLAB_API}/projects/{gitlab_project_id(owner, repo)}")
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, dict) else {}


def gitlab_latest_commit(
    owner: str,
    repo: str,
    ref: str | None = None,
) -> dict[str, Any]:
    """Tip commit for *ref* (branch/tag/SHA) or the default branch."""
    branch = (ref or "").strip()
    if not branch:
        info = gitlab_project(owner, repo)
        branch = str(info.get("default_branch") or "main") or "main"
    encoded = quote(branch, safe="")
    r = gitlab_get(
        f"{_GITLAB_API}/projects/{gitlab_project_id(owner, repo)}"
        f"/repository/commits/{encoded}"
    )
    r.raise_for_status()
    data = r.json() if r.content else {}
    if not isinstance(data, dict):
        data = {}
    sha = str(data.get("id") or data.get("short_id") or "").strip()
    date = str(data.get("committed_date") or data.get("created_at") or "")
    message = str(data.get("title") or data.get("message") or "")
    return {
        "sha": sha,
        "branch": branch,
        "message": message,
        "date": date,
    }


def list_gitlab_repo_tags(
    owner: str,
    repo: str,
    *,
    use_cache: bool = True,
    limit: int = 50,
) -> list[str]:
    """Public repository tags (session-cached). Empty on failure."""
    key = _repo_cache_key(owner, repo)
    if use_cache and key in _browse_tags_cache:
        return list(_browse_tags_cache[key])
    cap = max(1, min(int(limit), 100))
    tags: list[str] = []
    seen: set[str] = set()
    try:
        r = gitlab_get(
            f"{_GITLAB_API}/projects/{gitlab_project_id(owner, repo)}/repository/tags",
            params={"per_page": cap, "order_by": "updated"},
        )
        if r.status_code == 404:
            _browse_tags_cache[key] = []
            return []
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list):
            for item in data:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                low = name.lower()
                if name and low not in seen:
                    seen.add(low)
                    tags.append(name)
    except (requests.RequestException, ValueError, json.JSONDecodeError) as exc:
        log.debug("GitLab tag list failed for %s/%s: %s", owner, repo, exc)
        return []
    _browse_tags_cache[key] = list(tags)
    return tags


def parse_entry_gitlab(
    entry: dict[str, Any] | None,
    meta: dict[str, Any] | None = None,
) -> ParsedGitLabUrl | None:
    """First GitLab URL on a catalog / installed-addon row."""
    entry = entry if isinstance(entry, dict) else {}
    meta = meta if isinstance(meta, dict) else {}
    for raw in (
        entry.get("repo"),
        entry.get("url"),
        meta.get("url"),
        entry.get("repository"),
        meta.get("repository"),
    ):
        parsed = parse_gitlab_url(str(raw or ""))
        if parsed:
            return parsed
    return None


def _absolute_gitlab_media_url(
    src: str,
    *,
    owner: str,
    repo: str,
    branch: str,
    readme_dir: str = "",
) -> str:
    url = (src or "").strip()
    if not url or url.startswith(("data:", "http://", "https://", "//")):
        if url.startswith("//"):
            return "https:" + url
        blob = re.match(
            r"https?://(?:www\.)?gitlab\.com/([^/]+)/([^/]+)/-/blob/([^/]+)/(.+)$",
            url,
            re.I,
        )
        if blob:
            return gitlab_raw_file_url(blob.group(1), blob.group(2), blob.group(3), blob.group(4))
        return url
    if url.startswith("#"):
        return url
    if url.startswith("/"):
        rel = url.lstrip("/")
    else:
        rel = f"{readme_dir}{url}"
    parts: list[str] = []
    for part in rel.replace("\\", "/").split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return gitlab_raw_file_url(owner, repo, branch, "/".join(parts))


def rewrite_gitlab_readme_media(
    markdown: str,
    *,
    owner: str,
    repo: str,
    branch: str,
    readme_dir: str = "",
) -> str:
    from ichalaunch.addons.github import _IMG_HTML_RE, _IMG_MD_RE, _REF_IMG_RE

    def _md_sub(m: re.Match[str]) -> str:
        return m.group(1) + _absolute_gitlab_media_url(
            m.group(2), owner=owner, repo=repo, branch=branch, readme_dir=readme_dir
        ) + m.group(3)

    def _html_sub(m: re.Match[str]) -> str:
        abs_url = _absolute_gitlab_media_url(
            m.group(2), owner=owner, repo=repo, branch=branch, readme_dir=readme_dir
        )
        return f'<img src="{abs_url}" />'

    def _ref_sub(m: re.Match[str]) -> str:
        target = m.group(2)
        lower = target.lower().split("?", 1)[0]
        if lower.endswith(
            (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".mp4", ".webm")
        ) or not target.startswith(("http://", "https://", "//", "#", "mailto:")):
            target = _absolute_gitlab_media_url(
                target, owner=owner, repo=repo, branch=branch, readme_dir=readme_dir
            )
        return f"{m.group(1)}{target}{m.group(3)}"

    out = _IMG_MD_RE.sub(_md_sub, markdown)
    out = _IMG_HTML_RE.sub(_html_sub, out)
    return _REF_IMG_RE.sub(_ref_sub, out)


def fetch_gitlab_readme(
    owner: str,
    repo: str,
    branch: str | None = None,
) -> dict[str, str] | None:
    """Fetch README markdown from GitLab (raw file API, then web raw)."""
    from ichalaunch.addons.github import (
        _README_MAX_CHARS,
        _README_RAW_NAMES,
        localize_readme_images,
    )

    use_branch = (branch or "main").strip() or "main"
    body = ""
    headers = {"User-Agent": "IchaLaunch/0.1", "Accept": "text/plain,*/*"}
    project = gitlab_project_id(owner, repo)
    for name in _README_RAW_NAMES:
        encoded = quote(name, safe="")
        api = (
            f"{_GITLAB_API}/projects/{project}/repository/files/{encoded}/raw"
            f"?ref={quote(use_branch, safe='')}"
        )
        try:
            r = requests.get(api, headers=headers, timeout=_TIMEOUT_SEC)
        except requests.RequestException as exc:
            log.debug("GitLab README miss %s: %s", api, exc)
            continue
        if r.status_code == 200 and (r.text or "").strip():
            body = r.text
            break
    if not body:
        for name in _README_RAW_NAMES:
            raw = gitlab_raw_file_url(owner, repo, use_branch, name)
            try:
                r = requests.get(raw, headers=headers, timeout=_TIMEOUT_SEC)
            except requests.RequestException as exc:
                log.debug("GitLab raw README miss %s: %s", raw, exc)
                continue
            if r.status_code == 200 and (r.text or "").strip():
                body = r.text
                break
    if not body.strip():
        return None
    if len(body) > _README_MAX_CHARS:
        body = body[:_README_MAX_CHARS] + "\n\n… (README truncated for preview)"
    md = rewrite_gitlab_readme_media(
        body, owner=owner, repo=repo, branch=use_branch
    )
    import tempfile
    from pathlib import Path

    cache_dir = Path(tempfile.mkdtemp(prefix="ichalaunch-readme-"))
    localized = localize_readme_images(md, cache_dir=cache_dir)
    return {
        "markdown": localized,
        "raw_markdown": body,
        "base_url": f"https://gitlab.com/{owner}/{repo}/-/raw/{use_branch}/",
        "cache_dir": str(cache_dir),
    }


def preview_gitlab_repo(url: str) -> dict[str, Any]:
    """Fetch GitLab repo metadata for the install / Settings README preview."""
    parsed = parse_gitlab_url(url)
    if not parsed:
        raise ValueError(
            "Not a valid GitLab repository URL. "
            "Example: https://gitlab.com/owner/repo"
        )
    owner, repo, tag = parsed.owner, parsed.repo, parsed.tag
    full = f"{owner}/{repo}"
    data: dict[str, Any] = {}
    branch = "main"
    try:
        data = gitlab_project(owner, repo)
        branch = str(data.get("default_branch") or "main") or "main"
    except (requests.RequestException, ValueError, json.JSONDecodeError) as exc:
        log.debug("GitLab project metadata skipped for %s: %s", full, exc)

    commit: dict[str, Any] = {}
    pin_resolved = False
    if tag:
        try:
            tip = gitlab_latest_commit(owner, repo, ref=tag)
            if str(tip.get("sha") or "").strip():
                commit = tip
                pin_resolved = True
        except Exception as exc:  # noqa: BLE001
            log.debug("GitLab pin %s/%s@%s unresolved: %s", owner, repo, tag, exc)
    if not str(commit.get("sha") or "").strip():
        try:
            commit = gitlab_latest_commit(owner, repo, ref=branch)
        except Exception as exc:  # noqa: BLE001
            log.debug("GitLab tip lookup failed for %s: %s", full, exc)
            commit = {"sha": "", "branch": branch, "message": "", "date": ""}

    from ichalaunch.addons.github import load_catalog
    from ichalaunch.config.settings import settings

    catalog_hit = None
    for entry in load_catalog():
        other = parse_gitlab_url(str(entry.get("repo") or entry.get("url") or ""))
        if other and other.owner.lower() == owner.lower() and other.repo.lower() == repo.lower():
            catalog_hit = entry
            break

    installed_meta = None
    for folder, meta in (settings.installed_addons or {}).items():
        if not isinstance(meta, dict):
            continue
        ou = parse_gitlab_url(str(meta.get("url") or ""))
        if ou and ou.owner.lower() == owner.lower() and ou.repo.lower() == repo.lower():
            installed_meta = {"folder": folder, **meta}
            break
        key = (meta.get("repository") or "").strip().lower()
        if key == full.lower() and str(meta.get("source") or "").lower() == "gitlab":
            installed_meta = {"folder": folder, **meta}
            break

    sha = str(commit.get("sha") or "")
    date = str(commit.get("date") or "")
    if "T" in date:
        date = date.split("T", 1)[0]
    msg = str(commit.get("message") or "").strip().splitlines()[0] if commit.get("message") else ""

    readme = None
    try:
        if tag and pin_resolved:
            readme = fetch_gitlab_readme(owner, repo, branch=tag)
        if not readme:
            readme = fetch_gitlab_readme(owner, repo, branch=branch)
    except Exception as exc:  # noqa: BLE001
        log.debug("GitLab README skipped for %s: %s", full, exc)
        readme = None

    install_url = (
        gitlab_tag_page_url(owner, repo, tag)
        if tag and pin_resolved
        else gitlab_browse_url(owner, repo)
    )
    return {
        "kind": "addon",
        "host": "gitlab",
        "url": install_url,
        "full_name": str(data.get("path_with_namespace") or full),
        "description": (data.get("description") or "").strip() or "(no description)",
        "stars": int(data.get("star_count") or 0),
        "default_branch": branch,
        "tag": tag if pin_resolved else "",
        "commit_sha": sha[:7] if sha else "",
        "commit_date": date,
        "commit_message": msg[:120],
        "catalog_name": (catalog_hit or {}).get("name"),
        "catalog_folder": (catalog_hit or {}).get("folder"),
        "already_installed": installed_meta is not None,
        "installed_folder": (installed_meta or {}).get("folder"),
        "readme_markdown": (readme or {}).get("markdown") or "",
        "readme_raw": (readme or {}).get("raw_markdown") or "",
        "readme_base_url": (readme or {}).get("base_url") or "",
        "readme_cache_dir": (readme or {}).get("cache_dir") or "",
    }

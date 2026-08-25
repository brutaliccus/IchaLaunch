"""In-app Available-catalog suggestions (HTTPS POST, no GitHub credentials)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import requests

from ichalaunch import __version__
from ichalaunch.addons.github import parse_github_url
from ichalaunch.config.settings import settings

# Built-in Cloudflare Worker for ADDONS → Suggest for catalog (not user-configurable).
ADDON_SUBMIT_URL = "https://ichalaunch-addon-submit.ichalaunch.workers.dev"

_UA = {
    "User-Agent": f"IchaLaunch/{__version__}",
    "Accept": "application/json",
    "Content-Type": "application/json",
}
_TIMEOUT_SEC = 20
_MAX_NAME = 120
# README excerpt for the GitHub issue body (Worker MAX_DESC must match).
_MAX_DESC = 4000
_MAX_FOLDER = 80
_MAX_CATEGORY = 64


@dataclass(frozen=True)
class SubmitResult:
    ok: bool
    message: str
    status_code: int | None = None
    issue_url: str | None = None


def addon_submit_url() -> str:
    """Hardcoded catalog-suggestion endpoint."""
    return ADDON_SUBMIT_URL


def anonymous_client_id() -> str:
    """Stable anonymous id for rate-limit hints only (no PII)."""
    existing = str(settings.get("anonymous_client_id") or "").strip()
    if existing:
        return existing
    new_id = str(uuid.uuid4())
    settings.set("anonymous_client_id", new_id)
    return new_id


def normalize_repo_url(url: str) -> str | None:
    """Return canonical ``https://github.com/owner/repo`` or None if invalid."""
    parsed = parse_github_url(url)
    if not parsed:
        return None
    owner = parsed.owner.strip()
    repo = parsed.repo.strip().removesuffix(".git")
    if not owner or not repo or "/" in owner or "/" in repo:
        return None
    return f"https://github.com/{owner}/{repo}"


def repo_slug_from_url(url: str) -> str:
    """Return the repository name segment from a canonical GitHub URL."""
    canon = normalize_repo_url(url) or (url or "").strip().rstrip("/")
    return canon.rsplit("/", 1)[-1] if canon else ""


def truncate_readme_excerpt(text: str, *, max_len: int = _MAX_DESC) -> str:
    """Trim README / description text for the suggestion payload."""
    s = (text or "").strip()
    if len(s) <= max_len:
        return s
    cut = max_len - 20
    if cut < 1:
        return s[:max_len]
    return s[:cut].rstrip() + "\n\n… (truncated)"


def repo_in_catalog(
    repo_url: str,
    catalog: list[dict[str, Any]] | None = None,
) -> bool:
    """True if *repo_url* matches an Available catalog ``repo`` or ``forks[].repo``.

    Same matching idea as ``tools/catalog_approve_from_issue.py``.
    """
    want = normalize_repo_url(repo_url)
    if not want:
        return False
    want_l = want.lower()

    if catalog is None:
        from ichalaunch.addons.catalog import load_catalog

        catalog = load_catalog()

    for item in catalog or []:
        if not isinstance(item, dict):
            continue
        existing = normalize_repo_url(str(item.get("repo") or item.get("url") or ""))
        if existing and existing.lower() == want_l:
            return True
        for fork in item.get("forks") or []:
            if not isinstance(fork, dict):
                continue
            f_repo = normalize_repo_url(str(fork.get("repo") or fork.get("url") or ""))
            if f_repo and f_repo.lower() == want_l:
                return True
    return False


def build_submit_payload(
    *,
    repo: str,
    name: str = "",
    category: str = "",
    description: str = "",
    folder: str = "",
) -> tuple[dict[str, Any] | None, str | None]:
    """Validate fields and build the JSON body. Returns ``(payload, error)``.

    ``name`` / ``folder`` default to the GitHub repo slug when omitted.
    ``description`` is expected to be a README excerpt (truncated to ``_MAX_DESC``).
    """
    canon = normalize_repo_url(repo)
    if not canon:
        return None, "Enter a GitHub repository URL (github.com/owner/repo)."

    slug = repo_slug_from_url(canon)
    name_s = (name or "").strip() or slug
    if len(name_s) > _MAX_NAME:
        return None, f"Name must be at most {_MAX_NAME} characters."

    cat_s = (category or "").strip()
    if not cat_s:
        return None, "Choose a category."
    if len(cat_s) > _MAX_CATEGORY:
        return None, f"Category must be at most {_MAX_CATEGORY} characters."

    desc_s = truncate_readme_excerpt(description or "")
    folder_s = (folder or "").strip() or slug
    if len(folder_s) > _MAX_FOLDER:
        return None, f"Folder must be at most {_MAX_FOLDER} characters."

    payload: dict[str, Any] = {
        "repo": canon,
        "name": name_s,
        "category": cat_s,
        "description": desc_s,
        "folder": folder_s,
        "launcher_version": __version__,
        "client_id": anonymous_client_id(),
    }
    return payload, None


def submit_catalog_suggestion(payload: dict[str, Any]) -> SubmitResult:
    """POST *payload* to the built-in HTTPS suggestion endpoint."""
    url = addon_submit_url()

    try:
        r = requests.post(url, json=payload, headers=_UA, timeout=_TIMEOUT_SEC)
    except requests.Timeout:
        return SubmitResult(ok=False, message="Request timed out. Try again later.")
    except requests.RequestException as exc:
        return SubmitResult(
            ok=False,
            message=f"Could not reach suggestion endpoint: {exc}",
        )

    body: dict[str, Any] = {}
    try:
        raw = r.json()
        if isinstance(raw, dict):
            body = raw
    except (ValueError, TypeError):
        body = {}

    msg = str(body.get("message") or body.get("error") or "").strip()
    issue_url = str(body.get("issue_url") or "").strip() or None

    if 200 <= r.status_code < 300:
        return SubmitResult(
            ok=True,
            message=msg or "Suggestion submitted. Maintainers will review it.",
            status_code=r.status_code,
            issue_url=issue_url,
        )

    if not msg:
        if r.status_code == 429:
            msg = "Too many suggestions. Please wait and try again."
        elif r.status_code >= 500:
            msg = "Suggestion service is temporarily unavailable."
        else:
            msg = f"Suggestion failed (HTTP {r.status_code})."
    return SubmitResult(
        ok=False,
        message=msg,
        status_code=r.status_code,
        issue_url=issue_url,
    )


def _readme_excerpt_for_repo(owner: str, repo: str) -> str:
    """Best-effort README / GitHub description text for auto-submit."""
    try:
        from ichalaunch.addons.github import fetch_repo_readme, github_get
    except ImportError:
        return ""

    readme = None
    try:
        readme = fetch_repo_readme(owner, repo)
    except Exception:  # noqa: BLE001
        readme = None
    if isinstance(readme, dict):
        text = str(readme.get("raw_markdown") or readme.get("markdown") or "").strip()
        if text:
            return text

    try:
        r = github_get(f"https://api.github.com/repos/{owner}/{repo}", timeout=20)
        data = r.json() if r.ok else {}
        if isinstance(data, dict):
            return str(data.get("description") or "").strip()
    except Exception:  # noqa: BLE001
        pass
    return ""


def try_auto_submit_after_git_import(
    repo_url: str,
    *,
    catalog: list[dict[str, Any]] | None = None,
    category: str = "General",
    name: str = "",
    folder: str = "",
) -> SubmitResult | None:
    """Best-effort catalog suggestion after a successful + Git Repo install.

    Returns ``None`` when the repo is already in the Available catalog (no
    POST). Never raises for expected network/API failures — callers should
    treat submit as non-blocking relative to local install.
    """
    from ichalaunch.core.logging_setup import log

    canon = normalize_repo_url(repo_url)
    if not canon:
        log.info("Auto catalog submit skipped: invalid GitHub URL")
        return SubmitResult(ok=False, message="Invalid GitHub URL")

    try:
        if repo_in_catalog(canon, catalog):
            log.info("Auto catalog submit skipped: already in catalog (%s)", canon)
            return None
    except Exception as exc:  # noqa: BLE001
        log.warning("Auto catalog submit: catalog check failed: %s", exc)

    parsed = parse_github_url(canon)
    if not parsed:
        return SubmitResult(ok=False, message="Invalid GitHub URL")

    description = ""
    try:
        description = _readme_excerpt_for_repo(parsed.owner, parsed.repo)
    except Exception as exc:  # noqa: BLE001
        log.warning("Auto catalog submit: README fetch failed: %s", exc)

    slug = repo_slug_from_url(canon)
    payload, err = build_submit_payload(
        repo=canon,
        name=(name or "").strip() or slug,
        category=(category or "").strip() or "General",
        description=description,
        folder=(folder or "").strip() or slug,
    )
    if err or not payload:
        log.info("Auto catalog submit skipped: %s", err or "bad payload")
        return SubmitResult(ok=False, message=err or "Could not build suggestion")

    try:
        result = submit_catalog_suggestion(payload)
    except Exception as exc:  # noqa: BLE001
        log.warning("Auto catalog submit failed: %s", exc)
        return SubmitResult(ok=False, message=str(exc) or "Submit failed")

    if result.ok:
        log.info("Auto catalog submit ok for %s", canon)
    else:
        log.info("Auto catalog submit failed for %s: %s", canon, result.message)
    return result

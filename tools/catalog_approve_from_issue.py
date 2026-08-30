#!/usr/bin/env python3
"""Parse a catalog-suggestion GitHub issue and append an entry to addons.json.

Used by `.github/workflows/catalog-approve.yml` in the private dev repo
when the public release repo dispatches ``catalog-approved`` (label on a
public catalog issue). Prints a small JSON summary on stdout for the Action.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "ichalaunch" / "data" / "addons.json"

REPO_BULLET_RE = re.compile(
    r"^\s*-\s*\*\*repo:\*\*\s*(.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
FIELD_BULLET_RE = re.compile(
    r"^\s*-\s*\*\*(name|category|description|folder):\*\*\s*(.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
REPO_URL_RE = re.compile(
    r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/?$",
    re.IGNORECASE,
)


def normalize_repo(raw: str) -> str | None:
    s = (raw or "").strip().rstrip("/").replace(".git", "")
    if s.lower() in {"(none)", "none", "n/a", "-"}:
        return None
    m = REPO_URL_RE.match(s)
    if not m:
        return None
    return f"https://github.com/{m.group(1)}/{m.group(2)}"


def repo_owner_name(repo_url: str) -> tuple[str, str]:
    path = urlparse(repo_url).path.strip("/")
    parts = path.split("/")
    if len(parts) < 2:
        raise ValueError(f"bad repo url: {repo_url}")
    return parts[0], parts[1]


def clean_optional(value: str | None) -> str:
    text = (value or "").strip()
    if not text or text.lower() in {"(none)", "none", "n/a", "-"}:
        return ""
    return text


def parse_issue_body(body: str) -> dict[str, str]:
    """Extract fields from the Worker-written issue template."""
    body = body or ""
    fields: dict[str, str] = {}

    m = REPO_BULLET_RE.search(body)
    if m:
        fields["repo"] = m.group(1).strip()

    for m in FIELD_BULLET_RE.finditer(body):
        key = m.group(1).lower()
        val = m.group(2).strip()
        # Placeholder when README is in a separate section
        if key == "description" and val.lower().startswith("(readme"):
            continue
        fields[key] = val

    # Prefer the fenced suggested JSON block when present (authoritative shape).
    fence = re.search(r"```json\s*(\{.*?\})\s*```", body, re.DOTALL | re.IGNORECASE)
    if fence:
        try:
            suggested = json.loads(fence.group(1))
        except json.JSONDecodeError:
            suggested = None
        if isinstance(suggested, dict):
            for key in ("repo", "name", "category", "description", "folder"):
                val = suggested.get(key)
                if isinstance(val, str) and val.strip():
                    fields[key] = val.strip()

    # README excerpt section (Worker puts multi-line description here).
    readme = re.search(
        r"###\s*README excerpt\s*\n+````(?:markdown)?\s*\n(.*?)````",
        body,
        re.DOTALL | re.IGNORECASE,
    )
    if readme:
        excerpt = readme.group(1).strip()
        if excerpt:
            fields["description"] = excerpt

    return fields


def build_entry(fields: dict[str, str]) -> dict[str, Any]:
    repo = normalize_repo(fields.get("repo", ""))
    if not repo:
        raise SystemExit("Could not parse a valid GitHub repo URL from the issue body.")

    owner, repo_name = repo_owner_name(repo)
    name = clean_optional(fields.get("name"))
    folder = clean_optional(fields.get("folder"))
    description = clean_optional(fields.get("description"))
    category = clean_optional(fields.get("category")) or "General"

    if not name:
        name = repo_name
    if not folder:
        folder = repo_name

    entry: dict[str, Any] = {
        "name": name,
        "repo": repo,
        "category": category,
        "source": "community",
        "folder": folder,
    }
    if description:
        entry["description"] = description

    entry["_owner"] = owner  # stripped before write; used for PR title
    entry["_repo_name"] = repo_name
    return entry


def pick_pr_url_for_head(prs: Any, branch: str) -> str:
    """Return the first PR URL whose ``headRefName`` matches *branch*.

    Used by the catalog-approve Action after ``gh pr list --json url,headRefName``.
    ``gh --jq`` does not accept ``--arg``, and current ``gh`` rejects
    ``--head owner:branch``.
    """
    want = (branch or "").strip()
    if not want or not isinstance(prs, list):
        return ""
    for pr in prs:
        if not isinstance(pr, dict):
            continue
        if str(pr.get("headRefName") or "") == want:
            return str(pr.get("url") or "").strip()
    return ""


def repo_already_present(catalog: list[Any], repo_url: str) -> bool:
    want = normalize_repo(repo_url)
    if not want:
        return False
    want_l = want.lower()
    for item in catalog:
        if not isinstance(item, dict):
            continue
        existing = normalize_repo(str(item.get("repo") or ""))
        if existing and existing.lower() == want_l:
            return True
        for fork in item.get("forks") or []:
            if not isinstance(fork, dict):
                continue
            f_repo = normalize_repo(str(fork.get("repo") or ""))
            if f_repo and f_repo.lower() == want_l:
                return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--body-file", help="Path to issue body text")
    ap.add_argument(
        "--catalog",
        type=Path,
        default=DEFAULT_CATALOG,
        help="Path to addons.json",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and report without writing addons.json",
    )
    ap.add_argument(
        "--pick-pr-head",
        metavar="BRANCH",
        help="Read `gh pr list --json url,headRefName` from stdin; print matching PR URL",
    )
    args = ap.parse_args()

    if args.pick_pr_head is not None:
        raw = sys.stdin.read().strip()
        if not raw:
            return 0
        try:
            prs = json.loads(raw)
        except json.JSONDecodeError:
            return 0
        url = pick_pr_url_for_head(prs, args.pick_pr_head)
        if url:
            print(url)
        return 0

    if not args.body_file:
        ap.error("--body-file is required unless --pick-pr-head is set")

    body = Path(args.body_file).read_text(encoding="utf-8")
    fields = parse_issue_body(body)
    entry = build_entry(fields)
    owner = entry.pop("_owner")
    repo_name = entry.pop("_repo_name")

    catalog_path: Path = args.catalog
    raw = json.loads(catalog_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise SystemExit("addons.json must be a JSON array")

    if repo_already_present(raw, entry["repo"]):
        summary = {
            "skipped": True,
            "reason": "already_in_catalog",
            "repo": entry["repo"],
            "owner": owner,
            "repo_name": repo_name,
        }
        print(json.dumps(summary))
        return 0

    if not args.dry_run:
        raw.append(entry)
        catalog_path.write_text(
            json.dumps(raw, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    summary = {
        "skipped": False,
        "repo": entry["repo"],
        "owner": owner,
        "repo_name": repo_name,
        "name": entry["name"],
        "folder": entry["folder"],
        "category": entry["category"],
        "entry": entry,
    }
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())

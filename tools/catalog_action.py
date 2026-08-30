#!/usr/bin/env python3
"""Shared catalog-action issue family (mod pins, addon versions, signatures).

One identity per drifted artefact. Used by pin-check / tip-holdback / approve
workflows. Pure helpers here are safe for smoke_test; network stays in callers.

Issue titles:
    [mod-pin] classic_api
    [addon-tip] owner/repo
    [addon-pin] owner/repo
    [sign] addon_tips.json

Approve is the same label as first-time catalog suggestions
(``catalog-approved``). The approve job classifies the body and routes:

* New-addon suggestions and pin/tip bumps open or update a public PR that
  edits only the JSON payload, then **stop**. They do not squash-merge.
* The private key never enters CI. Sign locally with ``tools/sign.py``
  (catalog purpose ``ichalaunch-catalog``), commit the ``.sig`` on that PR,
  and merge JSON+sig together. Fail-closed clients ignore unsigned live JSON.

Players never see the change until the signed public file actually updates.
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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

KIND_MOD_PIN = "mod-pin"
KIND_ADDON_TIP = "addon-tip"
KIND_ADDON_PIN = "addon-pin"
KIND_SIGN = "sign"
ACTION_KINDS = frozenset({KIND_MOD_PIN, KIND_ADDON_TIP, KIND_ADDON_PIN, KIND_SIGN})

TITLE_PREFIX = {
    KIND_MOD_PIN: "[mod-pin]",
    KIND_ADDON_TIP: "[addon-tip]",
    KIND_ADDON_PIN: "[addon-pin]",
    KIND_SIGN: "[sign]",
}

SIGNED_LIVE_FILES = (
    "ichalaunch/data/addons.json",
    "ichalaunch/data/addon_tips.json",
    "ichalaunch/data/home_art.json",
    "ichalaunch/data/mods.json",
)

SIGN_STEP = (
    "This is **not live for players** until a maintainer approves the content "
    "and publishes a new sidecar signature. Signing keys must not enter CI.\n\n"
    "Two-step approve:\n"
    "1. Label this issue `catalog-approved` (or re-run **Catalog approve → "
    "public PR** with this issue number). The bot opens a PR that edits only "
    "the JSON payload.\n"
    "2. Sign that file locally and commit `<file>.sig` on the same PR:\n"
    "   `python tools/sign.py --key %LOCALAPPDATA%\\IchaLaunch\\signing\\"
    "ichalaunch-key1.pem <file>`\n"
    "   Merge JSON and `.sig` together. The launcher refuses unsigned or "
    "bad-sig live files and keeps cache/bundled. Do not merge JSON alone."
)

ACTION_FENCE_RE = re.compile(
    r"```json\s*(\{.*?\})\s*```",
    re.DOTALL | re.IGNORECASE,
)


def action_identity(payload: dict[str, Any]) -> str:
    kind = str(payload.get("kind") or "").strip()
    ident = str(payload.get("id") or payload.get("path") or "").strip()
    return f"{kind}:{ident}".lower()


def issue_title(payload: dict[str, Any]) -> str:
    kind = str(payload.get("kind") or "").strip()
    prefix = TITLE_PREFIX.get(kind, "[catalog-action]")
    ident = str(payload.get("id") or payload.get("path") or "unknown").strip()
    return f"{prefix} {ident}"


def issue_body(payload: dict[str, Any]) -> str:
    kind = str(payload.get("kind") or "")
    lines = [
        f"Catalog action `{kind}` — review before anything reaches players.",
        "",
        SIGN_STEP,
        "",
    ]
    if kind == KIND_MOD_PIN:
        lines.extend(
            [
                f"- **id:** `{payload.get('id', '')}`",
                f"- **name:** {payload.get('name') or payload.get('id') or ''}",
                f"- **source:** `{payload.get('label') or payload.get('id')}`",
                f"- **repo:** `{payload.get('repo') or ''}`",
                f"- **current pin:** `{payload.get('current_tag') or ''}` "
                f"`{payload.get('current_sha256') or ''}`",
                f"- **proposed:** `{payload.get('new_tag') or ''}` "
                f"`{payload.get('new_sha256') or ''}`",
                f"- **asset:** `{payload.get('asset') or ''}`",
                "",
            ]
        )
    elif kind in {KIND_ADDON_TIP, KIND_ADDON_PIN}:
        lines.extend(
            [
                f"- **repo:** `{payload.get('id') or ''}`",
                f"- **name:** {payload.get('name') or ''}",
                f"- **folder:** `{payload.get('folder') or ''}`",
                f"- **published:** tag `{payload.get('current_tag') or ''}` "
                f"sha `{str(payload.get('current_sha') or '')[:12]}`",
                f"- **upstream:** tag `{payload.get('new_tag') or ''}` "
                f"sha `{str(payload.get('new_sha') or '')[:12]}`",
                "",
            ]
        )
        if kind == KIND_ADDON_PIN:
            lines.append(
                "This addon is catalog-pinned (`pin_release` / `updates: false`). "
                "Approving bumps the pin in `addons.json` (then sign that file)."
            )
            lines.append("")
        else:
            lines.append(
                "Approving writes this tip into published `addon_tips.json` "
                "(then sign that file). Hourly rebuilds will not publish it first."
            )
            lines.append("")
    elif kind == KIND_SIGN:
        lines.extend(
            [
                f"- **file:** `{payload.get('path') or payload.get('id') or ''}`",
                f"- **reason:** {payload.get('reason') or 'signature missing or stale'}",
                "",
                "No JSON payload to merge. Sign the current public file and "
                "push `<file>.sig` beside it on public master (via PR).",
                "",
            ]
        )
    blob = json.dumps(payload, indent=2, ensure_ascii=False)
    lines.extend(["```json", blob, "```", ""])
    return "\n".join(lines)


def parse_action_payload(body: str) -> dict[str, Any] | None:
    """Return the action JSON fence, or None for first-time catalog suggestions."""
    if not body:
        return None
    for match in ACTION_FENCE_RE.finditer(body):
        try:
            obj = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        kind = str(obj.get("kind") or "").strip()
        if kind in ACTION_KINDS and (obj.get("id") or obj.get("path")):
            return obj
    return None


def find_open_action_issue(
    issues: list[Any], payload: dict[str, Any]
) -> dict[str, Any] | None:
    """First open issue for this identity (title prefix or identity in body)."""
    want_title = issue_title(payload).strip().lower()
    want_id = action_identity(payload)
    if not isinstance(issues, list):
        return None
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        title = str(issue.get("title") or "").strip().lower()
        if title == want_title or title.startswith(want_title + " "):
            return issue
        body = str(issue.get("body") or "")
        parsed = parse_action_payload(body)
        if parsed and action_identity(parsed) == want_id:
            return issue
    return None


def plan_issue_upsert(
    open_issues: list[Any], payload: dict[str, Any]
) -> dict[str, Any]:
    """Pure: create vs comment-update. No GitHub I/O."""
    title = issue_title(payload)
    body = issue_body(payload)
    existing = find_open_action_issue(open_issues, payload)
    if existing is None:
        return {
            "action": "create",
            "identity": action_identity(payload),
            "title": title,
            "body": body,
        }
    number = existing.get("number")
    prev = str(existing.get("body") or "")
    prev_parsed = parse_action_payload(prev)
    same = prev_parsed is not None and prev_parsed == payload
    return {
        "action": "skip" if same else "update",
        "number": number,
        "identity": action_identity(payload),
        "title": title,
        "body": body,
    }


def shape_mod_pin(
    *,
    mod_id: str,
    name: str = "",
    label: str = "",
    repo: str = "",
    current_tag: str = "",
    current_sha256: str = "",
    new_tag: str = "",
    new_sha256: str = "",
    asset: str = "",
    dest_sha256: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "kind": KIND_MOD_PIN,
        "id": str(mod_id).strip(),
        "name": name or mod_id,
        "label": label or mod_id,
        "repo": repo,
        "current_tag": current_tag,
        "current_sha256": current_sha256,
        "new_tag": new_tag,
        "new_sha256": new_sha256,
        "asset": asset,
    }
    if dest_sha256:
        payload["dest_sha256"] = dest_sha256
    return payload


def shape_addon_version(
    *,
    repo: str,
    name: str = "",
    folder: str = "",
    current_tag: str = "",
    current_sha: str = "",
    new_tag: str = "",
    new_sha: str = "",
    display_version: str = "",
    pinned: bool = False,
) -> dict[str, Any]:
    return {
        "kind": KIND_ADDON_PIN if pinned else KIND_ADDON_TIP,
        "id": repo.strip().lower(),
        "name": name,
        "folder": folder,
        "current_tag": current_tag,
        "current_sha": current_sha,
        "new_tag": new_tag,
        "new_sha": new_sha,
        "display_version": display_version,
    }


def shape_sign(*, path: str, reason: str) -> dict[str, Any]:
    rel = path.replace("\\", "/").strip()
    return {
        "kind": KIND_SIGN,
        "id": Path(rel).name,
        "path": rel,
        "reason": reason,
    }


def sign_payloads_from_status(rows: list[Any]) -> list[dict[str, Any]]:
    """Emit one [sign] payload per live file that is present but unsigned."""
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        path = str(row.get("path") or "").replace("\\", "/").strip()
        if path not in SIGNED_LIVE_FILES:
            continue
        payload_ok = bool(row.get("payload_ok"))
        sig_ok = bool(row.get("sig_ok"))
        if payload_ok and not sig_ok:
            reason = str(row.get("reason") or "live file has no valid .sig sidecar")
            out.append(shape_sign(path=path, reason=reason))
    return out


def _repo_key(repo: str) -> str:
    text = (repo or "").strip().rstrip("/").replace(".git", "").lower()
    if "github.com/" in text:
        path = urlparse(text if "://" in text else f"https://{text}").path.strip("/")
        parts = path.split("/")
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}"
        return path
    return text


def addon_pin_map(addons: list[Any]) -> dict[str, dict[str, Any]]:
    """repo-key → {name, folder, pin_release, locked} from addons.json."""
    from ichalaunch.addons.github import catalog_locks_updates, catalog_pin_tag

    out: dict[str, dict[str, Any]] = {}
    if not isinstance(addons, list):
        return out

    def add(entry: dict[str, Any], repo_raw: str) -> None:
        key = _repo_key(repo_raw)
        if not key or "/" not in key:
            return
        out[key] = {
            "name": str(entry.get("name") or ""),
            "folder": str(entry.get("folder") or ""),
            "pin_release": catalog_pin_tag(entry) or str(entry.get("pin_release") or ""),
            "locked": bool(catalog_locks_updates(entry)),
        }

    for entry in addons:
        if not isinstance(entry, dict):
            continue
        add(entry, str(entry.get("repo") or entry.get("url") or ""))
        for fork in entry.get("forks") or []:
            if isinstance(fork, dict):
                add(fork, str(fork.get("repo") or fork.get("url") or ""))
    return out


def _index_repos(index: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(index, dict):
        return {}
    repos = index.get("repos")
    return repos if isinstance(repos, dict) else {}


def hold_back_unpublished_tips(
    published: dict[str, Any],
    candidate: dict[str, Any],
    *,
    pin_map: dict[str, dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Keep published tips for repos whose tag/sha moved; issue those.

    Repos that appear only in the candidate (new catalog entry) are kept so a
    first-time catalog approve can grow the tip index without a second issue.
    """
    pins = pin_map or {}
    pub_repos = dict(_index_repos(published))
    cand_repos = _index_repos(candidate)
    held = dict(pub_repos)
    issues: list[dict[str, Any]] = []

    for key, cand in cand_repos.items():
        if not isinstance(cand, dict):
            continue
        repo_key = str(key).strip().lower()
        prev = pub_repos.get(repo_key)
        if not isinstance(prev, dict):
            held[repo_key] = cand
            continue
        old_tag = str(prev.get("latest_tag") or "").strip()
        new_tag = str(cand.get("latest_tag") or "").strip()
        old_sha = str(prev.get("sha") or "").strip()
        new_sha = str(cand.get("sha") or "").strip()
        if old_tag == new_tag and old_sha == new_sha:
            held[repo_key] = cand
            continue
        held[repo_key] = prev
        meta = pins.get(repo_key) or {}
        issues.append(
            shape_addon_version(
                repo=repo_key,
                name=str(meta.get("name") or ""),
                folder=str(meta.get("folder") or ""),
                current_tag=str(meta.get("pin_release") or old_tag),
                current_sha=old_sha,
                new_tag=new_tag,
                new_sha=new_sha,
                display_version=str(cand.get("display_version") or ""),
                pinned=bool(meta.get("locked")),
            )
        )

    out_index = {
        "generated_at": str((published or {}).get("generated_at") or ""),
        "source": str((published or {}).get("source") or "held"),
        "repos": held,
    }
    return out_index, issues


def _find_mod_source(
    catalog: list[Any], mod_id: str, label: str
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    for mod in catalog:
        if not isinstance(mod, dict):
            continue
        if str(mod.get("id") or "") != mod_id:
            continue
        if label.endswith(":addon_source"):
            src = mod.get("addon_source")
            return mod, src if isinstance(src, dict) else None
        src = mod.get("source")
        return mod, src if isinstance(src, dict) else None
    return None, None


def apply_mod_pin(catalog: list[Any], payload: dict[str, Any]) -> bool:
    """Write sha256 + pinned_tag (and dest hashes when the payload has them)."""
    mod_id = str(payload.get("id") or "")
    label = str(payload.get("label") or mod_id)
    _mod, source = _find_mod_source(catalog, mod_id, label)
    if source is None:
        raise SystemExit(f"No catalog source for {label}")
    new_hash = str(payload.get("new_sha256") or "").strip().lower()
    new_tag = str(payload.get("new_tag") or "").strip()
    if not new_hash:
        raise SystemExit("mod-pin payload missing new_sha256")
    already = (
        str(source.get("sha256") or "").strip().lower() == new_hash
        and (not new_tag or str(source.get("pinned_tag") or "").strip() == new_tag)
    )
    source["sha256"] = new_hash
    if new_tag:
        source["pinned_tag"] = new_tag
    dest = str(payload.get("dest_sha256") or "").strip().lower()
    if dest and _mod is not None:
        if "dest_sha256" in _mod:
            _mod["dest_sha256"] = dest
        files = _mod.get("files")
        if isinstance(files, list) and len(files) == 1 and isinstance(files[0], dict):
            if files[0].get("sha256") or files[0].get("dest_sha256"):
                if "sha256" in files[0]:
                    files[0]["sha256"] = dest
                if "dest_sha256" in files[0]:
                    files[0]["dest_sha256"] = dest
        if source.get("dest_sha256"):
            source["dest_sha256"] = dest
    return not already


def apply_addon_tip(index: dict[str, Any], payload: dict[str, Any]) -> bool:
    repos = index.setdefault("repos", {})
    if not isinstance(repos, dict):
        raise SystemExit("addon_tips.json repos must be an object")
    key = str(payload.get("id") or "").strip().lower()
    if not key:
        raise SystemExit("addon-tip payload missing id")
    prev = repos.get(key) if isinstance(repos.get(key), dict) else {}
    new_tag = str(payload.get("new_tag") or "").strip()
    new_sha = str(payload.get("new_sha") or "").strip()
    already = (
        str(prev.get("latest_tag") or "").strip() == new_tag
        and str(prev.get("sha") or "").strip() == new_sha
    )
    entry = dict(prev)
    if new_sha:
        entry["sha"] = new_sha
        branches = entry.get("branches") if isinstance(entry.get("branches"), dict) else {}
        default = str(entry.get("default_branch") or "").strip()
        if default:
            branches = dict(branches)
            branches[default] = new_sha
            entry["branches"] = branches
    if new_tag:
        entry["latest_tag"] = new_tag
    display = str(payload.get("display_version") or "").strip()
    if display:
        entry["display_version"] = display
    repos[key] = entry
    return not already


def apply_addon_pin(catalog: list[Any], payload: dict[str, Any]) -> bool:
    want = _repo_key(str(payload.get("id") or ""))
    new_tag = str(payload.get("new_tag") or "").strip()
    if not want or not new_tag:
        raise SystemExit("addon-pin payload needs id and new_tag")
    changed = False

    def bump(entry: dict[str, Any]) -> None:
        nonlocal changed
        repo = _repo_key(str(entry.get("repo") or entry.get("url") or ""))
        if repo != want:
            return
        prev = str(entry.get("pin_release") or "").strip()
        if prev != new_tag:
            changed = True
        entry["pin_release"] = new_tag

    for entry in catalog:
        if not isinstance(entry, dict):
            continue
        bump(entry)
        for fork in entry.get("forks") or []:
            if isinstance(fork, dict):
                bump(fork)
    if not changed and not any(
        _repo_key(str(e.get("repo") or e.get("url") or "")) == want
        for e in catalog
        if isinstance(e, dict)
    ):
        raise SystemExit(f"No addons.json row for {want}")
    return changed


def apply_action(
    payload: dict[str, Any],
    *,
    mods: Path | None,
    tips: Path | None,
    addons: Path | None,
    dry_run: bool,
) -> dict[str, Any]:
    kind = str(payload.get("kind") or "")
    changed = False
    target = ""
    if kind == KIND_MOD_PIN:
        if mods is None or not mods.is_file():
            raise SystemExit("mod-pin apply needs --mods")
        catalog = json.loads(mods.read_text(encoding="utf-8"))
        if not isinstance(catalog, list):
            raise SystemExit("mods.json must be a JSON array")
        changed = apply_mod_pin(catalog, payload)
        target = str(mods)
        if changed and not dry_run:
            mods.write_text(
                json.dumps(catalog, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
    elif kind == KIND_ADDON_TIP:
        if tips is None or not tips.is_file():
            raise SystemExit("addon-tip apply needs --tips")
        index = json.loads(tips.read_text(encoding="utf-8"))
        if not isinstance(index, dict):
            raise SystemExit("addon_tips.json must be an object")
        changed = apply_addon_tip(index, payload)
        target = str(tips)
        if changed and not dry_run:
            tips.write_text(
                json.dumps(index, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
    elif kind == KIND_ADDON_PIN:
        if addons is None or not addons.is_file():
            raise SystemExit("addon-pin apply needs --addons")
        catalog = json.loads(addons.read_text(encoding="utf-8"))
        if not isinstance(catalog, list):
            raise SystemExit("addons.json must be a JSON array")
        changed = apply_addon_pin(catalog, payload)
        target = str(addons)
        if changed and not dry_run:
            addons.write_text(
                json.dumps(catalog, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
    elif kind == KIND_SIGN:
        target = str(payload.get("path") or "")
        changed = False
    else:
        raise SystemExit(f"unknown action kind {kind!r}")

    ident = str(payload.get("id") or "")
    safe = re.sub(r"[^a-z0-9._-]+", "-", ident.lower()).strip("-")[:40]
    return {
        "skipped": not changed,
        "reason": "already_applied" if not changed else "",
        "kind": kind,
        "id": ident,
        "owner": kind,
        "repo_name": safe or "action",
        "repo": ident,
        "target": target,
        "sign_path": target,
    }


def pick_pr_url_for_head(prs: Any, branch: str) -> str:
    want = (branch or "").strip()
    if not want or not isinstance(prs, list):
        return ""
    for pr in prs:
        if not isinstance(pr, dict):
            continue
        if str(pr.get("headRefName") or "") == want:
            return str(pr.get("url") or "").strip()
    return ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--inspect", action="store_true", help="Print action kind JSON")
    ap.add_argument("--body-file", help="Issue body path")
    ap.add_argument("--mods", type=Path, help="mods.json path")
    ap.add_argument("--tips", type=Path, help="addon_tips.json path")
    ap.add_argument("--addons", type=Path, help="addons.json path")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--hold-tips", action="store_true")
    ap.add_argument("--published", type=Path, help="Published addon_tips.json")
    ap.add_argument("--candidate", type=Path, help="Freshly built addon_tips.json")
    ap.add_argument("--out", type=Path, help="Write held tips or issue list")
    ap.add_argument("--issues-out", type=Path, help="Write issue payloads JSON")
    ap.add_argument("--plan-upsert", action="store_true")
    ap.add_argument("--payloads", type=Path, help="JSON list of action payloads")
    ap.add_argument("--open-issues", type=Path, help="gh issue list JSON")
    ap.add_argument(
        "--pick-pr-head",
        metavar="BRANCH",
        help="Read gh pr list JSON on stdin; print matching URL",
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

    if args.hold_tips:
        if not args.published or not args.candidate:
            ap.error("--hold-tips needs --published and --candidate")
        published = json.loads(args.published.read_text(encoding="utf-8"))
        candidate = json.loads(args.candidate.read_text(encoding="utf-8"))
        pin_map = {}
        if args.addons and args.addons.is_file():
            pin_map = addon_pin_map(json.loads(args.addons.read_text(encoding="utf-8")))
        held, issues = hold_back_unpublished_tips(
            published, candidate, pin_map=pin_map
        )
        if args.out:
            args.out.write_text(
                json.dumps(held, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        if args.issues_out:
            args.issues_out.write_text(
                json.dumps(issues, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        print(json.dumps({"held_repos": len(held.get("repos") or {}), "issues": len(issues)}))
        return 0

    if args.plan_upsert:
        if not args.payloads:
            ap.error("--plan-upsert needs --payloads")
        payloads = json.loads(args.payloads.read_text(encoding="utf-8"))
        if isinstance(payloads, dict):
            payloads = payloads.get("drifted") or payloads.get("issues") or []
        open_issues: list[Any] = []
        if args.open_issues and args.open_issues.is_file():
            raw_open = json.loads(args.open_issues.read_text(encoding="utf-8"))
            open_issues = raw_open if isinstance(raw_open, list) else []
        plans = [
            plan_issue_upsert(open_issues, p)
            for p in payloads
            if isinstance(p, dict)
        ]
        text = json.dumps(plans, indent=2, ensure_ascii=False) + "\n"
        if args.out:
            args.out.write_text(text, encoding="utf-8")
        else:
            sys.stdout.write(text)
        return 0

    if not args.body_file:
        ap.error("--body-file is required unless --hold-tips / --plan-upsert / --pick-pr-head")

    body = Path(args.body_file).read_text(encoding="utf-8")
    payload = parse_action_payload(body)
    if args.inspect:
        if payload is None:
            print(json.dumps({"kind": "", "action": False}))
            return 0
        print(
            json.dumps(
                {
                    "kind": payload.get("kind"),
                    "action": True,
                    "id": payload.get("id") or payload.get("path") or "",
                    "payload": payload,
                }
            )
        )
        return 0

    if payload is None:
        raise SystemExit("Issue body is not a catalog-action payload")
    if args.apply or args.dry_run:
        summary = apply_action(
            payload,
            mods=args.mods,
            tips=args.tips,
            addons=args.addons,
            dry_run=bool(args.dry_run),
        )
        print(json.dumps(summary))
        return 0

    ap.error("pass --inspect, --apply, or --dry-run")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

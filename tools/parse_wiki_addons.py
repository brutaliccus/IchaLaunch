"""Parse Turtle WoW Fandom Addons wikitext into addons.json.

Fetches (or reads cached) wiki source covering:
- Featured + Full A–Z list bullet items
- SuperWoW Addons wiki tables
- Other GitHub-linked addon mentions outside archive collection dumps
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
WT = ROOT / "_wiki_addons.wikitext"
OUT = ROOT / "ichalaunch" / "data" / "addons.json"
WIKI_API = "https://turtle-wow.fandom.com/api.php"
UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}

LINK_ITEM = re.compile(
    r"^\*+\s*\[(https?://[^\s\]]+)\s+([^\]]+)\]\s*(?:[-–—:]\s*)?(.*)$",
    re.I,
)
# Featured-style: *'''Name:''' [url Label] (desc)
FEATURED_ITEM = re.compile(
    r"^\*+\s*'{2,3}([^':]+)[:\s]*'{2,3}\s*\[(https?://[^\s\]]+)\s+([^\]]+)\]\s*(?:\((.*)\))?\s*$",
    re.I,
)
WIKI_ITEM = re.compile(
    r"^\*+\s*\[\[([^|\]]+)(?:\|([^\]]+))?\]\]\s*(?:[-–—:]\s*(.*))?$",
)
BOLD_ITEM = re.compile(
    r"^\*+\s*'{2,3}([^']+)'{2,3}\s*(?:[-–—:]\s*(.*))?$",
)
SECTION = re.compile(r"^(={2,})\s*(.+?)\s*\1$")
LETTER = re.compile(r"^===.*<big>([A-Z])</big>.*===$", re.I)
TABLE_LINK = re.compile(
    r"\[(https?://(?:github|gitlab|codeberg)\.[^\s\]]+)\s+([^\]]+)\]",
    re.I,
)
# Skip mega-collection / archive rows (not installable single addons)
SKIP_SECTION_PREFIXES = (
    "further addons collections",
    "non-addon game modifications",
    "for addon developers",
    "looking to install",
    "about addons",
    "how to install",
    "how to update",
    "how to troubleshoot",
    "what is superwow",
    "how do i install superwow",
    "first steps to creating",
    "basic loop to write",
    "1.12 addon development",
    "library addons for addon developers",
    "addons in need of fixes",
)
SKIP_SECTION_SUBSTRINGS = (
    "addon developers",
    "development resources",
    "addons collections",
    "non-addon game",
)

# Extra known companions sometimes only mentioned in prose / mods docs
EXTRA_ENTRIES = [
    {
        "name": "UnitXP_SP3_Addon",
        "repo": "https://github.com/OldManAlpha/UnitXP_SP3_Addon",
        "category": "Client",
        "description": "Companion addon for UnitXP Service Pack 3 (nameplates, LoS helpers, Lua debugger package).",
        "source": "turtle_wiki",
        "folder": "UnitXP_SP3_Addon",
    },
]


def fetch_wikitext() -> str:
    r = requests.get(
        WIKI_API,
        params={"action": "parse", "page": "Addons", "prop": "wikitext", "format": "json"},
        headers=UA,
        timeout=120,
    )
    r.raise_for_status()
    return r.json()["parse"]["wikitext"]["*"]


def clean_url(url: str) -> str:
    url = (url or "").strip().rstrip(").,;")
    # Collapse accidental double slashes after host: github.com//owner/repo
    url = re.sub(r"(https?://[^/]+)//+", r"\1/", url)
    if url.endswith(".git"):
        url = url[:-4]
    # Drop /tree/... /blob/... /releases... suffixes → owner/repo
    for host in ("github.com", "gitlab.com", "codeberg.org"):
        m = re.match(
            rf"(https?://(?:www\.)?{host}/[^/]+/[^/#?\s]+)",
            url,
            re.I,
        )
        if m:
            base = m.group(1).rstrip("/")
            # Strip trailing path segments that aren't the repo name
            parts = base.split("/")
            # https://host/owner/repo[/...]
            if len(parts) >= 5:
                return "/".join(parts[:5])
            return base
    return url.split()[0] if url else ""


def strip_wiki(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"\{\{[^}]+\}\}", "", text)
    text = re.sub(r"<nowiki>(.*?)</nowiki>", r"\1", text, flags=re.I | re.S)
    text = re.sub(r"\[\[([^|\]]+)\|[^\]]+\]\]", r"\1", text)
    text = re.sub(r"\[\[([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"\[(https?://[^\s\]]+)\s+([^\]]+)\]", r"\2", text)
    text = re.sub(r"\[(https?://[^\s\]]+)\]", "", text)
    text = re.sub(r"'{2,}", "", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def guess_category(section: str, name: str, desc: str) -> str:
    sec = strip_wiki(section)
    s = f"{sec} {name} {desc}".lower()
    if "superwow" in sec.lower() or "superwow" in s and "require" in sec.lower():
        return "SuperWoW"
    if re.search(r"\bsuperwow\b", sec.lower()):
        return "SuperWoW"
    if re.fullmatch(r"[a-z]", sec.strip().lower() or "x"):
        pass
    rules = [
        (("quest", "pfquest", "tourguide"), "Questing"),
        (("raid", "loot", "atlasloot", "bigwigs", "threat", "rollfor"), "Raiding"),
        (("pvp", "battleground", "warsong", "av ", "ab "), "PvP"),
        (("profession", "craft", "trade skill", "tradeskill", "enchant"), "Professions"),
        (("auction", "aux", "mail", "vendor", "gold"), "Economy"),
        (("bag", "bank", "inventory"), "Bags"),
        (("map", "minimap", "cartograph", "flight"), "Maps"),
        (("unit frame", "nameplate", "plate", "hud", "ui ", "action bar", "bongos"), "UI"),
        (("damage", "dps", "heal", "combat", "threat", "aura", "buff", "debuff"), "Combat"),
        (("hardcore", "hc "), "Hardcore"),
        (("roleplay", " rp"), "Roleplay"),
        (("recommended", "essential", "featured"), "Recommended"),
        (("superwow", "superapi"), "SuperWoW"),
    ]
    for keys, cat in rules:
        if any(k in s for k in keys):
            return cat
    if len(sec.strip()) == 1:
        return "General"
    # Named thematic sections (Featured Addons, List of Addons, …)
    if sec.lower() in ("featured addons", "full addons list", "list of addons"):
        return "Recommended" if "featured" in sec.lower() else "General"
    return sec.strip() or "General"


def folder_from_repo(repo: str, name: str) -> str:
    specials = {
        "Aux-Revamped": "aux-addon",
        "Aux-addon": "aux-addon",
        "aux-addon": "aux-addon",
        "aux-addon-vanilla": "aux-addon",
        "Aux-Revamped-opaque": "aux-addon",
        "aux_merchant_prices": "aux_merchant_prices",
        "_LazyPig": "_LazyPig",
        "UnitFramesImproved_Vanilla": "UnitFramesImproved_Vanilla",
        "ShaguPlates-extra": "ShaguPlates",
        "roll-for-vanilla": "RollFor",
        "Rabuffs": "RABuffs",
        "AdvancedTradeSkillWindow2": "AdvancedTradeSkillWindow2",
        "Advanced-Trade-Skill-Window": "AdvancedTradeSkillWindow2",
        "pfQuest-turtle": "pfQuest-turtle",
        "ModernMapMarkers": "ModernMapMarkers",
        "SuperAPI": "SuperAPI",
        "SuperAPI_Castlib": "SuperAPI_Castlib",
        "UnitXP_SP3_Addon": "UnitXP_SP3_Addon",
        "aDF": "aDF",
        "oCB-SuperWoW": "oCB",
    }
    if not repo:
        return re.sub(r"\s+", "", name)
    leaf = repo.rstrip("/").split("/")[-1]
    leaf = leaf.split("?")[0]
    return specials.get(leaf, leaf)


def _skip_section(section: str) -> bool:
    s = strip_wiki(section).lower().strip()
    if any(s.startswith(p) for p in SKIP_SECTION_PREFIXES):
        return True
    return any(part in s for part in SKIP_SECTION_SUBSTRINGS)


def _is_collection_url(url: str) -> bool:
    """True for mega-addon collection repos (not a single installable addon)."""
    u = url.lower()
    needles = (
        "/addons-collection",
        "addons_collection",
        "classicaddons",
        "vanilla-addons",
        "wow-1.12.1-addons",
        "wow1.12.1_addons",
        "/addons-for-vanilla",
        "select_addons",
        "rootedcf/classicaddons",
        "road-block/select_addons",
    )
    return any(n in u for n in needles)


def _put(entries: dict[str, dict], entry: dict) -> None:
    name = entry.get("name") or ""
    if not name or len(name) < 2:
        return
    if name.lower().startswith("go to top"):
        return
    repo = entry.get("repo") or ""
    if repo and _is_collection_url(repo):
        return
    if repo and not any(h in repo for h in ("github.com", "gitlab.com", "codeberg.org", "gitea.com")):
        entry = dict(entry)
        entry["repo"] = ""
        entry["installable"] = False
        repo = ""
    if not repo:
        entry = dict(entry)
        entry["installable"] = False

    key = name.lower().strip()
    if key not in entries:
        entries[key] = entry
        return
    prev = entries[key]
    # Prefer entry with repo; prefer SuperWoW category when upgrading
    if repo and not prev.get("repo"):
        entries[key] = entry
    elif repo and prev.get("repo"):
        if entry.get("category") == "SuperWoW" and prev.get("category") != "SuperWoW":
            # Keep SuperWoW category / richer description when same addon re-listed
            merged = dict(prev)
            merged["category"] = "SuperWoW"
            if len(entry.get("description") or "") > len(prev.get("description") or ""):
                merged["description"] = entry["description"]
            entries[key] = merged
        elif len(entry.get("description") or "") > len(prev.get("description") or ""):
            prev["description"] = entry["description"]
    elif len(entry.get("description") or "") > len(prev.get("description") or ""):
        prev["description"] = entry["description"]


def _make_entry(name: str, repo: str, desc: str, section: str) -> dict:
    name = strip_wiki(name)
    desc = strip_wiki(desc)
    repo = clean_url(repo) if repo else ""
    return {
        "name": name,
        "repo": repo,
        "category": guess_category(section, name, desc),
        "description": (desc or name)[:220],
        "source": "turtle_wiki",
        "folder": folder_from_repo(repo, name),
    }


def _parse_table_rows(lines: list[str], start: int, section: str, entries: dict[str, dict]) -> int:
    """Parse a MediaWiki {| ... |} table of addon links. Returns index after table."""
    i = start
    n = len(lines)
    # Collect cells between |- separators
    row_cells: list[str] = []

    def flush_row() -> None:
        nonlocal row_cells
        if not row_cells:
            return
        # Find first cell with a github/gitlab link
        link_cell = ""
        other_cells: list[str] = []
        for cell in row_cells:
            if TABLE_LINK.search(cell):
                link_cell = cell
            else:
                other_cells.append(cell)
        row_cells = []
        if not link_cell:
            return
        m = TABLE_LINK.search(link_cell)
        if not m:
            return
        repo, label = m.group(1), m.group(2)
        # Description = last non-empty other cell (feature column may precede it)
        desc = ""
        for cell in reversed(other_cells):
            text = strip_wiki(cell)
            if text and text not in ("?", "-", "—"):
                desc = text
                break
        _put(entries, _make_entry(label, repo, desc, section))

    while i < n:
        line = lines[i].rstrip()
        if line.startswith("|}"):
            flush_row()
            return i + 1
        if line.startswith("|-"):
            flush_row()
            i += 1
            continue
        if line.startswith("!"):
            i += 1
            continue
        if line.startswith("|"):
            # New cell; may be ||-separated on one line
            raw = line.lstrip("|")
            parts = re.split(r"\|\|", raw)
            row_cells.extend(parts)
            i += 1
            continue
        i += 1
    flush_row()
    return i


def parse(wt: str) -> list[dict]:
    section = "General"
    entries: dict[str, dict] = {}
    lines = wt.splitlines()
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        stripped = line.strip()

        # Wiki tables (SuperWoW sections etc.)
        if stripped.startswith("{|"):
            if not _skip_section(section):
                i = _parse_table_rows(lines, i + 1, section, entries)
            else:
                # skip table
                i += 1
                while i < n and not lines[i].strip().startswith("|}"):
                    i += 1
                i += 1
            continue

        lm = LETTER.match(stripped)
        if lm:
            section = lm.group(1).upper()
            i += 1
            continue

        sm = SECTION.match(stripped)
        if sm:
            section = strip_wiki(sm.group(2))
            i += 1
            continue

        if _skip_section(section):
            i += 1
            continue

        name = desc = repo = ""
        m = FEATURED_ITEM.match(stripped)
        if m:
            # *'''Map Markers:''' [url Label] (desc) — prefer link label as name when useful
            featured_label = strip_wiki(m.group(1))
            repo = clean_url(m.group(2))
            link_name = strip_wiki(m.group(3))
            desc = strip_wiki(m.group(4) or "")
            name = link_name or featured_label
            if featured_label and featured_label.lower() not in (name or "").lower():
                desc = desc or featured_label
        else:
            m = LINK_ITEM.match(stripped)
            if m:
                repo = clean_url(m.group(1))
                name = strip_wiki(m.group(2))
                desc = strip_wiki(m.group(3) or "")
            else:
                m = WIKI_ITEM.match(stripped)
                if m:
                    name = strip_wiki(m.group(2) or m.group(1))
                    desc = strip_wiki(m.group(3) or "")
                    urls = re.findall(
                        r"https?://(?:github|gitlab|codeberg)\.[^\s\]\|<>\"]+",
                        line,
                        re.I,
                    )
                    if urls:
                        repo = clean_url(urls[0])
                else:
                    m = BOLD_ITEM.match(stripped)
                    if m:
                        name = strip_wiki(m.group(1))
                        desc = strip_wiki(m.group(2) or "")
                        urls = re.findall(
                            r"https?://(?:github|gitlab|codeberg)\.[^\s\]\|<>\"]+",
                            line,
                            re.I,
                        )
                        if urls:
                            repo = clean_url(urls[0])
                    else:
                        i += 1
                        continue

        _put(entries, _make_entry(name, repo, desc, section))
        i += 1

    for extra in EXTRA_ENTRIES:
        _put(entries, dict(extra))

    return sorted(entries.values(), key=lambda x: (x.get("category", ""), x["name"].lower()))


def main() -> None:
    ap = argparse.ArgumentParser(description="Parse Turtle WoW Addons wiki → addons.json")
    ap.add_argument("--fetch", action="store_true", help="Download fresh wikitext from Fandom API")
    ap.add_argument("--no-write-cache", action="store_true", help="Do not overwrite _wiki_addons.wikitext")
    args = ap.parse_args()

    if args.fetch or not WT.exists():
        print(f"Fetching {WIKI_API} page=Addons …")
        wt = fetch_wikitext()
        if not args.no_write_cache:
            WT.write_text(wt, encoding="utf-8")
            print(f"Cached wikitext → {WT} ({len(wt)} chars)")
    else:
        wt = WT.read_text(encoding="utf-8")
        print(f"Using cached wikitext {WT}")

    before = 0
    if OUT.exists():
        try:
            before = len(json.loads(OUT.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            before = 0

    catalog = parse(wt)
    with_repo = sum(1 for e in catalog if e.get("repo"))
    OUT.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(catalog)} addons ({with_repo} with installable repos) -> {OUT}")
    print(f"Delta vs previous file: {len(catalog) - before:+d}")
    cats: dict[str, int] = {}
    for e in catalog:
        cats[e.get("category", "?")] = cats.get(e.get("category", "?"), 0) + 1
    for k, v in sorted(cats.items(), key=lambda kv: -kv[1]):
        print(f"  {k}: {v}")
    # Spot-check SuperAPI
    for e in catalog:
        if "superapi" in (e.get("name") or "").lower() or (e.get("folder") or "") == "SuperAPI":
            print("SuperAPI entry:", json.dumps(e, ensure_ascii=False))


if __name__ == "__main__":
    main()

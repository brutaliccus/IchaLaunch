"""Parse Turtle WoW Fandom Addons wikitext into addons.json."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WT = ROOT / "_wiki_addons.wikitext"
OUT = ROOT / "ichalaunch" / "data" / "addons.json"

LINK_ITEM = re.compile(
    r"^\*+\s*\[(https?://[^\s\]]+)\s+([^\]]+)\]\s*(?:[-–—:]\s*(.*))?$",
    re.I,
)
WIKI_ITEM = re.compile(
    r"^\*+\s*\[\[([^|\]]+)(?:\|([^\]]+))?\]\]\s*(?:[-–—:]\s*(.*))?$",
)
BOLD_ITEM = re.compile(
    r"^\*+\s*'{2,3}([^']+)'{2,3}\s*(?:[-–—:]\s*(.*))?$",
)
SECTION = re.compile(r"^==+\s*(.+?)\s*==+$")
LETTER = re.compile(r"^===.*<big>([A-Z])</big>.*===$", re.I)


def clean_url(url: str) -> str:
    url = url.strip().rstrip(").,;")
    if url.endswith(".git"):
        url = url[:-4]
    for host in ("github.com", "gitlab.com", "codeberg.org"):
        m = re.match(rf"(https?://(?:www\.)?{host}/[^/]+/[^/#?\s]+)", url, re.I)
        if m:
            return m.group(1)
    return url.split()[0]


def strip_wiki(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"\{\{[^}]+\}\}", "", text)
    text = re.sub(r"\[\[([^|\]]+)\|[^\]]+\]\]", r"\1", text)
    text = re.sub(r"\[\[([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"\[(https?://[^\s\]]+)\s+([^\]]+)\]", r"\2", text)
    text = re.sub(r"\[(https?://[^\s\]]+)\]", "", text)
    text = re.sub(r"'{2,}", "", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def guess_category(section: str, name: str, desc: str) -> str:
    s = f"{section} {name} {desc}".lower()
    if re.fullmatch(r"[a-z]", section.strip().lower() or "x"):
        # letter bucket — classify by keywords
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
        (("recommended", "essential"), "Recommended"),
    ]
    for keys, cat in rules:
        if any(k in s for k in keys):
            return cat
    if len(section.strip()) == 1:
        return "General"
    return section.strip() or "General"


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
    }
    if not repo:
        return name.replace(" ", "")
    leaf = repo.rstrip("/").split("/")[-1]
    return specials.get(leaf, leaf)


def parse() -> list[dict]:
    wt = WT.read_text(encoding="utf-8")
    section = "General"
    entries: dict[str, dict] = {}

    for line in wt.splitlines():
        lm = LETTER.match(line)
        if lm:
            section = lm.group(1).upper()
            continue
        sm = SECTION.match(line)
        if sm:
            section = strip_wiki(sm.group(1))
            continue

        name = desc = repo = ""
        m = LINK_ITEM.match(line.strip())
        if m:
            repo = clean_url(m.group(1))
            name = strip_wiki(m.group(2))
            desc = strip_wiki(m.group(3) or "")
        else:
            m = WIKI_ITEM.match(line.strip())
            if m:
                name = strip_wiki(m.group(2) or m.group(1))
                desc = strip_wiki(m.group(3) or "")
                # try find url elsewhere on line
                urls = re.findall(r"https?://(?:github|gitlab|codeberg)\.[^\s\]\|<>\"]+", line, re.I)
                if urls:
                    repo = clean_url(urls[0])
            else:
                m = BOLD_ITEM.match(line.strip())
                if m:
                    name = strip_wiki(m.group(1))
                    desc = strip_wiki(m.group(2) or "")
                    urls = re.findall(r"https?://(?:github|gitlab|codeberg)\.[^\s\]\|<>\"]+", line, re.I)
                    if urls:
                        repo = clean_url(urls[0])
                else:
                    continue

        if not name or len(name) < 2:
            continue
        if name.lower().startswith("go to top"):
            continue

        # skip image-only / junk hosts for repo
        if repo and not any(h in repo for h in ("github.com", "gitlab.com", "codeberg.org", "gitea.com")):
            repo = ""

        key = name.lower()
        entry = {
            "name": name,
            "repo": repo,
            "category": guess_category(section, name, desc),
            "description": (desc or name)[:220],
            "source": "turtle_wiki",
            "folder": folder_from_repo(repo, name),
        }
        if not repo:
            entry["installable"] = False

        if key not in entries:
            entries[key] = entry
        else:
            # prefer entry with repo
            if repo and not entries[key].get("repo"):
                entries[key] = entry
            elif len(desc) > len(entries[key].get("description") or ""):
                entries[key]["description"] = entry["description"]

    return sorted(entries.values(), key=lambda x: (x.get("category", ""), x["name"].lower()))


def main():
    catalog = parse()
    with_repo = sum(1 for e in catalog if e.get("repo"))
    OUT.write_text(json.dumps(catalog, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {len(catalog)} addons ({with_repo} with installable repos) -> {OUT}")
    cats: dict[str, int] = {}
    for e in catalog:
        cats[e.get("category", "?")] = cats.get(e.get("category", "?"), 0) + 1
    for k, v in sorted(cats.items(), key=lambda kv: -kv[1]):
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()

# Sign and publish live catalogs

Operational steps for `brutaliccus/IchaLaunch-dev`. Run every command from the
**repo root** (not a `python tools` folder). Private keys never enter CI.

Players fetch four files from public `brutaliccus/IchaLaunch` `master`. Each
payload change needs a **new sibling `.sig`**. Fail-closed clients ignore
unsigned or bad-sig live JSON and keep cache, then the copy bundled in the EXE.

| File | What it is |
| --- | --- |
| `ichalaunch/data/addons.json` | Available addon catalog |
| `ichalaunch/data/addon_tips.json` | Addon version / tip index |
| `ichalaunch/data/home_art.json` | Home artwork index |
| `ichalaunch/data/mods.json` | Client-tab mods (pins, dest hashes) |

**Client-tab updates come only from `mods.json`.** The tip index does not
drive ClassicAPI or other client mods. Players see a pin or addon version only
after the **signed** public file actually changes.

Signing old JSON republishes the same pins. It does **not** rehash ClassicAPI
to a new GitHub release.

## Key (local only)

Default PEM: `%LOCALAPPDATA%\IchaLaunch\signing\ichalaunch-key1.pem`

`sign_live.py` / `sign.py` prompt for the key password. Do not write that
password into this repo, a README, or a GitHub secret.

- Catalog JSON + `.sig` → purpose `ichalaunch-catalog`
- `IchaLaunch.exe` + `.sig` → purpose `ichalaunch-launcher-update` (GitHub
  Release, not under `ichalaunch/data/`)

## Interactive signer

```bat
python tools/sign_live.py
```

Prompts yes/no per file (Enter skips). Yes writes `<file>.sig` beside the
JSON, then opens a public PR on `brutaliccus/IchaLaunch` branch
`sign/live-catalogs` with only the files you accepted.

```bat
python tools/sign_live.py --only mods
python tools/sign_live.py --only addons,addon_tips
python tools/sign_live.py --dry-run --yes-all
```

Single-file fallback:

```bat
python tools/sign.py --key %LOCALAPPDATA%\IchaLaunch\signing\ichalaunch-key1.pem ichalaunch/data\mods.json
```

Merge **JSON and `.sig` together**. Do not merge unsigned JSON to public
`master`.

## How pin drift is detected

Daily on this repo (`mod-pin-check.yml`, 08:00 UTC, plus **Actions → Check
catalog pins and signatures → Run workflow**):

1. `python tools/pin_mods.py --check` downloads each pinned GitHub/raw asset
   and compares SHA-256 to `mods.json`.
2. On drift it already has the new tag and hash (same download `--update`
   would write). It does **not** commit `mods.json`.
3. It opens or comments one public issue per id, title `[mod-pin] classic_api`
   (label `catalog-action`), body includes `new_tag` + `new_sha256`.

Until this branch is **merged to IchaLaunch-dev `master`**, the scheduled job
on master is the older private “Mod pin drift” issue only. Dispatch the new
workflow **from this branch**, or rehash locally (below).

Hourly `addon-tips.yml` holds unpublished addon version bumps and opens
`[addon-tip]` / `[addon-pin]` issues the same way. `[sign] <file>` means the
live JSON has no `.sig`.

## Approve → JSON-only PR → sign → merge

Issues live on **public** `brutaliccus/IchaLaunch`. One label:
`catalog-approved`. Public `catalog-relay.yml` dispatches this repo’s
`catalog-approve.yml`.

1. Review the issue (`[mod-pin]`, `[addon-tip]`, `[addon-pin]`, or `[catalog]`).
2. Add label **`catalog-approved`** (or **Actions → Catalog approve → public PR**
   with the public issue number).
3. The job writes the JSON only (`mods.json` pin, tip index, addon pin, or new
   addon row) on a public branch and **stops**. It does not squash-merge.
4. Sign that payload locally (`sign_live.py` or `sign.py`), commit the `.sig`
   on the **same** PR, merge JSON+`.sig` together.

For `[mod-pin]`, apply writes `source.sha256` + `source.pinned_tag` (and dest
hashes only if that catalog row already uses them). Same numbers as
`python tools/pin_mods.py --update <id>`.

`[sign]` issues have no JSON diff: sign the current public file and PR the
`.sig` only.

## Rehash now (skip the issue)

From repo root, after you trust the new upstream bytes:

```bat
python tools/pin_mods.py --update classic_api
python tools/sign_live.py --only mods
```

`--update --all` re-pins every drifted row. Commit `mods.json` on this repo
so the next check matches. Then merge the public `sign/live-catalogs` PR
(JSON+`.sig`).

## Typical days

**ClassicAPI (or any client mod) new GitHub release**

1. Confirm drift: `python tools/pin_mods.py --check` (or the `[mod-pin]` issue).
2. Rehash: approve the issue **or** `--update classic_api`.
3. Sign `mods.json`, merge JSON+`.sig` on public `master`.
4. Commit the same `mods.json` here.

**Existing addon new tag**

1. `[addon-tip]` / `[addon-pin]` on public IchaLaunch (hourly job holds it
   back until you approve).
2. Label `catalog-approved` → public JSON PR → sign `addon_tips.json` or
   `addons.json` → merge JSON+`.sig`.

**First-time catalog suggestion**

1. Worker opens `[catalog] …` on public IchaLaunch.
2. Label `catalog-approved` → public PR edits `addons.json` only → sign →
   merge JSON+`.sig`.

## Do not

- Put the signing key or its password in CI, git, or this doc.
- Merge unsigned live JSON to public `master`.
- Expect signing yesterday’s `mods.json` to bump ClassicAPI (or any pin).
- Treat the addon tip index as a Client-tab update.
- Auto-merge catalog PRs from Actions (approve opens the PR; you sign).

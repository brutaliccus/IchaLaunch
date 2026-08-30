# Private development repo

Day-to-day work lives here (`brutaliccus/IchaLaunch-dev`). Public
`brutaliccus/IchaLaunch` is a publish surface: GitHub Releases, live catalog
JSON, home-art files that shipped clients already pin, and catalog/crash
issues.

Do not advertise this private URL from the public README.

## Remotes (this checkout)

```
origin   https://github.com/brutaliccus/IchaLaunch-dev.git     # default
public   https://github.com/brutaliccus/IchaLaunch.git         # releases + catalog
```

Push feature branches to `origin`. Do not push application source to `public`.

## What shipped clients pin (do not change)

- Releases: `brutaliccus/IchaLaunch` (`LAUNCHER_REPO`)
- Catalog / tips / home art:
  `raw.githubusercontent.com/brutaliccus/IchaLaunch/master/ichalaunch/data/…`
- Suggest + crash Worker: `https://ichalaunch-addon-submit.ichalaunch.workers.dev`
- Worker still opens issues on **public** `brutaliccus/IchaLaunch`
  (catalog suggestions; crash comments on #58 Windows / #59 Linux)

## Secrets

Create one fine-grained PAT on `brutaliccus` and store it in both repos
(or two PATs with the scopes below).

| Secret | Repo | Token needs |
| --- | --- | --- |
| `DEV_DISPATCH_TOKEN` | **public** `brutaliccus/IchaLaunch` | **Contents: Read and write** on `IchaLaunch-dev` (required for `repository_dispatch`; Actions write is not enough) |
| `PUBLIC_PUSH_TOKEN` | **private** `brutaliccus/IchaLaunch-dev` | Contents, Issues, Pull requests on `IchaLaunch` |

Until both secrets exist, catalog-approve, hourly tips, and the public
draft-release workflow will fail closed.

Public repo setting (once): **Settings → Actions → General → Workflow
permissions → Allow GitHub Actions to create and approve pull requests**.

Public `master` rulesets (do not replace the second with an `update` block):

- **Protect master: no force-push or delete**
- **Master via pull request** — 0 reviews, squash only. An `update` rule
  returns `Cannot update this protected ref` on squash-merge, so the
  approve bot opens PRs and never lands them.

Day-to-day pin, approve, and sign steps (ClassicAPI rehash, `sign_live.py`,
what not to do): [`SIGNING.md`](SIGNING.md).

## Catalog suggestions and version/pin actions

Issues live on **public** `brutaliccus/IchaLaunch`. One label:
`catalog-approved`. Public `catalog-relay.yml` dispatches this repo.

1. **New addon** (`[catalog]`): Worker → issue → label → this repo opens a
   public PR that edits `addons.json` only, then **stops**.
2. **Client-mod pin drift** (`[mod-pin] <id>`): daily
   `mod-pin-check.yml` compares live GitHub assets to `mods.json` via
   `tools/pin_mods.py` (same resolver). One open issue per mod id.
3. **Existing addon version** (`[addon-tip]` / `[addon-pin]`): hourly
   `addon-tips.yml` builds a candidate tip index, **holds back** unpublished
   `latest_tag` / sha changes, and opens one issue per repo. Catalog-pinned
   addons (`pin_release` / `updates: false`) get `[addon-pin]`.
4. **Unsigned live file** (`[sign] <file>`): same daily job if a published
   catalog JSON exists without a `.sig`.

The approve job never squash-merges unsigned catalog JSON to public master.
It opens or updates the PR and stops. Signing keys must not enter CI.

Retry from this repo: **Actions → Catalog approve → public PR** with the
public issue number.

## Sign locally, then merge JSON + `.sig`

Every live fetch (`addons.json`, `addon_tips.json`, `home_art.json`,
`mods.json`) needs a **new `.sig` whenever the payload changes**. Purpose
is `ichalaunch-catalog` (not `ichalaunch-launcher-update`).

```
python tools/sign_live.py
```

Prompts yes/no per file (Enter skips). Yes signs with the key at
`%LOCALAPPDATA%\IchaLaunch\signing\ichalaunch-key1.pem`, writes `<file>.sig`
beside it, then opens a public PR on `brutaliccus/IchaLaunch` branch
`sign/live-catalogs` with only the files you accepted. The EXE `.sig` stays
on the GitHub Release — the script offers `publish_public_release.py` instead
of committing it under `ichalaunch/data/`. `--dry-run` prints the plan.
`--yes-all` / `--only addons,mods` skip prompts.

Single-file fallback:

```
python tools/sign.py --key %LOCALAPPDATA%\IchaLaunch\signing\ichalaunch-key1.pem ichalaunch/data\mods.json
```

Merge JSON+`.sig` together. Fail-closed clients ignore unsigned or bad-sig
live files (cache, then bundled). Client zip (`client_zip.sha256` /
`client_manifest.json`) is a 10 GB pin: re-hash by hand when that zip is
republished.

## Hourly addon / tip refresh

`.github/workflows/addon-tips.yml` builds a candidate index, holds unpublished
version bumps (action issues instead), and opens or updates a **standing PR**
(`catalog/unsigned-tips`) for leftover unsigned tips / download stamps. It
does **not** push `addon_tips.json` or stamped `addons.json` to public master.

## Client-mod catalog (signed `mods.json`)

Client-tab update nags read `ichalaunch/data/mods.json` only (pins, dest
hashes, bundled local hashes). They never use the addon tip index. Live
`mods.json` is fetched like the other three catalogs: verified `.sig` or
unused. Players do not see a client-mod or addon version until the signed
public file actually changes.

## Public launcher release

Sign locally (never in CI). `sign_live.py` can do the EXE prompt as well, or:

```
python tools/sign.py --key %LOCALAPPDATA%\IchaLaunch\signing\ichalaunch-key1.pem dist\IchaLaunch.exe
python tools/publish_public_release.py --tag vX.Y.Z --exe dist\IchaLaunch.exe --sig dist\IchaLaunch.exe.sig
```

The EXE `.sig` must include an `ichalaunch-launcher-update` attestation
(`version` + `sha256`). New clients refuse a genuine old EXE republished
under a newer tag. Shipped clients before that check still accept it
until they update once.

Optional, before you build the EXE, refresh bundled fallbacks from live public
data:

```
python tools/publish_public_release.py --sync-public-data
```

That copies `addons.json`, `addon_tips.json`, and `home_art.json` from
`public/master` into this tree. Commit them if you want the bundled copies to
match what clients already fetch.

CI **Actions → Publish public release** only opens a **draft** on the public
repo. Attach the signed EXE with the script above.

## Cloudflare Worker

Source: `tools/addon-submit-worker/`. Deploy from that folder:

```
wrangler deploy
```

Worker secrets stay on Cloudflare (do not put tokens in git):

```
wrangler secret put GITHUB_TOKEN
wrangler secret put GITHUB_REPO
```

`GITHUB_REPO` must remain `brutaliccus/IchaLaunch`. The workers.dev URL must
remain `https://ichalaunch-addon-submit.ichalaunch.workers.dev`. Redeploy only
after Worker source changes.

## Public `master` is thin

The public tip of `master` is README, screenshots, live JSON, raw theme
images, and the catalog relay workflow. Application source history remains in
older public commits; we do not rewrite that history.

## Team access

Add collaborators on `brutaliccus/IchaLaunch-dev` in GitHub settings. Catalog
and crash issues stay on the public repo.

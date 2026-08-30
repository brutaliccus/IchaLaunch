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

## Catalog suggestions

1. Users submit from the launcher (Cloudflare Worker → public issue).
2. On public `brutaliccus/IchaLaunch`, label the issue `catalog-approved`.
3. Public `catalog-relay.yml` dispatches this repo.
4. This repo’s `catalog-approve.yml` writes `ichalaunch/data/addons.json` on
   **public** master and squash-merges.

Retry a stuck issue from this repo: **Actions → Catalog approve → public PR →
Run workflow** with the public issue number.

## Hourly addon / tip refresh

`.github/workflows/addon-tips.yml` (this repo) reads live public `addons.json`
plus this repo’s `mods.json`, then pushes `addon_tips.json` and stamped
download counts to public master.

## Mod pin drift

`.github/workflows/mod-pin-check.yml` runs `python tools/pin_mods.py --check`
daily. Drift fails the job and opens a private issue. Re-pin is **manual**:

```
python tools/pin_mods.py --update <mod-id>
```

Test the new bytes in-game, then commit `ichalaunch/data/mods.json` here.
Pins ship inside the next signed EXE; they are not hot-fetched.

## Public launcher release

Sign locally (never in CI):

```
python tools/sign.py --key %LOCALAPPDATA%\IchaLaunch\signing\ichalaunch-key1.pem dist\IchaLaunch.exe
python tools/publish_public_release.py --tag vX.Y.Z --exe dist\IchaLaunch.exe --sig dist\IchaLaunch.exe.sig
```

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

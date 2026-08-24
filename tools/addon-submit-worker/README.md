# Catalog suggestion Worker

Minimal HTTPS endpoint for **IchaLaunch → Suggest for catalog**.

The launcher POSTs JSON here (no GitHub credentials). This Worker validates the
payload, rate-limits, and opens a GitHub Issue on `brutaliccus/IchaLaunch` using
**Worker secrets** only.

## Setup

1. Install [Wrangler](https://developers.cloudflare.com/workers/wrangler/install-and-update/) and log in:
   ```bash
   npm i -g wrangler
   wrangler login
   ```
2. From this folder, create the Worker (first deploy creates the script):
   ```bash
   cd tools/addon-submit-worker
   wrangler deploy
   ```
3. Put secrets (never commit these):
   ```bash
   wrangler secret put GITHUB_TOKEN
   wrangler secret put GITHUB_REPO
   ```
   - `GITHUB_TOKEN` — fine-grained PAT with **Issues: Read and write** on
     `brutaliccus/IchaLaunch` (and Metadata read). Classic `public_repo` also
     works but is broader than needed.
   - `GITHUB_REPO` — `brutaliccus/IchaLaunch` (required if you do not rely on
     the Worker default).
4. Confirm the Worker HTTPS URL matches `ADDON_SUBMIT_URL` in
   `ichalaunch/addons/submit.py`
   (`https://ichalaunch-addon-submit.ichalaunch.workers.dev`). Clients use that
   constant only — there is no Settings URL field.

## Labels

Issue titles are prefixed with `[catalog]`. Optionally create a
`catalog-suggestion` label on the repo and add it in `src/index.js` if you want
filtered triage.

**Approve for catalog:** label the issue **`catalog-approved`**. That triggers
`.github/workflows/catalog-approve.yml`, which opens a PR editing only
`ichalaunch/data/addons.json`, squash-merges it, comments the result, and closes
the issue. Spam submissions stay as issues until you approve.

## Promote issue → `addons.json`

1. Review the issue (repo exists, Turtle-compatible, category fits).
2. Label the issue **`catalog-approved`**.
3. The Action opens `catalog: add Owner/Repo`, squash-merges to `master`,
   comments the merged PR on the issue, and closes it.
4. Clients pick up the entry on the next Available catalog refresh (no launcher
   rebuild).

The Action skips if the repo URL is already in `addons.json` (no PR / no merge)
and comments the skip reason on the issue.

**Repo setting (once):** Settings → Actions → General → Workflow permissions →
enable **Allow GitHub Actions to create and approve pull requests** so
`GITHUB_TOKEN` can open and merge the PR. Master is currently unprotected;
if you add required reviews later, allow `github-actions[bot]` to bypass or
`gh pr merge --admin` needs a token that can.

## Example request body

```json
{
  "repo": "https://github.com/owner/MyAddon",
  "name": "MyAddon",
  "category": "General",
  "description": "# MyAddon\n\nREADME excerpt used as the issue description…",
  "folder": "MyAddon",
  "launcher_version": "1.2.11",
  "client_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
}
```

`name` and `folder` may be omitted or empty — the Worker fills them from the
repo slug. `description` is typically a truncated README excerpt (max 4000
chars). `repo` and `category` are required. `client_id` is an anonymous UUID
for rate-limit hints only.

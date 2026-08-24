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
`.github/workflows/catalog-approve.yml`, which opens a draft PR editing only
`ichalaunch/data/addons.json`. Spam submissions stay as issues until you approve.

## Promote issue → `addons.json`

1. Review the issue (repo exists, Turtle-compatible, category fits).
2. Label the issue **`catalog-approved`**.
3. Review the draft PR the Action opens (`catalog: add Owner/Repo`).
4. **Merge the PR** — that puts the entry on `master`. Clients pick it up on the
   next Available catalog refresh (no launcher rebuild).
5. Closing the issue alone does **not** update the catalog.

The Action skips opening a PR if the repo URL is already in `addons.json`, and
comments the PR link (or skip reason) on the issue.

## Example request body

```json
{
  "repo": "https://github.com/owner/MyAddon",
  "name": "MyAddon",
  "category": "General",
  "description": "Short blurb for the Available list",
  "folder": "MyAddon",
  "launcher_version": "1.2.10",
  "client_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
}
```

`folder`, `name`, and `description` may be empty strings; `repo` and `category`
are required. `client_id` is an anonymous UUID for rate-limit hints only.

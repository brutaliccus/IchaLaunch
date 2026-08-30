# Catalog suggestion + crash-report Worker

Minimal HTTPS endpoint for **IchaLaunch → Suggest for catalog** and optional
**opt-in crash / error reports**.

The launcher POSTs JSON here (no GitHub credentials). This Worker validates the
payload, rate-limits, and talks to GitHub using **Worker secrets** only.

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
   wrangler secret put CRASH_ISSUE_WINDOWS   # optional; default 58
   wrangler secret put CRASH_ISSUE_LINUX     # optional; default 59
   ```
   - `GITHUB_TOKEN` — fine-grained PAT with **Issues: Read and write** on
     **public** `brutaliccus/IchaLaunch` (and Metadata read), plus ability to
     read public repo metadata for fork/parent resolution. Classic
     `public_repo` also works but is broader than needed.
   - `GITHUB_REPO` — must stay `brutaliccus/IchaLaunch` (the public release
     repo). Do not point this at the private dev repo.
   - `CRASH_ISSUE_WINDOWS` / `CRASH_ISSUE_LINUX` — sticky issues that receive
     crash **comments** by OS
     ([Windows #58](https://github.com/brutaliccus/IchaLaunch/issues/58),
     [Linux #59](https://github.com/brutaliccus/IchaLaunch/issues/59)).
4. Confirm the Worker HTTPS URL matches `ADDON_SUBMIT_URL` in
   `ichalaunch/addons/submit.py`
   (`https://ichalaunch-addon-submit.ichalaunch.workers.dev`). Clients use that
   constant only — there is no Settings URL field. Crash reports POST to
   `/crash` on the same host.

## Crash reports (comment chain)

`POST /crash` appends a markdown comment to the sticky crash-log issue for that
OS (**Windows** or **Linux**). It does **not** open a new Issue per report.
Clients enable this in Settings (off by default). Rate-limited separately from
catalog submits.

## Labels

Issue titles for catalog suggestions are prefixed with `[catalog]`. Optionally create a
`catalog-suggestion` label on the repo and add it in `src/index.js` if you want
filtered triage.

**Approve for catalog:** on **public** `brutaliccus/IchaLaunch`, label the
issue **`catalog-approved`**. The public relay dispatches the private
`.github/workflows/catalog-approve.yml` job, which opens a PR editing only
public `ichalaunch/data/addons.json`, squash-merges it, comments the result,
and closes the issue. Spam submissions stay as issues until you approve.

## Review queue (submit time)

On every suggestion (in-app **Suggest for catalog**, git-import auto-submit, or
selecting an uncatalogued fork in the launcher), the Worker:

1. Loads the submitted repo via the GitHub API.
2. Resolves the **network root** (`source` if present, else `parent`, else the
   repo itself when it is not a fork).
3. Opens a `[catalog]` issue for:
   - the root (main / canonical repo)
   - the submitted repo **only when it is a different, requested fork**
4. Does **not** enumerate or enqueue every active fork. The launcher dropdown
   still lists forks; those become review items only when a user selects /
   suggests that fork.
5. Skips a repo if its URL is already in live `addons.json` (primary or nested
   `forks[]`) or already mentioned in an open `[catalog]` issue.
6. Returns **one** success response to the client; the extra root issue (when
   a fork was requested) is best-effort.

Token needs **Issues: Read and write** on this repo plus read access to public
repo metadata (parent / source). Classic `public_repo` also works.

**Redeploy** this Worker after changing review-queue logic for live Suggest to
pick up the new filter.

## Promote issue → `addons.json`

1. Review the issue on **public** `brutaliccus/IchaLaunch` (repo exists,
   Turtle-compatible, category fits).
2. Label the issue **`catalog-approved`**.
3. The public relay dispatches the private approve job, which opens
   `catalog: add Owner/Repo` on the public repo, squash-merges to `master`,
   comments the merged PR on the issue, and closes it.
4. Clients pick up the entry on the next Available catalog refresh (no launcher
   rebuild).

The approve job skips if the repo URL is already in public `addons.json`
(no PR / no merge) and comments the skip reason on the issue.

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
  "launcher_version": "1.2.12",
  "client_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
}
```

`name` and `folder` may be omitted or empty — the Worker fills them from the
repo slug. `description` is typically a truncated README excerpt (max 4000
chars). `repo` and `category` are required. `client_id` is an anonymous UUID
for rate-limit hints only.

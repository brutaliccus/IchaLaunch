# IchaLaunch

[![Release](https://img.shields.io/github/v/release/brutaliccus/IchaLaunch?label=version)](https://github.com/brutaliccus/IchaLaunch/releases/latest)

**IchaLaunch** installs a game client, manages addons and client mods, and launches the game — with a themed window for **HOME**, **ADDONS**, **CLIENT**, **SETTINGS**, and **PLAY**.

> **Fresh install:** Use the launcher **INSTALL** flow for a clean client. Do **not** point IchaLaunch at an older or leftover game folder.

Download **`IchaLaunch.exe`** from **[Releases](https://github.com/brutaliccus/IchaLaunch/releases/latest)**.

---

## Features

- **Install & play** — guided client install, realm setup, then **PLAY** with pre-launch checks
- **Addons** — browse the catalog, search/filter, install with fork + version + README preview, or paste any public GitHub repo
- **Update addons** — check and update one or all; unload without deleting from disk
- **Suggest for catalog** — propose a public addon for the shared Available list (no GitHub login in the client)
- **Live catalog** — Available list refreshes from the repo on a short interval; new entries appear without a new launcher build
- **Client mods & HD patches** — performance fixes, graphics backends, hooks, HD variants, sky packs, and more; **Apply Changes** or let **PLAY** sync
- **Settings** — game / AddOns paths, launch preferences, optional GitHub token, permissions check, reset client link
- **Self-update** — when a newer Windows build is on Releases, update in place from the launcher

---

## Screenshots

### HOME

![Home](docs/screenshots/home.png)

### ADDONS

![Addons](docs/screenshots/addons.png)

### CLIENT

![Client](docs/screenshots/client.png)

### SETTINGS

![Settings](docs/screenshots/settings.png)

Progress while checking updates:

![Home with loading bar](docs/screenshots/home-loading.png)

Themed dialogs:

![Themed dialog](docs/screenshots/themed_dialog.png)

---

## Get started

1. Open the latest release: [github.com/brutaliccus/IchaLaunch/releases/latest](https://github.com/brutaliccus/IchaLaunch/releases/latest)
2. Download **`IchaLaunch.exe`**
3. Run it (no installer). Windows SmartScreen may ask — **More info** → **Run anyway** if you trust the release
4. **INSTALL** a fresh client, or point **SETTINGS** at a folder that already contains the game executable
5. Use **ADDONS** / **CLIENT** as needed, then **PLAY**

**Install tip:** Prefer a simple path like `D:\Games`. Avoid Program Files, Desktop, Downloads, and Documents.

**Platforms:** Native **Windows 10/11** (64-bit) EXE. **Linux from source** can launch the Windows client via umu-launcher / Proton (set paths in Settings). Running the EXE under Proton/Wine is **not** supported.

---

## Troubleshooting

**Won't start / missing DLLs (`icuuc.dll`, `Qt6Core.dll`)**  
Run **`IchaLaunch.exe` on Windows**, not under Proton/Wine. Install the latest **[VC++ Redistributable (x64)](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist)** if needed.

**Defender / Controlled Folder Access blocks mods**  
Allow the launcher, launch helper, and game executable — or move the game out of a protected folder. Use **Check Game Permissions** in Settings. For hook-DLL issues, add the game folder as a Windows Security exclusion, then disable/re-enable the mod on **CLIENT** and **Apply**.

**PLAY does nothing / “Client not found”**  
Confirm **SETTINGS** points at the folder with the game executable, then **Verify**.

**Gold dots on ADDONS or CLIENT**  
Pending updates or unapplied changes — open the tab and update / apply.

More help: **[Releases](https://github.com/brutaliccus/IchaLaunch/releases)**.

---

<details>
<summary>Build from source</summary>

```bat
python -m pip install -r requirements.txt
python run.py
```

Build the EXE:

```bat
python -m PyInstaller IchaLaunch.spec --noconfirm
python tools\verify_bundle.py
dist\IchaLaunch.exe --qt-smoke
```

Output: `dist\IchaLaunch.exe`

</details>

<details>
<summary>Maintainer notes (catalog & suggestions)</summary>

Development remotes, catalog/mod pipelines, and public-release publish steps: [`docs/DEV_REPO.md`](docs/DEV_REPO.md).

The Available catalog is `ichalaunch/data/addons.json` on **public** `brutaliccus/IchaLaunch` `master`. Clients fetch and cache it; merge catalog PRs there and launchers pick them up on the next refresh.

**Suggest for catalog** posts to the Cloudflare Worker (`ichalaunch/addons/submit.py`). Maintainer approval: label the issue `catalog-approved` → Action opens/merges a catalog PR. Opt-in crash reports use the same Worker at `/crash` (`ichalaunch/core/crash_report.py`, Settings → Privacy). Worker setup: `tools/addon-submit-worker/README.md`.

Optional GitHub token in Settings unlocks fork/version browsing and README previews; update badges work without one.

**Signed launcher updates:** builds that include `ichalaunch/core/signing.py` download `IchaLaunch.exe.sig` beside the exe and **refuse** to install unless a pinned Ed25519 key signed the bytes. There is no override. v1.4.6 clients still use the old updater and ignore the sidecar. The **next** GitHub release after v1.4.6 (typically **v1.4.7**) must upload **both** `IchaLaunch.exe` and `IchaLaunch.exe.sig`. Sign locally after PyInstaller — never in CI:

```bat
python tools\sign.py --key %LOCALAPPDATA%\IchaLaunch\signing\ichalaunch-key1.pem dist\IchaLaunch.exe
```

Pinned public keys (SHA-256 of the raw 32-byte key; also in `PINNED_KEYS`):

- key 1 (active): `04fd0725af49fcb3a1fbe69845ef3bb1007ecc911ece3a093d7e623fe8878a23`
- key 2 (backup): `b75ae8582b9e4f338f7af4a7e77540445b988bcfb6bab04a4c1e91003f7c3272`
- key 3 (backup): `92991a640ca7adc5b49f69a75af799a6cca4c5521db99527c6cc5b69b9476752`

Private keys are not in this repository. Back them up offline. A key swap in the repo alone should be visible here.

</details>

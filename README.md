# IchaLaunch

> **Fresh install:** Use the launcher's **INSTALL** flow for a clean RavenCraft setup. Do **not** point IchaLaunch at an old TurtleWoW or Capybara client — start fresh for the best results.

[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-ffdd00?style=for-the-badge&logo=buy-me-a-coffee&logoColor=black)](https://buymeacoffee.com/jeb32411u)

**IchaLaunch** installs **[RavenCraft](https://ravencraft.io/)** (Turtle WoW–compatible 1.18), manages addons and client mods, and launches the game.

Download **`IchaLaunch.exe`** from **[Releases](https://github.com/brutaliccus/IchaLaunch/releases/latest)**.

A frameless RavenCraft-themed window: **HOME**, **ADDONS**, **CLIENT**, **SETTINGS**, and a **PLAY** bar.

---

## Get the launcher

1. Open the latest release: [github.com/brutaliccus/IchaLaunch/releases/latest](https://github.com/brutaliccus/IchaLaunch/releases/latest)
2. Download **`IchaLaunch.exe`**
3. Put it somewhere convenient (next to your game folder is fine — not required)
4. Run the EXE

No installer. Windows may show a SmartScreen prompt for an unsigned download — **More info** → **Run anyway** if you trust the release.


## Platform support (Windows only)

IchaLaunch ships as a **native Windows** executable for **Windows 10 or 11** (64-bit). Download **IchaLaunch.exe** from [Releases](https://github.com/brutaliccus/IchaLaunch/releases/latest) and run it on Windows.

**Proton, Wine, Linux, and Steam Deck are not supported.** Running the EXE under Proton/Wine or on a Steam Deck (including Desktop Mode) often fails with missing DLL errors (icuuc.dll, Qt6Core.dll, or `ImportError: DLL load failed` when loading Qt). Use Windows natively, dual-boot Windows, or a Windows VM if you are on Linux or a Deck.

---

## Install the RavenCraft client

![Home](docs/screenshots/home.png)

If you already have a 1.18 client, skip INSTALL and point **SETTINGS** at the folder that contains `WoW.exe`.

Otherwise tap **INSTALL** on the bottom bar:

1. Pick a parent folder (prefer something simple like `D:\Games` — avoid Program Files, Desktop, Downloads, and Documents)
2. Your browser opens **Gofile** — download **`twmoa_1181.zip`** (a VPN may be required)
3. Leave IchaLaunch open. It watches **Downloads**, extracts into a **`RavenCraft`** folder, writes `realmlist.wtf`, then deletes the zip
4. **PLAY** appears when the client is ready

To unlink and install again, use **Reset Client Link** in **SETTINGS**. That forgets the saved folder; it does not delete files on disk.

---

## Play

**PLAY** launches World of Warcraft. If VanillaFixes is installed, the launcher starts through `VanillaFixes.exe` (you can turn that off in Settings).

---

## Addons

![Addons](docs/screenshots/addons.png)

- Browse the Turtle WoW wiki **catalog**, search, and filter
- **Install** from the catalog, or **Add from GitHub** (paste any public addon repo)
- **Preview** a repo’s README before you install
- **Update** / **Update All** when a newer version is available
- Uncheck an addon to **unload** it (it stays on disk, the game just won’t load it)

---

## Client mods

![Client](docs/screenshots/client.png)

The **CLIENT** tab is for engine and visual packs — VanillaFixes, DXVK, SuperWoW, Nampower, UnitXP, night sky, and similar.

Tick what you want, then **Apply Changes**. Search across categories; **Open in Git** when a repo link exists.

---

## Settings

![Settings](docs/screenshots/settings.png)

- **Game location** — folder that contains `WoW.exe`, plus **Verify**
- **AddOns folder** — defaults to `Interface\AddOns` under the game; override if yours lives elsewhere
- **Launch** — VanillaFixes, minimize or close the launcher when the game starts
- **Automatically Check For Updates On Startup** — launcher, addons, and client mods
- **Auto-scan cooldown** — how long automatic/startup scans wait before running again (15 min–24 hours; default 1 hour). Manual Check for updates always runs.
- **GitHub API** — optional personal access token (see below); stored locally and sent only to GitHub over HTTPS
- **Reset Client Link** — unlink the saved WoW folder so **PLAY** becomes **INSTALL** again

### GitHub personal access token

Without a token, GitHub allows only about **60 API requests per hour**. IchaLaunch queues the rest of an addon update scan and continues automatically when budget refreshes — a large catalog may take **several hours** to finish one full pass. With a token, scans complete much faster (thousands of requests per hour).

1. Open [GitHub → Settings → Developer settings → Personal access tokens](https://github.com/settings/tokens)
2. Create a token:
   - **Fine-grained (recommended):** resource owner = your user; repository access = **Public repositories**; permissions = **Contents: Read-only** and **Metadata: Read-only**
   - **Classic:** `public_repo` works for API reads, but GitHub also grants **write** to public repositories with that scope. Prefer a fine-grained read-only token.
3. Copy the token, then in IchaLaunch open **SETTINGS → GitHub API** and paste it
4. Save — the token stays on your PC and is sent only as an `Authorization` header over **HTTPS** to GitHub hosts (`api.github.com`, `github.com`, `*.githubusercontent.com`). It is never sent to third-party image hosts or over HTTP.

---

## Progress and launcher updates

![Home with loading bar](docs/screenshots/home-loading.png)

Downloads, extracts, and update checks show a **determinate** bar on the bottom strip (how far along, not a spinner).

When a newer IchaLaunch is on GitHub Releases, **PLAY** becomes **UPDATE**. That replaces the EXE in place — you do not re-download from the site by hand.

---

## Troubleshooting

**Launcher won’t start / `ImportError: DLL load failed` / `icuuc.dll` or `Qt6Core.dll` not found**  
IchaLaunch is a **Windows 10 or 11** app. Run the release **`IchaLaunch.exe` natively** — not under **Proton**, Wine, or Steam Deck desktop mode. Those environments do not reliably load the PySide6/Qt6 bundle.  
If the EXE still fails on Windows, install the latest **[Microsoft Visual C++ Redistributable (x64)](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist)** and try again.

**Windows Defender / Controlled Folder Access blocks VanillaFixes or DLL mods**  
Allow `IchaLaunch.exe`, `VanillaFixes.exe`, and `WoW.exe`, or move the game out of a protected folder.

**Addon / update checks fail, feel stuck, or say “queued”**  
GitHub’s anonymous API limit is about **60 requests/hour**. Without a token, IchaLaunch checks what it can, queues the rest, and resumes automatically (status shows something like `Scanning addons… 60/240 (queued; resumes in ~47 min)`). Paste a personal access token in **SETTINGS → GitHub API** (see Settings above) so a full catalog pass finishes in one go.

**PLAY does nothing / “Client not found”**  
Confirm **SETTINGS** points at the folder that contains `WoW.exe`, then click **Verify**.

**Gold dots on ADDONS or CLIENT**  
Those tabs have pending updates or unapplied client changes — open the tab and update / apply.

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

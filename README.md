# IchaLaunch

Python launcher for **RavenCraft** (Turtle 1.18-compatible client).

RavenCraft-themed UI inspired by [ravencraft.io](https://ravencraft.io/) and [ichasarmory.quest/gear-planner](https://ichasarmory.quest/gear-planner).

## Screenshots

### Home

![Home](docs/screenshots/home.png)

### Addons

![Addons](docs/screenshots/addons.png)

### Client

![Client](docs/screenshots/client.png)

### Settings

![Settings](docs/screenshots/settings.png)

### Themed dialog

![Themed dialog](docs/screenshots/themed_dialog.png)

## Features

- **PLAY / INSTALL** — pick a game folder or point at an existing client; launches via `VanillaFixes.exe` when enabled
- **Home** — RavenCraft branding plus launch countdown (same target as ravencraft.io)
- **CLIENT** — desired-state checkboxes for RetroCro/TurtleWoW-Mods style fixes (Vanilla Tweaks, SuperWoW, Nampower, UnitXP, PerfBoost, no1600x1200, WDB block, VanillaFixes/DXVK) plus automated Visual/QoL MPQs (night sky, Epoch water, fog, darker nights, pink herbs, raid visuals)
- **ADDONS** — Turtle WoW wiki baseline catalog + paste any GitHub repo; per-row Install, last-updated stamp next to Up to date, Reinstall to force overwrite
- Quiet update flow — no success popup after addon updates; status bar + Up to date
- RavenCraft-themed dialogs instead of system message boxes
- Custom RavenCraft app icon on the window and packaged EXE
- Backups under `<game>/.ichalaunch/backups/`

## Run from source

```bat
cd F:\Launcher
python -m pip install -r requirements.txt
python run.py
```

## Build EXE

```bat
cd F:\Launcher
python -m PyInstaller IchaLaunch.spec --noconfirm
```

Output: `F:\Launcher\dist\IchaLaunch.exe`

## First trial

1. Open IchaLaunch
2. Settings → Browse → select your existing game folder (e.g. `F:\capybara wow v1181\capybara wow v1181\Game`)
3. CLIENT → toggle desired mods → Apply Changes
4. ADDONS → install from catalog or paste a GitHub URL
5. PLAY

Official Ravencraft client zip hosting is not wired yet — use Browse to an existing 1.18 client.

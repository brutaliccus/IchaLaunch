# IchaLaunch

Python launcher for **Ravencraft** (Turtle 1.18-compatible client).

Styled after [ichasarmory.quest/gear-planner](https://ichasarmory.quest/gear-planner).

## Features

- **PLAY / INSTALL** — pick a game folder or point at an existing client; launches via `VanillaFixes.exe` when enabled
- **CLIENT** — desired-state checkboxes for RetroCro/TurtleWoW-Mods style fixes (Vanilla Tweaks, SuperWoW, Nampower, UnitXP, PerfBoost, no1600x1200, WDB block, VanillaFixes/DXVK)
- **ADDONS** — Turtle WoW wiki baseline catalog + paste any GitHub repo; tracks commits and prompts for updates
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

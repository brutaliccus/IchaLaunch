"""SuperWoW install health checks and user troubleshooting prompts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from ichalaunch.config.settings import settings
from ichalaunch.core.filesystem import read_dlls_txt, validate_pe_binary
from ichalaunch.core.logging_setup import log
from ichalaunch.game.launcher import detect_game, resolve_addons_dir

_SUPERWOW_ID = "superwow"
_HOOK_DLL = "SuperWoWhook.dll"
_SUPERAPI_FOLDER = "SuperAPI"
_HOOK_MIN_BYTES = 200_000

SUPERWOW_TROUBLESHOOT_TITLE = "Broken talents after SuperWoW via IchaLaunch"

SUPERWOW_TROUBLESHOOT_BODY = (
    "This usually means Windows Security blocked or damaged SuperWoWhook.dll "
    "during install, or the SuperAPI companion addon was left behind when "
    "turning the mod off.\n\n"
    "Fix:\n"
    "1. Add your entire WoW folder as a Windows Security exclusion "
    "(Settings → Privacy & security → Windows Security → Virus & threat "
    "protection → Manage settings → Exclusions → Add an exclusion → Folder).\n"
    "2. In IchaLaunch Client tab: turn SuperWoW off, click Apply, then turn "
    "it on again and Apply (or reinstall SuperWoW manually from "
    "https://github.com/balakethelock/SuperWoW/releases).\n"
    "3. Confirm these are gone when SuperWoW is disabled:\n"
    "   • SuperWoWhook.dll in the WoW folder\n"
    "   • Interface\\AddOns\\SuperAPI\n"
    "   • SuperWoWhook.dll lines in dlls.txt and .ichalaunch\\dlls.txt "
    "(if present)\n"
    "4. If problems persist, move the game out of Downloads/Desktop to "
    "e.g. C:\\Games\\YourServer and use Check Game Permissions in Settings."
)

_SESSION_DRIFT_PROMPTED = False


class SuperWoWTrigger(str, Enum):
    INSTALL_FAIL = "install_fail"
    REMOVE_FAIL = "remove_fail"
    SYNC_FAIL = "sync_fail"
    ENABLE_BAD_DLL = "enable_bad_dll"
    CLIENT_DRIFT = "client_drift"


@dataclass(frozen=True)
class SuperWoWIssue:
    code: str
    detail: str


def _hook_listed_in_dlls_txt(game: Path) -> bool:
    listed = {n.lower() for n in read_dlls_txt(game)}
    return _HOOK_DLL.lower() in listed


def _superapi_path(game: Path) -> Path | None:
    addons = resolve_addons_dir(create=False)
    if addons is None:
        addons = game / "Interface" / "AddOns"
    return addons / _SUPERAPI_FOLDER


def detect_superwow_issues(game: Path | None = None) -> list[SuperWoWIssue]:
    """Return concrete SuperWoW drift or corruption signals (empty when healthy)."""
    game = game or detect_game()
    if not game:
        return []

    desired = bool(settings.desired_mods.get(_SUPERWOW_ID, False))
    hook = game / _HOOK_DLL
    hook_exists = hook.is_file()
    superapi = _superapi_path(game)
    superapi_exists = superapi is not None and superapi.exists()
    hook_listed = _hook_listed_in_dlls_txt(game)
    issues: list[SuperWoWIssue] = []

    if desired:
        if not hook_exists:
            issues.append(
                SuperWoWIssue(
                    "missing_hook",
                    f"{_HOOK_DLL} is enabled but not found in the game folder.",
                )
            )
        else:
            try:
                validate_pe_binary(hook, min_size=_HOOK_MIN_BYTES)
            except OSError as exc:
                detail = str(exc.args[1] if len(exc.args) > 1 else exc)
                issues.append(
                    SuperWoWIssue(
                        "corrupt_hook",
                        f"{_HOOK_DLL} failed verification: {detail}",
                    )
                )
        if not superapi_exists:
            issues.append(
                SuperWoWIssue(
                    "missing_superapi",
                    f"SuperAPI addon folder is missing under Interface\\AddOns.",
                )
            )
    else:
        if hook_exists:
            issues.append(
                SuperWoWIssue(
                    "stale_hook",
                    f"{_HOOK_DLL} is still on disk while SuperWoW is disabled.",
                )
            )
        if superapi_exists:
            issues.append(
                SuperWoWIssue(
                    "stale_superapi",
                    "SuperAPI addon folder is still installed while SuperWoW is disabled.",
                )
            )
        if hook_listed:
            issues.append(
                SuperWoWIssue(
                    "stale_dlls_txt",
                    f"dlls.txt still lists {_HOOK_DLL} while SuperWoW is disabled.",
                )
            )

    return issues


def superwow_troubleshoot_message(issues: list[SuperWoWIssue] | None = None) -> str:
    """Full dialog body, optionally prefixed with detected issue bullets."""
    game = detect_game()
    found = issues if issues is not None else detect_superwow_issues(game)
    if not found:
        return SUPERWOW_TROUBLESHOOT_BODY
    bullets = "\n".join(f"• {item.detail}" for item in found)
    return f"Detected:\n{bullets}\n\n{SUPERWOW_TROUBLESHOOT_BODY}"


def _failure_lines_refer_superwow(failures: list[str]) -> bool:
    blob = " ".join(failures).lower()
    return _SUPERWOW_ID in blob or _HOOK_DLL.lower() in blob


def should_prompt_superwow_troubleshoot(trigger: SuperWoWTrigger, issues: list[SuperWoWIssue]) -> bool:
    """Decide whether to show the troubleshooting dialog (avoid launch spam)."""
    if not issues:
        return False
    if trigger in (
        SuperWoWTrigger.INSTALL_FAIL,
        SuperWoWTrigger.REMOVE_FAIL,
        SuperWoWTrigger.SYNC_FAIL,
        SuperWoWTrigger.ENABLE_BAD_DLL,
    ):
        return True
    if trigger == SuperWoWTrigger.CLIENT_DRIFT:
        global _SESSION_DRIFT_PROMPTED
        if _SESSION_DRIFT_PROMPTED:
            return False
        _SESSION_DRIFT_PROMPTED = True
        return True
    return False


def maybe_show_superwow_troubleshoot(
    parent,
    trigger: SuperWoWTrigger,
    *,
    issues: list[SuperWoWIssue] | None = None,
    failures: list[str] | None = None,
) -> bool:
    """Show the themed troubleshooting dialog when triggers and issues align."""
    if failures and not _failure_lines_refer_superwow(failures):
        return False

    game = detect_game()
    found = issues if issues is not None else detect_superwow_issues(game)
    if not should_prompt_superwow_troubleshoot(trigger, found):
        return False

    from ichalaunch.ui.widgets import dialogs as themed

    log.info("SuperWoW troubleshooting prompt (%s): %s", trigger.value, found)
    themed.warning(parent, SUPERWOW_TROUBLESHOOT_TITLE, superwow_troubleshoot_message(found))
    return True


def maybe_show_superwow_after_mod_failures(parent, failures: list[str], action: str) -> bool:
    """Map install/remove/sync failure lines to the troubleshooting dialog."""
    if not failures or not _failure_lines_refer_superwow(failures):
        return False
    trigger = SuperWoWTrigger.SYNC_FAIL
    if action == "install":
        trigger = SuperWoWTrigger.INSTALL_FAIL
    elif action == "remove":
        trigger = SuperWoWTrigger.REMOVE_FAIL
    return maybe_show_superwow_troubleshoot(parent, trigger, failures=failures)

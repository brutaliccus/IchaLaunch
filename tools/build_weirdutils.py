"""Fetch MarcelineVQ/WeirdUtils release PEs, or build them her way.

Default: download her Codeberg release assets (v0.7.0, worldmarkers v0.7.1)
into ``tools/_weirdutils/out`` and ``ichalaunch/data/weirdutils``. The catalog
installs those same URLs with ``skip_local_override`` so IchaLaunch overlays
never replace her DLLs.

``--from-source`` clones her repo and runs her documented command
(``zig build all-variants -Doptimize=ReleaseSmall``) with **no**
``tools/weirdutils_patches`` overlays. That path is for reproducing her
artifacts, not for shipping our Zig 0.16 fork.

``--overlay-build`` is the old patched compile (DllMain / SuperWoW skips /
stuffed main.zig). Do not use it for Play/Apply payloads.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORK = REPO_ROOT / "tools" / "_weirdutils"
SRC = WORK / "src"
PREBUILT = WORK / "prebuilt"
OUT = WORK / "out"
BUNDLE = REPO_ROOT / "ichalaunch" / "data" / "weirdutils"

CLONE_URL = "https://codeberg.org/MarcelineVQ/WeirdUtils.git"
RELEASE_BASE = "https://codeberg.org/MarcelineVQ/WeirdUtils/releases/download"
ZIG_VERSION = "0.16.0"
ZIG_ZIP_NAME = f"zig-x86_64-windows-{ZIG_VERSION}.zip"
ZIG_URL = f"https://ziglang.org/download/{ZIG_VERSION}/{ZIG_ZIP_NAME}"
ZIG_DIR = WORK / "zig"
PATCHES = REPO_ROOT / "tools" / "weirdutils_patches"

# Marceline's published standalone PEs. outline / dpslog / framecrash /
# interact were never attached to a Codeberg release.
OFFICIAL_RELEASE_ASSETS: tuple[tuple[str, str], ...] = (
    ("v0.7.0", "clickthrough.dll"),
    ("v0.7.0", "customassets.dll"),
    ("v0.7.0", "healtextfix.dll"),
    ("v0.7.0", "logsessions.dll"),
    ("v0.7.0", "minimapicons.dll"),
    ("v0.7.0", "pngscreenshots.dll"),
    ("v0.7.0", "transmogfix.dll"),
    ("v0.7.0", "weirdperformance.dll"),
    ("v0.7.1", "worldmarkers.dll"),
)

# Packaged catalog modules (must match mods.json dest names).
# healtextfix is Client Enhancements, not Advanced.
BUILD_MODULES: tuple[str, ...] = (
    "worldmarkers",
    "outline",
    "interact",
    "pngscreenshots",
    "framecrash",
    "transmogfix",
    "customassets",
    "minimapicons",
    "clickthrough",
    "logsessions",
    "dpslog",
    "weirdperformance",
    "healtextfix",
)
# Compiled into the same tree but not offered in the catalog.
SKIP_DEFAULT_MODULES: tuple[str, ...] = (
    "bigcursor",
    "transform44",
    "addonperf",
    "ssemaths",
    "silicon",
    "superweirdo",
    "luagc",
)

# Upstream returns i32. Zig 0.16 start.zig types root.DllMain as
# ``std.os.windows.BOOL`` (enum(c_int), TRUE=1). The PE/VanillaFixes entry is
# Zig's ``_DllMainCRTStartup`` (stdcall @12), which calls DllMain then forces
# EAX=1. Replacing that CRT drops TLS (``__tls_index``) and breaks
# weirdperformance. DllMain must stay BOOL so start.zig type-checks; ``.TRUE``
# is integer 1 (WINAPI stdcall).
_DLLMAIN_I32 = """pub export fn DllMain(
    _: ?*anyopaque,
    reason: u32,
    _: ?*anyopaque,
) callconv(WINAPI) i32 {
    switch (reason) {
        1 => install(),
        0 => uninstall(),
        else => {},
    }
    return 1;
}"""
_DLLMAIN_FIXED = """pub export fn DllMain(
    _: ?*anyopaque,
    reason: u32,
    _: ?*anyopaque,
) callconv(WINAPI) std.os.windows.BOOL {
    switch (reason) {
        1 => install(),
        0 => uninstall(),
        else => {},
    }
    return .TRUE;
}"""

_UA = "IchaLaunch-WeirdUtilsBuild/1.0"


def _run(args: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        check=True,
        text=True,
        capture_output=True,
    )


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=120) as resp, dest.open("wb") as fh:
        shutil.copyfileobj(resp, fh)
    if dest.stat().st_size < 1024:
        raise RuntimeError(f"Download too small ({dest.stat().st_size} bytes): {url}")


def download_official_releases() -> None:
    """Download Marceline's release PEs into prebuilt/, out/, and the exe bundle."""
    PREBUILT.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    BUNDLE.mkdir(parents=True, exist_ok=True)
    for tag, name in OFFICIAL_RELEASE_ASSETS:
        dest = PREBUILT / name
        url = f"{RELEASE_BASE}/{tag}/{name}"
        print(f"Downloading {tag}/{name}...")
        _download(url, dest)
        for folder in (OUT, BUNDLE):
            copied = folder / name
            shutil.copy2(dest, copied)
            print(f"  {copied} ({copied.stat().st_size} bytes)")


def download_prebuilt() -> None:
    download_official_releases()


def _apply_dllmain_abi_patch() -> None:
    """Make DllMain ``BOOL WINAPI`` returning TRUE (1) for Zig 0.16 start.zig.

    Do not export our own ``_DllMainCRTStartup`` — that skips ``tls.zig`` and
    leaves ``__tls_index`` undefined (weirdperformance inflate hook).
    """
    main_zig = SRC / "src" / "main.zig"
    text = main_zig.read_text(encoding="utf-8")
    original = text
    owned_crt = """pub export fn _DllMainCRTStartup(
    hinstDLL: *anyopaque,
    fdwReason: u32,
    lpReserved: ?*anyopaque,
) callconv(WINAPI) i32 {
    return DllMain(hinstDLL, fdwReason, lpReserved);
}

"""
    if owned_crt in text:
        text = text.replace(owned_crt, "")
    if _DLLMAIN_I32 in text:
        text = text.replace(_DLLMAIN_I32, _DLLMAIN_FIXED, 1)
    if text == original:
        return
    if _DLLMAIN_FIXED not in text:
        raise RuntimeError(f"DllMain ABI patch: expected i32 or BOOL form in {main_zig}")
    main_zig.write_text(text, encoding="utf-8")
    print(f"Patched {main_zig.relative_to(SRC)} DllMain BOOL WINAPI / Zig CRT")


def _apply_llvm_backend_patch() -> None:
    """Force LLVM, strip, and single-threaded on the i386 DLL compile units."""
    path = SRC / "build.zig"
    text = path.read_text(encoding="utf-8")
    patched = text
    if ".use_llvm = true" not in patched:
        needle = ".linkage = .dynamic,\n"
        if needle not in patched:
            raise RuntimeError(f"LLVM backend patch: no dynamic libraries in {path}")
        patched = patched.replace(
            needle, ".linkage = .dynamic,\n        .use_llvm = true,\n"
        )
    root_old = '.root_source_file = b.path("src/main.zig"),'
    root_new = (
        '.root_source_file = b.path("src/main.zig"),\n'
        "            .strip = true,"
    )
    if ".strip = true" not in patched and root_old in patched:
        patched = patched.replace(root_old, root_new)
    if patched == text:
        return
    path.write_text(patched, encoding="utf-8")
    print(f"Patched {path.relative_to(SRC)} to use LLVM + strip for i386 DLLs")


_LFENCE_OLD = 'asm volatile ("lfence" ::: "memory");'
_LFENCE_NEW = "asm volatile (\"lfence\" ::: .{ .memory = true });"


def _apply_zig016_asm_clobber_patches() -> None:
    """Zig 0.16 requires packed-struct clobbers instead of GCC string lists."""
    timer = SRC / "src" / "weirdperformance" / "timer_fix.zig"
    if not timer.is_file():
        return
    text = timer.read_text(encoding="utf-8")
    if _LFENCE_OLD not in text:
        return
    timer.write_text(text.replace(_LFENCE_OLD, _LFENCE_NEW), encoding="utf-8")
    print(f"Patched {timer.relative_to(SRC)} for Zig 0.16.0 asm clobbers")


_CORE_HOOKS_SKIP_FN = """fn wantsCoreWowHooks() bool {
    // healtextfix-only must not attach 1.12.1 Storm/Lua/engine RVAs. SuperWoW 2.x
    // already owns those sites; a trampoline that calls 0 is EIP 0 / ERROR #132.
    const opts = @import("build_options");
    inline for (opts.all_module_names) |name| {
        if (comptime std.mem.eql(u8, name, "healtextfix")) continue;
        if (@field(opts, "enable_" ++ name)) return true;
    }
    return false;
}

"""


def _apply_healtext_core_hook_skip() -> None:
    """Skip WoW.exe core detours in the healtextfix-only variant."""
    main_zig = SRC / "src" / "main.zig"
    text = main_zig.read_text(encoding="utf-8")
    original = text
    if "fn wantsCoreWowHooks()" not in text:
        needle = "fn install() void {"
        if needle not in text:
            raise RuntimeError(f"healtextfix core-hook skip: no install() in {main_zig}")
        text = text.replace(needle, _CORE_HOOKS_SKIP_FN + needle, 1)
    open_core = """    log = logging.Logger.open("weirdutils", .console);

    // Core hooks chain safely across multiple DLLs via zhook's E9-detect path:"""
    open_core_new = """    log = logging.Logger.open("weirdutils", .console);

    if (comptime wantsCoreWowHooks()) {
    // Core hooks chain safely across multiple DLLs via zhook's E9-detect path:"""
    if open_core in text and "if (comptime wantsCoreWowHooks())" not in text:
        text = text.replace(open_core, open_core_new, 1)
    text = text.replace("if (wantsCoreWowHooks()) {", "if (comptime wantsCoreWowHooks()) {")
    close_core = """    _ = shutdown_hook.attach(0x490BD0, &shutdownDetour);

    // Module hooks always run (each module has its own mutex)"""
    close_core_new = """    _ = shutdown_hook.attach(0x490BD0, &shutdownDetour);
    }

    // Module hooks always run (each module has its own mutex)"""
    if close_core in text:
        text = text.replace(close_core, close_core_new, 1)
    addons_old = """    addons.install();
}"""
    addons_new = """    if (wantsCoreWowHooks()) addons.install();
}"""
    # Only the install() tail — next fn is uninstall
    install_tail = """    addons.install();
}

fn uninstall() void {"""
    install_tail_new = """    if (wantsCoreWowHooks()) addons.install();
}

fn uninstall() void {"""
    if install_tail in text:
        text = text.replace(install_tail, install_tail_new, 1)
    elif addons_old in text and "if (wantsCoreWowHooks()) addons.install()" not in text:
        text = text.replace(addons_old, addons_new, 1)
    if text == original:
        if "fn wantsCoreWowHooks()" in text and "wantsCoreWowHooks()" in text:
            return
        raise RuntimeError(f"healtextfix core-hook skip: needles missed in {main_zig}")
    main_zig.write_text(text, encoding="utf-8")
    print(f"Patched {main_zig.relative_to(SRC)} to skip core WoW.exe hooks for healtextfix-only")


def _find_zhook_zig() -> Path | None:
    roots = [SRC / "zig-pkg", SRC, WORK]
    for root in roots:
        if not root.is_dir():
            continue
        hits = sorted(p for p in root.rglob("zhook.zig") if "zhook" in str(p).lower())
        if hits:
            return hits[0]
    return None


def _apply_zhook_null_guards() -> None:
    """Refuse SuperWoW-owned / JMP-0 sites; never callOriginal through a NULL trampoline."""
    path = _find_zhook_zig()
    if path is None:
        print("zhook.zig not fetched yet; SuperWoW/NULL attach guards skip this pass")
        return
    text = path.read_text(encoding="utf-8")
    original = text
    if "fn ownedBySuperWow(" not in text:
        needle = """pub extern "kernel32" fn VirtualFree(
    lpAddress: *anyopaque,
    dwSize: usize,
    dwFreeType: u32,
) callconv(WINAPI) i32;
"""
        extra = needle + """
extern "kernel32" fn GetModuleHandleA(lpModuleName: ?[*:0]const u8) callconv(WINAPI) ?*anyopaque;
extern "kernel32" fn OutputDebugStringA(lpOutputString: [*:0]const u8) callconv(WINAPI) void;

fn debugSkip(msg: [*:0]const u8) void {
    OutputDebugStringA(msg);
}

/// True when ``addr`` falls inside the loaded SuperWoWhook.dll image.
pub fn ownedBySuperWow(addr: usize) bool {
    const base_ptr = GetModuleHandleA("SuperWoWhook.dll") orelse return false;
    const base = @intFromPtr(base_ptr);
    if (addr < base) return false;
    const mz: [*]const u8 = @ptrFromInt(base);
    if (mz[0] != 'M' or mz[1] != 'Z') return false;
    const e_lfanew = std.mem.readInt(u32, mz[0x3C..0x40], .little);
    const opt = mz + e_lfanew + 24;
    const size = std.mem.readInt(u32, opt[56..60], .little);
    return addr < base + size;
}

"""
        if needle not in text:
            raise RuntimeError(f"zhook SuperWoW guard: VirtualFree block missing in {path}")
        text = text.replace(needle, extra, 1)
    if "if (target < 0x10000) return .disasm_error;" not in text:
        text = text.replace(
            "        if (self.mem != null) return .ok;\n\n        const src: [*]const u8 = @ptrFromInt(target);",
            "        if (self.mem != null) return .ok;\n        if (target < 0x10000) return .disasm_error;\n\n        const src: [*]const u8 = @ptrFromInt(target);",
            1,
        )
    old_act = """    pub fn activate(self: *GenericHook, detour_addr: usize) void {
        var patch: [MAX_STOLEN]u8 = .{0x90} ** MAX_STOLEN;
        patch[0] = 0xE9;
        writeRel32(patch[1..5], self.target + 1, detour_addr);
        writeProtected(self.target, patch[0..self.stolen_size]);
    }"""
    new_act = """    pub fn activate(self: *GenericHook, detour_addr: usize) void {
        if (self.target < 0x10000 or detour_addr < 0x10000 or self.trampoline < 0x10000) return;
        if (self.stolen_size < JMP_SIZE) return;
        var patch: [MAX_STOLEN]u8 = .{0x90} ** MAX_STOLEN;
        patch[0] = 0xE9;
        writeRel32(patch[1..5], self.target + 1, detour_addr);
        writeProtected(self.target, patch[0..self.stolen_size]);
    }"""
    if old_act in text:
        text = text.replace(old_act, new_act, 1)
    old_att = """        pub fn attach(self: *Self, target: usize, detour: TargetFnPtr) GenericHook.Error {
            const err = self.inner.prepare(target);
            if (err != .ok) return err;
            self.inner.activate(@intFromPtr(detour));
            return .ok;
        }"""
    new_att = """        pub fn attach(self: *Self, target: usize, detour: TargetFnPtr) GenericHook.Error {
            const detour_addr = @intFromPtr(detour);
            if (target < 0x10000 or detour_addr < 0x10000) return .disasm_error;
            const src: [*]const u8 = @ptrFromInt(target);
            if (src[0] == 0xE9) {
                const dest = rel32Target(target);
                if (dest < 0x10000) {
                    debugSkip("zhook: skip attach, existing E9 dest is 0\\n");
                    return .disasm_error;
                }
                if (ownedBySuperWow(dest)) {
                    debugSkip("zhook: skip attach, SuperWoW already owns site\\n");
                    return .disasm_error;
                }
            }
            const err = self.inner.prepare(target);
            if (err != .ok) return err;
            if (self.inner.trampoline < 0x10000) {
                self.inner.remove();
                return .alloc_failed;
            }
            self.inner.activate(detour_addr);
            return .ok;
        }"""
    if old_att in text:
        text = text.replace(old_att, new_att, 1)
    old_co = """        pub fn callOriginal(self: *const Self, args: anytype) ReturnType {
            const orig: *const TargetFnType = @ptrFromInt(self.inner.trampoline);
            return @call(.never_tail, orig, args);
        }"""
    new_co = """        pub fn callOriginal(self: *const Self, args: anytype) ReturnType {
            if (self.inner.trampoline < 0x10000) {
                debugSkip("zhook: callOriginal skipped, trampoline is 0\\n");
                if (ReturnType == void) return;
                return std.mem.zeroes(ReturnType);
            }
            const orig: *const TargetFnType = @ptrFromInt(self.inner.trampoline);
            return @call(.never_tail, orig, args);
        }"""
    if old_co in text:
        text = text.replace(old_co, new_co, 1)
    if text == original:
        if "fn ownedBySuperWow(" in text and "callOriginal skipped" in text:
            return
        raise RuntimeError(f"zhook SuperWoW/NULL guards: needles missed in {path}")
    path.write_text(text, encoding="utf-8")
    print(f"Patched {path} SuperWoW-owned / JMP-0 attach guards")


def _apply_superwow_skip_all_core() -> None:
    """Never install shared Storm/Lua/engine hooks when SuperWoW is already loaded.

    2026-08-29 08:40:00 Crash.txt: EIP landed on the stack at 0x001AE6AC
    (zeros = ADD [EAX],AL → write NULL). Stack was a CheckFileExistence
    (0x654DD0) chain through every Advanced DLL. 08:40:19 worldmarkers-only
    still died on the same Storm path (filename in ESI, trampoline junk).
    SuperWoW 2.2 is always present on RavenCraft; skip the whole core set.
    """
    main_zig = SRC / "src" / "main.zig"
    text = main_zig.read_text(encoding="utf-8")
    if "SuperWoW loaded; skipping all core Storm/Lua/engine hooks" in text:
        return
    if "GetModuleHandleA" not in text:
        text = text.replace(
            "const WINAPI = std.builtin.CallingConvention.winapi;\n",
            "const WINAPI = std.builtin.CallingConvention.winapi;\n"
            'extern "kernel32" fn GetModuleHandleA(lpModuleName: ?[*:0]const u8) callconv(WINAPI) ?*anyopaque;\n',
            1,
        )
    if "extern \"kernel32\" fn CreateThread" not in text:
        after = 'extern "kernel32" fn GetModuleHandleA(lpModuleName: ?[*:0]const u8) callconv(WINAPI) ?*anyopaque;\n'
        extra = (
            after
            + 'extern "kernel32" fn Sleep(dwMilliseconds: u32) callconv(WINAPI) void;\n'
            + 'extern "kernel32" fn CreateThread(lpThreadAttributes: ?*anyopaque, dwStackSize: usize, lpStartAddress: *const fn (?*anyopaque) callconv(WINAPI) u32, lpParameter: ?*anyopaque, dwCreationFlags: u32, lpThreadId: ?*u32) callconv(WINAPI) ?*anyopaque;\n'
        )
        if after not in text:
            raise RuntimeError(f"SuperWoW core skip: GetModuleHandleA decl missing in {main_zig}")
        text = text.replace(after, extra, 1)
    old = """    if (comptime wantsCoreWowHooks()) {
    // Core hooks chain safely across multiple DLLs via zhook's E9-detect path:"""
    new = """    if (comptime wantsCoreWowHooks()) {
    if (GetModuleHandleA("SuperWoWhook.dll") != null) {
        log.print("SuperWoW loaded; skipping all core Storm/Lua/engine hooks\\n");
    } else {
    // Core hooks chain safely across multiple DLLs via zhook's E9-detect path:"""
    if old not in text:
        raise RuntimeError(f"SuperWoW core skip: install open not found in {main_zig}")
    text = text.replace(old, new, 1)
    close = """    _ = shutdown_hook.attach(0x490BD0, &shutdownDetour);
    }

    // Module hooks always run (each module has its own mutex)"""
    close_new = """    _ = shutdown_hook.attach(0x490BD0, &shutdownDetour);
    }
    }

    // Module hooks always run (each module has its own mutex)"""
    if close not in text:
        raise RuntimeError(f"SuperWoW core skip: install close not found in {main_zig}")
    text = text.replace(close, close_new, 1)
    for old_addons in (
        "    if (comptime wantsCoreWowHooks() and GetModuleHandleA(\"SuperWoWhook.dll\") == null) addons.install();",
        "    if (comptime wantsCoreWowHooks()) addons.install();",
        "    if (wantsCoreWowHooks()) addons.install();",
    ):
        if old_addons in text:
            text = text.replace(
                old_addons,
                "    if (comptime wantsCoreWowHooks()) {\n"
                '        if (GetModuleHandleA("SuperWoWhook.dll") == null) addons.install();\n'
                "    }",
                1,
            )
            break
    main_zig.write_text(text, encoding="utf-8")
    print(f"Patched {main_zig.relative_to(SRC)} to skip ALL core hooks when SuperWoW is loaded")


_LATE_INIT_THREAD = '''
fn lateInitThread(_: ?*anyopaque) callconv(WINAPI) u32 {
    // screenshot.installHook calls RegisterCVar (0x63DB90). Doing that from
    // DllMain holds the loader lock and hangs WoW at ~24MB with no window.
    Sleep(3000);
    if (build_opts.screenshot) screenshot.installHook();
    if (build_opts.outline) {
        _ = outline.init();
    }
    if (build_opts.healtextfix) healtextfix.lateInit();
    if (build_opts.bigcursor) bigcursor.lateInit();
    if (build_opts.transform44) transform44.lateInit();
    if (build_opts.ssemaths) ssemaths.lateInit();
    if (build_opts.silicon) silicon.lateInit();
    if (build_opts.weirdperformance) weirdperformance.lateInit();
    return 0;
}

'''


def _apply_lateinit_fallback() -> None:
    """If GameEngine_MainInitialize is SuperWoW-owned, run late inits after DllMain."""
    main_zig = SRC / "src" / "main.zig"
    text = main_zig.read_text(encoding="utf-8")
    original = text
    if "fn lateInitThread" not in text:
        if "fn install() void {" not in text:
            raise RuntimeError(f"lateInit fallback: no install() in {main_zig}")
        text = text.replace("fn install() void {", _LATE_INIT_THREAD + "fn install() void {", 1)
    addons_block = """    if (comptime wantsCoreWowHooks()) {
        if (GetModuleHandleA("SuperWoWhook.dll") == null) addons.install();
    }"""
    needle = """    if (wantsCoreWowHooks()) addons.install();
}"""
    insert = addons_block + """

    // SuperWoW 2.x owns 0x46a400. Do not call RegisterCVar / outline.init from
    // DllMain (pngscreenshots hung RavenCraft at 24MB). Defer until after attach.
    if (comptime wantsCoreWowHooks()) {
        if (engine_init_hook.inner.trampoline < 0x10000) {
            log.print("engineInit hook skipped; deferring late inits after DllMain\\n");
            _ = CreateThread(null, 0, &lateInitThread, null, 0, null);
        }
    }
}"""
    alt = """    if (comptime wantsCoreWowHooks()) addons.install();
}"""
    alt_sw = """    if (comptime wantsCoreWowHooks() and GetModuleHandleA("SuperWoWhook.dll") == null) addons.install();
}"""
    alt_block = addons_block + "\n}"
    if "deferring late inits after DllMain" in text:
        if text != original:
            main_zig.write_text(text, encoding="utf-8")
        return
    # Replace the old synchronous DllMain lateInit if a previous patch applied it.
    old_sync = """            log.print("engineInit hook skipped; running late inits without detour\\n");
            if (build_opts.screenshot) screenshot.installHook();
            if (build_opts.outline) {
                _ = outline.init();
            }
            if (build_opts.healtextfix) healtextfix.lateInit();
            if (build_opts.bigcursor) bigcursor.lateInit();
            if (build_opts.transform44) transform44.lateInit();
            if (build_opts.ssemaths) ssemaths.lateInit();
            if (build_opts.silicon) silicon.lateInit();
            if (build_opts.weirdperformance) weirdperformance.lateInit();"""
    new_sync = """            log.print("engineInit hook skipped; deferring late inits after DllMain\\n");
            _ = CreateThread(null, 0, &lateInitThread, null, 0, null);"""
    if old_sync in text:
        text = text.replace(old_sync, new_sync, 1)
        main_zig.write_text(text, encoding="utf-8")
        print(f"Patched {main_zig.relative_to(SRC)} lateInit to defer after DllMain")
        return
    if needle in text:
        text = text.replace(needle, insert, 1)
    elif alt in text:
        text = text.replace(alt, insert, 1)
    elif alt_sw in text:
        text = text.replace(alt_sw, insert, 1)
    elif alt_block in text:
        text = text.replace(alt_block, insert, 1)
    else:
        raise RuntimeError(f"lateInit fallback: install() tail not found in {main_zig}")
    if text == original:
        raise RuntimeError(f"lateInit fallback: no change in {main_zig}")
    main_zig.write_text(text, encoding="utf-8")
    print(f"Patched {main_zig.relative_to(SRC)} lateInit fallback when engineInit is SuperWoW-owned")


def _apply_weirdperformance_superwow_skip() -> None:
    """DllMain installHooks on SuperWoW 2.2 kills WoW with no crash dump."""
    path = SRC / "src" / "weirdperformance" / "weirdperformance.zig"
    if not path.is_file():
        return
    text = path.read_text(encoding="utf-8")
    if "SuperWoW loaded; skipping WeirdPerformance hooks" in text:
        return
    if "GetModuleHandleA" not in text:
        needle = 'pub const module_name: [*:0]const u8 = "weirdperformance";\n'
        extra = (
            needle
            + "const WINAPI = @import(\"std\").builtin.CallingConvention.winapi;\n"
            + 'extern "kernel32" fn GetModuleHandleA(lpModuleName: ?[*:0]const u8) callconv(WINAPI) ?*anyopaque;\n'
        )
        if needle not in text:
            raise RuntimeError(f"weirdperformance SuperWoW skip: module_name missing in {path}")
        text = text.replace(needle, extra, 1)
    old = "pub fn installHooks() void {\n"
    new = (
        "pub fn installHooks() void {\n"
        '    if (GetModuleHandleA("SuperWoWhook.dll") != null) {\n'
        '        return; // SuperWoW loaded; skipping WeirdPerformance hooks\n'
        "    }\n"
    )
    if old not in text:
        raise RuntimeError(f"weirdperformance SuperWoW skip: installHooks missing in {path}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    print(f"Patched {path.relative_to(SRC)} to skip installHooks when SuperWoW is loaded")


def _apply_screenshot_superwow_guard() -> None:
    """Do not wipe SuperWoW's CTgaFile::Write hook; still restore UnitXP as official does."""
    path = SRC / "src" / "screenshot" / "screenshot.zig"
    if not path.is_file():
        return
    text = path.read_text(encoding="utf-8")
    if "CTgaFile::Write SuperWoW-owned or JMP 0" in text and "Zig 0.16 fastcall RegisterCVar" in text:
        return
    if "GetModuleHandleA" not in text:
        text = text.replace(
            'extern "kernel32" fn GetLocalTime(lpSystemTime: *SYSTEMTIME) callconv(WINAPI) void;',
            'extern "kernel32" fn GetLocalTime(lpSystemTime: *SYSTEMTIME) callconv(WINAPI) void;\n'
            'extern "kernel32" fn GetModuleHandleA(lpModuleName: ?[*:0]const u8) callconv(WINAPI) ?*anyopaque;',
            1,
        )
    old = """    hook.writeProtected(0x5a4810, &.{ 0x55, 0x8B, 0xEC, 0x83, 0xEC, 0x08 });
    _ = tga_hook.attach(0x5a4810, &tgaWriteDetour);
}"""
    new = """    const tga: usize = 0x5a4810;
    if (hook.readMem(u8, tga) == 0xE9) {
        const dest = hook.rel32Target(tga);
        const sw = GetModuleHandleA("SuperWoWhook.dll");
        const sw_base = if (sw) |p| @intFromPtr(p) else 0;
        if (dest < 0x10000 or (sw_base != 0 and dest >= sw_base and dest < sw_base + 0x200000)) {
            log.print("CTgaFile::Write SuperWoW-owned or JMP 0, skipping\\n");
            return;
        }
    }
    hook.writeProtected(tga, &.{ 0x55, 0x8B, 0xEC, 0x83, 0xEC, 0x08 });
    if (tga_hook.attach(tga, &tgaWriteDetour) != .ok) {
        log.print("CTgaFile::Write attach failed, skipping\\n");
    }
}"""
    if "CTgaFile::Write SuperWoW-owned or JMP 0" not in text:
        if old not in text:
            raise RuntimeError(f"screenshot SuperWoW guard: attach block missing in {path}")
        text = text.replace(old, new, 1)
    if "Zig 0.16 fastcall RegisterCVar" not in text:
        old_cvar = """    // Register CVar for compression quality persistence (saved to config.wtf)
    _ = registerCVar(CVAR_NAME, 0, 0, "6", 0, 1, 0, 0);
"""
        new_cvar = """    // Zig 0.16 fastcall RegisterCVar (0x63DB90) AVs on SuperWoW 2.2:
    // 2026-08-29 08.54.38 Crash.txt EIP 0x64B3FD ECX=garbage. Default quality 6.
"""
        if old_cvar not in text:
            raise RuntimeError(f"screenshot SuperWoW guard: registerCVar missing in {path}")
        text = text.replace(old_cvar, new_cvar, 1)
        text = text.replace(
            """fn readCVarQuality() i32 {
    const cvar_ptr = hook.call(fn ([*:0]const u8) callconv(hook.cc.fastcall) u32, CVAR_LOOKUP, .{CVAR_NAME});
    if (cvar_ptr == 0) return 6;
    const val = hook.readMem(i32, cvar_ptr + 40);
    return std.math.clamp(val, 0, 9);
}""",
            """fn readCVarQuality() i32 {
    return 6;
}""",
            1,
        )
    path.write_text(text, encoding="utf-8")
    print(f"Patched {path.relative_to(SRC)} to skip SuperWoW-owned TGA hook / RegisterCVar")


def _apply_dpslog_superwow_skip() -> None:
    """DPSLog hooks InitializeGameEngine (0x401570) and swaps handler tables.

    Enter-world dumps (2026-08-29 09.23–09.25) all have dpslog+0x43D7 on the
    stack and EIP in an unmapped trampoline while SuperWoW calls RegisterCVar
    (0x63DB90). The same crash happens with Crash Fix / official WP / official
    Heal off. Skip every WoW.exe site when SuperWoW is already loaded.
    """
    path = SRC / "src" / "dpslog" / "dpslog.zig"
    if not path.is_file():
        return
    text = path.read_text(encoding="utf-8")
    if "SuperWoW loaded; skipping all DPSLog WoW.exe hooks" in text:
        return
    if 'extern "kernel32" fn GetModuleHandleA' not in text:
        winapi = "const WINAPI = std.builtin.CallingConvention.winapi;\n"
        if winapi in text:
            text = text.replace(
                winapi,
                winapi
                + 'extern "kernel32" fn GetModuleHandleA(lpModuleName: ?[*:0]const u8) callconv(WINAPI) ?*anyopaque;\n',
                1,
            )
        else:
            text = text.replace(
                'pub const module_name: [*:0]const u8 = "dpslog";\n',
                'pub const module_name: [*:0]const u8 = "dpslog";\n\n'
                + winapi
                + 'extern "kernel32" fn GetModuleHandleA(lpModuleName: ?[*:0]const u8) callconv(WINAPI) ?*anyopaque;\n',
                1,
            )
    old = """    log = logging.Logger.open(module_name, .both);

    if (resize_events_hook.attach(0x7053B0, &resizeEventsDetour) != .ok) {"""
    new = """    log = logging.Logger.open(module_name, .both);

    if (GetModuleHandleA("SuperWoWhook.dll") != null) {
        log.print("SuperWoW loaded; skipping all DPSLog WoW.exe hooks\\n");
        log.close();
        mod_mutex.release(&g_mutex);
        g_is_hook_owner = false;
        return;
    }

    if (resize_events_hook.attach(0x7053B0, &resizeEventsDetour) != .ok) {"""
    if old not in text:
        raise RuntimeError(f"dpslog SuperWoW skip: installHooks needle missing in {path}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    print(f"Patched {path.relative_to(SRC)} to skip all DPSLog hooks when SuperWoW is loaded")


_ENTERWORLD_SKIP = (
    "    if (GetModuleHandleA(\"SuperWoWhook.dll\") != null) {\n"
    "        log.print(\"SuperWoW loaded; skipping module WoW.exe hooks\\n\");\n"
    "        log.close();\n"
    "        mod_mutex.release(&g_mutex);\n"
    "        g_is_hook_owner = false;\n"
    "        return;\n"
    "    }\n\n"
)
_GETMODULE_DECL = (
    'extern "kernel32" fn GetModuleHandleA(lpModuleName: ?[*:0]const u8) '
    "callconv(WINAPI) ?*anyopaque;\n"
)


def _ensure_getmodulehandle(text: str) -> str:
    if 'extern "kernel32" fn GetModuleHandleA' in text:
        return text
    winapi = "const WINAPI = std.builtin.CallingConvention.winapi;\n"
    if winapi in text:
        return text.replace(winapi, winapi + _GETMODULE_DECL, 1)
    needle = "const std = @import(\"std\");\n"
    if needle not in text:
        raise RuntimeError("cannot insert GetModuleHandleA: std import missing")
    return text.replace(
        needle,
        needle + winapi + _GETMODULE_DECL,
        1,
    )


def _apply_enterworld_superwow_skips() -> None:
    """Skip module hooks that #132 on SuperWoW 2.2 enter-world.

    Proven 2026-08-29: minimapicons owns the unmapped 0x**0F0B / 0x**120B
    RegisterCVar trampoline; transmogfix dies at WoW 0x76710E; worldmarkers
    at 0x483005; outline at 0x6DF353. DPSLog is handled separately.
    """
    void_patches: tuple[tuple[str, str], ...] = (
        (
            "src/minimapicons/minimapicons.zig",
            "    log = logging.Logger.open(module_name, .console);\n\n"
            "    if (enum_proc_hook.attach(ADDR.ObjectEnumProc, &objectEnumProcDetour) != .ok) {",
        ),
        (
            "src/transmogfix/transmogfix.zig",
            "    log = logging.Logger.open(module_name, .console);\n\n"
            "    // Initialize state (already zero-initialized by Zig defaults)",
        ),
        (
            "src/worldmarkers/worldmarkers.zig",
            "    log = logging.Logger.open(module_name, .console);\n\n"
            "    // Hook OnWorldUpdate for per-frame animation tick",
        ),
    )
    log_line = "    log = logging.Logger.open(module_name, .console);\n"
    for rel, old in void_patches:
        path = SRC / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if "SuperWoW loaded; skipping module WoW.exe hooks" in text:
            continue
        text = _ensure_getmodulehandle(text)
        if old not in text:
            raise RuntimeError(f"enter-world SuperWoW skip needle missing in {path}")
        after_log = old[len(log_line):]
        text = text.replace(old, log_line + "\n" + _ENTERWORLD_SKIP + after_log, 1)
        path.write_text(text, encoding="utf-8")
        print(f"Patched {path.relative_to(SRC)} to skip hooks when SuperWoW is loaded")

    outline = SRC / "src" / "outline" / "outline.zig"
    if outline.is_file():
        text = outline.read_text(encoding="utf-8")
        if "SuperWoW loaded; skipping module WoW.exe hooks" not in text:
            text = _ensure_getmodulehandle(text)
            old = (
                "    log = logging.Logger.open(module_name, .console);\n"
                "    tracker.initLogger();\n"
            )
            new = (
                "    log = logging.Logger.open(module_name, .console);\n"
                "    if (GetModuleHandleA(\"SuperWoWhook.dll\") != null) {\n"
                "        log.print(\"SuperWoW loaded; skipping module WoW.exe hooks\\n\");\n"
                "        log.close();\n"
                "        mod_mutex.release(&g_mutex);\n"
                "        g_is_hook_owner = false;\n"
                "        return true;\n"
                "    }\n"
                "    tracker.initLogger();\n"
            )
            if old not in text:
                raise RuntimeError(f"enter-world SuperWoW skip needle missing in {outline}")
            outline.write_text(text.replace(old, new, 1), encoding="utf-8")
            print(f"Patched {outline.relative_to(SRC)} to skip hooks when SuperWoW is loaded")


def _apply_source_patches() -> None:
    """Overlay in-repo Crash Fix / Zig compat patches onto the cloned tree."""
    _apply_dllmain_abi_patch()
    _apply_llvm_backend_patch()
    _apply_zig016_asm_clobber_patches()
    _apply_healtext_core_hook_skip()
    _apply_superwow_skip_all_core()
    _apply_lateinit_fallback()
    _apply_screenshot_superwow_guard()
    _apply_weirdperformance_superwow_skip()
    _apply_dpslog_superwow_skip()
    _apply_enterworld_superwow_skips()
    _apply_zhook_null_guards()
    src_fc = SRC / "src" / "framecrash" / "framecrash.zig"
    patch_fc = PATCHES / "framecrash.zig"
    if patch_fc.is_file() and src_fc.parent.is_dir():
        shutil.copy2(patch_fc, src_fc)
        print(f"Applied {patch_fc.relative_to(REPO_ROOT)}")
    src_hx = SRC / "src" / "healtextfix" / "healtextfix.zig"
    patch_hx = PATCHES / "healtextfix.zig"
    if patch_hx.is_file() and src_hx.parent.is_dir():
        shutil.copy2(patch_hx, src_hx)
        print(f"Applied {patch_hx.relative_to(REPO_ROOT)}")


def clone_or_update_source(*, apply_overlays: bool = False) -> None:
    git = shutil.which("git")
    if not git:
        raise RuntimeError("git is required to clone WeirdUtils source")
    if (SRC / ".git").is_dir():
        print(f"Updating {SRC}...")
        _run([git, "-C", str(SRC), "fetch", "--depth", "1", "origin"], cwd=REPO_ROOT)
        _run([git, "-C", str(SRC), "reset", "--hard", "FETCH_HEAD"], cwd=REPO_ROOT)
        if apply_overlays:
            _apply_source_patches()
        return
    if SRC.exists():
        shutil.rmtree(SRC)
    print(f"Cloning {CLONE_URL}...")
    WORK.mkdir(parents=True, exist_ok=True)
    _run([git, "clone", "--depth", "1", CLONE_URL, str(SRC)], cwd=REPO_ROOT)
    if apply_overlays:
        _apply_source_patches()


def _zig_version(zig: str) -> str:
    proc = subprocess.run([zig, "version"], check=True, text=True, capture_output=True)
    return (proc.stdout or proc.stderr or "").strip()


def _portable_zig_exe() -> Path | None:
    if not ZIG_DIR.is_dir():
        return None
    hits = sorted(ZIG_DIR.rglob("zig.exe"))
    return hits[0] if hits else None


def _bootstrap_portable_zig() -> str:
    existing = _portable_zig_exe()
    if existing and existing.is_file():
        return str(existing)
    ZIG_DIR.mkdir(parents=True, exist_ok=True)
    archive = ZIG_DIR / ZIG_ZIP_NAME
    print(f"Downloading portable Zig {ZIG_VERSION}...")
    _download(ZIG_URL, archive)
    print(f"Extracting {archive.name}...")
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(ZIG_DIR)
    exe = _portable_zig_exe()
    if not exe:
        raise RuntimeError(f"Zig zip extracted but zig.exe was not found in {ZIG_DIR}")
    return str(exe)


def _require_zig() -> str:
    zig = shutil.which("zig")
    if zig:
        version = _zig_version(zig)
        if version.startswith("0.16"):
            print(f"Using Zig {version}")
            return zig
        print(f"System Zig is {version}; bootstrapping {ZIG_VERSION} instead")
    zig = _bootstrap_portable_zig()
    version = _zig_version(zig)
    if not version.startswith("0.16"):
        raise RuntimeError(f"Zig 0.16 is required, found {version or 'unknown'}")
    print(f"Using Zig {version} ({zig})")
    return zig


def _find_built_dll(name: str) -> Path:
    zig_out = SRC / "zig-out"
    candidates = [
        zig_out / "variants" / f"{name}.dll",
        zig_out / "bin" / f"{name}.dll",
    ]
    for path in candidates:
        if path.is_file():
            return path
    if zig_out.is_dir():
        hits = sorted(zig_out.rglob(f"{name}.dll"))
        if hits:
            return hits[0]
    raise FileNotFoundError(
        f"zig build did not produce {name}.dll under {zig_out}"
    )


def _copy_prebuilt_fallback(name: str) -> Path | None:
    src = PREBUILT / f"{name}.dll"
    if not src.is_file():
        return None
    OUT.mkdir(parents=True, exist_ok=True)
    dest = OUT / f"{name}.dll"
    shutil.copy2(src, dest)
    print(f"Vendored prebuilt {dest} ({dest.stat().st_size} bytes)")
    return dest


def _zig_build(zig: str, enabled: tuple[str, ...], *, apply_overlays: bool = False) -> None:
    skip = [name for name in SKIP_DEFAULT_MODULES if name not in enabled]
    skip.extend(name for name in BUILD_MODULES if name not in enabled)
    flags = [f"-D{name}=false" for name in skip]
    flags.extend(f"-D{name}=true" for name in enabled)
    cache = WORK / "zig-cache"
    cache.mkdir(parents=True, exist_ok=True)
    cmd = [
        zig,
        "build",
        "all-variants",
        "-Doptimize=ReleaseSmall",
        "--global-cache-dir",
        str(cache),
        *flags,
    ]
    print("Building:", " ".join(cmd))
    if apply_overlays:
        _apply_zhook_null_guards()
    proc = subprocess.run(cmd, cwd=str(SRC), text=True, capture_output=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout or "")
        sys.stderr.write(proc.stderr or "")
        raise RuntimeError(f"zig build failed with exit {proc.returncode}")


def _install_bundle() -> None:
    """Copy compiled DLLs into the frozen-exe data tree used at Apply/Play."""
    BUNDLE.mkdir(parents=True, exist_ok=True)
    for name in BUILD_MODULES:
        src = OUT / f"{name}.dll"
        if not src.is_file():
            continue
        dest = BUNDLE / f"{name}.dll"
        shutil.copy2(src, dest)
        print(f"Bundled {dest} ({dest.stat().st_size} bytes)")


def build_missing_modules(*, allow_prebuilt: bool = False, apply_overlays: bool = False) -> None:
    zig = _require_zig()
    if apply_overlays:
        _apply_source_patches()
    OUT.mkdir(parents=True, exist_ok=True)
    pending = list(BUILD_MODULES)
    try:
        _zig_build(zig, tuple(pending), apply_overlays=apply_overlays)
    except RuntimeError as exc:
        print(f"Full set failed ({exc}); retrying without weirdperformance")
        pending = [name for name in BUILD_MODULES if name != "weirdperformance"]
        _zig_build(zig, tuple(pending), apply_overlays=apply_overlays)
    for name in BUILD_MODULES:
        dest = OUT / f"{name}.dll"
        try:
            built = _find_built_dll(name)
        except FileNotFoundError:
            if dest.is_file():
                continue
            if allow_prebuilt and _copy_prebuilt_fallback(name) is not None:
                continue
            raise FileNotFoundError(
                f"zig build did not produce {name}.dll. "
                f"Compile from source (Zig {ZIG_VERSION} + LLVM); "
                "official prebuilts are not used."
            ) from None
        shutil.copy2(built, dest)
        print(f"Wrote {dest} ({dest.stat().st_size} bytes)")
    _install_bundle()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--download-only",
        action="store_true",
        help="Fetch Marceline's Codeberg release PEs (default if no build flag)",
    )
    parser.add_argument(
        "--download-prebuilt",
        action="store_true",
        help="Same as --download-only (kept for older scripts)",
    )
    parser.add_argument(
        "--from-source",
        action="store_true",
        help="Clone her repo and run zig build all-variants with no overlays",
    )
    parser.add_argument(
        "--overlay-build",
        action="store_true",
        help="Old IchaLaunch overlay compile. Do not ship these PEs.",
    )
    parser.add_argument(
        "--build-only",
        action="store_true",
        help="Alias for --from-source (her command, no overlays)",
    )
    args = parser.parse_args(argv)
    WORK.mkdir(parents=True, exist_ok=True)
    try:
        want_source = bool(args.from_source or args.build_only or args.overlay_build)
        if not want_source or args.download_only or args.download_prebuilt:
            download_official_releases()
        if args.overlay_build:
            clone_or_update_source(apply_overlays=True)
            build_missing_modules(allow_prebuilt=False, apply_overlays=True)
        elif args.from_source or args.build_only:
            clone_or_update_source(apply_overlays=False)
            build_missing_modules(allow_prebuilt=False, apply_overlays=False)
    except subprocess.CalledProcessError as exc:
        err = (exc.stderr or exc.stdout or str(exc)).strip()
        print(f"ERROR: {err}", file=sys.stderr)
        return 1
    except (OSError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Done. Official PEs: {OUT}  Bundled: {BUNDLE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

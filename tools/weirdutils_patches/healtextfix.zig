//! SuperWoW Heal Text Fix
//!
//! Patches SuperWoWhook.dll at runtime to disable the duplicate floating
//! healing combat text that SuperWoW 1.5.1 adds.
//!
//! SuperWoW-only: this module must never attach WoW.exe detours. The shared
//! WeirdUtils `install()` hooks Storm/Lua/engine RVAs; on SuperWoW 2.x those
//! sites are already owned and a trampoline/`callOriginal` of 0 is
//! ERROR #132 ACCESS_VIOLATION at EIP 0. `main.zig` skips core hooks for the
//! healtextfix-only variant. Here we only patch SuperWoW, and only when the
//! version and old bytes match. Missing SuperWoW or SuperWoW 2.2 / 1.18 is a
//! clean no-op (DllMain still returns TRUE).
//!
//! Based on:
//!   - https://github.com/MarcelineVQ/SuperWoWHealTextFix
//!   - https://github.com/turtlenips/superwow-patch
//!
//! The turtlenips version adds two extra patches (at file offsets 0x306E and
//! 0x3123) that fix HoT ticks (e.g. Renew) showing in the wrong color by
//! redirecting function pointers.
//!
//! The reference repos patch the DLL on disk before loading, which prevents
//! the hook registration call from executing. Since we patch at runtime (after
//! SuperWoW has already initialized and installed its hooks), we must instead
//! patch the handler function itself to skip its duplicate text creation.
//!
//! Patches applied to SuperWoWhook.dll in memory (SuperWoW 1.5.x only):
//!
//!   1. 0x3006 (2 bytes) - Skip duplicate heal text in handler
//!      The handler at RVA 0x3BF0 creates floating text, then calls through
//!      to the original wow.exe function (which also creates text = duplicate).
//!      Patch MOV ECX,[EDI] -> JMP +0x7A to skip to the call-through at 0x3C82.
//!      Old: 8B 0F
//!      New: EB 7A
//!
//!   2. 0x306E (4 bytes) - Redirect HoT text handler pointer
//!      Old: 9C D8 C4 00
//!      New: 06 7C 44 00
//!
//!   3. 0x3123 (4 bytes) - Redirect HoT text handler pointer (second site)
//!      Old: 9C D8 C4 00
//!      New: 06 7C 44 00
//!
//! NOTE: These are file offsets, converted to virtual addresses via PE section
//! headers at runtime.

const std = @import("std");
const hook = @import("zhook");
const logging = @import("../logging.zig");

const WINAPI = std.builtin.CallingConvention.winapi;
extern "kernel32" fn GetModuleHandleA(lpModuleName: ?[*:0]const u8) callconv(WINAPI) ?*anyopaque;
extern "kernel32" fn IsBadReadPtr(lp: ?*const anyopaque, ucb: usize) callconv(WINAPI) i32;
extern "kernel32" fn GetCurrentProcessId() callconv(WINAPI) u32;
extern "kernel32" fn CreateFileA(
    lpFileName: [*:0]const u8,
    dwDesiredAccess: u32,
    dwShareMode: u32,
    lpSecurityAttributes: ?*anyopaque,
    dwCreationDisposition: u32,
    dwFlagsAndAttributes: u32,
    hTemplateFile: ?*anyopaque,
) callconv(WINAPI) ?*anyopaque;
extern "kernel32" fn WriteFile(
    hFile: *anyopaque,
    lpBuffer: [*]const u8,
    nNumberOfBytesToWrite: u32,
    lpNumberOfBytesWritten: ?*u32,
    lpOverlapped: ?*anyopaque,
) callconv(WINAPI) i32;
extern "kernel32" fn SetFilePointer(
    hFile: *anyopaque,
    lDistanceToMove: i32,
    lpDistanceToMoveHigh: ?*i32,
    dwMoveMethod: u32,
) callconv(WINAPI) u32;
extern "kernel32" fn CloseHandle(hObject: *anyopaque) callconv(WINAPI) i32;

const GENERIC_WRITE: u32 = 0x40000000;
const FILE_SHARE_READ: u32 = 0x00000001;
const CREATE_ALWAYS: u32 = 2;
const OPEN_ALWAYS: u32 = 4;
const FILE_ATTRIBUTE_NORMAL: u32 = 0x80;
const FILE_END: u32 = 2;
const INVALID_HANDLE_VALUE: usize = 0xFFFFFFFF;
var g_attach_log_started: bool = false;

fn writeAttachLog(msg: []const u8) void {
    const disposition: u32 = if (g_attach_log_started) OPEN_ALWAYS else CREATE_ALWAYS;
    const fh = CreateFileA(
        "healtextfix.log",
        GENERIC_WRITE,
        FILE_SHARE_READ,
        null,
        disposition,
        FILE_ATTRIBUTE_NORMAL,
        null,
    ) orelse return;
    if (@intFromPtr(fh) == INVALID_HANDLE_VALUE) return;
    defer _ = CloseHandle(fh);
    if (g_attach_log_started) {
        _ = SetFilePointer(fh, 0, null, FILE_END);
    }
    g_attach_log_started = true;
    _ = WriteFile(fh, msg.ptr, @intCast(msg.len), null, null);
}

const Patch = struct {
    /// File offset into SuperWoWhook.dll
    file_offset: u32,
    old: []const u8,
    new: []const u8,
    /// Optional mask for old-byte verification. 0xFF = must match, 0x00 = skip
    /// (relocated operands). null = check all bytes exactly.
    mask: ?[]const u8 = null,
};

const PatchSet = struct {
    version: []const u8,
    patches: []const Patch,
};

const v1_5_patches = [_]Patch{
    // Patch 0: Skip duplicate heal text in the SuperWoW handler
    // The handler at RVA 0x3BF0 creates floating text then calls the original
    // (which also creates text). JMP from 0x3C06 to the call-through at 0x3C82.
    .{
        .file_offset = 0x3006,
        .old = &.{ 0x8B, 0x0F },
        .new = &.{ 0xEB, 0x7A },
    },
    // Patch 1: Redirect HoT text handler pointer (fixes Renew etc. color)
    .{
        .file_offset = 0x306E,
        .old = &.{ 0x9C, 0xD8, 0xC4, 0x00 },
        .new = &.{ 0x06, 0x7C, 0x44, 0x00 },
    },
    // Patch 2: Redirect HoT text handler pointer (second call site)
    .{
        .file_offset = 0x3123,
        .old = &.{ 0x9C, 0xD8, 0xC4, 0x00 },
        .new = &.{ 0x06, 0x7C, 0x44, 0x00 },
    },
};

const patch_sets = [_]PatchSet{
    .{ .version = "1.5", .patches = &v1_5_patches },
};

fn printHex(prefix: []const u8, bytes: []const u8) void {
    log.print(prefix);
    for (bytes) |b| {
        log.fmt("{x:0>2} ", .{b});
    }
    log.print("\n");
}

fn bytesMatchMasked(got: [*]const u8, expected: []const u8, mask: ?[]const u8) bool {
    for (0..expected.len) |j| {
        const m: u8 = if (mask) |msk| msk[j] else 0xFF;
        if (got[j] & m != expected[j] & m) return false;
    }
    return true;
}

/// Refuse a patch that would write a NULL absolute pointer or an E9 JMP whose
/// rel32 target is 0 (never write a JMP to 0).
fn patchPayloadSafe(new: []const u8, target_va: usize) bool {
    if (new.len == 0) return false;
    if (new.len >= 5 and new[0] == 0xE9) {
        const rel = std.mem.readInt(i32, new[1..5], .little);
        const dest = target_va +% 5 +% @as(usize, @bitCast(@as(isize, rel)));
        if (dest == 0) return false;
    }
    if (new.len == 4) {
        const va = std.mem.readInt(u32, new[0..4], .little);
        if (va == 0) return false;
        if (IsBadReadPtr(@ptrFromInt(va), 1) != 0) return false;
    }
    return true;
}

fn peLooksReadable(base: [*]const u8) bool {
    if (IsBadReadPtr(base, 0x40) != 0) return false;
    if (base[0] != 'M' or base[1] != 'Z') return false;
    const e_lfanew = std.mem.readInt(u32, base[0x3C..0x40], .little);
    if (e_lfanew < 4 or e_lfanew > 0x1000) return false;
    if (IsBadReadPtr(base + e_lfanew, 24) != 0) return false;
    return base[e_lfanew] == 'P' and base[e_lfanew + 1] == 'E';
}

/// Convert a file offset to a virtual address by walking PE section headers.
fn fileOffsetToVA(base: [*]const u8, file_offset: u32) ?[*]u8 {
    if (!peLooksReadable(base)) return null;

    const e_lfanew = std.mem.readInt(u32, base[0x3C..0x40], .little);
    const pe_base = base + e_lfanew;

    const num_sections = std.mem.readInt(u16, pe_base[6..8], .little);
    const opt_hdr_size = std.mem.readInt(u16, pe_base[20..22], .little);
    if (num_sections == 0 or num_sections > 96) return null;
    if (IsBadReadPtr(pe_base + 24, opt_hdr_size + @as(usize, num_sections) * 40) != 0) return null;

    const sections_start = pe_base + 24 + opt_hdr_size;

    var i: u16 = 0;
    while (i < num_sections) : (i += 1) {
        const sec = sections_start + @as(usize, i) * 40;
        const virt_addr = std.mem.readInt(u32, sec[12..16], .little);
        const raw_offset = std.mem.readInt(u32, sec[20..24], .little);
        const raw_size = std.mem.readInt(u32, sec[16..20], .little);

        if (file_offset >= raw_offset and file_offset < raw_offset + raw_size) {
            const rva = virt_addr + (file_offset - raw_offset);
            const va: [*]u8 = @ptrFromInt(@intFromPtr(base) + rva);
            if (IsBadReadPtr(va, 1) != 0) return null;
            return va;
        }
    }
    return null;
}

const mod_mutex = @import("../mutex.zig");

pub const module_name: [*:0]const u8 = "healtextfix";

var g_mutex: ?*anyopaque = null;
var g_is_hook_owner: bool = false;
var log: logging.Logger = .{};
var g_applied_set: ?*const PatchSet = null;

pub fn isActive() bool {
    return g_is_hook_owner;
}

fn mappedImageSize(base: [*]const u8) u32 {
    if (!peLooksReadable(base)) return 0;
    const e_lfanew = std.mem.readInt(u32, base[0x3C..0x40], .little);
    const opt = base + e_lfanew + 24;
    if (IsBadReadPtr(opt, 60) != 0) return 0;
    const magic = std.mem.readInt(u16, opt[0..2], .little);
    if (magic != 0x10B) return 0;
    return std.mem.readInt(u32, opt[56..60], .little);
}

/// Scan SuperWoWhook's mapped image for SUPERWOW_VERSION="...".
/// Official 1.5 builds keep this in the first 128K; SuperWoW 2.2 stores it
/// around RVA 0xEC349 (past the old cap), so we walk SizeOfImage.
fn detectVersion(base: [*]const u8) ?[]const u8 {
    var scan_len: usize = mappedImageSize(base);
    if (scan_len < 0x200) scan_len = 0x20000;
    if (scan_len > 0x200000) scan_len = 0x200000;
    if (IsBadReadPtr(base, scan_len) != 0) {
        scan_len = 0x20000;
        if (IsBadReadPtr(base, scan_len) != 0) return null;
    }
    const needle = "SUPERWOW_VERSION=\"";
    const mem = base[0..scan_len];

    const pos = std.mem.indexOf(u8, mem, needle) orelse return null;
    const ver_start = pos + needle.len;
    const remaining = mem[ver_start..];
    const end = std.mem.indexOfScalar(u8, remaining, '"') orelse return null;
    if (end == 0 or end > 16) return null;
    return remaining[0..end];
}

fn versionMatches(detected: []const u8, set_ver: []const u8) bool {
    if (std.mem.eql(u8, detected, set_ver)) return true;
    return detected.len > set_ver.len and std.mem.startsWith(u8, detected, set_ver) and detected[set_ver.len] == '.';
}

fn findPatchSet(version: []const u8) ?*const PatchSet {
    for (&patch_sets) |*ps| {
        if (versionMatches(version, ps.version)) return ps;
    }
    return null;
}

pub fn installHooks() void {
    const result = mod_mutex.acquire(module_name);
    g_mutex = result.handle;
    g_is_hook_owner = result.is_owner;
    if (g_is_hook_owner) log = logging.Logger.open(module_name, .console);
    tryPatchSuperWow();
}

/// Called from engineInitDetour when this DLL is part of a multi-module build
/// that still installs the GameEngine_MainInitialize hook. healtextfix-only
/// skips that hook; DllMain/installHooks already ran tryPatchSuperWow.
pub fn lateInit() void {
    tryPatchSuperWow();
}

fn tryPatchSuperWow() void {
    if (!g_is_hook_owner) return;
    if (g_applied_set != null) return;

    const superwow_base = GetModuleHandleA("SuperWoWhook.dll");
    var hdr: [256]u8 = undefined;
    const line = std.fmt.bufPrint(&hdr, "healtextfix attach pid={d} superwow={d}\n", .{
        GetCurrentProcessId(),
        @intFromBool(superwow_base != null),
    }) catch hdr[0..0];
    if (line.len > 0) writeAttachLog(line);

    if (superwow_base == null) {
        log.print("SuperWoWhook.dll not found, skipping\n");
        writeAttachLog("SKIP: SuperWoWhook.dll not loaded; no WoW.exe hooks\n");
        return;
    }

    const base: [*]const u8 = @ptrCast(superwow_base.?);
    if (!peLooksReadable(base)) {
        log.print("SuperWoWhook.dll PE is not readable, skipping\n");
        writeAttachLog("SKIP: SuperWoW PE unreadable\n");
        return;
    }
    log.fmt("SuperWoWhook.dll at 0x{x}\n", .{@intFromPtr(base)});

    const version = detectVersion(base) orelse {
        log.print("Could not detect SuperWoW version, skipping\n");
        writeAttachLog("SKIP: SUPERWOW_VERSION not found in mapped image\n");
        return;
    };
    log.fmt("Detected SuperWoW version: {s}\n", .{version});
    var verbuf: [80]u8 = undefined;
    const verline = std.fmt.bufPrint(&verbuf, "version={s}\n", .{version}) catch verbuf[0..0];
    if (verline.len > 0) writeAttachLog(verline);

    const set = findPatchSet(version) orelse {
        log.fmt("No patches for version \"{s}\", skipping\n", .{version});
        writeAttachLog("SKIP: no patch set (need SuperWoW 1.5.x); SuperWoW 2.x is a no-op\n");
        return;
    };

    var applied: u32 = 0;
    for (set.patches, 0..) |patch, idx| {
        const va = fileOffsetToVA(base, patch.file_offset) orelse {
            log.fmt("Patch {d}: failed to resolve file offset 0x{x}\n", .{ idx, patch.file_offset });
            writeAttachLog("SKIP patch: file offset not in SuperWoW image\n");
            continue;
        };

        const target: [*]u8 = va;
        if (IsBadReadPtr(target, patch.old.len) != 0 or IsBadReadPtr(target, patch.new.len) != 0) {
            log.fmt("Patch {d}: target not readable at VA 0x{x}\n", .{ idx, @intFromPtr(target) });
            writeAttachLog("SKIP patch: target bytes not readable\n");
            continue;
        }

        if (!patchPayloadSafe(patch.new, @intFromPtr(target))) {
            log.fmt("Patch {d}: refusing unsafe payload (NULL pointer or JMP to 0)\n", .{idx});
            writeAttachLog("SKIP patch: payload would write NULL / JMP to 0\n");
            continue;
        }

        if (!bytesMatchMasked(target, patch.old, patch.mask)) {
            if (bytesMatchMasked(target, patch.new, patch.mask)) {
                log.fmt("Patch {d}: already applied\n", .{idx});
                applied += 1;
            } else {
                log.fmt("Patch {d}: unexpected bytes at VA 0x{x}\n", .{ idx, @intFromPtr(target) });
                printHex("[healtextfix]   expected: ", patch.old);
                printHex("[healtextfix]   found:    ", target[0..patch.old.len]);
                writeAttachLog("SKIP patch: old bytes mismatch\n");
            }
            continue;
        }

        hook.writeProtected(@intFromPtr(target), patch.new);
        applied += 1;
        log.fmt("Patch {d}: applied at VA 0x{x}\n", .{ idx, @intFromPtr(target) });
    }

    if (applied > 0) g_applied_set = set;
    log.fmt("{d}/{d} patches applied\n", .{ applied, set.patches.len });
    var sum: [64]u8 = undefined;
    const summary = std.fmt.bufPrint(&sum, "applied={d}/{d}\n", .{ applied, set.patches.len }) catch sum[0..0];
    if (summary.len > 0) writeAttachLog(summary);
}

pub fn removeHooks() void {
    if (!g_is_hook_owner) return;

    const set = g_applied_set orelse {
        log.close();
        mod_mutex.release(&g_mutex);
        g_is_hook_owner = false;
        return;
    };

    const superwow_base = GetModuleHandleA("SuperWoWhook.dll");
    if (superwow_base == null) {
        g_applied_set = null;
        log.close();
        mod_mutex.release(&g_mutex);
        g_is_hook_owner = false;
        return;
    }

    const base: [*]const u8 = @ptrCast(superwow_base.?);
    if (!peLooksReadable(base)) {
        g_applied_set = null;
        log.close();
        mod_mutex.release(&g_mutex);
        g_is_hook_owner = false;
        return;
    }

    for (set.patches, 0..) |patch, idx| {
        const va = fileOffsetToVA(base, patch.file_offset) orelse continue;
        const target: [*]u8 = va;
        if (IsBadReadPtr(target, patch.new.len) != 0) continue;

        if (bytesMatchMasked(target, patch.new, patch.mask)) {
            hook.writeProtected(@intFromPtr(target), patch.old);
            log.fmt("Patch {d}: restored\n", .{idx});
        }
    }

    g_applied_set = null;
    log.print("All patches restored\n");
    log.close();
    mod_mutex.release(&g_mutex);
    g_is_hook_owner = false;
}

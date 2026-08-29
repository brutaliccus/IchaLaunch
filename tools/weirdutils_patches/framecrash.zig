//! Framecrash fix module.
//!
//! Fixes ACCESS_VIOLATION crashes caused by UI frame anchor objects holding raw
//! pointers to their "relativeTo" frame (anchor+0x0C). When the relativeTo frame
//! is destroyed, the pointer is never cleared, causing crashes in any code that
//! dereferences it (luaGetPoint, luaGetWidth, luaGetHeight, etc).
//!
//! Root cause fix: detour hook on cleanup_linked_list_structures (0x767720),
//! the common frame destruction path. Before the original runs, we walk the
//! dying frame's PauseAnimationGroup dependency list (frame+0x34) to find all
//! other frames whose anchors reference the dying frame. For each, we NULL the
//! relativeTo field, preventing any stale pointer dereferences.
//!
//! Defense-in-depth: anchor vtable hooks on [1] GetWidth, [2] GetHeight, and
//! [3] GetRelativeTo. Each independently validates anchor+0x0C (relativeTo)
//! with IsBadReadPtr before use. GetWidth/GetHeight return the layout sentinel
//! from [0x00cf550c] when relativeTo is invalid. Catches cases the root cause
//! fix misses (frames destroyed through paths other than cleanup_linked_list).
//!
//! SuperWoW 2.2: official framecrash dies on shared Storm/Lua/engine hooks
//! (EIP 0 / 0xC0000096) and on GetFrameFromLua (0x76c760) during layout.
//! This overlay never calls that Lua path. Function sites attach only when
//! the prologue is still stock (refuse NULL / SuperWoW E9 / foreign trampolines).
//! When SuperWoW is loaded, attach is deferred so runtime hooks are visible.
//!
//! See RESEARCH.md for full reverse engineering notes.

const std = @import("std");
const hook = @import("zhook");
const logging = @import("../logging.zig");

const WINAPI = std.builtin.CallingConvention.winapi;

extern "kernel32" fn IsBadReadPtr(lp: ?*const anyopaque, ucb: usize) callconv(WINAPI) i32;
extern "kernel32" fn CreateMutexA(lpMutexAttributes: ?*anyopaque, bInitialOwner: i32, lpName: [*:0]const u8) callconv(WINAPI) ?*anyopaque;
extern "kernel32" fn ReleaseMutex(hMutex: *anyopaque) callconv(WINAPI) i32;
extern "kernel32" fn CloseHandle(hObject: *anyopaque) callconv(WINAPI) i32;
extern "kernel32" fn GetLastError() callconv(WINAPI) u32;
extern "kernel32" fn GetCurrentProcessId() callconv(WINAPI) u32;
extern "kernel32" fn GetModuleHandleA(lpModuleName: ?[*:0]const u8) callconv(WINAPI) ?*anyopaque;
extern "kernel32" fn Sleep(dwMilliseconds: u32) callconv(WINAPI) void;
extern "kernel32" fn CreateThread(
    lpThreadAttributes: ?*anyopaque,
    dwStackSize: usize,
    lpStartAddress: *const fn (?*anyopaque) callconv(WINAPI) u32,
    lpParameter: ?*anyopaque,
    dwCreationFlags: u32,
    lpThreadId: ?*u32,
) callconv(WINAPI) ?*anyopaque;
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
const ERROR_ALREADY_EXISTS: u32 = 183;

const mod_mutex = @import("../mutex.zig");

pub const module_name: [*:0]const u8 = "framecrash";

var g_mutex: ?*anyopaque = null;
var g_is_hook_owner: bool = false;
var g_protection_active: bool = false;
var g_attached: bool = false;
var g_stop: bool = false;
var log: logging.Logger = .{};

pub fn isActive() bool {
    return g_protection_active;
}

// =============================================================================
// Anchor vtable layout (20-byte object allocated in SetPoint / SetAnimationOrder)
//
//   +0x00  vtable ptr  → 0x0081c44c (.rdata)
//   +0x04  x offset    (float)
//   +0x08  y offset    (float)
//   +0x0C  relativeTo  (raw frame pointer - the dangerous one)
//   +0x10  relPoint    (uint, anchor point enum on the relativeTo frame)
//
// vtable at 0x0081c44c:
//   [0] +0x00  GetAnimationOrder (0x767d80) - destructor/cleanup
//   [1] +0x04  luaGetWidth       (0x7a2f90) - reads [this+0xC]+0x3C
//   [2] +0x08  luaGetHeight      (0x7a3070) - reads [this+0xC]+0x3C
//   [3] +0x0C  GetRelativeTo     (0x767d70) - returns *(this+0x0C)
// =============================================================================

const ANCHOR_VTABLE_ADDR: usize = 0x0081c44c;
const GET_RELATIVE_TO_SLOT: usize = ANCHOR_VTABLE_ADDR + 0x0C; // vtable[3]

// Stock 1.12.1 (5875) prologues. Turtle/RavenCraft 1.18 or SuperWoW may rewrite
// these sites; mismatch must abort install (a JMP into the wrong function
// crashes the client immediately).
const CLEANUP_PROLOGUE = [_]u8{ 0x53, 0x56, 0x8B, 0xF1, 0x57 };
const DESTROY_UI_PROLOGUE = [_]u8{ 0x55, 0x8B, 0xEC, 0x56, 0x8B, 0xF1 };
const PROCESS_UI_PROLOGUE = [_]u8{ 0x55, 0x8B, 0xEC, 0x56, 0x8B, 0xF1 };
const PAUSE_ANIM_PROLOGUE = [_]u8{ 0x55, 0x8B, 0xEC, 0x53, 0x8B, 0xD9 };
const SET_ANIM_PROLOGUE = [_]u8{ 0x55, 0x8B, 0xEC, 0x8B, 0x45, 0x0C };

// Cached live UIParent CLayoutFrame inner. Filled when PauseAnimationGroup /
// SetAnimationOrder see a valid named "UIParent". Never resolved via
// GetFrameFromLua — that path reenters Lua during layout and crashes
// (see RESEARCH.md "Heal Implementation -- STATUS: CRASHES").
var g_cached_uiparent: u32 = 0;

// =============================================================================
// Root cause fix: hook frame destruction to clean up reverse anchor references
//
// cleanup_linked_list_structures (0x767720) is the common frame cleanup path,
// called from destroy_object, CleanupRegion, and cleanupGraphicsResources.
// It cleans up the dying frame's OWN anchors (forward direction) but NOT other
// frames' anchors that reference the dying frame (reverse direction).
//
// PauseAnimationGroup maintains a linked list on the relativeTo frame:
//   frame+0x34 → first node
//   Each node (0x10 bytes): [link0, next(+4), owner_frame(+8), bitmask(+C)]
//   bitmask = OR of (1 << anchor_point_enum) for each referencing anchor slot
//
// Anchor slots in a frame: frame + point_enum*4 + 4  (9 slots, enum 0..8)
//
// Prologue at 0x767720 (5 bytes, no rel32):
//   53 56 8B F1 57  =  PUSH EBX; PUSH ESI; MOV ESI,ECX; PUSH EDI
// =============================================================================

const CLEANUP_TARGET: usize = 0x767720;

const CleanupFn = fn (u32) callconv(hook.cc.thiscall) void;
var cleanup_hook: hook.Detour(CleanupFn) = .{};

// =============================================================================
// Second destruction path: destroyUIElement (0x7645a0)
//
// Called from cleanupGraphicsResources (UI teardown/reload) via the strata loop.
// Frees frames WITHOUT calling cleanup_linked_list_structures - just unlinks from
// lists, cleans up sub-regions, and calls FreeMemory. The dependency list at
// frame+0x34 is never walked, so other frames' anchors are left dangling.
//
// Signature: void* __thiscall destroyUIElement(void* this, byte free_flag)
// Prologue: 55 8B EC 56 8B F1 (6 bytes, no rel32)
// =============================================================================

const DESTROY_UI_TARGET: usize = 0x7645a0;

const DestroyUIFn = fn (u32, u32) callconv(hook.cc.thiscall) u32;
var destroy_ui_hook: hook.Detour(DestroyUIFn) = .{};

// =============================================================================
// Third destruction path: ProcessUIUpdateEvent (0x772ec0)
//
// Virtual function (vtable entry at 0x81c7ac), called via vtable dispatch.
// Calls CleanupUIElement + FreeMemory without cleanup_linked_list_structures.
// Signature: void* __thiscall ProcessUIUpdateEvent(void* this, byte free_flag)
// Prologue: 55 8B EC 56 8B F1 (6 bytes, no rel32)
// =============================================================================

const PROCESS_UI_TARGET: usize = 0x772ec0;

const ProcessUIFn = fn (u32, u32) callconv(hook.cc.thiscall) u32;
var process_ui_hook: hook.Detour(ProcessUIFn) = .{};

// =============================================================================
// Priority 1: Hook PauseAnimationGroup (0x767ee0) - dependency registration
//
// Records every dependency registration so we can later determine whether a
// stale relativeTo pointer was ever registered through PauseAnimationGroup.
// If PauseAnimationGroup was never called for an address, the dependency was
// never created - pointing to a race condition or unknown creation path.
//
// Signature: void __thiscall PauseAnimationGroup(ECX=relativeTo_frame, owner_frame, bitmask)
// Prologue: 55 8B EC 53 8B D9 (6 bytes, no rel32)
// RET 0x8 (callee cleans 2 stack args)
// =============================================================================

const PAUSE_ANIM_TARGET: usize = 0x767ee0;

const PauseAnimFn = fn (u32, u32, u32) callconv(hook.cc.thiscall) void;
var pause_anim_hook: hook.Detour(PauseAnimFn) = .{};

// =============================================================================
// Priority 2: Hook SetAnimationOrder (0x767c70) - anchor creation validation
//
// Validates the relativeTo param with IsBadReadPtr BEFORE the original runs.
// If relativeTo is already freed when the anchor is created, the dependency
// list node goes on dead frame memory (which may be reused). Detects race
// conditions at anchor creation time.
//
// Signature: void __thiscall SetAnimationOrder(ECX=frame, point_enum, relativeTo,
//            relPoint, xOfs, yOfs, param_6)
// Prologue: 55 8B EC 8B 45 0C (6 bytes, no rel32)
// RET 0x18 (callee cleans 6 stack args)
// =============================================================================

const SET_ANIM_TARGET: usize = 0x767c70;

const SetAnimFn = fn (u32, u32, u32, u32, u32, u32, u32) callconv(hook.cc.thiscall) void;
var set_anim_hook: hook.Detour(SetAnimFn) = .{};

// =============================================================================
// Dependency registration ring buffer - track PauseAnimationGroup calls
//
// When vtable hooks detect a stale pointer, we look up this buffer to answer:
// "was PauseAnimationGroup ever called for this address?"
// =============================================================================

const REG_HISTORY_SIZE = 2048;

const DepRegistration = struct {
    relativeTo: u32 = 0, // the frame being depended upon (ECX of PauseAnimationGroup)
    owner: u32 = 0, // the frame that owns the anchor
    bitmask: u32 = 0, // which anchor slots (OR of 1<<point_enum)
    seq: u32 = 0, // monotonic sequence number for ordering
    name: [31:0]u8 = @splat(0), // name of relativeTo frame at registration time
};

var reg_history: [REG_HISTORY_SIZE]DepRegistration = @splat(.{});
var reg_idx: u32 = 0;

fn recordRegistration(relativeTo: u32, owner: u32, bitmask: u32) void {
    var entry = DepRegistration{
        .relativeTo = relativeTo,
        .owner = owner,
        .bitmask = bitmask,
        .seq = reg_idx,
    };
    // Capture the frame name while it's still alive
    if (getFrameName(relativeTo)) |fname| {
        const span = std.mem.span(fname);
        const len = @min(span.len, 31);
        @memcpy(entry.name[0..len], span[0..len]);
    }
    reg_history[reg_idx % REG_HISTORY_SIZE] = entry;
    reg_idx +%= 1;
}

/// Look up whether PauseAnimationGroup was ever called for a given relativeTo address.
/// Returns the most recent registration entry if found, null otherwise.
fn lookupRegistration(relativeTo: u32) ?DepRegistration {
    var best: ?DepRegistration = null;
    for (&reg_history) |*entry| {
        if (entry.relativeTo == relativeTo) {
            if (best == null or entry.seq > best.?.seq) {
                best = entry.*;
            }
        }
    }
    return best;
}

/// Count all registrations for a given relativeTo address.
fn countRegistrations(relativeTo: u32) u32 {
    var count: u32 = 0;
    for (&reg_history) |*entry| {
        if (entry.relativeTo == relativeTo) count += 1;
    }
    return count;
}

/// Get the name stored at registration time for a relativeTo address.
fn getRegisteredName(relativeTo: u32) []const u8 {
    if (lookupRegistration(relativeTo)) |reg| {
        const span = std.mem.sliceTo(&reg.name, 0);
        if (span.len > 0) return span;
    }
    return "(unknown)";
}

// =============================================================================
// Destruction history ring buffer - correlate stale pointers with frame names
// =============================================================================

const HISTORY_SIZE = 1024;

const DestroyedFrame = struct {
    addr: u32 = 0,
    name: [63:0]u8 = @splat(0),
};

var destroy_history: [HISTORY_SIZE]DestroyedFrame = @splat(.{});
var history_idx: u32 = 0;

fn recordDestruction(layout_frame: u32) void {
    var entry = DestroyedFrame{};
    entry.addr = layout_frame;

    if (getFrameName(layout_frame)) |name| {
        const span = std.mem.span(name);
        const len = @min(span.len, 63);
        @memcpy(entry.name[0..len], span[0..len]);
    }

    destroy_history[history_idx % HISTORY_SIZE] = entry;
    history_idx +%= 1;
}

fn lookupDestroyed(layout_frame: u32) ?[]const u8 {
    for (&destroy_history) |*entry| {
        if (entry.addr == layout_frame) {
            const span = std.mem.sliceTo(&entry.name, 0);
            return if (span.len > 0) span else null;
        }
    }
    return null;
}

/// Format info about a stale relativeTo for vtable hook logging.
/// Checks the ring buffer first; falls back to reading (possibly garbage) memory.
fn fmtStaleInfo(relativeTo: u32) struct { name: []const u8, saw_destroy: bool } {
    if (lookupDestroyed(relativeTo)) |name| {
        return .{ .name = name, .saw_destroy = true };
    }
    return .{ .name = fmtFrameName(relativeTo), .saw_destroy = false };
}

// logRegistrationStatus and dumpStaleContext removed - verbose diagnostic logging
// superseded by HEAL/FIX/RACE messages. Ring buffers still used by tryFixStaleRelativeTo.

/// Detour for cleanup_linked_list_structures. Runs before the original to
/// walk the dying frame's dependency list and destroy referencing anchors.
fn cleanupDetour(frame: u32) callconv(hook.cc.thiscall) void {
    invalidateCachedUIParent(frame);
    recordDestruction(frame);
    cleanupReverseDependencies(frame);
    cleanup_hook.callOriginal(.{frame});
}

/// Detour for destroyUIElement. This is the second frame destruction path,
/// called from cleanupGraphicsResources during UI teardown/reload. The original
/// frees frames without walking the dependency list, leaving stale anchors.
/// Signature: void* __thiscall destroyUIElement(void* this, byte free_flag)
fn destroyUIDetour(frame: u32, free_flag: u32) callconv(hook.cc.thiscall) u32 {
    invalidateCachedUIParent(frame);
    recordDestruction(frame);
    if (IsBadReadPtr(@ptrFromInt(frame + 0x24), 4) == 0) {
        recordDestruction(frame + 0x24);
    }
    cleanupReverseDependencies(frame);
    cleanupReverseDependencies(frame + 0x24);
    return destroy_ui_hook.callOriginal(.{ frame, free_flag });
}

/// Detour for ProcessUIUpdateEvent - third destruction path, called via vtable.
fn processUIDetour(frame: u32, free_flag: u32) callconv(hook.cc.thiscall) u32 {
    invalidateCachedUIParent(frame);
    recordDestruction(frame);
    if (IsBadReadPtr(@ptrFromInt(frame + 0x24), 4) == 0) {
        recordDestruction(frame + 0x24);
    }
    cleanupReverseDependencies(frame);
    cleanupReverseDependencies(frame + 0x24);
    return process_ui_hook.callOriginal(.{ frame, free_flag });
}

/// Detour for PauseAnimationGroup - records every dependency registration.
/// This tells us whether a stale relativeTo was ever registered through the
/// normal dependency tracking system.
/// Signature: void __thiscall PauseAnimationGroup(ECX=relativeTo_frame, owner_frame, bitmask)
fn pauseAnimDetour(relativeTo_frame: u32, owner_frame: u32, bitmask: u32) callconv(hook.cc.thiscall) void {
    recordRegistration(relativeTo_frame, owner_frame, bitmask);
    cacheIfUIParent(relativeTo_frame);

    pause_anim_hook.callOriginal(.{ relativeTo_frame, owner_frame, bitmask });
}

/// Detour for SetAnimationOrder - validates relativeTo param before anchor creation.
/// Float params (xOfs, yOfs) are passed as raw u32 bit patterns on the stack.
fn setAnimOrderDetour(frame: u32, point_enum: u32, relativeTo: u32, rel_point: u32, x_ofs: u32, y_ofs: u32, param_6: u32) callconv(hook.cc.thiscall) void {
    var fixed_relativeTo = relativeTo;

    // Validate relativeTo BEFORE the original creates the anchor.
    // Check BOTH the CLayoutFrame inner (relativeTo) AND the CFrame base (relativeTo - 0x24).
    // The stale pointer pattern: CFrame base crosses a page boundary, first page is
    // decommitted but second page (containing the CLayoutFrame inner) survives.
    if (relativeTo != 0 and relativeTo != frame) {
        const frame_base = relativeTo -% 0x24;
        const inner_bad = IsBadReadPtr(@ptrFromInt(relativeTo), 0x10) != 0;
        const base_bad = relativeTo >= 0x24 and IsBadReadPtr(@ptrFromInt(frame_base), 0x10) != 0;

        if (inner_bad or base_bad) {
            // Dead relativeTo detected. Do a live Lua lookup for UIParent.
            const live_uiparent = getLiveUIParent();

            if (live_uiparent != 0 and live_uiparent != relativeTo) {
                log.fmt("FIX: SetAnimOrder dead relativeTo=0x{x:0>8} -> UIParent=0x{x:0>8}, owner=\"{s}\" point={d}\n", .{
                    relativeTo, live_uiparent, fmtFrameName(frame), point_enum,
                });
                fixed_relativeTo = live_uiparent;
            } else {
                log.fmt("RACE: SetAnimOrder dead relativeTo=0x{x:0>8}, no live UIParent! owner=\"{s}\" point={d}\n", .{
                    relativeTo, fmtFrameName(frame), point_enum,
                });
            }
        }
    }

    if (fixed_relativeTo != 0 and isRelativeToValid(fixed_relativeTo)) {
        cacheIfUIParent(fixed_relativeTo);
    }

    set_anim_hook.callOriginal(.{ frame, point_enum, fixed_relativeTo, rel_point, x_ofs, y_ofs, param_6 });
}

/// Count how many nodes are in the PauseAnimationGroup dependency list.
fn countReverseDependencies(dying_frame: u32) u32 {
    if (IsBadReadPtr(@ptrFromInt(dying_frame + 0x34), 4) != 0) return 0;

    var node: u32 = readAligned(dying_frame + 0x34);
    if (node == 0 or (node & 1) != 0) return 0;

    var count: u32 = 0;
    while (node != 0 and (node & 1) == 0) {
        if (IsBadReadPtr(@ptrFromInt(node), 0x10) != 0) break;
        count += 1;
        node = readAligned(node + 0x04);
    }
    return count;
}

/// Walk the PauseAnimationGroup dependency list on the dying frame and NULL out
/// the relativeTo field in any anchors from other frames that reference it.
///
/// Safety: purely defensive - does NOT call destructors, free nodes, or modify
/// the dying frame's list pointers. The original cleanup_linked_list_structures
/// handles its own data structures.
fn cleanupReverseDependencies(dying_frame: u32) void {
    // Validate dying_frame+0x34 is readable before dereferencing
    if (IsBadReadPtr(@ptrFromInt(dying_frame + 0x34), 4) != 0) return;

    var node: u32 = readAligned(dying_frame + 0x34);

    // Validate: odd pointer or zero means empty list
    if (node == 0 or (node & 1) != 0) return;

    var cleaned: u32 = 0;

    while (node != 0 and (node & 1) == 0) {
        // Validate node is readable (need 0x10 bytes: link0, next, owner_frame, bitmask)
        if (IsBadReadPtr(@ptrFromInt(node), 0x10) != 0) break;

        const next: u32 = readAligned(node + 0x04);
        const owner_frame: u32 = readAligned(node + 0x08);
        const bitmask: u32 = readAligned(node + 0x0C);

        if (owner_frame != 0 and IsBadReadPtr(@ptrFromInt(owner_frame), 0x28) == 0) {
            var bit: u5 = 0;
            while (bit < 9) : (bit += 1) {
                if ((bitmask & (@as(u32, 1) << bit)) == 0) continue;

                const anchor_slot_addr = owner_frame + @as(u32, bit) * 4 + 4;
                const anchor: u32 = readAligned(anchor_slot_addr);
                if (anchor == 0) continue;

                // Validate anchor is readable (need vtable + xOfs + yOfs + relativeTo = 0x10)
                if (IsBadReadPtr(@ptrFromInt(anchor), 0x10) != 0) continue;

                // Verify this anchor actually references the dying frame
                const relativeTo: u32 = readAligned(anchor + 0x0C);
                if (relativeTo != dying_frame) continue;

                // Verify vtable matches the known anchor vtable - reject garbage objects
                const vtable: u32 = readAligned(anchor);
                if (vtable != ANCHOR_VTABLE_ADDR) continue;

                // NULL the relativeTo pointer so it can't dangle.
                // Don't call destructors or free the anchor - that risks cascading
                // side effects and is unnecessary. A NULL relativeTo is handled
                // gracefully by all code paths (luaGetPoint, GetWidth, GetHeight).
                const field: *align(1) u32 = @ptrFromInt(anchor + 0x0C);
                field.* = 0;

                cleaned += 1;
            }
        }

        node = next;
    }

    if (cleaned > 0) {
        log.fmt("Nulled {d} stale relativeTo ptr(s) referencing dying frame 0x{x:0>8}\n", .{ cleaned, dying_frame });
    }
}

fn readAligned(addr: u32) u32 {
    return @as(*align(1) const u32, @ptrFromInt(addr)).*;
}

/// Given a CLayoutFrame inner pointer, derive the CFrame base (subtract 0x24)
/// and read the name string at CFrame+0x98. Returns null if any pointer is
/// invalid or the name field is NULL.
fn getFrameName(layout_frame: u32) ?[*:0]const u8 {
    if (layout_frame < 0x24) return null;
    const frame_base = layout_frame -% 0x24;

    // Validate that frame_base+0x98 (name pointer field) is readable
    if (IsBadReadPtr(@ptrFromInt(frame_base + 0x98), 4) != 0) return null;

    const name_ptr = readAligned(frame_base + 0x98);
    if (name_ptr == 0) return null;

    // Validate the name string itself is readable (at least 1 byte)
    if (IsBadReadPtr(@ptrFromInt(name_ptr), 1) != 0) return null;

    return @ptrFromInt(name_ptr);
}

/// Format a frame name for logging - returns "FrameName" or "(unnamed)".
fn fmtFrameName(layout_frame: u32) []const u8 {
    if (getFrameName(layout_frame)) |name| {
        return std.mem.span(name);
    }
    return "(unnamed)";
}


fn bytesMatch(addr: usize, expected: []const u8) bool {
    if (expected.len == 0) return false;
    if (IsBadReadPtr(@ptrFromInt(addr), expected.len) != 0) return false;
    const got: [*]const u8 = @ptrFromInt(addr);
    return std.mem.eql(u8, got[0..expected.len], expected);
}

const ModRange = struct { base: usize, end: usize };

const SiteKind = enum { stock, e9_null, e9_superwow, e9_foreign, mismatch, unreadable };

const STOCK_VTABLE_0: u32 = 0x00767d80;
const STOCK_VTABLE_1: u32 = 0x007a2f90;
const STOCK_VTABLE_2: u32 = 0x007a3070;
const STOCK_VTABLE_3: u32 = 0x00767d70;

fn moduleRange(name: ?[*:0]const u8) ?ModRange {
    const base_ptr = GetModuleHandleA(name) orelse return null;
    const base = @intFromPtr(base_ptr);
    if (IsBadReadPtr(@ptrFromInt(base), 0x40) != 0) return null;
    const mz: [*]const u8 = @ptrFromInt(base);
    if (mz[0] != 'M' or mz[1] != 'Z') return null;
    const e_lfanew = std.mem.readInt(u32, mz[0x3C..0x40], .little);
    if (IsBadReadPtr(@ptrFromInt(base + e_lfanew + 24 + 56), 4) != 0) return null;
    const opt: [*]const u8 = @ptrFromInt(base + e_lfanew + 24);
    const size = std.mem.readInt(u32, opt[56..60], .little);
    if (size < 0x1000) return null;
    return .{ .base = base, .end = base + size };
}

fn containsAddr(range: ?ModRange, addr: usize) bool {
    const r = range orelse return false;
    return addr >= r.base and addr < r.end;
}

fn e9Dest(addr: usize) ?usize {
    if (IsBadReadPtr(@ptrFromInt(addr), 5) != 0) return null;
    const p: [*]const u8 = @ptrFromInt(addr);
    if (p[0] != 0xE9) return null;
    const rel = @as(*align(1) const i32, @ptrFromInt(addr + 1)).*;
    const dest_i: i64 = @as(i64, @intCast(addr)) + 5 + rel;
    if (dest_i < 0) return 0;
    return @intCast(dest_i);
}

fn classifySite(addr: usize, expected: []const u8, wow: ?ModRange, sw: ?ModRange) SiteKind {
    if (expected.len == 0) return .mismatch;
    if (IsBadReadPtr(@ptrFromInt(addr), expected.len) != 0) return .unreadable;
    if (bytesMatch(addr, expected)) return .stock;
    if (e9Dest(addr)) |dest| {
        if (dest < 0x10000) return .e9_null;
        if (containsAddr(sw, dest)) return .e9_superwow;
        // SuperWoW 2.2 trampolines often live outside SuperWoWhook.dll.
        // Any live E9 is a foreign hook — never steal it.
        _ = wow;
        return .e9_foreign;
    }
    return .mismatch;
}

fn kindName(k: SiteKind) []const u8 {
    return switch (k) {
        .stock => "stock",
        .e9_null => "e9-null",
        .e9_superwow => "e9-superwow",
        .e9_foreign => "e9-foreign",
        .mismatch => "mismatch",
        .unreadable => "unreadable",
    };
}

fn superWowMentions(sw: ?ModRange, va: u32) bool {
    const r = sw orelse return false;
    var p = r.base;
    while (p + 4 <= r.end) : (p += 1) {
        if (IsBadReadPtr(@ptrFromInt(p), 4) != 0) return false;
        if (readAligned(@intCast(p)) == va) return true;
    }
    return false;
}

fn vtableStillStock() bool {
    if (IsBadReadPtr(@ptrFromInt(ANCHOR_VTABLE_ADDR), 0x10) != 0) return false;
    const s0 = readAligned(@intCast(ANCHOR_VTABLE_ADDR));
    const s1 = readAligned(@intCast(ANCHOR_VTABLE_ADDR + 4));
    const s2 = readAligned(@intCast(ANCHOR_VTABLE_ADDR + 8));
    const s3 = readAligned(@intCast(ANCHOR_VTABLE_ADDR + 12));
    return s0 == STOCK_VTABLE_0 and s1 == STOCK_VTABLE_1 and s2 == STOCK_VTABLE_2 and s3 == STOCK_VTABLE_3;
}

fn writeAttachLogFmt(comptime fmt: []const u8, args: anytype) void {
    var buf: [320]u8 = undefined;
    const line = std.fmt.bufPrint(&buf, fmt, args) catch return;
    writeAttachLog(line);
}

fn logSite(label: []const u8, addr: usize, kind: SiteKind, mentioned: bool) void {
    writeAttachLogFmt("site {s} 0x{X:0>8} kind={s} sw_ref={d} b={X:0>2}{X:0>2}{X:0>2}{X:0>2}{X:0>2}{X:0>2}\n", .{
        label,
        addr,
        kindName(kind),
        @intFromBool(mentioned),
        siteByte(addr, 0),
        siteByte(addr, 1),
        siteByte(addr, 2),
        siteByte(addr, 3),
        siteByte(addr, 4),
        siteByte(addr, 5),
    });
}

fn attachIfStock(
    comptime Fn: type,
    h: *hook.Detour(Fn),
    addr: usize,
    detour: *const Fn,
    expected: []const u8,
    wow: ?ModRange,
    sw: ?ModRange,
    label: []const u8,
) bool {
    const kind = classifySite(addr, expected, wow, sw);
    const mentioned = superWowMentions(sw, @intCast(addr));
    logSite(label, addr, kind, mentioned);
    if (kind != .stock) {
        writeAttachLogFmt("SKIP {s}: not stock / SuperWoW-owned / unsafe trampoline\n", .{label});
        return false;
    }
    if (addr < 0x10000) {
        writeAttachLogFmt("SKIP {s}: NULL target\n", .{label});
        return false;
    }
    if (h.attach(addr, detour) != .ok) {
        writeAttachLogFmt("FAIL {s}: zhook attach rejected\n", .{label});
        return false;
    }
    writeAttachLogFmt("ATTACH {s}\n", .{label});
    return true;
}

// ReleaseSmall compiles out logging.Logger. This always-on file next to WoW.exe
// is the only attach breadcrumb for a 1.18 repro (ABORT vs hooks installed).
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
        "framecrash.log",
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

fn siteByte(addr: usize, index: usize) u8 {
    if (IsBadReadPtr(@ptrFromInt(addr + index), 1) != 0) return 0xFF;
    const p: [*]const u8 = @ptrFromInt(addr);
    return p[index];
}

fn cacheIfUIParent(layout_frame: u32) void {
    if (layout_frame == 0) return;
    if (!isRelativeToValid(layout_frame)) return;
    if (getFrameName(layout_frame)) |name| {
        if (std.mem.eql(u8, std.mem.span(name), "UIParent")) {
            g_cached_uiparent = layout_frame;
        }
    }
}

fn invalidateCachedUIParent(frame: u32) void {
    if (g_cached_uiparent == 0) return;
    if (frame == g_cached_uiparent or (frame +% 0x24) == g_cached_uiparent) {
        g_cached_uiparent = 0;
    }
}

fn getLiveUIParent() u32 {
    if (g_cached_uiparent == 0) return 0;
    if (!isRelativeToValid(g_cached_uiparent)) {
        g_cached_uiparent = 0;
        return 0;
    }
    if (getFrameName(g_cached_uiparent)) |name| {
        if (std.mem.eql(u8, std.mem.span(name), "UIParent")) return g_cached_uiparent;
    }
    g_cached_uiparent = 0;
    return 0;
}

/// Attempt to fix a stale relativeTo in an anchor by substituting cached UIParent.
/// Returns true if the fix was applied, false if no valid substitute found.
fn tryFixStaleRelativeTo(anchor: u32, stale: u32) bool {
    const live = getLiveUIParent();
    if (live == 0 or live == stale) return false;

    const field: *align(1) u32 = @ptrFromInt(anchor + 0x0C);
    field.* = live;
    log.fmt("HEALED: anchor 0x{x:0>8} relativeTo 0x{x:0>8} -> UIParent 0x{x:0>8}\n", .{
        anchor, stale, live,
    });
    return true;
}

// =============================================================================
// Defense-in-depth: anchor vtable hooks
//
// Three vtable slots must be hooked because they independently read anchor+0x0C
// (relativeTo) without going through each other:
//   [1] GetWidth  - reads [this+0xC]+0x3C, crashes if relativeTo is NULL/dangling
//   [2] GetHeight - same pattern as GetWidth
//   [3] GetRelativeTo - returns *(this+0x0C), caller dereferences it
//
// GetRelativeTo NULLs the pointer on detection (self-heal). GetWidth/GetHeight
// must independently handle both NULL and dangling relativeTo by returning the
// sentinel value from [0x00cf550c] - the value SetFrameHitTestMode compares
// against to detect "no dimension available".
// =============================================================================

const GET_WIDTH_SLOT: usize = ANCHOR_VTABLE_ADDR + 0x04; // vtable[1]
const GET_HEIGHT_SLOT: usize = ANCHOR_VTABLE_ADDR + 0x08; // vtable[2]

/// Sentinel float that SetFrameHitTestMode compares GetWidth/GetHeight results
/// against. Stored at runtime in .bss at 0x00cf550c.
const SENTINEL_ADDR: usize = 0x00cf550c;

var orig_get_width: usize = 0;
var orig_get_height: usize = 0;
var orig_get_relative_to: usize = 0;

/// Check if a relativeTo pointer (CLayoutFrame inner ptr) is valid.
/// Returns true if the pointer is non-NULL and the frame base region is readable.
fn isRelativeToValid(relativeTo: u32) bool {
    if (relativeTo == 0) return false;
    // relativeTo is an inner offset; callers subtract 0x24 for the frame base.
    // Validate that the frame base region (vtable + lua ref + index) is readable.
    return IsBadReadPtr(@ptrFromInt(relativeTo -% 0x24), 0x10) == 0;
}

/// Hook for vtable[3] GetRelativeTo. Validates the stored pointer.
/// If stale, NULLs anchor+0x0C and returns 0 (safe "no relativeTo" path).
fn getRelativeToHook(this: u32) callconv(hook.cc.thiscall) u32 {
    const orig: *const fn (u32) callconv(hook.cc.thiscall) u32 = @ptrFromInt(orig_get_relative_to);
    const result = orig(this);

    if (result == 0) return 0;

    if (!isRelativeToValid(result)) {
        // Try to substitute live UIParent before NULLing
        if (tryFixStaleRelativeTo(this, result)) {
            // Re-call original - it now reads the fixed pointer
            return orig(this);
        }
        // No substitute available - NULL it out
        const field: *align(1) u32 = @ptrFromInt(this + 0x0C);
        field.* = 0;
        return 0;
    }

    return result;
}

/// Hook for vtable[1] GetWidth. Checks anchor+0x0C before calling original.
/// Returns sentinel if relativeTo is NULL or dangling.
/// Signature: f32 __thiscall GetWidth(this, u32 param) - callee cleans 1 stack arg.
fn getWidthHook(this: u32, param: u32) callconv(hook.cc.thiscall) f32 {
    const relativeTo: u32 = readAligned(this + 0x0C);
    if (!isRelativeToValid(relativeTo)) {
        if (relativeTo != 0) {
            // Try to substitute live UIParent instead of NULLing
            if (tryFixStaleRelativeTo(this, relativeTo)) {
                // Fixed - call original with the healed pointer
                const orig: *const fn (u32, u32) callconv(hook.cc.thiscall) f32 = @ptrFromInt(orig_get_width);
                return orig(this, param);
            }
            const field: *align(1) u32 = @ptrFromInt(this + 0x0C);
            field.* = 0;
        }
        return @as(*align(1) const f32, @ptrFromInt(SENTINEL_ADDR)).*;
    }

    const orig: *const fn (u32, u32) callconv(hook.cc.thiscall) f32 = @ptrFromInt(orig_get_width);
    return orig(this, param);
}

/// Hook for vtable[2] GetHeight. Same pattern as GetWidth.
/// Signature: f32 __thiscall GetHeight(this, u32 param) - callee cleans 1 stack arg.
fn getHeightHook(this: u32, param: u32) callconv(hook.cc.thiscall) f32 {
    const relativeTo: u32 = readAligned(this + 0x0C);
    if (!isRelativeToValid(relativeTo)) {
        if (relativeTo != 0) {
            // Try to substitute live UIParent instead of NULLing
            if (tryFixStaleRelativeTo(this, relativeTo)) {
                const orig: *const fn (u32, u32) callconv(hook.cc.thiscall) f32 = @ptrFromInt(orig_get_height);
                return orig(this, param);
            }
            const field: *align(1) u32 = @ptrFromInt(this + 0x0C);
            field.* = 0;
        }
        return @as(*align(1) const f32, @ptrFromInt(SENTINEL_ADDR)).*;
    }

    const orig: *const fn (u32, u32) callconv(hook.cc.thiscall) f32 = @ptrFromInt(orig_get_height);
    return orig(this, param);
}

// =============================================================================
// Module API
// =============================================================================

fn patchVtableSlot(slot_addr: usize, new_fn: usize, save_to: *usize) void {
    save_to.* = hook.readMem(u32, slot_addr);
    const new_val: u32 = @intCast(new_fn);
    hook.writeProtected(slot_addr, std.mem.asBytes(&new_val));
}

fn restoreVtableSlot(slot_addr: usize, saved: *usize) void {
    if (saved.* != 0) {
        const orig: u32 = @intCast(saved.*);
        hook.writeProtected(slot_addr, std.mem.asBytes(&orig));
        saved.* = 0;
    }
}

fn tryAttach() void {
    if (g_stop or !g_is_hook_owner or g_attached) return;

    const wow = moduleRange(null);
    const sw = moduleRange("SuperWoWhook.dll");
    writeAttachLogFmt("framecrash tryAttach pid={d} wow={d} superwow={d}\n", .{
        GetCurrentProcessId(),
        @intFromBool(wow != null),
        @intFromBool(sw != null),
    });

    const cleanup_ok = attachIfStock(CleanupFn, &cleanup_hook, CLEANUP_TARGET, &cleanupDetour, &CLEANUP_PROLOGUE, wow, sw, "cleanup");
    const destroy_ok = attachIfStock(DestroyUIFn, &destroy_ui_hook, DESTROY_UI_TARGET, &destroyUIDetour, &DESTROY_UI_PROLOGUE, wow, sw, "destroyUI");
    const process_ok = attachIfStock(ProcessUIFn, &process_ui_hook, PROCESS_UI_TARGET, &processUIDetour, &PROCESS_UI_PROLOGUE, wow, sw, "processUI");
    const pause_ok = attachIfStock(PauseAnimFn, &pause_anim_hook, PAUSE_ANIM_TARGET, &pauseAnimDetour, &PAUSE_ANIM_PROLOGUE, wow, sw, "pauseAnim");
    const setanim_ok = attachIfStock(SetAnimFn, &set_anim_hook, SET_ANIM_TARGET, &setAnimOrderDetour, &SET_ANIM_PROLOGUE, wow, sw, "setAnim");

    var vtable_ok = false;
    if (vtableStillStock()) {
        patchVtableSlot(GET_WIDTH_SLOT, @intFromPtr(&getWidthHook), &orig_get_width);
        patchVtableSlot(GET_HEIGHT_SLOT, @intFromPtr(&getHeightHook), &orig_get_height);
        patchVtableSlot(GET_RELATIVE_TO_SLOT, @intFromPtr(&getRelativeToHook), &orig_get_relative_to);
        vtable_ok = orig_get_width != 0 and orig_get_height != 0 and orig_get_relative_to != 0;
        if (vtable_ok) {
            writeAttachLog("ATTACH vtable GetWidth/GetHeight/GetRelativeTo\n");
            log.print("Anchor vtable hooks installed (GetWidth/GetHeight/GetRelativeTo)\n");
        } else {
            writeAttachLog("FAIL vtable: saved originals were 0\n");
            restoreVtableSlot(GET_RELATIVE_TO_SLOT, &orig_get_relative_to);
            restoreVtableSlot(GET_HEIGHT_SLOT, &orig_get_height);
            restoreVtableSlot(GET_WIDTH_SLOT, &orig_get_width);
        }
    } else {
        writeAttachLog("SKIP vtable: slots are not stock 1.12.1 / SuperWoW-owned\n");
    }

    const any_fn = cleanup_ok or destroy_ok or process_ok or pause_ok or setanim_ok;
    g_attached = any_fn or vtable_ok;
    g_protection_active = g_attached;
    writeAttachLogFmt("RESULT cleanup={d} destroy={d} process={d} pause={d} setanim={d} vtable={d} active={d}\n", .{
        @intFromBool(cleanup_ok),
        @intFromBool(destroy_ok),
        @intFromBool(process_ok),
        @intFromBool(pause_ok),
        @intFromBool(setanim_ok),
        @intFromBool(vtable_ok),
        @intFromBool(g_protection_active),
    });
    if (!g_attached) {
        log.print("NO-OP: Crash Fix could not attach on this client (SuperWoW 2.2 or non-stock sites).\n");
        writeAttachLog("NO-OP: no safe Crash Fix sites; protection not attached\n");
    }
}

fn deferredAttachThread(_: ?*anyopaque) callconv(WINAPI) u32 {
    // SuperWoW 2.2 loads first (dlls.txt) but may finish engine hooks after DllMain.
    // Official attaches immediately, then SuperWoW can copy our E9 as "stock" bytes.
    Sleep(4000);
    if (!g_stop and g_is_hook_owner) tryAttach();
    return 0;
}

pub fn installHooks() void {
    const result = mod_mutex.acquire(module_name);
    g_mutex = result.handle;
    g_is_hook_owner = result.is_owner;
    if (!g_is_hook_owner) return;

    log = logging.Logger.open(module_name, .console);
    g_stop = false;
    g_attached = false;
    g_protection_active = false;

    const superwow_loaded = GetModuleHandleA("SuperWoWhook.dll") != null;
    writeAttachLogFmt("framecrash install pid={d} superwow={d}\n", .{
        GetCurrentProcessId(),
        @intFromBool(superwow_loaded),
    });

    if (superwow_loaded) {
        log.print("SuperWoW loaded; deferring Crash Fix attach 4s\n");
        writeAttachLog("DEFER: SuperWoW present; wait for runtime hooks before attaching\n");
        _ = CreateThread(null, 0, &deferredAttachThread, null, 0, null);
        return;
    }

    tryAttach();
}

pub fn removeHooks() void {
    g_stop = true;
    if (g_is_hook_owner) {
        if (g_attached) {
            set_anim_hook.detach();
            pause_anim_hook.detach();
            restoreVtableSlot(GET_RELATIVE_TO_SLOT, &orig_get_relative_to);
            restoreVtableSlot(GET_HEIGHT_SLOT, &orig_get_height);
            restoreVtableSlot(GET_WIDTH_SLOT, &orig_get_width);
            process_ui_hook.detach();
            destroy_ui_hook.detach();
            cleanup_hook.detach();
        }
        log.close();
        mod_mutex.release(&g_mutex);
    }
    g_is_hook_owner = false;
    g_protection_active = false;
    g_attached = false;
}

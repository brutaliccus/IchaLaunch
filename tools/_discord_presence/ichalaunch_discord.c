/*
 * ichalaunch_discord.dll — VanillaFixes in-process helper.
 *
 * Periodically reads this WoW process for player name, zone, level,
 * faction, class, guild, and race, then writes
 * %LOCALAPPDATA%\IchaLaunch\discord_wow_status.json for IchaLaunch.
 * This DLL must never talk to Discord, write coordinates, tokens, or
 * the game path.
 *
 * Broadcast filters (written by the launcher):
 *   %LOCALAPPDATA%\\IchaLaunch\\discord_broadcast_flags
 *   unsigned bitmask: name=1 guild=2 faction=4 class=8 level=16 zone=32.
 *   Missing file means all fields (63). Unchecked fields are omitted.
 *
 * File protocol:
 *   {
 *     "ts": <unix seconds>,
 *     "ok": true|false,
 *     "in_world": true|false,
 *     "name": "Thrall",
 *     "zone": "Orgrimmar",
 *     "level": 24,
 *     "faction": "horde",
 *     "class": "Shaman",
 *     "guild": "Frostwolf Clan",
 *     "race": "Orc",
 *     "build": 5875,
 *     "err": ""
 *   }
 *
 * Offset table is stock 1.12.1 build 5875 statics (image base 0x00400000).
 * RavenCraft's WoW.exe version resource is 1.12.1.5875; in-world addresses
 * are still unverified here and may fail on a patched client (ok:false).
 *
 * Crash policy (world-load race): never keep game pointers across polls.
 * Copy every value out immediately. Name/zone statics are sampled first.
 * Object-manager / descriptor / guild extras run only after name+zone look
 * valid for a few seconds; any failed hop omits that field (ok stays true).
 * Every game-memory read uses VirtualQuery + ReadProcessMemory. The whole
 * sample/write path is fenced so an AV cannot escape into WoW.
 */

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <excpt.h>
#include <setjmp.h>
#include <shlobj.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

/* Compact flags word written by IchaLaunch next to the status JSON.
 * name=1 guild=2 faction=4 class=8 level=16 zone=32. Missing file = all on.
 */
#define FLAG_NAME 1u
#define FLAG_GUILD 2u
#define FLAG_FACTION 4u
#define FLAG_CLASS 8u
#define FLAG_LEVEL 16u
#define FLAG_ZONE 32u
#define FLAG_ALL 63u

#define POLL_MS 2000
#define STARTUP_DELAY_MS 6000
#define OM_STABLE_MS 3000
#define NAME_MAX 24
#define ZONE_MAX 64
#define GUILD_MAX 48
#define JSON_MAX 768
#define IMAGE_BASE 0x00400000u
#define DBCACHE_BASE_VA 0x00C0E0C0u
#define DBCACHE_STRIDE 0x3Cu
#define DBCACHE_COUNT 12
#define FOURCC_WGLD 0x444C4757u
#define FOURCC_DLGW 0x57474C44u
#define USER_PTR_MIN 0x00010000u
#define USER_PTR_MAX 0x7FFEFFFFu
#define OM_MAX_HOPS 256
#define GUILD_MAX_HOPS 32
#define OBJ_HEADER_SPAN 0x40u

typedef struct Offsets {
    uint32_t build;
    uintptr_t name_va[4];
    uintptr_t zone_va[6];
    uintptr_t obj_mgr_va;
    uint32_t first_obj;
    uint32_t local_guid;
    uint32_t next_obj;
    uint32_t obj_type;
    uint32_t obj_guid;
    uint32_t descriptors;
    uint32_t unit_level;
    uint32_t unit_bytes0;
    uint32_t player_guildid;
    uint32_t player_info;
    uint32_t guild_key;
} Offsets;

/*
 * Community-documented 1.12.1 / 5875 statics. name_va / zone_va are tried
 * in order (direct C string, then pointer-to-string). The object-manager
 * walk (0xB41414) is best-effort and skipped until name+zone stay valid;
 * a failed hop never crashes the process. Class is UNIT_FIELD_BYTES_0
 * byte 1. Race is byte 0 (already used for faction). PLAYER_GUILDID is
 * descriptor + 0x2FC (field index 191); fallback CGPlayer+0xE68 + 0x0C.
 * Guild name comes from the GuildStats DBCache (WGLD) keyed by that id.
 */
static const Offsets kStock1121 = {
    5875,
    {0x00C27FC8u, 0x00C27D88u, 0x00C27FD8u, 0},
    {0x00B4B404u, 0x00B4B424u, 0x00CE06D0u, 0x00CE06F8u, 0x00B4B3C8u, 0},
    0x00B41414u,
    0xACu,
    0xC0u,
    0x3Cu,
    0x14u,
    0x30u,
    0x08u,
    0x88u,
    0x90u,
    0x2FCu,
    0xE68u,
    0x0Cu,
};

static HANDLE g_stop;
static HANDLE g_thread;
static PVOID g_veh;
static jmp_buf g_sample_jmp;
static volatile LONG g_in_sample;
static volatile DWORD g_sample_tid;
static DWORD g_world_stable_tick;

static uintptr_t module_base(void)
{
    return (uintptr_t)GetModuleHandleA(NULL);
}

static uintptr_t va_to_ptr(uintptr_t va)
{
    uintptr_t base = module_base();
    if (!base || va < IMAGE_BASE) {
        return 0;
    }
    return base + (va - IMAGE_BASE);
}

static int user_ptr(uintptr_t p)
{
    return p >= USER_PTR_MIN && p <= USER_PTR_MAX;
}

static int protect_readable(DWORD protect)
{
    if (protect & PAGE_GUARD) {
        return 0;
    }
    switch (protect & 0xFFu) {
    case PAGE_READONLY:
    case PAGE_READWRITE:
    case PAGE_WRITECOPY:
    case PAGE_EXECUTE_READ:
    case PAGE_EXECUTE_READWRITE:
    case PAGE_EXECUTE_WRITECOPY:
        return 1;
    default:
        return 0;
    }
}

static int region_readable(const MEMORY_BASIC_INFORMATION *mbi)
{
    if (!mbi || mbi->State != MEM_COMMIT) {
        return 0;
    }
    if (mbi->Protect & (PAGE_NOACCESS | PAGE_GUARD)) {
        return 0;
    }
    return protect_readable(mbi->Protect);
}

/* VirtualQuery every hop; reject execute-only / guard / free pages. */
static int readable(const void *p, size_t n)
{
    MEMORY_BASIC_INFORMATION first;
    MEMORY_BASIC_INFORMATION last_mbi;
    uintptr_t start;
    uintptr_t last;

    if (!p || n == 0 || n > 4096) {
        return 0;
    }
    start = (uintptr_t)p;
    last = start + n - 1;
    if (last < start || !user_ptr(start) || !user_ptr(last)) {
        return 0;
    }
    if (!VirtualQuery((const void *)start, &first, sizeof first) ||
        !region_readable(&first)) {
        return 0;
    }
    if (last >= (uintptr_t)first.BaseAddress + first.RegionSize) {
        if (!VirtualQuery((const void *)last, &last_mbi, sizeof last_mbi) ||
            !region_readable(&last_mbi)) {
            return 0;
        }
    }
    return 1;
}

/*
 * Never memcpy from game memory. RPM fails instead of AV if the page is
 * unmapped between VirtualQuery and the copy (world-load race).
 */
static int safe_read(uintptr_t addr, void *out, size_t n)
{
    SIZE_T got = 0;

    if (!out || n == 0) {
        return 0;
    }
    if (!readable((const void *)addr, n)) {
        return 0;
    }
    if (!ReadProcessMemory(GetCurrentProcess(), (LPCVOID)addr, out, n, &got)) {
        return 0;
    }
    return got == n;
}

static int read_u32(uintptr_t addr, uint32_t *out)
{
    uint32_t v = 0;

    if (!out || !safe_read(addr, &v, 4)) {
        return 0;
    }
    *out = v;
    return 1;
}

static int read_u64(uintptr_t addr, uint64_t *out)
{
    uint64_t v = 0;

    if (!out || !safe_read(addr, &v, 8)) {
        return 0;
    }
    *out = v;
    return 1;
}

static int copy_cstr(uintptr_t addr, char *out, size_t out_n, size_t max_len)
{
    char tmp[128];
    MEMORY_BASIC_INFORMATION mbi;
    uintptr_t region_end;
    size_t want;
    size_t span;
    SIZE_T got = 0;
    size_t i;

    if (!out || out_n == 0 || max_len == 0) {
        return 0;
    }
    out[0] = 0;
    if (!user_ptr(addr) || !readable((const void *)addr, 1)) {
        return 0;
    }
    if (!VirtualQuery((const void *)addr, &mbi, sizeof mbi) || !region_readable(&mbi)) {
        return 0;
    }
    region_end = (uintptr_t)mbi.BaseAddress + mbi.RegionSize;
    if (addr >= region_end) {
        return 0;
    }
    span = (size_t)(region_end - addr);
    want = max_len + 1;
    if (want > sizeof tmp) {
        want = sizeof tmp;
    }
    if (want > out_n) {
        want = out_n;
    }
    if (span < want) {
        want = span;
    }
    if (want == 0) {
        return 0;
    }
    memset(tmp, 0, sizeof tmp);
    if (!ReadProcessMemory(GetCurrentProcess(), (LPCVOID)addr, tmp, want, &got) ||
        got == 0) {
        return 0;
    }
    if (got < sizeof tmp) {
        tmp[got] = 0;
    } else {
        tmp[sizeof tmp - 1] = 0;
    }
    for (i = 0; i < got && i + 1 < out_n && i < max_len; i++) {
        if (tmp[i] == 0) {
            out[i] = 0;
            return i > 0;
        }
        out[i] = tmp[i];
    }
    return 0;
}

static int is_name(const char *s)
{
    size_t n;
    size_t i;

    if (!s) {
        return 0;
    }
    n = strlen(s);
    if (n < 2 || n > 16) {
        return 0;
    }
    if (!((s[0] >= 'A' && s[0] <= 'Z') || (s[0] >= 'a' && s[0] <= 'z'))) {
        return 0;
    }
    for (i = 1; i < n; i++) {
        char c = s[i];
        if (!((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || c == '\'')) {
            return 0;
        }
    }
    return 1;
}

static int is_zone(const char *s)
{
    size_t n;
    size_t i;

    if (!s) {
        return 0;
    }
    n = strlen(s);
    if (n < 2 || n > 64) {
        return 0;
    }
    if (!((s[0] >= 'A' && s[0] <= 'Z') || (s[0] >= 'a' && s[0] <= 'z') ||
          (s[0] >= '0' && s[0] <= '9'))) {
        return 0;
    }
    for (i = 1; i < n; i++) {
        char c = s[i];
        if ((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
            (c >= '0' && c <= '9') || c == ' ' || c == '\'' || c == '-' ||
            c == ':') {
            continue;
        }
        return 0;
    }
    return 1;
}

static int is_guild(const char *s)
{
    size_t n;
    size_t i;

    if (!s) {
        return 0;
    }
    n = strlen(s);
    if (n < 2 || n > GUILD_MAX) {
        return 0;
    }
    if (s[0] == 'N' && (strcmp(s, "None") == 0 || strcmp(s, "NONE") == 0)) {
        return 0;
    }
    if (!((s[0] >= 'A' && s[0] <= 'Z') || (s[0] >= 'a' && s[0] <= 'z') ||
          (s[0] >= '0' && s[0] <= '9'))) {
        return 0;
    }
    for (i = 1; i < n; i++) {
        char c = s[i];
        if ((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
            (c >= '0' && c <= '9') || c == ' ' || c == '\'' || c == '-' ||
            c == ':') {
            continue;
        }
        return 0;
    }
    return 1;
}

static int try_string_or_ptr(uintptr_t va, char *out, size_t out_n, size_t max_len,
                             int (*ok)(const char *))
{
    uintptr_t addr = va_to_ptr(va);
    uint32_t ptr = 0;

    if (!addr || !out) {
        return 0;
    }
    out[0] = 0;
    if (copy_cstr(addr, out, out_n, max_len) && ok(out)) {
        return 1;
    }
    out[0] = 0;
    if (read_u32(addr, &ptr) && user_ptr(ptr)) {
        if (copy_cstr((uintptr_t)ptr, out, out_n, max_len) && ok(out)) {
            return 1;
        }
    }
    out[0] = 0;
    return 0;
}

static uint32_t file_build(void)
{
    char path[MAX_PATH];
    DWORD handle = 0;
    DWORD size;
    UINT len = 0;
    VS_FIXEDFILEINFO *info = NULL;
    void *block;
    uint32_t build = 0;

    if (!GetModuleFileNameA(GetModuleHandleA(NULL), path, MAX_PATH)) {
        return 0;
    }
    size = GetFileVersionInfoSizeA(path, &handle);
    if (!size || size > 64 * 1024) {
        return 0;
    }
    block = HeapAlloc(GetProcessHeap(), 0, size);
    if (!block) {
        return 0;
    }
    if (GetFileVersionInfoA(path, 0, size, block) &&
        VerQueryValueA(block, "\\", (LPVOID *)&info, &len) && info) {
        build = LOWORD(info->dwFileVersionLS);
    }
    HeapFree(GetProcessHeap(), 0, block);
    return build;
}

static int local_guid(const Offsets *off, uint64_t *guid)
{
    uintptr_t mgr_slot = va_to_ptr(off->obj_mgr_va);
    uint32_t mgr = 0;

    if (!guid || !mgr_slot || !read_u32(mgr_slot, &mgr) || !user_ptr(mgr)) {
        return 0;
    }
    if (!readable((const void *)(uintptr_t)mgr, (size_t)off->local_guid + 8)) {
        return 0;
    }
    return read_u64((uintptr_t)mgr + off->local_guid, guid) && *guid != 0;
}

/* Two back-to-back reads; a loading-screen relocate will not match. */
static int local_guid_stable(const Offsets *off, uint64_t *guid)
{
    uint64_t a = 0;
    uint64_t b = 0;

    if (!local_guid(off, &a) || !local_guid(off, &b) || a != b || a == 0) {
        return 0;
    }
    *guid = a;
    return 1;
}

static const char *faction_for_race(uint32_t race)
{
    /* Stock 1.12.1: Alliance 1/3/4/7, Horde 2/5/6/8.
     * Turtle/RavenCraft extras: Goblin 9, Blood Elf 10, Draenei 11,
     * High Elf 16. Unknown → omit icon. */
    switch (race) {
    case 1:
    case 3:
    case 4:
    case 7:
    case 11:
    case 16:
        return "alliance";
    case 2:
    case 5:
    case 6:
    case 8:
    case 9:
    case 10:
        return "horde";
    default:
        return "";
    }
}

static const char *class_for_id(uint32_t class_id)
{
    switch (class_id) {
    case 1:
        return "Warrior";
    case 2:
        return "Paladin";
    case 3:
        return "Hunter";
    case 4:
        return "Rogue";
    case 5:
        return "Priest";
    case 7:
        return "Shaman";
    case 8:
        return "Mage";
    case 9:
        return "Warlock";
    case 11:
        return "Druid";
    default:
        return "";
    }
}

static const char *race_for_id(uint32_t race)
{
    switch (race) {
    case 1:
        return "Human";
    case 2:
        return "Orc";
    case 3:
        return "Dwarf";
    case 4:
        return "Night Elf";
    case 5:
        return "Undead";
    case 6:
        return "Tauren";
    case 7:
        return "Gnome";
    case 8:
        return "Troll";
    case 9:
        return "Goblin";
    case 10:
        return "Blood Elf";
    case 11:
        return "Draenei";
    case 16:
        return "High Elf";
    default:
        return "";
    }
}

static int filename_looks_guild(const char *s)
{
    if (!s || !s[0]) {
        return 0;
    }
    return strstr(s, "uild") != NULL || strstr(s, "UILD") != NULL ||
           strstr(s, "WGLD") != NULL || strstr(s, "wgld") != NULL;
}

static uintptr_t guild_cache_instance(void)
{
    int i;

    for (i = 0; i < DBCACHE_COUNT; i++) {
        uintptr_t inst = va_to_ptr(DBCACHE_BASE_VA + (uintptr_t)i * DBCACHE_STRIDE);
        uint32_t fourcc = 0;
        uint32_t fname_ptr = 0;
        char fname[32];

        if (!inst || !readable((const void *)inst, 0x30u)) {
            continue;
        }
        if (read_u32(inst + 0x28u, &fourcc) &&
            (fourcc == FOURCC_WGLD || fourcc == FOURCC_DLGW)) {
            return inst;
        }
        fname[0] = 0;
        if (read_u32(inst + 0x2Cu, &fname_ptr) && user_ptr(fname_ptr) &&
            copy_cstr((uintptr_t)fname_ptr, fname, sizeof fname, 24) &&
            filename_looks_guild(fname)) {
            return inst;
        }
    }
    return 0;
}

static int copy_guild_at(uintptr_t addr, char *out, size_t out_n)
{
    return copy_cstr(addr, out, out_n, GUILD_MAX) && is_guild(out);
}

static int walk_guild_chain(uint32_t start, uint32_t want, uint32_t link_off,
                            char *out, size_t out_n)
{
    uint32_t node = start;
    int hops = 0;

    if (!user_ptr(start) || !readable((const void *)(uintptr_t)start, 8)) {
        return 0;
    }
    while (node && user_ptr(node) && (node & 1u) == 0 && hops < GUILD_MAX_HOPS) {
        uint32_t nkey = 0;
        uint32_t next = 0;

        hops++;
        if (!readable((const void *)(uintptr_t)node, (size_t)link_off + 4) ||
            !read_u32((uintptr_t)node, &nkey)) {
            return 0;
        }
        if (nkey == want) {
            if (copy_guild_at((uintptr_t)node + 0x1Cu, out, out_n)) {
                return 1;
            }
            if (copy_guild_at((uintptr_t)node + 0x18u, out, out_n)) {
                return 1;
            }
            return 0;
        }
        if (!read_u32((uintptr_t)node + link_off, &next) || next == node ||
            !user_ptr(next)) {
            return 0;
        }
        node = next;
    }
    return 0;
}

static int lookup_guild_name(uint32_t key, char *out, size_t out_n)
{
    uintptr_t inst;
    uint32_t buckets = 0;
    uint32_t mask = 0;
    uint32_t head = 0;

    if (!key || !out || out_n < 3) {
        return 0;
    }
    out[0] = 0;
    inst = guild_cache_instance();
    if (!inst || !readable((const void *)inst, 0x28u)) {
        return 0;
    }
    if (!read_u32(inst + 0x1Cu, &buckets) || !user_ptr(buckets)) {
        return 0;
    }
    if (!read_u32(inst + 0x24u, &mask)) {
        return 0;
    }
    if (!read_u32((uintptr_t)buckets + (key & mask) * 12u + 8u, &head) ||
        !user_ptr(head)) {
        return 0;
    }
    if (walk_guild_chain(head, key, 4u, out, out_n)) {
        return 1;
    }
    return walk_guild_chain(head, key, 8u, out, out_n);
}

static int read_player_guild(const Offsets *off, uintptr_t obj, uint32_t desc,
                             char *out, size_t out_n)
{
    uint32_t guild_id = 0;
    uint32_t info = 0;

    if (!out || out_n == 0) {
        return 0;
    }
    out[0] = 0;
    if (off->player_guildid && desc && user_ptr(desc) &&
        readable((const void *)((uintptr_t)desc + off->player_guildid), 4) &&
        read_u32((uintptr_t)desc + off->player_guildid, &guild_id) && guild_id) {
        if (lookup_guild_name(guild_id, out, out_n)) {
            return 1;
        }
        out[0] = 0;
    }
    if (off->player_info && obj && user_ptr(obj) &&
        read_u32(obj + off->player_info, &info) && user_ptr(info) && off->guild_key &&
        readable((const void *)((uintptr_t)info + off->guild_key), 4) &&
        read_u32((uintptr_t)info + off->guild_key, &guild_id) && guild_id) {
        return lookup_guild_name(guild_id, out, out_n);
    }
    return 0;
}

static int player_unit_info(const Offsets *off, uint64_t want, uint32_t *level,
                            uint32_t *race, uint32_t *class_id, char *guild,
                            size_t guild_n)
{
    uintptr_t mgr_slot = va_to_ptr(off->obj_mgr_va);
    uint32_t mgr = 0;
    uint32_t cur = 0;
    uint32_t first = 0;
    int hops = 0;

    if (!mgr_slot || !read_u32(mgr_slot, &mgr) || !user_ptr(mgr)) {
        return 0;
    }
    if (!readable((const void *)(uintptr_t)mgr, (size_t)off->first_obj + 4)) {
        return 0;
    }
    if (!read_u32((uintptr_t)mgr + off->first_obj, &cur) || !user_ptr(cur)) {
        return 0;
    }
    first = cur;
    while (cur && user_ptr(cur) && (cur & 1u) == 0 && hops < OM_MAX_HOPS) {
        uint32_t type = 0;
        uint64_t guid = 0;
        uint32_t desc = 0;
        uint32_t next = 0;
        uint32_t still_first = 0;

        hops++;
        /* List relocated mid-walk (loading screen): abort extras. */
        if (!read_u32((uintptr_t)mgr + off->first_obj, &still_first) ||
            still_first != first) {
            return 0;
        }
        if (!readable((const void *)(uintptr_t)cur, OBJ_HEADER_SPAN)) {
            return 0;
        }
        if (!read_u32((uintptr_t)cur + off->obj_type, &type)) {
            return 0;
        }
        if (type == 4 && read_u64((uintptr_t)cur + off->obj_guid, &guid) &&
            guid == want &&
            read_u32((uintptr_t)cur + off->descriptors, &desc) && user_ptr(desc)) {
            uint32_t lv = 0;
            uint32_t bytes0 = 0;
            if (readable((const void *)((uintptr_t)desc + off->unit_level), 4) &&
                read_u32((uintptr_t)desc + off->unit_level, &lv) && lv >= 1 &&
                lv <= 80) {
                *level = lv;
            }
            if (off->unit_bytes0 &&
                readable((const void *)((uintptr_t)desc + off->unit_bytes0), 4) &&
                read_u32((uintptr_t)desc + off->unit_bytes0, &bytes0)) {
                *race = bytes0 & 0xFFu;
                if (class_id) {
                    *class_id = (bytes0 >> 8) & 0xFFu;
                }
            }
            if (guild && guild_n) {
                guild[0] = 0;
                read_player_guild(off, (uintptr_t)cur, desc, guild, guild_n);
            }
            return 1;
        }
        if (!read_u32((uintptr_t)cur + off->next_obj, &next) || next == cur ||
            !user_ptr(next)) {
            return 0;
        }
        cur = next;
    }
    return 0;
}

static void json_escape(const char *in, char *out, size_t out_n)
{
    size_t w = 0;
    size_t i;

    if (!out || out_n == 0) {
        return;
    }
    out[0] = 0;
    if (!in) {
        return;
    }
    for (i = 0; in[i] && w + 2 < out_n; i++) {
        unsigned char c = (unsigned char)in[i];
        if (c == '"' || c == '\\') {
            if (w + 3 >= out_n) {
                break;
            }
            out[w++] = '\\';
            out[w++] = (char)c;
        } else if (c >= 32 && c < 127) {
            out[w++] = (char)c;
        }
    }
    out[w] = 0;
}

static int appdata_file(char *out, size_t out_n, const char *name)
{
    char dir[MAX_PATH];

    if (!out || !name || out_n < 32) {
        return 0;
    }
    if (FAILED(SHGetFolderPathA(NULL, CSIDL_LOCAL_APPDATA, NULL, 0, dir))) {
        return 0;
    }
    if (strlen(dir) + 64 >= out_n) {
        return 0;
    }
    lstrcpynA(out, dir, (int)out_n);
    lstrcatA(out, "\\IchaLaunch");
    CreateDirectoryA(out, NULL);
    lstrcatA(out, "\\");
    lstrcatA(out, name);
    return 1;
}

static int status_path(char *out, size_t out_n)
{
    return appdata_file(out, out_n, "discord_wow_status.json");
}

static unsigned read_broadcast_flags(void)
{
    char dest[MAX_PATH];
    char buf[32];
    HANDLE hf;
    DWORD got = 0;
    unsigned flags;
    char *end = NULL;

    if (!appdata_file(dest, sizeof dest, "discord_broadcast_flags")) {
        return FLAG_ALL;
    }
    hf = CreateFileA(dest, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE,
                     NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (hf == INVALID_HANDLE_VALUE) {
        return FLAG_ALL;
    }
    memset(buf, 0, sizeof buf);
    if (!ReadFile(hf, buf, sizeof buf - 1, &got, NULL) || got == 0) {
        CloseHandle(hf);
        return FLAG_ALL;
    }
    CloseHandle(hf);
    flags = (unsigned)strtoul(buf, &end, 10);
    if (end == buf) {
        return FLAG_ALL;
    }
    return flags & FLAG_ALL;
}

static int write_json(const char *json)
{
    char dest[MAX_PATH];
    char tmp[MAX_PATH];
    HANDLE hf;
    DWORD written = 0;
    DWORD n;

    if (!json || !status_path(dest, sizeof dest)) {
        return 0;
    }
    lstrcpynA(tmp, dest, MAX_PATH);
    lstrcatA(tmp, ".tmp");
    hf = CreateFileA(tmp, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS,
                     FILE_ATTRIBUTE_NORMAL, NULL);
    if (hf == INVALID_HANDLE_VALUE) {
        return 0;
    }
    n = (DWORD)strlen(json);
    if (!WriteFile(hf, json, n, &written, NULL) || written != n) {
        CloseHandle(hf);
        DeleteFileA(tmp);
        return 0;
    }
    FlushFileBuffers(hf);
    CloseHandle(hf);
    if (!MoveFileExA(tmp, dest, MOVEFILE_REPLACE_EXISTING)) {
        DeleteFileA(tmp);
        return 0;
    }
    return 1;
}

static void write_fault_json(void)
{
    char json[256];

    _snprintf(
        json,
        sizeof json,
        "{\"v\":1,\"ts\":%ld,\"ok\":false,\"in_world\":false,\"name\":\"\","
        "\"zone\":\"\",\"level\":0,\"faction\":\"\",\"class\":\"\","
        "\"guild\":\"\",\"race\":\"\",\"build\":5875,\"err\":\"fault\"}",
        (long)time(NULL));
    json[sizeof json - 1] = 0;
    write_json(json);
}

static void publish_sample(const Offsets *off)
{
    char name[NAME_MAX + 1];
    char zone[ZONE_MAX + 1];
    char guild[GUILD_MAX + 1];
    char name_esc[NAME_MAX * 2 + 8];
    char zone_esc[ZONE_MAX * 2 + 8];
    char guild_esc[GUILD_MAX * 2 + 8];
    char err[32];
    char json[JSON_MAX];
    uint64_t guid = 0;
    uint32_t level = 0;
    uint32_t race = 0;
    uint32_t class_id = 0;
    uint32_t build;
    const char *faction = "";
    const char *class_name = "";
    const char *race_name = "";
    int have_name = 0;
    int have_zone = 0;
    int have_level = 0;
    int in_world = 0;
    int ok = 0;
    int can_walk_om = 0;
    int i;
    unsigned flags;
    DWORD now_tick;
    time_t ts = time(NULL);

    name[0] = 0;
    zone[0] = 0;
    guild[0] = 0;
    err[0] = 0;
    build = file_build();
    if (!build) {
        build = off->build;
    }

    for (i = 0; off->name_va[i]; i++) {
        if (try_string_or_ptr(off->name_va[i], name, sizeof name, NAME_MAX, is_name)) {
            have_name = 1;
            break;
        }
    }
    for (i = 0; off->zone_va[i]; i++) {
        if (try_string_or_ptr(off->zone_va[i], zone, sizeof zone, ZONE_MAX, is_zone)) {
            have_zone = 1;
            break;
        }
    }

    now_tick = GetTickCount();
    if (have_name && have_zone) {
        if (g_world_stable_tick == 0) {
            g_world_stable_tick = now_tick ? now_tick : 1;
        }
        in_world = 1;
        ok = 1;
        can_walk_om = (now_tick - g_world_stable_tick) >= OM_STABLE_MS;
    } else {
        g_world_stable_tick = 0;
        if (!have_name && !have_zone) {
            lstrcpynA(err, "offsets", sizeof err);
        } else {
            lstrcpynA(err, "not_in_world", sizeof err);
        }
    }

    /* OM / guild / class are optional. Name+zone JSON must still publish. */
    if (ok && can_walk_om && local_guid_stable(off, &guid)) {
        if (player_unit_info(off, guid, &level, &race, &class_id, guild,
                             sizeof guild)) {
            have_level = level >= 1 && level <= 80;
        }
    }

    if (ok) {
        faction = faction_for_race(race);
        class_name = class_for_id(class_id);
        race_name = race_for_id(race);
        if (!is_guild(guild)) {
            guild[0] = 0;
        }
    } else {
        guild[0] = 0;
        name[0] = 0;
        zone[0] = 0;
    }

    flags = read_broadcast_flags();
    json_escape((ok && (flags & FLAG_NAME)) ? name : "", name_esc, sizeof name_esc);
    json_escape((ok && (flags & FLAG_ZONE)) ? zone : "", zone_esc, sizeof zone_esc);
    json_escape((ok && (flags & FLAG_GUILD)) ? guild : "", guild_esc, sizeof guild_esc);
    if (!(flags & FLAG_FACTION)) {
        faction = "";
    }
    if (!(flags & FLAG_CLASS) || !ok) {
        class_name = "";
    }
    if (!(flags & FLAG_LEVEL) || !ok) {
        have_level = 0;
        level = 0;
    }
    _snprintf(
        json,
        sizeof json,
        "{\"v\":1,\"ts\":%ld,\"ok\":%s,\"in_world\":%s,\"name\":\"%s\","
        "\"zone\":\"%s\",\"level\":%u,\"faction\":\"%s\",\"class\":\"%s\","
        "\"guild\":\"%s\",\"race\":\"%s\",\"build\":%u,\"err\":\"%s\"}",
        (long)ts,
        ok ? "true" : "false",
        (ok && in_world) ? "true" : "false",
        name_esc,
        zone_esc,
        ok && have_level ? level : 0,
        ok ? faction : "",
        ok ? class_name : "",
        guild_esc,
        ok ? race_name : "",
        build,
        err);
    json[sizeof json - 1] = 0;
    write_json(json);
}

#if defined(_MSC_VER)
#define ICH_SAMPLE_SEH 1
#else
#define ICH_SAMPLE_SEH 0
#endif

static LONG CALLBACK sample_veh(EXCEPTION_POINTERS *ep)
{
    DWORD code;

    if (!g_in_sample || !ep || !ep->ExceptionRecord) {
        return EXCEPTION_CONTINUE_SEARCH;
    }
    if (GetCurrentThreadId() != g_sample_tid) {
        return EXCEPTION_CONTINUE_SEARCH;
    }
    code = ep->ExceptionRecord->ExceptionCode;
    if (code == EXCEPTION_ACCESS_VIOLATION || code == EXCEPTION_IN_PAGE_ERROR ||
        code == EXCEPTION_DATATYPE_MISALIGNMENT) {
        longjmp(g_sample_jmp, 1);
    }
    return EXCEPTION_CONTINUE_SEARCH;
}

static void publish(const Offsets *off)
{
    g_sample_tid = GetCurrentThreadId();
#if ICH_SAMPLE_SEH
    __try {
        publish_sample(off);
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        write_fault_json();
    }
#else
    if (setjmp(g_sample_jmp) != 0) {
        g_in_sample = 0;
        write_fault_json();
        return;
    }
    g_in_sample = 1;
    publish_sample(off);
    g_in_sample = 0;
#endif
}

static DWORD WINAPI worker(LPVOID unused)
{
    (void)unused;
    /* Do not sample during process/startup or the first loading screen. */
    if (WaitForSingleObject(g_stop, STARTUP_DELAY_MS) != WAIT_TIMEOUT) {
        return 0;
    }
    do {
        publish(&kStock1121);
    } while (WaitForSingleObject(g_stop, POLL_MS) == WAIT_TIMEOUT);
    return 0;
}

BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID reserved)
{
    (void)instance;
    (void)reserved;
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(instance);
        g_stop = CreateEventA(NULL, TRUE, FALSE, NULL);
        if (!g_stop) {
            return TRUE;
        }
#if !ICH_SAMPLE_SEH
        g_veh = AddVectoredExceptionHandler(1, sample_veh);
#endif
        g_thread = CreateThread(NULL, 0, worker, NULL, 0, NULL);
    } else if (reason == DLL_PROCESS_DETACH) {
        if (g_stop) {
            SetEvent(g_stop);
        }
        if (g_thread) {
            WaitForSingleObject(g_thread, 1500);
            CloseHandle(g_thread);
            g_thread = NULL;
        }
        if (g_veh) {
            RemoveVectoredExceptionHandler(g_veh);
            g_veh = NULL;
        }
        if (g_stop) {
            CloseHandle(g_stop);
            g_stop = NULL;
        }
    }
    return TRUE;
}

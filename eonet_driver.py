#!/usr/bin/env python3
"""
The Eonet Driver — Message Interceptor + HTTPS Proxy

Two modes, one resolve engine:

  MCP stream interceptor (_run_proxy / _EonetInterceptStream):
    Wraps server.py's stdio stream. Every user message passes through
    eonet_resolve before the MCP server sees it. If a ROM subject is
    recognized, a routing header is prepended pointing Claude at the
    exact NARC file(s). Unrecognized messages pass through unchanged.

  HTTPS proxy (_run_http_eonet_proxy):
    Binds localhost:443 with a self-signed cert. The hosts file redirects
    claude.ai → 127.0.0.1, so Desktop's requests hit this proxy instead.
    Completion requests get the same sliver injection before forwarding.
    All other traffic passes through unmodified.

eonet_resolve: string matching against in-memory text tables.
No embeddings, no NLP. Sub-100ms. Both modes share it directly.

Standalone test:
    python eonet_driver.py --test "What are Bulbasaur's Pokéathlon stats?"
"""

import asyncio
import json
import struct
import sys
import os
from pathlib import Path
from typing import Optional


class EonetDriver:
    """Client-side driver for API consumers (not the Desktop proxy path).

    Connects to an MCP session, checks eonet capability, and on each user
    message calls eonet/resolve. If resolved, prepends the routing sliver
    before sending to the Claude API.
    """

    def __init__(self, session):
        self.session = session
        self.eonet_supported = False
        self.turn_index = 0

        # Multiple ROM tracking: game_code -> abs_path
        # The game hardcodes its own identity. We track which are open.
        self._active_roms = {}        # game_code -> rom_path
        self._roms_in_context = set() # game_codes whose spotlight path Claude has already seen

    async def check_capability(self):
        """Check if server supports eonet. Call after connecting."""
        try:
            init = self.session.server_info
            if hasattr(init, 'capabilities'):
                caps = init.capabilities
                if hasattr(caps, 'experimental') and caps.experimental:
                    if 'eonet' in caps.experimental:
                        self.eonet_supported = True
                        return True
        except:
            pass

        # Probe: send a sentinel that won't resolve. Any dict response = method exists.
        try:
            result = await self.session.send_request(
                "eonet/resolve",
                {"message": "__eonet_probe__", "game_code": None}
            )
            if isinstance(result, dict):
                self.eonet_supported = True
                return True
        except:
            pass

        return False

    def set_active_rom(self, game_code: str, rom_path: str):
        """Called after spotlight succeeds. Registers ROM as active."""
        self._active_roms[game_code] = rom_path
        # New ROM load — spotlight path not yet in Claude's context for this ROM
        self._roms_in_context.discard(game_code)

    def clear_active_rom(self, game_code: str = None):
        """Called after return/close. Removes one ROM or all."""
        if game_code:
            self._active_roms.pop(game_code, None)
            self._roms_in_context.discard(game_code)
        else:
            self._active_roms.clear()
            self._roms_in_context.clear()

    @property
    def has_active_rom(self) -> bool:
        return bool(self._active_roms)

    async def resolve(self, message: str, game_code: str = None) -> dict:
        """Call eonet/resolve on the server."""
        if not self.eonet_supported or not self._active_roms:
            return {"resolved": False, "reason": "eonet not active"}

        target_gc = game_code or next(iter(self._active_roms))

        try:
            result = await self.session.send_request(
                "eonet/resolve",
                {
                    "message": message,
                    "game_code": target_gc,
                    "turn_index": self.turn_index,
                }
            )
            return result if isinstance(result, dict) else {"resolved": False}
        except Exception as e:
            return {"resolved": False, "reason": str(e)}

    def _extract_sliver_inner(self, sliver: str) -> str:
        """Extract the inner content from a routing sliver string.

        '[routing: decipher: a/1/6/9:000 - Bulbasaur (Pokéathlon)]'
        → 'decipher: a/1/6/9:000 - Bulbasaur (Pokéathlon)'

        No lstrip. Prefix check only.
        """
        if sliver.startswith('[routing: ') and sliver.endswith(']'):
            return sliver[len('[routing: '):-1]
        return sliver

    def _build_header(self, resolved_list: list) -> str:
        """Build the routing header Claude will see.

        New per-ROM block format:
          [rom: HeartGold (IPK)]
            spotlight: [C:/roms/HeartGold.nds]   <- only if not yet in context
            decipher: [path - label, ...]

        resolved_list: [(gc, sliver_str, rom_path_or_None), ...]
        rom_path_or_None is None when ROM is already in Claude's context.
        """
        from itertools import groupby
        blocks = []
        game_names = {
            'ADA': 'Diamond', 'APA': 'Pearl', 'CPU': 'Platinum',
            'IPK': 'HeartGold', 'IPG': 'SoulSilver',
            'IRB': 'Black', 'IRA': 'White',
            'IRE': 'Black 2', 'IRD': 'White 2',
        }
        for gc, inner, rom_path in resolved_list:
            name = game_names.get(gc, gc)
            lines = [f"[rom: {name} ({gc})]"]
            if rom_path and gc not in self._roms_in_context:
                lines.append(f"  spotlight: [{rom_path}]")
                self._roms_in_context.add(gc)
            lines.append(f"  decipher: [{inner}]")
            blocks.append('\n'.join(lines))
        return '\n'.join(blocks)

    async def process_message(self, user_message: str) -> str:
        """Resolve user message and prepend routing sliver if matched.

        Returns the original message unchanged if no ROM subject is found.
        """
        self.turn_index += 1

        if not self.has_active_rom or not self.eonet_supported:
            return user_message

        # Detect which ROMs are relevant from message content
        # If user says "Diamond" and Diamond is loaded, target that ROM first
        msg_lower = user_message.lower()
        target_gcs = []
        # Longer hints first so "black 2" matches before "black"
        for hint in sorted(_GAME_HINTS.keys(), key=lambda x: -len(x)):
            gc = _GAME_HINTS[hint]
            if hint in msg_lower and gc in self._active_roms and gc not in target_gcs:
                target_gcs.append(gc)

        if not target_gcs:
            target_gcs = list(self._active_roms.keys())

        # Resolve against each relevant ROM
        resolved = []
        for gc in target_gcs:
            result = await self.resolve(user_message, game_code=gc)
            if result.get("resolved"):
                resolved.append((gc, result))

        if not resolved:
            # Eonet steps aside. Claude gets the original. Works normally.
            return user_message

        # Build per-ROM block list for _build_header
        # Each entry: (gc, decipher_inner, rom_path_or_None)
        # rom_path included only when ROM not yet in Claude's context
        resolved_list = []
        for gc, result in resolved:
            inner = self._extract_sliver_inner(result["sliver"])
            rom_path = self._active_roms.get(gc) if gc not in self._roms_in_context else None
            resolved_list.append((gc, inner, rom_path))

        header = self._build_header(resolved_list)

        # Routing header tells Claude WHERE (which NARC files).
        # [user:] is WHAT they asked. Claude sees both; UI shows only the original.
        return f"{header}\n[user: {user_message}]"

    async def process_tool_result(self, tool_name: str, result: dict):
        """Track state changes from server tool calls.

        Call this after Claude's tool calls complete so the driver
        stays in sync with server state.
        """
        if tool_name == "spotlight":
            # spotlight returns game_code and the ROM path
            # Handle both 'rom_path' and 'path' key variants
            gc = result.get("game_code")
            path = result.get("rom_path") or result.get("path") or result.get("rom")
            if gc:
                if path:
                    self.set_active_rom(gc, path)
                else:
                    # Game code known but path not in result — register with empty path
                    # spotlight will have returned the path elsewhere; best effort
                    if gc not in self._active_roms:
                        self._active_roms[gc] = ""
                        self._roms_in_context.discard(gc)
            if not self.eonet_supported:
                await self.check_capability()

        elif tool_name == "return":
            # return closes a ROM (or switches to another)
            closed_gc = result.get("closed_game_code") or result.get("game_code")
            if closed_gc:
                self.clear_active_rom(closed_gc)
            elif not result.get("loaded") and not result.get("active"):
                # All ROMs closed
                self.clear_active_rom()


class EonetMiddleware:
    """Drop-in middleware for Claude API calls.

    Wraps the Anthropic client to automatically intercept user messages,
    resolve via Eonet, and send rewritten messages to Claude.

    Usage:
        import anthropic
        from eonet_driver import EonetDriver, EonetMiddleware

        client = anthropic.Anthropic()
        driver = EonetDriver(mcp_session)
        await driver.check_capability()
        middleware = EonetMiddleware(client, driver)

        response = await middleware.create_message(
            model="claude-sonnet-4-20250514",
            messages=[{"role": "user", "content": "What are Bulbasaur's stats?"}],
            tools=[...],
            max_tokens=4096,
        )
        # original_messages preserves what was actually typed, for UI display.
    """

    def __init__(self, anthropic_client, driver: EonetDriver):
        self.client = anthropic_client
        self.driver = driver
        # Preserves original text for UI display (the API receives the rewritten version).
        self.original_messages = []

    async def create_message(self, messages: list, **kwargs):
        """Intercept last user message, resolve, rewrite, send to Claude."""
        if not messages:
            return self.client.messages.create(messages=messages, **kwargs)

        last_idx = None
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "user":
                last_idx = i
                break

        if last_idx is None:
            return self.client.messages.create(messages=messages, **kwargs)

        original_content = messages[last_idx]["content"]

        if isinstance(original_content, str):
            user_text = original_content
        elif isinstance(original_content, list):
            user_text = " ".join(
                b.get("text", "") for b in original_content
                if isinstance(b, dict) and b.get("type") == "text"
            )
        else:
            user_text = str(original_content)

        # Store original BEFORE processing (turn_index increments inside process_message)
        next_turn = self.driver.turn_index + 1
        self.original_messages.append({"turn": next_turn, "original": user_text})

        rewritten = await self.driver.process_message(user_text)

        rewritten_messages = list(messages)
        rewritten_messages[last_idx] = {"role": "user", "content": rewritten}

        return self.client.messages.create(messages=rewritten_messages, **kwargs)

    def get_original_message(self, turn: int) -> Optional[str]:
        """Get original user message for a turn (for UI display)."""
        for m in self.original_messages:
            if m["turn"] == turn:
                return m["original"]
        return None


import re

# ============================================================
# Eonet ICR Engine — Iterative Cross-Referencing
# ============================================================
# Auto-discovery engine. Moved from server.py.
# server.py imports _build_eonet and eonet_resolve from here.
# Server state accessed via _srv() to avoid circular imports.

# Entity metadata: which games each entity appears in
# Built during ICR, used for smart routing
_entity_metadata = {}  # {entity_name_lower: {game_codes: set, contexts: {gc: [context_labels]}}}

# Generation boundaries for smart fallback
_GEN_BOUNDARIES = {
    4: {'ADA', 'APA', 'CPU', 'IPK', 'IPG'},
    5: {'IRB', 'IRA', 'IRD', 'IRE'},
}

def _get_entity_generation(game_codes: set) -> int:
    """Determine which generation(s) an entity appears in."""
    for gen, codes in _GEN_BOUNDARIES.items():
        if game_codes & codes:
            return gen
    return 5  # default to latest


def _srv():
    """Lazy import of server module. Avoids circular import at module load time."""
    # server.py is the entry point — Python registers it as __main__, not 'server'.
    # Check __main__ first, because that's where all the live state lives.
    if '__main__' in sys.modules:
        main = sys.modules['__main__']
        if hasattr(main, 'eonet_labels'):
            return main

    # Fallback: maybe it was imported as 'server' by something else
    if 'server' in sys.modules:
        srv = sys.modules['server']
        if hasattr(srv, 'eonet_labels'):
            return srv

    # Last resort: import from scripts directory explicitly
    scripts_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'scripts')
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    import importlib
    import server
    if not hasattr(server, 'eonet_labels'):
        import importlib.util
        server_path = os.path.join(scripts_dir, 'server.py')
        spec = importlib.util.spec_from_file_location("server", server_path)
        server = importlib.util.module_from_spec(spec)
        sys.modules['server'] = server
        spec.loader.exec_module(server)

    return server


async def _restore_roms_from_registry():
    """Load ROMs from last_rom.json. Returns count of ROMs loaded."""
    srv = _srv()
    initial_count = len(srv.loaded_roms)
    await srv._do_pending_restore()
    return len(srv.loaded_roms) - initial_count


class _GarcWrap:
    """Wraps a list of byte arrays to look like an ndspy NARC for ICR."""
    def __init__(self, file_list):
        self.files = file_list


def _walk_all_narcs():
    """Yield (narc_path, ndspy.narc.NARC) for every NARC in the ROM filesystem."""
    import ndspy.narc
    srv = _srv()
    if not srv.current_rom or srv.current_rom['type'] != 'nds':
        return
    rom = srv.current_rom['rom']

    def _walk(folder, prefix=""):
        for filename in folder.files:
            full = f"{prefix}/{filename}" if prefix else filename
            try:
                fid = folder.idOf(filename)
                data = rom.files[fid]
                if len(data) >= 4 and data[:4] == b'NARC':
                    yield full, ndspy.narc.NARC(data)
            except:
                pass
        for name, sub in folder.folders:
            fp = f"{prefix}/{name}" if prefix else name
            yield from _walk(sub, fp)

    if rom.filenames:
        yield from _walk(rom.filenames)


def _walk_all_garcs():
    """Yield (garc_path, _GarcWrap) for every GARC in the 3DS RomFS.

    Reads each GARC on demand from the file handle — never loads all into RAM.
    """
    srv = _srv()
    if not srv.current_rom or srv.current_rom['type'] != '3ds':
        return
    fh = srv.current_rom['romfs_fh']
    fs = srv.current_rom['romfs_files']
    from xoleon import read_garc_all

    for gpath in sorted(fs.keys()):
        abs_off = fs[gpath][0]
        try:
            file_list = read_garc_all(fh, abs_off)
            if len(file_list) >= 2:
                yield gpath, _GarcWrap(file_list)
        except Exception:
            pass


def _icr_get_tables():
    """Return all decoded text tables. The seed for everything that follows."""
    srv = _srv()
    return {n: t for n, t in srv.text_tables.items()
            if isinstance(t, list) and len(t) > 2}


def _icr_build_val_lookup(tables):
    """Pre-build value → set of table names for O(1) lookups during NARC scanning.

    Without this, each u16 value gets checked against every table (O(tables)).
    With this, it's a single dict lookup (O(1)). ~600x faster for full table sets.
    """
    lookup = {}
    for tname, tbl in tables.items():
        for val in range(len(tbl)):
            entry = tbl[val]
            if isinstance(entry, str) and len(entry.strip()) >= 3:
                if val not in lookup:
                    lookup[val] = set()
                lookup[val].add(tname)
    return lookup


def _icr_check_file(data, tables):
    """Check one file's bytes against text tables. Not guessing — checking.

    u16 LE at every even offset: is it a valid index into a text table?
    u8 at every offset: is it valid in ONLY types (18) or natures (25)?

    Returns {offset: {table_name: decoded_name}, "offset:u8": {table_name: name}}
    """
    hits = {}
    scan = min(len(data), 128)

    for off in range(0, scan - 1, 2):
        val = struct.unpack_from('<H', data, off)[0]
        if val == 0:
            continue
        for tname, tbl in tables.items():
            if val < len(tbl):
                name = tbl[val]
                if isinstance(name, str) and len(name.strip()) >= 3:
                    hits.setdefault(off, {})[tname] = name.strip()

    tiny_tables = {n: t for n, t in tables.items() if len(t) <= 25}
    for off in range(scan):
        val = data[off]
        if val == 0:
            continue
        for tname, tbl in tiny_tables.items():
            if val < len(tbl):
                name = tbl[val]
                if isinstance(name, str) and len(name.strip()) >= 3:
                    hits.setdefault(f"{off}:u8", {})[tname] = name.strip()

    return hits


def _icr_measure_fields(narc, scan_limit=128):
    """Map record structure from the byte pattern before any table lookups.

    Read every u16 at every even offset across all files.
    Track max of the full u16, AND max of each byte independently.

    Three categories:
      - High byte always 00 → u8 in a u16 slot
      - Both bytes small (≤25) but high byte non-zero → packed u8 pair
        (two independent small values sharing a u16 slot, like type pairs
        or Pokéathlon stats where each byte is 0-4)
      - At least one byte uses wide range → real u16 (species, moves, items)

    Returns {offset: {max, u16, packed, lo_max, hi_max}}.
    """
    maxes = {}
    lo_maxes = {}
    hi_maxes = {}
    for i in range(len(narc.files)):
        data = narc.files[i]
        if not data:
            continue
        end = min(len(data), scan_limit)
        for off in range(0, end - 1, 2):
            val = struct.unpack_from('<H', data, off)[0]
            lo = val & 0xFF
            hi = (val >> 8) & 0xFF
            if off not in maxes or val > maxes[off]:
                maxes[off] = val
            if off not in lo_maxes or lo > lo_maxes[off]:
                lo_maxes[off] = lo
            if off not in hi_maxes or hi > hi_maxes[off]:
                hi_maxes[off] = hi

    result = {}
    for off in maxes:
        mx = maxes[off]
        lo_mx = lo_maxes.get(off, 0)
        hi_mx = hi_maxes.get(off, 0)
        if mx < 256:
            # High byte always 00 → single u8
            result[off] = {'max': mx, 'u16': False, 'packed': False,
                           'lo_max': lo_mx, 'hi_max': hi_mx}
        elif lo_mx <= 25 and hi_mx <= 25:
            # Both bytes small → packed u8 pair (types, natures, Pokéathlon stats)
            result[off] = {'max': mx, 'u16': False, 'packed': True,
                           'lo_max': lo_mx, 'hi_max': hi_mx}
        else:
            # At least one byte uses wide range → real u16
            result[off] = {'max': mx, 'u16': True, 'packed': False,
                           'lo_max': lo_mx, 'hi_max': hi_mx}
    return result


def _icr_read_narc(narc, tables, narc_path='', gc=None, val_lookup=None):
    """Read a NARC by trusting the data. Every value, every position, every file.

    The game reads offset 4 in trpoke and trusts it's the species.
    It reads offset 6 in personal and trusts it's the type.
    It follows species 25 to personal file 25 to get the type.
    We do exactly the same thing.

    No field confirmation step. No sampling. No thresholds.
    Each value at each offset in each file either resolves to a known
    table or it doesn't. If it does, that's a fact — record it.
    The position carries meaning: (narc, file, offset, value, table).

    The graph assembles itself from millions of individual facts.
    Cross-references between NARCs emerge from shared values.
    Intra-record properties emerge from values within the same file.
    The schema is implicit — never declared, never confirmed, just read.

    Returns structure summary for labeling/role assignment, or None.
    """
    fc = len(narc.files)
    if fc == 0:
        return None

    non_empty = [i for i in range(fc) if len(narc.files[i]) > 0]
    if len(non_empty) < 2:
        return None

    # Field width measurement — physical constraint from the bytes themselves.
    # If high byte is always 00 at an offset, it's a u8 field. Can't hold species (>255).
    # This isn't a heuristic — it's what the bytes physically ARE.
    field_map = _icr_measure_fields(narc)

    u8_offsets = {off for off, info in field_map.items() if not info['u16'] and not info['packed']}
    packed_offsets = {off for off, info in field_map.items() if info.get('packed', False)}

    big_tables = {n: t for n, t in tables.items() if len(t) > 256}
    small_tables = {n: t for n, t in tables.items() if len(t) <= 256}

    # The scan. Every file. Every offset. Trust the data.
    #
    # For each value that resolves: record it with its full position.
    # The position (narc + file + offset) is what gives the value meaning.
    # Value 13 at offset 6 in personal = type Electric.
    # Value 13 at offset 6 in trpoke = item #13.
    # Same value, same offset number, different NARC = different meaning.
    #
    # We also track which tables appear at which offsets (for labeling
    # and role assignment later), but this is an OBSERVATION from the
    # recorded facts — not a gate that controls what gets recorded.
    offset_tables = {}   # {offset_key: set of table_names seen here}
    edges = {}           # {offset_key: set of values seen here}
    references = set()   # all table names referenced anywhere in this NARC
    any_resolution = False

    for i in non_empty:
        data = narc.files[i]
        file_len = len(data)

        # Every even offset, full file. The game reads all of it. So do we.
        for off in range(0, file_len - 1, 2):
            val = struct.unpack_from('<H', data, off)[0]
            if val == 0:
                continue

            # Packed u8 pair — check each byte against small tables
            if off in packed_offsets and off < 128:
                lo = val & 0xFF
                hi = (val >> 8) & 0xFF
                for byte_val, suffix in [(lo, f"{off}:lo"), (hi, f"{off}:hi")]:
                    if byte_val == 0:
                        continue
                    for tname, tbl in small_tables.items():
                        if len(tbl) <= 25 and byte_val < len(tbl):
                            name = tbl[byte_val]
                            if isinstance(name, str) and len(name.strip()) >= 3:
                                offset_tables.setdefault(suffix, set()).add(tname)
                                edges.setdefault(suffix, set()).add(byte_val)
                                references.add(tname)
                                any_resolution = True
                continue  # packed offsets handled, don't also check as u16

            # Check this value against known tables
            resolved = False
            if val_lookup is not None:
                matching = val_lookup.get(val)
                if matching:
                    for tname in matching:
                        # Physical constraint: u8 field can't hold big-table indices
                        if off in u8_offsets and tname in big_tables:
                            continue
                        if off < 128:
                            offset_tables.setdefault(off, set()).add(tname)
                            edges.setdefault(off, set()).add(val)
                        references.add(tname)
                        resolved = True
                    if resolved and narc_path and gc:
                        # Stream directly to disk — no RAM accumulation.
                        valid = [t for t in matching if not (off in u8_offsets and t in big_tables)]
                        for tname in valid:
                            _graph_write_rev(gc, val, narc_path, i, off, tname)
                            _graph_write_fc(gc, narc_path, i, val, tname)
                    if resolved:
                        any_resolution = True
            else:
                # No val_lookup — linear check (slow fallback)
                # Record ALL matching tables, not just the first.
                for tname, tbl in tables.items():
                    if off in u8_offsets and tname in big_tables:
                        continue
                    if val < len(tbl):
                        name = tbl[val]
                        if isinstance(name, str) and len(name.strip()) >= 3:
                            if off < 128:
                                offset_tables.setdefault(off, set()).add(tname)
                                edges.setdefault(off, set()).add(val)
                            references.add(tname)
                            if narc_path and gc:
                                _graph_write_rev(gc, val, narc_path, i, off, tname)
                                _graph_write_fc(gc, narc_path, i, val, tname)
                            any_resolution = True

        # Individual bytes for tiny tables (type at byte 6, nature at byte 7)
        scan = min(file_len, 128)
        for off in range(scan):
            even_off = off & ~1
            if even_off in packed_offsets:
                continue
            val = data[off]
            if val == 0:
                continue
            for tname, tbl in small_tables.items():
                if len(tbl) <= 25 and val < len(tbl):
                    name = tbl[val]
                    if isinstance(name, str) and len(name.strip()) >= 3:
                        key = f"{off}:u8"
                        offset_tables.setdefault(key, set()).add(tname)
                        references.add(tname)
                        any_resolution = True

    if not any_resolution:
        return None

    # Derive fields for labeling — observation from recorded facts, not a gate.
    # "Which table appears most at this offset?" is metadata for labels/roles.
    # The graph edges already exist regardless of this derivation.
    confirmed = {}
    for off_key, tnames in offset_tables.items():
        if len(tnames) == 1:
            tname = next(iter(tnames))
            # u8 matching big table = level 25 coinciding with species Pikachu
            is_u8 = isinstance(off_key, int) and off_key in u8_offsets
            if is_u8 and tname in big_tables:
                continue
            confirmed[off_key] = tname

    # Index table — positional edge. File 25 IS species 25.
    # Exact count match only (±1 for dummy file/entry — structural fact).
    index_table = None
    has_u16_fields = any(isinstance(off, int) for off in confirmed)

    if has_u16_fields:
        for tname, tbl in tables.items():
            tbl_len = len(tbl)
            if fc == tbl_len or fc == tbl_len + 1 or fc == tbl_len - 1:
                index_table = tname
                break
    else:
        # Positional match: file count matches a known species-indexed NARC.
        # Exact (±1 for dummy) OR larger (extra files = alternate forms).
        # 508 base species + 46 form files = 554 in Pokéathlon, for example.
        # If this NARC has at least as many files as personal/learnsets,
        # the first N files map positionally to species.
        srv = _srv()
        for p, role in srv.narc_roles.items():
            if role in ('personal', 'learnsets', 'evolutions'):
                try:
                    other_fc = len(srv._get_narc(p).files)
                    if fc >= other_fc - 1:
                        index_table = 'species'
                        break
                except Exception:
                    pass

    index_offset = 0
    if index_table:
        tbl = tables.get(index_table, [])
        tbl_len = len(tbl)
        entry0 = tbl[0] if tbl else ''
        entry0_empty = not (isinstance(entry0, str) and len(entry0.strip()) >= 3)
        if fc == tbl_len and not entry0_empty:
            index_offset = 1
        elif not has_u16_fields and fc > tbl_len:
            index_offset = 1

    # No resolutions that help with labeling and no positional match.
    if not confirmed and not index_table:
        return None

    all_sizes = set(len(narc.files[i]) for i in non_empty)
    uniform = len(all_sizes) == 1

    return {
        'file_count': fc,
        'file_size': next(iter(all_sizes)) if uniform else 0,
        'uniform': uniform,
        'fields': confirmed,
        'field_map': field_map,
        'edges': edges,
        'index_table': index_table,
        'index_offset': index_offset,
        'references': references,
    }


def _icr_cross_reference(narc_structures):
    """ICR Phase 2: follow values between NARCs.

    Returns:
        table_to_narcs: {table_name: [narc_paths]}
        cross_refs: {narc_path: {other_path: {shared_tables}}}
    """
    table_to_narcs = {}
    for path, structure in narc_structures.items():
        for tname in structure.get('references', set()):
            table_to_narcs.setdefault(tname, []).append(path)

    cross_refs = {}
    for tname, paths in table_to_narcs.items():
        if len(paths) < 2:
            continue
        for path in paths:
            for other in paths:
                if other == path:
                    continue
                cross_refs.setdefault(path, {}).setdefault(other, set()).add(tname)

    return table_to_narcs, cross_refs


def _icr_narc_desc(structure, narc_path=''):
    """Human-readable description from what ICR measured.

    Returns a string role label or None (graphics/sound — skip).
    """
    idx_tbl = structure.get('index_table')
    refs = structure.get('references', set())
    uniform = structure.get('uniform', False)
    fs = structure.get('file_size', 0)

    if idx_tbl == 'species' and uniform:
        if fs == 20:
            return 'Pokéathlon'
        elif fs in (44, 76):
            return 'Personal Data'
        elif 'moves' in refs:
            return 'Battle Facility Pokemon'
        else:
            return f'Personal ({fs}B)'

    elif idx_tbl == 'species' and not uniform:
        return 'Learnsets'

    elif idx_tbl == 'trainer_names':
        if 'species' in refs and 'moves' in refs:
            return 'Trainer Pokemon'
        elif uniform and fs in (16, 20):
            return 'Trainer Data'
        elif 'species' in refs:
            return 'Trainer Pokemon'
        return 'Trainer Data'

    elif idx_tbl == 'moves':
        return 'Move Data'

    elif idx_tbl == 'items':
        return 'Item Data'

    elif idx_tbl == 'location_names':
        if fs == 196:
            return 'Encounters (HGSS)'
        elif fs == 424:
            return 'Encounters (DPPt)'
        elif fs in (232, 928):
            return 'Encounters (Gen5)'
        return 'Encounters'

    elif isinstance(idx_tbl,str) and idx_tbl:
        tbl_label = idx_tbl.replace('_names', '').replace('_', ' ').title()
        return f'{tbl_label} Data'

    # No index table: graphics, sound, accidental byte collision. Skip.
    return None


# Which text tables are semantically relevant to each NARC role.
# Prevents level values (17) from matching ability indices ("Quicken") in learnset labels.
_FIELD_RELEVANCE = {
    'Learnsets': {'moves'},
    'Personal Data': {'types', 'abilities', 'items'},
    'Move Data': {'types'},
    'Trainer Pokemon': {'species', 'moves', 'items', 'abilities'},
    'Trainer Data': {'trainer_names', 'trainer_classes'},
    'Encounters': {'species', 'location_names'},
    'Encounters (HGSS)': {'species', 'location_names'},
    'Encounters (DPPt)': {'species', 'location_names'},
    'Encounters (Gen5)': {'species', 'location_names'},
    'Item Data': {'items'},
    'Battle Facility Pokemon': {'species', 'moves', 'items'},
}


def _icr_label_file(data, structure, file_idx, tables, narc_desc=None):
    """Label one file from what it contains. The data names itself.

    Primary name comes from the index table (species, trainer_names, etc.).
    Secondary names come from confirmed fields filtered by FIELD_RELEVANCE —
    only tables semantically relevant to the NARC's role appear in labels.
    """
    idx_tbl = structure.get('index_table')
    index_offset = structure.get('index_offset', 0)

    primary = None
    if idx_tbl:
        tbl = tables.get(idx_tbl, [])
        lookup = file_idx + index_offset
        if lookup < len(tbl):
            name = tbl[lookup]
            if isinstance(name, str) and name.strip():
                primary = name.strip()

    if not data:
        return primary or f"#{file_idx}"

    # Filter secondary fields by role relevance
    allowed = _FIELD_RELEVANCE.get(narc_desc) if narc_desc else None

    parts = []
    seen = {primary.lower()} if primary else set()
    for off in sorted(k for k in structure.get('fields', {}) if isinstance(k, int)):
        tname = structure['fields'][off]
        # Skip the index table's own field — already captured as primary
        if tname == idx_tbl:
            continue
        # Skip irrelevant tables for this NARC role
        if allowed and tname not in allowed:
            continue
        if off + 2 > len(data):
            continue
        val = struct.unpack_from('<H', data, off)[0]
        if val == 0:
            continue
        tbl = tables.get(tname, [])
        if val < len(tbl):
            name = tbl[val]
            if isinstance(name, str) and len(name.strip()) >= 3 and name.strip().lower() not in seen:
                parts.append(name.strip())
                seen.add(name.strip().lower())

    if primary:
        if parts:
            return f"{primary}, {', '.join(parts[:8])}"
        return primary

    return ', '.join(parts[:6]) if parts else f"#{file_idx}"


def _icr_scan_arm(data, tables):
    """Scan ARM binary (arm9, arm7, overlay) for text table references.

    Same approach as full file scan in _icr_read_narc: every even offset,
    every table. Returns set of table names referenced in this binary.

    No f100 hunt — Gen IV/V text lives in NARCs, not ARM9 character tables.
    """
    references = set()
    data_len = len(data)
    for off in range(0, data_len - 1, 2):
        val = struct.unpack_from('<H', data, off)[0]
        if val == 0:
            continue
        for tname, tbl in tables.items():
            if val < len(tbl):
                name = tbl[val]
                if isinstance(name, str) and len(name.strip()) >= 3:
                    references.add(tname)
                    break
    return references


def _flipnote_save():
    """Flush current flipnote to disk."""
    srv = _srv()
    if not srv.current_flipnote:
        return
    with open(srv.current_flipnote['path'], 'w', encoding='utf-8') as f:
        json.dump(srv.current_flipnote['data'], f, indent=2, ensure_ascii=False)


def _icr_cache_path(gc):
    return Path.home() / ".linkplay" / "flipnotes" / f"{gc}_icr.json"


def _icr_cache_save(gc):
    cache = _icr_cache.get(gc)
    if not cache:
        return
    p = _icr_cache_path(gc)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, indent=2, ensure_ascii=False)
    except Exception:
        pass


def _graph_cache_path(gc):
    return Path.home() / ".linkplay" / "flipnotes" / f"{gc}_graph.jsonl"


# Open streaming writers per gc — edges written immediately, never accumulated in RAM
_graph_writers = {}  # gc -> open file handle


def _graph_writer_open(gc):
    """Open the streaming graph file for writing. Call at BFS start."""
    p = _graph_cache_path(gc)
    p.parent.mkdir(parents=True, exist_ok=True)
    _graph_writers[gc] = open(p, 'w', encoding='utf-8', buffering=1)  # line-buffered


def _graph_writer_close(gc):
    """Close the streaming writer after BFS completes."""
    fh = _graph_writers.pop(gc, None)
    if fh:
        try:
            fh.close()
        except Exception:
            pass


def _graph_write_rev(gc, val, narc, file_idx, offset, table_name):
    """Write one rev_idx edge directly to disk. No RAM accumulation."""
    fh = _graph_writers.get(gc)
    if fh:
        fh.write(f'{{"r":{val},"n":{json.dumps(narc)},"f":{file_idx},"o":{offset},"t":{json.dumps(table_name)}}}\n')


def _graph_write_fc(gc, narc, file_idx, val, table_name):
    """Write one file_contents edge directly to disk. No RAM accumulation."""
    fh = _graph_writers.get(gc)
    if fh:
        fh.write(f'{{"c":{json.dumps(narc)},"f":{file_idx},"v":{val},"t":{json.dumps(table_name)}}}\n')


def _graph_cache_load(gc):
    """Build a lightweight offset index from the JSONL graph file.

    Instead of loading 50M+ edges into RAM, we build two small dicts:
      _rev_offsets[gc]  — {value: [byte_offset, ...]}   (just ints)
      _fc_offsets[gc]   — {"narc:file_idx": [byte_offset, ...]}  (just ints)

    These are tiny — one int per edge rather than a full tuple.
    Queries then seek directly to those byte positions and read the entries.
    The graph can be any size. RAM usage stays flat.
    """
    p = _graph_cache_path(gc)
    if not p.exists():
        return False
    rev_off = {}   # value(int) -> [byte_offset, ...]
    fc_off = {}    # "narc:file_idx" -> [byte_offset, ...]
    try:
        with open(p, 'rb') as fh:  # binary for accurate byte positions
            pos = 0
            for raw_line in fh:
                line = raw_line.strip()
                if not line:
                    pos += len(raw_line)
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    pos += len(raw_line)
                    continue
                if 'r' in entry:
                    val = entry['r']
                    rev_off.setdefault(val, []).append(pos)
                elif 'c' in entry:
                    key = f"{entry['c']}:{entry['f']}"
                    fc_off.setdefault(key, []).append(pos)
                pos += len(raw_line)
        _rev_offsets[gc] = rev_off
        _fc_offsets[gc] = fc_off
        return True
    except Exception as e:
        print(f"[linkplay] Graph index build failed for {gc}: {e}", file=sys.stderr, flush=True)
        return False


def _graph_read_at(gc, offsets):
    """Read specific entries from the graph JSONL file by byte offset."""
    p = _graph_cache_path(gc)
    results = []
    try:
        with open(p, 'rb') as fh:
            for off in offsets:
                fh.seek(off)
                raw = fh.readline()
                try:
                    results.append(json.loads(raw))
                except Exception:
                    pass
    except Exception:
        pass
    return results


def _icr_cache_load(gc):
    p = _icr_cache_path(gc)
    if not p.exists():
        return
    try:
        with open(p, "r", encoding="utf-8") as fh:
            _icr_cache[gc] = json.load(fh)
    except Exception:
        pass


def _eonet_try_write_flipnote(narc_path, desc, file_labels, structure=None, cross_refs=None):
    """Write ICR structure + labels to the in-memory ICR cache.

    Stores the NARC's field layout (which offsets hold which tables, u8 vs u16)
    so byte-level searches work from cache without re-running BFS.
    """
    srv = _srv()
    if not srv.current_rom:
        return False
    gc = srv.current_rom['header']['game_code']
    cache = _icr_cache.setdefault(gc, {})
    try:
        entry = {'desc': desc}
        if structure:
            # Store confirmed fields: {offset: table_name} — the search map
            entry['fields'] = {str(k): v for k, v in structure.get('fields', {}).items()
                               if isinstance(k, int)}
            # Store field widths: {offset: {max, u16}} — the 00-pattern
            fm = structure.get('field_map', {})
            if fm:
                entry['field_map'] = {str(k): v for k, v in fm.items()}
            entry['file_count'] = structure.get('file_count', 0)
            entry['file_size'] = structure.get('file_size', 0)
            entry['index_table'] = structure.get('index_table')
            entry['index_offset'] = structure.get('index_offset', 0)
        cache[narc_path] = entry
        for idx, label in file_labels.items():
            cache[f"{narc_path}:{idx:03d}"] = label
        return True
    except Exception:
        return False


def _build_entity_metadata(gc: str):
    """Build entity metadata: parse rich labels into individual searchable names.

    Each index entry label looks like "Cheren, Lillipup, Tackle (Trainer Pokemon)".
    This function splits that into individual names, records their category,
    and tracks which other entities they co-occur with (relationships).

    After this runs, _entity_metadata['lillipup'] knows:
    - Which games Lillipup appears in
    - What categories: Personal Data, Learnsets, Trainer Pokemon, etc.
    - What it's related to: Cheren (via Trainer Pokemon), Tackle (via Learnsets), etc.
    """
    srv = _srv()
    index = srv.eonet_index.get(gc, [])

    for entry in index:
        label = entry.get('label', '')
        if not label:
            continue

        # Parse: "Cheren, Lillipup, Tackle (Trainer Pokemon)" → names + category
        category = ''
        name_part = label
        if '(' in label and label.endswith(')'):
            paren_start = label.rfind('(')
            category = label[paren_start + 1:-1].strip()
            name_part = label[:paren_start].strip().rstrip(',').strip()

        # Split comma-separated entity names
        names = [n.strip() for n in name_part.split(',') if len(n.strip()) >= 3]
        names_lower = [n.lower() for n in names]

        for name in names:
            nl = name.lower()
            if nl not in _entity_metadata:
                _entity_metadata[nl] = {
                    'game_codes': set(),
                    'contexts': {},
                    'related': {},
                }

            meta = _entity_metadata[nl]
            meta['game_codes'].add(gc)

            if category:
                meta['contexts'].setdefault(gc, [])
                if category not in meta['contexts'][gc]:
                    meta['contexts'][gc].append(category)

            # Track co-occurring entities (relationships)
            others = {n for n in names_lower if n != nl}
            if others:
                meta['related'].setdefault(gc, set())
                meta['related'][gc].update(others)


def resolve_chain(gc: str, start_value: int, start_table: str = None, depth: int = 1):
    """Follow foreign key chains through the ROM's relational structure.

    Starting from a value (e.g., species 504 = Patrat), find every file
    that references it, then collect all OTHER entities in those files.
    At depth 2, follow those entities to THEIR files, and so on.

    Returns:
        {
            'start': {'value': 504, 'table': 'species', 'name': 'Patrat'},
            'files': [(narc_path, file_idx, role), ...],  # files containing start
            'connected': {
                'species': {506: 'Lillipup', ...},  # other species in same files
                'moves': {526: 'Work Up', 44: 'Bite', ...},
                'items': {17: 'Potion', ...},
                ...
            },
            'by_narc': {
                'a/0/9/2': {  # trpoke
                    'files': [156, 764, ...],
                    'entities': {'species': [...], 'moves': [...]}
                },
                ...
            }
        }
    """
    rev = _reverse_index.get(gc, {})
    fc = _file_contents.get(gc, {})
    if not rev or not fc:
        return {'error': 'No relational index built yet — run spotlight first'}

    srv = _srv()
    tables = {}
    for tname in ('species', 'moves', 'items', 'abilities', 'location_names',
                   'trainer_names', 'trainer_classes'):
        tbl = srv.text_tables.get(tname, [])
        if tbl:
            tables[tname] = tbl

    # Resolve start name
    start_name = ''
    if start_table and start_table in tables:
        tbl = tables[start_table]
        if start_value < len(tbl):
            start_name = tbl[start_value]
    else:
        # Try all tables
        for tname, tbl in tables.items():
            if start_value < len(tbl) and isinstance(tbl[start_value], str) and len(tbl[start_value].strip()) >= 3:
                start_table = tname
                start_name = tbl[start_value]
                break

    # Get narc roles for labeling
    roles = srv.narc_roles if srv.current_rom and srv.current_rom['header']['game_code'] == gc else \
        srv.loaded_roms.get(gc, {}).get('narc_roles', {})

    result = {
        'start': {'value': start_value, 'table': start_table or '?', 'name': start_name},
        'files': [],
        'connected': {},
        'by_narc': {},
    }

    visited_values = {(start_value, start_table)}
    current_wave = [(start_value, start_table)]

    for d in range(depth):
        next_wave = []
        for val, tbl in current_wave:
            # Find all files containing this value
            for narc_p, fi, off, tname in rev.get(val, []):
                role = roles.get(narc_p, '?')
                file_key = (narc_p, fi)

                # Record this file
                if file_key not in [(f[0], f[1]) for f in result['files']]:
                    result['files'].append((narc_p, fi, role))

                # Group by NARC
                narc_info = result['by_narc'].setdefault(narc_p, {
                    'role': role, 'files': set(), 'entities': {}
                })
                narc_info['files'].add(fi)

                # Get all other entities in this same file
                for other_val, other_tbl in fc.get(file_key, []):
                    if (other_val, other_tbl) in visited_values:
                        continue
                    # Resolve the name
                    other_name = ''
                    if other_tbl in tables and other_val < len(tables[other_tbl]):
                        other_name = tables[other_tbl][other_val]

                    result['connected'].setdefault(other_tbl, {})[other_val] = other_name
                    narc_info['entities'].setdefault(other_tbl, set()).add(other_val)

                    if d + 1 < depth and (other_val, other_tbl) not in visited_values:
                        next_wave.append((other_val, other_tbl))
                        visited_values.add((other_val, other_tbl))

        current_wave = next_wave

    # Clean up sets for serialization
    for narc_p, info in result['by_narc'].items():
        info['files'] = sorted(info['files'])
        info['entities'] = {k: sorted(v) for k, v in info['entities'].items()}

    return result


# ============================================================
# Graph Queries — simple reads against the graph
# ============================================================
# These are NOT algorithms. They're lookups. The graph already has the answers.

def graph_find_value(gc: str, value: int, table: str = None):
    """Find every location where a value appears in the ROM.

    graph_find_value('IPK', 25) → every file in HeartGold that references species 25.
    Seeks directly to the relevant entries — no full file scan, no RAM ceiling.
    """
    offsets = _rev_offsets.get(gc, {}).get(value, [])
    if not offsets:
        return []
    entries = _graph_read_at(gc, offsets)
    hits = [(e['n'], e['f'], e['o'], e['t']) for e in entries if 'r' in e]
    if table:
        hits = [h for h in hits if h[3] == table]
    return hits


def graph_file_entities(gc: str, narc_path: str, file_idx: int):
    """List every entity in a specific file.

    graph_file_entities('IPK', 'a/0/5/6', 260) → all entities in Red's trpoke entry.
    Seeks directly by (narc, file_idx) — no full file scan.
    """
    key = f"{narc_path}:{file_idx}"
    offsets = _fc_offsets.get(gc, {}).get(key, [])
    if not offsets:
        return []
    entries = _graph_read_at(gc, offsets)
    return [(e['v'], e['t']) for e in entries if 'c' in e]


def graph_connected(gc: str, value: int, table: str = None):
    """Follow one hop: find all other entities that share a file with this value.

    graph_connected('IPK', 25, 'species') → every move, item, ability, trainer
    that appears in any file alongside species 25 (Pikachu).
    """
    hits = graph_find_value(gc, value, table)
    connected = {}
    for narc_p, file_idx, off, tname in hits:
        for other_val, other_tbl in graph_file_entities(gc, narc_p, file_idx):
            if other_val == value and other_tbl == (table or tname):
                continue  # skip self
            connected.setdefault(other_tbl, {}).setdefault(other_val, []).append((narc_p, file_idx))
    return connected


def graph_at_offset(gc: str, narc_path: str, offset: int, table: str = None):
    """Find all values at a specific offset across all files in a NARC.

    graph_at_offset('IPK', 'a/0/0/2', 6, 'type_names') → every type value
    at offset 6 in the personal NARC. The position IS the schema.
    Scans the JSONL file once — no RAM ceiling.
    """
    p = _graph_cache_path(gc)
    results = {}
    if not p.exists():
        return results
    try:
        with open(p, 'rb') as fh:
            for raw_line in fh:
                if b'"r"' not in raw_line:
                    continue
                try:
                    e = json.loads(raw_line)
                except Exception:
                    continue
                if e.get('n') == narc_path and e.get('o') == offset:
                    if table and e.get('t') != table:
                        continue
                    results.setdefault(e['r'], []).append(e['f'])
    except Exception:
        pass
    return results


def _extract_entities_from_query(msg_lower: str, loaded_gcs: list) -> list:
    """Extract game entities from query using _entity_metadata (built by ICR).

    Checks single words and multi-word sequences (up to 3 words) against
    the metadata index. Returns matches with their ICR contexts.

    Also resolves the text table name and index for each entity so that
    direct NARC mapping works in _resolve_gc (no more falling through
    to substring search every time).
    """
    srv = _srv()
    entities = []
    found_names = set()
    words = msg_lower.split()
    gc_set = set(loaded_gcs)

    # Context label -> text table name mapping (deterministic, not scored)
    _CONTEXT_TO_TABLE = {
        'Personal Data': 'species', 'Learnsets': 'species',
        'Evolutions': 'species', 'Trainer Pokemon': 'trainer_names',
        'Trainer Data': 'trainer_names', 'Move Data': 'moves',
        'Item Data': 'items', 'Encounters': 'location_names',
        'Encounters (HGSS)': 'location_names', 'Encounters (DPPt)': 'location_names',
        'Encounters (Gen5)': 'location_names', 'Battle Facility Pokemon': 'species',
    }

    # Check multi-word sequences first (longest match wins), then singles
    for window in (3, 2, 1):
        for i in range(len(words) - window + 1):
            phrase = ' '.join(words[i:i + window])
            if len(phrase) < 3 or phrase in found_names:
                continue
            if phrase not in _entity_metadata:
                continue

            entity_info = _entity_metadata[phrase]
            available_in = entity_info['game_codes'] & gc_set
            if not available_in:
                continue

            all_contexts = []
            for gc in available_in:
                all_contexts.extend(entity_info['contexts'].get(gc, []))

            # Resolve text table + index: deterministic lookup, not guessing
            table = ''
            index = -1
            # Determine which table to check from contexts
            candidate_tables = set()
            for ctx in all_contexts:
                tbl = _CONTEXT_TO_TABLE.get(ctx)
                if tbl:
                    candidate_tables.add(tbl)

            # Look up the entity in the actual text tables
            for tbl_name in candidate_tables:
                tbl = srv.text_tables.get(tbl_name, [])
                for idx, entry in enumerate(tbl):
                    if isinstance(entry, str) and entry.strip().lower() == phrase:
                        table = tbl_name
                        index = idx
                        break
                if index >= 0:
                    break

            entities.append({
                'name': phrase,
                'contexts': all_contexts,
                'game_codes': available_in,
                'table': table,
                'index': index,
            })
            found_names.add(phrase)
            # Mark individual words as used so they don't double-match
            for w in words[i:i + window]:
                found_names.add(w)

    return entities



def _bfs_process_narc(path, narc, s, tables, gc, labels_dict, index_entries,
                      all_narcs, queue, visited, _connected):
    """Process a discovered NARC: label files, assign role, follow edges.

    Shared by the main BFS loop and the ARM9 drain loop.
    Returns True if the NARC was labeled (has a desc), False otherwise.
    Skips role assignment for the text NARC.
    """
    srv = _srv()
    desc = _icr_narc_desc(s, path)
    if desc is not None:
        file_labels = {}
        for i in range(len(narc.files)):
            raw = _icr_label_file(narc.files[i], s, i, tables, narc_desc=desc)
            file_labels[i] = f"{raw} ({desc})"

        _eonet_try_write_flipnote(path, desc, file_labels, structure=s)

        labels_dict[path] = {
            'desc': desc,
            'labels': file_labels,
            'fields': {str(k): v for k, v in s['fields'].items()},
            'index_table': s.get('index_table'),
            'index_offset': s.get('index_offset', 0),
            'cross_refs': [],
            'meta': {
                'file_count': s['file_count'],
                'file_size': s['file_size'],
                'uniform': s['uniform'],
            },
        }

        for idx, label in file_labels.items():
            index_entries.append({
                'name': label.lower(),
                'path': f"{path}:{idx:03d}",
                'idx': idx,
                'label': label,
                'narc': path,
                'desc': desc,
            })

    # Assign narc_roles — skip the text NARC.
    # GAME_INFO roles are authoritative and cannot be overwritten by BFS.
    text_narc_path = srv.GAME_INFO.get(gc, {}).get('narcs', {}).get('text', '')
    game_narcs = srv.GAME_INFO.get(gc, {}).get('narcs', {})
    has_game_role = any(gpath == path and role != 'text' for role, gpath in game_narcs.items())
    if has_game_role:
        # Path has a hardcoded GAME_INFO role — preserve it, don't let BFS override.
        pass
    elif path != text_narc_path:
        idx_tbl = s.get('index_table')
        refs = s.get('references', set())
        fs = s.get('file_size', 0)
        if idx_tbl == 'species' and s['uniform']:
            if fs == 20:
                srv.narc_roles[path] = 'pokeathlon_performance'
            elif 'moves' in refs:
                srv.narc_roles[path] = 'battle_facility_pokemon'
            else:
                srv.narc_roles[path] = 'personal'
        elif idx_tbl == 'species' and not s['uniform']:
            srv.narc_roles[path] = 'learnsets'
        elif idx_tbl == 'moves':
            srv.narc_roles[path] = 'move_data'
        elif idx_tbl == 'items':
            srv.narc_roles[path] = 'items'
        elif idx_tbl == 'trainer_names':
            if 'species' in refs and 'moves' in refs:
                srv.narc_roles[path] = 'trpoke'
            elif s['uniform'] and fs in (16, 20):
                srv.narc_roles[path] = 'trdata'
            else:
                srv.narc_roles[path] = 'trpoke' if 'species' in refs else 'trdata'
        elif idx_tbl == 'location_names':
            srv.narc_roles[path] = 'encounters'
        elif refs:
            srv.narc_roles[path] = '+'.join(sorted(str(r) for r in refs))
    else:
        return desc is not None

    # Follow edges — queue every NARC that shares actual values with this one.
    edge_values = s.get('edges', {})
    for other_path, other_narc in all_narcs.items():
        if other_path in visited or other_path in queue:
            continue
        if _connected(other_narc, edge_values):
            queue.append(other_path)

    return desc is not None


def _build_eonet(gc=None):
    """ICR: Map the entire ROM via BFS. Called after text tables are bootstrapped.

    Fast path: if ICR cache exists for this game code, restore from it and return.
    Full path: BFS from NARCs whose file count matches a known text table length,
    following edges until no unvisited connected NARCs remain.
    Graphics/sound never get queued: nothing in the game data references them.
    """
    srv = _srv()
    if gc is None:
        gc = srv.current_rom['header']['game_code']

    # Fast path: restore from cache, skip BFS entirely
    _icr_cache_load(gc)
    if _icr_cache.get(gc):
        cache = _icr_cache[gc]
        labels_dict = {}
        index_entries = []
        for key, value in cache.items():
            # Handle both old format (string) and new format (dict)
            if isinstance(value, dict):
                label = value.get('desc', str(value))
            else:
                label = str(value)
            
            if ':' in key:
                narc_path, idx_str = key.rsplit(':', 1)
                try:
                    idx = int(idx_str)
                    index_entries.append({
                        'name': label.lower(), 'path': f"{narc_path}:{idx:03d}",
                        'idx': idx, 'label': label, 'narc': narc_path,
                        'desc': cache.get(narc_path, {}).get('desc', '') if isinstance(cache.get(narc_path), dict) else cache.get(narc_path, ''),
                    })
                except ValueError:
                    pass
            else:
                entry = {'desc': label, 'labels': {}, 'cross_refs': []}
                # Restore field structures from cache (for byte-level search)
                if isinstance(value, dict):
                    if 'fields' in value:
                        entry['fields'] = value['fields']
                    if 'field_map' in value:
                        entry['field_map'] = value['field_map']
                    if 'index_table' in value:
                        entry['index_table'] = value['index_table']
                    if 'index_offset' in value:
                        entry['index_offset'] = value['index_offset']
                    entry['meta'] = {
                        'file_count': value.get('file_count', 0),
                        'file_size': value.get('file_size', 0),
                    }
                labels_dict[key] = entry
                # Restore narc_roles from cached desc
                _ROLE_FROM_DESC = {
                    'Pokéathlon': 'pokeathlon_performance',
                    'Personal Data': 'personal', 'Learnsets': 'learnsets',
                    'Move Data': 'move_data', 'Item Data': 'items',
                    'Trainer Pokemon': 'trpoke', 'Trainer Data': 'trdata',
                    'Encounters (HGSS)': 'encounters', 'Encounters (DPPt)': 'encounters',
                    'Encounters (Gen5)': 'encounters', 'Encounters': 'encounters',
                    'Battle Facility Pokemon': 'battle_facility_pokemon',
                }
                role = _ROLE_FROM_DESC.get(label)
                text_path = srv.GAME_INFO.get(gc, {}).get('narcs', {}).get('text', '')
                if role and key != text_path:
                    srv.narc_roles[key] = role
        srv.eonet_labels[gc] = labels_dict
        srv.eonet_index[gc] = index_entries

        # GAME_INFO roles are authoritative — override any ICR cache misidentifications
        # (e.g., trdata mislabeled as trpoke due to blind-scan false species refs)
        game_info = srv.GAME_INFO.get(gc, {})
        for role, narc_path in game_info.get('narcs', {}).items():
            if role != 'text':
                srv.narc_roles[narc_path] = role

        # Restore graph from disk cache too
        _graph_cache_load(gc)

        _build_entity_metadata(gc)
        _build_game_title_map()
        return len(labels_dict), len(index_entries)
    
    tables = _icr_get_tables()
    if not tables:
        return 0, 0

    val_lookup = _icr_build_val_lookup(tables)

    all_narcs = {}
    rom_type = srv.current_rom.get('type', 'nds')
    walker = _walk_all_garcs() if rom_type == '3ds' else _walk_all_narcs()
    for narc_path, narc in walker:
        all_narcs[narc_path] = narc

    def _connected(other_narc, edge_values):
        """Check if a candidate NARC shares actual values with the current NARC.

        No file count tolerance. No guessing. The bytes either match or they don't.
        Peek at a few files of the candidate. If any value from our edge sets
        appears as a u16 in their data, they're connected through that value.
        """
        other_fc = len(other_narc.files)
        if other_fc < 2:
            return False
        # Check up to 3 files for shared values
        check_indices = [0, min(1, other_fc - 1), min(other_fc // 2, other_fc - 1)]
        for ci in check_indices:
            peek = other_narc.files[ci]
            if len(peek) < 4:
                continue
            for off, vals in edge_values.items():
                if not isinstance(off, int):
                    continue
                if off + 2 <= len(peek):
                    pval = struct.unpack_from('<H', peek, off)[0]
                    if pval > 0 and pval in vals:
                        return True
            # Also check all even offsets in peek against edge values
            # (the candidate might store the same value at a different offset)
            all_edge_vals = set()
            for vals in edge_values.values():
                if isinstance(vals, set):
                    all_edge_vals.update(v for v in vals if v > 0)
            if all_edge_vals:
                for poff in range(0, len(peek) - 1, 2):
                    pval = struct.unpack_from('<H', peek, poff)[0]
                    if pval in all_edge_vals:
                        return True
        return False

    narc_structures = {}
    labels_dict = {}
    index_entries = []
    visited = set()

    # Open the streaming graph writer. Every edge goes straight to disk.
    # No RAM accumulation — MemoryError eliminated.
    _graph_writer_open(gc)

    # Seed queue: start from NARCs that GAME_INFO already identifies.
    # These are KNOWN paths — text, personal, trdata, etc.
    # The text tables gave us the val_lookup (the decoder ring).
    # Now follow actual values from known NARCs to discover the rest.
    queue = []
    game_narcs = srv.GAME_INFO.get(gc, {}).get('narcs', {})
    for role, narc_path in game_narcs.items():
        if narc_path in all_narcs:
            queue.append(narc_path)

    # If no GAME_INFO (shouldn't happen for supported games), seed from
    # NARCs whose file count exactly matches a text table (±1 for dummy).
    if not queue:
        for narc_path, narc in all_narcs.items():
            fc = len(narc.files)
            for tbl in tables.values():
                tbl_len = len(tbl)
                if fc == tbl_len or fc == tbl_len + 1 or fc == tbl_len - 1:
                    queue.append(narc_path)
                    break

    # BFS — follow the values. The graph reveals itself.
    while queue:
        path = queue.pop(0)
        if path in visited:
            continue
        visited.add(path)

        narc = all_narcs.get(path)
        if narc is None:
            continue

        s = _icr_read_narc(narc, tables, narc_path=path, gc=gc, val_lookup=val_lookup)
        if not s:
            continue

        narc_structures[path] = s

        _bfs_process_narc(path, narc, s, tables, gc, labels_dict, index_entries,
                          all_narcs, queue, visited, _connected)

    # ARM9, ARM7, overlays — scan for values, queue any connected NARCs.
    rom = srv.current_rom.get('rom')
    if rom:
        arm_binaries = {}
        for arm_name in ('arm9', 'arm7'):
            arm_data = srv.current_rom.get(f'{arm_name}_data') or getattr(rom, arm_name, None)
            if arm_data and len(arm_data) > 100:
                arm_binaries[f'{arm_name}.bin'] = bytes(arm_data)
        overlays = srv.current_rom.get('overlays', {})
        for ov_id, ov_data in overlays.items():
            if ov_data and len(ov_data) > 100:
                arm_binaries[f'overlay{ov_id}.bin'] = bytes(ov_data)

        for arm_path, arm_data in arm_binaries.items():
            if arm_path in visited:
                continue
            visited.add(arm_path)
            refs = _icr_scan_arm(arm_data, tables)
            if not refs:
                continue
            # Queue unvisited NARCs — use actual value sharing, not file count
            for other_path, other_narc in all_narcs.items():
                if other_path in visited or other_path in queue:
                    continue
                # Check if any value in the ARM binary appears in the candidate
                # This is coarse but deterministic — the value is there or it isn't
                other_fc = len(other_narc.files)
                if other_fc < 2:
                    continue
                peek = other_narc.files[min(1, other_fc - 1)]
                if len(peek) < 4:
                    continue
                found = False
                for poff in range(0, min(len(peek), 64) - 1, 2):
                    pval = struct.unpack_from('<H', peek, poff)[0]
                    if pval > 0 and val_lookup and pval in val_lookup:
                        matching = val_lookup[pval]
                        if matching & refs:
                            queue.append(other_path)
                            found = True
                            break
                if found:
                    continue

        # Drain newly queued NARCs
        while queue:
            path = queue.pop(0)
            if path in visited:
                continue
            visited.add(path)
            narc = all_narcs.get(path)
            if narc is None:
                continue
            s = _icr_read_narc(narc, tables, narc_path=path, gc=gc, val_lookup=val_lookup)
            if not s:
                continue
            narc_structures[path] = s
            _bfs_process_narc(path, narc, s, tables, gc, labels_dict, index_entries,
                              all_narcs, queue, visited, _connected)

    # Cross-reference enrichment
    table_to_narcs, cross_refs = _icr_cross_reference(narc_structures)
    for path, others in cross_refs.items():
        if path not in narc_structures:
            continue
        shared = set()
        for other_tables in others.values():
            shared.update(other_tables)
        narc_structures[path]['references'].update(shared)
        if path in labels_dict:
            labels_dict[path]['cross_refs'] = list(others.keys())

    srv.eonet_labels[gc] = labels_dict
    srv.eonet_index[gc] = index_entries
    srv.eonet_labels[gc]['_cross_refs'] = {
        tname: len(paths) for tname, paths in table_to_narcs.items()
    }

    # Close the streaming writer — graph is now fully on disk.
    _graph_writer_close(gc)

    # Load graph from disk into memory for queries this session.
    _graph_cache_load(gc)

    _flipnote_save()
    _icr_cache_save(gc)

    # GAME_INFO roles are authoritative — override any BFS misidentifications.
    game_info = srv.GAME_INFO.get(gc, {})
    for role, narc_path in game_info.get('narcs', {}).items():
        if role != 'text':
            srv.narc_roles[narc_path] = role

    # Build entity metadata after full BFS
    _build_entity_metadata(gc)

    rev_count = sum(len(v) for v in _reverse_index.get(gc, {}).values())
    fc_count = len(_file_contents.get(gc, {}))
    print(f"[linkplay] BFS complete for {gc}: {len(narc_structures)} NARCs, "
          f"{len(index_entries)} index entries, {len(labels_dict)} labeled, "
          f"{rev_count} graph edges, {fc_count} files indexed",
          file=sys.stderr, flush=True)

    return len(narc_structures), len(index_entries)

def _eonet_search_flipnote(gc, query_lower):
    """Search manual flipnote notes then ICR cache for entries matching query.

    Manual notes take priority. ICR cache is searched as fallback.
    Returns (results, from_flipnote) where results is list of (key, label).
    """
    srv = _srv()
    results = []

    # Search manual notes first
    if srv.current_flipnote:
        notes = srv.current_flipnote['data'].get('notes', {})
        for key, note_data in notes.items():
            desc = note_data.get('description', '') if isinstance(note_data, dict) else str(note_data)
            if query_lower in desc.lower():
                results.append((key, desc))

    if results:
        return results, True

    # Fall back to ICR cache
    if gc not in _icr_cache:
        _icr_cache_load(gc)
    cache = _icr_cache.get(gc, {})
    for key, desc in cache.items():
        if query_lower in desc.lower():
            results.append((key, desc))

    if results:
        return results, True
    return [], False


# ============================================================
# ROM Discovery — lazy, query-driven
# ============================================================

# game_code -> absolute path, populated on demand
_discovered_roms = {}

# ICR cache: game_code -> {key: description}, kept separate from flipnote
_icr_cache = {}

# Reverse index: value -> [(narc_path, file_idx, offset, table_name), ...]
# Kept for resolve_chain compatibility. Populated only if graph fits in RAM.
_reverse_index = {}  # gc -> {value: [(narc, file_idx, offset, table), ...]}

# File contents index: (narc_path, file_idx) -> [(value, table_name), ...]
_file_contents = {}  # gc -> {(narc, file_idx): [(value, table), ...]}

# Lightweight byte-offset indices — replaces full in-memory graph.
# These are small: one int per edge instead of a full tuple.
# Queries seek to specific positions in the JSONL file — any size graph, flat RAM.
_rev_offsets = {}   # gc -> {value: [byte_offset, ...]}
_fc_offsets = {}    # gc -> {"narc:file_idx": [byte_offset, ...]}

# Auto-built enc->loc tables: derived from zone headers during BFS.
_auto_enc_loc = {}  # gc -> {enc_file_idx: loc_name_idx}

# Game code -> title mapping, built from ROM headers
_game_titles = {}  # {game_code: title_lower} populated from headers
_game_abbreviations = {}  # {game_code: [abbreviations]} derived from titles

def _derive_game_abbreviation(gc: str, title: str = None) -> list:
    """Derive abbreviations from game title. No hardcoding.
    
    Takes first letters of significant words in title.
    E.g., "Pokemon Black 2" -> "pb2", "black2", "b2"
    """
    abbrevs = []
    
    if not title:
        return abbrevs
    
    # Clean and split title
    title_lower = title.lower()
    words = [w for w in title_lower.split() if w not in ('pokemon', 'version', 'the')]
    
    if not words:
        return abbrevs
    
    # Full word combinations (e.g., "black2", "heartgold")
    if len(words) <= 2:
        abbrevs.append(''.join(words))
    
    # First letter abbreviation (e.g., "hgss" from "heart gold soul silver")
    if len(words) >= 2:
        abbrevs.append(''.join(w[0] for w in words))
    
    # Number-based variants (e.g., "b2" from "black 2")
    if len(words) == 2 and words[1].isdigit():
        abbrevs.append(words[0][0] + words[1])  # "b2"
        abbrevs.append(words[0] + words[1])      # "black2"
    
    return abbrevs

def _build_game_title_map():
    """Scan all discovered/loaded ROMs and build game_code -> title mapping.
    
    Uses actual ROM headers - no hardcoding.
    """
    srv = _srv()
    
    # Scan loaded ROMs
    for gc, state in srv.loaded_roms.items():
        rom_data = state.get('current_rom', {})
        header = rom_data.get('header', {})
        title = header.get('game_title', '')
        if title:
            _game_titles[gc] = title.lower()
            _game_abbreviations[gc] = _derive_game_abbreviation(gc, title)
    
    # Scan current ROM
    if srv.current_rom:
        gc = srv.current_rom['header']['game_code']
        title = srv.current_rom['header'].get('game_title', '')
        if title and gc not in _game_titles:
            _game_titles[gc] = title.lower()
        if gc not in _game_abbreviations:
            _game_abbreviations[gc] = _derive_game_abbreviation(gc, title)
    
    # Scan discovered ROM paths
    for gc, rom_path in _discovered_roms.items():
        if gc not in _game_titles:
            try:
                header = _peek_nds_header(rom_path)
                if header and header.get('game_title'):
                    title = header['game_title']
                    _game_titles[gc] = title.lower()
                    _game_abbreviations[gc] = _derive_game_abbreviation(gc, title)
            except Exception:
                pass

def _peek_nds_header(filepath):
    """Read full NDS header. Returns dict with game_code and game_title."""
    try:
        with open(filepath, 'rb') as f:
            short_title = f.read(12).decode('ascii', errors='ignore').strip('\x00')
            full_code = f.read(4).decode('ascii', errors='ignore')
            f.seek(0x68)
            banner_offset = struct.unpack('<I', f.read(4))[0]
        
        game_code = full_code[:3] if len(full_code) >= 3 else full_code
        
        # Read English title from banner
        english_title = ""
        if banner_offset:
            try:
                with open(filepath, 'rb') as f:
                    f.seek(banner_offset + 0x340)
                    title_bytes = f.read(256)
                    title = title_bytes.decode('utf-16-le', errors='ignore')
                    title = title.split('\x00')[0]
                    lines = title.split('\n')
                    if len(lines) >= 2:
                        english_title = f"{lines[0]} {lines[1]}".strip()
                    elif lines:
                        english_title = lines[0].strip()
            except Exception:
                pass
        
        return {
            'game_code': game_code,
            'game_title': english_title if english_title else short_title,
        }
    except Exception:
        return None

# All known DS Pokemon game codes
_GAME_HINTS = {
    'diamond': 'ADA', 'pearl': 'APA', 'platinum': 'CPU',
    'heartgold': 'IPK', 'heart gold': 'IPK',
    'soulsilver': 'IPG', 'soul silver': 'IPG',
    'black 2': 'IRE', 'white 2': 'IRD',
    'black': 'IRB', 'white': 'IRA',
    # Common abbreviations
    'b2w2': 'IRE', 'bw2': 'IRE',
    'bw': 'IRB',
    'hgss': 'IPK',
    'dppt': 'CPU', 'dp': 'ADA', 'pt': 'CPU',
}

def _peek_nds_game_code(filepath):
    """Read game code from NDS header. Offset 12, 4 bytes. Returns 3-char code or None."""
    try:
        with open(filepath, 'rb') as f:
            f.seek(12)
            full_code = f.read(4).decode('ascii', errors='ignore')
        gc = full_code[:3].strip()
        return gc if len(gc) == 3 else None
    except Exception:
        return None

def _scan_dirs_for_game_code(target_gc):
    """Scan outward from LinkPlay for an .nds file with matching game code.

    Order: LinkPlay dir -> parent dirs -> common locations (Documents, Downloads, Desktop, drives).
    Filename checked first as a fast hint; header confirms.
    Returns absolute path string or None.
    """
    import os

    # Name hints: game code -> plausible filename substrings
    name_hints = {
        'ADA': ['diamond'], 'APA': ['pearl'], 'CPU': ['platinum'],
        'IPK': ['heartgold', 'heart gold', 'hgss'],
        'IPG': ['soulsilver', 'soul silver', 'hgss'],
        'IRB': ['black'], 'IRA': ['white'],
        'IRE': ['black 2', 'black2'], 'IRD': ['white 2', 'white2'],
    }
    hints = name_hints.get(target_gc, [])

    linkplay_dir = Path(os.path.dirname(os.path.abspath(__file__)))

    # Build search order: LinkPlay and parents, then common locations
    search_dirs = []
    d = linkplay_dir
    for _ in range(4):
        search_dirs.append(d)
        if d.parent == d:
            break
        d = d.parent

    # Common locations
    home = Path.home()
    for common in ['Documents', 'Downloads', 'Desktop', 'ROMs', 'roms', 'Games', 'games']:
        p = home / common
        if p.exists():
            search_dirs.append(p)

    # Drive roots on Windows
    try:
        import string
        for letter in string.ascii_uppercase:
            drive = Path(f"{letter}:/")
            if drive.exists() and drive not in search_dirs:
                search_dirs.append(drive)
    except Exception:
        pass

    def _check(filepath):
        gc = _peek_nds_game_code(filepath)
        return gc == target_gc

    seen = set()
    for base in search_dirs:
        if base in seen:
            continue
        seen.add(base)
        try:
            # Filename-hint pass first (fast)
            for nds_file in base.rglob('*.nds'):
                name_lower = nds_file.name.lower()
                if any(h in name_lower for h in hints):
                    if _check(nds_file):
                        return str(nds_file)
            # Full header scan (slower, catches renamed files)
            for nds_file in base.rglob('*.nds'):
                if _check(nds_file):
                    return str(nds_file)
        except (PermissionError, OSError):
            continue

    return None

def _discover_roms_for_query(msg_lower):
    """Parse message for game hints, scan for any not yet discovered.

    Populates _discovered_roms. Returns list of game codes found.
    """
    needed = []
    for hint in sorted(_GAME_HINTS.keys(), key=lambda x: -len(x)):
        if hint in msg_lower:
            gc = _GAME_HINTS[hint]
            if gc not in needed:
                needed.append(gc)

    for gc in needed:
        if gc not in _discovered_roms:
            found = _scan_dirs_for_game_code(gc)
            if found:
                _discovered_roms[gc] = found

    # Build game title map from discovered ROMs
    _build_game_title_map()

    return [gc for gc in needed if gc in _discovered_roms]



def _smart_game_selection(subjects: list, loaded_gcs: list, msg_lower: str) -> list:
    """Select which game(s) to route to based on entity availability and query context.
    
    Uses entity metadata built during ICR to determine:
    1. Which games have ALL the entities mentioned
    2. If entity only exists in one generation, prefer that
    3. Use message context (game names, keywords) to disambiguate
    4. Fall back to most recent game if ambiguous
    
    Returns ordered list of game codes to try (most relevant first).
    """
    if not subjects or not loaded_gcs:
        return loaded_gcs
    
    # Check which games have each entity
    entity_games = {}  # entity_name -> set of game codes
    for s in subjects:
        name = s['name'].lower()
        if name in _entity_metadata:
            entity_games[name] = _entity_metadata[name]['game_codes'] & set(loaded_gcs)
        else:
            # Entity not in metadata - might be in all games, check loaded ROMs
            entity_games[name] = set(loaded_gcs)
    
    if not entity_games:
        return loaded_gcs
    
    # Find games that have ALL entities
    common_games = set.intersection(*entity_games.values()) if entity_games else set(loaded_gcs)
    
    if not common_games:
        # No single game has all entities - return games sorted by how many entities they have
        game_scores = {}
        for gc in loaded_gcs:
            game_scores[gc] = sum(1 for games in entity_games.values() if gc in games)
        return sorted(loaded_gcs, key=lambda gc: -game_scores.get(gc, 0))
    
    if len(common_games) == 1:
        return list(common_games)
    
    # Multiple games have all entities - use context to disambiguate
    # Check for explicit game mentions in message using dynamic title matching
    import re
    
    for gc in common_games:
        # Check against game title from ROM header (word boundaries)
        if gc in _game_titles:
            title_lower = _game_titles[gc]
            # Match significant words from title with word boundaries
            title_words = [w for w in title_lower.split() if len(w) > 3]
            for word in title_words:
                if re.search(rf'\b{re.escape(word)}\b', msg_lower):
                    return [gc] + [g for g in common_games if g != gc]
        
        # Check against abbreviations (HGSS, BW, BW2, etc.)
        if gc in _game_abbreviations:
            for abbrev in _game_abbreviations[gc]:
                if re.search(rf'\b{re.escape(abbrev)}\b', msg_lower):
                    return [gc] + [g for g in common_games if g != gc]
        
        # Check against 3-letter game code (word boundary)
        if re.search(rf'\b{re.escape(gc.lower())}\b', msg_lower):
            return [gc] + [g for g in common_games if g != gc]
    
    # No explicit game mention - prefer most recent generation
    gen5_games = common_games & _GEN_BOUNDARIES.get(5, set())
    if gen5_games:
        return sorted(gen5_games, reverse=True) + sorted(common_games - gen5_games, reverse=True)
    
    return sorted(common_games, reverse=True)


def _resolve_gc(gc, msg, subjects, preferred_refs):
    srv = _srv()

    # --- Step 0: Byte search (deterministic) ---
    # Names → text table index → u16 value → scan NARC bytes.
    # No heuristics. The bytes match or they don't.
    byte_paths = []
    for s in subjects:
        table = s.get('table', '')
        idx = s.get('index', -1)
        name = s['name']
        if not table or idx < 0:
            continue

        hits = _byte_search(table, idx, gc=gc)
        for h in hits:
            file_path = f"{h['narc_path']}:{h['file_idx']:03d}"
            label = f"{name} ({h['role']})" if h['role'] else name
            byte_paths.append({
                "path": file_path,
                "label": label,
                "narc": h['narc_path'],
                "name": label.lower(),
            })

    if byte_paths:
        # Deduplicate
        seen = set()
        unique = []
        for p in byte_paths:
            if p["path"] not in seen:
                seen.add(p["path"])
                unique.append(p)
        # Filter by preferred_refs if present
        if preferred_refs:
            filtered = [p for p in unique if any(r in p["label"] for r in preferred_refs)]
            if filtered:
                return filtered
        return unique

    # --- Fallback: existing role-based mapping ---
    # Maps text table name → list of (narc_role, human_label_suffix) to try.
    # Ordered by specificity; preferred_refs narrows further.
    _TABLE_TO_ROLES = {
        'trainer_names': [
            ('trpoke', 'Trainer Pokemon'),
            ('trdata', 'Trainer Data'),
        ],
        'species': [
            ('personal', 'Personal Data'),
            ('learnsets', 'Learnsets'),
            ('evolutions', 'Evolutions'),
            ('encounters', 'Encounters'),
        ],
        'moves': [
            ('move_data', 'Move Data'),
        ],
        'items': [
            ('items', 'Item Data'),
        ],
        'location_names': [
            ('encounters', 'Encounters'),
        ],
    }

    # Use gc-specific narc_roles — not always the current ROM's
    current_gc = srv.current_rom['header']['game_code'] if srv.current_rom else None
    if gc == current_gc:
        target_narc_roles = srv.narc_roles
    elif gc in srv.loaded_roms:
        target_narc_roles = srv.loaded_roms[gc].get('narc_roles', {})
    else:
        target_narc_roles = {}

    paths = []

    # Step 1: Try direct index→NARC mapping for each subject
    # NOTE: trainer_names CANNOT use direct index mapping — text table index
    # is NOT the file index in trdata/trpoke. A trainer can have multiple
    # entries (rival, gym leader, rematch) at scattered file indices.
    # Trainers go straight to ICR search in Steps 2/3.
    for s in subjects:
        table = s.get('table', '')
        idx = s.get('index', -1)
        name = s['name']

        # Skip direct mapping for trainers — index mismatch
        if table == 'trainer_names':
            continue

        role_candidates = _TABLE_TO_ROLES.get(table, [])

        if not role_candidates or idx < 0:
            continue

        # Narrow by keyword hints if they match (set intersection, not exclusion)
        if preferred_refs:
            matched_roles = [
                (role, label) for role, label in role_candidates
                if label in preferred_refs
            ]
            if matched_roles:
                role_candidates = matched_roles
            # If no keyword matches this entity's roles, show all roles (don't skip)

        for role, label in role_candidates:
            # Find the NARC path for this role using gc-specific roles
            narc_path = next((p for p, r in target_narc_roles.items() if r == role), None)
            if not narc_path:
                continue
            file_path = f"{narc_path}:{idx:03d}"
            full_label = f"{name} ({label})"
            paths.append({
                "path": file_path,
                "label": full_label,
                "narc": narc_path,
                "name": full_label.lower(),
            })

    # Step 2: If direct mapping found nothing, search flipnote/ICR cache
    if not paths:
        fn = []
        for s in subjects:
            r, ok = _eonet_search_flipnote(gc, s["name"].lower())
            if ok:
                fn.extend(r)
        if fn:
            for k, l in fn:
                paths.append({"path": k, "label": l, "narc": k.split(":")[0], "name": l.lower()})

    # Step 3: If still nothing, fall back to eonet_index substring match
    if not paths:
        if gc in srv.eonet_index and srv.eonet_index[gc]:
            for s in subjects:
                for e in srv.eonet_index[gc]:
                    if s["name"].lower() in e["name"]:
                        paths.append(e)

    if not paths:
        return None

    seen = set()
    unique = []
    for p in paths:
        if p["path"] not in seen:
            seen.add(p["path"])
            unique.append(p)
    return unique

_CLEAN_GAME_NAMES = {
    'ADA': 'Pokémon Diamond', 'APA': 'Pokémon Pearl', 'CPU': 'Pokémon Platinum',
    'IPK': 'Pokémon HeartGold', 'IPG': 'Pokémon SoulSilver',
    'IRB': 'Pokémon Black', 'IRA': 'Pokémon White',
    'IRE': 'Pokémon Black 2', 'IRD': 'Pokémon White 2',
}


def _resolve_rom_path(gc):
    """Find the ROM file path for a game code. Single source of truth."""
    srv = _srv()
    rp = (srv.loaded_roms.get(gc, {}).get('current_rom') or {}).get('path', '')
    if not rp and srv.current_rom and srv.current_rom.get('header', {}).get('game_code') == gc:
        rp = srv.current_rom.get('path', '')
    if not rp:
        rp = _discovered_roms.get(gc, '')
    if not rp:
        try:
            import json as _jr
            _reg = _jr.loads((Path.home() / ".linkplay" / "last_rom.json").read_text(encoding='utf-8'))
            rp = _reg.get(gc, '')
        except Exception:
            pass
    return rp


def _format_sliver_block(gc, hits, multi=False):
    """Format a single ROM's resolution results into a sliver block.

    multi=False: directive format — '# Game Name' (single winner, use this)
    multi=True:  menu format — '[rom: Game Name (GC)]' (multiple options, pick one)
    """
    srv = _srv()
    current_gc = srv.current_rom.get('header', {}).get('game_code') if srv.current_rom else None

    if gc != current_gc:
        dc = ", ".join(f"{gc}:{p['path']} - {p['label']}" for p in hits)
    else:
        dc = ", ".join(f"{p['path']} - {p['label']}" for p in hits)

    rp = _resolve_rom_path(gc)
    t = _CLEAN_GAME_NAMES.get(gc, gc)
    sp = ""
    if gc not in srv.loaded_roms and rp:
        sp = f"  spotlight: [{rp}]\n"

    if multi:
        # Menu format: include flipnote notes for context
        notes = getattr(srv, 'current_flipnote', None)
        notes = notes['data'].get('notes', {}) if notes else {}
        note_lines = []
        for p in hits:
            pkey = p['path']; narc_key = pkey.rsplit(':',1)[0] if ':' in pkey else pkey
            for k in (pkey, narc_key):
                if k in notes:
                    desc = notes[k].get('description', notes[k]) if isinstance(notes[k], dict) else notes[k]
                    note_lines.append(f"  note[{k}]: {str(desc)[:120]}")
        note_block = ('\n' + '\n'.join(note_lines)) if note_lines else ''
        return f"[rom: {t} ({gc})]\n{sp}  decipher: [{dc}]{note_block}"
    else:
        # Directive format: single winner
        return f"# {t}\n{sp}  decipher: [{dc}]\n"


def _byte_search(table_name, value, gc=None):
    """Search all NARCs for files containing a u16 value at a confirmed field offset.

    Deterministic: the bytes match or they don't. No string matching.
    Names go in (via text table lookup), hex searches the middle,
    names come out (via text table resolve).

    Args:
        table_name: which text table ('species', 'moves', 'items', etc.)
        value: the index to search for (e.g., 25 for Pikachu)
        gc: game code (defaults to current ROM)

    Returns: list of {narc_path, file_idx, offset, role} for every match.
    """
    srv = _srv()
    if gc is None:
        gc = srv.current_rom['header']['game_code'] if srv.current_rom else None
    if not gc:
        return []

    # Get NARC structures — field layouts from BFS/cache
    labels = srv.eonet_labels.get(gc, {})
    results = []

    for narc_path, info in labels.items():
        if narc_path.startswith('_') or not isinstance(info, dict):
            continue

        fields = info.get('fields', {})
        # Find offsets confirmed for this table
        target_offsets = [int(off) for off, tname in fields.items()
                          if tname == table_name and off.lstrip('-').isdigit()]
        if not target_offsets:
            continue

        desc = info.get('desc', '')

        # Get actual NARC bytes from the loaded ROM
        try:
            narc = srv._get_narc(narc_path)
        except Exception:
            continue

        # Scan every file at the confirmed offsets
        for file_idx in range(len(narc.files)):
            data = narc.files[file_idx]
            if not data:
                continue
            for off in target_offsets:
                if off + 2 <= len(data):
                    val = struct.unpack_from('<H', data, off)[0]
                    if val == value:
                        results.append({
                            'narc_path': narc_path,
                            'file_idx': file_idx,
                            'offset': off,
                            'role': desc,
                        })
                        break  # found in this file, move to next

    return results


def eonet_resolve(message: str, game_code: str = None, prior_context: list = None) -> dict:
    """Resolve user message → routing sliver. NOT a tool. Called by the driver.

    No vector DB. No embeddings. No NLP. String matching against in-memory
    text tables + flipnote entries. Sub-100ms.
    
    Conservative: only resolves when CONFIDENT the message is about game data.
    Claude has full conversation context - let it handle ambiguous cases.
    """
    srv = _srv()
    msg_lower = message.lower().strip()

    # Build candidate GC list
    if game_code:
        gcs = [game_code]
    else:
        gcs = []
        if srv.current_rom:
            gcs.append(srv.current_rom['header']['game_code'])
        for _gc in srv.loaded_roms:
            if _gc not in gcs: gcs.append(_gc)
        if not gcs: gcs=_discover_roms_for_query(msg_lower)
        if not gcs: 
            return {"resolved":False,"reason":"no ROM"}
    msg = msg_lower

    # Relational matching ONLY — if it's not in the ICR index, it doesn't exist.
    # The game structure IS the validation. No text table regex guessing.
    # _entity_metadata was built by ICR tracing actual foreign key relationships:
    # species→learnsets→moves, trainers→pokemon, routes→encounters, etc.
    # An entity only exists in _entity_metadata if the game references it structurally.
    subjects = []
    icr_entities = _extract_entities_from_query(msg_lower, gcs)
    for entity in icr_entities:
        subjects.append({
            'name': entity['name'],
            'contexts': entity.get('contexts', []),
            'game_codes': entity.get('game_codes', set()),
            'table': entity.get('table', ''),
            'index': entity.get('index', -1),
        })

    subjects.sort(key=lambda s: -len(s['name']))
    spans, filtered = [], []
    for s in subjects:
        pos = msg.find(s['name'].lower())
        end = pos + len(s['name'])
        if not any(pos >= a and end <= b for a, b in spans):
            filtered.append(s)
            spans.append((pos, end))
    subjects = filtered

    if not subjects:
        return {"resolved": False, "reason": "no recognizable subject in message"}

    # No confidence gate. The Eonet is a database: if entities exist in the
    # relational index, surface them. Claude handles disambiguation.

    # Smart game selection based on entity availability
    target_gcs = _smart_game_selection(subjects, gcs, msg_lower)
    
    # Keyword-to-role mapping: English context narrows results (set operations, not scores)
    _KEYWORD_ROLES = {
        'team': 'Trainer Pokemon', 'teams': 'Trainer Pokemon', 'roster': 'Trainer Pokemon',
        'pokemon': 'Trainer Pokemon', 'party': 'Trainer Pokemon',
        'stats': 'Personal Data', 'base stats': 'Personal Data', 'abilities': 'Personal Data',
        'typing': 'Personal Data', 'types': 'Personal Data',
        'learnset': 'Learnsets', 'learns': 'Learnsets', 'moves learned': 'Learnsets',
        'level up': 'Learnsets', 'level-up': 'Learnsets',
        'evolution': 'Evolutions', 'evolve': 'Evolutions', 'evolves': 'Evolutions',
        'encounter': 'Encounters', 'encounters': 'Encounters', 'wild': 'Encounters',
        'route': 'Encounters', 'catch': 'Encounters', 'found': 'Encounters',
        'move data': 'Move Data', 'power': 'Move Data', 'accuracy': 'Move Data',
        'item': 'Item Data', 'items': 'Item Data', 'price': 'Item Data',
        'trainer': 'Trainer Data', 'ai': 'Trainer Data',
    }
    preferred_refs = set()
    for kw, role in _KEYWORD_ROLES.items():
        if kw in msg:
            preferred_refs.add(role)

    rom_results = {}
    for _gc in target_gcs:
        hits = _resolve_gc(_gc, msg, subjects, preferred_refs)
        if hits: rom_results[_gc] = hits
    
    if not rom_results:
        return {"resolved": False, "reason": "no matching paths"}

    # Pick winner: single ROM, context-preferred, or all
    if len(rom_results) == 1:
        winner_gc = list(rom_results.keys())[0]
        sliver = _format_sliver_block(winner_gc, rom_results[winner_gc])
        return {"resolved": True, "sliver": sliver, "game_code": winner_gc}

    # Multiple ROMs: check if prior context biases toward one
    if prior_context and len(rom_results) > 1:
        context_votes = {}
        for entity_name, gc in prior_context:
            context_votes[gc] = context_votes.get(gc, 0) + 1
        if context_votes:
            preferred_gc = max(context_votes, key=context_votes.get)
            if context_votes[preferred_gc] >= 2 and preferred_gc in rom_results:
                sliver = _format_sliver_block(preferred_gc, rom_results[preferred_gc])
                return {"resolved": True, "sliver": sliver, "game_code": preferred_gc}

    # Multiple ROMs, no clear winner: surface all — let Claude decide
    blocks = [_format_sliver_block(gc, hits, multi=True) for gc, hits in rom_results.items()]
    sliver = blocks[0] if len(blocks) == 1 else "\n".join(blocks)
    return {"resolved": True, "sliver": sliver, "roms": list(rom_results.keys())}

# ============================================================
# Standalone test mode
# ============================================================
async def _test_resolve(message: str, server_script: str = None):
    """Test eonet resolution without full MCP setup.

    Imports server.py directly, opens a ROM, and tests the full pipeline.
    """
    import sys, os, glob

    if server_script is None:
        server_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts", "server.py")

    roms_dir = os.path.join(os.path.dirname(server_script), '..', 'roms')
    if not os.path.isdir(roms_dir):
        roms_dir = os.path.join(os.path.dirname(server_script), 'roms')

    nds_files = glob.glob(os.path.join(roms_dir, '*.nds'))
    if not nds_files:
        print(f"No .nds files found in {roms_dir}")
        return

    print(f"Found {len(nds_files)} ROM(s):")
    for i, f in enumerate(nds_files):
        print(f"  [{i}] {os.path.basename(f)}")

    rom_path = nds_files[0]
    print(f"\nUsing: {os.path.basename(rom_path)}")

    sys.path.insert(0, os.path.dirname(server_script))
    import importlib.util
    spec = importlib.util.spec_from_file_location("server", server_script)
    srv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(srv)

    print("Opening ROM (ICR runs at spotlight — text tables seed everything)...")
    result = await srv.spotlight(rom_path)
    gc = result.get('game_code')
    print(f"  Game: {result.get('game_title')} ({gc})")

    eonet_info = result.get('eonet', {})
    if 'error' in eonet_info:
        print(f"  ICR error: {eonet_info['error']}")
    else:
        print(f"  ICR: {eonet_info.get('narcs', 0)} NARCs, {eonet_info.get('indexed', 0)} files indexed")
        if eonet_info.get('f100_arm9_offset'):
            print(f"  f100 ARM9 offset: {eonet_info['f100_arm9_offset']}")
        if eonet_info.get('cross_refs'):
            xr = eonet_info['cross_refs']
            top = sorted(xr.items(), key=lambda x: -x[1])[:5]
            print(f"  Cross-refs: {', '.join(f'{k}:{v}' for k, v in top)}")

    print(f"\nResolving: \"{message}\"")
    resolution = srv.eonet_resolve(message, gc)
    print(f"  Resolved: {resolution.get('resolved')}")

    if resolution.get("resolved"):
        print(f"  Sliver: {resolution['sliver']}")
        print(f"  Paths: {resolution['paths']}")
        print(f"  Labels: {resolution['labels']}")

        # Simulate driver header building
        driver_sim = EonetDriver(None)
        driver_sim.eonet_supported = True
        driver_sim.set_active_rom(gc, rom_path)
        header = driver_sim._build_header(resolution['sliver'], [gc])
        print(f"\n  Claude would see:")
        print(f"  {header}")
        print(f"  [user: {message}]")
        print(f"\n  Follow-up (ROM path already in context):")
        header2 = driver_sim._build_header(resolution['sliver'], [])
        print(f"  {header2}")
        print(f"  [user: {message}]")
    else:
        print(f"  Not resolved: {resolution.get('reason', '?')}")
        print(f"  Eonet steps aside — Claude receives original message, works normally.")


def _hosts_redirect_activate():
    """Add claude.ai redirect entry to the hosts file.
    
    Returns True if entry was freshly written, False if already present or failed.
    """
    hosts_path = Path(r"C:\Windows\System32\drivers\etc\hosts")
    try:
        content = hosts_path.read_text(encoding='utf-8')
        if "claude.ai" not in content:
            with open(hosts_path, 'a', encoding='utf-8') as f:
                f.write('\n127.0.0.1 claude.ai  # eonet\n')
            return True  # freshly written
    except Exception as e:
        print(f"[EONET] Warning: could not activate hosts redirect: {e}", file=sys.stderr)

    return False  # already present or failed

def _hosts_redirect_deactivate():
    """Remove eonet claude.ai redirect entry from the hosts file."""
    hosts_path = Path(r"C:\Windows\System32\drivers\etc\hosts")
    try:
        lines = hosts_path.read_text(encoding='utf-8').splitlines(keepends=True)
        cleaned = [l for l in lines if "claude.ai" not in l]
        hosts_path.write_text(''.join(cleaned), encoding='utf-8')
    except Exception as e:
        print(f"[EONET] Warning: could not deactivate hosts redirect: {e}", file=sys.stderr)




def _eonet_pid_path():
    return Path.home()/'.linkplay'/'eonet.pid'
def _eonet_pid_write():
    try: _eonet_pid_path().write_text(str(os.getpid()))
    except: pass
def _eonet_pid_clear():
    try: _eonet_pid_path().unlink(missing_ok=True)
    except: pass
def _eonet_pid_check():
    import ctypes, socket as _sock
    p=_eonet_pid_path()
    if not p.exists(): return False,False
    try: old_pid=int(p.read_text().strip())
    except: p.unlink(missing_ok=True); return False,False
    # Check PID alive
    h=ctypes.windll.kernel32.OpenProcess(0x100000,False,old_pid)
    pid_alive=bool(h)
    if h: ctypes.windll.kernel32.CloseHandle(h)
    if not pid_alive: p.unlink(missing_ok=True); return True,False
    # PID alive — verify it actually holds port 443
    try:
        s=_sock.socket(_sock.AF_INET,_sock.SOCK_STREAM)
        s.settimeout(0.5)
        r=s.connect_ex(('127.0.0.1',443))
        s.close()
        if r==0: return False,True   # port 443 responding — real proxy
    except: pass
    # PID alive but port 443 not ours — stale PID reused by OS
    p.unlink(missing_ok=True); return True,False

async def _run_http_eonet_proxy(port: int = 443):
    """Method B: HTTPS proxy for Claude Desktop.

    Binds directly to localhost:443 with TLS (self-signed cert for claude.ai).
    Hosts file redirects claude.ai → 127.0.0.1, so Claude Desktop's browser
    hits this proxy instead of the real claude.ai.

    On each chat completion request:
      1. Calls eonet_resolve on the last user message
      2. If resolved: prepends routing sliver to the prompt
      3. Forwards full request to real claude.ai (bypassing hosts via direct DNS)
      4. Returns response unmodified

    Binds directly to port 443 (Windows allows non-elevated bind on loopback).
    """
    import aiohttp
    from aiohttp import web
    import socket
    import ssl as _ssl
    import sys as _sys

    CLAUDE_HOST = "claude.ai"
    CLAUDE_API  = f"https://{CLAUDE_HOST}"

    def _dns_lookup_all(hostname):
        """Query 8.8.8.8 directly to bypass hosts file redirect.
        Returns all A record IPs found. Handles CNAME chains (Cloudflare).
        """
        ips = []
        try:
            dns_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            dns_sock.settimeout(3)
            qname = b''.join(
                len(p).to_bytes(1, 'big') + p.encode()
                for p in hostname.split('.')
            ) + b'\x00'
            query = struct.pack('>HHHHHH', 0x1234, 0x0100, 1, 0, 0, 0) + qname + struct.pack('>HH', 1, 1)
            dns_sock.sendto(query, ('8.8.8.8', 53))
            dns_resp = dns_sock.recv(4096)
            dns_sock.close()

            ancount = struct.unpack('>H', dns_resp[6:8])[0]

            def _skip_name(buf, off):
                """Skip a DNS name (handles labels and pointer compression)."""
                while off < len(buf):
                    if buf[off] & 0xC0 == 0xC0:
                        return off + 2
                    if buf[off] == 0:
                        return off + 1
                    off += buf[off] + 1
                return off

            # Skip question section
            offset = 12
            offset = _skip_name(dns_resp, offset)
            offset += 4  # qtype + qclass

            # Walk ALL answer records — CNAMEs, A records, whatever order
            for _ in range(ancount):
                if offset >= len(dns_resp):
                    break
                offset = _skip_name(dns_resp, offset)
                if offset + 10 > len(dns_resp):
                    break
                rtype, rclass, ttl, rdlen = struct.unpack('>HHIH', dns_resp[offset:offset+10])
                offset += 10
                if offset + rdlen > len(dns_resp):
                    break
                if rtype == 1 and rdlen == 4:  # A record
                    ips.append('.'.join(str(b) for b in dns_resp[offset:offset+4]))
                # CNAME (5), AAAA (28), etc: skip via rdlen
                offset += rdlen
        except Exception:
            pass
        return ips

    def _dns_lookup_system(hostname):
        """Resolve via OS before hosts redirect is active. Clean lookup."""
        try:
            results = socket.getaddrinfo(hostname, 443, socket.AF_INET, socket.SOCK_STREAM)
            return list(set(r[4][0] for r in results))
        except Exception:
            return []

    # Start with Cloudflare fallback IPs, refresh async to avoid blocking
    _CLOUDFLARE_IPS = ["104.26.10.243", "162.159.135.233", "162.247.243.29"]
    _dns_cache = {CLAUDE_HOST: list(_CLOUDFLARE_IPS)}

    async def _dns_refresh_loop():
        # Immediate refresh on first run, then every 60s
        while True:
            loop = asyncio.get_event_loop()
            ips = await loop.run_in_executor(None, lambda: _dns_lookup_all(CLAUDE_HOST))
            if ips:
                _dns_cache[CLAUDE_HOST] = ips
            await asyncio.sleep(60)

    class _BypassResolver(aiohttp.ThreadedResolver):
        async def resolve(self, host, port=0, family=socket.AF_INET):
            ips = _dns_cache.get(host)
            if not ips:
                ips = _dns_lookup_all(host)
                if ips:
                    _dns_cache[host] = ips
            if not ips:
                raise OSError(f'[EONET] Cannot resolve {host} — no IPs')
            return [{'hostname': host, 'host': ip,
                     'port': port, 'family': family, 'proto': 0, 'flags': 0}
                    for ip in ips]

    _ssl_ctx_out = __import__('ssl').create_default_context()
    _ssl_ctx_out.check_hostname = False
    _ssl_ctx_out.verify_mode = __import__('ssl').CERT_NONE
    _aio_session = aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(ssl=_ssl_ctx_out, resolver=_BypassResolver()),
        auto_decompress=False
    )

    _log_path = Path.home() / ".linkplay" / "eonet_proxy.log"
    def _log(msg):
        line = f"[EONET] {msg}"
        print(line, file=_sys.stderr, flush=True)
        try:
            with open(_log_path, 'a', encoding='utf-8') as _lf:
                import datetime as _dt
                _lf.write(f"{_dt.datetime.now().strftime('%H:%M:%S')} {line}\n")
        except Exception:
            pass

    _SILENT = frozenset(['/healthcheck', '/api/bootstrap', '/oauth', '/auth', '/login', '/sso'])



    async def handle_completion(request: web.Request) -> web.StreamResponse:
        """Intercept claude.ai chat completion — inject Eonet sliver into prompt."""
        body = await request.read()
        try:
            data = json.loads(body)
        except Exception:
            _log(f"COMPLETION bad JSON: {body[:200]}")
            data = None

        if data is not None and 'prompt' in data:
            user_text = data['prompt']
            srv = _srv()
            resolution = eonet_resolve(user_text)
            if resolution.get('resolved'):
                sliver = resolution['sliver']
                _log(f"COMPLETION injecting sliver: {sliver[:80]}")
                data['prompt'] = f"[user: {user_text}]\n{sliver}\n"
                body = json.dumps(data).encode()
            else:
                _log(f"COMPLETION no sliver: {resolution.get('reason','?')}")
        else:
            _log(f"COMPLETION keys: {list(data.keys()) if data else 'unparseable'}")

        forward_headers = {
            k: v for k, v in request.headers.items()
            if k.lower() not in ('host', 'content-length', 'transfer-encoding')
        }
        forward_headers['content-length'] = str(len(body))
        try:
            _comp_headers = {k: v for k, v in forward_headers.items()
                             if k.lower() != 'accept-encoding'}
            _comp_headers['accept-encoding'] = 'identity'
            async with _aio_session.post(
                f"https://{CLAUDE_HOST}{request.path_qs}",
                data=body,
                headers=_comp_headers,
            ) as resp:
                HOP = frozenset(['connection', 'keep-alive', 'transfer-encoding',
                                 'te', 'trailers', 'upgrade',
                                 'proxy-authenticate', 'proxy-authorization'])
                fwd_headers = {k: v for k, v in resp.headers.items()
                               if k.lower() not in HOP}
                response = web.StreamResponse(status=resp.status, headers=fwd_headers)
                await response.prepare(request)
                async for chunk in resp.content.iter_any():
                    await response.write(chunk)
                await response.write_eof()
                return response
        except Exception as e:
            _log(f'COMPLETION upstream error: {e}')
            return web.Response(status=502, text=str(e))

    async def handle_passthrough(request: web.Request) -> web.StreamResponse:
        """Pass all other endpoints straight through to the correct upstream host.
        
        OAuth/SSO flows must pass through unmodified - no interception, no logging.
        """
        path_str = request.path
        
        # OAuth/auth paths pass through silently - don't log, don't modify
        is_auth = any(x in path_str.lower() for x in ['/oauth', '/auth', '/login', '/sso', '/callback'])
        
        silent = is_auth or any(path_str.startswith(s) for s in _SILENT)
        if not silent:
            _log(f'passthrough: {request.method} {path_str}')
        
        body = await request.read()
        forward_headers = {
            k: v for k, v in request.headers.items()
            if k.lower() not in ('host', 'content-length', 'transfer-encoding', 'accept-encoding')
        }
        forward_headers['accept-encoding'] = 'gzip, deflate'
        if body:
            forward_headers['content-length'] = str(len(body))
        try:
            async with _aio_session.request(
                request.method,
                f"https://{CLAUDE_HOST}{request.path_qs}",
                data=body or None,
                headers={**forward_headers, 'host': CLAUDE_HOST},
                allow_redirects=False,
            ) as resp:
                HOP2 = frozenset(['connection','keep-alive','transfer-encoding','te','trailers','upgrade'])
                fwd2 = {k: v for k, v in resp.headers.items() if k.lower() not in HOP2}
                out = web.StreamResponse(status=resp.status, headers=fwd2)
                await out.prepare(request)
                async for chunk in resp.content.iter_any():
                    await out.write(chunk)
                await out.write_eof()
                return out
        except Exception as e:
            if not silent:
                _log(f'passthrough error: {e}')
            return web.Response(status=502, text=str(e))

    app = web.Application()
    app.router.add_post('/{p:api/organizations/[^/]+/chat_conversations/[^/]+/completion}', handle_completion)
    app.router.add_route('*', '/{path_info:.*}', handle_passthrough)

    runner = web.AppRunner(app)
    await runner.setup()

    # Use SSL cert if available
    ssl_ctx = None
    cert_path = Path.home() / ".linkplay" / "eonet_ssl" / "cert.pem"
    key_path = Path.home() / ".linkplay" / "eonet_ssl" / "key.pem"
    if cert_path.exists() and key_path.exists():
        import ssl
        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_ctx.load_cert_chain(str(cert_path), str(key_path))

    site = web.TCPSite(runner, 'localhost', port, ssl_context=ssl_ctx)
    await site.start()

    # ROM loading handled once by _run_all() before proxies start
    _log("443 proxy ready.")

    asyncio.ensure_future(_dns_refresh_loop())
    import atexit
    _eonet_pid_write()
    atexit.register(_eonet_pid_clear)
    _log("Proxy ready.")

    # Run until cancelled — finally covers clean shutdown and handled exceptions
    try:
        while True:
            await asyncio.sleep(3600)
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        _log("Proxy shutting down.")
        _eonet_pid_clear()
        await _aio_session.close()
        await runner.cleanup()


async def _run_api_proxy(port: int = 8765):
    """Method A: HTTP proxy for Claude Code / IDE extensions.

    Binds localhost:8765 (plain HTTP, no TLS needed for loopback).
    Clients set ANTHROPIC_BASE_URL=http://localhost:8765 so their API
    traffic flows here instead of directly to api.anthropic.com.

    On each /v1/messages request:
      1. Extracts last user message text
      2. Calls eonet_resolve()
      3. If resolved: appends assistant prefill with routing sliver
      4. Forwards full request to https://api.anthropic.com
      5. Streams response back unmodified

    Agent prefill: the sliver is injected as {"role": "assistant", "content": ...}
    at the end of the messages array. Claude sees it as its own partial response
    and continues from there. The user's message stays untouched.
    """
    import aiohttp
    from aiohttp import web
    import sys as _sys

    ANTHROPIC_API = "https://api.anthropic.com"

    _log_path = Path.home() / ".linkplay" / "eonet_api_proxy.log"
    def _log(msg):
        line = f"[EONET-API] {msg}"
        print(line, file=_sys.stderr, flush=True)
        try:
            with open(_log_path, 'a', encoding='utf-8') as _lf:
                import datetime as _dt
                _lf.write(f"{_dt.datetime.now().strftime('%H:%M:%S')} {line}\n")
        except Exception:
            pass

    _aio_session = aiohttp.ClientSession(auto_decompress=False)

    # Rolling context for disambiguation (same idea as _EonetInterceptStream)
    from collections import deque
    _context = deque(maxlen=5)

    # Models that support assistant prefill (all retired/deprecated 3.x).
    # If Anthropic re-enables prefill for a future model, add it here.
    _PREFILL_MODELS = frozenset([
        'claude-3-opus-20240229',
        'claude-3-sonnet-20240229',
        'claude-3-haiku-20240307',
        'claude-3-5-sonnet-20240620',
        'claude-3-5-sonnet-20241022',
        'claude-3-5-haiku-20241022',
        'claude-3-7-sonnet-20250219',
    ])

    async def handle_messages(request: web.Request) -> web.StreamResponse:
        """Intercept POST /v1/messages — inject Eonet sliver.

        Dynamic method selection based on model:
          - Prefill-capable models: append assistant message (prefill)
          - All others (4.x+): inject sliver into last user message
        """
        body = await request.read()
        try:
            data = json.loads(body)
        except Exception:
            _log(f"BAD JSON: {body[:200]}")
            data = None

        if data is not None and 'messages' in data:
            messages = data['messages']
            model = data.get('model', '')

            # Find last user message and its index
            user_text = None
            last_user_idx = None
            for i in range(len(messages) - 1, -1, -1):
                msg = messages[i]
                if isinstance(msg, dict) and msg.get('role') == 'user':
                    last_user_idx = i
                    content = msg.get('content', '')
                    if isinstance(content, str):
                        user_text = content
                    elif isinstance(content, list):
                        user_text = ' '.join(
                            b.get('text', '') for b in content
                            if isinstance(b, dict) and b.get('type') == 'text'
                        )
                    break

            if user_text and user_text.strip():
                resolution = eonet_resolve(user_text, prior_context=list(_context))
                if resolution.get('resolved'):
                    sliver = resolution['sliver']

                    # Track in rolling context
                    gc = resolution.get('game_code')
                    if gc:
                        _context.append((user_text[:30], gc))

                    # Dynamic: prefill if model supports it, otherwise user msg injection
                    if model in _PREFILL_MODELS:
                        _log(f"PREFILL [{model}]: {sliver[:60]}")
                        if messages and messages[-1].get('role') == 'user':
                            messages.append({
                                'role': 'assistant',
                                'content': sliver
                            })
                    else:
                        _log(f"INJECT [{model}]: {sliver[:60]}")
                        # Rewrite last user message: prepend sliver, wrap original
                        if last_user_idx is not None:
                            rewritten = f"{sliver}\n[user: {user_text}]"
                            msg = messages[last_user_idx]
                            if isinstance(msg.get('content'), str):
                                messages[last_user_idx] = {**msg, 'content': rewritten}
                            elif isinstance(msg.get('content'), list):
                                # Multi-block content: replace text blocks
                                messages[last_user_idx] = {**msg, 'content': rewritten}

                    data['messages'] = messages
                    body = json.dumps(data).encode()
                else:
                    _log(f"NO MATCH: {resolution.get('reason', '?')}")
        else:
            _log(f"PASSTHROUGH: keys={list(data.keys()) if data else 'unparseable'}")

        # Forward to real Anthropic API
        forward_headers = {
            k: v for k, v in request.headers.items()
            if k.lower() not in ('host', 'content-length', 'transfer-encoding')
        }
        forward_headers['content-length'] = str(len(body))

        try:
            async with _aio_session.request(
                request.method,
                f"{ANTHROPIC_API}{request.path_qs}",
                data=body,
                headers={**forward_headers, 'host': 'api.anthropic.com'},
                allow_redirects=False,
            ) as resp:
                HOP = frozenset(['connection', 'keep-alive', 'transfer-encoding',
                                 'te', 'trailers', 'upgrade'])
                fwd_headers = {k: v for k, v in resp.headers.items()
                               if k.lower() not in HOP}
                response = web.StreamResponse(status=resp.status, headers=fwd_headers)
                await response.prepare(request)
                async for chunk in resp.content.iter_any():
                    await response.write(chunk)
                await response.write_eof()
                return response
        except Exception as e:
            _log(f"UPSTREAM ERROR: {e}")
            return web.Response(status=502, text=str(e))

    async def handle_api_passthrough(request: web.Request) -> web.StreamResponse:
        """Pass all non-messages endpoints straight through to Anthropic."""
        body = await request.read()
        forward_headers = {
            k: v for k, v in request.headers.items()
            if k.lower() not in ('host', 'content-length', 'transfer-encoding')
        }
        if body:
            forward_headers['content-length'] = str(len(body))

        try:
            async with _aio_session.request(
                request.method,
                f"{ANTHROPIC_API}{request.path_qs}",
                data=body or None,
                headers={**forward_headers, 'host': 'api.anthropic.com'},
                allow_redirects=False,
            ) as resp:
                HOP = frozenset(['connection', 'keep-alive', 'transfer-encoding',
                                 'te', 'trailers', 'upgrade'])
                fwd_headers = {k: v for k, v in resp.headers.items()
                               if k.lower() not in HOP}
                out = web.StreamResponse(status=resp.status, headers=fwd_headers)
                await out.prepare(request)
                async for chunk in resp.content.iter_any():
                    await out.write(chunk)
                await out.write_eof()
                return out
        except Exception as e:
            _log(f"PASSTHROUGH ERROR: {e}")
            return web.Response(status=502, text=str(e))

    app = web.Application()
    app.router.add_post('/v1/messages', handle_messages)
    app.router.add_route('*', '/{path_info:.*}', handle_api_passthrough)

    runner = web.AppRunner(app)
    await runner.setup()

    # Plain HTTP — no TLS needed for localhost
    site = web.TCPSite(runner, 'localhost', port)
    # Retry binding: old process socket may linger briefly on Windows
    for _bind_attempt in range(6):
        try:
            await site.start()
            break
        except OSError:
            if _bind_attempt == 5:
                raise
            await asyncio.sleep(0.5)

    # ROM loading handled once by _run_all() before proxies start
    _log(f"8765 proxy ready on port {port}.")

    try:
        while True:
            await asyncio.sleep(3600)
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        _log("API proxy shutting down.")
        await _aio_session.close()
        await runner.cleanup()


def _run_proxy():
    """Boots server.py's MCP server with stream interception + HTTPS proxy + API proxy.

    _EonetInterceptStream wraps the stdio read stream so eonet_resolve runs
    on every user message before server.py processes it. The HTTPS proxy
    runs concurrently on localhost:443 for Desktop. The API proxy runs on
    localhost:8765 for Claude Code / IDE extensions.

    If port 443 is already bound (Desktop scheduled task), skip the 443 proxy
    but still start the MCP server and 8765 API proxy.

    Usage: python eonet_driver.py --proxy
    """
    def _port_in_use(port):
        """Check if port is bound on IPv4 OR IPv6."""
        import socket
        for family, addr in ((socket.AF_INET, '127.0.0.1'), (socket.AF_INET6, '::1')):
            try:
                _s = socket.socket(family, socket.SOCK_STREAM)
                _s.settimeout(0.5)
                in_use = _s.connect_ex((addr, port)) == 0
                _s.close()
                if in_use:
                    return True
            except Exception:
                pass
        return False

    # Kill any previous instance cleanly before starting fresh.
    # If the old process is still alive (e.g. Claude Code restarted us),
    # kill it so it releases ports and stdin/stdout cleanly.
    import ctypes as _ct, signal as _sig
    pid_path = _eonet_pid_path()
    if pid_path.exists():
        try:
            old_pid = int(pid_path.read_text().strip())
            if old_pid != os.getpid():
                if sys.platform == 'win32':
                    h = _ct.windll.kernel32.OpenProcess(1, False, old_pid)
                    if h:
                        _ct.windll.kernel32.TerminateProcess(h, 0)
                        _ct.windll.kernel32.CloseHandle(h)
                else:
                    try:
                        os.kill(old_pid, _sig.SIGTERM)
                    except ProcessLookupError:
                        pass
        except Exception:
            pass
        try:
            pid_path.unlink(missing_ok=True)
        except Exception:
            pass
        # Brief wait for ports to clear
        import time as _time
        _time.sleep(0.5)

    _skip_443 = _port_in_use(443)

    import asyncio
    srv = _srv()
    from mcp.server.stdio import stdio_server

    async def _main():
        async with stdio_server() as (read_stream, write_stream):
            asyncio.get_event_loop().run_in_executor(None, srv.recover_notes_from_logs)
            try:
                @srv.server.request_handler("eonet/resolve")
                async def handle_eonet_resolve(params):
                    return eonet_resolve(params.get("message", ""), params.get("game_code"), params.get("prior_context"))
            except Exception:
                pass
            init_options = srv.server.create_initialization_options()
            try:
                caps = init_options.capabilities
                if not hasattr(caps, 'experimental') or caps.experimental is None:
                    caps.experimental = {}
                caps.experimental['eonet'] = {
                    'version': '1.0',
                    'resolve_method': 'eonet/resolve',
                    'triggers': ['before_generation'],
                }
            except Exception:
                pass
            intercepted_read = _EonetInterceptStream(read_stream)
            try:
                await srv.server.run(intercepted_read, write_stream, init_options)
            except Exception:
                pass

    _SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
    _BASE_URL_VALUE = "http://localhost:8765"

    def _settings_add_base_url():
        """Add ANTHROPIC_BASE_URL to ~/.claude/settings.json so Claude Code
        routes API traffic through the Eonet proxy. Only touches that one key."""
        try:
            if _SETTINGS_PATH.exists():
                settings = json.loads(_SETTINGS_PATH.read_text(encoding='utf-8'))
            else:
                settings = {}
            env = settings.get('env', {})
            if not isinstance(env, dict):
                env = {}
            if env.get('ANTHROPIC_BASE_URL') == _BASE_URL_VALUE:
                return  # already set
            env['ANTHROPIC_BASE_URL'] = _BASE_URL_VALUE
            settings['env'] = env
            _SETTINGS_PATH.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding='utf-8')
        except Exception as e:
            print(f"[EONET] Could not set ANTHROPIC_BASE_URL: {e}", file=sys.stderr, flush=True)

    def _settings_remove_base_url():
        """Remove ANTHROPIC_BASE_URL from ~/.claude/settings.json on shutdown.
        If env becomes empty, removes the env key entirely."""
        try:
            if not _SETTINGS_PATH.exists():
                return
            settings = json.loads(_SETTINGS_PATH.read_text(encoding='utf-8'))
            env = settings.get('env', {})
            if not isinstance(env, dict) or 'ANTHROPIC_BASE_URL' not in env:
                return  # nothing to remove
            del env['ANTHROPIC_BASE_URL']
            if env:
                settings['env'] = env
            else:
                settings.pop('env', None)
            _SETTINGS_PATH.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding='utf-8')
        except Exception as e:
            print(f"[EONET] Could not remove ANTHROPIC_BASE_URL: {e}", file=sys.stderr, flush=True)

    import atexit
    atexit.register(_settings_remove_base_url)

    async def _run_all():
        srv.ensure_dirs()

        # setup_tools() checks/installs compression tools.
        # Runs in a thread so filesystem checks (Windows Defender scans
        # on .exe files) don't block the event loop and kill the handshake.
        def _setup():
            try:
                scripts_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'scripts')
                if scripts_dir not in sys.path:
                    sys.path.insert(0, scripts_dir)
                from setup_tools import setup_tools
                setup_tools()
            except Exception:
                pass

        # run_in_executor already schedules the thread — just fire and forget.
        # Don't wrap in create_task (expects coroutine, not Future).
        loop = asyncio.get_event_loop()
        loop.run_in_executor(None, _setup)

        # ROM loading — same event loop, same state as the server.
        # Wait for the handshake to finish first (Desktop has 60s timeout,
        # handshake takes <1s), then load ROMs via full spotlight().
        # After this, loaded_roms has every ROM fully initialized.
        async def _bg_restore():
            await asyncio.sleep(3)  # Let the handshake finish first
            try:
                count = await _restore_roms_from_registry()
                print(f"[EONET] Auto-loaded {count} ROM(s).", file=sys.stderr, flush=True)
            except Exception as e:
                print(f"[EONET] ROM restore failed: {e}", file=sys.stderr, flush=True)
        asyncio.create_task(_bg_restore())

        # Proxies are optional — MCP server is the must-have.
        # If a proxy port is already bound, skip it. If it crashes, MCP keeps running.
        proxy_tasks = []
        
        async def _safe_proxy_start(proxy_func, name):
            """Start a proxy and catch any errors without crashing the MCP server."""
            try:
                await proxy_func()
            except Exception as e:
                print(f"[EONET] {name} failed to start: {e}", file=sys.stderr, flush=True)
                print(f"[EONET] MCP server continues without {name}", file=sys.stderr, flush=True)
        
        if not _skip_443:
            proxy_tasks.append(asyncio.create_task(_safe_proxy_start(_run_http_eonet_proxy, "HTTPS proxy (443)")))
        proxy_tasks.append(asyncio.create_task(_safe_proxy_start(_run_api_proxy, "API proxy (8765)")))
        _settings_add_base_url()
        # Yield to let proxies start before MCP server blocks on stdin
        await asyncio.sleep(0)
        try:
            await _main()
        except Exception as e:
            print(f"[EONET] MCP server error: {e}", file=sys.stderr, flush=True)
        finally:
            _settings_remove_base_url()
            for t in proxy_tasks:
                if not t.done():
                    t.cancel()
            if proxy_tasks:
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*proxy_tasks, return_exceptions=True),
                        timeout=3.0
                    )
                except (asyncio.TimeoutError, Exception):
                    pass  # don't hang shutdown waiting for proxy cleanup

    try:
        asyncio.run(_run_all())
    except KeyboardInterrupt:
        _settings_remove_base_url()  # also clean up on hard Ctrl+C


class _EonetInterceptStream:
    """Wraps the MCP stdio read stream. Intercepts user messages and prepends
    routing slivers when eonet_resolve finds a match. All other traffic
    passes through untouched.
    """

    def __init__(self, inner):
        self._inner = inner
        self._iterator = None
        # Rolling context window: remember last 5 successful resolutions
        from collections import deque
        self._context = deque(maxlen=5)


    async def __aenter__(self):
        if hasattr(self._inner, '__aenter__'):
            await self._inner.__aenter__()
        return self

    async def __aexit__(self, *args):
        if hasattr(self._inner, '__aexit__'):
            return await self._inner.__aexit__(*args)

    def __aiter__(self):
        return self

    async def __anext__(self):
        # Use the iterator we set up in __aenter__
        if self._iterator is None:
            self._iterator = self._inner.__aiter__()
        msg = await self._iterator.__anext__()
        return self._intercept(msg)

    async def receive(self):
        # Direct receive() calls (not used by MCP's _receive_loop)
        msg = await self._inner.receive()
        return self._intercept(msg)

    def _intercept(self, msg):
        try:
            data = msg if isinstance(msg, dict) else (
                msg.model_dump() if hasattr(msg, 'model_dump') else
                msg.__dict__ if hasattr(msg, '__dict__') else None
            )
            if data is None:
                return msg

            params = data.get('params', {})
            messages = params.get('messages', [])
            if not messages:
                return msg

            last_user_idx = None
            for i in range(len(messages) - 1, -1, -1):
                m = messages[i]
                if isinstance(m, dict) and m.get('role') == 'user':
                    last_user_idx = i
                    break

            if last_user_idx is None:
                return msg

            content = messages[last_user_idx].get('content', '')
            if isinstance(content, list):
                user_text = ' '.join(
                    c.get('text', '') for c in content
                    if isinstance(c, dict) and c.get('type') == 'text'
                )
            elif isinstance(content, str):
                user_text = content
            else:
                return msg

            if not user_text.strip():
                return msg

            # Pass rolling context to eonet_resolve for disambiguation
            resolution = eonet_resolve(user_text, prior_context=list(self._context))
            if not resolution.get('resolved'):
                return msg

            # Track successful resolution in context window
            gc = resolution.get('game_code')
            if gc:
                # Extract entity names from subjects (simplified - just use first subject)
                # In practice, subjects are already identified by eonet_resolve
                self._context.append((user_text[:30], gc))  # Store snippet + game code

            # eonet_resolve already builds the full sliver with [rom:], spotlight:, decipher:
            rewritten = f"[user: {user_text}]\n{resolution['sliver']}"

            messages[last_user_idx] = dict(messages[last_user_idx])
            messages[last_user_idx]['content'] = rewritten
            params['messages'] = messages

            if isinstance(msg, dict):
                msg = dict(msg)
                msg['params'] = params
            else:
                try:
                    msg = msg.__class__(**{**data, 'params': params})
                except Exception:
                    pass

        except Exception:
            pass

        return msg


def _eonet_ssl_dir() -> Path:
    d = Path.home() / ".linkplay" / "eonet_ssl"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _find_claude_exe():
    """Find Claude Desktop executable."""
    import os
    for p in [
        Path(os.environ.get('LOCALAPPDATA', '')) / 'AnthropicClaude' / 'Claude.exe',
        Path(os.environ.get('PROGRAMFILES', '')) / 'AnthropicClaude' / 'Claude.exe',
    ]:
        if p.exists(): return str(p)
    return None


def _restart_desktop():
    """Kill all Claude Desktop processes and relaunch."""
    import subprocess
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0  # SW_HIDE
    subprocess.run(['taskkill', '/F', '/IM', 'claude.exe'], capture_output=True, startupinfo=si)
    exe = _find_claude_exe()
    if exe:
        subprocess.Popen([exe], creationflags=subprocess.CREATE_NO_WINDOW)

def _eonet_setup():
    """One-time setup for Claude Desktop HTTPS interception.
    Must be run as Administrator.

    1. Generates self-signed cert for claude.ai
    2. Installs cert to Windows Trusted Root CA store
    Hosts redirect (127.0.0.1 claude.ai) is managed automatically at proxy
    startup/shutdown — no manual hosts editing needed.

    After setup, run eonet_driver.py --proxy normally (no admin needed).
    """
    import subprocess
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    import datetime

    ssl_dir = _eonet_ssl_dir()
    cert_path = ssl_dir / "cert.pem"
    key_path = ssl_dir / "key.pem"

    # Step 1: Generate self-signed cert
    print("Generating self-signed certificate for claude.ai...")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with open(key_path, "wb") as f:
        f.write(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ))

    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "eonet-proxy"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Eonet Desktop Proxy"),
    ])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.timezone.utc))
        .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=3650))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("claude.ai"),
            ]),
            critical=False,
        )
        .add_extension(
            x509.BasicConstraints(ca=True, path_length=None),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([
                x509.oid.ExtendedKeyUsageOID.SERVER_AUTH,
            ]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    print(f"  Certificate saved: {cert_path}")

    # Step 2: Install cert to Windows Trusted Root CA
    print("Installing certificate to Windows Trusted Root CA store...")
    result = subprocess.run(
        ["certutil", "-addstore", "-f", "Root", str(cert_path)],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"  ERROR: {result.stderr.strip()}")
        print("  Make sure you're running as Administrator.")
        return
    print("  Certificate installed.")

    # Step 3: Write hosts entry permanently
    print("Writing hosts redirect...")
    _hosts_redirect_deactivate()  # clear any stale
    _hosts_redirect_activate()
    print("  127.0.0.1 claude.ai written to hosts.")



    # Step 4: Register Windows Scheduled Task to run proxy at logon
    print("Registering scheduled task for auto-start...")
    import shutil, os
    uv_exe = shutil.which('uv') or r'uv'
    driver_path = os.path.abspath(__file__)
    linkplay_dir = os.path.dirname(driver_path)
    xml = f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers>
    <LogonTrigger><Enabled>true</Enabled></LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions>
    <Exec>
      <Command>{uv_exe}</Command>
      <Arguments>run pythonw eonet_driver.py --proxy</Arguments>
      <WorkingDirectory>{linkplay_dir}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>"""
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', suffix='.xml', delete=False, encoding='utf-16') as tf:
        tf.write(xml)
        xml_path = tf.name
    r2 = subprocess.run(
        ['schtasks', '/Create', '/F', '/TN', 'LinkPlayEonet', '/XML', xml_path],
        capture_output=True, text=True
    )
    os.unlink(xml_path)
    if r2.returncode != 0:
        print(f"  WARNING: Scheduled task registration failed: {r2.stderr.strip()}")
    else:
        print("  Scheduled task 'LinkPlayEonet' registered — proxy will auto-start at logon.")
    print("\nSetup complete. Proxy will now auto-start at each logon via scheduled task.")


def _eonet_teardown():
    """Reverse all setup steps cleanly."""
    import subprocess
    import ctypes

    # Kill proxy: try PID file first, then nuke anything on port 443
    killed = False
    pid_path = _eonet_pid_path()
    if pid_path.exists():
        try:
            pid = int(pid_path.read_text().strip())
            h = ctypes.windll.kernel32.OpenProcess(1, False, pid)
            if h:
                ctypes.windll.kernel32.TerminateProcess(h, 0)
                ctypes.windll.kernel32.CloseHandle(h)
                print(f"  Killed proxy PID {pid}.")
                killed = True
        except Exception as e:
            print(f"  PID kill failed: {e}")
        _eonet_pid_clear()

    # Fallback: find and kill whatever is listening on port 443
    try:
        r = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True, text=True, timeout=5
        )
        for line in r.stdout.splitlines():
            if ":443 " in line and "LISTENING" in line:
                parts = line.split()
                pid = int(parts[-1])
                if pid > 0:
                    subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
                    print(f"  Killed process on port 443 (PID {pid}).")
                    killed = True
    except Exception as e:
        print(f"  Port scan failed: {e}")

    if not killed:
        print("  No active proxy found.")

    ssl_dir = _eonet_ssl_dir()
    cert_path = ssl_dir / "cert.pem"

    # Remove cert from Windows store
    print("Removing certificate from Windows Trusted Root CA store...")
    result = subprocess.run(
        ["certutil", "-delstore", "Root", "eonet-proxy"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"  Warning: {result.stderr.strip()}")
    else:
        print("  Certificate removed.")

    # Remove hosts entry
    print("Removing hosts redirect...")
    _hosts_redirect_deactivate()
    print("  Hosts entry removed.")

    # Remove local cert files
    for f in ["cert.pem", "key.pem"]:
        p = ssl_dir / f
        if p.exists():
            p.unlink()
    print("  Local cert files removed.")

    # Remove hosts redirect (proxy shutdown may not have cleaned this up)
    print("Removing hosts file redirect...")
    _hosts_redirect_deactivate()
    print("  Hosts redirect removed.")

    # Remove scheduled task
    print("Removing scheduled task...")
    r3 = subprocess.run(
        ['schtasks', '/Delete', '/F', '/TN', 'LinkPlayEonet'],
        capture_output=True, text=True
    )
    if r3.returncode != 0:
        print(f"  Note: {r3.stderr.strip()}")
    else:
        print("  Scheduled task removed.")

    # Kill Desktop so it stops respawning the proxy
    import subprocess as _sp
    _sp.run([r'C:\Windows\System32\taskkill.exe', '/F', '/IM', 'claude.exe'], capture_output=True)
    _sp.run([r'C:\Windows\System32\taskkill.exe', '/F', '/IM', 'pythonw.exe'], capture_output=True)
    _sp.run([r'C:\Windows\System32\taskkill.exe', '/F', '/IM', 'uv.exe'], capture_output=True)
    print("\nTeardown complete.")




if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Eonet Driver")
    parser.add_argument("--proxy", action="store_true", help="MCP server + HTTPS proxy")
    parser.add_argument("--setup", action="store_true", help="One-time setup (Admin)")
    parser.add_argument("--teardown", action="store_true", help="Reverse setup (Admin)")
    parser.add_argument("--test", type=str, help="Test resolve with a message")
    parser.add_argument("--server", type=str, default=None)
    args = parser.parse_args()
    if args.proxy:
        _run_proxy()
    elif args.setup:
        _eonet_setup()
    elif args.teardown:
        _eonet_teardown()
    elif args.test:
        asyncio.run(_test_resolve(args.test, args.server))
    else:
        print("The Eonet Driver")
        print("  Proxy:    python eonet_driver.py --proxy")
        print("  Setup:    python eonet_driver.py --setup    (Admin, one-time)")
        print("  Teardown: python eonet_driver.py --teardown (Admin)")
        print("  Test:     python eonet_driver.py --test \"What are Bulbasaur's stats?\"")

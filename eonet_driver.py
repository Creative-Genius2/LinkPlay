#!/usr/bin/env python3
"""
The Eonet Driver — Client-Side Orchestrator

Sits between the user and Claude. Manages two timelines:
  - User's timeline: sees their original messages, unchanged, always
  - Claude's timeline: sees routing slivers + distilled message

The user's message never reaches Claude directly.
It goes to the server (eonet/resolve) first.
The server resolves it against the flipnote (routing database).
The sliver is what Claude receives — from Claude's perspective, that IS what
the user said. The original never arrived. There's nothing to replace.

Two timelines. Neither knows the other's version exists.

Usage:
    from eonet_driver import EonetDriver, EonetMiddleware

    driver = EonetDriver(session)
    await driver.check_capability()

    # After spotlight:
    driver.set_active_rom("IPK", "/abs/path/HeartGold.nds")

    # On each user message:
    rewritten = await driver.process_message("What are Bulbasaur's Pokéathlon stats?")
    # Send `rewritten` to Claude API. User sees their original. Claude sees the sliver.

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
    """The Eonet client-side orchestrator.

    Connects to server.py via MCP. Checks eonet capability.
    On every user message: calls eonet/resolve, builds routing header,
    sends rewritten message to Claude API.

    User sees their original. Claude sees the sliver. Two timelines.
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
            'IRD': 'Black 2', 'IRE': 'White 2',
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
        """Core function. Takes user's raw message, returns what Claude sees.

        If eonet resolves: returns routing header + original message.
        If eonet can't resolve: returns original message unchanged.
        Claude gets either the sliver or the raw message — never both.
        The raw message is what the user typed. It passes through if needed.
        """
        self.turn_index += 1

        if not self.has_active_rom or not self.eonet_supported:
            return user_message

        # Detect which ROMs are relevant from message content
        # If user says "Diamond" and Diamond is loaded, target that ROM first
        msg_lower = user_message.lower()
        game_hints = {
            'diamond': 'ADA', 'pearl': 'APA', 'platinum': 'CPU',
            'heartgold': 'IPK', 'heart gold': 'IPK',
            'soulsilver': 'IPG', 'soul silver': 'IPG',
            'black 2': 'IRD', 'white 2': 'IRE',  # check before "black"/"white"
            'black': 'IRB', 'white': 'IRA',
        }
        target_gcs = []
        # Longer hints first so "black 2" matches before "black"
        for hint in sorted(game_hints.keys(), key=lambda x: -len(x)):
            gc = game_hints[hint]
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

        # Two-part message: routing header + original
        # Header = WHERE. Original = WHAT the user wants.
        # Claude sees both. User sees only their original in the UI.
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
        # Last user message was rewritten with routing sliver.
        # original_messages stores what the user actually typed for UI display.
    """

    def __init__(self, anthropic_client, driver: EonetDriver):
        self.client = anthropic_client
        self.driver = driver
        # Original messages — Claude's version is permanent in the API history.
        # These are only for UI display.
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


# ============================================================
# Eonet ICR Engine — Iterative Cross-Referencing
# ============================================================
# Auto-discovery engine. Moved from server.py.
# server.py imports _build_eonet and eonet_resolve from here.
# Server state accessed via _srv() to avoid circular imports.


def _srv():
    """Lazy import of server module. Avoids circular import at module load time."""
    if 'server' in sys.modules:
        return sys.modules['server']
    scripts_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'scripts')
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import server
    return server


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


def _icr_get_tables():
    """Return all decoded text tables. The seed for everything that follows."""
    srv = _srv()
    return {n: t for n, t in srv.text_tables.items()
            if isinstance(t, list) and len(t) > 2}


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


def _icr_read_narc(narc, tables):
    """Read a NARC's structure AND all its values. Two steps.

    Step 1 — Confirm structure: probe a handful of files to find which offsets
    are constant fields. Step 2 — Collect ALL values at confirmed offsets.

    Returns structure dict with 'edges' (all unique values per field) or None.
    """
    PROBE_COUNT = 16

    fc = len(narc.files)
    if fc == 0:
        return None

    non_empty = [i for i in range(fc) if len(narc.files[i]) > 0]
    if len(non_empty) < 2:
        return None

    probe = non_empty[:PROBE_COUNT] if len(non_empty) > PROBE_COUNT else non_empty
    probe_hits = [_icr_check_file(narc.files[i], tables) for i in probe]
    n = len(probe_hits)

    all_offsets = set()
    for h in probe_hits:
        all_offsets.update(h.keys())

    confirmed = {}
    for off in sorted(all_offsets, key=lambda x: (isinstance(x, str), x if isinstance(x, str) else 0)):
        counts = {}
        for h in probe_hits:
            if off in h:
                for tname in h[off]:
                    counts[tname] = counts.get(tname, 0) + 1
        if not counts:
            continue
        best = max(counts, key=counts.get)
        threshold = max(int(n * 0.95), n - 2)
        if counts[best] >= threshold:
            confirmed[off] = best

    if not confirmed:
        return None

    edges = {}
    for off, tname in confirmed.items():
        tbl = tables.get(tname, [])
        vals = set()
        for i in non_empty:
            data = narc.files[i]
            if ':u8' in str(off):
                real_off = int(str(off).split(':')[0])
                if real_off < len(data):
                    vals.add(data[real_off])
            else:
                if isinstance(off, int) and off + 2 <= len(data):
                    val = struct.unpack_from('<H', data, off)[0]
                    if val < len(tbl):
                        vals.add(val)
        edges[off] = vals

    index_table = None
    best_diff = float('inf')
    for tname, tbl in tables.items():
        tbl_len = len(tbl)
        diff = min(abs(fc - tbl_len), abs(fc - (tbl_len + 1)))
        tolerance = max(10, int(tbl_len * 0.05))
        if diff <= tolerance and diff < best_diff:
            index_table = tname
            best_diff = diff

    index_offset = 0
    if index_table:
        tbl_len = len(tables[index_table])
        if fc == tbl_len:
            index_offset = 1

    all_sizes = set(len(narc.files[i]) for i in non_empty)
    uniform = len(all_sizes) == 1

    return {
        'file_count': fc,
        'file_size': next(iter(all_sizes)) if uniform else 0,
        'uniform': uniform,
        'fields': confirmed,
        'edges': edges,
        'index_table': index_table,
        'index_offset': index_offset,
        'references': set(v for v in confirmed.values()),
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
        if 'species' in refs:
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


def _icr_label_file(data, structure, file_idx, tables):
    """Label one file from what it contains. The data names itself."""
    idx_tbl = structure.get('index_table')
    index_offset = structure.get('index_offset', 0)

    if idx_tbl:
        tbl = tables.get(idx_tbl, [])
        lookup = file_idx + index_offset
        if lookup < len(tbl):
            name = tbl[lookup]
            if isinstance(name, str) and name.strip():
                return name.strip()

    if not data:
        return f"#{file_idx}"

    parts = []
    seen = set()
    for off in sorted(k for k in structure.get('fields', {}) if isinstance(k, int)):
        tname = structure['fields'][off]
        if off + 2 > len(data):
            continue
        val = struct.unpack_from('<H', data, off)[0]
        if val == 0:
            continue
        tbl = tables.get(tname, [])
        if val < len(tbl):
            name = tbl[val]
            if isinstance(name, str) and len(name.strip()) >= 3 and name.strip() not in seen:
                parts.append(name.strip())
                seen.add(name.strip())

    return ', '.join(parts[:6]) if parts else f"#{file_idx}"


def _icr_scan_arm(data, tables):
    """Scan ARM binary for text table sequences and f100 Unicode character table.

    Returns {'sequences': [...], 'f100': {'offset': int, 'count': int} or None}
    """
    sequences = []
    for tname, tbl in tables.items():
        tlen = len(tbl)
        if tlen < 20:
            continue
        run_start = None
        run_len = 0
        for off in range(0, len(data) - 1, 2):
            val = struct.unpack_from('<H', data, off)[0]
            if 0 < val < tlen:
                if run_start is None:
                    run_start = off
                run_len += 1
            else:
                if run_len >= 8:
                    sample = [struct.unpack_from('<H', data, run_start + i * 2)[0]
                              for i in range(min(5, run_len))]
                    sequences.append({'table': tname, 'offset': run_start,
                                      'count': run_len, 'sample': sample})
                run_start = None
                run_len = 0
        if run_len >= 8:
            sample = [struct.unpack_from('<H', data, run_start + i * 2)[0]
                      for i in range(min(5, run_len))]
            sequences.append({'table': tname, 'offset': run_start,
                              'count': run_len, 'sample': sample})

    UNICODE_LO, UNICODE_HI = 0x0020, 0x02FF
    ASCII_ALPHA_LO, ASCII_ALPHA_HI = 0x0041, 0x007A
    MIN_RUN = 64
    MIN_ALPHA = 20

    best_f100 = None
    run_start = None
    run_len = 0
    alpha_count = 0

    for off in range(0, len(data) - 1, 2):
        val = struct.unpack_from('<H', data, off)[0]
        in_range = (UNICODE_LO <= val <= UNICODE_HI) or val == 0xFFFF
        if in_range:
            if run_start is None:
                run_start = off
                alpha_count = 0
            run_len += 1
            if ASCII_ALPHA_LO <= val <= ASCII_ALPHA_HI:
                alpha_count += 1
        else:
            if run_len >= MIN_RUN and alpha_count >= MIN_ALPHA:
                if best_f100 is None or run_len > best_f100['count']:
                    best_f100 = {'offset': run_start, 'count': run_len}
            run_start = None
            run_len = 0
            alpha_count = 0

    if run_len >= MIN_RUN and alpha_count >= MIN_ALPHA:
        if best_f100 is None or run_len > best_f100['count']:
            best_f100 = {'offset': run_start, 'count': run_len}

    if best_f100:
        f_start = best_f100['offset']
        f_end = f_start + best_f100['count'] * 2
        sequences = [s for s in sequences
                     if not (s['offset'] < f_end and s['offset'] + s['count'] * 2 > f_start)]

    return {'sequences': sequences, 'f100': best_f100}


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
    """Write ICR labels to the in-memory ICR cache, not the flipnote."""
    srv = _srv()
    if not srv.current_rom:
        return False
    gc = srv.current_rom['header']['game_code']
    cache = _icr_cache.setdefault(gc, {})
    try:
        cache[narc_path] = desc
        for idx, label in file_labels.items():
            cache[f"{narc_path}:{idx:03d}"] = label
        return True
    except Exception:
        return False


def _build_eonet():
    """ICR: Map the entire ROM. Recursive BFS — read, follow, repeat.

    Called from server.py spotlight() after text tables are bootstrapped.
    Builds the routing database (flipnote) invisibly — result never surfaces
    in the spotlight tool response.
    """
    srv = _srv()
    gc = srv.current_rom['header']['game_code']
    tables = _icr_get_tables()
    if not tables:
        return 0, 0

    all_narcs = {}
    for narc_path, narc in _walk_all_narcs():
        all_narcs[narc_path] = narc

    narc_structures = {}
    visited = set()

    queue = list(all_narcs.keys())
    def _priority(path):
        fc = len(all_narcs[path].files)
        best = float('inf')
        for tbl in tables.values():
            diff = min(abs(fc - len(tbl)), abs(fc - (len(tbl) + 1)))
            if diff < best:
                best = diff
        return best
    queue.sort(key=_priority)

    while queue:
        path = queue.pop(0)
        if path in visited:
            continue
        visited.add(path)

        narc = all_narcs[path]
        s = _icr_read_narc(narc, tables)
        if not s:
            continue

        narc_structures[path] = s

        edge_tables = s.get('references', set())
        edge_values = s.get('edges', {})

        for other_path in list(all_narcs.keys()):
            if other_path in visited:
                continue
            other_narc = all_narcs[other_path]
            other_fc = len(other_narc.files)

            connected = False
            for tname in edge_tables:
                tbl = tables.get(tname, [])
                diff = min(abs(other_fc - len(tbl)), abs(other_fc - (len(tbl) + 1)))
                if diff <= max(10, int(len(tbl) * 0.05)):
                    connected = True
                    break

            if not connected and other_fc > 1:
                peek = other_narc.files[min(1, other_fc - 1)]
                if len(peek) >= 4:
                    for off, vals in edge_values.items():
                        if isinstance(off, int) and off + 2 <= len(peek):
                            pval = struct.unpack_from('<H', peek, off)[0]
                            if pval in vals and pval > 0:
                                connected = True
                                break

            if connected:
                if other_path in queue:
                    queue.remove(other_path)
                queue.insert(0, other_path)

    arm_results = {}
    f100_offset = None
    rom = srv.current_rom.get('rom')
    if rom:
        for arm_name in ('arm9', 'arm7'):
            arm_data = srv.current_rom.get(f'{arm_name}_data') or getattr(rom, arm_name, None)
            if arm_data and len(arm_data) > 100:
                result = _icr_scan_arm(arm_data, tables)
                arm_results[arm_name] = result
                if arm_name == 'arm9' and result.get('f100'):
                    f100_offset = result['f100']['offset']

    table_to_narcs, cross_refs = _icr_cross_reference(narc_structures)

    additions = {}
    for path, others in cross_refs.items():
        if path not in narc_structures:
            continue
        shared = set()
        for other_tables in others.values():
            shared.update(other_tables)
        additions[path] = shared
    for path, shared in additions.items():
        narc_structures[path]['references'].update(shared)

    labels_dict = {}
    index_entries = []

    for narc_path, structure in narc_structures.items():
        narc = all_narcs.get(narc_path)
        if narc is None:
            continue

        desc = _icr_narc_desc(structure, narc_path)
        if desc is None:
            continue

        file_labels = {}

        for i in range(len(narc.files)):
            raw = _icr_label_file(narc.files[i], structure, i, tables)
            file_labels[i] = f"{raw} ({desc})"

        _eonet_try_write_flipnote(narc_path, desc, file_labels)

        labels_dict[narc_path] = {
            'desc': desc,
            'labels': file_labels,
            'fields': {str(k): v for k, v in structure['fields'].items()},
            'index_table': structure.get('index_table'),
            'index_offset': structure.get('index_offset', 0),
            'cross_refs': [p for p in cross_refs.get(narc_path, {})],
            'meta': {
                'file_count': structure['file_count'],
                'file_size': structure['file_size'],
                'uniform': structure['uniform'],
            },
        }

        for idx, label in file_labels.items():
            index_entries.append({
                'name': label.lower(),
                'path': f"{narc_path}:{idx:03d}",
                'idx': idx,
                'label': label,
                'narc': narc_path,
                'desc': desc,
            })

        idx_tbl = structure.get('index_table')
        refs = structure.get('references', set())
        fs = structure.get('file_size', 0)
        if idx_tbl == 'species' and structure['uniform']:
            if fs == 20:
                srv.narc_roles[narc_path] = 'pokeathlon_performance'
            elif 'moves' in refs:
                srv.narc_roles[narc_path] = 'battle_facility_pokemon'
            else:
                srv.narc_roles[narc_path] = 'personal'
        elif idx_tbl == 'species' and not structure['uniform']:
            srv.narc_roles[narc_path] = 'learnsets'
        elif idx_tbl == 'moves':
            srv.narc_roles[narc_path] = 'move_data'
        elif idx_tbl == 'items':
            srv.narc_roles[narc_path] = 'items'
        elif idx_tbl == 'trainer_names':
            srv.narc_roles[narc_path] = 'trpoke' if 'species' in refs else 'trdata'
        elif idx_tbl == 'location_names':
            srv.narc_roles[narc_path] = 'encounters'
        elif refs:
            srv.narc_roles[narc_path] = '+'.join(sorted(str(r) for r in refs))

    srv.eonet_labels[gc] = labels_dict
    srv.eonet_index[gc] = index_entries

    if arm_results:
        srv.eonet_labels[gc]['_arm'] = arm_results
    if f100_offset is not None:
        srv.eonet_labels[gc]['_f100_arm9_offset'] = f100_offset

    srv.eonet_labels[gc]['_cross_refs'] = {
        tname: len(paths) for tname, paths in table_to_narcs.items()
    }

    _flipnote_save()
    _icr_cache_save(gc)

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

# All known DS Pokemon game codes
_GAME_HINTS = {
    'diamond': 'ADA', 'pearl': 'APA', 'platinum': 'CPU',
    'heartgold': 'IPK', 'heart gold': 'IPK',
    'soulsilver': 'IPG', 'soul silver': 'IPG',
    'black 2': 'IRD', 'white 2': 'IRE',
    'black': 'IRB', 'white': 'IRA',
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
        'IRD': ['black 2', 'black2'], 'IRE': ['white 2', 'white2'],
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

    return [gc for gc in needed if gc in _discovered_roms]



def _resolve_gc(gc,msg,subjects,preferred_refs):
    srv=_srv()
    if gc not in srv.eonet_index or not srv.eonet_index[gc]: return None
    fn=[]
    for s in subjects:
        r,ok=_eonet_search_flipnote(gc,s["name"].lower())
        if ok: fn.extend(r)
    paths=[]
    if fn:
        for k,l in fn: paths.append({"path":k,"label":l,"narc":k.split(":")[0],"name":l.lower()})
    else:
        for s in subjects:
            for e in srv.eonet_index[gc]:
                if s["name"].lower() in e["name"]: paths.append(e)
    import sys;print(f"[EONET_DBG] gc={gc} subjects={[s['name'] for s in subjects]} paths={len(paths)} sample={str(srv.eonet_index.get(gc,[])[0]) if srv.eonet_index.get(gc) else 'empty'}",file=sys.stderr,flush=True)
    if not paths: return None
    seen=set();unique=[]
    for p in paths:
        if p["path"] not in seen: seen.add(p["path"]);unique.append(p)
    return unique[:5]
def eonet_resolve(message: str, game_code: str = None) -> dict:
    """Resolve user message → routing sliver. NOT a tool. Called by the driver.

    No vector DB. No embeddings. No NLP. String matching against in-memory
    text tables + flipnote entries. Sub-100ms.
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
        if not gcs: return {"resolved":False,"reason":"no ROM"}
    msg = msg_lower

    subjects = []
    for tname in ('species', 'moves', 'items', 'trainer_names', 'location_names'):
        tbl = srv.text_tables.get(tname, [])
        for idx, entry in enumerate(tbl):
            if isinstance(entry, str):
                name = entry.strip()
                if len(name) >= 3 and name.lower() in msg:
                    subjects.append({'table': tname, 'index': idx, 'name': name})

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

    preferred_refs = set()
    kw_map = {
        'stats': 'Personal Data', 'base stats': 'Personal Data', 'personal': 'Personal Data',
        'learn': 'Move Data', 'learnset': 'Learnsets', 'moveset': 'Learnsets',
        'evolve': 'Personal Data', 'evolution': 'Learnsets',
        'trainer': 'Trainer Data', 'team': 'Trainer Pokemon',
        'gym': 'Trainer Data', 'elite': 'Trainer Data', 'champion': 'Trainer Data',
        'encounter': 'Encounters', 'wild': 'Encounters',
        'route': 'Encounters', 'area': 'Encounters', 'cave': 'Encounters',
        u'pok\u00e9athlon': 'Pok\u00e9athlon', 'pokeathlon': 'Pokéathlon',
        'performance': 'Pokéathlon',
        'item': 'Item Data', 'price': 'Item Data',
        'move data': 'Move Data', 'power': 'Move Data', 'accuracy': 'Move Data',
        'battle tower': 'Battle Facility Pokemon', 'facility': 'Battle Facility Pokemon',
        'subway': 'Battle Facility Pokemon',
    }
    for kw, desc_hint in kw_map.items():
        if kw in msg:
            preferred_refs.add(desc_hint)

    rom_results = {}
    for _gc in gcs:
        hits=_resolve_gc(_gc,msg,subjects,preferred_refs)
        if hits: rom_results[_gc]=hits
    if not rom_results:
        return {"resolved":False,"reason":"no matching paths"}
    blocks=[]
    for _gc,hits in rom_results.items():
        dc=", ".join(f"{p['path']} - {p['label']}" for p in hits)
        rp=_discovered_roms.get(_gc,'')
        t=srv.loaded_roms.get(_gc,{}).get('title',_gc)
        sp=f"  spotlight: [{rp}]\n" if rp else ""
        blocks.append(f"[rom: {t} ({_gc})]\n{sp}  decipher: [{dc}]")
    sliver=blocks[0] if len(blocks)==1 else "\n".join(blocks)
    return {"resolved":True,"sliver":sliver,"roms":list(rom_results.keys())}

# ============================================================
# Standalone test mode
# ============================================================
async def _test_resolve(message: str, server_script: str = None):
    """Test eonet resolution without full MCP setup.

    Imports server.py directly, opens a ROM, and tests the full pipeline.
    """
    import sys, os, glob

    if server_script is None:
        server_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "server.py")

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
    """Add claude.ai redirect entry to the hosts file."""
    hosts_path = Path(r"C:\Windows\System32\drivers\etc\hosts")
    try:
        content = hosts_path.read_text(encoding='utf-8')
        if "claude.ai" not in content:
            with open(hosts_path, 'a', encoding='utf-8') as f:
                f.write('\n127.0.0.1 claude.ai  # eonet\n')
    except Exception as e:
        print(f"[EONET] Warning: could not activate hosts redirect: {e}", file=sys.stderr)


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

    # Resolve BEFORE hosts redirect — OS resolver gives real IPs
    _CLOUDFLARE_IPS = ["104.26.10.243", "162.159.135.233", "162.247.243.29"]
    _initial_ips = _dns_lookup_all(CLAUDE_HOST) or _CLOUDFLARE_IPS
    _dns_cache = {CLAUDE_HOST: _initial_ips}

    async def _dns_refresh_loop():
        while True:
            await asyncio.sleep(60)
            ips = _dns_lookup_all(CLAUDE_HOST)
            if ips:
                _dns_cache[CLAUDE_HOST] = ips

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

    _SILENT = frozenset(['/healthcheck', '/api/organizations', '/api/bootstrap'])

    from curl_cffi.requests import AsyncSession
    from curl_cffi import CurlOpt
    _session = AsyncSession(impersonate="chrome124", verify=False)

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

            # Auto-restore last ROM if none open
            srv = _srv()
            if not srv.current_rom and not srv.loaded_roms:
                try:
                    last_rom_file = Path.home() / ".linkplay" / "last_rom.json"
                    if last_rom_file.exists():
                        with open(last_rom_file, 'r', encoding='utf-8') as f:
                            last = json.load(f)
                        rom_path = last.get('path')
                        if rom_path and Path(rom_path).exists():
                            await srv.spotlight(rom_path)
                except Exception as _e:
                    _log(f"auto-spotlight last_rom failed: {_e}")
                if not srv.current_rom and not srv.loaded_roms:
                    try:
                        import glob
                        scan_dirs=[Path.home()/"Downloads",Path.home()/"Desktop",Path(r"C:/Users/prado/Downloads/LinkPlay/roms")]
                        nds=[p for sd in scan_dirs if sd.exists() for p in sd.rglob("*.nds")]
                        for nds_path in nds:
                            await srv.spotlight(str(nds_path))
                    except Exception as _e2:
                        _log(f"auto-spotlight scan failed: {_e2}")

            _log(f"resolve: current={bool(srv.current_rom)} loaded={list(srv.loaded_roms.keys())} index={list(srv.eonet_index.keys())} tables={list(srv.text_tables.keys())}")
            _log(f"subjects test: {[s['name'] for s in __import__('eonet_driver',fromlist=['_srv']).eonet_resolve.__code__.co_consts]}")
            resolution = eonet_resolve(user_text)
            if resolution.get('resolved'):
                sliver = resolution['sliver']
                _log(f"COMPLETION injecting sliver: {sliver[:80]}")
                data['prompt'] = f"{sliver}\n[user: {user_text}]\n"
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
        """Pass all other endpoints straight through to the correct upstream host."""
        path_str = request.path
        silent = any(path_str.startswith(s) for s in _SILENT)
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
                body2 = await resp.read()
                return web.Response(status=resp.status, body=body2, headers=fwd2)
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

    # Clean stale entries from any previous crash, then activate fresh
    _log("Proxy ready. Activating hosts redirect...")
    asyncio.ensure_future(_dns_refresh_loop())
    import atexit
    _eonet_pid_write()
    _log("Proxy ready.")
    atexit.register(_eonet_pid_clear)

    # Run until cancelled — finally covers clean shutdown and handled exceptions
    try:
        while True:
            await asyncio.sleep(3600)
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        _log("Proxy shutting down.")
        _eonet_pid_clear()
        await runner.cleanup()


def _run_proxy():
    """Proxy mode: MCP server with Eonet preprocess primitive built in.

    Boots server.py's MCP server (all 15 tools), declares the 'eonet'
    preprocess capability during init, and implements message interception
    directly — no waiting for Claude Desktop to support it.

    The proxy intercepts every user message before the MCP server sees it:
      1. Extracts the last user message text
      2. Calls eonet/resolve internally (plain Python, no round-trip)
      3. If resolved: rewrites with the sliver prepended
      4. Forwards the (possibly rewritten) message onward

    Also starts the HTTPS proxy on localhost:443 (Method B) which intercepts
    claude.ai traffic via hosts redirect. Requires --setup to have been run
    as Administrator first (installs cert to trust store).

    Claude sees the sliver as if the user typed it.
    Two timelines. Neither knows the other exists.

    Usage: python eonet_driver.py --proxy
    """
    import asyncio
    srv = _srv()
    from mcp.server.stdio import stdio_server

    async def _main():
        async with stdio_server() as (read_stream, write_stream):
            import os
            scripts_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'scripts')
            if scripts_dir not in sys.path:
                sys.path.insert(0, scripts_dir)
            from setup_tools import setup_tools
            setup_tools()
            srv.ensure_dirs()
            try:
                srv.recover_notes_from_logs()
            except Exception:
                pass
            try:
                @srv.server.request_handler("eonet/resolve")
                async def handle_eonet_resolve(params):
                    return eonet_resolve(params.get("message", ""), params.get("game_code"))
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

    async def _run_all():
        http_task = asyncio.ensure_future(_run_http_eonet_proxy())
        try:
            await _main()
        except Exception:
            pass
        await http_task

    asyncio.run(_run_all())


class _EonetInterceptStream:
    """Wraps the MCP read stream. Intercepts user messages, rewrites with slivers.

    Transparent to all other JSON-RPC traffic. Only user message content
    is touched. The MCP server never knows. Claude never knows.
    """

    def __init__(self, inner):
        self._inner = inner

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
        msg = await self._inner.__anext__()
        return self._intercept(msg)

    async def receive(self):
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

            resolution = eonet_resolve(user_text)
            if not resolution.get('resolved'):
                return msg

            # Build new per-ROM block format
            srv = _srv()
            gc = resolution.get('gc') or (srv.current_rom['header']['game_code'] if srv.current_rom else None)
            inner = resolution['sliver']
            if inner.startswith('[routing: ') and inner.endswith(']'):
                inner = inner[len('[routing: '):-1]
            from pathlib import Path as _Path
            rom_path = (
                (srv.current_rom['path'] if srv.current_rom else None)
                or (srv.loaded_roms.get(gc, {}).get('current_rom') or {}).get('path')
                or _discovered_roms.get(gc)
            ) if gc else None
            game_names = {
                'ADA': 'Diamond', 'APA': 'Pearl', 'CPU': 'Platinum',
                'IPK': 'HeartGold', 'IPG': 'SoulSilver',
                'IRB': 'Black', 'IRA': 'White',
                'IRD': 'Black 2', 'IRE': 'White 2',
            }
            name = game_names.get(gc, gc) if gc else 'ROM'
            lines = [f"[rom: {name} ({gc})]"] if gc else []
            if rom_path:
                lines.append(f"  spotlight: [{rom_path}]")
            lines.append(f"  decipher: [{inner}]")
            rewritten = '\n'.join(lines) + f"\n[user: {user_text}]"

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

    print("\nSetup complete. Run 'python eonet_driver.py --proxy' to start.")
    print("Desktop will connect once proxy is running.")


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

    print("\nTeardown complete. Restart Claude Desktop.")




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

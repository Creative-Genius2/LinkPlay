#!/usr/bin/env python3
"""
LinkPlay MCP Server
ROM exploration and hacking for Nintendo DS/GBA/GBC/GB through Claude's interface.
"""

import json
import os
import re
import subprocess
import struct
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Optional
from mcp.server import Server
from mcp.types import Tool, TextContent

# Add scripts directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))
# Add parent directory so eonet_driver.py is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

# Ensure eonet_driver._srv() finds this module even when running as __main__
sys.modules["server"] = sys.modules[__name__]

# Import setup_tools but call inside main() after stdio is captured
from setup_tools import setup_tools, get_tool_path

# Eonet ICR engine — auto-discovery, flipnote labeling, query resolution
from eonet_driver import _build_eonet, eonet_resolve, _auto_enc_loc, resolve_chain

# Required: ndspy for DS ROM handling
import ndspy.rom
import ndspy.narc
import ndspy.fnt
import ndspy.lz10

# ARM disassembler for probe reads="arm"/"thumb"
try:
    import capstone
    _cs_arm = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_ARM | capstone.CS_MODE_LITTLE_ENDIAN)
    _cs_thumb = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB | capstone.CS_MODE_LITTLE_ENDIAN)
    _cs_arm.detail = False
    _cs_thumb.detail = False
except ImportError:
    _cs_arm = _cs_thumb = None

# ============ Generation-specific modules ============
from Generations.gen1_rgby import (
    _GEN1_CHARMAP_EN, _GEN1_CHARMAP_JP, _KATAKANA_JP,
    _GEN1_JP_H2K, _gen1_jp_normalize,
    _GEN1_JP_DISASM, _GEN1_JP_REF, _GEN1_JP_NORM_TO_ORIG,
    _GEN1_EOS, _JP_SPECIES_GEN1, _split_jp_species,
    _scan_gen1_trainer_classes_jp, _scan_gen1_trainer_classes_en,
    _scan_gen1_items, _scan_gen1_moves_jp,
    _scan_gen1_species_varlen, _scan_gen1_species,
    decode_gen1_encounters,
    _discover_gen1_tables,
    _GEN1_TYPE_NAMES, _GEN1_GROWTH_RATES,
    _gen1_resolve_const, _extract_gen12_evo_learnset, decode_gen12_trainer_class,
)
from Generations.gen2_gsc import (
    _GEN2_CHARMAP_EN, _GEN2_EOS,
    _scan_gen2_trainer_classes_en,
    decode_gen2_encounters,
)
from Generations.gen3_rse import (
    _GEN3_CHARMAP_EN, _GEN3_EOS,
    _scan_gen3_abilities, _scan_gen3_species,
    _scan_gen3_trainer_names, _scan_gen3_items,
    decode_gen3_trainer,
    _discover_gen3_tables,
)
from Generations.gen4_dppt_hgss import (
    _GEN4_COMMON, _GEN4_DP_COMMON, _GEN4_PLATINUM_OVERRIDES, _GEN4_HGSS,
    _GEN4_HIRAGANA, _GEN4_KATAKANA, _GEN4_FULLWIDTH_SYMBOLS, _GEN4_SPECIAL,
    _get_gen4_char, decode_gen4_text,
    AI_FLAGS_GEN4, TRPOKE_FORMATS_G4,
    _decode_encounters_dpp, _decode_encounters_hgss,
    POKEATHLON_STATS, _build_pokeathlon_form_map, _POKEATHLON_FORM_MAP,
    decode_pokeathlon_performance,
    _GEN4_FLIPNOTE_PAIRS, _GEN4_GAME_INFO,
    _GEN4_TRAINER_LOCATIONS, _GEN4_CLASS_LOCATIONS,
    _GEN4_TM_SEARCH, MOVE_CATEGORIES_G4,
)
from Generations.gen1_rb_y import _GEN1_FLIPNOTE_PAIRS, _GEN1_GAME_INFO, TABLE_FINGERPRINTS_JPN
from Generations.gen2_gs_c import _GEN2_FLIPNOTE_PAIRS, _GEN2_GAME_INFO
from Generations.gen3_rse_frlg import _GEN3_FLIPNOTE_PAIRS, _GEN3_GAME_INFO, TABLE_FINGERPRINTS_GEN3
from Generations.gen6_xy_oras import _GEN6_ORAS, _GEN6_FLIPNOTE_PAIRS, _GEN6_GAME_INFO
from Generations.gen7_sm_usum import _GEN7_USUM, _GEN7_SM, _GEN7_FLIPNOTE_PAIRS, _GEN7_GAME_INFO
from Generations.gen5_bw import (
    _GEN5_CHARMAP, _derive_gen5_mult, decode_gen5_text,
    _GEN5_B2W2, _GEN5_BW1,
    _BW1_ENCOUNTERS, _B2W2_ENCOUNTERS, _B2W2_PWT,
    _B2W2_SUBWAY, _BW1_SUBWAY,
    AI_FLAGS_GEN5, TRPOKE_FORMATS_G5,
    _BW2_CHALLENGE_FILE_DELTA, get_bw2_challenge_delta,
    _decode_encounters_gen5,
    _role_path, _resolve_pwt_trainer_name, _build_pwt_maps,
    _PWT_POOL_ROLES, _PWT_ROLE_CHAINS, _PWT_ROSTER_POOLS,
    decode_pwt, _resolve_pwt_pool_entry, decode_pwt_roster,
    decode_pwt_trainer_config, _scan_pwt_tournaments,
    _resolve_pwt_text, decode_pwt_tournament_def,
    pwt_name_to_entries, pwt_entry_tournaments,
    _GEN5_FLIPNOTE_PAIRS, _GEN5_GAME_INFO,
    _GEN5_TRAINER_LOCATIONS, _GEN5_CLASS_LOCATIONS,
    _GEN5_TM_SEARCH, MOVE_CATEGORIES_G5, _FORM_NAMES,
)
from xoleon import (
    read_3ds_header, open_3ds_romfs, read_garc_sub, read_garc_all,
)


server = Server("linkplay")

# State
current_rom = None
_user_active_gc = None   # Game code of the ROM the user most recently spotlighted (not changed by BFS)
current_flipnote = None
text_tables = {}  # Populated on open_rom: {file_index: [strings], 'species': [strings], ...}
text_narc = None   # Kept in memory for lazy lookups
text_mult = None   # Derived once from species file (Gen V only)
text_gen = None    # 4 or 5, set during bootstrap
narc_roles = {}    # Reverse map: narc_path -> role (e.g. 'a/0/9/2' -> 'trpoke')
tm_table = []      # Indexed by bit position: [(label, move_id), ...] — populated at ROM open
loaded_roms = {}   # game_code -> saved state for multi-ROM support
_narc_cache = {}   # (game_code, narc_path) -> parsed ndspy.narc.NARC
_rom_restore_in_progress = False  # Guards against concurrent restore runs
_rom_restore_done = False         # Set True after first restore attempt completes
_restore_task = None               # Background task handle for ROM restore
_startup_log = []                  # Collects restore/BFS messages for the model to see
eonet_labels = {}  # game_code -> {narc_path: {'role': str, 'labels': {idx: 'Name (Role)'}}}
eonet_index = {}   # game_code -> [{name_lower: str, path: str, role: str, idx: int}, ...]


def _rom_is_fully_loaded(gc: str) -> bool:
    """True if this game code has a live ndspy ROM object (not just registry metadata)."""
    # Check the active ROM first (not yet saved to loaded_roms)
    if current_rom and current_rom.get('header', {}).get('game_code') == gc:
        return current_rom.get('rom') is not None
    if gc not in loaded_roms:
        return False
    state = loaded_roms[gc]
    rom_obj = state.get('current_rom') if isinstance(state, dict) else None
    if rom_obj is None:
        return False
    return rom_obj.get('rom') is not None


async def _do_pending_restore():
    """Load any ROMs from last_rom.json that aren't fully loaded yet. Safe to call multiple times."""
    global _rom_restore_in_progress, _rom_restore_done
    if _rom_restore_done or _rom_restore_in_progress:
        return
    _rom_restore_in_progress = True
    try:
        reg_path = Path.home() / ".linkplay" / "last_rom.json"
        if not reg_path.exists():
            return
        registry = json.loads(reg_path.read_text(encoding='utf-8'))
        if 'game_code' in registry:
            registry = {registry['game_code']: registry['path']}

        async def _restore_one(gc, rom_path):
            if _rom_is_fully_loaded(gc):
                return
            if not rom_path or not Path(rom_path).exists():
                print(f"[linkplay] Registry ROM not found, skipping: {gc} → {rom_path}", file=sys.stderr, flush=True)
                return
            try:
                await spotlight(rom_path)
                print(f"[linkplay] Auto-restored ROM: {gc}", file=sys.stderr, flush=True)
                _startup_log.append(f"Restored: {gc}")
            except Exception as e:
                print(f"[linkplay] Failed to restore {gc}: {e}", file=sys.stderr, flush=True)
                _startup_log.append(f"FAILED to restore {gc}: {e}")

        restored = []
        for gc, path in registry.items():
            if not _rom_is_fully_loaded(gc):
                await _restore_one(gc, path)
                restored.append(gc)

        # Run BFS/ICR for each restored NDS ROM in sequence — never concurrent.
        # GBA/GB/GBC have no NARCs — BFS does nothing for them, skip entirely.
        import asyncio as _asyncio
        for gc in restored:
            rom_type = loaded_roms.get(gc, {}).get('current_rom', {}).get('type', 'nds')
            if rom_type != 'nds':
                _startup_log.append(f"Skipped BFS for {gc} (non-NDS)")
                continue
            try:
                _save_active_state()
                if gc in loaded_roms:
                    _restore_state(gc)
                loop = _asyncio.get_running_loop()
                await loop.run_in_executor(None, lambda g=gc: _build_eonet(g))
                _save_active_state()
                _startup_log.append(f"BFS complete for {gc}")
            except Exception as e:
                import traceback
                err_msg = f"[linkplay] BFS failed for {gc}: {e}\n{traceback.format_exc()}"
                print(err_msg, file=sys.stderr, flush=True)
                try:
                    with open(str(Path.home() / ".linkplay" / "bfs_error.log"), "a") as _ef:
                        _ef.write(err_msg + "\n")
                except: pass
                _startup_log.append(f"BFS FAILED for {gc}: {e}")
    except Exception as e:
        import traceback
        err_msg = f"[linkplay] Registry restore error: {e}\n{traceback.format_exc()}"
        print(err_msg, file=sys.stderr, flush=True)
        try:
            with open(str(Path.home() / ".linkplay" / "bfs_error.log"), "a") as _ef:
                _ef.write(err_msg + "\n")
        except: pass
    finally:
        _rom_restore_in_progress = False
        _rom_restore_done = True


def _get_narc(narc_path: str):
    """Get a parsed NARC, using cache to avoid re-parsing."""
    gc = current_rom['header']['game_code']
    key = (gc, narc_path)
    if key not in _narc_cache:
        data = current_rom['rom'].getFileByName(narc_path)
        _narc_cache[key] = ndspy.narc.NARC(data)
    return _narc_cache[key]


def _invalidate_narc(narc_path: str):
    """Remove a NARC from cache after a write."""
    gc = current_rom['header']['game_code']
    _narc_cache.pop((gc, narc_path), None)


def _parse_rom_prefix(path: str):
    """Parse optional game-code prefix from path. 'IRE:a/0/1/6:1' -> ('IRE', 'a/0/1/6:1').
    Handles both 3-char (NDS) and 4-char (GBA) game codes."""
    for code_len in (4, 3):
        if len(path) > code_len and path[code_len] == ':':
            candidate = path[:code_len]
            if candidate.isalpha() and candidate.isupper():
                if candidate in loaded_roms or (current_rom and current_rom['header']['game_code'] == candidate):
                    return candidate, path[code_len + 1:]
    return None, path


def _switch_rom(game_code: str):
    """Switch active ROM context. Returns original game_code for switching back."""
    orig = current_rom['header']['game_code'] if current_rom else None
    if orig == game_code:
        return orig
    _save_active_state()
    _restore_state(game_code)
    return orig


def _save_active_state():
    """Save active ROM's state to loaded_roms."""
    if not current_rom:
        return
    gc = current_rom['header']['game_code']
    loaded_roms[gc] = {
        'current_rom': current_rom,
        'flipnote': current_flipnote,
        'text_tables': text_tables,
        'text_narc': text_narc,
        'text_mult': text_mult,
        'text_gen': text_gen,
        'narc_roles': narc_roles,
        'tm_table': tm_table,
        'eonet_labels': eonet_labels.get(gc, {}),
        'eonet_index': eonet_index.get(gc, []),
    }


def _restore_state(game_code):
    """Restore a ROM's state from loaded_roms to globals."""
    global current_rom, current_flipnote, text_tables, text_narc, text_mult, text_gen, narc_roles, tm_table
    state = loaded_roms[game_code]
    current_rom = state['current_rom']
    current_flipnote = state['flipnote']
    text_tables = state['text_tables']
    text_narc = state['text_narc']
    text_mult = state['text_mult']
    text_gen = state['text_gen']
    narc_roles = state['narc_roles']
    tm_table = state.get('tm_table', [])
    gc = state['current_rom']['header']['game_code']
    eonet_labels[gc] = state.get('eonet_labels', {})
    eonet_index[gc] = state.get('eonet_index', [])


def _clear_active_state():
    """Clear all ROM state globals."""
    global current_rom, current_flipnote, text_tables, text_narc, text_mult, text_gen, narc_roles, tm_table
    current_rom = None
    current_flipnote = None
    text_tables = {}
    text_narc = None
    text_mult = None
    text_gen = None
    narc_roles = {}
    tm_table = []
working_dir = Path.home() / ".linkplay" / "work"
flipnotes_dir = Path.home() / ".linkplay" / "flipnotes"
sprites_dir = Path.home() / ".linkplay" / "sprites"
note_history = Path.home() / ".linkplay" / "note_history.jsonl"

# Region codes from game code suffix
REGION_MAP = {
    'E': 'US', 'P': 'EU', 'J': 'JP', 'K': 'KR',
    'D': 'DE', 'F': 'FR', 'S': 'ES', 'I': 'IT',
    'O': 'INT'  # International (used by Game Freak to bypass region locking)
}


def ensure_dirs():
    working_dir.mkdir(parents=True, exist_ok=True)
    flipnotes_dir.mkdir(parents=True, exist_ok=True)


def _note_belongs_to_game(path: str, game_codes: list) -> bool:
    """Return True if this note path belongs in a flipnote covering game_codes."""
    codes = set(game_codes)
    gen5_bw  = {'IRB', 'IRA'}
    gen5_bw2 = {'IRE', 'IRD'}
    hgss     = {'IPK', 'IPG'}
    gen4     = {'ADA', 'APA', 'CPU', 'IPK', 'IPG'}
    dp_pt    = {'ADA', 'APA', 'CPU'}

    # Paths starting with a/ or arm9 or swan_ are gen5 (either BW or BW2).
    # If the note has an explicit game= field that was stored, use it.
    # Otherwise fall back to path heuristics.
    if any(path.startswith(p) for p in ('poketool/', 'msgdata/', 'fielddata/',
                                         'battle/', 'itemtool/', 'contest/')):
        # Named gen4 paths
        if 'pl_' in path.split('/')[-1]:
            # Platinum-specific override files
            return bool(codes & dp_pt)
        return bool(codes & gen4)

    if path.startswith('arm9') or path.startswith('swan_'):
        # ARM9 patches / sound archive written for BW2
        return bool(codes & gen5_bw2)

    if path.startswith('a/') or path.startswith('_'):
        # Determine gen from path structure:
        # HGSS a/ paths were written during HGSS sessions:
        #   a/0/0/2, a/0/1/1, a/0/2/7, a/0/3/3, a/0/3/4, a/0/5/5, a/0/5/6
        #   a/1/2/8, a/1/2/9, a/1/3/6, a/1/6/9, a/2/0/2, a/2/0/3, a/2/0/4
        # BW2 a/ paths were written during BW2 sessions:
        #   a/0/0/2:xx, a/0/0/4, a/0/5/1, a/0/9/, a/1/2/4, a/1/2/6, a/3, a/3/0, a/3/0/7
        base = path.split(':')[0]
        bw2_bases = {
            'a/0/0/4', 'a/0/5/1', 'a/0/9/', 'a/0/9',
            'a/1/2/4', 'a/1/2/6', 'a/3', 'a/3/0', 'a/3/0/7',
        }
        hgss_bases = {
            'a/0/0/2', 'a/0/1/1', 'a/0/2/7', 'a/0/3/3', 'a/0/3/4',
            'a/0/5/5', 'a/0/5/6', 'a/1/2/8', 'a/1/2/9', 'a/1/3/6',
            'a/1/6/9', 'a/2/0/2', 'a/2/0/3', 'a/2/0/4',
        }
        meta_paths = {'_issues', '_test_note'}

        # Sub-paths of a/0/0/2 (like a/0/0/2:64) are BW2
        if ':' in path and path.split(':')[0] == 'a/0/0/2':
            return bool(codes & gen5_bw2)
        if base in bw2_bases:
            return bool(codes & gen5_bw2)
        if base in hgss_bases or path in meta_paths:
            return bool(codes & hgss)
        # Unknown a/ path: keep in gen5 only (safest default)
        return bool(codes & (gen5_bw | gen5_bw2))

    # Unknown path: write everywhere to be safe
    return True


def recover_notes_from_logs():
    """Mine Claude Code conversation logs for past note() calls. Replay them.

    Scans every .jsonl in the project's .claude directory for mcp__linkplay__note
    and mcp__linkplay__batch_notes tool calls. Writes each note only to the
    flipnote(s) it actually belongs to, based on path heuristics and explicit
    game= fields. Never writes ICR-sourced notes into flipnotes.

    This runs on server startup.
    """
    claude_dir = Path.home() / ".claude" / "projects"
    if not claude_dir.exists():
        return 0

    recovered = 0
    seen_notes = {}  # path -> input dict (latest wins)

    for project_dir in claude_dir.iterdir():
        if not project_dir.is_dir():
            continue
        for jsonl_file in project_dir.rglob("*.jsonl"):
            try:
                with open(jsonl_file, 'r', encoding='utf-8', errors='ignore') as fh:
                    for line in fh:
                        if 'mcp__linkplay__note' not in line:
                            continue
                        try:
                            entry = json.loads(line)
                        except:
                            continue
                        msg = entry.get('message', {})
                        for block in msg.get('content', []):
                            if not isinstance(block, dict):
                                continue
                            bname = block.get('name', '')
                            inp = block.get('input', {})
                            if bname == 'mcp__linkplay__note':
                                path = inp.get('path')
                                if path and inp.get('description'):
                                    seen_notes[path] = inp
                            elif bname == 'mcp__linkplay__batch_notes':
                                game = inp.get('game', '')
                                for note in inp.get('notes', []):
                                    path = note.get('path')
                                    if path and note.get('description'):
                                        if game and 'game' not in note:
                                            note = dict(note, game=game)
                                        seen_notes[path] = note
            except:
                continue

    # Also check server's own note history
    if note_history.exists():
        try:
            with open(note_history, 'r', encoding='utf-8', errors='ignore') as fh:
                for line in fh:
                    try:
                        inp = json.loads(line.strip())
                        path = inp.get('path')
                        if path and inp.get('description'):
                            seen_notes[path] = inp
                    except:
                        continue
        except:
            pass

    if not seen_notes:
        return 0

    # Write each note only to the flipnote(s) it belongs to
    for fpn_file in flipnotes_dir.glob("*.fpn"):
        try:
            with open(fpn_file, 'r', encoding='utf-8') as fh:
                fpn_data = json.load(fh)
        except:
            continue

        game_codes = fpn_data.get('game_codes', [fpn_data.get('game_code', '')])
        fpn_data.setdefault('notes', {})
        wrote = False

        for path, inp in seen_notes.items():
            # Never write ICR notes into flipnotes
            if inp.get('source') == 'icr':
                continue
            # Respect explicit game= if present
            explicit_game = inp.get('game', '')
            if explicit_game and explicit_game not in game_codes:
                continue
            # Path-based routing when no explicit game
            if not explicit_game and not _note_belongs_to_game(path, game_codes):
                continue
            # Don't overwrite existing manual notes
            if path in fpn_data['notes']:
                continue
            note_entry = {"description": inp['description']}
            if inp.get('name'): note_entry['name'] = inp['name']
            if inp.get('format'): note_entry['format'] = inp['format']
            if inp.get('tags'): note_entry['tags'] = inp['tags']
            if inp.get('file_range'): note_entry['file_range'] = inp['file_range']
            if inp.get('related'): note_entry['related'] = inp['related']
            fpn_data['notes'][path] = note_entry
            wrote = True
            recovered += 1

        if wrote:
            with open(fpn_file, 'w', encoding='utf-8') as fh:
                json.dump(fpn_data, fh, indent=2, ensure_ascii=False)

    _consolidate_flipnotes()

    return recovered
def _consolidate_flipnotes():
    """Merge notes from individual ROM flipnotes into shared partner flipnotes.

    If Diamond.fpn and Pokémon_Diamond_&_Pearl.fpn both exist,
    Diamond's notes flow into the shared one. Individual gets cleaned up.
    """
    # Map each game code to its flipnote file
    code_to_fpn = {}
    for fpn_file in flipnotes_dir.glob("*.fpn"):
        try:
            with open(fpn_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            codes = data.get('game_codes', [])
            if not codes:
                codes = [data.get('game_code', '')]
            for code in codes:
                if code:
                    code_to_fpn.setdefault(code, []).append((fpn_file, data))
        except:
            continue

    # For each pair group, find the shared flipnote and merge individuals into it
    for pair_name, pair_codes in FLIPNOTE_PAIRS.items():
        # Find all flipnotes that cover any code in this pair
        all_fpns = []
        for code in pair_codes:
            all_fpns.extend(code_to_fpn.get(code, []))

        # Deduplicate by path
        seen_paths = set()
        unique = []
        for fpn_file, data in all_fpns:
            if str(fpn_file) not in seen_paths:
                seen_paths.add(str(fpn_file))
                unique.append((fpn_file, data))

        if len(unique) <= 1:
            continue  # Only one flipnote for this group — nothing to merge

        # The shared one has multiple game_codes. Individual has one.
        shared = None
        individuals = []
        for fpn_file, data in unique:
            codes = data.get('game_codes', [])
            if len(codes) > 1:
                shared = (fpn_file, data)
            else:
                individuals.append((fpn_file, data))

        if not shared or not individuals:
            continue

        shared_file, shared_data = shared
        shared_data.setdefault('notes', {})
        merged = False

        for ind_file, ind_data in individuals:
            ind_notes = ind_data.get('notes', {})
            for path, note_val in ind_notes.items():
                # Don't overwrite existing notes in shared
                if path not in shared_data['notes']:
                    shared_data['notes'][path] = note_val
                    merged = True

            # Remove individual flipnote after merging
            try:
                ind_file.unlink()
            except:
                pass

        if merged:
            with open(shared_file, 'w', encoding='utf-8') as f:
                json.dump(shared_data, f, indent=2, ensure_ascii=False)


def detect_rom_type(path: str) -> str:
    ext = Path(path).suffix.lower()
    if ext == '.nds':
        return 'nds'
    elif ext == '.3ds':
        return '3ds'
    elif ext == '.gba':
        return 'gba'
    elif ext == '.gbc':
        return 'gbc'
    elif ext == '.gb':
        return 'gb'
    return 'unknown'


def read_nds_banner_title(path: str, banner_offset: int) -> str:
    """Read English title from NDS banner (UTF-16LE at banner+0x340)."""
    try:
        with open(path, 'rb') as f:
            f.seek(banner_offset + 0x340)
            title_bytes = f.read(256)
            title = title_bytes.decode('utf-16-le', errors='ignore')
            title = title.split('\x00')[0]
            lines = title.split('\n')
            if len(lines) >= 2:
                return f"{lines[0]} {lines[1]}".strip()
            return lines[0].strip() if lines else ""
    except:
        return ""


def read_nds_header(path: str) -> dict:
    """Read NDS ROM header for game code, title, etc."""
    with open(path, 'rb') as f:
        short_title = f.read(12).decode('ascii', errors='ignore').strip('\x00')
        full_code = f.read(4).decode('ascii', errors='ignore')
        f.seek(0x68)
        banner_offset = struct.unpack('<I', f.read(4))[0]

    game_code = full_code[:3] if len(full_code) >= 3 else full_code
    region_char = full_code[3] if len(full_code) >= 4 else 'E'
    region = REGION_MAP.get(region_char, 'INT')
    english_title = read_nds_banner_title(path, banner_offset) if banner_offset else ""
    is_english = bool(english_title and any(c.isalpha() for c in english_title))

    return {
        'game_code': game_code,
        'full_code': full_code,
        'region_char': region_char,
        'short_title': short_title,
        'game_title': english_title if is_english else short_title,
        'is_english': is_english,
        'region': region
    }


def read_gba_header(path: str) -> dict:
    """Read GBA ROM header."""
    with open(path, 'rb') as f:
        f.seek(0xA0)
        title = f.read(12).decode('ascii', errors='ignore').strip('\x00')
        full_code = f.read(4).decode('ascii', errors='ignore')

    # GBA uses the full 4-char code as game_code (BPRE, AXVE, BPEE, etc.)
    # unlike NDS which strips the region char
    game_code = full_code.strip() if full_code.strip() else full_code[:3]
    region_char = full_code[3] if len(full_code) >= 4 else 'E'
    region = REGION_MAP.get(region_char, 'US')

    return {
        'game_code': game_code,
        'full_code': full_code,
        'region_char': region_char,
        'game_title': title,
        'region': region,
        'is_english': region_char in ('E', 'P', 'D', 'F', 'S', 'I'),
    }


def read_gb_header(path: str) -> dict:
    """Read GB/GBC ROM header."""
    with open(path, 'rb') as f:
        data = f.read(0x150)

    # GBC header: 0x134-0x13E = 11B game title, 0x13F-0x142 = 4B manufacturer code (NOT part of title)
    title = data[0x134:0x13F].decode('ascii', errors='ignore').strip('\x00').strip()
    region_byte = data[0x14A] if len(data) > 0x14A else 0x01
    region = 'JP' if region_byte == 0x00 else 'US'
    region_char = 'J' if region == 'JP' else 'E'
    is_english = region != 'JP'

    # Use full title as game code (GB has no 4-char code like GBA/NDS)
    # Normalize: "POKEMON RED" -> "PMR", "POKEMON BLUE" -> "PMB" etc.
    _GB_GAME_CODES = {
        'POKEMON RED':    'PMR', 'POKEMON BLUE':   'PMB', 'POKEMON BLU':  'PMB',
        'POKEMON YELLOW': 'PMY', 'POKEMON GREEN':  'PMG', 'POKEMON YEL': 'PMY',
        'POKEMON_GLDAAUE':'PMG2','POKEMON_GLD':    'PMG2','POKEMON GOLD':   'PMG2',
        'PM_CRYSTAL':     'PMC', 'POKEMON CRYSTAL':'PMC',
        'POKEMON SILVER': 'PMS',
    }
    game_code = _GB_GAME_CODES.get(title.upper(), title[:3].upper())

    return {
        'game_code': game_code,
        'full_code': title[:4] if len(title) >= 4 else title,
        'region_char': region_char,
        'game_title': title,
        'region': region,
        'is_english': is_english,
    }


# Shared flipnotes — paired games share one flipnote
FLIPNOTE_PAIRS = {**_GEN7_FLIPNOTE_PAIRS, **_GEN6_FLIPNOTE_PAIRS, **_GEN5_FLIPNOTE_PAIRS, **_GEN4_FLIPNOTE_PAIRS, **_GEN3_FLIPNOTE_PAIRS, **_GEN2_FLIPNOTE_PAIRS, **_GEN1_FLIPNOTE_PAIRS}

def get_shared_name(game_code: str) -> Optional[str]:
    for name, codes in FLIPNOTE_PAIRS.items():
        if game_code in codes:
            return name
    return None

def get_partner_codes(game_code: str) -> list:
    for name, codes in FLIPNOTE_PAIRS.items():
        if game_code in codes:
            return codes
    return [game_code]


def get_sprites_folder(game_code: str) -> Path:
    """Get the sprites folder for a game, creating it if needed. Uses same naming as flipnotes."""
    shared = get_shared_name(game_code)
    folder_name = shared or GAME_INFO.get(game_code, {}).get('title', game_code)
    folder_name = folder_name.replace('/', '_').replace(':', '_')
    folder = sprites_dir / folder_name
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def find_flipnote(game_code: str) -> Optional[Path]:
    """Find existing flipnote by game code (checks shared partners too)."""
    partners = set(get_partner_codes(game_code))
    for fpn in flipnotes_dir.glob("*.fpn"):
        try:
            with open(fpn, 'r', encoding='utf-8') as f:
                data = json.load(f)
                codes = data.get('game_codes', [])
                if not codes:
                    codes = [data.get('game_code', '')]
                if partners & set(codes):
                    return fpn
        except:
            continue
    return None


def clean_game_title(title: str) -> str:
    """Strip 'Version' from game titles for cleaner pattern matching."""
    return title.replace(' Version ', ' ').replace('Version ', '').replace(' Version', '')

def upgrade_to_shared_flipnote(game_code: str) -> Path:
    """Merge all partner flipnotes into a single shared flipnote. Returns path."""
    shared_name = get_shared_name(game_code)
    partner_codes = get_partner_codes(game_code)
    display_name = shared_name or clean_game_title(game_code)
    safe_name = display_name.replace(' ', '_').replace('/', '_').replace(':', '_').replace('&', '&')
    shared_path = flipnotes_dir / f"{safe_name}.fpn"

    # Collect ALL existing flipnotes for any partner code
    found = []
    for fpn in flipnotes_dir.glob("*.fpn"):
        try:
            with open(fpn, 'r', encoding='utf-8') as f:
                data = json.load(f)
            codes = set(data.get('game_codes', []))
            if not codes:
                codes = {data.get('game_code', '')}
            if codes & set(partner_codes):
                found.append((fpn, data))
        except:
            continue

    # Merge notes, region codes, keep best tree/stats
    merged_notes = {}
    merged_regions = {}
    best_tree, best_stats = [], {}
    for _, data in found:
        merged_notes.update(data.get('notes', {}))
        for region, rcodes in data.get('region_codes', {}).items():
            merged_regions.setdefault(region, []).extend(rcodes)
        if not best_tree:
            best_tree = data.get('tree', [])
            best_stats = data.get('rom_stats', {})

    # Deduplicate region codes
    for region in merged_regions:
        merged_regions[region] = list(set(merged_regions[region]))

    merged_data = {
        'schema_version': 2,
        'game_codes': partner_codes,
        'game_title': display_name,
        'region_codes': merged_regions,
        'tree': best_tree,
        'rom_stats': best_stats,
        'notes': merged_notes,
    }

    with open(shared_path, 'w', encoding='utf-8') as f:
        json.dump(merged_data, f, indent=2, ensure_ascii=False)

    # Delete old separate flipnotes
    for fpn, _ in found:
        if fpn != shared_path and fpn.exists():
            fpn.unlink()

    return shared_path


def create_flipnote(game_code: str, game_title: str, region: str, region_char: str,
                    structure: list, rom_stats: dict, is_english: bool = False) -> Path:
    """Create new flipnote for a game (uses shared name if paired)."""
    shared_name = get_shared_name(game_code)
    partner_codes = get_partner_codes(game_code)
    display_name = shared_name or (clean_game_title(game_title) if game_title else game_title)

    if is_english and display_name:
        safe_title = display_name.replace(' ', '_').replace('/', '_').replace(':', '_').replace('&', '&')
        filename = f"{safe_title}.fpn"
    else:
        filename = f"{game_code}.fpn"

    path = flipnotes_dir / filename

    # Preserve existing notes if flipnote already exists
    existing_notes = {}
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                existing_data = json.load(f)
                existing_notes = existing_data.get("notes", {})
        except:
            pass

    data = {
        'schema_version': 2,
        'game_codes': partner_codes,
        'game_title': display_name,
        'region_codes': {region: [f"{game_code}{region_char}"]},
        'tree': structure,
        'rom_stats': rom_stats,
        'notes': existing_notes
    }

    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    return path


def build_nds_structure(rom, rom_path: str) -> tuple:
    """Build flat tree and ROM stats from NDS ROM."""
    tree = []
    rom_stats = {
        'total_bytes': Path(rom_path).stat().st_size,
        'arm9_size': len(rom.arm9),
        'arm7_size': len(rom.arm7),
        'files': {},  # path -> {size, type, file_count if narc}
        'file_count': 0,
        'narc_count': 0,
        'total_narc_files': 0
    }

    tree.append("arm9.bin")
    tree.append("arm7.bin")
    rom_stats['files']['arm9.bin'] = {'size': len(rom.arm9), 'type': 'binary'}
    rom_stats['files']['arm7.bin'] = {'size': len(rom.arm7), 'type': 'binary'}

    # Add overlays to tree
    try:
        parsed_ovs = rom.loadArm9Overlays()
        for ov_id in sorted(parsed_ovs.keys()):
            ov = parsed_ovs[ov_id]
            ov_name = f"overlay{ov_id}.bin"
            tree.append(ov_name)
            rom_stats['files'][ov_name] = {'size': len(ov.data), 'type': 'overlay'}
    except Exception:
        pass

    def walk_folder(folder, path=""):
        for filename in folder.files:
            full_path = f"{path}/{filename}" if path else filename
            tree.append(full_path)
            rom_stats['file_count'] += 1

            try:
                file_id = folder.idOf(filename)
                file_data = rom.files[file_id]
                file_info = {'size': len(file_data), 'type': 'file'}

                if len(file_data) >= 4 and file_data[:4] == b'NARC':
                    narc = ndspy.narc.NARC(file_data)
                    file_info['type'] = 'narc'
                    file_info['file_count'] = len(narc.files)
                    rom_stats['narc_count'] += 1
                    rom_stats['total_narc_files'] += len(narc.files)

                    # Add NARC internal files to tree
                    for idx in range(len(narc.files)):
                        tree.append(f"{full_path}:{idx}")

                rom_stats['files'][full_path] = file_info
            except:
                pass

        for name, subfolder in folder.folders:
            folder_path = f"{path}/{name}" if path else name
            tree.append(folder_path + "/")
            walk_folder(subfolder, folder_path)

    if rom.filenames:
        walk_folder(rom.filenames)

    return tree, rom_stats


def build_3ds_structure(romfs_files: dict, fh, rom_path: str) -> tuple:
    """Build flat tree and ROM stats from 3DS ROM's RomFS."""
    tree = []
    rom_stats = {
        'total_bytes': Path(rom_path).stat().st_size,
        'files': {},
        'file_count': 0,
        'garc_count': 0,
        'total_garc_files': 0
    }

    # Group paths into folders and files
    for fpath in sorted(romfs_files.keys()):
        abs_off, size = romfs_files[fpath]
        tree.append(fpath)
        rom_stats['file_count'] += 1
        file_info = {'size': size, 'type': 'file'}

        # Check if it's a GARC
        try:
            data, _ = read_garc_sub(fh, abs_off, 0)
            # If read_garc_sub succeeded, it's a GARC
            garc_files = read_garc_all(fh, abs_off)
            file_info['type'] = 'garc'
            file_info['file_count'] = len(garc_files)
            rom_stats['garc_count'] += 1
            rom_stats['total_garc_files'] += len(garc_files)
            for idx in range(len(garc_files)):
                tree.append(f"{fpath}:{idx}")
        except Exception:
            pass

        rom_stats['files'][fpath] = file_info

    return tree, rom_stats


def decompress_arm9(arm9_path: str):
    """Decompress ARM9 using blz."""
    blz_path = get_tool_path('blz')
    try:
        subprocess.run([blz_path, '-d', arm9_path], check=True, capture_output=True)
    except:
        pass


def compress_arm9(arm9_path: str):
    """Compress ARM9 using blz."""
    blz_path = get_tool_path('blz')
    try:
        subprocess.run([blz_path, '-en9', arm9_path], check=True, capture_output=True)
    except:
        pass


def _load_overlays(rom) -> dict:
    """Load all ARM9 overlays from ROM via ndspy's loadArm9Overlays().
    Returns {overlay_id: bytearray(decompressed_data)}.
    ndspy handles LZ10 decompression automatically.
    """
    overlays = {}
    try:
        parsed = rom.loadArm9Overlays()  # {int overlayID: ndspy.code.Overlay}
        for ov_id, ov in parsed.items():
            overlays[ov_id] = bytearray(ov.data)
    except Exception:
        pass
    return overlays


def _is_overlay_path(path: str) -> int:
    """Check if path is an overlay reference like 'overlay2.bin'. Returns overlay ID or -1."""
    m = re.match(r'^overlay(\d+)\.bin$', path.lower().strip('/'))
    return int(m.group(1)) if m else -1


def _resolve_nds_file(path: str) -> bytes:
    """Resolve an NDS/3DS file path to raw bytes. Raises ValueError on error."""
    p = path.strip('/')
    pl = p.lower()

    # 3DS: read from open RomFS via xoleon
    if current_rom.get('type') == '3ds':
        fh, fs = current_rom['romfs_fh'], current_rom['romfs_files']
        if ':' in p:
            gp, fi = p.rsplit(':', 1)
            gp = gp.lstrip('/')
            if gp not in fs:
                raise ValueError(f"GARC not found: {gp}")
            fi = int(fi)
            # WD flat files: single GARC sub-file packing multiple entries (move_data)
            wd = current_rom.get('wd_cache', {}).get(gp)
            if wd is None:
                data, total = read_garc_sub(fh, fs[gp][0], 0)
                if data and len(data) > 4 and data[0:2] == b'WD':
                    count = struct.unpack_from('<H', data, 2)[0]
                    offsets = [struct.unpack_from('<I', data, 4 + i*4)[0] for i in range(count + 1)]
                    wd = [data[offsets[i]:offsets[i+1]] for i in range(count)]
                    current_rom.setdefault('wd_cache', {})[gp] = wd
            if wd is not None:
                if fi >= len(wd):
                    raise ValueError(f"Index {fi} out of range (WD has {len(wd)} entries)")
                return wd[fi]
            data, total = read_garc_sub(fh, fs[gp][0], fi)
            if data is None:
                raise ValueError(f"Index {fi} out of range (GARC has {total} files)")
            return data
        if p not in fs:
            raise ValueError(f"File not found in RomFS: {p}")
        off, sz = fs[p]
        fh.seek(off)
        return fh.read(sz)
    if pl == 'arm9.bin':
        return bytes(current_rom['arm9_data'])
    if pl == 'arm7.bin':
        return bytes(current_rom['arm7_data'])
    ov_id = _is_overlay_path(p)
    if ov_id >= 0:
        overlays = current_rom.get('overlays', {})
        if ov_id not in overlays:
            raise ValueError(f"Overlay {ov_id} not found (available: {sorted(overlays.keys())})")
        return bytes(overlays[ov_id])
    if ':' in p:
        narc_path, file_idx = p.rsplit(':', 1)
        file_idx = int(file_idx)
        narc = _get_narc(narc_path.lstrip('/'))
        if file_idx >= len(narc.files):
            raise ValueError(f"Index {file_idx} out of range (NARC has {len(narc.files)} files)")
        return bytes(narc.files[file_idx])
    return bytes(current_rom['rom'].getFileByName(p))


def detect_compression(data: bytes) -> str:
    """Detect compression type from header byte."""
    if len(data) < 4:
        return 'none'
    header = data[0]
    if header == 0x10:
        return 'lz10'
    if header == 0x11:
        return 'lz11'
    if header == 0x40:
        return 'lz40'
    if header == 0x20:
        return 'huffman4'
    if header == 0x28:
        return 'huffman8'
    if header == 0x30:
        return 'rle'
    return 'none'


def decompress_data(data: bytes) -> tuple:
    """Attempt to decompress data. Returns (data, compression_type)."""
    compression = detect_compression(data)

    if compression == 'none':
        return data, 'none'

    tool_map = {
        'lz10': 'lzss', 'lz11': 'lzx', 'lz40': 'lzx',
        'huffman4': 'huffman', 'huffman8': 'huffman', 'rle': 'rle'
    }

    tool = tool_map.get(compression)
    if not tool:
        return data, compression

    tool_path = get_tool_path(tool)

    try:
        result = subprocess.run([tool_path, '-d', '-'], input=data, capture_output=True, timeout=5)
        if result.returncode == 0 and len(result.stdout) > 0 and len(result.stdout) != len(data):
            return result.stdout, compression
    except:
        pass

    # Temp file fallback — some tools (lzx) don't support stdin
    try:
        import tempfile as _tf
        tmp = os.path.join(_tf.gettempdir(), f'_lz_{os.getpid()}.bin')
        open(tmp, 'wb').write(data)
        subprocess.run([tool_path, '-d', tmp], capture_output=True, timeout=5)
        out = open(tmp, 'rb').read()
        os.unlink(tmp)
        if len(out) > 0 and len(out) != len(data):
            return out, compression
    except:
        pass

    if compression == 'lz10':
        try:
            return ndspy.lz10.decompress(data), 'lz10'
        except:
            pass

    return data, compression


def compress_data(data: bytes, compression: str) -> bytes:
    """Compress data with specified type."""
    if compression == 'none' or not compression:
        return data

    tool_map = {
        'lz10': ('lzss', '-evn'), 'lz11': ('lzx', '-evb'), 'lz40': ('lzx', '-evb'),
        'huffman4': ('huffman', '-e4'), 'huffman8': ('huffman', '-e8'), 'rle': ('rle', '-e')
    }

    tool_info = tool_map.get(compression)
    if not tool_info:
        return data

    tool, encode_flag = tool_info
    tool_path = get_tool_path(tool)

    try:
        result = subprocess.run([tool_path, encode_flag, '-'], input=data, capture_output=True, timeout=5)
        if result.returncode == 0 and len(result.stdout) > 0:
            return result.stdout
    except:
        pass

    if compression == 'lz10':
        try:
            return ndspy.lz10.compress(data)
        except:
            pass

    return data







# Species reference — verified against pokegreen disassembly data/pokemon/names.asm
# 190 internal slots (0-189); けつばん = MissingNo placeholder
# Gen I internal order → JP species names (index = game constant, 0-189)

# EOS bytes per platform




def scan_rom_text(rom_data: bytes, charmap: dict, eos: int,
                  min_strlen: int = 3, min_table: int = 8,
                  max_strlen: int = 20) -> list:
    """Scan a raw ROM binary for text tables.

    Looks for runs of back-to-back valid strings (terminated by eos byte).
    Returns list of lists — each inner list is a candidate text table (list of strings).

    Strategy: slide over the ROM at every offset. If we hit a valid string
    followed by EOS, keep going. When the run breaks, if we collected enough
    strings, save it as a candidate table.
    """
    tables = []
    current_table = []
    i = 0
    data_len = min(len(rom_data), 8 * 1024 * 1024)  # text lives in first 8MB

    while i < data_len:
        # Skip leading 0x00 bytes — these are null padding between fixed-width
        # table entries, not spaces at the start of a string.
        while i < data_len and rom_data[i] == 0x00:
            i += 1

        # Try to decode a string starting at i
        j = i
        chars = []
        while j < data_len and j < i + max_strlen:
            b = rom_data[j]
            if b == eos:
                break
            ch = charmap.get(b)
            if ch is None:
                break
            chars.append(ch)
            j += 1

        # Strip trailing spaces (from in-string 0x00s near end of entry)
        name = ''.join(chars).rstrip()

        if j < data_len and rom_data[j] == eos and len(name) >= min_strlen:
            # Valid string found
            current_table.append(name)
            i = j + 1  # advance past EOS
        else:
            # Not a valid string here
            if len(current_table) >= min_table:
                tables.append(current_table)
            current_table = []
            i += 1

    if len(current_table) >= min_table:
        tables.append(current_table)

    return tables



























def bootstrap_text_tables_binary(rom_data: bytes, rom_type: str,
                                 region: str = 'US') -> dict:
    """Bootstrap text tables from a raw GBA/GB/GBC ROM binary.

    Scans the binary for text tables, then fingerprints to identify
    species, moves, items, etc. Same fingerprint logic as NDS games —
    the indices are universal across all generations.
    """
    global text_tables, text_gen

    if rom_type == 'gba':
        charmap = _GEN3_CHARMAP_EN
        eos = _GEN3_EOS
        gen = 3
    elif rom_type in ('gbc', 'gb'):
        charmap = _GEN1_CHARMAP_JP if region == 'JP' else _GEN1_CHARMAP_EN
        eos = _GEN1_EOS
        gen = 2 if rom_type == 'gbc' else 1
    else:
        return {"error": f"Unsupported ROM type: {rom_type}"}

    text_tables = {}
    text_gen = gen

    # Scan for text tables (species, moves, abilities, natures — pure string arrays)
    candidates = scan_rom_text(rom_data, charmap, eos)

    if not candidates:
        return {"error": "No text tables found in ROM binary", "gen": gen}

    # Load candidates into text_tables as integer-indexed "files"
    for idx, table in enumerate(candidates):
        text_tables[idx] = table

    # Gen I/II: fixed-width species names can't be found by the generic scanner
    # (0x50 padding breaks run detection). Use dedicated scanner.
    # JP vs EN anchor pair comes from GAME_INFO — no hardcoding inside the scanner.
    # EN: fixed 10-byte slots (RHYDON→KANGASKHAN). JP: variable-length (サイドン→ガルーラ).
    if gen <= 2:
        gc = current_rom['header']['game_code'] if current_rom else ''
        _jp = GAME_INFO.get(gc, {}).get('jp', False)
        if _jp:
            # JP Gen I: packed variable-length table anchored at サイドン→ガルーラ.
            _scan_gen1_species_varlen(rom_data, charmap, eos,
                                      anchor_pair=('サイドン', 'ガルーラ'))
            _scan_gen1_moves_jp(rom_data, charmap, eos)
            _scan_gen1_trainer_classes_jp(rom_data, charmap, eos)
            _scan_gen1_items(rom_data, charmap, eos)
        else:
            if rom_type == 'gbc':
                # EN Gen II: Pokédex order (same as Gen III), BULBASAUR→IVYSAUR anchor
                # No dex reordering needed — already in dex order
                _scan_gen1_species(rom_data, charmap, eos,
                                   anchor_pair=('BULBASAUR', 'IVYSAUR'),
                                   max_entries=252,
                                   skip_reorder=True)
                _scan_gen2_trainer_classes_en(rom_data, charmap, eos)
            else:
                # EN Gen I: fixed 10-byte slots anchored at RHYDON→KANGASKHAN
                _scan_gen1_species(rom_data, charmap, eos,
                                   anchor_pair=('RHYDON', 'KANGASKHAN'))
                _scan_gen1_trainer_classes_en(rom_data, charmap, eos)
            _scan_gen1_items(rom_data, charmap, eos)

    # Gen III: items are 44-byte structs, abilities may be merged with descriptions.
    # Use dedicated scanners anchored to known strings.
    if gen == 3:
        _scan_gen3_abilities(rom_data, charmap, eos)
        _scan_gen3_items(rom_data, charmap, eos)
        _scan_gen3_species(rom_data, charmap, eos)
        _scan_gen3_trainer_names(rom_data, charmap, eos)

    # Auto-detect named tables via fingerprinting (same as NDS path)
    found = auto_detect_tables()

    result = {"gen": gen, "rom_type": rom_type,
              "candidates": len(candidates),
              "file_count": len(candidates)}

    if found:
        result["status"] = "ok"
        result["detected"] = {k: f"table:{v} ({len(text_tables.get(k, []))} entries)"
                              for k, v in found.items()}
        species = text_tables.get('species', [])
        if len(species) > 1:
            result["sample"] = {"species[1]": species[1]}
    else:
        result["status"] = "FAILED"
        result["_warning"] = "Fingerprints not found — character map may need tuning"

    return result




# Game info — gen + NARC role mappings. Roles auto-drive _auto_decode.
# Gen IV — DP/Pt use named folders, HGSS uses a/X/Y/Z


GAME_INFO = {**_GEN7_GAME_INFO, **_GEN6_GAME_INFO, **_GEN5_GAME_INFO, **_GEN4_GAME_INFO, **_GEN3_GAME_INFO, **_GEN2_GAME_INFO, **_GEN1_GAME_INFO}

# Content fingerprints — universal across all Pokemon games.
# (entry_index, expected_string) pairs that ALL must match.
TABLE_FINGERPRINTS = {
    'species':        [(1, "Bulbasaur"), (4, "Charmander")],
    'moves':          [(1, "Pound"), (5, "Mega Punch")],
    'items':          [(1, "Master Ball"), (17, "Potion")],
    'abilities':      [(1, "Stench"), (22, "Intimidate")],
    'natures':        [(0, "Hardy"), (1, "Lonely"), (3, "Adamant")],
    'type_names':     [(0, "Normal"), (1, "Fighting"), (2, "Flying")],
    'tournament_names': [(4, "Champions Tournament"), (13, "Rental Tournament")],
}

# Gen III fingerprints — move/item tables start at index 0, not 1.
# Species still starts at 1 (index 0 = dummy). Moves/items have no dummy.
# Items are 44-byte structs — scanned separately, not as raw strings.

# Japanese content fingerprints — same indices, Japanese text.
# Verified from Bulbapedia: はたく=Pound, メガトンパンチ=Mega Punch, etc.

# Heuristic markers — tables without unique index-based fingerprints.
# All listed strings must appear SOMEWHERE in the file.
# location_names uses per-game markers since regions have different cities/routes.
HEURISTIC_MARKERS = {
    'trainer_classes': ["Youngster", "Lass", "School Kid"],
    'location_names':  ["Mystery Zone"],
    'trainer_names':   ["Palmer", "Cynthia"],
    'trainer_names_gen5': ["Bianca", "Shauntal", "Grimsley"],
}

# Substring markers: all strings must appear as substrings within at least one entry.
# Must work across Gen IV (different wording, ê instead of é) AND Gen V.
HEURISTIC_SUBSTR = {
    'item_descriptions':  ["best Ball with the ultimate"],
    'move_descriptions':  ["pounded with a long tail"],
    'ability_descriptions': ["repel wild"],
    'pokedex_flavor':     ["seed on its back"],
    'pokedex_category':   ["Seed Pok", "Lizard Pok"],
}


def auto_detect_tables() -> dict:
    """Scan decoded text_tables to identify named tables by content fingerprinting."""
    # Seed with tables already set by dedicated scanners so they aren't overwritten.
    # Use -1 as sentinel (no valid integer key is negative).
    found = {k: -1 for k in text_tables if isinstance(k, str)}

    # Pass 1: exact fingerprints (entry at specific index must match)
    # Try English first, then Japanese — same indices, different strings.
    for fingerprint_set in (TABLE_FINGERPRINTS, TABLE_FINGERPRINTS_GEN3, TABLE_FINGERPRINTS_JPN):
        for file_idx in sorted(k for k in text_tables if isinstance(k, int)):
            strings = text_tables[file_idx]
            if not isinstance(strings, list) or len(strings) < 2:
                continue
            for table_name, markers in fingerprint_set.items():
                if table_name in found:
                    continue
                if all(idx < len(strings) and strings[idx].strip().upper() == expected.upper() for idx, expected in markers):
                    text_tables[table_name] = strings
                    found[table_name] = file_idx

    # Pass 2: heuristic markers (all listed strings must exist in file)
    for file_idx in sorted(k for k in text_tables if isinstance(k, int)):
        strings = text_tables[file_idx]
        if not isinstance(strings, list):
            continue
        string_set_upper = set(s.strip().upper() for s in strings if isinstance(s, str))
        for table_name, markers in HEURISTIC_MARKERS.items():
            if table_name in found:
                continue
            if all(m.upper() in string_set_upper for m in markers):
                text_tables[table_name] = strings
                found[table_name] = file_idx

    # Pass 2b: substring markers
    for file_idx in sorted(k for k in text_tables if isinstance(k, int)):
        strings = text_tables[file_idx]
        if not isinstance(strings, list):
            continue
        for table_name, markers in HEURISTIC_SUBSTR.items():
            if table_name in found:
                continue
            joined = ' '.join(s for s in strings if isinstance(s, str)).lower()
            if all(m.lower() in joined for m in markers):
                text_tables[table_name] = strings
                found[table_name] = file_idx

    # Promote trainer_names_gen5 -> trainer_names if Gen IV version wasn't found
    if 'trainer_names' not in found and 'trainer_names_gen5' in found:
        found['trainer_names'] = found.pop('trainer_names_gen5')
        text_tables['trainer_names'] = text_tables.pop('trainer_names_gen5')
    elif 'trainer_names_gen5' in found:
        found.pop('trainer_names_gen5')
        text_tables.pop('trainer_names_gen5', None)

    # Pass 3b: Gen IV has TWO trainer name files — generic NPC names and battle
    # trainer names. Heuristic markers match both. Use PPRE-verified indices.
    VERIFIED_TRAINER_NAMES = {
        'ADA': 559, 'APA': 559,   # Diamond / Pearl
        'CPU': 618,                # Platinum
        'IPK': 729, 'IPG': 729,   # HeartGold / SoulSilver
    }
    gc = current_rom['header']['game_code'] if current_rom else None
    if gc in VERIFIED_TRAINER_NAMES:
        correct_idx = VERIFIED_TRAINER_NAMES[gc]
        if correct_idx in text_tables and isinstance(text_tables[correct_idx], list):
            old_idx = found.get('trainer_names')
            if old_idx is not None and old_idx != correct_idx:
                text_tables['npc_names'] = text_tables[old_idx]
                found['npc_names'] = old_idx
            text_tables['trainer_names'] = text_tables[correct_idx]
            found['trainer_names'] = correct_idx

    # Pass 4: description tables — usually near their name tables.
    # Gen V: typically ±1. Gen IV: can be ±1 to ±3.
    # Descriptions have similar entry count but longer average string length.
    for name_tbl, desc_tbl in [('items', 'item_descriptions'), ('moves', 'move_descriptions'), ('abilities', 'ability_descriptions')]:
        if name_tbl in found and desc_tbl not in found:
            name_idx = found[name_tbl]
            name_count = len(text_tables[name_tbl])
            for offset in [-1, 1, -2, 2, -3, 3]:
                candidate = name_idx + offset
                if candidate in text_tables and isinstance(text_tables[candidate], list) and candidate not in found.values():
                    entries = text_tables[candidate]
                    if abs(len(entries) - name_count) < 10:
                        avg_len = sum(len(s) for s in entries[:20]) / max(1, min(20, len(entries)))
                        if avg_len > 10:  # descriptions longer than names (Gen IV can be short)
                            text_tables[desc_tbl] = entries
                            found[desc_tbl] = candidate
                            break

    # Pass 5: verified description indices (BW2 confirmed from PPRE)
    VERIFIED_DESCS = {
        'IRE': {'item_descriptions': 63, 'ability_descriptions': 375, 'move_descriptions': 402},
        'IRD': {'item_descriptions': 63, 'ability_descriptions': 375, 'move_descriptions': 402},
    }
    if gc in VERIFIED_DESCS:
        for desc_tbl, idx in VERIFIED_DESCS[gc].items():
            if desc_tbl not in found and idx in text_tables and isinstance(text_tables[idx], list):
                text_tables[desc_tbl] = text_tables[idx]
                found[desc_tbl] = idx

    # Pass 6: pokedex flavor — near species table, much longer entries (full dex descriptions)
    if 'species' in found and 'pokedex_flavor' not in found:
        sp_idx = found['species']
        sp_count = len(text_tables['species'])
        for offset in range(-5, 6):
            if offset == 0:
                continue
            candidate = sp_idx + offset
            if candidate in text_tables and isinstance(text_tables[candidate], list) and candidate not in found.values():
                entries = text_tables[candidate]
                if abs(len(entries) - sp_count) < 10:
                    avg_len = sum(len(s) for s in entries[:20]) / max(1, min(20, len(entries)))
                    if avg_len > 30:  # dex entries are much longer than species names
                        text_tables['pokedex_flavor'] = entries
                        found['pokedex_flavor'] = candidate
                        break

    return found











# AI Flags for Gen IV/V trainers


def decode_ai_flags(flags: int, gen: int = 5) -> list:
    """Decode AI flags into human-readable list."""
    flag_map = AI_FLAGS_GEN5 if gen >= 5 else AI_FLAGS_GEN4
    active_flags = []
    for bit, name in sorted(flag_map.items()):
        if flags & bit:
            active_flags.append(name)
    return active_flags if active_flags else ["None"]




# TRPoke template sizes (keyed by template bits from TRData byte 0)
# bit 0 = has custom moves, bit 1 = has held item
# Gen V: iv(u8) ability(u8) level(u8) pad(u8) species(u16) form(u16) = 8B base
# Gen IV: iv(u16) level(u16) species(u16) = 6B base

# =============================================================================
# TRAINER LOCATION MAPPING
# Maps special trainers (Gym Leaders, E4, Champions) to their battle locations.
# =============================================================================

TRAINER_LOCATIONS = {**_GEN4_TRAINER_LOCATIONS, **_GEN5_TRAINER_LOCATIONS}

# Class-only location mappings (fallback when name not found)
CLASS_LOCATIONS = {**_GEN4_CLASS_LOCATIONS, **_GEN5_CLASS_LOCATIONS}


def get_trainer_location(game_code: str, class_name: str, trainer_name: str):
    """Look up location for special trainers (Gym Leaders, E4, Champions, etc.)."""
    # Resolve alias
    mapping = TRAINER_LOCATIONS.get(game_code)
    if isinstance(mapping, str):
        mapping = TRAINER_LOCATIONS.get(mapping, {})
    if not mapping:
        return None
    
    # Try (class, name) first
    location = mapping.get((class_name, trainer_name))
    if location:
        return location
    
    # Try CLASS_LOCATIONS fallback
    class_map = CLASS_LOCATIONS.get(game_code)
    if isinstance(class_map, str):
        class_map = CLASS_LOCATIONS.get(class_map, {})
    if class_map:
        return class_map.get(class_name)
    
    return None




def get_text(key, entry_index: int = None):
    """Get decoded text. Key can be int (file index) or str (named alias like 'species').
    get_text('species', 26) -> 'Raichu'. get_text(90) -> all species names.
    """
    global text_tables
    if isinstance(key, str):
        strings = text_tables.get(key, [])
        if not strings:
            return [] if entry_index is None else f"#{entry_index}"
    else:
        if key not in text_tables:
            if text_narc is None:
                return [] if entry_index is None else f"#{entry_index}"
            if key >= len(text_narc.files):
                return [] if entry_index is None else f"#{entry_index}"
            # Lazy decode: use gen-appropriate decoder
            if text_gen == 5 and text_mult is not None:
                text_tables[key] = decode_gen5_text(text_narc.files[key], text_mult)
            elif text_gen == 4:
                text_tables[key] = decode_gen4_text(text_narc.files[key])
            else:
                return [] if entry_index is None else f"#{entry_index}"
        strings = text_tables[key]
    if entry_index is None:
        return strings
    return strings[entry_index] if entry_index < len(strings) else f"#{entry_index}"


def bootstrap_text_tables(rom, game_code: str, file_list: list = None) -> dict:
    """Load text NARC/GARC, decode all files, auto-detect named tables.
    If file_list is provided (3DS), skip NARC loading — files already extracted by xoleon.
    """
    global text_tables, text_narc, text_mult, text_gen
    text_tables = {}
    text_narc = None
    text_mult = None
    text_gen = None

    game_info = GAME_INFO.get(game_code)
    if not game_info:
        return {}

    gen = game_info['gen']
    text_gen = gen
    text_narc_path = game_info['narcs'].get('text')
    if not text_narc_path:
        return {}

    # Build reverse role map: path -> role (for _auto_decode)
    # Start fresh — previous ROM's paths don't belong in this ROM's roles.
    # ICR-discovered roles get re-added after BFS runs.
    global narc_roles
    narc_roles = {}
    for role, path in game_info['narcs'].items():
        if role != 'text':
            narc_roles[path] = role

    if file_list is not None:
        # 3DS: xoleon already read the GARC — wrap as narc-like object
        class _GarcFiles:
            pass
        text_narc = _GarcFiles()
        text_narc.files = file_list
    else:
        # NDS: load via ndspy
        try:
            narc_data = rom.getFileByName(text_narc_path)
            text_narc = ndspy.narc.NARC(narc_data)
        except Exception as e:
            return {"error": f"Failed to load text NARC {text_narc_path}: {e}"}

    file_count = len(text_narc.files)

    if gen in (6, 7):
        # Gen VI/VII: same cipher as Gen V, MULT always 0x2983
        text_mult = 0x2983
        for i in range(file_count):
            text_tables[i] = decode_gen5_text(text_narc.files[i], text_mult)

    elif gen == 5:
        # Gen V: find species file to derive MULT, then decode all
        # Try common indices first, then brute-force
        candidates = [90, 70] + [i for i in range(file_count) if i not in (90, 70)]
        for c in candidates:
            if c >= file_count:
                continue
            m = _derive_gen5_mult(text_narc.files[c])
            if m == 0:
                continue
            test = decode_gen5_text(text_narc.files[c], m)
            if len(test) > 1 and test[1] == "Bulbasaur":
                text_mult = m
                break

        if text_mult is None:
            return {"error": "Could not derive text MULT (no species file found)"}

        for i in range(file_count):
            text_tables[i] = decode_gen5_text(text_narc.files[i], text_mult)

    elif gen == 4:
        # Gen IV: each file has its own seed, decode independently
        for i in range(file_count):
            text_tables[i] = decode_gen4_text(text_narc.files[i])

    # Auto-detect all named tables by content fingerprinting
    found = auto_detect_tables()

    # Build result
    result = {"file_count": file_count, "gen": gen}
    if text_mult is not None:
        result["mult"] = f"0x{text_mult:04X}"

    species = text_tables.get('species', [])
    if len(species) > 1 and species[1].strip().upper() == "BULBASAUR":
        result["status"] = "ok"
        result["sample"] = {"species[1]": species[1], "species[26]": species[26] if len(species) > 26 else "?"}
    else:
        result["status"] = "FAILED"
        result["_warning"] = "Could not find species table"

    if found:
        detected_rich = {}
        for tname, fidx in found.items():
            count = len(text_tables.get(tname, []))
            detected_rich[tname] = f"{text_narc_path}:{fidx} ({count} entries)"
        result["detected"] = detected_rich

    return result


# TM table search patterns: first 4 TM move IDs as u16 LE
# Gen V: TM01=Hone Claws(468), TM02=Dragon Claw(337), TM03=Psyshock(473), TM04=Calm Mind(347)
# Gen IV: TM01=Focus Punch(264), TM02=Dragon Claw(337), TM03=Water Pulse(352), TM04=Calm Mind(347)
_TM_SEARCH = {**_GEN4_TM_SEARCH, **_GEN5_TM_SEARCH}


def _discover_tm_table():
    """Search ARM9 for TM→move table, build bit-ordered tm_table. Returns count or None."""
    global tm_table
    tm_table = []
    if not current_rom or current_rom['type'] != 'nds':
        tm_table = []   # Clear stale table from any previous NDS session
        return None
    gen = text_gen or 5
    search_info = _TM_SEARCH.get(gen)
    if not search_info:
        return None
    pattern, entry_count = search_info
    arm9 = bytes(current_rom['arm9_data'])
    offset = arm9.find(pattern)
    if offset < 0:
        return None

    # Read entry_count × u16 LE move IDs from ARM9
    raw_table = []
    for i in range(entry_count):
        pos = offset + i * 2
        if pos + 2 > len(arm9):
            return None
        move_id = struct.unpack_from('<H', arm9, pos)[0]
        raw_table.append(move_id)

    # Build bit-ordered table: personal data bits → (label, move_id)
    if gen == 5:
        # ARM9 order: TM01-92(0-91), HM01-06(92-97), TM93-95(98-100)
        # Bit order:  TM01-95(0-94), HM01-06(95-100)
        for bit in range(101):
            if bit < 92:
                # TM01-92 → ARM9 entries 0-91
                label = f"TM{bit + 1:02d}"
                move_id = raw_table[bit]
            elif bit < 95:
                # TM93-95 → ARM9 entries 98-100
                tm_num = bit + 1  # 93, 94, 95
                label = f"TM{tm_num:02d}"
                move_id = raw_table[98 + (bit - 92)]
            else:
                # HM01-06 → ARM9 entries 92-97
                hm_num = bit - 94  # 1-6
                label = f"HM{hm_num:02d}"
                move_id = raw_table[92 + (bit - 95)]
            tm_table.append((label, move_id))
    else:
        # Gen IV: ARM9 order = bit order: TM01-92(0-91), HM01-08(92-99)
        for bit in range(100):
            if bit < 92:
                label = f"TM{bit + 1:02d}"
            else:
                label = f"HM{bit - 91:02d}"
            tm_table.append((label, raw_table[bit]))

    return len(tm_table)



EV_STAT_BITS = ['HP', 'Atk', 'Def', 'Spe', 'SpA', 'SpD']  # bit 0-5






def decode_ev_spread(byte_val):
    """Decode EV bitmask: each set bit = 252 EVs in that stat."""
    stats = [EV_STAT_BITS[i] for i in range(6) if byte_val & (1 << i)]
    return stats if stats else ["None"]

def decode_trainer_iv(byte_val):
    """TRPoke difficulty byte → IV for all stats. 255 → 31, 0 → 0."""
    return byte_val * 31 // 255

def get_ability_from_personal(species_id: int, ability_slot: int) -> str:
    """Get actual ability name from personal data based on species and slot."""
    if not current_rom or current_rom['type'] != 'nds':
        return f"ability_slot_{ability_slot}"
    
    try:
        rom = current_rom['rom']
        personal_path = next((p for p, r in narc_roles.items() if r == 'personal'), None)
        
        if not personal_path:
            return f"ability_slot_{ability_slot}"
        
        personal_narc = _get_narc(personal_path)

        if species_id >= len(personal_narc.files):
            return f"ability_slot_{ability_slot}"
        
        personal_data = personal_narc.files[species_id]
        gen = text_gen or 5
        ability_list = text_tables.get('abilities', [])
        
        if gen <= 4:
            # Gen IV: abilities at bytes 0x16, 0x17 (u8)
            if len(personal_data) < 0x18:
                return f"ability_slot_{ability_slot}"
            abilities = [personal_data[0x16], personal_data[0x17]]
            if ability_slot < len(abilities):
                aid = abilities[ability_slot]
                return ability_list[aid] if aid < len(ability_list) else f"ability#{aid}"
        else:
            # Gen V: abilities at 0x18, 0x19, 0x1A (u8, slot 0/1/2 = normal/normal/hidden)
            if len(personal_data) < 0x1B:
                return f"ability_slot_{ability_slot}"
            abilities = []
            for i in range(3):
                off = 0x18 + i
                if off < len(personal_data):
                    aid = personal_data[off]
                    abilities.append(aid)
            if ability_slot < len(abilities):
                aid = abilities[ability_slot]
                return ability_list[aid] if aid < len(ability_list) else f"ability#{aid}"
        
        return f"ability_slot_{ability_slot}"
    except:
        return f"ability_slot_{ability_slot}"


def decode_gender(gender_byte: int, species_id: int) -> str:
    """Decode gender byte. 0=default (use species ratio), 1=male, 2=female, 3=genderless."""
    if gender_byte == 1:
        return "Male"
    elif gender_byte == 2:
        return "Female"
    elif gender_byte == 3:
        return "Genderless"
    else:
        # Gender 0 means use species gender ratio - check if species is genderless
        # For now, return "Random" - could enhance to check personal data gender ratio
        return "Random"


def decode_trpoke(data: bytes, trainer_data: bytes = None) -> dict:
    """Decode a TRPoke file into human-readable format using text_tables.
    Gen IV: iv(u16) level(u16) species(u16) = 6B base.
    Gen V: iv(u8) ability(u8) level(u8) pad(u8) species(u16) form(u16) = 8B base."""
    if len(data) == 0:
        return {"pokemon": []}

    gen = text_gen or 5
    formats = TRPOKE_FORMATS_G4 if gen <= 4 else TRPOKE_FORMATS_G5

    # Determine template from TRData byte 0 if available
    template = 0
    if trainer_data and len(trainer_data) >= 1:
        template = trainer_data[0] & 0x03
    else:
        # Guess from file size
        for t in [3, 2, 1, 0]:
            if len(data) % formats[t] == 0 and len(data) // formats[t] > 0:
                template = t
                break

    pokemon_size = formats.get(template, formats[0])
    num_pokemon = len(data) // pokemon_size

    species_list = text_tables.get('species', [])
    moves_list = text_tables.get('moves', [])
    items_list = text_tables.get('items', [])

    pokemon = []
    for i in range(num_pokemon):
        off = i * pokemon_size
        if off + pokemon_size > len(data):
            break

        if gen <= 4:
            # Gen IV layout: iv(u16) level(u16) species(u16)
            iv_raw = struct.unpack_from('<H', data, off)[0]
            level = struct.unpack_from('<H', data, off + 2)[0]
            species_id = struct.unpack_from('<H', data, off + 4)[0]
            species_name = species_list[species_id] if species_id < len(species_list) else f"#{species_id}"
            ivs = iv_raw * 31 // 255 if iv_raw <= 255 else 31
            base_size = 6

            entry = {
                "species": species_name,
                "species_id": species_id,
                "level": level,
                "ivs": ivs,
            }
        else:
            # Gen V layout: iv(u8) ability(u8) level(u8) pad(u8) species(u16) form(u16)
            difficulty = data[off]
            ability_gender = data[off + 1]
            level = data[off + 2]
            species_id = struct.unpack_from('<H', data, off + 4)[0]
            form = struct.unpack_from('<H', data, off + 6)[0]

            ability_slot = (ability_gender >> 4) & 0xF
            gender_byte = ability_gender & 0xF
            species_name = species_list[species_id] if species_id < len(species_list) else f"#{species_id}"
            ability_name = get_ability_from_personal(species_id, ability_slot)
            gender = decode_gender(gender_byte, species_id)
            ivs = decode_trainer_iv(difficulty)
            base_size = 8

            entry = {
                "species": species_name,
                "species_id": species_id,
                "level": level,
                "ability": ability_name,
                "gender": gender,
                "ivs": ivs,
                "form": form,
            }

        if template & 2:  # Has held item
            item_id = struct.unpack_from('<H', data, off + base_size)[0]
            item_name = items_list[item_id] if item_id < len(items_list) else f"item#{item_id}"
            entry["held_item"] = item_name if item_id > 0 else "None"

        if template & 1:  # Has moves
            move_off = off + base_size + (2 if template & 2 else 0)
            moves = []
            for m in range(4):
                mid = struct.unpack_from('<H', data, move_off + m * 2)[0]
                mname = moves_list[mid] if mid < len(moves_list) else f"move#{mid}"
                moves.append(mname if mid > 0 else "---")
            entry["moves"] = moves

        pokemon.append(entry)

    return {"template": template, "count": num_pokemon, "pokemon": pokemon, "raw": data.hex()}


def decode_trdata(data: bytes, index: int = None) -> dict:
    """Decode a TRData entry. Format detected by size — no gen check needed.
    16B = Gen IV (flags, class, battle_type, npoke, items×4, ai_flags)
    20B = Gen V  (+ pad, prize_base, area_id, pad)"""
    if len(data) < 16:
        return None

    trainer_names = text_tables.get('trainer_names', [])
    trainer_classes = text_tables.get('trainer_classes', [])
    items_list = text_tables.get('items', [])

    BATTLE_TYPES = {0: "Single", 1: "Double", 2: "Triple", 3: "Rotation"}

    flags = data[0]
    trainer_class = data[1]
    battle_type = data[2]
    num_pokemon = data[3]
    has_moves = bool(flags & 1)
    has_items = bool(flags & 2)

    battle_items = []
    for i in range(4):
        item_id = struct.unpack_from('<H', data, 4 + i * 2)[0]
        if item_id > 0:
            item_name = items_list[item_id] if item_id < len(items_list) else f"item#{item_id}"
            battle_items.append(item_name)

    ai_flags_raw = struct.unpack_from('<I', data, 12)[0]
    # 20B = Gen V AI flag meanings, 16B = Gen IV AI flag meanings
    ai_flags = decode_ai_flags(ai_flags_raw, 5 if len(data) >= 20 else 4)
    class_name = trainer_classes[trainer_class] if trainer_class < len(trainer_classes) else f"class#{trainer_class}"

    # 20B entries have extra fields at 16-19; 16B entries don't
    prize_money_base = data[17] if len(data) > 17 else 0
    area_id = data[18] if len(data) > 18 else 0

    result = {
        "class": class_name,
        "battle_type": BATTLE_TYPES.get(battle_type, f"Unknown ({battle_type})"),
        "num_pokemon": num_pokemon,
        "has_custom_moves": has_moves,
        "has_held_items": has_items,
        "ai_flags": ai_flags,
        "battle_items": battle_items if battle_items else "None",
        "reward_multiplier": prize_money_base,
        "area_id": area_id,
        "raw": data.hex(),
    }

    # Player-named rivals: no real entry in trainer_names — the game replaces
    # their name at runtime. We inject canonical names by trainer class ID.
    # 16B = Gen IV games (HGSS, DPPt), 20B = Gen V games (BW, BW2)
    _RIVAL_NAMES_16B = {
        23:  "Silver",  # HGSS rival
        95:  "Barry",   # DP/Pt vs male player
        96:  "Barry",   # DP/Pt vs female player
    }
    if len(data) < 20 and trainer_class in _RIVAL_NAMES_16B:
        result["name"] = _RIVAL_NAMES_16B[trainer_class]
    elif index is not None and index < len(trainer_names):
        name = trainer_names[index].strip()
        if name:
            result["name"] = name

    # BW2 Hugh (class 145 = blank " Trainer") — 20B entries only
    if len(data) >= 20 and result.get("name") == "Rival" and trainer_class == 145:
        result["name"] = "Hugh"
        result["name_note"] = (
            "Default English name (player can rename at game start). "
            "Stored as 'Rival' placeholder in trainer_names. "
            "3 trdata files per story encounter = one per starter counter; "
            "6 files = 3 starters x 2 player genders (Nate/Rosa)."
        )

    return result






EV_YIELD_STATS = ['HP', 'Atk', 'Def', 'Spe', 'SpA', 'SpD']

EXP_GROWTH_NAMES = {0: "Medium Fast", 1: "Erratic", 2: "Fluctuating", 3: "Medium Slow", 4: "Fast", 5: "Slow"}

EGG_GROUP_NAMES = {
    0: "—", 1: "Monster", 2: "Water 1", 3: "Bug", 4: "Flying", 5: "Field",
    6: "Fairy", 7: "Grass", 8: "Human-Like", 9: "Water 3", 10: "Mineral",
    11: "Amorphous", 12: "Water 2", 13: "Ditto", 14: "Dragon", 15: "Undiscovered",
}

GENDER_RATIOS = {
    0: "100% ♂", 31: "87.5% ♂ / 12.5% ♀", 63: "75% ♂ / 25% ♀",
    127: "50% ♂ / 50% ♀", 191: "25% ♂ / 75% ♀", 223: "12.5% ♂ / 87.5% ♀",
    254: "100% ♀", 255: "Genderless",
}












def decode_personal(data: bytes, file_idx: int = 0):
    """Decode personal data. All gens: Gen 1 (28B, dex# prefix), Gen 2 (32B, dex# prefix),
    Gen 3 (28B), Gen 4 (44B), Gen 5 (76B). Gen 3-5 share bytes 0-8 layout."""
    if len(data) < 28 or data == b'\x00' * len(data):
        return None
    species_list = text_tables.get('species', [])
    type_list = text_tables.get('type_names', [])
    ability_list = text_tables.get('abilities', [])
    item_list = text_tables.get('items', [])
    gen = text_gen or 5

    # ── Gen 1/2: dex# at byte 0, different stat/type layout ──
    if gen <= 2:
        is_gen2 = len(data) >= 32
        if is_gen2:
            hp, atk, dfn, spe, spa, spd = data[1], data[2], data[3], data[4], data[5], data[6]
            t1_idx, t2_idx = data[7], data[8]
            catch_rate, base_exp = data[9], data[10]
            init_moves = [data[16], data[17], data[18], data[19]]
            growth = data[20]
        else:
            hp, atk, dfn, spe = data[1], data[2], data[3], data[4]
            spa = spd = data[5]  # Gen 1 Special → display as both SpA/SpD
            t1_idx, t2_idx = data[6], data[7]
            catch_rate, base_exp = data[8], data[9]
            init_moves = [data[15], data[16], data[17], data[18]]
            growth = data[19]
        bst = hp + atk + dfn + spe + spa + spd
        t1 = _GEN1_TYPE_NAMES.get(t1_idx, f'?{t1_idx}')
        t2 = _GEN1_TYPE_NAMES.get(t2_idx, f'?{t2_idx}')
        types_str = t1 if t1 == t2 else f'{t1} / {t2}'
        # Species name resolution (Gen 1 internal order vs Gen 2 dex order)
        sp_name = ''
        if is_gen2:
            sp_name = species_list[file_idx] if file_idx < len(species_list) else ''
        else:
            g1off = (current_rom or {}).get('gen1_offsets', {})
            dex_tb = g1off.get('dex_table_base', 0)
            if dex_tb and current_rom:
                rd = bytes(current_rom.get('data') or b'')
                for ci, dex in enumerate(rd[dex_tb: dex_tb + 190]):
                    if dex == file_idx and ci < len(species_list):
                        sp_name = species_list[ci]
                        break
        species_name = sp_name or f'Species #{file_idx}'
        mv_list = text_tables.get('moves', [])
        mv_strs = [mv_list[m] if 0 < m < len(mv_list) else f'Move#{m}' for m in init_moves if m]
        gr_name = _GEN1_GROWTH_RATES.get(growth, f'growth#{growth}')
        stat_line = (f'HP {hp} | Atk {atk} | Def {dfn} | SpA {spa} | SpD {spd} | Spe {spe}'
                     if is_gen2 else f'HP {hp} | Atk {atk} | Def {dfn} | Spc {spa} | Spe {spe}')
        lines = [f'{species_name} (#{file_idx})', f'{types_str} | BST {bst}', stat_line,
                 f'Catch: {catch_rate} | Base EXP: {base_exp} | Growth: {gr_name}']
        if mv_strs:
            lines.append(f'Initial moves: {" / ".join(mv_strs)}')
        return '\n'.join(lines)

    # ── Gen 3-5: stats at bytes 0-5, types at 6-7, catch at 8 ──
    hp, atk, dfn, spe, spa, spd = data[0], data[1], data[2], data[3], data[4], data[5]
    bst = hp + atk + dfn + spe + spa + spd
    type1, type2 = data[6], data[7]
    catch_rate = data[8]

    ev_raw = struct.unpack_from('<H', data, 0x0A)[0]
    evs = []
    for i, stat in enumerate(EV_YIELD_STATS):
        val = (ev_raw >> (i * 2)) & 3
        if val:
            evs.append(f"+{val} {stat}")

    if len(data) < 76:
        # Gen III (28B) and Gen IV (44B) share the same field layout for bytes 0x0C-0x17
        items = [struct.unpack_from('<H', data, 0x0C + i * 2)[0] for i in range(2)]
        held_labels = ['common', 'rare']
        gender = data[0x10]
        hatch_cycles = data[0x11]
        base_happiness = data[0x12]
        exp_growth = data[0x13]
        egg1, egg2 = data[0x14], data[0x15]
        abilities = [data[0x16], data[0x17]]
        ability_names = [ability_list[a] if a < len(ability_list) else f"ability#{a}" for a in abilities if a > 0]
    else:
        # Gen V (76B) — different offsets from 0x0C onward
        items = [struct.unpack_from('<H', data, 0x0C + i * 2)[0] for i in range(3)]
        held_labels = ['common', 'rare', 'hidden']
        gender = data[0x12]
        hatch_cycles = data[0x13]
        base_happiness = data[0x14]
        exp_growth = data[0x15]
        egg1, egg2 = data[0x16], data[0x17]
        ability_names = []
        for i in range(3):
            off = 0x18 + i
            if off < len(data):
                aid = data[off]
                if aid > 0:
                    name = ability_list[aid] if aid < len(ability_list) else f"ability#{aid}"
                    ability_names.append(f"{name} (Hidden)" if i == 2 else name)

    species_name = species_list[file_idx] if file_idx < len(species_list) else f"#{file_idx}"
    t1 = type_list[type1] if type1 < len(type_list) else f"type#{type1}"
    t2 = type_list[type2] if type2 < len(type_list) else f"type#{type2}"
    types_str = t1 if type1 == type2 else f"{t1} / {t2}"

    held_parts = []
    for label, item_id in zip(held_labels, items):
        if item_id > 0:
            iname = item_list[item_id] if item_id < len(item_list) else f"item#{item_id}"
            held_parts.append(f"{iname} ({label})")

    # Build output
    lines = [f"{species_name} (#{file_idx})"]
    lines.append(f"{types_str} | BST {bst}")
    lines.append(f"HP {hp} | Atk {atk} | Def {dfn} | SpA {spa} | SpD {spd} | Spe {spe}")
    lines.append(f"Abilities: {' / '.join(ability_names)}" if ability_names else "Abilities: ---")
    gender_str = GENDER_RATIOS.get(gender, f"ratio {gender}")
    lines.append(f"Gender: {gender_str} | Catch Rate: {catch_rate} | Hatch: {hatch_cycles} cycles | Happiness: {base_happiness}")
    eg1 = EGG_GROUP_NAMES.get(egg1, f"#{egg1}")
    eg2 = EGG_GROUP_NAMES.get(egg2, f"#{egg2}")
    egg_str = eg1 if egg1 == egg2 else f"{eg1} / {eg2}"
    lines.append(f"Growth: {EXP_GROWTH_NAMES.get(exp_growth, f'#{exp_growth}')} | Egg Groups: {egg_str}")
    if held_parts:
        lines.append(f"Held Items: {' / '.join(held_parts)}")
    if evs:
        lines.append(f"EVs: {', '.join(evs)}")

    # Height/weight (76B+ only, at 0x24/0x26)
    if len(data) >= 0x28:
        height_dm = struct.unpack_from('<H', data, 0x24)[0]
        weight_hg = struct.unpack_from('<H', data, 0x26)[0]
        lines.append(f"Height: {height_dm / 10.0}m | Weight: {weight_hg / 10.0}kg")

    # TM/HM compatibility — offset depends on data size
    if tm_table:
        moves_list = text_tables.get('moves', [])
        # 28B has no TM flags. 44B has them at 0x1C. 76B at 0x28.
        if len(data) >= 0x38:       # 76B: TM flags at 0x28
            tm_offset = 0x28
        elif len(data) >= 0x2C:     # 44B: TM flags at 0x1C
            tm_offset = 0x1C
        else:
            tm_offset = None
        if tm_offset and len(data) >= tm_offset + 16:
            tm_flags = data[tm_offset:tm_offset + 16]
            tms, hms = [], []
            for bit_idx, (label, move_id) in enumerate(tm_table):
                byte_pos = bit_idx // 8
                bit_pos = bit_idx % 8
                if tm_flags[byte_pos] & (1 << bit_pos):
                    move_name = moves_list[move_id] if move_id < len(moves_list) else f"move#{move_id}"
                    num = label[2:]  # "TM26" -> "26"
                    if label.startswith('HM'):
                        hms.append(f"{num} {move_name}")
                    else:
                        tms.append(f"{num} {move_name}")
            if tms:
                lines.append(f"TM: {' / '.join(tms)}")
            if hms:
                lines.append(f"HM: {' / '.join(hms)}")

    # Pokédex category + flavor text
    cat_list    = text_tables.get('pokedex_category', [])
    flavor_list = text_tables.get('pokedex_flavor',   [])
    if file_idx < len(cat_list) and cat_list[file_idx]:
        lines.append(f'Category: "{cat_list[file_idx]}"')
    if file_idx < len(flavor_list) and flavor_list[file_idx]:
        lines.append(f'Pokédex: {flavor_list[file_idx]}')

    # Ability descriptions
    ab_desc = text_tables.get('ability_descriptions', [])
    if ab_desc:
        ab_ids = []
        if len(data) >= 0x20:   # Gen V: abilities at 0x18, 0x1A, 0x1C
            ab_ids = [struct.unpack_from('<H', data, 0x18)[0],
                      struct.unpack_from('<H', data, 0x1A)[0],
                      struct.unpack_from('<H', data, 0x1C)[0]]
        elif len(data) >= 0x18: # Gen IV: abilities at 0x16, 0x17
            ab_ids = [data[0x16], data[0x17]]
        for ab in ab_ids:
            if ab and ab < len(ab_desc) and ab_desc[ab]:
                ab_name = ability_list[ab] if ab < len(ability_list) else f'ability#{ab}'
                lines.append(f'{ab_name}: {ab_desc[ab]}')

    return "\n".join(lines)


def _detect_learnset_format(data: bytes) -> str:
    """Detect learnset format from the data itself. No gen flag needed.
    'packed' = single u16 per entry: (level<<9)|move_id  (Gen 3/4)
    'paired' = two u16s per entry: move_id, level        (Gen 5)
    """
    if len(data) < 4:
        return 'packed'
    # Read first entry as a u16 pair. In paired format, u16[1] is a level (small).
    # In packed format, u16[1] is another packed entry (large raw value).
    second = struct.unpack_from('<H', data, 2)[0]
    if second == 0xFFFF or second == 0:
        return 'packed'  # terminator or empty — ambiguous, default packed
    # Paired format: second u16 is a level, always < 120
    # Packed format: second u16 is another (level<<9)|move_id, raw value usually > 500
    if second < 120:
        return 'paired'
    return 'packed'


def decode_learnset(data: bytes, file_idx: int = 0):
    """Decode learnset. All gens: u8 pairs (Gen 1/2), packed u16 (Gen 3/4), paired u16×2 (Gen 5)."""
    if len(data) < 2:
        return None
    species_list = text_tables.get('species', [])
    moves_list = text_tables.get('moves', [])
    species_name = species_list[file_idx] if file_idx < len(species_list) else f"#{file_idx}"

    gen = text_gen or 5
    moves = []
    if gen <= 2:
        # Gen 1/2: raw u8 pairs [level, move_id, level, move_id, ...]
        for i in range(0, len(data) - 1, 2):
            level, mid = data[i], data[i + 1]
            if level == 0: break
            move_name = moves_list[mid] if 0 < mid < len(moves_list) else f'Move#{mid}'
            moves.append((level, move_name))
    else:
        fmt = _detect_learnset_format(data)
        if fmt == 'packed':
            for i in range(0, len(data) - 1, 2):
                raw = struct.unpack_from('<H', data, i)[0]
                if raw == 0xFFFF or raw == 0: break
                move_id = raw & 0x1FF
                level = (raw >> 9) & 0x7F
                move_name = moves_list[move_id] if move_id < len(moves_list) else f"move#{move_id}"
                moves.append((level, move_name))
        else:
            for i in range(0, len(data) - 3, 4):
                move_id = struct.unpack_from('<H', data, i)[0]
                level = struct.unpack_from('<H', data, i + 2)[0]
                if move_id == 0xFFFF: break
                move_name = moves_list[move_id] if move_id < len(moves_list) else f"move#{move_id}"
                moves.append((level, move_name))

    lines = [f"{species_name} (#{file_idx}) — Learnset"]
    for level, move_name in moves:
        lines.append(f"  Lv{level:<4}{move_name}")
    if not moves:
        lines.append("  (none)")
    return "\n".join(lines)


EVOLUTION_METHODS = {
    0: None, 1: "happiness", 2: "happiness_day", 3: "happiness_night",
    4: "level_up", 5: "trade", 6: "trade_with_item", 7: "trade_for_species",
    8: "stone", 9: "atk>def", 10: "atk=def", 11: "atk<def",
    12: "personality_lo", 13: "personality_hi", 14: "ninjask", 15: "shedinja",
    16: "beauty", 17: "item_day", 18: "item_night", 19: "move",
    20: "party_species", 21: "level_male", 22: "level_female", 23: "level_electric_field",
    24: "level_mossy_rock", 25: "level_icy_rock", 26: "level_mossy_rock_2",
    27: "level_icy_rock_2", 28: "level_dark", 29: "spin", 30: "level_rain",
}

def decode_evolution(data: bytes, file_idx: int = 0):
    """Decode evolution table. Returns positional text."""
    if len(data) < 6 or data[:min(42,len(data))] == b'\x00' * min(42,len(data)):
        return None
    species_list = text_tables.get('species', [])
    item_list = text_tables.get('items', [])
    moves_list = text_tables.get('moves', [])
    species_name = species_list[file_idx] if file_idx < len(species_list) else f"#{file_idx}"
    evo_lines = []
    for i in range(7):
        off = i * 6
        if off + 6 > len(data): break
        method = struct.unpack_from('<H', data, off)[0]
        param = struct.unpack_from('<H', data, off + 2)[0]
        target = struct.unpack_from('<H', data, off + 4)[0]
        if method == 0 and target == 0:
            continue
        method_name = EVOLUTION_METHODS.get(method, f"method#{method}")
        target_name = species_list[target] if target < len(species_list) else f"#{target}"
        # Build condition string
        if method in (4, 9, 10, 11, 21, 22, 23, 24, 25, 26, 27, 28):
            cond = f"Lv{param}" if method == 4 else f"Lv{param}, {method_name}"
        elif method in (6, 8, 17, 18):
            item_name = item_list[param] if param < len(item_list) else f"item#{param}"
            cond = item_name
        elif method == 19:
            move_name = moves_list[param] if param < len(moves_list) else f"move#{param}"
            cond = f"knows {move_name}"
        elif method in (7, 20):
            sp = species_list[param] if param < len(species_list) else f"#{param}"
            cond = f"trade for {sp}" if method == 7 else f"with {sp} in party"
        elif method in (1, 2, 3):
            cond = method_name
        elif method == 5:
            cond = "trade"
        elif method == 16:
            cond = f"beauty {param}"
        elif method == 29:
            cond = "spin"
        else:
            cond = f"{method_name}" + (f" ({param})" if param else "")
        evo_lines.append(f"  → {target_name} ({cond})")
    if not evo_lines:
        return None
    lines = [f"{species_name} (#{file_idx}) — Evolutions"] + evo_lines
    return "\n".join(lines)



def decode_move_data(data: bytes, file_idx: int = 0):
    """Decode move data. Format detected by size — no gen check needed.
    12B = Gen III, 16B = Gen IV, 36B = Gen V."""
    if data == b'\x00' * len(data):
        return None
    type_list = text_tables.get('type_names', [])
    moves_list = text_tables.get('moves', [])
    move_name = moves_list[file_idx] if file_idx < len(moves_list) else f"move#{file_idx}"

    if len(data) < 16:
        # 12-byte entries: effect(1), power(1), type(1), accuracy(1), pp(1), ...
        move_type = data[2]
        power = data[1]
        accuracy = data[3]
        pp = data[4]
        type_name = type_list[move_type] if move_type < len(type_list) else f"type#{move_type}"
        category = '—'
        extras = []
    elif len(data) < 36:
        # 16-byte entries: category at byte 2 uses Gen IV mapping
        category = MOVE_CATEGORIES_G4.get(data[2], f"cat#{data[2]}")
        power = data[3]
        move_type = data[4]
        accuracy = data[5]
        pp = data[6]
        type_name = type_list[move_type] if move_type < len(type_list) else f"type#{move_type}"
        extras = []
    elif len(data) >= 36:
        move_type = data[0]
        category = MOVE_CATEGORIES_G5.get(data[2], f"cat#{data[2]}")
        power = data[3]
        accuracy = data[4]
        pp = data[5]
        priority = struct.unpack_from('b', data, 6)[0]
        multi_hit = data[7]
        effect_chance = data[10]
        type_name = type_list[move_type] if move_type < len(type_list) else f"type#{move_type}"
        extras = []
        if priority != 0:
            extras.append(f"{'+' if priority > 0 else ''}{priority} priority")
        if multi_hit > 0:
            lo, hi = multi_hit & 0xF, (multi_hit >> 4) & 0xF
            extras.append(f"{lo}-{hi} hits" if lo != hi else f"{lo} hits")
        if effect_chance > 0:
            extras.append(f"{effect_chance}% effect")
    else:
        return None

    pow_str = f"{power} pow" if power > 0 else "—"
    acc_str = f"{accuracy}%" if accuracy <= 100 else "—"
    line = f"{move_name} (#{file_idx})\n{type_name} | {category} | {pow_str} | {acc_str} | {pp} PP"
    if extras:
        line += f" | {' | '.join(extras)}"
    desc_list = text_tables.get('move_descriptions', [])
    if file_idx < len(desc_list) and desc_list[file_idx]:
        line += f"\n{desc_list[file_idx]}"
    return line


def decode_encounters(data: bytes) -> dict:
    """Decode wild encounter data. Routes to gen-specific decoder by size/gen."""
    gen = text_gen or 5

    if gen == 5:
        return _decode_encounters_gen5(data)
    elif gen == 4:
        if len(data) == 196:
            return _decode_encounters_hgss(data)
        elif len(data) == 424:
            return _decode_encounters_dpp(data)

    return None

# (species_id, form_idx) -> parenthetical label appended to species name in encounter display
# Form 0 entries are included when the base form has a meaningful name (e.g. Basculin Red-Striped)








def decode_items(data: bytes, file_idx: int = 0):
    """Decode item data. Gen IV: 34 bytes, price direct. Gen V: 36 bytes, price * 10."""
    items_list = text_tables.get('items', [])
    desc_list = text_tables.get('item_descriptions', [])

    name = items_list[file_idx] if file_idx < len(items_list) else f'Item #{file_idx}'
    description = desc_list[file_idx] if file_idx < len(desc_list) else ''

    if len(data) < 10:
        return None

    raw_price = struct.unpack_from('<H', data, 0)[0]
    is_gen5 = len(data) >= 36
    price = raw_price * 10 if is_gen5 else raw_price

    fling_power = data[6] if len(data) > 6 else 0

    lines = [name]
    lines.append("")
    if price > 0:
        lines.append(f"Buy: ${price:,}")
        lines.append(f"Sell: ${price // 2:,}")
    else:
        lines.append("Buy: Not sold in shops")
    if fling_power > 0:
        lines.append(f"Fling Power: {fling_power}")
    if description:
        lines.append("")
        lines.append(description)

    return "\n".join(lines)


def decode_contest(data: bytes, file_idx: int = 0):
    """Decode Gen IV Contest data (Diamond/Pearl/Platinum).
    File 0: Contest pokemon data (96 bytes per entry, 80 entries).
    """
    if file_idx != 0 or len(data) < 96:
        return None

    species_list = text_tables.get('species', [])
    moves_list = text_tables.get('moves', [])

    num_entries = len(data) // 96
    lines = ["Contest Hall", "", f"Pokemon: {num_entries}"]

    for i in range(num_entries):
        offset = i * 96
        entry_data = data[offset:offset + 96]

        species_id = struct.unpack_from('<H', entry_data, 8)[0]
        if species_id == 0 or species_id >= len(species_list):
            continue

        species_name = species_list[species_id]
        moves = []
        for m in range(4):
            move_id = struct.unpack_from('<H', entry_data, 12 + m * 2)[0]
            if move_id > 0 and move_id < len(moves_list):
                moves.append(moves_list[move_id])

        lines.append("")
        lines.append(f"  #{i+1:<4}{species_name}")
        if moves:
            lines.append(f"       {' / '.join(moves)}")

    return "\n".join(lines)





# ============ Template Formatters ============

GRASS_SLOT_RATES = [20, 20, 10, 10, 10, 10, 5, 5, 4, 4, 1, 1]
WATER_SLOT_RATES = [60, 30, 5, 4, 1]


def _consolidate_slots(entries, rates):
    """Consolidate species across encounter slots, summing rates."""
    combined = {}
    for i, entry in enumerate(entries):
        name = entry['species']
        rate = rates[i] if i < len(rates) else 0
        if name not in combined:
            combined[name] = {'rate': 0, 'levels': set()}
        combined[name]['rate'] += rate
        lvl_str = str(entry.get('level', 0))
        if '-' in lvl_str:
            lo, hi = lvl_str.split('-')
            combined[name]['levels'].update(range(int(lo), int(hi) + 1))
        else:
            combined[name]['levels'].add(int(lvl_str))
    result = []
    for name, d in sorted(combined.items(), key=lambda x: -x[1]['rate']):
        levels = sorted(d['levels'])
        lv = f"Lv{levels[0]}" if len(levels) <= 1 else f"Lv{levels[0]}-{levels[-1]}"
        result.append({'species': name, 'rate': d['rate'], 'level': lv})
    return result


def _format_section(entries, rates, header):
    """Format a consolidated encounter section."""
    consolidated = _consolidate_slots(entries, rates)
    if not consolidated:
        return ""
    lines = [f"\n{header}:"]
    for e in consolidated:
        lv = e['level'].replace('Lv', 'Lv. ')
        lines.append(f"  {e['species']:<20}{lv:<12}{e['rate']:>3}%")
    return "\n".join(lines)


def _format_encounter_hgss(decoded, file_idx):
    """Format HGSS encounter data as template text."""
    lines = []
    grass = decoded.get('grass', {})
    if grass and isinstance(grass, dict) and 'morning' in grass:
        times = {}
        for t in ['morning', 'day', 'night']:
            entries = grass.get(t, [])
            times[t] = {}
            for i, entry in enumerate(entries):
                name = entry['species']
                rate = GRASS_SLOT_RATES[i] if i < len(GRASS_SLOT_RATES) else 0
                if name not in times[t]:
                    times[t][name] = {'rate': 0, 'levels': set()}
                times[t][name]['rate'] += rate
                times[t][name]['levels'].add(entry['level'])
        all_species = set()
        for td in times.values():
            all_species.update(td.keys())
        species_info = []
        for sp in all_species:
            m_rate = times['morning'].get(sp, {}).get('rate', 0)
            d_rate = times['day'].get(sp, {}).get('rate', 0)
            n_rate = times['night'].get(sp, {}).get('rate', 0)
            all_levels = set()
            for t in ['morning', 'day', 'night']:
                if sp in times[t]:
                    all_levels.update(times[t][sp]['levels'])
            levels = sorted(all_levels)
            lv = f"Lv{levels[0]}" if len(levels) <= 1 else f"Lv{levels[0]}-{levels[-1]}"
            if m_rate == d_rate == n_rate and m_rate > 0:
                rate_str = f"{m_rate}%"
            else:
                rate_groups = {}
                for rate, tname in [(m_rate, 'Morning'), (d_rate, 'Day'), (n_rate, 'Night')]:
                    if rate > 0:
                        rate_groups.setdefault(rate, []).append(tname)
                parts = []
                for rate, tnames in sorted(rate_groups.items(), reverse=True):
                    parts.append(f"{rate}% ({', '.join(tnames)})")
                rate_str = " / ".join(parts)
            species_info.append({'species': sp, 'rate_str': rate_str, 'level': lv, 'sort_key': max(m_rate, d_rate, n_rate)})
        species_info.sort(key=lambda x: -x['sort_key'])
        lines.append("Grass (Default):")
        for si in species_info:
            lv = si['level'].replace('Lv', 'Lv. ')
            lines.append(f"  {si['species']:<20}{lv:<12}{si['rate_str']}")

    water_sections = [
        ('surf', 'Surf (Default)'), ('rock_smash', 'Rock Smash'),
        ('old_rod', 'Fishing (Old Rod)'), ('good_rod', 'Fishing (Good Rod)'),
        ('super_rod', 'Fishing (Super Rod)'),
    ]
    for key, header in water_sections:
        entries = decoded.get(key, [])
        if entries:
            section = _format_section(entries, WATER_SLOT_RATES, header)
            if section:
                lines.append(section)

    sound = decoded.get('sound', {})
    if sound:
        hoenn = sound.get('hoenn', [])
        sinnoh = sound.get('sinnoh', [])
        if hoenn:
            lines.append(f"\nGrass (Hoenn Sound):\n  {', '.join(hoenn)}")
        if sinnoh:
            lines.append(f"\nGrass (Sinnoh Sound):\n  {', '.join(sinnoh)}")

    return "\n".join(lines).strip() if lines else None


def _format_encounter_gen5(decoded, file_idx):
    """Format Gen V encounter data as template text."""
    seasons_data = decoded.get('seasons', None)
    if seasons_data:
        return _format_encounter_gen5_seasonal(seasons_data, file_idx)

    lines = []
    location = decoded.get('location', '')
    if location:
        lines.append(f"Location: {location}\n")

    sections = [
        ('grass', 'Grass (Default)', GRASS_SLOT_RATES),
        ('double_grass', 'Dark Grass', GRASS_SLOT_RATES),
        ('special_grass', 'Shaking Grass', GRASS_SLOT_RATES),
        ('surf', 'Surf (Default)', WATER_SLOT_RATES),
        ('special_surf', 'Rippling Water', WATER_SLOT_RATES),
        ('fishing', 'Fishing (Default)', WATER_SLOT_RATES),
        ('special_fishing', 'Fishing (Rippling)', WATER_SLOT_RATES),
    ]
    for key, header, rates in sections:
        entries = decoded.get(key, [])
        if entries:
            section = _format_section(entries, rates, header)
            if section:
                lines.append(section)

    return "\n".join(lines).strip() if lines else None


def _format_encounter_gen5_seasonal(seasons, file_idx):
    """Format Gen V seasonal encounters with inline season notes."""
    section_types = [
        ('grass', 'Grass (Default)', GRASS_SLOT_RATES),
        ('double_grass', 'Dark Grass', GRASS_SLOT_RATES),
        ('special_grass', 'Shaking Grass', GRASS_SLOT_RATES),
        ('surf', 'Surf (Default)', WATER_SLOT_RATES),
        ('special_surf', 'Rippling Water', WATER_SLOT_RATES),
        ('fishing', 'Fishing (Default)', WATER_SLOT_RATES),
        ('special_fishing', 'Fishing (Rippling)', WATER_SLOT_RATES),
    ]
    season_names = ['Spring', 'Summer', 'Fall', 'Winter']
    lines = []
    location = seasons[0].get('location', '') if seasons else ''
    if location:
        lines.append(f"Location: {location}\n")

    for key, header, rates in section_types:
        season_consolidated = []
        has_data = False
        for s in seasons:
            entries = s.get(key, [])
            if entries:
                has_data = True
                season_consolidated.append(_consolidate_slots(entries, rates))
            else:
                season_consolidated.append([])
        if not has_data:
            continue
        all_species = set()
        for sc in season_consolidated:
            for e in sc:
                all_species.add(e['species'])
        species_info = []
        for sp in all_species:
            season_rates = []
            all_levels = set()
            for si, sc in enumerate(season_consolidated):
                rate = 0
                for e in sc:
                    if e['species'] == sp:
                        rate = e['rate']
                        lv = e['level'].replace('Lv', '')
                        if '-' in lv:
                            lo, hi = lv.split('-')
                            all_levels.update(range(int(lo), int(hi) + 1))
                        else:
                            all_levels.add(int(lv))
                        break
                season_rates.append(rate)
            levels = sorted(all_levels)
            lv = f"Lv{levels[0]}" if len(levels) <= 1 else f"Lv{levels[0]}-{levels[-1]}"
            if all(r == season_rates[0] for r in season_rates):
                rate_str = f"{season_rates[0]}%"
            else:
                rate_groups = {}
                for i, rate in enumerate(season_rates):
                    if rate > 0 and i < len(season_names):
                        rate_groups.setdefault(rate, []).append(season_names[i])
                parts = []
                for rate, snames in sorted(rate_groups.items(), reverse=True):
                    parts.append(f"{rate}% ({', '.join(snames)})")
                rate_str = " / ".join(parts)
            species_info.append({'species': sp, 'rate_str': rate_str, 'level': lv, 'sort_key': max(season_rates)})
        species_info.sort(key=lambda x: -x['sort_key'])
        lines.append(f"\n{header}:")
        for si in species_info:
            lv = si['level'].replace('Lv', 'Lv. ')
            lines.append(f"  {si['species']:<20}{lv:<12}{si['rate_str']}")

    return "\n".join(lines).strip() if lines else None


def _format_encounter_dpp(decoded, file_idx):
    """Format DPPt encounter data as template text."""
    lines = []
    grass = decoded.get('grass', [])
    if grass:
        section = _format_section(grass, GRASS_SLOT_RATES, "Grass (Default)")
        if section:
            lines.append(section)
    for key, label in [('swarm', 'Swarm'), ('day_replacements', 'Day'), ('night_replacements', 'Night'), ('radar', 'Radar')]:
        species = decoded.get(key, [])
        if species:
            names = species if isinstance(species[0], str) else [e['species'] for e in species]
            lines.append(f"\nGrass ({label}):\n  {', '.join(names)}")
    water_sections = [
        ('surf', 'Surf (Default)'), ('surf_special', 'Surf (Special)'),
        ('old_rod', 'Fishing (Old Rod)'), ('good_rod', 'Fishing (Good Rod)'),
        ('super_rod', 'Fishing (Super Rod)'),
    ]
    for key, header in water_sections:
        entries = decoded.get(key, [])
        if entries:
            section = _format_section(entries, WATER_SLOT_RATES, header)
            if section:
                lines.append(section)
    return "\n".join(lines).strip() if lines else None


def format_encounter(decoded, file_idx):
    """Format encounter data as template text with clean title line."""
    if not decoded:
        return None

    # Route by gen first, then format variant
    gen = text_gen or 5
    if gen == 5:
        body = _format_encounter_gen5(decoded, file_idx)  # handles seasons internally
    elif gen == 4:
        if isinstance(decoded.get('grass', {}), dict) and 'morning' in decoded.get('grass', {}):
            body = _format_encounter_hgss(decoded, file_idx)
        else:
            body = _format_encounter_dpp(decoded, file_idx)
    else:
        return None

    if not body:
        return None

    # Prepend title line (location name or generic)
    location = decoded.get('location', '')
    _loc_clean = location.strip() if location else ''
    _printable = sum(c.isascii() and c.isprintable() for c in _loc_clean)
    title = _loc_clean if _loc_clean and _printable >= len(_loc_clean) * 0.75 else f"Encounter Zone #{file_idx}"
    # Strip "Location: " prefix if the formatter already added it
    body = body.lstrip('\n')
    if body.startswith('Location:'):
        body = body.split('\n', 1)[1].lstrip('\n') if '\n' in body else ''
    return f"{title}\n{body}" if body else title


def _format_trainer_card(trdata: dict, pokemon: list, file_idx: int, prize: int = 0) -> str:
    """Format a trainer as positional text. Works for all gens — no gen check needed.
    trdata: dict with 'class', 'name', 'battle_type', 'ai_flags', 'battle_items'
    pokemon: list of dicts with 'species', 'species_id', 'level', 'ivs', 'held_item', 'moves', etc.
    """
    class_name = trdata.get('class', '???')
    trainer_name = trdata.get('name', f'Trainer #{file_idx}')
    battle_type = trdata.get('battle_type', 'Single')

    # Look up location for special trainers (Gym Leaders, E4, Champions, etc.)
    game_code = current_rom['header']['game_code'] if current_rom else None
    location = get_trainer_location(game_code, class_name, trainer_name) if game_code else None

    if location:
        lines = [f"{class_name} {trainer_name} - {location}"]
    else:
        lines = [f"{class_name} {trainer_name}"]

    if battle_type != 'Single':
        lines[0] += f"  [{battle_type} Battle]"

    _chal_delta = get_bw2_challenge_delta(file_idx, game_code)

    if pokemon:
        lines.append("")
        lines.append("Team:")

    for poke in pokemon:
        species = poke.get('species', '???')
        species_id = poke.get('species_id', 0)
        level = poke.get('level', '?')
        held = poke.get('held_item', None)

        # Form resolution
        form_idx = poke.get('form', 0)
        if form_idx:
            form_label = _FORM_NAMES.get((species_id, form_idx), '')
            if form_label:
                species = f"{species}-{form_label}"

        if _chal_delta:
            header = f"{species} (Lv. {level + _chal_delta})"
        else:
            header = f"{species} (Lv. {level})"
        if held and held != 'None':
            header += f"  [{held}]"
        lines.append(header)

        # Ability / IVs / Gender line
        ability = poke.get('ability')
        iv_val = poke.get('ivs')
        gender = poke.get('gender')
        gender_sym = {'Male': '♂', 'Female': '♀', 'Genderless': '⚲'}.get(gender, '')
        meta_parts = []
        if ability:
            meta_parts.append(f"Ability: {ability}")
        if iv_val is not None:
            meta_parts.append(f"IVs: {iv_val}/{iv_val}/{iv_val}/{iv_val}/{iv_val}/{iv_val}")
        if gender_sym:
            meta_parts.append(gender_sym)
        if meta_parts:
            lines.append('  '.join(meta_parts))

        moves = poke.get('moves', [])
        if moves:
            move_str = " / ".join(m for m in moves if m != '---')
            if move_str:
                lines.append(move_str)

    # Footer metadata
    footer = []
    if prize > 0:
        footer.append(f"Prize: ¥{prize:,}")

    items = trdata.get('battle_items', 'None')
    if isinstance(items, list) and items:
        footer.append(f"Items Used in Battle: {', '.join(items)}")
    else:
        footer.append("No Items Used")

    ai = trdata.get('ai_flags', [])
    if ai and ai != ['None']:
        footer.append(f"AI: {', '.join(ai)}")

    if footer:
        lines.append("")
        lines.append(" | ".join(footer))

    return "\n".join(lines)



def format_trainer(file_idx):
    """Load trainer data and format as positional text. NDS path — loads from NARCs."""
    td_path = next((p for p, r in narc_roles.items() if r == 'trdata'), None)
    tp_path = next((p for p, r in narc_roles.items() if r == 'trpoke'), None)
    if not td_path or not tp_path:
        return None

    try:
        td_narc = _get_narc(td_path)
        if file_idx >= len(td_narc.files):
            return None
        td_data = td_narc.files[file_idx]
        trdata = decode_trdata(td_data, file_idx)
        if not trdata:
            return None

        tp_narc = _get_narc(tp_path)
        if file_idx >= len(tp_narc.files):
            return None
        tp_data = tp_narc.files[file_idx]
        trpoke = decode_trpoke(tp_data, td_data)

        # Calculate prize money
        template = td_data[0] & 0x03
        gen = text_gen or 5
        _fmts = TRPOKE_FORMATS_G4 if gen <= 4 else TRPOKE_FORMATS_G5
        poke_size = _fmts.get(template, _fmts[0])
        num_pokemon = len(tp_data) // poke_size
        prize = 0
        if num_pokemon > 0:
            last_off = (num_pokemon - 1) * poke_size
            if len(tp_data) >= last_off + 4:
                last_level = struct.unpack_from('<H', tp_data, last_off + 2)[0]
            else:
                last_level = tp_data[last_off + 2] if last_off + 3 <= len(tp_data) else 0
            prize = trdata.get("reward_multiplier", 0) * last_level * 4

        return _format_trainer_card(trdata, trpoke.get('pokemon', []), file_idx, prize)
    except Exception as e:
        import traceback
        return f"[format_trainer error] {e}\n{traceback.format_exc()}"


def _format_hex(data: bytes, base_offset: int = 0) -> str:
    """Format bytes as readable hex dump: offset | hex | ascii."""
    lines = []
    for i in range(0, len(data), 16):
        chunk = data[i:i + 16]
        hex_part = ' '.join(f'{b:02X}' for b in chunk)
        ascii_part = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
        lines.append(f"{base_offset + i:08X}  {hex_part:<48}  {ascii_part}")
    return '\n'.join(lines)


def _notes_for_path(path: str) -> str:
    """Return flipnote notes matching this path. Surfaces before raw bytes so models read what's known first."""
    if not current_flipnote:
        return ''
    notes = current_flipnote['data'].get('notes', {})
    hits = []
    narc_part = path.rsplit(':', 1)[0] if ':' in path else path
    for key in (path, narc_part):
        if key in notes:
            n = notes[key]
            hits.append(f"[known: {key}] {n.get('description', n) if isinstance(n, dict) else n}")
    # arm9: surface arm9/* and patches/* notes
    if path.lower().startswith('arm9') and not hits:
        for key, n in notes.items():
            if key.startswith('arm9/') or key.startswith('patches/'):
                desc = n.get('description', n) if isinstance(n, dict) else n
                hits.append(f"[known: {key}] {str(desc)[:140]}")
    return '\n'.join(hits)


def _auto_decode(path: str, data: bytes, _rom=None):
    """Auto-decode known structures by role, not hardcoded paths."""
    # GBA/GB/GBC: binary ROM — role:key paths, no NARCs
    # Use _rom snapshot to avoid race with BFS background threads switching current_rom
    _active = _rom or current_rom
    if _active and _active['type'] in ('gba', 'gb', 'gbc'):
        rom_data = bytes(_active.get('data') or b'')
        if not rom_data:
            return {"_unknown": True, "reason": "ROM data not loaded"}
        offsets = (_active.get('gen1_offsets', {})
                   if _active['type'] in ('gb', 'gbc')
                   else _active.get('gen3_offsets', {}))
        # Lazy-discover Gen I offsets if missing (e.g. cross-ROM prefix used before spotlight)
        if _active['type'] in ('gb', 'gbc') and not offsets.get('personal_base'):
            _discover_gen1_tables()
            offsets = _active.get('gen1_offsets', {})
        gc = _active['header']['game_code']
        if ':' not in path:
            off = int(path, 16) if path.startswith('0x') else int(path)
            return {"path": path, "hex": _format_hex(rom_data[off: off + 64], off)}
        role, key = path.split(':', 1)
        key = key.strip()

        # ── Gen I / II (GB / GBC) ──────────────────────────────────────────
        if _active['type'] in ('gb', 'gbc'):
            if role == 'personal':
                base = offsets.get('personal_base', 0)
                if not base:
                    return {"_unknown": True, "reason": f"personal_base not discovered for {gc}"}
                dex_tb   = offsets.get('dex_table_base', 0)
                dex_list = list(rom_data[dex_tb: dex_tb + 190]) if dex_tb else []
                if key.isdigit():
                    dex_num = int(key)
                else:
                    # Name → internal constant → dex number
                    sp_list  = text_tables.get('species', [])
                    const    = next((i for i, n in enumerate(sp_list)
                                     if n and n.strip() == key.strip()), -1)
                    if const < 0:
                        const = next((i for i, n in enumerate(sp_list)
                                      if n and n.strip().upper() == key.strip().upper()), -1)
                    if const < 0:
                        return {"_unknown": True, "reason": f"Species not found: {key}"}
                    sz_peek = offsets.get('personal_size', 28)
                    if sz_peek == 32:
                        # Gen II: species table is in dex order → const == dex_num
                        dex_num = const
                    else:
                        dex_num = dex_list[const] if const < len(dex_list) else 0
                sz = offsets.get('personal_size', 28)
                max_dex = 251 if sz == 32 else 151
                if dex_num < 1 or dex_num > max_dex:
                    return {"_unknown": True, "reason": f"Invalid dex number: {dex_num}"}
                entry = rom_data[base + (dex_num - 1) * sz: base + dex_num * sz]
                decoded = decode_personal(entry, dex_num)
                if not decoded:
                    return {"_unknown": True, "reason": f"Could not decode personal for dex#{dex_num}"}
                return {"_card": True, "path": path, "text": decoded,
                        "game_code": gc}

            elif role == 'trainer':
                is_gen2 = offsets.get('personal_size', 28) == 32
                if is_gen2:
                    # Gen II (GBC): use pointer table + multi-format decoder
                    if key.isdigit():
                        class_id = int(key)
                    else:
                        ku = key.strip().upper()
                        # 1. Individual trainer name index (e.g. RED, FALKNER, WHITNEY)
                        name_map = offsets.get('trainer_name_to_class', {})
                        class_id = name_map.get(ku, name_map.get(key.strip(), -1))
                        if class_id < 0:
                            # 2. Class name text table (e.g. LEADER, RIVAL, CHAMPION)
                            cls_list = text_tables.get('trainer_classes', [])
                            class_id = next((i for i, n in enumerate(cls_list)
                                             if n and n.strip().upper() == ku), -1)
                    if class_id < 0:
                        return {"_unknown": True, "reason": f"Trainer not found: {key}"}
                    decoded = decode_gen12_trainer_class(class_id, label=key if not key.isdigit() else None)
                else:
                    # Gen I (GB): sequential gym leaders or pointer table
                    _GYM_IDX = {
                        # EN names — storage order: Brock,Misty,Surge,Erika,Koga,Sabrina,Blaine,Giovanni(hideout,gym)
                        'brock': 0, 'misty': 1, 'lt. surge': 2, 'surge': 2,
                        'erika': 3, 'koga': 4, 'blaine': 5, 'sabrina': 6,
                        'giovanni': 8,  # index 7=hideout, 8=gym (the real fight)
                        # JP names
                        'タケシ': 0, 'カスミ': 1, 'マチス': 2, 'エリカ': 3,
                        'キョウ': 4, 'カツラ': 5, 'ナツメ': 6, 'サカキ': 8,
                        # Alt spellings
                        'takeshi': 0, 'kasumi': 1, 'matis': 2, 'kyou': 4,
                        'katsura': 5, 'natsume': 6, 'sakaki': 8,
                    }
                    gym_offs = offsets.get('gym_leader_offsets', [])
                    if key.isdigit():
                        class_id = int(key)
                        decoded = decode_gen12_trainer_class(class_id)
                    elif key.lower() in _GYM_IDX:
                        idx = _GYM_IDX[key.lower()]
                        if idx >= len(gym_offs):
                            return {"_unknown": True, "reason": f"Gym leader #{idx} not discovered"}
                        decoded = decode_gen12_trainer_class(None, direct_off=gym_offs[idx], label=key)
                    else:
                        cls_list = text_tables.get('trainer_classes', [])
                        class_id = next((i for i, n in enumerate(cls_list)
                                         if n and n.strip().upper() == key.strip().upper()), -1)
                        if class_id < 0:
                            return {"_unknown": True, "reason": f"Trainer not found: {key}"}
                        decoded = decode_gen12_trainer_class(class_id, label=key)
                if not decoded:
                    return {"_unknown": True, "reason": f"No trainer data for {key}"}
                return {"_card": True, "path": path, "text": decoded,
                        "game_code": gc}

            elif role in ('learnsets', 'learnset'):
                const = _gen1_resolve_const(key, offsets)
                if const < 0:
                    return {"_unknown": True, "reason": f"Species not found: {key}"}
                _, ls_bytes = _extract_gen12_evo_learnset(const)
                if not ls_bytes:
                    return {"_unknown": True, "reason": f"Learnset not discovered for {gc}"}
                return {"_card": True, "path": path, "text": decode_learnset(ls_bytes, const), "game_code": gc}

            elif role in ('evolutions', 'evolution'):
                const = _gen1_resolve_const(key, offsets)
                if const < 0:
                    return {"_unknown": True, "reason": f"Species not found: {key}"}
                evo_lines, _ = _extract_gen12_evo_learnset(const)
                if evo_lines is None:
                    return {"_unknown": True, "reason": f"Evolution data not discovered for {gc}"}
                sp_list = text_tables.get('species', [])
                sp_name = sp_list[const] if const < len(sp_list) else f'Species#{const}'
                text = f'{sp_name} \u2014 Evolutions\n' + ('\n'.join(evo_lines) if evo_lines else '  Does not evolve')
                return {"_card": True, "path": path, "text": text, "game_code": gc}

            elif role == 'items':
                it_list = text_tables.get('items', [])
                if not it_list:
                    return {"_unknown": True, "reason": "Item names not scanned for this ROM"}
                if key.isdigit():
                    idx = int(key)
                else:
                    idx = next((i for i, n in enumerate(it_list)
                                if n and n.strip() == key.strip()), -1)
                if idx < 0 or idx >= len(it_list):
                    return {"_unknown": True, "reason": f"Item not found: {key}"}
                return {"_card": True, "path": path,
                        "text": f"Item #{idx}: {it_list[idx]}", "game_code": gc}

            elif role in ('encounters', 'encounter'):
                is_gen2 = offsets.get('personal_size', 28) == 32
                if is_gen2:
                    # Gen 2: key = "GROUP/MAP" or route name
                    _GEN2_MAP_NAMES = {
                        # ── Johto indoor (group 3) ──────────────────────────────
                        (3,2):'SPROUT TOWER', (3,3):'SPROUT TOWER',
                        (3,5):'TIN TOWER', (3,6):'TIN TOWER', (3,7):'TIN TOWER',
                        (3,8):'TIN TOWER', (3,9):'TIN TOWER', (3,10):'TIN TOWER',
                        (3,11):'TIN TOWER', (3,12):'TIN TOWER',
                        (3,13):'BURNED TOWER', (3,14):'BURNED TOWER',
                        (3,15):'NATIONAL PARK',
                        (3,22):'RUINS OF ALPH', (3,27):'RUINS OF ALPH',
                        (3,16):'UNION CAVE', (3,17):'UNION CAVE', (3,19):'UNION CAVE',
                        (3,18):'SLOWPOKE WELL', (3,20):'SLOWPOKE WELL',
                        (3,21):'ILEX FOREST',
                        (3,23):'MT. MORTAR', (3,24):'MT. MORTAR',
                        (3,25):'MT. MORTAR', (3,26):'MT. MORTAR',
                        (3,28):'ICE PATH', (3,29):'ICE PATH',
                        (3,30):'ICE PATH', (3,31):'ICE PATH', (3,32):'ICE PATH',
                        (3,33):'WHIRL ISLANDS', (3,34):'WHIRL ISLANDS',
                        (3,35):'WHIRL ISLANDS', (3,36):'WHIRL ISLANDS',
                        (3,37):'WHIRL ISLANDS', (3,38):'WHIRL ISLANDS',
                        (3,39):'WHIRL ISLANDS', (3,40):'WHIRL ISLANDS',
                        (3,41):'SILVER CAVE', (3,42):'SILVER CAVE',
                        (3,43):'SILVER CAVE',
                        (3,44):'DARK CAVE', (3,45):'DARK CAVE',
                        # ── Johto outdoor routes ─────────────────────────────────
                        (24,3):'ROUTE 29',
                        (26,1):'ROUTE 30', (26,2):'ROUTE 31',
                        (10,1):'ROUTE 32', (8,6):'ROUTE 33',
                        (11,1):'ROUTE 35',
                        (10,2):'ROUTE 36', (10,3):'ROUTE 36', (10,4):'ROUTE 37',
                        (1,12):'ROUTE 38', (1,13):'ROUTE 39',
                        (2,5):'ROUTE 42',  (9,5):'ROUTE 43',
                        (5,8):'MT. MORTAR OUTSIDE',
                        (5,9):'ROUTE 46',
                        (19,2):'ROUTE 28',
                        (2,6):'SILVER CAVE OUTSIDE',
                        # ── Kanto caves/special (group 3 except routes) ─────────
                        (3,75):"DIGLETT'S CAVE",
                        (3,76):'MT. MOON',
                        (3,78):'ROCK TUNNEL', (3,79):'ROCK TUNNEL',
                        (3,82):'VICTORY ROAD',
                        (3,74):'TOHJO FALLS',
                        # ── Kanto routes (from kanto_grass.asm map constants) ────
                        (13,1):'ROUTE 1',
                        (23,1):'ROUTE 2',
                        (14,1):'ROUTE 3',
                        (7,12):'ROUTE 4',
                        (25,1):'ROUTE 5',  (12,1):'ROUTE 6',
                        (21,1):'ROUTE 7',  (18,1):'ROUTE 8',
                        (7,13):'ROUTE 9',  (7,14):'ROUTE 10',
                        (18,3):'ROUTE 10',
                        (12,2):'ROUTE 11',
                        (17,1):'ROUTE 13', (17,2):'ROUTE 14', (17,3):'ROUTE 15',
                        (21,2):'ROUTE 16', (21,3):'ROUTE 17', (17,4):'ROUTE 18',
                        (6,7):'ROUTE 21',
                        (23,2):'ROUTE 22',
                        (7,15):'ROUTE 24', (7,16):'ROUTE 25',
                        (24,1):'ROUTE 26', (24,2):'ROUTE 27',
                        (19,1):'ROUTE 28',
                    }
                    if '/' in key:
                        parts = key.split('/')
                        map_group, map_number = int(parts[0]), int(parts[1])
                    else:
                        ku = key.strip().upper().replace('_', ' ')
                        match = next(((g,m) for (g,m),n in _GEN2_MAP_NAMES.items()
                                      if n.upper() == ku), None)
                        if match is None:
                            return {"_unknown": True, "reason": f"Route not found: {key}"}
                        map_group, map_number = match
                    decoded = decode_gen2_encounters(map_group, map_number)
                    if not decoded:
                        return {"_unknown": True, "reason": f"No encounter data for {map_group}/{map_number}"}
                    loc_name = _GEN2_MAP_NAMES.get((map_group, map_number), f'Map {map_group}/{map_number}')
                    decoded = decoded.replace(f'Map {map_group}/{map_number}', loc_name)
                    return {"_card": True, "path": path, "text": decoded, "game_code": gc}
                # Gen 1: key = map index (int) or route name
                _GEN1_MAP_NAMES = {
                    12:'ROUTE 1', 13:'ROUTE 2', 14:'VIRIDIAN FOREST', 15:'ROUTE 3',
                    16:'MT. MOON', 17:'MT. MOON', 18:'ROUTE 4', 19:'ROUTE 5',
                    20:'ROUTE 6', 21:'ROUTE 7', 22:'ROUTE 8', 23:'ROUTE 9',
                    24:'ROUTE 10', 25:'ROUTE 10', 26:'ROUTE 11', 27:'ROUTE 12',
                    28:'ROUTE 13', 29:'ROUTE 14', 32:'ROUTE 16', 33:'ROUTE 24',
                    34:'ROUTE 25', 35:'ROCK TUNNEL', 36:'ROCK TUNNEL',
                    51:'VIRIDIAN FOREST', 59:'SEAFOAM ISLANDS', 60:'SEAFOAM ISLANDS',
                    61:'SEAFOAM ISLANDS', 82:'ROUTE 15',
                }
                if key.isdigit():
                    map_idx = int(key)
                else:
                    ku = key.strip().upper().replace(' ', '')
                    map_idx = next((k for k, v in _GEN1_MAP_NAMES.items()
                                    if v.upper().replace(' ', '') == ku), -1)
                if map_idx < 0:
                    return {"_unknown": True, "reason": f"Map not found: {key}"}
                decoded = decode_gen1_encounters(map_idx)
                if not decoded:
                    return {"_unknown": True, "reason": f"No encounter data for map {map_idx}"}
                loc_name = _GEN1_MAP_NAMES.get(map_idx, f'Map #{map_idx}')
                decoded = decoded.replace(f'Map #{map_idx}', loc_name)
                return {"_card": True, "path": path, "text": decoded, "game_code": gc}

            return {"_unknown": True, "reason": f"Role '{role}' not implemented for Gen I/II"}
        # ── Gen III (GBA) — existing handlers below ───────────────────────

        if role == 'personal':
            base = offsets.get('personal_base', 0)
            if not base:
                return {"_unknown": True, "reason": f"personal offset not discovered for {gc}"}
            idx = int(key) if key.isdigit() else next(
                (i for i, n in enumerate(text_tables.get('species', [])) if n.strip().upper() == key.upper()), -1)
            if idx < 0:
                return {"_unknown": True, "reason": f"Species not found: {key}"}
            return decode_personal(rom_data[base + idx * 28: base + idx * 28 + 28], idx)
        elif role == 'trainer':
            # Resolve key to entry_offset — by index or name search
            entry_offset = -1
            if key.isdigit():
                offsets_list = text_tables.get('trainer_offsets', [])
                idx = int(key)
                if idx >= len(offsets_list):
                    return {"_unknown": True, "reason": f"Trainer index {idx} out of range ({len(offsets_list)} trainers)"}
                entry_offset = offsets_list[idx]
            else:
                reverse = {v: k for k, v in _GEN3_CHARMAP_EN.items() if isinstance(v, str) and len(v) == 1}
                try:
                    name_bytes = bytes([reverse[c] for c in key.upper()]) + b'\xff'
                except KeyError:
                    return {"_unknown": True, "reason": f"Unsupported characters in name: {key}"}
                search_start = 0
                while True:
                    name_off = rom_data.find(name_bytes, search_start)
                    if name_off < 4: break
                    chunk = rom_data[name_off - 4: name_off + 36]
                    count_v = struct.unpack_from('<I', chunk, 32)[0]
                    ptr_v   = struct.unpack_from('<I', chunk, 36)[0]
                    if chunk[0] <= 3 and chunk[1] < 200 and 1 <= count_v <= 6 and 0x08000000 <= ptr_v <= 0x0AFFFFFF:
                        entry_offset = name_off - 4
                        break
                    search_start = name_off + 1
            if entry_offset < 0:
                return {"_unknown": True, "reason": f"Trainer not found: {key}"}
            # ONE shared path: read header, sanity check, decode, format
            header = rom_data[entry_offset: entry_offset + 40]
            flags = header[0]
            party_count = struct.unpack_from('<I', header, 32)[0]
            party_ptr   = struct.unpack_from('<I', header, 36)[0]
            party_off   = party_ptr - 0x08000000 if party_ptr >= 0x08000000 else 0
            has_moves, has_item = bool(flags & 1), bool(flags & 2)
            msize = 18 if (has_moves and has_item) else 16 if has_moves else 8
            # Sanity-check party data (Emerald gym leaders: flags say 18B but actually 16B)
            if party_off and msize != 16:
                for _pi in range(min(party_count, 6)):
                    _lv = struct.unpack_from('<H', rom_data, party_off + _pi*msize + 2)[0]
                    _sp = struct.unpack_from('<H', rom_data, party_off + _pi*msize + 4)[0]
                    if _lv > 100 or _sp == 0:
                        msize = 16
                        flags = 1
                        has_moves, has_item = True, False
                        header = bytes([1]) + header[1:]
                        break
            party_data = rom_data[party_off: party_off + party_count * msize] if party_off else b''
            decoded = decode_gen3_trainer(header, party_data, flags)
            if not decoded:
                return {"_unknown": True, "reason": f"Trainer not found: {key}"}
            sp_list = text_tables.get('species', [])
            mv_list = text_tables.get('moves', [])
            it_list = text_tables.get('items', [])
            cls_list = text_tables.get('trainer_classes', [])
            cls_id = decoded['trainer_class']
            rival_cls = GAME_INFO.get(gc, {}).get('rival_cls', set())
            raw_name = decoded.get('name', '???')
            trainer_name = GAME_INFO.get(gc, {}).get('rival_aliases', [None])[0] or raw_name if cls_id in rival_cls else raw_name
            trdata = {
                'class': cls_list[cls_id] if cls_id < len(cls_list) else '',
                'name': trainer_name,
                'battle_type': 'Double' if decoded.get('is_double') else 'Single',
                'ai_flags': decode_ai_flags(decoded.get('ai_flags', 0), gen=3) or ['None'],
                'battle_items': [it_list[i] if i < len(it_list) else f"item#{i}" for i in decoded.get('battle_items', [])] or 'None',
            }
            pokemon = []
            for m in decoded.get('party', []):
                sp_id = m['species']
                iv_val = m.get('iv', 0) * 31 // 255 if m.get('iv') else 0
                entry = {'species': sp_list[sp_id] if sp_id < len(sp_list) else f"#{sp_id}",
                         'species_id': sp_id, 'level': m['level'],
                         'ivs': iv_val or None}
                if m.get('item'):
                    it_id = m['item']
                    entry['held_item'] = it_list[it_id] if it_id < len(it_list) else f"item#{it_id}"
                if m.get('moves'):
                    entry['moves'] = [mv_list[mv-1] if 0 < mv <= len(mv_list) else f"move#{mv}" for mv in m['moves']]
                pokemon.append(entry)
            return _format_trainer_card(trdata, pokemon, 0)
        elif role == 'learnset':
            ptr_table = offsets.get('learnset_ptr_table', 0)
            if not ptr_table:
                return {"_unknown": True, "reason": f"learnset offset not discovered for {gc}"}
            idx = int(key) if key.isdigit() else next(
                (i for i, n in enumerate(text_tables.get('species', [])) if n.strip().upper() == key.upper()), -1)
            if idx < 0:
                return {"_unknown": True, "reason": f"Species not found: {key}"}
            # Gen 3 learnset table has one extra entry before dex order begins; use idx+1
            ptr = struct.unpack_from('<I', rom_data, ptr_table + (idx + 1) * 4)[0]
            off = ptr - 0x08000000
            # Extract learnset bytes, adjusting move IDs from 1-indexed (ROM) to 0-indexed (text table)
            buf = bytearray()
            i = off
            while i + 2 <= len(rom_data):
                raw = struct.unpack_from('<H', rom_data, i)[0]
                if raw == 0xFFFF or raw == 0:
                    buf += struct.pack('<H', 0xFFFF)
                    break
                move_id = (raw & 0x1FF) - 1  # ROM is 1-indexed, text table is 0-indexed
                level = (raw >> 9) & 0x7F
                buf += struct.pack('<H', (level << 9) | (move_id & 0x1FF))
                i += 2
            return decode_learnset(bytes(buf), idx)
        elif role == 'move':
            base = offsets.get('move_base', 0)
            if not base:
                return {"_unknown": True, "reason": f"move offset not discovered for {gc}"}
            idx = int(key) if key.isdigit() else next(
                (i for i, n in enumerate(text_tables.get('moves', [])) if n.strip().upper() == key.upper()), -1)
            if idx < 0:
                return {"_unknown": True, "reason": f"Move not found: {key}"}
            # Gen III: ROM index 0=blank, 1=Pound. Text table index 0=Pound. Add 1 to convert.
            rom_idx = idx + 1
            return decode_move_data(rom_data[base + rom_idx * 12: base + rom_idx * 12 + 12], idx)
        elif role in ('encounters', 'encounter'):
            enc_tbl = offsets.get('enc_table_offset', 0)
            if not enc_tbl:
                return {"_unknown": True, "reason": f"encounter table not discovered for {gc}"}
            parts = key.replace('_', '/').split('/')
            if len(parts) != 2 or not all(p.isdigit() for p in parts):
                return {"_unknown": True, "reason": "Key format: group/num (e.g. 3/16)"}
            tgt_g, tgt_n = int(parts[0]), int(parts[1])
            offset = enc_tbl
            header = None
            while offset + 20 <= len(rom_data):
                mg, mn = rom_data[offset], rom_data[offset+1]
                if mg == 0xFF: break
                if mg == tgt_g and mn == tgt_n:
                    header = rom_data[offset:offset+20]; break
                offset += 20
            if not header:
                return {"_unknown": True, "reason": f"No encounters for group {tgt_g} / map {tgt_n}"}
            sp_list = text_tables.get('species', [])
            LAND_RATES  = [20,20,10,10,10,10,5,5,4,4,1,1]
            WATER_RATES = [60,30,5,4,1]
            def read_slots(ptr, n_slots, rates):
                # WildPokemonInfo: rate(u8) + pad(3B) + mons_ptr(u32 GBA pointer)
                # mons_ptr -> flat WildPokemon[n_slots] array
                if not (0x08000000 <= ptr <= 0x0AFFFFFF): return None, None
                off = ptr - 0x08000000
                if off + 8 > len(rom_data): return None, None
                enc_rate = rom_data[off]
                mons_ptr = struct.unpack_from('<I', rom_data, off + 4)[0]
                if not (0x08000000 <= mons_ptr <= 0x0AFFFFFF): return None, None
                mi = mons_ptr - 0x08000000
                if mi + n_slots*4 > len(rom_data): return None, None
                slots = []
                for i in range(n_slots):
                    p = mi + i*4
                    mn_lv, mx_lv = rom_data[p], rom_data[p+1]
                    sp = struct.unpack_from('<H', rom_data, p+2)[0]
                    name = sp_list[sp] if sp < len(sp_list) else f"#{sp}"
                    slots.append((name, mn_lv, mx_lv, rates[i] if i < len(rates) else 0))
                return enc_rate, slots
            land_ptr  = struct.unpack_from('<I', header, 4)[0]
            water_ptr = struct.unpack_from('<I', header, 8)[0]
            rock_ptr  = struct.unpack_from('<I', header, 12)[0]
            fish_ptr  = struct.unpack_from('<I', header, 16)[0]
            lines = [f"Group {tgt_g} / Map {tgt_n}"]
            for label, ptr, n_slots, rates in [
                    ('Grass', land_ptr, 12, LAND_RATES),
                    ('Surf',  water_ptr, 5, WATER_RATES),
                    ('Rock Smash', rock_ptr, 5, WATER_RATES)]:
                enc_rate, slots = read_slots(ptr, n_slots, rates)
                if slots:
                    lines.append(f"\n{label} (rate {enc_rate}):")
                    for name, mn_lv, mx_lv, rate in slots:
                        lv = f"Lv. {mn_lv}-{mx_lv}" if mn_lv != mx_lv else f"Lv. {mn_lv}"
                        lines.append(f"  {name:<16} {lv:<12} {rate}%")
            # Fishing: 8-byte header {encounterRate(u8), pad(3B), mons_ptr(u32)}
            # mons_ptr → flat WildPokemon array: 2 Old Rod + 3 Good Rod + 5 Super Rod slots
            if 0x08000000 <= fish_ptr <= 0x0AFFFFFF:
                fi = fish_ptr - 0x08000000
                if fi + 8 <= len(rom_data):
                    fish_rate = rom_data[fi]
                    mons_ptr  = struct.unpack_from('<I', rom_data, fi + 4)[0]
                    if 0x08000000 <= mons_ptr <= 0x0AFFFFFF:
                        mi = mons_ptr - 0x08000000
                        rod_labels = ['Old Rod']*2 + ['Good Rod']*3 + ['Super Rod']*5
                        fish_slots = []
                        for k in range(10):
                            if mi + k*4 + 4 > len(rom_data): break
                            mn_lv = rom_data[mi + k*4]
                            mx_lv = rom_data[mi + k*4 + 1]
                            sp    = struct.unpack_from('<H', rom_data, mi + k*4 + 2)[0]
                            if sp == 0 or mn_lv == 0 or mn_lv > 100: break
                            name  = sp_list[sp] if sp < len(sp_list) else f"#{sp}"
                            fish_slots.append((rod_labels[k] if k < len(rod_labels) else 'Rod', name, mn_lv, mx_lv))
                        if fish_slots:
                            lines.append(f"\nFishing (rate {fish_rate}):")
                            for rod, name, mn_lv, mx_lv in fish_slots:
                                lv = f"Lv. {mn_lv}-{mx_lv}" if mn_lv != mx_lv else f"Lv. {mn_lv}"
                                lines.append(f"  [{rod}] {name:<16} {lv}")
            return "\n".join(lines)

        elif role == 'items':
            item_base = offsets.get('item_base', 0)
            if not item_base:
                return {"_unknown": True, "reason": f"item offset not discovered for {gc}"}
            it_list = text_tables.get('items', [])
            idx = int(key) if key.isdigit() else next(
                (i for i, n in enumerate(it_list) if n and n.strip().upper() == key.upper()), -1)
            if idx < 0:
                return {"_unknown": True, "reason": f"Item not found: {key}"}
            # item_base points to blank entry (game ID 0); it_list[0] = Master Ball (game ID 1)
            game_idx = idx + 1
            entry = rom_data[item_base + game_idx * 44 : item_base + game_idx * 44 + 44]
            if len(entry) < 44:
                return {"_unknown": True, "reason": f"Item #{game_idx} out of range"}
            price     = struct.unpack_from('<H', entry, 0x10)[0]
            hold_eff  = entry[0x12]
            hold_prm  = entry[0x13]
            desc_ptr  = struct.unpack_from('<I', entry, 0x14)[0]
            importance = entry[0x18]
            pocket    = entry[0x1A]
            # Read description via GBA pointer
            desc = ''
            if 0x08000000 <= desc_ptr <= 0x0A000000:
                desc_off = desc_ptr - 0x08000000
                desc_bytes = []
                while desc_off < len(rom_data) and rom_data[desc_off] != _GEN3_EOS:
                    desc_bytes.append(rom_data[desc_off]); desc_off += 1
                desc = ''.join(_GEN3_CHARMAP_EN.get(b, '') for b in desc_bytes).strip()
            pockets = {1:'Items', 2:'Key Items', 3:'Poké Balls', 4:'TMs & HMs', 5:'Berries'}
            item_name = it_list[idx] if idx < len(it_list) else f"Item #{game_idx}"
            lines = [f"{item_name}  (#{game_idx})"]
            lines.append(f"Pocket: {pockets.get(pocket, f'#{pocket}')}  |  Price: ₽{price:,}" +
                         (f"  |  Key Item" if importance else ""))
            if hold_eff:
                lines.append(f"Hold Effect: #{hold_eff}" + (f" (param {hold_prm})" if hold_prm else ""))
            if desc:
                lines.append(desc)
            return "\n".join(lines)

        elif role == 'evolution':
            evo_base = offsets.get('evo_base', 0)
            if not evo_base:
                return {"_unknown": True, "reason": f"evolution offset not discovered for {gc}"}
            idx = int(key) if key.isdigit() else next(
                (i for i, n in enumerate(text_tables.get('species', [])) if n.strip().upper() == key.upper()), -1)
            if idx < 0:
                return {"_unknown": True, "reason": f"Species not found: {key}"}
            # Gen 3 evolution method numbers differ from Gen 4/5.
            # pokefirered: 1=happiness, 2=happiness_day, 3=happiness_night, 4=level,
            # 5=trade, 6=trade_with_item, 7=item(stone), 8-10=level_atk, 11-14=special, 15=beauty
            _G3_EVO = {1:'happiness',2:'happiness_day',3:'happiness_night',4:'level_up',
                       5:'trade',6:'trade_with_item',7:'item',8:'level_attack_gt_defense',
                       9:'level_attack_eq_defense',10:'level_attack_lt_defense',
                       11:'level',12:'level',13:'level',14:'level',15:'beauty'}
            sp_list = text_tables.get('species', [])
            it_list = text_tables.get('items', [])
            sp_name = sp_list[idx] if idx < len(sp_list) else f"#{idx}"
            block = rom_data[evo_base + idx * 40 : evo_base + idx * 40 + 40]
            evo_lines = []
            for s in range(0, 40, 8):
                if s + 6 > len(block): break
                method = struct.unpack_from('<H', block, s)[0]
                param  = struct.unpack_from('<H', block, s + 2)[0]
                target = struct.unpack_from('<H', block, s + 4)[0]
                if method == 0 or target == 0: continue
                tgt = sp_list[target] if target < len(sp_list) else f"#{target}"
                mname = _G3_EVO.get(method, f"method#{method}")
                if method == 4 or method in (8,9,10,11,12,13,14):
                    cond = f"Lv{param}"
                elif method == 7:
                    cond = it_list[param - 1] if 0 < param <= len(it_list) else f"item#{param}"
                elif method in (5, 6):
                    iname = it_list[param - 1] if 0 < param <= len(it_list) else f"item#{param}"
                    cond = f"trade" + (f" holding {iname}" if method == 6 else "")
                elif method == 15:
                    cond = f"beauty {param}"
                else:
                    cond = mname
                evo_lines.append(f"  → {tgt} ({cond})")
            if not evo_lines:
                return None
            lines = [f"{sp_name} (#{idx}) — Evolutions"] + evo_lines
            return "\n".join(lines)
        return {"_unknown": True, "reason": f"Unknown role '{role}'. Use personal/trainer/learnset/move/evolution"}

    if not narc_roles or ':' not in path:
        return {"_unknown": True, "reason": "no role mapping", "hint": f"scope({path}) for raw bytes"}

    narc_part, idx_str = path.rsplit(':', 1)
    narc_part = narc_part.strip('/')
    file_idx = int(idx_str)
    role = narc_roles.get(narc_part)
    if not role:
        return {"_unknown": True, "reason": f"no role for {narc_part}", "hint": f"scope({path}) or dowse(name='...', narc_path='{narc_part}')"}

    rom = current_rom.get('rom')

    try:
        if role in ('trpoke', 'trdata'):
            formatted = format_trainer(file_idx)
            return formatted
        elif role == 'personal':
            return decode_personal(data, file_idx)
        elif role == 'learnsets':
            return decode_learnset(data, file_idx)
        elif role == 'evolutions':
            return decode_evolution(data, file_idx)
        elif role == 'move_data':
            return decode_move_data(data, file_idx)
        elif role == 'encounters':
            decoded = decode_encounters(data)
            if decoded:
                # Resolve location name — game's own runtime mapping
                gc = current_rom['header']['game_code'] if current_rom else ''
                loc_id = 0
                enc_loc = current_rom.get('enc_loc', {})
                if enc_loc:
                    loc_id = enc_loc.get(file_idx, 0)
                elif gc in ('ADA', 'APA'):  # Diamond / Pearl — ARM9 table at 0xED738
                    arm9 = current_rom.get('arm9_data')
                    if arm9 and 0xED738 + file_idx * 2 + 2 <= len(arm9):
                        loc_id = struct.unpack_from('<H', arm9, 0xED738 + file_idx * 2)[0]
                else:
                    # Fallback: BFS auto-built table
                    auto_table = _auto_enc_loc.get(gc, {})
                    if auto_table:
                        loc_id = auto_table.get(file_idx, 0)
                if loc_id:
                    location_names = text_tables.get('location_names', [])
                    decoded['location'] = location_names[loc_id] if loc_id < len(location_names) else f'Area #{file_idx}'
                else:
                    decoded['location'] = f'Area #{file_idx}'
                formatted = format_encounter(decoded, file_idx)
                return formatted if formatted else decoded
        elif role == 'pwt_defs':
            return decode_pwt_tournament_def(data, file_idx)
        elif role.startswith('pwt_rosters'):
            return decode_pwt_roster(data, file_idx, roster_role=role)
        elif role.startswith('pwt_trainers'):
            return decode_pwt_trainer_config(data, file_idx, trainer_role=role)
        elif role in _PWT_POOL_ROLES:
            pool = role[4:].replace('_b', '-B').replace('_', ' ').title()
            return decode_pwt(data, 'champions' in role, pool, file_idx)
        elif role == 'subway_pokemon':
            return decode_pwt(data, False, 'Battle Subway', file_idx)
        elif role == 'subway_trainers':
            return decode_pwt_roster(data, file_idx, roster_role='subway_trainers')
        elif role == 'battle_tower_pokemon':
            return decode_pwt(data, True, 'Battle Tower', file_idx)
        elif role == 'battle_tower_trainers':
            return decode_pwt_roster(data, file_idx, roster_role='battle_tower_trainers')
        elif role == 'pokeathlon_performance':
            return decode_pokeathlon_performance(data, file_idx)
        elif role == 'contest':
            return decode_contest(data, file_idx)
        elif role == 'items':
            return decode_items(data, file_idx)
    except Exception as e:
        return {"_error": f"Decoder crash: {e}", "role": role}

    return None







# ============ Tool Handlers ============

def _discover_enc_loc_gen5(gc: str) -> dict:
    """Discover enc→loc via species fingerprinting + story-progression formula.
    Formula: loc = enc + C0 - N  where C0 = loc_anchor - enc_anchor,
    N = non-route enc files consumed before this point.
    Proved: enc NARC files are in story order; consecutive routes have loc+1 per enc+1.
    Handles Gen 5 (BW1/BW2) and Gen 4 HGSS.
    """
    if not current_rom or current_rom['type'] != 'nds':
        return {}

    if gc in ('IPK', 'IPG'):  # HGSS — read sMapHeaders from decompressed ARM9
        # sMapHeaders: 540 × 24-byte structs. byte 0 = wildEncounterBank (enc file, 0xFF=none).
        # byte 18 = mapsec (u8 loc_id). Anchor: map33=Route29 (enc=1,mapsec=0xB1),
        # map34=Route30 (enc=3,mapsec=0xB2). Verified against pret/pokeheartgold decomp.
        arm9 = current_rom.get('arm9_data', b'')
        enc_map = {}
        pos = 0
        while pos < len(arm9) - 24 * 5:
            X = arm9.find(b'\x01', pos)
            if X < 0: break
            if (X + 42 < len(arm9) and arm9[X+18] == 0xB1 and
                    arm9[X+24] == 3 and arm9[X+42] == 0xB2):
                T = X - 33 * 24
                if T >= 0 and arm9[T] == 0xFF and arm9[T+18] == 0:
                    for i in range(540):
                        s = T + i * 24
                        if s + 24 > len(arm9): break
                        ef, ms = arm9[s], arm9[s + 18]
                        if ef != 0xFF and ms > 0:
                            enc_map[ef] = ms
                    if len(enc_map) >= 100:
                        return enc_map
                    enc_map = {}
            pos = X + 1
        return {}  # ARM9 scan found nothing — enc_loc unavailable for this ROM

    enc_path = next((p for p, r in (narc_roles or {}).items() if r == 'encounters'), '')
    if not enc_path:
        enc_path = GAME_INFO.get(gc, {}).get('narcs', {}).get('encounters', '')
    try:
        import ndspy.narc as _n
        enc_narc = _n.NARC(current_rom['rom'].getFileByName(enc_path))
    except Exception:
        return {}

    def gsp(f):  # grass species + min level
        sp, lv = set(), 99
        for j in range(12):
            p = 8 + j * 4
            if p + 4 <= len(f):
                s = struct.unpack_from('<H', f, p)[0] & 0x7FF
                if s: sp.add(s); lv = min(lv, f[p + 2])
        return sp, lv

    # Anchors: (species_subset, loc, lv_min, lv_max)
    A = [(frozenset([504, 509]),  15,  4,  7),   # BW1 R2: Patrat+Purrloin lv4-7 — checked before R1
         (frozenset([504, 506]), 14,  0,  5),   # BW1 R1: Patrat+Lillipup, no Purrloin
         (frozenset([39, 505, 507]), 14, 50, 99), # BW2 R1 post: Jigglypuff+Watchog+Herdier
         (frozenset([193,505,507,509,520,523]),16,10,99), # BW2 R3 post
         (frozenset([504, 509]), 124, 0,  4),    # BW2 R19: Patrat+Purrloin lv2
         (frozenset([55,183,337,338,591]),127,35,55), # BW2 R22: Golduck+Lunatone+Amoonguss
         (frozenset([592, 458, 223]), 126, 0, 99)]  # BW2 R21 water: Frillish+Mantyke+Remoraid

    # Cave fingerprints: species_subset → loc
    C = {frozenset([524,527]):53, frozenset([525,527,610]):37, frozenset([524,527,447]):54,
         frozenset([605,607]):56, frozenset([536,616,618]):57, frozenset([536,618]):57,
         frozenset([532,546]):33, frozenset([551,557]):34, frozenset([551,562]):35,
         frozenset([532,533]):38, frozenset([619,622]):39,     frozenset([42,354]):133,
         frozenset([325,326,451]):132, frozenset([525,632]):137, frozenset([19,41,88]):129}

    enc_map, fsp = {}, {}
    anchors = []
    for i, f in enumerate(enc_narc.files):
        if len(f) < 232: continue
        sp, lv = gsp(f)
        if not sp: continue
        fsp[i] = (sp, lv)
        for fp, loc, lo, hi in A:
            if fp.issubset(sp) and lo <= lv <= hi:
                anchors.append((i, loc)); break
        else:
            for csp, cloc in C.items():
                if csp.issubset(sp): enc_map[i] = cloc; break

    anchors.sort()
    for ai, aloc in anchors:
        enc_map[ai] = aloc

    for idx, (enc_a, loc_a) in enumerate(anchors):
        enc_b = anchors[idx + 1][0] if idx + 1 < len(anchors) else len(enc_narc.files)
        C0, N = loc_a - enc_a, 0
        for e in range(enc_a, enc_b):
            if e in enc_map and e != enc_a: N += 1; continue
            pl = e + C0 - N
            if (14 <= pl <= 31) or (93 <= pl <= 128): enc_map[e] = pl
            elif e not in fsp: N += 1
            else: N += 1

    return enc_map


async def spotlight(path: str) -> dict:
    """Open a ROM file for exploration. Multiple ROMs can be open simultaneously."""
    global current_rom, current_flipnote, text_gen, _user_active_gc

    ensure_dirs()
    rom_type = detect_rom_type(path)

    # Peek at header to check if already loaded
    if rom_type == 'nds':
        header = read_nds_header(path)
    elif rom_type == '3ds':
        header = read_3ds_header(path)
    elif rom_type in ('gba', 'gbc', 'gb'):
        header = read_gba_header(path) if rom_type == 'gba' else read_gb_header(path)
    else:
        return {"error": f"Unknown ROM type: {path}"}

    gc = header['game_code']

    # Already loaded? Switch to it — but still show the full summary card
    if gc in loaded_roms:
        _save_active_state()
        _restore_state(gc)
        # Run any post-load scans that may not have existed when ROM was first loaded
        if gc in ('IRE', 'IRD') and 'tournament_classes' not in text_tables:
            _scan_pwt_tournaments(text_tables)
            _build_pwt_maps(text_tables)
            _save_active_state()
        game_info = GAME_INFO.get(gc, {})
        narcs = game_info.get("narcs", {})
        icr_done = bool(eonet_labels.get(gc))
        text = _build_spotlight_text(
            gc, header, rom_type, game_info, narcs,
            {'status': 'ok', 'gen': text_gen,
             'detected': {k: True for k, v in text_tables.items() if isinstance(v, list) and isinstance(k, str)}},
            current_flipnote['path'],
            tm_table or [],
            list(loaded_roms.keys()), icr_done
        )
        _user_active_gc = gc
        return text

    # Save current ROM state before loading new one
    _save_active_state()

    text_table_result = {}

    if rom_type == 'nds':
        rom = ndspy.rom.NintendoDSRom.fromFile(path)

        fpn_path = find_flipnote(gc)
        if fpn_path:
            fpn_path = upgrade_to_shared_flipnote(gc)
        else:
            structure, rom_stats = build_nds_structure(rom, path)
            fpn_path = create_flipnote(
                gc, (GAME_INFO.get(gc, {}).get('title') or header['game_title']).replace(' Nintendo','').replace(' Game Freak','').strip(), header['region'],
                header['region_char'], structure, rom_stats, header['is_english']
            )

        # Decompress ARM9 via blz (ndspy does NOT decompress BLZ-compressed ARM9)
        raw_arm9 = bytes(rom.arm9)
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix='.bin') as tmp:
                tmp.write(raw_arm9)
                tmp_path = tmp.name
            decompress_arm9(tmp_path)
            with open(tmp_path, 'rb') as f:
                arm9_data = bytearray(f.read())
            Path(tmp_path).unlink(missing_ok=True)
        except Exception:
            # Fallback: use raw if blz fails (might not be compressed)
            arm9_data = bytearray(raw_arm9)

        # --- BW2 Challenge/Easy Mode stat recalc patch (silent, in-memory only) ---
        # The vanilla B2/W2 ROM has a bug where difficulty modes change enemy levels
        # but don't recalculate stats. This patches the decompressed ARM9 in memory
        # so the model never encounters the broken routine.
        _BW2_PATCH_OFFSET = 0x145D0
        _BW2_PATCH_LEN = 172
        if gc in ('IRE', 'IRD') and len(arm9_data) > _BW2_PATCH_OFFSET + _BW2_PATCH_LEN:
            _b2_patch = bytes.fromhex('F8B582B00090151C081CFF F7ABF9F7F761FFF7F7A1FF041C281C00F077FB071C4F210098002289004254002C01D1501E474301 2C36D04F21009889004754002001900198810018484058810000984018456A002D21D0281C002407F0E5FB00281BDD281C211C07F067FC061C9E210022 04F05AFB0004000CC21900 2A00DC0122301C9E2104F062FB301C05F059FA281C641C07F0C9FB8442E3DB019840 1C019002 28CED302B0F8BD68000902'.replace(' ',''))
            _w2_patch = bytes.fromhex('F8B582B00090151C081CFF F7ABF9F7F761FFF7F7A1FF041C281C00F08DFB071C4F210098002289004254002C01D1501E474301 2C36D04F21009889004754002001900198810018484058810000984018456A002D21D0281C002407F0FBFB00281BDD281C211C07F07DFC061C9E210022 04F070FB0004000CC21900 2A00DC0122301C9E2104F078FB301C05F06FFA281C641C07F0DFFB8442E3DB019840 1C019002 28CED302B0F8BD94000902'.replace(' ',''))
            # Verified fingerprinting (tested against 3 ROMs):
            #   Prologue bytes 0-7: F8 B5 82 B0 00 90 15 1C (same in all BW2 ROMs)
            #   Byte 30: 0x00 = unpatched, 0x07 = already patched
            #   Byte 28: 0x77 = Black 2, 0x8D = White 2
            _prologue = bytes([0xF8,0xB5,0x82,0xB0,0x00,0x90,0x15,0x1C])
            _cur_pro = bytes(arm9_data[_BW2_PATCH_OFFSET:_BW2_PATCH_OFFSET + 8])
            if _cur_pro == _prologue and arm9_data[_BW2_PATCH_OFFSET + 30] == 0x00:
                # Unpatched. Byte 28 tells us which game's patch to apply.
                _byte28 = arm9_data[_BW2_PATCH_OFFSET + 28]
                if _byte28 == 0x77:    # Black 2
                    arm9_data[_BW2_PATCH_OFFSET:_BW2_PATCH_OFFSET + _BW2_PATCH_LEN] = _b2_patch
                elif _byte28 == 0x8D:  # White 2
                    arm9_data[_BW2_PATCH_OFFSET:_BW2_PATCH_OFFSET + _BW2_PATCH_LEN] = _w2_patch

        arm7_data = bytearray(rom.arm7)

        # Load and decompress all ARM9 overlays
        overlays = _load_overlays(rom)

        current_rom = {
            'type': 'nds', 'path': path, 'rom': rom, 'header': header,
            'arm9_data': arm9_data, 'arm7_data': arm7_data,
            'overlays': overlays,
            'compression_state': {}
        }

        # Pre-set text_gen before bootstrapping text tables
        game_info = GAME_INFO.get(gc, {})
        text_gen = game_info.get('gen')

        # Bootstrap text tables (Gen IV/V)
        try:
            text_table_result = bootstrap_text_tables(rom, gc)
        except Exception as e:
            text_table_result = {"error": str(e)}

        # Discover TM→move table from ARM9
        tm_count = _discover_tm_table()
        if tm_count:
            text_table_result["tm_table"] = f"{tm_count} TM/HM entries found"

        # Encounter→Location mapping
        # HGSS/Gen 5: species fingerprinting + story-progression formula; HGSS falls back to decomp table
        #   Formula: loc = enc + C0 - N_consumed
        #   C0 = loc_anchor - enc_anchor (derived from species anchor)
        #   N = number of non-route enc files consumed before this point
        #   Proved: enc NARC files are in story-progression order; consecutive routes have loc+1 per enc+1
        if gc in ('IPK', 'IPG', 'IRB', 'IRA', 'IRE', 'IRD'):  # HGSS + Black/White 1 & 2
            current_rom['enc_loc'] = _discover_enc_loc_gen5(gc)
        elif gc == 'CPU':  # Platinum — ARM9 flat table at 0xF0D4A
            arm9 = current_rom.get('arm9_data')
            if arm9:
                enc_path = game_info.get('narcs', {}).get('encounters', '')
                try:
                    enc_count = len(_get_narc(enc_path).files) if enc_path else 0
                except:
                    enc_count = 183
                enc_loc = {}
                for i in range(enc_count):
                    off = 0xF0D4A + i * 2
                    if off + 2 <= len(arm9):
                        v = struct.unpack_from('<H', bytes(arm9), off)[0]
                        if v > 0:
                            enc_loc[i] = v
                current_rom['enc_loc'] = enc_loc

        # Seed narc_roles from GAME_INFO so decoders work before BFS completes
        game_info = GAME_INFO.get(gc, {})
        for role, narc_path in game_info.get('narcs', {}).items():
            if role != 'text' and narc_path not in narc_roles:
                narc_roles[narc_path] = role

    elif rom_type == '3ds':
        fh, romfs_files = open_3ds_romfs(path)
        current_rom = {
            'type': '3ds', 'path': path, 'header': header,
            'romfs_fh': fh, 'romfs_files': romfs_files,
            'compression_state': {}
        }

        fpn_path = find_flipnote(gc)
        if fpn_path:
            fpn_path = upgrade_to_shared_flipnote(gc)
        else:
            structure, rom_stats = build_3ds_structure(romfs_files, fh, path)
            fpn_path = create_flipnote(
                gc, (GAME_INFO.get(gc, {}).get('title') or header['game_title']).replace(' Nintendo','').replace(' Game Freak','').strip(),
                header['region'], header['region_char'], structure, rom_stats, header.get('is_english', True)
            )

        game_info = GAME_INFO.get(gc, {})
        text_gen = game_info.get('gen')
        narcs = game_info.get('narcs', {})

        # Bootstrap text: load GARC files via xoleon, feed into existing decoder
        text_garc = narcs.get('text', '')
        if text_garc and text_garc in romfs_files:
            garc_files = read_garc_all(fh, romfs_files[text_garc][0])
            try:
                text_table_result = bootstrap_text_tables(None, gc, file_list=garc_files)
            except Exception as e:
                text_table_result = {"error": str(e)}

        for role, np in narcs.items():
            if role != 'text' and np not in narc_roles:
                narc_roles[np] = role

    else:  # gba/gbc/gb
        tm_table.clear()  # GB/GBC/GBA has no ARM9 TM table — clear any stale NDS data
        # Load raw ROM binary into memory
        with open(path, 'rb') as _f:
            rom_data = bytearray(_f.read())

        current_rom = {
            'type': rom_type, 'path': path, 'header': header,
            'data': rom_data,
        }

        fpn_path = find_flipnote(gc)
        if not fpn_path:
            fpn_path = create_flipnote(
                gc, (GAME_INFO.get(gc, {}).get('title') or header['game_title']).replace(' Nintendo','').replace(' Game Freak','').strip(), header['region'],
                header['region_char'], [], {}, header.get('is_english', True)
            )

        # Bootstrap text tables by scanning the binary
        try:
            text_table_result = bootstrap_text_tables_binary(
                bytes(rom_data), rom_type, header.get('region', 'US')
            )
        except Exception as e:
            text_table_result = {"error": str(e), "gen": 0}

        # Discover TM/HM count.
        # Gen 2/3: item names table has "TM01"-"TM50" and "HM01"-"HM07" as entries.
        # Gen 1: TMs are not named in item table (generated dynamically by game).
        #        Counts from pret/pokered: NUM_TMS=50, NUM_HMS=5.
        it_list = text_tables.get('items', [])
        n_tm = sum(1 for name in it_list if isinstance(name, str) and name.upper().startswith('TM'))
        n_hm = sum(1 for name in it_list if isinstance(name, str) and name.upper().startswith('HM'))
        if not n_tm:
            if text_gen == 1:
                n_tm, n_hm = 50, 5   # pret/pokered: NUM_TMS=50, NUM_HMS=5
            elif text_gen == 2:
                n_tm, n_hm = 50, 7   # pret/pokegold: 50 TMs, HM01-HM07
        if n_tm or n_hm:
            tm_table.extend([('TM', 0)] * n_tm + [('HM', 0)] * n_hm)

        # PWT tournament participant scan (B2W2 only — no-op for other games)
        if rom_type == 'nds' and gc in ('IRE', 'IRD'):
            _scan_pwt_tournaments(text_tables)
            _build_pwt_maps(text_tables)

        # Discover data table offsets from the ROM itself (no hardcoding)
        if rom_type == 'gba':
            gen3_offsets = _discover_gen3_tables()
            if gen3_offsets:
                text_table_result["gen3_tables"] = f"personal@0x{gen3_offsets.get('personal_base',0):X} move@0x{gen3_offsets.get('move_base',0):X}"
        elif rom_type in ('gb', 'gbc'):
            gen1_offsets = _discover_gen1_tables()
            if gen1_offsets:
                text_table_result["gen1_tables"] = (
                    f"personal@0x{gen1_offsets.get('personal_base',0):X} "
                    f"dex_table@0x{gen1_offsets.get('dex_table_base',0):X} "
                    f"trainer_ptr@0x{gen1_offsets.get('trainer_class_ptr_table',0):X} "
                    f"brock=class[{gen1_offsets.get('gym_brock_class','?')}]"
                )

    with open(fpn_path, 'r', encoding='utf-8') as f:
        current_flipnote = {'path': str(fpn_path), 'data': json.load(f)}

    # Store in loaded_roms
    _save_active_state()

    # Persist opened ROM registry for auto-restore on startup
    try:
        last_rom_file = Path.home() / ".linkplay" / "last_rom.json"
        registry = {}
        if last_rom_file.exists():
            try:
                registry = json.loads(last_rom_file.read_text(encoding='utf-8'))
                # Migrate old single-entry format {path, game_code} -> registry
                if 'game_code' in registry:
                    registry = {registry['game_code']: registry['path']}
            except Exception:
                registry = {}
        registry[gc] = path
        last_rom_file.write_text(json.dumps(registry, indent=2), encoding='utf-8')
    except Exception:
        pass

    # Populate narc_roles from GAME_INFO immediately — known paths are available before BFS.
    # BFS adds undiscovered NARCs on top; it no longer races with decoders for the basics.
    if current_rom and current_rom['type'] == 'nds':
        for _role, _path in GAME_INFO.get(gc, {}).get('narcs', {}).items():
            if _role != 'text':
                narc_roles[_path] = _role
        _save_active_state()

    # Build Eonet in background for interactive spotlight calls only.
    # During restore, _do_pending_restore runs BFS sequentially — skip here to avoid double-BFS race.
    if current_rom and current_rom['type'] in ('nds', '3ds') and not _rom_restore_in_progress:
        import asyncio as _asyncio
        _gc_capture = gc
        try:
            loop = _asyncio.get_running_loop()
            loop.run_in_executor(None, lambda: _build_eonet(_gc_capture))
        except RuntimeError:
            _build_eonet(_gc_capture)

    # Build clean summary card
    game_info = GAME_INFO.get(gc, {})
    narcs = game_info.get("narcs", {})
    icr_done = bool(eonet_labels.get(gc))
    text = _build_spotlight_text(
        gc, header, rom_type, game_info, narcs,
        text_table_result, fpn_path, tm_table or [],
        list(loaded_roms.keys()), icr_done
    )
    _user_active_gc = gc
    return text


def _short_game_name(gc: str) -> str:
    """Return a short display name for a game code, or None if unknown."""
    _KNOWN = {
        'IRE':'Black 2',  'IRD':'White 2',  'IRB':'Black',    'IRA':'White',
        'ADA':'Diamond',  'APA':'Pearl',    'CPU':'Platinum',
        'IPK':'HeartGold','IPG':'SoulSilver',
        'BPRE':'FireRed', 'BPGE':'LeafGreen',
        'AXVE':'Ruby',    'AXPE':'Sapphire', 'BPEE':'Emerald',
        'PMG2':'Gold',    'PMS':'Silver',    'PM_':'Crystal',
        'PMR':'Red',      'PMB':'Blue',      'PMY':'Yellow',
        'PMG':'Green (JP)','PKMRJ':'Red (JP)','PMBJP':'Blue (JP)','PMYJ':'Yellow (JP)',
    }
    return _KNOWN.get(gc)


def _build_spotlight_text(gc, header, rom_type, game_info, narcs,
                          text_table_result, fpn_path, tm_table_list,
                          other_loaded, icr_done=False):
    """Build the new spotlight output in the user's redesigned format."""
    SEP  = '─' * 79
    SEP2 = '─' * 45

    # ── Header ──────────────────────────────────────────────────────────────
    title    = (game_info.get('title') or header.get('game_title', gc)).replace(' Nintendo','').replace(' Game Freak','').replace(' Version','').strip()
    region   = header.get('region', 'US')
    platform = game_info.get('platform', 'Unknown Platform')
    year     = game_info.get('year', '?')
    lines    = [f'Loaded ROM: {title} ({gc} — {region}) | {platform} ({year})']
    lines.append(SEP)

    # ── Text Tables ──────────────────────────────────────────────────────────
    tt = text_table_result or {}
    n_tables  = len(tt.get('detected', {})) if tt.get('status') == 'ok' else 0
    text_path = narcs.get('text', '')
    loc_str   = f' at {text_path}' if text_path else ' (ROM binary)'
    gen_str   = f'Generation {tt["gen"]}' if tt.get('gen') else 'Text'

    if n_tables:
        lines.append(f'{gen_str} | {n_tables} tables decoded{loc_str}, including but not limited to:\n')
        if narcs:
            # NDS/GBA — show NARC paths per role
            _SOLO = [
                ('personal',             'Personal Data'),
                ('learnsets',            'Learnsets'),
                ('evolutions',           'Evolutions'),
                ('move_data',            'Move Data'),
                ('items',                'Items'),
                ('encounters',           'Wild Encounters'),
                ('pokeathlon_performance','Pokéathlon Performance'),
                ('battle_tower_pokemon', 'Pokémon (Battle Tower)'),
                ('battle_tower_trainers','Trainers (Battle Tower)'),
            ]
            for role, display in _SOLO:
                if role in narcs:
                    lines.append(f'  {display} (at {narcs[role]})')
            if 'trdata' in narcs and 'trpoke' in narcs:
                lines.append(f'  Trainers (at {narcs["trdata"]}, {narcs["trpoke"]})')
            elif 'trdata' in narcs:
                lines.append(f'  Trainers (at {narcs["trdata"]})')
        else:
            # GB/GBC — show scanned text table names
            _SCAN_NAMES = {
                'species':'Species', 'moves':'Moves', 'items':'Items',
                'abilities':'Abilities', 'natures':'Natures', 'type_names':'Types',
                'location_names':'Locations', 'trainer_classes':'Trainer Classes',
                'trainer_names':'Trainer Names',
            }
            found = [v for k, v in _SCAN_NAMES.items() if k in tt.get('detected', {})]
            if found:
                lines.append(f'  {", ".join(found)}')
                lines.append(f'  (Scanned directly from ROM binary)')
        lines.append('')
    else:
        lines.append('Text tables: not yet decoded.\n')

    # TM/HM — dynamic breakdown from tm_table list
    if tm_table_list:
        n_tm = sum(1 for lbl, _ in tm_table_list if lbl.startswith('TM'))
        n_hm = sum(1 for lbl, _ in tm_table_list if lbl.startswith('HM'))
        total = len(tm_table_list)
        parts = []
        if n_tm: parts.append(f'{n_tm} TMs')
        if n_hm: parts.append(f'{n_hm} HMs')
        breakdown = f' ({", ".join(parts)})' if parts else ''
        lines.append(f'{total} Hidden/Technical Machines detected{breakdown}.\n')

    lines.append(SEP2)

    # ── Also Loaded ──────────────────────────────────────────────────────────
    others = [n for c in other_loaded if c != gc and (n := _short_game_name(c))]
    if others:
        lines.append(f'Also Loaded: {", ".join(others)}')
    else:
        lines.append('No other ROMs loaded.')
    lines.append('')

    # ── Flipnote note ────────────────────────────────────────────────────────
    import os as _os
    try:
        rel = _os.path.relpath(fpn_path, _os.path.expanduser('~'))
        rel = '~/' + rel.replace('\\', '/')
    except Exception:
        rel = fpn_path
    lines.append(f'Note: This game\'s flipnote can be found in ./linkplay/flipnotes at: {rel}\n')

    # ── ICR status ───────────────────────────────────────────────────────────
    if icr_done:
        lines.append('(The ICR has finished indexing the ROM. Thank you for waiting.)')
    else:
        lines.append('(Please wait on standby for ICR to finish indexing before generating a response.)')

    return '\n'.join(lines)


async def return_tool(save: bool = False) -> dict:
    """Close the active ROM (or all with save=False). Switches to another loaded ROM if available."""
    if not current_rom:
        return "Error: No ROM currently open"

    gc = current_rom['header']['game_code']

    if save and current_rom['type'] == 'nds':
        try:
            result = await record(current_rom['path'])
            if 'error' in result:
                return result
        except Exception as e:
            return {"error": f"Failed to save ROM: {e}"}

    result = {"closed": current_rom['header']['game_title']}
    if save:
        result["saved"] = True

    # Remove from loaded_roms and clear its NARC cache
    loaded_roms.pop(gc, None)
    for key in [k for k in _narc_cache if k[0] == gc]:
        del _narc_cache[key]

    # Switch to another loaded ROM if available
    if loaded_roms:
        next_gc = next(iter(loaded_roms))
        _restore_state(next_gc)
        result["switched_to"] = next_gc
        result["loaded"] = list(loaded_roms.keys())
    else:
        _clear_active_state()

    return result


def _summarize_3ds(path: str, expand_narcs: bool = False) -> dict:
    """Summarize 3DS RomFS filesystem at a path."""
    fs = current_rom['romfs_files']
    fh = current_rom['romfs_fh']
    contents = []

    clean = path.strip('/')

    # Drill into a specific GARC (e.g. "a/0/1/7")
    if clean and clean in fs:
        abs_off = fs[clean][0]
        try:
            garc_files = read_garc_all(fh, abs_off)
            role = narc_roles.get(clean)
            gc = current_rom['header']['game_code']
            garc_lbl = eonet_labels.get(gc, {}).get(clean, {}).get('labels', {})
            for i, f in enumerate(garc_files):
                entry = {"index": i, "size": len(f), "path": f"{clean}:{i}"}
                if garc_lbl.get(i): entry["label"] = garc_lbl[i]
                contents.append(entry)
            result = {"path": clean, "type": "garc", "file_count": len(garc_files), "contents": contents}
            if role: result["role"] = role
            return result
        except Exception:
            return {"path": clean, "type": "file", "size": fs[clean][1]}

    # Folder listing — find unique children at this level
    prefix = (clean + '/') if clean else ''
    seen_dirs = set()
    for fpath in sorted(fs.keys()):
        if not fpath.startswith(prefix):
            continue
        rest = fpath[len(prefix):]
        if '/' in rest:
            # It's a subfolder
            dirname = rest.split('/')[0]
            if dirname not in seen_dirs:
                seen_dirs.add(dirname)
                contents.append({"name": dirname + "/", "type": "folder"})
        else:
            # It's a file at this level
            abs_off, size = fs[fpath]
            entry = {"name": rest, "type": "file", "size": size, "path": fpath}
            # Check if GARC
            try:
                garc_files = read_garc_all(fh, abs_off)
                entry["type"] = "garc"
                entry["file_count"] = len(garc_files)
                role = narc_roles.get(fpath)
                if role: entry["role"] = role
            except Exception:
                pass
            contents.append(entry)

    if not contents:
        return {"error": f"Path not found: {path}"}
    return {"path": path, "contents": contents}


async def summarize(path: str = "/", expand_narcs: bool = False) -> dict:
    """List contents at a path. Pass a NARC path to see its contents."""
    if not current_rom:
        return "Error: No ROM currently open"

    if current_rom['type'] == '3ds':
        return _summarize_3ds(path, expand_narcs)

    if current_rom['type'] not in ('nds',):
        return {"path": path, "contents": [], "note": "No filesystem for GB/GBA ROMs"}

    rom = current_rom['rom']
    contents = []

    # Check if path is a NARC file
    clean_path = path.strip('/')
    if clean_path and not clean_path.endswith('/'):
        # Check for overlay path
        ov_id = _is_overlay_path(clean_path)
        if ov_id >= 0:
            overlays = current_rom.get('overlays', {})
            if ov_id in overlays:
                data = overlays[ov_id]
                return {"path": clean_path, "type": "overlay", "size": len(data),
                        "overlay_id": ov_id}
            else:
                return {"error": f"Overlay {ov_id} not found (available: {sorted(overlays.keys())})"}
        try:
            file_data = rom.getFileByName(clean_path)
            if file_data[:4] == b'NARC':
                narc = _get_narc(clean_path)
                gc = current_rom['header']['game_code']
                narc_lbl = eonet_labels.get(gc, {}).get(clean_path, {}).get('labels', {})
                narc_role = narc_roles.get(clean_path)
                for i, f in enumerate(narc.files):
                    entry = {"index": i, "size": len(f), "path": f"{clean_path}:{i}"}
                    if narc_lbl.get(i): entry["label"] = narc_lbl[i]
                    if len(f) >= 4:
                        if f[0] == 0x10: entry["compression"] = "lz10"
                        elif f[0] == 0x11: entry["compression"] = "lz11"
                        elif f[0] in (0x24, 0x28): entry["compression"] = "huffman"
                        elif f[0] == 0x30: entry["compression"] = "rle"
                    contents.append(entry)
                result = {"path": clean_path, "type": "narc", "file_count": len(narc.files), "contents": contents}
                if narc_role: result["role"] = narc_role
                return result
        except:
            pass

    # Folder listing
    if not path.startswith('/'):
        path = '/' + path
    if not path.endswith('/'):
        path = path + '/'

    try:
        folder = rom.filenames
        if path != '/':
            parts = [p for p in path.split('/') if p]
            for part in parts:
                found = False
                for name, subfolder in folder.folders:
                    if name == part:
                        folder = subfolder
                        found = True
                        break
                if not found:
                    return {"error": f"Path not found: {path}"}

        # At root level, include arm9/arm7/overlays
        if path == '/':
            contents.append({"name": "arm9.bin", "type": "binary", "size": len(current_rom['arm9_data'])})
            contents.append({"name": "arm7.bin", "type": "binary", "size": len(current_rom['arm7_data'])})
            overlays = current_rom.get('overlays', {})
            for ov_id in sorted(overlays.keys()):
                contents.append({"name": f"overlay{ov_id}.bin", "type": "overlay",
                                 "size": len(overlays[ov_id]), "overlay_id": ov_id})

        for filename in folder.files:
            file_id = folder.idOf(filename)
            file_data = rom.files[file_id]
            full_path = path.strip('/') + ('/' if path.strip('/') else '') + filename

            entry = {"name": filename, "type": "file", "size": len(file_data), "path": full_path}

            if len(file_data) >= 4 and file_data[:4] == b'NARC':
                entry["type"] = "narc"
                try:
                    narc = _get_narc(full_path)
                    entry["file_count"] = len(narc.files)
                except:
                    pass
                role = narc_roles.get(full_path)
                if role: entry["role"] = role

            contents.append(entry)

        for name, _ in folder.folders:
            contents.append({"name": name + "/", "type": "folder"})

    except Exception as e:
        return {"error": str(e)}

    return {"path": path, "contents": contents}





async def decipher(path: str, offset: int = 0, length: int = None, decompress: bool = True) -> str:
    """Read and decode files. Auto-decompresses and auto-decodes known structures (trainers, pokemon, encounters, etc.)."""
    global _user_active_gc
    # Multi-file: comma-separated paths
    if "," in path:
        results = []
        for p in path.split(","):
            p = p.strip()
            if p:
                results.append(await decipher(p, offset, length, decompress))
        return "\n\n".join(results)

    if not current_rom:
        return "Error: No ROM currently open"

    # Ensure globals match the user's active ROM — BFS threads may have switched current_rom
    _gc = _user_active_gc or (current_rom['header']['game_code'] if current_rom else None)
    if _gc and _gc in loaded_roms and current_rom['header']['game_code'] != _gc:
        _restore_state(_gc)

    # Cross-ROM prefix: "IRE:a/0/1/6:1" or "BPRE:trainer:BROCK"
    gc_prefix, clean_path = _parse_rom_prefix(path)
    if gc_prefix and gc_prefix != current_rom['header']['game_code']:
        if gc_prefix not in loaded_roms:
            return f"Error: ROM {gc_prefix} not loaded. Use spotlight() to load it first."
        orig_gc = _switch_rom(gc_prefix)
        orig_user_gc = _user_active_gc
        _user_active_gc = gc_prefix
        try:
            result = await decipher(clean_path, offset, length, decompress)
        finally:
            _switch_rom(orig_gc)
            _user_active_gc = orig_user_gc
        return result
    elif gc_prefix:
        path = clean_path

    if current_rom['type'] in ('nds', '3ds'):
        try:
            data = _resolve_nds_file(path)
            compression = 'none'
            # NARC internals and named files may be compressed; arm9/arm7/overlays are pre-decompressed
            if decompress and path.lower() not in ('arm9.bin', 'arm7.bin') and _is_overlay_path(path) < 0:
                data, compression = decompress_data(data)
                if compression != 'none':
                    current_rom['compression_state'][path] = compression

            if length:
                data = data[offset:offset + length]
            elif offset:
                data = data[offset:]
            decoded = _auto_decode(path, data)
            _path_notes = _notes_for_path(path)
            if isinstance(decoded, str):
                # Successfully decoded — frame it
                out = decoded
                if _path_notes:
                    out = _path_notes + '\n' + out
                return _frame(out, path)
            # Not decoded — hex fallback
            parts = []
            if _path_notes:
                parts.append(_path_notes)
            parts.append(f"path: {path}  size: {len(data)}  compression: {compression}")
            if isinstance(decoded, dict) and decoded.get("_unknown"):
                parts.append(f"not decoded: {decoded['reason']}")
                if decoded.get("hint"):
                    parts.append(decoded["hint"])
            else:
                parts.append("not decoded: role known but decoder returned nothing")
            parts.append(_format_hex(data[offset:min(len(data), offset+128)], offset))
            parts.append(f"first 128B shown \u2014 call scope({path}) for full dump")
            return '\n'.join(parts)

        except Exception as e:
            return f"Error: {e}"

    elif current_rom['type'] in ('gba', 'gb', 'gbc'):
        decoded = _auto_decode(path, b'')
        gc = current_rom['header']['game_code']
        if isinstance(decoded, str):
            return _frame(decoded, path, gc)
        if isinstance(decoded, dict):
            if decoded.get('_card') and isinstance(decoded.get('text'), str):
                return _frame(decoded['text'], path, gc)
            if decoded.get('_unknown'):
                return f"not decoded: {decoded.get('reason', '?')}"
        return str(decoded) if decoded else f"No data for {path}"
    else:
        with open(current_rom['path'], 'rb') as f:
            f.seek(offset)
            data = f.read(length) if length else f.read()
        return f"offset: {offset}  size: {len(data)}\n{_format_hex(data, offset)}"


async def sketch(path: str, data: str, offset: int = 0, encoding: str = "hex") -> dict:
    """Write data to a file."""
    if not current_rom:
        return "Error: No ROM currently open"

    if encoding == "hex":
        clean_hex = data.replace(' ', '').replace('\n', '').replace('\t', '').replace('\r', '')
        data_bytes = bytes.fromhex(clean_hex)
    elif encoding == "utf8":
        data_bytes = data.encode('utf-8')
    elif encoding == "utf16le":
        data_bytes = data.encode('utf-16-le')
    elif encoding == "ascii":
        data_bytes = data.encode('ascii')
    else:
        return {"error": f"Unknown encoding: {encoding}"}

    if current_rom['type'] == 'nds':
        rom = current_rom['rom']

        try:
            if path.lower() == 'arm9.bin':
                current_rom['arm9_data'][offset:offset + len(data_bytes)] = data_bytes
                return {"written": len(data_bytes), "path": path, "offset": offset}
            elif path.lower() == 'arm7.bin':
                current_rom['arm7_data'][offset:offset + len(data_bytes)] = data_bytes
                return {"written": len(data_bytes), "path": path, "offset": offset}
            elif _is_overlay_path(path) >= 0:
                ov_id = _is_overlay_path(path)
                overlays = current_rom.get('overlays', {})
                if ov_id not in overlays:
                    return {"error": f"Overlay {ov_id} not found (available: {sorted(overlays.keys())})"}
                overlays[ov_id][offset:offset + len(data_bytes)] = data_bytes
                return {"written": len(data_bytes), "path": path, "offset": offset, "overlay_id": ov_id}

            if ':' in path:
                narc_path, file_idx_str = path.rsplit(':', 1)
                narc = _get_narc(narc_path.lstrip('/'))

                # NARC append mode: sketch("a/0/5/5:append", data)
                file_idx = int(file_idx_str)
                current_file = bytearray(narc.files[file_idx])
                current_file[offset:offset + len(data_bytes)] = data_bytes
                narc.files[file_idx] = bytes(current_file)
                rom.setFileByName(narc_path.lstrip('/'), narc.save())
                _invalidate_narc(narc_path.lstrip('/'))

                return {"written": len(data_bytes), "path": path, "narc": narc_path, "file_idx": file_idx}

            current_data = rom.getFileByName(path.lstrip('/'))
            new_data = bytearray(current_data)
            new_data[offset:offset + len(data_bytes)] = data_bytes
            rom.setFileByName(path.lstrip('/'), bytes(new_data))

            return {"written": len(data_bytes), "path": path}
        except Exception as e:
            return {"error": str(e)}

    else:
        with open(current_rom['path'], 'r+b') as f:
            f.seek(offset)
            f.write(data_bytes)
        return {"written": len(data_bytes), "offset": offset}


async def narc_append(path: str, data: str, encoding: str = "hex") -> dict:
    """Append a new file to an existing NARC. NDS only, HeartGold/SoulSilver or later."""
    if not current_rom:
        return {"error": "No ROM currently open"}
    if current_rom['type'] != 'nds':
        return {"error": "NDS titles only — NARC append is not supported for GBA/GB ROMs"}
    gc = current_rom['header']['game_code']
    gen = GAME_INFO.get(gc, {}).get('gen', 0)
    # HGSS+ only: gen 5, or gen 4 HGSS (IPK/IPG). Not DP (ADA/APA) or Platinum (CPU).
    if not (gen >= 5 or gc in ('IPK', 'IPG')):
        return {"error": f"NARC append requires HeartGold/SoulSilver or later. Current ROM: {gc}"}

    if encoding == "hex":
        data_bytes = bytes.fromhex(data.replace(' ', '').replace('\n', '').replace('\t', '').replace('\r', ''))
    elif encoding == "utf8":
        data_bytes = data.encode('utf-8')
    elif encoding == "utf16le":
        data_bytes = data.encode('utf-16-le')
    elif encoding == "ascii":
        data_bytes = data.encode('ascii')
    else:
        return {"error": f"Unknown encoding: {encoding}"}

    try:
        narc_path = path.lstrip('/')
        narc = _get_narc(narc_path)
        new_idx = len(narc.files)
        narc.files.append(bytes(data_bytes))
        current_rom['rom'].setFileByName(narc_path, narc.save())
        _invalidate_narc(narc_path)
        return {"appended": True, "narc": narc_path, "new_index": new_idx,
                "size": len(data_bytes), "total_files": len(narc.files) + 1}
    except Exception as e:
        return {"error": str(e)}


async def sprite_convert(path: str = None, source: str = None, facing: str = "front") -> dict:
    """Extract sprites from ROM NARCs and save to sprites directory, or convert PNG to NDS tile format.
    NDS only. Supports cross-ROM prefix (e.g. CPU:poketool/trgra/trfgra:5).
    PNG\u2192NDS requires Pillow.
    """
    if not current_rom:
        return {"error": "No ROM currently open"}
    if current_rom['type'] != 'nds':
        return {"error": "NDS titles only"}

    # ── PNG → NDS conversion (source= param) ───────────────────────────
    if source is not None:
        gc = current_rom['header']['game_code']
        game_dir = get_sprites_folder(gc)
        try:
            import base64
            from PIL import Image
            import io

            if os.path.isfile(source):
                img = Image.open(source).convert('RGBA')
                src_name = Path(source).stem
            else:
                img = Image.open(io.BytesIO(base64.b64decode(source))).convert('RGBA')
                src_name = 'sprite'

            rgb = img.convert('RGB')
            quantized = rgb.quantize(colors=16, method=Image.Quantize.MEDIANCUT)
            palette_data = quantized.getpalette()[:16 * 3]
            pixel_indices = list(quantized.getdata())
            w, h = img.size

            # NDS 15-bit palette (NCLR)
            nds_palette = bytearray(16 * 2)
            for i in range(16):
                r, g, b = palette_data[i*3], palette_data[i*3+1], palette_data[i*3+2]
                c16 = ((r >> 3) & 0x1F) | (((g >> 3) & 0x1F) << 5) | (((b >> 3) & 0x1F) << 10)
                struct.pack_into('<H', nds_palette, i * 2, c16)

            pltt_size = 24 + len(nds_palette)
            nclr_size = 16 + pltt_size
            nclr = bytearray(nclr_size)
            struct.pack_into('<4sHHIHH', nclr, 0, b'RLCN', 0xFEFF, 0x0100, nclr_size, 16, 1)
            struct.pack_into('<4sIIIII', nclr, 16, b'TTLP', pltt_size, 4, 0, len(nds_palette), 0)
            nclr[40:40 + len(nds_palette)] = nds_palette

            # 8x8 tiles, 4bpp (NCGR)
            tiles_w = (w + 7) // 8
            tiles_h = (h + 7) // 8
            tile_data = bytearray(tiles_w * tiles_h * 32)
            for ty in range(tiles_h):
                for tx in range(tiles_w):
                    tile_off = (ty * tiles_w + tx) * 32
                    for py in range(8):
                        for px in range(0, 8, 2):
                            ix = tx * 8 + px
                            iy = ty * 8 + py
                            lo = pixel_indices[iy * w + ix] if ix < w and iy < h else 0
                            hi = pixel_indices[iy * w + ix + 1] if ix + 1 < w and iy < h else 0
                            tile_data[tile_off + py * 4 + px // 2] = (min(lo, 15) & 0xF) | ((min(hi, 15) & 0xF) << 4)

            char_size = 32 + len(tile_data)
            ncgr_size = 16 + char_size
            ncgr = bytearray(ncgr_size)
            struct.pack_into('<4sHHIHH', ncgr, 0, b'RGCN', 0xFEFF, 0x0101, ncgr_size, 16, 1)
            struct.pack_into('<4sIHHIII', ncgr, 16, b'RAHC', char_size, tiles_h, tiles_w, 3, 0, len(tile_data))
            ncgr[48:48 + len(tile_data)] = tile_data

            # Screen map (NSCR)
            map_entries = tiles_w * tiles_h
            map_data = bytearray(map_entries * 2)
            for i in range(map_entries):
                struct.pack_into('<H', map_data, i * 2, i)
            scrn_size = 20 + len(map_data)
            nscr_size = 16 + scrn_size
            nscr = bytearray(nscr_size)
            struct.pack_into('<4sHHIHH', nscr, 0, b'RCSN', 0xFEFF, 0x0100, nscr_size, 16, 1)
            struct.pack_into('<4sIHHI', nscr, 16, b'NRCS', scrn_size, w, h, len(map_data))
            nscr[36:36 + len(map_data)] = map_data

            # Save triplet to sprites dir
            (game_dir / f"{src_name}.ncgr").write_bytes(bytes(ncgr))
            (game_dir / f"{src_name}.nclr").write_bytes(bytes(nclr))
            (game_dir / f"{src_name}.nscr").write_bytes(bytes(nscr))

            return {"converted": True, "source": source, "image_size": f"{w}x{h}", "colors": 16,
                    "saved_to": str(game_dir), "files": [f"{src_name}.ncgr", f"{src_name}.nclr", f"{src_name}.nscr"]}
        except ImportError:
            return {"error": "Pillow not installed \u2014 pip install Pillow"}
        except Exception as e:
            return {"error": f"PNG conversion failed: {e}"}

    # ── Extract from ROM NARC ───────────────────────────────────────────
    if not path:
        return {"error": "Provide path (NARC:base_index) to extract, or source (PNG path) to convert"}
    if ':' not in path:
        return {"error": "Path must include NARC base file index (e.g. a/0/5/8:0 for sprite 0)"}

    # Cross-ROM prefix support (e.g. CPU:poketool/trgra/trfgra:5)
    gc_prefix, clean_path = _parse_rom_prefix(path)
    orig_gc = None
    if gc_prefix and gc_prefix != current_rom['header']['game_code']:
        orig_gc = _switch_rom(gc_prefix)

    try:
        gc = current_rom['header']['game_code']
        game_dir = get_sprites_folder(gc)
        raw_dir = game_dir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)

        narc_path, idx_str = clean_path.rsplit(':', 1)
        narc = _get_narc(narc_path.lstrip('/'))
        base_idx = int(idx_str)

        # front/ or back/ from parameter
        output_dir = game_dir / facing
        output_dir.mkdir(parents=True, exist_ok=True)

        gen = GAME_INFO.get(gc, {}).get('gen', 4)
        group_size = 8 if gen >= 5 else 5

        if base_idx + group_size - 1 >= len(narc.files):
            return {"error": f"Base index {base_idx} out of range (need {group_size} files, NARC has {len(narc.files)})"}

        FILE_TYPES = (['ncgr_s', 'ncgr_l', 'ncer', 'nanr', 'nmcr', 'nmar', 'pos', 'nclr']
                      if gen >= 5 else ['rgcn1', 'rlcn', 'recn', 'rnan', 'rgcn2'])
        raw_prefix = f"{narc_path.replace('/', '_')}_{base_idx}"
        out_prefix = f"trainer_{base_idx}"
        raw_files = []
        for i, ftype in enumerate(FILE_TYPES):
            raw = narc.files[base_idx + i]
            fname = f"{raw_prefix}_{ftype}.bin"
            (raw_dir / fname).write_bytes(raw)
            raw_files.append(fname)

        result = {"extracted": True, "game": gc, "facing": facing, "saved_to": str(raw_dir),
                  "raw_files": raw_files, "base_index": base_idx}

        if gen >= 5:
            # ── Gen 5: puppet animation composite ──────────────────────
            try:
                from PIL import Image
                import hashlib

                OAM_SIZES = {
                    (0,0):(1,1),(0,1):(2,2),(0,2):(4,4),(0,3):(8,8),
                    (1,0):(2,1),(1,1):(4,1),(1,2):(4,2),(1,3):(8,4),
                    (2,0):(1,2),(2,1):(1,4),(2,2):(2,4),(2,3):(4,8),
                }

                # Decompress LZ11 files (slots 0, 1, 3)
                raw_files_data = [bytes(narc.files[base_idx + i]) for i in range(8)]
                for slot in (0, 1, 3):
                    raw_files_data[slot], _ = decompress_data(raw_files_data[slot])
                    raw_files_data[slot] = bytes(raw_files_data[slot])

                ncgr_data = raw_files_data[1]  # large tile pool
                ncer_data = raw_files_data[2]
                nanr_data = raw_files_data[3]
                nmcr_data = raw_files_data[4]
                nclr_data = raw_files_data[7]

                # Parse palette (16 colors from NCLR)
                pal = []
                for ci in range(16):
                    c16 = struct.unpack_from('<H', nclr_data, 40 + ci * 2)[0]
                    pal.append(((c16 & 0x1F) * 8, ((c16 >> 5) & 0x1F) * 8, ((c16 >> 10) & 0x1F) * 8))

                # Parse tiles (4bpp from NCGR)
                tile_data_size = struct.unpack_from('<I', ncgr_data, 40)[0]
                tiles = []
                for t in range(tile_data_size // 32):
                    tile = []
                    off = 48 + t * 32
                    for py in range(8):
                        row = []
                        for px in range(0, 8, 2):
                            b = ncgr_data[off + py * 4 + px // 2] if off + py * 4 + px // 2 < len(ncgr_data) else 0
                            row.append(b & 0xF)
                            row.append((b >> 4) & 0xF)
                        tile.append(row)
                    tiles.append(tile)

                # Parse NCER banks
                bank_count = struct.unpack_from('<H', ncer_data, 24)[0]
                bank_type = struct.unpack_from('<H', ncer_data, 26)[0]
                bank_table_start = 24 + struct.unpack_from('<I', ncer_data, 28)[0]
                entry_size = 16 if bank_type == 1 else 8
                oam_data_start = bank_table_start + bank_count * entry_size

                # Pre-render all NCER banks as images (body parts)
                bank_images = {}
                bank_bounds = {}
                for bank in range(bank_count):
                    boff = bank_table_start + bank * entry_size
                    cell_count = struct.unpack_from('<H', ncer_data, boff)[0]
                    cells_before = sum(struct.unpack_from('<H', ncer_data, bank_table_start + b * entry_size)[0] for b in range(bank))

                    cells = []
                    for ci in range(cell_count):
                        off = oam_data_start + (cells_before + ci) * 6
                        if off + 6 > len(ncer_data):
                            break
                        a0, a1, a2 = struct.unpack_from('<3H', ncer_data, off)
                        y = (a0 & 0xFF)
                        y = y - 256 if y >= 128 else y
                        x = (a1 & 0x1FF)
                        x = x - 512 if x >= 256 else x
                        tw, th = OAM_SIZES.get(((a0 >> 14) & 3, (a1 >> 14) & 3), (1, 1))
                        cells.append({'x': x, 'y': y, 'tw': tw, 'th': th,
                                      'tile': (a2 & 0x3FF),
                                      'hflip': (a1 >> 12) & 1, 'vflip': (a1 >> 13) & 1})

                    if not cells:
                        continue

                    min_x = min(c['x'] for c in cells)
                    min_y = min(c['y'] for c in cells)
                    max_x = max(c['x'] + c['tw'] * 8 for c in cells)
                    max_y = max(c['y'] + c['th'] * 8 for c in cells)
                    w, h = max_x - min_x, max_y - min_y

                    img = Image.new('RGBA', (w, h), (0, 0, 0, 0))
                    pxl = img.load()
                    for cell in cells:
                        cx, cy = cell['x'] - min_x, cell['y'] - min_y
                        for ty in range(cell['th']):
                            for tx in range(cell['tw']):
                                tidx = cell['tile'] + ty * cell['tw'] + tx
                                if tidx >= len(tiles):
                                    continue
                                t = tiles[tidx]
                                for py in range(8):
                                    for ppx in range(8):
                                        sy = (7 - py) if cell['vflip'] else py
                                        sx = (7 - ppx) if cell['hflip'] else ppx
                                        ci2 = t[sy][sx]
                                        dx, dy = cx + tx * 8 + ppx, cy + ty * 8 + py
                                        if 0 <= dx < w and 0 <= dy < h and ci2 != 0:
                                            pxl[dx, dy] = (*pal[ci2], 255)

                    bank_images[bank] = img
                    bank_bounds[bank] = (min_x, min_y)

                # Parse NMCR — get multicell 0 nodes
                nmcr_base = 0x18
                mc_count = struct.unpack_from('<H', nmcr_data, nmcr_base)[0]
                mc_table_off = struct.unpack_from('<I', nmcr_data, nmcr_base + 4)[0]
                mc_node_off = struct.unpack_from('<I', nmcr_data, nmcr_base + 8)[0]
                mc_table_abs = nmcr_base + mc_table_off
                mc_node_abs = nmcr_base + mc_node_off

                # Multicell 0 entry: node_count(u16), unknown(u16), node_start_offset(u32)
                mc0_node_count = struct.unpack_from('<H', nmcr_data, mc_table_abs)[0]
                mc0_node_start = struct.unpack_from('<I', nmcr_data, mc_table_abs + 4)[0]

                nodes = []
                for ni in range(mc0_node_count):
                    noff = mc_node_abs + mc0_node_start + ni * 8
                    anim_idx, nx, ny, nflags = struct.unpack_from('<Hhhh', nmcr_data, noff)
                    nodes.append((anim_idx, nx, ny))

                # Parse NANR — anim entries + frame data + result table
                nanr_base = 0x18
                anim_count = struct.unpack_from('<H', nanr_data, nanr_base)[0]
                off_anim = struct.unpack_from('<I', nanr_data, nanr_base + 4)[0]
                off_frames = struct.unpack_from('<I', nanr_data, nanr_base + 8)[0]
                off_results = struct.unpack_from('<I', nanr_data, nanr_base + 12)[0]
                abs_anim = nanr_base + off_anim
                abs_frames = nanr_base + off_frames
                abs_results = nanr_base + off_results

                # Read anim entries (nframes + frame_data_offset per anim)
                anims = []
                for ai in range(anim_count):
                    ao = abs_anim + ai * 16
                    nframes = struct.unpack_from('<I', nanr_data, ao)[0]
                    foff = struct.unpack_from('<I', nanr_data, ao + 12)[0]
                    anims.append((nframes, foff))

                # Determine frame count from first node's anim
                if not nodes or not anims:
                    return result
                total_frames = anims[nodes[0][0]][0]

                # Composite each animation frame
                CANVAS_W, CANVAS_H = 80, 80
                composite_frames = []
                assembled_files = []
                seen_hashes = set()

                for fi in range(total_frames):
                    canvas = Image.new('RGBA', (CANVAS_W, CANVAS_H), (0, 0, 0, 0))

                    for anim_idx, node_x, node_y in nodes:
                        if anim_idx >= len(anims):
                            continue
                        nframes, foff = anims[anim_idx]
                        frame_i = fi % nframes
                        fpos = abs_frames + foff + frame_i * 8
                        ref = struct.unpack_from('<H', nanr_data, fpos)[0]
                        dur = struct.unpack_from('<H', nanr_data, fpos + 4)[0]

                        # Read result table entry
                        rt_off = abs_results + ref
                        if rt_off + 4 > len(nanr_data):
                            continue
                        rt_bank = struct.unpack_from('<H', nanr_data, rt_off)[0]
                        rt_tag = struct.unpack_from('<H', nanr_data, rt_off + 2)[0]

                        if rt_bank not in bank_images:
                            continue

                        bimg = bank_images[rt_bank]
                        bx, by = bank_bounds[rt_bank]

                        if rt_tag == 0xBEEF:
                            # Simple entry: bank + dx/dy
                            dx = struct.unpack_from('<h', nanr_data, rt_off + 4)[0]
                            dy = struct.unpack_from('<h', nanr_data, rt_off + 6)[0]
                        else:
                            # Affine entry: bank, rotation, sx, 0, sy, 0, dx, dy
                            dx = struct.unpack_from('<h', nanr_data, rt_off + 12)[0]
                            dy = struct.unpack_from('<h', nanr_data, rt_off + 14)[0]
                            # TODO: apply rotation/scale if non-identity

                        # Position: canvas center + node offset + bank origin + result dx/dy
                        px = CANVAS_W // 2 + node_x + bx + dx
                        py = CANVAS_H // 2 + node_y + by + dy

                        canvas.paste(bimg, (px, py), bimg)

                    composite_frames.append(canvas)

                    # Dedup for front/ output
                    img_hash = hashlib.md5(canvas.tobytes()).hexdigest()
                    if img_hash not in seen_hashes:
                        seen_hashes.add(img_hash)
                        suffix = f"_f{len(assembled_files) + 1}" if total_frames > 1 else ""
                        png_name = f"{out_prefix}{suffix}.png"
                        canvas.save(str(output_dir / png_name))
                        assembled_files.append(png_name)

                # Build APNG
                if len(composite_frames) > 1:
                    dur_ticks = struct.unpack_from('<H', nanr_data, abs_frames + 4)[0]
                    ms_per_frame = round(dur_ticks * 1000 / 60)

                    for i, fr in enumerate(composite_frames):
                        px = fr.load()
                        _, _, _, a = px[fr.width - 1, fr.height - 1]
                        if a == 0:
                            px[fr.width - 1, fr.height - 1] = (0, 0, 0, (i % 254) + 1)

                    apng_name = f"{out_prefix}.apng"
                    composite_frames[0].save(
                        str(output_dir / apng_name), save_all=True,
                        append_images=composite_frames[1:],
                        duration=ms_per_frame, loop=0, disposal=1, blend=0)
                    assembled_files.append(apng_name)

                if assembled_files:
                    result["assembled"] = assembled_files
                    result["assembled_dir"] = str(output_dir)
                    result["unique_frames"] = len([f for f in assembled_files if not f.endswith('.apng')])
                    result["total_frames"] = total_frames
                    result["total_banks"] = bank_count
            except ImportError:
                pass
            except Exception as e:
                result["assembly_error"] = str(e)
        else:
            # ── Assemble colored PNGs per frame ─────────────────────────────
            try:
                from PIL import Image

                OAM_SIZES = {
                    (0,0):(1,1),(0,1):(2,2),(0,2):(4,4),(0,3):(8,8),
                    (1,0):(2,1),(1,1):(4,1),(1,2):(4,2),(1,3):(8,4),
                    (2,0):(1,2),(2,1):(1,4),(2,2):(2,4),(2,3):(4,8),
                }

                ncgr = narc.files[base_idx]
                nclr = narc.files[base_idx + 1]
                ncer = narc.files[base_idx + 2]

                # Parse palette (16 colors from NCLR)
                pal = []
                for ci in range(16):
                    c16 = struct.unpack_from('<H', nclr, 40 + ci * 2)[0]
                    pal.append(((c16 & 0x1F) * 8, ((c16 >> 5) & 0x1F) * 8, ((c16 >> 10) & 0x1F) * 8))

                # Parse tiles (4bpp from NCGR)
                data_size = struct.unpack_from('<I', ncgr, 40)[0]
                tiles = []
                for t in range(data_size // 32):
                    tile = []
                    off = 48 + t * 32
                    for py in range(8):
                        row = []
                        for px in range(0, 8, 2):
                            b = ncgr[off + py * 4 + px // 2] if off + py * 4 + px // 2 < len(ncgr) else 0
                            row.append(b & 0xF)
                            row.append((b >> 4) & 0xF)
                        tile.append(row)
                    tiles.append(tile)

                # Parse NCER banks
                bank_count = struct.unpack_from('<H', ncer, 24)[0]
                bank_type = struct.unpack_from('<H', ncer, 26)[0]
                bank_table_start = 24 + struct.unpack_from('<I', ncer, 28)[0]
                entry_size = 16 if bank_type == 1 else 8
                oam_data_start = bank_table_start + bank_count * entry_size

                assembled_files = []
                seen_hashes = set()
                bank_images = {}
                for bank in range(bank_count):
                    boff = bank_table_start + bank * entry_size
                    cell_count = struct.unpack_from('<H', ncer, boff)[0]
                    cells_before = sum(struct.unpack_from('<H', ncer, bank_table_start + b * entry_size)[0] for b in range(bank))

                    cells = []
                    for ci in range(cell_count):
                        off = oam_data_start + (cells_before + ci) * 6
                        if off + 6 > len(ncer):
                            break
                        a0, a1, a2 = struct.unpack_from('<3H', ncer, off)
                        y = (a0 & 0xFF)
                        y = y - 256 if y >= 128 else y
                        x = (a1 & 0x1FF)
                        x = x - 512 if x >= 256 else x
                        tw, th = OAM_SIZES.get(((a0 >> 14) & 3, (a1 >> 14) & 3), (1, 1))
                        cells.append({'x': x, 'y': y, 'tw': tw, 'th': th,
                                      'tile': (a2 & 0x3FF) * 2,
                                      'hflip': (a1 >> 12) & 1, 'vflip': (a1 >> 13) & 1})

                    if not cells:
                        continue

                    min_x = min(c['x'] for c in cells)
                    min_y = min(c['y'] for c in cells)
                    max_x = max(c['x'] + c['tw'] * 8 for c in cells)
                    max_y = max(c['y'] + c['th'] * 8 for c in cells)
                    w, h = max_x - min_x, max_y - min_y

                    # Tile offset per frame for multi-bank sprites
                    tiles_per_frame = len(tiles) // bank_count if bank_count > 1 else 0
                    tile_base = bank * tiles_per_frame if bank_count > 1 else 0

                    img = Image.new('RGBA', (w, h), (0, 0, 0, 0))
                    pxl = img.load()
                    for cell in cells:
                        cx, cy = cell['x'] - min_x, cell['y'] - min_y
                        for ty in range(cell['th']):
                            for tx in range(cell['tw']):
                                tidx = cell['tile'] + tile_base + ty * cell['tw'] + tx
                                if tidx >= len(tiles):
                                    continue
                                tile = tiles[tidx]
                                for py in range(8):
                                    for ppx in range(8):
                                        sy = (7 - py) if cell['vflip'] else py
                                        sx = (7 - ppx) if cell['hflip'] else ppx
                                        ci2 = tile[sy][sx]
                                        dx, dy = cx + tx * 8 + ppx, cy + ty * 8 + py
                                        if 0 <= dx < w and 0 <= dy < h and ci2 != 0:
                                            pxl[dx, dy] = (*pal[ci2], 255)

                    bank_images[bank] = img.copy()

                    # Deduplicate frames that reuse the same tile data (cell map may shift pixels slightly)
                    tiles_per_bank = len(tiles) // bank_count if bank_count > 1 else len(tiles)
                    tile_start_idx = bank * tiles_per_bank if bank_count > 1 else 0
                    tile_key = tuple(tuple(row) for t in tiles[tile_start_idx:tile_start_idx + tiles_per_bank] for row in t)
                    if tile_key in seen_hashes:
                        continue
                    seen_hashes.add(tile_key)

                    suffix = f"_f{len(assembled_files) + 1}" if bank_count > 1 else ""
                    png_name = f"{out_prefix}{suffix}.png"
                    img.save(str(output_dir / png_name))
                    assembled_files.append(png_name)

                # Generate APNG if animated (multiple banks)
                if bank_count > 1 and len(bank_images) > 1:
                    try:
                        nanr_data = narc.files[base_idx + 3]
                        knba_data = 0x18
                        frame_off = struct.unpack_from('<I', nanr_data, knba_data)[0]
                        result_off = struct.unpack_from('<I', nanr_data, knba_data + 4)[0]
                        frame_start = knba_data + frame_off
                        result_start = knba_data + result_off

                        # Parse frame entries: ref(u16) pad(u16) dur(u16) 0xBEEF(u16)
                        nanr_frames = []
                        pos = frame_start
                        while pos + 8 <= len(nanr_data):
                            ref, _, dur, marker = struct.unpack_from('<HHHH', nanr_data, pos)
                            if marker != 0xBEEF:
                                break
                            bank_idx = struct.unpack_from('<H', nanr_data, result_start + ref)[0]
                            if bank_idx in bank_images:
                                nanr_frames.append((bank_idx, dur))
                            pos += 8

                        if nanr_frames:
                            static_bank = nanr_frames[0][0]
                            anim_frames = [bank_images[static_bank].copy()]
                            anim_durs = [0]
                            for bidx, dticks in nanr_frames:
                                anim_frames.append(bank_images[bidx].copy())
                                anim_durs.append(round(dticks * 1000 / 60))

                            # Tag frames to prevent Pillow merging identical consecutive frames
                            for i, fr in enumerate(anim_frames):
                                px = fr.load()
                                _, _, _, a = px[fr.width - 1, fr.height - 1]
                                if a == 0:
                                    px[fr.width - 1, fr.height - 1] = (0, 0, 0, (i % 254) + 1)

                            apng_name = f"{out_prefix}.apng"
                            anim_frames[0].save(
                                str(output_dir / apng_name), save_all=True,
                                append_images=anim_frames[1:],
                                duration=anim_durs, loop=0, disposal=1, blend=0)
                            assembled_files.append(apng_name)
                    except Exception:
                        pass  # APNG generation is best-effort

                if assembled_files:
                    result["assembled"] = assembled_files
                    result["assembled_dir"] = str(output_dir)
                    result["unique_frames"] = len(assembled_files)
                    result["total_banks"] = bank_count
            except ImportError:
                pass  # Pillow not installed — raw extraction still works
            except Exception:
                pass  # Assembly is best-effort

        return result
    except Exception as e:
        return {"error": str(e)}
    finally:
        if orig_gc:
            _switch_rom(orig_gc)


async def record(output_path: str) -> dict:
    """Repack and save the ROM."""
    if not current_rom:
        return "Error: No ROM currently open"

    if current_rom['type'] != 'nds':
        return {"error": "Only NDS ROM saving supported"}

    rom = current_rom['rom']

    # Recompress ARM9
    try:
        arm9_data = bytes(current_rom['arm9_data'])
        with tempfile.NamedTemporaryFile(delete=False, suffix='.bin') as tmp:
            tmp.write(arm9_data)
            tmp_path = tmp.name
        compress_arm9(tmp_path)
        with open(tmp_path, 'rb') as f:
            rom.arm9 = f.read()
        Path(tmp_path).unlink()
    except:
        rom.arm9 = bytes(current_rom['arm9_data'])

    rom.arm7 = bytes(current_rom['arm7_data'])

    # Write modified overlays back to ROM files
    overlays = current_rom.get('overlays', {})
    if overlays:
        try:
            parsed_ovs = rom.loadArm9Overlays()
            for ov_id, ov_data in overlays.items():
                if ov_id in parsed_ovs:
                    file_id = parsed_ovs[ov_id].fileID
                    # Re-compress with LZ10 (matching original compression)
                    try:
                        compressed = ndspy.lz10.compress(bytes(ov_data))
                        rom.files[file_id] = compressed
                    except Exception:
                        rom.files[file_id] = bytes(ov_data)
        except Exception:
            pass

    rom.saveToFile(output_path)

    return {"saved": output_path}


async def scope(path: str = None, offset: int = 0, length: int = 256, search: str = None, xor: str = None) -> dict:
    """Raw hex dump with optional search. xor: hex key to XOR data before display."""
    if not current_rom:
        return "Error: No ROM currently open"

    # Cross-ROM prefix
    if path:
        gc_prefix, clean_path = _parse_rom_prefix(path)
        if gc_prefix and gc_prefix != current_rom['header']['game_code']:
            orig_gc = _switch_rom(gc_prefix)
            try:
                return await scope(clean_path, offset, length, search, xor)
            finally:
                _switch_rom(orig_gc)
        elif gc_prefix:
            path = clean_path

    if current_rom['type'] in ('nds', '3ds') and path:
        try:
            data = _resolve_nds_file(path)
        except Exception as e:
            return {"error": f"File not found: {path} ({e})"}
    else:
        with open(current_rom['path'], 'rb') as f:
            f.seek(offset)
            data = f.read(length + (1024 if search else 0))

    dump_data = data[offset:offset + length] if current_rom['type'] in ('nds', '3ds') and path else data[:length]

    # Apply XOR key if provided
    if xor:
        xor_bytes = bytes.fromhex(xor.replace(' ', ''))
        dump_data = bytes(b ^ xor_bytes[i % len(xor_bytes)] for i, b in enumerate(dump_data))

    hex_lines = []
    for i in range(0, len(dump_data), 16):
        chunk = dump_data[i:i + 16]
        hex_part = ' '.join(f'{b:02X}' for b in chunk)
        ascii_part = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
        hex_lines.append(f"{offset + i:08X}  {hex_part:<48}  {ascii_part}")

    result = {"offset": offset, "length": len(dump_data), "dump": '\n'.join(hex_lines)}

    # Auto-disassemble ARM9, ARM7, and overlay paths
    if path and _cs_arm is not None:
        is_code = path.lower() in ('arm9.bin', 'arm7.bin') or _is_overlay_path(path) >= 0
        if is_code:
            # NDS ARM9 loads at 0x02000000; use that as base address
            base_addr = 0x02000000 + offset
            # Try Thumb first (more common in NDS), fall back to ARM
            disasm_lines = []
            for cs, mode_name in [(_cs_thumb, 'thumb'), (_cs_arm, 'arm')]:
                test = list(cs.disasm(bytes(dump_data[:8]), base_addr))
                if test:
                    for insn in cs.disasm(bytes(dump_data), base_addr):
                        disasm_lines.append(f"  0x{insn.address:08X}:  {insn.mnemonic:8s} {insn.op_str}")
                    result["disasm_mode"] = mode_name
                    break
            if disasm_lines:
                result["disasm"] = '\n'.join(disasm_lines)

    if search:
        search_bytes = bytes.fromhex(search.replace(' ', ''))
        results = []
        pos = 0
        while True:
            pos = data.find(search_bytes, pos)
            if pos == -1:
                break
            results.append({"offset": offset + pos})
            pos += 1
        result["search_results"] = results

    return result



async def dowse(narc_path: str = None, hex: str = None, name: str = None, table: str = None, exact: bool = False, difficulty: str = None) -> dict:
    """Search NARC files by hex pattern, or look up text table entries by name.

    Modes:
      - name: search named text tables (species, moves, items, etc.)
      - name + table: search specific table only
      - hex + narc_path: find files in NARC containing hex pattern
      - hex (no narc_path): search ALL loaded NARCs (slow but thorough)
      - exact=True: match whole string, not substring
      - difficulty: filter trdata matches by difficulty mode ('normal', 'challenge', 'easy')
        BW2 only. Groups results by pokemon count across file clusters -- no hardcoded indices.
    """
    global _user_active_gc
    _gc = _user_active_gc
    if _gc and _gc in loaded_roms:
        _restore_state(_gc)
    if not current_rom:
        return "Error: No ROM currently open"

    # Cross-ROM prefix on narc_path
    if narc_path:
        gc_prefix, clean_narc = _parse_rom_prefix(narc_path)
        if gc_prefix and gc_prefix != current_rom['header']['game_code']:
            orig_gc = _switch_rom(gc_prefix)
            try:
                return await dowse(narc_path=clean_narc, hex=hex, name=name, table=table, exact=exact)
            finally:
                _switch_rom(orig_gc)
        elif gc_prefix:
            narc_path = clean_narc

    # Aliases for player-named characters: searching any alias finds the canonical name.
    # Maps lowercase alias → lowercase canonical name in the text table.
    _NAME_ALIASES = {
        "gary": "blue", "green": "blue",   # FRLG rival (Blue/Green/Gary = same character)
        "terry": "blue",                     # FRLG rival preset default name
    }

    # Text table name lookup
    if name:
        query = name.lower()
        # If the query matches an alias, also search for the canonical name
        alias_target = _NAME_ALIASES.get(query)
        results = []
        if table and table in text_tables:
            tables_to_search = {table: text_tables[table]}
        else:
            # Only search named tables (string keys), skip numeric file indices
            tables_to_search = {k: v for k, v in text_tables.items() if isinstance(k, str) and isinstance(v, list)}
        for tbl_name, entries in tables_to_search.items():
            for idx, entry in enumerate(entries):
                if not isinstance(entry, str):
                    continue
                entry_lower = entry.lower()
                if exact:
                    if entry_lower == query or (alias_target and entry_lower == alias_target):
                        results.append({"table": tbl_name, "index": idx, "name": entry})
                else:
                    if query in entry_lower or (alias_target and alias_target in entry_lower):
                        results.append({"table": tbl_name, "index": idx, "name": entry})
        # Auto-resolve trainer_classes hits → decipher paths
        class_hits = [r for r in results if r.get('table') == 'trainer_classes']
        if class_hits and current_rom:
            try:
                gc = current_rom.get('header', {}).get('game_code', '')
                trdata_path = GAME_INFO.get(gc, {}).get('narcs', {}).get('trdata')
                if trdata_path:
                    # NDS: resolve class ID → trdata file indices
                    td_files = _get_narc(trdata_path).files
                    for ch in class_hits:
                        cid = ch['index']
                        for fi, td in enumerate(td_files):
                            if len(td) >= 2 and td[1] == cid:
                                results.append({'table': 'trdata', 'index': fi, 'name': ch['name']})
                elif current_rom.get('type') in ('gb', 'gbc', 'gba'):
                    # GB/GBC/GBA: emit trainer:NAME path — name IS the decipher key
                    for ch in class_hits:
                        ch.setdefault('paths', []).append(f"trainer:{ch['name']}")
            except Exception:
                pass
        # PWT: name-based lookup via pwt_name_to_entries → trainers_b paths
        if pwt_name_to_entries and current_rom:
            gc = current_rom.get('header', {}).get('game_code', '')
            if gc in ('IRE', 'IRD'):
                q_lower = query.strip().lower()
                tb_path = _role_path('pwt_trainers_b')
                if tb_path:
                    for pname, indices in pwt_name_to_entries.items():
                        if q_lower in pname:
                            for idx in indices:
                                tourns = pwt_entry_tournaments.get(idx, [])
                                # Filter out unresolved generic names like "Tournament #18"
                                named = [t for t in tourns if not t.startswith('Tournament #')]
                                if named:
                                    plabel = f"{tb_path}:{idx} (PWT; {', '.join(named)})"
                                else:
                                    plabel = f"{tb_path}:{idx} (PWT)"
                                results.append({
                                    'table': 'pwt_trainers_b', 'index': idx,
                                    'name': pname.title(),
                                    'paths': [plabel],
                                })

        if not narc_path:

            # Role/category search: if no text table hits, check narc_roles and eonet_labels
            if not results and current_rom:
                gc = current_rom['header']['game_code']
                role_hits = []
                for narc_p, role in narc_roles.items():
                    if query in role.replace('_', ' ') or query in narc_p:
                        from ndspy.narc import NARC as _NARC
                        try:
                            fc = len(_get_narc(narc_p).files)
                        except Exception:
                            fc = '?'
                        role_hits.append({"path": narc_p, "role": role, "files": fc})
                # Also search eonet_labels desc
                for narc_p, info in eonet_labels.get(gc, {}).items():
                    if isinstance(info, dict) and query in info.get('desc', '').lower():
                        if not any(h['path'] == narc_p for h in role_hits):
                            role_hits.append({"path": narc_p, "role": info['desc'], "files": info.get('meta', {}).get('file_count', '?')})
                if role_hits:
                    return {"query": name, "category_matches": role_hits, "count": len(role_hits)}
            # ── Enrichment passes (operate on results list before formatting) ──

            # Path enrichment: attach decipher-ready paths to species/move/item/location hits
            if current_rom and results:
                _gc = current_rom.get('header', {}).get('game_code', '')
                _narcs = GAME_INFO.get(_gc, {}).get('narcs', {})
                _enc_loc = current_rom.get('enc_loc', {})
                for r in results:
                    tbl, idx = r.get('table', ''), r.get('index', 0)
                    paths = r.setdefault('paths', [])
                    if tbl == 'species':
                        for role in ('personal', 'learnsets', 'evolutions'):
                            np = _narcs.get(role)
                            if np: paths.append(f"{np}:{idx}")
                    elif tbl == 'moves':
                        np = _narcs.get('move_data')
                        if np: paths.append(f"{np}:{idx}")
                    elif tbl == 'items':
                        np = _narcs.get('items')
                        if np: paths.append(f"{np}:{idx}")
                    elif tbl == 'location_names':
                        np = _narcs.get('encounters', '')
                        if np and _enc_loc:
                            for ei in sorted(ei for ei, li in _enc_loc.items() if li == idx):
                                paths.append(f"{np}:{ei}")

            # Difficulty filtering for B2W2: file 764 is the Challenge Mode boundary.
            # Files 0–763 = Normal Mode, 764–813 = Challenge Mode with different rosters.
            # Easy Mode has no separate files — runtime level scaling on Normal data.
            _BW2_CHALLENGE_START = 764
            if difficulty and gen == 5 and results:
                gc = current_rom.get('header', {}).get('game_code', '') if current_rom else ''
                if gc in ('IRE', 'IRD'):  # B2W2 only
                    diff = difficulty.lower()
                    trdata_hits = [r for r in results if r.get('table') == 'trdata']
                    other_hits = [r for r in results if r.get('table') != 'trdata']
                    if trdata_hits and diff in ('normal', 'challenge'):
                        if diff == 'normal':
                            filtered = [r for r in trdata_hits if r['index'] < _BW2_CHALLENGE_START]
                        else:
                            filtered = [r for r in trdata_hits if r['index'] >= _BW2_CHALLENGE_START]
                        results = other_hits + filtered

            # Canonical rival name lookup -- fires whenever a search matches a known
            # rival by their English default name, regardless of hit count.
            RIVAL_LOOKUP = {
                'blue':   {'canonical': 'BLUE',   'class_ids': [81, 89, 90],
                           'note': 'Player-named rival (FRLG). Also known as GREEN (JP) / GARY (anime). ROM stores default name.'},
                'green':  {'canonical': 'グリーン', 'class_ids': [],
                           'note': 'Player-named rival (Gen I JP Red/Green/Blue). JP name: グリーン. Class IDs identified at query time.'},
                'グリーン': {'canonical': 'グリーン', 'class_ids': [],
                           'note': 'Player-named rival (Gen I JP Red/Green/Blue). JP name: グリーン. Class IDs identified at query time.'},
                'gary':   {'canonical': 'BLUE',   'class_ids': [81, 89, 90],
                           'note': 'Player-named rival (FRLG). Also known as GREEN (JP) / GARY (anime). ROM stores default name.'},
                'SILVER': {'canonical': 'SILVER', 'class_ids': [0],
                           'note': "Player-named rival (Gen II Gold/Silver/Crystal). ROM stores a placeholder — the player's chosen name is not in the data. Use trainer:RIVAL to see all rival battles in sequence, or trainer:8 by index."},
                'silver': {'canonical': 'Silver', 'class_ids': [23],
                           'note': 'Player-named rival (HGSS). Class 23 = Rival.'},
                'barry':  {'canonical': 'Barry',  'class_ids': [95, 96],
                           'note': 'Player-named rival (DP/Pt). Class 95 vs male player, 96 vs female. Not in trainer_names.'},
                'hugh':   {'canonical': 'Hugh',   'class_ids': [145],
                           'note': 'Player-named rival (BW2). Stored as placeholder in trainer_names. 3 files/encounter = one per starter; 6 files = 3 starters x 2 genders (Nate/Rosa).'},
            }
            rival = RIVAL_LOOKUP.get(query.strip().lower())
            rival_note = None
            if rival and current_rom:
                try:
                    gc = current_rom.get('header', {}).get('game_code', '')
                    trdata_path = GAME_INFO.get(gc, {}).get('narcs', {}).get('trdata')
                    if trdata_path:
                        td_files = _get_narc(trdata_path).files
                        for fi, td in enumerate(td_files):
                            if len(td) >= 2 and td[1] in rival['class_ids']:
                                results.append({'table': 'trdata', 'index': fi, 'name': rival['canonical']})
                        rival_note = rival.get('note', '')
                    elif current_rom.get('type') in ('gba', 'gb', 'gbc'):
                        for off in text_tables.get('trainer_offsets', []):
                            if off + 2 <= len(bytes(current_rom.get('data') or b'')) and \
                               current_rom['data'][off + 1] in rival['class_ids']:
                                results.append({'table': 'trainer_names', 'index': off, 'name': rival['canonical']})
                        rival_note = rival.get('note', '')
                except Exception:
                    pass

            # ── Format output as plain text ──

            def _compress_indices(indices):
                """Compress [1,2,3,5,7,8,9] → '1-3, 5, 7-9'."""
                if not indices:
                    return ''
                nums = sorted(set(indices))
                ranges = []
                start = prev = nums[0]
                for n in nums[1:]:
                    if n == prev + 1:
                        prev = n
                    else:
                        ranges.append(f"{start}-{prev}" if prev > start else str(start))
                        start = prev = n
                ranges.append(f"{start}-{prev}" if prev > start else str(start))
                return ', '.join(ranges)

            if not results:
                # Nothing found — check trainer_classes as fallback
                if not table or table == 'trainer_names':
                    if 'trainer_names' in text_tables and 'trainer_classes' in text_tables:
                        class_hits = []
                        for idx, entry in enumerate(text_tables['trainer_classes']):
                            if isinstance(entry, str) and query in entry.lower():
                                class_hits.append((idx, entry))
                        if class_hits:
                            out = ["No matches in trainer_names.", "", "Matches in trainer_classes:"]
                            for idx, cn in class_hits:
                                out.append(f"  [{idx}] {cn}")
                            out.append("")
                            out.append(
                                "Player-named rivals have no entry in trainer_names "
                                "because the player sets their name at game start."
                            )
                            return "\n".join(out)
                return f"No results for '{name}'."

            # Group results by name
            from collections import OrderedDict
            by_name = OrderedDict()
            for r in results:
                rn = r.get('name', '???')
                by_name.setdefault(rn, []).append(r)

            # NARC role labels
            gc = current_rom['header']['game_code'] if current_rom else ''
            narcs = GAME_INFO.get(gc, {}).get('narcs', {})
            _ROLE_LABELS = {'trdata': 'TRData', 'trpoke': 'TRPoke', 'personal': 'Personal',
                           'learnsets': 'Learnsets', 'evolutions': 'Evolutions',
                           'moves': 'Moves', 'items': 'Items'}

            out = []
            out.append("Note: Paths are formatted as a/x/x/x:index. "
                       "Hyphenated indices (e.g. 709-711) include all files in that range.")
            out.append("---")

            # Separate trainer/PWT hits from other table hits
            trainer_names_set = set()
            for rn, hits in by_name.items():
                for h in hits:
                    if h.get('table') in ('trainer_names', 'trdata', 'trainer_classes') or h.get('table', '').startswith('pwt_'):
                        trainer_names_set.add(rn)

            # Show trainer results first (full format)
            for rn, hits in by_name.items():
                if rn not in trainer_names_set:
                    continue
                out.append(f"Name: {rn}")

                pwt_hits = [h for h in hits if h.get('table', '').startswith('pwt_')]
                regular = [h for h in hits if not h.get('table', '').startswith('pwt_')]

                if regular:
                    # Collect indices per NARC
                    narc_indices = {}
                    for h in regular:
                        tbl = h.get('table', '')
                        idx = h.get('index', 0)
                        if tbl == 'trainer_names':
                            for role in ('trdata', 'trpoke'):
                                np = narcs.get(role)
                                if np:
                                    narc_indices.setdefault(np, []).append(idx)
                        elif tbl == 'trdata':
                            np = narcs.get('trdata')
                            if np:
                                narc_indices.setdefault(np, []).append(idx)
                                tp = narcs.get('trpoke')
                                if tp:
                                    narc_indices.setdefault(tp, []).append(idx)

                    # Merge NARCs sharing the same indices (trdata + trpoke)
                    idx_groups = {}
                    for np, idxs in narc_indices.items():
                        key = tuple(sorted(set(idxs)))
                        idx_groups.setdefault(key, []).append(np)

                    for idxs, narc_paths in idx_groups.items():
                        labels = []
                        for np in sorted(narc_paths):
                            rlabel = ''
                            for role, rpath in narcs.items():
                                if rpath == np and role in _ROLE_LABELS:
                                    rlabel = _ROLE_LABELS[role]
                                    break
                            labels.append(f"{np} ({rlabel})" if rlabel else np)
                        narc_str = ' and '.join(labels)
                        out.append(f"")
                        out.append(f"Indices within {narc_str}:")
                        out.append(f"")
                        out.append(_compress_indices(list(idxs)))

                if pwt_hits:
                    tb_path = _role_path('pwt_trainers_b') or 'a/2/5/4'
                    out.append("----")
                    out.append(f"Indices within {tb_path} (PWT):")
                    out.append("")
                    for h in pwt_hits:
                        idx = h.get('index', 0)
                        tourns = pwt_entry_tournaments.get(idx, [])
                        if tourns:
                            for tn in tourns:
                                out.append(f"{idx} ({tn})")
                        else:
                            out.append(str(idx))

                # Tournament results — show participants with their PWT paths
                tourn_hits = [h for h in hits if h.get('table') == 'tournament_names']
                if tourn_hits:
                    tc_map = text_tables.get('tournament_classes', {})
                    cls_list = text_tables.get('trainer_classes', [])
                    tb_path = _role_path('pwt_trainers_b') or 'a/2/5/4'
                    for th in tourn_hits:
                        tidx = th.get('index', 0)
                        class_ids = tc_map.get(tidx, [])
                        if not class_ids:
                            continue
                        out.append("")
                        out.append("Participants:")
                        out.append("")
                        for cid in class_ids:
                            pname = cls_list[cid] if cid < len(cls_list) else f'class#{cid}'
                            # Find PWT entry for this participant
                            entries = pwt_name_to_entries.get(pname.lower(), [])
                            if entries:
                                out.append(f"{pname} ({tb_path}:{entries[0]})")
                            else:
                                out.append(pname)

                if rival_note and rn == rival.get('canonical'):
                    out.append(f"\n{rival_note}")

            # Non-trainer results: show decipher paths when available
            other = [(rn, hits) for rn, hits in by_name.items() if rn not in trainer_names_set]
            if other:
                if trainer_names_set:
                    out.append("")
                    out.append("----")
                for rn, hits in other:
                    for h in hits:
                        tbl, idx = h.get('table', ''), h.get('index', 0)
                        paths = h.get('paths', [])
                        if paths:
                            out.append(f"{rn} ({tbl} #{idx})  \u2192  {', '.join(paths)}")
                        else:
                            out.append(f"{rn} ({tbl} #{idx})")

            return "\n".join(out)
        
        # name + narc_path: resolve matches to IDs, search NARC for those IDs as LE u16
        try:
            narc = _get_narc(narc_path.lstrip("/"))
        except Exception as e:
            return {"error": f"Could not open NARC: {e}"}
        narc_hits = []
        for match in results:
            sid = match["index"]
            sb = struct.pack("<H", sid)
            for fidx, fdata in enumerate(narc.files):
                if sb in fdata:
                    narc_hits.append({"file": f"{narc_path}:{fidx}", "name": match["name"], "id": sid})
        return {"query": name, "narc": narc_path, "text_matches": results, "narc_matches": narc_hits, "count": len(narc_hits)}
    
    # Hex pattern search
    if hex:
        if current_rom["type"] != "nds":
            return {"error": "Hex search only supported for NDS"}
        if not narc_path:
            return {"error": "Provide narc_path, arm9.bin, arm7.bin, or overlayN.bin"}
        search_bytes = bytes.fromhex(hex.replace(" ", ""))

        # ARM9 / ARM7
        if narc_path.lower() in ("arm9.bin", "arm7.bin"):
            data = bytes(current_rom["arm9_data"] if narc_path.lower() == "arm9.bin" else current_rom["arm7_data"])
            offsets, pos = [], 0
            while True:
                pos = data.find(search_bytes, pos)
                if pos < 0: break
                offsets.append(f"0x{pos:X}")
                pos += 1
            return {"pattern": hex, "path": narc_path, "matches": offsets, "count": len(offsets)}

        # Overlay
        ov_id = _is_overlay_path(narc_path)
        if ov_id >= 0:
            overlays = current_rom.get("overlays", {})
            if ov_id not in overlays:
                return {"error": f"Overlay {ov_id} not found (available: {sorted(overlays.keys())})"}
            data = bytes(overlays[ov_id])
            offsets, pos = [], 0
            while True:
                pos = data.find(search_bytes, pos)
                if pos < 0: break
                offsets.append(f"0x{pos:X}")
                pos += 1
            return {"pattern": hex, "path": narc_path, "matches": offsets, "count": len(offsets)}

        # NARC
        try:
            narc = _get_narc(narc_path.lstrip("/"))
        except Exception as e:
            return {"error": f"Could not open NARC: {e}"}
        results = []
        for idx, fdata in enumerate(narc.files):
            offsets, pos = [], 0
            while True:
                pos = fdata.find(search_bytes, pos)
                if pos < 0: break
                offsets.append(pos)
                pos += 1
            if offsets:
                results.append({"file": f"{narc_path}:{idx}", "offsets": offsets})
        return {"pattern": hex, "narc": narc_path, "matches": results, "count": len(results)}
    
    return {"error": "Provide either name (text lookup) or hex (hex search)"}


async def judgement(path_a: str, path_b: str) -> dict:
    """Compare two files. Supports cross-ROM: 'IRE:a/0/1/6:1' vs 'IPK:a/0/0/2:1'."""
    if not current_rom:
        return "Error: No ROM currently open"

    if current_rom['type'] != 'nds':
        return {"error": "Diff only supported for NDS"}

    def resolve_path(p):
        """Resolve a path, handling cross-ROM prefixes."""
        gc_prefix, clean_p = _parse_rom_prefix(p)
        if gc_prefix and gc_prefix != current_rom['header']['game_code']:
            orig_gc = _switch_rom(gc_prefix)
            try:
                return _resolve_nds_file(clean_p)
            finally:
                _switch_rom(orig_gc)
        return _resolve_nds_file(clean_p if gc_prefix else p)

    try:
        data_a = resolve_path(path_a)
        data_b = resolve_path(path_b)
    except Exception as e:
        return {"error": str(e)}

    max_len = max(len(data_a), len(data_b))
    raw_diffs = []
    for i in range(max_len):
        ba = data_a[i] if i < len(data_a) else None
        bb = data_b[i] if i < len(data_b) else None
        if ba != bb:
            raw_diffs.append((i, ba, bb))

    # Group into consecutive ranges
    ranges = []
    for off, ba, bb in raw_diffs:
        if ranges and off == ranges[-1]['end'] + 1:
            ranges[-1]['end'] = off
            ranges[-1]['count'] += 1
        else:
            ranges.append({"start": off, "end": off, "count": 1,
                           "a": f"{ba:02X}" if ba is not None else "N/A",
                           "b": f"{bb:02X}" if bb is not None else "N/A"})

    # Summarise each range
    diff_summary = []
    for r in ranges[:50]:
        s = f"0x{r['start']:X}"
        if r['count'] > 1: s += f"-0x{r['end']:X} ({r['count']} bytes)"
        diff_summary.append({"range": s, "a": r['a'], "b": r['b']})

    return {
        "identical": not raw_diffs,
        "size_a": len(data_a), "size_b": len(data_b),
        "diff_regions": len(ranges),
        "diff_bytes": len(raw_diffs),
        "differences": diff_summary
    }



async def stats(**_) -> dict:
    """Coverage: how much of the ROM the server can decode and has indexed."""
    if not current_rom:
        # Still show server status even without a ROM
        eonet_status = {}
        try:
            proxy_log = Path.home() / ".linkplay" / "eonet_proxy.log"
            pid_file = Path.home() / ".linkplay" / "eonet_proxy.pid"
            eonet_status["proxy_pid_file"] = pid_file.exists()
            if pid_file.exists():
                pid = int(pid_file.read_text().strip())
                try:
                    os.kill(pid, 0)
                    eonet_status["proxy_alive"] = True
                except OSError:
                    eonet_status["proxy_alive"] = False
                eonet_status["proxy_pid"] = pid
            if proxy_log.exists():
                lines = proxy_log.read_text(encoding='utf-8', errors='ignore').strip().split('\n')
                eonet_status["last_log"] = lines[-3:] if len(lines) >= 3 else lines
        except Exception:
            eonet_status["error"] = "could not check"
        return {
            "status": "no ROM loaded",
            "loaded_roms": {
                gc: {
                    "title": state['current_rom']['header'].get('game_title', '?'),
                    "path": state['current_rom']['header'].get('rom_path', '?'),
                }
                for gc, state in loaded_roms.items()
            },
            "eonet": eonet_status,
        }
    gc = current_rom['header']['game_code']
    fpn_notes = current_flipnote['data'].get('notes', {}) if current_flipnote else {}
    rom_stats = current_flipnote['data'].get('rom_stats', {}) if current_flipnote else {}
    total_bytes = rom_stats.get('total_bytes', 0)

    # ICR index coverage
    index = eonet_index.get(gc, [])
    labels = eonet_labels.get(gc, {})
    roles = narc_roles

    indexed_narcs = len([k for k in labels if k != '_cross_refs' and ':' not in k])
    indexed_files = len(index)
    role_counts = {}
    for path, role in roles.items():
        role_counts[role] = role_counts.get(role, 0) + 1

    # Flipnote notes (manual annotations)
    manual_notes = len(fpn_notes)

    # Decoded roles vs what _auto_decode handles
    handled_roles = {
        'trpoke', 'trdata', 'personal', 'learnsets', 'evolutions', 'move_data',
        'encounters', 'items', 'contest', 'pokeathlon_performance',
        'battle_tower_pokemon', 'battle_tower_trainers',
        'subway_pokemon', 'subway_trainers',
        'pwt_rental', 'pwt_rental_b', 'pwt_champions', 'pwt_champions_b',
        'pwt_trainers', 'pwt_trainers_b', 'pwt_rosters', 'pwt_rosters_b',
        'pwt_defs', 'pwt_mix', 'pwt_trainer_map',
    }
    decoded_roles = {r: c for r, c in role_counts.items() if r in handled_roles}
    unknown_roles = {r: c for r, c in role_counts.items() if r not in handled_roles}

    # Eonet proxy status
    eonet_status = {}
    try:
        proxy_log = Path.home() / ".linkplay" / "eonet_proxy.log"
        pid_file = Path.home() / ".linkplay" / "eonet_proxy.pid"
        eonet_status["proxy_pid_file"] = pid_file.exists()
        if pid_file.exists():
            pid = int(pid_file.read_text().strip())
            # Check if process is alive
            import signal
            try:
                os.kill(pid, 0)
                eonet_status["proxy_alive"] = True
            except OSError:
                eonet_status["proxy_alive"] = False
            eonet_status["proxy_pid"] = pid
        if proxy_log.exists():
            # Last 3 log lines
            lines = proxy_log.read_text(encoding='utf-8', errors='ignore').strip().split('\n')
            eonet_status["last_log"] = lines[-3:] if len(lines) >= 3 else lines
    except Exception:
        eonet_status["error"] = "could not check"

    return {
        "game": current_rom['header']['game_title'],
        "rom_size": f"{total_bytes / 1024 / 1024:.1f} MB" if total_bytes else "?",
        "startup_log": _startup_log if _startup_log else ["clean start"],
        "text_tables_detected": [k for k in text_tables if isinstance(k, str) and isinstance(text_tables.get(k), list)],
        "loaded_roms": {
            gc: {
                "title": state['current_rom']['header'].get('game_title', '?'),
                "path": state['current_rom']['header'].get('rom_path', '?'),
            }
            for gc, state in loaded_roms.items()
        },
        "icr": {
            "narcs_indexed": indexed_narcs,
            "files_indexed": indexed_files,
            "status": "cached" if indexed_files > 0 else "not built yet",
        },
        "decoded_roles": decoded_roles,
        "unknown_roles": unknown_roles,
        "manual_notes": manual_notes,
        "eonet": eonet_status,
    }


# ============ Flipnote Tools ============

async def list_flipnotes(**_) -> dict:
    """List all known game Flipnotes."""
    ensure_dirs()

    flipnotes = []
    for fpn in flipnotes_dir.glob("*.fpn"):
        try:
            with open(fpn, 'r', encoding='utf-8') as f:
                data = json.load(f)
                codes = data.get('game_codes', [])
                if not codes:
                    codes = [data.get('game_code', '')]
                flipnotes.append({
                    "game_codes": codes,
                    "title": data.get('game_title'),
                    "path": str(fpn),
                    "note_count": len(data.get('notes', {}))
                })
        except:
            continue

    return {"flipnotes": flipnotes}


async def view_flipnote(game: str, search: str = None, summary: bool = False) -> dict:
    """View a Flipnote. search= filters notes by path/description. summary=True returns note count + paths only."""
    ensure_dirs()
    for fpn in flipnotes_dir.glob("*.fpn"):
        try:
            with open(fpn, 'r', encoding='utf-8') as f:
                data = json.load(f)
            codes = data.get('game_codes', []) or [data.get('game_code', '')]
            title_lower = data.get('game_title', '').lower()
            if game not in codes and not all(w in title_lower for w in game.lower().split()):
                continue
            notes = data.get('notes', {})
            if search:
                q = search.lower()
                notes = {k: v for k, v in notes.items()
                         if q in k.lower() or q in (v.get('description', '') if isinstance(v, dict) else str(v)).lower()}
            if summary:
                return {"game_codes": codes, "game_title": data.get("game_title"),
                        "region_codes": data.get("region_codes", {}),
                        "note_count": len(notes), "paths": list(notes.keys())}
            return {"game_codes": codes, "game_title": data.get("game_title"),
                    "region_codes": data.get("region_codes", {}),
                    "note_count": len(notes), "notes": notes}
        except:
            continue
    return {"error": f"Flipnote not found for: {game}"}


def _get_flipnote(game=None):
    """Resolve flipnote data + save path. Returns (fpn_data, save_path, in_memory) or error dict."""
    if game:
        fpn_path = find_flipnote(game)
        if not fpn_path:
            return {"error": f"No flipnote for game: {game}"}
        with open(fpn_path, 'r', encoding='utf-8') as f:
            return json.load(f), fpn_path, False
    if current_flipnote:
        return current_flipnote['data'], current_flipnote['path'], True
    return {"error": "No ROM open and no game specified"}


def _save_flipnote(fpn_data, save_path, in_memory):
    """Save flipnote data to disk and update in-memory state."""
    with open(save_path, 'w', encoding='utf-8') as f:
        json.dump(fpn_data, f, indent=2, ensure_ascii=False)
    if in_memory:
        current_flipnote['data'] = fpn_data


def _log_note(**kwargs):
    """Append a note to the persistent history. Server's own record."""
    try:
        with open(note_history, 'a', encoding='utf-8') as f:
            f.write(json.dumps({k: v for k, v in kwargs.items() if v is not None}) + '\n')
    except:
        pass


async def note(path: str, description: str, name: str = None, format: str = None,
               tags: list = None, file_range: str = None, examples: list = None,
               related: list = None, game: str = None) -> dict:
    """Add a note to a Flipnote. Defaults to current ROM, or specify game code."""
    result = _get_flipnote(game)
    if isinstance(result, dict):
        return result
    fpn_data, save_path, in_memory = result
    fpn_data.setdefault('notes', {})[path] = {"description": description}
    if name: fpn_data['notes'][path]["name"] = name
    if format: fpn_data['notes'][path]["format"] = format
    if tags: fpn_data['notes'][path]["tags"] = tags
    if file_range: fpn_data['notes'][path]["file_range"] = file_range
    if examples: fpn_data['notes'][path]["examples"] = examples
    if related: fpn_data['notes'][path]["related"] = related
    _save_flipnote(fpn_data, save_path, in_memory)
    _log_note(path=path, description=description, name=name, format=format,
              tags=tags, file_range=file_range, related=related)
    return {"noted": path, "description": description}


async def batch_notes(notes: list, game: str = None) -> dict:
    """Write multiple notes at once. Each note: {path, description, name?, format?, tags?}.
    Defaults to current ROM, or specify game code. Single disk write."""
    result = _get_flipnote(game)
    if isinstance(result, dict):
        return result
    fpn_data, save_path, in_memory = result
    fpn_data.setdefault('notes', {})
    written = 0
    for n in notes:
        p = n.get('path')
        d = n.get('description')
        if not p or not d:
            continue
        entry = {"description": d}
        if n.get('name'): entry['name'] = n['name']
        if n.get('format'): entry['format'] = n['format']
        if n.get('tags'): entry['tags'] = n['tags']
        if n.get('file_range'): entry['file_range'] = n['file_range']
        if n.get('related'): entry['related'] = n['related']
        fpn_data['notes'][p] = entry
        _log_note(path=p, description=d, name=n.get('name'), format=n.get('format'),
                  tags=n.get('tags'), file_range=n.get('file_range'), related=n.get('related'))
        written += 1
    _save_flipnote(fpn_data, save_path, in_memory)
    return {"written": written, "total_notes": len(fpn_data['notes'])}


async def edit_note(path: str, description: str = None, name: str = None, format: str = None,
                    tags: list = None, file_range: str = None, examples: list = None,
                    related: list = None, game: str = None) -> dict:
    """Edit an existing note in the Flipnote."""
    result = _get_flipnote(game)
    if isinstance(result, dict):
        return result
    fpn_data, save_path, in_memory = result
    if path not in fpn_data.get('notes', {}):
        return {"error": f"Note not found: {path}"}
    if description: fpn_data['notes'][path]["description"] = description
    if name is not None: fpn_data['notes'][path]["name"] = name
    if format is not None: fpn_data['notes'][path]["format"] = format
    if tags is not None: fpn_data['notes'][path]["tags"] = tags
    if file_range is not None: fpn_data['notes'][path]["file_range"] = file_range
    if examples is not None: fpn_data['notes'][path]["examples"] = examples
    if related is not None: fpn_data['notes'][path]["related"] = related
    _save_flipnote(fpn_data, save_path, in_memory)
    return {"edited": path}


async def delete_note(path: str, game: str = None) -> dict:
    """Delete a note from the Flipnote."""
    result = _get_flipnote(game)
    if isinstance(result, dict):
        return result
    fpn_data, save_path, in_memory = result
    if path not in fpn_data.get('notes', {}):
        return {"error": f"Note not found: {path}"}
    del fpn_data['notes'][path]
    _save_flipnote(fpn_data, save_path, in_memory)
    return {"deleted": path}




async def probe(path: str, offset: int = 0, reads: str = "u16", count: int = 1,
                xor: str = None, endian: str = "little", stride: int = 0,
                base: int = 0) -> dict:
    """Structured binary read. No manual hex math needed.
    Types: u8, u16, u32, s8, s16, s32, ptr32 (follow pointer), text (decode text file).
    """
    if not current_rom:
        return "Error: No ROM currently open"
    gc_prefix, clean_path = _parse_rom_prefix(path)
    if gc_prefix and gc_prefix != current_rom['header']['game_code']:
        orig_gc = _switch_rom(gc_prefix)
        try:
            return await probe(clean_path, offset, reads, count, xor, endian, stride, base)
        finally:
            _switch_rom(orig_gc)
    elif gc_prefix:
        path = clean_path
    try:
        if current_rom['type'] not in ('nds', '3ds'):
            with open(current_rom['path'], 'rb') as f:
                data = f.read()
        else:
            data = _resolve_nds_file(path)
            # NARC internals and named files may be compressed; arm9/arm7/overlays are pre-decompressed
            if path.lower() not in ('arm9.bin', 'arm7.bin') and _is_overlay_path(path) < 0:
                data, _ = decompress_data(data)
    except Exception as e:
        return {"error": f"Failed to read {path}: {e}"}
    if xor:
        xk = bytes.fromhex(xor.replace(' ', ''))
        data = bytes(b ^ xk[i % len(xk)] for i, b in enumerate(data))
    if reads == 'text':
        gen = text_gen or 5
        if gen == 5 and text_mult is not None:
            strings = decode_gen5_text(data, text_mult)
        elif gen == 4:
            strings = decode_gen4_text(data)
        else:
            return {"error": "No text decoder available"}
        return {"path": path, "type": "text", "entries": len(strings),
                "strings": strings[:count] if count < len(strings) else strings}
    type_info = {
        'u8': (1, 'B'), 'u16': (2, 'H'), 'u32': (4, 'I'),
        's8': (1, 'b'), 's16': (2, 'h'), 's32': (4, 'i'),
        'ptr32': (4, 'I'),
    }
    if reads not in type_info:
        return {"error": f"Unknown type: {reads}. Use: u8 u16 u32 s8 s16 s32 ptr32 text"}
    size, fmt_char = type_info[reads]
    bo = '<' if endian == 'little' else '>'
    step = stride if stride > 0 else size
    results = []
    # Determine annotation mode from NARC role if path is a NARC file
    _role_hint = None
    if ':' in path:
        _np = path.rsplit(':', 1)[0].lstrip('/')
        _role_hint = narc_roles.get(_np)
    _ROLE_ANNOT = {
        'personal': [('species', 'species', 700)],
        'learnsets': [('moves', 'move', 600)],
        'move_data': [('type_names', 'type', 20)],
        'items': [('items', 'item', 800)],
        'trdata': [('trainer_classes', 'class', 300), ('trainer_names', 'trainer', 900)],
        'trpoke': [('species', 'species', 700), ('moves', 'move', 600), ('items', 'item', 800)],
        'encounters': [('species', 'species', 700)],
        'evolutions': [('species', 'species', 700), ('items', 'item', 800), ('moves', 'move', 600)],
    }
    _annot_tables = _ROLE_ANNOT.get(_role_hint) if _role_hint else None
    # Fallback for arm9/unknown: annotate all
    if _annot_tables is None and reads in ('u16', 'u32'):
        _annot_tables = [
            ('species', 'species', 700), ('moves', 'move', 600),
            ('items', 'item', 800), ('trainer_names', 'trainer', 900),
        ]
    for i in range(count):
        pos = offset + i * step
        if pos + size > len(data):
            results.append({"i": i, "off": f"0x{pos:X}", "error": "EOF"})
            break
        val = struct.unpack_from(f'{bo}{fmt_char}', data, pos)[0]
        entry = {"i": i, "off": f"0x{pos:X}", "val": val, "hex": f"0x{val:0{size*2}X}"}
        if _annot_tables and val > 0:
            for tname, label, cap in _annot_tables:
                tbl = text_tables.get(tname, [])
                if val < len(tbl) and val < cap:
                    s = tbl[val]
                    if isinstance(s, str) and s.strip():
                        entry[label] = s.strip()
        if reads == 'ptr32' and val > 0:
            foff = val - base if base else val
            entry["file_off"] = f"0x{foff:X}"
            if 0 <= foff < len(data):
                peek = data[foff:foff + 16]
                entry["peek"] = ' '.join(f'{b:02X}' for b in peek)
        results.append(entry)
    _path_notes = _notes_for_path(path)
    out = {"path": path, "offset": f"0x{offset:X}", "type": reads, "count": len(results)}
    if _path_notes:
        out["known"] = _path_notes
    if len(results) == 1:
        out.update(results[0])
    else:
        out["values"] = results
    return out


# ============ Output Framing ============

def _get_game_title(gc=None):
    """Get cleaned game title for a game code (or current ROM)."""
    if not gc and current_rom:
        gc = current_rom['header']['game_code']
    if not gc:
        return ''
    gt = GAME_INFO.get(gc, {}).get('title', '')
    if not gt:
        rom_state = loaded_roms.get(gc, {}).get('current_rom') if gc else None
        if rom_state:
            gt = rom_state['header'].get('game_title', gc)
        elif current_rom:
            gt = current_rom['header'].get('game_title', gc)
        else:
            gt = gc
    return gt.replace(' Nintendo', '').replace(' Game Freak', '').strip()

def _difficulty_label(path_str):
    """For B2W2 trdata paths, label difficulty block."""
    if not current_rom or text_gen != 5:
        return ''
    try:
        gc = current_rom['header'].get('game_code', '')
        if gc in ('IRB', 'IRA'):
            return ''
        trdata_path = GAME_INFO.get(gc, {}).get('narcs', {}).get('trdata', '')
        trpoke_path = GAME_INFO.get(gc, {}).get('narcs', {}).get('trpoke', '')
        path_narc = path_str.rsplit(':', 1)[0] if ':' in path_str else path_str
        if path_narc not in (trdata_path, trpoke_path):
            return ''
        import re as _re
        m = _re.search(r':(\d+)$', path_str)
        if not m:
            return ''
        file_idx = int(m.group(1))
        BW2_EXPLICIT_LABELS = {
            38:'Normal Mode | Pre-Champion',   143:'Challenge Mode | Pre-Champion',
            772:'Normal Mode | Post-Champion', 777:'Challenge Mode | Post-Champion',
            39:'Normal Mode | Pre-Champion',   144:'Challenge Mode | Pre-Champion',
            774:'Normal Mode | Post-Champion', 779:'Challenge Mode | Post-Champion',
            40:'Normal Mode | Pre-Champion',   145:'Challenge Mode | Pre-Champion',
            773:'Normal Mode | Post-Champion', 778:'Challenge Mode | Post-Champion',
            41:'Normal Mode | Pre-Champion',   146:'Challenge Mode | Pre-Champion',
            775:'Normal Mode | Post-Champion', 780:'Challenge Mode | Post-Champion',
            341:'Normal Mode | Pre-Champion',  536:'Challenge Mode | Pre-Champion',
            776:'Normal Mode | Post-Champion', 781:'Challenge Mode | Post-Champion',
        }
        if file_idx in BW2_EXPLICIT_LABELS:
            return BW2_EXPLICIT_LABELS[file_idx]
        _BW2_CHALLENGE_START = 764
        if file_idx >= _BW2_CHALLENGE_START:
            return 'Challenge Mode'
        if file_idx + 608 < 814:
            return 'Normal Mode'
    except Exception:
        pass
    return ''

def _frame(decoded_str, path_str, gc=None):
    """Wrap decoded text in bar frame with game title and path."""
    bar = chr(9552) * 39
    lines = decoded_str.split('\n', 1)
    title = lines[0]
    body = lines[1] if len(lines) > 1 else ''
    diff = _difficulty_label(path_str)
    diff_tag = f' [{diff}]' if diff else ''
    gt = _get_game_title(gc)
    if path_str:
        header = f"{bar}\n{title}{diff_tag}\n{gt} | {path_str}\n{bar}"
    else:
        header = f"{bar}\n{title}{diff_tag}\n{bar}"
    if body:
        return f"{header}\n\n{body}\n{bar}"
    return f"{header}\n{bar}"

# ============ Server Setup ============

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    """Route tool calls to handler functions."""
    # Wait for background restore if still running, or trigger if it never started
    if not _rom_restore_done:
        if _restore_task is not None:
            await _restore_task
        else:
            await _do_pending_restore()
    handlers = {
        "spotlight": spotlight,
        "return": return_tool,
        "summarize": summarize,
        "decipher": decipher,
        "sketch": sketch,
        "narc_append": narc_append,
        "sprite_convert": sprite_convert,
        "record": record,
        "scope": scope,
        "dowse": dowse,
        "judgement": judgement,
        "stats": stats,
        "list_flipnotes": list_flipnotes,
        "view_flipnote": view_flipnote,
        "note": note,
        "batch_notes": batch_notes,
        "edit_note": edit_note,
        "delete_note": delete_note,
        "probe": probe
    }

    handler = handlers.get(name)
    if not handler:
        raise ValueError(f"Unknown tool: {name}")
    
    result = await handler(**arguments)

    # Format result as readable text
    if isinstance(result, dict):
        if 'error' in result:
            text = f"Error: {result['error']}"
        else:
            lines = []
            for k, v in result.items():
                if isinstance(v, dict):
                    lines.append(f"{k}:")
                    for kk, vv in v.items():
                        lines.append(f"  {kk}: {vv}")
                elif isinstance(v, list) and v and isinstance(v[0], dict):
                    lines.append(f"{k}:")
                    for item in v[:50]:
                        lines.append("  " + "  ".join(f"{kk}: {vv}" for kk, vv in item.items()))
                else:
                    lines.append(f"{k}: {v}")
            text = "\n".join(lines)
    elif isinstance(result, str):
        text = result
    else:
        text = str(result)
    return [TextContent(type="text", text=text)]


@server.list_tools()
async def list_tools():
    return [
        Tool(name="spotlight", description="Open a ROM file for exploration. Second call on the same game restores from ICR cache instantly (no rescan). Returns NARC paths for key roles (trdata, trpoke, personal, learnsets).", inputSchema={
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Absolute path to .nds, .gba, .gbc, or .gb file"}},
            "required": ["path"]
        }),
        Tool(name="return", description="Close the current ROM. If multiple ROMs are open, switches to the next one. Use save=True only when you have sketched changes you want to keep.", inputSchema={
            "type": "object",
            "properties": {"save": {"type": "boolean", "description": "Repack and save before closing (default: false). Only needed after sketch calls."}}
        }),
        Tool(name="summarize", description="List filesystem contents or NARC file indices. Use to explore unknown paths. Skip if the path is already known from spotlight output or ICR.", inputSchema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Folder path (default: root) or NARC file path to list its internal files"},
                "expand_narcs": {"type": "boolean", "description": "Show NARC file count inline (default: false)"}
            }
        }),
        Tool(name="decipher", description="Read and decode a file. Known flipnote notes surface automatically at the top of output — read them before interpreting. Auto-decodes: trainers (trdata+trpoke combined), personal stats, learnsets, evolutions, move data, encounters (with location name), items, Pokeathlon, contest, PWT/subway/tower pools. Returns decoded text when recognized, hex summary otherwise. Path syntax: arm9.bin, narc/path:index, overlay0.bin. Comma-separate for multi-file.", inputSchema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path. NARC files: 'a/0/9/1:156'. ARM: 'arm9.bin'. Cross-ROM: 'IRE:a/0/9/1:156'. Comma-separated for batch."},
                "offset": {"type": "integer", "description": "Byte offset (default: 0)"},
                "length": {"type": "integer", "description": "Bytes to read (default: all)"},
                "decompress": {"type": "boolean", "description": "Auto-decompress LZ10/LZ11 (default: true)"}
            },
            "required": ["path"]
        }),
        Tool(name="sketch", description="Write bytes to a file. Writes in-place to the loaded ROM (not disk) — call record to persist.", inputSchema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path. NARC files: 'a/0/9/1:156'. ARM: 'arm9.bin'. Overlay: 'overlay0.bin'."},
                "data": {"type": "string", "description": "Data to write. Hex by default, or use encoding param."},
                "offset": {"type": "integer", "description": "Byte offset to write at (default: 0)"},
                "encoding": {"type": "string", "enum": ["hex", "utf8", "utf16le", "ascii"], "description": "Data encoding (default: hex)."}
            },
            "required": ["path", "data"]
        }),
        Tool(name="narc_append", description="Append a new file to an existing NARC. Use when adding custom trainers, Pokémon sets, sprites, or tournament data. Returns the new file index.", inputSchema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "NARC path (e.g. a/2/6/7). No file index — always appends."},
                "data": {"type": "string", "description": "Data for the new file. Hex by default."},
                "encoding": {"type": "string", "enum": ["hex", "utf8", "utf16le", "ascii"], "description": "Data encoding (default: hex)."}
            },
            "required": ["path", "data"]
        }),
        Tool(name="sprite_convert", description="Extract sprites from ROM NARCs and save to sprites directory, or convert PNG to NDS tile format (NCGR/NCLR/NSCR). NDS only. Platinum+ auto-converts with PNG preview. D/P extracts raw only. PNG→NDS requires Platinum or later.", inputSchema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "NARC path with file index to extract (e.g. a/0/5/8:42). Required for extraction mode."},
                "source": {"type": "string", "description": "PNG file path on disk for PNG→NDS conversion mode. Omit for extraction mode."},
                "facing": {"type": "string", "enum": ["front", "back"], "description": "Output to front/ or back/ directory. Default: front."}
            },
            "required": []
        }),
        Tool(name="record", description="Repack and save the ROM to disk. Recompresses ARM9 and writes all modified NARCs and overlays. Only needed after sketch calls. Can write to the original path or a new file.", inputSchema={
            "type": "object",
            "properties": {"output_path": {"type": "string", "description": "Output file path (can be same as input to overwrite)"}},
            "required": ["output_path"]
        }),
        Tool(name="scope", description="Raw hex dump. Auto-disassembles ARM9, ARM7, and overlay paths (ARM/Thumb). Flipnote notes surface automatically. Use when decipher doesn't auto-decode and you need to inspect raw bytes, search for a byte pattern, or apply an XOR mask. For structured reads use probe instead.", inputSchema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path (same syntax as decipher)"},
                "offset": {"type": "integer", "description": "Start offset (default: 0)"},
                "length": {"type": "integer", "description": "Bytes to dump (default: 256)"},
                "search": {"type": "string", "description": "Hex pattern to find — returns all offsets"},
                "xor": {"type": "string", "description": "XOR key applied before display (e.g. 'AB' or 'AB CD EF')"}
            }
        }),
        Tool(name="dowse", description="Three modes: (1) name lookup — find species/move/item/trainer/location in text tables, returns decipher-ready paths (e.g. dowse 'Garchomp' → personal, learnset, evolution paths; 'Earthquake' → move data path; 'Leftovers' → item path; 'Route 4' → encounter file paths; trainer names → trdata/trpoke indices). Falls back to NARC role/category search if no text hit. (2) name+narc_path — find NARCs containing that entity as a u16 reference. (3) hex+narc_path — find files in a NARC containing a byte pattern.", inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Entity or category to search. Searches all text tables; falls back to NARC role names if no match."},
                "table": {"type": "string", "description": "Restrict to one table: species, moves, items, abilities, trainer_names, trainer_classes"},
                "exact": {"type": "boolean", "description": "Exact match instead of substring (default: false)"},
                "narc_path": {"type": "string", "description": "With name: find NARCs referencing this entity. With hex: search this NARC for a byte pattern."},
                "hex": {"type": "string", "description": "Hex pattern to find in NARC files (requires narc_path)"},
                "difficulty": {"type": "string", "description": "Filter trainer results by difficulty mode: normal, challenge, easy (BW2 only — Challenge Mode has separate trainer files)"}
            }
        }),
        Tool(name="judgement", description="Byte-level diff of two files. Supports cross-ROM comparison using game code prefix: 'IRE:a/0/1/6:1' vs 'IPK:a/0/0/2:1'. Same path syntax as decipher.", inputSchema={
            "type": "object",
            "properties": {
                "path_a": {"type": "string", "description": "First file path (cross-ROM prefix supported: 'IRE:a/0/9/1:38')"},
                "path_b": {"type": "string", "description": "Second file path"}
            },
            "required": ["path_a", "path_b"]
        }),
        Tool(name="stats", description="Show ICR index coverage: how many NARCs and files have been indexed, which roles are decoded, and how many manual flipnote notes exist. Use to assess what the server knows about the current ROM.", inputSchema={
            "type": "object", "properties": {}
        }),
        Tool(name="list_flipnotes", description="List all flipnotes (one per game pair). Flipnotes store manual notes that persist across all restarts. Use view_flipnote to read a specific one.", inputSchema={
            "type": "object", "properties": {}
        }),
        Tool(name="view_flipnote", description="Read the flipnote for a game. summary=True returns paths only (cheaper). search= filters notes by path or description.", inputSchema={
            "type": "object",
            "properties": {
                "game": {"type": "string", "description": "Game code (e.g. IRE) or partial title (e.g. Black 2)"},
                "search": {"type": "string", "description": "Filter notes by path or description keyword"},
                "summary": {"type": "boolean", "description": "Return paths only, no descriptions (default: false)"}
            },
            "required": ["game"]
        }),
        Tool(name="note", description="Permanently record a discovery. Notes survive all restarts. Use immediately after finding something — NARC role, format, offset, bracket mapping, anything. Prefer batch_notes for 3+ notes.", inputSchema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path being documented"},
                "description": {"type": "string", "description": "What this path contains"},
                "name": {"type": "string", "description": "Human-readable name"},
                "format": {"type": "string", "description": "File format description"},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags for categorization"},
                "file_range": {"type": "string", "description": "Description of file range"},
                "examples": {"type": "array", "items": {"type": "string"}, "description": "Example files"},
                "related": {"type": "array", "items": {"type": "string"}, "description": "Related paths"},
                "game": {"type": "string", "description": "Game code to write to (e.g. IPK, IRE). Defaults to current ROM."}
            },
            "required": ["path", "description"]
        }),
        Tool(name="batch_notes", description="Write multiple notes in one disk write. Use instead of repeated note calls when documenting multiple paths at once.", inputSchema={
            "type": "object",
            "properties": {
                "notes": {"type": "array", "items": {"type": "object", "properties": {
                    "path": {"type": "string"}, "description": {"type": "string"},
                    "name": {"type": "string"}, "format": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "file_range": {"type": "string"}, "related": {"type": "array", "items": {"type": "string"}}
                }, "required": ["path", "description"]}, "description": "Array of notes to write"},
                "game": {"type": "string", "description": "Game code (defaults to current ROM)"}
            },
            "required": ["notes"]
        }),
        Tool(name="edit_note", description="Edit an existing note", inputSchema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path of note"},
                "description": {"type": "string", "description": "New description"},
                "name": {"type": "string", "description": "Human-readable name"},
                "format": {"type": "string", "description": "File format description"},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags"},
                "file_range": {"type": "string", "description": "File range description"},
                "examples": {"type": "array", "items": {"type": "string"}, "description": "Examples"},
                "related": {"type": "array", "items": {"type": "string"}, "description": "Related paths"},
                "game": {"type": "string", "description": "Game code (defaults to current ROM)"}
            },
            "required": ["path"]
        }),
        Tool(name="probe", description="Structured binary read. Known flipnote notes for the path surface automatically — read them before interpreting raw values. Primary for ARM9, overlay, unknown binary. Types: u8/u16/u32/s8/s16/s32/ptr32/text. Auto-annotates values with species/move/item names when they match.", inputSchema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path (arm9.bin, narc:index, overlay#.bin, or ROM file path)"},
                "offset": {"type": "integer", "description": "Byte offset to start reading (default: 0)"},
                "reads": {"type": "string", "description": "Type to read: u8/u16/u32/s8/s16/s32/ptr32/text (default: u16)"},
                "count": {"type": "integer", "description": "Number of values to read (default: 1)"},
                "xor": {"type": "string", "description": "XOR key hex (e.g. AB CD)"},
                "endian": {"type": "string", "enum": ["little", "big"], "description": "Byte order (default: little)"},
                "stride": {"type": "integer", "description": "Bytes between reads, 0=packed (default: 0)"},
                "base": {"type": "integer", "description": "Base address for ptr32 pointer arithmetic"}
            },
            "required": ["path"]
        }),
        Tool(name="delete_note", description="Delete a note", inputSchema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path of note to delete"},
                "game": {"type": "string", "description": "Game code (defaults to current ROM)"}
            },
            "required": ["path"]
        })
    ]


if __name__ == "__main__":
    import asyncio
    from mcp.server.stdio import stdio_server

    async def main():
        async with stdio_server() as (read_stream, write_stream):
            setup_tools()
            ensure_dirs()
            
            # Restore ROMs in background — don't block MCP handshake
            global _restore_task
            _restore_task = asyncio.create_task(_do_pending_restore())
            
            # Recover notes from past conversations on startup
            try:
                recovered = recover_notes_from_logs()
            except Exception:
                recovered = 0
            try:
                @server.request_handler("eonet/resolve")
                async def handle_eonet_resolve(params):
                    return eonet_resolve(params.get("message", ""), params.get("game_code"))
            except Exception:
                pass
            
            # Keepalive: ping the connection every 30s to prevent idle timeout
            async def keepalive():
                while True:
                    await asyncio.sleep(30)
                    try:
                        # Write a comment to stderr to keep the pipe alive
                        print("[linkplay] keepalive", file=sys.stderr, flush=True)
                    except:
                        break
            
            keepalive_task = asyncio.create_task(keepalive())
            
            # Try to wrap read stream with Eonet interception for IDE support
            # If it fails, fall back to raw stream
            try:
                from eonet_driver import _EonetInterceptStream
                intercepted_read = _EonetInterceptStream(read_stream)
                await server.run(intercepted_read, write_stream, server.create_initialization_options())
            except Exception:
                await server.run(read_stream, write_stream, server.create_initialization_options())
            finally:
                keepalive_task.cancel()

    asyncio.run(main())
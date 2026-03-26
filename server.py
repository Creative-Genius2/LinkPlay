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

# Import setup_tools but call inside main() after stdio is captured
from setup_tools import setup_tools, get_tool_path

# Eonet ICR engine — auto-discovery, flipnote labeling, query resolution
from eonet_driver import _build_eonet, eonet_resolve

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

server = Server("linkplay")

# State
current_rom = None
current_flipnote = None
text_tables = {}  # Populated on open_rom: {file_index: [strings], 'species': [strings], ...}
text_narc = None   # Kept in memory for lazy lookups
text_mult = None   # Derived once from species file (Gen V only)
text_gen = None    # 4 or 5, set during bootstrap
narc_roles = {}    # Reverse map: narc_path -> role (e.g. 'a/0/9/2' -> 'trpoke')
tm_table = []      # Indexed by bit position: [(label, move_id), ...] — populated at ROM open
loaded_roms = {}   # game_code -> saved state for multi-ROM support
_narc_cache = {}   # (game_code, narc_path) -> parsed ndspy.narc.NARC
_rom_restore_done = False  # Flipped once after first tool call triggers restore
eonet_labels = {}  # game_code -> {narc_path: {'role': str, 'labels': {idx: 'Name (Role)'}}}
eonet_index = {}   # game_code -> [{name_lower: str, path: str, role: str, idx: int}, ...]


async def _do_pending_restore():
    """Load any ROMs from last_rom.json that aren't already in loaded_roms. Runs once."""
    global _rom_restore_done
    if _rom_restore_done:
        return
    _rom_restore_done = True
    try:
        reg_path = Path.home() / ".linkplay" / "last_rom.json"
        if not reg_path.exists():
            return
        registry = json.loads(reg_path.read_text(encoding='utf-8'))
        if 'game_code' in registry:
            registry = {registry['game_code']: registry['path']}
        for gc, rom_path in registry.items():
            if gc in loaded_roms:
                continue
            if not rom_path or not Path(rom_path).exists():
                print(f"[linkplay] Registry ROM not found, skipping: {gc} → {rom_path}", file=sys.stderr, flush=True)
                continue
            try:
                await spotlight(rom_path)
                print(f"[linkplay] Auto-restored ROM: {gc}", file=sys.stderr, flush=True)
            except Exception as e:
                print(f"[linkplay] Failed to restore {gc}: {e}", file=sys.stderr, flush=True)
    except Exception as e:
        print(f"[linkplay] Registry restore error: {e}", file=sys.stderr, flush=True)


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
    """Parse optional game-code prefix from path. 'IRE:a/0/1/6:1' -> ('IRE', 'a/0/1/6:1')."""
    if len(path) > 4 and path[3] == ':' and path[:3].isalpha() and path[:3].isupper():
        gc = path[:3]
        if gc in loaded_roms or (current_rom and current_rom['header']['game_code'] == gc):
            return gc, path[4:]
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

    game_code = full_code[:3] if len(full_code) >= 3 else full_code
    region_char = full_code[3] if len(full_code) >= 4 else 'E'
    region = REGION_MAP.get(region_char, 'US')

    return {
        'game_code': game_code,
        'full_code': full_code,
        'region_char': region_char,
        'game_title': title,
        'region': region
    }


def read_gb_header(path: str) -> dict:
    """Read GB/GBC ROM header."""
    with open(path, 'rb') as f:
        f.seek(0x134)
        title = f.read(16).decode('ascii', errors='ignore').strip('\x00')

    game_code = title[:3] if len(title) >= 3 else title
    
    return {
        'game_code': game_code,
        'full_code': title[:4] if len(title) >= 4 else title,
        'region_char': 'E',
        'game_title': title,
        'region': 'US'
    }


# Shared flipnotes — paired games share one flipnote
FLIPNOTE_PAIRS = {
    'Pokémon Diamond & Pearl': ['ADA', 'APA'],
    'Pokémon Platinum': ['CPU'],
    'Pokémon HeartGold & SoulSilver': ['IPK', 'IPG'],
    'Pokémon Black & White': ['IRB', 'IRA'],
    'Pokémon Black & White 2': ['IRE', 'IRD'],
}

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


# ============ Gen V Text Decoder ============

# Gen V special character substitutions (packed words for common game terms)
_GEN5_CHARMAP = {
    0x2467: 'Mr.', 0x2468: 'Ms.', 0x2469: 'Mrs.',
    0x246D: 'the', 0x246E: 'The',
    0x2486: 'Poké', 0x2487: 'mon',
}

def _derive_gen5_mult(species_data: bytes) -> int:
    """Derive XOR multiplier from species file entry 1 ('Bulbasaur').
    Seed for entry 1 = (1+3)*MULT = 4*MULT. XOR encrypted[0] with 'B' (0x0042) gives 4*MULT.
    """
    if len(species_data) < 16:
        return 0x2983
    entry_count = struct.unpack_from('<H', species_data, 2)[0]
    section_offset = struct.unpack_from('<I', species_data, 0x0C)[0]
    if entry_count < 2 or section_offset + 4 > len(species_data):
        return 0x2983
    # Read entry 1 from entry table
    entry_pos = section_offset + 4 + (1 * 8)
    if entry_pos + 8 > len(species_data):
        return 0x2983
    offset = struct.unpack_from('<I', species_data, entry_pos)[0]
    str_offset = section_offset + offset
    if str_offset + 2 > len(species_data):
        return 0x2983
    encrypted_0 = struct.unpack_from('<H', species_data, str_offset)[0]
    four_mult = encrypted_0 ^ 0x0042
    return (four_mult // 4) & 0xFFFF


def decode_gen5_text(data: bytes, mult: int = 0x2983) -> list:
    """Decode a Gen V encrypted text file. MULT derived once from NARC, passed in.
    Seed = (entry_index + 3) * mult, key advances via ROL3.
    Control codes (0xFFFE) consumed properly: type(u16), param_count(u16), params(u16*n).
    """
    if len(data) < 16:
        return []

    entry_count = struct.unpack_from('<H', data, 2)[0]
    section_offset = struct.unpack_from('<I', data, 0x0C)[0]

    if entry_count == 0 or entry_count > 10000:
        return []
    if section_offset + 4 > len(data):
        return []

    entry_table_start = section_offset + 4
    strings = []

    for i in range(entry_count):
        entry_pos = entry_table_start + (i * 8)
        if entry_pos + 8 > len(data):
            break
        offset = struct.unpack_from('<I', data, entry_pos)[0]
        char_count = struct.unpack_from('<H', data, entry_pos + 4)[0]

        str_offset = section_offset + offset
        key = ((i + 3) * mult) & 0xFFFF

        # Decrypt all u16 values for this entry
        vals = []
        for j in range(char_count):
            char_pos = str_offset + (j * 2)
            if char_pos + 2 > len(data):
                break
            enc = struct.unpack_from('<H', data, char_pos)[0]
            dec = enc ^ key
            key = ((key << 3) | (key >> 13)) & 0xFFFF
            vals.append(dec)

        # F100 = 9-bit compressed text (LSB-first, 0x1FF terminator)
        if vals and vals[0] == 0xF100:
            bits = 0
            nbits = 0
            for w in vals[1:]:
                if w == 0xFFFF:
                    break
                bits |= (w << nbits)
                nbits += 16
            chars = []
            while nbits >= 9:
                c = bits & 0x1FF
                bits >>= 9
                nbits -= 9
                if c == 0x1FF:
                    break
                try:
                    chars.append(chr(c) if c >= 0x20 else f'\\x{c:04X}')
                except (ValueError, OverflowError):
                    chars.append(f'\\x{c:04X}')
            strings.append(''.join(chars))
            continue

        # Normal text: parse control codes and characters
        chars = []
        j = 0
        while j < len(vals):
            dec = vals[j]
            j += 1

            if dec == 0xFFFF:
                break
            elif dec == 0xFFFE:
                ctrl_type = vals[j] if j < len(vals) else 0
                j += 1
                param_count = vals[j] if j < len(vals) else 0
                j += 1
                j += param_count  # skip params
                if ctrl_type == 0x0000 or ctrl_type & 0xFF00 == 0x0000:
                    chars.append('\n')
                elif ctrl_type & 0xFF00 == 0x0100:
                    chars.append('[var]')
                elif ctrl_type & 0xFF00 in (0xBE00, 0xFF00):
                    pass  # formatting, skip
                else:
                    chars.append(f'[ctrl:{ctrl_type:04X}]')
            elif dec in _GEN5_CHARMAP:
                chars.append(_GEN5_CHARMAP[dec])
            else:
                try:
                    chars.append(chr(dec))
                except (ValueError, OverflowError):
                    chars.append(f'\\x{dec:04X}')

        strings.append(''.join(chars))

    return strings


# Game info — gen + NARC role mappings. Roles auto-drive _auto_decode.
_GEN5_B2W2 = {
    'text': 'a/0/0/2',
    'trdata': 'a/0/9/1',
    'trpoke': 'a/0/9/2',
    'personal': 'a/0/1/6',
    'learnsets': 'a/0/1/8',
    'evolutions': 'a/0/1/9',
    'move_data': 'a/0/2/1',
    'items': 'a/0/2/4',
    'baby_species': 'a/0/2/0',  # Maps species→baby form (NOT egg moves)
}
_GEN5_BW1 = {
    'text': 'a/0/0/2',
    'trdata': 'a/0/9/2',  # Different from B2W2!
    'trpoke': 'a/0/9/3',  # Different from B2W2!
    'personal': 'a/0/1/6',
    'learnsets': 'a/0/1/8',
    'evolutions': 'a/0/1/9',
    'move_data': 'a/0/2/1',
    'items': 'a/0/2/4',
    'baby_species': 'a/0/2/0',  # Maps species→baby form (NOT egg moves)
    'egg_moves': 'a/0/2/0',
}
_BW1_ENCOUNTERS = {
    'encounters': 'a/1/2/6',  # 112 files, 232 bytes each
}
_B2W2_ENCOUNTERS = {
    'encounters': 'a/1/2/7',  # 135 files, 232 or 928 bytes (seasonal)
}
_B2W2_PWT = {
    'pwt_rental': 'a/2/5/0',           # 1000 pokemon pools (16 bytes each)
    'pwt_trainers': 'a/2/5/1',         # 120 tournament trainer configs (6 bytes each)
    'pwt_rosters': 'a/2/5/2',          # 120 tournament rosters -> pool indices
    'pwt_rental_b': 'a/2/5/3',         # 1000 pokemon pools B
    'pwt_trainers_b': 'a/2/5/4',       # 69 tournament trainer configs B
    'pwt_rosters_b': 'a/2/5/5',        # 69 tournament rosters B -> pool indices
    'pwt_champions': 'a/2/5/6',        # 1000 pokemon pools
    'pwt_champions_b': 'a/2/5/7',      # 1000 pokemon pools B
    'pwt_download': 'a/2/5/8',         # 1 file — download tournament metadata (multilingual)
    'pwt_ui': 'a/2/5/9',              # 9 files — UI graphics (RLCN/RGCN/RCSN)
    'pwt_trainers_2': 'a/2/4/8',      # 120 trainer configs (secondary tournament set)
    'pwt_rosters_2': 'a/2/4/9',        # 120 rosters (secondary tournament set)
    'pwt_mix': 'a/2/6/1',              # 1000 pokemon pool (Mix Tournament)
    'pwt_defs': 'a/2/6/9',             # 42 tournament definitions (1688B each)
    'pwt_trainer_map': 'a/2/4/0',      # trainer index → name/sprite mapping (20B stride, u16[8]=class_id)
}
_B2W2_SUBWAY = {
    'subway_pokemon': 'a/2/1/1',       # 1000 pokemon pool (16B, same format as PWT)
    'subway_trainers': 'a/2/1/2',      # 315 trainers (format + count + pool indices)
}
_BW1_SUBWAY = {
    'subway_pokemon': 'a/2/1/4',       # 1000 pokemon pool (same format)
    'subway_trainers': 'a/2/1/5',      # 315 trainers
}
# Gen IV — DP/Pt use named folders, HGSS uses a/X/Y/Z
_GEN4_COMMON = {
    'personal':  'poketool/personal/personal.narc',
    'learnsets': 'poketool/personal/wotbl.narc',
    'evolutions': 'poketool/personal/evo.narc',
    'baby_species': 'poketool/personal/pms.narc',  # Maps species→baby form (NOT egg moves)
    'move_data': 'poketool/waza/waza_tbl.narc',
    'trdata':    'poketool/trainer/trdata.narc',
    'trpoke':    'poketool/trainer/trpoke.narc',
    'items':     'itemtool/itemdata/item_data.narc',
    'contest':   'contest/data/contest_data.narc',
}
_GEN4_DP_COMMON = {
    **_GEN4_COMMON,
    'text':                  'msgdata/msg.narc',
    'battle_tower_pokemon':  'battle/b_tower/btdpm.narc',
    'battle_tower_trainers': 'battle/b_tower/btdtr.narc',
}
_GEN4_PLATINUM_OVERRIDES = {
    'text':                  'msgdata/pl_msg.narc',
    'personal':              'poketool/personal/pl_personal.narc',
    'move_data':             'poketool/waza/pl_waza_tbl.narc',
    'items':                 'itemtool/itemdata/pl_item_data.narc',
    'encounters':            'fielddata/encountdata/pl_enc_data.narc',
    'battle_tower_pokemon':  'battle/b_pl_tower/pl_btdpm.narc',
    'battle_tower_trainers': 'battle/b_pl_tower/pl_btdtr.narc',
}
_GEN4_HGSS = {
    'text':                  'a/0/2/7',
    'personal':              'a/0/0/2',
    'learnsets':             'a/0/3/3',
    'evolutions':            'a/0/3/4',
    'move_data':             'a/0/1/1',
    'trdata':                'a/0/5/5',
    'trpoke':                'a/0/5/6',
    'encounters':            'a/1/3/6',   # 142 files, 196 bytes each
    'battle_tower_pokemon':  'a/2/0/3',   # Real Pt-era data (a/1/2/9 is DP leftover)
    'battle_tower_trainers': 'a/2/0/2',   # Real Pt-era data (a/1/2/8 is DP leftover)
    'items':                 'a/0/1/7',   # 514 files, 34 bytes each
    'pokeathlon_performance': 'a/1/6/9',  # Pokéathlon performance stats (554 entries, 20B each)
}

GAME_INFO = {
    # Gen V
    'IRE': {'gen': 5, 'narcs': {**_GEN5_B2W2, **_B2W2_ENCOUNTERS, **_B2W2_PWT, **_B2W2_SUBWAY}},  # Black 2
    'IRD': {'gen': 5, 'narcs': {**_GEN5_B2W2, **_B2W2_ENCOUNTERS, **_B2W2_PWT, **_B2W2_SUBWAY}},  # White 2
    'IRB': {'gen': 5, 'narcs': {**_GEN5_BW1, **_BW1_ENCOUNTERS, **_BW1_SUBWAY}},                  # Black
    'IRA': {'gen': 5, 'narcs': {**_GEN5_BW1, **_BW1_ENCOUNTERS, **_BW1_SUBWAY}},                  # White
    # Gen IV — Diamond/Pearl
    'ADA': {'gen': 4, 'narcs': {**_GEN4_DP_COMMON, 'encounters': 'fielddata/encountdata/d_enc_data.narc'}},  # Diamond
    'APA': {'gen': 4, 'narcs': {**_GEN4_DP_COMMON, 'encounters': 'fielddata/encountdata/p_enc_data.narc'}},  # Pearl
    # Gen IV — Platinum
    'CPU': {'gen': 4, 'narcs': {**_GEN4_COMMON, **_GEN4_PLATINUM_OVERRIDES}},                      # Platinum
    # Gen IV — HGSS
    'IPK': {'gen': 4, 'narcs': {**_GEN4_HGSS}},                                                    # HeartGold
    'IPG': {'gen': 4, 'narcs': {**_GEN4_HGSS}},                                                    # SoulSilver
}

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
    found = {}

    # Pass 1: exact fingerprints (entry at specific index must match)
    for file_idx in sorted(k for k in text_tables if isinstance(k, int)):
        strings = text_tables[file_idx]
        if not isinstance(strings, list) or len(strings) < 2:
            continue
        for table_name, markers in TABLE_FINGERPRINTS.items():
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


# Gen IV complete character map
# Based on Bulbapedia: https://bulbapedia.bulbagarden.net/wiki/Character_encoding_(Generation_IV)

# Hiragana (0x0001-0x0051)
# Per Bulbapedia: https://bulbapedia.bulbagarden.net/wiki/Character_encoding_(Generation_IV)
_GEN4_HIRAGANA = {
    0x0001: 'ぁ', 0x0002: 'あ', 0x0003: 'ぃ', 0x0004: 'い', 0x0005: 'ぅ',
    0x0006: 'う', 0x0007: 'ぇ', 0x0008: 'え', 0x0009: 'ぉ', 0x000A: 'お',
    0x000B: 'か', 0x000C: 'が', 0x000D: 'き', 0x000E: 'ぎ', 0x000F: 'く',
    0x0010: 'ぐ', 0x0011: 'け', 0x0012: 'げ', 0x0013: 'こ', 0x0014: 'ご',
    0x0015: 'さ', 0x0016: 'ざ', 0x0017: 'し', 0x0018: 'じ', 0x0019: 'す',
    0x001A: 'ず', 0x001B: 'せ', 0x001C: 'ぜ', 0x001D: 'そ', 0x001E: 'ぞ',
    0x001F: 'た', 0x0020: 'だ', 0x0021: 'ち', 0x0022: 'ぢ', 0x0023: 'っ',
    0x0024: 'つ', 0x0025: 'づ', 0x0026: 'て', 0x0027: 'で', 0x0028: 'と',
    0x0029: 'ど', 0x002A: 'な', 0x002B: 'に', 0x002C: 'ぬ', 0x002D: 'ね',
    0x002E: 'の', 0x002F: 'は', 0x0030: 'ば', 0x0031: 'ぱ', 0x0032: 'ひ',
    0x0033: 'び', 0x0034: 'ぴ', 0x0035: 'ふ', 0x0036: 'ぶ', 0x0037: 'ぷ',
    0x0038: 'へ', 0x0039: 'べ', 0x003A: 'ぺ', 0x003B: 'ほ', 0x003C: 'ぼ',
    0x003D: 'ぽ', 0x003E: 'ま', 0x003F: 'み', 0x0040: 'む', 0x0041: 'め',
    0x0042: 'も', 0x0043: 'ゃ', 0x0044: 'や', 0x0045: 'ゅ', 0x0046: 'ゆ',
    0x0047: 'ょ', 0x0048: 'よ', 0x0049: 'ら', 0x004A: 'り', 0x004B: 'る',
    0x004C: 'れ', 0x004D: 'ろ', 0x004E: 'わ', 0x004F: 'を', 0x0050: 'ん',
    0x0051: 'ゔ',
}

# Katakana (0x0052-0x00A1)
_GEN4_KATAKANA = {
    0x0052: 'ァ', 0x0053: 'ア', 0x0054: 'ィ', 0x0055: 'イ', 0x0056: 'ゥ',
    0x0057: 'ウ', 0x0058: 'ェ', 0x0059: 'エ', 0x005A: 'ォ', 0x005B: 'オ',
    0x005C: 'カ', 0x005D: 'ガ', 0x005E: 'キ', 0x005F: 'ギ', 0x0060: 'ク',
    0x0061: 'グ', 0x0062: 'ケ', 0x0063: 'ゲ', 0x0064: 'コ', 0x0065: 'ゴ',
    0x0066: 'サ', 0x0067: 'ザ', 0x0068: 'シ', 0x0069: 'ジ', 0x006A: 'ス',
    0x006B: 'ズ', 0x006C: 'セ', 0x006D: 'ゼ', 0x006E: 'ソ', 0x006F: 'ゾ',
    0x0070: 'タ', 0x0071: 'ダ', 0x0072: 'チ', 0x0073: 'ヂ', 0x0074: 'ッ',
    0x0075: 'ツ', 0x0076: 'ヅ', 0x0077: 'テ', 0x0078: 'デ', 0x0079: 'ト',
    0x007A: 'ド', 0x007B: 'ナ', 0x007C: 'ニ', 0x007D: 'ヌ', 0x007E: 'ネ',
    0x007F: 'ノ', 0x0080: 'ハ', 0x0081: 'バ', 0x0082: 'パ', 0x0083: 'ヒ',
    0x0084: 'ビ', 0x0085: 'ピ', 0x0086: 'フ', 0x0087: 'ブ', 0x0088: 'プ',
    0x0089: 'ヘ', 0x008A: 'ベ', 0x008B: 'ペ', 0x008C: 'ホ', 0x008D: 'ボ',
    0x008E: 'ポ', 0x008F: 'マ', 0x0090: 'ミ', 0x0091: 'ム', 0x0092: 'メ',
    0x0093: 'モ', 0x0094: 'ャ', 0x0095: 'ヤ', 0x0096: 'ュ', 0x0097: 'ユ',
    0x0098: 'ョ', 0x0099: 'ヨ', 0x009A: 'ラ', 0x009B: 'リ', 0x009C: 'ル',
    0x009D: 'レ', 0x009E: 'ロ', 0x009F: 'ワ', 0x00A0: 'ヲ', 0x00A1: 'ン',
}

# Fullwidth numbers and letters (0x00A2-0x00DF)
_GEN4_FULLWIDTH = {
    0x00A2: '0', 0x00A3: '1', 0x00A4: '2', 0x00A5: '3', 0x00A6: '4',
    0x00A7: '5', 0x00A8: '6', 0x00A9: '7', 0x00AA: '8', 0x00AB: '9',
    0x00AC: 'A', 0x00AD: 'B', 0x00AE: 'C', 0x00AF: 'D', 0x00B0: 'E',
    0x00B1: 'F', 0x00B2: 'G', 0x00B3: 'H', 0x00B4: 'I', 0x00B5: 'J',
    0x00B6: 'K', 0x00B7: 'L', 0x00B8: 'M', 0x00B9: 'N', 0x00BA: 'O',
    0x00BB: 'P', 0x00BC: 'Q', 0x00BD: 'R', 0x00BE: 'S', 0x00BF: 'T',
    0x00C0: 'U', 0x00C1: 'V', 0x00C2: 'W', 0x00C3: 'X', 0x00C4: 'Y',
    0x00C5: 'Z', 0x00C6: 'a', 0x00C7: 'b', 0x00C8: 'c', 0x00C9: 'd',
    0x00CA: 'e', 0x00CB: 'f', 0x00CC: 'g', 0x00CD: 'h', 0x00CE: 'i',
    0x00CF: 'j', 0x00D0: 'k', 0x00D1: 'l', 0x00D2: 'm', 0x00D3: 'n',
    0x00D4: 'o', 0x00D5: 'p', 0x00D6: 'q', 0x00D7: 'r', 0x00D8: 's',
    0x00D9: 't', 0x00DA: 'u', 0x00DB: 'v', 0x00DC: 'w', 0x00DD: 'x',
    0x00DE: 'y', 0x00DF: 'z',
}

# Fullwidth symbols (0x00E0-0x011F)
_GEN4_FULLWIDTH_SYMBOLS = {
    0x00E1: '！', 0x00E2: '？', 0x00E3: '、', 0x00E4: '。', 0x00E5: '…',
    0x00E6: '・', 0x00E7: '／', 0x00E8: '「', 0x00E9: '」', 0x00EA: '『',
    0x00EB: '』', 0x00EC: '（', 0x00ED: '）', 0x00EE: '♂', 0x00EF: '♀',
    0x00F0: '＋', 0x00F1: 'ー', 0x00F2: '×', 0x00F3: '÷', 0x00F4: '＝',
    0x00F5: '～', 0x00F6: '：', 0x00F7: '；', 0x00F8: '．', 0x00F9: '，',
    0x00FA: '♠', 0x00FB: '♣', 0x00FC: '♥', 0x00FD: '♦', 0x00FE: '★',
    0x00FF: '◎', 0x0100: '○', 0x0101: '□', 0x0102: '△', 0x0103: '◇',
    0x0104: '＠', 0x0105: '♪', 0x0106: '％', 0x0107: '☀', 0x0108: '☁',
    0x0109: '☂', 0x010A: '☃', 0x0111: '円', 0x0118: '←', 0x0119: '↑',
    0x011A: '↓', 0x011B: '→', 0x011C: '►',
}

# Halfwidth special characters
# Positions confirmed from game data (space at 0x01DE, etc.)
_GEN4_SPECIAL = {
    # Inverted punctuation
    0x01A9: '\u00a1', 0x01AA: '\u00bf',
    # Punctuation and symbols
    0x01AC: '!', 0x01AD: '?', 0x01AE: ',', 0x01AF: '.',
    0x01B0: '\u2026', 0x01B1: '\uff65', 0x01B2: '/', 0x01B3: '\u2018',
    0x01B4: '\u2019', 0x01B5: '\u201C', 0x01B6: '\u201D', 0x01B7: '\u201e',
    0x01B8: '\u00ab', 0x01B9: '\u00bb', 0x01BA: '(', 0x01BB: ')',
    0x01BC: '\u2642', 0x01BD: '\u2640', 0x01BE: '+', 0x01BF: '-',
    # More symbols
    0x01C0: '*', 0x01C1: '#', 0x01C2: '=', 0x01C3: '&',
    0x01C4: '~', 0x01C5: ':', 0x01C6: ';', 0x01C7: '\u2660',
    0x01C8: '\u2663', 0x01C9: '\u2665', 0x01CA: '\u2666', 0x01CB: '\u2605',
    0x01CC: '\u25ce', 0x01CD: '\u25cb', 0x01CE: '\u25a1', 0x01CF: '\u25b3',
    0x01D0: '\u25c7', 0x01D1: '@', 0x01D2: '\u266a', 0x01D3: '%',
    0x01D4: '\u2600', 0x01D5: '\u2601', 0x01D6: '\u2602', 0x01D7: '\u2603',
    0x01DE: ' ', 0x01DF: 'e',  # Space and lowercase e (confirmed from game data)
    # Extended characters
    0x01E0: 'PK', 0x01E1: 'MN', 0x01E4: '\u00b0', 0x01E5: '_',
    0x01E6: '\uff3f', 0x01E7: '\u2024', 0x01E8: '\u2025',
}

def _get_gen4_char(c: int) -> str:
    """Get Gen IV character by code point.
    Halfwidth Latin block (used by English/EU ROMs):
      0x0121-0x012A = 0-9
      0x012B-0x0144 = A-Z
      0x0145-0x015E = a-z
    Kana blocks cover 0x0001-0x00A1.
    """
    if c == 0x0000:
        return ' '
    elif c in _GEN4_HIRAGANA:
        return _GEN4_HIRAGANA[c]
    elif c in _GEN4_KATAKANA:
        return _GEN4_KATAKANA[c]
    elif c in _GEN4_FULLWIDTH:
        return _GEN4_FULLWIDTH[c]
    elif c in _GEN4_FULLWIDTH_SYMBOLS:
        return _GEN4_FULLWIDTH_SYMBOLS[c]
    elif 0x0121 <= c <= 0x012A:
        return chr(ord('0') + c - 0x0121)
    elif 0x012B <= c <= 0x0144:
        return chr(ord('A') + c - 0x012B)
    elif 0x0145 <= c <= 0x015E:
        return chr(ord('a') + c - 0x0145)
    elif 0x015F <= c <= 0x019E:
        ACCENTED = "ÀÁÂÃÄÅÆÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖרÙÚÛÜÝÞßàáâãäåæçèéêëìíîïðñòóôõö÷øùúûüýþÿ"
        idx = c - 0x015F
        return ACCENTED[idx] if idx < len(ACCENTED) else '?'
    elif 0x019F <= c <= 0x01AB:
        # Extended Latin: Œ œ Ş ş ª º er re r ¡ ¿
        extended = ['Œ', 'œ', 'Ş', 'ş', 'ª', 'º', 'er', 're', 'r', '', '¡', '¿', '!']
        idx = c - 0x019F
        return extended[idx] if idx < len(extended) else '?'
    elif c in _GEN4_SPECIAL:
        return _GEN4_SPECIAL[c]
    elif c == 0xFFFE or c == 0xE000:
        return '\n'
    elif c == 0xFFFF or c == 0x01FF:
        return ''
    else:
        return '?'


def decode_gen4_text(data: bytes) -> list:
    """Decode Gen IV (DPPt/HGSS) text file.
    Format: u16 num_entries, u16 seed, encrypted entry table, encrypted strings.
    Entry table XOR: rolling key from seed * 0x2FD, advancing +0x493D per u16.
    String XOR: key = 0x91BD3 * (entry + 1) & 0xFFFF, advancing +0x493D per u16.
    Supports 0xF100 compressed text (9-bit encoding, same as Gen V).
    """
    if len(data) < 4:
        return []

    num_entries = struct.unpack_from('<H', data, 0)[0]
    seed = struct.unpack_from('<H', data, 2)[0]

    if num_entries == 0 or num_entries > 10000:
        return []

    table_end = 4 + num_entries * 8
    if table_end > len(data):
        return []

    # Decrypt entry table: offset(u32) + length(u32) per entry
    # seed32 = (key * 765 * (i+1)) & 0xFFFF, replicated: seed32 |= seed32 << 16
    base_key = (seed * 0x2FD) & 0xFFFF
    entry_data = bytearray(data[4:table_end])
    entries = []
    for i in range(num_entries):
        key16 = (base_key * (i + 1)) & 0xFFFF
        seed32 = key16 | (key16 << 16)
        off = i * 8
        offset = struct.unpack_from('<I', entry_data, off)[0] ^ seed32
        charcount = struct.unpack_from('<I', entry_data, off + 4)[0] ^ seed32
        entries.append((offset, charcount))

    strings = []
    for i, (offset, length) in enumerate(entries):
        if length == 0 or offset + length * 2 > len(data):
            strings.append("")
            continue

        # Per-string decryption key
        key = ((i + 1) * 0x91BD3) & 0xFFFF
        vals = []
        for j in range(length):
            pos = offset + j * 2
            if pos + 2 > len(data):
                break
            enc = struct.unpack_from('<H', data, pos)[0]
            dec = (enc ^ key) & 0xFFFF
            key = (key + 0x493D) & 0xFFFF
            vals.append(dec)

        # Check for 0xF100 compressed text (trainer names)
        # Algorithm from pret decomp (String_ConcatTrainerName):
        # Each u16 word contributes only 15 bits. 9-bit chars are extracted
        # with bit 15 of each word skipped (shift threshold is 15, not 16).
        if vals and vals[0] == 0xF100:
            src = vals[1:]  # skip the 0xF100 marker
            chars = []
            si = 0   # source word index
            shift = 0
            while si < len(src):
                # Extract 9-bit character spanning current word (and possibly next)
                cur_char = (src[si] >> shift) & 0x1FF
                shift += 9
                if shift >= 15:
                    si += 1
                    shift -= 15
                    if shift and si < len(src):
                        cur_char |= (src[si] << (9 - shift)) & 0x1FF
                if cur_char == 0x1FF:  # compressed EOS
                    break
                ch = _get_gen4_char(cur_char)
                if ch == '?':
                    chars.append(f'\\x{cur_char:04X}')
                else:
                    chars.append(ch)
            strings.append(''.join(chars))
            continue

        # Normal text: process decrypted values through shared character table
        chars = []
        for dec in vals:
            if dec == 0xFFFF:
                break
            ch = _get_gen4_char(dec)
            if ch == '?':
                chars.append(f'\\x{dec:04X}')
            else:
                chars.append(ch)

        strings.append(''.join(chars))

    return strings

# AI Flags for Gen IV/V trainers
AI_FLAGS_GEN5 = {
    0x001: "Basic AI",
    0x002: "Check bad moves",
    0x004: "Try to faint",
    0x008: "Check viability",
    0x010: "Setup first turn",
    0x020: "Risky",
    0x040: "Prefer strongest",
    0x080: "Prefer status",
    0x100: "Risky (advanced)",
    0x200: "Weather",
    0x400: "Trapping",
    0x800: "Expert",
    0x1000: "Double battle",
    0x2000: "HP aware",
    0x4000: "Unknown (0x4000)",
    0x8000: "Roaming",
}

AI_FLAGS_GEN4 = {
    0x001: "Basic AI",
    0x002: "Check bad moves",
    0x004: "Try to faint",
    0x008: "Check viability",
    0x010: "Setup first turn",
    0x020: "Risky",
    0x040: "Prefer strongest",
    0x080: "Prefer status",
    0x100: "Weather",
    0x200: "Trapping",
    0x400: "Unknown (0x400)",
    0x800: "Unknown (0x800)",
    0x1000: "Unknown (0x1000)",
    0x2000: "Unknown (0x2000)",
    0x4000: "Unknown (0x4000)",
    0x8000: "Unknown (0x8000)",
}

def decode_ai_flags(flags: int, gen: int = 5) -> list:
    """Decode AI flags into human-readable list."""
    flag_map = AI_FLAGS_GEN5 if gen >= 5 else AI_FLAGS_GEN4
    active_flags = []
    for bit, name in sorted(flag_map.items()):
        if flags & bit:
            active_flags.append(name)
    return active_flags if active_flags else ["None"]


# BW2 Challenge Mode runtime level delta table.
# Verified by measuring stored trpoke levels vs actual in-game levels.
# The game applies a flat per-trainer-file delta at runtime on top of stored levels.
# Pattern: +1 per pair of gyms, capped at +4 from gym 7 onward (E4, Champion included).
# Keyed by trdata/trpoke file index -> challenge delta.
# Normal mode files and unkeyed files get delta 0.
_BW2_CHALLENGE_FILE_DELTA = {
    # Gym 1 - Cheren (Aspertia)
    764: 1,
    # Gym 2 - Roxie (Virbank)
    765: 1,
    # Gym 3 - Burgh (Castelia)
    766: 2,
    # Gym 4 - Elesa (Nimbasa)
    767: 2,
    # Gym 5 - Clay (Driftveil)
    768: 3,
    # Gym 6 - Skyla (Mistralton)
    769: 3,
    # Gym 7 - Drayden (Opelucid)
    770: 4,
    # Gym 8 - Marlon (Humilau)
    771: 4,
    # Elite Four - Shauntal, Caitlin, Grimsley, Marshal (pre-champion)
    772: 4, 773: 4, 774: 4, 775: 4,
    # Champion Iris (pre-champion)
    776: 4,
    # Elite Four rematches (post-game)
    777: 4, 778: 4, 779: 4, 780: 4,
    # Champion Iris rematch
    781: 4,
}

def get_bw2_challenge_delta(file_idx: int, game_code: str = '') -> int:
    """Get runtime challenge level delta for a BW2 trainer file.
    Returns 0 for Normal mode files, non-BW2 games, or unkeyed files.
    """
    if game_code not in ('IRE', 'IRD'):
        return 0
    return _BW2_CHALLENGE_FILE_DELTA.get(file_idx, 0)


# TRPoke template sizes (keyed by template bits from TRData byte 0)
# bit 0 = has custom moves, bit 1 = has held item
# Gen V: iv(u8) ability(u8) level(u8) pad(u8) species(u16) form(u16) = 8B base
TRPOKE_FORMATS_G5 = {
    0: 8,   # base
    1: 16,  # + moves(8)
    2: 10,  # + item(2)
    3: 18,  # + item(2) + moves(8)
}
# Gen IV: iv(u16) level(u16) species(u16) = 6B base
TRPOKE_FORMATS_G4 = {
    0: 6,   # base
    1: 14,  # + moves(8)
    2: 8,   # + item(2)
    3: 18,  # + item(2) + moves(8) + pad(2)
}

# =============================================================================
# TRAINER LOCATION MAPPING
# Maps special trainers (Gym Leaders, E4, Champions) to their battle locations.
# =============================================================================

TRAINER_LOCATIONS = {
    # Gen IV - Diamond/Pearl
    "ADA": {
        ("Leader", "Roark"): "Oreburgh Gym",
        ("Leader", "Gardenia"): "Eterna Gym",
        ("Leader", "Maylene"): "Veilstone Gym",
        ("Leader", "Crasher Wake"): "Pastoria Gym",
        ("Leader", "Wake"): "Pastoria Gym",
        ("Leader", "Fantina"): "Hearthome Gym",
        ("Leader", "Byron"): "Canalave Gym",
        ("Leader", "Candice"): "Snowpoint Gym",
        ("Leader", "Volkner"): "Sunyshore Gym",
        ("Elite Four", "Aaron"): "Pokémon League",
        ("Elite Four", "Bertha"): "Pokémon League",
        ("Elite Four", "Flint"): "Pokémon League",
        ("Elite Four", "Lucian"): "Pokémon League",
        ("Champion", "Cynthia"): "Pokémon League",
    },
    "APA": "ADA",  # Pearl alias
    
    # Gen IV - Platinum
    "CPU": {
        ("Leader", "Roark"): "Oreburgh Gym",
        ("Leader", "Gardenia"): "Eterna Gym",
        ("Leader", "Fantina"): "Hearthome Gym",
        ("Leader", "Maylene"): "Veilstone Gym",
        ("Leader", "Crasher Wake"): "Pastoria Gym",
        ("Leader", "Wake"): "Pastoria Gym",
        ("Leader", "Byron"): "Canalave Gym",
        ("Leader", "Candice"): "Snowpoint Gym",
        ("Leader", "Volkner"): "Sunyshore Gym",
        ("Elite Four", "Aaron"): "Pokémon League",
        ("Elite Four", "Bertha"): "Pokémon League",
        ("Elite Four", "Flint"): "Pokémon League",
        ("Elite Four", "Lucian"): "Pokémon League",
        ("Champion", "Cynthia"): "Pokémon League",
        ("Tower Tycoon", "Palmer"): "Battle Tower",
    },
    
    # Gen IV - HeartGold/SoulSilver
    "IPK": {
        # Johto Gym Leaders
        ("Leader", "Falkner"): "Violet Gym",
        ("Leader", "Bugsy"): "Azalea Gym",
        ("Leader", "Whitney"): "Goldenrod Gym",
        ("Leader", "Morty"): "Ecruteak Gym",
        ("Leader", "Chuck"): "Cianwood Gym",
        ("Leader", "Jasmine"): "Olivine Gym",
        ("Leader", "Pryce"): "Mahogany Gym",
        ("Leader", "Clair"): "Blackthorn Gym",
        # Kanto Gym Leaders (class = name in HGSS)
        ("Leader", "Brock"): "Pewter Gym",
        ("Leader", "Misty"): "Cerulean Gym",
        ("Leader", "Lt. Surge"): "Vermilion Gym",
        ("Leader", "Erika"): "Celadon Gym",
        ("Leader", "Janine"): "Fuchsia Gym",
        ("Leader", "Sabrina"): "Saffron Gym",
        ("Leader", "Blaine"): "Seafoam Gym",
        ("Leader", "Blue"): "Viridian Gym",
        # Elite Four & Champion
        ("Elite Four", "Will"): "Indigo Plateau",
        ("Elite Four", "Koga"): "Indigo Plateau",
        ("Elite Four", "Bruno"): "Indigo Plateau",
        ("Elite Four", "Karen"): "Indigo Plateau",
        ("Champion", "Lance"): "Indigo Plateau",
        # Special
        ("PKMN Trainer", "Red"): "Mt. Silver (Summit)",
    },
    "IPG": "IPK",  # SoulSilver alias
    
    # Gen V - Black/White
    "IRB": {
        ("Leader", "Cilan"): "Striaton Gym",
        ("Leader", "Chili"): "Striaton Gym",
        ("Leader", "Cress"): "Striaton Gym",
        ("Leader", "Lenora"): "Nacrene Gym",
        ("Leader", "Burgh"): "Castelia Gym",
        ("Leader", "Elesa"): "Nimbasa Gym",
        ("Leader", "Clay"): "Driftveil Gym",
        ("Leader", "Skyla"): "Mistralton Gym",
        ("Leader", "Brycen"): "Icirrus Gym",
        ("Leader", "Drayden"): "Opelucid Gym",
        ("Leader", "Iris"): "Opelucid Gym",
        ("Elite Four", "Shauntal"): "Pokémon League",
        ("Elite Four", "Grimsley"): "Pokémon League",
        ("Elite Four", "Caitlin"): "Pokémon League",
        ("Elite Four", "Marshal"): "Pokémon League",
        ("Champion", "Alder"): "Pokémon League",
        ("PKMN Trainer", "N"): "N's Castle",
        ("Subway Boss", "Ingo"): "Battle Subway",
        ("Subway Boss", "Emmet"): "Battle Subway",
    },
    "IRA": "IRB",  # White alias
    
    # Gen V - Black 2/White 2
    "IRE": {
        ("Leader", "Cheren"): "Aspertia Gym",
        ("Leader", "Roxie"): "Virbank Gym",
        ("Leader", "Burgh"): "Castelia Gym",
        ("Leader", "Elesa"): "Nimbasa Gym",
        ("Leader", "Clay"): "Driftveil Gym",
        ("Leader", "Skyla"): "Mistralton Gym",
        ("Leader", "Drayden"): "Opelucid Gym",
        ("Leader", "Marlon"): "Humilau Gym",
        ("Elite Four", "Shauntal"): "Pokémon League",
        ("Elite Four", "Grimsley"): "Pokémon League",
        ("Elite Four", "Caitlin"): "Pokémon League",
        ("Elite Four", "Marshal"): "Pokémon League",
        ("Champion", "Iris"): "Pokémon League",
        ("Subway Boss", "Ingo"): "Battle Subway",
        ("Subway Boss", "Emmet"): "Battle Subway",
    },
    "IRD": "IRE",  # White 2 alias
}

# Class-only location mappings (fallback when name not found)
CLASS_LOCATIONS = {
    "ADA": {"Elite Four": "Pokémon League", "Champion": "Pokémon League"},
    "APA": "ADA",
    "CPU": {"Elite Four": "Pokémon League", "Champion": "Pokémon League", "Tower Tycoon": "Battle Tower"},
    "IPK": {"Elite Four": "Indigo Plateau", "Champion": "Indigo Plateau",
            "Brock": "Pewter Gym", "Misty": "Cerulean Gym", "Lt. Surge": "Vermilion Gym",
            "Erika": "Celadon Gym", "Janine": "Fuchsia Gym", "Sabrina": "Saffron Gym",
            "Blaine": "Seafoam Gym", "Blue": "Viridian Gym"},
    "IPG": "IPK",
    "IRB": {"Elite Four": "Pokémon League", "Champion": "Pokémon League", "Subway Boss": "Battle Subway"},
    "IRA": "IRB",
    "IRE": {
        "Elite Four": "Pokémon League", "Champion": "Pokémon League", "Subway Boss": "Battle Subway",
        # PWT participants (class = name)
        "Brock": "Pokémon World Tournament", "Misty": "Pokémon World Tournament",
        "Lt. Surge": "Pokémon World Tournament", "Erika": "Pokémon World Tournament",
        "Sabrina": "Pokémon World Tournament", "Blaine": "Pokémon World Tournament",
        "Giovanni": "Pokémon World Tournament", "Falkner": "Pokémon World Tournament",
        "Bugsy": "Pokémon World Tournament", "Whitney": "Pokémon World Tournament",
        "Morty": "Pokémon World Tournament", "Chuck": "Pokémon World Tournament",
        "Jasmine": "Pokémon World Tournament", "Pryce": "Pokémon World Tournament",
        "Clair": "Pokémon World Tournament", "Janine": "Pokémon World Tournament",
        "Roxanne": "Pokémon World Tournament", "Brawly": "Pokémon World Tournament",
        "Wattson": "Pokémon World Tournament", "Flannery": "Pokémon World Tournament",
        "Norman": "Pokémon World Tournament", "Winona": "Pokémon World Tournament",
        "Tate": "Pokémon World Tournament", "Liza": "Pokémon World Tournament",
        "Juan": "Pokémon World Tournament", "Roark": "Pokémon World Tournament",
        "Gardenia": "Pokémon World Tournament", "Fantina": "Pokémon World Tournament",
        "Maylene": "Pokémon World Tournament", "Wake": "Pokémon World Tournament",
        "Byron": "Pokémon World Tournament", "Candice": "Pokémon World Tournament",
        "Volkner": "Pokémon World Tournament", "Blue": "Pokémon World Tournament",
        "Lance": "Pokémon World Tournament", "Steven": "Pokémon World Tournament",
        "Wallace": "Pokémon World Tournament", "Red": "Pokémon World Tournament",
        "Cynthia": "Pokémon World Tournament", "Alder": "Pokémon World Tournament",
    },
    "IRD": "IRE",
}


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


def bootstrap_text_tables(rom, game_code: str) -> dict:
    """Load text NARC, decode all files, auto-detect named tables. Returns summary."""
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
    global narc_roles
    narc_roles = {path: role for role, path in game_info['narcs'].items() if role != 'text'}

    try:
        narc_data = rom.getFileByName(text_narc_path)
        text_narc = ndspy.narc.NARC(narc_data)
    except Exception as e:
        return {"error": f"Failed to load text NARC {text_narc_path}: {e}"}

    file_count = len(text_narc.files)

    if gen == 5:
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
_TM_SEARCH = {
    5: (bytes([0xD4, 0x01, 0x51, 0x01, 0xD9, 0x01, 0x5B, 0x01]), 101),  # 95 TMs + 6 HMs
    4: (bytes([0x08, 0x01, 0x51, 0x01, 0x60, 0x01, 0x5B, 0x01]), 100),  # 92 TMs + 8 HMs
}


def _discover_tm_table():
    """Search ARM9 for TM→move table, build bit-ordered tm_table. Returns count or None."""
    global tm_table
    tm_table = []
    if not current_rom or current_rom['type'] != 'nds':
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
            # Gen V: abilities at 0x18, 0x1A, 0x1C (u16, slot 0/1/2 = normal/normal/hidden)
            if len(personal_data) < 0x1E:
                return f"ability_slot_{ability_slot}"
            abilities = []
            for i in range(3):
                off = 0x18 + i * 2
                if off + 2 <= len(personal_data):
                    aid = struct.unpack_from('<H', personal_data, off)[0]
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
    """Decode a TRData entry into human-readable format."""
    if len(data) < 20:
        return None
    
    trainer_names = text_tables.get('trainer_names', [])
    trainer_classes = text_tables.get('trainer_classes', [])
    items_list = text_tables.get('items', [])
    gen = text_gen or 5

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
    ai_flags = decode_ai_flags(ai_flags_raw, gen)
    prize_money_base = data[17]
    area_id = data[18]
    class_name = trainer_classes[trainer_class] if trainer_class < len(trainer_classes) else f"class#{trainer_class}"

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

    # Class-based canonical name injection for player-named rivals only.
    # These trainers have no real entry in trainer_names — the game replaces
    # their name at runtime with whatever the player chose.
    # Gym leaders, Red, etc. now resolve correctly via trainer_names (file 729
    # in HGSS) after the dual-file fingerprinting fix (Pass 3b).
    RIVAL_CLASS_NAMES_G4 = {
        # Player-named rivals — no canonical name in trainer_names
        23:  "Silver",  # HGSS rival
        95:  "Barry",   # DP/Pt vs male player
        96:  "Barry",   # DP/Pt vs female player
    }
    if gen == 4 and trainer_class in RIVAL_CLASS_NAMES_G4:
        result["name"] = RIVAL_CLASS_NAMES_G4[trainer_class]
    elif index is not None and index < len(trainer_names):
        name = trainer_names[index].strip()
        if name:
            result["name"] = name

    # Gen V BW2 -- Hugh (class 145 = blank " Trainer"):
    # Stored as "Rival" placeholder in trainer_names (runtime-replaced by player's chosen name).
    # 3 trdata files per story encounter = one per starter counter (Snivy/Tepig/Oshawott).
    # 6 files = 3 starters x 2 player genders (Nate vs Hugh / Rosa vs Hugh).
    if gen == 5 and result.get("name") == "Rival" and trainer_class == 145:
        result["name"] = "Hugh"
        result["name_note"] = (
            "Default English name (player can rename at game start). "
            "Stored as 'Rival' placeholder in trainer_names. "
            "3 trdata files per story encounter = one per starter counter; "
            "6 files = 3 starters x 2 player genders (Nate/Rosa)."
        )

    return result




def _role_path(role_name):
    """Look up a NARC path by its role name, using the narc_roles reverse map."""
    for path, role in narc_roles.items():
        if role == role_name:
            return path
    return None


def _resolve_pwt_trainer_name(trainer_idx, trainer_role="pwt_trainers"):
    """Resolve a PWT trainer index to a name via the trainer mapping table (a/2/4/0).
    Entry stride: 20 bytes (10 u16s). Class IDs live at different positions per group:
      - Group 1 (Kanto/Johto): u16[8] has the class ID
      - Groups 2-5 (Hoenn/Sinnoh/Unova/Champions): u16[5], u16[6], u16[7] have class IDs
    Check all candidate positions, return the first that resolves to a real leader name."""
    _JUNK = {'Pokmon Trainer', 'Boss Trainer', 'no data', 'Pokmon Trainer',
             'Team Plasma', 'GAME FREAK', 'Leader', ''}
    classes = text_tables.get('trainer_classes', [])
    try:
        map_path = _role_path('pwt_trainer_map')
        if not map_path:
            return None
        map_narc = _get_narc(map_path)
        if not map_narc.files:
            return None
        data = bytes(map_narc.files[0])
        stride = 20
        entry_off = trainer_idx * stride
        if entry_off + stride > len(data):
            return None
        # Check u16[8] first (works for group 1), then u16[5], u16[6], u16[7]
        for pos in (8, 5, 6, 7):
            cid = struct.unpack_from('<H', data, entry_off + pos * 2)[0]
            if cid == 0 or cid >= len(classes):
                continue
            raw = classes[cid]
            if isinstance(raw, str):
                clean = re.sub(r'[^\x20-\x7E]', '', raw).strip()
                if clean and clean not in _JUNK:
                    return clean
    except:
        pass
    return None


# PWT pool roles — these decode as individual pokemon entries (16B each)
_PWT_POOL_ROLES = {
    'pwt_rental', 'pwt_rental_b', 'pwt_champions', 'pwt_champions_b', 'pwt_mix',
}

# PWT role relationships: trainer role → (roster role, pool role)
_PWT_ROLE_CHAINS = {
    'pwt_trainers':   ('pwt_rosters',   'pwt_rental'),
    'pwt_trainers_b': ('pwt_rosters_b', 'pwt_champions'),
    'pwt_trainers_2': ('pwt_rosters_2', 'pwt_rental'),
}

# Roster role → pool role (includes non-PWT facilities that share the format)
_PWT_ROSTER_POOLS = {
    'pwt_rosters':           'pwt_rental',
    'pwt_rosters_b':         'pwt_champions',
    'pwt_rosters_2':         'pwt_rental',
    'subway_trainers':       'subway_pokemon',
    'battle_tower_trainers': 'battle_tower_pokemon',
}


def decode_pwt(data: bytes, is_champions: bool = False, pool_name: str = "", pool_index: int = 0):
    """Decode PWT/facility pokemon pool entry (16B). Returns positional text."""
    if len(data) < 16 or data == b'\x00' * 16:
        return None

    species_list = text_tables.get('species', [])
    moves_list = text_tables.get('moves', [])
    natures_list = text_tables.get('natures', [])
    items_list = text_tables.get('items', [])

    species_id = struct.unpack_from('<H', data, 0)[0]
    moves = [struct.unpack_from('<H', data, 2 + i * 2)[0] for i in range(4)]
    ev_spread = data[10]
    nature = data[11]
    field12 = struct.unpack_from('<H', data, 12)[0]

    species_name = species_list[species_id] if species_id < len(species_list) else f"#{species_id}"
    nature_raw = natures_list[nature] if nature < len(natures_list) else ""
    nature_name = re.sub(r'[^\x20-\x7E]', '', nature_raw).replace(' nature.', '').strip() if nature_raw else f"nature#{nature}"

    move_names = [moves_list[m] if m < len(moves_list) else f"move#{m}" for m in moves if m != 0]
    ev_names = decode_ev_spread(ev_spread)

    item_tag = ""
    if field12 > 0:
        item_name = items_list[field12] if field12 < len(items_list) else f"item#{field12}"
        item_tag = f"  [{item_name}]"

    poke_line = f"{species_name} ({nature_name}){item_tag}"
    out = [f"[{pool_name} #{pool_index}] {poke_line}" if pool_name else poke_line]
    if move_names:
        out.append(" / ".join(move_names))
    if ev_names and ev_names != ['None']:
        out.append(f"EVs: {', '.join(ev_names)}")

    return "\n".join(out)


def _resolve_pwt_pool_entry(pool_idx, pool_narc_path=None, pool_role='pwt_champions'):
    """Resolve a PWT pool index to a single formatted pokemon line."""
    try:
        if not pool_narc_path:
            pool_narc_path = _role_path(pool_role)
        if not pool_narc_path:
            return None
        pool_narc = _get_narc(pool_narc_path)
        if pool_idx >= len(pool_narc.files):
            return None
        pdata = bytes(pool_narc.files[pool_idx])
        result = decode_pwt(pdata)
        if not result:
            return None
        return result.replace("\n", "  |  ")
    except:
        return None


def decode_pwt_roster(data: bytes, slot_index: int = 0, roster_role: str = "pwt_rosters"):
    """Decode PWT/facility roster with resolved pokemon. Returns positional text."""
    if len(data) < 4:
        return None
    fmt = struct.unpack_from('<H', data, 0)[0]
    count = struct.unpack_from('<H', data, 2)[0]
    if count == 0 and fmt == 0:
        return None
    indices = []
    for i in range(count):
        off = 4 + i * 2
        if off + 2 > len(data):
            break
        indices.append(struct.unpack_from('<H', data, off)[0])
    label = roster_role.replace('pwt_', '').replace('_', ' ').title()
    out = [f"{label} Roster #{slot_index} | {count} Pokémon"]
    pool_role = _PWT_ROSTER_POOLS.get(roster_role, 'pwt_rental')
    pool_path = _role_path(pool_role)
    for pi in indices:
        line = _resolve_pwt_pool_entry(pi, pool_narc_path=pool_path)
        if line:
            out.append(f"  Pool[{pi}] {line}")
        else:
            out.append(f"  Pool[{pi}] (empty)")
    return "\n".join(out)


def decode_pwt_trainer_config(data: bytes, slot_index: int = 0, trainer_role: str = "pwt_trainers"):
    """Decode PWT trainer config (6B) with resolved roster + pokemon. Returns positional text."""
    if len(data) < 6:
        return None
    fmt = struct.unpack_from('<H', data, 0)[0]
    count = struct.unpack_from('<H', data, 2)[0]
    start_idx = struct.unpack_from('<H', data, 4)[0]
    if fmt == 0 and count == 0 and start_idx == 0:
        return None
    trainer_name = _resolve_pwt_trainer_name(slot_index, trainer_role)
    if trainer_name:
        out = [f"PKMN Trainer {trainer_name} | Picks {count} from pool | Pool start: {start_idx}"]
    else:
        label = trainer_role.replace('pwt_', '').replace('_', ' ').title()
        out = [f"{label} Trainer #{slot_index} | Format: {fmt} | Picks {count} from pool | Pool start: {start_idx}"]
    # Follow the role chain: trainer role → roster role → pool role
    chain = _PWT_ROLE_CHAINS.get(trainer_role)
    if chain:
        roster_role, pool_role = chain
        roster_path = _role_path(roster_role)
        pool_path = _role_path(pool_role)
        if roster_path and pool_path:
            try:
                roster_narc = _get_narc(roster_path)
                if slot_index < len(roster_narc.files):
                    rd = bytes(roster_narc.files[slot_index])
                    if len(rd) >= 4:
                        r_count = struct.unpack_from('<H', rd, 2)[0]
                        indices = []
                        for i in range(r_count):
                            off = 4 + i * 2
                            if off + 2 <= len(rd):
                                indices.append(struct.unpack_from('<H', rd, off)[0])
                        for pi in indices:
                            line = _resolve_pwt_pool_entry(pi, pool_narc_path=pool_path)
                            if line:
                                out.append(f"  {line}")
            except:
                pass
    return "\n".join(out)


def _resolve_pwt_text(tournament_id):
    """Resolve a PWT tournament ID to its name via the tournament_names text table.
    Tournament ID (u16 at offset 0x00 of the def) indexes directly into text file 405."""
    names = text_tables.get('tournament_names', [])
    if tournament_id < len(names):
        raw = names[tournament_id]
        if isinstance(raw, str):
            clean = re.sub(r'[^\x20-\x7E]', '', raw).strip()
            if clean and clean != '???':
                return clean
    return None


def decode_pwt_tournament_def(data: bytes, file_idx: int = 0):
    """Decode PWT tournament definition (1688B) from pwt_defs. Returns positional text."""
    if len(data) < 0x60:
        return None
    # Header
    tid = struct.unpack_from('<H', data, 0)[0]
    category = struct.unpack_from('<H', data, 2)[0]
    trainer_count = struct.unpack_from('<H', data, 4)[0]
    battle_format = struct.unpack_from('<H', data, 6)[0]
    pool_type = struct.unpack_from('<H', data, 8)[0]
    cfg5 = struct.unpack_from('<H', data, 0x0A)[0]
    cfg6 = struct.unpack_from('<H', data, 0x0C)[0]
    cfg7 = struct.unpack_from('<H', data, 0x0E)[0]
    cfg8 = struct.unpack_from('<H', data, 0x10)[0]
    flag1 = struct.unpack_from('<H', data, 0x12)[0]
    flag2 = struct.unpack_from('<H', data, 0x14)[0]

    BATTLE_TYPES = {1: "Single", 2: "Double", 3: "Triple", 4: "Rotation"}
    bt = BATTLE_TYPES.get(battle_format, f"Type {battle_format}")

    music_a = struct.unpack_from('<H', data, 0x18)[0]
    music_b = struct.unpack_from('<H', data, 0x1A)[0]

    # Tournament ID indexes directly into the tournament_names text table (file 405)
    tournament_name = _resolve_pwt_text(tid) or f"Tournament #{tid}"

    out = [f"Tournament #{tid} — {tournament_name}"]
    out.append(f"Trainers: {trainer_count} | Battle: {bt} | Pool type: {pool_type}")
    out.append(f"Config: [{cfg5}, {cfg6}, {cfg7}, {cfg8}]")
    if flag1 != 0xFFFF:
        flags = [f for f in [flag1, flag2] if f != 0xFFFF]
        out.append(f"Save flags: {flags}")
    out.append(f"Music: {music_a} / {music_b}")

    # Scan data regions at known offsets for pwttr indices
    trainer_indices = set()
    for region_start, region_end in [(0xA0, 0x130), (0x160, 0x1A8)]:
        if len(data) < region_end:
            continue
        for off in range(region_start, region_end, 2):
            val = struct.unpack_from('<H', data, off)[0]
            if 1 <= val <= 68:
                trainer_indices.add(val)

    if trainer_indices:
        sorted_idx = sorted(trainer_indices)
        out.append(f"Trainer pool indices: {sorted_idx}")
        # Resolve each via role chain
        roster_path = _role_path('pwt_rosters_b')
        pool_path = _role_path('pwt_champions')
        if roster_path and pool_path:
            try:
                roster_narc = _get_narc(roster_path)
                for ti in sorted_idx:
                    if ti >= len(roster_narc.files):
                        continue
                    rd = bytes(roster_narc.files[ti])
                    if len(rd) >= 6:
                        r_count = struct.unpack_from('<H', rd, 2)[0]
                        first_pool = struct.unpack_from('<H', rd, 4)[0]
                        line = _resolve_pwt_pool_entry(first_pool, pool_narc_path=pool_path)
                        if line:
                            species_part = line.split('|')[0].strip()
                            out.append(f"  pwttr[{ti}]: {species_part}  (+{r_count - 1} more)")
            except:
                pass

    return "\n".join(out)


EV_YIELD_STATS = ['HP', 'Atk', 'Def', 'Spe', 'SpA', 'SpD']

EXP_GROWTH_NAMES = {0: "Medium Fast", 1: "Erratic", 2: "Fluctuating", 3: "Medium Slow", 4: "Fast", 5: "Slow"}

def decode_personal(data: bytes, file_idx: int = 0):
    """Decode personal data. Gen IV=44B, Gen V=76B. Returns positional text."""
    if len(data) < 28 or data == b'\x00' * len(data):
        return None
    gen = text_gen or 5
    species_list = text_tables.get('species', [])
    type_list = text_tables.get('type_names', [])
    ability_list = text_tables.get('abilities', [])
    item_list = text_tables.get('items', [])

    # Base stats (identical layout across Gen IV/V)
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

    if gen <= 4:
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
        items = [struct.unpack_from('<H', data, 0x0C + i * 2)[0] for i in range(3)]
        held_labels = ['common', 'rare', 'hidden']
        gender = data[0x12]
        hatch_cycles = data[0x13]
        base_happiness = data[0x14]
        exp_growth = data[0x15]
        egg1, egg2 = data[0x16], data[0x17]
        ability_names = []
        for i in range(3):
            off = 0x18 + i * 2
            if off + 2 <= len(data):
                aid = struct.unpack_from('<H', data, off)[0]
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
    lines.append(f"Catch Rate: {catch_rate} | Hatch: {hatch_cycles} cycles | Happiness: {base_happiness}")
    lines.append(f"Growth: {EXP_GROWTH_NAMES.get(exp_growth, f'#{exp_growth}')} | Egg Groups: {egg1}, {egg2}")
    if held_parts:
        lines.append(f"Held Items: {' / '.join(held_parts)}")
    if evs:
        lines.append(f"EVs: {', '.join(evs)}")

    # Height/weight (Gen V only, at 0x24/0x26)
    if gen >= 5 and len(data) >= 0x28:
        height_dm = struct.unpack_from('<H', data, 0x24)[0]
        weight_hg = struct.unpack_from('<H', data, 0x26)[0]
        lines.append(f"Height: {height_dm / 10.0}m | Weight: {weight_hg / 10.0}kg")

    # TM/HM compatibility
    if tm_table:
        moves_list = text_tables.get('moves', [])
        tm_offset = 0x1C if gen <= 4 else 0x28
        if len(data) >= tm_offset + 16:
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

    return "\n".join(lines)


def decode_learnset(data: bytes, file_idx: int = 0):
    """Decode learnset. Returns positional text."""
    if len(data) < 2:
        return None
    gen = text_gen or 5
    species_list = text_tables.get('species', [])
    moves_list = text_tables.get('moves', [])
    species_name = species_list[file_idx] if file_idx < len(species_list) else f"#{file_idx}"

    moves = []
    if gen <= 4:
        for i in range(0, len(data) - 1, 2):
            raw = struct.unpack_from('<H', data, i)[0]
            if raw == 0xFFFF:
                break
            move_id = raw & 0x1FF
            level = (raw >> 9) & 0x7F
            move_name = moves_list[move_id] if move_id < len(moves_list) else f"move#{move_id}"
            moves.append((level, move_name))
    else:
        for i in range(0, len(data) - 3, 4):
            move_id = struct.unpack_from('<H', data, i)[0]
            level = struct.unpack_from('<H', data, i + 2)[0]
            if move_id == 0xFFFF:
                break
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
    if len(data) < 42 or data[:42] == b'\x00' * 42:
        return None
    species_list = text_tables.get('species', [])
    item_list = text_tables.get('items', [])
    moves_list = text_tables.get('moves', [])
    species_name = species_list[file_idx] if file_idx < len(species_list) else f"#{file_idx}"
    evo_lines = []
    for i in range(7):
        off = i * 6
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


MOVE_CATEGORIES_G5 = {0: "Status", 1: "Physical", 2: "Special"}
MOVE_CATEGORIES_G4 = {0: "Physical", 1: "Special", 2: "Status"}

def decode_move_data(data: bytes, file_idx: int = 0):
    """Decode move data. Returns positional text."""
    if data == b'\x00' * len(data):
        return None
    gen = text_gen or 5
    type_list = text_tables.get('type_names', [])
    moves_list = text_tables.get('moves', [])
    move_name = moves_list[file_idx] if file_idx < len(moves_list) else f"move#{file_idx}"

    if gen <= 4 and len(data) >= 12:
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


# Encounter NARC file index (a/1/2/7) -> location_names text table index
# Corrected mapping from script cross-reference against known BW2 location species.
# Zone header u16[2] stores internal zone IDs, NOT location_names text indices.
_B2W2_ENC_LOC = {
    0:6, 1:8, 2:21,                                          # Striaton City, Castelia City, Route 8
    3:117, 4:118, 5:119,                                     # Aspertia City, Virbank City, Humilau City
    6:32, 7:32,                                              # Dreamyard
    8:33, 9:72,                                              # Pinwheel Forest, Lostlorn Forest
    10:34, 11:34,                                            # Desert Resort
    12:35, 13:35, 14:35, 15:35, 16:35, 17:35, 18:35, 19:35, # Relic Castle
    20:37, 21:37, 22:37,                                     # Chargestone Cave
    23:38, 24:38, 25:38, 26:38,                              # Twist Mountain
    27:39, 28:39, 29:39, 30:39,                              # Dragonspiral Tower
    31:134,                                                  # Victory Road (BW2)
    32:61, 33:61, 34:61, 35:61, 36:61,                      # Giant Chasm
    37:129, 38:129, 39:129, 40:129, 41:129,                  # Castelia Sewers
    42:63,                                                   # P2 Laboratory
    43:71,                                                   # Undella Bay
    44:130, 45:130,                                          # Floccesy Ranch
    46:131, 47:131,                                          # Virbank Complex
    48:132, 49:132, 50:132, 51:132, 52:132, 53:132,         # Reversal Mountain
    54:132, 55:132, 56:132, 57:132, 58:132, 59:132, 60:132,
    61:133, 62:133, 63:133, 64:133, 65:133,                  # Strange House
    66:133, 67:133, 68:133, 69:133, 70:133,
    71:134, 72:134, 73:134, 74:134, 75:134,                  # Victory Road (BW2)
    76:134, 77:134, 78:134, 79:134, 80:134,
    81:136, 82:136, 83:136,                                  # Relic Passage
    84:137, 85:137, 86:137,                                  # Clay Tunnel
    87:149, 88:149, 89:149,                                  # Underground Ruins
    90:150,                                                  # Rock Peak Chamber
    91:151,                                                  # Iceberg Chamber
    92:152,                                                  # Iron Chamber
    93:141, 94:141,                                          # Seaside Cave
    95:147,                                                  # Nature Preserve
    96:65, 97:67, 98:68,                                     # Driftveil Drawbridge, Village Bridge, Marvelous Bridge
    99:14, 100:15, 101:16,                                   # Route 1, Route 2, Route 3
    102:53, 103:53,                                          # Wellspring Cave
    104:17, 105:17,                                          # Route 4
    106:18, 107:19,                                          # Route 5, Route 6
    108:54, 109:54,                                          # Mistralton Cave
    110:74,                                                  # Guidance Chamber
    111:20,                                                  # Route 7
    112:56, 113:56, 114:56, 115:56,                          # Celestial Tower
    116:21,                                                  # Route 8
    117:57,                                                  # Moor of Icirrus
    118:22,                                                  # Route 9
    119:24, 120:25, 121:26, 122:27,                          # Route 11, Route 12, Route 13, Route 14
    123:70,                                                  # Abundant Shrine
    124:28, 125:29,                                          # Route 15, Route 16
    126:72,                                                  # Lostlorn Forest
    127:31,                                                  # Route 18
    128:124, 129:125,                                        # Route 19, Route 20
    130:127, 131:128,                                        # Route 22, Route 23
    132:42,                                                  # Undella Town
    133:30,                                                  # Route 17
    134:126,                                                 # Route 21
}

# Encounter NARC file index (a/1/2/6) -> location_names text table index
# Corrected mapping from script cross-reference against known BW1 location species.
_BW1_ENC_LOC = {
    0:6, 1:8,                                                # Striaton City, Castelia City (surf)
    2:21,                                                    # Route 8
    3:32, 4:32,                                              # Dreamyard
    5:33, 6:33,                                              # Pinwheel Forest
    7:34, 8:34,                                              # Desert Resort
    9:35, 10:35, 11:35, 12:35, 13:35, 14:35,                # Relic Castle
    15:35, 16:35, 17:35, 18:35, 19:35, 20:35,
    21:35, 22:35, 23:35, 24:35, 25:35, 26:35,
    27:35, 28:35, 29:35, 30:35, 31:35, 32:35,
    33:35, 34:35, 35:35, 36:35, 37:35, 38:35, 39:35,
    40:36,                                                   # Cold Storage
    41:37, 42:37, 43:37,                                     # Chargestone Cave
    44:38, 45:38, 46:38, 47:38,                              # Twist Mountain
    48:20, 49:20,                                            # Route 7
    50:39, 51:39,                                            # Dragonspiral Tower
    52:40, 53:40, 54:40, 55:40, 56:40, 57:40,               # Victory Road
    58:40, 59:40, 60:40, 61:40, 62:40, 63:40,
    64:40, 65:40, 66:40, 67:40,
    68:26,                                                   # Route 13
    69:61, 70:61, 71:61,                                     # Giant Chasm
    72:63,                                                   # P2 Laboratory
    73:71,                                                   # Undella Bay
    74:0, 76:0,                                              # Empty/unused
    75:67,                                                   # Village Bridge
    77:14, 78:15, 79:16,                                     # Route 1, Route 2, Route 3
    80:53, 81:53,                                            # Wellspring Cave
    82:17,                                                   # Route 4
    83:18,                                                   # Route 5
    84:19,                                                   # Route 6
    85:54, 86:54, 87:54,                                     # Mistralton Cave
    88:20,                                                   # Route 7
    89:56, 90:56, 91:56, 92:56,                              # Celestial Tower
    93:57, 94:57,                                            # Moor of Icirrus
    95:22,                                                   # Route 9
    96:59, 97:59, 98:59,                                     # Challenger's Cave
    99:23, 100:23,                                           # Route 10
    101:24,                                                  # Route 11
    102:25,                                                  # Route 12
    103:26,                                                  # Route 13
    104:27,                                                  # Route 14
    105:70,                                                  # Abundant Shrine
    106:28,                                                  # Route 15
    107:29,                                                  # Route 16
    108:72,                                                  # Lostlorn Forest
    109:31,                                                  # Route 18
    110:30,                                                  # Route 17
    111:42,                                                  # Undella Town
}

# Encounter NARC file index (fielddata/encountdata/pl_enc_data.narc) -> location_names text index
# Built by species cross-reference against Platinum location list.
# Platinum has NO flat enc->loc in ARM9 (stride 6 is garbage). Hardcoded like HGSS.
_CPU_ENC_LOC = {
    5:46, 6:46,                                             # Oreburgh Mine
    7:47,                                                   # Valley Windworks
    8:48,                                                   # Eterna Forest
    9:28,                                                   # Route 213
    10:22, 21:22,                                           # Route 207
    11:50, 12:50, 13:50, 14:50, 15:50, 16:50, 17:50,       # Mt. Coronet
    18:50, 19:50, 20:50, 22:50,                             # Mt. Coronet
    23:45, 24:45, 25:45, 26:45, 27:45, 28:45,              # Route 230
    29:53, 30:53, 31:53, 32:53, 33:53, 34:53, 35:53,       # Solaceon Ruins (Unown)
    36:53, 37:53, 38:53, 39:53, 40:53, 41:53, 42:53,       # Solaceon Ruins (Unown)
    43:53, 44:53, 45:53, 46:53,                             # Solaceon Ruins (Unown)
    47:54, 48:54, 49:54, 50:54, 51:54, 52:54,              # Victory Road
    53:57, 54:57, 55:57,                                    # Ravaged Path
    56:41, 57:41,                                           # Route 226
    58:84,                                                  # Stark Mountain
    59:73,                                                  # Valor Lakefront
    60:70, 63:70, 64:70, 65:70, 66:70, 67:70, 68:70,       # Old Chateau
    61:117, 62:117,                                         # Distortion World
    69:117, 70:117, 71:117, 72:117, 73:117, 74:117,         # Distortion World
    75:117, 76:117, 77:117, 78:117, 79:117, 80:117,         # Distortion World
    81:117, 82:117, 83:117, 84:117, 85:117, 86:117,         # Distortion World
    87:117, 88:117, 89:117, 90:117, 91:117, 92:117,         # Distortion World
    93:117, 94:117, 95:117, 96:117, 97:117, 98:117,         # Distortion World
    99:117, 100:117, 101:117, 102:117, 103:117, 104:117,    # Distortion World
    105:117,                                                # Distortion World
    106:32, 107:32, 108:32, 109:32, 110:32, 111:32,        # Route 217 (Jynx/Sneasel/Smoochum)
    112:46,                                                 # Oreburgh Mine
    113:65,                                                 # Wayward Cave (Gible)
    114:66, 115:66, 116:66,                                 # Ruin Maniac Cave (Hippopotas)
    117:68,                                                 # Trophy Garden (Pikachu)
    119:50,                                                 # Mt. Coronet
    120:69, 121:69, 122:69, 123:69, 124:69,                # Iron Island
    125:62, 126:62, 127:62, 128:62, 129:62,                # Turnback Cave (Gastly)
    130:62, 131:62, 132:62, 133:62,                        # Turnback Cave (Gastly)
    134:16, 135:16,                                        # Route 201
    136:76,                                                # Lake Verity
    137:31,                                                # Route 216
    138:29,                                                # Route 214
    139:31,                                                # Route 216
    140:17, 141:17, 142:17, 143:17, 144:17,               # Route 202
    145:20,                                                # Route 205
    146:48,                                                # Eterna Forest
    147:21, 148:21,                                        # Route 206
    149:23, 150:23,                                        # Route 208
    151:70, 152:70, 153:70, 154:70, 155:70,               # Old Chateau (Gastly/Golbat)
    156:21,                                                # Route 206
    157:22, 158:22, 159:50,                                # Route 207 / Mt. Coronet
    160:23,                                                # Route 208
    161:27,                                                # Route 212 (Croagunk)
    162:28,                                                # Route 213
    163:29,                                                # Route 214
    164:30,                                                # Route 215
    165:32,                                                # Route 217
    166:31,                                                # Route 216
    167:28,                                                # Route 213
    169:36,                                                # Route 221
    170:37,                                                # Route 222
    171:44,                                                # Route 229
    172:54,                                                # Victory Road
    173:41,                                                # Route 226
    174:43,                                                # Route 228
    175:44,                                                # Route 229
    181:54,                                                # Victory Road
    182:28,                                                # Route 213
}

# Encounter NARC file index (a/1/3/6) -> location_names text table index (a/0/2/7:279)
# Built by species cross-reference: each encounter file's species composition was matched
# to known HGSS locations. HGSS has NO flat enc->loc table in the binary (unlike DP/Pt) —
# the game resolves encounters through map headers at runtime. 142 entries, all verified.
_HGSS_ENC_LOC = {
    0: 126,                                              # New Bark Town
    1: 177,                                              # Route 29
    2: 127,                                              # Cherrygrove City
    3: 178,                                              # Route 30
    4: 179,                                              # Route 31
    5: 128,                                              # Violet City
    6: 204, 7: 204,                                      # Sprout Tower
    8: 180,                                              # Route 32
    9: 209, 10: 209, 11: 209, 12: 209, 13: 209,         # Ruins of Alph
    14: 210, 15: 210, 16: 210,                           # Union Cave
    17: 181,                                              # Route 33
    18: 211, 19: 211,                                    # SLOWPOKE Well
    20: 214,                                              # Ilex Forest
    21: 182,                                              # Route 34
    22: 183,                                              # Route 35
    23: 207, 24: 207,                                    # National Park
    25: 184,                                              # Route 36
    26: 185,                                              # Route 37
    27: 133,                                              # Ecruteak City
    28: 206, 29: 206,                                    # Burned Tower
    30: 205, 31: 205, 32: 205, 33: 205,                 # Bell Tower
    34: 205, 35: 205, 36: 205, 37: 205,
    38: 186,                                              # Route 38
    39: 187,                                              # Route 39
    40: 132,                                              # Olivine City
    41: 188,                                              # Route 40
    42: 189,                                              # Route 41
    43: 218, 44: 218, 45: 218, 46: 218,                 # Whirl Islands
    47: 218, 48: 218, 49: 218, 50: 218,
    51: 130,                                              # Cianwood City
    52: 190,                                              # Route 42
    53: 216, 54: 216, 55: 216, 56: 216,                 # Mt. Mortar
    57: 191,                                              # Route 43
    58: 135,                                              # Lake of Rage
    59: 192,                                              # Route 44
    60: 217, 61: 217, 62: 217, 63: 217,                 # Ice Path
    64: 136,                                              # Blackthorn City
    65: 136,                                              # Blackthorn City
    66: 222,                                              # Dragon's Den
    67: 193,                                              # Route 45
    68: 194,                                              # Route 46
    69: 220, 70: 220,                                    # Dark Cave
    71: 195,                                              # Route 47
    72: 196, 73: 196,                                    # Route 48
    74: 228, 75: 228, 76: 228, 77: 228, 78: 228,       # Cliff Cave
    79: 219, 80: 219, 81: 219, 82: 219,                 # Mt. Silver Cave
    83: 234,                                              # Cliff Edge Gate
    84: 227,                                              # Safari Zone Gate
    85: 176,                                              # Route 28
    86: 219, 87: 219, 88: 219, 89: 219,                 # Mt. Silver Cave
    90: 137,                                              # Mt. Silver
    91: 174,                                              # Route 26
    92: 175,                                              # Route 27
    93: 223,                                              # Tohjo Falls
    94: 175,                                              # Route 27
    95: 174,                                              # Route 26
    96: 143,                                              # Vermilion City
    97: 139,                                              # Viridian City
    98: 138,                                              # Pallet Town
    99: 144,                                              # Celadon City
    100: 145,                                             # Fuchsia City
    101: 146,                                             # Cinnabar Island
    102: 195,                                             # Route 47
    103: 161,                                             # Route 13
    104: 162,                                             # Route 14
    105: 176,                                             # Route 28
    106: 198, 107: 198,                                  # Mt. Moon
    108: 200, 109: 200,                                  # Rock Tunnel
    110: 221,                                             # Victory Road
    111: 149,                                             # Route 1
    112: 150,                                             # Route 2
    113: 151,                                             # Route 3
    114: 152,                                             # Route 4
    115: 153,                                             # Route 5
    116: 154,                                             # Route 6
    117: 155,                                             # Route 7
    118: 156,                                             # Route 8
    119: 157,                                             # Route 9
    120: 158,                                             # Route 10
    121: 159,                                             # Route 11
    122: 161,                                             # Route 13
    123: 162,                                             # Route 14
    124: 163,                                             # Route 15
    125: 164,                                             # Route 16
    126: 165,                                             # Route 17
    127: 166,                                             # Route 18
    128: 169,                                             # Route 21
    129: 170,                                             # Route 22
    130: 172,                                             # Route 24
    131: 173,                                             # Route 25
    132: 223,                                             # Tohjo Falls
    133: 197,                                             # Diglett's Cave
    134: 221, 135: 221,                                  # Victory Road
    136: 150,                                             # Route 2
    137: 224,                                             # Viridian Forest
    138: 226,                                             # S.S. Aqua
    139: 199, 140: 199, 141: 199,                        # Cerulean Cave
}

# (species_id, form_idx) -> parenthetical label appended to species name in encounter display
# Form 0 entries are included when the base form has a meaningful name (e.g. Basculin Red-Striped)
_FORM_NAMES = {
    (351, 1): "Sunny", (351, 2): "Rainy", (351, 3): "Snowy",
    (386, 1): "Attack", (386, 2): "Defense", (386, 3): "Speed",
    (412, 0): "Plant", (412, 1): "Sandy", (412, 2): "Trash",
    (413, 0): "Plant", (413, 1): "Sandy", (413, 2): "Trash",
    (421, 1): "Sunshine",
    (422, 0): "West", (422, 1): "East",
    (423, 0): "West", (423, 1): "East",
    (479, 1): "Heat", (479, 2): "Wash", (479, 3): "Frost", (479, 4): "Fan", (479, 5): "Mow",
    (487, 1): "Origin",
    (492, 1): "Sky",
    (550, 0): "Red-Striped", (550, 1): "Blue-Striped",
    (555, 0): "Standard", (555, 1): "Zen Mode",
    (585, 0): "Spring", (585, 1): "Summer", (585, 2): "Autumn", (585, 3): "Winter",
    (586, 0): "Spring", (586, 1): "Summer", (586, 2): "Autumn", (586, 3): "Winter",
    (641, 1): "Therian", (642, 1): "Therian", (645, 1): "Therian",
    (646, 1): "White", (646, 2): "Black",
    (647, 1): "Resolute",
    (648, 1): "Pirouette",
}


def _decode_encounters_gen5(data: bytes) -> dict:
    """Decode Gen V encounter data (BW/B2W2).
    232 bytes per season. Species u16 encodes form in upper bits (& 0x7FF)."""
    if len(data) < 232:
        return None

    seasons = []
    season_names = ['Spring', 'Summer', 'Fall', 'Winter']
    num_seasons = len(data) // 232

    for season_idx in range(num_seasons):
        season_data = data[season_idx * 232:(season_idx + 1) * 232]

        rates = {
            "grass": season_data[0], "double_grass": season_data[1], "special_grass": season_data[2],
            "surf": season_data[3], "special_surf": season_data[4],
            "fishing": season_data[5], "special_fishing": season_data[6]
        }

        def read_entries(offset, count):
            entries = []
            for j in range(count):
                pos = offset + j * 4
                if pos + 4 > len(season_data):
                    break
                raw = struct.unpack_from("<H", season_data, pos)[0]
                species_id = raw & 0x7FF
                form = raw >> 11
                min_lv = season_data[pos + 2]
                max_lv = season_data[pos + 3]
                if species_id == 0:
                    continue
                name = get_text("species", species_id)
                form_label = _FORM_NAMES.get((species_id, form))
                if form_label is None and form > 0:
                    form_label = f"Form {form}"
                if form_label:
                    name += f" ({form_label})"
                entries.append({"species": name, "level": f"{min_lv}-{max_lv}" if min_lv != max_lv else str(min_lv)})
            return entries

        result = {"rates": {k: v for k, v in rates.items() if v > 0}}

        groups = [
            ("grass", 8, 12), ("double_grass", 56, 12), ("special_grass", 104, 12),
            ("surf", 152, 5), ("special_surf", 172, 5),
            ("fishing", 192, 5), ("special_fishing", 212, 5)
        ]
        for name, offset, count in groups:
            if rates.get(name, 0) > 0:
                entries = read_entries(offset, count)
                if entries:
                    result[name] = entries

        if num_seasons > 1:
            result["season"] = season_names[season_idx] if season_idx < len(season_names) else f"Season {season_idx + 1}"
            seasons.append(result)
        else:
            return result

    return {"seasons": seasons} if seasons else None


def _decode_encounters_dpp(data: bytes) -> dict:
    """Decode Gen IV DP/Pt encounter data (424 bytes).
    Land: rate(u32) + 12 grass slots(u32 level + u32 species) + replacements.
    Water: 5 sections × (rate u32 + 5 × {max_lv u8, min_lv u8, pad u16, species u16, pad u16})."""
    if len(data) != 424:
        return None

    result = {}

    # Grass rate at offset 0, slots at offset 4
    grass_rate = struct.unpack_from("<I", data, 0)[0]
    if grass_rate > 0:
        grass = []
        for i in range(12):
            pos = 4 + i * 8
            level = struct.unpack_from("<I", data, pos)[0]
            species_id = struct.unpack_from("<I", data, pos + 4)[0]
            if species_id == 0:
                continue
            grass.append({"species": get_text("species", species_id), "level": level})
        if grass:
            result["grass"] = grass
            result["grass_rate"] = grass_rate

    # Replacement species (offset 100): swarm(2), day(2), night(2), radar(4)
    def read_replacements(offset, count):
        species = []
        for i in range(count):
            sid = struct.unpack_from("<I", data, offset + i * 4)[0]
            if sid > 0:
                species.append(get_text("species", sid))
        return species

    swarm = read_replacements(100, 2)
    if swarm:
        result["swarm"] = swarm
    day = read_replacements(108, 2)
    if day:
        result["day_replacements"] = day
    night = read_replacements(116, 2)
    if night:
        result["night_replacements"] = night
    radar = read_replacements(124, 4)
    if radar:
        result["radar"] = radar

    # Water sections start at offset 204 (0xCC)
    # 5 sections: surf, surf_special(?), old_rod, good_rod, super_rod
    water_names = ["surf", "surf_special", "old_rod", "good_rod", "super_rod"]
    water_offset = 204
    for section_name in water_names:
        rate = struct.unpack_from("<I", data, water_offset)[0]
        water_offset += 4
        if rate > 0:
            entries = []
            for i in range(5):
                pos = water_offset + i * 8
                max_lv = data[pos]
                min_lv = data[pos + 1]
                species_id = struct.unpack_from("<H", data, pos + 4)[0]
                if species_id == 0:
                    continue
                name = get_text("species", species_id)
                lvl = f"{min_lv}-{max_lv}" if min_lv != max_lv else str(min_lv)
                entries.append({"species": name, "level": lvl})
            if entries:
                result[section_name] = entries
        water_offset += 40  # 5 slots × 8 bytes

    return result if result else None


def _decode_encounters_hgss(data: bytes) -> dict:
    """Decode Gen IV HGSS encounter data (196 bytes).
    Header: 8 × u8 rates. Grass: 12 levels + 3×12 species (morn/day/night) + 4 sound species.
    Water: surf(5) + rocksmash(2) + oldrod(5) + goodrod(5) + superrod(5), each 4B/slot."""
    if len(data) != 196:
        return None

    # Header rates (u8 each)
    grass_rate = data[0]
    surf_rate = data[1]
    rock_smash_rate = data[2]
    old_rod_rate = data[3]
    good_rod_rate = data[4]
    super_rod_rate = data[5]

    result = {}

    # Grass: 12 levels at offset 8, then 3 species tables (morning/day/night)
    if grass_rate > 0:
        levels = [data[8 + i] for i in range(12)]
        tables = {}
        for t_idx, t_name in enumerate(["morning", "day", "night"]):
            base = 20 + t_idx * 24  # 12 species × 2 bytes = 24
            species = []
            for i in range(12):
                sid = struct.unpack_from("<H", data, base + i * 2)[0]
                if sid == 0:
                    continue
                species.append({"species": get_text("species", sid), "level": levels[i]})
            if species:
                tables[t_name] = species
        if tables:
            result["grass"] = tables
            result["grass_rate"] = grass_rate

    # Sound species at offset 92 (Hoenn Sound × 2, Sinnoh Sound × 2)
    sound_species = []
    for i in range(4):
        sid = struct.unpack_from("<H", data, 92 + i * 2)[0]
        if sid > 0:
            sound_species.append(get_text("species", sid))
    if sound_species:
        result["sound"] = {"hoenn": sound_species[:2], "sinnoh": sound_species[2:]}

    # Water helper: each slot is min_lv u8, max_lv u8, species u16 (4 bytes)
    def read_water(offset, count):
        entries = []
        for i in range(count):
            pos = offset + i * 4
            min_lv = data[pos]
            max_lv = data[pos + 1]
            species_id = struct.unpack_from("<H", data, pos + 2)[0]
            if species_id == 0:
                continue
            lvl = f"{min_lv}-{max_lv}" if min_lv != max_lv else str(min_lv)
            entries.append({"species": get_text("species", species_id), "level": lvl})
        return entries

    if surf_rate > 0:
        surf = read_water(100, 5)
        if surf:
            result["surf"] = surf

    if rock_smash_rate > 0:
        rocks = read_water(120, 2)
        if rocks:
            result["rock_smash"] = rocks

    if old_rod_rate > 0:
        old = read_water(128, 5)
        if old:
            result["old_rod"] = old

    if good_rod_rate > 0:
        good = read_water(148, 5)
        if good:
            result["good_rod"] = good

    if super_rod_rate > 0:
        sup = read_water(168, 5)
        if sup:
            result["super_rod"] = sup

    return result if result else None


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


POKEATHLON_STATS = ['Power', 'Speed', 'Jump', 'Stamina', 'Skill']

def decode_pokeathlon_performance(data: bytes, file_idx: int = 0):
    """Decode Pokéathlon performance stats (HGSS only). Returns positional text."""
    if len(data) != 20:
        return None

    species_idx = file_idx + 1
    species_list = text_tables.get('species', [])
    species_name = species_list[species_idx] if species_idx < len(species_list) else f"#{species_idx}"

    parts = []
    for i, stat_name in enumerate(POKEATHLON_STATS):
        base = data[i] + 1
        mn = data[9 + i * 2] + 1
        mx = data[10 + i * 2] + 1
        if mn == base == mx:
            parts.append(f"{stat_name}: {base}★")
        elif mn == base:
            parts.append(f"{stat_name}: {base}-{mx}★")
        else:
            parts.append(f"{stat_name}: {mn}/{base}/{mx}★")

    lines = [f"{species_name} (#{species_idx}) — Pokéathlon"]
    lines.append(" | ".join(parts))
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


def format_trainer(file_idx):
    """Eagerly load trdata + trpoke and format as positional text."""
    # Use narc_roles (built from GAME_INFO at bootstrap or auto-discovery)
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

        template = td_data[0] & 0x03
        gen = text_gen or 5
        _fmts = TRPOKE_FORMATS_G4 if gen <= 4 else TRPOKE_FORMATS_G5
        poke_size = _fmts.get(template, _fmts[0])
        num_pokemon = len(tp_data) // poke_size
        prize = 0
        if num_pokemon > 0:
            last_off = (num_pokemon - 1) * poke_size
            if gen <= 4:
                last_level = struct.unpack_from('<H', tp_data, last_off + 2)[0]
            else:
                last_level = tp_data[last_off + 2]
            prize = trdata.get("reward_multiplier", 0) * last_level * 4

        class_name = trdata.get('class', '???')
        trainer_name = trdata.get('name', f'Trainer #{file_idx}')

        # Look up location for special trainers (Gym Leaders, E4, Champions, etc.)
        game_code = current_rom['header']['game_code'] if current_rom else None
        location = get_trainer_location(game_code, class_name, trainer_name) if game_code else None
        
        if location:
            lines = [f"{class_name} {trainer_name} - {location}"]
        else:
            lines = [f"{class_name} {trainer_name}"]

        _chal_delta = get_bw2_challenge_delta(file_idx, game_code)



        _gen = text_gen or 5
        pokemon = trpoke.get('pokemon', [])
        for poke in pokemon:
            species = poke.get('species', '???')
            species_id = poke.get('species_id', 0)
            level = poke.get('level', '?')
            held = poke.get('held_item', None)

            # Gen IV uses ALL CAPS species names; Gen V uses title case
            if _gen <= 4:
                species = species.upper()

            # Form resolution
            form_idx = poke.get('form', 0)
            if form_idx:
                form_label = _FORM_NAMES.get((species_id, form_idx), '')
                if form_label:
                    species = f"{species}-{form_label}"

            _delta = _chal_delta
            if _delta:
                header = f"{species} (Lv. {level + _delta})"
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
            footer.append(f"Items: {', '.join(items)}")
        if footer:
            lines.append("")
            lines.append(" | ".join(footer))

        return "\n".join(lines)
    except Exception:
        return None


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


def _auto_decode(path: str, data: bytes):
    """Auto-decode known structures by role, not hardcoded paths."""
    if not narc_roles or ':' not in path:
        return {"_unknown": True, "reason": "no role mapping", "hint": f"scope({path}) for raw bytes"}

    narc_part, idx_str = path.rsplit(':', 1)
    narc_part = narc_part.strip('/')
    file_idx = int(idx_str)
    role = narc_roles.get(narc_part)
    if not role:
        return {"_unknown": True, "reason": f"no role for {narc_part}", "hint": f"scope({path}) or dowse(name='...', narc_path='{narc_part}')"}

    rom = current_rom['rom']

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
                # Resolve location name -- use game-specific mapping
                gc = current_rom['header']['game_code'] if current_rom else ''
                loc_id = 0
                if gc in ('IRE', 'IRD'):  # Black 2 / White 2
                    loc_id = _B2W2_ENC_LOC.get(file_idx, 0)
                elif gc in ('IRB', 'IRA'):  # Black / White
                    loc_id = _BW1_ENC_LOC.get(file_idx, 0)
                elif gc in ('ADA', 'APA'):  # Diamond / Pearl
                    arm9 = current_rom.get('arm9_data')
                    if arm9 and 0xED738 + file_idx * 2 + 2 <= len(arm9):
                        loc_id = struct.unpack_from('<H', arm9, 0xED738 + file_idx * 2)[0]
                elif gc == 'CPU':  # Platinum
                    loc_id = _CPU_ENC_LOC.get(file_idx, 0)
                elif gc in ('IPK','IPG'):  # HG/SS
                    loc_id = _HGSS_ENC_LOC.get(file_idx, 0)
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
    except Exception:
        pass

    return None







# ============ Tool Handlers ============

async def spotlight(path: str) -> dict:
    """Open a ROM file for exploration. Multiple ROMs can be open simultaneously."""
    global current_rom, current_flipnote

    ensure_dirs()
    rom_type = detect_rom_type(path)

    # Peek at header to check if already loaded
    if rom_type == 'nds':
        header = read_nds_header(path)
    elif rom_type in ('gba', 'gbc', 'gb'):
        header = read_gba_header(path) if rom_type == 'gba' else read_gb_header(path)
    else:
        return {"error": f"Unknown ROM type: {path}"}

    gc = header['game_code']

    # Already loaded? Just switch to it
    if gc in loaded_roms:
        _save_active_state()
        _restore_state(gc)
        result = {
            "rom_type": rom_type, "game_code": gc,
            "game_title": header['game_title'], "region": header['region'],
            "flipnote": current_flipnote['path'],
            "switched": True, "loaded": list(loaded_roms.keys())
        }
        return result

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
                gc, header['game_title'], header['region'],
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
            _b2_patch = bytes([
                0xF8,0xB5,0x82,0xB0,0x00,0x90,0x15,0x1C,0x08,0x1C,0xFF,0xF7,0xAB,0xF9,0xF7,0xF7,
                0x61,0xFF,0xF7,0xF7,0xA1,0xFF,0x04,0x1C,0x28,0x1C,0x00,0xF0,0x77,0xFB,0x07,0x1C,
                0x4F,0x21,0x00,0x98,0x00,0x22,0x89,0x00,0x42,0x54,0x00,0x2C,0x01,0xD1,0x50,0x1E,
                0x47,0x43,0x01,0x2C,0x36,0xD0,0x4F,0x21,0x00,0x98,0x89,0x00,0x47,0x54,0x00,0x20,
                0x01,0x90,0x01,0x98,0x81,0x00,0x18,0x48,0x40,0x58,0x81,0x00,0x00,0x98,0x40,0x18,
                0x45,0x6A,0x00,0x2D,0x21,0xD0,0x28,0x1C,0x00,0x24,0x07,0xF0,0xE5,0xFB,0x00,0x28,
                0x1B,0xDD,0x28,0x1C,0x21,0x1C,0x07,0xF0,0x67,0xFC,0x06,0x1C,0x9E,0x21,0x00,0x22,
                0x04,0xF0,0x5A,0xFB,0x00,0x04,0x00,0x0C,0xC2,0x19,0x00,0x2A,0x00,0xDC,0x01,0x22,
                0x30,0x1C,0x9E,0x21,0x04,0xF0,0x62,0xFB,0x30,0x1C,0x05,0xF0,0x59,0xFA,0x28,0x1C,
                0x64,0x1C,0x07,0xF0,0xC9,0xFB,0x84,0x42,0xE3,0xDB,0x01,0x98,0x40,0x1C,0x01,0x90,
                0x02,0x28,0xCE,0xD3,0x02,0xB0,0xF8,0xBD,0x68,0x00,0x09,0x02,
            ])
            _w2_patch = bytes([
                0xF8,0xB5,0x82,0xB0,0x00,0x90,0x15,0x1C,0x08,0x1C,0xFF,0xF7,0xAB,0xF9,0xF7,0xF7,
                0x61,0xFF,0xF7,0xF7,0xA1,0xFF,0x04,0x1C,0x28,0x1C,0x00,0xF0,0x8D,0xFB,0x07,0x1C,
                0x4F,0x21,0x00,0x98,0x00,0x22,0x89,0x00,0x42,0x54,0x00,0x2C,0x01,0xD1,0x50,0x1E,
                0x47,0x43,0x01,0x2C,0x36,0xD0,0x4F,0x21,0x00,0x98,0x89,0x00,0x47,0x54,0x00,0x20,
                0x01,0x90,0x01,0x98,0x81,0x00,0x18,0x48,0x40,0x58,0x81,0x00,0x00,0x98,0x40,0x18,
                0x45,0x6A,0x00,0x2D,0x21,0xD0,0x28,0x1C,0x00,0x24,0x07,0xF0,0xFB,0xFB,0x00,0x28,
                0x1B,0xDD,0x28,0x1C,0x21,0x1C,0x07,0xF0,0x7D,0xFC,0x06,0x1C,0x9E,0x21,0x00,0x22,
                0x04,0xF0,0x70,0xFB,0x00,0x04,0x00,0x0C,0xC2,0x19,0x00,0x2A,0x00,0xDC,0x01,0x22,
                0x30,0x1C,0x9E,0x21,0x04,0xF0,0x78,0xFB,0x30,0x1C,0x05,0xF0,0x6F,0xFA,0x28,0x1C,
                0x64,0x1C,0x07,0xF0,0xDF,0xFB,0x84,0x42,0xE3,0xDB,0x01,0x98,0x40,0x1C,0x01,0x90,
                0x02,0x28,0xCE,0xD3,0x02,0xB0,0xF8,0xBD,0x94,0x00,0x09,0x02,
            ])
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
        global text_gen
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

    else:  # gba/gbc/gb
        fpn_path = find_flipnote(gc)
        if not fpn_path:
            fpn_path = create_flipnote(
                gc, header['game_title'], header['region'],
                header['region_char'], [], {}, False
            )
        current_rom = {
            'type': rom_type, 'path': path, 'header': header, 'data': None
        }

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

    # Build Eonet in background — capture gc now so the thread doesn't race on current_rom
    if current_rom and current_rom['type'] == 'nds':
        import asyncio as _asyncio
        _gc_capture = gc
        _asyncio.ensure_future(
            _asyncio.get_event_loop().run_in_executor(None, lambda: _build_eonet(_gc_capture))
        )

    # Build clean summary card
    game_info = GAME_INFO.get(gc, {})
    narcs = game_info.get("narcs", {})
    lines = [
        f"{header['game_title']} ({gc}) — {header['region']}",
        f"Type: {rom_type}  |  Loaded: {list(loaded_roms.keys())}",
    ]
    tt = text_table_result
    if tt and tt.get("status") == "ok":
        detected = list(tt.get("detected", {}).keys())
        lines.append(f"Text: Gen {tt['gen']} decoded — {tt['file_count']} files, tables: {', '.join(detected)}")
        if tt.get("tm_table"): lines.append(f"TM/HM: {tt['tm_table']}")
    elif tt and tt.get("error"):
        lines.append(f"Text: ERROR — {tt['error']}")
    key_roles = ("trdata", "trpoke", "personal", "learnsets", "encounters", "items")
    for role in key_roles:
        if role in narcs: lines.append(f"  {role}: {narcs[role]}")
    lines.append(f"Flipnote: {fpn_path}")
    lines.append("ICR indexing in background...")
    return {"_card": True, "text": "\n".join(lines), "game_code": gc, "loaded": list(loaded_roms.keys())}


async def return_tool(save: bool = False) -> dict:
    """Close the active ROM (or all with save=False). Switches to another loaded ROM if available."""
    if not current_rom:
        return {"error": "No ROM currently open"}

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


async def summarize(path: str = "/", expand_narcs: bool = False) -> dict:
    """List contents at a path. Pass a NARC path to see its contents."""
    if not current_rom:
        return {"error": "No ROM currently open"}

    if current_rom['type'] != 'nds':
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





async def decipher(path: str, offset: int = 0, length: int = None, decompress: bool = True) -> dict:
    """Read and decode files. Auto-decompresses and auto-decodes known structures (trainers, pokemon, encounters, etc.)."""
    # Multi-file: comma-separated paths
    if "," in path:
        results = []
        for p in path.split(","):
            p = p.strip()
            if p:
                results.append(await decipher(p, offset, length, decompress))
        return {"multi": True, "results": results}

    if not current_rom:
        return {"error": "No ROM currently open"}

    # Cross-ROM prefix: "IRE:a/0/1/6:1" routes to that ROM's data
    gc_prefix, clean_path = _parse_rom_prefix(path)
    if gc_prefix and gc_prefix != current_rom['header']['game_code']:
        orig_gc = _switch_rom(gc_prefix)
        try:
            result = await decipher(clean_path, offset, length, decompress)
        finally:
            _switch_rom(orig_gc)
        return result
    elif gc_prefix:
        path = clean_path  # Same ROM, just strip prefix

    if current_rom['type'] == 'nds':
        rom = current_rom['rom']

        try:
            if path.lower() == 'arm9.bin':
                data = bytes(current_rom['arm9_data'])
                compression = 'none'
            elif path.lower() == 'arm7.bin':
                data = bytes(current_rom['arm7_data'])
                compression = 'none'
            elif _is_overlay_path(path) >= 0:
                ov_id = _is_overlay_path(path)
                overlays = current_rom.get('overlays', {})
                if ov_id not in overlays:
                    return {"error": f"Overlay {ov_id} not found (available: {sorted(overlays.keys())})"}
                data = bytes(overlays[ov_id])
                compression = 'none'  # already decompressed during load
            elif ':' in path:
                narc_path, file_idx = path.rsplit(':', 1)
                file_idx = int(file_idx)
                narc = _get_narc(narc_path.lstrip('/'))
                if file_idx >= len(narc.files):
                    return {"error": f"Index {file_idx} out of range (NARC has {len(narc.files)} files)"}
                data = narc.files[file_idx]
                compression = 'none'
                if decompress:
                    data, compression = decompress_data(data)
                    if compression != 'none':
                        current_rom['compression_state'][path] = compression
            else:
                data = rom.getFileByName(path.lstrip('/'))
                compression = 'none'
                if decompress:
                    data, compression = decompress_data(data)
                    if compression != 'none':
                        current_rom['compression_state'][path] = compression

            if length:
                data = data[offset:offset + length]
            elif offset:
                data = data[offset:]
            decoded = _auto_decode(path, data)
            _path_notes = _notes_for_path(path)
            result = {"path": path, "size": len(data), "compression": compression, "decoded": decoded}
            if _path_notes:
                result["known"] = _path_notes
            if isinstance(decoded, dict) and decoded.get("_unknown"):
                result["status"] = f"not decoded: {decoded['reason']}"
                result["hint"] = decoded.get("hint", "")
                result["hex"] = _format_hex(data[offset:min(len(data), offset+128)], offset)
                result["hex_note"] = f"first 128B shown — call scope({path}) for full dump"
            elif decoded is None:
                result["status"] = "not decoded: role known but decoder returned nothing"
                result["hex"] = _format_hex(data[offset:min(len(data), offset+128)], offset)
                result["hex_note"] = f"first 128B shown — call scope({path}) for full dump"
            return result

        except Exception as e:
            return {"error": str(e)}

    else:
        with open(current_rom['path'], 'rb') as f:
            f.seek(offset)
            data = f.read(length) if length else f.read()
        return {"offset": offset, "size": len(data), "hex": _format_hex(data, offset)}


async def sketch(path: str, data: str, offset: int = 0, encoding: str = "hex") -> dict:
    """Write data to a file."""
    if not current_rom:
        return {"error": "No ROM currently open"}

    if encoding == "png":
        # PNG → NDS tile data (NCGR/NCLR/NSCR triplet)
        # Input: base64-encoded PNG, or file path on disk
        # Output: appended as 3 consecutive NARC files (tiles, palette, map)
        try:
            import base64
            from PIL import Image
            import io

            # Load image from base64 or file path
            if os.path.isfile(data):
                img = Image.open(data).convert('RGBA')
            else:
                img = Image.open(io.BytesIO(base64.b64decode(data))).convert('RGBA')

            # Quantize to 16 colors (NDS palette limit)
            # Convert RGBA to RGB for quantization, preserve alpha as transparency
            rgb = img.convert('RGB')
            quantized = rgb.quantize(colors=16, method=Image.Quantize.MEDIANCUT)
            palette_data = quantized.getpalette()[:16 * 3]  # 16 colors × RGB
            pixel_indices = list(quantized.getdata())
            w, h = img.size

            # Build NDS 15-bit palette (NCLR)
            # Format: XBBBBBGGGGGRRRRR (little-endian u16)
            nds_palette = bytearray(16 * 2)
            # Slot 0 = transparent
            for i in range(16):
                r, g, b = palette_data[i*3], palette_data[i*3+1], palette_data[i*3+2]
                r5 = (r >> 3) & 0x1F
                g5 = (g >> 3) & 0x1F
                b5 = (b >> 3) & 0x1F
                c16 = r5 | (g5 << 5) | (b5 << 10)
                struct.pack_into('<H', nds_palette, i * 2, c16)

            # Build NCLR file (palette)
            nclr_data_size = len(nds_palette)
            # NCLR header: magic(4) + BOM(2) + version(2) + filesize(4) + headersize(2) + sections(2)
            # PLTT section: magic(4) + size(4) + depth(4) + padding(4) + datasize(4) + offset(4) + data
            pltt_size = 24 + nclr_data_size
            nclr_size = 16 + pltt_size
            nclr = bytearray(nclr_size)
            struct.pack_into('<4sHHIHH', nclr, 0, b'RLCN', 0xFEFF, 0x0100, nclr_size, 16, 1)
            struct.pack_into('<4sIIIII', nclr, 16, b'TTLP', pltt_size, 4, 0, nclr_data_size, 0)
            nclr[40:40 + nclr_data_size] = nds_palette

            # Build tile data (NCGR) — 8×8 pixel tiles, 4bpp (2 pixels per byte)
            tiles_w = (w + 7) // 8
            tiles_h = (h + 7) // 8
            tile_data = bytearray(tiles_w * tiles_h * 32)  # 32 bytes per 8×8 4bpp tile
            for ty in range(tiles_h):
                for tx in range(tiles_w):
                    tile_off = (ty * tiles_w + tx) * 32
                    for py in range(8):
                        for px in range(0, 8, 2):
                            ix = tx * 8 + px
                            iy = ty * 8 + py
                            lo = pixel_indices[iy * w + ix] if ix < w and iy < h else 0
                            hi = pixel_indices[iy * w + ix + 1] if ix + 1 < w and iy < h else 0
                            lo = min(lo, 15)
                            hi = min(hi, 15)
                            tile_data[tile_off + py * 4 + px // 2] = (lo & 0xF) | ((hi & 0xF) << 4)

            # NCGR header
            char_data_size = len(tile_data)
            char_size = 32 + char_data_size
            ncgr_size = 16 + char_size
            ncgr = bytearray(ncgr_size)
            struct.pack_into('<4sHHIHH', ncgr, 0, b'RGCN', 0xFEFF, 0x0101, ncgr_size, 16, 1)
            struct.pack_into('<4sIHHIII', ncgr, 16, b'RAHC', char_size, tiles_h, tiles_w, 3, 0, char_data_size)
            ncgr[48:48 + char_data_size] = tile_data

            # Build screen map (NSCR) — sequential tile indices
            map_entries = tiles_w * tiles_h
            map_data = bytearray(map_entries * 2)
            for i in range(map_entries):
                struct.pack_into('<H', map_data, i * 2, i)  # sequential, no flip/palette

            # NSCR header
            scrn_data_size = len(map_data)
            scrn_size = 20 + scrn_data_size
            nscr_size = 16 + scrn_size
            nscr = bytearray(nscr_size)
            struct.pack_into('<4sHHIHH', nscr, 0, b'RCSN', 0xFEFF, 0x0100, nscr_size, 16, 1)
            struct.pack_into('<4sIHHI', nscr, 16, b'NRCS', scrn_size, w, h, scrn_data_size)
            nscr[36:36 + scrn_data_size] = map_data

            # Write as 3 consecutive appends to the NARC
            if ':' not in path:
                return {"error": "PNG encoding requires a NARC path (e.g. a/2/6/7:append)"}

            narc_path, idx_str = path.rsplit(':', 1)
            narc = _get_narc(narc_path.lstrip('/'))
            rom = current_rom['rom']
            base_idx = len(narc.files)

            narc.files.append(bytes(ncgr))
            narc.files.append(bytes(nclr))
            narc.files.append(bytes(nscr))
            rom.setFileByName(narc_path.lstrip('/'), narc.save())
            _invalidate_narc(narc_path.lstrip('/'))

            return {
                "converted": True, "source": "png", "image_size": f"{w}x{h}",
                "colors": 16, "tiles": f"{tiles_w}x{tiles_h}",
                "narc": narc_path,
                "ncgr_index": base_idx, "nclr_index": base_idx + 1, "nscr_index": base_idx + 2,
                "total_files": len(narc.files),
                "sizes": {"ncgr": len(ncgr), "nclr": len(nclr), "nscr": len(nscr)}
            }
        except ImportError:
            return {"error": "Pillow not installed — pip install Pillow"}
        except Exception as e:
            return {"error": f"PNG conversion failed: {e}"}

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
                if file_idx_str.lower() == 'append':
                    new_idx = len(narc.files)
                    narc.files.append(bytes(data_bytes))
                    rom.setFileByName(narc_path.lstrip('/'), narc.save())
                    _invalidate_narc(narc_path.lstrip('/'))
                    return {"appended": True, "path": path, "narc": narc_path,
                            "new_index": new_idx, "size": len(data_bytes),
                            "total_files": len(narc.files)}

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


async def record(output_path: str) -> dict:
    """Repack and save the ROM."""
    if not current_rom:
        return {"error": "No ROM currently open"}

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
        return {"error": "No ROM currently open"}

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

    if current_rom['type'] == 'nds' and path:
        rom = current_rom['rom']
        try:
            if path.lower() == 'arm9.bin':
                data = bytes(current_rom['arm9_data'])
            elif path.lower() == 'arm7.bin':
                data = bytes(current_rom['arm7_data'])
            elif _is_overlay_path(path) >= 0:
                ov_id = _is_overlay_path(path)
                overlays = current_rom.get('overlays', {})
                if ov_id not in overlays:
                    return {"error": f"Overlay {ov_id} not found (available: {sorted(overlays.keys())})"}
                data = bytes(overlays[ov_id])
            elif ':' in path:
                narc_path, file_idx = path.rsplit(':', 1)
                file_idx = int(file_idx)
                narc = _get_narc(narc_path.lstrip('/'))
                if file_idx >= len(narc.files):
                    return {"error": f"Index {file_idx} out of range (NARC has {len(narc.files)} files)"}
                data = narc.files[file_idx]
            else:
                data = rom.getFileByName(path.lstrip('/'))
        except Exception as e:
            return {"error": f"File not found: {path} ({e})"}
    else:
        with open(current_rom['path'], 'rb') as f:
            f.seek(offset)
            data = f.read(length + (1024 if search else 0))

    dump_data = data[offset:offset + length] if current_rom['type'] == 'nds' and path else data[:length]

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
    if not current_rom:
        return {"error": "No ROM currently open"}

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

    # Text table name lookup
    if name:
        query = name.lower()
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
                if exact:
                    if entry.lower() == query:
                        results.append({"table": tbl_name, "index": idx, "name": entry})
                else:
                    if query in entry.lower():
                        results.append({"table": tbl_name, "index": idx, "name": entry})
            # Auto-resolve trainer_classes hits → trdata files via class ID byte
            class_hits = [r for r in results if r.get('table') == 'trainer_classes']
            if class_hits and current_rom:
                try:
                    gc = current_rom.get('header', {}).get('game_code', '')
                    trdata_path = GAME_INFO.get(gc, {}).get('narcs', {}).get('trdata')
                    if trdata_path:
                        td_files = _get_narc(trdata_path).files
                        for ch in class_hits:
                            cid = ch['index']
                            for fi, td in enumerate(td_files):
                                if len(td) >= 2 and td[1] == cid:
                                    results.append({'table': 'trdata', 'index': fi, 'name': ch['name']})
                except Exception:
                    pass
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
            result = {"query": name, "exact": exact, "matches": results, "count": len(results)}

            # Difficulty filtering for Gen V BW2 (Challenge/Easy/Normal modes).
            # Groups trdata matches into clusters (consecutive file indices = same difficulty block),
            # then labels them by pokemon count: fewest = Easy, most = Challenge, middle = Normal.
            # No hardcoded file ranges -- derived from the data itself.
            if difficulty and gen == 5 and results:
                trdata_hits = [r for r in results if r.get('table') == 'trdata']
                other_hits  = [r for r in results if r.get('table') != 'trdata']
                if trdata_hits:
                    # Group by cluster: new cluster when gap > 20 file indices
                    trdata_hits.sort(key=lambda r: r['index'])
                    clusters = []
                    cur = [trdata_hits[0]]
                    for r in trdata_hits[1:]:
                        if r['index'] - cur[-1]['index'] <= 20:
                            cur.append(r)
                        else:
                            clusters.append(cur)
                            cur = [r]
                    clusters.append(cur)
                    # Get median npoke per cluster by probing trdata
                    try:
                        gc = current_rom.get('header', {}).get('game_code', '')
                        trdata_path = GAME_INFO.get(gc, {}).get('narcs', {}).get('trdata')
                        def _cluster_npoke(cluster):
                            files = _get_narc(trdata_path).files
                            counts = [files[r['index']][3] for r in cluster if r['index'] < len(files) and len(files[r['index']]) >= 4]
                            return sum(counts) / max(len(counts), 1)
                        scored = sorted(clusters, key=_cluster_npoke)
                        # scored[0]=fewest pokemon=Easy, scored[-1]=most=Challenge, middle=Normal
                        label_map = {'easy': 0, 'normal': len(scored)//2, 'challenge': -1}
                        pick = label_map.get(difficulty.lower())
                        if pick is not None:
                            chosen = scored[pick]
                            results = other_hits + chosen
                            result['matches'] = results
                            result['count'] = len(results)
                            result['difficulty'] = difficulty.lower()
                    except Exception:
                        pass

            # Canonical rival name lookup -- fires whenever a search matches a known
            # rival by their English default name, regardless of hit count.
            # These are player-named so they never appear in trainer_names under
            # their canonical names; searching them always surfaces the right files.
            # Maps canonical rival/special names to trainer class IDs.
            # File indices are derived at query time by scanning loaded trdata -- nothing hardcoded.
            RIVAL_LOOKUP = {
                'silver': {'canonical': 'Silver', 'class_ids': [23],
                           'note': 'Player-named rival (HGSS). Class 23 = Rival.'},
                'barry':  {'canonical': 'Barry',  'class_ids': [95, 96],
                           'note': 'Player-named rival (DP/Pt). Class 95 vs male player, 96 vs female. Not in trainer_names.'},
                'hugh':   {'canonical': 'Hugh',   'class_ids': [145],
                           'note': 'Player-named rival (BW2). Stored as placeholder in trainer_names. 3 files/encounter = one per starter; 6 files = 3 starters x 2 genders (Nate/Rosa).'},
            }
            rival = RIVAL_LOOKUP.get(query.strip().lower())
            if rival and current_rom:
                try:
                    gc = current_rom.get('header', {}).get('game_code', '')
                    trdata_path = GAME_INFO.get(gc, {}).get('narcs', {}).get('trdata')
                    if trdata_path:
                        td_files = _get_narc(trdata_path).files
                        for fi, td in enumerate(td_files):
                            if len(td) >= 2 and td[1] in rival['class_ids']:
                                results.append({'table': 'trdata', 'index': fi, 'name': rival['canonical']})
                        result['count'] = len(results)
                except Exception:
                    pass

            # If nothing found and trainer_names was in scope, check trainer_classes
            # and explain why rivals won't appear in trainer_names
            if len(results) == 0 and (not table or table == 'trainer_names'):
                if 'trainer_names' in text_tables and 'trainer_classes' in text_tables:
                    class_hits = []
                    for idx, entry in enumerate(text_tables['trainer_classes']):
                        if isinstance(entry, str) and query in entry.lower():
                            class_hits.append({"table": "trainer_classes", "index": idx, "name": entry})
                    if class_hits:
                        result['trainer_class_matches'] = class_hits
                        result['note'] = (
                            "Not found in trainer_names. Player-named rivals have no entry there "
                            "because the player sets their name at the start of the game "
                            "(this has been true since Blue/Gary in Gen 1). "
                            "They appear in trainer_classes only (shown above). "
                            "Dawn and Lucas are exceptions: they are the unchosen player character "
                            "whose name is fixed by the game, so they DO appear in trainer_names. "
                            "Rival battle trdata locations by game: "
                            "HGSS (Silver): a/0/5/5 files [1,2,3,263-272,285-289,489-491,735-737] (class 23 = Rival). "
                            "Diamond/Platinum (Barry): poketool/trainer/trdata.narc files "
                            "[613-615,621-623] vs male player, [616-618,624-626] vs female player "
                            "(classes 95/96 = blank Trainer, same indices in both games). "
                            "BW2 (Hugh): a/0/9/1 files "
                            "[161-163,166-168,368-370,378-380,588-590,684-686,693-698,701-703,794-796] "
                            "(class 145 = blank Trainer; stored as 'Rival' in trainer_names; "
                            "3 files per encounter = one per starter counter, "
                            "6 files = 3 starters x 2 player genders Nate/Rosa)."
                        )
            return result
        
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
        return {"error": "No ROM currently open"}

    if current_rom['type'] != 'nds':
        return {"error": "Diff only supported for NDS"}

    def resolve_path(p):
        """Resolve a path, handling cross-ROM prefixes."""
        gc_prefix, clean_p = _parse_rom_prefix(p)
        if gc_prefix and gc_prefix != current_rom['header']['game_code']:
            orig_gc = _switch_rom(gc_prefix)
            try:
                return _resolve_nds_path(clean_p)
            finally:
                _switch_rom(orig_gc)
        elif gc_prefix:
            p = clean_p
        return _resolve_nds_path(p)

    def _resolve_nds_path(p):
        p = p.strip('/')
        if p.lower() == 'arm9.bin':
            return bytes(current_rom['arm9_data'])
        elif p.lower() == 'arm7.bin':
            return bytes(current_rom['arm7_data'])
        elif _is_overlay_path(p) >= 0:
            ov_id = _is_overlay_path(p)
            overlays = current_rom.get('overlays', {})
            if ov_id not in overlays:
                raise ValueError(f"Overlay {ov_id} not found (available: {sorted(overlays.keys())})")
            return bytes(overlays[ov_id])
        elif ':' in p:
            narc_path, file_idx = p.rsplit(':', 1)
            file_idx = int(file_idx)
            narc = _get_narc(narc_path.lstrip('/'))
            if file_idx >= len(narc.files):
                raise ValueError(f"Index {file_idx} out of range (NARC has {len(narc.files)} files)")
            return narc.files[file_idx]
        else:
            return current_rom['rom'].getFileByName(p)

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



async def stats() -> dict:
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
            "loaded_roms": list(loaded_roms.keys()),
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
        "loaded_roms": list(loaded_roms.keys()),
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

async def list_flipnotes() -> dict:
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


async def note(path: str, description: str, name: str = None, format: str = None,
               tags: list = None, file_range: str = None, examples: list = None,
               related: list = None, game: str = None) -> dict:
    """Add a note to a Flipnote. Defaults to current ROM, or specify game code."""
    # Multi-ROM: if game specified, find that flipnote
    if game:
        fpn_path = find_flipnote(game)
        if not fpn_path:
            return {"error": f"No flipnote for game: {game}"}
        with open(fpn_path, 'r', encoding='utf-8') as f:
            fpn_data = json.load(f)
        fpn_data.setdefault('notes', {})[path] = {"description": description}
        if name: fpn_data['notes'][path]["name"] = name
        if format: fpn_data['notes'][path]["format"] = format
        if tags: fpn_data['notes'][path]["tags"] = tags
        if file_range: fpn_data['notes'][path]["file_range"] = file_range
        if examples: fpn_data['notes'][path]["examples"] = examples
        if related: fpn_data['notes'][path]["related"] = related
        with open(fpn_path, 'w', encoding='utf-8') as f:
            json.dump(fpn_data, f, indent=2, ensure_ascii=False)
        return {"noted": path, "description": description, "game": game}

    if not current_rom:
        return {"error": "No ROM currently open"}
    if not current_flipnote:
        return {"error": "No flipnote loaded"}

    fpn_data = current_flipnote['data']
    fpn_data.setdefault('notes', {})[path] = {"description": description}
    if name: fpn_data['notes'][path]["name"] = name
    if format: fpn_data['notes'][path]["format"] = format
    if tags: fpn_data['notes'][path]["tags"] = tags
    if file_range: fpn_data['notes'][path]["file_range"] = file_range
    if examples: fpn_data['notes'][path]["examples"] = examples
    if related: fpn_data['notes'][path]["related"] = related

    with open(current_flipnote['path'], 'w', encoding='utf-8') as f:
        json.dump(fpn_data, f, indent=2, ensure_ascii=False)

    # Log for future recovery
    _log_note(path=path, description=description, name=name, format=format,
              tags=tags, file_range=file_range, related=related)

    return {"noted": path, "description": description}


def _log_note(**kwargs):
    """Append a note to the persistent history. Server's own record."""
    try:
        with open(note_history, 'a', encoding='utf-8') as f:
            f.write(json.dumps({k: v for k, v in kwargs.items() if v is not None}) + '\n')
    except:
        pass


async def batch_notes(notes: list, game: str = None) -> dict:
    """Write multiple notes at once. Each note: {path, description, name?, format?, tags?}.
    Defaults to current ROM, or specify game code. Single disk write."""
    if game:
        fpn_path = find_flipnote(game)
        if not fpn_path:
            return {"error": f"No flipnote for game: {game}"}
        with open(fpn_path, 'r', encoding='utf-8') as f:
            fpn_data = json.load(f)
        target_path = fpn_path
    elif current_flipnote:
        fpn_data = current_flipnote['data']
        target_path = current_flipnote['path']
    else:
        return {"error": "No ROM open and no game specified"}

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

    with open(target_path, 'w', encoding='utf-8') as f:
        json.dump(fpn_data, f, indent=2, ensure_ascii=False)

    if not game and current_flipnote:
        current_flipnote['data'] = fpn_data

    return {"written": written, "total_notes": len(fpn_data['notes'])}


async def edit_note(path: str, description: str = None, name: str = None, format: str = None,
                    tags: list = None, file_range: str = None, examples: list = None,
                    related: list = None, game: str = None) -> dict:
    """Edit an existing note in the Flipnote."""
    if game:
        fpn_path = find_flipnote(game)
        if not fpn_path: return {"error": f"No flipnote for game: {game}"}
        with open(fpn_path, 'r', encoding='utf-8') as f: fpn_data = json.load(f)
        save_path = fpn_path
        in_memory = False
    elif current_flipnote:
        fpn_data = current_flipnote['data']
        save_path = current_flipnote['path']
        in_memory = True
    else:
        return {"error": "No ROM open and no game specified"}

    if path not in fpn_data['notes']:
        return {"error": f"Note not found: {path}"}

    if description: fpn_data['notes'][path]["description"] = description
    if name is not None: fpn_data['notes'][path]["name"] = name
    if format is not None: fpn_data['notes'][path]["format"] = format
    if tags is not None: fpn_data['notes'][path]["tags"] = tags
    if file_range is not None: fpn_data['notes'][path]["file_range"] = file_range
    if examples is not None: fpn_data['notes'][path]["examples"] = examples
    if related is not None: fpn_data['notes'][path]["related"] = related

    with open(save_path, 'w', encoding='utf-8') as f:
        json.dump(fpn_data, f, indent=2, ensure_ascii=False)
    if in_memory: current_flipnote['data'] = fpn_data
    return {"edited": path}


async def delete_note(path: str, game: str = None) -> dict:
    """Delete a note from the Flipnote."""
    if game:
        fpn_path = find_flipnote(game)
        if not fpn_path: return {"error": f"No flipnote for game: {game}"}
        with open(fpn_path, 'r', encoding='utf-8') as f: fpn_data = json.load(f)
        save_path = fpn_path
        in_memory = False
    elif current_flipnote:
        fpn_data = current_flipnote['data']
        save_path = current_flipnote['path']
        in_memory = True
    else:
        return {"error": "No ROM open and no game specified"}

    if path not in fpn_data['notes']:
        return {"error": f"Note not found: {path}"}
    del fpn_data['notes'][path]
    with open(save_path, 'w', encoding='utf-8') as f:
        json.dump(fpn_data, f, indent=2, ensure_ascii=False)
    if in_memory: current_flipnote['data'] = fpn_data
    return {"deleted": path}




async def probe(path: str, offset: int = 0, reads: str = "u16", count: int = 1,
                xor: str = None, endian: str = "little", stride: int = 0,
                base: int = 0) -> dict:
    """Structured binary read. No manual hex math needed.
    Types: u8, u16, u32, s8, s16, s32, ptr32 (follow pointer), text (decode text file).
    """
    if not current_rom:
        return {"error": "No ROM currently open"}
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
        if current_rom['type'] != 'nds':
            with open(current_rom['path'], 'rb') as f:
                data = f.read()
        elif path.lower() == 'arm9.bin':
            data = bytes(current_rom['arm9_data'])
        elif path.lower() == 'arm7.bin':
            data = bytes(current_rom['arm7_data'])
        elif _is_overlay_path(path) >= 0:
            ov_id = _is_overlay_path(path)
            overlays = current_rom.get('overlays', {})
            if ov_id not in overlays:
                return {"error": f"Overlay {ov_id} not found (available: {sorted(overlays.keys())})"}
            data = bytes(overlays[ov_id])
        elif ':' in path:
            narc_path, file_idx = path.rsplit(':', 1)
            narc = _get_narc(narc_path.lstrip('/'))
            raw = narc.files[int(file_idx)]
            data, _ = decompress_data(raw)
        else:
            raw = current_rom['rom'].getFileByName(path.lstrip('/'))
            data, _ = decompress_data(raw)
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

# ============ Server Setup ============

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    """Route tool calls to handler functions."""
    # Lazy restore: load ROMs from last session on first tool call
    if not _rom_restore_done:
        await _do_pending_restore()
    handlers = {
        "spotlight": spotlight,
        "return": return_tool,
        "summarize": summarize,
        "decipher": decipher,
        "sketch": sketch,
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

    # When decipher returns decoded strings, frame with ═══ bars
    if name == 'decipher' and isinstance(result, dict):
        game_title = current_rom['header']['game_title'] if current_rom else ''
        bar = '═' * 39

        def _difficulty_label(path_str):
            """For B2W2 trdata paths, label difficulty block by clustering trainers.
            BW1 (IRB/IRA) has no difficulty modes -- returns empty.
            Easy Mode in B2W2 is runtime level scaling on Normal data, no separate block.
            Challenge Mode is the only variant with actual separate trainer files.
            Both B2 and W2 ROMs contain both Normal and Challenge blocks.

            Hybrid approach:
            - Hardcoded table for trainers with >2 files (E4, Iris) where clustering
              cannot distinguish the Normal/Challenge axis from the Pre/Post-champion axis.
            - Exactly-2-clusters fallback for all others (gym leaders etc.).
              Memory Link / starter variants / rematch blocks produce 3+ clusters
              and correctly get no label."""
            if not current_rom or text_gen != 5:
                return ''
            try:
                gc = current_rom.get('header', {}).get('game_code', '')
                # BW1 has no difficulty modes at all
                if gc in ('IRB', 'IRA'):
                    return ''
                import re as _re
                m = _re.search(r':(\d+)$', path_str)
                if not m:
                    return ''
                file_idx = int(m.group(1))

                # Hardcoded labels for trainers whose file sets span >2 clusters.
                # E4/Champion have 4 files each: Normal, Challenge, Normal Rematch, Challenge Rematch.
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

                trdata_path = GAME_INFO.get(gc, {}).get('narcs', {}).get('trdata')
                if not trdata_path:
                    return ''
                td_files = _get_narc(trdata_path).files
                if file_idx >= len(td_files) or len(td_files[file_idx]) < 2:
                    return ''
                this_class = td_files[file_idx][1]
                # Collect all files with the same trainer class, cluster by proximity
                same_class = [(i, td_files[i][3]) for i in range(len(td_files))
                              if len(td_files[i]) >= 4 and td_files[i][1] == this_class]
                if len(same_class) < 2:
                    return ''
                same_class.sort()
                clusters, cur = [], [same_class[0]]
                for item in same_class[1:]:
                    if item[0] - cur[-1][0] <= 20:
                        cur.append(item)
                    else:
                        clusters.append(cur)
                        cur = [item]
                clusters.append(cur)
                # Only label when exactly 2 clusters exist (Normal + Challenge).
                # 3+ clusters = Memory Link / rematch tiers / starter variants -- ambiguous, skip.
                if len(clusters) != 2:
                    return ''
                # Sort by avg npoke: lower = Normal, higher = Challenge
                # Easy Mode has no separate block (runtime level scaling only)
                def avg_npoke(cl): return sum(x[1] for x in cl) / len(cl)
                scored = sorted(clusters, key=avg_npoke)
                labels = ['Normal Mode', 'Challenge Mode']
                for rank, cl in enumerate(scored):
                    if any(x[0] == file_idx for x in cl):
                        return labels[rank]
            except Exception:
                pass
            return ''

        def _frame(decoded_str, path_str):
            """Wrap decoded text in ═══ frame with game title and path."""
            lines = decoded_str.split('\n', 1)
            title = lines[0]
            body = lines[1] if len(lines) > 1 else ''
            diff = _difficulty_label(path_str)
            diff_tag = f' [{diff}]' if diff else ''
            header = f"{bar}\n{title}{diff_tag}\n{game_title} | {path_str}\n{bar}"
            if body:
                return f"{header}\n\n{body}\n{bar}"
            return f"{header}\n{bar}"

        # Multi-file reads
        if result.get('multi') and isinstance(result.get('results'), list):
            blocks = []
            for sub in result['results']:
                if not isinstance(sub, dict):
                    blocks.append(TextContent(type="text", text=str(sub)))
                    continue
                decoded = sub.get('decoded')
                path_str = sub.get('path', '')
                if isinstance(decoded, str):
                    blocks.append(TextContent(type="text", text=_frame(decoded, path_str)))
                else:
                    summary = {"path": path_str, "size": sub.get("size", "?")}
                    if sub.get("hex"): summary["hex"] = sub["hex"]
                    if sub.get("hex_note"): summary["hex_note"] = sub["hex_note"]
                    if sub.get("error"): summary["error"] = sub["error"]
                    blocks.append(TextContent(type="text", text=json.dumps(summary, indent=2)))
            return blocks
        # Single file
        decoded = result.get('decoded')
        if isinstance(decoded, str):
            return [TextContent(type="text", text=_frame(decoded, result.get('path', '')))]

    # Format result as readable text instead of raw JSON
    if isinstance(result, dict):
        if result.get('_card'):
            return [TextContent(type="text", text=result['text'])]
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
        Tool(name="sketch", description="Write bytes to a file. Writes in-place to the loaded ROM (not disk) — call record to persist. NARC append: use ':append' as index (e.g. 'a/2/6/7:append'). PNG sprite import: encoding='png', data=base64 or file path — auto-converts to NCGR/NCLR/NSCR triplet and appends to NARC.", inputSchema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path. Use ':append' to add new file to NARC (e.g. a/2/6/7:append)"},
                "data": {"type": "string", "description": "Data to write. Hex by default. For png encoding: base64 string or file path on disk."},
                "offset": {"type": "integer", "description": "Byte offset to write at (default: 0)"},
                "encoding": {"type": "string", "enum": ["hex", "utf8", "utf16le", "ascii", "png"], "description": "Encoding. 'png' converts image to NDS tile format (NCGR/NCLR/NSCR)."}
            },
            "required": ["path", "data"]
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
        Tool(name="dowse", description="Three modes: (1) name lookup — find species/move/item/trainer/location in text tables, returns file indices; if no text hit, falls back to NARC role/category search (e.g. name='encounters' or name='trdata'). (2) name+narc_path — find NARCs containing that entity as a u16 reference. (3) hex+narc_path — find files in a NARC containing a byte pattern.", inputSchema={
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
            await server.run(read_stream, write_stream, server.create_initialization_options())

    asyncio.run(main())

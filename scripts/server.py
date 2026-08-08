#!/usr/bin/env python3
"""
Silphéon
A MCP Server that helps to facilitate the exploration of the entire Pokémon mainline series of games through Claude.
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


# Import setup_tools but call inside main() after stdio is captured
from setup_tools import setup_tools, get_tool_path



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
from leswitch import open_rom as open_switch_rom
from xoleon import (
    read_3ds_header, open_3ds_romfs, read_garc_sub, read_garc_all,
    decompress_lz11, compress_lz11,
    encode_gen4_text, encode_gen5_text,
    decode_gen4_text, decode_gen5_text,
    derive_gen5_mult as derive_gen5_mult,
)


# ============ Game spec classes (SDK inheritance chain) ============
from Generations.sdk import SDK
from Generations.Kanto_rbg import Kanto_rbg
from Generations.Kanto_yellow import Kanto_yellow
from Generations.Johto_gs import Johto_gs
from Generations.Johto_crystal import Johto_crystal
from Generations.Hoenn_rse import Hoenn_rse
from Generations.Sinnoh_dp import Sinnoh_dp
from Generations.Sinnoh_pt import Sinnoh_pt
from Generations.Johto_remake import Johto_remake
from Generations.Unova_prequel import Unova_prequel
from Generations.Unova_sequel import Unova_sequel
from Generations.Kalos_prequel import Kalos_prequel
from Generations.Hoenn_remake import Hoenn_remake
from Generations.Alola_sm import Alola_sm
from Generations.Alola_usum import Alola_usum

# ── Gen-specific functions (from class modules, not old gen files) ──
# Kanto_rbg functions accessed via spec/class
# Johto_gs functions accessed via spec/class
# Hoenn_rse functions accessed via spec/class
# Johto_remake functions accessed via spec/class
# Unova_sequel functions accessed via Unova_sequel.METHOD()






server = Server("silphéon")

# State
current_rom = None
_user_active_gc = None   # Game code of the ROM the user most recently spotlighted (not changed by BFS)
current_flipnote = None
loaded_roms = {}   # game_code -> saved state for multi-ROM support
_rom_restore_in_progress = False  # Guards against concurrent restore runs
_rom_restore_done = False         # Set True after first restore attempt completes
_restore_task = None               # Background task handle for ROM restore
_startup_log = []                  # Collects restore/BFS messages for the model to see



def _rom_is_fully_loaded(gc: str) -> bool:
    """True if this game code has a live ndspy ROM object (not just registry metadata)."""
    if current_rom and getattr(current_rom, 'header', {}).get('game_code') == gc:
        return getattr(current_rom, 'rom', None) is not None
    if gc not in loaded_roms:
        return False
    spec = loaded_roms[gc]
    rom_obj = spec.rom if hasattr(spec, 'rom') else None
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
        reg_path = Path.home() / ".silphéon" / "last_rom.json"
        if not reg_path.exists():
            return
        registry = json.loads(reg_path.read_text(encoding='utf-8'))
        if 'game_code' in registry:
            registry = {registry['game_code']: registry['path']}

        async def _restore_one(gc, rom_path):
            global current_rom
            if _rom_is_fully_loaded(gc):
                return
            if not rom_path or not Path(rom_path).exists():
                print(f"[Silphéon] Registry ROM not found, skipping: {gc} → {rom_path}", file=sys.stderr, flush=True)
                return
            try:
                await spotlight(rom_path)
                print(f"[Silphéon] Auto-restored ROM: {gc}", file=sys.stderr, flush=True)
                _startup_log.append(f"Restored: {gc}")
            except Exception as e:
                print(f"[Silphéon] Failed to restore {gc}: {e}", file=sys.stderr, flush=True)
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
            rom_type = getattr(loaded_roms.get(gc), 'rom', {}).get('type', 'nds') if gc in loaded_roms else 'nds'
            if rom_type != 'nds':
                _startup_log.append(f"Skipped BFS for {gc} (non-NDS)")
                continue
            try:
                if gc in loaded_roms:
                    current_rom = loaded_roms[gc]
                loop = _asyncio.get_running_loop()
                await loop.run_in_executor(None, lambda g=gc: _build_eonet(g))
                _startup_log.append(f"BFS complete for {gc}")
            except Exception as e:
                import traceback
                err_msg = f"[Silphéon] BFS failed for {gc}: {e}\n{traceback.format_exc()}"
                print(err_msg, file=sys.stderr, flush=True)
                try:
                    with open(str(Path.home() / ".silphéon" / "bfs_error.log"), "a") as _ef:
                        _ef.write(err_msg + "\n")
                except: pass
                _startup_log.append(f"BFS FAILED for {gc}: {e}")
    except Exception as e:
        import traceback
        err_msg = f"[Silphéon] Registry restore error: {e}\n{traceback.format_exc()}"
        print(err_msg, file=sys.stderr, flush=True)
        try:
            with open(str(Path.home() / ".silphéon" / "bfs_error.log"), "a") as _ef:
                _ef.write(err_msg + "\n")
        except: pass
    finally:
        _rom_restore_in_progress = False
        _rom_restore_done = True


def _parse_rom_prefix(path: str):
    """Parse optional game-code prefix from path. 'IRE:a/0/1/6:1' -> ('IRE', 'a/0/1/6:1').
    Handles both 3-char (NDS) and 4-char (GBA) game codes."""
    for code_len in (4, 3):
        if len(path) > code_len and path[code_len] == ':':
            candidate = path[:code_len]
            if candidate.isalpha() and candidate.isupper():
                if candidate in loaded_roms or (current_rom and current_rom.header['game_code'] == candidate):
                    return candidate, path[code_len + 1:]
    return None, path


def _switch_rom(game_code: str):
    """Switch active ROM context. Returns original game_code for switching back."""
    global current_rom
    orig = current_rom.header['game_code'] if current_rom else None
    if orig == game_code:
        return orig
    if game_code in loaded_roms:
        current_rom = loaded_roms[game_code]
    return orig







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

    Scans every .jsonl in the project's .claude directory for mcp__silphéon__note (and legacy mcp__silphéon__note)
    and mcp__silphéon__batch_notes tool calls. Writes each note only to the
    flipnote(s) it actually belongs to, based on path heuristics and explicit
    game= fields. Never writes ICR-sourced notes into flipnotes.

    This runs on server startup.
    """
    note_history = Path.home() / ".silphéon" / "note_history.jsonl"
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
                        if 'mcp__silphéon__note' not in line and 'mcp__silphéon__note' not in line:
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
                            if bname in ('mcp__silphéon__note', 'mcp__silphéon__note'):
                                path = inp.get('path')
                                if path and inp.get('description'):
                                    seen_notes[path] = inp
                            elif bname in ('mcp__silphéon__batch_notes', 'mcp__silphéon__batch_notes'):
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
    for fpn_file in (Path.home() / '.silphéon' / 'flipnotes').glob("*.fpn"):
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
    """Merge notes from individual ROM flipnotes into shared partner Flipnotes.

    Example: If Diamond.fpn and Pokémon_Diamond_&_Pearl.fpn both exist, then
    Diamond's notes consolidate into the former, allowing individual Flipnotes (if they exist) to be cleaned up.
    """
    # Map each game code to its flipnote file
    code_to_fpn = {}
    for fpn_file in (Path.home() / '.silphéon' / 'flipnotes').glob("*.fpn"):
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

    # For each set of game codes, find the shared Flipnote (if it exists) and merge existing individuals into it, otherwise create the shared Flipnote.
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
    elif ext in ('.xci', '.nsp'):
        return 'switch'
    return 'unknown'



def read_switch_header(path: str) -> dict:
    """Open Switch ROM, fingerprint game code by checking spec PERSONAL_PATHs against file listing."""
    sw = open_switch_rom(path)
    files = sw.files
    # Walk all SDK subclasses that have CONTAINER indicating Switch
    def _all_specs(cls):
        for sub in cls.__subclasses__():
            yield sub
            yield from _all_specs(sub)
    from Generations.sdk import SDK
    for cls in _all_specs(SDK):
        pp = getattr(cls, 'PERSONAL_PATH', None)
        if not pp:
            continue
        if pp in files or any(f.startswith(pp + '/') for f in files):
            gc = cls.GAME_CODES[0]
            title = cls.TITLES[0]
            return {'game_code': gc, 'game_title': title, 'region': 'World',
                    'region_char': 'W', 'is_english': True, '_switch_rom': sw}
    sw.close()
    raise ValueError('Could not identify Switch ROM from file listing')

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


# Shared flipnotes — build from class chain, no merged dict needed
def _build_flipnote_pairs():
    pairs = {}
    def walk(cls):
        fp = cls.__dict__.get('FLIPNOTE_PAIRS')
        if fp:
            pairs.update(fp)
        for sub in cls.__subclasses__():
            walk(sub)
    walk(SDK)
    return pairs

FLIPNOTE_PAIRS = _build_flipnote_pairs()

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
    _s = SDK.get_spec(game_code)
    folder_name = shared or (_s.TITLES[_s.GAME_CODES.index(game_code)] if _s and game_code in _s.GAME_CODES else game_code)
    folder_name = folder_name.replace('/', '_').replace(':', '_')
    folder = sprites_dir / folder_name
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def find_flipnote(game_code: str) -> Optional[Path]:
    """Find existing flipnote by game code (checks shared partners too)."""
    partners = set(get_partner_codes(game_code))
    for fpn in (Path.home() / '.silphéon' / 'flipnotes').glob("*.fpn"):
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
    shared_path = Path.home() / '.silphéon' / 'flipnotes' / f"{safe_name}.fpn"

    # Collect ALL existing flipnotes for any partner code
    found = []
    for fpn in (Path.home() / '.silphéon' / 'flipnotes').glob("*.fpn"):
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

    path = Path.home() / '.silphéon' / 'flipnotes' / filename

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

    if compression == 'lz11':
        try:
            return decompress_lz11(data), 'lz11'
        except Exception:
            return data, compression

    tool_map = {
        'lz10': 'lzss', 'lz40': 'lzx',
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


# Species reference — verified against pokegreen disassembly data/pokemon/names.asm
# 190 internal slots (0-189); けつばん = MissingNo placeholder
# Gen I internal order → JP species names (index = game constant, 0-189)

# EOS bytes per platform


# AI Flags for Gen IV/V trainers


def _read_3ds_exefs_code(path: str) -> bytes:
    """Read the .code section from a 3DS ROM's ExeFS. Returns bytes or None."""
    try:
        f = open(path, 'rb')
        # NCSD partition 0 offset (media units = 0x200 bytes)
        f.seek(0x120)
        part0_off = struct.unpack('<I', f.read(4))[0] * 0x200
        # ExeFS offset from NCCH header (+0x1A0)
        f.seek(part0_off + 0x1A0)
        exefs_off = struct.unpack('<I', f.read(4))[0] * 0x200
        exefs_abs = part0_off + exefs_off
        # ExeFS header: first entry is .code (name[8] + offset[4] + size[4])
        f.seek(exefs_abs)
        name = f.read(8).rstrip(b'\x00')
        off = struct.unpack('<I', f.read(4))[0]
        size = struct.unpack('<I', f.read(4))[0]
        if name != b'.code' or size == 0:
            f.close()
            return None
        f.seek(exefs_abs + 0x200 + off)
        data = f.read(size)
        f.close()
        return data
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


# ============ Tool Handlers ============

async def spotlight(path: str) -> dict:
    """Open a ROM file for exploration. Multiple ROMs can be open simultaneously."""
    global current_rom

    rom_type = detect_rom_type(path)

    # Peek at header to check if already loaded
    if rom_type == 'nds':
        header = read_nds_header(path)
    elif rom_type == '3ds':
        header = read_3ds_header(path)
    elif rom_type in ('gba', 'gbc', 'gb'):
        header = read_gba_header(path) if rom_type == 'gba' else read_gb_header(path)
    elif rom_type == 'switch':
        header = read_switch_header(path)
    else:
        return {"error": f"Unknown ROM type: {path}"}

    gc = header['game_code']
    spec_class = SDK.get_spec(gc)
    spec = spec_class() if spec_class else None

    # Build backward-compat dicts from spec (bridges until all callers use spec directly)
    if spec:
        _narcs = SDK.spec_narcs(spec)
        _title = spec.TITLES[spec.GAME_CODES.index(gc)] if gc in spec.GAME_CODES else gc
        _game_info = {'title': _title, 'gen': getattr(spec, 'GEN', None),
                      'platform': getattr(spec, 'PLATFORM', 'Unknown'),
                      'year': getattr(spec, 'YEAR', None), 'narcs': _narcs}
    else:
        _narcs = {}
        _game_info = {}

    # Already loaded? Switch to it — but still show the full summary card
    if gc in loaded_roms:
        spec = loaded_roms[gc]
        current_rom = spec
        # Run any post-load scans that may not have existed when ROM was first loaded
        if gc in ('IRE', 'IRD') and 'tournament_classes' not in current_rom.text_tables:
            Unova_sequel.SCAN_PWT_TOURNAMENTS(current_rom.text_tables)
            Unova_sequel.BUILD_PWT_MAPS(current_rom.text_tables)
        game_info = _game_info
        narcs = _narcs
        icr_done = bool(eonet_labels.get(gc))
        text = _build_spotlight_text(
            gc, header, rom_type, game_info, narcs,
            {'status': 'ok', 'gen': current_rom.GEN,
             'detected': {k: True for k, v in current_rom.text_tables.items() if isinstance(v, list) and isinstance(k, str)}},
            current_flipnote['path'],
            spec.tm_table or [],
            list(loaded_roms.keys()), icr_done
        )
        _user_active_gc = gc
        return text

    # Save current ROM state before loading new one

    text_table_result = {}

    if rom_type == 'nds':
        rom = ndspy.rom.NintendoDSRom.fromFile(path)

        fpn_path = find_flipnote(gc)
        if fpn_path:
            fpn_path = upgrade_to_shared_flipnote(gc)
        else:
            structure, rom_stats = build_nds_structure(rom, path)
            fpn_path = create_flipnote(
                gc, (_game_info.get('title') or header['game_title']).replace(' Nintendo','').replace(' Game Freak','').strip(), header['region'],
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

        spec.rom_type = 'nds'
        spec.rom_path = path
        spec.rom = rom
        spec.header = header
        spec.arm9_data = arm9_data
        spec.arm7_data = arm7_data
        spec.overlays = overlays
        spec.compression_state = {}
        current_rom = spec

        # Pre-set current_rom.GEN before bootstrapping text tables
        game_info = _game_info

        # Bootstrap text tables via spec instance
        try:
            narc_data = rom.getFileByName(spec.TEXT_PATH) if hasattr(spec, 'TEXT_PATH') and spec.TEXT_PATH else None
            if narc_data:
                spec.text_narc = ndspy.narc.NARC(narc_data)
                spec.bootstrap_text(spec.text_narc.files)
                text_table_result = {"status": "ok", "gen": spec.GEN, "file_count": len(spec.text_narc.files) if narc_data else 0,
                                     "detected": {k: True for k in spec.text_tables if isinstance(k, str)}}
        except Exception as e:
            text_table_result = {"error": str(e)}

        # Discover TM→move table from ARM9
        arm9 = getattr(current_rom, 'arm9_data', b'')
        if arm9:
            spec.tm_table = spec.discover_tm_table(bytes(arm9))
            if spec.tm_table:
                text_table_result["tm_table"] = f"{len(spec.tm_table)} TM/HM entries found"

        # Encounter→Location mapping via spec method
        if hasattr(spec, 'discover_enc_loc'):
            arm9 = getattr(current_rom, 'arm9_data', b'')
            if gc in ('IPK', 'IPG'):
                spec.enc_loc = spec.discover_enc_loc(arm9)
            elif gc == 'CPU':
                enc_path = _narcs.get('encounter', '')
                try:
                    enc_count = len(current_rom.get_narc(enc_path).files) if enc_path else 0
                except:
                    enc_count = 183
                spec.enc_loc = spec.discover_enc_loc(arm9, enc_count)
            elif gc in ('IRB', 'IRA', 'IRE', 'IRD'):
                enc_path = _narcs.get('encounter', '')
                spec.enc_loc = spec.discover_enc_loc(current_rom.rom, enc_path)

        # Seed current_rom.narc_roles from spec paths so decoders work before BFS completes
        for role, narc_path in _narcs.items():
            if role != 'text' and narc_path not in current_rom.narc_roles:
                current_rom.narc_roles[narc_path] = role

    elif rom_type == '3ds':
        fh, romfs_files = open_3ds_romfs(path)
        # Read ExeFS .code for TM table discovery
        code_data = _read_3ds_exefs_code(path)

        spec.rom_type = '3ds'
        spec.rom_path = path
        spec.header = header
        spec.romfs_fh = fh
        spec.romfs_files = romfs_files
        spec.code_data = code_data
        spec.compression_state = {}
        current_rom = spec

        fpn_path = find_flipnote(gc)
        if fpn_path:
            fpn_path = upgrade_to_shared_flipnote(gc)
        else:
            structure, rom_stats = build_3ds_structure(romfs_files, fh, path)
            fpn_path = create_flipnote(
                gc, (_game_info.get('title') or header['game_title']).replace(' Nintendo','').replace(' Game Freak','').strip(),
                header['region'], header['region_char'], structure, rom_stats, header.get('is_english', True)
            )

        game_info = _game_info
        narcs = _narcs

        # Bootstrap text via spec instance
        text_garc = narcs.get('text', '')
        if text_garc and text_garc in romfs_files:
            garc_files = read_garc_all(fh, romfs_files[text_garc][0])
            try:
                spec.bootstrap_text(garc_files)
                text_table_result = {"status": "ok", "gen": spec.GEN, "file_count": len(garc_files),
                                     "detected": {k: True for k in spec.text_tables if isinstance(k, str)}}
            except Exception as e:
                text_table_result = {"error": str(e)}

        for role, np in _narcs.items():
            if role != 'text' and np not in current_rom.narc_roles:
                current_rom.narc_roles[np] = role

        # Discover TM table from ExeFS .code
        if code_data:
            spec.tm_table = spec.discover_tm_table(code_data)
            if spec.tm_table:
                text_table_result['tm_count'] = len(spec.tm_table)

    elif rom_type == 'switch':
        sw = header.pop('_switch_rom')  # LéSwitch.SwitchROM from read_switch_header

        spec.rom_type = 'switch'
        spec.rom_path = path
        spec.header = header
        spec.switch_rom = sw
        spec.romfs_files = sw.files  # {path: (offset, size)} — same interface as 3DS
        spec.compression_state = {}
        current_rom = spec

        fpn_path = find_flipnote(gc)
        if fpn_path:
            fpn_path = upgrade_to_shared_flipnote(gc)
        else:
            structure = {'files': sorted(sw.files.keys())[:200]}
            rom_stats = {'file_count': len(sw.files), 'rom_type': 'switch'}
            fpn_path = create_flipnote(
                gc, (_game_info.get('title') or header['game_title']).strip(),
                header['region'], header['region_char'], structure, rom_stats, header.get('is_english', True)
            )

        game_info = _game_info
        narcs = _narcs

        # Bootstrap text — same Gen V cipher (0x2983) all the way through Switch
        text_path = narcs.get('text', '')
        if text_path:
            # Read all files under text path
            text_files = []
            for fpath in sorted(sw.files.keys()):
                if fpath.startswith(text_path):
                    text_files.append(sw.read_file(fpath))
            if text_files:
                spec.bootstrap_text(text_files)
                text_table_result = {"status": "ok", "gen": spec.GEN, "file_count": len(text_files),
                                     "detected": {k: True for k in spec.text_tables if isinstance(k, str)}}

        for role, np in _narcs.items():
            if role != 'text' and np not in current_rom.narc_roles:
                current_rom.narc_roles[np] = role

    else:  # gba/gbc/gb
        spec.tm_table = []  # GB/GBC/GBA has no ARM9 TM table
        # Load raw ROM binary into memory
        with open(path, 'rb') as _f:
            rom_data = bytearray(_f.read())

        spec.rom_type = rom_type
        spec.rom_path = path
        spec.header = header
        spec.rom = rom_data
        current_rom = spec

        fpn_path = find_flipnote(gc)
        if not fpn_path:
            fpn_path = create_flipnote(
                gc, (_game_info.get('title') or header['game_title']).replace(' Nintendo','').replace(' Game Freak','').strip(), header['region'],
                header['region_char'], [], {}, header.get('is_english', True)
            )

        # Bootstrap text via spec instance
        try:
            spec.bootstrap_text(bytes(rom_data), region=header.get('region', 'US'))
            text_table_result = {"status": "ok", "gen": spec.GEN,
                                 "detected": {k: True for k in spec.text_tables if isinstance(k, str)}}
        except Exception as e:
            text_table_result = {"error": str(e), "gen": 0}

        # Discover TM/HM count.
        # Gen 2/3: item names table has "TM01"-"TM50" and "HM01"-"HM07" as entries.
        # Gen 1: TMs are not named in item table (generated dynamically by game).
        #        Counts from pret/pokered: NUM_TMS=50, NUM_HMS=5.
        it_list = spec.text_tables.get('items', [])
        n_tm = sum(1 for name in it_list if isinstance(name, str) and name.upper().startswith('TM'))
        n_hm = sum(1 for name in it_list if isinstance(name, str) and name.upper().startswith('HM'))
        if not n_tm:
            if spec.GEN == 1:
                n_tm, n_hm = 50, 5   # pret/pokered: NUM_TMS=50, NUM_HMS=5
            elif spec.GEN == 2:
                n_tm, n_hm = 50, 7   # pret/pokegold: 50 TMs, HM01-HM07
        if n_tm or n_hm:
            spec.tm_table = [('TM', 0)] * n_tm + [('HM', 0)] * n_hm

        # PWT tournament participant scan (B2W2 only — no-op for other games)
        if rom_type == 'nds' and gc in ('IRE', 'IRD'):
            Unova_sequel.SCAN_PWT_TOURNAMENTS(current_rom.text_tables)
            Unova_sequel.BUILD_PWT_MAPS(current_rom.text_tables)

        # Discover data table offsets from the ROM itself (no hardcoding)
        if rom_type == 'gba':
            gen3_offsets = Hoenn_rse.DISCOVER_TABLES()
            if gen3_offsets:
                text_table_result["gen3_tables"] = f"personal@0x{gen3_offsets.get('personal_base',0):X} move@0x{gen3_offsets.get('move_base',0):X}"
        elif rom_type in ('gb', 'gbc'):
            gen1_offsets = Kanto_rbg.DISCOVER_TABLES()
            if gen1_offsets:
                text_table_result["gen1_tables"] = (
                    f"personal@0x{gen1_offsets.get('personal_base',0):X} "
                    f"dex_table@0x{gen1_offsets.get('dex_table_base',0):X} "
                    f"trainer_ptr@0x{gen1_offsets.get('trainer_class_ptr_table',0):X} "
                    f"brock=class[{gen1_offsets.get('gym_brock_class','?')}]"
                )

    with open(fpn_path, 'r', encoding='utf-8') as f:
        current_flipnote = {'path': str(fpn_path), 'data': json.load(f)}

    # Store spec instance in loaded_roms (source of truth)
    loaded_roms[gc] = spec

    # Persist opened ROM registry for auto-restore on startup
    try:
        last_rom_file = Path.home() / ".silphéon" / "last_rom.json"
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

    # Populate current_rom.narc_roles from spec paths — known paths available before BFS.
    if current_rom and current_rom.rom_type in ('nds', '3ds', 'switch'):
        for _role, _path in _narcs.items():
            if _role != 'text':
                current_rom.narc_roles[_path] = _role

    # Build Eonet in background for interactive spotlight calls only.
    # During restore, _do_pending_restore runs BFS sequentially — skip here to avoid double-BFS race.
    if current_rom and current_rom.rom_type in ('nds', '3ds', 'switch') and not _rom_restore_in_progress:
        import asyncio as _asyncio
        _gc_capture = gc
        try:
            loop = _asyncio.get_running_loop()
            loop.run_in_executor(None, lambda: _build_eonet(_gc_capture))
        except RuntimeError:
            _build_eonet(_gc_capture)

    # Build clean summary card
    game_info = _game_info
    narcs = _narcs
    icr_done = bool(eonet_labels.get(gc))
    text = _build_spotlight_text(
        gc, header, rom_type, game_info, narcs,
        text_table_result, fpn_path, spec.tm_table or [],
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
    lines.append(f'Note: This game\'s flipnote can be found in ./Silphéon/flipnotes at: {rel}\n')

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

    gc = current_rom.header['game_code']

    if save and current_rom.rom_type == 'nds':
        try:
            result = await record(current_rom.rom_path)
            if 'error' in result:
                return result
        except Exception as e:
            return {"error": f"Failed to save ROM: {e}"}

    result = {"closed": current_rom.header['game_title']}
    if save:
        result["saved"] = True
    if current_rom._text_modified:
        result["text_saved"] = sorted(current_rom._text_modified)

    # Remove from loaded_roms and clear its NARC cache
    loaded_roms.pop(gc, None)
    # NARC cache lives on the spec — cleared when spec is removed from loaded_roms

    # Switch to another loaded ROM if available
    if loaded_roms:
        next_gc = next(iter(loaded_roms))
        _switch_rom(next_gc)
        result["switched_to"] = next_gc
        result["loaded"] = list(loaded_roms.keys())

    return result


def _summarize_3ds(path: str, expand_narcs: bool = False) -> dict:
    """Summarize 3DS RomFS filesystem at a path."""
    fs = current_rom.romfs_files
    fh = current_rom.romfs_fh
    contents = []

    clean = path.strip('/')

    # Drill into a specific GARC (e.g. "a/0/1/7")
    if clean and clean in fs:
        abs_off = fs[clean][0]
        try:
            garc_files = read_garc_all(fh, abs_off)
            role = current_rom.narc_roles.get(clean)
            gc = current_rom.header['game_code']
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
                role = current_rom.narc_roles.get(fpath)
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

    if current_rom.rom_type in ('3ds', 'switch'):
        return _summarize_3ds(path, expand_narcs)

    if current_rom.rom_type not in ('nds',):
        return {"path": path, "contents": [], "note": "No filesystem for GB/GBA ROMs"}

    rom = current_rom.rom
    contents = []

    # Check if path is a NARC file
    clean_path = path.strip('/')
    if clean_path and not clean_path.endswith('/'):
        # Check for overlay path
        ov_id = _is_overlay_path(clean_path)
        if ov_id >= 0:
            overlays = getattr(current_rom, 'overlays', {})
            if ov_id in overlays:
                data = overlays[ov_id]
                return {"path": clean_path, "type": "overlay", "size": len(data),
                        "overlay_id": ov_id}
            else:
                return {"error": f"Overlay {ov_id} not found (available: {sorted(overlays.keys())})"}
        try:
            file_data = rom.getFileByName(clean_path)
            if file_data[:4] == b'NARC':
                narc = current_rom.get_narc(clean_path)
                gc = current_rom.header['game_code']
                narc_lbl = eonet_labels.get(gc, {}).get(clean_path, {}).get('labels', {})
                narc_role = current_rom.narc_roles.get(clean_path)
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
            contents.append({"name": "arm9.bin", "type": "binary", "size": len(current_rom.arm9_data)})
            contents.append({"name": "arm7.bin", "type": "binary", "size": len(current_rom.arm7_data)})
            overlays = getattr(current_rom, 'overlays', {})
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
                    narc = current_rom.get_narc(full_path)
                    entry["file_count"] = len(narc.files)
                except:
                    pass
                role = current_rom.narc_roles.get(full_path)
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
    _gc = _user_active_gc or (current_rom.header['game_code'] if current_rom else None)
    if _gc and _gc in loaded_roms and current_rom.header['game_code'] != _gc:
        _switch_rom(_gc)

    # Cross-ROM prefix: "IRE:a/0/1/6:1" or "BPRE:trainer:BROCK"
    gc_prefix, clean_path = _parse_rom_prefix(path)
    if gc_prefix and gc_prefix != current_rom.header['game_code']:
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

    if current_rom.rom_type in ('nds', '3ds', 'switch'):
        try:
            data = current_rom.read_file(path)
            compression = 'none'
            # NARC internals and named files may be compressed; arm9/arm7/overlays are pre-decompressed
            if decompress and path.lower() not in ('arm9.bin', 'arm7.bin') and _is_overlay_path(path) < 0:
                data, compression = decompress_data(data)
                if compression != 'none':
                    current_rom.compression_state[path] = compression

            if length:
                data = data[offset:offset + length]
            elif offset:
                data = data[offset:]
            # Route through spec instance
            gc = current_rom.header['game_code']
            spec = loaded_roms.get(gc)
            narc_path = path.split(':')[0] if ':' in path else path
            file_idx = int(path.split(':')[1]) if ':' in path and path.split(':')[1].isdigit() else 0
            role = current_rom.narc_roles.get(narc_path)
            if not role:  # prefix match for folder-based systems (donut, dim, etc.)
                for known, r in current_rom.narc_roles.items():
                    if narc_path.startswith(known + '/'):
                        role = r
                        break
            decoded = spec.decode(role, data, file_idx, path=narc_path) if spec and role else None
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

    elif current_rom.rom_type in ('gba', 'gb', 'gbc'):
        gc = current_rom.header['game_code']
        spec = loaded_roms.get(gc)
        role = path.split(':')[0] if ':' in path else path
        key = path.split(':')[1].strip() if ':' in path else '0'
        decoded = spec.decode(role, b'', int(key) if key.isdigit() else 0) if spec else None
        if isinstance(decoded, str):
            return _frame(decoded, path, gc)
        if isinstance(decoded, dict):
            if decoded.get('_card') and isinstance(decoded.get('text'), str):
                return _frame(decoded['text'], path, gc)
            if decoded.get('_unknown'):
                return f"not decoded: {decoded.get('reason', '?')}"
        return str(decoded) if decoded else f"No data for {path}"
    else:
        with open(current_rom.rom_path, 'rb') as f:
            f.seek(offset)
            data = f.read(length) if length else f.read()
        return f"offset: {offset}  size: {len(data)}\n{_format_hex(data, offset)}"


async def sketch(path: str, data: str, offset: int = 0, encoding: str = "hex") -> dict:
    """Write data to a file."""
    if not current_rom:
        return "Error: No ROM currently open"

    # Ensure globals match the user's active ROM
    global _user_active_gc
    _gc = _user_active_gc or (current_rom.header['game_code'] if current_rom else None)
    if _gc and _gc in loaded_roms and current_rom.header['game_code'] != _gc:
        _switch_rom(_gc)

    # Text edit: update entries in current_rom.text_tables, encoding deferred to record/return
    if path.isdigit() and current_rom.text_narc:
        fidx = int(path)
        if fidx not in current_rom.text_tables:
            return {"error": f"Text file {fidx} not loaded"}
        entries = data.split('; ')
        for i, val in enumerate(entries):
            current_rom.text_tables[fidx][offset + i] = val
        current_rom._text_modified.add(fidx)
        return {"written": len(entries), "file": fidx, "from": offset}

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

    if current_rom.rom_type == 'nds':
        rom = current_rom.rom

        try:
            if path.lower() == 'arm9.bin':
                current_rom.arm9_data[offset:offset + len(data_bytes)] = data_bytes
                return {"written": len(data_bytes), "path": path, "offset": offset}
            elif path.lower() == 'arm7.bin':
                current_rom.arm7_data[offset:offset + len(data_bytes)] = data_bytes
                return {"written": len(data_bytes), "path": path, "offset": offset}
            elif _is_overlay_path(path) >= 0:
                ov_id = _is_overlay_path(path)
                overlays = getattr(current_rom, 'overlays', {})
                if ov_id not in overlays:
                    return {"error": f"Overlay {ov_id} not found (available: {sorted(overlays.keys())})"}
                overlays[ov_id][offset:offset + len(data_bytes)] = data_bytes
                return {"written": len(data_bytes), "path": path, "offset": offset, "overlay_id": ov_id}

            if ':' in path:
                narc_path, file_idx_str = path.rsplit(':', 1)
                narc = current_rom.get_narc(narc_path.lstrip('/'))

                # NARC append mode: sketch("a/0/5/5:append", data)
                file_idx = int(file_idx_str)
                current_file = bytearray(narc.files[file_idx])
                current_file[offset:offset + len(data_bytes)] = data_bytes
                narc.files[file_idx] = bytes(current_file)
                rom.setFileByName(narc_path.lstrip('/'), narc.save())
                current_rom._narc_cache.pop(narc_path.lstrip('/'), None)

                return {"written": len(data_bytes), "path": path, "narc": narc_path, "file_idx": file_idx}

            current_data = rom.getFileByName(path.lstrip('/'))
            new_data = bytearray(current_data)
            new_data[offset:offset + len(data_bytes)] = data_bytes
            rom.setFileByName(path.lstrip('/'), bytes(new_data))

            return {"written": len(data_bytes), "path": path}
        except Exception as e:
            return {"error": str(e)}

    else:
        with open(current_rom.rom_path, 'r+b') as f:
            f.seek(offset)
            f.write(data_bytes)
        return {"written": len(data_bytes), "offset": offset}


async def narc_append(path: str, data: str, encoding: str = "hex") -> dict:
    """Append a new file to an existing NARC. NDS only, HeartGold/SoulSilver or later."""
    if not current_rom:
        return {"error": "No ROM currently open"}
    if current_rom.rom_type != 'nds':
        return {"error": "NDS titles only — NARC append is not supported for GBA/GB ROMs"}
    gc = current_rom.header['game_code']
    gen = getattr(current_rom, 'GEN', 0) if current_rom else 0
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
        narc = current_rom.get_narc(narc_path)
        new_idx = len(narc.files)
        narc.files.append(bytes(data_bytes))
        current_rom.rom.setFileByName(narc_path, narc.save())
        current_rom._narc_cache.pop(narc_path, None)
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
    if current_rom.rom_type != 'nds':
        return {"error": "NDS titles only"}

    # ── PNG → NDS conversion (source= param) ───────────────────────────
    if source is not None:
        gc = current_rom.header['game_code']
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
    if gc_prefix and gc_prefix != current_rom.header['game_code']:
        orig_gc = _switch_rom(gc_prefix)

    try:
        gc = current_rom.header['game_code']
        game_dir = get_sprites_folder(gc)
        raw_dir = game_dir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)

        narc_path, idx_str = clean_path.rsplit(':', 1)
        narc = current_rom.get_narc(narc_path.lstrip('/'))
        base_idx = int(idx_str)

        # front/ or back/ from parameter
        output_dir = game_dir / facing
        output_dir.mkdir(parents=True, exist_ok=True)

        gen = getattr(current_rom, 'GEN', 4) if current_rom else 4
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

    if current_rom.rom_type != 'nds':
        return {"error": "Only NDS ROM saving supported"}

    rom = current_rom.rom

    # Recompress ARM9
    try:
        arm9_data = bytes(current_rom.arm9_data)
        with tempfile.NamedTemporaryFile(delete=False, suffix='.bin') as tmp:
            tmp.write(arm9_data)
            tmp_path = tmp.name
        compress_arm9(tmp_path)
        with open(tmp_path, 'rb') as f:
            rom.arm9 = f.read()
        Path(tmp_path).unlink()
    except:
        rom.arm9 = bytes(current_rom.arm9_data)

    rom.arm7 = bytes(current_rom.arm7_data)

    # Write modified overlays back to ROM files
    overlays = getattr(current_rom, 'overlays', {})
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

    # Re-encode any modified text files before saving
    if current_rom._text_modified and current_rom.text_narc and current_rom.text_narc_path:
        for fidx in current_rom._text_modified:
            strings = current_rom.text_tables.get(fidx, [])
            if current_rom.GEN == 5:
                current_rom.text_narc.files[fidx] = encode_gen5_text(strings, current_rom.text_mult or 0x2983)
            elif current_rom.GEN == 4:
                seed = struct.unpack_from('<H', current_rom.text_narc.files[fidx], 2)[0]
                current_rom.text_narc.files[fidx] = encode_gen4_text(strings, seed)
        rom.setFileByName(current_rom.text_narc_path, current_rom.text_narc.save())

    rom.saveToFile(output_path)

    return {"saved": output_path}


async def scope(path: str = None, offset: int = 0, length: int = 256, search: str = None, xor: str = None) -> dict:
    """Raw hex dump with optional search. xor: hex key to XOR data before display."""
    if not current_rom:
        return "Error: No ROM currently open"

    # Cross-ROM prefix
    if path:
        gc_prefix, clean_path = _parse_rom_prefix(path)
        if gc_prefix and gc_prefix != current_rom.header['game_code']:
            orig_gc = _switch_rom(gc_prefix)
            try:
                return await scope(clean_path, offset, length, search, xor)
            finally:
                _switch_rom(orig_gc)
        elif gc_prefix:
            path = clean_path

    if current_rom.rom_type in ('nds', '3ds', 'switch') and path:
        try:
            data = current_rom.read_file(path)
        except Exception as e:
            return {"error": f"File not found: {path} ({e})"}
    else:
        with open(current_rom.rom_path, 'rb') as f:
            f.seek(offset)
            data = f.read(length + (1024 if search else 0))

    dump_data = data[offset:offset + length] if current_rom.rom_type in ('nds', '3ds', 'switch') and path else data[:length]

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
        _switch_rom(_gc)
    if not current_rom:
        return "Error: No ROM currently open"

    # Cross-ROM prefix on narc_path
    if narc_path:
        gc_prefix, clean_narc = _parse_rom_prefix(narc_path)
        if gc_prefix and gc_prefix != current_rom.header['game_code']:
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
        if table and table in current_rom.text_tables:
            tables_to_search = {table: current_rom.text_tables[table]}
        else:
            # Search named tables + any files sketch has touched
            tables_to_search = {k: v for k, v in current_rom.text_tables.items() if isinstance(k, str) and isinstance(v, list)}
            for fidx in current_rom._text_modified:
                if fidx in current_rom.text_tables and isinstance(current_rom.text_tables[fidx], list):
                    tables_to_search[fidx] = current_rom.text_tables[fidx]
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
                gc = getattr(current_rom, 'header', {}).get('game_code', '')
                trdata_path = getattr(current_rom, 'TRDATA_PATH', None) if current_rom else None
                if trdata_path:
                    # NDS: resolve class ID → trdata file indices
                    td_files = current_rom.get_narc(trdata_path).files
                    for ch in class_hits:
                        cid = ch['index']
                        for fi, td in enumerate(td_files):
                            if len(td) >= 2 and td[1] == cid:
                                results.append({'table': 'trdata', 'index': fi, 'name': ch['name']})
                elif getattr(current_rom, 'rom_type', None) in ('gb', 'gbc', 'gba'):
                    # GB/GBC/GBA: emit trainer:NAME path — name IS the decipher key
                    for ch in class_hits:
                        ch.setdefault('paths', []).append(f"trainer:{ch['name']}")
            except Exception:
                pass
        # PWT: name-based lookup via pwt_name_to_entries → trainers_b paths
        if Unova_sequel.pwt_name_to_entries and current_rom:
            gc = getattr(current_rom, 'header', {}).get('game_code', '')
            if gc in ('IRE', 'IRD'):
                q_lower = query.strip().lower()
                tb_path = Unova_sequel.ROLE_PATH('pwt_trainers_b')
                if tb_path:
                    for pname, indices in Unova_sequel.pwt_name_to_entries.items():
                        if q_lower in pname:
                            for idx in indices:
                                tourns = Unova_sequel.pwt_entry_tournaments.get(idx, [])
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

            # Role/category search: if no text table hits, check current_rom.narc_roles and eonet_labels
            if not results and current_rom:
                gc = current_rom.header['game_code']
                role_hits = []
                for narc_p, role in current_rom.narc_roles.items():
                    if query in role.replace('_', ' ') or query in narc_p:
                        from ndspy.narc import NARC as _NARC
                        try:
                            fc = len(current_rom.get_narc(narc_p).files)
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
                _gc = getattr(current_rom, 'header', {}).get('game_code', '')
                _narcs = SDK.spec_narcs(SDK.get_spec(_gc)) if SDK.get_spec(_gc) else {}
                _spec = loaded_roms.get(_gc)
                _enc_loc = getattr(_spec, 'enc_loc', {}) if _spec else {}
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
                gc = getattr(current_rom, 'header', {}).get('game_code', '') if current_rom else ''
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
                    gc = getattr(current_rom, 'header', {}).get('game_code', '')
                    trdata_path = getattr(current_rom, 'TRDATA_PATH', None) if current_rom else None
                    if trdata_path:
                        td_files = current_rom.get_narc(trdata_path).files
                        for fi, td in enumerate(td_files):
                            if len(td) >= 2 and td[1] in rival['class_ids']:
                                results.append({'table': 'trdata', 'index': fi, 'name': rival['canonical']})
                        rival_note = rival.get('note', '')
                    elif getattr(current_rom, 'rom_type', None) in ('gba', 'gb', 'gbc'):
                        for off in current_rom.text_tables.get('trainer_offsets', []):
                            if off + 2 <= len(bytes(getattr(current_rom, 'rom', None) or b'')) and \
                               current_rom.rom[off + 1] in rival['class_ids']:
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
                    if 'trainer_names' in current_rom.text_tables and 'trainer_classes' in current_rom.text_tables:
                        class_hits = []
                        for idx, entry in enumerate(current_rom.text_tables['trainer_classes']):
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
            gc = current_rom.header['game_code'] if current_rom else ''
            narcs = SDK.spec_narcs(current_rom) if current_rom and current_rom else {}
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
                    if h.get('table') in ('trainer_names', 'trdata', 'trainer_classes') or str(h.get('table', '')).startswith('pwt_'):
                        trainer_names_set.add(rn)

            # Show trainer results first (full format)
            for rn, hits in by_name.items():
                if rn not in trainer_names_set:
                    continue
                out.append(f"Name: {rn}")

                pwt_hits = [h for h in hits if str(h.get('table', '')).startswith('pwt_')]
                regular = [h for h in hits if not str(h.get('table', '')).startswith('pwt_')]

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
                    tb_path = Unova_sequel.ROLE_PATH('pwt_trainers_b') or 'a/2/5/4'
                    out.append("----")
                    out.append(f"Indices within {tb_path} (PWT):")
                    out.append("")
                    for h in pwt_hits:
                        idx = h.get('index', 0)
                        tourns = Unova_sequel.pwt_entry_tournaments.get(idx, [])
                        if tourns:
                            for tn in tourns:
                                out.append(f"{idx} ({tn})")
                        else:
                            out.append(str(idx))

                # Tournament results — show participants with their PWT paths
                tourn_hits = [h for h in hits if h.get('table') == 'tournament_names']
                if tourn_hits:
                    tc_map = current_rom.text_tables.get('tournament_classes', {})
                    cls_list = current_rom.text_tables.get('trainer_classes', [])
                    tb_path = Unova_sequel.ROLE_PATH('pwt_trainers_b') or 'a/2/5/4'
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
                            entries = Unova_sequel.pwt_name_to_entries.get(pname.lower(), [])
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
            narc = current_rom.get_narc(narc_path.lstrip("/"))
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
        if current_rom.rom_type != "nds":
            return {"error": "Hex search only supported for NDS"}
        if not narc_path:
            return {"error": "Provide narc_path, arm9.bin, arm7.bin, or overlayN.bin"}
        search_bytes = bytes.fromhex(hex.replace(" ", ""))

        # ARM9 / ARM7
        if narc_path.lower() in ("arm9.bin", "arm7.bin"):
            data = bytes(current_rom.arm9_data if narc_path.lower() == "arm9.bin" else current_rom.arm7_data)
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
            overlays = getattr(current_rom, "overlays", {})
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
            narc = current_rom.get_narc(narc_path.lstrip("/"))
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

    if current_rom.rom_type != 'nds':
        return {"error": "Diff only supported for NDS"}

    def resolve_path(p):
        """Resolve a path, handling cross-ROM prefixes."""
        gc_prefix, clean_p = _parse_rom_prefix(p)
        if gc_prefix and gc_prefix != current_rom.header['game_code']:
            orig_gc = _switch_rom(gc_prefix)
            try:
                return current_rom.read_file(clean_p)
            finally:
                _switch_rom(orig_gc)
        return current_rom.read_file(clean_p if gc_prefix else p)

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
            proxy_log = Path.home() / ".silphéon" / "eonet_proxy.log"
            pid_file = Path.home() / ".silphéon" / "eonet_proxy.pid"
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
    gc = current_rom.header['game_code']
    fpn_notes = current_flipnote['data'].get('notes', {}) if current_flipnote else {}
    rom_stats = current_flipnote['data'].get('rom_stats', {}) if current_flipnote else {}
    total_bytes = rom_stats.get('total_bytes', 0)

    # ICR index coverage
    index = eonet_index.get(gc, [])
    labels = eonet_labels.get(gc, {})
    roles = current_rom.narc_roles

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
        proxy_log = Path.home() / ".silphéon" / "eonet_proxy.log"
        pid_file = Path.home() / ".silphéon" / "eonet_proxy.pid"
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
        "game": current_rom.header['game_title'],
        "rom_size": f"{total_bytes / 1024 / 1024:.1f} MB" if total_bytes else "?",
        "startup_log": _startup_log if _startup_log else ["clean start"],
        "text_tables_detected": [k for k in current_rom.text_tables if isinstance(k, str) and isinstance(current_rom.text_tables.get(k), list)],
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

    flipnotes = []
    for fpn in (Path.home() / '.silphéon' / 'flipnotes').glob("*.fpn"):
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
    for fpn in (Path.home() / '.silphéon' / 'flipnotes').glob("*.fpn"):
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
    note_history = Path.home() / ".silphéon" / "note_history.jsonl"
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
    if gc_prefix and gc_prefix != current_rom.header['game_code']:
        orig_gc = _switch_rom(gc_prefix)
        try:
            return await probe(clean_path, offset, reads, count, xor, endian, stride, base)
        finally:
            _switch_rom(orig_gc)
    elif gc_prefix:
        path = clean_path
    try:
        if current_rom.rom_type not in ('nds', '3ds', 'switch'):
            with open(current_rom.rom_path, 'rb') as f:
                data = f.read()
        else:
            data = current_rom.read_file(path)
            # NARC internals and named files may be compressed; arm9/arm7/overlays are pre-decompressed
            if path.lower() not in ('arm9.bin', 'arm7.bin') and _is_overlay_path(path) < 0:
                data, _ = decompress_data(data)
    except Exception as e:
        return {"error": f"Failed to read {path}: {e}"}
    if xor:
        xk = bytes.fromhex(xor.replace(' ', ''))
        data = bytes(b ^ xk[i % len(xk)] for i, b in enumerate(data))
    if reads == 'text':
        gen = current_rom.GEN or 5
        if gen == 5 and current_rom.text_mult is not None:
            strings = decode_gen5_text(data, current_rom.text_mult)
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
        _role_hint = current_rom.narc_roles.get(_np)
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
                tbl = current_rom.text_tables.get(tname, [])
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
        gc = current_rom.header['game_code']
    if not gc:
        return ''
    _s = current_rom if current_rom else None
    gt = (_s.TITLES[_s.GAME_CODES.index(gc)] if _s and gc in getattr(_s, 'GAME_CODES', ()) else '')
    if not gt:
        rom_state = loaded_roms[gc].rom if gc and gc in loaded_roms else None
        if rom_state:
            gt = rom_state['header'].get('game_title', gc)
        elif current_rom:
            gt = current_rom.header.get('game_title', gc)
        else:
            gt = gc
    return gt.replace(' Nintendo', '').replace(' Game Freak', '').strip()

def _difficulty_label(path_str):
    """For B2W2 trdata paths, label difficulty block."""
    if not current_rom or current_rom.GEN != 5:
        return ''
    try:
        gc = current_rom.header.get('game_code', '')
        if gc in ('IRB', 'IRA'):
            return ''
        trdata_path = getattr(current_rom, 'TRDATA_PATH', '') if current_rom else ''
        trpoke_path = getattr(current_rom, 'TRPOKE_PATH', '') if current_rom else ''
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
                    

            # Restore ROMs in background — don't block MCP handshake
            global _restore_task
            _restore_task = asyncio.create_task(_do_pending_restore())
            
            # Recover notes from past conversations on startup
            try:
                recovered = recover_notes_from_logs()
            except Exception:
                recovered = 0

            
            # Keepalive: ping the connection every 30s to prevent idle timeout
            async def keepalive():
                while True:
                    await asyncio.sleep(30)
                    try:
                        # Write a comment to stderr to keep the pipe alive
                        print("[Silphéon] keepalive", file=sys.stderr, flush=True)
                    except:
                        break
            
            keepalive_task = asyncio.create_task(keepalive())
            try:
                await server.run(read_stream, write_stream, server.create_initialization_options())
            finally:
                keepalive_task.cancel()

    asyncio.run(main())
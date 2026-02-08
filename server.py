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

server = Server("linkplay")

# State
current_rom = None
current_flipnote = None
text_tables = {}  # Populated on open_rom: {file_index: [strings], 'species': [strings], ...}
text_narc = None   # Kept in memory for lazy lookups
text_mult = None   # Derived once from species file (Gen V only)
text_gen = None    # 4 or 5, set during bootstrap
narc_roles = {}    # Reverse map: narc_path -> role (e.g. 'a/0/9/2' -> 'trpoke')
loaded_roms = {}   # game_code -> saved state for multi-ROM support


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
    }


def _restore_state(game_code):
    """Restore a ROM's state from loaded_roms to globals."""
    global current_rom, current_flipnote, text_tables, text_narc, text_mult, text_gen, narc_roles
    state = loaded_roms[game_code]
    current_rom = state['current_rom']
    current_flipnote = state['flipnote']
    text_tables = state['text_tables']
    text_narc = state['text_narc']
    text_mult = state['text_mult']
    text_gen = state['text_gen']
    narc_roles = state['narc_roles']


def _clear_active_state():
    """Clear all ROM state globals."""
    global current_rom, current_flipnote, text_tables, text_narc, text_mult, text_gen, narc_roles
    current_rom = None
    current_flipnote = None
    text_tables = {}
    text_narc = None
    text_mult = None
    text_gen = None
    narc_roles = {}
working_dir = Path.home() / ".linkplay" / "work"
flipnotes_dir = Path.home() / ".linkplay" / "flipnotes"

# Region codes from game code suffix
REGION_MAP = {
    'E': 'US', 'P': 'EU', 'J': 'JP', 'K': 'KR',
    'D': 'DE', 'F': 'FR', 'S': 'ES', 'I': 'IT',
    'O': 'INT'  # International (used by Game Freak to bypass region locking)
}


def ensure_dirs():
    working_dir.mkdir(parents=True, exist_ok=True)
    flipnotes_dir.mkdir(parents=True, exist_ok=True)


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
                if ctrl_type == 0x0000:
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
_GEN5_BASE = {
    'text': 'a/0/0/2',
    'trdata': 'a/0/9/1',
    'trpoke': 'a/0/9/2',
    'encounters': 'a/0/8/2',
    'personal': 'a/0/1/6',
    'learnsets': 'a/0/1/8',
    'evolutions': 'a/0/1/9',
    'move_data': 'a/0/2/1',
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
    'personal': 'poketool/personal/personal.narc',
    'learnsets': 'poketool/personal/wotbl.narc',
    'evolutions': 'poketool/personal/evo.narc',
    'move_data': 'poketool/waza/waza_tbl.narc',
    'trdata': 'poketool/trainer/trdata.narc',
    'trpoke': 'poketool/trainer/trpoke.narc',
    'items': 'itemtool/itemdata/item_data.narc',
    'contest': 'contest/data/contest_data.narc',
}
_GEN4_PLATINUM_OVERRIDES = {
    'personal': 'poketool/personal/pl_personal.narc',
    'move_data': 'poketool/waza/pl_waza_tbl.narc',
    'items': 'itemtool/itemdata/pl_item_data.narc',
    'encounters': 'fielddata/encountdata/pl_enc_data.narc',
    'battle_tower_pokemon': 'battle/b_pl_tower/pl_btdpm.narc',
    'battle_tower_trainers': 'battle/b_pl_tower/pl_btdtr.narc',
}
_GEN4_HGSS = {
    'text': 'a/0/2/7',
    'personal': 'a/0/0/2',
    'learnsets': 'a/0/3/3',
    'evolutions': 'a/0/3/4',
    'move_data': 'a/0/1/1',
    'trdata': 'a/0/5/5',
    'trpoke': 'a/0/5/6',
    'battle_tower_pokemon': 'a/1/2/9',
    'battle_tower_trainers': 'a/1/2/8',
}

GAME_INFO = {
    # Gen V
    'IRE': {'gen': 5, 'narcs': {**_GEN5_BASE, **_B2W2_PWT, **_B2W2_SUBWAY}},  # Black 2
    'IRD': {'gen': 5, 'narcs': {**_GEN5_BASE, **_B2W2_PWT, **_B2W2_SUBWAY}},  # White 2
    'IRB': {'gen': 5, 'narcs': {**_GEN5_BASE, **_BW1_SUBWAY}},  # Black
    'IRA': {'gen': 5, 'narcs': {**_GEN5_BASE, **_BW1_SUBWAY}},  # White
    # Gen IV — Diamond/Pearl
    'ADA': {'gen': 4, 'narcs': {**_GEN4_COMMON,
        'text': 'msgdata/msg.narc',
        'encounters': 'fielddata/encountdata/d_enc_data.narc',
        'battle_tower_pokemon': 'battle/b_tower/btdpm.narc',
        'battle_tower_trainers': 'battle/b_tower/btdtr.narc',
    }},
    'APA': {'gen': 4, 'narcs': {**_GEN4_COMMON,
        'text': 'msgdata/msg.narc',
        'encounters': 'fielddata/encountdata/p_enc_data.narc',
        'battle_tower_pokemon': 'battle/b_tower/btdpm.narc',
        'battle_tower_trainers': 'battle/b_tower/btdtr.narc',
    }},
    # Gen IV — Platinum
    'CPU': {'gen': 4, 'narcs': {**_GEN4_COMMON, **_GEN4_PLATINUM_OVERRIDES,
        'text': 'msgdata/pl_msg.narc',
    }},
    # Gen IV — HGSS
    'IPK': {'gen': 4, 'narcs': {**_GEN4_HGSS}},  # HeartGold
    'IPG': {'gen': 4, 'narcs': {**_GEN4_HGSS}},  # SoulSilver
}

# Content fingerprints — universal across all Pokemon games.
# (entry_index, expected_string) pairs that ALL must match.
TABLE_FINGERPRINTS = {
    'species':    [(1, "Bulbasaur"), (4, "Charmander")],
    'moves':      [(1, "Pound"), (5, "Mega Punch")],
    'items':      [(1, "Master Ball"), (17, "Potion")],
    'abilities':  [(1, "Stench"), (22, "Intimidate")],
    'natures':    [(0, "Hardy"), (1, "Lonely"), (3, "Adamant")],
    'type_names': [(0, "Normal"), (1, "Fighting"), (2, "Flying")],
}

# Heuristic markers — tables without unique index-based fingerprints.
# All listed strings must appear SOMEWHERE in the file.
HEURISTIC_MARKERS = {
    'trainer_classes': ["Youngster", "Lass", "Bug Catcher"],
    'location_names':  ["Route 1", "Route 2", "Route 3"],
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
            if all(idx < len(strings) and strings[idx] == expected for idx, expected in markers):
                text_tables[table_name] = strings
                found[table_name] = file_idx

    # Pass 2: heuristic markers (all listed strings must exist in file)
    for file_idx in sorted(k for k in text_tables if isinstance(k, int)):
        strings = text_tables[file_idx]
        if not isinstance(strings, list):
            continue
        string_set = set(strings)
        for table_name, markers in HEURISTIC_MARKERS.items():
            if table_name in found:
                continue
            if all(m in string_set for m in markers):
                text_tables[table_name] = strings
                found[table_name] = file_idx

    # Pass 3: adjacency — trainer_names is usually near trainer_classes
    if 'trainer_classes' in found and 'trainer_names' not in found:
        tc_idx = found['trainer_classes']
        for offset in [-1, -2, 1, 2]:
            candidate = tc_idx + offset
            if candidate in text_tables and isinstance(text_tables[candidate], list):
                entries = text_tables[candidate]
                if len(entries) > 100 and candidate not in found.values():
                    text_tables['trainer_names'] = entries
                    found['trainer_names'] = candidate
                    break

    # Pass 4: description tables — usually adjacent to their name tables
    for name_tbl, desc_tbl in [('items', 'item_descriptions'), ('moves', 'move_descriptions'), ('abilities', 'ability_descriptions')]:
        if name_tbl in found and desc_tbl not in found:
            name_idx = found[name_tbl]
            name_count = len(text_tables[name_tbl])
            for offset in [-1, 1]:
                candidate = name_idx + offset
                if candidate in text_tables and isinstance(text_tables[candidate], list) and candidate not in found.values():
                    entries = text_tables[candidate]
                    if abs(len(entries) - name_count) < 10:
                        avg_len = sum(len(s) for s in entries[:20]) / max(1, min(20, len(entries)))
                        if avg_len > 20:  # descriptions are longer than names
                            text_tables[desc_tbl] = entries
                            found[desc_tbl] = candidate
                            break

    return found


def decode_gen4_text(data: bytes) -> list:
    """Decode Gen IV (DPPt/HGSS) text file.
    Format: u16 num_entries, u16 seed, encrypted entry table, encrypted strings.
    Entry table XOR: rolling key from seed * 0x2FD, advancing +0x493D per u16.
    String XOR: key = 0x91BD3 * (entry + 1) & 0xFFFF, advancing +0x493D per u16.
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

    # Decrypt entry table (each u16 XOR'd with rolling key)
    key = (seed * 0x2FD) & 0xFFFF
    entry_data = bytearray(data[4:table_end])
    for i in range(0, len(entry_data), 2):
        if i + 1 < len(entry_data):
            val = entry_data[i] | (entry_data[i + 1] << 8)
            val ^= key
            entry_data[i] = val & 0xFF
            entry_data[i + 1] = (val >> 8) & 0xFF
            key = (key + 0x493D) & 0xFFFF

    entries = []
    for i in range(num_entries):
        offset = struct.unpack_from('<I', entry_data, i * 8)[0]
        length = struct.unpack_from('<I', entry_data, i * 8 + 4)[0]
        entries.append((offset & 0xFFFF, length & 0xFFFF))

    strings = []
    for i, (offset, length) in enumerate(entries):
        str_start = table_end + offset
        if length == 0 or str_start + length * 2 > len(data):
            strings.append("")
            continue

        # Per-string decryption key
        key = ((i + 1) * 0x91BD3) & 0xFFFF
        chars = []
        for j in range(length):
            pos = str_start + j * 2
            if pos + 2 > len(data):
                break
            enc = struct.unpack_from('<H', data, pos)[0]
            dec = (enc ^ key) & 0xFFFF
            key = (key + 0x493D) & 0xFFFF

            if dec == 0xFFFF:
                break
            elif dec == 0xFFFE:
                chars.append('\n')
            elif dec in _GEN5_CHARMAP:
                chars.append(_GEN5_CHARMAP[dec])
            elif 0x0020 <= dec < 0xFFFE:
                try:
                    chars.append(chr(dec))
                except (ValueError, OverflowError):
                    chars.append(f'\\x{dec:04X}')
            else:
                chars.append(f'\\x{dec:04X}')

        strings.append(''.join(chars))

    return strings

# TRPoke template sizes (keyed by template bits from TRData byte 0)
# bit 0 = has custom moves, bit 1 = has held item
TRPOKE_FORMATS = {
    0: 8,   # iv(1) ability(1) level(1) pad(1) species(2) form(2)
    1: 16,  # + move1(2) move2(2) move3(2) move4(2)
    2: 10,  # + item(2)
    3: 18,  # + item(2) + moves(8)
}


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
    if len(species) > 1 and species[1] == "Bulbasaur":
        result["status"] = "ok"
        result["sample"] = {"species[1]": species[1], "species[26]": species[26] if len(species) > 26 else "?"}
    else:
        result["status"] = "FAILED"
        result["_warning"] = "Could not find species table"

    if found:
        result["detected"] = found

    return result


EV_STAT_BITS = ['HP', 'Atk', 'Def', 'Spe', 'SpA', 'SpD']  # bit 0-5

def decode_ev_spread(byte_val):
    """Decode EV bitmask: each set bit = 252 EVs in that stat."""
    stats = [EV_STAT_BITS[i] for i in range(6) if byte_val & (1 << i)]
    return stats if stats else ["None"]

def decode_trainer_iv(byte_val):
    """TRPoke difficulty byte → IV for all stats. 255 → 31, 0 → 0."""
    return byte_val * 31 // 255

def decode_trpoke(data: bytes, trainer_data: bytes = None) -> dict:
    """Decode a TRPoke file into human-readable format using text_tables."""
    if len(data) == 0:
        return {"pokemon": [], "raw": data.hex()}

    # Determine template from TRData byte 0 if available
    template = 0
    if trainer_data and len(trainer_data) >= 1:
        template = trainer_data[0] & 0x03
    else:
        # Guess from file size
        for t in [3, 2, 1, 0]:
            if len(data) % TRPOKE_FORMATS[t] == 0 and len(data) // TRPOKE_FORMATS[t] > 0:
                template = t
                break

    pokemon_size = TRPOKE_FORMATS.get(template, 8)
    num_pokemon = len(data) // pokemon_size

    species_list = text_tables.get('species', [])
    moves_list = text_tables.get('moves', [])
    items_list = text_tables.get('items', [])

    GENDER_OVERRIDE = {0: "default", 1: "male", 2: "female"}

    pokemon = []
    for i in range(num_pokemon):
        off = i * pokemon_size
        if off + pokemon_size > len(data):
            break

        difficulty = data[off]
        ability_gender = data[off + 1]
        level = data[off + 2]
        species_id = struct.unpack_from('<H', data, off + 4)[0]
        form = struct.unpack_from('<H', data, off + 6)[0]

        ability_slot = ability_gender >> 4
        gender = GENDER_OVERRIDE.get(ability_gender & 0xF, f"unknown ({ability_gender & 0xF})")
        species_name = species_list[species_id] if species_id < len(species_list) else f"#{species_id}"

        entry = {
            "species": species_name, "species_id": species_id,
            "level": level,
            "ivs": decode_trainer_iv(difficulty),
            "ability_slot": ability_slot,
            "gender": gender,
            "form": form,
        }

        if template & 2:  # Has held item
            item_id = struct.unpack_from('<H', data, off + 8)[0]
            item_name = items_list[item_id] if item_id < len(items_list) else f"item#{item_id}"
            entry["held_item"] = item_name if item_id > 0 else "None"

        if template & 1:  # Has moves
            move_off = off + 8 + (2 if template & 2 else 0)
            for m in range(4):
                mid = struct.unpack_from('<H', data, move_off + m * 2)[0]
                mname = moves_list[mid] if mid < len(moves_list) else f"move#{mid}"
                entry[f"move_{m+1}"] = mname if mid > 0 else "---"

        pokemon.append(entry)

    return {"template": template, "count": num_pokemon, "pokemon": pokemon, "raw": data.hex()}


def decode_trdata(data: bytes, index: int = None) -> dict:
    """Decode a TRData entry into human-readable format."""
    if len(data) < 20:
        return {"raw": data.hex()}
    
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

    ai_flags = struct.unpack_from('<I', data, 12)[0]
    prize_money_base = data[17]
    area_id = data[18]
    class_name = trainer_classes[trainer_class] if trainer_class < len(trainer_classes) else f"class#{trainer_class}"

    result = {
        "class": class_name,
        "battle_type": BATTLE_TYPES.get(battle_type, f"Unknown ({battle_type})"),
        "num_pokemon": num_pokemon,
        "has_custom_moves": has_moves,
        "has_held_items": has_items,
        "ai_flags": f"0x{ai_flags:08X}",
        "battle_items": battle_items if battle_items else "None",
        "reward_multiplier": prize_money_base,
        "area_id": area_id,
        "raw": data.hex(),
    }

    if index is not None and index < len(trainer_names):
        result["name"] = trainer_names[index]

    return result




def decode_pwt(data: bytes, is_champions: bool = False) -> dict:
    """Decode PWT pokemon pool entry. 16 bytes per entry.
    Format: species(2) + moves(8) + ev_spread(1) + nature(1) + field12(2) + pad(2).
    field12 = held_item in Champions NARCs, trainer_id in Rental NARCs."""
    if len(data) < 16 or data == b'\x00' * 16:
        return None

    species_list = text_tables.get('species', [])
    moves_list = text_tables.get('moves', [])
    natures_list = text_tables.get('natures', [])
    items_list = text_tables.get('items', [])
    classes_list = text_tables.get('trainer_classes', [])

    species_id = struct.unpack_from('<H', data, 0)[0]
    moves = [struct.unpack_from('<H', data, 2 + i * 2)[0] for i in range(4)]
    ev_spread = data[10]
    nature = data[11]
    field12 = struct.unpack_from('<H', data, 12)[0]

    species_name = species_list[species_id] if species_id < len(species_list) else f"#{species_id}"
    nature_raw = natures_list[nature] if nature < len(natures_list) else ""
    nature_name = re.sub(r'[^\x20-\x7E]', '', nature_raw).replace(' nature.', '').strip() if nature_raw else f"nature#{nature}"

    move_names = []
    for mid in moves:
        if mid == 0:
            move_names.append("---")
        elif mid < len(moves_list):
            move_names.append(moves_list[mid])
        else:
            move_names.append(f"move#{mid}")

    result = {
        "species": species_name, "species_id": species_id,
        "moves": move_names,
        "evs": decode_ev_spread(ev_spread),
        "nature": nature_name,
        "raw": data.hex(),
    }

    if is_champions:
        item_name = items_list[field12] if field12 < len(items_list) else f"item#{field12}"
        result["held_item"] = item_name if field12 > 0 else "None"
    else:
        class_name = classes_list[field12] if field12 < len(classes_list) else f"class#{field12}"
        result["trainer_class"] = class_name

    return result


def decode_pwt_roster(data: bytes) -> dict:
    """Decode PWT tournament roster — maps tournament slot to pool indices.
    Format: format(u16) + count(u16) + pool_indices(u16 * count)."""
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
    return {"format": fmt, "pool_count": count, "pool_indices": indices, "raw": data.hex()}


def decode_pwt_trainer_config(data: bytes) -> dict:
    """Decode PWT tournament trainer config (compact, 6 bytes).
    Format: format(u16) + count(u16) + start_index(u16)."""
    if len(data) < 6:
        return None
    fmt = struct.unpack_from('<H', data, 0)[0]
    count = struct.unpack_from('<H', data, 2)[0]
    start_idx = struct.unpack_from('<H', data, 4)[0]
    if fmt == 0 and count == 0 and start_idx == 0:
        return None
    return {"format": fmt, "count": count, "start_index": start_idx, "raw": data.hex()}


EV_YIELD_STATS = ['HP', 'Atk', 'Def', 'Spe', 'SpA', 'SpD']

def decode_personal(data: bytes, file_idx: int = 0) -> dict:
    """Decode personal data. Gen IV=44B (u8 abilities), Gen V=76B (u16 abilities+hidden)."""
    if len(data) < 28 or data == b'\x00' * len(data):
        return None
    gen = text_gen or 5
    species_list = text_tables.get('species', [])
    type_list = text_tables.get('type_names', [])
    ability_list = text_tables.get('abilities', [])
    item_list = text_tables.get('items', [])

    # First 10 bytes identical across Gen IV/V
    base = {s: data[i] for i, s in enumerate(['hp', 'atk', 'def', 'spe', 'spa', 'spd'])}
    type1, type2 = data[6], data[7]
    catch_rate = data[8]

    ev_raw = struct.unpack_from('<H', data, 0x0A)[0]
    ev_yield = {}
    for i, stat in enumerate(EV_YIELD_STATS):
        val = (ev_raw >> (i * 2)) & 3
        if val:
            ev_yield[stat] = val

    if gen <= 4:
        # Gen IV: 2 held items (u16), gender, hatch, happiness, growth, eggs, 2 abilities (u8)
        items = [struct.unpack_from('<H', data, 0x0C + i * 2)[0] for i in range(2)]
        held = {}
        for label, item_id in zip(['common', 'rare'], items):
            if item_id > 0:
                held[label] = item_list[item_id] if item_id < len(item_list) else f"item#{item_id}"
        gender = data[0x10]
        hatch_cycles = data[0x11]
        base_happiness = data[0x12]
        exp_growth = data[0x13]
        egg1, egg2 = data[0x14], data[0x15]
        abilities = []
        for ab in (data[0x16], data[0x17]):
            if ab > 0:
                abilities.append(ability_list[ab] if ab < len(ability_list) else f"ability#{ab}")
    else:
        # Gen V: 3 held items, abilities as u16×3 (slot1/slot2/hidden)
        items = [struct.unpack_from('<H', data, 0x0C + i * 2)[0] for i in range(3)]
        held = {}
        for label, item_id in zip(['common', 'rare', 'hidden'], items):
            if item_id > 0:
                held[label] = item_list[item_id] if item_id < len(item_list) else f"item#{item_id}"
        gender = data[0x12]
        hatch_cycles = data[0x13]
        base_happiness = data[0x14]
        exp_growth = data[0x15]
        egg1, egg2 = data[0x16], data[0x17]
        abilities = []
        for i in range(3):
            off = 0x18 + i * 2
            if off + 2 <= len(data):
                aid = struct.unpack_from('<H', data, off)[0]
                if aid > 0:
                    abilities.append(ability_list[aid] if aid < len(ability_list) else f"ability#{aid}")

    species_name = species_list[file_idx] if file_idx < len(species_list) else f"#{file_idx}"
    result = {
        "species": species_name, "species_id": file_idx,
        "base_stats": base, "bst": sum(base.values()),
        "types": [type_list[type1] if type1 < len(type_list) else f"type#{type1}",
                  type_list[type2] if type2 < len(type_list) else f"type#{type2}"],
        "catch_rate": catch_rate,
        "ev_yield": ev_yield if ev_yield else "None",
        "held_items": held if held else "None",
        "abilities": abilities,
        "gender_ratio": gender,
        "hatch_cycles": hatch_cycles,
        "base_happiness": base_happiness,
        "exp_growth": exp_growth,
        "egg_groups": [egg1, egg2],
    }
    if result["types"][0] == result["types"][1]:
        result["types"] = [result["types"][0]]
    return result


def decode_learnset(data: bytes, file_idx: int = 0) -> dict:
    """Decode learnset. Gen IV: packed u16 (level<<9)|move_id. Gen V: explicit u16 pairs."""
    if len(data) < 2:
        return None
    gen = text_gen or 5
    species_list = text_tables.get('species', [])
    moves_list = text_tables.get('moves', [])
    species_name = species_list[file_idx] if file_idx < len(species_list) else f"#{file_idx}"

    moves = []
    if gen <= 4:
        # Gen IV: packed u16 — (level << 9) | move_id, terminated by 0xFFFF
        for i in range(0, len(data) - 1, 2):
            raw = struct.unpack_from('<H', data, i)[0]
            if raw == 0xFFFF:
                break
            move_id = raw & 0x1FF
            level = (raw >> 9) & 0x7F
            move_name = moves_list[move_id] if move_id < len(moves_list) else f"move#{move_id}"
            moves.append({"move": move_name, "level": level})
    else:
        # Gen V: (move_id u16, level u16) pairs, terminated by FFFFFFFF
        for i in range(0, len(data) - 3, 4):
            move_id = struct.unpack_from('<H', data, i)[0]
            level = struct.unpack_from('<H', data, i + 2)[0]
            if move_id == 0xFFFF:
                break
            move_name = moves_list[move_id] if move_id < len(moves_list) else f"move#{move_id}"
            moves.append({"move": move_name, "level": level})

    return {"species": species_name, "species_id": file_idx, "moves": moves}


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

def decode_evolution(data: bytes, file_idx: int = 0) -> dict:
    """Decode evolution table — 7 slots of (method u16, param u16, target u16). 42B Gen V, 44B Gen IV."""
    if len(data) < 42 or data[:42] == b'\x00' * 42:
        return None
    species_list = text_tables.get('species', [])
    item_list = text_tables.get('items', [])
    moves_list = text_tables.get('moves', [])
    species_name = species_list[file_idx] if file_idx < len(species_list) else f"#{file_idx}"
    evolutions = []
    for i in range(7):
        off = i * 6
        method = struct.unpack_from('<H', data, off)[0]
        param = struct.unpack_from('<H', data, off + 2)[0]
        target = struct.unpack_from('<H', data, off + 4)[0]
        if method == 0 and target == 0:
            continue
        method_name = EVOLUTION_METHODS.get(method, f"method#{method}")
        target_name = species_list[target] if target < len(species_list) else f"#{target}"
        evo = {"method": method_name, "target": target_name, "target_id": target}
        if method in (4, 9, 10, 11, 21, 22, 23, 24, 25, 26, 27, 28):
            evo["level"] = param
        elif method in (6, 8, 17, 18):
            evo["item"] = item_list[param] if param < len(item_list) else f"item#{param}"
        elif method == 19:
            evo["move"] = moves_list[param] if param < len(moves_list) else f"move#{param}"
        elif method == 7:
            evo["trade_species"] = species_list[param] if param < len(species_list) else f"#{param}"
        elif method == 20:
            evo["party_species"] = species_list[param] if param < len(species_list) else f"#{param}"
        elif param > 0:
            evo["param"] = param
        evolutions.append(evo)
    if not evolutions:
        return None
    return {"species": species_name, "species_id": file_idx, "evolutions": evolutions}


MOVE_CATEGORIES_G5 = {0: "Status", 1: "Physical", 2: "Special"}
MOVE_CATEGORIES_G4 = {0: "Physical", 1: "Special", 2: "Status"}

def decode_move_data(data: bytes, file_idx: int = 0) -> dict:
    """Decode move data. Gen IV=16B, Gen V=36B — different field layouts."""
    if data == b'\x00' * len(data):
        return None
    gen = text_gen or 5
    type_list = text_tables.get('type_names', [])
    moves_list = text_tables.get('moves', [])
    move_name = moves_list[file_idx] if file_idx < len(moves_list) else f"move#{file_idx}"

    if gen <= 4 and len(data) >= 12:
        # Gen IV: effect(u16), category(u8), power(u8), type(u8), accuracy(u8), PP(u8), ...
        category = data[2]
        power = data[3]
        move_type = data[4]
        accuracy = data[5]
        pp = data[6]
        type_name = type_list[move_type] if move_type < len(type_list) else f"type#{move_type}"
        result = {
            "move": move_name, "move_id": file_idx,
            "type": type_name, "category": MOVE_CATEGORIES_G4.get(category, f"cat#{category}"),
            "power": power if power > 0 else "—",
            "accuracy": accuracy if accuracy <= 100 else "—",
            "pp": pp,
        }
    elif len(data) >= 36:
        # Gen V: type(u8), ?(u8), category(u8), power(u8), accuracy(u8), PP(u8), priority(i8), ...
        move_type = data[0]
        category = data[2]
        power = data[3]
        accuracy = data[4]
        pp = data[5]
        priority = struct.unpack_from('b', data, 6)[0]
        multi_hit = data[7]
        effect_chance = data[10]
        type_name = type_list[move_type] if move_type < len(type_list) else f"type#{move_type}"
        result = {
            "move": move_name, "move_id": file_idx,
            "type": type_name, "category": MOVE_CATEGORIES_G5.get(category, f"cat#{category}"),
            "power": power if power > 0 else "—",
            "accuracy": accuracy if accuracy <= 100 else "—",
            "pp": pp,
        }
        if priority != 0:
            result["priority"] = priority
        if multi_hit > 0:
            lo, hi = multi_hit & 0xF, (multi_hit >> 4) & 0xF
            result["hits"] = f"{lo}-{hi}" if lo != hi else str(lo)
        if effect_chance > 0:
            result["effect_chance"] = f"{effect_chance}%"
    else:
        return None
    return result


def decode_encounters(data: bytes) -> dict:
    """Decode Gen V wild encounter data using text_tables."""
    if len(data) < 232:
        return None
    
    # Encounter rates
    rates = {
        "grass": data[0], "double_grass": data[1], "special_grass": data[2],
        "surf": data[3], "special_surf": data[4],
        "fishing": data[5], "special_fishing": data[6]
    }
    
    def read_entries(offset, count):
        entries = []
        for j in range(count):
            pos = offset + j * 4
            if pos + 4 > len(data):
                break
            species_id = struct.unpack_from("<H", data, pos)[0]
            min_lv = data[pos + 2]
            max_lv = data[pos + 3]
            if species_id == 0:
                continue
            name = get_text("species", species_id)
            entries.append({"species_id": species_id, "species": name, "level": f"{min_lv}-{max_lv}"})
        return entries
    
    result = {"rates": {k: v for k, v in rates.items() if v > 0}}
    
    groups = [
        ("grass", 8, 12), ("double_grass", 56, 12), ("special_grass", 104, 12),
        ("surf", 152, 5), ("special_surf", 172, 5),
        ("fishing", 192, 5), ("special_fishing", 212, 5)
    ]
    for name, offset, count in groups:
        if rates.get(name, 0) > 0 or name in ("grass", "surf", "fishing"):
            entries = read_entries(offset, count)
            if entries:
                result[name] = entries
    
    return result


def _auto_decode(path: str, data: bytes):
    """Auto-decode known structures by role, not hardcoded paths."""
    if not narc_roles or ':' not in path:
        return None

    narc_part, idx_str = path.rsplit(':', 1)
    narc_part = narc_part.strip('/')
    file_idx = int(idx_str)
    role = narc_roles.get(narc_part)
    if not role:
        return None

    narcs = GAME_INFO.get(current_rom['header']['game_code'], {}).get('narcs', {})
    rom = current_rom['rom']

    try:
        if role == 'trpoke':
            trdata_path = narcs.get('trdata', '')
            td_narc = ndspy.narc.NARC(rom.getFileByName(trdata_path))
            td = td_narc.files[file_idx] if file_idx < len(td_narc.files) else None
            decoded = decode_trpoke(data, td)
            decoded["trainer_index"] = file_idx
            trainer_names = text_tables.get('trainer_names', [])
            if file_idx < len(trainer_names):
                decoded["trainer_name"] = trainer_names[file_idx]
            return decoded
        elif role == 'trdata':
            decoded = decode_trdata(data, file_idx)
            trpoke_path = narcs.get('trpoke', '')
            tp_narc = ndspy.narc.NARC(rom.getFileByName(trpoke_path))
            if file_idx < len(tp_narc.files):
                tp_data = tp_narc.files[file_idx]
                template = data[0] & 0x03
                poke_size = TRPOKE_FORMATS.get(template, 8)
                num_pokemon = len(tp_data) // poke_size
                if num_pokemon > 0:
                    last_off = (num_pokemon - 1) * poke_size
                    last_level = tp_data[last_off + 2]
                    multiplier = decoded.get("reward_multiplier", 0)
                    decoded["prize_money"] = multiplier * last_level * 4
            return decoded
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
                location_names = text_tables.get('location_names', [])
                if file_idx < len(location_names):
                    decoded['location'] = location_names[file_idx]
                return decoded
        elif role in ('pwt_rosters', 'pwt_rosters_b'):
            decoded = decode_pwt_roster(data)
            if decoded:
                decoded['pool'] = 'Rosters-B' if role.endswith('_b') else 'Rosters'
                decoded['slot_index'] = file_idx
                return decoded
        elif role in ('pwt_trainers', 'pwt_trainers_b'):
            decoded = decode_pwt_trainer_config(data)
            if decoded:
                decoded['pool'] = 'Trainers-B' if role.endswith('_b') else 'Trainers'
                decoded['slot_index'] = file_idx
                return decoded
        elif role.startswith('pwt_') and role not in ('pwt_download', 'pwt_ui'):
            decoded = decode_pwt(data, 'champions' in role)
            if decoded:
                decoded['pool'] = role[4:].replace('_b', '-B').replace('_', ' ').title()
                decoded['pool_index'] = file_idx
                return decoded
        elif role == 'subway_pokemon':
            decoded = decode_pwt(data, False)
            if decoded:
                decoded['facility'] = 'Battle Subway'
                decoded['pool_index'] = file_idx
                return decoded
        elif role == 'subway_trainers':
            decoded = decode_pwt_roster(data)
            if decoded:
                decoded['facility'] = 'Battle Subway'
                decoded['trainer_index'] = file_idx
                return decoded
        elif role == 'battle_tower_pokemon':
            decoded = decode_pwt(data, False)
            if decoded:
                decoded['facility'] = 'Battle Tower'
                decoded['pool_index'] = file_idx
                return decoded
        elif role == 'battle_tower_trainers':
            decoded = decode_pwt_roster(data)
            if decoded:
                decoded['facility'] = 'Battle Tower'
                decoded['trainer_index'] = file_idx
                return decoded
    except Exception:
        pass

    return None


# ============ Tool Handlers ============

async def open_rom(path: str) -> dict:
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

        # Decompress ARM9 in memory
        arm9_data = bytearray(rom.arm9)
        arm7_data = bytearray(rom.arm7)
        try:
            import tempfile
            with tempfile.NamedTemporaryFile(delete=False, suffix='.bin') as tmp:
                tmp.write(arm9_data)
                tmp_path = tmp.name
            decompress_arm9(tmp_path)
            with open(tmp_path, 'rb') as f:
                arm9_data = bytearray(f.read())
            Path(tmp_path).unlink()
        except:
            pass

        current_rom = {
            'type': 'nds', 'path': path, 'rom': rom, 'header': header,
            'arm9_data': arm9_data, 'arm7_data': arm7_data,
            'compression_state': {}
        }

        # Bootstrap text tables (Gen IV/V)
        try:
            text_table_result = bootstrap_text_tables(rom, gc)
        except Exception as e:
            text_table_result = {"error": str(e)}

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

    result = {
        "rom_type": rom_type, "game_code": gc,
        "game_title": header['game_title'], "region": header['region'],
        "flipnote": str(fpn_path), "loaded": list(loaded_roms.keys())
    }
    if text_table_result:
        result["text_tables"] = text_table_result

    return result


async def close_rom(save: bool = False) -> dict:
    """Close the active ROM (or all with save=False). Switches to another loaded ROM if available."""
    if not current_rom:
        return {"error": "No ROM currently open"}

    gc = current_rom['header']['game_code']

    if save and current_rom['type'] == 'nds':
        try:
            result = await save_rom(current_rom['path'])
            if 'error' in result:
                return result
        except Exception as e:
            return {"error": f"Failed to save ROM: {e}"}

    result = {"closed": current_rom['header']['game_title']}
    if save:
        result["saved"] = True

    # Remove from loaded_roms
    loaded_roms.pop(gc, None)

    # Switch to another loaded ROM if available
    if loaded_roms:
        next_gc = next(iter(loaded_roms))
        _restore_state(next_gc)
        result["switched_to"] = next_gc
        result["loaded"] = list(loaded_roms.keys())
    else:
        _clear_active_state()

    return result


async def ls(path: str = "/", expand_narcs: bool = False) -> dict:
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
        try:
            file_data = rom.getFileByName(clean_path)
            if file_data[:4] == b'NARC':
                narc = ndspy.narc.NARC(file_data)
                for i, f in enumerate(narc.files):
                    entry = {"index": i, "size": len(f), "path": f"{clean_path}:{i}"}
                    if len(f) >= 4:
                        if f[0] == 0x10: entry["compression"] = "lz10"
                        elif f[0] == 0x11: entry["compression"] = "lz11"
                        elif f[0] in (0x24, 0x28): entry["compression"] = "huffman"
                        elif f[0] == 0x30: entry["compression"] = "rle"
                    contents.append(entry)
                return {"path": clean_path, "type": "narc", "file_count": len(narc.files), "contents": contents}
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

        for filename in folder.files:
            file_id = folder.idOf(filename)
            file_data = rom.files[file_id]
            full_path = path.strip('/') + ('/' if path.strip('/') else '') + filename

            entry = {"name": filename, "type": "file", "size": len(file_data), "path": full_path}

            if len(file_data) >= 4 and file_data[:4] == b'NARC':
                entry["type"] = "narc"
                try:
                    narc = ndspy.narc.NARC(file_data)
                    entry["file_count"] = len(narc.files)
                except:
                    pass

            contents.append(entry)

        for name, _ in folder.folders:
            contents.append({"name": name + "/", "type": "folder"})

    except Exception as e:
        return {"error": str(e)}

    return {"path": path, "contents": contents}


async def read(path: str, offset: int = 0, length: int = None, decompress: bool = True) -> dict:
    """Read file contents or bytes."""
    # Multi-file: comma-separated paths
    if "," in path:
        results = []
        for p in path.split(","):
            p = p.strip()
            if p:
                results.append(await read(p, offset, length, decompress))
        return {"multi": True, "results": results}

    if not current_rom:
        return {"error": "No ROM currently open"}

    if current_rom['type'] == 'nds':
        rom = current_rom['rom']

        try:
            if path.lower() == 'arm9.bin':
                data = bytes(current_rom['arm9_data'])
                compression = 'none'
            elif path.lower() == 'arm7.bin':
                data = bytes(current_rom['arm7_data'])
                compression = 'none'
            elif ':' in path:
                narc_path, file_idx = path.rsplit(':', 1)
                file_idx = int(file_idx)
                narc_data = rom.getFileByName(narc_path.lstrip('/'))
                narc = ndspy.narc.NARC(narc_data)
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

            return {"path": path, "size": len(data), "compression": compression, "data": data.hex(), "decoded": _auto_decode(path, data)}

        except Exception as e:
            return {"error": str(e)}

    else:
        with open(current_rom['path'], 'rb') as f:
            f.seek(offset)
            data = f.read(length) if length else f.read()
        return {"offset": offset, "size": len(data), "data": data.hex()}


async def write(path: str, data: str, offset: int = 0, encoding: str = "hex") -> dict:
    """Write data to a file."""
    if not current_rom:
        return {"error": "No ROM currently open"}

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

            if ':' in path:
                narc_path, file_idx = path.rsplit(':', 1)
                file_idx = int(file_idx)
                narc_data = rom.getFileByName(narc_path.lstrip('/'))
                narc = ndspy.narc.NARC(narc_data)

                current_file = bytearray(narc.files[file_idx])
                current_file[offset:offset + len(data_bytes)] = data_bytes
                narc.files[file_idx] = bytes(current_file)
                rom.setFileByName(narc_path.lstrip('/'), narc.save())

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


async def save_rom(output_path: str) -> dict:
    """Repack and save the ROM."""
    if not current_rom:
        return {"error": "No ROM currently open"}

    if current_rom['type'] != 'nds':
        return {"error": "Only NDS ROM saving supported"}

    rom = current_rom['rom']

    # Recompress ARM9
    try:
        import tempfile
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
    rom.saveToFile(output_path)

    return {"saved": output_path}


async def hexdump(path: str = None, offset: int = 0, length: int = 256, search: str = None) -> dict:
    """Raw hex dump with optional search."""
    if not current_rom:
        return {"error": "No ROM currently open"}

    if current_rom['type'] == 'nds' and path:
        rom = current_rom['rom']
        try:
            if path.lower() == 'arm9.bin':
                data = bytes(current_rom['arm9_data'])
            elif path.lower() == 'arm7.bin':
                data = bytes(current_rom['arm7_data'])
            elif ':' in path:
                narc_path, file_idx = path.rsplit(':', 1)
                file_idx = int(file_idx)
                narc_data = rom.getFileByName(narc_path.lstrip('/'))
                narc = ndspy.narc.NARC(narc_data)
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

    hex_lines = []
    for i in range(0, len(dump_data), 16):
        chunk = dump_data[i:i + 16]
        hex_part = ' '.join(f'{b:02X}' for b in chunk)
        ascii_part = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
        hex_lines.append(f"{offset + i:08X}  {hex_part:<48}  {ascii_part}")

    result = {"offset": offset, "length": len(dump_data), "dump": '\n'.join(hex_lines)}

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



async def search(narc_path: str = None, hex: str = None, name: str = None, table: str = None, exact: bool = False) -> dict:
    """Search NARC files by hex pattern, or look up text table entries by name.
    
    Modes:
      - name: search named text tables (species, moves, items, etc.)
      - name + table: search specific table only
      - hex + narc_path: find files in NARC containing hex pattern
      - hex (no narc_path): search ALL loaded NARCs (slow but thorough)
      - exact=True: match whole string, not substring
    """
    if not current_rom:
        return {"error": "No ROM currently open"}
    
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
        if not narc_path:
            return {"query": name, "exact": exact, "matches": results, "count": len(results)}
        
        # name + narc_path: resolve matches to IDs, search NARC for those IDs as LE u16
        rom = current_rom["rom"]
        try:
            nd = rom.getFileByName(narc_path.lstrip("/"))
            narc = ndspy.narc.NARC(nd)
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
    
    # Hex pattern search in NARC
    if hex:
        if current_rom["type"] != "nds":
            return {"error": "Hex search only supported for NDS"}
        rom = current_rom["rom"]
        search_bytes = bytes.fromhex(hex.replace(" ", ""))
        results = []
        
        if narc_path:
            # Search specific NARC
            try:
                narc_data = rom.getFileByName(narc_path.lstrip("/"))
                narc = ndspy.narc.NARC(narc_data)
            except Exception as e:
                return {"error": f"Could not open NARC: {e}"}
            for idx, fdata in enumerate(narc.files):
                pos = 0
                offsets = []
                while True:
                    pos = fdata.find(search_bytes, pos)
                    if pos == -1:
                        break
                    offsets.append(pos)
                    pos += 1
                if offsets:
                    results.append({"file": f"{narc_path}:{idx}", "offsets": offsets})
        else:
            return {"error": "Provide narc_path for hex search"}
        
        return {"pattern": hex, "narc": narc_path, "matches": results, "count": len(results)}
    
    return {"error": "Provide either name (text lookup) or hex (hex search)"}


async def diff(path_a: str, path_b: str) -> dict:
    """Compare two files."""
    if not current_rom:
        return {"error": "No ROM currently open"}

    if current_rom['type'] != 'nds':
        return {"error": "Diff only supported for NDS"}

    rom = current_rom['rom']

    def resolve_path(p):
        p = p.strip('/')
        if p.lower() == 'arm9.bin':
            return bytes(current_rom['arm9_data'])
        elif p.lower() == 'arm7.bin':
            return bytes(current_rom['arm7_data'])
        elif ':' in p:
            narc_path, file_idx = p.rsplit(':', 1)
            file_idx = int(file_idx)
            narc_data = rom.getFileByName(narc_path.lstrip('/'))
            narc = ndspy.narc.NARC(narc_data)
            if file_idx >= len(narc.files):
                raise ValueError(f"Index {file_idx} out of range (NARC has {len(narc.files)} files)")
            return narc.files[file_idx]
        else:
            return rom.getFileByName(p)

    try:
        data_a = resolve_path(path_a)
        data_b = resolve_path(path_b)
    except Exception as e:
        return {"error": str(e)}

    differences = []
    max_len = max(len(data_a), len(data_b))

    for i in range(max_len):
        byte_a = data_a[i] if i < len(data_a) else None
        byte_b = data_b[i] if i < len(data_b) else None

        if byte_a != byte_b:
            differences.append({
                "offset": i,
                "a": f"{byte_a:02X}" if byte_a is not None else "N/A",
                "b": f"{byte_b:02X}" if byte_b is not None else "N/A"
            })

    return {
        "identical": len(differences) == 0,
        "size_a": len(data_a),
        "size_b": len(data_b),
        "difference_count": len(differences),
        "differences": differences[:100]
    }



async def stats() -> dict:
    """Show honest documentation coverage statistics."""
    if not current_flipnote:
        return {"error": "No ROM currently open"}

    fpn = current_flipnote['data']
    notes = fpn.get('notes', {})
    tree = fpn.get('tree', [])
    rom_stats = fpn.get('rom_stats', {})

    # Count files in tree (excluding folders)
    total_files = len([p for p in tree if not p.endswith('/')])
    narc_internal = len([p for p in tree if ':' in p])
    top_level_files = total_files - narc_internal

    # Count documented paths
    documented = len(notes)

    # Calculate byte coverage (rough estimate)
    total_bytes = rom_stats.get('total_bytes', 0)
    arm9_size = rom_stats.get('arm9_size', 0)

    # Count arm9 regions documented (look for arm9 in notes)
    arm9_notes = [n for n in notes.keys() if 'arm9' in n.lower()]
    arm9_documented_bytes = 0
    for note_path in arm9_notes:
        # Try to extract byte count from description
        note_data = notes[note_path]
        desc = note_data.get('description', '')
        # Look for patterns like "180 bytes" or "172 bytes"
        import re
        match = re.search(r'(\d+)\s*bytes?', desc, re.IGNORECASE)
        if match:
            arm9_documented_bytes += int(match.group(1))

    # Count files with actual structure documented (have 'format' or 'structure' field)
    structured = len([n for n in notes.values() if n.get('format') or n.get('structure')])

    files_percent = f"{(documented / top_level_files * 100):.1f}%" if top_level_files > 0 else "0%"
    bytes_total_human = f"{total_bytes / 1024 / 1024:.1f} MB" if total_bytes > 0 else "?"
    arm9_percent = f"{(arm9_documented_bytes / arm9_size * 100):.3f}%" if arm9_size > 0 else "0%"
    coverage_percent = f"{(arm9_documented_bytes / total_bytes * 100):.4f}%" if total_bytes > 0 else "0%"

    return {
        "game": fpn.get('game_title', 'Unknown'),
        "coverage": {
            "files_labeled": documented,
            "files_total": top_level_files,
            "files_percent": files_percent,
            "narc_files_total": narc_internal,
            "files_with_structure": structured,
            "bytes_total": total_bytes,
            "bytes_total_human": bytes_total_human,
            "arm9_size": arm9_size,
            "arm9_documented_bytes": arm9_documented_bytes,
            "arm9_percent": arm9_percent
        },
        "honest_assessment": (
            f"You've labeled {documented} paths but most are just descriptions, not real documentation. "
            f"Real byte-level understanding: ~{arm9_documented_bytes} bytes out of {total_bytes:,} ({coverage_percent})"
        )
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


async def view_flipnote(game: str) -> dict:
    """View a Flipnote's contents."""
    ensure_dirs()

    for fpn in flipnotes_dir.glob("*.fpn"):
        try:
            with open(fpn, 'r', encoding='utf-8') as f:
                data = json.load(f)
                codes = data.get('game_codes', [])
                if not codes:
                    codes = [data.get('game_code', '')]
                title_lower = data.get('game_title', '').lower()
                words_match = all(w in title_lower for w in game.lower().split())
                if game in codes or words_match:
                    return {
                        "game_codes": codes,
                        "game_title": data.get("game_title"),
                        "region_codes": data.get("region_codes", {}),
                        "note_count": len(data.get("notes", {})),
                        "notes": data.get("notes", {})
                    }
        except:
            continue

    return {"error": f"Flipnote not found for: {game}"}


async def note(path: str, description: str, name: str = None, format: str = None,
               tags: list = None, file_range: str = None, examples: list = None, related: list = None) -> dict:
    """Add a note to the current Flipnote."""
    if not current_flipnote:
        return {"error": "No ROM currently open"}

    fpn_data = current_flipnote['data']

    note_data = {"description": description}
    if name: note_data["name"] = name
    if format: note_data["format"] = format
    if tags: note_data["tags"] = tags
    if file_range: note_data["file_range"] = file_range
    if examples: note_data["examples"] = examples
    if related: note_data["related"] = related

    fpn_data['notes'][path] = note_data

    with open(current_flipnote['path'], 'w', encoding='utf-8') as f:
        json.dump(fpn_data, f, indent=2, ensure_ascii=False)

    return {"noted": path, "description": description}


async def edit_note(path: str, description: str = None, name: str = None, format: str = None,
                    tags: list = None, file_range: str = None, examples: list = None, related: list = None) -> dict:
    """Edit an existing note."""
    if not current_flipnote:
        return {"error": "No ROM currently open"}

    fpn_data = current_flipnote['data']

    if path not in fpn_data['notes']:
        return {"error": f"Note not found: {path}"}

    if description: fpn_data['notes'][path]["description"] = description
    if name is not None: fpn_data['notes'][path]["name"] = name
    if format is not None: fpn_data['notes'][path]["format"] = format
    if tags is not None: fpn_data['notes'][path]["tags"] = tags
    if file_range is not None: fpn_data['notes'][path]["file_range"] = file_range
    if examples is not None: fpn_data['notes'][path]["examples"] = examples
    if related is not None: fpn_data['notes'][path]["related"] = related

    with open(current_flipnote['path'], 'w', encoding='utf-8') as f:
        json.dump(fpn_data, f, indent=2, ensure_ascii=False)

    return {"edited": path}


async def delete_note(path: str) -> dict:
    """Delete a note from the Flipnote."""
    if not current_flipnote:
        return {"error": "No ROM currently open"}

    fpn_data = current_flipnote['data']

    if path not in fpn_data['notes']:
        return {"error": f"Note not found: {path}"}

    del fpn_data['notes'][path]

    with open(current_flipnote['path'], 'w') as f:
        json.dump(fpn_data, f, indent=2)

    return {"deleted": path}


# ============ Server Setup ============

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    """Route tool calls to handler functions."""
    handlers = {
        "open_rom": open_rom,
        "close_rom": close_rom,
        "ls": ls,
        "read": read,
        "write": write,
        "save_rom": save_rom,
        "hexdump": hexdump,
        "search": search,
        "diff": diff,
        "stats": stats,
        "list_flipnotes": list_flipnotes,
        "view_flipnote": view_flipnote,
        "note": note,
        "edit_note": edit_note,
        "delete_note": delete_note
    }

    handler = handlers.get(name)
    if not handler:
        raise ValueError(f"Unknown tool: {name}")

    result = await handler(**arguments)
    return [TextContent(type="text", text=json.dumps(result, indent=2))]


@server.list_tools()
async def list_tools():
    return [
        Tool(name="open_rom", description="Open a ROM file (.nds, .gba, .gbc, .gb) for exploration", inputSchema={
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Path to ROM file"}},
            "required": ["path"]
        }),
        Tool(name="close_rom", description="Close the current ROM", inputSchema={
            "type": "object",
            "properties": {"save": {"type": "boolean", "description": "Save changes before closing"}}
        }),
        Tool(name="ls", description="List contents at a path within the ROM. Pass a NARC file path to see its contents.", inputSchema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to list (default: root). Can be a folder or NARC file path."},
                "expand_narcs": {"type": "boolean", "description": "If true, show preview of NARC contents inline (default: false)"}
            }
        }),
        Tool(name="read", description="Read file contents (auto-decompresses LZ10/LZ11)", inputSchema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path. Use 'arm9.bin' or 'arm7.bin' for ARM binaries. Use 'narc_path:index' for files inside NARCs. Comma-separated for multi-file (e.g. 'a/0/9/1:5,a/0/9/2:5')."},
                "offset": {"type": "integer", "description": "Byte offset (default: 0)"},
                "length": {"type": "integer", "description": "Bytes to read (default: entire file)"},
                "decompress": {"type": "boolean", "description": "Auto-decompress LZ10/LZ11 (default: true)"}
            },
            "required": ["path"]
        }),
        Tool(name="write", description="Write data to a file (supports text and hex with spaces)", inputSchema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path"},
                "data": {"type": "string", "description": "Data to write (hex can have spaces: 'F8 B5 82 B0')"},
                "offset": {"type": "integer", "description": "Byte offset (default: 0)"},
                "encoding": {"type": "string", "enum": ["hex", "utf8", "utf16le", "ascii"], "description": "Data encoding (default: hex)"}
            },
            "required": ["path", "data"]
        }),
        Tool(name="save_rom", description="Repack and save the ROM", inputSchema={
            "type": "object",
            "properties": {"output_path": {"type": "string", "description": "Output path"}},
            "required": ["output_path"]
        }),
        Tool(name="hexdump", description="Raw hex dump with optional search", inputSchema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path (optional)"},
                "offset": {"type": "integer", "description": "Start offset"},
                "length": {"type": "integer", "description": "Bytes to dump"},
                "search": {"type": "string", "description": "Hex pattern to search"}
            }
        }),
        Tool(name="search", description="Search NARC files by hex pattern, or look up text table entries by name", inputSchema={
            "type": "object",
            "properties": {
                "narc_path": {"type": "string", "description": "NARC path to search (for hex mode)"},
                "hex": {"type": "string", "description": "Hex pattern to find in NARC files"},
                "name": {"type": "string", "description": "Name to look up in text tables (e.g. Raichu)"},
                "table": {"type": "string", "description": "Specific table to search (species, moves, items, abilities, trainer_names, trainer_classes)"},
                "exact": {"type": "boolean", "description": "Match whole string instead of substring (default: false)"}
            }
        }),
        Tool(name="diff", description="Compare two files", inputSchema={
            "type": "object",
            "properties": {
                "path_a": {"type": "string", "description": "First file"},
                "path_b": {"type": "string", "description": "Second file"}
            },
            "required": ["path_a", "path_b"]
        }),
        Tool(name="stats", description="Show honest documentation coverage statistics", inputSchema={
            "type": "object", "properties": {}
        }),
        Tool(name="list_flipnotes", description="List all known game Flipnotes", inputSchema={
            "type": "object", "properties": {}
        }),
        Tool(name="view_flipnote", description="View a Flipnote's contents", inputSchema={
            "type": "object",
            "properties": {"game": {"type": "string", "description": "Game code or title"}},
            "required": ["game"]
        }),
        Tool(name="note", description="Add a note to the current Flipnote", inputSchema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path being documented"},
                "description": {"type": "string", "description": "What this path contains"},
                "name": {"type": "string", "description": "Human-readable name"},
                "format": {"type": "string", "description": "File format description"},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags for categorization"},
                "file_range": {"type": "string", "description": "Description of file range"},
                "examples": {"type": "array", "items": {"type": "string"}, "description": "Example files"},
                "related": {"type": "array", "items": {"type": "string"}, "description": "Related paths"}
            },
            "required": ["path", "description"]
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
                "related": {"type": "array", "items": {"type": "string"}, "description": "Related paths"}
            },
            "required": ["path"]
        }),
        Tool(name="delete_note", description="Delete a note", inputSchema={
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Path of note to delete"}},
            "required": ["path"]
        })
    ]


if __name__ == "__main__":
    import asyncio
    from mcp.server.stdio import stdio_server

    async def main():
        async with stdio_server() as (read_stream, write_stream):
            setup_tools()
            await server.run(read_stream, write_stream, server.create_initialization_options())

    asyncio.run(main())

"""Gen IV (Diamond/Pearl/Platinum/HeartGold/SoulSilver) - text decoder, NARC paths, encounters, Pokeathlon."""
import struct


# ============================================================
# # _GEN4_COMMON, _GEN4_DP_COMMON, _GEN4_PLATINUM_OVERRIDES, _GEN4_HGSS
# server.py lines 2125-2164
# ============================================================

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

# ============================================================
# # _GEN4 char tables + _get_gen4_char
# server.py lines 2372-2467
# ============================================================

# Gen IV complete character map
# Based on Bulbapedia: https://bulbapedia.bulbagarden.net/wiki/Character_encoding_(Generation_IV)

# Hiragana (0x0001-0x0051)
# Per Bulbapedia: https://bulbapedia.bulbagarden.net/wiki/Character_encoding_(Generation_IV)
# Hiragana (0x0001-0x0051) — sequential Unicode U+3041+ with tail overrides for archaic skips
_GEN4_HIRAGANA = {i: chr(0x3040 + i) for i in range(0x01, 0x4E)}
_GEN4_HIRAGANA.update({0x4E: 'わ', 0x4F: 'を', 0x50: 'ん', 0x51: 'ゔ'})

# Katakana (0x0052-0x00A1) — sequential Unicode U+30A1+ with tail overrides
_GEN4_KATAKANA = {i: chr(0x304F + i) for i in range(0x52, 0x9F)}
_GEN4_KATAKANA.update({0x9F: 'ワ', 0xA0: 'ヲ', 0xA1: 'ン'})

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
    elif 0x00A2 <= c <= 0x00AB:
        return chr(ord('0') + c - 0x00A2)
    elif 0x00AC <= c <= 0x00C5:
        return chr(ord('A') + c - 0x00AC)
    elif 0x00C6 <= c <= 0x00DF:
        return chr(ord('a') + c - 0x00C6)
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

# ============================================================
# # decode_gen4_text
# server.py lines 2650-2742
# ============================================================

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

# ============================================================
# # AI_FLAGS_GEN4
# server.py lines 2764-2781
# ============================================================

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

# ============================================================
# # TRPOKE_FORMATS_G4
# server.py lines 2845-2850
# ============================================================

TRPOKE_FORMATS_G4 = {
    0: 6,   # base
    1: 14,  # + moves(8)
    2: 8,   # + item(2)
    3: 18,  # + item(2) + moves(8) + pad(2)
}

# ============================================================
# # _decode_encounters_dpp
# server.py lines 5070-5139
# ============================================================

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

# ============================================================
# # _decode_encounters_hgss
# server.py lines 5142-5225
# ============================================================

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

# ============================================================
# # POKEATHLON_STATS
# server.py lines 5297-5297
# ============================================================

POKEATHLON_STATS = ['Power', 'Speed', 'Jump', 'Stamina', 'Skill']

# ============================================================
# # _build_pokeathlon_form_map + _POKEATHLON_FORM_MAP + decode_pokeathlon_performance
# server.py lines 5299-5387
# ============================================================

# sPokeathlonPerformanceArcIdxs[] from pret/pokeheartgold src/pokemon.c
# maps species_id → base NARC file index. Game reads: file = arr[species] + form.
# Built into a reverse map: narc_file → (species_id, form_label)
def _build_pokeathlon_form_map():
    # Extracted from sPokeathlonPerformanceArcIdxs (494 entries, species 0-493)
    # Only entries where the species has >1 form need explicit mapping.
    # Form labels per species (index = form number):
    # Labels uppercase to match Gen 4 text style.
    # Leading '*' = full name override (no parenthetical — e.g. Spiky-eared Pichu).
    _FORM_LABELS = {
        172: ['', '*SPIKY-EARED PICHU'],
        201: list('ABCDEFGHIJKLMNOPQRSTUVWXYZ') + ['!', '?'],
        386: ['NORMAL FORME', 'ATTACK FORME', 'DEFENSE FORME', 'SPEED FORME'],
        412: ['PLANT CLOAK', 'SANDY CLOAK', 'TRASH CLOAK'],
        413: ['PLANT CLOAK', 'SANDY CLOAK', 'TRASH CLOAK'],
        422: ['WEST SEA', 'EAST SEA'],
        423: ['WEST SEA', 'EAST SEA'],
        479: ['', 'HEAT', 'WASH', 'FROST', 'FAN', 'MOW'],
        487: ['ALTERED FORME', 'ORIGIN FORME'],
        492: ['LAND FORME', 'SKY FORME'],
        493: ['NORMAL','FIGHTING','FLYING','POISON','GROUND','ROCK','BUG','GHOST',
              'STEEL','FIRE','WATER','GRASS','ELECTRIC','PSYCHIC','ICE','DRAGON','DARK','???'],
    }
    # Base indices extracted from the decomp array (only species with >1 form listed;
    # all others are sequential: arr[sp] = sp-1 adjusted for gaps).
    # We build the reverse map by replaying the full arr[] logic.
    # Jumps from the script analysis (species → (base_idx, n_forms)):
    _FORM_SPECIES = {
        172: (171, 2), 201: (201, 28), 386: (413, 4),
        412: (442, 3), 413: (445, 3), 422: (456, 2), 423: (458, 2),
        479: (515, 6), 487: (528, 2), 492: (534, 2), 493: (536, 18),
    }
    result = {}
    # Build full reverse map: reconstruct arr[] by simulating the sequential + form offsets
    narc_i = 0
    for sp in range(1, 494):
        if sp in _FORM_SPECIES:
            base_idx, n_forms = _FORM_SPECIES[sp]
            labels = _FORM_LABELS.get(sp, [])
            for f in range(n_forms):
                label = labels[f] if f < len(labels) else f'Form {f}'
                result[base_idx + f] = (sp, label)
            narc_i = base_idx + n_forms
        else:
            # Sequential: use the known narc_i counter
            result[narc_i] = (sp, '')
            narc_i += 1
    return result

_POKEATHLON_FORM_MAP = _build_pokeathlon_form_map()


def decode_pokeathlon_performance(data: bytes, text_tables: dict, file_idx: int = 0):
    """Decode Pokéathlon performance stats (HGSS only). Returns positional text."""
    if len(data) != 20:
        return None

    species_list = text_tables.get('species', [])
    entry = _POKEATHLON_FORM_MAP.get(file_idx)
    if entry:
        sp_id, form_label = entry
        sp_name = species_list[sp_id] if sp_id < len(species_list) else f"#{sp_id}"
        if form_label.startswith('*'):
            title = form_label[1:]          # full name override
        elif form_label:
            title = f"{sp_name} ({form_label})"
        else:
            title = sp_name
        sp_display = f"#{sp_id}"
    else:
        sp_id = file_idx + 1
        sp_name = species_list[sp_id] if sp_id < len(species_list) else f"#{sp_id}"
        title, sp_display = sp_name, f"#{sp_id}"

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

    lines = [f"{title} ({sp_display}) — Pokéathlon"]
    lines.append(" | ".join(parts))
    return "\n".join(lines)

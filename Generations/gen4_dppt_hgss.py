"""Gen IV (Diamond/Pearl/Platinum/HeartGold/SoulSilver) - text decoder, NARC paths, encounters, Pokeathlon."""
import struct
from xoleon import (
    _GEN4_HIRAGANA, _GEN4_KATAKANA, _GEN4_FULLWIDTH_SYMBOLS, _GEN4_SPECIAL,
    _get_gen4_char, decode_gen4_text,
)
from Generations.sdk import MOVE_CATEGORIES_G4


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


_GEN4_FLIPNOTE_PAIRS = {
    # Gen IV
    'Pokémon Diamond & Pearl': ['ADA', 'APA'],
    'Pokémon Platinum': ['CPU'],
    'Pokémon HeartGold & SoulSilver': ['IPK', 'IPG'],
}


_GEN4_GAME_INFO = {
    # Gen IV — Nintendo DS
    'ADA': {'gen': 4, 'platform': 'Nintendo DS', 'year': 2007, 'narcs': {**_GEN4_DP_COMMON, 'encounters': 'fielddata/encountdata/d_enc_data.narc'}},  # Diamond US
    'APA': {'gen': 4, 'platform': 'Nintendo DS', 'year': 2007, 'narcs': {**_GEN4_DP_COMMON, 'encounters': 'fielddata/encountdata/p_enc_data.narc'}},  # Pearl US
    'CPU': {'gen': 4, 'platform': 'Nintendo DS', 'year': 2009, 'narcs': {**_GEN4_COMMON, **_GEN4_PLATINUM_OVERRIDES}},                      # Platinum US
    'IPK': {'gen': 4, 'platform': 'Nintendo DS', 'year': 2010, 'narcs': {**_GEN4_HGSS}},                                                    # HeartGold US
    'IPG': {'gen': 4, 'platform': 'Nintendo DS', 'year': 2010, 'narcs': {**_GEN4_HGSS}},                                                    # SoulSilver US
}


_GEN4_TRAINER_LOCATIONS = {
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
}


_GEN4_CLASS_LOCATIONS = {
    "ADA": {"Elite Four": "Pokémon League", "Champion": "Pokémon League"},
    "APA": "ADA",
    "CPU": {"Elite Four": "Pokémon League", "Champion": "Pokémon League", "Tower Tycoon": "Battle Tower"},
    "IPK": {"Elite Four": "Indigo Plateau", "Champion": "Indigo Plateau",
            "Brock": "Pewter Gym", "Misty": "Cerulean Gym", "Lt. Surge": "Vermilion Gym",
            "Erika": "Celadon Gym", "Janine": "Fuchsia Gym", "Sabrina": "Saffron Gym",
            "Blaine": "Seafoam Gym", "Blue": "Viridian Gym"},
    "IPG": "IPK",
}


_GEN4_TM_SEARCH = {
    4: (bytes([0x08, 0x01, 0x51, 0x01, 0x60, 0x01, 0x5B, 0x01]), 100),  # 92 TMs + 8 HMs
}


"""Gen VI (X/Y + Omega Ruby/Alpha Sapphire) — GARC paths, encounter decoder, text table indices."""
import struct

# ============================================================
# GARC path dicts — from pk3DS GARCReference.cs
# ============================================================
_GEN6_XY = {
    'text': 'a/0/7/2',
    'personal': 'a/2/1/8',
    'learnsets': 'a/2/1/4',
    'evolutions': 'a/2/1/5',
    'egg_moves': 'a/2/1/3',
    'mega_evos': 'a/2/1/6',
    'baby_species': 'a/2/1/9',
    'items': 'a/2/2/0',
    'move_data': 'a/2/1/2',
    'encounters': 'a/0/1/2',
    'trdata': 'a/0/3/8',
    'trpoke': 'a/0/4/0',
    'trclass': 'a/0/3/9',
    'maison_pokemon_normal': 'a/2/0/3',
    'maison_trainers_normal': 'a/2/0/4',
    'maison_pokemon_super': 'a/2/0/5',
    'maison_trainers_super': 'a/2/0/6',
}

_GEN6_ORAS = {
    'text': 'a/0/7/1',
    'personal': 'a/1/9/5',
    'learnsets': 'a/1/9/1',
    'evolutions': 'a/1/9/2',
    'egg_moves': 'a/1/9/0',
    'mega_evos': 'a/1/9/3',
    'items': 'a/1/9/7',
    'move_data': 'a/1/8/9',
    'encounters': 'a/0/1/3',
    'trdata': 'a/0/3/6',
    'trpoke': 'a/0/3/8',
    'trclass': 'a/0/3/7',
    'maison_pokemon_normal': 'a/1/8/2',
    'maison_trainers_normal': 'a/1/8/3',
    'maison_pokemon_super': 'a/1/8/4',
    'maison_trainers_super': 'a/1/8/5',
}


# ============================================================
# Gen VI encounter decoder
# ============================================================

# XY: 94 slots x 4 bytes = 0x178
_XY_SECTIONS = [
    ('Grass',          0,  12),
    ('Yellow Flowers', 12, 12),
    ('Purple Flowers', 24, 12),
    ('Red Flowers',    36, 12),
    ('Rough Terrain',  48, 12),
    ('Surf',           60,  5),
    ('Rock Smash',     65,  5),
    ('Old Rod',        70,  3),
    ('Good Rod',       73,  3),
    ('Super Rod',      76,  3),
    ('Horde A',        79,  5),
    ('Horde B',        84,  5),
    ('Horde C',        89,  5),
]

# ORAS: 61 slots x 4 bytes = 0xF4
_ORAS_SECTIONS = [
    ('Grass',          0,  12),
    ('Tall Grass',     12, 12),
    ('Rock Smash',     24,  3),
    ('Surf',           27,  5),
    ('Old Rod',        32,  5),
    ('Good Rod',       37,  3),
    ('Super Rod',      40,  3),
    ('Swarm',          43,  3),
    ('Horde A',        46,  5),
    ('Horde B',        51,  5),
    ('Horde C',        56,  5),
]

# Standard encounter rates per slot count
_RATES_12 = [20, 20, 10, 10, 10, 10, 5, 5, 4, 4, 1, 1]
_RATES_5  = [50, 30, 15, 4, 1]
_RATES_3  = [60, 35, 5]

def _get_rates(count):
    if count == 12: return _RATES_12
    if count == 5:  return _RATES_5
    if count == 3:  return _RATES_3
    return [100 // count] * count


def _parse_slots_gen6(data: bytes, num_slots: int) -> list:
    """Parse Gen VI encounter slots. Each slot = 4 bytes: u16(species|form<<11) + u8 min + u8 max."""
    slots = []
    for i in range(num_slots):
        ofs = i * 4
        if ofs + 4 > len(data):
            break
        raw = struct.unpack_from('<H', data, ofs)[0]
        species = raw & 0x7FF
        form = raw >> 11
        min_lv = data[ofs + 2]
        max_lv = data[ofs + 3]
        slots.append((species, form, min_lv, max_lv))
    return slots


def _decode_encounters_gen6(data: bytes, is_oras: bool = False) -> dict | None:
    """Decode Gen VI encounter data from a GARC sub-file.
    File has a header; encounter table starts at offset read from file[0x10].
    XY: +0x10, 94 slots (0x178 bytes). ORAS: +0x0E, 61 slots."""
    if not data or len(data) < 0x14:
        return None

    ptr = struct.unpack_from('<I', data, 0x10)[0]
    enc_offset = ptr + (0x0E if is_oras else 0x10)
    enc_size = 0xF4 if is_oras else 0x178
    sections = _ORAS_SECTIONS if is_oras else _XY_SECTIONS

    if enc_offset + enc_size > len(data):
        return None

    enc_data = data[enc_offset:enc_offset + enc_size]
    total_slots = enc_size // 4
    all_slots = _parse_slots_gen6(enc_data, total_slots)

    # Check if any species present
    if not any(s[0] != 0 for s in all_slots):
        return None

    result = {'gen': 6, 'is_oras': is_oras, 'sections': {}}
    for name, start, count in sections:
        slots = all_slots[start:start + count]
        if any(s[0] != 0 for s in slots):
            result['sections'][name] = slots

    return result


def _format_encounter_gen6(decoded: dict, file_idx: int, name_resolver=None) -> str | None:
    """Format Gen VI encounter data as readable text."""
    if not decoded or 'sections' not in decoded:
        return None

    def resolve(species_id, form):
        if species_id == 0:
            return None
        if name_resolver:
            return name_resolver(species_id, form)
        suffix = f" (Form {form})" if form else ""
        return f"#{species_id}{suffix}"

    lines = []
    is_oras = decoded.get('is_oras', False)
    horde_rates = {'Horde A': 60, 'Horde B': 35, 'Horde C': 5}

    for section_name, slots in decoded['sections'].items():
        if section_name.startswith('Horde'):
            # Hordes: 5 pokemon appear at once, show as group
            names = []
            for sp, fm, min_lv, max_lv in slots:
                n = resolve(sp, fm)
                if n:
                    names.append(n)
            if not names:
                continue
            pct = horde_rates.get(section_name, '?')
            # Consolidate duplicates: "Pidgey x3, Spearow x2"
            from collections import Counter
            counts = Counter(names)
            parts = []
            for name, cnt in counts.items():
                parts.append(f"{name} x{cnt}" if cnt > 1 else name)
            lv = slots[0][2]  # hordes are same level
            lines.append(f"\n{section_name} ({pct}%) — Lv. {lv}:")
            lines.append(f"  {', '.join(parts)}")
        else:
            # Normal section: consolidate species, sum rates
            rates = _get_rates(len(slots))
            combined = {}
            levels = {}
            for i, (sp, fm, min_lv, max_lv) in enumerate(slots):
                if sp == 0:
                    continue
                name = resolve(sp, fm)
                if not name:
                    continue
                rate = rates[i] if i < len(rates) else 0
                combined[name] = combined.get(name, 0) + rate
                if name not in levels:
                    levels[name] = (min_lv, max_lv)
                else:
                    lo, hi = levels[name]
                    levels[name] = (min(lo, min_lv), max(hi, max_lv))

            if not combined:
                continue
            lines.append(f"\n{section_name}:")
            for name, rate in sorted(combined.items(), key=lambda x: -x[1]):
                lo, hi = levels[name]
                lv = f"Lv. {lo}-{hi}" if lo != hi else f"Lv. {lo}"
                lines.append(f"  {name:<22}{lv:<12}{rate:>3}%")

    return "\n".join(lines).strip() if lines else None


from Generations.sdk import (
    EV_YIELD_STATS, EXP_GROWTH_NAMES, EGG_GROUP_NAMES, GENDER_RATIOS,
    MOVE_CATEGORIES as MOVE_CATEGORIES_G6, EVOLUTION_METHODS,
)

# ============================================================
# Gen VI personal data — XY: 0x40 (64B), ORAS: 0x50 (80B)
# Bytes 0x00-0x27 identical to Gen VII (SM inherits XY)
# ============================================================

def _decode_personal_gen6(data: bytes, file_idx: int, is_oras: bool, text_tables: dict, tm_table: list = None):
    """Decode Gen VI personal data. Returns formatted string."""
    expected = 0x50 if is_oras else 0x40
    if len(data) < expected or data == b'\x00' * len(data):
        return None

    species_list = text_tables.get('species', [])
    type_list = text_tables.get('type_names', [])
    ability_list = text_tables.get('abilities', [])
    item_list = text_tables.get('items', [])
    moves_list = text_tables.get('moves', [])

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

    items = [struct.unpack_from('<h', data, 0x0C + i * 2)[0] for i in range(3)]
    gender = data[0x12]
    hatch_cycles = data[0x13]
    base_happiness = data[0x14]
    exp_growth = data[0x15]
    egg1, egg2 = data[0x16], data[0x17]

    # Abilities: u8 x 3 at 0x18-0x1A (NOT u16 like Gen V)
    ability_names = []
    for i in range(3):
        aid = data[0x18 + i]
        if aid > 0:
            name = ability_list[aid] if aid < len(ability_list) else f"ability#{aid}"
            ability_names.append(f"{name} (Hidden)" if i == 2 else name)

    forme_count = data[0x20]
    form_stats_idx = struct.unpack_from('<H', data, 0x1C)[0]
    height_dm = struct.unpack_from('<H', data, 0x24)[0]
    weight_hg = struct.unpack_from('<H', data, 0x26)[0]

    species_name = species_list[file_idx] if file_idx < len(species_list) else f"#{file_idx}"
    t1 = type_list[type1] if type1 < len(type_list) else f"type#{type1}"
    t2 = type_list[type2] if type2 < len(type_list) else f"type#{type2}"
    types_str = t1 if type1 == type2 else f"{t1} / {t2}"

    held_parts = []
    for label, item_id in zip(['common', 'rare', 'hidden'], items):
        if item_id > 0:
            iname = item_list[item_id] if item_id < len(item_list) else f"item#{item_id}"
            held_parts.append(f"{iname} ({label})")

    lines = [f"{species_name} (#{file_idx})", f"{types_str} | BST {bst}",
             f"HP {hp} | Atk {atk} | Def {dfn} | SpA {spa} | SpD {spd} | Spe {spe}",
             f"Abilities: {' / '.join(ability_names)}" if ability_names else "Abilities: ---"]
    lines.append(f"Gender: {GENDER_RATIOS.get(gender, f'ratio {gender}')} | Catch Rate: {catch_rate} | Hatch: {hatch_cycles} cycles | Happiness: {base_happiness}")
    eg1 = EGG_GROUP_NAMES.get(egg1, f"#{egg1}")
    eg2 = EGG_GROUP_NAMES.get(egg2, f"#{egg2}")
    lines.append(f"Growth: {EXP_GROWTH_NAMES.get(exp_growth, f'#{exp_growth}')} | Egg Groups: {eg1 if egg1 == egg2 else f'{eg1} / {eg2}'}")
    if held_parts:
        lines.append(f"Held Items: {' / '.join(held_parts)}")
    if evs:
        lines.append(f"EVs: {', '.join(evs)}")
    lines.append(f"Height: {height_dm / 10.0}m | Weight: {weight_hg / 10.0}kg")
    if forme_count > 1:
        lines.append(f"Forms: {forme_count} (base index {form_stats_idx})")

    if tm_table and len(data) >= 0x38:
        tm_flags = data[0x28:0x38]
        tms, hms = [], []
        for bit_idx, (label, move_id) in enumerate(tm_table):
            if tm_flags[bit_idx // 8] & (1 << (bit_idx % 8)):
                move_name = moves_list[move_id] if move_id < len(moves_list) else f"move#{move_id}"
                (hms if label.startswith('HM') else tms).append(f"{label[2:]} {move_name}")
        if tms: lines.append(f"TM: {' / '.join(tms)}")
        if hms: lines.append(f"HM: {' / '.join(hms)}")

    return "\n".join(lines)


# ============================================================
# Gen VI evolution — 8 slots x 6B = 48B
# Same slot format as Gen V but 8 slots instead of 7
# ============================================================

def _decode_evolution_gen6(data: bytes, file_idx: int, text_tables: dict):
    """Decode Gen VI evolution data. Returns formatted string."""
    if len(data) < 48 or data[:48] == b'\x00' * 48:
        return None
    species_list = text_tables.get('species', [])
    item_list = text_tables.get('items', [])
    moves_list = text_tables.get('moves', [])
    species_name = species_list[file_idx] if file_idx < len(species_list) else f"#{file_idx}"

    evo_lines = []
    for i in range(8):
        off = i * 6
        method = struct.unpack_from('<H', data, off)[0]
        param = struct.unpack_from('<H', data, off + 2)[0]
        target = struct.unpack_from('<H', data, off + 4)[0]
        if method == 0 and target == 0:
            continue
        method_name = EVOLUTION_METHODS.get(method, f"method#{method}")
        target_name = species_list[target] if target < len(species_list) else f"#{target}"
        if method in (4, 9, 10, 11, 21, 22, 23, 24, 25, 26, 27, 28, 31, 34, 35, 36):
            cond = f"Lv{param}" if method == 4 else f"Lv{param}, {method_name}"
        elif method in (6, 8, 17, 18):
            cond = item_list[param] if param < len(item_list) else f"item#{param}"
        elif method == 19:
            mn = moves_list[param] if param < len(moves_list) else f"move#{param}"
            cond = f"knows {mn}"
        elif method in (7, 20):
            sp = species_list[param] if param < len(species_list) else f"#{param}"
            cond = f"trade for {sp}" if method == 7 else f"with {sp} in party"
        elif method in (1, 2, 3): cond = method_name
        elif method == 5: cond = "trade"
        elif method == 16: cond = f"beauty {param}"
        elif method == 29: cond = "spin"
        else: cond = f"{method_name}" + (f" ({param})" if param else "")
        evo_lines.append(f"  -> {target_name} ({cond})")

    if not evo_lines:
        return None
    return f"{species_name} (#{file_idx}) \u2014 Evolutions\n" + "\n".join(evo_lines)


# ============================================================
# Gen VI move data — 0x22 = 34B
# Bytes 0x00-0x1D identical to Gen VII
# ============================================================

def _decode_move_data_gen6(data: bytes, file_idx: int, text_tables: dict):
    """Decode Gen VI move data. Returns formatted string."""
    if len(data) < 0x22 or data == b'\x00' * len(data):
        return None
    moves_list = text_tables.get('moves', [])
    type_list = text_tables.get('type_names', [])
    move_name = moves_list[file_idx] if file_idx < len(moves_list) else f"move#{file_idx}"
    type_name = type_list[data[0]] if data[0] < len(type_list) else f"type#{data[0]}"
    category = MOVE_CATEGORIES_G6.get(data[2], f"cat#{data[2]}")
    power, accuracy, pp = data[3], data[4], data[5]
    priority = struct.unpack_from('b', data, 6)[0]
    multi_hit = data[7]

    extras = []
    if priority != 0:
        extras.append(f"{'+' if priority > 0 else ''}{priority} priority")
    if multi_hit > 0:
        lo, hi = multi_hit & 0xF, (multi_hit >> 4) & 0xF
        extras.append(f"{lo}-{hi} hits" if lo != hi else f"{lo} hits")
    if data[0x0A] > 0:
        extras.append(f"{data[0x0A]}% effect")
    if data[0x0F] > 0:
        extras.append(f"{data[0x0F]}% flinch")

    pow_str = f"{power} pow" if power > 0 else "\u2014"
    acc_str = f"{accuracy}%" if accuracy <= 100 else "\u2014"
    line = f"{move_name} (#{file_idx})\n{type_name} | {category} | {pow_str} | {acc_str} | {pp} PP"
    if extras:
        line += f" | {' | '.join(extras)}"
    desc_list = text_tables.get('move_descriptions', [])
    if file_idx < len(desc_list) and desc_list[file_idx]:
        line += f"\n{desc_list[file_idx]}"
    return line


# ============================================================
# Gen VI trainer decoder
# XY: format(u8)+class(u8), ORAS: format(u16)+class(u16)+unk(u16)
# Pokemon: IVs(u8)+PID(u8)+level(u16)+species(u16)+form(u16)
# ============================================================

def _decode_trainer_gen6(trdata: bytes, trpoke: bytes, is_oras: bool, text_tables: dict):
    """Decode Gen VI trainer. Returns formatted string."""
    if not trdata or len(trdata) < 10:
        return None
    species_list = text_tables.get('species', [])
    item_list = text_tables.get('items', [])
    moves_list = text_tables.get('moves', [])
    class_list = text_tables.get('trainer_classes', [])

    if is_oras:
        fmt = struct.unpack_from('<H', trdata, 0)[0]
        tr_class = struct.unpack_from('<H', trdata, 2)[0]
        off = 6
    else:
        fmt, tr_class = trdata[0], trdata[1]
        off = 2

    has_item = (fmt >> 1) & 1
    has_moves = fmt & 1
    battle_type = trdata[off]
    num_pokemon = trdata[off + 1]
    items = [struct.unpack_from('<H', trdata, off + 2 + i * 2)[0] for i in range(4)]
    ai = trdata[off + 10]
    money = trdata[off + 15]

    class_name = class_list[tr_class] if tr_class < len(class_list) else f"class#{tr_class}"
    bt = {0: "Singles", 1: "Doubles", 2: "Multi"}.get(battle_type, f"type#{battle_type}")
    lines = [f"{class_name} (class #{tr_class})", f"Battle: {bt} | AI: {ai} | Money: {money}x"]
    battle_items = [item_list[it] if it < len(item_list) else f"item#{it}" for it in items if it > 0]
    if battle_items:
        lines.append(f"Items: {' / '.join(battle_items)}")

    if trpoke and num_pokemon > 0:
        poke_size = 8 + (2 if has_item else 0) + (8 if has_moves else 0)
        for i in range(num_pokemon):
            po = i * poke_size
            if po + 8 > len(trpoke): break
            ivs_byte = trpoke[po]
            level = struct.unpack_from('<H', trpoke, po + 2)[0]
            species = struct.unpack_from('<H', trpoke, po + 4)[0]
            form = struct.unpack_from('<H', trpoke, po + 6)[0]
            sp_name = species_list[species] if species < len(species_list) else f"#{species}"
            if form > 0: sp_name += f" (Form {form})"
            iv_val = ivs_byte * 31 // 255
            parts = [f"  {sp_name} Lv.{level} (IVs:{iv_val})"]
            extra = po + 8
            if has_item:
                item_id = struct.unpack_from('<H', trpoke, extra)[0]
                if item_id > 0:
                    parts[0] += f" @ {item_list[item_id] if item_id < len(item_list) else f'item#{item_id}'}"
                extra += 2
            if has_moves:
                mvs = [moves_list[struct.unpack_from('<H', trpoke, extra + m*2)[0]]
                       for m in range(4)
                       if struct.unpack_from('<H', trpoke, extra + m*2)[0] > 0
                       and struct.unpack_from('<H', trpoke, extra + m*2)[0] < len(moves_list)]
                if mvs: parts.append(f"    Moves: {' / '.join(mvs)}")
            lines.extend(parts)

    return "\n".join(lines)


# ============================================================
# Flipnote pairs and game info
# ============================================================

_GEN6_FLIPNOTE_PAIRS = {
    'Pokemon Omega Ruby & Alpha Sapphire': ['ECR', 'ECL'],
    'Pokemon X & Y': ['EKJ', 'EK2'],
}

_GEN6_GAME_INFO = {
    'ECR': {'gen': 6, 'platform': 'Nintendo 3DS', 'year': 2014, 'title': 'Pokemon Omega Ruby', 'narcs': {**_GEN6_ORAS}},
    'ECL': {'gen': 6, 'platform': 'Nintendo 3DS', 'year': 2014, 'title': 'Pokemon Alpha Sapphire', 'narcs': {**_GEN6_ORAS}},
    'EKJ': {'gen': 6, 'platform': 'Nintendo 3DS', 'year': 2013, 'title': 'Pokemon X', 'narcs': {**_GEN6_XY}},
    'EK2': {'gen': 6, 'platform': 'Nintendo 3DS', 'year': 2013, 'title': 'Pokemon Y', 'narcs': {**_GEN6_XY}},
}

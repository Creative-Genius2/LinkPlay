"""Alola_sm.py: Sun/Moon — Gen VII structural baseline.

Inherits Hoenn_remake.
Gen VII: Z-Moves, Alolan forms, 802 species, Mini/BinLinkerAccessor containers.
"""
from Generations.Hoenn_remake import Hoenn_remake

import struct


class Alola_sm(Hoenn_remake):
    """Sun/Moon. Gen VII baseline."""

    GAME_CODES = ('1Q1', '1Q2')
    TITLES = ('POKÉMON SUN', 'POKÉMON MOON')
    YEAR = 2016

    GEN = 7

    SPECIES_COUNT = 802

    TEXT_PATH = 'a/0/3/2'
    PERSONAL_PATH = 'a/0/1/7'
    LEARNSET_PATH = 'a/0/1/3'
    EVOLUTION_PATH = 'a/0/1/4'
    EGG_MOVES_PATH = 'a/0/1/2'
    BABY_SPECIES_PATH = 'a/0/1/8'
    ITEM_PATH = 'a/0/1/9'
    TRDATA_PATH = 'a/1/0/2'
    TRPOKE_PATH = 'a/1/0/3'
    MOVE_DATA_PATH = 'a/0/1/1'

    # ── Text table indices (shared SM/USUM) ──
    TEXT_TABLE_MAP = {
        'species': 60, 'items': 40, 'moves': 118,
        'abilities': 101, 'natures': 92, 'type_names': 112,
        'trainer_names': 110, 'trainer_classes': 111, 'location_names': 72,
    }

    FLIPNOTE_PAIRS = {
        'Pokémon Sun & Moon': ['1Q1', '1Q2'],
    }

    @staticmethod
    def _unpack_mini(data, ident="EA"):
        """Unpack a Mini/BinLinkerAccessor container. Returns list of byte arrays."""
        if not data or len(data) < 4:
            return None
        if chr(data[0]) != ident[0] or chr(data[1]) != ident[1]:
            return None
        count = struct.unpack_from('<H', data, 2)[0]
        if len(data) < 4 + (count + 1) * 4:
            return None
        ctr = 4
        start = struct.unpack_from('<I', data, ctr)[0]; ctr += 4
        entries = []
        for i in range(count):
            end = struct.unpack_from('<I', data, ctr)[0]; ctr += 4
            length = end - start
            entries.append(data[start:start + length])
            start = end
        return entries

    @staticmethod
    def _parse_encounter_table(self, t):
        """Parse a single 0x164-byte Gen VII encounter table."""
        if not t or len(t) < 0x164:
            return None
        min_level = t[0]
        max_level = t[1]
        if min_level == 0 and max_level == 0:
            return None
        rates = list(t[2:12])
        slot_sets = []
        for i in range(8):
            slots = []
            ofs = 0x0C + (i * 40)
            for j in range(10):
                val = struct.unpack_from('<I', t, ofs + j * 4)[0]
                species = val & 0x7FF
                form = (val >> 11) & 0x1F
                slots.append((species, form))
            slot_sets.append(slots)
        sos_weather = []
        for i in range(6):
            val = struct.unpack_from('<I', t, 0x14C + i * 4)[0]
            species = val & 0x7FF
            form = (val >> 11) & 0x1F
            sos_weather.append((species, form))
        return {
            'min_level': min_level, 'max_level': max_level,
            'rates': rates, 'slot_sets': slot_sets, 'sos_weather': sos_weather,
        }

    @staticmethod

    def decode_personal(self, data, file_idx, text_tables, tm_table=None):
        """Decode Gen VII personal data (0x54 bytes). SM/USUM."""
        if len(data) < 0x54 or data == b'\x00' * len(data):
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
        for i, stat in enumerate(self.EV_STAT_ORDER):
            val = (ev_raw >> (i * 2)) & 3
            if val: evs.append(f"+{val} {stat}")

        items = [struct.unpack_from('<h', data, 0x0C + i * 2)[0] for i in range(3)]
        gender = data[0x12]
        hatch_cycles = data[0x13]
        base_happiness = data[0x14]
        exp_growth = data[0x15]
        egg1, egg2 = data[0x16], data[0x17]

        # Gen VII: abilities are u8 (same as Gen VI)
        ability_names = []
        for i in range(3):
            aid = data[0x18 + i]
            if aid > 0:
                name = ability_list[aid] if aid < len(ability_list) else f"ability#{aid}"
                ability_names.append(f"{name} (Hidden)" if i == 2 else name)

        forme_count = data[0x20]
        form_stats_idx = struct.unpack_from('<H', data, 0x1C)[0]
        base_exp = struct.unpack_from('<H', data, 0x22)[0]
        height_dm = struct.unpack_from('<H', data, 0x24)[0]
        weight_hg = struct.unpack_from('<H', data, 0x26)[0]

        species_name = species_list[file_idx] if file_idx < len(species_list) else f"#{file_idx}"
        t1 = type_list[type1] if type1 < len(type_list) else f"type#{type1}"
        t2 = type_list[type2] if type2 < len(type_list) else f"type#{type2}"
        types_str = t1 if type1 == type2 else f"{t1} / {t2}"

        def _lbl(prefix, val):
            for k in dir(self):
                if k.startswith(prefix) and getattr(_SDK, k) == val:
                    return k[len(prefix):].replace('_', ' ').title()
            return f"#{val}"

        held_parts = []
        for label, item_id in zip(['common', 'rare', 'hidden'], items):
            if item_id > 0:
                iname = item_list[item_id] if item_id < len(item_list) else f"item#{item_id}"
                held_parts.append(f"{iname} ({label})")

        out = [f"{species_name} (#{file_idx})", f"{types_str} | BST {bst}",
               f"HP {hp} | Atk {atk} | Def {dfn} | SpA {spa} | SpD {spd} | Spe {spe}",
               f"Abilities: {' / '.join(ability_names)}" if ability_names else "Abilities: ---"]
        out.append(f"Gender: {_lbl('GENDER_', gender)} | Catch Rate: {catch_rate} | Hatch: {hatch_cycles} cycles | Happiness: {base_happiness}")
        out.append(f"Growth: {_lbl('GROWTH_', exp_growth)} | Egg Groups: {_lbl('EGG_', egg1)}" +
                   (f" / {_lbl('EGG_', egg2)}" if egg1 != egg2 else ""))
        if held_parts:
            out.append(f"Held Items: {' / '.join(held_parts)}")
        if evs:
            out.append(f"EVs: {', '.join(evs)}")
        out.append(f"Height: {height_dm / 10.0}m | Weight: {weight_hg / 10.0}kg | Base EXP: {base_exp}")
        if forme_count > 1:
            out.append(f"Forms: {forme_count} (base index {form_stats_idx})")

        # TM/HM compatibility (128 bits at 0x28)
        if tm_table and len(data) >= 0x38:
            tm_flags = data[0x28:0x38]
            tms, hms = [], []
            for bit_idx, (label, move_id) in enumerate(tm_table):
                if tm_flags[bit_idx // 8] & (1 << (bit_idx % 8)):
                    move_name = moves_list[move_id] if move_id < len(moves_list) else f"move#{move_id}"
                    (hms if label.startswith('HM') else tms).append(f"{label[2:]} {move_name}")
            if tms: out.append(f"TM: {' / '.join(tms)}")
            if hms: out.append(f"HM: {' / '.join(hms)}")

        # Z-Move signature (SM/USUM specific)
        z_item = struct.unpack_from('<H', data, 0x4C)[0]
        z_base = struct.unpack_from('<H', data, 0x4E)[0]
        z_move = struct.unpack_from('<H', data, 0x50)[0]
        if z_item > 0:
            z_item_name = item_list[z_item] if z_item < len(item_list) else f"item#{z_item}"
            z_base_name = moves_list[z_base] if z_base < len(moves_list) else f"move#{z_base}"
            z_move_name = moves_list[z_move] if z_move < len(moves_list) else f"move#{z_move}"
            out.append(f"Z-Move: {z_item_name} + {z_base_name} -> {z_move_name}")

        # Regional form flag
        if data[0x52]:
            out.append("Regional Form: Yes (Alolan)")

        return "\n".join(out)

    def decode_encounters(self, data):
        """Decode Gen VII encounter data from a zone encounter sub-file."""
        entries = Alola_sm._unpack_mini(data, "EA")
        if not entries:
            return None
        tables = []
        for entry in entries:
            if len(entry) < 4 + 0x164:
                continue
            day = Alola_sm._parse_encounter_table(entry[4:4 + 0x164])
            night_start = 4 + 0x164
            night = None
            if len(entry) >= night_start + 0x164:
                night = Alola_sm._parse_encounter_table(entry[night_start:night_start + 0x164])
            if day or night:
                tables.append({'day': day, 'night': night})
        if not tables:
            return None
        return {'gen': 7, 'tables': tables}

    @staticmethod
    def format_encounter_gen7(decoded, file_idx, name_resolver=None):
        """Format Gen VII encounter data as readable text."""
        if not decoded or 'tables' not in decoded:
            return None

        def resolve(species_id, form):
            if species_id == 0: return None
            if name_resolver: return name_resolver(species_id, form)
            suffix = f" (Form {form})" if form else ""
            return f"#{species_id}{suffix}"

        lines = []
        tables = decoded['tables']
        for t_idx, tbl in enumerate(tables):
            if len(tables) > 1:
                lines.append(f"\nTable {t_idx + 1}:")
            for time_key, label in [('day', 'Day'), ('night', 'Night')]:
                table = tbl.get(time_key)
                if not table: continue
                rates = table['rates']
                slots = table['slot_sets'][0]
                min_lv = table['min_level']
                max_lv = table['max_level']
                lv_str = f"Lv. {min_lv}-{max_lv}" if min_lv != max_lv else f"Lv. {min_lv}"
                combined = {}
                for i, (sp, fm) in enumerate(slots):
                    if sp == 0: continue
                    name = resolve(sp, fm)
                    if not name: continue
                    rate = rates[i] if i < len(rates) else 0
                    if name in combined: combined[name] += rate
                    else: combined[name] = rate
                if not combined: continue
                lines.append(f"\n{label} ({lv_str}):")
                for name, rate in sorted(combined.items(), key=lambda x: -x[1]):
                    lines.append(f"  {name:<22}{rate:>3}%")
                sos_names = []
                for sos_set in table['slot_sets'][1:]:
                    for sp, fm in sos_set:
                        if sp == 0: continue
                        n = resolve(sp, fm)
                        if n and n not in sos_names: sos_names.append(n)
                if sos_names:
                    lines.append(f"  SOS Allies: {', '.join(sos_names)}")
                weather_names = []
                for sp, fm in table['sos_weather']:
                    if sp == 0: continue
                    n = resolve(sp, fm)
                    if n and n not in weather_names: weather_names.append(n)
                if weather_names:
                    lines.append(f"  Weather SOS: {', '.join(weather_names)}")

        return "\n".join(lines).strip() if lines else None

    # ── Clean-name aliases ──
    FORMAT_ENCOUNTER = staticmethod(format_encounter_gen7.__func__)

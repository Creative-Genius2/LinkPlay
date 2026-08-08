"""Unova_prequel.py: Black/White — Gen V structural baseline.

Inherits Johto_remake. Gen V: fully 3D, reboot dex, seasons, triple/rotation battles.
"""
from Generations.Johto_remake import Johto_remake
import struct


class Unova_prequel(Johto_remake):
    """Black/White. Gen V baseline."""

    GAME_CODES = ('IRB', 'IRA')
    TITLES = ('POKÉMON BLACK', 'POKÉMON WHITE')
    YEAR = 2011

    PLATFORM = 'Nintendo DS'
    GEN = 5
    CONTAINER = 'nds'

    SPECIES_COUNT = 649

    TEXT_PATH = 'a/0/0/2'
    PERSONAL_PATH = 'a/0/1/6'
    LEARNSET_PATH = 'a/0/1/8'
    EVOLUTION_PATH = 'a/0/1/9'
    EGG_MOVES_PATH = 'a/0/2/0'
    MOVE_DATA_PATH = 'a/0/2/1'
    TRDATA_PATH = 'a/0/9/2'
    TRPOKE_PATH = 'a/0/9/3'
    ITEM_PATH = 'a/0/2/4'
    ENCOUNTER_PATH = 'a/1/2/6'
    SUBWAY_POKEMON_PATH = 'a/2/1/4'
    SUBWAY_TRAINERS_PATH = 'a/2/1/5'

    PERSONAL_SIZE = 76
    MOVE_DATA_SIZE = 36
    EVOLUTION_SIZE = 42

    TEXT_SPECIES = 70
    TEXT_MOVES = 359
    TEXT_ITEMS = 54
    TEXT_ABILITIES = 339
    TEXT_NATURES = 341
    TEXT_TRAINER_NAMES = 336
    TEXT_TRAINER_CLASSES = 337
    TEXT_TYPE_NAMES = 356
    TEXT_LOCATIONS = 89


    AI_FLAGS = {
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

    TRPOKE_FORMATS = {
        0: 8,   # base
        1: 16,  # + moves(8)
        2: 10,  # + item(2)
        3: 18,  # + item(2) + moves(8)
    }

    # # decode_encounters
    @staticmethod


    def bootstrap_text(self, narc_files):
        """Gen V: derive MULT from species file, then decrypt all."""
        from xoleon import decode_gen5_text, derive_gen5_mult
        # MULT derives itself: species file (TEXT_SPECIES), encrypted[0] ^ 0x0042 = 4*MULT
        mult = derive_gen5_mult(narc_files[self.TEXT_SPECIES])
        if mult == 0:
            raise ValueError("Could not derive text MULT from species file")
        self.text_mult = mult
        for i, f in enumerate(narc_files):
            self.text_tables[i] = decode_gen5_text(f, mult)
        self._map_text_tables()

    def decode_personal(self, data, file_idx, text_tables, tm_table=None):
        """Decode Gen V personal data (76 bytes). BW/B2W2."""
        if len(data) < 76 or data == b'\x00' * len(data):
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

        # Gen V: 3 items, gender at 0x12, abilities 3x u8 at 0x18-0x1A
        items = [struct.unpack_from('<h', data, 0x0C + i * 2)[0] for i in range(3)]
        gender = data[0x12]
        hatch_cycles = data[0x13]
        base_happiness = data[0x14]
        exp_growth = data[0x15]
        egg1, egg2 = data[0x16], data[0x17]

        ability_names = []
        for i in range(3):
            aid = data[0x18 + i]
            if aid > 0:
                name = ability_list[aid] if aid < len(ability_list) else f"ability#{aid}"
                ability_names.append(f"{name} (Hidden)" if i == 2 else name)

        forme_count = data[0x20] if len(data) > 0x20 else 1
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
        out.append(f"Height: {height_dm / 10.0}m | Weight: {weight_hg / 10.0}kg")
        if forme_count > 1:
            out.append(f"Forms: {forme_count}")

        # TM flags at 0x28 (16 bytes = 128 bits)
        if tm_table and len(data) >= 0x38:
            tm_flags = data[0x28:0x38]
            tms, hms = [], []
            for bit_idx, (label, move_id) in enumerate(tm_table):
                if tm_flags[bit_idx // 8] & (1 << (bit_idx % 8)):
                    move_name = moves_list[move_id] if move_id < len(moves_list) else f"move#{move_id}"
                    (hms if label.startswith('HM') else tms).append(f"{label[2:]} {move_name}")
            if tms: out.append(f"TM: {' / '.join(tms)}")
            if hms: out.append(f"HM: {' / '.join(hms)}")

        return "\n".join(out)

    def decode_encounters(self, data: bytes, text_tables: dict = None) -> dict:
        """Decode Gen V encounter data (BW/B2W2).
        232 bytes per season. Species u16 encodes form in upper bits (& 0x7FF)."""
        if len(data) < 232:
            return None

        species_list = (text_tables or {}).get('species', [])
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
                    name = species_list[species_id] if species_id < len(species_list) else f"#{species_id}"
                    form_label = Unova_prequel.FORM_NAMES.get((species_id, form))
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
    FLIPNOTE_PAIRS = {
        # Gen V
        'Pokémon Black & White': ['IRB', 'IRA'],
        'Pokémon Black & White 2': ['IRE', 'IRD'],
    }


    TRAINER_LOCATIONS = {
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

    }


    CLASS_LOCATIONS = {
        "IRB": {"Elite Four": "Pokémon League", "Champion": "Pokémon League", "Subway Boss": "Battle Subway"},
        "IRA": "IRB",
    }


    TM_SEARCH = {
        5: (bytes([0xD4, 0x01, 0x51, 0x01, 0xD9, 0x01, 0x5B, 0x01]), 101),  # 95 TMs + 6 HMs
    }


    FORM_NAMES = {
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

    def _format_encounter_gen5(self, decoded, file_idx):
        """Format Gen V encounter data as template text."""
        seasons_data = decoded.get('seasons', None)
        if seasons_data:
            return _format_encounter_gen5_seasonal(seasons_data, file_idx)

        lines = []
        location = decoded.get('location', '')
        if location:
            lines.append(f"Location: {location}\n")

        sections = [
            ('grass', 'Grass (Default)', self.GRASS_SLOT_RATES),
            ('double_grass', 'Dark Grass', self.GRASS_SLOT_RATES),
            ('special_grass', 'Shaking Grass', self.GRASS_SLOT_RATES),
            ('surf', 'Surf (Default)', self.WATER_SLOT_RATES),
            ('special_surf', 'Rippling Water', self.WATER_SLOT_RATES),
            ('fishing', 'Fishing (Default)', self.WATER_SLOT_RATES),
            ('special_fishing', 'Fishing (Rippling)', self.WATER_SLOT_RATES),
        ]
        for key, header, rates in sections:
            entries = decoded.get(key, [])
            if entries:
                section = _format_section(entries, rates, header)
                if section:
                    lines.append(section)

        return "\n".join(lines).strip() if lines else None



    def _format_encounter_gen5_seasonal(self, seasons, file_idx):
        """Format Gen V seasonal encounters with inline season notes."""
        section_types = [
            ('grass', 'Grass (Default)', self.GRASS_SLOT_RATES),
            ('double_grass', 'Dark Grass', self.GRASS_SLOT_RATES),
            ('special_grass', 'Shaking Grass', self.GRASS_SLOT_RATES),
            ('surf', 'Surf (Default)', self.WATER_SLOT_RATES),
            ('special_surf', 'Rippling Water', self.WATER_SLOT_RATES),
            ('fishing', 'Fishing (Default)', self.WATER_SLOT_RATES),
            ('special_fishing', 'Fishing (Rippling)', self.WATER_SLOT_RATES),
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

    def discover_enc_loc(self, rom, enc_path):
        """Gen 5: enc->loc via species fingerprinting + story-progression formula.
        loc = enc + C0 - N where C0 = loc_anchor - enc_anchor."""
        import ndspy.narc as _n
        try:
            enc_narc = _n.NARC(rom.getFileByName(enc_path))
        except Exception:
            return {}

        def gsp(f):
            sp, lv = set(), 99
            for j in range(12):
                p = 8 + j * 4
                if p + 4 <= len(f):
                    s = struct.unpack_from('<H', f, p)[0] & 0x7FF
                    if s: sp.add(s); lv = min(lv, f[p + 2])
            return sp, lv

        A = [(frozenset([504, 509]),  15,  4,  7),
             (frozenset([504, 506]), 14,  0,  5),
             (frozenset([39, 505, 507]), 14, 50, 99),
             (frozenset([193,505,507,509,520,523]),16,10,99),
             (frozenset([504, 509]), 124, 0,  4),
             (frozenset([55,183,337,338,591]),127,35,55),
             (frozenset([592, 458, 223]), 126, 0, 99)]

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

    def decode_item(self, data, file_idx, text_tables):
        """Decode item data (0x24 / 36 bytes). Gen V through LGPE share this layout."""
        if len(data) < 0x24:
            return None

        items_list = text_tables.get('items', [])
        desc_list = text_tables.get('item_descriptions', [])

        name = items_list[file_idx] if file_idx < len(items_list) else f'Item #{file_idx}'
        description = desc_list[file_idx] if file_idx < len(desc_list) else ''

        raw_price = struct.unpack_from('<H', data, 0x00)[0]
        buy = raw_price * 10
        sell = raw_price * 5

        held_effect = data[0x02]
        held_arg = data[0x03]
        fling_effect = data[0x05]
        fling_power = data[0x06]
        natural_gift_power = data[0x07]

        packed = struct.unpack_from('<H', data, 0x08)[0]
        pocket = (packed >> 7) & 0xF

        field_effect = data[0x0A]
        battle_effect = data[0x0B]
        consumable = data[0x0E]
        sort_index = data[0x0F]

        cure_inflict = data[0x10]

        # Stat boosts packed in nibbles (0x11-0x14)
        boost0, boost1, boost2, boost3 = data[0x11], data[0x12], data[0x13], data[0x14]
        boosts = {
            'Atk': boost0 >> 4, 'Def': boost1 & 0xF,
            'SpA': boost1 >> 4, 'SpD': boost2 & 0xF,
            'Spe': boost2 >> 4, 'Acc': boost3 & 0xF,
            'Crit': (boost3 >> 4) & 3,
        }

        # EV yields (signed bytes, 0x17-0x1C)
        ev_names = ['HP', 'Atk', 'Def', 'Spe', 'SpA', 'SpD']
        evs = [struct.unpack_from('<b', data, 0x17 + i)[0] for i in range(6)]

        heal_amount = data[0x1D]
        pp_gain = data[0x1E]
        friendship = [struct.unpack_from('<b', data, 0x1F + i)[0] for i in range(3)]

        POCKET_NAMES = {1: 'Items', 2: 'Medicine', 3: 'TMs', 4: 'Berries', 5: 'Key Items', 6: 'Battle Items', 7: 'Poké Balls'}
        pocket_name = POCKET_NAMES.get(pocket, f'pocket#{pocket}')

        out = [name]
        out.append(f'Pocket: {pocket_name}')
        if buy > 0:
            out.append(f'Buy: ${buy:,} | Sell: ${sell:,}')
        else:
            out.append('Cannot be bought')

        if fling_power:
            out.append(f'Fling: {fling_power} power (effect {fling_effect})')
        if natural_gift_power:
            ng_type = packed & 0x1F
            out.append(f'Natural Gift: {natural_gift_power} power, type {ng_type}')

        if held_effect:
            out.append(f'Held effect: {held_effect} (arg {held_arg})')

        active_boosts = {k: v for k, v in boosts.items() if v}
        if active_boosts:
            parts = [f'{k}+{v}' for k, v in active_boosts.items()]
            out.append(f'Boosts: {", ".join(parts)}')

        active_evs = [(ev_names[i], evs[i]) for i in range(6) if evs[i]]
        if active_evs:
            parts = [f'{n}{v:+d}' for n, v in active_evs]
            out.append(f'EVs: {", ".join(parts)}')

        if heal_amount:
            out.append(f'Heal: {heal_amount}')
        if pp_gain:
            out.append(f'PP restore: {pp_gain}')

        active_friend = [f for f in friendship if f]
        if active_friend:
            out.append(f'Friendship: {"/".join(str(f) for f in friendship)}')

        if description:
            out.append('')
            out.append(description)

        return "\n".join(out)


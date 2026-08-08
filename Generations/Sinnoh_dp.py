"""Sinnoh_dp.py: Diamond/Pearl — Gen IV structural baseline.

Inherits Hoenn_rse. Gen IV jump: NDS platform, NARC filesystem,
text encryption (XOR cipher + 9-bit packing), 493 species,
physical/special split per move, online play.
"""
from Generations.Hoenn_rse import Hoenn_rse
import struct
from xoleon import (
    _GEN4_HIRAGANA, _GEN4_KATAKANA, _GEN4_FULLWIDTH_SYMBOLS, _GEN4_SPECIAL,
    _get_gen4_char, decode_gen4_text,
)


class Sinnoh_dp(Hoenn_rse):
    """Diamond/Pearl. Gen IV baseline."""

    GAME_CODES = ('ADA', 'APA')
    TITLES = ('POKÉMON DIAMOND', 'POKÉMON PEARL')
    YEAR = 2007

    PLATFORM = 'Nintendo DS'
    GEN = 4
    CONTAINER = 'nds'

    SPECIES_COUNT = 493

    TEXT_PATH = 'msgdata/msg.narc'
    PERSONAL_PATH = 'poketool/personal/personal.narc'
    LEARNSET_PATH = 'poketool/personal/wotbl.narc'
    EVOLUTION_PATH = 'poketool/personal/evo.narc'
    BABY_SPECIES_PATH = 'poketool/personal/pms.narc'
    MOVE_DATA_PATH = 'poketool/waza/waza_tbl.narc'
    TRDATA_PATH = 'poketool/trainer/trdata.narc'
    TRPOKE_PATH = 'poketool/trainer/trpoke.narc'
    ITEM_PATH = 'itemtool/itemdata/item_data.narc'
    CONTEST_PATH = 'contest/data/contest_data.narc'
    BATTLE_TOWER_POKEMON_PATH = 'battle/b_tower/btdpm.narc'
    BATTLE_TOWER_TRAINERS_PATH = 'battle/b_tower/btdtr.narc'
    ENCOUNTER_PATH_D = 'fielddata/encountdata/d_enc_data.narc'
    ENCOUNTER_PATH_P = 'fielddata/encountdata/p_enc_data.narc'

    PERSONAL_SIZE = 44
    MOVE_DATA_SIZE = 16
    EVOLUTION_SIZE = 44

    TEXT_SPECIES = 362
    TEXT_MOVES = 588
    TEXT_ITEMS = 344
    TEXT_ABILITIES = 552
    TEXT_TYPE_NAMES = 565
    TEXT_NATURES = 190
    TEXT_TRAINER_CLASSES = 560
    TEXT_TRAINER_NAMES = 559
    TEXT_LOCATIONS = 382

    AI_FLAGS = {
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

    TRPOKE_FORMATS = {
        0: 6,   # base
        1: 14,  # + moves(8)
        2: 8,   # + item(2)
        3: 18,  # + item(2) + moves(8) + pad(2)
    }
    @staticmethod


    def get_narc(self, narc_path):
        import ndspy.narc
        key = narc_path
        if key not in self._narc_cache:
            data = self.rom.getFileByName(narc_path)
            self._narc_cache[key] = ndspy.narc.NARC(data)
        return self._narc_cache[key]

    def read_file(self, path):
        p = path.strip('/')
        if p.lower() == 'arm9.bin':
            return bytes(self.arm9_data)
        if p.lower() == 'arm7.bin':
            return bytes(self.arm7_data)
        # Overlay: overlay/overlay_XXXX.bin or ovN
        if 'overlay' in p.lower() or p.startswith('ov'):
            import re
            m = re.search(r'(\d+)', p)
            if m:
                ov_id = int(m.group(1))
                if ov_id in self.overlays:
                    return bytes(self.overlays[ov_id])
                raise ValueError(f"Overlay {ov_id} not found")
        if ':' in p:
            narc_path, file_idx = p.rsplit(':', 1)
            file_idx = int(file_idx)
            narc = self.get_narc(narc_path.lstrip('/'))
            if file_idx >= len(narc.files):
                raise ValueError(f"Index {file_idx} out of range (NARC has {len(narc.files)} files)")
            return bytes(narc.files[file_idx])
        return bytes(self.rom.getFileByName(p))

    def bootstrap_text(self, narc_files):
        """Gen IV: each file has its own seed. decode_gen4_text handles it."""
        from xoleon import decode_gen4_text
        for i, f in enumerate(narc_files):
            self.text_tables[i] = decode_gen4_text(f)
        self._map_text_tables()

    def decode_personal(self, data, file_idx, text_tables, tm_table=None):
        """Decode Gen IV personal data (44 bytes). DP/Pt/HGSS."""
        if len(data) < 44 or data == b'\x00' * len(data):
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

        # Gen IV: 2 items, gender at 0x10, abilities 2x u8 at 0x16-0x17
        items = [struct.unpack_from('<H', data, 0x0C + i * 2)[0] for i in range(2)]
        gender = data[0x10]
        hatch_cycles = data[0x11]
        base_happiness = data[0x12]
        exp_growth = data[0x13]
        egg1, egg2 = data[0x14], data[0x15]
        ability_names = [ability_list[a] if a < len(ability_list) else f"ability#{a}" for a in [data[0x16], data[0x17]] if a > 0]

        species_name = species_list[file_idx] if file_idx < len(species_list) else f"#{file_idx}"
        t1 = type_list[type1] if type1 < len(type_list) else f"type#{type1}"
        t2 = type_list[type2] if type2 < len(type_list) else f"type#{type2}"
        types_str = t1 if type1 == type2 else f"{t1} / {t2}"

        held_parts = []
        for label, item_id in zip(['common', 'rare'], items):
            if item_id > 0:
                iname = item_list[item_id] if item_id < len(item_list) else f"item#{item_id}"
                held_parts.append(f"{iname} ({label})")

        def _lbl(prefix, val):
            for k in dir(self):
                if k.startswith(prefix) and getattr(self, k) == val:
                    return k[len(prefix):].replace('_', ' ').title()
            return f"#{val}"

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

        # TM flags at 0x1C (16 bytes = 128 bits)
        if tm_table and len(data) >= 0x2C:
            tm_flags = data[0x1C:0x2C]
            tms, hms = [], []
            for bit_idx, (label, move_id) in enumerate(tm_table):
                if tm_flags[bit_idx // 8] & (1 << (bit_idx % 8)):
                    move_name = moves_list[move_id] if move_id < len(moves_list) else f"move#{move_id}"
                    (hms if label.startswith('HM') else tms).append(f"{label[2:]} {move_name}")
            if tms: out.append(f"TM: {' / '.join(tms)}")
            if hms: out.append(f"HM: {' / '.join(hms)}")

        return "\n".join(out)

    def decode_encounters(self, data: bytes, text_tables: dict = None) -> dict:
        """Decode Gen IV DP/Pt encounter data (424 bytes).
        Land: rate(u32) + 12 grass slots(u32 level + u32 species) + replacements.
        Water: 5 sections × (rate u32 + 5 × {max_lv u8, min_lv u8, pad u16, species u16, pad u16})."""
        if len(data) != 424:
            return None

        species_list = (text_tables or {}).get('species', [])
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
                grass.append({"species": species_list[species_id] if species_id < len(species_list) else f"#{species_id}", "level": level})
            if grass:
                result["grass"] = grass
                result["grass_rate"] = grass_rate

        # Replacement species (offset 100): swarm(2), day(2), night(2), radar(4)
        def read_replacements(offset, count):
            species = []
            for i in range(count):
                sid = struct.unpack_from("<I", data, offset + i * 4)[0]
                if sid > 0:
                    species.append(species_list[sid] if sid < len(species_list) else f"#{sid}")
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
                    name = species_list[species_id] if species_id < len(species_list) else f"#{species_id}"
                    lvl = f"{min_lv}-{max_lv}" if min_lv != max_lv else str(min_lv)
                    entries.append({"species": name, "level": lvl})
                if entries:
                    result[section_name] = entries
            water_offset += 40  # 5 slots × 8 bytes

        return result if result else None
    FLIPNOTE_PAIRS = {
        # Gen IV
        'Pokémon Diamond & Pearl': ['ADA', 'APA'],
        'Pokémon Platinum': ['CPU'],
        'Pokémon HeartGold & SoulSilver': ['IPK', 'IPG'],
    }


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
    }


    CLASS_LOCATIONS = {
        "ADA": {"Elite Four": "Pokémon League", "Champion": "Pokémon League"},
        "APA": "ADA",
        "CPU": {"Elite Four": "Pokémon League", "Champion": "Pokémon League", "Tower Tycoon": "Battle Tower"},
        "IPK": {"Elite Four": "Indigo Plateau", "Champion": "Indigo Plateau",
                "Brock": "Pewter Gym", "Misty": "Cerulean Gym", "Lt. Surge": "Vermilion Gym",
                "Erika": "Celadon Gym", "Janine": "Fuchsia Gym", "Sabrina": "Saffron Gym",
                "Blaine": "Seafoam Gym", "Blue": "Viridian Gym"},
        "IPG": "IPK",
    }


    TM_SEARCH = {
        4: (bytes([0x08, 0x01, 0x51, 0x01, 0x60, 0x01, 0x5B, 0x01]), 100),  # 92 TMs + 8 HMs
    }

    def decode_trpoke(self, data, text_tables, trainer_data=None) -> dict:
        """Decode a TRPoke file into human-readable format using text_tables.
        Gen IV: iv(u16) level(u16) species(u16) = 6B base."""
        if len(data) == 0:
            return {"pokemon": []}

        species_list = text_tables.get('species', [])
        moves_list = text_tables.get('moves', [])
        items_list = text_tables.get('items', [])
        natures_list = text_tables.get('natures', [])

        formats = getattr(self, 'TRPOKE_FORMATS', {})

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

        pokemon = []
        for i in range(num_pokemon):
            off = i * pokemon_size
            if off + pokemon_size > len(data):
                break

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



    def decode_trdata(self, data, file_idx, text_tables) -> dict:
        """Decode a TRData entry. Format detected by size — no gen check needed.
        16B = Gen IV (flags, class, battle_type, npoke, items×4, ai_flags)
        20B = Gen V  (+ pad, prize_base, area_id, pad)"""
        if len(data) < 16:
            return None

        trainer_names = text_tables.get('trainer_names', [])
        trainer_classes = text_tables.get('trainer_classes', [])
        items_list = text_tables.get('items', [])

        BATTLE_TYPES = {0: "Single", 1: "Double", 2: "Triple", 3: "Rotation"}

        # Gen IV/V: flags at 0, class at 1
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
        ai_flags = self.decode_ai_flags(ai_flags_raw)
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
        elif file_idx is not None and file_idx < len(trainer_names):
            name = trainer_names[file_idx].strip()
            if name:
                result["name"] = name



        return result








    def decode_learnset(self, data, file_idx, text_tables):
        """Decode learnset. All gens: u8 pairs (Gen 1/2), packed u16 (Gen 3/4), paired u16×2 (Gen 5)."""
        if len(data) < 2:
            return None
        species_list = text_tables.get('species', [])
        moves_list = text_tables.get('moves', [])
        species_name = species_list[file_idx] if file_idx < len(species_list) else f"#{file_idx}"

        moves = []
        for i in range(0, len(data) - 1, 2):
            raw = struct.unpack_from('<H', data, i)[0]
            if raw == 0xFFFF or raw == 0: break
            move_id = raw & 0x1FF
            level = (raw >> 9) & 0x7F
            move_name = moves_list[move_id] if move_id < len(moves_list) else f"move#{move_id}"
            moves.append((level, move_name))


        lines = [f"{species_name} (#{file_idx}) — Learnset"]
        for level, move_name in moves:
            lines.append(f"  Lv{level:<4}{move_name}")
        if not moves:
            lines.append("  (none)")
        return "\n".join(lines)




    def decode_evolution(self, data, file_idx, text_tables):
        """Decode evolution table. Returns positional text."""
        if len(data) < 6 or data[:min(42,len(data))] == b'\x00' * min(42,len(data)):
            return None
        species_list = text_tables.get('species', [])
        item_list = text_tables.get('items', [])
        moves_list = text_tables.get('moves', [])
        species_name = species_list[file_idx] if file_idx < len(species_list) else f"#{file_idx}"
        evo_lines = []
        slot_count, slot_size = 7, 6  # Gen IV: 7 slots x 6 bytes
        for i in range(slot_count):
            off = i * slot_size
            if off + slot_size > len(data): break
            method = struct.unpack_from('<H', data, off)[0]
            param = struct.unpack_from('<H', data, off + 2)[0]
            target = struct.unpack_from('<H', data, off + 4)[0]
            if method == 0 and target == 0:
                continue
            method_name = next((n[4:].lower().replace('_',' ') for n in dir(self) if n.startswith('EVO_') and getattr(self,n)==method), f"method#{method}")
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



    def decode_move(self, data, file_idx, text_tables):
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
            category = {0: 'Status', 1: 'Physical', 2: 'Special'}.get(data[2], f"cat#{data[2]}")
            power = data[3]
            move_type = data[4]
            accuracy = data[5]
            pp = data[6]
            type_name = type_list[move_type] if move_type < len(type_list) else f"type#{move_type}"
            extras = []
        elif len(data) >= 36:
            move_type = data[0]
            category = {0: 'Status', 1: 'Physical', 2: 'Special'}.get(data[2], f"cat#{data[2]}")
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



    def decode_item(self, data, file_idx, text_tables):
        """Decode item data. Gen IV: 34 bytes, price direct. Gen V: 36 bytes, price * 10."""
        items_list = text_tables.get('items', [])
        desc_list = text_tables.get('item_descriptions', [])

        name = items_list[file_idx] if file_idx < len(items_list) else f'Item #{file_idx}'
        description = desc_list[file_idx] if file_idx < len(desc_list) else ''

        if len(data) < 10:
            return None

        raw_price = struct.unpack_from('<H', data, 0)[0]
        price = raw_price  # Gen IV: price is direct

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



    def decode_contest(self, data, file_idx, text_tables):
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


    def _consolidate_slots(self, entries, rates):
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



    def _format_encounter_dpp(self, decoded, file_idx):
        """Format DPPt encounter data as template text."""
        lines = []
        grass = decoded.get('grass', [])
        if grass:
            section = self._format_section(grass, self.GRASS_SLOT_RATES, "Grass (Default)")
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
                section = self._format_section(entries, self.WATER_SLOT_RATES, header)
                if section:
                    lines.append(section)
        return "\n".join(lines).strip() if lines else None



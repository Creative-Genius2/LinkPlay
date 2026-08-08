"""Kalos_prequel.py: X/Y — Gen VI structural baseline.

Inherits Unova_sequel. Gen VI jump: 3DS platform, GARC containers,
full 3D, Mega Evolution, Fairy type, 721 species.
"""
from Generations.Unova_sequel import Unova_sequel

import struct


class Kalos_prequel(Unova_sequel):
    """X/Y. Gen VI baseline."""

    GAME_CODES = ('EKJ', 'EK2')
    TITLES = ('POKÉMON X', 'POKÉMON Y')
    YEAR = 2013

    PLATFORM = 'Nintendo 3DS'
    GEN = 6
    CONTAINER = '3ds'

    SPECIES_COUNT = 721

    TEXT_PATH = 'a/0/7/2'
    PERSONAL_PATH = 'a/2/1/8'
    LEARNSET_PATH = 'a/2/1/4'
    EVOLUTION_PATH = 'a/2/1/5'
    EGG_MOVES_PATH = 'a/2/1/3'
    MEGA_EVOS_PATH = 'a/2/1/6'
    BABY_SPECIES_PATH = 'a/2/1/9'
    ITEM_PATH = 'a/2/2/0'
    MOVE_DATA_PATH = 'a/2/1/2'
    ENCOUNTER_PATH = 'a/0/1/2'
    TRDATA_PATH = 'a/0/3/8'
    TRPOKE_PATH = 'a/0/4/0'
    TRCLASS_PATH = 'a/0/3/9'
    MAISON_POKEMON_NORMAL_PATH = 'a/2/0/3'
    MAISON_TRAINERS_NORMAL_PATH = 'a/2/0/4'
    MAISON_POKEMON_SUPER_PATH = 'a/2/0/5'
    MAISON_TRAINERS_SUPER_PATH = 'a/2/0/6'

    PERSONAL_SIZE = 0x50

    # ── Evolution methods (Gen VI adds to Gen V set) ──
    EVO_LEVEL_UPSIDE_DOWN = 31
    EVO_LEVEL_DARK_TYPE_PARTY = 32
    EVO_LEVEL_RAIN = 33
    EVO_LEVEL_DAY = 34
    EVO_LEVEL_NIGHT = 35
    EVO_LEVEL_FEMALE = 36
    EVO_LEVEL_GAME_A = 37
    EVO_LEVEL_GAME_B = 38

    FLIPNOTE_PAIRS = {
        'Pokemon X & Y': ['EKJ', 'EK2'],
    }

    # ── XY encounter sections: 94 slots x 4 bytes = 0x178 ──
    XY_SECTIONS = [
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

    # ── ORAS encounter sections: 61 slots x 4 bytes = 0xF4 ──
    ORAS_SECTIONS = [
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

    RATES_12 = [20, 20, 10, 10, 10, 10, 5, 5, 4, 4, 1, 1]
    RATES_5  = [50, 30, 15, 4, 1]
    RATES_3  = [60, 35, 5]


    def get_narc(self, narc_path):
        from xoleon import read_garc_all
        key = narc_path
        if key not in self._narc_cache:
            if narc_path not in self.romfs_files:
                raise ValueError(f"GARC not found: {narc_path}")
            files = read_garc_all(self.romfs_fh, self.romfs_files[narc_path][0])
            class _GarcNarc:
                pass
            obj = _GarcNarc()
            obj.files = files
            self._narc_cache[key] = obj
        return self._narc_cache[key]

    def read_file(self, path):
        from xoleon import read_garc_sub
        p = path.strip('/')
        if ':' in p:
            gp, fi = p.rsplit(':', 1)
            gp = gp.lstrip('/')
            fi = int(fi)
            # WD flat files: single GARC sub packing multiple entries
            wd = getattr(self, '_wd_cache', {}).get(gp)
            if wd is None:
                data, total = read_garc_sub(self.romfs_fh, self.romfs_files[gp][0], 0)
                if data and len(data) > 4 and data[0:2] == b'WD':
                    count = struct.unpack_from('<H', data, 2)[0]
                    offsets = [struct.unpack_from('<I', data, 4 + i*4)[0] for i in range(count + 1)]
                    wd = [data[offsets[i]:offsets[i+1]] for i in range(count)]
                    if not hasattr(self, '_wd_cache'):
                        self._wd_cache = {}
                    self._wd_cache[gp] = wd
            if wd is not None:
                if fi >= len(wd):
                    raise ValueError(f"Index {fi} out of range (WD has {len(wd)} entries)")
                return wd[fi]
            data, total = read_garc_sub(self.romfs_fh, self.romfs_files[gp][0], fi)
            if data is None:
                raise ValueError(f"Index {fi} out of range (GARC has {total} files)")
            return data
        if p not in self.romfs_files:
            raise ValueError(f"File not found in RomFS: {p}")
        off, sz = self.romfs_files[p]
        self.romfs_fh.seek(off)
        return self.romfs_fh.read(sz)

    def bootstrap_text(self, narc_files):
        """Gen VI/VII: same cipher as Gen V, MULT always 0x2983."""
        from xoleon import decode_gen5_text
        self.text_mult = 0x2983
        for i, f in enumerate(narc_files):
            self.text_tables[i] = decode_gen5_text(f, 0x2983)
        self._map_text_tables()

    @staticmethod
    def _get_rates(count):
        if count == 12: return Kalos_prequel.RATES_12
        if count == 5:  return Kalos_prequel.RATES_5
        if count == 3:  return Kalos_prequel.RATES_3
        return [100 // count] * count

    @staticmethod
    def _parse_slots_gen6(self, data, num_slots):
        """Parse Gen VI encounter slots. Each slot = 4 bytes: u16(species|form<<11) + u8 min + u8 max."""
        slots = []
        for i in range(num_slots):
            ofs = i * 4
            if ofs + 4 > len(data): break
            raw = struct.unpack_from('<H', data, ofs)[0]
            species = raw & 0x7FF
            form = raw >> 11
            min_lv = data[ofs + 2]
            max_lv = data[ofs + 3]
            slots.append((species, form, min_lv, max_lv))
        return slots

    @staticmethod
    def decode_encounters(self, data, is_oras=False):
        """Decode Gen VI encounter data from a GARC sub-file."""
        if not data or len(data) < 0x14:
            return None
        ptr = struct.unpack_from('<I', data, 0x10)[0]
        enc_offset = ptr + (0x0E if is_oras else 0x10)
        enc_size = 0xF4 if is_oras else 0x178
        sections = Kalos_prequel.ORAS_SECTIONS if is_oras else Kalos_prequel.XY_SECTIONS
        if enc_offset + enc_size > len(data):
            return None
        enc_data = data[enc_offset:enc_offset + enc_size]
        total_slots = enc_size // 4
        all_slots = Kalos_prequel._parse_slots_gen6(enc_data, total_slots)
        if not any(s[0] != 0 for s in all_slots):
            return None
        result = {'gen': 6, 'is_oras': is_oras, 'sections': {}}
        for name, start, count in sections:
            slots = all_slots[start:start + count]
            if any(s[0] != 0 for s in slots):
                result['sections'][name] = slots
        return result

    @staticmethod
    def format_encounter_gen6(decoded, file_idx, name_resolver=None):
        """Format Gen VI encounter data as readable text."""
        if not decoded or 'sections' not in decoded:
            return None

        def resolve(species_id, form):
            if species_id == 0: return None
            if name_resolver: return name_resolver(species_id, form)
            suffix = f" (Form {form})" if form else ""
            return f"#{species_id}{suffix}"

        lines = []
        is_oras = decoded.get('is_oras', False)
        horde_rates = {'Horde A': 60, 'Horde B': 35, 'Horde C': 5}

        for section_name, slots in decoded['sections'].items():
            if section_name.startswith('Horde'):
                names = []
                for sp, fm, min_lv, max_lv in slots:
                    n = resolve(sp, fm)
                    if n: names.append(n)
                if not names: continue
                pct = horde_rates.get(section_name, '?')
                from collections import Counter
                counts = Counter(names)
                parts = []
                for name, cnt in counts.items():
                    parts.append(f"{name} x{cnt}" if cnt > 1 else name)
                lv = slots[0][2]
                lines.append(f"\n{section_name} ({pct}%) \u2014 Lv. {lv}:")
                lines.append(f"  {', '.join(parts)}")
            else:
                rates = Kalos_prequel._get_rates(len(slots))
                combined = {}
                levels = {}
                for i, (sp, fm, min_lv, max_lv) in enumerate(slots):
                    if sp == 0: continue
                    name = resolve(sp, fm)
                    if not name: continue
                    rate = rates[i] if i < len(rates) else 0
                    combined[name] = combined.get(name, 0) + rate
                    if name not in levels:
                        levels[name] = (min_lv, max_lv)
                    else:
                        lo, hi = levels[name]
                        levels[name] = (min(lo, min_lv), max(hi, max_lv))
                if not combined: continue
                lines.append(f"\n{section_name}:")
                for name, rate in sorted(combined.items(), key=lambda x: -x[1]):
                    lo, hi = levels[name]
                    lv = f"Lv. {lo}-{hi}" if lo != hi else f"Lv. {lo}"
                    lines.append(f"  {name:<22}{lv:<12}{rate:>3}%")

        return "\n".join(lines).strip() if lines else None

    @staticmethod
    def decode_personal(self, data, file_idx, is_oras, text_tables, tm_table=None):
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
        for i, stat in enumerate(self.EV_STAT_ORDER):
            val = (ev_raw >> (i * 2)) & 3
            if val: evs.append(f"+{val} {stat}")

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

        forme_count = data[0x20]
        form_stats_idx = struct.unpack_from('<H', data, 0x1C)[0]
        height_dm = struct.unpack_from('<H', data, 0x24)[0]
        weight_hg = struct.unpack_from('<H', data, 0x26)[0]

        species_name = species_list[file_idx] if file_idx < len(species_list) else f"#{file_idx}"
        t1 = type_list[type1] if type1 < len(type_list) else f"type#{type1}"
        t2 = type_list[type2] if type2 < len(type_list) else f"type#{type2}"
        types_str = t1 if type1 == type2 else f"{t1} / {t2}"

        # Gender/growth/egg label helpers
        def _gender_label(g):
            for k in dir(self):
                if k.startswith('GENDER_') and getattr(_SDK, k) == g:
                    return k[7:].replace('_', ' ').title()
            return f"ratio {g}"
        def _growth_label(g):
            for k in dir(self):
                if k.startswith('GROWTH_') and getattr(_SDK, k) == g:
                    return k[7:].replace('_', ' ').title()
            return f"#{g}"
        def _egg_label(g):
            for k in dir(self):
                if k.startswith('EGG_') and getattr(_SDK, k) == g:
                    return k[4:].replace('_', ' ').title()
            return f"#{g}"

        held_parts = []
        for label, item_id in zip(['common', 'rare', 'hidden'], items):
            if item_id > 0:
                iname = item_list[item_id] if item_id < len(item_list) else f"item#{item_id}"
                held_parts.append(f"{iname} ({label})")

        lines = [f"{species_name} (#{file_idx})", f"{types_str} | BST {bst}",
                 f"HP {hp} | Atk {atk} | Def {dfn} | SpA {spa} | SpD {spd} | Spe {spe}",
                 f"Abilities: {' / '.join(ability_names)}" if ability_names else "Abilities: ---"]
        lines.append(f"Gender: {_gender_label(gender)} | Catch Rate: {catch_rate} | Hatch: {hatch_cycles} cycles | Happiness: {base_happiness}")
        eg1 = _egg_label(egg1)
        eg2 = _egg_label(egg2)
        lines.append(f"Growth: {_growth_label(exp_growth)} | Egg Groups: {eg1 if egg1 == eg2 else f'{eg1} / {eg2}'}")
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

    @staticmethod
    def decode_evolution(self, data, file_idx, text_tables):
        """Decode Gen VI evolution data. Returns formatted string."""
        if len(data) < 48 or data[:48] == b'\x00' * 48:
            return None
        species_list = text_tables.get('species', [])
        item_list = text_tables.get('items', [])
        moves_list = text_tables.get('moves', [])
        species_name = species_list[file_idx] if file_idx < len(species_list) else f"#{file_idx}"

        def _evo_label(method_id):
            for k in dir(self):
                if k.startswith('EVO_') and getattr(_SDK, k) == method_id:
                    return k[4:].replace('_', ' ').title()
            # Check Kalos_xy's own additions
            for k in dir(Kalos_prequel):
                if k.startswith('EVO_') and getattr(Kalos_prequel, k) == method_id:
                    return k[4:].replace('_', ' ').title()
            return f"method#{method_id}"

        evo_lines = []
        for i in range(8):
            off = i * 6
            method = struct.unpack_from('<H', data, off)[0]
            param = struct.unpack_from('<H', data, off + 2)[0]
            target = struct.unpack_from('<H', data, off + 4)[0]
            if method == 0 and target == 0: continue
            method_name = _evo_label(method)
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

        if not evo_lines: return None
        return f"{species_name} (#{file_idx}) \u2014 Evolutions\n" + "\n".join(evo_lines)

    @staticmethod
    def decode_move_data(self, data, file_idx, text_tables):
        """Decode Gen VI move data. Returns formatted string."""
        if len(data) < 0x22 or data == b'\x00' * len(data):
            return None
        moves_list = text_tables.get('moves', [])
        type_list = text_tables.get('type_names', [])
        move_name = moves_list[file_idx] if file_idx < len(moves_list) else f"move#{file_idx}"
        type_name = type_list[data[0]] if data[0] < len(type_list) else f"type#{data[0]}"

        category = {self.CAT_STATUS: 'Status', self.CAT_PHYSICAL: 'Physical', self.CAT_SPECIAL: 'Special'}.get(data[2], f"cat#{data[2]}")
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

    @staticmethod
    def decode_trainer(self, trdata, trpoke, is_oras, text_tables):
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


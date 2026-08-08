"""Galar_swsh.py: Sword / Shield.

Inherits Kanto_lgpe. Gen VIII: Dynamax, Wild Area, 400 base dex.
First Switch game with standard battle mechanics restored.
DLC: Isle of Armor, Crown Tundra (expand species count + encounter tables).
"""
from Generations.Kanto_lgpe import Kanto_lgpe

import struct
from leswitch import fb_field, fb_root, fb_table, fb_vector


class Galar_swsh(Kanto_lgpe):
    """Sword / Shield."""

    GAME_CODES = ('SW1', 'SH1')
    TITLES = ('POKÉMON SWORD', 'POKÉMON SHIELD')
    YEAR = 2019

    GEN = 8
    SPECIES_COUNT = 898

    # -- Paths (from pkNX GameFileMapping.cs) --
    PERSONAL_PATH = 'bin/pml/personal'
    LEARNSET_PATH = 'bin/pml/waza_oboe/wazaoboe_total.bin'
    EVOLUTION_PATH = 'bin/pml/evolution'
    MOVE_DATA_PATH = 'bin/pml/waza'
    ITEM_PATH = 'bin/pml/item/item.dat'
    EGG_MOVES_PATH = 'bin/pml/tamagowaza'
    TEXT_PATH = 'bin/message/English/common'
    TEXT_SCRIPT_PATH = 'bin/message/English/script'
    TRDATA_PATH = 'bin/trainer/trainer_data'
    TRPOKE_PATH = 'bin/trainer/trainer_poke'
    TRAINER_CLASS_PATH = 'bin/trainer/trainer_type'

    # -- Encounters --
    WILD_AREA_PATH = 'bin/archive/field/resident/data_table.gfpak'
    STATIC_ENCOUNTER_PATH = 'bin/script_event_data/event_encount_data.bin'
    GIFT_PATH = 'bin/script_event_data/add_poke.bin'
    TRADE_PATH = 'bin/script_event_data/field_trade.bin'
    RENTAL_PATH = 'bin/script_event_data/rental.bin'

    # -- Battle facilities --
    BATTLE_TOWER_POKE_PATH = 'bin/field/param/battle_tower/battle_tower_poke_table.bin'
    BATTLE_TOWER_TRAINER_PATH = 'bin/field/param/battle_tower/battle_tower_trainer_table.bin'

    # -- SWSH-specific mechanics --
    DYNAMAX_PATH = 'bin/appli/chika/data_table/underground_exploration_poke.bin'
    NEST_DATA_PATH = 'bin/archive/field/resident/data_table.gfpak'
    SHOP_PATH = 'bin/appli/shop/bin/shop_data.bin'
    SYMBOL_BEHAVE_PATH = 'bin/field/param/symbol_encount_mons_param/symbol_encount_mons_param.bin'

    # -- Container formats --
    LEARNSET_CONTAINER = 'single'
    ITEM_CONTAINER = 'single'
    WILD_AREA_CONTAINER = 'gfpak'


    def decode_personal(self, data, file_idx, text_tables, tm_table=None):
        """Decode Gen VIII personal data (0xB0 bytes). Sword/Shield."""
        if len(data) < 0xB0 or data == b'\x00' * len(data):
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

        # Gen VIII: abilities are u16 (NOT u8!)
        ability_names = []
        for i in range(3):
            aid = struct.unpack_from('<H', data, 0x18 + i * 2)[0]
            if aid > 0:
                name = ability_list[aid] if aid < len(ability_list) else f"ability#{aid}"
                ability_names.append(f"{name} (Hidden)" if i == 2 else name)

        forme_count = data[0x20]
        is_present = bool((data[0x21] >> 6) & 1)
        form_stats_idx = struct.unpack_from('<H', data, 0x1E)[0]
        base_exp = struct.unpack_from('<H', data, 0x22)[0]
        height_dm = struct.unpack_from('<H', data, 0x24)[0]
        weight_hg = struct.unpack_from('<H', data, 0x26)[0]

        species_name = species_list[file_idx] if file_idx < len(species_list) else f"#{file_idx}"
        t1 = type_list[type1] if type1 < len(type_list) else f"type#{type1}"
        t2 = type_list[type2] if type2 < len(type_list) else f"type#{type2}"
        types_str = t1 if type1 == type2 else f"{t1} / {t2}"

        def _lbl(prefix, val):
            for k in dir(self):
                if k.startswith(prefix) and getattr(self, k) == val:
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
        if not is_present:
            out.append("NOT IN GAME (Dexit)")
        if forme_count > 1:
            out.append(f"Forms: {forme_count} (base index {form_stats_idx})")

        # TM compatibility (128 bits at 0x28)
        if tm_table and len(data) >= 0x38:
            tm_flags = data[0x28:0x38]
            tms = []
            for bit_idx, (label, move_id) in enumerate(tm_table):
                if bit_idx < 128 and tm_flags[bit_idx // 8] & (1 << (bit_idx % 8)):
                    move_name = moves_list[move_id] if move_id < len(moves_list) else f"move#{move_id}"
                    tms.append(f"{label[2:]} {move_name}")
            if tms: out.append(f"TM: {' / '.join(tms)}")

        # TR compatibility (128 bits at 0x3C)
        tr_flags = data[0x3C:0x4C]
        # TODO: TR table resolution once we have the TR move list loaded
        tr_count = sum(bin(b).count('1') for b in tr_flags)
        if tr_count:
            out.append(f"TRs: {tr_count} compatible")

        # Dynamax
        can_dmax = not bool((data[0x5A] >> 2) & 1)
        if not can_dmax:
            out.append("Cannot Dynamax")

        # Regional form
        if data[0x5A] & 1:
            out.append("Regional Form: Yes (Galarian)")

        # DLC dex indices
        dex_regional = struct.unpack_from('<H', data, 0x5C)[0]
        dex_armor = struct.unpack_from('<H', data, 0xAC)[0]
        dex_crown = struct.unpack_from('<H', data, 0xAE)[0]
        dex_parts = []
        if dex_regional: dex_parts.append(f"Galar #{dex_regional}")
        if dex_armor: dex_parts.append(f"Armor #{dex_armor}")
        if dex_crown: dex_parts.append(f"Crown #{dex_crown}")
        if dex_parts:
            out.append(f"Dex: {' | '.join(dex_parts)}")

        return "\n".join(out)


    # ── decode_move ─────────────────────────────────────────────────
    def decode_move(self, data, file_idx, text_tables):
        """Decode SWSH move data (Waza FlatBuffer). Includes Gigantamax power."""
        if len(data) < 12:
            return None

        moves_list = text_tables.get('moves', [])
        type_list = text_tables.get('type_names', [])

        root = fb_root(data)
        vt, to = fb_table(data, root)

        # SWSH Waza.fbs: Version(0), MoveID(1), CanUseMove(2), Type(3), Quality(4),
        # Category(5), Power(6), Accuracy(7), PP(8), Priority(9), HitMax(10), HitMin(11),
        # Inflict(12 ushort), InflictPercent(13), RawInflictCount(14), TurnMin(15), TurnMax(16),
        # CritStage(17), Flinch(18), EffectSequence(19 ushort), Recoil(20), RawHealing(21),
        # RawTarget(22), Stat1-3(23-25), Stat1-3Stage(26-28), Stat1-3Percent(29-31),
        # GigantamaxPower(32), Flags(33-50)
        move_id = fb_field(data, vt, to, 1, '<I') or 0
        mtype = fb_field(data, vt, to, 3, '<B') or 0
        category = fb_field(data, vt, to, 5, '<B') or 0
        power = fb_field(data, vt, to, 6, '<B') or 0
        accuracy = fb_field(data, vt, to, 7, '<B') or 0
        pp = fb_field(data, vt, to, 8, '<B') or 0
        priority = fb_field(data, vt, to, 9, '<b') or 0
        hit_max = fb_field(data, vt, to, 10, '<B') or 0
        hit_min = fb_field(data, vt, to, 11, '<B') or 0
        inflict = fb_field(data, vt, to, 12, '<H') or 0
        inflict_pct = fb_field(data, vt, to, 13, '<B') or 0
        crit = fb_field(data, vt, to, 17, '<b') or 0
        flinch = fb_field(data, vt, to, 18, '<B') or 0
        effect_seq = fb_field(data, vt, to, 19, '<H') or 0
        recoil = fb_field(data, vt, to, 20, '<b') or 0
        healing = fb_field(data, vt, to, 21, '<b') or 0
        target = fb_field(data, vt, to, 22, '<B') or 0
        gmax_power = fb_field(data, vt, to, 32, '<B') or 0

        cat_str = self.CATEGORY_NAMES[category]
        move_name = moves_list[move_id] if move_id < len(moves_list) else f"#{move_id}"
        type_name = type_list[mtype] if mtype < len(type_list) else f"type#{mtype}"

        out = [f"{move_name} (#{move_id})", f"{type_name} | {cat_str}"]
        if power: out.append(f"Power: {power}")
        out.append(f"Accuracy: {'--' if accuracy == 101 else accuracy} | PP: {pp}")
        if priority: out.append(f"Priority: {'+' if priority > 0 else ''}{priority}")
        if hit_min and hit_max:
            out.append(f"Hits: {hit_min}-{hit_max}" if hit_min != hit_max else f"Hits: {hit_min}")
        if crit: out.append(f"Crit Stage: +{crit}")
        if flinch: out.append(f"Flinch: {flinch}%")
        if inflict and inflict_pct:
            out.append(f"Inflict: status#{inflict} ({inflict_pct}%)")
        if recoil: out.append(f"Recoil: {recoil}%")
        if healing: out.append(f"Heal: {healing}%")
        if gmax_power: out.append(f"G-Max Power: {gmax_power}")

        flag_names = [
            (33, 'Contact'), (34, 'Charge'), (35, 'Recharge'), (36, 'Protect'),
            (37, 'Reflectable'), (38, 'Snatch'), (39, 'Mirror'), (40, 'Punch'),
            (41, 'Sound'), (42, 'Gravity'), (43, 'Defrost'), (44, 'DistanceTriple'),
            (45, 'Heal'), (46, 'IgnoreSub'), (47, 'FailSkyBattle'), (48, 'AnimateAlly'),
            (49, 'Dance'), (50, 'Metronome'),
        ]
        flags = [name for fi, name in flag_names if fb_field(data, vt, to, fi, '<B')]
        if flags:
            out.append(f"Flags: {' / '.join(flags)}")

        return "\n".join(out)

    # ── decode_encounters ───────────────────────────────────────────
    def decode_encounters(self, data, file_idx, text_tables, path='', **kwargs):
        """Decode SWSH encounters. Dispatches by path:
        static -> fixed encounters (legendaries, story)
        gift/add_poke -> gift pokemon
        trade/field_trade -> in-game trades
        default -> wild encounters (zone-based, weather-slotted)"""
        if len(data) < 12:
            return None

        species_list = text_tables.get('species', [])
        moves_list = text_tables.get('moves', [])
        item_list = text_tables.get('items', [])

        root = fb_root(data)
        vt, to = fb_table(data, root)

        # ── Static encounters ──
        if path == self.STATIC_ENCOUNTER_PATH:
            species = fb_field(data, vt, to, 18, '<i') or 0
            form = fb_field(data, vt, to, 6, '<B') or 0
            level = fb_field(data, vt, to, 17, '<B') or 0
            held = fb_field(data, vt, to, 16, '<i') or 0
            shiny = fb_field(data, vt, to, 19, '<I') or 0
            nature = fb_field(data, vt, to, 20, '<I') or 0
            gender = fb_field(data, vt, to, 21, '<b') or 0
            ability = fb_field(data, vt, to, 28, '<i') or 0
            dmax_lv = fb_field(data, vt, to, 7, '<B') or 0
            can_gmax = fb_field(data, vt, to, 15, '<B') or 0

            sp_name = species_list[species] if species < len(species_list) else f"#{species}"
            form_str = f"-{form}" if form else ""
            out = [f"Static: {sp_name}{form_str} Lv{level}"]
            if held:
                iname = item_list[held] if held < len(item_list) else f"item#{held}"
                out.append(f"Held: {iname}")
            moves = []
            for mi in range(4):
                mid = fb_field(data, vt, to, 29 + mi, '<i') or 0
                if mid: moves.append(moves_list[mid] if mid < len(moves_list) else f"move#{mid}")
            if moves: out.append(f"Moves: {' / '.join(moves)}")
            if shiny: out.append(f"Shiny Lock: {shiny}")
            if can_gmax: out.append("Can Gigantamax")
            if dmax_lv: out.append(f"Dynamax Lv: {dmax_lv}")
            # IVs
            ivs = []
            for stat, fi in [('Spe',22),('Atk',23),('Def',24),('HP',25),('SpA',26),('SpD',27)]:
                v = fb_field(data, vt, to, fi, '<b')
                if v is not None and v >= 0: ivs.append(f"{stat}={v}")
            if ivs: out.append(f"IVs: {', '.join(ivs)}")
            return "\n".join(out)

        # ── Gift pokemon ──
        if path == self.GIFT_PATH:
            species = fb_field(data, vt, to, 0, '<i') or 0
            form = fb_field(data, vt, to, 1, '<B') or 0
            level = fb_field(data, vt, to, 2, '<B') or 0
            held = fb_field(data, vt, to, 3, '<i') or 0
            is_egg = fb_field(data, vt, to, 4, '<B') or 0
            shiny_lock = fb_field(data, vt, to, 9, '<B') or 0
            ability = fb_field(data, vt, to, 8, '<i') or 0
            special_move = fb_field(data, vt, to, 10, '<i') or 0

            sp_name = species_list[species] if species < len(species_list) else f"#{species}"
            form_str = f"-{form}" if form else ""
            egg_str = " (Egg)" if is_egg else ""
            out = [f"Gift: {sp_name}{form_str} Lv{level}{egg_str}"]
            if held:
                iname = item_list[held] if held < len(item_list) else f"item#{held}"
                out.append(f"Held: {iname}")
            if special_move:
                mname = moves_list[special_move] if special_move < len(moves_list) else f"move#{special_move}"
                out.append(f"Special Move: {mname}")
            if shiny_lock: out.append("Shiny Locked")
            return "\n".join(out)

        # ── In-game trades ──
        if path == self.TRADE_PATH:
            species = fb_field(data, vt, to, 0, '<i') or 0
            form = fb_field(data, vt, to, 1, '<B') or 0
            level = fb_field(data, vt, to, 2, '<B') or 0
            held = fb_field(data, vt, to, 3, '<i') or 0
            req_species = fb_field(data, vt, to, 4, '<i') or 0
            req_form = fb_field(data, vt, to, 5, '<B') or 0
            req_nature = fb_field(data, vt, to, 6, '<i') or 0

            sp_name = species_list[species] if species < len(species_list) else f"#{species}"
            req_name = species_list[req_species] if req_species < len(species_list) else f"#{req_species}"
            form_str = f"-{form}" if form else ""
            out = [f"Trade: {sp_name}{form_str} Lv{level}"]
            out.append(f"Requires: {req_name}")
            if held:
                iname = item_list[held] if held < len(item_list) else f"item#{held}"
                out.append(f"Held: {iname}")
            return "\n".join(out)

        # ── Wild encounters (default) ──
        # EncounterTable: ZoneID(0 ulong), SubTables(1 vector)
        zone_id = fb_field(data, vt, to, 0, '<Q') or 0
        sub_voff = fb_field(data, vt, to, 1, '<H')
        if not sub_voff:
            return f"Zone {zone_id:#x}: no encounter data"

        out = [f"Zone {zone_id:#x}"]
        count, doff = fb_vector(data, to, sub_voff)
        for i in range(count):
            # Each sub-table is an offset to EncounterSubTable
            sub_ptr = doff + i * 4
            sub_rel = struct.unpack_from('<I', data, sub_ptr)[0]
            sub_abs = sub_ptr + sub_rel
            svt, sto = fb_table(data, sub_abs)

            lv_min = fb_field(data, svt, sto, 0, '<B') or 0
            lv_max = fb_field(data, svt, sto, 1, '<B') or 0
            # Slots vector (field 2)
            slot_voff = fb_field(data, svt, sto, 2, '<H')
            if not slot_voff:
                continue
            slot_count, slot_doff = fb_vector(data, sto, slot_voff)
            out.append(f"  Sub-table {i} (Lv{lv_min}-{lv_max}, {slot_count} slots):")
            for s in range(slot_count):
                # EncounterSlot: Probability(0 u8), Species(1 int), Form(2 u8)
                sl_ptr = slot_doff + s * 4
                sl_rel = struct.unpack_from('<I', data, sl_ptr)[0]
                sl_abs = sl_ptr + sl_rel
                slvt, slto = fb_table(data, sl_abs)
                prob = fb_field(data, slvt, slto, 0, '<B') or 0
                sp = fb_field(data, slvt, slto, 1, '<i') or 0
                fm = fb_field(data, slvt, slto, 2, '<B') or 0
                sp_name = species_list[sp] if sp < len(species_list) else f"#{sp}"
                fm_str = f"-{fm}" if fm else ""
                out.append(f"    {sp_name}{fm_str} {prob}%")

        return "\n".join(out)

    # ── decode_trainer ──────────────────────────────────────────────
    def decode_trainer(self, data, file_idx, text_tables):
        """Decode SWSH trainer (binary structs: TrainerData8 0x14 + TrainerPoke8 0x20)."""
        if len(data) < 0x14:
            return None

        species_list = text_tables.get('species', [])
        moves_list = text_tables.get('moves', [])
        item_list = text_tables.get('items', [])
        class_list = text_tables.get('trainer_classes', [])
        name_list = text_tables.get('trainer_names', [])

        # TrainerData8: Mode(0 u8), NumPokemon(1 u8), Item1-4(u16 x4 at 2-9),
        # AI(u32 at 0x0C), Heal(bool at 0x10), Money(u8 at 0x11), Gift(u16 at 0x12)
        mode = data[0]
        num_poke = data[1]
        items_used = []
        for i in range(4):
            iid = struct.unpack_from('<H', data, 2 + i * 2)[0]
            if iid:
                items_used.append(item_list[iid] if iid < len(item_list) else f"item#{iid}")
        ai = struct.unpack_from('<I', data, 0x0C)[0]
        heal = bool(data[0x10])
        money = data[0x11]
        gift = struct.unpack_from('<H', data, 0x12)[0]

        battle_type = self.BATTLE_TYPES[mode] if mode < len(self.BATTLE_TYPES) else f"mode#{mode}"

        class_name = class_list[file_idx] if file_idx < len(class_list) else f"class#{file_idx}"
        trainer_name = name_list[file_idx] if file_idx < len(name_list) else f"Trainer #{file_idx}"

        out = [f"{class_name} {trainer_name}"]
        if battle_type != 'Single': out[0] += f"  [{battle_type} Battle]"
        if heal: out.append("Will heal during battle")

        # TrainerPoke8 entries follow at 0x14, each 0x20 bytes
        poke_offset = 0x14
        for pi in range(num_poke):
            base = poke_offset + pi * 0x20
            if base + 0x20 > len(data):
                break
            # TrainerPoke8 fields (from extract):
            # Nature(0 u8?), EV_HP-SPE(bytes), DynamaxLevel(byte), CanGigantamax(bool),
            # ... Level, Species, Form, HeldItem, Move1-4 are packed in the struct
            # Binary layout needs verification against actual data, using IMove field order:
            iv32 = struct.unpack_from('<I', data, base)[0]
            iv_val = (iv32 >> 0) & 0x1F  # HP IV as representative
            level = struct.unpack_from('<H', data, base + 0x04)[0]
            species = struct.unpack_from('<H', data, base + 0x06)[0]
            form = struct.unpack_from('<H', data, base + 0x08)[0]
            held = struct.unpack_from('<H', data, base + 0x0A)[0]
            moves = [struct.unpack_from('<H', data, base + 0x0C + i * 2)[0] for i in range(4)]
            nature = data[base + 0x14]
            dmax_lv = data[base + 0x1C]
            can_gmax = bool(data[base + 0x1D])
            shiny = bool(data[base + 0x1E])

            sp_name = species_list[species] if species < len(species_list) else f"#{species}"
            form_str = f"-{form}" if form else ""
            header = f"{sp_name}{form_str} (Lv. {level})"
            if held:
                iname = item_list[held] if held < len(item_list) else f"item#{held}"
                header += f"  [{iname}]"
            out.append(header)
            move_names = []
            for mid in moves:
                if mid: move_names.append(moves_list[mid] if mid < len(moves_list) else f"move#{mid}")
            if move_names: out.append(f"  {' / '.join(move_names)}")
            if can_gmax: out.append("  Can Gigantamax")
            if dmax_lv: out.append(f"  Dynamax Lv: {dmax_lv}")
            if shiny: out.append("  Shiny")

        footer = []
        if money: footer.append(f"Prize: x{money}")
        if items_used: footer.append(f"Items: {', '.join(items_used)}")
        ai_flags = self.decode_ai_flags(ai)
        if ai_flags and ai_flags != ['None']: footer.append(f"AI: {', '.join(ai_flags)}")
        if footer:
            out.append("")
            out.append(" | ".join(footer))

        return "\n".join(out)

    # ── decode_item ─────────────────────────────────────────────────
    def decode_item(self, data, file_idx, text_tables, path='', **kwargs):
        """Decode SWSH item/shop data. Dispatches by path:
        shop -> shop inventory (SingleShop/MultiShop)
        default -> item data (binary)"""
        if len(data) < 8:
            return None

        # ── Shop inventory ──
        if path == self.SHOP_PATH:
            root = fb_root(data)
            vt, to = fb_table(data, root)
            hash_val = fb_field(data, vt, to, 0, '<Q') or 0
            inv_off = fb_field(data, vt, to, 1, '<H')
            if not inv_off:
                return f"Shop {hash_val:#x}: empty"
            # Inventory sub-table -> Items vector
            inv_abs = to + inv_off
            inv_sub = inv_abs + struct.unpack_from('<I', data, inv_abs)[0]
            ivt, ito = fb_table(data, inv_sub)
            items_voff = fb_field(data, ivt, ito, 0, '<H')
            if not items_voff:
                return f"Shop {hash_val:#x}: no items"
            item_list = text_tables.get('items', [])
            count, doff = fb_vector(data, ito, items_voff)
            out = [f"Shop {hash_val:#x} ({count} items):"]
            for i in range(count):
                iid = struct.unpack_from('<i', data, doff + i * 4)[0]
                iname = item_list[iid] if iid < len(item_list) else f"item#{iid}"
                out.append(f"  {iname}")
            return "\n".join(out)

        # ── Item data (binary, inherits LGPE format) ──
        return super().decode_item(data, file_idx, text_tables)

    # ── decode_nest ─────────────────────────────────────────────────
    def decode_nest(self, data, file_idx, text_tables, path='', **kwargs):
        """Decode SWSH raid nest data. Dispatches by path:
        crystal -> crystal raid encounters
        distribution -> event distribution raids
        reward -> raid reward tables
        default -> base nest encounters (EncounterNest)"""
        if len(data) < 12:
            return None

        species_list = text_tables.get('species', [])
        root = fb_root(data)
        vt, to = fb_table(data, root)

        section = path.split('/')[-1] if path else ''

        # ── Reward tables ──
        if 'reward' in section:
            # NestHoleRewardTable: TableID(0 ulong), Entries(1 vector)
            table_id = fb_field(data, vt, to, 0, '<Q') or 0
            ent_voff = fb_field(data, vt, to, 1, '<H')
            if not ent_voff:
                return f"Reward Table {table_id:#x}: empty"
            item_list = text_tables.get('items', [])
            count, doff = fb_vector(data, to, ent_voff)
            out = [f"Reward Table {table_id:#x} ({count} entries):"]
            for i in range(count):
                e_ptr = doff + i * 4
                e_rel = struct.unpack_from('<I', data, e_ptr)[0]
                e_abs = e_ptr + e_rel
                evtt, eto = fb_table(data, e_abs)
                iid = fb_field(data, evtt, eto, 0, '<i') or 0
                qty = fb_field(data, evtt, eto, 1, '<i') or 1
                iname = item_list[iid] if iid < len(item_list) else f"item#{iid}"
                out.append(f"  {iname} x{qty}")
            return "\n".join(out)

        # ── Base nest / crystal / distribution (same schema) ──
        # EncounterNest: EntryIndex(0), Species(1), Form(2), LevelTableID(3 ulong),
        # Ability(4 u8), IsGigantamax(5 bool), DropTableID(6 ulong), BonusTableID(7 ulong),
        # Probabilities(8 vector u32), Gender(9 byte), FlawlessIVs(10 byte)
        entry_idx = fb_field(data, vt, to, 0, '<i') or 0
        species = fb_field(data, vt, to, 1, '<i') or 0
        form = fb_field(data, vt, to, 2, '<i') or 0
        level_table = fb_field(data, vt, to, 3, '<Q') or 0
        ability = fb_field(data, vt, to, 4, '<B') or 0
        is_gmax = fb_field(data, vt, to, 5, '<B') or 0
        drop_table = fb_field(data, vt, to, 6, '<Q') or 0
        bonus_table = fb_field(data, vt, to, 7, '<Q') or 0
        gender = fb_field(data, vt, to, 9, '<b') or 0
        flawless = fb_field(data, vt, to, 10, '<b') or 0

        sp_name = species_list[species] if species < len(species_list) else f"#{species}"
        form_str = f"-{form}" if form else ""
        ability_str = ('Ability 1', 'Ability 2', 'Hidden', 'Ability 1/2', 'Any')[ability]

        tag = "Crystal " if 'crystal' in section else "Event " if 'distribution' in section else ""
        out = [f"{tag}Nest #{entry_idx}: {sp_name}{form_str}"]
        out.append(f"Ability: {ability_str} | Flawless IVs: {flawless}")
        if is_gmax: out.append("Gigantamax Raid")

        # Probabilities vector (star ratings)
        prob_voff = fb_field(data, vt, to, 8, '<H')
        if prob_voff:
            count, doff = fb_vector(data, to, prob_voff)
            probs = [struct.unpack_from('<I', data, doff + i * 4)[0] for i in range(count)]
            star_strs = [f"{i+1}*:{p}%" for i, p in enumerate(probs) if p > 0]
            if star_strs: out.append(f"Rates: {' | '.join(star_strs)}")

        gender_str = ('Random', 'Male', 'Female', 'Genderless')[gender]
        out.append(f"Gender: {gender_str}")
        out.append(f"Drop: {drop_table:#x} | Bonus: {bonus_table:#x}")

        return "\n".join(out)

    FLIPNOTE_PAIRS = {
        'Pokemon Sword & Shield': ['SW1', 'SH1'],
    }

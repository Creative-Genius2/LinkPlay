"""Kalos_sequel.py: Legends: Z-A.

Inherits Paldea_sv. Kalos region revisit, Lumiose City focus.
Second Legends title. Mega Evolution returns.
DLC: Mega Dimension (donuts, hyperspace, pocket dimensions).
"""
import struct
from Generations.Paldea_sv import Paldea_sv
from leswitch import fb_field, fb_root, fb_string, fb_table, fb_vector


class Kalos_sequel(Paldea_sv):
    """Legends: Z-A."""

    GAME_CODES = ('LZA',)
    TITLES = ('POKÉMON LEGENDS: Z-A',)
    YEAR = 2025

    SPECIES_COUNT = 1025  # DevID enum max

    CONTAINER = 'trinity'
    TRINITY_DATA_PATH = 'arc/data.trpfs'
    TRINITY_DIR_PATH = 'arc/data.trpfd'

    MEGA_EVO_PATH = 'bin/pml/mega_evolution'

    # Z-A paths (override SV where they moved)
    PERSONAL_PATH = 'avalon/data/personal_array.bin'
    MOVE_DATA_PATH = 'avalon/data/waza_array.bin'
    ENCOUNTER_PATH = 'world/ik_data/field/pokemon/encount_data'
    TRDATA_PATH = 'world/ik_data/trainer/trdata'
    ITEM_PATH = 'world/exl/item_data/item_data'
    DONUT_PATH = 'world/exl/donut'
    DIM_PATH = 'world/ik_data/field/dimension'
    ZA_ROYALE_PATH = 'world/ik_data/capture'

    # ZA-specific: 160 TMs (107 base + 53 DLC), move IDs in bit order
    TM_MOVE_IDS = (
        29, 337, 473, 249, 46, 347, 92, 86, 812, 280,
        339, 157, 58, 424, 423, 113, 182, 612, 408, 583,
        422, 332, 9, 8, 242, 412, 129, 91, 7, 14,
        115, 104, 34, 400, 203, 317, 446, 126, 435, 331,
        352, 202, 19, 63, 282, 341, 97, 120, 196, 315,
        219, 414, 188, 434, 416, 38, 261, 442, 428, 248,
        421, 53, 94, 76, 444, 521, 85, 257, 89, 250,
        304, 83, 57, 247, 406, 710, 398, 523, 542, 334,
        404, 369, 417, 430, 164, 528, 231, 191, 390, 399,
        174, 605, 200, 18, 269, 56, 377, 127, 118, 441,
        527, 411, 526, 394, 59, 87, 370,
        # DLC TMs (Mega Dimension)
        4, 263, 886, 47, 491, 490, 488, 885, 6, 318,
        325, 466, 246, 259, 206, 305, 706, 102, 443, 138,
        402, 509, 451, 409, 458, 299, 814, 530, 815, 480,
        524, 207, 330, 252, 660, 799, 813, 13, 130, 161,
        503, 333, 410, 80, 669, 143, 90, 329, 800, 796,
        307, 308, 338,
    )

    # Evolution method → plib item conversion (evo arg → real item ID)
    PLIB_ITEMS = {
        1: 80, 2: 81, 3: 82, 4: 83, 5: 84, 6: 85, 7: 107, 8: 108,
        9: 110, 10: 1779, 15: 229, 16: 236, 19: 280, 49: 326, 50: 327,
        51: 644, 52: 849, 70: 1103, 71: 1104, 79: 1116, 80: 1117,
        81: 1253, 82: 1254, 83: 1582, 84: 1592, 85: 2344, 86: 1861,
        87: 2345, 88: 1857, 89: 1858, 92: 218, 93: 109, 94: 2403,
        95: 2404, 96: 2402, 102: 765, 111: 537, 112: 325, 113: 252,
        114: 324, 115: 322, 116: 323, 117: 321, 118: 235, 119: 2482,
        121: 847, 1691: 1691,
    }

    # ZARank tiers (Z=weakest, A=strongest, Inf=postgame)
    ZA_RANKS = ('Z','Y','X','W','V','U','T','S','R','Q','P','O','N',
                'M','L','K','J','I','H','G','F','E','D','C','B','A','Inf')

    FLIPNOTE_PAIRS = {
        'Pokemon Legends: Z-A': ['LZA'],
    }

    # ── decode_personal ─────────────────────────────────────────────
    def decode_personal(self, data, file_idx, text_tables, tm_table=None):
        """Decode ZA personal data (FlatBuffer). Everything is embedded:
        stats, types, abilities, evos, learnsets, TMs, eggs, reminders,
        classification, alpha move, mega evo lookup, gender, hatch, dex."""
        if len(data) < 12:
            return None

        species_list = text_tables.get('species', [])
        type_list = text_tables.get('type_names', [])
        ability_list = text_tables.get('abilities', [])
        item_list = text_tables.get('items', [])
        moves_list = text_tables.get('moves', [])

        root = fb_root(data)
        vt, to = fb_table(data, root)

        # Field 0: Info (PersonalInfoDetail struct — 16 bytes inline)
        info_off = fb_field(data, vt, to, 0, '<H')
        spec_internal = spec_national = 0
        height = weight = color = debut = 0
        cls_major = cls_minor = form_num = 0
        if info_off:
            base = to + info_off
            spec_internal = struct.unpack_from('<H', data, base)[0]
            form_num = struct.unpack_from('<H', data, base + 2)[0]
            spec_national = struct.unpack_from('<H', data, base + 4)[0]
            color = data[base + 6]
            height = struct.unpack_from('<H', data, base + 8)[0]
            weight = struct.unpack_from('<H', data, base + 10)[0]
            debut = data[base + 12]
            cls_major = data[base + 13]
            cls_minor = struct.unpack_from('<I', data, base + 14)[0]

        # Field 1: IsPresentInGame
        present = fb_field(data, vt, to, 1, '<B')
        if present is not None:
            present = bool(present)
        else:
            present = True

        # Field 2: Dex (nullable struct — Index u16, Group u8)
        # Field 3-4: Types
        type1 = fb_field(data, vt, to, 3, '<B') or 0
        type2 = fb_field(data, vt, to, 4, '<B') or 0
        # Field 5-7: Abilities (u16 each)
        ab1 = fb_field(data, vt, to, 5, '<H') or 0
        ab2 = fb_field(data, vt, to, 6, '<H') or 0
        abH = fb_field(data, vt, to, 7, '<H') or 0
        # Field 8: EXPGrowth, Field 9: CatchRate
        exp_growth = fb_field(data, vt, to, 8, '<B') or 0
        catch_rate = fb_field(data, vt, to, 9, '<B') or 0
        # Field 10: Gender (struct: SexGroup u8, Ratio u8)
        gender_off = fb_field(data, vt, to, 10, '<H')
        gender_group = gender_ratio = 0
        if gender_off:
            g_base = to + gender_off
            gender_group = data[g_base]
            gender_ratio = data[g_base + 1]
        # Field 11-12: EggGroups
        egg1 = fb_field(data, vt, to, 11, '<B') or 0
        egg2 = fb_field(data, vt, to, 12, '<B') or 0
        # Field 13: Hatch (struct: SpeciesInternal u16, Form u16, RegionalFlags u16, EverstoneForm u16)
        # Field 14: HatchCycles, Field 15: BaseFriendship
        hatch_cycles = fb_field(data, vt, to, 14, '<B') or 0
        friendship = fb_field(data, vt, to, 15, '<B') or 0
        # Field 16: BaseEXPAddend (i16)
        exp_addend = fb_field(data, vt, to, 16, '<h') or 0
        # Field 17: EvoStage
        evo_stage = fb_field(data, vt, to, 17, '<B') or 0
        # Field 18: IsTypeChangeDisallowed
        # Field 19: EVYield (PersonalInfoStats struct — 6 bytes)
        ev_off = fb_field(data, vt, to, 19, '<H')
        evs = []
        if ev_off:
            ev_base = to + ev_off
            for i, stat in enumerate(self.EV_STAT_ORDER):
                val = data[ev_base + i]
                if val: evs.append(f"+{val} {stat}")
        # Field 20: Base stats (PersonalInfoStats struct — 6 bytes: HP ATK DEF SPA SPD SPE)
        stat_off = fb_field(data, vt, to, 20, '<H')
        hp = atk = dfn = spa = spd = spe = 0
        if stat_off:
            s = to + stat_off
            hp, atk, dfn, spa, spd, spe = data[s], data[s+1], data[s+2], data[s+3], data[s+4], data[s+5]
        bst = hp + atk + dfn + spa + spd + spe

        # Resolve names
        species_name = species_list[spec_national] if spec_national < len(species_list) else f"#{spec_national}"
        t1 = type_list[type1] if type1 < len(type_list) else f"type#{type1}"
        t2 = type_list[type2] if type2 < len(type_list) else f"type#{type2}"
        types_str = t1 if type1 == type2 else f"{t1} / {t2}"

        ability_names = []
        for aid, suffix in ((ab1, ''), (ab2, ''), (abH, ' (Hidden)')):
            if aid > 0:
                name = ability_list[aid] if aid < len(ability_list) else f"ability#{aid}"
                ability_names.append(f"{name}{suffix}")

        gender_str = f"{gender_ratio}% female" if gender_group == 0 else ('Male only', 'Female only', 'Genderless')[gender_group - 1] if 1 <= gender_group <= 3 else f"group#{gender_group}"


        out = [f"{species_name} (#{spec_national})", f"{types_str} | BST {bst}",
               f"HP {hp} | Atk {atk} | Def {dfn} | SpA {spa} | SpD {spd} | Spe {spe}",
               f"Abilities: {' / '.join(ability_names)}" if ability_names else "Abilities: ---"]
        out.append(f"Gender: {gender_str} | Catch Rate: {catch_rate} | Hatch: {hatch_cycles} cycles | Friendship: {friendship}")
        out.append(f"Growth: {self.GROWTH_NAMES[exp_growth]} | Egg Groups: {self.EGG_GROUP_NAMES[egg1]}" +
                   (f" / {self.EGG_GROUP_NAMES[egg2]}" if egg1 != egg2 else ""))
        out.append(f"Height: {height / 100.0}m | Weight: {weight / 10.0}kg")
        out.append(f"Evo Stage: {evo_stage} | EXP Addend: {exp_addend}")
        if evs:
            out.append(f"EVs: {', '.join(evs)}")
        if not present:
            out.append("NOT IN GAME")

        # Classification
        tags = []
        if cls_major in self.CLASS_MAJOR: tags.append(self.CLASS_MAJOR[cls_major])
        if cls_minor in self.CLASS_MINOR: tags.append(self.CLASS_MINOR[cls_minor])
        if tags:
            out.append(f"Classification: {' / '.join(tags)}")

        # Field 21: Evolutions (vector of 16-byte structs)
        evo_voff = fb_field(data, vt, to, 21, '<H')
        if evo_voff:
            count, doff = fb_vector(data, to, evo_voff)
            for i in range(count):
                e = doff + i * 16
                level = struct.unpack_from('<H', data, e)[0]
                method = struct.unpack_from('<H', data, e + 2)[0]
                arg = struct.unpack_from('<H', data, e + 4)[0]
                target_internal = struct.unpack_from('<H', data, e + 12)[0]
                target_form = struct.unpack_from('<H', data, e + 14)[0]
                tname = species_list[target_internal] if target_internal < len(species_list) else f"#{target_internal}"
                form_str = f"-{target_form}" if target_form else ""
                out.append(f"  Evo: {tname}{form_str} (method {method}, arg {arg}, lv {level})")

        # Field 22: TechnicalMachine (vector of u16 move IDs)
        tm_voff = fb_field(data, vt, to, 22, '<H')
        if tm_voff:
            count, doff = fb_vector(data, to, tm_voff)
            tms = []
            for i in range(count):
                mid = struct.unpack_from('<H', data, doff + i * 2)[0]
                mname = moves_list[mid] if mid < len(moves_list) else f"move#{mid}"
                # Find TM number
                tm_num = self.TM_MOVE_IDS.index(mid) + 1 if mid in self.TM_MOVE_IDS else 0
                tms.append(f"TM{tm_num:03d} {mname}" if tm_num else mname)
            if tms:
                out.append(f"TMs ({len(tms)}): {' / '.join(tms)}")

        # Field 23: EggMoves (vector of u16)
        egg_voff = fb_field(data, vt, to, 23, '<H')
        if egg_voff:
            count, doff = fb_vector(data, to, egg_voff)
            eggs = []
            for i in range(count):
                mid = struct.unpack_from('<H', data, doff + i * 2)[0]
                eggs.append(moves_list[mid] if mid < len(moves_list) else f"move#{mid}")
            if eggs:
                out.append(f"Egg Moves: {' / '.join(eggs)}")

        # Field 24: ReminderMoves (vector of u16)
        rem_voff = fb_field(data, vt, to, 24, '<H')
        if rem_voff:
            count, doff = fb_vector(data, to, rem_voff)
            rems = []
            for i in range(count):
                mid = struct.unpack_from('<H', data, doff + i * 2)[0]
                rems.append(moves_list[mid] if mid < len(moves_list) else f"move#{mid}")
            if rems:
                out.append(f"Reminder: {' / '.join(rems)}")

        # Field 25: Learnset (vector of 4-byte structs: Move u16, Level i8, LevelPlus u8)
        learn_voff = fb_field(data, vt, to, 25, '<H')
        if learn_voff:
            count, doff = fb_vector(data, to, learn_voff)
            learns = []
            for i in range(count):
                e = doff + i * 4
                mid = struct.unpack_from('<H', data, e)[0]
                level = struct.unpack_from('<b', data, e + 2)[0]
                lplus = data[e + 3]
                mname = moves_list[mid] if mid < len(moves_list) else f"move#{mid}"
                lvl_str = 'EVO' if level == -3 else 'RELEARN' if level == -2 else str(level)
                plus_str = f" {{{lplus}}}" if lplus else ""
                learns.append(f"[{lvl_str}] {mname}{plus_str}")
            if learns:
                out.append(f"Learnset ({len(learns)}):")
                for l in learns:
                    out.append(f"  {l}")

        return "\n".join(out)

    # ── decode_move ─────────────────────────────────────────────────
    def decode_move(self, data, file_idx, text_tables):
        """Decode ZA move data (Waza FlatBuffer). Includes battle params."""
        if len(data) < 12:
            return None

        moves_list = text_tables.get('moves', [])
        type_list = text_tables.get('type_names', [])

        root = fb_root(data)
        vt, to = fb_table(data, root)

        move_id = fb_field(data, vt, to, 0, '<H') or 0
        can_use = fb_field(data, vt, to, 1, '<B')
        mtype = fb_field(data, vt, to, 2, '<B') or 0
        quality = fb_field(data, vt, to, 3, '<B') or 0
        category = fb_field(data, vt, to, 4, '<B') or 0
        power = fb_field(data, vt, to, 5, '<B') or 0
        accuracy = fb_field(data, vt, to, 6, '<B') or 0
        pp = fb_field(data, vt, to, 7, '<B') or 0
        priority = fb_field(data, vt, to, 8, '<b') or 0
        hit_max = fb_field(data, vt, to, 9, '<B') or 0
        hit_min = fb_field(data, vt, to, 10, '<B') or 0
        # Field 11: Inflict struct (6 bytes: value u16, chance u8, turn1-3 u8)
        inflict_off = fb_field(data, vt, to, 11, '<H')
        inflict_val = inflict_chance = 0
        if inflict_off:
            ib = to + inflict_off
            inflict_val = struct.unpack_from('<H', data, ib)[0]
            inflict_chance = data[ib + 2]
        crit = fb_field(data, vt, to, 12, '<B') or 0
        flinch = fb_field(data, vt, to, 13, '<B') or 0
        effect_seq = fb_field(data, vt, to, 14, '<H') or 0
        recoil = fb_field(data, vt, to, 15, '<b') or 0
        self_heal = fb_field(data, vt, to, 16, '<b') or 0
        dmg_heal = fb_field(data, vt, to, 17, '<B') or 0
        target = fb_field(data, vt, to, 18, '<B') or 0

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
        if inflict_val and inflict_chance:
            out.append(f"Inflict: status#{inflict_val} ({inflict_chance}%)")
        if recoil: out.append(f"Recoil: {recoil}%")
        if self_heal: out.append(f"Self Heal: {self_heal}%")
        if dmg_heal: out.append(f"Drain: {dmg_heal}%")

        # Flags (fields 20+)
        flag_names = [
            (20, 'Contact'), (21, 'Charge'), (22, 'Recharge'), (23, 'Protect'),
            (24, 'Reflectable'), (25, 'Snatch'), (26, 'Mirror'), (27, 'Punch'),
            (28, 'Sound'), (29, 'Dance'), (30, 'Gravity'), (31, 'Defrost'),
            (33, 'Heal'), (34, 'IgnoreSub'), (36, 'AnimateAlly'), (37, 'Metronome'),
            (42, 'Powder'), (43, 'Bite'), (44, 'Bullet'), (47, 'SheerForce'),
            (48, 'Slicing'), (49, 'Wind'),
        ]
        flags = [name for fi, name in flag_names if fb_field(data, vt, to, fi, '<B')]
        if flags:
            out.append(f"Flags: {' / '.join(flags)}")

        return "\n".join(out)

    # ── decode_encounters ───────────────────────────────────────────
    def decode_encounters(self, data, file_idx, text_tables, path='', **kwargs):
        """Decode ZA encounter data (spawner-based).
        Handles both overworld spawners and dimension wild branches."""
        if len(data) < 12:
            return None

        species_list = text_tables.get('species', [])
        root = fb_root(data)
        vt, to = fb_table(data, root)

        # EncountData: Id, DevNo, MinLevel, MaxLevel, Sex, FormNo, Rare,
        #              Tokusei, Seikaku, TalentScale, TalentVNum, OyabunProbability
        enc_id_off = fb_field(data, vt, to, 0, '<H')
        enc_id = fb_string(data, to, enc_id_off) if enc_id_off else "?"
        dev_no = fb_field(data, vt, to, 1, '<H') or 0
        min_lv = fb_field(data, vt, to, 2, '<i') or 0
        max_lv = fb_field(data, vt, to, 3, '<i') or 0
        sex = fb_field(data, vt, to, 4, '<i') or 0
        form = fb_field(data, vt, to, 5, '<i') or 0
        rare = fb_field(data, vt, to, 6, '<i') or 0
        ability = fb_field(data, vt, to, 7, '<i') or 0
        nature = fb_field(data, vt, to, 8, '<i') or 0
        iv_scale = fb_field(data, vt, to, 9, '<i') or 0
        iv_count = fb_field(data, vt, to, 10, '<i') or 0
        oyabun_prob = fb_field(data, vt, to, 11, '<f') or 0.0

        sp_name = species_list[dev_no] if dev_no < len(species_list) else f"#{dev_no}"
        form_str = f"-{form}" if form else ""
        lv_str = f"Lv{min_lv}" if min_lv == max_lv else f"Lv{min_lv}-{max_lv}"
        sex_str = self.SEX_SYMBOLS[sex] if sex < len(self.SEX_SYMBOLS) else ''
        rare_str = ' *Shiny*' if rare == 2 else ''

        out = [f"{sp_name}{form_str}{sex_str} {lv_str}{rare_str}"]
        if oyabun_prob > 0:
            out.append(f"Alpha chance: {oyabun_prob:.1%}")
        if iv_count > 0:
            out.append(f"IVs: {iv_count} perfect (scale {iv_scale})")
        if ability:
            out.append(f"Ability slot: {ability}")
        if nature:
            out.append(f"Nature: fixed #{nature}")
        out[0] = f"[{enc_id}] {out[0]}"

        return "\n".join(out)

    # ── decode_trainer ──────────────────────────────────────────────
    def decode_trainer(self, data, file_idx, text_tables):
        """Decode ZA trainer (TrDataMain FlatBuffer).
        Includes team, mega flags, AI, vision cone, env/dlog."""
        if len(data) < 12:
            return None

        species_list = text_tables.get('species', [])
        moves_list = text_tables.get('moves', [])
        item_list = text_tables.get('items', [])

        root = fb_root(data)
        vt, to = fb_table(data, root)

        tr_id_off = fb_field(data, vt, to, 0, '<H')
        tr_id = fb_string(data, to, tr_id_off) if tr_id_off else "?"
        tr_type = fb_field(data, vt, to, 1, '<Q') or 0
        tr_type2 = fb_field(data, vt, to, 2, '<Q') or 0
        za_rank = fb_field(data, vt, to, 3, '<B') or 0
        money_rate = fb_field(data, vt, to, 4, '<B') or 0
        mega = fb_field(data, vt, to, 5, '<B') or 0
        last_mega = fb_field(data, vt, to, 6, '<B') or 0

        rank_str = self.ZA_RANKS[za_rank] if za_rank < len(self.ZA_RANKS) else f"rank#{za_rank}"
        out = [f"Trainer: {tr_id} | Rank {rank_str} | Money x{money_rate}"]
        if mega:
            out.append(f"Mega Evolution: {'Last Pokemon only' if last_mega else 'Yes'}")

        # Poke1-6 are fields 7-12 (PokeDataBattle sub-tables)
        for slot in range(6):
            poke_off = fb_field(data, vt, to, 7 + slot, '<H')
            if not poke_off:
                continue
            # Navigate to sub-table
            abs_off = to + poke_off
            sub_off = abs_off + struct.unpack_from('<I', data, abs_off)[0]
            pvt, pto = fb_table(data, sub_off)
            dev = fb_field(data, pvt, pto, 0, '<H') or 0
            if dev == 0:
                continue
            form = fb_field(data, pvt, pto, 1, '<h') or 0
            sex = fb_field(data, pvt, pto, 2, '<B') or 0
            item = fb_field(data, pvt, pto, 3, '<i') or 0
            level = fb_field(data, pvt, pto, 4, '<i') or 0

            sp_name = species_list[dev] if dev < len(species_list) else f"#{dev}"
            form_str = f"-{form}" if form else ""
            item_name = item_list[item] if item and item < len(item_list) else ""
            item_str = f" @ {item_name}" if item_name else ""

            # Moves are fields 6-9 (WazaSetBattle sub-tables)
            move_names = []
            for mi in range(4):
                m_off = fb_field(data, pvt, pto, 6 + mi, '<H')
                if m_off:
                    m_abs = pto + m_off
                    m_sub = m_abs + struct.unpack_from('<I', data, m_abs)[0]
                    mvt, mto = fb_table(data, m_sub)
                    mid = fb_field(data, mvt, mto, 0, '<H') or 0
                    if mid:
                        move_names.append(moves_list[mid] if mid < len(moves_list) else f"move#{mid}")

            moves_str = f" [{' / '.join(move_names)}]" if move_names else ""
            out.append(f"  {slot+1}. {sp_name}{form_str} Lv{level}{item_str}{moves_str}")

        # AI flags (fields 13-20)
        ai_names = ['Basic', 'High', 'Expert', 'Double', 'Raid', 'Weak', 'Item', 'Change']
        ai_flags = [name for i, name in enumerate(ai_names) if fb_field(data, vt, to, 13 + i, '<B')]
        if ai_flags:
            out.append(f"AI: {' / '.join(ai_flags)}")

        # Vision cone (fields 21-24)
        h_angle = fb_field(data, vt, to, 21, '<f')
        v_angle = fb_field(data, vt, to, 22, '<f')
        v_range = fb_field(data, vt, to, 23, '<f')
        hearing = fb_field(data, vt, to, 24, '<f')
        if h_angle and v_range:
            out.append(f"Detection: {h_angle:.0f}x{v_angle:.0f} deg, {v_range:.1f}m range, {hearing:.1f}m hearing")

        return "\n".join(out)

    # ── decode_item ─────────────────────────────────────────────────
    def decode_item(self, data, file_idx, text_tables):
        """Decode ZA item data (FlatBuffer). Includes ball mechanics and shop prices."""
        if len(data) < 12:
            return None

        root = fb_root(data)
        vt, to = fb_table(data, root)

        item_id = fb_field(data, vt, to, 0, '<i') or 0
        # Field 1: ItemType, Field 2: InternalName, Field 3: IconName
        item_type = fb_field(data, vt, to, 1, '<i') or 0
        name_off = fb_field(data, vt, to, 2, '<H')
        internal_name = fb_string(data, to, name_off) if name_off else f"item#{item_id}"
        price = fb_field(data, vt, to, 4, '<i') or 0
        pocket = fb_field(data, vt, to, 5, '<i') or 0
        stack_max = fb_field(data, vt, to, 6, '<i') or 0
        cant_hold = fb_field(data, vt, to, 10, '<B') or 0
        tm_move = fb_field(data, vt, to, 11, '<H') or 0
        tm_index = fb_field(data, vt, to, 12, '<i') or 0

        moves_list = text_tables.get('moves', [])
        out = [f"{internal_name} (#{item_id})"]
        out.append(f"Pocket: {pocket} | Stack: {stack_max}")
        if price: out.append(f"Price: {price}")
        if cant_hold: out.append("Cannot be held")
        if tm_move:
            mname = moves_list[tm_move] if tm_move < len(moves_list) else f"move#{tm_move}"
            out.append(f"TM{tm_index}: {mname}")

        # Healing/stat effects (fields 13-46 cover status healing, stat boosts, etc.)
        heal_pct = fb_field(data, vt, to, 31, '<i') or 0
        revive = fb_field(data, vt, to, 33, '<i') or 0
        exp_gain = fb_field(data, vt, to, 34, '<i') or 0
        if heal_pct: out.append(f"Heals: {heal_pct}% HP")
        if revive: out.append(f"Revive: {revive}% HP")
        if exp_gain: out.append(f"EXP: +{exp_gain}")

        return "\n".join(out)

    # ── decode_donut ────────────────────────────────────────────────
    def decode_donut(self, data, file_idx, text_tables, path='', **kwargs):
        """Decode ZA donut system. Berry -> recipe -> flavor -> power.
        DLC currencies (Mega Shards, Colorful Screws) live here.
        Path determines section: world/exl/donut/{berry,donut_recipe,flavor,...}"""
        if len(data) < 12:
            return None

        root = fb_root(data)
        vt, to = fb_table(data, root)

        section = path.replace(self.DONUT_PATH + '/', '').split('/')[0] if self.DONUT_PATH in path else ''
        if section == 'berry':
            item_id = fb_field(data, vt, to, 0, '<I') or 0
            donut_idx = fb_field(data, vt, to, 1, '<I') or 0
            is_hyper = fb_field(data, vt, to, 2, '<B') or 0
            spicy = fb_field(data, vt, to, 3, '<I') or 0
            fresh = fb_field(data, vt, to, 4, '<I') or 0
            sweet = fb_field(data, vt, to, 5, '<I') or 0
            bitter = fb_field(data, vt, to, 6, '<I') or 0
            sour = fb_field(data, vt, to, 7, '<I') or 0
            lv_boost = fb_field(data, vt, to, 8, '<I') or 0
            calories = fb_field(data, vt, to, 9, '<I') or 0
            items = text_tables.get('items', [])
            iname = items[item_id] if item_id < len(items) else f"item#{item_id}"
            out = [f"{iname} (donut #{donut_idx})"]
            out.append(f"Spicy {spicy} | Fresh {fresh} | Sweet {sweet} | Bitter {bitter} | Sour {sour}")
            out.append(f"Calories: {calories} | Lv Boost: {lv_boost}")
            if is_hyper: out.append("Hyperspace Berry")
            return "\n".join(out)

        if section == 'donut_recipe':
            idx = fb_field(data, vt, to, 0, '<I') or 0
            s_min = fb_field(data, vt, to, 1, '<I') or 0
            s_max = fb_field(data, vt, to, 2, '<I') or 0
            sp_min = fb_field(data, vt, to, 3, '<I') or 0
            sp_max = fb_field(data, vt, to, 4, '<I') or 0
            so_min = fb_field(data, vt, to, 5, '<I') or 0
            so_max = fb_field(data, vt, to, 6, '<I') or 0
            b_min = fb_field(data, vt, to, 7, '<I') or 0
            b_max = fb_field(data, vt, to, 8, '<I') or 0
            f_min = fb_field(data, vt, to, 9, '<I') or 0
            f_max = fb_field(data, vt, to, 10, '<I') or 0
            name_off = fb_field(data, vt, to, 12, '<H')
            rname = fb_string(data, to, name_off) if name_off else f"recipe#{idx}"
            out = [f"Recipe: {rname} (#{idx})"]
            out.append(f"Sweet {s_min}-{s_max} | Spicy {sp_min}-{sp_max} | Sour {so_min}-{so_max} | Bitter {b_min}-{b_max} | Fresh {f_min}-{f_max}")
            return "\n".join(out)

        if section == 'flavor_parameter':
            flav_off = fb_field(data, vt, to, 0, '<H')
            flavor = fb_string(data, to, flav_off) if flav_off else "?"
            boost_arg = fb_field(data, vt, to, 3, '<i') or 0
            is_battle = fb_field(data, vt, to, 6, '<B') or 0
            level = fb_field(data, vt, to, 7, '<i') or 0
            is_special = fb_field(data, vt, to, 9, '<B') or 0
            name_off = fb_field(data, vt, to, 10, '<H')
            fname = fb_string(data, to, name_off) if name_off else flavor
            out = [f"Flavor: {fname} (Lv{level})"]
            out.append(f"Power: {flavor} | Boost: type#{boost_arg}")
            if is_battle: out.append("Battle Stat Boost")
            if is_special: out.append("Special Flavor")
            return "\n".join(out)

        return None

    # ── decode_dimension ────────────────────────────────────────────
    def decode_dim(self, data, file_idx, text_tables, path='', **kwargs):
        """Decode ZA dimension system. Rank progression + bosses + zones.
        Wild encounters branch to decode_encounters.
        Path determines section: world/ik_data/field/dimension/{dimension_rank,...}"""
        if len(data) < 12:
            return None

        root = fb_root(data)
        vt, to = fb_table(data, root)

        section = path.replace(self.DIM_PATH + '/', '').split('/')[0] if self.DIM_PATH in path else ''
        if section == 'dimension_wild_pokemon':
            return self.decode_encounters(data, file_idx, text_tables, path=path)
        if section == 'dimension_rank':
            rank = fb_field(data, vt, to, 0, '<i') or 0
            ext_level = fb_field(data, vt, to, 1, '<i') or 0
            est_level = fb_field(data, vt, to, 2, '<i') or 0
            limit_speed = fb_field(data, vt, to, 3, '<f') or 0.0
            rank_str = self.ZA_RANKS[rank] if rank < len(self.ZA_RANKS) else f"#{rank}"
            out = [f"Dimension Rank {rank_str}"]
            out.append(f"Extension Lv: {ext_level} | Estimate Lv: {est_level}")
            out.append(f"Limit Reduction: {limit_speed:.2f}")
            return "\n".join(out)

        if section == 'dimension_boss_lottery':
            species_list = text_tables.get('species', [])
            boss_id_off = fb_field(data, vt, to, 0, '<H')
            boss_id = fb_string(data, to, boss_id_off) if boss_id_off else "?"
            rank = fb_field(data, vt, to, 2, '<i') or 0
            est_lv = fb_field(data, vt, to, 3, '<i') or 0
            ext_lv = fb_field(data, vt, to, 4, '<i') or 0
            rematch = fb_field(data, vt, to, 5, '<B') or 0
            dev_no = fb_field(data, vt, to, 6, '<i') or 0
            form = fb_field(data, vt, to, 7, '<i') or 0
            sp_name = species_list[dev_no] if dev_no < len(species_list) else f"#{dev_no}"
            form_str = f"-{form}" if form else ""
            rank_str = self.ZA_RANKS[rank] if rank < len(self.ZA_RANKS) else f"#{rank}"
            out = [f"Boss: {sp_name}{form_str} [{boss_id}]"]
            out.append(f"Rank {rank_str} | Lv {est_lv}+{ext_lv}")
            if rematch: out.append("Rematch available")
            return "\n".join(out)

        if section == 'dimension_progress':
            prog_id_off = fb_field(data, vt, to, 0, '<H')
            prog_id = fb_string(data, to, prog_id_off) if prog_id_off else "?"
            wild_min = fb_field(data, vt, to, 1, '<i') or 0
            wild_max = fb_field(data, vt, to, 2, '<i') or 0
            battle_min = fb_field(data, vt, to, 3, '<i') or 0
            battle_max = fb_field(data, vt, to, 4, '<i') or 0
            event_min = fb_field(data, vt, to, 5, '<i') or 0
            event_max = fb_field(data, vt, to, 6, '<i') or 0
            out = [f"Dimension Progress: {prog_id}"]
            out.append(f"Wild: {wild_min}-{wild_max} | Battle: {battle_min}-{battle_max} | Event: {event_min}-{event_max}")
            return "\n".join(out)

        return None

    # ── decode_za_royale ────────────────────────────────────────────
    def decode_za_royale(self, data, file_idx, text_tables, path='', **kwargs):
        """Decode ZA Royale capture system. Ball multipliers, status bonuses,
        back-strike, AI state, rank-based catch scaling.
        Path: world/ik_data/capture/{capture_ball_data, capture_data, capture_zarank_data}"""
        if len(data) < 12:
            return None

        root = fb_root(data)
        vt, to = fb_table(data, root)
        section = path.replace(self.ZA_ROYALE_PATH + '/', '').split('/')[0] if self.ZA_ROYALE_PATH in path else ''

        if section == 'capture_ball_data':
            item_list = text_tables.get('items', [])
            out = ['Ball Catch Rate Multipliers']
            # Read all fields as f32 - each is a ball's catch multiplier
            fi = 0
            while True:
                val = fb_field(data, vt, to, fi, '<f')
                if val is None:
                    break
                if val != 0.0:
                    out.append(f'  field {fi}: {val:.2f}x')
                fi += 1
            return '\n'.join(out)

        if section == 'capture_data':
            out = ['Capture Mechanics']
            # Sick sub-table (field 0)
            sick_off = fb_field(data, vt, to, 0, '<H')
            if sick_off:
                sb = to + sick_off
                s_sub = sb + struct.unpack_from('<I', data, sb)[0]
                svt, sto = fb_table(data, s_sub)
                status = [('Freeze', 0), ('Sleep', 1), ('Poison', 2), ('Burn', 3), ('Paralysis', 4)]
                bonuses = []
                for name, fi in status:
                    v = fb_field(data, svt, sto, fi, '<f')
                    if v: bonuses.append(f'{name} {v:.2f}x')
                if bonuses: out.append(f'  Status: {", ".join(bonuses)}')
            rare = fb_field(data, vt, to, 1, '<f')
            if rare: out.append(f'  Shiny bonus: {rare:.2f}x')
            # BackStrike (field 2)
            bs_off = fb_field(data, vt, to, 2, '<H')
            if bs_off:
                bb = to + bs_off
                b_sub = bb + struct.unpack_from('<I', data, bb)[0]
                bvt, bto = fb_table(data, b_sub)
                cow = fb_field(data, bvt, bto, 0, '<f')
                ncow = fb_field(data, bvt, bto, 1, '<f')
                out.append(f'  Back-strike: coward {cow:.2f}x / brave {ncow:.2f}x')
            oyabun = fb_field(data, vt, to, 6, '<f')
            oyabun_ch = fb_field(data, vt, to, 7, '<f')
            if oyabun: out.append(f'  Alpha penalty: {oyabun:.2f}x (chance {oyabun_ch:.2f})')
            angle = fb_field(data, vt, to, 9, '<i')
            height = fb_field(data, vt, to, 10, '<i')
            if angle: out.append(f'  Back-strike angle: {angle} deg, height: {height}')
            return '\n'.join(out)

        if section == 'capture_zarank_data':
            out = ['ZA Rank Catch Rate Scaling']
            # Field 0: LevelThreshold (inline struct, 10 ints)
            thresh_off = fb_field(data, vt, to, 0, '<H')
            if thresh_off:
                tb = to + thresh_off
                levels = [struct.unpack_from('<i', data, tb + i*4)[0] for i in range(10)]
                out.append(f'  Level brackets: {levels}')
            # Fields 1-13: rank arrays (Z,Y,X,W,V,G,F,E,D,C,B,A,Inf)
            rank_names = self.ZA_RANKS
            for ri, rname in enumerate(rank_names):
                r_off = fb_field(data, vt, to, 1 + ri, '<H')
                if r_off:
                    rb = to + r_off
                    ratios = [struct.unpack_from('<f', data, rb + i*4)[0] for i in range(10)]
                    ratios_str = ' '.join(f'{r:.2f}' for r in ratios)
                    out.append(f'  Rank {rname}: [{ratios_str}]')
            return '\n'.join(out)

        return None

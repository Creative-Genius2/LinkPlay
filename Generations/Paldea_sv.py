"""Paldea_sv.py: Scarlet / Violet.

Inherits Sinnoh_prequel. Gen IX: Terastallize, open world, Trinity engine.
DLC: The Teal Mask (Kitakami), The Indigo Disk (Blueberry Academy).
"""
from Generations.Sinnoh_prequel import Sinnoh_prequel

import struct
from leswitch import fb_field, fb_root, fb_string, fb_table, fb_vector


class Paldea_sv(Sinnoh_prequel):
    """Scarlet / Violet."""

    GAME_CODES = ('SCA', 'VIO')
    TITLES = ('POKÉMON SCARLET', 'POKÉMON VIOLET')
    YEAR = 2022

    GEN = 9
    SPECIES_COUNT = 1025

    # -- Trinity filesystem --
    CONTAINER = 'trinity'
    TRINITY_DATA_PATH = 'arc/data.trpfs'
    TRINITY_DIR_PATH = 'arc/data.trpfd'

    # -- Internal paths (resolved from inside Trinity archive) --
    # Same pml/ pattern as SWSH/PLA once Trinity is unpacked
    PERSONAL_PATH = 'bin/pml/personal'
    LEARNSET_PATH = 'bin/pml/waza_oboe'
    EVOLUTION_PATH = 'bin/pml/evolution'
    MOVE_DATA_PATH = 'bin/pml/waza'
    ITEM_PATH = 'bin/pml/item'
    TEXT_PATH = 'bin/message/English/common'
    TEXT_SCRIPT_PATH = 'bin/message/English/script'
    TRDATA_PATH = 'bin/trainer'
    ENCOUNTER_PATH = 'world/data/encount/pokedata/encount_poke_data'
    RAID_PATH = 'world/data/raid/raid_enemy_table_array'
    FOOD_BUF_PATH = 'world/data/cooking/cooking_buf_data'
    FOOD_RECIPE_PATH = 'world/data/cooking/cooking_recipe_data'
    OUTBREAK_PATH = 'world/data/outbreak/delivery_outbreak_poke_data'
    SHOP_PATH = 'bin/appli/shop/shop_data'

    # -- Removed / restructured from SWSH --
    TRPOKE_PATH = None
    TRAINER_CLASS_PATH = None
    WILD_AREA_PATH = None
    DYNAMAX_PATH = None
    NEST_DATA_PATH = None
    SYMBOL_BEHAVE_PATH = None


    FOOD_SKILL = ('None', 'Egg Power', 'Catching Power', 'EXP Power', 'Item Drop',
                  'Raid Power', 'Title Power', 'Sparkling Power', 'Humungo Power',
                  'Teensy Power', 'Encounter Power')

    # Species classification enums (Gen 9+)
    CLASS_MAJOR = {1: 'Legendary', 2: 'Sub-Legendary', 3: 'Mythical'}
    CLASS_MINOR = {1: 'Ultra Beast', 2: 'Paradox (Past)', 4: 'Paradox (Future)', 8: 'Mega Evolution'}

    def decode_personal(self, data, file_idx, text_tables, tm_table=None):
        """Decode SV personal data (FlatBuffer with sub-tables).
        SV schema has KitakamiDex/BlueberryDex at fields 3-4 (removed in ZA),
        shifting all subsequent fields +2 vs ZA."""
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
        present = bool(present) if present is not None else True

        # Field 2: Dex (nullable struct)
        # Field 3: KitakamiDex, Field 4: BlueberryDex (SV-only)
        kitakami_dex = fb_field(data, vt, to, 3, '<H') or 0
        blueberry_dex = fb_field(data, vt, to, 4, '<H') or 0

        # Fields 5-6: Types (ZA: 3-4)
        type1 = fb_field(data, vt, to, 5, '<B') or 0
        type2 = fb_field(data, vt, to, 6, '<B') or 0
        # Fields 7-9: Abilities (ZA: 5-7)
        ab1 = fb_field(data, vt, to, 7, '<H') or 0
        ab2 = fb_field(data, vt, to, 8, '<H') or 0
        abH = fb_field(data, vt, to, 9, '<H') or 0
        # Field 10: EXPGrowth, Field 11: CatchRate (ZA: 8-9)
        exp_growth = fb_field(data, vt, to, 10, '<B') or 0
        catch_rate = fb_field(data, vt, to, 11, '<B') or 0
        # Field 12: Gender struct (ZA: 10)
        gender_off = fb_field(data, vt, to, 12, '<H')
        gender_group = gender_ratio = 0
        if gender_off:
            g_base = to + gender_off
            gender_group = data[g_base]
            gender_ratio = data[g_base + 1]
        # Fields 13-14: EggGroups (ZA: 11-12)
        egg1 = fb_field(data, vt, to, 13, '<B') or 0
        egg2 = fb_field(data, vt, to, 14, '<B') or 0
        # Field 15: Hatch struct (ZA: 13)
        # Field 16: HatchCycles, Field 17: BaseFriendship (ZA: 14-15)
        hatch_cycles = fb_field(data, vt, to, 16, '<B') or 0
        friendship = fb_field(data, vt, to, 17, '<B') or 0
        # Field 18: BaseEXPAddend (ZA: 16)
        exp_addend = fb_field(data, vt, to, 18, '<h') or 0
        # Field 19: EvoStage (ZA: 17)
        evo_stage = fb_field(data, vt, to, 19, '<B') or 0
        # Field 20: IsTypeChangeDisallowed (ZA: 18)
        # Field 21: EVYield struct (ZA: 19)
        ev_off = fb_field(data, vt, to, 21, '<H')
        evs = []
        if ev_off:
            ev_base = to + ev_off
            for i, stat in enumerate(self.EV_STAT_ORDER):
                val = data[ev_base + i]
                if val: evs.append(f"+{val} {stat}")
        # Field 22: Base stats struct (ZA: 20)
        stat_off = fb_field(data, vt, to, 22, '<H')
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

        # DLC dex indices (SV-only)
        dex_parts = []
        if kitakami_dex: dex_parts.append(f"Kitakami #{kitakami_dex}")
        if blueberry_dex: dex_parts.append(f"Blueberry #{blueberry_dex}")
        if dex_parts:
            out.append(f"DLC Dex: {' | '.join(dex_parts)}")

        # Classification
        tags = []
        if cls_major in self.CLASS_MAJOR: tags.append(self.CLASS_MAJOR[cls_major])
        if cls_minor in self.CLASS_MINOR: tags.append(self.CLASS_MINOR[cls_minor])
        if tags:
            out.append(f"Classification: {' / '.join(tags)}")

        # Field 23: Evolutions (ZA: 21)
        evo_voff = fb_field(data, vt, to, 23, '<H')
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

        # Field 24: TechnicalMachine (ZA: 22)
        tm_voff = fb_field(data, vt, to, 24, '<H')
        if tm_voff:
            count, doff = fb_vector(data, to, tm_voff)
            tms = []
            for i in range(count):
                mid = struct.unpack_from('<H', data, doff + i * 2)[0]
                mname = moves_list[mid] if mid < len(moves_list) else f"move#{mid}"
                tms.append(mname)
            if tms:
                out.append(f"TMs ({len(tms)}): {' / '.join(tms)}")

        # Field 25: EggMoves (ZA: 23)
        egg_voff = fb_field(data, vt, to, 25, '<H')
        if egg_voff:
            count, doff = fb_vector(data, to, egg_voff)
            eggs = []
            for i in range(count):
                mid = struct.unpack_from('<H', data, doff + i * 2)[0]
                eggs.append(moves_list[mid] if mid < len(moves_list) else f"move#{mid}")
            if eggs:
                out.append(f"Egg Moves: {' / '.join(eggs)}")

        # Field 26: ReminderMoves (ZA: 24)
        rem_voff = fb_field(data, vt, to, 26, '<H')
        if rem_voff:
            count, doff = fb_vector(data, to, rem_voff)
            rems = []
            for i in range(count):
                mid = struct.unpack_from('<H', data, doff + i * 2)[0]
                rems.append(moves_list[mid] if mid < len(moves_list) else f"move#{mid}")
            if rems:
                out.append(f"Reminder: {' / '.join(rems)}")

        # Field 27: Learnset (ZA: 25)
        learn_voff = fb_field(data, vt, to, 27, '<H')
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
        """Decode SV move data (Waza FlatBuffer). Identical schema to ZA."""
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

    # ── decode_trainer ─────────────────────────────────────────────
    def decode_trainer(self, data, file_idx, text_tables):
        """Decode SV trainer (TrDataMain FlatBuffer).
        SV schema: TrId/TrNameLabel/TrainerType strings, BattleType, ChangeGem (Tera),
        Poke1-6 at fields 8-13, AI at 14-21."""
        if len(data) < 12:
            return None

        species_list = text_tables.get('species', [])
        moves_list = text_tables.get('moves', [])
        item_list = text_tables.get('items', [])

        root = fb_root(data)
        vt, to = fb_table(data, root)

        tr_id_off = fb_field(data, vt, to, 0, '<H')
        tr_id = fb_string(data, to, tr_id_off) if tr_id_off else "?"
        tr_name_off = fb_field(data, vt, to, 1, '<H')
        tr_name = fb_string(data, to, tr_name_off) if tr_name_off else ""
        tr_type_off = fb_field(data, vt, to, 2, '<H')
        tr_type = fb_string(data, to, tr_type_off) if tr_type_off else ""
        is_strong = fb_field(data, vt, to, 3, '<B') or 0
        battle_type = fb_field(data, vt, to, 4, '<B') or 0
        data_type = fb_field(data, vt, to, 5, '<B') or 0
        money_rate = fb_field(data, vt, to, 6, '<B') or 0
        change_gem = fb_field(data, vt, to, 7, '<B') or 0

        battle_str = self.BATTLE_TYPES[battle_type] if battle_type < len(self.BATTLE_TYPES) else f'type#{battle_type}'
        out = [f"Trainer: {tr_id} | {tr_type} {tr_name} | {battle_str} | Money x{money_rate}"]
        if is_strong:
            out.append("Strong Trainer")
        if change_gem:
            out.append("Terastallizes")

        # Poke1-6 at fields 8-13 (PokeDataBattle sub-tables, same as ZA)
        for slot in range(6):
            poke_off = fb_field(data, vt, to, 8 + slot, '<H')
            if not poke_off:
                continue
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

        # AI flags (fields 14-21)
        ai_names = ['Basic', 'High', 'Expert', 'Double', 'Raid', 'Weak', 'Item', 'Change']
        ai_flags = [name for i, name in enumerate(ai_names) if fb_field(data, vt, to, 14 + i, '<B')]
        if ai_flags:
            out.append(f"AI: {' / '.join(ai_flags)}")

        return "\n".join(out)


    # ── decode_encounters ─────────────────────────────────────────

    def decode_encounters(self, data, file_idx, text_tables, path='', **kwargs):
        """Decode SV encounter data (EncountPokeData FlatBuffer).
        Biome-based with lot values, area/location strings, band (horde) data."""
        if len(data) < 12:
            return None

        species_list = text_tables.get('species', [])
        root = fb_root(data)
        vt, to = fb_table(data, root)

        dev_id = fb_field(data, vt, to, 0, '<i') or 0
        sex = fb_field(data, vt, to, 1, '<B') or 0
        form = fb_field(data, vt, to, 2, '<b') or 0
        min_lv = fb_field(data, vt, to, 3, '<h') or 0
        max_lv = fb_field(data, vt, to, 4, '<h') or 0
        lot_value = fb_field(data, vt, to, 5, '<h') or 0

        # Biome slots (fields 6-13: 4 pairs of Biome enum + LotValue)
        biomes = []
        for i in range(4):
            biome = fb_field(data, vt, to, 6 + i * 2, '<i') or 0
            blot = fb_field(data, vt, to, 7 + i * 2, '<h') or 0
            if biome and blot:
                biomes.append(f"biome#{biome}:{blot}")

        # Area/Location strings (fields 14-15)
        area_off = fb_field(data, vt, to, 14, '<H')
        area = fb_string(data, to, area_off) if area_off else ""
        loc_off = fb_field(data, vt, to, 15, '<H')
        location = fb_string(data, to, loc_off) if loc_off else ""

        min_h = fb_field(data, vt, to, 16, '<i') or 0
        max_h = fb_field(data, vt, to, 17, '<i') or 0

        # Band/horde data (fields 21-25)
        band_rate = fb_field(data, vt, to, 21, '<h') or 0
        band_type = fb_field(data, vt, to, 22, '<i') or 0
        band_poke = fb_field(data, vt, to, 23, '<i') or 0

        outbreak_lot = fb_field(data, vt, to, 26, '<B') or 0

        sp_name = species_list[dev_id] if dev_id < len(species_list) else f"#{dev_id}"
        form_str = f"-{form}" if form else ""
        lv_str = f"Lv{min_lv}" if min_lv == max_lv else f"Lv{min_lv}-{max_lv}"
        sex_str = self.SEX_SYMBOLS[sex] if sex < len(self.SEX_SYMBOLS) else ''

        out = [f"{sp_name}{form_str}{sex_str} {lv_str} (weight {lot_value})"]
        if area or location:
            out.append(f"Area: {area} | Location: {location}")
        if biomes:
            out.append(f"Biomes: {' / '.join(biomes)}")
        if min_h or max_h:
            out.append(f"Height: {min_h}-{max_h}")
        if band_rate:
            bp_name = species_list[band_poke] if band_poke < len(species_list) else f"#{band_poke}"
            out.append(f"Horde: {bp_name} (rate {band_rate}, type {band_type})")
        if outbreak_lot:
            out.append(f"Outbreak weight: {outbreak_lot}")

        return "\n".join(out)

    # ── decode_item ───────────────────────────────────────────────

    def decode_item(self, data, file_idx, text_tables, path='', **kwargs):
        """Decode SV item data (ItemData FlatBuffer).
        Subsets: shop, TM_machine (dispatched by path section)."""
        if len(data) < 12:
            return None

        moves_list = text_tables.get('moves', [])
        root = fb_root(data)
        vt, to = fb_table(data, root)

        item_id = fb_field(data, vt, to, 0, '<i') or 0
        item_type = fb_field(data, vt, to, 1, '<i') or 0
        icon_off = fb_field(data, vt, to, 2, '<H')
        icon_name = fb_string(data, to, icon_off) if icon_off else f"item#{item_id}"
        price = fb_field(data, vt, to, 3, '<i') or 0
        bp = fb_field(data, vt, to, 4, '<i') or 0
        equip_effect = fb_field(data, vt, to, 5, '<i') or 0
        equip_power = fb_field(data, vt, to, 6, '<i') or 0
        machine_waza = fb_field(data, vt, to, 12, '<i') or 0
        sort_num = fb_field(data, vt, to, 13, '<i') or 0
        item_group = fb_field(data, vt, to, 14, '<i') or 0
        group_id = fb_field(data, vt, to, 15, '<i') or 0
        pocket = fb_field(data, vt, to, 16, '<i') or 0
        field_func = fb_field(data, vt, to, 17, '<i') or 0
        battle_func = fb_field(data, vt, to, 18, '<i') or 0

        # Healing fields
        heal_hp = fb_field(data, vt, to, 40, '<i') or 0
        revival = fb_field(data, vt, to, 57, '<i') or 0

        out = [f"{icon_name} (#{item_id})"]
        out.append(f"Pocket: {pocket} | Group: {item_group}/{group_id}")
        if price: out.append(f"Price: {price}")
        if bp: out.append(f"BP cost: {bp}")
        if equip_effect: out.append(f"Equip effect: {equip_effect} (power {equip_power})")
        if machine_waza:
            mname = moves_list[machine_waza] if machine_waza < len(moves_list) else f"move#{machine_waza}"
            out.append(f"TM: {mname}")
        if heal_hp: out.append(f"Heals: {heal_hp} HP")
        if revival: out.append(f"Revive: {revival}")

        return "\n".join(out)

    # ── decode_raid ───────────────────────────────────────────────

    def decode_raid(self, data, file_idx, text_tables, path='', **kwargs):
        """Decode SV Tera raid data (RaidEnemyTable → RaidEnemyInfo FlatBuffer).
        Wraps PokeDataBattle for boss pokemon, plus shield/HP/timer mechanics."""
        if len(data) < 12:
            return None

        species_list = text_tables.get('species', [])
        moves_list = text_tables.get('moves', [])
        item_list = text_tables.get('items', [])

        root = fb_root(data)
        vt, to = fb_table(data, root)

        # RaidEnemyTable has one field: Info (RaidEnemyInfo sub-table)
        info_off = fb_field(data, vt, to, 0, '<H')
        if not info_off:
            return None
        abs_off = to + info_off
        sub_off = abs_off + struct.unpack_from('<I', data, abs_off)[0]
        ivt, ito = fb_table(data, sub_off)

        rom_ver = fb_field(data, ivt, ito, 0, '<i') or 0
        raid_no = fb_field(data, ivt, ito, 1, '<i') or 0
        group_id = fb_field(data, ivt, ito, 2, '<B') or 0
        difficulty = fb_field(data, ivt, ito, 3, '<i') or 0
        rate = fb_field(data, ivt, ito, 4, '<B') or 0
        capture_rate = fb_field(data, ivt, ito, 7, '<B') or 0
        capture_lv = fb_field(data, ivt, ito, 8, '<B') or 0

        # BossPokePara (field 9) — PokeDataBattle sub-table
        poke_off = fb_field(data, ivt, ito, 9, '<H')
        sp_name = "?"
        form_str = ""
        level = 0
        move_names = []
        if poke_off:
            p_abs = ito + poke_off
            p_sub = p_abs + struct.unpack_from('<I', data, p_abs)[0]
            pvt, pto = fb_table(data, p_sub)
            dev = fb_field(data, pvt, pto, 0, '<H') or 0
            form = fb_field(data, pvt, pto, 1, '<h') or 0
            level = fb_field(data, pvt, pto, 4, '<i') or 0
            sp_name = species_list[dev] if dev < len(species_list) else f"#{dev}"
            form_str = f"-{form}" if form else ""
            for mi in range(4):
                m_off = fb_field(data, pvt, pto, 6 + mi, '<H')
                if m_off:
                    m_abs = pto + m_off
                    m_sub = m_abs + struct.unpack_from('<I', data, m_abs)[0]
                    mvt, mto = fb_table(data, m_sub)
                    mid = fb_field(data, mvt, mto, 0, '<H') or 0
                    if mid:
                        move_names.append(moves_list[mid] if mid < len(moves_list) else f"move#{mid}")

        # BossDesc (field 11) — RaidBossData sub-table
        boss_off = fb_field(data, ivt, ito, 11, '<H')
        hp_coef = 0
        shield_hp = shield_time = 0
        if boss_off:
            b_abs = ito + boss_off
            b_sub = b_abs + struct.unpack_from('<I', data, b_abs)[0]
            bvt, bto = fb_table(data, b_sub)
            hp_coef = fb_field(data, bvt, bto, 0, '<h') or 0
            shield_hp = fb_field(data, bvt, bto, 1, '<B') or 0
            shield_time = fb_field(data, bvt, bto, 2, '<B') or 0

        ver_str = ('', ' [Scarlet]', ' [Violet]')[rom_ver] if rom_ver < 3 else ''
        star_str = f"{'★' * difficulty}" if difficulty else ""

        out = [f"Raid #{raid_no}: {sp_name}{form_str} Lv{level} {star_str}{ver_str}"]
        if move_names:
            out.append(f"Moves: {' / '.join(move_names)}")
        out.append(f"Rate: {rate} | Catch: {capture_rate}% Lv{capture_lv}")
        if hp_coef:
            out.append(f"HP Coef: {hp_coef}x | Shield trigger: {shield_hp}% HP / {shield_time}s")

        return "\n".join(out)

    # ── decode_food ───────────────────────────────────────────────

    def decode_food(self, data, file_idx, text_tables, path='', **kwargs):
        """Decode SV sandwich/cooking data (RecipeData + BufData FlatBuffers).
        Dispatches by path: recipe → ingredients, buf → buff effects."""
        if len(data) < 12:
            return None



        root = fb_root(data)
        vt, to = fb_table(data, root)

        # BufData: sandwich buff effects
        if path == self.FOOD_BUF_PATH:
            bufid_off = fb_field(data, vt, to, 0, '<H')
            bufid = fb_string(data, to, bufid_off) if bufid_off else f"buf#{file_idx}"
            out = [f"Sandwich Buff: {bufid}"]
                for slot in range(3):
                    base = 1 + slot * 3
                    skill = fb_field(data, vt, to, base, '<i') or 0
                    level = fb_field(data, vt, to, base + 1, '<i') or 0
                    poke_type = fb_field(data, vt, to, base + 2, '<i') or 0
                    if skill:
                        sname = self.FOOD_SKILL[skill] if skill < len(self.FOOD_SKILL) else f'skill#{skill}'
                        out.append(f"  {sname} Lv{level} (type {poke_type})")
                return "\n".join(out)

        # RecipeData: recipe ingredients
        recipe_type = fb_field(data, vt, to, 0, '<i') or 0
        seasonings = []
        for i in range(4):
            s = fb_field(data, vt, to, 1 + i, '<i') or 0
            if s: seasonings.append(f"seasoning#{s}")
        ingredients = []
        for i in range(6):
            ing = fb_field(data, vt, to, 5 + i, '<i') or 0
            if ing: ingredients.append(f"ingredient#{ing}")

        out = [f"Recipe #{file_idx} (type {recipe_type})"]
        if seasonings:
            out.append(f"Seasonings: {' / '.join(seasonings)}")
        if ingredients:
            out.append(f"Ingredients: {' / '.join(ingredients)}")

        return "\n".join(out)

    # ── decode_outbreak ───────────────────────────────────────────

    def decode_outbreak(self, data, file_idx, text_tables, path='', **kwargs):
        """Decode SV mass outbreak data (DeliveryOutbreakPokeData FlatBuffer)."""
        if len(data) < 12:
            return None

        species_list = text_tables.get('species', [])
        root = fb_root(data)
        vt, to = fb_table(data, root)

        ob_id = fb_field(data, vt, to, 0, '<Q') or 0
        dev_id = fb_field(data, vt, to, 1, '<i') or 0
        sex = fb_field(data, vt, to, 2, '<B') or 0
        form = fb_field(data, vt, to, 3, '<b') or 0
        min_lv = fb_field(data, vt, to, 4, '<h') or 0
        max_lv = fb_field(data, vt, to, 5, '<h') or 0

        # Field 11: EnableRarePercentage, Field 12: RarePercentage
        rare_enabled = fb_field(data, vt, to, 11, '<B') or 0
        rare_pct = fb_field(data, vt, to, 12, '<f') or 0.0

        # Field 15: EnableScaleRange, Fields 16-17: MinScale/MaxScale
        scale_enabled = fb_field(data, vt, to, 15, '<B') or 0
        min_scale = fb_field(data, vt, to, 16, '<h') or 0
        max_scale = fb_field(data, vt, to, 17, '<h') or 0

        sp_name = species_list[dev_id] if dev_id < len(species_list) else f"#{dev_id}"
        form_str = f"-{form}" if form else ""
        sex_str = self.SEX_SYMBOLS[sex] if sex < len(self.SEX_SYMBOLS) else ''
        lv_str = f"Lv{min_lv}" if min_lv == max_lv else f"Lv{min_lv}-{max_lv}"

        out = [f"Outbreak: {sp_name}{form_str}{sex_str} {lv_str} [ID {ob_id:#x}]"]
        if rare_enabled and rare_pct:
            out.append(f"Shiny rate: {rare_pct:.2%}")
        if scale_enabled:
            out.append(f"Scale: {min_scale}-{max_scale}")

        return "\n".join(out)
    FLIPNOTE_PAIRS = {
        'Pokemon Scarlet & Violet': ['SCA', 'VIO'],
    }

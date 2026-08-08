"""Sinnoh_prequel.py: Legends: Arceus.

Inherits Galar_swsh. Ancient Sinnoh (Hisui region). Open-world action RPG.
Agile/Strong style moves, alpha Pokemon, research tasks, crafting.
"""
from Generations.Galar_swsh import Galar_swsh

import struct
from leswitch import fb_field, fb_root, fb_string, fb_table, fb_vector


class Sinnoh_prequel(Galar_swsh):
    """Legends: Arceus."""

    GAME_CODES = ('PLA',)
    TITLES = ('POKÉMON LEGENDS: ARCEUS',)
    YEAR = 2022

    SPECIES_COUNT = 242

    # -- Paths (from pkNX GameFileMapping.cs) --
    PERSONAL_PATH = 'bin/pml/personal/personal_data_total.perbin'
    LEARNSET_PATH = 'bin/pml/waza_oboe/waza_oboe_total.wazaoboe'
    EVOLUTION_PATH = 'bin/pml/evolution/evolution_data_total.evobin'
    MOVE_DATA_PATH = 'bin/pml/waza'
    ITEM_PATH = 'bin/pml/item/item.dat'
    TEXT_PATH = 'bin/message/English/common'
    TEXT_SCRIPT_PATH = 'bin/message/English/script'
    TRDATA_PATH = 'bin/trainer'

    # -- Encounters --
    STATIC_ENCOUNTER_PATH = 'bin/pokemon/data/poke_event_encount.bin'
    GIFT_PATH = 'bin/pokemon/data/poke_add.bin'
    TRADE_PATH = 'bin/script_event_data/field_trade.bin'
    OUTBREAK_PATH = 'bin/field/encount/huge_outbreak.bin'
    NEW_OUTBREAK_GROUP_PATH = 'bin/field/encount/new_huge_outbreak_group.bin'
    NEW_OUTBREAK_LOTTERY_PATH = 'bin/field/encount/new_huge_outbreak_lottery.bin'

    # -- PLA-specific --
    MOVE_SHOP_PATH = 'bin/appli/wazaremember/bin/wazashop_table.bin'
    POKE_MISC_PATH = 'bin/pokemon/data/poke_misc.bin'
    DEX_RESEARCH_PATH = 'bin/appli/pokedex/res_table/pokedex_research_task_table.bin'
    FIELD_DROPS_PATH = 'bin/pokemon/data/poke_drop_item.bin'
    BATTLE_DROPS_PATH = 'bin/pokemon/data/poke_drop_item_battle.bin'
    SHINY_ROLLS_PATH = 'bin/misc/app_config/pokemon_rare.bin'
    SHOP_PATH = 'bin/appli/shop/bin/ha_shop_data.bin'
    CRAFT_PATH = 'bin/pokemon/data/poke_make_item.bin'
    THROW_PARAM_PATH = 'bin/pokemon/data/throw_param.bin'
    THROWABLE_PARAM_PATH = 'bin/pokemon/data/throwable_param.bin'

    # -- Container formats --
    PERSONAL_CONTAINER = 'single'
    LEARNSET_CONTAINER = 'single'
    EVOLUTION_CONTAINER = 'single'

    # -- Removed from PLA (not applicable) --
    TRPOKE_PATH = None
    TRAINER_CLASS_PATH = None
    EGG_MOVES_PATH = None
    WILD_AREA_PATH = None
    BATTLE_TOWER_POKE_PATH = None
    BATTLE_TOWER_TRAINER_PATH = None
    DYNAMAX_PATH = None
    NEST_DATA_PATH = None


    def decode_personal(self, data, file_idx, text_tables, tm_table=None):
        """Decode PLA personal data (FlatBuffer). Flat schema — no sub-tables.
        Stats at fields 8-13, types 3-4, abilities 5-7, MoveShop bitfields at 56-57."""
        if len(data) < 12:
            return None

        species_list = text_tables.get('species', [])
        type_list = text_tables.get('type_names', [])
        ability_list = text_tables.get('abilities', [])
        item_list = text_tables.get('items', [])

        root = fb_root(data)
        vt, to = fb_table(data, root)

        dex_national = fb_field(data, vt, to, 0, '<H') or 0
        form = fb_field(data, vt, to, 1, '<H') or 0
        present = fb_field(data, vt, to, 2, '<B')
        present = bool(present) if present is not None else True

        type1 = fb_field(data, vt, to, 3, '<B') or 0
        type2 = fb_field(data, vt, to, 4, '<B') or 0
        ab1 = fb_field(data, vt, to, 5, '<H') or 0
        ab2 = fb_field(data, vt, to, 6, '<H') or 0
        abH = fb_field(data, vt, to, 7, '<H') or 0

        hp = fb_field(data, vt, to, 8, '<B') or 0
        atk = fb_field(data, vt, to, 9, '<B') or 0
        dfn = fb_field(data, vt, to, 10, '<B') or 0
        spa = fb_field(data, vt, to, 11, '<B') or 0
        spd = fb_field(data, vt, to, 12, '<B') or 0
        spe = fb_field(data, vt, to, 13, '<B') or 0
        bst = hp + atk + dfn + spa + spd + spe

        gender = fb_field(data, vt, to, 14, '<B') or 0
        exp_growth = fb_field(data, vt, to, 15, '<B') or 0
        evo_stage = fb_field(data, vt, to, 16, '<B') or 0
        catch_rate = fb_field(data, vt, to, 17, '<B') or 0
        color = fb_field(data, vt, to, 19, '<B') or 0
        height = fb_field(data, vt, to, 20, '<H') or 0
        weight = fb_field(data, vt, to, 21, '<H') or 0
        base_exp = fb_field(data, vt, to, 31, '<H') or 0

        # EVs (fields 32-37)
        evs = []
        ev_names = ('HP', 'Atk', 'Def', 'SpA', 'SpD', 'Spe')
        for i, name in enumerate(ev_names):
            val = fb_field(data, vt, to, 32 + i, '<B') or 0
            if val: evs.append(f"+{val} {name}")

        # Held items (fields 38-40)
        items = []
        for i in range(3):
            iid = fb_field(data, vt, to, 38 + i, '<H') or 0
            if iid:
                iname = item_list[iid] if iid < len(item_list) else f"item#{iid}"
                items.append(iname)

        egg1 = fb_field(data, vt, to, 41, '<B') or 0
        egg2 = fb_field(data, vt, to, 42, '<B') or 0
        hatch_species = fb_field(data, vt, to, 43, '<H') or 0
        hatch_cycles = fb_field(data, vt, to, 47, '<B') or 0
        friendship = fb_field(data, vt, to, 48, '<B') or 0
        dex_hisui = fb_field(data, vt, to, 49, '<H') or 0
        is_regional = fb_field(data, vt, to, 45, '<B') or 0

        # Resolve names
        species_name = species_list[dex_national] if dex_national < len(species_list) else f"#{dex_national}"
        t1 = type_list[type1] if type1 < len(type_list) else f"type#{type1}"
        t2 = type_list[type2] if type2 < len(type_list) else f"type#{type2}"
        types_str = t1 if type1 == type2 else f"{t1} / {t2}"

        ability_names = []
        for aid, suffix in ((ab1, ''), (ab2, ''), (abH, ' (Hidden)')):
            if aid > 0:
                name = ability_list[aid] if aid < len(ability_list) else f"ability#{aid}"
                ability_names.append(f"{name}{suffix}")

        out = [f"{species_name} (#{dex_national})", f"{types_str} | BST {bst}",
               f"HP {hp} | Atk {atk} | Def {dfn} | SpA {spa} | SpD {spd} | Spe {spe}",
               f"Abilities: {' / '.join(ability_names)}" if ability_names else "Abilities: ---"]
        out.append(f"Gender: {gender} | Catch Rate: {catch_rate} | Hatch: {hatch_cycles} cycles | Friendship: {friendship}")
        out.append(f"Growth: {self.GROWTH_NAMES[exp_growth]} | Egg Groups: {self.EGG_GROUP_NAMES[egg1]}" +
                   (f" / {self.EGG_GROUP_NAMES[egg2]}" if egg1 != egg2 else ""))
        out.append(f"Height: {height / 100.0}m | Weight: {weight / 10.0}kg")
        out.append(f"Evo Stage: {evo_stage} | Base EXP: {base_exp}")
        if evs:
            out.append(f"EVs: {', '.join(evs)}")
        if items:
            out.append(f"Held items: {' / '.join(items)}")
        if dex_hisui:
            out.append(f"Hisui Dex: #{dex_hisui}")
        if is_regional:
            out.append("Regional Form")
        if not present:
            out.append("NOT IN GAME")
        if form:
            out.append(f"Form: {form}")

        return "\n".join(out)

    # ── decode_move ───────────────────────────────────────────────

    def decode_move(self, data, file_idx, text_tables):
        """Decode PLA move data (Waza FlatBuffer). Includes Agile/Strong style modifiers."""
        if len(data) < 12:
            return None

        moves_list = text_tables.get('moves', [])
        type_list = text_tables.get('type_names', [])

        root = fb_root(data)
        vt, to = fb_table(data, root)

        move_id = fb_field(data, vt, to, 0, '<i') or 0
        mtype = fb_field(data, vt, to, 2, '<B') or 0
        category = fb_field(data, vt, to, 4, '<B') or 0
        power = fb_field(data, vt, to, 5, '<B') or 0
        accuracy = fb_field(data, vt, to, 6, '<B') or 0
        pp = fb_field(data, vt, to, 7, '<B') or 0
        priority = fb_field(data, vt, to, 8, '<b') or 0
        hit_max = fb_field(data, vt, to, 9, '<B') or 0
        hit_min = fb_field(data, vt, to, 10, '<B') or 0
        inflict = fb_field(data, vt, to, 11, '<h') or 0
        inflict_pct = fb_field(data, vt, to, 12, '<B') or 0
        crit = fb_field(data, vt, to, 16, '<B') or 0
        flinch = fb_field(data, vt, to, 17, '<B') or 0
        recoil = fb_field(data, vt, to, 19, '<B') or 0
        healing = fb_field(data, vt, to, 20, '<B') or 0

        # Agile/Strong style (field 68+)
        can_style = fb_field(data, vt, to, 68, '<B') or 0
        agile_power = fb_field(data, vt, to, 74, '<B') or 0
        strong_power = fb_field(data, vt, to, 79, '<B') or 0
        strong_acc = fb_field(data, vt, to, 80, '<B') or 0
        strong_crit = fb_field(data, vt, to, 81, '<B') or 0

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
        if healing: out.append(f"Healing: {healing}%")

        # Style info
        if can_style:
            style_parts = []
            if agile_power: style_parts.append(f"Agile Power: {agile_power}")
            if strong_power: style_parts.append(f"Strong Power: {strong_power}")
            if strong_acc: style_parts.append(f"Strong Acc: {strong_acc}")
            if strong_crit: style_parts.append(f"Strong Crit: +{strong_crit}")
            out.append(f"Styles: {' | '.join(style_parts)}" if style_parts else "Styles: Yes")

        # Flags
        flag_names = [
            (35, 'Contact'), (36, 'Charge'), (37, 'Recharge'), (38, 'Protect'),
            (39, 'Reflectable'), (40, 'Snatch'), (41, 'Mirror'), (42, 'Punch'),
            (43, 'Sound'), (44, 'Gravity'), (45, 'Defrost'), (47, 'Heal'),
            (48, 'IgnoreSub'), (50, 'AnimateAlly'), (51, 'Dance'), (52, 'Metronome'),
        ]
        flags = [name for fi, name in flag_names if fb_field(data, vt, to, fi, '<B')]
        if flags:
            out.append(f"Flags: {' / '.join(flags)}")

        return "\n".join(out)

    # ── decode_encounters ─────────────────────────────────────────

    def decode_encounters(self, data, file_idx, text_tables, path='', **kwargs):
        """Decode PLA encounter slot (EncounterSlot FlatBuffer).
        Includes alpha traits, IVs/GVs, movesets, time/weather multipliers."""
        if len(data) < 12:
            return None

        species_list = text_tables.get('species', [])
        moves_list = text_tables.get('moves', [])
        root = fb_root(data)
        vt, to = fb_table(data, root)

        species = fb_field(data, vt, to, 0, '<i') or 0
        gender = fb_field(data, vt, to, 2, '<i') or 0
        form = fb_field(data, vt, to, 3, '<i') or 0
        shiny_lock = fb_field(data, vt, to, 4, '<i') or 0
        nature = fb_field(data, vt, to, 6, '<i') or 0
        num_perfect_ivs = fb_field(data, vt, to, 24, '<i') or 0
        base_prob = fb_field(data, vt, to, 28, '<i') or 0
        min_lv = fb_field(data, vt, to, 29, '<i') or 0
        max_lv = fb_field(data, vt, to, 30, '<i') or 0

        # Moveset (fields 33-36, with mastered flags 37-40)
        has_moveset = fb_field(data, vt, to, 32, '<B') or 0
        move_names = []
        if has_moveset:
            for mi in range(4):
                mid = fb_field(data, vt, to, 33 + mi, '<i') or 0
                mastered = fb_field(data, vt, to, 37 + mi, '<B') or 0
                if mid:
                    mname = moves_list[mid] if mid < len(moves_list) else f"move#{mid}"
                    move_names.append(f"{mname}{'*' if mastered else ''}")

        # Oyabun/Alpha traits (field 46)
        oybn_off = fb_field(data, vt, to, 46, '<H')
        is_alpha = False
        if oybn_off:
            o_abs = to + oybn_off
            o_sub = o_abs + struct.unpack_from('<I', data, o_abs)[0]
            ovt, oto = fb_table(data, o_sub)
            is_alpha = bool(fb_field(data, ovt, oto, 0, '<B') or 0)

        sp_name = species_list[species] if species < len(species_list) else f"#{species}"
        form_str = f"-{form}" if form else ""
        lv_str = f"Lv{min_lv}" if min_lv == max_lv else f"Lv{min_lv}-{max_lv}"
        sex_str = self.SEX_SYMBOLS[gender] if gender < len(self.SEX_SYMBOLS) else ''
        shiny_str = '' if shiny_lock == 0 else ' *ShinyLocked*' if shiny_lock == 1 else ' *AlwaysShiny*'

        out = [f"{sp_name}{form_str}{sex_str} {lv_str}{shiny_str} (weight {base_prob})"]
        if is_alpha:
            out.append("ALPHA")
        if num_perfect_ivs:
            out.append(f"IVs: {num_perfect_ivs} perfect")
        if nature:
            out.append(f"Nature: fixed #{nature}")
        if move_names:
            out.append(f"Moves: {' / '.join(move_names)}")

        return "\n".join(out)

    # ── decode_trainer ────────────────────────────────────────────

    def decode_trainer(self, data, file_idx, text_tables):
        """Decode PLA trainer (TrainerData FlatBuffer).
        Team is a vector of TrainerPoke with WazaSet sub-tables for moves."""
        if len(data) < 12:
            return None

        species_list = text_tables.get('species', [])
        moves_list = text_tables.get('moves', [])
        root = fb_root(data)
        vt, to = fb_table(data, root)

        # Music string (field 3)
        music_off = fb_field(data, vt, to, 3, '<H')
        music = fb_string(data, to, music_off) if music_off else ""

        money = fb_field(data, vt, to, 10, '<i') or 0

        out = [f"Trainer #{file_idx} | Music: {music} | Money: {money}"]

        # Team vector (field 22)
        team_off = fb_field(data, vt, to, 22, '<H')
        if team_off:
            count, doff = fb_vector(data, to, team_off)
            for i in range(count):
                # Each element is an offset to a TrainerPoke sub-table
                elem_off = doff + i * 4
                ptr = struct.unpack_from('<I', data, elem_off)[0]
                p_abs = elem_off + ptr
                pvt, pto = fb_table(data, p_abs)

                species = fb_field(data, pvt, pto, 0, '<i') or 0
                form = fb_field(data, pvt, pto, 1, '<i') or 0
                level = fb_field(data, pvt, pto, 6, '<i') or 0
                nature = fb_field(data, pvt, pto, 7, '<i') or 0
                gender = fb_field(data, pvt, pto, 8, '<i') or 0
                shiny = fb_field(data, pvt, pto, 15, '<B') or 0
                is_alpha = fb_field(data, pvt, pto, 16, '<B') or 0

                sp_name = species_list[species] if species < len(species_list) else f"#{species}"
                form_str = f"-{form}" if form else ""

                # Moves (fields 2-5, WazaSet sub-tables: Move int + Mastered bool)
                move_names = []
                for mi in range(4):
                    m_off = fb_field(data, pvt, pto, 2 + mi, '<H')
                    if m_off:
                        m_abs = pto + m_off
                        m_sub = m_abs + struct.unpack_from('<I', data, m_abs)[0]
                        mvt, mto = fb_table(data, m_sub)
                        mid = fb_field(data, mvt, mto, 0, '<i') or 0
                        mastered = fb_field(data, mvt, mto, 1, '<B') or 0
                        if mid:
                            mname = moves_list[mid] if mid < len(moves_list) else f"move#{mid}"
                            move_names.append(f"{mname}{'*' if mastered else ''}")

                moves_str = f" [{' / '.join(move_names)}]" if move_names else ""
                alpha_str = " ALPHA" if is_alpha else ""
                shiny_str = " *Shiny*" if shiny else ""
                out.append(f"  {i+1}. {sp_name}{form_str} Lv{level}{alpha_str}{shiny_str}{moves_str}")

        return "\n".join(out)

    # ── decode_item ───────────────────────────────────────────────

    def decode_item(self, data, file_idx, text_tables, path='', **kwargs):
        """Decode PLA item-related data. Subsets: drops, craftworks.
        Dispatches by path: drop_item → field/battle drops, craft → MakeList recipes."""
        if len(data) < 12:
            return None

        item_list = text_tables.get('items', [])
        root = fb_root(data)
        vt, to = fb_table(data, root)

        # Craftworks (MakeList): RecipeID, RecipeItemID, ResultItemID, materials
        if path == self.CRAFT_PATH:
            recipe_id = fb_field(data, vt, to, 0, '<B') or 0
            recipe_item = fb_field(data, vt, to, 1, '<I') or 0
            result_item = fb_field(data, vt, to, 2, '<I') or 0
            result_qty = fb_field(data, vt, to, 3, '<I') or 0

            rname = item_list[result_item] if result_item < len(item_list) else f"item#{result_item}"
            out = [f"Recipe #{recipe_id}: {rname} x{result_qty}"]
            for i in range(4):
                mat_id = fb_field(data, vt, to, 4 + i * 2, '<I') or 0
                mat_qty = fb_field(data, vt, to, 5 + i * 2, '<I') or 0
                if mat_id:
                    mname = item_list[mat_id] if mat_id < len(item_list) else f"item#{mat_id}"
                    out.append(f"  {mname} x{mat_qty}")
            return "\n".join(out)

        # Battle drops: 5 pairs of (rate, item_index)
        if path == self.BATTLE_DROPS_PATH:
            out = [f"Battle Drops #{file_idx}"]
            for i in range(5):
                rate = fb_field(data, vt, to, i * 2, '<i') or 0
                item_id = fb_field(data, vt, to, i * 2 + 1, '<i') or 0
                if rate and item_id:
                    iname = item_list[item_id] if item_id < len(item_list) else f"item#{item_id}"
                    out.append(f"  {iname} ({rate}%)")
            return "\n".join(out)

        # Field drops: hash, regular item+prob, rare item+prob
        drop_hash = fb_field(data, vt, to, 0, '<Q') or 0
        reg_item = fb_field(data, vt, to, 1, '<i') or 0
        reg_prob = fb_field(data, vt, to, 2, '<i') or 0
        rare_item = fb_field(data, vt, to, 3, '<i') or 0
        rare_prob = fb_field(data, vt, to, 4, '<i') or 0

        out = [f"Drops [{drop_hash:#x}]"]
        if reg_item:
            rname = item_list[reg_item] if reg_item < len(item_list) else f"item#{reg_item}"
            out.append(f"  Regular: {rname} ({reg_prob}%)")
        if rare_item:
            rname = item_list[rare_item] if rare_item < len(item_list) else f"item#{rare_item}"
            out.append(f"  Rare: {rname} ({rare_prob}%)")
        return "\n".join(out)

    # ── decode_outbreak ───────────────────────────────────────────

    def decode_outbreak(self, data, file_idx, text_tables, path='', **kwargs):
        """Decode PLA mass outbreak data (NewHugeOutbreakLottery FlatBuffer).
        Lottery-based spawning with outbreak chances and thresholds."""
        if len(data) < 12:
            return None

        root = fb_root(data)
        vt, to = fb_table(data, root)

        ob_hash = fb_field(data, vt, to, 0, '<Q') or 0
        group_off = fb_field(data, vt, to, 1, '<H')
        group_str = fb_string(data, to, group_off) if group_off else "?"
        chance = fb_field(data, vt, to, 2, '<i') or 0
        total_min = fb_field(data, vt, to, 3, '<i') or 0
        total_max = fb_field(data, vt, to, 4, '<i') or 0
        rare2 = fb_field(data, vt, to, 5, '<i') or 0
        rare1 = fb_field(data, vt, to, 6, '<i') or 0
        first_min = fb_field(data, vt, to, 7, '<i') or 0
        first_max = fb_field(data, vt, to, 8, '<i') or 0
        second_min = fb_field(data, vt, to, 9, '<i') or 0
        second_max = fb_field(data, vt, to, 10, '<i') or 0

        out = [f"Outbreak: {group_str} [{ob_hash:#x}]"]
        out.append(f"Chance: {chance}% | Total: {total_min}-{total_max}")
        out.append(f"Wave 1: {first_min}-{first_max} | Wave 2: {second_min}-{second_max}")
        if rare1 or rare2:
            out.append(f"Rare rates: tier1={rare1}% tier2={rare2}%")

        return "\n".join(out)

    # ── decode_research ───────────────────────────────────────────

    def decode_research(self, data, file_idx, text_tables, path='', **kwargs):
        """Decode PLA Pokedex research tasks (PokedexResearchTask FlatBuffer).
        Subset: wld (throw/catch mechanics) dispatched by path."""
        if len(data) < 12:
            return None

        # Throw/catch subset (decode_wld)
        if path == self.THROW_PARAM_PATH or path == self.THROWABLE_PARAM_PATH:
            return self._decode_wld(data, file_idx, text_tables)

        species_list = text_tables.get('species', [])
        moves_list = text_tables.get('moves', [])
        root = fb_root(data)
        vt, to = fb_table(data, root)

        TASK_TYPES = ('Unknown', 'Use Move', 'Defeat')

        species = fb_field(data, vt, to, 0, '<i') or 0
        task_type = fb_field(data, vt, to, 1, '<i') or 0
        threshold = fb_field(data, vt, to, 2, '<i') or 0
        move = fb_field(data, vt, to, 3, '<i') or 0
        move_type = fb_field(data, vt, to, 4, '<i') or 0
        time_of_day = fb_field(data, vt, to, 5, '<i') or 0
        points_single = fb_field(data, vt, to, 13, '<i') or 0
        points_bonus = fb_field(data, vt, to, 14, '<i') or 0
        required = fb_field(data, vt, to, 15, '<B') or 0

        sp_name = species_list[species] if species < len(species_list) else f"#{species}"
        type_str = TASK_TYPES[task_type] if task_type < len(TASK_TYPES) else f'type#{task_type}'

        out = [f"Research: {sp_name} — {type_str}"]
        if move:
            mname = moves_list[move] if move < len(moves_list) else f"move#{move}"
            out.append(f"Move: {mname}")
        if threshold:
            out.append(f"Threshold: {threshold}")

        # Progressive thresholds (fields 9-13)
        thresholds = []
        for i in range(5):
            t = fb_field(data, vt, to, 9 + i, '<i') or 0
            if t: thresholds.append(str(t))
        if thresholds:
            out.append(f"Milestones: {'/'.join(thresholds)}")

        out.append(f"Points: {points_single}" + (f" (bonus: {points_bonus})" if points_bonus else ""))
        if required:
            out.append("Required for completion")

        return "\n".join(out)

    def _decode_wld(self, data, file_idx, text_tables):
        """Decode PLA throw/catch parameters (ThrowParam / ThrowableParam)."""
        root = fb_root(data)
        vt, to = fb_table(data, root)

        # Try ThrowableParam first (has ItemID at field 0 as int)
        item_id = fb_field(data, vt, to, 0, '<i') or 0
        field_05 = fb_field(data, vt, to, 5, '<i')

        if field_05 is not None:
            # ThrowableParam format
            item_list = text_tables.get('items', [])
            iname = item_list[item_id] if item_id < len(item_list) else f"item#{item_id}"
            return f"Throwable: {iname} (#{item_id})"

        # ThrowParam format (velocity, arc, gravity, angle)
        velocity = fb_field(data, vt, to, 1, '<f') or 0.0
        arc = fb_field(data, vt, to, 2, '<f') or 0.0
        gravity = fb_field(data, vt, to, 3, '<f') or 0.0
        angle = fb_field(data, vt, to, 4, '<f') or 0.0

        return f"Throw params | Velocity: {velocity:.1f} | Arc: {arc:.1f} | Gravity: {gravity:.1f} | Angle: {angle:.1f}"

    # ── decode_moveshop ───────────────────────────────────────────

    def decode_moveshop(self, data, file_idx, text_tables):
        """Decode PLA move tutor shop (MoveShopIndex FlatBuffer)."""
        if len(data) < 12:
            return None

        moves_list = text_tables.get('moves', [])
        root = fb_root(data)
        vt, to = fb_table(data, root)

        index = fb_field(data, vt, to, 0, '<i') or 0
        move_id = fb_field(data, vt, to, 1, '<i') or 0
        price = fb_field(data, vt, to, 2, '<i') or 0

        move_name = moves_list[move_id] if move_id < len(moves_list) else f"move#{move_id}"
        return f"Shop #{index}: {move_name} (#{move_id}) — {price} pts"

    FLIPNOTE_PAIRS = {
        'Pokemon Legends Arceus': ['PLA'],
    }

"""Kanto_lgpe.py: Let's Go, Pikachu! / Let's Go, Eevee!

Inherits Alola_usum. First Switch mainline title. Simplified mechanics,
no wild battles (Go-style catching), no held items, no abilities in battle.
Kanto region revisit with Mega Evolution carried forward from Gen VI.
"""
from Generations.Alola_usum import Alola_usum

import struct
from leswitch import fb_field, fb_root, fb_table, fb_vector


class Kanto_lgpe(Alola_usum):
    """Let's Go, Pikachu! / Let's Go, Eevee!"""

    GAME_CODES = ('GP1', 'GE1')
    TITLES = ("POKÉMON: LET'S GO, PIKACHU!", "POKÉMON: LET'S GO, EEVEE!")
    YEAR = 2018

    PLATFORM = 'Nintendo Switch'
    GEN = 7
    CONTAINER = 'switch'

    SPECIES_COUNT = 809

    # -- Paths (from pkNX GameFileMapping.cs) --
    PERSONAL_PATH = 'bin/pokelib/personal'
    LEARNSET_PATH = 'bin/archive/waza_oboe.gfpak'
    EVOLUTION_PATH = 'bin/pokelib/evolution'
    MEGA_EVO_PATH = 'bin/pokelib/mega_evolution'
    MOVE_DATA_PATH = 'bin/pokelib/waza/waza_data.bin'
    ITEM_PATH = 'bin/pokelib/item'
    TEXT_PATH = 'bin/message/English/common'
    TEXT_SCRIPT_PATH = 'bin/message/English/script'
    TRDATA_PATH = 'bin/trainer/trainer_data'
    TRPOKE_PATH = 'bin/trainer/trainer_poke'
    TRAINER_CLASS_PATH = 'bin/trainer/trainer_type'

    # -- Encounters --
    WILD_PATH_P = 'bin/field/param/encount/encount_data_p.bin'
    WILD_PATH_E = 'bin/field/param/encount/encount_data_e.bin'
    STATIC_ENCOUNTER_PATH = 'bin/script_event_data/event_encount.bin'
    GIFT_PATH = 'bin/script_event_data/add_poke.bin'
    TRADE_PATH = 'bin/script_event_data/field_trade_data.bin'

    # -- Misc --
    SHOP_PATH = 'bin/app/shop/shop_data.bin'

    # -- Container format --
    LEARNSET_CONTAINER = 'gfpak'
    MOVE_DATA_CONTAINER = 'binlinker'


    def get_narc(self, narc_path):
        key = narc_path
        if key not in self._narc_cache:
            # Switch: list files under path, wrap as narc-like object
            matches = sorted(f for f in self.switch_rom.files if f.startswith(narc_path.rstrip('/') + '/') or f == narc_path)
            class _SwitchNarc:
                pass
            obj = _SwitchNarc()
            obj.files = [self.switch_rom.read_file(m) for m in matches]
            self._narc_cache[key] = obj
        return self._narc_cache[key]

    def read_file(self, path):
        p = path.strip('/')
        if ':' in p:
            folder, fi = p.rsplit(':', 1)
            fi = int(fi)
            narc = self.get_narc(folder.lstrip('/'))
            if fi >= len(narc.files):
                raise ValueError(f"Index {fi} out of range ({len(narc.files)} files)")
            return narc.files[fi]
        return self.switch_rom.read_file(p)

    def decode_personal(self, data, file_idx, text_tables, tm_table=None):
        """Decode LGPE personal data (0x54 bytes). Same as SM with GoSpecies, reduced TMs."""
        if len(data) < 0x54 or data == b'\x00' * len(data):
            return None
        # Base fields identical to SM — call parent
        result = super().decode_personal(data, file_idx, text_tables, tm_table)
        if result is None:
            return None

        # LGPE-specific: GO species mapping at 0x48
        go_species = struct.unpack_from('<H', data, 0x48)[0]
        if go_species > 0:
            species_list = text_tables.get('species', [])
            go_name = species_list[go_species] if go_species < len(species_list) else f"#{go_species}"
            result += f"\nGO Species: {go_name} (#{go_species})"

        return result

    # ── decode_trdata ──────────────────────────────────────────────
    def decode_trdata(self, data, file_idx, text_tables):
        """Decode LGPE trainer header (0x17 bytes)."""
        if len(data) < 0x17:
            return None

        tr_class = struct.unpack_from('<H', data, 0x00)[0]
        battle_mode = data[0x02]
        num_pokemon = data[0x03]
        items = [struct.unpack_from('<H', data, 0x04 + i*2)[0] for i in range(4)]
        ai = struct.unpack_from('<I', data, 0x0C)[0]
        heal = bool(data[0x10])
        money = data[0x11]
        gift = struct.unpack_from('<H', data, 0x14)[0]
        gift_qty = data[0x16]

        class_list = text_tables.get('trainer_classes', [])
        name_list = text_tables.get('trainer_names', [])
        item_list = text_tables.get('items', [])

        class_name = class_list[tr_class] if tr_class < len(class_list) else f'class#{tr_class}'
        tr_name = name_list[file_idx] if file_idx < len(name_list) else f'#{file_idx}'

        out = [f'{class_name} {tr_name} (#{file_idx})']
        out.append(f'Pokemon: {num_pokemon} | Money: x{money}')
        mode_str = {0: 'Single', 1: 'Double', 2: 'Triple', 3: 'Rotation'}.get(battle_mode, f'mode#{battle_mode}')
        out.append(f'Battle: {mode_str}')

        held = [item_list[i] if i and i < len(item_list) else None for i in items]
        held = [h for h in held if h]
        if held:
            out.append(f'Items: {" / ".join(held)}')

        ai_flags = self.decode_ai_flags(ai)
        if ai_flags:
            out.append(f'AI: {ai_flags}')
        if heal:
            out.append('Heals between battles')
        if gift:
            gname = item_list[gift] if gift < len(item_list) else f'item#{gift}'
            out.append(f'Gift: {gname} x{gift_qty}')

        return '\n'.join(out)

    # ── decode_trpoke ──────────────────────────────────────────────
    def decode_trpoke(self, data, file_idx, text_tables):
        """Decode LGPE trainer pokemon (0x28 bytes per entry).
        LGPE-unique: Awakening Values, friendship stat scaling, mega choice."""
        if len(data) < 0x28:
            return None

        species_list = text_tables.get('species', [])
        moves_list = text_tables.get('moves', [])
        item_list = text_tables.get('items', [])
        nature_list = text_tables.get('natures', [])

        entry_size = 0x28
        count = len(data) // entry_size
        out = []

        for i in range(count):
            e = i * entry_size
            gender = data[e] & 0x3
            ability_slot = (data[e] >> 4) & 0x3
            nature = data[e + 1]
            evs = [data[e + 2 + j] for j in range(6)]  # HP/ATK/DEF/SPA/SPD/SPE
            avs = [data[e + 8 + j] for j in range(6)]  # HP/ATK/DEF/SPA/SPD/SPE
            friendship = data[e + 0x0E]
            rank = data[e + 0x0F]

            iv32 = struct.unpack_from('<I', data, e + 0x10)[0]
            ivs = [(iv32 >> (j*5)) & 0x1F for j in range(6)]  # HP/ATK/DEF/SPE/SPA/SPD
            shiny = bool((iv32 >> 30) & 1)
            can_mega = bool((iv32 >> 31) & 1)

            mega_form = struct.unpack_from('<H', data, e + 0x14)[0]
            level = struct.unpack_from('<H', data, e + 0x16)[0]
            species = struct.unpack_from('<H', data, e + 0x18)[0]
            form = struct.unpack_from('<H', data, e + 0x1A)[0]
            held_item = struct.unpack_from('<H', data, e + 0x1C)[0]
            moves = [struct.unpack_from('<H', data, e + 0x20 + j*2)[0] for j in range(4)]

            sp_name = species_list[species] if species < len(species_list) else f'#{species}'
            form_str = f'-{form}' if form else ''
            gender_str = {0: '', 1: ' (M)', 2: ' (F)'}.get(gender, '')
            item_str = ''
            if held_item:
                iname = item_list[held_item] if held_item < len(item_list) else f'item#{held_item}'
                item_str = f' @ {iname}'

            out.append(f'{i+1}. {sp_name}{form_str}{gender_str} Lv{level}{item_str}')

            move_names = []
            for mid in moves:
                if mid:
                    move_names.append(moves_list[mid] if mid < len(moves_list) else f'move#{mid}')
            if move_names:
                out.append(f'   Moves: {" / ".join(move_names)}')

            nat_name = nature_list[nature] if nature < len(nature_list) else f'nature#{nature}'
            out.append(f'   Nature: {nat_name} | Ability: slot {ability_slot}')

            # IVs
            stat_names = ['HP', 'Atk', 'Def', 'Spe', 'SpA', 'SpD']
            iv_str = ' / '.join(f'{stat_names[j]}:{ivs[j]}' for j in range(6))
            out.append(f'   IVs: {iv_str}')

            # EVs (if any non-zero)
            ev_names = ['HP', 'Atk', 'Def', 'SpA', 'SpD', 'Spe']
            ev_parts = [f'{ev_names[j]}:{evs[j]}' for j in range(6) if evs[j]]
            if ev_parts:
                out.append(f'   EVs: {" / ".join(ev_parts)}')

            # AVs (LGPE unique — if any non-zero)
            av_parts = [f'{ev_names[j]}:{avs[j]}' for j in range(6) if avs[j]]
            if av_parts:
                out.append(f'   AVs: {" / ".join(av_parts)}')

            if friendship:
                bonus = int(((friendship / 255.0 / 10.0) + 1.0) * 100)
                out.append(f'   Friendship: {friendship} (stat scale: {bonus}%)')

            extras = []
            if shiny: extras.append('Shiny')
            if can_mega: extras.append(f'Mega (form {mega_form})')
            if rank: extras.append(f'Rank {rank}')
            if extras:
                out.append(f'   {" / ".join(extras)}')

        return '\n'.join(out)


    # ── decode_encounters ──────────────────────────────────────────
    def decode_encounters(self, data, file_idx, text_tables, path='', **kwargs):
        """Decode LGPE encounters. Path routes to sub-type:
        encount_data → wild (FlatBuffer), event_encount → static (0x40),
        add_poke → gift (0x20), field_trade → trade (0x58)."""
        if not data or len(data) < 4:
            return None

        species_list = text_tables.get('species', [])
        moves_list = text_tables.get('moves', [])
        nature_list = text_tables.get('natures', [])
        item_list = text_tables.get('items', [])

        def sp(idx):
            return species_list[idx] if idx < len(species_list) else f'#{idx}'
        def mv(idx):
            return moves_list[idx] if idx and idx < len(moves_list) else None
        def nat(idx):
            return nature_list[idx] if idx < 25 and idx < len(nature_list) else 'Random'

        if 'encount_data' in path:
            return self._decode_wild_lgpe(data, species_list)
        elif 'event_encount' in path:
            return self._decode_static_lgpe(data, sp, mv, nat)
        elif 'add_poke' in path:
            return self._decode_gift_lgpe(data, sp, mv, nat)
        elif 'field_trade' in path:
            return self._decode_trade_lgpe(data, sp, nat, item_list)
        return None

    def _decode_wild_lgpe(self, data, species_list):
        """Wild encounters — FlatBuffer EncounterArchive."""
        import struct as st

        root = fb_root(data)
        vt, to = fb_table(data, root)
        zones_off = fb_field(data, vt, to, 0, '<i')
        if not zones_off:
            return 'No encounter zones'

        vec_off = to + zones_off
        count = st.unpack_from('<i', data, vec_off)[0]
        out = [f'LGPE Wild Encounters ({count} zones)']

        TABLES = [
            ('Ground', 6, 7, 8, 10),
            ('Water', 14, 15, 16, 18),
            ('Old Rod', 19, 20, 21, 23),
            ('Good Rod', 24, 25, 26, 28),
            ('Super Rod', 29, 30, 31, 33),
            ('Sky', 37, 38, 39, 41),
        ]

        for i in range(count):
            zone_ref = st.unpack_from('<i', data, vec_off + 4 + i*4)[0]
            zone_abs = vec_off + 4 + i*4 + zone_ref
            zvt, zto = fb_table(data, zone_abs)

            zone_id = fb_field(data, zvt, zto, 0, '<Q') or 0
            out.append(f'\n--- Zone {i} (ID: {zone_id:#x}) ---')

            for name, rate_f, lmin_f, lmax_f, slots_f in TABLES:
                rate = fb_field(data, zvt, zto, rate_f, '<i')
                if not rate:
                    continue
                lmin = fb_field(data, zvt, zto, lmin_f, '<i') or 0
                lmax = fb_field(data, zvt, zto, lmax_f, '<i') or 0
                out.append(f'  {name} (rate {rate}, Lv{lmin}-{lmax}):')

                slots_off = fb_field(data, zvt, zto, slots_f, '<i')
                if not slots_off:
                    continue
                svec = zto + slots_off
                scount = st.unpack_from('<i', data, svec)[0]
                for s in range(scount):
                    sref = st.unpack_from('<i', data, svec + 4 + s*4)[0]
                    sabs = svec + 4 + s*4 + sref
                    svt2, sto2 = fb_table(data, sabs)
                    prob = fb_field(data, svt2, sto2, 0, '<i') or 0
                    sp_id = fb_field(data, svt2, sto2, 1, '<i') or 0
                    form = fb_field(data, svt2, sto2, 2, '<h') or 0
                    if sp_id == 0:
                        continue
                    sp_name = species_list[sp_id] if sp_id < len(species_list) else f'#{sp_id}'
                    form_str = f'-{form}' if form else ''
                    out.append(f'    {sp_name}{form_str} ({prob}%)')

        return '\n'.join(out)

    def _decode_static_lgpe(self, data, sp, mv, nat):
        """Static encounters — 0x40 bytes each."""
        import struct as st
        SIZE = 0x40
        count = len(data) // SIZE
        out = [f'LGPE Static Encounters ({count})']
        GENDER = {0: '', 1: ' (M)', 2: ' (F)', 3: ''}
        SHINY = {0: '', 1: ' [Shiny]', 2: ' [Never Shiny]'}

        for i in range(count):
            e = i * SIZE
            species = st.unpack_from('<H', data, e + 0x08)[0]
            form = data[e + 0x0A]
            level = data[e + 0x0B]
            shiny = data[e + 0x0C] & 3
            gender = data[e + 0x0D] & 3
            nature = data[e + 0x0E]
            ability = data[e + 0x0F]
            moves = [st.unpack_from('<H', data, e + 0x20 + j*2)[0] for j in range(4)]
            ivs = [data[e + 0x2C + j] for j in range(6)]

            form_str = f'-{form}' if form else ''
            line = f'{i}: {sp(species)}{form_str} Lv{level}{GENDER.get(gender,"")}{SHINY.get(shiny,"")}'
            out.append(line)

            move_names = [mv(m) for m in moves if mv(m)]
            if move_names:
                out.append(f'   Moves: {" / ".join(move_names)}')
            out.append(f'   Nature: {nat(nature)} | Ability: slot {ability}')

            stat_names = ['HP','Atk','Def','SpA','SpD','Spe']
            fixed = [(stat_names[j], ivs[j]) for j in range(6) if ivs[j] != 0xFF]
            if fixed:
                out.append(f'   IVs: {", ".join(f"{n}:{v}" for n, v in fixed)}')

        return '\n'.join(out)

    def _decode_gift_lgpe(self, data, sp, mv, nat):
        """Gift pokemon — 0x20 bytes each."""
        import struct as st
        SIZE = 0x20
        count = len(data) // SIZE
        out = [f'LGPE Gift Pokemon ({count})']
        GENDER = {0: '', 1: ' (M)', 2: ' (F)', 3: ''}

        for i in range(count):
            e = i * SIZE
            species = st.unpack_from('<H', data, e + 0x08)[0]
            form = data[e + 0x0A]
            level = data[e + 0x0B]
            shiny = data[e + 0x0C] & 3
            gender = data[e + 0x0D] & 3
            nature = data[e + 0x0E]
            ability = data[e + 0x0F]
            special_move = st.unpack_from('<H', data, e + 0x10)[0]
            ivs = [data[e + 0x12 + j] for j in range(6)]
            avs = [data[e + 0x18 + j] for j in range(6)]

            form_str = f'-{form}' if form else ''
            out.append(f'{i}: {sp(species)}{form_str} Lv{level}{GENDER.get(gender,"")}')
            out.append(f'   Nature: {nat(nature)} | Ability: slot {ability}')

            if special_move:
                mname = mv(special_move) or f'move#{special_move}'
                out.append(f'   Special Move: {mname}')

            stat_names = ['HP','Atk','Def','SpA','SpD','Spe']
            fixed = [(stat_names[j], ivs[j]) for j in range(6) if ivs[j] != 0xFF]
            if fixed:
                out.append(f'   IVs: {", ".join(f"{n}:{v}" for n, v in fixed)}')
            av_parts = [(stat_names[j], avs[j]) for j in range(6) if avs[j]]
            if av_parts:
                out.append(f'   AVs: {", ".join(f"{n}:{v}" for n, v in av_parts)}')

        return '\n'.join(out)

    def _decode_trade_lgpe(self, data, sp, nat, item_list):
        """In-game trades — 0x58 bytes each."""
        import struct as st
        SIZE = 0x58
        count = len(data) // SIZE
        out = [f'LGPE In-Game Trades ({count})']

        for i in range(count):
            e = i * SIZE
            species = st.unpack_from('<H', data, e + 0x08)[0]
            form = data[e + 0x0A]
            level = data[e + 0x0C]

            ivs = []
            for j in range(6):
                v = st.unpack_from('<h', data, e + 0x0E + j*2)[0]
                ivs.append(v if v >= 0 else -1)

            avs = []
            for j in range(6):
                v = st.unpack_from('<h', data, e + 0x1A + j*2)[0]
                avs.append(v & 0xFF if v >= 0 else -1)

            gender_raw = st.unpack_from('<h', data, e + 0x26)[0]
            gender = {-1: '', 0: ' (M)', 1: ' (F)'}.get(gender_raw, '')
            nature_raw = st.unpack_from('<h', data, e + 0x28)[0]
            nature_str = nat(nature_raw) if nature_raw >= 0 else 'Random'

            req_species = st.unpack_from('<H', data, e + 0x42)[0]
            req_form = st.unpack_from('<H', data, e + 0x44)[0]

            form_str = f'-{form}' if form else ''
            out.append(f'{i}: {sp(species)}{form_str} Lv{level}{gender}')
            out.append(f'   Nature: {nature_str}')

            req_form_str = f'-{req_form}' if req_form else ''
            out.append(f'   Wants: {sp(req_species)}{req_form_str}')

            stat_names = ['HP','Atk','Def','SpA','SpD','Spe']
            fixed = [(stat_names[j], ivs[j]) for j in range(6) if ivs[j] >= 0]
            if fixed:
                out.append(f'   IVs: {", ".join(f"{n}:{v}" for n, v in fixed)}')

        return '\n'.join(out)



    # ── decode_shop ────────────────────────────────────────────────
    def decode_shop(self, data, file_idx, text_tables, path='', **kwargs):
        """Decode LGPE shop inventory (FlatBuffer). Single + Multi shops."""
        import struct as st

        if len(data) < 8:
            return None

        item_list = text_tables.get('items', [])
        def iname(idx):
            return item_list[idx] if idx < len(item_list) else f'item#{idx}'

        root = fb_root(data)
        vt, to = fb_table(data, root)
        out = ['LGPE Shop Inventory']

        # Field 0: Single shops vector
        single_off = fb_field(data, vt, to, 0, '<i')
        if single_off:
            svec = to + single_off
            scount = st.unpack_from('<i', data, svec)[0]
            out.append(f'\nSingle Shops ({scount}):')
            for i in range(scount):
                ref = st.unpack_from('<i', data, svec + 4 + i*4)[0]
                sabs = svec + 4 + i*4 + ref
                svt2, sto2 = fb_table(data, sabs)
                shop_hash = fb_field(data, svt2, sto2, 0, '<Q') or 0
                # Field 1: Inventory table
                inv_off = fb_field(data, svt2, sto2, 1, '<i')
                items = []
                if inv_off:
                    iabs = sto2 + inv_off
                    ivt, ito = fb_table(data, iabs)
                    items_off = fb_field(data, ivt, ito, 0, '<i')
                    if items_off:
                        ivec = ito + items_off
                        ic = st.unpack_from('<i', data, ivec)[0]
                        items = [st.unpack_from('<i', data, ivec + 4 + j*4)[0] for j in range(ic)]
                out.append(f'  Shop {shop_hash:#x}: {", ".join(iname(x) for x in items)}')

        # Field 1: Multi shops vector
        multi_off = fb_field(data, vt, to, 1, '<i')
        if multi_off:
            mvec = to + multi_off
            mcount = st.unpack_from('<i', data, mvec)[0]
            out.append(f'\nMulti Shops ({mcount}):')
            for i in range(mcount):
                ref = st.unpack_from('<i', data, mvec + 4 + i*4)[0]
                mabs = mvec + 4 + i*4 + ref
                mvt2, mto2 = fb_table(data, mabs)
                shop_hash = fb_field(data, mvt2, mto2, 0, '<Q') or 0
                out.append(f'  Shop {shop_hash:#x}:')
                # Field 1: vector of Inventory tables
                invs_off = fb_field(data, mvt2, mto2, 1, '<i')
                if invs_off:
                    invvec = mto2 + invs_off
                    inv_count = st.unpack_from('<i', data, invvec)[0]
                    for k in range(inv_count):
                        iref = st.unpack_from('<i', data, invvec + 4 + k*4)[0]
                        iabs = invvec + 4 + k*4 + iref
                        ivt, ito = fb_table(data, iabs)
                        items_off = fb_field(data, ivt, ito, 0, '<i')
                        items = []
                        if items_off:
                            itemvec = ito + items_off
                            ic = st.unpack_from('<i', data, itemvec)[0]
                            items = [st.unpack_from('<i', data, itemvec + 4 + j*4)[0] for j in range(ic)]
                        out.append(f'    Inv {k}: {", ".join(iname(x) for x in items)}')

        return '\n'.join(out)


    FLIPNOTE_PAIRS = {
        "Pokemon Let's Go": ['GP1', 'GE1'],
    }

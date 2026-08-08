"""Unova_sequel.py: Black 2/White 2.

Inherits Unova_prequel. Different trdata/trpoke/encounter paths,
adds PWT (Pokemon World Tournament), Challenge Mode trainer delta.
"""
from Generations.Unova_prequel import Unova_prequel


class Unova_sequel(Unova_prequel):
    """Black 2/White 2. Inherits Black/White, adds PWT."""

    GAME_CODES = ('IRE', 'IRD')
    TITLES = ('POKÉMON BLACK 2', 'POKÉMON WHITE 2')
    YEAR = 2012

    TRDATA_PATH = 'a/0/9/1'
    TRPOKE_PATH = 'a/0/9/2'
    ENCOUNTER_PATH = 'a/1/2/7'

    PWT_POKEMON_PATH = 'a/2/5/0'
    PWT_TRAINERS_PATH = 'a/2/5/1'
    PWT_ROSTERS_PATH = 'a/2/5/2'
    PWT_POKEMON_B_PATH = 'a/2/5/3'
    PWT_TRAINERS_B_PATH = 'a/2/5/4'
    PWT_ROSTERS_B_PATH = 'a/2/5/5'
    PWT_CHAMPIONS_PATH = 'a/2/5/6'
    PWT_CHAMPIONS_B_PATH = 'a/2/5/7'
    PWT_DOWNLOAD_PATH = 'a/2/5/8'

    SUBWAY_POKEMON_PATH = 'a/2/4/0'
    SUBWAY_TRAINERS_PATH = 'a/2/4/1'

    TEXT_SPECIES = 90
    TEXT_MOVES = 403
    TEXT_ITEMS = 64
    TEXT_ABILITIES = 374
    TEXT_NATURES = 380
    TEXT_TRAINER_NAMES = 382
    TEXT_TRAINER_CLASSES = 383
    TEXT_TYPE_NAMES = 398
    TEXT_LOCATIONS = 109

    # BW2 Challenge Mode runtime level delta table.
    # Verified by measuring stored trpoke levels vs actual in-game levels.
    # The game applies a flat per-trainer-file delta at runtime on top of stored levels.
    # Pattern: +1 per pair of gyms, capped at +4 from gym 7 onward (E4, Champion included).
    # Keyed by trdata/trpoke file index -> challenge delta.
    # Normal mode files and unkeyed files get delta 0.
    CHALLENGE_FILE_DELTA = {
        # Gym 1 - Cheren (Aspertia)
        764: 1,
        # Gym 2 - Roxie (Virbank)
        765: 1,
        # Gym 3 - Burgh (Castelia)
        766: 2,
        # Gym 4 - Elesa (Nimbasa)
        767: 2,
        # Gym 5 - Clay (Driftveil)
        768: 3,
        # Gym 6 - Skyla (Mistralton)
        769: 3,
        # Gym 7 - Drayden (Opelucid)
        770: 4,
        # Gym 8 - Marlon (Humilau)
        771: 4,
        # Elite Four - Shauntal, Caitlin, Grimsley, Marshal (pre-champion)
        772: 4, 773: 4, 774: 4, 775: 4,
        # Champion Iris (pre-champion)
        776: 4,
        # Elite Four rematches (post-game)
        777: 4, 778: 4, 779: 4, 780: 4,
        # Champion Iris rematch
        781: 4,
    }

    @staticmethod
    def get_bw2_challenge_delta(file_idx: int, game_code: str = '') -> int:
        """Get runtime challenge level delta for a BW2 trainer file.
        Returns 0 for Normal mode files, non-BW2 games, or unkeyed files.
        """
        if game_code not in ('IRE', 'IRD'):
            return 0
        return Unova_sequel.CHALLENGE_FILE_DELTA.get(file_idx, 0)

    @staticmethod
    def _role_path(role_name, narc_roles: dict):
        """Look up a NARC path by its role name, using the narc_roles reverse map."""
        for path, role in narc_roles.items():
            if role == role_name:
                return path
        return None


    @staticmethod
    def _resolve_pwt_trainer_name(trainer_idx, text_tables: dict, trainer_role="pwt_trainers"):
        """Resolve a PWT trainer index to a name via the trainer mapping table (a/2/4/0).
        Entry stride: 20 bytes (10 u16s). Class IDs live at different positions per group:
          - Group 1 (Kanto/Johto): u16[8] has the class ID
          - Groups 2-5 (Hoenn/Sinnoh/Unova/Champions): u16[5], u16[6], u16[7] have class IDs
        Check all candidate positions, return the first that resolves to a real leader name."""
        _JUNK = {'Pokmon Trainer', 'Boss Trainer', 'no data', 'Pokmon Trainer',
                 'Team Plasma', 'GAME FREAK', 'Leader', ''}
        classes = text_tables.get('trainer_classes', [])
        try:
            map_path = Unova_sequel._role_path('pwt_trainer_map')
            if not map_path:
                return None
            map_narc = _get_narc(map_path)
            if not map_narc.files:
                return None
            data = bytes(map_narc.files[0])
            stride = 20
            entry_off = trainer_idx * stride
            if entry_off + stride > len(data):
                return None
            # Only u16[8] has trainer identity; u16[5-7] are generic tournament classes
            for pos in (8,):
                cid = struct.unpack_from('<H', data, entry_off + pos * 2)[0]
                if cid == 0 or cid >= len(classes):
                    continue
                raw = classes[cid]
                if isinstance(raw, str):
                    clean = re.sub(r'[^\x20-\x7E]', '', raw).strip()
                    if clean and clean not in _JUNK:
                        return clean
        except:
            pass
        return None


    # Globals built at ROM open for B2W2 PWT dowse support
    pwt_name_to_entries = {}    # name (lowercase) -> [trainers_b file indices]
    pwt_entry_tournaments = {}  # trainers_b index -> [tournament name strings]

    @staticmethod
    def _build_pwt_maps(text_tables: dict):
        """Build PWT reverse indexes from ROM data. No hardcoding.
        1) pwt_entry_tournaments: read pwt_defs, extract trainers_b indices per tournament.
        2) pwt_name_to_entries: for each trainers_b entry, auto-resolve name via trainer_map class IDs.
        3) Unresolved entries: cross-reference PWT pool types vs in-game Leader team types.
        Called once at ROM open for B2W2."""
        global pwt_name_to_entries, pwt_entry_tournaments
        pwt_name_to_entries = {}
        pwt_entry_tournaments = {}
        try:
            # Step 1: pwt_defs -> trainers_b indices per tournament
            defs_path = Unova_sequel._role_path('pwt_defs')
            if not defs_path:
                return
            defs_narc = _get_narc(defs_path)
            for fi, raw in enumerate(defs_narc.files):
                data = bytes(raw)
                if len(data) < 0x1A8:
                    continue
                tid = struct.unpack_from('<H', data, 0)[0]
                tname = Unova_sequel._resolve_pwt_text(tid) or f"Tournament #{tid}"
                if isinstance(tname, str):
                    tname = re.sub(r'[^\x20-\x7E]', '', tname).strip()
                indices = set()
                for rs, re_ in [(0xA0, 0x130), (0x160, 0x1A8)]:
                    for off in range(rs, re_, 2):
                        val = struct.unpack_from('<H', data, off)[0]
                        if 1 <= val <= 68:
                            indices.add(val)
                for idx in indices:
                    pwt_entry_tournaments.setdefault(idx, []).append(tname)

            # Step 2: auto-resolve names via trainer_map class IDs (works for external leaders)
            tb_path = Unova_sequel._role_path('pwt_trainers_b')
            if not tb_path:
                return
            tb_count = len(_get_narc(tb_path).files)
            unresolved = []
            for idx in range(1, tb_count):
                name = Unova_sequel._resolve_pwt_trainer_name(idx)
                if name:
                    pwt_name_to_entries.setdefault(name.lower(), []).append(idx)
                else:
                    unresolved.append(idx)

            # Step 3: resolve BW2 gym leaders by name + type cross-reference
            # The text table has "Leader" at MULTIPLE class IDs (112-119, one per gym leader).
            # Collect ALL of them so every leader is found.
            if not unresolved:
                return
            tc = text_tables.get('trainer_classes', [])
            leader_cids = set()
            for ci, cn in enumerate(tc):
                if isinstance(cn, str) and cn.strip() == 'Leader':
                    leader_cids.add(ci)
            if not leader_cids:
                return
            trdata_path = Unova_sequel._role_path('trdata')
            trpoke_path = Unova_sequel._role_path('trpoke')
            personal_path = Unova_sequel._role_path('personal')
            if not (trdata_path and trpoke_path and personal_path):
                return
            td_narc = _get_narc(trdata_path)
            tp_narc = _get_narc(trpoke_path)
            ps_narc = _get_narc(personal_path)
            tnames = text_tables.get('trainer_names', [])
            PSIZES = {0: 8, 1: 16, 2: 10, 3: 18}

            def _get_type(sid):
                """Return (type1, type2) for a species from personal data."""
                if sid >= len(ps_narc.files):
                    return None
                pers = bytes(ps_narc.files[sid])
                if len(pers) < 8:
                    return None
                return pers[6], pers[7]

            def _dominant_type(species_ids):
                """Find the most common type across a list of species."""
                counts = {}
                for sid in species_ids:
                    tp = _get_type(sid)
                    if tp is None:
                        continue
                    t1, t2 = tp
                    counts[t1] = counts.get(t1, 0) + 1
                    if t2 != t1:
                        counts[t2] = counts.get(t2, 0) + 1
                return max(counts, key=counts.get) if counts else None

            # Scan Normal-mode trdata for Leaders (any of the leader class IDs)
            leader_profiles = []  # [(name, type_specialty), ...]
            seen_names = set()
            normal_limit = min(764, len(td_narc.files))
            for ti in range(normal_limit):
                td = bytes(td_narc.files[ti])
                if len(td) < 16 or td[1] not in leader_cids:
                    continue
                nm = tnames[ti] if ti < len(tnames) else None
                if not isinstance(nm, str):
                    continue
                nm = re.sub(r'[^\x20-\x7E]', '', nm).strip()
                if not nm or nm in seen_names:
                    continue
                seen_names.add(nm)
                # Get this leader's pokemon from trpoke -> personal -> types
                psize = PSIZES.get(td[0] & 3, 8)
                tp = bytes(tp_narc.files[ti]) if ti < len(tp_narc.files) else b''
                sids = []
                for pi in range(td[3]):
                    off = pi * psize
                    if off + 6 > len(tp):
                        break
                    sids.append(struct.unpack_from('<H', tp, off + 4)[0])
                specialty = _dominant_type(sids)
                if specialty is not None:
                    leader_profiles.append((nm, specialty))

            if not leader_profiles:
                return

            # Match unresolved PWT entries against leader profiles by type
            roster_path = Unova_sequel._role_path('pwt_rosters_b')
            pool_path = Unova_sequel._role_path('pwt_champions')
            if not (roster_path and pool_path):
                return
            ro_narc = _get_narc(roster_path)
            po_narc = _get_narc(pool_path)
            matched_leaders = set()  # prevent double-matching
            for idx in unresolved:
                if idx >= len(ro_narc.files):
                    continue
                rd = bytes(ro_narc.files[idx])
                if len(rd) < 6:
                    continue
                r_count = struct.unpack_from('<H', rd, 2)[0]
                pool_sids = []
                for ri in range(r_count):
                    poff = 4 + ri * 2
                    if poff + 2 > len(rd):
                        break
                    pidx = struct.unpack_from('<H', rd, poff)[0]
                    if pidx < len(po_narc.files):
                        pdata = bytes(po_narc.files[pidx])
                        if len(pdata) >= 2:
                            pool_sids.append(struct.unpack_from('<H', pdata, 0)[0])
                pwt_specialty = _dominant_type(pool_sids)
                if pwt_specialty is None:
                    continue
                for lname, ltype in leader_profiles:
                    if lname in matched_leaders:
                        continue
                    if ltype == pwt_specialty:
                        pwt_name_to_entries.setdefault(lname.lower(), []).append(idx)
                        matched_leaders.add(lname)
                        break
        except Exception:
            pass


    # PWT pool roles — these decode as individual pokemon entries (16B each)
    _PWT_POOL_ROLES = {
        'pwt_rental', 'pwt_rental_b', 'pwt_champions', 'pwt_champions_b', 'pwt_mix',
    }

    # PWT role relationships: trainer role → (roster role, pool role)
    _PWT_ROLE_CHAINS = {
        'pwt_trainers':   ('pwt_rosters',   'pwt_rental'),
        'pwt_trainers_b': ('pwt_rosters_b', 'pwt_champions'),
        'pwt_trainers_2': ('pwt_rosters_2', 'pwt_rental'),
    }

    # Roster role → pool role (includes non-PWT facilities that share the format)
    _PWT_ROSTER_POOLS = {
        'pwt_rosters':           'pwt_rental',
        'pwt_rosters_b':         'pwt_champions',
        'pwt_rosters_2':         'pwt_rental',
        'subway_trainers':       'subway_pokemon',
        'battle_tower_trainers': 'battle_tower_pokemon',
    }


    @staticmethod
    def decode_pwt(self, data: bytes, text_tables: dict, is_champions: bool = False, pool_name: str = "", pool_index: int = 0):
        """Decode PWT/facility pokemon pool entry (16B). Returns positional text."""
        if len(data) < 16 or data == b'\x00' * 16:
            return None

        species_list = text_tables.get('species', [])
        moves_list = text_tables.get('moves', [])
        natures_list = text_tables.get('natures', [])
        items_list = text_tables.get('items', [])

        species_id = struct.unpack_from('<H', data, 0)[0]
        moves = [struct.unpack_from('<H', data, 2 + i * 2)[0] for i in range(4)]
        ev_spread = data[10]
        nature = data[11]
        field12 = struct.unpack_from('<H', data, 12)[0]

        species_name = species_list[species_id] if species_id < len(species_list) else f"#{species_id}"
        nature_raw = natures_list[nature] if nature < len(natures_list) else ""
        nature_name = re.sub(r'[^\x20-\x7E]', '', nature_raw).replace(' nature.', '').strip() if nature_raw else f"nature#{nature}"

        move_names = [moves_list[m] if m < len(moves_list) else f"move#{m}" for m in moves if m != 0]
        ev_names = decode_ev_spread(ev_spread)

        item_tag = ""
        if field12 > 0:
            item_name = items_list[field12] if field12 < len(items_list) else f"item#{field12}"
            item_tag = f"  [{item_name}]"

        poke_line = f"{species_name} ({nature_name}){item_tag}"
        out = [f"[{pool_name} #{pool_index}] {poke_line}" if pool_name else poke_line]
        if move_names:
            out.append(" / ".join(move_names))
        if ev_names and ev_names != ['None']:
            out.append(f"EVs: {', '.join(ev_names)}")

        return "\n".join(out)


    @staticmethod
    def _resolve_pwt_pool_entry(pool_idx, pool_narc_path=None, pool_role='pwt_champions'):
        """Resolve a PWT pool index to a single formatted pokemon line."""
        try:
            if not pool_narc_path:
                pool_narc_path = Unova_sequel._role_path(pool_role)
            if not pool_narc_path:
                return None
            pool_narc = _get_narc(pool_narc_path)
            if pool_idx >= len(pool_narc.files):
                return None
            pdata = bytes(pool_narc.files[pool_idx])
            result = Unova_sequel.decode_pwt(pdata)
            if not result:
                return None
            return result.replace("\n", "  |  ")
        except:
            return None


    @staticmethod
    def decode_pwt_roster(self, data: bytes, slot_index: int = 0, roster_role: str = "pwt_rosters"):
        """Decode PWT/facility roster with resolved pokemon. Returns positional text."""
        if len(data) < 4:
            return None
        fmt = struct.unpack_from('<H', data, 0)[0]
        count = struct.unpack_from('<H', data, 2)[0]
        if count == 0 and fmt == 0:
            return None
        indices = []
        for i in range(count):
            off = 4 + i * 2
            if off + 2 > len(data):
                break
            indices.append(struct.unpack_from('<H', data, off)[0])
        label = roster_role.replace('pwt_', '').replace('_', ' ').title()
        out = [f"{label} Roster #{slot_index} | {count} Pokémon"]
        pool_role = Unova_sequel._PWT_ROSTER_POOLS.get(roster_role, 'pwt_rental')
        pool_path = Unova_sequel._role_path(pool_role)
        for pi in indices:
            line = Unova_sequel._resolve_pwt_pool_entry(pi, pool_narc_path=pool_path)
            if line:
                out.append(f"  Pool[{pi}] {line}")
            else:
                out.append(f"  Pool[{pi}] (empty)")
        return "\n".join(out)


    @staticmethod
    def decode_pwt_trainer_config(self, data: bytes, slot_index: int = 0, trainer_role: str = "pwt_trainers"):
        """Decode PWT trainer config (6B) with resolved roster + pokemon. Returns positional text."""
        if len(data) < 6:
            return None
        fmt = struct.unpack_from('<H', data, 0)[0]
        count = struct.unpack_from('<H', data, 2)[0]
        start_idx = struct.unpack_from('<H', data, 4)[0]
        if fmt == 0 and count == 0 and start_idx == 0:
            return None
        trainer_name = Unova_sequel._resolve_pwt_trainer_name(slot_index, trainer_role)
        if trainer_name:
            out = [f"PKMN Trainer {trainer_name} | Picks {count} from pool | Pool start: {start_idx}"]
        else:
            label = trainer_role.replace('pwt_', '').replace('_', ' ').title()
            out = [f"{label} Trainer #{slot_index} | Format: {fmt} | Picks {count} from pool | Pool start: {start_idx}"]
        # Follow the role chain: trainer role → roster role → pool role
        chain = Unova_sequel._PWT_ROLE_CHAINS.get(trainer_role)
        if chain:
            roster_role, pool_role = chain
            roster_path = Unova_sequel._role_path(roster_role)
            pool_path = Unova_sequel._role_path(pool_role)
            if roster_path and pool_path:
                try:
                    roster_narc = _get_narc(roster_path)
                    if slot_index < len(roster_narc.files):
                        rd = bytes(roster_narc.files[slot_index])
                        if len(rd) >= 4:
                            r_count = struct.unpack_from('<H', rd, 2)[0]
                            indices = []
                            for i in range(r_count):
                                off = 4 + i * 2
                                if off + 2 <= len(rd):
                                    indices.append(struct.unpack_from('<H', rd, off)[0])
                            for pi in indices:
                                line = Unova_sequel._resolve_pwt_pool_entry(pi, pool_narc_path=pool_path)
                                if line:
                                    out.append(f"  {line}")
                except:
                    pass
        return "\n".join(out)


    @staticmethod
    def _scan_pwt_tournaments(text_tables: dict):
        """Scan every file in a/0/3/8, find RCSN tournament configs, parse participant
        trainer_class IDs, and populate text_tables['tournament_classes'].

        Structure: after RCSN magic, locate first [id ≥ 0x50][flag] pair, then read
        (id, flag) pairs in groups separated by 0x00. IDs are trainer_class indices.
        Works for B2W2 only (a/0/3/8 has 20 files = 20 tournaments).
        """
        if 'tournament_classes' in text_tables:
            return

        try:
            narc = _get_narc('a/0/3/8')
        except Exception:
            return

        RCSN = b'RCSN'
        result = {}

        for file_idx, raw in enumerate(narc.files):
            try:
                data, _ = decompress_data(bytes(raw))
            except Exception:
                continue
            if not data:
                continue

            magic_off = data.find(RCSN)
            if magic_off < 0:
                continue

            # Find data start: first [id≥0x50][any] [id≥0x50][any] after the header
            data_start = -1
            for i in range(magic_off + 16, min(magic_off + 64, len(data) - 3)):
                if data[i] >= 0x50 and data[i + 2] >= 0x50 and data[i + 3] not in (0x00,):
                    data_start = i
                    break

            if data_start < 0:
                continue

            # Parse (id, flag) pairs, 0x00 = group separator
            class_ids = set()
            i = data_start
            while i + 1 < len(data):
                b = data[i]
                if b == 0x00:
                    i += 1
                    # Two consecutive 0x00 or out-of-range byte = end of section
                    if i < len(data) and (data[i] == 0x00 or data[i] < 0x40):
                        break
                    continue
                if b >= 0x50:
                    class_ids.add(b)
                    i += 2  # skip id + flag byte
                else:
                    break

            if class_ids:
                result[file_idx] = sorted(class_ids)

        if result:
            text_tables['tournament_classes'] = result


    @staticmethod
    def _resolve_pwt_text(tournament_id, text_tables: dict):
        """Resolve a PWT tournament ID to its name via the tournament_names text table.
        Tournament ID (u16 at offset 0x00 of the def) indexes directly into text file 405."""
        names = text_tables.get('tournament_names', [])
        if tournament_id < len(names):
            raw = names[tournament_id]
            if isinstance(raw, str):
                clean = re.sub(r'[^\x20-\x7E]', '', raw).strip()
                if clean and clean != '???':
                    return clean
        return None


    @staticmethod
    def decode_pwt_tournament_def(self, data: bytes, file_idx: int = 0):
        """Decode PWT tournament definition (1688B) from pwt_defs. Returns positional text."""
        if len(data) < 0x60:
            return None
        # Header
        tid = struct.unpack_from('<H', data, 0)[0]
        category = struct.unpack_from('<H', data, 2)[0]
        trainer_count = struct.unpack_from('<H', data, 4)[0]
        battle_format = struct.unpack_from('<H', data, 6)[0]
        pool_type = struct.unpack_from('<H', data, 8)[0]
        cfg5 = struct.unpack_from('<H', data, 0x0A)[0]
        cfg6 = struct.unpack_from('<H', data, 0x0C)[0]
        cfg7 = struct.unpack_from('<H', data, 0x0E)[0]
        cfg8 = struct.unpack_from('<H', data, 0x10)[0]
        flag1 = struct.unpack_from('<H', data, 0x12)[0]
        flag2 = struct.unpack_from('<H', data, 0x14)[0]

        BATTLE_TYPES = {1: "Single", 2: "Double", 3: "Triple", 4: "Rotation"}
        bt = BATTLE_TYPES.get(battle_format, f"Type {battle_format}")

        music_a = struct.unpack_from('<H', data, 0x18)[0]
        music_b = struct.unpack_from('<H', data, 0x1A)[0]

        # Tournament ID indexes directly into the tournament_names text table (file 405)
        tournament_name = Unova_sequel._resolve_pwt_text(tid) or f"Tournament #{tid}"

        out = [f"Tournament #{tid} — {tournament_name}"]
        out.append(f"Trainers: {trainer_count} | Battle: {bt} | Pool type: {pool_type}")
        out.append(f"Config: [{cfg5}, {cfg6}, {cfg7}, {cfg8}]")
        if flag1 != 0xFFFF:
            flags = [f for f in [flag1, flag2] if f != 0xFFFF]
            out.append(f"Save flags: {flags}")
        out.append(f"Music: {music_a} / {music_b}")

        # Scan data regions at known offsets for pwttr indices
        trainer_indices = set()
        for region_start, region_end in [(0xA0, 0x130), (0x160, 0x1A8)]:
            if len(data) < region_end:
                continue
            for off in range(region_start, region_end, 2):
                val = struct.unpack_from('<H', data, off)[0]
                if 1 <= val <= 68:
                    trainer_indices.add(val)

        if trainer_indices:
            sorted_idx = sorted(trainer_indices)
            out.append(f"Trainer pool indices: {sorted_idx}")
            # Resolve each via role chain
            roster_path = Unova_sequel._role_path('pwt_rosters_b')
            pool_path = Unova_sequel._role_path('pwt_champions')
            if roster_path and pool_path:
                try:
                    roster_narc = _get_narc(roster_path)
                    for ti in sorted_idx:
                        if ti >= len(roster_narc.files):
                            continue
                        rd = bytes(roster_narc.files[ti])
                        if len(rd) >= 6:
                            r_count = struct.unpack_from('<H', rd, 2)[0]
                            first_pool = struct.unpack_from('<H', rd, 4)[0]
                            line = Unova_sequel._resolve_pwt_pool_entry(first_pool, pool_narc_path=pool_path)
                            if line:
                                species_part = line.split('|')[0].strip()
                                out.append(f"  pwttr[{ti}]: {species_part}  (+{r_count - 1} more)")
                except:
                    pass

        return "\n".join(out)





    TRAINER_LOCATIONS = {
        "IRE": {
            ("Leader", "Cheren"): "Aspertia Gym",
            ("Leader", "Roxie"): "Virbank Gym",
            ("Leader", "Burgh"): "Castelia Gym",
            ("Leader", "Elesa"): "Nimbasa Gym",
            ("Leader", "Clay"): "Driftveil Gym",
            ("Leader", "Skyla"): "Mistralton Gym",
            ("Leader", "Drayden"): "Opelucid Gym",
            ("Leader", "Marlon"): "Humilau Gym",
            ("Elite Four", "Shauntal"): "Pokémon League",
            ("Elite Four", "Grimsley"): "Pokémon League",
            ("Elite Four", "Caitlin"): "Pokémon League",
            ("Elite Four", "Marshal"): "Pokémon League",
            ("Champion", "Iris"): "Pokémon League",
            ("Subway Boss", "Ingo"): "Battle Subway",
            ("Subway Boss", "Emmet"): "Battle Subway",
        },
        "IRD": "IRE",
    }

    CLASS_LOCATIONS = {
        "IRE": {
            "Elite Four": "Pokémon League", "Champion": "Pokémon League", "Subway Boss": "Battle Subway",
            "Brock": "Pokémon World Tournament", "Misty": "Pokémon World Tournament",
            "Lt. Surge": "Pokémon World Tournament", "Erika": "Pokémon World Tournament",
            "Sabrina": "Pokémon World Tournament", "Blaine": "Pokémon World Tournament",
            "Giovanni": "Pokémon World Tournament", "Falkner": "Pokémon World Tournament",
            "Bugsy": "Pokémon World Tournament", "Whitney": "Pokémon World Tournament",
            "Morty": "Pokémon World Tournament", "Chuck": "Pokémon World Tournament",
            "Jasmine": "Pokémon World Tournament", "Pryce": "Pokémon World Tournament",
            "Clair": "Pokémon World Tournament", "Janine": "Pokémon World Tournament",
            "Roxanne": "Pokémon World Tournament", "Brawly": "Pokémon World Tournament",
            "Wattson": "Pokémon World Tournament", "Flannery": "Pokémon World Tournament",
            "Norman": "Pokémon World Tournament", "Winona": "Pokémon World Tournament",
            "Tate": "Pokémon World Tournament", "Liza": "Pokémon World Tournament",
            "Juan": "Pokémon World Tournament", "Roark": "Pokémon World Tournament",
            "Gardenia": "Pokémon World Tournament", "Fantina": "Pokémon World Tournament",
            "Maylene": "Pokémon World Tournament", "Wake": "Pokémon World Tournament",
            "Byron": "Pokémon World Tournament", "Candice": "Pokémon World Tournament",
            "Volkner": "Pokémon World Tournament", "Blue": "Pokémon World Tournament",
            "Lance": "Pokémon World Tournament", "Steven": "Pokémon World Tournament",
            "Wallace": "Pokémon World Tournament", "Red": "Pokémon World Tournament",
            "Cynthia": "Pokémon World Tournament", "Alder": "Pokémon World Tournament",
        },
        "IRD": "IRE",
    }


    GET_CHALLENGE_DELTA = get_bw2_challenge_delta
    BUILD_PWT_MAPS = _build_pwt_maps
    SCAN_PWT_TOURNAMENTS = _scan_pwt_tournaments
    ROLE_PATH = _role_path
    PWT_POOL_ROLES = _PWT_POOL_ROLES


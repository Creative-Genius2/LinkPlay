"""Gen V (Black/White/Black2/White2) - text decoder, NARC paths, PWT, encounters."""
import struct


# ============================================================
# # Gen V text decoder: _GEN5_CHARMAP, _derive_gen5_mult, decode_gen5_text
# server.py lines 1938-2066
# ============================================================

# ============ Gen V Text Decoder ============

# Gen V special character substitutions (packed words for common game terms)
_GEN5_CHARMAP = {
    0x2467: 'Mr.', 0x2468: 'Ms.', 0x2469: 'Mrs.',
    0x246D: 'the', 0x246E: 'The',
    0x2486: 'Poké', 0x2487: 'mon',
}

def _derive_gen5_mult(species_data: bytes) -> int:
    """Derive XOR multiplier from species file entry 1 ('Bulbasaur').
    Seed for entry 1 = (1+3)*MULT = 4*MULT. XOR encrypted[0] with 'B' (0x0042) gives 4*MULT.
    """
    if len(species_data) < 16:
        return 0x2983
    entry_count = struct.unpack_from('<H', species_data, 2)[0]
    section_offset = struct.unpack_from('<I', species_data, 0x0C)[0]
    if entry_count < 2 or section_offset + 4 > len(species_data):
        return 0x2983
    # Read entry 1 from entry table
    entry_pos = section_offset + 4 + (1 * 8)
    if entry_pos + 8 > len(species_data):
        return 0x2983
    offset = struct.unpack_from('<I', species_data, entry_pos)[0]
    str_offset = section_offset + offset
    if str_offset + 2 > len(species_data):
        return 0x2983
    encrypted_0 = struct.unpack_from('<H', species_data, str_offset)[0]
    four_mult = encrypted_0 ^ 0x0042
    return (four_mult // 4) & 0xFFFF


def decode_gen5_text(data: bytes, mult: int = 0x2983) -> list:
    """Decode a Gen V encrypted text file. MULT derived once from NARC, passed in.
    Seed = (entry_index + 3) * mult, key advances via ROL3.
    Control codes (0xFFFE) consumed properly: type(u16), param_count(u16), params(u16*n).
    """
    if len(data) < 16:
        return []

    entry_count = struct.unpack_from('<H', data, 2)[0]
    section_offset = struct.unpack_from('<I', data, 0x0C)[0]

    if entry_count == 0 or entry_count > 10000:
        return []
    if section_offset + 4 > len(data):
        return []

    entry_table_start = section_offset + 4
    strings = []

    for i in range(entry_count):
        entry_pos = entry_table_start + (i * 8)
        if entry_pos + 8 > len(data):
            break
        offset = struct.unpack_from('<I', data, entry_pos)[0]
        char_count = struct.unpack_from('<H', data, entry_pos + 4)[0]

        str_offset = section_offset + offset
        key = ((i + 3) * mult) & 0xFFFF

        # Decrypt all u16 values for this entry
        vals = []
        for j in range(char_count):
            char_pos = str_offset + (j * 2)
            if char_pos + 2 > len(data):
                break
            enc = struct.unpack_from('<H', data, char_pos)[0]
            dec = enc ^ key
            key = ((key << 3) | (key >> 13)) & 0xFFFF
            vals.append(dec)

        # F100 = 9-bit compressed text (LSB-first, 0x1FF terminator)
        if vals and vals[0] == 0xF100:
            bits = 0
            nbits = 0
            for w in vals[1:]:
                if w == 0xFFFF:
                    break
                bits |= (w << nbits)
                nbits += 16
            chars = []
            while nbits >= 9:
                c = bits & 0x1FF
                bits >>= 9
                nbits -= 9
                if c == 0x1FF:
                    break
                try:
                    chars.append(chr(c) if c >= 0x20 else f'\\x{c:04X}')
                except (ValueError, OverflowError):
                    chars.append(f'\\x{c:04X}')
            strings.append(''.join(chars))
            continue

        # Normal text: parse control codes and characters
        chars = []
        j = 0
        while j < len(vals):
            dec = vals[j]
            j += 1

            if dec == 0xFFFF:
                break
            elif dec == 0xFFFE:
                ctrl_type = vals[j] if j < len(vals) else 0
                j += 1
                param_count = vals[j] if j < len(vals) else 0
                j += 1
                j += param_count  # skip params
                if ctrl_type == 0x0000 or ctrl_type & 0xFF00 == 0x0000:
                    chars.append('\n')
                elif ctrl_type & 0xFF00 == 0x0100:
                    chars.append('[var]')
                elif ctrl_type & 0xFF00 in (0xBE00, 0xFF00):
                    pass  # formatting, skip
                else:
                    chars.append(f'[ctrl:{ctrl_type:04X}]')
            elif dec in _GEN5_CHARMAP:
                chars.append(_GEN5_CHARMAP[dec])
            else:
                try:
                    chars.append(chr(dec))
                except (ValueError, OverflowError):
                    chars.append(f'\\x{dec:04X}')

        strings.append(''.join(chars))

    return strings

# ============================================================
# # _GEN5_B2W2, _GEN5_BW1, encounters, PWT, subway NARC dicts
# server.py lines 2070-2123
# ============================================================

_GEN5_B2W2 = {
    'text': 'a/0/0/2',
    'trdata': 'a/0/9/1',
    'trpoke': 'a/0/9/2',
    'personal': 'a/0/1/6',
    'learnsets': 'a/0/1/8',
    'evolutions': 'a/0/1/9',
    'move_data': 'a/0/2/1',
    'items': 'a/0/2/4',
    'baby_species': 'a/0/2/0',  # Maps species→baby form (NOT egg moves)
}
_GEN5_BW1 = {
    'text': 'a/0/0/2',
    'trdata': 'a/0/9/2',  # Different from B2W2!
    'trpoke': 'a/0/9/3',  # Different from B2W2!
    'personal': 'a/0/1/6',
    'learnsets': 'a/0/1/8',
    'evolutions': 'a/0/1/9',
    'move_data': 'a/0/2/1',
    'items': 'a/0/2/4',
    'baby_species': 'a/0/2/0',  # Maps species→baby form (NOT egg moves)
    'egg_moves': 'a/0/2/0',
}
_BW1_ENCOUNTERS = {
    'encounters': 'a/1/2/6',  # 112 files, 232 bytes each
}
_B2W2_ENCOUNTERS = {
    'encounters': 'a/1/2/7',  # 135 files, 232 or 928 bytes (seasonal)
}
_B2W2_PWT = {
    'pwt_rental': 'a/2/5/0',           # 1000 pokemon pools (16 bytes each)
    'pwt_trainers': 'a/2/5/1',         # 120 tournament trainer configs (6 bytes each)
    'pwt_rosters': 'a/2/5/2',          # 120 tournament rosters -> pool indices
    'pwt_rental_b': 'a/2/5/3',         # 1000 pokemon pools B
    'pwt_trainers_b': 'a/2/5/4',       # 69 tournament trainer configs B
    'pwt_rosters_b': 'a/2/5/5',        # 69 tournament rosters B -> pool indices
    'pwt_champions': 'a/2/5/6',        # 1000 pokemon pools
    'pwt_champions_b': 'a/2/5/7',      # 1000 pokemon pools B
    'pwt_download': 'a/2/5/8',         # 1 file — download tournament metadata (multilingual)
    'pwt_ui': 'a/2/5/9',              # 9 files — UI graphics (RLCN/RGCN/RCSN)
    'pwt_trainers_2': 'a/2/4/8',      # 120 trainer configs (secondary tournament set)
    'pwt_rosters_2': 'a/2/4/9',        # 120 rosters (secondary tournament set)
    'pwt_mix': 'a/2/6/1',              # 1000 pokemon pool (Mix Tournament)
    'pwt_defs': 'a/2/6/9',             # 42 tournament definitions (1688B each)
    'pwt_trainer_map': 'a/2/4/0',      # trainer index → name/sprite mapping (20B stride, u16[8]=class_id)
}
_B2W2_SUBWAY = {
    'subway_pokemon': 'a/2/1/1',       # 1000 pokemon pool (16B, same format as PWT)
    'subway_trainers': 'a/2/1/2',      # 315 trainers (format + count + pool indices)
}
_BW1_SUBWAY = {
    'subway_pokemon': 'a/2/1/4',       # 1000 pokemon pool (same format)
    'subway_trainers': 'a/2/1/5',      # 315 trainers
}

# ============================================================
# # AI_FLAGS_GEN5
# server.py lines 2745-2762
# ============================================================

AI_FLAGS_GEN5 = {
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

# ============================================================
# # _BW2_CHALLENGE_FILE_DELTA + get_bw2_challenge_delta
# server.py lines 2793-2832
# ============================================================

# BW2 Challenge Mode runtime level delta table.
# Verified by measuring stored trpoke levels vs actual in-game levels.
# The game applies a flat per-trainer-file delta at runtime on top of stored levels.
# Pattern: +1 per pair of gyms, capped at +4 from gym 7 onward (E4, Champion included).
# Keyed by trdata/trpoke file index -> challenge delta.
# Normal mode files and unkeyed files get delta 0.
_BW2_CHALLENGE_FILE_DELTA = {
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

def get_bw2_challenge_delta(file_idx: int, game_code: str = '') -> int:
    """Get runtime challenge level delta for a BW2 trainer file.
    Returns 0 for Normal mode files, non-BW2 games, or unkeyed files.
    """
    if game_code not in ('IRE', 'IRD'):
        return 0
    return _BW2_CHALLENGE_FILE_DELTA.get(file_idx, 0)

# ============================================================
# # TRPOKE_FORMATS_G5
# server.py lines 2838-2843
# ============================================================

TRPOKE_FORMATS_G5 = {
    0: 8,   # base
    1: 16,  # + moves(8)
    2: 10,  # + item(2)
    3: 18,  # + item(2) + moves(8)
}

# ============================================================
# # _decode_encounters_gen5
# server.py lines 5007-5067
# ============================================================

def _decode_encounters_gen5(data: bytes) -> dict:
    """Decode Gen V encounter data (BW/B2W2).
    232 bytes per season. Species u16 encodes form in upper bits (& 0x7FF)."""
    if len(data) < 232:
        return None

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
                name = get_text("species", species_id)
                form_label = _FORM_NAMES.get((species_id, form))
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

# ============================================================
# # PWT system (all functions)
# server.py lines 3894-4396
# ============================================================

def _role_path(role_name, narc_roles: dict):
    """Look up a NARC path by its role name, using the narc_roles reverse map."""
    for path, role in narc_roles.items():
        if role == role_name:
            return path
    return None


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
        map_path = _role_path('pwt_trainer_map')
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
        defs_path = _role_path('pwt_defs')
        if not defs_path:
            return
        defs_narc = _get_narc(defs_path)
        for fi, raw in enumerate(defs_narc.files):
            data = bytes(raw)
            if len(data) < 0x1A8:
                continue
            tid = struct.unpack_from('<H', data, 0)[0]
            tname = _resolve_pwt_text(tid) or f"Tournament #{tid}"
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
        tb_path = _role_path('pwt_trainers_b')
        if not tb_path:
            return
        tb_count = len(_get_narc(tb_path).files)
        unresolved = []
        for idx in range(1, tb_count):
            name = _resolve_pwt_trainer_name(idx)
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
        trdata_path = _role_path('trdata')
        trpoke_path = _role_path('trpoke')
        personal_path = _role_path('personal')
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
        roster_path = _role_path('pwt_rosters_b')
        pool_path = _role_path('pwt_champions')
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


def decode_pwt(data: bytes, text_tables: dict, is_champions: bool = False, pool_name: str = "", pool_index: int = 0):
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


def _resolve_pwt_pool_entry(pool_idx, pool_narc_path=None, pool_role='pwt_champions'):
    """Resolve a PWT pool index to a single formatted pokemon line."""
    try:
        if not pool_narc_path:
            pool_narc_path = _role_path(pool_role)
        if not pool_narc_path:
            return None
        pool_narc = _get_narc(pool_narc_path)
        if pool_idx >= len(pool_narc.files):
            return None
        pdata = bytes(pool_narc.files[pool_idx])
        result = decode_pwt(pdata)
        if not result:
            return None
        return result.replace("\n", "  |  ")
    except:
        return None


def decode_pwt_roster(data: bytes, slot_index: int = 0, roster_role: str = "pwt_rosters"):
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
    pool_role = _PWT_ROSTER_POOLS.get(roster_role, 'pwt_rental')
    pool_path = _role_path(pool_role)
    for pi in indices:
        line = _resolve_pwt_pool_entry(pi, pool_narc_path=pool_path)
        if line:
            out.append(f"  Pool[{pi}] {line}")
        else:
            out.append(f"  Pool[{pi}] (empty)")
    return "\n".join(out)


def decode_pwt_trainer_config(data: bytes, slot_index: int = 0, trainer_role: str = "pwt_trainers"):
    """Decode PWT trainer config (6B) with resolved roster + pokemon. Returns positional text."""
    if len(data) < 6:
        return None
    fmt = struct.unpack_from('<H', data, 0)[0]
    count = struct.unpack_from('<H', data, 2)[0]
    start_idx = struct.unpack_from('<H', data, 4)[0]
    if fmt == 0 and count == 0 and start_idx == 0:
        return None
    trainer_name = _resolve_pwt_trainer_name(slot_index, trainer_role)
    if trainer_name:
        out = [f"PKMN Trainer {trainer_name} | Picks {count} from pool | Pool start: {start_idx}"]
    else:
        label = trainer_role.replace('pwt_', '').replace('_', ' ').title()
        out = [f"{label} Trainer #{slot_index} | Format: {fmt} | Picks {count} from pool | Pool start: {start_idx}"]
    # Follow the role chain: trainer role → roster role → pool role
    chain = _PWT_ROLE_CHAINS.get(trainer_role)
    if chain:
        roster_role, pool_role = chain
        roster_path = _role_path(roster_role)
        pool_path = _role_path(pool_role)
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
                            line = _resolve_pwt_pool_entry(pi, pool_narc_path=pool_path)
                            if line:
                                out.append(f"  {line}")
            except:
                pass
    return "\n".join(out)


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


def decode_pwt_tournament_def(data: bytes, file_idx: int = 0):
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
    tournament_name = _resolve_pwt_text(tid) or f"Tournament #{tid}"

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
        roster_path = _role_path('pwt_rosters_b')
        pool_path = _role_path('pwt_champions')
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
                        line = _resolve_pwt_pool_entry(first_pool, pool_narc_path=pool_path)
                        if line:
                            species_part = line.split('|')[0].strip()
                            out.append(f"  pwttr[{ti}]: {species_part}  (+{r_count - 1} more)")
            except:
                pass

    return "\n".join(out)

"""Gen VII (Sun/Moon/Ultra Sun/Ultra Moon + Let's Go Pikachu/Eevee) — GARC paths, text table indices."""

# ============================================================
# Text table sub-file indices within the text GARC (a/0/3/2)
# Verified from Ultra Moon scan — 127 sub-files total
# ============================================================
_GEN7_TEXT_TABLE_MAP = {
    'species': 60, 'items': 40, 'moves': 118,
    'abilities': 101, 'natures': 92, 'type_names': 112,
    'trainer_names': 110, 'trainer_classes': 111, 'location_names': 72,
}

# ============================================================
# GARC path dicts — same structure as Gen V NARC dicts
# ============================================================
_GEN7_USUM = {
    'text': 'a/0/3/2',
    'personal': 'a/0/1/7',
    'learnsets': 'a/0/1/3',
    'evolutions': 'a/0/1/4',
    'egg_moves': 'a/0/1/2',
    'baby_species': 'a/0/1/8',
    'items': 'a/0/1/9',
    'trdata': 'a/1/0/6',
    'trpoke': 'a/1/0/7',
    'move_data': 'a/0/1/1',  # flat WD file: 4B header + 730 u32 offsets + 729 entries × 40B
}

_GEN7_SM = {
    'text': 'a/0/3/2',
    'personal': 'a/0/1/7',
    'learnsets': 'a/0/1/3',   # TBD: verify — SM map matches these paths
    'evolutions': 'a/0/1/4',
    'egg_moves': 'a/0/1/2',
    'baby_species': 'a/0/1/8',
    'items': 'a/0/1/9',
    'trdata': 'a/1/0/2',     # SM map — not verified against actual ROM
    'trpoke': 'a/1/0/3',     # SM map — not verified against actual ROM
    'move_data': 'a/0/1/1',  # SM map — not verified against actual ROM
}

# LGPE: Switch games, Gen 7 mechanics. Container = Switch ROM (not 3DS).
# Same 0x2983/ROL3 cipher — xoleon decodes the text, just needs a Switch container reader.
_GEN7_LGPE = {
    # TBD: Switch ROM support not yet implemented
}


# ============================================================
# Gen VII encounter decoder + formatter
# ============================================================

def _unpack_mini(data: bytes, ident: str = "EA"):
    """Unpack a Mini/BinLinkerAccessor container. Returns list of byte arrays."""
    if not data or len(data) < 4:
        return None
    if chr(data[0]) != ident[0] or chr(data[1]) != ident[1]:
        return None
    import struct
    count = struct.unpack_from('<H', data, 2)[0]
    if len(data) < 4 + (count + 1) * 4:
        return None
    ctr = 4
    start = struct.unpack_from('<I', data, ctr)[0]; ctr += 4
    entries = []
    for i in range(count):
        end = struct.unpack_from('<I', data, ctr)[0]; ctr += 4
        length = end - start
        entries.append(data[start:start + length])
        start = end
    return entries


def _parse_encounter_table_gen7(t: bytes) -> dict | None:
    """Parse a single 0x164-byte Gen VII encounter table.
    Returns dict with integer species/form IDs — no name resolution."""
    import struct
    if not t or len(t) < 0x164:
        return None

    min_level = t[0]
    max_level = t[1]
    if min_level == 0 and max_level == 0:
        return None  # empty table

    rates = list(t[2:12])  # 10 u8 slot rates

    # 8 slot sets x 10 entries: [0]=base, [1-6]=SOS ally types, [7]=weather SOS
    slot_sets = []
    for i in range(8):
        slots = []
        ofs = 0x0C + (i * 40)  # 10 entries x 4 bytes = 40
        for j in range(10):
            val = struct.unpack_from('<I', t, ofs + j * 4)[0]
            species = val & 0x7FF
            form = (val >> 11) & 0x1F
            slots.append((species, form))
        slot_sets.append(slots)

    # 6 additional SOS weather entries at 0x14C
    sos_weather = []
    for i in range(6):
        val = struct.unpack_from('<I', t, 0x14C + i * 4)[0]
        species = val & 0x7FF
        form = (val >> 11) & 0x1F
        sos_weather.append((species, form))

    return {
        'min_level': min_level,
        'max_level': max_level,
        'rates': rates,
        'slot_sets': slot_sets,
        'sos_weather': sos_weather,
    }


def _decode_encounters_gen7(data: bytes) -> dict | None:
    """Decode Gen VII encounter data from a zone encounter sub-file.
    Input: raw bytes from GARC sub-file (index 9 + 11*area).
    Format: Mini("EA") packed, each entry = 4B pad + Day(0x164) + Night(0x164).
    Returns structured dict with integer species/form IDs.
    Returns None if not an encounter file (no "EA" header)."""
    entries = _unpack_mini(data, "EA")
    if not entries:
        return None

    tables = []
    for entry in entries:
        if len(entry) < 4 + 0x164:
            continue
        day = _parse_encounter_table_gen7(entry[4:4 + 0x164])
        night_start = 4 + 0x164
        night = None
        if len(entry) >= night_start + 0x164:
            night = _parse_encounter_table_gen7(entry[night_start:night_start + 0x164])
        if day or night:
            tables.append({'day': day, 'night': night})

    if not tables:
        return None
    return {'gen': 7, 'tables': tables}


def _format_encounter_gen7(decoded: dict, file_idx: int, name_resolver=None) -> str | None:
    """Format Gen VII encounter data as readable text.
    name_resolver: callable(species_id, form) -> str. If None, uses species ID numbers."""
    if not decoded or 'tables' not in decoded:
        return None

    def resolve(species_id, form):
        if species_id == 0:
            return None
        if name_resolver:
            return name_resolver(species_id, form)
        suffix = f" (Form {form})" if form else ""
        return f"#{species_id}{suffix}"

    lines = []
    tables = decoded['tables']
    for t_idx, tbl in enumerate(tables):
        if len(tables) > 1:
            lines.append(f"\nTable {t_idx + 1}:")

        for time_key, label in [('day', 'Day'), ('night', 'Night')]:
            table = tbl.get(time_key)
            if not table:
                continue
            rates = table['rates']
            slots = table['slot_sets'][0]  # base encounter set
            min_lv = table['min_level']
            max_lv = table['max_level']
            lv_str = f"Lv. {min_lv}-{max_lv}" if min_lv != max_lv else f"Lv. {min_lv}"

            # Consolidate duplicate species, summing rates
            combined = {}
            for i, (sp, fm) in enumerate(slots):
                if sp == 0:
                    continue
                name = resolve(sp, fm)
                if not name:
                    continue
                rate = rates[i] if i < len(rates) else 0
                if name in combined:
                    combined[name] += rate
                else:
                    combined[name] = rate

            if not combined:
                continue
            lines.append(f"\n{label} ({lv_str}):")
            for name, rate in sorted(combined.items(), key=lambda x: -x[1]):
                lines.append(f"  {name:<22}{rate:>3}%")

            # SOS allies — collect unique non-zero species from sets 1-7
            sos_names = []
            for sos_set in table['slot_sets'][1:]:
                for sp, fm in sos_set:
                    if sp == 0:
                        continue
                    n = resolve(sp, fm)
                    if n and n not in sos_names:
                        sos_names.append(n)
            if sos_names:
                lines.append(f"  SOS Allies: {', '.join(sos_names)}")

            # Weather SOS
            weather_names = []
            for sp, fm in table['sos_weather']:
                if sp == 0:
                    continue
                n = resolve(sp, fm)
                if n and n not in weather_names:
                    weather_names.append(n)
            if weather_names:
                lines.append(f"  Weather SOS: {', '.join(weather_names)}")

    return "\n".join(lines).strip() if lines else None


_GEN7_FLIPNOTE_PAIRS = {
    # Gen VII
    'Pokémon Ultra Sun & Ultra Moon': ['A2A', 'A2B'],
    'Pokémon Sun & Moon': ['1Q1', '1Q2'],
}


_GEN7_GAME_INFO = {
    # Gen VII — Nintendo 3DS
    'A2B': {'gen': 7, 'platform': 'Nintendo 3DS', 'year': 2017, 'title': 'Pokémon Ultra Moon', 'narcs': {**_GEN7_USUM, 'encounters': 'a/0/8/3'}},    # Ultra Moon
    'A2A': {'gen': 7, 'platform': 'Nintendo 3DS', 'year': 2017, 'title': 'Pokémon Ultra Sun', 'narcs': {**_GEN7_USUM, 'encounters': 'a/0/8/2'}},     # Ultra Sun
    '1Q2': {'gen': 7, 'platform': 'Nintendo 3DS', 'year': 2016, 'title': 'Pokémon Moon', 'narcs': {**_GEN7_SM, 'encounters': 'a/0/8/3'}},            # Moon
    '1Q1': {'gen': 7, 'platform': 'Nintendo 3DS', 'year': 2016, 'title': 'Pokémon Sun', 'narcs': {**_GEN7_SM, 'encounters': 'a/0/8/2'}},             # Sun
}

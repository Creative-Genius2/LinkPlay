"""Gen VII (Sun/Moon/Ultra Sun/Ultra Moon + Let's Go Pikachu/Eevee) — GARC paths, text table indices."""

# ============================================================
# Text table sub-file indices within the text GARC (a/0/3/2)
# Verified from Ultra Moon scan — 127 sub-files total
# ============================================================
_GEN7_TEXT_TABLE_MAP = {
    'species': 60, 'items': 40, 'moves': 118,
    'abilities': 101, 'natures': 92, 'type_names': 112,
    'trainer_names': 104, 'trainer_classes': 111, 'location_names': 72,
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
    'encounters': 'a/0/6/1',
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
    'encounters': 'a/0/6/1',
    'trdata': 'a/1/0/2',     # SM map — not verified against actual ROM
    'trpoke': 'a/1/0/3',     # SM map — not verified against actual ROM
    'move_data': 'a/0/1/1',  # SM map — not verified against actual ROM
}

# LGPE: Switch games, Gen 7 mechanics. Container = Switch ROM (not 3DS).
# Same 0x2983/ROL3 cipher — xoleon decodes the text, just needs a Switch container reader.
_GEN7_LGPE = {
    # TBD: Switch ROM support not yet implemented
}


_GEN7_FLIPNOTE_PAIRS = {
    # Gen VII
    'Pokémon Ultra Sun & Ultra Moon': ['A2A', 'A2B'],
    'Pokémon Sun & Moon': ['1Q1', '1Q2'],
}


_GEN7_GAME_INFO = {
    # Gen VII — Nintendo 3DS
    'A2B': {'gen': 7, 'platform': 'Nintendo 3DS', 'year': 2017, 'title': 'Pokémon Ultra Moon', 'narcs': {**_GEN7_USUM}},    # Ultra Moon
    'A2A': {'gen': 7, 'platform': 'Nintendo 3DS', 'year': 2017, 'title': 'Pokémon Ultra Sun', 'narcs': {**_GEN7_USUM}},     # Ultra Sun
    '1Q2': {'gen': 7, 'platform': 'Nintendo 3DS', 'year': 2016, 'title': 'Pokémon Moon', 'narcs': {**_GEN7_SM}},            # Moon
    '1Q1': {'gen': 7, 'platform': 'Nintendo 3DS', 'year': 2016, 'title': 'Pokémon Sun', 'narcs': {**_GEN7_SM}},             # Sun
}

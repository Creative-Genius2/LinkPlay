_GEN3_FLIPNOTE_PAIRS = {
    # Gen III (GBA)
    'Pokémon FireRed & LeafGreen': ['BPRE', 'BPGE'],
    'Pokémon Ruby & Sapphire': ['AXVE', 'AXPE'],
    'Pokémon Emerald': ['BPEE'],
}

_GEN3_GAME_INFO = {
    # Gen III — Game Boy Advance
    'BPRE': {'gen': 3, 'platform': 'Game Boy Advance', 'year': 2004, 'title': 'POKÉMON FIRERED'},
    'BPGE': {'gen': 3, 'platform': 'Game Boy Advance', 'year': 2004, 'title': 'POKÉMON LEAFGREEN'},
    'AXVE': {'gen': 3, 'platform': 'Game Boy Advance', 'year': 2003, 'title': 'POKÉMON RUBY'},
    'AXPE': {'gen': 3, 'platform': 'Game Boy Advance', 'year': 2003, 'title': 'POKÉMON SAPPHIRE'},
    'BPEE': {'gen': 3, 'platform': 'Game Boy Advance', 'year': 2005, 'title': 'POKÉMON EMERALD'},
}

TABLE_FINGERPRINTS_GEN3 = {
    'moves':      [(0, "Pound"), (4, "Mega Punch")],
    'items':      [(0, "Master Ball"), (3, "Poké Ball"), (12, "Potion")],
    'type_names': [(0, "NORMAL"), (1, "FIGHT"), (2, "FLYING")],  # Gen III uses abbreviated display names
}

_GEN1_FLIPNOTE_PAIRS = {
    # Gen I (GB) — US
    'Pokémon Red & Blue': ['PMR', 'PMB'],
    'Pokémon Yellow': ['PMY'],
    # Gen I (GB) — JP
    'Pocket Monsters Red & Green': ['PKMRJ', 'PMG'],
    'Pocket Monsters Blue (JP)': ['PMBJP'],
    'Pocket Monsters Yellow (JP)': ['PMYJ'],
}

_GEN1_GAME_INFO = {
    # Gen I — Game Boy (EN)
    'PMR':  {'gen': 1, 'platform': 'Game Boy', 'year': 1998, 'title': 'POKÉMON RED'},
    'PMB':  {'gen': 1, 'platform': 'Game Boy', 'year': 1998, 'title': 'POKÉMON BLUE'},
    'PMY':  {'gen': 1, 'platform': 'Game Boy', 'year': 1999, 'title': 'POKÉMON YELLOW'},
    # Gen I — Game Boy (JP)
    'PMG':    {'gen': 1, 'platform': 'Game Boy', 'year': 1996, 'title': 'POCKET MONSTERS GREEN',  'jp': True},
    'PKMRJ':  {'gen': 1, 'platform': 'Game Boy', 'year': 1996, 'title': 'POCKET MONSTERS RED',    'jp': True},
    'PMBJP':  {'gen': 1, 'platform': 'Game Boy', 'year': 1996, 'title': 'POCKET MONSTERS BLUE',   'jp': True},
    'PMYJ':   {'gen': 1, 'platform': 'Game Boy', 'year': 1998, 'title': 'POCKET MONSTERS YELLOW', 'jp': True},
}

TABLE_FINGERPRINTS_JPN = {
    'species':    [(1, "フシギダネ"), (4, "ヒトカゲ")],
    'moves':      [(0, "はたく"), (4, "メガトンパンチ")],  # JP Gen I: Pound at index 0 (no dummy)
    'items':      [(1, "マスターボール")],
    'natures':    [(0, "がんばりや"), (1, "さみしがり"), (3, "いじっぱり")],
    'type_names': [(0, "ノーマル")],
}

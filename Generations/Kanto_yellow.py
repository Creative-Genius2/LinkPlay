"""Kanto_yellow.py: Pokemon Yellow (EN + JP).

Inherits Kanto_rbg. Structurally identical to Red/Blue/Green.
Separate file because game codes differ.
"""
from Generations.Kanto_rbg import Kanto_rbg


class Kanto_yellow(Kanto_rbg):
    """Pokemon Yellow (EN + JP). Inherits Red/Blue/Green baseline."""

    GAME_CODES = ('PMY', 'PMYJ')
    TITLES = ('POKÉMON YELLOW', 'POCKET MONSTERS YELLOW')
    YEAR = 1999

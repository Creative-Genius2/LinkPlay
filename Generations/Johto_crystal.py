"""Johto_crystal.py: Pokemon Crystal.

Inherits Johto_gs. Structurally identical to Gold/Silver.
"""
from Generations.Johto_gs import Johto_gs


class Johto_crystal(Johto_gs):
    """Pokemon Crystal. Inherits Gold/Silver baseline."""

    GAME_CODES = ('PM_',)
    TITLES = ('POKÉMON CRYSTAL',)
    YEAR = 2001

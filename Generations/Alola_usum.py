"""Alola_usum.py: Ultra Sun/Ultra Moon.

Inherits Alola_sm. Different trainer paths, 807 species.
"""
from Generations.Alola_sm import Alola_sm


class Alola_usum(Alola_sm):
    """Ultra Sun/Ultra Moon. Inherits Sun/Moon, shifted trainer paths."""

    GAME_CODES = ('A2A', 'A2B')
    TITLES = ('POKÉMON ULTRA SUN', 'POKÉMON ULTRA MOON')
    YEAR = 2017

    SPECIES_COUNT = 807

    TRDATA_PATH = 'a/1/0/6'
    TRPOKE_PATH = 'a/1/0/7'

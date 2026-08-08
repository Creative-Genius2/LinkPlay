"""Hoenn_remake.py: Omega Ruby/Alpha Sapphire.

Inherits Kalos_prequel. Same engine, different GARC paths.
"""
from Generations.Kalos_prequel import Kalos_prequel


class Hoenn_remake(Kalos_prequel):
    """Omega Ruby/Alpha Sapphire. Inherits X/Y engine, ORAS paths."""

    GAME_CODES = ('ECR', 'ECL')
    TITLES = ('POKÉMON OMEGA RUBY', 'POKÉMON ALPHA SAPPHIRE')
    YEAR = 2014

    TEXT_PATH = 'a/0/7/9'
    PERSONAL_PATH = 'a/1/9/5'
    LEARNSET_PATH = 'a/1/9/1'
    EVOLUTION_PATH = 'a/1/9/2'
    EGG_MOVES_PATH = 'a/1/9/0'
    MEGA_EVOS_PATH = 'a/1/9/3'
    ITEM_PATH = 'a/1/9/7'
    MOVE_DATA_PATH = 'a/1/8/9'
    ENCOUNTER_PATH = 'a/0/1/3'
    TRDATA_PATH = 'a/0/3/6'
    TRPOKE_PATH = 'a/0/3/8'
    TRCLASS_PATH = 'a/0/3/7'
    MAISON_POKEMON_NORMAL_PATH = 'a/1/8/2'
    MAISON_TRAINERS_NORMAL_PATH = 'a/1/8/3'
    MAISON_POKEMON_SUPER_PATH = 'a/1/8/4'
    MAISON_TRAINERS_SUPER_PATH = 'a/1/8/5'

    PERSONAL_SIZE = 0x50

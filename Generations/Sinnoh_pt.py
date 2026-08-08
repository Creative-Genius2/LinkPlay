"""Sinnoh_pt.py: Platinum.

Inherits Sinnoh_dp. Overrides 7 paths (pl_ prefix variants)
and text table indices.
"""
from Generations.Sinnoh_dp import Sinnoh_dp


class Sinnoh_pt(Sinnoh_dp):
    """Platinum. Inherits Diamond/Pearl, overrides pl_ paths."""

    GAME_CODES = ('CPU',)
    TITLES = ('POKÉMON PLATINUM',)
    YEAR = 2009

    # ── Path overrides (pl_ prefix) ──
    TEXT_PATH = 'msgdata/pl_msg.narc'
    PERSONAL_PATH = 'poketool/personal/pl_personal.narc'
    MOVE_DATA_PATH = 'poketool/waza/pl_waza_tbl.narc'
    ITEM_PATH = 'itemtool/itemdata/pl_item_data.narc'
    ENCOUNTER_PATH = 'fielddata/encountdata/pl_enc_data.narc'
    BATTLE_TOWER_POKEMON_PATH = 'battle/b_pl_tower/pl_btdpm.narc'
    BATTLE_TOWER_TRAINERS_PATH = 'battle/b_pl_tower/pl_btdtr.narc'

    # ── Text table indices (Platinum) ──
    TEXT_SPECIES = 412
    TEXT_MOVES = 648
    TEXT_ITEMS = 392
    TEXT_ABILITIES = 611
    TEXT_TYPE_NAMES = 624
    TEXT_NATURES = 202
    TEXT_TRAINER_CLASSES = 619
    TEXT_TRAINER_NAMES = 618
    TEXT_LOCATIONS = 433

    def discover_enc_loc(self, arm9, enc_count=183):
        """Platinum: ARM9 flat table at 0xF0D4A — u16 loc per enc file."""
        import struct
        enc_map = {}
        for i in range(enc_count):
            off = 0xF0D4A + i * 2
            if off + 2 <= len(arm9):
                v = struct.unpack_from('<H', bytes(arm9), off)[0]
                if v > 0:
                    enc_map[i] = v
        return enc_map

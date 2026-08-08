"""Johto_gs.py: Gold/Silver — Gen II structural baseline.

Inherits Kanto_yellow. Gen II added: Special split (5->6 stats),
Dark/Steel types, breeding, held items, time-of-day, 251 species.
Same charmap as Gen I (EN), same EOS.
"""
from Generations.Kanto_yellow import Kanto_yellow
import struct


class Johto_gs(Kanto_yellow):
    """Gold/Silver. Gen II baseline."""

    GAME_CODES = ('PMG2', 'PMS')
    TITLES = ('POKÉMON GOLD', 'POKÉMON SILVER')
    YEAR = 2000

    PLATFORM = 'Game Boy Color'
    GEN = 2
    CONTAINER = 'gbc'

    STAT_COUNT = 6
    STAT_ORDER = ('HP', 'Atk', 'Def', 'Spe', 'SpA', 'SpD')

    CHARMAP_EN = dict(Kanto_yellow.CHARMAP_EN)

    SPECIES_COUNT = 251

    FLIPNOTE_PAIRS = {
        'Pokémon Gold & Silver': ['PMG2', 'PMS'],
        'Pokémon Crystal': ['PM_'],
    }

    @staticmethod
    def _scan_gen2_trainer_classes_en(rom_data: bytes, charmap: dict, eos: int, text_tables: dict):
        """Scan Gen II EN trainer class names. Anchors at LEADER (class 0 = first gym leader class).
        Class ordering matches group pointer table (pret/pokegold): LEADER×8, RIVAL, PROF, ELITE FOUR×4,
        CHAMPION, LEADER×3(Kanto), then random trainers.
        Populates text_tables['trainer_classes'].
        """
        if 'trainer_classes' in text_tables:
            return

        reverse = {v: k for k, v in charmap.items() if isinstance(v, str) and len(v) == 1}
        # Anchor: "LEADER" appears first in both the text table and the group pointer table
        # Find the occurrence of LEADER followed 8 entries later by RIVAL
        try:
            leader_enc = bytes([reverse[c] for c in 'LEADER']) + bytes([eos])
            rival_enc  = bytes([reverse[c] for c in 'RIVAL'])  + bytes([eos])
        except KeyError:
            return

        # Find LEADER occurrence that has RIVAL exactly 8 names later
        table_start = -1
        search_off = 0
        while True:
            pos = rom_data.find(leader_enc, search_off)
            if pos < 0:
                break
            # Scan forward 8 names from here to see if the 9th is RIVAL
            j = pos + len(leader_enc)
            for _ in range(7):   # skip 7 more names (total 8 LEADER entries)
                while j < len(rom_data) and rom_data[j] != eos:
                    j += 1
                j += 1  # skip eos
            if rom_data[j: j + len(rival_enc)] == rival_enc:
                table_start = pos
                break
            search_off = pos + 1

        if table_start < 0:
            # Fallback: anchor at RIVAL like before
            table_start = rom_data.find(rival_enc)
        if table_start < 0:
            return

        names = []
        i = table_start
        while i < len(rom_data) and len(names) < 80:
            chars = []
            while i < len(rom_data) and rom_data[i] != eos:
                b = rom_data[i]
                ch = charmap.get(b)
                if ch is not None:
                    chars.append(ch)
                elif b >= 0x80:
                    break
                i += 1
            name = ''.join(chars).strip()
            if i < len(rom_data) and rom_data[i] == eos:
                names.append(name)
                i += 1
            else:
                break

        if len(names) > 4:
            text_tables['trainer_classes'] = names

    @staticmethod
    def decode_encounters(self, map_group: int, map_number: int, current_rom: dict, text_tables: dict) -> str:
        """Decode Gen II wild encounters for a map identified by (group, map_number).
        Format: [group][map][morn_rate][day_rate][nite_rate][7×lv,sp morning][day][night] = 47B
        Species = Pokédex numbers (same as Gen 2 trainer data)."""
        if not current_rom:
            return ''
        g1off    = current_rom.get('gen1_offsets', {})
        table        = g1off.get('enc2_table_base', 0)
        kanto_table  = g1off.get('enc2_kanto_table_base', 0)
        if not table:
            return ''
        rom_data = bytes(current_rom.get('data') or b'')
        sp_list  = text_tables.get('species', [])

        def sp_name(dex):
            if 0 < dex < len(sp_list) and sp_list[dex]:
                return sp_list[dex]
            return f'sp#{dex}'

        # Scan both tables for matching entry (Johto table + Kanto table)
        for table_start in [t for t in [table, kanto_table] if t]:
            off = table_start
            while off + 47 <= len(rom_data):
                mg = rom_data[off]
                mn = rom_data[off + 1]
                if mg == 0xFF:
                    break
                if mg == map_group and mn == map_number:
                    morn_rate = rom_data[off + 2]
                    day_rate  = rom_data[off + 3]
                    nite_rate = rom_data[off + 4]
                    lines = [f'Map {map_group}/{map_number}']
                    for period_idx, (label, rate) in enumerate(
                            [('Morning', morn_rate), ('Day', day_rate), ('Night', nite_rate)]):
                        if rate == 0:
                            continue
                        lines.append(f'Grass ({label}):')
                        seen = {}
                        for slot in range(7):
                            base = off + 5 + period_idx * 14 + slot * 2
                            lv = rom_data[base]
                            sp = rom_data[base + 1]
                            name = sp_name(sp)
                            seen.setdefault(name, []).append(lv)
                        for name, lvs in seen.items():
                            lv_str = f'Lv. {min(lvs)}-{max(lvs)}' if min(lvs) != max(lvs) else f'Lv. {min(lvs)}'
                            lines.append(f'  {name:<20}{lv_str}')
                    return '\n'.join(lines)
                off += 47
        return ''

    def bootstrap_text(self, rom_data, region='US'):
        """Gen II text bootstrap — dex-order species, Gen II trainer classes."""
        charmap = self.CHARMAP_JP if region == 'JP' else self.CHARMAP_EN
        eos = self.EOS
        self.text_tables = {}

        candidates = self.scan_rom_text(rom_data, charmap, eos)
        for idx, table in enumerate(candidates):
            self.text_tables[idx] = table

        self._scan_gen1_species(rom_data, charmap, eos, self.text_tables,
                                anchor_pair=('BULBASAUR', 'IVYSAUR'),
                                max_entries=252, skip_reorder=True)
        self._scan_gen2_trainer_classes_en(rom_data, charmap, eos, self.text_tables)
        self._scan_gen1_items(rom_data, charmap, eos, self.text_tables)

        self._auto_detect_tables()
        self._map_text_tables()

    SCAN_TRAINER_CLASSES_EN = _scan_gen2_trainer_classes_en


"""Johto_remake.py: HeartGold/SoulSilver.

Inherits Sinnoh_pt (built on Platinum engine). Different a/x/x/x paths,
HGSS encounter format, Pokeathlon performance data.
"""
import struct
from Generations.Sinnoh_pt import Sinnoh_pt


class Johto_remake(Sinnoh_pt):
    """HeartGold/SoulSilver. Inherits Platinum engine, HGSS paths."""

    GAME_CODES = ('IPK', 'IPG')
    TITLES = ('POKÉMON HEARTGOLD', 'POKÉMON SOULSILVER')
    YEAR = 2010

    TEXT_PATH = 'a/0/2/7'
    PERSONAL_PATH = 'a/0/0/2'
    LEARNSET_PATH = 'a/0/3/3'
    EVOLUTION_PATH = 'a/0/3/4'
    MOVE_DATA_PATH = 'a/0/1/1'
    TRDATA_PATH = 'a/0/5/5'
    TRPOKE_PATH = 'a/0/5/6'
    ENCOUNTER_PATH = 'a/1/3/6'
    BATTLE_TOWER_POKEMON_PATH = 'a/2/0/3'
    BATTLE_TOWER_TRAINERS_PATH = 'a/2/0/2'
    ITEM_PATH = 'a/0/1/7'
    POKEATHLON_PATH = 'a/1/6/9'

    TEXT_SPECIES = 237
    TEXT_MOVES = 750
    TEXT_ITEMS = 222
    TEXT_ABILITIES = 720
    TEXT_TYPE_NAMES = 735
    TEXT_NATURES = 34
    TEXT_TRAINER_CLASSES = 730
    TEXT_TRAINER_NAMES = 729
    TEXT_LOCATIONS = 279


    @staticmethod
    def decode_encounters(self, data: bytes) -> dict:
        """Decode Gen IV HGSS encounter data (196 bytes).
        Header: 8 × u8 rates. Grass: 12 levels + 3×12 species (morn/day/night) + 4 sound species.
        Water: surf(5) + rocksmash(2) + oldrod(5) + goodrod(5) + superrod(5), each 4B/slot."""
        if len(data) != 196:
            return None

        # Header rates (u8 each)
        grass_rate = data[0]
        surf_rate = data[1]
        rock_smash_rate = data[2]
        old_rod_rate = data[3]
        good_rod_rate = data[4]
        super_rod_rate = data[5]

        result = {}

        # Grass: 12 levels at offset 8, then 3 species tables (morning/day/night)
        if grass_rate > 0:
            levels = [data[8 + i] for i in range(12)]
            tables = {}
            for t_idx, t_name in enumerate(["morning", "day", "night"]):
                base = 20 + t_idx * 24  # 12 species × 2 bytes = 24
                species = []
                for i in range(12):
                    sid = struct.unpack_from("<H", data, base + i * 2)[0]
                    if sid == 0:
                        continue
                    species.append({"species": get_text("species", sid), "level": levels[i]})
                if species:
                    tables[t_name] = species
            if tables:
                result["grass"] = tables
                result["grass_rate"] = grass_rate

        # Sound species at offset 92 (Hoenn Sound × 2, Sinnoh Sound × 2)
        sound_species = []
        for i in range(4):
            sid = struct.unpack_from("<H", data, 92 + i * 2)[0]
            if sid > 0:
                sound_species.append(get_text("species", sid))
        if sound_species:
            result["sound"] = {"hoenn": sound_species[:2], "sinnoh": sound_species[2:]}

        # Water helper: each slot is min_lv u8, max_lv u8, species u16 (4 bytes)
        def read_water(offset, count):
            entries = []
            for i in range(count):
                pos = offset + i * 4
                min_lv = data[pos]
                max_lv = data[pos + 1]
                species_id = struct.unpack_from("<H", data, pos + 2)[0]
                if species_id == 0:
                    continue
                lvl = f"{min_lv}-{max_lv}" if min_lv != max_lv else str(min_lv)
                entries.append({"species": get_text("species", species_id), "level": lvl})
            return entries

        if surf_rate > 0:
            surf = read_water(100, 5)
            if surf:
                result["surf"] = surf

        if rock_smash_rate > 0:
            rocks = read_water(120, 2)
            if rocks:
                result["rock_smash"] = rocks

        if old_rod_rate > 0:
            old = read_water(128, 5)
            if old:
                result["old_rod"] = old

        if good_rod_rate > 0:
            good = read_water(148, 5)
            if good:
                result["good_rod"] = good

        if super_rod_rate > 0:
            sup = read_water(168, 5)
            if sup:
                result["super_rod"] = sup

        return result if result else None

    POKEATHLON_STATS = ['Power', 'Speed', 'Jump', 'Stamina', 'Skill']

    # sPokeathlonPerformanceArcIdxs[] — species→NARC index mapping.
    # Formula research: Peter O. (peteroupc), The Ultimate Pokémon Center.
    # Archive: https://upcarchive.playker.info/0/upokecenter/content/pokemon-heartgold-and-soulsilver-pokeathlon-performance-and-aprijuice.html
    # maps species_id → base NARC file index. Game reads: file = arr[species] + form.
    # Built into a reverse map: narc_file → (species_id, form_label)
    def _build_pokeathlon_form_map():
        # Extracted from sPokeathlonPerformanceArcIdxs (494 entries, species 0-493)
        # Only entries where the species has >1 form need explicit mapping.
        # Form labels per species (index = form number):
        # Labels uppercase to match Gen 4 text style.
        # Leading '*' = full name override (no parenthetical — e.g. Spiky-eared Pichu).
        _FORM_LABELS = {
            172: ['', '*SPIKY-EARED PICHU'],
            201: list('ABCDEFGHIJKLMNOPQRSTUVWXYZ') + ['!', '?'],
            386: ['NORMAL FORME', 'ATTACK FORME', 'DEFENSE FORME', 'SPEED FORME'],
            412: ['PLANT CLOAK', 'SANDY CLOAK', 'TRASH CLOAK'],
            413: ['PLANT CLOAK', 'SANDY CLOAK', 'TRASH CLOAK'],
            422: ['WEST SEA', 'EAST SEA'],
            423: ['WEST SEA', 'EAST SEA'],
            479: ['', 'HEAT', 'WASH', 'FROST', 'FAN', 'MOW'],
            487: ['ALTERED FORME', 'ORIGIN FORME'],
            492: ['LAND FORME', 'SKY FORME'],
            493: ['NORMAL','FIGHTING','FLYING','POISON','GROUND','ROCK','BUG','GHOST',
                  'STEEL','FIRE','WATER','GRASS','ELECTRIC','PSYCHIC','ICE','DRAGON','DARK','???'],
        }
        # Base indices extracted from the decomp array (only species with >1 form listed;
        # all others are sequential: arr[sp] = sp-1 adjusted for gaps).
        # We build the reverse map by replaying the full arr[] logic.
        # Jumps from the script analysis (species → (base_idx, n_forms)):
        _FORM_SPECIES = {
            172: (171, 2), 201: (201, 28), 386: (413, 4),
            412: (442, 3), 413: (445, 3), 422: (456, 2), 423: (458, 2),
            479: (515, 6), 487: (528, 2), 492: (534, 2), 493: (536, 18),
        }
        result = {}
        # Build full reverse map: reconstruct arr[] by simulating the sequential + form offsets
        narc_i = 0
        for sp in range(1, 494):
            if sp in _FORM_SPECIES:
                base_idx, n_forms = _FORM_SPECIES[sp]
                labels = _FORM_LABELS.get(sp, [])
                for f in range(n_forms):
                    label = labels[f] if f < len(labels) else f'Form {f}'
                    result[base_idx + f] = (sp, label)
                narc_i = base_idx + n_forms
            else:
                # Sequential: use the known narc_i counter
                result[narc_i] = (sp, '')
                narc_i += 1
        return result

    POKEATHLON_FORM_MAP = _build_pokeathlon_form_map()


    @staticmethod
    def decode_pokeathlon(self, data: bytes, text_tables: dict, file_idx: int = 0):
        """Decode Pokéathlon performance stats (HGSS only). Returns positional text."""
        if len(data) != 20:
            return None

        species_list = text_tables.get('species', [])
        entry = Johto_remake.POKEATHLON_FORM_MAP.get(file_idx)
        if entry:
            sp_id, form_label = entry
            sp_name = species_list[sp_id] if sp_id < len(species_list) else f"#{sp_id}"
            if form_label.startswith('*'):
                title = form_label[1:]          # full name override
            elif form_label:
                title = f"{sp_name} ({form_label})"
            else:
                title = sp_name
            sp_display = f"#{sp_id}"
        else:
            sp_id = file_idx + 1
            sp_name = species_list[sp_id] if sp_id < len(species_list) else f"#{sp_id}"
            title, sp_display = sp_name, f"#{sp_id}"

        parts = []
        for i, stat_name in enumerate(Johto_remake.POKEATHLON_STATS):
            base = data[i] + 1
            mn = data[9 + i * 2] + 1
            mx = data[10 + i * 2] + 1
            if mn == base == mx:
                parts.append(f"{stat_name}: {base}★")
            elif mn == base:
                parts.append(f"{stat_name}: {base}-{mx}★")
            else:
                parts.append(f"{stat_name}: {mn}/{base}/{mx}★")

        lines = [f"{title} ({sp_display}) — Pokéathlon"]
        lines.append(" | ".join(parts))
        return "\n".join(lines)


    def _format_encounter_hgss(self, decoded, file_idx):
        """Format HGSS encounter data as template text."""
        lines = []
        grass = decoded.get('grass', {})
        if grass and isinstance(grass, dict) and 'morning' in grass:
            times = {}
            for t in ['morning', 'day', 'night']:
                entries = grass.get(t, [])
                times[t] = {}
                for i, entry in enumerate(entries):
                    name = entry['species']
                    rate = self.GRASS_SLOT_RATES[i] if i < len(GRASS_SLOT_RATES) else 0
                    if name not in times[t]:
                        times[t][name] = {'rate': 0, 'levels': set()}
                    times[t][name]['rate'] += rate
                    times[t][name]['levels'].add(entry['level'])
            all_species = set()
            for td in times.values():
                all_species.update(td.keys())
            species_info = []
            for sp in all_species:
                m_rate = times['morning'].get(sp, {}).get('rate', 0)
                d_rate = times['day'].get(sp, {}).get('rate', 0)
                n_rate = times['night'].get(sp, {}).get('rate', 0)
                all_levels = set()
                for t in ['morning', 'day', 'night']:
                    if sp in times[t]:
                        all_levels.update(times[t][sp]['levels'])
                levels = sorted(all_levels)
                lv = f"Lv{levels[0]}" if len(levels) <= 1 else f"Lv{levels[0]}-{levels[-1]}"
                if m_rate == d_rate == n_rate and m_rate > 0:
                    rate_str = f"{m_rate}%"
                else:
                    rate_groups = {}
                    for rate, tname in [(m_rate, 'Morning'), (d_rate, 'Day'), (n_rate, 'Night')]:
                        if rate > 0:
                            rate_groups.setdefault(rate, []).append(tname)
                    parts = []
                    for rate, tnames in sorted(rate_groups.items(), reverse=True):
                        parts.append(f"{rate}% ({', '.join(tnames)})")
                    rate_str = " / ".join(parts)
                species_info.append({'species': sp, 'rate_str': rate_str, 'level': lv, 'sort_key': max(m_rate, d_rate, n_rate)})
            species_info.sort(key=lambda x: -x['sort_key'])
            lines.append("Grass (Default):")
            for si in species_info:
                lv = si['level'].replace('Lv', 'Lv. ')
                lines.append(f"  {si['species']:<20}{lv:<12}{si['rate_str']}")

        water_sections = [
            ('surf', 'Surf (Default)'), ('rock_smash', 'Rock Smash'),
            ('old_rod', 'Fishing (Old Rod)'), ('good_rod', 'Fishing (Good Rod)'),
            ('super_rod', 'Fishing (Super Rod)'),
        ]
        for key, header in water_sections:
            entries = decoded.get(key, [])
            if entries:
                section = self._format_section(entries, self.WATER_SLOT_RATES, header)
                if section:
                    lines.append(section)

        sound = decoded.get('sound', {})
        if sound:
            hoenn = sound.get('hoenn', [])
            sinnoh = sound.get('sinnoh', [])
            if hoenn:
                lines.append(f"\nGrass (Hoenn Sound):\n  {', '.join(hoenn)}")
            if sinnoh:
                lines.append(f"\nGrass (Sinnoh Sound):\n  {', '.join(sinnoh)}")

        return "\n".join(lines).strip() if lines else None




    def discover_enc_loc(self, arm9):
        """HGSS: scan ARM9 sMapHeaders (540x24B). byte0=enc file, byte18=mapsec.
        Anchor: map33=Route29 (enc=1,mapsec=0xB1), map34=Route30 (enc=3,mapsec=0xB2)."""
        enc_map = {}
        pos = 0
        while pos < len(arm9) - 24 * 5:
            X = arm9.find(b'\x01', pos)
            if X < 0: break
            if (X + 42 < len(arm9) and arm9[X+18] == 0xB1 and
                    arm9[X+24] == 3 and arm9[X+42] == 0xB2):
                T = X - 33 * 24
                if T >= 0 and arm9[T] == 0xFF and arm9[T+18] == 0:
                    for i in range(540):
                        s = T + i * 24
                        if s + 24 > len(arm9): break
                        ef, ms = arm9[s], arm9[s + 18]
                        if ef != 0xFF and ms > 0:
                            enc_map[ef] = ms
                    if len(enc_map) >= 100:
                        return enc_map
                    enc_map = {}
            pos = X + 1
        return {}


"""Gen III (Ruby/Sapphire/Emerald/FireRed/LeafGreen) - charmap, scanners, decoders."""
import struct


# ============================================================
# # _GEN3_CHARMAP_EN + section header
# server.py lines 989-1009
# ============================================================

# ============ Gen III/II/I Text Decoders ============

# Gen III (GBA) character map — English FireRed/Ruby/Emerald
# Empirically verified from FireRed (USA): A=0xBB, confirmed via BULBASAUR at 0x245EEB.
# Bulbapedia listed A=0xC1 but that was wrong for this ROM version.
# EOS=0xFF. 0x00 is null padding between fixed-width entries (NOT space).
# Space within strings (e.g. "MASTER BALL") is 0x00 contextually — we skip leading 0x00s.
_GEN3_CHARMAP_EN: dict = {}
for _i, _c in enumerate('ABCDEFGHIJKLMNOPQRSTUVWXYZ'):
    _GEN3_CHARMAP_EN[0xBB + _i] = _c
for _i, _c in enumerate('abcdefghijklmnopqrstuvwxyz'):
    _GEN3_CHARMAP_EN[0xD5 + _i] = _c
for _i, _c in enumerate('0123456789'):
    _GEN3_CHARMAP_EN[0xA1 + _i] = _c
_GEN3_CHARMAP_EN.update({
    0x00: ' ',   # space within strings; skip leading 0x00 (entry padding) in scanner
    0x1B: 'é',   # POKé BALL etc.
    0xAB: '!', 0xAC: '?', 0xAD: '.', 0xAE: '-', 0xB8: ',',
    0xB2: "'", 0xB5: '♀', 0xB6: '♂',
    0x53: '', 0x54: '',  # control-code prefixes in some class names (silently skipped)
})

# ============================================================
# # _GEN3_EOS
# server.py lines 1125-1125
# ============================================================

_GEN3_EOS = 0xFF

# ============================================================
# # _scan_gen3_abilities
# server.py lines 1224-1270
# ============================================================

def _scan_gen3_abilities(rom_data: bytes, charmap: dict, eos: int, text_tables: dict):
    """Scan Gen III ability names starting from STENCH.

    Ability names are all-uppercase and short (≤12 chars).
    Descriptions are mixed-case and longer — we stop when we hit those.
    Prepends a blank entry at index 0 (ability 0 = no ability).
    Populates an integer-keyed entry in text_tables.
    """
    if 'abilities' in text_tables:
        return  # already found by fingerprinting

    reverse = {v: k for k, v in charmap.items() if isinstance(v, str) and len(v) == 1}
    try:
        stench_bytes = bytes([reverse[c] for c in 'STENCH']) + bytes([eos])
    except KeyError:
        return

    idx = rom_data.find(stench_bytes)
    if idx < 0:
        return

    abilities = ['']  # index 0 = no ability (blank)
    i = idx
    while i < len(rom_data):
        while i < len(rom_data) and rom_data[i] == 0x00:
            i += 1
        j = i
        chars = []
        while j < len(rom_data) and rom_data[j] != eos and j < i + 15:
            ch = charmap.get(rom_data[j])
            if ch is None:
                break
            chars.append(ch)
            j += 1
        name = ''.join(chars).rstrip()
        if rom_data[j] == eos and 2 <= len(name) <= 13:
            # Accept only if looks like an ability name (no lowercase, not a sentence)
            if name == name.upper():
                abilities.append(name)
                i = j + 1
                continue
        break  # hit descriptions or end

    if len(abilities) > 5:
        next_key = max((k for k in text_tables if isinstance(k, int)), default=-1) + 1
        text_tables[next_key] = abilities

# ============================================================
# # _scan_gen3_species
# server.py lines 1273-1325
# ============================================================

def _scan_gen3_species(rom_data: bytes, charmap: dict, eos: int, text_tables: dict):
    """Scan Gen III species names starting from BULBASAUR.

    Species are fixed-width name slots. Find BULBASAUR, verify IVYSAUR follows
    at the right distance (to get slot size), then read forward.
    Prepends a dummy at index 0. Sets text_tables['species'] directly.
    """
    if 'species' in text_tables:
        return

    reverse = {v: k for k, v in charmap.items() if isinstance(v, str) and len(v) == 1}
    try:
        bulb = bytes([reverse[c] for c in 'BULBASAUR']) + bytes([eos])
        ivy  = bytes([reverse[c] for c in 'IVYSAUR'])
    except KeyError:
        return

    # Find BULBASAUR followed by IVYSAUR at the right slot distance
    search = 0
    while True:
        pos = rom_data.find(bulb, search)
        if pos < 0:
            return
        # Slot size = distance from BULBASAUR start to next non-padding byte
        nxt = pos + len(bulb)
        while nxt < len(rom_data) and rom_data[nxt] in (0x00, eos):
            nxt += 1
        slot = nxt - pos
        if 2 <= slot <= 20 and rom_data[nxt: nxt + len(ivy)] == ivy:
            break
        search = pos + 1

    # Read backwards one slot for dummy (index 0 = Missingno)
    start = max(0, pos - slot)
    names = []
    i = start
    while i < len(rom_data) and len(names) < 500:
        j, chars = i, []
        while j < i + slot and j < len(rom_data) and rom_data[j] != eos:
            ch = charmap.get(rom_data[j])
            if ch is None:
                break
            chars.append(ch)
            j += 1
        name = ''.join(chars).rstrip()
        if len(names) > 10 and not name:
            break
        names.append(name)
        i += slot

    if len(names) > 10:
        text_tables['species'] = names

# ============================================================
# # _scan_gen3_trainer_names
# server.py lines 1762-1798
# ============================================================

def _scan_gen3_trainer_names(rom_data: bytes, charmap: dict, eos: int, text_tables: dict):
    """Scan Gen III ROM for trainer structs and extract names into text_tables['trainer_names'].

    Trainer struct (40 bytes): flags(1), class(1), music(1), sprite(1), name(12), ...
    party_count(u32 at +32), party_ptr(u32 at +36).
    Validates each candidate by checking flags 0-3, class 1-199, count 1-6, valid GBA ptr.
    """
    if 'trainer_names' in text_tables:
        return

    import struct as _struct
    names_by_offset = {}
    for i in range(0, len(rom_data) - 40, 4):
        if rom_data[i] > 3 or rom_data[i + 1] == 0 or rom_data[i + 1] >= 200:
            continue
        count = _struct.unpack_from('<I', rom_data, i + 32)[0]
        if not (1 <= count <= 6):
            continue
        ptr = _struct.unpack_from('<I', rom_data, i + 36)[0]
        if not (0x08000000 <= ptr <= 0x0AFFFFFF):
            continue
        j, chars = i + 4, []
        while j < i + 16 and rom_data[j] != eos:
            ch = charmap.get(rom_data[j])
            if ch is None:
                break
            chars.append(ch)
            j += 1
        name = ''.join(chars).strip()
        if len(name) >= 2 and name == name.upper():
            names_by_offset[i] = name

    if len(names_by_offset) > 10:
        sorted_entries = sorted(names_by_offset.items())
        text_tables['trainer_names']   = [n for _, n in sorted_entries]
        text_tables['trainer_offsets'] = [off for off, _ in sorted_entries]

# ============================================================
# # _scan_gen3_items
# server.py lines 1802-1842
# ============================================================

def _scan_gen3_items(rom_data: bytes, charmap: dict, eos: int, text_tables: dict):
    """Scan Gen III item table (44-byte structs, name in first 14 bytes).

    Finds MASTER BALL to anchor the table, then reads fixed-stride entries.
    Populates text_tables['items'] directly.
    """
    reverse = {v: k for k, v in charmap.items() if isinstance(v, str) and len(v) == 1}
    try:
        mb_bytes = bytes([reverse[c] for c in 'MASTER BALL']) + bytes([eos])
    except KeyError:
        return

    idx = rom_data.find(mb_bytes)
    if idx < 0:
        return

    ITEM_STRUCT = 44
    NAME_LEN = 14
    items = []
    offset = idx
    while offset + ITEM_STRUCT <= len(rom_data):
        name_bytes = rom_data[offset:offset + NAME_LEN]
        name = ''
        for b in name_bytes:
            if b == eos:
                break
            ch = charmap.get(b)
            if ch is None:
                break
            name += ch
        name = name.strip()
        if not name:
            break
        items.append(name)
        offset += ITEM_STRUCT

    if items:
        # Add as integer key so auto_detect_tables can fingerprint it
        next_key = max((k for k in text_tables if isinstance(k, int)), default=-1) + 1
        text_tables[next_key] = items

# ============================================================
# # decode_gen3_trainer
# server.py lines 2589-2647
# ============================================================

def decode_gen3_trainer(header: bytes, party_data: bytes, party_flags: int) -> dict:
    """Decode Gen III trainer header (40 bytes) and party data.

    party_flags: bit 0 = has custom moves, bit 1 = has held item
    """
    import struct
    if len(header) < 40:
        return {}

    flags = header[0]
    trainer_class = header[1]
    name_bytes = header[4:16]
    items = [struct.unpack_from('<H', header, 16 + i*2)[0] for i in range(4)]
    is_double = struct.unpack_from('<I', header, 24)[0]
    ai_flags = struct.unpack_from('<I', header, 28)[0]
    party_count = struct.unpack_from('<I', header, 32)[0]

    # Decode name using Gen III charmap
    name = ''.join(_GEN3_CHARMAP_EN.get(b, '') for b in name_bytes).strip()

    # Party member size depends on flags
    has_moves = bool(flags & 1)
    has_item = bool(flags & 2)
    if has_moves and has_item:
        member_size = 18  # iv(2)+level(2)+species(2)+item(2)+moves(8)+pad(2)
    elif has_moves:
        member_size = 16  # iv(2)+level(2)+species(2)+moves(8)+pad(2)
    elif has_item:
        member_size = 8   # iv(2)+level(2)+species(2)+item(2)
    else:
        member_size = 8   # iv(2)+level(2)+species(2)+pad(2)

    party = []
    for i in range(min(party_count, 6)):
        off = i * member_size
        if off + member_size > len(party_data):
            break
        m = party_data[off:off+member_size]
        iv = struct.unpack_from('<H', m, 0)[0]
        level = struct.unpack_from('<H', m, 2)[0]
        species = struct.unpack_from('<H', m, 4)[0]
        member = {'species': species, 'level': level, 'iv': iv}
        pos = 6
        if has_item:
            member['item'] = struct.unpack_from('<H', m, pos)[0]
            pos += 2
        if has_moves:
            moves = [struct.unpack_from('<H', m, pos + j*2)[0] for j in range(4)]
            member['moves'] = [mv for mv in moves if mv > 0]
        party.append(member)

    return {
        'trainer_class': trainer_class,
        'name': name,
        'ai_flags': ai_flags,
        'is_double': bool(is_double),
        'battle_items': [it for it in items if it > 0],
        'party': party,
    }

# ============================================================
# # _discover_gen3_tables
# server.py lines 3223-3334
# ============================================================

def _discover_gen3_tables(current_rom: dict):
    """Find personal/move/trainer table offsets in a GBA ROM by searching for known anchors.

    Same philosophy as _discover_tm_table() — the data finds itself.
    Results stored in current_rom['gen3_offsets'].
    """
    if not current_rom or current_rom['type'] != 'gba':
        return None
    rom_data = bytes(current_rom.get('data') or b'')
    if not rom_data:
        return None

    offsets = {}

    # ── Personal data: find via Bulbasaur's unique stat+type+catch signature ──
    # HP=45, Atk=49, Def=49, Spe=45, SpA=65, SpD=65, Type1=12(Grass), Type2=3(Poison), Catch=45
    bulb_sig = bytes([45, 49, 49, 45, 65, 65, 12, 3, 45])
    idx = rom_data.find(bulb_sig)
    if idx >= 0:
        # Bulbasaur is species index 1 → base = found - 1 * 28
        offsets['personal_base'] = idx - 28
        offsets['personal_size'] = 28

    # ── Move data: find via Pound's signature with blank entry before it ──
    # effect=0, power=40, type=0(Normal), acc=100, pp=35
    pound_sig = bytes([0, 40, 0, 100, 35])
    idx = rom_data.find(pound_sig)
    while idx >= 0:
        if all(b == 0 for b in rom_data[idx - 12: idx]):
            offsets['move_base'] = idx - 12  # entry 0 = blank, entry 1 = Pound
            offsets['move_size'] = 12
            break
        idx = rom_data.find(pound_sig, idx + 1)

    # ── Learnset pointer table: find via species 1 (Bulbasaur) lv1 Tackle ──
    # Table is an array of GBA pointers, one per species. Each points to u16 pairs
    # (level << 9) | move_id, terminated by 0x0000.
    tackle_lv1 = struct.pack('<H', (1 << 9) | 33)  # 0x0221
    for i in range(0, min(len(rom_data) - 8, 0x800000), 4):
        ptr = struct.unpack_from('<I', rom_data, i)[0]
        if not (0x08000000 <= ptr <= 0x0A000000):
            continue
        off = ptr - 0x08000000
        if off + 2 <= len(rom_data) and rom_data[off:off+2] == tackle_lv1:
            ptr2 = struct.unpack_from('<I', rom_data, i + 4)[0]
            if 0x08000000 <= ptr2 <= 0x0A000000:
                off2 = ptr2 - 0x08000000
                if off2 + 2 <= len(rom_data) and rom_data[off2:off2+2] == tackle_lv1:
                    offsets['learnset_ptr_table'] = i - 4  # entry 0 = dummy
                    break

    # ── Evolution table: find via Bulbasaur (sp1) level 16 → Ivysaur (sp2) ──
    # Struct: 5 slots × {u16 method, u16 param, u16 target, u16 pad} = 40B per species
    # Method 4 = EVO_LEVEL. Verify Ivysaur at +40 also has its evo (level 32 → Venusaur).
    bulb_evo = struct.pack('<HHHH', 4, 16, 2, 0)
    idx = rom_data.find(bulb_evo)
    while idx >= 0:
        if (all(b == 0 for b in rom_data[idx+8:idx+40]) and
                rom_data[idx+40:idx+48] == struct.pack('<HHHH', 4, 32, 3, 0)):
            offsets['evo_base'] = idx - 40  # species 0 (dummy) precedes Bulbasaur
            break
        idx = rom_data.find(bulb_evo, idx + 1)

    # ── Wild encounter table: WildPokemonHeader[] terminated by {0xFF, 0xFF, 0, 0, NULL×4}
    # Each entry: mapGroup(u8) mapNum(u8) pad(2) landPtr(u32) waterPtr(u32) rockPtr(u32) fishPtr(u32)
    # Sentinel has 0xFF at bytes 0-1 and all remaining bytes 0.
    sentinel_20 = bytes([0xFF, 0xFF, 0x00, 0x00]) + bytes(16)
    si = 0
    while si < len(rom_data) - 20:
        si = rom_data.find(sentinel_20, si)
        if si < 0: break
        if si >= 20:
            prev = rom_data[si-20:si]
            mg, mn = prev[0], prev[1]
            if mg < 50 and mn < 200:
                for poff in (4, 8, 12, 16):
                    ptr = struct.unpack_from('<I', prev, poff)[0]
                    if 0x08000000 <= ptr <= 0x0AFFFFFF:
                        # Walk backward to find table start
                        tbl_start = si
                        while tbl_start >= 20:
                            e = rom_data[tbl_start-20:tbl_start]
                            emg, emn = e[0], e[1]
                            if emg > 50 or emn > 200: break
                            if not any(0x08000000 <= struct.unpack_from('<I',e,o)[0] <= 0x0AFFFFFF
                                       for o in (4,8,12,16)): break
                            tbl_start -= 20
                        offsets['enc_table_offset'] = tbl_start
                        break
        if 'enc_table_offset' in offsets: break
        si += 1

    # ── Item table: find via MASTER BALL name (same anchor as _scan_gen3_items) ──
    # Struct: 44B. name(14B) | index(u16) | price(u16) | holdEffect(u8) | holdParam(u8)
    # | descPtr(u32) | importance(u8) | pad | pocket(u8) | type(u8) | ...
    # MASTER BALL = item 1, so table_base = anchor - 44 (item 0 is blank entry).
    reverse = {v: k for k, v in _GEN3_CHARMAP_EN.items() if isinstance(v, str) and len(v) == 1}
    try:
        mb_bytes = bytes([reverse[c] for c in 'MASTER BALL']) + bytes([_GEN3_EOS])
        mb_idx = rom_data.find(mb_bytes)
        if mb_idx >= 44:
            offsets['item_base'] = mb_idx - 44  # item 0 = blank entry before Master Ball
    except (KeyError, TypeError):
        pass

    # ── Trainer data: no global base needed — search by name at query time ──
    offsets['trainer_size'] = 40

    if offsets:
        current_rom['gen3_offsets'] = offsets

    return offsets if offsets else None

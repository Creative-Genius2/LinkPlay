"""xoleon — Gen IV/V Pokémon text decryption."""
import struct


# ============ Gen V ============

# Special character substitutions (packed words for common game terms)
_GEN5_CHARMAP = {
    0x2467: 'Mr.', 0x2468: 'Ms.', 0x2469: 'Mrs.',
    0x246D: 'the', 0x246E: 'The',
    0x2486: 'Poké', 0x2487: 'mon',
}

def derive_gen5_mult(species_data: bytes) -> int:
    """Derive XOR multiplier from species file entry 1 ('Bulbasaur').
    Seed for entry 1 = (1+3)*MULT = 4*MULT. XOR encrypted[0] with 'B' (0x0042) gives 4*MULT.
    """
    if len(species_data) < 16:
        return 0x2983
    entry_count = struct.unpack_from('<H', species_data, 2)[0]
    section_offset = struct.unpack_from('<I', species_data, 0x0C)[0]
    if entry_count < 2 or section_offset + 4 > len(species_data):
        return 0x2983
    # Read entry 1 from entry table
    entry_pos = section_offset + 4 + (1 * 8)
    if entry_pos + 8 > len(species_data):
        return 0x2983
    offset = struct.unpack_from('<I', species_data, entry_pos)[0]
    str_offset = section_offset + offset
    if str_offset + 2 > len(species_data):
        return 0x2983
    encrypted_0 = struct.unpack_from('<H', species_data, str_offset)[0]
    four_mult = encrypted_0 ^ 0x0042
    return (four_mult // 4) & 0xFFFF


def decode_gen5_text(data: bytes, mult: int = 0x2983) -> list:
    """Decode a Gen V encrypted text file. MULT derived once from NARC, passed in.
    Seed = (entry_index + 3) * mult, key advances via ROL3.
    Control codes (0xFFFE) consumed properly: type(u16), param_count(u16), params(u16*n).
    """
    if len(data) < 16:
        return []

    entry_count = struct.unpack_from('<H', data, 2)[0]
    section_offset = struct.unpack_from('<I', data, 0x0C)[0]

    if entry_count == 0 or entry_count > 10000:
        return []
    if section_offset + 4 > len(data):
        return []

    entry_table_start = section_offset + 4
    strings = []

    for i in range(entry_count):
        entry_pos = entry_table_start + (i * 8)
        if entry_pos + 8 > len(data):
            break
        offset = struct.unpack_from('<I', data, entry_pos)[0]
        char_count = struct.unpack_from('<H', data, entry_pos + 4)[0]

        str_offset = section_offset + offset
        key = ((i + 3) * mult) & 0xFFFF

        # Decrypt all u16 values for this entry
        vals = []
        for j in range(char_count):
            char_pos = str_offset + (j * 2)
            if char_pos + 2 > len(data):
                break
            enc = struct.unpack_from('<H', data, char_pos)[0]
            dec = enc ^ key
            key = ((key << 3) | (key >> 13)) & 0xFFFF
            vals.append(dec)

        # F100 flag = 9-bit packed text (LSB-first, 0x1FF terminator)
        if vals and vals[0] == 0xF100:
            bits = 0
            nbits = 0
            for w in vals[1:]:
                if w == 0xFFFF:
                    break
                bits |= (w << nbits)
                nbits += 16
            chars = []
            while nbits >= 9:
                c = bits & 0x1FF
                bits >>= 9
                nbits -= 9
                if c == 0x1FF:
                    break
                try:
                    chars.append(chr(c) if c >= 0x20 else f'\\x{c:04X}')
                except (ValueError, OverflowError):
                    chars.append(f'\\x{c:04X}')
            strings.append(''.join(chars))
            continue

        # Normal text: parse control codes and characters
        chars = []
        j = 0
        while j < len(vals):
            dec = vals[j]
            j += 1

            if dec == 0xFFFF:
                break
            elif dec == 0xFFFE:
                ctrl_type = vals[j] if j < len(vals) else 0
                j += 1
                param_count = vals[j] if j < len(vals) else 0
                j += 1
                j += param_count  # skip params
                if ctrl_type == 0x0000 or ctrl_type & 0xFF00 == 0x0000:
                    chars.append('\n')
                elif ctrl_type & 0xFF00 == 0x0100:
                    chars.append('[var]')
                elif ctrl_type & 0xFF00 in (0xBE00, 0xFF00):
                    pass  # formatting, skip
                else:
                    chars.append(f'[ctrl:{ctrl_type:04X}]')
            elif dec in _GEN5_CHARMAP:
                chars.append(_GEN5_CHARMAP[dec])
            else:
                try:
                    chars.append(chr(dec))
                except (ValueError, OverflowError):
                    chars.append(f'\\x{dec:04X}')

        strings.append(''.join(chars))

    return strings


# ============ Gen IV ============

# Gen IV complete character map
# Based on Bulbapedia: https://bulbapedia.bulbagarden.net/wiki/Character_encoding_(Generation_IV)

# Hiragana (0x0001-0x0051) — sequential Unicode U+3041+ with tail overrides for archaic skips
_GEN4_HIRAGANA = {i: chr(0x3040 + i) for i in range(0x01, 0x4E)}
_GEN4_HIRAGANA.update({0x4E: 'わ', 0x4F: 'を', 0x50: 'ん', 0x51: 'ゔ'})

# Katakana (0x0052-0x00A1) — sequential Unicode U+30A1+ with tail overrides
_GEN4_KATAKANA = {i: chr(0x304F + i) for i in range(0x52, 0x9F)}
_GEN4_KATAKANA.update({0x9F: 'ワ', 0xA0: 'ヲ', 0xA1: 'ン'})

# Fullwidth symbols (0x00E0-0x011F)
_GEN4_FULLWIDTH_SYMBOLS = {
    0x00E1: '！', 0x00E2: '？', 0x00E3: '、', 0x00E4: '。', 0x00E5: '…',
    0x00E6: '・', 0x00E7: '／', 0x00E8: '「', 0x00E9: '」', 0x00EA: '『',
    0x00EB: '』', 0x00EC: '（', 0x00ED: '）', 0x00EE: '♂', 0x00EF: '♀',
    0x00F0: '＋', 0x00F1: 'ー', 0x00F2: '×', 0x00F3: '÷', 0x00F4: '＝',
    0x00F5: '～', 0x00F6: '：', 0x00F7: '；', 0x00F8: '．', 0x00F9: '，',
    0x00FA: '♠', 0x00FB: '♣', 0x00FC: '♥', 0x00FD: '♦', 0x00FE: '★',
    0x00FF: '◎', 0x0100: '○', 0x0101: '□', 0x0102: '△', 0x0103: '◇',
    0x0104: '＠', 0x0105: '♪', 0x0106: '％', 0x0107: '☀', 0x0108: '☁',
    0x0109: '☂', 0x010A: '☃', 0x0111: '円', 0x0118: '←', 0x0119: '↑',
    0x011A: '↓', 0x011B: '→', 0x011C: '►',
}

# Halfwidth special characters
# Positions confirmed from game data (space at 0x01DE, etc.)
_GEN4_SPECIAL = {
    # Inverted punctuation
    0x01A9: '\u00a1', 0x01AA: '\u00bf',
    # Punctuation and symbols
    0x01AC: '!', 0x01AD: '?', 0x01AE: ',', 0x01AF: '.',
    0x01B0: '\u2026', 0x01B1: '\uff65', 0x01B2: '/', 0x01B3: '\u2018',
    0x01B4: '\u2019', 0x01B5: '\u201C', 0x01B6: '\u201D', 0x01B7: '\u201e',
    0x01B8: '\u00ab', 0x01B9: '\u00bb', 0x01BA: '(', 0x01BB: ')',
    0x01BC: '\u2642', 0x01BD: '\u2640', 0x01BE: '+', 0x01BF: '-',
    # More symbols
    0x01C0: '*', 0x01C1: '#', 0x01C2: '=', 0x01C3: '&',
    0x01C4: '~', 0x01C5: ':', 0x01C6: ';', 0x01C7: '\u2660',
    0x01C8: '\u2663', 0x01C9: '\u2665', 0x01CA: '\u2666', 0x01CB: '\u2605',
    0x01CC: '\u25ce', 0x01CD: '\u25cb', 0x01CE: '\u25a1', 0x01CF: '\u25b3',
    0x01D0: '\u25c7', 0x01D1: '@', 0x01D2: '\u266a', 0x01D3: '%',
    0x01D4: '\u2600', 0x01D5: '\u2601', 0x01D6: '\u2602', 0x01D7: '\u2603',
    0x01DE: ' ', 0x01DF: 'e',  # Space and lowercase e (confirmed from game data)
    # Extended characters
    0x01E0: 'PK', 0x01E1: 'MN', 0x01E4: '\u00b0', 0x01E5: '_',
    0x01E6: '\uff3f', 0x01E7: '\u2024', 0x01E8: '\u2025',
}

def _get_gen4_char(c: int) -> str:
    """Get Gen IV character by code point.
    Halfwidth Latin block (used by English/EU ROMs):
      0x0121-0x012A = 0-9
      0x012B-0x0144 = A-Z
      0x0145-0x015E = a-z
    Kana blocks cover 0x0001-0x00A1.
    """
    if c == 0x0000:
        return ' '
    elif c in _GEN4_HIRAGANA:
        return _GEN4_HIRAGANA[c]
    elif c in _GEN4_KATAKANA:
        return _GEN4_KATAKANA[c]
    elif 0x00A2 <= c <= 0x00AB:
        return chr(ord('0') + c - 0x00A2)
    elif 0x00AC <= c <= 0x00C5:
        return chr(ord('A') + c - 0x00AC)
    elif 0x00C6 <= c <= 0x00DF:
        return chr(ord('a') + c - 0x00C6)
    elif c in _GEN4_FULLWIDTH_SYMBOLS:
        return _GEN4_FULLWIDTH_SYMBOLS[c]
    elif 0x0121 <= c <= 0x012A:
        return chr(ord('0') + c - 0x0121)
    elif 0x012B <= c <= 0x0144:
        return chr(ord('A') + c - 0x012B)
    elif 0x0145 <= c <= 0x015E:
        return chr(ord('a') + c - 0x0145)
    elif 0x015F <= c <= 0x019E:
        ACCENTED = "ÀÁÂÃÄÅÆÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖרÙÚÛÜÝÞßàáâãäåæçèéêëìíîïðñòóôõö÷øùúûüýþÿ"
        idx = c - 0x015F
        return ACCENTED[idx] if idx < len(ACCENTED) else '?'
    elif 0x019F <= c <= 0x01AB:
        # Extended Latin: Œ œ Ş ş ª º er re r ¡ ¿
        extended = ['Œ', 'œ', 'Ş', 'ş', 'ª', 'º', 'er', 're', 'r', '', '¡', '¿', '!']
        idx = c - 0x019F
        return extended[idx] if idx < len(extended) else '?'
    elif c in _GEN4_SPECIAL:
        return _GEN4_SPECIAL[c]
    elif c == 0xFFFE or c == 0xE000:
        return '\n'
    elif c == 0xFFFF or c == 0x01FF:
        return ''
    else:
        return '?'


def decode_gen4_text(data: bytes) -> list:
    """Decode Gen IV (DPPt/HGSS) text file.
    Format: u16 num_entries, u16 seed, encrypted entry table, encrypted strings.
    Entry table XOR: rolling key from seed * 0x2FD, advancing +0x493D per u16.
    String XOR: key = 0x91BD3 * (entry + 1) & 0xFFFF, advancing +0x493D per u16.
    0xF100 flag marks 9-bit packed text (15-bit word boundaries).
    """
    if len(data) < 4:
        return []

    num_entries = struct.unpack_from('<H', data, 0)[0]
    seed = struct.unpack_from('<H', data, 2)[0]

    if num_entries == 0 or num_entries > 10000:
        return []

    table_end = 4 + num_entries * 8
    if table_end > len(data):
        return []

    # Decrypt entry table: offset(u32) + length(u32) per entry
    # seed32 = (key * 765 * (i+1)) & 0xFFFF, replicated: seed32 |= seed32 << 16
    base_key = (seed * 0x2FD) & 0xFFFF
    entry_data = bytearray(data[4:table_end])
    entries = []
    for i in range(num_entries):
        key16 = (base_key * (i + 1)) & 0xFFFF
        seed32 = key16 | (key16 << 16)
        off = i * 8
        offset = struct.unpack_from('<I', entry_data, off)[0] ^ seed32
        charcount = struct.unpack_from('<I', entry_data, off + 4)[0] ^ seed32
        entries.append((offset, charcount))

    strings = []
    for i, (offset, length) in enumerate(entries):
        if length == 0 or offset + length * 2 > len(data):
            strings.append("")
            continue

        # Per-string decryption key
        key = ((i + 1) * 0x91BD3) & 0xFFFF
        vals = []
        for j in range(length):
            pos = offset + j * 2
            if pos + 2 > len(data):
                break
            enc = struct.unpack_from('<H', data, pos)[0]
            dec = (enc ^ key) & 0xFFFF
            key = (key + 0x493D) & 0xFFFF
            vals.append(dec)

        # Check for 0xF100 packed text (trainer names)
        # Algorithm from pret decomp (String_ConcatTrainerName):
        # Each u16 word contributes only 15 bits. 9-bit chars are extracted
        # with bit 15 of each word skipped (shift threshold is 15, not 16).
        if vals and vals[0] == 0xF100:
            src = vals[1:]  # skip the 0xF100 marker
            chars = []
            si = 0   # source word index
            shift = 0
            while si < len(src):
                # Extract 9-bit character spanning current word (and possibly next)
                cur_char = (src[si] >> shift) & 0x1FF
                shift += 9
                if shift >= 15:
                    si += 1
                    shift -= 15
                    if shift and si < len(src):
                        cur_char |= (src[si] << (9 - shift)) & 0x1FF
                if cur_char == 0x1FF:  # packed EOS
                    break
                ch = _get_gen4_char(cur_char)
                if ch == '?':
                    chars.append(f'\\x{cur_char:04X}')
                else:
                    chars.append(ch)
            strings.append(''.join(chars))
            continue

        # Normal text: process decrypted values through shared character table
        chars = []
        for dec in vals:
            if dec == 0xFFFF:
                break
            ch = _get_gen4_char(dec)
            if ch == '?':
                chars.append(f'\\x{dec:04X}')
            else:
                chars.append(ch)

        strings.append(''.join(chars))

    return strings

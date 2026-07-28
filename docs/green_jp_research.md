# Pocket Monsters Green (JP) — Charmap & Text Research

## Charmap Status: MOSTLY CORRECT

### Verified Empirically
- ニ=0x95, ビ=0x1A (from ニビシティ)
- ハ=0x99, ナ=0x94, ダ=0x0F (from ハナダシティ)
- フ=0x9B, シ=0x8B, ギ=0x06 (from フシギダネ bytes in code)
- サ=0x8A, イ=0x81, ホ=0x9C, ー=0xE3, ン=0xAB (from サイホーン)
- ガ=0x05 (voiced), ル=0xA6, ラ=0xA5 (from ガルーラ)

### Key Corrections from Bulbapedia Gen I JP Table
- ー = 0xE3 (NOT 0xE0 — was wrong in original charmap)
- ゃ=0xE0, ゅ=0xE1, ょ=0xE2 (small kana row E cols 0-2)
- ♂=0xEF, ♀=0xF5
- ヘ and リ are ABSENT from main katakana block (0x80-0xBF)
  - Removes ヘ (old position 28→0x9C was wrong), ホ=0x9C now correct
  - Removes リ from ラ行, so ル=0xA6, レ=0xA7, ロ=0xA8 shift left by 1
- パ=0x40, ピ=0x41, プ=0x42, ポ=0x43 (row 4, NOT 0x26-0x2A which was wrong)
- Row 2 (0x20-0x2F) = hiragana voiced: が=0x26, じ=0x2C, ず=0x2D, ぜ=0x2E, ぞ=0x2F
- Hiragana rows B-D: 0xB0-0xDF added (あ-ん, っ, etc.)
- Row E extras: エ=0xEB, ア=0xE9, ウ=0xEA

### Still Unknown (appear in species names)
- Row 3 (0x30-0x3F): appear in species entries. 0x3A likely = ば (from けつばん pattern)
- Various other < 0x60 bytes appear as transparent control codes in species names
- Need to scan for ALL unique unknown bytes in species region 0x39068-0x393FF

## Move Names: WORKING
- Location: ROM 0x10000 (bank 4)
- Format: variable-length EOS(0x50)-terminated Japanese
- Scanner: `_scan_gen1_moves_jp` anchored on はたく
- Result: 84 moves found correctly (all Gen I moves present but some with hiragana voiced gaps)
- First 10: はたく, からてチョップ, おうふくビンタ, れんぞくパンチ, メガトンパンチ, ネコにこん, ほのおのパンチ, れいとうパンチ, かみなりパンチ, ひっかく

## Species Names: PARTIALLY WORKING

### Table Location
- ROM 0x39068 (bank 14 = 0x38000-0x3BFFF)
- サイドン (Rhydon, internal #1) at 0x39068
- ガルーラ (Kangaskhan, internal #2) at 0x3906d

### Table Structure (IMPORTANT)
- NOT fixed-width 10-byte slots like EN Blue
- Variable-length EOS(0x50)-terminated entries
- BUT: multiple species names are PACKED within single EOS groups
  - e.g., 'フシギソウナッシー' = Ivysaur + Exeggutor in one entry
  - 'ギャラドスシエルダーメノ...' = Gyarados + others packed together
- Between groups: separator EOS (double EOS pattern common)
- Placeholder for MissingNo/empty: `B9 C2 3A DE 50` = けつ[ば]ん = 欠番

### Scanner Status
- Gets 21+ groups before stopping at unknown byte in species_charmap
- Scanner uses restricted charmap (only confirmed bytes) to avoid mixing move-context bytes
- Stops when encountering unknown byte ≥ 0x80 not in species charmap
- Need to map more row 3 (0x30-0x3F) bytes to continue past MissingNo entries

### Next Steps to Get All 151
1. Find all unique unknown bytes in 0x39068-0x393FF region
2. Map them from Bulbapedia row 3: 0x30=だ?, 0x3A=ば?, 0x3B=び? etc.
3. The scan will naturally collect all 151+ internal entries once all bytes are mapped
4. Note: entries will still have merged names — need species order table to split properly

### Internal Species Order
- #1=サイドン (Rhydon), #2=ガルーラ (Kangaskhan)
- Very different from National Dex order
- Placeholder けつばん appears for MissingNo slots

## Other Tables Found
- City names: candidate [9] = ハナダ, シオン, クチバ etc.
- Item names: candidate [0] = medicine names in hiragana
- Badge names: candidate [2]
- All via scan_rom_text with updated JP charmap

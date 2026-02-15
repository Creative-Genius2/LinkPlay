---
name: linkplay
description: Use when the user mentions .nds, .gba, .gbc, .gb files, ROM hacking, NARCs, ARM9, game data extraction, Pokémon ROM data, trainer teams, base stats, learnsets, battle facilities, PWT, Battle Tower, Battle Subway, or any request to explore, read, modify, or document Nintendo DS ROM contents. LinkPlay is an MCP server that exposes DS ROMs as navigable filesystems. It decrypts text, decodes binary structures, and provides persistent notes (Flipnotes) across sessions.
---

# LinkPlay — Portable Skill Reference

This document is your working reference for the LinkPlay MCP server. Use it to understand what each tool does, what data you can decode, what you cannot decode, and how to interpret results.

## Tool Reference

### ROM Lifecycle

#### `spotlight` — Open a ROM

Loads a ROM file into memory. For DS ROMs, this:
1. Reads the NDS header (game code, title, region)
2. Loads the Nitro File System
3. Decompresses ARM9 via BLZ
4. Loads the text NARC and decrypts ALL text files into memory
5. Auto-detects and labels text tables (species, moves, items, abilities, natures, types, trainer names, trainer classes, location names)
6. Builds the NARC role map for auto-decoding
7. Creates or loads the Flipnote for this game

**Parameters:**
- `path` (string, required): Path to ROM file (.nds, .gba, .gbc, .gb)

**After calling spotlight**, all other tools become functional against the loaded ROM. Multiple ROMs can be loaded simultaneously.

#### `return` — Close a ROM

Recalls the ROM from memory. Clears all loaded state (text tables, NARC cache, etc.).

**Parameters:**
- `save` (boolean, optional): Save changes before closing. Default: false

#### `record` — Save ROM

Repacks the ROM with all in-memory modifications. Recompresses ARM9 via BLZ, repacks filesystem via ndspy.

**Parameters:**
- `output_path` (string, required): Path for output ROM file

---

### Reading Data

#### `decipher` — Read and decode file contents

Reads a file from the ROM with auto-decompression and auto-decode for known data structures. This is the primary data retrieval tool.

**Parameters:**
- `path` (string, required): File path. Supports:
  - `arm9.bin`, `arm7.bin` — ARM binaries
  - `narc_path:index` — File inside a NARC (e.g., `a/0/9/2:156`)
  - Comma-separated paths for multi-file reads
- `offset` (integer, optional): Byte offset
- `length` (integer, optional): Bytes to read. Default: entire file
- `decompress` (boolean, optional): Auto-decompress LZ10/LZ11. Default: true

**Returns both `data` (raw hex) and `decoded` (structured JSON) when a decoder exists for the file's role.**

#### `summarize` — List contents at a path

Lists folder or NARC contents with types and sizes.

**Parameters:**
- `path` (string, optional): Folder or NARC path. Default: root
- `expand_narcs` (boolean, optional): Preview NARC contents inline. Default: false

#### `scope` — Raw hex dump

Shows raw bytes as offset + hex + ASCII columns. Use when you need to see the actual binary data without any decoder interpretation.

**Parameters:**
- `path` (string, optional): File path
- `offset` (integer, optional): Start offset
- `length` (integer, optional): Bytes to dump. Default: 256
- `search` (string, optional): Hex pattern to find, returns all match offsets

---

### Searching

#### `dowse` — Search text tables and NARC files

Searches text tables by name, NARC files by hex pattern, or both combined.

**Parameters:**
- `name` (string, optional): Text to search (e.g., "Pikachu", "Thunderbolt")
- `table` (string, optional): Limit to one table: species, moves, items, abilities, trainer_names, trainer_classes, natures, type_names, location_names
- `narc_path` (string, optional): NARC to search
- `hex` (string, optional): Hex pattern to find in NARC
- `exact` (boolean, optional): Whole-string match. Default: false

**Modes:**
- Name only — searches all text tables
- Hex + narc_path — searches NARC files for byte pattern
- Name + narc_path — resolves name to ID, converts to u16 LE, searches NARC

---

### Writing Data

#### `sketch` — Write data to a file

Writes data to a file in the ROM. Changes stay in memory until `record` is called.

**Parameters:**
- `path` (string, required): File path (same path types as `decipher`)
- `data` (string, required): Data to write (hex accepts spaces: `F8 B5 82 B0`)
- `offset` (integer, optional): Byte offset. Default: 0
- `encoding` (string, optional): hex, utf8, utf16le, ascii. Default: hex

---

### Comparison

#### `judgement` — Compare two files

Byte-level comparison of two files. Shows offset + byte A + byte B for each difference. Caps at 100 differences.

**Parameters:**
- `path_a` (string, required): First file path
- `path_b` (string, required): Second file path

---

### Analysis

#### `stats` — Documentation coverage report

Reports Flipnote coverage: labeled paths vs total files, notes with format/structure fields, ARM9 byte coverage estimates.

**Parameters:** None

---

### Flipnote Operations

Flipnotes are persistent JSON files (`~/.linkplay/flipnotes/`) that store what you learn about each game's ROM structure across sessions. Identified by game code, named by title.

#### `list_flipnotes` — List all known games
#### `view_flipnote` — Read a Flipnote
- `game` (string, required): Game code (e.g., "IRE") or title words (e.g., "black 2")

#### `note` — Add knowledge to current Flipnote
- `path` (string, required): Path being documented
- `description` (string, required): What this path contains
- `name`, `format`, `tags`, `file_range`, `examples`, `related` — all optional

#### `edit_note` — Modify an existing note
#### `delete_note` — Remove a note

---

## Supported Games

| Game | Code | Generation | Text NARC |
|---|---|---|---|
| Diamond | ADA | IV | `msgdata/msg.narc` |
| Pearl | APA | IV | `msgdata/msg.narc` |
| Platinum | CPU | IV | `msgdata/pl_msg.narc` |
| HeartGold | IPK | IV | `a/0/2/7` |
| SoulSilver | IPG | IV | `a/0/2/7` |
| Black | IRB | V | `a/0/0/2` |
| White | IRA | V | `a/0/0/2` |
| Black 2 | IRE | V | `a/0/0/2` |
| White 2 | IRD | V | `a/0/0/2` |

---

## Auto-Decode System

When text tables are loaded, `decipher` returns a `decoded` JSON field alongside raw hex for known NARC roles. The server maps NARC paths to roles via `GAME_INFO`, then `_auto_decode()` dispatches to the correct decoder. If no decoder exists, `decoded` is `null` and you only get hex.

### Decoded Data Structures

#### Personal Data (base stats) — `personal` role
- Gen IV: 44 bytes/entry — HP, Atk, Def, Spe, SpA, SpD, types (2×u8), abilities (2×u8), catch rate, EV yield, egg groups, gender ratio, hatch cycles, happiness, growth rate
- Gen V: 76 bytes/entry — same fields plus hidden ability (3×u16), wider fields
- **Does NOT include:** TM/HM compatibility bitmask (bits exist in data but are not extracted)

#### Learnsets — `learnsets` role
- Gen IV: packed u16 — `level<<9 | move_id`, terminated by 0xFFFF
- Gen V: separate u16 pairs — `(move_id, level)`, terminated by 0xFFFF

#### Evolutions — `evolutions` role
- 7 slots × 6 bytes per entry, 30 evolution methods decoded
- Returns: method name, parameter, target species name

#### Move Data — `move_data` role
- Gen IV: 16 bytes/move — type, category, power, accuracy, PP
- Gen V: 36 bytes/move — type, category, power, accuracy, PP, priority, multi-hit, effect chance

#### Trainer Data — `trdata` role
- 20 bytes/trainer — class, battle type, AI flags, held items (4 slots), reward multiplier
- Gen V battle types include Triple and Rotation

#### Trainer Pokémon — `trpoke` role
- 4 template formats determined by bit flags:
  - Bit 0 set: has custom moves (+8 bytes, 4 moves × u16)
  - Bit 1 set: has held item (+2 bytes, item_id u16)
  - Template 0 (0b00): 8 bytes — species, level, IVs, ability, form
  - Template 1 (0b01): 16 bytes — + moves
  - Template 2 (0b10): 10 bytes — + item
  - Template 3 (0b11): 18 bytes — + item + moves
- IV encoding: difficulty byte (0-255) → `IV = difficulty * 31 / 255`

#### Encounters — `encounters` role
- DPPt: 424 bytes — rates + species/level tables for grass, water, fishing, time-of-day
- HGSS: 196 bytes — rates + species/level tables
- Gen V BW: 232 bytes per zone
- Gen V B2W2: 928 bytes per zone (more encounter types)
- Each entry: species_id (u16), min_level (u8), max_level (u8)

#### Battle Tower (Gen IV) — `battle_tower_pokemon` / `battle_tower_trainers` roles
- Pokémon: 16 bytes/entry — species, 4 moves, EV spread (bitmask), nature, held item
- Trainers: format + pool count + pool indices

#### Battle Subway (Gen V) — `subway_pokemon` / `subway_trainers` roles
- Same 16-byte format as Battle Tower
- BW1 paths differ from B2W2 paths

#### PWT (B2W2 only) — `pwt_rental`, `pwt_champions`, `pwt_rosters`, `pwt_trainers` roles
- Rental Pokémon: 16 bytes — species, moves, EVs, nature, trainer class
- Champions Pokémon: 16 bytes — species, moves, EVs, nature, held item
- Trainer configs: 6 bytes — format, count, start index
- Rosters: format + pool count + pool indices
- `pwt_download` and `pwt_ui` are explicitly skipped (no decoder)

#### Pokéathlon (HGSS only) — `pokeathlon_performance` role
- 20 bytes/entry, 5 stats (Speed/Power/Skill/Stamina/Jump)
- Each stat has min, base, and max values (Aprijuice boost variants)

#### Contest (DPPt only) — `contest` role
- 96-byte entries with species and moves

### EV Spread Encoding (Battle Facilities)
- Bitmask: bits 0-5 = HP/Atk/Def/Spe/SpA/SpD
- Each set bit = 252 EVs in that stat
- Example: 0x03 = bits 0,1 = HP+Atk = 252/252/0/0/0/0

---

## Text Table System

### Gen IV Text Decryption
- Entry table: per-entry key derived from seed at offset 0x02
- String XOR: key = `((entry + 1) * 0x91BD3) & 0xFFFF`, advances `+0x493D` per u16
- Control code 0xF100: 9-bit compressed text (LSB-first bitstream, 0x1FF terminator)
- Character encoding is **proprietary** — NOT Unicode. Uses `_get_gen4_char()` lookup covering hiragana, katakana, fullwidth, accented, special characters
- Terminator: 0xFFFF. Newline: 0xFFFE.

### Gen V Text Decryption
- XOR key per entry: `key = ((entry_index + 3) * MULT) & 0xFFFF`
- Key advances via ROL3: `key = ((key << 3) | (key >> 13)) & 0xFFFF`
- MULT derived from species file: `encrypted_entry_1[0] ^ 0x0042 = 4 * MULT`
- Control code 0xF100: 9-bit compressed text
- Characters ARE Unicode (UTF-16)
- 495 text files in Gen V ROMs

### Auto-Detection (Fingerprinting)
The server identifies text tables by content:
- **Exact index**: species[1]="Bulbasaur", moves[1]="Pound", items[1]="Master Ball", abilities[1]="Stench"
- **Heuristic markers**: trainer_classes contains ["Youngster", "Lass"], natures contains ["Hardy", "Lonely", "Brave"], types contains ["Normal", "Fighting", "Flying"]
- **Adjacency**: trainer_names usually ±1-2 file indices from trainer_classes
- **Pass 4**: description tables detected near name tables by longer average string length

### Labeled Text Tables
These are auto-detected and usable via `dowse`:
- species, moves, items, abilities, natures, type_names, trainer_names, trainer_classes, location_names
- Some description tables (move/item/ability descriptions) found via adjacency — **inconsistent**

### Unlabeled Text (Decrypted But Not Findable)
This text IS decrypted and in memory, but no fingerprint identifies it. You cannot search for it by category — only by brute-forcing file indices:
- Pokédex flavor text (species descriptions)
- Pokédex species categories ("Seed Pokémon")
- Item descriptions
- Move descriptions
- Ability descriptions
- Story/intro text (professor speeches)
- NPC dialogue
- Battle dialogue
- Tutorial/UI strings

---

## Known Limitations

### Not Decoded (Hex Only for ALL 9 Games)
- **Item data** — effects, prices, bag pocket, fling power, hold effects. DP/Pt have the NARC path mapped (`items` role) but `_auto_decode()` has NO handler — returns null. HGSS/BW/B2W2 don't even have the path mapped.
- **TM/HM compatibility** — bitmask fields exist inside personal data but `decode_personal()` does not extract them
- **TM → Move mapping** — which move each TM teaches is not extracted
- **Egg moves** — separate NARC, not mapped
- **Tutor moves** — ARM9 overlay in Gen IV, separate NARC in Gen V, not mapped
- **Pokédex numeric data** — height, weight, regional dex order — separate NARC, not mapped
- **Form data tables** — not mapped
- **Type effectiveness table** — in ARM9, not exposed
- **EXP growth tables** — in ARM9, not exposed
- **Scripts/Events** — complex bytecode format, not mapped
- **Map/Zone data** — connections, warps, camera — not mapped
- **Overworld/NPC positions** — sprite placement, movement, sight ranges — not mapped
- **Shop inventories** — embedded in scripts, not mapped
- **Gift/Trade Pokémon** — embedded in scripts, not mapped
- **Berry data (Gen IV)** — not mapped
- **Safari Zone config (HGSS)** — not mapped
- **Bug Catching Contest (HGSS)** — not mapped
- **Hidden Grotto data (B2W2)** — not mapped
- **Graphics/Sprites** — NCGR/NCLR/NANR (Gen IV), RLCN/RGCN/RCSN (Gen V) — not mapped
- **Sound/Music** — SDAT format — not mapped

### Known Bugs
1. **Items role (DP/Pt)**: NARC path mapped but `_auto_decode()` has no `elif role == 'items':` branch — silently returns null decoded field
2. **Gen IV trainer names garbled**: Species decode correctly but trainer names show as `\xF100\x1B37...` escape sequences
3. **Gen IV compressed text `chr(c)` fallback**: In the compressed text (0xF100) path, the `else` branch incorrectly uses `chr(c)` instead of `_get_gen4_char(c)`. Gen IV is NOT Unicode — this produces silently wrong characters. See `FIX_GEN4_COMPRESSED_TEXT.md`.

### What You CANNOT Answer With This Server
- "What does [item] do?" — no item data decoder
- "Can [Pokémon] learn [TM]?" — TM compat bits not extracted
- "What's [Pokémon]'s Pokédex entry?" — text decrypted but file index unknown
- "How tall/heavy is [Pokémon]?" — Pokédex numeric data not mapped
- "What egg moves can [Pokémon] get?" — egg move NARC not mapped
- "What tutor moves can [Pokémon] learn?" — tutor data not mapped
- "Compare the intro text across games" — text decrypted but file indices unknown
- "What does this NPC say?" — dialogue text not fingerprinted
- "What happens in the story?" — scripts not decoded, dialogue unlabeled

### What You CAN Answer
- Base stats, types, abilities for any Pokémon
- Level-up learnsets
- Evolution methods and targets
- Move mechanical data (power, accuracy, PP, category, type)
- Full trainer team compositions (species, level, IVs, moves, items)
- Wild encounter tables (species, levels, rates per area)
- Battle Tower/Subway/PWT facility pools
- Pokéathlon performance stats (HGSS)
- Contest data (DPPt)
- Any text that has been fingerprinted (species/move/item/ability names, trainer names/classes, location names, nature names, type names)

---

## Game-Specific NARC Paths

### Diamond (ADA) / Pearl (APA)
| Role | Path |
|---|---|
| text | `msgdata/msg.narc` |
| personal | `poketool/personal/personal.narc` |
| learnsets | `poketool/personal/wotbl.narc` |
| evolutions | `poketool/personal/evo.narc` |
| move_data | `poketool/waza/waza_tbl.narc` |
| trdata | `poketool/trainer/trdata.narc` |
| trpoke | `poketool/trainer/trpoke.narc` |
| encounters | `fielddata/encountdata/d_enc_data.narc` (D) / `p_enc_data.narc` (P) |
| battle_tower_pokemon | `battle/b_tower/btdpm.narc` |
| battle_tower_trainers | `battle/b_tower/btdtr.narc` |
| contest | `contest/data/contest_data.narc` |

### Platinum (CPU)
| Role | Path |
|---|---|
| text | `msgdata/pl_msg.narc` |
| personal | `poketool/personal/pl_personal.narc` |
| learnsets | `poketool/personal/wotbl.narc` |
| evolutions | `poketool/personal/evo.narc` |
| move_data | `poketool/waza/pl_waza_tbl.narc` |
| trdata | `poketool/trainer/trdata.narc` |
| trpoke | `poketool/trainer/trpoke.narc` |
| encounters | `fielddata/encountdata/pl_enc_data.narc` |
| battle_tower_pokemon | `battle/b_pl_tower/pl_btdpm.narc` |
| battle_tower_trainers | `battle/b_pl_tower/pl_btdtr.narc` |
| contest | `contest/data/contest_data.narc` |

### HeartGold (IPK) / SoulSilver (IPG)
| Role | Path |
|---|---|
| text | `a/0/2/7` |
| personal | `a/0/0/2` |
| learnsets | `a/0/3/3` |
| evolutions | `a/0/3/4` |
| move_data | `a/0/1/1` |
| trdata | `a/0/5/5` |
| trpoke | `a/0/5/6` |
| encounters | `a/1/3/6` |
| battle_tower_pokemon | `a/2/0/3` |
| battle_tower_trainers | `a/2/0/2` |
| pokeathlon_performance | `a/1/6/9` |

### Black (IRB) / White (IRA)
| Role | Path |
|---|---|
| text | `a/0/0/2` |
| personal | `a/0/1/6` |
| learnsets | `a/0/1/8` |
| evolutions | `a/0/1/9` |
| move_data | `a/0/2/1` |
| trdata | `a/0/9/2` |
| trpoke | `a/0/9/3` |
| encounters | `a/1/2/6` |
| subway_pokemon | `a/2/1/4` |
| subway_trainers | `a/2/1/5` |

### Black 2 (IRE) / White 2 (IRD)
| Role | Path |
|---|---|
| text | `a/0/0/2` |
| personal | `a/0/1/6` |
| learnsets | `a/0/1/8` |
| evolutions | `a/0/1/9` |
| move_data | `a/0/2/1` |
| trdata | `a/0/9/1` |
| trpoke | `a/0/9/2` |
| encounters | `a/1/2/6` |
| subway_pokemon | `a/2/1/1` |
| subway_trainers | `a/2/1/2` |
| pwt_rental | `a/2/5/0` |
| pwt_trainers | `a/2/5/1` |
| pwt_rosters | `a/2/5/2` |
| pwt_trainers_b | `a/2/5/4` |
| pwt_rosters_b | `a/2/5/5` |
| pwt_champions | `a/2/5/6` |
| pwt_champions_b | `a/2/5/7` |

---

## Compression Support

Transparent to the user. LinkPlay auto-detects and decompresses on read, recompresses on save.

| Format | Header Byte | Tool |
|---|---|---|
| LZ10 | `0x10` | lzss (or ndspy fallback) |
| LZ11/LZ40 | `0x11`, `0x40` | lzx |
| Huffman | `0x20`, `0x28` | huffman |
| RLE | `0x30` | rle |
| BLZ | (tail compression) | blz (ARM9/overlays) |

---

## Typical Workflows

### Explore a trainer's team
```
spotlight /path/to/rom.nds
decipher a/0/9/1:47          → trainer data (class, AI, items)
decipher a/0/9/2:47          → trainer's Pokémon (species, level, moves)
```

### Find a Pokémon's data across structures
```
dowse name="Garchomp" table="species"     → get species index
decipher a/0/1/6:{index}                   → base stats
decipher a/0/1/8:{index}                   → learnset
decipher a/0/1/9:{index}                   → evolutions
dowse name="Garchomp" narc_path="a/0/9/2"  → find trainers using Garchomp
```

### Compare data between games
```
spotlight /path/to/platinum.nds
decipher poketool/personal/pl_personal.narc:445    → Garchomp in Platinum
spotlight /path/to/black2.nds
decipher a/0/1/6:445                                → Garchomp in Black 2
```

### Document findings
```
note path="a/0/9/2" description="Trainer Pokémon data" format="TRPoke: 4 templates (8/10/16/18 bytes)" tags=["trainers", "battle"]
```

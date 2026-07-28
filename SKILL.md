---
name: linkplay
description: Use when the user mentions .nds, .gba, .gbc, .gb files, ROM hacking, NARCs, ARM9, game data extraction, Pokémon ROM data, trainer teams, base stats, learnsets, battle facilities, PWT, Battle Tower, Battle Subway, Gen I/II/III/IV/V Pokémon games, or any request to explore, read, modify, or document Nintendo ROM contents. LinkPlay is an MCP server for Pokémon ROMs across all generations (Gen I GB through Gen V DS). It decrypts text, decodes binary structures (trainers, personal data, encounters, learnsets, moves, items), and provides persistent notes (Flipnotes) across sessions. Gen V and IV have full decode support. Gen III GBA has trainers/personal/moves. Gen I JP has personal/trainers/species/moves. Gen II and Gen I EN are partially supported.
---

# LinkPlay — Portable Skill Reference

This document is your working reference for the LinkPlay MCP server. Use it to understand what each tool does, what data you can decode, and how to interpret results.

## Tool Reference

### ROM Lifecycle

#### `spotlight` — Open a ROM

Loads a ROM file into memory. For DS ROMs, this:
1. Reads the NDS header (game code, title, region)
2. Loads the Nitro File System
3. Decompresses ARM9 via BLZ, loads all overlays
4. Loads the text NARC and decrypts ALL text files into memory
5. Auto-detects and labels text tables (species, moves, items, abilities, natures, types, trainer names, trainer classes, location names)
6. Discovers TM→move table in ARM9 (for TM/HM compatibility in personal data)
7. Discovers F100 9-bit compression table in ARM9/overlays
8. Builds NARC role map for auto-decoding
9. Creates or loads the Flipnote for this game

Multiple ROMs can be open simultaneously. Cross-ROM comparison supported via `IRE:path` prefix syntax. Previously opened ROMs auto-restore on server restart (no re-spotlight needed).

**Parameters:**
- `path` (string, required): Path to ROM file (.nds, .gba, .gbc, .gb)

#### `return` — Close a ROM

Closes the active ROM. If multiple ROMs are loaded, switches to the next one.

**Parameters:**
- `save` (boolean, optional): Save changes before closing. Default: false

#### `record` — Save ROM

Repacks the ROM with all in-memory modifications.

**Parameters:**
- `output_path` (string, required): Path for output ROM file

---

### Reading Data

#### `decipher` — Read and decode file contents

Primary data tool. Reads files with auto-decompression and auto-decode.

**Parameters:**
- `path` (string, required): Supports `arm9.bin`, `arm7.bin`, `narc:index`, `overlay{N}.bin`, comma-separated, cross-ROM prefix
- `offset` (integer, optional): Byte offset
- `length` (integer, optional): Bytes to read. Default: entire file
- `decompress` (boolean, optional): Auto-decompress. Default: true
- `reads` (string, optional): Structured binary types: u8/u16/u32/s8/s16/s32/ptr32/text
- `count` (integer, optional): Values to read. Default: 1
- `xor` (string, optional): XOR key hex (e.g., `AB CD`)
- `endian` (string, optional): `little` or `big`
- `stride` (integer, optional): Bytes between reads
- `base` (integer, optional): Base address for ptr32

#### `summarize` — List contents
- `path` (string, optional): Folder or NARC path. Default: root
- `expand_narcs` (boolean, optional): Preview NARC contents. Default: false

#### `scope` — Raw hex dump
- `path`, `offset`, `length` (default 256), `search` (hex pattern), `xor` (XOR key)

---

### Searching

#### `dowse` — Search text tables and NARCs
- `name` (string): Text to search
- `table` (string): Specific table to search
- `narc_path` (string): NARC to search
- `hex` (string): Hex pattern
- `exact` (boolean): Whole-string match
- `difficulty` (string): Filter trainer results by `'normal'`, `'challenge'`, or `'easy'` (BW2 only). Auto-clusters trainer files by proximity and pokemon count — no hardcoded ranges.

#### `probe` — Structured binary read
Reads typed values from files without manual hex math. Annotates u16/u32 values with species/move/item name guesses.
- `path` (string, required): File path (same as decipher)
- `reads` (string): Type — `u8`, `u16`, `u32`, `s8`, `s16`, `s32`, `ptr32`, `text`
- `offset`, `count`, `stride`, `endian`, `xor`, `base` (for ptr32)

---

### Writing & Comparison

#### `sketch` — Write data
- `path`, `data`, `offset` (default 0), `encoding` (hex/utf8/utf16le/ascii)

#### `judgement` — Compare two files
- `path_a`, `path_b` — Supports cross-ROM prefixes

#### `stats` — Documentation coverage report

---

### Flipnote Operations

#### `note` / `batch_notes` / `edit_note` / `delete_note` / `list_flipnotes` / `view_flipnote`

Persistent research notes stored in `~/.linkplay/flipnotes/`. Paired games share flipnotes.

---

## Supported Games

| Game | Code | Gen | Text NARC | Encounters |
|------|------|-----|-----------|------------|
| Diamond | ADA | IV | `msgdata/msg.narc` | `fielddata/encountdata/d_enc_data.narc` |
| Pearl | APA | IV | `msgdata/msg.narc` | `fielddata/encountdata/p_enc_data.narc` |
| Platinum | CPU | IV | `msgdata/pl_msg.narc` | `fielddata/encountdata/pl_enc_data.narc` |
| HeartGold | IPK | IV | `a/0/2/7` | `a/1/3/6` |
| SoulSilver | IPG | IV | `a/0/2/7` | `a/1/3/6` |
| Black | IRB | V | `a/0/0/2` | `a/1/2/6` |
| White | IRA | V | `a/0/0/2` | `a/1/2/6` |
| Black 2 | IRE | V | `a/0/0/2` | `a/1/2/7` |
| White 2 | IRD | V | `a/0/0/2` | `a/1/2/7` |

---

## Auto-Decode System

When `decipher` reads a file from a known NARC role, it returns structured data alongside raw hex. The server maps NARC paths to roles via `GAME_INFO`, then dispatches to the correct decoder.

### Decoded Data Structures

#### Personal Data — `personal` role
- Gen IV: 44 bytes — HP/Atk/Def/Spe/SpA/SpD, types, abilities, catch rate, EV yield, egg groups, gender, hatch cycles, happiness, growth rate, **TM/HM compatibility bitmask**
- Gen V: 76 bytes — same fields plus hidden ability, height/weight

#### Learnsets — `learnsets` role
- Gen IV: packed u16 — `level<<9 | move_id`, terminated by 0xFFFF
- Gen V: separate u16 pairs — `(move_id, level)`, terminated by 0xFFFF

#### Evolutions — `evolutions` role
- 7 slots × 6 bytes, 30 evolution methods decoded

#### Move Data — `move_data` role
- Gen IV: 16 bytes — type, category, power, accuracy, PP
- Gen V: 36 bytes — + priority, multi-hit, effect chance

#### Trainer Data — `trdata` role
- 20 bytes — class, battle type (Single/Double/Triple/Rotation), AI flags, held items (4 slots), reward multiplier

#### Trainer Pokémon — `trpoke` role
- 4 templates (8/10/16/18 bytes) determined by bit flags
- Species, level, IVs, ability slot, form, optional moves and held item
- IV encoding: `difficulty * 31 / 255`

When `decipher` hits a trdata or trpoke file, it eagerly loads BOTH files and formats them together: class name, trainer name, location (gym leaders/E4/champions mapped per-game), each pokemon with species, level, ability (resolved from personal data), gender, IVs, held item, moves, and prize money. For BW2, automatically labels Normal Mode vs Challenge Mode by clustering file indices and comparing average pokemon counts, with hardcoded overrides for E4/Iris where clustering is ambiguous.

#### Encounters — `encounters` role
- **DPPt**: 424 bytes — grass (12 slots) + swarm/day/night/radar replacements + surf/fishing (5 sections)
- **HGSS**: 196 bytes — morning/day/night grass tables + Sound species + surf/rock smash/fishing. Location names resolved via hardcoded `_HGSS_ENC_LOC` table (142 entries, species-verified)
- **Gen V**: 232 bytes/season — grass/double/special + surf + fishing + seasonal variants. Location names via `_BW1_ENC_LOC` / `_B2W2_ENC_LOC`
- **DP/Pt**: Location names resolved from ARM9 lookup tables

#### Item Data — `items` role
- Gen IV: 34 bytes — price, fling power
- Gen V: 36 bytes — price × 10, fling power

#### Battle Tower (Gen IV) — `battle_tower_pokemon` / `battle_tower_trainers`
- Pokémon: 16 bytes — species, 4 moves, EV spread (bitmask), nature, held item
- Trainers: format + pool count + pool indices

#### Battle Subway (Gen V) — `subway_pokemon` / `subway_trainers`
- Same 16-byte format as Battle Tower

#### PWT (B2W2 only) — `pwt_rental` / `pwt_champions` / `pwt_rosters` / `pwt_trainers`
- Rental: 16 bytes — species, moves, EVs, nature, trainer class
- Champions: 16 bytes — species, moves, EVs, nature, held item
- Trainers/Rosters: pool configs

#### Pokéathlon (HGSS) — `pokeathlon_performance` role
- 20 bytes, 5 stats (Power/Speed/Jump/Stamina/Skill), min/base/max values

#### Contest (DPPt) — `contest` role
- 96 bytes per entry with species and moves

### EV Spread Encoding (Battle Facilities)
- Bitmask: bits 0-5 = HP/Atk/Def/Spe/SpA/SpD
- Each set bit = 252 EVs in that stat

---

## Text Table System

### Gen IV Text Decryption
- Entry table: key from seed at offset 0x02, advances `+0x493D` per u16
- String XOR: key = `((entry + 1) * 0x91BD3) & 0xFFFF`
- F100 compressed text: 9-bit encoding (LSB-first, 0x1FF terminator)
- Character encoding is **proprietary** (NOT Unicode) — uses lookup tables
- F100 compression table auto-discovered from ARM9/overlays

### Gen V Text Decryption
- XOR key: `((entry_index + 3) * MULT) & 0xFFFF`, advances via ROL3
- MULT derived from species file: `encrypted_entry_1[0] ^ 0x0042 = 4 * MULT`
- Characters ARE Unicode (UTF-16)

### Auto-Detection
Text tables identified by content fingerprinting:
- **Exact index**: species[1]="Bulbasaur", moves[1]="Pound", items[1]="Master Ball"
- **Heuristic**: trainer_classes has ["Youngster", "Lass"], natures has ["Hardy", "Lonely"]
- **Adjacency**: trainer_names detected near trainer_classes, descriptions near name tables

---

## Game-Specific NARC Paths

### Diamond (ADA) / Pearl (APA)
| Role | Path |
|------|------|
| text | `msgdata/msg.narc` |
| personal | `poketool/personal/personal.narc` |
| learnsets | `poketool/personal/wotbl.narc` |
| evolutions | `poketool/personal/evo.narc` |
| move_data | `poketool/waza/waza_tbl.narc` |
| trdata | `poketool/trainer/trdata.narc` |
| trpoke | `poketool/trainer/trpoke.narc` |
| encounters | `d_enc_data.narc` / `p_enc_data.narc` |
| items | `itemtool/itemdata/item_data.narc` |
| battle_tower | `battle/b_tower/btdpm.narc`, `btdtr.narc` |
| contest | `contest/data/contest_data.narc` |

### Platinum (CPU)
| Role | Path |
|------|------|
| text | `msgdata/pl_msg.narc` |
| personal | `poketool/personal/pl_personal.narc` |
| move_data | `poketool/waza/pl_waza_tbl.narc` |
| encounters | `fielddata/encountdata/pl_enc_data.narc` |
| items | `itemtool/itemdata/pl_item_data.narc` |
| battle_tower | `battle/b_pl_tower/pl_btdpm.narc`, `pl_btdtr.narc` |

### HeartGold (IPK) / SoulSilver (IPG)
| Role | Path |
|------|------|
| text | `a/0/2/7` |
| personal | `a/0/0/2` |
| learnsets | `a/0/3/3` |
| evolutions | `a/0/3/4` |
| move_data | `a/0/1/1` |
| trdata | `a/0/5/5` |
| trpoke | `a/0/5/6` |
| encounters | `a/1/3/6` |
| items | `a/0/1/7` |
| battle_tower | `a/2/0/3`, `a/2/0/2` |
| pokeathlon | `a/1/6/9` |

### Black (IRB) / White (IRA)
| Role | Path |
|------|------|
| text | `a/0/0/2` |
| personal | `a/0/1/6` |
| learnsets | `a/0/1/8` |
| evolutions | `a/0/1/9` |
| move_data | `a/0/2/1` |
| trdata | `a/0/9/2` |
| trpoke | `a/0/9/3` |
| encounters | `a/1/2/6` |
| subway | `a/2/1/4`, `a/2/1/5` |

### Black 2 (IRE) / White 2 (IRD)
| Role | Path |
|------|------|
| text | `a/0/0/2` |
| personal | `a/0/1/6` |
| learnsets | `a/0/1/8` |
| evolutions | `a/0/1/9` |
| move_data | `a/0/2/1` |
| trdata | `a/0/9/1` |
| trpoke | `a/0/9/2` |
| encounters | `a/1/2/7` |
| subway | `a/2/1/1`, `a/2/1/2` |
| pwt_rental | `a/2/5/0` |
| pwt_trainers | `a/2/5/1`, `a/2/5/4` |
| pwt_rosters | `a/2/5/2`, `a/2/5/5` |
| pwt_champions | `a/2/5/6`, `a/2/5/7` |

---

## Compression Support

Transparent to the user. Auto-detects and decompresses on read, recompresses on save.

| Format | Header Byte | Tool |
|--------|-------------|------|
| LZ10 | `0x10` | lzss (or ndspy fallback) |
| LZ11/LZ40 | `0x11`, `0x40` | lzx |
| Huffman | `0x20`, `0x28` | huffman |
| RLE | `0x30` | rle |
| BLZ | (tail compression) | blz (ARM9/overlays) |

---

## What You CAN Answer

- Base stats, types, abilities, TM/HM compatibility for any Pokémon
- Level-up learnsets and evolution chains
- Move mechanical data (power, accuracy, PP, category, type, priority)
- Full trainer team compositions (species, level, IVs, moves, items, AI)
- Wild encounter tables with correct location names (all 9 games)
- Battle Tower/Subway/PWT facility pools
- Item prices and fling power
- Pokéathlon performance stats (HGSS)
- Contest data (DPPt)
- Any named text (species/move/item/ability names, trainer names/classes, locations, natures, types)

## What You CANNOT Answer (Yet)

- Egg moves (NARC not mapped)
- Tutor moves (ARM9 overlay / separate NARC, not mapped)
- Pokédex entries (text decrypted but file index not fingerprinted)
- Scripts/events, NPC dialogue, shop inventories
- Map/zone connections, overworld positions
- Safari Zone config, Bug Catching Contest (HGSS)
- Hidden Grotto data (B2W2)
- Graphics/sprites (NCGR/NCLR format), sound/music (SDAT format)

---

## Eonet (Optional)

The Eonet system (`eonet_driver.py`) is an optional client-side orchestrator that sits between the user and Claude. It resolves queries against flipnote routing databases before they reach the model — so "What's Iris's team?" automatically resolves to the correct trdata/trpoke file indices. Two timelines: user sees their original message, Claude sees the resolved sliver.

---

## Typical Workflows

### Explore a trainer's team
```
spotlight /path/to/rom.nds
decipher a/0/5/6:47          → trainer's Pokémon
```

### Find a Pokémon's data
```
dowse name="Garchomp" table="species"     → species index
decipher a/0/0/2:{index}                   → base stats + TM compat
decipher a/0/3/3:{index}                   → learnset
decipher a/0/3/4:{index}                   → evolutions
```

### Check encounters
```
decipher a/1/3/6:57                        → Route 43 encounters (HGSS)
decipher a/1/2/7:104                       → Route 4 encounters (B2W2)
```

### Compare across games
```
spotlight /path/to/heartgold.nds
spotlight /path/to/black2.nds
judgement IPK:a/0/0/2:445 IRE:a/0/1/6:445  → Garchomp: HG vs B2
```

### Document findings
```
note path="a/1/3/6" description="Wild encounters, 142 files" tags=["encounters"]
```

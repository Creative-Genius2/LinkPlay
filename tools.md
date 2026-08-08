# Silphéon Tool Specifications

## ROM Operations

### spotlight — Open ROM

Opens a ROM file and loads all game data into memory.

**Parameters:**
- `path` (string, required): Path to ROM file (.nds, .gba, .gbc, .gb)

**Behavior:**
1. Detect ROM type from header
2. Read header: game code, title, region
3. DS: decompress ARM9 via BLZ, load overlays
4. Load text NARC, decrypt all text files, auto-detect named tables (species, moves, items, abilities, natures, types, trainer names/classes, location names)
5. Discover TM→move table and F100 compression table in ARM9
6. Build NARC role map for auto-decoding
7. Create or load Flipnote for this game

Multiple ROMs can be open simultaneously. Cross-ROM comparison is supported.

**Returns:** ROM metadata, text table summary, flipnote path

---

### return — Close ROM

Closes the active ROM and clears state. Switches to another loaded ROM if available.

**Parameters:**
- `save` (boolean, optional): Save changes before closing. Default: false

---

### record — Save ROM

Repacks the ROM with all in-memory modifications.

**Parameters:**
- `output_path` (string, required): Path for output ROM file

**Behavior:** Recompresses ARM9 via BLZ, repacks filesystem via ndspy, writes to output path.

---

## Reading Data

### decipher — Read and decode files

Primary data retrieval tool. Reads files with auto-decompression and auto-decode for known structures.

**Parameters:**
- `path` (string, required): File path. Supports:
  - `arm9.bin`, `arm7.bin` — ARM binaries
  - `narc_path:index` — File inside a NARC (e.g., `a/0/5/6:47`)
  - `overlay{N}.bin` — Overlay files
  - Comma-separated paths for multi-file reads
  - Cross-ROM prefix: `IRE:a/0/1/6:1`
- `offset` (integer, optional): Byte offset
- `length` (integer, optional): Bytes to read. Default: entire file
- `decompress` (boolean, optional): Auto-decompress. Default: true
- `reads` (string, optional): Structured binary read types: u8, u16, u32, s8, s16, s32, ptr32, text
- `count` (integer, optional): Number of values to read. Default: 1
- `xor` (string, optional): XOR key in hex (e.g., `AB CD`)
- `endian` (string, optional): `little` or `big`. Default: little
- `stride` (integer, optional): Bytes between reads. Default: 0 (packed)
- `base` (integer, optional): Base address for ptr32 pointer following

**Auto-decoded structures:**
- Personal data (base stats, types, abilities, TM compat)
- Learnsets (level-up moves)
- Evolutions (methods, targets)
- Move data (power, accuracy, PP, type, category)
- Trainer data (class, AI flags, battle items)
- Trainer Pokémon (species, level, IVs, moves, held items)
- Encounters (species, levels, rates by terrain/time, with location names)
- Item data (prices, fling power)
- Battle Tower/Subway/PWT pools
- Pokéathlon performance (HGSS)
- Contest data (DPPt)

---

### summarize — List contents

Lists folder or NARC contents with types and sizes.

**Parameters:**
- `path` (string, optional): Folder or NARC path. Default: root
- `expand_narcs` (boolean, optional): Preview NARC contents inline. Default: false

---

### scope — Raw hex dump

Shows raw bytes as offset + hex + ASCII columns.

**Parameters:**
- `path` (string, optional): File path
- `offset` (integer, optional): Start offset
- `length` (integer, optional): Bytes to dump. Default: 256
- `search` (string, optional): Hex pattern to find
- `xor` (string, optional): XOR key to apply before display

---

## Searching

### dowse — Search text and NARCs

Searches text tables by name, NARC files by hex pattern, or both combined.

**Parameters:**
- `name` (string, optional): Text to search (e.g., "Pikachu", "Thunderbolt")
- `table` (string, optional): Limit to one table: species, moves, items, abilities, trainer_names, trainer_classes, natures, type_names, location_names
- `narc_path` (string, optional): NARC to search
- `hex` (string, optional): Hex pattern to find in NARC
- `exact` (boolean, optional): Whole-string match. Default: false

**Modes:**
- Name only → searches all text tables
- Hex + narc_path → searches NARC files for byte pattern
- Name + narc_path → resolves name to ID, converts to u16 LE, searches NARC
- Hex without narc_path → searches ALL loaded NARCs

---

## Writing

### sketch — Write data

Writes data to a file. Changes stay in memory until `record`.

**Parameters:**
- `path` (string, required): File path
- `data` (string, required): Data to write (hex supports spaces: `F8 B5 82 B0`)
- `offset` (integer, optional): Byte offset. Default: 0
- `encoding` (string, optional): hex, utf8, utf16le, ascii. Default: hex

---

## Comparison

### judgement — Compare files

Byte-level comparison of two files. Supports cross-ROM prefixes.

**Parameters:**
- `path_a` (string, required): First file path
- `path_b` (string, required): Second file path

Shows offset + byte A + byte B for each difference (capped at 100).

---

## Analysis

### stats — Coverage report

Documentation coverage report for the current Flipnote.

**Parameters:** None

---

## Flipnote Operations

Flipnotes are persistent JSON files (`~/.silphéon/flipnotes/`) that store knowledge about each game's ROM structure across sessions. Paired games share flipnotes.

### list_flipnotes
Lists all Flipnote files with game code, title, and note count.

### view_flipnote
- `game` (string, required): Game code (e.g., "IPK") or title words (e.g., "heartgold")

### note — Add knowledge
- `path` (string, required): Path being documented
- `description` (string, required): What this path contains
- `name`, `format`, `tags`, `file_range`, `examples`, `related` — optional
- `game` (string, optional): Game code to write to. Default: current ROM

### batch_notes — Write multiple notes
- `notes` (array, required): Array of note objects
- `game` (string, optional): Game code. Default: current ROM

### edit_note — Modify existing note
- `path` (string, required): Path of note to edit
- All other note fields optional

### delete_note — Remove a note
- `path` (string, required): Path of note to delete

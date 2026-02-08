# LinkPlay Tool Specifications

## ROM Operations

### open_rom

Opens a ROM file and loads/creates the associated Flipnote.

**Parameters:**
- `path` (string, required): Path to ROM file (.nds, .gba, .gbc, .gb)

**Behavior:**
1. Detect ROM type from extension/header
2. Read header: game_code, title, region
3. Scan .fpn files for matching game_code
4. If no match: create Flipnote named after English game title
5. DS only: decompress ARM9/ARM7 via blz
6. Gen V (B/W/B2/W2): load text NARC (a/0/0/2), derive XOR multiplier, decode all 495 text files into `text_tables`

**Returns:**
```json
{
  "rom_type": "nds",
  "game_code": "IRE",
  "game_title": "Pokémon Black Version 2",
  "region": "INT",
  "flipnote": "~/.linkplay/flipnotes/Pokémon_Black_2.fpn",
  "text_tables": {"file_count": 495, "mult": "0x2983", "status": "ok"}
}
```

---

### close_rom

Closes the currently open ROM and clears all state.

**Parameters:**
- `save` (boolean, optional): Save changes before closing. Default: false

**Behavior:**
1. If save=true, calls save_rom internally
2. Clears current_rom, text_tables, text_narc state

---

### ls

Lists contents at a path within the ROM. Pass a NARC path to see its internal files.

**Parameters:**
- `path` (string, optional): Folder or NARC path. Default: root
- `expand_narcs` (boolean, optional): Preview NARC contents inline. Default: false

**Behavior:**
- Folders: lists children with types and sizes
- NARC files: lists internal files with sizes, compression, and `narc_path:index` paths

---

### read

Reads file contents with auto-decompression and auto-decode for known structures.

**Parameters:**
- `path` (string, required): File path. Supports `arm9.bin`, `arm7.bin`, `narc_path:index`, comma-separated for multi-file
- `offset` (integer, optional): Byte offset
- `length` (integer, optional): Bytes to read. Default: entire file
- `decompress` (boolean, optional): Auto-decompress LZ10/LZ11. Default: true

**Auto-decode:**
When text_tables are loaded, known paths return a `decoded` field alongside raw hex:
- `a/0/9/1:N` — TRData: trainer class, battle type, items, AI flags, prize money
- `a/0/9/2:N` — TRPoke: species, level, IVs, moves, held items, gender
- `a/0/8/2:N` — Encounters: species, levels, rates by terrain
- `a/2/5/0:N`, `a/2/5/3:N` — PWT Rental: species, moves, EVs, nature, trainer class
- `a/2/5/6:N`, `a/2/5/7:N` — PWT Champions: species, moves, EVs, nature, held item

**Returns:**
```json
{
  "path": "a/0/9/2:156",
  "size": 32,
  "compression": "none",
  "data": "32200b00f80100000e022c00...",
  "decoded": {
    "template": 1, "count": 2,
    "pokemon": [{"species": "Patrat", "level": 11, "ivs": 6, "move_1": "Work Up"}],
    "trainer_name": "Cheren"
  }
}
```

---

### write

Writes data to a file. Changes stay in memory until save_rom.

**Parameters:**
- `path` (string, required): File path. Same path types as read
- `data` (string, required): Data to write (hex can have spaces: `F8 B5 82 B0`)
- `offset` (integer, optional): Byte offset. Default: 0
- `encoding` (string, optional): hex, utf8, utf16le, ascii. Default: hex

---

### save_rom

Repacks the ROM with all in-memory modifications.

**Parameters:**
- `output_path` (string, required): Path for output ROM

**Behavior:**
1. Recompress ARM9 via blz
2. Repack filesystem via ndspy
3. Write to output_path

---

### hexdump

Raw hex dump with offset+hex+ASCII columns.

**Parameters:**
- `path` (string, optional): File path. Supports arm9.bin, arm7.bin, narc_path:index
- `offset` (integer, optional): Start offset
- `length` (integer, optional): Bytes to dump. Default: 256
- `search` (string, optional): Hex pattern to find, returns all match offsets

---

### search

Searches text tables by name, NARC files by hex pattern, or both combined.

**Parameters:**
- `name` (string, optional): Text to search (e.g. "Pikachu", "Red")
- `table` (string, optional): Limit to one table (species, moves, items, abilities, trainer_names, trainer_classes, natures, type_names, location_names)
- `narc_path` (string, optional): NARC to search
- `hex` (string, optional): Hex pattern to find in NARC
- `exact` (boolean, optional): Whole-string match. Default: false

**Modes:**
- Name only: searches all text tables (or one if `table` specified)
- Hex + narc_path: searches NARC files for byte pattern
- Name + narc_path: resolves name to ID, converts to u16 LE, searches NARC for those bytes

---

### diff

Byte-level comparison of two files.

**Parameters:**
- `path_a` (string, required): First file path
- `path_b` (string, required): Second file path

**Behavior:**
- Supports top-level files, arm9.bin, arm7.bin, narc_path:index
- Returns offset + byte A + byte B for each difference
- Caps at 100 differences

---

## Analysis

### stats

Documentation coverage report for the current Flipnote.

**Parameters:** None

**Behavior:**
- Counts labeled paths vs total files
- Checks for notes with format/structure fields
- Estimates ARM9 byte coverage from note descriptions

---

## Flipnote Operations

Flipnotes are persistent JSON files in `~/.linkplay/flipnotes/`, named after the game's English title. They store the ROM's file tree, stats, and research notes across sessions.

### list_flipnotes

Lists all Flipnote files with game code, title, and note count.

**Parameters:** None

---

### view_flipnote

Shows a Flipnote's header and all notes (skips the file tree).

**Parameters:**
- `game` (string, required): Game code (e.g. "IRE") or title words (e.g. "black 2")

**Behavior:**
- Game codes match exactly
- Title search matches all words independently ("black 2" finds "Pokémon Black Version 2")

---

### note

Adds a note to the current Flipnote. Saves to disk immediately.

**Parameters:**
- `path` (string, required): Path being documented
- `description` (string, required): What this path contains
- `name` (string, optional): Human-readable label
- `format` (string, optional): Data format description
- `tags` (array, optional): Categorization tags
- `file_range` (string, optional): File index range description
- `examples` (array, optional): Example entries
- `related` (array, optional): Related paths

---

### edit_note

Modifies an existing note. Only provided fields are changed.

**Parameters:**
- `path` (string, required): Path of note to edit
- All other note fields optional

---

### delete_note

Removes a note from the Flipnote.

**Parameters:**
- `path` (string, required): Path of note to delete

---
name: linkplay
description: ROM hacking and exploration tool for Nintendo DS, GBA, GBC, and GB ROMs. Treats DS ROMs as filesystems (because they are) via the Nitro File System. Use when user wants to open, explore, edit, or understand ROM file structures. Triggers on mentions of .nds, .gba, .gbc, .gb files, ROM hacking, NARCs, ARM9, game data extraction, or requests to explore/modify game files. Manages Flipnotes (.fpn) - persistent knowledge files that track what Claude learns about each game's data structures.
---

# LinkPlay

ROM exploration and hacking through Claude's interface. "Link" (Link Cable) + "Play" (Download Play) = LinkPlay.

## Core Concept

**DS ROMs are filesystems.** The Nitro File System (NFS) means `.nds` files are essentially zip archives containing folders, NARCs (nested archives), ARM binaries, and overlays. LinkPlay exposes this structure directly.

**GB/GBA/GBC ROMs are byte arrays.** No filesystem - just raw data at offsets. LinkPlay handles both paradigms.

## Text Tables (Gen V)

On open_rom, Gen V games (B/W/B2/W2) decode the entire text NARC (a/0/0/2) into `text_tables`. All 495 files — species, moves, items, abilities, trainer names, trainer classes, natures, type names, location names. This powers auto-decode: `read a/0/9/2:156` returns "Patrat lv11 with Work Up" instead of raw hex.

## Flipnotes (.fpn)

JSON files that persist Claude's knowledge about a game's structure.

- **Created automatically** when opening a ROM for the first time
- **Identified by game code** (IRE, IRD, IRB, etc.) from ROM header
- **Named by game title** (`Pokémon_Black_2.fpn`) — "Version" stripped for pattern matching
- **Structure is immutable** - auto-generated file tree
- **Notes are editable** - Claude documents what each NARC/file contains

## Tools

See `tools.md` for full specifications.

### ROM Operations
- `open_rom(path)` - Load ROM, bootstrap text tables (Gen V), create/load Flipnote
- `close_rom(save?)` - Clear state, optionally save
- `ls(path?, expand_narcs?)` - List contents, pass NARC path to see internal files
- `read(path, offset?, length?, decompress?)` - Read files with auto-decode for known structures
- `write(path, data, offset?, encoding?)` - Modify files (hex, utf8, utf16le, ascii)
- `save_rom(output_path)` - Repack ROM
- `hexdump(path?, offset?, length?, search?)` - Raw hex view with pattern search
- `search(name?, table?, narc_path?, hex?, exact?)` - Text lookup, hex search, or cross-reference
- `diff(path_a, path_b)` - Byte-level comparison

### Analysis
- `stats()` - Documentation coverage report

### Flipnote Operations
- `list_flipnotes()` - Show known games
- `view_flipnote(game)` - Read flipnote (game code or title words)
- `note(path, description, name?, format?, tags?, ...)` - Add knowledge
- `edit_note(path, ...)` - Modify note
- `delete_note(path)` - Remove note

## Auto-decode Paths

When text_tables are loaded, `read` returns decoded data for:
- `a/0/9/1:N` — Trainer data (class, battle type, items, AI, prize money)
- `a/0/9/2:N` — Trainer pokemon (species, level, IVs, moves, held items)
- `a/0/8/2:N` — Wild encounters (species, levels, rates by terrain)
- `a/2/5/0:N`, `a/2/5/3:N` — PWT Rental pool (species, moves, EVs, nature, trainer class)
- `a/2/5/6:N`, `a/2/5/7:N` — PWT Champions pool (species, moves, EVs, nature, held item)

## Dependencies

- **ndspy** - DS ROM/NARC handling, LZ10 fallback
- **CUE's DS/GBA Compressors** - blz, lzss, lzx, huffman, rle (auto-downloaded)
- Standard Python for GB/GBA byte handling

## Compression Support

Auto-detects and handles:
- LZ10 (`0x10`) - Standard DS/GBA
- LZ11/LZ40 (`0x11`, `0x40`) - Newer DS games
- Huffman (`0x20`, `0x28`) - 4-bit and 8-bit
- RLE (`0x30`)
- BLZ - ARM9/overlays

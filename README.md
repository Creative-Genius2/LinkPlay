# LinkPlay

*Link Cable + Download Play*

A general-purpose MCP server for Pokémon games — Gen I through Gen V, GB through DS. Trainers, base stats, learnsets, encounters, moves, items, text tables, battle facilities, and more, across every main-series game from 1996 to 2012.

---

## What It Does

DS ROMs are filesystems — folders, NARCs, structured binary. GB and GBA ROMs are flat binaries with known offsets. LinkPlay handles both. Open a ROM, decode what's inside, write changes back, save. Persistent notes (Flipnotes) mean knowledge carries across sessions.

The same tools work whether you're reading Cheren's team in Black 2, Brock's party in Pocket Monsters Green, or wild encounters in HeartGold. The server figures out the format.

## Supported Games

| Generation | Games | Text Decryption | Auto-Decode |
|------------|-------|-----------------|-------------|
| **Gen V** | Black, White, Black 2, White 2 | ✅ Gen V XOR + ROL3 | ✅ Full |
| **Gen IV** | Diamond, Pearl, Platinum, HeartGold, SoulSilver | ✅ Gen IV XOR + F100 9-bit | ✅ Full |
| **Gen III (GBA)** | FireRed, LeafGreen, Ruby, Sapphire, Emerald | ✅ EN charmap | ✅ Trainers, personal, moves, learnsets |
| **Gen I JP (GB)** | Red JP, Green JP, Blue JP, Yellow JP | ✅ JP charmap (disassembly-verified) | ✅ Personal, trainers, species, moves |
| **Gen I EN (GB)** | Red EN, Blue EN, Yellow EN | ✅ EN charmap | ⚠️ Partial — species/moves only |
| **Gen II (GBC)** | Gold, Silver, Crystal | ✅ EN charmap | ⚠️ Partial — trainers, encounters |

## What You Can Decode

- **Base stats** — HP/Atk/Def/SpA/SpD/Spe, types, abilities, catch rate, EV yield, TM/HM compatibility
- **Learnsets** — level-up moves for every Pokémon
- **Evolutions** — 30 evolution methods, targets, parameters
- **Move data** — power, accuracy, PP, type, category, priority, multi-hit, effect chance
- **Trainer teams** — species, level, IVs, moves, held items, AI flags
- **Wild encounters** — species, levels, rates by terrain/time-of-day, with correct location names
- **Battle facilities** — Battle Tower, Battle Subway, PWT pools and rosters
- **Item data** — prices, fling power
- **Pokéathlon stats** — HGSS performance data
- **Contest data** — DPPt contest Pokémon
- **All text** — species, moves, items, abilities, natures, types, trainer names/classes, location names

## Tools

| Tool | Server Name | What It Does |
|------|-------------|--------------|
| Open ROM | `spotlight` | Load ROM, bootstrap text tables, create flipnote |
| Close ROM | `return` | Clear state, optionally save |
| ROM browsing (without reading) | `summarize` | List folder or NARC contents |
| Reading files in the rom | `decipher` | Read + auto-decode known structures |
| Write | `sketch` | Write hex/text data to files |
| Save | `record` | Repack ROM with modifications |
| Hex Dump | `scope` | Raw bytes with search and XOR |
| Searching | `dowse` | Name → decipher-ready paths across all tables; hex pattern scan |
| Diffing | `judgement` | Byte-level diff, supports cross-ROM |
| Binary Struct Reads | `probe` | Structured read at offset — u8/u16/u32, auto-annotates |
| Sprite Creation | `sprite_convert` | Extract NDS sprites; PNG→NDS conversion (Gen IV, Gen V in dev) |
| Appending files to existing NARC folders | `narc_append` | Add new files to a NARC (HGSS+) |
| Stats | `stats` | Documentation coverage report |
| Flipnote notation (consists of the next 5 tools) | `note` | Add knowledge to current flipnote |
| Batch Notes | `batch_notes` | Write multiple notes at once |
| Edit Note | `edit_note` | Modify existing note |
| Delete Note | `delete_note` | Remove a note |
| List Flipnotes | `list_flipnotes` | See all known games |
| View Flipnote | `view_flipnote` | Read a game's notes |

See `tools.md` for full parameter specs.

## Flipnotes

Persistent `.fpn` files that store what you learn about a ROM across sessions. Open HeartGold, document that `a/1/3/6` contains encounters. Close. Come back a week later. That knowledge is still there.

Paired games share flipnotes — Diamond & Pearl, HeartGold & SoulSilver, Black & White, Black 2 & White 2.

## Eonet (Optional)

The Eonet system (`eonet_driver.py`) is an optional client-side orchestrator that sits between the user and Claude. It uses iterative cross-referencing (ICR) to automatically discover what each NARC file contains by matching binary content against decoded text tables. When a user asks "What's Iris's team?", Eonet resolves `a/0/9/1:47` and `a/0/9/2:47` before Claude even sees the message.

See `docs/ICR.md` for the underlying pattern.

## Setup

### 1. Install uv

```bash
pip install uv
```

### 2. Configure Your MCP Client

Add to your MCP config (Claude Desktop, Antigravity, etc.):

**Standard (no Eonet):**
```json
{
  "mcpServers": {
    "linkplay": {
      "command": "uv",
      "args": [
        "--directory",
        "/path/to/LinkPlay",
        "run",
        "python",
        "scripts/server.py"
      ]
    }
  }
}
```

**With Eonet (automatic routing):**
```json
{
  "mcpServers": {
    "linkplay": {
      "command": "uv",
      "args": [
        "--directory",
        "/path/to/LinkPlay",
        "run",
        "python",
        "eonet_driver.py",
        "--proxy"
      ]
    }
  }
}
```

Replace the path with your actual LinkPlay directory. See `our_mcp_config.json` for a working example.

### 3. Restart Your Client

`uv` automatically installs dependencies and manages the environment. Compression tools are downloaded on first use.

See `INSTALL.md` for detailed setup and troubleshooting.

## In Practice

Open HeartGold. Ask for Route 43's encounters. Get back:

```
Route 43
Grass (Default):
  FLAAFFY             Lv. 15-17   40% (Day) / 30% (Morning, Night)
  GIRAFARIG           Lv. 15      30%
  PIDGEOTTO           Lv. 17      25% (Morning) / 20% (Day)
  ...
```

Open Black 2. Read Iris's champion team. Get species, levels, IVs, moves, held items, AI flags. Search for every trainer using Garchomp. Compare Garchomp's base stats between HeartGold and Black 2 with both ROMs open at once.

Document what you find. Come back a week later. It's all still there.

## Dependencies

**Python packages** (auto-installed by `uv`):
- `mcp` — Model Context Protocol
- `ndspy` — DS ROM/NARC handling
- `aiohttp` — HTTP proxy for Eonet
- `cryptography` — TLS cert generation for Eonet
- `Pillow` — PNG sprite conversion
- `spacy` — NLP for Eonet resolution
- `curl-cffi` — Cloudflare bypass for tool downloads

**Compression tools** (auto-downloaded on first run):
CUE's DS/GBA Compressors — blz, lzss, lzx, huffman, rle

## Status

Tested against 14 Pokémon ROMs — Gen I through Gen V, GB through DS. Decodes trainers, encounters, base stats, learnsets, evolutions, moves, items, battle facilities, and all text. Location name resolution verified for DP, Pt, HGSS, BW, and B2W2.

---

*Named for the link cable that connected Game Boys and the Download Play that shared DS games. Connection. Sharing. Play.*

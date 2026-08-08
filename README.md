# Silphéon: A MCP Server For Pokémon fans

A general-purpose MCP server for Pokémon games — Gen I through Gen IX, Game Boy through Nintendo Switch. Trainers, base stats, learnsets, encounters, moves, items, text tables, battle facilities, and more, across every main-series game from 1996 to 2025.

---

## What It Does

Switch ROMs are encrypted containers — XCI/NSP with NCA, RomFS, and FlatBuffer data inside. DS and 3DS ROMs are filesystems — folders, NARCs/GARCs, structured binary. GB and GBA ROMs are flat binaries with known offsets. Silphéon handles all of them. Open a ROM, decode what's inside, write changes back, save. Persistent notes (Flipnotes) mean knowledge carries across sessions.

The same tools work whether you're reading Cheren's team in Black 2, Brock's party in Pokémon Green, or wild encounters in HeartGold. The server figures out the format.

## Supported Games

| Generation | Games | Platform | Auto-Decode |
|------------|-------|----------|-------------|
| **Gen IX** | Legends: Z-A | Switch | ✅ Full (FlatBuffer) |
| **Gen IX** | Scarlet, Violet | Switch | ✅ Full (FlatBuffer) |
| **Gen VIII** | Legends: Arceus | Switch | ✅ Full (FlatBuffer) |
| **Gen VIII** | Sword, Shield | Switch | ✅ Full (FlatBuffer) |
| **Gen VII** | Let's Go Pikachu, Let's Go Eevee | Switch | ✅ Full (FlatBuffer) |
| **Gen VII** | Sun, Moon, Ultra Sun, Ultra Moon | 3DS | ✅ Full |
| **Gen VI** | X, Y, Omega Ruby, Alpha Sapphire | 3DS | ✅ Full |
| **Gen V** | Black, White, Black 2, White 2 | DS | ✅ Full |
| **Gen IV** | Diamond, Pearl, Platinum, HeartGold, SoulSilver | DS | ✅ Full |
| **Gen III** | Ruby, Sapphire, Emerald, FireRed, LeafGreen | GBA | ✅ Full |
| **Gen II** | Gold, Silver, Crystal | GBC | ⚠️ Partial |
| **Gen I** | Red, Blue, Green, Yellow (JP + EN) | GB | ✅ Full |

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

The Eonet (`eonet_driver.py`) is an optional proxy that sits between the user and Claude. It uses iterative cross-referencing (ICR) to automatically discover what each NARC file contains by matching binary content against decoded text tables. When a user asks Claude "What's Iris's team?", Eonet resolves `a/0/9/1:47` and `a/0/9/2:47` before the model even sees the message.

See `docs/ICR.md` for details on how ICR works.

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
    "Silphéon": {
      "command": "uv",
      "args": [
        "--directory",
        "/path/to/Silphéon",
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
    "Silphéon": {
      "command": "uv",
      "args": [
        "--directory",
        "/path/to/Silphéon",
        "run",
        "python",
        "eonet_driver.py",
        "--proxy"
      ]
    }
  }
}
```

Replace the path with your actual Silphéon directory.

### 3. Restart Your Client

`uv` automatically installs dependencies and manages the environment. Compression tools are downloaded on first use.

See `INSTALL.md` for detailed setup and troubleshooting.

## In Practice

Open Sword. Ask for Route 1's encounters. Get species, levels, weather conditions, and overworld/grass split. Open HeartGold. Ask for Route 43's encounters. Get back:

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
- `cryptography` — TLS cert generation for Eonet; Switch NCA decryption
- `pycryptodome` — Switch NCA/XCI decryption (AES-CTR, AES-ECB)
- `Pillow` — PNG sprite conversion
- `spacy` — NLP for Eonet resolution
- `curl-cffi` — Cloudflare bypass for tool downloads

**Compression tools** (auto-downloaded on first run via setup_tools.py):
downloads CUE's DS/GBA Compressors including blz, lzss, lzx, huffman, rle

## Status

Tested against 20+ Pokémon ROMs — Gen I through Gen IX, Game Boy through Nintendo Switch. Decodes trainers, encounters, base stats, learnsets, evolutions, moves, items, battle facilities, and all text. Location name resolution verified for DP, Pt, HGSS, BW, and B2W2.
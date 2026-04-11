# LinkPlay

*Link Cable + Download Play*

An MCP server that lets AI explore, decode, and modify Nintendo ROMs as navigable filesystems.

---

## What It Does

DS ROMs aren't mysterious binary blobs — they're filesystems. The Nitro File System gives them folders, files, and structure. Tools like Tinke have known this for years. LinkPlay brings that to AI.

Open a ROM. Browse its file tree. Read encounter tables, trainer teams, and base stats. Decode text — species names, move names, location names. Write changes back. Save a modified ROM. Come back later and remember what you found.

For GB/GBA/GBC — no filesystem there, just bytes. LinkPlay handles that too.

## Supported Games

| Generation | Games | Text Decryption | Auto-Decode |
|------------|-------|-----------------|-------------|
| **Gen IV** | Diamond, Pearl, Platinum, HeartGold, SoulSilver | ✅ Gen IV XOR + F100 9-bit | ✅ Full |
| **Gen V** | Black, White, Black 2, White 2 | ✅ Gen V XOR + ROL3 | ✅ Full |
| **GBA/GB/GBC** | Any ROM | — | Hex only |

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
| Browse | `summarize` | List folder or NARC contents |
| Read | `decipher` | Read + auto-decode known structures |
| Write | `sketch` | Write hex/text data to files |
| Save | `record` | Repack ROM with modifications |
| Hex Dump | `scope` | Raw bytes with search and XOR |
| Search | `dowse` | Find text by name, hex patterns in NARCs |
| Compare | `judgement` | Byte-level diff, supports cross-ROM |
| Stats | `stats` | Documentation coverage report |
| Note | `note` | Add knowledge to current flipnote |
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

See `docs/EONET.md` for details.

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

Replace the path with your actual LinkPlay directory. See `mcp_config.example.json` for full config with comments.

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
- `curl-cffi` — Cloudflare bypass for tool downloads

**Compression tools** (auto-downloaded on first run):
CUE's DS/GBA Compressors — blz, lzss, lzx, huffman, rle

## Status

Tested against all 9 Gen IV/V Pokémon DS games. Decodes trainers, encounters, base stats, learnsets, evolutions, moves, items, battle facilities, and all text. Location name resolution verified for DP, Pt, HGSS, BW, and B2W2.

---

*Named for the link cable that connected Game Boys and the Download Play that shared DS games. Connection. Sharing. Play.*

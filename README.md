# LinkPlay

*Link Cable + Download Play*

An MCP server that lets Claude open Nintendo ROMs like folders.

---

## The Idea

DS ROMs aren't mysterious binary blobs. They're filesystems. The Nitro File System gives them actual folders, actual files, actual structure. Tools like Tinke have known this for years.

But Claude couldn't see it. Until now.

LinkPlay treats `.nds` files the way they deserve to be treated: as archives you can browse, explore, and understand. Open a ROM. See its structure. Read the files inside. Learn what each NARC contains. Write that knowledge down. Come back later and remember.

For GB/GBA/GBC—no filesystem there, just bytes. LinkPlay handles that too. Different paradigm, same interface.

## Why This Matters

ROM documentation is fragmented. Scattered across dead forums, wrong wikis, and outdated tools. Gen V data gets confused with Gen IV. Obscure games have almost nothing.

What if Claude could explore ROMs directly? Document what it finds? Build up knowledge over time?

That's LinkPlay.

## Text Tables (Gen V)

When opening a Gen V ROM (Black, White, Black 2, White 2), LinkPlay decodes the entire text NARC (a/0/0/2) — all 495 files — into memory. Species names, move names, item names, trainer names, ability names, everything. This makes `read` able to show you "Pikachu" instead of "0x1900" when reading trainer data. The text decryption uses a per-entry XOR key derived from the species file.

## Flipnotes

When Claude opens a game for the first time, LinkPlay creates a Flipnote—a `.fpn` file that stores:

- The complete file structure (auto-generated, immutable)
- Notes Claude adds about what each path contains (editable, persistent)

Open Pokemon Black 2. Claude explores `a/0/9/1`, figures out it's trainer data, notes it. Close. Come back a week later. That knowledge is still there.

Flipnotes are identified by game code but named by title. `Pokémon_Black_2.fpn` is human-readable. The game code inside (`IRE`) is what actually links it to any Black 2 ROM, regardless of filename.

Share Flipnotes. Correct bad documentation. Build institutional memory for ROM research.

## Tools

**ROM Operations:**
- `open_rom` / `close_rom` — load ROM, bootstrap text tables, clean up
- `ls` / `read` / `write` — navigate and modify (read auto-decodes known structures)
- `save_rom` — repack with changes
- `hexdump` — raw bytes when you need them
- `search` — find text by name, hex patterns in NARCs, or cross-reference both
- `diff` — compare files byte-by-byte

**Analysis:**
- `stats` — documentation coverage report

**Flipnote Operations:**
- `list_flipnotes` / `view_flipnote` — see what Claude knows
- `note` / `edit_note` / `delete_note` — document discoveries

See `tools.md` for full specifications.

## Dependencies

**Python packages** (auto-installed by `uv`):
- `mcp` — Model Context Protocol
- `ndspy` — DS ROM/NARC handling, LZ10 fallback

**Compression tools** (auto-downloaded on first run):
LinkPlay automatically downloads CUE's DS/GBA Compressors (GPL licensed):

| Tool | Format | Header | Usage |
|------|--------|--------|-------|
| `blz` | BLZ | (tail) | ARM9/overlays |
| `lzss` | LZ10 | `0x10` | Standard DS/GBA |
| `lzx` | LZ11/LZ40 | `0x11`, `0x40` | Newer DS games |
| `huffman` | Huffman | `0x20`, `0x28` | Various |
| `rle` | RLE | `0x30` | Various |

The download uses Selenium with undetected-chromedriver to bypass Cloudflare on romhacking.net. If the RAR already exists in your Downloads folder, it skips the browser entirely.

Compression is completely transparent - Claude works with decompressed data, and LinkPlay automatically recompresses on save/close.

**Supported platforms:**
- Nintendo DS (.nds) - Full filesystem support
- Game Boy Advance (.gba) - Byte-level access
- Game Boy Color (.gbc) - Byte-level access
- Game Boy (.gb) - Byte-level access

## Setup

### 1. Install uv (Python package manager)

```bash
pip install uv
```

Or download from: https://docs.astral.sh/uv/

### 2. Configure Claude Desktop

Edit your Claude Desktop config:

**Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
**macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
**Linux:** `~/.config/Claude/claude_desktop_config.json`

Add:
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

Replace the path with your actual LinkPlay directory path.

### 3. Restart Claude Desktop

That's it! `uv` automatically installs dependencies and manages the environment. Compression tools are downloaded on first use.

See `INSTALL.md` for detailed setup and troubleshooting.

## Status

Works. Tested against Pokemon Black 2, White 2, and Black. Has decoded trainer data, wild encounters, PWT tournament pools, and ARM9 patches. Text decryption verified against all 495 Gen V text files.

What's missing will become obvious when people actually use it.

---

*Named for the link cable that connected Game Boys and the Download Play that shared DS games. Connection. Sharing. Play.*

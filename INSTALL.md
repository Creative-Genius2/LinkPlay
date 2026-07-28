# LinkPlay Installation Guide

## Quick Start

### 1. Install uv (Python package manager)

```bash
pip install uv
```

Or download from: https://docs.astral.sh/uv/getting-started/installation/

### 2. Configure Your MCP Client

LinkPlay works with any MCP-compatible client. Add it to your client's MCP config.

#### Claude Desktop

Edit: `%APPDATA%\Claude\claude_desktop_config.json` (Windows)
or: `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS)
or: `~/.config/Claude/claude_desktop_config.json` (Linux)

#### Antigravity / Other MCP Clients

See `mcp_config.example.json` for the config format.

#### Config Entry

```json
{
  "mcpServers": {
    "linkplay": {
      "command": "uv",
      "args": [
        "--directory",
        "/absolute/path/to/LinkPlay",
        "run",
        "python",
        "scripts/server.py"
      ]
    }
  }
}
```

> **Important:** Replace the path with the actual absolute path to your LinkPlay directory.
> On Windows, use double backslashes: `"C:\\Users\\you\\LinkPlay"`

### 3. Restart Your Client

On first run, `uv` will:
- Create a virtual environment in `.venv/`
- Install all dependencies from `requirements.txt`
- Start the LinkPlay server
- Auto-download compression tools on first ROM open

### 4. Test It

Open your client and try:
```
Open the ROM at C:\path\to\your\game.nds
```

## Dependencies

All Python packages are auto-installed by `uv`:

| Package | Purpose |
|---------|---------|
| `mcp` | Model Context Protocol server framework |
| `ndspy` | DS ROM/NARC parsing and repacking |
| `aiohttp` | HTTP proxy for Eonet Method B |
| `cryptography` | TLS cert generation for Eonet |
| `curl-cffi` | Cloudflare bypass for tool downloads |
| `rarfile` | RAR archive extraction |

### Compression Tools

CUE's DS/GBA Compressors (blz, lzss, lzx, huffman, rle) are **auto-downloaded** on first ROM open. If auto-download fails:

1. Download manually from: https://www.romhacking.net/utilities/826/
2. Extract the RAR file
3. Copy the `.exe` files to `scripts/tools/win32/`
4. Restart your client

## Testing Manually

To verify the server starts correctly:

```bash
cd /path/to/LinkPlay
uv run python scripts/server.py
```

The server will report compression tool status and wait for MCP messages on stdin. Press `Ctrl+C` to stop.

## Troubleshooting

### "uv: command not found"

```bash
pip install uv
```

### "Module not found: setup_tools"

The `--directory` path must point to the LinkPlay **root** directory (not `scripts/`).

### Tools don't appear in client

1. Check that the config path is absolute
2. Restart the client completely
3. Check client logs for startup errors

### Compression tools not found

Tools auto-download on first ROM open. If it fails due to network issues:
- Download manually from https://www.romhacking.net/utilities/826/
- Extract to `scripts/tools/win32/`

### Cross-platform

LinkPlay is developed on Windows. The compression tools are Windows executables (`.exe`). For macOS/Linux, you'll need to compile CUE's compressors from source or use Wine.

## File Structure

```
LinkPlay/
├── scripts/
│   ├── server.py          # MCP server — all tools and decoders
│   ├── setup_tools.py     # Compression tool auto-download
│   └── tools/win32/       # Compression executables (auto-populated)
├── eonet_driver.py        # Optional: client-side orchestrator
├── docs/                  # Eonet documentation
├── mcp_config.example.json
├── requirements.txt
├── README.md
├── INSTALL.md
├── tools.md               # Tool parameter reference
└── SKILL.md               # AI skill reference
```

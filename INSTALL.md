# LinkPlay Installation Guide

## Quick Start

### 1. Install uv (Python package manager)

**Windows:**
```bash
pip install uv
```

Or download from: https://docs.astral.sh/uv/getting-started/installation/

### 2. Configure Claude Desktop

Edit your Claude Desktop config file:

**Windows:** `%APPDATA%\Claude\claude_desktop_config.json`  
**macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`  
**Linux:** `~/.config/Claude/claude_desktop_config.json`

Add LinkPlay to the `mcpServers` section:

```json
{
  "mcpServers": {
    "linkplay": {
      "command": "uv",
      "args": [
        "--directory",
        "C:\\Users\\prado\\Downloads\\LinkPlay",
        "run",
        "python",
        "scripts/server.py"
      ]
    }
  }
}
```

**Important:** Replace the path with the actual absolute path to your LinkPlay directory.

### 3. Restart Claude Desktop

Close and reopen Claude Desktop completely. On first run, `uv` will:
- Create a virtual environment in `.venv/`
- Install `mcp`, `ndspy`, and `undetected-chromedriver` automatically from `requirements.txt`
- Start the LinkPlay server
- Auto-download compression tools (uses Selenium to bypass Cloudflare, extracts RAR automatically)

### 4. Test It

In Claude Desktop, try:
```
Can you list the available LinkPlay tools?
```

You should see tools like `open_rom`, `ls`, `read`, `write`, etc.

## How It Works

`uv` is like `npx` for Python - it automatically:
- Creates isolated virtual environments
- Installs dependencies from `requirements.txt`
- Runs the server with the correct Python environment

No manual `pip install` needed! Everything is automatic.

## Testing the Server Manually

To test the server directly (without Claude Desktop):

```bash
cd C:\Users\prado\Downloads\LinkPlay
uv run python scripts/server.py
```

The server should start and show:
```
Compression tools not found. Attempting automatic download...
Found existing RAR file: C:\Users\YourName\Downloads\Nintendo_DS_Compressors_v1.4-CUE.rar
Using RAR file: C:\Users\YourName\Downloads\Nintendo_DS_Compressors_v1.4-CUE.rar
Downloading UnRAR.exe...
Extracting tools to scripts/tools/win32...
  Extracted: blz.exe
  Extracted: lzss.exe
  Extracted: lzx.exe
  Extracted: huffman.exe
  Extracted: rle.exe
Compression tools installed successfully (5 tools)
```

Or if the RAR isn't in Downloads, it will use Selenium:
```
Downloading CUE's DS/GBA Compressors using Selenium...
Starting browser to download file (this bypasses Cloudflare)...
Navigating to https://www.romhacking.net/download/utilities/826/...
Waiting for download to complete...
Download complete: [path]
```

Or if tools are already installed:
```
Compression tools already installed
```

Then it will wait for MCP protocol messages on stdin.

Press `Ctrl+C` to stop.

## Troubleshooting

### "uv: command not found"

Install uv:
```bash
pip install uv
```

Or follow: https://docs.astral.sh/uv/getting-started/installation/

### "Module not found: setup_tools"

Make sure the `--directory` path in your config points to the LinkPlay root directory (not the scripts folder).

### Claude Desktop doesn't see the tools

1. Check the config file path is correct
2. Make sure the `--directory` path is absolute
3. Restart Claude Desktop completely
4. Check Claude Desktop logs for errors

### Compression tools not found

The tools will auto-download on first use. If automatic download fails:

**Reason:** Cloudflare protection or network issues

**Solution:**
1. Manually download from: https://www.romhacking.net/utilities/826/
2. Extract the RAR file (use WinRAR, 7-Zip, or similar)
3. Copy these files to `scripts/tools/win32/`:
   - blz.exe
   - lzss.exe
   - lzx.exe
   - huffman.exe
   - rle.exe
4. Restart Claude Desktop

The server will detect the bundled tools and skip automatic download.

### Testing with a ROM

Once configured, in Claude Desktop:

```
Open the ROM at C:\path\to\your\game.nds
```

Claude will use the `open_rom` tool and you can start exploring!

## What Happens on First Run

1. `uv` creates `.venv/` directory
2. `uv` installs `mcp`, `ndspy`, and `undetected-chromedriver` from `requirements.txt`
3. Server starts
4. `setup_tools.py` checks for compression tools
5. If missing, automatically:
   - Checks user's Downloads folder for existing RAR file
   - If not found, uses Selenium with undetected-chromedriver to download (bypasses Cloudflare)
   - Downloads 7-Zip command-line tool
   - Extracts tools to `scripts/tools/win32/`
   - Cleans up temporary files
6. Server runs with full compression support

If automatic download fails, manual installation instructions are shown.

## Next Steps

- Read `README.md` for usage examples
- Check `tools.md` for detailed tool documentation
- See `SKILL.md` for Claude's understanding of LinkPlay

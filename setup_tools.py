#!/usr/bin/env python3
"""
Auto-download and setup CUE's DS/GBA compression tools.
Called automatically on server startup if tools are missing.
"""

import os
import platform
import stat
import subprocess
import sys
import time
from pathlib import Path

# Redirect all print output to stderr to avoid corrupting MCP JSON-RPC on stdout
_builtin_print = print
def print(*args, **kwargs):
    """Override print to use stderr by default for MCP compatibility."""
    kwargs.setdefault('file', sys.stderr)
    _builtin_print(*args, **kwargs)


# Direct download URL for CUE's tools (RAR archive, protected by Cloudflare)
TOOLS_DOWNLOAD_URL = "https://www.romhacking.net/download/utilities/826/"

# Expected RAR filename in Downloads folder
RAR_FILENAME = "Nintendo_DS_Compressors_v1.4-CUE.rar"

# UnRAR for Windows (official from rarlab.com)
UNRAR_URL = "https://www.rarlab.com/rar/unrarw32.exe"

# Tools directory relative to this script
TOOLS_DIR = Path(__file__).resolve().parent / "tools"


def get_platform_name():
    """Get normalized platform name for tool selection."""
    system = platform.system().lower()
    if system == "windows":
        return "win32"
    elif system == "darwin":
        return "darwin"
    elif system == "linux":
        return "linux"
    return system


def get_tool_names():
    """Get list of required tool names with platform-specific extensions."""
    tools = ['blz', 'lzss', 'lzx', 'huffman', 'rle']
    if platform.system() == "Windows":
        return [f"{tool}.exe" for tool in tools]
    return tools


def check_tools_installed():
    """Check if all required tools are present."""
    platform_name = get_platform_name()
    platform_dir = TOOLS_DIR / platform_name
    
    print(f"[DEBUG] Checking tools in: {platform_dir}", file=sys.stderr)
    
    if not platform_dir.exists():
        print(f"[DEBUG] Platform dir does not exist", file=sys.stderr)
        return False
    
    tool_names = get_tool_names()
    for tool in tool_names:
        tool_path = platform_dir / tool
        if not tool_path.exists():
            print(f"[DEBUG] Missing tool: {tool_path}", file=sys.stderr)
            return False
    
    print(f"[DEBUG] All tools found!", file=sys.stderr)
    return True


def find_rar_in_downloads():
    """
    Check if the RAR file already exists in the user's Downloads folder.
    Returns path if found, None otherwise.
    """
    # Common Downloads folder locations
    downloads_paths = [
        Path.home() / "Downloads",
        Path.home() / "Download",
        Path(os.path.expandvars("%USERPROFILE%")) / "Downloads",
    ]
    
    for downloads_dir in downloads_paths:
        if downloads_dir.exists():
            rar_path = downloads_dir / RAR_FILENAME
            if rar_path.exists():
                print(f"Found existing RAR file: {rar_path}")
                return rar_path
    
    return None


def download_with_selenium(url: str, output_path: Path, timeout: int = 60):
    """
    Download file using Selenium with undetected-chromedriver to bypass Cloudflare.
    Returns True if successful, False otherwise.
    """
    try:
        import undetected_chromedriver as uc
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
    except ImportError:
        print("undetected-chromedriver not found. Install with: pip install undetected-chromedriver")
        return False
    
    driver = None
    try:
        print("Starting browser to download file (this bypasses Cloudflare)...")
        
        # Setup Chrome options for download
        options = uc.ChromeOptions()
        options.add_argument('--headless=new')  # Run in background
        options.add_argument('--disable-gpu')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        
        # Set download directory
        download_dir = str(output_path.parent.absolute())
        prefs = {
            "download.default_directory": download_dir,
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "safebrowsing.enabled": False
        }
        options.add_experimental_option("prefs", prefs)
        
        # Create undetected Chrome driver
        driver = uc.Chrome(options=options, version_main=None)
        
        print(f"Navigating to {url}...")
        driver.get(url)
        
        # Wait for download to start (file should appear in temp location)
        print("Waiting for download to complete...")
        start_time = time.time()
        
        # Check for downloaded file
        while time.time() - start_time < timeout:
            # Check if file exists (might have .crdownload extension while downloading)
            if output_path.exists():
                # Wait a bit more to ensure download is complete
                time.sleep(2)
                if output_path.stat().st_size > 0:
                    print(f"Download complete: {output_path}")
                    return True
            
            # Check for .crdownload file (Chrome partial download)
            crdownload = output_path.with_suffix(output_path.suffix + '.crdownload')
            if crdownload.exists():
                print("Download in progress...")
            
            time.sleep(1)
        
        print("Download timeout reached")
        return False
        
    except Exception as e:
        print(f"Selenium download failed: {e}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        return False
    finally:
        if driver:
            driver.quit()


def download_and_extract_tools():
    """
    Download CUE's tools from romhacking.net and extract them.
    Multi-tier approach:
    1. Check if RAR exists in Downloads folder
    2. If not, use Selenium UC to download it
    3. Download UnRAR.exe if needed
    4. Extract using rarfile + UnRAR
    Returns True if successful, False otherwise.
    """
    platform_name = get_platform_name()
    platform_dir = TOOLS_DIR / platform_name
    platform_dir.mkdir(parents=True, exist_ok=True)
    
    temp_rar = None
    temp_unrar = None
    
    try:
        # Import rarfile
        try:
            import rarfile
        except ImportError:
            print("rarfile module not found. Install with: pip install rarfile")
            return False
        
        # Step 1: Check if RAR file already exists in Downloads
        rar_path = find_rar_in_downloads()
        
        if not rar_path:
            # Step 2: Download using Selenium
            print(f"Downloading CUE's DS/GBA Compressors using Selenium...")
            temp_rar = TOOLS_DIR / RAR_FILENAME
            
            if not download_with_selenium(TOOLS_DOWNLOAD_URL, temp_rar):
                print("Selenium download failed")
                return False
            
            rar_path = temp_rar
        
        print(f"Using RAR file: {rar_path}")
        
        # Step 3: Download UnRAR.exe if not present
        unrar_exe = TOOLS_DIR / "unrar.exe"
        if not unrar_exe.exists():
            print("Downloading UnRAR.exe...")
            import urllib.request
            with urllib.request.urlopen(UNRAR_URL, timeout=30) as response:
                with open(unrar_exe, 'wb') as f:
                    f.write(response.read())
        
        # Configure rarfile to use WinRAR's UnRAR.exe (if installed) or our downloaded one
        winrar_unrar = Path("C:/Program Files/WinRAR/UnRAR.exe")
        if winrar_unrar.exists():
            rarfile.UNRAR_TOOL = str(winrar_unrar)
        else:
            rarfile.UNRAR_TOOL = str(unrar_exe)
        
        print(f"Extracting tools to {platform_dir}...")
        
        # Extract RAR file
        with rarfile.RarFile(rar_path) as rf:
            extracted_count = 0
            for file_info in rf.infolist():
                filename = file_info.filename
                
                # Extract Windows .exe files
                if platform_name == "win32" and filename.lower().endswith('.exe'):
                    tool_name = os.path.basename(filename).lower()
                    if any(tool in tool_name for tool in ['blz', 'lzss', 'lzx', 'huffman', 'rle']):
                        # Extract to temp location
                        rf.extract(file_info, TOOLS_DIR)
                        
                        # Move to platform directory root
                        extracted_path = TOOLS_DIR / filename
                        target_path = platform_dir / os.path.basename(filename)
                        
                        if extracted_path.exists():
                            if extracted_path != target_path:
                                extracted_path.rename(target_path)
                            print(f"  Extracted: {os.path.basename(filename)}")
                            extracted_count += 1
        
        # Clean up temp files
        if temp_rar and temp_rar.exists():
            temp_rar.unlink(missing_ok=True)
        
        # Clean up any subdirectories created during extraction
        for item in TOOLS_DIR.iterdir():
            if item.is_dir() and item != platform_dir:
                import shutil
                shutil.rmtree(item, ignore_errors=True)
        
        # Make tools executable on Unix
        if platform_name in ('linux', 'darwin'):
            for tool_file in platform_dir.glob('*'):
                if tool_file.is_file():
                    tool_file.chmod(tool_file.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        
        if extracted_count > 0 and check_tools_installed():
            print(f"Compression tools installed successfully ({extracted_count} tools)")
            return True
        else:
            print("Warning: Some tools may not have been extracted correctly")
            return False
            
    except Exception as e:
        print(f"Failed to download/extract tools: {e}")
        import traceback
        traceback.print_exc(file=sys.stderr)
        # Clean up temp files if they exist
        if temp_rar and temp_rar.exists():
            temp_rar.unlink(missing_ok=True)
        return False


def setup_tools():
    """
    Check for compression tools and download if missing.
    Returns True if tools are ready, False if not found.
    
    IMPORTANT: This function is called at server startup.
    All output goes to stderr to avoid corrupting MCP stdio.
    """
    # FIRST: Check if tools exist - if yes, return immediately
    if check_tools_installed():
        return True
    
    # Tools not installed - attempt auto-download
    print("Compression tools not found. Attempting automatic download...", file=sys.stderr)
    
    if download_and_extract_tools():
        return True
    
    # Download failed, provide manual instructions
    platform_name = get_platform_name()
    platform_dir = TOOLS_DIR / platform_name
    
    print(f"""
Automatic download failed. Manual installation required:

1. Download from: https://www.romhacking.net/utilities/826/
2. Extract the binaries for your platform
3. Place them in: {platform_dir}
4. Required files: blz, lzss, lzx, huffman, rle

The server will work with LIMITED compression support (LZ10 only via ndspy).
Full support requires the external tools.
""", file=sys.stderr)
    
    return False


def get_tool_path(tool_name):
    """
    Get the full path to a compression tool.
    Returns tool path if found, otherwise returns just the tool name.
    """
    platform_name = get_platform_name()
    platform_dir = TOOLS_DIR / platform_name
    
    if platform.system() == "Windows" and not tool_name.endswith('.exe'):
        tool_name = f"{tool_name}.exe"
    
    tool_path = platform_dir / tool_name
    
    if tool_path.exists():
        return str(tool_path)
    
    # Fall back to PATH
    return tool_name


if __name__ == "__main__":
    # When run directly (not as MCP server), allow interactive download
    print("LinkPlay Tool Setup")
    print("=" * 40)
    
    if check_tools_installed():
        print("All compression tools are already installed!")
    else:
        print("Compression tools not found. Attempting download...")
        if download_and_extract_tools():
            print("\nSetup complete!")
        else:
            platform_name = get_platform_name()
            platform_dir = TOOLS_DIR / platform_name
            print(f"""
Manual installation required:

1. Download from: https://www.romhacking.net/utilities/826/
2. Extract the binaries for your platform
3. Place them in: {platform_dir}
4. Required files: blz, lzss, lzx, huffman, rle
""")
    
    print(f"\nTool paths:")
    for tool in ['blz', 'lzss', 'lzx', 'huffman', 'rle']:
        path = get_tool_path(tool)
        exists = Path(path).exists() if os.path.isabs(path) else False
        status = "FOUND" if exists else "NOT FOUND"
        print(f"  {tool}: {path} [{status}]")
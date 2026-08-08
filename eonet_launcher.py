import sys, os, subprocess
from pathlib import Path

silphéon_dir = Path(__file__).parent
cert_path = Path.home() / ".silphéon" / "eonet_ssl" / "cert.pem"

uv = sys.executable.replace("python.exe", "uv.exe")
if not Path(uv).exists():
    import shutil
    uv = shutil.which("uv") or "uv"

# Run setup if cert doesn't exist — task is already elevated so no UAC
if not cert_path.exists():
    subprocess.run([uv, "run", "python", "eonet_driver.py", "--setup"],
                   cwd=str(silphéon_dir))

# Start proxy
subprocess.run([uv, "run", "pythonw", "eonet_driver.py", "--proxy"],
               cwd=str(silphéon_dir))

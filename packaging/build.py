"""Build the packaged app into dist/: a folder plus a .zip (Windows/macOS) or .tar.gz (Linux).

    python packaging/build.py             # editor + runner + scheduler
    python packaging/build.py --headless  # runner + scheduler only (Linux servers)
"""

import argparse
import os
import platform
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from taskloom import __version__  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--headless", action="store_true", help="leave out the editor (for servers)")
    args = parser.parse_args()
    dist, work = ROOT / "dist", ROOT / "build"
    shutil.rmtree(dist / "taskloom", ignore_errors=True)
    env = {**os.environ, "TASKLOOM_HEADLESS": "1" if args.headless else "0"}
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--distpath", str(dist), "--workpath", str(work),
                    str(ROOT / "packaging" / "taskloom.spec")], check=True, env=env)
    folder = dist / "taskloom"
    (folder / "VERSION").write_text(__version__)
    shutil.copytree(ROOT / "examples", folder / "examples", ignore=shutil.ignore_patterns("out"))
    shutil.copy(ROOT / "README.md", folder / "README.md")
    shutil.copy(ROOT / "LICENSE", folder / "LICENSE")

    system = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
    name = f"taskloom{'-runner' if args.headless else ''}-{__version__}-{system}-{platform.machine().lower()}"
    if system == "linux":  # tar keeps the programs executable
        archive = dist / f"{name}.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(folder, arcname="taskloom")
    else:
        archive = Path(shutil.make_archive(str(dist / name), "zip", dist, "taskloom"))
    print(f"built {folder}\npackaged {archive}")


if __name__ == "__main__":
    main()

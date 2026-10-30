"""Build the packaged app into dist/: a folder plus a .zip (Windows/macOS) or .tar.gz (Linux).

    python packaging/build.py             # editor + runner + scheduler
    python packaging/build.py --headless  # runner + scheduler only (Linux servers)
"""

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
from importlib import metadata
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
    if not args.headless:  # the editor's Git (dulwich, with urllib3) and Mermaid engine (mmdr, with novasvg)
        for name in ("dulwich", "urllib3", "mmdr", "novasvg"):  # their licence texts, and mmdr's component list
            for file in metadata.distribution(name).files:
                if file.parent.name in ("licenses", "sboms"):
                    (folder / "licenses" / name).mkdir(parents=True, exist_ok=True)
                    shutil.copy(file.locate(), folder / "licenses" / name / file.name)
        mermaid_notices(folder / "licenses" / "mmdr")

    system = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
    # No version in the name, so flows that deploy it don't change with each release; it is in VERSION.
    name = f"taskloom{'-runner' if args.headless else ''}-{system}-{platform.machine().lower()}"
    if system == "linux":  # tar keeps the programs executable
        archive = dist / f"{name}.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(folder, arcname="taskloom")
    else:
        archive = Path(shutil.make_archive(str(dist / name), "zip", dist, "taskloom"))
    print(f"built {folder}\npackaged {archive}")


def mermaid_notices(folder: Path):
    """mmdr's Rust components under the MPL-2.0 or the EPL-2.0 (shipped unmodified): those licences' texts and
    SOURCES.txt, where each component's source is, from mmdr's component list (the SBOM in its wheel)."""
    for text in ("MPL-2.0.txt", "EPL-2.0.txt"):
        shutil.copy(ROOT / "packaging" / "licenses" / text, folder / text)
    [sbom] = [f for f in metadata.distribution("mmdr").files if f.parent.name == "sboms"]
    lines = []
    for c in json.loads(sbom.read_text(encoding="utf-8"))["components"]:
        licence = " AND ".join(entry.get("expression", "") for entry in c.get("licenses", []))
        if re.search(r"\b(MPL|EPL)-2\.0\b", licence):
            repository = "".join(f" ({r['url']})" for r in c.get("externalReferences", []) if r["type"] == "vcs")
            lines.append(f"{c['name']} {c['version']} ({licence}): https://crates.io/crates/{c['name']}/{c['version']}"
                         f"{repository}")
    (folder / "SOURCES.txt").write_text(
        "mmdr includes these Rust components unmodified, under the Mozilla Public License 2.0 (MPL-2.0.txt) or the "
        "Eclipse Public License 2.0 (EPL-2.0.txt).\nTheir source code (each version's crate, and its repository):\n\n"
        + "\n".join(lines) + f"\n\nAll of mmdr's components and their licences: {sbom.name}\n", encoding="utf-8")


if __name__ == "__main__":
    main()

# PyInstaller recipe: one folder with `taskloom` (console) and, unless headless, `taskloomw` (no console).
# Build with: python packaging/build.py [--headless]
import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

headless = os.environ.get("TASKLOOM_HEADLESS") == "1"
root = os.path.dirname(SPECPATH)

hidden = collect_submodules("holidays") + collect_submodules("taskloom")
excludes = ["tkinter", "pytest", "IPython"]
if headless:  # the Linux server runner: no editor, no Qt
    hidden = [m for m in hidden if not m.startswith("taskloom.editor")]
    excludes += ["PySide6", "shiboken6", "taskloom.editor"]

a = Analysis(
    [os.path.join(SPECPATH, "entry.py")],
    pathex=[root],
    hiddenimports=hidden,
    datas=collect_data_files("holidays"),
    excludes=excludes,
)
pyz = PYZ(a.pure)
programs = [EXE(pyz, a.scripts, [], exclude_binaries=True, name="taskloom", console=True)]
if not headless:
    programs.append(EXE(pyz, a.scripts, [], exclude_binaries=True, name="taskloomw", console=False))
COLLECT(*programs, a.binaries, a.datas, name="taskloom")

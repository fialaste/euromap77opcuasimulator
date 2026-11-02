# PyInstaller spec: one-file console executable with bundled NodeSets, profiles and the web UI.
# Build: pyinstaller packaging/emsim.spec  (output in dist/)
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

root = Path(SPECPATH).parent
pkg = root / "emsim"

a = Analysis(
    [str(root / "packaging" / "entry.py")],
    pathex=[str(root)],
    datas=[
        (str(pkg / "nodesets"), "emsim/nodesets"),
        (str(pkg / "profiles"), "emsim/profiles"),
        (str(pkg / "ui" / "static"), "emsim/ui/static"),
    ],
    hiddenimports=collect_submodules("emsim") + collect_submodules("asyncua.ua"),
    excludes=["tkinter", "matplotlib", "numpy", "PIL", "IPython", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="emsim",
    debug=False,
    strip=False,
    upx=False,
    console=True,
    runtime_tmpdir=None,
)

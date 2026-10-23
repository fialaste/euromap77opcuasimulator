# PyInstaller spec: one-file console executable with bundled NodeSets, profiles and the web UI.
# Build: pyinstaller packaging/e77sim.spec  (output in dist/)
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

root = Path(SPECPATH).parent
pkg = root / "e77sim"

a = Analysis(
    [str(root / "packaging" / "entry.py")],
    pathex=[str(root)],
    datas=[
        (str(pkg / "nodesets"), "e77sim/nodesets"),
        (str(pkg / "profiles"), "e77sim/profiles"),
        (str(pkg / "ui" / "static"), "e77sim/ui/static"),
    ],
    hiddenimports=collect_submodules("e77sim") + collect_submodules("asyncua.ua"),
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
    name="e77sim",
    debug=False,
    strip=False,
    upx=False,
    console=True,
    runtime_tmpdir=None,
)

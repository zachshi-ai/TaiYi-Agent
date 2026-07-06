# PyInstaller spec for the Taiyi macOS .app.
#   pyinstaller deploy/macos/Taiyi.spec --noconfirm
# Produces dist/Taiyi.app — an unsigned, double-clickable app that starts the
# gateway and opens the bundled web UI. (See deploy/macos/build.sh.)
import os
from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, "..", ".."))
SRC = os.path.join(ROOT, "src", "taiyi")

# All bundled data uses __file__-relative paths inside the `taiyi` package, so we
# ship each directory at its in-package relative location. web/dist lives OUTSIDE
# the package; the launcher points at it via sys._MEIPASS/web/dist.
datas = [
    (os.path.join(SRC, "rules"), "taiyi/rules"),
    (os.path.join(SRC, "scenarios", "catalog"), "taiyi/scenarios/catalog"),
    (os.path.join(SRC, "skills", "catalog"), "taiyi/skills/catalog"),
    (os.path.join(SRC, "value_stream", "value_streams.yaml"), "taiyi/value_stream"),
    (os.path.join(ROOT, "web", "dist"), "web/dist"),
]

# Grab every taiyi submodule (several are imported lazily: tools.SandboxExecutor,
# llm.live, mcp, …) plus optional deps so a configured live model works.
hiddenimports = collect_submodules("taiyi") + ["yaml"]
try:
    import httpx  # noqa: F401
    hiddenimports += collect_submodules("httpx") + ["httpcore"]
except Exception:
    pass

a = Analysis(
    [os.path.join(SPECPATH, "taiyi_launcher.py")],
    pathex=[os.path.join(ROOT, "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Taiyi",
    console=False,          # windowed app (no terminal)
    disable_windowed_traceback=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Taiyi")

app = BUNDLE(
    coll,
    name="Taiyi.app",
    icon=None,              # drop a .icns path here to customize
    bundle_identifier="ai.zachshi.taiyi",
    info_plist={
        "CFBundleName": "Taiyi",
        "CFBundleDisplayName": "Taiyi / The One",
        "CFBundleShortVersionString": "0.1.0",
        "NSHighResolutionCapable": True,
        # Keep a normal Dock app; the server runs while the app is open.
        "LSBackgroundOnly": False,
    },
)

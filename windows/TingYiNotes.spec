"""Windows one-folder build; run from the repository root on Windows."""

from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs
from pathlib import Path


project_root = Path(SPECPATH).resolve().parent


whisper_data, whisper_binaries, whisper_hidden = collect_all("faster_whisper")
ctranslate_data, ctranslate_binaries, ctranslate_hidden = collect_all("ctranslate2")
cuda_binaries = collect_dynamic_libs("nvidia.cublas") + collect_dynamic_libs("nvidia.cudnn")

a = Analysis(
    [str(project_root / "windows" / "launcher.py")],
    pathex=[str(project_root / "src")],
    binaries=whisper_binaries + ctranslate_binaries + cuda_binaries,
    datas=whisper_data + ctranslate_data + [
        (str(project_root / "assets" / "brand" / "tingyiji-icon-256.png"), "assets/brand"),
    ],
    hiddenimports=whisper_hidden + ctranslate_hidden + ["soundcard", "sounddevice"],
    hookspath=[],
    runtime_hooks=[],
    excludes=["mlx_whisper", "mlx", "torch"],
    noarchive=False,
)
# Qt's Windows build uses the OS ICU C API. A developer's unrelated PATH
# (for example Poppler) may contain an incompatible icuuc.dll; never ship it.
a.binaries = [
    entry for entry in a.binaries
    if Path(entry[0]).name.lower() not in {"icuuc.dll", "icudt78.dll"}
]
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TingYiNotes",
    icon=str(project_root / "assets" / "brand" / "tingyiji.ico"),
    console=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="TingYiNotes-Windows")

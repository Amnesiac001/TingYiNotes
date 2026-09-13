from PyInstaller.utils.hooks import collect_all
from pathlib import Path


project_root = Path(SPECPATH).resolve().parent


mlx_data, mlx_binaries, mlx_hidden = collect_all("mlx_whisper")
brand_data = [(str(project_root / "assets" / "brand" / "tingyiji-icon-256.png"), "assets/brand")]

a = Analysis(
    [str(project_root / "macos" / "launcher.py")],
    pathex=[str(project_root / "src")],
    binaries=mlx_binaries,
    datas=mlx_data + brand_data,
    hiddenimports=mlx_hidden + ["mlx.core", "sounddevice"],
    hookspath=[],
    runtime_hooks=[],
    excludes=["faster_whisper", "ctranslate2", "soundcard"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TingYiNotes",
    icon=str(project_root / "assets" / "brand" / "tingyiji.icns"),
    console=False,
    target_arch="arm64",
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="TingYiNotes")
app = BUNDLE(
    coll,
    name="听译记.app",
    icon=str(project_root / "assets" / "brand" / "tingyiji.icns"),
    bundle_identifier="com.tingyiji.notes",
    info_plist={
        "CFBundleDisplayName": "听译记",
        "CFBundleName": "听译记",
        "NSHighResolutionCapable": True,
        "NSMicrophoneUsageDescription": "听译记需要使用麦克风实时记录、翻译并整理课堂内容。",
    },
)

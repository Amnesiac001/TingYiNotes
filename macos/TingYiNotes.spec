from PyInstaller.utils.hooks import collect_all


mlx_data, mlx_binaries, mlx_hidden = collect_all("mlx_whisper")
brand_data = [("assets/brand/tingyiji-icon-256.png", "assets/brand")]

a = Analysis(
    ["macos/launcher.py"],
    pathex=["."],
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
    icon="assets/brand/tingyiji.icns",
    console=False,
    target_arch="arm64",
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="TingYiNotes")
app = BUNDLE(
    coll,
    name="听译记.app",
    icon="assets/brand/tingyiji.icns",
    bundle_identifier="com.tingyiji.notes",
    info_plist={
        "CFBundleDisplayName": "听译记",
        "CFBundleName": "听译记",
        "NSHighResolutionCapable": True,
        "NSMicrophoneUsageDescription": "听译记需要使用麦克风实时记录、翻译并整理课堂内容。",
    },
)

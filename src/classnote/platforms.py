from __future__ import annotations

import platform
import sys


IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"
IS_APPLE_SILICON = IS_MACOS and platform.machine().lower() in {"arm64", "aarch64"}


def local_speech_name() -> str:
    if IS_MACOS:
        return "本地 Apple 芯片"
    return "本地 RTX"


def local_speech_option() -> str:
    if IS_MACOS:
        return "本地 Apple 芯片实时识别"
    return "本地 RTX 实时识别"


def microphone_permission_hint() -> str:
    if IS_MACOS:
        return "请在 macOS“系统设置 → 隐私与安全性 → 麦克风”中允许听译记或终端访问。"
    return "请在 Windows“隐私和安全性 → 麦克风”中允许桌面应用访问。"

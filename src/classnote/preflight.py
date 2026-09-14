from __future__ import annotations

from dataclasses import dataclass

from .config import Settings, verify_output_directory
from .live import AudioDevice, list_input_devices


@dataclass(frozen=True)
class PreflightCheck:
    name: str
    state: str
    detail: str


def run_quick_preflight(settings: Settings) -> list[PreflightCheck]:
    """Free local checks only. Never download a model or call a paid API."""
    checks: list[PreflightCheck] = []
    try:
        devices: list[AudioDevice] = list_input_devices()
        microphones = sum(not device.is_loopback for device in devices)
        checks.append(PreflightCheck(
            "录音设备", "通过" if devices else "需处理",
            f"找到 {microphones} 个麦克风、{len(devices) - microphones} 个系统声音源；请在实时页测试实际音量。"
            if devices else "没有可用输入设备；检查麦克风权限或连接。",
        ))
    except Exception as exc:
        checks.append(PreflightCheck("录音设备", "需处理", f"读取设备失败：{exc}"))
    if settings.live_mode == "local":
        checks.append(PreflightCheck(
            "英文识别", "待验证", "本地模型需在设置页下载并预热；此快速检查不会自动下载。",
        ))
    else:
        checks.append(PreflightCheck(
            "英文识别", "待验证" if settings.api_key else "需处理",
            "已配置语音 API Key；仍需实际连接测试。" if settings.api_key
            else "云端语音模式缺少 OpenAI API Key。",
        ))
    checks.append(PreflightCheck(
        "中文翻译", "待验证" if settings.text_api_key and settings.text_model else "需处理",
        f"已填写 {settings.text_provider} · {settings.text_model}；请在设置页测试连接。"
        if settings.text_api_key and settings.text_model else "缺少文本模型或 API Key；英文字幕仍可保存。",
    ))
    try:
        target = verify_output_directory(settings.export_dir)
        checks.append(PreflightCheck("笔记输出", "通过", f"可写：{target}"))
    except Exception as exc:
        checks.append(PreflightCheck("笔记输出", "需处理", str(exc)))
    return checks

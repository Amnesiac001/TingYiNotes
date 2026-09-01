#!/bin/zsh
set -e

cd "$(dirname "$0")"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "此安装脚本只能在 macOS 上运行。"
  exit 1
fi

if [[ "$(uname -m)" != "arm64" ]]; then
  echo "当前是 Intel Mac。本地 MLX 实时识别需要 M1 或更新的 Apple 芯片。"
  echo "你仍可安装后在设置中选择 OpenAI 云端实时语音。"
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "没有找到 Python 3.11 或更新版本。请先安装 Python。"
  exit 1
fi

python3 - <<'PY'
import sys
if sys.version_info < (3, 11):
    raise SystemExit("听译记需要 Python 3.11 或更新版本。")
PY

if ! command -v ffmpeg >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1; then
    echo "正在安装音频文件解析组件 ffmpeg……"
    brew install ffmpeg
  else
    echo "提示：没有检测到 ffmpeg。实时麦克风仍可使用；导入 mp3/mp4 前请安装 Homebrew 和 ffmpeg。"
  fi
fi

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip setuptools wheel
.venv/bin/python -m pip install -e ".[macos]"

if [[ ! -f .env ]]; then
  cp .env.example .env
fi

echo
echo "安装完成。双击“启动听译记-macOS.command”即可打开。"
echo "首次录音时，请允许终端或听译记使用麦克风。"
read -k 1 "?按任意键关闭……"

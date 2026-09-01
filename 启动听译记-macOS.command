#!/bin/zsh
set -e

cd "$(dirname "$0")"

if [[ ! -x .venv/bin/python ]]; then
  echo "尚未安装。请先双击“安装听译记-macOS.command”。"
  read -k 1 "?按任意键关闭……"
  exit 1
fi

log_path="听译记-启动日志.log"
.venv/bin/python -m classnote.qt_gui 2>&1 | tee "$log_path"
app_status=${pipestatus[1]}

if [[ $app_status -ne 0 ]]; then
  echo
  echo "听译记未能正常启动，诊断日志已保存到：$log_path"
  read -k 1 "?按任意键关闭……"
fi

exit $app_status

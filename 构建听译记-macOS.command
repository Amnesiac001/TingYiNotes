#!/bin/zsh
set -e

cd "$(dirname "$0")"

if [[ ! -x .venv/bin/pyinstaller ]]; then
  echo "尚未安装打包组件。请先运行“安装听译记-macOS.command”。"
  exit 1
fi

iconutil -c icns assets/brand/tingyiji.iconset -o assets/brand/tingyiji.icns
rm -rf build/TingYiNotes build/dmg-root "dist/听译记.app"
.venv/bin/pyinstaller --noconfirm macos/TingYiNotes.spec
mkdir -p build/dmg-root
cp -R "dist/听译记.app" build/dmg-root/
ln -s /Applications build/dmg-root/Applications
hdiutil create \
  -volname "听译记" \
  -srcfolder build/dmg-root \
  -ov \
  -format UDZO \
  "dist/听译记-macOS-AppleSilicon.dmg"

echo
echo "构建完成：dist/听译记-macOS-AppleSilicon.dmg"
echo "别人只需下载并打开这个 DMG。当前版本未签名，首次需右键应用选择“打开”。"
open dist

#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# 解释器可移植：优先 $PYTHON，其次 PATH 里的 python3，最后系统自带
PY="${PYTHON:-$(command -v python3 || echo /usr/bin/python3)}"
APP="$HERE/LocalNotes.app"
CONTENTS="$APP/Contents"
MACOS="$CONTENTS/MacOS"
RES="$CONTENTS/Resources"
APP_SRC="$HERE/../app"

# 1) 杀掉旧实例，避免覆盖可执行文件时 ETXTBSY（坑 2）
pkill -f "LocalNotes.app/Contents/MacOS/LocalNotes" 2>/dev/null || true
sleep 1

# 2) 建目录（不 rm -rf 整个 .app，避免批量删除保护，见坑 3）
mkdir -p "$MACOS" "$RES/app" 2>/dev/null || true

# 3) 编译外壳（通用二进制：Intel + Apple Silicon）
#    只编译单架构也能跑，但通用二进制在任何 Mac 上都不需要 Rosetta。
echo "→ 编译 Swift 外壳（x86_64 + arm64）…"
cd "$HERE/shell"
# 临时产物放 /tmp：脚本末尾不做批量删除（环境有 safe-delete 拦截，rm 多个文件会让 set -e 中断，
# 曾因此跳过签名步骤，打出一个「未签名」的包）
TMPX="/tmp/localnotes-build-x86_64"; TMPA="/tmp/localnotes-build-arm64"
rm -f "$TMPX" 2>/dev/null || true
rm -f "$TMPA" 2>/dev/null || true
/usr/bin/xcrun swiftc -O -target x86_64-apple-macosx11.0 main.swift -o "$TMPX" 2>&1 | grep -v "warning:" || true
/usr/bin/xcrun swiftc -O -target arm64-apple-macosx11.0  main.swift -o "$TMPA" 2>&1 | grep -v "warning:" || true
if [ -x "$TMPX" ] && [ -x "$TMPA" ]; then
  /usr/bin/lipo -create "$TMPX" "$TMPA" -output "$MACOS/LocalNotes"
else
  echo "  (arm64 交叉编译失败，退回单架构)"
  /usr/bin/xcrun swiftc -O main.swift -o "$MACOS/LocalNotes"
fi
chmod +x "$MACOS/LocalNotes"
echo "  架构: $(/usr/bin/lipo -archs "$MACOS/LocalNotes" 2>/dev/null || echo unknown)"

# 4) 先生成图标（写回 app/static/icon-*.png，必须在拷贝代码之前！）
#    当前默认：B 硬壳笔记本；换方案：ICON_VARIANT=A bash build.sh（A 折角笔记 / C 卡片笔记）
echo "→ 生成图标…"
# 图标依赖 Pillow。没有 Pillow 就跳过——沿用仓库里已有的 AppIcon.png，
# 不让整条构建链因为一个可选步骤失败（这一步以前用 rm -rf，害过整包没签名）。
ICON_PY="${ICON_PYTHON:-$PY}"
if "$ICON_PY" -c "import PIL" >/dev/null 2>&1; then
  "$ICON_PY" "$HERE/make_icon.py" --variant "${ICON_VARIANT:-B}" \
    || echo "  (图标生成失败，沿用已有 AppIcon.png)"
else
  echo "  (缺 Pillow，跳过重新生成，沿用已有 AppIcon.png；需要时: pip install pillow)"
fi

# 5) 拷入 web 工程（server.py + static + importers + sample.enex）
echo "→ 拷贝应用代码…"
mkdir -p "$RES/app"
# 用 rsync 排除 __pycache__/*.pyc：以前是 cp 完再 find -delete，
# 那是「批量删除」，一旦命中环境的 safe-delete 拦截就会让 set -e 中断，
# 后面「内嵌运行时 + 签名」整段被跳过，于是打出一个没签名的包。
# 同时排除开发期产生在 app/ 里的 notes.db 与 assets/（数据目录），
# 否则会把本地数据一起分发给别人——即使是示例数据也不该躺在安装包里。
/usr/bin/rsync -a --exclude '__pycache__' --exclude '*.pyc' \
  --exclude '.DS_Store' --exclude 'notes.db*' --exclude 'assets/' \
  "$APP_SRC/" "$RES/app/"
# 把 importers 包一并打进 bundle（server.py 用 import importers，见坑 8）
/usr/bin/rsync -a --exclude '__pycache__' --exclude '*.pyc' \
  "$HERE/../importers" "$RES/app/"
# 清掉上几次构建残留的旧数据文件（数量少，不会触发批量删除保护）
rm -f "$RES/app/notes.db" "$RES/app/notes.db-shm" "$RES/app/notes.db-wal" 2>/dev/null || true
rm -rf "$RES/app/assets" 2>/dev/null || true

# 6) Info.plist + 由主图切 icns
cp "$HERE/Info.plist" "$CONTENTS/Info.plist"

if [ -f "$HERE/AppIcon.png" ]; then
  # 临时 iconset 放 /tmp 且**不删**：这里原来 rm -rf 一个含 590 个 png 的目录，
  # 被 safe-delete 拦截 → set -e 中断 → 后面内嵌运行时和签名全被跳过。
  ICONSET="$(mktemp -d /tmp/localnotes-iconset.XXXXXX)"
  for s in 16 32 128 256 512; do
    /usr/bin/sips -z $s $s "$HERE/AppIcon.png" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null 2>&1
  done
  /usr/bin/sips -z 32 32 "$HERE/AppIcon.png" --out "$ICONSET/icon_16x16@2x.png" >/dev/null 2>&1
  /usr/bin/sips -z 64 64 "$HERE/AppIcon.png" --out "$ICONSET/icon_32x32@2x.png" >/dev/null 2>&1
  /usr/bin/sips -z 256 256 "$HERE/AppIcon.png" --out "$ICONSET/icon_128x128@2x.png" >/dev/null 2>&1
  /usr/bin/sips -z 512 512 "$HERE/AppIcon.png" --out "$ICONSET/icon_256x256@2x.png" >/dev/null 2>&1
  /usr/bin/sips -z 1024 1024 "$HERE/AppIcon.png" --out "$ICONSET/icon_512x512@2x.png" >/dev/null 2>&1
  /usr/bin/iconutil -c icns "$ICONSET" -o "$RES/AppIcon.icns" 2>/dev/null || true
fi

# 6) 内嵌 Python 运行时（通用二进制、已裁剪到 ~35MB）
#    有 runtime/python 就自动带上——这是 dmg 能装到「没装 Python 的 Mac」上的关键。
#    想跳过：BUNDLE_PYTHON=0 bash build.sh
if [ "${BUNDLE_PYTHON:-1}" = "1" ] && [ -d "$HERE/runtime/python" ]; then
  echo "→ 内嵌 Python 运行时…"
  rm -rf "$RES/python"
  /usr/bin/ditto "$HERE/runtime/python" "$RES/python"
  echo "  体积: $(/usr/bin/du -sh "$RES/python" | cut -f1)  架构: $(/usr/bin/lipo -archs "$RES/python/bin/python3.13" 2>/dev/null)"
else
  echo "→ 跳过内嵌 Python（目标机需自带 python3）"
fi

# 7) 本地自签名，绕过 Gatekeeper（无 sandbox 权利，ad-hoc 即可）
echo "→ 自签名…"
/usr/bin/codesign --force --deep --sign - "$APP" 2>/dev/null || echo "  (codesign 跳过)"

# 8) 收尾自检：可分发包的四个前提，任一不成立就直接失败
#    （曾经因为 set -e 中途中断，静默产出一个「没运行时、没签名」的包）
echo "→ 收尾自检…"
FAIL=0
[ -x "$RES/python/bin/python3" ]      || { echo "  ✗ 缺少内嵌 Python 运行时"; FAIL=1; }
[ -d "$RES/app/importers" ]           || { echo "  ✗ 缺少 importers 包"; FAIL=1; }
[ -f "$RES/app/static/index.html" ]   || { echo "  ✗ 缺少前端页面"; FAIL=1; }
if [ -e "$RES/app/notes.db" ] || [ -d "$RES/app/assets" ]; then
  echo "  ✗ 包里混进了本地数据（notes.db / assets），不能分发"
  FAIL=1
fi
if ! /usr/bin/codesign --verify --deep --strict "$APP" 2>/dev/null; then
  echo "  ✗ 签名无效"; FAIL=1
fi
[ "$FAIL" = "0" ] || { echo "✗ 打包未通过自检，已中止"; exit 1; }
echo "  自检通过：运行时 + 代码 + 签名 就绪"

# 9) 刷新 LaunchServices / 图标缓存
touch "$APP"
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP" 2>/dev/null || true
killall Dock 2>/dev/null || true; killall Finder 2>/dev/null || true

echo "✅ 打包完成: $APP"

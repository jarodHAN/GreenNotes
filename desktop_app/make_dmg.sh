#!/usr/bin/env bash
# 把 LocalNotes.app 打成可直接分发的 dmg（拖进「应用程序」即完成安装）
#   bash make_dmg.sh            默认版本号取 Info.plist
#   VERSION=1.1 bash make_dmg.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
APP="$HERE/LocalNotes.app"
DIST="$HERE/dist"
VER="${VERSION:-$(/usr/libexec/PlistBuddy -c 'Print CFBundleShortVersionString' "$HERE/Info.plist" 2>/dev/null || echo 1.0)}"
VOL="离线笔记本 ${VER}"
OUT="$DIST/LocalNotes-${VER}.dmg"

[ -d "$APP" ] || { echo "✗ 找不到 $APP，先跑 bash build.sh"; exit 1; }
mkdir -p "$DIST"

echo "→ 校验签名…"
if ! /usr/bin/codesign --verify --deep --strict "$APP" 2>/dev/null; then
  echo "  ✗ 签名无效，拒绝打包：这样的 dmg 装到别人机器上会提示「已损坏」"
  echo "    请先跑 bash build.sh（它会做收尾自检）"
  exit 1
fi
echo "  签名有效（ad-hoc）"
[ -x "$APP/Contents/Resources/python/bin/python3" ] \
  || { echo "  ✗ 包里没有内嵌 Python 运行时，这个 dmg 到别的 Mac 上起不来"; exit 1; }

# 用临时目录做暂存，避免反复删大目录
STAGE="$(mktemp -d /tmp/localnotes-dmg.XXXXXX)"
trap 'rm -rf "$STAGE"' EXIT
echo "→ 组装暂存目录 $STAGE"
/usr/bin/ditto "$APP" "$STAGE/LocalNotes.app"
ln -s /Applications "$STAGE/Applications"
cat > "$STAGE/安装说明.txt" <<'TXT'
离线笔记本 LocalNotes · 安装说明
──────────────────────────────────────
【安装】
  把左边的 LocalNotes.app 拖到右边的 Applications 文件夹，
  然后在「启动台 / 应用程序」里打开它。

【第一次打开被系统拦住怎么办】
  这个 App 没有 Apple 付费开发者证书，第一次会被 Gatekeeper 拦一下：
    · 在「应用程序」里 **右键点 LocalNotes → 打开 → 再点「打开」**，以后就不会再问。
    · 若提示「已损坏，无法打开」，打开「终端」执行下面这行再打开：
        xattr -dr com.apple.quarantine /Applications/LocalNotes.app

【数据在哪】
  所有笔记都在本机：~/Library/Application Support/LocalNotes
  （删除 App 不会删笔记；换电脑迁移时，把这个文件夹一起拷过去即可。）

【系统要求】
  macOS 11 (Big Sur) 或更新；Intel 与 Apple Silicon 均可，已内置运行环境，
  目标电脑无需安装 Python 或任何依赖，也不需要联网。
TXT

echo "→ 生成 dmg…"
/usr/bin/hdiutil create -volname "$VOL" -srcfolder "$STAGE" -ov \
  -fs HFS+ -format UDZO -imagekey zlib-level=9 "$OUT" >/dev/null

echo "→ 校验 dmg…"
/usr/bin/hdiutil verify "$OUT" >/dev/null && echo "  dmg 结构完好"
/usr/bin/codesign --sign - "$OUT" 2>/dev/null && echo "  dmg 已 ad-hoc 签名" || true

echo
echo "✅ 完成: $OUT  ($(/usr/bin/du -h "$OUT" | cut -f1))"
echo "   挂载看内容: hdiutil attach \"$OUT\""

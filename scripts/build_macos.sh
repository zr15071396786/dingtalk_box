#!/usr/bin/env bash
# scripts/build_macos.sh — 本地 Mac dev wrapper，镜像 CI 行为
# 用法: ./scripts/build_macos.sh [arm64|x86_64|universal2|all]
#   arm64       仅 Apple Silicon
#   x86_64      仅 Intel（需 Intel Mac 或 arch -x86_64 shell）
#   universal2  arm64 + x86_64 + lipo 合并（默认）
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"

TARGET="${1:-universal2}"

# 1) Python deps
python3 -m pip install -q -r requirements.txt
if [ -f requirements-mac.txt ]; then
  python3 -m pip install -q -r requirements-mac.txt
fi
python3 -m pip install -q "pyinstaller>=6.0"

# 2) 准备 logo.icns（缺失时 sips 生成）
if [ ! -f assets/logo.icns ]; then
  echo "[build_macos] 生成 assets/logo.icns ..."
  if [ ! -f assets/logo.png ]; then
    sips -s format png --resampleHeightWidthMax 1024 \
      assets/logo.ico --out assets/logo.png || true
  fi
  rm -rf assets/logo.iconset
  mkdir -p assets/logo.iconset
  sips -z 16 16     assets/logo.png --out assets/logo.iconset/icon_16x16.png        > /dev/null
  sips -z 32 32     assets/logo.png --out assets/logo.iconset/icon_16x16@2x.png     > /dev/null
  sips -z 32 32     assets/logo.png --out assets/logo.iconset/icon_32x32.png        > /dev/null
  sips -z 64 64     assets/logo.png --out assets/logo.iconset/icon_32x32@2x.png     > /dev/null
  sips -z 128 128   assets/logo.png --out assets/logo.iconset/icon_128x128.png      > /dev/null
  sips -z 256 256   assets/logo.png --out assets/logo.iconset/icon_128x128@2x.png   > /dev/null
  sips -z 256 256   assets/logo.png --out assets/logo.iconset/icon_256x256.png      > /dev/null
  sips -z 512 512   assets/logo.png --out assets/logo.iconset/icon_256x256@2x.png   > /dev/null
  sips -z 512 512   assets/logo.png --out assets/logo.iconset/icon_512x512.png      > /dev/null
  sips -z 1024 1024 assets/logo.png --out assets/logo.iconset/icon_512x512@2x.png   > /dev/null
  iconutil -c icns assets/logo.iconset -o assets/logo.icns
fi

build_one() {
  local ARCH="$1"
  echo "[build_macos] === build $ARCH ==="
  pyinstaller launcher-mac.spec  --clean --noconfirm --target-arch "$ARCH"
  pyinstaller sidecar-mac.spec   --clean --noconfirm --target-arch "$ARCH"
  pyinstaller ai_bridge-mac.spec --clean --noconfirm --target-arch "$ARCH"

  mkdir -p "stage-$ARCH"
  [ -d dist/dingtalk_box.app ] && cp -R dist/dingtalk_box.app "stage-$ARCH/"
  [ -f dist/sidecar   ] && cp dist/sidecar   "stage-$ARCH/sidecar-$ARCH"
  [ -f dist/ai_bridge ] && cp dist/ai_bridge "stage-$ARCH/ai_bridge-$ARCH"
}

case "$TARGET" in
  arm64)
    build_one arm64
    ;;
  x86_64)
    build_one x86_64
    ;;
  universal2|all)
    build_one arm64
    build_one x86_64

    echo "[build_macos] === lipo merge ==="
    mkdir -p deliver/dingtalk_box_mac
    rm -rf deliver/dingtalk_box_mac/dingtalk_box.app
    cp -R stage-arm64/dingtalk_box.app deliver/dingtalk_box_mac/

    lipo -create -output deliver/dingtalk_box_mac/dingtalk_box.app/Contents/MacOS/sidecar \
      stage-arm64/sidecar-arm64 stage-x86_64/sidecar-x86_64
    lipo -create -output deliver/dingtalk_box_mac/dingtalk_box.app/Contents/MacOS/ai_bridge \
      stage-arm64/ai_bridge-arm64 stage-x86_64/ai_bridge-x86_64

    lipo -info deliver/dingtalk_box_mac/dingtalk_box.app/Contents/MacOS/sidecar
    lipo -info deliver/dingtalk_box_mac/dingtalk_box.app/Contents/MacOS/ai_bridge

    cp stage-arm64/sidecar-arm64   deliver/dingtalk_box_mac/sidecar   2>/dev/null || true
    cp stage-arm64/ai_bridge-arm64 deliver/dingtalk_box_mac/ai_bridge 2>/dev/null || true
    ;;
  *)
    echo "用法: $0 [arm64|x86_64|universal2|all]"
    exit 1
    ;;
esac

echo "[build_macos] === ad-hoc codesign ==="
codesign --force --deep --sign - \
  --entitlements assets/entitlements.mac.plist \
  deliver/dingtalk_box_mac/dingtalk_box.app 2>/dev/null || true

echo "[build_macos] done. 产物: deliver/dingtalk_box_mac/"
ls -la deliver/dingtalk_box_mac/

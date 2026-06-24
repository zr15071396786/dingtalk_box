"""对 robot-hero / robot-mini 抠图生成透明 PNG

依赖:
    pip install rembg pillow
u2net 模型放到:
    C:\\Users\\lesoon\\.u2net\\u2net.onnx

注意: rembg 即使开 alpha_matting 也会把浅紫色渐变背景切掉（气泡左右两翼）
- 第一步: rembg alpha_matting=True 抠主体
- 第二步: post-processing 把被误判为透明、但实际是紫色气泡的像素捞回来

颜色判定 (HSV):
  H in 230~290 (蓝紫)  S > 0.12  V > 0.35
"""
import sys
import io
from pathlib import Path

from rembg import remove
from PIL import Image, ImageFilter
import colorsys

ASSETS = Path("assets")
DESKTOP = Path.home() / "Desktop"
# (输入路径, 输出文件名, 目标宽, 目标高)
TARGETS = [
    (DESKTOP / "01.png", ASSETS / "robot-hero.png", 480, 452),
    (DESKTOP / "02.png", ASSETS / "robot-mini.png", 240, 218),
]

# alpha_matting 参数（v0.3.7：更激进保留前景 + 更小 erode_size 保边缘细节）
MATTING_KWARGS = dict(
    alpha_matting=True,
    alpha_matting_foreground_threshold=250,   # 240 → 250：更激进保留前景像素
    alpha_matting_background_threshold=10,
    alpha_matting_erode_size=5,               # 10 → 5：少腐蚀，保留更多边缘细节
)

# 紫色气泡 HSV 范围
PURPLE_HSV = dict(h_min=230, h_max=290, s_min=0.12, v_min=0.35)


def restore_purple_ring(out: Image.Image):
    """对 rembg 抠完的图做后处理：把被误判为透明、但实际是紫色的像素捞回来。
    返回 (恢复像素数, 新图)。"""
    img = out.copy()  # rembg 返回的是 readonly，必须 copy
    w, h = img.size
    px = img.load()
    restored = 0
    h_min, h_max = PURPLE_HSV["h_min"], PURPLE_HSV["h_max"]
    s_min, v_min = PURPLE_HSV["s_min"], PURPLE_HSV["v_min"]
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a > 0:
                continue
            if r > 245 and g > 245 and b > 245:
                continue  # 跳过纯白背景
            h_n, s_n, v_n = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
            h_deg = h_n * 360
            if h_min <= h_deg <= h_max and s_n > s_min and v_n > v_min:
                px[x, y] = (r, g, b, 255)
                restored += 1
    return restored, img


def remove_bg(src: Path, dst: Path, target_w: int, target_h: int):
    """src: 输入 PNG 路径; dst: 输出 PNG 路径。返回 (原文件大小, 输出大小, 透明%, 恢复紫色像素)"""
    orig_size = src.stat().st_size

    img = Image.open(src).convert("RGBA")
    out = remove(img, **MATTING_KWARGS)

    restored, out = restore_purple_ring(out)

    # v0.3.7：alpha 边缘高斯模糊 → 柔化抠图硬边
    r, g, b, a = out.split()
    a = a.filter(ImageFilter.GaussianBlur(radius=1.5))
    out = Image.merge("RGBA", (r, g, b, a))

    out = out.resize((target_w, target_h), Image.Resampling.LANCZOS)

    out.save(dst, "PNG", optimize=True)

    alpha = out.split()[3]
    a_bytes = alpha.tobytes()
    transparent = a_bytes.count(b"\x00")
    total = len(a_bytes)
    trans_pct = 100 * transparent / total

    new_size = dst.stat().st_size
    return orig_size, new_size, trans_pct, restored


if __name__ == "__main__":
    # Windows 终端默认 GBK, 强制 UTF-8 输出
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
        sys.stderr = sys.stdout

    for src, dst, w, h in TARGETS:
        print(f"=== {dst.name} (from {src.name}) ===")
        if not src.exists():
            print(f"  SKIP: {src} 不存在")
            print()
            continue
        orig, new, pct, restored = remove_bg(src, dst, w, h)
        print(f"  src size:  {orig:,} bytes")
        print(f"  out size:  {new:,} bytes")
        print(f"  alpha=0 (transparent): {pct:.1f}%")
        print(f"  restored purple pixels: {restored:,}")
        print(f"  -> {'OK' if pct > 5 else 'WARN'}")
        print()

#!/usr/bin/env python3
"""从托盘源图标生成全套品牌图标：favicon、macOS icns、Windows ico、托盘与 og 图。

设计：近黑墨色圆角底（#0B0E0C）+ 琥珀波形（#FFB000）。源图是白色模板 PNG（alpha 通道即笔划）。
托盘专用变体反转配色（琥珀满幅底 + 墨色加粗波形）：Windows 任务栏多为深色，
墨底徽章会与任务栏融为一体、细线波形在 16-20px 缩放下不可读。
用法：python3 packaging/assets/gen_icons.py  （需 Pillow，仅生成期依赖）
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / 'macos/Assets/ResearchIcon.png'
OUT = ROOT / 'packaging'
WEB = ROOT / 'website'

INK = (11, 14, 12, 255)        # #0B0E0C
AMBER = (255, 176, 0, 255)     # #FFB000


def recolor(src, color):
    """用源图 alpha 作为笔划蒙版，填充目标色。"""
    alpha = src.getchannel('A')
    mark = Image.new('RGBA', src.size, color)
    mark.putalpha(alpha)
    return mark


def badge(size, radius_ratio=0.225, pad_ratio=0.10):
    """圆角墨底 + 居中琥珀波形，输出 size×size。"""
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=int(size * radius_ratio), fill=INK)
    src = Image.open(SRC).convert('RGBA')
    side = int(size * (1 - 2 * pad_ratio))
    mark = recolor(src.resize((side, side), Image.LANCZOS), AMBER)
    off = (size - side) // 2
    img.alpha_composite(mark, (off, off))
    return img


def tray_badge(size=512, radius_ratio=0.22, pad_ratio=0.045, dilate=31):
    """托盘专用：琥珀满幅圆角底 + 墨色加粗波形（细线膨胀后在小尺寸仍可读）。"""
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=int(size * radius_ratio), fill=AMBER)
    side = int(size * (1 - 2 * pad_ratio))
    alpha = Image.open(SRC).convert('RGBA').resize((side, side), Image.LANCZOS).getchannel('A')
    if dilate > 1:
        # MaxFilter 需奇数尺寸；膨胀笔划让 16-20px 托盘渲染仍保留波形轮廓
        alpha = alpha.filter(ImageFilter.MaxFilter(dilate if dilate % 2 else dilate + 1))
    mark = Image.new('RGBA', (side, side), INK)
    mark.putalpha(alpha)
    off = (size - side) // 2
    img.alpha_composite(mark, (off, off))
    return img


def main():
    master = badge(1024)

    # 网站 favicon 与 Apple touch icon
    master.resize((32, 32), Image.LANCZOS).save(WEB / 'favicon-32.png')
    master.resize((180, 180), Image.LANCZOS).save(WEB / 'apple-touch-icon.png')
    (WEB / 'assets').mkdir(exist_ok=True)

    # og 图 1200×630：墨底 + 左侧大标 + 右侧名称
    og = Image.new('RGBA', (1200, 630), INK)
    og.alpha_composite(master.resize((360, 360), Image.LANCZOS), (110, 135))
    og.convert('RGB').save(WEB / 'assets/og.png')

    # 托盘图标（反转配色 + 加粗波形，非模板直接着色显示）
    tray_badge().resize((256, 256), Image.LANCZOS).save(OUT / 'assets/tray-icon.png')

    # macOS iconset → icns
    iconset = OUT / 'macos/WorldQuant.iconset'
    iconset.mkdir(parents=True, exist_ok=True)
    for base in (16, 32, 128, 256, 512):
        master.resize((base, base), Image.LANCZOS).save(iconset / f'icon_{base}x{base}.png')
        master.resize((base * 2, base * 2), Image.LANCZOS).save(iconset / f'icon_{base}x{base}@2x.png')

    # Windows ico（PNG 压缩帧，Vista+ 通用）
    ico = OUT / 'windows'
    ico.mkdir(parents=True, exist_ok=True)
    master.save(ico / 'WorldQuant.ico', sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])

    print('icons written under packaging/ and website/')


if __name__ == '__main__':
    main()

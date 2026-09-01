from __future__ import annotations

from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
BRAND = ROOT / "assets" / "brand"
MASTER = BRAND / "tingyiji-icon-master.png"


def fitted_square(image: Image.Image, size: int) -> Image.Image:
    return image.convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)


def main() -> None:
    BRAND.mkdir(parents=True, exist_ok=True)
    image = Image.open(MASTER)
    fitted_square(image, 1024).save(BRAND / "tingyiji-icon-1024.png", optimize=True)
    fitted_square(image, 512).save(BRAND / "tingyiji-icon-512.png", optimize=True)
    fitted_square(image, 256).save(BRAND / "tingyiji-icon-256.png", optimize=True)
    fitted_square(image, 128).save(BRAND / "tingyiji-icon-128.png", optimize=True)
    fitted_square(image, 64).save(BRAND / "tingyiji-icon-64.png", optimize=True)
    fitted_square(image, 32).save(BRAND / "tingyiji-icon-32.png", optimize=True)
    fitted_square(image, 16).save(BRAND / "tingyiji-icon-16.png", optimize=True)
    fitted_square(image, 256).save(
        BRAND / "tingyiji.ico",
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )

    iconset = BRAND / "tingyiji.iconset"
    iconset.mkdir(exist_ok=True)
    icon_sizes = {
        "icon_16x16.png": 16,
        "icon_16x16@2x.png": 32,
        "icon_32x32.png": 32,
        "icon_32x32@2x.png": 64,
        "icon_128x128.png": 128,
        "icon_128x128@2x.png": 256,
        "icon_256x256.png": 256,
        "icon_256x256@2x.png": 512,
        "icon_512x512.png": 512,
        "icon_512x512@2x.png": 1024,
    }
    for filename, size in icon_sizes.items():
        fitted_square(image, size).save(iconset / filename, optimize=True)

    print(f"Brand assets written to: {BRAND}")


if __name__ == "__main__":
    main()

"""Downscale local official artworks (max edge, JPEG).

The launcher no longer packs this folder into the exe; gallery images are
fetched from ravencraft.io. Keep this for optional local preview copies.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QImageWriter

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "ichalaunch" / "ui" / "theme" / "official_artworks"
MAX_EDGE = 1600
JPEG_QUALITY = 82


def main() -> int:
    files = [
        p
        for p in sorted(SRC.iterdir())
        if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"} and p.is_file()
    ]
    print(f"Optimizing {len(files)} images in {SRC} (max edge {MAX_EDGE})")
    for path in files:
        img = QImage(str(path))
        if img.isNull():
            print(f"  FAIL read {path.name}")
            continue
        w, h = img.width(), img.height()
        scale = MAX_EDGE / max(w, h)
        if scale < 1.0:
            nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
            img = img.scaled(
                nw,
                nh,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        out = path.with_suffix(".jpg")
        # Convert to RGB for JPEG
        if img.format() != QImage.Format.Format_RGB888:
            img = img.convertToFormat(QImage.Format.Format_RGB888)
        writer = QImageWriter(str(out))
        writer.setFormat(b"jpg")
        writer.setQuality(JPEG_QUALITY)
        if not writer.write(img):
            print(f"  FAIL write {out.name}: {writer.errorString()}")
            continue
        if out.resolve() != path.resolve() and path.exists():
            path.unlink()
        print(f"  {out.name} ({img.width()}x{img.height()}, {out.stat().st_size // 1024} KB)")

    # Refresh manifest
    names = sorted(
        p.name
        for p in SRC.iterdir()
        if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
    )
    (SRC / "manifest.txt").write_text("\n".join(names) + "\n", encoding="utf-8")
    total = sum(p.stat().st_size for p in SRC.iterdir() if p.is_file())
    print(f"Done: {len(names)} files, {total / (1024 * 1024):.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())

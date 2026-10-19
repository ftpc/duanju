#!/usr/bin/env python3
"""生成应用图标 assets/app.ico（离屏绘制，无第三方依赖）。"""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QColor,
    QGuiApplication,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPolygonF,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def render(size: int = 256) -> QImage:
    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)

    rect = QRectF(8, 8, size - 16, size - 16)
    path = QPainterPath()
    path.addRoundedRect(rect, size * 0.22, size * 0.22)
    gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
    gradient.setColorAt(0.0, QColor("#ff5f57"))
    gradient.setColorAt(1.0, QColor("#ff8a5f"))
    painter.fillPath(path, gradient)

    # 白色圆底 + 播放三角
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(255, 255, 255, 235))
    circle = QRectF(size * 0.24, size * 0.24, size * 0.52, size * 0.52)
    painter.drawEllipse(circle)
    painter.setBrush(QColor("#e5483f"))
    triangle = QPolygonF(
        [
            QPointF(size * 0.42, size * 0.34),
            QPointF(size * 0.72, size * 0.50),
            QPointF(size * 0.42, size * 0.66),
        ]
    )
    painter.drawPolygon(triangle)

    # 顶部高光
    painter.setBrush(QColor(255, 255, 255, 30))
    highlight = QPainterPath()
    highlight.addRoundedRect(QRectF(8, 8, size - 16, (size - 16) * 0.42), size * 0.22, size * 0.22)
    painter.fillPath(highlight, QColor(255, 255, 255, 26))
    painter.end()
    return image


def main() -> int:
    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    _ = app
    assets = PROJECT_ROOT / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    image = render(256)
    png_path = assets / "app.png"
    if not image.save(str(png_path), "PNG"):
        print("保存 PNG 失败")
        return 1
    png = png_path.read_bytes()
    # ICO 容器（单张 256x256 PNG，Windows Vista+ 支持）
    header = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(png), 22)
    (assets / "app.ico").write_bytes(header + entry + png)
    print(f"已生成：{assets / 'app.ico'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

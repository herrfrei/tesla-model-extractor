"""The app icon, drawn in code: the repository may not contain PNGs outside docs/images (see check_no_assets.py),
and `packaging/make_icon.py` renders this same drawing into the .ico / .icns files of the binaries."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap


def render(size: int = 256) -> QImage:
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = size / 256.0

    bg = QLinearGradient(0, 0, 0, size)
    bg.setColorAt(0, QColor("#2b3340"))
    bg.setColorAt(1, QColor("#11151b"))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(bg))
    p.drawRoundedRect(QRectF(8 * s, 8 * s, 240 * s, 240 * s), 52 * s, 52 * s)

    # generic coupe silhouette in side view
    body = QPainterPath()
    body.moveTo(30 * s, 160 * s)
    body.cubicTo(30 * s, 140 * s, 44 * s, 132 * s, 70 * s, 128 * s)
    body.cubicTo(96 * s, 100 * s, 120 * s, 88 * s, 150 * s, 90 * s)
    body.cubicTo(180 * s, 92 * s, 200 * s, 112 * s, 214 * s, 130 * s)
    body.cubicTo(228 * s, 134 * s, 230 * s, 148 * s, 228 * s, 160 * s)
    body.closeSubpath()
    paint = QLinearGradient(0, 88 * s, 0, 164 * s)
    paint.setColorAt(0, QColor("#f4f6f8"))
    paint.setColorAt(1, QColor("#aeb6c0"))
    p.setBrush(QBrush(paint))
    p.drawPath(body)

    glass = QPainterPath()
    glass.moveTo(86 * s, 127 * s)
    glass.cubicTo(106 * s, 106 * s, 124 * s, 98 * s, 148 * s, 99 * s)
    glass.cubicTo(170 * s, 100 * s, 186 * s, 114 * s, 196 * s, 127 * s)
    glass.closeSubpath()
    p.setBrush(QColor("#1d2530"))
    p.drawPath(glass)

    p.setBrush(QColor("#0b0e12"))
    p.setPen(QPen(QColor("#e8473c"), 7 * s))
    for cx in (78, 182):
        p.drawEllipse(QPointF(cx * s, 162 * s), 22 * s, 22 * s)

    # wireframe hint: this is about 3D models
    p.setPen(QPen(QColor(232, 71, 60, 200), 3 * s))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawLine(QPointF(40 * s, 200 * s), QPointF(216 * s, 200 * s))
    p.drawLine(QPointF(70 * s, 214 * s), QPointF(186 * s, 214 * s))
    p.end()
    return img


def app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 32, 48, 64, 128, 256):
        icon.addPixmap(QPixmap.fromImage(render(size)))
    return icon

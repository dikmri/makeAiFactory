from __future__ import annotations

import math
import time

from PySide6.QtCore import QPropertyAnimation, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QRadialGradient
from PySide6.QtMultimedia import QSoundEffect
from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget

from .se_generator import ensure_se_files


class LoadingClock(QWidget):
    setup_finished = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._hand_angle = 0.0
        self._speed = 60.0
        self._opacity = 1.0
        self._finished = False
        self._finishing = False
        self._message = ""
        self._detail = ""
        self._last_tick = time.monotonic()
        self._tick_count = 0

        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(1200)
        self._tick_timer.timeout.connect(self._play_tick)

        self._opacity_effect = QGraphicsOpacityEffect(self)
        self._opacity_effect.setOpacity(1.0)
        self.setGraphicsEffect(self._opacity_effect)

        se = ensure_se_files()
        self._se: dict[str, QSoundEffect] = {}
        for name, path in se.items():
            eff = QSoundEffect(self)
            eff.setSource(QUrl.fromLocalFile(str(path)))
            eff.setVolume(0.3)
            self._se[name] = eff

    def start(self) -> None:
        self._timer.start()
        self._tick_timer.start()
        self._play_tick()

    def stop(self) -> None:
        self._timer.stop()
        self._tick_timer.stop()

    def play_drum(self) -> None:
        pass

    def play_typekey(self) -> None:
        se = self._se.get("typekey")
        if se:
            se.play()

    def play_enter(self) -> None:
        se = self._se.get("enter")
        if se:
            se.play()

    def play_special_key(self, key: str) -> None:
        se = self._se.get(key)
        if se:
            se.play()

    def set_progress(self, message: str, percent: float, detail: str = "") -> None:
        self._message = message
        self._detail = detail
        if not self._finishing:
            self._speed = 30.0 + percent * 540.0
        self.update()

    def finish_setup(self) -> None:
        self._finishing = True
        self._tick_timer.stop()
        self._speed = 3600.0
        self._message = "⚡準備完了!⚡"
        se = self._se.get("swoosh")
        if se:
            se.play()
        QTimer.singleShot(1500, self._start_fade)

    def _play_tick(self) -> None:
        if not self._finishing:
            se = self._se.get("tick")
            if se:
                se.play()

    def _start_fade(self) -> None:
        self._finished = True
        anim = QPropertyAnimation(self._opacity_effect, b"opacity")
        anim.setDuration(600)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.finished.connect(self._on_fade_done)
        anim.start()
        self._fade_anim = anim

    def _on_fade_done(self) -> None:
        self._timer.stop()
        self.hide()
        self.setup_finished.emit()

    def _tick(self) -> None:
        now = time.monotonic()
        dt = now - self._last_tick
        self._last_tick = now
        self._hand_angle = (self._hand_angle + self._speed * dt) % 360.0
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        cx = w / 2
        cy = h / 2
        r = min(w, h) * 0.32

        bg = QColor(15, 15, 26, 220)
        painter.fillRect(self.rect(), bg)

        outer_glow = QRadialGradient(cx, cy, r * 1.5)
        outer_glow.setColorAt(0.0, QColor(0, 255, 255, 40))
        outer_glow.setColorAt(0.6, QColor(128, 0, 255, 20))
        outer_glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setBrush(outer_glow)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(cx - r * 1.5, cy - r * 1.5, r * 3, r * 3)

        face_grad = QRadialGradient(cx, cy - r * 0.3, r)
        face_grad.setColorAt(0.0, QColor(30, 30, 60))
        face_grad.setColorAt(1.0, QColor(10, 10, 30))
        painter.setBrush(face_grad)
        rim_pen = QPen(QColor(0, 255, 255), 3)
        painter.setPen(rim_pen)
        painter.drawEllipse(cx - r, cy - r, r * 2, r * 2)

        inner_rim = QPen(QColor(128, 0, 255, 120), 1.5)
        painter.setPen(inner_rim)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(cx - r * 0.92, cy - r * 0.92, r * 1.84, r * 1.84)

        for i in range(12):
            angle_rad = math.radians(i * 30 - 90)
            x1 = cx + math.cos(angle_rad) * r * 0.82
            y1 = cy + math.sin(angle_rad) * r * 0.82
            x2 = cx + math.cos(angle_rad) * r * 0.92
            y2 = cy + math.sin(angle_rad) * r * 0.92
            pen = QPen(QColor(0, 255, 255, 200), 3 if i % 3 == 0 else 1.5)
            painter.setPen(pen)
            painter.drawLine(int(x1), int(y1), int(x2), int(y2))

        for i in range(60):
            if i % 5 == 0:
                continue
            angle_rad = math.radians(i * 6 - 90)
            x1 = cx + math.cos(angle_rad) * r * 0.88
            y1 = cy + math.sin(angle_rad) * r * 0.88
            x2 = cx + math.cos(angle_rad) * r * 0.92
            y2 = cy + math.sin(angle_rad) * r * 0.92
            painter.setPen(QPen(QColor(0, 255, 255, 60), 0.5))
            painter.drawLine(int(x1), int(y1), int(x2), int(y2))

        hand_angle_rad = math.radians(self._hand_angle - 90)
        hx = cx + math.cos(hand_angle_rad) * r * 0.78
        hy = cy + math.sin(hand_angle_rad) * r * 0.78

        trail_count = 12
        for i in range(trail_count, 0, -1):
            trail_angle = self._hand_angle - i * (self._speed * 0.001 + 3)
            ta_rad = math.radians(trail_angle - 90)
            tx = cx + math.cos(ta_rad) * r * 0.78
            ty = cy + math.sin(ta_rad) * r * 0.78
            alpha = int(180 * (1 - i / trail_count))
            if self._finishing:
                trail_color = QColor(255, 255, 0, alpha)
            else:
                trail_color = QColor(0, 255, 255, alpha)
            painter.setPen(QPen(trail_color, 2))
            painter.drawLine(int(cx), int(cy), int(tx), int(ty))

        if self._finishing:
            hand_color = QColor(255, 255, 0)
            glow_color = QColor(255, 255, 0, 100)
            center_color = QColor(255, 200, 0)
        else:
            hand_color = QColor(0, 255, 255)
            glow_color = QColor(0, 255, 255, 100)
            center_color = QColor(0, 200, 255)

        painter.setPen(QPen(glow_color, 5))
        painter.drawLine(int(cx), int(cy), int(hx), int(hy))
        painter.setPen(QPen(hand_color, 2.5))
        painter.drawLine(int(cx), int(cy), int(hx), int(hy))

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(center_color)
        painter.drawEllipse(cx - 6, cy - 6, 12, 12)
        painter.setBrush(QColor(255, 255, 255))
        painter.drawEllipse(cx - 2, cy - 2, 4, 4)

        if self._finishing:
            font = QFont("Impact", max(16, int(r * 0.22)))
            font.setBold(True)
            painter.setFont(font)
            gradient_offset = (time.monotonic() * 3) % 1.0
            msg_grad = QRadialGradient(cx, cy + r * 0.5, r)
            msg_grad.setColorAt(0.0, QColor(255, 255, 0))
            msg_grad.setColorAt(0.4 + gradient_offset * 0.2, QColor(255, 0, 255))
            msg_grad.setColorAt(0.7, QColor(0, 255, 255))
            msg_grad.setColorAt(1.0, QColor(255, 255, 0))
            painter.setPen(QPen(msg_grad, 1))
            painter.drawText(self.rect().adjusted(0, 0, 0, -int(r * 0.5)),
                             Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
                             self._message)
        else:
            font = QFont("Consolas", max(10, int(r * 0.12)))
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QColor(200, 200, 220))
            painter.drawText(self.rect().adjusted(0, 0, 0, -int(r * 0.5)),
                             Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
                             self._message)

        if self._detail:
            small_font = QFont("Consolas", max(8, int(r * 0.08)))
            painter.setFont(small_font)
            painter.setPen(QColor(140, 140, 160))
            painter.drawText(self.rect().adjusted(0, 0, 0, -int(r * 0.2)),
                             Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
                             self._detail)
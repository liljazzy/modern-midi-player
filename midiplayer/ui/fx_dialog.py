"""Settings dialog for the piano particle effects. Changes are applied live; Cancel reverts."""
from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
                               QLabel, QPushButton, QSlider, QVBoxLayout, QWidget)

from .piano_strip import COLOR_MODES, KINDS, FxConfig, PianoStrip


class _Slider(QWidget):
    """Integer slider with a value label; *scale* maps slider units to the shown value."""

    def __init__(self, lo, hi, value, scale=1.0, suffix="", on_change=None):
        super().__init__()
        self.scale, self.suffix = scale, suffix
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(lo, hi)
        self.slider.setValue(value)
        self.label = QLabel()
        self.label.setMinimumWidth(52)
        self.label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.label)
        self.slider.valueChanged.connect(self._changed)
        self._cb = on_change
        self._refresh()

    def value(self) -> float:
        return self.slider.value() * self.scale

    def set_value(self, v: float):
        self.slider.setValue(int(round(v / self.scale)))

    def _refresh(self):
        v = self.value()
        self.label.setText(f"{v:.0f}{self.suffix}" if self.scale >= 1 else f"{v:.2f}{self.suffix}")

    def _changed(self, _):
        self._refresh()
        if self._cb:
            self._cb()


class PianoFxDialog(QDialog):
    def __init__(self, strip: PianoStrip, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Piano effects")
        self.strip = strip
        self._original = replace(strip.fx)
        self._color = strip.fx.color
        cfg = strip.fx

        root = QVBoxLayout(self)
        form = QFormLayout()
        root.addLayout(form)

        self.enabled = QCheckBox("Show particle effects")
        self.enabled.setChecked(cfg.enabled)
        form.addRow(self.enabled)
        self.docked = QCheckBox("Also show them while the piano is docked in the window")
        self.docked.setToolTip("By default the effects only appear when the piano is popped out into its own window")
        self.docked.setChecked(cfg.docked)
        form.addRow(self.docked)
        self.hint_visible = QLabel("")
        self.hint_visible.setObjectName("dim")
        self.hint_visible.setWordWrap(True)
        form.addRow(self.hint_visible)

        self.kind = QComboBox()
        for key, k in KINDS.items():
            self.kind.addItem(k["label"], key)
        self.kind.setCurrentIndex(max(0, self.kind.findData(cfg.kind)))
        form.addRow("Type", self.kind)

        self.mode = QComboBox()
        for key, label in COLOR_MODES.items():
            self.mode.addItem(label, key)
        self.mode.setCurrentIndex(max(0, self.mode.findData(cfg.color_mode)))
        self.color_btn = QPushButton("Pick colour…")
        row = QHBoxLayout()
        row.addWidget(self.mode, 1)
        row.addWidget(self.color_btn)
        form.addRow("Colour", row)

        ch = self._apply
        self.amount = _Slider(0, 400, int(cfg.amount * 100), 0.01, "×", ch)
        self.size = _Slider(30, 400, int(cfg.size * 100), 0.01, "×", ch)
        self.speed = _Slider(20, 400, int(cfg.speed * 100), 0.01, "×", ch)
        self.life = _Slider(20, 400, int(cfg.life * 100), 0.01, "×", ch)
        self.gravity = _Slider(-100, 100, cfg.gravity, 1, "", ch)
        self.bounce = _Slider(0, 150, int(cfg.bounce * 100), 0.01, "×", ch)
        self.height = _Slider(20, 240, cfg.height, 1, " px", ch)
        form.addRow("Amount", self.amount)
        form.addRow("Size", self.size)
        form.addRow("Speed", self.speed)
        form.addRow("Lifetime", self.life)
        form.addRow("Gravity", self.gravity)
        hint = QLabel("extra pull on top of each effect's own physics: negative = floats up, positive = falls")
        hint.setObjectName("dim")
        form.addRow("", hint)
        form.addRow("Bounce", self.bounce)
        form.addRow("Effect height", self.height)

        self.glow = QCheckBox("Glow")
        self.glow.setChecked(cfg.glow)
        form.addRow(self.glow)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.preview_btn = buttons.addButton("Preview", QDialogButtonBox.ActionRole)
        self.reset_btn = buttons.addButton("Reset", QDialogButtonBox.ResetRole)
        root.addWidget(buttons)

        for w in (self.enabled, self.docked, self.glow):
            w.toggled.connect(self._apply)
        self.mode.currentIndexChanged.connect(self._apply)
        self.kind.currentIndexChanged.connect(self._apply)
        self.color_btn.clicked.connect(self._pick_color)
        self.preview_btn.clicked.connect(self._preview)
        self.reset_btn.clicked.connect(self._reset)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self._sync_enabled()

    # ------------------------------------------------------------------
    def config(self) -> FxConfig:
        return FxConfig(
            enabled=self.enabled.isChecked(), docked=self.docked.isChecked(), kind=self.kind.currentData(),
            amount=self.amount.value(), size=self.size.value(), speed=self.speed.value(),
            life=self.life.value(), gravity=int(self.gravity.value()), bounce=self.bounce.value(),
            glow=self.glow.isChecked(),
            color_mode=self.mode.currentData(), color=self._color, height=int(self.height.value()))

    def _apply(self, *_):
        self.strip.set_fx(self.config())
        self._sync_enabled()                    # (after applying, so the note reflects the new setting)

    def _sync_enabled(self):
        self.color_btn.setEnabled(self.mode.currentData() == "custom")
        on = self.strip.effects_on() if hasattr(self.strip, "effects_on") else True
        cfg_on = self.enabled.isChecked()
        self.hint_visible.setText(
            "" if on or not cfg_on else
            "The piano is docked in the window, so the effects are hidden for now. They appear when you pop it out "
            "(View ▸ Pop piano out), or tick the box above to see them here too.")

    def _pick_color(self):
        c = QColorDialog.getColor(QColor(self._color), self, "Particle colour")
        if c.isValid():
            self._color = c.name()
            self.mode.setCurrentIndex(self.mode.findData("custom"))
            self._apply()

    def _preview(self):
        if not self.enabled.isChecked():
            self.enabled.setChecked(True)
        self.strip.demo()

    def _reset(self):
        d = FxConfig()
        self._color = d.color
        self.enabled.setChecked(d.enabled)
        self.docked.setChecked(d.docked)
        self.kind.setCurrentIndex(self.kind.findData(d.kind))
        self.mode.setCurrentIndex(self.mode.findData(d.color_mode))
        self.amount.set_value(d.amount)
        self.size.set_value(d.size)
        self.speed.set_value(d.speed)
        self.life.set_value(d.life)
        self.gravity.set_value(d.gravity)
        self.bounce.set_value(d.bounce)
        self.height.set_value(d.height)
        self.glow.setChecked(d.glow)
        self._apply()

    def reject(self):
        self.strip.set_fx(self._original)
        super().reject()

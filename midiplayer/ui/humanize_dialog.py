"""Settings dialog for Humanize: a preset plus a slider for every kind of nudge. Changes are applied as you move them (a
moment after you stop); Cancel puts back what you had."""
from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QPushButton,
                               QVBoxLayout)

from ..humanize import MAX_AMOUNT, MAX_STRENGTH, SKILL_LABELS, SKILL_NAMES, SKILL_TEXT, HumanizeConfig
from .fx_dialog import _Slider

PRESETS = (("off", "Off (exactly as written)"), ("light", "Light"), ("natural", "Natural"), ("loose", "Loose")) + \
    tuple((k, "Skill: " + SKILL_LABELS[k]) for k in SKILL_NAMES)
PRESET_TEXT = {"off": "Every note plays exactly on the grid.", "light": "A tidy player: small timing and volume differences.",
               "natural": "Rolled chords, a leaning melody, drifting tempo and dynamics.",
               "loose": "A sloppy, very expressive player: big differences.", "custom": "Your own mix of the sliders below.",
               **SKILL_TEXT}
DETAILS = (
    ("timing", "Timing looseness", "How early or late each note starts, note by note"),
    ("drift", "Tempo drift", "The slow push and pull of the tempo, like a player speeding up and relaxing"),
    ("chords", "Chord rolling", "How much chords are rolled (spread from the bottom up) instead of struck together"),
    ("melody", "Melody lead", "How far the top note of a chord leans ahead, and how much it stands out from the others"),
    ("velocity", "Volume variation", "How much the loudness varies from note to note"),
    ("dynamics", "Phrase dynamics", "How much the loudness swells and fades over a phrase"),
    ("release", "Note lengths", "How unevenly the keys are let go"),
    ("repeat", "Key repetition", "How hard it is to strike the same key again quickly: how long a key needs to come back up, and how much later and lighter a fast re-strike is"),
    ("know", "Unfamiliar passages", "How much it matters whether the player knows a passage: known parts are played faster and cleaner, new ones slower, shakier and with more mistakes (0 = every part is played the same)"),
    ("slips", "Stumbles", "How often a note is a lot early or late, like a player who is still learning (0 = never)"),
    ("fat", "Fat fingers", "How often the key next to the right one is struck too, a little softer (0 = never)"),
    ("wrong", "Wrong notes", "How often a neighbouring key is played instead of the right one (0 = never)"),
    ("missed", "Missed notes", "How often a note is not played at all (0 = never)"),
)


class HumanizeDialog(QDialog):
    def __init__(self, cfg: HumanizeConfig, apply, reroll, parent=None, original: HumanizeConfig = None, apply_now: bool = False):
        """*cfg* is what the sliders start at; *original* (default: the same) is what Cancel goes back to; *apply_now* applies
        *cfg* straight away (when it is not what is currently in use)."""
        super().__init__(parent)
        self.setWindowTitle("Humanize")
        self._apply_cb, self._reroll_cb = apply, reroll
        self._original = replace(original if original is not None else cfg)
        self._loading = False
        root = QVBoxLayout(self)
        form = QFormLayout()
        root.addLayout(form)

        self.preset = QComboBox()
        for key, label in PRESETS:
            self.preset.addItem(label, key)
        self.preset.addItem("Custom", "custom")
        self.preset.setToolTip("Start from one of the presets, then adjust the sliders")
        form.addRow("Preset", self.preset)
        self.preset_text = QLabel("")
        self.preset_text.setObjectName("dim")
        self.preset_text.setWordWrap(True)
        form.addRow("", self.preset_text)

        self.strength = _Slider(0, int(MAX_STRENGTH * 100), int(cfg.strength * 100), 0.01, "×", self._changed)
        self.strength.setToolTip("The overall amount: 0 plays exactly as written, 1 is an expressive player, 2 is very loose")
        form.addRow("Overall amount", self.strength)
        self.sliders = {}
        for key, label, tip in DETAILS:
            sl = _Slider(0, int(MAX_AMOUNT * 100), int(getattr(cfg, key) * 100), 0.01, "×", self._changed)
            sl.setToolTip(tip + " (1× = the normal amount, 0 = none)")
            self.sliders[key] = sl
            form.addRow(label, sl)
        self.drums = QCheckBox("Loosen the drums a little too")
        self.drums.setToolTip("Off: drums stay exactly on the grid. On: they get a little timing and loudness variation (never rolled)")
        self.drums.setChecked(cfg.drums)
        self.drums.toggled.connect(self._changed)
        form.addRow(self.drums)
        hint = QLabel("Each detail slider multiplies the overall amount for that kind of nudge. Changes apply to the song "
                      "as it plays; a different performance is made each time you open a song.")
        hint.setObjectName("dim")
        hint.setWordWrap(True)
        form.addRow(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.reroll_btn = buttons.addButton("New performance", QDialogButtonBox.ActionRole)
        self.reroll_btn.setToolTip("Play the song again with a different set of random nudges")
        self.reset_btn = buttons.addButton("Reset", QDialogButtonBox.ResetRole)
        root.addWidget(buttons)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.reroll_btn.clicked.connect(self._reroll)
        self.reset_btn.clicked.connect(lambda: self.load(HumanizeConfig.preset("natural")))

        self._timer = QTimer(self)                       # (the song is reloaded a moment after you stop moving a slider)
        self._timer.setSingleShot(True)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._push)
        self.preset.activated.connect(self._pick_preset)
        self._sync_preset()
        if apply_now:
            self._timer.start()

    # ------------------------------------------------------------------
    def config(self) -> HumanizeConfig:
        return HumanizeConfig(strength=self.strength.value(), drums=self.drums.isChecked(),
                              **{k: sl.value() for k, sl in self.sliders.items()}).clamped()

    def load(self, cfg: HumanizeConfig):
        """Show *cfg* in the sliders (and apply it)."""
        self._loading = True
        self.strength.set_value(cfg.strength)
        for k, sl in self.sliders.items():
            sl.set_value(getattr(cfg, k))
        self.drums.setChecked(cfg.drums)
        self._loading = False
        self._changed()

    def _pick_preset(self, _idx=None):
        key = self.preset.currentData()
        if key != "custom":
            self.load(HumanizeConfig.preset(key))

    def _sync_preset(self):
        name = self.config().preset_name() or "custom"
        self.preset.blockSignals(True)
        self.preset.setCurrentIndex(max(0, self.preset.findData(name)))
        self.preset.blockSignals(False)
        self.preset_text.setText(PRESET_TEXT.get(name, ""))

    def _changed(self, *_):
        if self._loading:
            return
        self._sync_preset()
        self._timer.start()

    def _push(self):
        self._apply_cb(self.config())

    def _reroll(self):
        self._timer.stop()
        self._apply_cb(self.config())
        self._reroll_cb()

    def accept(self):
        self._timer.stop()
        self._apply_cb(self.config())
        super().accept()

    def reject(self):
        self._timer.stop()
        self._apply_cb(self._original)
        super().reject()

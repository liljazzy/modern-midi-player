"""In-app updates: check GitHub Releases, show what's new, download and install."""
from __future__ import annotations

import os
import sys
import threading
import time
from typing import Optional

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QApplication, QDialog, QFileDialog, QHBoxLayout, QLabel, QMessageBox, QProgressDialog,
                               QPushButton, QTextBrowser, QVBoxLayout)

from .. import __version__, updater
from ..updater import UpdateError, UpdateInfo

CHECK_INTERVAL = 12 * 3600      # automatic checks at most twice a day


class UpdateDialog(QDialog):
    UPDATE, SKIP, LATER = 1, 2, 0

    def __init__(self, info: UpdateInfo, can_install: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Update available")
        self.setMinimumSize(520, 380)
        self.choice = self.LATER
        lay = QVBoxLayout(self)
        head = QLabel(f"<h2 style='margin:0'>Version {info.version} is available</h2>"
                      f"<p style='color:#8b93a7'>You have version {__version__}"
                      + (f" · released {info.published}" if info.published else "") + "</p>")
        head.setTextFormat(Qt.RichText)
        lay.addWidget(head)
        notes = QTextBrowser()
        notes.setOpenExternalLinks(True)
        notes.setMarkdown(info.notes or "_No release notes._")
        lay.addWidget(notes, 1)
        if can_install:
            size = f" ({info.installer_size / 1e6:.0f} MB)" if info.installer_size else ""
            hint = f"The update will download{size}, close the player, install and restart it."
        else:
            hint = "Download the new version from the release page."
        tip = QLabel(hint)
        tip.setObjectName("dim")
        tip.setWordWrap(True)
        lay.addWidget(tip)
        row = QHBoxLayout()
        skip = QPushButton("Skip this version")
        later = QPushButton("Remind me later")
        go = QPushButton("Update now" if can_install else "Open download page")
        go.setDefault(True)
        go.setStyleSheet("background:#7c6cff; border-color:#7c6cff; color:white; font-weight:600;")
        row.addWidget(skip)
        row.addStretch(1)
        row.addWidget(later)
        row.addWidget(go)
        lay.addLayout(row)
        skip.clicked.connect(lambda: self._done(self.SKIP))
        later.clicked.connect(lambda: self._done(self.LATER))
        go.clicked.connect(lambda: self._done(self.UPDATE))

    def _done(self, choice: int):
        self.choice = choice
        self.accept()


class UpdateController(QObject):
    """Owned by the main window. ``window`` must provide ``settings``,
    ``maybe_save()`` and ``close()``."""
    _checked = Signal(object, object, bool)     # info-or-None, error-or-None, manual
    _progress = Signal(int, int)
    _downloaded = Signal(object, object)         # path-or-None, error-or-None
    statusMessage = Signal(str)

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.win = window
        self.settings = window.settings
        self._busy = False
        self._cancel = threading.Event()
        self._dlg: Optional[QProgressDialog] = None
        updater.set_repo_override(self.settings.value("updates/repo", "", str) or "")
        self._checked.connect(self._on_checked)
        self._progress.connect(self._on_progress)
        self._downloaded.connect(self._on_downloaded)

    # ------------------------------------------------------------ settings
    @property
    def auto_check(self) -> bool:
        v = self.settings.value("updates/auto", True)
        return v not in (False, "false", "0", 0)

    def set_auto_check(self, on: bool):
        self.settings.setValue("updates/auto", bool(on))

    def configured(self) -> bool:
        return bool(updater.update_repo())

    def choose_source(self):
        from PySide6.QtWidgets import QInputDialog
        cur = updater.update_repo()
        text, ok = QInputDialog.getText(
            self.win, "Update source",
            "GitHub repository that publishes Modern MIDI Player releases\n"
            "(owner/repo or its github.com address; leave empty for the default):", text=cur)
        if not ok:
            return
        repo = updater.normalize_repo(text)
        if text.strip() and not repo:
            QMessageBox.warning(self.win, "Update source", "That doesn't look like a GitHub repository (owner/repo).")
            return
        self.settings.setValue("updates/repo", repo)
        updater.set_repo_override(repo)
        if repo:
            self.check(manual=True)

    # --------------------------------------------------------------- check
    def startup(self):
        """Called once after the window is shown."""
        last = self.settings.value("updates/lastRunVersion", "", str) or ""
        if last and last != __version__:
            self.statusMessage.emit(f"Updated to version {__version__} ✓")
        self.settings.setValue("updates/lastRunVersion", __version__)
        if not (self.auto_check and self.configured()):
            return
        try:
            last_check = float(self.settings.value("updates/lastCheck", 0) or 0)
        except (TypeError, ValueError):
            last_check = 0.0
        if time.time() - last_check >= CHECK_INTERVAL:
            QTimer.singleShot(4000, lambda: self.check(manual=False))

    def check(self, manual: bool = True):
        if self._busy:
            return
        if not self.configured():
            if manual:
                QMessageBox.information(self.win, "Updates", (
                    "This copy has no update source configured.\n\n"
                    "Copies installed from a published GitHub release update automatically. Set the "
                    "repository with Help ▸ Update source…, or use Help ▸ Install update from file… "
                    "with a downloaded Setup.exe."))
            return
        self._busy = True
        if manual:
            self.statusMessage.emit("Checking for updates…")

        def work():
            try:
                info = updater.check()
                self._checked.emit(info, None, manual)
            except UpdateError as e:
                self._checked.emit(None, str(e), manual)
            except Exception as e:  # never crash the app because of an update check
                self._checked.emit(None, f"Unexpected error: {e}", manual)
        threading.Thread(target=work, name="update-check", daemon=True).start()

    def _on_checked(self, info: Optional[UpdateInfo], error: Optional[str], manual: bool):
        self._busy = False
        self.settings.setValue("updates/lastCheck", time.time())
        if error:
            if manual:
                QMessageBox.warning(self.win, "Updates", error)
            return
        if info is None:
            if manual:
                QMessageBox.information(self.win, "Updates",
                                        f"You're up to date — Modern MIDI Player {__version__} is the latest version.")
            return
        if not manual and self.settings.value("updates/skip", "", str) == info.version:
            return
        can_install = info.can_auto_install and updater.can_self_update()
        dlg = UpdateDialog(info, can_install, self.win)
        dlg.exec()
        if dlg.choice == UpdateDialog.SKIP:
            self.settings.setValue("updates/skip", info.version)
        elif dlg.choice == UpdateDialog.UPDATE:
            if can_install:
                self.download_and_install(info)
            else:
                QDesktopServices.openUrl(QUrl(info.page_url or f"https://github.com/{updater.update_repo()}/releases"))

    # ------------------------------------------------------------ download
    def download_and_install(self, info: UpdateInfo):
        if self._busy:
            return
        self._busy = True
        self._cancel.clear()
        self._dlg = QProgressDialog(f"Downloading version {info.version}…", "Cancel", 0, 100, self.win)
        self._dlg.setWindowTitle("Updating")
        self._dlg.setWindowModality(Qt.WindowModal)
        self._dlg.setMinimumDuration(0)
        self._dlg.setAutoClose(False)
        self._dlg.setAutoReset(False)
        self._dlg.canceled.connect(self._cancel.set)
        self._dlg.show()

        def work():
            try:
                path = updater.download(info, progress=lambda a, b: self._progress.emit(a, b), cancel=self._cancel)
                self._downloaded.emit(path, None)
            except UpdateError as e:
                self._downloaded.emit(None, str(e))
            except Exception as e:
                self._downloaded.emit(None, f"Unexpected error: {e}")
        threading.Thread(target=work, name="update-download", daemon=True).start()

    def _on_progress(self, done: int, total: int):
        if self._dlg is None:
            return
        if total > 0:
            self._dlg.setValue(int(100 * done / total))
            self._dlg.setLabelText(f"Downloading… {done / 1e6:.1f} of {total / 1e6:.1f} MB")
        else:
            self._dlg.setLabelText(f"Downloading… {done / 1e6:.1f} MB")

    def _on_downloaded(self, path: Optional[str], error: Optional[str]):
        self._busy = False
        if self._dlg is not None:
            self._dlg.close()
            self._dlg = None
        if error:
            if "cancelled" not in error:
                QMessageBox.warning(self.win, "Update failed", error)
            return
        self.install(path)

    # ------------------------------------------------------------- install
    def install(self, path: str, confirm: bool = False) -> bool:
        if confirm and QMessageBox.question(
                self.win, "Install update",
                "Modern MIDI Player will close, install the update and start again.\n\nContinue?") \
                != QMessageBox.Yes:
            return False
        if not self.win.maybe_save():
            return False
        silent = updater.can_self_update()
        try:
            updater.launch_installer(path, silent=silent, relaunch=True)
        except UpdateError as e:
            QMessageBox.warning(self.win, "Update failed", str(e))
            return False
        self.win.skip_save_prompt = True
        QTimer.singleShot(0, self.win.close)
        QTimer.singleShot(200, QApplication.quit)
        return True

    def install_from_file(self):
        if sys.platform != "win32":
            QMessageBox.information(self.win, "Install update",
                                    "Installing from a Setup.exe is only available on Windows.\n"
                                    "On other systems, replace the program folder with the new version.")
            return
        start = os.path.join(os.path.expanduser("~"), "Downloads")
        p, _ = QFileDialog.getOpenFileName(self.win, "Choose the new Modern MIDI Player setup file", start,
                                           "Setup (ModernMidiPlayer-Setup-*.exe);;Programs (*.exe)")
        if p:
            self.install(p, confirm=True)

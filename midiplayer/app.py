"""Application entry point."""
import getpass
import os
import subprocess
import sys

APP_ID = "ModernMidiPlayer.App.1"


def _win_message(text: str, title: str = "Modern MIDI Player", flags: int = 0x40) -> int:
    try:
        import ctypes
        return ctypes.windll.user32.MessageBoxW(None, text, title, flags)
    except Exception:
        return 0


def _missing_pyside6() -> int:
    msg = "PySide6 (the Qt user interface library) is not installed."
    if sys.platform != "win32":
        print(msg + "  Install it with:  pip install PySide6", file=sys.stderr)
        return 1
    # Offer to repair the installation (needs an internet connection).
    if _win_message(msg + "\n\nDownload and install it now? (about 80 MB, needs internet)",
                    flags=0x04 | 0x20) != 6:   # MB_YESNO | MB_ICONQUESTION -> IDYES
        return 1
    exe = sys.executable
    if exe.lower().endswith("pythonw.exe"):
        exe = exe[:-len("pythonw.exe")] + "python.exe"
    rc = subprocess.call([exe, "-m", "pip", "install", "--disable-pip-version-check",
                          "--no-warn-script-location", "PySide6-Essentials>=6.5,<7"],
                         creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
    if rc != 0:
        _win_message("Installing PySide6 failed. Check your internet connection and try again.", flags=0x10)
        return 1
    os.execv(sys.executable, [sys.executable] + sys.argv)
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)
    if sys.platform == "win32":
        try:  # own taskbar icon/group instead of Python's
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except Exception:
            pass
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        return _missing_pyside6()
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QIcon

    from .ui.icon import make_icon
    from .ui.main_window import MainWindow
    from .ui.theme import apply_theme

    files = [os.path.abspath(a) for a in argv[1:] if not a.startswith("-")]

    # --- single instance: hand files to an already running window
    server_name = f"ModernMidiPlayer-{_user()}"
    try:
        from PySide6.QtNetwork import QLocalServer, QLocalSocket
    except ImportError:
        QLocalServer = QLocalSocket = None
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(argv)
    if QLocalSocket is not None and "--new-window" not in argv:
        sock = QLocalSocket()
        sock.connectToServer(server_name)
        if sock.waitForConnected(300):
            sock.write(("\n".join(files) or "\x00").encode("utf-8"))
            sock.flush()
            sock.waitForBytesWritten(1000)
            sock.disconnectFromServer()
            return 0

    app.setApplicationName("Modern MIDI Player")
    app.setOrganizationName("ModernMidi")
    apply_theme(app)
    app.setWindowIcon(QIcon(make_icon()))
    win = MainWindow(files=files)
    win.show()

    if QLocalServer is not None:
        server = QLocalServer(app)
        if not server.listen(server_name):
            QLocalServer.removeServer(server_name)
            server.listen(server_name)

        def on_connection():
            conn = server.nextPendingConnection()
            if conn is None:
                return

            def read():
                data = bytes(conn.readAll()).decode("utf-8", "replace")
                paths = [p for p in data.split("\n") if p and p != "\x00" and os.path.exists(p)]
                if paths:
                    win.open_paths(paths)
                win.setWindowState(win.windowState() & ~Qt.WindowMinimized)
                win.show()
                win.raise_()
                win.activateWindow()
            conn.readyRead.connect(read)
            conn.disconnected.connect(conn.deleteLater)
        server.newConnection.connect(on_connection)
    return app.exec()


def _user() -> str:
    try:
        return "".join(c for c in getpass.getuser() if c.isalnum()) or "user"
    except Exception:
        return "user"

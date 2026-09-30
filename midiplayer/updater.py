"""Update checking and installation (no Qt dependency, fully testable).

Updates are published as GitHub Releases.  Each release carries the Windows
installer (``ModernMidiPlayer-Setup-<version>.exe``) and a matching
``.sha256`` file.  The app compares the release tag with its own version,
downloads the installer, verifies the checksum and runs it silently with
``/S /RELAUNCH`` so the new version starts automatically.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from . import __version__

API_BASE = "https://api.github.com"
USER_AGENT = f"ModernMidiPlayer/{__version__}"
INSTALLER_RE = re.compile(r"ModernMidiPlayer-Setup-.*\.exe$", re.I)


class UpdateError(Exception):
    pass


@dataclass
class UpdateInfo:
    version: str
    notes: str = ""
    page_url: str = ""
    installer_url: str = ""
    installer_name: str = ""
    installer_size: int = 0
    sha256_url: str = ""
    published: str = ""
    assets: List[str] = field(default_factory=list)

    @property
    def can_auto_install(self) -> bool:
        return bool(self.installer_url)


# ------------------------------------------------------------------ config
_repo_override = ""


def set_repo_override(repo: str) -> None:
    """User-chosen update source (Help ▸ Update source…); '' clears it."""
    global _repo_override
    _repo_override = normalize_repo(repo)


def normalize_repo(text: str) -> str:
    """Accept 'owner/repo' or a github.com URL; return 'owner/repo' or ''."""
    t = (text or "").strip().rstrip("/")
    t = re.sub(r"^(https?://)?(www\.)?github\.com/", "", t, flags=re.I)
    t = re.sub(r"\.git$", "", t)
    parts = [p for p in t.split("/") if p]
    if len(parts) >= 2 and all(re.fullmatch(r"[A-Za-z0-9_.-]+", p) for p in parts[:2]):
        return f"{parts[0]}/{parts[1]}"
    return ""


def update_repo() -> str:
    """GitHub ``owner/repo`` that publishes releases ('' = not configured).

    Order: MMP_UPDATE_REPO env var, the user's choice in the app, then
    update_config.py (written by the release build)."""
    env = os.environ.get("MMP_UPDATE_REPO", "").strip()
    if env:
        return env
    if _repo_override:
        return _repo_override
    try:
        from .update_config import GITHUB_REPO
        return (GITHUB_REPO or "").strip()
    except ImportError:
        return ""


def install_dir() -> Optional[str]:
    """Folder of an installed (Setup.exe) copy, or None for a source checkout."""
    app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # …/app
    root = os.path.dirname(app_dir)                                          # install dir
    if os.path.exists(os.path.join(root, "Uninstall.exe")) and \
            os.path.exists(os.path.join(root, "ModernMidiPlayer.exe")):
        return root
    return None


def can_self_update() -> bool:
    return sys.platform == "win32" and install_dir() is not None


# ---------------------------------------------------------------- versions
def parse_version(v: str) -> Tuple:
    """'v1.2.10' -> (1, 2, 10, 1); pre-releases ('1.2.0-beta.1') sort lower."""
    v = (v or "").strip().lstrip("vV")
    m = re.match(r"^(\d+(?:\.\d+)*)(.*)$", v)
    if not m:
        return (0,)
    nums = [int(x) for x in m.group(1).split(".")]
    while len(nums) < 3:
        nums.append(0)
    suffix = m.group(2).strip("-.+ ")
    return tuple(nums) + ((0, suffix) if suffix else (1, ""))


def is_newer(remote: str, local: str = __version__) -> bool:
    return parse_version(remote) > parse_version(local)


# ------------------------------------------------------------------- fetch
def _get(url: str, timeout: float = 15.0, accept: str = "application/vnd.github+json") -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise UpdateError("No releases have been published yet.") from e
        if e.code == 403:
            raise UpdateError("The update server is busy (rate limit). Please try again later.") from e
        raise UpdateError(f"Update server error ({e.code}).") from e
    except (urllib.error.URLError, OSError) as e:
        reason = getattr(e, "reason", e)
        raise UpdateError(f"Could not reach the update server: {reason}") from e


def release_from_json(data: dict) -> UpdateInfo:
    tag = data.get("tag_name") or data.get("name") or ""
    info = UpdateInfo(version=tag.lstrip("vV"), notes=data.get("body") or "",
                      page_url=data.get("html_url") or "", published=(data.get("published_at") or "")[:10])
    assets = data.get("assets") or []
    info.assets = [a.get("name", "") for a in assets]
    for a in assets:
        name = a.get("name", "")
        if INSTALLER_RE.search(name):
            info.installer_url = a.get("browser_download_url", "")
            info.installer_name = name
            info.installer_size = int(a.get("size") or 0)
    if info.installer_name:
        for a in assets:
            if a.get("name", "").lower() in (info.installer_name.lower() + ".sha256", "sha256sums.txt"):
                info.sha256_url = a.get("browser_download_url", "")
    return info


def fetch_latest(repo: Optional[str] = None, api_base: Optional[str] = None, timeout: float = 15.0) -> UpdateInfo:
    api_base = api_base or API_BASE
    repo = repo if repo is not None else update_repo()
    if not repo:
        raise UpdateError("No update source is configured for this copy.")
    raw = _get(f"{api_base.rstrip('/')}/repos/{repo}/releases/latest", timeout)
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError as e:
        raise UpdateError("The update server sent an invalid response.") from e
    info = release_from_json(data)
    if not info.version:
        raise UpdateError("The latest release has no version tag.")
    return info


def check(repo: Optional[str] = None, api_base: Optional[str] = None, current: Optional[str] = None) -> Optional[UpdateInfo]:
    """Return UpdateInfo when a newer version exists, else None."""
    info = fetch_latest(repo, api_base)
    return info if is_newer(info.version, current or __version__) else None


# ---------------------------------------------------------------- download
def _expected_sha256(url: str, filename: str) -> Optional[str]:
    text = _get(url, accept="application/octet-stream").decode("utf-8", "replace")
    for line in text.splitlines():
        parts = line.strip().replace("*", " ").split()
        if not parts:
            continue
        if re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]) and (len(parts) == 1 or parts[-1] == filename):
            return parts[0].lower()
    return None


def download(info: UpdateInfo, dest_dir: Optional[str] = None,
             progress: Optional[Callable[[int, int], None]] = None,
             cancel: Optional[threading.Event] = None, timeout: float = 30.0) -> str:
    """Download and verify the installer. Returns the local path."""
    if not info.installer_url:
        raise UpdateError("This release has no Windows installer.")
    dest_dir = dest_dir or os.path.join(tempfile.gettempdir(), "ModernMidiPlayer-update")
    os.makedirs(dest_dir, exist_ok=True)
    name = os.path.basename(info.installer_name or "ModernMidiPlayer-Setup.exe")
    path = os.path.join(dest_dir, name)
    part = path + ".part"
    expected = _expected_sha256(info.sha256_url, name) if info.sha256_url else None
    req = urllib.request.Request(info.installer_url, headers={"User-Agent": USER_AGENT,
                                                             "Accept": "application/octet-stream"})
    h = hashlib.sha256()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r, open(part, "wb") as f:
            total = int(r.headers.get("Content-Length") or info.installer_size or 0)
            done = 0
            while True:
                if cancel is not None and cancel.is_set():
                    raise UpdateError("Download cancelled.")
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    except (urllib.error.URLError, OSError) as e:
        _remove(part)
        raise UpdateError(f"Download failed: {getattr(e, 'reason', e)}") from e
    except UpdateError:
        _remove(part)
        raise
    if total and done != total:
        _remove(part)
        raise UpdateError("Download was incomplete. Please try again.")
    if expected and h.hexdigest() != expected:
        _remove(part)
        raise UpdateError("The downloaded file is damaged (checksum mismatch). Please try again.")
    os.replace(part, path)
    return path


def _remove(p: str):
    try:
        os.remove(p)
    except OSError:
        pass


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ----------------------------------------------------------------- install
def launch_installer(path: str, silent: bool = True, relaunch: bool = True) -> None:
    """Start the Setup.exe detached so it survives this process exiting."""
    if not os.path.exists(path):
        raise UpdateError("Installer file not found.")
    args = [path]
    if silent:
        args.append("/S")
    if relaunch:
        args.append("/RELAUNCH")
    d = install_dir()
    if d and silent:
        args.append("/D=" + d)          # must be last, unquoted (NSIS rule)
    kw = {}
    if sys.platform == "win32":
        kw["creationflags"] = 0x00000008 | 0x00000200   # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        kw["close_fds"] = True
    else:
        kw["start_new_session"] = True
    try:
        subprocess.Popen(args, **kw)
    except OSError as e:
        raise UpdateError(f"Could not start the installer: {e}") from e

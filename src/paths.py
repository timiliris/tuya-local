"""Install vs. user-data paths. macOS, Windows and Linux. No secrets."""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "TuyaRtspBridge"
APP_UNIX = "tuya-rtsp-bridge"


def install_root() -> Path:
    env = os.environ.get("TUYA_BRIDGE_ROOT")
    if env:
        return Path(env)
    here = Path(__file__).resolve().parent
    if here.name == "src":
        return here.parent
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return here


def user_data() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA") or Path.home()) / APP_NAME
    else:
        xdg = os.environ.get("XDG_DATA_HOME")
        base = Path(xdg) / APP_UNIX if xdg else Path.home() / ".local" / "share" / APP_UNIX
    base.mkdir(parents=True, exist_ok=True)
    return base


def tuya_data() -> Path:
    d = user_data() / ".tuya-data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_path() -> Path:
    if os.name != "nt":
        xdg = os.environ.get("XDG_CONFIG_HOME")
        cfg = Path(xdg) / APP_UNIX if xdg else Path.home() / ".config" / APP_UNIX
        cfg.mkdir(parents=True, exist_ok=True)
        return cfg / "config.json"
    return user_data() / "config.json"


def web_dir() -> Path:
    return install_root() / "web"


def bin_dir() -> Path:
    return install_root() / "bin"


def engine_src() -> Path:
    return install_root() / "vendor" / "tuya-ipc-terminal"


def engine_name() -> str:
    return "tuya-ipc-terminal.exe" if os.name == "nt" else "tuya-ipc-terminal"


def engine_exe() -> Path:
    name = engine_name()
    candidates = [
        bin_dir() / name,
        Path("/usr/lib") / APP_UNIX / "bin" / "tuya-ipc-terminal",
        Path("/usr/bin") / "tuya-ipc-terminal",
    ]
    for p in candidates:
        if p.exists():
            return p
    return bin_dir() / name


def ffmpeg_exe() -> Path | None:
    """Bundled ffmpeg first, then PATH."""
    import shutil

    name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    bundled = bin_dir() / name
    if bundled.exists():
        return bundled
    extra = install_root() / "ffmpeg" / name
    if extra.exists():
        return extra
    w = shutil.which("ffmpeg")
    return Path(w) if w else None


def prepend_bundled_path() -> None:
    """Make bundled media tools visible to child processes."""
    parts: list[str] = []
    b = bin_dir()
    if b.is_dir():
        parts.append(str(b))
    if parts:
        os.environ["PATH"] = os.pathsep.join(parts) + os.pathsep + os.environ.get("PATH", "")


prepend_bundled_path()

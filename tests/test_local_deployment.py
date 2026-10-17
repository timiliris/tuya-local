"""Smoke checks for the local Docker deployment, without Tuya credentials."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def test_public_host_override() -> None:
    from server import lan_ip

    with patch.dict(os.environ, {"TUYA_BRIDGE_PUBLIC_HOST": "127.0.0.1"}):
        assert lan_ip() == "127.0.0.1"
    with patch.dict(os.environ, {"TUYA_BRIDGE_PUBLIC_HOST": "192.168.10.2"}):
        assert lan_ip() == "192.168.10.2"


def test_running_server() -> None:
    with urlopen("http://127.0.0.1:8787/api/state", timeout=5) as response:
        state = json.load(response)
    assert isinstance(state["cameras"], list)
    for camera in state["cameras"]:
        assert camera["rtspHd"].startswith("rtsp://127.0.0.1:8554/")
    with urlopen("http://127.0.0.1:8787/", timeout=5) as response:
        assert response.status == 200
        assert b"<html" in response.read().lower()


if __name__ == "__main__":
    test_public_host_override()
    test_running_server()
    print("Local Docker deployment checks passed")

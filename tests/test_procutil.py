"""Process cleanup also works in minimal containers without pkill."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import procutil


def test_cleanup_without_pkill() -> None:
    with patch.object(procutil.subprocess, "run", side_effect=FileNotFoundError), patch.object(procutil, "pids_matching", return_value=[]) as matching:
        procutil.kill_engine()
        matching.assert_called_once_with("tuya-ipc-terminal")


if __name__ == "__main__":
    test_cleanup_without_pkill()
    print("Minimal-container process cleanup passed")

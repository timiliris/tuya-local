"""Desktop and Docker reuse the same server with isolated loopback ports."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from runtime_config import port,rtsp_host
from console_ai_worker import source_url
from console_settings_wake import LocalReader
from rtsp_manager import RtspManager

class DesktopRuntimeTests(unittest.TestCase):
    def test_configured_port_is_shared_by_all_local_readers(self):
        with patch.dict(os.environ,{'TUYA_RTSP_PORT':'18554'}):
            self.assertEqual(rtsp_host(),'127.0.0.1:18554')
            self.assertEqual(RtspManager(Path('/tmp')).port,18554)
            self.assertIn(':18554/',LocalReader('/camera',None,30).url)
            self.assertEqual(source_url({'rtspSd':'rtsp://untrusted/camera/sd'},'passive'),'rtsp://127.0.0.1:18554/camera/sd?ai=passive')
    def test_invalid_ports_are_rejected(self):
        for value in ('0','65536','other'):
            with patch.dict(os.environ,{'TUYA_RTSP_PORT':value}),self.assertRaises(ValueError):rtsp_host()
    def test_runtime_does_not_attach_to_or_kill_another_instance(self):
        manager=RtspManager(Path('/tmp'))
        self.assertFalse(manager.running())
        manager.proc=Mock();manager.proc.poll.return_value=None
        with patch('rtsp_manager.stop_process') as stop:
            own=manager.proc;manager.stop();stop.assert_called_once_with(own)
        self.assertIsNone(manager.proc)

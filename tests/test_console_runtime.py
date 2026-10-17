import sys
import unittest
from unittest.mock import Mock, patch
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from console_runtime import Runtime

class RuntimeTests(unittest.TestCase):
    def backend(self):
        backend=Mock()
        backend.client.login={'sid':'private'}
        backend.client.generation=4
        backend.client.region_id='we'
        backend.rtsp.running.return_value=True
        backend.public_state.return_value={'loggedIn':True}
        return backend

    def test_start_and_stop_own_the_engine(self):
        backend=self.backend();runtime=Runtime(backend)
        runtime.start();backend.rtsp.start.assert_called_once()
        runtime.stop();backend.rtsp.stop.assert_called_once()
        self.assertTrue(runtime.closed.is_set())
        self.assertFalse(runtime.worker.is_alive())

    def test_fresh_install_serves_login_without_starting_engine(self):
        backend=self.backend();backend.client.login=None
        runtime=Runtime(backend);runtime.start();runtime.stop()
        backend.rtsp.start.assert_not_called()

    def test_authenticated_account_rejects_second_qr_flow(self):
        runtime=Runtime(self.backend())
        with self.assertRaises(ValueError):runtime.qr_start('we')
        runtime.backend.client.generate_qr.assert_not_called()

    def test_qr_confirmation_persists_discovers_and_starts_local_engine(self):
        backend=self.backend();backend.client.login=None
        backend.client.wait_login.return_value=True
        runtime=Runtime(backend)
        runtime.poll_login(4)
        backend.client.save_session.assert_called_once()
        backend.client.discover_cameras.assert_called_once()
        backend.rtsp.start.assert_called_once()
        backend.local_ptz.warmup.assert_not_called()
        backend.apply_service_flags.assert_not_called()

    def test_cancelled_qr_flow_cannot_save_or_start_engine(self):
        backend=self.backend();runtime=Runtime(backend);runtime.closed.set()
        runtime.poll_login(4)
        backend.client.save_session.assert_not_called()
        backend.rtsp.start.assert_not_called()

    def test_no_external_http_bridge_for_state_or_actions(self):
        import console
        runtime=Runtime(self.backend())
        with patch.object(console,'runtime',runtime):
            self.assertEqual(console.upstream('/api/state'),{'loggedIn':True})
            console.upstream('/api/rtsp/start',{})
        runtime.backend.rtsp.start.assert_called_once()
        with self.assertRaises(ValueError):runtime.action('/arbitrary',{})

if __name__=='__main__':unittest.main()

class DiagnosticPrivacyTests(unittest.TestCase):
    def test_diagnostics_only_expose_classified_events(self):
        import threading
        backend=Mock()
        backend.rtsp._lock=threading.Lock()
        backend.rtsp.log=['13:00:00.000 INF Camera startup wake pulse sent pulse=2',
                          '13:00:00.010 TRC SDP: private-user-secret',
                          '13:00:00.030 TRC Successfully sent first video packet session=private-session']
        result=Runtime(backend).diagnostics()
        self.assertEqual([r['event'] for r in result],['wake_repeated','video_received'])
        self.assertNotIn('private',str(result))

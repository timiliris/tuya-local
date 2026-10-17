"""RTSP uses real account verifiers/ACLs, never an IP or query-string exemption."""
import base64
import http.client
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from console_security import Security
from console_rtsp_auth import RTSPAuthorization, INTERNAL_USER
from runtime_config import internal_rtsp_url, internal_rtsp_authorization


def basic(name, password):
    return 'Basic ' + base64.b64encode((name + ':' + password).encode()).decode()


class RTSPTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'TUYA_ACCESS_PASSWORD': '', 'TUYA_ACCESS_PASSWORD_FILE': '', 'TUYA_RTSP_INTERNAL_PASSWORD': ''}); self.env.start()
        self.security = Security()
        self.user = self.security.make_user('reader', 'viewer', 'synthetic-viewer-password', camera_ids=['allowed'])
        self.security.users[self.user['id']] = self.user
        self.broker = RTSPAuthorization(self.security)

    def tearDown(self):
        self.broker.stop(); self.env.stop()

    def login(self, password='synthetic-viewer-password', device='allowed'):
        return self.broker.check({'authorization': basic('reader', password), 'deviceId': device, 'address': '127.0.0.1'})

    def test_same_password_and_grants_apply_even_on_loopback(self):
        self.assertFalse(self.broker.check({'deviceId': 'allowed', 'address': '127.0.0.1'}))
        self.assertFalse(self.login('incorrect'))
        self.assertTrue(self.login()['allowed'])
        self.assertFalse(self.login(device='denied')['allowed'])
        self.assertFalse(self.login()['admin'])

    def test_active_ticket_revoked_on_grant_change_password_or_deletion(self):
        for change in ('cameraIds', 'digest', 'delete'):
            with self.subTest(change=change):
                self.security.attempts.clear()
                self.security.users[self.user['id']] = dict(self.user)
                ticket = self.login()['ticket']
                if change == 'delete': del self.security.users[self.user['id']]
                else: self.security.users[self.user['id']] = {**self.user, change: [] if change == 'cameraIds' else '00' * 32}
                self.assertFalse(self.broker.check({'ticket': ticket, 'deviceId': 'allowed'}))

    def test_no_public_stream_before_first_admin_and_internal_auth_is_explicit(self):
        self.security.users.clear()
        self.assertFalse(self.login())
        self.assertFalse(self.broker.check({'authorization': basic(INTERNAL_USER, 'incorrect'), 'deviceId': 'allowed'}))
        grant = self.broker.check({'authorization': basic(INTERNAL_USER, self.broker.internal), 'deviceId': 'allowed'})
        self.assertTrue(grant['allowed']); self.assertTrue(grant['internal'])

    def test_missing_unknown_expired_tickets_and_rate_limits_fail_closed(self):
        self.assertFalse(self.broker.check({'ticket': 'fake', 'deviceId': 'allowed'}))
        ticket = self.login()['ticket']; self.broker.grants[ticket]['expires'] = 0
        self.assertFalse(self.broker.check({'ticket': ticket, 'deviceId': 'allowed'}))
        self.security.attempts.clear()
        for _ in range(5): self.login('incorrect')
        self.assertFalse(self.login())
        for value in ('Basic !!!', 'Bearer abc', 'Basic ' + 'A'*3000):
            self.assertFalse(self.broker.check({'authorization': value}))

    def test_successful_vlc_reconnects_do_not_exhaust_failure_budget(self):
        for _ in range(10):
            self.assertTrue(self.login()["allowed"])

    def test_private_listener_requires_separate_bearer_secret(self):
        self.broker.start()
        connection = http.client.HTTPConnection('127.0.0.1', self.broker.server.server_port, timeout=3)
        body = json.dumps({'authorization': basic('reader', 'synthetic-viewer-password'), 'deviceId': 'allowed'})
        connection.request('POST', '/authorize', body, {'Content-Type': 'application/json'})
        response = connection.getresponse(); response.read(); self.assertEqual(response.status, 403)
        connection.request('POST', '/authorize', body, {'Authorization': 'Bearer ' + self.broker.secret})
        response = connection.getresponse(); self.assertTrue(json.load(response)['allowed']); connection.close()
        source = 'rtsp://127.0.0.1:8554/demo/sd?ai=passive'
        self.assertIn('@127.0.0.1:8554', internal_rtsp_url(source))
        self.assertNotIn(self.broker.internal, internal_rtsp_authorization())
        with self.assertRaises(ValueError): internal_rtsp_url('rtsp://192.0.2.1/demo')


if __name__ == '__main__': unittest.main()

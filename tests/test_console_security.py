"""Exercise real HTTP boundaries without account data or camera connections."""
import http.client
import json
import os
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import console
from console_security import ConsoleHTTPServer, Security

class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'TUYA_ACCESS_PASSWORD': 'synthetic-test-password', 'TUYA_ACCESS_PASSWORD_FILE': '', 'TUYA_PUBLIC_ORIGIN': '', 'TUYA_ALLOWED_HOSTS': ''})
        self.env.start()
        self.security = Security()
        self.server = ConsoleHTTPServer(('127.0.0.1', 0), console.Handler, self.security)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host = f'127.0.0.1:{self.server.server_port}'
        self.runtime = patch.object(console, 'runtime', Mock())
        self.fake = self.runtime.start()
        self.fake.state.return_value = {'cameras': [], 'rtsp': {'running': False}}
        self.fake.action.return_value = self.fake.state.return_value

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        self.runtime.stop(); self.env.stop()

    def request(self, path='/api/state', method='GET', body=None, headers=None):
        connection = http.client.HTTPConnection(self.host, timeout=3)
        payload = json.dumps(body) if body is not None else None
        fields = {'Content-Type': 'application/json', **(headers or {})}
        connection.request(method, path, payload, fields)
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def login(self):
        status, headers, _ = self.request('/api/access/login', 'POST', {'password': 'synthetic-test-password'})
        self.assertEqual(status, 200)
        return headers['Set-Cookie'].split(';')[0]

    def test_health_is_minimal_and_camera_surfaces_require_authentication(self):
        status, _, body = self.request('/api/health')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {'service': 'tuya-local', 'localOnly': True})
        for path in ('/api/state', '/api/qr.png', '/api/diagnostics', '/api/ai/events', '/api/ai/snapshot/example.jpg', '/stream/example.mp4'):
            self.assertEqual(self.request(path)[0], 401, path)
        self.fake.state.assert_not_called()
        self.assertIn(b'id="login"', self.request('/')[2])
        self.assertEqual(self.request('/brand.svg')[0], 200)

    def test_cookie_session_login_logout_and_expiry(self):
        cookie = self.login()
        status, headers, _ = self.request('/api/state', headers={'Cookie': cookie})
        self.assertEqual(status, 200)
        self.assertNotIn('Python', headers['Server'])
        status, headers, _ = self.request('/api/access/logout', 'POST', {}, {'Cookie': cookie})
        self.assertEqual(status, 200)
        self.assertIn('Max-Age=0', headers['Set-Cookie'])
        self.assertEqual(self.request(headers={'Cookie': cookie})[0], 401)
        cookie = self.login()
        self.security.sessions[cookie.split('=', 1)[1]] = time.monotonic() - 1
        self.assertEqual(self.request(headers={'Cookie': cookie})[0], 401)
        self.assertEqual(self.request(headers={'Cookie': 'tuya_local_session=forged'})[0], 401)

    def test_block_cross_origin_forms_fetches_and_dns_rebinding(self):
        cookie = self.login()
        for headers in ({'Origin': 'https://evil.example'}, {'Origin': 'null'}, {'Origin': 'https://' + self.host}, {'Sec-Fetch-Site': 'cross-site'}, {'Sec-Fetch-Site': 'same-site'}, {'Host': 'evil.example'}, {'Host': '127.0.0.1@evil.example'}):
            self.assertEqual(self.request('/api/cameras/refresh', 'POST', {}, {'Cookie': cookie, **headers})[0], 403, headers)
        self.fake.action.assert_not_called()
        self.assertEqual(self.request('/api/access/login', 'POST', {'password': 'synthetic-test-password'}, {'Origin': 'https://evil.example'})[0], 403)
        self.assertEqual(self.request('/api/qr.png', headers={'Sec-Fetch-Site': 'cross-site'})[0], 403)
        self.assertEqual(self.request('/', headers={'Sec-Fetch-Site': 'cross-site', 'Sec-Fetch-Mode': 'navigate'})[0], 200)

    def test_json_limits_and_security_headers(self):
        cookie = self.login()
        for content_type in ('text/plain', 'application/x-www-form-urlencoded', 'multipart/form-data'):
            self.assertEqual(self.request('/api/cameras/refresh', 'POST', {}, {'Cookie': cookie, 'Content-Type': content_type})[0], 415)
        self.assertEqual(self.request('/api/cameras/refresh', 'POST', {'data': 'x' * 65536}, {'Cookie': cookie})[0], 413)
        self.assertEqual(self.request('/api/cameras/refresh', 'POST', [], {'Cookie': cookie})[0], 400)
        self.assertEqual(self.request('/api/cameras/refresh', 'POST', {}, {'Cookie': cookie, 'Transfer-Encoding': 'chunked'})[0], 400)
        self.fake.action.assert_not_called()
        for path in ('/', '/brand.svg', '/api/state', '/missing'):
            status, headers, _ = self.request(path)
            self.assertEqual(headers['X-Frame-Options'], 'DENY')
            self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
            self.assertIn("script-src 'self'", headers['Content-Security-Policy'])
            self.assertEqual(headers['Referrer-Policy'], 'no-referrer')

    def test_login_attempts_are_limited_even_if_address_changes(self):
        for _ in range(5):
            self.assertEqual(self.request('/api/access/login', 'POST', {'password': 'wrong'})[0], 401)
        self.assertEqual(self.request('/api/access/login', 'POST', {'password': 'synthetic-test-password'})[0], 429)
        self.assertEqual(len(self.security.sessions), 0)
        self.security.attempts['*'] = (time.monotonic() + 60, 30)
        self.assertEqual(self.security.login('another-client', 'synthetic-test-password')[0], 429)

    def test_sessions_are_bounded_secure_and_configuration_fails_closed(self):
        cookie = self.security.cookie('example')
        self.assertIn('HttpOnly', cookie); self.assertIn('SameSite=Strict', cookie)
        self.security.public_origin = 'https://cameras.example'
        self.assertIn('; Secure', self.security.cookie('example'))
        for host in ('127.0.0.1:8787', '[::1]:8787', '192.168.1.10', 'localhost'):
            self.assertTrue(self.security.valid_host(host))
        for host in ('evil.example', '127.0.0.1/evil', '0.0.0.0', '8.8.8.8', '127.0.0.1:invalid'):
            self.assertFalse(self.security.valid_host(host))
        with patch.dict(os.environ, {'TUYA_ACCESS_PASSWORD': 'short'}), self.assertRaises(ValueError): Security()
        with patch.dict(os.environ, {'TUYA_ACCESS_PASSWORD_FILE': '/nonexistent/password'}), self.assertRaises(OSError): Security()
        with patch.dict(os.environ, {'TUYA_PUBLIC_ORIGIN': 'http://public.example'}), self.assertRaises(ValueError): Security()

    def test_duplicate_lengths_and_session_capacity_do_not_bypass_boundaries(self):
        connection = http.client.HTTPConnection(self.host, timeout=3)
        connection.putrequest('POST', '/api/access/login')
        connection.putheader('Content-Type', 'application/json')
        connection.putheader('Content-Length', '2')
        connection.putheader('Content-Length', '2')
        connection.endheaders(b'{}')
        response = connection.getresponse()
        self.assertEqual(response.status, 400); response.read(); connection.close()
        for number in range(64): self.security.sessions[str(number)] = time.monotonic() + 60
        self.login()
        self.assertEqual(len(self.security.sessions), 64)
        self.assertNotIn('0', self.security.sessions)
        self.security.attempts = type(self.security.attempts)({'127.0.0.1': (time.monotonic() - 1, 5), '*': (time.monotonic() - 1, 30)})
        self.login()

    def test_loopback_installation_can_run_without_a_password(self):
        self.security.digest = None
        self.assertEqual(self.request('/api/state')[0], 200)
        self.assertEqual(self.request('/api/cameras/refresh', 'POST', {}, {'Origin': 'http://' + self.host})[0], 200)

if __name__ == '__main__': unittest.main()

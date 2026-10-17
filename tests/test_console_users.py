"""HTTP role/ownership enforcement and v1 migration, with no real media or account data."""
import json
import os
import sys
import tempfile
import threading
import unittest
import http.client
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import console
from console_security import Security, ConsoleHTTPServer

class UserTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'web-access.json'
        self.env = patch.dict(os.environ, {'TUYA_ACCESS_PASSWORD': '', 'TUYA_ACCESS_PASSWORD_FILE': '', 'TUYA_PUBLIC_ORIGIN': '', 'TUYA_ALLOWED_HOSTS': ''}); self.env.start()
        self.security = Security(self.path)
        self.server = ConsoleHTTPServer(('127.0.0.1', 0), console.Handler, self.security)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.host = '127.0.0.1:' + str(self.server.server_port)
        self.runtime = Mock()
        self.runtime.state.return_value = {'loggedIn': True, 'hasQr': True, 'qrId': 'private-qr', 'rtsp': {'running': True}, 'cameras': [{'deviceId': 'demo', 'deviceName': 'Demo', 'rtspSd': 'rtsp://127.0.0.1:8554/demo/sd', 'rtspHd': 'rtsp://127.0.0.1:8554/demo/hd'}]}
        self.runtime.action.return_value = self.runtime.state.return_value
        self.patch = patch.object(console, 'runtime', self.runtime); self.patch.start()
        self.players = console.Players(); self.player_patch = patch.object(console, 'players', self.players); self.player_patch.start()
        status, headers, _ = self.request('/api/access/password', {'username': 'owner', 'password': 'synthetic-owner-password', 'confirmation': 'synthetic-owner-password'})
        self.assertEqual(status, 200); self.admin = headers['Set-Cookie'].split(';')[0]
        self.owner = self.security.principal(self.admin)['id']
        status, _, users = self.request('/api/access/users/create', {'username': 'guest', 'role': 'viewer', 'cameraIds': None, 'password': 'synthetic-viewer-password', 'confirmation': 'synthetic-viewer-password'}, self.admin)
        self.assertEqual(status, 200)
        self.viewer_id = next(user['id'] for user in users['users'] if user['username'] == 'guest')
        self.viewer = self.login('guest', 'synthetic-viewer-password')
        self.security.attempts.clear()

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        self.player_patch.stop(); self.patch.stop(); self.env.stop(); self.directory.cleanup()

    def request(self, path, body=None, cookie=None):
        connection = http.client.HTTPConnection(self.host, timeout=4)
        headers = {'Content-Type': 'application/json'}
        if cookie: headers['Cookie'] = cookie
        connection.request('GET' if body is None else 'POST', path, None if body is None else json.dumps(body), headers)
        response = connection.getresponse(); raw = response.read(); status = response.status; headers = dict(response.getheaders()); connection.close()
        return status, headers, json.loads(raw)

    def login(self, username, password):
        status, headers, _ = self.request('/api/access/login', {'username': username, 'password': password})
        self.assertEqual(status, 200); return headers['Set-Cookie'].split(';')[0]

    def test_viewer_can_discover_cameras_but_cannot_read_private_management_surfaces(self):
        status, _, state = self.request('/api/state', cookie=self.viewer)
        self.assertEqual(status, 200); self.assertEqual(state['cameras'][0]['deviceId'], 'demo')
        self.assertEqual(state['cameras'][0]['rtspHd'], ''); self.assertFalse(state['hasQr']); self.assertIsNone(state['qrId'])
        status, _, access = self.request('/api/access', cookie=self.viewer)
        self.assertEqual(access['user']['role'], 'viewer'); self.assertFalse(access['canManage'])
        for path in ('/api/access/users', '/api/diagnostics', '/api/qr.png', '/api/ai/state', '/api/ai/events', '/api/ai/snapshot/example.jpg'):
            self.assertEqual(self.request(path, cookie=self.viewer)[0], 403, path)
        self.runtime.qr.assert_not_called(); self.runtime.diagnostics.assert_not_called()

    def test_forged_admin_role_and_all_management_writes_are_denied_before_side_effects(self):
        for path in sorted(console.ALLOWED_ACTIONS | {'/api/access/password', '/api/access/users/create', '/api/access/users/update', '/api/access/users/delete', '/api/ai/config', '/api/ai/analyze', '/api/ai/events/clear'}):
            status, _, _ = self.request(path, {'role': 'admin', 'id': self.owner, 'currentPassword': 'synthetic-owner-password', 'password': 'synthetic-new-password', 'confirmation': 'synthetic-new-password'}, self.viewer)
            self.assertEqual(status, 403, path)
        self.runtime.action.assert_not_called()
        self.assertEqual(self.security.users[self.viewer_id]['role'], 'viewer')
        self.assertEqual(len(self.security.users), 2)
        for path in ('/api/access/users/create', '/api/access/users/delete'):
            self.assertEqual(self.request(path, {}, None)[0], 401)

    def test_viewer_can_play_only_its_own_sessions_and_cannot_interfere_with_an_administrator(self):
        def create(cookie):
            status, _, session = self.request('/api/player/start', {'deviceId': 'demo', 'quality': 'sd', 'transport': 'webrtc'}, cookie)
            self.assertEqual(status, 200); return session['token']
        own, other = create(self.viewer), create(self.admin)
        for path in ('/api/player/offer', '/api/player/heartbeat', '/api/player/stop'):
            self.assertEqual(self.request(path, {'token': other, 'sdp': 'offer'}, self.viewer)[0], 404)
        with patch.object(self.players, 'open') as opened:
            self.assertEqual(self.request('/stream/' + other + '.mp4', cookie=self.viewer)[0], 404)
            opened.assert_not_called()
        with patch.object(self.players, 'offer', return_value={'type': 'answer', 'sdp': 'local-answer'}) as offer:
            self.assertEqual(self.request('/api/player/offer', {'token': own, 'sdp': 'offer'}, self.viewer)[0], 200)
            offer.assert_called_once_with(own, 'offer')
        self.assertEqual(self.request('/api/player/heartbeat', {'token': own}, self.viewer)[0], 200)
        self.assertEqual(self.request('/api/player/stop', {'token': own}, self.viewer)[0], 200)
        self.assertIn(other, self.players.items)

    def test_role_change_reset_and_deletion_revoke_target_sessions_without_logging_out_other_users(self):
        status, _, _ = self.request('/api/access/users/update', {'id': self.viewer_id, 'role': 'admin'}, self.admin)
        self.assertEqual(status, 200); self.assertFalse(self.security.authenticated(self.viewer)); self.assertTrue(self.security.authenticated(self.admin))
        promoted = self.login('guest', 'synthetic-viewer-password')
        self.assertTrue(self.security.is_admin(promoted))
        status, _, _ = self.request('/api/access/users/update', {'id': self.viewer_id, 'role': 'viewer', 'password': 'synthetic-reset-password', 'confirmation': 'synthetic-reset-password'}, self.admin)
        self.assertEqual(status, 200); self.assertFalse(self.security.authenticated(promoted))
        self.assertEqual(self.security.login('test', 'synthetic-viewer-password', 'guest')[0], 401)
        reset = self.login('guest', 'synthetic-reset-password')
        status, _, _ = self.request('/api/access/users/delete', {'id': self.viewer_id}, self.admin)
        self.assertEqual(status, 200); self.assertFalse(self.security.authenticated(reset)); self.assertTrue(self.security.authenticated(self.admin))
        self.assertEqual(self.security.login('test', 'synthetic-reset-password', 'guest')[0], 401)

    def test_own_admin_cannot_be_deleted_or_demoted_and_invalid_writes_are_atomic(self):
        for operation, body in [('delete', {'id': self.owner}), ('update', {'id': self.owner, 'role': 'viewer'}), ('create', {'username': 'GUEST', 'role': 'admin', 'password': 'synthetic-another-password', 'confirmation': 'synthetic-another-password'}), ('update', {'id': self.viewer_id, 'role': 'invalid'})]:
            self.assertEqual(self.request('/api/access/users/' + operation, body, self.admin)[0], 400)
        previous = self.path.read_bytes()
        with patch('console_security.os.replace', side_effect=OSError('storage unavailable')):
            self.assertEqual(self.request('/api/access/users/delete', {'id': self.viewer_id}, self.admin)[0], 400)
        self.assertEqual(self.path.read_bytes(), previous); self.assertTrue(self.security.authenticated(self.viewer))
        self.assertEqual(len(self.security.users), 2)

    def test_revoked_video_owners_are_reaped_and_self_rotation_preserves_other_users(self):
        status, _, session = self.request('/api/player/start', {'deviceId': 'demo'}, self.viewer)
        self.assertEqual(status, 200)
        token = session['token']
        self.assertEqual(self.request('/api/access/users/update', {'id': self.viewer_id, 'role': 'viewer', 'cameraIds': None}, self.admin)[0], 200)
        self.players.reap(self.security.active_owners())
        self.assertNotIn(token, self.players.items)
        viewer = self.login('guest', 'synthetic-viewer-password')
        status, _, _ = self.request('/api/access/password', {'currentPassword': 'synthetic-owner-password', 'password': 'synthetic-owner-new-password', 'confirmation': 'synthetic-owner-new-password'}, self.admin)
        self.assertEqual(status, 200)
        self.assertTrue(self.security.authenticated(viewer))
        self.assertFalse(self.security.authenticated(self.admin))

    def restrict_viewer(self, selection):
        status, _, _ = self.request('/api/access/users/update', {'id': self.viewer_id, 'role': 'viewer', 'cameraIds': selection}, self.admin)
        self.assertEqual(status, 200)
        self.security.attempts.clear()
        self.viewer = self.login('guest', 'synthetic-viewer-password')

    def add_private_camera(self):
        self.runtime.state.return_value['cameras'].append({'deviceId': 'other', 'deviceName': 'Hidden camera', 'rtspSd': 'rtsp://127.0.0.1:8554/other/sd', 'rtspHd': 'rtsp://127.0.0.1:8554/other/hd'})

    def test_camera_list_and_direct_playback_are_filtered_before_any_wake_or_decoder(self):
        self.add_private_camera(); self.restrict_viewer(['demo'])
        status, _, state = self.request('/api/state', cookie=self.viewer)
        self.assertEqual(status, 200)
        self.assertEqual([camera['deviceId'] for camera in state['cameras']], ['demo'])
        self.assertNotIn('Hidden camera', json.dumps(state)); self.assertNotIn('other', json.dumps(state))
        self.runtime.state.return_value['rtsp']['running'] = False
        with patch.object(self.players, 'create') as create, patch('console.subprocess.Popen') as popen:
            for quality in ('sd', 'hd'):
                for transport in ('mp4', 'webrtc'):
                    status, _, error = self.request('/api/player/start', {'deviceId': 'other', 'quality': quality, 'transport': transport}, self.viewer)
                    self.assertEqual(status, 404)
                    self.assertEqual(error, self.request('/api/player/start', {'deviceId': 'unknown', 'quality': quality, 'transport': transport}, self.viewer)[2])
            create.assert_not_called(); popen.assert_not_called(); self.runtime.action.assert_not_called()
        self.assertEqual(self.request('/api/player/start', {'deviceId': 'demo', 'quality': 'sd'}, self.viewer)[0], 200)

    def test_grants_are_checked_again_for_old_tokens_even_when_session_ownership_matches(self):
        self.add_private_camera(); self.restrict_viewer(['demo'])
        token = self.players.create('rtsp://127.0.0.1:8554/other/sd', owner=self.security.owner(self.viewer), device='other')
        with patch.object(self.players, 'offer') as offer, patch.object(self.players, 'open') as opened:
            for path in ('/api/player/offer', '/api/player/heartbeat', '/api/player/stop'):
                self.assertEqual(self.request(path, {'token': token, 'sdp': 'offer'}, self.viewer)[0], 404)
            self.assertEqual(self.request('/stream/' + token + '.mp4', cookie=self.viewer)[0], 404)
            offer.assert_not_called(); opened.assert_not_called()
        self.players.reap(self.security.active_owners(), self.security.can_view_owner)
        self.assertNotIn(token, self.players.items)

    def test_permission_change_stops_an_existing_player_and_leaves_admin_signed_in(self):
        self.add_private_camera()
        status, _, session = self.request('/api/player/start', {'deviceId': 'other'}, self.viewer)
        self.assertEqual(status, 200)
        process = Mock(); process.poll.return_value = None
        stopped = threading.Event(); process.terminate.side_effect = stopped.set
        self.players.items[session['token']]['process'] = process
        self.assertEqual(self.request('/api/access/users/update', {'id': self.viewer_id, 'role': 'viewer', 'cameraIds': ['demo']}, self.admin)[0], 200)
        self.assertTrue(stopped.wait(2)); self.assertTrue(self.security.authenticated(self.admin))
        self.assertEqual(self.request('/api/state', cookie=self.viewer)[0], 401)
        self.assertNotIn(session['token'], self.players.items)

    def test_zero_selected_and_all_grants_survive_restart_and_new_cameras_are_not_implicitly_shared(self):
        self.add_private_camera()
        for selection, visible in (([], []), (['demo'], ['demo']), (None, ['demo', 'other'])):
            self.restrict_viewer(selection)
            restarted = Security(self.path)
            status, token = restarted.login('client', 'synthetic-viewer-password', 'guest')
            self.assertEqual(status, 200)
            actual = restarted.visible_cameras(restarted.cookie(token), self.runtime.state.return_value['cameras'])
            self.assertEqual([camera['deviceId'] for camera in actual], visible)
            self.assertEqual(json.loads(self.path.read_text())['version'], 3)
        self.restrict_viewer(['demo'])
        self.runtime.state.return_value['cameras'].append({'deviceId': 'new-camera', 'deviceName': 'New camera'})
        self.assertEqual([camera['deviceId'] for camera in self.request('/api/state', cookie=self.viewer)[2]['cameras']], ['demo'])
        self.restrict_viewer(None)
        self.assertEqual(len(self.request('/api/state', cookie=self.viewer)[2]['cameras']), 3)

    def test_new_viewers_default_to_no_cameras_and_invalid_grants_do_not_change_accounts(self):
        self.assertEqual(self.request('/api/access/users/create', {'username': 'new-reader', 'role': 'viewer', 'password': 'synthetic-new-reader-password', 'confirmation': 'synthetic-new-reader-password'}, self.admin)[0], 200)
        fresh = self.login('new-reader', 'synthetic-new-reader-password')
        self.assertEqual(self.request('/api/state', cookie=fresh)[2]['cameras'], [])
        before = self.path.read_bytes()
        for value in ('demo', [42], ['demo', 'demo'], ['not-known'], ['bad\nid']):
            self.assertEqual(self.request('/api/access/users/update', {'id': self.viewer_id, 'role': 'viewer', 'cameraIds': value}, self.admin)[0], 400)
            self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.request('/api/access/users/update', {'id': self.viewer_id, 'role': 'viewer', 'cameraIds': None}, self.viewer)[0], 403)

    def test_password_reset_and_unavailable_cameras_do_not_silently_widen_saved_grants(self):
        self.add_private_camera(); self.restrict_viewer(['other'])
        self.runtime.state.return_value['cameras'] = self.runtime.state.return_value['cameras'][:1]
        self.assertEqual(self.request('/api/access/users/update', {'id': self.viewer_id, 'role': 'viewer', 'password': 'synthetic-scoped-new-password', 'confirmation': 'synthetic-scoped-new-password'}, self.admin)[0], 200)
        self.assertEqual(self.security.users[self.viewer_id]['cameraIds'], ['other'])
        restarted = Security(self.path)
        status, token = restarted.login('client', 'synthetic-scoped-new-password', 'guest')
        self.assertEqual(status, 200)
        self.assertFalse(restarted.can_view(restarted.cookie(token), 'demo'))
        self.assertEqual(self.request('/api/access/users/update', {'id': self.viewer_id, 'role': 'viewer', 'cameraIds': ['other']}, self.admin)[0], 200)

    def test_v2_accounts_migrate_to_previous_all_camera_rights_and_v3_corruption_fails_closed(self):
        records = [{key: value for key, value in user.items() if key != 'cameraIds'} for user in self.security.users.values()]
        self.path.write_text(json.dumps({'version': 2, 'algorithm': 'scrypt-16384-8-1', 'users': records}))
        migrated = Security(self.path)
        status, token = migrated.login('client', 'synthetic-viewer-password', 'guest')
        self.assertEqual(status, 200); self.assertTrue(migrated.can_view(migrated.cookie(token), 'future-camera'))
        for user in records: user['cameraIds'] = None
        viewer = next(user for user in records if user['role'] == 'viewer')
        for bad in ('demo', ['demo', 'demo'], [42]):
            viewer['cameraIds'] = bad
            self.path.write_text(json.dumps({'version': 3, 'algorithm': 'scrypt-16384-8-1', 'users': records}))
            with self.assertRaises(ValueError): Security(self.path)

    def test_legacy_password_is_an_admin_and_migration_preserves_credentials_on_disk(self):
        legacy = self.security.make_user('admin', 'admin', 'synthetic-legacy-password')
        self.path.write_text(json.dumps({'version': 1, 'algorithm': 'scrypt-16384-8-1', 'salt': legacy['salt'], 'digest': legacy['digest']}))
        migrated = Security(self.path)
        status, token = migrated.login('client', 'synthetic-legacy-password')
        self.assertEqual(status, 200); cookie = migrated.cookie(token); self.assertTrue(migrated.is_admin(cookie))
        migrated.manage_user(cookie, 'create', {'username': 'reader', 'role': 'viewer', 'password': 'synthetic-reader-password', 'confirmation': 'synthetic-reader-password'})
        saved = self.path.read_text(); self.assertNotIn('synthetic-legacy-password', saved); self.assertNotIn('synthetic-reader-password', saved)
        self.assertEqual(json.loads(saved)['version'], 3)
        restarted = Security(self.path)
        self.assertEqual(restarted.login('client', 'synthetic-legacy-password')[0], 200)
        self.assertEqual(restarted.login('client', 'synthetic-reader-password', 'reader')[0], 200)
        self.assertEqual(restarted.public_users(restarted.cookie(restarted.login('client', 'synthetic-legacy-password')[1]))['users'][0]['username'], 'admin')

if __name__ == '__main__': unittest.main()

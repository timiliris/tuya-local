"""Exercise frozen media, access setup, rotation and persistence using isolated data."""
from pathlib import Path
import http.cookiejar
import json
import os
import socket
import subprocess
import tempfile
import time
from urllib.error import HTTPError
from urllib.request import build_opener, HTTPCookieProcessor, Request
ROOT = Path(__file__).resolve().parents[1]


def client():
    return build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))


def post(opener, base, path, body):
    request = Request(base + path, data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
    return opener.open(request, timeout=5)


def stop(process):
    try:
        if process.poll() is None:
            process.stdin.close()
            process.wait(timeout=20)
        assert process.returncode == 0, 'Server did not exit gracefully after parent disconnect'
    finally:
        if process.poll() is None: process.kill(); process.wait()
        process.stderr.close()


def start(executable, env, base):
    process = subprocess.Popen([str(executable)], env=env, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 30
        while True:
            if process.poll() is not None:
                raise RuntimeError('Frozen server exited before readiness: ' + process.stderr.read().decode(errors='replace')[-1000:])
            try:
                with client().open(base + '/api/health', timeout=2) as response:
                    assert json.load(response)['service'] == 'tuya-local'
                return process
            except OSError:
                if time.monotonic() > deadline: raise RuntimeError('Frozen server readiness timeout')
                time.sleep(.25)
    except BaseException:
        stop(process)
        raise


def require_auth(opener, base):
    try:
        opener.open(base + '/api/state', timeout=3)
        raise AssertionError('Private state was accessible without authentication')
    except HTTPError as error:
        assert error.code == 401


def test_runtime():
    executable = ROOT / 'desktop/src-tauri/runtime' / ('tuya-local-server.exe' if os.name == 'nt' else 'tuya-local-server')
    subprocess.run([str(executable), '--self-test'], check=True, timeout=90)
    for protected in (False, True):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0)); port = listener.getsockname()[1]
        with tempfile.TemporaryDirectory(prefix='tuya-runtime-test-') as data:
            env = {**os.environ, 'TUYA_DESKTOP_DATA': data, 'TUYA_HTTP_HOST': '127.0.0.1', 'TUYA_HTTP_PORT': str(port), 'TUYA_RTSP_PORT': '18554', 'TUYA_RTSP_BIND': '127.0.0.1', 'TUYA_LOCAL_MEDIA_ONLY': '1', 'TUYA_ACCESS_PASSWORD': 'synthetic-runtime-password' if protected else '', 'TUYA_ACCESS_PASSWORD_FILE': '', 'TUYA_PUBLIC_ORIGIN': '', 'TUYA_ALLOWED_HOSTS': ''}
            base = f'http://127.0.0.1:{port}'
            process = start(executable, env, base)
            opener = client()
            try:
                if protected:
                    require_auth(opener, base)
                    with opener.open(base + '/', timeout=3) as response: assert b'id="login"' in response.read()
                    with post(opener, base, '/api/access/login', {'password': env['TUYA_ACCESS_PASSWORD']}) as response:
                        assert 'HttpOnly' in response.headers['Set-Cookie']
                with opener.open(base + '/api/state', timeout=3) as response:
                    state = json.load(response)
                    assert response.headers['X-Frame-Options'] == 'DENY'
                assert state['localOnly'] is True and state['loggedIn'] is False and state['cameras'] == []
                with opener.open(base + '/api/ai/state', timeout=3) as response: assert json.load(response)['mode'] == 'server'
                with opener.open(base + '/', timeout=3) as response: assert b'server-settings-open' in response.read()
                with opener.open(base + '/server-settings.js', timeout=3) as response: assert b'/api/access/password' in response.read()
                password = 'synthetic-persisted-password'
                with post(opener, base, '/api/access/password', {'currentPassword': env['TUYA_ACCESS_PASSWORD'], 'password': password, 'confirmation': password}) as response:
                    assert json.load(response)['enabled'] is True
                with opener.open(base + '/api/state', timeout=3) as response: assert response.status == 200
                records = list(Path(data).rglob('web-access.json'))
                assert len(records) == 1 and password not in records[0].read_text()
            finally:
                stop(process)
            process = start(executable, env, base)
            try:
                fresh = client()
                require_auth(fresh, base)
                with post(fresh, base, '/api/access/login', {'password': password}) as response: assert response.status == 200
                with fresh.open(base + '/api/state', timeout=3) as response: assert json.load(response)['cameras'] == []
            finally:
                stop(process)
    print('Frozen server: models, assets, password setup/rotation, restart persistence and graceful shutdown passed.')


if __name__ == '__main__': test_runtime()

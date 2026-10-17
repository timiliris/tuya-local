"""Exercise the frozen runtime and access gate without real accounts or cameras."""
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


def test_runtime():
    runtime = ROOT / 'desktop/src-tauri/runtime'
    executable = runtime / ('tuya-local-server.exe' if os.name == 'nt' else 'tuya-local-server')
    subprocess.run([str(executable), '--self-test'], check=True, timeout=90)
    for protected in (False, True):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0)); port = listener.getsockname()[1]
        with tempfile.TemporaryDirectory(prefix='tuya-runtime-test-') as data:
            env = {**os.environ, 'TUYA_DESKTOP_DATA': data, 'TUYA_HTTP_HOST': '127.0.0.1', 'TUYA_HTTP_PORT': str(port), 'TUYA_RTSP_PORT': '18554', 'TUYA_RTSP_BIND': '127.0.0.1', 'TUYA_LOCAL_MEDIA_ONLY': '1', 'TUYA_ACCESS_PASSWORD': 'synthetic-runtime-password' if protected else '', 'TUYA_ACCESS_PASSWORD_FILE': '', 'TUYA_PUBLIC_ORIGIN': '', 'TUYA_ALLOWED_HOSTS': ''}
            process = subprocess.Popen([str(executable)], env=env, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            base = f'http://127.0.0.1:{port}'
            opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
            try:
                deadline = time.monotonic() + 30
                while True:
                    if process.poll() is not None:
                        raise RuntimeError('Frozen server exited before readiness: ' + process.stderr.read().decode(errors='replace')[-1000:])
                    try:
                        with opener.open(base + '/api/health', timeout=2) as response:
                            assert json.load(response)['service'] == 'tuya-local'
                        break
                    except OSError:
                        if time.monotonic() > deadline: raise RuntimeError('Frozen server readiness timeout')
                        time.sleep(.25)
                if protected:
                    try:
                        opener.open(base + '/api/state', timeout=3)
                        raise AssertionError('Private state was accessible without authentication')
                    except HTTPError as error:
                        assert error.code == 401
                    with opener.open(base + '/', timeout=3) as response:
                        assert b'id="login"' in response.read()
                    request = Request(base + '/api/access/login', data=json.dumps({'password': env['TUYA_ACCESS_PASSWORD']}).encode(), headers={'Content-Type': 'application/json'})
                    with opener.open(request, timeout=3) as response:
                        assert 'HttpOnly' in response.headers['Set-Cookie']
                with opener.open(base + '/api/state', timeout=3) as response:
                    state = json.load(response)
                    assert response.headers['X-Frame-Options'] == 'DENY'
                assert state['localOnly'] is True and state['loggedIn'] is False and state['cameras'] == []
                with opener.open(base + '/api/ai/state', timeout=3) as response:
                    assert json.load(response)['mode'] == 'server'
                with opener.open(base + '/', timeout=3) as response:
                    assert b'Tuya Local' in response.read()
                with opener.open(base + '/brand.svg', timeout=3) as response:
                    assert b'aria-label="Tuya Local"' in response.read()
                process.stdin.close(); process.wait(timeout=20)
                assert process.returncode == 0, 'Server did not exit gracefully after parent disconnect'
            finally:
                if process.poll() is None: process.kill(); process.wait()
                process.stderr.close()
    print('Frozen server: models, assets, isolated data, password access and graceful shutdown passed.')


if __name__ == '__main__': test_runtime()

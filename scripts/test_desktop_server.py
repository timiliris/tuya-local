"""Exercise the actual frozen runtime with no account, camera or network credentials."""
from pathlib import Path
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen
ROOT=Path(__file__).resolve().parents[1]

def test_runtime():
    runtime=ROOT/'desktop/src-tauri/runtime'
    executable=runtime/('tuya-local-server.exe' if os.name=='nt' else 'tuya-local-server')
    subprocess.run([str(executable),'--self-test'],check=True,timeout=90)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='tuya-runtime-test-') as data:
        env={**os.environ,'TUYA_DESKTOP_DATA':data,'TUYA_HTTP_HOST':'127.0.0.1','TUYA_HTTP_PORT':str(port),'TUYA_RTSP_PORT':'18554','TUYA_RTSP_BIND':'127.0.0.1','TUYA_LOCAL_MEDIA_ONLY':'1'}
        process=subprocess.Popen([str(executable)],env=env,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        try:
            deadline=time.monotonic()+30
            while True:
                if process.poll() is not None:raise RuntimeError('Frozen server exited before readiness: '+process.stderr.read().decode(errors='replace')[-1000:])
                try:
                    with urlopen(f'http://127.0.0.1:{port}/api/state',timeout=2) as response:state=json.load(response)
                    break
                except OSError:
                    if time.monotonic()>deadline:raise RuntimeError('Frozen server readiness timeout')
                    time.sleep(.25)
            assert state['localOnly'] is True and state['loggedIn'] is False and state['cameras']==[]
            with urlopen(f'http://127.0.0.1:{port}/api/ai/state',timeout=3) as response:assert json.load(response)['mode']=='server'
            with urlopen(f'http://127.0.0.1:{port}/',timeout=3) as response:assert b'Tuya Local' in response.read()
            process.stdin.close();process.wait(timeout=20)
            assert process.returncode==0,'Server did not exit gracefully after parent disconnect'
        finally:
            if process.poll() is None:process.kill();process.wait()
            process.stderr.close()
    print('Frozen server startup, assets, AI state, data isolation and graceful shutdown passed.')

if __name__=='__main__':test_runtime()

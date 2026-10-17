"""PTZ commands using the existing account session; video remains local."""
from __future__ import annotations

import json
import time
from pathlib import Path
from cloud_mobile import CloudMobile, DIR
from tuya_client import TuyaClient


def pulse(mobile: CloudMobile, device_id: str, direction: str) -> dict:
    def publish(dps):
        result = mobile._request('thing.m.device.dp.publish', {
            'devId': device_id, 'gwId': device_id,
            'dps': json.dumps(dps, separators=(',', ':')),
        })
        if result is not True:
            raise RuntimeError('La caméra a refusé la commande PTZ.')
    if direction == 'stop':
        publish({'116': True})
    else:
        try:
            publish({'119': DIR[direction]})
            time.sleep(.35)
        finally:
            publish({'116': True})
    return {'status': 'ok', 'response': {'via': 'cloud'}}


def move(data_dir: Path, device_id: str, direction: str) -> dict:
    if direction not in {*DIR, 'stop'}:
        raise ValueError('Direction inconnue.')
    client = TuyaClient(data_dir)
    if not client.load_latest_session():
        raise ValueError('Connecte ton compte Tuya avant de piloter une caméra.')
    if not any(camera.get('deviceId') == device_id for camera in client.cameras):
        raise ValueError('Caméra inconnue.')
    mobile = CloudMobile(Path('/tmp/tuya-local-ptz'))
    mobile._sid = client.login['sid']
    mobile._endpoint = client.login['domain']['mobileApiUrl'].rstrip('/') + '/api.json'
    return pulse(mobile, device_id, direction)

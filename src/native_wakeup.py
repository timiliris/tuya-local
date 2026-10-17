"""Wake a battery IPC through mobile signaling, without opening media sockets."""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import socket
import ssl
import struct
import time
import uuid
import zlib

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from cloud_mobile import CloudMobile
from paths import tuya_data, user_data
from tuya_client import TuyaClient


# Public application credentials and derivation: abovecolin/tuya-mobile,
# tuya_mobile/profiles.py, mqtt_auth.py and signer.py (Tuya Smart profile).
_APP_ID = '3cxxt3au9x33ytvq3h9j'
_APP_KEY = 'f3hd7pet4p83kemjdf5wqsa5tavrv579'
_APP_SECRET = '5gdtanjtf38vyxkqh87cjwfcqjhvjjqa'
_APP_CERT = ('93:21:9F:C2:73:E2:20:0F:4A:DE:E5:F7:19:1D:C6:56:'
             'BA:2A:2D:7B:2F:F5:D2:4C:D5:5C:4B:61:55:00:1E:40')


def mobile_credentials(login: dict) -> tuple[str, str]:
    md5 = lambda value: hashlib.md5(value.encode()).hexdigest()
    if not login.get('partnerIdentity'):
        return login['sid'], md5(login['ecode'])[8:24]
    certificate_material = 'com.tuya.smart_' + _APP_CERT
    global_material = certificate_material + '_' + _APP_KEY + '_' + _APP_SECRET
    channel_key = hmac.new(_APP_ID.encode(), certificate_material.encode(), hashlib.sha256).hexdigest()[8:16]
    username = (login['partnerIdentity'] + '_v1_' + _APP_ID + '_' + channel_key + '_mb_'
                + login['sid'] + md5(md5(_APP_ID) + login['ecode'])[-16:])
    return username, md5(md5(global_material) + login['ecode'])[8:24]


def mqtt_string(value: str) -> bytes:
    data = value.encode()
    return struct.pack('!H', len(data)) + data


def encode_control(key: bytes, sequence: int, source: bytes, data: dict) -> bytes:
    header = b'2.3' + struct.pack('!I', sequence) + source + b'\0'
    nonce = os.urandom(12)
    plain = json.dumps(data, separators=(',', ':')).encode()
    return header + nonce + AESGCM(key).encrypt(nonce, plain, header)


def decode_control(key: bytes, frame: bytes) -> dict:
    if len(frame) < 40 or frame[:3] != b'2.3':
        raise ValueError('unsupported control frame')
    return json.loads(AESGCM(key).decrypt(frame[12:24], frame[24:], frame[:12]))


def build_offer(session: dict, uid: str) -> str:
    # No candidates are advertised and no media or relay transport is opened.
    return '\r\n'.join([
        'v=0', f'o=- {int(time.time())} 1 IN IP4 127.0.0.1', 's=-', 't=0 0',
        'a=group:BUNDLE imm0', f'a=msid-semantic: WMS {session["sessionId"]}',
        'm=application 9 imm 6001', 'c=IN IP4 0.0.0.0', 'a=rtcp:9 IN IP4 0.0.0.0',
        f'a=ice-ufrag:{session["iceUfrag"]}', f'a=ice-pwd:{session["icePassword"]}',
        'a=ice-options:trickle', f'a=aes-key:{session["aesKey"]}', 'a=mid:imm0',
        'a=rtpmap:6001 AES/KCP 330', f'a=ssrc:0 cname:{uid}', '',
    ])


class ControlChannel:
    def __init__(self, stream):
        self.stream = stream

    def send(self, header: int, data: bytes) -> None:
        length = len(data)
        encoded = bytearray()
        while True:
            digit = length % 128
            length //= 128
            encoded.append(digit | (128 if length else 0))
            if not length:
                break
        self.stream.sendall(bytes([header]) + encoded + data)

    def exact(self, size: int) -> bytes:
        result = bytearray()
        while len(result) < size:
            chunk = self.stream.recv(size - len(result))
            if not chunk:
                raise EOFError('control channel closed')
            result.extend(chunk)
        return bytes(result)

    def receive(self) -> tuple[int, bytes]:
        header = self.exact(1)[0]
        length = 0
        for shift in range(0, 28, 7):
            digit = self.exact(1)[0]
            length |= (digit & 127) << shift
            if not digit & 128:
                if length > 1024 * 1024:
                    raise ValueError('oversized control packet')
                return header, self.exact(length)
        raise ValueError('invalid control length')


def mobile_webrtc_config(device_id: str) -> dict:
    client = TuyaClient(tuya_data())
    if not client.load_latest_session():
        raise RuntimeError('Tuya session unavailable')
    if not any(c['deviceId'] == device_id for c in client.cameras):
        raise ValueError('unknown camera')
    mobile = CloudMobile(user_data())
    mobile._sid = client.login['sid']
    mobile._endpoint = client.login['domain']['mobileApiUrl'].rstrip('/') + '/api.json'
    web = client._post('/api/jarvis/config', payload={
        'devId': device_id, 'clientTraceId': uuid.uuid4().hex,
    }, referer='/playback')['result']
    mobile._request('smartlife.m.p2p.main.pre.link.get', {'devId': device_id})
    config = mobile._request('smartlife.m.rtc.config.get', {'devId': device_id})
    mobile._request('smartlife.m.rtc.session.init', {'devId': device_id})
    config = json.loads(config) if isinstance(config, str) else config
    config['localKey'] = web['localKey']
    return config


def wake_device(device_id: str, prepare_only: bool = False) -> bool:
    client = TuyaClient(tuya_data())
    if not client.load_latest_session():
        raise RuntimeError('Tuya session unavailable')
    if not any(c['deviceId'] == device_id for c in client.cameras):
        raise ValueError('unknown camera')
    mobile = CloudMobile(user_data())
    mobile._sid = client.login['sid']
    mobile._endpoint = client.login['domain']['mobileApiUrl'].rstrip('/') + '/api.json'
    web = client._post('/api/jarvis/config', payload={
        'devId': device_id, 'clientTraceId': uuid.uuid4().hex,
    }, referer='/playback')['result']
    mobile._request('smartlife.m.p2p.main.pre.link.get', {'devId': device_id})
    mobile._request('smartlife.m.rtc.config.get', {'devId': device_id})
    mobile._request('smartlife.m.rtc.session.init', {'devId': device_id})
    if prepare_only:
        return True
    config = mobile._request('m.ipc.v4.rtc.config.get', {
        'devId': device_id, 'bizDM': 'ipc', 'ctId': uuid.uuid4().hex,
    })
    config = json.loads(config) if isinstance(config, str) else config
    p2p = config['p2pConfig']
    p2p = json.loads(p2p) if isinstance(p2p, str) else p2p
    session = p2p['session']
    session = json.loads(session) if isinstance(session, str) else session
    relay_token = dict(p2p.get('tcpRelay', {}))
    relay_token['sessionId'] = device_id + str(int(time.time())) + uuid.uuid4().hex[:8]
    trace_id = session.get('traceId', '') + '_' + device_id + '_' + str(int(time.time() * 1000))
    key = web['localKey'].encode()
    uid = client.login['uid']
    host = client.login['domain']['mobileMqttsUrl']
    identity = 'com.tuya.smart_mb_bridge' + uuid.uuid4().hex + '_' + hashlib.md5(
        (uid + 'sdkfasodifca').encode()).hexdigest() + '_DEFAULT'
    username, password = mobile_credentials(client.login)
    source = b'\0\0\0\1'
    sequence = 0
    with socket.create_connection((host, 8883), timeout=8) as raw:
        with ssl.create_default_context().wrap_socket(raw, server_hostname=host) as stream:
            channel = ControlChannel(stream)
            channel.send(0x10, mqtt_string('MQTT') + bytes([4, 194]) + struct.pack('!H', 60)
                         + mqtt_string(identity) + mqtt_string(username) + mqtt_string(password))
            if channel.receive() != (0x20, b'\x00\x00'):
                raise RuntimeError('mobile control authentication rejected')
            channel.send(0x82, b'\x00\x01' + mqtt_string('smart/mb/in/' + device_id) + b'\x01'
                         + mqtt_string('/av/u/' + uid) + b'\x01')
            header, ack = channel.receive()
            if header != 0x90 or len(ack) < 3 or 0x80 in ack[2:]:
                raise RuntimeError('mobile control subscription rejected')

            def header_for(kind: str) -> dict:
                return {'type': kind, 'from': uid, 'to': device_id, 'sessionid': session['sessionId'],
                        'moto_id': '', 'path': 'mqtt', 'trace_id': trace_id}

            def publish(protocol: int, data: dict) -> None:
                nonlocal sequence
                sequence += 1
                frame = encode_control(key, sequence, source, {
                    'protocol': protocol, 't': int(time.time()), 'data': data,
                })
                channel.send(0x30, mqtt_string('smart/mb/out/' + device_id) + frame)

            try:
                wake_payload = struct.pack('!I', zlib.crc32(key))
                for _ in range(3):
                    channel.send(0x30, mqtt_string('m/w/' + device_id) + wake_payload)
                    time.sleep(0.1)
                publish(22, {'reqType': 'sigQry'})
                header = header_for('offer')
                header.update({'is_pre': 0, 'p2p_skill': 1635, 'security_level': 3})
                publish(302, {'header': header, 'msg': {
                    'sdp': build_offer(session, uid), 'preconnect': True, 'token': p2p.get('ices', []),
                    'tcp_token': relay_token, 'log': p2p.get('log', {}),
                }})
                deadline = time.monotonic() + 18
                while time.monotonic() < deadline:
                    # Keep framing intact even when TCP fragments a packet.
                    stream.settimeout(max(0.1, deadline - time.monotonic()))
                    packet_header, packet = channel.receive()
                    if packet_header >> 4 != 3:
                        continue
                    topic_length = int.from_bytes(packet[:2], 'big')
                    offset = 2 + topic_length
                    if packet_header & 6:
                        packet_id = packet[offset:offset + 2]
                        offset += 2
                        channel.send(0x40, packet_id)
                    try:
                        message = decode_control(key, packet[offset:])
                    except (ValueError, json.JSONDecodeError):
                        continue
                    data = message.get('data', {})
                    reply = data.get('header', {})
                    if reply.get('sessionid') != session['sessionId']:
                        continue
                    if message.get('protocol') == 302 and reply.get('type') == 'answer':
                        return True
                    if reply.get('type') == 'disconnect':
                        return False
                return False
            finally:
                # Release the native session before the local WebRTC session starts.
                try:
                    publish(302, {'header': header_for('disconnect'),
                                  'msg': {'close_reason': 4, 'close_reason_local': 0}})
                    channel.send(0xE0, b'')
                except OSError:
                    pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--device', required=True)
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--webrtc-config', action='store_true')
    args = parser.parse_args()
    try:
        if args.webrtc_config:
            # The engine captures this output in memory; it must never log it.
            print(json.dumps(mobile_webrtc_config(args.device)), flush=True)
            return 0
        success = wake_device(args.device, args.prepare_only)
        print('Native wake answered' if success else 'Native wake unanswered', flush=True)
        return 0 if success else 1
    except Exception as error:
        # Never print remote payloads, session credentials or exception details.
        print('Native wake failed: ' + type(error).__name__, flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

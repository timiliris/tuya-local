"""Native wake exchanges control messages and never opens a video transport."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import native_wakeup as wake


def test_control_authentication() -> None:
    key = b'0123456789abcdef'
    value = {'protocol': 302, 'data': {'header': {'type': 'answer'}}}
    encoded = wake.encode_control(key, 4, b'\0\0\0\1', value)
    assert wake.decode_control(key, encoded) == value
    for offset in [3, 12, 24, len(encoded) - 1]:
        invalid = bytearray(encoded)
        invalid[offset] ^= 1
        try:
            wake.decode_control(key, bytes(invalid))
        except Exception:
            pass
        else:
            raise AssertionError('Unauthenticated control packet accepted')


def test_mqtt_framing_handles_fragmented_packets_and_bounds() -> None:
    stream = MagicMock()
    stream.recv.side_effect = [b'\x30', b'\x03', b'a', b'b', b'c']
    channel = wake.ControlChannel(stream)
    assert channel.receive() == (0x30, b'abc')
    channel.send(0x30, b'x' * 130)
    assert stream.sendall.call_args.args[0][:3] == b'\x30\x82\x01'
    stream.recv.side_effect = [b'\x30', b'\xff', b'\xff', b'\xff', b'\x7f']
    try:
        channel.receive()
    except ValueError:
        pass
    else:
        raise AssertionError('Unbounded MQTT packet accepted')


def test_native_wake_disconnects_before_local_video_and_checks_session() -> None:
    key = b'0123456789abcdef'
    session = {'sessionId': 'session', 'aesKey': '00' * 16,
               'iceUfrag': 'user', 'icePassword': 'password', 'traceId': 'trace'}
    config = {'p2pConfig': {'session': session, 'ices': [], 'tcpRelay': {'sessionId': 'server-issued', 'credential': 'test-credential'}}}
    client = MagicMock()
    client.load_latest_session.return_value = True
    client.cameras = [{'deviceId': 'camera'}]
    client.login = {'uid': 'user', 'sid': 'session-credential', 'ecode': 'ecode',
                    'domain': {'mobileApiUrl': 'https://example.invalid', 'mobileMqttsUrl': 'mqtt.example.invalid'}}
    client._post.return_value = {'result': {'localKey': key.decode()}}
    mobile = MagicMock()
    mobile._request.return_value = config
    reply = {'protocol': 302, 'data': {'header': {'type': 'answer', 'sessionid': 'session'}}}
    frame = wake.encode_control(key, 1, b'\0' * 4, reply)
    packet = wake.mqtt_string('smart/mb/in/camera') + frame
    foreign = wake.encode_control(key, 2, b'\0' * 4, {'protocol': 302, 'data': {'header': {'type': 'answer', 'sessionid': 'other'}}})
    foreign_packet = wake.mqtt_string('smart/mb/in/camera') + foreign
    stream = MagicMock()
    stream.__enter__.return_value = stream
    with patch.object(wake, 'TuyaClient', return_value=client), \
         patch.object(wake, 'CloudMobile', return_value=mobile), \
         patch.object(wake.socket, 'create_connection') as connect, \
         patch.object(wake.ssl, 'create_default_context') as tls, \
         patch.object(wake.ControlChannel, 'receive', side_effect=[(0x20, b'\0\0'), (0x90, b'\0\1\1'), (0x30, foreign_packet), (0x30, packet)]):
        tls.return_value.wrap_socket.return_value = stream
        assert wake.wake_device('camera') is True
        assert connect.call_count == 1
        assert connect.call_args.args[0] == ('mqtt.example.invalid', 8883)
        assert tls.return_value.wrap_socket.call_args.kwargs['server_hostname'] == 'mqtt.example.invalid'
        sent = [c.args[0] for c in stream.sendall.call_args_list]
        offer_packet = sent[-3]
        pos = 1
        while offer_packet[pos] & 128:
            pos += 1
        body = offer_packet[pos + 1:]
        topic_size = int.from_bytes(body[:2], 'big')
        offer_frame = body[2 + topic_size:]
        assert offer_frame[7:11] == b'\0\0\0\1'
        offer = wake.decode_control(key, offer_frame)['data']
        token = offer['msg']['tcp_token']
        assert token['sessionId'].startswith('camera')
        assert token['sessionId'] != 'server-issued'
        assert token['credential'] == 'test-credential'
        assert offer['header']['sessionid'] == 'session'
        assert sent[-1] == b'\xe0\0'
        # The final publish explicitly releases the native camera session.
        last_publish = sent[-2]
        offset = 1
        while last_publish[offset] & 128:
            offset += 1
        offset += 1
        body = last_publish[offset:]
        topic_length = int.from_bytes(body[:2], 'big')
        decoded = wake.decode_control(key, body[2 + topic_length:])
        assert decoded['data']['header']['type'] == 'disconnect'
        assert decoded['data']['msg']['close_reason'] == 4
    sdp = wake.build_offer(session, 'user')
    assert 'a=candidate:' not in sdp
    assert 'c=IN IP4 0.0.0.0' in sdp


def test_partner_credentials_match_public_vector() -> None:
    username, password = wake.mobile_credentials({
        'sid': 'example-session', 'ecode': 'example-ecode', 'partnerIdentity': 'example-partner',
    })
    assert username == 'example-partner_v1_3cxxt3au9x33ytvq3h9j_3f7060ea_mb_example-sessione746cddda28e9289'
    assert password == 'f7afbd9bd2c9c2c4'


def test_mobile_config_keeps_mobile_auth_and_prepares_session() -> None:
    client = MagicMock()
    client.load_latest_session.return_value = True
    client.cameras = [{'deviceId': 'camera'}]
    client.login = {'sid': 'session', 'domain': {'mobileApiUrl': 'https://example.invalid'}}
    client._post.return_value = {'result': {'localKey': '0123456789abcdef', 'auth': 'web-auth'}}
    mobile = MagicMock()
    mobile._request.side_effect = [True, {'auth': 'mobile-auth', 'p2pConfig': {}}, {}]
    with patch.object(wake, 'TuyaClient', return_value=client), patch.object(wake, 'CloudMobile', return_value=mobile):
        config = wake.mobile_webrtc_config('camera')
    assert config['auth'] == 'mobile-auth'
    assert config['localKey'] == '0123456789abcdef'
    assert [call.args[0] for call in mobile._request.call_args_list] == [
        'smartlife.m.p2p.main.pre.link.get', 'smartlife.m.rtc.config.get', 'smartlife.m.rtc.session.init',
    ]


if __name__ == '__main__':
    test_partner_credentials_match_public_vector()
    test_mobile_config_keeps_mobile_auth_and_prepares_session()
    test_control_authentication()
    test_mqtt_framing_handles_fragmented_packets_and_bounds()
    test_native_wake_disconnects_before_local_video_and_checks_session()
    print('Native wake control tests passed')

"""Account state shared by the Docker and desktop server runtimes."""
from __future__ import annotations
import io
import os
import socket
import threading
import time
import qrcode
from PIL import Image
from paths import tuya_data, user_data
from rtsp_manager import RtspManager
from tuya_client import REGIONS, TuyaClient

client=TuyaClient(tuya_data())
rtsp=RtspManager(user_data())
state_lock=threading.Lock()
ui_state={'phase':'idle','message':''}
client.load_latest_session()
if client.login:ui_state['phase']='logged_in'


def lan_ip():
    if host:=os.environ.get('TUYA_BRIDGE_PUBLIC_HOST'):return host
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as stream:
        try:
            stream.connect(('192.0.2.1',80))
            return stream.getsockname()[0]
        except OSError:return '127.0.0.1'


def public_state():
    ip=lan_ip()
    cameras=[]
    for camera in client.cameras:
        path=camera.get('rtspPath') or '/'+camera['deviceId']
        cameras.append({**camera,'rtspHd':f'rtsp://{ip}:{rtsp.port}{path}/hd',
                        'rtspSd':f'rtsp://{ip}:{rtsp.port}{path}/sd'})
    with state_lock:phase=ui_state['phase']
    return {'phase':phase,'loggedIn':bool(client.login),'region':client.region_id,
            'regions':{key:{'label':value['label']} for key,value in REGIONS.items()},
            'hasQr':bool(client.token) and phase=='waiting','qrId':(client.token or '')[-12:],
            'cameras':cameras,'rtsp':rtsp.status()}


def set_phase(phase, message):
    with state_lock:ui_state.update(phase=phase,message=message)


def restart_rtsp_engine():
    rtsp.stop()
    time.sleep(.4)
    rtsp.start()


def qr_png_bytes() -> bytes:
    """Square QR PNG at fixed pixel size — GUI must not stretch further."""
    payload = client.qr_payload()
    # box_size picked so final image ≈ 320px after border
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    # Normalize to fixed size with NEAREST (module edges stay sharp).
    target = 320
    if img.size != (target, target):
        img = img.resize((target, target), Image.Resampling.NEAREST)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

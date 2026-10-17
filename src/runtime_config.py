"""Shared validated loopback endpoint settings for Docker and desktop."""
import os

def port(name, default):
    value=int(os.environ.get(name,default))
    if not 1<=value<=65535:raise ValueError('Invalid local server port')
    return value

def rtsp_port():return port('TUYA_RTSP_PORT',8554)

def rtsp_host():return '127.0.0.1:'+str(rtsp_port())

WEB_ASSETS=('index.html','app.js','settings.js','ai.js','webrtc.js','display.js','recovery.js','app.css','i18n.js','locales.json','brand.svg','login.html','login.css','login.js','server-settings.js','access.js')


def internal_rtsp_authorization():
    import base64
    password = os.environ.get('TUYA_RTSP_INTERNAL_PASSWORD')
    return 'Basic ' + base64.b64encode(('_tuya_local_engine:' + password).encode()).decode() if password else ''


def internal_rtsp_url(value):
    from urllib.parse import urlsplit, urlunsplit, quote
    password = os.environ.get('TUYA_RTSP_INTERNAL_PASSWORD')
    if not password:
        return value
    parsed = urlsplit(value)
    if parsed.scheme != 'rtsp' or parsed.netloc != rtsp_host() or parsed.username:
        raise ValueError('Only the local RTSP engine is allowed')
    return urlunsplit((parsed.scheme, '_tuya_local_engine:' + quote(password, safe='') + '@' + parsed.netloc, parsed.path, parsed.query, ''))

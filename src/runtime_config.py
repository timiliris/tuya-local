"""Shared validated loopback endpoint settings for Docker and desktop."""
import os

def port(name, default):
    value=int(os.environ.get(name,default))
    if not 1<=value<=65535:raise ValueError('Invalid local server port')
    return value

def rtsp_port():return port('TUYA_RTSP_PORT',8554)

def rtsp_host():return '127.0.0.1:'+str(rtsp_port())

WEB_ASSETS=('index.html','app.js','settings.js','ai.js','webrtc.js','display.js','recovery.js','app.css','i18n.js','locales.json','brand.svg','login.html','login.css','login.js')

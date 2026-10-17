"""Server-owned, bounded local video readers. No browser or cloud media input."""
from __future__ import annotations
from procutil import creationflags
from runtime_config import rtsp_host
import json
import base64
import selectors
import subprocess
import threading
import time
from urllib.parse import urlparse, quote


def source_url(camera, mode):
    path = urlparse(camera.get('rtspSd', '')).path
    if not path.startswith('/') or any(c in path for c in '\r\n'):
        raise ValueError('Invalid local camera path')
    return 'rtsp://'+rtsp_host() + quote(path, safe='/%') + '?ai=' + mode


def effective_mode(camera, config):
    try:
        skill=camera.get('skill',{})
        if isinstance(skill,str):skill=json.loads(skill)
        # Missing capabilities are treated conservatively as a battery camera.
        if not isinstance(skill,dict) or skill.get('lowPower',0) != 0 or not skill.get('videos'):return 'passive'
    except (TypeError,ValueError):return 'passive'
    return config['sourceMode']


def command(url):
    return ['ffmpeg','-hide_banner','-loglevel','error','-rtsp_transport','tcp',
            '-timeout','110000000','-allowed_media_types','video','-threads','1',
            '-i',url,'-map','0:v:0','-an','-vf',"fps=1,scale=640:640:force_original_aspect_ratio=decrease",
            '-threads','1','-c:v','mjpeg','-q:v','5','-f','image2pipe','pipe:1']


class AIWorkers:
    def __init__(self, service, state):
        self.service=service;self.state=state;self.closed=threading.Event()
        self.lock=threading.RLock();self.jobs={};self.statuses={};self.thread=None

    def start(self):
        self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start()

    def status(self, device, **data):
        with self.lock:self.statuses[device]={**self.statuses.get(device,{}),**data}

    def snapshot(self, devices):
        with self.lock:return {device:dict(self.statuses.get(device,{'phase':'disabled'})) for device in devices}

    def sync(self, cameras):
        wanted={c['deviceId']:c for c in cameras if self.service.config(c['deviceId'])['enabled']}
        with self.lock:
            for device,(cancel,thread,mode,url) in list(self.jobs.items()):
                config=self.service.config(device)
                if device not in wanted or effective_mode(wanted.get(device,{}),config)!=mode or source_url(wanted[device],mode)!=url:
                    cancel.set()
                if not thread.is_alive():self.jobs.pop(device)
            for camera in cameras:
                device=camera['deviceId']
                if device not in wanted:
                    self.statuses[device]={'phase':'disabled'}
                    continue
                if device in self.jobs:continue
                if len(self.jobs)>=8:
                    self.statuses[device]={'phase':'capacity'};continue
                mode=effective_mode(camera,self.service.config(device));url=source_url(camera,mode)
                self.statuses[device]={'phase':'connecting','sourceMode':mode}
                cancel=threading.Event();thread=threading.Thread(target=self.read,args=(device,url,mode,cancel),daemon=True)
                self.jobs[device]=(cancel,thread,mode,url);thread.start()

    def run(self):
        while not self.closed.is_set():
            try:self.sync(self.state().get('cameras',[]))
            except Exception:pass
            self.closed.wait(1)

    def read(self, device, url, mode, cancel):
        failures=0
        while not cancel.is_set() and not self.closed.is_set():
            proc=None
            self.status(device,phase='connecting')
            try:
                proc=subprocess.Popen(command(url),stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,creationflags=creationflags())
                with selectors.DefaultSelector() as selector:
                    selector.register(proc.stdout,selectors.EVENT_READ)
                    buffer=b'';last=time.monotonic();received=False
                    while not cancel.is_set() and not self.closed.is_set():
                        if time.monotonic()-last>(20 if received else 120):break
                        if not selector.select(.5):continue
                        chunk=proc.stdout.read1(65536)
                        if not chunk:break
                        buffer+=chunk
                        if len(buffer)>500000:raise ValueError('Oversized local frame')
                        while b'\xff\xd9' in buffer:
                            end=buffer.index(b'\xff\xd9')+2;jpeg=buffer[:end];buffer=buffer[end:]
                            if not jpeg.startswith(b'\xff\xd8'):continue
                            last=time.monotonic();received=True;failures=0
                            result=self.service.analyze(device,base64.b64encode(jpeg).decode())
                            self.status(device,phase='analyzing',lastFrame=time.time())
            except Exception:
                self.status(device,phase='retrying')
            finally:
                if proc is not None:
                    if proc.poll() is None:
                        proc.terminate()
                        try:proc.wait(2)
                        except subprocess.TimeoutExpired:proc.kill();proc.wait()
                    proc.stdout.close()
            failures+=1
            delay=5 if mode=='passive' else min(60,5*2**min(failures,4))
            self.status(device,phase='waiting' if mode=='passive' else 'retrying',retryAt=time.time()+delay)
            cancel.wait(delay)

    def stop(self):
        self.closed.set()
        with self.lock:
            jobs=list(self.jobs.values())
            for cancel,_,_,_ in jobs:cancel.set()
        if self.thread:self.thread.join(3)
        for _,thread,_,_ in jobs:thread.join(4)

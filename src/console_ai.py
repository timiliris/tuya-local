"""Optional local object detection and bounded local event history."""
from __future__ import annotations
import base64
from contextlib import contextmanager
import copy
import io
import json
import math
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from PIL import Image
from ai_models import CLASSES, MODELS, Detector

DEFAULT = {'enabled': False, 'model': 'nanodet-int8', 'classes': ['person','car','dog','cat'],
           'confidence': .5, 'interval': 2, 'confirmations': 2, 'cooldown': 30,
           'minArea': .001, 'zone': [0.,0.,1.,1.], 'snapshots': False, 'sourceMode': 'passive'}
MAX_EVENTS = 500
RETENTION = 7 * 86400


def number(value, low, high):
    return type(value) in (int,float) and math.isfinite(value) and low <= value <= high


def validate_config(data):
    if not isinstance(data, dict) or set(data)-set(DEFAULT) or set(DEFAULT)-set(data)-{'sourceMode'}:
        raise ValueError('Réglages IA invalides.')
    data={**DEFAULT,**data}
    if data['sourceMode'] not in ('passive','continuous'):raise ValueError('Réglages IA invalides.')
    if type(data['enabled']) is not bool or type(data['snapshots']) is not bool or not isinstance(data['model'],str) or data['model'] not in MODELS:
        raise ValueError('Réglages IA invalides.')
    classes = data['classes']
    if not isinstance(classes,list) or not classes or len(classes)>len(CLASSES) or any(not isinstance(c,str) or c not in CLASSES for c in classes) or len(set(classes)) != len(classes):
        raise ValueError('Choisis au moins un type d’objet.')
    if not number(data['confidence'],.25,.95) or not number(data['minArea'],0,.5) or data['interval'] not in (1,2,5) or type(data['interval']) is not int or data['confirmations'] not in (1,2,3) or type(data['confirmations']) is not int or data['cooldown'] not in (15,30,60):
        raise ValueError('Réglages IA invalides.')
    zone = data['zone']
    if not isinstance(zone,list) or len(zone)!=4 or any(not number(v,0,1) for v in zone) or zone[2]-zone[0]<.05 or zone[3]-zone[1]<.05:
        raise ValueError('Zone d’analyse invalide.')
    return copy.deepcopy(data)


def filter_objects(objects, config):
    result=[]
    for item in objects:
        name, score, box=item.get('class'),item.get('score'),item.get('box')
        if name not in config['classes'] or not number(score,config['confidence'],1) or not isinstance(box,list) or len(box)!=4 or any(not number(v,0,1) for v in box):continue
        x1,y1,x2,y2=box
        if (x2-x1)*(y2-y1)<config['minArea'] or x2<=x1 or y2<=y1:continue
        zx1,zy1,zx2,zy2=config['zone'];cx,cy=(x1+x2)/2,(y1+y2)/2
        if zx1<=cx<=zx2 and zy1<=cy<=zy2:
            result.append({'class':name,'score':round(score,4),'box':[round(v,5) for v in box]})
    return sorted(result,key=lambda item:item['score'],reverse=True)[:30]


def decode_frame(encoded):
    if not isinstance(encoded,str) or len(encoded)>350000:raise ValueError('Image d’analyse invalide.')
    try:
        jpeg=base64.b64decode(encoded,validate=True)
        if len(jpeg)>250000:raise ValueError()
        with Image.open(io.BytesIO(jpeg)) as image:
            if image.format!='JPEG' or image.width>640 or image.height>640 or min(image.size)<16:raise ValueError()
            image.verify()
    except Exception:
        raise ValueError('Image d’analyse invalide.') from None
    return jpeg


class LocalAI:
    def __init__(self, directory: Path, detector=None, clock=time.time, monotonic=time.monotonic):
        self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=True)
        self.config_file=self.directory/'ai-config.json';self.database=self.directory/'ai-events.sqlite'
        self.lock=threading.RLock();self.infer_gate=threading.Lock();self.configs={};self.revision={};self.last_frame={};self.tracks={};self.results={};self.workers=None
        self.detector=detector or Detector(Path(__file__).resolve().parents[1]/'models')
        self.clock=clock;self.monotonic=monotonic
        try:
            saved=json.loads(self.config_file.read_text())
            self.configs={device:validate_config(config) for device,config in saved.items()}
        except (OSError,ValueError,TypeError):pass
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, time REAL, device TEXT, class TEXT, score REAL, box TEXT, model TEXT, image BLOB)')
            db.execute('CREATE INDEX IF NOT EXISTS event_time ON events(time)')
        self.database.chmod(0o600)

    @contextmanager
    def connect(self):
        connection=sqlite3.connect(self.database)
        try:
            with connection:yield connection
        finally:connection.close()

    def config(self, device):
        with self.lock:return copy.deepcopy(self.configs.get(device,DEFAULT))

    def state(self, devices):
        return {'models':[{'id':key,'name':m['name'],'bytes':m['size']} for key,m in MODELS.items()],
                'classes':[{'id':key,'label':value[1]} for key,value in CLASSES.items()],
                'cameras':{device:self.config(device) for device in devices},
                'localOnly':True,'retentionDays':7,'maxEvents':MAX_EVENTS,'mode':'server', 'results':{d:copy.deepcopy(self.results.get(d,{})) for d in devices},
                'workers':self.workers.snapshot(devices) if self.workers else {}}

    def configure(self, device, data):
        config=validate_config(data)
        with self.lock:
            updated={**self.configs,device:config}
            temp=self.config_file.with_suffix('.tmp');temp.write_text(json.dumps(updated));temp.chmod(0o600);temp.replace(self.config_file)
            self.configs=updated;self.revision[device]=self.revision.get(device,0)+1
            self.tracks={key:value for key,value in self.tracks.items() if key[0]!=device}
            self.last_frame.pop(device,None)
            self.results.pop(device,None)
        return config

    def prune(self, db):
        db.execute('DELETE FROM events WHERE time < ?', (self.clock()-RETENTION,))
        db.execute('DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY time DESC LIMIT ?)',(MAX_EVENTS,))

    def analyze(self, device, encoded):
        with self.lock:
            config=self.config(device);revision=self.revision.get(device,0)
            if not config['enabled']:return {'disabled':True,'objects':[]}
            now=self.monotonic()
            if now-self.last_frame.get(device,-1e10)<config['interval'] or not self.infer_gate.acquire(blocking=False):return {'skipped':True}
            self.last_frame[device]=now
        started=self.monotonic()
        try:
            jpeg=decode_frame(encoded)
            objects=filter_objects(self.detector.infer(jpeg,config['model']),config)
            with self.lock:
                if revision!=self.revision.get(device,0):return {'skipped':True}
                detected={}
                for obj in objects:detected.setdefault(obj['class'],obj)
                for key in list(self.tracks):
                    if key[0]==device and key[1] not in detected:self.tracks[key]['hits']=0
                with self.connect() as db:
                    for name,obj in detected.items():
                        key=(device,name);previous=self.tracks.get(key,{'hits':0,'last':-1e10,'event':-1e10})
                        hits=previous['hits']+1 if now-previous['last']<=max(6,config['interval']*3) else 1
                        track={'hits':hits,'last':now,'event':previous['event']};self.tracks[key]=track
                        if hits>=config['confirmations'] and now-track['event']>=config['cooldown']:
                            db.execute('INSERT INTO events VALUES (?,?,?,?,?,?,?,?)',
                                       (uuid.uuid4().hex,self.clock(),device,name,obj['score'],json.dumps(obj['box']),config['model'],jpeg if config['snapshots'] else None))
                            track['event']=now
                    self.prune(db)
                result={'objects':objects,'elapsedMs':round((self.monotonic()-started)*1000),'model':config['model'],
                        'time':self.clock(),'confirmed':[name for name in detected if self.tracks[(device,name)]['hits']>=config['confirmations']]}
                self.results[device]=result
            return result
        finally:self.infer_gate.release()

    def events(self, devices):
        with self.lock,self.connect() as db:
            self.prune(db)
            rows=db.execute('SELECT id,time,device,class,score,box,model,image IS NOT NULL FROM events ORDER BY time DESC LIMIT 500').fetchall()
        return [{'id':row[0],'time':row[1],'deviceId':row[2],'class':row[3],'score':row[4],'box':json.loads(row[5]),'model':row[6],'snapshot':bool(row[7])} for row in rows if row[2] in devices]

    def snapshot(self, event, devices):
        if not isinstance(event,str) or len(event)!=32:raise ValueError('Événement introuvable.')
        with self.lock,self.connect() as db:
            self.prune(db)
            row=db.execute('SELECT image,device FROM events WHERE id=?',(event,)).fetchone()
        if not row or not row[0] or row[1] not in devices:raise ValueError('Événement introuvable.')
        return row[0]

    def cleanup(self):
        with self.lock,self.connect() as db:self.prune(db)

    def clear(self):
        with self.lock,self.connect() as db:
            db.execute('DELETE FROM events');self.tracks.clear()
        return {'ok':True}

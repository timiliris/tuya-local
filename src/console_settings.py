"""Discover typed camera settings. Only curated DPs cross the public API boundary.

Device metadata also contains account and LAN keys: never return or persist it.
Reported values are Tuya's cached state, not proof of physical execution.
"""
from __future__ import annotations

import json
import secrets
import requests
from contextlib import nullcontext
from console_settings_wake import local_camera_awake, check_cancel
import threading
import time
from pathlib import Path
from cloud_mobile import CloudMobile
from tuya_client import TuyaClient

# code: (translated label, section, expected type). No arbitrary DP writes.
CONTROLS = {
    'basic_indicator': ('Voyant de la caméra', 'Image', 'bool'),
    'basic_flip': ('Retourner l’image', 'Image', 'bool'),
    'basic_osd': ('Date et heure sur l’image', 'Image', 'bool'),
    'basic_private': ('Mode confidentialité', 'Image', 'bool'),
    'basic_nightvision': ('Vision infrarouge', 'Image', 'enum'),
    'nightvision_mode': ('Vision nocturne', 'Image', 'enum'),
    'basic_anti_flicker': ('Antiscintillement', 'Image', 'enum'),
    'floodlight_switch': ('Projecteur', 'Image', 'bool'),
    'motion_switch': ('Détection de mouvement', 'Détection', 'bool'),
    'motion_sensitivity': ('Sensibilité au mouvement', 'Détection', 'enum'),
    'humanoid_filter': ('Filtrage des personnes', 'Détection', 'bool'),
    'motion_tracking': ('Suivi du mouvement', 'Détection', 'bool'),
    'pir_switch': ('Détection PIR', 'Détection', 'enum'),
    'decibel_switch': ('Détection sonore', 'Détection', 'bool'),
    'decibel_sensitivity': ('Sensibilité sonore', 'Détection', 'enum'),
    'ipc_object_outline': ('Encadrer les objets détectés', 'Détection', 'bool'),
    'record_switch': ('Enregistrement sur carte SD', 'Enregistrement', 'bool'),
    'record_mode': ('Mode d’enregistrement', 'Enregistrement', 'enum'),
    'md_record_switch': ('Enregistrer les mouvements', 'Enregistrement', 'bool'),
    'hd_record_switch': ('Enregistrer les personnes', 'Enregistrement', 'bool'),
    'sd_record_switch': ('Enregistrer les sons', 'Enregistrement', 'bool'),
    'wireless_lowpower': ('Seuil d’alerte batterie', 'Batterie et stockage', 'value'),
    'wireless_electricity': ('Batterie', 'Batterie et stockage', 'value'),
    'wireless_powermode': ('Alimentation', 'Batterie et stockage', 'enum'),
    'sd_status': ('État de la carte SD', 'Batterie et stockage', 'value'),
    'sd_storge': ('Capacité de la carte SD', 'Batterie et stockage', 'string'),
}
READ_ONLY = {'wireless_electricity', 'wireless_powermode', 'sd_status', 'sd_storge'}
OPTIONS = {
    'basic_nightvision': {'0': 'Automatique', '1': 'Désactivé', '2': 'Activé'},
    'basic_anti_flicker': {'0': 'Désactivé', '1': '50 Hz', '2': '60 Hz'},
    'motion_sensitivity': {'0': 'Faible', '1': 'Moyenne', '2': 'Élevée'},
    'decibel_sensitivity': {'0': 'Faible', '1': 'Élevée'},
    'record_mode': {'1': 'Sur événement', '2': 'En continu'},
    'nightvision_mode': {'ir_mode': 'Infrarouge', 'auto': 'Automatique'},
    'wireless_powermode': {'0': 'Batterie', '1': 'Secteur ou en charge'},
    'sd_status': {'1': 'Normale', '2': 'Erreur', '3': 'Espace insuffisant', '4': 'Formatage en cours', '5': 'Aucune carte SD'},
}


def schema_items(metadata):
    schema = metadata.get('schema', [])
    if isinstance(schema, str):
        schema = json.loads(schema)
    if not isinstance(schema, list):
        raise ValueError('Les réglages de cette caméra sont indisponibles.')
    return [item for item in schema if isinstance(item, dict)]


def describe(metadata):
    items = []
    dps = metadata.get('dps') or {}
    for item in schema_items(metadata):
        code = item.get('code')
        if code not in CONTROLS:
            continue
        label, group, kind = CONTROLS[code]
        prop = item.get('property') or {}
        if prop.get('type') != kind or item.get('mode') not in {'ro', 'rw'}:
            continue
        dp = str(item.get('id', ''))
        if not dp.isdigit():
            continue
        value = dps.get(dp)
        valid = (value is None or kind == 'bool' and type(value) is bool
                 or kind == 'enum' and isinstance(value, str)
                 or kind == 'value' and type(value) is int
                 or kind == 'string' and isinstance(value, str) and len(value) <= 255)
        field = {'code': code, 'label': label, 'group': group, 'type': kind,
                 'writable': item['mode'] == 'rw' and code not in READ_ONLY,
                 'value': value if valid else None, 'unit': str(prop.get('unit', ''))[:12]}
        if kind == 'enum':
            choices = prop.get('range')
            if not isinstance(choices, list) or not choices or len(choices) > 50 or any(not isinstance(v, str) or len(v) > 80 for v in choices):
                continue
            field['options'] = [{'value': v, 'label': OPTIONS.get(code, {}).get(v)} for v in choices]
        if kind == 'value':
            for key, default in [('min', 0), ('max', 100), ('step', 1), ('scale', 0)]:
                field[key] = prop.get(key, default)
            if any(type(field[k]) is not int for k in ('min', 'max', 'step', 'scale')) or not 0 <= field['scale'] <= 6 or field['step'] <= 0 or field['max'] < field['min']:
                continue
        if code == 'sd_status' and value is not None:
            field['valueLabel'] = OPTIONS[code].get(str(value))
        items.append(field)
    return {'settings': items, 'via': 'cloud', 'videoLocal': True,
            'source': 'tuya_reported', 'readAt': int(time.time()),
            'autoWake': any(item.get('code') == 'wireless_electricity' for item in schema_items(metadata))}


def validate(field, value):
    kind = field['type']
    if not field['writable']:
        raise ValueError('Ce réglage est en lecture seule.')
    if kind == 'bool' and type(value) is bool:
        return
    if kind == 'enum' and isinstance(value, str) and value in {item['value'] for item in field['options']}:
        return
    if kind == 'value' and type(value) is int and field['min'] <= value <= field['max'] and (value - field['min']) % field['step'] == 0:
        return
    raise ValueError('Valeur de réglage invalide.')


class Settings:
    def __init__(self, data_dir: Path, ensure_engine=None):
        self.data_dir = data_dir
        self.locks = {}
        self.guard = threading.Lock()
        self.jobs = {}
        self.ensure_engine = ensure_engine

    def connection(self, device):
        client = TuyaClient(self.data_dir)
        if not client.load_latest_session():
            raise ValueError('Connecte ton compte Tuya avant de régler une caméra.')
        if not isinstance(device, str) or not any(cam.get('deviceId') == device for cam in client.cameras):
            raise ValueError('Caméra inconnue.')
        mobile = CloudMobile(Path('/tmp/tuya-local-settings'))
        mobile._sid = client.login['sid']
        mobile._endpoint = client.login['domain']['mobileApiUrl'].rstrip('/') + '/api.json'
        return mobile

    def request(self, body, write=False, progress=None, cancel=None):
        device = body.get('deviceId')
        mobile = self.connection(device)
        if not write:
            return self.exchange(mobile, device, body)
        with self.guard:
            lock = self.locks.setdefault(device, threading.Lock())
        # Separate from runtime.lock: slow cloud requests must not block playback.
        with lock:
            return self.exchange(mobile, device, body, write, progress=progress, cancel=cancel)

    def start(self, body):
        device = body.get('deviceId')
        self.connection(device)
        with self.guard:
            self.jobs = {token: job for token, job in self.jobs.items()
                         if not job['done'] or time.monotonic() - job['updated'] < 300}
            if any(job['device'] == device and not job['done'] for job in self.jobs.values()):
                raise ValueError('Une modification est déjà en cours sur cette caméra.')
            if len(self.jobs) >= 16:
                raise ValueError('Trop de modifications récentes. Réessaie dans quelques instants.')
            token = secrets.token_urlsafe(24)
            self.jobs[token] = {'device': device, 'stage': 'reading', 'done': False,
                                'updated': time.monotonic(), 'cancel': threading.Event()}
        def progress(stage):
            with self.guard:
                if stage == 'applying':
                    check_cancel(self.jobs[token]['cancel'])
                self.jobs[token]['stage'] = stage
        def work():
            try:
                result = self.request(body, write=True, progress=progress, cancel=self.jobs[token]['cancel'])
                with self.guard:
                    self.jobs[token]['result'] = result
                    self.jobs[token]['stage'] = result['writeStatus']
            except Exception as error:
                with self.guard:
                    # Only our curated validation errors may reach the browser.
                    self.jobs[token]['error'] = str(error) if isinstance(error, ValueError) else 'La modification a échoué. Actualise les valeurs avant de réessayer.'
                    self.jobs[token]['stage'] = 'error'
            finally:
                with self.guard:
                    self.jobs[token]['done'] = True
                    self.jobs[token]['updated'] = time.monotonic()
        threading.Thread(target=work, daemon=True).start()
        return {'job': token}

    def job_state(self, token, cancel=False):
        with self.guard:
            job = self.jobs.get(token) if isinstance(token, str) else None
            if job is None:
                raise ValueError('Opération introuvable. Actualise les valeurs.')
            if cancel and not job['done'] and job['stage'] in {'reading', 'waking'}:
                job['cancel'].set()
            return {key: job[key] for key in ('stage', 'done', 'result', 'error') if key in job}

    def camera_wake(self, device, cancel):
        client = TuyaClient(self.data_dir)
        if not client.load_latest_session():
            raise ValueError('Connecte ton compte Tuya avant de régler une caméra.')
        camera = next((item for item in client.cameras if item.get('deviceId') == device), None)
        if camera is None or not camera.get('rtspPath'):
            raise ValueError('Caméra inconnue.')
        if self.ensure_engine:
            self.ensure_engine()
        return local_camera_awake(camera['rtspPath'], cancel)

    def exchange(self, mobile, device, body, write=False, progress=None, cancel=None):
        progress = progress or (lambda stage: None)
        def read():
            for attempt in range(2):
                try:
                    result = mobile._request('thing.m.device.get', {'devId': device})
                    if not isinstance(result, dict):
                        raise ValueError('Les réglages de cette caméra sont indisponibles.')
                    return result
                except (RuntimeError, requests.RequestException):
                    if attempt:
                        raise
                    check_cancel(cancel)
                    time.sleep(.6)
        def prepare(metadata):
            result = describe(metadata)
            changes = body.get('changes', [body])
            if not isinstance(changes, list) or not 1 <= len(changes) <= len(CONTROLS):
                raise ValueError('Valeur de réglage invalide.')
            fields = {item['code']: item for item in result['settings']}
            seen, changed = set(), []
            for change in changes:
                if not isinstance(change, dict) or not isinstance(change.get('code'), str):
                    raise ValueError('Réglage non pris en charge.')
                code, value = change['code'], change.get('value')
                if code in seen or code not in fields:
                    raise ValueError('Réglage non pris en charge.')
                seen.add(code)
                field = fields[code]
                validate(field, value)
                if 'expectedValue' not in change or type(change['expectedValue']) is not type(field['value']) or change['expectedValue'] != field['value']:
                    raise ValueError('Le réglage a changé. Actualise les valeurs avant de réessayer.')
                if type(value) is not type(field['value']) or value != field['value']:
                    changed.append(change)
            return result, changed
        check_cancel(cancel)
        metadata = read()
        result = describe(metadata)
        if not write:
            return result
        result, changes = prepare(metadata)
        if not changes:
            result['writeStatus'] = 'unchanged'
            return result
        # Privacy mode intentionally prevents video; do not circumvent it.
        private = next((item['value'] for item in result['settings'] if item['code'] == 'basic_private'), False)
        should_wake = result['autoWake'] and not private and self.ensure_engine is not None
        if should_wake:
            progress('waking')
        with self.camera_wake(device, cancel) if should_wake else nullcontext():
            check_cancel(cancel)
            if should_wake:
                # Detect phone/app edits made during a potentially long wake.
                metadata = read()
                result, changes = prepare(metadata)
                if not changes:
                    result['writeStatus'] = 'unchanged'
                    return result
            ids = {item.get('code'): str(item['id']) for item in schema_items(metadata) if 'id' in item}
            check_cancel(cancel)
            progress('applying')
            accepted = mobile._request('thing.m.device.dp.publish', {
                'devId': device, 'gwId': device,
                'dps': json.dumps({ids[item['code']]: item['value'] for item in changes}, separators=(',', ':')),
            })
            if accepted is not True:
                raise RuntimeError('La caméra a refusé le réglage.')
            progress('confirming')
            result['writeStatus'] = 'pending'
            confirmation_deadline = time.monotonic() + 10
            for _ in range(6 if should_wake else 2):
                if time.monotonic() >= confirmation_deadline:
                    break
                time.sleep(1 if should_wake else .8)
                try:
                    result = describe(read())
                except Exception:
                    break
                fields = {item['code']: item['value'] for item in result['settings']}
                result['writeStatus'] = 'pending'
                if all(type(fields.get(item['code'])) is type(item['value']) and fields[item['code']] == item['value'] for item in changes):
                    result['writeStatus'] = 'reported'
                    break
            result['writeStatus'] = result.get('writeStatus', 'pending')
            return result

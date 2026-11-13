"""AISStream arrival checks and Windows user-protected key storage."""
import ctypes
from ctypes import wintypes
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import websocket

KEY_FILE = Path.home() / 'Documents' / 'ActivTrack Reports' / 'arrival_aisstream.key'
BOX = [[[5.5, 78.5], [8.5, 81.5]]]

class Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_byte))]

def _crypt(raw, decrypt=False):
    source = ctypes.create_string_buffer(raw)
    src = Blob(len(raw), ctypes.cast(source, ctypes.POINTER(ctypes.c_byte)))
    dst = Blob()
    crypt = ctypes.windll.crypt32
    if decrypt:
        ok = crypt.CryptUnprotectData(ctypes.byref(src), None, None, None, None, 1, ctypes.byref(dst))
    else:
        ok = crypt.CryptProtectData(ctypes.byref(src), None, None, None, None, 1, ctypes.byref(dst))
    if not ok:
        raise OSError('Windows could not protect or open the saved key')
    try:
        return ctypes.string_at(dst.data, dst.size)
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(dst.data, ctypes.c_void_p))

def save_key(key):
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    KEY_FILE.write_bytes(_crypt(key.encode('utf-8')))

def load_key():
    try:
        return _crypt(KEY_FILE.read_bytes(), True).decode('utf-8')
    except (OSError, ValueError, UnicodeError):
        return ''

def name_key(name):
    return re.sub(r'\s+', ' ', name.strip().upper())

def observation(data, wanted):
    if data.get('MessageType') != 'PositionReport': return None
    meta, msg = data.get('MetaData') or {}, (data.get('Message') or {}).get('PositionReport') or {}
    name = name_key(str(meta.get('ShipName') or ''))
    mmsi = str(meta.get('MMSI') or msg.get('UserID') or '')
    if name not in wanted or not re.fullmatch(r'\d{9}', mmsi) or msg.get('Valid') is False: return None
    raw = str(meta.get('time_utc') or '')
    try:
        # AISStream uses Go UTC timestamps, sometimes with nanoseconds.
        match = re.match(r'^(\d{4}-\d\d-\d\d)[ T](\d\d:\d\d:\d\d)', raw)
        if not match: return None
        seen = datetime.strptime(' '.join(match.groups()), '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc)-seen).total_seconds()
        if age < -300 or age > 7200: return None
    except (TypeError, ValueError): return None
    status, label = {0: ('UNDERWAY','Underway by engine'), 1: ('ANCHOR','At anchor'), 5: ('MOORED','Moored')}.get(msg.get('NavigationalStatus'), ('UNKNOWN','AIS status unavailable'))
    return name, mmsi, {'status':status, 'label':label, 'ais_at':seen.isoformat(), 'checked_at':datetime.now().astimezone().isoformat(timespec='seconds'), 'mmsi':mmsi}

def check_vessels(items, key, seconds=60):
    wanted = {name_key(item['name']) for item in items}
    found = {name: {} for name in wanted}
    ws = None
    confirmed = False
    error = None
    try:
        ws = websocket.create_connection('wss://stream.aisstream.io/v0/stream', timeout=12)
        ws.send(json.dumps({'APIKey':key, 'BoundingBoxes':BOX, 'FilterMessageTypes':['PositionReport']}))
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            ws.settimeout(min(3, max(.1,end-time.monotonic())))
            try: data = json.loads(ws.recv())
            except websocket.WebSocketTimeoutException: continue
            if data.get('MessageType') == 'SubscriptionConfirmation': confirmed = True; continue
            if 'error' in data or 'Error' in data: raise ValueError('AISStream rejected the subscription')
            item = observation(data, wanted)
            if item:
                name, mmsi, result = item
                found[name][mmsi] = result
    except Exception:
        error = 'AISStream connection failed or access was rejected'
    finally:
        if ws:
            try: ws.close()
            except Exception: pass
    result = {}
    for name, records in found.items():
        if error: result[name] = {'status':'UNKNOWN','label':error}
        elif not confirmed: result[name] = {'status':'UNKNOWN','label':'AISStream did not confirm subscription'}
        elif len(records) > 1: result[name] = {'status':'UNKNOWN','label':'Multiple vessels match this name; verify identity'}
        elif records: result[name] = next(iter(records.values()))
        else: result[name] = {'status':'UNKNOWN','label':'No recent signal received near Sri Lanka'}
    return result

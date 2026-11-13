"""TinyFish secret storage protected by Windows DPAPI for the current user."""
import ctypes
import os
from ctypes import wintypes
from pathlib import Path

KEY_PATH = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData' / 'Local'))) / 'Globactiv' / 'VesselArrivalAlerts' / 'tinyfish.key'


class Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def protect(raw, decrypt=False):
    if os.name != 'nt':
        raise OSError('Windows encrypted storage is required')
    buffer = ctypes.create_string_buffer(raw)
    source = Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.restype = wintypes.BOOL
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    # No machine-wide flag: the encrypted secret belongs to this Windows user.
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise OSError('Windows could not access encrypted key storage')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel.LocalFree(ctypes.cast(output.data, ctypes.c_void_p))


def save_key(key, path=KEY_PATH):
    if not key.strip():
        raise ValueError('Enter an API key first')
    encrypted = protect(key.strip().encode('utf-8'))
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_bytes(encrypted)
    temp.replace(path)


def load_key(path=KEY_PATH):
    try:
        return protect(path.read_bytes(), decrypt=True).decode('utf-8')
    except (OSError, ValueError, UnicodeError):
        return ''


def forget_key(path=KEY_PATH):
    path.unlink(missing_ok=True)

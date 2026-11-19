"""Protect the local API key with Windows DPAPI; never return it from the API."""
import ctypes
import os
from ctypes import wintypes
from . import store


class Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _crypt(data, decrypt=False):
    if os.name != "nt":
        raise RuntimeError("This credential store requires Windows DPAPI.")
    memory = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(memory, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    lib = ctypes.WinDLL("crypt32", use_last_error=True)
    fn = lib.CryptUnprotectData if decrypt else lib.CryptProtectData
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    fn.restype = wintypes.BOOL
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        free = ctypes.WinDLL("kernel32").LocalFree
        free.argtypes = [ctypes.c_void_p]
        free(ctypes.cast(target.data, ctypes.c_void_p))


def configured():
    return (store.DATA / "youtube-key.dpapi").is_file()


def save_key(key):
    if not 15 <= len(key.strip()) <= 256 or any(c.isspace() for c in key.strip()):
        raise ValueError("Enter a valid YouTube Data API key.")
    store.DATA.mkdir(exist_ok=True, parents=True)
    tmp = store.DATA / "youtube-key.tmp"
    tmp.write_bytes(_crypt(key.strip().encode()))
    os.replace(tmp, store.DATA / "youtube-key.dpapi")


def get_key():
    if not configured():
        raise RuntimeError("Add a YouTube Data API key in Monitor setup.")
    return _crypt((store.DATA / "youtube-key.dpapi").read_bytes(), True).decode()

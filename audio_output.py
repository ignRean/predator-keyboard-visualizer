"""
audio_output.py - Reads the *current* Windows default playback device name.

PortAudio only enumerates devices once at startup, so it can't tell when you
switch outputs. This asks Windows Core Audio (IMMDeviceEnumerator) directly
through ctypes; no extra packages needed.
"""

import ctypes
from ctypes import POINTER, byref, c_uint, c_ushort, c_void_p, c_wchar_p

ole32 = ctypes.OleDLL("ole32")


class GUID(ctypes.Structure):
    _fields_ = [("d1", ctypes.c_uint32), ("d2", ctypes.c_uint16), ("d3", ctypes.c_uint16), ("d4", ctypes.c_ubyte * 8)]

    def __init__(self, s):
        super().__init__()
        ole32.CLSIDFromString(c_wchar_p("{" + s + "}"), byref(self))


class PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", ctypes.c_uint32)]


class PROPVARIANT(ctypes.Structure):
    _fields_ = [("vt", c_ushort), ("r1", c_ushort), ("r2", c_ushort), ("r3", c_ushort),
                ("pwszVal", c_wchar_p), ("pad", c_void_p)]


CLSID_MMDeviceEnumerator = "BCDE0395-E52F-467C-8E3D-C4579291692E"
IID_IMMDeviceEnumerator = "A95664D2-9614-4F35-A746-DE8DB63617E6"
PKEY_FRIENDLY_NAME = ("A45C254E-DF1C-4EFD-8020-67D146A850E0", 14)
CLSCTX_ALL = 0x17
E_RENDER, E_CONSOLE, STGM_READ = 0, 0, 0
VT_LPWSTR = 31


def _method(ptr, index, *argtypes):
    """Fetch COM vtable entry `index` of interface pointer `ptr` as a callable."""
    vtbl = ctypes.cast(ptr, POINTER(POINTER(c_void_p))).contents
    proto = ctypes.WINFUNCTYPE(ctypes.HRESULT, c_void_p, *argtypes)
    fn = proto(vtbl[index])
    return lambda *a: fn(ptr, *a)


def _release(ptr):
    if ptr:
        vtbl = ctypes.cast(ptr, POINTER(POINTER(c_void_p))).contents
        ctypes.WINFUNCTYPE(c_uint, c_void_p)(vtbl[2])(ptr)


def default_output_name():
    """Friendly name of the default playback device, e.g. 'Headphones (Realtek(R) Audio)', or '' on failure."""
    try:
        ole32.CoInitializeEx(None, 0)  # COINIT_MULTITHREADED; harmless if already initialized
    except OSError:
        pass
    enum = dev = store = None
    try:
        enum = c_void_p()
        ole32.CoCreateInstance(byref(GUID(CLSID_MMDeviceEnumerator)), None, CLSCTX_ALL,
                               byref(GUID(IID_IMMDeviceEnumerator)), byref(enum))
        dev = c_void_p()
        _method(enum, 4, c_uint, c_uint, POINTER(c_void_p))(E_RENDER, E_CONSOLE, byref(dev))  # GetDefaultAudioEndpoint
        store = c_void_p()
        _method(dev, 4, c_uint, POINTER(c_void_p))(STGM_READ, byref(store))                    # OpenPropertyStore
        key = PROPERTYKEY(GUID(PKEY_FRIENDLY_NAME[0]), PKEY_FRIENDLY_NAME[1])
        pv = PROPVARIANT()
        _method(store, 5, POINTER(PROPERTYKEY), POINTER(PROPVARIANT))(byref(key), byref(pv))   # GetValue
        name = pv.pwszVal if pv.vt == VT_LPWSTR else ""
        ole32.PropVariantClear(byref(pv))
        return name or ""
    except OSError:
        return ""
    finally:
        for p in (store, dev, enum):
            _release(p)


def is_wave_link(name):
    n = (name or "").lower()
    return "elgato" in n or "wave link" in n


if __name__ == "__main__":
    name = default_output_name()
    print(repr(name), "-> Wave Link" if is_wave_link(name) else "")

"""Windows foreground Raw Input reader; identifies the source device.

Does not install hooks, suppress global input, or change keyboard configuration.
"""
import ctypes as C
from ctypes import wintypes as W


class RawHeader(C.Structure):
    _fields_ = [('type', W.DWORD), ('size', W.DWORD), ('device', W.HANDLE), ('wparam', C.c_size_t)]


class RawKey(C.Structure):
    _fields_ = [('scan', W.USHORT), ('flags', W.USHORT), ('reserved', W.USHORT),
                ('vkey', W.USHORT), ('message', W.UINT), ('extra', W.ULONG)]


class Registration(C.Structure):
    _fields_ = [('page', W.USHORT), ('usage', W.USHORT), ('flags', W.DWORD), ('window', W.HWND)]


class KeyboardReader:
    def __init__(self, window_id):
        self.api = C.WinDLL('user32', use_last_error=True)
        self.api.RegisterRawInputDevices.argtypes = [C.POINTER(Registration), W.UINT, W.UINT]
        self.api.RegisterRawInputDevices.restype = W.BOOL
        self.api.GetRawInputData.argtypes = [W.HANDLE, W.UINT, C.c_void_p, C.POINTER(W.UINT), W.UINT]
        self.api.GetRawInputData.restype = W.UINT
        self.api.GetRawInputDeviceInfoW.argtypes = [W.HANDLE, W.UINT, C.c_void_p, C.POINTER(W.UINT)]
        self.api.GetRawInputDeviceInfoW.restype = W.UINT
        registration = Registration(1, 6, 0, window_id)
        if not self.api.RegisterRawInputDevices(C.byref(registration), 1, C.sizeof(registration)):
            raise C.WinError(C.get_last_error())

    def read(self, message):
        msg = W.MSG.from_address(int(message))
        if msg.message != 0xFF:
            return None
        size = W.UINT()
        if self.api.GetRawInputData(msg.lParam, 0x10000003, None, C.byref(size), C.sizeof(RawHeader)) == 0xFFFFFFFF:
            return None
        if size.value < C.sizeof(RawHeader) + C.sizeof(RawKey):
            return None
        buf = C.create_string_buffer(size.value)
        if self.api.GetRawInputData(msg.lParam, 0x10000003, buf, C.byref(size), C.sizeof(RawHeader)) == 0xFFFFFFFF:
            return None
        header = RawHeader.from_buffer(buf)
        if header.type != 1:
            return None
        length = W.UINT()
        if self.api.GetRawInputDeviceInfoW(header.device, 0x20000007, None, C.byref(length)) == 0xFFFFFFFF:
            return None
        name = C.create_unicode_buffer(length.value + 1)
        if self.api.GetRawInputDeviceInfoW(header.device, 0x20000007, name, C.byref(length)) == 0xFFFFFFFF:
            return None
        source = name.value.upper()
        if 'VID_413D&PID_553A' not in source:
            return None
        key = RawKey.from_buffer(buf, C.sizeof(RawHeader))
        return dict(vkey=key.vkey, scan=key.scan, released=bool(key.flags & 1), source=name.value)

    def close(self):
        registration = Registration(1, 6, 1, None)
        self.api.RegisterRawInputDevices(C.byref(registration), 1, C.sizeof(registration))

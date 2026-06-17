"""
WASAPI loopback capture via Windows native API (ctypes).
Pure Windows API — no external dependencies, works on any Windows 7+.
"""

import ctypes
from ctypes import (
    POINTER, Structure, byref, cast, c_void_p, c_uint32, c_uint16, c_uint8,
    c_int16, c_uint64, c_int32, c_int64, c_float, memmove, sizeof,
)
import numpy as np


# ── Win32 load ──────────────────────────────────────────────────────────────

_ole32 = ctypes.windll.ole32
_ole32.CoInitializeEx.argtypes = [c_void_p, c_uint32]
_ole32.CoInitializeEx.restype = ctypes.c_int32
_ole32.CoUninitialize.argtypes = []
_ole32.CoUninitialize.restype = None
_ole32.CoCreateInstance.argtypes = [c_void_p, c_void_p, c_uint32, c_void_p, POINTER(c_void_p)]
_ole32.CoCreateInstance.restype = ctypes.c_int32
_ole32.CoTaskMemFree.argtypes = [c_void_p]
_ole32.CoTaskMemFree.restype = None


# ── GUID / IID ──────────────────────────────────────────────────────────────

class GUID(Structure):
    _fields_ = [
        ('Data1', c_uint32),
        ('Data2', c_uint16),
        ('Data3', c_uint16),
        ('Data4', c_uint8 * 8),
    ]

    def __init__(self, s=None):
        super().__init__()
        if s:
            s = s.strip('{}')
            parts = s.split('-')
            self.Data1 = int(parts[0], 16)
            self.Data2 = int(parts[1], 16)
            self.Data3 = int(parts[2], 16)
            ba = bytearray()
            for p in parts[3:]:
                ba.extend(bytes.fromhex(p))
            self.Data4 = (c_uint8 * 8)(*ba)

    def ptr(self):
        return byref(self)


CLSID_MMDeviceEnumerator = GUID('{BCDE0395-E52F-467C-8E3D-C4579291692E}')
IID_IMMDeviceEnumerator = GUID('{A95664D2-9614-4F35-A746-DE8DB63617E6}')
IID_IMMDevice = GUID('{D666063F-1587-4E43-81F1-B948E807363F}')
IID_IAudioClient = GUID('{1CB9AD4C-DBFA-4C32-B178-C2F568A703B2}')
IID_IAudioCaptureClient = GUID('{C8ADBD64-E71E-48A0-A4DE-185C395CD317}')
KSDATAFORMAT_SUBTYPE_IEEE_FLOAT = GUID('{00000003-0000-0010-8000-00AA00389B71}')
KSDATAFORMAT_SUBTYPE_PCM = GUID('{00000001-0000-0010-8000-00AA00389B71}')


# ── WAVEFORMATEX ────────────────────────────────────────────────────────────

class WAVEFORMATEX(Structure):
    _pack_ = 1
    _fields_ = [
        ('wFormatTag', c_uint16),
        ('nChannels', c_uint16),
        ('nSamplesPerSec', c_uint32),
        ('nAvgBytesPerSec', c_uint32),
        ('nBlockAlign', c_uint16),
        ('wBitsPerSample', c_uint16),
        ('cbSize', c_uint16),
    ]


class WAVEFORMATEXTENSIBLE(Structure):
    _pack_ = 1
    _fields_ = [
        ('Format', WAVEFORMATEX),
        ('Samples', c_uint16),
        ('dwChannelMask', c_uint32),
        ('SubFormat', GUID),
    ]


WAVE_FORMAT_PCM = 1
WAVE_FORMAT_IEEE_FLOAT = 3
WAVE_FORMAT_EXTENSIBLE = 0xFFFE

AUDCLNT_SHAREMODE_SHARED = 0
AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
CLSCTX_ALL = 23

eRender = 0
eConsole = 0

COINIT_APARTMENTTHREADED = 2
COINIT_MULTITHREADED = 0

S_OK = 0


# ── COM helpers ─────────────────────────────────────────────────────────────

class ComPtr:
    def __init__(self, ptr):
        if isinstance(ptr, int):
            self.ptr = c_void_p(ptr)
        elif isinstance(ptr, c_void_p):
            self.ptr = ptr
        else:
            self.ptr = ptr
        vtbl_ptr = cast(self.ptr, POINTER(c_void_p))[0]
        vtbl_array = cast(vtbl_ptr, POINTER(c_void_p * 64))[0]
        self.vtbl = vtbl_array

    def release(self):
        fn = ctypes.WINFUNCTYPE(ctypes.c_int32, c_void_p)(self.vtbl[2])
        return fn(self.ptr)


# ── IMMDeviceEnumerator ─────────────────────────────────────────────────────

class DeviceEnumerator(ComPtr):
    def get_default_audio_endpoint(self, data_flow=eRender, role=eConsole):
        ppdev = c_void_p()
        fn = ctypes.WINFUNCTYPE(ctypes.c_int32, c_void_p, c_int32, c_int32, POINTER(c_void_p))(self.vtbl[4])
        hr = fn(self.ptr, data_flow, role, byref(ppdev))
        if hr < 0:
            raise RuntimeError(f"GetDefaultAudioEndpoint failed: HRESULT=0x{hr & 0xFFFFFFFF:08X}")
        return ppdev.value


class Device(ComPtr):
    def activate(self, iid, dw_cls_ctx=CLSCTX_ALL):
        ppv = c_void_p()
        fn = ctypes.WINFUNCTYPE(
            ctypes.c_int32, c_void_p,
            POINTER(GUID), c_uint32, c_void_p,
            POINTER(c_void_p),
        )(self.vtbl[3])
        hr = fn(self.ptr, byref(iid), dw_cls_ctx, None, byref(ppv))
        if hr < 0:
            raise RuntimeError(f"Activate failed: HRESULT=0x{hr & 0xFFFFFFFF:08X}")
        return ppv.value


# ── IAudioClient ────────────────────────────────────────────────────────────

class AudioClient(ComPtr):
    def get_mix_format(self):
        ppfmt = c_void_p()
        fn = ctypes.WINFUNCTYPE(ctypes.c_int32, c_void_p, POINTER(c_void_p))(self.vtbl[8])
        hr = fn(self.ptr, byref(ppfmt))
        if hr < 0:
            raise RuntimeError(f"GetMixFormat failed: HRESULT=0x{hr & 0xFFFFFFFF:08X}")
        wfex = cast(ppfmt, POINTER(WAVEFORMATEX)).contents
        if wfex.wFormatTag == WAVE_FORMAT_EXTENSIBLE:
            ext = cast(ppfmt, POINTER(WAVEFORMATEXTENSIBLE)).contents
            result = WAVEFORMATEXTENSIBLE()
            memmove(byref(result), byref(ext), sizeof(WAVEFORMATEXTENSIBLE))
            _ole32.CoTaskMemFree(ppfmt)
            return result
        else:
            result = WAVEFORMATEXTENSIBLE()
            memmove(byref(result.Format), byref(wfex), sizeof(WAVEFORMATEX))
            result.Samples = result.Format.wBitsPerSample
            result.SubFormat = KSDATAFORMAT_SUBTYPE_PCM if wfex.wFormatTag == WAVE_FORMAT_PCM else KSDATAFORMAT_SUBTYPE_IEEE_FLOAT
            _ole32.CoTaskMemFree(ppfmt)
            return result

    def initialize(self, share_mode, stream_flags, hns_buffer_duration=0, hns_periodicity=0, p_format=None):
        if p_format is not None:
            fmt_ptr = cast(byref(p_format), POINTER(WAVEFORMATEX))
        else:
            fmt_ptr = c_void_p()
        session_guid = c_void_p()
        fn = ctypes.WINFUNCTYPE(
            ctypes.c_int32, c_void_p,
            c_int32, c_uint32, c_int64, c_int64,
            POINTER(WAVEFORMATEX), c_void_p,
        )(self.vtbl[3])
        hr = fn(self.ptr, share_mode, stream_flags,
                c_int64(hns_buffer_duration), c_int64(hns_periodicity),
                fmt_ptr, session_guid)
        if hr < 0:
            raise RuntimeError(f"Initialize failed: HRESULT=0x{hr & 0xFFFFFFFF:08X}")

    def get_buffer_size(self):
        pbuf = c_uint32()
        fn = ctypes.WINFUNCTYPE(ctypes.c_int32, c_void_p, POINTER(c_uint32))(self.vtbl[4])
        hr = fn(self.ptr, byref(pbuf))
        if hr < 0:
            raise RuntimeError(f"GetBufferSize failed: HRESULT=0x{hr & 0xFFFFFFFF:08X}")
        return pbuf.value

    def start(self):
        fn = ctypes.WINFUNCTYPE(ctypes.c_int32, c_void_p)(self.vtbl[10])
        hr = fn(self.ptr)
        if hr < 0:
            raise RuntimeError(f"Start failed: HRESULT=0x{hr & 0xFFFFFFFF:08X}")

    def stop(self):
        fn = ctypes.WINFUNCTYPE(ctypes.c_int32, c_void_p)(self.vtbl[11])
        fn(self.ptr)

    def get_service(self, iid):
        ppv = c_void_p()
        fn = ctypes.WINFUNCTYPE(ctypes.c_int32, c_void_p, POINTER(GUID), POINTER(c_void_p))(self.vtbl[14])
        hr = fn(self.ptr, byref(iid), byref(ppv))
        if hr < 0:
            raise RuntimeError(f"GetService failed: HRESULT=0x{hr & 0xFFFFFFFF:08X}")
        return ppv.value


# ── IAudioCaptureClient ─────────────────────────────────────────────────────

class CaptureClient(ComPtr):
    def get_buffer(self):
        pp_data = c_void_p()
        p_frames = c_uint32()
        p_flags = c_uint32()
        p_devpos = c_uint64()
        p_qpcpos = c_uint64()
        fn = ctypes.WINFUNCTYPE(
            ctypes.c_int32, c_void_p,
            POINTER(c_void_p), POINTER(c_uint32),
            POINTER(c_uint32), POINTER(c_uint64), POINTER(c_uint64),
        )(self.vtbl[3])
        hr = fn(self.ptr, byref(pp_data), byref(p_frames), byref(p_flags), byref(p_devpos), byref(p_qpcpos))
        if hr == 0:
            return pp_data.value, p_frames.value, p_flags.value
        return None, 0, 0

    def release_buffer(self, frames):
        fn = ctypes.WINFUNCTYPE(ctypes.c_int32, c_void_p, c_uint32)(self.vtbl[4])
        fn(self.ptr, frames)

    def get_next_packet_size(self):
        p_frames = c_uint32()
        fn = ctypes.WINFUNCTYPE(ctypes.c_int32, c_void_p, POINTER(c_uint32))(self.vtbl[5])
        hr = fn(self.ptr, byref(p_frames))
        if hr < 0:
            return 0
        return p_frames.value


# ── converters ──────────────────────────────────────────────────────────────

def _wave_to_float(data_ptr, num_frames, num_channels, bits_per_sample, is_float):
    """Convert raw WASAPI buffer to float32 numpy array [num_frames, num_channels]."""
    if is_float:
        buf_type = POINTER(c_float * num_frames * num_channels)
        arr = np.frombuffer(cast(data_ptr, buf_type)[0], dtype=np.float32).reshape(-1, num_channels)
    elif bits_per_sample == 16:
        buf_type = POINTER(ctypes.c_int16 * num_frames * num_channels)
        arr = np.frombuffer(cast(data_ptr, buf_type)[0], dtype=np.int16).reshape(-1, num_channels)
        arr = arr.astype(np.float32) / 32768.0
    elif bits_per_sample == 24:
        # 24-bit packed in 3 bytes per sample
        raw = cast(data_ptr, POINTER(c_uint8 * (num_frames * num_channels * 3)))[0]
        raw_bytes = bytes(raw)
        arr_24 = np.frombuffer(raw_bytes, dtype=np.uint8).reshape(-1, 3)
        arr_int32 = np.zeros(len(arr_24), dtype=np.int32)
        arr_int32 |= arr_24[:, 0].astype(np.int32)
        arr_int32 |= arr_24[:, 1].astype(np.int32) << 8
        arr_int32 |= arr_24[:, 2].astype(np.int32) << 16
        arr_int32[arr_int32 >= 0x800000] -= 0x1000000
        arr = arr_int32.astype(np.float32) / 8388608.0
        arr = arr.reshape(-1, num_channels)
    else:
        raise RuntimeError(f"Unsupported bits_per_sample: {bits_per_sample}")
    return arr


# ── Main class ──────────────────────────────────────────────────────────────

class WasapiCapture:
    """WASAPI loopback capture — pure Windows API, no PortAudio needed.

    Usage:
        cap = WasapiCapture()
        cap.start(target_rate=16000)
        while cap.is_active:
            data = cap.read()  # returns float32 mono at target_rate or None
        cap.stop()
    """

    def __init__(self):
        self._coinit = False
        self._enumerator = None
        self._device = None
        self._audio_client = None
        self._capture_client = None
        self.is_active = False
        self.sample_rate = 0
        self.num_channels = 0

    def start(self, target_rate=16000):
        hr = _ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
        self._coinit = hr in (0, 1)  # S_OK or S_FALSE

        # Create device enumerator
        ppenum = c_void_p()
        hr = _ole32.CoCreateInstance(
            CLSID_MMDeviceEnumerator.ptr(), None, CLSCTX_ALL,
            IID_IMMDeviceEnumerator.ptr(), byref(ppenum),
        )
        if hr < 0:
            raise RuntimeError(f"CoCreateInstance MMDeviceEnumerator failed: 0x{hr & 0xFFFFFFFF:08X}")

        self._enumerator = DeviceEnumerator(ppenum.value)

        # Get default render device (speakers)
        dev_ptr = self._enumerator.get_default_audio_endpoint(eRender, eConsole)
        self._device = Device(dev_ptr)

        # Activate IAudioClient
        client_ptr = self._device.activate(IID_IAudioClient)
        self._audio_client = AudioClient(client_ptr)

        # Get format
        mix_fmt = self._audio_client.get_mix_format()
        self.sample_rate = mix_fmt.Format.nSamplesPerSec
        self.num_channels = mix_fmt.Format.nChannels
        bits = mix_fmt.Format.wBitsPerSample
        tag = mix_fmt.Format.wFormatTag
        if tag == WAVE_FORMAT_EXTENSIBLE:
            sub = mix_fmt.SubFormat
            is_float = (sub.Data1 == KSDATAFORMAT_SUBTYPE_IEEE_FLOAT.Data1 and
                        sub.Data2 == KSDATAFORMAT_SUBTYPE_IEEE_FLOAT.Data2 and
                        sub.Data3 == KSDATAFORMAT_SUBTYPE_IEEE_FLOAT.Data3 and
                        bytes(sub.Data4) == bytes(KSDATAFORMAT_SUBTYPE_IEEE_FLOAT.Data4))
        else:
            is_float = tag == WAVE_FORMAT_IEEE_FLOAT

        self._is_float = is_float
        self._bits_per_sample = bits
        self._target_rate = target_rate

        is_float_str = 'float' if is_float else 'int'
        print(f"[wasapi] Format: {self.sample_rate} Hz, {self.num_channels}ch, "
              f"{bits}bit {is_float_str}, "
              f"loopback -> {target_rate} Hz mono")

        # Initialize with loopback flag — pass actual mix format
        self._audio_client.initialize(
            AUDCLNT_SHAREMODE_SHARED,
            AUDCLNT_STREAMFLAGS_LOOPBACK,
            hns_buffer_duration=0,
            hns_periodicity=0,
            p_format=mix_fmt,
        )

        # Get capture client
        cap_ptr = self._audio_client.get_service(IID_IAudioCaptureClient)
        self._capture_client = CaptureClient(cap_ptr)

        self._buf_size = self._audio_client.get_buffer_size()

        # Start
        self._audio_client.start()
        self.is_active = True
        print(f"[wasapi] Loopback started, buffer={self._buf_size} frames")

    def read(self):
        if not self.is_active or self._capture_client is None:
            return None

        frames_list = []
        for _ in range(64):
            data_ptr, frames, flags = self._capture_client.get_buffer()
            if data_ptr is None:
                break

            try:
                arr = _wave_to_float(
                    data_ptr, frames,
                    self.num_channels, self._bits_per_sample, self._is_float,
                )
                if arr.shape[1] > 1:
                    arr = np.mean(arr, axis=1, keepdims=False)
                else:
                    arr = arr.flatten()
                frames_list.append(arr.copy())
            finally:
                self._capture_client.release_buffer(frames)

        if not frames_list:
            return None
        return np.concatenate(frames_list)

    def read_and_resample(self):
        arr = self.read()
        if arr is None:
            return None
        if self.sample_rate != self._target_rate:
            ratio = self._target_rate / self.sample_rate
            new_len = max(1, int(len(arr) * ratio))
            x_old = np.linspace(0, 1, len(arr))
            x_new = np.linspace(0, 1, new_len)
            arr = np.interp(x_new, x_old, arr).astype(np.float32)
        return arr

    def stop(self):
        self.is_active = False
        if self._audio_client:
            try:
                self._audio_client.stop()
            except Exception:
                pass
        if self._enumerator:
            try:
                self._enumerator.release()
            except Exception:
                pass
        # Note: in a full implementation we'd release all COM objects,
        # but the process is exiting anyway so it's not critical.
        if self._coinit:
            try:
                _ole32.CoUninitialize()
            except Exception:
                pass

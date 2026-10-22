from __future__ import annotations

import ctypes
from pathlib import Path


def read_game_file_version(executable: str | Path) -> int:
    """Read the same Windows FileVersionMS used by the CA launcher."""
    target = Path(executable)
    if not target.is_file():
        raise ValueError(f"Game executable is missing: {target}")
    version = ctypes.WinDLL("version", use_last_error=True)
    version.GetFileVersionInfoSizeW.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p]
    version.GetFileVersionInfoSizeW.restype = ctypes.c_uint32
    version.GetFileVersionInfoW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    version.GetFileVersionInfoW.restype = ctypes.c_int
    version.VerQueryValueW.argtypes = [
        ctypes.c_void_p, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_uint32),
    ]
    version.VerQueryValueW.restype = ctypes.c_int
    size = version.GetFileVersionInfoSizeW(str(target), None)
    if not size:
        raise ValueError(f"Game executable has no Windows version resource: {target}")
    buffer = ctypes.create_string_buffer(size)
    fixed, length = ctypes.c_void_p(), ctypes.c_uint32()
    if not version.GetFileVersionInfoW(str(target), 0, size, buffer) or not version.VerQueryValueW(
        buffer, "\\", ctypes.byref(fixed), ctypes.byref(length),
    ) or length.value < 52:
        raise ValueError(f"Cannot read game executable version: {target}")
    fields = ctypes.cast(fixed, ctypes.POINTER(ctypes.c_uint32))
    if fields[0] != 0xFEEF04BD or not fields[2]:
        raise ValueError(f"Invalid game executable version: {target}")
    return int(fields[2])

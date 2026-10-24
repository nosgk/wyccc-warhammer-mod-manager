"""Optional Windows DPAPI protection for local secrets.

The default settings keep the AI API key as plain text for compatibility. When
the user enables ``encrypt_ai_api_key``, the stored value is wrapped with the
current Windows user's DPAPI key. Other platforms keep the plain value.
"""

from __future__ import annotations

import base64
import ctypes
import os
from ctypes import wintypes

PREFIX = "dpapi:"


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def is_supported() -> bool:
    return os.name == "nt"


def is_protected(value: str) -> bool:
    return str(value or "").startswith(PREFIX)


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data, len(data))
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer


def _protect_bytes(data: bytes) -> bytes:
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    input_blob, _buffer = _blob(data)
    output_blob = _DataBlob()
    if not crypt32.CryptProtectData(
        ctypes.byref(input_blob), None, None, None, None, 0, ctypes.byref(output_blob)
    ):
        raise OSError("CryptProtectData failed")
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)


def _unprotect_bytes(data: bytes) -> bytes:
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    input_blob, _buffer = _blob(data)
    output_blob = _DataBlob()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(input_blob), None, None, None, None, 0, ctypes.byref(output_blob)
    ):
        raise OSError("CryptUnprotectData failed")
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)


def protect(value: str) -> str:
    """Protect a non-empty secret; returns the input unchanged off Windows."""
    text = str(value or "")
    if not text or is_protected(text) or not is_supported():
        return text
    encrypted = _protect_bytes(text.encode("utf-8"))
    return PREFIX + base64.b64encode(encrypted).decode("ascii")


def unprotect(value: str) -> str:
    """Unprotect a stored secret; plain values are returned unchanged."""
    text = str(value or "")
    if not is_protected(text):
        return text
    if not is_supported():
        raise OSError("DPAPI is only available on Windows")
    encrypted = base64.b64decode(text[len(PREFIX) :])
    return _unprotect_bytes(encrypted).decode("utf-8")

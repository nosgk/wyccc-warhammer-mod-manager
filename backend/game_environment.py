"""Prepare WH3's child environment without moving the user's Windows data."""
from __future__ import annotations

import ctypes
import hashlib
import json
import logging
import os
import stat
import struct
import sys
import uuid
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .constants import WH3_EXECUTABLE

_LOG = logging.getLogger(__name__)
_PROFILE_KEYS = ("USERPROFILE", "APPDATA", "LOCALAPPDATA")
_OWNER = "wyccc-mod-manager/profile-aliases-v1"
_MARKER = ".wyccc-profile.json"
_MOUNT_POINT_TAG = 0xA0000003
_REPARSE_ATTRIBUTE = 0x400
_FOLDERS = {
    "USERPROFILE": "5e6c858f-0e22-4760-9afe-ea3317b67173",
    "APPDATA": "3eb685db-65f9-4cf6-a03a-e3ef65729f3d",
    "LOCALAPPDATA": "f1b32785-6fba-4fcf-9d55-7b8e7f157091",
    "ProgramData": "62ab5d82-fdc1-4dc3-a9dd-070d1d495d97",
    "PublicDocuments": "ed4824af-dce4-45a8-81e2-fc7965083634",
}


@dataclass(frozen=True)
class PreparedGameEnvironment:
    environment: dict[str, str]
    compatibility: dict[str, Any]


class _OccupiedPath(OSError):
    """An existing object cannot be changed by this feature."""


class _WindowsPaths:
    """Native calls use instance-local signatures, including concurrent launches."""

    def __init__(self) -> None:
        from ctypes import wintypes

        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        self.shell = ctypes.WinDLL("shell32", use_last_error=True)
        self.ole = ctypes.WinDLL("ole32", use_last_error=True)
        signatures = (
            (self.kernel, "GetCurrentProcess", (), wintypes.HANDLE),
            (self.kernel, "CloseHandle", (wintypes.HANDLE,), wintypes.BOOL),
            (self.kernel, "LocalFree", (ctypes.c_void_p,), ctypes.c_void_p),
            (self.kernel, "CreateFileW", (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE), wintypes.HANDLE),
            (self.kernel, "DeviceIoControl", (wintypes.HANDLE, wintypes.DWORD,
                ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p), wintypes.BOOL),
            (self.kernel, "CreateDirectoryW", (wintypes.LPCWSTR, ctypes.c_void_p), wintypes.BOOL),
            (self.kernel, "GetFileAttributesW", (wintypes.LPCWSTR,), wintypes.DWORD),
            (self.kernel, "SetFileAttributesW", (wintypes.LPCWSTR, wintypes.DWORD), wintypes.BOOL),
            (self.kernel, "CreateMutexW", (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR), wintypes.HANDLE),
            (self.kernel, "WaitForSingleObject", (wintypes.HANDLE, wintypes.DWORD), wintypes.DWORD),
            (self.kernel, "ReleaseMutex", (wintypes.HANDLE,), wintypes.BOOL),
            (self.advapi, "OpenProcessToken", (wintypes.HANDLE, wintypes.DWORD,
                ctypes.POINTER(wintypes.HANDLE)), wintypes.BOOL),
            (self.advapi, "GetTokenInformation", (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)), wintypes.BOOL),
            (self.advapi, "ConvertSidToStringSidW", (ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_void_p)), wintypes.BOOL),
            (self.advapi, "ConvertStringSecurityDescriptorToSecurityDescriptorW", (
                wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p),
                wintypes.BOOL),
            (self.shell, "SHGetKnownFolderPath", (ctypes.c_void_p, wintypes.DWORD, wintypes.HANDLE,
                ctypes.POINTER(ctypes.c_void_p)), ctypes.c_long),
            (self.ole, "CoTaskMemFree", (ctypes.c_void_p,), None),
        )
        for library, name, arguments, result in signatures:
            function = getattr(library, name)
            function.argtypes = arguments
            function.restype = result

    def known_folder(self, name: str) -> Path:
        guid = (ctypes.c_byte * 16).from_buffer_copy(uuid.UUID(_FOLDERS[name]).bytes_le)
        output = ctypes.c_void_p()
        try:
            # DONT_VERIFY does not create missing Known Folders.
            result = self.shell.SHGetKnownFolderPath(ctypes.byref(guid), 0x4000, None, ctypes.byref(output))
            if result < 0 or not output.value:
                raise OSError(f"SHGetKnownFolderPath({name}) failed: {result:#x}")
            return Path(ctypes.wstring_at(output))
        finally:
            self.ole.CoTaskMemFree(output)

    def user_sid(self) -> str:
        from ctypes import wintypes

        token = wintypes.HANDLE()
        if not self.advapi.OpenProcessToken(self.kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
            raise ctypes.WinError(ctypes.get_last_error())
        output = ctypes.c_void_p()
        try:
            size = wintypes.DWORD()
            self.advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
            if not size.value:
                raise ctypes.WinError(ctypes.get_last_error())
            buffer = ctypes.create_string_buffer(size.value)
            if not self.advapi.GetTokenInformation(token, 1, buffer, size, ctypes.byref(size)):
                raise ctypes.WinError(ctypes.get_last_error())
            sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
            if not self.advapi.ConvertSidToStringSidW(sid, ctypes.byref(output)):
                raise ctypes.WinError(ctypes.get_last_error())
            return ctypes.wstring_at(output)
        finally:
            self.kernel.LocalFree(output)
            self.kernel.CloseHandle(token)

    def private_directory(self, path: Path, sid: str) -> None:
        from ctypes import wintypes

        class SecurityAttributes(ctypes.Structure):
            _fields_ = [("length", wintypes.DWORD), ("descriptor", ctypes.c_void_p),
                        ("inherit", wintypes.BOOL)]

        descriptor = ctypes.c_void_p()
        sddl = f"D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;{sid})"
        if not self.advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, 1, ctypes.byref(descriptor), None,
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            attributes = SecurityAttributes(ctypes.sizeof(SecurityAttributes), descriptor, False)
            if not self.kernel.CreateDirectoryW(str(path), ctypes.byref(attributes)):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            self.kernel.LocalFree(descriptor)

    def hide_new_directory(self, path: Path) -> None:
        attributes = self.kernel.GetFileAttributesW(str(path))
        if attributes == 0xFFFFFFFF or not self.kernel.SetFileAttributesW(str(path), attributes | 2):
            raise ctypes.WinError(ctypes.get_last_error())

    @contextmanager
    def lock(self, identity: str):
        handle = self.kernel.CreateMutexW(None, False, f"Global\\WycccMM.Profile.{identity}")
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        acquired = False
        try:
            result = self.kernel.WaitForSingleObject(handle, 10_000)
            if result not in (0, 0x80):  # Also repair after an abandoned creator.
                raise OSError(f"Profile mutex wait failed: {result:#x}")
            acquired = True
            yield
        finally:
            if acquired:
                self.kernel.ReleaseMutex(handle)
            self.kernel.CloseHandle(handle)

    def create_junction(self, path: Path, target: Path) -> None:
        from ctypes import wintypes

        # Mount-point reparse buffers accept local targets, not UNC paths.
        substitute = ("\\??\\" + str(target)).encode("utf-16-le")
        printed = str(target).encode("utf-16-le")
        names = substitute + b"\0\0" + printed + b"\0\0"
        body = struct.pack("<HHHH", 0, len(substitute), len(substitute) + 2, len(printed)) + names
        data = struct.pack("<IHH", _MOUNT_POINT_TAG, len(body), 0) + body
        if len(data) > 16_384:
            raise OSError("Junction target is too long")
        path.mkdir()  # Never open or replace an existing object for writing.
        handle = None
        try:
            handle = self.kernel.CreateFileW(str(path), 0x40000000, 7, None, 3,
                                            0x02000000 | 0x00200000, None)
            if handle in (None, ctypes.c_void_p(-1).value):
                raise ctypes.WinError(ctypes.get_last_error())
            buffer = ctypes.create_string_buffer(data)
            returned = wintypes.DWORD()
            if not self.kernel.DeviceIoControl(handle, 0x000900A4, buffer, len(data),
                                              None, 0, ctypes.byref(returned), None):
                raise ctypes.WinError(ctypes.get_last_error())
        except OSError:
            if handle not in (None, ctypes.c_void_p(-1).value):
                self.kernel.CloseHandle(handle)
                handle = None
            # rmdir removes only this new directory/link, never its target.
            os.rmdir(path)
            raise
        finally:
            if handle not in (None, ctypes.c_void_p(-1).value):
                self.kernel.CloseHandle(handle)


def _local_path(path: Path) -> bool:
    drive = path.drive.removeprefix("\\\\?\\")
    return len(drive) == 2 and drive[1] == ":" and path.is_absolute()


def _physical_path(path: Path) -> Path:
    value = str(path.resolve(strict=True))
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return Path(value)


def _candidate_roots(native: _WindowsPaths, game_path: Path) -> list[Path]:
    manager = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent.parent
    candidates = []
    for location, name in (("ProgramData", "WycccMM"), (manager, ".wyccc-user-paths"),
                           (game_path, ".wyccc-user-paths"), ("PublicDocuments", "WycccMM")):
        try:
            parent = native.known_folder(location) if isinstance(location, str) else location
            # Resolve the parent to avoid persistent links on a transient SUBST drive.
            root = _physical_path(parent) / name
            if str(root).isascii() and _local_path(root) and root not in candidates:
                candidates.append(root)
        except (OSError, RuntimeError):
            _LOG.warning("Unavailable profile alias location: %s", location, exc_info=True)
    return candidates


def _ordinary_directory(path: Path) -> bool:
    entry = path.lstat()
    return stat.S_ISDIR(entry.st_mode) and not getattr(entry, "st_file_attributes", 0) & _REPARSE_ATTRIBUTE


def _owned_directory(path: Path, marker: dict, native: _WindowsPaths, *, create: bool, sid: str = "") -> None:
    try:
        path.lstat()
    except FileNotFoundError:
        if not create:
            raise
        try:
            if sid:
                native.private_directory(path, sid)
            else:
                path.mkdir()
        except FileExistsError:
            # A different identity may have created the shared root concurrently.
            return _owned_directory(path, marker, native, create=False)
        with (path / _MARKER).open("x", encoding="utf-8") as stream:
            json.dump(marker, stream, ensure_ascii=True, sort_keys=True)
        native.hide_new_directory(path)
    if not _ordinary_directory(path):
        raise _OccupiedPath(f"Alias container is not an ordinary directory: {path}")
    marker_path = path / _MARKER
    try:
        entry = marker_path.lstat()
        if (not stat.S_ISREG(entry.st_mode) or entry.st_size > 4096
                or getattr(entry, "st_file_attributes", 0) & _REPARSE_ATTRIBUTE):
            raise _OccupiedPath(f"Invalid alias ownership marker: {marker_path}")
        if json.loads(marker_path.read_text(encoding="utf-8")) != marker:
            raise _OccupiedPath(f"Unowned alias container: {path}")
    except (FileNotFoundError, ValueError) as error:
        raise _OccupiedPath(f"Unowned alias container: {path}") from error


def _correct_junction(path: Path, target: Path) -> bool:
    entry = path.lstat()
    return (getattr(entry, "st_reparse_tag", 0) == _MOUNT_POINT_TAG
            and os.path.samefile(path, target))


def _mapping(container: Path, originals: dict[str, str], targets: dict[str, Path]) -> tuple[dict, dict]:
    profile = targets["USERPROFILE"]
    links = {"P": profile}
    effective = {"USERPROFILE": str(container / "P")}
    for key, leaf, link in (("APPDATA", "Roaming", "R"), ("LOCALAPPDATA", "Local", "L")):
        standard = profile / "AppData" / leaf
        try:
            standard_layout = os.path.samefile(standard, targets[key])
        except OSError:
            standard_layout = False
        if standard_layout:
            effective[key] = str(container / "P" / "AppData" / leaf)
        elif originals[key].isascii():
            effective[key] = originals[key]
        elif str(targets[key]).isascii():
            effective[key] = str(targets[key])
        else:
            links[link] = targets[key]
            effective[key] = str(container / link)
    return links, effective


def _prepare_aliases(native: _WindowsPaths, roots: list[Path], sid: str, identity: str,
                     originals: dict[str, str], targets: dict[str, Path]) -> tuple[Path, dict]:
    root_marker = {"owner": _OWNER}
    marker = {"owner": _OWNER, "identity": identity, "sid": sid}
    # Prefer an existing valid alias even if an earlier candidate becomes writable.
    for create in (False, True):
        for root in roots:
            try:
                _owned_directory(root, root_marker, native, create=create)
                for slot in range(16):
                    suffix = f"-{slot}" if slot else ""
                    container = root / (identity[:20] + suffix)
                    try:
                        _owned_directory(container, marker, native, create=create, sid=sid)
                        links, effective = _mapping(container, originals, targets)
                        # Preflight every existing link before creating any missing link.
                        missing = []
                        for name, target in links.items():
                            path = container / name
                            try:
                                correct = _correct_junction(path, target)
                            except FileNotFoundError:
                                if os.path.lexists(path):
                                    raise _OccupiedPath(f"Broken or wrong alias: {path}") from None
                                missing.append((path, target))
                            else:
                                if not correct:
                                    raise _OccupiedPath(f"Wrong alias type or target: {path}")
                        if missing and not create:
                            continue
                        for path, target in missing:
                            native.create_junction(path, target)
                        for name, target in links.items():
                            if not _correct_junction(container / name, target):
                                raise OSError(f"Alias verification failed: {container / name}")
                        for key, value in effective.items():
                            if not value.isascii() or not os.path.samefile(value, targets[key]):
                                raise OSError(f"Effective profile path verification failed: {key}={value}")
                        return container, effective
                    except _OccupiedPath:
                        if create:
                            _LOG.warning("Skipping occupied profile alias container: %s", container, exc_info=True)
                        continue
                    except FileNotFoundError:
                        continue
            except OSError:
                if create:
                    _LOG.warning("Profile alias candidate unavailable: %s", root, exc_info=True)
                else:
                    _LOG.debug("Profile alias candidate unavailable: %s", root, exc_info=True)
    raise OSError("No persistent ASCII profile alias location is available")


def prepare_game_environment(base_environment: Mapping[str, str], game_path: Path,
                             executable_name: str = WH3_EXECUTABLE) -> PreparedGameEnvironment:
    """Only Windows WH3 gets verified ASCII aliases, scoped to its child process."""
    environment = dict(base_environment)
    if os.name != "nt" or str(executable_name).casefold() != WH3_EXECUTABLE.casefold():
        return PreparedGameEnvironment(environment, {"enabled": False})
    by_name = {key.upper(): value for key, value in environment.items()}
    if all(by_name.get(key) and by_name[key].isascii() for key in _PROFILE_KEYS):
        return PreparedGameEnvironment(environment, {"enabled": False})
    try:
        native = _WindowsPaths()
    except OSError as error:
        _LOG.exception("Unable to initialize Windows profile path APIs")
        raise ValueError("launchProfile.prepareFailed") from error
    try:
        originals = {key: by_name.get(key) or str(native.known_folder(key)) for key in _PROFILE_KEYS}
        if all(value.isascii() for value in originals.values()):
            return PreparedGameEnvironment(environment, {"enabled": False})
        targets = {}
        for key, value in originals.items():
            source = Path(value)
            if not source.is_absolute():
                raise ValueError("launchProfile.invalidPaths")
            if not value.isascii() and not _local_path(source):
                raise ValueError("launchProfile.networkPath")
            target = _physical_path(source)
            if not target.is_dir():
                raise ValueError("launchProfile.invalidPaths")
            targets[key] = target
        # USERPROFILE always uses P. Other remote folders may retain an existing ASCII path.
        for key, target in targets.items():
            if (key == "USERPROFILE" or (not originals[key].isascii() and not str(target).isascii())) \
                    and not _local_path(target):
                raise ValueError("launchProfile.networkPath")
    except (OSError, RuntimeError) as error:
        _LOG.exception("Unable to resolve game profile directories")
        raise ValueError("launchProfile.invalidPaths") from error
    roots: list[Path] = []
    try:
        sid = native.user_sid()
        fingerprint = [sid, *(os.path.normcase(str(targets[key])) for key in _PROFILE_KEYS)]
        identity = hashlib.sha256(json.dumps(fingerprint, ensure_ascii=True).encode("ascii")).hexdigest()
        roots = _candidate_roots(native, Path(game_path))
        with native.lock(identity):
            container, effective = _prepare_aliases(native, roots, sid, identity, originals, targets)
        # Remove case variants from caller mappings before inserting Windows keys.
        environment = {key: value for key, value in environment.items() if key.upper() not in _PROFILE_KEYS}
        environment.update(effective)
        metadata = {"enabled": True, "alias_directory": str(container), "original_paths": originals,
                    "targets": {key: str(value) for key, value in targets.items()}, "effective_paths": effective}
        _LOG.info("Game profile compatibility: %s", json.dumps(metadata, ensure_ascii=True))
        return PreparedGameEnvironment(environment, metadata)
    except (OSError, RuntimeError) as error:
        _LOG.exception("Unable to prepare game profile aliases; targets=%s; candidates=%s", targets, roots)
        raise ValueError("launchProfile.prepareFailed") from error

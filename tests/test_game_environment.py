from __future__ import annotations

import ast
import ctypes
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from backend import game_environment as profiles
from backend.launcher import forget_launched_game, launch_game


def test_ascii_environment_is_copied_without_discovery_or_files():
    original = {"USERPROFILE": r"C:\Users\player", "APPDATA": r"C:\Users\player\AppData\Roaming",
                "LOCALAPPDATA": r"C:\Users\player\AppData\Local", "PATH": "unchanged"}
    with patch.object(profiles, "_WindowsPaths") as native:
        prepared = profiles.prepare_game_environment(original, Path("game"))
    native.assert_not_called()
    assert prepared.environment == original
    assert prepared.environment is not original
    assert prepared.compatibility == {"enabled": False}


def test_other_games_leave_unicode_profile_alone():
    original = {"USERPROFILE": r"C:\Users\中文用户"}
    with patch.object(profiles, "_WindowsPaths") as native:
        prepared = profiles.prepare_game_environment(original, Path("game"), "Three_Kingdoms.exe")
    native.assert_not_called()
    assert prepared.environment == original
    assert prepared.compatibility == {"enabled": False}


def test_non_windows_environment_is_unchanged():
    original = {"USERPROFILE": "/home/中文用户"}
    with patch.object(profiles.os, "name", "posix"), patch.object(profiles, "_WindowsPaths") as native:
        # Construct before mocking os.name: Windows cannot instantiate PosixPath.
        prepared = profiles.prepare_game_environment(original, "game")
    native.assert_not_called()
    assert prepared.environment == original


def test_module_syntax_supports_python_311():
    ast.parse(Path(profiles.__file__).read_text(encoding="utf-8"), feature_version=(3, 11))


def _remove_fixture_junctions(root: Path) -> None:
    # Do not traverse a Junction while cleaning up a fixture.
    for entry in root.iterdir():
        info = entry.lstat()
        if getattr(info, "st_reparse_tag", 0) == profiles._MOUNT_POINT_TAG:
            os.rmdir(entry)
        elif entry.is_dir():
            _remove_fixture_junctions(entry)


@pytest.fixture
def profile_tree(tmp_path, monkeypatch):
    if os.name != "nt":
        pytest.skip("Real Windows Junctions required")
    profile = tmp_path / "中文用户名"
    roaming = profile / "AppData" / "Roaming"
    local = profile / "AppData" / "Local"
    roaming.mkdir(parents=True)
    local.mkdir()
    roots = [tmp_path / "aliases", tmp_path / "fallback"]
    monkeypatch.setattr(profiles, "_candidate_roots", lambda native, game: roots)
    environment = os.environ.copy()
    environment.update(USERPROFILE=str(profile), APPDATA=str(roaming), LOCALAPPDATA=str(local))
    try:
        yield tmp_path, profile, environment, roots
    finally:
        _remove_fixture_junctions(tmp_path)


def _prepare(tree):
    root, _, environment, _ = tree
    return profiles.prepare_game_environment(environment, root)


def test_real_junctions_share_original_data_and_child_environment(profile_tree):
    root, profile, original, _ = profile_tree
    before = dict(original)
    marker = profile / "AppData" / "Roaming" / "settings.txt"
    marker.write_text("original", encoding="utf-8")
    original_attributes = {key: Path(original[key]).stat().st_file_attributes for key in profiles._PROFILE_KEYS}
    prepared = _prepare(profile_tree)
    assert prepared.compatibility["enabled"]
    effective = prepared.environment
    alias = Path(effective["USERPROFILE"])
    assert alias.lstat().st_reparse_tag == profiles._MOUNT_POINT_TAG
    assert (alias.parent.lstat().st_file_attributes & 2) == 2
    assert os.path.samefile(alias, profile)
    for key in profiles._PROFILE_KEYS:
        assert effective[key].isascii()
        assert os.path.samefile(effective[key], original[key])
    assert Path(effective["APPDATA"]) == alias / "AppData" / "Roaming"
    assert (Path(effective["APPDATA"]) / "settings.txt").read_text(encoding="utf-8") == "original"
    (Path(effective["LOCALAPPDATA"]) / "new.txt").write_text("same directory", encoding="utf-8")
    assert (profile / "AppData" / "Local" / "new.txt").read_text(encoding="utf-8") == "same directory"
    assert original == before
    assert {key: Path(original[key]).stat().st_file_attributes for key in profiles._PROFILE_KEYS} == original_attributes
    for key, value in original.items():
        if key not in profiles._PROFILE_KEYS:
            assert effective[key] == value
    child = subprocess.run(
        [sys.executable, "-c", "import json,os; print(json.dumps({k:os.environ[k] for k in "
         "('USERPROFILE','APPDATA','LOCALAPPDATA')}))"],
        cwd=root, env=effective, capture_output=True, text=True, check=True,
    )
    assert json.loads(child.stdout) == {key: effective[key] for key in profiles._PROFILE_KEYS}


def test_reuse_across_instances_and_repair_missing_owned_link(profile_tree):
    first = _prepare(profile_tree)
    container = Path(first.compatibility["alias_directory"])
    before = (container / profiles._MARKER).stat().st_mtime_ns
    with patch.object(profiles._WindowsPaths, "create_junction", side_effect=AssertionError("unneeded")):
        assert _prepare(profile_tree).environment == first.environment
    assert (container / profiles._MARKER).stat().st_mtime_ns == before
    os.rmdir(container / "P")
    repaired = _prepare(profile_tree)
    assert repaired.environment == first.environment
    assert os.path.samefile(container / "P", profile_tree[1])


def test_redirected_appdata_uses_sibling_links_and_keeps_ascii_external_folder(profile_tree):
    root, profile, environment, _ = profile_tree
    redirected = root / "重定向漫游"
    redirected.mkdir()
    ascii_local = root / "external-local"
    ascii_local.mkdir()
    environment.update(APPDATA=str(redirected), LOCALAPPDATA=str(ascii_local))
    prepared = _prepare(profile_tree)
    alias = Path(prepared.environment["USERPROFILE"])
    roaming = Path(prepared.environment["APPDATA"])
    assert roaming == alias.parent / "R"
    assert roaming.lstat().st_reparse_tag == profiles._MOUNT_POINT_TAG
    assert os.path.samefile(roaming, redirected)
    assert prepared.environment["LOCALAPPDATA"] == str(ascii_local)
    assert not (alias.parent / "L").exists()
    assert sorted(path.name for path in profile.iterdir()) == ["AppData"]
    assert sorted(path.name for path in (profile / "AppData").iterdir()) == ["Local", "Roaming"]


def test_both_redirected_appdata_folders_and_ascii_profile_are_supported(profile_tree):
    root, _, environment, _ = profile_tree
    profile = root / "ascii-user"
    profile.mkdir()
    roaming = root / "漫游配置"
    local = root / "本地配置"
    roaming.mkdir()
    local.mkdir()
    environment.update(USERPROFILE=str(profile), APPDATA=str(roaming), LOCALAPPDATA=str(local))
    prepared = _prepare(profile_tree)
    container = Path(prepared.compatibility["alias_directory"])
    assert os.path.samefile(container / "P", profile)
    assert os.path.samefile(container / "R", roaming)
    assert os.path.samefile(container / "L", local)
    assert all(prepared.environment[key].isascii() for key in profiles._PROFILE_KEYS)


@pytest.mark.parametrize("occupied_kind", ["directory", "wrong-junction", "broken-junction"])
def test_wrong_alias_objects_are_preserved_and_another_container_is_used(profile_tree, occupied_kind):
    root, _, _, _ = profile_tree
    first = _prepare(profile_tree)
    original_container = Path(first.compatibility["alias_directory"])
    alias = original_container / "P"
    os.rmdir(alias)
    other = root / "unrelated"
    other.mkdir()
    sentinel = other / "keep.txt"
    sentinel.write_text("keep")
    if occupied_kind == "directory":
        alias.mkdir()
        (alias / "keep.txt").write_text("keep")
    else:
        profiles._WindowsPaths().create_junction(alias, other)
        if occupied_kind == "broken-junction":
            sentinel.unlink()
            other.rmdir()
    prepared = _prepare(profile_tree)
    assert Path(prepared.compatibility["alias_directory"]).name == original_container.name + "-1"
    assert os.path.lexists(alias)
    if occupied_kind == "directory":
        assert (alias / "keep.txt").read_text() == "keep"
        assert not getattr(alias.lstat(), "st_reparse_tag", 0)
    elif occupied_kind == "wrong-junction":
        assert os.path.samefile(alias, other)
        assert sentinel.read_text() == "keep"


def test_unknown_root_is_untouched_and_next_candidate_is_used(profile_tree):
    _, _, _, roots = profile_tree
    roots[0].mkdir()
    marker = roots[0] / "keep.txt"
    marker.write_text("user data")
    prepared = _prepare(profile_tree)
    assert Path(prepared.compatibility["alias_directory"]).parent == roots[1]
    assert sorted(path.name for path in roots[0].iterdir()) == ["keep.txt"]
    assert marker.read_text() == "user data"


def test_root_junction_is_not_followed_or_claimed(profile_tree):
    root, _, _, roots = profile_tree
    unrelated = root / "unrelated-root"
    unrelated.mkdir()
    profiles._WindowsPaths().create_junction(roots[0], unrelated)
    prepared = _prepare(profile_tree)
    assert Path(prepared.compatibility["alias_directory"]).parent == roots[1]
    assert os.path.samefile(roots[0], unrelated)
    assert not list(unrelated.iterdir())


def test_permission_failure_falls_back_and_existing_alias_wins_later(profile_tree, monkeypatch):
    _, _, _, roots = profile_tree
    native_create = profiles._WindowsPaths.private_directory

    def deny_first(native, path, sid):
        if path.parent == roots[0]:
            raise PermissionError("first candidate denied")
        return native_create(native, path, sid)

    with monkeypatch.context() as context:
        context.setattr(profiles._WindowsPaths, "private_directory", deny_first)
        first = _prepare(profile_tree)
    assert Path(first.compatibility["alias_directory"]).parent == roots[1]
    # The default is now writable, but stable existing paths take precedence.
    with patch.object(profiles._WindowsPaths, "create_junction", side_effect=AssertionError("unneeded")):
        assert _prepare(profile_tree).environment == first.environment


def test_all_candidates_failing_produces_localized_failure_without_spawning(profile_tree):
    root, _, environment, _ = profile_tree
    (root / "Warhammer3.exe").touch()
    order = root / "used_mods.txt"
    order.touch()
    with (patch.dict(os.environ, environment, clear=True),
          patch("backend.launcher.is_game_running", return_value=False),
          patch("backend.launcher.subprocess.Popen") as popen,
          patch.object(profiles._WindowsPaths, "private_directory", side_effect=PermissionError("denied"))):
        with pytest.raises(ValueError, match="^launchProfile.prepareFailed$"):
            launch_game(str(root), str(order))
    popen.assert_not_called()


def test_source_changes_select_a_new_container_without_retargeting_old_links(profile_tree):
    root, _, environment, _ = profile_tree
    first = _prepare(profile_tree)
    old_local = Path(first.environment["LOCALAPPDATA"])
    redirected = root / "新本地目录"
    redirected.mkdir()
    previous_target = environment["LOCALAPPDATA"]
    environment["LOCALAPPDATA"] = str(redirected)
    second = _prepare(profile_tree)
    assert second.compatibility["alias_directory"] != first.compatibility["alias_directory"]
    assert os.path.samefile(old_local, previous_target)
    assert os.path.samefile(second.environment["LOCALAPPDATA"], redirected)


def test_missing_environment_uses_known_folders_without_overriding_custom_path(profile_tree):
    root, profile, environment, _ = profile_tree
    custom_roaming = root / "自定义漫游"
    custom_roaming.mkdir()
    del environment["USERPROFILE"]
    del environment["LOCALAPPDATA"]
    environment["APPDATA"] = str(custom_roaming)
    known = {"USERPROFILE": profile, "LOCALAPPDATA": profile / "AppData" / "Local"}
    with patch.object(profiles._WindowsPaths, "known_folder", side_effect=lambda key: known[key]) as folders:
        prepared = _prepare(profile_tree)
    assert sorted(call.args[0] for call in folders.call_args_list) == ["LOCALAPPDATA", "USERPROFILE"]
    assert "USERPROFILE" not in environment
    assert os.path.samefile(prepared.environment["APPDATA"], custom_roaming)


def test_existing_ascii_external_compatibility_environment_is_reused(profile_tree):
    first = _prepare(profile_tree)
    with patch.object(profiles, "_WindowsPaths") as native:
        external = profiles.prepare_game_environment(first.environment, profile_tree[0])
    native.assert_not_called()
    assert external.environment == first.environment
    assert external.compatibility == {"enabled": False}


def test_extended_local_path_is_not_misclassified_as_a_network_folder(profile_tree):
    _, profile, environment, _ = profile_tree
    environment["USERPROFILE"] = "\\\\?\\" + str(profile)
    prepared = _prepare(profile_tree)
    assert prepared.compatibility["targets"]["USERPROFILE"] == str(profile)
    assert os.path.samefile(prepared.environment["USERPROFILE"], profile)


def test_invalid_unicode_source_fails_before_alias_creation(profile_tree):
    _, _, environment, roots = profile_tree
    environment["APPDATA"] += "\\不存在"
    with pytest.raises(ValueError, match="^launchProfile.invalidPaths$"):
        _prepare(profile_tree)
    assert not any(root.exists() for root in roots)


def test_unicode_network_source_fails_without_connecting(profile_tree):
    _, _, environment, roots = profile_tree
    environment["APPDATA"] = r"\\unreachable-server\profiles\中文用户名"
    with patch.object(Path, "resolve", side_effect=AssertionError("network access")):
        # Validate the UNC source first; preceding local paths may be resolved.
        network_env = dict(environment, USERPROFILE=environment["APPDATA"])
        with pytest.raises(ValueError, match="^launchProfile.networkPath$"):
            profiles.prepare_game_environment(network_env, profile_tree[0])
    assert not any(root.exists() for root in roots)


def test_sid_discovery_failure_is_reported_without_an_unbound_variable(profile_tree):
    with patch.object(profiles._WindowsPaths, "user_sid", side_effect=OSError("token unavailable")):
        with pytest.raises(ValueError, match="^launchProfile.prepareFailed$"):
            _prepare(profile_tree)


def test_native_api_initialization_failure_is_localized(profile_tree):
    with patch.object(profiles, "_WindowsPaths", side_effect=OSError("library unavailable")):
        with pytest.raises(ValueError, match="^launchProfile.prepareFailed$"):
            _prepare(profile_tree)


def test_threads_create_one_verified_container(profile_tree):
    with ThreadPoolExecutor(max_workers=4) as workers:
        results = list(workers.map(lambda _: _prepare(profile_tree), range(8)))
    assert len({result.compatibility["alias_directory"] for result in results}) == 1
    assert len(list(profile_tree[3][0].iterdir())) == 2  # root marker + one private container


def test_concurrent_processes_reuse_persistent_junctions(profile_tree):
    root, _, environment, roots = profile_tree
    worker = "\n".join([
        "import json,sys", "from pathlib import Path", "from backend import game_environment as p",
        "data=json.load(sys.stdin)", "p._candidate_roots=lambda native,game:[Path(data['root'])]",
        "result=p.prepare_game_environment(data['env'],Path(data['game']))",
        "print(json.dumps(result.compatibility))",
    ])
    payload = json.dumps({"env": environment, "root": str(roots[0]), "game": str(root)})
    children = [subprocess.Popen([sys.executable, "-c", worker], cwd=Path(profiles.__file__).parent.parent,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True) for _ in range(3)]
    results = []
    try:
        with ThreadPoolExecutor(max_workers=3) as workers:
            outputs = list(workers.map(lambda child: child.communicate(payload, timeout=20), children))
        for child, (stdout, stderr) in zip(children, outputs):
            assert child.returncode == 0, stderr
            results.append(json.loads(stdout))
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait()
    assert len({result["alias_directory"] for result in results}) == 1
    assert len(list(roots[0].iterdir())) == 2
    assert os.path.samefile(results[0]["effective_paths"]["USERPROFILE"], environment["USERPROFILE"])


def test_launcher_preserves_save_modlist_steam_context_and_parent_environment(profile_tree):
    root, _, environment, _ = profile_tree
    game = root / "game"
    game.mkdir()
    (game / "Warhammer3.exe").touch()
    order = game / "used_mods.txt"
    order.touch()
    popen = Mock(return_value=Mock(pid=99001, poll=Mock(return_value=None)))
    with (patch.dict(os.environ, environment, clear=True),
          patch("backend.launcher.is_game_running", return_value=False),
          patch("backend.launcher.subprocess.Popen", popen)):
        before = dict(os.environ)
        try:
            result = launch_game(str(game), str(order), "my campaign.save", app_id="1142710")
        finally:
            forget_launched_game(99001)
        assert dict(os.environ) == before
    assert popen.call_args.args[0] == [str(game / "Warhammer3.exe"), "game_startup_mode", "campaign_load",
                                       "my campaign.save", ";", "used_mods.txt;"]
    assert popen.call_args.kwargs["cwd"] == str(game)
    child_env = popen.call_args.kwargs["env"]
    assert child_env["SteamAppId"] == child_env["SteamGameId"] == "1142710"
    assert result["profile_compatibility"]["enabled"]
    for key in profiles._PROFILE_KEYS:
        assert child_env[key].isascii()
        assert os.path.samefile(child_env[key], environment[key])


def test_candidate_roots_use_known_folders_skip_unicode_and_resolve_parent(profile_tree):
    root, _, _, _ = profile_tree
    program_data = root / "program-data"
    public = root / "public"
    game = root / "中文游戏"
    program_data.mkdir()
    public.mkdir()
    game.mkdir()
    virtual_parent = root / "virtual-program-data"
    profiles._WindowsPaths().create_junction(virtual_parent, program_data)
    native = Mock(known_folder=Mock(side_effect=lambda key: {
        "ProgramData": virtual_parent, "PublicDocuments": public,
    }[key]))
    # Read the actual implementation, overridden by the fixture for isolation.
    with patch.object(profiles, "_candidate_roots", _REAL_CANDIDATES):
        candidates = profiles._candidate_roots(native, game)
    assert candidates[0] == program_data / "WycccMM"
    assert candidates[-1] == public / "WycccMM"
    assert not any(path.parent == game for path in candidates)
    assert all(str(path).isascii() for path in candidates)


@contextmanager
def _without_admin_privileges():
    from ctypes import wintypes

    class SidAndAttributes(ctypes.Structure):
        _fields_ = [("sid", ctypes.c_void_p), ("attributes", wintypes.DWORD)]

    native = profiles._WindowsPaths()
    advapi = native.advapi
    advapi.ConvertStringSidToSidW.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p))
    advapi.ConvertStringSidToSidW.restype = wintypes.BOOL
    advapi.CreateRestrictedToken.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
        ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
        ctypes.POINTER(wintypes.HANDLE))
    advapi.CreateRestrictedToken.restype = wintypes.BOOL
    advapi.ImpersonateLoggedOnUser.argtypes = (wintypes.HANDLE,)
    advapi.ImpersonateLoggedOnUser.restype = wintypes.BOOL
    advapi.RevertToSelf.argtypes = ()
    advapi.RevertToSelf.restype = wintypes.BOOL
    advapi.CheckTokenMembership.argtypes = (wintypes.HANDLE, ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL))
    advapi.CheckTokenMembership.restype = wintypes.BOOL
    token = wintypes.HANDLE()
    restricted = wintypes.HANDLE()
    admin_sid = ctypes.c_void_p()
    impersonating = False
    try:
        if not advapi.OpenProcessToken(native.kernel.GetCurrentProcess(), 0x000A, ctypes.byref(token)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not advapi.ConvertStringSidToSidW("S-1-5-32-544", ctypes.byref(admin_sid)):
            raise ctypes.WinError(ctypes.get_last_error())
        disabled = SidAndAttributes(admin_sid, 0)
        # DISABLE_MAX_PRIVILEGE removes every privilege except ChangeNotify;
        # the Administrators SID becomes deny-only. This affects this thread only.
        if not advapi.CreateRestrictedToken(token, 1, 1, ctypes.byref(disabled), 0, None,
                                            0, None, ctypes.byref(restricted)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not advapi.ImpersonateLoggedOnUser(restricted):
            raise ctypes.WinError(ctypes.get_last_error())
        impersonating = True
        member = wintypes.BOOL()
        if not advapi.CheckTokenMembership(None, admin_sid, ctypes.byref(member)):
            raise ctypes.WinError(ctypes.get_last_error())
        assert not member.value
        yield
    finally:
        if impersonating and not advapi.RevertToSelf():
            raise ctypes.WinError(ctypes.get_last_error())
        native.kernel.LocalFree(admin_sid)
        if restricted:
            native.kernel.CloseHandle(restricted)
        if token:
            native.kernel.CloseHandle(token)


def test_native_junction_creation_and_reuse_do_not_require_admin_privileges(profile_tree, monkeypatch):
    root, _, environment, _ = profile_tree
    # A user-owned tree models a location writable by that user without granting
    # access through the Administrators group; no existing ACL is modified.
    user_root = root / "restricted-token-tree"
    native = profiles._WindowsPaths()
    native.private_directory(user_root, native.user_sid())
    profile = user_root / "中文普通用户"
    roaming = profile / "AppData" / "Roaming"
    local = profile / "AppData" / "Local"
    roaming.mkdir(parents=True)
    local.mkdir()
    environment.update(USERPROFILE=str(profile), APPDATA=str(roaming), LOCALAPPDATA=str(local))
    monkeypatch.setattr(profiles, "_candidate_roots", lambda native, game: [user_root / "aliases"])
    with _without_admin_privileges():
        first = _prepare(profile_tree)
        assert os.path.samefile(first.environment["USERPROFILE"], profile)
        assert _prepare(profile_tree).environment == first.environment


_REAL_CANDIDATES = profiles._candidate_roots

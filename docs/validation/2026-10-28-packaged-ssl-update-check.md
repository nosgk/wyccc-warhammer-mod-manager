# 正式包检查更新失败：SSL 运行时数据遗漏

## 现象与证据

用户检查更新时提示 `无法从更新源检查新版本：Gitee、GitHub`。

- 源码环境的 `UpdateService.check()` 能读取到两个更新源的 1.3.4 清单。
- 正式包运行日志在 2026-10-28 19:22、19:31、19:35 和 19:40 多次记录工坊请求的 `NETWORK_TRANSPORT_FAILURE`，阶段均为 `ssl_init`，最终停在 `ssl.py:440` 的 `SSLContext.__new__`，异常为不带文件名的 `FileNotFoundError: [Errno 2] No such file or directory`。
- 构建所用 Python 3.12.14 / OpenSSL 3.5.8 的 `ssl.py` 有一段运行时补丁：创建 SSLContext 时，读取 `ssl.py` 同目录的 `codex-rejected-cas.pem`。
- 该文件存在于构建解释器中，大小 906 字节，SHA-256 为 `c916ce1b14ed8ca69e9b5c9fab22799a900dd610d6d4ec9b1347f4fd48e53718`，但正式 1.3.4 EXE 的 PyInstaller 归档中没有它。

## 复现与对照

通过 PyInstaller `CArchiveReader` 读取现有正式 EXE 的 `PYZ.pyz`，执行其中原始 `ssl` 模块代码，将其 `__file__` 设置到项目 `build/` 下的临时目录，然后调用真实 `UpdateService.check()`：

1. 临时目录没有 PEM 时，稳定得到与用户截图一致的双更新源失败提示，底层异常与日志的 `ssl.py:440` 一致。
2. 只复制构建解释器中原样的 PEM 到模块同目录，再调用同一个更新检查，成功读取 1.3.4，来源为 Gitee。
3. `create_default_context()` 的 `verify_mode` 仍为 `CERT_REQUIRED`，`check_hostname` 仍为 `True`。

这是运行时数据文件遗漏，失败发生在联网之前。没有修改系统代理、Windows 证书库、SSL 模块或证书校验策略。对照使用的项目临时目录已自动删除。

## 修复

`scripts/build.py` 打包前验证当前解释器能创建默认 SSL 上下文，并将解释器附带的 `codex-rejected-cas.pem` 以原样加入冻结目录根部，供打包后的 `ssl.py` 读取。没有此辅助文件的普通 Python 解释器不增加该资源。

新增 `tests/test_packaged_ssl.py`，截取实际 `package_desktop` 生成的 PyInstaller 数据参数，按参数复制根部资源，将真实 `ssl.py` 移至模拟冻结目录，在独立 Python 进程中初始化 urllib opener 和默认 SSL 上下文。修复前以相同的 Errno 2 失败，修复后通过，同时检查证书及主机名校验保持启用。

## 验证

```powershell
.\.venv-build\Scripts\python.exe -m pytest tests/test_packaged_ssl.py tests/test_main.py tests/test_update_service.py -q
```

39 passed，6 subtests passed。

```powershell
.\.venv-build\Scripts\python.exe -m ruff check scripts/build.py tests/test_packaged_ssl.py
```

通过。正式包内原始代码的联网对照也通过。

本次完成源码、回归测试和诊断记录；尚未重新打包或替换公开的 1.3.4 EXE。已有 EXE 需要在打包后携带该资源，才能获得此修复。

## 1.3.5 正式打包验证

随后按用户要求准备 1.3.5，使用既有 `scripts/package_release.ps1 -SkipInstall -SkipTests` 打包；此前已完成完整后端与前端测试，因此打包阶段不重复执行测试。

- 后端：858 passed，251 subtests passed；前端：425 passed；生产构建和相关 Ruff 检查通过。
- 正式 EXE 的 ProductName 为 `Wyccc's Mod Manager`，FileVersion、ProductVersion 均为 `1.3.5`。
- 大小 65,781,173 字节，SHA-256 为 `18964F3A8FC43339DEB940DB7D250DD795F1044F93DBB0CFA5212F6A063AE687`。
- 直接读取该 EXE 的 PyInstaller 归档，确认存在原样的 906 字节 PEM，且其哈希与构建解释器中的文件一致。
- 从该 EXE 提取实际 `ssl` 与 `backend.update_service` 模块代码，配合归档中的 PEM 在隔离目录执行，成功读取仍指向 1.3.4 的公开清单；客户端版本识别为 1.3.5。证书与主机名校验均保持启用。

此验证覆盖正式包归档资源和实际更新请求代码；没有启动游戏，也没有把归档验证描述成桌面界面操作验证。

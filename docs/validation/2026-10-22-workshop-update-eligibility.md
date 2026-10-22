# 本地 MOD 的工坊更新资格

## 反馈与确认范围

用户反馈最新版本中，单选自己的 MOD 时没有“更新到工坊”；移除 Data 中的 Pack 后出现，放回后消失。重启和刷新工坊信息无效。尚未取得工坊链接及两边的 Pack 文件名，因此不能确认现场与下述复现完全相同。

当前源码的同名 Data/Workshop 一对一合并能保留作者信息，添加、删除、重新添加 Data 副本的测试通过。

## 已复现的缺陷与修复

Data Pack 已保存 `published_workshop_id`，但没有同名订阅副本时，扫描器不会为它加载工坊作者信息。API 在扫描后只恢复工坊 ID，没有恢复 `creator_id`。即使缓存已经有正确作者、刚完成在线刷新，更新资格接口仍拒绝本地项。若同时存在不同文件名的订阅副本，该工坊项可以更新，本地项不能更新。

修复在恢复发布关联后，批量从现有工坊缓存读取对应游戏的作者 ID。保留本地 Pack 路径，不增加网络请求，也不把发布关联本身当作所有权证明。当前 Steam 账号必须与作者相同；最终发布仍经过既有 Steam 所有者校验。

新增回归覆盖同名副本添加/移除、已发布本地项没有订阅副本或与订阅副本异名、在线刷新、重新创建 API 实例，以及作者缺失、账号不匹配、身份不可用和其他游戏缓存。

## 验证

- 修复前：新增用例捕获本地项资格为空；同名合并控制场景正常。
- 修复后：原始复现中，本地项与工坊项均恢复正确作者和更新资格。
- 后端：119 项测试和 43 个子用例通过。
- 前端：右键菜单、资格缓存、发布窗口等 4 个文件共 47 项测试通过。
- Ruff、Python 编译及 `git diff --check` 通过。

```powershell
python -m pytest tests/test_workshop_update_eligibility.py tests/test_scanner.py tests/test_storage_and_api.py tests/test_workshop_refresh.py tests/test_workshop_publish_tags.py tests/test_game_isolation.py -q
.\.venv-build\Scripts\ruff.exe check backend/api.py tests/test_workshop_update_eligibility.py
python -m py_compile backend/api.py tests/test_workshop_update_eligibility.py
git diff --check
```

前端在 `frontend` 目录执行：

```powershell
node node_modules/vitest/vitest.mjs run src/components/__tests__/ModContextMenu.test.js src/__tests__/filterAndMenuComponents.test.js src/__tests__/liveDetection.test.js src/__tests__/publishAndListComponents.test.js
```

本机 npm 自身启动失败，改用项目已安装的 Vitest 完成检查；未修改全局 Node/npm。验证使用隔离临时目录、工坊缓存及模拟的 Steam 账号/网络响应，没有上传真实工坊项目。未打包或发布，尚无反馈用户环境的复测结果。

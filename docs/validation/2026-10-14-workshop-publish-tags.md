# Workshop 上传和更新分类标签修复

2026-10-14，用户反馈上传或更新 MOD 后，分类标签总被改成“美化”。

## 原因与行为

前端发布窗口和后端分类映射都把无法识别的本地类型默认映射为 `graphical`。更新窗口没有保留工坊标签的选项，后端每次都提交 `['mod', category]`，覆盖原有标签。Python 包装层把未指定标签转为空数组，Node 桥接层又补为 `['mod']`；因此只去掉“美化”默认值仍会丢掉原标签。

修复后，更新窗口默认选择“保持现有标签”。没有明确选择分类时，后端不推断本地分类，Python 和 Node 均省略更新参数中的 `tags`。明确选择分类时，从现有的所有权查询结果保留其他标签，仅替换分类标签，匹配旧分类时不区分大小写。新上传仍可使用明确映射的本地类型（例如 `unit` → `units`）；未知、功能和语言包类型没有可靠映射时，需要在窗口中选择分类，前后端均阻止缺少分类的上传。相关文案覆盖六种内置语言。

项目记录的原生模块版本中，`tags` 为可选参数，只有提供该字段才调用标签更新，见 [固定版本 steamworks.js 源码](https://github.com/ceifa/steamworks.js/blob/80c5fd7c4958ba90cf87403c3f967b8a6d748473/src/api/workshop.rs#L73)。Steam 的标签写入接口见 [SetItemTags 文档](https://partner.steamgames.com/doc/api/ISteamUGC#SetItemTags)。

## 复现与验证

修复前，前端回归稳定得到 `category: 'graphical'`。新增后端回归经过真实 API、Python 包装和 Node 脚本，用隔离的 Steam 客户端替身记录最后的更新参数及标签：原标签 `['mod', 'Campaign', 'Scripting']` 在普通更新后变成 `['mod', 'graphical']`；手动改为单位分类后，`Scripting` 也被删除。修复后普通更新完全保留原标签，明确改为单位分类得到 `['mod', 'Scripting', 'units']`。

```powershell
python -m pytest tests/test_workshop_publish_tags.py tests/test_steamworks_bridge.py tests/test_storage_and_api.py tests/test_game_isolation.py -q
# 101 passed, 26 subtests passed

cd frontend
node node_modules/vitest/vitest.mjs run src/__tests__/publishAndListComponents.test.js src/__tests__/steamworksNative.test.js src/__tests__/languages.test.js --maxWorkers=2
# 3 test files passed, 43 tests passed
node node_modules/vite/bin/vite.js build
# Production build passed
```

Ruff 和 `git diff --check` 通过。前端回归还覆盖已知和未知本地类型的更新默认值、新上传选择分类后提交、手动改分类，以及重新打开窗口后恢复“保持现有标签”。既有标题、描述、语言、封面、所有权和游戏隔离回归仍通过。

验证没有调用真实 Steam 上传或修改线上工坊标签，未打包发布。玩家端和真实 Steam 提交需要在修复版上复测；已经被旧版覆盖的标签需要手动改回，本次修复无法推断原有标签。

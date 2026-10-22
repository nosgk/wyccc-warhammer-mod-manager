# Steamworks Workshop bridge

此目录用于调用 Steam 客户端的 Workshop 能力。`workshop_bridge.js` 通过 steamworks.js
查询指定语言的标题和长描述、检查及新增订阅、取消订阅、请求强制更新，并为用户明确选择的本地 MOD
创建或更新 Workshop 项目；更新时会把用户选择的 Steam 语言写入标题与描述，更新前会比较项目所有者与当前本机 Steam 账号。Python 后端
通过标准输入/输出与它通信。

发布分类与启动器本地 MOD 分类分别处理。新上传只推断能明确映射到 Workshop 的本地类型，无法映射时要求用户选择分类。更新默认保持现有工坊标签，Python 请求和原生更新参数均省略 `tags`；用户明确选择分类时，桥接层从所有权查询的最新结果保留非分类标签，再替换分类。参见 [分类标签修复与验证](../docs/validation/2026-10-14-workshop-publish-tags.md)。

源码运行时使用 `WMM_STEAMWORKS_NODE`、`WMM_NODE` 或 `PATH` 中的 Node.js。旧版
`WWM_*`、`WWMM_*` 环境变量仍可兼容读取。发布打包
流程会将构建时检测到的 `node.exe` 一并放入 PyInstaller 单文件程序，不要求发布版用户
另行安装 Node.js。

WH3 上传与更新都会在同一次 Steam 内容提交中写入 `pack_name` 和 `game_version`
键值标签。Pack 名从实际上传目录取得，游戏版本读取当前游戏 EXE 的 `FileVersionMS`。
更新旧条目时也会补齐或替换这两项，并处理原有键名的大小写变体；提交后关闭查询缓存回读，
确认成功才返回发布完成。其它游戏沿用其原有字段规则。
`repair_workshop_identification` 可只补齐自有条目的这两项字段，不提交标题、描述、分类、
封面或内容。现场修复及原版查询结果见
[2026-10-22 验证记录](../docs/validation/2026-10-22-workshop-ca-identification.md)。

`steamworks/` 中的原生模块来自本项目研究使用的 WH3-Mod-Manager 本地版本，其上游为
[steamworks.js](https://github.com/ceifa/steamworks.js)，许可证见
`../licenses/steamworks.js-LICENSE.txt`。本项目为语言化 Workshop 更新添加的最小原生补丁及
可复现来源记录见 `steamworks/LOCAL_PATCH.md`。

依赖关系查询使用隔离的 `steamworks_dependencies/` 运行时。该模块提供 Steam 官方
`GetQueryUGCChildren` 对应的 `getItemDependencies`，并与其匹配的 `steam_api64.dll`
放在独立目录，避免和语言化更新模块的 Steamworks SDK 版本互相覆盖。来源、哈希与许可证记录
见 `steamworks_dependencies/README.md`。

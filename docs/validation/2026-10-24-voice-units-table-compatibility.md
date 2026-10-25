# 语音-单位表兼容：离线验证

日期：2026-10-24。范围：战锤 3 启动器源码；未打包、发布或修改游戏与工坊 Pack。

## 行为

- MOD 右键菜单底部新增默认关闭的复选项，支持多选批量开启和关闭，悬浮显示用户指定说明。
- 普通字段：普通 MOD > 勾选兼容的 MOD > 原版。语音字段：勾选兼容的 MOD > 普通 MOD > 原版。
- 同类 MOD 之间保持既有内部 DB 文件名优先级及列表顺序；不改变 MOD 本身的加载顺序或其他文件的优先级。
- `main_units_tables` 语音字段：`audio_voiceover_culture`、`audio_voiceover_culture_override`、`audio_voiceover_actor_group`、`has_spoken_vo`、`vo_is_dragon`、`vo_is_dinosaur`。
- `land_units_tables` 语音字段：`selection_vo`、`selected_vo_secondary`、`selected_vo_tertiary`。
- 启动时通过已有 `!!!!wyccc_game_data_patch.pack` 输出完整合并行；仅兼容模式下只输出需要覆盖的记录。其他游戏数据功能关闭或未订阅时仍可独立工作。
- 开关持久化并参与补丁缓存指纹；关掉开关后清除无用补丁。可自动加载的 Movie Pack 同样参与判断。
- 兼容开关只对战锤 3 显示；联机模式继续沿用禁用启动器运行时补丁的既有规则。

## 实际语音包

只读输入：

- `X:\SteamLibrary\steamapps\workshop\content\1142710\3822270650\claude_vo_cth_soldiers.pack`
- `X:\SteamLibrary\steamapps\common\Total War WARHAMMER III\data\db.pack`

语音包包含 19 行 `main_units_tables` 与 24 行 `land_units_tables`。从当前原版同键记录建立平衡 MOD 测试数据，分别修改人数与近战攻击；使用 `!validation_balance` 和 `zzz_validation_balance` 两种内部文件名，并分别把平衡 MOD 放在语音 MOD 前后，覆盖四种组合。此平衡数据为验证夹具，不代表另一款真实 MOD。

四组均生成 43 行正确合并结果，每组核对 2,295 个字段：语音字段等于输入语音包；全部普通字段与平衡数据逐字段字节一致；输出内部 DB 文件名优先于两类源文件。合计核对 9,180 个字段。

## 自动回归

后端覆盖：两表字段优先级、多语音 MOD 顺序、不同表版本、空语音值、游戏数据倍率叠加、无修改输出、真实 Pack 写入与读取、独立开关生成和关闭、缓存复用与失效、Movie Pack、旧数据库迁移、API 保存和扫描恢复。共享单位编辑器、百科、三国与联机测试也包含在回归范围。

前端覆盖：菜单位置与默认状态、悬浮说明、六语言文案、批量开关与状态更新、保存期间的游戏切换隔离，以及既有菜单、选择和表结构更新测试。

本机 npm 启动因其依赖报错，前端测试改用项目本地 `node node_modules/vitest/vitest.mjs run ...`。

游戏内试听与实际运行效果仍需用户实测；本次证据为静态、离线合并及 Pack 回读验证。

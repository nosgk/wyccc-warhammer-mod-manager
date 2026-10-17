# 动态语音兼容验证

2026-10-17。实现位于启动器源码，未制作或发布新版 EXE，未启动游戏。

## 使用与行为

先订阅 [Dynamic Voice Patch](https://steamcommunity.com/sharedfiles/filedetails/?id=3821459750)，再在「兼容补丁」中勾选「动态语音兼容」，默认关闭。设置会持久保存。未订阅时禁用开关并显示订阅提示；保存开启设置及实际启动前，通过 Steam 原生接口重新核验当前账号的订阅状态。仅在 Data 或另一账号的工坊目录中存在空壳文件，不视为已订阅。

功能标记是 `wyccc_dynamic_audio_patch.pack`；它与实际生成的运行时兼容 Pack 分开。标记已按仅好友可见发布到工坊（3821459750），仅含一个 JSON 标记及 RPFM 元数据，不包含脚本、DB 或音频。源文件位于战锤 MOD 工作区的 `mod/已完工/wyccc_dynamic_audio_patch`，映射位置是游戏 Data；封面为同名 PNG。Data 与工坊下载副本的 Pack、封面 SHA-256 均一致，Steam 原生回读 `visibility=1`。

通过启动器启动 WH3 前，根据当前播放集、排序和实际可加载的 Movie Pack 生成 `!!!!wyccc_voice_compatibility.pack`，放在用户 MOD 前面。原语音 MOD 仍需启用，其 WEM、播放对象、DB 和脚本仍由原 MOD 提供。普通 MOD 停用后不再参与；实际自动加载的 Movie Pack 仍参与。

根据当前原版语音库识别预合并语音库中的原版分支，合并各 MOD 的新增与改动分支。同一 MOD 优先读取作者提供的 `_for_merging` 决策树；多个 MOD 修改同一路径时，列表靠前者优先。保留完整原版回退分支，不用高优先级 MOD 携带的原版值覆盖低优先级 MOD 的实际改动。

补丁覆盖原有 MOD 的相关 BNK 路径，保留各库的 BKHD、播放对象及其他分块，并把共用事件的每一份副本统一成相同决策树，避免依赖不同语音库的文件名加载顺序。不会复制或重新编码 WEM/WAV。补丁用专用标记识别，即使改名也不会成为下一轮生成输入；仅在 WH3 中按启动器内部补丁隐藏。

缓存指纹包含播放集、顺序、来源路径、文件状态与语音库内容 SHA-256，并核对生成 Pack 的 SHA-256。更新 MOD、修改排序、切换播放集或缓存损坏都会重建；关闭功能或不再需要合并时，在下次启动准备阶段删除旧运行时补丁及 Data 中的副本。源码、缓存和实际启动清单分开处理。

## 真实 Pack 对照

输入为用户指定的两个语音 MOD：

- `3821219934/!nippon_expedition_voices.pack`：22 个 BNK，95 个对话事件实例。
- `3780589718/@cathay_vo_chinese.pack`：36 个 BNK，296 个对话事件实例，包含作者的 `_for_merging` 库。

参考为 `3821220044/!nippon_expedition_voices_cathay_vo_compat.pack`。原版依据当前安装的 `audio_*_bnk.pack` 中的核心语音库读取。

生成结果：33 个 BNK 加 1 个标记文件，共 4,435,440 字节。94 个共用对话事件、51,839 条状态路径，与参考补丁逐条比较：目标音频节点、叶节点权重、概率及中间节点元数据全部一致，缺失路径 0、额外路径 0。188 份重复事件副本的内容一致。两个 MOD 之间实际修改同一路径的冲突数为 0。

参考补丁的 4 个事件使用 `mode=1`，原 MOD 使用 `mode=0`：894625206、2692524880、364316595、3693221533。动态补丁按优先级保留原 MOD 的模式，没有照抄参考补丁的这项额外调整。因此本次是决策树内容一致，不是 BNK 字节完全一致；模式在游戏中的实际影响仍待用户复测。

三个工坊 Pack 的完整 SHA-256 在生成前后相同，详见 [validation.json](validation.json)。生成 Pack 已回读并通过 RPFM MCP 的 WH3 诊断，Error=0，结果数组为空，详见 [rpfm-diagnostics.json](rpfm-diagnostics.json)。这些属于静态、离线及 Pack 证据，不代表游戏内已验证。

## 回归与范围

新增后端测试覆盖原版回退、三 MOD 合并、同分支优先级、同名 BNK、提前结束的决策树路径、静音节点、语言隔离、参数与版本不匹配、损坏输入、索引容量、缓存篡改、来源变化、Movie Pack、补丁输入排除、模拟启动排序及停用清理。模拟启动替换了进程启动函数，不会打开游戏。

订阅检查完成后的后端相关回归 180 项及 28 项子测试通过，其中本功能 22 项测试覆盖取消订阅清理、当前账号核验、工坊文件冒充、接口不可用与跨游戏隔离。前端全套 404 项通过，包含订阅门槛、开关保存和全部六种界面语言检查；`pnpm build`、Ruff 和 `git diff --check` 通过。上一轮首次无限制并发运行曾有一个无关的单位数据分页测试触发 5 秒超时，限制为两个 worker 后全部通过，没有修改该测试或超时设置。

当前支持 WH3 `0x80000088` 的 Wwise 对话事件；不同语言目录分别处理，未安装的非当前语言库不阻塞生成。不同 MOD 把同一事件分别放在通用目录和语言目录的混合布局会明确报告暂不支持。音乐、音效、冲突 WEM、需要重建播放对象或内嵌媒体的同名 BNK 不在合并范围。遇到不兼容结构会报告来源并中止启动准备。纯预合并库缺少作者原始树时，以当前原版作差；作者有意删除分支的行为不能从缺失路径推导，因此保留回退分支。

用户实机复测：启用两个原语音 MOD，在兼容补丁面板开启本功能，先停用独立的二者兼容补丁，分别检查震旦与日本角色在前端、战役、战役对话和战斗中的语音。再停用其中一个 MOD、切换排序或关闭本功能，完全退出后重新启动，确认对应语音及补丁清理行为。

## 复现

从项目根目录执行，输入 Pack 只读，输出保存在指定的验证目录：

```powershell
python -m scripts.validate_voice_compatibility --data 'X:\SteamLibrary\steamapps\common\Total War WARHAMMER III\data' --pack 'X:\SteamLibrary\steamapps\workshop\content\1142710\3821219934\!nippon_expedition_voices.pack' --pack 'X:\SteamLibrary\steamapps\workshop\content\1142710\3780589718\@cathay_vo_chinese.pack' --reference 'X:\SteamLibrary\steamapps\workshop\content\1142710\3821220044\!nippon_expedition_voices_cathay_vo_compat.pack' --output-dir 'docs\validation\voice-compatibility-20261017'
```

机制参考：[ChaosRobie 的对话事件合并工具说明](https://github.com/robert-d-schultz/Total-War-Modding-Scripts/blob/main/README.md)、[wwiser 的 Wwise 二进制结构解析源码](https://github.com/bnnm/wwiser/blob/master/wwiser/parser/wparser.py)。实现以本地 Pack 的实际数据和离线回读为准，没有引入这些项目的代码或运行依赖。

## English notes

Subscribe to [Dynamic Voice Patch](https://steamcommunity.com/sharedfiles/filedetails/?id=3821459750), then enable “Dynamic voice compatibility” in the compatibility dialog. The launcher verifies the current Steam account's subscription when enabling the setting and before launch; merely having the marker Pack on disk does not unlock the feature. The launcher merges WH3 Wwise dialogue state paths, retains vanilla fallbacks, and resolves real branch edits using the first MOD's priority. Existing MOD audio and playback objects remain necessary. Cached output is rebuilt when inputs change and removed on the next launch preparation after disabling the feature, unsubscribing or removing the conflict.

The two supplied MODs produce 94 shared events and 51,839 state paths, matching the reference patch's complete tree contents. Four event selection modes intentionally retain the original MOD values rather than the reference patch's extra changes. Source Pack hashes are unchanged and RPFM reports no errors. Game playback is unverified and must be tested by the user. This implementation does not merge music, sound effects, conflicting media or incompatible bank structures. Language directories are handled separately; missing unused language banks are allowed, while mixing global and localized copies of the same event across MODs is reported as unsupported.

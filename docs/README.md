# 文档索引

> 适用版本：`1.3.0`
> 本索引只列出**仓库内**文档。harness 改造的架构审计、问题清单、重构地图属于本地开发文档，默认不入库、不推送。

## 现行功能文档

| 文档 | 内容 |
| --- | --- |
| [features/ai-mod-order.md](features/ai-mod-order.md) | AI 智能排序：数据来源、使用方式、规则与限制 |
| [features/exception-probe.md](features/exception-probe.md) | 异常探针功能说明 |
| [unit-encyclopedia.md](unit-encyclopedia.md) | 单位百科数据与界面说明 |

## 研究与验证记录

这些是按日期记录的研究/验证材料，用于追溯当时的结论，**不代表当前实现**。当前行为以代码与 `README.md` 为准。

| 目录 | 内容 |
| --- | --- |
| [research/](research/) | 变体选择器、RPFM 表结构、MOD 诊断等专题研究 |
| [validation/](validation/) | 多人模式、语音兼容、工坊发布/更新、MOD 文件夹等人工验证记录 |
| [audits/](audits/) | 运行时 Pack 与工坊刷新的审计报告及复现脚本 |

## 历史归档

| 目录 | 内容 |
| --- | --- |
| [archive/superpowers/](archive/superpowers/) | 2026-07 至 2026-07 的历史实施计划与设计稿，已归档，不再维护 |

归档文档中的版本号、路径、功能范围可能已过时。需要了解当前实现时，请优先阅读：

- 根目录 [README.md](../README.md) / [README.en.md](../README.en.md)
- 本文档索引中的现行功能文档
- 代码与测试

## 文档维护规则

1. 新增功能文档放在 `features/`，并在本索引登记。
2. 研究/验证材料按日期命名，放在 `research/` 或 `validation/`，结论需注明适用版本。
3. 已完成的历史计划移入 `archive/`，不要与现行文档混放。
4. 设计文本变更需同步 6 种内置语言。
5. 不提交包含内部实现细节、未公开功能或敏感路径的开发文档。

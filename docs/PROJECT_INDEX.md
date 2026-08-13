# 项目文档索引 (PROJECT_INDEX)

> Stock Analyst（智能个股分析与评级系统）项目文档导航。更新于 2026-08-13（自父仓库迁入全部项目文档后整合）。
> 所有路径以项目根目录 `stock_analyst/` 为基准。

## 1. 入门与使用

| 文档 | 说明 |
|------|------|
| [AGENTS.md](../AGENTS.md) | AI 代理导航入口：项目概述、技术栈、入口点、模块地图、风险红线、工作约定。 |
| [用户使用说明.md](../用户使用说明.md) | 面向零代码用户的操作手册：安装、启动、8 个标签页功能导航、数据采集与分析流程。 |
| [CHANGELOG.md](../CHANGELOG.md) | 变更日志：功能开发、缺陷修复、数据库变更与验证记录（按日期倒序）。 |

## 2. 项目文档档案（docs/）

> 以下档案原存于父仓库 `Qoder cn/docs/`，2026-08-13 随项目独立成库整体迁入（git 历史一并迁移）。

| 文档/目录 | 说明 |
|------|------|
| [requirements_v1.1.md](requirements_v1.1.md) | **需求基线 v1.1**：唯一权威需求文档。 |
| [PM批次索引_20260727.md](PM批次索引_20260727.md) | PM 维护的批次总览：B1~B25 任务/模型/状态/验收报告对照表、遗留观察项、模型使用统计（原 PROJECT_INDEX.md）。 |
| [PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md) | 项目结构说明。 |
| [v5_implementation_report_revised_20260717.md](v5_implementation_report_revised_20260717.md) | v5.0 数据契约与评分引擎实施报告。 |
| [tasks/](tasks/) | 任务书（159 份，按批次 B2~B24 命名）。 |
| [reports/](reports/) | PM 验收报告（`pm_accept_*.md`，19 份）+ UX 验收截图等（152 项）。 |
| [reviews/](reviews/) | 架构师/监理评审记录（25 项）。 |
| [roles/](roles/) | 角色职责说明（PM/开发/架构师/QA，5 项）。 |
| [test_cases/](test_cases/) | 验收测试用例（4 项）。 |
| `pm_context_*.md` / `PM_接手提示词_20260809.md` 等 | 各批次 PM 上下文与交接提示词。 |
| `session_resume_20260719.md` 等 | 历史会话恢复摘要。 |

## 3. 架构与设计决策（docs/knowledge_base/）

| 文档 | 说明 |
|------|------|
| [decisions.yaml](knowledge_base/decisions.yaml) | v5.0 数据契约决策记录（Q01-Q06 / BUGFIX-01）：汇率处理、降级规则表述、Mock 参数化等。 |
| [entities.yaml](knowledge_base/entities.yaml) | v5.0 关键实体与属性：StockData / DataQuality / AnalysisResult / MockDataProvider。 |
| [open_questions.yaml](knowledge_base/open_questions.yaml) | v5.0 遗留待决问题清单（D01-D06），2026-08-13 已按代码实况更新状态。 |

## 4. 运行产物与运维

| 路径 | 说明 |
|------|------|
| [reports/](../reports/) | 每日分析报告（Markdown，按日期；今后生成物不入库）。 |
| [backups/](../backups/) | 数据库备份（`db_backup_*.db`，破坏性操作前自动生成）。 |
| [logs/](../logs/) | 运行日志（`app.log`，按日滚动，保留 7 天）。 |
| [scripts/](../scripts/) | 运维脚本：托盘程序（`tray.py`）、Windows 服务安装（`service_install.py`）、诊断脚本归档（`archive/`）。 |

## 5. 验证与测试

| 入口 | 说明 |
|------|------|
| `python -m pytest tests/` | 标准测试入口：评分引擎单测、路由冒烟、风控、回测、数据新鲜度等（隔离临时库，不触网）。 |
| `ruff check .` | 静态检查（项目内 `pyproject.toml` 质量门禁）。 |
| `mypy app.py config.py modules` | 类型检查（存量 54 项标注欠账待清理）。 |
| `python test_engine_compare.py` | 新旧评分引擎并行对比验收脚本。 |
| `python test_us11_consistency.py` | US11 每日报告一致性验证脚本。 |

## 6. 代码导航速查

| 关注点 | 入口 |
|--------|------|
| Flask 入口与启动 | `app.py` |
| API 路由 | `blueprints/`（9 个业务蓝图 + `_utils.py` 展示层工具） |
| 四维评分引擎（v5） | `modules/scoring_engine.py`（子项定义、降级规则、归一化） |
| 标准数据契约 | `modules/data_contract.py`（StockData / AnalysisResult） |
| 采集与补采 | `modules/data_collector.py` / `modules/backfill_scheduler.py` |
| 评级建议（红线） | `modules/advisor.py`（`generate_advice` 禁止修改） |
| 权重与评级档位 | `config_weights.json`（热加载）/ `config.py`（代码级兜底） |
| 引擎灰度切换 | `config_engine_switch.json` / `modules/engine_switcher.py` |
| 数据库结构与迁移 | `database/db_manager.py` |

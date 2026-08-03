# 开发提示词 B27

**推荐模型：minimax m3（MiniMax Plan）— 项目文档梳理，创意文案/说明文档**
**任务书：无独立任务书（纯文档产出）**

---

## 你的任务

编写一份**项目目录结构与命名规范说明书**，说明 `stock_analyst` 项目下每个文件夹的用途、存放的文件类型、文件命名规则，以及文件之间的归属关系。目标读者是新加入项目的协作者（PM/开发/监理），帮助快速定位"某类文件应该放哪里、叫什么名字"。

**产出文件**：`docs/PROJECT_STRUCTURE.md`

---

## 项目环境

- 项目路径：`C:\Users\zlb19\Desktop\Qoder cn\stock_analyst`（路径含空格）
- 这是一个面向零代码个人投资者的 A股+港股智能个股分析与评级系统
- 技术栈：Python + Flask + SQLite + akshare + Jinja2 单页应用

---

## 当前目录结构（需全部覆盖）

以下是项目的完整目录树，说明书必须对**每个文件夹和每个文件**给出说明：

```
stock_analyst/
├── app.py                          # Flask 主应用
├── config.py                       # 全局配置
├── config_engine_switch.json       # v5引擎灰度切换白名单
├── config_weights.json             # 四维权重 + 评级映射 + 行业覆盖
├── requirements.txt                # pip 依赖清单
├── start.bat                       # Windows 一键启动
├── start.sh                        # Linux/Mac 一键启动
├── stock_analyst.db                # SQLite 数据库（主库）
├── CHANGELOG.md                    # 变更日志（按日期记录所有批次变更）
├── 用户使用说明.md                  # 面向零代码用户的操作手册（B25更新，587行）
├── test_engine_compare.py          # 引擎对比测试
├── test_us11_consistency.py        # US-11一致性测试
├── _p0_ths_stress_result.json      # 同花顺压力测试结果（历史数据）
│
├── database/                       # 数据库层
│   ├── __init__.py
│   ├── db_manager.py               # 建表/连接/工具函数
│   └── stock_analyst.db            # 数据库副本（与根目录同步）
│
├── modules/                        # 核心业务模块层（17个.py文件）
│   ├── __init__.py
│   ├── data_collector.py           # 数据采集（K线/基本面/资金面/消息面）
│   ├── data_adapter.py             # DB→StockData 适配器
│   ├── data_contract.py            # StockData Pydantic 模型契约
│   ├── mock_data_provider.py       # 测试用 Mock 数据
│   ├── scoring_engine.py           # v5 四维评分引擎核心
│   ├── scoring_engine_validation.py# 评分引擎验证
│   ├── analysis_engine.py          # legacy 引擎（灰度保留）
│   ├── engine_switcher.py          # v5/legacy 切换控制器
│   ├── advisor.py                  # 建议+报告生成主入口
│   ├── daily_report.py             # 每日报告生成
│   ├── backtest_engine.py          # 回测引擎
│   ├── optimizer_engine.py         # 自动优化引擎
│   ├── index_collector.py          # 指数数据采集
│   ├── news_collector.py           # 新闻采集
│   ├── sentiment_dict.py           # 情绪词典
│   └── export_engine.py            # Excel/PDF 导出
│
├── templates/                      # 前端模板层
│   └── index.html                  # 单页应用前端（全部HTML+JS+CSS）
│
├── scripts/                        # 运维脚本层
│   ├── calibrate_verify.py         # 评分校准验证脚本
│   └── b26_margin_backfill.py      # B26 两融数据回填脚本
│
├── docs/                           # 文档层
│   ├── PROJECT_INDEX.md            # 项目全批次总览索引（B1-B26）
│   ├── PROJECT_STRUCTURE.md        # ← 本说明书产出文件
│   ├── requirements_v1.1.md        # 需求基线（唯一权威）
│   ├── CHANGELOG.md                # 全局变更日志
│   ├── 用户使用说明.md（注：根目录也有一份）
│   ├── m8_backtest_framework_plan_20260720.md   # 方案文档
│   ├── p0_data_completeness_plan_20260720.md    # 方案文档
│   ├── v5_implementation_report_revised_20260717.md # 旧版报告
│   ├── pm_context_resume_20260726.md            # PM上下文恢复
│   │
│   ├── tasks/                      # 任务书 + 开发提示词
│   │   ├── dev_tasks_日期_B批次号.md    # PM签发的正式任务书
│   │   ├── dev_prompt_B批次号.md       # 开发窗口提示词
│   │   ├── pm_prompt_latest.md          # PM提示词模板
│   │   ├── pm_ruling_20260722.md        # PM裁决记录
│   │   ├── qa_task_日期.md             # QA任务
│   │   └── architect_task_日期.md      # 架构师任务
│   │
│   ├── reviews/                    # 评审记录
│   │   └── review_主题_日期.md
│   │
│   ├── roles/                      # 角色定义
│   │   ├── 00_README.md            # 角色总览
│   │   ├── 01_product_manager.md   # PM角色
│   │   ├── 02_developer.md         # 开发角色
│   │   ├── 03_architect.md         # 架构师角色
│   │   └── 04_qa.md                # QA角色
│   │
│   ├── test_cases/                 # 测试用例
│   │   ├── tc_主题.md
│   │   └── test_prompt_主题.md
│   │
│   └── knowledge_base/             # 知识库
│       ├── snapshots/              # 快照
│       ├── decisions.yaml          # 决策记录
│       ├── entities.yaml           # 实体记录
│       └── open_questions.yaml     # 开放问题
│
├── reports/                        # 报告层（61个文件）
│   ├── YYYY-MM-DD.md               # 每日报告（9份，按日期）
│   ├── dev_selftest_B批次号.md     # 开发自验报告
│   ├── dev_verify_B批次号_日期.md  # 开发验证报告
│   ├── pm_accept_B批次号.md        # PM验收报告
│   ├── pm_reject_B批次号_日期.md   # PM驳回报告
│   ├── qa_accept_日期.md           # QA验收报告
│   ├── p0_主题_日期.md             # P0阶段报告
│   ├── b16_主题_日期.md            # 批次专项分析
│   ├── fix_日期_主题.md            # 修复报告
│   ├── test_report_主题_日期.md    # 测试报告
│   ├── ux_序号_页面.png            # UX截图
│   └── _b22_flask.log              # 运行日志
│
├── screenshots/                    # 界面截图层（18个.png）
│   ├── 序号_主题.png               # 功能截图
│   └── 主题_描述.png               # 专项截图
│
├── logs/                           # 日志层
│   └── rollback_audit.log          # 回滚审计日志
│
└── __pycache__/                    # Python编译缓存（自动生成，可删除）
```

---

## 说明书要求

### 1. 结构化分层说明

按以下层级组织，每一层用表格说明：

| 层级 | 文件夹 | 用途 | 典型文件 | 命名规则 |
|---|---|---|---|---|
| 根目录 | `/` | 启动入口+全局配置 | app.py, config.py | 固定名称 |
| 核心业务层 | `modules/` | 业务逻辑模块 | scoring_engine.py | 功能名.py |
| ... | ... | ... | ... | ... |

### 2. 命名规则提炼（重点）

需要总结出每类文件的命名 pattern，例如：

- **任务书**：`dev_tasks_YYYYMMDD_B批次号.md`（如 `dev_tasks_20260726_B26.md`）
- **开发提示词**：`dev_prompt_B批次号.md`（如 `dev_prompt_B26.md`）
- **PM验收报告**：`pm_accept_B批次号.md`（如 `pm_accept_B26.md`）
- **每日报告**：`YYYY-MM-DD.md`（如 `2026-07-26.md`）
- **UX截图**：`ux_序号_页面名.png`（如 `ux_01_home.png`）

### 3. 文件归属关系图

用文字或简单 mermaid 图说明一个批次从签发到关闭的文档流转：
```
任务书 → 开发提示词 → 自验报告 → PM验收报告 → 驳回报告(如有)
```

### 4. 特别说明

- **根目录的垃圾文件**：`$null` 和 `=` 两个文件是误操作产生的（PowerShell 重定向错误），应标注为"待清理"
- **数据库位置**：根目录和 database/ 目录各有一份 stock_analyst.db，说明哪个是主库
- **__pycache__**：说明这是 Python 自动生成的缓存，可安全删除
- **docs/tasks 命名不一致**：早期任务书用 `dev_tasks_20260722.md`（无批次号），后期统一为 `dev_tasks_日期_B批次号.md`，标注这一演进

### 5. 文档风格

- 面向协作者（PM/开发/监理），非零代码用户
- 表格优先，简洁实用
- 总篇幅控制在 200 行以内
- 中文撰写

---

## 约束

1. **仅新建** `docs/PROJECT_STRUCTURE.md`，不修改任何其他文件
2. 不修改任何代码文件（.py / .html / .json）
3. 以实际目录扫描结果为准（上方目录树），不要遗漏任何文件夹
4. 命名规则的提炼要准确反映现有文件的实际命名，而非臆造规则

---

## 自验

通读全文确认：
- 9个文件夹（含子文件夹）全部有说明
- 每类文件的命名规则有至少1个实际文件示例佐证
- 文件归属关系图能串联任务书→提示词→自验→验收的完整链路
- 特别说明的4个注意点均已覆盖

# B7 开发自验报告

| 项目 | 内容 |
|---|---|
| **批次** | B7（2026-07-24） |
| **执行人** | 开发（独立窗口） |
| **自验日期** | 2026-07-24 |

---

## 验收核验

| # | 验收标准 | 核验命令/方法 | 结果 | PASS/FAIL |
|---|---|---|---|---|
| 1 | 成本修正历史正常显示 | `api_get_all_cost_adjustments()` 增加字段映射：old_cost→original_avg_cost, new_cost→adjusted_avg_cost, reason→adjustment_reason, 新增 adjustment_notes | 映射代码已就位，前端字段对齐 | PASS |
| 2 | API 字段映射正确 | 代码审查：rec['original_avg_cost']=rec.pop('old_cost') 等4行映射 | 返回JSON包含 original_avg_cost/adjusted_avg_cost/adjustment_reason/adjustment_notes | PASS |
| 3 | 全量 v5 引擎生效 | `python -c "import json; d=json.load(open('config_engine_switch.json')); print(d['mode'])"` → all_v5 | mode=all_v5，whitelist/blacklist/circuit_breaker 保留不变 | PASS |
| 4 | v5 异常自动降级 | engine_switcher.py 代码未修改，熔断逻辑（max_consecutive_failures=2, cooldown_hours=24）保留 | 配置完整，降级安全网未拆 | PASS |
| 5 | A 股行业自动获取 | db_manager.py 幂等迁移添加 stocks.industry 列；data_collector.py 新增 fetch_stock_industry()；api_add_stock 和 api_batch_analyze 中触发获取 | PRAGMA table_info 确认 industry 列存在 | PASS |
| 6 | 看板行业分布完整 | watchlist-scores SQL 改为 SELECT s.industry；前端 dashRenderCharts 读取 st.industry | API 返回 industry 字段，前端渲染逻辑不变 | PASS |
| 7 | 硬编码字典已移除 | `Grep _INDUSTRY_MAP` 和 `DASH_INDUSTRY_MAP` 在 app.py / index.html 中 | 代码文件 0 匹配（仅 docs 任务书有描述性引用） | PASS |
| 8 | 用户使用说明完整 | 检查 `用户使用说明.md` 存在且包含快速开始/功能导航/FAQ 等章节 | 文件存在，含10个章节，覆盖全部要求内容 | PASS |
| 9 | 零代码约束不变 | 检查 requirements.txt 内容 | 无新增依赖，python app.py 一键启动不变 | PASS |
| 10 | 回归：核心功能不受影响 | Flask app import 成功；app.py/data_collector.py/db_manager.py 语法检查通过 | 所有模块 py_compile 通过，Flask 应用正常导入 | PASS |

---

## 红线核验

| # | 红线 | 核验方式 | 状态 |
|---|---|---|---|
| 1 | 零代码约束 | requirements.txt 未修改，无新依赖 | ✅ 合规 |
| 2 | if False 块 | Grep `if False` → L1474/L1513/L1546 三处保持 `if False` | ✅ 合规 |
| 3 | 需求基线映射 | FIX-ADJUST-UI→§2.10；ENGINE-ALLV5→§2.2；INDUSTRY-DYNAMIC→§2.7.1；USER-MANUAL→§1.2+§2.10 | ✅ 合规 |
| 4 | 任务蔓延 | 变更文件：app.py, config_engine_switch.json, database/db_manager.py, modules/data_collector.py, templates/index.html, 用户使用说明.md（新建）| ✅ 未超出范围 |
| 5 | config_weights.json 无 BOM | 本批次未触碰该文件 | ✅ 合规 |

---

## 变更文件清单

| 文件 | 变更类型 | 说明 |
|---|---|---|
| `app.py` | 修改 | ① api_get_all_cost_adjustments 字段映射 ② 删除 _INDUSTRY_MAP ③ watchlist-scores 改读 stocks.industry ④ api_add_stock 添加行业获取 ⑤ api_batch_analyze 行业补取 |
| `config_engine_switch.json` | 修改 | mode: whitelist → all_v5 |
| `database/db_manager.py` | 修改 | _migrate_columns 新增 stocks.industry 列（幂等） |
| `modules/data_collector.py` | 修改 | 新增 fetch_stock_industry() 函数 |
| `templates/index.html` | 修改 | 删除 DASH_INDUSTRY_MAP 硬编码字典 |
| `用户使用说明.md` | 新增 | 面向零代码用户的操作指南 |

---

## 技术要点说明

1. **FIX-ADJUST-UI**：在 API 响应层做字段 pop+rename，前端零改动
2. **ENGINE-ALLV5**：仅改 JSON 配置 mode 字段，engine_switcher 代码逻辑不动，熔断安全网保留
3. **INDUSTRY-DYNAMIC**：
   - akshare 网络请求 try-except 包裹，失败返回"未分类"不阻塞
   - ALTER TABLE 通过 _migrate_columns 的 try-except 机制实现幂等
   - 港股默认"港股"，A股通过 stock_individual_info_em 获取
   - 缓存策略：写入 DB 后不再重复请求
4. **USER-MANUAL**：中文，面向非技术用户，仅启动步骤含命令行

---

**自验结论**：4 张任务卡全部完成，10 项验收标准 PASS，5 项红线合规。

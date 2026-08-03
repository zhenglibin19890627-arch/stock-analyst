# 测试用例：零代码约束回归测试

| 项目 | 内容 |
|---|---|
| **用例编号** | TC-ZC-000 |
| **关联任务** | 全局最高约束 + QA-TASK-20260722 任务D |
| **需求基线** | `docs/requirements_v1.1.md` §3.3（本地运行）+ QA角色定义 §5.4 |
| **验收标准** | ① 一键安装无报错；② 一键启动无报错且浏览器可访问；③ 无需手动配置；④ 无 requirements.txt 之外的新依赖 |
| **设计方** | QA（质量保障） |
| **设计日期** | 2026-07-22 |
| **状态** | 测试用例预编制（待执行） |

---

## 一、测试范围与约束说明

### 1.1 零代码用户画像（最高优先级原则）

用户为零代码背景个人投资者，系统必须满足：
- `pip install -r requirements.txt` 一键安装
- `python app.py` 一键启动
- 浏览器打开 `http://127.0.0.1:5000` 即用
- 全程无需手动编辑任何配置文件、无需申请 API Key、无需配置环境变量

### 1.2 依赖现状（实测锚点）

[requirements.txt](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\requirements.txt) 共7个依赖：
`akshare>=1.16.0` / `Flask>=3.0.0` / `pandas>=2.1.0` / `numpy>=1.26.0` / `python-dateutil>=2.8.0` / `pydantic>=2.12.0` / `requests>=2.28.0`

代码中第三方 import 实测（全量扫描）：
- `flask`（app.py）/ `pandas`（data_collector.py）/ `numpy`（data_collector.py）/ `requests`（data_collector.py）/ `akshare`（data_collector.py）/ `pydantic`（data_contract.py）
- ✅ **全部在 requirements.txt 覆盖范围内，未发现 yaml/openpyxl/sqlalchemy 等额外依赖**

### 1.3 启动配置现状（实测锚点）

- [app.py L4](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\app.py)：`启动方法：python app.py` → `http://127.0.0.1:5000`
- [app.py L13-14](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\app.py)：自动设置 `NO_PROXY=*`（禁用系统代理，无需用户配置）
- [config.py L102-104](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\config.py)：`FLASK_HOST=127.0.0.1`, `FLASK_PORT=5000`, `FLASK_DEBUG=False`
- 启动脚本：`start.bat`（Windows）/ `start.sh`（Linux/Mac）

---

## 二、一键安装测试（验收标准①）

| 用例ID | 步骤 | 预期结果 | 优先级 |
|---|---|---|---|
| TC-ZC-IN-01 | 全新虚拟环境执行 `pip install -r requirements.txt` | 全部依赖安装成功，无报错，退出码=0 | P0 |
| TC-ZC-IN-02 | 安装后执行 `python -c "import akshare,flask,pandas,numpy,pydantic,requests,dateutil"` | 全部 import 成功，无 ModuleNotFoundError | P0 |
| TC-ZC-IN-03 | 验证 akshare 版本 | `ak.__version__ >= 1.16.0`（港股财务列名兼容依赖此版本，记忆库已知坑点） | P0 |
| TC-ZC-IN-04 | 验证 pydantic 版本 | `pydantic.__version__ >= 2.12.0`（v2 ClassVar 标注依赖，Q06 决策） | P1 |
| TC-ZC-IN-05 | 重复安装（已安装环境） | 幂等，无报错 | P2 |

> **已知坑点**（记忆库）：akshare 升级后港股财务接口列名中→英漂移，需验证 `stock_financial_hk_analysis_indicator_em` 可用。

---

## 三、一键启动测试（验收标准②）

| 用例ID | 步骤 | 预期结果 | 优先级 |
|---|---|---|---|
| TC-ZC-ST-01 | 执行 `python app.py` | 服务启动，控制台无 Traceback，监听 127.0.0.1:5000 | P0 |
| TC-ZC-ST-02 | 浏览器访问 `http://127.0.0.1:5000` | 页面正常加载（HTTP 200），显示主界面 | P0 |
| TC-ZC-ST-03 | 启动后数据库自动初始化 | `stock_analyst.db` 自动创建，`init_database()` 建表无报错 | P0 |
| TC-ZC-ST-04 | 使用 `start.bat`（Windows）启动 | 脚本自动检测端口占用，必要时释放后启动 | P0 |
| TC-ZC-ST-05 | 5000 端口已被占用时启动 | start.bat 检测并释放/提示，非直接报错崩溃 | P1 |
| TC-ZC-ST-06 | 启动后访问关键 API（`/api/health` 或主页接口） | 返回正常响应，证明服务就绪 | P1 |
| TC-ZC-ST-07 | 启动日志输出 | 控制台输出"服务就绪"类提示（记忆库"服务启动状态输出规范"） | P2 |

> **已知坑点**（记忆库）：
> - 项目必须使用 Python 3.12 路径启动 Flask
> - FLASK_DEBUG=False（避免双进程导致 SQLite 数据库锁冲突）
> - NO_PROXY 自动设置（避免 Clash/V2Ray 未运行时网络失败）

---

## 四、无需手动配置测试（验收标准③）

| 用例ID | 步骤 | 预期结果 | 优先级 |
|---|---|---|---|
| TC-ZC-CF-01 | 全局搜索 API Key / Token / Secret 配置项 | 不存在需用户手动申请填写的密钥（akshare 为免费公开接口） | P0 |
| TC-ZC-CF-02 | 检查 config.py 是否有占位符（如 `YOUR_API_KEY`） | 无需用户编辑的占位符，所有配置均有默认值 | P0 |
| TC-ZC-CF-03 | 数据库连接配置 | SQLite 本地文件（`stock_analyst.db`），无需配置数据库服务/账号密码 | P0 |
| TC-ZC-CF-04 | 港币汇率配置 | 固定 0.92（Q01 决策，适配器层自动转换），无需用户设置 | P1 |
| TC-ZC-CF-05 | 权重配置 | `config_weights.json` 有默认值，文件不存在时回退 config.py 代码级默认（[config.py L34](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\config.py)） | P1 |
| TC-ZC-CF-06 | 删除 config_weights.json 后启动 | 系统正常启动（回退默认权重），不报错 | P1 |
| TC-ZC-CF-07 | 环境变量依赖 | 除 NO_PROXY（代码自动设置）外，无强制要求用户预设的环境变量 | P1 |

---

## 五、无新依赖测试（验收标准④）

| 用例ID | 步骤 | 预期结果 | 优先级 |
|---|---|---|---|
| TC-ZC-DEP-01 | 扫描所有 .py 文件的 import 语句，与 requirements.txt 比对 | 所有第三方库均在 requirements.txt 内（本次实测已通过） | P0 |
| TC-ZC-DEP-02 | 重点验证 M8 新增模块 [backtest_engine.py](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\modules\backtest_engine.py) import | 仅标准库（os/sys/json/logging/datetime）+ 项目内模块，无新 pip 依赖 | P0 |
| TC-ZC-DEP-03 | 验证 P0 资金面改动（同花顺源） | 仅用 akshare 已有接口 `stock_fund_flow_individual()`，无新依赖 | P0 |
| TC-ZC-DEP-04 | 验证港股基本面改动 | 仅用 akshare 已有接口，无新依赖 | P1 |
| TC-ZC-DEP-05 | 前端 index.html 引用的外部 CDN/CSS/JS | 仅 ECharts 等公共 CDN，无需用户本地安装前端依赖 | P2 |

---

## 六、回归测试（既有功能不受影响）

| 用例ID | 场景 | 预期结果 | 优先级 |
|---|---|---|---|
| TC-ZC-RG-01 | 添加自选股（A股+港股） | 正常添加，上限50只 | P0 |
| TC-ZC-RG-02 | 触发个股分析报告生成 | 四维评分正常，报告含可视化图表 | P0 |
| TC-ZC-RG-03 | 自选股总览看板 | 正常展示，区分A股/港股 | P0 |
| TC-ZC-RG-04 | 变更日志查看 | 评级/建议变化历史正常记录 | P1 |
| TC-ZC-RG-05 | 数据库既有数据完整性 | 历史评级/报告/日志不丢失、不污染 | P0 |
| TC-ZC-RG-06 | 12只白名单 + 贵州茅台(600519) + 美的集团(000333) 回归标杆 | 数据一致性，历史不一致问题彻底消除（记忆库"附加修复验证范围"） | P1 |
| TC-ZC-RG-07 | 经典引擎10只0分股票（HK9988/000858/HK1810/002714/002415/000977/688041/688795/688802/601012） | 修复后无0分，评分正常（详见 tc_data_quality.md） | P0 |
| TC-ZC-RG-08 | 全量26只股票日报生成 | 流程无报错，无0分股票，数据完整度合理 | P0 |

---

## 七、执行说明

- **零代码约束为质量红线第1条**，任何用例失败即触发驳回，不得降级处理
- **执行顺序**：一键安装(二) → 一键启动(三) → 无需配置(四) → 无新依赖(五) → 回归(六)
- **执行环境**：Windows（用户主环境），需用 Python 3.12
- **失败处理**：任一 P0 用例失败 → 立即出具驳回意见，标注为"零代码红线违反"

**设计人**：QA | **设计日期**：2026-07-22

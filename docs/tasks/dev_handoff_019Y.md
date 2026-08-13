# 开发窗口启动提示词：019Y 数据源扩展（2026-08-11）

> 本文档供开发窗口启动时作为首条消息粘贴使用。包含 019Y 批次的完整上下文。
> 任务书：`docs/tasks/dev_tasks_20260811_019Y_data_source_expansion.md`（请先完整阅读）

---

## 一、你的角色与任务

你是 019Y 批次的**开发角色**。PM 已签发任务书，监理已批准。请阅读任务书后开始开发，完成后提交自测报告。

**任务书路径**：`docs/tasks/dev_tasks_20260811_019Y_data_source_expansion.md`

---

## 二、项目环境

- **项目路径**：`C:\Users\zlb19\Desktop\Qoder cn\stock_analyst`（路径含空格，PowerShell 操作须加引号）
- **Python**：`C:\Users\zlb19\AppData\Local\Programs\Python\Python312\python.exe`（3.12.9）
- **SQLite**：`stock_analyst.db`（只读核验：`sqlite3.connect(r"file:...?mode=ro", uri=True)`）
- **日志**：`logs\app.log`（当日）+ 滚动归档

---

## 三、019Y 已就绪的依赖

- `mootdx==0.11.7`（含 tdxpy）—— 已安装，PM 探针实测通过
- `baostock==0.9.3` —— 已安装，PM 探针实测通过
- **httpx=0.28.1 / tenacity=9.1.4** —— 不得降级。mootdx 声明需 httpx<0.26，但核心功能走 tdxpy TCP socket，不依赖 httpx，实测正常。
- **探针脚本**：`scripts/probe_019y_mootdx_baostock.py`（含全部接口调用示例，开发时可直接参考）

---

## 四、开发纪律（必须遵守）

1. **五步中转法**：项目目录文件操作须 Write 到工作区 → Copy-Item 回写 → Select-String 锚点核验 → DeleteFile 删临时副本 → 呈报
2. **git 不可用作红线核验**：git 仓库根在父目录 `Qoder cn`，全仓文件均带 ` M` 标记（autocrlf 历史污染）→ 红线核验改用**文件 mtime**
3. **PowerShell 编码坑**：中文输出 GBK 乱码但数值可读；统计中文日志用 `-Encoding UTF8`；SQL 里的 `*` 会被 PowerShell 当通配符，需用临时 .py 脚本查库
4. **不直接采信自己的结论**：开发完成后用独立核验（数字、mtime、查库）确认，不自说自话
5. **零代码用户优先**：代码注释和设计要让非技术人员能理解，用大白话

---

## 五、三条红线（违反即返工）

1. **不动评分逻辑**：本批次只做数据采集 + 入库 + 前端展示标注。严禁修改评分计算模块
2. **不改现有接口行为**：现有 akshare 接口、野接口（腾讯/东财/新浪）的主源地位不变，新数据源只作降级备用
3. **不动现有数据库已有字段**：新数据维度一律建新表或 ALTER TABLE ADD COLUMN

---

## 六、关键背景知识

- **资金流表名**：`raw_capital_flow`（非 capital_flows），字段含 `trade_date/is_estimated/capital_source`
- **代码格式差异**（重要）：
  - akshare：`000001`（纯数字）
  - mootdx：`000001`（纯数字，沪市 6 开头，深市 0/3 开头）
  - baostock：`sz.000001` / `sh.600276`（带前缀）
  - 需写统一映射函数
- **baostock 不支持港股**：港股估值仍走 akshare
- **mootdx 港股支持有限**：港股 K线仍走现有源（腾讯/akshare）
- **mootdx bestip 首次耗时**：`Quotes.factory(bestip=True)` 首次 ~5 秒选服务器，须做全局单例缓存
- **baostock 登录管理**：`bs.login()` / `bs.logout()` 成对使用，批次级管理，不要每只股票重复登录

---

## 七、自测报告要求（完成后必须提交，缺一不可）

1. **改动文件清单**：列出所有修改/新增的文件（含行数变化）
2. **数据库变更说明**：新建了哪些表、加了哪些字段、是否影响已有数据（附建表 SQL）
3. **接口实测日志**：mootdx（K线降级、五档盘口样本、单例缓存验证）、baostock（估值样本、财务备用、生命周期验证）、akshare（新接口样本）
4. **降级链路验证**：模拟主源失败，验证降级到备用源的全链路日志
5. **回归测试**：跑一次完整日报流程，确认现有功能不受影响，附日报评分结果对比
6. **前端展示**：五档盘口、估值数据展示效果截图（如有前端改动）
7. **文件 mtime 锚点**：所有改动文件的 mtime 时间戳

---

## 八、PM 探针实测关键结论（供参考）

- mootdx 实时行情 0.03s 返回，含完整五档买卖盘（bid1-5/ask1-5 + 对应量）
- mootdx 日K线 0.02s 返回，含当天数据（比 baostock T+1 更实时）
- baostock 估值数据（PE/PB/PS/PCF）0.02s 返回，数值合理
- baostock 财务数据（ROE/净利率/毛利率/EPS/股本）0.02s 返回
- 两者均走 TCP socket，不受项目 `requests.Session.request` 全局 patch 影响

---

> 粘贴本文件全部内容到新窗口作为首条消息，开发角色即获得 019Y 完整上下文。

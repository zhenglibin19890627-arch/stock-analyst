# 开发任务书 019L — "刷新报告"后生成时间显示"—"修复（/advise 端点补 generated_at）

**签发日期**：2026-08-05
**签发人**：PM
**批次编号**：019L
**优先级**：P2（前端展示缺陷；DB 时间未丢，仅刷新报告路径显示"—"）
**关联批次**：019D（generated_at 链路建立）、019G（时间展示优化）
**架构评审**：⚠️ 有条件通过（评审报告：`docs/reviews/review_019L_advise_generated_at_20260805.md`），已按 M-1/M-2 修订定稿 v2

---

## 角色定义（内嵌，无需额外窗口提示词）

### 你的角色：开发人员

**职责边界**：
- 按本任务书规格实现 /advise 端点 generated_at 补充，完成编码+自验
- 不负责正式验收（QA 独立验收）
- 不修改红线区域（advisor.generate_advice、风控阈值）
- 交付物：修改后的 `app.py` + 自验报告 `reports/dev_selftest_019L_advise_generated_at_20260805.md`

### 独立性原则
- 各角色独立不兼职：PM 不兼架构、架构师不编码、开发不验收、QA 独立测试
- 开发人员仅做编码+自验，不执行正式验收
- 架构师评审结论未出前，本任务书为 v1；评审通过后 PM 修订定稿 v2，开发以定稿为准

### 项目背景摘要
| 项 | 内容 |
|---|---|
| 项目路径 | `C:\Users\zlb19\Desktop\Qoder cn\stock_analyst`（含空格，命令行需引号） |
| 数据库路径 | `C:\Users\zlb19\Desktop\Qoder cn\stock_analyst\stock_analyst.db` |
| Python 解释器 | `C:\Users\zlb19\AppData\Local\Programs\Python\Python312\python.exe` |
| 技术栈 | Python + Flask + SQLite + akshare + Jinja2 单页应用 |
| 最高约束 | **零代码用户可独立运行**：无新 pip 依赖（当前 9 包） |

### 环境约束（硬性，违反将导致执行失败）
1. **项目在 IDE 工区外**：路径含空格，Write 工具直写会报错，须 "工作区 Copy + SearchReplace 编辑 + Copy-Item 覆盖回项目目录"
2. **PowerShell 中文**：追加中文到文件用 `[System.IO.File]::ReadAllText + WriteAllText`（UTF-8），禁止 Add-Content/Out-File（乱码）
3. **PowerShell 内联 Python**：含 `*` 的 SQL 会被通配符解析破坏，须用 `chr(39)` 包裹字符串或写临时 .py 脚本
4. **用户是零代码用户**：所有技术决策以"零代码用户可独立运行"为最高优先级

---

## 〇、执行窗口与流程说明

| 项目 | 说明 |
|---|---|
| 流程路径 | ✅PM 签发 v1 → ✅架构师评审（有条件通过，M-1/M-2 并入 v2） → ✅监理批准 v2（2026-08-05） → ✅开发执行+自验（13/13 PASS） → ✅QA 独立验收（39/39 断言 PASS） → ✅PM+QA 双签（2026-08-05） → ✅监理批准关闭（2026-08-05） |
| 并行说明 | 与 019N（data_collector.py）**文件零重叠可并行**；QA 各自独立 mock 验收，不依赖运行实例 |

---

## 一、背景

### 1.1 缺陷现象（监理反馈 + PM 代码定位）

报告页点"🔄 刷新报告"按钮后，"报告生成于"显示"—"；普通查看正常显示时间。

### 1.2 根因

**前端**（`templates/index.html` L4185）：刷新报告按钮 `onclick="loadReport(stockId, true)"`——`forceRefresh=true` 走**实时重新分析路径** `_loadReportFromAdvise`（L4145-4168）→ POST `/api/stocks/<id>/advise`。

**前端渲染**（L4213）：`报告生成于：_fmtGenTime(adviseData.generated_at)`；`_fmtGenTime`（L5412-5415）对 undefined/非字符串返回 '—'。

**后端**（`app.py` L1117-1134 `api_advise_stock`）：调用 `generate_advice(stock_id)` 返回 result，**未补充 `generated_at` 字段** → 前端拿到 undefined → 显示"—"。

**对照**：`/api/stocks/<id>/report-latest` 无快照回退路径（app.py L939-940）已补 generated_at（019D），但 `/advise` 端点**漏补**（019D 遗漏点）。

### 1.3 影响面

- 仅"刷新报告"路径显示"—"；DB 时间未丢（列表/看板/普通查看正常）
- 纯展示缺陷，不影响评分/数据

---

## 二、执行角色

**开发**（单人）

---

## 三、任务范围

> **改动范围收敛：仅 `app.py` 一个端点，一处代码。**

### 任务 1：`api_advise_stock` 成功分支补充 generated_at

**文件**：`app.py`
**位置**：L1117-1134 `api_advise_stock`

**改动**（与 /report-latest L939-940 同型，019D 先例）：

```python
@app.route('/api/stocks/<int:stock_id>/advise', methods=['POST'])
def api_advise_stock(stock_id):
    """执行模块2分析+模块3建议生成，返回完整评级建议"""
    from modules.advisor import generate_advice

    try:
        result = generate_advice(stock_id)
        # 005: 后处理集成价格建议（不修改 generate_advice）
        if result.get('success'):
            from modules.price_advisor import generate_price_advice

            result['price_advice'] = generate_price_advice(stock_id, result)
            # 009补充：动态操作建议覆盖旧建议，避免矛盾
            if result.get('price_advice', {}).get('action_suggestion'):
                result['position_advice'] = result['price_advice']['action_suggestion']
            # 019L: 补充 generated_at（报告生成时刻，与 /report-latest 019D 同型）
            from datetime import datetime, timezone
            from datetime import timedelta as _td

            _CN_TZ = timezone(_td(hours=8), name='Asia/Shanghai')
            result['generated_at'] = datetime.now(_CN_TZ).isoformat()
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'message': f'建议生成失败: {str(e)}'}), 500
```

**要求**：
1. `generated_at` 仅当 `result.get('success')` 时补充（失败路径不加，与 /report-latest 语义一致）
2. 时区用 `_CN_TZ`（东八区），格式 `datetime.now(_CN_TZ).isoformat()`——与 019D L940 完全一致（前端 `_fmtGenTime` 兼容 ISO 格式，slice(0,16) 显示"YYYY-MM-DD HH:MM"）
3. `from datetime import ...` 放函数内（与 L902-905 /report-latest 同型，避免顶部 import 改动扩大范围）

**明确不改范围**：

- `templates/index.html` — 不碰（前端已正确读取 adviseData.generated_at，只缺后端字段）
- `modules/advisor.py` — 不碰（B24 红线：generate_advice 禁止修改）
- `/api/stocks/<id>/refresh-full` 全量刷新端点（**app.py L836-871，函数 `api_refresh_full`**，M-1 更正：路由为 `/refresh-full` 非 `/refresh`）— 不碰（同型问题存在；架构师核验该端点**前端零调用**（R-3），不构成用户可见缺陷，登记备查——如监理要求一并处理可零成本扩展）
- `modules/data_collector.py` — 不碰（019N 并行批次）
- `config.py` / `requirements.txt` / `database/db_manager.py` / `templates/*` — 不碰

---

## 四、验收标准

1. **代码级核查**：`api_advise_stock` 成功分支含 `result['generated_at'] = datetime.now(_CN_TZ).isoformat()`；失败分支无该行
2. **编译验证**：`python -m py_compile app.py` 无错误
3. **功能验证（QA mock，M-2 细化）**：
   - **mock 方式**：Flask test_client POST `/api/stocks/1/advise`（`jsonify` 需应用上下文，勿直接调视图函数）
   - **成功态**：须**同时 patch** `modules.advisor.generate_advice` 与 `modules.price_advisor.generate_price_advice`（防 price_advisor 真实执行查库/持仓破坏 mock 隔离）→ 断言响应含 `generated_at`，格式 `YYYY-MM-DDTHH:MM:SS` 前缀
   - **时间偏差断言**：`datetime.fromisoformat(resp['generated_at'])` 与 mock 执行时刻（东八区 `datetime.now(timezone(timedelta(hours=8)))`）求差 **<60s**
   - mock `generate_advice` 返回 success=False → 断言响应无 generated_at 或 message 含错误
   - 断言 price_advice / position_advice 逻辑不受影响（005/009 行为保持）
4. **前端联动验证（QA 可选）**：`_fmtGenTime(ISO串)` → "YYYY-MM-DD HH:MM"（纯 JS 断言，无需浏览器）
5. **零改动确认**：除 `app.py` 外所有文件哈希不变

---

## 五、红线约束

1. **B24 红线**：`modules/advisor.py` 的 `generate_advice` 禁止修改——本批次只在 app.py 端点层补字段，不触碰
2. **范围红线**：改动仅限 `app.py` 的 `api_advise_stock` 一个端点
3. **语义红线**：失败路径不补 generated_at（与 /report-latest 一致）；正常路径行为零变化（仅增字段）
4. **零代码约束**：不引入新 pip 依赖（datetime/timezone 标准库）；config.py/DB schema 不碰
5. **并行批次隔离**：本批次不触碰 `data_collector.py`（019N 范围）；QA 验收不与 019N 混用 mock

---

## 六、执行顺序

```
Step 1: ✅ PM 签发 v1
Step 2: ✅ 架构师评审（2026-08-05 有条件通过，M-1/M-2 并入 v2）
Step 3: ✅ 监理批准 v2（2026-08-05）
Step 4: ⏳ 开发执行 + 自验
Step 5: ⏳ QA 独立验收 → PM+QA 双签 → 监理批准关闭
```

---

## 七、PM 备注

1. **立项来源**：监理反馈"刷新报告后生成时间为空"→ PM 定位根因（/advise 端点缺 generated_at，019D 遗漏）→ 监理列入待修清单 → 019N 立项时监理指示同步签发 019L。
2. **同型遗漏点登记（M-1 更正）**：`/api/stocks/<int:stock_id>/refresh-full` 全量刷新端点（app.py L836-871，函数 `api_refresh_full`）同样未补 generated_at——架构师核验该端点**前端零调用**（用户界面不可达，R-3），不构成用户可见缺陷，维持备查不处理；如需修复可零成本追加（模式与 A-1 相同）。
3. **019D 先例**：`/report-latest` 无快照回退路径 L939-940 已有同型代码（`advice['generated_at'] = datetime.now(_CN_TZ).isoformat()`），本批次照搬该模式，保证时间格式全站一致。
4. **与 019N 并行**：本批次只动 app.py，019N 只动 data_collector.py，文件零重叠，可并行开发/并行 QA。注意双方 QA 各自用 mock 验收，验收报告独立出具。
5. **v2 修订说明（M-1/M-2）**：M-1 端点名更正（refresh → refresh-full，app.py L836）；M-2 验收细化（test_client 方式 + 同时 patch price_advisor + 时间偏差 <60s 断言）。

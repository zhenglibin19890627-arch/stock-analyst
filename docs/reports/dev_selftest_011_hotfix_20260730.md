# DEV自验报告：011-HOTFIX 时区Bug紧急修复

> **执行人**：开发 | **日期**：2026-07-30 | **状态**：自验通过

---

## 一、修复内容

| 编号 | 文件 | 行号 | 修复内容 |
|------|------|------|----------|
| FIX-1 | `modules/data_collector.py` | L526 | `datetime.now(_CN_TZ)` → `datetime.now(_CN_TZ).replace(tzinfo=None)` |
| FIX-2 | `modules/data_collector.py` | L874 | `datetime.now(_CN_TZ)` → `datetime.now(_CN_TZ).replace(tzinfo=None)` |
| FIX-3 | `modules/data_collector.py` | L2104 | `datetime.now(_CN_TZ)` → `datetime.now(_CN_TZ).replace(tzinfo=None)` |

**根因**：`datetime.now(_CN_TZ)` 返回 tz-aware 对象，与 `datetime.strptime()` 返回的 tz-naive 对象相减抛出 `TypeError: can't subtract offset-naive and offset-aware datetimes`，被外层 except 静默捕获导致增量门控失效。

**修复方式**：统一使用 `.replace(tzinfo=None)` 转为 naive 后再做日期运算（与 L541、L1951 已有正确范例一致）。

---

## 二、自验结果

验证脚本：`scripts/verify_011_hotfix.py`

| 验证项 | 调用 | 预期 | 实际结果 | 判定 |
|--------|------|------|----------|------|
| V1 A股80天门控 | `fetch_a_fundamental('000333')` | 返回含"跳过"，无TypeError | `('success', '同日跳过(财报80天TTL内+PE/PB 24h内)')` | **PASS** |
| V2 港股80天门控 | `fetch_hk_fundamental('HK3690')` | 返回含"跳过" | `('success', '同日跳过(港股财报8天内)')` | **PASS** |
| V3 融资余额增量 | `fetch_margin_balance('600276', 'a_stock')` | 不抛TypeError，正常返回 | `('success', '融资余额已更新(3条记录)')` | **PASS** |
| V4 force_full绕过 | `fetch_a_fundamental('000333', force_full=True)` | 全量采集不受门控 | `('success', '基本面数据采集成功')` | **PASS** |

---

## 三、红线合规确认

| 红线 | 状态 |
|------|------|
| 仅改3行 | ✓ 仅修改 L526/L874/L2104 各1行 |
| `fetch_capital_flow` 签名不变 | ✓ 未触碰 |
| `advisor.py` 不可修改 | ✓ 未触碰 |
| 011增量逻辑不破坏 | ✓ V1~V4验证门控+绕过均正常 |
| 零代码约束（无新pip依赖） | ✓ 无新依赖 |
| 三处 `if False` 硬禁用不变 | ✓ 未触碰 |

---

## 四、结论

3处时区Bug已全部修复，V1~V4自验全部通过，增量门控恢复正常工作。提交QA复验。

# PM 验收报告 ACCEPT-016 启动优化

| 项 | 内容 |
|---|---|
| 批次 | 016 |
| 验收日期 | 2026-08-03 |
| 痛点 | #8 需命令行启动（零代码用户对命令行有天然恐惧） |
| 执行方式 | Chats 当前窗口直接执行（PM 上下文授权，改动极小） |
| 改动文件 | `start.bat`（仅2处） |

---

## 一、改动清单

| # | 位置 | 改动前 | 改动后 | 说明 |
|---|---|---|---|---|
| 1 | start.bat L52-53 | `for %%D in (flask pydantic requests) do (` | `for %%D in (flask pydantic requests akshare pandas numpy dateutil openpyxl pytest) do (` | 依赖检查扩充为全量9包（对齐 requirements.txt） |
| 2 | start.bat L66 | `pip install flask pydantic requests akshare pandas numpy python-dateutil -q` | `pip install -r requirements.txt -q` | 安装命令改用 requirements.txt（与 start.sh 对齐，不漏包） |

---

## 二、自验结果

### V1 依赖检查列表与 requirements.txt 对齐 ✅

| requirements.txt 包名 | start.bat 检查的 import 名 | 对应 |
|---|---|---|
| Flask | flask | ✅ |
| pydantic | pydantic | ✅ |
| requests | requests | ✅ |
| akshare | akshare | ✅ |
| pandas | pandas | ✅ |
| numpy | numpy | ✅ |
| python-dateutil | dateutil | ✅ |
| openpyxl | openpyxl | ✅ |
| pytest | pytest | ✅ |

9/9 完全对齐。**修复前只检查3包（漏6包），修复后检查全量9包。**

### V2 安装命令对齐 ✅
- 修复前：手动列7个包名（漏 openpyxl + pytest）
- 修复后：`pip install -r requirements.txt -q`（自动读全量9包，与 start.sh L66 一致）

### V3 start.sh 无需改 ✅
- start.sh L66 已是 `pip install -r requirements.txt -q`，本次仅对齐 start.bat

### V4 start.bat 其余部分未受影响 ✅
- L1-46（Python路径检测）不变
- L76-170（端口检测/数据库/健康检查/浏览器打开）不变
- 中文显示完好无乱码

---

## 三、红线核验

| 红线项 | 状态 |
|---|---|
| 业务代码（app.py/modules/） | ✅ 未触碰（016 仅改运维脚本） |
| 数据库 | ✅ 未触碰 |
| 零代码约束 | ✅ 无新依赖 |
| 不回写 | ✅ start.bat 不涉及数据写入 |

---

## 四、关于 UX 痛点 #8 的说明

UX 评价痛点 #8 描述："需命令行启动，零代码用户对命令行有天然恐惧。建议提供双击启动的 .bat 脚本"。

**实际情况**：start.bat 早已存在（2026-07-18 创建），功能完善（Python检测→依赖检查→端口释放→健康检查→自动打开浏览器），已能实现"双击即启动"。本次016修复了依赖检查漏包的健壮性问题。

痛点 #8 的核心矛盾是"用户知晓度"——start.bat 存在但用户可能不知道。此项建议在后续用户使用说明更新中突出强调"双击 start.bat 启动"。

---

## 五、验收结论

**016 启动优化通过 PM 验收，建议监理直接关闭。**

理由：016 为运维脚本2行修正，不涉及业务代码/数据库/红线，改动极小且自验通过，无需 QA 独立验收。

> PM 签发日期：2026-08-03

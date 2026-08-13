# DEV-TASKS-20260803-017 数据源标注

## 角色定义
你是**开发人员**，负责按本任务书完成编码并自验。职责边界：只编码+自验，不负责正式验收（QA职责），不修改需求。

## 独立性原则
各角色独立不兼职：PM不兼架构/测试，架构师不编码/验收，开发不负责正式验收（只做自验），QA不依赖开发自验报告。

## 项目背景摘要
- 项目路径：`C:\Users\zlb19\Desktop\Qoder cn\stock_analyst`（含空格）
- 数据库路径：`C:\Users\zlb19\Desktop\Qoder cn\stock_analyst\stock_analyst.db`
- 技术栈：Python + Flask + SQLite + akshare + Jinja2 单页应用
- 零代码约束：无新 pip 依赖，双击 start.bat 即用

## 推荐执行信息
- **推荐模型**：qwen3.8 / glm5.2（并列优先）
- **窗口类型**：Chats（当前窗口）
- **执行模式**：智能体 agent（单代理）

## 需求来源
监理反馈：资金面数据与同花顺APP不一致（来源东方财富），港股价格与同花顺APP不一致（来源腾讯财经，前复权）。用户需要在前端清晰看到数据来源标注，避免误解。

## 改动范围（仅前端，零后端改动）

### 改动文件
仅 `templates/index.html` 一个文件。

### 改动点1：个股详情-资金面数据区域（约L2477）
在"资金面数据（最近10条）"标题行末尾，追加数据来源标注：
```
资金面数据（最近10条）<span style="font-size:12px;color:#999;font-weight:normal;">　来源：东方财富</span>
```
在资金面数据表格下方（`</tbody></table>` 之后），追加一行说明文字：
```html
<div style="font-size:12px;color:#999;margin-top:4px;">数据来源：东方财富资金流向接口，与同花顺/通达信等APP口径可能不同</div>
```

### 改动点2：个股详情-K线数据区域（约L2452）
在"K线数据（最近20条）"标题行末尾，追加数据来源标注：
```
K线数据（最近20条）<span style="font-size:12px;color:#999;font-weight:normal;">　来源：腾讯财经（前复权）</span>
```
在K线数据表格下方，追加一行说明文字：
```html
<div style="font-size:12px;color:#999;margin-top:4px;">数据来源：腾讯财经日K线接口（前复权），与同花顺/通达信等APP显示可能因复权方式不同而有差异</div>
```

### 改动点3：个股详情-基本面数据区域（约L2465）
在"基本面数据"标题行末尾，追加数据来源标注：
```
基本面数据<span style="font-size:12px;color:#999;font-weight:normal;">　来源：新浪财经/东方财富</span>
```

### 改动点4：个股详情-消息面数据区域（约L2490）
在"📰 消息面原始数据"标题行末尾，追加数据来源标注：
```
📰 消息面原始数据<span style="font-size:12px;color:#999;font-weight:normal;">　来源：东方财富新闻</span>
```

## 红线约束
- 不修改任何 Python 后端文件
- 不修改 API 接口返回结构
- 不新增 pip 依赖
- 仅修改 `templates/index.html` 的 HTML 展示层
- 015已完成的按钮精简/术语tooltip/预警入口等UX优化不可破坏

## 验收标准
1. 打开个股详情页，K线/资金面/基本面/消息面四个区域的标题旁均显示来源标注
2. K线和资金面表格下方显示口径差异说明文字
3. 标注文字样式为小号灰色（font-size:12px; color:#999），不干扰主要信息
4. 页面其他功能不受影响（按钮、弹窗、数据加载正常）

## 交付物
1. 修改后的 `templates/index.html`
2. 开发自验报告（含截图或文字描述验证结果）

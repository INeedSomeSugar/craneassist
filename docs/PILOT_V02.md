# v0.2 批次与人工复核

本轮把单份报告操作扩展成输入可冻结、运行可恢复、人工处理可追溯的批次流程。当前 `pilot-v02` 已准备5份报告、80个空白判断位置，尚未开始真实模型生成和人工评价。

## 操作命令

```powershell
cd D:\User\AI\craneassist
.\.venv\Scripts\python.exe -m craneassist.cli batch status --id pilot-v02
```

新的研究批次先冻结输入，再导出空白人工表，保留输入摘要：

```powershell
.\.venv\Scripts\python.exe -m craneassist.cli batch create --id another-pilot --cases TC003 TC008 TC001 TC016 TC028
.\.venv\Scripts\python.exe -m craneassist.cli batch export --id another-pilot --output reports/another-pilot-before-ai
```

需获得本次报告内容向 DeepSeek 发送的明确同意后，才能执行：

```powershell
.\.venv\Scripts\python.exe -m craneassist.cli batch run --id pilot-v02
.\.venv\Scripts\python.exe -m craneassist.cli batch export --id pilot-v02 --output reports/pilot-v02-assisted
```

恢复执行跳过已完成的报告；失败尝试不隐式重跑，显式 `--retry-failed` 会产生新的付费尝试。运行前核对来源和代码本快照，输入变化会拒绝继续。导出目标必须是新目录。

## 人工怎么填写

空白表保存正文、来源和定义，`label_value`、`support_type` 留空，不提供 AI 候选。若评价分类正确性，应由实际人员先完成独立判断，后续记录分歧和裁决，不能由AI代填。

辅助复核时在网页选择批次、事故和对应历史运行，填写实际复核人后开始计时。每类选择接受、修改或驳回，保留证据、意见和修改历史，结束会话。接受要求判断和逐字证据保持一致；驳回不等于把标签直接改成0。`0` 仍须满足原有证据及完整排除核心条件说明。

输出分别记录候选条数、已复核条数、三种处理数量和已结束会话历时。会话历时包含停留，不能直接换算效率提升；接受率也不等于分类准确率。专家验证、独立标注完成和训练评测准入始终不由脚本自动开放。

## 展示与简历

可表述为：“基于 DeepSeek 工具调用构建事故报告证据整理与 HFACS 辅助复核 Agent，冻结报告和代码本输入，校验逐字引用并离线重放工具轨迹；实现断点批次执行、候选接受／修改／驳回、复核历史与会话计时，以及独立空表和辅助复核稿导出。”

当前28项工程测试验证的是流程与状态约束，不能写成标注准确率或节约时间结果。

![v0.2 合成教学例界面与会话计时](images/craneassist-v02.png)

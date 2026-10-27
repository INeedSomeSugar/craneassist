# CraneAssist：证据驱动的 HFACS 辅助标注 Agent

将事故报告整理为可定位的事实与 A1—D4 四层16类候选，逐字核对引文，再交给人工接受、修改或驳回。原文、模型候选和人工判断分别保存。

面向大模型应用开发的独立 Python / FastAPI 项目，核心链路显式实现：

```text
来源与正文哈希 → 工具读取全部段落 → 提交16类候选 → 引用与结构检查
             → 人工复核及修改历史 → 可追溯导出
```

- 工具调用：读取代码本、分页阅读、检索段落、提交候选；校验错误返回模型继续修订。
- 原文不可变，来源、段落编号和 SHA-256 随候选保留；材料不足使用 `unknown`。
- 批次冻结输入和代码本，操作系统锁、恢复与显式失败重试保留全部尝试。
- 独立人工空表和辅助复核稿分别导出；复核有版本检查、修改历史及会话计时。
- CLI 与本地网页，默认仅监听 `127.0.0.1:8012`。

## 零密钥演示

公开仓库不包含真实事故正文、研究批次、个人复核记录或含正文的运行报告。可直接创建合成教学例体验完整流程：

```powershell
git clone https://github.com/INeedSomeSugar/craneassist.git
cd craneassist
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -X utf8 scripts/seed_demo.py
.\.venv\Scripts\python.exe -X utf8 -m craneassist.cli run --case DEMO --provider demo
.\.venv\Scripts\python.exe -m craneassist.cli serve --port 8012
```

打开 <http://127.0.0.1:8012>，选择 DEMO 和“合成演示”，查看段落、候选、引用及人工复核。此模式使用固定脚本，不调用模型。`seed_demo.py` 不读取论文目录，重复执行保留已有 DEMO。

使用自己的报告时，可在网页导入文本。选择 DeepSeek 会发送该报告正文并消耗 API 用量；调用前自行确认数据可发送。密钥仅来自进程环境或未提交的 `.env`；配置示例见 `.env.example`。

## 验证与设计边界

工程测试覆盖引文篡改、段落覆盖、状态完整性、复核冲突、批次恢复和导出。运行：

```powershell
.\scripts\check.ps1
```

v0.2 已完成28项工程测试。本地历史真实模型样例读取86段、生成16类候选，用量25,487 tokens，验证了调用与证据追溯流程；原始资料和报告保留本地。当前没有人工真值比较、分类准确率或效率提升结论。

逐字引用与结构通过不等于分类正确；会话历时包含停留。专家验证、独立标注和训练评测准入不会随复核条数自动开启。独立人工判断应在查看 AI 候选前由实际人员填写。

代码本保留已确认的 Zhou–Lei 四层16类名称和本课题操作边界，出处与摘要见 `src/craneassist/resources/codebook_provenance.json`；操作边界仍需人工审阅。完整批次操作见 [PILOT_V02.md](docs/PILOT_V02.md)，本地研究状态见 [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md)。PDF/OCR、联网取证和真实准确率评测尚未实现。

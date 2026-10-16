# 学习资料分析助手

本项目提供本地资料整理和按需在线分析。可以导入 PDF、DOCX、TXT、Markdown 文件，查看提取文字及页码/段落来源，并在配置 OpenAI 兼容服务后生成摘要、结构提纲、知识点和学习建议。工具不做 OCR、考试评分或答案核验。

## 启动

需要 Python 3.12。在 Windows PowerShell 的项目根目录运行：

```powershell
.\scripts\setup.ps1
.\scripts\start.ps1
```

页面默认在 `http://127.0.0.1:8501`。只导入和查看资料时无需配置模型。也可运行旧版 408 真题学习台：

```powershell
.\scripts\start_408.ps1
```

可选的 Windows 原生桌面版需要单独安装 PySide6。运行 `.\scripts\setup.ps1 -Desktop` 后，可双击项目根目录的 `启动桌面版.cmd`，或运行 `.\scripts\start_desktop.ps1`。桌面版与网页端共享通用资料数据库；详细说明见[桌面版指南](docs/desktop.md)。

旧版入口在 `legacy_exam_app.py`，默认使用端口 8502。真题、答案及其本机记录仍保存在 `data/exam.sqlite3`。

## 支持格式与边界

- PDF：仅提取可选择的文字；扫描件不做 OCR。部分页面没有文字时会显示页码警告。
- DOCX：按正文顺序提取段落及表格中的文字，保留可识别的标题层级，不还原表格布局；忽略删除修订。
- TXT、MD：支持 UTF-8、UTF-8 BOM、带 BOM 的 UTF-16 和 GB18030 编码，保留换行、段落和 Markdown 结构。
- 单个文件最大 20 MB、最多 600,000 个字符和 300 个来源片段。超限时请拆分文件。
- 每条报告提纲和知识点都带有来源片段编号，可在页面展开对应原文核对。来源引用合法不代表 AI 生成内容正确，请自行检查。
- PDF/DOCX 中的复杂公式、图像、表格和页面布局可能无法完整提取或理解，应对照原文核验。

分析会将所选资料中全部可提取文字分批发送到配置的模型服务，可能触发多次模型请求和计费。只有用户显式点击“生成分析报告”或“重新分析”才会联网；未填写完整设置时不会发出请求。请在分析前确认服务提供方和资料内容适合在线处理。

## 桌面资料整理

每份资料独立整理，不跨文件合并。桌面版提供两层模式，中间切换整理结果，右侧核对提取原文：

- **本地整理（无需模型）**：导入后自动识别 Markdown/DOCX 标题、明确的中文章节和数字小节；无可靠标题时按来源页/段落提供目录。可点击定位、导出 Markdown/JSON，不联网、不产生模型费用。不生成语义摘要或知识点。
- **智能整理（可选在线）**：显式点击“在线智能整理”后，调用配置的模型归纳摘要、分层大纲和知识点，可能产生费用。切换模式本身不会联网。
- 本地结果按已保存原文在内存重建，不覆盖智能报告；两种模式按当前显示结果分别导出。已有智能报告默认显示智能模式，无报告时默认本地模式。

智能整理能力包括：

- 大纲按章节分层，区分“原文标题”和“自动归纳”；点击大纲节点或来源链接定位原文。
- 知识点按章节展示说明，以及资料有依据时的定义、要点、公式和适用条件；没有依据的字段不强行补齐。
- 所有说明均为 AI 归纳，不是逐字引文；提示模型不补充外部知识，但仍需人工核对。
- 区分“已处理来源”和“已引用来源”，未引用来源可在整理详情中查看；这些计数均不证明语义完整。
- 报告与来源可导出为 Markdown / JSON；覆盖最终目标文件前再次确认。

旧报告可继续查看。已有智能报告不会自动升级；需要新字段时，请显式重新分析，可能产生模型费用。已有资料无需删除或重新导入即可使用本地目录；若要更新文件提取结果，则仍需先备份、删除旧记录后重新导入。详细操作和验收边界见[资料整理指南](docs/material-organization.md)。

## 在线模型设置和隐私

页面支持 OpenAI Chat Completions 兼容服务。可在侧栏临时填写地址、模型名和 API 密钥；也可以用环境变量提供默认值：

```powershell
$env:CSA_ANALYSIS_BASE_URL = "https://api.example.com/v1"
$env:CSA_ANALYSIS_MODEL = "your-model"
$env:CSA_ANALYSIS_API_KEY = "your-api-key"
```

密钥在页面中以密码框显示，只保存在当前 Streamlit 会话或环境变量中，不写入资料数据库或报告导出。资料和报告按文件分别保存在本机 `data/material.sqlite3`；通过 `CSA_DATA_DIR` 可更换数据目录。原先的 408 数据继续使用独立的 `data/exam.sqlite3`。

## 项目检查

```powershell
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
.\.venv\Scripts\python -m pytest
```

通用资料功能的覆盖范围、模拟服务验证方式和边界见[验证记录](docs/material-analysis-validation.md)。

桌面版提供暖纸色与墨蓝侧栏的原生界面；启动方式、离屏 Qt 与 Windows 启动验证结果及其人工验收限制见[桌面版指南与验证记录](docs/desktop.md)。

## 项目结构

```text
app.py                         通用资料分析页面
legacy_exam_app.py             原版 408 真题学习台
study_assistant/materials.py   本地文件导入和资料存储
study_assistant/analysis.py    OpenAI兼容服务与来源约束分析
study_assistant/local_organization.py  无模型的规则目录与来源导航
study_assistant/exams.py       旧版真题与学习记录服务
scripts/start.ps1              启动通用资料分析助手（8501）
scripts/start_408.ps1          启动旧版 408 学习台（8502）
data/material.sqlite3          运行后生成的本地资料和分析报告
data/exam.sqlite3              运行后生成的本地真题和学习记录
```

项目代码使用 [MIT License](LICENSE)。用户导入的资料由用户自行负责其使用权限。

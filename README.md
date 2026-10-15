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
- DOCX：提取文档正文段落。
- TXT、MD：支持 UTF-8、UTF-8 BOM 和 GB18030 编码。
- 单个文件最大 20 MB、最多 600,000 个字符和 300 个来源片段。超限时请拆分文件。
- 每条报告提纲和知识点都带有来源片段编号，可在页面展开对应原文核对。来源引用合法不代表 AI 生成内容正确，请自行检查。
- PDF/DOCX 中的复杂公式、图像、表格和页面布局可能无法完整提取或理解，应对照原文核验。

分析会将所选资料中全部可提取文字分批发送到配置的模型服务，可能触发多次模型请求和计费。只有用户显式点击“生成分析报告”或“重新分析”才会联网；未填写完整设置时不会发出请求。请在分析前确认服务提供方和资料内容适合在线处理。

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

桌面版启动方式、离屏 Qt 与 Windows 启动验证结果及其人工验收限制见[桌面版指南与验证记录](docs/desktop.md)。

## 项目结构

```text
app.py                         通用资料分析页面
legacy_exam_app.py             原版 408 真题学习台
study_assistant/materials.py   本地文件导入和资料存储
study_assistant/analysis.py    OpenAI兼容服务与来源约束分析
study_assistant/exams.py       旧版真题与学习记录服务
scripts/start.ps1              启动通用资料分析助手（8501）
scripts/start_408.ps1          启动旧版 408 学习台（8502）
data/material.sqlite3          运行后生成的本地资料和分析报告
data/exam.sqlite3              运行后生成的本地真题和学习记录
```

项目代码使用 [MIT License](LICENSE)。用户导入的资料由用户自行负责其使用权限。

# Windows 桌面版

桌面版使用 PySide6 原生窗口，和网页端调用相同的资料服务及 `material.sqlite3`。默认数据目录是项目的 `data`；设置 `CSA_DATA_DIR` 后，网页端与桌面端都会使用该目录。原有 408 真题库仍在独立的 `exam.sqlite3`。

## 安装和启动

需要 64 位 Python 3.12。打开 PowerShell 并进入项目目录后运行：

```powershell
.\scripts\setup.ps1 -Desktop
.\scripts\start_desktop.ps1
```

也可以双击项目根目录的 `启动桌面版.cmd`。脚本使用项目 `.venv` 中的 `pythonw.exe` 启动，不会保留命令行窗口。缺少项目环境或 PySide6 时，启动脚本会提示运行 `scripts\setup.ps1 -Desktop`。现有 `.venv` 若不是 Python 3.12，setup 会按原有行为停止并提示；请先按提示处理环境。

桌面依赖独立放在 `requirements-desktop.txt` 和 `desktop` extra 中，使用 `PySide6-Essentials` 提供的 Qt Core、Gui 和 Widgets，不安装未使用的 Qt add-on。仅使用网页端时继续运行原有 `scripts\setup.ps1` 和 `scripts\start.ps1` 即可。旧版 408 启动脚本不变。包的模块划分见 [Qt for Python 官方说明](https://doc.qt.io/qtforpython-6/package_details.html)。

## 使用、联网与数据

桌面端支持导入可选择文字的 PDF、DOCX、TXT 和 MD 文件，浏览提取文字与来源，生成摘要、提纲、知识点等报告，并导出报告。PDF 不做 OCR。桌面版和网页端会看到相同的本机资料及已保存报告。

界面采用暖纸色背景、深墨蓝资料栏、陶土色主操作和纸白内容卡片。图标与空库插画由 Qt 绘制，不依赖位图素材；紧凑布局保留资料、来源原文和报告的并排阅读区域。

分析只在用户点击分析后发送资料。分析可能分批发起多次在线模型请求并产生费用；发送前请核对模型服务及资料内容是否适合在线处理。API 地址、模型名和密钥可由当前窗口的配置填写，也可使用 `CSA_ANALYSIS_BASE_URL`、`CSA_ANALYSIS_MODEL`、`CSA_ANALYSIS_API_KEY` 环境变量。密钥仅保留在当前会话或环境中，不写入本地数据库。

报告来源用于帮助回查原文，不保证模型生成结论正确；请对照来源自行核验。模型服务需能连接，且其可用性、输出质量和费用由服务提供方决定。

取消分析或导入采用协作式处理：当前正在进行的导入或模型请求会先返回或超时，等待期间窗口仍可响应。关闭窗口时，应用会请求取消并等待任务结束后再退出。若导入中途收到取消，资料可能已经保存到本机；任务结束后请刷新资料列表确认。只有分析成功并由界面接受结果后，报告才会保存。

## 验证记录

在 Windows 项目虚拟环境安装 PySide6-Essentials 6.12.0 后，桌面相关 20 项通过：DesktopJobs 7 项、Qt UI 8 项、入口 3 项、布局 2 项；其中离屏 UI、入口和布局测试使用以下命令运行，13 项通过：

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
.\.venv\Scripts\python.exe -m pytest tests/test_desktop_entry.py tests/test_desktop_ui.py tests/test_desktop_layout.py
```

当前全量测试共 107 项通过，另有 1 条既有 jieba `pkg_resources` 弃用警告。新增紧凑布局回归在 900×600 窗口检查主操作与停止按钮的可达性、长文件名和多来源片段、空库与无报告引导、资料切换后的报告清空、当前资料卡的选中反馈，以及来源跳转和取消后的待机状态。入口帮助、缺少可选依赖提示和入口委派也单独覆盖。PowerShell 启动/安装脚本通过语法解析，项目打包元数据可正常读取。

Windows `pythonw.exe` 实际启动了桌面窗口，Qt Windows 平台报告 152 个系统字体。主窗口与模型设置窗口均通过程序调用 QWidget `grab()` 渲染并作了视觉检查；主窗口预览使用合成资料和模拟报告。Windows computer-use 截图两次因窗口捕获超时而失败，因此没有完成原生鼠标点击验收。Qt 自动交互测试通过，但不代表各种分辨率或多屏环境下的人工操作体验。模型集成测试使用模拟 HTTP 响应；真实模型语义、提供方可用性和费用未验证。

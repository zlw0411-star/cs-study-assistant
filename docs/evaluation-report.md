# 检索与回答评测报告

## 固定题集与口径

固定题目位于 `evaluation/questions.jsonl`，共30题：dev 20题、test 10题；25题可依据自编笔记回答，5题无答案。题目与gold来源标签由开发阶段AI协作预先编写标注，没有使用被测系统输出充当gold。gold文件只指向项目自编的操作系统教学笔记。test集包含1道必须覆盖两份笔记的题。dev/test使用固定ID，调参不得查看test结果后再回头修改题集。

Hit@5表示前5条结果中至少出现一个预先标注的gold文件时，该可答题记为命中。多来源覆盖率是需要多个gold文件的题目中，检索到的gold文件占比。每道题的答案、来源片段、引用编号和耗时保存在JSONL，便于逐条检查。自动指标不代表答案正确或引用能够支撑结论。

## 检索结果

2026-10-10在本机运行真实BGE语义模型（CPU，模型revision `7999e1d3359715c523056ef9478215996d62a620`）和BM25；未使用测试假向量。每个方法先执行预热查询，首轮启动时间单独记录。下面是一次小样本运行结果：

| Split | 方法 | 可答题命中 | 多来源覆盖 | 平均检索时间 | P95 | 启动预热 |
|---|---|---:|---:|---:|---:|---:|
| dev | BM25 | 20/20 (1.00) | 不适用 | 12.3 ms | 18.2 ms | 589.0 ms |
| dev | Dense | 19/20 (0.95) | 不适用 | 10.4 ms | 13.2 ms | 13.3 ms |
| dev | Hybrid | 20/20 (1.00) | 不适用 | 22.2 ms | 25.4 ms | 18.8 ms |
| test | BM25 | 5/5 (1.00) | 1/1 (1.00) | 14.8 ms | 22.1 ms | 560.5 ms |
| test | Dense | 5/5 (1.00) | 1/1 (1.00) | 11.7 ms | 16.1 ms | 12.3 ms |
| test | Hybrid | 5/5 (1.00) | 1/1 (1.00) | 26.6 ms | 34.3 ms | 20.4 ms |

逐题结果和环境信息见 [dev检索JSONL](../artifacts/dev-retrieval.jsonl)、[dev汇总](../artifacts/dev-retrieval.summary.json)、[test检索JSONL](../artifacts/test-retrieval.jsonl)、[test汇总](../artifacts/test-retrieval.summary.json)。当前环境为Python 3.12.14、Windows 10、NumPy 2.5.3、jieba 0.42.1、rank-bm25 0.2.2、sentence-transformers 5.7.0。索引建立预热约9秒，未计入逐题检索时间。

## 生成与人工核查

本报告的正式dev/test指标来自 `--skip-generation` 检索运行；没有测量生成成功率、有效引用率或模型拒答率。另有一次生成预热试跑在首条回答上发现JSON声明的引用与正文引用不一致，记录在 `artifacts/dev-full.*`，该试跑只触发了一次预热回答，没有对20道dev题逐题生成，不能当作完整生成评测。后来生成模型接口可用，但在用户将项目优先级切换到真题学习与分类后，正式生成评测被中止；本报告不把未完成任务算作结果。不可答题在test集三种检索方法下均找到至少一个片段，所以系统的“零检索结果拒答”率为0/5；这不等于模型无法拒答。dev集全是可答题，无法估计拒答表现。

答案正确性和引用支撑性尚未由人类评分。仅当人类逐条核对JSONL中的答案及引用片段后，才能填写人工分数；编号存在只说明引用格式有效，不等于证据充分。正式生成评测尚未完成，不填写生成指标。

可复现命令：

```powershell
.venv\Scripts\python.exe tools\evaluate.py --split dev --methods bm25,dense,hybrid --skip-generation --output artifacts\dev-retrieval.jsonl
.venv\Scripts\python.exe tools\evaluate.py --split test --methods bm25,dense,hybrid --skip-generation --output artifacts\test-retrieval.jsonl
```

Dense和Hybrid使用真实配置的BGE模型。请求的方法无法运行时，命令会保留汇总并返回非零状态；只有明确传入`--allow-partial`才允许部分成功返回0。`--skip-generation`用于只评测检索。测试用FakeEmbedder仅用于单元测试，绝不会进入此评测流程。

## 局限

笔记是十份短小的自编操作系统材料，不是官方大纲或真题语料。题目数量少且由AI协作编写，不能代表真实408试题分布，也不能外推到政治、英语二、数学二或整套真题。test仅有5道可答题，单次100%命中不构成稳定性能估计。dev没有无答案题，test中的无答案题仅5道。模型、提示词、硬件和检索切块设置变化都会影响结果。真题PDF库存仅统计提取性，不纳入gold，不自动抽题或生成答案。

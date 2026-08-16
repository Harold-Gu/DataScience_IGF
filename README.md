# IGF 全量数据管道（MVC 版）

IGF（Internet Governance Forum）2006–2025 会议数据的爬取、分类、JSON 提取、
验证、去噪、分析与 LLM 结构化抽取实验，按 MVC 分层。

## 快速开始

```powershell
.venv\Scripts\python.exe main.py selftest                  # 离线自测（~10s）
.venv\Scripts\python.exe main.py probe --url <URL>         # 单 URL 探测
.venv\Scripts\python.exe main.py scrape --years 2023 --limit 10   # 调试：单年小样
.venv\Scripts\python.exe main.py scrape                    # 正式全量
.venv\Scripts\python.exe main.py validate                  # 验证报告（自动选最新目录）
.venv\Scripts\python.exe main.py classify --classify-dir igf_full_xxx
.venv\Scripts\python.exe main.py extract --classify-dir igf_classified_xxx
```

旧命令入口 `scrape_igf.py`（及 `report_scrape.py`、`denoise_json.py`、
`recover_transcripts.py`、`analyze_corpus.py`、`_test_dl.py`）均为兼容壳，
原有命令行用法不变。

## 结构

- `igf_pipeline/models/`：下载引擎、DOM、深度爬取、分类、提取、验证、去噪、
  逐字记录恢复、语料分析（Model）
- `igf_pipeline/views/`：控制台与验证报告渲染（View）
- `igf_pipeline/controllers/`：抓取步骤、流程编排、验证编排、LLM 实验调度（Controller）
- `igf_pipeline/cli.py`：统一 argparse 入口
- `tests/test_download.py`：40 项离线自测（方法验证）
- `llm_extract_benchmark/`：LLM 提取/模型选择实验（金标准、结果、设计文档）

完整说明见 `docs/EXPERIMENT_DOC.md`。

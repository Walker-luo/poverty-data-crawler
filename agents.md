# 项目代理交接说明

本文件用于让全新的 AI 对话快速接手本项目。开始工作前先阅读本文件，再按任务阅读对应脚本和 README。不要仅根据历史对话猜测当前实现，命令行参数以脚本的 `--help` 和当前代码为准。

## 工作方式

- 项目根目录：`/Users/luo/Desktop/poverty`
- 文件修改直接在该工作区完成，不需要打开或操作 VS Code。
- 修改前先阅读相关代码、README 和 `git status`，保留用户已有的未提交修改。
- 手工编辑使用补丁方式，避免覆盖整个文件；不回滚与当前任务无关的改动。
- 数据量可能达到数千至十万条，新增逻辑应优先采用流式读写、增量保存和目录索引，避免无必要地一次性加载全部正文。
- 不要把 API key、代理凭据或 `.env` 内容写入 README、日志或提交文件；优先使用环境变量或 CLI 参数。
- 修改 Python 后至少运行 `python -m py_compile <修改的脚本>`；文档或代码修改后运行 `git diff --check`。
- 用户的运行环境常用 Conda 环境 `crawler`，服务器可能是 Windows，代码需兼容 macOS/Linux/Windows 路径。

## 项目目标

项目采集中国贫困治理、精准扶贫、脱贫攻坚、乡村振兴及全球减贫相关语料，用于数据库导入、知识图谱和主题建模。现有四条主要数据流程：

1. 中文新闻：搜索、正文下载、LLM 清洗、生成数据库 CSV。
2. 国外学术文献：OpenAlex/Crossref 元数据、引文扩展、OA 全文下载、CSV 导出。
3. 国际组织报告：多机构 DSpace 元数据、PDF/TXT 下载、CSV 导出。
4. 国际多语种新闻：Bing/Google RSS 等搜索、正文下载、LLM 清洗。

## 主要入口

| 文件 | 用途 |
|---|---|
| `main.py` | 中文新闻统一入口：crawl/download/clean/pipeline/gen-csv |
| `spiders/bing_news_spider.py` | 中文 Bing 新闻搜索与 maximize 策略 |
| `utils/news_md_downloader.py` | 中文新闻正文下载、失败记录、断点跳过 |
| `utils/llm_cleaner.py` | 中文新闻 DeepSeek 批量清洗和 `db_import.csv` |
| `english_news_collector.py` | 国际多语种新闻搜索与正文下载 |
| `english_news_cleaner.py` | 国际新闻 DeepSeek 清洗 |
| `academic_collector.py` | OpenAlex/Crossref 学术元数据、引文扩展、全文下载 |
| `academic_export_csv.py` | 学术数据导出数据库 CSV |
| `report_collector.py` | 国际组织报告采集与全文下载 |
| `report_export_csv.py` | 报告数据导出数据库 CSV |
| `check_csv_coverage.py` | 检查中文清洗文件是否被 CSV 收录并修复部分 frontmatter |
| `README_news.md` | 中文新闻完整说明 |
| `README_en.md` | 学术、报告、国际新闻完整说明 |
| `TODO.md` | 已完成能力、遗留事项和后续方向 |

旧文档或对话中可能出现 `collect_english.py`，当前学术采集入口是 `academic_collector.py`。

## 数据目录

所有运行结果位于 `data/processed/`，大部分被 `.gitignore` 忽略：

```text
data/processed/
├── news/{run_id}/                  # 中文新闻
│   ├── articles/                   # 原始正文 Markdown
│   ├── articles/clean/             # LLM 清洗结果及 db_import.csv
│   └── fail.log
├── news/en/{run_id}/               # 国际多语种新闻
│   ├── news.json
│   ├── news.csv
│   ├── articles/
│   ├── download_log.json
│   ├── fail.log
│   └── summary.md
├── academic/{run_id}/              # 学术文献
│   ├── works.json
│   ├── works.csv
│   ├── fulltext/
│   └── summary.md
└── report/{run_id}/                # 国际组织报告
    ├── works.json
    ├── works.csv
    ├── fulltext/
    └── summary.md
```

`run_id` 通常是 `YYYYMMDD_HHMMSS`。涉及已有数据时，优先要求或使用明确的 `--run-id`，不要误选最新目录。`--run-id` 通常表示复用该目录；具体是否继续采集或只下载，以对应脚本实现为准。

## 常用命令

安装依赖：

```bash
python -m pip install -r requirements.txt
```

中文新闻全流程：

```bash
python main.py --pipeline --source news --strategy maximize --engine bing
python main.py --download --run-id <RUN_ID>
python main.py --clean --run-id <RUN_ID> --workers 4
python main.py --gen-csv --run-id <RUN_ID>
python main.py --show-fails --run-id <RUN_ID>
```

学术文献：

```bash
python academic_collector.py --source openalex --min-relevance 100
python academic_collector.py --source crossref --max-pages 5
python academic_collector.py --run-id <RUN_ID> --expand-references --expand-limit 2000
python academic_collector.py --run-id <RUN_ID> --fulltext pdf --fulltext-limit 3
python academic_export_csv.py --run-id <RUN_ID>
```

国际组织报告：

```bash
python report_collector.py --check-orgs
python report_collector.py --org WHO UNESCAP ECLAC UNECA
python report_collector.py --run-id <RUN_ID> --download
python report_export_csv.py --run-id <RUN_ID>
```

国际新闻默认全量采集；无参数时使用全部默认范围、语言、关键词和年份：

```bash
python english_news_collector.py
python english_news_collector.py --download
python english_news_collector.py --download-only --run-id <RUN_ID>
python english_news_collector.py --download-only --run-id <RUN_ID> --limit 10
python english_news_cleaner.py --run-id <RUN_ID> --limit 5
```

Windows 服务器需走本机 7897 代理时：

```bash
python english_news_collector.py --use-proxy --proxy-port 7897
python english_news_collector.py --download-only --run-id <RUN_ID> --use-proxy --proxy-port 7897
```

## 已实现的重要行为

### 中文新闻

- Bing maximize 包含按年份的宽泛关键词搜索、近期补齐和官媒定向搜索。
- 下载与清洗会跳过已有成功文件，支持中断后续跑。
- 下载和清洗失败写入 run 下的 `fail.log`。
- LLM 默认批量处理，并支持并发、批量失败回退逐篇、token/费用统计。
- `--gen-csv` 只解析已经清洗的 Markdown，不调用大模型。
- YAML frontmatter 损坏可能导致 clean 文件不进入 CSV，可用 `check_csv_coverage.py` 检查。

### 学术文献

- OpenAlex 字段丰富但 API 有额度；Crossref 可补充元数据但摘要覆盖较差。
- DOI 优先用于去重，没有 DOI 时使用稳定 ID/标题等后备键。
- OpenAlex API key 支持多 key 轮换；不要在代码或文档中暴露真实 key。
- `--expand-references` 从 OpenAlex `referenced_works` 扩展，并按批次请求、定期落盘。
- 全文下载记录应支持增量跳过；PDF/XML 的优先级由 `--fulltext` 决定。

### 国际组织报告

- 主要适配 DSpace REST API。可用机构受网络、SSL、代理和机构端接口变化影响，先运行 `--check-orgs`。
- 全文默认 PDF 优先，PDF 不可得时 TXT 兜底；除非显式指定，不应在已有 PDF 后重复下载 TXT。
- 下载记录和失败日志应实时写入，不能只在全部任务结束时生成。
- 导出 CSV 的 `*文件名` 使用元数据 `handle`，不使用报告标题；`文件地址` 指向实际全文，不存在时填 `None`。

### 国际新闻

- 默认 `scope=all`，覆盖中国和全球减贫主题，并支持多语种关键词。
- Bing HTML 结果不足时可使用 Bing RSS 和 Google News RSS 兜底。
- 数据固定存入 `data/processed/news/en/{run_id}/`，旧目录 `data/processed/env_news/` 仅作兼容。
- `download_log.json` 是正文下载状态的主要依据：成功项跳过，失败项可在续跑时重试；`fail.log` 用于人工诊断，不应作为唯一状态源。
- 正文下载默认正常校验证书；仅遇到 `SSLError` 时，对当前 URL 使用 `verify=False` 再尝试一次。403、404、429、验证码和 JS 页面仍应按失败记录。
- 当前正文下载逻辑会尝试页面正文和候选 canonical/AMP/`og:url` 页面，以减少“正文过短”。

## 数据库 CSV 约定

学术和报告导出 CSV 对齐数据库批量导入模板，主要字段为：

```text
*文件名, *标题, 描述 / 内容, *分类, *类型, *国家, 地区,
关键词, 发展阶段, *话语类型, 发布日期, 来源, 原始URL, 文件地址
```

- 学术 `*文件名` 使用 OpenAlex ID，缺失时使用 DOI。
- 报告 `*文件名` 使用 `handle`。
- `文件地址` 必须来自实际存在的 `fulltext/` 文件，没有则为 `None`。
- 导出器为大数据设计：全文目录只建立一次索引，并逐条写 CSV。
- 精简字段说明见 `data/processed/csv_import_brief.md`（该文件被允许纳入 Git）。

## 网络与失败处理原则

- 服务器结果接近 0 时，先用脚本的预检/探测参数确认搜索页、最终 URL、状态码和解析数量，再判断是否是代理、地域重定向或页面结构变化。
- 代理参数必须同时应用到搜索、RSS、重定向解析和正文请求；Windows 的 `127.0.0.1:7897` 只有代理程序实际监听时才有效。
- 对超时、SSL EOF、连接重置、分块响应不完整等瞬时错误，应有限次数重试并采用退避；单条失败不能终止整个批次。
- 写下载文件时先写临时文件，校验类型和最小长度后再替换目标文件，避免中断留下被误判为成功的残缺文件。
- 日志应包含总数、已处理、成功、失败、跳过/未下载，并实时同步到终端和 run 内日志。
- 不要用关闭全局证书校验来掩盖网络问题；SSL 降级必须局部、可追踪。

## 当前注意事项

- 工作区可能已有未提交修改。接手时先运行 `git status --short` 和 `git diff -- <相关文件>`。
- 最近对 `english_news_collector.py` 的修改为：正文请求遇到 SSL 证书错误时，仅针对当前 URL 自动进行一次不校验证书的降级重试；相关说明同步在 `README_en.md`。
- 真实服务器网络与本机不同。无法在本地复现时，仍需保证异常被捕获、失败原因落盘、任务可继续，并给出小批量服务器验证命令。
- `TODO.md` 中有些状态可能落后于当前代码；判断功能是否完成时以实现和实际输出为准，并在修改功能后同步 README/TODO。

## 新对话接手步骤

1. 阅读本文件以及任务对应的 `README_news.md` 或 `README_en.md`。
2. 运行 `git status --short`，确认已有修改，不覆盖用户工作。
3. 查看目标脚本的 `--help`、入口函数和相关日志/状态文件实现。
4. 若用户提供服务器日志，以该日志为事实，不用本地数据推翻服务器现象。
5. 直接在工作区实施修改，并同步对应 README。
6. 做语法检查、针对性测试和 `git diff --check`，明确说明未能执行的真实网络测试。

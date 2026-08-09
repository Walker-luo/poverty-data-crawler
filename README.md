# 扶贫数据采集系统

## 项目目标

建立一个**大规模、结构化**的中国扶贫/脱贫攻坚相关数据数据库：
- 📰 官方媒体新闻报道（覆盖 1979-2026，六个扶贫发展阶段）
- 🔍 按关键词搜索，支持百度/Bing 双引擎
- 📥 下载原文为 .md 格式，LLM 清洗统一格式化
- 📊 自动生成统计报告，数据按运行时间戳分目录保存

## 快速开始

```bash
# 一行命令：爬取 + 下载 + 清洗
python main.py --pipeline --source news --strategy maximize --engine bing

# 小批量测试
python main.py --pipeline --source news --strategy maximize --engine bing --limit 5
```

## 环境准备

```bash
pip install -r requirements.txt
```

## 命令行参数详解

### `--source` 数据来源

| 值 | 说明 |
|------|------|
| `news` | **推荐**，新闻搜索爬虫（百度/Bing） |
| `gov` | 政府网站爬虫（数据量少，不推荐） |
| `all` | 同时运行 gov + news |

默认 `all`，但 gov 爬虫产出极少，建议显式指定 `--source news`。

### `--strategy` 采集策略

| 值 | 说明 | 适用场景 |
|------|------|------|
| `keyword` | 关键词搜索 × N 页 | 快速测试、日常采样 |
| `site` | 官媒站点 `site:domain` 定向搜索 | 特定媒体专题（仅百度） |
| `maximize` | **全量采集**：逐年分区 + 补齐 + 官媒点名 | **建数据库用** |

默认 `keyword`。建库必须用 `maximize`。

### `--engine` 搜索引擎

| 值 | 反爬 | 稳定性 | 推荐 |
|------|------|------|:--:|
| `bing` | 无封IP风险 | ✅ 稳定 | **推荐** |
| `baidu` | IP 级封禁 | ⚠️ 大概率不可用 | 不推荐 |

默认 `baidu`，建议显式指定 `--engine bing`。

### `--max-pages` 翻页深度

控制每个查询翻多少页。默认 `5`。

| 值 | 说明 |
|------|------|
| `1-2` | 快速测试 |
| `5` | **推荐**，均衡覆盖与耗时 |
| `8-10` | 全量建库，耗时更长 |

### `--filter` 媒体来源过滤

| 值 | 说明 |
|------|------|
| `official` | **推荐**，仅保留官方媒体（党媒/央媒/政府网站），通过来源名+域名+别名三重匹配 |
| `all` | 不过滤，含商业媒体 |
| `commercial` | 仅商业/民间媒体 |

### `--years` 限定年份

空格分隔，仅在 `maximize` 策略下生效。不指定则覆盖全部 1979-2026。

```bash
--years 2023                  # 单年
--years 2020 2021 2022        # 多年
```

### `--verbose` 详细日志

开启 DEBUG 级别日志，输出每次 HTTP 请求详情。调试网络问题时使用。

### `--download` 下载正文

从已爬取的 URL 下载文章正文，保存为 `.md` 文件。不运行爬虫。

```bash
python main.py --download                     # 下载最新 run
python main.py --download --run-id ID         # 指定 run
python main.py --download --limit 5           # 只下载 5 篇测试
```

### `--clean` LLM 清洗

调用 DeepSeek API 将下载的 `.md` 清洗为统一模板格式。需先运行爬虫或在 [utils/llm_cleaner.py](utils/llm_cleaner.py) 第 43 行填入 API Key。

```bash
python main.py --clean --limit 3              # 测试 3 篇
python main.py --clean                        # 全量清洗
python main.py --clean --run-id ID            # 指定 run
```

> **批量清洗节省 Token**：默认每 5 篇文章合并为一次 API 请求，System Prompt 只传一次。
> 1000 篇文章从 1000 次请求 → 200 次，省 ~120 万 token。可通过修改 `DEFAULT_BATCH_SIZE` 调整每批篇数。

### `--pipeline` 流水线

一行命令完成 爬取 → 下载 → 清洗 全流程。

```bash
python main.py --pipeline --source news --strategy maximize --engine bing
python main.py --pipeline --source news --strategy maximize --engine bing --limit 5  # 测试
```

### `--run-id` 指定数据目录

不指定时自动选择最新的 `run_id`。适用于 `--download` 和 `--clean` 模式。

### `--delay` 请求间隔

`--download` 模式下的请求间隔秒数，默认 `2.0`。降低可加速但可能被限流。

### `--limit` 限制数量

限制 `--download` 或 `--clean` 模式下的处理数量，测试用。

### `--show-fails` 查看失败日志

查看下载和清洗过程中的失败记录，方便排查问题。

```bash
python main.py --show-fails                    # 查看最新 run 的 fail.log
python main.py --show-fails --run-id ID        # 查看指定 run 的 fail.log
```

输出示例：

```
📄 失败日志: data/processed/news/20260808_120000/fail.log
   下载失败: 42 条 | 清洗失败: 3 条
============================================================
[2026-08-08 12:30:12] step=download | id=a1b2c3d4 | title=... | reason=请求超时
[2026-08-08 12:30:45] step=download | id=e5f6g7h8 | title=... | reason=页面内容过短(45字符)
[2026-08-08 15:05:10] step=clean | id=x9y0z1 | title=... | reason=API失败(3次重试)
============================================================
```

fail.log 文件位于 `data/processed/news/{run_id}/fail.log`，下载和清洗共享同一文件，追加写入不覆盖。

### `--fetch-content` / `--output-db`

旧版功能，已不推荐使用。`--output-db` 输出 MongoDB 导入格式（resource.json）。

## 常用命令

### 一行命令（推荐）

```bash
python main.py --pipeline --source news --strategy maximize --engine bing
```

自动完成 爬取 → 下载正文 → LLM 清洗。

### 分步执行

```bash
# ① 爬取
python main.py --source news --strategy maximize --engine bing

# ② 下载正文
python main.py --download

# ③ LLM 清洗（先测试，后全量）
python main.py --clean --limit 3    # 测试 3 篇
python main.py --clean               # 全量清洗
```

## 数据规模预估

| 命令 | 请求数 | 耗时 | 去重后产出 |
|------|--------|------|-----------|
| `keyword --max-pages 1` | 4 | 30 秒 | 20-40 条 |
| `keyword --max-pages 10` | 60 | 5-8 分钟 | 200-400 条 |
| `maximize --years 2022 2023 --max-pages 5` | ~150 | 12-18 分钟 | 600-1000 条 |
| `maximize --max-pages 5` (全年代) | ~800 | 60-80 分钟 | 2000-4000 条 |
| `maximize --max-pages 8` (全年代) | ~1200 | 90-120 分钟 | 3000-5000 条 |

> **注意**：实际产出取决于官方媒体过滤的严格程度。`--filter all` 可获得 2-3 倍数据量，但包含非官方媒体。

## maximize 策略详解

### 百度引擎 (三阶段)

百度需要通过时间分区绕过翻页限制和反爬：

```
Phase 1: 15 关键词 × 48 年逐年分区（bt/et 时间戳）
Phase 2: 关键词通用搜索 (补齐)
Phase 3: Top 10 官媒 site: 定向 (兜底)
```

### Bing 引擎 (三阶段，推荐)

三阶段，覆盖 15 关键词 × 48 年逐年分区：

```
Phase 1 (主力): 15 关键词 × 48 年逐年搜索
        → Bing 新闻不支持年份过滤，靠翻页深度覆盖
        → 自动去重，统计官媒占比

Phase 2 (补齐): 关键词 + 最近2年搜索
        → 以年份字符串拼入搜索词捕获遗漏
        → 翻页深度减半，控制请求量

Phase 3 (兜底): 核心官媒点名搜索
        → 前12个中央级官媒 × 前2个高频关键词
        → Bing 不支持 site: 语法，改用"来源名+关键词"组合
        → 只翻第1页，补齐官媒覆盖
```

## 数据字段

### 爬虫原始字段 (news.csv / news.json)

每条新闻记录包含：

| 字段 | 说明 | 百度 | Bing |
|------|------|:--:|:---:|
| `title` | 文章标题 (已去 HTML) | ✅ | ✅ |
| `url` | 原文链接 | ✅ | ✅ |
| `publishDate` | 发布日期 (URL提取 > 绝对日期 > 相对日期推算) | ✅ | ✅ |
| `summary` | 文章摘要 (前 500 字) | ✅ | ✅ |
| `source` | 来源媒体名称 | ✅ | ✅ |
| `is_official` | 是否官方媒体 | ✅ | ✅ |
| `keywords` | 标题/摘要命中的关键词 | ✅ | ✅ |
| `search_mode` | 采集阶段标记 | `year_partition/keyword/site` | `keyword/catchup/source` |
| `crawl_time` | 数据采集时间 | ✅ | ✅ |

### Resource Schema 字段 (resource.json，需 `--output-db`)

映射到 anti-poverty-server 的 MongoDB Resource 模型，在爬虫字段基础上自动推断：

| 字段 | 推断规则 |
|------|----------|
| `type` | 固定 `["text"]` (新闻均为文本) |
| `category` | 标题含"通知/意见/方案"→`policy`，含"案例"→`cases`，默认 `news` |
| `country` | 默认 `"china"` |
| `region` | 默认 `"asia"` |
| `contentLanguage` | 固定 `["中文"]` |
| `discourseType` | 官媒→`institutional`，学术→`academic`，商业→`civilian` |
| `stage` | 标题/摘要命中 6 个扶贫阶段关键词 (traditional→rural) |
| `keywords` | 标题/摘要命中 15 个关键词库 |
| `publishDate` | `pub_date` > 绝对日期解析 > 相对日期推算 |
| `status` | 官媒→`published`，其他→`pending` |
| `institution` | 来源名称 (匹配官方媒体标准名称) |
| `originalUrl` | 原始爬取 URL |

## 输出文件

每次运行按 `run_id` (时间戳) 保存到独立子目录。

### 基础输出 (始终生成)

```
data/processed/news/{run_id}/
├── news.csv              ← 爬虫原始字段
├── news.json             ← 同上 JSON 格式
├── summary.md            ← 统计报告
├── articles/             ← 下载的原文 .md (--download)
│   ├── a1b2c3d4.md
│   ├── e5f6g7h8.md
│   ├── db_import.csv     ← 数据库批量导入索引
│   └── clean/            ← LLM 清洗后 .md (--clean)
│       ├── a1b2c3d4.md   ← 统一模板格式
│       └── e5f6g7h8.md
└── resource.json         ← MongoDB 导入 (--output-db)
```

### MD 模板格式

LLM 清洗后的每篇文章采用统一格式：

```markdown
---
id: "run_id/article_id"
title: "文章标题"
source: "来源名称"
source_url: "https://..."
publish_date: "YYYY-MM-DD"
category: "news"
type: "text"
country: "china"
discourse_type: "institutional"
keywords: ["扶贫", "脱贫攻坚"]
development_stage: "precision"
summary: "LLM 生成的100字摘要"
---

正文段落内容...
```

- YAML frontmatter → 机器可解析，对应数据库字段
- 正文 → 纯段落，无广告/导航/推荐阅读

## 反爬机制

仅百度引擎需要。Bing 引擎无需反爬。

| 机制 | 说明 |
|------|------|
| UA 轮换 | 每次请求从 4 个 UA 中轮流选用 |
| 拦截检测 | 检测页面是否为 "百度安全验证"，若是自动退避 |
| 自动退避 | 触达验证码 → 换 Session → 等 30-60s → 重试 |
| 冷词跳过 | 关键词连续 3 年无结果 → 跳过更早年份，省 30-40% 请求 |

```python
# 调参: config/settings.py
CRAWL_CONFIG = {
    "request_delay": 5,    # 请求间隔秒数，5-10 可降低被封概率
    "max_retries": 3,      # 单请求最大重试次数
}
```

## 官方媒体覆盖

### 来源判定 (三重匹配)

1. **来源名称精确匹配** — `source_name` 在官方媒体名称/别名表中
2. **URL 域名匹配** — 文章 URL 包含官方媒体域名
3. **来源名称模糊匹配** — 来源名包含媒体名 (如 "新华社新媒体" ⊃ "新华社")

### 已收录官方媒体 (48 个)

| 级别 | 媒体 |
|------|------|
| 中央通讯社 | 新华网、人民网、央视网、中国新闻网、中国日报网 |
| 中央广播 | 央广网 (中央人民广播电台) |
| 中央报刊 | 光明网、中国经济网、中国青年网、中国网、环球网、求是网、中国军网、经济参考报、中国经济导报 |
| 中央专业 | 法制网、中国证券网、中国农网、中国教育新闻网、中国质量新闻网、中青在线 |
| 政府机构 | 中国政府网、人民政协网、中央纪委、国家发改委 |
| 省级党媒 | 北京日报、上观新闻(上海)、南方网(广东)、中国甘肃网、华声在线(湖南)、大众网(山东)、云南网、东南网(福建)、四川在线、荆楚网(湖北)、南海网(海南)、深圳新闻网、齐鲁网、大江网(江西)、浙江在线、西部网(陕西)、中国江苏网、广西新闻网、千龙网(北京)、中国西藏新闻网、东北网(黑龙江)、中国宁波网、北青网 |

### 采集关键词 (15个)

| 类别 | 关键词 |
|------|--------|
| 核心概念 | `扶贫` `脱贫攻坚` `精准扶贫` `乡村振兴` |
| 政策机制 | `建档立卡` `对口帮扶` `两不愁三保障` `防止返贫` |
| 具体领域 | `产业扶贫` `教育扶贫` `健康扶贫` `易地搬迁` |
| 对象 | `贫困县` `贫困村` `摘帽` |

## 项目结构

```
poverty/
├── main.py                   # 主入口 (爬取/下载/清洗)
├── requirements.txt          # Python 依赖
├── config/
│   └── settings.py           # 媒体分类(48个) / 关键词 / 采集规则
├── spiders/
│   ├── base_spider.py        # 基础爬虫 (UA轮换/反爬检测/自适应delay)
│   ├── news_spider.py        # 百度新闻爬虫
│   ├── bing_news_spider.py   # Bing 新闻爬虫 (预检/自适应地区/三阶段)
│   └── gov_spider.py         # 政府网站爬虫
├── utils/
│   ├── data_processor.py     # 数据清洗/去重/日期标准化/关键词提取
│   ├── storage.py            # CSV/JSON 存储 + 统计报告生成
│   ├── news_md_downloader.py # 正文下载器 (URL → .md)
│   ├── llm_cleaner.py        # LLM 清洗器 (DeepSeek API → 统一模板)
│   ├── content_fetcher.py    # 全文抓取工具 (兼容旧版)
│   └── resource_mapper.py    # Resource Schema 映射 (MongoDB 导入)
├── data/
│   ├── raw/                  # 原始数据
│   ├── processed/            # 清洗后数据 (按 run_id 分目录)
│   │   └── news/{run_id}/
│   │       ├── news.csv
│   │       ├── articles/     # 下载的 .md
│   │       │   ├── *.md
│   │       │   ├── db_import.csv
│   │       │   └── clean/   # LLM 清洗后
│   │       └── summary.md
│   └── article_template.md   # 统一 MD 模板
└── logs/                     # 运行日志
```

## tmux 使用

```bash
tmux new -s crawler           # 新建会话
conda activate crawler        # 激活环境
python main.py ...            # 启动采集
# Ctrl+B, D                   # 断开 (爬虫继续跑)
tmux attach -t crawler        # 重连查看进度
tmux ls                       # 查看所有会话
tmux kill-session -t crawler  # 结束会话
```

鼠标滚轮可直接翻看终端历史。完整配置见 `~/.tmux.conf`。

## 故障排查

### 百度一直触发反爬

**现象**：日志刷屏 `⚠️ 触发百度反爬`

**解决**：切 Bing `--engine bing`

### 数据量远低于预期

**检查项**：
1. 是否用了百度且被拦？→ `grep "触发百度反爬" logs/crawler.log`
2. 过滤太严？→ 试试 `--filter all`
3. 翻页深度不够？→ 加大 `--max-pages`
4. 日志中是否有 `跳过 xxx (连续 3 年无结果)` 的冷词跳过信息？

### 日志排查

```bash
grep "触发百度反爬" logs/crawler.log | wc -l   # 拦截次数
grep "跳过" logs/crawler.log                    # 冷词跳过
grep "全量采集完成\|累计" logs/crawler.log      # 各阶段统计
```

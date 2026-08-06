# 扶贫数据采集系统

## 项目目标

建立一个**大规模、结构化**的中国扶贫/脱贫攻坚相关数据数据库：
- 📰 官方媒体新闻报道（2013 精准扶贫至今）
- 🔍 按关键词 + 年份分区搜索，突破翻页限制
- 📊 自动生成统计报告，数据按运行时间戳分目录保存

## 环境准备

```bash
conda activate crawler (python=3.11)
pip install -r requirements.txt
```

## 命令行参数

### 全部参数

| 参数 | 可选值 | 默认值 | 说明 |
|------|--------|--------|------|
| `--source` | `news`, `gov`, `all` | `all` | 数据来源 |
| `--strategy` | `keyword`, `site`, `maximize` | `keyword` | 采集策略 (见下方详解) |
| `--engine` | `baidu`, `bing` | `baidu` | 搜索引擎 |
| `--max-pages` | 整数 | 5 | 翻页深度，越大采集越多 |
| `--filter` | `official`, `all`, `commercial` | `official` | 媒体来源过滤 |
| `--years` | 空格分隔的年份 | 无 | 限定年份，如 `--years 2022 2023` |
| `--fetch-content` | flag | 关闭 | 从搜索结果 URL 抓取全文正文（较慢） |
| `--output-db` | flag | 关闭 | 额外输出 MongoDB 导入格式 |

### `--strategy` 策略说明

| 值 | 说明 | 适用场景 |
|------|------|------|
| `keyword` | 关键词搜索 × N 页 | 快速测试、日常采样 |
| `site` | 对官媒站点做 `site:domain` 定向搜索 | 特定媒体专题 (仅百度) |
| `maximize` | 全量采集：逐年分区 + 补齐搜索 | **建数据库用** |

### `--engine` 引擎对比

| | 百度 `baidu` | Bing `bing` |
|------|------|------|
| 反爬程度 | IP 级封禁，极易被拦 | 无限制 |
| 可用性 | ⚠️ 大概率不可用 | ✅ 稳定可用 |
| 推荐 | 不推荐 | **推荐** |

### `--filter` 过滤说明

- `official`：仅保留官方媒体 (党媒、央媒、政府网站)，通过来源名+域名+别名三重匹配
- `all`：不过滤，保留全部来源
- `commercial`：仅保留民间/商业媒体

## 常用命令

```bash
# 快速测试 (4 请求，30 秒)
python main.py --source news --strategy keyword --max-pages 1 --engine bing

# 百度可用性测试 (测完就知道 IP 是否还被封)
python main.py --source news --strategy keyword --max-pages 1 --engine baidu

# 中等采样 (约 50-100 次请求，5-10 分钟)
python main.py --source news --strategy keyword --max-pages 10 --engine bing

# 限定年份全量采集 (精确控制)
python main.py --source news --strategy maximize --years 2022 2023 --max-pages 5 --engine bing

# 全量采集 全部14年 (挂 tmux 跑)
python main.py --source news --strategy maximize --max-pages 5 --engine bing

# 输出 MongoDB 导入格式 (resource.json + import.mongosh.js)
python main.py --source news --strategy keyword --max-pages 5 --engine bing --output-db

# 建库采集 + 全文抓取 (较慢，适合首次建库)
python main.py --source news --strategy maximize --max-pages 5 --engine bing --fetch-content --output-db
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
Phase 1: 15 关键词 × 14 年逐年分区搜索 (主力)
Phase 2: 关键词通用搜索 (补齐)
Phase 3: Top 10 官媒 site: 定向 (兜底)
```

### Bing 引擎 (三阶段，推荐)

Bing 通过连通性预检后按三阶段采集：

```
Phase 1 (主力): 15 关键词纯文本搜索 + 深翻页
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
data/processed/news/20260805_174923/
├── news.csv          ← 爬虫原始字段 (Excel 可打开)
├── news.json         ← 同上 JSON 格式
└── summary.md         ← 统计报告
```

### MongoDB 导入输出 (需 `--output-db`)

```
data/processed/news/20260805_174923/
├── ...
├── resource.json      ← Resource Schema 格式，可直接 mongoimport
└── import.mongosh.js  ← mongosh 导入脚本 (基于 originalUrl 做 upsert)
```

导入方式：
```bash
cd data/processed/news/20260805_174923/
mongosh mongodb://localhost:27017/poverty-db --file import.mongosh.js
```

### 统计报告内容

- 📊 来源分布 (每个媒体贡献条数)
- 📅 日期分布 (数据时间覆盖范围)
- 🔑 关键词标题命中率
- 🔍 搜索模式分布 (year/catchup 比例)
- 📰 文章预览 (前 5 条)
- 📋 汇总 (总条数、请求次数、拦截次数、官方占比)

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

### 已收录官方媒体 (20+)

| 级别 | 媒体 |
|------|------|
| 中央通讯社 | 新华网、人民网、央视网、中国新闻网、中国日报网 |
| 中央报刊 | 光明网、中国经济网、中国青年网、中国网、环球网、求是网、中国军网 |
| 政府机构 | 中国政府网、人民政协网、中央纪委、国家发改委 |
| 省级党媒 | 北京日报、上观新闻(上海)、南方网(广东) |

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
├── main.py                   # 主入口，命令行控制
├── requirements.txt          # Python 依赖
├── config/
│   └── settings.py           # 媒体分类 / 关键词 / 采集规则 / 反爬参数
├── spiders/
│   ├── base_spider.py        # 基础爬虫 (UA轮换/反爬检测/自动退避/Session管理)
│   ├── news_spider.py        # 百度新闻爬虫 (关键词/时间分区/site定向)
│   ├── bing_news_spider.py   # Bing 新闻爬虫 (关键词/时间分区)
│   ├── gov_spider.py         # 政府网站爬虫
│   └── baidu_news_spider.py  # 百度新闻独立爬虫 (兼容旧版)
├── utils/
│   ├── data_processor.py     # 数据清洗/去重/日期标准化/关键词提取
│   ├── resource_mapper.py    # 爬虫字段 → Resource Schema 映射 + 智能推断
│   ├── content_fetcher.py    # 可选：从 URL 抓取全文正文
│   └── storage.py            # 存储 + Markdown 统计报告 + MongoDB 导入脚本生成
├── data/
│   ├── raw/{news,gov}/       # 原始数据 (按 run_id 分目录)
│   └── processed/{news,gov}/ # 清洗后数据 (按 run_id 分目录)
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

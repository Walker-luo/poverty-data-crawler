# 英文学术文献采集器

## 项目目标

采集**国外英文**关于中国贫困治理/精准扶贫/脱贫/乡村振兴的**学术文献**，输出结构化元数据供**知识图谱**和**主题建模**使用：

- 📚 从 OpenAlex / Crossref 采集英文学术文献
- 🔍 按英文关键词检索，聚焦中国（通用词自动加 China 限定）
- 📥 可选下载全文（grobid XML 结构化全文 / PDF）
- 🧩 含引用关系、作者、机构、概念标签 —— 知识图谱直接可用
- 📊 数据按运行时间戳分目录保存，跨 run 自动去重

## 快速开始

```bash
# 用 OpenAlex（字段最全），额度用完自动切 Crossref
python collect_english.py

# 用 Crossref（免费无限量）
python collect_english.py --source crossref

# 小批量测试（每个关键词限 2 条）
python collect_english.py --limit 2
```

## 环境准备

```bash
pip install requests
```

需要 **OpenAlex API key** 才能下载全文（免费注册：https://openalex.org/users）。

## 计费说明（重要）

| 操作 | 单价 | 免费额度（带 key） |
|------|------|-------------------|
| **爬取元数据** | $0.001/请求 | $1/天 ≈ **1000 次请求** |
| **下载全文** | $0.01/文件 | $1/天 ≈ **100 个文件** |
| 不带 key 元数据 | $0.001/请求 | $0.1/天 ≈ 100 次请求 |

- 免费额度每天 **UTC 午夜重置**
- 日常元数据采集（千次内）完全够用；**全文下载贵 10 倍**，批量下载时留意额度
- 额度耗尽返回 `429 Insufficient budget`，脚本会自动跳过并提示

## 命令行参数详解

### 配置

| 位置 | 说明 |
|------|------|
| [collect_english.py:95](collect_english.py#L95) `MAILTO` | polite pool 邮箱标识 |
| [collect_english.py:101](collect_english.py#L101) `OPENALEX_API_KEY` | 你的 OpenAlex API key（或 `--api-key`） |

### `--source` 数据源

| 值 | 说明 |
|------|------|
| `openalex` | **默认**，字段最全（摘要/概念/机构/引用），额度 $1/天 |
| `crossref` | 免费无限量，元数据+引用关系，**但几乎无摘要** |
| `all` | 两者都跑（OpenAlex 额度用完自动切 Crossref），自动去重 |

### `--keywords` 关键词

英文关键词，分两类（内置 15 个）：

| 类型 | 检索方式 | 关键词 |
|------|---------|--------|
| **中国特有概念** | 不需加 China | `targeted poverty alleviation`(精准扶贫)、`rural revitalization`(乡村振兴)、`poverty governance`、`battle against poverty`、`common prosperity`(共同富裕)、`relocation for poverty alleviation`(易地搬迁)、`industrial/educational/health poverty alleviation` |
| **通用词** | 自动加 China 限定 | `poverty alleviation`、`poverty reduction`、`poverty eradication`、`multidimensional poverty`、`absolute poverty`、`poverty trap` |

```bash
# 自定义关键词
python collect_english.py --keywords "common prosperity" "rural revitalization"
```

### `--years` 限定年份

```bash
python collect_english.py --years 2013 2020   # 限定 2013-2020
python collect_english.py --years 2000        # 单年
```
不指定则覆盖 2000 至今。

### `--has-abstract` 只采有摘要的

仅对 OpenAlex 生效（OpenAlex 摘要覆盖率约 20-50%）：

```bash
python collect_english.py --source openalex --has-abstract
```

### `--limit` 限制数量

每个关键词限制条数（测试用）：

```bash
python collect_english.py --limit 2    # 每个关键词限 2 条，快速验证
```

### `--fulltext` 下载全文

| 值 | 说明 |
|------|------|
| `grobid-xml` | **推荐**，结构化全文（TEI XML），主题建模语料 |
| `pdf` | 原始 PDF 文件 |
| `none` | 默认，只爬元数据 |

```bash
# 采集 + 下载全文（需 API key）
python collect_english.py --source openalex --fulltext grobid-xml
```

**全文获取两路**：
1. **content API**（需 key）：`content.openalex.org/works/{id}.grobid-xml`，命中率约 40-50%
2. **oa_url 兜底**：content API 无索引时，尝试从开放获取链接直接下 PDF

**下载细节**：
- grobid XML 以 gzip 压缩传输，脚本自动解压为纯文本
- content API 无索引（`404 Work not found`）不算失败，会尝试 oa_url，无则跳过
- 带浏览器 UA 下载，用 `%PDF` magic 校验过滤 HTML 验证页

### `--run-id` 复用已有数据

对已采集的 run 下载全文，**不重新采集**（适合先采集、后补全文）：

```bash
# 只对历史 run 下载全文
python collect_english.py --run-id 20260826_190330 --fulltext grobid-xml
```

## 查重机制

| 场景 | 机制 |
|------|------|
| **单次运行内**（OpenAlex vs Crossref 重叠） | 按 DOI 去重 |
| **跨 run**（第二天续爬） | 自动扫描历史 run 的 works.json，跳过已采集的 DOI |

```bash
python collect_english.py --source openalex   # 明天重跑，自动跳过昨天已采的文献
```

**要点**：
- 去重键为**小写裸 DOI**（如 `10.1002/pop4.292`），兼容 OpenAlex 带前缀 / Crossref 大写字段
- 无 DOI 的文献用 OpenAlex W ID 兜底
- 若删除/移动了某 run 目录，去重键减少，下次可能重爬

## 输出格式

```
data/processed/academic/{run_id}/
├── works.csv          # 扁平元数据（列表字段分号分隔）
├── works.json         # 完整元数据（嵌套字段保留，知识图谱首选）
├── summary.md         # 统计报告（年份分布、期刊 Top、摘要覆盖率）
└── fulltext/          # 全文下载 (--fulltext)
    ├── W2078734051.xml    # grobid 结构化全文（主题建模推荐）
    └── W2078734051.pdf    # 原始 PDF
```

### works.json 字段（20 个）

| 字段 | 说明 | 覆盖率 |
|------|------|--------|
| `title` / `publication_year` / `publication_date` | 标题 / 年份 / 日期 | 100% |
| `journal` / `type` / `language` | 期刊 / 文献类型 / 语言 | 100% |
| `authors` | 作者列表 | ~96% |
| `institutions` | 作者机构列表 | ~92% |
| `concepts` | **OpenAlex 概念标签**（带置信度，主题聚类直接可用） | 100% |
| `keywords` | 作者关键词 | 100% |
| `abstract` | **摘要（已清洗为纯文本）** | ~50%（OpenAlex）/ 低频（Crossref） |
| `references` | 引用关系（**知识图谱的边**，W ID 列表） | ~92% |
| `cited_by_count` | 被引次数 | 100% |
| `doi` / `id` | DOI / OpenAlex W ID | 96% / 100% |
| `is_oa` / `oa_url` | 是否开放获取 / 全文链接 | 34%（仅 OA 文献） |
| `source_api` | 数据来源（openalex/crossref） | 100% |

### 摘要清洗

Crossref 的摘要为 **JATS XML 格式**（含 `<jats:p>`、`<jats:sec>` 等标签），采集时自动清洗：

```text
清洗前: '<jats:title>Abstract</jats:title>\n<jats:p>Inequalities within ...</jats:p>'
清洗后: Inequalities within and between countries continue to grow...
```

剥离全部标签 + 解码 HTML 实体 + 合并空白 + 剔除 "Abstract/Purpose" 等小标题。

## 常用命令

```bash
# ① 小批量测试（推荐先跑）
python collect_english.py --source crossref --keywords "targeted poverty alleviation" --limit 5

# ② 正式采集（OpenAlex 主源，额度用完切 Crossref）
python collect_english.py

# ③ 采集 + 下载全文
python collect_english.py --source openalex --fulltext grobid-xml

# ④ 对已有 run 补下载全文（不重新采集）
python collect_english.py --run-id <RUN_ID> --fulltext grobid-xml

# ⑤ 明天续爬（自动跳过已采集的）
python collect_english.py --source openalex
```

## 数据规模参考

| 命令 | 请求数 | 产出 |
|------|--------|------|
| `--limit 5` (15关键词) | ~30 次 | 几十条 |
| 全量（15 关键词 × 2000-2026） | ~百次 | 数百上千条 |
| OpenAlex 日均额度 | 1000 次元数据请求 | 足够全量采集 |

## 计费与额度提醒

- 元数据采集 `$0.001/次`，全文下载 `$0.01/个`
- 免费额度 $1/天，UTC 午夜重置
- 大量下全文时额度消耗快 10 倍 —— 建议先采元数据，确认需要再补全文
- 额度用完脚本会提示"OpenAlex 今日免费额度用完"，UTC 重置后重跑即可（配合跨 run 去重，不会重复扣费）

## 数据目录结构

```
poverty/
├── collect_english.py        # 英文学术文献采集器
├── README_academic.md        # 本文档
└── data/
    └── processed/
        └── academic/
            └── {run_id}/
                ├── works.csv
                ├── works.json
                ├── summary.md
                └── fulltext/
```

## 故障排查

### 爬取到 0 条文献

1. **额度用完**？→ 日志显示 `Insufficient budget`，UTC 午夜后重跑，或填 API key (`--api-key`) 提升额度
2. **关键词太窄**？→ 先用 `--keywords "targeted poverty alleviation" --limit 5` 单独测
3. **跨 run 去重跳过**？→ 显示 `+0 条` 说明该关键词下文献已在库里，属正常

### 摘要为空

Crossref 源摘要覆盖率极低（常见的 0/5），这是数据源固有特性。需要摘要请用 OpenAlex 源 + `--has-abstract`。

### 全文下载不到

- 日志显示"无索引跳过" → 该文献全文不在 OpenAlex content 索引（约一半的 OA 文献），可手动从 `oa_url` 下载
- `api-key` 未设置 → 用 `--api-key` 或脚本顶部 `OPENALEX_API_KEY` 填入

### 触发限流

- 429 + `Insufficient budget` → 额度用尽，等 UTC 重置或充值
- 429 + 其他 → 请求过快，脚本已有自动退避（等待 10s 重试）
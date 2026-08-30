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

## 常用命令

### 建库推荐流程：每天一个关键词组（5 天一轮）

OpenAlex 默认主源，`--min-relevance` 相关性触底自动停。每天一组，约 15-45 次请求，$1/天额度仅用 1.5-4.5%。

```bash
# 第1天: 组1 核心战略           第2天: 组2 共同富裕+攻坚
python collect_english.py --group 1 --min-relevance 100
python collect_english.py --group 2 --min-relevance 100

# 第3天: 组3 产业/领域          第4天: 组4 健康+通用
python collect_english.py --group 3 --min-relevance 100
python collect_english.py --group 4 --min-relevance 100

# 第5天: 组5 减贫学术
python collect_english.py --group 5 --min-relevance 100
```

跑完 5 天一轮后，隔一段时间再轮转一遍补充新文献（跨 run 自动去重，只付新增费用）。

### 全量采集（一次性，不分组）

额度充足或想一次跑完时，用 OpenAlex 全量爬取全部 15 词，结束后再用 Crossref 无限量补充（跨 run 自动去重，Crossref 不会重复采集 OpenAlex 已有的 DOI）：

```bash
# ① OpenAlex 全量爬取元数据（15 词，--min-relevance 相关性触底自动停）
python collect_english.py --source openalex --min-relevance 100

# ② OpenAlex 结束后，Crossref 补充（无限量，标题精准检索，自动跳过已采的 DOI）
python collect_english.py --source crossref --max-pages 5
```

> **说明**：OpenAlex 字段最全（摘要/概念/引用），Crossref 无限量但几乎无摘要。
> 两者按 DOI 去重合并，先 OpenAlex 后 Crossref，最终数据是两者的并集。
> 若 OpenAlex 额度不够一次跑完 15 词，用 `--group N` 分几天跑，最后再跑一次 Crossref 全量补齐。

### 其他常用

```bash
# ① 建库前小批量测试（先验证 API 和检索正常）
python collect_english.py --source crossref --keywords "targeted poverty alleviation" --limit 5 --max-pages 1

# ② 查看组状态（运行前自动打印各组已采集情况）
python collect_english.py --group 1

# ③ 采集 + 下载全文（grobid-xml 主题建模语料）
python collect_english.py --group 1 --min-relevance 100 --fulltext grobid-xml

# ④ 对已有 run 补下载全文（不重新采集）
python collect_english.py --run-id <RUN_ID> --fulltext grobid-xml

# ⑤ 引文扩展（从已采文献的引用关系继续挖相关文献，需 OpenAlex 源）
python collect_english.py --run-id <OPENALEX_RUN_ID> --expand-references --expand-limit 2000
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

### `--max-pages` 限制翻页深度（防额度失控）

**重要**：OpenAlex 的 `search` 是全库相关性匹配，不限制翻页时一个关键词可能翻几百页（如 "poverty alleviation" 命中 12 万条 → 595 页）。
且 search **按相关性排序，高相关文献集中在前几页**，深页全是只含 "poverty" 一个词的弱相关边缘结果，对"中国扶贫"主题无价值，却消耗大量额度。

```bash
# 每关键词最多翻 5 页（5×200=1000 条/关键词，覆盖高相关部分）
python collect_english.py --max-pages 5

# 每关键词 1 页（200 条，快，实测 1 秒采到 81 条高相关）
python collect_english.py --max-pages 1
```

**额度估算**（15 关键词全量）：

| 场景 | 请求数 | 说明 |
|------|--------|------|
| 不带 max-pages | ~8800 次 ≈ 9 天额度 | 失控，深页全是无关结果 |
| `--max-pages 5` | ~75 次 | 高相关全覆盖，1 天额度富余 |
| `--max-pages 1` | ~15 次 | 最快，只取相关性最高的 |

**建议**：全量建库用 `--max-pages 5`；日常增量/测试用 `--max-pages 1-2`。

### `--min-relevance` 相关性触底自动停（额度用足且不浪费）

**最佳策略**：不固定翻页数，而是让 OpenAlex 的 `relevance_score` 决定深度 —— 
翻页直到相关性低于阈值自动停止，把**高相关元数据都挖完**（额度花在刀刃上），弱相关噪声不采。

```bash
# 强相关词自动挖深（实测 targeted poverty alleviation → 3页 +465条）
# 弱相关词自动浅爬（实测 poverty dynamics → 1页 +32条）
python collect_english.py --min-relevance 100
```

relevance_score 参考值（实测 "targeted poverty alleviation China"）：

| 页码 | 相关性范围 | 数据质量 |
|------|-----------|---------|
| 第1-2页 | 260-1876 | 高相关（直接命中中国扶贫） |
| 第3-5页 | 150-230 | 相关（边界但可用） |
| 第6-11页 | 96-150 | 边际（部分相关） |
| 12页后 | <96 | 噪声（扩散到全球贫困/测量工具） |

**推荐用法**：

| 需求 | 命令 | 预期 |
|------|------|------|
| 挖高相关为主 | `--min-relevance 150` | 每词爬到第5页左右 |
| 平衡深度 | `--min-relevance 100` | 每词爬到第11页左右 |
| 尽量多采 | `--min-relevance 60` | 挖到噪声边缘 |
| 硬保护 | 配合 `--max-pages 20` | relevance 触底或 20 页先到先停 |

**两个参数结合**：`--max-pages` 是硬上限（防失控），`--min-relevance` 是质量软停止。两者先到先停，互不冲突。建议日常 `--min-relevance 100` 就不需要固定的 max-pages 约束。

### `--group` 分组采集（按天分批，防重复扣额）

内置 15 个关键词分为 **5 组，每组仅 3 词**，每天手动选择爬哪一组，配合跨 run 去重避免额度浪费：

```bash
python collect_english.py --group 1      # 只爬组1（3 词）
python collect_english.py --group 4 5    # 爬组4 + 组5
```

| 组号 | 关键词 | 主题 |
|------|--------|------|
| **组1** | targeted poverty alleviation、rural revitalization、poverty governance | 核心战略 |
| **组2** | common prosperity、battle against poverty、multidimensional poverty | 共同富裕 + 脱贫攻坚 |
| **组3** | relocation / industrial / educational poverty alleviation | 易地搬迁 + 产业 + 教育 |
| **组4** | health poverty alleviation、poverty alleviation、poverty reduction | 健康扶贫 + 通用 |
| **组5** | poverty eradication、absolute poverty、poverty trap | 减贫学术概念 |

每组 3 词 × `--max-pages 5` = **15 次请求**，仅占当日额度的 1.5%，$1/天额度完全无压力。

**组使用状态自动记录**（`data/processed/academic/group_status.json`）：

- 运行前打印各组状态 → 看到哪些组已采集、哪些待爬：

```
🗂 关键词组使用状态:
   组1 (3词: ...) ✅ 上次 2026-08-28 12:39 (run 20260828_123907)
   组2 (3词: ...) ⬜ 未采集
   组3 (3词: ...) ⬜ 未采集
   组4 (3词: ...) ⬜ 未采集
   组5 (3词: ...) ⬜ 未采集
```

- 采集成功后更新状态文件，并在 `summary.md` 记录（本次组别 + 全部组累计状态）

> **注意**：若修改了分组定义（如增减组数/词），旧 `group_status.json` 记录会失真，建议删除该文件重置状态。

### `--group` 与 `--source` 的关系（正交，互不影响）

**不需要**——`--source` 默认就是 `openalex`，`--group 1` 不加 `--source` 即用 OpenAlex 爬组1。

两者是**独立正交**的两个维度，任意组合：

```bash
# --source 默认就是 openalex，无需显式写
python collect_english.py --group 1                    # OpenAlex 爬组1（默认）
python collect_english.py --group 1 --source crossref  # 改用 Crossref 爬组1
python collect_english.py --group 1 --source all       # OpenAlex 为主，额度用完切 Crossref
```

| 组合 | 效果 | 适用 |
|------|------|------|
| `--group 1` | OpenAlex 爬组1（**默认**） | ✅ 推荐，字段最全（摘要/概念/引用） |
| `--group 1 --source all` | OpenAlex 先爬，额度用完自动切 Crossref 补齐 | 每组都想数据最多的场景 |
| `--group 1 --source crossref` | 只用 Crossref 爬组1 | 无摘要也无妨，无限量兜底 |
| `--source openalex` | 不加 `--group`，爬全部 15 词 | 一轮全量 |

**结论**：
- 想用 OpenAlex（默认推荐）→ 只写 `--group N` 即可，**无需加 `--source`**
- 只有想切到 Crossref 或 all 时才显式写 `--source`

**推荐连爬**（一天一组，OpenAlex 主源 + 相关性触底自动停）：

```bash
# 第1天 ~ 第5天，每天一组，每组 3 词 × 3-15 页 ≈ 9-45 次请求
python collect_english.py --group 1 --min-relevance 100   # 第1天
python collect_english.py --group 2 --min-relevance 100   # 第2天
python collect_english.py --group 3 --min-relevance 100   # 第3天
python collect_english.py --group 4 --min-relevance 100   # 第4天
python collect_english.py --group 5 --min-relevance 100   # 第5天
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

### `--resume-run` 续爬写回原 run 文件夹

**跳过指定 run 已采集的文献 + 新增数据直接合并写回该 run**（不再新开文件夹），适合分天补充同一批数据：

```bash
# 基于 run X 继续采集：跳过 X 已有的 + 新增写回 X（合并去重）
python collect_english.py --source crossref --resume-run <OPENALEX_RUN_ID> --max-pages 10
```

- 去重基准：只跳过指定 run 已采的文献（区别于默认扫描全部历史）
- **写回**：新采集的文献合并进该 run 的 works.json/works.csv，不覆盖原有数据
- 验证：日志显示 `🔗 合并原 run N 条 + 新增 M 条 = 累计 N+M 条`

不指定时默认扫描全部历史 run 去重，且写回**新** run 文件夹。

### `--expand-references` 引文扩展采集

觉得关键词检索采集太少时，从已采文献的**引用关系**里继续挖相关文献（引文追踪）：

```bash
# 从指定 run 的引用中扩展采集，新增写回该 run
python collect_english.py --run-id <OPENALEX_RUN_ID> --expand-references

# 限制扩展规模（已采文献引用常达几十万条，建议设上限控制额度）
python collect_english.py --run-id <OPENALEX_RUN_ID> --expand-references --expand-limit 2000
```

**原理**：
1. 收集该 run 所有文献的 `references`（OpenAlex W ID，即"这些文献引用了谁"）
2. 批量查询这些引用文献（`filter=ids.openalex:W1|W2|...`，每批 50 个）
3. 相关性过滤（标题须含扶贫关键词，通用词需含 China）+ 跨 run 去重
4. 新增文献合并写回原 run

**注意**：
- 仅 OpenAlex 源可用（Crossref 的引用是 DOI，无法按 W ID 扩展）
- 引用里的文献主题发散（含方法论/理论引用），过滤后会保留约 20-30% 相关文献
- 批量查询用**精简字段**（不含摘要/引用，避免 504 超时），扩展文献的 `abstract`/`references` 为空，需要时可用 `--fulltext` 或按 DOI 重采补充
- **务必设 `--expand-limit`**：每批 20 个引用 ID，`--expand-limit N` ≈ N/20 次请求。4582 篇文献引用高达 16.8 万 W ID，全量会耗尽额度。建议 1000-3000（约 50-150 次请求，$0.05-0.15）

## 查重机制

| 场景 | 机制 |
|------|------|
| **单次运行内**（OpenAlex vs Crossref 重叠） | 按 DOI 去重 |
| **跨 run**（第二天续爬） | 默认扫描历史 run 的 works.json，跳过已采集的 DOI |
| **指定续爬基准** | `--resume-run <ID>` 只跳过指定 run 的文献 |

```bash
# 默认：明天重跑，自动跳过所有历史 run 已采的文献
python collect_english.py --source openalex

# 指定基准：只从某 run 续爬（仅跳过该 run 的文献）
python collect_english.py --source openalex --resume-run 20260827_130902
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
| `relevance` | **OpenAlex 相关性分数**（越高越相关，排序/筛选依据） | OpenAlex 100% / Crossref 0 |
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
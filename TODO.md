# TODO: 国外语料 — 官方组织报告 + 新闻报告采集

## 背景与现状

国外语料分三层，**学术文献层已完成**：

- [x] 学术文献采集器（`collect_english.py`）— 已完成
  - OpenAlex / Crossref 双源，供知识图谱 + 主题建模
  - 数据目录：`data/processed/academic/{run_id}/`

**本次目标**：补齐国外语料另外两块 —— **官方组织报告** + **国际新闻报道**，与学术文献形成统一英文语料，支撑后续中外话语对比的知识图谱与主题建模。

---

## 一、国外官方组织报告采集

### 数据源清单（中国贫困/减贫/乡村振兴主题）

| 机构 | 报告库 / API | 主题 |
|------|-------------|------|
| World Bank | Open Knowledge Repository（有 API/OAI-PMH） | 减贫、农村发展、中国特辑 |
| UNDP | UNDP Library / 多维贫困指数 MPI 报告 | 多维贫困、SDG |
| FAO | FAO 文献库 / 粮食安全 | 农村、粮食、农业减贫 |
| IFAD | IFAD 评价报告库 | 农村发展、农发基金 |
| ADB | ADB 出版物 | 亚洲减贫、区域合作 |
| IMF | IMF eLibrary | 宏观经济与减贫 |
| WHO | 健康贫困相关 | 健康扶贫 |
| ILO | ILO Publications | 就业、体面劳动与减贫 |
| Oxfam | Oxfam 报告 | 消除极端贫困（全球视角） |

### 采集方案

- [x] 调研各机构可用的公开接口 —— **已实测**：多数国际组织报告库是 **DSpace 系统**（统一 REST API）
  - ✅ 已验证 DSpace 可用：**WHO IRIS**（`iris.who.int`，全文含提取的 .txt）
  - ✅ 疑似 DSpace（待服务器验证）：World Bank OKR、IFAD、UN Women、UNESCAP
  - ⏳ 非 DSpace（后续适配）：FAO、ADB、ILO
- [x] 设计英文关键词检索，从各库抓取标题/摘要/PDF 链接 —— 完成（`REPORT_KEYWORDS`）
- [x] 按 `data/processed/report/{run_id}/` 输出，字段对齐学术文献 —— 完成
- [x] 抓取正文 —— 完成：DSpace **TEXT bundle 提供全文文本 .txt**（无需 PDF 解析）+ ORIGINAL PDF 兜底

### 子任务

- [x] 评估 World Bank OKR API 的可用性 —— **已确认 DSpace**（本环境 SSL 抖动，服务器上需复测）
- [x] 搭建 ReportCollector —— **已完成 `report_collector.py`**（多机构 DSpace 采集器）
  ```bash
  python report_collector.py                    # 全部 DSpace 机构
  python report_collector.py --org WHO WorldBank --limit 5
  python report_collector.py --org WHO --download   # 采集 + 下载全文(pdf+txt)
  ```
- [x] 增量/断点续传（按 handle 去重）+ 失败记录 —— 完成
- [ ] 摘要清洗：报告常缺摘要（WHO 很多 `dc.description.abstract` 为空），后续可用标题+全文补

### 已知限制

- 本机环境部分机构 SSL 抖动（World Bank/IFAD），**需在你的服务器上实测**确认哪些可用
- 非 DSpace 机构（FAO/ADB/ILO）未适配，报告量覆盖有限 — 如需扩大可后续加
- 关键词相关性：报告库检索返回范围较宽（含营养成分/动物健康等非扶贫主题），已按机构注释区分

---

## 二、国外新闻报告（国际英文媒体）采集

### 数据源

**中国官方英文媒体**（政策话语，与中文直接对应）：
- CGTN、Xinhua English、People's Daily Online、China Daily、english.gov.cn（白皮书/政策发布）

**国际主流媒体**（外部视角，可做话语对比）：
- Reuters、BBC、The Guardian、SCMP、The Diplomat、Sixth Tone、Caixin Global

### 采集方案（复用现有 Bing 爬虫）

现有 `spiders/bing_news_spider.py` 已用于中文新闻，可扩展英文采集：

- [ ] 新增英文关键词集（见第三节），Bing 检索自动识别返回英文结果（`Accept-Language` 已设 en-US）
- [ ] 新增英文媒体来源过滤（对齐 `OFFICIAL_MEDIA` 思路，建 `INTL_ENGLISH_MEDIA` 官方/非官方表）
- [ ] 输出到独立目录 `data/processed/env_news/{run_id}/`，**不与中文 news 混放**
- [ ] 媒体来源判定：英文媒体名/域名匹配（`source_filter` 复用）

### 子任务

- [ ] 扩展 `bing_news_spider` 支持英文关键词 + 英文来源过滤（或新建 `english_news_spider.py`）
- [ ] 政策类文本补充：english.gov.cn 白皮书（结构化页面，单独抓）
- [ ] 新闻正文下载 + LLM 清洗（复用 `--download` / `--clean` 流程，需支持指定英文语料目录）

---

## 三、英文关键词（官方译法 + 组织/媒体常用）

**核心概念**（沿用学术部分）：
- targeted poverty alleviation（精准扶贫）、rural revitalization（乡村振兴）、poverty reduction（减贫）、poverty eradication（消除贫困）

**组织报告常用**：
- multidimensional poverty（多维贫困）、absolute poverty、poverty trap、SDG 1（No Poverty）

**后 2020 政策热点**：
- common prosperity（共同富裕）、poverty governance、battle against poverty(脱贫攻坚)

> 关键词集中在既有 `ENGLISH_KEYWORDS`（见 `collect_english.py`），报告采集可复用；新闻采集需要更"媒体化"的词（如 China poverty、poverty alleviation policy）

---

## 四、统一数据格式（对齐知识图谱/主题建模）

报告 / 新闻统一输出以下字段，与学术文献兼容：

| 字段 | 说明 |
|------|------|
| `title` | 标题 |
| `publish_date` | 日期 |
| `source` / `institution` | 媒体 / 机构名 |
| `url` | 原始链接 |
| `abstract` / `summary` | 摘要（清洗为纯文本） |
| `keywords` / `concepts` | 主题标签 |
| `content` / `fulltext` | 正文（报告 PDF / 新闻正文） |
| `source_type` | `report` / `news` / `academic`（区分语料类型） |

- [ ] 三块语料（academic / report / news）在入库/建图前合并，`source_type` 区分来源
- [ ] 汇总到统一 `works.json` 风格或建图谱数据准备脚本

---

## 五、依赖与复用

- [ ] `collect_english.py` 的关键词集 / 摘要清洗（`clean_abstract`）/ 失败重试（`_request`）直接复用
- [ ] `spiders/bing_news_spider.py` 复用做英文新闻
- [ ] `utils/news_md_downloader.py` + `llm_cleaner.py` 复用做新闻正文下载/清洗（需支持英文语料目录参数）
- [ ] 报告 PDF 下载复用 `_download_oa_url`（%PDF 校验）

---

## 六、建议排期

| 阶段 | 内容 | 产出 | 状态 |
|------|------|------|------|
| 1 | 调研报告库 API（Discovery: 多数为 DSpace，WHO IRIS 实测可用） | 接口清单 + 可行性 | ✅ 完成 |
| 2 | 报告采集器 ReportCollector | `data/processed/report/` 元数据 | ✅ 完成（`report_collector.py`） |
| 3 | 扩展新闻爬虫（英文关键词 + 来源过滤） | `data/processed/env_news/` 元数据 | ⏳ 待做 |
| 4 | 报告 PDF/TXT / 新闻正文下载 + 清洗 | `fulltext` / `.md` | ✅ 报告部分完成 |
| 5 | 三语料合并 → 知识图谱/主题建模输入 | 统一 dataset | ⏳ 待做 |

**里程碑**：
- [x] 报告库可行性验证 → **DSpace 统一 API 覆盖 WHO 等机构，报告采集落地**
- [ ] 下一步：在**服务器实测**多机构（World Bank/IFAD）可用性
 
---

*状态标记：`[ ]`=待办，`[x]`=已完成，`[~]`=进行中*
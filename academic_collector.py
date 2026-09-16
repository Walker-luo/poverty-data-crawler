# ============================================
# 英文学术文献采集器 — OpenAlex + Crossref 双源
# ============================================
"""
从 OpenAlex / Crossref 采集关于中国贫困治理/精准扶贫/脱贫/乡村振兴的英文学术文献，
输出结构化数据，供知识图谱和主题建模使用。

数据源对比（实测）:
    OpenAlex  免费 $0.1/天(约100次请求) | 字段最全(摘要/概念/机构/引用)
    Crossref  免费无限量              | 元数据+引用关系，但几乎无摘要
    建议: 默认 OpenAlex 为主，额度用完自动切 Crossref；也可直接 --source crossref

用法:
    python collect_english.py                          # OpenAlex 主源，额度用完切 Crossref
    python collect_english.py --source crossref        # 只用 Crossref（无限量）
    python collect_english.py --source openalex        # 只用 OpenAlex
    python collect_english.py --limit 50               # 每个关键词限制条数（测试）
    python collect_english.py --years 2013 2020        # 限定年份
    python collect_english.py --keywords "poverty alleviation" "common prosperity"
    python collect_english.py --has-abstract           # 只采集有摘要的文献（OpenAlex）

    # 全文下载（需 OpenAlex API key，免费注册 openalex.org/users）
    python collect_english.py --source openalex --fulltext grobid-xml --api-key YOUR_KEY

输出:
    data/processed/academic/{run_id}/
    ├── works.csv          # 文献元数据（含摘要、概念、引用关系）
    ├── works.json         # 同上 JSON（嵌套字段完整保留）
    ├── summary.md         # 采集统计报告
    └── fulltext/          # 全文下载 (--fulltext)
        ├── W3038568908.xml    # grobid 结构化全文（主题建模推荐）
        └── W3038568908.pdf    # 原始 PDF

依赖:
    pip install requests
"""

import csv
import gzip
import html as _html
import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import requests
import urllib3

# 静音 SSL 证书验证警告（下载 OA 文件用 verify=False，警告噪音大）
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ============================================================
# 英文关键词 — 官方译法 + 学术用法
# 分两类：中国特有概念（不需 China 限定） vs 通用词（需 China 限定）
# ============================================================
ENGLISH_KEYWORDS = [
    # 中国特有概念（概念本身已绑定中国，检索不需额外加 China）
    "targeted poverty alleviation",   # 精准扶贫
    "rural revitalization",           # 乡村振兴
    "poverty governance",             # 贫困治理
    "battle against poverty",         # 脱贫攻坚战
    "common prosperity",              # 共同富裕
    "relocation for poverty alleviation",   # 易地扶贫搬迁
    "industrial poverty alleviation",       # 产业扶贫
    "educational poverty alleviation",      # 教育扶贫
    "health poverty alleviation",           # 健康扶贫
    # 通用词（全球通用，需加 China 限定聚焦中国）
    "poverty alleviation",            # 扶贫
    "poverty reduction",              # 减贫
    "poverty eradication",            # 消除贫困
    "multidimensional poverty",       # 多维贫困
    "absolute poverty",               # 绝对贫困
    "poverty trap",                   # 贫困陷阱
]

# 中国特有概念集合（这些词检索时不需要额外加 China）
CHINA_SPECIFIC = {
    "targeted poverty alleviation",
    "rural revitalization",
    "poverty governance",
    "battle against poverty",
    "common prosperity",
    "relocation for poverty alleviation",
    "industrial poverty alleviation",
    "educational poverty alleviation",
    "health poverty alleviation",
}

# ============================================================
# 关键词分组 — 用 --group N 每天手动选择爬哪一组
# 每天爬一组（每组仅 3 词），配合 --resume-run 去重，避免额度浪费
# 每组 3 词 × --max-pages 5 ≈ 15 次请求，远低于 $1/天 (1000次)
# ============================================================
KEYWORD_GROUPS = {
    1: [  # 核心战略（3 词）
        "targeted poverty alleviation",
        "rural revitalization",
        "poverty governance",
    ],
    2: [  # 共同富裕 + 脱贫攻坚（3 词）
        "common prosperity",
        "battle against poverty",
        "multidimensional poverty",
    ],
    3: [  # 易地搬迁 + 产业扶贫（3 词）
        "relocation for poverty alleviation",
        "industrial poverty alleviation",
        "educational poverty alleviation",
    ],
    4: [  # 健康扶贫 + 通用（3 词）
        "health poverty alleviation",
        "poverty alleviation",
        "poverty reduction",
    ],
    5: [  # 减贫学术（3 词）
        "poverty eradication",
        "absolute poverty",
        "poverty trap",
    ],
}
# 状态文件：记录每组最近采集时间（在 academic 根目录，跨 run 持久）
GROUP_STATUS_FILE = Path("data/processed/academic/group_status.json")

MAILTO = "unknownluo7@gmail.com"  # polite pool 标识，建议改成你的真实邮箱
PER_PAGE = 200                     # 单页条数
REQUEST_DELAY = 0.2                # 请求间隔秒数（礼貌爬取）
# Crossref offset 分页上限（超过返回 400 Bad Request，必须主动停止）
CROSSREF_OFFSET_LIMIT = 10000

# OpenAlex 全文下载 API（content API）
# 免费注册获取 key: https://openalex.org/users
# 可填多个 key（每人各自额度），程序轮换使用，整体额度翻倍
OPENALEX_API_KEYS = [
    "dke7UcbHWbmO6iYa9rfXOt",      # key 1（主）
    "GdQak83gTOMNdUXboensIr"
    # "your-second-openalex-key",   # ⬅ 填入第二个 key，即可双倍额度
    # "your-third-openalex-key",
]
CONTENT_BASE_URL = "https://content.openalex.org/works"

# 检索停用词：标题相关性过滤时，这些词不作为"主题词"判断依据
_STOPWORDS = {
    "targeted", "battle", "against", "for", "of", "in", "the", "and",
    "to", "a", "an", "with", "on", "at", "by", "from", "into", "toward",
    "towards", "based", "using", "analysis", "study", "evidence", "case",
}


def keyword_terms(keyword: str) -> List[str]:
    """提取关键词的主题词（去掉停用词），用于标题相关性判断"""
    return [w for w in keyword.lower().split() if w not in _STOPWORDS and len(w) >= 3]


def is_relevant(title: str, keyword: str) -> bool:
    """判断标题是否与关键词相关（标题须包含至少一个主题词）"""
    if not title:
        return False
    t = title.lower()
    terms = keyword_terms(keyword)
    return any(term in t for term in terms)


# JATS/HTML 标签内常见但仍属噪音的小标题（清洗摘要时剔除）
_JATS_NOISE_HEADINGS = {
    "abstract", "purpose", "results", "conclusions", "methods",
    "background", "objective", "findings", "introduction", "design/methodology/approach",
}


def clean_abstract(raw_abstract: str) -> str:
    """清洗 Crossref 的 JATS XML / HTML 摘要为纯文本

    Crossref 的 abstract 多为 JATS 格式（含 <jats:p>、<jats:sec> 等标签），
    直接存库会污染文本，需剥离标签 + 解码实体 + 合并空白。
    """
    if not raw_abstract:
        return ""
    # 1. 块级闭合标签 → 空格分隔（还原段落边界）
    text = re.sub(
        r"</(?:jats:)?(?:p|sec|title|abstract)\s*>", " ",
        raw_abstract, flags=re.I,
    )
    # 2. 剥离所有剩余标签
    text = re.sub(r"<[^>]+>", " ", text)
    # 3. 解码 HTML 实体（如 &amp; → &, &#8211; → –）
    text = _html.unescape(text)
    # 4. 合并空白 + 零宽字符
    text = re.sub(r"[\s​-‍⁠﻿]+", " ", text).strip()
    # 5. 去掉开头的 "Abstract" 等章节小标题（如 "<jats:title>Abstract</jats:title> 正文"）
    parts = text.split(" ", 1)
    if parts and parts[0].strip(".:—-–").lower() in _JATS_NOISE_HEADINGS:
        text = parts[1].strip() if len(parts) > 1 else ""
    return text


class BudgetExhausted(Exception):
    """OpenAlex 每日免费额度用完"""
    pass


# ============================================================
# 数据源 1: OpenAlex（字段最全，额度 $0.1/天）
# ============================================================
class OpenAlexSource:
    BASE_URL = "https://api.openalex.org/works"
    SELECT_FIELDS = ",".join([
        "id", "doi", "title", "display_name", "publication_year",
        "publication_date", "type", "language", "cited_by_count",
        "authorships", "institutions", "concepts", "keywords",
        "topics", "referenced_works", "related_works",
        "abstract_inverted_index", "primary_location",
        "sustainable_development_goals", "open_access",
        "referenced_works_count", "relevance_score",
    ])
    # 引文扩展用精简字段：去掉 abstract/references 等大字段（避免 504 超时）
    EXPAND_SELECT_FIELDS = ",".join([
        "id", "doi", "title", "display_name", "publication_year",
        "publication_date", "type", "language", "cited_by_count",
        "authorships", "institutions", "concepts", "keywords",
        "open_access", "best_oa_location", "relevance_score",
    ])

    def __init__(self, session: requests.Session, mailto: str, delay: float,
                 api_keys: Optional[List[str]] = None):
        self.session = session
        self.mailto = mailto
        self.delay = delay
        self.api_keys = api_keys or [""]
        self._key_idx = 0
        self.name = "openalex"

    def _next_api_key(self) -> str:
        """返回当前 API key（一个用完后再切换下一个）"""
        if not self.api_keys:
            return ""
        return self.api_keys[min(self._key_idx, len(self.api_keys) - 1)]

    def _switch_api_key(self) -> bool:
        """当前 key 额度用完 → 切下一个；还有剩余 key 返回 True"""
        if self._key_idx < len(self.api_keys) - 1:
            self._key_idx += 1
            return True
        return False

    def search(self, keyword: str, year_filter: str, has_abstract: bool,
               limit: Optional[int], china_specific: bool,
               max_pages: Optional[int] = None,
               min_relevance: Optional[float] = None) -> List[Dict]:
        """按关键词检索（分页）

        Args:
            max_pages: 每关键词最大翻页数（硬上限，防额度失控）。
            min_relevance: 相关性触底阈值。OpenAlex search 按相关性排序，
                relevance 低于该值的连续结果都是噪声 → 自动停止翻页。
                用绝对值分数（实测: 首页median≈420, 第5页≈153, 第11页≈101）。
                这样翻页深度由数据质量决定，把高相关元数据都挖完。
        """
        results = []
        cursor = "*"
        pages_done = 0
        filters = [f"language:en", f"publication_year:{year_filter}"]
        if has_abstract:
            filters.append("has_abstract:true")
        # 通用词加 China 限定，聚焦中国贫困研究
        query = keyword if china_specific else f"{keyword} China"

        while True:
            if limit and len(results) >= limit:
                break
            if max_pages and pages_done >= max_pages:
                break
            params = {
                "search": query,
                "filter": ",".join(filters),
                "per-page": PER_PAGE,
                "cursor": cursor,
                "mailto": self.mailto,
                "select": self.SELECT_FIELDS,
            }
            data = self._request(params)
            if data is None:
                break
            batch = data.get("results", [])
            if not batch:
                break
            # relevance 触底：页内按相关性递减，一旦低于阈值即停止（后续只会更低）
            if min_relevance:
                kept = []
                stopped = False
                for r in batch:
                    if r.get("relevance_score", 0) < min_relevance:
                        stopped = True
                        break
                    kept.append(r)
                results.extend(kept)
                if stopped:
                    logger.info(f"    '{keyword}' 相关性触底(<{min_relevance})"
                                f"停止翻页, 已爬 {pages_done+1} 页")
                    break
            else:
                results.extend(batch)
            pages_done += 1
            next_cursor = data.get("meta", {}).get("next_cursor")
            if not next_cursor:
                break
            cursor = next_cursor
            time.sleep(self.delay)
        return results if max_pages is None else results[:min(
            len(results), max_pages * PER_PAGE)]

    def _request(self, params: Dict, retries: int = 5) -> Optional[Dict]:
        """带重试的 OpenAlex 请求

        处理各类失败：
          - 429 限流 → 区分额度用完(BudgetExhausted) 与普通限流
          - 502/503/504 服务器繁忙 → 退避重试
          - SSL/连接/超时 等网络异常 → 指数退避重试 + 重建连接（防坏连接复用）
          - 其他 4xx HTTP 错误 → 优雅返回 None（不重试，永久错误）
        """
        n_keys = max(1, len(self.api_keys))
        for attempt in range(retries + n_keys):
            key = self._next_api_key()
            if key:
                params["api-key"] = key
            try:
                resp = self.session.get(
                    self.BASE_URL, params=params, timeout=30)
                if resp.status_code == 429:
                    # 区分：额度用完 vs 普通限流
                    if ("Insufficient budget" in resp.text
                            or "budget" in resp.text):
                        # 当前 key 额度用完 → 切换下一个 key 继续
                        if self._switch_api_key():
                            logger.warning(
                                f"OpenAlex 某 key 额度用完，"
                                f"切换到 key{self._key_idx + 1} 继续")
                            continue
                        raise BudgetExhausted()  # 所有 key 用尽
                    logger.warning(f"OpenAlex 限流(429)，等待 10s...")
                    time.sleep(10)
                    return None
                if resp.status_code in (502, 503, 504):
                    # 服务器繁忙/网关超时 → 退避重试
                    if attempt < retries - 1:
                        wait = 5 * (attempt + 1)
                        logger.warning(
                            f"OpenAlex {resp.status_code} 服务器繁忙，"
                            f"重试 {attempt+1}/{retries} (等待 {wait}s)"
                        )
                        time.sleep(wait)
                        continue
                resp.raise_for_status()
                return resp.json()
            except requests.exceptions.HTTPError as e:
                # 4xx/5xx（已被 raise_for_status 抛出）→ 永久错误不重试
                status = e.response.status_code if e.response else "?"
                logger.error(f"OpenAlex HTTP {status} 错误，放弃该批")
                return None
            except requests.RequestException as e:
                # SSL/连接/超时 等网络异常 → 指数退避 + 重建连接（防坏连接复用）
                if attempt < retries - 1:
                    wait = 3 * (2 ** attempt)  # 3, 6, 12, 24...
                    logger.warning(
                        f"OpenAlex 连接异常 {type(e).__name__}，"
                        f"重试 {attempt+1}/{retries} (等待 {wait}s)"
                    )
                    # 重建 session：SSLError 后连接池可能有坏连接
                    self.session.close()
                    self.session = requests.Session()
                    self.session.headers["User-Agent"] = (
                        f"poverty-research/1.0 (mailto:{self.mailto})")
                    self.session.headers["Accept"] = "application/json"
                    time.sleep(wait)
                else:
                    logger.error(
                        f"OpenAlex 请求失败（{retries}次重试）: "
                        f"{type(e).__name__}: {e}"
                    )
                    return None
        return None

    def get_by_ids(self, ids: List[str]) -> List[Dict]:
        """按 OpenAlex W ID 批量查询文献（引文扩展用）

        filter=ids.openalex:W1|W2|... 一次查多个 ID，分批避免 URL 过长。
        用精简字段 + 小批（20），避免大响应导致 504 超时。
        每 50 批输出一次进度，便于长任务观察。
        """
        results = []
        BATCH = 20
        total = len(ids)
        total_batches = (total + BATCH - 1) // BATCH
        for bi, i in enumerate(range(0, total, BATCH), 1):
            batch = ids[i:i + BATCH]
            params = {
                "filter": "ids.openalex:" + "|".join(batch),
                "per-page": len(batch),
                "mailto": self.mailto,
                "select": self.EXPAND_SELECT_FIELDS,
            }
            data = self._request(params)
            if data:
                results.extend(data.get("results", []))
            # 进度：每 50 批或最后一批
            if bi % 50 == 0 or bi == total_batches:
                done = min(bi * BATCH, total)
                logger.info(
                    f"   ⏳ 扩展进度: {done}/{total} 引用 "
                    f"({bi}/{total_batches} 批) | 命中 {len(results)} 篇"
                )
            time.sleep(self.delay)
        return results

    def normalize(self, work: Dict) -> Dict:
        primary = work.get("primary_location") or {}
        source = primary.get("source") or {}
        return {
            "id": (work.get("id") or "").split("/")[-1],
            "doi": (work.get("doi") or "").replace("https://doi.org/", ""),
            "title": (work.get("title") or work.get("display_name") or "").strip(),
            "publication_year": work.get("publication_year", ""),
            "publication_date": work.get("publication_date", ""),
            "type": work.get("type", ""),
            "language": work.get("language", ""),
            "journal": source.get("display_name", ""),
            "landing_page_url": primary.get("landing_page_url", ""),
            "cited_by_count": work.get("cited_by_count", 0),
            "referenced_works_count": work.get("referenced_works_count", 0),
            "abstract": self._reconstruct_abstract(work.get("abstract_inverted_index")),
            "authors": self._extract_authors(work),
            "institutions": self._extract_institutions(work),
            "concepts": self._extract_concepts(work),
            "keywords": self._extract_keywords(work),
            "references": [w.split("/")[-1] for w in work.get("referenced_works", [])],
            "source_api": "openalex",
            "relevance": round(work.get("relevance_score", 0), 1),
            "is_oa": bool(work.get("open_access", {}).get("is_oa", False)),
            "oa_url": (work.get("best_oa_location") or {}).get("pdf_url", "")
                       or (work.get("open_access") or {}).get("oa_url", ""),
        }

    @staticmethod
    def _extract_keywords(work: Dict) -> List[str]:
        """提取作者关键词（兼容新版 OpenAlex 的 dict 格式）"""
        kws = []
        for k in work.get("keywords", []):
            if isinstance(k, str):
                kws.append(k)
            elif isinstance(k, dict) and k.get("display_name"):
                kws.append(k["display_name"])
        return kws

    @staticmethod
    def _reconstruct_abstract(inverted_index: Optional[Dict]) -> str:
        if not inverted_index:
            return ""
        pos = {}
        for word, indices in inverted_index.items():
            for i in indices:
                pos[i] = word
        return " ".join(pos[i] for i in sorted(pos))

    @staticmethod
    def _extract_authors(work: Dict) -> List[str]:
        return [a.get("author", {}).get("display_name", "")
                for a in work.get("authorships", [])
                if a.get("author", {}).get("display_name")]

    @staticmethod
    def _extract_institutions(work: Dict) -> List[str]:
        seen, insts = set(), []
        for a in work.get("authorships", []):
            for inst in a.get("institutions", []):
                name = inst.get("display_name", "")
                if name and name not in seen:
                    seen.add(name)
                    insts.append(name)
        return insts

    @staticmethod
    def _extract_concepts(work: Dict, top_n: int = 10) -> List[Dict]:
        return [
            {"name": c.get("display_name", ""), "score": round(c.get("score", 0), 3)}
            for c in sorted(work.get("concepts", []),
                            key=lambda x: -x.get("score", 0))[:top_n]
        ]


# ============================================================
# 数据源 2: Crossref（无限量，缺摘要）
# ============================================================
class CrossrefSource:
    BASE_URL = "https://api.crossref.org/works"

    def __init__(self, session: requests.Session, mailto: str, delay: float):
        self.session = session
        self.mailto = mailto
        self.delay = delay
        self.name = "crossref"

    def search(self, keyword: str, year_filter: str, has_abstract: bool,
               limit: Optional[int], china_specific: bool,
               max_pages: Optional[int] = None,
               min_relevance: Optional[float] = None) -> List[Dict]:
        """按关键词检索（offset 分页，无限量）

        注意：Crossref 的 cursor 分页与 query.title 冲突（会导致标题检索失效），
        故改用 offset 分页。
        """
        results = []
        offset = 0
        pages_done = 0
        y_from, y_to = year_filter.split("-")
        # 通用词加 China 限定（标题须同时含关键词和 China）
        query = keyword if china_specific else f"{keyword} China"

        while True:
            if limit and len(results) >= limit:
                break
            if max_pages and pages_done >= max_pages:
                break
            # Crossref offset 上限保护（超过 10000 返回 400，提前停止避免崩溃）
            if offset >= CROSSREF_OFFSET_LIMIT:
                logger.info(
                    f"    '{keyword}' 结果超 {CROSSREF_OFFSET_LIMIT} 条，"
                    f"达到 Crossref offset 上限，停止翻页（已爬 {pages_done} 页）"
                )
                break
            params = {
                # 标题精准检索（全文 query 噪音太大，会混入无关文献）
                "query.title": query,
                "filter": f"from-pub-date:{y_from}-01-01,until-pub-date:{y_to}-12-31,type:journal-article",
                "rows": 200,
                "offset": offset,
                "mailto": self.mailto,
                "select": "DOI,title,author,issued,container-title,reference,"
                          "is-referenced-by-count,publisher,type,URL,abstract",
            }
            data = self._request(params)
            if data is None:
                break
            msg = data.get("message", {})
            items = msg.get("items", [])
            if not items:
                break
            results.extend(items)
            pages_done += 1
            total = msg.get("total-results", 0)
            offset += len(items)
            if offset >= total or not items:
                break
            time.sleep(self.delay)
        if max_pages is not None:
            results = results[:max_pages * 200]
        return results[:limit] if limit else results

    def _request(self, params: Dict, retries: int = 5) -> Optional[Dict]:
        for attempt in range(retries):
            try:
                resp = self.session.get(
                    self.BASE_URL, params=params, timeout=30)
                if resp.status_code == 429:
                    logger.warning(f"Crossref 限流(429)，等待 10s...")
                    time.sleep(10)
                    return None
                # 400 通常是 offset 触顶，返回 None 让上层停止（配合 offset 上限保护）
                if resp.status_code in (400, 404):
                    logger.warning(
                        f"Crossref 请求失败(HTTP {resp.status_code})，停止翻页")
                    return None
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as e:
                # SSL/连接/超时 等网络异常 → 退避重试
                if attempt < retries - 1:
                    wait = 3 * (2 ** attempt)
                    logger.warning(
                        f"Crossref 连接异常 {type(e).__name__}，"
                        f"重试 {attempt+1}/{retries} (等待 {wait}s)"
                    )
                    self.session.close()
                    self.session = requests.Session()
                    self.session.headers["User-Agent"] = (
                        f"poverty-research/1.0 (mailto:{self.mailto})")
                    time.sleep(wait)
                else:
                    logger.error(
                        f"Crossref 请求失败（{retries}次重试）: "
                        f"{type(e).__name__}: {e}")
                    return None
        return None

    def normalize(self, item: Dict) -> Dict:
        # 日期
        issued = item.get("issued", {}).get("date-parts", [[None]])[0]
        year = issued[0] if issued and issued[0] else ""
        date = "-".join(str(x).zfill(2) for x in issued if x) if issued else ""

        # 作者 + 机构
        authors = []
        institutions = []
        for a in item.get("author", []):
            name = " ".join(x for x in [a.get("given"), a.get("family")] if x)
            if name:
                authors.append(name)
            for aff in a.get("affiliation", []):
                if aff.get("name") and aff["name"] not in institutions:
                    institutions.append(aff["name"])

        # 引用关系（仅保留有 DOI 的，可建图）
        references = [
            r["DOI"].replace("https://doi.org/", "")
            for r in item.get("reference", [])
            if r.get("DOI")
        ]

        return {
            "id": item.get("DOI", "").replace("https://doi.org/", ""),
            "doi": item.get("DOI", "").replace("https://doi.org/", ""),
            "title": (item.get("title") or [""])[0].strip(),
            "publication_year": year,
            "publication_date": date,
            "type": item.get("type", ""),
            "language": "en",
            "journal": (item.get("container-title") or [""])[0],
            "landing_page_url": item.get("URL", ""),
            "cited_by_count": item.get("is-referenced-by-count", 0),
            "referenced_works_count": len(references),
            "abstract": clean_abstract(item.get("abstract") or ""),
            "authors": authors,
            "institutions": institutions,
            "concepts": [],
            "keywords": [],
            "references": references,
            "source_api": "crossref",
            "relevance": 0,
            "is_oa": False,
            "oa_url": "",
        }


# ============================================================
# 采集器（编排多源）
# ============================================================
class AcademicCollector:
    def __init__(self, source: str = "openalex", mailto: str = MAILTO,
                 delay: float = REQUEST_DELAY, api_key: str = "",
                 run_id: Optional[str] = None,
                 resume_run: Optional[str] = None,
                 max_pages: Optional[int] = None,
                 min_relevance: Optional[float] = None,
                 out_dir: Optional[str] = None):
        self.mailto = mailto
        self.delay = delay
        # API key 支持多个：CLI 传的（逗号/空格分隔）优先，否则用配置列表
        self.api_keys = self._parse_keys(api_key)
        self._key_idx = 0
        self.session = requests.Session()
        self.session.headers["User-Agent"] = f"poverty-research/1.0 (mailto:{mailto})"
        self.resume_run = resume_run
        self.max_pages = max_pages
        self.min_relevance = min_relevance
        self.used_groups: List[int] = []

        # 按 source 组装数据源顺序
        self.sources = []
        if source in ("openalex", "all"):
            self.sources.append(OpenAlexSource(self.session, mailto, delay,
                                               self.api_keys))
        if source in ("crossref", "all"):
            self.sources.append(CrossrefSource(self.session, mailto, delay))

        # run_id → 复用其目录（下载全文用）
        # resume_run → 续爬并写回该 run 目录（数据累积在同一文件夹）
        # out_dir   → 显式指定输出目录（覆盖默认路径）
        if out_dir:
            self.out_dir = Path(out_dir)
            self.run_id = run_id or self.out_dir.name
        else:
            self.run_id = run_id or resume_run or datetime.now().strftime("%Y%m%d_%H%M%S")
            self.out_dir = Path(f"data/processed/academic/{self.run_id}")
        self.out_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _parse_keys(api_key: str) -> List[str]:
        """解析 API key：CLI 传入（逗号/空格分隔）优先，否则用配置列表"""
        src = api_key.strip() if api_key else ""
        if not src:
            return list(OPENALEX_API_KEYS)
        # 支持 "k1,k2" 或 "k1 k2"
        return [k for k in src.replace(",", " ").split() if k]

    def _next_api_key(self) -> str:
        """返回当前 API key（一个用完后再切换下一个）"""
        if not self.api_keys:
            return ""
        return self.api_keys[min(self._key_idx, len(self.api_keys) - 1)]

    def _switch_api_key(self) -> bool:
        """当前 key 额度用完 → 切下一个；还有剩余 key 返回 True"""
        if self._key_idx < len(self.api_keys) - 1:
            self._key_idx += 1
            return True
        return False

    def load_run(self) -> List[Dict]:
        """读取已有 run 的 works.json

        来源优先级：自定义 out_dir（若含 works.json）> academic/{run_id}。
        这样 --run-id X --out-dir Y 可加载 X 的数据、输出到 Y。
        """
        out_path = self.out_dir / "works.json"
        path = out_path if out_path.exists() else Path(
            f"data/processed/academic/{self.run_id}/works.json")
        if not path.exists():
            raise SystemExit(f"未找到已采集数据: {path}")
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def _load_group_status() -> Dict:
        """读取关键词组使用状态 {组号: 最近采集时间}"""
        if GROUP_STATUS_FILE.exists():
            try:
                with open(GROUP_STATUS_FILE, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def print_group_status(self) -> None:
        """打印关键词组使用状态（供每日选择组别参考）"""
        status = self._load_group_status()
        logger.info("🗂 关键词组使用状态:")
        for g in sorted(KEYWORD_GROUPS):
            kws = KEYWORD_GROUPS[g]
            last = status.get(str(g))
            label = f"✅ 上次 {last}" if last else "⬜ 未采集"
            logger.info(f"   组{g} ({len(kws)}词: {', '.join(kws[:3])}...)"
                        f" {label}")

    def _save_group_status(self, groups: List[int], run_id: str) -> None:
        """更新关键词组使用状态（记录最近采集时间 + 对应 run_id）"""
        status = self._load_group_status()
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        for g in groups:
            status[str(g)] = f"{now} (run {run_id})"
        GROUP_STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(GROUP_STATUS_FILE, "w", encoding="utf-8") as f:
            json.dump(status, f, ensure_ascii=False, indent=2)

    @staticmethod
    def _dedup_key(item: Dict) -> str:
        """提取文献去重键：小写裸 DOI 优先，缺失则用 id（OpenAlex W ID）

        兼容两种源原始返回的字段差异：
          - OpenAlex:  'doi'        → "https://doi.org/10.xxx"（带前缀）
          - Crossref:  'DOI'        → "10.xxx"（大写键名，裸 DOI）
          - normalize: 'doi'        → "10.xxx"（已去前缀）
          统一规范化为小写裸 DOI，保证与 works.json 的历史键一致。
        """
        doi = (item.get("doi") or item.get("DOI") or "").strip()
        doi = doi.replace("https://doi.org/", "").replace("http://dx.doi.org/", "")
        doi = doi.strip().lower()
        if doi:
            return doi
        return (item.get("id") or "").strip()

    def _load_seen_keys(self, resume_run: Optional[str] = None) -> set:
        """收集已采集文献的 DOI/id，作为本次采集的去重基准

        Args:
            resume_run: 指定从哪个 run 续爬（只跳过该 run 已采集的文献）；
                        否则扫描全部历史 run。
        """
        academic_dir = Path("data/processed/academic")
        keys = set()

        # 指定 run：只读取该 run
        if resume_run:
            target = academic_dir / resume_run / "works.json"
            if not target.exists():
                raise SystemExit(f"续爬基准不存在: {target}")
            with open(target, encoding="utf-8") as f:
                for w in json.load(f):
                    key = self._dedup_key(w)
                    if key:
                        keys.add(key)
            logger.info(f"🔄 续爬基准: {resume_run} 已采集 "
                        f"{len(keys)} 个去重键")
            return keys

        # 未指定：扫描全部历史 run
        if not academic_dir.exists():
            return keys
        for run_dir in sorted(academic_dir.iterdir(), reverse=True):
            if not run_dir.is_dir() or run_dir.name == self.run_id:
                continue
            works_path = run_dir / "works.json"
            if not works_path.exists():
                continue
            try:
                with open(works_path, encoding="utf-8") as f:
                    works = json.load(f)
            except Exception:
                continue
            for w in works:
                key = self._dedup_key(w)
                if key:
                    keys.add(key)
        return keys

    def collect_all(self, keywords: Optional[List[str]] = None,
                    years: Optional[List[int]] = None,
                    has_abstract: bool = False,
                    limit: Optional[int] = None,
                    group: Optional[List[int]] = None) -> List[Dict]:
        # 指定关键词组 → 用组内关键词；否则用传入或全部内置
        if group:
            selected = []
            for g in group:
                if g not in KEYWORD_GROUPS:
                    raise SystemExit(f"无效组号 {g}，可选 {sorted(KEYWORD_GROUPS)}")
                selected.extend(KEYWORD_GROUPS[g])
                if g not in self.used_groups:
                    self.used_groups.append(g)
            keywords = list(dict.fromkeys(selected))
        elif keywords is None:
            # 全量采集内置全部关键词 → 视为 5 个组都已覆盖
            keywords = ENGLISH_KEYWORDS
            self.used_groups = list(KEYWORD_GROUPS.keys())

        years = years or list(range(2000, datetime.now().year + 1))
        year_filter = f"{min(years)}-{max(years)}"

        group_hint = f" | 组 {self.used_groups}" if self.used_groups else ""
        logger.info("=" * 60)
        logger.info(f"📚 学术文献采集 | 源: {[s.name for s in self.sources]} | "
                    f"关键词 {len(keywords)} 个 | 年份 {year_filter}{group_hint}")
        logger.info("=" * 60)

        all_works = []
        # 跨 run 查重：加载历史已采集的 DOI/id，避免额度分天爬取时重复
        seen_ids = self._load_seen_keys(self.resume_run)
        if seen_ids:
            scope = f"指定 run {self.resume_run} 的" if self.resume_run else "历史"
            logger.info(f"🔄 已加载{scope}去重键 {len(seen_ids)} 个 "
                        f"（跨 run 跳过已采集文献）")

        # 逐源采集
        for source in self.sources:
            logger.info(f"▸ 数据源: {source.name}")
            for qi, kw in enumerate(keywords, 1):
                china_specific = kw in CHINA_SPECIFIC
                try:
                    works = source.search(kw, year_filter, has_abstract, limit,
                                          china_specific, self.max_pages,
                                          self.min_relevance)
                except BudgetExhausted:
                    logger.warning(
                        f"⚠️ OpenAlex 今日免费额度用完($0.1)，"
                        f"UTC 午夜重置后重跑可继续。跳过剩余关键词。"
                    )
                    break
                new = 0
                for w in works:
                    # 本地相关性过滤：标题须含主题词，过滤检索噪音
                    title = ""
                    if source.name == "openalex":
                        title = w.get("title") or w.get("display_name") or ""
                    else:
                        title = (w.get("title") or [""])[0]
                    if not is_relevant(title, kw):
                        continue
                    # 通用词额外要求标题含 China/Chinese
                    if not china_specific and "china" not in title.lower():
                        continue
                    # 统一去重键（小写裸 DOI，OpenAlex/Crossref 都兼容）
                    key = self._dedup_key(w)
                    if key and key in seen_ids:
                        continue
                    if key:
                        seen_ids.add(key)
                    all_works.append(source.normalize(w))
                    new += 1
                logger.info(
                    f"  [{qi}/{len(keywords)}] '{kw}': +{new} 条 "
                    f"(累计 {len(all_works)})"
                )

        logger.info("=" * 60)
        logger.info(f"✅ 采集完成: {len(all_works)} 条英文文献")
        return all_works

    def expand_from_references(self, works: List[Dict],
                               expand_limit: Optional[int] = None,
                               save_every: int = 5000) -> int:
        """从已采文献的引用列表扩展采集（引文追踪）

        收集所有 references 的 OpenAlex W ID → 分批查询 → 相关性过滤 → 去重。
        每处理 save_every 个引用就落盘一次（合并写回 works.json），
        避免大任务（数万引用）全量驻留内存、中途中断丢失成果。
        已处理的引用记录在 run 目录 `expanded_refs.json`，分多天调用时
        自动跳过已处理部分，从剩余引用继续（额度分摊到每天，累积覆盖更多）。

        Args:
            expand_limit: 本次最多处理的引用 W ID 数（默认全部剩余）。
                每批 20 个 = 1 次请求，建议按当日额度设置
                （如 expand_limit 15000 ≈ 750 次请求 ≈ $0.75）。
            save_every: 每处理多少引用落盘一次（默认 5000），
                大任务建议 5000-10000，内存/中断更稳。

        Returns:
            本次新增文献总数
        """
        openalex_src = next(
            (s for s in self.sources if s.name == "openalex"), None)
        if not openalex_src:
            raise SystemExit("引文扩展需要 OpenAlex 源 (--source openalex 或 all)")

        # 1. 收集所有引用的 W ID
        ref_ids = set()
        for w in works:
            for rid in w.get("references", []):
                if isinstance(rid, str) and rid.startswith("W"):
                    ref_ids.add(rid)
        if not ref_ids:
            logger.warning("已采文献没有可用的引用关系（W ID）")
            return 0

        # 2. 加载已处理进度，计算本次剩余
        progress_file = self.out_dir / "expanded_refs.json"
        processed = set()
        if progress_file.exists():
            try:
                processed = set(json.loads(
                    progress_file.read_text(encoding="utf-8")))
            except Exception:
                processed = set()
        remaining = sorted(ref_ids - processed)
        total_refs = len(ref_ids)
        if not remaining:
            logger.info(f"✅ 所有引用({total_refs})均已扩展过，无需继续")
            return 0

        selected = remaining[:expand_limit] if expand_limit else remaining
        logger.info(
            f"📎 引用共 {total_refs}，已处理 {len(processed)}，"
            f"本次扩展 {len(selected)}（{len(selected)//20} 批请求）..."
        )

        # 3. 分批处理 + 分批落盘
        chunk_size = save_every if save_every and save_every > 0 else len(selected)
        seen_ids = self._load_seen_keys()
        total_new = 0

        for chunk_start in range(0, len(selected), chunk_size):
            chunk = selected[chunk_start:chunk_start + chunk_size]
            raw_results = openalex_src.get_by_ids(chunk)
            logger.info(f"   → 本批命中 {len(raw_results)} 篇引用文献")

            # 相关性过滤 + 去重
            expanded = []
            for w in raw_results:
                title = (w.get("title") or w.get("display_name") or "").strip()
                if not title:
                    continue
                # 标题须含任一内置关键词的主题词（证明主题相关）
                if not any(is_relevant(title, kw) for kw in ENGLISH_KEYWORDS):
                    continue
                # 只命中通用主题词（如单纯 poverty）时，需标题含 China 才放行
                t = title.lower()
                if "china" not in t and "chinese" not in t:
                    hit_china_kw = any(
                        is_relevant(title, kw) for kw in CHINA_SPECIFIC)
                    if not hit_china_kw:
                        continue
                key = self._dedup_key(w)
                if key and key in seen_ids:
                    continue
                if key:
                    seen_ids.add(key)
                expanded.append(openalex_src.normalize(w))

            # 更新进度（含被过滤的引用）
            processed.update(chunk)
            progress_file.write_text(
                json.dumps(sorted(processed), ensure_ascii=False),
                encoding="utf-8",
            )

            # 分批落盘
            if expanded:
                self.save(expanded)
                total_new += len(expanded)
                logger.info(
                    f"   💾 已落盘 {len(expanded)} 篇 | "
                    f"进度 {len(processed)}/{total_refs} | 累计新增 {total_new}"
                )
            else:
                logger.info(
                    f"   (本批无相关文献) 进度 {len(processed)}/{total_refs}"
                )

        logger.info(
            f"📎 引文扩展完成: 新增 {total_new} 篇相关文献 | "
            f"📌 进度 {len(processed)}/{total_refs}（剩余 {total_refs-len(processed)}）"
        )
        return total_new

    def save(self, works: List[Dict]) -> None:
        json_path = self.out_dir / "works.json"

        # --resume-run 续爬 / 引文扩展写回：与原数据合并去重，而非覆盖
        if json_path.exists():
            try:
                existing = json.loads(json_path.read_text(encoding="utf-8"))
            except Exception:
                existing = []
            seen = {self._dedup_key(w) for w in existing if self._dedup_key(w)}
            merged = list(existing)
            new_cnt = 0
            for w in works:
                key = self._dedup_key(w)
                if key and key in seen:
                    continue
                if key:
                    seen.add(key)
                merged.append(w)
                new_cnt += 1
            works = merged
            logger.info(
                f"🔗 合并原 run {len(existing)} 条 + 新增 {new_cnt} 条"
                f" = 累计 {len(works)} 条"
            )

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(works, f, ensure_ascii=False, indent=2)
        logger.info(f"📄 JSON: {json_path} ({len(works)} 条)")

        csv_fields = [
            "id", "doi", "title", "publication_year", "publication_date",
            "type", "journal", "cited_by_count", "referenced_works_count",
            "abstract", "authors", "institutions", "concepts", "keywords",
            "relevance", "landing_page_url", "source_api", "is_oa", "oa_url",
        ]
        csv_path = self.out_dir / "works.csv"
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=csv_fields)
            writer.writeheader()
            for w in works:
                row = dict(w)
                row["authors"] = "; ".join(w["authors"])
                row["institutions"] = "; ".join(w["institutions"])
                row["concepts"] = "; ".join(
                    f"{c['name']}({c['score']})" for c in w["concepts"])
                row["keywords"] = "; ".join(w["keywords"])
                writer.writerow({k: row.get(k, "") for k in csv_fields})
        logger.info(f"📊 CSV: {csv_path} ({len(works)} 条)")

        # 先更新组状态，再生成 summary（让 summary 显示最新累计状态）
        if self.used_groups:
            self._save_group_status(self.used_groups, self.run_id)
            logger.info(f"🗂 已更新组状态: 组 {self.used_groups} → {self.run_id}")

        self._generate_summary(works)

    def _generate_summary(self, works: List[Dict]) -> None:
        year_dist = {}
        for w in works:
            y = w.get("publication_year")
            if y:
                year_dist[y] = year_dist.get(y, 0) + 1

        by_source = {}
        for w in works:
            by_source[w["source_api"]] = by_source.get(w["source_api"], 0) + 1

        has_abs = sum(1 for w in works if w.get("abstract"))
        has_ref = sum(1 for w in works if w.get("references"))
        has_oa = sum(1 for w in works if w.get("is_oa"))

        # 本次采集的关键词组
        if self.used_groups and sorted(self.used_groups) == sorted(KEYWORD_GROUPS):
            group_desc = f"全部(组{min(KEYWORD_GROUPS)}-{max(KEYWORD_GROUPS)})"
        elif self.used_groups:
            group_desc = ", ".join(
                f"组{g}({len(KEYWORD_GROUPS[g])}词)" for g in self.used_groups)
        else:
            group_desc = "自定义"

        # 全套组使用状态（从状态文件读取）
        status = self._load_group_status()
        status_desc = "；".join(
            f"组{g}: {status.get(str(g), '未采集')}" for g in sorted(KEYWORD_GROUPS))

        lines = [
            "# 英文学术文献采集报告", "",
            f"- 采集时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"- Run ID: {self.run_id}",
            f"- 关键词组: {group_desc}",
            f"- 文献总数: {len(works)}",
            f"- 数据源: {by_source}",
            f"- 有摘要: {has_abs} ({has_abs*100//max(1,len(works))}%)",
            f"- 有引用关系: {has_ref}",
            f"- 可下载全文(OA): {has_oa} ({has_oa*100//max(1,len(works))}%)",
            "", f"- 组别使用状态: {status_desc}",
            "", "## 年份分布", "",
        ]
        for y in sorted(year_dist, reverse=True):
            lines.append(f"- {y}: {year_dist[y]} 篇")

        summary_path = self.out_dir / "summary.md"
        summary_path.write_text("\n".join(lines), encoding="utf-8")
        logger.info(f"📋 报告: {summary_path}")

    # ================================================================
    # 全文下载（OpenAlex content API，需 API key）
    # ================================================================

    def download_fulltext(self, works: List[Dict],
                          fmt: str = "grobid-xml",
                          limit: Optional[int] = None) -> int:
        """下载开放获取文献的全文

        优先 content API（需 key）。pdf 模式 content API 无索引时，
        尝试从 oa_url（best_oa_location.pdf_url）直接下载兜底。
        下载状态记录到 fulltext/download_log.json，支持断点续传与追溯。

        Args:
            works: 采集到的文献列表（含 source_api / id / is_oa / oa_url）
            fmt: "pdf"(优先,通用) | "grobid-xml"(结构化全文) | "both"(两者都下,pdf 优先)
            limit: 只下载前 N 篇 OA 文献（小批量测试用，默认全部）

        Returns:
            下载成功（含断点续传跳过）的篇数
        """
        if not self.api_keys:
            logger.warning(
                "⚠️ 未设置 OpenAlex API key，跳过全文下载。\n"
                "   免费注册: https://openalex.org/users 后在脚本顶部 "
                "OPENALEX_API_KEYS 或 --api-key 填入（多个 key 用逗号分隔）。"
            )
            return 0

        oa_works = [
            w for w in works
            if w.get("source_api") == "openalex"
            and w.get("id", "").startswith("W")
            and w.get("is_oa")
        ]
        if not oa_works:
            logger.info("无可下载全文的 OA 文献（OpenAlex 源）")
            return 0
        total_oa = len(oa_works)  # run 里 OA 总数（统计用）

        # 格式策略：
        #   both        → pdf + grobid-xml 都尽力下载（两个都想要）
        #   pdf         → pdf 优先，pdf 无果时用 grobid-xml 兜底
        #   grobid-xml  → xml 优先，xml 无果时用 pdf 兜底
        if fmt == "both":
            formats = ["pdf", "grobid-xml"]
            fallback = None
        else:
            formats = [fmt]
            fallback = "grobid-xml" if fmt == "pdf" else "pdf"
        fulltext_dir = self.out_dir / "fulltext"
        fulltext_dir.mkdir(parents=True, exist_ok=True)

        # 下载记录（断点续传 + 可追溯）
        log_path = fulltext_dir / "download_log.json"
        log = {}
        if log_path.exists():
            try:
                log = json.loads(log_path.read_text(encoding="utf-8"))
            except Exception:
                log = {}

        # 增量过滤：跳过已处理的文献，区分"已成功" / "已无全文"
        # --fulltext-limit N 每次推进到"尚未处理"的前 N 篇
        downloaded = 0   # 已成功下载（历史 ok + 文件存在 + 本次成功）
        not_found = 0    # 已确认无全文（历史 not_found + 本次）
        pending = []
        for w in oa_works:
            oa_id = w["id"]
            entry = log.get(oa_id, {})
            success = any(entry.get(f) == "ok" for f in entry)
            if not success:
                for f in formats:
                    ext = "xml" if f == "grobid-xml" else "pdf"
                    p = fulltext_dir / f"{oa_id}.{ext}"
                    if p.exists() and p.stat().st_size > 0:
                        success = True
                        break
            if success:
                downloaded += 1
                continue
            if any(entry.get(f) == "not_found" for f in entry):
                not_found += 1
                continue
            pending.append(w)

        oa_works = pending[:limit] if limit else pending
        if not oa_works:
            logger.info(
                f"✅ 增量检查: 待下 OA 均已处理，无需继续"
                f" (成功 {downloaded} | 无全文 {not_found})"
            )
            return downloaded

        logger.info(
            f"📥 开始下载全文: {len(oa_works)} 篇待下载 "
            f"(已成功 {downloaded} | 无全文 {not_found} | 格式 {formats})"
        )
        fail = 0
        failures: List[str] = []  # 失败明细（写 download_fail.log + 精简报错）
        budget_stop = False  # content API 额度用完时停止

        for i, w in enumerate(oa_works, 1):
            oa_id = w["id"]
            entry = log.setdefault(oa_id, {})
            got = False  # 本篇是否至少成功一个格式

            # 1. 主格式（both 时依次两个）
            for f in formats:
                ext = "xml" if f == "grobid-xml" else "pdf"
                out_path = fulltext_dir / f"{oa_id}.{ext}"

                # 断点续传：记录成功 或 文件已存在
                if entry.get(f) == "ok" or (
                        out_path.exists() and out_path.stat().st_size > 0):
                    if entry.get(f) != "ok":
                        entry[f] = "ok"
                    got = True
                    downloaded += 1
                    continue

                # content API 下载
                status = self._download_content_api(oa_id, f, out_path)
                if status == "ok":
                    entry[f] = "ok"
                    got = True
                    downloaded += 1
                elif status == "budget":
                    budget_stop = True
                    break  # 额度用完 → 停止下载
                elif status == "not_found":
                    # pdf + content API 无索引 → oa_url 兜底
                    if (f == "pdf" and w.get("oa_url")
                            and self._download_oa_url(w["oa_url"], out_path)):
                        entry[f] = "ok"
                        got = True
                        downloaded += 1
                    else:
                        entry[f] = "not_found"
                        not_found += 1
                        failures.append(f"{oa_id} | 无全文")
                else:
                    entry[f] = "fail"
                    fail += 1
                    failures.append(f"{oa_id} | 下载失败")
            if budget_stop:
                break

            # 2. 兜底格式：主格式没成功且非 both → 试另一格式补充
            if fallback and not got and entry.get(fallback) not in ("ok",):
                f = fallback
                ext = "xml" if f == "grobid-xml" else "pdf"
                out_path = fulltext_dir / f"{oa_id}.{ext}"

                if entry.get(f) == "ok" or (
                        out_path.exists() and out_path.stat().st_size > 0):
                    entry[f] = "ok"
                    downloaded += 1
                    continue
                status = self._download_content_api(oa_id, f, out_path)
                if status == "ok":
                    entry[f] = "ok"
                    downloaded += 1
                elif status == "budget":
                    budget_stop = True
                    break
                elif status == "not_found":
                    if (f == "pdf" and w.get("oa_url")
                            and self._download_oa_url(w["oa_url"], out_path)):
                        entry[f] = "ok"
                        downloaded += 1
                    else:
                        entry[f] = "not_found"
                        not_found += 1
                        failures.append(f"{oa_id} | 无全文")
                else:
                    entry[f] = "fail"
                    fail += 1
                    failures.append(f"{oa_id} | 下载失败")

            if i % 20 == 0 or i == len(oa_works):
                logger.info(f"  全文进度: {i}/{len(oa_works)} | "
                            f"✓{downloaded} ✗{fail} (无索引 {not_found})")
            time.sleep(self.delay)
            if budget_stop:
                logger.info("   ⏹️ 由于 content API 额度用完，停止本次下载")
                break

        # 保存下载记录
        log_path.write_text(
            json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"✅ 全文下载完成: {downloaded} 篇 | 失败 {fail} 篇 | "
                    f"无索引跳过 {not_found} 篇")
        logger.info(f"   📋 下载记录: {log_path}")

        # 完整下载统计（文献级，从 download_log 准确统计）+ 写入 summary.md
        done_cnt = sum(1 for e in log.values() if "ok" in e.values())
        nf_cnt = sum(1 for e in log.values()
                     if "ok" not in e.values() and "not_found" in e.values())
        fail_cnt = sum(1 for e in log.values()
                       if "ok" not in e.values()
                       and "not_found" not in e.values()
                       and "fail" in e.values())
        remaining = max(0, total_oa - len(log))  # 从未处理过的 OA
        pct = done_cnt * 100 // max(1, total_oa)
        logger.info(
            f"📊 下载统计: 总OA {total_oa} | ✓成功 {done_cnt} ({pct}%) | "
            f"✗失败 {fail_cnt} | 无全文 {nf_cnt} | ⏳未下载 {remaining}"
        )
        if budget_stop:
            logger.info(
                "   ⚠️ content API 额度用完中止，未下载部分下次重跑自动续爬")
        summary_path = self.out_dir / "summary.md"
        if summary_path.exists():
            try:
                with open(summary_path, "a", encoding="utf-8") as f:
                    f.write(
                        f"\n## 全文下载统计 "
                        f"({datetime.now().strftime('%Y-%m-%d %H:%M')})\n"
                        f"- 成功 {done_cnt} | 失败 {fail_cnt} | "
                        f"无全文 {nf_cnt} | 未下载 {remaining} "
                        f"(总OA {total_oa}，进度 {pct}%)\n"
                    )
            except Exception:
                pass

        # 失败明细：写 download_fail.log（追加累积）+ 终端精简报错
        if failures:
            fail_log = fulltext_dir / "download_fail.log"
            # 按文献去重（一篇可能 pdf+xml 都失败），保留首个原因
            seen_fail: Dict[str, str] = {}
            for line in failures:
                wid = line.split(" | ")[0]
                if wid not in seen_fail:
                    seen_fail[wid] = line
            with open(fail_log, "a", encoding="utf-8") as f:
                f.write(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                        f"Download Run: {len(oa_works)} 篇 | 格式 {formats}\n")
                for line in seen_fail.values():
                    f.write(f"  ✗ {line}\n")
            logger.info(f"   ❌ 失败明细: {fail_log} ({len(seen_fail)} 篇)")
            # 终端只显示前 15 条，避免刷屏
            for line in list(seen_fail.values())[:15]:
                logger.info(f"     ✗ {line}")
            if len(seen_fail) > 15:
                logger.info(f"     ... 其余 {len(seen_fail)-15} 条见 {fail_log}")

        # 全部无索引时给出友好提示（避免误判为故障）
        if downloaded == 0 and not_found > 0:
            logger.info(
                "💡 提示: 这批 OA 文献在 content 索引可能无全文"
                "（正常现象，命中率约 50%，Elsevier/部分仓库常见）。"
                "可加大 --fulltext-limit 多测几篇，或对 --out-dir "
                "配合完整下载观察真实成功率。"
            )
        return downloaded

    def _download_content_api(self, oa_id: str, fmt: str,
                              out_path: Path) -> str:
        """从 content API 下载单个格式（一个 key 用完再切下一个）

        当前 key 额度用完 → 切换下一个 key 继续；所有 key 用尽才返回 budget。

        Returns: ok / not_found / budget(所有 key 用尽) / fail
        """
        n_keys = max(1, len(self.api_keys))
        for _ in range(n_keys):
            key = self._next_api_key()
            url = f"{CONTENT_BASE_URL}/{oa_id}.{fmt}?api_key={key}"
            try:
                resp = self.session.get(url, timeout=60)
                if resp.status_code == 200 and len(resp.content) > 100:
                    content = resp.content
                    # content API 的 XML 是 gzip 压缩的，自动解压
                    if content[:2] == b"\x1f\x8b":
                        try:
                            content = gzip.decompress(content)
                        except OSError:
                            pass
                    out_path.write_bytes(content)
                    return "ok"
                if resp.status_code == 404:
                    return "not_found"
                if resp.status_code == 429:
                    if ("Insufficient budget" in resp.text
                            or "budget" in resp.text):
                        # 当前 key 额度用完 → 切下一个 key 继续
                        if self._switch_api_key():
                            logger.warning(
                                f"  某 key 额度用完，切换到 "
                                f"key{self._key_idx + 1} 继续")
                            continue
                        return "budget"  # 所有 key 用尽
                    time.sleep(10)  # 普通限流退避
                    return "fail"
                if resp.status_code in (502, 503, 504):
                    time.sleep(5)  # 服务器繁忙，重试一次
                    resp = self.session.get(url, timeout=60)
                    if resp.status_code == 200 and len(resp.content) > 100:
                        out_path.write_bytes(resp.content)
                        return "ok"
                    return "fail"
                return "fail"
            except requests.RequestException:
                return "fail"
        return "budget"  # 所有 key 额度用完

    @staticmethod
    def _download_oa_url(oa_url: str, out_path: Path) -> bool:
        """从开放获取 URL 下载 PDF（不依赖 URL 后缀，用 %PDF magic 判定）"""
        url = oa_url.strip()
        if not url:
            return False
        try:
            resp = requests.get(
                url, timeout=60, verify=False,
                headers={"User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
                )},
            )
            # 校验是真正的 PDF（%PDF magic）而非 HTML 验证页
            if (resp.status_code == 200
                    and resp.content[:4] == b"%PDF"
                    and len(resp.content) > 1000):
                out_path.write_bytes(resp.content)
                return True
        except requests.RequestException:
            pass
        return False


def main():
    import argparse
    parser = argparse.ArgumentParser(description="英文学术文献采集器")
    parser.add_argument("--source", choices=["openalex", "crossref", "all"],
                        default="openalex",
                        help="数据源: openalex(默认,字段全) | crossref(无限量) | all(两者)")
    parser.add_argument("--keywords", nargs="+", default=None)
    parser.add_argument("--years", nargs="+", type=int, default=None)
    parser.add_argument("--has-abstract", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--max-pages", type=int, default=None,
                        help="每关键词最大翻页数(每页200条)。OpenAlex 按相关性排序，"
                             "限制深度避免额度耗在无关深页")
    parser.add_argument("--min-relevance", type=float, default=None,
                        help="相关性触底阈值(OpenAlex)。relevance 低于该值的噪声"
                             "结果自动停止翻页，把高相关元数据挖完。"
                             "参考: 首页median≈420, 第5页≈153, 第11页≈101")
    parser.add_argument("--mailto", default=MAILTO)
    parser.add_argument("--api-key", default="",
                        help="OpenAlex API key（可用多个，逗号分隔，轮换提升额度。"
                             "免费注册 openalex.org/users）")
    parser.add_argument("--fulltext", choices=["grobid-xml", "pdf", "both", "none"],
                        default="none",
                        help="采集后下载全文: pdf(优先,通用) | grobid-xml(结构化,主题建模) "
                             "| both(两者都下,pdf优先) | none")
    parser.add_argument("--run-id", type=str, default=None,
                        help="复用已有 run 的数据（读取其 works.json 下载全文，不重新采集）")
    parser.add_argument("--resume-run", type=str, default=None,
                        help="从指定 run 继续采集: 仅跳过该 run 已采集的文献去重")
    parser.add_argument("--group", type=int, nargs="+", default=None,
                        help="指定关键词组号(1-5)，如 --group 1 或 --group 4 5。"
                             "不指定则爬全部内置关键词")
    parser.add_argument("--expand-references", action="store_true",
                        help="从已采文献的引用列表扩展采集（引文追踪，OpenAlex）。"
                             "需配合 --run-id 指定来源 run，新增文献写回该 run")
    parser.add_argument("--expand-limit", type=int, default=None,
                        help="引文扩展最多查询的引用 W ID 数（默认全部）。"
                             "已采文献引用常达几十万条，建议设 500-2000 控制额度")
    parser.add_argument("--expand-save-every", type=int, default=5000,
                        help="引文扩展每处理多少引用落盘一次（默认 5000）。"
                             "大任务（数万引用）建议保持 5000-10000，防内存峰值/中断丢失")
    parser.add_argument("--out-dir", type=str, default=None,
                        help="指定输出目录（默认 data/processed/academic/{run_id}）。"
                             "下载全文/采集数据都写入该目录")
    parser.add_argument("--fulltext-limit", type=int, default=None,
                        help="下载全文时只处理前 N 篇 OA 文献（小批量测试用，默认全部）")
    args = parser.parse_args()

    collector = AcademicCollector(
        source=args.source, mailto=args.mailto, api_key=args.api_key,
        run_id=args.run_id, resume_run=args.resume_run,
        max_pages=args.max_pages, min_relevance=args.min_relevance,
        out_dir=args.out_dir,
    )

    # ---- 引文扩展：从已采文献的引用关系扩展采集 ----
    if args.expand_references:
        if not args.run_id:
            # 未指定来源 → 自动用最新 run
            academic_dir = Path("data/processed/academic")
            run_dirs = sorted(
                [d.name for d in academic_dir.iterdir() if d.is_dir()],
                reverse=True,
            )
            if not run_dirs:
                raise SystemExit("未找到任何 run，请先采集元数据")
            args.run_id = run_dirs[0]
            collector = AcademicCollector(
                source=args.source, mailto=args.mailto, api_key=args.api_key,
                run_id=args.run_id, max_pages=args.max_pages,
                min_relevance=args.min_relevance,
            )
        works = collector.load_run()
        logger.info(f"📂 引文扩展来源: run {collector.run_id} ({len(works)} 篇)")
        # 内部分批处理 + 分批落盘（每 save_every 引用保存一次）
        new_count = collector.expand_from_references(
            works, args.expand_limit, args.expand_save_every)
        if new_count:
            logger.info(
                f"✅ 引文扩展完成: 新增 {new_count} 篇，"
                f"已分批保存到 run {collector.run_id}")
        return

    # ---- 复用已有 run：只下载全文 ----
    if args.run_id:
        works = collector.load_run()
        logger.info(f"📂 复用已有 run {args.run_id}: 加载 {len(works)} 条文献")
        if args.fulltext != "none":
            limit_hint = f" (前 {args.fulltext_limit} 篇)" if args.fulltext_limit else ""
            logger.info(f"📥 开始下载全文{limit_hint}...")
            collector.download_fulltext(works, fmt=args.fulltext,
                                        limit=args.fulltext_limit)
        else:
            logger.info("   提示: 加 --fulltext grobid-xml 可下载全文")
        return

    # 指定关键词组时，先显示各组使用状态供参考
    if args.group:
        collector.print_group_status()

    works = collector.collect_all(
        keywords=args.keywords, years=args.years,
        has_abstract=args.has_abstract, limit=args.limit,
        group=args.group,
    )
    if works:
        collector.save(works)
        if args.fulltext != "none":
            collector.download_fulltext(works, fmt=args.fulltext,
                                        limit=args.fulltext_limit)
        logger.info(f"✅ 数据目录: {collector.out_dir}")
    else:
        logger.warning("未采集到任何文献")


if __name__ == "__main__":
    main()

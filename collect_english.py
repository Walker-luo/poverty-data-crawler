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

# OpenAlex 全文下载 API（content API）
# 免费注册获取 key: https://openalex.org/users
OPENALEX_API_KEY = "dke7UcbHWbmO6iYa9rfXOt"              # TODO: 填入你的 OpenAlex API key
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

    def __init__(self, session: requests.Session, mailto: str, delay: float,
                 api_key: str = ""):
        self.session = session
        self.mailto = mailto
        self.delay = delay
        self.api_key = api_key
        self.name = "openalex"

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

    def _request(self, params: Dict) -> Optional[Dict]:
        # 带 API key 可提升额度（免费 $0.1/天 → 带 key $1/天，约10倍）
        if self.api_key:
            params["api-key"] = self.api_key
        resp = self.session.get(self.BASE_URL, params=params, timeout=30)
        if resp.status_code == 429:
            # 区分：额度用完 vs 普通限流
            if "Insufficient budget" in resp.text or "budget" in resp.text:
                raise BudgetExhausted()
            logger.warning(f"OpenAlex 限流(429)，等待 10s...")
            time.sleep(10)
            return None
        resp.raise_for_status()
        return resp.json()

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

    def _request(self, params: Dict) -> Optional[Dict]:
        resp = self.session.get(self.BASE_URL, params=params, timeout=30)
        if resp.status_code == 429:
            logger.warning(f"Crossref 限流(429)，等待 10s...")
            time.sleep(10)
            return None
        resp.raise_for_status()
        return resp.json()

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
                 min_relevance: Optional[float] = None):
        self.mailto = mailto
        self.delay = delay
        self.api_key = api_key or OPENALEX_API_KEY
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
                                               self.api_key))
        if source in ("crossref", "all"):
            self.sources.append(CrossrefSource(self.session, mailto, delay))

        # run_id 已存在 → 复用其目录（用于对已有数据下载全文），否则新建
        self.run_id = run_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        self.out_dir = Path(f"data/processed/academic/{self.run_id}")
        if not run_id:
            self.out_dir.mkdir(parents=True, exist_ok=True)

    def load_run(self) -> List[Dict]:
        """读取已有 run 的 works.json"""
        path = self.out_dir / "works.json"
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
            keywords = ENGLISH_KEYWORDS

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

    def save(self, works: List[Dict]) -> None:
        json_path = self.out_dir / "works.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(works, f, ensure_ascii=False, indent=2)
        logger.info(f"📄 JSON: {json_path} ({len(works)} 条)")

        csv_fields = [
            "id", "doi", "title", "publication_year", "publication_date",
            "type", "journal", "cited_by_count", "referenced_works_count",
            "abstract", "authors", "institutions", "concepts", "keywords",
            "landing_page_url", "source_api", "is_oa", "oa_url",
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
        if self.used_groups:
            group_desc = ", ".join(
                f"组{g}({len(KEYWORD_GROUPS[g])}词)" for g in self.used_groups)
        else:
            group_desc = "全部"

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
                          fmt: str = "grobid-xml") -> int:
        """下载开放获取文献的全文

        优先 content API（需 key）。content API 无索引时，
        pdf 模式会尝试从 oa_url（best_oa_location.pdf_url）直接下载兜底。

        Args:
            works: 采集到的文献列表（含 source_api / id / is_oa / oa_url）
            fmt: "grobid-xml"(结构化全文,适合主题建模) 或 "pdf"(原始文件)

        Returns:
            下载成功（含断点续传跳过）的篇数
        """
        if not self.api_key:
            logger.warning(
                "⚠️ 未设置 OpenAlex API key，跳过全文下载。\n"
                "   免费注册: https://openalex.org/users 后在脚本顶部 "
                "OPENALEX_API_KEY 或 --api-key 填入。"
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

        ext = "xml" if fmt == "grobid-xml" else "pdf"
        fulltext_dir = self.out_dir / "fulltext"
        fulltext_dir.mkdir(parents=True, exist_ok=True)

        logger.info(
            f"📥 开始下载全文: {len(oa_works)} 篇 OA 文献 (格式 {fmt})"
        )
        downloaded = fail = not_found = 0

        for i, w in enumerate(oa_works, 1):
            oa_id = w["id"]
            out_path = fulltext_dir / f"{oa_id}.{ext}"

            # 断点续传
            if out_path.exists() and out_path.stat().st_size > 0:
                downloaded += 1
                continue

            # 1. content API 优先
            ok = status = None
            url = f"{CONTENT_BASE_URL}/{oa_id}.{fmt}?api_key={self.api_key}"
            try:
                resp = self.session.get(url, timeout=60)
                status = resp.status_code
                if status == 200 and len(resp.content) > 100:
                    # content API 的 XML 是 gzip 压缩的，自动解压
                    content = resp.content
                    if content[:2] == b"\x1f\x8b":
                        try:
                            content = gzip.decompress(content)
                        except OSError:
                            pass  # 解压失败保留原始内容
                    out_path.write_bytes(content)
                    downloaded += 1
                    ok = True
                elif status == 404:
                    not_found += 1  # content API 无此全文索引（正常现象）
                    ok = False
                else:
                    fail += 1
                    ok = False
            except requests.RequestException as e:
                logger.warning(f"  {oa_id}: content API 异常 {type(e).__name__}")
                fail += 1
                ok = False

            # 2. content API 无结果 → 从 oa_url 直接下载 PDF 兜底
            if not ok and status == 404 and w.get("oa_url"):
                logger.debug(f"  {oa_id}: content API 无索引，尝试 oa_url 兜底")
                pdf_path = fulltext_dir / f"{oa_id}.pdf"
                if (pdf_path.exists() and pdf_path.stat().st_size > 0) \
                        or self._download_oa_url(w["oa_url"], pdf_path):
                    downloaded += 1
                    not_found = max(0, not_found - 1)

            if i % 20 == 0 or i == len(oa_works):
                logger.info(f"  全文进度: {i}/{len(oa_works)} | "
                            f"✓{downloaded} ✗{fail} (无索引 {not_found})")

            time.sleep(self.delay)

        logger.info(f"✅ 全文下载完成: {downloaded} 篇 | 失败 {fail} 篇 | "
                    f"无索引跳过 {not_found} 篇")
        logger.info(f"   目录: {fulltext_dir}")
        return downloaded

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
                        help="OpenAlex API key（全文下载用，免费注册 openalex.org/users）")
    parser.add_argument("--fulltext", choices=["grobid-xml", "pdf", "none"],
                        default="none",
                        help="采集后下载全文: grobid-xml(结构化,主题建模推荐) | pdf | none")
    parser.add_argument("--run-id", type=str, default=None,
                        help="复用已有 run 的数据（读取其 works.json 下载全文，不重新采集）")
    parser.add_argument("--resume-run", type=str, default=None,
                        help="从指定 run 继续采集: 仅跳过该 run 已采集的文献去重")
    parser.add_argument("--group", type=int, nargs="+", default=None,
                        help="指定关键词组号(1-3)，如 --group 1 或 --group 1 2。"
                             "不指定则爬全部内置关键词")
    args = parser.parse_args()

    collector = AcademicCollector(
        source=args.source, mailto=args.mailto, api_key=args.api_key,
        run_id=args.run_id, resume_run=args.resume_run,
        max_pages=args.max_pages, min_relevance=args.min_relevance,
    )

    # ---- 复用已有 run：只下载全文 ----
    if args.run_id:
        works = collector.load_run()
        logger.info(f"📂 复用已有 run {args.run_id}: 加载 {len(works)} 条文献")
        if args.fulltext != "none":
            collector.download_fulltext(works, fmt=args.fulltext)
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
            collector.download_fulltext(works, fmt=args.fulltext)
        logger.info(f"✅ 数据目录: {collector.out_dir}")
    else:
        logger.warning("未采集到任何文献")


if __name__ == "__main__":
    main()

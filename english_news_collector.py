#!/usr/bin/env python3
"""Collect English news about China's poverty governance and rural development.

Examples:
    python english_news_collector.py --limit 5
    python english_news_collector.py --keywords "China poverty" --limit 10
    python english_news_collector.py --run-id 20260916_120000 --download --limit 20

The collector is intentionally independent from the Chinese news pipeline. It
stores data under data/processed/news/en/{run_id}/.
"""

import argparse
import base64
import csv
import datetime as dt
import json
import logging
import os
import re
import time
import warnings
import xml.etree.ElementTree as ET
from html import unescape
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("english_news")

# English news is kept in a separate subdirectory so it can live alongside
# Chinese news under data/processed/news without mixing the two datasets.
BASE_DIR = Path("data/processed/news/en")
# Backward compatibility for runs created before the directory was moved.
LEGACY_BASE_DIR = Path("data/processed/env_news")
# Server proxy convention. Enable with ``--use-proxy`` or override with
# ``--proxy`` / ``NEWS_PROXY``; direct access remains the default on laptops.
DEFAULT_PROXY_HOST = "127.0.0.1"
DEFAULT_PROXY_PORT = 7897
KEYWORDS = [
    "China poverty", "China poverty alleviation", "China poverty reduction",
    "China poverty eradication", "targeted poverty alleviation China",
    "China rural revitalization", "China rural development",
    "China common prosperity", "China poverty governance",
    "China rural poverty", "China poverty reduction policy",
    "China anti-poverty", "China development poverty",
]

CHINA_KEYWORDS_BY_LANGUAGE = {
    "en": KEYWORDS,
    "zh": ["扶贫", "脱贫", "减贫", "贫困治理", "乡村振兴", "共同富裕"],
}

GLOBAL_KEYWORDS_BY_LANGUAGE = {
    "en": [
        "poverty alleviation", "poverty reduction", "poverty eradication",
        "rural development", "social protection", "inclusive development",
        "multidimensional poverty", "anti-poverty policy",
    ],
    "es": [
        "reducción de la pobreza", "erradicación de la pobreza",
        "desarrollo rural", "protección social", "desarrollo inclusivo",
    ],
    "fr": [
        "réduction de la pauvreté", "éradication de la pauvreté",
        "développement rural", "protection sociale", "développement inclusif",
    ],
    "pt": [
        "redução da pobreza", "erradicação da pobreza",
        "desenvolvimento rural", "proteção social", "desenvolvimento inclusivo",
    ],
    "ar": [
        "الحد من الفقر", "القضاء على الفقر", "التنمية الريفية",
        "الحماية الاجتماعية", "التنمية الشاملة",
    ],
    "hi": [
        "गरीबी उन्मूलन", "गरीबी कम करना", "ग्रामीण विकास", "सामाजिक सुरक्षा",
    ],
    "id": [
        "pengentasan kemiskinan", "pengurangan kemiskinan",
        "pembangunan pedesaan", "perlindungan sosial",
    ],
    "vi": [
        "giảm nghèo", "xóa đói giảm nghèo", "phát triển nông thôn",
        "an sinh xã hội",
    ],
}

DEFAULT_GLOBAL_LANGUAGES = ["en", "es", "fr", "pt", "ar", "hi", "id", "vi"]
DEFAULT_ALL_LANGUAGES = ["en", "zh", "es", "fr", "pt", "ar", "hi", "id", "vi"]

MEDIA = {
    "CGTN": ["cgtn.com"],
    "Xinhua English": ["english.news.cn", "xinhuanet.com"],
    "People's Daily Online": ["en.people.cn"],
    "China Daily": ["chinadaily.com.cn"],
    "english.gov.cn": ["english.gov.cn"],
    "Reuters": ["reuters.com"],
    "BBC": ["bbc.com", "bbc.co.uk"],
    "The Guardian": ["theguardian.com"],
    "SCMP": ["scmp.com"],
    "The Diplomat": ["thediplomat.com"],
    "Sixth Tone": ["sixthtone.com"],
    "Caixin Global": ["caixinglobal.com"],
}

FIELDS = ["id", "title", "url", "source", "date", "pub_date", "summary",
          "is_official", "source_type", "search_keyword", "language",
          "country_focus", "scope"]


def domain_matches(url: str, domains: Iterable[str]) -> bool:
    host = urlparse(url).netloc.lower().split(":", 1)[0]
    return any(host == d or host.endswith("." + d) for d in domains)


def source_from_url(url: str, fallback: str = "") -> str:
    for name, domains in MEDIA.items():
        if domain_matches(url, domains):
            return name
    return clean_source_name(fallback) or urlparse(url).netloc


def clean_source_name(value: str) -> str:
    """Remove Bing relative-time suffixes from source labels."""
    value = re.sub(r"\s+", " ", value or "").strip(" ·|-")
    if not value:
        return ""
    if re.fullmatch(
        r"\d+\s*(?:s|m|h|d|w|mo|mon|y|seconds?|minutes?|hours?|days?|weeks?|months?|years?)",
        value,
        flags=re.IGNORECASE,
    ):
        return ""
    value = re.split(
        r"\s+(?:\d+\s*(?:s|m|h|d|w|mo|mon|y)|\d+\s*"
        r"(?:seconds?|minutes?|hours?|days?|weeks?|months?|years?)|"
        r"today|yesterday)\b",
        value,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    return value.strip(" ·|-")


def normalize_proxy_url(proxy: str = "", host: str = DEFAULT_PROXY_HOST,
                        port: int = DEFAULT_PROXY_PORT) -> str:
    """Normalize CLI proxy input into a requests-compatible URL."""
    proxy = (proxy or "").strip()
    if not proxy:
        return ""
    if proxy.lower() in {"none", "off", "direct", "false"}:
        return ""
    if proxy.isdigit():
        return f"http://{host}:{proxy}"
    if "://" not in proxy:
        proxy = f"http://{proxy}"
    return proxy.rstrip("/")


def proxy_label(proxy: str) -> str:
    """Return a credential-free proxy label for logs and summaries."""
    if not proxy:
        return "direct/environment"
    parsed = urlparse(proxy)
    return f"{parsed.scheme}://{parsed.hostname or 'unknown'}:{parsed.port or ''}"


def build_query_plan(custom_keywords: Optional[List[str]], scope: str,
                     languages: Optional[List[str]], countries: Optional[List[str]]) -> Tuple[List[str], Dict[str, Dict], Dict]:
    """Build query strings plus language/country metadata without exploding defaults."""
    if custom_keywords:
        metadata = {
            keyword: {
                "language": languages[0] if languages and len(languages) == 1 else "custom",
                "country_focus": "",
                "scope": scope,
            }
            for keyword in custom_keywords
        }
        return custom_keywords, metadata, {
            "scope": scope, "languages": languages or ["custom"],
            "countries": countries or [], "custom_keywords": True,
        }

    if languages:
        selected_languages = languages
    elif scope == "china":
        selected_languages = ["en"]
    elif scope == "global":
        selected_languages = DEFAULT_GLOBAL_LANGUAGES
    else:
        selected_languages = DEFAULT_ALL_LANGUAGES
    selected_countries = countries or []
    queries: List[str] = []
    metadata: Dict[str, Dict] = {}

    def add_query(query: str, language: str, country: str, query_scope: str) -> None:
        if query not in metadata:
            queries.append(query)
            metadata[query] = {
                "language": language,
                "country_focus": country,
                "scope": query_scope,
            }

    if scope in {"china", "all"}:
        for language in selected_languages:
            for keyword in CHINA_KEYWORDS_BY_LANGUAGE.get(language, []):
                add_query(keyword, language, "China", "china")

    if scope in {"global", "all"}:
        for language in selected_languages:
            for keyword in GLOBAL_KEYWORDS_BY_LANGUAGE.get(language, []):
                add_query(keyword, language, "", "global")
                for country in selected_countries:
                    country_name = country.replace("_", " ")
                    add_query(f"{keyword} {country_name}", language, country_name, "global")

    if not queries:
        raise ValueError(f"没有可用查询词: scope={scope}, languages={selected_languages}")
    return queries, metadata, {
        "scope": scope, "languages": selected_languages,
        "countries": selected_countries, "custom_keywords": False,
    }


def parse_date(url: str, text: str = "") -> str:
    patterns = [
        r"/(20\d{2})[-/](\d{1,2})[-/](\d{1,2})(?:/|[-_])",
        r"/(20\d{2})/(\d{1,2})(\d{1,2})(?:/|[-_])",
        r"/(20\d{2})(\d{2})(\d{2})(?:/|[-_])",
    ]
    for pattern in patterns:
        m = re.search(pattern, url)
        if m:
            y, mo, day = (int(x) for x in m.groups())
            try:
                return dt.date(y, mo, day).isoformat()
            except ValueError:
                pass
    m = re.search(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b", text)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return ""


class EnglishNewsCollector:
    BASE_URLS = {
        "www": "https://www.bing.com/news/search",
        "cn": "https://cn.bing.com/news/search",
    }
    GOOGLE_RSS_URL = "https://news.google.com/rss/search"

    def __init__(self, run_id: Optional[str] = None, delay: float = 1.5,
        timeout: int = 20, bing_host: str = "auto",
        rss_fallback: bool = True, rss_threshold: int = 5,
        google_fallback: bool = True, proxy: str = "",
        proxy_host: str = DEFAULT_PROXY_HOST, proxy_port: int = DEFAULT_PROXY_PORT,
        use_proxy: bool = False):
        self.run_id = run_id or dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.data_dir = self._resolve_data_dir(self.run_id)
        self.articles_dir = self.data_dir / "articles"
        self.download_report = self.data_dir / "download_summary.md"
        self.debug_dir = self.data_dir / "debug"
        self.fail_log = self.data_dir / "fail.log"
        self.delay = delay
        self.timeout = timeout
        self.bing_host = bing_host
        self.rss_fallback = rss_fallback
        self.rss_threshold = max(1, rss_threshold)
        self.google_fallback = google_fallback
        explicit_proxy = normalize_proxy_url(proxy, proxy_host, proxy_port)
        if use_proxy and not explicit_proxy:
            explicit_proxy = f"http://{proxy_host}:{proxy_port}"
        self.proxy_url = explicit_proxy
        env_proxy_enabled = any(os.environ.get(name) for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"))
        self.proxy_display = proxy_label(explicit_proxy) if explicit_proxy else (
            "environment" if env_proxy_enabled else "direct"
        )
        self.debug_saved = 0
        self.debug_limit = 20
        self.search_stats = {
            "requests": 0,
            "failures": 0,
            "abnormal_empty": 0,
            "debug_pages": 0,
            "rss_requests": 0,
            "rss_hits": 0,
            "html_results": 0,
            "rss_results": 0,
            "google_rss_requests": 0,
            "google_rss_hits": 0,
            "google_rss_results": 0,
            "duplicate_results": 0,
            "no_new_pages": 0,
            "last_reason": "",
        }
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 Chrome/124 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        })
        if explicit_proxy:
            self.session.proxies.update({"http": explicit_proxy, "https": explicit_proxy})
            logger.info("网络代理已启用: %s", self.proxy_display)
        elif any(os.environ.get(name) for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")):
            logger.info("将沿用环境变量代理（未打印具体地址）")
        else:
            logger.info("网络代理: 未显式启用，使用直连")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.articles_dir.mkdir(parents=True, exist_ok=True)

    def _search_urls(self) -> List[str]:
        if self.bing_host == "www":
            return [self.BASE_URLS["www"]]
        if self.bing_host == "cn":
            return [self.BASE_URLS["cn"]]
        return [self.BASE_URLS["www"], self.BASE_URLS["cn"]]

    @staticmethod
    def _resolve_data_dir(run_id: str) -> Path:
        """Resolve a run directory, preferring the new English-news path.

        A supplied run ID that only exists in the old ``env_news`` location is
        kept there so existing runs can still be downloaded or resumed.
        """
        new_dir = BASE_DIR / run_id
        legacy_dir = LEGACY_BASE_DIR / run_id
        if not new_dir.exists() and legacy_dir.exists():
            logger.warning("检测到旧英文新闻目录，继续使用: %s", legacy_dir)
            return legacy_dir
        return new_dir

    def collect(self, keywords: List[str], years: List[int], pages: int,
                limit: Optional[int] = None, sources: Optional[List[str]] = None,
                query_meta: Optional[Dict[str, Dict]] = None,
                plan_info: Optional[Dict] = None) -> List[Dict]:
        existing = self._load_json()
        seen = {a.get("url") for a in existing if a.get("url")}
        output = existing[:]
        self.plan_info = plan_info or {"scope": "china", "languages": ["en"], "countries": []}
        query_meta = query_meta or {}
        domains: List[str] = []
        if sources:
            for source in sources:
                domains.extend(MEDIA.get(source, [source]))
        total = len(keywords) * len(years)
        done = 0
        if not self.check_search(write_summary=False):
            logger.error("Bing 预检失败，已停止采集。请查看 %s 和 %s/ 下的 HTML 诊断页。",
                         self.fail_log, self.debug_dir)
            self._write_summary(len(output), 0, 0, 0)
            return output

        logger.info("英文新闻采集: %s 个关键词 × %s 年 × %s 页 | 已有 %s 条",
                    len(keywords), len(years), pages, len(existing))
        for year in sorted(years, reverse=True):
            for keyword in keywords:
                done += 1
                query = f"{keyword} {year}"
                page_signatures: Set[Tuple[str, ...]] = set()
                for page in range(pages):
                    cards = self._fetch(query, page)
                    if not cards:
                        break
                    signature = tuple(sorted({card["url"] for card in cards if card.get("url")}))
                    if signature in page_signatures:
                        logger.debug("分页结果重复，停止当前查询: %s page=%s", query, page)
                        break
                    page_signatures.add(signature)
                    page_new = 0
                    for card in cards:
                        url = card["url"]
                        if domains and not domain_matches(url, domains):
                            continue
                        if url in seen:
                            self.search_stats["duplicate_results"] += 1
                            continue
                        seen.add(url)
                        card["search_keyword"] = keyword
                        meta = query_meta.get(keyword, {})
                        card["language"] = meta.get("language", "en")
                        card["country_focus"] = meta.get("country_focus", "")
                        card["scope"] = meta.get("scope", self.plan_info.get("scope", "china"))
                        output.append(card)
                        page_new += 1
                        if limit and len(output) - len(existing) >= limit:
                            self._save(output)
                            logger.info("达到本次 limit=%s，累计新增 %s 条", limit, len(output)-len(existing))
                            return output
                    if page_new == 0:
                        self.search_stats["no_new_pages"] += 1
                        logger.debug("当前页均为重复/过滤结果，继续翻页: %s page=%s", query, page)
                    # 每页落盘，进程中断时保留已经拿到的结果
                    self._save(output)
                if done % 5 == 0 or done == total:
                    logger.info("采集进度: %s/%s | 本 run 总计 %s 条", done, total, len(output))
        self._save(output)
        return output

    def _fetch(self, query: str, page: int) -> List[Dict]:
        time.sleep(self.delay)
        errors = []
        combined: List[Dict] = []
        seen_urls: Set[str] = set()
        normal_empty_hosts = 0
        for search_url in self._search_urls():
            try:
                self.search_stats["requests"] += 1
                response = self.session.get(search_url, params={
                    "q": query,
                    "first": page * 10 + 1,
                    "setmkt": "en-US",
                    "mkt": "en-US",
                    "setlang": "en-US",
                    "cc": "US",
                    "ensearch": "1",
                }, timeout=self.timeout)
                response.raise_for_status()
                results = self._parse(response.text)
                self.search_stats["html_results"] += len(results)
                if results:
                    for item in results:
                        if item["url"] not in seen_urls:
                            seen_urls.add(item["url"])
                            combined.append(item)
                    continue

                diag = self._diagnose_empty_response(response.text, response.url)
                if diag["kind"] == "normal_empty":
                    normal_empty_hosts += 1
                    if query == "China poverty 2024":
                        self._save_debug_page(query, page, response.text, "preflight_normal_empty")
                    continue

                self.search_stats["abnormal_empty"] += 1
                self.search_stats["last_reason"] = diag["reason"]
                debug_path = self._save_debug_page(query, page, response.text, diag["reason"])
                reason = (f"{diag['reason']} | status={response.status_code} | "
                          f"len={diag['length']} | title={diag['title'][:80]}")
                if debug_path:
                    reason += f" | debug={debug_path}"
                errors.append(f"{urlparse(search_url).netloc}: {reason}")
                logger.warning("Bing 返回异常空页 [%s p%s] via %s: %s",
                               query, page, urlparse(search_url).netloc, diag["reason"])
            except requests.RequestException as exc:
                reason = f"{type(exc).__name__}: {str(exc)[:220]}"
                errors.append(f"{urlparse(search_url).netloc}: {reason}")
                self.search_stats["last_reason"] = reason
                logger.warning("搜索失败 [%s] via %s: %s",
                               query, urlparse(search_url).netloc, type(exc).__name__)

        # Bing HTML is often reduced or blocked on servers. RSS is a lighter
        # response and usually survives proxies that alter the HTML page.
        should_try_rss = (
            self.rss_fallback
            and len(combined) < self.rss_threshold
            and (combined or normal_empty_hosts < len(self._search_urls()) or errors)
        )
        if should_try_rss:
            for search_url in self._search_urls():
                try:
                    rss_results = self._fetch_rss(search_url, query, page)
                    for item in rss_results:
                        if item["url"] not in seen_urls:
                            seen_urls.add(item["url"])
                            combined.append(item)
                    if rss_results:
                        self.search_stats["rss_hits"] += 1
                    if len(combined) >= self.rss_threshold:
                        break
                except requests.RequestException as exc:
                    reason = f"RSS {type(exc).__name__}: {str(exc)[:180]}"
                    errors.append(f"{urlparse(search_url).netloc}: {reason}")
                    logger.warning("RSS 搜索失败 [%s] via %s: %s",
                                   query, urlparse(search_url).netloc, type(exc).__name__)
                except (ET.ParseError, ValueError) as exc:
                    reason = f"RSS {type(exc).__name__}: {str(exc)[:180]}"
                    errors.append(f"{urlparse(search_url).netloc}: {reason}")

        if (
            self.google_fallback
            and len(combined) < self.rss_threshold
            and (combined or normal_empty_hosts < len(self._search_urls()) or errors)
        ):
            try:
                google_results = self._fetch_google_rss(query)
                for item in google_results:
                    if item["url"] not in seen_urls:
                        seen_urls.add(item["url"])
                        combined.append(item)
                if google_results:
                    self.search_stats["google_rss_hits"] += 1
            except requests.RequestException as exc:
                reason = f"Google RSS {type(exc).__name__}: {str(exc)[:180]}"
                errors.append(reason)
                logger.warning("Google RSS 搜索失败 [%s]: %s", query, type(exc).__name__)
            except (ET.ParseError, ValueError) as exc:
                errors.append(f"Google RSS {type(exc).__name__}: {str(exc)[:180]}")

        if combined:
            return combined
        if normal_empty_hosts == len(self._search_urls()) and not errors:
            return []

        self.search_stats["failures"] += 1
        self._log_fail("search", f"{query} page={page}", " | ".join(self._search_urls()),
                       " ; ".join(errors) if errors else "unknown search error")
        return []

    def _fetch_rss(self, search_url: str, query: str, page: int) -> List[Dict]:
        self.search_stats["rss_requests"] += 1
        response = self.session.get(search_url, params={
            "q": query,
            "first": page * 10 + 1,
            "format": "rss",
            "setmkt": "en-US",
            "mkt": "en-US",
            "ensearch": "1",
        }, timeout=self.timeout)
        response.raise_for_status()
        root = ET.fromstring(response.content)
        results: List[Dict] = []
        for item in root.iter():
            if self._xml_local_name(item.tag) != "item":
                continue
            values = {}
            for child in item:
                name = self._xml_local_name(child.tag)
                values[name] = (child.text or "").strip()
            url = self._normalize_bing_href(values.get("link", ""))
            if not url:
                continue
            title = re.sub(r"\s+", " ", values.get("title", "")).strip()
            if len(title) < 8:
                continue
            source = source_from_url(url, values.get("source", ""))
            description = BeautifulSoup(values.get("description", ""), "lxml").get_text(" ", strip=True)
            results.append({
                "id": self._id(url), "title": title[:500], "url": url,
                "source": source, "date": values.get("pubDate", ""),
                "pub_date": parse_date(url, values.get("pubDate", "")),
                "summary": description[:500], "is_official": source in {
                    "CGTN", "Xinhua English", "People's Daily Online", "China Daily", "english.gov.cn"
                }, "source_type": "news", "search_keyword": "",
            })
        self.search_stats["rss_results"] += len(results)
        return results

    def _fetch_google_rss(self, query: str) -> List[Dict]:
        self.search_stats["google_rss_requests"] += 1
        response = self.session.get(self.GOOGLE_RSS_URL, params={
            "q": query,
            "hl": "en-US",
            "gl": "US",
            "ceid": "US:en",
        }, timeout=self.timeout)
        response.raise_for_status()
        root = ET.fromstring(response.content)
        results: List[Dict] = []
        for item in root.iter():
            if self._xml_local_name(item.tag) != "item":
                continue
            values = {}
            source_label = ""
            for child in item:
                name = self._xml_local_name(child.tag)
                text = (child.text or "").strip()
                values[name] = text
                if name == "source":
                    source_label = text

            description_html = values.get("description", "")
            description_soup = BeautifulSoup(description_html, "lxml")
            direct_url = ""
            for anchor in description_soup.select("a[href]"):
                candidate = anchor.get("href", "")
                host = urlparse(candidate).netloc.lower()
                if candidate.startswith("http") and "news.google.com" not in host:
                    direct_url = candidate
                    break
            url = direct_url or values.get("link", "")
            if not url:
                continue
            title = re.sub(r"\s+", " ", values.get("title", "")).strip()
            if len(title) < 8:
                continue
            if not source_label and " - " in title:
                source_label = title.rsplit(" - ", 1)[-1]
            source = source_from_url(url, source_label)
            description = description_soup.get_text(" ", strip=True)
            results.append({
                "id": self._id(url), "title": title[:500], "url": url,
                "source": source, "date": values.get("pubDate", ""),
                "pub_date": parse_date(url, values.get("pubDate", "")),
                "summary": description[:500], "is_official": source in {
                    "CGTN", "Xinhua English", "People's Daily Online", "China Daily", "english.gov.cn"
                }, "source_type": "news", "search_keyword": "",
            })
        self.search_stats["google_rss_results"] += len(results)
        return results

    @staticmethod
    def _xml_local_name(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    def inspect_search(self, query: str = "China poverty 2024", page: int = 0) -> bool:
        """Print one-request diagnostics for each Bing host without collecting data."""
        proxy_vars = [name for name in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY") if os.environ.get(name)]
        if proxy_vars:
            logger.info("检测到代理环境变量: %s（未打印具体地址）", ", ".join(proxy_vars))

        ok = False
        for search_url in self._search_urls():
            host = urlparse(search_url).netloc
            try:
                response = self.session.get(search_url, params={
                    "q": query,
                    "first": page * 10 + 1,
                    "setmkt": "en-US",
                    "mkt": "en-US",
                    "setlang": "en-US",
                    "cc": "US",
                    "ensearch": "1",
                }, timeout=self.timeout)
                results = self._parse(response.text)
                diag = self._diagnose_empty_response(response.text, response.url)
                debug_path = ""
                if not results:
                    debug_path = self._save_debug_page(query, page, response.text, diag["reason"])
                logger.info(
                    "Bing诊断 host=%s | status=%s | results=%s | len=%s | final_url=%s | title=%s%s",
                    host,
                    response.status_code,
                    len(results),
                    diag["length"],
                    response.url[:160],
                    diag["title"][:100] or "None",
                    f" | debug={debug_path}" if debug_path else "",
                )
                if response.ok and results:
                    ok = True
                elif not response.ok:
                    self._log_fail("inspect", query, response.url, f"HTTP {response.status_code}")
                else:
                    self._log_fail("inspect", query, response.url, diag["reason"])
            except requests.RequestException as exc:
                reason = f"{type(exc).__name__}: {str(exc)[:220]}"
                logger.warning("Bing诊断 host=%s | 请求失败: %s", host, reason)
                self._log_fail("inspect", query, search_url, reason)
        self._write_summary(len(self._load_json()), 0, 0, 0)
        return ok

    def check_search(self, query: str = "China poverty 2024", write_summary: bool = True) -> bool:
        """Run one known Bing News query so server-side network problems are visible."""
        proxy_vars = [name for name in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY") if os.environ.get(name)]
        if proxy_vars:
            logger.info("检测到代理环境变量: %s（未打印具体地址）", ", ".join(proxy_vars))
        before_failures = self.search_stats["failures"]
        before_abnormal = self.search_stats["abnormal_empty"]
        results = self._fetch(query, 0)
        ok = bool(results)
        if ok:
            logger.info("Bing 预检通过: '%s' 解析到 %s 条新闻", query, len(results))
        else:
            reason = self.search_stats.get("last_reason") or "解析结果为空"
            logger.error("Bing 预检未解析到新闻: %s", reason)
            if self.search_stats["failures"] == before_failures and self.search_stats["abnormal_empty"] == before_abnormal:
                self._log_fail("preflight", query, "", "known query returned 0 results")
        if write_summary:
            self._write_summary(len(self._load_json()), 0, 0, 0)
        return ok

    def _parse(self, html: str) -> List[Dict]:
        soup = BeautifulSoup(html, "lxml")
        cards = soup.select("[class*=news-card], [class*=newsitem], [class*=cardcommon], article")
        results = []
        for card in cards:
            link, url = self._extract_news_link(card)
            if not link or not url:
                continue
            title = re.sub(r"\s+", " ", link.get_text(" ", strip=True))
            if not title:
                title = link.get("aria-label", "") or link.get("title", "")
            if not title:
                heading = card.select_one("h2, h3, [class*=title]")
                title = heading.get_text(" ", strip=True) if heading else ""
            if len(title) < 8:
                continue
            text = re.sub(r"\s+", " ", card.get_text(" ", strip=True))
            source = source_from_url(url)
            source_el = card.select_one("[class*=source], [class*=author]")
            if source_el:
                source = source_from_url(url, clean_source_name(source_el.get_text(" ", strip=True)))
            results.append({
                "id": self._id(url), "title": title[:500], "url": url,
                "source": source, "date": "", "pub_date": parse_date(url, text),
                "summary": text[:500], "is_official": source in {
                    "CGTN", "Xinhua English", "People's Daily Online", "China Daily", "english.gov.cn"
                }, "source_type": "news", "search_keyword": "",
            })

        if not results:
            results = self._parse_global_links(soup)
        return results

    def _extract_news_link(self, card) -> Tuple[Optional[object], str]:
        for link in card.select("a[href]"):
            url = self._normalize_bing_href(link.get("href", ""))
            if url:
                return link, url
        return None, ""

    def _parse_global_links(self, soup: BeautifulSoup) -> List[Dict]:
        """Fallback for simplified Bing pages where card classes differ."""
        results: List[Dict] = []
        seen: Set[str] = set()
        for link in soup.select("a[href]"):
            url = self._normalize_bing_href(link.get("href", ""))
            title = re.sub(r"\s+", " ", link.get_text(" ", strip=True))
            if not title:
                title = link.get("aria-label", "") or link.get("title", "")
            if not title:
                heading = link.parent.select_one("h2, h3, [class*=title]") if link.parent else None
                title = heading.get_text(" ", strip=True) if heading else ""
            if not url or url in seen or len(title) < 12 or self._is_non_news_url(url):
                continue
            seen.add(url)
            parent_text = re.sub(r"\s+", " ", link.parent.get_text(" ", strip=True)) if link.parent else title
            source = source_from_url(url)
            results.append({
                "id": self._id(url), "title": title[:500], "url": url,
                "source": source, "date": "", "pub_date": parse_date(url, parent_text),
                "summary": parent_text[:500], "is_official": source in {
                    "CGTN", "Xinhua English", "People's Daily Online", "China Daily", "english.gov.cn"
                }, "source_type": "news", "search_keyword": "",
            })
            if len(results) >= 10:
                break
        return results

    @staticmethod
    def _is_non_news_url(url: str) -> bool:
        host = urlparse(url).netloc.lower()
        blocked_hosts = (
            "microsoft.com", "go.microsoft.com", "support.microsoft.com",
            "privacy.microsoft.com", "login.live.com", "account.microsoft.com",
            "office.com", "aka.ms", "beian.miit.gov.cn", "dxzhgl.miit.gov.cn",
        )
        return any(host == item or host.endswith("." + item) for item in blocked_hosts)

    def _normalize_bing_href(self, href: str) -> str:
        if not href:
            return ""
        href = urljoin("https://www.bing.com", href)
        parsed = urlparse(href)
        host = parsed.netloc.lower()
        if host and "bing.com" not in host:
            return href
        query = parse_qs(parsed.query)
        for key in ("u", "url", "r"):
            for raw in query.get(key, []):
                decoded = self._decode_bing_url(raw)
                if decoded and "bing.com" not in urlparse(decoded).netloc.lower():
                    return decoded
        return ""

    @staticmethod
    def _decode_bing_url(raw: str) -> str:
        raw = unquote(raw or "")
        if raw.startswith("http"):
            return raw
        if raw.startswith("a1"):
            payload = raw[2:]
            padding = "=" * (-len(payload) % 4)
            try:
                decoded = base64.urlsafe_b64decode((payload + padding).encode("ascii")).decode("utf-8", "ignore")
                if decoded.startswith("http"):
                    return decoded
            except Exception:
                return ""
        return ""

    def _diagnose_empty_response(self, html: str, final_url: str) -> Dict[str, str]:
        soup = BeautifulSoup(html or "", "lxml")
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).lower()
        final = final_url.lower()
        length = len(html or "")
        if any(marker in text for marker in [
            "verify you are human", "captcha", "unusual traffic", "our services aren't available right now",
            "access denied", "robot", "blocked",
        ]):
            kind, reason = "blocked", "疑似验证码/反爬/访问被拦截"
        elif "/news/search" not in final:
            kind, reason = "redirect", f"被重定向到非 news/search 页面: {final_url[:120]}"
        elif any(marker in text for marker in [
            "we didn't find any results", "there are no results", "no results for", "try different keywords",
        ]):
            kind, reason = "normal_empty", "Bing 正常返回无结果"
        elif length < 2000:
            kind, reason = "short", f"响应过短({length} bytes)，可能是代理/网关错误页"
        else:
            news_card_count = len(soup.select("[class*=news-card], [class*=newsitem], [class*=cardcommon], article"))
            ext_links = sum(1 for a in soup.select("a[href]") if self._normalize_bing_href(a.get("href", "")))
            if news_card_count == 0 and ext_links == 0:
                kind, reason = "parser_miss", "未发现新闻卡片或外部新闻链接，可能是 Bing 页面结构/地区页不同"
            else:
                kind, reason = "parser_miss", f"发现卡片{news_card_count}个/外链{ext_links}个，但未能解析出有效新闻"
        return {"kind": kind, "reason": reason, "title": title, "length": str(length), "final_url": final_url}

    def _save_debug_page(self, query: str, page: int, html: str, reason: str) -> str:
        if self.debug_saved >= self.debug_limit:
            return ""
        self.debug_dir.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", query).strip("_")[:80] or "query"
        path = self.debug_dir / f"search_{slug}_p{page}_{self.debug_saved + 1}.html"
        path.write_text(html or "", encoding="utf-8", errors="ignore")
        self.debug_saved += 1
        self.search_stats["debug_pages"] = self.debug_saved
        return str(path)

    @staticmethod
    def _id(url: str) -> str:
        import hashlib
        return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _normalise_article_text(value: str) -> str:
        """Collapse HTML whitespace while keeping paragraph boundaries."""
        value = re.sub(r"\xa0", " ", value or "")
        value = re.sub(r"[ \t\r\f\v]+", " ", value)
        value = re.sub(r"\n[ \t]+", "\n", value)
        value = re.sub(r"\n{3,}", "\n\n", value)
        return value.strip()

    @classmethod
    def _json_ld_article_bodies(cls, soup: BeautifulSoup) -> List[str]:
        """Read articleBody from JSON-LD used by MSN and many news CMSs."""
        bodies: List[str] = []

        def walk(value) -> None:
            if isinstance(value, dict):
                body = value.get("articleBody")
                if isinstance(body, str) and body.strip():
                    bodies.append(cls._normalise_article_text(body))
                for child in value.values():
                    if isinstance(child, (dict, list)):
                        walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        for script in soup.select('script[type="application/ld+json"]'):
            raw = script.string or script.get_text()
            if not raw.strip():
                continue
            try:
                walk(json.loads(raw))
            except (json.JSONDecodeError, TypeError, ValueError):
                # Some pages append invalid JSON after a valid articleBody.
                match = re.search(r'"articleBody"\s*:\s*"((?:\\.|[^"\\])*)"', raw, re.S)
                if match:
                    try:
                        body = json.loads('"' + match.group(1) + '"')
                    except (json.JSONDecodeError, ValueError):
                        body = match.group(1).replace("\\n", "\n").replace('\\"', '"')
                    if body.strip():
                        bodies.append(cls._normalise_article_text(body))
        return [body for body in bodies if len(body) >= 120]

    @classmethod
    def _embedded_article_bodies(cls, soup: BeautifulSoup) -> List[str]:
        """Recover article text serialized in application JSON by SPA sites."""
        bodies: List[str] = []
        body_keys = {
            "articlebody", "article_body", "articletext", "article_text",
            "bodytext", "body_text", "fulltext", "full_text",
        }
        structured_keys = {
            "body", "content", "contents", "paragraphs", "blocks",
            "article", "articlecontent", "article_content", "story",
        }

        def add(value: str) -> None:
            value = unescape(value or "")
            if "<" in value and ">" in value:
                value = BeautifulSoup(value, "lxml").get_text(" ", strip=True)
            value = cls._normalise_article_text(value)
            punctuation = sum(value.count(mark) for mark in ".!?。！？")
            if len(value) >= 160 and punctuation >= 2 and value not in bodies:
                bodies.append(value)

        def collect_structured(value) -> None:
            """Join text-bearing nodes used by React/Next/SPA article blocks."""
            parts: List[str] = []

            def collect(node) -> None:
                if isinstance(node, str):
                    value = cls._normalise_article_text(
                        BeautifulSoup(unescape(node), "lxml").get_text(" ", strip=True)
                        if "<" in node and ">" in node else unescape(node)
                    )
                    if len(value) >= 20:
                        parts.append(value)
                elif isinstance(node, list):
                    for child in node:
                        collect(child)
                elif isinstance(node, dict):
                    for key in ("text", "value", "html", "markup", "children", "content"):
                        if key in node:
                            collect(node[key])

            collect(value)
            add("\n\n".join(parts))

        def walk(value) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    normalized_key = str(key).lower().replace("-", "_")
                    if normalized_key in body_keys:
                        if isinstance(child, str):
                            add(child)
                        elif isinstance(child, (dict, list)):
                            collect_structured(child)
                    elif normalized_key in structured_keys:
                        if isinstance(child, str):
                            add(child)
                        elif isinstance(child, (dict, list)):
                            collect_structured(child)
                    elif isinstance(child, (dict, list)):
                        walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        for script in soup.select("script"):
            raw = script.string or script.get_text()
            if not raw.strip() or "{" not in raw:
                continue
            try:
                walk(json.loads(raw))
            except (json.JSONDecodeError, TypeError, ValueError):
                # JSON is often assigned to ``window.__DATA__`` rather than
                # being the complete script body.
                start, end = raw.find("{"), raw.rfind("}")
                if start >= 0 and end > start:
                    try:
                        walk(json.loads(raw[start:end + 1]))
                    except (json.JSONDecodeError, TypeError, ValueError):
                        pass
                # Also support a JSON fragment embedded in a JavaScript assignment.
                for key in body_keys:
                    match = re.search(
                        rf'"{re.escape(key)}"\s*:\s*"((?:\\.|[^"\\])*)"', raw, re.I | re.S
                    )
                    if match:
                        try:
                            add(json.loads('"' + match.group(1) + '"'))
                        except (json.JSONDecodeError, ValueError):
                            add(match.group(1).replace("\\n", "\n").replace('\\"', '"'))
        return bodies

    @staticmethod
    def _related_article_urls(response: requests.Response) -> List[str]:
        """Find canonical/AMP URLs that may contain the publisher's full text."""
        soup = BeautifulSoup(response.text, "lxml")
        urls: List[str] = []
        selectors = [
            "link[rel='canonical'][href]",
            "link[rel='amphtml'][href]",
            "link[rel='alternate'][href]",
            "meta[property='og:url'][content]",
            "meta[name='twitter:url'][content]",
        ]
        for node in soup.select(", ".join(selectors)):
            raw = node.get("href") or node.get("content") or ""
            candidate = urljoin(response.url, raw.strip())
            if urlparse(candidate).scheme in {"http", "https"} and candidate not in urls:
                urls.append(candidate)
        return urls[:4]

    @classmethod
    def _container_text(cls, container) -> str:
        """Extract text from both paragraph-based and div-based article layouts."""
        if not container:
            return ""

        blocks: List[str] = []
        for node in container.select("p, h2, h3, h4, blockquote"):
            value = cls._normalise_article_text(node.get_text(" ", strip=True))
            if len(value) >= 20 and value not in blocks:
                blocks.append(value)
        paragraph_text = "\n\n".join(blocks)
        if len(paragraph_text) >= 120:
            return paragraph_text

        # Older government/CMS pages put each sentence in a div or table cell.
        # Use the complete container only when paragraph extraction was too short;
        # this avoids duplicating nested div text on normal pages.
        fallback = cls._normalise_article_text(container.get_text("\n", strip=True))
        lines = []
        for line in fallback.splitlines():
            line = cls._normalise_article_text(line)
            if len(line) >= 20 and line not in lines:
                lines.append(line)
        return "\n\n".join(lines) or cls._normalise_article_text(container.get_text(" ", strip=True))

    @classmethod
    def _extract_article_text(cls, soup: BeautifulSoup) -> str:
        """Extract an article body without mistaking an error page for content."""
        json_bodies = cls._json_ld_article_bodies(soup)
        if json_bodies:
            return max(json_bodies, key=len)
        embedded_bodies = cls._embedded_article_bodies(soup)
        if embedded_bodies:
            return max(embedded_bodies, key=len)

        for tag in soup.select(
            "script, style, nav, footer, header, aside, form, iframe, noscript, svg, "
            "[role='navigation'], [aria-hidden='true'], [hidden], "
            ".cookie, .cookies, .advert, .advertisement, .ad, .related, .recommend, "
            ".share, .social, .breadcrumb, .pagination"
        ):
            tag.decompose()

        selectors = [
            "[itemprop='articleBody']", "article", "main",
            "[class*='article-body']", "[class*='article-content']",
            "[class*='story-body']", "[class*='story-content']",
            "[class*='post-content']", "[class*='entry-content']",
            "[class*='detail-content']", "[class*='content-detail']",
            "[class*='article']", "[data-testid*='article']", "[data-article-body]",
            "[id*='article-body']", "[id*='article-content']",
            "[id*='story-body']", "[id*='content']", "[class*='content']",
            "body",
        ]
        candidates = []
        seen = set()
        for selector in selectors:
            for container in soup.select(selector):
                marker = id(container)
                if marker in seen:
                    continue
                seen.add(marker)
                text = cls._container_text(container)
                if len(text) < 80:
                    continue
                link_text = sum(len(a.get_text(" ", strip=True)) for a in container.select("a"))
                link_ratio = link_text / max(len(text), 1)
                paragraphs = len(container.select("p"))
                score = len(text) + min(paragraphs * 80, 800) - int(link_ratio * 500)
                candidates.append((score, text))
        return max(candidates, key=lambda item: item[0])[1] if candidates else ""

    @staticmethod
    def _is_error_page(response: requests.Response, text: str) -> bool:
        """Reject captcha, access-denied and obvious registry pages."""
        lower = (text or "").lower()
        markers = (
            "verify you are human", "captcha", "access denied", "unusual traffic",
            "request blocked", "enable javascript to continue", "robot check",
            "增值电信业务经营许可证", "京公网安备", "备案号",
        )
        return response.status_code in {401, 403, 429, 451, 521} or any(
            marker in lower for marker in markers
        )

    def _article_url_variants(self, url: str) -> List[str]:
        """Return a small set of safe URL variants for syndicated pages."""
        variants = [url]
        parsed = urlparse(url)
        if parsed.netloc.lower().endswith("msn.com") and parsed.query:
            clean_url = parsed._replace(query="", fragment="").geturl()
            if clean_url not in variants:
                variants.append(clean_url)
        return variants

    def _get_article_response(self, url: str) -> requests.Response:
        """Fetch one article URL; retry once without certificate verification on SSL errors.

        Search requests keep normal certificate verification. The relaxed retry is
        limited to the failing article URL so one certificate issue does not abort
        the whole download run.
        """
        headers = {"Referer": "https://www.bing.com/"}
        try:
            return self.session.get(url, timeout=self.timeout, headers=headers)
        except requests.exceptions.SSLError as exc:
            host = urlparse(url).netloc or url
            detail = str(exc).splitlines()[0][:160]
            logger.warning("正文 SSL 校验失败，尝试当前 URL 重试: %s (%s)", host, detail)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                return self.session.get(
                    url,
                    timeout=self.timeout,
                    headers=headers,
                    verify=False,
                )

    def _fetch_article(self, url: str) -> Tuple[requests.Response, str]:
        """Fetch and extract an article, following safe canonical/AMP fallbacks."""
        last_response: Optional[requests.Response] = None
        last_text = ""
        candidates = self._article_url_variants(url)
        attempted: Set[str] = set()
        attempt = 0
        while candidates and attempt < 5:
            candidate_url = candidates.pop(0)
            if candidate_url in attempted:
                continue
            attempted.add(candidate_url)
            if attempt:
                time.sleep(min(1.0, max(0.2, self.delay / 2)))
            attempt += 1
            response = self._get_article_response(candidate_url)
            # Keep HTTP errors visible after normal and SSL-fallback requests.
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "lxml")
            text = self._extract_article_text(soup)
            last_response, last_text = response, text
            is_error = self._is_error_page(response, text) or (
                len(text) < 120 and self._is_error_page(response, response.text)
            )
            if is_error or len(text) < 120:
                for related_url in self._related_article_urls(response):
                    if related_url not in attempted and related_url not in candidates:
                        candidates.append(related_url)
                continue
            if len(text) >= 120:
                return response, text
        if last_response is None:
            raise ValueError("未获取到响应")
        return last_response, last_text

    @staticmethod
    def _matches_failed_retry(article: Dict, entry: Dict, mode: str) -> bool:
        """Check whether a prior failed download belongs to a retry subset."""
        if entry.get("status") != "failed":
            return False
        if mode == "all":
            return True

        reason = str(entry.get("reason") or "").lower()
        url = str(article.get("url") or "").lower()
        is_ssl = "sslerror" in reason or "ssl error" in reason
        if mode == "ssl":
            return is_ssl
        if mode == "google-ssl":
            return is_ssl and ("news.google.com" in reason or "news.google.com" in url)
        return False

    def download(
        self,
        articles: List[Dict],
        limit: Optional[int] = None,
        retry_failed: Optional[str] = None,
    ) -> Tuple[int, int, int]:
        status = self._load_download_status()
        if retry_failed:
            candidates = [
                article for article in articles
                if not (self.articles_dir / f"{article['id']}.md").exists()
                and self._matches_failed_retry(
                    article, status.get(article["id"], {}), retry_failed
                )
            ]
            logger.info(
                "失败重试筛选: %s | download_log.json 中匹配 %s 篇",
                retry_failed,
                len(candidates),
            )
        else:
            candidates = [a for a in articles
                          if not (self.articles_dir / f"{a['id']}.md").exists()
                          and status.get(a["id"], {}).get("status") != "success"]
        available = len(candidates)
        if limit:
            candidates = candidates[:limit]
        total, success, failed = len(candidates), 0, 0
        existing_skipped = len(articles) - available
        deferred_by_limit = available - total
        skipped = existing_skipped + deferred_by_limit
        logger.info("正文下载: 待处理 %s 篇 | 已存在跳过 %s 篇", total, skipped)
        current_run = {
            "mode": retry_failed or "incremental",
            "available": available,
            "selected": total,
            "processed": 0,
            "success": 0,
            "failed": 0,
            "existing_skipped": existing_skipped,
            "deferred_by_limit": deferred_by_limit,
        }
        self._write_download_report(articles, status, current_run)
        for index, article in enumerate(candidates, 1):
            try:
                if self._is_probably_non_article_url(article["url"]):
                    raise ValueError("疑似专题页/备案页，跳过正文下载")
                time.sleep(self.delay)
                response, text = self._fetch_article(article["url"])
                if self._is_error_page(response, text) or (
                    len(text) < 120 and self._is_error_page(response, response.text)
                ):
                    raise ValueError("疑似验证码/拦截页或备案页")
                if len(text) < 120:
                    raise ValueError(
                        f"正文过短({len(text)}字符，HTML {len(response.content)} bytes)"
                    )
                path = self.articles_dir / f"{article['id']}.md"
                path.write_text(f"# {article['title']}\n\n{text}\n", encoding="utf-8")
                success += 1
                status[article["id"]] = {"status": "success", "updated_at": dt.datetime.now().isoformat()}
            except Exception as exc:
                failed += 1
                reason = f"{type(exc).__name__}: {str(exc)[:160]}"
                self._log_fail("download", article["id"], article["url"], reason)
                logger.warning("正文失败 %s: %s", article["id"], reason)
                status[article["id"]] = {"status": "failed", "reason": reason,
                                          "updated_at": dt.datetime.now().isoformat()}
            self._save_download_status(status)
            current_run["processed"] = index
            current_run["success"] = success
            current_run["failed"] = failed
            self._write_download_report(articles, status, current_run)
            if index % 10 == 0 or index == total:
                logger.info("正文进度: %s/%s | 成功 %s | 失败 %s | 跳过 %s", index, total, success, failed, skipped)
        self._write_summary(len(articles), success, failed, skipped)
        logger.info("下载情况已写入: %s", self.download_report)
        return success, failed, skipped

    @staticmethod
    def _is_probably_non_article_url(url: str) -> bool:
        """Identify URLs that are useful search results but not article pages."""
        parsed = urlparse(url)
        host = parsed.netloc.lower().split(":", 1)[0]
        path = unquote(parsed.path).lower().rstrip("/")
        blocked_hosts = {
            "beian.miit.gov.cn", "beian.mps.gov.cn", "dxzhgl.miit.gov.cn",
        }
        if host in blocked_hosts:
            return True
        blocked_fragments = (
            "/topics/", "/topic/", "/tag/", "/category/", "/search",
            "/nature-index/article/",
        )
        return any(fragment in path for fragment in blocked_fragments)

    def _load_download_status(self) -> Dict:
        path = self.data_dir / "download_log.json"
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_download_status(self, status: Dict) -> None:
        (self.data_dir / "download_log.json").write_text(
            json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")

    def _download_counts(self, articles: List[Dict], status: Dict) -> Dict[str, int]:
        """Count the durable download state from files and the status log."""
        counts = {
            "total": len(articles),
            "downloaded": 0,
            "failed": 0,
            "pending": 0,
            "success_without_file": 0,
        }
        for article in articles:
            path = self.articles_dir / f"{article['id']}.md"
            entry = status.get(article["id"], {})
            if path.is_file() and path.stat().st_size > 0:
                counts["downloaded"] += 1
            elif entry.get("status") == "failed":
                counts["failed"] += 1
            elif entry.get("status") == "success":
                counts["success_without_file"] += 1
            else:
                counts["pending"] += 1
        return counts

    def _write_download_report(
        self,
        articles: List[Dict],
        status: Dict,
        current_run: Dict[str, object],
    ) -> None:
        """Write a human-readable download report inside ``articles/``."""
        counts = self._download_counts(articles, status)
        now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        processed = int(current_run.get("processed", 0))
        selected = int(current_run.get("selected", 0))
        progress = f"{processed}/{selected}" if selected else "0/0"
        report = (
            f"# English news download summary\n\n"
            f"- Run ID: `{self.run_id}`\n"
            f"- Last updated: {now}\n"
            f"- Metadata records: {counts['total']}\n"
            f"- Downloaded Markdown: {counts['downloaded']}\n"
            f"- Failed: {counts['failed']}\n"
            f"- Not downloaded/pending: {counts['pending']}\n"
            f"- Success status but file missing: {counts['success_without_file']}\n\n"
            f"## Current invocation\n\n"
            f"- Mode: `{current_run.get('mode', 'incremental')}`\n"
            f"- Eligible historical items: {current_run.get('available', 0)}\n"
            f"- Selected this time: {selected}\n"
            f"- Progress: {progress}\n"
            f"- Success this time: {current_run.get('success', 0)}\n"
            f"- Failed this time: {current_run.get('failed', 0)}\n"
            f"- Existing/status skipped: {current_run.get('existing_skipped', 0)}\n"
            f"- Deferred by limit: {current_run.get('deferred_by_limit', 0)}\n\n"
            f"## Related files\n\n"
            f"- Per-article status: `../download_log.json`\n"
            f"- Failure details: `../fail.log`\n"
        )
        temporary = self.download_report.with_suffix(".md.tmp")
        try:
            temporary.write_text(report, encoding="utf-8")
            temporary.replace(self.download_report)
        except OSError as exc:
            logger.warning("无法写入下载汇总 %s: %s", self.download_report, exc)
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def _load_json(self) -> List[Dict]:
        path = self.data_dir / "news.json"
        if not path.exists():
            return []
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []

    def _save(self, articles: List[Dict]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "news.json").write_text(json.dumps(articles, ensure_ascii=False, indent=2), encoding="utf-8")
        with (self.data_dir / "news.csv").open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(articles)
        self._write_summary(len(articles), 0, 0, 0)

    def _write_summary(self, total: int, success: int, failed: int, skipped: int) -> None:
        (self.data_dir / "summary.md").write_text(
            f"# English news run {self.run_id}\n\n"
            f"- Scope: {getattr(self, 'plan_info', {}).get('scope', 'unknown')}\n"
            f"- Languages: {', '.join(getattr(self, 'plan_info', {}).get('languages', []))}\n"
            f"- Countries: {', '.join(getattr(self, 'plan_info', {}).get('countries', [])) or 'all/global'}\n"
            f"- Records: {total}\n- Download success: {success}\n"
            f"- Download failed: {failed}\n- Existing/skipped: {skipped}\n"
            f"- Search requests: {self.search_stats['requests']}\n"
            f"- Search failures: {self.search_stats['failures']}\n"
            f"- Abnormal empty pages: {self.search_stats['abnormal_empty']}\n"
            f"- HTML parsed results: {self.search_stats['html_results']}\n"
            f"- RSS fallback requests: {self.search_stats['rss_requests']}\n"
            f"- RSS fallback hits: {self.search_stats['rss_hits']}\n"
            f"- RSS parsed results: {self.search_stats['rss_results']}\n"
            f"- Google RSS fallback requests: {self.search_stats['google_rss_requests']}\n"
            f"- Google RSS fallback hits: {self.search_stats['google_rss_hits']}\n"
            f"- Google RSS parsed results: {self.search_stats['google_rss_results']}\n"
            f"- Duplicate/filtered results: {self.search_stats['duplicate_results']}\n"
            f"- Pages without new records: {self.search_stats['no_new_pages']}\n"
            f"- Proxy: {self.proxy_display}\n"
            f"- Debug pages saved: {self.search_stats['debug_pages']}\n"
            f"- Last search reason: {self.search_stats['last_reason'] or 'None'}\n",
            encoding="utf-8")

    def _log_fail(self, step: str, item: str, url: str, reason: str) -> None:
        with self.fail_log.open("a", encoding="utf-8") as handle:
            handle.write(f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] step={step} | id={item} | url={url[:300]} | reason={reason}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="国际英文新闻采集器")
    parser.add_argument("--run-id", help="复用已有 run；采集时增量写回，下载时断点续传")
    parser.add_argument("--keywords", nargs="+",
                        help="自定义关键词；不指定时由 --scope/--languages 自动生成")
    parser.add_argument("--scope", choices=["china", "global", "all"], default="all",
                        help="采集范围：all（默认全量）、china（中国）、global（全球）")
    parser.add_argument("--languages", nargs="+", choices=sorted(set(CHINA_KEYWORDS_BY_LANGUAGE) | set(GLOBAL_KEYWORDS_BY_LANGUAGE)),
                        help="查询语言；all 默认 en/zh/es/fr/pt/ar/hi/id/vi，global 默认不含 zh")
    parser.add_argument("--countries", nargs="+",
                        help="全球模式的国家限定词，例如 India Brazil South_Africa；不指定则不展开国家组合")
    parser.add_argument("--sources", nargs="+", choices=list(MEDIA), help="只保留指定媒体")
    parser.add_argument("--years", nargs="+", type=int, default=list(range(2000, dt.date.today().year + 1)))
    parser.add_argument("--pages", type=int, default=10,
                        help="每个关键词/年份最多翻页数，默认 10")
    parser.add_argument("--limit", type=int, help="本次采集或下载最多处理多少条")
    parser.add_argument("--download", action="store_true", help="下载新闻正文")
    parser.add_argument("--download-only", action="store_true", help="只下载已有 run，不重新搜索")
    parser.add_argument(
        "--retry-failed",
        choices=["google-ssl", "ssl", "all"],
        help=("只重试 download_log.json 中的历史失败项：google-ssl 仅重试 "
              "news.google.com 的 SSL 错误，ssl 重试全部 SSL 错误，all 重试全部失败"),
    )
    parser.add_argument("--delay", type=float, default=1.5)
    parser.add_argument("--timeout", type=int, default=20, help="请求超时时间（秒），服务器代理较慢时可调大")
    parser.add_argument("--use-proxy", action="store_true",
                        help=f"启用本机 HTTP 代理 127.0.0.1:{DEFAULT_PROXY_PORT}")
    parser.add_argument("--proxy", default=os.getenv("NEWS_PROXY", ""),
                        help="指定代理地址，如 http://127.0.0.1:7897 或 socks5h://127.0.0.1:7897")
    parser.add_argument("--proxy-host", default=DEFAULT_PROXY_HOST,
                        help=f"--use-proxy 使用的代理主机，默认 {DEFAULT_PROXY_HOST}")
    parser.add_argument("--proxy-port", type=int, default=DEFAULT_PROXY_PORT,
                        help=f"--use-proxy 使用的代理端口，默认 {DEFAULT_PROXY_PORT}")
    parser.add_argument("--rss-threshold", type=int, default=5,
                        help="HTML 结果少于该数量时启用 Bing RSS 兜底，默认 5")
    parser.add_argument("--no-rss-fallback", action="store_true",
                        help="关闭 Bing RSS 兜底，仅使用 HTML 页面")
    parser.add_argument("--no-google-fallback", action="store_true",
                        help="关闭 Google News RSS 兜底")
    parser.add_argument("--bing-host", choices=["auto", "www", "cn"], default="auto",
                        help="Bing 域名策略：auto 先试 www 再试 cn；服务器访问异常时可指定 cn")
    parser.add_argument("--check-search", action="store_true",
                        help="只做 Bing 新闻搜索预检并保存诊断，不采集数据")
    parser.add_argument("--inspect-search", action="store_true",
                        help="输出每个 Bing 域名的状态码/最终URL/标题/解析数量，用于服务器诊断")
    parser.add_argument("--debug-query", default="China poverty 2024",
                        help="配合 --check-search/--inspect-search 使用的测试查询")
    args = parser.parse_args()
    if args.retry_failed and not args.download_only:
        parser.error("--retry-failed 必须与 --download-only 一起使用")
    try:
        keywords, query_meta, plan_info = build_query_plan(
            args.keywords, args.scope, args.languages, args.countries
        )
    except ValueError as exc:
        parser.error(str(exc))
    collector = EnglishNewsCollector(
        args.run_id,
        args.delay,
        timeout=args.timeout,
        bing_host=args.bing_host,
        rss_fallback=not args.no_rss_fallback,
        rss_threshold=args.rss_threshold,
        google_fallback=not args.no_google_fallback,
        proxy=args.proxy,
        proxy_host=args.proxy_host,
        proxy_port=args.proxy_port,
        use_proxy=args.use_proxy,
    )
    if args.inspect_search:
        ok = collector.inspect_search(args.debug_query)
        logger.info("搜索诊断完成: %s | run=%s | 目录=%s", "可解析" if ok else "不可解析", collector.run_id, collector.data_dir)
        raise SystemExit(0 if ok else 2)
    if args.check_search:
        ok = collector.check_search(query=args.debug_query)
        logger.info("搜索预检完成: %s | run=%s | 目录=%s", "通过" if ok else "失败", collector.run_id, collector.data_dir)
        raise SystemExit(0 if ok else 2)
    if args.download_only:
        articles = collector._load_json()
        if not articles:
            parser.error(f"{collector.data_dir} 下没有 news.json，无法执行 --download-only")
    else:
        articles = collector.collect(
            keywords, args.years, max(1, args.pages), args.limit, args.sources,
            query_meta=query_meta, plan_info=plan_info,
        )
    if args.download or args.download_only:
        collector.download(articles, args.limit, retry_failed=args.retry_failed)
    logger.info("完成: run=%s, 目录=%s, 记录=%s", collector.run_id, collector.data_dir, len(articles))


if __name__ == "__main__":
    main()

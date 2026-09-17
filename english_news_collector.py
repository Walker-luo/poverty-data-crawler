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
import csv
import datetime as dt
import json
import logging
import re
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("english_news")

# English news is kept in a separate subdirectory so it can live alongside
# Chinese news under data/processed/news without mixing the two datasets.
BASE_DIR = Path("data/processed/news/en")
# Backward compatibility for runs created before the directory was moved.
LEGACY_BASE_DIR = Path("data/processed/env_news")
KEYWORDS = [
    "China poverty", "China poverty alleviation", "China poverty reduction",
    "China poverty eradication", "targeted poverty alleviation China",
    "China rural revitalization", "China rural development",
    "China common prosperity", "China poverty governance",
]

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
          "is_official", "source_type", "search_keyword"]


def domain_matches(url: str, domains: Iterable[str]) -> bool:
    host = urlparse(url).netloc.lower().split(":", 1)[0]
    return any(host == d or host.endswith("." + d) for d in domains)


def source_from_url(url: str, fallback: str = "") -> str:
    for name, domains in MEDIA.items():
        if domain_matches(url, domains):
            return name
    return fallback or urlparse(url).netloc


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
    BASE_URL = "https://www.bing.com/news/search"

    def __init__(self, run_id: Optional[str] = None, delay: float = 1.5,
        timeout: int = 20):
        self.run_id = run_id or dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.data_dir = self._resolve_data_dir(self.run_id)
        self.articles_dir = self.data_dir / "articles"
        self.fail_log = self.data_dir / "fail.log"
        self.delay = delay
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 Chrome/124 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        })
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.articles_dir.mkdir(parents=True, exist_ok=True)

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
                limit: Optional[int] = None, sources: Optional[List[str]] = None) -> List[Dict]:
        existing = self._load_json()
        seen = {a.get("url") for a in existing if a.get("url")}
        output = existing[:]
        domains: List[str] = []
        if sources:
            for source in sources:
                domains.extend(MEDIA.get(source, [source]))
        total = len(keywords) * len(years)
        done = 0
        logger.info("英文新闻采集: %s 个关键词 × %s 年 × %s 页 | 已有 %s 条",
                    len(keywords), len(years), pages, len(existing))
        for year in sorted(years, reverse=True):
            for keyword in keywords:
                done += 1
                query = f"{keyword} {year}"
                for page in range(pages):
                    cards = self._fetch(query, page)
                    if not cards:
                        break
                    page_new = 0
                    for card in cards:
                        url = card["url"]
                        if domains and not domain_matches(url, domains):
                            continue
                        if url in seen:
                            continue
                        seen.add(url)
                        card["search_keyword"] = keyword
                        output.append(card)
                        page_new += 1
                        if limit and len(output) - len(existing) >= limit:
                            self._save(output)
                            logger.info("达到本次 limit=%s，累计新增 %s 条", limit, len(output)-len(existing))
                            return output
                    if page_new == 0:
                        break
                    # 每页落盘，进程中断时保留已经拿到的结果
                    self._save(output)
                if done % 5 == 0 or done == total:
                    logger.info("采集进度: %s/%s | 本 run 总计 %s 条", done, total, len(output))
        self._save(output)
        return output

    def _fetch(self, query: str, page: int) -> List[Dict]:
        time.sleep(self.delay)
        try:
            response = self.session.get(self.BASE_URL, params={
                "q": query, "first": page * 10 + 1, "setmkt": "en-US", "ensearch": "1",
            }, timeout=self.timeout)
            response.raise_for_status()
            return self._parse(response.text)
        except requests.RequestException as exc:
            self._log_fail("search", query, "", str(exc))
            logger.warning("搜索失败 [%s]: %s", query, type(exc).__name__)
            return []

    def _parse(self, html: str) -> List[Dict]:
        soup = BeautifulSoup(html, "lxml")
        cards = soup.select("[class*=news-card], article")
        results = []
        for card in cards:
            link = next((a for a in card.select("a[href]")
                         if a.get("href", "").startswith("http")
                         and "bing.com" not in a.get("href", "")), None)
            if not link:
                continue
            url = link["href"]
            title = re.sub(r"\s+", " ", link.get_text(" ", strip=True))
            if len(title) < 8:
                continue
            text = re.sub(r"\s+", " ", card.get_text(" ", strip=True))
            source = source_from_url(url)
            source_el = card.select_one("[class*=source], [class*=author]")
            if source_el:
                source = source_from_url(url, source_el.get_text(" ", strip=True))
            results.append({
                "id": self._id(url), "title": title[:500], "url": url,
                "source": source, "date": "", "pub_date": parse_date(url, text),
                "summary": text[:500], "is_official": source in {
                    "CGTN", "Xinhua English", "People's Daily Online", "China Daily", "english.gov.cn"
                }, "source_type": "news", "search_keyword": "",
            })
        return results

    @staticmethod
    def _id(url: str) -> str:
        import hashlib
        return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]

    def download(self, articles: List[Dict], limit: Optional[int] = None) -> Tuple[int, int, int]:
        status = self._load_download_status()
        candidates = [a for a in articles
                      if not (self.articles_dir / f"{a['id']}.md").exists()
                      and status.get(a["id"], {}).get("status") != "success"]
        if limit:
            candidates = candidates[:limit]
        total, success, failed, skipped = len(candidates), 0, 0, len(articles) - len(candidates)
        logger.info("正文下载: 待处理 %s 篇 | 已存在跳过 %s 篇", total, skipped)
        for index, article in enumerate(candidates, 1):
            try:
                time.sleep(self.delay)
                response = self.session.get(article["url"], timeout=self.timeout)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, "lxml")
                for tag in soup.select("script,style,nav,footer,header,aside,form,iframe"):
                    tag.decompose()
                container = next((soup.select_one(sel) for sel in
                                  ["article", "main", "[class*=article-body]", "[class*=article-content]", "[class*=content]"]
                                  if soup.select_one(sel)), soup.body)
                text = "\n\n".join(p.get_text(" ", strip=True) for p in container.select("p") if len(p.get_text(strip=True)) > 20) if container else ""
                if len(text) < 100:
                    raise ValueError("正文过短")
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
            if index % 10 == 0 or index == total:
                logger.info("正文进度: %s/%s | 成功 %s | 失败 %s | 跳过 %s", index, total, success, failed, skipped)
        self._write_summary(len(articles), success, failed, skipped)
        return success, failed, skipped

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
            f"- Records: {total}\n- Download success: {success}\n"
            f"- Download failed: {failed}\n- Existing/skipped: {skipped}\n",
            encoding="utf-8")

    def _log_fail(self, step: str, item: str, url: str, reason: str) -> None:
        with self.fail_log.open("a", encoding="utf-8") as handle:
            handle.write(f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] step={step} | id={item} | url={url[:300]} | reason={reason}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="国际英文新闻采集器")
    parser.add_argument("--run-id", help="复用已有 run；采集时增量写回，下载时断点续传")
    parser.add_argument("--keywords", nargs="+", default=KEYWORDS)
    parser.add_argument("--sources", nargs="+", choices=list(MEDIA), help="只保留指定媒体")
    parser.add_argument("--years", nargs="+", type=int, default=list(range(2000, dt.date.today().year + 1)))
    parser.add_argument("--pages", type=int, default=3)
    parser.add_argument("--limit", type=int, help="本次采集或下载最多处理多少条")
    parser.add_argument("--download", action="store_true", help="下载新闻正文")
    parser.add_argument("--download-only", action="store_true", help="只下载已有 run，不重新搜索")
    parser.add_argument("--delay", type=float, default=1.5)
    args = parser.parse_args()
    collector = EnglishNewsCollector(args.run_id, args.delay)
    if args.download_only:
        articles = collector._load_json()
        if not articles:
            parser.error(f"{collector.data_dir} 下没有 news.json，无法执行 --download-only")
    else:
        articles = collector.collect(args.keywords, args.years, max(1, args.pages), args.limit, args.sources)
    if args.download or args.download_only:
        collector.download(articles, args.limit)
    logger.info("完成: run=%s, 目录=%s, 记录=%s", collector.run_id, collector.data_dir, len(articles))


if __name__ == "__main__":
    main()

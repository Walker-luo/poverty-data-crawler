# ============================================
# Bing 新闻搜索爬虫 — 不挑IP，无反爬
# ============================================
import re
import logging
from typing import List, Dict, Optional
from bs4 import BeautifulSoup

from spiders.base_spider import BaseSpider
from config.settings import (
    OFFICIAL_MEDIA,
    OFFICIAL_NAME_MAP,
    KEYWORDS,
)

logger = logging.getLogger(__name__)


class BingNewsSpider(BaseSpider):
    """
    Bing 新闻搜索爬虫

    和百度 NewsSpider 接口完全一致，直接替换引擎:
      python main.py --source news --strategy maximize --engine bing
    """

    BASE_URL = "https://www.bing.com/news/search"

    def __init__(self, source_filter: str = "official"):
        super().__init__(name="bing_news")
        self.source_filter = source_filter
        self._official_domains = {
            info["domain"] for info in OFFICIAL_MEDIA.values()
        }
        self._official_names = set(OFFICIAL_NAME_MAP.keys())

    # ================================================================
    # 公开接口 (同名，直接替换)
    # ================================================================

    def search_by_keyword(
        self, keyword: str, max_pages: int = 3
    ) -> List[Dict[str, str]]:
        all_articles = []
        for page in range(max_pages):
            articles = self._fetch_page(keyword, page)
            if not articles:
                break
            all_articles.extend(articles)
        return all_articles

    def search_by_site(
        self, keyword: str, site_domain: str, max_pages: int = 2
    ) -> List[Dict[str, str]]:
        query = f"site:{site_domain} {keyword}"
        all_articles = []
        for page in range(max_pages):
            articles = self._fetch_page(query, page)
            if not articles:
                break
            all_articles.extend(articles)
        return all_articles

    def search_by_years(
        self, keyword: str, years: List[int], max_pages_per_year: int = 2
    ) -> List[Dict[str, str]]:
        """按年份分区搜索 — 直接在 query 中加年份"""
        all_articles = []
        for year in years:
            query = f"{keyword} {year}"
            for page in range(max_pages_per_year):
                articles = self._fetch_page(query, page)
                if not articles:
                    break
                all_articles.extend(articles)
        return all_articles

    def search_top_official_sites(
        self, keyword: str = "扶贫", top_n: int = 5
    ) -> List[Dict[str, str]]:
        central_media = [
            m for m in OFFICIAL_MEDIA.values() if m["level"] == "中央"
        ][:top_n]
        all_articles = []
        for media in central_media:
            articles = self.search_by_site(keyword, media["domain"], max_pages=2)
            for a in articles:
                if not a.get("source"):
                    a["source"] = media["name"]
            all_articles.extend(articles)
            logger.info(f"  {media['name']}({media['domain']}): {len(articles)} 条")
        return all_articles

    def maximize(
        self,
        years: Optional[List[int]] = None,
        keywords: Optional[List[str]] = None,
        max_pages: int = 5,
    ) -> List[Dict[str, str]]:
        """
        全量采集 — 逐年关键词搜索 + 最近年份补齐

        设计逻辑:
          - 主力: 每个关键词 × 每年的独立搜索 (覆盖最全，不重复)
          - 补齐: 每个关键词无年份搜索 (捕获Bing年份过滤漏掉的，仅限最近2年)
          - 不做 site: 搜索 (Bing site: 只返回导航卡片，产生0条真实数据)
        """
        years = years or list(range(2013, 2027))
        keywords = keywords or KEYWORDS
        years_desc = sorted(years, reverse=True)
        all_articles = []
        seen_urls = set()

        logger.info(
            f"Bing全量采集: {len(keywords)} 关键词 × {len(years)} 年, "
            f"翻页深度 {max_pages}"
        )

        # ---- 主力: 逐年关键词搜索 ----
        logger.info("=== 逐年关键词搜索 ===")
        keyword_empty_streak = {}
        requests_this_phase = 0

        for kw in keywords:
            keyword_empty_streak[kw] = 0
            kw_new = 0
            for year in years_desc:
                if keyword_empty_streak[kw] >= 3:
                    logger.debug(f"  {kw}: 连续3年无结果，跳过 {year}及更早")
                    break
                articles = self.search_by_years(kw, [year], max_pages_per_year=max_pages)
                requests_this_phase += max_pages  # 估算
                new = 0
                for a in articles:
                    if a["url"] not in seen_urls:
                        seen_urls.add(a["url"])
                        a["search_mode"] = "year"
                        all_articles.append(a)
                        new += 1
                if new == 0:
                    keyword_empty_streak[kw] += 1
                else:
                    keyword_empty_streak[kw] = 0
                    kw_new += new
            if kw_new:
                logger.info(f"  {kw}: +{kw_new} 条")
            else:
                logger.warning(f"  {kw}: 所有年份 0 命中")

        logger.info(f"逐年搜索完成: 累计 {len(all_articles)} 条")

        # ---- 补齐: 最近2年无年份过滤搜索 ----
        recent_years = years_desc[:2]
        active_kw = [
            kw for kw in keywords
            if keyword_empty_streak.get(kw, 0) < len(years_desc)
        ]
        logger.info(f"=== 补齐搜索: {len(active_kw)} 关键词 × 最近{len(recent_years)}年 ===")
        for kw in active_kw:
            query = f"{kw} {' '.join(str(y) for y in recent_years)}"
            articles = self.search_by_keyword(query, max_pages=max_pages)
            new = 0
            for a in articles:
                if a["url"] not in seen_urls:
                    seen_urls.add(a["url"])
                    a["search_mode"] = "catchup"
                    all_articles.append(a)
                    new += 1
            if new:
                logger.info(f"  {kw}: +{new} 条")

        logger.info(f"=== 全量采集完成: 共 {len(all_articles)} 条 ===")
        return all_articles

    # ================================================================
    # 内部实现 — Bing HTML 解析
    # ================================================================

    def _fetch_page(
        self, query: str, page: int = 0
    ) -> List[Dict[str, str]]:
        """获取并解析一页 Bing 新闻搜索结果"""
        params = {
            "q": query,
            "first": page * 10 + 1,  # Bing 分页: 1, 11, 21...
            "FORM": "YFNR",
        }
        html = self.fetch(self.BASE_URL, params=params)
        if not html:
            return []
        articles = self._parse_results(html)
        return articles

    def _parse_results(self, html: str) -> List[Dict[str, str]]:
        """解析 Bing 新闻搜索结果页"""
        soup = BeautifulSoup(html, "lxml")
        articles = []
        cards = soup.select("[class*=news-card]")

        for card in cards:
            try:
                # 标题链接: 第一个指向外部的 a 标签
                title = ""
                url = ""
                for a in card.find_all("a", href=True):
                    href = a["href"]
                    if (href.startswith("http")
                            and "bing.com" not in href
                            and "microsoft.com" not in href
                            and "go.microsoft" not in href):
                        title = a.get_text(strip=True)
                        url = href
                        if len(title) > 5:
                            break

                if not title or not url:
                    continue

                title = re.sub(r"\s+", " ", title).strip()

                # 来源和日期: Bing 有 class="source" 标签
                # 格式: "新华社 on MSN1 天" 或 "腾讯网17 天" 或 "人民网8 个月"
                source = ""
                date = ""
                source_el = card.select_one("[class*=source]")
                if source_el:
                    source_text = source_el.get_text(strip=True)
                    # "新华社 on MSN1 天" → "新华社 1 天"
                    source_text = re.sub(r"\s*on\s+MSN\s*", " ", source_text)
                    # 匹配合并后的: "{来源} {N} {单位}"
                    m = re.match(
                        r"^(.+?)\s*(\d+)\s*(天|小时|分钟|个月|年|秒)\s*$",
                        source_text,
                    )
                    if m:
                        source = m.group(1).strip()
                        date = f"{m.group(2)} {m.group(3)}"

                # 从 URL 中提取绝对日期 (Bing 只给相对时间)
                pub_date = self._extract_url_date(url)

                # 摘要: 从 card body 中取
                summary = ""
                desc_el = card.select_one("[class*=snippet], [class*=body], [class*=desc]")
                if desc_el:
                    summary = desc_el.get_text(strip=True)[:200]
                else:
                    # 用整个 card 文本，去掉来源行和标题
                    full = card.get_text(strip=True)
                    if source and full.startswith(source):
                        full = full[len(source):]
                    if date and full.startswith(date):
                        full = full[len(date):]
                    if title and title in full:
                        full = full[full.index(title) + len(title):]
                    summary = full[:200].strip()

                articles.append({
                    "title": title,
                    "url": url,
                    "date": date,              # Bing 显示时间: "5 年" "2 天"
                    "pub_date": pub_date,      # URL中提取的绝对日期: "2021-02-26"
                    "summary": summary[:200],
                    "source": source,
                    "is_official": self._check_official(source),
                    "search_mode": "",
                })

            except Exception as e:
                logger.debug(f"解析Bing条目失败: {e}")
                continue

        return articles

    # ================================================================
    # 来源判定 (和百度版共用逻辑)
    # ================================================================

    def _is_official(self, article: Dict) -> bool:
        source = article.get("source", "")
        if source in self._official_names:
            return True
        url = article.get("url", "")
        for domain in self._official_domains:
            if domain in url:
                return True
        for name in self._official_names:
            if len(name) >= 3 and name in source:
                return True
        return False

    def _check_official(self, source_name: str) -> bool:
        if source_name in self._official_names:
            return True
        for name in self._official_names:
            if len(name) >= 3 and name in source_name:
                return True
        return False

    @staticmethod
    def _extract_url_date(url: str) -> str:
        """从新闻 URL 路径中提取发布日期

        中国新闻网URL常见格式:
          .../2021-02/26/content_xxx.shtml → 2021-02-26
          .../n1/2021/1018/c436975-xxx.html → 2021-10-18
          .../2026/0731/c42272-xxx.html → 2026-07-31
        """
        if not url:
            return ""
        # 模式1: /YYYY-MM/DD/
        m = re.search(r"/(\d{4})-(\d{2})/(\d{2})/", url)
        if m:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        # 模式2: /YYYY/MMDD/ 或 /n1/YYYY/MMDD/
        m = re.search(r"/(\d{4})/(\d{2})(\d{2})/", url)
        if m:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        # 模式3: .../YYYYMM/tYYYYMMDD_...
        m = re.search(r"/t(\d{8})_", url)
        if m:
            d = m.group(1)
            return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
        return ""

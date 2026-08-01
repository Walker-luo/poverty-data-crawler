# ============================================
# 新闻爬虫 — 百度新闻搜索 + 官方媒体过滤
# 支持三种采集策略:
#   1. keyword   — 关键词搜索
#   2. site      — 站点定向搜索
#   3. maximize  — 时间分区 + 全关键词 + 全站点 组合搜索
# ============================================
import re
import json
import logging
import calendar
from typing import List, Dict, Optional, Tuple
from datetime import datetime
from bs4 import BeautifulSoup

from spiders.base_spider import BaseSpider
from config.settings import (
    OFFICIAL_MEDIA,
    OFFICIAL_NAME_MAP,
    KEYWORDS,
    PRIORITY_KEYWORDS,
    BAIDU_NEWS_CONFIG,
)

logger = logging.getLogger(__name__)


class NewsSpider(BaseSpider):
    """
    扶贫新闻爬虫

    策略:
    1. 百度新闻搜索 (按关键词)  → 覆盖面广，按来源名过滤官方媒体
    2. 百度 site: 定向搜索     → 精确搜索指定官方媒体站点
    """

    BASE_URL = BAIDU_NEWS_CONFIG["base_url"]

    def __init__(self, source_filter: str = "official"):
        """
        Args:
            source_filter: 来源过滤策略
                - "official": 仅保留官方媒体
                - "all": 不过滤
                - "commercial": 仅保留商业媒体
        """
        super().__init__(name="news_spider")
        self.source_filter = source_filter
        self._official_domains = {
            info["domain"] for info in OFFICIAL_MEDIA.values()
        }
        self._official_names = set(OFFICIAL_NAME_MAP.keys())

    # ================================================================
    # 公开接口
    # ================================================================

    def search_by_keyword(
        self,
        keyword: str,
        max_pages: int = 3,
    ) -> List[Dict[str, str]]:
        """
        按关键词搜索新闻

        Args:
            keyword: 搜索关键词
            max_pages: 最大翻页数
        """
        all_articles = []
        for page in range(max_pages):
            articles = self._fetch_page(keyword, page)
            if not articles:
                break
            all_articles.extend(articles)
            logger.info(
                f"  关键词 '{keyword}' 第{page+1}页: +{len(articles)} 条"
            )
        return all_articles

    def search_by_site(
        self,
        keyword: str,
        site_domain: str,
        max_pages: int = 2,
    ) -> List[Dict[str, str]]:
        """
        在指定站点内搜索新闻 (site:domain.com)

        Args:
            keyword: 搜索关键词
            site_domain: 目标站点域名 (如 xinhuanet.com)
            max_pages: 最大翻页数
        """
        query = f"site:{site_domain} {keyword}"
        logger.info(f"  site搜索: site:{site_domain} {keyword}")

        all_articles = []
        for page in range(max_pages):
            articles = self._fetch_page(query, page)
            if not articles:
                break
            all_articles.extend(articles)

        return all_articles

    def search_all_official(
        self,
        keywords: Optional[List[str]] = None,
        max_per_keyword: int = 3,
    ) -> List[Dict[str, str]]:
        """
        按关键词在全部官方媒体中搜索 (综合策略)

        先用百度新闻通用搜索 → 按来源名过滤官方媒体
        """
        keywords = keywords or PRIORITY_KEYWORDS
        all_articles = []

        for kw in keywords:
            articles = self.search_by_keyword(kw, max_pages=max_per_keyword)
            all_articles.extend(articles)

        return all_articles

    # ================================================================
    # 时间分区搜索 — 突破翻页限制的核心手段
    # ================================================================

    @staticmethod
    def _year_timestamps(year: int) -> Tuple[int, int]:
        """返回某年的 Unix 时间戳范围 (bt, et)"""
        bt = int(calendar.timegm(datetime(year, 1, 1).timetuple()))
        et = int(calendar.timegm(datetime(year, 12, 31, 23, 59, 59).timetuple()))
        return bt, et

    def search_by_timerange(
        self,
        keyword: str,
        bt: int,
        et: int,
        max_pages: int = 2,
    ) -> List[Dict[str, str]]:
        """
        在指定时间窗口内搜索 — 突破翻页深度限制

        百度新闻每次搜索最多返回 76 页。通过将时间切分为
        月/年级别窗口，每个窗口独立搜索，即可绕过此限制
        覆盖更长时间跨度的数据。
        """
        all_articles = []
        for page in range(max_pages):
            params_override = {"bt": str(bt), "et": str(et)}
            articles = self._fetch_page(keyword, page, extra_params=params_override)
            if not articles:
                break
            all_articles.extend(articles)
        return all_articles

    def search_by_years(
        self,
        keyword: str,
        years: List[int],
        max_pages_per_year: int = 2,
    ) -> List[Dict[str, str]]:
        """
        按年份分区搜索 — 将关键词搜索拆分为逐年查询

        Example:
            spider.search_by_years("精准扶贫", years=list(range(2015,2027)))
            → 12 年 × 2 页 × ~10条 = ~240 条
        """
        all_articles = []
        for year in years:
            bt, et = self._year_timestamps(year)
            articles = self.search_by_timerange(
                keyword, bt, et, max_pages=max_pages_per_year
            )
            if articles:
                all_articles.extend(articles)
                logger.info(
                    f"  {keyword} @ {year}年: {len(articles)} 条"
                )
        return all_articles

    # ================================================================
    # 全量采集入口
    # ================================================================

    def maximize(
        self,
        years: Optional[List[int]] = None,
        keywords: Optional[List[str]] = None,
        top_official_sites: int = 10,
        max_pages: int = 5,
    ) -> List[Dict[str, str]]:
        """
        全量采集：时间分区 + 全关键词 + 站点定向 组合搜索

        三管齐下:
          1. 所有关键词 × 逐年分区搜索 (突破翻页限制)
          2. 每关键词的普通搜索 (补齐无日期的结果)
          3. Top N 官方媒体 site: 定向搜索
        """
        years = years or list(range(2013, 2027))  # 精准扶贫至今
        keywords = keywords or KEYWORDS  # 全量 15 个关键词
        all_articles = []
        seen_urls = set()

        logger.info(
            f"全量采集启动: {len(keywords)} 关键词 × {len(years)} 年 "
            f"+ {top_official_sites} site定向"
        )

        # ---- Phase 1: 逐年分区搜索 (核心，绕过翻页限制) ----
        logger.info("=== Phase 1/3: 逐年分区搜索 ===")
        years_desc = sorted(years, reverse=True)  # 从新到旧搜
        keyword_empty_streak = {}  # 记录每个关键词连续空结果的年数
        wasted = 0

        for kw in keywords:
            keyword_empty_streak[kw] = 0
            kw_start_count = len(all_articles)

            for year in years_desc:
                # 连续 3 年搜不到 → 跳过更早的年份 (早期没有这个关键词的数据)
                if keyword_empty_streak[kw] >= 3:
                    logger.info(f"  跳过 {kw} @ {year}年及更早 (连续 {keyword_empty_streak[kw]} 年无结果)")
                    wasted += len([y for y in years_desc if y <= year])
                    break

                bt, et = self._year_timestamps(year)
                articles = self.search_by_timerange(kw, bt, et, max_pages=2)

                if not articles:
                    keyword_empty_streak[kw] += 1
                    logger.debug(f"  {kw} @ {year}年: 0 条 (连续空 {keyword_empty_streak[kw]})")
                    continue
                else:
                    keyword_empty_streak[kw] = 0  # 有新结果，重置连续空计数

                for a in articles:
                    url = a.get("url", "")
                    if url not in seen_urls:
                        seen_urls.add(url)
                        a["search_mode"] = "year_partition"
                        all_articles.append(a)

            kw_count = len(all_articles) - kw_start_count
            if kw_count:
                logger.info(f"  {kw}: +{kw_count} 条 (累计 {len(all_articles)})")
            else:
                logger.warning(f"  {kw}: 全部年份 0 条命中，跳过")

        logger.info(f"Phase 1 完成: 累计 {len(all_articles)} 条 (跳过约 {wasted} 次无意义请求)")

        # ---- Phase 2: 普通关键词搜索 (补齐) ----
        # 跳过 Phase 1 全部为 0 的关键词
        active_keywords = [
            kw for kw in keywords if keyword_empty_streak.get(kw, 0) < len(years_desc)
        ]
        dead_keywords = [kw for kw in keywords if kw not in active_keywords]
        if dead_keywords:
            logger.info(f"Phase 2 跳过无产出的关键词: {', '.join(dead_keywords)}")

        logger.info(f"=== Phase 2/3: 普通关键词搜索 ({len(active_keywords)} 个关键词) ===")
        for kw in active_keywords:
            articles = self.search_by_keyword(kw, max_pages=3)
            for a in articles:
                url = a.get("url", "")
                if url not in seen_urls:
                    seen_urls.add(url)
                    a["search_mode"] = "keyword"
                    all_articles.append(a)

        logger.info(f"Phase 2 完成: 累计 {len(all_articles)} 条")

        # ---- Phase 3: site: 定向搜索官方媒体 ----
        logger.info(f"=== Phase 3/3: site: 定向搜索 Top {top_official_sites} 媒体 ===")
        central_media = [
            m for m in OFFICIAL_MEDIA.values() if m["level"] == "中央"
        ][:top_official_sites]

        site_kws = active_keywords[:5]  # 只用高产出关键词
        for media in central_media:
            for kw in site_kws:
                articles = self.search_by_site(kw, media["domain"], max_pages=2)
                new_count = 0
                for a in articles:
                    url = a.get("url", "")
                    if url not in seen_urls:
                        seen_urls.add(url)
                        if not a.get("source"):
                            a["source"] = media["name"]
                        a["search_mode"] = "site"
                        all_articles.append(a)
                        new_count += 1
                if new_count:
                    logger.info(f"  {media['name']} × {kw}: +{new_count} 条")

        logger.info(f"=== 全量采集完成: 共 {len(all_articles)} 条 ===")
        return all_articles

    # ================================================================
    # 内部实现
    # ================================================================

    def _fetch_page(
        self, query: str, page: int = 0, extra_params: Optional[Dict] = None
    ) -> List[Dict[str, str]]:
        """获取并解析单页搜索结果"""
        params = {
            "word": query,
            "pn": page * BAIDU_NEWS_CONFIG["results_per_page"],
            "cl": "2",
            "ct": "1",
            "tn": "newstitledy",
            "rn": BAIDU_NEWS_CONFIG["results_per_page"],
            "ie": "utf-8",
            "bt": "0",
            "et": "0",
        }
        if extra_params:
            params.update(extra_params)

        html = self.fetch(self.BASE_URL, params=params)
        if not html:
            return []

        articles = self._parse_results(html)
        logger.info(
                f"  本次总获取: {len(articles)} 条"
            )

        return articles

    def _parse_results(self, html: str) -> List[Dict[str, str]]:
        """解析百度新闻搜索结果页 (从 s-data JSON 提取)"""
        soup = BeautifulSoup(html, "lxml")
        articles = []

        for item in soup.select("div.result-op"):
            try:
                data = self._extract_sdata(item)
                if not data:
                    continue

                title = self._clean_html(data.get("title", ""))
                url = data.get("titleUrl", "")
                if not title or not url:
                    continue

                source_name = data.get("sourceName", "") or data.get("rtses", "")

                article = {
                    "title": title,
                    "url": url,
                    "date": data.get("dispTime", ""),
                    "summary": self._clean_html(data.get("summary", ""))[:200],
                    "source": source_name,
                    "is_official": self._check_official(source_name),
                    "has_image": data.get("hasImg", False),
                }
                articles.append(article)

            except Exception as e:
                logger.debug(f"解析条目失败: {e}")
                continue

        return articles

    # ================================================================
    # 来源判定
    # ================================================================

    def _is_official(self, article: Dict) -> bool:
        """判断文章是否来自官方媒体"""
        source = article.get("source", "")

        # 方法1: 来源名称精确匹配
        if source in self._official_names:
            return True

        # 方法2: URL 域名匹配
        url = article.get("url", "")
        for domain in self._official_domains:
            if domain in url:
                return True

        # 方法3: 来源名称模糊匹配 (如 "人民网" 在官方媒体列表中)
        for name in self._official_names:
            if len(name) >= 3 and name in source:
                return True

        return False

    def _check_official(self, source_name: str) -> bool:
        """检查来源名称是否官方媒体"""
        if source_name in self._official_names:
            return True
        for name in self._official_names:
            if len(name) >= 3 and name in source_name:
                return True
        return False

    # ================================================================
    # 静态工具方法
    # ================================================================

    @staticmethod
    def _extract_sdata(item: BeautifulSoup) -> Optional[Dict]:
        """从 result-op 节点中提取 s-data JSON"""
        comment = item.find(string=re.compile(r"s-data:"))
        if not comment:
            return None
        try:
            json_str = comment.split("s-data:", 1)[1].strip()
            return json.loads(json_str)
        except (json.JSONDecodeError, IndexError):
            return None

    @staticmethod
    def _clean_html(text: str) -> str:
        """去除 HTML 标签"""
        if not text:
            return ""
        return re.sub(r"<[^>]+>", "", text)

    # ================================================================
    # 站点定向搜索 (site:domain)
    # ================================================================

    def search_top_official_sites(
        self,
        keyword: str = "扶贫",
        top_n: int = 5,
    ) -> List[Dict[str, str]]:
        """
        对排名前 N 的官方媒体做 site: 定向搜索

        Args:
            keyword: 搜索关键词
            top_n: 覆盖前 N 个官方媒体站点
        """
        # 优先中央级媒体
        central_media = [
            m for m in OFFICIAL_MEDIA.values() if m["level"] == "中央"
        ][:top_n]

        all_articles = []
        for media in central_media:
            domain = media["domain"]
            articles = self.search_by_site(keyword, domain, max_pages=2)
            # site 搜索时给每条结果标注来源
            for a in articles:
                if not a.get("source"):
                    a["source"] = media["name"]
            all_articles.extend(articles)
            logger.info(
                f"  {media['name']}({domain}): {len(articles)} 条"
            )

        return all_articles

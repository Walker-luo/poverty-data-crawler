# ============================================
# Bing 新闻搜索爬虫
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
    Bing 新闻搜索爬虫 — 无封IP风险，稳定采集

    用法:
      python main.py --source news --strategy maximize --engine bing
    """

    BASE_URL = "https://www.bing.com/news/search"

    def __init__(self, source_filter: str = "official"):
        super().__init__(name="bing", delay=1.0)  # Bing 反爬宽松，1秒间隔即可
        self.source_filter = source_filter
        self._official_domains = {
            info["domain"] for info in OFFICIAL_MEDIA.values()
        }
        self._official_names = set(OFFICIAL_NAME_MAP.keys())
        # 锚定国际版，防止中国IP被重定向到 cn.bing.com（cn 版不支持 /news/search）
        self.session.headers["Accept-Language"] = "en-US,en;q=0.9,zh-CN;q=0.8"

    # ================================================================
    # 公开接口
    # ================================================================

    def _preflight_check(self) -> bool:
        """连通性预检：发一次测试请求，确认 Bing 可访问且能解析到结果"""
        logger.info("🔍 连通性预检: 测试 Bing 新闻搜索...")
        try:
            articles = self._fetch_page("扶贫", page=0)
            if articles:
                logger.info(f"✅ 预检通过: 获取到 {len(articles)} 条结果 (示例: {articles[0]['title'][:50]}...)")
                return True
            else:
                logger.warning("⚠️ 预检警告: 请求成功但未解析到任何新闻卡片")
                logger.warning("   可能原因: Bing 地区限制 / HTML 结构变化 / 网络代理")
                return False
        except Exception as e:
            logger.error(f"❌ 预检失败: {e}")
            return False

    def search_by_keyword(
        self, keyword: str, max_pages: int = 3
    ) -> List[Dict[str, str]]:
        """按关键词搜索"""
        all_articles = []
        for page in range(max_pages):
            articles = self._fetch_page(keyword, page)
            if not articles:
                break
            all_articles.extend(articles)
        return all_articles

    def search_by_years(
        self, keyword: str, years: List[int], max_pages_per_year: int = 3
    ) -> List[Dict[str, str]]:
        """按年份分区搜索 — 将年份拼入搜索词"""
        all_articles = []
        for year in years:
            query = f"{keyword} {year}"
            for page in range(max_pages_per_year):
                articles = self._fetch_page(query, page)
                if not articles:
                    break
                all_articles.extend(articles)
        return all_articles

    def search_by_site(
        self, keyword: str, site_domain: str, max_pages: int = 2
    ) -> List[Dict[str, str]]:
        """站点定向搜索 (Bing 上效果不佳，不推荐)"""
        query = f"site:{site_domain} {keyword}"
        all_articles = []
        for page in range(max_pages):
            articles = self._fetch_page(query, page)
            if not articles:
                break
            all_articles.extend(articles)
        return all_articles

    def maximize(
        self,
        years: Optional[List[int]] = None,
        keywords: Optional[List[str]] = None,
        max_pages: int = 5,
    ) -> List[Dict[str, str]]:
        """
        全量采集

        两阶段:
          1. 逐年搜索 — 每个关键词×每年独立查询 (主力)
          2. 补齐搜索 — 关键词+最近2年 (捕获遗漏)
        """
        years = years or list(range(2013, 2027))
        keywords = keywords or KEYWORDS
        years_desc = sorted(years, reverse=True)
        all_articles = []
        seen_urls = set()

        total_combos = len(keywords) * len(years_desc)
        done = 0

        logger.info("=" * 50)
        logger.info(
            f"📡 Bing 全量采集: {len(keywords)} 关键词 × {len(years)} 年"
            f"  |  翻页深度 {max_pages}"
            f"  |  预计 {total_combos * max_pages} 次请求"
        )
        logger.info("=" * 50)

        # ---- 连通性预检 ----
        if not self._preflight_check():
            logger.error("连通性预检未通过，请检查网络或 Bing 可访问性后重试")
            return []

        # ---- Phase 1: 逐年关键词搜索 ----
        logger.info("▸ Phase 1: 逐年关键词搜索")
        kw_empty_streak: Dict[str, int] = {}
        official_count = 0

        for kw in keywords:
            kw_empty_streak[kw] = 0
            kw_total = 0
            kw_official = 0
            for year in years_desc:
                done += 1
                if kw_empty_streak[kw] >= 3:
                    skipped_years = [y for y in years_desc if y <= year]
                    logger.debug(
                        f"  ⏭ {kw} 连续 {kw_empty_streak[kw]} 年无结果, "
                        f"跳过 {year} 及更早 ({len(skipped_years)} 年)"
                    )
                    done += len(skipped_years) - 1
                    break

                articles = self.search_by_years(kw, [year], max_pages_per_year=max_pages)
                new = 0
                for a in articles:
                    if a["url"] not in seen_urls:
                        seen_urls.add(a["url"])
                        a["search_mode"] = "year"
                        all_articles.append(a)
                        new += 1
                        if a["is_official"]:
                            kw_official += 1

                kw_total += new

                if new == 0:
                    kw_empty_streak[kw] += 1
                else:
                    kw_empty_streak[kw] = 0

                # 每5个年份组合输出一次进度
                if done % max(1, total_combos // 10) == 0:
                    logger.info(
                        f"  进度 {done}/{total_combos} "
                        f"({done * 100 // total_combos}%)  "
                        f"已收集 {len(all_articles)} 条"
                    )

            if kw_total:
                logger.info(
                    f"  ✓ {kw}: {kw_total} 条"
                    + (f" (官媒 {kw_official})" if kw_official else "")
                )
            else:
                logger.warning(f"  ✗ {kw}: 所有年份均无结果")

        official_count = sum(1 for a in all_articles if a["is_official"])
        logger.info(
            f"Phase 1 完成: {len(all_articles)} 条 "
            f"(官媒 {official_count}, 其他 {len(all_articles) - official_count})"
        )

        # ---- Phase 2: 补齐搜索 ----
        recent_years = years_desc[:2]
        active_kw = [
            kw for kw in keywords
            if kw_empty_streak.get(kw, 0) < len(years_desc)
        ]
        logger.info(
            f"▸ Phase 2: 补齐搜索 "
            f"({len(active_kw)} 个有效关键词 × 最近 {recent_years} 年)"
        )

        phase2_total = 0
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
            phase2_total += new
            if new:
                logger.info(f"  ✓ {kw}: +{new} 条")

        logger.info(f"Phase 2 完成: +{phase2_total} 条, 累计 {len(all_articles)} 条")

        # ---- 汇总 ----
        final_official = sum(1 for a in all_articles if a["is_official"])
        logger.info("=" * 50)
        logger.info(
            f"✅ 全量采集完成!"
            f"  共 {len(all_articles)} 条"
            f"  |  官媒 {final_official} 条 ({final_official * 100 // max(1, len(all_articles))}%)"
            f"  |  其他 {len(all_articles) - final_official} 条"
            f"  |  请求 {self.request_count} 次"
            f"  |  拦截 {self.block_count} 次"
        )
        logger.info("=" * 50)
        return all_articles

    # ================================================================
    # 内部: 页面获取与解析
    # ================================================================

    def _fetch_page(
        self, query: str, page: int = 0
    ) -> List[Dict[str, str]]:
        """获取一页搜索结果并解析"""
        params = {
            "q": query,
            "first": page * 10 + 1,
            "FORM": "YFNR",
            "setmkt": "en-US",       # 锚定国际版，防止中国IP被重定向到 cn.bing.com
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

        if not cards:
            # 诊断日志：无结果时输出 HTML 概况，方便排查
            text_preview = html[:800].replace("\n", " ")[:400]
            logger.warning(
                f"未匹配到新闻卡片 (news-card) | "
                f"HTML长度: {len(html)} | "
                f"开头: {text_preview}..."
            )
            # 尝试备用选择器
            fallback_cards = soup.select(".news-card, [class*=card], [class*=result]")
            if fallback_cards:
                logger.info(f"备用选择器匹配到 {len(fallback_cards)} 个候选元素")
            return []

        for card in cards:
            try:
                # 标题 & 链接 — 第一个指向外部的 a 标签
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

                # 来源 & 发布时间 — Bing 的 class="source" 标签
                #   原始格式: "新华社 on MSN1 天" / "腾讯网17 天" / "人民网8 个月"
                #   解析后:   source="新华社"  date="1 天"
                source = ""
                date = ""
                source_el = card.select_one("[class*=source]")
                if source_el:
                    source_text = source_el.get_text(strip=True)
                    source_text = re.sub(r"\s*on\s+MSN\s*", " ", source_text)
                    m = re.match(
                        r"^(.+?)\s*(\d+)\s*(天|小时|分钟|个月|年|秒)\s*$",
                        source_text,
                    )
                    if m:
                        source = m.group(1).strip()
                        date = f"{m.group(2)} {m.group(3)}"

                # 从 URL 中提取发布日期 (Bing 只给相对时间)
                pub_date = self._extract_url_date(url)

                # 摘要
                summary = ""
                desc_el = card.select_one("[class*=snippet], [class*=body], [class*=desc]")
                if desc_el:
                    summary = desc_el.get_text(strip=True)[:200]
                else:
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
                    "date": date,
                    "pub_date": pub_date,
                    "summary": summary[:200],
                    "source": source,
                    "is_official": self._check_official(source),
                    "search_mode": "",
                })

            except Exception:
                continue

        return articles

    # ================================================================
    # 来源判定
    # ================================================================

    def _check_official(self, source_name: str) -> bool:
        """判定来源是否为官方媒体 (名称精确匹配 + 模糊匹配)"""
        if source_name in self._official_names:
            return True
        for name in self._official_names:
            if len(name) >= 3 and name in source_name:
                return True
        return False

    @staticmethod
    def _extract_url_date(url: str) -> str:
        """从新闻 URL 路径中提取发布日期

        常见格式:
          .../2021-02/26/content_xxx.shtml  → 2021-02-26
          .../n1/2021/1018/c436975-xxx.html → 2021-10-18
          .../2026/0731/c42272-xxx.html     → 2026-07-31
        """
        if not url:
            return ""
        m = re.search(r"/(\d{4})-(\d{2})/(\d{2})/", url)
        if m:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        m = re.search(r"/(\d{4})/(\d{2})(\d{2})/", url)
        if m:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        m = re.search(r"/t(\d{8})_", url)
        if m:
            d = m.group(1)
            return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
        return ""

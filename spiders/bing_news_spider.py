# ============================================
# Bing 新闻搜索爬虫
# ============================================
import re
import logging
import datetime
import requests
from typing import List, Dict, Optional
from bs4 import BeautifulSoup

from spiders.base_spider import BaseSpider
from config.settings import (
    CRAWL_CONFIG,
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
        """站点定向搜索 — site:domain 语法，Phase 3 主力"""
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

        三阶段:
          1. 关键词搜索 — 纯关键词 + 深翻页 (Bing 新闻不支持年份过滤)
          2. 补齐搜索 — 关键词+最近2年 (捕获遗漏)
          3. 站点定向 — site: 核心官媒域名 (补齐官媒覆盖)
        """
        keywords = keywords or KEYWORDS
        all_articles = []
        seen_urls = set()

        logger.info("=" * 50)
        logger.info(
            f"📡 Bing 全量采集: {len(keywords)} 关键词"
            f"  |  翻页深度 {max_pages}"
        )
        logger.info("=" * 50)

        # ---- 连通性预检 ----
        if not self._preflight_check():
            logger.error(
                "❌ 连通性预检失败，采集终止。"
                "请检查网络或使用 --engine baidu 切换到百度搜索。"
            )
            return []

        # ---- Phase 1: 关键词搜索（无年份过滤） ----
        logger.info("▸ Phase 1: 关键词搜索")
        for kw in keywords:
            articles = self.search_by_keyword(kw, max_pages=max_pages)
            new = 0
            kw_official = 0
            for a in articles:
                if a["url"] not in seen_urls:
                    seen_urls.add(a["url"])
                    a["search_mode"] = "keyword"
                    all_articles.append(a)
                    new += 1
                    if a["is_official"]:
                        kw_official += 1
            if new:
                logger.info(
                    f"  ✓ {kw}: {new} 条"
                    + (f" (官媒 {kw_official})" if kw_official else "")
                )
            else:
                logger.debug(f"  - {kw}: 无结果")

        official_count = sum(1 for a in all_articles if a["is_official"])
        logger.info(
            f"Phase 1 完成: {len(all_articles)} 条 "
            f"(官媒 {official_count}, 其他 {len(all_articles) - official_count})"
        )

        # ---- Phase 2: 补齐搜索 ----
        logger.info("▸ Phase 2: 补齐搜索 (关键词 × 最近2年)")
        current_year = datetime.datetime.now().year
        recent_years = [current_year, current_year - 1]
        phase2_total = 0
        for kw in keywords:
            query = f"{kw} {' '.join(str(y) for y in recent_years)}"
            articles = self.search_by_keyword(query, max_pages=max(1, max_pages // 2))
            new = 0
            for a in articles:
                if a["url"] not in seen_urls:
                    seen_urls.add(a["url"])
                    a["search_mode"] = "catchup"
                    all_articles.append(a)
                    new += 1
            phase2_total += new
            if new:
                logger.debug(f"  {kw}: +{new} 条")

        logger.info(f"Phase 2 完成: +{phase2_total} 条, 累计 {len(all_articles)} 条")

        # ---- Phase 3: 核心官媒点名搜索 (补齐官媒覆盖) ----
        # Bing 不支持 site: 操作符，改用"来源名 + 关键词"组合搜索
        # 挑选覆盖最少的核心官媒进行定向搜索
        central_names = [
            m["name"] for m in OFFICIAL_MEDIA.values()
            if m["level"] == "中央"
        ][:12]  # 只取前12个最核心的，控制请求量
        # 只用前2个最高频关键词
        site_kws = keywords[:2]
        phase3_total = 0
        logger.info(
            f"▸ Phase 3: 官媒点名搜索 "
            f"({len(central_names)} 个核心官媒 × {len(site_kws)} 个关键词)"
        )
        for src_name in central_names:
            for kw in site_kws:
                query = f"{src_name} {kw}"
                articles = self._fetch_page(query, page=0)
                new = 0
                for a in articles:
                    if a["url"] not in seen_urls:
                        seen_urls.add(a["url"])
                        a["search_mode"] = "source"
                        all_articles.append(a)
                        new += 1
                if new:
                    logger.debug(f"  \"{src_name} {kw}\": +{new} 条")
                    phase3_total += new

        logger.info(f"Phase 3 完成: +{phase3_total} 条, 累计 {len(all_articles)} 条")

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

    def _preflight_check(self) -> bool:
        """连通性预检：发一次测试请求，确认 Bing 可访问且能解析到结果"""
        logger.info("🔍 连通性预检: 测试 Bing 新闻搜索...")
        try:
            articles = self._fetch_page("扶贫", page=0)
            if articles:
                logger.info(
                    f"✅ 预检通过: 获取到 {len(articles)} 条结果 "
                    f"(示例: {articles[0]['title'][:50]}...)"
                )
                return True
            else:
                logger.warning("⚠️ 预检警告: 请求成功但未解析到任何新闻卡片")
                logger.warning("   可能原因: Bing 地区限制 / HTML 结构变化 / 网络代理")
                return False
        except Exception as e:
            logger.error(f"❌ 预检失败: {e}")
            return False

    def _fetch_page(
        self, query: str, page: int = 0
    ) -> List[Dict[str, str]]:
        """获取一页搜索结果并解析"""
        params = {
            "q": query,
            "first": page * 10 + 1,
        }
        html = self._fetch_with_diag(self.BASE_URL, params=params)
        if not html:
            return []
        articles = self._parse_results(html)
        return articles

    def _fetch_with_diag(
        self, url: str, params: Optional[Dict] = None
    ) -> Optional[str]:
        """带重定向诊断的请求，自动应对地区限制"""
        for attempt in range(2):
            self._delay()
            self.request_count += 1
            self._rotate_ua()
            try:
                resp = self.session.get(
                    url, params=params,
                    timeout=CRAWL_CONFIG["timeout"],
                    allow_redirects=True,
                )
                # 记录重定向链
                if resp.history:
                    chain = " → ".join(
                        f"{r.status_code} {r.url}" for r in resp.history
                    )
                    logger.debug(f"重定向链: {chain} → {resp.status_code} {resp.url}")
                    # 被重定向到首页则是地域限制
                    final = resp.url.rstrip("/")
                    if final in (
                        "https://www.bing.com", "https://cn.bing.com",
                    ):
                        if attempt == 0:
                            # 第一次被重定向了，加反重定向参数再试
                            logger.debug(
                                "检测到地区限制重定向，追加 setmkt=en-US + ensearch=1"
                            )
                            params = dict(params)
                            params["setmkt"] = "en-US"
                            params["ensearch"] = "1"
                            continue  # 用新参数重试
                        else:
                            logger.warning(
                                f"⚠️ Bing 搜索请求被重定向到首页，反重定向参数无效 "
                                f"(最终URL: {resp.url})"
                            )
                            return None

                resp.raise_for_status()
                if resp.apparent_encoding:
                    resp.encoding = resp.apparent_encoding
                return resp.text

            except requests.RequestException as e:
                # 网络超时/错误直接放弃
                logger.error(f"[{self.name}] 请求失败: {e}")
                return None

        return None

    def _parse_results(self, html: str) -> List[Dict[str, str]]:
        """解析 Bing 新闻搜索结果页"""
        soup = BeautifulSoup(html, "lxml")
        articles = []
        cards = soup.select("[class*=news-card]")

        # 无 news-card 时的诊断
        if not cards:
            # 检测 Bing "无结果" 页面 — 静默返回空
            if soup.select("[class*=no-result]"):
                return []
            # 检测是否是普通网页搜索(非新闻)页面
            # Bing 在新闻结果不足时会 fallback 到网页搜索结果
            web_results = soup.select("[class*=b_algo]")
            if web_results:
                logger.debug(
                    f"Bing 返回网页搜索结果 (非新闻) | {len(web_results)} 个 b_algo 节点"
                )
                # 尝试用通用选择器解析
                cards = soup.select("li.b_algo, .b_algo h2, [class*=b_title]")
                if cards:
                    logger.info(f"从网页搜索结果中提取到 {len(cards)} 个条目")
            else:
                # 既不是新闻页也不是结果页，记录日志但不打WARNING（太吵）
                logger.debug(
                    f"未找到 news-card | HTML长度: {len(html)}"
                )
                # 尝试备选选择器
                for sel in [".news-card", "[class*=card]", "[class*=result]", "article"]:
                    fallback = soup.select(sel)
                    if fallback:
                        logger.debug(f"备选选择器 '{sel}' 命中 {len(fallback)} 个节点")

        # ---- 解析 news-card 卡片 ----
        for card in cards:
            try:
                # 如果 card 不是标准 news-card，尝试从备选结构提取
                title, url = self._extract_title_url(card)
                if not title or not url:
                    continue

                title = re.sub(r"\s+", " ", title).strip()

                # 来源 & 发布时间
                source, date = self._extract_source_date(card)
                # 回退: data-author 属性 (Bing 的新版 HTML 有此属性)
                if not source:
                    source = card.get("data-author", "")

                # 从 URL 中提取发布日期
                pub_date = self._extract_url_date(url)

                # 摘要
                summary = self._extract_summary(card, title, source, date)

                articles.append({
                    "title": title,
                    "url": url,
                    "date": date,
                    "pub_date": pub_date,
                    "summary": summary[:200],
                    "source": source,
                    "is_official": self._check_official(source, url),
                    "search_mode": "",
                })

            except Exception:
                continue

        return articles

    # ---- 解析辅助方法 ----

    def _extract_title_url(self, card) -> tuple:
        """从卡片节点提取标题和URL"""
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
        return title, url

    def _extract_source_date(self, card) -> tuple:
        """从卡片节点提取来源名称和相对日期

        Bing 两种格式:
          旧版: "新华社 on MSN1 天" / "腾讯网17 天" / "人民网8 个月"
          新版: "人民网6d" / "新华社 on MSN3h" (英文缩写)
        """
        source = ""
        date = ""
        source_el = card.select_one("[class*=source]")
        if source_el:
            source_text = source_el.get_text(strip=True)
            source_text = re.sub(r"\s*on\s+MSN\s*", " ", source_text)

            # 新版格式: "来源名Nd/h/m/y/mo/mon/w" (如 "人民网理论频道6d", "腾讯网5mon")
            m = re.match(
                r"^(.*?)\s*(\d+)\s*(d|h|m|y|mo|mon|w)(?![a-zA-Z])\s*$",
                source_text,
            )
            if m:
                unit_map = {
                    "d": "天", "h": "小时", "m": "分钟",
                    "y": "年", "mo": "个月", "mon": "个月", "w": "周",
                }
                source = m.group(1).strip()
                date = f"{m.group(2)} {unit_map.get(m.group(3), m.group(3))}"
                return source, date
                source = m.group(1).strip()
                date = f"{m.group(2)} {unit_map.get(m.group(3), m.group(3))}"
                return source, date

            # 旧版格式: "来源名17 天" / "来源名8 个月"
            m = re.match(
                r"^(.+?)\s*(\d+)\s*(天|小时|分钟|个月|年|秒)\s*$",
                source_text,
            )
            if m:
                source = m.group(1).strip()
                date = f"{m.group(2)} {m.group(3)}"
                return source, date

            # 无日期：整段作为来源名
            source = source_text.strip()

        return source, date

    def _extract_summary(self, card, title: str, source: str, date: str) -> str:
        """从卡片提取摘要文本"""
        desc_el = card.select_one(
            "[class*=snippet], [class*=body], [class*=desc]"
        )
        if desc_el:
            return desc_el.get_text(strip=True)[:200]

        # 回退：取整张卡片文本，去掉已知部分
        full = card.get_text(strip=True)
        for prefix in (source, date, title):
            if prefix and full.startswith(prefix):
                full = full[len(prefix):]
            elif prefix and prefix in full:
                full = full[full.index(prefix) + len(prefix):]
        return full[:200].strip()

    # ================================================================
    # 来源判定
    # ================================================================

    def _check_official(self, source_name: str, url: str = "") -> bool:
        """判定来源是否为官方媒体 (名称匹配 + URL域名匹配)"""
        # 方法1: 来源名称精确匹配
        if source_name in self._official_names:
            return True
        # 方法2: URL 域名匹配 (处理 Bing 上来源名称为数字/乱码的情况)
        if url:
            for domain in self._official_domains:
                if domain in url:
                    return True
        # 方法3: 来源名称模糊匹配 (如 "人民网健康频道" 包含 "人民网")
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

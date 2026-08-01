# ============================================
# 政府网站扶贫数据爬虫
# 目标: 国家乡村振兴局等政府网站
# ============================================
import re
import logging
from typing import List, Dict, Optional
from bs4 import BeautifulSoup

from spiders.base_spider import BaseSpider

logger = logging.getLogger(__name__)

# 政府网站配置
GOV_SITES = {
    "nrra": {
        "name": "国家乡村振兴局",
        "base_url": "https://www.nrra.gov.cn",
        "encoding": "utf-8",
    },
    "cpad": {
        "name": "国务院扶贫办（历史）",
        "base_url": "https://www.cpad.gov.cn",
        "encoding": "utf-8",
    },
}


class GovPovertySpider(BaseSpider):
    """政府网站扶贫政策/数据爬虫"""

    def __init__(self):
        super().__init__(name="gov_spider")
        self.gov_configs = GOV_SITES

    def parse_article_list(
        self,
        html: str,
        list_pattern: Optional[Dict] = None,
    ) -> List[Dict[str, str]]:
        """
        解析文章列表页

        Args:
            html: 列表页 HTML
            list_pattern: 自定义解析规则（CSS选择器）

        Returns:
            [{title, url, date, source}, ...]
        """
        soup = BeautifulSoup(html, "lxml")
        articles = []

        # 默认政府网站列表解析规则
        default_selectors = {
            "list_container": "ul.list-tit, ul.right_list, div.list-content",
            "item": "li",
            "title": "a",
            "date": "span, em.date",
            "link": "a",
        }
        selectors = list_pattern or default_selectors

        container = soup.select_one(selectors["list_container"])
        if not container:
            container = soup  # 回退到全页搜索

        items = container.select(selectors["item"])
        for item in items:
            try:
                link_tag = item.select_one(selectors["link"])
                if not link_tag or not link_tag.get("href"):
                    continue

                article = {
                    "title": link_tag.get_text(strip=True),
                    "url": self._normalize_url(link_tag["href"]),
                    "date": self._extract_date(item, selectors.get("date")),
                    "source": self.name,
                }
                articles.append(article)
            except Exception as e:
                logger.debug(f"解析文章条目失败: {e}")
                continue

        logger.info(f"[{self.name}] 解析到 {len(articles)} 篇文章")
        return articles

    def parse_article_detail(self, html: str) -> Dict[str, Optional[str]]:
        """
        解析文章详情页

        Returns:
            {title, publish_date, source, content, author}
        """
        soup = BeautifulSoup(html, "lxml")
        detail = {
            "title": None,
            "publish_date": None,
            "source": None,
            "content": None,
            "author": None,
        }

        # 标题
        title_tag = soup.select_one(
            "h1.article-title, h1.bt, h1.title, div.detail-tit h2, h1"
        )
        if title_tag:
            detail["title"] = title_tag.get_text(strip=True)

        # 发布日期
        date_patterns = [
            r"(\d{4}[-/年]\d{1,2}[-/月]\d{1,2})",
            r"(\d{4}-\d{2}-\d{2})",
        ]
        page_text = soup.get_text()
        for pattern in date_patterns:
            match = re.search(pattern, page_text)
            if match:
                detail["publish_date"] = match.group(1)
                break

        # 正文
        content_tag = soup.select_one(
            "div.article-content, div.TRS_Editor, div#zoom, "
            "div.news-content, div.content, div.detail-con, article"
        )
        if content_tag:
            # 移除 script/style 标签
            for tag in content_tag(["script", "style"]):
                tag.decompose()
            detail["content"] = content_tag.get_text("\n", strip=True)

        return detail

    def _normalize_url(self, url: str) -> str:
        """补全相对 URL"""
        if url.startswith("http"):
            return url
        # 默认基于 NRRA 的 base_url
        base = self.gov_configs.get("nrra", {}).get("base_url", "")
        if url.startswith("/"):
            return base.rstrip("/") + url
        if url.startswith("./"):
            return base.rstrip("/") + url[1:]
        return base.rstrip("/") + "/" + url

    def _extract_date(
        self, item: BeautifulSoup, selector: Optional[str]
    ) -> Optional[str]:
        """从列表项中提取日期"""
        if not selector:
            return None
        date_tag = item.select_one(selector)
        if date_tag:
            text = date_tag.get_text(strip=True)
            match = re.search(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}", text)
            if match:
                return match.group()
        return None

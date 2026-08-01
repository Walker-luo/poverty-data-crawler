# ============================================
# 百度新闻搜索爬虫
# ============================================
import re
import json
import logging
from typing import List, Dict, Optional
from bs4 import BeautifulSoup, Comment

from spiders.base_spider import BaseSpider

logger = logging.getLogger(__name__)


class BaiduNewsSpider(BaseSpider):
    """百度新闻搜索爬虫 — 通过关键词搜索扶贫相关新闻"""

    BASE_URL = "https://news.baidu.com/ns"

    def __init__(self):
        super().__init__(name="baidu_news")

    def search(
        self,
        keyword: str,
        page: int = 0,
        count: int = 20,
    ) -> List[Dict[str, str]]:
        """
        搜索新闻

        Args:
            keyword: 搜索关键词
            page: 页码（从0开始）
            count: 每页条数
        """
        params = {
            "word": keyword,
            "pn": page * count,
            "cl": "2",
            "ct": "1",
            "tn": "newstitledy",
            "rn": count,
            "ie": "utf-8",
            "bt": "0",
            "et": "0",
        }

        html = self.fetch(self.BASE_URL, params=params)
        if not html:
            return []

        return self._parse_results(html)

    def _parse_results(self, html: str) -> List[Dict[str, str]]:
        """解析百度新闻搜索结果（从 s-data JSON 中提取）"""
        soup = BeautifulSoup(html, "lxml")
        articles = []

        # 每条新闻在 div.result-op.c-container 中
        # 数据嵌入在 <!--s-data:{...}--> 注释里
        result_items = soup.select("div.result-op")

        for item in result_items:
            try:
                # 从 s-data 注释中提取 JSON
                data = self._extract_sdata(item)
                if not data:
                    continue

                title = self._strip_html(data.get("title", ""))
                url = data.get("titleUrl", "")
                if not title or not url:
                    continue

                articles.append({
                    "title": title,
                    "url": url,
                    "date": data.get("dispTime", ""),
                    "summary": self._strip_html(data.get("summary", ""))[:200],
                    "source": data.get("sourceName", "") or data.get("rtses", ""),
                    "has_image": data.get("hasImg", False),
                })

            except Exception as e:
                logger.debug(f"解析条目失败: {e}")
                continue

        logger.info(f"[{self.name}] 搜索到 {len(articles)} 条新闻")
        return articles

    @staticmethod
    def _extract_sdata(item: BeautifulSoup) -> Optional[Dict]:
        """从 result-op 节点中提取 s-data JSON"""
        # 查找包含 s-data: 的注释节点
        comment = item.find(string=re.compile(r"s-data:"))
        if not comment:
            return None
        try:
            json_str = comment.split("s-data:", 1)[1].strip()
            return json.loads(json_str)
        except (json.JSONDecodeError, IndexError):
            return None

    @staticmethod
    def _strip_html(text: str) -> str:
        """去除 HTML 标签（如 <em>关键词</em>）"""
        if not text:
            return ""
        return re.sub(r"<[^>]+>", "", text)

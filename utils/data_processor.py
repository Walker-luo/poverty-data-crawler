# ============================================
# 数据清洗与处理工具
# ============================================
import re
import logging
import hashlib
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta

from config.settings import KEYWORDS

logger = logging.getLogger(__name__)


class DataProcessor:
    """数据清洗与标准化"""

    def __init__(self):
        self._crawl_date = datetime.now()

    # ================================================================
    # 文本清洗
    # ================================================================

    @staticmethod
    def clean_text(text: Optional[str]) -> str:
        """清洗文本：去空白、去特殊字符"""
        if not text:
            return ""
        text = text.replace("\xa0", " ")       # 非断行空格
        text = text.replace("　", " ")      # 全角空格
        text = re.sub(r"\s+", " ", text)        # 合并多个空白
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)  # 控制字符
        return text.strip()

    # ================================================================
    # 日期处理
    # ================================================================

    @staticmethod
    def normalize_date(date_str: Optional[str]) -> Optional[str]:
        """标准化日期为 YYYY-MM-DD 格式"""
        if not date_str:
            return None

        patterns = [
            (r"(\d{4})年(\d{1,2})月(\d{1,2})日", "%Y-%m-%d"),
            (r"(\d{4})-(\d{1,2})-(\d{1,2})", "%Y-%m-%d"),
            (r"(\d{4})/(\d{1,2})/(\d{1,2})", "%Y-%m-%d"),
            (r"(\d{4})\.(\d{1,2})\.(\d{1,2})", "%Y-%m-%d"),
        ]

        for pattern, fmt in patterns:
            match = re.search(pattern, date_str)
            if match:
                try:
                    dt = datetime.strptime(
                        f"{match.group(1)}-{match.group(2).zfill(2)}-{match.group(3).zfill(2)}",
                        "%Y-%m-%d",
                    )
                    return dt.strftime("%Y-%m-%d")
                except ValueError:
                    pass
        return date_str

    def parse_relative_date(self, date_str: Optional[str]) -> Optional[str]:
        """
        将 Bing/搜索引擎的相对日期转为绝对日期

        中文格式: "3 天", "5 小时", "2 个月", "1 年", "30 分钟"
        英文格式: "6d", "3h", "5mon", "1y", "2w"

        Returns:
            YYYY-MM-DD 格式的日期字符串，无法解析返回 None
        """
        if not date_str:
            return None
        date_str = date_str.strip()

        # 中文格式
        m = re.match(r"(\d+)\s*(天|小时|分钟|个月|年|周|秒)", date_str)
        if m:
            num = int(m.group(1))
            unit = m.group(2)
            return self._calc_relative_date(num, unit)

        # 英文格式 (Bing 新版)
        m = re.match(r"(\d+)\s*(d|h|m|y|mo|mon|w)$", date_str, re.IGNORECASE)
        if m:
            num = int(m.group(1))
            unit = m.group(2).lower()
            unit_map = {
                "d": "天", "h": "小时", "m": "分钟",
                "y": "年", "mo": "个月", "mon": "个月", "w": "周",
            }
            return self._calc_relative_date(num, unit_map.get(unit, "天"))

        return None

    def _calc_relative_date(self, num: int, unit: str) -> str:
        """根据数值和单位计算绝对日期"""
        now = self._crawl_date
        if unit in ("小时", "分钟", "秒"):
            return now.strftime("%Y-%m-%d")
        elif unit == "天":
            return (now - timedelta(days=num)).strftime("%Y-%m-%d")
        elif unit == "周":
            return (now - timedelta(weeks=num)).strftime("%Y-%m-%d")
        elif unit in ("个月",):
            return (now - timedelta(days=num * 30)).strftime("%Y-%m-%d")
        elif unit == "年":
            return (now - timedelta(days=num * 365)).strftime("%Y-%m-%d")
        return now.strftime("%Y-%m-%d")

    def resolve_publish_date(self, article: Dict[str, Any]) -> str:
        """
        解析发布日期，优先级:
          1. pub_date (从 URL 提取，如 "2023-05-15"，最可靠)
          2. date 经过 normalize_date (百度绝对日期 "2023年5月15日")
          3. date 经过 parse_relative_date (Bing 相对日期 "3 天")
          4. 空字符串
        """
        # 优先使用 URL 中提取的日期
        pub_date = (article.get("pub_date") or "").strip()
        if pub_date and re.match(r"^\d{4}-\d{2}-\d{2}$", pub_date):
            return pub_date

        date_str = (article.get("date") or "").strip()
        if not date_str:
            return ""

        # 尝试绝对日期格式
        normalized = self.normalize_date(date_str)
        if normalized and re.match(r"^\d{4}-\d{2}-\d{2}$", normalized):
            return normalized

        # 尝试相对日期格式
        relative = self.parse_relative_date(date_str)
        if relative:
            return relative

        return ""

    # ================================================================
    # 内容提取
    # ================================================================

    @staticmethod
    def generate_id(text: str) -> str:
        """为文章生成唯一 ID（基于标题的 MD5）"""
        return hashlib.md5(text.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def extract_keywords(text: str, keyword_list: List[str] = None) -> List[str]:
        """从文本中提取命中的关键词"""
        if keyword_list is None:
            keyword_list = KEYWORDS
        return sorted(set(kw for kw in keyword_list if kw in text))

    @staticmethod
    def extract_numbers(text: str) -> List[float]:
        """从文本中提取数字（如金额、人数等）"""
        pattern = r"(\d+(?:\.\d+)?)(?:万|亿|元|人|户|个)"
        matches = re.findall(pattern, text)
        return [float(m) for m in matches]

    # ================================================================
    # 文章清洗
    # ================================================================

    def clean_article(self, article: Dict[str, Any]) -> Dict[str, Any]:
        """清洗单篇文章数据，计算派生字段"""
        title = self.clean_text(article.get("title"))
        summary = self.clean_text(article.get("summary"))
        combined = f"{title} {summary}"

        return {
            "id": self.generate_id(title),
            "title": title,
            "url": article.get("url", ""),
            "publishDate": self.resolve_publish_date(article),
            "pub_date": self.resolve_publish_date(article),  # 兼容旧字段名
            "summary": summary[:500] if summary else "",
            "content": self.clean_text(article.get("content")),
            "source": article.get("source", ""),
            "is_official": article.get("is_official", False),
            "search_mode": article.get("search_mode", ""),
            "keywords": self.extract_keywords(combined),
            "crawl_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    @staticmethod
    def deduplicate(articles: List[Dict]) -> List[Dict]:
        """基于 URL 去重"""
        seen = set()
        result = []
        for article in articles:
            url = article.get("url", "")
            if url not in seen:
                seen.add(url)
                result.append(article)
        logger.info(f"去重: {len(articles)} -> {len(result)} 条")
        return result

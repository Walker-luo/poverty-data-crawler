# ============================================
# 数据清洗与处理工具
# ============================================
import re
import logging
import hashlib
from typing import Dict, List, Optional, Any
from datetime import datetime

logger = logging.getLogger(__name__)


class DataProcessor:
    """数据清洗与标准化"""

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

    @staticmethod
    def generate_id(text: str) -> str:
        """为文章生成唯一 ID（基于标题的 MD5）"""
        return hashlib.md5(text.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def extract_keywords(text: str, keyword_list: List[str]) -> List[str]:
        """从文本中提取命中的关键词"""
        return sorted(set(kw for kw in keyword_list if kw in text))

    @staticmethod
    def extract_numbers(text: str) -> List[float]:
        """从文本中提取数字（如金额、人数等）"""
        pattern = r"(\d+(?:\.\d+)?)(?:万|亿|元|人|户|个)"
        matches = re.findall(pattern, text)
        return [float(m) for m in matches]

    @staticmethod
    def clean_article(article: Dict[str, Any]) -> Dict[str, Any]:
        """清洗单篇文章数据"""
        return {
            "id": DataProcessor.generate_id(article.get("title", "")),
            "title": DataProcessor.clean_text(article.get("title")),
            "url": article.get("url", ""),
            "date": DataProcessor.normalize_date(article.get("date")),
            "pub_date": article.get("pub_date", ""),
            "summary": article.get("summary", ""),
            "content": DataProcessor.clean_text(article.get("content")),
            "source": article.get("source", ""),
            "is_official": article.get("is_official", False),
            "search_mode": article.get("search_mode", ""),
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

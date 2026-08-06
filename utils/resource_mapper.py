# ============================================
# 爬虫数据 → MongoDB Resource Schema 映射器
# ============================================
"""
将爬虫输出的原始字段映射为 anti-poverty-server 的 Resource 文档格式。

用法:
    from utils.resource_mapper import ResourceMapper
    mapper = ResourceMapper()
    resource_doc = mapper.map_to_resource(spider_article)
"""

import re
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta

from config.settings import KEYWORDS, OFFICIAL_NAME_MAP

logger = logging.getLogger(__name__)

# ============================================================
# 扶贫阶段 → 特征词映射
# ============================================================
STAGE_PATTERNS = {
    "traditional": [
        "救济", "五保", "低保", "社会救助", "鳏寡孤独",
        "救济式扶贫", "输血式扶贫",
    ],
    "reform": [
        "改革开放", "联产承包", "家庭联产承包", "市场经济",
        "体制改革", "农村改革",
    ],
    "development": [
        "开发式扶贫", "八七扶贫", "贫困县", "以工代赈",
        "整村推进", "产业开发", "扶贫开发",
    ],
    "poverty": [
        "脱贫攻坚", "精准扶贫", "建档立卡", "两不愁三保障",
        "摘帽", "防止返贫", "脱贫摘帽", "扶贫攻坚",
    ],
    "precision": [
        "精准到户", "第一书记", "驻村工作队", "五个一批",
        "因户施策", "精准施策", "精准识别",
    ],
    "rural": [
        "乡村振兴", "美丽乡村", "农业农村现代化", "三农",
        "城乡融合", "农村人居环境",
    ],
}

# ============================================================
# 论述类型 → 来源特征词映射
# ============================================================
ACADEMIC_PATTERNS = [
    "大学", "学院", "研究院", "学术", "学报",
    "社科院", "农科院", "中国科学院", "工程院",
]

INSTITUTIONAL_PATTERNS = [
    "人民政府", "国务院", "发改委", "扶贫办",
    "农业农村部", "财政部", "教育部", "卫健委",
]


class ResourceMapper:
    """
    爬虫数据 → Resource Schema 映射器

    将 Bing/百度新闻搜索返回的扁平字段映射为 MongoDB Resource 文档结构，
    自动推断缺失字段: type, category, country, region, contentLanguage,
    keywords, discourseType, stage, publishDate
    """

    def __init__(self, default_country: str = "china", default_region: str = "asia"):
        self.default_country = default_country
        self.default_region = default_region
        self._crawl_date = datetime.now()

    # ================================================================
    # 公开接口
    # ================================================================

    def map_to_resource(self, article: Dict[str, Any]) -> Dict[str, Any]:
        """
        将单篇爬虫文章映射为 Resource 文档

        Args:
            article: 爬虫输出的原始 dict
                {title, url, date, pub_date, summary, source, is_official, search_mode}

        Returns:
            MongoDB Resource 文档 (可直接 mongoimport)
        """
        title = (article.get("title") or "").strip()
        summary = (article.get("summary") or "").strip()
        source = (article.get("source") or "").strip()
        url = (article.get("url") or "").strip()
        is_official = article.get("is_official", False)

        # 合并标题和摘要用于文本分析
        combined_text = f"{title} {summary}"

        # 构建 Resource 文档
        doc = {
            # ---- 直接映射 ----
            "title": title,
            "originalUrl": url,
            "description": summary[:500] if summary else "",
            "source": source,

            # ---- 推断字段 ----
            "type": ["text"],
            "category": self._infer_category(title, summary, url),
            "country": self.default_country,
            "region": self.default_region,
            "contentLanguage": ["中文"],
            "status": "published" if is_official else "pending",
            "publishDate": self._resolve_publish_date(article),
            "keywords": self._extract_keywords(combined_text),
            "discourseType": self._infer_discourse_type(source, is_official),
            "stage": self._infer_stage(combined_text),
            "institution": self._infer_institution(source, is_official, url),

            # ---- 默认空字段 ----
            "content": "",
            "authors": [],
            "views": 0,
            "downloads": 0,
        }

        return doc

    def map_batch(self, articles: List[Dict]) -> List[Dict]:
        """批量映射"""
        return [self.map_to_resource(a) for a in articles]

    # ================================================================
    # 日期处理
    # ================================================================

    def _resolve_publish_date(self, article: Dict) -> Optional[str]:
        """
        解析发布日期，优先级:
          1. pub_date (从 URL 中提取的绝对日期，最可靠)
          2. date (Bing 相对日期 "3 天" / 百度绝对日期 "2023-05-15")
          3. None (留空，由数据库默认值填充)
        """
        # 优先使用 URL 中提取的日期
        pub_date = article.get("pub_date", "")
        if pub_date and self._is_valid_date(pub_date):
            return pub_date

        # 其次解析搜索引擎返回的日期
        date_str = article.get("date", "")
        if date_str:
            parsed = self._parse_relative_date(date_str)
            if parsed:
                return parsed
            # 可能是百度返回的绝对日期
            if self._is_valid_date(date_str):
                return date_str

        return None

    @staticmethod
    def _is_valid_date(date_str: str) -> bool:
        """检查是否为有效的 YYYY-MM-DD 格式日期"""
        if not date_str:
            return False
        return bool(re.match(r"^\d{4}-\d{2}-\d{2}$", date_str))

    def _parse_relative_date(self, date_str: str) -> Optional[str]:
        """
        将 Bing 的相对日期转为绝对日期

        支持格式:
          "3 天" / "5 小时" / "2 个月" / "1 年" / "30 分钟"
          "6d" / "3h" / "5mon" / "1y" (新版 Bing 英文缩写)
        """
        if not date_str:
            return None

        # 中文格式: "3 天", "5 小时", "2 个月", "1 年"
        m = re.match(r"(\d+)\s*(天|小时|分钟|个月|年|周|秒)", date_str)
        if m:
            num = int(m.group(1))
            unit = m.group(2)
            return self._calc_relative(num, unit)

        # 英文格式: "6d", "3h", "5mon", "1y", "2w"
        m = re.match(r"(\d+)\s*(d|h|m|y|mo|mon|w)$", date_str, re.IGNORECASE)
        if m:
            num = int(m.group(1))
            unit = m.group(2).lower()
            unit_map = {
                "d": "天", "h": "小时", "m": "分钟",
                "y": "年", "mo": "个月", "mon": "个月", "w": "周",
            }
            return self._calc_relative(num, unit_map.get(unit, "天"))

        return None

    def _calc_relative(self, num: int, unit: str) -> str:
        """根据相对时间计算绝对日期"""
        if unit in ("小时", "分钟", "秒"):
            # 当天
            return self._crawl_date.strftime("%Y-%m-%d")
        elif unit in ("天",):
            return (self._crawl_date - timedelta(days=num)).strftime("%Y-%m-%d")
        elif unit in ("周",):
            return (self._crawl_date - timedelta(weeks=num)).strftime("%Y-%m-%d")
        elif unit in ("个月",):
            return (self._crawl_date - timedelta(days=num * 30)).strftime("%Y-%m-%d")
        elif unit in ("年",):
            return (self._crawl_date - timedelta(days=num * 365)).strftime("%Y-%m-%d")
        return self._crawl_date.strftime("%Y-%m-%d")

    # ================================================================
    # 分类推断
    # ================================================================

    @staticmethod
    def _infer_category(title: str, summary: str, url: str) -> str:
        """
        推断资源类别 — 新闻爬虫默认为 "news"，
        但检查是否有政策/报告/案例特征

        Returns:
            类别枚举值 (news / policy / reports / cases)
        """
        text = f"{title} {summary} {url}".lower()

        # 政策文件特征
        policy_signals = [
            "通知", "意见", "方案", "条例", "办法", "规定",
            "国务院关于", "关于印发", "实施意见",
        ]
        if any(s in text for s in policy_signals):
            return "policy"

        # 研究报告特征
        report_signals = [
            "研究报告", "蓝皮书", "白皮书", "评估报告",
            "调研报告", "年度报告",
        ]
        if any(s in text for s in report_signals):
            return "reports"

        # 案例特征
        case_signals = [
            "案例", "典型经验", "经验做法", "减贫案例",
            "脱贫案例", "模式研究",
        ]
        if any(s in text for s in case_signals):
            return "cases"

        return "news"

    # ================================================================
    # 关键词提取
    # ================================================================

    @staticmethod
    def _extract_keywords(text: str) -> List[str]:
        """从文本中提取匹配的关键词"""
        if not text:
            return []
        return sorted(set(kw for kw in KEYWORDS if kw in text))

    # ================================================================
    # 论述类型推断
    # ================================================================

    @staticmethod
    def _infer_discourse_type(source: str, is_official: bool) -> List[str]:
        """
        推断论述类型

        - 官方媒体 / 政府网站 → institutional
        - 学术机构 → academic
        - 商业/民间媒体 → civilian
        """
        result = []

        if is_official:
            result.append("institutional")

        if any(pat in source for pat in ACADEMIC_PATTERNS):
            result.append("academic")

        # 如果是官媒且没命中学术特征 → 纯机构论述
        # 如果是商业媒体且没命中学术特征 → 民间论述
        # 如果是商业媒体 + 学术特征 → 两者都有
        if not result:
            result.append("civilian")

        return result

    # ================================================================
    # 扶贫阶段推断
    # ================================================================

    @staticmethod
    def _infer_stage(text: str) -> List[str]:
        """基于关键词匹配推断扶贫阶段"""
        if not text:
            return []
        matched = []
        for stage, patterns in STAGE_PATTERNS.items():
            if any(pat in text for pat in patterns):
                matched.append(stage)
        return matched

    # ================================================================
    # 机构名称推断
    # ================================================================

    @staticmethod
    def _infer_institution(source: str, is_official: bool, url: str) -> str:
        """
        推断发布机构名称

        优先级:
          1. OFFICIAL_NAME_MAP 精确匹配 → 官方标准名称
          2. source 本身不为空 → 直接用
          3. URL 域名作为兜底
        """
        if source and source in OFFICIAL_NAME_MAP:
            info = OFFICIAL_NAME_MAP[source]
            return info.get("name", source)

        if source:
            return source

        # 从 URL 提取域名作为兜底
        if url:
            m = re.search(r"https?://(?:www\.)?([^/]+)", url)
            if m:
                return m.group(1)

        return ""

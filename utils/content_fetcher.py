# ============================================
# 全文抓取工具 — 从新闻 URL 提取正文内容
# ============================================
"""
可选的正文抓取模块。爬虫搜索结果只包含摘要（~200 字符），
此模块从原始 URL 抓取完整的新闻正文，填充 Resource.content 字段。

用法:
    from utils.content_fetcher import ContentFetcher
    fetcher = ContentFetcher(delay=2.0, timeout=15)
    fetcher.fetch_all(resource_docs)

注意:
  - 串行请求，带延迟，尊重源站
  - 单个页面最多提取 10000 字符
  - 失败的 URL 静默跳过，不阻塞整体流程
"""

import re
import logging
import time
from typing import Dict, List, Optional
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from config.settings import CRAWL_CONFIG, HEADERS

logger = logging.getLogger(__name__)

# 正文最大字符数（MongoDB 单文档不宜过大）
MAX_CONTENT_CHARS = 10000

# 正文容器选择器（按优先级）
CONTENT_SELECTORS = [
    "article",
    "[class*=article-body]",
    "[class*=article-content]",
    "[class*=post-content]",
    "[class*=entry-content]",
    "[class*=content]",
    "[id*=content]",
    "[id*=article]",
    "main",
    ".news-content",
    ".text-content",
    "#Article",
]

# 需要移除的无关标签
REMOVE_TAGS = [
    "script", "style", "nav", "footer", "header",
    "aside", "iframe", "noscript", "form",
    "[class*=sidebar]", "[class*=footer]", "[class*=header]",
    "[class*=nav]", "[class*=ad]", "[class*=recommend]",
    "[class*=related]", "[class*=comment]", "[class*=share]",
    "[id*=sidebar]", "[id*=footer]", "[id*=header]",
    "[id*=nav]", "[id*=ad]", "[id*=recommend]",
    "[id*=related]", "[id*=comment]", "[id*=share]",
]


class ContentFetcher:
    """
    新闻正文抓取器

    Attributes:
        delay: 请求间隔（秒），默认 2.0
        timeout: 单次请求超时（秒），默认 15
        max_chars: 正文最大字符数，默认 10000
    """

    def __init__(
        self,
        delay: float = 2.0,
        timeout: int = 15,
        max_chars: int = MAX_CONTENT_CHARS,
    ):
        self.delay = delay
        self.timeout = timeout
        self.max_chars = max_chars
        self.success_count = 0
        self.fail_count = 0
        self.skip_count = 0
        self._session = None

    @property
    def session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update(HEADERS)
        return self._session

    # ================================================================
    # 公开接口
    # ================================================================

    def fetch_one(self, url: str) -> Optional[str]:
        """
        抓取单篇新闻的正文文本

        Args:
            url: 新闻 URL

        Returns:
            清洗后的纯文本正文，失败返回 None
        """
        if not url:
            return None

        try:
            time.sleep(self.delay)
            resp = self.session.get(url, timeout=self.timeout)
            resp.raise_for_status()

            # 自动检测编码
            if resp.apparent_encoding:
                resp.encoding = resp.apparent_encoding

            html = resp.text
            if not html or len(html) < 200:
                logger.debug(f"页面内容过短: {url}")
                self.fail_count += 1
                return None

            text = self._extract_content(html)
            if text:
                self.success_count += 1
                return text
            else:
                self.fail_count += 1
                return None

        except requests.Timeout:
            logger.debug(f"请求超时: {url}")
            self.fail_count += 1
            return None
        except requests.RequestException as e:
            logger.debug(f"请求失败 [{url[:60]}]: {e}")
            self.fail_count += 1
            return None
        except Exception as e:
            logger.debug(f"解析异常 [{url[:60]}]: {e}")
            self.fail_count += 1
            return None

    def fetch_all(self, resources: List[Dict]) -> None:
        """
        批量抓取正文，直接修改每个 resource 的 content 字段

        Args:
            resources: Resource 文档列表，原地修改 content 字段
        """
        total = len(resources)
        logger.info(
            f"📄 开始全文抓取: {total} 篇 "
            f"(延迟 {self.delay}s, 超时 {self.timeout}s)"
        )

        start_time = datetime.now()
        for i, doc in enumerate(resources, 1):
            url = doc.get("originalUrl", "")
            existing_content = doc.get("content", "")

            # 已有内容的跳过
            if existing_content and len(existing_content) > 100:
                self.skip_count += 1
                continue

            if i % 10 == 0 or i == total:
                elapsed = (datetime.now() - start_time).total_seconds()
                logger.info(
                    f"  进度: {i}/{total} | "
                    f"✓ {self.success_count} | ✗ {self.fail_count} | "
                    f"⊘ {self.skip_count} | {elapsed:.0f}s"
                )

            content = self.fetch_one(url)
            if content:
                doc["content"] = content

        elapsed = (datetime.now() - start_time).total_seconds()
        logger.info(
            f"✅ 全文抓取完成: {total} 篇 "
            f"| ✓ {self.success_count} 篇 | ✗ {self.fail_count} 篇 "
            f"| ⊘ {self.skip_count} 篇跳过 | 耗时 {elapsed:.0f}s"
        )

    # ================================================================
    # 正文提取
    # ================================================================

    def _extract_content(self, html: str) -> str:
        """从 HTML 中提取正文文本"""
        soup = BeautifulSoup(html, "lxml")

        # 1. 移除无关标签
        for sel in REMOVE_TAGS:
            for tag in soup.select(sel):
                tag.decompose()

        # 2. 优先匹配正文容器
        content_el = None
        for sel in CONTENT_SELECTORS:
            content_el = soup.select_one(sel)
            if content_el and len(content_el.get_text(strip=True)) > 100:
                break

        # 3. 兜底：取 body 下所有 <p> 标签文本
        if not content_el or len(content_el.get_text(strip=True)) < 100:
            paragraphs = soup.find_all("p")
            if paragraphs:
                texts = []
                for p in paragraphs:
                    t = p.get_text(strip=True)
                    # 跳过太短的段落（可能是导航/页脚残片）
                    if len(t) > 15:
                        texts.append(t)
                text = "\n\n".join(texts)
            else:
                text = soup.body.get_text(strip=True) if soup.body else ""
        else:
            text = content_el.get_text(strip=True)

        # 4. 清洗
        text = self._clean_text(text)

        # 5. 截断
        if len(text) > self.max_chars:
            # 在完整句子边界截断
            cutoff = text.rfind("。", self.max_chars - 200, self.max_chars)
            if cutoff == -1:
                cutoff = self.max_chars
            else:
                cutoff += 1  # 包含句号
            text = text[:cutoff]

        return text if len(text) > 50 else ""

    @staticmethod
    def _clean_text(text: str) -> str:
        """清洗提取的文本"""
        if not text:
            return ""

        # 合并空白
        text = re.sub(r"\s+", " ", text)
        # 移除零宽字符
        text = re.sub(r"[​‌‍‎‏﻿]", "", text)
        # 合并连续换行
        text = re.sub(r"\n{3,}", "\n\n", text)
        # 去首尾空白
        text = text.strip()

        return text

    def close(self):
        """关闭会话"""
        if self._session:
            self._session.close()
            self._session = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

# ============================================
# 新闻 Markdown 下载器 — 正文下载 + 数据库索引
# ============================================
"""
从已爬取的新闻 URL 下载正文，保存为 .md 文件，并生成数据库导入 CSV 索引。

用法:
    python main.py --download                  # 下载最新 run 的正文
    python main.py --download --run-id ID      # 下载指定 run
    python main.py --download --delay 3.0      # 自定义请求间隔

输出结构:
    data/processed/news/{run_id}/
    └── articles/
        ├── a1b2c3d4.md        # 纯正文 Markdown
        ├── e5f6g7h8.md
        └── db_import.csv       # 数据库批量导入索引
"""

import re
import csv
import logging
import time
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime

import requests
from bs4 import BeautifulSoup, Tag
import urllib3

from config.settings import KEYWORDS, HEADERS, CRAWL_CONFIG

# 部分中文新闻网站 SSL 证书配置不规范，关闭警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

# 正文容器选择器（与 ContentFetcher 保持一致，按优先级）
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

# 需移除的噪音标签
REMOVE_SELECTORS = [
    "script", "style", "nav", "footer", "header",
    "aside", "iframe", "noscript", "form",
    "[class*=sidebar]", "[class*=footer]", "[class*=header]",
    "[class*=nav]", "[class*=ad]", "[class*=recommend]",
    "[class*=related]", "[class*=comment]", "[class*=share]",
    "[id*=sidebar]", "[id*=footer]", "[id*=header]",
    "[id*=nav]", "[id*=ad]", "[id*=recommend]",
    "[id*=related]", "[id*=comment]", "[id*=share]",
]

# 内容相关的 HTML 标签（需要保留并转换为 Markdown）
CONTENT_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6",
                "blockquote", "ul", "ol", "li", "pre", "code",
                "strong", "em", "b", "i", "a", "br"}


class NewsMarkdownDownloader:
    """
    新闻正文 Markdown 下载器

    下载已爬取的新闻 URL 正文 → 清洗 → 保存为 .md 文件
    并生成数据库批量导入 CSV 索引
    """

    MAX_CONTENT_CHARS = 10000

    def __init__(
        self,
        run_id: Optional[str] = None,
        delay: float = 2.0,
        timeout: int = 15,
    ):
        self.delay = delay
        self.timeout = timeout
        self.request_count = 0
        self.success_count = 0
        self.fail_count = 0
        self.skip_count = 0
        self._session = None

        # 定位数据目录
        if run_id:
            self.run_id = run_id
        else:
            self.run_id = self._find_latest_run()

        self.data_dir = Path(f"data/processed/news/{self.run_id}")
        self.csv_path = self.data_dir / "news.csv"
        self.articles_dir = self.data_dir / "articles"

        if not self.csv_path.exists():
            raise FileNotFoundError(f"未找到 CSV 数据: {self.csv_path}")

        logger.info(f"初始化下载器: run_id={self.run_id}, delay={delay}s")
        logger.info(f"数据目录: {self.data_dir}")

    @property
    def session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update(HEADERS)
            self._session.verify = False  # 部分中文新闻站 SSL 证书不规范
        return self._session

    # ================================================================
    # 公开接口
    # ================================================================

    def download_all(self, limit: Optional[int] = None) -> Tuple[int, int, int]:
        """
        批量下载全部文章正文为 .md 文件

        Args:
            limit: 限制下载数量（测试用）

        Returns:
            (success, fail, skip) 计数
        """
        articles = self._load_articles()
        if limit:
            articles = articles[:limit]
        total = len(articles)
        logger.info(
            f"📥 开始下载正文: {total} 篇 "
            f"(延迟 {self.delay}s, 超时 {self.timeout}s)"
        )

        # 创建 articles 目录
        self.articles_dir.mkdir(parents=True, exist_ok=True)

        start_time = datetime.now()
        for i, article in enumerate(articles, 1):
            article_id = article.get("id", "")
            if not article_id:
                continue

            md_path = self.articles_dir / f"{article_id}.md"

            # 断点续传：已存在且内容足够则跳过
            if md_path.exists() and md_path.stat().st_size > 100:
                self.skip_count += 1
                if i % 20 == 0:
                    self._log_progress(i, total, start_time)
                continue

            # 下载正文
            url = article.get("url", "")
            content = self._fetch_and_extract(url)
            self.request_count += 1

            if content:
                self._save_markdown(article, content, md_path)
                self.success_count += 1
            else:
                self.fail_count += 1

            # 进度日志
            if i % 10 == 0 or i == total:
                self._log_progress(i, total, start_time)

        elapsed = (datetime.now() - start_time).total_seconds()
        logger.info(
            f"✅ 正文下载完成: {total} 篇 "
            f"| ✓ {self.success_count} 篇 | ✗ {self.fail_count} 篇 "
            f"| ⊘ {self.skip_count} 篇跳过 | 请求 {self.request_count} 次 "
            f"| 耗时 {elapsed:.0f}s"
        )

        return self.success_count, self.fail_count, self.skip_count

    # ================================================================
    # 内部: 数据加载
    # ================================================================

    def _load_articles(self) -> List[Dict]:
        """加载爬取到的文章数据"""
        articles = []
        with open(self.csv_path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                articles.append(row)
        return articles

    def _find_latest_run(self) -> str:
        """找到最新的 run_id"""
        news_dir = Path("data/processed/news")
        run_dirs = sorted(
            [d for d in news_dir.iterdir()
             if d.is_dir() and not d.name.startswith(".")],
            reverse=True,
        )
        if not run_dirs:
            raise FileNotFoundError("未找到任何爬取数据目录")
        return run_dirs[0].name

    # ================================================================
    # 内部: HTTP 请求 + 内容提取
    # ================================================================

    def _fetch_and_extract(self, url: str) -> Optional[str]:
        """下载 URL 并提取正文为 Markdown 格式"""
        if not url:
            return None

        try:
            time.sleep(self.delay)
            resp = self.session.get(url, timeout=self.timeout)
            resp.raise_for_status()

            if resp.apparent_encoding:
                resp.encoding = resp.apparent_encoding

            html = resp.text
            if not html or len(html) < 200:
                logger.debug(f"页面内容过短: {url}")
                return None

            markdown = self._html_to_markdown(html)
            if markdown and len(markdown) > 50:
                return markdown
            return None

        except requests.Timeout:
            logger.debug(f"请求超时: {url[:80]}")
            return None
        except requests.RequestException as e:
            logger.debug(f"请求失败 [{url[:60]}]: {e}")
            return None
        except Exception as e:
            logger.debug(f"解析异常 [{url[:60]}]: {e}")
            return None

    def _html_to_markdown(self, html: str) -> str:
        """从 HTML 提取正文并转换为 Markdown"""
        soup = BeautifulSoup(html, "lxml")

        # 1. 移除噪音标签
        for sel in REMOVE_SELECTORS:
            for tag in soup.select(sel):
                tag.decompose()

        # 2. 找到正文容器
        content_el = None
        for sel in CONTENT_SELECTORS:
            content_el = soup.select_one(sel)
            if content_el and len(content_el.get_text(strip=True)) > 100:
                break

        # 3. 提取段落文本
        paragraphs = []

        if content_el and len(content_el.get_text(strip=True)) >= 100:
            # 从正文容器中提取结构化内容
            self._extract_block(content_el, paragraphs)
        else:
            # 兜底：取所有 <p> 标签
            for p in soup.find_all("p"):
                text = p.get_text(strip=True)
                if len(text) > 15:
                    paragraphs.append(text)

        if not paragraphs:
            # 最终兜底：取 body 文本
            body = soup.body
            if body:
                paragraphs.append(body.get_text(strip=True))

        # 4. 清洗每条段落
        cleaned = []
        for p in paragraphs:
            text = self._clean_text(p)
            if len(text) > 5:
                cleaned.append(text)

        # 5. 截断
        markdown = "\n\n".join(cleaned)
        if len(markdown) > self.MAX_CONTENT_CHARS:
            # 在句号处截断
            cutoff = markdown.rfind("。", self.MAX_CONTENT_CHARS - 500,
                                    self.MAX_CONTENT_CHARS)
            if cutoff == -1:
                cutoff = self.MAX_CONTENT_CHARS
            else:
                cutoff += 1
            markdown = markdown[:cutoff]

        return markdown

    def _extract_block(self, element: Tag, paragraphs: List[str]) -> None:
        """递归提取块级元素为段落文本"""
        for child in element.children:
            if isinstance(child, str):
                text = child.strip()
                if len(text) > 5:
                    paragraphs.append(text)
                continue

            if not hasattr(child, "name") or child.name is None:
                continue

            tag = child.name.lower()

            # 跳过噪音
            if tag not in CONTENT_TAGS:
                self._extract_block(child, paragraphs)
                continue

            text = child.get_text(strip=True)
            if not text:
                continue

            if tag in ("p", "blockquote"):
                paragraphs.append(text)
            elif tag.startswith("h") and len(tag) == 2:
                paragraphs.append(f"## {text}")
            elif tag in ("li",):
                paragraphs.append(f"- {text}")
            elif tag in ("pre", "code"):
                paragraphs.append(f"```\n{text}\n```")
            elif tag == "br":
                pass  # 换行 → 后续段落自然处理

    @staticmethod
    def _clean_text(text: str) -> str:
        """清洗文本"""
        if not text:
            return ""
        # 合并空白
        text = re.sub(r"\s+", " ", text)
        # 移除零宽字符
        text = re.sub(r"[​‌‍‎‏﻿]", "", text)
        # 去首尾空白
        return text.strip()

    # ================================================================
    # 内部: 文件保存
    # ================================================================

    def _save_markdown(self, article: Dict, content: str,
                       filepath: Path) -> None:
        """保存一篇新闻为 Markdown 文件"""
        title = article.get("title", "").strip()
        lines = [f"# {title}", "", content]
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    # ================================================================
    # 内部: 数据库索引 CSV
    # ================================================================

    def _generate_db_csv(self) -> None:
        """
        生成数据库批量导入 CSV 索引

        字段对齐 ./数据库相关指南/批量上传使用指南.md 中的 Excel 模板
        """
        articles = self._load_articles()
        csv_path = self.articles_dir / "db_import.csv"
        rows = []

        for article in articles:
            article_id = article.get("id", "")
            md_file = f"{article_id}.md"
            md_path = self.articles_dir / md_file

            # 只包含下载成功的文章
            if not md_path.exists() or md_path.stat().st_size < 100:
                continue

            title = (article.get("title") or "").strip()
            pub_date = self._resolve_date(article)
            is_official = article.get("is_official", "")

            # 关键词
            kw_text = f"{title} {article.get('summary', '')}"
            keywords = [kw for kw in KEYWORDS if kw in kw_text]

            # 话语类型
            discourse_type = ("institutional" if is_official == "True"
                              else "civilian")

            rows.append({
                "*文件名": md_file,
                "*标题": title,
                "描述 / 内容": (article.get("summary") or "")[:200],
                "*分类": "news",
                "*类型": "text",
                "*国家": "china",
                "地区": "asia",
                "关键词": ", ".join(keywords),
                "发展阶段": self._infer_development_stage(pub_date),
                "*话语类型": discourse_type,
                "发布日期": pub_date,
                "来源": article.get("source", ""),
                "原始URL": article.get("url", ""),
            })

        if not rows:
            logger.warning("没有可导出的文章，跳过 CSV 生成")
            return

        # 写入 CSV (UTF-8 BOM 兼容 Excel)
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)

        logger.info(f"📊 数据库索引已生成: {csv_path} ({len(rows)} 条)")

    # ================================================================
    # 辅助方法
    # ================================================================

    @staticmethod
    def _resolve_date(article: Dict) -> str:
        """解析发布日期"""
        pub_date = (article.get("pub_date") or "").strip()
        if pub_date and re.match(r"^\d{4}-\d{2}-\d{2}$", pub_date):
            return pub_date
        # 尝试从相对日期解析（简化版：取当前日期减去偏移量）
        date_str = (article.get("date") or "").strip()
        m = re.match(r"(\d+)\s*(天|小时|分钟|个月|年|周|秒|d|h|m|y|mo|mon|w)",
                     date_str, re.IGNORECASE)
        if m:
            num = int(m.group(1))
            unit = m.group(2).lower()
            now = datetime.now()
            from datetime import timedelta
            delta_map = {
                "天": timedelta(days=num), "d": timedelta(days=num),
                "小时": timedelta(hours=num), "h": timedelta(hours=num),
                "周": timedelta(weeks=num), "w": timedelta(weeks=num),
                "个月": timedelta(days=int(num * 30)),
                "mo": timedelta(days=int(num * 30)),
                "mon": timedelta(days=int(num * 30)),
                "年": timedelta(days=int(num * 365)),
                "y": timedelta(days=int(num * 365)),
            }
            delta = delta_map.get(unit, timedelta(days=num))
            return (now - delta).strftime("%Y-%m-%d")
        return ""

    @staticmethod
    def _infer_development_stage(pub_date: str) -> str:
        """根据发布日期推断中国扶贫发展阶段"""
        if not pub_date:
            return "rural"
        try:
            year = int(pub_date[:4])
        except (ValueError, IndexError):
            return "rural"

        if year < 1979:
            return "traditional"
        elif year < 1986:
            return "reform"
        elif year < 1994:
            return "development"
        elif year < 2013:
            return "poverty"
        elif year < 2021:
            return "precision"
        else:
            return "rural"

    def _log_progress(self, current: int, total: int,
                      start_time: datetime) -> None:
        """输出进度日志"""
        elapsed = (datetime.now() - start_time).total_seconds()
        logger.info(
            f"  进度: {current}/{total} | "
            f"✓ {self.success_count} | ✗ {self.fail_count} | "
            f"⊘ {self.skip_count} | {elapsed:.0f}s"
        )

    def close(self):
        """关闭会话"""
        if self._session:
            self._session.close()
            self._session = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

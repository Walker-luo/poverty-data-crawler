# ============================================
# 基础爬虫类 — 反爬检测 + 退避 + UA轮换
# ============================================
import time
import random
import logging
import requests
from typing import Optional, Dict, Any
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config.settings import CRAWL_CONFIG, HEADERS

logger = logging.getLogger(__name__)

# 轮流使用的 User-Agent 池
UA_POOL = [
    # Chrome Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    # Chrome Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    # Edge
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
    # Firefox
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
]


class BaseSpider:
    """基础爬虫 — 请求管理 / 反爬检测 / 退避"""

    def __init__(self, name: str = "base", delay: float = None, proxy: Optional[str] = None):
        self.name = name
        self.delay = delay if delay is not None else CRAWL_CONFIG["request_delay"]
        self.proxy = proxy
        self.session = self._build_session()
        self.request_count = 0
        self.block_count = 0  # 被拦截次数
        self._ua_idx = 0

    def _build_session(self) -> requests.Session:
        session = requests.Session()
        session.headers.update(HEADERS)
        session.headers["User-Agent"] = UA_POOL[0]

        if self.proxy:
            session.proxies = {
                "http": self.proxy,
                "https": self.proxy,
            }
            logger.info(f"[{self.name}] 使用代理: {self.proxy}")

        retry_strategy = Retry(
            total=CRAWL_CONFIG["max_retries"],
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "HEAD"],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    def _rotate_ua(self):
        """轮换 User-Agent"""
        self._ua_idx = (self._ua_idx + 1) % len(UA_POOL)
        self.session.headers["User-Agent"] = UA_POOL[self._ua_idx]

    def _delay(self, extra: float = 0):
        """请求间隔（随机延时）"""
        jitter = random.uniform(0.5, 2.0)
        time.sleep(self.delay * jitter + extra)

    def _is_blocked(self, html: str) -> bool:
        """检测百度反爬/验证码页面"""
        if not html:
            return False
        if len(html) < 5000 and ("安全验证" in html or "百度安全" in html):
            return True
        if "timeout hide-callback" in html and len(html) < 3000:
            return True
        return False

    def fetch(
        self,
        url: str,
        params: Optional[Dict] = None,
        encoding: Optional[str] = None,
        **kwargs,
    ) -> Optional[str]:
        """
        发起 GET 请求。遇到反爬自动退避重试。
        """
        max_retries = CRAWL_CONFIG["max_retries"]

        for attempt in range(max_retries + 1):
            self._delay()
            self.request_count += 1
            self._rotate_ua()  # 每次请求轮换 UA

            try:
                # 提取搜索词用于日志 (比打 URL 更有意义)
                query_hint = ""
                if params and "q" in params:
                    query_hint = params["q"][:40]
                elif params and "word" in params:
                    query_hint = params["word"][:40]
                log_msg = f"[{self.name}] #{self.request_count}"
                if query_hint:
                    log_msg += f" 搜索: {query_hint}"
                if attempt:
                    log_msg += f" (重试{attempt})"
                logger.debug(log_msg)

                resp = self.session.get(
                    url, params=params,
                    timeout=CRAWL_CONFIG["timeout"], **kwargs,
                )
                resp.raise_for_status()

                if encoding:
                    resp.encoding = encoding
                elif resp.apparent_encoding:
                    resp.encoding = resp.apparent_encoding

                # --- 反爬检测 ---
                if self._is_blocked(resp.text):
                    self.block_count += 1
                    backoff = 30 + random.randint(10, 30)  # 被封后等 30-60 秒
                    logger.warning(
                        f"[{self.name}] ⚠️ 触发百度反爬 (第{self.block_count}次), "
                        f"等待 {backoff}s ..."
                    )
                    # 换 Session 重置 cookie
                    self.session.close()
                    self.session = self._build_session()
                    time.sleep(backoff)
                    continue  # 重试

                return resp.text

            except requests.RequestException as e:
                logger.error(f"[{self.name}] 请求失败: {e}")
                if attempt < max_retries:
                    time.sleep(10 * (attempt + 1))
                    continue
                return None

        logger.error(f"[{self.name}] 已达最大重试次数，放弃")
        return None

    def fetch_json(
        self,
        url: str,
        params: Optional[Dict] = None,
        **kwargs,
    ) -> Optional[Dict[str, Any]]:
        self._delay()
        self.request_count += 1
        try:
            logger.info(f"[{self.name}] API: {url[:120]}")
            resp = self.session.get(
                url, params=params,
                timeout=CRAWL_CONFIG["timeout"], **kwargs,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"[{self.name}] API失败: {e}")
            return None

    def close(self):
        self.session.close()
        logger.info(
            f"[{self.name}] 已关闭 | 请求:{self.request_count} "
            f"拦截:{self.block_count}"
        )

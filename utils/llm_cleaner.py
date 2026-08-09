# ============================================
# LLM 清洗模块 — DeepSeek API 批量清洗新闻正文
# ============================================
"""
通过 DeepSeek V4 Flash 将杂乱的 .md 文章清洗为统一模板格式。

用法:
    设置环境变量:
        export DEEPSEEK_API_KEY="sk-xxx"

    命令行:
        python main.py --clean                    # 清洗最新 run
        python main.py --clean --run-id ID        # 指定 run
        python main.py --clean --limit 10         # 限制数量测试

    程序调用:
        from utils.eaner import LLMCleaner
        with LLMCleaner(run_id="20260805_122229") as cleaner:
            cleaner.clean_all()

输出:
    data/processed/news/{run_id}/articles/clean/
    ├── a1b2c3d4.md    # 清洗后的文章
    ├── e5f6g7h8.md
    └── ...
"""

import os
import re
import json
import logging
import time
import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from openai import OpenAI
from config.settings import KEYWORDS

from dotenv import load_dotenv
load_dotenv()  # 这行会读取 .env 文件并注入到环境变量中


logger = logging.getLogger(__name__)


# ============================================================
# 🔑 在此填入 DeepSeek API Key（优先于环境变量）
# ============================================================
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")  # 从环境变量读取

# DeepSeek API 配置
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-v4-flash"  # DeepSeek 

# 系统提示词 — 定义清洗规则和输出格式
SYSTEM_PROMPT = """你是一个专业的新闻文本清洗助手。你的任务是将爬虫下载的新闻文章整理为统一格式。

## 清洗规则
1. 删除所有非文章正文的内容：广告、导航栏、页脚、侧边栏、相关推荐、评论区、分享按钮、二维码、版权声明
2. 删除图片说明文字（如"新华社记者 XXX 摄"、"图为XXX"）
3. 删除记者署名行和来源重复声明
4. 删除"查看余下全文"、"责任编辑"、"敬请关注"等网站功能文字
5. 保留文章的正副标题、导语和全部正文段落
6. 保持原文段落结构，不要合并段落，不要改写或缩写正文
7. 保留原文中的直接引语和关键数据

## 输出格式
严格按以下 Markdown 格式输出，不要添加任何其他内容：

---
id: "{这里留空，我会填入}"
title: "{清理后的标题}"
source: "{来源名称}"
source_url: "{原始URL}"
publish_date: "{YYYY-MM-DD}"
category: "news"
type: "text"
country: "china"
discourse_type: "{institutional 或 civilian}"
keywords: ["关键词1", "关键词2"]
development_stage: "{traditional/reform/development/poverty/precision/rural}"
summary: "{根据正文生成的摘要，不超过100字}"
---

{正文第一段}

{正文第二段}

## 关键词列表
只能从以下列表中选取文章中实际讨论的关键词：
{keywords}

## 发展阶段判断
- traditional: 1949-1978年
- reform: 1979-1985年
- development: 1986-1993年
- poverty: 1994-2012年
- precision: 2013-2020年
- rural: 2021年至今
根据文章发布日期判断。

## 话语类型判断
- institutional: 来源是官方媒体（人民网、新华网、央视网、央广网、光明网、中国日报、求是等）
- civilian: 来源是商业媒体或其他
"""

# 批量清洗：每篇文章之间的分隔符
BATCH_SEPARATOR = "\n\n---ARTICLE---\n\n"

# 批量清洗系统提示词（与单篇共享清洗规则，增加输出格式说明）
BATCH_SYSTEM_PROMPT = """你是一个专业的新闻文本清洗助手。你的任务是将爬虫下载的新闻文章整理为统一格式。

## 清洗规则
1. 删除所有非文章正文的内容：广告、导航栏、页脚、侧边栏、相关推荐、评论区、分享按钮、二维码、版权声明
2. 删除图片说明文字（如"新华社记者 XXX 摄"、"图为XXX"）
3. 删除记者署名行和来源重复声明
4. 删除"查看余下全文"、"责任编辑"、"敬请关注"等网站功能文字
5. 保留文章的正副标题、导语和全部正文段落
6. 保持原文段落结构，不要合并段落，不要改写或缩写正文
7. 保留原文中的直接引语和关键数据

## 输出格式
你会收到多篇新闻文章。每篇文章独立清洗，严格按以下格式输出。
**每篇文章之间必须用分隔符 `---ARTICLE---` 隔开**（独占一行）。

每篇文章的格式：

---
id: ""
title: "{清理后的标题}"
source: "{来源名称}"
source_url: "{原始URL}"
publish_date: "{YYYY-MM-DD}"
category: "news"
type: "text"
country: "china"
discourse_type: "{institutional 或 civilian}"
keywords: ["关键词1", "关键词2"]
development_stage: "{traditional/reform/development/poverty/precision/rural}"
summary: "{根据正文生成的摘要，不超过100字}"
---

{正文第一段}

{正文第二段}

## 关键词列表
只能从以下列表中选取文章中实际讨论的关键词：
{keywords}

## 发展阶段判断
- traditional: 1949-1978年
- reform: 1979-1985年
- development: 1986-1993年
- poverty: 1994-2012年
- precision: 2013-2020年
- rural: 2021年至今
根据文章发布日期判断。

## 话语类型判断
- institutional: 来源是官方媒体（人民网、新华网、央视网、央广网、光明网、中国日报、求是等）
- civilian: 来源是商业媒体或其他
"""

# 每批最多处理文章数
DEFAULT_BATCH_SIZE = 10


class LLMCleaner:
    """DeepSeek API 批量文章清洗器"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        run_id: Optional[str] = None,
        delay: float = 0.5,       # API 调用间隔
        max_retries: int = 3,
        batch_size: int = DEFAULT_BATCH_SIZE,  # 批量清洗篇数
    ):
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "") or DEEPSEEK_API_KEY
        if not self.api_key:
            raise ValueError(
                "请设置 DEEPSEEK_API_KEY 环境变量，或传入 api_key 参数"
            )

        self.model = model or DEEPSEEK_MODEL
        self.delay = delay
        self.max_retries = max_retries
        self.batch_size = max(1, batch_size)

        self.client = OpenAI(
            api_key=self.api_key,
            base_url=DEEPSEEK_BASE_URL,
        )

        # 定位数据目录
        if run_id:
            self.run_id = run_id
        else:
            self.run_id = self._find_latest_run()

        self.data_dir = Path(f"data/processed/news/{self.run_id}")
        self.articles_dir = self.data_dir / "articles"
        self.clean_dir = self.articles_dir / "clean"
        self.csv_path = self.data_dir / "news.csv"

        if not self.csv_path.exists():
            raise FileNotFoundError(f"未找到 CSV 数据: {self.csv_path}")

        self.success_count = 0
        self.fail_count = 0
        self.skip_count = 0
        self._fail_log_path = self.data_dir / "fail.log"

        logger.info(f"LLM 清洗器就绪: model={self.model}, run_id={self.run_id}")

    # ================================================================
    # 公开接口
    # ================================================================

    def clean_all(self, limit: Optional[int] = None) -> Tuple[int, int, int]:
        """
        批量清洗所有已下载的 .md 文章

        每 batch_size 篇文章合并为一次 API 请求，节省 system prompt 重复传输的 token。
        单篇模式下 system prompt ~1500 token 每次都要传；批量模式下 N 篇共享一次。

        Args:
            limit: 限制清洗数量（测试用）

        Returns:
            (success, fail, skip) 计数
        """
        import csv

        # 加载原始文章数据
        with open(self.csv_path, "r", encoding="utf-8-sig") as f:
            articles = list(csv.DictReader(f))

        # 筛选：有下载 .md 且未清洗的文章
        to_process = []
        for a in articles:
            md_path = self.articles_dir / f"{a['id']}.md"
            if not md_path.exists() or md_path.stat().st_size < 100:
                continue
            clean_path = self.clean_dir / f"{a['id']}.md"
            if clean_path.exists() and clean_path.stat().st_size > 200:
                self.skip_count += 1  # 断点续传：已清洗跳过
                continue
            to_process.append(a)

        if limit:
            to_process = to_process[:limit]

        total = len(to_process)
        if total == 0:
            logger.warning("没有可清洗的文章（请先运行 --download）")
            return 0, 0, 0

        self.clean_dir.mkdir(parents=True, exist_ok=True)

        # 预处理 system prompt（注入关键词，只需做一次）
        keywords_str = "、".join(KEYWORDS)
        system_prompt = BATCH_SYSTEM_PROMPT.replace("{keywords}", keywords_str)

        batch_count = (total + self.batch_size - 1) // self.batch_size
        logger.info(
            f"🤖 开始 LLM 清洗: {total} 篇 → {batch_count} 批 "
            f"(模型: {self.model}, 每批 {self.batch_size} 篇)"
        )
        logger.info(f"   Token 节省估算: {total}次请求 → {batch_count}次 (省 ~{(total - batch_count) * 1500} token)")

        # 失败日志运行头
        with open(self._fail_log_path, "a", encoding="utf-8") as f:
            f.write(f"\n{'='*60}\n"
                    f"🤖 Clean Run: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | "
                    f"total={total} | model={self.model} | batch_size={self.batch_size}\n"
                    f"{'='*60}\n")

        done = 0
        for batch_idx in range(batch_count):
            start = batch_idx * self.batch_size
            end = min(start + self.batch_size, total)
            batch = to_process[start:end]

            logger.debug(f"  批次 {batch_idx+1}/{batch_count}: {len(batch)} 篇")

            # 批量调用 LLM
            results = self._clean_batch(batch, system_prompt)

            # 保存结果
            for article, (cleaned, error) in zip(batch, results):
                article_id = article["id"]
                clean_path = self.clean_dir / f"{article_id}.md"
                done += 1

                if cleaned:
                    clean_path.write_text(cleaned, encoding="utf-8")
                    self.success_count += 1
                else:
                    self.fail_count += 1
                    self._log_fail("clean", article_id,
                                   article.get("title", ""),
                                   article.get("url", ""), error)

            if (batch_idx + 1) % 5 == 0 or (batch_idx + 1) == batch_count:
                logger.info(
                    f"  进度: {done}/{total} (批次 {batch_idx+1}/{batch_count}) | "
                    f"✓{self.success_count} ✗{self.fail_count} ⊘{self.skip_count}"
                )

        logger.info(
            f"✅ LLM 清洗完成: {total} 篇 | "
            f"✓ {self.success_count} | ✗ {self.fail_count} | ⊘ {self.skip_count}"
        )
        logger.info(f"清洗后文件目录: {self.clean_dir}")

        if self.fail_count > 0:
            logger.info(f"📄 失败记录: {self._fail_log_path}")

        # 生成数据库导入 CSV（使用 AI 清洗后的 summary）
        self._generate_db_csv()

        return self.success_count, self.fail_count, self.skip_count

    # ================================================================
    # 内部: 失败日志
    # ================================================================

    def _log_fail(self, step: str, article_id: str, title: str, url: str, reason: str) -> None:
        """记录失败条目到 fail.log（追加模式）"""
        line = (
            f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
            f"step={step} | id={article_id} | "
            f"title={title[:60]} | url={url[:80]} | "
            f"reason={reason}\n"
        )
        with open(self._fail_log_path, "a", encoding="utf-8") as f:
            f.write(line)

    # ================================================================
    # 内部: 批量清洗
    # ================================================================

    def _clean_batch(self, articles: List[Dict], system_prompt: str) -> List[Tuple[Optional[str], str]]:
        """一次 API 调用清洗多篇文章

        Args:
            articles: 待清洗文章列表
            system_prompt: 已注入关键词的系统提示词

        Returns:
            [(cleaned_text, error_reason), ...] 与 articles 一一对应
        """
        if len(articles) == 1:
            # 单篇走原逻辑（输出格式无分隔符）
            raw_path = self.articles_dir / f"{articles[0]['id']}.md"
            raw_text = raw_path.read_text(encoding="utf-8")
            result, err = self._clean_one(articles[0], raw_text)
            return [(result, err)]

        # 构建批量用户消息
        parts = []
        for i, article in enumerate(articles, 1):
            raw_path = self.articles_dir / f"{article['id']}.md"
            raw_text = raw_path.read_text(encoding="utf-8")
            # 批量模式下每篇截断以控制总 token（1M上下文绰绰有余）
            max_chars = 8000
            if len(raw_text) > max_chars:
                raw_text = raw_text[:max_chars] + "\n\n[文本过长，已截断]"

            parts.append(f"## 文章 {i}")
            parts.append(self._build_article_meta(article))
            parts.append(f"\n### 原始内容\n\n{raw_text}")

        user_message = (
            f"请一次性清洗以下 {len(articles)} 篇新闻文章。\n"
            f"每篇文章独立清洗，用分隔符 `---ARTICLE---`（独占一行）隔开。\n\n"
            + "\n\n".join(parts)
        )

        # API 调用（含重试）
        result_text = None
        for attempt in range(self.max_retries):
            try:
                time.sleep(self.delay)
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message},
                    ],
                    temperature=0.1,
                    max_tokens=16384,
                )
                result_text = response.choices[0].message.content
                if result_text and len(result_text) > 200:
                    break
                result_text = None
                logger.warning(
                    f"批量 API 返回过短 ({len(result_text) if result_text else 0} 字符)"
                )
            except Exception as e:
                if attempt < self.max_retries - 1:
                    wait = (attempt + 1) * 5
                    logger.debug(f"批量 API 错误 (重试 {attempt+1}): {e}")
                    time.sleep(wait)
                else:
                    logger.error(f"批量 API 失败: {type(e).__name__}")
                    return [(None, f"批量API失败: {type(e).__name__}")] * len(articles)

        if not result_text:
            return [(None, "批量API返回为空")] * len(articles)

        return self._parse_batch_result(result_text, articles)

    def _build_article_meta(self, article: Dict) -> str:
        """构建单篇文章的元数据头"""
        title = article.get("title", "").strip()
        source = article.get("source", "").strip()
        url = article.get("url", "").strip()
        pub_date = article.get("pub_date", "").strip()
        is_official = article.get("is_official", "")
        discourse = "institutional" if is_official == "True" else "civilian"
        stage_hint = self._stage_hint(pub_date)

        return (
            f"\n### 元数据\n"
            f"- 标题: {title}\n"
            f"- 来源: {source}\n"
            f"- URL: {url}\n"
            f"- 发布日期: {pub_date}\n"
            f"- 话语类型: {discourse}\n"
            f"{stage_hint}"
        )

    def _parse_batch_result(self, text: str, articles: List[Dict]) -> List[Tuple[Optional[str], str]]:
        """拆分批量 API 返回结果为单篇文章

        LLM 用 `---ARTICLE---` 分隔各篇文章的输出。
        """
        # 按分隔符拆分
        parts = re.split(r'\n?---ARTICLE---\n?', text)
        # 去除空白段（第一个段可能是空的）
        parts = [p.strip() for p in parts if p.strip()]

        results = []
        for i in range(len(articles)):
            if i < len(parts) and parts[i] and len(parts[i]) > 200:
                results.append((parts[i], ""))
            elif i < len(parts):
                results.append((None,
                    f"批量结果第{i+1}篇过短({len(parts[i])}字符)"))
            else:
                results.append((None,
                    f"批量结果缺少第{i+1}篇(共解析{len(parts)}篇)"))

        return results

    def _clean_one(self, article: Dict, raw_text: str) -> Tuple[Optional[str], str]:
        """调用 LLM 清洗单篇文章

        Returns:
            (cleaned_text, error_reason) — 成功时 error_reason 为空字符串
        """
        title = article.get("title", "").strip()
        source = article.get("source", "").strip()
        url = article.get("url", "").strip()
        pub_date = article.get("pub_date", "").strip()
        is_official = article.get("is_official", "")

        # 构建用户消息
        user_message = self._build_user_message(
            raw_text=raw_text,
            title=title,
            source=source,
            url=url,
            pub_date=pub_date,
            is_official=is_official,
        )

        # 构建系统提示词（注入关键词列表）
        keywords_str = "、".join(KEYWORDS)
        system_prompt = SYSTEM_PROMPT.replace("{keywords}", keywords_str)

        for attempt in range(self.max_retries):
            try:
                time.sleep(self.delay)
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message},
                    ],
                    temperature=0.1,        # 低温度保证一致性
                    max_tokens=8192,
                )

                result = response.choices[0].message.content
                if result and len(result) > 100:
                    return result, ""
                else:
                    err = f"LLM返回过短({len(result) if result else 0}字符)"
                    logger.warning(f"{err} [{title[:30]}]")
                    return None, err

            except Exception as e:
                if attempt < self.max_retries - 1:
                    wait = (attempt + 1) * 5
                    logger.debug(
                        f"API 错误 (重试 {attempt+1}/{self.max_retries}, "
                        f"等待 {wait}s): {e}"
                    )
                    time.sleep(wait)
                else:
                    err = f"API失败({self.max_retries}次重试): {type(e).__name__}"
                    logger.error(f"{err} [{title[:30]}]")
                    return None, err

    def _build_user_message(
        self,
        raw_text: str,
        title: str,
        source: str,
        url: str,
        pub_date: str,
        is_official: str,
    ) -> str:
        """构建发给 LLM 的用户消息"""
        discourse = "institutional" if is_official == "True" else "civilian"

        # 发展阶段提示
        stage_hint = self._stage_hint(pub_date)

        # 截断过长文本（DeepSeek 上下文 128K，但控制成本）
        max_input = 12000
        if len(raw_text) > max_input:
            raw_text = raw_text[:max_input] + "\n\n[文本过长，已截断]"

        return f"""请清洗以下新闻文章。

## 已知元数据
- 标题: {title}
- 来源: {source}
- URL: {url}
- 发布日期: {pub_date}
- 话语类型: {discourse}
{stage_hint}

## 原始文章内容

{raw_text}"""

    @staticmethod
    def _stage_hint(pub_date: str) -> str:
        """根据日期给出发展阶段提示"""
        if not pub_date:
            return "- 发展阶段: rural (默认)"
        try:
            year = int(pub_date[:4])
        except (ValueError, IndexError):
            return "- 发展阶段: rural (默认)"

        if year < 1979:
            stage = "traditional"
            desc = "传统救济阶段 (1949-1978)"
        elif year < 1986:
            stage = "reform"
            desc = "体制改革阶段 (1979-1985)"
        elif year < 1994:
            stage = "development"
            desc = "开发式扶贫阶段 (1986-1993)"
        elif year < 2013:
            stage = "poverty"
            desc = "八七扶贫攻坚阶段 (1994-2012)"
        elif year < 2021:
            stage = "precision"
            desc = "精准扶贫阶段 (2013-2020)"
        else:
            stage = "rural"
            desc = "乡村振兴阶段 (2021至今)"
        return f"- 发展阶段: {stage} ({desc})"

    # ================================================================
    # 数据库索引 CSV
    # ================================================================

    def _generate_db_csv(self) -> None:
        """清洗完成后，从 YAML frontmatter 生成数据库导入 CSV"""
        import csv

        csv_path = self.clean_dir / "db_import.csv"
        rows = []

        for md_file in sorted(self.clean_dir.glob("*.md")):
            if md_file.name == "db_import.csv":
                continue

            text = md_file.read_text(encoding="utf-8")
            meta = self._parse_frontmatter(text)
            if not meta:
                continue

            # 发展阶段中文映射
            stage_map = {
                "traditional": "传统救济 (1949-1978)",
                "reform": "体制改革 (1979-1985)",
                "development": "开发式扶贫 (1986-1993)",
                "poverty": "八七攻坚 (1994-2012)",
                "precision": "精准扶贫 (2013-2020)",
                "rural": "乡村振兴 (2021至今)",
            }
            stage = meta.get("development_stage", "rural")
            stage_label = stage_map.get(stage, stage)

            # keywords 可能是 YAML list 或逗号分隔字符串
            keywords = meta.get("keywords", [])
            if isinstance(keywords, list):
                keywords_str = ", ".join(keywords)
            else:
                keywords_str = str(keywords)

            rows.append({
                "*文件名": md_file.name,
                "*标题": meta.get("title", ""),
                "描述 / 内容": meta.get("summary", ""),
                "*分类": meta.get("category", "news"),
                "*类型": meta.get("type", "text"),
                "*国家": meta.get("country", "china"),
                "地区": "asia",
                "关键词": keywords_str,
                "发展阶段": stage_label,
                "*话语类型": meta.get("discourse_type", "civilian"),
                "发布日期": meta.get("publish_date", ""),
                "来源": meta.get("source", ""),
                "原始URL": meta.get("source_url", ""),
            })

        if not rows:
            logger.warning("没有可导出的文章，跳过 CSV 生成")
            return

        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)

        logger.info(f"📊 数据库索引已生成: {csv_path} ({len(rows)} 条)")

    @staticmethod
    def _parse_frontmatter(text: str) -> Dict:
        """解析 Markdown 的 YAML frontmatter (---...---)"""
        if not text.startswith("---"):
            return {}
        parts = text.split("---", 2)
        if len(parts) < 3:
            return {}
        frontmatter = parts[1].strip()
        meta = {}
        # 简单 KV 解析，支持 YAML 列表值
        current_key = None
        for line in frontmatter.split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # 列表续行: "  - value"
            if line.startswith("- ") and current_key:
                if isinstance(meta.get(current_key), list):
                    meta[current_key].append(line[2:].strip().strip('"'))
                continue
            # 普通 KV: "key: value" 或 "key: ["a","b"]"
            m = re.match(r'^(\w+):\s*(.*)', line)
            if m:
                key = m.group(1)
                val = m.group(2).strip()
                current_key = key
                # 列表: ["a", "b"]
                if val.startswith("[") and val.endswith("]"):
                    val = [v.strip().strip('"\'') for v in val[1:-1].split(",") if v.strip()]
                else:
                    val = val.strip('"\'')
                meta[key] = val
        return meta

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

    def close(self):
        pass  # OpenAI client 无需显式关闭

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

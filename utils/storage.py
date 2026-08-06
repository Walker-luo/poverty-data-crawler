# ============================================
# 数据存储工具 — 每次运行按时间戳子目录保存
# ============================================
import os
import csv
import json
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

from config.settings import STORAGE_CONFIG

logger = logging.getLogger(__name__)


class DataStorage:
    """
    数据持久化存储

    每次运行使用统一的 run_id (时间戳)，
    所有文件保存在同一个子目录下:

        data/processed/news/20260731_150000/
        ├── news.csv
        ├── news.json
        └── summary.md
    """

    def __init__(self):
        self.raw_base = STORAGE_CONFIG["raw_data_dir"]
        self.processed_base = STORAGE_CONFIG["processed_data_dir"]
        self.run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._ensure_base_dirs()

    def _ensure_base_dirs(self):
        for d in [self.raw_base, self.processed_base]:
            os.makedirs(d, exist_ok=True)

    def _run_dir(self, crawl_type: str, to_processed: bool = False) -> str:
        """获取本次运行的子目录: data/processed/news/20260731_150000/"""
        base = self.processed_base if to_processed else self.raw_base
        target = os.path.join(base, crawl_type, self.run_id)
        os.makedirs(target, exist_ok=True)
        return target

    # ================================================================
    # 文件存储
    # ================================================================

    def save_csv(
        self,
        data: List[Dict[str, Any]],
        crawl_type: str = "general",
        filename: str = "data.csv",
        to_processed: bool = False,
    ) -> str:
        """保存 CSV 到 run 子目录"""
        if not data:
            logger.warning("数据为空，跳过 CSV 保存")
            return ""

        target_dir = self._run_dir(crawl_type, to_processed)
        filepath = os.path.join(target_dir, filename)

        fieldnames = list(data[0].keys())
        with open(filepath, "w", encoding=STORAGE_CONFIG["csv_encoding"], newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(data)

        logger.info(f"CSV 已保存: {filepath} ({len(data)} 条)")
        return filepath

    def save_json(
        self,
        data: Any,
        crawl_type: str = "general",
        filename: str = "data.json",
        to_processed: bool = False,
        indent: int = 2,
    ) -> str:
        """保存 JSON 到 run 子目录"""
        if not data and not isinstance(data, list):
            return ""

        target_dir = self._run_dir(crawl_type, to_processed)
        filepath = os.path.join(target_dir, filename)

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=indent)

        count = len(data) if isinstance(data, list) else 1
        logger.info(f"JSON 已保存: {filepath} ({count} 条)")
        return filepath

    def load_json(self, filepath: str) -> Optional[Any]:
        """加载 JSON 文件"""
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.error(f"JSON 加载失败: {filepath} -- {e}")
            return None

    # ================================================================
    # Resource Schema JSON 输出（供 MongoDB 导入）
    # ================================================================

    def save_resource_json(
        self,
        resources: List[Dict[str, Any]],
        crawl_type: str = "news",
        filename: str = "resource.json",
    ) -> str:
        """
        保存 MongoDB-ready Resource 文档 JSON

        输出格式与 anti-poverty-server 的 Resource Schema 对齐，
        可直接通过 mongoimport 或后端上传接口导入。

        Args:
            resources: ResourceMapper.map_batch() 的输出
            crawl_type: 爬取类型
            filename: 文件名

        Returns:
            文件路径
        """
        if not resources:
            logger.warning("Resource 数据为空，跳过保存")
            return ""

        target_dir = self._run_dir(crawl_type, to_processed=True)
        filepath = os.path.join(target_dir, filename)

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(resources, f, ensure_ascii=False, indent=2)

        logger.info(f"Resource JSON 已保存: {filepath} ({len(resources)} 条)")
        return filepath

    def generate_import_script(
        self,
        crawl_type: str = "news",
        resource_file: str = "resource.json",
    ) -> str:
        """
        生成 MongoDB 导入脚本 (mongosh)

        通过 originalUrl 做 upsert，重复运行不会产生重复数据。

        Args:
            crawl_type: 爬取类型
            resource_file: Resource JSON 文件名

        Returns:
            脚本文件路径
        """
        target_dir = self._run_dir(crawl_type, to_processed=True)
        filepath = os.path.join(target_dir, "import.mongosh.js")

        script = f"""// MongoDB Resource 导入脚本
// 用法: mongosh mongodb://localhost:27017/poverty-db --file import.mongosh.js
//
// Run ID: {self.run_id}
// 说明: 基于 originalUrl 做 upsert，已存在的文档不会重复插入

const fs = require('fs');
const path = require('path');

const data = JSON.parse(
    fs.readFileSync(path.join(__dirname, '{resource_file}'), 'utf-8')
);

print(`准备导入 ${{data.length}} 条资源文档...`);

let inserted = 0;
let skipped = 0;

data.forEach(doc => {{
    // 确保必填字段存在
    if (!doc.title || !doc.originalUrl) {{
        print(`跳过无效文档: ${{doc.title || '无标题'}}`);
        return;
    }}

    const result = db.resources.updateOne(
        {{ originalUrl: doc.originalUrl }},
        {{ $setOnInsert: doc }},
        {{ upsert: true }}
    );

    if (result.upsertedCount > 0) {{
        inserted++;
    }} else {{
        skipped++;
    }}
}});

print(`✅ 导入完成: ${{inserted}} 条新增, ${{skipped}} 条已存在 (跳过)`);
"""

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(script)

        logger.info(f"MongoDB 导入脚本已生成: {filepath}")
        return filepath

    # ================================================================
    # 统计报告 — 保存在 run 子目录
    # ================================================================

    def generate_summary(
        self,
        results: Dict[str, List[Dict]],
        run_info: Dict[str, Any],
    ) -> str:
        """
        生成爬取统计报告 (Markdown)，保存到 run 子目录

        Args:
            results: {"news": [...articles], "gov": [...]}
            run_info: {"start_time", "max_pages", "filter", "strategy", ...}
        """
        lines = []
        lines.append(f"# 扶贫数据爬取 — 统计报告")
        lines.append(f"")
        lines.append(f"**Run ID**: `{self.run_id}`")
        lines.append(f"**爬取时间**: {run_info.get('start_time', datetime.now())}")
        lines.append(f"**总耗时**: {run_info.get('duration', 'N/A')}")
        lines.append(f"**爬取参数**: 深度={run_info.get('max_pages')}, "
                      f"过滤={run_info.get('filter')}, "
                      f"策略={run_info.get('strategy')}")
        lines.append(f"")

        total_articles = 0

        for crawl_type in ["news", "gov"]:
            articles = results.get(crawl_type, [])
            if not articles:
                continue

            total_articles += len(articles)
            lines.append(f"---")
            lines.append(f"")
            lines.append(f"## {self._type_label(crawl_type)} ({len(articles)} 条)")
            lines.append(f"")

            # 来源分布
            source_stats = self._count_by(articles, "source")
            lines.append(f"### 📊 来源分布")
            lines.append(f"")
            lines.append(f"| 来源 | 数量 |")
            lines.append(f"|------|------|")
            for src, cnt in sorted(source_stats.items(), key=lambda x: -x[1]):
                lines.append(f"| {src} | {cnt} |")
            lines.append(f"")

            # 日期分布
            date_stats = self._count_by(articles, "date")
            if date_stats:
                lines.append(f"### 📅 日期分布")
                lines.append(f"")
                lines.append(f"| 日期 | 数量 |")
                lines.append(f"|------|------|")
                for d, cnt in sorted(date_stats.items())[:15]:
                    lines.append(f"| {d} | {cnt} |")
                lines.append(f"")

            # 关键词命中
            from config.settings import KEYWORDS
            kw_hits = {}
            for kw in KEYWORDS:
                count = sum(
                    1 for a in articles
                    if kw in (a.get("title", "") or "")
                )
                if count > 0:
                    kw_hits[kw] = count
            if kw_hits:
                lines.append(f"### 🔑 关键词命中 (标题)")
                lines.append(f"")
                lines.append(f"| 关键词 | 命中数 |")
                lines.append(f"|--------|--------|")
                for kw, cnt in sorted(kw_hits.items(), key=lambda x: -x[1]):
                    lines.append(f"| {kw} | {cnt} |")
                lines.append(f"")

            # 搜索模式分布
            mode_stats = self._count_by(articles, "search_mode")
            if mode_stats:
                lines.append(f"### 🔍 搜索模式")
                lines.append(f"")
                mode_label = {
                    "year_partition": "逐年分区",
                    "keyword": "关键词搜索",
                    "site": "站点定向",
                }
                for mode, cnt in sorted(mode_stats.items(), key=lambda x: -x[1]):
                    label = mode_label.get(mode, mode)
                    lines.append(f"- {label} (`{mode}`): {cnt} 条")
                lines.append(f"")

            # 文章预览
            lines.append(f"### 📰 文章预览 (前5条)")
            lines.append(f"")
            for i, a in enumerate(articles[:5], 1):
                title = a.get("title", "")[:60]
                src = a.get("source", "未知")
                date = a.get("date", "")
                lines.append(f"{i}. **{title}** — *{src}* ({date})")
            lines.append(f"")

        # 汇总
        lines.append(f"---")
        lines.append(f"")
        lines.append(f"## 📋 汇总")
        lines.append(f"")
        lines.append(f"| 指标 | 数值 |")
        lines.append(f"|------|------|")
        lines.append(f"| 总文章数 | {total_articles} |")
        lines.append(f"| 请求次数 | {run_info.get('total_requests', 'N/A')} |")
        lines.append(f"| 拦截次数 | {run_info.get('block_count', 0)} |")
        lines.append(f"| 官方媒体占比 | {self._official_ratio(results):.0f}% |")
        lines.append(f"")

        # 文件清单
        lines.append(f"### 📁 本次运行文件")
        lines.append(f"")
        for crawl_type in ["news", "gov"]:
            if results.get(crawl_type):
                d = f"`data/processed/{crawl_type}/{self.run_id}/`"
                lines.append(f"- {d}")
                lines.append(f"  - `data.csv` / `data.json` — 处理后数据")
                lines.append(f"  - `summary.md` — 本报告")
        lines.append(f"")

        # 写入 — 保存到每个有数据的类型的 run 目录
        report_path = ""
        for crawl_type in ["news", "gov"]:
            if results.get(crawl_type):
                target_dir = self._run_dir(crawl_type, to_processed=True)
                report_path = os.path.join(target_dir, "summary.md")
                with open(report_path, "w", encoding="utf-8") as f:
                    f.write("\n".join(lines))
                break  # 只写一份

        if report_path:
            logger.info(f"统计报告已生成: {report_path}")
        return report_path

    # ================================================================
    # 辅助
    # ================================================================

    @staticmethod
    def _type_label(crawl_type: str) -> str:
        return {"news": "📰 新闻数据", "gov": "🏛 政府数据"}.get(
            crawl_type, crawl_type
        )

    @staticmethod
    def _count_by(articles: List[Dict], key: str) -> Dict[str, int]:
        stats = {}
        for a in articles:
            val = a.get(key, "未知") or "未知"
            stats[val] = stats.get(val, 0) + 1
        return stats

    @staticmethod
    def _official_ratio(results: Dict[str, List[Dict]]) -> float:
        total = 0
        official = 0
        for articles in results.values():
            for a in articles:
                total += 1
                if a.get("is_official", False):
                    official += 1
        return official / total * 100 if total else 100.0

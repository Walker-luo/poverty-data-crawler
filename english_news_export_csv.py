#!/usr/bin/env python3
"""Export collected international news metadata to the database CSV template.

Reads news.csv one row at a time. Article Markdown is only checked for an
attachment path; its contents are never used as the description.
"""

import argparse
import csv
import logging
import os
import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Dict, Iterator, List, Optional

NEWS_DIR = Path("data/processed/news/en")
LEGACY_DIR = Path("data/processed/env_news")
CSV_FIELDS = [
    "*文件名", "*标题", "描述 / 内容", "*分类", "*类型", "*国家", "地区",
    "关键词", "发展阶段", "*话语类型", "发布日期", "来源", "原始URL",
    "文件地址",
]
STAGES = [
    (1978, "传统救济 (1949-1978)"),
    (1985, "体制改革 (1979-1985)"),
    (1993, "开发式扶贫 (1986-1993)"),
    (2012, "八七攻坚 (1994-2012)"),
    (2020, "精准扶贫 (2013-2020)"),
]
logger = logging.getLogger("english_news_export")


def _run_dirs(run_id: Optional[str], all_runs: bool) -> List[Path]:
    if all_runs:
        dirs = []
        for root in (NEWS_DIR, LEGACY_DIR):
            if root.exists():
                dirs.extend(d for d in root.iterdir()
                            if d.is_dir() and (d / "news.csv").is_file())
        dirs.sort(key=lambda d: d.name)
        if not dirs:
            raise SystemExit("未找到国际新闻 news.csv，请先运行 english_news_collector.py")
        return dirs

    if run_id:
        for root in (NEWS_DIR, LEGACY_DIR):
            path = root / run_id
            if (path / "news.csv").is_file():
                return [path]
        raise SystemExit(f"未找到 run {run_id} 的 news.csv")

    dirs = _run_dirs(None, True)
    return [max(dirs, key=lambda d: d.name)]


def _iter_news(path: Path) -> Iterator[Dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        yield from csv.DictReader(handle)


def _publish_date(row: Dict[str, str]) -> str:
    for value in (row.get("pub_date"), row.get("date")):
        value = (value or "").strip()
        if not value:
            continue
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            pass
        try:
            return parsedate_to_datetime(value).date().isoformat()
        except (TypeError, ValueError, IndexError):
            pass
    url = row.get("url") or ""
    match = re.search(r"/(20\d{2})[/-](\d{1,2})[/-](\d{1,2})(?:/|\b)", url)
    if not match:
        match = re.search(r"/(20\d{2})(\d{2})(\d{2})(?:/|\b)", url)
    if match:
        try:
            return datetime(*(int(part) for part in match.groups())).date().isoformat()
        except ValueError:
            pass
    return ""


def _is_china(row: Dict[str, str]) -> bool:
    scope = (row.get("scope") or "").strip().lower()
    focus = (row.get("country_focus") or "").strip().lower()
    if scope == "china" or focus == "china":
        return True
    if scope == "global" or focus:
        return False
    text = " ".join((row.get("title") or "", row.get("summary") or ""))
    return bool(re.search(r"\b(?:china|chinese|prc)\b|中国|我国|乡村振兴|扶贫|脱贫", text, re.I))


def _stage(pub_date: str) -> str:
    if not pub_date:
        return ""
    year = int(pub_date[:4])
    for last_year, label in STAGES:
        if year <= last_year:
            return label
    return "乡村振兴 (2021至今)"


def _build_row(row: Dict[str, str], run_dir: Path, output_dir: Path,
               desc_chars: int, attachments: bool,
               attachment_dir: Optional[Path] = None,
               attachment_ids: Optional[set[str]] = None) -> Dict[str, str]:
    identifier = (row.get("id") or "").strip()
    china = _is_china(row)
    focus = (row.get("country_focus") or "").strip()
    pub_date = _publish_date(row)
    article_path = (attachment_dir or (run_dir / "articles")) / f"{identifier}.md"
    file_address = "None"
    has_attachment = (
        identifier in attachment_ids
        if attachment_ids is not None
        else article_path.is_file() and article_path.stat().st_size > 0
    )
    if attachments and has_attachment:
        file_address = Path(os.path.relpath(article_path.resolve(), output_dir.resolve())).as_posix()
    return {
        "*文件名": identifier,
        "*标题": (row.get("title") or "").strip(),
        "描述 / 内容": re.sub(r"\s+", " ", row.get("summary") or "").strip()[:desc_chars],
        "*分类": "news",
        "*类型": "text",
        "*国家": "china" if china else "global",
        "地区": "asia" if china else focus,
        "关键词": (row.get("search_keyword") or "").strip(),
        "发展阶段": _stage(pub_date) if china else "",
        "*话语类型": "institutional" if (row.get("is_official") or "").strip().lower() == "true" else "civilian",
        "发布日期": pub_date,
        "来源": (row.get("source") or "").strip(),
        "原始URL": (row.get("url") or "").strip(),
        "文件地址": file_address,
    }


def export(run_id: Optional[str] = None, all_runs: bool = False,
           out_path: Optional[str] = None, desc_chars: int = 1000,
           attachments: bool = True, quality_dir: Optional[str] = None) -> int:
    if desc_chars < 1:
        raise ValueError("--desc-chars 必须大于 0")
    runs = _run_dirs(run_id, all_runs)
    default_path = (NEWS_DIR / "db_import.csv") if all_runs else (runs[0] / "db_import.csv")
    output = Path(out_path) if out_path else default_path
    output.parent.mkdir(parents=True, exist_ok=True)
    seen = set()
    total = with_file = with_summary = 0
    quality_dirs: Dict[Path, Optional[Path]] = {}
    quality_ids: Dict[Path, Optional[set[str]]] = {}
    if quality_dir and not attachments:
        logger.warning("--quality-dir 与 --no-attachments 同时使用，按 --no-attachments 处理")
    for run_dir in runs:
        if not quality_dir:
            quality_dirs[run_dir] = None
            quality_ids[run_dir] = None
            continue
        configured = Path(quality_dir).expanduser()
        candidates = [configured] if configured.is_absolute() else [
            run_dir / configured,
            run_dir / "articles" / configured,
        ]
        selected_dir = next((path for path in candidates if path.is_dir()), None)
        if selected_dir is None:
            raise SystemExit(
                f"未找到质量筛选目录: {quality_dir}（run {run_dir.name}，"
                f"期望位置为 {run_dir / 'articles' / quality_dir}）"
            )
        quality_dirs[run_dir] = selected_dir
        selected_ids = {
            item.stem for item in selected_dir.iterdir()
            if item.is_file() and item.suffix.lower() == ".md" and item.stat().st_size > 0
        }
        quality_ids[run_dir] = selected_ids
        logger.info("run %s 仅使用筛选正文: %s（%s 篇）", run_dir.name, selected_dir, len(selected_ids))
    with output.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for run_dir in runs:
            for news in _iter_news(run_dir / "news.csv"):
                identifier = (news.get("id") or "").strip()
                url = (news.get("url") or "").strip()
                key = url or identifier
                if not identifier or not url or key in seen:
                    continue
                seen.add(key)
                row = _build_row(
                    news, run_dir, output.parent, desc_chars, attachments,
                    quality_dirs[run_dir] if quality_dir else None,
                    quality_ids[run_dir] if quality_dir else None,
                )
                writer.writerow(row)
                total += 1
                with_file += row["文件地址"] != "None"
                with_summary += bool(row["描述 / 内容"])
                if total % 2000 == 0:
                    logger.info("已导出 %s 条", total)
    logger.info("数据库导入 CSV: %s (%s 条)", output, total)
    logger.info("有 Markdown: %s | 无附件: %s | 有摘要: %s", with_file, total - with_file, with_summary)
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description="国际新闻元数据导出数据库 CSV（不调用 LLM）")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--run-id", help="指定 news/en run（默认最新）")
    group.add_argument("--all", action="store_true", help="合并所有国际新闻 run，按 URL 去重")
    parser.add_argument("--out", help="指定输出 CSV 路径")
    parser.add_argument("--desc-chars", type=int, default=1000, help="摘要字符上限（默认 1000）")
    parser.add_argument("--no-attachments", action="store_true", help="文件地址统一填 None，仅导出元数据与原始 URL")
    parser.add_argument(
        "--quality-dir",
        help="仅将指定筛选目录中的 Markdown 写入文件地址，例如 quality_selected_70；目录外文件填 None",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    if args.desc_chars < 1:
        parser.error("--desc-chars 必须大于 0")
    export(
        args.run_id, args.all, args.out, args.desc_chars,
        not args.no_attachments, args.quality_dir,
    )


if __name__ == "__main__":
    main()

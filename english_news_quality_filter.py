#!/usr/bin/env python3
"""Rank downloaded international news Markdown files without using an LLM.

The script keeps the original ``articles/`` directory untouched. It writes a
score sheet and can copy the highest-scoring files to a separate directory.
"""

import argparse
import csv
import json
import logging
import re
import shutil
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

NEWS_DIR = Path("data/processed/news/en")
LEGACY_DIR = Path("data/processed/env_news")
logger = logging.getLogger("english_news_quality")

WORD_RE = re.compile(r"[A-Za-z\u00C0-\u024F\u4E00-\u9FFF]{2,}")
SENTENCE_RE = re.compile(r"[^.!?。！？]+[.!?。！？]?")
NOISE_PATTERNS = [
    r"see more headlines", r"more headlines", r"top stories", r"most read",
    r"related (?:stories|articles|news)", r"recommended (?:stories|articles)",
    r"sign up", r"subscribe to", r"newsletter", r"cookie", r"privacy policy",
    r"terms of use", r"advertisement", r"advertising", r"enable javascript",
    r"all rights reserved", r"follow us", r"share this", r"read more",
]
NOISE_RE = re.compile("|".join(NOISE_PATTERNS), re.I)
BOILERPLATE_LINE_RE = re.compile(
    r"^(?:by\s+[^.]{2,100}|see more|read more|subscribe|advertisement|"
    r"share|follow us|copyright|all rights reserved)\s*[^.]*$",
    re.I,
)


def _find_run(run_id: Optional[str]) -> Path:
    roots = [NEWS_DIR, LEGACY_DIR]
    if run_id:
        for root in roots:
            path = root / run_id
            if (path / "news.csv").is_file() or (path / "news.json").is_file():
                return path
        raise SystemExit(f"未找到国际新闻 run: {run_id}")

    candidates = []
    for root in roots:
        if root.exists():
            candidates.extend(
                d for d in root.iterdir()
                if d.is_dir() and ((d / "news.csv").is_file() or (d / "news.json").is_file())
            )
    if not candidates:
        raise SystemExit("未找到国际新闻数据，请先运行 english_news_collector.py")
    return max(candidates, key=lambda path: path.name)


def _iter_metadata(run_dir: Path) -> Iterator[Dict[str, str]]:
    csv_path = run_dir / "news.csv"
    if csv_path.is_file():
        with csv_path.open(newline="", encoding="utf-8-sig") as handle:
            yield from csv.DictReader(handle)
        return
    try:
        records = json.loads((run_dir / "news.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"无法读取新闻元数据: {run_dir}: {exc}")
    for record in records:
        if isinstance(record, dict):
            yield {str(key): "" if value is None else str(value) for key, value in record.items()}


def _normalise(text: str) -> str:
    text = text.replace("\ufeff", "").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _title_tokens(title: str) -> set[str]:
    return {word.lower() for word in WORD_RE.findall(title or "") if len(word) > 2}


def _score_article(title: str, text: str) -> Tuple[float, Dict[str, object]]:
    body = _normalise(text)
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    body_without_heading = "\n".join(lines[1:]) if lines and lines[0].startswith("#") else body
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body_without_heading) if p.strip()]
    chars = len(body_without_heading)
    words = WORD_RE.findall(body_without_heading)
    sentences = [s.strip() for s in SENTENCE_RE.findall(body_without_heading) if len(s.strip()) >= 25]
    noise_hits = len(NOISE_RE.findall(body_without_heading))
    boilerplate_lines = sum(bool(BOILERPLATE_LINE_RE.search(line)) for line in lines)
    short_lines = sum(1 for line in lines[1:] if 1 < len(line) < 45)
    title_tokens = _title_tokens(title)
    body_lower = body_without_heading.lower()
    title_overlap = (
        len({token for token in title_tokens if token in body_lower}) / len(title_tokens)
        if title_tokens else 0.0
    )
    # A real article normally has paragraphs, sentences and a reasonable body
    # length. These are soft signals because short news items can still be valid.
    length_score = min(chars / 1800.0, 1.0)
    paragraph_score = min(len(paragraphs) / 5.0, 1.0)
    sentence_score = min(len(sentences) / 8.0, 1.0)
    noise_ratio = min((noise_hits + boilerplate_lines) / max(len(lines), 1), 1.0)
    headline_ratio = min(short_lines / max(len(lines[1:]), 1), 1.0)
    score = 100.0 * (
        0.30 * length_score
        + 0.20 * paragraph_score
        + 0.15 * sentence_score
        + 0.20 * title_overlap
        - 0.10 * noise_ratio
        - 0.05 * headline_ratio
    )
    if chars < 120:
        score -= 25
    if len(words) < 40:
        score -= 15
    score = max(0.0, min(100.0, score))
    details = {
        "chars": chars,
        "words": len(words),
        "paragraphs": len(paragraphs),
        "sentences": len(sentences),
        "noise_hits": noise_hits + boilerplate_lines,
        "title_overlap": round(title_overlap, 3),
        "score": round(score, 2),
    }
    return score, details


def _quality_label(score: float) -> str:
    if score >= 65:
        return "high"
    if score >= 45:
        return "medium"
    return "low"


def filter_run(run_dir: Path, keep_ratio: float, output_dir: Optional[str],
               copy_selected: bool) -> Tuple[int, int, Path, Optional[Path]]:
    article_dir = run_dir / "articles"
    if not article_dir.is_dir():
        raise SystemExit(f"未找到正文目录: {article_dir}")
    scored: List[Dict[str, object]] = []
    metadata_count = 0
    for item in _iter_metadata(run_dir):
        metadata_count += 1
        article_id = (item.get("id") or "").strip()
        path = article_dir / f"{article_id}.md"
        if not article_id or not path.is_file() or path.stat().st_size == 0:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            logger.warning("跳过无法读取 %s: %s", path, exc)
            continue
        score, details = _score_article(item.get("title", ""), text)
        scored.append({
            "id": article_id,
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "source": item.get("source", ""),
            "path": path,
            "score": score,
            "label": _quality_label(score),
            **details,
        })

    scored.sort(key=lambda row: (-float(row["score"]), str(row["id"])))
    selected_count = round(len(scored) * keep_ratio)
    selected_count = min(len(scored), max(0, selected_count))
    selected_ids = {str(row["id"]) for row in scored[:selected_count]}
    for row in scored:
        row["selected"] = str(row["id"]) in selected_ids
        row["path"] = str(row["path"])

    report_path = Path(output_dir) if output_dir else article_dir / "quality_scores.csv"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["selected", "score", "label", "id", "title", "source", "url",
              "chars", "words", "paragraphs", "sentences", "noise_hits", "title_overlap", "path"]
    with report_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({key: row.get(key, "") for key in fields} for row in scored)

    selected_dir = None
    if copy_selected:
        percent = int(round(keep_ratio * 100))
        selected_dir = article_dir / f"quality_selected_{percent}"
        selected_dir.mkdir(parents=True, exist_ok=True)
        for row in scored[:selected_count]:
            shutil.copy2(str(row["path"]), selected_dir / Path(str(row["path"])).name)

    logger.info("元数据记录: %s | 有效 Markdown: %s", metadata_count, len(scored))
    logger.info("保留比例: %.1f%% | 选中: %s | 排除: %s", keep_ratio * 100, selected_count, len(scored) - selected_count)
    logger.info("评分明细: %s", report_path)
    if selected_dir:
        logger.info("高质量正文: %s", selected_dir)
    return metadata_count, selected_count, report_path, selected_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="国际新闻 Markdown 本地质量筛选（不调用 LLM）")
    parser.add_argument("--run-id", help="指定国际新闻 run，默认最新")
    parser.add_argument("--keep-ratio", type=float, default=0.7, help="保留有效 Markdown 的比例，默认 0.7")
    parser.add_argument("--scores-out", help="评分明细 CSV 路径，默认写入 articles/quality_scores.csv")
    parser.add_argument("--copy-selected", action="store_true", help="将选中的正文复制到 articles/quality_selected_{百分比}/")
    args = parser.parse_args()
    if not 0 < args.keep_ratio <= 1:
        parser.error("--keep-ratio 必须大于 0 且不超过 1")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    filter_run(_find_run(args.run_id), args.keep_ratio, args.scores_out, args.copy_selected)


if __name__ == "__main__":
    main()

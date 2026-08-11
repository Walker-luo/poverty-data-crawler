# ============================================
# 检查 db_import.csv 是否收录了 clean/ 下所有文章
# ============================================
"""
用法:
    python check_csv_coverage.py                     # 最新 run
    python check_csv_coverage.py --run-id ID         # 指定 run
    python check_csv_coverage.py --dry-run           # 只列出不重命名
    python check_csv_coverage.py --print-missing     # 只打印缺失，不操作
    python check_csv_coverage.py --restore           # 撤销 000_ 前缀（恢复原文件名）
    python check_csv_coverage.py --repair            # 自动修复缺失 --- 的 frontmatter
"""
import argparse
import csv
import re
import sys
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

CSV_FILENAME_COL = "*文件名"
PREFIX = "000_"

# 与 utils/llm_cleaner.py 的 frontmatter 字段保持一致
FRONTMATTER_FIELDS = [
    "id", "title", "source", "source_url", "publish_date",
    "category", "type", "country", "discourse_type",
    "keywords", "development_stage", "summary",
]
_FIELD_RE = re.compile(
    r"^(?:" + "|".join(FRONTMATTER_FIELDS) + r")\s*:"
)
_LIST_RE = re.compile(r"^\s*-\s")


def is_valid_frontmatter(text: str) -> bool:
    """判断 frontmatter 结构是否有效（与 llm_cleaner 的解析要求一致）"""
    return text.startswith("---") and len(text.split("---", 2)) >= 3


def repair_frontmatter(text: str) -> str:
    """修复缺失 --- 分隔符的 frontmatter，返回修复后的文本

    定位 YAML 字段块（id:/title:/.../summary: + 可能的列表续行），
    去掉字段前的 preamble 和正文混淆，重新包裹 ---。
    """
    if is_valid_frontmatter(text):
        return text

    lines = text.split("\n")

    # 1. 找到第一个 YAML 字段行（跳过 LLM 加的前言文字）
    start = None
    for i, line in enumerate(lines):
        if _FIELD_RE.match(line):
            start = i
            break
    if start is None:
        raise ValueError("找不到 YAML 字段行（id:/title:/...），无法自动修复")

    # 2. 收集字段块：字段行 + 列表续行，跳过字段间空行
    field_lines = []
    i = start
    while i < len(lines):
        line = lines[i]
        if _FIELD_RE.match(line) or _LIST_RE.match(line):
            field_lines.append(line)
            i += 1
        elif not line.strip():
            i += 1  # 字段间空行，跳过
        else:
            break  # 正文从这里开始

    # 3. 正文 = 剩余所有行，去掉开头空行
    body = lines[i:]
    while body and not body[0].strip():
        body.pop(0)

    return "---\n" + "\n".join(field_lines) + "\n---\n\n" + "\n".join(body)


def find_latest_run() -> Path:
    news_dir = Path("data/processed/news")
    run_dirs = sorted(
        [d for d in news_dir.iterdir()
         if d.is_dir() and not d.name.startswith(".")],
        reverse=True,
    )
    if not run_dirs:
        raise SystemExit("未找到任何数据目录，请先运行爬虫或 --run-id 指定")
    return run_dirs[0]


def main():
    parser = argparse.ArgumentParser(description="检查 db_import.csv 文章收录情况")
    parser.add_argument("--run-id", type=str, default=None,
                        help="指定 run_id（默认最新）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只列出缺失文件，不实际重命名")
    parser.add_argument("--print-missing", action="store_true",
                        help="只打印缺失文件清单，不重命名")
    parser.add_argument("--restore", action="store_true",
                        help="撤销 000_ 前缀，恢复原文件名")
    parser.add_argument("--repair", action="store_true",
                        help="自动修复缺失 --- 的 frontmatter（不花 LLM 费用）")
    args = parser.parse_args()

    run_dir = find_latest_run() if not args.run_id else Path(f"data/processed/news/{args.run_id}")
    if not run_dir.exists():
        raise SystemExit(f"数据目录不存在: {run_dir}")

    clean_dir = run_dir / "articles" / "clean"
    csv_path = clean_dir / "db_import.csv"

    if not clean_dir.exists():
        raise SystemExit(f"clean 目录不存在: {clean_dir}")

    # ---- 自动修复模式 ----
    if args.repair:
        repaired = 0
        failed = 0
        for p in sorted(clean_dir.glob("*.md")):
            if p.name == "db_import.csv":
                continue
            text = p.read_text(encoding="utf-8")
            if is_valid_frontmatter(text):
                continue
            try:
                new_text = repair_frontmatter(text)
            except ValueError as e:
                logger.warning(f"  ❌ {p.name}: {e}")
                failed += 1
                continue
            # 修复成功：恢复原文件名（去掉 000_ 前缀）
            new_name = p.name[len(PREFIX):] if p.name.startswith(PREFIX) else p.name
            dst = clean_dir / new_name
            if dst != p and dst.exists():
                logger.warning(f"  ⚠️ {new_name} 已存在，保留 {p.name}")
                failed += 1
                continue
            p.write_text(new_text, encoding="utf-8")
            if dst != p:
                p.rename(dst)
            repaired += 1
            logger.info(f"  🔧 修复 {p.name} → {new_name}")
        logger.info("=" * 60)
        logger.info(f"✅ 修复完成: {repaired} 篇，未能修复 {failed} 篇")
        logger.info("   下一步: python main.py --gen-csv --run-id {run} 重新生成 CSV".format(
            run=args.run_id or run_dir.name))
        return

    if not csv_path.exists():
        raise SystemExit(f"db_import.csv 不存在: {csv_path}")

    # ---- 收集 CSV 中收录的文件名 ----
    csv_files = set()
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            name = (row.get(CSV_FILENAME_COL) or "").strip()
            if name:
                csv_files.add(name)
    logger.info(f"📄 db_import.csv 收录: {len(csv_files)} 篇")

    # ---- 收集 clean/ 下所有 .md 文件 ----
    md_files = {p.name for p in clean_dir.glob("*.md") if p.is_file()}
    logger.info(f"📁 clean/ 目录文件: {len(md_files)} 个 .md")

    # ---- 找出缺失（有 md 但不在 CSV）----
    missing = sorted(md_files - csv_files)
    logger.info("=" * 60)
    if not missing:
        logger.info("✅ 全部收录，无缺失文件")
        return

    logger.info(f"⚠️ 缺失 {len(missing)} 篇（clean/ 有文件但 CSV 未收录）:")
    for name in missing:
        path = clean_dir / name
        size = path.stat().st_size if path.exists() else 0
        logger.info(f"  {name} ({size} bytes)")

    if args.print_missing:
        return

    # ---- 撤销模式：去掉 000_ 前缀 ----
    if args.restore:
        restored = 0
        for name in missing:
            if name.startswith(PREFIX):
                orig = name[len(PREFIX):]
                src = clean_dir / name
                dst = clean_dir / orig
                if dst.exists():
                    logger.warning(f"  ⚠️ {orig} 已存在，跳过 {name}")
                    continue
                src.rename(dst)
                restored += 1
                logger.info(f"  ↩️ 恢复: {name} → {orig}")
        logger.info(f"✅ 已恢复 {restored} 篇")
        return

    # ---- 重命名模式：加上 000_ 前缀 ----
    if args.dry_run:
        logger.info("🔍 预览（--dry-run，未实际重命名）:")
    renamed = 0
    for name in missing:
        src = clean_dir / name
        dst = clean_dir / f"{PREFIX}{name}"
        if args.dry_run:
            logger.info(f"  🔧 将重命名: {name} → {PREFIX}{name}")
        else:
            src.rename(dst)
            renamed += 1
            logger.info(f"  🔧 已重命名: {name} → {PREFIX}{name}")
    if not args.dry_run:
        logger.info(f"✅ 已重命名 {renamed} 篇，前缀 {PREFIX} 便于识别")
        logger.info(f"   目录: {clean_dir}")
        logger.info("   处理完可重新运行 --gen-csv 或检查这些文件为何未被解析")


if __name__ == "__main__":
    main()

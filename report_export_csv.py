# ============================================
# 国际组织报告 → 数据库导入 CSV 导出器
# ============================================
"""
把 report_collector.py 采集的报告元数据 + 全文，导出为数据库批量导入 CSV。

字段对齐 ./数据库相关指南/批量上传使用指南.md 的 Excel 模板：
    *文件名 / *标题 / 描述 内容 / *分类 / *类型 / *国家 / 地区 /
    关键词 / 发展阶段 / *话语类型 / 发布日期 / 来源 / 原始URL / 文件地址

- `*文件名` 直接取报告元数据中的 `handle`，例如 `10665/62630`，
  不绑定标题，也不添加物理文件扩展名。
- `文件地址` 指向 `fulltext/` 下的实际附件；没有 PDF/TXT 时填 `None`。

用法:
    python report_export_csv.py                                  # 自动选最新 run
    python report_export_csv.py --run-id 20260909_233623
    python report_export_csv.py --all                            # 合并所有 run
    python report_export_csv.py --run-id X --no-pdf-text         # 跳过 PDF 正文提取
    python report_export_csv.py --run-id X --desc-chars 2000     # 控制描述长度

说明:
    - 描述优先用摘要；摘要为空时（WHO 报告常见）从 PDF/TXT 全文提取前 N 字。
    - PDF 正文提取优先用 pypdf，其次 pdftotext 命令行；都没有时跳过（不报错）。
    - 报告多为全球议题（非中国），*国家 默认 global，地区留空；
      只有标题/摘要明确提到 China 的行才标 china / asia。
    - 大文件友好：fulltext/ 目录只建一次索引做 O(1) 查找；逐条流式写 CSV，
      不把所有行堆在内存（10w+ 报告也能跑）。
"""

import argparse
import csv
import json
import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("report_export")

REPORT_DIR = Path("data/processed/report")

# 数据库模板字段（顺序即 CSV 列顺序）；末尾追加项目自定义的"文件地址"
CSV_FIELDS = [
    "*文件名", "*标题", "描述 / 内容", "*分类", "*类型", "*国家", "地区",
    "关键词", "发展阶段", "*话语类型", "发布日期", "来源", "原始URL",
    "文件地址",
]

# 中国相关判定（报告库多为全球议题，需从文本判断）
CHINA_PATTERNS = [
    r"\bchina\b", r"\bchinese\b", r"\bprc\b",
    r"people'?s republic of china", r"beijing", r"mainland china",
]

# 主题关键词（用于生成 关键词 列；命中标题/摘要即保留）
TOPIC_KEYWORDS = [
    "poverty alleviation", "poverty reduction", "poverty eradication",
    "poverty", "rural development", "rural revitalization",
    "targeted poverty", "social protection", "food security",
    "agriculture", "inequality", "sustainable development",
    "health", "nutrition", "education", "employment",
]

# 发展阶段（数据库仅对 china 资源有意义；全球报告给默认值）
STAGE_MAP = {
    "traditional": "传统救济 (1949-1978)",
    "reform": "体制改革 (1979-1985)",
    "development": "开发式扶贫 (1986-1993)",
    "poverty": "八七攻坚 (1994-2012)",
    "precision": "精准扶贫 (2013-2020)",
    "rural": "乡村振兴 (2021至今)",
}


def infer_stage(year: str) -> str:
    """按发布年份推断中国贫困治理阶段"""
    try:
        y = int(year)
    except (TypeError, ValueError):
        return ""
    if y <= 1978:
        return STAGE_MAP["traditional"]
    if y <= 1985:
        return STAGE_MAP["reform"]
    if y <= 1993:
        return STAGE_MAP["development"]
    if y <= 2012:
        return STAGE_MAP["poverty"]
    if y <= 2020:
        return STAGE_MAP["precision"]
    return STAGE_MAP["rural"]


def is_china_report(title: str, abstract: str) -> bool:
    text = f"{title} {abstract}".lower()
    return any(re.search(p, text) for p in CHINA_PATTERNS)


def extract_keywords(title: str, abstract: str) -> str:
    text = f"{title} {abstract}".lower()
    hits = [kw for kw in TOPIC_KEYWORDS if kw in text]
    return ", ".join(dict.fromkeys(hits))


# ============================================================
# PDF / TXT 正文提取
# ============================================================
def _pdf_text_pypdf(path: Path, max_chars: int) -> str:
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError:
        return ""
    # pypdf 对缺字体的 PDF 会刷大量 warning，导出时只需正文，静默处理
    import logging as _logging
    _logging.getLogger("pypdf").setLevel(_logging.ERROR)
    try:
        reader = PdfReader(str(path))
        chunks = []
        total = 0
        for page in reader.pages:
            t = page.extract_text() or ""
            chunks.append(t)
            total += len(t)
            if total >= max_chars * 2:
                break
        return "\n".join(chunks)
    except Exception:
        return ""


def _pdf_text_cli(path: Path, max_chars: int) -> str:
    if not shutil.which("pdftotext"):
        return ""
    try:
        r = subprocess.run(
            ["pdftotext", "-l", "10", "-q", str(path), "-"],
            capture_output=True, timeout=60)
        if r.returncode == 0:
            return r.stdout.decode("utf-8", errors="ignore")
    except Exception:
        pass
    return ""


def _clean_text(text: str) -> str:
    text = text.replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    # 丢弃 PDF 页码等噪声行
    lines = [ln.strip() for ln in text.split("\n")]
    lines = [ln for ln in lines if len(ln) > 20 or re.search(r"[.!?。！？]", ln)]
    return " ".join(lines).strip()


def build_fulltext_index(ft_dir: Path) -> Dict[str, Path]:
    """扫描 fulltext/ 一次，建立 base_name → 实际文件 的索引。

    只做一次目录扫描（O(1) 查找），避免每个报告都 stat 磁盘，
    大目录（10w+ 文件）也不会拖慢导出。
    """
    index: Dict[str, Path] = {}
    if not ft_dir.exists():
        return index
    for f in ft_dir.iterdir():
        if not f.is_file():
            continue
        if f.suffix.lower() not in (".pdf", ".txt"):
            continue
        if f.stat().st_size <= 100:
            continue
        # 同一个 base_name 多个格式：txt 优先（纯文本），其次 pdf
        cur = index.get(f.stem)
        if cur is None or (f.suffix.lower() == ".txt"
                           and cur.suffix.lower() != ".txt"):
            index[f.stem] = f
    return index


def resolve_file_name(w: Dict) -> str:
    """返回 works.json 中的原始 handle，保持斜杠和原始格式。"""
    return str(w.get("handle") or "").strip() or str(w.get("id") or "")


def read_fulltext(path: Path, desc_chars: int,
                  use_pdf: bool = True) -> str:
    """读取正文文本。TXT 直接读；PDF 解析前 N 字。失败返回空串。"""
    if path.suffix.lower() == ".txt":
        try:
            return path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return ""
    if path.suffix.lower() == ".pdf":
        if not use_pdf:
            return ""
        text = _pdf_text_pypdf(path, desc_chars)
        if not text:
            text = _pdf_text_cli(path, desc_chars)
        return text
    return ""


def _find_latest_run() -> Optional[str]:
    if not REPORT_DIR.exists():
        return None
    runs = [d.name for d in REPORT_DIR.iterdir()
            if d.is_dir() and (d / "works.json").exists()]
    return sorted(runs)[-1] if runs else None


def _iter_run_dirs(run_id: Optional[str], all_runs: bool):
    """产出要导出的 run 目录（早到晚，便于历史优先去重）"""
    if all_runs:
        if not REPORT_DIR.exists():
            raise SystemExit(f"未找到报告数据目录: {REPORT_DIR}")
        runs = [d for d in sorted(REPORT_DIR.iterdir())
                if d.is_dir() and (d / "works.json").exists()]
        if not runs:
            raise SystemExit(f"{REPORT_DIR} 下没有含 works.json 的 run")
        return runs
    rid = run_id or _find_latest_run()
    if not rid:
        raise SystemExit(
            f"未找到报告数据，请先运行 report_collector.py，"
            f"或用 --run-id 指定（{REPORT_DIR} 下无含 works.json 的目录）")
    d = REPORT_DIR / rid
    if not (d / "works.json").exists():
        raise SystemExit(f"未找到报告数据: {d / 'works.json'}")
    return [d]


def _build_row(w: Dict, ft_index: Dict[str, Path], run_dir: Path,
               desc_chars: int, use_pdf: bool) -> Tuple[Dict, bool, bool]:
    """由一条元数据构建 CSV 行。返回 (row, 有附件, 描述非空)。"""
    handle = (w.get("handle") or "").strip()
    base_name = handle.replace("/", "_") or w.get("id", "")

    ft_path = ft_index.get(base_name) if base_name else None
    fulltext = read_fulltext(ft_path, desc_chars, use_pdf) if ft_path else ""

    title = (w.get("title") or "").strip()
    abstract = (w.get("abstract") or "").strip()

    # 描述：摘要优先；缺失时用全文前 N 字
    if abstract:
        desc = re.sub(r"\s+", " ", abstract).strip()
    elif fulltext:
        desc = _clean_text(fulltext)[:desc_chars]
    else:
        desc = ""
    desc = desc[:desc_chars]

    # 文件地址：fulltext/ 下真实相对路径；无附件填 None
    if ft_path is not None:
        try:
            file_addr = str(ft_path.relative_to(run_dir))
        except ValueError:
            file_addr = str(ft_path)
    else:
        file_addr = "None"

    china = is_china_report(title, abstract or desc)
    year = str(w.get("publication_year") or "")

    row = {
        "*文件名": resolve_file_name(w),
        "*标题": title,
        "描述 / 内容": desc,
        "*分类": "reports",
        "*类型": "text",
        "*国家": "china" if china else "global",
        "地区": "asia" if china else "",
        "关键词": extract_keywords(title, abstract or desc),
        "发展阶段": infer_stage(year) if china else "",
        "*话语类型": "institutional",
        "发布日期": f"{year}-01-01" if year else "",
        "来源": w.get("institution", ""),
        "原始URL": w.get("url", ""),
        "文件地址": file_addr,
    }
    return row, ft_path is not None, bool(desc)


def export(run_id: Optional[str], all_runs: bool, out_path: Optional[str],
           desc_chars: int, use_pdf: bool) -> int:
    run_dirs = _iter_run_dirs(run_id, all_runs)

    # 每个 run 的 fulltext/ 只扫一次（O(1) 查找）；不把全部行堆内存
    ft_indexes: Dict[Path, Dict[str, Path]] = {
        d: build_fulltext_index(d / "fulltext") for d in run_dirs
    }

    csv_path = Path(out_path) if out_path else (run_dirs[-1] / "db_import.csv")
    seen = set()
    total = with_ft = n_none = n_desc = 0

    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        # 逐 run、逐条写盘（流式），内存占用与报告总数无关
        for run_dir in run_dirs:
            works_path = run_dir / "works.json"
            try:
                works = json.loads(works_path.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning(f"跳过无法解析的 {works_path}: {e}")
                continue
            ft_index = ft_indexes[run_dir]
            for w in works:
                key = w.get("handle") or w.get("id")
                if key and key in seen:
                    continue
                if key:
                    seen.add(key)
                row, has_ft, has_desc = _build_row(
                    w, ft_index, run_dir, desc_chars, use_pdf)
                writer.writerow(row)
                total += 1
                with_ft += int(has_ft)
                n_none += int(not has_ft)
                n_desc += int(has_desc)
                if total % 2000 == 0:
                    logger.info(f"  ⏳ 已导出 {total} 条...")

    if total == 0:
        logger.warning("没有可导出的报告，CSV 仅有表头")
        return 0

    logger.info(f"📊 数据库导入 CSV: {csv_path} ({total} 条)")
    logger.info(f"   有正文附件: {with_ft} | 文件地址=None: {n_none}")
    logger.info(f"   描述非空: {n_desc} | 描述为空: {total - n_desc}")
    return total


def main():
    parser = argparse.ArgumentParser(
        description="国际组织报告 → 数据库导入 CSV 导出器")
    parser.add_argument("--run-id", type=str, default=None,
                        help="指定 report run（默认取最新）")
    parser.add_argument("--all", action="store_true",
                        help="合并 data/processed/report 下所有 run（按 handle 去重）")
    parser.add_argument("--out", type=str, default=None,
                        help="输出 CSV 路径（默认写到该 run 目录下的 db_import.csv）")
    parser.add_argument("--desc-chars", type=int, default=1000,
                        help="描述/内容字符上限（默认 1000）")
    parser.add_argument("--no-pdf-text", action="store_true",
                        help="不解析 PDF 正文（仅用摘要，速度快）")
    args = parser.parse_args()

    export(run_id=args.run_id, all_runs=args.all, out_path=args.out,
           desc_chars=args.desc_chars, use_pdf=not args.no_pdf_text)


if __name__ == "__main__":
    main()

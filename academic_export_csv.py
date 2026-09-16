# ============================================
# 英文学术文献 → 数据库导入 CSV 导出器
# ============================================
"""
把 academic_collector.py 采集的学术文献元数据 + 可选全文，导出为数据库批量导入 CSV。

字段对齐 ./数据库相关指南/批量上传使用指南.md 的 Excel 模板：
    *文件名 / *标题 / 描述 内容 / *分类 / *类型 / *国家 / 地区 /
    关键词 / 发展阶段 / *话语类型 / 发布日期 / 来源 / 原始URL / 文件地址

- `*文件名` 直接取学术元数据中的 `id`，例如 `W2606951880`；
  Crossref 无 OpenAlex ID 时通常是 DOI，例如 `10.1016/j.xxx`。
- `文件地址` 指向 `fulltext/` 下的实际附件；没有 PDF/XML/TXT 时填 `None`。
- 大文件友好：优先逐行读取 works.csv，不一次性加载全部 works.json；
  fulltext/ 目录只建一次索引，写 CSV 时逐条落盘。

用法:
    python academic_export_csv.py                                  # 自动选最新 run
    python academic_export_csv.py --run-id 20260829_161402
    python academic_export_csv.py --all                            # 合并所有 run
    python academic_export_csv.py --run-id X --no-fulltext-text     # 不解析全文补描述
    python academic_export_csv.py --run-id X --desc-chars 2000      # 控制描述长度
"""

import argparse
import csv
import html
import json
import logging
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, Iterator, Optional, Tuple

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("academic_export")

try:
    csv.field_size_limit(sys.maxsize)
except OverflowError:
    csv.field_size_limit(2**31 - 1)

ACADEMIC_DIR = Path("data/processed/academic")

# 数据库模板字段（顺序即 CSV 列顺序）；末尾追加项目自定义的"文件地址"
CSV_FIELDS = [
    "*文件名", "*标题", "描述 / 内容", "*分类", "*类型", "*国家", "地区",
    "关键词", "发展阶段", "*话语类型", "发布日期", "来源", "原始URL",
    "文件地址",
]

CHINA_PATTERNS = [
    r"\bchina\b", r"\bchinese\b", r"\bprc\b",
    r"people'?s republic of china", r"\bbeijing\b", r"mainland china",
    r"targeted poverty alleviation", r"rural revitalization",
    r"common prosperity", r"battle against poverty",
    r"poverty governance",
]

TOPIC_KEYWORDS = [
    "targeted poverty alleviation", "rural revitalization",
    "poverty governance", "battle against poverty", "common prosperity",
    "relocation for poverty alleviation", "industrial poverty alleviation",
    "educational poverty alleviation", "health poverty alleviation",
    "poverty alleviation", "poverty reduction", "poverty eradication",
    "multidimensional poverty", "absolute poverty", "poverty trap",
    "rural poverty", "rural development", "social protection",
    "inequality", "development economics", "china", "poverty",
]

STAGE_MAP = {
    "traditional": "传统救济 (1949-1978)",
    "reform": "体制改革 (1979-1985)",
    "development": "开发式扶贫 (1986-1993)",
    "poverty": "八七攻坚 (1994-2012)",
    "precision": "精准扶贫 (2013-2020)",
    "rural": "乡村振兴 (2021至今)",
}


def infer_stage(year: str) -> str:
    try:
        y = int(str(year).strip())
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


def _stringify(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                name = item.get("name") or item.get("display_name") or ""
                score = item.get("score")
                parts.append(f"{name}({score})" if name and score is not None else name)
            else:
                parts.append(str(item))
        return "; ".join(p.strip() for p in parts if p and str(p).strip())
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value).strip()


def is_china_work(w: Dict, title: str, abstract: str) -> bool:
    text = " ".join([
        title, abstract,
        _stringify(w.get("keywords")),
        _stringify(w.get("concepts")),
    ]).lower()
    return any(re.search(p, text) for p in CHINA_PATTERNS)


def extract_keywords(w: Dict, title: str, abstract: str) -> str:
    text = " ".join([
        title, abstract,
        _stringify(w.get("keywords")),
        _stringify(w.get("concepts")),
    ]).lower()
    hits = [kw for kw in TOPIC_KEYWORDS if kw in text]

    # 保留元数据已有关键词/概念的前几个高信号词，避免只剩通用 poverty。
    for raw in (_stringify(w.get("keywords")), _stringify(w.get("concepts"))):
        for part in raw.split(";"):
            clean = re.sub(r"\([^)]+\)", "", part).strip()
            if clean and len(clean) <= 60:
                hits.append(clean)
    return ", ".join(dict.fromkeys(hits))


def resolve_file_name(w: Dict) -> str:
    """返回元数据中的稳定 ID，不使用标题，也不添加物理文件扩展名。"""
    return _stringify(w.get("id")) or _stringify(w.get("doi"))


def _dedup_key(w: Dict) -> str:
    doi = _stringify(w.get("doi")).lower()
    doi = doi.replace("https://doi.org/", "").replace("http://dx.doi.org/", "")
    return doi or resolve_file_name(w).lower()


def _base_name(w: Dict) -> str:
    """全文文件名基准：OpenAlex 文件一般就是 W ID。"""
    fid = resolve_file_name(w)
    if fid.startswith("W"):
        return fid
    return re.sub(r"[^A-Za-z0-9._-]+", "_", fid).strip("_")


# ============================================================
# PDF / XML / TXT 正文提取
# ============================================================
def _pdf_text_pypdf(path: Path, max_chars: int) -> str:
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError:
        return ""
    import logging as _logging
    _logging.getLogger("pypdf").setLevel(_logging.ERROR)
    try:
        reader = PdfReader(str(path))
        chunks = []
        total = 0
        for page in reader.pages:
            text = page.extract_text() or ""
            chunks.append(text)
            total += len(text)
            if total >= max_chars * 2:
                break
        return "\n".join(chunks)
    except Exception:
        return ""


def _pdf_text_cli(path: Path) -> str:
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


def _xml_text(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    return html.unescape(text)


def _clean_text(text: str) -> str:
    text = text.replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    lines = [ln.strip() for ln in text.split("\n")]
    lines = [ln for ln in lines if len(ln) > 20 or re.search(r"[.!?。！？]", ln)]
    return " ".join(lines).strip()


def read_fulltext(path: Optional[Path], desc_chars: int, use_fulltext: bool) -> str:
    if not path or not use_fulltext:
        return ""
    suffix = path.suffix.lower()
    if suffix == ".txt":
        try:
            return path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return ""
    if suffix == ".xml":
        return _xml_text(path)
    if suffix == ".pdf":
        return _pdf_text_pypdf(path, desc_chars) or _pdf_text_cli(path)
    return ""


def build_fulltext_index(ft_dir: Path) -> Dict[str, Dict[str, Path]]:
    """扫描 fulltext/ 一次，建立 base_name -> {pdf/xml/txt: path} 索引。"""
    index: Dict[str, Dict[str, Path]] = {}
    if not ft_dir.exists():
        return index
    for f in ft_dir.iterdir():
        if not f.is_file() or f.stat().st_size <= 100:
            continue
        ext = f.suffix.lower().lstrip(".")
        if ext not in ("pdf", "xml", "txt"):
            continue
        index.setdefault(f.stem, {})[ext] = f
    return index


def pick_attachment(files: Dict[str, Path]) -> Optional[Path]:
    # 数据库附件优先给原始 PDF；没有 PDF 再给 XML/TXT。
    for ext in ("pdf", "xml", "txt"):
        if ext in files:
            return files[ext]
    return None


def pick_text_source(files: Dict[str, Path]) -> Optional[Path]:
    # 描述补全优先用已结构化文本，再退到 PDF 解析。
    for ext in ("txt", "xml", "pdf"):
        if ext in files:
            return files[ext]
    return None


# ============================================================
# 元数据流式读取
# ============================================================
def _find_latest_run() -> Optional[str]:
    if not ACADEMIC_DIR.exists():
        return None
    runs = [d.name for d in ACADEMIC_DIR.iterdir()
            if d.is_dir() and ((d / "works.csv").exists()
                               or (d / "works.json").exists())]
    return sorted(runs)[-1] if runs else None


def _iter_run_dirs(run_id: Optional[str], all_runs: bool):
    if all_runs:
        if not ACADEMIC_DIR.exists():
            raise SystemExit(f"未找到学术数据目录: {ACADEMIC_DIR}")
        runs = [d for d in sorted(ACADEMIC_DIR.iterdir())
                if d.is_dir() and ((d / "works.csv").exists()
                                   or (d / "works.json").exists())]
        if not runs:
            raise SystemExit(f"{ACADEMIC_DIR} 下没有含 works.csv/works.json 的 run")
        return runs
    rid = run_id or _find_latest_run()
    if not rid:
        raise SystemExit(
            f"未找到学术数据，请先运行 academic_collector.py，"
            f"或用 --run-id 指定（{ACADEMIC_DIR} 下无有效 run）")
    d = ACADEMIC_DIR / rid
    if not (d / "works.csv").exists() and not (d / "works.json").exists():
        raise SystemExit(f"未找到学术数据: {d / 'works.csv'} 或 {d / 'works.json'}")
    return [d]


def _iter_works_csv(path: Path) -> Iterator[Dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            yield dict(row)


def _iter_works_json(path: Path) -> Iterator[Dict]:
    """标准库增量解析 JSON 数组，避免大 works.json 一次性进内存。"""
    decoder = json.JSONDecoder()
    buf = ""
    in_array = False
    with open(path, encoding="utf-8-sig") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk and not buf.strip():
                break
            buf += chunk
            while True:
                buf = buf.lstrip()
                if not in_array:
                    if not buf:
                        break
                    if buf[0] != "[":
                        raise ValueError(f"{path} 不是 JSON 数组")
                    buf = buf[1:]
                    in_array = True
                    continue
                if buf.startswith("]"):
                    return
                if buf.startswith(","):
                    buf = buf[1:]
                    continue
                try:
                    obj, idx = decoder.raw_decode(buf)
                except json.JSONDecodeError:
                    break
                if isinstance(obj, dict):
                    yield obj
                buf = buf[idx:]
            if not chunk:
                if buf.strip() and buf.strip() != "]":
                    raise ValueError(f"{path} JSON 未完整解析")
                break


def _iter_works(run_dir: Path) -> Iterator[Dict]:
    csv_path = run_dir / "works.csv"
    if csv_path.exists():
        yield from _iter_works_csv(csv_path)
        return
    json_path = run_dir / "works.json"
    yield from _iter_works_json(json_path)


def _build_row(w: Dict, ft_index: Dict[str, Dict[str, Path]], run_dir: Path,
               desc_chars: int, use_fulltext: bool) -> Tuple[Dict, bool, bool]:
    base_name = _base_name(w)
    files = ft_index.get(base_name, {}) if base_name else {}
    attachment = pick_attachment(files)
    text_source = pick_text_source(files)

    title = _stringify(w.get("title"))
    abstract = re.sub(r"\s+", " ", _stringify(w.get("abstract"))).strip()
    fulltext = read_fulltext(text_source, desc_chars, use_fulltext) if not abstract else ""
    desc = (abstract or _clean_text(fulltext))[:desc_chars]

    if attachment is not None:
        try:
            file_addr = str(attachment.relative_to(run_dir))
        except ValueError:
            file_addr = str(attachment)
    else:
        file_addr = "None"

    year = _stringify(w.get("publication_year"))
    pub_date = _stringify(w.get("publication_date"))
    if not pub_date and year:
        pub_date = f"{year}-01-01"

    china = is_china_work(w, title, abstract or desc)
    source = _stringify(w.get("journal")) or _stringify(w.get("source_api"))
    url = _stringify(w.get("landing_page_url")) or _stringify(w.get("url"))

    row = {
        "*文件名": resolve_file_name(w),
        "*标题": title,
        "描述 / 内容": desc,
        "*分类": "academic",
        "*类型": "text",
        "*国家": "china" if china else "global",
        "地区": "asia" if china else "",
        "关键词": extract_keywords(w, title, abstract or desc),
        "发展阶段": infer_stage(year) if china else "",
        "*话语类型": "academic",
        "发布日期": pub_date,
        "来源": source,
        "原始URL": url,
        "文件地址": file_addr,
    }
    return row, attachment is not None, bool(desc)


def export(run_id: Optional[str], all_runs: bool, out_path: Optional[str],
           desc_chars: int, use_fulltext: bool) -> int:
    run_dirs = _iter_run_dirs(run_id, all_runs)
    ft_indexes = {d: build_fulltext_index(d / "fulltext") for d in run_dirs}

    csv_path = Path(out_path) if out_path else (run_dirs[-1] / "db_import.csv")
    seen = set()
    total = with_ft = n_none = n_desc = 0

    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for run_dir in run_dirs:
            ft_index = ft_indexes[run_dir]
            try:
                iterator = _iter_works(run_dir)
                for w in iterator:
                    key = _dedup_key(w)
                    if key and key in seen:
                        continue
                    if key:
                        seen.add(key)
                    row, has_ft, has_desc = _build_row(
                        w, ft_index, run_dir, desc_chars, use_fulltext)
                    writer.writerow(row)
                    total += 1
                    with_ft += int(has_ft)
                    n_none += int(not has_ft)
                    n_desc += int(has_desc)
                    if total % 2000 == 0:
                        logger.info(f"  ⏳ 已导出 {total} 条...")
            except Exception as e:
                logger.warning(f"跳过无法导出的 run {run_dir}: {e}")

    if total == 0:
        logger.warning("没有可导出的学术文献，CSV 仅有表头")
        return 0

    logger.info(f"📊 数据库导入 CSV: {csv_path} ({total} 条)")
    logger.info(f"   有正文附件: {with_ft} | 文件地址=None: {n_none}")
    logger.info(f"   描述非空: {n_desc} | 描述为空: {total - n_desc}")
    return total


def main() -> None:
    parser = argparse.ArgumentParser(
        description="英文学术文献 → 数据库导入 CSV 导出器")
    parser.add_argument("--run-id", type=str, default=None,
                        help="指定 academic run（默认取最新）")
    parser.add_argument("--all", action="store_true",
                        help="合并 data/processed/academic 下所有 run（按 DOI/id 去重）")
    parser.add_argument("--out", type=str, default=None,
                        help="输出 CSV 路径（默认写到该 run 目录下的 db_import.csv）")
    parser.add_argument("--desc-chars", type=int, default=1000,
                        help="描述/内容字符上限（默认 1000）")
    parser.add_argument("--no-fulltext-text", action="store_true",
                        help="摘要为空时不解析 fulltext PDF/XML/TXT 补描述")
    args = parser.parse_args()

    export(run_id=args.run_id, all_runs=args.all, out_path=args.out,
           desc_chars=args.desc_chars, use_fulltext=not args.no_fulltext_text)


if __name__ == "__main__":
    main()

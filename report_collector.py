# ============================================
# 国际官方组织报告采集器 — DSpace 报告库多机构
# ============================================
"""
从国际组织公开报告库采集中国贫困/减贫/乡村振兴相关报告元数据与全文。

多数国际组织报告库基于 DSpace（WHO IRIS / World Bank OKR / IFAD 等），
统一 DSpace REST API 检索，覆盖多个机构。DSpace 的 TEXT bundle 提供
报告全文文本（.txt），主题建模直接可用，无需 PDF 解析。

用法:
    python report_collector.py                              # 全部机构
    python report_collector.py --org WHO WorldBank          # 指定机构
    python report_collector.py --org WHO --limit 5          # 每机构限条数
    python report_collector.py --org WHO --out-dir /tmp/x   # 指定输出目录
    python report_collector.py --org WHO --download         # 采集后下载全文(pdf+txt)

输出:
    data/processed/report/{run_id}/
    ├── works.json      # 报告元数据（标题/年份/机构/摘要/全文链接）
    ├── works.csv
    ├── summary.md
    └── fulltext/       # --download 下载的 PDF / TXT 全文
"""

import csv
import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import subprocess
import urllib.parse

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ============================================================
# 机构报告库配置（DSpace 统一 API）
# status: verified=本环境实测可用 | untested=待用户服务器验证
# ============================================================
REPORT_REPOSITORIES = {
    # 已验证（本环境实测 DSpace API 可用）
    "WHO": {
        "base": "https://iris.who.int", "api": "dspace",
        "note": "世界卫生组织 IRIS (已验证✓)",
    },
    "WorldBank": {
        "base": "https://openknowledge.worldbank.org", "api": "dspace",
        "note": "世界银行开放知识库 (curl✓, 本机requests间歇抖动)",
    },
    # 疑似 DSpace（本环境 SSL 抖动，待服务器验证）
    "IFAD": {
        "base": "https://repository.ifad.org", "api": "dspace",
        "note": "国际农业发展基金",
    },
    "UNWomen": {
        "base": "https://docs.unwomen.org", "api": "dspace",
        "note": "联合国妇女署",
    },
    "UNESCAP": {
        "base": "https://repository.unescap.org", "api": "dspace",
        "note": "联合国亚太经社会",
    },
    "UNRISD": {
        "base": "https://cdn.unrisd.org", "api": "dspace",
        "note": "联合国社会发展研究所 (需确认库类型)",
    },
    # 补充候选：常见国际组织 DSpace 报告库（待服务器 check 验证 URL）
    "WFP": {
        "base": "https://repository.wfp.org", "api": "dspace",
        "note": "世界粮食计划署 DSpace(候选)",
    },
    "ECLAC": {
        "base": "https://repositorio.cepal.org", "api": "dspace",
        "note": "拉美经委会 DSpace(候选,西语多含英文)",
    },
    "UNECA": {
        "base": "https://repository.uneca.org", "api": "dspace",
        "note": "联合国非洲经委会(候选)",
    },
    "UNICEF": {
        "base": "https://open.unicef.org", "api": "dspace",
        "note": "联合国儿童基金会(候选)",
    },
    "IOM": {
        "base": "https://iom.rechord.net", "api": "dspace",
        "note": "国际移民组织(候选,需确认)",
    },
    # 非 DSpace（预留，后续适配）
    "FAO": {"base": "", "api": "todo", "note": "粮农组织文档库(未适配)"},
    "ADB": {"base": "", "api": "todo", "note": "亚洲开发银行(未适配)"},
    "ILO": {"base": "", "api": "todo", "note": "国际劳工组织(未适配)"},
}

# 报告检索关键词（中国贫困/减贫/发展主题，报告比学术更倾向政策话语）
REPORT_KEYWORDS = [
    "poverty alleviation China",
    "poverty reduction China",
    "rural development poverty",
    "targeted poverty alleviation",
    "rural revitalization",
    "poverty eradication",
    "social protection China",
    "agriculture poverty rural",
]

REQUEST_DELAY = 0.3


def md_val(val) -> str:
    """DSpace 7.6 metadata 值：list[str] / list[{value}] / 单值"""
    if not val:
        return ""
    v = val[0] if isinstance(val, list) else val
    if isinstance(v, dict):
        return v.get("value", "")
    return str(v)


# ============================================================
# DSpace 报告库（统一发现/检索/全文）
# ============================================================
class DSpaceRepository:
    def __init__(self, session: requests.Session, base: str,
                 org_name: str, delay: float = REQUEST_DELAY):
        self.session = session
        self.base = base.rstrip("/")
        self.org_name = org_name
        self.delay = delay
        self.api = f"{self.base}/server/api"
        self.name = org_name

    def _fetch(self, url: str, params: Optional[Dict] = None):
        """requests 优先；SSL/连接失败时 curl -sk fallback

        解决部分机构（WorldBank/IFAD/WFP 等）在 macOS/部分环境的
        TLS 兼容问题（requests/urllib3 握手 EOF，但 curl 能通）。
        返回类 response 对象（.json/.text/.content），失败返回 None。
        """
        try:
            resp = self.session.get(url, params=params, timeout=30,
                                    verify=False)
            if resp.status_code < 500:
                return resp
            logger.warning(f"  {self.org_name} HTTP {resp.status_code} "
                           f"→ curl fallback")
        except (requests.exceptions.SSLError,
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout) as e:
            logger.warning(f"  {self.org_name} requests "
                           f"{type(e).__name__} → curl fallback")
        # curl fallback（-k 忽略证书，解决 TLS 兼容）
        qs = urllib.parse.urlencode(params or {})
        full = f"{url}?{qs}" if qs else url
        try:
            r = subprocess.run(["curl", "-sk", "-m", "30", full],
                               capture_output=True, timeout=35)
            if r.returncode == 0 and r.stdout:
                class Ctx:
                    status_code = 200
                c = Ctx()
                c.text = r.stdout.decode("utf-8", errors="ignore")
                c.content = r.stdout
                c.json = lambda t=c.text: json.loads(t)
                return c
        except Exception:
            pass
        return None

    # ---- 检索 ----
    def search(self, query: str, page: int = 0,
               size: int = 20) -> Optional[List[Dict]]:
        url = f"{self.api}/discover/search/objects"
        resp = self._fetch(url, params={"query": query, "page": page,
                                        "size": size})
        if resp is None:
            return None
        try:
            d = resp.json()
            sr = d.get("_embedded", {}).get("searchResult", {})
            objects = sr.get("_embedded", {}).get("objects", [])
            return [o["_embedded"]["indexableObject"] for o in objects]
        except Exception:
            return None

    def fetch(self, query: str, limit: Optional[int] = None) -> List[Dict]:
        """翻页检索，返回 normalize 后的报告元数据"""
        results = []
        page = 0
        while True:
            if limit and len(results) >= limit:
                break
            items = self.search(query, page=page)
            if not items:
                break
            for it in items:
                results.append(self.normalize(it))
                if limit and len(results) >= limit:
                    break
            page += 1
            if len(items) < 20:
                break
            time.sleep(self.delay)
        return results

    # ---- 元数据解析 ----
    def normalize(self, item: Dict) -> Dict:
        md = item.get("metadata", {})
        title = md_val(md.get("dc.title"))
        year = md_val(md.get("dc.date.issued"))[:4]
        if not str(year).isdigit():
            year = ""
        # 作者：dc.creator 可能是 list[str] 或 list[{value}]，逐个解析
        authors = []
        for c in (md.get("dc.creator") or []):
            name = c.get("value") if isinstance(c, dict) else str(c)
            if name:
                authors.append(name)
        return {
            "id": item.get("uuid", ""),
            "handle": item.get("handle", ""),
            "title": title.strip(),
            "publication_year": year,
            "abstract": md_val(md.get("dc.description.abstract")).strip(),
            "authors": authors,
            "institution": self.org_name,
            "url": f"{self.base}/handle/{item.get('handle','')}"
                    if item.get("handle") else item.get("_links", {}).get("self", {}).get("href", ""),
            "source_type": "report",
        }

    # ---- 全文链接（pdf + txt）----
    def get_content_links(self, uuid: str) -> tuple:
        """返回 (pdf_url, txt_url)。txt 是 DSpace 提取的全文文本，主题建模首选。"""
        pdf_url = txt_url = ""
        try:
            resp = self._fetch(f"{self.api}/core/items/{uuid}/bundles")
            if resp is None:
                return "", ""
            bundles = resp.json().get("_embedded", {}).get("bundles", [])
            for b in bundles:
                bname = (b.get("name") or "").upper()
                r2 = self._fetch(
                    f"{self.api}/core/bundles/{b['uuid']}/bitstreams")
                if r2 is None:
                    continue
                for bf in r2.json().get("_embedded", {}).get("bitstreams", []):
                    fname = (bf.get("name") or "").lower()
                    c_url = f"{self.api}/core/bitstreams/{bf['uuid']}/content"
                    if bname == "ORIGINAL" and fname.endswith(".pdf"):
                        pdf_url = c_url
                    elif bname == "TEXT" and fname.endswith(".txt"):
                        txt_url = c_url
        except Exception:
            pass
        return pdf_url, txt_url


# ============================================================
# 采集器（多机构 × 关键词）
# ============================================================
class ReportCollector:
    def __init__(self, orgs: Optional[List[str]] = None,
                 out_dir: Optional[str] = None,
                 run_id: Optional[str] = None,
                 delay: float = REQUEST_DELAY):
        self.session = requests.Session()
        self.delay = delay
        self.session.headers["User-Agent"] = (
            "poverty-research/1.0 (mailto:unknownluo7@gmail.com)")

        # 机构选择：全部 or 指定
        if orgs:
            self.orgs = [o for o in orgs if o in REPORT_REPOSITORIES]
            missing = [o for o in orgs if o not in REPORT_REPOSITORIES]
            if missing:
                raise SystemExit(f"未知机构 {missing}，可选 {sorted(REPORT_REPOSITORIES)}")
        else:
            self.orgs = [n for n, c in REPORT_REPOSITORIES.items()
                         if c["api"] == "dspace"]
        self.repos = [
            DSpaceRepository(self.session, REPORT_REPOSITORIES[o]["base"], o, delay)
            for o in self.orgs
        ]

        # 输出目录：out_dir > run_id(默认 data/processed/report/{run_id}) > 新时间戳
        if out_dir:
            self.out_dir = Path(out_dir)
            self.run_id = run_id or self.out_dir.name
        elif run_id:
            self.out_dir = Path(f"data/processed/report/{run_id}")
            self.run_id = run_id
        else:
            self.run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.out_dir = Path(f"data/processed/report/{self.run_id}")
        self.out_dir.mkdir(parents=True, exist_ok=True)

        # 增量去重基准（扫已有 report run 的 handle）
        self.seen_handles = self._load_seen_handles()

    def _load_seen_handles(self) -> set:
        report_dir = Path("data/processed/report")
        handles = set()
        if not report_dir.exists():
            return handles
        for run_dir in sorted(report_dir.iterdir(), reverse=True):
            if not run_dir.is_dir() or run_dir.name == self.run_id:
                continue
            fp = run_dir / "works.json"
            if not fp.exists():
                continue
            try:
                for w in json.loads(fp.read_text(encoding="utf-8")):
                    if w.get("handle"):
                        handles.add(w["handle"])
            except Exception:
                continue
        return handles

    def load_run(self) -> List[Dict]:
        """读取已有 run 的报告元数据 works.json"""
        path = self.out_dir / "works.json"
        if not path.exists():
            raise SystemExit(f"未找到报告数据: {path}")
        return json.loads(path.read_text(encoding="utf-8"))

    def check_orgs(self, query: str = "poverty alleviation",
                   size: int = 1) -> List[str]:
        """探测各机构库可用性，返回可用机构列表（不采集）

        用 repo._fetch（requests + curl 兜底），自动兼容 TLS 问题。
        """
        logger.info("🔍 机构库可用性探测（requests + curl 兜底）：")
        available = []
        for repo in self.repos:
            note = REPORT_REPOSITORIES[repo.name]["note"]
            url = f"{repo.api}/discover/search/objects"
            resp = repo._fetch(url, params={"query": query, "page": 0,
                                            "size": size})
            if resp is not None and "_embedded" in resp.text:
                available.append(repo.name)
                logger.info(f"  ✅ {repo.name}: 可用 ({note})")
            elif resp is not None:
                logger.warning(f"  ❌ {repo.name}: 非 DSpace 响应 ({note})")
            else:
                logger.warning(f"  ❌ {repo.name}: requests+curl 均不可达 "
                               f"({note})")
        logger.info(
            f"\n📋 可用机构: {available}\n"
            f"   全量采集: python report_collector.py --org "
            f"{' '.join(available)}"
        )
        return available

    def collect_all(self, keywords: Optional[List[str]] = None,
                    limit: Optional[int] = None) -> List[Dict]:
        keywords = keywords or REPORT_KEYWORDS
        all_reports = []
        logger.info("=" * 60)
        logger.info(f"📚 官方组织报告采集 | 机构 {len(self.repos)} 个 | "
                    f"关键词 {len(keywords)} 个")
        logger.info(f"   机构: {[r.name for r in self.repos]}")
        logger.info("=" * 60)

        for repo in self.repos:
            logger.info(f"▸ 机构库: {repo.name} ({REPORT_REPOSITORIES[repo.name]['note']})")
            new = 0
            for qi, kw in enumerate(keywords, 1):
                got = repo.fetch(kw, limit=limit)
                kw_new = 0
                for w in got:
                    if w.get("handle") and w["handle"] in self.seen_handles:
                        continue
                    if w.get("handle"):
                        self.seen_handles.add(w["handle"])
                    if not w.get("title"):
                        continue
                    all_reports.append(w)
                    new += 1
                    kw_new += 1
                logger.info(f"  [{qi}/{len(keywords)}] '{kw}': +{kw_new} 条 "
                            f"(累计 {len(all_reports)})")
            logger.info(f"  {repo.name} 合计新增 {new} 条")

        logger.info("=" * 60)
        logger.info(f"✅ 采集完成: 共 {len(all_reports)} 条报告元数据")
        return all_reports

    def save(self, reports: List[Dict]) -> None:
        json_path = self.out_dir / "works.json"
        # 增量合并（续爬写回）
        if json_path.exists():
            try:
                existing = json.loads(json_path.read_text(encoding="utf-8"))
            except Exception:
                existing = []
            seen = {w.get("handle") for w in existing if w.get("handle")}
            merged = list(existing)
            for w in reports:
                if w.get("handle") and w["handle"] in seen:
                    continue
                if w.get("handle"):
                    seen.add(w["handle"])
                merged.append(w)
            reports = merged
            logger.info(f"🔗 合并原 {len(existing)} + 新增 → 累计 {len(reports)}")

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(reports, f, ensure_ascii=False, indent=2)
        logger.info(f"📄 JSON: {json_path} ({len(reports)} 条)")

        fields = ["id", "handle", "title", "publication_year", "abstract",
                  "authors", "institution", "url", "source_type"]
        csv_path = self.out_dir / "works.csv"
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for w in reports:
                row = dict(w)
                row["authors"] = "; ".join(w.get("authors", []))
                writer.writerow({k: row.get(k, "") for k in fields})
        logger.info(f"📊 CSV: {csv_path}")

        self._generate_summary(reports)

    def _generate_summary(self, reports: List[Dict]) -> None:
        by_org = {}
        year_dist = {}
        has_abs = 0
        for w in reports:
            by_org[w.get("institution")] = by_org.get(w.get("institution"), 0) + 1
            y = w.get("publication_year")
            if y:
                year_dist[y] = year_dist.get(y, 0) + 1
            if w.get("abstract"):
                has_abs += 1
        lines = [
            "# 国际组织报告采集报告", "",
            f"- 采集时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"- 报告总数: {len(reports)}",
            f"- 有摘要: {has_abs} ({has_abs*100//max(1,len(reports))}%)",
            "", f"- 机构分布: {by_org}",
            "", "## 年份分布", "",
        ]
        for y in sorted(year_dist, reverse=True):
            lines.append(f"- {y}: {year_dist[y]} 篇")
        self.out_dir.joinpath("summary.md").write_text(
            "\n".join(lines), encoding="utf-8")
        logger.info(f"📋 报告: {self.out_dir}/summary.md")

    # ---- 全文下载（可选）----
    def download_fulltext(self, reports: List[Dict],
                          limit: Optional[int] = None,
                          include_txt: bool = False) -> int:
        """按机构归类，解析 PDF + TEXT 全文链接并下载

        pdf 优先（原始报告，可读/保留图表）；pdf 缺失时用 txt 兜底。
        include_txt=True 时额外下载 txt（DSpace 提取的全文文本，主题建模用）。

        命名规则: fulltext/{handle 的 / 换成 _}.{ext}，
        例: handle 10665/62630 → 10665_62630.pdf（handle 全局唯一，可对应原页面）。
        记录: download_log.json（每篇 pdf/txt 状态，断点续传）
            + download_fail.log（失败明细，追加）
        """
        # 先确保结构存在：目录 + download_log.json + download_fail.log
        ft_dir = self.out_dir / "fulltext"
        ft_dir.mkdir(parents=True, exist_ok=True)
        log_path = ft_dir / "download_log.json"
        if not log_path.exists():
            log_path.write_text("{}", encoding="utf-8")
        fail_log = ft_dir / "download_fail.log"
        fail_log.touch(exist_ok=True)

        if not reports:
            logger.warning("⚠️ 无报告可下载（确认 --run-id 的 works.json 非空）")
            logger.info(f"   📁 已初始化: {ft_dir}")
            return 0
        todo = reports[:limit] if limit else reports
        logger.info(f"📥 本次需下载: {len(todo)} 篇")

        # 下载记录（断点续传 + 可追溯）
        log = {}
        if log_path.exists():
            try:
                log = json.loads(log_path.read_text(encoding="utf-8"))
            except Exception:
                log = {}

        # 机构 → repo 映射
        repo_map = {r.name: r for r in self.repos}
        downloaded = skipped = fail = 0

        # fail.log 运行头（无条件，本次概况在最上）
        with open(fail_log, "a", encoding="utf-8") as f:
            f.write(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                    f"Report Download: 需下载 {len(todo)} 篇\n")

        for i, w in enumerate(todo, 1):
            handle = w.get("handle", "") or w.get("id", "x")
            entry = log.setdefault(
                handle, {"title": (w.get("title") or "")[:60]})
            # 断点续传：任一格式已成功 → 跳过
            if entry.get("pdf") == "ok" or entry.get("txt") == "ok":
                skipped += 1
                continue

            repo = repo_map.get(w.get("institution"))
            if not repo or not w.get("id"):
                entry.setdefault("pdf", "fail")
                entry.setdefault("txt", "fail")
                fail += 1
                self._append_fail(fail_log, handle, "机构不可用")
                continue

            pdf_url, txt_url = repo.get_content_links(w["id"])
            base_name = handle.replace("/", "_") or w["id"]
            got = False
            reason = ""
            # 1) PDF 优先：有 pdf_url 就下 PDF（成功即完成本报告）
            if pdf_url:
                pdf_ok, pdf_reason = self._dl(
                    pdf_url, ft_dir / f"{base_name}.pdf")
                entry["pdf"] = "ok" if pdf_ok else "fail"
                got = pdf_ok
                reason = "" if pdf_ok else pdf_reason
            else:
                entry["pdf"] = "not_found"
                reason = "无PDF链接"
            # 2) PDF 未成功 → txt 兜底
            if not got:
                if txt_url:
                    txt_ok, txt_reason = self._dl(
                        txt_url, ft_dir / f"{base_name}.txt")
                    entry["txt"] = "ok" if txt_ok else "fail"
                    got = txt_ok
                    if not txt_ok:
                        reason = (reason + "+" + txt_reason) if reason else txt_reason
                else:
                    entry["txt"] = "not_found"
                    reason = reason + "+无TXT全文" if reason else "无TXT全文"
            # 3) PDF 已成功时默认不下 txt；仅显式 --include-txt 才额外补
            elif include_txt and txt_url:
                txt_ok, _ = self._dl(
                    txt_url, ft_dir / f"{base_name}.txt")
                entry["txt"] = "ok" if txt_ok else "fail"

            if got:
                downloaded += 1
            else:
                fail += 1
                # 实时写入失败原因（与下载同步）
                self._append_fail(fail_log, handle, reason or "下载失败")
            if i % 20 == 0 or i == len(todo):
                logger.info(
                    f"  ⏳ 下载进度: {i}/{len(todo)} | "
                    f"✓{downloaded} ⊘跳过{skipped} ✗{fail}")
                # 实时写 download_log（与进度同步，中断保留）
                log_path.write_text(
                    json.dumps(log, ensure_ascii=False, indent=2),
                    encoding="utf-8")
            time.sleep(self.delay)

        # 最终写一次 download_log（确保完整）
        log_path.write_text(
            json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")

        logger.info(
            f"✅ 全文下载完成: ✓新下 {downloaded} | ⊘跳过 {skipped} "
            f"| ✗失败 {fail}")
        logger.info(f"   📋 下载记录: {log_path}")
        if fail > 0:
            logger.info(f"   ❌ 失败明细（实时记录）: {fail_log}")

        # 整体下载统计（最新一次运行，覆盖写，方便查看）
        summary = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "need": len(todo),          # 本次需要下载
            "downloaded": downloaded,   # 本次新下载成功
            "skipped": skipped,         # 已下载/已有，跳过
            "failed": fail,             # 失败
            "done_total": len([v for v in log.values()
                               if v.get("pdf") == "ok"
                               or v.get("txt") == "ok"]),  # 累计已下载
        }
        sum_path = ft_dir / "download_summary.json"
        sum_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        logger.info(f"   📊 下载统计: {sum_path}")
        return downloaded

    def _append_fail(self, fail_log: Path, handle: str, reason: str) -> None:
        """实时追加一条失败到 download_fail.log（与下载同步）"""
        with open(fail_log, "a", encoding="utf-8") as f:
            f.write(f"  ✗ {handle} | {reason}\n")

    def _dl(self, url: str, path: Path) -> Tuple[bool, str]:
        """下载并校验（PDF 用 %PDF magic，TXT 非空）；requests 失败 curl 兜底

        Returns:
            (True, "ok") 成功；或 (False, 具体原因)（HTTP码/chunked/校验失败等）
        """
        content = None
        reason = ""
        try:
            resp = self.session.get(url, timeout=90, verify=False)
            if resp.status_code == 200 and resp.content:
                content = resp.content
            else:
                reason = f"HTTP {resp.status_code}"
        except requests.exceptions.RequestException as e:
            reason = type(e).__name__  # ChunkedEncodingError/SSLError/Timeout...
            content = None
        if content is None:
            # curl 兜底（--retry 应对 chunked 断开，-k 解决 TLS 兼容）
            try:
                r = subprocess.run(
                    ["curl", "-sk", "-m", "90", "--retry", "3",
                     "-o", str(path), url],
                    capture_output=True, timeout=100)
                if r.returncode == 0 and path.exists() \
                        and path.stat().st_size > 100:
                    content = path.read_bytes()
                else:
                    return False, reason or f"curl失败(rc={r.returncode})"
            except Exception as e:
                return False, reason or f"curl异常 {type(e).__name__}"
        # 校验内容有效
        if path.suffix == ".pdf" and content[:4] != b"%PDF":
            return False, "非PDF"
        if len(content) < 100:
            return False, f"内容过短({len(content)})"
        path.write_bytes(content)
        return True, "ok"


def main():
    import argparse
    parser = argparse.ArgumentParser(description="国际组织报告采集器")
    parser.add_argument("--org", nargs="+", default=None,
                        help="机构名（空格分隔，默认全部 DSpace 机构）")
    parser.add_argument("--keywords", nargs="+", default=None)
    parser.add_argument("--limit", type=int, default=None,
                        help="每机构每关键词限条数（测试用）")
    parser.add_argument("--out-dir", type=str, default=None)
    parser.add_argument("--run-id", type=str, default=None,
                        help="复用已有 run 的报告元数据下载全文（不重新采集）")
    parser.add_argument("--download", action="store_true",
                        help="采集后下载全文（PDF 优先，txt 兜底）")
    parser.add_argument("--include-txt", action="store_true",
                        help="已下 PDF 还额外下载 txt 全文文本（主题建模用）")
    parser.add_argument("--check-orgs", action="store_true",
                        help="探测各机构库可用性，列出可用清单（不采集）")
    args = parser.parse_args()

    collector = ReportCollector(orgs=args.org, out_dir=args.out_dir,
                                run_id=args.run_id)

    # ---- 机构可用性探测（不采集）----
    if args.check_orgs:
        collector.check_orgs()
        return

    # ---- 复用已有 run：加载报告元数据，直接下载全文 ----
    if args.run_id:
        reports = collector.load_run()
        logger.info(f"📂 复用 run {args.run_id}: {len(reports)} 条报告")
        if args.download:
            collector.download_fulltext(reports,
                                        include_txt=args.include_txt)
        else:
            logger.info("   提示: 加 --download 下载全文")
        return

    # ---- 正常采集 ----
    reports = collector.collect_all(keywords=args.keywords, limit=args.limit)
    if reports:
        collector.save(reports)
        if args.download:
            collector.download_fulltext(reports,
                                        include_txt=args.include_txt)
        logger.info(f"✅ 数据目录: {collector.out_dir}")
    else:
        logger.warning("未采集到任何报告（可尝试 --org WHO 或用服务器网络）")


if __name__ == "__main__":
    main()
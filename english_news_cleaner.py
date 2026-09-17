#!/usr/bin/env python3
"""Clean collected English news with DeepSeek and generate db_import.csv."""
import argparse
import csv
import datetime as dt
import logging
import os
import re
from pathlib import Path
from openai import OpenAI

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("english_news_cleaner")
ROOT = Path("data/processed/env_news")
MODEL = "deepseek-chat"
DEEPSEEK_API_KEY = ""
SYSTEM = "Clean each English news article. Return only YAML frontmatter with title, summary, keywords, followed by the cleaned body. Use --- as the frontmatter delimiter. Preserve facts and remove ads/navigation."


def main():
    p = argparse.ArgumentParser(description="英文新闻 LLM 清洗")
    p.add_argument("--run-id", required=True)
    p.add_argument("--limit", type=int)
    p.add_argument("--batch-size", type=int, default=5)
    p.add_argument("--api-key")
    args = p.parse_args()
    key = args.api_key or os.getenv("DEEPSEEK_API_KEY") or DEEPSEEK_API_KEY
    if not key:
        p.error("请填写 --api-key、DEEPSEEK_API_KEY 或脚本顶部 DEEPSEEK_API_KEY")
    run = ROOT / args.run_id
    articles, clean = run / "articles", run / "articles" / "clean"
    csv_path = run / "news.csv"
    if not csv_path.exists():
        p.error(f"未找到 {csv_path}")
    clean.mkdir(parents=True, exist_ok=True)
    with csv_path.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    pending = []
    for row in rows:
        src, dst = articles / f"{row['id']}.md", clean / f"{row['id']}.md"
        if src.exists() and src.stat().st_size > 100 and not (dst.exists() and dst.stat().st_size > 200):
            row["_text"] = src.read_text(encoding="utf-8", errors="ignore")
            pending.append(row)
    if args.limit:
        pending = pending[:args.limit]
    logger.info("待清洗 %s 篇，已跳过 %s 篇", len(pending), len(rows) - len(pending))
    client = OpenAI(api_key=key, base_url="https://api.deepseek.com/v1")
    for start in range(0, len(pending), max(1, args.batch_size)):
        batch = pending[start:start + max(1, args.batch_size)]
        inputs = []
        for n, row in enumerate(batch, 1):
            inputs.append(f"ARTICLE {n}\nTITLE: {row['title']}\nSOURCE: {row['source']}\nDATE: {row['pub_date']}\n\n{row['_text'][:20000]}")
        try:
            response = client.chat.completions.create(model=MODEL, messages=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": "Clean each article separately. Separate outputs with ARTICLE_SEPARATOR.\n\n" + "\n\nARTICLE_INPUT\n".join(inputs)},
            ], temperature=0.1, max_tokens=16000)
            parts = re.split(r"\n?ARTICLE_SEPARATOR\n?", response.choices[0].message.content or "")
            if len(parts) != len(batch):
                raise ValueError(f"返回文章数 {len(parts)} != 请求数 {len(batch)}")
            for row, text in zip(batch, parts):
                if "---" not in text or len(text) < 100:
                    raise ValueError(f"返回内容过短: {row['id']}")
                (clean / f"{row['id']}.md").write_text(text.strip() + "\n", encoding="utf-8")
        except Exception as exc:
            reason = f"{type(exc).__name__}: {str(exc)[:180]}"
            logger.error("批次失败 %s-%s: %s", start + 1, start + len(batch), reason)
            with (run / "fail.log").open("a", encoding="utf-8") as f:
                for row in batch:
                    f.write(f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] step=clean | id={row['id']} | reason={reason}\n")
        logger.info("清洗进度: %s/%s", min(start + len(batch), len(pending)), len(pending))
    generate_csv(run, rows, clean)


def generate_csv(run, rows, clean):
    fields = ["*文件名", "*标题", "描述 / 内容", "*分类", "*类型", "*国家", "地区", "关键词", "发展阶段", "*话语类型", "发布日期", "来源", "原始URL", "文件地址"]
    with (run / "db_import.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields); writer.writeheader(); count = 0
        for row in rows:
            path = clean / f"{row['id']}.md"
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            match = re.search(r"^summary:\s*[\"']?(.*?)[\"']?\s*$", text, re.M)
            writer.writerow({"*文件名": row["id"], "*标题": row["title"], "描述 / 内容": match.group(1).strip() if match else row.get("summary", ""), "*分类": "news", "*类型": "text", "*国家": "global", "地区": "", "关键词": row.get("search_keyword", ""), "发展阶段": "", "*话语类型": "institutional" if str(row.get("is_official")).lower() == "true" else "civilian", "发布日期": row.get("pub_date", ""), "来源": row.get("source", ""), "原始URL": row.get("url", ""), "文件地址": f"articles/clean/{row['id']}.md"})
            count += 1
    logger.info("数据库 CSV 已生成: %s (%s 条)", run / "db_import.csv", count)


if __name__ == "__main__":
    main()

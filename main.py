# ============================================
# 扶贫数据爬虫 - 主入口
# ============================================
"""
用法:
    python main.py --strategy keyword   # 快速采集 (默认，少量)
    python main.py --strategy site      # 站点定向采集
    python main.py --strategy maximize  # 全量采集 (建数据库用)
    python main.py --max-pages 20       # 控制翻页深度
    python main.py --filter official    # 仅保留官方媒体 (默认)
    python main.py --filter all         # 不筛选来源
"""
import argparse
import logging
import sys
from datetime import datetime

from config.settings import LOG_CONFIG, PRIORITY_KEYWORDS
from spiders.gov_spider import GovPovertySpider
from spiders.news_spider import NewsSpider
from spiders.bing_news_spider import BingNewsSpider
from utils.storage import DataStorage
from utils.data_processor import DataProcessor


def setup_logging():
    """配置日志"""
    logging.basicConfig(
        level=getattr(logging, LOG_CONFIG["level"]),
        format=LOG_CONFIG["format"],
        datefmt=LOG_CONFIG["datefmt"],
        handlers=[
            logging.FileHandler(LOG_CONFIG["file"], encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def run_gov_spider(storage: DataStorage, processor: DataProcessor, max_pages: int):
    """运行政府网站爬虫 → 存入 data/*/gov/"""
    logger = logging.getLogger(__name__)
    logger.info("=" * 50)
    logger.info("开始爬取政府扶贫数据...")
    logger.info("=" * 50)

    spider = GovPovertySpider()
    all_articles = []

    try:
        test_urls = [
            "https://www.nrra.gov.cn/col/col3059/index.html",
        ]

        for url in test_urls:
            html = spider.fetch(url)
            if html:
                articles = spider.parse_article_list(html)
                all_articles.extend(articles)

        all_articles = processor.deduplicate(all_articles)
        if all_articles:
            storage.save_csv(all_articles, crawl_type="gov", filename="data.csv")
            storage.save_json(all_articles, crawl_type="gov", filename="data.json")

        logger.info(f"政府数据爬取完成: 共 {len(all_articles)} 条")

    finally:
        spider.close()

    return all_articles, spider


def run_news_spider_keyword(spider: NewsSpider, max_pages: int) -> list:
    """策略1: 按关键词搜索 + 官方媒体过滤"""
    logger = logging.getLogger(__name__)
    logger.info("策略: 关键词搜索 + 官方媒体过滤")

    all_articles = []
    per_kw = max(1, max_pages // len(PRIORITY_KEYWORDS)) + 1

    for kw in PRIORITY_KEYWORDS:
        articles = spider.search_by_keyword(kw, max_pages=per_kw)
        all_articles.extend(articles)
        logger.info(f"  关键词 '{kw}': 累计 {len(all_articles)} 条")

    return all_articles


def run_news_spider_site(spider: NewsSpider, max_pages: int) -> list:
    """策略2: site: 定向搜索官方媒体站点"""
    logger = logging.getLogger(__name__)
    logger.info("策略: site: 定向搜索官方媒体站点")

    top_n = min(max_pages, 8)
    return spider.search_top_official_sites(keyword="扶贫", top_n=top_n)


def run_news_spider(
    storage: DataStorage,
    processor: DataProcessor,
    max_pages: int,
    source_filter: str = "official",
    strategy: str = "keyword",
    years: list = None,
    engine: str = "baidu",
):
    """运行新闻爬虫 → 存入 data/*/news/"""
    logger = logging.getLogger(__name__)
    logger.info("=" * 50)
    logger.info(f"开始爬取扶贫新闻数据")
    logger.info(f"来源过滤: {source_filter} | 搜索策略: {strategy}")
    if years:
        logger.info(f"限定年份: {years}")
    logger.info("=" * 50)

    spider = NewsSpider(source_filter=source_filter) if engine == "baidu" else BingNewsSpider(source_filter=source_filter)
    all_articles = []

    try:
        if strategy == "maximize":
            all_articles = spider.maximize(years=years, max_pages=max_pages)
        elif strategy == "site":
            all_articles = run_news_spider_site(spider, max_pages)
        else:
            all_articles = run_news_spider_keyword(spider, max_pages)

        all_articles = processor.deduplicate(all_articles)
        logger.info(f"去重后: {len(all_articles)} 条")

        # 来源分布
        source_stats = {}
        for a in all_articles:
            src = a.get("source", "未知")
            source_stats[src] = source_stats.get(src, 0) + 1

        if source_stats:
            logger.info("--- 来源分布 ---")
            for src, cnt in sorted(source_stats.items(), key=lambda x: -x[1])[:10]:
                logger.info(f"  {src}: {cnt} 条")

        # 清洗 -> 保存到 data/processed/news/{run_id}/
        if all_articles:
            cleaned = [processor.clean_article(a) for a in all_articles]
            storage.save_csv(cleaned, crawl_type="news", filename="news.csv", to_processed=True)
            storage.save_json(cleaned, crawl_type="news", filename="news.json", to_processed=True)

        logger.info(f"新闻数据爬取完成: 共 {len(all_articles)} 条")

    finally:
        spider.close()

    return all_articles, spider


def main():
    parser = argparse.ArgumentParser(
        description="扶贫数据爬虫",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--source", choices=["gov", "news", "all"], default="all")
    parser.add_argument("--max-pages", type=int, default=5)
    parser.add_argument(
        "--filter", dest="source_filter",
        choices=["official", "all", "commercial"], default="official",
    )
    parser.add_argument(
        "--strategy", choices=["keyword", "site", "maximize"], default="keyword",
        help="采集策略: keyword=快速, site=定向, maximize=全量建库",
    )
    parser.add_argument(
        "--years", nargs="+", type=int,
        help="限定年份，如: --years 2023 或 --years 2020 2021 2022",
    )
    parser.add_argument(
        "--engine", choices=["baidu", "bing"], default="baidu",
        help="搜索引擎: baidu (默认) | bing (不挑IP)",
    )
    args = parser.parse_args()

    setup_logging()
    logger = logging.getLogger(__name__)

    start_time = datetime.now()
    logger.info(f"扶贫数据爬虫启动 @ {start_time}")
    logger.info(
        f"source={args.source} max_pages={args.max_pages} "
        f"filter={args.source_filter} strategy={args.strategy}"
        + (f" years={args.years}" if args.years else "")
        + (f" engine={args.engine}" if args.engine != "baidu" else "")
    )

    storage = DataStorage()
    processor = DataProcessor()
    results = {}
    total_requests = 0
    total_blocks = 0

    # --- 爬取 ---
    if args.source in ("gov", "all"):
        articles, spider = run_gov_spider(storage, processor, args.max_pages)
        results["gov"] = articles
        total_requests += spider.request_count
        total_blocks += spider.block_count

    if args.source in ("news", "all"):
        articles, spider = run_news_spider(
            storage, processor, args.max_pages,
            source_filter=args.source_filter,
            strategy=args.strategy,
            years=args.years,
            engine=args.engine,
        )
        results["news"] = articles
        total_requests += spider.request_count
        total_blocks += spider.block_count

    # --- 生成统计报告 ---
    duration = datetime.now() - start_time
    run_info = {
        "start_time": start_time.strftime("%Y-%m-%d %H:%M:%S"),
        "duration": str(duration).split(".")[0],
        "max_pages": args.max_pages,
        "filter": args.source_filter,
        "strategy": args.strategy,
        "total_requests": total_requests,
        "block_count": total_blocks,
    }
    storage.generate_summary(results, run_info)

    # --- 汇总 ---
    total = sum(len(v) for v in results.values())
    logger.info("=" * 50)
    logger.info(f"全部完成! 共 {total} 条数据 | 耗时 {run_info['duration']}")
    logger.info(f"Run ID: {storage.run_id}")
    logger.info(f"数据目录: data/processed/news/{storage.run_id}/")
    logger.info("=" * 50)


if __name__ == "__main__":
    main()

# ============================================
# 扶贫数据爬虫 - 主入口
# ============================================
"""
用法:
    # 爬取
    python main.py --strategy maximize --engine bing   # 全量采集
    python main.py --strategy keyword --max-pages 5    # 快速测试

    # 下载 & 清洗
    python main.py --download --run-id ID              # 下载正文
    python main.py --clean --run-id ID --limit 5       # LLM 清洗(测试)
    python main.py --clean --run-id ID                 # LLM 清洗(全量)

    # 查看失败日志
    python main.py --show-fails                        # 最新 run 的失败记录
    python main.py --show-fails --run-id ID            # 指定 run 的失败记录

    # 一键流水线
    python main.py --pipeline --strategy maximize --engine bing
"""
import argparse
import logging
import sys
from datetime import datetime

from config.settings import LOG_CONFIG, PRIORITY_KEYWORDS, PROXY
from spiders.gov_spider import GovPovertySpider
from spiders.news_spider import NewsSpider
from spiders.bing_news_spider import BingNewsSpider
from utils.storage import DataStorage
from utils.data_processor import DataProcessor
from utils.resource_mapper import ResourceMapper


def setup_logging(verbose: bool = False):
    """配置日志"""
    level = "DEBUG" if verbose else LOG_CONFIG["level"]
    logging.basicConfig(
        level=getattr(logging, level),
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
    """策略1: 按关键词搜索"""
    logger = logging.getLogger(__name__)
    logger.info("策略: 关键词搜索")

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
    fetch_content: bool = False,
    output_db: bool = False,
    proxy: str = None,
):
    """运行新闻爬虫 → 存入 data/*/news/"""
    logger = logging.getLogger(__name__)
    logger.info("=" * 50)
    logger.info(f"开始爬取扶贫新闻数据")
    logger.info(f"来源过滤: {source_filter} | 搜索策略: {strategy}")
    if years:
        logger.info(f"限定年份: {years}")
    logger.info("=" * 50)

    spider = NewsSpider(source_filter=source_filter) if engine == "baidu" else BingNewsSpider(source_filter=source_filter, proxy=proxy)
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

            # ---- Resource Schema 输出 (供 MongoDB 导入) ----
            if output_db:
                mapper = ResourceMapper()
                resources = mapper.map_batch(all_articles)

                # 可选: 全文内容抓取
                if fetch_content:
                    from utils.content_fetcher import ContentFetcher
                    with ContentFetcher() as fetcher:
                        fetcher.fetch_all(resources)

                storage.save_resource_json(resources, crawl_type="news")
                storage.generate_import_script(crawl_type="news")

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
    parser.add_argument(
        "--verbose", action="store_true",
        help="详细日志模式 (DEBUG级别，可看到每次请求)",
    )
    parser.add_argument(
        "--proxy",
        help="代理地址，如 socks5://127.0.0.1:10809 (国内服务器访问 Bing 需要)",
    )
    parser.add_argument(
        "--fetch-content", action="store_true",
        help="启用全文内容抓取 (从搜索结果URL获取完整正文，较慢)",
    )
    parser.add_argument(
        "--output-db", action="store_true",
        help="输出 MongoDB 导入格式 (resource.json + import.mongosh.js)",
    )
    parser.add_argument(
        "--download", action="store_true",
        help="下载已爬取新闻正文为 .md 文件 (不运行爬虫)",
    )
    parser.add_argument(
        "--clean", action="store_true",
        help="LLM 清洗已下载的 .md 文章 (需设置 DEEPSEEK_API_KEY)",
    )
    parser.add_argument(
        "--gen-csv", action="store_true",
        help="仅从已清洗的 .md 生成 db_import.csv (不需要 LLM)",
    )
    parser.add_argument(
        "--run-id", type=str, default=None,
        help="指定 run_id (--download / --clean 模式)",
    )
    parser.add_argument(
        "--delay", type=float, default=2.0,
        help="请求间隔秒数 (--download / --clean 模式)",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="限制处理数量 (--download / --clean 模式测试用)",
    )
    parser.add_argument(
        "--workers", type=int, default=4,
        help="--clean 模式的并发批次数，默认 4（太高可能触发 API 限流）",
    )
    parser.add_argument(
        "--pipeline", action="store_true",
        help="一键完成: 爬取 → 下载 → LLM清洗",
    )
    parser.add_argument(
        "--show-fails", action="store_true",
        help="查看失败日志 (最新或指定 --run-id 的 fail.log)",
    )
    args = parser.parse_args()

    setup_logging(verbose=args.verbose)
    logger = logging.getLogger(__name__)

    # ---- 下载模式: 不运行爬虫，只下载已爬取文章的正文 ----
    if args.download:
        from utils.news_md_downloader import NewsMarkdownDownloader
        try:
            with NewsMarkdownDownloader(
                run_id=args.run_id,
                delay=args.delay,
            ) as downloader:
                downloader.download_all(limit=args.limit)
        except FileNotFoundError as e:
            logger.error(str(e))
            sys.exit(1)
        return

    # ---- LLM 清洗模式: 清洗已下载的 .md 文章 ----
    if args.clean:
        from utils.llm_cleaner import LLMCleaner
        try:
            with LLMCleaner(run_id=args.run_id, max_workers=args.workers) as cleaner:
                cleaner.clean_all(limit=args.limit)
        except FileNotFoundError as e:
            logger.error(str(e))
            sys.exit(1)
        except ValueError as e:
            logger.error(str(e))
            sys.exit(1)
        return

    # ---- 仅生成 db_import.csv（不需要 LLM） ----
    if args.gen_csv:
        from utils.llm_cleaner import LLMCleaner
        try:
            with LLMCleaner(run_id=args.run_id) as cleaner:
                cleaner.generate_csv()
        except FileNotFoundError as e:
            logger.error(str(e))
            sys.exit(1)
        return

    # ---- 查看失败日志 ----
    if args.show_fails:
        from pathlib import Path
        news_dir = Path("data/processed/news")
        if args.run_id:
            fail_path = news_dir / args.run_id / "fail.log"
        else:
            # 自动找最新 run
            if not news_dir.exists() or not list(news_dir.iterdir()):
                logger.error("未找到任何数据目录，请先运行爬虫")
                sys.exit(1)
            latest = sorted(
                [d for d in news_dir.iterdir() if d.is_dir()],
                reverse=True,
            )[0]
            fail_path = latest / "fail.log"

        if not fail_path.exists():
            logger.info(f"📄 无失败记录: {fail_path}")
            return

        content = fail_path.read_text(encoding="utf-8")
        # 统计
        download_fails = content.count("step=download")
        clean_fails = content.count("step=clean")
        logger.info(f"📄 失败日志: {fail_path}")
        logger.info(f"   下载失败: {download_fails} 条 | 清洗失败: {clean_fails} 条")
        logger.info("=" * 60)
        # 只打印失败条目（跳过运行头分隔线）
        for line in content.split("\n"):
            if line.startswith("[") and "step=" in line:
                print(line)
        logger.info("=" * 60)
        return

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
        # 代理优先级: CLI参数 > 配置文件 > 环境变量 HTTPS_PROXY
        proxy = args.proxy or PROXY
        articles, spider = run_news_spider(
            storage, processor, args.max_pages,
            source_filter=args.source_filter,
            strategy=args.strategy,
            years=args.years,
            engine=args.engine,
            fetch_content=args.fetch_content,
            output_db=args.output_db,
            proxy=proxy,
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

    # ---- 流水线: 爬取 → 下载 → 清洗 ----
    if args.pipeline and total > 0:
        run_id = storage.run_id
        logger.info("")
        logger.info("=" * 50)
        logger.info("🔗 流水线模式: 自动执行 下载 → LLM清洗")
        logger.info(f"   Run ID: {run_id}")
        logger.info("=" * 50)

        # ① 下载
        from utils.news_md_downloader import NewsMarkdownDownloader
        try:
            with NewsMarkdownDownloader(
                run_id=run_id, delay=args.delay
            ) as downloader:
                downloader.download_all(limit=args.limit)
        except Exception as e:
            logger.error(f"下载步骤失败: {e}")

        # ② LLM 清洗
        from utils.llm_cleaner import LLMCleaner
        try:
            with LLMCleaner(run_id=run_id, max_workers=args.workers) as cleaner:
                cleaner.clean_all(limit=args.limit)
        except ValueError as e:
            logger.warning(f"跳过清洗步骤: {e}")
        except Exception as e:
            logger.error(f"清洗步骤失败: {e}")

        logger.info("=" * 50)
        logger.info(f"🎉 流水线全部完成! 数据目录: data/processed/news/{run_id}/")
        logger.info("=" * 50)


if __name__ == "__main__":
    main()

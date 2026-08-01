# ============================================
# 扶贫数据爬虫 - 配置文件
# ============================================

# ============================================================
# 中国媒体分类
# ============================================================

# 官方媒体（党媒、央媒、国新办主管媒体）
OFFICIAL_MEDIA = {
    # --- 中央级新闻通讯社 ---
    "xinhua": {
        "name": "新华网",
        "domain": "xinhuanet.com",
        "alias": ["新华社", "新华每日电讯", "Xinhua"],
        "level": "中央",
    },
    "people": {
        "name": "人民网",
        "domain": "people.com.cn",
        "alias": ["人民日报", "人民日报海外版"],
        "level": "中央",
    },
    "cctv": {
        "name": "央视网",
        "domain": "cctv.com",
        "alias": ["中央电视台", "央视新闻", "CCTV"],
        "level": "中央",
    },
    "chinanews": {
        "name": "中国新闻网",
        "domain": "chinanews.com.cn",
        "alias": ["中国新闻社", "中新社"],
        "level": "中央",
    },
    "china_daily": {
        "name": "中国日报网",
        "domain": "chinadaily.com.cn",
        "alias": ["中国日报", "China Daily"],
        "level": "中央",
    },

    # --- 中央级报刊/网站 ---
    "gmw": {
        "name": "光明网",
        "domain": "gmw.cn",
        "alias": ["光明日报"],
        "level": "中央",
    },
    "ce": {
        "name": "中国经济网",
        "domain": "ce.cn",
        "alias": ["经济日报"],
        "level": "中央",
    },
    "youth": {
        "name": "中国青年网",
        "domain": "youth.cn",
        "alias": ["中国青年报"],
        "level": "中央",
    },
    "china_net": {
        "name": "中国网",
        "domain": "china.com.cn",
        "alias": ["中国互联网新闻中心"],
        "level": "中央",
    },
    "huanqiu": {
        "name": "环球网",
        "domain": "huanqiu.com",
        "alias": ["环球时报"],
        "level": "中央",
    },
    "qstheory": {
        "name": "求是网",
        "domain": "qstheory.cn",
        "alias": ["求是", "求是杂志"],
        "level": "中央",
    },
    "81cn": {
        "name": "中国军网",
        "domain": "81.cn",
        "alias": ["解放军报", "解放军新闻传播中心"],
        "level": "中央",
    },

    # --- 政府/官方机构网站 ---
    "gov": {
        "name": "中国政府网",
        "domain": "gov.cn",
        "alias": ["国务院", "中央人民政府"],
        "level": "中央",
    },
    "cppcc": {
        "name": "人民政协网",
        "domain": "cppcc.gov.cn",
        "alias": ["中国人民政治协商会议全国委员会", "全国政协"],
        "level": "中央",
    },
    "ccdi": {
        "name": "中央纪委国家监委网站",
        "domain": "ccdi.gov.cn",
        "alias": ["中央纪委", "国家监委"],
        "level": "中央",
    },
    "ndrc": {
        "name": "国家发展和改革委员会",
        "domain": "ndrc.gov.cn",
        "alias": ["国家发改委"],
        "level": "中央",
    },

    # --- 地方党媒（示例）---
    "beijing": {
        "name": "北京日报",
        "domain": "bjd.com.cn",
        "alias": ["京报网"],
        "level": "省级",
    },
    "shanghai": {
        "name": "上观新闻",
        "domain": "shobserver.com",
        "alias": ["解放日报", "上海观察"],
        "level": "省级",
    },
    "southcn": {
        "name": "南方网",
        "domain": "southcn.com",
        "alias": ["南方日报"],
        "level": "省级",
    },
}

# 民间/商业媒体
COMMERCIAL_MEDIA = {
    "sina": {"name": "新浪新闻", "domain": "sina.com.cn"},
    "sohu": {"name": "搜狐新闻", "domain": "sohu.com"},
    "netease": {"name": "网易新闻", "domain": "163.com"},
    "qq": {"name": "腾讯新闻", "domain": "qq.com"},
    "ifeng": {"name": "凤凰网", "domain": "ifeng.com"},
    "thepaper": {"name": "澎湃新闻", "domain": "thepaper.cn"},
    "bjnews": {"name": "新京报", "domain": "bjnews.com.cn"},
    "infzm": {"name": "南方周末", "domain": "infzm.com"},
    "caixin": {"name": "财新网", "domain": "caixin.com"},
    "jiemian": {"name": "界面新闻", "domain": "jiemian.com"},
    "guancha": {"name": "观察者网", "domain": "guancha.cn"},
    "toutiao": {"name": "今日头条", "domain": "toutiao.com"},
    "bjd": {"name": "北京日报", "domain": "bjnews.com.cn"},
}

# 构建快速查找映射: source_name -> media_info
OFFICIAL_NAME_MAP = {}
for key, info in OFFICIAL_MEDIA.items():
    OFFICIAL_NAME_MAP[info["name"]] = info
    for alias in info.get("alias", []):
        OFFICIAL_NAME_MAP[alias] = info

# ============================================================
# 爬取规则
# ============================================================
CRAWL_CONFIG = {
    "request_delay": 5,          # 请求间隔（秒），百度反爬严格
    "max_retries": 3,
    "timeout": 30,
    "max_pages": 50,
    "concurrent_requests": 1,
}

# ============================================================
# 请求头
# ============================================================
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}

# ============================================================
# 数据存储
# ============================================================
STORAGE_CONFIG = {
    "raw_data_dir": "data/raw",
    "processed_data_dir": "data/processed",
    "log_dir": "logs",
    "csv_encoding": "utf-8-sig",
}

# ============================================================
# 日志配置
# ============================================================
LOG_CONFIG = {
    "level": "INFO",
    "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    "datefmt": "%Y-%m-%d %H:%M:%S",
    "file": "logs/crawler.log",
}

# ============================================================
# 关键词配置
# ============================================================
KEYWORDS = [
    "扶贫", "脱贫攻坚", "精准扶贫", "乡村振兴",
    "贫困县", "贫困村", "建档立卡", "对口帮扶",
    "产业扶贫", "教育扶贫", "健康扶贫", "易地搬迁",
    "两不愁三保障", "摘帽", "防止返贫",
]

# 搜索时使用的高优先级关键词
PRIORITY_KEYWORDS = [
    "脱贫攻坚",
    "精准扶贫",
    "乡村振兴",
    "扶贫",
]

# ============================================================
# 百度新闻搜索配置
# ============================================================
BAIDU_NEWS_CONFIG = {
    "base_url": "https://news.baidu.com/ns",
    "results_per_page": 20,          # 每页结果数
    "max_pages_per_keyword": 3,      # 每个关键词最大翻页数
    "source_filter": "official",     # 来源过滤: "official" | "all" | "commercial"
}

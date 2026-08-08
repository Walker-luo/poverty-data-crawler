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

    # --- 中央级广播/电视/报刊 ---
    "cnr": {
        "name": "央广网",
        "domain": "cnr.cn",
        "alias": ["中央人民广播电台", "央广", "中国之声"],
        "level": "中央",
    },
    "farmer": {
        "name": "中国农网",
        "domain": "farmer.com.cn",
        "alias": ["农民日报", "中国农业新闻网"],
        "level": "中央",
    },
    "legaldaily": {
        "name": "法制网",
        "domain": "legaldaily.com.cn",
        "alias": ["法制日报", "法治日报", "法治网"],
        "level": "中央",
    },
    "cnstock": {
        "name": "中国证券网",
        "domain": "cnstock.com",
        "alias": ["中国证券报", "上海证券报"],
        "level": "中央",
    },
    "jjckb": {
        "name": "经济参考报",
        "domain": "jjckb.cn",
        "alias": ["经济参考网"],
        "level": "中央",
    },
    "ceh": {
        "name": "中国经济导报",
        "domain": "ceh.com.cn",
        "alias": ["中国发展网"],
        "level": "中央",
    },
    "jyb": {
        "name": "中国教育新闻网",
        "domain": "jyb.cn",
        "alias": ["中国教育报", "中国教育在线"],
        "level": "中央",
    },
    "cqn": {
        "name": "中国质量新闻网",
        "domain": "cqn.com.cn",
        "alias": ["中国质量报"],
        "level": "中央",
    },
    "cyol": {
        "name": "中青在线",
        "domain": "cyol.com",
        "alias": ["中国青年报", "青年参考"],
        "level": "中央",
    },

    # --- 地方党媒 ---
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
        "alias": ["南方日报", "南方+"],
        "level": "省级",
    },
    "gscn": {
        "name": "中国甘肃网",
        "domain": "gscn.com.cn",
        "alias": ["甘肃网", "每日甘肃网"],
        "level": "省级",
    },
    "voc": {
        "name": "华声在线",
        "domain": "voc.com.cn",
        "alias": ["湖南日报", "新湖南"],
        "level": "省级",
    },
    "dzwww": {
        "name": "大众网",
        "domain": "dzwww.com",
        "alias": ["大众日报", "海报新闻", "齐鲁晚报"],
        "level": "省级",
    },
    "yunnan": {
        "name": "云南网",
        "domain": "yunnan.cn",
        "alias": ["云南日报", "云报"],
        "level": "省级",
    },
    "fjsen": {
        "name": "东南网",
        "domain": "fjsen.com",
        "alias": ["福建日报"],
        "level": "省级",
    },
    "scol": {
        "name": "四川在线",
        "domain": "scol.com.cn",
        "alias": ["四川日报", "川观新闻"],
        "level": "省级",
    },
    "cnhubei": {
        "name": "荆楚网",
        "domain": "cnhubei.com",
        "alias": ["湖北日报"],
        "level": "省级",
    },
    "hinews": {
        "name": "南海网",
        "domain": "hinews.cn",
        "alias": ["海南日报"],
        "level": "省级",
    },
    "sznews": {
        "name": "深圳新闻网",
        "domain": "sznews.com",
        "alias": ["深圳特区报", "深圳商报"],
        "level": "省级",
    },
    "iqilu": {
        "name": "齐鲁网",
        "domain": "iqilu.com",
        "alias": ["山东广播电视台"],
        "level": "省级",
    },
    "jxnews": {
        "name": "大江网",
        "domain": "jxnews.com.cn",
        "alias": ["江西日报", "中国江西网"],
        "level": "省级",
    },
    "zjol": {
        "name": "浙江在线",
        "domain": "zjol.com.cn",
        "alias": ["浙江日报"],
        "level": "省级",
    },
    "cnwest": {
        "name": "西部网",
        "domain": "cnwest.com",
        "alias": ["陕西日报", "陕西新闻网"],
        "level": "省级",
    },
    "jschina": {
        "name": "中国江苏网",
        "domain": "jschina.com.cn",
        "alias": ["新华日报"],
        "level": "省级",
    },
    "gxnews": {
        "name": "广西新闻网",
        "domain": "gxnews.com.cn",
        "alias": ["广西日报"],
        "level": "省级",
    },
    "qianlong": {
        "name": "千龙网",
        "domain": "qianlong.com",
        "alias": ["北京千龙"],
        "level": "省级",
    },
    "chinatibetnews": {
        "name": "中国西藏新闻网",
        "domain": "chinatibetnews.com",
        "alias": ["西藏日报", "西藏新闻网"],
        "level": "省级",
    },
    "dbw": {
        "name": "东北网",
        "domain": "dbw.cn",
        "alias": ["黑龙江日报", "东北新闻网"],
        "level": "省级",
    },
    "cnnb": {
        "name": "中国宁波网",
        "domain": "cnnb.com.cn",
        "alias": ["宁波日报"],
        "level": "省级",
    },
    "ynet": {
        "name": "北青网",
        "domain": "ynet.com",
        "alias": ["北京青年报"],
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
# 代理配置 (中国大陆服务器访问 Bing 需要)
# ============================================================
# 格式: "http://host:port" 或 "socks5://host:port"
# 也可通过环境变量 HTTPS_PROXY 设置
PROXY = "http://127.0.0.1:10809"

# ============================================================
# 百度新闻搜索配置
# ============================================================
BAIDU_NEWS_CONFIG = {
    "base_url": "https://news.baidu.com/ns",
    "results_per_page": 20,          # 每页结果数
    "max_pages_per_keyword": 3,      # 每个关键词最大翻页数
    "source_filter": "official",     # 来源过滤: "official" | "all" | "commercial"
}

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
中国半导体技术突破新闻监控 (China Semiconductor Breakthrough Tracker)

覆盖领域：
  1. 光刻机（整机/系统）
  2. 光刻机电源与光源
  3. 机台（工件台/精密运动台）
  4. 镜头（光学系统/物镜）
  5. 光刻胶与电子材料
  6. 光通讯（光模块/光芯片/硅光）
  7. 逻辑芯片先进制程
  8. 存储芯片先进制程

数据流：多源抓取 -> 领域分类 -> 标题去重 -> 累积历史 -> 生成自包含看板 index.html
运行环境：Windows 本地 / GitHub Actions (ubuntu) 均可，纯标准库 + feedparser。
"""

import csv
import hashlib
import io
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from html import unescape
from pathlib import Path

try:
    import feedparser
except ImportError:  # 允许缺失，RSS 走内置解析回退
    feedparser = None

# ---------------------------------------------------------------- 基础配置
ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DATA_FILE = DATA_DIR / "news.json"
CSV_FILE = DATA_DIR / "news.csv"
HTML_FILE = ROOT / "index.html"
LOG_FILE = ROOT / "scrape.log"

BJ = timezone(timedelta(hours=8))
TODAY = datetime.now(BJ).strftime("%Y-%m-%d")
NOW_STR = datetime.now(BJ).strftime("%Y-%m-%d %H:%M:%S")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

MAX_HISTORY_DAYS = 120          # 历史保留天数
MAX_ITEMS_PER_SOURCE = 60       # 单源单次最多取多少条

# ---------------------------------------------------------------- 领域与关键词

# 顺序即平局优先级（越靠前越"具体"，优先归类）
DOMAINS = [
    {
        "key": "photoresist",
        "name": "光刻胶与电子材料",
        "color": "#7c3aed",
        "kw": [
            "光刻胶", "光阻", "光引发剂", "光刻胶树脂", "感光树脂", "KrF胶", "ArF胶",
            "EUV光刻胶", "光刻胶国产", "南大光电", "彤程新材", "晶瑞电材", "晶瑞股份",
            "徐州博康", "上海新阳", "容大感光", "强力新材", "广信材料", "飞凯材料",
            "电子特气", "特种气体", "CMP抛光", "抛光液", "抛光垫", "前驱体",
            "湿电子化学品", "电子材料", "雅克科技", "鼎龙股份", "安集科技", "江化微",
            "光掩膜", "掩膜版", "石英掩膜",
        ],
    },
    {
        "key": "litho_power",
        "name": "光刻机电源/光源",
        "color": "#dc2626",
        "kw": [
            "光刻光源", "准分子激光", "激光光源", "科益虹源", "炬光科技", "氟化氩",
            "ArF光源", "KrF光源", "光刻机电源", "光源系统", "相干光源", "大功率激光",
            "脉冲电源", "射频电源", "高压电源", "激光器", "固体激光", "光纤激光",
            "深紫外光源", "DUV光源", "EUV光源", "锡液滴", "激光等离子体",
        ],
    },
    {
        "key": "stage",
        "name": "机台/工件台",
        "color": "#ea580c",
        "kw": [
            "双工件台", "工件台", "晶圆台", "精密运动台", "华卓精科", "纳米级运动台",
            "运动平台", "工件台系统", "机台", "直写光刻", "步进扫描", "磁悬浮平台",
            "精密位移", "超精密平台", "纳米位移台", "气浮平台", "直线电机",
            "音圈电机", "纳米定位", "精密定位", "光栅尺", "精密导轨", "超精密运动",
        ],
    },
    {
        "key": "optics",
        "name": "镜头/光学系统",
        "color": "#0891b2",
        "kw": [
            "光刻物镜", "投影物镜", "光学镜头", "镜头", "光学系统", "国望光学",
            "长春光机", "长光所", "反射镜", "光学元件", "透镜", "茂莱光学",
            "非球面", "光学模组", "镜片", "镀膜", "多层膜", "极紫外光学",
            "福光股份", "永新光学", "腾景科技", "波长光电", "蓝特光学", "光刻镜头",
            "物镜系统", "光学镜片", "衍射光学", "超表面", "精密光学", "镜组",
        ],
    },
    {
        "key": "litho_machine",
        "name": "光刻机整机",
        "color": "#b91c1c",
        "kw": [
            "光刻机", "光刻设备", "光刻系统", "光刻装备", "EUV", "极紫外光刻",
            "DUV", "浸没式光刻", "immersion", "上海微电子", "SMEE", "宇量昇",
            "套刻精度", "曝光机", "步进机", "光刻技术", "光刻工艺", "光刻胶产线",
            "光刻机国产", "先进光刻", "纳米压印", "电子束光刻", "直写",
        ],
    },
    {
        "key": "memory",
        "name": "存储芯片先进制程",
        "color": "#059669",
        "kw": [
            "存储芯片", "DRAM", "NAND", "3D NAND", "HBM", "长江存储", "长鑫存储",
            "长鑫", "兆易创新", "闪存芯片", "内存芯片", "Xtacking", "堆叠层数",
            "232层", "294层", "300层", "HBM3", "HBM4", "DDR5", "LPDDR5",
            "存储颗粒", "利基存储", "NOR Flash", "存算一体", "存储先进制程",
        ],
    },
    {
        "key": "logic",
        "name": "逻辑芯片先进制程",
        "color": "#2563eb",
        "kw": [
            "先进制程", "先进工艺", "FinFET", "GAA", "环栅", "纳米片", "中芯国际",
            "华虹", "晶圆代工", "逻辑芯片", "麒麟", "昇腾", "海思", "5nm", "3nm",
            "2nm", "7nm", "14nm", "N+1", "N+2", "先进逻辑", "芯片制程", "光刻工艺节点",
            "国产CPU", "国产GPU", "AI芯片", "算力芯片", "玄戒", "龙芯", "飞腾",
        ],
    },
    {
        "key": "optical_comm",
        "name": "光通讯/光模块",
        "color": "#0d9488",
        "kw": [
            "光模块", "光通信", "光通讯", "硅光", "光芯片", "EML", "DFB", "VCSEL",
            "CPO", "800G", "1.6T", "400G", "光互连", "光互联", "光电共封装",
            "中际旭创", "新易盛", "源杰科技", "光迅科技", "长光华芯", "仕佳光子",
            "天孚通信", "太辰光", "光器件", "光电子", "光引擎", "薄膜铌酸锂",
            "相干光", "光收发", "OTN", "波分复用",
        ],
    },
]

DOMAIN_BY_KEY = {d["key"]: d for d in DOMAINS}

# 重点跟踪企业：标题中出现即打标签（按长度降序，避免短名抢占）
COMPANIES = sorted(set([
    # 光刻机整机 / 核心部件
    "上海微电子", "SMEE", "宇量昇", "华卓精科", "科益虹源", "炬光科技", "福晶科技",
    "国望光学", "长春光机", "茂莱光学", "福光股份", "永新光学", "腾景科技",
    "波长光电", "蓝特光学", "启尔机电", "苏大维格", "英诺激光", "大族激光",
    # 光刻胶 / 电子材料
    "南大光电", "彤程新材", "晶瑞电材", "晶瑞股份", "徐州博康", "上海新阳",
    "容大感光", "强力新材", "广信材料", "飞凯材料", "雅克科技", "鼎龙股份",
    "安集科技", "江化微", "华特气体", "金宏气体", "中巨芯", "艾森股份",
    # 逻辑 / 代工 / 设计
    "中芯国际", "华虹", "海思", "华为", "中微公司", "北方华创", "拓荆科技",
    "盛美上海", "华海清科", "芯源微", "精测电子", "长川科技", "万业企业",
    "寒武纪", "壁仞科技", "摩尔线程", "燧原科技", "天数智芯", "龙芯中科",
    "飞腾", "海光信息", "兆芯", "平头哥", "紫光展锐", "沐曦",
    # 存储
    "长江存储", "长鑫存储", "兆易创新", "佰维存储", "江波龙", "德明利",
    "东芯股份", "普冉股份", "聚辰股份", "澜起科技",
    # 光通讯 / 光模块 / 光芯片
    "中际旭创", "新易盛", "源杰科技", "光迅科技", "长光华芯", "仕佳光子",
    "天孚通信", "太辰光", "博创科技", "德科立", "华工科技", "剑桥科技",
    "铭普光磁", "光库科技", "铖昌科技", "联特科技",
]), key=len, reverse=True)


def extract_companies(title: str):
    hits = []
    for c in COMPANIES:
        if c in title:
            hits.append(c)
            if len(hits) >= 3:
                break
    return hits

# 「技术突破」信号词
BREAKTHROUGH_POS = [
    "突破", "量产", "下线", "交付", "攻克", "验证", "通过验证", "研制成功", "自主研制",
    "首发", "首次", "首台", "流片", "点亮", "中试", "送样", "导入", "认证", "填补空白",
    "打破垄断", "国产替代", "良率", "自研", "技术攻关", "取得进展", "实现", "问世",
    "投产", "扩产", "出货", "上量", "商用", "通过验收", "验收", "新品发布", "面世",
    "成功研发", "研发成功", "自主可控", "国产化", "新突破", "里程碑", "领先",
]
BREAKTHROUGH_NEG = [
    "股价", "涨停", "跌停", "增持", "减持", "融资", "财报", "业绩预告", "营收",
    "净利润", "人事", "任免", "离职", "诉讼", "签约仪式", "回购", "解禁", "估值",
    "市值", "开盘", "收盘", "涨幅", "跌幅", "机构调研",
]


# 非技术类噪音：股票行情 / 公司公告，避免污染「技术突破」监控
STRONG_NOISE = [
    "融资融券", "融资买入", "融资余额", "基金重仓", "重仓该股", "北向资金",
    "主力资金", "龙虎榜", "成交额", "市盈率", "股价", "涨停", "跌停",
    "减持", "回购", "解禁", "限售股", "提供担保", "担保的进展", "股东大会",
    "董事会决议", "监事会", "机构调研", "业绩说明会", "问询函", "关注函",
    "交易异常波动", "投资者关系",
]


def is_noise(title: str) -> bool:
    if re.search(r"(涨|跌)\s?-?\d", title):
        return True
    if re.search(r"[涨跌]幅|收盘|开盘|成交额|指数报", title):
        return True
    return any(w in title for w in STRONG_NOISE)


def breakthrough_score(title: str) -> int:
    s = 0
    for w in BREAKTHROUGH_POS:
        if w in title:
            s += 2
    for w in BREAKTHROUGH_NEG:
        if w in title:
            s -= 3
    return s


def classify(title: str):
    """返回 (domain_key, score)；score==0 表示未命中任何领域。"""
    best_key, best_score = None, 0
    for d in DOMAINS:
        sc = 0
        for kw in d["kw"]:
            if kw in title:
                sc += 1 + (0.5 if len(kw) >= 5 else 0)
        if sc > best_score:
            best_score, best_key = sc, d["key"]
    return best_key, best_score


# ---------------------------------------------------------------- HTTP 工具
def http_get(url: str, timeout: int = 20, retries: int = 2) -> str:
    last = None
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            })
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as r:
                raw = r.read()
            for enc in ("utf-8", "gb18030", "gbk", "latin-1"):
                try:
                    return raw.decode(enc)
                except UnicodeDecodeError:
                    continue
            return raw.decode("utf-8", "ignore")
        except Exception as e:  # noqa: BLE001
            last = e
            if i < retries:
                time.sleep(1.5 * (i + 1))
    raise last  # type: ignore[misc]


def clean_text(s: str) -> str:
    s = unescape(s or "")
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


# ---------------------------------------------------------------- 各源解析器
def parse_html_list(url: str, pattern: str, base: str, name: str):
    """通用 HTML 列表页解析：正则抓 (相对/绝对链接, 标题)。"""
    out = []
    try:
        html = http_get(url)
    except Exception as e:  # noqa: BLE001
        log(f"[{name}] 抓取失败: {type(e).__name__} {e}")
        return out
    seen = set()
    for m in re.finditer(pattern, html):
        href, title = m.group(1), clean_text(m.group(2))
        if len(title) < 8 or not re.search(r"[\u4e00-\u9fff]", title):
            continue
        if href.startswith("http"):
            link = href
        else:
            link = urllib.parse.urljoin(base, href)
        if link in seen:
            continue
        seen.add(link)
        out.append({"title": title, "url": link, "source": name, "published": ""})
        if len(out) >= MAX_ITEMS_PER_SOURCE:
            break
    return out


def parse_rss(url: str, name: str):
    out = []
    try:
        raw = http_get(url)
    except Exception as e:  # noqa: BLE001
        log(f"[{name}] RSS 抓取失败: {type(e).__name__} {e}")
        return out
    if feedparser is not None:
        feed = feedparser.parse(raw)
        for e in feed.entries[:MAX_ITEMS_PER_SOURCE]:
            title = clean_text(e.get("title", ""))
            link = e.get("link", "")
            desc = clean_text(e.get("summary", "") or e.get("description", ""))[:160]
            pub = ""
            if e.get("published_parsed"):
                try:
                    pub = datetime(*e.published_parsed[:6], tzinfo=timezone.utc).astimezone(BJ).strftime("%Y-%m-%d")
                except Exception:  # noqa: BLE001
                    pub = ""
            if len(title) >= 8:
                out.append({"title": title, "url": link, "source": name,
                            "published": pub, "summary": desc})
        return out
    # 回退：正则解析 RSS
    for m in re.finditer(r"<item>(.*?)</item>", raw, re.S):
        blk = m.group(1)
        t = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", blk, re.S)
        l = re.search(r"<link>(.*?)</link>", blk, re.S)
        d = re.search(r"<pubDate>(.*?)</pubDate>", blk, re.S)
        title = clean_text(t.group(1)) if t else ""
        link = clean_text(l.group(1)) if l else ""
        pub = ""
        if d:
            try:
                pub = datetime.strptime(d.group(1).strip()[:25], "%a, %d %b %Y %H:%M:%S").strftime("%Y-%m-%d")
            except Exception:  # noqa: BLE001
                pub = ""
        if len(title) >= 8:
            out.append({"title": title, "url": link, "source": name, "published": pub})
        if len(out) >= MAX_ITEMS_PER_SOURCE:
            break
    return out


GNEWS_BASE = "https://news.google.com/rss/search?q={q}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"

GNEWS_QUERIES = [
    "光刻机 国产 突破",
    "上海微电子",
    "EUV 光刻 中国",
    "国产光刻机 交付",
    "光刻机 光源",
    "科益虹源",
    "华卓精科",
    "双工件台",
    "工件台 光刻",
    "光刻物镜",
    "国望光学",
    "茂莱光学",
    "光刻胶 国产 量产",
    "光刻胶 突破",
    "光模块 800G",
    "硅光芯片",
    "1.6T 光模块",
    "中芯国际 先进制程",
    "长江存储",
    "长鑫存储",
    "HBM 国产",
    "国产半导体设备 突破",
]


def parse_gnews(name: str = "Google News"):
    out, seen = [], set()
    for i, q in enumerate(GNEWS_QUERIES):
        if i:
            time.sleep(0.6)
        url = GNEWS_BASE.format(q=urllib.parse.quote(q + " when:20d"))
        try:
            raw = http_get(url, timeout=25)
        except Exception as e:  # noqa: BLE001
            log(f"[{name}] 查询「{q}」失败: {type(e).__name__}")
            continue
        for m in re.finditer(r"<item>(.*?)</item>", raw, re.S):
            blk = m.group(1)
            t = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", blk, re.S)
            l = re.search(r"<link>(.*?)</link>", blk, re.S)
            d = re.search(r"<pubDate>(.*?)</pubDate>", blk, re.S)
            s = re.search(r'<source url="[^"]*">(.*?)</source>', blk, re.S)
            if not t:
                continue
            title = clean_text(t.group(1))
            src_name = clean_text(s.group(1)) if s else ""
            # Google News 标题形如 "标题 - 媒体名"，剥离尾部媒体名
            title = re.sub(r"\s*-\s*[^-]{2,20}$", "", title) if src_name and title.endswith(src_name) else title
            pub = ""
            if d:
                try:
                    pub = datetime.strptime(d.group(1).strip()[:25], "%a, %d %b %Y %H:%M:%S").strftime("%Y-%m-%d")
                except Exception:  # noqa: BLE001
                    pub = ""
            if len(title) < 8:
                continue
            fp = hashlib.md5(title.encode("utf-8")).hexdigest()[:12]
            if fp in seen:
                continue
            seen.add(fp)
            out.append({
                "title": title,
                "url": clean_text(l.group(1)) if l else "",
                "source": f"GoogleNews·{src_name}" if src_name else name,
                "published": pub,
            })
    return out


# ---------------------------------------------------------------- 源清单
SOURCES = [
    {"name": "集微网", "fn": lambda: parse_html_list(
        "https://www.laoyaoba.com/",
        r'href="(/n/\d+)"[^>]*>\s*([^<]{8,160}?)\s*<',
        "https://www.laoyaoba.com", "集微网")},
    {"name": "闪存市场", "fn": lambda: parse_html_list(
        "https://www.chinaflashmarket.com/",
        r'href="(/a/\d+)"[^>]*>\s*([^<]{8,160}?)\s*<',
        "https://www.chinaflashmarket.com", "闪存市场")},
    {"name": "芯东西", "fn": lambda: parse_html_list(
        "https://zhidx.com/",
        r'href="(https?://zhidx\.com/p/\d+\.html|/p/\d+\.html)"[^>]*>\s*([^<]{8,160}?)\s*<',
        "https://zhidx.com", "芯东西")},
    {"name": "电子发烧友", "fn": lambda: parse_html_list(
        "https://www.elecfans.com/news/",
        r'href="(https?://www\.elecfans\.com/d/\d+\.html|/d/\d+\.html)"[^>]*>\s*([^<]{8,160}?)\s*<',
        "https://www.elecfans.com", "电子发烧友")},
    {"name": "IT之家", "fn": lambda: parse_rss("https://www.ithome.com/rss/", "IT之家")},
    {"name": "cnBeta", "fn": lambda: parse_rss("https://www.cnbeta.com.tw/backend.php", "cnBeta")},
    {"name": "Google News", "fn": lambda: parse_gnews()},
]


# ---------------------------------------------------------------- 存储
def load_history():
    if DATA_FILE.exists():
        try:
            d = json.loads(DATA_FILE.read_text(encoding="utf-8"))
            return d.get("items", [])
        except Exception as e:  # noqa: BLE001
            log(f"读取历史失败: {e}")
    return []


def dedup_key(title: str) -> str:
    t = re.sub(r"[\s\-—_|·,，。.、:：!！?？\"'“”‘’()（）\[\]【】]+", "", title)
    t = re.sub(r"[A-Za-z0-9]+", lambda m: m.group(0).lower(), t)
    return hashlib.sha1(t.encode("utf-8")).hexdigest()


def merge(history, fresh):
    idx = {it["id"]: it for it in history}
    added = 0
    for it in fresh:
        title = it["title"]
        key = dedup_key(title)
        if key in idx:
            cur = idx[key]
            cur["last_seen"] = TODAY
            # 补全信息
            if not cur.get("url") and it.get("url"):
                cur["url"] = it["url"]
            if not cur.get("published") and it.get("published"):
                cur["published"] = it["published"]
            continue
        if is_noise(title):
            continue
        dom, score = classify(title)
        if not dom:
            continue  # 不属于监控领域
        rec = {
            "id": key,
            "title": title,
            "url": it.get("url", ""),
            "source": it.get("source", ""),
            "domain": dom,
            "published": it.get("published", ""),
            "summary": it.get("summary", ""),
            "first_seen": TODAY,
            "last_seen": TODAY,
            "bt": breakthrough_score(title),
            "co": extract_companies(title),
        }
        idx[key] = rec
        added += 1
    items = list(idx.values())
    # 清理过期
    cutoff = (datetime.now(BJ) - timedelta(days=MAX_HISTORY_DAYS)).strftime("%Y-%m-%d")
    items = [x for x in items if x.get("first_seen", TODAY) >= cutoff]
    items.sort(key=lambda x: (x.get("first_seen", ""), x.get("bt", 0)), reverse=True)
    return items, added


def log(msg: str):
    line = f"[{NOW_STR}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------- 导出 CSV
def write_csv(items):
    with open(CSV_FILE, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["首次发现", "领域", "标题", "来源", "原文日期", "技术突破分", "链接"])
        for it in items:
            w.writerow([
                it.get("first_seen", ""),
                DOMAIN_BY_KEY.get(it.get("domain", ""), {}).get("name", it.get("domain", "")),
                it.get("title", ""),
                it.get("source", ""),
                it.get("published", ""),
                it.get("bt", 0),
                it.get("url", ""),
            ])


# ---------------------------------------------------------------- 看板 HTML
def build_dashboard(items):
    payload = []
    for it in items:
        d = DOMAIN_BY_KEY.get(it.get("domain", ""))
        payload.append({
            "t": it.get("title", ""),
            "u": it.get("url", ""),
            "s": it.get("source", ""),
            "d": it.get("domain", ""),
            "dn": d["name"] if d else it.get("domain", ""),
            "c": d["color"] if d else "#64748b",
            "fd": it.get("first_seen", ""),
            "g": (it.get("published") or it.get("first_seen", "")),
            "pd": it.get("published", ""),
            "bt": it.get("bt", 0),
            "co": it.get("co", []),
            "sm": (it.get("summary", "") or "")[:200],
        })
    data_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    # 统计
    dom_counts = {}
    for p in payload:
        dom_counts[p["d"]] = dom_counts.get(p["d"], 0) + 1
    today_new = sum(1 for p in payload if p["fd"] == TODAY)
    bt_count = sum(1 for p in payload if p["bt"] > 0)
    src_set = {p["s"].split("·")[0] for p in payload}

    legend = "".join(
        f'<span class="lg"><i style="background:{d["color"]}"></i>{d["name"]}'
        f'<b>{dom_counts.get(d["key"],0)}</b></span>' for d in DOMAINS
    )

    html = DASHBOARD_TMPL
    for k, v in (
        ("__DATA_JSON__", data_json),
        ("__TODAY__", TODAY),
        ("__UPDATED__", NOW_STR),
        ("__TOTAL__", str(len(payload))),
        ("__TODAY_NEW__", str(today_new)),
        ("__BT_COUNT__", str(bt_count)),
        ("__SRC_COUNT__", str(len(src_set))),
        ("__LEGEND__", legend),
    ):
        html = html.replace(k, v)
    return html


DASHBOARD_TMPL = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>中国半导体技术突破监控台</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  :root{
    --bg:#f6f7f9; --card:#ffffff; --ink:#0f172a; --sub:#64748b;
    --line:#e6e9ef; --accent:#1d4ed8; --warn:#dc2626; --ok:#059669;
  }
  body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
       background:var(--bg);color:var(--ink);line-height:1.6;-webkit-font-smoothing:antialiased}
  .wrap{max-width:1240px;margin:0 auto;padding:26px 20px 60px}
  header{background:linear-gradient(135deg,#0f172a 0%,#1e3a8a 55%,#1d4ed8 100%);
         border-radius:16px;padding:26px 30px;color:#fff;box-shadow:0 8px 26px rgba(15,23,42,.16)}
  header h1{font-size:23px;font-weight:700;letter-spacing:.3px}
  header .sub{margin-top:6px;font-size:13px;color:#c7d2fe}
  .stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-top:20px}
  .stat{background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.16);
        border-radius:11px;padding:12px 14px}
  .stat b{display:block;font-size:24px;font-weight:700;line-height:1.2}
  .stat span{font-size:12px;color:#dbeafe}
  .legend{margin-top:16px;display:flex;flex-wrap:wrap;gap:8px}
  .lg{display:inline-flex;align-items:center;gap:6px;font-size:12px;color:#e0e7ff;
      background:rgba(255,255,255,.1);border-radius:20px;padding:4px 11px}
  .lg i{width:8px;height:8px;border-radius:50%;display:inline-block}
  .lg b{font-weight:600;color:#fff}
  .toolbar{margin:22px 0 14px;display:flex;flex-direction:column;gap:12px}
  .chips{display:flex;flex-wrap:wrap;gap:8px}
  .chip{border:1px solid var(--line);background:var(--card);border-radius:20px;
        padding:6px 14px;font-size:13px;cursor:pointer;transition:.15s;color:#334155;user-select:none}
  .chip:hover{border-color:#94a3b8}
  .chip.on{background:var(--ink);color:#fff;border-color:var(--ink)}
  .chip .n{font-size:11px;opacity:.65;margin-left:5px}
  .row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
  input[type=search]{flex:1;min-width:220px;border:1px solid var(--line);background:var(--card);
        border-radius:10px;padding:9px 14px;font-size:14px;outline:none;color:var(--ink)}
  input[type=search]:focus{border-color:var(--accent)}
  .toggle{display:inline-flex;align-items:center;gap:7px;font-size:13px;color:#334155;
          background:var(--card);border:1px solid var(--line);border-radius:10px;padding:8px 13px;cursor:pointer;user-select:none}
  .toggle.on{background:#fef2f2;border-color:#fecaca;color:#b91c1c}
  .chips.cochips{margin-top:-4px}
  .co-chip{font-size:12px;border-style:dashed}
  .co-chip.on{background:#1e3a8a;border-color:#1e3a8a;border-style:solid}
  .co-tag{background:#eff6ff;color:#1d4ed8;border:1px solid #dbeafe;border-radius:6px;
          padding:2px 8px;font-size:11px;font-weight:600}
  .day{margin-top:26px}
  .day-h{display:flex;align-items:center;gap:10px;font-size:14px;font-weight:600;color:#0f172a;margin-bottom:10px}
  .day-h .dot{width:9px;height:9px;border-radius:50%;background:var(--accent)}
  .day-h .cnt{font-size:12px;color:var(--sub);font-weight:500}
  .cards{display:grid;grid-template-columns:1fr;gap:9px}
  .card{background:var(--card);border:1px solid var(--line);border-left:3px solid #cbd5e1;
        border-radius:11px;padding:13px 16px;transition:.15s}
  .card:hover{box-shadow:0 4px 16px rgba(15,23,42,.08);transform:translateY(-1px)}
  .card h3{font-size:15px;font-weight:600;line-height:1.5;color:#0f172a}
  .card h3 a{color:inherit;text-decoration:none}
  .card h3 a:hover{color:var(--accent)}
  .card .sm{font-size:12.5px;color:#64748b;margin-top:6px;line-height:1.55}
  .meta{margin-top:8px;display:flex;flex-wrap:wrap;gap:8px;align-items:center;font-size:11.5px;color:var(--sub)}
  .tag{border-radius:6px;padding:2px 8px;font-size:11px;font-weight:600;color:#fff}
  .src{background:#f1f5f9;border-radius:6px;padding:2px 8px;color:#475569}
  .bt{color:var(--warn);font-weight:600}
  .empty{text-align:center;color:var(--sub);padding:60px 0;font-size:14px}
  footer{margin-top:44px;text-align:center;font-size:12px;color:#94a3b8;line-height:1.9}
  @media(max-width:720px){.stats{grid-template-columns:repeat(2,1fr)}header{padding:20px}}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>中国半导体技术突破监控台</h1>
    <div class="sub">光刻机 · 电源光源 · 机台 · 镜头 · 光刻胶材料 · 光通讯 · 逻辑制程 · 存储制程 &nbsp;|&nbsp; 更新于 __UPDATED__</div>
    <div class="stats">
      <div class="stat"><b>__TOTAL__</b><span>累计情报条目</span></div>
      <div class="stat"><b>__TODAY_NEW__</b><span>今日新增</span></div>
      <div class="stat"><b>__BT_COUNT__</b><span>技术突破类</span></div>
      <div class="stat"><b>__SRC_COUNT__</b><span>信息来源</span></div>
    </div>
    <div class="legend">__LEGEND__</div>
  </header>

  <div class="toolbar">
    <div class="chips" id="chips"></div>
    <div class="chips cochips" id="cochips"></div>
    <div class="row">
      <input type="search" id="q" placeholder="搜索标题关键词，如：光刻胶 / 华卓精科 / HBM …">
      <div class="toggle" id="btTg">仅看技术突破</div>
      <div class="toggle" id="todayTg">仅看今日新增</div>
    </div>
  </div>

  <div id="list"></div>

  <footer>
    数据每日自动抓取自集微网 / 闪存市场 / 芯东西 / 电子发烧友 / IT之家 / cnBeta / Google News<br>
    「技术突破」按标题信号词自动打分，仅供参考；点击标题查看原文。
  </footer>
</div>

<script>
const DATA = __DATA_JSON__;
const DOMS = [["","全部"]].concat(Array.from(new Set(DATA.map(x=>x.d))).map(k=>{
  const f = DATA.find(x=>x.d===k); return [k, f?f.dn:k];
}));
let curDom = "", curCo = "", kw = "", onlyBt = false, onlyToday = false;

const COS = (()=>{const m={};DATA.forEach(x=>(x.co||[]).forEach(c=>m[c]=(m[c]||0)+1));
  return Object.entries(m).sort((a,b)=>b[1]-a[1]).slice(0,20);})();

function renderCo(){
  const box = document.getElementById('cochips');
  if(!COS.length){box.innerHTML='';return;}
  box.innerHTML = COS.map(([k,n])=>
    `<div class="chip co-chip${curCo===k?' on':''}" data-c="${esc(k)}">${esc(k)}<span class="n">${n}</span></div>`
  ).join('');
  box.querySelectorAll('.chip').forEach(el=>el.onclick=()=>{
    curCo = (curCo===el.dataset.c)?'':el.dataset.c; renderCo(); render();});
}

function renderChips(){
  const box = document.getElementById('chips');
  box.innerHTML = DOMS.map(([k,n])=>{
    const c = DATA.filter(x=>!k||x.d===k).length;
    return `<div class="chip${curDom===k?' on':''}" data-k="${k}">${n}<span class="n">${c}</span></div>`;
  }).join('');
  box.querySelectorAll('.chip').forEach(el=>el.onclick=()=>{curDom=el.dataset.k;renderChips();render();});
}

function render(){
  let arr = DATA.filter(x=>{
    if(curDom && x.d!==curDom) return false;
    if(curCo && !(x.co||[]).includes(curCo)) return false;
    if(onlyBt && x.bt<=0) return false;
    if(onlyToday && x.fd!=='__TODAY__') return false;
    if(kw){const k=kw.toLowerCase(); if(!(x.t.toLowerCase().includes(k)||(x.s||'').toLowerCase().includes(k))) return false;}
    return true;
  });
  const box = document.getElementById('list');
  if(!arr.length){box.innerHTML='<div class="empty">没有匹配的情报</div>';return;}
  const groups = {};
  arr.forEach(x=>{(groups[x.g]=groups[x.g]||[]).push(x);});
  const days = Object.keys(groups).sort().reverse();
  box.innerHTML = days.map(day=>{
    const items = groups[day].sort((a,b)=>b.bt-a.bt);
    return `<div class="day"><div class="day-h"><span class="dot"></span>${day}
      <span class="cnt">· ${items.length} 条</span></div><div class="cards">` +
      items.map(x=>`<div class="card" style="border-left-color:${x.c}">
        <h3>${x.u?`<a href="${x.u}" target="_blank" rel="noopener">${esc(x.t)}</a>`:esc(x.t)}</h3>
        ${x.sm?`<div class="sm">${esc(x.sm)}</div>`:''}
        <div class="meta">
          <span class="tag" style="background:${x.c}">${x.dn}</span>
          ${(x.co||[]).map(c=>`<span class="co-tag">${esc(c)}</span>`).join('')}
          <span class="src">${esc(x.s)}</span>
          ${x.pd?`<span>原发 ${x.pd}</span>`:''}
          ${x.bt>0?`<span class="bt">▲ 技术突破信号</span>`:''}
        </div></div>`).join('') + `</div></div>`;
  }).join('');
}
function esc(s){return (s||'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
document.getElementById('q').oninput = e=>{kw=e.target.value.trim();render();};
document.getElementById('btTg').onclick = e=>{onlyBt=!onlyBt;e.target.classList.toggle('on',onlyBt);render();};
document.getElementById('todayTg').onclick = e=>{onlyToday=!onlyToday;e.target.classList.toggle('on',onlyToday);render();};
renderChips();renderCo();render();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------- 主流程
def main():
    DATA_DIR.mkdir(exist_ok=True)
    log("=" * 60)
    log("开始抓取")
    history = load_history()
    log(f"历史条目: {len(history)}")

    fresh = []
    for src in SOURCES:
        try:
            got = src["fn"]()
            fresh.extend(got)
            log(f"[{src['name']}] 抓到 {len(got)} 条")
        except Exception as e:  # noqa: BLE001
            log(f"[{src['name']}] 异常: {type(e).__name__} {e}")

    log(f"本次原始抓取合计: {len(fresh)} 条")
    items, added = merge(history, fresh)
    log(f"新增入库: {added} 条；库内合计: {len(items)} 条")

    DATA_FILE.write_text(json.dumps(
        {"updated": NOW_STR, "count": len(items), "items": items},
        ensure_ascii=False, indent=1), encoding="utf-8")
    write_csv(items)

    html = build_dashboard(items)
    HTML_FILE.write_text(html, encoding="utf-8")
    log(f"看板已生成: {HTML_FILE.name} ({len(html)//1024} KB)")

    # 领域分布摘要
    cnt = {}
    for it in items:
        cnt[it["domain"]] = cnt.get(it["domain"], 0) + 1
    for d in DOMAINS:
        log(f"  · {d['name']}: {cnt.get(d['key'], 0)}")
    log("完成")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    main()

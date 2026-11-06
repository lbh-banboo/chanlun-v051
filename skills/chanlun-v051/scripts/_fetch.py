#!/usr/bin/env python3
"""30 分钟 K 线实时取数：新浪为主、东财为备（1010 实测：新浪 64/64 稳、0.3 秒/只；东财约一半连接被断）。
只用标准库。返回 [(日期, "HHMM", 开, 高, 低, 收, 量(股), 额(元)), ...]，价格为不复权价。
K 线时间标签＝该根的结束时刻：10:00 表示 9:30—10:00 那根。"""
import json
import ssl
import urllib.request

CTX = ssl.create_default_context()
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"}


def http(url, timeout=8):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout, context=CTX) as r:
        return r.read().decode("utf-8", "replace")


def fetch_em(code, lmt=320):
    num, ex = code.split(".")
    js = json.loads(http("https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=%s%s&klt=30&fqt=0&lmt=%d&end=20500101&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56,f57"
                         % ("1." if ex == "SH" else "0.", num, lmt)))
    out = []
    for s in js["data"]["klines"]:
        t, o, c, h, l, v, a = s.split(",")[:7]
        out.append((t[:10], t[11:13] + t[14:16], float(o), float(h), float(l), float(c), float(v) * 100, float(a)))
    return out


def fetch_sina(code, lmt=320):
    num, ex = code.split(".")
    b = http("https://quotes.sina.cn/cn/api/jsonp_v2.php/var%%20_x=/CN_MarketDataService.getKLineData?symbol=%s%s&scale=30&ma=no&datalen=%d" % (ex.lower(), num, lmt))
    js = json.loads(b[b.index("["):b.rindex("]") + 1])
    return [(x["day"][:10], x["day"][11:13] + x["day"][14:16], float(x["open"]), float(x["high"]), float(x["low"]), float(x["close"]), float(x["volume"]), float(x["amount"])) for x in js]

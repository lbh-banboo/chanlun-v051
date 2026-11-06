#!/usr/bin/env python3
"""全市场日线增量更新（要联网）——给 regime_daily.py（档位）、v51_scan.py（准入/带宽）、trin_daily.py 供数。

数据口径（搜狐不复权 + 官方前收反推前复权）：
  列 trade_date,open,high,low,close,volume(手),raw_close,amount(元),turnover(%),pct(%),adj；最新一日 adj=1。
增量来源：
  ① 新浪行情中心 hs_a 全市场快照（约 55 次请求拿全 5000+ 只当日开高低收、量额、换手、前收、总市值）；
     当日前收 ≠ 文件里上一日不复权收盘 → 当日除权除息，整段历史按比例重算前复权（adj 同乘）。
  ② 文件最后一日不是上一交易日（漏更、停牌复牌）→ 用搜狐 hisHq 单票补齐，受 max_secs 预算限制，没补完的下次接着补。
交易日以搜狐沪深300（zs_000300）日线为准；非交易日运行不写任何数据。
两道闸门（针对盘中补跑事故加，详见 references/11-incidents-and-pitfalls.md）：
  ①工作日 09:15—15:30 一律不写（新浪快照是盘中价）；②快照"前收"与库里上一日收盘大面积对不上（>20%）就整次不写。
用法：python3 daily_update.py        收盘后（建议 15:30 之后；定时任务 18:35）跑；非交易日跑不会写数据
"""
import os, sys, json, time
import requests  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import _paths as P
DATA = P.DAILY
CTL = os.path.join(P.META, "c_update_ctl.json")      # 可选：{"max_secs": 200, "force": false}
LOG = os.path.join(P.META, "c_update_log.json")
os.makedirs(P.META, exist_ok=True)
HDR = "trade_date,open,high,low,close,volume,raw_close,amount,turnover,pct,adj"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
SINA = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        "Market_Center.getHQNodeData?page=%d&num=100&sort=symbol&asc=1&node=hs_a")
SOHU = "https://q.stock.sohu.com/hisHq?code=%s&start=%s&end=%s&stat=1&order=A&period=d&rt=json"
S = requests.Session()


def get(url, headers):
    try:
        return S.get(url, headers=headers, timeout=20)
    except requests.exceptions.ProxyError:
        s2 = requests.Session(); s2.trust_env = False          # 系统代理不可用时直连
        return s2.get(url, headers=headers, timeout=20)


def fnum(x):
    try:
        return float(str(x).replace("%", "").replace(",", ""))
    except Exception:
        return None


def sohu_rows(sc, start, end):
    js = get(SOHU % (sc, start, end), {"User-Agent": UA}).json()
    rows = js[0]["hq"] if isinstance(js, list) and js and "hq" in js[0] else []
    out = []
    for r in rows:   # date, open, close, chg, pct, low, high, vol(手), amt(万元), turn
        o, c, chg, pct, lo, hi, vol, amt, turn = [fnum(x) for x in r[1:10]]
        if o and c and o > 0 and c > 0:
            out.append(dict(date=r[0], open=o, close=c, chg=chg, pct=pct, low=lo, high=hi, vol=vol,
                            amount=(amt or 0) * 1e4, turn=turn))
    return sorted(out, key=lambda x: x["date"])


def snapshot():
    rows, t0 = [], time.time()
    for page in range(1, 90):
        r = get(SINA % page, {"User-Agent": UA, "Referer": "https://vip.stock.finance.sina.com.cn/"})
        txt = r.text.strip()
        js = r.json() if txt not in ("", "null") else []
        if not js:
            break
        rows += js
        time.sleep(0.2)
    snap = {}
    for x in rows:
        s = x.get("symbol", "")
        if s[:2] not in ("sh", "sz"):
            continue
        snap[s[2:] + "." + s[:2].upper()] = x
    return snap, round(time.time() - t0, 1)


def last_line(p):
    with open(p, "rb") as f:
        f.seek(0, 2)
        n = f.tell()
        f.seek(max(n - 600, 0))
        tail = f.read().decode("utf-8").strip().splitlines()
    return tail[-1].split(",")


def fmt(v):
    return "" if v is None else repr(float(v))


def rescale(p, ratio):
    """除权除息：历史前复权价与 adj 同乘 ratio（最新一日因子恒为 1）。"""
    L = open(p, encoding="utf-8").read().strip().splitlines()
    out = [L[0]]
    for ln in L[1:]:
        c = ln.split(",")
        for i in (1, 2, 3, 4, 10):
            if c[i]:
                c[i] = repr(float(c[i]) * ratio)
        out.append(",".join(c))
    open(p, "w", encoding="utf-8").write("\n".join(out) + "\n")


def append(p, d, o, h, l, c, vol, amount, turn, pct):
    with open(p, "a", encoding="utf-8") as f:
        f.write(",".join([d, fmt(o), fmt(h), fmt(l), fmt(c), fmt(vol), fmt(c), fmt(amount), fmt(turn), fmt(pct), "1.0"]) + "\n")


def last_closed_day():
    """最近一个已收盘的交易日：搜狐沪深300 日线里早于今天的最后一天；15:30 以后若当天已出也算。取不到返回 None。"""
    today = time.strftime("%Y-%m-%d")
    idx = sohu_rows("zs_000300", time.strftime("%Y%m%d", time.localtime(time.time() - 40 * 86400)), time.strftime("%Y%m%d"))
    done = [r["date"] for r in idx if r["date"] < today or (r["date"] == today and time.strftime("%H%M") >= "1530")]
    return done[-1] if done else None


def main():
    ctl = json.load(open(CTL)) if os.path.exists(CTL) else {}
    max_secs = ctl.get("max_secs", 200)
    t00 = time.time()
    idx = sohu_rows("zs_000300", time.strftime("%Y%m%d", time.localtime(time.time() - 40 * 86400)), time.strftime("%Y%m%d"))
    if len(idx) < 2:
        print("取不到沪深300日线，无法确定交易日，本次不更新"); return
    D, prevD = idx[-1]["date"], idx[-2]["date"]
    if D == time.strftime("%Y-%m-%d") and time.strftime("%H%M") < "1530" and not ctl.get("force"):
        # 新浪快照在收盘前是盘中价，写进去就成了假收盘 → 15:30 前一律不写
        print("现在 %s，当日尚未收盘定稿（15:30 前不写日线）；收盘后再跑" % time.strftime("%H:%M")); return
    # 针对盘中补跑事故加的闸门：工作日 09:15—15:30 之间新浪快照是"今天"的盘中价，而搜狐指数日线最新一日 D 还是昨天，
    # 上面那条判断拦不住（D≠今天）→ 补跑时会把今天盘中价当成前一交易日收盘写进全库、并按假前收重算全市场复权。
    # 现在交易时段内一律不写；漏掉的上一交易日用 backfill_sohu_batch.py（搜狐日线）补。
    if time.localtime().tm_wday < 5 and "0915" <= time.strftime("%H%M") < "1530" and not ctl.get("force"):
        print("现在 %s 在交易时段内，新浪快照是今天的盘中价，不能当 %s 的收盘写入；本次不更新。漏掉的交易日请用 backfill_sohu_batch.py / catchup_lastday.py 补。" % (time.strftime("%H:%M"), D)); return
    log = {"date": D, "prev": prevD, "run_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    # 指数
    pidx = os.path.join(DATA, "000300.SH.csv")
    lastI = last_line(pidx)[0]
    for r in idx:
        if r["date"] > lastI:
            append(pidx, r["date"], r["open"], r["high"], r["low"], r["close"], r["vol"], r["amount"], None, r["pct"])
    snap, secs = snapshot()
    log["snapshot_n"], log["snapshot_secs"] = len(snap), secs
    # 针对盘中补跑事故加的第二道闸门（防"快照日期 ≠ D"）：快照若真是 D 当天的，它的"前收"应等于库里上一日（prevD）的不复权收盘，
    # 只有当天除权除息的少数票不等（平常 16—50 只）。若大面积不等（10-14 误跑时是 5046/5200），说明快照是更晚一天的行情，
    # 写进去就是把别的日子的价格记成 D，并按假前收重算全库复权 → 一行都不写，直接退出。
    chk = mism = 0
    for f in sorted(os.listdir(DATA)):
        if not f.endswith(".csv") or f.startswith("000300.SH"):
            continue
        last = last_line(os.path.join(DATA, f))
        x = snap.get(f[:-4])
        if last[0] != prevD or not x or not fnum(x.get("volume")) or not fnum(x.get("settlement")):
            continue
        chk += 1
        if abs(fnum(x.get("settlement")) / float(last[6]) - 1.0) > 1e-6:
            mism += 1
    log["prev_close_check"] = [mism, chk]
    if chk >= 100 and mism > 0.2 * chk and not ctl.get("force"):
        print("快照前收与库里 %s 收盘对不上的有 %d/%d 只（正常只有几十只除权），快照不是 %s 当天的行情，本次不写任何数据。" % (prevD, mism, chk, D))
        log["aborted"] = "prev_close_mismatch"
        prev = json.load(open(LOG)) if os.path.exists(LOG) else []
        json.dump((prev + [log])[-60:], open(LOG, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
        return
    # 量的单位自检：新浪 volume 是股还是手（amount÷(volume×价) ≈1 → 股）
    ks = [k for k, x in snap.items() if fnum(x.get("volume")) and fnum(x.get("trade"))][:200]
    rat = sorted(fnum(snap[k]["amount"]) / (fnum(snap[k]["volume"]) * fnum(snap[k]["trade"])) for k in ks) if ks else [1.0]
    vol_div = 100.0 if 0.5 < rat[len(rat) // 2] < 2.0 else 1.0
    log["vol_unit"] = "股→÷100 记为手" if vol_div == 100.0 else "手"
    n_app = n_ex = n_susp = 0
    gap = []
    for f in sorted(os.listdir(DATA)):
        if not f.endswith(".csv") or f.startswith("000300.SH"):
            continue
        code, p = f[:-4], os.path.join(DATA, f)
        last = last_line(p)
        if last[0] >= D:
            continue
        x = snap.get(code)
        vol = fnum(x.get("volume")) if x else None
        if not x or not vol or not fnum(x.get("open")):
            n_susp += 1            # 当日停牌或无快照：不写行（与搜狐日线口径一致：停牌日无记录）
            if last[0] < prevD:
                gap.append(code)
            continue
        if last[0] != prevD:
            gap.append(code); continue
        pre, lraw = fnum(x.get("settlement")), float(last[6])
        ratio = pre / lraw if (pre and lraw) else 1.0
        if abs(ratio - 1.0) > 1e-6:
            rescale(p, ratio); n_ex += 1
        append(p, D, fnum(x["open"]), fnum(x["high"]), fnum(x["low"]), fnum(x["trade"]), vol / vol_div,
               fnum(x.get("amount")), fnum(x.get("turnoverratio")), fnum(x.get("changepercent")))
        n_app += 1
    # 漏更补齐（搜狐单票）
    done_gap, left = [], []
    for code in gap:
        if time.time() - t00 > max_secs:
            left.append(code); continue
        p = os.path.join(DATA, code + ".csv")
        last = last_line(p)
        start = last[0].replace("-", "")
        try:
            rows = [r for r in sohu_rows("cn_" + code[:6], start, D.replace("-", "")) if r["date"] > last[0]]
        except Exception:
            left.append(code); continue
        lraw = float(last[6])
        for r in rows:
            pre = r["close"] - (r["chg"] or 0)
            ratio = pre / lraw if lraw else 1.0
            if abs(ratio - 1.0) > 1e-6:
                rescale(p, ratio); n_ex += 1
            append(p, r["date"], r["open"], r["high"], r["low"], r["close"], r["vol"], r["amount"], r["turn"], r["pct"])
            lraw = r["close"]
        if rows:
            done_gap.append(code)          # 只统计真正补上行的（长期停牌的票每天都会查一次，但不算"补漏"）
        time.sleep(0.3)
    log.update(appended=n_app, exdiv=n_ex, suspended_or_missing=n_susp, gap_filled=len(done_gap), gap_left=left[:50],
               gap_left_n=len(left), secs=round(time.time() - t00, 1))
    prev = json.load(open(LOG)) if os.path.exists(LOG) else []
    json.dump((prev + [log])[-60:], open(LOG, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    print(json.dumps(log, ensure_ascii=False))


if __name__ == "__main__":
    main()

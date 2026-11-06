#!/usr/bin/env python3
"""股票池 30 分钟缠论遍历（只读：不写 V5.1 正式账本、不改 state/m30_ext）。
三个口径：①临近买点（结构成形中、未确认）②近 N 个交易日已确认买点的现状 ③30 分钟强弱排序。
引擎与 V5.1 实时扫描完全相同（sop_run.run_series，S＝30 分钟线段），数据拼接口径与 v51_scan.py 相同。
用法：python3 pool_watch.py                 （默认新浪取最新已收盘 30m，仅在内存合并，约 15 秒/65 只）
      python3 pool_watch.py --no-fetch [--asof "YYYY-MM-DD HH:MM"] [--look 10]
输出：live/watch/pool_watch_YYYYMMDD_HHMM.json（md 报告由 pool_watch_report.py 生成）
第二个池：环境变量 PW_POOL_FILE / PW_CFG / PW_STATE / PW_OUT_DIR / PW_LABEL，由 pool_watch_focus.py 设置。"""
import os, sys
import re, json, math, csv, time, argparse, bisect, datetime as dt
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "engine"))
import _paths as P
import _common as C
import _fetch as F
import sop_run as R
import sop_engine as SE

ap = argparse.ArgumentParser()
ap.add_argument("--no-fetch", action="store_true"); ap.add_argument("--asof"); ap.add_argument("--look", type=int, default=20)
ap.add_argument("--out", default="")
A = ap.parse_args()
NOW = dt.datetime.strptime(A.asof, "%Y-%m-%d %H:%M") if A.asof else dt.datetime.now()
LIVE = P.DAILY; ALL = P.DAILY_HIST
M30 = P.M30; REC = P.M30_RECENT
PW_POOL = os.environ.get("PW_POOL_FILE") or "watchlist.md"           # 第二个池（重点池）由 pool_watch_focus.py 设置；全不设＝第一个池
PW_CFG = os.environ.get("PW_CFG") or "v51_config.json"
PW_STATE = os.environ.get("PW_STATE") or "state"
PW_OUT = os.environ.get("PW_OUT_DIR") or "watch"
PW_LABEL = os.environ.get("PW_LABEL") or ""
EXT = os.path.join(P.LIVE, PW_STATE, "m30_ext"); PIPE = P.PIPE
OUTD = os.path.join(P.LIVE, PW_OUT); os.makedirs(OUTD, exist_ok=True)
CFG = P.load_config(PW_CFG)
BASE_END = CFG["m30_base_end"]
SLOTS = C.SLOTS
NAV = CFG["nav0"]; START = CFG["start_date"]
RISK, CAP, MULT, CN = C.RISK, C.CAP, C.MULT, C.CN
COOLING = C.read_cooling(PW_POOL, NOW.strftime("%Y-%m-%d"))          # 名单行状态列里的冷却期声明（references/05 第 4 条）


# ---------------- 名单（与 v51_scan.read_pool 相同） ----------------
NAMES, CLUSTERS = C.read_pool(PW_POOL)
CODES = sorted(NAMES)
POOL_FROM = {c: v for c, v in C.read_pool_from(PW_POOL).items() if c in NAMES and v[:10] >= START}


# ---------------- 取数（新浪主、东财备；只进内存） ----------------
def fetch_sina(code, lmt=120): return F.fetch_sina(code, lmt)
def fetch_em(code, lmt=120): return F.fetch_em(code, lmt)

def complete(d, hhmm):
    return dt.datetime.strptime(d + hhmm, "%Y-%m-%d%H%M") + dt.timedelta(seconds=8) <= NOW

FRESH = {}; HEALTH = dict(src={}, fail=[])
def fetch_all():
    from concurrent.futures import ThreadPoolExecutor
    def one(code):
        last = ""
        for nm, fn in (("sina", fetch_sina), ("eastmoney", fetch_em)):
            for _ in range(2):
                try: return code, nm, fn(code), ""
                except Exception as e:  # noqa
                    last = repr(e)[:80]; time.sleep(0.6)
        return code, None, None, last
    with ThreadPoolExecutor(max_workers=4) as ex:
        for code, nm, rows, last in ex.map(one, CODES):
            if rows is None: HEALTH["fail"].append([code, last]); continue
            HEALTH["src"][nm] = HEALTH["src"].get(nm, 0) + 1
            FRESH[code] = pd.DataFrame([r for r in rows if r[0] > BASE_END and r[1] in SLOTS],
                                       columns=["date", "time", "open", "high", "low", "close", "volume", "amount"])


# ---------------- 载入（与 v51_scan.load_daily / load_stock 同口径） ----------------
def load_daily(code):
    lv = pd.read_csv(os.path.join(LIVE, code + ".csv"), parse_dates=["trade_date"]).set_index("trade_date").sort_index()
    pa = os.path.join(ALL, code + ".csv")
    if os.path.exists(pa):
        a0 = pd.read_csv(pa, parse_dates=["trade_date"]).set_index("trade_date").sort_index()
        a = a0[a0.index < lv.index[0]].copy()
        if len(a):
            k = lv["adj"].iloc[0] / a0["adj"].reindex([lv.index[0]]).iloc[0] if lv.index[0] in a0.index else 1.0
            a["adj"] = a["adj"] * k
            lv = pd.concat([a, lv])
    return lv

def load_m30(code, d):
    frames = []
    p1 = os.path.join(M30, code + ".csv.gz")
    if os.path.exists(p1): frames.append(pd.read_csv(p1))
    for pth in (os.path.join(REC, code + ".csv"), os.path.join(EXT, code + ".csv")):
        if os.path.exists(pth):
            df_ = pd.read_csv(pth)
            if len(df_): frames.append(df_)
    if code in FRESH and len(FRESH[code]): frames.append(FRESH[code])
    # 先把各来源的 date/time 统一成字符串再去重（CSV 读进来 time 是整数、新浪内存数据是字符串，不统一会去重失败、K 线重复）
    for f_ in frames:
        f_["date"] = f_["date"].astype(str); f_["time"] = f_["time"].astype(int).map(lambda x: "%04d" % x)
    m = pd.concat(frames).drop_duplicates(["date", "time"], keep="last")
    assert not m.duplicated(["date", "time"]).any()
    m["time"] = m["time"].astype(int).map(lambda x: "%04d" % x)
    m = m[[complete(x, t) for x, t in zip(m["date"], m["time"])]].sort_values(["date", "time"]).reset_index(drop=True)
    adj = d["adj"].reindex(pd.to_datetime(m["date"]).values).ffill().bfill().values
    for c in ("open", "high", "low", "close"):
        m["r_" + c] = m[c].astype(float); m[c] = m[c].astype(float) * adj
    m["adj"] = adj
    return m


# ---------------- 引擎最终状态（逐根推进同 run_series；另取最后的线段/笔/中枢） ----------------
def analyze2(units, dif, bar):
    """与 sop_engine.analyze 逐行相同，另返回中枢列表（含 B3 位置、离开位置）。"""
    ev = []
    m = len(units) - 1
    if m < 3:
        return ev, [], []
    U = []
    for i in range(m):
        a, b = units[i], units[i + 1]
        U.append((1 if b[1] > a[1] else -1, min(a[1], b[1]), max(a[1], b[1]), a[2], b[2], b[3], a[1], b[1]))
    pivots = []
    i = 0
    while i + 2 < m:
        z_g = min(U[i][2], U[i + 1][2], U[i + 2][2]); z_d = max(U[i][1], U[i + 1][1], U[i + 2][1])
        if not z_g > z_d:
            i += 1; continue
        pv = dict(i0=i, ZG=z_g, ZD=z_d, avail=U[i + 2][5], first_s=U[i][3], end=None, brk=None, b3=None, last=i + 2,
                  GG=max(U[i][2], U[i + 1][2], U[i + 2][2]), DD=min(U[i][1], U[i + 1][1], U[i + 2][1]))
        ev.append(("NEWP", U[i + 2][5], dict(ZG=z_g, ZD=z_d, first_s=U[i][3], i0=i)))
        j = i + 3; nxt = None
        while j < m:
            u = U[j]
            if u[0] == -1:
                if u[1] >= z_g and j >= i + 4:
                    if U[j - 1][2] > z_g:
                        ev.append(("B3", u[5], dict(ZG=z_g, ZD=z_d, lo=u[1], e_idx=u[4], s_idx=u[3])))
                        pv["end"] = "B3"; pv["b3"] = j; nxt = j + 1; break
                if u[1] < z_d:
                    pv["end"] = "BRK"; pv["brk"] = j; nxt = j + 1; break
            pv["last"] = j
            j += 1
        pivots.append(pv)
        if pv["end"] is None:
            break
        i = nxt
    for n, pv in enumerate(pivots):
        if pv["end"] != "BRK":
            continue
        c = U[pv["brk"]]
        if n >= 1 and pivots[n - 1]["end"] == "BRK" and pv["ZG"] < pivots[n - 1]["ZD"]:
            b = U[pivots[n - 1]["brk"]]; kind = "B1"
        else:
            if pv["i0"] >= 1 and U[pv["i0"] - 1][0] == -1:
                b = U[pv["i0"] - 1]; kind = "B1r"
            else:
                continue
        if not c[1] < b[1]:
            continue
        sa, ea = b[3], b[4]; sc, ec = c[3], c[4]
        if ea <= sa or ec <= sc:
            continue
        areaA = -np.nansum(np.minimum(bar[sa:ea + 1], 0)); areaC = -np.nansum(np.minimum(bar[sc:ec + 1], 0))
        dA, dC = dif[sa:ea + 1], dif[sc:ec + 1]
        if np.all(np.isnan(dA)) or np.all(np.isnan(dC)):
            continue
        if areaC < areaA and np.nanmin(dC) > np.nanmin(dA):
            ev.append((kind, c[5], dict(lo=c[1], e_idx=c[4], s_idx=c[3], idx_unit=pv["brk"], n_unit=len(U), areaA=float(areaA), areaC=float(areaC))))
    return ev, U, pivots


def final_walker(h, l):
    W = Walker2()
    for k, kind, it in SE.pen_events(h, l):
        W.push(kind, it, k)
    return W


# ---------------- 引擎 ----------------
# "线段端点被延伸→永久卡死"缺陷已修进正式 sop_engine.py（Walker.push）。
# 本脚本此前的本地修正版 Walker2/run_series2 与修复后的正式引擎逐事件相同，现直接用正式引擎；
# res["fixed"]>0 表示这只票在修复前会卡死（修复逻辑被触发过）。旧版脚本备份：pool_watch.py.bak_1013_walker2。
Walker2 = SE.Walker


def run_series2(h, l, c):
    return R.run_series(h, l, c, "S")


def ev_sig(res):
    return sorted((t, int(k), int(info.get("e_idx", -1)), round(float(info.get("lo", 0)), 6)) for t in ("B1", "B1r", "B2c", "B2w", "B3", "S3", "S1") for k, info in res[t])


def official_stuck_at(h, l):
    """正式 Walker 第一次"找不到 seg[-1]"（此后永久卡死）的 K 线序号；没卡死返回 None。"""
    W = SE.Walker()
    for k, kind, it in SE.pen_events(h, l):
        W.push(kind, it, k)
        if kind == "ext" and W.seg:
            s0 = W.seg[-1]
            if not any(p[2] == s0[2] and p[0] == s0[0] for p in W.pen[-3:]) and not any(p[2] == s0[2] and p[0] == s0[0] for p in W.pen):
                return int(k)
    return None


def seg_progress(rest):
    """进行中线段的推进状态（与 find_seg_end 同一套跟踪逻辑，但不要求结束）：返回 best 下标、r1 价、best 之后的端点。"""
    up = rest[0][0] == "bot"; same = "top" if up else "bot"
    best = None; r1 = None
    for j in range(1, len(rest)):
        p = rest[j]
        if p[0] == same:
            if best is None or (p[1] > rest[best][1] if up else p[1] < rest[best][1]):
                best = j; r1 = None
        else:
            if best is None: continue
            if r1 is None: r1 = p[1]
            elif (p[1] < r1) if up else (p[1] > r1): r1 = p[1]
    after = rest[best + 1:] if best is not None else []
    return best, r1, after


# ---------------- 档位 ----------------
def regime_before(date):
    return C.regime_before(date, PIPE)


# ---------------- 闸门预检（V5.1 6.3 的 1/2/4/5/6/8 与 3.3 资金可交易性；用"预计买价"估） ----------------
board_of = C.board_of
CEIL = C.ceilings(NAV)

def gate_check(st, code, entry, stop, regime):
    """返回 dict(ok, 股数, 原因列表, 明细)"""
    why = []; det = {}
    bd = board_of(code)
    if "ST" in NAMES[code]: why.append("ST")
    if st["n_daily"] < 250: why.append("上市不满250日")
    if st["amt20"] < 1e8: why.append("20日均额%.2f亿<1亿" % (st["amt20"] / 1e8))
    if code in COOLING: why.append("冷却期(视同)")
    if entry > CEIL[bd]: why.append("1.5a超价(>%.2f)" % CEIL[bd])
    mult, capr = MULT[regime], CAP[regime]
    if mult <= 0: why.append("档位禁开")
    dist = (entry - stop) / entry if entry > 0 else 9
    det["dist"] = dist
    if dist > 0.12: why.append("止损距离%.1f%%>12%%" % (100 * dist))
    if dist < 0.01:
        stop = entry * 0.99; dist = 0.01; det["stop_adj1"] = True
    det["band"] = (entry - stop) / st["amp5"] if st["amp5"] > 0 else None
    if (entry - stop) < st["amp5"]: why.append("带宽%.2f<1" % det["band"])
    per = entry - stop
    sh_risk = math.floor(RISK * mult * NAV / per) if per > 0 and mult > 0 else 0
    capsingle = 0.20 if bd == "main" else 0.10
    sh = min(sh_risk, math.floor(capsingle * NAV / entry), math.floor(capr * NAV / entry))
    sh = (sh if sh >= 200 else 0) if bd == "star" else (sh // 100) * 100
    det.update(sh_risk=sh_risk, shares=sh, mkt=sh * entry, plan_loss=sh * per)
    if sh <= 0 and not any(w.startswith(("1.5a", "止损距离", "带宽")) for w in why):
        why.append("不足一手(风险预算股数%d)" % sh_risk)
    return dict(ok=not why, why=why, **det)


def gate_window(st, code, stop, regime):
    """临近买点的闸门预检：买点还没确认、买价未知，所以不判"过/不过"，而是算出"能买的确认价区间"。
    下限＝失效价＋近5日平均振幅（#57 带宽）；上限＝min(失效价/0.88（止损距离≤12%）、失效价＋风险预算/一手（至少买得起一手）、1.5a 价格上限)。
    另列与价格无关的准入：ST、上市满250日、20日均额≥1亿、冷却期。"""
    bd = board_of(code); lot = 200 if bd == "star" else 100
    mult = MULT[regime]
    budget = RISK * mult * NAV
    lo_e = stop + st["amp5"]
    caps = dict(stop12=stop / 0.88, lot_risk=stop + budget / lot if budget > 0 else 0.0, ceil=CEIL[bd])
    hi_e = min(caps.values())
    why_hi = min(caps, key=caps.get)
    pre = []
    if "ST" in NAMES[code]: pre.append("ST")
    if st["n_daily"] < 250: pre.append("上市不满250日")
    if st["amt20"] < 1e8: pre.append("20日均额%.2f亿<1亿" % (st["amt20"] / 1e8))
    if code in COOLING: pre.append("冷却期(视同)")
    if mult <= 0: pre.append("档位禁开")
    return dict(lot=lot, budget=budget, lo_e=lo_e, hi_e=hi_e, why_hi={"stop12": "止损距离12%", "lot_risk": "风险预算只够一手", "ceil": "1.5a价格上限"}[why_hi],
                feasible=bool(lo_e <= hi_e) and not pre, pre=pre, amp5=st["amp5"])


# ---------------- 单票分析 ----------------
def analyze_stock(code, regime):
    d = load_daily(code)
    m = load_m30(code, d)
    n = len(m)
    h, l, c = m.high.values, m.low.values, m.close.values
    adj = m["adj"].values
    res = R.run_series(h, l, c, "S")                       # 正式引擎（已修复卡死缺陷；与 V5.1 实时扫描同一套）
    res_off = res
    stuck_k = None
    off_ok = True
    dif, dea, hist = SE.E.macd(c)
    _, bar = SE.macd_hist(c)
    W = final_walker(h, l)
    units = W.seg
    ev_f, U, piv = analyze2(units, dif, bar)
    # 一致性：analyze2 的事件必须与 sop_engine.analyze 完全相同
    ev_chk, _ = SE.analyze(units, dif, bar, None)
    assert [(a, b, tuple(sorted((k, v) for k, v in x.items() if k in ("lo", "e_idx", "ZG", "ZD", "first_s")))) for a, b, x in ev_chk] == \
           [(a, b, tuple(sorted((k, v) for k, v in x.items() if k in ("lo", "e_idx", "ZG", "ZD", "first_s")))) for a, b, x in ev_f], code
    dates = m["date"].values; times = m["time"].values
    udates = sorted(set(dates))
    last_date = dates[-1]
    # 日线：严格早于最后一根 K 线所在日的数据（与 V5.1 准入/带宽口径一致：入场日之前）
    dk = [str(x)[:10] for x in d.index]
    i_prev = bisect.bisect_left(dk, last_date) - 1
    seg5 = d.iloc[max(0, i_prev - 4):i_prev + 1]
    amp5 = float(((seg5["high"] - seg5["low"]) / seg5["adj"]).mean())
    amt20 = float(d["amount"].iloc[max(0, i_prev - 19):i_prev + 1].mean())
    pclose = float(d["raw_close"].iloc[i_prev])
    st = dict(n_daily=i_prev + 1, amp5=amp5, amt20=amt20)
    last_raw = float(m["r_close"].iloc[-1]); last_adj = float(c[-1]); a_last = float(adj[-1])
    toraw = lambda px: px / a_last                          # 前复权价 → 当前不复权价（最新复权因子下二者一致）
    # 30 分钟均线与 MACD
    cs = pd.Series(c)
    ma20 = float(cs.rolling(20).mean().iloc[-1]); ma60 = float(cs.rolling(60).mean().iloc[-1])
    def close_at_day_end(k_days):
        if len(udates) <= k_days: return np.nan
        dd = udates[-1 - k_days]
        idx = np.where(dates == dd)[0][-1]
        return float(c[idx])
    ret5 = last_adj / close_at_day_end(5) - 1; ret10 = last_adj / close_at_day_end(10) - 1
    # 进行中线段
    s0 = units[-1]
    seg_dir = "up" if s0[0] == "bot" else "down"
    idx = None
    for i in range(len(W.pen) - 1, -1, -1):
        p = W.pen[i]
        if p[2] == s0[2] and p[0] == s0[0]: idx = i; break
    rest = W.pen[idx:] if idx is not None else [s0]
    best, r1, after = seg_progress(rest)
    a0 = min(s0[2] + 1, n - 1)
    if seg_dir == "down":
        kx = a0 + int(np.argmin(l[a0:])); ext_px = float(l[kx])
    else:
        kx = a0 + int(np.argmax(h[a0:])); ext_px = float(h[kx])
    # 笔数：从线段起点到当前极值（含尚未成笔的最后一段）
    pens_to_best = best if best is not None else 0
    # 最近中枢与位置
    pv_last = piv[-1] if piv else None
    pv_prev = piv[-2] if len(piv) >= 2 else None
    pos = None
    if pv_last:
        pos = "中枢上方" if last_adj > pv_last["ZG"] else ("中枢下方" if last_adj < pv_last["ZD"] else "中枢内")
    trend = None
    if pv_last and pv_prev:
        trend = "中枢抬高" if pv_last["ZD"] > pv_prev["ZG"] else ("中枢下移" if pv_last["ZG"] < pv_prev["ZD"] else "中枢重叠")
    # 已确认事件（近 LOOK 个交易日）
    look_days = set(udates[-A.look:])
    def bar_s(k): return "%s %s" % (dates[k], times[k])
    evs = []
    for typ in ("B3", "B1", "B2c", "B2w", "S3", "S1", "B1r"):
        for k, info in res[typ]:
            evs.append((int(k), typ, info))
    evs.sort(key=lambda x: x[0])
    recent_buys = []; recent_sells = []
    for k, typ, info in evs:
        if dates[k] not in look_days: continue
        if typ in ("S1", "S3"):
            recent_sells.append(dict(typ={"S3": "三卖", "S1": "趋势一卖"}[typ], bar=bar_s(k), k=k, hi=toraw(-info["lo"]) if info["lo"] < 0 else None)); continue
        if typ == "B1r":
            continue
        e = int(info["e_idx"]); stop_adj = info["lo"] * 0.99; stop_raw = stop_adj / float(adj[min(e, n - 1)])
        sig_close = float(m["r_close"].iloc[k])
        nxt = k + 1 if k + 1 < n else None
        brk = None
        for kk in range(k + 1, n):
            if c[kk] < stop_adj: brk = bar_s(kk); break
        later_sell = [bar_s(kk) + typ2 for kk, typ2, _ in evs if kk > k and typ2 in ("S1", "S3")]
        pre = code in POOL_FROM and bar_s(k) <= POOL_FROM[code]          # 入池前确认的买点（不入账）
        rb = dict(typ={"B3": "三买", "B1": "一买", "B2c": "二买", "B2w": "二买(破一买)"}[typ], bar=bar_s(k), k=k, ext_bar=bar_s(e),
                  age=len(udates) - 1 - udates.index(dates[k]),
                  sig_close=sig_close, stop=stop_raw, lo=toraw(info["lo"]), ZG=toraw(info["ZG"]) if "ZG" in info else None,
                  next_open=float(m["r_open"].iloc[nxt]) if nxt is not None else None, next_bar=bar_s(nxt) if nxt is not None else None,
                  broke_stop=brk, later_sell=later_sell, chg_since=last_raw / sig_close - 1, to_stop=last_raw / stop_raw - 1,
                  live=(dates[k] >= START and not pre), prepool=pre)
        # 当时按 V5.1 能不能买：开盘三条 + 闸门（档位用信号日前一交易日）
        if nxt is not None:
            op = rb["next_open"]; rg, _ = regime_before(dates[nxt])
            i_p2 = bisect.bisect_left(dk, dates[nxt]) - 1
            seg5b = d.iloc[max(0, i_p2 - 4):i_p2 + 1]
            st2 = dict(n_daily=i_p2 + 1, amp5=float(((seg5b["high"] - seg5b["low"]) / seg5b["adj"]).mean()),
                       amt20=float(d["amount"].iloc[max(0, i_p2 - 19):i_p2 + 1].mean()))
            pc2 = float(d["raw_close"].iloc[i_p2]); lim = 0.20 if board_of(code) != "main" else 0.10
            if op > sig_close * 1.03: rb["exec"] = "次根开盘%.2f高开>3%%，取消" % op
            elif op >= round(pc2 * (1 + lim), 2) - 1e-6: rb["exec"] = "次根开盘涨停，取消"
            elif op <= stop_raw: rb["exec"] = "次根开盘已破失效价，取消"
            else:
                entry = op * 1.001; stp = min(stop_raw, entry * 0.99)
                g = gate_check(st2, code, entry, stp, rg)
                rb["exec"] = ("闸门全过，记账股数%d" % g["shares"]) if g["ok"] else ("闸门不过：" + "、".join(g["why"]))
                rb["exec_gate"] = g
        else:
            rb["exec"] = "待下一根开盘"
        recent_buys.append(rb)
    # 临近买点（假设"进行中线段就在当前极值结束"，看引擎会不会出买点）
    pend = []
    b1_infos = [info for _, info in res["B1"]]
    if seg_dir == "down" and n - 1 > s0[2]:
        uh = units + [("bot", ext_px, kx, n - 1)]
        ev_h, U_h, piv_h = analyze2(uh, dif, bar)
        keys0 = {(t, x.get("e_idx")) for t, _, x in ev_f}
        for t, av, x in ev_h:
            if t in ("B3", "B1") and x.get("e_idx") == kx and (t, kx) not in keys0:
                pend.append(dict(typ={"B3": "三买", "B1": "一买"}[t], lo=toraw(ext_px), ZG=toraw(x["ZG"]) if "ZG" in x else None,
                                 ZD=toraw(x["ZD"]) if "ZD" in x else None, areaA=x.get("areaA"), areaC=x.get("areaC")))
        if len(uh) >= 3:
            for info in b1_infos:
                p1 = len(uh) - 3
                P1, P2 = uh[p1], uh[p1 + 1]
                if P1[0] == "bot" and P1[2] == info["e_idx"] and P2[0] == "top":
                    pend.append(dict(typ="二买" if ext_px > P1[1] else "二买(破一买)", lo=toraw(ext_px), P1=toraw(P1[1]), P2=toraw(P2[1])))
        # 向上离开中枢后的这次回调已跌破 ZG → 这一回调做不成三买（回到中枢里或跌穿）
        pre_fail = None
        if piv_h:
            pv = piv_h[-1]
            jh = len(U_h) - 1                                # 假设单元（本次回调）的下标
            hit = (pv["end"] is None and pv["last"] >= jh) or (pv["end"] == "BRK" and pv["brk"] == jh)
            if hit and jh >= 1 and U_h[jh - 1][0] == 1 and U_h[jh - 1][2] > pv["ZG"] and ext_px < pv["ZG"]:
                pre_fail = dict(ZG=toraw(pv["ZG"]), ZD=toraw(pv["ZD"]), broke_zd=bool(ext_px < pv["ZD"]))
    else:
        pre_fail = None
    # 预备（进行中线段向上：等它走完后的回调）
    prep = []
    if seg_dir == "up" and n - 1 > s0[2]:
        uh1 = units + [("top", ext_px, kx, n - 1)]
        ev_h1, U_h1, piv_h1 = analyze2(uh1, dif, bar)
        if piv_h1:
            pv = piv_h1[-1]
            jn = len(U_h1)                                   # 下一个（向下）线段的下标
            if pv["end"] is None and jn >= pv["i0"] + 4 and U_h1[-1][2] > pv["ZG"]:
                prep.append(dict(typ="三买预备", ZG=toraw(pv["ZG"]), ZD=toraw(pv["ZD"]), hi=toraw(ext_px)))
            elif pv["end"] is None and U_h1[-1][2] <= pv["ZG"]:
                prep.append(dict(typ="中枢内向上", ZG=toraw(pv["ZG"]), ZD=toraw(pv["ZD"]), hi=toraw(ext_px)))
        for info in b1_infos:
            if units[-1][0] == "bot" and units[-1][2] == info["e_idx"]:
                prep.append(dict(typ="二买预备", P1=toraw(units[-1][1]), hi=toraw(ext_px)))
    # 进行中线段的确认进度（向下线段：低点后须 上-下-上 三笔且第二个上笔高过 r1）
    prog = dict(dir=seg_dir, start=toraw(s0[1]), start_bar=bar_s(s0[2]), ext=toraw(ext_px), ext_bar=bar_s(kx), npens=len(rest) - 1,
                pens_to_best=pens_to_best, best_px=toraw(rest[best][1]) if best is not None else None,
                r1=toraw(r1) if r1 is not None else None, after=[(p[0], toraw(p[1]), bar_s(p[2])) for p in after])
    # 进行中线段的极值是否已是笔端点
    prog["ext_is_pen"] = bool(best is not None and abs(rest[best][1] - ext_px) < 1e-9)
    # 价格是否已越过进行中线段的起点（向下段：最高价高过起点顶；向上段：最低价跌破起点底）→ 前一线段可能延续
    if seg_dir == "down":
        prog["beyond_start"] = bool(float(np.max(h[a0:])) > s0[1]) if n - 1 > s0[2] else False
    else:
        prog["beyond_start"] = bool(float(np.min(l[a0:])) < s0[1]) if n - 1 > s0[2] else False
    # 闸门预检（临近买点）：失效价＝假设极值×0.99；给"能买的确认价区间"，再和"确认时价格至少多少"比
    for p in pend:
        stop = p["lo"] * 0.99
        p["stop"] = stop
        # 确认时价格下限：向下段的低点已成笔且其后已有向上笔 r1 → 确认要等第二个向上笔高过 r1，买价约 ≥ r1；否则至少是现价
        conf_lo = prog["r1"] if (seg_dir == "down" and prog["ext_is_pen"] and prog["r1"] is not None) else last_raw
        p["conf_lo"] = conf_lo
        gw = gate_window(st, code, stop, regime)
        p["gate"] = gw
        p["exec_view"] = ("准入不过：" + "、".join(gw["pre"])) if gw["pre"] else (
            "%s下无解（带宽要求买价≥%.2f，但%s只允许≤%.2f）" % (CN[regime], gw["lo_e"], gw["why_hi"], gw["hi_e"]) if gw["lo_e"] > gw["hi_e"] else (
                "确认价至少%.2f，已高于可买上限%.2f（%s）" % (conf_lo, gw["hi_e"], gw["why_hi"]) if conf_lo > gw["hi_e"] else
                "确认价落在 %.2f—%.2f 才买得了" % (max(gw["lo_e"], 0), gw["hi_e"])))
    # 价格已越过进行中线段起点时，向上段的"预备"判断不成立（这一段实际已坏），不列
    if prog.get("beyond_start"):
        prep = []
    # 强弱打分（6 项，0—6；仅观察用，未回测）
    sc_items = dict(above_ma20=bool(last_adj > ma20), ma20_gt_ma60=bool(ma20 > ma60), dif_pos=bool(dif[-1] > 0), dif_gt_dea=bool(dif[-1] > dea[-1]),
                    above_zg=bool(pv_last is not None and last_adj > pv_last["ZG"]), seg_up=bool(seg_dir == "up"))
    score = sum(sc_items.values()) - (1 if (pv_last is not None and last_adj < pv_last["ZD"]) else 0)
    # 当前档位下"这只票不管出什么买点能不能买"：带宽要求止损距离≥近5日平均振幅，风险预算要求止损距离≤预算/一手 → 振幅大于后者就无解
    lot = 200 if board_of(code) == "star" else 100
    budget = RISK * MULT[regime] * NAV
    fw = []
    if amp5 > budget / lot: fw.append("振幅%.2f元>预算/一手%.2f元" % (amp5, budget / lot))
    if last_raw > CEIL[board_of(code)]: fw.append("价%.2f>1.5a上限%.2f" % (last_raw, CEIL[board_of(code)]))
    if amt20 < 1e8: fw.append("20日均额不足1亿")
    if i_prev + 1 < 250: fw.append("上市不满250日")
    if code in COOLING: fw.append("冷却期(视同)")
    out = dict(code=code, name=NAMES[code], board=board_of(code), clusters=CLUSTERS.get(code, []), last_bar=bar_s(n - 1), n=n,
               look_start=udates[-A.look], look_start10=udates[-10],
               last=last_raw, pclose=pclose, pct=last_raw / pclose - 1, ret5=ret5, ret10=ret10,
               ma20=toraw(ma20), ma60=toraw(ma60), dif=float(dif[-1]), dea=float(dea[-1]), hist=float(hist[-1]),
               amp5=amp5, amt20=amt20, n_daily=i_prev + 1, ceil=CEIL[board_of(code)],
               pivot=dict(ZG=toraw(pv_last["ZG"]), ZD=toraw(pv_last["ZD"]), GG=toraw(pv_last["GG"]), DD=toraw(pv_last["DD"]), end=pv_last["end"],
                          start_bar=bar_s(pv_last["first_s"]), formed_bar=bar_s(pv_last["avail"]) if pv_last["avail"] < n else None) if pv_last else None,
               pivot_prev=dict(ZG=toraw(pv_prev["ZG"]), ZD=toraw(pv_prev["ZD"]), end=pv_prev["end"]) if pv_prev else None,
               pos=pos, trend=trend, prog=prog, pend=pend, pre_fail=pre_fail, prep=prep,
               recent_buys=recent_buys, recent_sells=recent_sells, score=score, sc_items=sc_items,
               n_seg=len(units), revoked=res["revoked"], off_ok=off_ok, off_units=res_off["n_units"], fixed=res["fixed"],
               stuck_at=bar_s(stuck_k) if stuck_k is not None else None,
               n_ev_off=len(ev_sig(res_off)), n_ev=len(ev_sig(res)), pre_fix_stuck=bool(res.get("fixed", 0) > 0),
               feas_any=not fw, feas_why=fw, lot=lot, budget=budget,
               zg_dist=(last_adj / pv_last["ZG"] - 1) if pv_last else None, zd_dist=(last_adj / pv_last["ZD"] - 1) if pv_last else None)
    return out


if __name__ == "__main__":
    P.require_data(CODES, need_m30=True)
    t0 = time.time()
    if not A.no_fetch and not A.asof: fetch_all()
    day = NOW.strftime("%Y-%m-%d")
    regime, rsrc = regime_before(day)
    rows = []; errs = []
    for code in CODES:
        try: rows.append(analyze_stock(code, regime))
        except Exception as e:  # noqa
            import traceback; errs.append([code, traceback.format_exc()[-600:]])
    last_bars = sorted({r["last_bar"] for r in rows})
    ls10 = sorted({r["look_start10"] for r in rows})[-1] if rows else None
    ls = sorted({r["look_start"] for r in rows})[-1] if rows else None
    res = dict(look_start10=ls10, look_start=ls, asof=NOW.strftime("%Y-%m-%d %H:%M:%S"), regime=regime, regime_cn=CN[regime], regime_src=rsrc, cap=CAP[regime], mult=MULT[regime], nav=NAV,
               start=START, pool_from=POOL_FROM, look=A.look, health=HEALTH, fetched=not A.no_fetch and not A.asof, last_bars=last_bars, errs=errs, rows=rows, secs=round(time.time() - t0, 1))
    res.update(base_end=BASE_END, cooling=COOLING, stage_real=CFG["stage_real"])
    if PW_LABEL: res.update(label=PW_LABEL, state_dir=PW_STATE, out_dir=PW_OUT, pool_file=PW_POOL)       # 第二个池才带这几个字段，关注池 JSON 与原来逐字段相同
    tag = A.out or NOW.strftime("%Y%m%d_%H%M")
    fp = os.path.join(OUTD, "pool_watch_%s.json" % tag)
    json.dump(res, open(fp, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    print("pool_watch %s｜%d只｜最后K线 %s｜取数 %s 失败%d｜错误%d｜%.1fs" % (NOW.strftime("%m-%d %H:%M"), len(rows), last_bars[-3:], HEALTH["src"] or "未联网", len(HEALTH["fail"]), len(errs), time.time() - t0))
    for r in rows:
        if r["pend"] or r["recent_buys"]:
            print(r["code"], r["name"], [p["typ"] for p in r["pend"]], [b["typ"] + "@" + b["bar"] for b in r["recent_buys"]])
    print("JSON:", fp)

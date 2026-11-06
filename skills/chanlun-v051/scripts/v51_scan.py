#!/usr/bin/env python3
"""V5.1 实时回测扫描 + 记账（纸面性质，不下真单）。用法：python3 v51_scan.py   （环境变量 V51_HOME 指向工作区）
每次运行：取股票池 30 分钟线（新浪主、东财备）→ 与历史库拼接 → S 引擎（sop_run.run_series）→ 从起点日起按 V5.1 第 6—8 章逐根重放
（成交＝下一根开盘±10bp、八条闸门、四个卖出条件、T+1、跌停顺延、档位、熔断、冷却期、主题簇 30%）→ 写账本与报告。
重放是确定性的：同一份数据同一结果，所以补跑/漏跑不会丢记录；首次发现某信号的时刻写进 signals_log.jsonl（量滞后）。
用法：v51_scan.py [--no-fetch] [--asof "YYYY-MM-DD HH:MM"] [--start YYYY-MM-DD] [--tag NAME]
  --asof/--start/--tag 用于历史回放自检，输出写 live/state_<tag>/ 与 live/reports_<tag>/，不碰正式账本。
第二个池：环境变量 V51_POOL_FILE（名单文件名，默认 watchlist.md）、V51_CFG（配置文件名，默认 v51_config.json）、
  V51_TAG（默认输出标签）、V51_LABEL（报告标题标注）。全部不设＝第一个池；重点池由包装脚本 v51_scan_focus.py 设置。
输出（stdout，定时任务据此判断）：NEW_SIGNAL＝本根新确认的买点；PENDING＝下一根开盘待执行单；FILL＝纸面成交。"""
import os, sys
import re, json, math, csv, time, argparse, datetime as dt
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "engine"))
import _paths as P
import _common as C
import _fetch as F
import sop_run as R
import hashlib
ENGINE_MD5 = hashlib.md5(open(os.path.join(P.ENGINE_DIR, "sop_engine.py"), "rb").read()).hexdigest()

POOL_FILE = os.environ.get("V51_POOL_FILE") or "watchlist.md"      # 第二个池（重点池）：见 v51_scan_focus.py
CFG_FILE = os.environ.get("V51_CFG") or "v51_config.json"
POOL_LABEL = os.environ.get("V51_LABEL") or ""
ap = argparse.ArgumentParser()
ap.add_argument("--no-fetch", action="store_true"); ap.add_argument("--asof"); ap.add_argument("--start"); ap.add_argument("--tag", default=os.environ.get("V51_TAG") or "")
A = ap.parse_args()
CFG = P.load_config(CFG_FILE)
NOW = dt.datetime.strptime(A.asof, "%Y-%m-%d %H:%M") if A.asof else dt.datetime.now()
START = A.start or CFG["start_date"]
SUF = ("_" + A.tag) if A.tag else ""
STATE = os.path.join(P.LIVE, "state" + SUF); REPORTS = os.path.join(P.LIVE, "reports" + SUF)
EXT = os.path.join(STATE, "m30_ext")
for p in (STATE, REPORTS, EXT): os.makedirs(p, exist_ok=True)
LIVE = P.DAILY; ALL = P.DAILY_HIST
M30 = P.M30; REC = P.M30_RECENT
PIPE = P.PIPE
BASE_END = CFG["m30_base_end"]    # baostock 历史库（含 m30_recent）覆盖到此日；其后的 K 线来自实时源，存 state/m30_ext
SLIP, BUYF, STAMP = C.SLIP, C.BUYF, C.STAMP
RISK, EXPO_MAX, CLUSTER, MAXPOS = C.RISK, C.EXPO_MAX, C.CLUSTER, C.MAXPOS
CAP, MULT, CN, SLOTS = C.CAP, C.MULT, C.CN, C.SLOTS


# ---------------- 名单、名称、主题簇 ----------------
def read_pool():
    names, clu = C.read_pool(POOL_FILE, overlay=True)
    return names, {c: set(v) for c, v in clu.items()}
NAMES, CLUSTERS = read_pool()
CODES = sorted(NAMES)


def read_pool_from():
    """名单表「加入日期」→ 入池生效时刻。V5.1 第 3 章：入池必须发生在信号出现之前，不得出现买点后再补进池里。"""
    return C.read_pool_from(POOL_FILE)
# 只管实时回测起点之后才入池的票（起点前入池的不受影响；情景回放 --start 提前时同样只限制这些票）
POOL_FROM = {c: v for c, v in read_pool_from().items() if c in NAMES and v[:10] >= CFG["start_date"]}


# ---------------- 取数：新浪主（实测稳）、东财备（实测约一半连接被断） ----------------
fetch_sina, fetch_em = F.fetch_sina, F.fetch_em

def complete(d, hhmm):
    return dt.datetime.strptime(d + hhmm, "%Y-%m-%d%H%M") + dt.timedelta(seconds=8) <= NOW

HEALTH = dict(src={}, fail=[], fetched=0)
def _one(code):
    last = ""
    for nm, fn in (("sina", fetch_sina), ("eastmoney", fetch_em)):
        for _ in range(2):
            try:
                return code, nm, fn(code), ""
            except Exception as e:  # noqa
                last = repr(e)[:80]; time.sleep(0.6)
    return code, None, None, last

def update_ext():
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=4) as ex:
        results = list(ex.map(_one, CODES))
    for code, nm, rows, last in results:
        if rows is None:
            HEALTH["fail"].append((code, last)); continue
        HEALTH["src"][nm] = HEALTH["src"].get(nm, 0) + 1
        p = os.path.join(EXT, code + ".csv")
        old = {}
        if os.path.exists(p):
            for r in csv.reader(open(p)):
                if r and r[0] != "date": old[(r[0], r[1])] = r
        n0 = len(old)
        for d, t, o, h, l, c, v, a in rows:
            if d > BASE_END and t in SLOTS and complete(d, t):
                old[(d, t)] = [d, t, "%.4f" % o, "%.4f" % h, "%.4f" % l, "%.4f" % c, "%d" % v, "%.2f" % a]
        with open(p + ".tmp", "w", newline="") as f:
            w = csv.writer(f); w.writerow(["date", "time", "open", "high", "low", "close", "volume", "amount"])
            for k in sorted(old): w.writerow(old[k])
        os.replace(p + ".tmp", p); HEALTH["fetched"] += len(old) - n0


# ---------------- 日线与 30 分钟载入 ----------------
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

class Stk:
    pass

def load_stock(code):
    s = Stk(); s.code = code; s.name = NAMES[code]
    s.d = load_daily(code)
    frames = []
    p1 = os.path.join(M30, code + ".csv.gz")
    if os.path.exists(p1): frames.append(pd.read_csv(p1))
    for pth in (os.path.join(REC, code + ".csv"), os.path.join(EXT, code + ".csv")):
        if os.path.exists(pth):
            df_ = pd.read_csv(pth)
            if len(df_): frames.append(df_)
    m = pd.concat(frames).drop_duplicates(["date", "time"], keep="last")
    m["time"] = m["time"].astype(int).map(lambda x: "%04d" % x)
    m = m[[complete(d, t) for d, t in zip(m["date"], m["time"])]].sort_values(["date", "time"]).reset_index(drop=True)
    dd = pd.to_datetime(m["date"])
    adj = s.d["adj"].reindex(dd.values).ffill().bfill().values
    s.adj = adj
    for c in ("open", "high", "low", "close"):
        m["r_" + c] = m[c].astype(float); m[c] = m[c].astype(float) * adj
    s.m = m; s.n = len(m)
    s.date = m["date"].values; s.time = m["time"].values
    s.lim = 0.20 if code.startswith(("300", "301", "688", "689")) else 0.10
    s.star = code.startswith(("688", "689")); s.board = "star" if s.star else ("gem" if code.startswith(("300", "301")) else "main")
    s.st = "ST" in s.name
    res = R.run_series(m.high.values, m.low.values, m.close.values, "S")
    ev = []
    for typ in ("B3", "B1", "B2c", "B2w", "S3", "S1"):
        for k, info in res[typ]:
            ev.append((int(k), typ, info))
    ev.sort(key=lambda x: x[0]); s.ev = ev
    s.dpos = {d: i for i, d in enumerate([str(x)[:10] for x in s.d.index])}
    s.dkeys = [str(x)[:10] for x in s.d.index]
    return s

def prev_daily_idx(s, date):
    """日线中严格早于 date 的最后一行的位置"""
    import bisect
    i = bisect.bisect_left(s.dkeys, date) - 1
    return i

def pclose_raw(s, date):
    i = prev_daily_idx(s, date)
    return float(s.d["raw_close"].iloc[i]) if i >= 0 else np.nan


# ---------------- 档位 ----------------
def regime_before(date):
    """date 之前最近一个交易日的收盘档位：取 pipeline/regime_YYYYMMDD.json 的 state_bt（日期 < date 的最新一份）"""
    return C.regime_before(date, PIPE)


def pool_from_txt(c):
    v = POOL_FROM[c]
    return v[:10] + (" 收盘后" if v[11:] == "2359" else " %s:%s" % (v[11:13], v[13:15]))


# ---------------- 重放 ----------------
def simulate(S):
    cfg = CFG; NAV0 = cfg["nav0"]; STG = cfg["stage_sim"]
    cal = sorted({d for s in S.values() for d in set(s.date)})
    cali = {d: i for i, d in enumerate(cal)}
    stamps = sorted({(d, t) for s in S.values() for d, t in zip(s.date, s.time) if d >= START})
    by_code_k = {c: {(s.date[k], s.time[k]): k for k in range(s.n)} for c, s in S.items()}
    ev_at = {c: {} for c in S}
    for c, s in S.items():
        for k, typ, info in s.ev: ev_at[c].setdefault(k, []).append((typ, info))
    cash = NAV0; pos = {}; orders = {}; last_sell = {}
    trades = []; skips = []; sigs = []; fills = []; eq_day = {}
    streak = 0; block_until = -1; freeze_until = -1; half_left = 0; dead = False; peak = NAV0; nfreeze = 0; warn = []
    lastpx = {}                                   # 最近收盘（原价）

    def nav_now():
        return cash + sum(p["shares"] * lastpx.get(c, p["entry"]) for c, p in pos.items())

    def sizing(s, entry, stop, date, prefix=""):
        """八条闸门里的 2/3/4/5/6/8；返回 (股数, 原因, 细节)。1/7 在调用处。"""
        di = cali[date]
        st, st_src = regime_before(date)
        mult, capr = MULT[st], min(EXPO_MAX, CAP[st])
        if capr <= 0 or mult <= 0: return 0, "档位禁开(" + CN[st] + ")", {}
        if dead or di <= freeze_until: return 0, "熔断:回撤冻结/本金底线", {}
        if di <= block_until: return 0, "熔断:连亏停开", {}
        if s.code in pos: return 0, "已持仓", {}
        if s.code in last_sell and di - cali.get(last_sell[s.code], -99) <= 3: return 0, "冷却期3日", {}
        if len(pos) >= MAXPOS: return 0, "持仓已满20只", {}
        dist = (entry - stop) / entry
        if dist > 0.12: return 0, "止损距离%.1f%%>12%%" % (100 * dist), {}
        i = prev_daily_idx(s, date)
        if i >= 4:
            seg = s.d.iloc[i - 4:i + 1]; amp5 = float(((seg["high"] - seg["low"]) / seg["adj"]).mean())
            if (entry - stop) < amp5: return 0, "带宽<1(止损距%.2f<5日振幅%.2f)" % (entry - stop, amp5), {}
        nav = nav_now(); f = 0.5 if half_left > 0 else 1.0
        per = entry - stop
        sh_risk = math.floor(RISK * mult * STG * f * nav / per)
        invested = sum(p["shares"] * lastpx.get(c, p["entry"]) for c, p in pos.items())
        capsingle = 0.20 if s.board == "main" else 0.10
        sh_room = math.floor((capr * nav - invested) / entry)
        cl_room = 10 ** 9
        for cn in CLUSTERS.get(s.code, ()):
            inv_c = sum(p["shares"] * lastpx.get(c, p["entry"]) for c, p in pos.items() if cn in CLUSTERS.get(c, ()))
            cl_room = min(cl_room, math.floor((CLUSTER * nav - inv_c) / entry))
        sh = min(sh_risk, math.floor(capsingle * nav / entry), sh_room, cl_room, math.floor(cash / (entry * (1 + BUYF))))
        sh = (sh if sh >= 200 else 0) if s.star else (sh // 100) * 100
        det = dict(state=st, state_src=st_src, mult=mult, cap=capr, nav=nav, sh_risk=sh_risk, sh_room=sh_room, cluster_room=cl_room, dist=dist)
        if sh <= 0:
            lot = 200 if s.star else 100
            why = "不足一手(风险预算股数%d)" % sh_risk if sh_risk < lot else ("档位仓位余量不足(余%d股)" % sh_room if sh_room < lot else "单票/簇/资金上限")
            return 0, why, det
        return sh, "OK", det

    def close_pos(c, s, k, px, why, date, hhmm):
        nonlocal cash, streak, block_until
        p = pos.pop(c)
        proceeds = p["shares"] * px * (1 - BUYF - STAMP)
        cash += proceeds; pnl = proceeds - p["cost"]
        risk = p["shares"] * (p["entry"] - p["stop"])
        trades.append(dict(code=c, name=s.name, typ=p["typ"], ent=p["ent_dt"], ext="%s %s" % (date, hhmm), entry=round(p["entry"], 3), exit=round(px, 3),
                           shares=p["shares"], pnl=round(pnl, 2), R=round(pnl / risk, 2) if risk > 0 else None, why=why, state=p["state"]))
        last_sell[c] = date
        if pnl < 0:
            streak += 1
            if streak >= 5: block_until = cali[date] + 5; streak = 0
        else: streak = 0

    for (date, hhmm) in stamps:
        di = cali[date]
        for c in CODES:
            s = S[c]; k = by_code_k[c].get((date, hhmm))
            if k is None: continue
            op = float(s.m["r_open"].iloc[k]); cl = float(s.m["r_close"].iloc[k])
            pc = pclose_raw(s, date)
            # A) 执行挂单（本根开盘）
            od = orders.get(c)
            if od and k >= od["k_exec"]:
                if od["kind"] == "sell":
                    p = pos.get(c)
                    if p and date > p["ent_date"] and not (not np.isnan(pc) and op <= round(pc * (1 - s.lim), 2) + 1e-6):
                        close_pos(c, s, k, op * (1 - SLIP), od["why"], date, hhmm); fills.append((date, hhmm, c, s.name, "卖", op * (1 - SLIP), od["why"])); orders.pop(c)
                    elif not p: orders.pop(c)
                    else: od["k_exec"] = k + 1                            # T+1 或跌停：顺延
                else:                                                     # buy
                    orders.pop(c)
                    sig = od
                    if op > sig["sig_close"] * 1.03: skips.append((sig["bar"], c, s.name, sig["typ"], "开盘高开>3%取消"))
                    elif not np.isnan(pc) and op >= round(pc * (1 + s.lim), 2) - 1e-6: skips.append((sig["bar"], c, s.name, sig["typ"], "开盘涨停取消"))
                    elif op <= sig["stop"]: skips.append((sig["bar"], c, s.name, sig["typ"], "开盘已破失效价取消"))
                    else:
                        entry = op * (1 + SLIP); stop = min(sig["stop"], entry * 0.99)
                        sh, why, det = sizing(s, entry, stop, date)
                        if sh <= 0: skips.append((sig["bar"], c, s.name, sig["typ"], why))
                        else:
                            cost = sh * entry * (1 + BUYF); cash -= cost
                            if half_left > 0: half_left -= 1
                            pos[c] = dict(shares=sh, entry=entry, stop=stop, stop_adj=stop * float(s.adj[k]), cost=cost, typ=sig["typ"], ent_date=date, ent_dt="%s %s" % (date, hhmm),
                                          ent_di=di, state=det["state"], sig_bar=sig["bar"])
                            fills.append((date, hhmm, c, s.name, "买", entry, "%s 档位%s 股数%d" % (sig["typ"], CN[det["state"]], sh)))
            lastpx[c] = cl
            # B) 收盘处理：持仓退出条件
            p = pos.get(c)
            if p and c not in orders:
                why = None
                if float(s.m["close"].iloc[k]) < p["stop_adj"]: why = "失效价止损"
                else:
                    for typ, info in ev_at[c].get(k, []):
                        if typ in ("S3", "S1"): why = "三卖" if typ == "S3" else "趋势一卖"
                    if why is None and hhmm == "1500" and di - p["ent_di"] >= cfg["max_hold_days"]: why = "持满%d日到期" % cfg["max_hold_days"]
                if why: orders[c] = dict(kind="sell", why=why, k_exec=k + 1, bar="%s %s" % (date, hhmm))
            # C) 新买点
            for typ, info in ev_at[c].get(k, []):
                if typ not in ("B3", "B1", "B2c", "B2w"): continue
                tag = {"B3": "三买", "B1": "一买", "B2c": "二买", "B2w": "二买(破一买)"}[typ]
                e = int(info["e_idx"]); adje = float(s.adj[min(e, s.n - 1)])
                stop_raw = info["lo"] * 0.99 / adje
                sg = dict(code=c, name=s.name, typ=tag, bar="%s %s" % (date, hhmm), sig_close=cl, stop=stop_raw, ext_bar="%s %s" % (s.date[e], s.time[e]), k=k, last=(k == s.n - 1))
                if c in POOL_FROM and sg["bar"] <= POOL_FROM[c]:   # 入池前确认的买点：不算系统信号、不挂单（V5.1 第 3 章）
                    skips.append((sg["bar"], c, s.name, tag, "入池前确认，不计（%s 入池）" % pool_from_txt(c))); continue
                sigs.append(sg)
                if c in pos: skips.append((sg["bar"], c, s.name, tag, "持有期间新买点不计")); continue
                if c in orders and orders[c]["kind"] == "buy": continue
                # 闸门1 准入（信号前一交易日）
                i = prev_daily_idx(s, date)
                amt20 = float(s.d["amount"].iloc[max(0, i - 19):i + 1].mean()) if i >= 0 else 0
                if s.st: skips.append((sg["bar"], c, s.name, tag, "ST")); continue
                if i + 1 < 250: skips.append((sg["bar"], c, s.name, tag, "上市不满250日")); continue
                if amt20 < 1e8: skips.append((sg["bar"], c, s.name, tag, "20日均额%.2f亿<1亿" % (amt20 / 1e8))); continue
                orders[c] = dict(kind="buy", typ=tag, sig_close=cl, stop=stop_raw, k_exec=k + 1, bar=sg["bar"], ext_bar=sg["ext_bar"])
        if hhmm == "1500":
            eq = nav_now(); eq_day[date] = eq
            if cali[date] == freeze_until + 1: peak = eq; half_left = 2
            peak = max(peak, eq); dd = eq / peak - 1
            if cali[date] > freeze_until and dd <= -0.10: freeze_until = cali[date] + 10; nfreeze += 1
            if eq / NAV0 <= 0.70: dead = True
    last_stamp = stamps[-1] if stamps else None
    return dict(cash=cash, pos=pos, orders=orders, trades=trades, skips=skips, sigs=sigs, fills=fills, eq_day=eq_day, lastpx=lastpx, streak=streak,
                block_until=block_until, freeze_until=freeze_until, nfreeze=nfreeze, dead=dead, peak=peak, half_left=half_left, cal=cal, cali=cali, last_stamp=last_stamp, nav=nav_now())


# ---------------- 报告 ----------------
def pending_precheck(S, sim):
    rows = []
    for c, od in sim["orders"].items():
        s = S[c]
        if od["kind"] == "buy":
            date = s.date[s.n - 1]
            nxt = od["k_exec"]
            if nxt < s.n: continue                    # 已有下一根（已被重放消化）
            ref = od["sig_close"]; entry = ref * (1 + SLIP); stop = min(od["stop"], entry * 0.99)
            nd = next_trade_date(S, date, s.time[s.n - 1])
            sh, why, det = pre_size(S, sim, s, entry, stop, nd)
            pc = float(s.m["r_close"].iloc[s.n - 1])
            rows.append(dict(kind="买", code=c, name=s.name, typ=od["typ"], bar=od["bar"], ref=ref, stop=stop, cancel_hi=round(ref * 1.03, 2),
                             limit_up=round(pc * (1 + s.lim), 2), shares=sh, why=why, state=det.get("state")))
        else:
            if od["k_exec"] >= s.n: rows.append(dict(kind="卖", code=c, name=s.name, why=od["why"], bar=od["bar"]))
    return rows

def next_trade_date(S, date, hhmm):
    cal = sim_cal
    return date if hhmm != "1500" else (cal[cal.index(date) + 1] if date in cal and cal.index(date) + 1 < len(cal) else date)

def pre_size(S, sim, s, entry, stop, date):
    # 预检：用最后状态近似（仅用于待执行单展示，不改账）
    st, src = regime_before(date if date else s.date[s.n - 1])
    mult, capr = MULT[st], min(EXPO_MAX, CAP[st])
    det = dict(state=st)
    if capr <= 0 or mult <= 0: return 0, "档位禁开(" + CN[st] + ")", det
    nav = sim["nav"]; dist = (entry - stop) / entry
    if dist > 0.12: return 0, "止损距离%.1f%%>12%%" % (100 * dist), det
    i = len(s.d) - 1
    if i >= 4:
        seg = s.d.iloc[i - 4:i + 1]; amp5 = float(((seg["high"] - seg["low"]) / seg["adj"]).mean())
        if (entry - stop) < amp5: return 0, "带宽<1(止损距%.2f<5日振幅%.2f)" % (entry - stop, amp5), det
    invested = sum(p["shares"] * sim["lastpx"].get(c, p["entry"]) for c, p in sim["pos"].items())
    capsingle = 0.20 if s.board == "main" else 0.10
    sh_risk = math.floor(RISK * mult * CFG["stage_sim"] * nav / (entry - stop)); sh_room = math.floor((capr * nav - invested) / entry)
    sh = min(sh_risk, math.floor(capsingle * nav / entry), sh_room, math.floor(sim["cash"] / (entry * (1 + BUYF))))
    sh = (sh if sh >= 200 else 0) if s.star else (sh // 100) * 100
    det.update(sh_risk=sh_risk, sh_room=sh_room)
    return sh, ("OK" if sh > 0 else ("不足一手(风险预算%d股)" % sh_risk if sh_risk < (200 if s.star else 100) else "档位仓位余量不足(余%d股)" % sh_room)), det


def write_outputs(S, sim, pend):
    day = NOW.strftime("%Y-%m-%d")
    last_dt = "%s %s" % sim["last_stamp"] if sim["last_stamp"] else "无"
    st_prev, src = regime_before(day if NOW.time() >= dt.time(0, 0) else day)
    stale = []
    for c in CODES:
        s = S[c]
        if s.n == 0 or s.date[s.n - 1] < max(S[x].date[S[x].n - 1] for x in CODES): stale.append(c)
    L = ["# V5.1 实时回测日报%s %s（%s 运行）" % (("（" + POOL_LABEL + "）") if POOL_LABEL else "", day, NOW.strftime("%H:%M")), "",
         "性质：纸面，不下真单。阶段系数：真实下单 %s（只记录）；记账口径按满额 %s 计，起点 %s，账户本金 %.0f。" % (CFG["stage_real"], CFG["stage_sim"], START, CFG["nav0"]),
         "引擎：sop_engine.py md5 %s（含\"线段端点被延伸后卡死\"缺陷修复），池内 %d 只。" % (ENGINE_MD5[:8], len(CODES)), ""]
    if POOL_FROM:
        L += ["池子变动：" + "；".join("%s %s %s 入池，之前确认的买点不计" % (c, NAMES[c], pool_from_txt(c)) for c in sorted(POOL_FROM)) + "（V5.1 第 3 章：入池必须发生在信号出现之前）。", ""]
    L += [
         "## 数据健康", "最后一根 K 线：%s；取数源：%s；失败 %d 只%s；本次新增 K 线 %d 根；落后于最新 K 线的票 %d 只%s。" % (
             last_dt, HEALTH["src"] or "(未联网，用已存数据)", len(HEALTH["fail"]), ("（" + "、".join(c for c, _ in HEALTH["fail"][:6]) + "）") if HEALTH["fail"] else "",
             HEALTH["fetched"], len(stale), ("（" + "、".join(NAMES[c] for c in stale[:8]) + "）") if stale else ""), "",
         "## 档位（前一交易日收盘，来源 .pipeline/买点C_%s.json）" % (src or "无"), "%s｜总仓位上限 %d%%｜风险倍率 %.2f" % (CN[st_prev], 100 * min(EXPO_MAX, CAP[st_prev]), MULT[st_prev]), ""]
    today_sigs = [x for x in sim["sigs"] if x["bar"].startswith(day)]
    L += ["## 今日新确认的买点（%d 个）" % len(today_sigs)]
    if today_sigs:
        L += ["| 确认时刻 | 股票 | 类别 | 结构极值点 | 信号收盘 | 失效价 | 处置 |", "|---|---|---|---|---:|---:|---|"]
        sk = {(a[0], a[1], a[3]): a[4] for a in sim["skips"]}
        for x in today_sigs:
            fate = sk.get((x["bar"], x["code"], x["typ"]), "已挂单/已成交")
            L.append("| %s | %s %s | %s | %s | %.2f | %.2f | %s |" % (x["bar"], x["code"], x["name"], x["typ"], x["ext_bar"], x["sig_close"], x["stop"], fate))
    else: L.append("无。")
    L += ["", "## 待执行单（下一根 K 线开盘）"]
    if pend:
        L += ["| 方向 | 股票 | 说明 | 参考价 | 失效价 | 取消条件 | 记账股数(满额口径) | 预检 |", "|---|---|---|---:|---:|---|---:|---|"]
        for r in pend:
            if r["kind"] == "买":
                L.append("| 买 | %s %s | %s（%s确认） | %.2f | %.2f | 开>%.2f 或 ≥涨停%.2f 或 ≤失效价 | %d | %s |" % (r["code"], r["name"], r["typ"], r["bar"], r["ref"], r["stop"], r["cancel_hi"], r["limit_up"], r["shares"], r["why"]))
            else: L.append("| 卖 | %s %s | %s（%s触发） |  |  | 跌停开盘顺延；买入当日不卖 |  |  |" % (r["code"], r["name"], r["why"], r["bar"]))
    else: L.append("无。")
    L += ["", "## 今日成交（纸面）"]
    tf = [f for f in sim["fills"] if f[0] == day]
    L += (["| 时刻 | 股票 | 方向 | 价 | 说明 |", "|---|---|---|---:|---|"] + ["| %s %s | %s %s | %s | %.3f | %s |" % (f[0], f[1], f[2], f[3], f[4], f[5], f[6]) for f in tf]) if tf else ["无。"]
    L += ["", "## 持仓与失效价"]
    if sim["pos"]:
        L += ["| 股票 | 买点 | 买入时刻 | 成本 | 股数 | 现价 | 浮盈 | 失效价(原价) | 距失效价 |", "|---|---|---|---:|---:|---:|---:|---:|---:|"]
        for c, p in sim["pos"].items():
            px = sim["lastpx"].get(c, p["entry"]); stop_now = p["stop_adj"] / float(S[c].adj[S[c].n - 1])   # 除权后按 7.6 换算的当前失效价
            L.append("| %s %s | %s | %s | %.3f | %d | %.2f | %+.1f%% | %.2f | %.1f%% |" % (c, NAMES[c], p["typ"], p["ent_dt"], p["entry"], p["shares"], px, 100 * (px / p["entry"] - 1), stop_now, 100 * (px / stop_now - 1)))
    else: L.append("无持仓。")
    eq = sim["nav"]; dd = eq / sim["peak"] - 1
    L += ["", "## 账户", "净值 %.0f（本金 %.0f，%+.2f%%）｜历史高点回撤 %.2f%%｜已平仓 %d 笔｜连亏计数 %d｜熔断：%s" % (
        eq, CFG["nav0"], 100 * (eq / CFG["nav0"] - 1), 100 * dd, len(sim["trades"]), sim["streak"],
        "本金底线停用" if sim["dead"] else ("回撤冻结至交易日序 %d" % sim["freeze_until"] if sim["freeze_until"] >= sim["cali"].get(day, -1) else "无")), ""]
    if sim["trades"]:
        L += ["| 买入 | 卖出 | 股票 | 买点 | 买价 | 卖价 | R | 原因 |", "|---|---|---|---|---:|---:|---:|---|"] + ["| %s | %s | %s %s | %s | %.3f | %.3f | %s | %s |" % (t["ent"], t["ext"], t["code"], t["name"], t["typ"], t["entry"], t["exit"], t["R"], t["why"]) for t in sim["trades"][-15:]]
    L += ["", "## 被闸门拦下/取消的信号（最近20条）"]
    L += (["| 确认时刻 | 股票 | 类别 | 原因 |", "|---|---|---|---|"] + ["| %s | %s %s | %s | %s |" % (a[0], a[1], a[2], a[3], a[4]) for a in sim["skips"][-20:]]) if sim["skips"] else ["无。"]
    L += ["", "说明：成交价＝下一根开盘±10bp；买入手续费万2.6，卖出万2.6＋印花税万5；起点前的信号不计；按回测口径，同一根出现多个买点按代码顺序处理；盘中数据只用已收盘 K 线。"]
    # 首次发现时刻日志（1011 增加：bar_close_at=该根K线收盘时刻，delay_min=首次发现距收盘的分钟数；回测假设下一根开盘即成交＝0 延迟，>30 分钟即超过一根 K 线，回测数字不再适用）
    lp = os.path.join(STATE, "signals_log.jsonl"); seen = set()
    if os.path.exists(lp):
        for ln in open(lp, encoding="utf-8"):
            try: j = json.loads(ln); seen.add((j["code"], j["typ"], j["bar"]))
            except Exception: pass
    with open(lp, "a", encoding="utf-8") as f:
        for x in sim["sigs"]:
            key = (x["code"], x["typ"], x["bar"])
            if key not in seen and x["bar"] >= START:
                bc = dt.datetime.strptime(x["bar"].replace(" ", ""), "%Y-%m-%d%H%M")
                dm = round((NOW - bc).total_seconds() / 60.0, 1)
                f.write(json.dumps(dict(code=x["code"], name=x["name"], typ=x["typ"], bar=x["bar"], sig_close=x["sig_close"], stop=x["stop"], detected_at=NOW.strftime("%Y-%m-%d %H:%M:%S"),
                                        bar_close_at=bc.strftime("%Y-%m-%d %H:%M:%S"), delay_min=dm, replay=bool(A.asof)), ensure_ascii=False) + "\n")
    dl = []
    for ln in open(lp, encoding="utf-8"):
        try:
            j = json.loads(ln)
            if "delay_min" in j and not j.get("replay"): dl.append(j)
        except Exception: pass
    L += ["", "## 信号延迟记录（累计，实时扫描首次发现距该根 K 线收盘）"]
    if dl:
        ds = sorted(x["delay_min"] for x in dl)
        L += ["已记录 %d 个实时信号；延迟中位 %.1f 分钟、最大 %.1f 分钟；超过 30 分钟（一根 K 线）的 %d 个。回测假设信号确认后下一根开盘即成交（0 延迟），回测里（关注池、引擎修复后口径）晚 1 根 K 线买入年化 7.5%%→5.5%%、晚 2 根→5.6%%，所以超过 30 分钟的信号要在执行卡上单独标注。" % (
            len(ds), ds[len(ds) // 2], ds[-1], sum(1 for x in ds if x > 30)), "", "| 确认K线 | 股票 | 买点 | 首次发现 | 延迟(分钟) |", "|---|---|---|---|---:|"]
        L += ["| %s | %s %s | %s | %s | %.1f |" % (x["bar"], x["code"], x["name"], x["typ"], x["detected_at"][5:16], x["delay_min"]) for x in dl[-10:]]
    else:
        L.append("暂无实时信号记录（历史回放不计入）。")
    open(os.path.join(REPORTS, "V51_%s.md" % day.replace("-", "")), "w", encoding="utf-8").write("\n".join(L))
    open(os.path.join(REPORTS, "V51_latest.md"), "w", encoding="utf-8").write("\n".join(L))
    led = dict(asof=NOW.strftime("%Y-%m-%d %H:%M:%S"), start=START, cfg=CFG, nav=sim["nav"], cash=sim["cash"], peak=sim["peak"], streak=sim["streak"], dead=sim["dead"],
               positions={c: {k: (round(v, 4) if isinstance(v, float) else v) for k, v in p.items()} for c, p in sim["pos"].items()},
               pending=pend, trades=sim["trades"], fills=sim["fills"], signals=sim["sigs"], skips=sim["skips"], equity_by_day=sim["eq_day"], health=dict(HEALTH, stale=stale))
    if POOL_LABEL: led["pool"] = dict(label=POOL_LABEL, file=POOL_FILE, n=len(CODES))
    json.dump(led, open(os.path.join(STATE, "v51_ledger.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    return today_sigs, tf


if __name__ == "__main__":
    t0 = time.time()
    if not A.no_fetch and not A.asof: update_ext()
    P.require_data(CODES, need_m30=True)
    S = {c: load_stock(c) for c in CODES}
    sim = simulate(S); sim_cal = sim["cal"]
    pend = pending_precheck(S, sim)
    today_sigs, tf = write_outputs(S, sim, pend)
    day = NOW.strftime("%Y-%m-%d")
    print("V51 扫描%s %s｜池%d只｜最后K线 %s｜取数 %s 失败%d｜%.1fs" % (("[" + POOL_LABEL + "]") if POOL_LABEL else "", NOW.strftime("%m-%d %H:%M"), len(CODES), ("%s %s" % sim["last_stamp"]) if sim["last_stamp"] else "无", HEALTH["src"] or "未联网", len(HEALTH["fail"]), time.time() - t0))
    print("今日新买点 %d｜待执行 %d｜今日成交 %d｜持仓 %d｜净值 %.0f｜已平仓 %d" % (len(today_sigs), len(pend), len(tf), len(sim["pos"]), sim["nav"], len(sim["trades"])))
    for x in today_sigs: print("NEW_SIGNAL", x["bar"], x["code"], x["name"], x["typ"], "失效价%.2f" % x["stop"])
    for r in pend: print("PENDING", r)
    for f in tf: print("FILL", f)
    print("报告：", os.path.join(REPORTS, "V51_latest.md"))

#!/usr/bin/env python3
"""V5.1 账户情景回放：假设纸面账户从更早的某天就开始按 V5.1 规则跑，看"这一周"它持有什么、赚亏多少。
规则、引擎、成交、闸门、卖出、熔断全部用 v51_scan.py 的原函数；唯一的改动是档位：
  有 pipeline/regime_YYYYMMDD.json 的日子用实时扫描同一套文件；更早的日子用 pipeline/regime_history.csv
  （先跑 python3 regime_daily.py --history 生成，窗口内每一天的收盘档位）。
只写 live/state_<TAG>/、live/reports_<TAG>/，不碰正式账本。
用法：week_replay.py START ASOF TAG [WEEK0]   例：week_replay.py YYYY-MM-DD "YYYY-MM-DD HH:MM" wk1y
第二个池：先设环境变量 V51_POOL_FILE / V51_CFG / V51_TAG（同 v51_scan.py），本脚本会从对应的 live/state_<V51_TAG>/m30_ext 取数据。
注意：回放是"事后凑的起点"情景，不进正式账本；关注池是事后挑出来的票，回放收益有挑票偏差。"""
import os, sys, json, shutil, bisect
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _paths as P
import _common as C
if len(sys.argv) < 4:
    raise SystemExit("用法：week_replay.py START ASOF TAG [WEEK0]\n  例：week_replay.py YYYY-MM-DD \"YYYY-MM-DD HH:MM\" wk1y      （ASOF 带空格，要加引号；格式 YYYY-MM-DD HH:MM）")
START, ASOF, TAG = sys.argv[1], sys.argv[2], sys.argv[3]
import datetime as _d
try:
    _d.datetime.strptime(ASOF, "%Y-%m-%d %H:%M")
except ValueError:
    raise SystemExit("ASOF 格式应为 \"YYYY-MM-DD HH:MM\"（要加引号），收到：%r" % ASOF)
WEEK0 = sys.argv[4] if len(sys.argv) > 4 else None
SRC_STATE = "state" + (("_" + os.environ["V51_TAG"]) if os.environ.get("V51_TAG") else "")
st_dir = os.path.join(P.LIVE, "state_" + TAG)
if os.path.exists(st_dir):
    shutil.rmtree(st_dir)
os.makedirs(st_dir)
shutil.copytree(os.path.join(P.LIVE, SRC_STATE, "m30_ext"), os.path.join(st_dir, "m30_ext"))
sys.argv = ["v51_scan.py", "--no-fetch", "--asof", ASOF, "--start", START, "--tag", TAG]
import pandas as pd
import v51_scan as V

HIST = os.path.join(P.PIPE, "regime_history.csv")
RD, RE = [], []
if os.path.exists(HIST):
    h = pd.read_csv(HIST)
    RD, RE = h["date"].tolist(), [C.regime_key(x) for x in h["state"].tolist()]
FILES = C.regime_files(P.PIPE)
FIRST_JSON = (FILES[0][0][:4] + "-" + FILES[0][0][4:6] + "-" + FILES[0][0][6:]) if FILES else "9999-99-99"
_orig = V.regime_before


def regime_before(date):
    if RD and date <= FIRST_JSON:                  # 第一份档位文件之前：用历史序列（取日期 < date 的最近一天）
        i = bisect.bisect_left(RD, date) - 1
        return (RE[i] if i >= 0 else "REPAIR"), "hist:" + (RD[i] if i >= 0 else "none")
    return _orig(date)


V.regime_before = regime_before
S = {c: V.load_stock(c) for c in V.CODES}
sim = V.simulate(S)
V.sim_cal = sim["cal"]
pend = V.pending_precheck(S, sim)
V.write_outputs(S, sim, pend)
if WEEK0 is None:                                  # 默认＝ASOF 所在周的周一
    import datetime as _dt
    d0 = _dt.datetime.strptime(ASOF[:10], "%Y-%m-%d").date()
    WEEK0 = str(d0 - _dt.timedelta(days=d0.weekday()))

# ---------- 这一周 ----------
eq = sim["eq_day"]
days = sorted(eq)
base_day = max(d for d in days if d < WEEK0)
eq0 = eq[base_day]; eq_now = sim["nav"]
wk_tr = [t for t in sim["trades"] if t["ext"][:10] >= WEEK0]
wk_fill = [f for f in sim["fills"] if f[0] >= WEEK0]
wk_sig = [s for s in sim["sigs"] if s["bar"][:10] >= WEEK0]
wk_skip = [s for s in sim["skips"] if s[0][:10] >= WEEK0]
pos = []
for c, p in sim["pos"].items():
    s = S[c]
    px = sim["lastpx"].get(c, p["entry"])
    # 周初（上周五收盘）的价，若这周才买则用买价
    i0 = V.prev_daily_idx(s, WEEK0)
    p0 = float(s.d["raw_close"].iloc[i0]) if p["ent_date"] < WEEK0 else p["entry"]
    pos.append(dict(code=c, name=s.name, typ=p["typ"], ent=p["ent_dt"], entry=round(p["entry"], 3), shares=p["shares"], px=px,
                    wk_pnl=round(p["shares"] * (px - p0), 2), total_pnl=round(p["shares"] * px - p["cost"], 2),
                    stop=round(p["stop_adj"] / float(s.adj[s.n - 1]), 3)))
# 周内平仓单的本周盈亏：周初持有的按上周五收盘算起
for t in wk_tr:
    s = S[t["code"]]
    if t["ent"][:10] < WEEK0:
        i0 = V.prev_daily_idx(s, WEEK0); p0 = float(s.d["raw_close"].iloc[i0])
        t["wk_pnl"] = round(t["shares"] * (t["exit"] * (1 - V.BUYF - V.STAMP) - p0), 2)
    else:
        t["wk_pnl"] = t["pnl"]
# 基准：关注池 64 只等权（上周五收盘 → 最新已收盘 30 分钟 K 线，不复权价）
rets = []
for c, s in S.items():
    i0 = V.prev_daily_idx(s, WEEK0)
    p0 = float(s.d["raw_close"].iloc[i0]); p1 = float(s.m["r_close"].iloc[s.n - 1])
    if p0 > 0:
        rets.append(p1 / p0 - 1)
out = dict(start=START, asof=ASOF, week0=WEEK0, base_day=base_day, eq0=eq0, eq_now=eq_now, wk_ret=eq_now / eq0 - 1,
           total_ret=eq_now / V.CFG["nav0"] - 1, n_trades_all=len(sim["trades"]), n_sig_all=len(sim["sigs"]),
           week_trades=wk_tr, week_fills=wk_fill, week_signals=wk_sig, week_skips=wk_skip, positions=pos,
           pool_ew=sum(rets) / len(rets), pool_n=len(rets), eq_days={d: eq[d] for d in days if d >= base_day}, last_stamp=sim["last_stamp"])
json.dump(out, open(os.path.join(st_dir, "week_summary.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
print(json.dumps({k: out[k] for k in ("start", "base_day", "eq0", "eq_now", "wk_ret", "total_ret", "n_trades_all", "pool_ew", "last_stamp")}, ensure_ascii=False, default=str))
print("本周成交", wk_fill); print("本周平仓", [(t["name"], t["typ"], t["ent"], t["ext"], t["R"], t["why"], t["wk_pnl"]) for t in wk_tr])
print("持仓", pos); print("本周信号", [(s["name"], s["typ"], s["bar"]) for s in wk_sig]); print("本周被拦", wk_skip)

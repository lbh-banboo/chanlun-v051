#!/usr/bin/env python3
"""回看检验："临近买点"（假设进行中的向下线段就在当前最低点结束、引擎会出买点）这种状态，事后有多少真的被确认。
逐日 15:00 快照（2023-01-03 起，到最后一天前 20 个交易日为止），引擎状态逐根推进（只用快照时刻之前的数据），
候选按 (类型, 回调线段起点) 归并成"一段"，检查 20 个交易日内有没有同一结构的正式确认（修正版引擎全量事件）。
用法：pool_watch_backcheck.py 分片号 分片数   → live/watch/backcheck_<分片>.json（分片＝把股票池按序号取模切开，可分几个进程并行；本机并发别超过 3 个）
报告 pool_watch_report.py 若在 JSON 同目录找到 backcheck_0.json，就把"临近买点多大比例会确认、要等几天"写进第二节。
证据（2023-01 起、64 只、修正版引擎）：临近三买 335 段 64% 在 20 个交易日内确认、中位 7 天；一买 95 段 67%、8 天；二买 69 段 78%、8 天。只验"会不会确认"，没验盈亏。"""
import os, sys, json, bisect
sys.argv, SH = [sys.argv[0], "--no-fetch"], (int(sys.argv[1]), int(sys.argv[2])) if len(sys.argv) > 2 else (0, 1)
HERE = os.path.dirname(os.path.abspath(__file__))
src = open(os.path.join(HERE, "pool_watch.py"), encoding="utf-8").read().split('if __name__ == "__main__":')[0]
exec(compile(src, "pool_watch", "exec"))
import numpy as np

T0, FOLLOW = "2023-01-03", 20
out = []
for ci, code in enumerate(CODES):
    if ci % SH[1] != SH[0]: continue
    d = load_daily(code); m = load_m30(code, d)
    n = len(m); h, l, c = m.high.values, m.low.values, m.close.values
    dates = m["date"].values; times = m["time"].values
    dif, bar = SE.macd_hist(c)
    res = run_series2(h, l, c)
    conf = {}
    for t in ("B3", "B1"):
        for k, info in res[t]: conf.setdefault((t, int(info["s_idx"])), int(k))
    for t in ("B2c", "B2w"):
        for k, info in res[t]: conf.setdefault(("B2", int(info["p1_idx"])), int(k))
    b1_conf = sorted((int(k), info) for k, info in res["B1"])
    udates = sorted(set(dates)); last_ok = udates[-1 - FOLLOW]
    snaps = [i for i in range(n) if times[i] == "1500" and T0 <= dates[i] <= last_ok]
    ev = SE.pen_events(h, l); W = Walker2(); ei = 0
    seen = {}
    for t in snaps:
        while ei < len(ev) and ev[ei][0] <= t:
            k, kind, it = ev[ei]; W.push(kind, it, k); ei += 1
        units = list(W.seg)
        if len(units) < 5 or units[-1][0] != "top": continue
        s0 = units[-1]; a0 = s0[2] + 1
        if a0 > t: continue
        kx = a0 + int(np.argmin(l[a0:t + 1])); lo = float(l[kx])
        ev_f, _, _ = analyze2(units, dif, bar)
        keys0 = {(tp, x.get("e_idx")) for tp, _, x in ev_f}
        uh = units + [("bot", lo, kx, t)]
        ev_h, _, _ = analyze2(uh, dif, bar)
        cands = [(tp, s0[2]) for tp, _, x in ev_h if tp in ("B3", "B1") and x.get("e_idx") == kx and (tp, kx) not in keys0]
        p1 = len(uh) - 3
        for kk, info in b1_conf:
            if kk > t: break
            if uh[p1][0] == "bot" and uh[p1][2] == info["e_idx"] and uh[p1 + 1][0] == "top":
                cands.append(("B2", uh[p1][2]))
        for key in cands:
            if key not in seen:
                seen[key] = dict(code=code, typ=key[0], first=int(t), first_day=str(dates[t]), lo=lo)
    di = {dd: i for i, dd in enumerate(udates)}
    for key, e in seen.items():
        kc = conf.get(key)
        e["confirmed"] = bool(kc is not None and kc > e["first"] and di[str(dates[kc])] - di[e["first_day"]] <= FOLLOW)
        e["days"] = (di[str(dates[kc])] - di[e["first_day"]]) if (kc is not None and kc > e["first"]) else None
        e["already"] = bool(kc is not None and kc <= e["first"])
        out.append(e)
os.makedirs(OUTD, exist_ok=True)
json.dump(out, open(os.path.join(OUTD, "backcheck_%d.json" % SH[0]), "w", encoding="utf-8"), ensure_ascii=False)
print("shard", SH, "episodes", len(out))

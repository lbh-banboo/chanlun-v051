#!/usr/bin/env python3
"""把 pool_watch.py 的 JSON 写成给使用者看的 md 报告。用法：pool_watch_report.py JSON路径 [输出md路径]
默认输出在 JSON 同目录：<池标签>30分钟缠论遍历_<日期>_<最后K线时刻>.md"""
import os, sys, json
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import _paths as P
if len(sys.argv) < 2 or not os.path.exists(sys.argv[1]):
    raise SystemExit("用法：pool_watch_report.py <pool_watch_*.json> [输出md路径]\n  先运行 pool_watch.py 生成 JSON（在 $V51_HOME/live/watch/）；找不到文件：%s" % (sys.argv[1] if len(sys.argv) > 1 else "(未给)"))
J = json.load(open(sys.argv[1], encoding="utf-8"))
rows = J["rows"]; R = {r["code"]: r for r in rows}; N = len(rows)
PF = J.get("pool_from") or {}
def pf_txt(c):
    v = PF[c]; return v[:10] + (" 收盘后" if v[11:] == "2359" else " %s:%s" % (v[11:13], v[13:15]))
LABEL = J.get("label") or "关注池"                                   # 第二个池（重点池）的 JSON 带 label/state_dir/out_dir
led_p = os.path.join(P.LIVE, J.get("state_dir", "state"), "v51_ledger.json")
RG = J["regime_cn"]                                                  # 当前档位名称：报告里所有"X 下能不能买"的 X
NAV = J["nav"]; RISK_BUDGET = 0.0075 * J["mult"] * NAV
led = json.load(open(led_p, encoding="utf-8")) if os.path.exists(led_p) else {}
led_sigs = sorted((x["code"], x["typ"], x["bar"]) for x in led.get("signals", []) if x["bar"] >= J["start"])
last_bar = J["last_bars"][-1] if J["last_bars"] else "?"
day, hhmm = last_bar.split()
OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(os.path.abspath(sys.argv[1])), "%s30分钟缠论遍历_%s_%s.md" % (LABEL, day.replace("-", ""), hhmm))

def f2(x): return "—" if x is None else "%.2f" % x
def pc(x): return "—" if x is None else "%+.1f%%" % (100 * x)
def nm(r): return "%s %s%s" % (r["name"], r["code"][:6], "（科创）" if r["board"] == "star" else "")
def eng(r): return "正式" if r["off_ok"] else "修正版"
def end_mark(r, short=False):   # 1021：向上段高点之后还没有回调笔时 r1 为空，原来印成"跌破 —"，改成写明还差什么
    v = r["prog"]["r1"]
    if v is not None: return "跌破 %.2f" % v
    return "待定（高点之后的回调还没走成一笔）" if short else "高点之后回调一笔（低点记 B）、反弹不创新高、再跌破 B（现在高点之后的回调还没走成一笔）"

# ---------- 临近买点：每条写成"还差什么" ----------
def need_text(r, p):
    g = r["prog"]; X = p["lo"]
    s = []
    if g["dir"] == "down":
        A = g["r1"] if g["ext_is_pen"] else None
        after = g["after"] if g["ext_is_pen"] else []
        if not g["ext_is_pen"]:
            if g["ext_bar"] == r["last_bar"]:
                s.append("低点 %.2f 就是刚收的这根，价格还在往下探，先要出底分型" % X)
            else:
                s.append("低点 %.2f（%s）还没被认成笔的端点（底分型未确认，或离上一个高点不够 4 根合并 K 线）" % (X, g["ext_bar"][5:]))
            s.append("成笔后再走上—下—上三笔：第一笔涨到的高点记 A，回落不破低点，再涨过 A 才确认")
        elif A is None:
            s.append("低点 %.2f（%s）已成笔" % (X, g["ext_bar"][5:]))
            s.append("等第一笔上涨走完（高点记 A），回落不破 %.2f，再涨过 A 才确认" % X)
        elif len(after) == 1:
            s.append("低点 %.2f 之后已涨到 A＝%.2f（%s）" % (X, A, after[0][2][5:]))
            s.append("等回落一笔不破 %.2f，再涨过 %.2f 才确认" % (X, A))
        else:
            s.append("低点 %.2f → 涨到 A＝%.2f → 已回落到 %.2f 没破低点" % (X, A, after[1][1]))
            s.append("再涨过 %.2f 就确认" % A)
        if g["pens_to_best"] < 3:
            s.append("这段回调目前只有 %d 笔，不够 3 笔成段，还要多走" % g["pens_to_best"])
    if p["typ"] == "三买":
        s.append("全程低点不能跌破中枢上沿 ZG %.2f（跌破就不是三买）" % p["ZG"])
    elif p["typ"] == "一买":
        s.append("低点若继续下移，要看背驰还在不在（MACD 绿柱面积小于上一段、DIF 低点抬高），背驰没了就作废")
    elif p["typ"].startswith("二买"):
        s.append("一买低点 %.2f；V5.1 里二买破不破一买都算" % p["P1"])
    return "；".join(s)

def struct_text(r, p):
    if p["typ"] == "三买":
        return "中枢 %.2f—%.2f 向上离开后回调，回调低点 %.2f 仍在 ZG 上方" % (p["ZD"], p["ZG"], p["lo"])
    if p["typ"] == "一买":
        a = p.get("areaA"); c = p.get("areaC")
        return "两个下降中枢后的离开段创新低到 %.2f，绿柱面积 %.0f→%.0f（缩小）" % (p["lo"], a or 0, c or 0)
    return "一买 %.2f → 反弹高点 %.2f → 回调到 %.2f" % (p["P1"], p["P2"], p["lo"])

def stage_rank(r, p):
    """离确认的远近：0 最近"""
    g = r["prog"]
    if g["dir"] != "down": return 9
    if not g["ext_is_pen"]: return 3
    if g["r1"] is None: return 2
    return 1 if len(g["after"]) == 1 else 0

pend = []
for r in rows:
    for p in r["pend"]:
        pend.append((r, p))
def feas_key(rp):
    r, p = rp; g = p["gate"]
    ok = g["feasible"] and p["conf_lo"] <= g["hi_e"]
    return (0 if ok else 1, stage_rank(r, p), r["code"])
pend.sort(key=feas_key)

L = []
L.append("# %s %d 只 · 30 分钟缠论遍历（%s %s:%s 已收盘 K 线）" % (LABEL, N, day, hhmm[:2], hhmm[2:]))
L.append("")
BASE = J.get("base_end") or "?"
STAGE = "当前为实时回测期（纸面性质）：只记纸面、不下真单。" if not J.get("stage_real") else "当前已进入真实下单阶段，请按手册第 9 章执行卡逐条核对。"
L.append("> 数据：30 分钟线＝baostock 历史库（到 %s）＋新浪实时，只用已收盘 K 线，最后一根 %s；日线（准入、振幅、前收）用 %s 之前最近一个交易日。引擎＝V5.1 的 S 引擎（30 分钟线段）。档位＝%s（%s 收盘口径），总仓位上限 %d%%、风险倍率 %.2f，一笔风险预算 %.0f 元（净值 %.0f×0.75%%×%.2f）。**本文件只是观察名单，不是买入信号；%s**" % (
    BASE, last_bar, day, RG, J["regime_src"], 100 * J["cap"], J["mult"], RISK_BUDGET, NAV, J["mult"], STAGE))
L.append("")

# ---------- 一、结论 ----------
recent_live = [(r, b) for r in rows for b in r["recent_buys"] if b["live"]]
L.append("## 一、结论")
L.append("")
today_new = [(r, b) for r in rows for b in r["recent_buys"] if b["bar"].startswith(day)]
L.append("截至 %s，%d 只里%s；V5.1 实时回测账本（起点 %s，%s 那次运行）记了 %d 个信号、%d 笔持仓。下面是按\"离买点多近、退潮期买不买得了\"挑出来值得盯的票，排在前面的更值得看：" % (
    last_bar, N, ("**没有一只今天确认新买点**" if not today_new else "今天确认了 %d 个买点：%s" % (len(today_new), "、".join("%s %s %s" % (r["name"], b["typ"], b["bar"][11:]) for r, b in today_new))),
    J["start"], led.get("asof", "?")[5:16], len(led_sigs), len(led.get("positions", {}))))
L.append("")
if PF:
    L.append("池子变动：%s（V5.1 第 3 章：入池必须发生在信号出现之前，入池前确认的买点不入账）。" % "；".join("%s 于 %s 入池" % (nm(R[c]) if c in R else c, pf_txt(c)) for c in sorted(PF)))
    L.append("")
L.append("| 优先 | 股票 | 现在是什么状态 | 盯什么价位 | %s能不能买 | 引擎 |" % RG)
L.append("|---|---|---|---|---|---|")
pri = []
# 1) 近期已确认且仍有效的买点
for r in rows:
    for b in r["recent_buys"]:
        prep = [x for x in r["prep"] if x["typ"] == "二买预备"]
        if b["broke_stop"] is None and not b["later_sell"] and (b["age"] <= 9 or prep):
            st = "%s %s 确认的%s还有效（现价离失效价 %s）" % (b["bar"][5:10], b["bar"][11:], b["typ"], pc(b["to_stop"]))
            if prep:
                st += "；一买之后的第一段上涨还没走完，涨完回调确认就是二买"
            pri.append((0, r, st, "失效价 %.2f；上涨段结束标志＝%s" % (b["stop"], end_mark(r, True)) if prep else "失效价 %.2f" % b["stop"],
                        ("能（振幅 %.2f 元，%s一手风险够）" % (r["amp5"], RG)) if r["feas_any"] else "不能：" + "、".join(r["feas_why"])))
for r, p in pend:
    g = p["gate"]; ok = g["feasible"] and p["conf_lo"] <= g["hi_e"]
    st = "%s候选：%s" % (p["typ"], struct_text(r, p))
    sell = [s for s in r["recent_sells"]]
    if sell: st += "；但 %s 刚出过%s" % (sell[-1]["bar"][5:], sell[-1]["typ"])
    if any(b["broke_stop"] for b in r["recent_buys"]): st += "；前一个买点已被止损"
    key = "失效价约 %.2f" % p["stop"] + ("；守住 ZG %.2f" % p["ZG"] if p["typ"] == "三买" else "")
    pri.append((1 if ok else 2, r, st, key, ("能，" + p["exec_view"]) if ok else ("不能：" + p["exec_view"])))
pri.sort(key=lambda x: (x[0], stage_rank(x[1], next((p for p in x[1]["pend"]), {"typ": ""})) if x[1]["pend"] else 0))
pri_star = [x for x in pri if x[1]["board"] == "star"]; pri = [x for x in pri if x[1]["board"] != "star"]
for i, (lv, r, st, key, fe) in enumerate(pri, 1):
    L.append("| %d | %s | %s | %s | %s | %s |" % (i, nm(r), st, key, fe, eng(r)))
L.append("")
if pri_star:
    L.append("科创板单列（一手 200 股）：%s。" % "；".join("%s——%s，%s" % (nm(r), st, fe) for lv, r, st, key, fe in pri_star))
    L.append("")
in_pri = {x[1]["code"] for x in pri + pri_star}
top = [r for r in sorted(rows, key=lambda r: (-r["score"], -r["ret10"])) if r["code"] not in in_pri][:5]
old_ok = {r["code"]: b for r in rows for b in r["recent_buys"] if b["age"] >= 10 and b["broke_stop"] is None and not b["later_sell"]}
def topdesc(r):
    t = "%s%s（%d 分）" % (r["name"], "（科创）" if r["board"] == "star" else "", r["score"])
    if r["code"] in old_ok:
        b = old_ok[r["code"]]; t += "——%s 的%s还有效（失效价 %.2f），但已过了买入那根，错过不追" % (b["bar"][5:10], b["typ"], b["stop"])
    return t
L.append(("30 分钟强弱排在前面、但上表没有的：%s。它们走得最强，可结构上都**没有临近的买点**：要么三买已经出过、价格离开中枢后还没形成新中枢，要么正在中枢里来回，得等下一个结构。强票没买点、有买点的多是下跌途中的一买候选" + ("，这和退潮期的盘面一致。" if J["regime"] in ("EBB", "HARD") else "。")) % "；".join(topdesc(r) for r in top))
L.append("")
L.append("引擎：正式 S 引擎（已含\"线段端点被延伸后卡死\"缺陷修复），%d 只全部可见。" % N)
L.append("")

# ---------- 二、临近买点 ----------
L.append("## 二、临近买点（结构正在成形，还没确认，确认前不能买）")
L.append("")
L.append("判定方法：把每只票\"正在走的这一段\"假设就在当前最低点结束，交给同一个引擎去算，会不会出一买/二买/三买。会出的就列在这里。它说明\"如果接下来走出确认，会是哪种买点、失效价大概在哪\"，**不等于一定会确认**——手册 6.2 写明\"回试线段终点没被确认的都不是买点\"。")
L.append("")
BC = os.path.join(os.path.dirname(os.path.abspath(sys.argv[1])), "backcheck_0.json")
if os.path.exists(BC):
    import statistics
    E = json.load(open(BC, encoding="utf-8"))
    def st_(t):
        v = [x for x in E if x["typ"] == t]; cf = [x["days"] for x in v if x["confirmed"]]
        return len(v), len(cf), ("%g" % statistics.median(cf) if cf else "—")
    a, b, c = st_("B3"), st_("B1"), st_("B2")
    L.append("这种\"临近\"靠不靠谱，回看过（pool_watch_backcheck.py：历史上每个交易日 15:00 拍一次快照，只用快照当时以前的数据）：被标成临近三买的 %d 段里，%d 段（%.0f%%）在 20 个交易日内真的确认了，确认中位要等 %s 个交易日；临近一买 %d 段确认 %.0f%%、中位 %s 天；临近二买 %d 段确认 %.0f%%、中位 %s 天。也就是说**多数（六成多到近八成）会变成真买点，但一般要等一周多**；其余的结构被破坏、不了了之。这只回答\"会不会确认\"，不回答\"确认后赚不赚钱\"。" % (
        a[0], a[1], 100 * a[1] / max(a[0], 1), a[2], b[0], 100 * b[1] / max(b[0], 1), b[2], c[0], 100 * c[1] / max(c[0], 1), c[2]))
    L.append("")
def pend_table(items):
    L.append("| 股票 | 候选 | 结构 | 失效价（假设低点×0.99） | 还差什么才确认 | %s闸门预检 | 引擎 |" % RG)
    L.append("|---|---|---|---:|---|---|---|")
    for r, p in items:
        L.append("| %s | %s | %s | %.2f | %s | %s | %s |" % (nm(r), p["typ"], struct_text(r, p), p["stop"], need_text(r, p), p["exec_view"], eng(r)))
    L.append("")
main = [(r, p) for r, p in pend if r["board"] != "star"]; star = [(r, p) for r, p in pend if r["board"] == "star"]
if main: pend_table(main)
else: L.append("（主板/创业板：无）"); L.append("")
if star:
    L.append("科创板单列（一手 200 股，%s风险预算只够振幅 ≤%.2f 元的票）：" % (RG, RISK_BUDGET / 200)); L.append("")
    pend_table(star)
L.append("闸门预检怎么读：买点没确认就不知道买价，所以这里不判\"过/不过\"，而是算出**确认价落在哪个区间才买得了**。下限来自带宽闸门（买价−失效价 ≥ 近 5 日平均振幅），上限取三者最小：止损距离 ≤12%%、风险预算 %.0f 元至少够买一手、1.5a 价格上限（主板 %.2f、创业板 %.2f、科创板 %.2f）。下限高于上限就是\"%s下无解\"。真到确认那根 K 线，还要再过开盘三条（高开 >3%%、涨停、开盘破失效价都取消）。" % (RISK_BUDGET, NAV / 800, NAV * 0.10 / 100, NAV * 0.10 / 200, RG))
L.append("")
prep_rows = [(r, x) for r in rows for x in r["prep"]]
if prep_rows:
    L.append("还差一步的（正在走向上段，要等它走完再回调）：")
    L.append("")
    L.append("| 股票 | 预备 | 说明 | 引擎 |")
    L.append("|---|---|---|---|")
    for r, x in prep_rows:
        if x["typ"] == "二买预备":
            t = "一买低点 %.2f 之后的上涨段进行中（目前最高 %.2f）。这段上涨要等%s才算走完，之后的回调终点确认就是二买，失效价＝回调低点×0.99" % (x["P1"], x["hi"], end_mark(r))
        elif x["typ"] == "三买预备":
            t = "正在向上离开中枢 %.2f—%.2f（目前最高 %.2f）；之后第一段回调低点若守住 ZG %.2f，确认即三买" % (x["ZD"], x["ZG"], x["hi"], x["ZG"])
        else:
            t = "在中枢 %.2f—%.2f 里向上，还没突破 ZG" % (x["ZD"], x["ZG"])
        L.append("| %s | %s | %s | %s |" % (nm(r), x["typ"], t, eng(r)))
    L.append("")
# ---------- 三、近期已确认买点 ----------
L.append("## 三、近 10 个交易日已确认的买点（%s 起）" % J["look_start10"])
L.append("")
def rb_now(b):
    if b["broke_stop"]: return "**已失效**：%s 收盘跌破失效价" % b["broke_stop"][5:]
    if b["later_sell"]: return "出过卖点 %s" % "、".join(x[5:] for x in b["later_sell"])
    return "**仍有效**，离失效价 %s，较信号价 %s" % (pc(b["to_stop"]), pc(b["chg_since"]))
def rb_table(items):
    L.append("| 确认时刻 | 股票 | 类别 | 信号收盘 | 失效价 | 现价 | 现在 | 当时按 V5.1 能不能买 | 引擎 |")
    L.append("|---|---|---|---:|---:|---:|---|---|---|")
    for r, b in sorted(items, key=lambda x: x[1]["bar"]):
        ex = b.get("exec", "")
        if b.get("prepool"): ex += "（入池前确认，不入账；%s 入池）" % pf_txt(r["code"])
        elif not b["live"]: ex += "（在实时回测起点 %s 之前，不入账）" % J["start"]
        L.append("| %s | %s | %s | %.2f | %.2f | %.2f | %s | %s | %s |" % (b["bar"][5:], nm(r), b["typ"], b["sig_close"], b["stop"], r["last"], rb_now(b), ex, eng(r)))
    L.append("")
rb10 = [(r, b) for r in rows for b in r["recent_buys"] if b["age"] <= 9]
rb20 = [(r, b) for r in rows for b in r["recent_buys"] if b["age"] >= 10 and b["broke_stop"] is None and not b["later_sell"]]
if rb10: rb_table(rb10)
else: L.append("无。"); L.append("")
if rb20:
    L.append("再往前 10 个交易日（%s 起）确认、到现在**仍有效**的：" % J["look_start"]); L.append("")
    rb_table(rb20)
L.append("已确认的买点过了下一根 K 线开盘就不再补买（V5.1 6.3 第 7 条：错过不追）。列在这里是为了看结构还在不在：仍有效的，后面可能接着出二买或新的三买；已失效的，原结构作废。")
L.append("")

# ---------- 四、强弱排序 ----------
L.append("## 四、30 分钟强弱排序（%d 只）" % N)
L.append("")
L.append("打分 6 项各 1 分：①收盘在 30 分钟 MA20 上 ②30 分钟 MA20 在 MA60 上 ③MACD 的 DIF>0 ④DIF>DEA ⑤价格在最近中枢上沿 ZG 上方 ⑥正在走的线段向上；价格在中枢下沿 ZD 下方再扣 1 分。同分按 10 日涨幅排。**这个排序没有回测过，只帮你看谁强谁弱，不能当买入条件**（V5.1 第 3 章第 5 条：不额外加强势过滤；第 2 章：同时出信号也不许择优）。")
L.append("")
L.append("| # | 股票 | 现价 | 今日 | 5日 | 10日 | 30分MA20 / MA60 | 最近中枢 ZD—ZG | 位置 | 正在走 | 得分 | %s可买 | 引擎 |" % RG)
L.append("|---:|---|---:|---:|---:|---:|---|---|---|---|---:|---|---|")
for i, r in enumerate(sorted(rows, key=lambda r: (-r["score"], -r["ret10"])), 1):
    pv = r["pivot"]
    pvs = ("%.2f—%.2f" % (pv["ZD"], pv["ZG"]) + {"B3": "（已三买）", "BRK": "（已向下离开）", None: ""}[pv["end"]]) if pv else "—"
    seg = "向上段" if r["prog"]["dir"] == "up" else "向下段"
    flag = "能" if r["feas_any"] else "不能：" + "、".join(r["feas_why"])
    L.append("| %d | %s | %.2f | %s | %s | %s | %s / %s | %s | %s | %s | %d | %s | %s |" % (
        i, nm(r), r["last"], pc(r["pct"]), pc(r["ret5"]), pc(r["ret10"]), f2(r["ma20"]), f2(r["ma60"]), pvs, r["pos"] or "—", seg, r["score"], flag, eng(r)))
L.append("")
nf = sum(1 for r in rows if not r["feas_any"])
NEXT = {"EBB": ("修复期", 0.75), "REPAIR": ("健康趋势", 1.0), "HOT": ("健康趋势", 1.0), "HARD": ("退潮期(soft)", 0.40)}.get(J["regime"])
L.append("\"%s可买\"一栏是说：按现在的档位，这只票不管出什么买点能不能买。**%d 只是\"不能\"**，最常见的原因是振幅太大：带宽闸门要求止损距离 ≥ 近 5 日平均振幅，而%s一笔只有 %.0f 元风险预算，止损距离超过 %.2f 元（科创板 %.2f 元）就连一手都买不起，两条一夹就无解。%s" % (
    RG, nf, RG, RISK_BUDGET, RISK_BUDGET / 100, RISK_BUDGET / 200,
    ("档位升到%s（倍率 %.2f）时预算变成 %.0f 元，会放出来一部分。" % (NEXT[0], NEXT[1], 0.0075 * NEXT[1] * NAV)) if NEXT else ""))
L.append("")

# ---------- 六、口径 ----------
L.append("## 五、口径与局限")
L.append("")
COOL = J.get("cooling") or {}
L.append("引擎与 V5.1 实时扫描同一套（`sop_run.run_series`，S 引擎），30 分钟价＝不复权价×日线复权因子；只用已收盘 K 线，盘中这根不算。\"临近买点\"是观察口径，不在手册规则里；它\"多少会确认\"见第二节的回看，确认后的盈亏没有检验。闸门预检用的是现在的档位，真到确认那天档位可能已变。失效价按假设低点×0.99 估算，低点再下移，失效价跟着下移。科创板单列、1.5a 用分板块上限。" + ("冷却期（视同）：%s。" % "；".join("%s %s" % (R[c]["name"] if c in R else c, v) for c, v in COOL.items()) if COOL else ""))
L.append("")
live_all = sorted((r["code"], b["typ"], b["bar"]) for r in rows for b in r["recent_buys"] if b["live"])
L.append("核对（脚本复算）：①引擎无前视（截断重算自检见 tests/test_engine_truncation.py）；②%s 起确认的买点本次遍历 %d 个，V5.1 实时账本（%s 运行）记了 %d 个，%s。" % (
    J["start"], len(live_all), led.get("asof", "?")[5:16], len(led_sigs), "一致" if live_all == led_sigs else "**不一致，需要查**：%s vs %s" % (live_all, led_sigs)))
L.append("")
L.append("脚本：`pool_watch.py`（取数＋遍历，只读，不碰 V5.1 账本）→ `pool_watch_report.py`（出本报告）；本次原始结果 `live/%s/%s`。" % (J.get("out_dir", "watch"), os.path.basename(sys.argv[1])))
open(OUT, "w", encoding="utf-8").write("\n".join(L) + "\n")
print(OUT)

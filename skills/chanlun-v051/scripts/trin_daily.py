#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TRIN（阿姆氏指标）每日收盘插件 2.0 —— 给收盘定时任务（v51-close，V5.1 收盘日报）用。

在收盘流程里的位置：close_pipeline.py（更新全市场日线、算档位）→ v51_scan.py（出日报）→ 本脚本 → 任务读输出并回复。
必须在 v51_scan.py 之后跑：v51_scan.py 每次整份重写 V51_日期.md / V51_latest.md，先写入会被覆盖。

2.0 相比 1.0 的变化（产出以可读数据为主、核心结论置顶）：
  1. 产出以"数据"为主：输出现成的两行 TRIN_CORE_1 / TRIN_CORE_2（以 "> " 开头的 Markdown 引用行），
     任务原样放进回复最上方的【整理结论】块；同样的两行也写进日报 md 的最上方（标题下面）。
     不再让 "TRIN_STATUS=OK" 这种内部记号出现在报告里；
  2. 数据健康用人话写一句（TRIN_HEALTH）：个股是否齐全、数据源是新浪收盘快照还是搜狐日线补数、
     近 10 个交易日有没有缺口、涨跌方向与收盘价是否自洽、是否疑似复制昨天、单位、成交额、与历史库对账；
  3. 状态分级 OK / WARN / STALE / PARTIAL / ERROR。有问题时也照样给数据，并把问题写在同一行里；
     算不出来的日子，核心结论里也有一行，写明"未算出"和原因（以及最近一次可用的数），不再整行消失。

做什么（只读 data_live 与两个日志；只写下面列出的自己的文件，不碰账本、引擎和日线库）：
  1. 读 data/daily 每只个股文件末尾几十行，按 pct 判涨跌、amount 为成交额，
     算最近 10 个交易日的 TRIN（口径见 references/12-trin-observation.md）：
         TRIN = [上涨家数 ÷ (下跌+平盘家数)] ÷ [上涨股成交额 ÷ 下跌股成交额]
  2. 上面的健康检查（见 build_health）；
  3. upsert 进 pipeline/trin_state/trin_history.csv（首次运行历史库为空，只有当天起算；重跑幂等；分位要攒够 60 天才稳）；
  4. 写 pipeline/TRIN_YYYYMMDD.json / .md；
  5. 若 最新数据日 == 今天，把 TRIN 块写进 reports/V51_今天.md 与首行日期也是今天的 V51_latest.md：
     最上方一块（<!-- TRIN:TOP:BEGIN/END -->）+ 末尾一节（<!-- TRIN:BEGIN/END -->），已有则原位替换，重跑不重复；
  6. 向 stdout 打印协议行（见下），处理过的失败一律 exit 0，不让收盘主流程因观察指标中断。

stdout 协议：
  TRIN_PLUGIN=2.0
  TRIN_STATUS=OK|WARN|STALE|PARTIAL|ERROR date=… today=… [msg=…]     内部记号，任务不要写进回复
  TRIN_CORE_1=> **【阿姆氏 TRIN】**…     给【整理结论】块的第二行（已带 "> "），每天都有
  TRIN_CORE_2=> 同广度对照：…            第三行；算不出来时没有
  TRIN_HEALTH=TRIN 数据健康：… / TRIN 数据留意：…      给【数据健康与局限】的一句人话
  TRIN_LINE1 / TRIN_LINE2                                 1.0 兼容行（旧提示词仍可用）
  TRIN_CHECK / TRIN_HIST / TRIN_FILES / TRIN_APPENDED / TRIN_ELAPSED

TRIN 是观察指标：不进档位、不进买卖点、不影响仓位；V5.1 实时回测期（第 10.1 条）不改规则。
只用标准库，Python ≥ 3.8。命令行参数仅用于测试，计划任务不带参数直接跑。
"""
import os, sys, csv, glob, json, time, datetime, statistics, argparse, traceback

sys.dont_write_bytecode = True
try:
    sys.stdout.reconfigure(encoding="utf-8")          # 宿主机默认已是 utf-8，保险起见
except Exception:                                      # noqa
    pass

PLUGIN_VERSION = "2.0"
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import _paths as P
ROOT = P.HOME
D_LIVE = P.DAILY
D_LOGS = P.META                                                             # c_update_log.json / backfill_batch_log.json
D_PIPE = P.PIPE
D_REPORTS = os.path.join(P.LIVE, "reports")
D_STATE = os.path.join(P.PIPE, "trin_state")
SEED = None                                                                 # 公开版不带历史种子；历史库从第一次运行起逐日累积
DOC = "references/12-trin-observation.md"

WINDOW = 10               # 每次重算最近多少个交易日（用来自愈历史库）
TAIL_BYTES = 4096         # 每个个股文件读末尾多少字节（约 35 行，够覆盖 10 个交易日）
PARTIAL_FRAC = 0.97       # 最新一天个股只数低于"参考只数×0.97"，视为日线库没更新完
MIN_HIST = 60             # 历史库少于这么多天，分位不稳，要在文字里提醒
SINA_DECIMALS = 4         # 新浪收盘快照入库的行：换手率有 ≥4 位小数；搜狐日线行只有 ≤2 位（实测区分干净）
# ---- 健康检查阈值（均用 2025-12~2026-10 的 221 个交易日标定，不误报）----
DIR_WARN_SHARE = 0.005    # pct 方向与收盘价涨跌方向不一致的占比 >0.5%（历史每天 0~2 只，约 0.04%）
COPY_WARN_SHARE = 0.005   # 量、额、收盘价与上一行完全相同的占比 >0.5%（疑似把昨天复制成今天）
UNIT_LO, UNIT_HI = 0.9, 1.1          # 成交额 ÷（手×100×收盘价）的中位数；历史逐日 0.968~1.026
AMT_LO, AMT_HI = 0.35, 3.0           # 当日全市场成交额 ÷ 前几日中位数
HIST_DN_WARN = 5          # 与历史库对账：某天个股数差 >5 只，算大改动
HIST_TRIN_WARN = 0.01     # 或该天 TRIN 相对变化 >1%
BREADTH_HS_DOWN, BREADTH_RED_HI = -1.5, 80.0     # 指数跌≥1.5% 而红盘占比≥80%：方向矛盾（历史最高 67.6%）
BREADTH_HS_UP, BREADTH_RED_LO = 1.5, 12.0        # 指数涨≥1.5% 而红盘占比≤12%：方向矛盾（历史最低 23.1%）

TOP_BEGIN, TOP_END = "<!-- TRIN:TOP:BEGIN -->", "<!-- TRIN:TOP:END -->"
BEGIN, END = "<!-- TRIN:BEGIN -->", "<!-- TRIN:END -->"
HCOLS = ["date", "N", "up", "down", "flat", "amt_up_yi", "amt_dn_yi", "amt_flat_yi", "red_pct", "up_amt_share", "trin", "hs300_pct"]
INTCOLS = ("N", "up", "down", "flat")


# ------------------------------------------------------------------ 读数据
def read_tail(path, nbytes):
    with open(path, "rb") as f:
        header = f.readline().decode("utf-8", "ignore").strip()
        f.seek(0, 2)
        size = f.tell()
        start = max(0, size - nbytes)
        f.seek(start)
        buf = f.read().decode("utf-8", "ignore")
    lines = buf.splitlines()
    if start > 0 and lines:
        lines = lines[1:]                              # 第一行可能被截断
    return header, lines


def decimals(s):
    i = s.find(".")
    return (len(s) - i - 1) if i >= 0 else 0


def _f(s):
    """字符串转 float；空/非法/NaN 返回 None。"""
    try:
        v = float(s)
    except (TypeError, ValueError):
        return None
    return None if v != v else v


def read_index(live_dir, asof):
    """沪深300 日线末尾：交易日历 + 当日涨跌幅。返回 {date: pct}。"""
    path = os.path.join(live_dir, "000300.SH.csv")
    hs = {}
    if not os.path.exists(path):
        return hs
    header, lines = read_tail(path, 8192)
    cols = header.split(",")
    try:
        i_d, i_p = cols.index("trade_date"), cols.index("pct")
    except ValueError:
        return hs
    for ln in lines:
        if not ln or ln.startswith("trade_date"):
            continue
        p = ln.split(",")
        if len(p) <= max(i_d, i_p):
            continue
        d = p[i_d]
        if asof and d > asof:
            continue
        v = _f(p[i_p])
        if v is not None:
            hs[d] = v
    return hs


def collect(live_dir, asof, window, hs):
    """读全部个股末尾几十行。返回 (by_date, info)。
    by_date[date] = [(pct, amt, close, raw, vol, pclose, praw, pvol, pamt, sina), …]；p* 是该文件上一行的值。"""
    if hs:
        cal = sorted(hs)
        cutoff = cal[max(0, len(cal) - (window + 3))]
    else:
        base = datetime.date.fromisoformat(asof) if asof else datetime.date.today()
        cutoff = (base - datetime.timedelta(days=40)).isoformat()
    by_date = {}
    nfile = bad_file = bad_row = 0
    for path in sorted(glob.glob(os.path.join(live_dir, "*.csv"))):
        if os.path.basename(path).startswith("000300"):
            continue
        header, lines = read_tail(path, TAIL_BYTES)
        cols = header.split(",")
        try:
            i_d, i_p, i_a = cols.index("trade_date"), cols.index("pct"), cols.index("amount")
        except ValueError:
            bad_file += 1
            continue
        i_c = cols.index("close") if "close" in cols else -1
        i_r = cols.index("raw_close") if "raw_close" in cols else -1
        i_v = cols.index("volume") if "volume" in cols else -1
        i_t = cols.index("turnover") if "turnover" in cols else -1
        need = max(i_d, i_p, i_a, i_c, i_r, i_v, i_t)
        prev = (None, None, None, None)
        for ln in lines:
            if not ln or ln.startswith("trade_date"):
                continue
            p = ln.split(",")
            if len(p) <= need:
                bad_row += 1
                continue
            d = p[i_d]
            if asof and d > asof:
                continue
            pct, amt = _f(p[i_p]), _f(p[i_a])
            if pct is None or amt is None:
                bad_row += 1
                continue
            close = _f(p[i_c]) if i_c >= 0 else None
            raw = _f(p[i_r]) if i_r >= 0 else None
            vol = _f(p[i_v]) if i_v >= 0 else None
            sina = (decimals(p[i_t]) >= SINA_DECIMALS) if (i_t >= 0 and p[i_t] != "") else None
            if d >= cutoff:
                by_date.setdefault(d, []).append((pct, amt, close, raw, vol, prev[0], prev[1], prev[2], prev[3], sina))
            prev = (close, raw, vol, amt)
        nfile += 1
    return by_date, dict(files=nfile, bad_files=bad_file, bad_rows=bad_row)


def day_row(date, rows, hs_pct):
    nu = nd = nf = 0
    au = ad = af = 0.0
    for r in rows:
        pct, amt = r[0], r[1]
        if pct > 0:
            nu += 1; au += amt
        elif pct < 0:
            nd += 1; ad += amt
        else:
            nf += 1; af += amt
    n = nu + nd + nf
    trin = None
    if n - nu > 0 and au > 0 and ad > 0:
        trin = (nu / float(n - nu)) / (au / ad)
    tot = au + ad + af
    return dict(date=date, N=n, up=nu, down=nd, flat=nf, amt_up_yi=au / 1e8, amt_dn_yi=ad / 1e8, amt_flat_yi=af / 1e8,
                red_pct=100.0 * nu / n if n else None, up_amt_share=100.0 * au / tot if tot else None,
                trin=trin, hs300_pct=hs_pct)


# ------------------------------------------------------------------ 历史库
def _num(s):
    if s is None or s == "":
        return None
    return float(s)


def load_history(path, seed):
    """读历史库；不存在则用 seed（trin_series.csv，带 BOM）打底。返回 (dict: date→行, 来源说明)。"""
    hist = {}
    src, enc = (path, "utf-8") if os.path.exists(path) else (seed, "utf-8-sig")
    if not (src and os.path.exists(src)):
        return hist, "无"
    mapping = {"trin": "trin_main"} if src == seed else {}
    with open(src, encoding=enc, newline="") as f:
        for r in csv.DictReader(f):
            d = r["date"]
            row = {"date": d}
            for c in HCOLS[1:]:
                v = _num(r.get(mapping.get(c, c)))
                row[c] = int(round(v)) if (v is not None and c in INTCOLS) else v
            hist[d] = row
    return hist, ("历史库" if src == path else "seed(trin_series.csv)")


def save_history(path, hist):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(HCOLS)
        for d in sorted(hist):
            r = hist[d]
            w.writerow([d] + ["" if r[c] is None else (("%d" % r[c]) if c in INTCOLS else ("%.6f" % r[c])) for c in HCOLS[1:]])
    os.replace(tmp, path)


def ref_n(hist):
    ns = [hist[d]["N"] for d in sorted(hist)[-20:] if hist[d]["N"]]
    return statistics.median(ns) if ns else None


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:                                  # noqa  日志缺失/损坏不能影响 TRIN 本身
        return default


# ------------------------------------------------------------------ 统计与文字
def label(t):
    """只用稿件自己给的线：口播的 0.7/0.6/0.5（低于 = 上涨股放量）和稿件注的 1.0。0.7–1.0 稿件没有名字，记"中性区"。"""
    if t is None:
        return "—"
    if t < 0.5:
        return "<0.5 上涨股放量（作者最低一档）"
    if t < 0.6:
        return "<0.6 上涨股放量"
    if t < 0.7:
        return "<0.7 上涨股放量"
    if t <= 1.0:
        return "0.7–1.0 中性区"
    return ">1.0 下跌股吃量更多"


def pctile(vals, x):
    return 100.0 * sum(1 for v in vals if v <= x) / len(vals)


def same_breadth(hist, D, red, x):
    """红盘占比在 ±10 个百分点（样本<10 则放宽到 ±15、±20）内的历史日（只看今日之前）里，TRIN 的分布。"""
    for half in (10.0, 15.0, 20.0):
        lo, hi = max(0.0, red - half), min(100.0, red + half)
        vals = [r["trin"] for d, r in hist.items() if d < D and r["trin"] is not None and r["red_pct"] is not None and lo <= r["red_pct"] <= hi]
        if len(vals) >= 10:
            break
    if not vals:
        return None
    return dict(lo=lo, hi=hi, half=half, n=len(vals), median=statistics.median(vals), tmin=min(vals), tmax=max(vals),
                rank=1 + sum(1 for v in vals if v < x))


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sxx * syy) ** 0.5


def n0(x):
    return "{:,}".format(int(round(x)))


def hhmm(run_at):
    return run_at[11:16] if run_at and len(run_at) >= 16 else "?"


def src_kind(rows):
    """按行格式判定数据来源：新浪收盘快照 / 搜狐日线补数 / 混合 / 未知。"""
    flags = [r[9] for r in rows if r[9] is not None]
    if not flags:
        return "unknown", None
    share = sum(1 for x in flags if x) / float(len(flags))
    if share >= 0.9:
        return "sina", share
    if share <= 0.1:
        return "sohu", share
    return "mixed", share


SRC_NAME = {"sina": "新浪收盘快照", "sohu": "搜狐日线补数", "mixed": "新浪快照与搜狐补数混合", "unknown": "来源未知"}


# ------------------------------------------------------------------ 健康检查
def build_health(D, today, by_date, hs, window_dates, nref, thr, hist_old, fresh, logs, blog):
    """返回 dict(level, flags, text, src, ...)。flags: [(level, code, 一句话)]，level ∈ WARN/INFO。"""
    rows = by_date[D]
    N = len(rows)
    flags = []
    facts = {}

    # 1) 来源（行格式）+ 更新日志
    kind, share = src_kind(rows)
    facts["src"] = kind
    runs = [e for e in logs if e.get("date") == D]
    sina_in = sum(int(e.get("appended") or 0) for e in runs)
    gap_in = sum(int(e.get("gap_filled") or 0) for e in runs)
    batch_in = sum(int(e.get("stocks_filled") or 0) for e in blog if e.get("target") == D)
    ing = [e.get("run_at") for e in runs if int(e.get("appended") or 0) > 0 and e.get("run_at")]
    first_ing = min(ing) if ing else None
    facts.update(sina_in=sina_in, gap_in=gap_in, batch_in=batch_in, first_ingest=first_ing, runs=len(runs))
    src_txt = SRC_NAME[kind]
    if kind == "sina" and first_ing:
        src_txt += "（%s 入库）" % hhmm(first_ing)
    elif kind == "sohu":
        bits = []
        if runs:
            bits.append("该日新浪快照入库 %d 只" % sina_in)
        else:
            bits.append("更新日志里没有该日的运行记录")
        if gap_in:
            bits.append("缺口补漏 %d 只" % gap_in)
        if batch_in:
            bits.append("搜狐批量补数 %d 只" % batch_in)
        src_txt += "（%s）" % "，".join(bits)
    elif kind == "mixed":
        src_txt += "（新浪 %.0f%%／搜狐 %.0f%%）" % (100 * share, 100 - 100 * share)
    if first_ing and first_ing[11:16] < "15:30" and first_ing[:10] == D:
        flags.append(("WARN", "intraday", "更新日志显示 %s 在 %s 就已入库，可能是盘中价而不是收盘定稿" % (D[5:], hhmm(first_ing))))

    # 2) 个股只数
    facts["N"], facts["nref"] = N, nref
    # 3) 窗口缺口：以沪深300 交易日历为准，窗口内哪天个股不全
    gaps = []
    if hs:
        for d in window_dates:
            c = len(by_date.get(d, []))
            if d != D and c < thr:
                gaps.append((d, c))
    facts["gaps"] = gaps
    if gaps:
        flags.append(("WARN", "gap", "近 %d 个交易日里有缺口：%s" % (
            len(window_dates), "、".join("%s 只有 %d 只（应约 %d）" % (d[5:], c, nref) for d, c in gaps))))

    # 4) 涨跌方向与收盘价一致性（pct 的符号 vs 复权收盘价较上一行的涨跌）
    chk = mm = 0
    for r in rows:
        pct, close, pclose = r[0], r[2], r[5]
        if close is None or pclose is None or pclose <= 0:
            continue
        chk += 1
        ret = close / pclose - 1.0
        s_ret = 1 if ret > 1e-7 else (-1 if ret < -1e-7 else 0)
        s_pct = 1 if pct > 0 else (-1 if pct < 0 else 0)
        if s_ret != s_pct:
            mm += 1
    facts.update(dir_chk=chk, dir_mm=mm)
    if chk and mm / float(chk) > DIR_WARN_SHARE:
        flags.append(("WARN", "direction", "涨跌幅与收盘价变动方向不一致 %d 只（占 %.1f%%），涨跌家数可能不可信" % (mm, 100.0 * mm / chk)))

    # 5) 疑似把昨天复制成今天
    cp = cpn = 0
    for r in rows:
        raw, vol, amt, praw, pvol, pamt = r[3], r[4], r[1], r[6], r[7], r[8]
        if raw is None or vol is None or praw is None or pvol is None or pamt is None or vol <= 0:
            continue
        cpn += 1
        if vol == pvol and amt == pamt and raw == praw:
            cp += 1
    facts.update(copy_chk=cpn, copy_n=cp)
    if cpn and cp / float(cpn) > COPY_WARN_SHARE:
        flags.append(("WARN", "stale_copy", "有 %d 只的成交量、额、收盘价与上一行完全相同（占 %.1f%%），疑似没更新" % (cp, 100.0 * cp / cpn)))

    # 6) 单位：成交额 ÷（手×100×收盘价）
    ratios = []
    for r in rows:
        raw, vol, amt = r[3], r[4], r[1]
        if raw and vol and raw > 0 and vol > 0 and amt > 0:
            ratios.append(amt / (vol * 100.0 * raw))
    unit = statistics.median(ratios) if len(ratios) >= 20 else None
    facts["unit"] = unit
    if unit is not None and not (UNIT_LO <= unit <= UNIT_HI):
        flags.append(("WARN", "unit", "成交额÷(手×100×收盘价)的中位数是 %.3f，偏离 1，成交量或成交额的单位可能变了" % unit))

    # 7) 成交额量级：当日全市场成交额 vs 窗口内前几日
    totD = sum(r[1] for r in rows)
    prev_tot = []
    for d in window_dates:
        if d < D and len(by_date.get(d, [])) >= thr:
            prev_tot.append(sum(r[1] for r in by_date[d]))
    ratio_amt = None
    if len(prev_tot) >= 3 and statistics.median(prev_tot) > 0:
        ratio_amt = totD / statistics.median(prev_tot)
        if not (AMT_LO <= ratio_amt <= AMT_HI):
            flags.append(("WARN", "amount", "全市场成交额 %.0f 亿，是前几日中位数的 %.2f 倍，量级异常" % (totD / 1e8, ratio_amt)))
    facts.update(total_amt_yi=totD / 1e8, amt_ratio=ratio_amt)

    # 8) 与历史库对账（重算的近 10 日 vs 已存的同一天）
    changed = []
    for d in window_dates:
        if d in hist_old and d in fresh and hist_old[d].get("trin") is not None and fresh[d]["trin"] is not None:
            o, f = hist_old[d], fresh[d]
            dcnt = (f["N"] - o["N"], f["up"] - o["up"], f["down"] - o["down"], f["flat"] - o["flat"])
            dt = f["trin"] - o["trin"]
            if any(dcnt) or abs(dt) >= 1e-6:
                big = abs(dcnt[0]) > HIST_DN_WARN or abs(dt) > HIST_TRIN_WARN * max(abs(o["trin"]), 1e-9)
                changed.append(dict(date=d, dN=dcnt[0], dup=dcnt[1], ddown=dcnt[2], dflat=dcnt[3], old=o["trin"], new=f["trin"], big=big))
    facts["hist_changed"] = changed
    for c in changed:
        if c["big"]:
            flags.append(("WARN", "hist", "%s 的数据与历史库差异较大（个股数 %+d，TRIN %.4f→%.4f）" % (c["date"][5:], c["dN"], c["old"], c["new"])))

    # 9) 指数与广度方向矛盾
    hp, red = hs.get(D), fresh[D]["red_pct"]
    if hp is not None and red is not None:
        if (hp <= BREADTH_HS_DOWN and red >= BREADTH_RED_HI) or (hp >= BREADTH_HS_UP and red <= BREADTH_RED_LO):
            flags.append(("WARN", "breadth", "沪深300 %+.2f%% 而红盘占比 %.1f%%，指数与涨跌家数方向矛盾" % (hp, red)))

    warns = [f for f in flags if f[0] == "WARN"]
    level = "WARN" if warns else "OK"

    # ---- 人话 ----
    parts = ["%s 个股 %s 只齐全（参考 %s）" % (D[5:], n0(N), n0(nref)), "数据源：" + src_txt]
    if hs and not gaps:
        parts.append("近 %d 个交易日无缺漏" % len(window_dates))
    parts.append("涨跌幅与收盘价方向核对 %d 只不一致（共 %s 只）" % (mm, n0(chk)) if chk else "涨跌幅方向核对：报告未提供")
    if unit is not None:
        parts.append("成交额单位核对通过（比值中位 %.3f）" % unit if UNIT_LO <= unit <= UNIT_HI else "成交额单位核对未通过（比值中位 %.3f）" % unit)
    if changed:
        small = [c for c in changed if not c["big"]]
        if small:
            parts.append("与历史库对账：" + "；".join(
                "%s 回补/修正（个股数 %+d，涨%+d/跌%+d/平%+d，TRIN %.4f→%.4f）" % (c["date"][5:], c["dN"], c["dup"], c["ddown"], c["dflat"], c["old"], c["new"])
                for c in small[:3]) + ("等 %d 天" % len(small) if len(small) > 3 else ""))
    else:
        parts.append("与历史库对账无变化")
    if warns:
        text = "TRIN 数据留意：" + "；".join(f[2] for f in warns) + "。其余：" + "；".join(parts)
    else:
        text = "TRIN 数据健康：" + "；".join(parts)
    return dict(level=level, flags=[dict(level=a, code=b, text=c) for a, b, c in flags], text=text, src_name=SRC_NAME[kind],
                src_short=("新浪快照" if kind == "sina" else "搜狐补数" if kind == "sohu" else "混合" if kind == "mixed" else "未知"),
                warn_short=[f[2] for f in warns], facts=facts)


# ------------------------------------------------------------------ 文字块
def build(hist, D, dw, health, note=""):
    """生成 TRIN_CORE_1/2、v1 兼容的 LINE1/2、日报末尾小节、日报顶部块、ctx。
    dw：第二行里指代当天的词（最新数据日 == 今天用"今日"，否则用"该日"）；note：核心结论行末尾追加的注意事项（STALE 时写原因）。"""
    dates = sorted(d for d in hist if d <= D)
    t = hist[D]
    prev = hist[dates[-2]] if len(dates) >= 2 else None
    tv = [hist[d]["trin"] for d in dates if hist[d]["trin"] is not None]
    pc = pctile(tv, t["trin"])
    sb = same_breadth(hist, D, t["red_pct"], t["trin"])
    pr = [hist[d] for d in dates if hist[d]["trin"] is not None and hist[d]["red_pct"] is not None]
    corr = pearson([r["red_pct"] for r in pr], [r["trin"] for r in pr])
    corr_txt = ("历史 %d 日相关 %.2f" % (len(pr), corr)) if corr is not None else "历史样本太少，相关系数未算"
    few = len(tv) < MIN_HIST
    md = D[5:]
    # 差值按"显示值"相减（满精度相减会出现 1.074−0.940 却显示 +0.135 的观感矛盾）
    chg = ("较前日 %+.3f（%s 为 %.3f）" % (round(t["trin"], 3) - round(prev["trin"], 3), prev["date"][5:], prev["trin"])) if (prev and prev["trin"] is not None) else "较前日 —"
    pcs = "历史分位 %.0f%%（%d日%s）" % (pc, len(tv), "，样本偏少分位不稳" if few else "")
    nums = "红盘占比 %.1f%%（涨%s/跌%s/平%s）｜上涨股成交额占比 %.1f%%" % (t["red_pct"], n0(t["up"]), n0(t["down"]), n0(t["flat"]), t["up_amt_share"])
    if sb:
        line2 = ("同广度对照：红盘占比 %.1f%%–%.1f%% 的历史 %d 个交易日（不含%s），TRIN 中位 %.2f（区间 %.2f–%.2f）；%s %.3f 放进去由低到高排第 %d/%d 位。"
                 % (sb["lo"], sb["hi"], sb["n"], dw, sb["median"], sb["tmin"], sb["tmax"], dw, t["trin"], sb["rank"], sb["n"] + 1))
    else:
        line2 = "同广度对照：历史库里没有红盘占比相近的交易日，报告未提供。"
    stale_tag = "" if dw == "今日" else "（非今日数据）"
    warn_tag = ("｜留意：" + "；".join(health["warn_short"])) if health["warn_short"] else ""
    note_tag = ("｜注意：" + note) if note else ""
    core1 = ("> **【阿姆氏 TRIN】**（观察指标，不参与档位/仓位） `%s TRIN %.3f%s`｜%s｜%s｜%s｜数据源：%s%s%s"
             % (md, t["trin"], stale_tag, nums, chg, pcs, health["src_short"], warn_tag, note_tag))
    core2 = "> " + line2
    line1 = ("TRIN（观察指标，不参与档位/仓位）%s %.3f｜%s｜%s｜%s" % (md, t["trin"], nums, pcs, chg))

    last10 = dates[-10:]
    rows = [[d[5:], ("%+.2f%%" % hist[d]["hs300_pct"]) if hist[d]["hs300_pct"] is not None else "—", n0(hist[d]["up"]), n0(hist[d]["down"]),
             n0(hist[d]["flat"]), "%.1f%%" % hist[d]["red_pct"], "%.1f%%" % hist[d]["up_amt_share"],
             ("**%.3f**" % hist[d]["trin"]) if d == D else ("%.3f" % hist[d]["trin"]), "%.0f%%" % pctile(tv, hist[d]["trin"]), label(hist[d]["trin"])]
            for d in last10]
    L = ["## 市场结构：TRIN（阿姆氏指标，观察指标，不参与档位、买卖点和仓位）", "", core1, ">", core2, "",
         health["text"], "",
         "上涨股成交额 %s 亿、下跌股成交额 %s 亿、平盘股成交额 %s 亿。" % (n0(t["amt_up_yi"]), n0(t["amt_dn_yi"]), n0(t["amt_flat_yi"])), "",
         "近 %d 个交易日：" % len(last10), "",
         "| 日期 | 沪深300 | 涨 | 跌 | 平 | 红盘占比 | 上涨额占比 | TRIN | 历史分位 | 按作者口播的线 |", "|---|---|---:|---:|---:|---:|---:|---:|---:|---|"]
    L += ["| " + " | ".join(r) + " |" for r in rows]
    L += ["",
          "口径：TRIN = [上涨家数 ÷ (下跌＋平盘家数)] ÷ [上涨股成交额 ÷ 下跌股成交额]；沪深 A 股约 5,200 只（不含北交所），涨跌按日线 pct，成交用成交额。"
          "该口径由作者图上 5 个读数反推，详见 %s。“按作者口播的线”只是把口播里的 0.7／0.6／0.5 和稿件注的 1.0 对号入座，不代表这些线有统计意义。"
          "TRIN 随红盘占比机械联动（%s），单看绝对值容易读偏，请对照“同广度对照”一行。"
          "作者读法（低＝冰点）与通行读法（极低＝超买）相反，本机还没做全历史检验，这里只给事实，不下结论。"
          "V5.1 实时回测期（第 10.1 条）不改规则，本小节不影响任何信号。" % (DOC, corr_txt)]
    top = [core1, ">", core2]
    ctx = dict(date=D, latest=t, prev=prev, hist_pctile=pc, hist_days=len(tv), same_breadth=sb, label=label(t["trin"]), corr_red_trin=corr,
               last10=[dict(hist[d], pctile=pctile(tv, hist[d]["trin"]), label=label(hist[d]["trin"])) for d in last10])
    return dict(core1=core1, core2=core2, line1=line1, line2=line2, section=L, top=top, ctx=ctx)


def missing_core(md_label, reason, hist, extra=""):
    """算不出来时的核心结论行：写明原因，并给最近一次可用的数。"""
    last = None
    ds = sorted(d for d in hist if hist[d].get("trin") is not None)
    if ds:
        last = hist[ds[-1]]
    tail = ""
    if last:
        tail = "｜最近一次可用：%s TRIN %.3f（红盘占比 %.1f%%）" % (last["date"][5:], last["trin"], last["red_pct"])
    return "> **【阿姆氏 TRIN】**（观察指标，不参与档位/仓位） `%s 未算出`｜原因：%s%s%s" % (md_label, reason, tail, extra)


# ------------------------------------------------------------------ 写文件
def write_text(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    os.replace(tmp, path)


def _pair(s, b, e):
    """找一对标记。返回 (起点, 终点, 问题说明)：都没有 → (None, None, "")；只有一个或顺序反了 → (None, None, 说明)。"""
    i, j = s.find(b), s.find(e)
    if i == -1 and j == -1:
        return None, None, ""
    if i == -1 or j == -1 or j < i:
        return None, None, "已有的 TRIN 标记不成对，不敢动"
    return i, j, ""


def upsert_report(path, date, top_lines, section_lines):
    """把"最上方块"插到日报标题下面、"末尾小节"放到日报最后；各自用标记，已有则原位替换。返回 (结果, 说明)。"""
    if not os.path.exists(path):
        return "skip", "文件不存在"
    s = open(path, encoding="utf-8").read()
    first, sep, rest = s.partition("\n")
    if date not in first:
        return "skip", "首行日期不是 %s（首行：%s）" % (date, first[:40])
    new = s
    # ---- 顶部块
    ti, tj, why = _pair(new, TOP_BEGIN, TOP_END)
    if why:
        return "skip", why
    block_top = TOP_BEGIN + "\n" + "\n".join(top_lines) + "\n" + TOP_END
    if ti is not None:
        new = new[:ti] + block_top + new[tj + len(TOP_END):]
    else:
        f2, sep2, rest2 = new.partition("\n")
        new = f2 + "\n\n" + block_top + ("\n" + rest2 if rest2.startswith("\n") else "\n\n" + rest2)
    # ---- 末尾小节
    bi, bj, why = _pair(new, BEGIN, END)
    if why:
        return "skip", why
    block_bot = BEGIN + "\n" + "\n".join(section_lines) + "\n" + END
    if bi is not None:
        new = new[:bi] + block_bot + new[bj + len(END):]
    else:
        new = new.rstrip("\n") + "\n\n" + block_bot + "\n"
    if new == s:
        return "unchanged", "内容未变"
    write_text(path, new)
    return ("replaced" if (ti is not None or bi is not None) else "appended"), ""


def rel(p):
    try:
        r = os.path.relpath(p, ROOT)
    except ValueError:
        return p
    return p if r.startswith("..") else r                # 测试用的临时目录显示绝对路径，别带一串 ../


# ------------------------------------------------------------------ 主流程
def emit(status, extra=""):
    print("TRIN_PLUGIN=" + PLUGIN_VERSION)
    print("TRIN_STATUS=" + status + extra)


def run(a):
    t0 = time.time()
    today = a.today or datetime.date.today().isoformat()
    live = a.live_dir or D_LIVE
    logd = a.log_dir or D_LOGS
    pipe = a.pipeline_dir or D_PIPE
    rep = a.reports_dir or D_REPORTS
    st_dir = a.state_dir or D_STATE
    hpath = os.path.join(st_dir, "trin_history.csv")
    hist, hsrc = load_history(hpath, a.seed if a.seed is not None else SEED)
    md_today = today[5:]

    if not os.path.isdir(live):
        emit("ERROR", " date=- today=%s msg=日线目录不存在：%s" % (today, live))
        print("TRIN_CORE_1=" + missing_core(md_today, "日线目录不存在", hist))
        print("TRIN_HEALTH=TRIN 数据留意：日线目录不存在，TRIN 未算出。")
        return 0
    hs = read_index(live, a.asof)
    by_date, info = collect(live, a.asof, a.window, hs)
    if not by_date:
        emit("ERROR", " date=- today=%s msg=没读到任何个股行（文件 %d，坏文件 %d）" % (today, info["files"], info["bad_files"]))
        print("TRIN_CORE_1=" + missing_core(md_today, "没读到任何个股行情（文件 %d，坏文件 %d）" % (info["files"], info["bad_files"]), hist))
        print("TRIN_HEALTH=TRIN 数据留意：没读到任何个股行情，TRIN 未算出。")
        return 0
    nref = ref_n(hist) or max(len(v) for v in by_date.values())
    thr = PARTIAL_FRAC * nref
    all_dates = sorted(by_date)
    latest = all_dates[-1]
    n_latest = len(by_date[latest])
    idx_latest = max(hs) if hs else None
    if n_latest < thr:
        reason = "最新一天（%s）只有 %d 只个股有数据，低于预期 %.0f（参考 %.0f×%.2f），日线库可能没更新完" % (latest[5:], n_latest, thr, nref, PARTIAL_FRAC)
        emit("PARTIAL", " date=%s today=%s msg=%s；本次不写任何文件" % (latest, today, reason))
        print("TRIN_CORE_1=" + missing_core(latest[5:], reason, hist))
        print("TRIN_HEALTH=TRIN 数据留意：%s，TRIN 未算出，本次不写任何文件。" % reason)
        return 0

    # 交易日历：优先沪深300；个股里只数够的日子 ∪ 指数里的日子
    full_dates = [d for d in all_dates if len(by_date[d]) >= thr]
    cal = sorted(set(hs) | set(full_dates)) if hs else full_dates
    cal = [d for d in cal if d <= latest]
    window_dates = cal[-a.window:]
    dates = [d for d in window_dates if d in by_date and len(by_date[d]) >= thr]
    D = latest
    fresh = {d: day_row(d, by_date[d], hs.get(d)) for d in dates}
    if fresh[D]["trin"] is None:
        emit("ERROR", " date=%s today=%s msg=最新一天无法计算 TRIN（上涨/下跌成交额为 0）" % (D, today))
        print("TRIN_CORE_1=" + missing_core(D[5:], "最新一天无法计算（上涨或下跌成交额为 0）", hist))
        print("TRIN_HEALTH=TRIN 数据留意：最新一天上涨或下跌成交额为 0，TRIN 未算出。")
        return 0

    logs = load_json(os.path.join(logd, "c_update_log.json"), [])
    blog = load_json(os.path.join(logd, "backfill_batch_log.json"), [])
    if not isinstance(logs, list):
        logs = []
    if not isinstance(blog, list):
        blog = []
    hist_old = {d: dict(v) for d, v in hist.items()}
    health = build_health(D, today, by_date, hs, window_dates, nref, thr, hist_old, fresh, logs, blog)
    diffs = [(d, abs(fresh[d]["trin"] - hist_old[d]["trin"])) for d in dates if d in hist_old and hist_old[d].get("trin") is not None and fresh[d]["trin"] is not None]
    mx = max(diffs, key=lambda x: x[1]) if diffs else None
    for d in dates:
        if fresh[d]["trin"] is not None:
            hist[d] = fresh[d]
    if not a.no_write:
        save_history(hpath, hist)

    dw = "今日" if D == today else "该日"
    stale = D != today
    if stale and idx_latest and idx_latest > D:
        stale_msg = "指数已更新到 %s，个股还停在 %s，今天的行情还没入库" % (idx_latest, D)
    elif stale:
        stale_msg = "最新数据日不是今天：今天非交易日，或日线库没更新到今天"
    else:
        stale_msg = ""
    status = "STALE" if stale else health["level"]
    if stale:
        health["text"] = health["text"] + "。注意：%s" % stale_msg
    out = build(hist, D, dw, health, stale_msg)

    out_files = []
    if not a.no_write:
        os.makedirs(pipe, exist_ok=True)
        stamp = D.replace("-", "")
        jp, mp = os.path.join(pipe, "TRIN_%s.json" % stamp), os.path.join(pipe, "TRIN_%s.md" % stamp)
        payload = dict(plugin_version=PLUGIN_VERSION, run_date=today, generated_at=time.strftime("%Y-%m-%d %H:%M:%S"), status=status,
                       definition="TRIN=[up/(down+flat)]/[up_amount/down_amount]；沪深A股，不含北交所；涨跌按日线pct；成交额",
                       core_1=out["core1"], core_2=out["core2"], health=health["text"], flags=health["flags"], facts=health["facts"],
                       line1=out["line1"], line2=out["line2"], hist_source=hsrc, data_files=info)
        assert not (set(payload) & set(out["ctx"])), "payload 与 ctx 字段重名：%s" % (set(payload) & set(out["ctx"]))
        payload.update(out["ctx"])
        write_text(jp, json.dumps(payload, ensure_ascii=False, indent=1, default=str))
        write_text(mp, "# TRIN 收盘观察 %s\n\n" % D + "\n".join(out["section"]) + "\n")
        out_files = [rel(jp), rel(mp)]

    emit(status, " date=%s today=%s" % (D, today) + ("" if status in ("OK", "WARN") else " msg=%s；已按 %s 计算并存档，未写入日报" % (stale_msg, D)))
    print("TRIN_CORE_1=" + out["core1"])
    print("TRIN_CORE_2=" + out["core2"])
    print("TRIN_HEALTH=" + health["text"])
    print("TRIN_LINE1=" + out["line1"])
    print("TRIN_LINE2=" + out["line2"])
    if mx:
        print("TRIN_CHECK=与历史库重叠 %d 天，TRIN 最大差 %.6f%s" % (len(diffs), mx[1], "" if mx[1] < 1e-4 else "（有变动，详见 TRIN_HEALTH）"))
    else:
        print("TRIN_CHECK=与历史库无重叠日（首次运行或历史库为空）")
    print("TRIN_HIST=来源 %s，共 %d 日（%s ~ %s）" % (hsrc, len(hist), min(hist), max(hist)))
    if out_files:
        print("TRIN_FILES=" + "，".join(out_files))
    if status in ("OK", "WARN") and not a.no_append and not a.no_write:
        res = []
        for fn in ("V51_%s.md" % D.replace("-", ""), "V51_latest.md"):
            how, why = upsert_report(os.path.join(rep, fn), D, out["top"], out["section"])
            res.append("%s:%s%s" % (fn, how, ("（" + why + "）") if why else ""))
        print("TRIN_APPENDED=" + "；".join(res))
    else:
        print("TRIN_APPENDED=无（%s）" % ("最新数据日不是今天" if stale else "按参数不写入"))
    print("TRIN_ELAPSED=%.1fs（个股文件 %d，坏文件 %d，坏行 %d）" % (time.time() - t0, info["files"], info["bad_files"], info["bad_rows"]))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="TRIN 每日收盘插件 2.0（计划任务不带参数直接跑；参数仅用于测试）")
    ap.add_argument("--live-dir"); ap.add_argument("--pipeline-dir"); ap.add_argument("--reports-dir"); ap.add_argument("--state-dir")
    ap.add_argument("--log-dir", help="放 c_update_log.json / backfill_batch_log.json 的目录")
    ap.add_argument("--seed"); ap.add_argument("--today", help="覆盖今天的日期 YYYY-MM-DD（测试用）")
    ap.add_argument("--asof", help="只用不晚于该日的数据（测试用）")
    ap.add_argument("--window", type=int, default=WINDOW)
    ap.add_argument("--no-append", action="store_true"); ap.add_argument("--no-write", action="store_true", help="完全不写文件，只打印")
    a = ap.parse_args(argv)
    try:
        return run(a)
    except Exception as e:                              # noqa  观察指标不能拖垮收盘主流程
        emit("ERROR", " date=- today=%s msg=%s: %s" % (a.today or datetime.date.today().isoformat(), type(e).__name__, e))
        print("TRIN_CORE_1=> **【阿姆氏 TRIN】**（观察指标，不参与档位/仓位） `%s 未算出`｜原因：插件异常（%s: %s）"
              % ((a.today or datetime.date.today().isoformat())[5:], type(e).__name__, str(e)[:80]))
        print("TRIN_HEALTH=TRIN 数据留意：插件运行异常，TRIN 未算出（%s）。" % type(e).__name__)
        for ln in traceback.format_exc().strip().splitlines()[-6:]:
            print("TRIN_TRACE=" + ln)
        return 0


if __name__ == "__main__":
    sys.exit(main())

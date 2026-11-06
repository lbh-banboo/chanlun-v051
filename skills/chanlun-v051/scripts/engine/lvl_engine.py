#!/usr/bin/env python3
"""缠论多级别基础引擎：包含处理、分型（右侧 K 线出现才确认）、MACD。
V5.1 实盘链路只用到 fractals() 与 macd()；run_level()/aggregate() 是研究阶段的多级别区间套工具，保留以便复现研究结论。
纯函数：给一条某级别的 K 线（高、低、收），逐根推进地算分型→笔→中枢，产出
  - 事件型买点 B1 / B2 / B2-0轴 / B3（带确认 K 线序号与结构低点）
  - 每根 K 线收盘时的大级别状态（区间套用）：q1_ok[b]、q2_p1[b]、q3_zg[b]
笔与分型规则：包含处理、右侧 K 线出现才确认、合并序号差 ≥4 成笔、同向取极端延伸。
"""
import numpy as np
import pandas as pd


def macd(c, fast=12, slow=26, sig=9):
    s = pd.Series(c)
    d = s.ewm(span=fast, adjust=False).mean() - s.ewm(span=slow, adjust=False).mean()
    e = d.ewm(span=sig, adjust=False).mean()
    return d.values, e.values, 2 * (d - e).values


def fractals(h, l):
    """包含处理 + 分型。返回按确认时间排序的 [(确认K线序号, 类型, 价, 中间K线首根原始序号, 合并序号)]。"""
    n = len(h)
    mh, ml, mf = [h[0]], [l[0]], [0]
    dirn = 0
    out = []
    for i in range(1, n):
        if (h[i] <= mh[-1] and l[i] >= ml[-1]) or (h[i] >= mh[-1] and l[i] <= ml[-1]):
            if dirn >= 0:
                mh[-1], ml[-1] = max(mh[-1], h[i]), max(ml[-1], l[i])
            else:
                mh[-1], ml[-1] = min(mh[-1], h[i]), min(ml[-1], l[i])
            continue
        dirn = 1 if h[i] > mh[-1] else -1
        mh.append(h[i]); ml.append(l[i]); mf.append(i)
        m = len(mh)
        if m >= 3:
            k = m - 2
            if mh[k] > mh[k - 1] and mh[k] > h[i]:
                out.append((i, "top", mh[k], mf[k], k))
            elif ml[k] < ml[k - 1] and ml[k] < l[i]:
                out.append((i, "bot", ml[k], mf[k], k))
    return out


def _div(e, bar, dif, k0):
    """e = 6 个端点 [top, bot, top, bot, top, bot]，判 B1 背驰（与 1008 X3 相同）。k0 = 最后底分型的确认 K 线。"""
    if not (e[0][1] == "top" and e[5][1] == "bot" and e[5][2] < e[1][2]):
        return False
    hs = [max(e[i][2], e[i + 1][2]) for i in (1, 2, 3)]
    ls = [min(e[i][2], e[i + 1][2]) for i in (1, 2, 3)]
    if not min(hs) > max(ls):
        return False
    sa, ea, sc, ec = e[0][3], e[1][3], e[4][3], k0
    if not (ea > sa and ec > sc):
        return False
    areaA = -np.nansum(np.minimum(bar[sa:ea + 1], 0))
    areaC = -np.nansum(np.minimum(bar[sc:ec + 1], 0))
    dA, dC = dif[sa:ea + 1], dif[sc:ec + 1]
    if np.all(np.isnan(dA)) or np.all(np.isnan(dC)):
        return False
    return bool(areaC < areaA and np.nanmin(dC) > np.nanmin(dA))


def run_level(h, l, c, want_state=False, b3_lookback=120):
    """h,l,c: 本级别 K 线数组（前复权）。返回 dict：
    sig: {name: [(确认K线序号 k, 结构低点)]}，name ∈ B1/B2/B20/B3
    state（want_state=True 时）：q1_ok(bool[n]), q2_p1(float[n], nan=不成立), q3_zg(float[n])，均为"第 b 根收盘时"的状态，
          且已包含"从关键端点之后到第 b 根的最低价"条件。"""
    n = len(c)
    h = np.asarray(h, float); l = np.asarray(l, float); c = np.asarray(c, float)
    dif, dea, bar = macd(c)
    ma5 = pd.Series(c).rolling(5).mean().values
    sig = {"B1": [], "B2": [], "B20": [], "B3": []}
    frs = fractals(h, l)
    pts = []
    divflag = {}          # (确认序号, 中间序号) -> 该底端点当时是否满足 B1 背驰
    st_q1 = np.zeros(n, dtype=bool); st_q2 = np.full(n, np.nan); st_q3 = np.full(n, np.nan)
    fi = 0

    def key(f):
        return (f[0], f[3])

    def confirm(kind, f, k0, extra=None):
        lo = f[2]
        lim = 5 if kind == "B1" else 10
        for k in range(k0, min(n, k0 + lim)):
            if l[f[3]:k + 1].min() < lo - 1e-9:
                return None
            if k < 1:
                continue
            if kind == "B1":
                if c[k] > h[k - 1]:
                    return k
            else:
                if c[k] > c[k - 1] and not np.isnan(ma5[k]) and c[k] > ma5[k]:
                    if extra is not None and not extra(k):
                        continue
                    return k
        return None

    def on_change(f):
        """pts 刚因底分型 f 发生变化（新增或延伸）时判定 B1/B2/B3。"""
        k0 = f[0]
        # B1：需要 6 个端点
        if len(pts) >= 6:
            e = pts[-6:]
            d = _div(e, bar, dif, k0)
            divflag[key(f)] = d
            if d:
                k = confirm("B1", f, k0)
                if k is not None:
                    sig["B1"].append((k, f[2]))
        else:
            divflag[key(f)] = False
        # B2：P1=pts[-3]（满足背驰的底），P2=pts[-2]（顶），P3=pts[-1]=f，P3 > P1
        if len(pts) >= 3 and pts[-3][1] == "bot" and divflag.get(key(pts[-3]), False) and pts[-1][2] > pts[-3][2]:
            k = confirm("B2", f, k0)
            if k is not None:
                sig["B2"].append((k, f[2]))
            p1, p2 = pts[-3], pts[-2]
            seg = dif[p1[3]:p2[3] + 1]
            if len(seg) and np.nanmax(seg) > 0:
                k = confirm("B2", f, k0, extra=lambda kk: dif[kk] > 0)
                if k is not None:
                    sig["B20"].append((k, f[2]))
        # B3：e[0..3] 三笔中枢 → e[4] 顶离开 > ZG → e[5]=f 回抽 > ZG；中枢内曾回撤 ≥25%
        if len(pts) >= 6:
            e = pts[-6:]
            if e[4][1] == "top":
                hs = [max(e[i][2], e[i + 1][2]) for i in range(3)]
                ls = [min(e[i][2], e[i + 1][2]) for i in range(3)]
                ZG, ZD = min(hs), max(ls)
                if ZG > ZD and e[4][2] > ZG and e[5][2] > ZG:
                    a0 = max(0, e[0][3] - b3_lookback)
                    hi_before = c[a0:e[0][3] + 1].max()
                    if min(ls) <= hi_before * 0.75:
                        k = confirm("B3", f, k0)
                        if k is not None:
                            sig["B3"].append((k, f[2]))

    for b in range(n):
        # 处理所有在第 b 根确认的分型
        while fi < len(frs) and frs[fi][0] <= b:
            f = frs[fi]; fi += 1
            changed = False
            if not pts:
                pts.append(f)
                continue
            last = pts[-1]
            if f[1] == last[1]:
                if (f[1] == "top" and f[2] > last[2]) or (f[1] == "bot" and f[2] < last[2]):
                    pts[-1] = f; changed = True
            elif f[4] - last[4] >= 4 and ((f[1] == "top" and f[2] > last[2]) or (f[1] == "bot" and f[2] < last[2])):
                pts.append(f); changed = True
            if changed and f[1] == "bot":
                on_change(f)
        if not want_state or len(pts) < 2:
            continue
        # ---- 大级别状态（第 b 根收盘时）----
        if pts[-1][1] == "top":
            T_ = pts[-1]
            after_lo = l[T_[3] + 1:b + 1].min() if b > T_[3] else np.inf
            # Q1：top,bot,top,bot,top；e1..e4 三笔中枢；T 后创新低；背驰（面积、DIF）
            if len(pts) >= 5:
                e = pts[-5:]
                if e[0][1] == "top":
                    hs = [max(e[i][2], e[i + 1][2]) for i in (1, 2, 3)]
                    ls = [min(e[i][2], e[i + 1][2]) for i in (1, 2, 3)]
                    if min(hs) > max(ls) and after_lo < e[1][2]:
                        sa, ea, sc = e[0][3], e[1][3], T_[3]
                        if ea > sa and b > sc:
                            areaA = -np.nansum(np.minimum(bar[sa:ea + 1], 0))
                            areaC = -np.nansum(np.minimum(bar[sc:b + 1], 0))
                            dA, dC = dif[sa:ea + 1], dif[sc:b + 1]
                            if (not np.all(np.isnan(dA))) and (not np.all(np.isnan(dC))) and areaC < areaA \
                                    and np.nanmin(dC) > np.nanmin(dA):
                                st_q1[b] = True
            # Q2：P1（背驰底）→ P2（顶，最后端点），P2 以来最低 > P1
            if len(pts) >= 2 and pts[-2][1] == "bot" and divflag.get(key(pts[-2]), False):
                if after_lo > pts[-2][2]:
                    st_q2[b] = pts[-2][2]
            # Q3：e0..e3 三笔中枢、e4=T 顶离开 > ZG、e4 以来最低 > ZG、中枢内回撤 ≥25%
            if len(pts) >= 5:
                e = pts[-5:]
                if e[0][1] == "top":
                    hs = [max(e[i][2], e[i + 1][2]) for i in range(3)]
                    ls = [min(e[i][2], e[i + 1][2]) for i in range(3)]
                    ZG, ZD = min(hs), max(ls)
                    if ZG > ZD and e[4][2] > ZG and after_lo > ZG:
                        a0 = max(0, e[0][3] - b3_lookback)
                        if min(ls) <= c[a0:e[0][3] + 1].max() * 0.75:
                            st_q3[b] = ZG
    out = {"sig": sig, "ma5": ma5}
    if want_state:
        out.update(q1_ok=st_q1, q2_p1=st_q2, q3_zg=st_q3)
    return out


def aggregate(dates, o, h, l, c, v, how):
    """把日线（只含有效交易日）聚合成周/月 K。返回 (每根结束的日线位置 end_pos, o,h,l,c,v)。how ∈ W/M。"""
    d = pd.DatetimeIndex(dates)
    if how == "W":
        iso = d.isocalendar()
        key = (iso["year"].astype(int) * 100 + iso["week"].astype(int)).values
    else:
        key = (d.year * 100 + d.month).values
    brk = np.r_[np.nonzero(key[1:] != key[:-1])[0], len(key) - 1]
    start = np.r_[0, brk[:-1] + 1]
    O = o[start]; C = c[brk]
    H = np.maximum.reduceat(h, start); L = np.minimum.reduceat(l, start); V = np.add.reduceat(v, start)
    return brk, O, H, L, C, V

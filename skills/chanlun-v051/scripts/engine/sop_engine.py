#!/usr/bin/env python3
"""缠论 SOP 机械化引擎（V5.1 的 S 引擎＝30 分钟线段引擎）。
逐根推进：笔(lvl_engine.fractals) → 线段(S 引擎) / 笔(D 引擎) 作为次级别单元 → 中枢 → 趋势 → 买卖点。
所有事件带 avail（K 线序号，该根收盘后才可用）。
已修复 Walker.push 的"线段端点被延伸后找不到→永久卡死"缺陷（详见 push 内注释）。
规则全文见 references/02-engine-spec.md；本文件的行为是 V5.1 回测与实时扫描的唯一依据，改动＝改交易系统，须先走预登记与回归测试。"""
import os, sys
import numpy as np
import pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lvl_engine as E


def pen_events(h, l):
    """笔端点事件流：[(avail, 'add'|'ext', (类型, 价, 极值K线序号, avail))]，按 avail 升序。"""
    frs = E.fractals(h, l)
    pts, ev = [], []
    for f in frs:
        it = (f[1], float(f[2]), int(f[3]), int(f[0]))
        if not pts:
            pts.append(f); ev.append((f[0], "add", it)); continue
        last = pts[-1]
        if f[1] == last[1]:
            if (f[1] == "top" and f[2] > last[2]) or (f[1] == "bot" and f[2] < last[2]):
                pts[-1] = f; ev.append((f[0], "ext", it))
        elif f[4] - last[4] >= 4 and ((f[1] == "top" and f[2] > last[2]) or (f[1] == "bot" and f[2] < last[2])):
            pts.append(f); ev.append((f[0], "add", it))
    return ev


def find_seg_end(rest):
    """rest[0] 为线段起点（已确认），rest 为其后笔端点序列（含起点）。
    返回 (结束端点在 rest 的下标, 确认端点在 rest 的下标, 状态)；状态 'ok' 或 'fail'(短线段被破坏)，没有则 None。
    上升线段：跟踪最高顶；其后出现 反向点 r1，之后若再出现比 r1 更低的反向点且期间未创新高 → 在最高顶结束。"""
    up = rest[0][0] == "bot"
    same = "top" if up else "bot"
    s_price = rest[0][1]
    best = None
    r1 = None
    for j in range(1, len(rest)):
        p = rest[j]
        if p[0] == same:
            if best is None or (p[1] > rest[best][1] if up else p[1] < rest[best][1]):
                best = j; r1 = None
        else:
            if best is None:
                continue
            if r1 is None:
                r1 = p[1]
            else:
                broke = p[1] < r1 if up else p[1] > r1
                if broke:
                    if best >= 3:
                        return best, j, "ok"
                    # 不足 3 笔：只有当价格破坏线段起点时视为短线段失败
                    if (p[1] < s_price) if up else (p[1] > s_price):
                        return best, j, "fail"
                # 更极端的反向点刷新 r1
                if (p[1] < r1) if up else (p[1] > r1):
                    r1 = p[1]
    return None


class Walker:
    def __init__(self):
        self.pen = []
        self.seg = []      # 已确认线段端点 (类型, 价, 极值K线序号, avail)
        self.revoked = 0
        self.fixed = 0     # 卡死缺陷修复的触发次数（见 push）

    def push(self, kind, it, k):
        ext_fix = False
        if kind == "add":
            self.pen.append(it)
        else:
            # 卡死缺陷修复：seg[-1] 指向的正是这个被"同向更极端分型"延伸的笔端点时，
            # 线段端点跟着延伸到新极值（与 5.3 第 3 条"短线段作废→前一线段延续到新极值"同一语义）。
            # 修复前这里只替换 pen[-1]，下面按 (类型, 极值K线序号) 再也找不到 seg[-1]，引擎从此永久不再生成线段：
            # 关注池 64 只里 40 只、ev_S 256 只里 161 只从 2020 年开头就卡死。没卡死的票这个情形从未发生（一发生即卡死），
            # 所以修复后它们的结果与修复前逐事件相同。修复前原文件：sop_engine.py.bak_1013_pre_fix（md5 37e20ffc…）。
            old = self.pen[-1]
            if self.seg and self.seg[-1][0] == old[0] and self.seg[-1][2] == old[2]:
                self.seg[-1] = (it[0], it[1], it[2], it[3]); self.fixed += 1; ext_fix = True
            self.pen[-1] = it
        changed = ext_fix
        if not self.seg:
            if len(self.pen) >= 1:
                p0 = self.pen[0]
                self.seg.append((p0[0], p0[1], p0[2], p0[3]))
                changed = True
        while True:
            s0 = self.seg[-1]
            idx = None
            for i in range(len(self.pen) - 1, -1, -1):
                p = self.pen[i]
                if p[2] == s0[2] and p[0] == s0[0]:
                    idx = i; break
            if idx is None:
                break
            rest = self.pen[idx:]
            r = find_seg_end(rest)
            if r is None:
                break
            bi, ci, st = r
            ex = rest[bi]
            if st == "ok":
                self.seg.append((ex[0], ex[1], ex[2], rest[ci][3])); changed = True
            else:
                # 短线段失败：前一线段延续，起点改写为破坏点（该点与起点同类型）
                nw = rest[ci]
                self.seg[-1] = (s0[0], nw[1], nw[2], nw[3]); self.revoked += 1; changed = True
        return changed


def macd_hist(c):
    d, e, b = E.macd(c)
    return d, b


def analyze(units, dif, bar, extra_idx):
    """units: 端点序列 [(类型,价,极值K线序号,avail)]（交替）。unit i = pts[i]→pts[i+1]。
    返回事件列表 [(kind, avail, info)]：
      NEWP 新中枢形成；B3 三买；B1 趋势背驰一买；B1r 盘整背驰候选；(B2 由调用方基于 B1 推出)
    仅向下方向；卖点用镜像序列调用得到。"""
    ev = []
    m = len(units) - 1                 # unit 数
    if m < 3:
        return ev, []
    # unit: (dir, lo, hi, s_idx, e_idx, avail_end, start_price, end_price)
    U = []
    for i in range(m):
        a, b = units[i], units[i + 1]
        U.append((1 if b[1] > a[1] else -1, min(a[1], b[1]), max(a[1], b[1]), a[2], b[2], b[3], a[1], b[1]))
    pivots = []
    i = 0
    prev_break = None                       # 上一个中枢的向下离开 unit 下标
    while i + 2 < m:
        z_g = min(U[i][2], U[i + 1][2], U[i + 2][2])
        z_d = max(U[i][1], U[i + 1][1], U[i + 2][1])
        if not z_g > z_d:
            i += 1
            continue
        pv = dict(i0=i, ZG=z_g, ZD=z_d, avail=U[i + 2][5], first_s=U[i][3], end=None, brk=None, last=i + 2,
                  GG=max(U[i][2], U[i + 1][2], U[i + 2][2]), DD=min(U[i][1], U[i + 1][1], U[i + 2][1]))
        ev.append(("NEWP", U[i + 2][5], dict(ZG=z_g, ZD=z_d, first_s=U[i][3], i0=i)))
        j = i + 3
        nxt = None
        while j < m:
            u = U[j]
            if u[0] == -1:                  # 下行 unit：看低点与前一个上行 unit
                if u[1] >= z_g and j >= i + 4:
                    # 回试低点 ≥ZG，且之前有上行离开 unit（上一个 unit 端点高于 ZG）
                    if U[j - 1][2] > z_g:
                        ev.append(("B3", u[5], dict(ZG=z_g, ZD=z_d, lo=u[1], e_idx=u[4], s_idx=u[3])))
                        pv["end"] = "B3"; nxt = j + 1; break
                if u[1] < z_d:
                    pv["end"] = "BRK"; pv["brk"] = j; nxt = j + 1; break
            else:
                pass
            pv["last"] = j
            j += 1
        pivots.append(pv)
        if pv["end"] is None:
            break
        i = nxt
    # 趋势与背驰
    for n, pv in enumerate(pivots):
        if pv["end"] != "BRK":
            continue
        c = U[pv["brk"]]
        # B1：前一中枢 A 满足 B.ZG < A.ZD 且 A 以向下离开结束
        if n >= 1 and pivots[n - 1]["end"] == "BRK" and pv["ZG"] < pivots[n - 1]["ZD"]:
            b = U[pivots[n - 1]["brk"]]
            kind = "B1"
        else:
            # 盘整背驰候选：进入段 = 中枢第一个 unit 之前的 unit（若为下行）
            if pv["i0"] >= 1 and U[pv["i0"] - 1][0] == -1:
                b = U[pv["i0"] - 1]; kind = "B1r"
            else:
                continue
        if not c[1] < b[1]:
            continue
        # 力度：绿柱面积 + DIF 最低（按各自极值K线区间）
        sa, ea = b[3], b[4]
        sc, ec = c[3], c[4]
        if ea <= sa or ec <= sc:
            continue
        areaA = -np.nansum(np.minimum(bar[sa:ea + 1], 0)); areaC = -np.nansum(np.minimum(bar[sc:ec + 1], 0))
        dA, dC = dif[sa:ea + 1], dif[sc:ec + 1]
        if np.all(np.isnan(dA)) or np.all(np.isnan(dC)):
            continue
        if areaC < areaA and np.nanmin(dC) > np.nanmin(dA):
            ev.append((kind, c[5], dict(lo=c[1], e_idx=c[4], s_idx=c[3], idx_unit=pv["brk"], n_unit=len(U))))
    return ev, U

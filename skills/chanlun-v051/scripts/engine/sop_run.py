#!/usr/bin/env python3
"""逐只票生成事件流：run_series(h, l, c, "S") 是 V5.1 实时扫描与回测共用的入口。
S 引擎＝30 分钟线段；D 引擎＝日线笔（研究对照用）。返回 dict：NEWP/B1/B1r/B2c/B2w/B3/S3/S1 事件列表（每项 (确认K线序号, info)），
另有 revoked（被改写的线段端点数）、fixed（卡死缺陷修复被触发的次数）、n_units。
"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import numpy as np
import sop_engine as SE


def mirror(units):
    return [("top" if u[0] == "bot" else "bot", -u[1], u[2], u[3]) for u in units]


def run_series(h, l, c, mode):
    dif, bar = SE.macd_hist(c)
    ev = SE.pen_events(h, l)
    W = SE.Walker()
    seen = {}
    out = {"NEWP": [], "B1": [], "B1r": [], "B2c": [], "B2w": [], "B3": [], "S3": [], "S1": []}
    b1_list = []
    nrev = 0
    for k, kind, it in ev:
        ch = W.push(kind, it, k)
        if mode == "S":
            if not ch:
                continue
            units = W.seg
        else:
            units = W.pen
        if len(units) < 5:
            continue
        r = SE.analyze(units, dif, bar, None)
        evs, U = r
        for typ, av, info in evs:
            key = (typ, info.get("e_idx", info.get("first_s")))
            if typ == "NEWP":
                key = (typ, info["first_s"], round(info["ZG"], 4))
            if key in seen:
                continue
            seen[key] = k
            if typ == "B1" or typ == "B1r":
                out[typ].append((k, info))
                if typ == "B1":
                    b1_list.append(info)
            elif typ == "B3":
                out["B3"].append((k, info))
            else:
                out["NEWP"].append((k, info))
        # 镜像：卖点
        mu = mirror(units)
        evm, _ = SE.analyze(mu, -dif, -bar, None)
        for typ, av, info in evm:
            if typ == "NEWP":
                continue
            key = ("M" + typ, info.get("e_idx"))
            if key in seen:
                continue
            seen[key] = k
            if typ == "B3":
                out["S3"].append((k, info))
            elif typ == "B1":
                out["S1"].append((k, info))
        # B2：基于 B1
        for info in b1_list:
            p1 = None
            for i in range(len(units) - 1, -1, -1):
                if units[i][2] == info["e_idx"] and units[i][0] == "bot":
                    p1 = i; break
            if p1 is None or len(units) <= p1 + 2:
                continue
            P1, P2, P3 = units[p1], units[p1 + 1], units[p1 + 2]
            if P2[0] != "top" or P3[0] != "bot":
                continue
            key = ("B2", P1[2])
            if key in seen:
                continue
            seen[key] = k
            typ = "B2c" if P3[1] > P1[1] else "B2w"
            out[typ].append((k, dict(lo=P3[1], p1=P1[1], e_idx=P3[2], p1_idx=P1[2])))
    out["revoked"] = W.revoked
    out["fixed"] = getattr(W, "fixed", 0)          # 卡死缺陷修复的触发次数（只计数，不影响事件）
    out["n_units"] = len(W.seg) if mode == "S" else len(W.pen)
    return out

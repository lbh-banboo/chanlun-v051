#!/usr/bin/env python3
"""引擎边界用例（构造数据，精确钉住规则的"边"）。

随机合成序列（test_engine_truncation.py）覆盖"整体行为是否变化"，但覆盖不到几乎永远碰不到的贴线边界
（例如：三买的回试低点恰好等于中枢上沿 ZG——要 600 条随机序列才碰到 67 次）。这些边界是规则的一部分，必须用构造用例钉住：
一旦有人把 `>=` 悄悄改成 `>`、把 0.99 改成 0.98，随机测试可能一声不吭，这里会立刻失败。

用法：python3 tests/test_engine_edges.py
"""
import os
import sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.abspath(os.path.join(HERE, "..", "skills", "chanlun-v051", "scripts", "engine"))
SCRIPTS = os.path.abspath(os.path.join(HERE, "..", "skills", "chanlun-v051", "scripts"))
sys.path.insert(0, ENGINE); sys.path.insert(0, SCRIPTS)
import sop_engine as SE

FAILS = []
N = 0


def check(cond, msg):
    global N
    N += 1
    if not cond:
        FAILS.append(msg); print("  FAIL:", msg)


def units(pts):
    """端点价格序列 → analyze 需要的 units：[(类型, 价, 极值K线序号, avail)]，bot/top 交替，序号递增。"""
    return [("bot" if i % 2 == 0 else "top", float(p), 100 + 20 * i, 103 + 20 * i) for i, p in enumerate(pts)]


def analyze(pts):
    u = units(pts)
    n = u[-1][3] + 60
    c = 10 + np.random.default_rng(7).normal(0, 0.05, n).cumsum() * 0.0
    dif, bar = SE.macd_hist(c + np.random.default_rng(1).normal(0, 0.01, n))
    ev, U = SE.analyze(u, dif, bar, None)
    return ev, U


def b3_lows(ev):
    return [round(i["lo"], 6) for t, a, i in ev if t == "B3"]


# ------------------------------------------------------------------ 三买：回试低点 ≥ ZG（含等于）
# 中枢三段：9→13(上)、13→11(下)、11→13.4(上)  → ZG=min(13,13,13.4)=13，ZD=max(9,11,11)=11
# 随后 13.4→12.2(下，回到中枢内，不是回试)，12.2→15(上，向上离开>ZG)，15→low(下，回试)
def b3_case(low):
    return [9, 13, 11, 13.4, 12.2, 15, low]


for low, want, why in ((13.0, True, "回试低点＝ZG（恰好相等）算三买"), (13.0001, True, "ZG 上方一点点算三买"), (13.012, True, "ZG 上方 0.09% 算三买（不得加容差）"),
                       (14.0, True, "明显高于 ZG 算三买"), (12.9999, False, "ZG 下方一点点不算三买"), (12.0, False, "回到中枢内不算三买")):
    ev, _ = analyze(b3_case(low))
    check((len(b3_lows(ev)) == 1) == want, "三买边界：%s（low=%s，得到 %s）" % (why, low, b3_lows(ev)))

# 三买要求"向上离开 >ZG"：回试之前的上行段高点必须 > ZG
ev, _ = analyze([9, 13, 11, 13.4, 12.2, 12.9, 12.6])      # 反弹只到 12.9 ≤ ZG=13，没有向上离开
check(not b3_lows(ev), "三买需要先向上离开中枢（高点>ZG）：反弹只到 12.9 时不应出三买，得到 %s" % b3_lows(ev))

# ------------------------------------------------------------------ 失效价＝结构低点×0.99
ev, _ = analyze(b3_case(13.5))
info = [i for t, a, i in ev if t == "B3"][0]
check(abs(info["lo"] - 13.5) < 1e-9, "三买事件的 lo 是回试低点 13.5（失效价由调用方×0.99 得到），得到 %s" % info["lo"])

# ------------------------------------------------------------------ 中枢：ZG 必须 > ZD 才成立
ev, _ = analyze([9, 12, 9.5, 11.5, 10, 11.8, 10.2])        # 三段几乎重叠但 ZG>ZD → 成立
check(any(t == "NEWP" for t, a, i in ev), "三段有重叠区间（ZG>ZD）应形成中枢")
ev, _ = analyze([5, 8, 6, 12, 10, 16, 14])                  # 逐段抬高、前两段与第三段无重叠（ZG<=ZD）→ 不成中枢
ev_newp = [i for t, a, i in ev if t == "NEWP"]
check(all(i["ZG"] > i["ZD"] for i in ev_newp), "任何成立的中枢必须 ZG>ZD")

# ------------------------------------------------------------------ 线段：best 只会落在奇数下标（偶数下标是反向端点）
# 这是 `best >= 3` 与 `best >= 2` 等价的原因——文档化，防止有人以为是遗漏
P = lambda t, p, k: (t, float(p), k, k + 3)
rest = [P("bot", 10, 100), P("top", 14, 120), P("bot", 12, 140), P("top", 16, 160), P("bot", 13, 180), P("top", 15, 200), P("bot", 12.5, 220)]
r = SE.find_seg_end(rest)
check(r is not None and r[0] % 2 == 1, "上升线段的结束端点（最高顶）一定在奇数下标，得到 %s" % (r,))
check(r is not None and r[2] == "ok" and r[0] == 3, "10→14→12→16→13→15→12.5：最高顶是 16（下标 3），其后 r1=13、再出现更低的底 12.5 且期间未创新高 → 线段在 16 结束（ok），得到 %s" % (r,))
# 短线段（只有 1 笔）被破坏：价格破起点才算 fail
rest2 = [P("bot", 10, 100), P("top", 14, 120), P("bot", 12, 140), P("top", 13, 160), P("bot", 9, 180)]
r2 = SE.find_seg_end(rest2)
check(r2 is not None and r2[2] == "fail", "只有 1 笔的短线段被反向破坏且价格跌破起点 → fail（前一线段延续），得到 %s" % (r2,))
rest3 = [P("bot", 10, 100), P("top", 14, 120), P("bot", 12, 140), P("top", 13, 160), P("bot", 11.5, 180)]
check(SE.find_seg_end(rest3) is None, "只有 1 笔的短线段被破坏但价格没破起点 10 → 不算结束，继续等")

# ------------------------------------------------------------------ 笔：相邻成笔端点的合并 K 线序号差 ≥4（直接验证 pen_events 的输出）
import lvl_engine as E
rng = np.random.default_rng(11)
bad_gap = 0; n_pen = 0
for seed in range(30):
    r = np.random.default_rng(seed)
    cc = 20 * np.exp(np.cumsum(r.normal(0, 0.012, 1500)))
    hh = cc * (1 + np.abs(r.normal(0, 0.004, 1500))); ll = cc * (1 - np.abs(r.normal(0, 0.004, 1500)))
    fr = {(f[1], f[3]): f for f in E.fractals(hh, ll)}                 # (类型, 首根原始序号) → 分型（含合并序号 f[4]）
    pts = [it for k, kind, it in SE.pen_events(hh, ll) if kind == "add"]
    # pen_events 里 "add" 事件按成笔顺序给出；用类型+极值序号反查合并序号，检查相邻差
    seq = []
    for typ, px, ext_idx, avail in pts:
        cand = [f for key, f in fr.items() if key[0] == typ and f[3] == ext_idx and abs(f[2] - px) < 1e-9]
        if cand: seq.append(cand[0][4])
    for a_, b_ in zip(seq, seq[1:]):
        n_pen += 1
        if b_ - a_ < 4:
            bad_gap += 1
check(n_pen > 200 and bad_gap == 0, "成笔间隔：30 条随机序列、%d 对相邻端点里，合并 K 线序号差 <4 的有 %d 对（应为 0；对数应足够多）" % (n_pen, bad_gap))

# ------------------------------------------------------------------ 包含处理方向
# 向上走势中遇到包含：高点取大、低点取大；向下走势中：高点取小、低点取小
h = np.array([10, 11, 12, 11.8, 12.5, 12.0, 11.0, 10.0, 10.5, 9.5], float)
l = np.array([9.0, 10, 11, 11.2, 11.5, 11.0, 10.0, 9.0, 9.5, 8.5], float)
fr = E.fractals(h, l)
tops = [f for f in fr if f[1] == "top"]
check(any(abs(f[2] - 12.5) < 1e-9 for f in tops), "向上走势里的包含 K 线（11.8/11.2 被 12/11 包含）合并后，顶分型应取到 12.5")

# ------------------------------------------------------------------ 仓位/闸门常量（防止被悄悄改）
import _common as C
check(C.RISK == 0.0075 and C.EXPO_MAX == 0.70 and C.CLUSTER == 0.30 and C.MAXPOS == 20, "账户层常量：单笔风险 0.75%、总仓位上限 70%、主题簇 30%、最多 20 只")
check(C.CAP == {"HEALTHY": .70, "HOT": .50, "REPAIR": .40, "EBB": .20, "HARD": 0.0}, "档位总仓位上限：70/50/40/20/0")
check(C.MULT == {"HEALTHY": 1.0, "HOT": .50, "REPAIR": .75, "EBB": .40, "HARD": 0.0}, "档位风险倍率：1.00/0.50/0.75/0.40/0")
check((C.SLIP, C.BUYF, C.STAMP) == (0.001, 0.00026, 0.0005), "成本：滑点 10bp、买入费 2.6bp、卖出印花税 5bp")
c = C.ceilings(200000.0)
check(abs(c["main"] - 250.0) < 0.01 and abs(c["gem"] - 200.0) < 0.01 and abs(c["star"] - 100.0) < 0.01, "1.5a 价格天花板：主板 净值/800、创业板 净值×10%%/100、科创板 净值×10%%/200，得到 %s" % c)
check(C.limit_pct("300750.SZ") == 0.20 and C.limit_pct("301000.SZ") == 0.20 and C.limit_pct("688001.SH") == 0.20 and C.limit_pct("689009.SH") == 0.20 and C.limit_pct("600000.SH") == 0.10,
      "涨跌幅限制：300/301/688/689 为 20%，其余 10%（301、689 曾被误当 10%）")
check(C.board_of("688001.SH") == "star" and C.board_of("300750.SZ") == "gem" and C.board_of("002000.SZ") == "main", "板块判定")
check(C.regime_key("退潮期(hard)") == "HARD" and C.regime_key("退潮期") == "EBB" and C.regime_key("健康趋势") == "HEALTHY" and C.regime_key("高温分化") == "HOT" and C.regime_key("修复期") == "REPAIR", "档位文本→键")

print("边界用例 %d 项，失败 %d 项" % (N, len(FAILS)))
if FAILS:
    sys.exit(1)
print("通过：三买边界（含 ZG 恰好相等）、线段端点奇偶性与短线段规则、成笔间隔、包含处理方向、账户层常量均与规则一致")

#!/usr/bin/env python3
"""引擎无前视自检（截断重算）：对任意时刻 T，把数据截到 T 重算，已在 T 之前确认的信号必须与全量结果完全一致。

用合成数据（带趋势段、盘整段、跳空的随机游走），不依赖任何行情文件。
判据：对每个截断点 T，全量事件里 "确认 K 线序号 k < T" 的事件集合，必须与只用 [0, T) 数据算出的事件集合逐项相同（类型、k、结构低点、e_idx）。
另测：
  · 黄金快照：固定种子合成序列的事件摘要必须与仓库记录一致（截断重算只抓前视，抓不到"规则被悄悄改了"，这条补上）
  · 事件的确认序号 k 总是 >= 其结构极值序号 e_idx（不会在极值之前"确认"）
  · 同一份数据重复计算结果相同（确定性）
  · Walker 在"线段端点被延伸"场景下不卡死（线段卡死缺陷的回归测试）：卡死的票 n_units 会远小于不卡死版本
用法：python3 tests/test_engine_truncation.py [--seeds 8] [--n 12000]
（真实 30 分钟序列约 13,000 根才出十几个买卖点；合成数据要给到同量级长度，否则事件太少检验不出东西）
"""
import os
import sys
import argparse
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.abspath(os.path.join(HERE, "..", "skills", "chanlun-v051", "scripts", "engine"))
sys.path.insert(0, ENGINE)
import sop_run as R
import sop_engine as SE

TYPES = ("B1", "B1r", "B2c", "B2w", "B3", "S3", "S1")


def synth(seed, n):
    """合成 30 分钟 K 线：分段的趋势/盘整，叠加跳空和随机波动，保证有足够多的笔、线段与中枢。"""
    rng = np.random.default_rng(seed)
    c = np.empty(n); price = 20.0; i = 0
    while i < n:
        seg = int(rng.integers(40, 220))
        drift = rng.choice([-1, 0, 1], p=[0.35, 0.3, 0.35]) * rng.uniform(0.0006, 0.004)
        vol = rng.uniform(0.004, 0.014)
        for _ in range(min(seg, n - i)):
            if rng.random() < 0.02:
                price *= 1 + rng.normal(0, 0.02)
            price *= 1 + drift + rng.normal(0, vol)
            c[i] = max(price, 1.0); i += 1
    o = np.r_[c[0], c[:-1]] * (1 + rng.normal(0, 0.002, n))
    spread = np.abs(rng.normal(0, 0.006, n)) * c
    h = np.maximum(o, c) + spread * rng.uniform(0.2, 1.0, n)
    l = np.minimum(o, c) - spread * rng.uniform(0.2, 1.0, n)
    return h, l, c


def events(res, upto=None):
    out = []
    for t in TYPES:
        for k, info in res[t]:
            if upto is None or k < upto:
                out.append((t, int(k), int(info.get("e_idx", -1)), round(float(info.get("lo", 0.0)), 6)))
    return sorted(out)


GOLDEN_FILE = os.path.join(HERE, "golden_engine_events.json")
GOLDEN_SEEDS = 40       # 40 条×12000 根，每条只算一次（约 3 秒）。8 条时"面积判定翻转会真正改变事件"的分支一次都没碰到（突变检验暴露的盲区），24 条起开始覆盖


def golden_digest():
    """固定种子 × 固定长度的合成序列 → 每条序列的事件列表摘要。用来发现"规则被悄悄改了"（截断测试只抓前视，抓不到规则变化）。"""
    import hashlib, json
    out = {}
    for seed in range(GOLDEN_SEEDS):
        h, l, c = synth(seed, 12000)
        res = R.run_series(h, l, c, "S")
        ev = events(res)
        out[str(seed)] = dict(n=len(ev), sha=hashlib.sha256(json.dumps(ev).encode()).hexdigest()[:16],
                              n_units=int(res["n_units"]), revoked=int(res["revoked"]), fixed=int(res["fixed"]))
    return out


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--seeds", type=int, default=8); ap.add_argument("--n", type=int, default=12000)
    ap.add_argument("--update-golden", action="store_true", help="规则有意改动后，重新生成黄金快照（改动必须先走预登记，并在提交说明里写明）")
    a = ap.parse_args()
    if a.update_golden:
        import json
        json.dump(golden_digest(), open(GOLDEN_FILE, "w"), indent=1, sort_keys=True); print("已写", GOLDEN_FILE); return
    total_ev = 0; checks = 0; fails = []
    for seed in range(a.seeds):
        h, l, c = synth(seed, a.n)
        full = R.run_series(h, l, c, "S")
        again = R.run_series(h, l, c, "S")
        if events(full) != events(again):
            fails.append("seed %d 重复计算结果不同（非确定性）" % seed)
        ev_full = events(full); total_ev += len(ev_full)
        for t, k, e, lo in ev_full:
            if k < e:
                fails.append("seed %d 事件 %s 在 k=%d 确认，却早于结构极值 e_idx=%d" % (seed, t, k, e))
        # 截断点：覆盖全程，并刻意包含每个事件确认点的前一根与后一根（最容易暴露前视的位置）
        cuts = set(int(x) for x in np.linspace(300, a.n - 1, 14))
        for t, k, e, lo in ev_full:
            cuts.update((k, k + 1))                      # 每个事件确认点的前一根与后一根：最容易暴露前视的位置
        for T in sorted(x for x in cuts if 300 <= x <= a.n):
            part = R.run_series(h[:T], l[:T], c[:T], "S")
            want = events(full, upto=T); got = events(part)
            checks += 1
            if want != got:
                diff = sorted(set(want) ^ set(got))[:3]
                fails.append("seed %d 截断 T=%d：全量里 k<T 的事件与截断重算不一致，例：%s" % (seed, T, diff))
    # 回归：Walker 端点被延伸后不卡死。构造"线段起点笔端点被同向更极端分型延伸"的序列，修复版应持续产生线段。
    stuck = 0
    for seed in range(40):
        h, l, c = synth(1000 + seed, 4000)
        res = R.run_series(h, l, c, "S")
        if res["n_units"] < 5 and len(SE.pen_events(h, l)) > 60:
            stuck += 1
    if stuck:
        fails.append("Walker 疑似卡死：%d/40 条序列笔很多却几乎没有线段（线段卡死缺陷的回归）" % stuck)
    # 黄金快照：固定种子的合成序列，事件摘要必须与仓库里记录的一致；不一致＝引擎行为变了（规则改动或缺陷）
    import json
    if os.path.exists(GOLDEN_FILE):
        gold = json.load(open(GOLDEN_FILE)); now = golden_digest()
        for k in sorted(gold):
            if gold[k] != now.get(k):
                fails.append("黄金快照 seed %s 不一致：记录 %s，现在 %s（引擎行为变了；若是有意改规则，须先走预登记，再 --update-golden）" % (k, gold[k], now.get(k)))
    else:
        fails.append("缺少 %s（首次请运行 --update-golden 生成并提交）" % os.path.basename(GOLDEN_FILE))
    print("合成序列 %d 条 × %d 根；买卖点事件合计 %d；截断重算 %d 组；卡死回归 40 条；黄金快照 %d 条" % (a.seeds, a.n, total_ev, checks, len(json.load(open(GOLDEN_FILE))) if os.path.exists(GOLDEN_FILE) else 0))
    if total_ev < 40:
        fails.append("事件太少（%d），测试数据不足以检验——调大 --n 或 --seeds" % total_ev)
    if fails:
        print("失败 %d 项：" % len(fails)); [print("  -", x) for x in fails[:15]]
        sys.exit(1)
    print("通过：所有截断重算与全量结果 0 差异；确认序号均不早于结构极值；重复计算一致；线段不卡死")


if __name__ == "__main__":
    main()

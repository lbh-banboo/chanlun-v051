#!/usr/bin/env python3
"""收盘市场档位（V5.1 第 4 章＝V2.6 §3.2/3.2a/3.2b）：读全市场日线，算当日收盘档位，写 pipeline/regime_YYYYMMDD.json。

判定顺序（全部用沪深300 与全市场收盘数据）：
  ① 沪深300 收盘 < MA20，且全市场收盘站上"自身 MA20"的股票占比 < 45%         → 退潮期 soft（总仓位 20%，倍率 0.40）
  ② trend_up（收盘 > MA20 > MA60，且 MA20 高于 5 日前）且占比 ≥55% 且无过热    → 健康趋势（70%，1.00）
  ③ trend_up，但占比 <55% 或过热                                              → 高温分化（50%，0.50）
  ④ 其余                                                                      → 修复期（40%，0.75）
  撤退条款：沪深300 滚动 126 个交易日跌幅超过 15% → 退潮期改 hard（0%，禁开新仓），至少 63 个交易日；
            之后沪深300 收盘站上 MA60 连续 2 日才恢复。hard 只作用于"当天判为退潮期"的那几天，不是第五档。
  过热＝全市场 5 日平均成交额 ≥ 20 日均值的 1.25 倍，且沪深300 5 日涨幅 < 1.5%。
  从修复期或退潮期升到健康趋势要连续 2 个收盘日确认；降级当日生效。
宽度口径：分母＝当天有收盘价且 MA20 有效的沪深 A 股（含科创、创业、ST，不含北交所），MA20 按每只票自己的交易日序列算
（停牌日不占窗口），前复权收盘；窗口为最近 330 个指数交易日（与实盘一致，改窗口会改变 hard 状态机的历史起点）。

用法：python3 regime_daily.py                 算最近一个交易日，写 pipeline/regime_YYYYMMDD.json
      python3 regime_daily.py --date YYYY-MM-DD
      python3 regime_daily.py --history       再把窗口内每一天的档位写进 pipeline/regime_history.csv（回放、核对用）
      python3 regime_daily.py --dry           只打印，不写文件
输出键：date、state_bt（中文档位名，v51_scan 读它）、breadth_full_market（宽度）、近期 r126 等诊断字段。
"""
import os
import sys
import json
import argparse
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _paths as P

ROWS = 330
WARMUP = 60
BR_HEALTHY, BR_WEAK = 0.55, 0.45
OH_VOL, OH_GAIN = 1.25, 0.015
BT2CN = {"HEALTHY": "健康趋势", "HOT": "高温分化", "REPAIR": "修复期", "EBB": "退潮期", "HARD": "退潮期(hard)"}


def load_panels(data_dir, upto=None, rows=ROWS):
    """日历取指数文件的最近 rows 个交易日；个股只读窗口内的行。返回 (cal, codes, C面板, AMT面板)。"""
    idx = pd.read_csv(os.path.join(data_dir, P.INDEX_CODE + ".csv"))
    cal = [d for d in idx["trade_date"].tolist() if upto is None or d <= upto][-rows:]
    pos = {d: i for i, d in enumerate(cal)}
    codes = sorted(f[:-4] for f in os.listdir(data_dir) if f.endswith(".csv") and not f.startswith(P.INDEX_CODE))
    T, N = len(cal), len(codes)
    C = np.full((T, N), np.nan)
    AMT = np.full((T, N), np.nan)
    for j, c in enumerate(codes):
        df = pd.read_csv(os.path.join(data_dir, c + ".csv"), usecols=["trade_date", "close", "amount"])
        ii = df["trade_date"].map(pos)
        ok = ii.notna().values
        if not ok.any():
            continue
        C[ii[ok].astype(int).values, j] = pd.to_numeric(df["close"], errors="coerce").values[ok]
        AMT[ii[ok].astype(int).values, j] = pd.to_numeric(df["amount"], errors="coerce").values[ok]
    return cal, codes, C, AMT


def ma20_own_days(C):
    """每只票按"自己的交易日序列"算 MA20（停牌日不占窗口），再对回日历面板。"""
    T, N = C.shape
    M = np.full((T, N), np.nan)
    for j in range(N):
        ok = ~np.isnan(C[:, j])
        if ok.sum() < 2:
            continue
        ix = np.where(ok)[0]
        M[ix, j] = pd.Series(C[ix, j]).rolling(20).mean().values
    return M


def market_state(data_dir, upto=None, rows=ROWS):
    """返回 (cal, 每日档位中文名, 每日宽度, 诊断 dict)。"""
    cal, codes, C, AMT = load_panels(data_dir, upto, rows)
    idx = pd.read_csv(os.path.join(data_dir, P.INDEX_CODE + ".csv")).set_index("trade_date")["close"].astype(float)
    ic = idx.reindex(cal)
    im20, im60 = ic.rolling(20).mean(), ic.rolling(60).mean()
    im20p5 = im20.shift(5)
    tu = ((ic > im20) & (im20 > im60) & (im20 > im20p5)).values
    M = ma20_own_days(C)
    valid = ~np.isnan(C) & ~np.isnan(M)
    br = np.where(valid.sum(1) > 0, (np.where(valid, C > M, False)).sum(1) / np.maximum(valid.sum(1), 1), 0.5)
    amt = pd.Series(np.nansum(AMT, axis=1), index=cal)
    a5, a20 = amt.rolling(5).mean().values, amt.rolling(20).mean().values
    ir5 = (ic / ic.shift(5) - 1).values
    oh = (a5 / np.where(a20 > 0, a20, np.nan) >= OH_VOL) & (ir5 < OH_GAIN)
    raw = []
    for t in range(len(cal)):
        if np.isnan(im60.values[t]) or np.isnan(im20p5.values[t]):
            raw.append("REPAIR")
        elif ic.values[t] < im20.values[t] and br[t] < BR_WEAK:
            raw.append("EBB")
        elif tu[t] and br[t] >= BR_HEALTHY and not oh[t]:
            raw.append("HEALTHY")
        elif tu[t]:
            raw.append("HOT")
        else:
            raw.append("REPAIR")
    st = []
    for t in range(len(cal)):
        x = raw[t]
        if x == "HEALTHY" and t > 0 and raw[t - 1] != "HEALTHY" and st[t - 1] in ("REPAIR", "EBB"):
            x = st[t - 1]                                   # 升健康要连续 2 日确认
        st.append(x)
    r126 = (ic / ic.shift(126) - 1).values
    hard, on, hs, a60 = [], False, -1, 0
    for t in range(len(cal)):
        a60 = a60 + 1 if (not np.isnan(im60.values[t]) and ic.values[t] > im60.values[t]) else 0
        if not on and not np.isnan(r126[t]) and r126[t] < -0.15:
            on, hs = True, t
        elif on and t - hs >= 63 and a60 >= 2:
            on = False
        hard.append(on)
    out = [BT2CN["HARD"] if (x == "EBB" and hard[t]) else BT2CN[x] for t, x in enumerate(st)]
    nv = valid.sum(1)
    diag = dict(index_close=float(ic.values[-1]), ma20=float(im20.values[-1]), ma60=float(im60.values[-1]),
                n_valid_prev_median=float(np.median(nv[-11:-1])) if len(nv) > 11 else None,
                r126=None if np.isnan(r126[-1]) else float(r126[-1]), overheat=bool(oh[-1]), trend_up=bool(tu[-1]),
                hard_on=bool(hard[-1]), n_valid=int(valid[-1].sum()), n_above=int((valid[-1] & (C[-1] > M[-1])).sum()))
    return cal, out, br, diag


def main():
    ap = argparse.ArgumentParser(description="收盘市场档位")
    ap.add_argument("--date"); ap.add_argument("--dry", action="store_true"); ap.add_argument("--history", action="store_true")
    ap.add_argument("--data", default=P.DAILY)
    ap.add_argument("--min-valid", type=int, default=1000, help="最新一天至少多少只个股有效（默认 1000；小样本测试时调低）")
    ap.add_argument("--force", action="store_true", help="跳过覆盖闸门")
    a = ap.parse_args()
    if a.data == P.DAILY:
        P.require_data(need_index=True)
    cal, states, br, diag = market_state(a.data, a.date)
    D = cal[-1]
    if a.date and D != a.date:
        sys.exit("日线库里没有 %s（最新 %s），先跑 daily_update.py / backfill_sohu_batch.py" % (a.date, D))
    # 覆盖闸门：最新一天有效个股数明显少于前 10 日 → 日线库没更新完，宽度会被算歪（0 只时宽度默认 0.5、档位被判成修复期）。
    # 档位文件是 v51_scan 逐日重放的输入，写错＝回头改写账本历史，所以宁可不写。相关事故见 references/11。
    prev = diag.get("n_valid_prev_median")
    if not a.force and (diag["n_valid"] < a.min_valid or (prev and diag["n_valid"] < 0.9 * prev)):
        sys.exit("REGIME_ABORT %s：只有 %d 只个股有有效收盘价（要求 ≥%d，前 10 日中位 %s）。日线库没更新完，先跑 daily_update.py / "
                 "backfill_sohu_batch.py 补齐再算；确认无误可加 --force" % (D, diag["n_valid"], a.min_valid, ("%.0f" % prev) if prev else "—"))
    rec = {"date": D, "state_bt": states[-1], "breadth_full_market": float(br[-1]), "diag": diag,
           "prev_state": states[-2] if len(states) > 1 else None}
    print("档位 %s：%s｜宽度 %.2f%%（%d/%d）｜沪深300 %.2f（MA20 %.2f、MA60 %.2f）｜r126 %s｜过热 %s｜hard %s" % (
        D, states[-1], 100 * br[-1], diag["n_above"], diag["n_valid"], diag["index_close"], diag["ma20"], diag["ma60"],
        ("%.2f%%" % (100 * diag["r126"])) if diag["r126"] is not None else "—", diag["overheat"], diag["hard_on"]))
    if a.dry:
        return
    os.makedirs(P.PIPE, exist_ok=True)
    fp = os.path.join(P.PIPE, "regime_%s.json" % D.replace("-", ""))
    json.dump(rec, open(fp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if a.history:
        w = WARMUP                                          # 窗口最前面 60 天 MA60 / 个股 MA20 还没算出来，档位与宽度是默认值，不可信，不写
        pd.DataFrame(dict(date=cal[w:], state=states[w:], breadth=np.round(br[w:], 6))).to_csv(os.path.join(P.PIPE, "regime_history.csv"), index=False)
        print("已写 regime_history.csv（%d 天，已跳过窗口前 %d 天预热期）" % (len(cal) - w, w))
    print("写入", fp)


if __name__ == "__main__":
    main()

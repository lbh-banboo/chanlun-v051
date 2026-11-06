#!/usr/bin/env python3
"""收盘任务漏跑后的补跑（盘中也能安全跑）：只用搜狐的"已收盘日线"，不碰新浪盘中快照。
步骤：①定出"最近一个已收盘交易日" T（搜狐沪深300 日线里早于今天的最后一天；15:30 以后若当天已出也算）；
     ②个股用 backfill_sohu_batch（搜狐一次 40 码）补到 T，多趟直到剩余很少或用完时间；
     ③个股补齐之后才补 000300.SH 指数行。
幂等：已到 T 的跳过。之后再跑 regime_daily.py 出 T 的档位。
用法：python3 catchup_lastday.py        （盘中也能安全跑；桥接/执行通道有 60 秒超时的话，脚本会在后台跑完，等 1—2 分钟再看日线库日期分布）"""
import os, sys, json, time, shutil, runpy
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import daily_update as CU

t0 = time.time()
today = time.strftime("%Y-%m-%d")
idx = CU.sohu_rows("zs_000300", time.strftime("%Y%m%d", time.localtime(time.time() - 40 * 86400)), time.strftime("%Y%m%d"))
done_days = [r for r in idx if r["date"] < today or (r["date"] == today and time.strftime("%H%M") >= "1530")]
if not done_days:
    print("取不到搜狐沪深300 日线，停"); sys.exit(1)
T = done_days[-1]["date"]
print("最近一个已收盘交易日 T =", T)
DATA = CU.DATA
# ② 个股
left = None
for k in range(4):
    if time.time() - t0 > 140:
        break
    os.environ["TARGET"] = T; os.environ["WALL"] = str(max(20, min(50, 150 - (time.time() - t0))))
    runpy.run_path(os.path.join(HERE, "backfill_sohu_batch.py"), run_name="__main__")
    left = sum(1 for f in os.listdir(DATA) if f.endswith(".csv") and not f.startswith("000300.SH") and CU.last_line(os.path.join(DATA, f))[0] < T)
    print("第 %d 趟后仍缺 %d 只" % (k + 1, left), flush=True)
    if left <= 30:
        break
if left is None or left > 30:
    print("个股还没补齐（仍缺 %s），指数先不补；再跑一次本脚本" % left); sys.exit(0)
# ③ 指数行（个股补齐之后才补：regime_daily 以指数日历为准，先补指数会让并发跑的档位计算读到半截库）
pidx = os.path.join(DATA, "000300.SH.csv")
lastI = CU.last_line(pidx)[0]
n_idx = 0
for r in done_days:
    if lastI < r["date"] <= T:
        CU.append(pidx, r["date"], r["open"], r["high"], r["low"], r["close"], r["vol"], r["amount"], None, r["pct"]); n_idx += 1
print(json.dumps(dict(T=T, stocks_left=left, index_appended=n_idx, secs=round(time.time() - t0, 1)), ensure_ascii=False))

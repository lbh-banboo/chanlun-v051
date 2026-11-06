#!/usr/bin/env python3
"""收盘流程（定时任务 18:35 的第 1 步）：① 全市场日线增量更新 → ② 当日收盘档位。
之后由任务依次跑 v51_scan.py（日报与次日待执行单）、trin_daily.py（观察指标，可选）。
档位文件 pipeline/regime_YYYYMMDD.json 是 v51_scan 逐日重放的输入，必须先于 v51_scan 写出，且必须是用"完整的当日日线"算的——
日线库没更新完就算档位会得到错误的"修复期、宽度 50%"（regime_daily.py 的覆盖闸门会拒绝写出并报 REGIME_ABORT）。
建议 15:30 之后跑（新浪快照要收盘后才是当日定稿）；非交易日跑不会写数据。
用法：python3 close_pipeline.py
成功标志（stdout）：出现「档位 YYYY-MM-DD：…」且日期是今天。超时/失败就再跑一次；日线落后（suspended_or_missing 很大或日期不是今天）先跑 backfill_sohu_batch.py。"""
import os
import sys
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
for step, args in (("全市场日线增量", [os.path.join(HERE, "daily_update.py")]),
                   ("当日收盘档位", [os.path.join(HERE, "regime_daily.py")])):
    print("==", step, flush=True)
    try:
        r = subprocess.run([sys.executable] + args, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        print("!! %s 超时（600 秒）" % step); continue
    print((r.stdout or "").strip()[-3000:])
    if r.returncode != 0:
        print("!! %s 失败（exit %s）：%s" % (step, r.returncode, (r.stderr or "").strip()[-1500:]))

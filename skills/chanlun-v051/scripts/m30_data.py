#!/usr/bin/env python3
"""30 分钟历史数据工具（baostock，不复权 adjustflag=3）。需要：pip install baostock

子命令
  download  全量历史（默认 2020-01-01 起）→ data/m30/<code>.csv.gz。已存在的跳过，可断点续跑。
            单进程顺序下载（baostock 多进程会挂死）；约 3—6 秒/只，全市场要几十小时，先下股票池里的票。
  recent    补"历史库末端 → 实时扫描起点"之间的几天 → data/m30_recent/<code>.csv（幂等，已存在的跳过；单次最多 WALL 秒，多跑几次）
  fix       重下被静默截断的历史：按年分段取数，核对覆盖 ≥99% 且末日对才替换，旧文件备份到 data/m30_bak/
  check     与日线库对账：覆盖天数、缺日、每个交易日是否 8 根、收盘/最高/最低偏差、成交额偏差
代码来源：--pool 池文件名（可多个；默认 pools/ 下全部 .md）或 --codes 999001.SH 999003.SZ ...
文件格式：date,time,open,high,low,close,volume,amount；time 为该根 K 线的结束时刻 HHMM（1000/1030/1100/1130/1330/1400/1430/1500）；
          volume 单位股、amount 单位元、价格不复权（读入时乘日线 adj 得前复权价，与日线同口径）。

为什么需要 fix：baostock 单次请求整段历史时，个别票会被中途静默截断（只到某一年），下载器和旧的检查脚本都不报错，
靠核对"每个交易日应有 8 根、对日线交易日历无缺口"才能发现。新票/新池上线前一律先 check。
长任务别在前台跑：用 bg_run.py 启动后台进程，再读日志。
"""
import os
import sys
import csv
import gzip
import io
import json
import time
import socket
import shutil
import argparse
import contextlib
import datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _paths as P
import _common as C

FIELDS = "date,time,open,high,low,close,volume,amount"
LOGS = os.path.join(P.DATA, "logs")
BAK = os.path.join(P.DATA, "m30_bak")


def _bs():
    try:
        import baostock as bs
    except ImportError:
        raise SystemExit("缺少 baostock：pip install baostock")
    socket.setdefaulttimeout(90)
    return bs


def quiet(f, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return f(*a, **k)


def login(bs, tries=5, wait=10):
    for i in range(tries):
        try:
            lg = quiet(bs.login)
            if lg.error_code == "0":
                return True
        except Exception:                                        # noqa
            pass
        time.sleep(wait * (i + 1))
    return False


def fetch_range(bs, code, start, end):
    num, ex = code.split(".")
    rs = bs.query_history_k_data_plus("%s.%s" % (ex.lower(), num), FIELDS, start_date=start, end_date=end, frequency="30", adjustflag="3")
    if rs.error_code != "0":
        raise RuntimeError("%s %s" % (rs.error_code, rs.error_msg))
    rows = []
    while rs.next():
        rows.append(rs.get_row_data())
    if rs.error_code != "0":
        raise RuntimeError("%s %s" % (rs.error_code, rs.error_msg))
    return rows


def norm_rows(rows):
    """baostock 的 time 形如 20200102100000000 → 取 8:12 得 HHMM；(date,time) 去重后排序。"""
    seen = {}
    for r in rows:
        r = list(r)
        r[1] = r[1][8:12]
        seen[(r[0], r[1])] = r
    return [seen[k] for k in sorted(seen)]


def codes_from_args(a):
    if a.codes:
        return sorted(set(a.codes))
    pools = a.pool or sorted(f for f in os.listdir(P.POOLS) if f.endswith(".md"))
    out = set()
    for pf in pools:
        out |= set(C.read_pool(pf)[0])
    if not out:
        raise SystemExit("没有找到股票代码：检查 %s 下的池文件，或用 --codes" % P.POOLS)
    return sorted(out)


def write_gz(path, rows):
    with gzip.open(path + ".tmp", "wt", newline="") as f:
        w = csv.writer(f)
        w.writerow(FIELDS.split(","))
        w.writerows(rows)
    os.replace(path + ".tmp", path)


# ------------------------------------------------------------------ download
def cmd_download(a):
    bs = _bs()
    os.makedirs(P.M30, exist_ok=True); os.makedirs(LOGS, exist_ok=True)
    codes = codes_from_args(a)
    end = a.end or (dt.date.today() - dt.timedelta(days=1)).isoformat()
    if not login(bs):
        raise SystemExit("baostock 登录失败")
    t0 = time.time(); done = 0; fails = {}
    for code in codes:
        if os.path.exists(os.path.join(a.stop_file or LOGS, "STOP")):
            print("发现 STOP 文件，停止"); break
        p = os.path.join(P.M30, code + ".csv.gz")
        if os.path.exists(p):
            continue
        ok = False
        for att in range(3):
            try:
                rows = norm_rows(fetch_range(bs, code, a.start, end))
                write_gz(p, rows); ok = True; done += 1
                break
            except Exception as e:                               # noqa
                fails[code] = repr(e)[:120]
                quiet(bs.logout); time.sleep(15 * (att + 1)); login(bs)
        if ok:
            fails.pop(code, None)
        if done % 5 == 0 or not ok:
            json.dump({"updated": time.strftime("%Y-%m-%d %H:%M:%S"), "end": end, "total": len(codes), "this_run_done": done,
                       "have": sum(os.path.exists(os.path.join(P.M30, c + ".csv.gz")) for c in codes), "fails": fails},
                      open(os.path.join(LOGS, "m30_download_status.json"), "w"), ensure_ascii=False, indent=1)
    quiet(bs.logout)
    print("finished 新下 %d 只，失败 %d 只，%.0fs" % (done, len(fails), time.time() - t0))


# ------------------------------------------------------------------ recent
def cmd_recent(a):
    bs = _bs()
    os.makedirs(P.M30_RECENT, exist_ok=True)
    codes = codes_from_args(a)
    start = a.start
    end = a.end or (dt.date.today() - dt.timedelta(days=1)).isoformat()
    wall = int(os.environ.get("WALL", "200"))                    # 前台跑 200 秒；后台跑设 1800
    lg = quiet(bs.login); print("login", lg.error_code, lg.error_msg, flush=True)
    t0 = time.time(); n = 0; errs = 0
    for c in codes:
        p = os.path.join(P.M30_RECENT, c + ".csv")
        if os.path.exists(p):
            continue
        if time.time() - t0 > wall:
            break
        try:
            rows = norm_rows(fetch_range(bs, c, start, end))
        except Exception as e:                                   # noqa
            print("ERR", c, repr(e)[:80], flush=True); errs += 1; continue
        if len(rows) < 8:                                        # 0 根或接收出错：不落盘，下趟重试（停牌票也会走到这里，人工确认）
            print("SHORT", c, len(rows), flush=True); errs += 1; continue
        with open(p + ".tmp", "w", newline="") as f:
            w = csv.writer(f); w.writerow(FIELDS.split(",")); w.writerows(rows)
        os.replace(p + ".tmp", p); n += 1
        if n % 10 == 0:
            print("progress", n, c, "%.0fs" % (time.time() - t0), flush=True)
    done = sum(os.path.exists(os.path.join(P.M30_RECENT, c + ".csv")) for c in codes)
    print("codes", len(codes), "done", done, "new", n, "errs", errs, "%.0fs" % (time.time() - t0), flush=True)
    quiet(bs.logout)


# ------------------------------------------------------------------ fix
def daily_days(code):
    ds = set()
    for d in (P.DAILY_HIST, P.DAILY):
        p = os.path.join(d, code + ".csv")
        if os.path.exists(p):
            for r in csv.DictReader(open(p)):
                ds.add(r["trade_date"][:10])
    return ds


def coverage(rows, exp_days, start, end):
    """返回 (覆盖率, 缺失日列表, 非 8 根的天数, 末日)。exp_days＝日线交易日历。"""
    days = {}
    for r in rows:
        days[r[0]] = days.get(r[0], 0) + 1
    exp = {d for d in exp_days if start <= d <= end}
    miss = sorted(exp - set(days))
    n8 = sum(1 for v in days.values() if v != 8)
    return 1 - len(miss) / max(1, len(exp)), miss, n8, (max(days) if days else "")


def cmd_fix(a):
    bs = _bs()
    os.makedirs(BAK, exist_ok=True)
    end = a.end or (dt.date.today() - dt.timedelta(days=1)).isoformat()
    codes = codes_from_args(a)
    if not login(bs, wait=5):
        raise SystemExit("baostock 登录失败")
    y0 = int(a.start[:4]); y1 = int(end[:4])
    for code in codes:
        t0 = time.time(); allrows = []; ok = True
        for y in range(y0, y1 + 1):
            s_, e_ = ("%d-01-01" % y if y > y0 else a.start), ("%d-12-31" % y if y < y1 else end)
            rows = None
            for att in range(3):
                try:
                    rows = fetch_range(bs, code, s_, e_); break
                except Exception as e:                           # noqa
                    print("  retry", code, s_, e_, repr(e)[:80], flush=True)
                    quiet(bs.logout); time.sleep(8 * (att + 1)); login(bs, wait=5)
            if rows is None:
                ok = False; break
            allrows += rows
        if not ok:
            print("FAIL", code, "分段取数失败"); continue
        rows = norm_rows(allrows)
        cover, miss, n8, last = coverage(rows, daily_days(code), a.start, end)
        print("%s rows %d 缺 %d 非8根 %d 末日 %s 覆盖 %.4f  %.0fs" % (code, len(rows), len(miss), n8, last, cover, time.time() - t0), flush=True)
        if cover < 0.99 or last != end:
            print("NOT_REPLACED", code, "核对不通过，不覆盖（末日应为 %s；可能该日停牌，人工确认后可用 --end 指向实际末日）" % end); continue
        p = os.path.join(P.M30, code + ".csv.gz")
        if os.path.exists(p):
            shutil.copy2(p, os.path.join(BAK, code + ".csv.gz"))
        write_gz(p, rows)
        print("REPLACED", code, "旧文件已备份到 data/m30_bak/", flush=True)
    quiet(bs.logout)


# ------------------------------------------------------------------ check
def cmd_check(a):
    import numpy as np
    import pandas as pd
    os.makedirs(LOGS, exist_ok=True)

    def daily(code):
        frames = []
        for d in (P.DAILY_HIST, P.DAILY):
            p = os.path.join(d, code + ".csv")
            if os.path.exists(p):
                frames.append(pd.read_csv(p, parse_dates=["trade_date"]))
        if not frames:
            return None
        x = pd.concat(frames).drop_duplicates("trade_date", keep="last").set_index("trade_date").sort_index()
        for k in ("open", "high", "low"):
            x["raw_" + k] = x[k] / x["adj"]
        return x

    res = {}
    codes = codes_from_args(a)
    for code in codes:
        p = os.path.join(P.M30, code + ".csv.gz")
        if not os.path.exists(p):
            res[code] = {"bars": 0, "note": "无 30 分钟历史文件"}; continue
        m = pd.read_csv(p, parse_dates=["date"])
        if m.empty:
            res[code] = {"bars": 0}; continue
        g = m.groupby("date").agg(o=("open", "first"), h=("high", "max"), l=("low", "min"), c=("close", "last"), amt=("amount", "sum"), n=("time", "size"))
        d = daily(code)
        if d is None:
            res[code] = {"bars": len(m), "note": "无日线文件"}; continue
        d = d[(d.index >= g.index.min()) & (d.index <= g.index.max()) & (d.volume > 0)]
        j = g.join(d[["raw_open", "raw_high", "raw_low", "raw_close", "amount"]], how="inner")
        bad_c = (np.abs(j.c - j.raw_close) > 0.011).mean()
        bad_h = (np.abs(j.h - j.raw_high) > 0.011 + j.raw_high * 0.0005).mean()
        bad_l = (np.abs(j.l - j.raw_low) > 0.011 + j.raw_low * 0.0005).mean()
        amt = (j.amt / j.amount - 1).abs()
        res[code] = {"bars": len(m), "days": len(g), "daily_days": len(d), "last": str(g.index.max())[:10],
                     "miss_days": int(len(set(d.index) - set(g.index))), "extra_days": int(len(set(g.index) - set(d.index))),
                     "not8": int((g.n != 8).sum()), "bad_close": round(float(bad_c), 4), "bad_high": round(float(bad_h), 4),
                     "bad_low": round(float(bad_l), 4), "amt_dev_med": round(float(amt.median()), 4)}
    R = pd.DataFrame(res).T
    summ = {"checked": len(R), "empty": int((R.bars == 0).sum())}
    if "days" in R:
        R2 = R[R.bars > 0].dropna(subset=["days"]).copy()
        for k in ("miss_days", "daily_days", "not8", "bad_close", "bad_high", "bad_low", "amt_dev_med"):
            R2[k] = R2[k].astype(float)
        summ.update({"stocks_with_miss": int((R2.miss_days > 0).sum()), "miss_days_total": int(R2.miss_days.sum()),
                     "not8_days_total": int(R2.not8.sum()), "bad_close_mean": round(R2.bad_close.mean(), 4),
                     "amt_dev_med": round(R2.amt_dev_med.median(), 4),
                     "worst_miss": R2.miss_days.sort_values().tail(5).to_dict(),
                     "last_dates": sorted(set(R2["last"]))[-3:]})
    json.dump({"summary": summ, "per": res}, open(os.path.join(LOGS, "check_m30.json"), "w"), ensure_ascii=False, indent=1)
    print(json.dumps(summ, ensure_ascii=False, indent=1))
    print("逐只明细 → %s" % os.path.join(LOGS, "check_m30.json"))


def main():
    ap = argparse.ArgumentParser(description="30 分钟历史数据工具（baostock）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn, h in (("download", cmd_download, "全量历史"), ("recent", cmd_recent, "补近期"), ("fix", cmd_fix, "重下被截断的"), ("check", cmd_check, "与日线对账")):
        sp = sub.add_parser(name, help=h)
        sp.add_argument("--pool", nargs="*", help="池文件名（pools/ 下），可多个；默认全部 .md")
        sp.add_argument("--codes", nargs="*", help="直接给代码，如 999001.SH")
        sp.add_argument("--start", default="2020-01-01" if name in ("download", "fix") else None, help="起始日期 YYYY-MM-DD")
        sp.add_argument("--end", help="截止日期（默认昨天）")
        sp.add_argument("--stop-file", help="download：放 STOP 文件的目录（默认 data/logs）")
        sp.set_defaults(fn=fn)
    a = ap.parse_args()
    if a.cmd == "recent" and not a.start:
        raise SystemExit("recent 需要 --start（历史库末端之后的第一个交易日，例如 m30_base_end 之后一天往前留几天余量）")
    a.fn(a)


if __name__ == "__main__":
    main()

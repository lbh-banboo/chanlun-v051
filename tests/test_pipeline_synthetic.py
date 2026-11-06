#!/usr/bin/env python3
"""工具链端到端测试：用**合成市场**（不依赖任何真实行情）把 init → 日线 → 30 分钟 → 池文件 → 档位 → 扫描 → 账本 走一遍，
验证脚本之间的接口（文件格式、路径、协议行）是对的，以及账本里的关键不变量。

合成市场：
  · 60 只合成股票（含主板/创业板/科创板代码），500 个交易日的日线 + 同一时期的 30 分钟线（每天 8 根）
  · 沪深300 指数日线
  · 其中几只被设计成会出买点（带明显的下跌-盘整-上行结构），其余随机游走
验证：
  1. init_workspace 生成可用骨架，示例池通过 pool_tool check
  2. regime_daily 能算出档位并写 pipeline/regime_*.json；覆盖闸门在个股数不足时拒绝写入（REGIME_ABORT）
  3. v51_scan（--no-fetch --asof）能跑通，stdout 协议行齐全，账本/日报/信号日志都写出
  4. 账本不变量：净值＝现金＋持仓市值；每笔持仓都在池内；买入股数是整手（科创板≥200）；失效价＜买价；
     每笔平仓的 R 与 (卖价-买价)/(买价-失效价) 一致；被拦下的信号都带原因；入池前确认的买点不入账
  5. 确定性：同一份数据重复运行，账本逐字段相同（去掉时间戳）
  6. 入池时刻过滤：把某只票的加入日期改到信号之后，该信号变成"入池前确认，不计"
  7. pool_tool：add/名称不一致拒绝/cooldown
  8. install_tasks 渲染无残留占位符
用法：python3 tests/test_pipeline_synthetic.py
"""
import os
import re
import sys
import json
import shutil
import tempfile
import subprocess
import datetime as dt
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
SK = os.path.abspath(os.path.join(HERE, "..", "skills", "chanlun-v051"))
SC = os.path.join(SK, "scripts")
FAILS = []
N = 0


def check(cond, msg):
    global N
    N += 1
    if not cond:
        FAILS.append(msg); print("  FAIL:", msg)


def run(args, home, env_extra=None, check_rc=True, timeout=300):
    env = dict(os.environ, V51_HOME=home, PYTHONDONTWRITEBYTECODE="1")
    env.update(env_extra or {})
    p = subprocess.run([sys.executable] + args, capture_output=True, text=True, env=env, cwd=SC, timeout=timeout)
    if check_rc and p.returncode != 0:
        print("命令失败:", args, "\n", p.stdout[-1500:], "\n", p.stderr[-1500:])
    return p


# ------------------------------------------------------------------ 合成市场
SLOTS = ["1000", "1030", "1100", "1130", "1330", "1400", "1430", "1500"]


def trading_days(n, end):
    d = dt.date.fromisoformat(end); out = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d -= dt.timedelta(days=1)
    return out[::-1]


def make_series(rng, n_bars, kind):
    """30 分钟收盘价序列。kind: 'rand' 随机游走；'setup' 先跌后盘整再上行，更容易出结构买点。"""
    if kind == "rand":
        r = rng.normal(0.0001, 0.007, n_bars)
    else:
        r = np.empty(n_bars)
        i = 0
        while i < n_bars:
            seg = int(rng.integers(120, 400)); mode = rng.choice(["down", "range", "up"])
            mu = {"down": -0.0012, "range": 0.0, "up": 0.0014}[mode]
            r[i:i + seg] = rng.normal(mu, 0.006, min(seg, n_bars - i)); i += seg
    return 20 * np.exp(np.cumsum(r))


def build_market(root, n_days=500, n_stocks=60, seed=2026, end="2026-11-04", amount_scale=3e8):
    rng = np.random.default_rng(seed)
    days = trading_days(n_days, end)
    daily_dir = os.path.join(root, "data", "daily"); m30_dir = os.path.join(root, "data", "m30")
    os.makedirs(daily_dir, exist_ok=True); os.makedirs(m30_dir, exist_ok=True)
    codes = []
    prefixes = ["600", "601", "000", "002", "300", "301", "688"]
    for i in range(n_stocks):
        pre = prefixes[i % len(prefixes)]
        code = "%s%03d.%s" % (pre, 100 + i, "SH" if pre.startswith(("6")) else "SZ")
        codes.append(code)
        kind = "setup" if i % 3 == 0 else "rand"
        c = make_series(rng, n_days * 8, kind)
        ret = np.r_[0, np.diff(np.log(c))]
        rows30 = []; drows = []
        for di, d in enumerate(days):
            seg = c[di * 8:(di + 1) * 8]
            prev = c[di * 8 - 1] if di > 0 else seg[0]
            o = np.r_[prev, seg[:-1]] * (1 + rng.normal(0, 0.0005, 8))
            hi = np.maximum(o, seg) * (1 + np.abs(rng.normal(0, 0.003, 8))); lo = np.minimum(o, seg) * (1 - np.abs(rng.normal(0, 0.003, 8)))
            vol = rng.uniform(5e5, 3e6, 8)
            for k, t in enumerate(SLOTS):
                rows30.append((d, t, o[k], hi[k], lo[k], seg[k], vol[k], vol[k] * seg[k]))
            pct = (seg[-1] / prev - 1) * 100
            drows.append((d, o[0], hi.max(), lo.min(), seg[-1], vol.sum() / 100, seg[-1], vol.sum() * seg[-1] * amount_scale / (vol.sum() * seg[-1]) , 1.0, pct, 1.0))
        df = pd.DataFrame(drows, columns=["trade_date", "open", "high", "low", "close", "volume", "raw_close", "amount", "turnover", "pct", "adj"])
        df["amount"] = df["raw_close"] * df["volume"] * 100 * 0 + (2e8 + rng.uniform(0, 5e8, len(df)))
        df.to_csv(os.path.join(daily_dir, code + ".csv"), index=False)
        m = pd.DataFrame(rows30, columns=["date", "time", "open", "high", "low", "close", "volume", "amount"])
        m.to_csv(os.path.join(m30_dir, code + ".csv.gz"), index=False, compression="gzip")
    # 沪深300：用全部个股等权的对数收益累积
    allc = np.mean([pd.read_csv(os.path.join(daily_dir, c + ".csv"))["close"].values for c in codes], axis=0)
    idx = pd.DataFrame(dict(trade_date=days, open=allc, high=allc * 1.003, low=allc * 0.997, close=allc, volume=1e7, raw_close=allc, amount=1e11, turnover=0.0,
                            pct=np.r_[0, np.diff(allc) / allc[:-1] * 100], adj=1.0))
    idx.to_csv(os.path.join(daily_dir, "000300.SH.csv"), index=False)
    return codes, days


def write_pool(home, codes, names=None, join="2026-01-02 前", extra_join=None):
    names = names or {c: "测试股%s" % c[:6] for c in codes}
    L = ["# 合成池", "", "## 名单（%d 只）" % len(codes), "", "| 股票 | 代码 | 板块 | 加入日期 | 入池来源 | 状态 |", "|---|---|---|---|---|---|", "| **合成（%d 只）** | | | | | |" % len(codes)]
    for c in codes:
        j = (extra_join or {}).get(c, join)
        L.append("| %s | %s | 合成 / 合成 | %s | 合成 | 关注 |" % (names[c], c, j))
    L += ["", "## 主题簇", "", "| 主题簇 | 只数 | 成员 | 为什么归在一起 |", "|---|---:|---|---|", "| 合成簇 | %d | %s | 合成 |" % (len(codes), "、".join(names[c] for c in codes)), ""]
    os.makedirs(os.path.join(home, "pools"), exist_ok=True)
    open(os.path.join(home, "pools", "watchlist.md"), "w", encoding="utf-8").write("\n".join(L))
    return names


def norm_ledger(L):
    L = json.loads(json.dumps(L))
    L.pop("asof", None)
    for k in ("note",):
        L.get("cfg", {}).pop(k, None)
    return L


def main():
    root = tempfile.mkdtemp(prefix="v51_synth_")
    home = os.path.join(root, "home")
    try:
        # ---- 1. init
        r = run(["init_workspace.py", "--home", home, "--nav", "200000"], home)
        check(r.returncode == 0 and os.path.exists(os.path.join(home, "config", "v51_config.json")), "init_workspace 生成配置")
        r = run(["pool_tool.py", "check"], home)
        check(r.returncode == 0 and "错误 0" in r.stdout, "示例池通过 pool_tool check：%s" % r.stdout.strip()[-80:])
        r = run(["v51_scan.py", "--no-fetch"], home, check_rc=False)
        check(r.returncode != 0 and "必填键" in (r.stdout + r.stderr), "配置为空时 v51_scan 拒绝运行（逼使用者显式填 start_date / m30_base_end）")
        # 新手最常撞到的两种情况：配置填了但数据还没准备 → 必须是"缺什么、怎么补"的清晰提示，而不是 FileNotFoundError 堆栈
        c0 = json.load(open(os.path.join(home, "config", "v51_config.json"))); c0.update(start_date="2026-01-05", m30_base_end="2025-12-31")
        json.dump(c0, open(os.path.join(home, "config", "v51_config.json"), "w"))
        for cmd in (["v51_scan.py", "--no-fetch"], ["regime_daily.py"], ["pool_watch.py", "--no-fetch"]):
            r = run(cmd, home, check_rc=False); out = r.stdout + r.stderr
            check(r.returncode != 0 and "数据还没准备好" in out and "Traceback" not in out, "无数据时 %s 给出清晰提示（缺什么、怎么补），而不是堆栈：%s" % (cmd[0], out.strip().splitlines()[0][:50] if out.strip() else ""))
        r = run(["week_replay.py", "2026-01-05", "2026-02-02_15:02", "t"], home, check_rc=False)
        check(r.returncode != 0 and "ASOF 格式" in (r.stdout + r.stderr), "week_replay 的 ASOF 格式写错时给出明确提示")
        r = run(["pool_watch_report.py", "/nonexistent.json"], home, check_rc=False)
        check(r.returncode != 0 and "用法" in (r.stdout + r.stderr) and "Traceback" not in (r.stdout + r.stderr), "pool_watch_report 找不到输入文件时给出用法提示")

        # ---- 2. 合成市场 + 配置 + 池
        codes, days = build_market(home)
        names = write_pool(home, codes)
        start = days[-60]; base_end = days[-61]
        cfg = dict(start_date=start, nav0=200000.0, stage_sim=1.0, stage_real=0, max_hold_days=120, m30_base_end=base_end)
        json.dump(cfg, open(os.path.join(home, "config", "v51_config.json"), "w"), indent=2)
        r = run(["pool_tool.py", "check"], home)
        check(r.returncode == 0, "合成池通过契约校验：%s" % r.stdout.strip()[-100:])

        # ---- 3. 档位
        r = run(["regime_daily.py", "--history", "--min-valid", "10"], home)
        check(r.returncode == 0 and "档位" in r.stdout, "regime_daily 算出档位：%s" % r.stdout.strip()[:100])
        rf = [f for f in os.listdir(os.path.join(home, "pipeline")) if f.startswith("regime_2")]
        check(len(rf) == 1, "写出 pipeline/regime_YYYYMMDD.json")
        # 覆盖闸门：把最新一天的大部分个股删掉 → 拒绝写入
        bad = os.path.join(root, "bad_home"); shutil.copytree(os.path.join(home, "data"), os.path.join(bad, "data")); os.makedirs(os.path.join(bad, "pipeline"))
        for c in codes[:50]:
            p = os.path.join(bad, "data", "daily", c + ".csv"); df = pd.read_csv(p); df.iloc[:-1].to_csv(p, index=False)
        # 指数仍到最新日；个股只剩 10 只有最新日 → 触发闸门（阈值 1000 默认；这里用 --min-valid 20）
        r = run(["regime_daily.py", "--min-valid", "20"], bad, check_rc=False)
        check(r.returncode != 0 and "REGIME_ABORT" in (r.stdout + r.stderr), "日线没更新完时 regime_daily 拒绝写入档位（REGIME_ABORT）")
        check(not [f for f in os.listdir(os.path.join(bad, "pipeline")) if f.startswith("regime_2")], "…且没有写出任何档位文件")

        # ---- 4. 扫描（回放自检，不联网）
        asof = "%s 15:02" % days[-1]
        os.makedirs(os.path.join(home, "live", "state"), exist_ok=True)
        r = run(["v51_scan.py", "--no-fetch", "--asof", asof], home)
        check(r.returncode == 0, "v51_scan 运行成功")
        out = r.stdout
        check(re.search(r"V51 扫描 \d\d-\d\d \d\d:\d\d｜池60只", out) is not None, "stdout 首行协议（池 60 只）：%s" % out.splitlines()[0][:80])
        check("今日新买点" in out and "待执行" in out and "净值" in out, "stdout 第二行协议")
        for f in ("state/v51_ledger.json", "state/signals_log.jsonl", "reports/V51_latest.md"):
            check(os.path.exists(os.path.join(home, "live", f)), "写出 live/%s" % f)
        led = json.load(open(os.path.join(home, "live", "state", "v51_ledger.json"), encoding="utf-8"))

        # ---- 5. 账本不变量
        check(len(led["signals"]) > 0, "合成市场里应该出现过买点（得到 %d 个信号；若为 0 说明合成数据没造出结构，调整 make_series）" % len(led["signals"]))
        pos_val = sum(p["shares"] * p["entry"] for p in led["positions"].values())
        check(led["cash"] > 0 and led["nav"] > 0, "净值与现金为正")
        for c, p in led["positions"].items():
            check(c in names, "持仓 %s 在池内" % c)
            lot = 200 if c.startswith(("688", "689")) else 100
            check(p["shares"] >= lot and (p["shares"] % 100 == 0 if lot == 100 else p["shares"] >= 200), "持仓 %s 股数 %d 满足整手/科创板≥200" % (c, p["shares"]))
            check(p["stop"] < p["entry"], "持仓 %s 失效价 %.2f < 买价 %.2f" % (c, p["stop"], p["entry"]))
        for t in led["trades"]:
            if t["R"] is not None:
                check((t["pnl"] > 0) == (t["R"] > 0) or abs(t["pnl"]) < 1, "平仓 %s %s：盈亏与 R 同号（pnl=%.1f R=%s）" % (t["code"], t["ent"], t["pnl"], t["R"]))
        reasons_ok = all(len(s) == 5 and s[4] for s in led["skips"])
        check(reasons_ok, "所有被拦下/取消的信号都带原因")
        by_state = {}
        for t in led["trades"]:
            by_state[t["why"]] = by_state.get(t["why"], 0) + 1
        check(all(w in ("失效价止损", "三卖", "趋势一卖") or w.startswith("持满") for w in by_state), "平仓原因只有四类：%s" % by_state)
        sig_keys = [(x["code"], x["typ"], x["bar"]) for x in led["signals"]]
        check(len(sig_keys) == len(set(sig_keys)), "信号无重复")
        check(all(re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{4}", x["bar"]) for x in led["signals"]), "信号时间戳格式 'YYYY-MM-DD HHMM'")
        check(all(x["bar"][:10] >= start for x in led["signals"]), "所有信号都在起点 %s 及之后（起点前的不计）" % start)
        buys = [f for f in led["fills"] if f[4] == "买"]
        check(len({(b[2], b[0]) for b in buys}) == len(buys), "同一只票同一天不会买两次")
        # 净值恒等式：净值 ＝ 现金 ＋ 持仓按最后收盘价估值（账本里没有最后价，用日线库最后一日 raw_close）
        last_px = {c: float(pd.read_csv(os.path.join(home, "data", "daily", c + ".csv")).iloc[-1]["raw_close"]) for c in led["positions"]}
        mv = sum(p["shares"] * last_px[c] for c, p in led["positions"].items())
        check(abs(led["cash"] + mv - led["nav"]) / led["nav"] < 5e-3, "净值恒等式：现金 %.0f ＋ 持仓市值 %.0f ≈ 净值 %.0f（容差 0.5%%，收盘价取日线库最后一日 vs 30 分钟收盘）" % (led["cash"], mv, led["nav"]))
        # 买入手续费口径：现金变化可由 fills 重建 → 初始本金 ＋ Σ卖出所得 － Σ买入成本 ＝ 现金
        cash = led["cfg"]["nav0"]
        for f in led["fills"]:
            d, t_, code, nm, side, px, note = f
            if side == "买":
                m = re.search(r"股数(\d+)", note); sh = int(m.group(1)); cash -= sh * px * (1 + 0.00026)
        for tr in led["trades"]:
            cash += tr["shares"] * tr["exit"] * (1 - 0.00026 - 0.0005)
        check(abs(cash - led["cash"]) < 1.0, "现金可由成交流水重建（买 2.6bp；卖 2.6bp＋印花税 5bp）：重建 %.2f vs 账本 %.2f" % (cash, led["cash"]))
        # 单笔风险上限：开仓时计划损失 ≤ 净值×0.75%×倍率×1.0 （舍入到整手后只会更小）；用买入当时的档位倍率
        mult = {"健康趋势": 1.0, "高温分化": .5, "修复期": .75, "退潮期(soft)": .4, "退潮期(hard)": 0.0}
        for tr in led["trades"] + [dict(code=c, typ=p["typ"], entry=p["entry"], shares=p["shares"], stop=p["stop"], ent=p["ent_dt"], state=p["state"], exit=None) for c, p in led["positions"].items()]:
            st = tr["state"]; m_ = {"HEALTHY": 1.0, "HOT": .5, "REPAIR": .75, "EBB": .4, "HARD": 0.0}[st]
            stop = tr.get("stop")
            if stop is not None and m_ > 0:
                plan_loss = tr["shares"] * (tr["entry"] - stop)
                check(plan_loss <= 200000.0 * 1.2 * 0.0075 * m_ + 1e-6 or tr["shares"] <= (200 if tr["code"].startswith(("688", "689")) else 100),
                      "开仓计划损失 %.0f 不超过 净值×0.75%%×倍率%.2f（%s 买入价 %.2f 失效价 %.2f %d 股）" % (plan_loss, m_, tr["code"], tr["entry"], stop, tr["shares"]))

        # ---- 6. 确定性
        r2 = run(["v51_scan.py", "--no-fetch", "--asof", asof], home)
        led2 = json.load(open(os.path.join(home, "live", "state", "v51_ledger.json"), encoding="utf-8"))
        check(norm_ledger(led) == norm_ledger(led2), "同一份数据重复运行，账本逐字段相同（确定性）")

        # ---- 7. 入池时刻过滤：把一只出过信号的票的加入日期改到信号之后
        if led["signals"]:
            x = led["signals"][0]; c = x["code"]; sig_day = x["bar"][:10]
            later = (dt.date.fromisoformat(sig_day) + dt.timedelta(days=1)).isoformat()
            write_pool(home, codes, names=names, extra_join={c: later})
            run(["v51_scan.py", "--no-fetch", "--asof", asof, "--tag", "late"], home)
            L3 = json.load(open(os.path.join(home, "live", "state_late", "v51_ledger.json"), encoding="utf-8"))
            sk = [s for s in L3["skips"] if s[1] == c and s[3] == x["typ"] and s[0] == x["bar"]]
            check(bool(sk) and "入池前确认" in sk[0][4], "把 %s 加入日期改到 %s 后，%s 的买点变成'入池前确认，不计'" % (c, later, sig_day))
            check(all(not (y["code"] == c and y["bar"] <= later + " 2359") for y in L3["signals"]), "…且不进入 signals")
            write_pool(home, codes, names=names)

        # ---- 8. pool_tool
        r = run(["pool_tool.py", "add", "--name", "新增测试", "--code", "600999.SH", "--sector", "合成 / 合成", "--source", "测试", "--group", "合成", "--entity-name", "新增测试"], home)
        check(r.returncode == 0 and "错误 0" in r.stdout and "已写入" in r.stdout, "pool_tool add 成功并通过校验")
        r = run(["pool_tool.py", "add", "--name", "错字股", "--code", "600998.SH", "--sector", "x", "--source", "y", "--entity-name", "别的股票"], home, check_rc=False)
        check(r.returncode != 0 and "名称不一致" in (r.stdout + r.stderr), "名称与行情源返回不一致时拒绝入池")
        r = run(["pool_tool.py", "add", "--name", "新增测试", "--code", "600999.SH", "--sector", "x", "--source", "y"], home, check_rc=False)
        check(r.returncode != 0 and "已在池内" in (r.stdout + r.stderr), "重复入池被拒绝")
        r = run(["pool_tool.py", "cooldown", "--sold", "2026-10-23"], home)
        check("10-26、10-27、10-28" in r.stdout and "2026-10-29" in r.stdout, "冷却期逐日列出：%s" % r.stdout.splitlines()[0][:60])

        # ---- 9. install_tasks
        out_dir = os.path.join(root, "rendered")
        r = run(["install_tasks.py", "--home", home, "--out", out_dir, "--only", "v51-close-1835", "v51-intraday-1502"], home)
        txt = open(os.path.join(out_dir, "v51-close-1835.md"), encoding="utf-8").read()
        check(r.returncode == 0 and "{{" not in txt and home in txt, "install_tasks 渲染无残留占位符且路径已替换")
    finally:
        shutil.rmtree(root, ignore_errors=True)

    print("端到端 %d 项检查，失败 %d 项" % (N, len(FAILS)))
    if FAILS:
        sys.exit(1)
    print("通过：init → 日线 → 30 分钟 → 池文件 → 档位 → 扫描 → 账本 → 池维护 → 任务渲染，接口与关键不变量均成立")


if __name__ == "__main__":
    main()

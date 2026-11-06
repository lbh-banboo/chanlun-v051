#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""trin_daily.py 2.0 的合成小市场测试。

造一个 300 只股票 × 30 个交易日的小市场（完全是合成数据，不碰任何真实行情），逐项注入异常，验证：
  · TRIN 数字对（与测试自己独立算的真值逐日对账，含平盘）；
  · 每一项健康检查都能抓住对应的异常，且状态分级正确（OK / WARN / STALE / PARTIAL / ERROR）；
  · 无论什么状态，核心结论 TRIN_CORE_1 每天都有（算不出来也有，写明原因）；
  · 报告里不出现 "TRIN_STATUS" 这类内部记号；
  · 日报上下两块（顶部块 + 末尾小节）幂等、可原位替换、遇到异常标记或日期对不上时不动文件；
  · 脚本源码符合 Python 3.8 语法。

用法：python3 tests/test_trin_health.py            # 测 skills/chanlun-v051/scripts/trin_daily.py
      TRIN_SCRIPT=/tmp/mutated.py python3 test_trin_health.py   # 测变异副本，应当失败
"""
import os, sys, ast, csv, json, random, shutil, subprocess, tempfile, hashlib, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.environ.get("TRIN_SCRIPT", os.path.join(HERE, "..", "skills", "chanlun-v051", "scripts", "trin_daily.py"))
FAILS, PASSES = [], 0


def check(cond, msg):
    global PASSES
    if cond:
        PASSES += 1
    else:
        FAILS.append(msg)
        print("  FAIL:", msg)


def md5(path):
    return hashlib.md5(open(path, "rb").read()).hexdigest() if os.path.exists(path) else None


# ------------------------------------------------------------------ 合成小市场
def bdays(start, n):
    d, out = datetime.date.fromisoformat(start), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += datetime.timedelta(days=1)
    return out


class Uni(object):
    HDR = "trade_date,open,high,low,close,volume,raw_close,amount,turnover,pct,adj"

    def __init__(self, n=300, ndays=30, seed=7, start="2026-09-28"):
        rnd = random.Random(seed)
        self.dates = bdays(start, ndays)
        self.codes = [("%06d.SH" % (600000 + i)) if i < 150 else ("%06d.SZ" % (1 + i - 150)) for i in range(n)]
        self.by = {}                     # code -> {date: row}
        self.sohu = set()                # 这些日期的行用搜狐格式（换手/涨跌幅只有 2 位小数）
        self.index_pct = {}
        for i, c in enumerate(self.codes):
            p = 1200 + 37 * (i % 50)     # 分
            rows = {}
            for d in self.dates:
                k = rnd.choice([-5, -4, -3, -2, -1, -1, 0, 0, 1, 1, 2, 3, 4, 5])
                new = max(600, p + 10 * k)
                vol = rnd.randint(2000, 400000)
                raw = new / 100.0
                rows[d] = dict(close=raw, raw=raw, vol=float(vol), amt=round(vol * 100 * raw * (1 + rnd.uniform(-0.01, 0.01)), 1),
                               pct=round((new / float(p) - 1) * 100, 3), turn=round(rnd.uniform(0.1, 5), 5))
                p = new
            self.by[c] = rows
        for d in self.dates:
            ps = [self.by[c][d]["pct"] for c in self.codes]
            self.index_pct[d] = round(sum(ps) / len(ps) * 0.7, 2)

    def row(self, c, d):
        return self.by[c].get(d)

    def fmt(self, d, r):
        sohu = d in self.sohu
        return ",".join([d, "%.2f" % r["close"], "%.2f" % r["close"], "%.2f" % r["close"], "%.2f" % r["close"],
                         ("%d" % r["vol"]) if sohu else ("%.2f" % r["vol"]), "%.2f" % r["raw"], "%.1f" % r["amt"],
                         ("%.2f" if sohu else "%.5f") % r["turn"], ("%.2f" if sohu else "%.3f") % r["pct"], "1.0"])

    def write(self, live):
        os.makedirs(live, exist_ok=True)
        for c in self.codes:
            with open(os.path.join(live, c + ".csv"), "w", encoding="utf-8") as f:
                f.write(self.HDR + "\n")
                for d in self.dates:
                    r = self.by[c].get(d)
                    if r is not None:
                        f.write(self.fmt(d, r) + "\n")
        with open(os.path.join(live, "000300.SH.csv"), "w", encoding="utf-8") as f:
            f.write(self.HDR + "\n")
            for d in sorted(self.index_pct):
                f.write(",".join([d, "4000", "4000", "4000", "4000", "1000", "4000", "9e11", "", "%.2f" % self.index_pct[d], "1.0"]) + "\n")

    def truth(self, d):
        up = dn = fl = 0
        au = ad = 0.0
        for c in self.codes:
            r = self.by[c].get(d)
            if r is None:
                continue
            if r["pct"] > 0:
                up += 1; au += r["amt"]
            elif r["pct"] < 0:
                dn += 1; ad += r["amt"]
            else:
                fl += 1
        n = up + dn + fl
        return dict(N=n, up=up, down=dn, flat=fl, trin=(up / float(n - up)) / (au / ad), red=100.0 * up / n)


class Env(object):
    def __init__(self, tmp, name):
        self.root = os.path.join(tmp, name)
        self.live, self.pipe, self.rep, self.st, self.logd = [os.path.join(self.root, x) for x in ("live", "pipe", "reports", "state", "logs")]
        for p in (self.pipe, self.rep, self.st, self.logd):
            os.makedirs(p, exist_ok=True)
        self.noseed = os.path.join(self.root, "无此seed.csv")

    def logs(self, c_update=None, batch=None):
        json.dump(c_update or [], open(os.path.join(self.logd, "c_update_log.json"), "w", encoding="utf-8"))
        json.dump(batch or [], open(os.path.join(self.logd, "backfill_batch_log.json"), "w", encoding="utf-8"))

    def report(self, d, name=None, first=None):
        txt = (first or "# V5.1 实时回测日报 %s（21:02 运行）" % d) + "\n\n性质：纸面，不下真单。\n\n## 数据健康\n最后一根 K 线：%s 1500\n\n## 账户\n净值 213445\n" % d
        p = os.path.join(self.rep, name or ("V51_%s.md" % d.replace("-", "")))
        open(p, "w", encoding="utf-8").write(txt)
        return p, txt

    def args(self, today, extra=()):
        return ["--live-dir", self.live, "--seed", self.noseed, "--pipeline-dir", self.pipe, "--reports-dir", self.rep,
                "--state-dir", self.st, "--log-dir", self.logd, "--today", today] + list(extra)

    def hist(self):
        with open(os.path.join(self.st, "trin_history.csv"), encoding="utf-8") as f:
            return {r["date"]: r for r in csv.DictReader(f)}


def run(args):
    p = subprocess.run([sys.executable, "-B", SCRIPT] + args, capture_output=True, text=True)
    kv = {}
    for ln in p.stdout.splitlines():
        if ln.startswith("TRIN_") and "=" in ln:
            k, v = ln.split("=", 1)
            kv.setdefault(k, v)
    return p.returncode, p.stdout, kv


def common(kv, rc, tag, want_status=None, core=True):
    check(rc == 0, "%s exit code=%s" % (tag, rc))
    check(kv.get("TRIN_PLUGIN") == "2.0", "%s 插件版本 %s" % (tag, kv.get("TRIN_PLUGIN")))
    if want_status:
        check(kv.get("TRIN_STATUS", "").split(" ")[0] == want_status, "%s 状态应为 %s：%s" % (tag, want_status, kv.get("TRIN_STATUS")))
    if core:
        c1 = kv.get("TRIN_CORE_1", "")
        check(c1.startswith("> **【阿姆氏 TRIN】**"), "%s TRIN_CORE_1 必须每天都有且以引用+粗体标题开头：%r" % (tag, c1[:50]))
    for k in ("TRIN_CORE_1", "TRIN_CORE_2", "TRIN_HEALTH"):
        check("TRIN_STATUS" not in kv.get(k, ""), "%s %s 里不能出现内部记号 TRIN_STATUS" % (tag, k))


tmp = tempfile.mkdtemp(prefix="trin_health_")
try:
    # ---------- 0 源码语法兼容 Python 3.8 ----------
    print("S0 源码语法（Python 3.8 语法树）")
    try:
        ast.parse(open(SCRIPT, encoding="utf-8").read(), feature_version=(3, 8))
        ok38 = True
    except SyntaxError as e:
        ok38 = False
        print("   ", e)
    check(ok38, "S0 trin_daily.py 不符合 Python 3.8 语法")

    # ---------- 1 基线：全新浪、日志一致 ----------
    print("S1 基线（新浪快照、日志一致）")
    u = Uni(); D = u.dates[-1]; env = Env(tmp, "s1"); u.write(env.live)
    env.logs([dict(date=D, prev=u.dates[-2], run_at=D + " 18:11:07", appended=300, gap_filled=0)])
    p_rep, txt0 = env.report(D)
    p_lat, _ = env.report(D, name="V51_latest.md")
    rc, out, kv = run(env.args(D))
    common(kv, rc, "S1", "OK")
    tr = u.truth(D)
    check(("`%s TRIN %.3f`" % (D[5:], tr["trin"])) in kv["TRIN_CORE_1"], "S1 核心结论里的 TRIN 应为真值 %.3f：%s" % (tr["trin"], kv["TRIN_CORE_1"]))
    check(("涨%d/跌%d/平%d" % (tr["up"], tr["down"], tr["flat"])) in kv["TRIN_CORE_1"], "S1 涨跌平家数：%s" % kv["TRIN_CORE_1"])
    check("数据源：新浪快照" in kv["TRIN_CORE_1"] and "留意" not in kv["TRIN_CORE_1"], "S1 数据源/无留意：%s" % kv["TRIN_CORE_1"])
    check(kv["TRIN_CORE_2"].startswith("> 同广度对照"), "S1 CORE_2：%s" % kv.get("TRIN_CORE_2"))
    check(kv["TRIN_HEALTH"].startswith("TRIN 数据健康：") and "新浪收盘快照（18:11 入库）" in kv["TRIN_HEALTH"] and "近 10 个交易日无缺漏" in kv["TRIN_HEALTH"],
          "S1 健康句：%s" % kv["TRIN_HEALTH"])
    h = env.hist()
    check(len(h) == 10, "S1 历史库应有近 10 日：%d" % len(h))
    for d in u.dates[-10:]:
        t = u.truth(d)
        check(abs(float(h[d]["trin"]) - t["trin"]) < 2e-6 and int(h[d]["up"]) == t["up"] and int(h[d]["down"]) == t["down"] and int(h[d]["flat"]) == t["flat"],
              "S1 %s 历史库与真值不符：%s vs %s" % (d, h[d]["trin"], t))
    j = json.load(open(os.path.join(env.pipe, "TRIN_%s.json" % D.replace("-", "")), encoding="utf-8"))
    check(j["plugin_version"] == "2.0" and j["status"] == "OK" and j["core_1"] == kv["TRIN_CORE_1"] and j["health"] == kv["TRIN_HEALTH"], "S1 json 字段")
    rep = open(p_rep, encoding="utf-8").read()
    lines = rep.split("\n")
    check(lines[0] == txt0.split("\n")[0] and lines[1] == "" and lines[2] == "<!-- TRIN:TOP:BEGIN -->", "S1 顶部块紧跟标题：%r" % lines[:4])
    check(lines[3] == kv["TRIN_CORE_1"] and lines[4] == ">" and lines[5] == kv["TRIN_CORE_2"] and lines[6] == "<!-- TRIN:TOP:END -->", "S1 顶部块内容")
    check(rep.count("TRIN:TOP:BEGIN") == 1 and rep.count("TRIN:TOP:END") == 1 and rep.count("<!-- TRIN:BEGIN -->") == 1 and rep.count("<!-- TRIN:END -->") == 1, "S1 四个标记各一个")
    orig_body = "\n".join(txt0.split("\n")[2:])
    check(orig_body.strip() in rep, "S1 原日报正文一字未动")
    m1 = md5(p_rep)
    rc, out, kv2 = run(env.args(D))
    check(kv2["TRIN_APPENDED"].count("unchanged") == 2 and md5(p_rep) == m1, "S1 重跑幂等：%s" % kv2["TRIN_APPENDED"])
    check("来源 历史库" in kv2["TRIN_HIST"] and "最大差 0.000000" in kv2["TRIN_CHECK"], "S1 第二次读历史库并对账一致")
    check("与历史库对账无变化" in kv2["TRIN_HEALTH"], "S1 对账无变化：%s" % kv2["TRIN_HEALTH"])

    # ---------- 2 搜狐补数日：信息提示，不降级 ----------
    print("S2 搜狐补数日（信息，不降级）")
    u = Uni(); D = u.dates[-1]; u.sohu = {D}; env = Env(tmp, "s2"); u.write(env.live)
    env.logs([], [])
    rc, out, kv = run(env.args(D))
    common(kv, rc, "S2", "OK")
    check("数据源：搜狐补数" in kv["TRIN_CORE_1"], "S2 CORE_1 标明搜狐：%s" % kv["TRIN_CORE_1"])
    check("搜狐日线补数" in kv["TRIN_HEALTH"] and "更新日志里没有该日的运行记录" in kv["TRIN_HEALTH"], "S2 健康句：%s" % kv["TRIN_HEALTH"])
    env = Env(tmp, "s2b"); u.write(env.live)
    env.logs([dict(date=D, run_at=D + " 18:42:01", appended=0, gap_filled=120)], [dict(target=D, stocks_filled=180)])
    rc, out, kv = run(env.args(D))
    common(kv, rc, "S2b", "OK")
    check("该日新浪快照入库 0 只" in kv["TRIN_HEALTH"] and "缺口补漏 120 只" in kv["TRIN_HEALTH"] and "搜狐批量补数 180 只" in kv["TRIN_HEALTH"], "S2b 日志事实：%s" % kv["TRIN_HEALTH"])

    # ---------- 3 盘中入库 ----------
    print("S3 盘中入库（WARN）")
    u = Uni(); D = u.dates[-1]; env = Env(tmp, "s3"); u.write(env.live)
    env.logs([dict(date=D, run_at=D + " 11:05:00", appended=300)])
    rc, out, kv = run(env.args(D))
    common(kv, rc, "S3", "WARN")
    check("留意：" in kv["TRIN_CORE_1"] and "11:05" in kv["TRIN_CORE_1"], "S3 CORE_1 带留意：%s" % kv["TRIN_CORE_1"])
    check(kv["TRIN_HEALTH"].startswith("TRIN 数据留意："), "S3 健康句：%s" % kv["TRIN_HEALTH"])

    # ---------- 4 窗口缺口 ----------
    print("S4 窗口内某天个股不全（WARN）")
    u = Uni(); D = u.dates[-1]; gd = u.dates[-4]
    for c in u.codes[:180]:
        del u.by[c][gd]
    env = Env(tmp, "s4"); u.write(env.live); env.logs([dict(date=D, run_at=D + " 18:11:07", appended=300)])
    rc, out, kv = run(env.args(D))
    common(kv, rc, "S4", "WARN")
    check(("%s 只有 120 只" % gd[5:]) in kv["TRIN_HEALTH"] and "有缺口" in kv["TRIN_HEALTH"], "S4 健康句点名缺口日：%s" % kv["TRIN_HEALTH"])
    check("留意：" in kv["TRIN_CORE_1"], "S4 CORE_1 带留意")
    check(gd not in env.hist(), "S4 缺口日不写进历史库")

    # ---------- 5 最新一天只更新了一半：PARTIAL ----------
    print("S5 最新一天个股只有 40%（PARTIAL）")
    u = Uni(); D = u.dates[-1]; env = Env(tmp, "s5"); u.write(env.live); env.logs([dict(date=D, run_at=D + " 18:11:07", appended=300)])
    rc, out, kv = run(env.args(D)); common(kv, rc, "S5a", "OK")
    snap = {p: md5(p) for p in [os.path.join(env.st, "trin_history.csv"), os.path.join(env.pipe, "TRIN_%s.json" % D.replace("-", ""))]}
    for c in u.codes[:180]:
        del u.by[c][D]
    shutil.rmtree(env.live); u.write(env.live)
    rc, out, kv = run(env.args(D))
    common(kv, rc, "S5b", "PARTIAL")
    check("未算出" in kv["TRIN_CORE_1"] and "只有 120 只" in kv["TRIN_CORE_1"] and "最近一次可用：" in kv["TRIN_CORE_1"], "S5b 说明原因并给最近可用值：%s" % kv["TRIN_CORE_1"])
    check("TRIN_CORE_2" not in kv and "TRIN_APPENDED" not in kv, "S5b 不输出第二行和写入结果")
    check(all(md5(p) == m for p, m in snap.items()), "S5b 什么都没改写")

    # ---------- 6 方向 / 复制 / 单位 / 成交额 ----------
    print("S6 涨跌方向与收盘价不一致（WARN）")
    u = Uni(); D = u.dates[-1]
    for c in u.codes[:9]:
        r = u.by[c][D]
        if r["pct"] != 0:
            r["pct"] = -r["pct"]
    env = Env(tmp, "s6"); u.write(env.live); env.logs([dict(date=D, run_at=D + " 18:11:07", appended=300)])
    rc, out, kv = run(env.args(D)); common(kv, rc, "S6", "WARN")
    check("涨跌幅与收盘价变动方向不一致" in kv["TRIN_HEALTH"], "S6 健康句：%s" % kv["TRIN_HEALTH"])

    print("S7 疑似把昨天复制成今天（WARN）")
    u = Uni(); D, P = u.dates[-1], u.dates[-2]
    for c in u.codes[:9]:
        u.by[c][D] = dict(u.by[c][P], pct=0.0)
    env = Env(tmp, "s7"); u.write(env.live); env.logs([dict(date=D, run_at=D + " 18:11:07", appended=300)])
    rc, out, kv = run(env.args(D)); common(kv, rc, "S7", "WARN")
    check("完全相同" in kv["TRIN_HEALTH"] and "疑似没更新" in kv["TRIN_HEALTH"], "S7 健康句：%s" % kv["TRIN_HEALTH"])

    print("S8 成交量单位错（WARN）")
    u = Uni(); D = u.dates[-1]
    for c in u.codes:
        u.by[c][D]["vol"] *= 100
    env = Env(tmp, "s8"); u.write(env.live); env.logs([dict(date=D, run_at=D + " 18:11:07", appended=300)])
    rc, out, kv = run(env.args(D)); common(kv, rc, "S8", "WARN")
    check("单位可能变了" in kv["TRIN_HEALTH"], "S8 健康句：%s" % kv["TRIN_HEALTH"])

    print("S9 全市场成交额量级异常（WARN，单位不变）")
    u = Uni(); D = u.dates[-1]
    for c in u.codes:
        u.by[c][D]["vol"] *= 20; u.by[c][D]["amt"] = round(u.by[c][D]["amt"] * 20, 1)
    env = Env(tmp, "s9"); u.write(env.live); env.logs([dict(date=D, run_at=D + " 18:11:07", appended=300)])
    rc, out, kv = run(env.args(D)); common(kv, rc, "S9", "WARN")
    check("量级异常" in kv["TRIN_HEALTH"] and "单位可能变了" not in kv["TRIN_HEALTH"], "S9 健康句只报成交额不报单位：%s" % kv["TRIN_HEALTH"])

    # ---------- 10 与历史库对账 ----------
    print("S10 与历史库对账（大改动 WARN；小回补只是信息）")
    u = Uni(); D = u.dates[-1]; env = Env(tmp, "s10"); u.write(env.live); env.logs([dict(date=D, run_at=D + " 18:11:07", appended=300)])
    rc, out, kv = run(env.args(D)); common(kv, rc, "S10a", "OK")
    hp = os.path.join(env.st, "trin_history.csv")
    rows = list(csv.reader(open(hp, encoding="utf-8")))
    hdr = rows[0]; it = hdr.index("trin"); iN = hdr.index("N"); idn = hdr.index("down")
    big, small = u.dates[-3], u.dates[-5]
    for r in rows[1:]:
        if r[0] == big:
            r[it] = "%.6f" % (float(r[it]) * 1.05)
        if r[0] == small:
            r[iN] = str(int(r[iN]) - 1); r[idn] = str(int(r[idn]) - 1); r[it] = "%.6f" % (float(r[it]) + 1e-4)
    csv.writer(open(hp, "w", encoding="utf-8", newline="")).writerows(rows)
    rc, out, kv = run(env.args(D))
    common(kv, rc, "S10b", "WARN")
    check(("%s 的数据与历史库差异较大" % big[5:]) in kv["TRIN_HEALTH"], "S10b 点名大改动日：%s" % kv["TRIN_HEALTH"])
    check(("%s 回补/修正" % small[5:]) in kv["TRIN_HEALTH"], "S10b 小回补只作信息：%s" % kv["TRIN_HEALTH"])
    rc, out, kv = run(env.args(D))
    common(kv, rc, "S10c", "OK")
    check("与历史库对账无变化" in kv["TRIN_HEALTH"], "S10c 修正后对账无变化（历史库被新算覆盖）：%s" % kv["TRIN_HEALTH"])

    # ---------- 11 指数与广度方向矛盾 ----------
    print("S11 沪深300 −2% 而 90% 个股上涨（WARN）")
    u = Uni(); D, P = u.dates[-1], u.dates[-2]
    for i, c in enumerate(u.codes):
        r = u.by[c][D]
        prevc = u.by[c][P]["close"]
        if i % 10 != 0:
            r["close"] = r["raw"] = round(prevc * 1.02, 2); r["pct"] = round((r["close"] / prevc - 1) * 100, 3)
    u.index_pct[D] = -2.0
    env = Env(tmp, "s11"); u.write(env.live); env.logs([dict(date=D, run_at=D + " 18:11:07", appended=300)])
    rc, out, kv = run(env.args(D)); common(kv, rc, "S11", "WARN")
    check("方向矛盾" in kv["TRIN_HEALTH"], "S11 健康句：%s" % kv["TRIN_HEALTH"])

    # ---------- 12 今天的行情还没入库：STALE ----------
    print("S12 STALE：今天（D+1）行情没入库")
    u = Uni(); D = u.dates[-1]; nxt = bdays(D, 2)[-1]
    env = Env(tmp, "s12"); u.write(env.live); env.logs([dict(date=D, run_at=D + " 18:11:07", appended=300)])
    p_rep, txt0 = env.report(D)
    rc, out, kv = run(env.args(nxt)); common(kv, rc, "S12", "STALE")
    check("（非今日数据）" in kv["TRIN_CORE_1"] and "注意：" in kv["TRIN_CORE_1"] and ("`%s TRIN" % D[5:]) in kv["TRIN_CORE_1"], "S12 CORE_1 标注非今日并仍给数据：%s" % kv["TRIN_CORE_1"])
    check("不含该日" in kv["TRIN_CORE_2"] and "今日" not in kv["TRIN_CORE_2"], "S12 第二行用“该日”：%s" % kv["TRIN_CORE_2"])
    check(kv["TRIN_APPENDED"].startswith("无") and open(p_rep, encoding="utf-8").read() == txt0, "S12 不追加日报")
    check(os.path.exists(os.path.join(env.pipe, "TRIN_%s.json" % D.replace("-", ""))), "S12 仍存档")
    # 12b 指数已经到了今天，个股还停在昨天
    u.index_pct[nxt] = 0.3
    shutil.rmtree(env.live); u.write(env.live)
    rc, out, kv = run(env.args(nxt)); common(kv, rc, "S12b", "STALE")
    check("指数已更新到 %s，个股还停在 %s" % (nxt, D) in kv["TRIN_STATUS"] and "指数已更新到" in kv["TRIN_CORE_1"], "S12b 点明指数领先个股：%s" % kv["TRIN_STATUS"])

    # ---------- 13 报告写入的各种边界 ----------
    print("S13 日报写入：替换 / 日期对不上 / 标记不成对 / 文件不存在")
    u = Uni(); D = u.dates[-1]; env = Env(tmp, "s13"); u.write(env.live); env.logs([dict(date=D, run_at=D + " 18:11:07", appended=300)])
    p1, t1 = env.report(D)
    p2, t2 = env.report(D, name="V51_latest.md", first="# V5.1 实时回测日报 %s（21:02 运行）" % u.dates[-2])
    rc, out, kv = run(env.args(D)); common(kv, rc, "S13a", "OK")
    check("V51_%s.md:appended" % D.replace("-", "") in kv["TRIN_APPENDED"] and "V51_latest.md:skip（首行日期不是 %s" % D in kv["TRIN_APPENDED"], "S13a：%s" % kv["TRIN_APPENDED"])
    check(open(p2, encoding="utf-8").read() == t2, "S13a latest 首行日期不符时一字不动")
    # 让 TRIN 变化后重跑，应原位替换而不是重复追加
    for c in u.codes[:40]:
        u.by[c][D]["pct"] = abs(u.by[c][D]["pct"]) + 0.5
        u.by[c][D]["close"] = u.by[c][D]["raw"] = round(u.by[c][u.dates[-2]]["close"] * (1 + u.by[c][D]["pct"] / 100), 2)
    shutil.rmtree(env.live); u.write(env.live)
    rc, out, kv = run(env.args(D)); common(kv, rc, "S13b", "WARN")
    check("与历史库差异较大" in kv["TRIN_HEALTH"], "S13b 当天数据被改后应报与历史库差异：%s" % kv["TRIN_HEALTH"])
    rep = open(p1, encoding="utf-8").read()
    check("V51_%s.md:replaced" % D.replace("-", "") in kv["TRIN_APPENDED"] and rep.count("TRIN:TOP:BEGIN") == 1 and rep.count("<!-- TRIN:BEGIN -->") == 1, "S13b 原位替换：%s" % kv["TRIN_APPENDED"])
    check(("`%s TRIN %.3f`" % (D[5:], u.truth(D)["trin"])) in rep, "S13b 日报里是新数")
    # 标记不成对
    broken = rep.replace("<!-- TRIN:TOP:END -->", "")
    open(p1, "w", encoding="utf-8").write(broken)
    rc, out, kv = run(env.args(D))
    check("标记不成对" in kv["TRIN_APPENDED"] and open(p1, encoding="utf-8").read() == broken, "S13c 标记不成对不动：%s" % kv["TRIN_APPENDED"])
    os.remove(p1)
    rc, out, kv = run(env.args(D))
    check("文件不存在" in kv["TRIN_APPENDED"], "S13d 文件不存在：%s" % kv["TRIN_APPENDED"])

    # ---------- 14 ERROR ----------
    print("S14 ERROR：目录不存在 / 历史库损坏")
    env = Env(tmp, "s14")
    rc, out, kv = run(["--live-dir", os.path.join(tmp, "不存在"), "--seed", env.noseed, "--pipeline-dir", env.pipe, "--reports-dir", env.rep,
                       "--state-dir", env.st, "--log-dir", env.logd, "--today", "2026-10-30"])
    common(kv, rc, "S14a", "ERROR")
    check("未算出" in kv["TRIN_CORE_1"] and "日线目录不存在" in kv["TRIN_CORE_1"], "S14a CORE_1：%s" % kv["TRIN_CORE_1"])
    u = Uni(); D = u.dates[-1]; env = Env(tmp, "s14b"); u.write(env.live)
    open(os.path.join(env.st, "trin_history.csv"), "w", encoding="utf-8").write("date,N,up,down,flat,amt_up_yi,amt_dn_yi,amt_flat_yi,red_pct,up_amt_share,trin,hs300_pct\n2026-10-01,abc,1,1,1,1,1,1,1,1,1,1\n")
    rc, out, kv = run(env.args(D))
    common(kv, rc, "S14b", "ERROR")
    check("未算出" in kv["TRIN_CORE_1"] and "插件异常" in kv["TRIN_CORE_1"], "S14b 异常也要有核心结论行：%s" % kv["TRIN_CORE_1"])

    # ---------- 15 日线库没有任何新浪/搜狐标记（换手率为空）----------
    print("S15 换手率列为空 → 来源未知，不崩")
    u = Uni(); D = u.dates[-1]; env = Env(tmp, "s15"); u.write(env.live)
    for c in u.codes:
        p = os.path.join(env.live, c + ".csv")
        s = open(p, encoding="utf-8").read().split("\n")
        out_lines = [s[0]]
        for ln in s[1:]:
            if ln:
                f = ln.split(","); f[8] = ""; ln = ",".join(f)
            out_lines.append(ln)
        open(p, "w", encoding="utf-8").write("\n".join(out_lines))
    rc, out, kv = run(env.args(D)); common(kv, rc, "S15", "OK")
    check("数据源：未知" in kv["TRIN_CORE_1"], "S15 来源未知：%s" % kv["TRIN_CORE_1"])
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n通过 %d 项断言，失败 %d 项" % (PASSES, len(FAILS)))
for m in FAILS[:40]:
    print(" -", m)
sys.exit(1 if FAILS else 0)

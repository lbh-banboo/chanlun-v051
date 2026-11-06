#!/usr/bin/env python3
"""重点池（池B）名单生成器：从质地榜快照取每个行业 rank<=N 的票，写 pools/focus.md。

输入  --snapshot  质地榜快照 JSON：列表，每项＝一个行业 {"ind": 行业名, "top": [ {rank, code, name, sw, score, tier, fill_why, roe_avg, pe_kf, red, yel, price}, ... ]}
                  （score_quality.py 的 result2.json 经 slim 后即是这个形状；字段见 quality/SCHEMA.md）
输出  pools/focus.md   六列名单表＋主题簇表（默认按申万一级、可叠加跨行业簇）＋入池核查＋待裁定
      pools/focus.json 机器可读明细（流动性、1.5a、重叠…）
用法  python3 build_focus_pool.py --snapshot slim.json --join YYYY-MM-DD [--top 5] [--nav 200000] [--pool-a watchlist.md]
                                  [--cross-cluster "金融=银行,非银金融" ...] [--cross-members "AI 算力链=示例算力A,示例光模块B"]

标注口径
  流动性  日线库最近 20 个交易日成交额均值；< 1 亿元＝V5.1 闸门 1 准入暂拦（成交额放大后自动放行，不用动名单）
  1.5a    最新收盘价 > 价格天花板（主板 净值÷800、创业板 净值×10%÷100、科创板 净值×10%÷200）＝入池仅跟踪
  重叠    与第一个池（--pool-a）代码相同＝两个账本各记一笔
  补位    质地榜"达标"不足 N 只的行业用"过门槛、无红旗"的票补位并标灰（tier=fill）
入池后不随质地榜修订自动增删；要换名单须使用者点头再重新生成，并遵守"新入池只管新信号、老持仓走完"。
注意：质地榜用最新财报选票，拿它挑出的票去回测 2020 年以来的表现带前视偏差——重点池只适合做实时的样本外检验。
"""
import os
import re
import sys
import json
import argparse
import unicodedata
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(HERE, ".."))
import _paths as P
import _common as C

ap = argparse.ArgumentParser(description="重点池名单生成器")
ap.add_argument("--snapshot", required=True)
ap.add_argument("--join", default=None, help="入池日期 YYYY-MM-DD（该日收盘后入池，次一交易日起算）；默认今天")
ap.add_argument("--top", type=int, default=5, help="每个行业取前几名")
ap.add_argument("--nav", type=float, default=None, help="虚拟净值（算 1.5a 天花板用）；默认读 config 的 nav0")
ap.add_argument("--pool-a", default="watchlist.md", help="第一个池（用来标重叠）")
ap.add_argument("--out", default=os.path.join(P.POOLS, "focus.md"))
ap.add_argument("--expect", type=int, default=0, help="期望入池只数（0＝不检查）")
ap.add_argument("--cross-cluster", nargs="*", default=[], help="跨行业叠加簇（按申万一级合并），形如 金融=银行,非银金融")
ap.add_argument("--cross-members", nargs="*", default=[], help="跨行业叠加簇（按成员名称），形如 'AI 算力链=示例算力A,示例光模块B'")
A = ap.parse_args()
A.join = A.join or C.today_str()
if A.nav is None:
    cfgname = "v51_config_focus.json" if os.path.exists(os.path.join(P.CONFIG, "v51_config_focus.json")) else "v51_config.json"
    A.nav = P.load_config(cfgname)["nav0"]
CEIL = C.ceilings(A.nav)

D = json.load(open(A.snapshot, encoding="utf-8"))
try:
    POOL_A = C.read_pool(A.pool_a)[0]
except Exception:                                                  # 第一个池不存在也能生成
    POOL_A = {}


def suffix(c): return c + (".SH" if c.startswith("6") else ".SZ")
def nm(s): return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s))


rows = []
for d in D:
    for s in d["top"]:
        if s["rank"] > A.top:
            continue
        c = suffix(s["code"]); p = os.path.join(P.DAILY, c + ".csv")
        if not os.path.exists(p):
            raise SystemExit("日线库里没有 %s（%s）。先把全市场日线补齐（daily_update.py / backfill_sohu_batch.py），名单里的每只票都要有日线才能核流动性与 1.5a" % (c, s["name"]))
        x = pd.read_csv(p)
        sw = s["sw"].split("-")
        sec2 = " / ".join(sw[1:3]) if len(sw) >= 3 else s["sw"]
        b = C.board_of(c)
        rows.append(dict(ind=d["ind"], rank=s["rank"], code=c, name=nm(s["name"]), sec=sec2, score=s["score"], tier=s.get("tier", "elig"),
                         fill_why=s.get("fill_why", ""), roe_avg=s.get("roe_avg"), pe_kf=s.get("pe_kf"), yel=s.get("yel", []), red=s.get("red", []),
                         last=float(x["raw_close"].iloc[-1]), last_d=str(x["trade_date"].iloc[-1]), amt20=float(x["amount"].iloc[-20:].mean()),
                         board=b, cap=CEIL[b], in_A=c in POOL_A))
names = [r["name"] for r in rows]
assert len(set(r["code"] for r in rows)) == len(rows), "代码重复"
dups = {n for n in names if names.count(n) > 1}
assert not dups, "名称重复会污染簇映射：%s" % dups
assert not A.expect or len(rows) == A.expect, "入池只数 %d，期望 %d" % (len(rows), A.expect)
assert all(r["board"] in ("main", "gem") for r in rows), "出现科创板：质地榜入榜前应已剔除；科创板一手 200 股，另需单独评估再入池"
last_ds = sorted({r["last_d"] for r in rows})
lot_ratio = lambda r: 100 * r["last"] * 100 / A.nav


def status(r):
    f = []
    if r["tier"] == "fill":
        f.append("★ **补位**（未达标，质地榜标灰）：%s" % r["fill_why"])
    if r["amt20"] < 1e8:
        f.append("★ **准入暂拦**：20 日均额 %.2f 亿＜1 亿（V5.1 闸门 1，成交额放大后自动放行）" % (r["amt20"] / 1e8))
    elif r["amt20"] < 1.25e8:
        f.append("流动性贴线：20 日均额 %.2f 亿（距 1 亿门槛不足 25%%）" % (r["amt20"] / 1e8))
    if r["last"] > r["cap"]:
        f.append("★★ **1.5a 不通过**：收 %.2f＞天花板 %.2f，一手 %s 元≈净值 %.1f%%，入池仅跟踪" % (r["last"], r["cap"], format(round(r["last"] * 100), ","), lot_ratio(r)))
    elif r["last"] > 0.85 * r["cap"]:
        f.append("1.5a 通过但贴线：收 %.2f，天花板 %.2f，余量 %.1f%%" % (r["last"], r["cap"], 100 * (1 - r["last"] / r["cap"])))
    if r["in_A"]:
        f.append("★ **已在第一个池**：两池账本独立，同一买点各记一笔")
    if r["yel"]:
        f.append("质地榜提示：" + "；".join(r["yel"]))
    return "关注（重点池）" + ("｜" + "｜".join(f) if f else "")


JOIN = "%s（收盘后入池）" % A.join
n_fill = sum(r["tier"] == "fill" for r in rows)
L = ["# 重点池（已确认）：申万一级行业质地榜前 %d 名" % A.top, ""]
L += ["- 确认日期：**%s**（按质地榜快照 %s 生成；%s 收盘后入池，共 **%d 只**）" % (A.join, os.path.basename(A.snapshot), A.join, len(rows))]
L += ["- 性质：第二个池，简称**重点池 / 池B**，与 `watchlist.md`（池A）并列、**互不干扰**。V5.1 实时回测账本独立：`live/state_focus/`，报告在 `live/reports_focus/`；"
      "起算日＝入池日的下一个交易日，此前已确认的买点一律不计（V5.1 第 3 章：入池必须发生在信号出现之前）。"]
L += ["- 选票口径：质地榜「行业相对前 %d 名」——申万一级各行业取前 %d（达标票优先，不足用「过门槛、无红旗」的票补位并标灰）。「降级观察」里有红旗的票不进。"
      "**入池后不随质地榜修订自动增删**，要换名单须使用者点头、重新生成本文件。" % (A.top, A.top)]
L += ["- 与 V5.1 的关系：买卖规则、八道入场闸门、四档市场状态、熔断、30% 主题簇、3 日冷却期全部沿用；**唯一差别是股票池来源**。"
      "V5.1 第 3 章写的是「唯一来源是关注池」，本池是平行池，手册写法待裁定见文末。"]
L += ["- 质地榜是**系统外的静态基本面初筛**：不看股价位置和趋势，不代表投资价值高，也不是买点。入池 ≠ 可买，能不能买只由 V5.1 的结构买点和八道闸门决定。"]
L += ["- 下游解析契约（改本文件前必读，`pool_tool.py check` 可校验）：①名称列**不得加粗**；②名单表在 `## 名单` 节内、**六列**（名称/代码/板块/加入日期/入池来源/状态），行业分组行不带代码；"
      "③「加入日期」写 `YYYY-MM-DD` 或 `YYYY-MM-DD HH:MM`，**该列不得出现「前」字**；④主题簇表在 `## 主题簇` 节，表头含「主题簇」「成员」，成员用顿号分隔、名称与名单表完全一致，簇表之后不得再有表格；"
      "⑤新票要补 `data/m30_recent`（m30_data.py recent）。", ""]
L += ["## 板块列的口径", "", "- 板块列为**申万行业分类（2021）二级 / 三级**，取自质地榜快照；一级即分组标题。主题簇默认以**申万一级**为簇，可另加跨行业叠加（见主题簇表）。", ""]
L += ["## 名单（**%d 只**，按申万一级行业分组）" % len(rows), "", "| 股票 | 代码 | 板块（申万二级 / 三级） | 加入日期 | 入池来源 | 状态 |", "|---|---|---|---|---|---|"]
order = []
for r in rows:
    if r["ind"] not in order:
        order.append(r["ind"])
for ind in order:
    rs = [r for r in rows if r["ind"] == ind]
    L.append("| **%s（%d 只）** | | | | | |" % (ind, len(rs)))
    for r in rs:
        src = "质地榜：%s第%d｜%.1f 分｜%s｜ROE 均值 %s｜扣非 PE %s" % (ind, r["rank"], r["score"], "达标" if r["tier"] == "elig" else "补位",
                                                              ("%.1f%%" % r["roe_avg"]) if r["roe_avg"] is not None else "—", ("%.1f" % r["pe_kf"]) if r["pe_kf"] else "—")
        L.append("| %s | %s | %s | %s | %s | %s |" % (r["name"], r["code"], r["sec"], JOIN, src, status(r)))
L += ["", "- **参与扫描 ≠ 可买**：处于准入暂拦（20 日均额＜1 亿）、1.5a 不通过、冷却期的票，即使出现缠论买点也不下纸面单，信号照记并标注被哪条闸门拦下。", ""]

L += ["## 主题簇（执行 30% 上限时以此表为准）", "", "| 主题簇 | 只数 | 成员 | 为什么归在一起 |", "|---|---:|---|---|"]
for ind in order:
    rs = [r for r in rows if r["ind"] == ind]
    L.append("| %s（申万一级） | %d | %s | 申万一级同行业，按「同一行业合计 30%%」计量（实现假设，待使用者裁定） |" % (ind, len(rs), "、".join(r["name"] for r in rs)))
for spec in A.cross_cluster:                                       # 按申万一级合并，如 金融=银行,非银金融
    nm_, inds = spec.split("=", 1); inds = [x.strip() for x in inds.split(",")]
    mem = [r["name"] for r in rows if r["ind"] in inds]
    if mem:
        L.append("| %s（跨行业叠加） | %d | %s | %s 同受共同因素驱动，各自受 30%% 约束之外，再合并按 30%% 计量（实现假设，待使用者裁定） |" % (nm_, len(mem), "、".join(mem), "、".join(inds)))
for spec in A.cross_members:                                       # 按成员名称合并，如 AI 算力链=示例算力A,示例光模块B
    nm_, mm = spec.split("=", 1); mm = [x.strip() for x in mm.split(",")]
    mem = [m for m in mm if m in names]
    if mem:
        L.append("| %s（跨行业叠加） | %d | %s | 申万拆在不同一级行业但同属一条产业链（实现假设，待使用者裁定） |" % (nm_, len(mem), "、".join(mem)))
L += [""]

low = [r for r in rows if r["amt20"] < 1e8]; over = [r for r in rows if r["last"] > r["cap"]]; ovl = [r for r in rows if r["in_A"]]
L += ["## 入池时核查（数据截至 %s 收盘）" % last_ds[-1], ""]
L += ["- **20 日均成交额＜1 亿（准入暂拦）%d 只**：" % len(low) + "、".join("%s %.2f亿" % (r["name"], r["amt20"] / 1e8) for r in sorted(low, key=lambda z: z["amt20"]))]
L += ["- **1.5a 一手买不起 %d 只**（天花板：主板 %.2f、创业板 %.2f）：" % (len(over), CEIL["main"], CEIL["gem"]) + "、".join("%s 收%.2f" % (r["name"], r["last"]) for r in over)]
L += ["- **与池A 重叠 %d 只**：" % len(ovl) + "、".join(r["name"] for r in ovl) + "。这些票在两个账本里各记一笔；汇总看收益时不要把两池仓位相加理解成同一个账户。"]
L += ["- **补位（未达标）%d 只**：" % n_fill + "、".join(r["name"] for r in rows if r["tier"] == "fill")]
L += ["- 板块构成：主板 %d 只、创业板 %d 只，**无科创板、北交所、ST、上市不满一年**（质地榜入榜前已剔除）。" % (sum(r["board"] == "main" for r in rows), sum(r["board"] == "gem" for r in rows)), ""]
L += ["## 待使用者裁定", "",
      "1. **V5.1 第 3 章与 10.1 第 5 条的写法**：现文是「唯一来源是关注池」「全部模拟单都来自关注池」。重点池是平行池，建议在手册里加一条附注（池B 与池A 同规则、账本独立、机制验收各算各的），或写成具名例外。手册未改。",
      "2. **主题簇口径**：现按申万一级为簇，另加使用者指定的跨行业叠加；是否认可，或要再加别的跨行业簇。",
      "3. **补位票**是否保留（它们没达标，保留是因为选了「前 %d 名」）。" % A.top,
      "4. **两池重叠的票**目前各记一笔；若要「同一只票两池合计不超过某上限」，需另立规则。",
      "5. 质地榜后续修订（财报季更新）时，池子是否跟着换；换的话按「新入池只管新信号、老持仓走完」处理。", ""]
os.makedirs(os.path.dirname(os.path.abspath(A.out)), exist_ok=True)
open(A.out, "w", encoding="utf-8").write("\n".join(L))
json.dump(rows, open(os.path.splitext(A.out)[0] + ".json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("写出", A.out, len(rows), "只；准入暂拦", len(low), "；1.5a 不通过", len(over), "；与池A重叠", len(ovl), "；补位", n_fill, "；日线最后日期", last_ds)

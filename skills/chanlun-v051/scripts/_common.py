#!/usr/bin/env python3
"""共享常量与池文件解析（v51_scan / pool_watch / pool_tool 共用）。

池文件格式契约（references/05-pool-file-contract.md）：
  ## 名单…        六列表：名称｜代码｜板块｜加入日期｜入池来源｜状态；名称列不得加粗；行业分组行不带代码
  ## 主题簇…      表头含「主题簇」「成员」；成员用顿号分隔，名称须与名单表完全一致；其后不得再出现其它表格
  ## 簇叠加…      （可选）列表项：「股票「名称」→ 簇「簇名」」「簇「甲」→ 簇「乙」」；保守口径下"也计入"目标簇的 30% 上限
"""
import os
import re
import json
import datetime as dt
import _paths as P

# ---------------- V5.1 账户层参数（手册第 8、11 章，继承 V2.6 的 1.1/1.2/1.4/3.2） ----------------
RISK, EXPO_MAX, CLUSTER, MAXPOS = 0.0075, 0.70, 0.30, 20
CAP = {"HEALTHY": .70, "HOT": .50, "REPAIR": .40, "EBB": .20, "HARD": 0.0}
MULT = {"HEALTHY": 1.0, "HOT": .50, "REPAIR": .75, "EBB": .40, "HARD": 0.0}
CN = {"HEALTHY": "健康趋势", "HOT": "高温分化", "REPAIR": "修复期", "EBB": "退潮期(soft)", "HARD": "退潮期(hard)"}
SLOTS = ["1000", "1030", "1100", "1130", "1330", "1400", "1430", "1500"]
SLIP, BUYF, STAMP = 0.001, 0.00026, 0.0005          # 滑点 10bp；买入费 2.6bp；卖出费 2.6bp＋印花税 5bp


def board_of(code):
    return "star" if code.startswith(("688", "689")) else ("gem" if code.startswith(("300", "301")) else "main")


def limit_pct(code):
    """涨跌幅限制：创业板（300/301）、科创板（688/689）20%，其余 10%。"""
    return 0.20 if code.startswith(("300", "301", "688", "689")) else 0.10


def ceilings(nav):
    """1.5a 资金可交易性闸门：可买的最高股价（一手/科创板 200 股的市值不得超过单票上限与风险约束）。
    主板＝净值÷800；创业板＝净值×10%÷100；科创板＝净值×10%÷200。"""
    return {"main": nav / 800.0, "gem": nav * 0.10 / 100, "star": nav * 0.10 / 200}


# ---------------- 档位（前一交易日收盘） ----------------
def regime_files(pipe=None):
    """pipeline 目录里的档位文件：regime_YYYYMMDD.json（本工具链）或 买点C_YYYYMMDD.json（旧命名，兼容）。"""
    pipe = pipe or P.PIPE
    out = []
    if os.path.isdir(pipe):
        for f in os.listdir(pipe):
            m = re.fullmatch(r"(?:regime|买点C)_(\d{8})\.json", f)
            if m:
                out.append((m.group(1), f))
    return sorted(out)


def regime_key(state_text):
    st = state_text or ""
    if "hard" in st:
        return "HARD"
    if "退潮" in st:
        return "EBB"
    if "健康" in st:
        return "HEALTHY"
    if "高温" in st:
        return "HOT"
    return "REPAIR"


def regime_before(date, pipe=None):
    """date 之前最近一个交易日的收盘档位：取日期 < date 的最新一份文件的 state_bt。
    没有任何文件时返回 ("REPAIR", None)（保守的中间档，且会在日报里标出来源为"无"）。"""
    pipe = pipe or P.PIPE
    ds = date.replace("-", "")
    cand = [(d, f) for d, f in regime_files(pipe) if d < ds]
    for d, f in reversed(cand):
        j = json.load(open(os.path.join(pipe, f), encoding="utf-8"))
        dg = j.get("diag") or {}
        if dg.get("n_valid") is not None and dg["n_valid"] < 1000:      # 覆盖异常的坏文件：跳过，回退到更早一份（旧格式文件无 diag，照常使用）
            continue
        return regime_key(j.get("state_bt", "")), d
    return "REPAIR", None


# ---------------- 池文件解析 ----------------
def _section(txt, heading):
    parts = re.split(r"\n## " + heading, txt)
    if len(parts) < 2:
        return ""
    return parts[1].split("\n## ")[0]


def read_pool(pool_file, overlay=False):
    """返回 (names{代码: 名称}, clusters{代码: [簇名...]}（表内顺序、去重）)。
    overlay=True 时再套用「## 簇叠加」声明（v51_scan 的主题簇 30% 上限用）。"""
    txt = open(P.pool_path(pool_file), encoding="utf-8").read()
    sec = _section(txt, "名单")
    names = {}
    for ln in sec.splitlines():
        m = re.match(r"\|\s*([^|*]+?)\s*\|\s*(\d{6}\.(?:SH|SZ))\s*\|", ln)
        if m:
            names[m.group(2)] = m.group(1)
    clu = {}
    n2c = {v: k for k, v in names.items()}
    for ln in _section(txt, "主题簇").splitlines():
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(cells) >= 4 and cells[0] not in ("主题簇", "---", ":---", "---:") and not cells[0].startswith("-"):
            cname = cells[0].replace("*", "")
            for nm in re.split(r"[、，,]", cells[2].replace("*", "")):
                c = n2c.get(nm.strip())
                if c and cname not in clu.setdefault(c, []):
                    clu[c].append(cname)
    if overlay:
        for kind, src, dst in read_overlay(txt):
            for c in list(names):
                hit = (names[c] == src) if kind == "股票" else any(s.startswith(src) for s in clu.get(c, []))
                if hit and dst not in clu.setdefault(c, []):
                    clu[c].append(dst)
    return names, clu


def read_overlay(txt):
    """「## 簇叠加」列表项 → [(类型, 源名称, 目标簇)]。类型＝股票/簇；簇按"簇名以源名称开头"匹配。"""
    out = []
    for ln in _section(txt, "簇叠加").splitlines():
        s = ln.strip()
        if not s.startswith(("-", "*")) or "→" not in s:
            continue
        q = re.findall(r"「([^」]+)」", s)
        if len(q) >= 2 and (s.lstrip("-* ").startswith(("股票", "簇"))):
            out.append((s.lstrip("-* ")[:2], q[0].strip(), q[1].strip()))
    return out


def read_pool_from(pool_file):
    """名单表「加入日期」→ 入池生效时刻 {代码: "YYYY-MM-DD HHMM"}（与 K 线标记 "日期 收盘时刻" 直接比大小）。
    V5.1 第 3 章：入池必须发生在信号出现之前。写了时刻的（"YYYY-MM-DD HH:MM"）：该时刻及以前收盘的 K 线上确认的买点不计；
    只写日期的：按当天收盘后入池，当天及以前的不计；日期里带「前」字的（"YYYY-MM-DD 前"）不设限。"""
    txt = open(P.pool_path(pool_file), encoding="utf-8").read()
    out = {}
    for ln in _section(txt, "名单").splitlines():
        cells = [x.strip() for x in ln.split("|")]
        if len(cells) < 6 or not re.fullmatch(r"\d{6}\.(?:SH|SZ)", cells[2]) or "前" in cells[4]:
            continue
        m = re.search(r"(\d{4}-\d{2}-\d{2})(?:\s*(\d{1,2}):(\d{2}))?", cells[4])
        if m:
            out[cells[2]] = m.group(1) + " " + (("%02d%s" % (int(m.group(2)), m.group(3))) if m.group(2) else "2359")
    return out


def read_cooling(pool_file, today):
    """名单行状态列里的冷却期声明 → {代码: 说明}。识别两种写法（references/05 第 4 条）：
      「最早 YYYY-MM-DD 可买回」（或「最早可买 YYYY-MM-DD」）且日期 > today → 冷却中；
      「冷却期无法核定 / 视同仍在冷却期」→ 按最保守口径视同冷却中。"""
    txt = open(P.pool_path(pool_file), encoding="utf-8").read()
    out = {}
    for ln in _section(txt, "名单").splitlines():
        cells = [x.strip() for x in ln.split("|")]
        if len(cells) < 7 or not re.fullmatch(r"\d{6}\.(?:SH|SZ)", cells[2]):
            continue
        row = "|".join(cells[1:])
        m = re.search(r"最早(?:可买)?\s*(\d{4}-\d{2}-\d{2})(?:\s*可买回)?", row)
        if m and m.group(1) > today:
            out[cells[2]] = "%s 前冷却中" % m.group(1)
        elif re.search(r"视同仍在冷却期", row):
            out[cells[2]] = "冷却期无法核定，视同仍在冷却期"
    return out


def today_str():
    return dt.date.today().strftime("%Y-%m-%d")

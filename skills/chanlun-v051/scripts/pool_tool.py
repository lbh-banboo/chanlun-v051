#!/usr/bin/env python3
"""股票池维护工具：把池文件"解析契约"与"入池前置检查"做成可执行的校验（references/05-pool-file-contract.md）。

子命令
  check     校验池文件是否满足下游脚本的解析契约（名单六列、名称不加粗、主题簇成员与名单一致、簇表后无其它表格、冷却期写法…）
            有错误时退出码 1。改池文件之前、之后各跑一次。
  add       新增一只票：核名称、核代码格式与重复、算 1.5a 资金可交易性、按契约写入名单行（并可挂到一个主题簇），写完自动 check。
            --entity-name 传行情源/妙想返回的"实体名称"，与 --name 不一致就拒绝（错字会被模糊匹配成一只不相干的票）。
  ceilings  按净值算 1.5a 价格天花板（主板 净值÷800；创业板 净值×10%÷100；科创板 净值×10%÷200）。
  cooldown  卖出后冷却期：逐日列出 3 个冷却交易日与最早可买回日（禁止只写"冷却至 X"，曾因此少算一天违规）。

入池 ≠ 可买：入池只是观察名单；能不能买只由 V5.1 的结构买点与八道闸门决定。入池必须发生在信号出现之前（加入日期决定哪些买点计入账本）。
"""
import os
import re
import sys
import json
import argparse
import datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _paths as P
import _common as C

CODE_RE = re.compile(r"\d{6}\.(?:SH|SZ)")


# ------------------------------------------------------------------ 解析辅助
def _read(pool):
    return open(P.pool_path(pool), encoding="utf-8").read()


def _rows(txt):
    """名单节里的表格行 → [(行号, cells)]；cells[0] 为空串（首个 | 之前），cells[1]=名称，cells[2]=代码…"""
    lines = txt.split("\n")
    out, inside = [], False
    for i, ln in enumerate(lines):
        if re.match(r"## 名单", ln):
            inside = True; continue
        if inside and ln.startswith("## "):
            break
        if inside and ln.lstrip().startswith("|"):
            out.append((i, [c.strip() for c in ln.split("|")]))
    return out


def check_pool(pool):
    """返回 (errors, warnings, stats)。"""
    txt = _read(pool)
    err, warn = [], []
    try:
        _start = P.load_config("v51_config_focus.json" if "focus" in pool else "v51_config.json").get("start_date") or ""
    except SystemExit:
        _start = ""
    if not re.search(r"\n## 名单", txt):
        return ["缺少「## 名单」节（名单表必须在该节内）"], [], {}
    rows = _rows(txt)
    data, codes = [], {}
    for i, cells in rows:
        if len(cells) >= 3 and CODE_RE.fullmatch(cells[2] or ""):
            data.append((i, cells))
    for i, cells in data:
        ln = i + 1
        name, code = cells[1], cells[2]
        if "*" in name:
            err.append("第 %d 行 %s：名称列加粗了（会被解析器静默丢掉，下游名称全部污染）" % (ln, name))
        if len(cells) < 8:
            err.append("第 %d 行 %s：不是六列（名称｜代码｜板块｜加入日期｜入池来源｜状态）" % (ln, name))
        if code in codes:
            err.append("第 %d 行：代码 %s 与第 %d 行重复" % (ln, code, codes[code]))
        codes[code] = ln
        join = cells[4] if len(cells) > 4 else ""
        if "前" in join:
            if _start and re.search(r"(\d{4}-\d{2}-\d{2})", join) and re.search(r"(\d{4}-\d{2}-\d{2})", join).group(1) >= _start:
                warn.append("第 %d 行 %s：加入日期（%s）晚于实时回测起点 %s 却带「前」字＝不设限，入池前确认的买点也会计入账本；新增票请写具体日期" % (ln, name, join, _start))
        elif not re.search(r"\d{4}-\d{2}-\d{2}", join):
            warn.append("第 %d 行 %s：加入日期没写成 YYYY-MM-DD[ HH:MM]（解析不到＝不设限，入池前的信号也会计入）" % (ln, name))
        status = cells[6] if len(cells) > 6 else ""
        row_txt = "|".join(cells[1:])
        if "冷却" in row_txt and not re.search(r"最早(?:可买)?\s*\d{4}-\d{2}-\d{2}|视同仍在冷却期|已解除", row_txt):
            warn.append("第 %d 行 %s：写了「冷却」但不是可识别的写法（要写「最早 YYYY-MM-DD 可买回」或「视同仍在冷却期」；已过期的写「冷却期已解除」），冷却期不会生效" % (ln, name))
    names = {cells[1]: cells[2] for _, cells in data}
    # 主题簇
    secs = re.split(r"\n## 主题簇", txt)
    if len(secs) < 2:
        warn.append("没有「## 主题簇」节：主题簇 30% 上限不起作用")
    else:
        sec = secs[1].split("\n## ")[0]
        header_seen = False; in_cluster_tbl = False; table_closed = False
        member_of = {}
        for ln in sec.split("\n"):
            s_ = ln.strip()
            is_row = s_.startswith("|")
            if is_row:
                cells = [c.strip() for c in s_.strip("|").split("|")]
                if not header_seen and cells[0] == "主题簇":                  # 簇表表头
                    header_seen = True; in_cluster_tbl = True
                    if "成员" not in "".join(cells):
                        err.append("主题簇表头缺「成员」列")
                    continue
                if table_closed:                                              # 簇表已结束，又出现表格行
                    err.append("主题簇表之后又出现了表格行（解析器会把它当成簇行，簇数虚高）：%s" % s_[:50])
                    continue
                if not in_cluster_tbl or re.fullmatch(r"[-: |]+", s_):
                    continue
                if len(cells) >= 4 and not cells[0].startswith("-"):
                    cname = cells[0].replace("*", "")
                    for nm in re.split(r"[、，,]", cells[2].replace("*", "")):
                        nm = nm.strip()
                        if not nm:
                            continue
                        if nm not in names:
                            warn.append("主题簇「%s」的成员「%s」不在名单里（名称须与名单表完全一致，否则被忽略）" % (cname, nm))
                        else:
                            member_of.setdefault(nm, []).append(cname)
            elif in_cluster_tbl:                                              # 簇表内遇到空行或普通文字 → 簇表结束；其后再出现表格行即违约
                in_cluster_tbl = False; table_closed = True
        for nm in names:
            if nm not in member_of:
                warn.append("「%s」不属于任何主题簇（簇 30%% 上限管不到它）" % nm)
    # 簇叠加
    txt_over = C.read_overlay(txt)
    for kind, src, dst in txt_over:
        if kind == "股票" and src not in names:
            warn.append("簇叠加：股票「%s」不在名单里" % src)
    stats = dict(n=len(names), overlay=len(txt_over), has_clusters=len(secs) >= 2)
    return err, warn, stats


# ------------------------------------------------------------------ 1.5a / 冷却期
def last_close(code):
    p = os.path.join(P.DAILY, code + ".csv")
    if not os.path.exists(p):
        return None, None
    with open(p, "rb") as f:
        f.seek(0, 2); n = f.tell(); f.seek(max(n - 600, 0)); tail = f.read().decode("utf-8").strip().splitlines()
    c = tail[-1].split(",")
    return float(c[6]), c[0]                                        # raw_close（不复权）, 日期


def trading_days_after(date, n, cal=None):
    """sold 日之后的 n 个交易日。优先用日线库里的指数日历；日历不够长就按周一至周五推算（返回第二项＝是否含推算）。"""
    if cal is None:
        cal = []
        p = os.path.join(P.DAILY, P.INDEX_CODE + ".csv")
        if os.path.exists(p):
            cal = [l.split(",")[0] for l in open(p, encoding="utf-8").read().strip().splitlines()[1:]]
    out = [d for d in cal if d > date][:n]
    assumed = False
    d = dt.date.fromisoformat(out[-1] if out else date)
    while len(out) < n:
        d += dt.timedelta(days=1)
        if d.weekday() < 5:
            out.append(d.isoformat()); assumed = True
    return out, assumed


def cooldown_text(sold_date):
    cool, assumed = trading_days_after(sold_date, 3)
    earliest, assumed2 = trading_days_after(sold_date, 4)
    e = earliest[-1]
    md = "、".join(d[5:] for d in cool)
    txt = "**冷却期（逐日）%s → 最早 %s 可买回**" % (md, e)
    return txt, cool, e, (assumed or assumed2)


# ------------------------------------------------------------------ add
def cmd_add(a):
    if a.entity_name and a.entity_name.strip() != a.name.strip():
        raise SystemExit("名称不一致：输入「%s」，行情源返回「%s」。错字会被模糊匹配成别的票——先向使用者确认，不要直接采用返回的代码。" % (a.name, a.entity_name))
    if "*" in a.name:
        raise SystemExit("名称不得加粗/含 *")
    if not CODE_RE.fullmatch(a.code):
        raise SystemExit("代码格式应为 999001.SH / 999003.SZ（A 股，不含北交所/港股）")
    txt = _read(a.pool)
    names, _ = C.read_pool(a.pool)
    if a.code in names:
        raise SystemExit("%s 已在池内（%s），不重复登记" % (a.code, names[a.code]))
    cfg = P.load_config(a.cfg)
    cap = C.ceilings(cfg["nav0"])[C.board_of(a.code)]
    px, pdate = last_close(a.code)
    status = [a.status or "关注（新增）"]
    if px is None:
        status.append("1.5a 未核（日线库里没有该票）")
    elif px > cap:
        status.append("**★★ 1.5a 闸门不通过**：收 %.2f ＞ 天花板 %.2f（净值 %.0f），入池仅跟踪，当前不可执行" % (px, cap, cfg["nav0"]))
    else:
        status.append("收 %.2f｜1.5a 通过（天花板 %.2f）" % (px, cap))
    now = dt.datetime.now()
    join = a.join or (now.strftime("%Y-%m-%d %H:%M") if (now.weekday() < 5 and (now.hour, now.minute) < (15, 0)) else now.strftime("%Y-%m-%d"))
    row = "| %s | %s | %s | %s | %s | %s |" % (a.name, a.code, a.sector, join, a.source, "｜".join(status))
    lines = txt.split("\n")
    rows = _rows(txt)
    if not rows:
        raise SystemExit("名单表为空，先手工放好表头")
    grp_i = None
    if a.group:
        for i, cells in rows:
            if cells[2] == "" and a.group in cells[1].replace("*", ""):
                grp_i = i
                break
    if a.group and grp_i is None:                                      # 分组不存在：在表尾新建分组行
        ins = rows[-1][0] + 1
        lines[ins:ins] = ["| **%s（1 只）** | | | | | |" % a.group, row]
    elif grp_i is not None:                                            # 插到该分组最后一只之后，并把分组标题里的"（N 只）"加一
        last = grp_i
        for i, cells in rows:
            if i > grp_i:
                if cells[2] == "":                                     # 下一个分组行
                    break
                last = i
        m = re.search(r"（(\d+) 只）", lines[grp_i])
        if m:
            lines[grp_i] = lines[grp_i].replace(m.group(0), "（%d 只）" % (int(m.group(1)) + 1))
        lines.insert(last + 1, row)
    else:
        lines.insert(rows[-1][0] + 1, row)
    new = "\n".join(lines)
    new = re.sub(r"(## 名单（\*\*)(\d+)( 只\*\*)", lambda m: m.group(1) + str(len(names) + 1) + m.group(3), new, count=1)
    if a.cluster:                                                       # 挂到主题簇（已有则追加成员，没有则新建一行）
        new = _add_to_cluster(new, a.cluster, a.name)
    open(P.pool_path(a.pool), "w", encoding="utf-8").write(new)
    print("已写入：", row[:160])
    err, warn, st = check_pool(a.pool)
    for e in err: print("ERROR:", e)
    for w in warn: print("WARN :", w)
    print("名单 %s 只｜簇叠加 %s 条｜错误 %d｜提示 %d" % (st.get("n", "?"), st.get("overlay", 0), len(err), len(warn)))
    print("\n下一步：① 补该票的 30 分钟数据（m30_data.py recent --codes %s --start <历史库末端之后>）；② 跑 v51_scan.py（入池前确认的买点自动不计，只管加入日期之后的信号）；"
          "③ 入池不等于可买——是否能买只由 V5.1 的结构买点与八道闸门决定。" % a.code)
    sys.exit(1 if err else 0)


def _add_to_cluster(txt, cluster, name):
    lines = txt.split("\n")
    in_sec = False; last_tbl = None; header_i = None
    for i, ln in enumerate(lines):
        if ln.startswith("## 主题簇"):
            in_sec = True; continue
        if in_sec and ln.startswith("## "):
            break
        if in_sec and ln.strip().startswith("|"):
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            if cells[0] == "主题簇":
                header_i = i
            last_tbl = i
            if header_i is not None and cells[0].replace("*", "") == cluster and len(cells) >= 4:
                cells[2] = cells[2] + "、" + name
                m = re.fullmatch(r"\**(\d+)\**", cells[1])
                if m:
                    cells[1] = str(int(m.group(1)) + 1)
                lines[i] = "| " + " | ".join(cells) + " |"
                return "\n".join(lines)
    if last_tbl is None:
        return txt
    lines.insert(last_tbl + 1, "| %s | 1 | %s | 新增（归簇口径待使用者裁定；裁定前按保守口径单独成簇） |" % (cluster, name))
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="股票池维护工具")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("check"); s.add_argument("--pool", default="watchlist.md")
    s = sub.add_parser("add"); s.add_argument("--pool", default="watchlist.md"); s.add_argument("--cfg", default="v51_config.json")
    s.add_argument("--name", required=True); s.add_argument("--code", required=True); s.add_argument("--sector", required=True, help="申万二级 / 三级，须取数，不得按印象填")
    s.add_argument("--source", required=True, help="入池来源（谁、何时、依据）"); s.add_argument("--group", help="名单里的行业分组标题（子串匹配；没有就新建）")
    s.add_argument("--join", help="加入日期，默认：盘中 YYYY-MM-DD HH:MM，收盘后 YYYY-MM-DD"); s.add_argument("--status", help="状态列开头，默认「关注（新增）」")
    s.add_argument("--cluster", help="挂到哪个主题簇（已有则追加成员）"); s.add_argument("--entity-name", help="行情源返回的实体名称，须与 --name 一致")
    s = sub.add_parser("ceilings"); s.add_argument("--nav", type=float); s.add_argument("--cfg", default="v51_config.json")
    s = sub.add_parser("cooldown"); s.add_argument("--sold", required=True, help="卖出日 YYYY-MM-DD")
    a = ap.parse_args()
    if a.cmd == "check":
        err, warn, st = check_pool(a.pool)
        for e in err: print("ERROR:", e)
        for w in warn: print("WARN :", w)
        print("名单 %s 只｜簇叠加 %s 条｜错误 %d｜提示 %d" % (st.get("n", "?"), st.get("overlay", 0), len(err), len(warn)))
        sys.exit(1 if err else 0)
    elif a.cmd == "add":
        cmd_add(a)
    elif a.cmd == "ceilings":
        nav = a.nav or P.load_config(a.cfg)["nav0"]
        c = C.ceilings(nav)
        print("净值 %.0f 下的 1.5a 价格天花板：主板 %.2f｜创业板 %.2f｜科创板（一手 200 股）%.2f" % (nav, c["main"], c["gem"], c["star"]))
        print("规则：一手（科创板 200 股）市值不得超过单票上限（主板 20%、创业板/科创板 10%），且 1% 风险约束下止损距离按 8% 推算；净值变动后必须复算。")
    elif a.cmd == "cooldown":
        txt, cool, e, assumed = cooldown_text(a.sold)
        print(txt)
        print("冷却日：", "、".join(cool), "｜最早可买回：", e)
        if assumed:
            print("注意：日线库里的交易日历不够长，部分日期按周一至周五推算，遇节假日请人工顺延。")


if __name__ == "__main__":
    main()

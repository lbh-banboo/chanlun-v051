#!/usr/bin/env python3
"""把 assets/scheduled-tasks/*.md 模板渲染成可用的定时任务提示词，并输出对应的 cron 备选。

为什么有 cron 备选：定时任务提示词是给"能执行脚本的助手"（如桌面版 Claude 的定时任务）用的；
不想用助手的人，直接用系统 cron 跑 v51_scan.py 也完全可以——账本、日报、信号日志都是脚本写的，助手只负责"读结果、写汇报"。

用法
  python3 install_tasks.py --home ~/a-share-v51 --address 你 [--python python3] [--focus-n 100 --focus-start YYYY-MM-DD --nav 200000]
        [--out ~/a-share-v51/tasks_rendered]       渲染到目录（默认 $V51_HOME/tasks_rendered）
        [--only v51-close-1835 ...]                只渲染指定任务
        [--crontab]                                额外打印可直接粘贴进 `crontab -e` 的脚本版（无助手汇报）
渲染后：把 tasks_rendered/<name>.md 的正文粘进你的定时任务（Claude 桌面版：`create_scheduled_task`，cron 取模板 frontmatter 里的值）。
"""
import os
import re
import sys
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
TPL = os.path.abspath(os.path.join(HERE, "..", "assets", "scheduled-tasks"))


def render(text, vars_):
    out = text
    for k, v in vars_.items():
        out = out.replace("{{%s}}" % k, str(v))
    left = sorted(set(re.findall(r"\{\{(\w+)\}\}", out)))
    if left:
        raise SystemExit("还有未替换的占位符：%s" % left)
    return out


def split_front(text):
    m = re.match(r"---\n(.*?)\n---\n(.*)", text, re.S)
    if not m:
        raise SystemExit("模板缺少 frontmatter")
    fm = {}
    for ln in m.group(1).splitlines():
        k, _, v = ln.partition(":")
        fm[k.strip()] = v.strip().strip('"')
    return fm, m.group(2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", default=os.environ.get("V51_HOME") or "~/a-share-v51")
    ap.add_argument("--address", default="你", help="汇报时对使用者的称呼")
    ap.add_argument("--python", default="python3")
    ap.add_argument("--focus-n", default="100"); ap.add_argument("--focus-start", default="YYYY-MM-DD"); ap.add_argument("--nav", default="200000")
    ap.add_argument("--out"); ap.add_argument("--only", nargs="*"); ap.add_argument("--crontab", action="store_true")
    a = ap.parse_args()
    home = os.path.abspath(os.path.expanduser(a.home))
    out = os.path.abspath(os.path.expanduser(a.out)) if a.out else os.path.join(home, "tasks_rendered")
    os.makedirs(out, exist_ok=True)
    vars_ = dict(HOME=home, SKILL=HERE, PY=a.python, ADDRESS=a.address, NAV=a.nav, FOCUS_START=a.focus_start, FOCUS_N=a.focus_n)
    names = []
    for f in sorted(os.listdir(TPL)):
        if not f.endswith(".md") or f == "README.md":
            continue
        if a.only and f[:-3] not in a.only:
            continue
        txt = open(os.path.join(TPL, f), encoding="utf-8").read()
        fm, body = split_front(txt)
        rendered = "---\nname: %s\ndescription: %s\ncron: \"%s\"\n---\n%s" % (fm["name"], fm["description"], fm["cron"], render(body, vars_))
        open(os.path.join(out, f), "w", encoding="utf-8").write(rendered)
        names.append((fm["name"], fm["cron"], fm["description"]))
        print("渲染", f, "→", os.path.join(out, f))
    print("\n任务（name / cron / 说明）：")
    for n, c, d in names:
        print("  %-24s %-24s %s" % (n, c, d))
    if a.crontab:
        print("\n# ---- 可粘贴进 crontab -e 的脚本版（无助手汇报；时间为机器本地时间；先 export 你的 V51_HOME）----")
        print("V51_HOME=%s" % home)
        print("2,32 10,11,14 * * 1-5  %s %s/v51_scan.py >> %s/live/cron_scan.log 2>&1" % (a.python, HERE, home))
        print("32 13 * * 1-5          %s %s/v51_scan.py >> %s/live/cron_scan.log 2>&1" % (a.python, HERE, home))
        print("2 15 * * 1-5           %s %s/v51_scan.py >> %s/live/cron_scan.log 2>&1" % (a.python, HERE, home))
        print("35 18 * * 1-5          %s %s/close_pipeline.py >> %s/live/cron_close.log 2>&1 && %s %s/v51_scan.py >> %s/live/cron_close.log 2>&1 && %s %s/trin_daily.py >> %s/live/cron_close.log 2>&1" % (
            a.python, HERE, home, a.python, HERE, home, a.python, HERE, home))
        print("7,37 10,11,14 * * 1-5  %s %s/v51_scan_focus.py >> %s/live/cron_scan_focus.log 2>&1" % (a.python, HERE, home))
        print("7 15 * * 1-5           %s %s/v51_scan_focus.py >> %s/live/cron_scan_focus.log 2>&1" % (a.python, HERE, home))
        print("# 注意：cron 不会像助手那样读结果写汇报；信号看 live/reports/V51_latest.md 和 live/state/signals_log.jsonl。")
        print("# 注意：盘中扫描 10:02 之类的时刻要等新浪把 K 线推出来（滞后几秒到几十秒）；v51_scan 自带 +8 秒的收盘判定缓冲。")


if __name__ == "__main__":
    main()

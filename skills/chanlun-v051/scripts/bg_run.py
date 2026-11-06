#!/usr/bin/env python3
"""后台启动长任务（立即返回，不会撞执行通道的单次超时）。日志与 pid 落在 $V51_DATA/logs/。
用法：python3 bg_run.py NAME -- COMMAND [ARGS...]
例：  python3 bg_run.py m30_download -- python3 m30_data.py download --pool watchlist.md
      python3 bg_run.py backfill -- python3 backfill_sohu_batch.py
看进度：tail -f $V51_DATA/logs/NAME.log

为什么要有它（实测教训）：
  · 部分执行通道（如桌面助手的本机脚本工具）单次调用约 60 秒就超时，且不能传参；下载/补数/全量重算必须后台跑、结果落盘、再从日志读；
  · baostock 只能单进程：两个 baostock 任务要先后顺序跑，不要并发；
  · 本机同时挂的重计算后台进程不要超过 3 个，否则宿主机卡死、所有工具调用超时（曾实际发生）；
  · macOS 上会自动套 caffeinate 防止长任务中途睡眠。
"""
import os
import re
import sys
import shutil
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import _paths as P


def main():
    if "--" not in sys.argv or len(sys.argv) < 4:
        raise SystemExit(__doc__)
    i = sys.argv.index("--")
    name, cmd = sys.argv[1], sys.argv[i + 1:]
    # NAME 会拼进日志/pid 文件名；不校验的话 "../x" 能把日志写到工作区外。
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
        raise SystemExit("NAME 只允许字母、数字、下划线、连字符（≤64 字符），避免日志路径穿越：%r" % name)
    logd = os.path.join(P.DATA, "logs"); os.makedirs(logd, exist_ok=True)
    if cmd[0] in ("python3", "python"):
        cmd[0] = sys.executable                                    # 用同一个解释器，避免环境不一致
    if sys.platform == "darwin" and shutil.which("caffeinate"):
        cmd = ["caffeinate", "-i"] + cmd
    log = open(os.path.join(logd, name + ".log"), "a")
    p = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, cwd=HERE, start_new_session=True, env=dict(os.environ))
    open(os.path.join(logd, name + ".pid"), "w").write(str(p.pid))
    print("started %s pid=%d 日志 %s" % (name, p.pid, os.path.join(logd, name + ".log")))


if __name__ == "__main__":
    main()

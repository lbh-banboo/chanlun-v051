#!/usr/bin/env python3
"""工作区路径与配置的唯一入口（全部脚本共用，不要在别处写死路径）。

环境变量
  V51_HOME  工作区根目录。默认 ~/a-share-v51。第一次用先跑：python3 init_workspace.py
  V51_DATA  行情数据根目录。默认 $V51_HOME/data。日线+30 分钟线几个 GB，可以放到别的盘。

工作区布局（init_workspace.py 会建出骨架）
  $V51_HOME/
    config/    v51_config.json、v51_config_focus.json      账户本金、起算日、30 分钟历史覆盖到哪天
    pools/     watchlist.md、focus.md                       股票池（唯一权威名单，格式契约见 references/05）
    data/      daily/ daily_hist/ m30/ m30_recent/ meta/    行情
    pipeline/  regime_YYYYMMDD.json、TRIN_YYYYMMDD.*        收盘流程产物（档位等）
    live/      state*/ reports*/ watch*/                    实时回测账本、日报、结构遍历
    journal/   daily-review/ intraday/                      定时任务逐次落盘的报告
"""
import os
import json

HOME = os.path.abspath(os.path.expanduser(os.environ.get("V51_HOME") or "~/a-share-v51"))
DATA = os.path.abspath(os.path.expanduser(os.environ.get("V51_DATA") or os.path.join(HOME, "data")))

CONFIG = os.path.join(HOME, "config")
POOLS = os.path.join(HOME, "pools")
PIPE = os.path.join(HOME, "pipeline")
LIVE = os.path.join(HOME, "live")
JOURNAL = os.path.join(HOME, "journal")

DAILY = os.path.join(DATA, "daily")              # 全市场日线（不复权价+前复权因子 adj），每只一个 csv，最新一日 adj=1
DAILY_HIST = os.path.join(DATA, "daily_hist")    # 可选：更早的日线历史，只用于给 DAILY 往前补足 250 日准入窗口
M30 = os.path.join(DATA, "m30")                  # 30 分钟历史（baostock，不复权）<code>.csv.gz
M30_RECENT = os.path.join(DATA, "m30_recent")    # 历史库末端到"实时扫描起点"之间的补丁 <code>.csv
META = os.path.join(DATA, "meta")                # mcap_live.json、c_update_log.json、backfill_batch_log.json

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
ENGINE_DIR = os.path.join(SCRIPTS, "engine")

INDEX_CODE = "000300.SH"                          # 档位用的指数（沪深300），日线文件放在 DAILY 里


def ensure_dirs():
    for d in (CONFIG, POOLS, PIPE, LIVE, JOURNAL, DAILY, DAILY_HIST, M30, M30_RECENT, META):
        os.makedirs(d, exist_ok=True)


def load_config(name="v51_config.json"):
    """读账户配置。必填键：start_date、nav0、m30_base_end。"""
    p = os.path.join(CONFIG, name)
    if not os.path.exists(p):
        raise SystemExit("找不到配置 %s。先运行 init_workspace.py，或检查 V51_HOME（当前 %s）" % (p, HOME))
    cfg = json.load(open(p, encoding="utf-8"))
    miss = [k for k in ("start_date", "nav0", "m30_base_end") if k not in cfg or cfg[k] in ("", None)]
    if miss:
        raise SystemExit("配置 %s 缺少（或为空的）必填键 %s。start_date＝实时回测起点；m30_base_end＝baostock 历史库（含 m30_recent）覆盖到的最后一个交易日，"
                         "其后的 K 线由实时扫描写入 state/m30_ext" % (p, miss))
    cfg.setdefault("stage_sim", 1.0)
    cfg.setdefault("stage_real", 0)
    cfg.setdefault("max_hold_days", 120)
    return cfg


def require_data(codes=None, need_index=False, need_m30=False):
    """数据没准备好时给出"缺什么、怎么补"的清晰提示并退出，而不是抛 FileNotFoundError 堆栈（新手第一次上手最常撞到这里）。"""
    miss = []
    if not os.path.isdir(DAILY) or not [f for f in os.listdir(DAILY) if f.endswith(".csv")]:
        miss.append("日线库为空（%s）。先补日线：见 references/04-setup-and-data.md 第 2 节（backfill_sohu_batch.py / daily_update.py，或自备 csv）" % DAILY)
    else:
        if need_index and not os.path.exists(os.path.join(DAILY, INDEX_CODE + ".csv")):
            miss.append("缺沪深300 指数日线 %s.csv（档位计算要用，放在 %s）" % (INDEX_CODE, DAILY))
        if codes:
            no = [c for c in codes if not os.path.exists(os.path.join(DAILY, c + ".csv"))]
            if no:
                miss.append("池内 %d 只票没有日线（例：%s）。先补日线，或从池文件里移除这些票" % (len(no), "、".join(no[:5])))
    if need_m30 and codes:
        no = [c for c in codes if not os.path.exists(os.path.join(M30, c + ".csv.gz")) and not os.path.exists(os.path.join(M30_RECENT, c + ".csv"))]
        if no:
            miss.append("池内 %d 只票没有 30 分钟历史（例：%s）。先下载：python3 scripts/m30_data.py download --pool <池文件>，再 check（见 references/04 第 3 节）" % (len(no), "、".join(no[:5])))
    if miss:
        raise SystemExit("数据还没准备好：\n  - " + "\n  - ".join(miss) + "\n（V51_HOME=%s；换位置请设置环境变量 V51_HOME / V51_DATA）" % HOME)


def pool_path(pool_file):
    return pool_file if os.path.isabs(pool_file) else os.path.join(POOLS, pool_file)

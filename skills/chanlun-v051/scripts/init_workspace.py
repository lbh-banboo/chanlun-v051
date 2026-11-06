#!/usr/bin/env python3
"""初始化工作区骨架：建目录、写示例配置与示例池文件。已存在的文件一律不覆盖。

用法：python3 init_workspace.py [--home ~/a-share-v51] [--nav 200000] [--start 2026-01-05]
然后按 references/04-setup-and-data.md 的顺序：补日线 → 下 30 分钟历史 → 编辑 pools/watchlist.md → 算档位 → 首次扫描。

写入的东西（全部是模板，不含任何真实持仓）：
  config/v51_config.json         账户虚拟本金 nav0、实时回测起点 start_date、30 分钟历史库覆盖到哪天 m30_base_end
  config/v51_config_focus.json   第二个池（重点池）用；不用第二个池可删
  pools/watchlist.md             示例池文件（3 只示例票，用来演示格式契约；换成你自己的名单）
"""
import os
import sys
import json
import shutil
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.abspath(os.path.join(HERE, "..", "assets"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", default=os.environ.get("V51_HOME") or "~/a-share-v51")
    ap.add_argument("--nav", type=float, default=200000.0, help="虚拟本金（元）。1.5a 价格天花板、仓位都按它算")
    ap.add_argument("--start", default=None, help="实时回测起点 YYYY-MM-DD；不给则留空串，v51_scan 会拒绝运行，逼你显式定")
    a = ap.parse_args()
    os.environ["V51_HOME"] = os.path.abspath(os.path.expanduser(a.home))
    sys.path.insert(0, HERE)
    import _paths as P
    P.ensure_dirs()
    for d in ("state", "reports", "watch"):
        os.makedirs(os.path.join(P.LIVE, d), exist_ok=True)
    for sub in ("daily-review", "intraday"):
        os.makedirs(os.path.join(P.JOURNAL, sub), exist_ok=True)

    def put(dst, src=None, text=None):
        if os.path.exists(dst):
            print("已存在，跳过", os.path.relpath(dst, P.HOME)); return
        if src:
            shutil.copy2(src, dst)
        else:
            open(dst, "w", encoding="utf-8").write(text)
        print("写入", os.path.relpath(dst, P.HOME))

    cfg = {"start_date": a.start or "", "nav0": a.nav, "stage_sim": 1.0, "stage_real": 0, "max_hold_days": 120, "m30_base_end": "",
           "note": "start_date＝实时回测起点；nav0＝虚拟本金；stage_sim＝记账按满额口径(1.0)；stage_real＝真实下单系数(实时回测期必须是 0)；"
                   "m30_base_end＝baostock 历史库(含 data/m30_recent)覆盖到的最后一个交易日，其后的 K 线由实时扫描写入 live/state/m30_ext"}
    put(os.path.join(P.CONFIG, "v51_config.json"), text=json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
    cfg_f = dict(cfg); cfg_f["note"] = "重点池（池B）配置：与 v51_config.json 同参数，账本独立（live/state_focus、reports_focus）。起算日＝入池日的下一个交易日。"
    put(os.path.join(P.CONFIG, "v51_config_focus.json"), text=json.dumps(cfg_f, ensure_ascii=False, indent=2) + "\n")
    put(os.path.join(P.POOLS, "watchlist.md"), src=os.path.join(ASSETS, "pools", "watchlist.example.md"))
    print("\n工作区：", P.HOME)
    print("下一步：编辑 config/v51_config.json 把 start_date、m30_base_end 填上（空串会让 v51_scan 拒绝运行），再按 references/04-setup-and-data.md 准备数据。")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""重点池（池B）V5.1 实时回测扫描：包装 v51_scan.py，指定重点池名单（pools/focus.md）、独立配置（config/v51_config_focus.json）
与独立账本目录（live/state_focus、live/reports_focus），不碰关注池账本。参数同 v51_scan.py：--no-fetch --asof "YYYY-MM-DD HH:MM" --start 日期 --tag 名
只设置环境变量，再原样运行 v51_scan.py。"""
import os, runpy
HERE = os.path.dirname(os.path.abspath(__file__))
os.environ["V51_POOL_FILE"] = "focus.md"
os.environ["V51_CFG"] = "v51_config_focus.json"
os.environ["V51_TAG"] = "focus"
os.environ["V51_LABEL"] = "重点池"
runpy.run_path(os.path.join(HERE, "v51_scan.py"), run_name="__main__")

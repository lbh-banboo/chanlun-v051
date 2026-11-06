#!/usr/bin/env python3
"""重点池（池B）30 分钟缠论遍历：包装 pool_watch.py，指定重点池名单、独立配置、重点池自己的 ext 数据目录（live/state_focus/m30_ext）和输出目录（live/watch_focus/）。
用法：python3 pool_watch_focus.py [--no-fetch] [--asof "YYYY-MM-DD HH:MM"] [--look 10]
出报告：python3 pool_watch_report.py live/watch_focus/pool_watch_<日期_时刻>.json"""
import os, runpy
HERE = os.path.dirname(os.path.abspath(__file__))
os.environ["PW_POOL_FILE"] = "focus.md"
os.environ["PW_CFG"] = "v51_config_focus.json"
os.environ["PW_STATE"] = "state_focus"
os.environ["PW_OUT_DIR"] = "watch_focus"
os.environ["PW_LABEL"] = "重点池"
runpy.run_path(os.path.join(HERE, "pool_watch.py"), run_name="__main__")

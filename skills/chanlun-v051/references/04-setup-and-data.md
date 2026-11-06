# 04 · 搭建与数据准备

> 这是**从零把 V5.1 实时回测跑起来**的顺序。每一步都标了验证办法；跳步最常见的后果是"扫描能跑但看不见一半的票"。

## 0. 前置

- Python ≥3.9，`pip install pandas numpy`（TRIN 插件只用标准库；30 分钟历史下载要 `pip install baostock`；质地榜取数要 `MX_APIKEY`，见 [03](03-stock-screening.md)）。
- 网络：A 股行情源**直连**可用（新浪/东财/搜狐/baostock）；若本机走系统代理，注意代理对这些域名的路由（见 [11](11-incidents-and-pitfalls.md) 的代理一节）。
- 磁盘：全市场日线约 200MB，30 分钟历史全市场约 1.1GB（只下池内票则几十 MB）。
- 本机常驻：**实时回测靠定时任务在盘中每 30 分钟跑一次**，电脑睡眠/应用关闭期间错过的任务不会自己补——但账本重放是确定性的，补跑不丢记录。

## 1. 初始化工作区

```bash
python3 scripts/init_workspace.py --home ~/a-share-v51 --nav 200000
export V51_HOME=~/a-share-v51          # 之后所有脚本都读这个环境变量
```

生成 `config/`（本金、起算日、30 分钟历史覆盖到哪天）、`pools/watchlist.md`（示例池，格式契约见 [05](05-pool-file-contract.md)）、`data/`、`live/`、`journal/`。已存在的文件不会覆盖。

**`config/v51_config.json` 三个必填键（空串会让所有脚本拒绝运行）**

| 键 | 含义 |
|---|---|
| `start_date` | **实时回测起点**。此日之前的信号一律不计；起点之后才入池的票另按加入日期过滤 |
| `nav0` | 虚拟本金（元）。1.5a 天花板、仓位全按它算。**别拿真实净值乱填**——它只是纸面账本的初始资金 |
| `m30_base_end` | baostock 历史库（含 `data/m30_recent`）覆盖到的最后一个**交易日**。其后的 K 线由实时扫描写入 `live/state/m30_ext`。填错会导致 K 线重复或缺口 |

`stage_sim=1.0`（记账按满额口径）、`stage_real=0`（真实下单系数，实时回测期**必须是 0**）、`max_hold_days=120`。

## 2. 日线库（全市场，每只一个 csv）

格式（最新一日 `adj=1`）：`trade_date,open,high,low,close,volume,raw_close,amount,turnover,pct,adj`，其中 `open/high/low/close` 是**前复权价**，`raw_close` 是不复权收盘，`amount` 单位元，`volume` 单位手。放 `data/daily/<代码>.csv`，指数 `000300.SH.csv` 同目录。

```bash
python3 scripts/backfill_sohu_batch.py     # 首次：TARGET 默认＝最近已收盘交易日；搜狐一次 40 码，反复跑到 left 很小
python3 scripts/daily_update.py            # 之后每天 15:30 以后增量（新浪全市场快照 ~55 次请求）
```

- **首次建库要自己准备历史**：本仓库不附带行情。可用任何数据源整理成上述格式（至少 250 个交易日 + 沪深300），或用 `backfill_sohu_batch.py` 配合你自己的起始文件逐步回补。准入闸门要求上市满 250 个交易日的日线，档位计算要最近 330 个指数交易日。
- `daily_update.py` 有**两道闸门**（针对盘中价污染事故加）：①工作日 09:15—15:30 一律不写；②快照"前收"与库里上一日收盘大面积对不上（>20%）整次不写。详见 [11](11-incidents-and-pitfalls.md)。
- 漏更时用 `catchup_lastday.py`（只用搜狐已收盘日线，**盘中也能安全跑**）。
- 验证：`ls data/daily | wc -l` ≈ 5,200；抽查 `tail -2 data/daily/600036.SH.csv` 日期是最近交易日、`adj` 为 1。

## 3. 30 分钟历史（baostock，只下池内票）

```bash
python3 scripts/bg_run.py m30_dl -- python3 scripts/m30_data.py download --pool watchlist.md      # 后台，约 3—6 秒/只
python3 scripts/m30_data.py check --pool watchlist.md                                              # 必做：对账日线
```

**必须跑 `check`**——baostock 会**静默截断**：整段请求个别票只返回到某一年（实例：一只只到 2021-01-12 共 2000 根，一只只到 2024-02-22 共 8000 根），下载器不报错。`check` 逐只核"每个交易日应有 8 根、对日线交易日历无缺口、收盘/最高/最低与日线一致"。有问题的用 `m30_data.py fix --codes 代码`（按年分段重下，覆盖 ≥99% 且末日对才替换，旧文件备份到 `data/m30_bak/`）。已知数据源本身缺 1 天的票有若干只（不是你的错）。

再补"历史库末端 → 实时扫描起点"之间的几天：

```bash
python3 scripts/m30_data.py recent --pool watchlist.md --start <历史库末端往前留几天>     # 幂等；单次最多 WALL 秒，多跑几次
```

`m30_recent` 窗口不要按"13 天"想当然：含节假日，实际交易日更少（例：09-21→10-09 只有 9 个交易日＝72 根）；门槛别写死 100+ 根。

把 `config/v51_config.json` 的 `m30_base_end` 填成历史库+recent 覆盖到的最后一个交易日。

## 4. 池文件

编辑 `pools/watchlist.md`（契约见 [05](05-pool-file-contract.md)），然后：

```bash
python3 scripts/pool_tool.py check            # 必须 0 错误
```

## 5. 档位

```bash
python3 scripts/regime_daily.py --history      # 算最近一个交易日的档位，并把窗口内历史档位写到 pipeline/regime_history.csv（回放用）
```

`v51_scan` 读 `pipeline/regime_YYYYMMDD.json` 里日期 **< 当日** 的最新一份。**没有任何档位文件时回退到"修复期"并在日报里标"来源：无"**——别带着这个状态上线。

## 6. 首次扫描与自检

```bash
python3 scripts/v51_scan.py                    # 实时：取新浪 30 分钟线 → 引擎 → 账本
python3 scripts/v51_scan.py --no-fetch --asof "YYYY-MM-DD HH:MM" --start YYYY-MM-DD --tag chk   # 历史回放自检（不碰正式账本）
```

自检看什么：回放能跑通、信号/成交/平仓数合理、`live/reports_chk/V51_latest.md` 的"数据健康"一节取数失败 0、"落后于最新 K 线的票"0。正式扫描输出里 `NEW_SIGNAL / PENDING / FILL` 三类行是定时任务据以判断的协议。

## 7. 定时任务

见 [07](07-scheduled-tasks.md)。**先手动把每个任务的流程完整走一遍再上定时**（第一次跑常需要点工具授权）。

## 常见失败对照

| 现象 | 原因 | 处理 |
|---|---|---|
| `配置…缺少（或为空的）必填键` | `start_date`/`m30_base_end` 没填 | 填 `config/v51_config.json` |
| 日报"池内 N 只"比名单少 | 名称列加了粗体 / 表格不是六列 / 簇表后又有表格 | `pool_tool.py check` |
| 扫描只看见一部分票 | 引擎用了未修复版本（线段卡死） | 确认 `sop_engine.py` 含 `self.fixed += 1`；日报"引擎 md5"一行 |
| 同一根 K 线出现两次 / 指标全偏 | `m30_base_end` 填早了或 recent 与历史库重叠 | 重叠是安全的（按 `(date,time)` 去重），但键类型必须一致；见 [02](02-engine-spec.md) 第 8 节 |
| 档位一直是"修复期"、宽度 50% | 档位 json 是用不完整日线算的 | 补齐日线后重算；`regime_daily.py` 有覆盖闸门（`REGIME_ABORT`） |
| 助手的"本机脚本"工具之类执行通道 ~60 秒超时 | 长任务前台跑 | 用 `bg_run.py` 后台启动，读日志 |

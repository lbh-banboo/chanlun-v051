# 更新记录 / Changelog

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)：
**主版本**＝交易规则或引擎行为变化（会改变信号）；**次版本**＝新功能/新工具；**修订版本**＝文档、缺陷修复且不改变信号。

> 任何会改变信号的改动都必须：先走预登记 → 更新 `tests/golden_engine_events.json` 并在此处写明理由 → 提升主版本号。

## [1.0.1] — 2026-11-07

对应 GitHub Release `v1.0.1`。**不改变交易规则、引擎行为或任何信号**（修订版本）；
本次只修发布链路与文档，`.skill` 的功能不变。

### 修复

- **CI**：`test` 矩阵的 `actions/setup-python` 原带 `cache: pip`，但仓库没有
  `requirements.txt` / `pyproject.toml` 可计算缓存键，导致 4 个 Python 版本 job
  全部在准备阶段失败、测试步骤被跳过。已移除该选项（依赖版本约束直接写在 `pip install` 里）。
- **文档**：多处把全套自检写成「约 30 秒」，实测约 8–10 分钟（TRIN 健康检查最耗时）；
  README 的 Python 兼容性说明改为与 CI 矩阵一致的实测口径。
- **版本标识**：`plugin.json` 与 `SKILL.md` 的 `metadata.version` 对齐到 `1.0.1`。

## [1.0.0] — 2026-11-04

首个公开版本。由一套研究工具链整理、通用化而成。

### 新增

- **引擎**（`scripts/engine/`）：分型 → 笔 → 线段 → 中枢 → 一买 / 二买 / 三买 / 三卖 / 趋势一卖；包含**线段"永久卡死"缺陷修复**。
- **实时纸面回测**（`v51_scan.py`）：八道入场闸门、四档市场状态、四个卖出条件、T+1 与跌停顺延、账户熔断、冷却期、主题簇 30% 上限；确定性重放（漏跑不丢记录）；信号延迟记录；入池时刻过滤（防前视）。重点池包装 `v51_scan_focus.py`。
- **只读遍历**（`pool_watch*.py`）：临近买点、近期已确认买点、30 分钟强弱排序、回看确认率；`week_replay.py` 情景回放。
- **档位与收盘流程**：`regime_daily.py`（含覆盖闸门，防止用不完整日线写出错误档位）、`close_pipeline.py`。
- **数据维护**：`daily_update.py`（两道闸门，防止盘中价被当作收盘写库）、`backfill_sohu_batch.py`、`catchup_lastday.py`、`m30_data.py`（download / recent / fix / check，处理 baostock 静默截断）、`bg_run.py`。
- **股票筛选**：`pool_tool.py`（池文件契约校验、加票、1.5a 天花板、冷却期逐日列出）；申万一级行业质地榜全链路（`quality/`：问句生成 → 取数 → 解析 → 打分 → 快照 → 重点池）。
- **定时任务模板**（`assets/scheduled-tasks/`）：6 个任务 + `install_tasks.py` 渲染器 + cron 示例。
- **TRIN 观察插件**（`trin_daily.py`，2.0）。
- **文档** 12 章（`references/`）：规则、引擎、筛选、搭建、池契约、扫描与账本、定时任务、回测证据、上线验收、纪律、事故与坑、TRIN。
- **测试**（`tests/`，不联网、合成数据）：引擎截断 + 黄金快照、引擎边界、TRIN、端到端、仓库自检（含脱敏扫描）。

### 通用化处理

- 路径全部改为 `V51_HOME` / `V51_DATA` 环境变量；池文件 `关注池_已确认.md` → `pools/watchlist.md`，重点池 → `pools/focus.md`；档位文件 `买点C_YYYYMMDD.json` → `pipeline/regime_YYYYMMDD.json`（`_common.regime_before()` 仍兼容旧命名）。
- 主题簇的两条"叠加"规则（原先写死在代码里）改为池文件里的 `## 簇叠加` 声明。
- 冷却期原先写死了一只票，改为从池文件状态列解析（`最早 YYYY-MM-DD 可买回` / `视同仍在冷却期`）。
- 档位计算抽成独立的 `regime_daily.py`，并加了覆盖闸门，防止用不完整日线写出错误档位。
- 不含：行情与账本数据、个人持仓与交易流水、外部同步脚本、全部回测流水线（预登记与大型回测脚本）。
- 验证：`tests/` 下的离线测试覆盖引擎截断/黄金快照、边界、TRIN 与端到端合成市场；示例池与配置通过 `pool_tool.py check`。本仓库不含任何真实行情、账本或持仓数据。
- 回测数字全部采用**引擎修复后**的复跑结果；修复前的数字已作废并在文中标明。

### 易用性

- 数据没准备好时（日线库为空、缺沪深300、池内票没有日线或 30 分钟历史），`v51_scan.py` / `regime_daily.py` / `pool_watch.py` 给出"缺什么、怎么补"的清晰提示，而不是 `FileNotFoundError` 堆栈；`week_replay.py` 与 `pool_watch_report.py` 参数写错时给出用法。这些情形都纳入了端到端测试。

### 已知问题

- 打分器的财年写死为 2025（`FY` 常量 + 字段后缀），换财年须整体重命名输入字段（见 `quality/SCHEMA.md`）。
- 质地榜取数依赖需要密钥的第三方服务；可替换，只需产出 `stocks.json` + `supp.json`。
- SOP 事件层验证与"叠加买点 C"检验的 S 引擎部分受线段缺陷影响，**未按预登记重跑**；相关取舍（盘整背驰不开仓、二买不设不破一买过滤、不用均线卖）的证据级别为"方向已见、数字待复核"。
- 定时任务会话偶发卡住（见 `references/11` C1）；根因未查明。

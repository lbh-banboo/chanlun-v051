---
name: chanlun-v051
description: A 股「稳健波段」V5.1 缠论交易系统的实时回测与研究工具箱：30 分钟线段引擎识别一买/二买/三买与卖点，按八道入场闸门、四档市场状态、账户熔断做纸面记账；含股票筛选（申万一级行业质地榜、重点池生成、1.5a 资金可交易性、入池前置检查、池文件格式契约）、盘中/收盘定时任务模板、日线与 30 分钟数据维护、TRIN 观察指标、回测证据与踩坑记录。用户提到 V5.1、缠论买卖点、30 分钟线段、关注池/重点池、质地榜、退潮期/hard、失效价、纸面账本、实时回测、盘中扫描、"现在能买什么"，或要搭建/运行/排查这套系统时使用。仅供研究与模拟，不构成投资建议，不自动下真单。
license: MIT
compatibility: 需要 Python 3.9+ 与 pandas、numpy；可联网访问新浪/东财/搜狐行情与 baostock（30 分钟历史）；质地榜取数需东方财富妙想 API 密钥（可选）。定时任务模板面向能执行本机脚本的助手（如 Claude 桌面版定时任务），也可改用系统 cron。
metadata:
  version: "1.0.0"
  market: "A-share"
  language: "zh-CN"
  upstream-manual: "稳健波段交易系统 V5.1（缠论买卖点交易流程）"
---

# A 股缠论 V5.1：实时回测与研究工具箱

**这是什么**：一套把"缠论买卖点"机械化后跑在 A 股关注池上的**实时纸面回测系统**——引擎（分型→笔→线段→中枢→买卖点）、入场闸门与仓位、市场档位、账户熔断、扫描与记账、股票筛选、定时任务、数据维护、回测证据和踩坑记录，全部在这里。

**它不是什么**：不是荐股，不是投资建议，**不会自动下真单**。实时回测期的真实下单系数是 0；规则在期内冻结。回测证据显示**优势主要来自人工挑票而非买点本身**（见 `references/08`）。使用者输出任何结论时都应带上这层诚实的限定。

> 语言：文档与输出默认中文。**用户称呼与语气以用户自己的偏好为准**（本技能不规定称呼）。

## 先分流：用户要做什么？

| 用户的话 | 去哪 | 先做什么 |
|---|---|---|
| "V5.1 的规则/买点/卖点/仓位/熔断是什么" | `references/01-trading-rules.md` | 直接引用，注明参数出处，**不要凭记忆改数字** |
| "引擎怎么识别三买/一买/二买/背驰/线段" | `references/02-engine-spec.md` | 对照 `scripts/engine/` 源码 |
| "怎么筛票/选股/建池/加票/质地榜/重点池" | `references/03-stock-screening.md` + `05-pool-file-contract.md` | `pool_tool.py check` |
| "从零搭起来 / 怎么准备数据" | `references/04-setup-and-data.md` | `init_workspace.py` |
| "现在能买什么 / 有没有新买点 / 今天扫描结果" | `references/06-live-scan-and-ledger.md` | **先快答**（见下），再核对 |
| "建/改/排查定时任务" | `references/07-scheduled-tasks.md` | 渲染 `assets/scheduled-tasks/*` |
| "回测结果怎样 / 有没有优势 / 能不能上真单" | `references/08-backtest-evidence.md` + `09-rollout-and-acceptance.md` | 先说局限，再说数字 |
| "为什么没信号 / 数据不对 / 任务卡住 / 档位不对" | `references/11-incidents-and-pitfalls.md` | 先用工具实查，再下判断 |
| "改规则/改引擎/改手册" | `references/10-discipline-and-verification.md` | **必须先问用户并得到明确同意** |
| "TRIN / 阿姆氏" | `references/12-trin-observation.md` | 只陈述事实，不下结论 |

## 不可违反的纪律（读完这几条再动手）

1. **规则冻结**。实时回测期内不得根据新回测改买卖规则；要改就开新版本、重计 3 个月。**改手册、改引擎、覆盖文件，一律先问用户，拿到明确"同意"再做**——分析与建议不等于授权。
2. **数字只来自脚本输出与报告文件**。读不到就写"报告未提供"；不编造、不凭记忆补数、不自行换数据源。
3. **不给投资建议**。只做事实解读与规则推演，不说"该买该卖"；任何回测数字附"历史模拟，不代表未来，不构成投资建议"。
4. **回测数字有版本**。线段"永久卡死"缺陷修复**前**的 V5.x 回测数字已作废；引用以 `references/08` 标"修复后"的为准。
5. **入池 ≠ 可买**。能不能买只由结构买点＋八道闸门决定；"有空位"永远不是买点；用户指定入池不降低任何门槛。
6. **入池必须发生在信号之前**。池文件的"加入日期"决定哪些买点计入账本（防前视）。
7. **盘中被问"能买什么"先快答**：几分钟内先给结论（有没有新确认买点、哪只、限价＝信号价×1.03、失效价、股数、最要紧的一条风险），核对后补；临近收盘先说清今天还能不能下单。
8. **别在可能还在运行的任务会话旁边写正式账本**；只读的 `pool_watch.py` 随时可跑，`v51_scan.py` 写账本。
9. **本机并发重进程 ≤3**；长任务用 `bg_run.py` 后台跑、日志落盘。
10. **工作区在用户机器上**：路径由环境变量 `V51_HOME` 给出，不要写死；不要把用户的持仓、账号、密钥写进任何会被提交的文件。

## 工作区与脚本一览

```text
$V51_HOME/                config/ pools/ data/ pipeline/ live/ journal/        （init_workspace.py 建骨架）
scripts/
  _paths.py _common.py _fetch.py        共享：路径与配置、池文件解析/档位读取/常量、行情取数
  engine/                               lvl_engine.py（分型/笔/MACD）  sop_engine.py（线段/中枢/买卖点）  sop_run.py（run_series）
  v51_scan.py / v51_scan_focus.py       实时扫描 + 纸面记账（关注池 / 重点池包装）
  pool_watch.py / _focus / _report / _backcheck     只读遍历：临近买点、已确认买点、强弱排序、回看确认率
  week_replay.py                        挪起点的情景回放（不进正式账本）
  regime_daily.py                       收盘档位（带覆盖闸门）      close_pipeline.py  日线增量 → 档位
  daily_update.py / backfill_sohu_batch.py / catchup_lastday.py     日线增量 / 批量补数 / 漏跑补跑
  m30_data.py                           30 分钟历史：download / recent / fix / check（baostock）
  pool_tool.py                          池文件 check / add / ceilings / cooldown
  quality/                              质地榜：make_queries → mx_screen_client → parse_mx → score_quality → make_snapshot → build_focus_pool
  trin_daily.py                         TRIN 观察插件
  init_workspace.py install_tasks.py bg_run.py now.py
assets/    scheduled-tasks/（6 个任务模板）  cron/crontab.example  pools/watchlist.example.md  config/  templates/（执行卡、质地审查报告）
tests/     run_all.py  test_engine_truncation.py（无前视+黄金快照）  test_engine_edges.py  test_trin_health.py  test_pipeline_synthetic.py  test_repo_hygiene.py
```

## 最常用的 8 条命令

```bash
export V51_HOME=~/a-share-v51
python3 scripts/init_workspace.py --home $V51_HOME --nav 200000        # 建骨架（填 start_date / m30_base_end）
python3 scripts/pool_tool.py check                                       # 池文件契约校验（改池前后各跑一次）
python3 scripts/regime_daily.py --history                                # 算最近一个交易日的收盘档位
python3 scripts/v51_scan.py                                              # 实时扫描 + 记账；读 NEW_SIGNAL / PENDING / FILL
python3 scripts/v51_scan.py --no-fetch --asof "YYYY-MM-DD HH:MM" --start YYYY-MM-DD --tag chk   # 历史回放自检，不碰正式账本
python3 scripts/pool_watch.py && python3 scripts/pool_watch_report.py $V51_HOME/live/watch/pool_watch_*.json   # "现在离买点多近"
python3 scripts/install_tasks.py --home $V51_HOME --address 你           # 渲染定时任务提示词
python3 tests/run_all.py                                                 # 全部自检（不联网，约 30 秒）
```

## 回答"今天有没有信号 / 能买什么"的标准做法

1. **先读**（不要先写）：`live/reports/V51_latest.md`（若是重点池读 `reports_focus/`）与 `live/state/v51_ledger.json` 的 `pending / signals / skips`；需要更新就跑 `v51_scan.py`（写账本）或 `pool_watch.py`（只读）。
2. **先给结论**：新确认买点（股票、类别、确认时刻）→ 限价（信号收盘×1.03）→ 失效价 → 满额口径股数（实时回测期系数为 0，只记纸面）→ 最要紧的一条风险。
3. **再补核对**：档位及来源、数据健康（最后 K 线、取数失败、落后只数）、被闸门拦下的信号及原因、次日 9:25 取消条件。
4. **诚实限定**：当前是纸面；样本期短，3 个月约 6 笔，只能验证机制不能验证优势；优势主要来自挑票。
5. **边界**：不替用户决定买不买、买多少；用户的真实持仓与资金由用户自己管。

## 当用户要"改点什么"

- **加一只票**：`pool_tool.py add`（会核名称一致、1.5a、重复、写入契约格式并自检）→ 补 30 分钟数据（`m30_data.py recent --codes …`）→ `v51_scan.py`（入池前确认的买点自动不计）。
- **换整份重点池**：先问用户 → `score_quality.py` → `make_snapshot.py` → `build_focus_pool.py` → 补数据。遵守"新入池只管新信号、老持仓走完"。
- **改引擎/规则**：先问用户；开新版本；预登记；改前备份；回归测试（`tests/`）；复现关卡。见 `references/10`。
- **删/建定时任务**：宁少勿多；删除前后都明确说出任务名。

## 已知缺口与待用户裁定（别替用户做决定）

具名例外（没过 G1/G5 仍进实时回测；3 个月实时回测取代纸面 40 笔）、机制验收 5 条与小仓撤回线（建议值）、质地 C 的票挡不挡 V5.1、重点池与"只买关注池"条文的写法、主题簇归簇口径、两池重叠票的合计上限、退潮期规则的证据不足（见 `references/08` 第 9 节）。这些在 `references/09`、`11` 里逐条写了现状与默认处理。

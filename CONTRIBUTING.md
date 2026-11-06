# 贡献指南 / Contributing

谢谢你愿意改进这个项目。先说清楚这个项目的性质，以免期待错位：

- 它是一套**研究与模拟工具箱**，不是荐股工具，也不会下真单（见 [DISCLAIMER.md](DISCLAIMER.md)）。
- 它的价值在于**可复现、无前视、验证诚实**。所以评审时，**"这个改动怎么证明是对的"比"这个改动做了什么"更重要**。

## 开发环境

```bash
git clone https://github.com/lbh-banboo/chanlun-v051.git && cd chanlun-v051
python3 -m venv .venv && . .venv/bin/activate
pip install pandas numpy          # 下载 30 分钟历史才需要 baostock
python3 tests/run_all.py          # 必须全绿；不联网、约 30 秒
```

不要在仓库里放任何行情数据、账本、密钥或你自己的持仓。`.gitignore` 已经挡住 `data/ live/ pipeline/ journal/` 和常见密钥文件，`tests/test_repo_hygiene.py` 会扫描通用密钥形态、个人路径形态与示例池命名契约（不保存具体词表）。

## 改动分三类（要求逐级变严）

### 1. 文档、示例、模板、注释、错别字

直接提 PR。要求：`python3 tests/run_all.py --fast` 通过（含链接检查和脱敏扫描）。

### 2. 工具与数据链路（不改变任何信号）

例如：新的数据源适配、报告格式、`pool_tool.py` 的新子命令、定时任务模板文案、跨平台兼容。要求：
- 在 PR 里写明**如何证明没有改变信号**——最好是"改动前后，对同一份数据、同一时间点，账本逐字段对比差异为 0"（`v51_scan.py --no-fetch --asof … --start … --tag …`，对比 `live/state_<tag>/v51_ledger.json`）；
- 新增行为要有对应测试（合成数据即可）；
- 涉及网络请求的，必须有超时、有失败出口，失败要**如实报告而不是静默换数据源**。

### 3. 引擎行为 / 交易规则 / 账户参数（会改变信号）

这是最严的一类，因为它会让历史回测与实时结果脱钩。要求**全部**满足：

1. **先开 Issue 讨论**，说明动机和预期影响；不要直接提 PR。
2. **预登记**：在 PR 描述里写明——规则/参数的新旧值、要用什么数据和判据检验、第几次看数据；**看到结果之后再改参数＝新版本**，要重新开一个预登记。
3. **对照手册原文核对参数**（`references/01-trading-rules.md` 第 9 节参数总表）。历史上有两次凭记忆写错账户参数（熔断、单票上限），都是看过结果才发现。
4. **回归**：
   - 未受影响的票，改动前后事件**逐项相同**；
   - 截断重算 0 差异（`tests/test_engine_truncation.py` 必须通过，不要为了过测试放宽判据）；
   - 修引擎缺陷时按"复现关卡"：先用修复前引擎复现旧结果，再看修复后。
5. **更新黄金快照**：`python3 tests/test_engine_truncation.py --update-golden`，并在 PR 与 `CHANGELOG.md` 里写明**为什么**快照该变、变了什么。
6. **提升主版本号**，在 `CHANGELOG.md` 记录；`references/08-backtest-evidence.md` 里受影响的回测数字要标注"新版本口径"，旧数字标"作废"。
7. 不要在"实时回测期"内据新回测改规则——实时回测期的规则冻结由账户所有人裁定，这个仓库只提供机制。

## 提交信息

建议 [Conventional Commits](https://www.conventionalcommits.org/zh-hans/)：`feat:` 新功能、`fix:` 缺陷、`docs:` 文档、`test:` 测试、`refactor:` 重构（不改行为）、`chore:` 杂项。**会改变信号的提交请在类型后加 `!`**，如 `fix(engine)!: …`。

## 代码风格

- Python ≥3.9，标准库优先；新依赖需要在 PR 里说明理由。
- 脚本要能 `python3 -m py_compile`；路径一律经 `_paths.py`，不要写死；时间一律本机时钟，不要依赖容器时区。
- 注释写"为什么"，尤其是踩过坑的地方（带日期更好）。
- 输出给人看的文字用中文；协议行（`NEW_SIGNAL` / `PENDING` / `FILL` / `TRIN_*` / `REGIME_ABORT`）是下游依赖的接口，**不要改格式**，要改先开 Issue。

## 报告问题

- **看起来像缺陷**：附上 `python3 tests/run_all.py` 的输出、你的 Python 版本、操作系统、复现步骤；**不要贴行情数据、账本、密钥**。
- **你认为规则本身有问题**：请带上证据（回测设计、数据、对照组），并先读 [`references/08`](skills/chanlun-v051/references/08-backtest-evidence.md) 里已经写明的局限，避免重复讨论。
- **安全问题**（泄露密钥、可被利用的脚本等）：不要公开提 Issue，见 [SECURITY.md](SECURITY.md)。

## 行为准则

参与即表示同意遵守 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。

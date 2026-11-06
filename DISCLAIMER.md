# 免责声明 / Disclaimer

**请在使用本仓库的任何内容之前完整阅读。**

## 中文

1. **不构成投资建议。** 本仓库是一套**研究与模拟工具箱**及其文档，描述的是一种交易方法的机械化实现和历史模拟结果。其中的任何内容——规则、参数、回测数字、示例、报告模板、定时任务提示词、脚本输出——都**不是**对任何证券的买卖建议、推荐或要约，也不是对任何收益的承诺。
2. **不自动下真单。** 本仓库不含任何券商交易接口，也不会替你下单。实时回测期的"真实下单系数"为 0，所有成交均为**纸面记账**。
3. **历史模拟不代表未来。** 文档中的回测数字来自 2020 年中至 2026 年的**单一历史窗口**，其中大部分收益集中在两个自然年；回测证据显示**优势主要来自人工挑选的股票池**，而非买卖点本身（在没有挑选过的股票上，候选信号的逐笔平均收益为负）。没有通过原设计的全部验收门槛；"实时回测"本身也只能验证机制，验证不了策略有没有优势。请阅读 `skills/chanlun-v051/references/08-backtest-evidence.md` 的全部局限。
4. **数据可能出错。** 工具链依赖第三方公开行情接口（新浪、东方财富、搜狐、baostock 等）和一个可选的第三方数据服务（东方财富「妙想」）。这些接口可能延迟、缺失、改版或返回错误数据；工具链内置了一些闸门与对账，但**不保证数据正确、完整或及时**。你需要自己核对任何用于真实决策的数字。
5. **软件按"现状"提供。** 见 `LICENSE`（MIT）。作者和贡献者**不对任何因使用或无法使用本仓库造成的损失负责**，包括但不限于交易亏损、数据错误、定时任务失败、账本错乱。
6. **合规与权限由你自己负责。** 抓取行情数据请遵守相应网站的服务条款与所在地法律法规；使用第三方 API 请遵守其协议并保管好你自己的密钥（**不要把密钥提交到仓库**）。本仓库不含也不教授绕过任何访问限制的方法。
7. **本仓库的示例不是真实持仓。** 示例标的名称、代码、价格与账户数值均为占位示例，仅用于演示格式，不对应任何真实标的或持仓；回测统计来自历史行情模拟，见 `skills/chanlun-v051/references/08-backtest-evidence.md`。
8. **AI 助手的输出需要核实。** 如果你配合 AI 助手使用这些文档/提示词，助手可能出错。定时任务提示词里写明了"数字只来自脚本输出、读不到就写'报告未提供'、不给投资建议"，但这不能替代你自己的判断。
9. **你对自己的投资决策负全部责任。** 股市有风险，投资需谨慎。如有需要，请咨询持牌的专业机构。

## English

1. **Not investment advice.** This repository is a research and simulation toolkit plus documentation. Nothing here — rules, parameters, backtest figures, examples, templates, scheduled-task prompts or script outputs — is a recommendation, solicitation or offer to buy or sell any security, nor a promise of any return.
2. **No automatic order placement.** There is no brokerage integration. The live "real-money coefficient" during the live-test phase is 0; all fills are **paper-ledger entries**.
3. **Past simulation ≠ future results.** Backtests cover a single historical window (mid-2020 – 2026); most of the return comes from two calendar years. The evidence indicates the edge comes mainly from a **hand-picked stock pool**, not from the entry signals themselves (on un-picked stocks the average per-trade result of the candidate signals is negative). The design's full acceptance gates were not all passed, and a 3-month live test can only validate mechanics, not edge. Read `references/08-backtest-evidence.md` in full.
4. **Data may be wrong.** The toolchain relies on third-party public quote endpoints and an optional third-party data service. They may be delayed, incomplete, changed or erroneous. Built-in gates and reconciliations reduce, but do not eliminate, this risk.
5. **Provided "AS IS"** under the MIT License. The authors and contributors are not liable for any loss arising from use of this repository.
6. **Compliance is yours.** Respect the terms of service of data providers and applicable law. Never commit your API keys.
7. **Examples are placeholders**; they are not real holdings. Backtest statistics come from historical market simulations.
8. **AI-assistant output must be verified.**
9. **You are solely responsible for your own investment decisions.**

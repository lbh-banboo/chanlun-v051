<div align="center">

# chanlun-v051

**A 股缠论 V5.1：实时纸面回测 · 股票筛选 · 定时任务 · 数据维护 · 回测证据**

一套把"缠论买卖点"机械化后跑在 A 股关注池上的**研究与模拟工具箱**，同时是一个可被 AI 助手加载的 **Agent Skill**。

[中文](README.md) · [English](README.en.md) · [免责声明](DISCLAIMER.md) · [更新记录](CHANGELOG.md)

![license](https://img.shields.io/badge/license-MIT-blue)
![python](https://img.shields.io/badge/python-3.9%2B%20(CI)%20%C2%B7%203.10%20verified-blue)
![tests](https://img.shields.io/badge/tests-offline%20%C2%B7%20synthetic-brightgreen)
![status](https://img.shields.io/badge/status-paper%20trading%20only-orange)

</div>

> ## ⚠️ 先读这段
>
> - **不构成投资建议，不会自动下真单。** 所有成交都是纸面记账；实时回测期真实下单系数为 **0**。详见 [DISCLAIMER.md](DISCLAIMER.md)。
> - **回测证据并不支持"买点本身有优势"。** 在人工挑选的关注池上，这套规则年化约 7%、最大回撤约 −16%；放到**没挑过的**全市场股票上，候选信号逐笔平均 **−0.12R**。收益集中在 2023、2025 两个年份，去掉贡献最大的几只票就几乎归零。**优势主要来自"挑票"，不是买点。** 这个仓库的价值在于：规则可复现、没有前视、风控清晰、验证方法诚实——**不在于它能赚钱**。完整证据与局限见 [`references/08`](skills/chanlun-v051/references/08-backtest-evidence.md)。
> - 文档里的示例标的名称、代码、价格与账户数值均为**占位示例**，不对应任何真实标的或持仓；回测统计来自历史行情模拟（见 `references/08`）。

## 这是什么

| 你想要… | 这里有… |
|---|---|
| 一套**可复现、无前视**的缠论买卖点引擎 | 30 分钟线段引擎：分型 → 笔 → 线段 → 中枢 → 一买 / 二买 / 三买 / 三卖 / 趋势一卖，截断重算 0 差异 |
| 把买点变成**完整的交易系统** | 八道入场闸门、四档市场状态（含 hard 撤退条款）、风险预算仓位公式、账户熔断、主题簇 30% 上限 |
| **实时跑起来、不下真单** | 盘中每 30 分钟扫描 → 纸面成交 / 持仓 / 平仓 / 信号延迟记录 → 日报 + 次日待执行单 |
| 一套**选股流程**，不只是"找会涨的" | 九道闸门；申万一级行业**质地榜**（打分、红旗、补位）；重点池生成；1.5a 资金可交易性；池文件格式契约与校验 |
| **定时任务**直接可用 | 6 个任务模板（盘中 / 午后 / 收盘 K 线 / 收盘日报 / 重点池 ×2），也可输出 cron 版 |
| 数据**不出错** | 日线增量的两道闸门、30 分钟历史截断检测与修复、档位文件覆盖闸门 |
| **诚实的**回测证据 | 修复前后对照、随机篮子、全市场对照、收益集中度、验收门槛、退潮期规则的证据评估 |
| **踩坑记录** | 16 类常见故障与坑（数据、引擎、定时任务、业务口径、协作）：现象、根因、修法、如何避免 |

## 快速开始

```bash
git clone https://github.com/lbh-banboo/chanlun-v051.git
cd chanlun-v051
pip install pandas numpy                       # 30 分钟历史下载另需：pip install baostock

python3 tests/run_all.py                       # 先验证环境：全部自检，不联网，约 8–10 分钟

export V51_HOME=~/a-share-v51
python3 skills/chanlun-v051/scripts/init_workspace.py --home $V51_HOME --nav 200000
```

然后按 [`references/04-setup-and-data.md`](skills/chanlun-v051/references/04-setup-and-data.md) 的顺序：**补日线 → 下 30 分钟历史并 `check` → 编辑池文件 → 算档位 → 首次扫描**。每一步都写了验证办法。

```bash
S=skills/chanlun-v051/scripts
python3 $S/pool_tool.py check                  # 池文件必须 0 错误
python3 $S/regime_daily.py --history           # 收盘档位
python3 $S/v51_scan.py                         # 实时扫描 + 记账
# 输出里：NEW_SIGNAL = 新买点   PENDING = 下一根开盘待执行单   FILL = 纸面成交
python3 $S/pool_watch.py                       # 只读：现在谁离买点近
```

> **注意**：本仓库**不附带任何行情数据**（体量大、且是你自己的数据）。首次需要自己准备日线（格式见文档），30 分钟历史可用 `m30_data.py` 通过 baostock 下载。

## 作为 Agent Skill 使用

`skills/chanlun-v051/` 遵循 [Agent Skills](https://agentskills.io/specification) 目录规范（`SKILL.md` + `references/` + `scripts/` + `assets/`），`name` 与目录名一致，`description` 写明了触发场景。

| 平台 | 安装 |
|---|---|
| Claude Code（个人） | `cp -r skills/chanlun-v051 ~/.claude/skills/` |
| Claude Code（项目） | `cp -r skills/chanlun-v051 <项目>/.claude/skills/` |
| Claude Code 插件市场 | `/plugin marketplace add lbh-banboo/chanlun-v051` → `/plugin install chanlun-v051@chanlun-v051` |
| Claude 桌面版 / claude.ai | 上传打包好的 `chanlun-v051.skill`（见下方"打包"） |
| 其它支持 SKILL.md 的 Agent | 把 `skills/chanlun-v051/` 放进它的 skills 目录 |

装好后，直接对助手说：*"V5.1 的三买是怎么定义的？"*、*"帮我给关注池加一只票"*、*"今天有没有新买点？"*、*"为什么扫描一直没信号？"*、*"生成重点池"*。`SKILL.md` 会把请求分流到对应章节，并带着十条不可违反的纪律（规则冻结、数字只来自脚本输出、不给投资建议、改手册先问用户……）。

## 系统一览

```text
 行情源（新浪/东财/搜狐/baostock）
        │
        ▼
 daily_update ─► 日线库 ─► regime_daily ─► 收盘档位 ─┐
 m30_data ─────► 30 分钟历史 ─┐                       │ 仓位总闸：上限 + 风险倍率
                              ▼                       ▼
 池文件(pools/*.md) ─► v51_scan ─► 引擎(S: 30 分钟线段) ─► 八道入场闸门 ─► 纸面账本
        ▲                    │          三买/一买/二买/三卖/趋势一卖        ├─ 日报 V51_latest.md
        │                    │                                              ├─ 待执行单（9:25 取消条件）
 质地榜 → 重点池              └─► signals_log（信号延迟）                     └─ 熔断状态
 pool_tool（契约/1.5a/冷却期）          pool_watch（临近买点 / 强弱，只读）
```

### 核心规则（摘要，全文见 [`references/01`](skills/chanlun-v051/references/01-trading-rules.md)）

| 环节 | 规则 |
|---|---|
| 票从哪来 | 只做池里的票；上市满 250 日、20 日均额 ≥1 亿、非 ST、不停牌 |
| 买什么 | 三买（主力）、一买（趋势背驰）、二买（破不破一买都算）；**盘整背驰不开仓** |
| 何时买 | 30 分钟 K 线收盘确认 → 下一根开盘买；高开 >3% / 涨停 / 已破失效价则取消 |
| 买多少 | 净值 × 0.75% × 档位倍率 ÷（买价 − 失效价），取整；再受单票 / 总仓位 / 主题簇 30% / 20 只限制 |
| 止损 | 失效价 = 结构低点 × 0.99，**不移动**；30 分钟**收盘**跌破才算 |
| 卖出 | 失效价 / 三卖 / 趋势一卖 / 持满 120 日，先到先走；T+1、跌停顺延 |
| 档位 | 健康 70% / 高温 50% / 修复 40% / 退潮 20% / hard 0%（禁开新仓） |
| 熔断 | 连亏 5 笔停 5 日；回撤 10% 停新仓；12% 冻结；份额净值 ≤0.70 停用 |

## 目录

```text
.
├── README.md / README.en.md            本文件
├── DISCLAIMER.md  LICENSE              免责声明、MIT
├── CHANGELOG.md  CONTRIBUTING.md  SECURITY.md  CODE_OF_CONDUCT.md
├── skills/chanlun-v051/         ◀ Agent Skill 本体
│   ├── SKILL.md                        入口：分流 + 纪律 + 常用命令
│   ├── references/  01…12              规则、引擎、筛选、搭建、池契约、扫描账本、定时任务、回测证据、上线验收、纪律、事故、TRIN
│   ├── scripts/                        引擎、扫描、档位、数据、池工具、质地榜、TRIN
│   └── assets/                         任务模板、cron 示例、示例池/配置、执行卡与质地审查模板
├── tests/                              自检（不联网、合成数据）
├── docs/                               架构与设计说明
└── .github/                            Issue / PR 模板、CI
```

## 测试

```bash
python3 tests/run_all.py          # 全部（约 8–10 分钟；TRIN 健康检查最耗时）
python3 tests/run_all.py --fast   # 跳过端到端
```

| 测试 | 它在防什么 |
|---|---|
| `test_engine_truncation.py` | **前视**：数据截到任意时刻重算，已发出的信号必须与全量一致；**规则被悄悄改**：40 条固定种子合成序列的"黄金快照"；**线段卡死缺陷**回归 |
| `test_engine_edges.py` | 三买"回试低点 ≥ ZG（含等于）"这类贴线边界、线段奇偶性与短线段规则、成笔间隔、包含处理方向、账户层常量（单笔风险 0.75%、档位倍率、1.5a、涨跌幅） |
| `test_trin_health.py` | TRIN 插件：合成市场逐项注入异常，213 项断言 |
| `test_pipeline_synthetic.py` | 端到端：init → 日线 → 30 分钟 → 池 → 档位 → 扫描 → 账本不变量（净值恒等式、现金可由流水重建、股数整手、单笔风险上限）→ 池维护 → 任务渲染；含**确定性**与**入池时刻过滤** |
| `test_repo_hygiene.py` | SKILL.md 规范、文档链接、脚本引用、**文档里的每条命令行参数都真实存在**（用 `--help` 探测，保证无副作用）、**脱敏扫描**（通用密钥/路径形态 + 示例池命名契约）、占位符闭合、示例数据 |

这些测试本身也用"**突变检验**"验证过——故意把引擎和常量改坏，看测试能不能发现：还原线段卡死缺陷、制造三种前视、改成笔间隔、改三买边界/中枢 ZG/背驰判据/二买阈值、改仓位与档位常量、把创业板 301 当 10% 涨跌幅……共 19 个有效突变**全部被抓住**；另有 1 个（线段最少笔数 `>=3` 改 `>=2`）经 20 万条随机序列证明是**等价突变**（线段结束端点只可能落在奇数下标，两种写法对任何输入结果相同），不算漏网。第一轮检验曾暴露两处盲区（背驰面积偷看未来、三买贴线边界），已通过扩大黄金快照和增加边界用例补上。仓库自检同样做过植入验证：密钥、令牌、私钥、私有 ID、邮箱、手机号、个人路径、失效链接、非法 name、超长 description、占位符未定义、大文件等泄露/违规，以及 5 种命令行参数错误都能被抓住，合法命令不误报；其中两处（变量名带前缀的密钥赋值、脚本名写错被静默跳过）最初漏过，已修正。自检文件不保存任何作者相关的具体词表，避免「防御性词表」本身成为泄露源。

## 打包为 `.skill`

```bash
cd skills && zip -r ../chanlun-v051.skill chanlun-v051 -x '*/__pycache__/*' '*.pyc'
```

## 参与

欢迎 Issue 与 PR，请先读 [CONTRIBUTING.md](CONTRIBUTING.md)。**引擎行为 / 交易规则相关的改动有额外要求**（预登记、回归测试、更新黄金快照并说明理由）。安全问题见 [SECURITY.md](SECURITY.md)。

## 已知局限（再说一遍）

- 只有**一个 6 年窗口**，2023、2025 两年贡献了大部分收益；30 分钟历史从 2020 年起，**2015—2020 的历史验证段不可检验**。
- 关注池是**事后挑出来的强势票**；没挑过的票上不赚钱。重点池（按基本面挑）的实时结果才是样本外检验，但要等时间。
- 实时回测 3 个月只有约 6 笔信号，**只能验证机制，验证不了策略有没有优势**。
- 退潮期（soft/hard）规则的证据不足，只够当成"降回撤的仓位刻度"，不够当成择时信号。
- 设计里留有若干**待账户所有人裁定**的口径缺口（具名例外、质地 C 的票挡不挡、重点池条文写法、主题簇归簇…），见 [`references/09`](skills/chanlun-v051/references/09-rollout-and-acceptance.md)、[`11`](skills/chanlun-v051/references/11-incidents-and-pitfalls.md)。
- 部分取数依赖第三方公开接口，可能随时改版；质地榜取数依赖需要密钥的第三方服务（可替换，见 [`scripts/quality/SCHEMA.md`](skills/chanlun-v051/scripts/quality/SCHEMA.md)）。

## 许可

[MIT](LICENSE)。请同时阅读 [DISCLAIMER.md](DISCLAIMER.md)。

## 兼容性说明

| 项 | 状态 |
|---|---|
| Python 3.9 / 3.10 / 3.11 / 3.12 | 由 GitHub Actions CI 矩阵**真实运行全套自检**（见 `.github/workflows/ci.yml`）；结果见仓库 Actions 页面 |
| 本机实测 | Python 3.9.6 与 3.11 全套 6/6 通过；代码按 3.9 语法树静态检查通过（无 `X\|Y` 注解、无 `match`、无 `zip(strict=)` 等 3.10+ 特性） |
| macOS | 已在 macOS 验证；`bg_run.py` 在 macOS 上自动套 `caffeinate` |
| Linux | 自检在 Linux 容器内运行通过 |
| Windows | **未验证**。路径处理用 `os.path`，但 `bg_run.py`（`start_new_session`）与 cron 示例是 POSIX 向的，Windows 请用 WSL |

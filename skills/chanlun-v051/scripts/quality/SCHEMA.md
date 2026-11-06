# 质地榜数据格式（SCHEMA）

质地榜流水线的每一步都读写 JSON。本文档写清**每个文件的形状**，方便你换数据源（不用妙想）时只替换第一步。

```text
make_queries.py   →  q.json, q2.json                 自然语言问句集
mx_screen_client.py →  r.json, r2.json               接口原始返回（幂等续跑）
parse_mx.py       →  stocks.json                     主表（每只一条）
(自备/抓取)        →  supp.json                       补充字段（最近完整财年现金流等）
score_quality.py  →  result2.json                    打分结果（每行业 top10 + 降级观察）
make_snapshot.py  →  slim.json                       压缩快照（build_focus_pool.py 的输入）
build_focus_pool.py → pools/focus.md + focus.json    重点池名单
```

> **想换数据源**：只要你能产出 `stocks.json` + `supp.json`（形状见下），第 3 步之后的所有脚本照常工作。

## stocks.json —— `{ "600000": {…}, … }`（键是 6 位代码，不含交易所后缀）

| 字段 | 含义 | 单位 / 备注 |
|---|---|---|
| `code`, `name` | 代码、简称 | |
| `sw` | 申万行业分类链，形如 `机械设备-自动化设备-工控设备` | 一级-二级-三级 |
| `sw1` | 申万一级（`sw` 的第一段） | 打分按它分组 |
| `board` | 上市板块：`上交所主板` / `深交所主板` / `创业板` | 科创板、北交所已被 `parse_mx.py` 排除 |
| `price` | 最新价 | 元 |
| `roe25`, `roe24`, `roe23` | 最近三个完整财年的加权 ROE | %（字段后缀＝财年，FY=2025 时为 25/24/23） |
| `roeL` | 最新一期 ROE | % |
| `cagr3` | 近三年营收复合增速 | % |
| `rev_yoy` | 最新营收同比 | % |
| `np_yoy` | 最新半年扣非净利润同比 | % |
| `gm` | 销售毛利率 | % |
| `debt` | 资产负债率 | % |
| `ocf` | 最新半年经营活动现金流净额 | 元（可负） |
| `kf_h1` | 最新半年报扣非归母净利润 | 元 |
| `pe`, `pb` | 市盈率 TTM（归母）、市净率 | |
| `pe_pct`, `pb_pct` | 5 年百分位 | %（接口的 PE 分位按归母 PE 算；一次性收益会把它压低） |
| `mv` | 总市值 | 元 |
| `amt` | 成交额 | 元 |
| `dy` | 年度股息率（银行/非银/高股息行业） | % |
| `npl`, `pcr` | 不良贷款率、拨备覆盖率（仅银行） | % |

## supp.json —— `{ "600000": {…}, … }`

| 字段 | 含义 |
|---|---|
| `ocf25`, `ocf24` | 最近两个完整财年的经营活动现金流净额（元） |
| `rev25` | 最近完整财年营收同比（%） |
| `kf25`, `kf24` | 最近两个完整财年扣非归母净利润（元） |
| `kfy25` | 最近完整财年扣非同比（%） |
| `days` | 上市天数（用来反推上市日；缺失时按 99999 当老股） |

## result2.json —— `{ "行业": { n_all, n_pass, n_elig, n_red, med, top[], watch[] }, … }`

- `n_all` 样本数；`n_pass` 过门槛数；`n_elig` 达标数（过门槛＋上市后 ROE 均值≥8%＋非经常性损益占比≤30%＋无红旗）；`n_red` 红旗数。
- `top`：行业内前 10（达标优先、不足用过门槛无红旗的票补位 `tier='fill'`），每项含全部原始字段加：
  `score`（0—100）、`s_prof / s_grow / s_qual / s_val`（四个分组得分 0—100）、`rank`、`tier`（`elig` / `fill`）、`fill_why`、`red[]`、`yel[]`、`roe_avg / roe_min / roe_years`、`pe_kf`（扣非 PE）、`nr_share`（TTM 非经常性损益占比 %）、`ocf_r25 / ocf_r_h1`（现金质量）、`list_date`、`young`（上市不足 5 年）。
- `watch`：**降级观察**（有红旗但分数够进前十，或上一版前十里的红旗票）。红旗票**不进前五**。
- `med`：行业中位数（`roe_avg, gm, pe_kf, debt, cagr3`），用于判断"相对第一"是否只是弱行业里的矮子里拔将军。

## slim.json —— `[ { ind, n_all, n_pass, n_elig, n_red, med, top[], watch[] }, … ]`

同 result2.json 但改成列表（按 `n_all` 降序）并只保留下游字段。`build_focus_pool.py` 只用 `ind`、`top[].rank/code/name/sw/score/tier/fill_why/roe_avg/pe_kf/yel/red`。

## 已知限制

- 打分器里财年写死为 `FY=2025`（字段后缀 `roe25/roe24/roe23/ocf25/kf25`）。换财年时：把输入字段整体按"最近三个完整财年"重命名为这些键，并改 `score_quality.py` 顶部的 `FY`。**不要只改常量不改输入**——那样会把 2026 年的数据当 2025 年算。
- `score_quality.py` 的 `--asof` 只用来由上市天数反推上市日，必须是数据截止日。
- 金融（银行、非银）走各自的权重表，不看毛利率与现金流；这两个行业的问句必须单独问（见 `make_queries.py` 注释）。
- 综合行业这类样本少、质地普遍达不了标的行业，前五会全是"补位"票（标灰）；这是规则的预期行为，不是 bug。

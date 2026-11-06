<div align="center">

# chanlun-v051

**A-share Chan-theory (缠论) V5.1: live paper-trading · stock screening · scheduled tasks · data upkeep · backtest evidence**

A **research and simulation toolkit** that mechanises Chan-theory entry/exit points on a hand-picked A-share watchlist — and an **Agent Skill** an AI assistant can load.

[中文](README.md) · [English](README.en.md) · [Disclaimer](DISCLAIMER.md) · [Changelog](CHANGELOG.md)

![license](https://img.shields.io/badge/license-MIT-blue)
![python](https://img.shields.io/badge/python-3.9%2B%20(CI)%20%C2%B7%203.10%20verified-blue)
![status](https://img.shields.io/badge/status-paper%20trading%20only-orange)

</div>

> **Docs are primarily in Chinese** (the strategy, data sources and terminology are China A-share specific). This page is a concise English guide; `skills/chanlun-v051/references/` holds the full detail.

> ## ⚠️ Read this first
>
> - **Not investment advice. No automatic order placement.** Every fill is a paper-ledger entry; the live "real-money coefficient" is **0**. See [DISCLAIMER.md](DISCLAIMER.md).
> - **The evidence does *not* show that the entry signals themselves have an edge.** On the hand-picked watchlist the rules return ≈7 % annualised with a ≈ −16 % max drawdown; on **un-picked** stocks the candidate signals average **−0.12 R per trade**. Returns are concentrated in two calendar years and nearly vanish once the few biggest winners are removed. **The edge comes mainly from picking the stocks, not from the entries.** What this repo offers is reproducibility, no look-ahead, clear risk controls and honest validation — **not profitability**. See [`references/08-backtest-evidence.md`](skills/chanlun-v051/references/08-backtest-evidence.md).
> - Sample stock names, codes, prices and account figures in the docs are **placeholders**; backtest statistics come from historical market simulations (see `references/08-backtest-evidence.md`).

## What's inside

| You want… | You get… |
|---|---|
| A **reproducible, look-ahead-free** Chan engine | 30-minute *segment* engine: fractal → stroke → segment → pivot (中枢) → 1st/2nd/3rd buy, 3rd sell, trend 1st sell. Truncate-and-recompute = 0 differences |
| A **complete trading system** around it | 8 entry gates, 4 market regimes (+ a "hard" retreat clause), risk-budget position sizing, account circuit-breakers, 30 % theme-cluster cap |
| **Live paper trading** | Every 30-min bar: scan → paper fills / positions / exits / signal-latency log → daily report + next-day pending orders with 9:25 cancel conditions |
| A **stock-selection pipeline** | 9-gate funnel; Shenwan-L1 **quality ranking** (scoring, red flags, back-fill); focus-pool generator; price-ceiling gate (1.5a); pool-file contract + validator |
| **Scheduled tasks** | 6 task templates (intraday ×3, close report, focus-pool ×2) and a plain-`cron` variant |
| **Data hygiene** | Two guards on the daily-bar updater, truncation detection/repair for 30-min history, coverage guard on the regime file |
| **Honest backtest evidence** | Before/after engine-fix comparison, random baskets, full-market control, concentration, acceptance gates, evidence review of the regime rule |
| **Post-mortems** | 16 real incidents/pitfalls: symptom → root cause → fix → how to avoid |

## Quick start

```bash
git clone https://github.com/lbh-banboo/chanlun-v051.git && cd chanlun-v051
pip install pandas numpy                  # + baostock if you download 30-min history
python3 tests/run_all.py                  # offline self-tests (~30 s) — run this first

export V51_HOME=~/a-share-v51
S=skills/chanlun-v051/scripts
python3 $S/init_workspace.py --home $V51_HOME --nav 200000
# then follow references/04-setup-and-data.md: daily bars → 30-min history (+ `m30_data.py check`) → pool file → regime → first scan
python3 $S/pool_tool.py check             # pool file must report 0 errors
python3 $S/regime_daily.py --history
python3 $S/v51_scan.py                    # NEW_SIGNAL / PENDING / FILL lines
```

**No market data is bundled.** You must supply daily bars (format documented) and can download 30-minute history through baostock with `m30_data.py`.

## Use as an Agent Skill

`skills/chanlun-v051/` follows the [Agent Skills](https://agentskills.io/specification) layout (`SKILL.md` + `references/` + `scripts/` + `assets/`).

| Platform | Install |
|---|---|
| Claude Code (user) | `cp -r skills/chanlun-v051 ~/.claude/skills/` |
| Claude Code (project) | `cp -r skills/chanlun-v051 <project>/.claude/skills/` |
| Claude Code plugin | `/plugin marketplace add lbh-banboo/chanlun-v051` then `/plugin install chanlun-v051@chanlun-v051` |
| Claude Desktop / claude.ai | upload the packaged `chanlun-v051.skill` (see below) |

`SKILL.md` routes requests to the right chapter and carries ten non-negotiable rules: rules are frozen during the live test; numbers come only from script output; no investment advice; ask before editing the manual or engine; etc.

## Core rules (summary)

| Item | Rule |
|---|---|
| Universe | Pool file only; listed ≥250 days, 20-day avg turnover ≥ ¥100 M, non-ST, trading |
| Entries | 3rd buy (main), 1st buy (trend divergence), 2nd buy (whether or not it breaks the 1st); **consolidation-divergence candidates are not traded** |
| Timing | Confirm on a closed 30-min bar → buy at the next bar's open; cancel if gap-up >3 %, limit-up, or open ≤ stop |
| Size | NAV × 0.75 % × regime multiplier ÷ (entry − stop), lot-rounded; then capped by per-name / total exposure / 30 % cluster / 20 names |
| Stop | Structure low × 0.99, **never moved**; triggered by a 30-min **close** below it |
| Exits | Stop / 3rd sell / trend 1st sell / 120 trading days — first to fire; T+1 and limit-down roll-over |
| Regimes | Healthy 70 % · Hot 50 % · Repair 40 % · Ebb 20 % · Hard 0 % (no new entries) |
| Breakers | 5 straight losses → pause 5 days; 10 % drawdown → pause new entries; 12 % → freeze; unit NAV ≤ 0.70 → retire |

## Tests

`python3 tests/run_all.py` runs, offline and on synthetic data: look-ahead (truncate-and-recompute) + golden snapshot of 40 fixed-seed series; exact-boundary cases (e.g. 3rd-buy pull-back low ≥ ZG, *including equal*); the TRIN plug-in (213 assertions); an end-to-end synthetic market (ledger invariants: NAV identity, cash reconstructable from fills, lot sizes, per-trade risk cap; determinism; pool-entry-time filtering); and a repository hygiene check (SKILL.md spec, links, **secret/PII scan**, placeholders).
The tests were themselves validated by **mutation testing**: 19 deliberate breakages of the engine/constants were all caught (plus one provably equivalent mutant); 19 injected leaks were all caught by the hygiene scan.

## Known limitations

- A **single 6-year window**; two calendar years carry most of the return; 30-min history starts in 2020, so 2015–2020 cannot be validated.
- The watchlist is **hand-picked in hindsight**; on un-picked stocks the signals lose money. The focus pool (picked by fundamentals) is the real out-of-sample test — it needs time.
- A 3-month live test yields ≈ 6 signals — enough to validate **mechanics**, not **edge**.
- Evidence for the "ebb/hard" regime rule is thin; it works as a drawdown-reducing exposure dial, not as a timing signal.
- Several design decisions are **pending the account owner's ruling** (named exceptions, quality-C names, focus-pool wording, cluster mapping…).
- Some data paths rely on third-party public endpoints that may change at any time.

## Contributing · Security · License

See [CONTRIBUTING.md](CONTRIBUTING.md) (engine/rule changes have extra requirements), [SECURITY.md](SECURITY.md). Licensed under [MIT](LICENSE) — and please read the [DISCLAIMER](DISCLAIMER.md).

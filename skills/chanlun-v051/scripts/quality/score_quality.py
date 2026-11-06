#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""申万一级行业质地榜打分（系统外的静态基本面初筛；入池 ≠ 可买，不看股价位置和趋势，不是买点）。
输入（--dir 目录下，格式见 SCHEMA.md）：stocks.json（主表）、supp.json（补充字段）、可选 r.json（原始接口返回，仅用于补 kf_25h1/上市天数）、
可选 result.json（上一版结果，仅用于"降级观察"里保留上一版前十的红旗票）；输出 result2.json。

规则（修订版，现行口径）：
  1. ROE 只算上市后的完整财年（上市日在当年 7/1 之前才算），次新股不再用上市前的高 ROE 撑分
  2. 估值改用扣非 PE（总市值 ÷ TTM 扣非），不再用含非经常性损益的归母 PE
  3. 上市不足 5 年的，PE/PB 五年分位不参与打分（给中性分）；非经常性损益占 TTM 归母 >20% 的，PE 分位也给中性分
     （接口的 PE 分位是按归母 PE 算的，一次性收益会把它压低）
  4. 现金质量改为最近完整财年经营现金流/扣非（权重 7）+ 最新半年报（权重 3），减少半年季节性
  5. 非银金融去掉营收和扣非增速（行情驱动），改看 ROE 水平与稳定性、PB、股息率
  6. 进入行业前五需「达标」：过门槛 + 上市后 ROE 均值 ≥ 8% + TTM 非经常性损益占比 ≤ 30% + 无红旗；
     红旗票移到「降级观察」；达标不足 5 只的行业用过门槛、无红旗的票补位并标明
     红旗：最新半年扣非同比 < -30%；最近完整财年与最新半年经营现金流都为负
字段后缀约定：本版固定 FY=2025（roe25/roe24/roe23＝最近三个完整财年，kf_h1＝2026 半年报扣非）；换财年请整体改名输入字段并改下面的 FY（见 SCHEMA.md「已知限制」）。

用法：python3 score_quality.py --dir DIR --asof YYYY-MM-DD
"""
import json, argparse, datetime as dt, statistics as st, os, sys

_ap = argparse.ArgumentParser(description="申万一级行业质地榜打分")
_ap.add_argument("--dir", default=".", help="放 stocks.json / supp.json 的目录，结果写 result2.json")
_ap.add_argument("--asof", required=True, help="数据截止日 YYYY-MM-DD（用来由上市天数反推上市日）")
_A = _ap.parse_args()
FY = 2025                                      # 最近一个完整财年；半年报年份＝FY+1
H1Y = FY + 1
D0 = dt.date.fromisoformat(_A.asof)
ROE_FLOOR = 8.0
CYC = {'有色金属', '煤炭', '钢铁', '基础化工', '石油石化', '建筑材料'}
_P = lambda n: os.path.join(_A.dir, n)


def num(v):
    if v is None: return None
    s = str(v).split('|')[0].strip().replace(',', '')
    if s in ('', '-', '--', 'None'): return None
    m = 1
    for u, f in (('万亿', 1e12), ('亿', 1e8), ('万', 1e4)):
        if s.endswith(u): s = s[:-len(u)]; m = f; break
    try: return float(s) * m
    except ValueError: return None


S = json.load(open(_P('stocks.json')))
SUP = json.load(open(_P('supp.json')))
S = {c: s for c, s in S.items() if not c.startswith(('688', '689', '4', '8', '92'))}

# 从原始返回里补 2025H1 扣非与上市天数（金融股不在 supp 里）；没有 r.json 时这两个字段须已在 stocks.json / supp.json 里
R = json.load(open(_P('r.json'))) if os.path.exists(_P('r.json')) else {}
for k, rec in R.items():
    if k.startswith('DIV'): continue
    for x in rec['raw']['data']['data']['allResults']['result'].get('dataList') or []:
        c = x['SECURITY_CODE']
        if c not in S: continue
        for kk, v in x.items():
            if 'KCFJCXSYJLR{2025-06-30}' in kk: S[c]['kf_25h1'] = num(v)
            elif 'KCFJCXSYJLR{2025-12-31}' in kk: S[c]['kf_25'] = num(v)
            elif 'TDATE_dummy' in kk: S[c]['days'] = num(v)

for c, s in S.items():
    sp = SUP.get(c, {})
    for k in ('ocf25', 'ocf24', 'rev25', 'kfy25', 'kf24'):
        s[k] = sp.get(k)
    if s.get('kf_25') is None: s['kf_25'] = sp.get('kf25')
    if s.get('days') is None: s['days'] = sp.get('days')


def prank(vals, v, higher=True):
    xs = [x for x in vals if x is not None]
    if v is None or not xs: return 0.0
    lt = sum(1 for x in xs if x < v); eq = sum(1 for x in xs if x == v)
    p = (lt + 0.5 * eq) / len(xs)
    return p if higher else 1 - p


for s in S.values():
    d = s.get('days') or 99999
    ld = D0 - dt.timedelta(days=int(d))
    s['list_date'] = str(ld)
    yrs = [y for y in (FY, FY - 1, FY - 2) if ld < dt.date(y, 7, 1)]
    s['roe_years'] = yrs
    s['roe_pre_ipo'] = len(yrs) < 3
    vals = [s['roe%d' % (y % 100)] for y in yrs] if yrs else [s['roe25']]
    vals = [v for v in vals if v is not None]
    s['roe_avg'] = st.mean(vals) if vals else None
    s['roe_min'] = min(vals) if vals else None
    s['young'] = d < 1825
    # TTM 扣非与扣非 PE
    kf_ttm = None
    if None not in (s.get('kf_25'), s.get('kf_h1'), s.get('kf_25h1')):
        kf_ttm = s['kf_25'] + s['kf_h1'] - s['kf_25h1']
    s['kf_ttm'] = kf_ttm
    s['pe_kf'] = s['mv'] / kf_ttm if kf_ttm and kf_ttm > 0 else None
    s['pe_kf_v'] = s['pe_kf'] if s['pe_kf'] else 9999
    np_ttm = s['mv'] / s['pe'] if s.get('pe') and s['pe'] > 0 else None
    s['nr_share'] = (1 - kf_ttm / np_ttm) * 100 if (np_ttm and kf_ttm is not None) else None
    # 估值分位是否可用
    s['pe_pct_ok'] = (not s['young']) and not (s['nr_share'] is not None and s['nr_share'] > 20)
    s['pb_pct_ok'] = not s['young']
    s['pe_pct_use'] = s['pe_pct'] if s['pe_pct_ok'] else None
    s['pb_pct_use'] = s['pb_pct'] if s['pb_pct_ok'] else None
    # 现金质量
    s['ocf_r25'] = s['ocf25'] / s['kf_25'] if (s.get('ocf25') is not None and s.get('kf_25') and s['kf_25'] > 0) else None
    s['ocf_r_h1'] = s['ocf'] / s['kf_h1'] if (s.get('ocf') is not None and s.get('kf_h1') and s['kf_h1'] > 0) else None

# (指标, 权重, 越高越好, 不可用时给中性分的开关字段)
NONFIN = [('roe_avg', 20, 1, None), ('roe_min', 10, 1, None),
          ('cagr3', 10, 1, None), ('rev_yoy', 5, 1, None), ('np_yoy', 5, 1, None),
          ('gm', 10, 1, None), ('ocf_r25', 7, 1, None), ('ocf_r_h1', 3, 1, None), ('debt', 5, 0, None),
          ('pe_kf_v', 10, 0, None), ('pe_pct_use', 10, 0, 'pe_pct_ok'), ('pb_pct_use', 5, 0, 'pb_pct_ok')]
BANK = [('roe_avg', 30, 1, None), ('roe_min', 10, 1, None), ('np_yoy', 10, 1, None), ('rev_yoy', 5, 1, None),
        ('npl', 15, 0, None), ('pcr', 10, 1, None), ('pb', 10, 0, None), ('dy', 10, 1, None)]
NB = [('roe_avg', 35, 1, None), ('roe_min', 25, 1, None), ('pb', 20, 0, None),
      ('pb_pct_use', 10, 0, 'pb_pct_ok'), ('dy', 10, 1, None)]
GROUP = {'prof': ['roe_avg', 'roe_min'], 'grow': ['cagr3', 'rev_yoy', 'np_yoy'],
         'qual': ['gm', 'ocf_r25', 'ocf_r_h1', 'debt', 'npl', 'pcr'],
         'val': ['pe_kf_v', 'pe_pct_use', 'pb_pct_use', 'pb', 'dy']}


def yi(v): return '%.2f亿' % (v / 1e8)


def flags_of(s, ind):
    red, yel = [], []
    fin = ind in ('银行', '非银金融')
    if s['np_yoy'] is not None and s['np_yoy'] < -30:
        red.append('%d上半年扣非同比%.1f%%，盈利修复未被证明' % (H1Y, s['np_yoy']))
    if not fin and s.get('ocf25') is not None and s['ocf25'] < 0 and s.get('ocf') is not None and s['ocf'] < 0:
        red.append('经营现金流连续两期为负（%d全年%s、%d上半年%s），利润的现金含量待验证' % (FY, yi(s['ocf25']), H1Y, yi(s['ocf'])))
    if s['roe_pre_ipo']:
        yel.append('%s上市，ROE只算上市后年度（%s）' % (s['list_date'][:7], '、'.join(str(y) for y in s['roe_years']) or '无完整年度，暂用2025'))
    if s['young']:
        yel.append('上市不足5年，PE/PB五年分位样本不足，不参与打分')
    if s['nr_share'] is not None and s['nr_share'] > 20 and not fin:
        yel.append('TTM归母里约%.0f%%是非经常性损益，PE已改按扣非算（扣非PE %.1f，归母PE %.1f）' % (s['nr_share'], s['pe_kf'] or 0, s['pe']))
    if ind in CYC and s['roe23'] and s['roe23'] > 0 and s['roe25'] > 2 * s['roe23'] and (s['pe_kf'] or 99) < 15:
        yel.append('周期高位：ROE较2023翻倍且PE低，低PE可能是在给周期顶定价')
    if s['np_yoy'] is not None and s['np_yoy'] > 200 and not fin:
        base = s.get('kf_25h1')
        if base is not None and base < 0.5e8:
            yel.append('扣非增速%.0f%%，上年同期基数只有%s，低基数效应' % (s['np_yoy'], yi(base)))
        else:
            yel.append('扣非增速%.0f%%，上年同期基数%s不算低，属主业放量，要看能否持续' % (s['np_yoy'], yi(base) if base else '—'))
    if not fin and s.get('ocf') is not None and s['ocf'] < 0 and not (s.get('ocf25') is not None and s['ocf25'] < 0):
        yel.append('上半年经营现金流为负（%s），但%d全年为正（%s），可能是季节性' % (yi(s['ocf']), FY, yi(s['ocf25']) if s.get('ocf25') is not None else '—'))
    if s['pe_pct_ok'] and s['pe_pct'] is not None and s['pe_pct'] > 90:
        yel.append('PE处于近5年90%分位以上，偏贵')
    if s['rev_yoy'] is not None and s['rev_yoy'] < -30:
        yel.append('上半年营收同比%.1f%%，先查原因（业务收缩还是需求下滑）' % s['rev_yoy'])
    if (s['pe_kf'] or 0) > 40 and s['np_yoy'] is not None and s['np_yoy'] < 10:
        yel.append('扣非PE %.0f倍而扣非增速不到10%%，不是便宜的高成长' % s['pe_kf'])
    if s.get('kfy25') is not None and s['kfy25'] < 0 and s['np_yoy'] is not None and -30 <= s['np_yoy'] < 0:
        yel.append('扣非连续两期下滑（%d全年%.1f%%、%d上半年%.1f%%）' % (FY, s['kfy25'], H1Y, s['np_yoy']))
    if ind == '非银金融' and '保险' in s['sw']:
        yel.append('保险利润受投资端公允价值变动影响大，低PE和高增速不能当可持续盈利，应结合PB与内含价值看')
    elif ind == '非银金融' and '证券' in s['sw']:
        yel.append('券商利润随成交和行情波动，ROE有周期性')
    return red, yel


OLD = json.load(open(_P('result.json'))) if os.path.exists(_P('result.json')) else {}
out = {}
by = {}
for s in S.values(): by.setdefault(s['sw1'], []).append(s)
for ind, L in by.items():
    fin = ind in ('银行', '非银金融')
    W = BANK if ind == '银行' else NB if ind == '非银金融' else NONFIN
    cols = {k: [x.get(k) for x in L] for k, _, _, _ in W}
    for s in L:
        sub = {}
        for k, w, hi, okf in W:
            if okf and not s.get(okf): sub[k] = 0.5 * w            # 不可用→中性
            else: sub[k] = prank(cols[k], s.get(k), bool(hi)) * w
        s['score'] = round(sum(sub.values()), 2)
        wsum = {k: w for k, w, _, _ in W}
        for g, ks in GROUP.items():
            ks2 = [k for k in ks if k in wsum]
            s['s_' + g] = round(sum(sub[k] for k in ks2) / sum(wsum[k] for k in ks2) * 100, 1) if ks2 else None
        s['red'], s['yel'] = flags_of(s, ind)

    def gate(s):
        if s['mv'] < 30e8: return False
        if s['roe_min'] is None or s['roe_min'] <= 0: return False
        if not s.get('kf_ttm') or s['kf_ttm'] <= 0: return False
        if not fin and s.get('debt') is not None and s['debt'] > 75: return False
        return True
    passed = [s for s in L if gate(s)]
    def ok_abs(s):  # 绝对门槛：ROE 均值达标，且利润不是靠一次性收益撑起来的
        if s['roe_avg'] < ROE_FLOOR: return False
        if not fin and s['nr_share'] is not None and s['nr_share'] > 30: return False
        return True
    for s in passed:
        why = []
        if s['roe_avg'] < ROE_FLOOR: why.append('上市后ROE均值%.2f%%，低于8%%' % s['roe_avg'])
        if not fin and s['nr_share'] is not None and s['nr_share'] > 30: why.append('TTM利润约%.0f%%来自非经常性损益' % s['nr_share'])
        s['fill_why'] = '；'.join(why)
    elig = sorted([s for s in passed if not s['red'] and ok_abs(s)], key=lambda s: -s['score'])
    fill = sorted([s for s in passed if not s['red'] and not ok_abs(s)], key=lambda s: -s['score'])
    reds = sorted([s for s in passed if s['red']], key=lambda s: -s['score'])
    ranked = elig + fill
    for i, s in enumerate(ranked):
        s['rank'] = i + 1
        s['tier'] = 'elig' if s in elig else 'fill'
    top = ranked[:10]
    cut = top[-1]['score'] if len(top) == 10 else -1
    old_top = {x['code'] for x in OLD.get(ind, {}).get('top', [])}
    watch = [s for s in reds if s['score'] >= cut or s['code'] in old_top][:8]
    out[ind] = dict(n_all=len(L), n_pass=len(passed), n_elig=len(elig), n_red=len(reds), top=top, watch=watch,
                    med={k: (round(st.median([x[k] for x in L if x.get(k) is not None]), 2) if any(x.get(k) is not None for x in L) else None)
                         for k in ['roe_avg', 'gm', 'pe_kf', 'debt', 'cagr3']})
json.dump(out, open(_P('result2.json'), 'w'), ensure_ascii=False)

old = OLD
for ind, o in sorted(out.items(), key=lambda kv: -kv[1]['n_all']):
    o5 = [s['name'] for s in old.get(ind, {}).get('top', [])[:5]]; n5 = [s['name'] for s in o['top'][:5]]
    fills = sum(1 for s in o['top'][:5] if s['tier'] == 'fill')
    print(f"{ind} 全{o['n_all']} 过门槛{o['n_pass']} 达标{o['n_elig']} 红旗{o['n_red']} 补位{fills} | 新前五 {'、'.join(n5)} | 出局 {'、'.join(x for x in o5 if x not in n5) or '—'} | 降级观察 {'、'.join(s['name'] for s in o['watch']) or '—'}")

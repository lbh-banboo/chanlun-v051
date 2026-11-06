#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 mx_screen_client.py 抓回的原始返回（r.json）解析成打分器输入 stocks.json（字段见 SCHEMA.md）。
按 前缀 区分三类查询键：普通行业（"行业|板块"）、金融（"FIN|行业"）、股息/不良贷款（"DIV|…"）。
财报期与日期由参数给定（下列为出厂默认值，使用时请按你的数据快照日期覆盖）：
  --fy 2025 --h1 2026-06-30 --asof 2026-10-11 --px 2026-10-09
排除：科创板、北交所（板块字段含"科创"/"北京"/"北交"）。
用法：python3 parse_mx.py --dir DIR --fy 2025 --h1 2026-06-30 --asof 2026-10-11 --px 2026-10-09"""
import json, re, os, argparse
_ap = argparse.ArgumentParser()
_ap.add_argument("--dir", default="."); _ap.add_argument("--fy", type=int, default=2025)
_ap.add_argument("--h1", default="2026-06-30", help="最新半年报期末日"); _ap.add_argument("--asof", default="2026-10-11", help="接口返回'最新 ROE'字段对应的日期")
_ap.add_argument("--px", default="2026-10-09", help="市净率字段对应的价格日")
_A = _ap.parse_args()
R = json.load(open(os.path.join(_A.dir, 'r.json')))
def num(v):
    if v is None: return None
    s = str(v).split('|')[0].strip().replace(',', '')
    if s in ('', '-', '--', 'None'): return None
    m = 1
    for u, f in (('万亿', 1e12), ('亿', 1e8), ('万', 1e4)):
        if s.endswith(u): s = s[:-len(u)]; m = f; break
    try: return float(s) * m
    except: return None
def pick(x, pat):
    for k, v in x.items():
        if re.search(pat, k): return v
    return None
stocks = {}; cover = {}
for key, rec in R.items():
    if key.startswith('DIV') : continue
    rs = rec['raw']['data']['data']['allResults']['result']
    conds = rec['raw']['data']['data'].get('responseConditionList') or []
    ind = key.split('|')[1] if key.startswith('FIN') else key.split('|')[0]
    for c in conds:
        if '申万行业分类包含' in (c.get('describe') or ''): cover.setdefault(ind, c.get('stockCount'))
    for x in rs.get('dataList') or []:
        sw = x.get('SW_INDUSTRY') or ''
        board = pick(x, r'TRADEMARKET_TRADEMARKET') or ''
        if '科创' in board or '北京' in board or '北交' in board: continue
        d = dict(code=x['SECURITY_CODE'], name=x['SECURITY_SHORT_NAME'], sw=sw, sw1=sw.split('-')[0], board=board,
                 price=num(x.get('NEWEST_PRICE')),
                 roe25=num(pick(x, r'ROEJQ\{%d-12-31' % _A.fy)), roe24=num(pick(x, r'ROEJQ\{%d-12-31' % (_A.fy - 1))),
                 roe23=num(pick(x, r'ROEJQ\{%d-12-31' % (_A.fy - 2))), roeL=num(pick(x, r'ROEJQ\{%s' % _A.asof)),
                 cagr3=num(pick(x, r'TOTALOPERATEREVE_N_3\{%d-12-31' % _A.fy)), rev_yoy=num(pick(x, r'^营业总收入最新同比')),
                 np_yoy=num(pick(x, r'^扣除非经常性损益.*同比')), gm=num(pick(x, r'XSMLL')), debt=num(pick(x, r'ZCFZL')),
                 ocf=num(pick(x, r'NETOPERATECASHFLOW')), pe=num(pick(x, r'PETTM<70>')), pb=num(pick(x, r'^010000_PB<70>\{%s' % _A.px)),
                 pe_pct=num(pick(x, r'PETTM_PERCENTILE_5Y')), pb_pct=num(pick(x, r'^市净率区间百分位')),
                 mv=num(pick(x, r'TOAL_MARKET_VALUE')), amt=num(pick(x, r'TRADING_VOLUMES')))
        kf = [(k, num(v)) for k, v in x.items() if k.startswith('扣除非经常性损益后归属于母公司股东的净利润(元)') or 'KCFJCXSYJLR' in k]
        d['kf_h1'] = None
        for k, v in x.items():
            if re.search(r'\{%s\}' % _A.h1, k) and str(v).endswith('半年报') and ('扣除' in k or 'KCF' in k or 'DEDUCT' in k.upper()):
                d['kf_h1'] = num(v)
        d['_keys'] = None
        stocks[d['code']] = {**stocks.get(d['code'], {}), **{k: v for k, v in d.items() if v is not None or k not in stocks.get(d['code'], {})}}
# dividends / NPL
for key, rec in R.items():
    if not key.startswith('DIV'): continue
    for x in rec['raw']['data']['data']['allResults']['result'].get('dataList') or []:
        c = x['SECURITY_CODE']
        if c not in stocks: continue
        dy = num(pick(x, r'DIVIDENDYIELD'))
        if dy is not None: stocks[c]['dy'] = dy
        npl = num(pick(x, r'NPL_col_NPL')); pcr = num(pick(x, r'BLDKBBL'))
        if npl is not None: stocks[c]['npl'] = npl
        if pcr is not None: stocks[c]['pcr'] = pcr
json.dump(stocks, open(os.path.join(_A.dir, 'stocks.json'), 'w'), ensure_ascii=False)
from collections import Counter
cnt = Counter(s['sw1'] for s in stocks.values())
print('total', len(stocks))
for k in sorted(cover, key=lambda k: -cover[k]): print(f"{k:6s} 申万含={cover[k]:4d} 实得={cnt.get(k,0):4d}")
print('其他sw1:', {k: v for k, v in cnt.items() if k not in cover})

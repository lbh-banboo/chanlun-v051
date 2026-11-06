#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 score_quality.py 的 result2.json（{行业: {top, watch, n_all…}}）压成 build_focus_pool.py 需要的快照 slim.json（[{ind, top, watch…}, …]）。

只保留下游用到的字段（体积从几百 KB 降到约 100 KB），并按行业样本数从大到小排序。不依赖上一版结果。
用法：python3 make_snapshot.py --dir DIR [--out slim.json]
"""
import os
import json
import argparse

KEEP = ['code', 'name', 'sw', 'price', 'list_date', 'roe_avg', 'roe_min', 'roe25', 'roe24', 'roe23', 'roe_years', 'cagr3', 'rev_yoy', 'np_yoy',
        'gm', 'debt', 'ocf_r25', 'ocf_r_h1', 'pe', 'pe_kf', 'pe_pct', 'pb', 'pb_pct', 'pe_pct_ok', 'pb_pct_ok', 'mv', 'dy', 'npl', 'pcr',
        's_prof', 's_grow', 's_qual', 's_val', 'score', 'red', 'yel', 'rank', 'tier', 'fill_why', 'nr_share']


def slim(s):
    return {k: (round(s[k], 2) if isinstance(s.get(k), float) else s.get(k)) for k in KEEP}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--dir', default='.'); ap.add_argument('--out', default='slim.json')
    a = ap.parse_args()
    R = json.load(open(os.path.join(a.dir, 'result2.json'), encoding='utf-8'))
    out = [dict(ind=ind, n_all=o['n_all'], n_pass=o['n_pass'], n_elig=o['n_elig'], n_red=o['n_red'], med=o['med'],
                top=[slim(s) for s in o['top']], watch=[slim(s) for s in o['watch']]) for ind, o in R.items()]
    out.sort(key=lambda x: -x['n_all'])
    json.dump(out, open(os.path.join(a.dir, a.out), 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    n5 = sum(min(5, len(x['top'])) for x in out)
    print('%d 个行业，前五合计 %d 只 → %s' % (len(out), n5, os.path.join(a.dir, a.out)))


if __name__ == '__main__':
    main()

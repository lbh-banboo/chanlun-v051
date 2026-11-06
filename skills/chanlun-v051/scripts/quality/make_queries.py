#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成质地榜的 stock-screen 问句集（q.json 主表 + q2.json 补充），键与问句保持固定；改动问句等于换口径，须重新取数。

主表 q.json（113 句）：
  · 非金融 29 个行业（申万一级，不含银行/非银金融/…）× 上市板块（上交所主板/深交所主板/创业板；科创板、北交所排除），行业按每板块拆开避免超 200 行
    问：ROE（近三年+最新）、近三年营收复合增速、营收同比、扣非同比、毛利率、负债率、经营现金流、PE、PB、PE/PB 五年分位、总市值
  · FIN|银行、FIN|非银金融：不问毛利率/现金流（金融股返回 0 只），多问 ROE/PE/PB
  · DIV|行业|板块：股息率；DIV|银行|NPL：不良贷款率、拨备覆盖率
补充 q2.json（43 句）：S2|行业[|板块]：最近两个完整财年经营现金流、最近完整财年营收/扣非同比（打分器的"现金质量"要用全年而非半年）
财年与年份用参数；上市时间大于 1 年写死（次新股另由打分器的 ROE-上市后年度规则处理）。
用法：python3 make_queries.py --dir DIR [--fy 2025] [--industries 行业1 行业2 ...]
"""
import json, os, argparse

ap = argparse.ArgumentParser()
ap.add_argument('--dir', default='.'); ap.add_argument('--fy', type=int, default=2025)
ap.add_argument('--industries', nargs='*', help='只生成这些行业（默认全部 31 个申万一级）')
a = ap.parse_args()
FY = a.fy
ALL31 = ['农林牧渔', '基础化工', '钢铁', '有色金属', '电子', '家用电器', '食品饮料', '纺织服饰', '轻工制造', '医药生物', '公用事业', '交通运输', '房地产',
         '商贸零售', '社会服务', '综合', '建筑材料', '建筑装饰', '电力设备', '国防军工', '计算机', '传媒', '通信', '银行', '非银金融', '汽车', '机械设备',
         '石油石化', '煤炭', '环保', '美容护理']
FIN = ['银行', '非银金融']
BIG = {'机械设备', '电子', '医药生物', '计算机', '电力设备', '基础化工', '汽车'}          # 单行业 >200 只：按板块拆
BOARDS = ['上交所主板', '深交所主板', '创业板']
inds = a.industries or ALL31

FIELDS_MAIN = ('{y0}年净资产收益率 {y1}年净资产收益率 {y2}年净资产收益率 净资产收益率ROE 近三年营收复合增长率 营业总收入同比增长率 扣非归母净利润同比增长率 '
               '销售毛利率 资产负债率 经营活动现金流净额 市盈率TTM 市净率 市盈率5年百分位 市净率近5年百分位 总市值').format(y0=FY, y1=FY - 1, y2=FY - 2)
FIELDS_FIN = ('{y0}年净资产收益率 {y1}年净资产收益率 {y2}年净资产收益率 净资产收益率ROE 营业总收入同比增长率 扣非归母净利润同比增长率 '
              '市盈率TTM 市净率 市盈率5年百分位 市净率近5年百分位 总市值').format(y0=FY, y1=FY - 1, y2=FY - 2)
q, q2 = {}, {}
for ind in inds:
    if ind in FIN:
        q['FIN|' + ind] = 'A股 非ST 申万一级行业%s 上市时间大于1年 %s' % (ind, FIELDS_FIN)
    elif ind in BIG or True:
        for b in BOARDS:
            q['%s|%s' % (ind, b)] = 'A股 非ST 申万一级行业%s 上市板块%s 上市时间大于1年 %s' % (ind, b, FIELDS_MAIN)
for ind in inds:
    if ind in ('银行', '煤炭', '公用事业', '石油石化', '交通运输', '家用电器', '食品饮料', '非银金融'):          # 高股息行业单问股息率
        for b in (BOARDS[:2] if ind == '银行' else BOARDS):
            q['DIV|%s|%s' % (ind, b)] = 'A股 非ST 申万一级行业%s 上市板块%s 上市时间大于1年 年度股息率' % (ind, b)
if '银行' in inds:
    q['DIV|银行|NPL'] = 'A股 非ST 申万一级行业银行 不良贷款率 不良贷款拨备覆盖率'
F2 = '{y0}年经营活动产生的现金流量净额 {y1}年经营活动产生的现金流量净额 {y0}年营业总收入同比增长率 {y0}年扣非归母净利润同比增长率'.format(y0=FY, y1=FY - 1)
for ind in inds:
    if ind in FIN:
        continue
    if ind in BIG:
        for b in BOARDS:
            q2['S2|%s|%s' % (ind, b)] = 'A股 非ST 申万一级行业%s 上市板块%s 上市时间大于1年 %s' % (ind, b, F2)
    else:
        q2['S2|' + ind] = 'A股 非ST 非科创板 非北交所 申万一级行业%s 上市时间大于1年 %s' % (ind, F2)
json.dump(q, open(os.path.join(a.dir, 'q.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=0)
json.dump(q2, open(os.path.join(a.dir, 'q2.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=0)
print('q.json %d 句，q2.json %d 句 → %s' % (len(q), len(q2), a.dir))

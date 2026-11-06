#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""东方财富「妙想」stock-screen 批量取数客户端（幂等续跑）。

  输入  q.json  {"键": "自然语言问句"}      （由 make_queries.py 生成）
  输出  r.json  {"键": {"ok","raw","q"}}    已成功且问句没变的键会跳过，所以被限频/超时打断后直接重跑即可

密钥：环境变量 MX_APIKEY（必需，向妙想申请；**不要写进仓库、不要提交**）。
限频：每次请求间隔 GAP 秒（默认 6.5），单次运行最长 WALL 秒（默认 38）；需要多跑几次直到「剩余 0」。
已知坑：
  · 单次最多返回 200 行 → 大行业必须按「行业 × 上市板块」拆开问（make_queries.py 已拆）；
  · **问句里每个字段都是交集条件**：带"股息率"会把不分红的票筛掉；带"销售毛利率/经营现金流"会让银行/券商/保险返回 0 只
    → 金融（FIN|）、股息（DIV|）单独问；
  · 38 秒墙钟 ≈ 5 句，全套 113+43 句要跑好几轮；
  · 返回里的 PE 分位是按归母 PE 算的，次新股样本短，打分器会处理（score_quality.py）。
用法：MX_APIKEY=xxx python3 mx_screen_client.py --dir DIR [--q q.json] [--r r.json]
"""
import os, io, json, time, ssl, subprocess, argparse, urllib.request

URL = 'https://mkapi2.dfcfs.com/finskillshub/api/claw/stock-screen'
GAP = float(os.environ.get('MX_GAP', '6.5'))
WALL = float(os.environ.get('MX_WALL', '38'))


def ctxs():
    yield ssl.create_default_context()
    for p in ('/etc/ssl/cert.pem',):
        if os.path.exists(p):
            yield ssl.create_default_context(cafile=p)


def ask(q, key):
    body = json.dumps({'keyword': q}).encode('utf-8'); last = None
    for c in ctxs():
        try:
            req = urllib.request.Request(URL, data=body, method='POST', headers={'Content-Type': 'application/json', 'apikey': key})
            with urllib.request.urlopen(req, timeout=28, context=c) as r:
                return json.loads(r.read().decode('utf-8'))
        except Exception as e:                                   # noqa
            last = e
    # 兜底：系统 curl（把密钥从 stdin 传入，避免出现在进程列表里）
    p = subprocess.run(['/usr/bin/curl', '-sS', '--max-time', '28', '-H', 'Content-Type: application/json', '-H', '@-', '-X', 'POST',
                        '--data-binary', json.dumps({'keyword': q}), URL], input='apikey: %s' % key, capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError('%s / curl %s' % (last, p.stderr[:100]))
    return json.loads(p.stdout)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', default='.'); ap.add_argument('--q', default='q.json'); ap.add_argument('--r', default='r.json')
    a = ap.parse_args()
    key = (os.environ.get('MX_APIKEY') or '').strip()
    if not key:
        raise SystemExit('缺少环境变量 MX_APIKEY（妙想 API 密钥）。密钥不要写进仓库。')
    qf, rf = os.path.join(a.dir, a.q), os.path.join(a.dir, a.r)
    qs = json.load(io.open(qf, encoding='utf-8'))
    res = json.load(io.open(rf, encoding='utf-8')) if os.path.exists(rf) else {}
    t0 = time.time(); n = 0
    for k, q in qs.items():
        if res.get(k, {}).get('ok') and res[k].get('q') == q:
            continue
        if time.time() - t0 > WALL:
            print('WALL用尽'); break
        if n:
            time.sleep(GAP)
        n += 1
        try:
            r = ask(q, key); rs = r['data']['data']['allResults']['result']
            ok = r.get('status') == 0 and rs.get('total') is not None
            tot, rows = rs.get('total'), len(rs.get('dataList') or [])
        except Exception as e:                                   # noqa
            r, ok, tot, rows = str(e)[:200], False, None, 0
        res[k] = {'ok': ok, 'raw': r, 'q': q}
        json.dump(res, io.open(rf, 'w', encoding='utf-8'), ensure_ascii=False)
        print('%s %s total=%s rows=%s' % ('OK ' if ok else 'BAD', k, tot, rows))
    left = [k for k in qs if not (res.get(k, {}).get('ok') and res[k].get('q') == qs[k])]
    print('剩余', len(left), left[:10], '%.0fs' % (time.time() - t0))


if __name__ == '__main__':
    main()

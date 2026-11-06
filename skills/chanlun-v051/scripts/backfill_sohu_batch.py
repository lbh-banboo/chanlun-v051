#!/usr/bin/env python3
"""批量补日线（搜狐一次 40 码）：把 data_live 里 最后一日 < TARGET 的个股补到 TARGET（含）。
口径同 c_update_host.py 的"漏更补齐"：搜狐不复权行；前收 = close - chg；与文件最后一日 raw_close 不等 → 除权除息，整段历史按比例重算。
不碰 c_update_log.json；只追加 data/daily/*.csv。幂等：已到 TARGET 的跳过。
环境变量：TARGET（默认＝最近一个已收盘交易日）、DRY=1 只演算不写、WALL（默认 42 秒）。
用法：python3 backfill_sohu_batch.py   （要联网；多跑几次直到"left"很小。每次约 50 秒，注意执行通道的单次超时）"""
import os, sys, json, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import daily_update as CU

TARGET = os.environ.get("TARGET") or CU.last_closed_day()      # 默认＝最近一个已收盘交易日
DRY = os.environ.get("DRY") == "1"
WALL = float(os.environ.get("WALL", "42"))
BATCH = 40
DATA = CU.DATA
LOGF = os.path.join(CU.P.META, "backfill_batch_log.json")

def multi(codes, start, end):
    url = CU.SOHU % (",".join("cn_" + c[:6] for c in codes), start, end)
    js = CU.get(url, {"User-Agent": CU.UA}).json()
    out = {}
    for it in js if isinstance(js, list) else []:
        code6 = str(it.get("code", ""))[3:9]
        rows = []
        for r in it.get("hq", []):
            o, c, chg, pct, lo, hi, vol, amt, turn = [CU.fnum(x) for x in r[1:10]]
            if o and c and o > 0 and c > 0:
                rows.append(dict(date=r[0], open=o, close=c, chg=chg, pct=pct, low=lo, high=hi, vol=vol,
                                 amount=(amt or 0) * 1e4, turn=turn))
        out[code6] = sorted(rows, key=lambda x: x["date"])
    return out

def main():
    t0 = time.time()
    todo = []
    for f in sorted(os.listdir(DATA)):
        if not f.endswith(".csv") or f.startswith("000300.SH"):
            continue
        last = CU.last_line(os.path.join(DATA, f))[0]
        if last < TARGET:
            todo.append((f[:-4], last))
    print("待补 %d 只（TARGET=%s%s）" % (len(todo), TARGET, "，DRY" if DRY else ""))
    n_app = n_ex = n_rows = n_empty = n_err = 0; batches = 0; sample = []
    end = TARGET.replace("-", "")
    for i in range(0, len(todo), BATCH):
        if time.time() - t0 > WALL:
            print("WALL 用尽"); break
        chunk = todo[i:i + BATCH]
        start = min(l for _, l in chunk).replace("-", "")
        try:
            got = multi([c for c, _ in chunk], start, end)
        except Exception as e:
            n_err += 1; print("批失败", repr(e)[:80]); time.sleep(2); continue
        batches += 1
        for code, last in chunk:
            rows = [r for r in got.get(code[:6], []) if last < r["date"] <= TARGET]
            if not rows:
                n_empty += 1; continue          # 停牌/退市/搜狐无数据
            p = os.path.join(DATA, code + ".csv")
            lraw = float(CU.last_line(p)[6])
            for r in rows:
                pre = r["close"] - (r["chg"] or 0)
                ratio = pre / lraw if lraw else 1.0
                if abs(ratio - 1.0) > 1e-6:
                    if not DRY: CU.rescale(p, ratio)
                    n_ex += 1
                if DRY and len(sample) < 4: sample.append((code, r["date"], r["close"], round(ratio, 5)))
                if not DRY:
                    CU.append(p, r["date"], r["open"], r["high"], r["low"], r["close"], r["vol"], r["amount"], r["turn"], r["pct"])
                lraw = r["close"]; n_rows += 1
            n_app += 1
        time.sleep(0.6)
    left = sum(1 for c, _ in todo if CU.last_line(os.path.join(DATA, c + ".csv"))[0] < TARGET)
    rec = dict(at=time.strftime("%Y-%m-%d %H:%M:%S"), target=TARGET, dry=DRY, batches=batches, stocks_filled=n_app,
               rows=n_rows, exdiv=n_ex, empty=n_empty, err=n_err, left=left, secs=round(time.time() - t0, 1))
    print(json.dumps(rec, ensure_ascii=False)); print("样例", sample)
    if not DRY:
        prev = json.load(open(LOGF)) if os.path.exists(LOGF) else []
        json.dump((prev + [rec])[-80:], open(LOGF, "w"), ensure_ascii=False)

if __name__ == "__main__":
    main()

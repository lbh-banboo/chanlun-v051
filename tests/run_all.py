#!/usr/bin/env python3
"""一键跑全部测试（不联网、不依赖任何真实行情文件，约 30 秒）。任何一项失败退出码非 0。

  0 依赖预检      numpy / pandas 缺失时直接给明确提示，不制造误导性的失败清单
  1 语法          所有 .py 能编译（纯内存，不落盘 .pyc）
  2 引擎截断/黄金   test_engine_truncation.py：无前视 + 固定种子黄金快照（规则被悄悄改会失败）
  3 引擎边界       test_engine_edges.py：三买 ZG 边界、线段奇偶性/短线段、成笔间隔、包含方向、账户层常量
  4 TRIN 插件      test_trin_health.py：合成小市场，逐项注入异常
  5 端到端         test_pipeline_synthetic.py：init → 日线 → 30 分钟 → 池 → 档位 → 扫描 → 账本不变量 → 池维护 → 任务渲染
  6 文档/仓库自检   test_repo_hygiene.py：SKILL.md frontmatter 合规、文档内链接都存在、无密钥/个人路径、占位符闭合

用法：python3 tests/run_all.py [--fast]    （--fast 跳过端到端）
"""
import os
import sys
import time
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
fast = "--fast" in sys.argv
steps = [("引擎截断 + 黄金快照", "test_engine_truncation.py"), ("引擎边界用例", "test_engine_edges.py"), ("TRIN 插件", "test_trin_health.py")]
if not fast:
    steps.append(("端到端（合成市场）", "test_pipeline_synthetic.py"))
steps.append(("仓库自检（frontmatter / 链接 / 脱敏）", "test_repo_hygiene.py"))

# 0 依赖预检
# 缺 pandas/numpy 时后面 5 项会各报各的错（ImportError、--help 探测失败……），
# 使用者只看到一堆 FAIL 却不知道根因只差一条 pip install。先给明确提示再退出。
missing = []
for mod in ("numpy", "pandas"):
    try:
        __import__(mod)
    except ImportError:
        missing.append(mod)
if missing:
    sys.exit("缺少依赖：%s\n先执行：python3 -m pip install pandas numpy\n"
             "（测试套件本身不联网，但合成行情计算需要这两个包）" % ", ".join(missing))

bad = 0
# 1 语法：用内置 compile() 纯内存检查，不再 `python -m py_compile` 落盘。
# 自检不该有副作用；只读 / 受保护目录里写 .pyc 会让 37 个文件全部假失败。
t0 = time.time(); n = 0; errs = []
for dp, dn, fn in os.walk(ROOT):
    dn[:] = [d for d in dn if d not in (".git", "__pycache__", "node_modules")]
    for f in fn:
        if f.endswith(".py"):
            n += 1
            fp = os.path.join(dp, f)
            try:
                compile(open(fp, encoding="utf-8").read(), fp, "exec")
            except (SyntaxError, UnicodeDecodeError, OSError, ValueError) as e:
                errs.append((f, str(e)[-200:]))
print("[1/%d] 语法：%d 个 .py，%s（%.1fs）" % (len(steps) + 1, n, "全部通过" if not errs else "失败 %s" % errs, time.time() - t0))
bad += bool(errs)
for i, (name, f) in enumerate(steps, 2):
    t0 = time.time()
    p = subprocess.run([sys.executable, os.path.join(HERE, f)], capture_output=True, text=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    last = (p.stdout.strip().splitlines() or ["(无输出)"])[-1]
    print("[%d/%d] %s：%s（%.1fs）" % (i, len(steps) + 1, name, "通过" if p.returncode == 0 else "失败", time.time() - t0))
    print("      " + last[:150])
    if p.returncode:
        bad += 1
        print("\n".join("      | " + l for l in (p.stdout + p.stderr).strip().splitlines()[-12:]))
print("\n" + ("全部通过" if not bad else "有 %d 项失败" % bad))
sys.exit(1 if bad else 0)

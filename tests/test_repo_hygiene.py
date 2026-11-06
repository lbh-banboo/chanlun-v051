#!/usr/bin/env python3
"""仓库自检（脱敏、结构、链接、占位符、示例数据）——公开发布前后都应该是绿的。

检查项
  1 SKILL.md        frontmatter 合规：name 与目录一致且符合命名规则、description 1—1024 字符、compatibility ≤500、有 license
  2 文档链接         所有 markdown 里的相对链接都指向存在的文件；references 里的 `[NN](NN-xxx.md)` 互链有效；脚本/路径引用（scripts/xxx.py）存在
  3 脚本引用         文档里提到的 `scripts/…py`、`tests/…py` 都真实存在；SKILL.md 里列的脚本名在目录里都有
  4 脱敏             通用形态扫描：密钥/令牌、个人绝对路径、私有知识库 ID、私人邮箱/手机/身份证、会话 UUID；
                      示例池命名契约（标的名须以「示例」开头）。本文件不保存任何作者相关具体词表
  5 占位符闭合       assets/scheduled-tasks 的 {{X}} 都在 README 的占位符表里有定义，反之亦然
  6 示例数据         示例池/配置能通过 pool_tool check；golden 快照文件存在
  7 仓库卫生         .gitignore 含 data/ live/ pipeline/ journal/ .env __pycache__；没有超过 1MB 的文件；没有 .pyc / .DS_Store
  8 LICENSE/免责     LICENSE 与 DISCLAIMER 存在（README 写完后才会有，缺失时提示）

用法：python3 tests/test_repo_hygiene.py [--strict]     --strict：把"提示"也当作失败
"""
import os
import re
import sys
import json
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
SK = os.path.join(ROOT, "skills", "chanlun-v051")
STRICT = "--strict" in sys.argv
FAILS, WARNS = [], []
N = 0


def check(cond, msg, warn=False):
    global N
    N += 1
    if not cond:
        (WARNS if warn else FAILS).append(msg)
        print("  %s: %s" % ("WARN" if warn else "FAIL", msg))


def files(exts=None, skip_dirs=(".git", "__pycache__", "node_modules")):
    for dp, dn, fn in os.walk(ROOT):
        dn[:] = [d for d in dn if d not in skip_dirs]
        for f in fn:
            if exts is None or f.endswith(exts):
                yield os.path.join(dp, f)


def rel(p):
    return os.path.relpath(p, ROOT)


def read(p):
    try:
        return open(p, encoding="utf-8").read()
    except UnicodeDecodeError:
        return ""


# ------------------------------------------------------------------ 1 SKILL.md
t = read(os.path.join(SK, "SKILL.md"))
m = re.match(r"---\n(.*?)\n---\n", t, re.S)
check(bool(m), "SKILL.md 缺少 YAML frontmatter")
if m:
    fm = m.group(1)
    get = lambda k: (re.search(r"^%s: (.*)$" % k, fm, re.M) or [None, None])[1]
    name, desc, comp = get("name"), get("description"), get("compatibility")
    check(bool(name) and re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name or "") is not None and len(name) <= 64, "name 须为小写字母数字与单连字符、≤64：%r" % name)
    check(name == os.path.basename(SK), "name（%s）必须与目录名（%s）一致" % (name, os.path.basename(SK)))
    check(bool(desc) and 1 <= len(desc) <= 1024, "description 须 1—1024 字符，当前 %d" % len(desc or ""))
    check(comp is None or len(comp) <= 500, "compatibility ≤500 字符")
    check(get("license") is not None, "frontmatter 应声明 license")
    body_lines = len(t.splitlines())
    check(body_lines < 500, "SKILL.md 建议 <500 行，当前 %d" % body_lines, warn=True)
    check("when" in (desc or "").lower() or "使用" in (desc or ""), "description 应写明何时使用（触发词/场景）")

# ------------------------------------------------------------------ 2 & 3 链接与脚本引用
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
mds = list(files((".md",)))
broken = []
for p in mds:
    txt = read(p)
    txt_nocode = re.sub(r"```.*?```", "", txt, flags=re.S)
    for href in LINK.findall(txt_nocode):
        if href.startswith(("http://", "https://", "mailto:", "#")):
            continue
        target = href.split("#")[0]
        if not target:
            continue
        full = os.path.normpath(os.path.join(os.path.dirname(p), target))
        if not os.path.exists(full):
            broken.append((rel(p), href))
check(not broken, "文档里有 %d 个失效的相对链接，例：%s" % (len(broken), broken[:5]))

REF = re.compile(r"`((?:scripts|tests|assets|references)/[A-Za-z0-9_./-]+\.(?:py|md|json|example|sh))`")
missing = []
for p in mds:
    for ref in REF.findall(read(p)):
        cand = [os.path.join(SK, ref), os.path.join(ROOT, ref), os.path.join(os.path.dirname(p), ref)]
        if not any(os.path.exists(c) for c in cand):
            missing.append((rel(p), ref))
# 下面这些是"使用者运行后才有的产物"，文档里提到但仓库里本来就没有
ALLOWED_MISSING = {"assets/pools/focus.md", "assets/pools/focus.json"}
missing = [x for x in missing if x[1] not in ALLOWED_MISSING]
check(not missing, "文档里引用了不存在的文件 %d 处，例：%s" % (len(missing), missing[:6]))

# SKILL.md 脚本清单里的每个文件名
sk = read(os.path.join(SK, "SKILL.md"))
names = set(re.findall(r"\b([a-z_0-9]+\.py)\b", sk))
have = {os.path.basename(p) for p in files((".py",))}
absent = sorted(n for n in names if n not in have)
check(not absent, "SKILL.md 提到的脚本不存在：%s" % absent)

# 文档里的命令行：`python3 <脚本>.py [子命令] --参数…` 的每个 --参数都必须是该脚本真实接受的参数。
# 自检**绝不能有副作用**（不联网、不写文件、不执行主流程）：
#   · 有 argparse 的脚本：用 `--help` 探测（argparse 在解析 --help 时就退出，不会进入主流程）；
#   · 没有 argparse 的脚本（daily_update.py 等，直接执行主流程）：绝不运行；它们不接受任何命令行参数，文档里给它们写 --参数 就是错的。
#   · --help 探测自身失败（依赖缺失 / 超时）单独记 FAIL 并说明原因，绝不静默当成"脚本不接受该参数"。
import subprocess
CMD = re.compile(r"python3?\s+((?:\$S|\$\{S\}|scripts|skills/chanlun-v051/scripts|\{\{SKILL\}\})/[\w./-]+?\.py)((?:[ \t]+[^\n`|&;]*)?)")
cmd_bad = []; cmd_n = 0; help_cache = {}
for p in mds:
    for mm in CMD.finditer(read(p)):
        tok = mm.group(1).replace("$S", "scripts").replace("${S}", "scripts").replace("{{SKILL}}", "scripts")
        script = os.path.join(SK, tok) if tok.startswith("scripts/") else os.path.join(ROOT, tok)
        if not os.path.exists(script):
            cmd_bad.append((rel(p), tok, "脚本不存在"))                    # 绝不静默跳过：跳过会让整条校验悄悄失效
            continue
        own = re.split(r"\s--(?:\s|$)", mm.group(2), maxsplit=1)[0]     # `bg_run.py NAME -- <被包装的命令>`：" -- " 之后属于被包装的命令，不属于本脚本
        opts = re.findall(r"(?<![\w-])(--[a-z][a-z0-9-]*)", own)
        if not opts:
            cmd_n += 1; continue
        src = read(script)
        if "argparse" not in src:
            cmd_n += 1
            cmd_bad += [(rel(p), os.path.basename(script), o + "（该脚本不接受命令行参数）") for o in opts if o != "--"]
            continue
        args = own.split()
        sub = args[0] if args and not args[0].startswith("-") and re.fullmatch(r"[a-z][a-z-]*", args[0]) else None
        key = (script, sub)
        if key not in help_cache:
            try:
                r = subprocess.run([sys.executable, script] + ([sub] if sub else []) + ["--help"],
                                   capture_output=True, text=True, timeout=30,
                                   env=dict(os.environ, V51_HOME=os.path.join(tempfile.gettempdir(), "v51_help_probe"),
                                            PYTHONDONTWRITEBYTECODE="1"))
                text = r.stdout + r.stderr
                # 「探测失败」≠「探测成功但脚本不接受该参数」。早期版本只取 stdout 内容、
                # 不看 returncode：缺 numpy 时 --help 直接 ImportError，空输出被当成"参数不存在"，
                # 一次报出 5 个假 FAIL，整条判据在缺依赖环境里变成恒假式。
                ok = r.returncode == 0 and "usage:" in text.lower()
            except (subprocess.TimeoutExpired, OSError) as e:
                ok, text = False, repr(e)
            help_cache[key] = (ok, text)
        probed_ok, help_text = help_cache[key]
        cmd_n += 1
        if not probed_ok:
            cmd_bad.append((rel(p), os.path.basename(script) + (" " + sub if sub else ""),
                            "--help 探测失败（多半缺 pandas/numpy，先 pip install pandas numpy）：" + help_text.strip()[-120:]))
            continue
        for opt in opts:
            if opt not in help_text and opt != "--help":
                cmd_bad.append((rel(p), os.path.basename(script) + (" " + sub if sub else ""), opt))
check(not cmd_bad, "文档里的命令行用了脚本不接受的参数（核对 %d 条）：%s" % (cmd_n, cmd_bad[:5]))

# ------------------------------------------------------------------ 4 脱敏
# 纪律：**本文件不得保存任何作者相关的具体词表**（真实用户名、私有目录/工具名、
# 真实个股名单……）。公开仓库里写「不许出现在文档里的词」，等于把隐私清单公开一遍。
# 所以这里只保留通用形态；标的名改由下面的「示例池命名契约」检查，不依赖任何名单。
GENERIC_SECRET_PATTERNS = [
    (r"(?i)[A-Za-z0-9_]*(api[_-]?key|secret|token|passwd|password)[A-Za-z0-9_]*\s*[:=]\s*['\"][A-Za-z0-9_\-./+=]{16,}['\"]", "疑似密钥赋值"),
    (r"\bsk-[A-Za-z0-9]{20,}\b", "疑似 API 密钥"),
    (r"\b(?:ghp|gho|ghu|ghs)_[A-Za-z0-9]{30,}\b", "疑似 GitHub 令牌"),
    (r"\bAKIA[0-9A-Z]{16}\b", "疑似 AWS Access Key"),
    (r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", "私钥"),
    (r"\b[a-z]+_kb_[A-Za-z0-9]+\b|\bfolder_\d{10,}\b", "疑似私有知识库/文件夹 ID"),
    (r"[\w.+-]+@(?:qq|163|126|gmail|outlook|hotmail|foxmail)\.(?:com|cn)", "私人邮箱"),
    (r"(?<!\d)1[3-9]\d{9}(?!\d)", "疑似手机号"),
    (r"(?<!\d)\d{17}[\dXx](?!\d)", "疑似身份证号"),
    (r"/Users/(?!me(?:/|\b)|YOU(?:/|\b)|you(?:/|\b)|user(?:/|\b)|example(?:/|\b)|username(?:/|\b)|name(?:/|\b)|<)[A-Za-z0-9_.-]{2,}/", "个人绝对路径"),
    (r"[Cc]:\\Users\\[A-Za-z0-9_.-]+", "Windows 个人路径"),
    (r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b", "疑似会话/文件 UUID"),
]
hits = []
for p in files((".md", ".py", ".json", ".example", ".sh", ".yml", ".yaml", ".txt", ".toml", ".cfg", ".gitignore")):
    if os.path.basename(p) in ("golden_engine_events.json",):      # 数值夹具，非文档
        continue
    txt = read(p)
    for pat, why in GENERIC_SECRET_PATTERNS:
        for mm in re.finditer(pat, txt):
            hits.append((rel(p), why, mm.group(0)[:40]))
check(not hits, "脱敏扫描命中 %d 处，例：%s" % (len(hits), hits[:8]))

# 示例池命名契约：不依赖真实名单，只要求示例标的名以「示例」开头。
# 真实标的混进示例池时会报红；但仓库里不保存任何真实标的名。
pool_example = os.path.join(SK, "assets", "pools", "watchlist.example.md")
non_sample = []
if os.path.exists(pool_example):
    for ln in read(pool_example).splitlines():
        m = re.match(r"\|\s*([^|\s][^|]*?)\s*\|\s*(\d{6}\.(?:SH|SZ|BJ))\s*\|", ln)
        if m and not m.group(1).startswith("示例"):
            non_sample.append(m.group(1))
check(not non_sample, "示例池出现非「示例」开头的标的名：%s" % non_sample[:5])

# ------------------------------------------------------------------ 5 占位符闭合
tdir = os.path.join(SK, "assets", "scheduled-tasks")
if os.path.isdir(tdir):
    used = set()
    for f in os.listdir(tdir):
        if f.endswith(".md") and f != "README.md":
            used |= set(re.findall(r"\{\{(\w+)\}\}", read(os.path.join(tdir, f))))
    defined = set(re.findall(r"\| `\{\{(\w+)\}\}` \|", read(os.path.join(tdir, "README.md"))))
    check(used <= defined, "模板用了 README 没定义的占位符：%s" % sorted(used - defined))
    check(defined <= used | {"HOME", "SKILL", "PY", "ADDRESS"}, "README 定义了模板里没用到的占位符：%s" % sorted(defined - used), warn=True)

# ------------------------------------------------------------------ 6 示例数据
import subprocess, tempfile
tmp = tempfile.mkdtemp(prefix="v51_hyg_")
env = dict(os.environ, V51_HOME=tmp, PYTHONDONTWRITEBYTECODE="1")
r = subprocess.run([sys.executable, os.path.join(SK, "scripts", "init_workspace.py"), "--home", tmp, "--start", "2026-01-05"], capture_output=True, text=True, env=env)
cfgp = os.path.join(tmp, "config", "v51_config.json")
if os.path.exists(cfgp):
    c = json.load(open(cfgp)); c["m30_base_end"] = "2025-12-31"; json.dump(c, open(cfgp, "w"))
r = subprocess.run([sys.executable, os.path.join(SK, "scripts", "pool_tool.py"), "check"], capture_output=True, text=True, env=env)
check(r.returncode == 0 and "错误 0" in r.stdout and "提示 0" in r.stdout, "示例池应 0 错误 0 提示：%s" % r.stdout.strip()[-120:])
import shutil
shutil.rmtree(tmp, ignore_errors=True)
check(os.path.exists(os.path.join(HERE, "golden_engine_events.json")), "缺少 tests/golden_engine_events.json")

# ------------------------------------------------------------------ 7 仓库卫生
if os.path.exists(os.path.join(ROOT, ".gitignore")):
    gi = read(os.path.join(ROOT, ".gitignore"))
    for must in ("data/", "live/", "pipeline/", "journal/", ".env", "__pycache__"):
        check(must in gi, ".gitignore 应包含 %s（防止行情/账本/密钥被提交）" % must)
else:
    check(False, "缺少 .gitignore", warn=True)
big = [(rel(p), os.path.getsize(p)) for p in files() if os.path.getsize(p) > 1_000_000]
check(not big, "有超过 1MB 的文件：%s" % big[:3])
junk = [rel(p) for p in files() if p.endswith((".pyc", ".DS_Store")) or "/__pycache__/" in p]
check(not junk, "仓库里有缓存/杂物文件：%s" % junk[:3], warn=True)

# ------------------------------------------------------------------ 8 许可与免责
for fn, why in (("LICENSE", "开源许可"), ("DISCLAIMER.md", "免责声明"), ("README.md", "README")):
    check(os.path.exists(os.path.join(ROOT, fn)), "缺少 %s（%s）" % (fn, why), warn=True)

print("仓库自检 %d 项，失败 %d，提示 %d" % (N, len(FAILS), len(WARNS)))
if FAILS or (STRICT and WARNS):
    sys.exit(1)
print("通过：frontmatter、链接、脚本引用、脱敏、占位符、示例数据、仓库卫生均符合要求" + ("" if not WARNS else "（另有 %d 条提示）" % len(WARNS)))

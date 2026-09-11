#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分层记忆管理体系 · 健康巡检器 v1.2

v1.2 新增（ID 撞号检测，09-05）：
  - J 组 ID 唯一性：数据层条目行（`- [类型][状态] XX-NNN …`）按字母前缀分区查重，
    同前缀同号即 FAIL——撞号会令「引用带 ID」纪律产生歧义（实例：FACT M-007
    两条不同内容共号，存活十余轮升版才被全量体检发现）。

v1.1 新增（多窗口并发写防护，09-03）：
  - I 组 版本回退检测：磁盘当前签名版本 < history/ 快照最大版本 → 疑似被并行会话
    整文件覆盖，报警并提示从快照恢复；
  - --expect CAS 写前校验：写公共文件前校验磁盘签名版本 = 会话预期版本，
    不符即停（防"基于旧版本改、写回覆盖他人"的丢失更新）。

设计要点：
  - 所有检查都是纯函数，输入 = Ctx（文件内容字典 / 快照清单 / 日志日期清单），
    输出 = [(level, group, msg), ...]。这样才能做内存毒丸自检（不碰磁盘）。
  - --poison 模式：先构造全绿基线夹具跑一遍（证明检查器不是见谁咬谁），
    再逐组注入已知假失效，断言对应检查组必须报错（证明检查组不是橡皮图章）。
    详见 SKILL.md「健康巡检与毒丸自检」节。

用法：
  python memory_health_check.py --base .workbuddy/memory            # 全量巡检
  python memory_health_check.py --base <dir> --index <index.md>     # 启用 G2 反向映射
  python memory_health_check.py --base <dir> --expect RULE=3.18 STATUS=1.6 DATA=1.45
                                                                    # 写前 CAS 校验
  python memory_health_check.py --poison                            # 毒丸自检
退出码：0 = 无 FAIL（毒丸模式 = 全部捕获；CAS 模式 = 全部匹配）；1 = 有 FAIL / 有毒丸未捕获 / 版本不符
"""
import argparse
import datetime
import io
import os
import re
import sys

# ---------------------------------------------------------------- 配置区
# 文件名改过的话，同步改这里
F_RULE = 'MEMORY.md'
F_STATUS = 'STATUS.md'
F_DATA = 'DATA.md'
F_HISTORY = 'history'
# 工作索引（项目级 skill）：相对 base 的路径；文件不存在时 SKILL 相关检测全部静默跳过，
# 其他项目无此结构零影响（误报比漏报更坏，同 C 组哲学）
F_INDEX = 'SKILL.md'
F_INDEX_REL = '../skills/<your-index>/SKILL.md'

SIG = {
    'RULE': 'MEM-END',
    'STATUS': 'STATUS-END',
    'DATA': 'DATA-END',
    'INDEX': 'SKILL-END',
}
PLACEHOLDER_PAT = re.compile(r'\{(MEM|DATA|STA)_(CUR|NEW)\}')
DATE_RE = re.compile(r'\d{4}-\d{2}-\d{2}\.md')
SEC_RE = re.compile(r'§\s*(\d+(?:\.\d+)?)')
STEP_RE = re.compile(r'步骤\s*(\d+(?:\.\d+)?)')
# 跨文件「」标题引用：已知目标名 + 小节标题
QUOTE_NAME_RE = re.compile(r'([A-Za-z_][A-Za-z_0-9]{1,20}|[\u4e00-\u9fff]{2,8})\s*[「『]([^」』]{2,30})[」』]')
# 派生待办关键词：不在表内会漏报，加宽了会误报（「待定」曾命中文件名）
DERIVE_KW = ['同步启动', '同步进行', '尚未启动', '未启动', '须细化', '须设计', '待设计']
TERMINAL_STATUS = {'已完成', '已定稿', '背景', '已完结'}
# I 组：history/ 快照命名规范 {stem}_vX.Y_说明.md，快照版本必须 ≤ 磁盘当前版本
SNAP_VER_RE = re.compile(r'^(\w+?)_v(\d+\.\d+)')


def _ver(s):
    """版本字符串 → 可比较元组（'1.10' > '1.9'，浮点比较会算错）。"""
    return tuple(map(int, s.split('.')))


class Ctx(object):
    """检查输入的载体。真实跑 = 从磁盘收集；毒丸跑 = 手工构造/变异。"""

    def __init__(self, files=None, snapshots=None, logs=None, index=None, today=None):
        self.files = files or {}          # {文件名: 内容 or None}
        self.snapshots = snapshots or []  # [(文件名, 字节数), ...]
        self.logs = logs or []            # ['YYYY-MM-DD', ...] 已排序
        self.index = index                # 索引文件内容（可选，G2 用）
        self.today = today or datetime.date.today()


def read(p):
    try:
        with io.open(p, encoding='utf-8') as f:
            return f.read()
    except Exception:
        return None


# ---------------------------------------------------------------- A 版本号一致性
def check_versions(ctx):
    out = []
    m = ctx.files.get(F_RULE)
    d = ctx.files.get(F_DATA)
    if m is None or d is None:
        out.append(('FAIL', 'A', '%s / %s 读取失败' % (F_RULE, F_DATA)))
        return out

    vers = set(re.findall(r'^# .*%s v(\d+\.\d+)' % re.escape(F_RULE), m, re.M)) | \
           set(re.findall(r'^%s v(\d+\.\d+)' % SIG['RULE'], m, re.M))
    if len(vers) == 1:
        out.append(('OK', 'A', '%s 版本号唯一: v%s' % (F_RULE, vers.pop())))
    else:
        out.append(('FAIL', 'A', '%s 版本号不唯一: %s（标题行与 %s 必须同为 vX.YY）'
                    % (F_RULE, sorted(vers), SIG['RULE'])))

    s = ctx.files.get(F_STATUS)
    if s is None:
        out.append(('FAIL', 'A', '%s 读取失败' % F_STATUS))
    else:
        head = [l for l in s.split('\n')[:12] if l.startswith(SIG['STATUS'])]
        if not head:
            out.append(('FAIL', 'A', '%s 前 12 行未找到 %s 签名（签名必须在头部，'
                        '规避尾部静默截断先丢签名）' % (F_STATUS, SIG['STATUS'])))
        else:
            sv = set(re.findall(r'^%s v(\d+\.\d+)' % SIG['STATUS'], s, re.M))
            if len(sv) == 1:
                out.append(('OK', 'A', '%s 头部签名唯一: %s v%s' % (F_STATUS, SIG['STATUS'], sv.pop())))
            else:
                out.append(('FAIL', 'A', '%s 签名版本号不唯一: %s' % (F_STATUS, sorted(sv))))

    dv = set(re.findall(r'^# .*%s v(\d+\.\d+)' % re.escape(F_DATA), d, re.M)) | \
         set(re.findall(r'^- 版本: v(\d+\.\d+)', d, re.M)) | \
         set(re.findall(r'^%s v(\d+\.\d+)' % SIG['DATA'], d, re.M))
    if len(dv) == 1:
        out.append(('OK', 'A', '%s 版本号三处一致: v%s' % (F_DATA, dv.pop())))
    else:
        out.append(('FAIL', 'A', '%s 版本号不一致: %s（应为三处同步）' % (F_DATA, sorted(dv))))

    idx = ctx.files.get(F_INDEX)
    if idx is None:
        out.append(('OK', 'A', '%s 不存在，SKILL 签名检测跳过' % F_INDEX))
    else:
        head = [l for l in idx.split('\n')[:12] if l.startswith(SIG['INDEX'])]
        if not head:
            out.append(('FAIL', 'A', '%s 前 12 行未找到 %s 签名（头部纪律，同 %s）'
                        % (F_INDEX, SIG['INDEX'], F_STATUS)))
        else:
            iv = set(re.findall(r'^%s v(\d+\.\d+)' % SIG['INDEX'], idx, re.M))
            if len(iv) == 1:
                out.append(('OK', 'A', '%s 头部签名唯一: %s v%s' % (F_INDEX, SIG['INDEX'], iv.pop())))
            else:
                out.append(('FAIL', 'A', '%s 签名版本号不唯一: %s' % (F_INDEX, sorted(iv))))
    return out


# ---------------------------------------------------------------- B 占位符残留
def check_placeholders(ctx):
    out = []
    for name in (F_RULE, F_STATUS, F_DATA):
        s = ctx.files.get(name)
        if s is None:
            continue
        hits = PLACEHOLDER_PAT.findall(s)
        if hits:
            out.append(('FAIL', 'B', '%s 含未替换占位符 %s' % (name, sorted(set(hits)))))
        else:
            out.append(('OK', 'B', '%s 无占位符残留' % name))
    return out


# ---------------------------------------------------------------- C 引用完整性
def collect_anchors(text):
    secs, steps = set(), set()
    for line in text.split('\n'):
        m = re.match(r'^#{1,5}\s*§?\s*(\d+(?:\.\d+)?)[、.\s]', line)
        if m:
            secs.add(m.group(1))
        m3 = re.match(r'^#{1,5}\s*(\d+\.\d+)', line)
        if m3:
            secs.add(m3.group(1))
        s2 = re.match(r'^#{1,5}\s*步骤\s*(\d+(?:\.\d+)?)', line)
        if s2:
            steps.add(s2.group(1))
    return secs, steps


def _is_cross_file(line, pos):
    """引用点前 14 字符内出现别的文档名 → 跨文件引用，不拿本文档锚点比对。"""
    ctx = line[max(0, pos - 14):pos]
    return bool(re.search(r'(\.md|MEMORY|STATUS|DATA|notes/)', ctx))


def check_refs_same(ctx):
    out = []
    for name, s in sorted(ctx.files.items()):
        if s is None or DATE_RE.fullmatch(name):
            continue
        secs, steps = collect_anchors(s)
        if not secs and not steps:
            continue  # 该文档不使用编号体系，跳过（误报比漏报更坏）
        bad = []
        for i, line in enumerate(s.split('\n')):
            for pat, pool in ((SEC_RE, secs), (STEP_RE, steps)):
                if not pool:
                    continue
                for m in pat.finditer(line):
                    if m.group(1) not in pool and not _is_cross_file(line, m.start()):
                        bad.append((name, i + 1, m.group(0)))
        if bad:
            for nm, ln, frag in bad:
                out.append(('WARN', 'C', '%s L%d 引用 **%s** —— 本文档中不存在（承诺未落地？）'
                            % (nm, ln, frag)))
        else:
            out.append(('OK', 'C', '%s 同文件引用全部落地' % name))
    return out


def check_refs_cross(ctx):
    """跨文件「」标题引用：目标 = 主三件 + notes/ 下各文件的文件名（去扩展名）。
    只在源文件里点名了目标名时才检查——没点名的不猜。"""
    out = []
    targets = {}
    for stem in (F_RULE[:-3], F_STATUS[:-3], F_DATA[:-3]):
        targets[stem] = ctx.files.get(stem + '.md')
    for stem, s in ctx.files.items():
        if stem.startswith('notes/') and s:
            targets[stem.split('/', 1)[1]] = s

    for name, s in sorted(ctx.files.items()):
        if s is None or DATE_RE.fullmatch(name):
            continue
        for m in QUOTE_NAME_RE.finditer(s):
            key, title = m.group(1), m.group(2)
            if key not in targets:
                continue
            ts = targets[key]
            if ts is None:
                out.append(('WARN', 'C', '%s 引用「%s」的「%s」，但目标文件不存在'
                            % (name, key, title)))
            elif title.replace(' ', '') not in ts.replace(' ', ''):
                out.append(('WARN', 'C', '%s 引用「%s」的小节「%s」—— 目标文件中未找到'
                            '（承诺未落地）' % (name, key, title)))
    return out


def check_refs(ctx):
    return check_refs_same(ctx) + check_refs_cross(ctx)


# ---------------------------------------------------------------- D 快照
def check_snapshots(ctx):
    out = []
    if not ctx.snapshots:
        out.append(('FAIL', 'D', 'history/ 无任何快照（写前快照机制未生效）'))
        return out
    empty = [n for n, size in ctx.snapshots if size == 0]
    if empty:
        out.append(('FAIL', 'D', '存在空快照（回退会失败）: %s' % empty))
    else:
        out.append(('OK', 'D', '快照 %d 个，无空快照' % len(ctx.snapshots)))
    return out


# ---------------------------------------------------------------- E 待同步区
def check_pending(ctx):
    d = ctx.files.get(F_DATA)
    if d is None:
        return []
    m = re.search(r'待同步:\s*(\d+)\s*条', d)
    if not m:
        return [('WARN', 'E', '%s 未找到「待同步: N 条」字段' % F_DATA)]
    n = int(m.group(1))
    if n == 0:
        return [('OK', 'E', '待同步 0 条')]
    return [('WARN', 'E', '待同步 %d 条（开工前应先核销）' % n)]


# ---------------------------------------------------------------- F 日志连续性
def check_log(ctx):
    if not ctx.logs:
        return [('WARN', 'F', '未找到任何日志文件')]
    last = ctx.logs[-1]
    y, mo, dy = map(int, last.split('-'))
    gap = (ctx.today - datetime.date(y, mo, dy)).days
    if gap <= 3:
        return [('OK', 'F', '最近日志 %s（距今 %d 天）' % (last, gap))]
    return [('WARN', 'F', '最近日志 %s 距今 %d 天（>3 天，注意记忆断档）' % (last, gap))]


# ---------------------------------------------------------------- G 状态层健康
def check_status_health(ctx):
    out = []
    s = ctx.files.get(F_STATUS)
    if s is None:
        return [('WARN', 'G', '%s 不存在，跳过 G 组' % F_STATUS)]
    if '## 热层' in s:
        hot = s.split('## 热层')[1].split('## 温层')[0]
    else:
        hot = ''
    n_hot = len(re.findall(r'^- \*\*', hot, re.M))
    if n_hot == 0:
        out.append(('WARN', 'G', '状态层热层 0 条（全部降级/未登记？）'))
    elif n_hot > 10:
        out.append(('WARN', 'G', '状态层热层 %d 条（>10，须核对降级——温/冷层是压字段不是删行）' % n_hot))
    else:
        out.append(('OK', 'G', '状态层热层 %d 条（≤10）' % n_hot))

    # G2 反向映射：索引文件的非终态工作线必须登记在状态层（09-02 同款漏登记真实发作过）
    idx = ctx.index
    if idx and '## 工作清单' in idx:
        seg = idx.split('## 工作清单')[1].split('\n## ')[0]
        rows = re.findall(r'^\|\s*(\d+)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|', seg, re.M)
        alive = [(a, b, c) for a, b, c, d4 in rows if d4.strip() not in TERMINAL_STATUS]
        tracked = s.split('## 已闭环')[0]
        missing = []
        for num, name, st in alive:
            key = re.sub(r'[（(].*?[)）]', '', name).strip()[:4]
            if key and key not in tracked:
                missing.append((num, name, st))
        if missing:
            for num, name, st in missing:
                out.append(('WARN', 'G', '索引序%s「%s」状态=%s，但状态层未见——漏登记？'
                            % (num, name[:16], st)))
        else:
            out.append(('OK', 'G', 'G2 反向映射：索引非终态 %d 项全部登记' % len(alive)))
    return out


# ---------------------------------------------------------------- H 派生待办
def check_decision_derivatives(ctx):
    out = []
    d = ctx.files.get(F_DATA)
    s = ctx.files.get(F_STATUS)
    if d is None or s is None:
        return out
    hits = []
    for i, line in enumerate(d.split('\n'), 1):
        if '[✅]' not in line:
            continue
        kws = [k for k in DERIVE_KW if k in line]
        if not kws:
            continue
        m = re.search(r'\b([A-Z]{1,4}-\d{1,3})\b', line)
        fid = m.group(1) if m else '(无ID)'
        hits.append((fid, i, kws))
    if not hits:
        return [('OK', 'H', '数据层 [✅] 条目无派生关键词，H 组无对象')]
    missing = [(f, i, k) for f, i, k in hits if f not in s]
    if missing:
        for fid, i, kws in missing:
            out.append(('WARN', 'H', '%s L%d %s 为 [✅] 决策，但含派生动作词 %s，'
                        '状态层未见该 ID——派生待办可能漏登记' % (F_DATA, i, fid, '/'.join(kws))))
    else:
        out.append(('OK', 'H', '%d 条派生词条的 [✅] 决策，ID 均已登记' % len(hits)))
    return out


# ---------------------------------------------------------------- I 版本回退检测
def check_rollback(ctx):
    """磁盘当前签名版本 < history/ 快照最大版本 → 疑似被并行会话整文件覆盖。
    只信签名行，不信标题（标题也是签名纪律管辖的三处之一，A 组已管一致性）。"""
    out = []
    cur = {}
    for key, fname, sig in (('MEMORY', F_RULE, SIG['RULE']),
                            ('STATUS', F_STATUS, SIG['STATUS']),
                            ('DATA', F_DATA, SIG['DATA']),
                            ('SKILL', F_INDEX, SIG['INDEX'])):
        s = ctx.files.get(fname)
        if not s:
            continue
        m = re.search(r'^%s v(\d+\.\d+)' % re.escape(sig), s, re.M)
        if m:
            cur[key] = m.group(1)
    if not cur:
        return [('WARN', 'I', '核心文件（含 SKILL，若存在）均未提取到签名版本，I 组无对象')]
    snap_max = {}
    for name, _size in ctx.snapshots:
        m = SNAP_VER_RE.match(name)
        if not m:
            continue
        key = m.group(1).upper()
        if key in cur:
            v = m.group(2)
            if key not in snap_max or _ver(v) > _ver(snap_max[key]):
                snap_max[key] = v
    rolled = [(k, cur[k], snap_max[k]) for k in sorted(snap_max)
              if _ver(snap_max[k]) > _ver(cur[k])]
    if rolled:
        for k, c, sm in rolled:
            out.append(('FAIL', 'I', '%s 磁盘版本 v%s < 快照最大版本 v%s —— 疑似被并行'
                        '会话覆盖回退，核对后从 history/ 恢复' % (k, c, sm)))
    else:
        out.append(('OK', 'I', '版本回退检测：%s，快照最大版本均不高于磁盘当前版本'
                    % '/'.join('%s v%s' % (k, v) for k, v in sorted(cur.items()))))
    return out


# ---------------------------------------------------------------- J ID 唯一性
# 条目行格式：`- [类型][状态] XX-NNN 标题…`；字母前缀=分区（G/O/M/DRF…），
# 同前缀内 ID 必须唯一（不同前缀各管各的号段，互不约束）。
ID_ENTRY_RE = re.compile(r'^- \[[^\]]+\]\[[^\]]+\]\s*([A-Z]{1,4}-\d{1,3})\b')


def check_id_uniqueness(ctx):
    out = []
    d = ctx.files.get(F_DATA)
    if d is None:
        return [('WARN', 'J', '%s 读取失败，J 组跳过' % F_DATA)]
    seen = {}
    for i, line in enumerate(d.split('\n'), 1):
        m = ID_ENTRY_RE.match(line)
        if m:
            seen.setdefault(m.group(1), []).append(i)
    dups = {fid: lns for fid, lns in sorted(seen.items()) if len(lns) > 1}
    if dups:
        for fid, lns in dups.items():
            out.append(('FAIL', 'J', '%s 撞号：出现于 L%s —— 引用歧义，保留先登记者，'
                        '后登记者改号（当前 max+1）' % (fid, '/'.join(map(str, lns)))))
    else:
        out.append(('OK', 'J', 'ID 唯一性：%d 个条目 ID 无重复' % len(seen)))
    return out


# ---------------------------------------------------------------- 汇总
CHECKS = [check_versions, check_placeholders, check_refs, check_snapshots,
          check_pending, check_log, check_status_health, check_decision_derivatives,
          check_rollback, check_id_uniqueness]


def run_checks(ctx):
    results = []
    for fn in CHECKS:
        results.extend(fn(ctx))
    return results


def report(results):
    for level, group, msg in results:
        print('  [%s] %s  %s' % (level.ljust(4), group, msg))
    fails = [r for r in results if r[0] == 'FAIL']
    warns = [r for r in results if r[0] == 'WARN']
    print('\nFAIL %d 条 / WARN %d 条' % (len(fails), len(warns)))
    if fails:
        print('结论: 存在 FAIL，须处理')
    elif warns:
        print('结论: 无 FAIL，但有 WARN 需人工判断')
    else:
        print('结论: 全绿')
    print('=' * 66)
    return 1 if fails else 0


# ---------------------------------------------------------------- 真实模式
def load_ctx(base, index_path=None):
    files = {}
    for fn in (F_RULE, F_STATUS, F_DATA):
        files[fn] = read(os.path.join(base, fn))
    # 工作索引 SKILL.md（不在 base 下，相对路径可达才读；读不到保持 None → 相关检测跳过）
    files[F_INDEX] = read(os.path.normpath(os.path.join(base, F_INDEX_REL)))
    if os.path.isdir(base):
        for fn in sorted(os.listdir(base)):
            if fn.endswith('.md') and not DATE_RE.fullmatch(fn) and fn not in files:
                files[fn] = read(os.path.join(base, fn))
    ndir = os.path.join(base, 'notes')
    if os.path.isdir(ndir):
        for fn in sorted(os.listdir(ndir)):
            if fn.endswith('.md'):
                files['notes/' + fn] = read(os.path.join(ndir, fn))

    hdir = os.path.join(base, F_HISTORY)
    snapshots = []
    if os.path.isdir(hdir):
        for fn in sorted(os.listdir(hdir)):
            p = os.path.join(hdir, fn)
            if os.path.isfile(p):
                snapshots.append((fn, os.path.getsize(p)))

    logs = sorted(fn[:-3] for fn in os.listdir(base)
                  if DATE_RE.fullmatch(fn)) if os.path.isdir(base) else []

    index = read(index_path) if index_path else None
    return Ctx(files=files, snapshots=snapshots, logs=logs, index=index)


# ---------------------------------------------------------------- 毒丸模式
def green_fixture():
    """全绿基线夹具（合成内容，无任何真实项目信息）。"""
    today = datetime.date.today()
    memory = ('# 项目记忆 MEMORY.md v1.0（规则层）\n\n## 一、背景\n- 示例背景\n\n'
              '## 二、规则\n- 示例规则\n\n## 五、历史锚点\n- 见 history/\n\n'
              'MEM-END v1.0\n')
    status = ('# STATUS.md 状态层 v1.0\n\nSTATUS-END v1.0\n\n## 热层\n\n'
              '- **示例工作线**［示例］最后 %s\n  - 进度：示例进度\n  - 下一步：示例下一步\n'
              '  - 卡点：无\n\n## 温层\n\n（空）\n\n## 冷层\n\n（空）\n\n'
              '## 待确认清单\n\n（无）\n\n## 已闭环\n\n- ✅ 示例闭环\n' % today.isoformat())
    data = ('# DATA.md 事实登记表 v1.0\n\n## §0 元信息\n'
            '- 版本: v1.0 | 最后更新: %s | 待同步: 0 条\n\n## §1 待同步区\n（空）\n\n'
            '## §2 示例区\n- [示例][✅] X-001 示例事实条目 | - | - | %s\n\n'
            'DATA-END v1.0\n' % (today.isoformat(), today.isoformat()))
    skill = ('---\nname: work-index\n---\n\n# 工作索引\n\nSKILL-END v1.0\n\n'
             '## 工作清单\n\n| 序 | 工作线 | 唤醒词 | 状态 | 续作点 | 产物 |\n'
             '|---|---|---|---|---|---|\n| 1 | 示例线 | 示例 | 进行中 | 示例下一步 | 示例产物 |\n')
    files = {F_RULE: memory, F_STATUS: status, F_DATA: data, F_INDEX: skill}
    snapshots = [('snapshot_v1.md', 120),
                 ('MEMORY_v0.1_基线快照.md', 100),
                 ('DATA_v0.1_基线快照.md', 100),
                 ('SKILL_v0.1_基线快照.md', 100)]
    logs = [today.isoformat()]
    return Ctx(files=files, snapshots=snapshots, logs=logs, index=None, today=today)


POISONS = [
    ('版本号不唯一', F_RULE, lambda s: s.replace('MEM-END v1.0', 'MEM-END v0.9', 1), 'A'),
    ('占位符残留', F_RULE, lambda s: s + '\n引用 {MEM_CUR}\n', 'B'),
    ('悬空引用', F_DATA, lambda s: s.replace('示例事实条目', '示例事实条目（见 §9）', 1), 'C'),
    ('空快照', None, None, 'D'),      # 变异走快照清单，不走文件内容
    ('待同步未核销', F_DATA, lambda s: s.replace('待同步: 0 条', '待同步: 2 条', 1), 'E'),
    ('日志断档', None, None, 'F'),    # 变异走日志清单
    ('热层超限', F_STATUS, lambda s: s.replace(
        '## 温层', ''.join('- **填充线%02d**［填］最后 2026-01-01\n' % i for i in range(1, 12)) + '\n## 温层', 1), 'G'),
    ('派生待办漏登记', F_DATA, lambda s: s.replace(
        'DATA-END v1.0',
        '- [示例][✅] X-002 示例决策，配套动作同步启动 | - | - | 占位\n\nDATA-END v1.0', 1), 'H'),
    ('版本回退（疑并行覆盖）', None, None, 'I'),   # 变异走快照清单：塞入高于当前的快照版本
    ('SKILL 签名缺失', F_INDEX,
     lambda s: s.replace('SKILL-END v1.0\n', '', 1), 'A'),
    ('SKILL 签名版本不唯一', F_INDEX,
     lambda s: s + '\nSKILL-END v0.8（旧版残留）\n', 'A'),
    ('SKILL 版本回退（疑并行覆盖）', None, None, 'I'),  # 变异走快照清单：塞 SKILL_v9.9
    ('ID 撞号（J 组）', F_DATA, lambda s: s.replace(
        'DATA-END v1.0',
        '- [示例][✅] X-001 重复撞号条目（与既有 X-001 共号） | - | - | 占位\n\nDATA-END v1.0', 1), 'J'),
]


def poison_test():
    print('=' * 66)
    print('毒丸自检（内存变异，不碰磁盘）')
    print('=' * 66)

    base_ctx = green_fixture()
    base_results = run_checks(base_ctx)
    base_bad = [r for r in base_results if r[0] in ('FAIL', 'WARN')]
    print('\n--- 基线（应为全绿）---')
    for level, group, msg in base_bad:
        print('  [基线异常] [%s] %s  %s' % (level, group, msg))
    if base_bad:
        print('\n基线不绿，先修夹具再谈毒丸（夹具错 ≠ 检查器错，但分不清就没意义）')
        return 1
    print('  基线全绿（%d 条 OK）' % len(base_results))

    caught, skipped = 0, 0
    total = len(POISONS)
    for title, fname, mutate, expect in POISONS:
        ctx = green_fixture()
        if fname is None:
            # 走非文件通道的变异
            if expect == 'D':
                ctx.snapshots = [('empty_snapshot.md', 0)]
            elif expect == 'F':
                ctx.logs = [(ctx.today - datetime.timedelta(days=30)).isoformat()]
            elif expect == 'I':
                # 磁盘 v1.0，快照里出现 v9.9 → 回退告警；SKILL 版毒丸单测 SKILL 回退通道
                if 'SKILL' in title:
                    ctx.snapshots = ctx.snapshots + [('SKILL_v9.9_覆盖嫌疑.md', 100)]
                else:
                    ctx.snapshots = ctx.snapshots + [('MEMORY_v9.9_覆盖嫌疑.md', 100)]
        else:
            src = ctx.files[fname]
            dst = mutate(src)
            if dst == src:
                print('  [SKIP] %s —— 锚点未命中，毒丸自身失效，须修毒丸' % title)
                skipped += 1
                continue
            ctx.files[fname] = dst
        results = run_checks(ctx)
        hit = [r for r in results if r[0] in ('FAIL', 'WARN') and r[1] == expect]
        if hit:
            caught += 1
            print('  [OK]   %s → 组 %s 报错: %s' % (title, expect, hit[0][2][:60]))
        else:
            others = [r for r in results if r[0] in ('FAIL', 'WARN')]
            print('  [FAIL] %s → 期望组 %s 未报错%s'
                  % (title, expect,
                     ('，但其他组报了: %s' % [(r[1], r[2][:40]) for r in others]) if others else '，全绿'))
    print('\n捕获 %d/%d，跳过 %d' % (caught, total, skipped))
    # 捕获率 100% 的定义：全部毒丸都有着落，未 SKIP 的全部被捕获
    trusted = (caught + skipped == total) and (caught == total - skipped)
    print('结论: %s' % ('检查器可信（捕获率 100%）' if trusted else '不可信'))
    return 0 if trusted else 1


# ---------------------------------------------------------------- CAS 写前校验
CAS_KEYS = {
    'RULE': (F_RULE, SIG['RULE']),
    'STATUS': (F_STATUS, SIG['STATUS']),
    'DATA': (F_DATA, SIG['DATA']),
    'SKILL': (F_INDEX_REL, SIG['INDEX']),
}


def cas_precheck(base, expects):
    """写公共文件前的 CAS 校验：磁盘签名版本 == 会话预期版本才放行。
    用法：--expect RULE=3.18 STATUS=1.6 DATA=1.45 SKILL=1.0（键可小写）。"""
    print('=' * 66)
    print('CAS 写前校验  (base=%s)' % base)
    print('=' * 66)
    bad = 0
    for kv in expects:
        k, sep, want = kv.partition('=')
        k = k.strip().upper()
        want = want.strip()
        if not sep or k not in CAS_KEYS or not re.fullmatch(r'\d+\.\d+', want):
            print('  [FAIL] 参数「%s」不合法（应为 键=版本，键 ∈ RULE/STATUS/DATA/SKILL）' % kv)
            bad += 1
            continue
        fname, sig = CAS_KEYS[k]
        text = read(os.path.join(base, fname))
        got = re.findall(r'^%s v(\d+\.\d+)' % re.escape(sig), text or '', re.M)
        if not text:
            print('  [FAIL] %s 读取失败（文件不存在？）' % fname)
            bad += 1
        elif not got:
            print('  [FAIL] %s 未找到签名 %s（无法校验，视为冲突）' % (fname, sig))
            bad += 1
        elif want not in got:
            print('  [FAIL] %s 版本冲突：磁盘=%s，会话预期=v%s —— 另一会话已写入，'
                  '全量重读基于最新版重放改动' % (fname, '/'.join(sorted(set(got))), want))
            bad += 1
        else:
            print('  [PASS] %s = v%s（与预期一致，可写）' % (fname, want))
    print('\n结论: %s' % ('全部匹配，可写' if not bad else '%d 项冲突，停止写入' % bad))
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser(description='分层记忆管理体系 · 健康巡检器')
    ap.add_argument('--base', default='.workbuddy/memory', help='记忆目录')
    ap.add_argument('--index', default=None, help='工作线索引文件（启用 G2 反向映射）')
    ap.add_argument('--expect', nargs='*', default=None,
                    help='CAS 写前校验，如 --expect RULE=3.18 STATUS=1.6 DATA=1.45')
    ap.add_argument('--poison', action='store_true', help='毒丸自检（内存变异）')
    args = ap.parse_args()

    if args.poison:
        return poison_test()

    if not os.path.isdir(args.base):
        print('记忆目录不存在: %s' % args.base)
        return 1

    if args.expect:
        return cas_precheck(args.base, args.expect)

    print('=' * 66)
    print('记忆体系健康巡检  %s  (base=%s)' % (datetime.date.today(), args.base))
    print('=' * 66)
    ctx = load_ctx(args.base, args.index)
    return report(run_checks(ctx))


if __name__ == '__main__':
    sys.exit(main())

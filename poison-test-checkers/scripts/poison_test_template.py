# -*- coding: utf-8 -*-
"""
毒丸自检骨架 —— 可直接运行的模板

用法：
    python poison_test_template.py            # 跑主检查 + 毒丸自检
    python poison_test_template.py --check    # 只跑主检查

改造指南：
    1. 把 DEMO_FILES 换成真实文件加载（见 load_real()）
    2. 把 check_* 换成你自己的检查组，每个返回 (passed, group_id, item, detail)
    3. 为每个检查组配一个毒丸，填进 POISONS
    4. 跑一遍，确认「捕获 N/N」

设计要点（改动时别破坏）：
    - 毒丸只改内存，绝不写磁盘
    - SKIP（锚点失效）与漏网分开统计，SKIP 不入捕获率分母
    - 期望值只锁结构不锁具体值（见 check_c）
"""
import re
import sys

# ---------------------------------------------------------------- 被检内容

# 演示用：真实使用时换成从磁盘读（见 load_real）
DEMO_FILES = {
    # 示例数据必须让主检查全绿，靠毒丸注入才转红 —— 否则开箱即用就是红的，
    # 演示不出「注入前通过 / 注入后报错」的对比。
    'MEMORY.md': '\n'.join([
        '# MEMORY v1.0',
        '- 规则：数据只在此维护',
        '- 版本: v1.0',
        'SIGN-END v1.0 2026-01-01',
    ]),
    'DATA.md': '\n'.join([
        '# DATA v1.2',
        '- 版本: v1.2 | 待同步: 0 条',
        '- 条目 A（引用 notes/a.md）',
        '- 条目 B（引用 notes/b.md）',
        'DATA-END v1.2 2026-01-01',
    ]),
    'notes/a.md': '# A\n',
    'notes/b.md': '# B\n',
}


def load():
    """演示模式返回内置内容；接真实文件时改这里。"""
    return dict(DEMO_FILES)


def load_real(base):
    """接真实文件的参考实现（需要时替换 load 的调用）。"""
    import io
    import os
    names = ['MEMORY.md', 'DATA.md']
    out = {}
    for n in names:
        p = os.path.join(base, n)
        out[n] = io.open(p, encoding='utf-8').read()
    tdir = os.path.join(base, 'topics')
    if os.path.isdir(tdir):
        for f in os.listdir(tdir):
            if f.endswith('.md'):
                out['notes/' + f] = io.open(os.path.join(tdir, f), encoding='utf-8').read()
    return out


# ---------------------------------------------------------------- 检查组

def check_a(files):
    """A 组：规则区不得出现状态字段（否则状态内容回流到规则层）。"""
    res = []
    m = files['MEMORY.md']
    rules = [l for l in m.split('\n') if l.startswith('- ')]
    bad = [l for l in rules if l.startswith('- 状态：')]
    res.append((not bad, 'A1', '规则区无状态字段', '命中 %d 条' % len(bad)))
    return res


def check_b(files):
    """B 组：引用完整性 —— 所有 (引用 X) 指向的文件必须存在。"""
    res = []
    f = files['DATA.md']
    refs = re.findall(r'（引用 (.+?)）', f)
    missing = [r for r in refs if r not in files]
    res.append((not missing, 'B1', '引用目标均存在',
                '缺失 %s' % missing if missing else '%d 个引用全部命中' % len(refs)))
    return res


def check_c(files):
    """C 组：签名格式 —— 只锁结构，不锁具体版本号。"""
    res = []
    m = files['MEMORY.md']
    sig = [l for l in m.split('\n') if l.startswith('SIGN-END')]
    ok = bool(sig) and bool(
        re.match(r'^SIGN-END v\d+\.\d+ \d{4}-\d{2}-\d{2}$', sig[0]))
    res.append((ok, 'C1', '签名格式合规（不锁具体版本号）', sig[0] if sig else '未找到'))
    return res


def run_all(files, quiet=False):
    out = []
    for fn in (check_a, check_b, check_c):
        out.extend(fn(files))
    if not quiet:
        for passed, gid, item, detail in out:
            print('  [%s] %-4s %s  → %s' % ('OK' if passed else 'FAIL', gid, item, detail))
    return out


# ---------------------------------------------------------------- 毒丸

# (标题, 文件名, mutate 函数, 期望触发的检查组)
# 注：锚点必须来自被检文件实测，不要从文档里抄（文档里的 **加粗** 会导致替换静默失败）
POISONS = [
    ('注入状态字段到规则区', 'MEMORY.md',
     lambda s: s.replace('- 规则：', '- 状态：毒丸\n- 规则：', 1), 'A1'),
    ('引用目标改名', 'DATA.md',
     lambda s: s.replace('（引用 notes/b.md）', '（引用 notes/不存在.md）', 1), 'B1'),
    ('签名格式破坏', 'MEMORY.md',
     lambda s: s.replace('SIGN-END v1.0 2026-01-01', 'SIGN-END 版本一 二〇二六', 1), 'C1'),
    # 这条故意用不存在的锚点，用来演示 SKIP 判据（不计入分母）
    ('锚点故意写错（演示 SKIP）', 'MEMORY.md',
     lambda s: s.replace('- 这个锚点不存在：', '- 状态：毒丸\n', 1), 'A1'),
]


def poison_test():
    print('\n' + '=' * 62)
    print('毒丸自检（内存注入，不写磁盘）')
    print('=' * 62)
    base = load()
    caught, skipped = 0, 0
    for title, key, mutate, expect in POISONS:
        poisoned = mutate(base[key])
        if poisoned == base[key]:
            # 锚点没命中 = 毒丸自身失效，不是检查组失效
            print('  [SKIP] %-30s → 毒丸未注入（锚点文本不存在，须修毒丸）' % title)
            skipped += 1
            continue
        F = dict(base)
        F[key] = poisoned
        bad = [r for r in run_all(F, quiet=True) if not r[0]]
        hit = any(r[1].startswith(expect) for r in bad)
        caught += hit
        print('  [%s] %-30s → 期望触发 %s，实测 FAIL %d 条%s'
              % ('OK' if hit else 'FAIL', title, expect, len(bad),
                 '' if hit else '  ⚠️ 未触发！'))
    total = len(POISONS) - skipped
    print('  ---')
    print('  捕获 %d/%d（跳过 %d：锚点失效，须修毒丸）' % (caught, total, skipped))
    print('  判定: %s' % ('毒丸自检通过，检查器有效' if caught == total else '存在未触发项，检查器不可信'))
    return caught == total


# ---------------------------------------------------------------- 主流程

def main():
    print('=' * 62)
    print('主检查')
    print('=' * 62)
    files = load()
    res = run_all(files)
    failed = [r for r in res if not r[0]]
    print('  ---')
    print('  FAIL %d 条 / 总 %d 条' % (len(failed), len(res)))

    if '--check' in sys.argv:
        return 0 if not failed else 1

    ok = poison_test()
    print('\n' + '=' * 62)
    print('结论: %s' % ('全部通过，检查器有效' if not failed and ok else '存在未通过项，须排查'))
    print('=' * 62)
    return 0 if (not failed and ok) else 1


if __name__ == '__main__':
    sys.exit(main())

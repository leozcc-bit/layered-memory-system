---
name: poison-test-checkers
description: >-
  Give any check/validation/acceptance script a "poison test" — inject known fake
  defects in memory, verify every check group actually fires, and prove the checker
  is not a rubber stamp. Use when writing a health-check, lint, acceptance, or
  consistency script; when a checker's first run reports a pile of failures and you
  must separate real defects from bugs in the checker itself; or when a checker has
  been green for so long you suspect it is not actually checking anything. Covers
  7 classes of injectable defects, a runnable Python skeleton, and the three
  judgement rules that make a poison test trustworthy (SKIP vs miss, measure-then-assert
  anchors, and verifying the poison itself).
agent_created: true
author: 敏哥与小B
version: 1.0.0
---

# 毒丸自检：证明检查脚本不是橡皮图章

## 核心问题

一个检查脚本最危险的失败方式**不是报错，是永远返回通过**。

写完一个健康检查 / 一致性校验 / 验收脚本，跑一遍全绿——这什么都不能证明。可能它真在查，也
可能它的匹配模式从来没命中过任何东西，于是 `for` 循环空转、一条检查都没执行。

**误报比漏报更坏。** 漏报是一次没抓住，误报会训练人忽略这个脚本的全部输出。一个长期误报的
检查器，等它真抓到问题时也没人看了。

毒丸自检就是解决这个：主动往被检内容里注入一处**已知的假失效**，看检查组是否报错。不报错，
说明这个检查组是死的。

## 何时使用

- 刚写完检查 / 校验 / 验收脚本，准备交付前
- 检查脚本首跑报了一堆 FAIL —— 先别急着改被检物，先确认这些 FAIL 不是脚本自己的病
- 检查脚本长期全绿，怀疑它其实没在查
- 改造了检查脚本的匹配模式之后（改模式最容易把检查改成空转）

## 做法：四步

1. **每个检查组配至少一个毒丸**。有 N 个检查组，就要有 N 个毒丸，一一对应。
2. **毒丸 = 往被检内容的副本注入一处已知假失效**，只改内存不写磁盘。
3. **注入后重跑检查，该组必须报错**。报错 = 捕获；不报错 = 这个检查组失效。
4. **捕获率必须 100%**。有一条没捕获，检查脚本就不能算可信，回去查那个组。

## 七类可注入的假失效

按"什么东西最容易悄悄失效"分类，每一类对应一类检查组：

| # | 注入什么 | 典型检查组 |
|---|---|---|
| 1 | 往规则区注入一个被禁止的字段 | 禁词 / 状态字段检查 |
| 2 | 把版本号回退一格 | 版本号一致性 |
| 3 | 删掉清单里的一行 | 条目完整性 |
| 4 | 把被引用的目标改个名 | 引用完整性 |
| 5 | 往不该写死的地方注入一个硬编码值 | 硬编码值扫描 |
| 6 | 破坏格式（去掉签名行、改表头） | 结构校验 |
| 7 | 塞入越界值（数量超限、日期过期） | 阈值检查 |

**设计原则**：毒丸要注入在**被检物最可能真实出问题的地方**。注入一个没人会犯的错，验出来的
是"检查组活着"，验不出"检查组有用"。

## 必须分清的判据

### 1. SKIP（锚点失效）≠ 漏网

毒丸靠"替换某段文本"来注入。如果那段文本在被检物里根本不存在，替换不生效，毒丸没注进去，
检查组自然不报错——这不是检查组失效，是**毒丸自己失效了**。

```python
poisoned = mutate(text)
if poisoned == text:
    print('[SKIP] 毒丸未注入 —— 锚点文本不存在，毒丸本身失效，须修毒丸')
    skipped += 1
    continue          # 不计入捕获率的分母
```

把 SKIP 算成"未捕获"，会得出"检查器不可信"的错误结论，然后去改一个本来正确的检查组——
**这就是误报**。首版实现踩过这个坑。

### 2. 锚点必须实测，不能照抄文档

注入用的锚点文本（要被替换的那串字）必须**从被检文件里实测取出来**。

常见翻车：从方案文档或需求表格里抄锚点，而文档里的字符串带 markdown 格式化（加粗 `**`、
反引号 `` ` ``），真实文件里没有。替换静默失败，毒丸变成 SKIP。

```python
# 先把真实原文打出来看一眼，再决定锚点
for i, line in enumerate(text.split('\n'), 1):
    if KEYWORD in line:
        print('%4d| %s' % (i, line[:120]))
```

同一个病也会发生在检查模式上：检查用的正则/子串如果抄自文档表格，同样会静默不命中。

### 3. 毒丸自身也要验

毒丸是脚本的一部分，它自己也会有 bug。验收脚本的期望值如果硬编码（比如断言签名必须等于
`v1.1`），被检物一升版就 FAIL——**检查器自己成了新故障源**。

期望值只锁**结构**不锁**具体值**：

```python
# 末尾不加 $ —— 签名行后面常带括注说明，加了会匹配不上
m = re.match(r'^SIGNATURE v(\d+)\.(\d+) (\d{4}-\d{2}-\d{2})', sig)
assert m                      # 格式合规
assert int(m.group(1)) >= 1   # 单调不回退，只卡下限
```

具体值以被检文件为准，人工核对。这样升版不再需要同步改检查脚本。

### 修一处 ≠ 扫一类

改完一处硬编码期望值后，**必须回头全量扫同款**。实战里同一个脚本有 C3 和 C5 两个同款断言，
只改了当时报 FAIL 的 C3，半小时后另一个被检物升版，C5 立刻炸。

没报错不代表没问题，只代表**触发条件还没到**。每改完一处就问一句：

> 这个文件里，还有几个地方是在做同一件事？

配套一处同步改：**毒丸锚点也不能含易变值**。锚点写死 `SIGN-END v1.43`，被检物升版后锚点失效，
毒丸变 SKIP，自检从 7/7 掉到 6/6。用正则定位（`\d+\.\d+`），锚点只留稳定前缀。

## 可运行骨架

完整模板见 `scripts/poison_test_template.py`，可直接改。最小结构：

```python
def run_all(files):      # 被检内容 -> [(passed, group_id, item, detail), ...]
    ...

POISONS = [
    # (标题, 文件名, mutate 函数, 期望触发的检查组)
    ('注入禁字段', 'MEMORY.md', lambda s: s.replace('- 规则：', '- 状态：毒丸\n- 规则：', 1), 'A2'),
    ('版本号回退', 'DATA.md',   lambda s: s.replace('v1.2', 'v1.1', 1),                  'C5'),
]

def poison_test():
    base = load()                      # {文件名: 内容}
    caught, skipped = 0, 0
    for title, key, mutate, expect in POISONS:
        poisoned = mutate(base[key])
        if poisoned == base[key]:
            skipped += 1               # 毒丸自身失效，不入分母
            print('[SKIP] %s —— 锚点未命中，须修毒丸' % title)
            continue
        F = dict(base); F[key] = poisoned
        bad = [r for r in run_all(F) if not r[0]]
        hit = any(r[1].startswith(expect) for r in bad)
        caught += hit
        print('[%s] %s → 期望触发 %s%s'
              % ('OK' if hit else 'FAIL', title, expect,
                 '' if hit else '  ⚠️ 未触发'))
    print('捕获 %d/%d，跳过 %d' % (caught, len(POISONS), skipped))
    return caught == len(POISONS) - skipped
```

## 实战：用它来区分"真问题"和"脚本自己的病"

首跑检查报了 N 条 FAIL 时，**不要直接去改被检物**。流程是：

1. 逐条把命中原文打印出来，看清楚命中的到底是什么
2. 判断：这是被检物的真缺陷，还是检查模式的缺陷？

四种最常见的"检查脚本自己的病"：

| 病 | 表现 | 修法 |
|---|---|---|
| 检查范围擅自扩大 | 文档说查 A 文件，脚本查了 A+B+C，命中 B 里的历史陈述 | 收回到文档要求的范围 |
| 自述区没排除 | 变更日志里写着"把 X 改成了 Y"，检查 X 时命中了日志本身 | 排除自述/历史区 |
| 模式照抄文档格式 | 真实文件没加粗，模式带 `**` | 实测原文后修正模式 |
| 全文扫描替身 | 命中的是"为什么不能这么写"那段说明里的反面引用 | 限定扫描区间 |

**改被检物去迎合一个错误的检查器，会把真故障造出来。** 历史陈述被改等于篡改事实，变更记录
被改等于抹掉变更。

## 检查清单

交付一个检查脚本前，逐条过：

- [ ] 每个检查组都有对应的毒丸
- [ ] 毒丸注入后，对应检查组确实报错（捕获率 100%）
- [ ] SKIP 与漏网分开统计，SKIP 必须显式报告
- [ ] 所有锚点都从被检文件实测取得，逐处打印核对过
- [ ] 检查模式与注入模式都不含文档格式化产物（加粗、反引号）
- [ ] 自述区 / 历史陈述区已排除或已确认无干扰
- [ ] 期望值只锁结构不锁具体值
- [ ] 首跑报 FAIL 时，先证伪检查器，再动被检物
- [ ] 改完一处缺陷后，全量扫过同款（修一处 ≠ 扫一类）

## 一句话

**不跑毒丸的检查脚本，全绿只能说明它能跑完，不能说明它查到了。**

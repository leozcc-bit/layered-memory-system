# layered-memory-system · 分层记忆管理体系

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![Release](https://img.shields.io/github/v/release/leozcc-bit/layered-memory-system)](https://github.com/leozcc-bit/layered-memory-system/releases)
[![Stars](https://img.shields.io/github/stars/leozcc-bit/layered-memory-system?style=social)](https://github.com/leozcc-bit/layered-memory-system)

**仓库地址**：https://github.com/leozcc-bit/layered-memory-system


一套给 AI 助手工作空间用的记忆管理方法论 + 检查工具。解决一个具体问题：

> **常驻记忆文件是硬预算容器**——框架按字符数把它全量注入每个会话，超出即**静默从尾部截断**，
> 不报错。而记忆文件里的"进行中工作线"随业务增长线性膨胀，最先被砍掉的往往是你放在尾部的
> 版本签名——**自检机制本身失效了，而你毫不知情**。

它不是"要不要记、记什么"的记忆设计，是**硬预算约束下、多工作线并行时如何不失控**的实战体系。
每条机制背后对应一次真实故障（版本号漂移三次无人发现、悬空引用、快照为空、修旧病时造新病），
且都经过"改完再验"的闭环。

## 它长什么样

```
memory/
├── MEMORY.md    规则层：恒定内容，硬预算，远离截断线
├── STATUS.md    状态层：活跃工作线，热/温/冷分层
├── DATA.md      数据层：事实登记，带 ID 单一事实源，含待同步区
├── notes/       细节层：各线正文与经验沉淀
└── history/     快照：写前 cp，回退一步化
```

**归属判定只有一条：凡随工作线数量增长的内容，一律不进规则层。** 之后加到一百条线，
规则层一个字符都不用动。

## 机制一览

| 机制 | 一句话 |
|---|---|
| 四层归属 | 规则/状态/数据/细节，增长项全部外置出硬预算 |
| 容量实测 | Python 口径实测字符数，固定+边际公式反推上限，三值交叉验证 |
| 热度分层 | 热≤7天/温8~30/冷>30；降级只压字段不删行；逾期不降级；被提及不算动作 |
| 唯一事实源 | 事实带 ID，引用必带 ID，无 ID 视为未载入；归档不回收 ID |
| 三锚点 | 开工回显事实 → 变更记待同步区 → 收尾核销（四条件缺一不算完） |
| 签名纪律 | 状态层签名放头部（防尾部截断先丢校验位）；版本号只存一处，别处写比对动作 |
| 占位符 | 别处维护的值一律用占位符，执行时才取真值；示例值也是硬编码 |
| 快照回退 | 写前快照 + 预生成回退脚本，回退退化为一步 |
| 纪律三问 | 谁检查 / 怎么检查 / 失败会怎样——答不出不落盘 |
| 左右脑互搏 | 产出方与评审方轮流对抗；新增内容必须同轮自检，防"修旧病造新病" |
| 并发写防护 | 公共文件（含工作索引）单主窗口核销；写前 CAS 校验磁盘签名=预期版本；I 组检测版本回退 |
| 毒丸自检 | 往内存副本注入已知假失效，证明检查组不是橡皮图章；捕获率必须 100% |

## 快速开始

```bash
# 1. 复制模板
cp templates/*.md your-workspace/.workbuddy/memory/
cp -r templates/notes your-workspace/.workbuddy/memory/
mkdir -p your-workspace/.workbuddy/memory/history   # Git 不跟踪空目录，需自建

# 2. 巡检
python scripts/memory_health_check.py --base your-workspace/.workbuddy/memory

# 3. 毒丸自检（确认检查器在你环境下活着）
python scripts/memory_health_check.py --poison

# 4. 写公共文件前 CAS 校验（版本不符退出码 1，停止写入先重读）
python scripts/memory_health_check.py --base your-workspace/.workbuddy/memory --expect RULE=1.0 STATUS=1.0 DATA=1.0
```

### 首次运行会有两条预期内的提示

走完上面第 1、2 步，实测结果是 `FAIL 1 条 / WARN 1 条`，退出码 `1`。两条都是模板的
固有初始状态，不是缺陷：

| 提示 | 原文 | 为什么是预期内 | 怎么让它消失 |
|---|---|---|---|
| FAIL | D 组「history/ 无任何快照（写前快照机制未生效）」 | D 组查的不是目录**存不存在**，而是里面**有没有快照文件**；新建的空目录必然零快照 | 做一次写前快照：`cp 文件 history/文件_vX.Y_说明.md` |
| WARN | F 组「未找到任何日志文件」 | 模板不含日志；日志按天新建（`YYYY-MM-DD.md`） | 开工建当天日志文件，或按需忽略 |

补做一次快照后再跑 → `FAIL 0 条 / WARN 1 条`，退出码 `0`（WARN 不影响退出码）。

**随模板附带空目录 + `.gitkeep` 解决不了 D 组**：`.gitkeep` 不是快照，要么仍算零快照，
要么被当成 0 字节快照、触发 D 组另一条 FAIL「存在空快照（回退会失败）」。所以这里选择
把预期写明，而不是让首跑假装全绿。

除这两条外，其余各组首跑应全 OK。若还报别的 FAIL，先跑 `--poison` 证伪检查器，再动被检物。

退出码：`0` = 无 FAIL（毒丸模式 = 捕获率 100%；CAS 模式 = 版本全部匹配）；`1` = 有 FAIL / 有毒丸未捕获 / CAS 版本冲突。

## 仓库结构

```
├── SKILL.md                        # skill 本体：给 AI 助手的完整操作规程
├── README.md                       # 本文件
├── LICENSE                         # MIT
├── scripts/
│   └── memory_health_check.py      # 十组检查（含 J 组 ID 唯一性）+ 写前 CAS 校验 + 内置毒丸自检（纯函数设计，内存变异不碰磁盘）
├── poison-test-checkers/           # 配套 skill：给任何检查脚本加毒丸自检（详见下）
│   ├── SKILL.md
│   └── scripts/poison_test_template.py
└── templates/
    ├── MEMORY.md                   # 规则层模板（含体系规则起步集）
    ├── STATUS.md                   # 状态层模板（热/温/冷 + 待确认 + 已闭环）
    ├── DATA.md                     # 数据层模板（待同步区 + ID 分区）
    └── notes/README.md             # 细节层说明
```

## 设计立场

- **规则没有检查兜底就是文字。** 每条纪律必须答得出「谁检查、怎么检查、失败会怎样」，
  答不出的降级为建议。
- **误报比漏报更坏。** 长期误报的检查器，等它真抓到问题时也没人看了——所以检查组必须
  按毒丸验证，SKIP 与漏网分开统计。
- **改被检物去迎合错误的检查器，会把真故障造出来。** 首跑报一堆 FAIL 时，先证伪检查器，
  再动被检物。
- **结论对 ≠ 理由对。** 修复动作自身要过同样的检查；示例值也是硬编码。

## 配套 skill：poison-test-checkers

本仓库的健康巡检脚本**内置了毒丸自检**（`--poison`），那条能力本身是从一个独立 skill 里来的。
既然任何检查脚本都需要它，就一并放在这里：

> **poison-test-checkers** —— 给任意检查 / 校验 / 验收脚本加"毒丸自检"：往被检内容的内存副本里
> 注入一处**已知的假失效**，看对应检查组是否报错。不报错说明这个检查组是死的。覆盖 7 类可注入
> 缺陷、一个可运行 Python 骨架，以及三条让毒丸可信的判据（SKIP 与漏网分开统计、锚点必须实测
> 而非照抄文档、毒丸自身也要验）。
>
> 用法见 `poison-test-checkers/SKILL.md`。与记忆体系没有依赖关系，可以单独拿去用。

**为什么放在一起**：本体系的一条核心立场是「规则没有检查兜底就是文字」，而毒丸自检是
「检查本身有没有检查」的答案。两者是同一套方法论的两半。

## 署名与协议

由「敏哥与小B」在真实长任务工作流中提炼而成——人定方向与拍板，AI 落地与执行，
双方在多轮互搏评审中把这套体系磨出来。

本仓库两个 skill（`layered-memory-system` 与 `poison-test-checkers`）同一署名、同一协议。

署名中的称呼为有意保留的联合署名，非可反查身份信息，随作者意愿公开。

MIT License。

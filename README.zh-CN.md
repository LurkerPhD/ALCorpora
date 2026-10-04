# protocolize

> English: [README.md](README.md)

把**任意项目**归一化成两个产物,每个都在**出口自验证**:

| 产物 | 回答的问题 | schema |
|---|---|---|
| **构建协议** | 这个项目是怎么**构建**的? | `private_protocol.Protocol` |
| **验证协议** | 我们凭什么说它的一次运行是**对的**? | `verification_plan.VerificationPlan` |

没被**重建过**(构建协议)/ **跑过**(验证协议)的协议不是产物。Fortran 只是第一个
*profile*——这个仓库里不许出现语言名、构建系统名、项目名。

构建协议写成 `compile_commands.json` 的**数组**:最后一个元素之前的每个元素都是编译
条目(四个标准字段 + 本协议的额外字段),最后一个是携带「compile database 表达不了
的段」的节点。它可以原封不动地交给只看 cc 的消费者——数组结构永不被破坏,允许多加
条目。

`PLAN.md` 是计划、验收标准与实测证据;本文件讲的是它**实际怎么运转**。
**请随时维护本文件**——这里一张图如果指向了一个已经搬走的模块,对下一个读者的
误导和一个过期注释完全一样。

---

## 不可违反的不变量

1. **构建是构建的权威。** 只问构建系统自己的子工具
   (`ninja -t compdb/query/commands/targets`、`make -Bn`、
   `ctest --show-only=json-v1`)。**绝不解析 manifest**——manifest 是那个工具
   的内部格式。
2. **同一条路径的两种拼写,含义相反。**
   * 在 `${TREE}` 之下——重放**必须自己 make** 的(生成源、产物)。它随协议走。
   * **绝对路径**——重放**只读**的(项目源、第三方库、配置期生成的 header)。
     它不随协议走,且**不许改写**。
   `$` 是保留字符:字面美元写 `$$`,裸 `$` 直接拒绝。
3. **stem 就是 unit 的身份**:去掉一个尾扩展名,大小写保留。产物用 stem 记它的成员,
   所以链接行按 unit 的身份引用它,而不是按恰好持有它的那个文件。本仓库**不做转换**
   ——它读入一个构建再**重放**它,所以 unit 的语言就是它的语言,从来不是一个可翻转的 form。
4. **协议**内嵌**一份 compile database,但它本身不是。** 文件是一个 JSON 对象,
   `compile_commands` 是其中一个元素:真实的 cc 加载器是严格白名单且全有或全无,
   只要条目上多一个 `stem`/`language`/`output`,clangd 就**整份拒载**。合法的 cc
   是**导出物**(`to_cc`,剥掉一切 cc 未定义的字段)。
5. **不许猜。** 歧义一律**拒绝**,绝不静默选一个:多个候选 case 声明(会拿邻居的
   案例来验证本语料)、stem 冲突(会静默丢掉一个 unit)、make 的 `$(shell)` 源
   没有任何命令产出、state 要的 form 协议里没有。

---

## 模块地图

```
bench.py                工具入口:一个子命令一个原子操作
                        (tree/wipe/plan/verify/evidence/row/fetch/admit);
                        每个都委托,没有一个重写
admit.py                准入流程(编号/取源/原生/§A/§B/记行)
protocolize/
  tree_anchor.py        ${TREE} 占位符: expand / escape / 校验(裸 $ 拒绝)
  compile_commands.py   读 compile database;协议**内嵌**一份
  output_layout.py      输出目录规则;repo root;longest owning root
  private_protocol.py   构建协议 schema + problems() + canonical_stem
  verification_plan.py  验证协议 schema + problems()
  flat_cmake.py         协议 + state -> 一个扁平 CMakeLists.txt
  volatiles.py          测量同一个二进制的两次运行**在哪些行上不一致**
  onboard/
    extract.py          从已构建的树里读出协议(ninja/CMake);
                        capture_verification;own_roots;prepare;tests;support_files
    make_extract.py     make 后端(make -Bn)
    selfcheck.py        §A:从协议重建并比对
  verify/
    suite.py            运行一个验证计划;算裁决
    cli.py              CLI: python3 -m protocolize.verify.cli <build tree>
    reference.py        origin(黄金)捕获;计划的运行时环境
    compare.py          text/field 比对(lockstep / numclose / 列模型);
                        drop / keep / mask
    counts.py           BenchCounts:从 runner 自己的输出解析计数
    log.py              日志 / tee / Action
skills/onboard-project/  技能:SKILL.md + scripts/onboard.py + reference/*.json + tests/
skills/fetch-benchmark-project/  把一个项目准入 benchmark/:SKILL.md + scripts/fetch.py
```

**分层。** `tree_anchor.py` 是 layer 1(transpiler 也要用它);协议代码是 layer 2,
import 它——所以 layer 1 永远不会反过来去够 layer 2。

---

## 交付流程

一次准入,从头到尾。**`[AGENT]` 是只有泛化才能给出的判断;`[PY]` 是工具确定性执行的
一步。** agent 只在**工具停下来**的地方被调用,下面每一处都写明了它该产出什么——以及
它**绝不该**做什么。

```python
ADMIT_ONE_PROJECT(source):
    # ── [AGENT] 唯一一个"每个项目都必然要泛化"的步骤 ─────────────────────
    # 「这个项目怎么原生构建、怎么原生验证」没有确定答案。这是 agent 必须
    # 产出的唯一研究结果。
    configure, build, test, tree_subdir = research_how_it_builds(source)

    # ── [PY] 以下全部在一次调用里跑完 ────────────────────────────────────
    [AGENT] -> python3 bench.py {fetch <source> | admit <dir>} <args...>
    #   fetch = 首次准入;  admit = 重证一个已在 benchmark/ 里的

    n, dest = smallest_free_number(), benchmark/{n:03d}-{name}
    fetch(source -> dest/inner)                 # clone 或 copy,带重试
    src = inner_source(dest)                    # 按"它持有什么"选,不按名字
    if re-prove: clear_previous_build(src, tree_subdir)
        # in-source -> 只删构建系统生成的状态,绝不删目录本身

    # ── 1-2  原生:项目用自己的工具链构建、用自己的方式验证 ───────────────
    for op in prepare_ops:                      # 幂等:{op, check, run}
        if not prepare_done(op): run(op.run)
    for line in (configure, build, test):       # 项目自己的命令
        if 失败: record(REFUSED); -> [AGENT] JUDGE_BUILD_FAILURE

    # ── 3  §A:构建协议,靠"重放它"来证明 ────────────────────────────────
    proto = extract_protocol(build)             # 问构建系统自己的子工具
        # build.ninja -> ninja 后端;  有 Makefile -> make 后端
        # 都没有      -> REFUSE,并说清这棵树"是什么"(即缺一个后端)
        # -> 单元、产物、生成物、own_roots、路径的两种拼写
    self_check(proto)                           # 镜像树;删掉生成源**和产物**;
        # flat_cmake -> cmake+ninja;逐产物按**已定义符号**比对
        # (两次正确的构建永远不会逐字节相同)
    if 不能重放: record(A=no); -> [AGENT] JUDGE_EXTRACTION_FAILURE

    # ── 4  验证协议:从项目自己的声明里读出来 ────────────────────────────
    plan = capture_verification(proto, declaration, facts)
        # 定位验收方式:注册的测试 | 用例清单 | 每用例一个目录
        # 读声明的比较规则(drop/keep/mask/rtol/atol);展开 facts
    if plan.problems(): record(FAIL_B_PLAN); -> [AGENT] JUDGE_PLAN
        # 空计划、读不懂的计划都**不是**中性答案

    # ── 5  参考跑:golden 是"做出来"的,绝不"借来" ───────────────────────
    if plan.golden is reference_run:
        if ensure_reference(plan) == 0: record(FAIL_B_REF); -> [AGENT] JUDGE_NO_GOLDENS

    # ── 6  §B 跑两次 —— 项目构建上 与 重放上 的裁决必须**一致** ──────────
    if verify(project) != green: record(FAIL_B_REF);  -> [AGENT] JUDGE_B_PROJECT
    if verify(replay)  != green: record(FAIL_B_FLAT); -> [AGENT] JUDGE_B_FLAT

    record(PASS, language, notes)               # 只有走到这里才算 legal


# ══ agent 只在这些出口被调用,且每次都只产出"数据" ══════════════════════════

[AGENT] JUDGE_BUILD_FAILURE(log):
    读 log;诊断成因;修法二选一:
        prepare_op {op, check, run}   # 写进项目树 = 补丁;必须幂等
        build_flag                    # 调用层适配,不算补丁
    重跑                             # 没让构建通过的"修复"**不记录**
    若定不出可确认的修法: BLOCKED

[AGENT] JUDGE_EXTRACTION_FAILURE(exc):
    "协议错了"                -> 改传给工具的参数
    "工具读不了这种构建"      -> 扩 extract.py 里那条规则 + 一个测试
                                 # **绝不**手写一份协议绕过

[AGENT] JUDGE_PLAN(problems):
    声明错/缺                 -> 写项目自己的声明
    捕获器读不了这种声明形状  -> 扩 capture_verification + 一个测试
                                 # **绝不**手写一份计划绕过

[AGENT] JUDGE_NO_GOLDENS():
    写 facts.json             # 程序"能跑起来"所需、但树里看不出的事实

[AGENT] JUDGE_B_PROJECT(printed):
    python3 bench.py evidence --shift-date    # [PY] 跑两次参考,第二次改 TZ
    对每条被报为"未覆盖"的行形状:
        识别类别 -> 把一条规则写进项目自己的声明
        # drop | keep | mask;默认 mask;同行有被判量时**绝不 drop 整行**
    重新捕获;重新测量(必须为空);在**重新捕获**的 golden 上重验
        # **绝不**把一条差异"归为合理"就放过

[AGENT] JUDGE_B_FLAT(printed):
    项目上绿、重放上不绿 -> 是**工具**错了,不是项目
```

**每个项目都要付的那一步,只有 `research_how_it_builds`**:一个项目的原生工具链是
**关于那个项目**的知识,把猜测编码进工具就是把项目知识推进了工具。agent 其余所有动作
都由**工具停下来**触发,产出的都是**数据**——一个 prepare op、一条事实、一条规则,或
一条被扩写的规则——**从不是流程的一步**。

---

## 构建协议:抽取 + 证明(§A)

```
已构建的树
   |
   |- build.ninja / CMakeCache.txt -> ninja 后端
   |- Makefile(根,或下一级)       -> make 后端
   `- 都没有                       -> 拒绝(不许猜)
   |
   v  Toolchain(build, root)
   |- targets()       -> 每条边的 (output, rule)
   |- compdb()        -> 编译数据库,由构建系统自己清理过
   |- commands_for()  -> 每 target;闭包在前,该 target **自己的边在最后一行**
   |- inputs_many()   -> explicit 与 | / || 之后分开
   |
   |- _raw_entries : COMPILER 边 + PREPROCESS 边
   |     `- _edges : 在**中间产物**上 join
   |           (一个 unit != 一条 compdb 记录:CMake 把扫描型源编成两步)
   |           `- _units : stem = 源相对它自己的根部,去扩展名
   |
   |- _products    : 链接目标;输入记成 STEM;libs 保序
   |- _generated   : CUSTOM_COMMAND 的产出 + 命令 + depends
   |- produced 集  : 决定每条路径的**拼写**(必须在拼写之前算出来)
   |- own_roots    : 哪些 unit 是项目自己的(.gitmodules / cache 的 _SOURCE_DIR)
   |- tests        : ctest --show-only=json-v1
   `- support_files: 测试**要读**、但既不编译也不生成的文件(以**内容**携带)
   |
   v
Protocol  -- problems() 必须 == [] -- (是 list,不是 raise:一次给全部缺口)
   |
   v
_prove -> selfcheck.self_check():
   mirror 树 -> 删掉生成的源**和产物**(逼重放真的去 MAKE 它们,而不是捡现成的)
   -> flat_cmake -> cmake+ninja -> 逐产物比对
      |- 不等 -> ExtractionError:拒绝交出协议
      `- 相等 -> 返回
```

产物和生成的源同因同删:一个把产物放在源码旁边的构建(mirror 会一起拷过来),
其产物若重放根本没跑过命令,也照样会被"找到"——找到的是拿来比对的同一个文件本身
——于是判成 EQUAL,而实际什么都没建。只删重放自己树里的拼写:绝对路径指的是摄取树,
重放只读它。

### §A 比对——判据

`selfcheck` 比的是**产物**。`PLAN.md` §A 规定每个产物四项检查(下面这张实测表决定了
什么该红、什么不该红):

1. **defined symbols**(`nm -g --defined-only`;滤掉 archive 成员头和版本化符号);
2. **规范化指令序列**(`objdump -d --no-show-raw-insn`):去掉地址列和
   `# <addr> <sym>` 注释,`0x…(%rip)` -> `(%rip)`,
   `call/jmp 0x… <sym>` -> `call/jmp <sym>`;
3. **`.data` 原始字节**(`objcopy -j .data`)——逮住只看代码时看不见的 `DATA` 常量;
4. **section 清单**(`readelf -S` 的名称+类型;**不含大小**)。

| 构造 | 整文件 | 规范化 .text | .data | 结论 |
|---|---|---|---|---|
| 同代码,路径**长度不同** | 差 | **同** | 同 | EQUAL |
| `-O0` vs `-O2` | 差 | **差** | — | DIFFER |
| `__DATE__`/`__TIME__` 差一秒 | 差 | **同** | 同 | EQUAL |
| `data x /1/` vs `/2/` | 差 | 同(盲) | **差** | DIFFER |
| section 基址平移 | 同 | 同 | 同 | EQUAL |

**不许用**:整文件 sha256(`__DATE__` 反例)、`.rodata` 内容(内嵌的源码路径就落在
那里)、裸 `.text` 字节(路径长度变化会移动 rip 位移)、收 `.o` 文件。

机制:路径串落在 `.rodata`;`.text` 用 rip 相对位移引用它,所以路径长度变化会移动
位移(被规范化掉)但不改变指令序列。产物是 PIE,所以 `.data` 里的指针是加载期重定位
——`.data` 字节与路径无关,可以裸比。

> **现状:** `selfcheck.py` 今天只实现了第 (1) 项。第 (2)–(4) 项属于 M4。

---

## 验证协议:捕获 + 证明(§B)

```
capture_verification(proto, build, root, project_root, declaration, facts)
   |
   |- proto.tests 非空?
   |     `- -> registry 形状: target=tree, run=suite (ctest),
   |           golden=none + stamp, assertions=self_report(项目自己的表)
   |
   `- 否则 _locate(project, finders):
        |- case_list (cases.txt / testcases.txt / *.files)
        |     |- 穿 INCLUDE 链读记录
        |     |- 列布局取**最宽**的记录(列表里混着有断言和无断言的条目)
        |     `- deck_root 从树里推(不一定是列表所在目录)
        |- one_per_dir (verification/ / Verification/ / tests/cases)
        |     |- 输入名拼写从树里推
        |     `- **整个**目录被 stage(支撑 deck 和 restart 状态就在输入旁边)
        `- 都没有 / 有歧义 -> 拒绝
   |
   v
VerificationPlan.from_json -> problems() 必须 == []
   (拒绝:无 require_min;process 运行没 inputs;golden 是 none 却没 stamp;
    裸 $;golden.root 是字面路径;source=reference_run 时 golden.root 不在运行
    产物根下;自相矛盾的 chain)
   |
   v
在 reference 上跑 cli.main([tree]) -> 退出码**就是**裁决 -> 0 才算证明通过
```

**origin 对所有项目是一条规则:** 让 REFERENCE 实例跑同一个 plan,把它产出的、
plan 称之为 golden 的东西留下来。reference 与 candidate 因此走同一条代码路径,
「失败的 reference 运行什么都捕获不到」由构造保证成立。

**golden 只能现做,不能外借。** 仓库提供的是*输入文件*;golden 来自跑本机原生构建。
所以 `golden.source: reference_run` 时 `golden.root` 只允许是运行产物根
(`{origin}`、`{results}`)——写在采集树之下的根指的是**项目自带的**文件,那只是
"和另一台机器的编译器、操作系统、另一个年代结果一致",不是"这次构建跑对了"的证据。
实测:一个老语料 132 个用例各带一份 `.out`,没有一份与同一批卡组在本机跑出的结果
逐字节相同。

`golden.source: reference_run` 证明的是 candidate **与 reference 一致**——这是
*稳定性*,不是*正确性*。一个自带参考输出的项目手里有外部真值,但那份输出是**输入**,
所以要比对它就写进项目的 `declaration`。

---

## 重放:协议 -> flat build

```
Protocol
   |
   v  生成的 CMakeLists.txt 逐字跑「记录下来的命令」:
   |     单元    -> 它自己的 `command`,在它自己的 `directory` 里
   |                (两步/扫描型单元:先 preprocess 边,再 compile 边,
   |                 后者消费前者的中间产物)
   |     产物    -> 记录下来的 link/archive `command`
   |     生成源  -> 记录下来的 generator `command`
   |     目标文件路径从命令里读出(`directory` + `-o`)
   v
CMakeLists.txt -> cmake+ninja -> 产物 -> §A 比对
```

**逐字重放,不重新组装。** 生成器不说「这些源、这些 flags」让 CMake 组装命令,
而是重放构建记录下来的那条命令。重新组装会漂移,而且是静默地漂:实测在一个用
`-fvisibility=hidden` 编译的真实项目上,让 CMake 重建链接行产出了 `-rdynamic`、
并让每个可执行文件的 `main` 被**本地化**,于是 19 个产物「不一致」而它们的代码其实
完全相同。重放记录命令消除了整类问题:flags、`-fPIC`、可见性、链接顺序、源码拼写,
都是构建自己的。

生成器仍决定两件记录命令不包含的事:展开 `${TREE}` 到重放树;让一个单元 DEPENDS
在它所用模块的对象上,于是编译顺序交给 CMake。


## benchmark(被驱动的语料)

本工具按它「被使用的方式」开发:**拉一个自带验证的项目下来,跑完整流程,哪里坏了就修
工具。** `benchmark/`(已 gitignore)存放这些项目;`benchmark.csv` 记录各自的状态。

一个项目**合法**,当它用自己的工具链构建+验证、其构建协议能重放它(§A)、其验证协议
在项目自身**和**重放上都得绿(§B)。`fetch-benchmark-project` 技能负责准入一个项目:
按最小缺失编号编号,并写入它那一行。

当前状态见 `benchmark/benchmark.csv`;项目按准入顺序阅读,失败的项目就是下一个要补的
缺口——不是要丢掉的项目。

## 怎么跑

```bash
# 包自身 + 技能的单测
python3 -m pytest -q

# 工具:一个子命令一个原子操作。用它们,别自己拼一份等价的——
# 第二份实现会在某个细节上和第一份分叉,而那个分叉在咬人之前看不见。
python3 bench.py tree     <benchmark dir>   # 哪个目录是源码树
python3 bench.py wipe     <benchmark dir>   # 清**本工具**的产物(不动项目文件)
python3 bench.py plan     <benchmark dir>   # 计划可执行吗 + 几条 drop/keep/mask
python3 bench.py verify   <benchmark dir>   # §B 裁决
python3 bench.py evidence <benchmark dir> --shift-date   # 两次跑在哪里不一致
python3 bench.py row      <benchmark dir>   # 它在 benchmark.csv 的那一行
python3 bench.py fetch    <url>  [--name … --build … ]   # 首次准入
python3 bench.py admit    <benchmark dir> [--build … ]   # 重证一个已准入的

# §A 端到端,在一个真构建上
python3 -c "
from pathlib import Path
from protocolize.onboard.extract import extract_and_prove
proto, report = extract_and_prove('<build tree>', tree_root='<root>',
                                  work_root='<scratch>')
print(report.ok, len(proto.compilation))
"                     # report.ok 必须是 True
```

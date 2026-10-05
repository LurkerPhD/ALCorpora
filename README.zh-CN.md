# ALCorpora

> English: [README.md](README.md)

**ALCorpora 是 ALForce 底下的语料层** —— ALForce 是转译器;本仓做它自己做不到的
那一件事:取一个**真实项目**,证明它用自己的工具链构建成功、且它自己的验证是绿的
——于是 ALForce 是拿一个**已经通过**的语料来开发和判定的,不是拿手搓的样例。

把**任意项目**归一化成两个产物,每个都在**出口自验证**:

| 产物               | 回答的问题                                | schema                                 |
| ------------------ | ----------------------------------------- | -------------------------------------- |
| **构建协议** | 这个项目是怎么**构建**的?           | `private_protocol.Protocol`          |
| **验证协议** | 我们凭什么说它的一次运行是**对的**? | `verification_plan.VerificationPlan` |

没被**重建过**(构建协议)/ **跑过**(验证协议)的协议不是产物。Fortran 只是第一个
*profile*——这个仓库里不许出现语言名、构建系统名、项目名。

构建协议写成 `compile_commands.json` 的**数组**:最后一个元素之前的每个元素都是编译
条目(四个标准字段 + 本协议的额外字段),最后一个是携带「compile database 表达不了
的段」的节点。它可以原封不动地交给只看 cc 的消费者——数组结构永不被破坏,允许多加
条目。

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
                        (tree/wipe/plan/extract/capture/cases/verify/
                        evidence/row/fetch/admit);每个都委托,没有一个重写
admit.py                准入流程(编号/取源/原生/§A/§B/记行)
alcorpora/
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
    cli.py              CLI: python3 -m alcorpora.verify.cli <build tree>
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

一次准入,从头到尾。**`[PY]` 是工具确定性执行的一步;`[SKILL]` 是调用方给出的
逐项目知识;`SEAM`(接缝)是一步**失败**时流程自己去问 agent 的地方——而且只有在
"把它给的答案拿去重跑、那一步真的过了"之后才采纳。

```python
ADMIT_ONE_PROJECT(source):
    # [SKILL] A1 —— 逐项目的构建命令:关于**那个项目**的知识
    A1  configure, build, test, tree_subdir = how_it_builds(source)

    # [PY] 一次调用;以下管线是**脚本**,只在某步失败时由流程打开 SEAM
    P1  调用:  python3 bench.py {fetch <source> | admit <dir>} <args...>
    P2  n, dest = 最小空缺编号, benchmark/{n:03d}-{name}
    P3  fetch(source -> dest/inner)              # clone/copy,带重试;submodule
                                                 # 随取源一起检查并拉取
    P4  src = inner_source(dest)                 # 按"它持有什么"选,不按名字
        clear_previous_build(src, tree_subdir)   # 仅重证时;in-source 不删目录

    # P5-P6 原生:项目自己的工具链、自己的验证
    P5  应用 prepare_ops                         # 幂等 {op, check, run}
    P6  跑 (configure, build, test)              # 项目自己的命令
        失败 -> SEAM A

    # P7-P8 §A:构建协议,靠"重放它"来证明
    P7  proto = extract_protocol(build)          # 问构建系统自己的子工具
    P8  self_check(proto)                        # 镜像;删生成物**和产物**
        读不了 / 重放不出来 -> 工具缺口           # 扩抽取器规则 + 一个测试

    # P9 验证协议:从项目自己的声明里读
    P9  plan = capture_verification(proto, declaration, facts)
        不可执行 -> SEAM D

    # P10 golden 是"做出来"的,绝不"借来"
    P10 goldens = ensure_reference(plan)         # 仅 reference_run
        若为 0 -> SEAM D

    # P11-P12 §B 跑两次:项目构建上 与 重放上 的裁决必须**一致**
    P11 verify(project)      失败 -> SEAM E
    P12 verify(replay)       失败 -> 工具缺口     # 协议与构建不一致

    P13 record(PASS, language, notes)            # 只有走到这里才算 legal


# ══ SEAM(接缝):流程停下,问 agent,**只采纳"重跑能证明"的答案** ══════════

SEAM(seam_id, what_failed):
    answer = ask_agent(seam_id, what_failed)     # 类型化契约(seams.KINDS)
    if answer.kind == protocol_gap:
        上报并停止                               # 协议本身要扩;绝不重试
    adopt(answer); 重跑刚才失败的那一步          # **那一步过了**才被采纳
    同一次运行里,同一个失败不会问第二遍;轮数有界
    agent 不可达 -> 接缝关闭,该步照原样失败
        # agent 不可达**永远不是**项目的裁决

# 三个接缝,各自接受什么:
#   A  P6 失败(构建/验证)          -> native_commands | prepare_ops
#   D  P9/P10(计划 / 无 golden)     -> facts | declaration
#   E  P11 失败(项目上的 §B)        -> prepare_ops | facts | declaration
# 任一接缝都可改答 protocol_gap:"协议表达不了这件事"
```

### 每一步在做什么

**`[PY]` —— 管线(脚本;P6/P9/P10/P11 失败时打开接缝)。**

* **P1** `bench.py {fetch|admit}` —— 单一入口。`fetch` 是首次准入(编号、取源、
  证明);`admit` 是重证一个已在 `benchmark/` 里的。合成一个命令,流程就只有一份定义。
* **P2** 编号与目标目录 —— 最小空缺编号就是准入顺序,目录是 `benchmark/<NNN>-<name>`。
* **P3** 取源 —— 浅 clone(带重试,链路不稳)或本地拷贝。失败的拷贝会留下**空目录**,
  必须删掉,否则它白白占掉一个编号却不是项目。**submodule 随取源一起进来**:
  取源没拿到 submodule 就不算取完,所以这一步先查树里有没有 vendored 依赖,
  有才执行 update,再查一次,直到 git 报告它们到位为止 —— 没有 submodule 的树
  一分钱不花。放到后面任何地方都只是把同一条命令挪到更差的位置:它会变成"要诊断的
  症状",而不是"取源的一部分"。
* **P4** 找到树、清上次构建 —— 树是**持有一个构建系统**的那个目录(绝不按名字)。
  out-of-source 的构建目录整个删;in-source 的只删构建系统生成的状态,**绝不删源码**。
* **P5** 应用 prepare ops —— agent 诊断出来的补丁,每条 `{op, check, run}` 且幂等:
  `check` 决定是否已经打过,重复执行不花钱。
* **P6** 项目自己的构建与验证 —— 即逐项目的命令(A1)。失败就**停在这里**,
  失败就打开 SEAM A,打印出来的日志随之交过去。
* **P7** 抽取协议 —— **问构建系统自己的子工具**(`ninja -t compdb`、`make -Bn`),
  **绝不解析清单文件**。选哪个后端由树里持有什么决定。
* **P8** 证明它 —— 镜像树、删掉生成源**和产物**(逼重放去"做"出来)、**只从协议**
  生成一份构建、编译、逐产物按**已定义符号**比对。这一步才让协议成为**产物**,
  而不是一份描述。
* **P9** 读出验证协议 —— 从项目**自己的声明**:验收方式(注册测试 | 用例清单 |
  每用例一目录)、比较规则(`drop`/`keep`/`mask`/`rtol`/`atol`)、程序运行所需的事实。
  计划若不可执行就拒绝。
* **P10** 做出 golden —— 让 **reference 实例**跑同一个计划,留住它的输出。
  是"做出来"的,不是"借来"的:仓库自带的参考输出来自另一台机器、另一个年代。
* **P11** §B 跑在项目自己的构建上。
* **P12** §B 跑在**重放**上 —— 两个裁决必须一致。同一个计划跑在两个构建上,
  不一致就是**协议**的错。
* **P13** 记行。只有走到这里,项目才算 legal。

**接缝(SEAM)—— 流程停下,问 agent,只采纳"重跑能证明"的答案。**

接缝是"确定性的某一步失败了、而没有规则能决定怎么办"的地方。**流程自己去开它**:
把失败和树交给 agent,拿回一个**类型化**答案 —— 而这里最吃重的一点是:**只有把它拿去
重跑、那一步真的过了,才采纳。** 没让那一步通过的答案,会被"那一步又失败"推翻,所以
没有任何东西是凭 agent 一句话被接受的。同一次运行里同一个失败不会问第二遍,轮数有界。

* **SEAM A**(P6 失败)—— 构建或验证没过。接受 `native_commands`(真正能建它的命令)
  或 `prepare_ops`(它先要打的补丁,每条 `{op, check, run}`,幂等)。
  **没让构建通过的"修复"绝不记录。**
* **SEAM D**(P9/P10)—— 计划不可执行,或参考跑什么都没采到。接受 `facts`(程序
  **能跑起来**需要、而树里看不出的事实:home 目录、资源目录、程序配置)或
  `declaration`(项目**自己的**比较规则:`drop`/`keep`/`mask`、`rtol`/`atol`)。
* **SEAM E**(P11)—— §B 在项目自己的构建上不绿。接受 `prepare_ops`、`facts` 或
  `declaration` —— 一个能靠补丁解决的项目问题。

任一接缝都可以改答 **`protocol_gap`**:"协议表达不了这件事"。它**不重试、不猜**:流程
记录后停下,因为扩协议是**工具**的事,不是 agent 的事。

**不是所有"停下"都是接缝。** §A 读不了这种构建、或重放与项目不一致 —— 那是**工具**的
缺口,答案是把抽取器/比较器**扩一条规则 + 一个测试**,绝不手写一份协议或计划绕过去。
接缝够不到 agent 时同理:该步照原样失败,而那**永远不是**项目的裁决。

**唯一不归接缝管的一步是 `research_how_it_builds`(A1)** —— 一个项目的原生工具链是
**关于那个项目**的知识,把猜测编码进工具就是把项目知识推进了工具。它由调用方提供,
和项目自己的声明完全同类。

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

`selfcheck` 比的是**产物**。每个产物规定四项检查(下面这张实测表决定了
什么该红、什么不该红):

1. **defined symbols**(`nm -g --defined-only`;滤掉 archive 成员头和版本化符号);
2. **规范化指令序列**(`objdump -d --no-show-raw-insn`):去掉地址列和
   `# <addr> <sym>` 注释,`0x…(%rip)` -> `(%rip)`,
   `call/jmp 0x… <sym>` -> `call/jmp <sym>`;
3. **`.data` 原始字节**(`objcopy -j .data`)——逮住只看代码时看不见的 `DATA` 常量;
4. **section 清单**(`readelf -S` 的名称+类型;**不含大小**)。

| 构造                             | 整文件 | 规范化 .text | .data        | 结论   |
| -------------------------------- | ------ | ------------ | ------------ | ------ |
| 同代码,路径**长度不同**    | 差     | **同** | 同           | EQUAL  |
| `-O0` vs `-O2`               | 差     | **差** | —           | DIFFER |
| `__DATE__`/`__TIME__` 差一秒 | 差     | **同** | 同           | EQUAL  |
| `data x /1/` vs `/2/`        | 差     | 同(盲)       | **差** | DIFFER |
| section 基址平移                 | 同     | 同           | 同           | EQUAL  |

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
        |     |- deck_root 从树里推(不一定是列表所在目录)
        |     `- 报告的扩展名从**已存的参考产物**里观测,`.bdf` 的 deck 写
        |        `.F06` 就不会被当成"写在输入旁边"(有声明时声明优先)
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
按最小缺失编号编号,并写入它那一行。准进来的项目就是 **ALForce** 拿来做开发的语料。

当前状态是 `benchmark/benchmark.csv` —— 19 个项目全部 PASS,由 `sh regress.sh`
从裸 checkout 重新推导(清空 → 重证 → 全 PASS)。项目按准入顺序阅读,失败的项目
就是下一个要补的缺口——不是要丢掉的项目。

## 怎么跑

```bash
# 包自身 + 技能的单测
python3 -m pytest -q

# 工具:一个子命令一个原子操作。用它们,别自己拼一份等价的——
# 第二份实现会在某个细节上和第一份分叉,而那个分叉在咬人之前看不见。
python3 bench.py tree     <benchmark dir>   # 哪个目录是源码树
python3 bench.py wipe     <benchmark dir>   # 清**本工具**的产物(不动项目文件)
python3 bench.py plan     <benchmark dir>   # 计划可执行吗 + 几条 drop/keep/mask
python3 bench.py extract  <tree> [--out F]  # §A:从已构建的树读出协议并证明它能重放
python3 bench.py capture  <benchmark dir> --declaration F [--facts F]   # 读出验证协议
python3 bench.py cases    <benchmark dir>   # 计划发现的用例 id
python3 bench.py verify   <benchmark dir>   # §B 裁决
python3 bench.py evidence <benchmark dir> --shift-date   # 两次跑在哪里不一致
python3 bench.py row      <benchmark dir>   # 它在 benchmark.csv 的那一行
python3 bench.py fetch    <url>  [--name … --build … ]   # 首次准入
python3 bench.py admit    <benchmark dir> [--build … ]   # 重证一个已准入的

# §A 端到端,在一个真构建上
python3 -c "
from pathlib import Path
from alcorpora.onboard.extract import extract_and_prove
proto, report = extract_and_prove('<build tree>', tree_root='<root>',
                                  work_root='<scratch>')
print(report.ok, len(proto.compilation))
"                     # report.ok 必须是 True
```

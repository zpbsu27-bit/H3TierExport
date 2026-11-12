# H3TierExport 中文用户手册

版本：1.3.42（2026-11-11）  
适用对象：首次使用 H3TierExport 的美术、技术美术、素材维护者和测试人员。

## 1. 这个软件做什么

H3TierExport 是《英雄无敌 III》素材的本地处理、检查和交付工作台。它把高清母图按官方资源槽位、canvas/crop/origin 和固定坐标处理成可核验的候选，批量检查动画、道路、河流、地面、透明边缘和资源来源，并保存可复跑的报告。

它不是生图软件，也不负责自动决定画面艺术质量。AI 或美术人员提供画面后，软件负责核对身份和来源、按原生规则处理、执行可量化检查，并把结果与输入 SHA 绑定。工具检查通过不等于用户审美批准、已写入游戏、实机运行通过或正式发布。

常用工作流程是：

```text
选择官方身份、槽位和可信来源
  → 冻结输入清单及 SHA
  → check 预检
  → run 在隔离目录生成候选或报告
  → verify 复算并核对来源、像素和回执
  → 人工审图／按资源 owner 的正式写入流程交付
```

发行版 GUI 与冻结 companion 共用 H3TierExport 产品根；即使 EXE 从 dist 启动，实验输出仍进入产品根的 build/experiments，不会落进 dist/build。大多数 companion 命令会把候选写入 `build/experiments/<功能>/<operation-id>/`。同一个 operation 不会静默覆盖不同内容；输入或结果变化时建立新的 operation。只有明确提供 `restore` 的资源专用流程才会按其回执写回母版目录。

## 2. 下载、解压和首次启动

发行目录位于：

```text
H3TierExport\dist\
```

**下载时保留 `dist` 的全部内容和同级 `.runtime-<SHA>` 目录。** `.runtime-<SHA>` 里是随版本核准的 Python companion 脚本与资源；不要只拷贝 `H3TierExport.exe`，否则本地素材抢救、全槽审计、地图拼接等 companion 功能会报告找不到运行负载。不要把当前版本的运行目录与另一个版本的 EXE/收据混在一起。

发行目录主要文件：

| 文件 | 用途 |
|---|---|
| `H3TierExport.exe` | x64 图形界面和命令行入口 |
| `.runtime-<SHA>/` | 与 EXE 哈希绑定的冻结 companion 运行负载 |
| `H3TierExport-使用说明.md` | 本手册的发行副本 |
| `H3TierExport-release.json` | 发行版本、EXE、运行负载和文件身份记录 |
| `H3TierExport-build-receipt.json` | 编译来源和 EXE SHA 回执 |
| `H3TierExport-tests-receipt.json` | 对应 EXE 的自动化测试回执 |
| `H3TierExport-workspace.json` | 本机默认 H3 根目录绑定，属于受校验的发行文件，不要手工改它 |
| `H3TierExport.ico`、`H3TierExport-icon.png` | 图标资源 |
| `H3TierExport-第三方声明.txt` | 第三方组件许可声明 |
| `archive/` | 旧发行版归档；当前运行不需要，可单独保存作回滚资料 |

双击 `H3TierExport.exe` 打开界面。首次使用可以先运行：

```powershell
.\H3TierExport.exe info
.\H3TierExport.exe capabilities
.\H3TierExport.exe selftest --require-gpu
```

`info` 查看版本、默认采样、GPU 状态和硬件信息；`capabilities` 输出完整 JSON 命令目录；`selftest` 检查 CPU/GPU 图像内核一致性。`--require-gpu` 会要求 GPU 检查实际通过。基础 CLI 与原生导出内核不依赖 Python、CUDA 或在线 API。

### Companion 命令的依赖

地图拼接、素材抢救、类别尺度、批量语义审阅等高级 companion 需要 Python 3.10 或更新版本，并使用命令声明的模块（通常为 `numpy` 和 `Pillow`）。单帧姿态预测还需要 `onnxruntime` CPU：

```powershell
py -3 --version
py -3 -m pip install numpy Pillow
```

源码构建若已安装 `build/creature-auto-pose-provider/venv/`，`rebuild.cmd` 会优先使用其中含 `onnxruntime` 的 Python；否则回退到默认 Python。完整回归包含姿态 companion 的入口合同，所选 Python 也必须能导入 `onnxruntime`。

如果电脑上有多个 Python，可以用 `--python` 指定解释器：

```powershell
.\H3TierExport.exe scale-audit --python "C:\Python311\python.exe" --help
```

Blender 动作功能还需要本机 Blender；RIFE 插帧实验需要已绑定的 RIFE 可执行文件和模型；云端候选依赖各服务自己的账号和凭据。这些是可选功能，不影响基础导出。

### 生物自动身体描点（人形辅助）

以下两个入口已接入源码和候选版；当前正式 `dist` 尚未包含它们，待完整发布门禁通过后更新。候选点位仍需人工复核，不自动批准美术、尺度或游戏效果。

在“批量任务中心”选择 `自动身体描点 creature-auto-pose`，选一张真实透明 RGBA PNG 或由 `scale-audit freeze` 生成的官方全槽 JSON，选“run”并开始；之后可选“verify”复核。单帧没有官方清单时仅登记图像 SHA，不能声称已绑定 DEF 槽。全槽清单必须与当前 inventory、各槽图片、文件列表和原生几何一致；变更后重新冻结。结果在 `build/experiments/creature-auto-pose/<operation-id>/review.html`，源 PNG 不会被改写。

本功能使用单独安装于 `build/creature-auto-pose-provider/` 的 Python 3.13、ONNX Runtime CPU 和 SHA 固定的本机 RTMPose 模型，不安装到 ComfyUI。若本地 provider 缺失或模型 SHA 改变，安装/修复后才可运行；模型与训练数据的再分发许可另行核实，不能把本机试用写成已获再分发授权。所有推理离线执行，其他游戏、客户端及母版目录均不改动。

红点是原图自动估计，蓝点是翻转图估计映回的位置；不一致、遮挡和低分均需审阅，二者接近也不保证点位正确。结果是 `AUTO_ESTIMATED_REVIEW_REQUIRED`，不自动确认身份、肢体数量、身体尺度或艺术通过。只有显式指定同姿势组和参考帧时才计算等比尺度及相对位移预览；预览以估计髋部对齐，会遮蔽真实位移，既不是母版，也没有绑定原版地面或原生采样 K。龙、坐骑等非人形不由此人体模型自动验收。真实注册仍使用已核对的身体标注、原生几何和明确根部轨迹，经现有无损注册与 3X 管线处理。详情见[生物尺度与动作序列工作流](生物尺度与动作序列工作流.md)。

骑马英雄选择 `骑马英雄描点 hero-mounted-pose`：额外用独立的 AP-10K 动物模型估计马体，人体模型估计骑手。红色为马体候选、蓝色为其翻转复测、绿色为骑手候选；骑手搜索区域来自马体估计，遮挡或正背面可能明显偏离。该功能同时显示两套点位和分歧，仍不能证明马和骑手的尺度、根节点或动作正确。批量处理 AH 身份时用本命令的 `freeze` 从官方 inventory 与实际目录冻结，例如 `--identity AH00_`，再把生成的 JSON 载入任务中心。动物模型同样仅作本机试用，训练数据和模型再分发条款未作结论。

报告会统计 8 邻接意义下整块 Alpha 均为 1 的游离分量，提示可能影响原生槽 FIT 的透明噪点。它只检测，不擦除像素；与主体连接的半透明边缘保留。具体是否为可忽略的背景杂点、是否需要允许的无插值裁切或重绘，仍须结合真实画面及来源判断。

### 指定本机的 H3 数据目录

默认工作区绑定只适用于随发行生成的机器。如果你的 H3 工程在其他目录，在启动程序前设置 `H3_TIER_EXPORT_H3_ROOT`，指向包含 `h3-paths.json` 的 H3 根目录：

```powershell
$env:H3_TIER_EXPORT_H3_ROOT = 'D:\Games\H3-Project'
& 'D:\Apps\H3TierExport\H3TierExport.exe'
```

这个变量只选择本机数据根目录，不替代清单中的源文件 SHA、官方 inventory 或身份槽位核验。删除 PowerShell 窗口后变量失效；需要长期使用时，可在 Windows 用户环境变量中设置。未设置时软件使用发行时绑定的默认 H3 根目录。

## 3. 图形界面快速上手

主窗口提供任务、母版扩展、动画预览和硬件测试。处理一批素材时打开“批量任务中心”：

1. 在“任务”下拉框选择功能。
2. 点击“浏览…”载入该功能要求的 JSON 清单；SHA-256 会显示在输入框中。
3. 对支持阶段的任务选择 `check`、`run` 或 `verify`。
4. 核对 Operation ID 和输出目录。输出默认为产品隔离目录下的新操作目录。
5. 点“开始任务”。结束后点“打开输出目录”检查 JSON/Markdown/HTML 报告和候选。

任务面板中的三个阶段含义：

| 阶段 | 做什么 | 是否写图 |
|---|---|---|
| `check` | 核输入 SHA、槽位、几何、依赖和运行计划 | 不写候选图片 |
| `run` | 运行已经冻结的处理/测量流程 | 只写本次隔离输出 |
| `verify` | 独立复算报告、注册、图像字节或像素 | 不修改原图 |

`motion-review` 是审阅实际运行报告/集合的只读任务，属于 run-only。其他任务若输入 SHA 或文件集合变化，应重新冻结新任务；不能复用旧报告冒充当前结果。

动画预览器可以打开 PNG 帧目录，按 DEF 名、动作组和帧号分组。选择“逐帧适配”或“整组同倍率”观察画面；同倍率模式更适合看跨帧主体尺度。它用于审阅，不会重写帧图。APNG 只适合临时播放；项目当前只交接一套正式 3X PNG，不把 APNG 当作游戏资源。

## 4. 软件功能和适用场景

### 母版导出与原生几何

- **VCMI DEF 精灵打包门**（campaign_vcmi_mod.py 的 build 阶段）：每个 DEF PNG 都重新对照官方 1999 native inventory，要求 RGBA 完整逻辑画布×3；按该帧自己的 crop/origin 校验 Alpha 内容没有跑出官方裁片，并把 canvas、crop、origin、3X 裁片矩形和 PNG SHA 写入回执。裁成 crop×3、画布尺寸错误、透明内容错误居中到裁片之外、原生几何不匹配，都会在候选目录创建前拒绝。识别出官方 5 个静态组 + 5 个 8 帧组的骑马英雄时，必须一次提交全部 45 个官方槽位。
- `map-sprite-mod`：面向 AH00_–AH17_ 地图骑马英雄的独立 VCMI MOD 打包器。`freeze` 同时绑定 `expand-batch`、成功的 `expand-verify`、原生 1X authority、官方 native inventory 和 identity checkpoint；随后 `check → run → verify` 逐槽复核完整45槽、母版 K、canvas×3、Alpha 与官方 crop/origin 的范围，并逐字节复制已经复验的 PNG。crop×3、错居中、缺槽、重复槽、额外槽、错 descriptor 和来源漂移都会在新候选目录创建前拒绝。候选仅在 `build/experiments` 生成，不安装或改写现有 MOD。
  `H3TierExport.exe map-sprite-mod freeze --identity AH03_ --batch <expand-batch.json> --batch-sha256 <SHA> --verify-report <expand-verify.json> --verify-report-sha256 <SHA> --operation-id ah03-map-mod-r01 --candidate-root <build/experiments/新目录> --mod-id h3hd-map-ah03 --out <新请求.json>`
  冻结后用返回的请求 SHA 依次运行 `map-sprite-mod check --manifest <请求.json> --manifest-sha256 <SHA> --operation-id ah03-map-mod-r01`、`run`；最后 `verify --manifest <请求.json> --manifest-sha256 <SHA> --operation-id ah03-map-mod-r01 --receipt <回执.json> --receipt-sha256 <SHA>`。

- `plan / check / approve / run / verify`：冻结图片批次，核对来源与目标几何；用户必须为精确任务记录明确批准后，才可运行批准计划。
- `expand-plan / expand-check / expand / expand-verify`：从已绑定的真实来源制作指定尺寸的扩展候选，并核验。扩展只是像素重采样，不产生新的绘画细节，不能把低倍率素材冒充高清母版。
  - 静态建筑带动态特效的整组帧可在图形母版扩展设置中勾选“静态建筑 + 动态特效”，或为 CLI 加 `--animation-contract static-structure-dynamic-fx` 并在同一次 `expand-plan` 中重复 `--source` 列出整组帧。工具会按身份目录和 DEF 组号归组，使用首帧作锚点，对比各帧的母版 Alpha 框及压制后落点；3X 落点或宽高出现任何整数像素差，或拟合比例相差超过 0.1%，计划会被拒绝。错误会列出异常帧、首帧、母版 Alpha 框、原生参考轮廓框、3X 拟合框及 X/Y/宽高/比例差，并建议固定建筑框或为整组使用相同参考轮廓。`expand-check`、`expand` 和 `expand-verify` 会重算该合同，不能通过改批次绕过。
  **母版只读**：扩展绝不裁切、补边、重绘或另存母版本身；必须等于"官方原生尺寸×目标倍率"的是**输出**，所以母版自身比例与官方槽位差 ≤5% 时，按官方尺寸输出并在收据里记 `fit_mode`/`aspect_error_pct`；差 >5% 说明这张母版不属于该槽位，仍然拒绝，必须先去 `scale-layout` 做真实几何注册。
  **只有一条质量通路**：扩展固定使用 `--filter h3-quality`（`linear-premultiplied-lanczos3-ewa-alpha-ar-clarity-v4`）。旧的低质量采样器 `linear-premultiplied-lanczos3-local-range-v1` 已不可选：`expand-plan --filter lanczos3` 直接拒绝，已封存该算法的旧批次在 `expand-check`/`expand` 下也整体失败、零写入；它只在 `expand-verify` 复核历史收据时按原算法回放，不产生新字节。
  **画布不许垫边**：比例门只看画布外框，看不见画占了多少。母版若"一轴两端都贴边（<0.5%）、另一轴两端各留 ≥2% 且两侧相差 ≤15%"，说明画布是被人为加宽/加高去凑比例的，输出会让画面在官方格子里缩水；`expand` 在解码之后、写盘之前直接拒绝且零写入，收据固定记 `artwork_bands_pct`（左/右/上/下留空比例）与 `padded_master_allowed`。阈值故意收紧：单侧留空、或两侧留空不对称（道路拐弯、山峰被槽位裁掉一边）属于正常构图，一律不拦。正确处置是重新派生母版、让画面铺满画布（把原画横向或纵向拉伸到目标比例即可，实测"拉伸再均匀压"与"一步非均匀压"只差 0.05/255，横向锐度到小数点后三位一致）；不要用补边或镜像——不规则轮廓镜像会连轮廓带投影复制一份，成品两侧会各出现一条接缝。确有理由照旧压制时用 `expand-plan --allow-padded-master`，该开关封进任务指纹，事后从批次里删掉它复核会报"expanded-master receipt missing"；已压制完成的历史批次因收据短路不受影响。
- `native-review`：按官方槽位 canvas/crop/origin 显示候选和原生参考，生成诊断，不自动批准画面。
- `motion-plan / motion-check / motion-probe / motion-run / motion-verify`：对显式给定的图层网格、2D 骨架或原生 ROI 处理动作候选。对应点、根部和关键帧必须有真实来源；不能只靠提示词猜坐标。

### 类别尺度、母版槽位和生物动作

- **`scale-draft`**：按步兵、法师、骑乘单位、大型生物，从 SHA 绑定的原生 inventory 生成画前尺度草案。骑乘单位衡量骑手、坐骑、装备整体。没有核准样本或 landmarks 时显示 `DRAFT / NOT_MEASURED`，不猜目标尺寸。
- **`scale-audit`**：制作后列出官方全槽缺漏、命名、母版 SHA、canvas/crop/origin、透明框、采样 K、绑定的解剖标点、正式 3X 与运行状态。bbox 只诊断留白/FIT，不代表身体尺寸，也不自动缩放。
- **动作连续性复核**：`scale-audit` 可选绑定 SHA 锁定的 `h3.creature.motion-review.v1` 复核记录，验证帧顺序、所有相邻转场、loop/non-loop 分类、身份/解剖/装备/材质/动作逻辑条目以及风险说明是否完整、是否对应当前 PNG。它校验已填写的审阅记录，不从单帧自动识别动作含义。
- **`scale-layout`**：处理人物序列无损注册、类别质量档案、道路批检和地图素材审核入口；如有问题的画面放不进原生槽，流程报告冲突，不靠单帧拉伸掩盖。
- **`blender-actions`**：按已绑定的模型、骨架、Action、相机和官方槽运行；声明 `motion_consistency` 后检查相机是否固定、刚性段尺度和逐槽 root 轨迹。没有合同则报告未测量。它不自动判定动作叙事、接触关系或游戏时序。
- **`creature-production` / `creature-master-set` / `creature-actions`**：核对生物官方帧队列、来源、别名、导出和整组覆盖。归一化只产生工具隔离候选，母版原图保留。
- **`sheet-intake` / `native-alias`**：整张帧图映射到官方槽位，或在真实二进制证据支持时复用别名。未知归属不能靠文件名猜。

### 道路、河流、地面和地图素材

- **`map-material-family`**：从共享材质和原生轮廓生成供研究的处理候选，并检查部分邻接与开口；原生低分辨率蒙版的放大可能造成方块轮廓和材质分界，几何结果不能证明美术质量。候选不得当作原始高清母版，`restore` 已禁用；历史 `FAMILY_PASS` 不代表合格或可交付。
- **`map-source-batch`**：审核并精确注册已找回的道路/河流原始高清父图。只按有理数原生网格直接采样 3X，不先缩小/拉伸母图；要恢复原父图必须通过原版邻接、开口连通、边界和 SHA 检查。
- `map-source-batch` 恢复判定还必须采用彩绘公共接口和合法配对的实际结果。只有端口尺寸／连通通过，不能获得 `RESTORE_CANDIDATE`；邻接失败、彩绘接口未验证或证据缺失时显示 `REVIEW_REQUIRED`，不会自动当成需要 AI 重绘。新 freeze 会绑定当前模型注册表中已经核验的道路接口合同。verify 重新计算彩绘配对；restore 只接受当前算法及与确切报告 SHA 绑定的验证。旧报告须重新冻结到新 operation 后检查，不修改原报告、图像或旧回执。
- **`scale-layout road-batch-*`**：针对官方道路 3X 集合，以地图槽、镜像、半格绘制规则和合法邻接表检查端口、开口和接缝；输出配对审阅页。缺少编辑器规则或彩绘接口证据时显示未测，不伪报通过。
- **`scale-layout quality-init` 的原生蒙版放大检测**：道路／河流批量检查可增加 `--native-mask-root <原版帧目录>`，目录内按 `DEF名/组_帧.png` 提供已核实的原版帧。软件冻结每张参考的路径与 SHA，检查母版 Alpha 是否与该低分辨率 Alpha 的最近邻整幅放大完全一致；一致报告 `RISK / EXACT_NATIVE_MASK_NEAREST_UPSCALE`，参考缺失或源尺寸不足时报告 `NOT_MEASURED`。未命中只说明没有检测到这一种模式，不能据此认定边缘自然、道路合缝或美术通过。
- 道路绘制简报会写明统一 2.5D 俯斜视角、光向和材质尺度；碎石路肩保持自然细微起伏、整体干净，可有零星碎石，避免密集散落或刀切般平滑。接口仍须按原生邻接、镜像与实际 3X 验证。
- `road-batch-*` 报告新增 `interface_equivalence_model`：从已绑定的合法横纵配对推导原始边的相等／反向关系和镜像对称约束，横竖接口不自动强制成同一组。该推导仅说明一种足够严格的接口约束；实际彩绘组件、3X 配对、底图场景和游戏仍分别验收。原版会镜像整张 RGBA，强单向阴影也会翻转；当前简报采用环境光和短接触阴影，不宣称已实现固定世界光源重打光。
- 公共彩绘接口支持 `h3.road-painted-interface.v2`：把横向 `east` 与纵向 `south` 的既有 v1 合同分别放进 `axis_contracts`。两者可使用不同的高清父图、材质方向和接口带深度；必须来自同一道路身份，绑定同一原生 inventory，且各自接口在允许镜像下兼容。旧 v1 仍表示全部方向使用同一参考接口带。缺轴、串身份、来源漂移或任何一轴接口损坏都会阻断，不以另一轴通过掩盖。
- **`scale-layout map-review`**：读取 SHA 绑定的 H3M 地形表，按原生地图格回放当前地图；`actual3x_layers` 可要求道路、河流或两者使用正式 3X 裁片。旧清单省略时默认保留道路 3X 兼容行为。报告逐项列出道路/河流布置数、缺图和相邻诊断；只写 JSON/HTML，不保存 1X、2X、拼板 PNG，也不声称游戏实机通过。
- **`map-synth` / `map-artifact-*` / `tile-design` / `tile-interface`**：确定性地生成地图素材候选、对象组合、几何模板或共享构件。几何通过不代表颜色和艺术质量通过。

### 官方单帧地图物件扩展成动画

`map-object-sequence` 把一个**官方原本只有 1 帧的冒险地图物件 DEF 组**扩展成多帧 VCMI JSON 动画，并准备对应的 1X/3X MOD 覆盖候选。它只负责冻结来源、按帧名配对、生成两个 JSON descriptor 和静态 1X 回退图、校验尺寸/透明画面/帧序/哈希；不会生图，不改高清母版、正式 3X、MOD 或原始 DEF/LOD，也不会判定动作是否画得好。

适用范围严格限定为官方 native inventory 中 `adventure_objects` 类别、所选 DEF 组恰好 1 个原生帧的资源。若原组已经有多帧，工具会拒绝，避免扩展时误删/替换官方动画。身份与原生帧数从绑定的 `codex-map/native-resource-inventory.json.gz` 读取，1X 静态回退图还要与该身份的官方 Complete 检查点和原生图 SHA 一致。

#### 制作前要点

- **帧顺序**：输入目录顶层只放所选组的 3X PNG，按 `组号_帧号.png` 命名，帧号从 0 连续递增，例如 `0_0.png` 至 `0_14.png`。缺帧、重复号、混进其他组或额外 PNG 都会阻断。
- **画布**：每帧必须是官方完整 `canvas × 3` 的 RGBA PNG，不是 `crop × 3`。例如官方 canvas 为 96×96、crop 为 96×65、origin 为 0×31，则输入必须是 288×288；不能去掉透明留白，否则屋顶/地面等锚点会在帧间跳动。
- **两档资源必须配套**：产物包含 `content/Sprites/<ID>.json` 和 `content/Sprites3x/<ID>.json`，两份 `sequences` 里的文件名严格一致。每个 3X 动态帧都配一个同名 1X 官方原图副本，使低倍率解析也能完整建立序列；低倍率会显示静态原图，动画帧来自 Sprites3x。
- **`sequences` 会替换整组**：VCMI 的 JSON 动画 `sequences` 会用清单内容替换对应 group，即使原动画比清单更长。因此只允许官方单帧组使用本功能，且只写所选组；其他组保持原版。不要把 `images` 单帧覆盖格式当成扩帧格式。
- **帧时长与节奏**：当前 VCMI 冒险地图物件按每帧 180 ms 选帧。总循环时间 = 输出帧数 × 180 ms；15 帧约 2.7 秒，30 帧约 5.4 秒。`--repeat-each 2` 会把每张关键姿态在相邻槽重复一次，15 张输入变 30 帧、每个姿态约停留 360 ms；重复只放慢姿态，不创造中间动作。目标 VCMI 分支改过时长逻辑时，以该分支源码为准。
- **逐帧人工审图仍不可省**：看主体身体大小是否稳定、轮廓有没有突然缩小/变大、脚和地面是否滑动、透明区是否意外带底色、首尾循环是否跳变。翅膀或脖子这种局部动作应连同静止主体一起检查。工具的 `VERIFIED` 只证明文件、几何和 descriptor 一致，不证明视觉连续、实机播放或美术验收。

#### 冻结、运行与复验

先用与候选帧配套的 H3TierExport，在 CLI 冻结请求。`--frames-dir` 指向含 `0_0.png` 等帧的目录，`--repeat-each` 默认 1：

```powershell
.\H3TierExport.exe map-object-sequence freeze `
  --identity AVXBNK30 --group 0 `
  --frames-dir "D:\素材批次\AVXBNK30\scaled-3x-candidate" `
  --operation-id AVXBNK30-neck-idle-r03 --repeat-each 1
```

命令返回冻结 manifest 路径和 SHA。把 manifest 载入任务中心，选择 **地图物件单帧扩展为动画 `map-object-sequence`**，依次执行 `check`、`run`、`verify`。也可 CLI 操作：

```powershell
.\H3TierExport.exe map-object-sequence check `
  --manifest "<冻结manifest.json>" --manifest-sha256 <SHA> `
  --outdir "{H3TierExport}\build\experiments\map-object-sequence\AVXBNK30-neck-idle-r03" `
  --operation-id AVXBNK30-neck-idle-r03
.\H3TierExport.exe map-object-sequence run `
  --manifest "<冻结manifest.json>" --manifest-sha256 <SHA> `
  --outdir "{H3TierExport}\build\experiments\map-object-sequence\AVXBNK30-neck-idle-r03" `
  --operation-id AVXBNK30-neck-idle-r03
.\H3TierExport.exe map-object-sequence verify `
  --manifest "<冻结manifest.json>" --manifest-sha256 <SHA> `
  --outdir "{H3TierExport}\build\experiments\map-object-sequence\AVXBNK30-neck-idle-r03" `
  --operation-id AVXBNK30-neck-idle-r03
```

`run` 只在新的隔离目录生成 overlay 候选。复验通过并完成人工审图后，再按对应 MOD owner 的交付流程安装；本命令不会自动把候选覆盖进 MOD。输出里的 `sequence-report.json` 列出原生/新帧数、每槽 1X/3X 来源 SHA、画布和循环时长，`receipt.json` 记录候选文件 SHA。

此功能遵循 VCMI 官方 [Animation Format](https://github.com/vcmi/vcmi/blob/develop/docs/modders/Animation_Format.md) 的 `sequences` 组替换语义。当前每帧 180 ms 的依据是 VCMI [MapRendererContext.cpp](https://github.com/vcmi/vcmi/blob/develop/client/mapView/MapRendererContext.cpp) 的冒险地图物件选帧实现。
- **地图拼接与材质检测工作流**：道路/河流合法邻接、材质父图、几何模型和实际3X检查的技术参考，不代替任务清单与逐批验收。

### 地图编辑器布局与实际 3X 场景复核

- 编辑器布局证明：在《英雄无敌 III》地图编辑器中按原生地图格摆放道路、河流、地形和转弯/分支，保存为 H3M；配合 H3HD 工程脚本，把地图文件 SHA、每格资源类型/帧号/镜像位和地图尺寸冻结成布局证明。
- 正式 3X 回放：由布局证明、官方 native inventory 和当前正式 3X library 生成场景清单，再调用 scale-layout map-review。道路与河流都可选择正式 3X 裁片，按原生 canvas/crop/origin 及地图格变换合成；报告含缺图、实际绘制数、相邻接口诊断和 HTML 网格预览。
- 可视化审阅：HTML 预览可切换网格、帧号/镜像标记、游戏逻辑倍率和 3X 像素倍率。它只在内存合成并写 JSON/HTML，不生成 PNG，也不等于正式游戏运行测试。报告仍要求人工查看实际接缝；若缺 3X 槽会明确报告，不能把原版参考图误记成高清通过。
- 适用范围：当前 H3M 证明脚本只解析 SoD 版本 28、默认玩家/物件状态的编辑器地图，并沿已固定的 VCMI 读取顺序定位地形表；不支持的地图结构会拒绝，不会扫字节猜偏移。运行时还需 H3 工程中已固定并通过 SHA 核验的 VCMI 读取源码。它属于 H3 工程集成脚本，不包含在单独下载的 H3TierExport dist 中。

### 本地抢救和质量检测

- **`asset-repair`**：对 8 类适用缺陷使用版本化本地算子，如同父图切片、注册接口、温和材质对齐、静止层冻结、显式色键边缘去污等。先 `check`，再 `run` 和 `verify`；只写隔离候选，不覆盖原图。不能恢复真实绘画结构时报告需要重绘。
- **`alpha-benchmark`**：比较已冻结的透明帧候选；缺少对照或类别时保持未测量。
- **`checkerboard-alpha`**：对明确的棋盘底图候选执行安全抠除测试，输出新目录和报告；不原地修改输入。
- **`quality-profile-window`（任务面板“画前档案 / 一键检测”）**：准备类别生产档案并运行批量质量策略。它可发现格式、透明背景、槽位、色差、身体标点和候选问题，不是可靠的通用肢体/身份 AI。

### 补充专项命令

- tile-sampling-proof 只读复核中性 3X 瓦片样片的四向接口、边缘带和内部连通；它不证明彩绘质量或游戏运行结果。
- source-audit 按官方 inventory 检查母版来源是否足以生成原生 3X，不生成或修复图片。
- town-scene 按 SHA 绑定的 3X 背景和图层合成城镇场景；town-process-review 逐槽检查 SHA 绑定的城镇过程素材。
- motion-prepare 从已证明的帧序列冻结小型动作试验；official-motion-prepare 按官方帧索引准备复用/重注册候选；creature-actions 从 DEF inventory 输出实际动作组及完整帧队列。
- creature-master-set 检查生物官方槽位覆盖、来源和几何；native-alias 只在原生证据支持时核对别名帧；sheet-intake 检查整张帧表或有类型定义的分区表；这些报告不能代替动作语义和艺术验收。
- logical-alpha 和 logical-alpha-render 处理显式逻辑透明通道；它们生成的是遮罩数据，不是绘画素材。
- quality-review 已退役，禁止用于新批次；请使用绑定真实 3X 来源的 native-review。旧发行目录里的脚本或帮助文字不代表当前版本仍支持该入口。

### 审图、插帧与可选外部功能

- 同倍率帧组预览：选择“整组同倍率”后，所有帧按同一个显示像素比例查看，并显示 DEF/动作组/帧号；“逐帧适配”则单独适配每一帧。两种模式只改变屏幕预览，不改变图片像素、画布、原点或交付结果。

- **`apng-preview`**：读取现有 PNG，按当前 DEF 命名播放；不写 1X/2X 变体。
- **`motion-review`**：对已产生的运动/审图报告或 collection 检查槽位覆盖、HOLD 间隔和 wrap 诊断。
- **`interpolate`**：可选的 RIFE 插帧试验；需已绑定外部 RIFE 和模型文件，结果是候选，不能替换原始官方帧合同。
- **`art-pipeline`**：本地提供器入口，运行和预览需本机安装并绑定的 Blender/节点组件。
- **`cloud-animation`**：可选云候选计划。提交/上传可能计费，脚本要求显式提交参数与凭据；不属于默认运行流程。
- **`defpack`**：隔离调用 H3DefTool 处理索引帧和检查原生 DEF。它不把 RGBA 母版转成另一种美术风格。

## 5. 最常用的 CLI 命令

在发行目录打开 PowerShell，以下命令均以当前目录的 `H3TierExport.exe` 为例。写入类命令的输出路径必须是新的操作目录；不要复用别人的输出目录。

下方 `<manifest.json>`、`<SHA>`、`<CompleteOfficialRelease>` 等尖括号内容是占位说明，执行前要换成实际路径/值，不要把尖括号原样粘进 PowerShell。

### 查询与检查

```powershell
.\H3TierExport.exe help
.\H3TierExport.exe info
.\H3TierExport.exe capabilities > .\capabilities.json
.\H3TierExport.exe selftest --require-gpu
.\H3TierExport.exe scale-audit --help
.\H3TierExport.exe map-material-family --help
```

`capabilities` 是本版本完整命令目录，含每个 companion 的精确参数和依赖；以它或 `<command> --help` 为准，不要用旧版本网页上的命令。

### 其他原生 CLI 入口

- ui-smoke：只检查 Win32 主窗口能否初始化，不点击界面，也不处理素材。
- bench：运行指定并发数、后端与作业数的采样基准；只输出性能报告。
- review-render --spec SPEC.json --spec-sha256 SHA --outdir NEW-DIRECTORY：按调用者提供的坐标/几何生成临时审图画面；几何由调用者负责，不能据此宣称官方槽位、艺术或游戏验收。
- logical-alpha-render --spec SPEC.json --spec-sha256 SHA --outdir NEW-DIRECTORY --operation-id ID：按显式 alpha 数据运行精确 3X 采样；结果是逻辑遮罩，不是彩绘母版。
- skill-variant-check、skill-variant-export、skill-variant-verify --manifest MANIFEST.json --manifest-sha256 SHA --outdir NEW-DIRECTORY：依次检查、派生和重放技能变体；只能引用已绑定的来源，不能改写原始 AI 母版。
- map-artifact-check、map-artifact-export、map-artifact-verify --manifest MANIFEST.json --manifest-sha256 SHA --outdir NEW-DIRECTORY：检查官方对象 ID、DEF 几何和来源；按官方地面阴影蒙版组合透明母版及显式动画高光，再独立复核输出像素。

### 生成普通图像批次并导出

```powershell
.\H3TierExport.exe plan --batches .\batches --out .\work\plan.json
.\H3TierExport.exe check --plan .\work\plan.json --report .\work\check.json
.\H3TierExport.exe approve --plan .\work\plan.json --id ASSET_ID `
  --evidence "我已查看该母版和确切裁片区域" --out .\work\approved-plan.json
.\H3TierExport.exe run --plan .\work\approved-plan.json
.\H3TierExport.exe verify --plan .\work\approved-plan.json
```

`approve` 针对所选精确母图/裁片合同记录用户审核证据；不能由模型或助手替用户签认。普通 plan/export 流程只用于确实符合计划 schema 的任务。

### 全槽 `scale-audit`

```powershell
.\H3TierExport.exe scale-audit freeze `
  --inventory "D:\H3\H3HD\assets\CompleteOfficialRelease-20261006-v1\codex-map\native-resource-inventory.json.gz" `
  --inventory-sha256 <官方inventory的SHA256> `
  --identity CGARCHER --category large_creature `
  --masters-dir "D:\H3\H3HD\assets\CompleteOfficialRelease-20261006-v1\codex-高清母版\生物\CGARCHER" `
  --operation-id cgarcher-audit-r01
```

把 freeze 输出的 manifest 路径、SHA 和 output 目录原样复制到 `check`、`run`、`verify`。运行例：

```powershell
.\H3TierExport.exe scale-audit check --manifest <manifest.json> `
  --manifest-sha256 <SHA> --outdir <新操作目录> --operation-id cgarcher-audit-r01
.\H3TierExport.exe scale-audit run --manifest <manifest.json> `
  --manifest-sha256 <SHA> --outdir <同一个新操作目录> --operation-id cgarcher-audit-r01
.\H3TierExport.exe scale-audit verify --manifest <manifest.json> `
  --manifest-sha256 <SHA> --outdir <同一个操作目录> --operation-id cgarcher-audit-r01
```

`--context` 可提供已批准样本、注册回执、landmarks 和动作审阅 JSON 的 SHA 引用。源图改变后 freeze 新操作；bbox 不会被自动当成身体尺度。

### 整组道路/河流 `map-material-family`

冻结阶段必须绑定共同高清材质、实际官方 inventory、官方 alpha 帧目录、规则/邻接证明、当前发布根和高清母版根。河流例（参数值按真实路径替换）：

```powershell
.\H3TierExport.exe map-material-family freeze `
  --operation-id clrrvr-family-r01 --identity CLRRVR `
  --texture "D:\materials\clrrvr-water.png" `
  --bank-texture "D:\materials\cobble-bank.png" --bank-width-native 2 `
  --inventory "D:\H3\H3HD\assets\CompleteOfficialRelease-20261006-v1\codex-map\native-resource-inventory.json.gz" `
  --native-frame-root "D:\H3\H3HD\assets\CompleteOfficialRelease-20261006-v1\codex-map\sources\complete\frames" `
  --rule-proof "D:\H3\H3HD\docs\road-river-repair-20261025\river52-tool-repair-r01\seams-r01.json" `
  --seam-auditor "D:\H3\H3HD\tools\verify_map_tile_seams.py" `
  --release-root "D:\H3\H3HD\assets\CompleteOfficialRelease-20261006-v1" `
  --master-root "D:\H3\H3HD\assets\CompleteOfficialRelease-20261006-v1\codex-高清母版\地图"
```

接着可按 freeze 输出执行 `map-material-family check → run → verify`，仅供研究和诊断。几何通过显示 `GEOMETRY_PASS_VISUAL_REVIEW_REQUIRED`；必须另查高清轮廓、材质分界、实际 3X 和场景拼接。`restore` 始终阻断（包括旧 `FAMILY_PASS` 回执），处理图不能写入原始高清母版。道路和河流各用自身的原生邻接模型；不能拿 CLRRVR 规则证明 COBBRD。

### 历史道路/河流源图批次 `map-source-batch`

```powershell
.\H3TierExport.exe map-source-batch freeze `
  --operation-id road-river-source-r01 `
  --road-prepared <road-prepared.json> --river-prepared <river-prepared.json> `
  --inventory <native-resource-inventory.json.gz> `
  --editor-proof <map-source-oracle-PASS.json> --seam-auditor <verify_map_tile_seams.py> `
  --release-root <CompleteOfficialRelease> --master-root <codex-高清母版\地图>
```

之后按回显 manifest SHA 执行 `check → run → verify`；只有审核决策为通过的原始高清父源才可执行 `restore`。源图裁切超出官方边界、接口宽度/中心不符或分支断开时记录重绘，不靠拉伸/模糊强行过关。

### 地图编辑器道路/河流实际 3X 回放

先在 H3 地图编辑器中启用网格，按游戏地图格布设道路和河流，保存一张用于审阅的 SoD v28 H3M。布局证明会读取地图实际保存的每格素材编号、方向/镜像位及地形表；它不会从截图推断布局。

下面示例中的 H3M、正式 3X library、inventory 和目录须换成本机本批文件。$work、$outdir 必须使用新的审阅目录；右下界 x1/y1 不包含在 viewport 内。示例按 72×72 地图查看整张地图：

    $h3 = '{H3-workspace}'
    $tool = '{H3TierExport}'
    $exe = Join-Path $tool 'dist\H3TierExport.exe'
    $helper = Join-Path $h3 'H3HD\tools\map_editor_h3m_review.py'
    $map = 'D:\maps\road-river-layout.h3m'
    $inventory = Join-Path $h3 'H3HD\assets\CompleteOfficialRelease-20261006-v1\codex-map\native-resource-inventory.json.gz'
    $library = 'D:\maps\this-batch\final-library.json'
    $work = 'D:\maps\review-road-river-r01'
    $proof = Join-Path $work 'editor-layout-proof.json'
    $scene = Join-Path $work 'scene-spec.json'
    $operation = 'editor-road-river-review-r01'
    $outdir = Join-Path $tool "build\experiments\map-assembly\$operation"
    function Get-Sha256([string]$Path) { (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() }
    python -B -X utf8 $helper inspect --map $map --map-sha256 (Get-Sha256 $map) --out $proof
    $viewport = @(0, 0, 72, 72)
    python -B -X utf8 $helper scene-spec --layout-proof $proof --layout-proof-sha256 (Get-Sha256 $proof) --inventory $inventory --inventory-sha256 (Get-Sha256 $inventory) --final-library $library --final-library-sha256 (Get-Sha256 $library) --viewport $viewport --out $scene
    & $exe scale-layout map-review --spec $scene --spec-sha256 (Get-Sha256 $scene) --outdir $outdir --operation-id $operation

打开输出目录的 index.html 检查整张地图和网格；同时查看 report.json 中的 road_placements、river_placements、missing_road_placements、missing_river_placements、missing_context_road_draws、measured_road_seams 和 measured_river_seams。完整场景中缺失项应为零，再逐格查看转弯、T/Y 分支及相邻接口。HTML 的 SCENE_RENDERED_REQUIRES_INTERFACE_AND_VISUAL_ACCEPTANCE 表示场景已绘制、还需接缝与人工画面验收；INCOMPLETE 表示有缺图/上下文道路，不能当作通过。此流程不启动游戏、不写客户端，也不会生成预览 PNG。

场景规格由 scene-spec 从当前正式 3X library 自动绑定道路与河流，并设置 actual3x_layers 为两者。若自己手写 map-review JSON，必须显式设置 actual3x_layers；旧规格省略此字段时为兼容旧行为只显示道路 3X。viewport 不超过 4096 个格，指定身份、inventory 几何或裁片 SHA 不符会在输出前阻断。

### H3 工程道路/河流正式交付（项目集成脚本）

H3HD/tools/map_interface_final_delivery.py 不属于独立 H3TierExport 发行包。它把七个静态道路/河流系列（COBBRD、GRAVRD、DIRTRD、CLRRVR、ICYRVR、MUDRVR、LAVRVR）的已验证 family 回执、当前 H3TierExport 3X 复验和既有 180 个河口/三角洲槽位的冻结 library 组合成唯一交付计划。下面的批次路径是占位值，必须换成当前输入；每个后续步骤都要传同一份 final-plan.json 和它当前的 SHA：

    $delivery = '{H3-workspace}\H3HD\tools\map_interface_final_delivery.py'
    $mapMaterials = '{H3-assets}\CompleteOfficialRelease-20261006-v1\codex-高清母版\制作资料\地图界面'
    $planDir = Join-Path $mapMaterials ('road-river-delivery-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
    $families = @('COBBRD={batches}\COBBRD', 'GRAVRD={batches}\GRAVRD', 'DIRTRD={batches}\DIRTRD', 'CLRRVR={batches}\CLRRVR', 'ICYRVR={batches}\ICYRVR', 'MUDRVR={batches}\MUDRVR', 'LAVRVR={batches}\LAVRVR')
    $familyArgs = @()
    foreach ($family in $families) { $familyArgs += @('--family-operation', $family) }
    python -B -X utf8 $delivery prepare-map-materials --outdir $planDir --frozen-delta-library '{batches}\frozen-delta-library.json' --tool-release-directory '{H3TierExport}\dist' @familyArgs
    $plan = Join-Path $planDir 'final-plan.json'
    $planSha = (Get-FileHash -LiteralPath $plan -Algorithm SHA256).Hash.ToLowerInvariant()
    python -B -X utf8 $delivery stage-map-material-outputs --plan $plan --plan-sha256 $planSha
    python -B -X utf8 $delivery install-masters --plan $plan --plan-sha256 $planSha
    python -B -X utf8 $delivery install-3x --plan $plan --plan-sha256 $planSha
    # 实际检查最终 library 后，才建立 acceptance.json 并登记 Source；不要伪造通过记录。
    python -B -X utf8 $delivery register --plan $plan --plan-sha256 $planSha --acceptance '{batches}\acceptance.json'

以上脚本是有写入能力的 H3 项目交付流程：prepare-map-materials 要求恰好七个完整 family operation，逐项复验其来源和 3X，并核对冻结 delta library 恰好 180 槽；stage-map-material-outputs 为已核验的现有候选建立交付回执，不重新采样；install-masters 核对 family 阶段已经恢复的母版字节；install-3x 按计划复制 3X 并核对 SHA；register 只有在 acceptance 明确绑定最终 library 后才更新 Source。此 source-basis 下不要运行 export，它会阻止二次压降。acceptance 必须以最终 library 的当前路径与 SHA 为准，并记录实际检查；未完成实际最终库检查前不得创建通过状态的 acceptance 文件。操作 ID 和计划目录已存在时应停止并创建新 ID，不得复用旧计划覆盖。

### CLI 包装参数与超时

```powershell
.\H3TierExport.exe scale-audit --tool-root "D:\Tools\H3TierExport" --python "C:\Python311\python.exe" --companion-timeout-seconds 3600 freeze ...
```

- `--tool-root` 指 H3TierExport 安装目录/冻结负载根，仅当自动定位失败时显式使用。
- `--python` 指定 Python 可执行文件或 `py.exe`。
- `--companion-timeout-seconds` 范围 1 到 86400 秒，默认 3600 秒；超时只终止该任务自己启动的子进程树。
- companion 会自动绑定正在调用的 EXE SHA，禁止使用 `--renderer` 改成另一个可执行文件。

## 6. 报告状态怎么读

| 状态/字段 | 含义 |
|---|---|
| `FROZEN` | 已固定输入路径、文件 SHA、原生槽位和操作 ID；尚未证明处理通过 |
| `CHECKED` / `READY` | 当前输入满足运行前置条件；不代表输出完成 |
| `FAMILY_PASS` | 该地图系列所有声明槽位通过软件规定的几何/邻接/材质检查；仍不是用户艺术批准或实机结果 |
| `VERIFIED` | 对运行结果及输入回执完成当前版本规定的复算 |
| `NOT_MEASURED` / `NOT_PROVIDED` | 缺少真实可核对的依据；不能当成通过 |
| `REDRAW_REQUIRED` / `PILOT_NEEDS_REWORK` | 当前结构、来源或整组接缝不通过；先修输入/算法或重绘，不写回母版 |
| `STALE` / `SHA_DRIFT` | 输入图、inventory、脚本或合同与冻结时不同；应新建 operation |
| `RISK` | 启发式或人工审阅提示需关注；不自动删除、重绘或批准 |

软件报告中的 `art_approved=false`、`game_runtime_accepted=false`、`game_deployed=false` 是有意保留的边界。只有真实发生并记录的用户审核、导出、运行、部署才可改变各自状态。

## 7. 常见问题

**双击后主窗口能开，批量任务报 Python 缺失。** 安装 Python 3.10+，确认运行 `py -3 --version`，安装 `numpy`、`Pillow`；多 Python 时用 `--python` 明确选解释器。

**错误 `COMPANION_TOOL_ROOT_NOT_FOUND`。** 保留发行目录中的 `.runtime-<SHA>`；或者用 `--tool-root` 指向完整发行目录/工具源目录。不能只复制 EXE。

**`Bound H3 workspace missing`。** 设置 `H3_TIER_EXPORT_H3_ROOT` 为含 `h3-paths.json` 的目录；确认该项目本身配置了原生 Complete inventory 和资源路径。

当前扁平素材根也受支持：工作区根目录应同时包含 `codex-map/native-resource-inventory.json.gz`、`codex-高清母版/` 与 `codex-3X/`。`H3TierExport-workspace.json` 或 `H3_TIER_EXPORT_H3_ROOT` 应指向这一层目录，不要指向其中某个子目录。

**`SHA_DRIFT`、`DIRECTORY_CHANGED`、`MANIFEST_SOURCE_CHANGED`。** 冻结后图或目录发生变化。不要改旧收据；对当前目录重新 freeze，使用新 operation ID。

**身体尺度显示 `NOT_MEASURED`。** 给每帧添加 SHA 绑定 landmarks，并用真实 native-review 注册回执绑定采样倍率 K。没有可比姿势/朝向的帧不会互相比较。透明框只看画幅，不代表身体尺寸。

**只有部分地图邻接通过。** 检查该资源确切的官方合法配对、镜像和半格规则；必须完整跑系列，不能只看一张孤立缩略图。共享材质相位、alpha 形状或 RGB 接口未过时不要 `restore`。

**如何更新软件？** 使用同一版本包里的 EXE、收据、`.runtime-<SHA>`、配置和本手册。不同发布版本的文件不要混搭；`H3TierExport-release.json` 及两份 build/test 收据可用于核对来源。

## 8. 安全边界与数据位置

- 操作前保留原始高清源、来源链和 SHA。工具不会把低倍率 PNG 放大后冒充母版。
- 任务候选和报告默认写在工具隔离目录；不会直接写入游戏 LOD/DEF，也不会自动发布游戏。
- 不要把候选审核目录、源素材、正式 H3 客户端或他人操作目录当成临时目录清空。
- 默认建议只下载/备份 `dist` 里最新当前版本的文件和对应 `.runtime-<SHA>`。`dist/archive/` 保存旧版发布资料，回滚时使用整套归档，不从不同发行版拼文件。
- 外部 service、Blender、RIFE 等可选功能各自需要独立软件/许可/凭据；核心图像采样和大多数本地检查不需要联网或生图额度。

本手册是发行包用户的主要操作说明。开发和算法字段集中在源码目录的[批量美术检测与本地抢救](批量美术检测与本地抢救.md)、[地图拼接与材质检测工作流](地图拼接与材质检测工作流.md)和[生物尺度与动作序列工作流](生物尺度与动作序列工作流.md)；历史整改记录仅作证据，不作为当前操作指令。发行包内的本手册不要求额外下载源码技术文档。

### 城镇建筑母版整备与 3X

```powershell
.\H3TierExport.exe town-master-prepare prepare `
  --inventory "{H3-assets}\CompleteOfficialRelease-20261006-v1\codex-map\native-resource-inventory.json.gz" `
  --inventory-sha256 <inventory SHA256> `
  --masters-root "{H3-assets}\CompleteOfficialRelease-20261006-v1\codex-高清母版\建筑" `
  --complete-frames-root "{H3-assets}\CompleteOfficialRelease-20261006-v1\codex-map\sources\complete\frames" `
  --native-archive-root "{H3-assets}\CompleteOfficialRelease-20261006-v1\codex-map\sources\native-archive" `
  --outdir "{H3TierExport}\build\experiments\town-master-fit-r01"
```

默认处理已审计的 33 个城镇建筑资源；需限定范围时可重复传入 `--identity DEF`。准备结果中的 `normalized-masters` 是官方 DEF crop 的整数倍画布（例如 5X、12X、24X），只裁除完全透明外边缘并透明补边，不缩放、拉伸或插值原始绘画像素。`effective_detail_multiple` 按 alpha>=16 的实际内容分别测量宽高；任一轴不足 3X 的帧不会进入压制批次，而是列入 `api-needed.csv`。

准备后，对同一份 `expand-batch.json` 依次执行：

```powershell
.\H3TierExport.exe expand-check --batch "<outdir>\expand-batch.json" --report "<outdir>\reports\expand-check.json"
.\H3TierExport.exe expand --batch "<outdir>\expand-batch.json" --report "<outdir>\reports\expand-run.json"
.\H3TierExport.exe expand-verify --batch "<outdir>\expand-batch.json" --report "<outdir>\reports\expand-verify.json"
```

H3TierExport 在此处执行唯一次重采样：linear-light、premultiplied-alpha、`h3-quality`，直接输出官方 DEF crop 的精确 3X 尺寸。原始高清母版和官方 LOD 均为只读；候选仍需视觉及游戏内验收。

### 地图物件与界面素材按官方真实轮廓适配（1.3.30）

官方 1X 帧是**槽位**，不是画面：帧内通常留有真实空白（地图物件左侧平均留白 67.9px、最大 106px；主菜单按钮还带一整团与边框连通的不透明底）。expand 的旧合同只保证输出=官方槽位×3，母版一旦铺满自己的画布，画就被放大到“铺满槽位”，比游戏原本画得更大——地图上会压到相邻格子（山体站在路上），菜单里按钮会互相贴住。

```powershell
.\H3TierExport.exe expand-plan --source "<母版根>\地图\AVLAUTR0\0_0.png" `
  --base-width 64 --base-height 64 --target-scale 3 `
  --reference-root "{H3-assets}\CompleteOfficialRelease-20261006-v1\codex-map\sources\complete\frames" `
  --fit-anchor bottom_right --output-root <输出根> --out <批次.json>
```

- 参考帧按 `REFERENCE-ROOT/<母版所在目录名>/<母版文件名>` 定位；缺失或尺寸不等于声明的 1X 槽位时整批拒绝，不会静默退回旧行为。
- 轮廓只认参考帧自己的 alpha。唯一窄例外：帧角点为不透明时，把“与边框连通且与角点完全同色”的像素当作底抠掉（菜单那类提取图）。角点透明时绝不抠，避免把山体左缘的深色画法当背景吃掉（12/12 山体抽样零变化）。
- `--fit-anchor bottom_right` 用于挂在格子底边的地图物件，`center` 用于界面素材；等比缩进轮廓框，不裁、不拉、不改母版。
- 轮廓模式下不再拿"母版画布长宽比 vs 槽位"当门（方形母版进宽槽是常态），改按**轮廓覆盖率**把关：母版画在轮廓框短轴上覆盖不足 50% 直接拒绝，提示"形状不属于这个槽位，请重派生母版或走 scale-layout"。已验收的 153 张地图物件覆盖率区间是 0.627~1.000。
- 硬门：输出仍是官方槽位×3；`expand-verify` 会重新读取参考帧、重算轮廓框、独立重放合成像素，并断言落图内容的 alpha 框完全落在轮廓框内，越界即 FAIL 并打印两个框。
- 收据新增 `silhouette` 段：参考帧路径/sha/像素、1X 轮廓框、是否抠底、放大后的轮廓框、母版内容框、`fit_anchor` 与 `overflow:"NONE"`。
- 不传 `--reference-root` 时完全沿用旧语义，历史收据照旧可验。

### 尺寸权威：1999 原生 1X，不许用 2015 HD 包（2026-11-04 用户裁定）

`--reference-root` 只认"那张 PNG 的尺寸"，它无法知道那张图是从哪个 LOD 解出来的。而 Complete 装机里同一身份可能在两个 LOD 各有一份 DEF：`H3sprite.lod`/`H3bitmap.lod` 是 **1999 原生 1X**，`H3ab_spr.lod`/`H3ab_bmp.lod` 是 **2015 HD 版自己的高清素材包**。实测两包都有的身份 347 个，其中 **12 个画布不同**（CSSARM 306×168 vs 原生 305×119、GTBACK 250×140 vs 127×107、MMENULG 226×120 vs 227×121 等，全是主菜单按钮，也正是 VCMI `animToSkip` 硬跳的那批）。资料库的提取通路曾用 HD 包覆盖原生，导致按它压出的 3X 天生带错画布——所以**尺寸权威必须显式给，不能靠参考帧自证**。

```powershell
# 1) 生成权威表（只读原生 LOD；H3HD/tools/build_native_1x_authority.py）
# 2) 压制时用权威表定 1X 尺寸，不必再声明 --base-width/--base-height
.\H3TierExport.exe expand-plan --source "<母版根>\界面\CSSARM\0_0.png" `
  --target-scale 3 --native-authority "<资料库>\codex-map\contracts\build-inputs\native-1x-authority.json" `
  --output-root <输出根> --out <批次.json>
```

- 权威表格式 `h3.native-1x-authority.v1`：`source_lods` 必须点名原生 LOD，**出现 `h3ab_` 直接拒绝**（`NATIVE_1X_AUTHORITY_SOURCE_IS_HD_PACK`）；`frames` 按 `身份(大写) → "组_帧" → [宽,高]` 登记。
- 身份/帧在表里没有登记 ⇒ `NATIVE_1X_AUTHORITY_MISSING_IDENTITY` / `..._MISSING_FRAME`，**不会**因为"表里没写"就放行。
- 同时传了 `--source-tier` 或 `--base-width/--base-height` 且与表不符 ⇒ `NATIVE_1X_AUTHORITY_CONFLICT`，错误里点名 2015 HD 包。
- 与 `--reference-root` 同时使用时，参考帧尺寸必须等于权威表的 1X 尺寸，否则拒绝并提示"这张可能来自 H3ab_spr/H3ab_bmp"。
- 表的 SHA 封进批次；`expand-check`/`expand`/`expand-verify` 会**重新读表复核**：表变了 ⇒ `NATIVE_1X_AUTHORITY_DRIFTED`；有人手改批次里的 `base_pixels` 或删掉 `native_1x_pixels` ⇒ `NATIVE_1X_AUTHORITY_CONFLICT` / `NATIVE_1X_AUTHORITY_UNSEALED`。也就是说批次建好之后改表或改批次都压不出东西。
- 不传 `--native-authority` 时完全沿用旧语义，历史收据照旧可验。

### 母版标准化到官方画布的整数倍（1.3.31）

`expand-plan --target-scale` 现在接受 **3X..36X**（原先只有 3X/4X）。用途是把母版一次性标准化成 `官方 1X 画布 × N`，N 取"母版自身像素能扛住的最大 3 的倍数"（建议 6..36）；随后压 3X 就是**整数倍降采样**（N/3），几何与色调都不再漂移。

```powershell
# 1) 标准化（参考帧同时给出，内容按官方轮廓框定位）
.\H3TierExport.exe expand-plan --source "<母版>\AVLMTGN2\0_0.png" `
  --base-width 128 --base-height 96 --target-scale 9 `
  --reference-root "<原生1X帧根>" --fit-anchor bottom_right `
  --mirror-source-root "<母版根>" --output-root "<标准母版根>" --out std.json
.\H3TierExport.exe expand --batch std.json
.\H3TierExport.exe expand-verify --batch std.json

# 2) 从标准母版压 3X（不再需要 --reference-root：摆放已经标准化）
.\H3TierExport.exe expand-plan --source "<标准母版根>\AVLMTGN2\0_0_9X.png" `
  --base-width 128 --base-height 96 --target-scale 3 --output-root "<3X根>" --out press.json
```

超过 36X 会在计划阶段被拒（`master target must be 3X..36X`）。缩小算子仍是 `h3-quality`：线性光、预乘 alpha、尺度加宽 sinc + footprint antiring + clarity；实测在整数比下比纯面积平均锐 23%，所以标准化只是为了几何，不换算子。

### 城镇旗标对齐向导（GUI 任务中心）

对城镇母版生成待审的 3X 候选时，旧 `town-flag-alignment` 使用 `bottom_right` 注册并叠加原始旗面参考。这个锚点只描述导出方法，不代表旗杆、门口或道路已经对齐；不能对 18 城统一套用后宣布通过。画布中线也不是旗杆中点或道路中心。

VCMI 支持从 MOD 的 `Sprites3x` 读取与建筑同名的 `-OVERLAY.png`，其白色旗面由引擎按玩家颜色着色；该视觉层可以逐旗调整，不会改变地图通行格。`spriteFlagsInfo.txt` 是 HD Edition 加载路径的参考数据，不能当作 MOD overlay 永远不可移动的证明。

对实际 MOD 使用 `town-flag-review`：先从既有 `town-flag-alignment` 原版参考请求冻结当前 MOD 快照，再在 GUI 任务中心选“实际 MOD 旗面逐城复核”并载入新 JSON，运行后打开输出的 `index.html`。每城可单独拖动左、右旗面和建筑预览，或标记旗面挂点、两杆顶、门口和道路，再计算逐旗位移；原版 1X 对照包含从 1999 DEF 取出的 palette-5 旗面。绑定 VCMI 默认城镇模板后显示逻辑访问格，但地图实例或其他 MOD 可能覆盖它；格中心也不必等于道路实际中心。网页可保存或重新载入 SHA 绑定布局。位移以 3X 像素计，保存布局不写 MOD，不证明道路、美术或游戏通过。

审图快照逐张区分“新候选”和“当前 MOD”，混合快照中二者的文件均按清单 SHA 固定。网页可切换建筑亮度至 70% 作明暗对照，旗面、网格和 1X 对照不随之变暗；这只是浏览器显示效果，不写图片，不能代替按材质保留高光的新绘效果。

保存布局后，在命令行同时传入 `--layout <布局.json> --layout-sha256 <布局SHA>` 可以导出独立旗面候选。如果布局还包含建筑位移，须显式增加 `--export-body-translation`：工具输出独立 3X 身体候选和旗面候选，以整数像素无插值平移，并核对 1X 原生 Alpha 范围、画布、输入 SHA 和读回像素；任何非零 Alpha 越界都会拒绝。身体候选只记录相对于输入 3X 的平移，不是新母版，也不能代替原母版的 `expand-verify`、美术或游戏验收；工具不会写入 MOD。

```powershell
H3TierExport.exe town-flag-review --freeze `
  --reference-request "<原版参考请求.json>" --reference-request-sha256 "<SHA256>" `
  --sprites3x-root "<MOD>\content\Sprites3x" --operation-id town-review-r01 --extra-identity AVCHFORX `
  --town-mask-config "<VCMI源码>\config\objects\moddables.json" `
  --out "<新目录>\town-review-request.json"

H3TierExport.exe town-flag-review `
  --manifest "<新目录>\town-review-request.json" --manifest-sha256 "<SHA256>" `
  --outdir "<新的审图输出目录>"

# 网页保存布局后，仅生成新的白色 overlay 候选；不会安装到 MOD。
H3TierExport.exe town-flag-review `
  --manifest "<新目录>\town-review-request.json" --manifest-sha256 "<SHA256>" `
  --layout "<网页保存的布局.json>" --layout-sha256 "<SHA256>" --outdir "<新的候选目录>"
```

历史 r31 审图清单曾把元素城 0/Z 当作大小城并把 X 列为额外身份。按当前九族大小城槽位，元素城大小城应为 `AVCHFOR0/AVCHFORX`；`AVCHFORZ` 为议会/首府形态。继续看历史 MOD 快照时，仍须按该快照自己的 0/Z/X 清单解释，不可把它当成本次 18 城清单。Z 形态远离入口的额外上方旗面也不能当作门旗。

先冻结一个请求清单。默认身份是当前的 18 个城镇槽；也可重复传入 `--identity DEF`。每个母版必须位于 `<source-root>/<阵营目录>/.../<DEF>/0_0.png`，原版参考帧必须位于 `<native-frames-root>/<DEF>/0_0.png`。

```powershell
H3TierExport.exe town-flag-alignment freeze `
  --source-root "{H3-assets}\CompleteOfficialRelease-20261006-v1\codex-高清母版\建筑" `
  --native-frames-root "{H3-assets}\CompleteOfficialRelease-20261006-v1\codex-map\sources\complete\frames" `
  --native-def-root "{H3-assets}\CompleteOfficialRelease-20261006-v1\codex-map\sources\complete\payloads" `
  --sprite-flags-info "{H3-hd}\data\spriteFlagsInfo.txt" `
  --flag-grey "{H3-hd}\data\flags\flag_grey.png" `
  --operation-id town-flag-alignment-r01 `
  --out "D:\path\to\town-flag-alignment-r01-request.json"
```

在 GUI 中打开“批量任务中心”，选择 `town-flag-alignment`，载入冻结的 request JSON，依次运行 `check`、`run`、`verify`。输出位于 `build/experiments/town-flag-alignment/<operation_id>`：包含原生 `expand-batch.json` 和逐项收据、完整画布旗标预览、分页面板以及 `index.html`。`check` 会确认每个输入 SHA 和锚点表；`run` 只向新的实验目录写底右注册候选和审图文件；`verify` 会重放 `expand-verify` 并复核母版、参考帧、候选与预览 SHA。

身份在 VCMI 表中没有精确键时，参考请求读取原版 1999 DEF group 0/frame 0 的 palette index 5 旗面；这不等于该城没有旗。旧向导的预览和收据只证明像素来源与导出结果，不证明旗杆、入口或游戏效果。

### 历史城镇旗杆检查

`scripts/flag_alignment_guard.py` 保留历史逐点与收据检查。旧版将旗面边界和 alpha 支撑误当成杆位证据，不能判断实际杆顶或道路；现对这些结果返回 `REVIEW_REQUIRED`，缺 VCMI 键也不再返回“不适用”。收据审计仍仅证明导出字节和来源。

```powershell
python .\scripts\flag_alignment_guard.py verify `
  --building-3x "<候选根>\AVCRAMZ0\0_0_3X.png" `
  --spec "<测量清单>\AVCRAMZ0.flag-alignment.json" `
  --receipt "<候选根>\receipts\MASTER_1-<fingerprint>.json" `
  --verify-report "<候选根>\reports\expand-verify.json" `
  --proof "<审核根>\AVCRAMZ0-flag-alignment-proof.png" `
  --report "<审核根>\AVCRAMZ0-flag-alignment-report.json"

python .\scripts\flag_alignment_guard.py verify-batch `
  --batch "<审核根>\flag-alignment-batch.json" `
  --report "<审核根>\flag-alignment-batch-report.json"

python .\scripts\flag_alignment_guard.py audit-receipts `
  --batch "<审核根>\flag-alignment-receipt-batch.json" `
  --report "<审核根>\flag-alignment-receipt-audit.json"
```

历史 spec 绑定 1999 原生 1X 帧、导出收据以及 `spriteFlagsInfo.txt` 等输入 SHA；这些数据可用于追溯旧检查，表中旗面图的局部像素不是已证实的旗杆脚坐标。

旧 spec 中记录的左右挂点、杆顶、杆身、杆脚和支撑区仅作为人工录入数据；旗面局部点与 17×17 不透明区域不能证明绘画里真实存在旗杆。真实杆顶需在当前身体图上观察，原始旗面与实际 MOD 旗面均需分别确认。

旧检查还会验证候选 PNG SHA、收据和 `expand-verify`；它仍限定底右注册与不透明支撑区，所以不能用于否决其他视觉正确的注册方案，也不能凭这些条件批准美术。

`audit-receipts` 只审计批量收据的输出 SHA、原生 1X 来源、3X 画布、`bottom_right` 锚点和 `expand-verify` 回执；它不会把结果冒充为旗杆几何通过。历史 `verify` 即使通过这些计算也只返回 `REVIEW_REQUIRED`。

无 VCMI 精确旗键的身份仍需核对 1999 DEF 和 MOD 旗面，不能算无旗或自动通过。

批量 JSON 的根 schema 分别是 `h3.flag-alignment-receipt-batch.v1` 与 `h3.flag-alignment-batch.v1`。每项都要有 `identity`、`building_3x`、`receipt`、`verify_report`；完整几何 `verify-batch` 还要增加对应的 `spec`，proof 可选。所有路径可写绝对路径，也可相对批量 JSON 所在目录。

旧版 `Flag Anchors` 坐标只表示旗杆脚/地面着力点，不能替代旗面挂点和杆顶测量。缺少身份 spec、旗面遮罩、任一侧杆顶/杆脚/中心线或有效扩展收据都会失败关闭；未知身份不会跳过并报 PASS。proof 会分别画出左右旗面、各自杆顶、杆脚和地面支撑矩形。门禁只审核并生成证明，不会移动或覆盖素材。

## 9. 近期新增功能与使用入口（1.3.30–1.3.37）

近期功能集中在新任务中心；单图自动描点除外，其余流程通常先固定输入清单和 SHA，再将输出写入新的 `build/experiments/<任务>/<operation-id>`。

`native-review` 按 H3TierExport 的显式 workspace 根解析原版 inventory。当前素材根直接包含 `codex-map/native-resource-inventory.json.gz` 时读取该绑定文件；旧的版本化和 legacy 目录布局继续支持。它不会搜索绑定根以外的路径，也不会把 native-review 诊断当作美术、正式3X或游戏批准。

| 功能 | 任务中心入口 | 适合的工作 | 使用要点 |
|---|---|---|---|
| 官方轮廓适配与母版整备 | `town-master-prepare`、`native-review` | 城镇、地图对象按原生可见框/画布比例导出 | 先冻结 1X 几何与母版清单，再 `check → run → verify`；来源母版保持只读。 |
| 城镇旗面逐城复核 | `town-flag-review` | 检查当前 MOD 的旗面、城门、道路及访问格关系 | 先冻结当前 MOD 快照，载入 request JSON；网页布局与候选不会直接写 MOD。 |
| **通用旗杆坐标与杆长** | `flag-pole-audit` | 原版 DEF 的逐帧画布、裁片、原点与旗面审图 | 内置 1999 Complete `H3sprite.lod` 资源；完整画布/裁片的整数倍自动注册，其他尺寸须人工确认。详情见 `docs/旗杆坐标与杆长批量核对.md`。 |
| 生物单帧/骑乘姿态描点 | `creature-auto-pose`、`hero-mounted-pose` | 人形生物或骑乘英雄的部位比例初筛 | 选 PNG 或冻结清单；模型点位要人工复核，不修改母图，也不替代尺度/动作验收。 |
| 类别尺度与整组动作 | `scale-draft`、`scale-audit`、`scale-layout`、`creature-master-set` | 开工前确定类别尺度，制作后审官方全槽、母版比例和 3X 布局 | 无可比基准时保留 `NOT_MEASURED`；不要用透明框 bbox 代替身体测量。 |
| 本地素材抢救 | `asset-repair` | 有来源的共享接口、材质、固定底座、旗纹派生与边缘诊断 | 先 `check`，再 `run` 和 `verify`；输出候选，不覆写来源。 |
| 地图批量生成与接口核验 | `map-synth`、`map-source-batch`、`map-material-family` | 道路/河流、道路材质系列及原生端口 | 先冻结父材质、邻接规则、参考图和 SHA；局部平滑不能替代全部相邻接缝检查。 |

### 旗杆核对的快速操作

1. 单图可直接导入 `AVMSAWR0/0_0.png` 等原生目录和帧名，或采用 `AVCCAST0.png` 这样的身份命名；也可以先准备批量 manifest 并载入任务中心。
2. 软件显示所选 1999 原版 DEF 帧的画布、裁片、裁片原点及 palette-5 旗面；蓝框为裁片、黄框为完整旗面、绿点为按原版形状推断的旗面挂接侧可见像素。绿点不证明建筑杆顶；橙框为残缺旗面，不能自动当作精确旗位。
3. 同组杆子可用组内中位数作为本批比较值，也可填写明确的目标杆长。官方数据不包含对所有建筑都适用的统一装饰杆长。
4. 导出标注布局 JSON，再运行 `flag-pole-audit --verify-layout` 生成 `flag-pole-measurement-report.json`。将 `ai_edit_instruction`、坐标和长度数值交给局部绘图步骤；报告不会自动改图。

内置库直接从 1999 Complete `H3sprite.lod` 提取 2,565 个 DEF、34,324 个逻辑帧，包含 49 个 `AVM` 身份共 474 帧；每个 DEF 附来源 SHA。`--catalog DEF [--group N --frame N]` 可只读查询原始几何。部分旗面和 12 个非矿物异常帧头明确标为待复核，不伪造精确值。

完整画布或裁片为原版整数倍时自动推定注册；仅裁片按 `-原版裁片原点×倍率` 设置注册偏移。所有输入均须确认注册之后才计算杆位偏差；任意尺寸的高清母版还须手工设定倍率与偏移。实际杆顶、杆长和游戏效果需单独复核。前文城镇专项的 HD/VCMI 数据是独立历史工作流，不是本功能的 1999 依据。

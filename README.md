# H3TierExport

**A local, evidence-oriented high-resolution asset workbench for Heroes of Might and Magic III Complete / HD Edition.**
**面向《英雄无敌 III》Complete / HD Edition 的本地高清素材处理与证据工作台。**

- Version: 1.3.44
- Platform: Windows 10/11 x64
- Core license: MIT (see `LICENSE`)
- Third-party components: see `THIRD_PARTY_NOTICES.md`
- This repository contains **source code only**. It does not include game assets, models, generated art, or runtime dependencies.

---

## 中文

### 1. 项目定位

H3TierExport 不是生图软件，也不自动决定画面艺术质量。它负责把美术人员或 AI 提供的画面，按《英雄无敌 III》原生资源槽位、canvas / crop / origin 和固定坐标处理成**可复核的候选**，并保存可复跑的回执。

它解决的是“身份、几何、来源和结果是否一致”的问题，而不是“这张图好不好看”。工具自测通过不等于人工美术接受，也不等于已经写入游戏或实机运行通过。

### 2. 核心功能

- **GUI 任务中心 + CLI**：同一套 C++20 原生计算内核，支持批处理和单任务。
- **X3-only 导出**：正式成品只接受原生几何的 3 倍尺寸；1X/2X 只用于身份与几何核对，4X 不能冒充 X3。
- **母版整备与变体**：`town-master-prepare`、`master-expand`、`master-variant`。
- **生物工作流**：生物母版集、静态槽位、landmark、整组动作、动作时序、姿态候选与序列复核。
- **地图与城镇**：地图材质族、道路/河流合法邻接、城镇母版、城镇旗面复核。
- **旗杆审计**：内置 1999 Complete `H3sprite.lod` 的 DEF/逻辑帧画布、裁片和原点，辅助判断旗面与杆顶关系。
- **VCMI MOD 候选**：把已通过 `expand-verify` 的动作批次打包为隔离的 VCMI MOD 候选；不安装、不覆盖现有 MOD。
- **素材抢救与质量检查**：批量档案、透明边缘、Alpha 逻辑、颜色/材质漂移、逐项比较图。
- **可复跑回执**：输入清单、输入 SHA、构建/测试回执、输出 SHA、独立 verify 收据。

### 3. 调研方法

1. **冻结输入身份**：记录来源文件/母版的完整路径、尺寸、字节数和 SHA-256；不以文件名或版本字符串代替身份。
2. **回到原生几何**：用 1999 Complete 的 `H3sprite.lod` / DEF 的 canvas、crop、origin 和逻辑帧作为几何依据；2015 Official HD 只作画面参考，不作为命名或槽位来源。
3. **分离母版、候选、正式 X3**：母版是可追溯的原始高清输出；候选是 `build/experiments/<operation-id>` 中的过程结果；只有正式 `codex-3X` 目录才是交付档。
4. **按 operation 隔离**：每次检查/生成使用独立 operation 目录，不静默覆盖不同输入或不同结果。
5. **check → run → verify**：先预检，再在隔离目录生成候选，最后独立复算来源、像素、尺寸与回执。
6. **人工与实机分离**：工具只能证明它实现的几何/像素/来源合同；美术接受、游戏写入和实机运行必须分别验收。

### 4. 快速开始

**构建要求**

- Windows 10/11 x64
- Visual Studio（含“使用 C++ 的桌面开发”工作负载）与 Windows SDK
- Python 3.10+（仅 companion 功能需要；基础 CLI 与原生导出不依赖 Python）
- 可选：`numpy`、`Pillow`、`onnxruntime`、Blender、RIFE、VCMI

```powershell
# 在仓库根目录运行：生成图标、构建、回归测试、发布
.\rebuild.cmd

# 产物
.\dist\H3TierExport.exe info
.\dist\H3TierExport.exe capabilities
.\dist\H3TierExport.exe selftest --require-gpu
```

**配置本地素材根**

编辑 `resources\H3TierExport-workspace.json`，把 `{H3-assets}` 替换为你本机的 H3 素材根：

```json
{
  "schema": "h3.tier-export.workspace.v1",
  "h3_root": "D:\\path\\to\\H3-assets"
}
```

仓库中的 `{H3TierExport}`、`{H3-assets}`、`{H3-workspace}`、`{VX-root}`、`{user}` 等都是**占位符**，请按本机路径替换。

### 5. 目录结构

```
src/                  C++20 GUI / CLI / 原生图像内核
scripts/              Python companion、批处理、审阅与发布工具
tests/                合同测试与原生/合成夹具
docs/                 用户手册与工作流说明
providers/            第三方上游流程适配器（GPL-2.0-or-later，独立子进程）
third_party/          nlohmann/json 等固定版本第三方源码
resources/            manifest、图标、shader、workspace 模板
integration-patches/  可复用的集成补丁
build/                本地实验与候选输出（不随仓库分发）
dist/                 本地发行包（不随仓库分发）
venv-win/             本地 Python 虚拟环境（不随仓库分发）
```

### 6. 许可与素材边界

- 本项目原创代码采用 **MIT**。
- `providers/h3-art-pipeline/upstream` 来自 `yzh119/h3-art-pipeline`，采用 **GPL-2.0-or-later**，作为独立子进程使用，不并入 MIT 内核；分发该目录时必须遵守 GPL。
- `third_party/json.hpp` 为 **MIT**。
- **不包含任何《英雄无敌 III》游戏素材、LOD/DEF、官方 HD 图、模型或生成图。** 使用者必须自行合法取得游戏与素材。
- “Heroes of Might and Magic III”及相关商标归其权利人所有；本项目与 Ubisoft / New World Computing 无隶属或背书关系。

---

## English

### 1. What this is

H3TierExport is a local asset-processing workbench for Heroes of Might and Magic III Complete / HD Edition. It is **not** an image generator and does **not** decide whether art looks good. It takes artwork produced by an artist or an AI pipeline, binds it to the game's native resource slots and geometry (canvas / crop / origin), produces verifiable candidates, and stores reproducible evidence.

Its job is to answer: *are identity, geometry, provenance and output consistent?* Tool self-tests are not human art approval, game installation, or in-game verification.

### 2. Features

- **GUI task center + CLI** over one native C++20 image-processing core.
- **X3-only formal export**: formal assets are exactly 3× the native geometry. 1X/2X are for identity and geometry checks only; 4X cannot masquerade as X3.
- **Master preparation and variants**: `town-master-prepare`, `master-expand`, `master-variant`.
- **Creature workflows**: master sets, static slots, landmarks, grouped actions, motion timing, pose candidates and sequence review.
- **Maps and towns**: material families, legal road/river adjacency, town masters, town-flag review.
- **Flag-pole audit**: built-in canvas/crop/origin data from the 1999 Complete `H3sprite.lod` DEF inventory.
- **VCMI MOD candidates**: package an `expand-verify` action batch into an isolated VCMI MOD candidate; never install or overwrite an existing MOD.
- **Asset repair and quality checks**: batch archives, alpha edges, logical alpha, colour/material drift, side-by-side review images.
- **Reproducible receipts**: input inventory, input SHA-256, build/test receipts, output SHA-256, independent verify receipts.

### 3. Research methodology

1. **Freeze input identity** — record full source path, dimensions, byte length and SHA-256; never rely on a filename or version string.
2. **Return to native geometry** — use the 1999 Complete `H3sprite.lod` / DEF canvas, crop, origin and logical frames as the geometric authority. The 2015 Official HD release is a visual reference only, not a naming or slot authority.
3. **Separate master, candidate and formal X3** — masters are traceable high-resolution outputs; candidates live under `build/experiments/<operation-id>`; only the formal `codex-3X` tree is deliverable.
4. **Bind every operation** — each check/run gets its own operation directory and never silently overwrites a different input or result.
5. **check → run → verify** — preflight, generate into an isolated directory, then independently recompute provenance, pixels, dimensions and receipts.
6. **Keep human and in-game acceptance separate** — the tool proves only the geometry/pixel/provenance contracts it implements. Art approval, game installation and runtime acceptance are separate gates.

### 4. Quick start

**Build requirements**

- Windows 10/11 x64
- Visual Studio with the "Desktop development with C++" workload and a Windows SDK
- Python 3.10+ only for companion features; the native CLI/export core does not require Python
- Optional: `numpy`, `Pillow`, `onnxruntime`, Blender, RIFE, VCMI

```powershell
# From the repository root: regenerate icon, build, run tests, publish
.\rebuild.cmd

# Outputs
.\dist\H3TierExport.exe info
.\dist\H3TierExport.exe capabilities
.\dist\H3TierExport.exe selftest --require-gpu
```

**Configure the local asset root**

Edit `resources\H3TierExport-workspace.json` and replace `{H3-assets}` with your local H3 asset root:

```json
{
  "schema": "h3.tier-export.workspace.v1",
  "h3_root": "D:\\path\\to\\H3-assets"
}
```

Placeholders such as `{H3TierExport}`, `{H3-assets}`, `{H3-workspace}`, `{VX-root}` and `{user}` must be replaced with your local paths.

### 5. Repository layout

```
src/                 C++20 GUI / CLI / native image core
scripts/             Python companions, batch, review and publishing tools
tests/               Contract tests and native/synthetic fixtures
docs/                User manual and workflow guides
providers/           Third-party upstream adapters (GPL-2.0-or-later, separate subprocess)
third_party/         Pinned third-party sources such as nlohmann/json
resources/           Manifests, icons, shader, workspace template
integration-patches/ Reusable integration patches
build/               Local experiments/candidates (not distributed)
dist/                Local release package (not distributed)
venv-win/            Local Python virtual environment (not distributed)
```

### 6. License and asset boundary

- Original project code: **MIT**.
- `providers/h3-art-pipeline/upstream` is from `yzh119/h3-art-pipeline` and is **GPL-2.0-or-later**. It runs as a separate subprocess and is not merged into the MIT core; redistributing that directory requires GPL compliance.
- `third_party/json.hpp` is **MIT**.
- **No Heroes III game assets, LOD/DEF files, official HD artwork, models or generated art are included.** Users must obtain the game and assets legally.
- "Heroes of Might and Magic III" and related marks belong to their respective owners. This project is not affiliated with or endorsed by Ubisoft or New World Computing.

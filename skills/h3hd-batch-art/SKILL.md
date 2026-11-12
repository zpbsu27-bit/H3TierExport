---
name: h3hd-batch-art
description: Batch-produce and review Heroes III HD game art with H3TierExport, official source frames, existing H3HD adapters, and targeted AI repairs. Use for creature animation, town, map, terrain, and UI asset batches; not for arbitrary image generation.
---

# H3HD 批量制图

先核对当前项目 `AGENTS.md`、唯一 H3HD 计划、素材索引和本窗口认领范围。工具在 `{H3TierExport}`；素材、批次合同与正式接收仍由 `{H3-workspace}` 的对应 owner 管理。保留其他窗口的代码、素材和 Git 现场。

## 先选最省模型调用的生产路径

1. 从当前 canonical 索引统计原 DEF 槽位、不同画面、已通过设计、原版等价重复与真正缺口。先核对实际 exe 存在、SHA、`info` 能力与输入源可恢复状态，再冻结批次输入和工具版本；正式 producer 的路径绑定变更由其 owner 处理。原始参考优先官方高清 3X；它缺席时才使用原 3.45。不要因旧计划数字或缺失的审阅帧板而重复生图。
2. 官方 3X 已覆盖且美术合格时，优先 `mode:reuse`；同一原画的重复槽按等价类复用。官方源尺寸与原槽不同，先用 `scripts/prepare_official_motion_reuse.py` 做只读来源/几何冻结；只有唯一位置且裁掉的全是 alpha=0 才能用 `mode:reframe`，可见内容超框或位置歧义一律留给人工/绘画，不强拉尺寸。多张已有母版且目标几何相同，先考虑一个多 `--source` 的 `expand-plan`/`expand`/`expand-verify` 批次，减少逐张启动工具。`expand` 仅从原始整图一次等比例导出精确 3X，不声称产生新绘画细节或替代正式 adapter 接收。
3. 需要动作派生时先做 5–10 个原版槽位的小批：用 `scripts/prepare_motion_pilot.py` 或同等已核对 spec 冻结原版几何、关键帧、足点和来源；执行 `motion-plan`、`motion-check`，当前工具版本支持时再用只诊断不写PNG的 `motion-probe`，相容后才 `motion-run`、`motion-verify`。保留 `NEEDS_KEYFRAME` 的拒绝语义。局部肢体、转身、遮挡、特效相位须有相容的关键帧和显式网格/图层；对失败帧增加真实关键帧或改进工具，不靠放宽阈值、裁掉像素或伪造动作填满批次。
4. 只把工具无法形成的形体、新露出部位、材质、光照与用户明确指出的艺术缺陷交给图像模型。一次请求覆盖完整绘画画面，冻结具体父图和参考 SHA，批量处理相容缺口；修正后保留模型原始 PNG 字节，再由本领域唯一 adapter 入库与导出。原图、必要编辑父图和已经通过的设计保留。

## 内置浏览器生图的固定批量流程

- 使用内置浏览器里的 ChatGPT 时，同一批次只保留一个生成页面；不要用并发 tab 规避等待。
- 单次参考图文件上传最多 **6 张图片**，配置显式记录 `max_images_per_upload: 6`、文件顺序和每张图的 SHA256；不要把六张参考图错误拆成三张一组。每次 ChatGPT 生成请求最多 **3 张输出图**。一个输出可以按冻结提示词包含多个小图，但城堡图层等独立素材仍必须请求独立透明 PNG，不能把拼板当作多张成品。
- 发送前确认所有附件都已落到输入区，且不存在 `正在上传` 进度条；发送后记录稳定的批次/请求标识。只读轮询页面状态，不重复点击发送或重新提交同一请求。
- 将页面状态分为 `SUBMITTED_WAITING`、`GENERATING`、`COMPLETED`、`FAILED_PARTIAL`、`FAILED_EMPTY`。出现“图像生成失败”但旁边已有半成品、占位图或页面仍在变化时，先按未完成处理：等待一个有界观察窗口并重新读取页面，保存截图/DOM/请求收据，不要立即点“重试”。半成品和失败提示必须同时保留为证据，不能记为通过。
- 只有在页面稳定进入失败终态、已保存截图/DOM/请求收据、确认没有仍在生成的输出后，才允许一次同请求上下文的有依据重试。重试仍失败时，停止盲重试，改为仍使用最多 6 张参考图、但拆成每次最多 3 张输出的请求，并把失败根因反馈到本 skill 或工具 owner。
- A/B/C 等多输出请求只有在每个独立图像都可见、可下载、透明边和内容验收完成后才算完成；其中任一项失败都保持批次开放，先归档成功项与失败证据，不覆盖原始输出。

## 每批必须独立判定

工具 `PASS` 只证明批次合同、像素和几何。另在原图、精确 3X、实际 1X 和动作循环检查身份、体量、足底、轮廓、透明边、材料层次、动作顺序及状态变化。城镇另查场景光照、落地、空间与主次；地形另查格边；界面另查按钮和文字留白。美术、原生时序、正式游戏分别记录，不能由 CPU 候选或模型预览直接核销。

Alpha-aware 复核必须保留所有 attached alpha1 像素；不能按透明像素的 RGB 颜色计数硬失败，也不能用清 alpha 消除 ghosting。先记录 alpha>=2 的画布边缘与原生裁片外几何，再用正确 alpha 的白底、深底合成在实际 3X/1X 观察；这些字段只提供可复核证据，不能自动授予美术、时序、runtime 或游戏通过。

失败后先读取本次报告、原图和参考，归纳可复现根因。若相同缺陷跨批次重复，先在 H3TierExport 中修算法、约束或可视检查，补能验证问题的测试并重新构建；工具源码已有他人未提交修改时先协调 owner，不能覆盖。每次验证过的经验更新 [证据与返工经验](references/proven-lessons.md)。美术标准只有 `{H3-workspace}\H3HD\assets\CompleteOfficialRelease-20261006-v1\codex-高清母版\批量生图工具\美术标准\town-art-direction-canonical.json` 一份，由其 owner 合并；本 skill 只记录如何应用和证据，不另建竞争性的美术 JSON。长期成品提示词和美术资料也须按领域保存在同一个 `codex-高清母版` 根下，中间目录只作暂存证据。

删除废图前核对有效引用、原始字节与编辑父图，按精确路径执行并留收据；删除动作被工具或自动审查拒绝时保留文件并如实记录。长任务结果不明时按同一批次/报告恢复，禁止盲目再次执行。

按任务读取 [已验证的经验与待解决工具缺口](references/proven-lessons.md)。CLI 和 `h3.motion.spec.v1` 具体字段以工具当前 `README.md` 及源码为准；这份 skill 不复制第二套工具规范。

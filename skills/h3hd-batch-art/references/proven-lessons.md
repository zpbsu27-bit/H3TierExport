# 已验证经验与工具待办

此文件记录有原始图、冻结请求或运行收据支撑的决策。当前艺术标准由 H3 素材根下唯一的 `codex-高清母版/美术标准/town-art-direction-canonical.json` 持有；这里不另定第二套颜色或造型规范。新增结论先写明可复核的输入、结果和适用范围，再决定是否推广到其他品类。

## 2026-10-18：内置浏览器生图失败恢复与上传上限

- 适用范围：内置浏览器 ChatGPT 的图片生成，不改变 H3TierExport 的本地导出合同。
- 观察：一次选择 6 张参考图并在单页请求三张独立图时，页面先显示部分图像，随后显示“图像生成失败”。这只能证明本次网页请求没有形成完整终态；不能据此判断额度耗尽，也不能把半成品当成成功。
- 固定处理：保留当前页面、附件、已出现的半成品、失败文字、截图和请求收据；先等待并重新读取 DOM/截图，确认页面不再处于生成或上传状态。不得在状态未收敛时点击“重试”、重新发送同一提示词或新开 tab。
- 上传合同：文件选择器单次最多 6 张参考图，生成请求每次最多 3 张输出图。一个输出可按冻结提示词包含多个小图，但城堡独立图层仍必须是独立 PNG；在批次收据中记录 `max_images_per_upload=6`、`max_outputs_per_request=3`、文件顺序/SHA256 和实际完成状态。
- 重试合同：只在页面稳定失败、已留下失败证据且确认没有仍在生成的输出后，允许同一请求上下文重试一次；再次失败时保留最多 6 张参考图，改为每次最多 3 张输出的拆分请求，按新批次记录，禁止无限重试。该规则来自当前城堡批次失败，推广到其他素材前仍要保留各自的真实证据。

## 2026-10-13：动作工具首次八帧试跑

- H3 根：`{H3-workspace}`；本次 CBOAR G0 原版8槽、已有8张逐帧绘制，冻结三个现有模型关键帧 F0/F3/F7，用 H3TierExport 1.3.0 的 `motion-plan/check/run/verify` 实跑。工具 exe SHA256：`8b1b6b293312bb3c7f72a91e258e6fa8e0bd9927bc8c782deb9a9044ff86f474`。
- 证据目录：`H3HD/assets/CompleteOfficialRelease-20261006-v1/codex-prepared/人物生物/CBOAR-motion-pilot-r57/`。`motion-run-r57.json` SHA256 `ddf46f29e723c0910a285dc1f4829b87434822d8e5d161b0db12d47738eac53b`；`motion-verify-r57.json` SHA256 `09466f95e2e53ffed5a03de186d891239694661bc5e2fd011e609ed95a591ba7`。
- 结果：3个关键帧得到已验证的精确3X候选；5个插值槽返回 `NEEDS_KEYFRAME`，没有失败帧PNG。两段身体透明覆盖约0.685、0.731，低于工具默认0.8，颜色误差约0.10仍合格。此处是跑动四肢/骑手与坐骑的对应关系不足，不能靠降低0.8阈值宣称节省五次模型调用。下一次工具试点应选低形变片段，或为四足/骑手分图层、标控制点并增关键帧；若仍失败，再给缺口绘画。旧8张已经存在，不重复模型生图。
- 同一批次再以 F0/F2/F4/F6/F7 五个已画关键帧试跑，只有5个端点候选通过；三个真正插值 F1/F3/F5 仍分别以覆盖0.675/0.620/0.708返回 `NEEDS_KEYFRAME`。证据在其 `pilot-k5/motion-run-r57.json` SHA256 `3e32baa8a67c267e7ef5433c74b5fe49bf8af750a5b5a929196ded8851b1e240`，验证 SHA256 `9186b8b26602f070ea64d8ae4c21b6ee27bf66e9333ed9514875a1210399fb0a`。多加端点本身没有省一次模型调用；先改对应网格/身体分层或换低形变对象，再投更多关键帧。
- 工具源码新增 `motion-probe` 候选命令：在任何候选PNG写入前用真实像素计算相容度、足点和体量，报告每槽 `READY_FOR_RUN` 或 `NEEDS_KEYFRAME`；不替代美术审核。隔离构建 exe SHA256 `32ad22ea5cad83fe0572382614c42725418af1a6d7af501887f33e5bd54e4a4d`，build 收据 SHA256 `17ac05e8c05e47425f5f4f8038a8f80ceb4431b1687b1b99f3dd54179efc4ccc`，37项 motion 合同测试全过。它在旧3关键帧批给出相同的3可运行/5需关键帧，报告 SHA256 `33456e9ff8701c6e7d7d55a8cd7f2a2c7c1239c4f8fd3b53cb97ba9873e8373c`；5关键帧批为5/3，报告 SHA256 `8cd728d72c4b3a1540b652b7576280f253a8a0ef49251da18d8f5a0d15a60042`；两次 `outputs_written=0`。此命令目前只在隔离 candidate exe；正式 dist 1.3.0 尚未升级，调用前必须查实际 exe 能力。
- 性能纠偏：初版探针串行处理，八帧耗约6.7秒；第二版沿用工具内存预算和工作线程并行检查，八帧耗约2.26秒，保持同一3/5判定与零PNG写入。第二版 exe SHA256 `3bb4be201fc4772c02851f62c6e9930c8fcb48ec7480600eb272cbd720955f1c`；build 收据 SHA256 `6c796332c01dbefdb3705d498c95ebb9da2476ac7b3a441db78977e6e4ea6f9a`；真实探针报告 SHA256 `f0cf931b4d1864d8628af13ba50ef2427db4b3e42a3c780d2cb1e5336a198328`；37项合同测试再次全过。正式 dist 仍是旧版，发布前必须处理工具目录现有其他窗口的未提交代码。

## 生物事实与返工边界

- CBOAR 当前83/83槽、61个不同PNG已存在；此前缺失的G0/G1/G2审阅板只是展示资料，零生图恢复。十二张当前动作板复看发现G0骑手头盔是短金刺，而其他组及原版是浅色弯角，整套美术暂缓。证据：`.../codex-prepared/人物生物/CBOAR-current83-art-review-r55/current83-all-boards-sol-art-review-r56.json`，SHA256 `039dee67b7275728b0ddc48d4d1382fe5eb851215ea943c46b29c4a094053193`。修正应只改有证据的不一致，不推翻通过的野猪、骑手材质和动作。
- 当前 `CSTONE.DEF` 是升级的岩浆元素，`CEELEM.DEF` 是未升级的土元素。用户现要求优先完成未升级形态：保留现有石人的饱满站姿和大块连续岩面作为视觉父图，重画哑光、非麻点材质；升级版以后才加强沿岩缝流动的熔岩及轻微喷溅。现有 CSTONE 模型PNG是带原来源的待审父图，不能直接改名挂到 CEELEM。两 DEF 的原槽有差异：例如G1F3裁片宽度 CEELEM45、CSTONE50，纵向原点也差2。CEELEM已有81/81官方3X参考，但当前人物生物 adapter 没有它的正式模型请求、艺术通过或实机验收；官方源可用于省模型的复用/关键帧，不自动等于本次用户认可的新岩面。
- CSTONE G1四幅待审父图 a09 的 F3 含拳头和飞石的连通包围宽376，原裁片宽50使自然K7需≤346；更窄提示后的 a11 反而宽384，自然K8，说明连续两次局部提示未解决形体范围。另三幅 a10 的模型半透明画布边 alpha max105、外缘5016像素；a12 消除外缘到alpha1，但空白第四格仍有117个alpha≥2像素。两者已保留原始字节，不以工具几何 PASS 替代艺术通过。下一方案须从动作/图层结构重审，不复读同一提示词、不用CPU抹alpha。
- 蓝色升级元素 CNRG 的直立身体已获用户认可，手、腕、肘及五指仍要自然；气旋元素 CSTORM 两只手要看清，环流是成组的宽气旋，不是乱麻细线。石人禁止方块拼装和噪点麻皮。圣龙的威严和鳞片必须在实际1X形象仍可读。创新要保留一眼能认出的H3身份。
- CENCH G20/G21父图已经生成并入库，禁止依据旧交接重复生图；G14新汇总已从当前F7同步；CENCH无G6。凤凰原先保留用户选定a19作为熔金身体/火焰材质父图；用户2026-10-14明确指出它和鹰太像，重钩喙、暗眉、大黑爪不再是可继承的选定细节。后续只保留a19的可识别主体与材质，头颈、脚爪、尾羽须按最新神鸟方向改，并逐组审实际1X。
- CENCH G20/G21旧交接所述共同K12、主体约原版74%及G21触及alpha边界，不能当成当前缺陷。现行manifest只接收两组a03，导出收据的共同K均为9；四张当前3X在alpha≥2的底边余量均为2像素，实体高除以3分别约93.67/94/93/94，原裁片高为97/97/96/95。当前1X接触板逐帧对照显示体量接近原版；这只消除旧尺寸/触边返工理由，完整动作艺术、原生时序和实机仍需独立验收。冻结1X收据 `.../codex-review/人物生物/receipts/CENCH-frozen135-all19-native1x-r18-20261011.json` SHA256 `97ae896142eb5e6c0e13b088515f7208026fa5d41523bd323b849d5d5ff1e768`，G20/G21当前3X板SHA256分别为 `da0ac46e325917b042ceff15f7f112cfda9fe93223a02fb51072bbc9fd975d8d`、`9e1faec3a0a688b1526e0d3f81da5b501ffecbbab225c31e6f0a314d45d05268`。遇到旧交接中的尺寸结论，先核唯一manifest、现行receipt和真实1X板，再决定是否修图。

## 工具与协作待办

- 三窗口的工具与返工经验归此唯一 skill；地图与城镇提供各自冻结收据，已核精确 SHA 后合并下文。它们只证明各自领域的生产经验，不代替人物生物、城镇或地图素材的正式验收。唯一美术标准仍由宝物城堡 owner 写入其 canonical JSON。
- 城镇审核结合用户已明确的光照、落地、空间关系和视觉主次；重要建筑必须有明暗、冷暖和材质区分。地形另检查接格连续，UI保留按钮/文字/操作空间。要把这类失败判据落实为图和实际尺寸的 QA，而不是只看批处理成功数。
- H3TierExport 工具目录当前 `README.md`、`src/jobs.hpp`、`tests/integration.py` 有他人未提交修改。本窗口的新 skill 与试跑产物不覆盖这些文件；针对上面5个 `NEEDS_KEYFRAME` 的后续工具改进先与当前改动 owner 对齐，再补最小测试和重建收据。
- 四张模型默认重复PNG（CSTONE a09/a10/a11/a12）已核对与 canonical 母图字节相同、无有效索引/生成引用。删除计划 `.../CSTONE-group1-a11/native-default-identical4-prune-plan-r57.json` SHA256 `53e8252459569c9491a84cafd4e0ccfca6311a5cb4c1d129a300de3784d41f5d`；本机批量删除命令被自动审查以 `blocked by policy` 拒绝，尚未删。必要父图仍要保留；后续不能把计划当作实际删除收据。

## 地图／界面窗口的已核实贡献

地图窗口给出的唯一贡献收据是 `{H3-workspace}\H3HD\assets\CompleteOfficialRelease-20261006-v1\codex-review\地图界面\receipts\map-ui-batch-tool-skill-contribution-20261013-r01.json`，SHA256 `5b345b254a16dbb328ebbb927492905488899d373753d2173a806647d9cc0a4e`；其列出的9份原始证据在合并时逐一重算SHA均一致。以下为各自范围内的经验，不自动推广为别种素材已验收。

- TRADE2 整图模型多次漂移装备格、披风/脚、底栏和控件座。先固定原版 canvas/crop/origin、98个控件矩形和底栏，再按独立控件的正常/按下/禁用层检查；原版留给动态内容的空区不能烘焙假控件。用户授权的整图几何校正保留模型PNG原字节，把校正用途另记；A77 的392条边最大偏差1个3X像素、16条底栏边偏差0只是几何证据，浏览器叠层不等于引擎验收，6X回3X也不能替代原生坐标复核。
- WATRTL 海面应有深蓝光学层次，SWMPTL 沼泽应读成湿泥、泥炭和苔藓；不能以画意模糊水或纯杂色代替真实材质。逐帧按官方来源、原生96/32像素、4×4接格、光照与相位检查。八张1254方形现成母版已用当前正式 dist 的一个多源批次导出8张96×96候选：plan/check/expand/verify四步合计3169ms、0新模型调用、源字节未改、完整PNG和RGBA SHA均匹配。收据 `.../codex-review/地图界面/receipts/ground-batch05-eight-real-tool-candidates-r01.json` SHA256 `c948478b28e57f967f6ef96ca5e8b9ab90c434bb9d9925261d2f86bd61b16bf1`。这证明批次减少重复工具进程（原逐图四步约24次启动，合为四次），0新AI只因复用8张已有母版，不能宣称3秒画出8张新设计或已省8次新绘画调用。这些只是工具候选，后续须由正式 adapter 接收；美术和游戏均未通过。汇总读取器第一次误把 plan 当成含 output_pixels 的 run，修汇总时没有重跑图片，故调用方必须按实际报告 schema 读取。
- 四种真实河流 CLRRVR/ICYRVR/LAVRVR/MUDRVR 各13帧共52槽，不能拿 WATRTL 海面代替；该次旧正式 scope 为4870，补齐52后为4922。4909是已纠正的早期草算，不能当基数或未来固定上限；后续须按当前索引重算。官方裁图像素尺寸有时不同于 Complete crop×3，批量前把视觉来源与运行 canvas/crop/origin 分开核查，不把河岸参考拉满全格。当前河流尚无新画面通过，解码器格式2 seek 警告也仍在范围内。
- 河流只读几何预检 `.../codex-review/地图界面/receipts/river-official-crop-preflight-for-tool-batch-r01.json` SHA256 `efc8c73e95596ce4fb311951702c70eeeead64de82364f3320f027a39d055180`；403份官方视觉源逐一核SHA与 alpha，映射回52个原生槽。44槽的可见 alpha 包围盒已经超出 Complete crop×3，另8槽源尺寸匹配仍须核原点和美术；没有透明边无损裁切可覆盖的河流缺口。故 CEELEM 的 `reframe` 不能推广到这44槽；工具下一步须显式分离官方 source 几何与 Complete target 几何，注册图元/原点后才考虑网格重映射，拒绝丢可见像素或把河岸拉满32格。ICYRVR 13份文件没有 `_0` 后缀，不靠命名猜相位。原生52槽不是403个新槽或403张已接受画面；本预检没有新PNG/AI调用。
- 地图 producer 当前冻结的 exporter 指向已经清理的 build 路径；同SHA备份还在 dist/archive，现行 dist 又是另一SHA。先由地图 producer owner 修正式入口绑定/重新限定发布来源，核可执行文件存在、SHA和目标几何，再恢复八张现成母版的正式导出；不靠环境变量绕过，也不因路径错而重做模型。废稿清理只删已核无依赖的旧候选，A77与有效编辑父图保留。
- 用户指定沼泽按物理世界组织材料与水文，不要求复刻官方色板。地图窗口研究收据 `.../codex-review/地图界面/receipts/SWMPTL-physical-wetland-colour-research-20261013-r01.json` SHA256 `debbf26c6c1ef0f76fe6a16901f0e3f425e889fdc90bda31a112c1b1335e5d69`；[EPA 湿地类型](https://www.epa.gov/wetlands/classification-and-types-wetlands)、[USGS 天然水色](https://www.usgs.gov/labs/national-water-quality-laboratory/science/science-topics/color)、[Maryland DNR 柏树沼泽](https://dnr.maryland.gov/centennial/pages/centennial-notes/southernexposure.aspx) 支持有机深色土、单宁深褐水和缓流水域。橄榄绿植被、褐黑泥与灰绿水的组合可合理，但“物理上可能”仍需实际32像素下检查水体动势、材料分离、光照和接格，不能仅因偏离官方蓝绿判退，也不能给静水套白浪/全局滤镜。此结论是材料取舍，不是SWMPTL整套ART通过；已有8张AI母版绘画发生在此前，H3TierExport本轮只采样现有母版。

## 宝物／城堡窗口的已核实贡献

城镇贡献收据 `{H3-workspace}\H3HD\assets\CompleteOfficialRelease-20261006-v1\codex-review\宝物城堡\receipts\art-town-batch-tool-lessons-evidence-handoff-r1396.json` SHA256 `fc8cd3187f5caf7712f0e4064892aedb2b23474293a2db7df0a0d59e3468584e`；7份不同原始证据的文件大小与 SHA 均已复核。美术标准仍只有其 `town-art-direction-canonical.json`（当时 r1394 SHA256 `285281cb5c9f00c52938d9bff7a291274f7d3a7fc11a9b3a795f4148f7225215`），不能把此历史 SHA 当作永久当前版本。

- 九城基础／升级建筑要在原生800×374场景按冻结的绘制顺序和XY叠放，成对比较。整图须检查光照、落地、前后空间、视觉主次、透明互动；重要建筑要有明暗、冷暖和材质差异。当前场景审计 r1395 SHA256 `6abeef32397c762b75bdab51a9e8c45946a5257c34dff9bfae3598eaa2f9f671`：342个当前精确静态键之外仍有7个官方占位（元素城6个他组原型、墓园1个宽图仅母版）；动画、遮罩、原生运行与正式游戏仍开放。适合给工具加只读场景/变体槽位探针，不替代艺术验收。
- Inferno 与 Rampart 的 HRD1/HRD2、HRD3/HRD4 分属两组独立的基础／升级对，不能把 HRD1–4 简化为单一四级序列。工具若组合场景，先读 `building_slot` 与XY，按槽位对分组并拒绝缺项或重复候选。城镇最大建造原生场景收据 r1393 SHA256 `a06aad77138eac8e0e96e1712cf3c5ec262768b0dfa56d284bb008cff8863c11`。
- 魔力漩涡动画须在原生144×34检查星点是否可辨、alpha 是否意外连接成色带。模型编辑过渡帧曾把已分开的星点重新连带，被拒收且未入库；失败收据不证明模型默认输出已物理删除。两状态原生检查 r1360 SHA256 `05bdff998ca9eecc0fbc8c63c1f48c7e8a2d59b1c617c9d6c9f56813a1f6ca41`，失败收据 r1366 SHA256 `1e1d5758f66bdee7748a90b8d6aecd749e70b339273d8f9dae95781b3b883544`。可优先给工具增加只读 alpha 连通分量与原生覆盖度探针，不自动造星点或代替完整平滑动画。
- 墓园 `TBNCEXT2` 的原生宽732要求精确3X宽至少2196，当前整图母版仅2172×724；应记录 `SOURCE_ONLY_CAPACITY` 与尺寸原因，不能用CPU放大冒充新增绘画细节。来源清单 r1316 SHA256 `b99471fc648c03bdf8e568c566167da766879ad963f93584ea489237c362f198`。
- 用户仅授权排除已独立证实的主体外 alpha1 残留；主体、附着阴影和模型母版原字节保持。裁片需留下 source rect、alpha 范围、排除像素数和字节SHA，不能全局清 alpha。收据 r1362 SHA256 `dfd51841a8662b317c5a101a126bd2f9086d8aafe3beea3ad253696f9d5e5cfa`。候选覆盖度统计应区分自有精确3X、仅母版、他组原型、官方占位和已完成动画，不能合成一个“完成总数”。

## 2026-10-13：CEELEM 官方3X批量几何试跑

- 新增 `scripts/prepare_official_motion_reuse.py` 按单一 `native-resource-inventory.json.gz`（SHA256 `8b71130fd8046039a0f9a819d7e63018f8b635165afddce99de2a5e4a8c5a0ec`）冻结原版槽、源PNG哈希与原点。CEELEM 原槽81：31个源尺寸已精确匹配，14个仅有一处完全不损失 alpha>0 像素的透明边裁切位置，30个可见内容超裁片或尺寸不合，6个有多个无损位置而不能自动判定。triage SHA256 `edd93661faa656dd7d015ec298668ab0410927747a817d7bbb4ac25a96520f91`。
- `mode:reframe` 只在明确 `source_rect` 下裁 alpha==0 外边，不缩放、不清 alpha1；工具 `motion-probe` 把有一像素 alpha1 损失的请求拒为 `NEEDS_KEYFRAME`。实际批次所用隔离候选 exe v4 SHA256 `a74aa4ec4cf14b24c3998a3b451768f3ba7a230e20c477881a8993409ec666ef`；加入 triage 回归后重建的源码当前 v5 SHA256 `389a1db6a298bdead56916502be8ca1a975707f6642049cdee5493f9b57c757f`，39项 motion、2项 triage、180599项 core 与11项 quality 检查通过。正式 dist 尚无新能力，不能以候选 exe 冒充正式发布。
- CEELEM 一批45槽 `motion-check/probe` 都通过，`motion-run` 写45张候选、`motion-verify` 45/45 验证，0新模型调用且0正式 manifest/游戏写入。run 报告 SHA256 `43939a180411598a523dcb71af4962c1e9663af40e7290a089f83fd088418261`，verify SHA256 `4f4ab6e5436c116f8ea6f104db9c6223c60a40c1e81be5adb98a79f568edbb36`。这里节省的是重复几何裁切与来源整理，不是重新绘画：实际查看候选仍有用户拒绝的碎石麻点，应作姿态/几何参考，未升级形态的哑光连续大块岩面尚须新画；36个停留待审。艺术、时序、实机全部开放。
- 进一步按来源 PNG SHA、原槽几何和无损 `source_rect` 建等价类：45个可用槽实际只有33个独立原画/几何 owner，12槽可通过 `reuse_from` 复用；原版每槽及其时序仍独立。新脚本和真实81槽只读 `motion-plan/check/probe` 给45 READY、0图写入，报告 `.../codex-prepared/人物生物/CEELEM-matte-alias-r61/alias-probe-r61.json` SHA256 `ee374fd37bfcefa8b6dcf44b8e99e223a6f8a03a74179eb1058d5489e891f30d`；这只减少重复来源处理，不补绘画细节。
- 未升级土元素两姿势哑光重画已冻结官方55/56来源、原裁片和完整透明sheet请求；首次 built-in 图像生成在输出审核阶段返回 `moderation_blocked`、category `other`，没有PNG或更具体原因。失败收据 `.../codex-prepared/人物生物/CEELEM-matte-alias-r61/imagegen-attempt01-blocked.json`；不得把请求或工具旧官方候选计为新画，也不换入口规避拦截。

## 2026-10-13：中立生物母版扁平目录迁移

- 用户指定中立生物母版直接在 `codex-高清母版/生物/中立生物`，不再按 `选定` 子目录分层；最终采用提示词放 `中立生物/提示词`。53张既有 `选定` PNG / 80,981,038字节已逐一复制到根并核原字节，旧路径的582条不可变映射（原529+新增53）验证通过，唯一manifest CAS从 `45e4aadc466e` 到 `553636b9df84`，当时设计引用3项改为根路径。H3 main单一集成人提交 `3c847a5e` 修正式producer对中立生物的未来写入路径；当时53别名读回通过。分层收据 `.../codex-review/人物生物/receipts/creature-neutral-flat-move-r62.json` SHA256 `ebd66e01a7bec805fcc63d95aad5821913e051722341c717ecbf96e4da24560d`。后续物理删除见下条实际收据，不能把复制和索引切换当成删除完成。
- 唯一“已采用提示词索引”列出13份中立成品。逐份核原请求 `prompt` UTF8 SHA、母版、请求和证明后，13个TXT字节原样复制到新 `中立生物/提示词`；收据 `.../codex-review/人物生物/receipts/creature-neutral-final-prompts-r64.json` SHA256 `366c94dc7a5601411bafec02e65df9f917883588a8d26124ef23b51c662e5574`。索引字段 `prompt_sha256` 指 **原prompt文本**，不是包装元数据后的TXT文件SHA；当时索引SHA `601b6c9372fb8681d9e1e0ff507686ba37d33ade4f43785498e1d785175cd831`、中立13/13读回通过，本组不建第二索引。后续元素城11份亦迁到本阵营的 `提示词`，仍使用这一份共用索引。
- 新旧53张逐字节相同、现行manifest/提示词均已从旧 `选定` 路径切离。旧副本与空目录的非递归删除计划 `.../codex-prepared/人物生物/neutral-flat-move-r62/selected-old-prune-plan.json` SHA256 `44dd5a82162448a42a50a93ced55b7e90ee15deac01b51b3b90e2fd39220e160`；历史第一次删除曾被自动审查拒绝。用户随后明确授权继续精确清理；53张旧副本及空目录已实际删除，见 `.../codex-review/人物生物/receipts/creature-neutral-cleanup-actual-r65.json` SHA256 `d4e61bc946bd757a6c5bc60ed234cb08520b47ef246df89361ded98324d0e803`。
- 10张CNOMAD原图已核为几何拒收或取代，当时请求、manifest、展示页与已采用提示词均无引用；精确删除计划 `.../codex-prepared/人物生物/neutral-discard-prune-r63/prune-plan.json` SHA256 `9e62589eb0d89a742183b83a70a2e978b247be44cb8a2fb33518f1ecb2ad6190`。历史首次删除被自动审查拒绝；用户随后明确授权，10张已实际删除。与上述53张合计63张／95,660,277字节，实际删除收据同为r65。清理前扫描必须再加上下一节的完整编辑祖先链，不能沿用当时只核直接引用的判废方式。
- 用户要求阵营成品提示词归各自目录。唯一 `生物/提示词/提示词索引.json` 已由其owner更新到SHA256 `aed7bb6d809dd354d8be75353bd72453b8a03f5d64aba45f8111dec36dde7529`：中立13和元素城10份现行记录均指向阵营 `提示词`，缺原PNG的CMAGEL提示词仍在unresolved中。元素城11份TXT逐一与旧共用目录核同字节和索引引用后，旧11份／23,780字节已精确非递归删除；共用目录仅留唯一索引。实际收据 `.../codex-review/人物生物/receipts/creature-elemental-prompt-prune-r70.json` SHA256 `001044423a0d4bf430ac360934b660db339d232700faec5bd27584e05fa2b2ef`。

## 2026-10-14：删除候选必须检查整条编辑祖先链

- 扁平化清理时只扫描当前owner直接引用，误把仍供新请求编辑继承的旧母图判废。精确复核 `.../codex-prepared/人物生物/creature-lineage-audit-r69/active-lineage-audit.json`（SHA256 `df4f2bf36f0752c564894d832abf2fa1149b4e70da8f22bdc5d9ca79de8e2f4e`）：85个当前已导出owner需追溯119份 `supersedes_request` 请求、241组有SHA的PNG引用；迁移后68条原路径失效，其中66条有同SHA阵营根目录副本，2条暂无同SHA根目录副本。`CNRG_group1_all1_raw_a02__03e2d3c8012a.png` 被上轮误删，`CENCH_group5_all2_raw_a34__4d95b3c7daec.png` 也仍被G5编辑请求引用。目录整平和候选删除一律先沿所有当前owner的 `supersedes_request` 追溯祖先，再核每份请求的 `referenced_image_paths/reference_sha256`、祖先的 `raw_receipt`、当前 `group_export_receipt`；不能只查顶层请求、manifest现行设计路径或候选索引。原PNG找回必须同字节同SHA，不能用导出图、别的设计或重编码图冒充冻结父图。路径映射必须累计、按真实字节与单一manifest writer的CAS生效；旧证明原字节不回写。
- 66条有原字节的别名已并入唯一累计路径图 `.../codex-review/人物生物/receipts/creature-neutral-elemental-flat-path-map-r69.json`（SHA256 `2d58eb3dd2d414c266e57a31c6044149928f94fa0bf8abceb89e81f9dbfb742b`），从814增到880条；唯一manifest经正式writer的CAS切至该map，SHA256由 `734e3d918e32e799ea93586ba47a57687aa8b45bf847132fe8084adb3e471925` 到 `05df65cac738d9b75ba4ec1671b1752bfe2e55ce689387f9ab54a85bc773f56b`。新进程真实读回66/66同SHA，另2条保持 `ASSET_PATH_NOT_LIVE`，没有降级守门或伪造父图；CAS收据 `.../codex-prepared/人物生物/creature-lineage-audit-r69/manifest-cas-r69.json` SHA256 `698a8b969929e659298ba47933569d98b93b37b5105f1bf9f2f8a1887573f8fb`。CSTORM G1已有3X proof和PNG，但此映射修复本身不证明完整动作汇总或缺失父图恢复。
- 汇总页刷新另有过宽收集缺陷：重复组只依赖其冻结 `logical_slot_bindings[*].art_owner_slot[0]` 中的来源组，旧实现却遍历同一身份全部历史已导出组，导致无关的CENCH G5丢失父图阻断其他组。正式adapter现将 `exported_owner_pictures` 限定到该重复组真实来源组并按 `(identity, groups)` 缓存；若必需组缺失依旧原样拒绝。104项源码回归通过；实际页面更新后34个原生回绑组与旧页同组同帧，没有移除有效组，页面SHA256 `836a39603f0cf9e7231107e3b4159c096056186d8e03feb33f80219d884e9518`，收据 `.../codex-prepared/人物生物/creature-lineage-audit-r69/animation-review-refresh.json` SHA256 `dc3e4d890ee70f96c929505eac8dbd150cd995b0273812424da6007ab2ac1545`。CSTORM G1只有F0正式3X候选、`original_sequence_rebound=false`，故不在完整动作汇总中；刷新通过不等于本组完整时序或两张丢失父图恢复。

## 2026-10-14：地图／界面窗口补充的可复核工具经验

地图窗口的唯一交接收据 `.../codex-review/地图界面/receipts/map-ui-tool-skill-current-lessons-handoff-20261014-r01.json` SHA256 `a38a3e5728c1d9dd25b72095407158ade08ea2c8f0f455ef5cbbfa61c2349940`；列出的四份原始证据已复核实际字节和SHA一致。本节是工具/审查经验，不是第二套美术标准，也不代表其R25/r05源码方案已经应用；该方案收据仍为 `SOL_REVIEWED_PROPOSAL_NOT_APPLIED_CURRENTNESS_OPEN`。

- 官方原版的索引色、调色板或几何严格等价，不能证明 OfficialHD3X RGB或模型编辑父图同字节、同形体或艺术通过。ABF01D a01/a02 的八帧别名样本虽然源PNG有有效SHA和原槽匹配，整图检查发现两批均裁掉每帧连通的可见旗帜区域；投递前必须拒绝原生crop外的 `alpha>0`，保留完整来源与诊断，不靠裁切或清alpha让工具过关。失败证据 SHA256 `d2c3d4640e8d3d9fc34a30f8598c78b02b1c0b33feeeae8bb16de7d100c2568c`。
- 河流和旗帜的官方源都可能有超出Complete目标crop的可见延展。工具必须把source画布/原点与native target裁片/原点分开注册，并为每个可见源像素建立去向；不能把超框内容悄悄丢掉。现有八张水面/湿地设计经过真实32/96像素和混合格审查仍保留为候选，未证实需逐张重画；原生翻转、调色板、计时、正式adapter和实机继续独立开放。后续批量应先复用合格候选，0新模型调用不等于0待验收工作。
- 对生成元数据的路径绑定，建议读取与解析同一缓冲、校验请求schema及准确category/key/attempt、同优先级冲突即拒绝，并在投影前复核所选源字节。此为地图窗口已审方案，当前source/currentness仍OPEN，不可记成已在正式dist生效。该交接本身0模型调用、0候选PNG、0源码修改。

## 2026-10-14：凤凰与鹰的区别要连同原槽体量批量验证

- 最新用户要求凤凰一眼不像鹰。旧a19的熔金胸身、羽翼、火焰仍是材质父图，但重钩喙、暗色猛禽眉、外露黄鳞腿和大黑爪须去掉；长而上扬的焰羽冠、细小近直金喙、羽火包住的脚与较短的爪尖，以及顺身延伸的尾羽能在真实1X形成更清楚的凤凰轮廓。不能只把旧鹰头涂金。当前局部审核收据 `.../codex-review/人物生物/receipts/CPHX-G13-firebird-redesign-art-review-r74.json` SHA256 `7639b3f18c35461db194ead922954e0a520a72774f812ff795c1398a9b500094`；a27 F11与a29 F0/F1原始模型PNG和精确3X已冻结，整组时序、其他凤凰帧和实机仍开放。
- G13 F0/F1曾在a28把横向尾羽和火焰铺宽：几何 `FIT_AVAILABLE`、3X导出皆通过，但共同K9使alpha≥16主体面积仅为上一版a24的65.01%/65.87%，游戏1X高度明显不够。a29保持两个原版独立动作，把火焰向上、尾羽向下收拢，生成sheet同图两帧共有K7，精确3X的主体面积回到a24的93.63%/97.41%；1X高度仍约原裁片的91.84%/88.24%，不能用工具PASS替艺术验收。冻结请求应给每格原裁片的宽高、同组可行K、主体高度和透明边目标，先在同一批的真实1X对照体量，再正式接收。a28必须保留作a29的必要编辑父图，而不当成品。
- `sequence-review`要求H3TierExport `expand --report`、`expand-verify --report` 的固定报告路径；只有CLI stdout和单项receipt时会明确报 `SEQUENCE_REVIEW_PREREQUISITE_MISSING`，不应伪造报告。已存在单项receipt的再次 `expand --report` 会走 `REUSED_VERIFIED`，复核原字节而不重复重采样。本次a27首次漏传 `--report`，核单项结果后幂等恢复，未再次生图。正式工具仍缺任意正式sprite的同核 native1X 审图批次；本轮FFmpeg缩小图仅是有标记的非canonical诊断。
- 后续用当前H3TierExport产品采样核做完整G13 1X板，收据 `.../codex-review/人物生物/previews/CPHX-G13-current-product-kernel-1X-r75-20261014/review.json` SHA256 `9edde970e6989fcc46b407d60891a0f8b48015fa643e3acee9b3ce186c8040f6`。初看a27 F11的alpha底边比官方全图高48px，但官方底部含脱离主体的火星，不能误称“脚底悬空”。单独连通域更正收据 `.../CPHX-G13-F11-main-component-audit-r75-addendum.json` SHA256 `51eb1cd3215688dc95f90849c6de2e7f8aac05e518e99a6733497945e24ff22c`：官方主体与a27主体足底都是y=183，足点PASS；排火星后的主体面积84.44%、高度90.5%，不能仅凭整图bbox无谓重画。F0主体面积仍只有官方76.32%，是实在的偏瘦问题；F1约91.06%接近参考。审阅页和指标都需分清主体、特效、足底，而不是用全alpha包围框替代。
- 全15组旧凤凰回放真实99槽/66不同PNG。`.../codex-review/人物生物/receipts/CPHX-preserved99-cross-group-eagle-scope-audit-r01.json` SHA256 `3503b2efe5717ff383a609b7cfb2aa5b6b473204df80f9864fff710c7fd768c6` 将活体旧鹰式画面与G5 F4–F6焦黑/余烬/灰烬分开：保留3张死亡效果；G13已新画的F0/F1/F11不重复生；旧活体余约60张独立画面可组织成约33个成对请求，并按G7–G10、G20/G21及G11/G12跨组等价类一次画后复用。完整艺术、时序和实机仍开放；33是计划批次数而非已接受数。

## 2026-10-14：动作算法与地图可见损失的真实回归

- 对CBOAR五个留出动作帧做隔离FFmpeg双向光流试验：平均alpha IoU比线性淡入约高0.011且颜色误差降低，但5帧全部产生DEF裁片外正alpha，脚底误差变大，轮廓有2帧变差；更细网格耗时15.8秒仍未达标。报告 `.../codex-prepared/人物生物/H3TierExport-cboar-motion-flow-r05/report.json` SHA256 `fa0ae42bcdcdd4d2945ca0de3f8698f8b73ec9d7b552339db25110dbcc0efa7b`。故没有接入工具或把5个 `NEEDS_KEYFRAME` 改成PASS；下一轮先增加真实姿势关键帧或骑手/坐骑/四肢分层对应，再以留出帧核alpha、脚底、轮廓、速度。
- 地图窗口用当前真实来源发现道路51槽中24槽裁片外有正alpha、其中9槽是较不透明的可见损失；收据 `.../codex-review/地图界面/receipts/road-current51-visible-crop-loss-preflight-20261014-r01.json` SHA256 `2f0927804f79e8936d03643130fb6d4a5a02ee4244622681b8f94566d7d9dd94`。R25/r07适配器在现有tile/actor/road实际候选上都拒绝可见裁失，错误分别为 `TILE_VISIBLE_PIXELS_OUTSIDE_ORIGINAL_CROP`、`ACTOR_VISIBLE_PIXELS_OUTSIDE_ORIGINAL_CROP`、`ROAD_VISIBLE_PIXELS_OUTSIDE_ORIGINAL_CROP`；执行收据SHA256 `a0094a96a4f1dfd4246a8c5dd69e941f4f47ce07ed0d0725bf33bec6a1ba095f`。8张已有水面/湿地母版复用正式3X批次，8/8导出候选、0模型调用，收据SHA256 `6cead42486eb301f8386396288cddee4f6653c1701ac59739ceb397e2f7578db`；接格、美术与游戏仍独立开放。
- Map窗口另冻结 `motion_source_coverage` 的正alpha漏洞：原算法忽略alpha=1（`a<2/255`），会允许可见源像素掉出网格。r02私有补丁把豁免限制到完全透明，且把算法身份v3→v4，防旧receipt/cache绕过；私有原生0..255内外测试各512例通过，证据 `.../codex-review/地图界面/receipts/tool-motion-alpha1-private-native-regression-20261014-r01.json` SHA256 `fd6ac6f5ebfb6314b0b7ff1f755a70b6785bad853284b1a1c88f60c9864aeffa`。生物窗口独立clean worktree的单行源码候选也跑过43项motion合同，但它未包含v4身份修复，不可单独发布。唯一Tool writer现已将v4和EWA零权重快路并入正式发行：dist EXE SHA256 `a5998c67c83b4eed24bda8b6590ef499b2fbaa5da5f68d363dcf8d396bac301b`，发行收据 SHA256 `107e1a0c4e00474446e5a80f05984b34a7418ce60859516a43fc04d0b133fd91`，16项全门禁及42项motion实际测试通过。版本号仍为1.3.0，必须按EXE SHA区分新旧；旧v3批次从冻结原spec重新 `motion-plan`，不改旧receipt或手调算法字符串。生物窗口用新EXE对旧算法合同下的凤凰a29双帧精确3X批次作只读 `expand-verify`，2/2通过、零PNG写入，报告 SHA256 `1febf45667cf9d0cdb3e46b64b6590c0c14e87d323d93ae241f64f797945f968`。这证明旧3X输出在新EXE中可读回，不等于motion v4的真实素材质量已通过。
- G13新设计现只有相邻F0/F1和独立F11。F0/F1之间无留出槽，F11跨明显动作与裁片体量；F4/F6/F8、F5/F7/F9各自是来源重复，两个交替动作不能假装平滑中间帧。故暂不构造虚假的Phoenix补帧pilot；先让同一新头爪设计覆盖含真实中间帧的相邻区间，再用 `motion-plan/check/probe/run/verify` 和留出帧检测。
- 新正式EXE已用冻结CBOAR r57原spec重新 `motion-plan` 得到独立v4批次：3个关键帧仍PASS，5个留出帧仍 `NEEDS_KEYFRAME`、零候选；旧/新端点PNG逐字节相同。真实48×48边缘用旧v3正式EXE会接受alpha=1网格外源像素并在输出把它丢掉；v4 `motion-probe` 在任何PNG写入前以 `mesh hull discards visible source pixels` 拒绝。总回执 `.../codex-prepared/人物生物/CBOAR-motion-v4-pilot-r01/motion-v4-pilot-receipt.json` SHA256 `69558c29711f108fe355eba705ce682aa2d5bf69e1b6cc65e014e749e91a4af3`，alpha专项SHA256 `c794bf10df70bfe2808b5220155d9a2c63435e6664cf6e3475db5db01df20420`。单次v4 probe约1.40秒，对比旧并行v3单次2.26秒约快38%，不是充分性能基准。3张与旧v3完全相同的v4冗余PNG共291624字节已按SHA删除，保留验证receipt，清理收据SHA256 `5c6426b870238cd5af6dc054b50b1e32b5ed5346f44547432bc0ba6586eb2483`。
- CPHX G13 a25/a26是已断开的试验分支，a25仅被未选用a26作为编辑父图，二者无活动后代。核唯一manifest、请求祖先、提示词索引和审阅页后，两个母版PNG及各自同字节生成器副本共4文件／4,742,326字节已按准确SHA非递归删除；a19/a24/a27/a28/a29必要父图和全部冻结请求/收据保留。完成收据 `.../codex-review/人物生物/receipts/creature-cphx-g13-a25-a26-unused-png-cleanup-r15-20261014-completion.json` SHA256 `10c14a65c28d8732670d980195947eb4d742e4b40a8de0c7975eb977e2580a0f`。a26没有独立艺术拒绝收据，只可记为未选用无活动后代；PowerShell精确删除曾在执行前被自动审查拒绝，用户已明确授权这类情况完成删除，最终按每文件SHA/大小复核后用非递归 `pathlib.unlink` 完成，不得把原拒绝当作已删除。
- 删除活动候选时沿整条编辑祖先链追溯；对丢失的CENCH G5和CNRG G1原始父PNG，额外扫描137126个松散PNG和407个ZIP/TAR目录仍未找到原SHA，收据 `.../codex-prepared/人物生物/creature-lineage-audit-r69/missing-original-search-r72.json` SHA256 `d30e5d2f8de346d356fd495239a475ecd847db81bc6d3313fdbc3c5f04738cee`。不得把导出3X重编码为“恢复原母图”，也不能因历史父图丢失就放宽当前活跃组的来源验证。

## 2026-10-14：凤凰头爪与真实正常路径回归

- G13 F2/F3 的 a33/a34 尽管已按“短直喙、短爪”提示并精确3X导出，查看原始PNG和3X仍有下钩喙、猛禽眉与抓握脚，不能仅因火冠/配色就称与鹰区分完成。下一轮 a35/a36 把编辑目标明确为圆润发光的脸、极短无钩楔形嘴、后扬的分束焰羽冠、羽焰包住小腿及紧凑火焰趾；保留各自官方动作、身体材质、透明留边与落地。a35/a36原PNG均已存，单帧各自一次等比例精确3X导出并回读通过；F2 K9、384×426，F3 K7、594×441。现有板 `.../codex-review/人物生物/previews/CPHX-group13-a35-3X-board.png` 与 `.../CPHX-group13-a36-3X-board.png` 只能作为3X候选，实际1X、完整动作和游戏仍需审查。必要编辑父图 a33/a34 应沿引用链保留，不当成当前画面。
- `inspect-sequence-fit` 只说明可无裁切注册；它曾对a28和a33/a34给出FIT，却无法证明原槽体量、凤鹰识别度或真实1X材质。因此批次报告要同时给主体连通域面积/宽高相对原槽、排除脱离火星后的足底、alpha1透明边和1X可读性，并把诊断阈值与最终艺术裁决分开。建议产品工具质量核直接产出这些可复核指标；不自动改图、不放宽保留可见alpha的规则。
- 2026-10-16 alpha-aware 纠偏：attached alpha1 必须原样保留，不能把其 RGB 颜色计数当作源图硬失败，也不能清 alpha 来掩盖 ghosting。先记录 alpha>=2 是否触及画布边缘或原生裁片外，再对正确 alpha 的 3X/1X 做白底与深底合成观察；native-review 收据应记录两种合成证据和 alpha>=2 几何字段，状态保持 `REVIEW_REQUIRED`，不自动授予艺术、时序、runtime 或游戏通过。AVCSTRO0 R07 证明 producer 的 exact3X 必须绑定 prepared source，而不是 raw generated PNG；使用 owner preparation 绑定的 1728×1359 `source.png` 后与 exact3X 逐 RGBA 相同。
- H3 adapter 的 `prepare-sequence` 新增可安全恢复半写入批次与跨进程manifest锁后，真实a35正常路径首次运行在已写prepared PNG后因未定义的`width`失败；这不是模型失败。修复为每帧从冻结槽`crop_1x`取宽高，新增执行真实正常分支至原生索引/placement/batch/CAS的隔离回归，完整`test_full_art_*.py`由117增至118项通过。随后同一a35原图无再生图地重跑正常路径成功，a36也成功；旧r72/r73源码交接包HOLD，唯一新r74包已交Main待接收。以后所有resume/partial-write改动必须同时测试首次正常路径和恢复路径，再拿真实冻结批次回放；源码测试PASS仍不等于画面/原生/实机验收。

## 2026-10-14：城镇水景官方动画复用边界

- TBCSEXT2 当前定稿首帧对应的9个后续原槽，可用现有正式 H3TierExport 1.3.0 对应的官方 HD 3X 不同帧做精确复用；9/9 `motion-plan/check/probe` 技术就绪，18个官方3X源／原1X参考哈希复核一致，probe 写0 PNG。首次本机投影缺 `frames` 顶层字段时计划失败；只补齐证据投影、保留失败记录后，正式 EXE 正常通过。详见母版 `制作资料/宝物城堡/动画复用可行性/TBCSEXT2-motion-reuse-feasibility-20261014-r01.json`（SHA256 `e96831c7fa71c990a3627ea15414a8665f5397d17f057a5c5996b63dd55cee6f`）。
- 这只是几何／来源候选，不是艺术或实机通过：当前新首帧岩石细节更多、双水流更饱满；旧官方序列水流较窄、较柔，接帧可能有风格和轮廓跳变。方向、时序、循环和真实城镇场景尚未知；不可将静帧复制到动画槽。若场景审阅判定不兼容，保留九个槽，按原水流逐帧设计关键画面，再冻结原生时序后验证；不返工已通过首帧。
- 本次受限扫描另找到20组5–10个追加槽可由官方帧精确复用或唯一无损透明留边重排，说明1270个待补3X槽不等于1270次手绘；该计数仅是来源／几何筛查，不代表风格、时序或游戏验收。当前全局 `asset-index.json` 改为 f9cc 后不含 TBCSEXT2 key（0次匹配）；城镇首帧来源以 selection receipt 为准，资产总索引无该城镇键级绑定。

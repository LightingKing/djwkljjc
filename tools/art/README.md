# 离线美术管线

仅用于用户个人离线同人内容，不对外分发。仓库只保存工具、提示词模板、来源清单与哈希；生成 PNG、抠图、预览、模型与资源包放在外部 `runtime/`。本工具不接触服务、模拟器、APK签名或TSS。

## 工作流与接口

1. `python -m tools.art prompt full_body --name ... --identity ... --pose ... --outfit ...` 只输出提示词。生图由图像工具执行，记录实际提示词、参考图SHA256、生成时间。1607必须明确魔王形态；全身图必须独立生成，完整头、手和脚均入画。不要把半身缩小冒充全身。
2. 使用独立环境执行 `python -m tools.art cutout <source.png> <new-output.png>`，调用 `rembg/isnet-anime`；既有原图不覆盖。模型及版本/hash另记于该批来源。已有环境在 `runtime/art-gen-20261104/.venv/`；工具不会隐式使用该绝对路径。
3. `python -m tools.art derive --portrait <cut.png> --full-body <full-cut.png> --actor-id 1501 --anim-res 1501a --face-box 400 100 200 --output-root <new-review-dir>` 生成12张美术图；可加 `--logo` 和 `--introduction` 传同Role原图，合计14张。输出不覆盖已有文件。可用 `detect_face(image,cascade_path)`建议坐标，0/多脸时要求人工坐标；实际裁切仍须预览复核。
4. `python -m tools.art record --asset-root <external-root> --spec <spec.json> --output <art-origins.json>` 批量记录每张PNG。spec是 `{path,generatedAt,origin}` 数组，path相对外部根；origin含 `kind`、实际 `prompt`（generated）或 `operation`（derived/original-copy）及 `references:[{path,sha256}]`。新记录恒为pending，工具不能自行批准。
5. 向用户展示图像，得到明确确认后记录对应**最终PNG**哈希的approval：`status=approved`、`confirmedBy=user`、`sha256`、`confirmedAt`、`evidence`（本轮确认消息/记录位置）。由人或负责交互的代理记录，不设自动批准命令。最终裁图如有变化必须重新审阅，不能沿用旧哈希。
6. 构建器先调用 `tools.art.origins.verify_approved_asset(entry,asset_root)->bytes`；无审批、哈希变化、路径越界、尺寸/mode变化均拒绝。再调用 `tools.art.bundle.clone_texture_bundle(template,png,target,expected_size=...,expected_format=...,alpha_source=...)->(bytes,report)`。低层纯bytes接口也用于合成测试图，不能由此绕过真实角色图的审批流程。

`clone` 命令要求单条审批记录、显式模板SHA256、目标、尺寸、格式与alpha来源。它只输出新的外部资源包，禁止隐式缩放。`inspect` 命令只读回包名、容器、CAB、纹理尺寸/格式/alpha。

## 格式与验证

- ETC2_RGBA8=47：RGBA PNG，`alpha_source=rgba`。Alpha8=1：RGBA的真实A通道用`alpha`；显式L遮罩用`mask`。禁止把RGB亮度隐式当alpha。UnityPy原始A编码要求RGBA容器，代码把指定mask放入A后编码。
- 模板只接受1个CAB、1个Texture2D、1个AssetBundle、1个正确指向纹理的容器，不支持外部stream。
- 克隆同时改AssetBundle的m_Name/m_AssetBundleName、容器路径、Texture2D.m_Name和内部CAB；CAB由目标路径确定，多个目标互不冲突。
- 序列化后重新读取核对名字、路径、尺寸、格式。Alpha8逐像素无损；ETC2为有损格式，透明度每像素最大差不得超过20。双构建必须字节一致。正式补丁仍需Lua/FCI、ZIP及完整验收。
- 原1502尺寸：portrait/full1024×1024，headBig256×512，headRectangle256×256，selectTeam256×1024，gacha256×128，infight main/main_r128×128、sub_2 512×128，journey512×256，skill512×64，logo256×256，introduction512×1024，skinHeadBig438×530。
- `derive_ui`机械拒绝缺全身或逐像素相同的半身输入，但不能自动判断人体完整性；人工审核不可省略。skinHeadBig是按全身alpha与亮度生成的显式灰度遮罩，须与其余预览一起审核。

测试：设置 `LJJC_TEST_ASSET_ROOT=I:/workspace/mobilegame` 后运行 `python -m pytest -q tests/test_art_pipeline.py`。真实原版ETC2/Alpha8模板加程序绘制测试图验证包装；不会包装未经确认的角色素材。抠图单元测试验证后端边界和alpha，不把受控后端测试冒充模型质量验收；本次新图仍要单独视觉确认及隔离客户端验收。

临时验证出处为 `runtime/art-gate-20261104/clone_bundle.py`、`s8.png`、`s18.png` 和v21–v24测试ZIP；这些只是1–3张portrait的有限证据，不等于14类UI或全部角色验收通过。现有 `derive_ui.py:79` 的缩半身full_body方案没有沿用。

# M2.1 APK 清单

由 `tools/build_apk_inventory.py` 对原版 APK 的 ZIP 中央目录生成，未改写原始 APK，也未把其内容复制进仓库。

- 文件：`I:\workspace\mobilegame\downloads\djwkljjc_v2.0.4_bili.apk`
- SHA-256：`b997dbac9ff1764b46809486d83e4a65c6d511cae17fee6f8063ef598d66a776`
- 条目数：19,433
- Unity 数据条目：109
- assets 条目：18,613
- 原生库：37（`armeabi-v7a` 19 个、`x86` 18 个）
- 清单数据：[data/apk-inventory.json](../data/apk-inventory.json)

清单包含每个条目的路径、分类、压缩方式、压缩后大小和解压大小；重新运行同一命令会按路径排序并生成确定性结果。

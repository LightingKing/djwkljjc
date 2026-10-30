# M2.6 Protobuf 描述符审计

`tools/audit_descriptors.py` 对 `data/descriptor_set.pb` 和 `contracts/api.yaml` 做可重复比对。它只报告恢复结果，不根据字段名猜测缺失消息，因此不会把兼容 JSON 当作官方 protobuf 定义。

恢复导出中有少数文件的 `syntax` 元数据被序列化成非法字节。审计会把这一个元数据字段规范化为 `proto3`，并在 `repairedFiles` 中记录原始十六进制值；消息、嵌套消息、字段号和字段类型都不会被改写。无法安全加入描述符池的文件仍会留在 `unresolvedFiles`，不会被伪造。

运行：

```powershell
python tools/audit_descriptors.py `
  --descriptor-set data/descriptor_set.pb `
  --contracts contracts/api.yaml `
  --il2cpp-dump analysis/il2cpp/dump-v6.7.46-x86/dump.cs `
  --output data/descriptor-audit.json
```

审计结果中的 `resolvedNames` 是服务端可以从描述符池构造的消息；`missingNames` 是契约表引用但无法构造的消息；`declaredButUnresolved` 表示消息名在输入描述符里存在，但被其它缺失或损坏依赖阻塞。`skippedFiles` 会列出仍无法安全加入的文件，`repairedFiles` 列出仅修复语法元数据的文件。枚举引用单独放在 `contractEnumTypes`，不会被误报为消息。

当前结果为 23/23 个契约消息类型和 1/1 个枚举类型可解析，`ApiServerUpdateInfo.result.TimeInfoEntry` 已从恢复的 `playeraction.proto` 嵌套 map 描述中解析出来。另用 Il2CppDumper v6.7.46 的 `dump.cs` 做了只读交叉核对：1,608 个 `IMessage` 类中有 21/23 个契约消息类型能按类名和 `*FieldNumber` 常量匹配；剩余两个是 protobuf map 的合成 entry（`ApiDbAccess.result.ResultEntry`、`ApiServerUpdateInfo.result.TimeInfoEntry`），没有独立的 C# 类可匹配。报告把这些字段号记为 `static_candidate_only`，不会写回 descriptor，也不会把静态类证据当成服务端实现。该结论只代表描述符结构和静态类证据可用，不代表对应服务端业务已经完整实现；接口行为仍以契约 smoke 和本地路由测试为准。

同一份报告还保留全量描述符资产清单：当前集合包含 79 个 `FileDescriptorProto`，共 1,748 个顶层/嵌套消息、131 个 enum 和 222 条依赖边。每个文件记录包名、依赖、消息/enum 数量、规范化状态和序列化 SHA-256，便于后续替换单个损坏输入时发现漂移。7 个文件仅做了 `syntax` 元数据规范化，`aq.proto` 与 `redpoint.proto` 仍因依赖缺口无法加入池；这些状态不会被折算成“完整恢复”。`data/descriptor-audit.json` 还记录 `dump.cs` 的 SHA-256（`17f46856e41d149b8d9bcc55b4fd9cdb9648405f3f2db218143d4b412fd5c48d`）和静态候选字段号，便于复核 Il2CppDumper 输出是否发生漂移。

报告的 `descriptorFieldEvidence` 还按字段号做只读交叉核对：当前 21 个能匹配到 C# 类的契约消息均为 `field_numbers_match`，没有发现描述符比静态类少字段或出现额外字段。这个结果只确认字段号集合一致，字段类型、map 合成 entry 和业务语义仍以描述符及运行时契约为准；它不会把静态字段号写回描述符。两个 map 合成 entry 没有独立 C# 类，继续保留为未匹配的静态证据边界。

这个工具不修改 APK、不访问远程服务，也不会把缺失字段写回描述符。补齐某个类型前，应先增加独立的 wireformat 编解码测试和来源记录。

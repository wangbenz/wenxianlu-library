# 问仙录 JSON 文件资料库

资料以 Git 中的 JSON 文件为准。FastAPI 启动时校验完整文件集，在内存中建立只读索引；本工程没有 Django 模型、维护后台、资料数据库或写入资料的 HTTP 接口。游戏中的人物状态、物品归属、D20、突破及 AI 输出裁决由游戏工程负责。

本分支是任务要求的**第一阶段 PR：格式和服务**。包含十类 Schema、加载器、交叉校验、公开/内部接口、受保护重载、显式旧文件迁移工具与闭合样例。格式经 Feno、Claude 审核后，第二个 PR 才起草第一批完整内容，不把示例当作正式平衡规则。

## 安装与校验

Python 3.11、uv >=0.10：

```bash
uv sync --locked
uv run python -m library_service.cli
uv run pytest -q
uv run ruff check library_service tests
uv run python scripts/smoke_http.py
```

校验脚本可供 CI 调用，失败退出码为 1，输出文件、JSON Pointer 与约束信息；不会回显隐藏正文。Schema 使用 Draft 2020-12，所有对象均关闭未知字段，Schema 自包含且不进行外部网络解析。项目版本、文件格式、内容指纹和 API 版本独立。

## 启动

为两个环境变量分别设置至少 32 字符、互不相同、无空白的 ASCII 随机令牌：

- `LIBRARY_SERVICE_TOKEN`：游戏服务读取完整资料。
- `LIBRARY_RELOAD_TOKEN`：运维重新加载。

无令牌或不安全配置拒绝启动；无内置生产密钥，令牌不放 URL、不写入文件或响应。

```bash
uv run uvicorn library_service.api:create_app --factory --host 127.0.0.1 --port 8012 --workers 1
```

`8012` 用于独立评审，不替换现有 `8011` 服务。可用 `LIBRARY_DATA_ROOT`、`LIBRARY_SCHEMA_ROOT` 指定目录；`LIBRARY_ENABLED_PHASE` 默认 1，不能超过 manifest 的 `published_phase`。修改完成并通过 Git 审阅后，重启或调用重载生效；不自动监视、导入或修改文件。

**重载模式只支持单个 worker。** 每个进程持有自己的内存快照，不能用一次 HTTP 请求同步多个 worker。多副本部署应通过 Git 发布版本与逐进程重启切换；本 PR 不提供跨进程热更新或部署流程。

## 文件结构

`data/manifest.json` 包含 `format_version: 1`、`published_phase` 和 `categories`。categories 用十个单数类型作为键，每项为相对 data 目录的完整文件清单，禁止目录外路径、符号链接、重复登记及未登记 JSON。

| type | 目录 | Schema |
| --- | --- | --- |
| world | world/ | schema/world.json |
| region | regions/ | schema/region.json |
| place | places/ | schema/place.json |
| realm | realms/ | schema/realm.json |
| root | roots/ | schema/root.json |
| item | items/分类/ | schema/item.json |
| art | arts/ | schema/art.json |
| faction | factions/ | schema/faction.json |
| character | characters/ | schema/character.json |
| event | events/ | schema/event.json |

每条资料的外层固定为 `type/key/name/phase/public/rules/narration/secret`。文件名与 key 相同；引用一律为 `类型:key`，由 Schema 检查类别、加载器检查存在性和阶段。name 使用中文，key 使用小写英文数字和连字符。新 key 是新身份；改名只改 name，不把文件改名作为同条资料的无声重命名。

- `public`：玩家展示字段。来源可在此标记为“通用修仙设定”或“问仙录原创设定”。
- `rules`：机器使用的枚举、数量与引用。`can/cannot` 为 Schema 中列出的能力标签；突破 requires 为条件标签、risk 为结果/严重度/可恢复标志。这里只校验定义，不执行对应游戏规则。
- `narration`：AI 写作提示。
- `secret`：按类别限定的隐藏信息。

任务中明确要求保留的 `effect.note`、`side_effects`、`duties` 是解释性注释，不通过关键词解析成判定规则。

枚举使用英文。品阶映射：灵石 `low/mid/high/supreme` 对应下/中/上/极品；丹药、灵草、材料、符箓的 `tier_1` 至 `tier_9` 对应一至九品；法器为 `low_artifact/mid_artifact/high_artifact/supreme_artifact/magic_treasure/spirit_treasure`；功法为 `huang/xuan/di/tian` 与 `low/mid/high` 的组合，如 `huang_low`。价格单位 `low_spirit_stone` 对应下品灵石，`silver_liang` 对应两；必须与 currency 配对。

## 启动与重载校验

加载全部阶段的资料，逐条验证格式、路径、引用和跨字段约束，再只索引已启用阶段。校验包括：

- 唯一世界、类别内唯一 key、地点树无环并最终归属大域。
- 境界 order 从 0 连续且唯一；full 境界能力列表非空，小阶段 key 唯一，允许/禁止能力不冲突。
- 六类灵根完整、概率精确合计 1.0，属性组合与种类相符。
- 品阶/分类、效果/分类、价格上下限及货币单位匹配。
- 所有引用存在、类别正确且不指向更晚阶段；人物小阶段属于其境界，功法最高境界不低于最低境界。
- 收徒规则中同一灵根不能匹配多条结果；事件触发阶段与资料阶段一致。

加载期间文件改变也会失败。重载构造完整候选快照，成功后一次替换；失败保留旧快照，继续服务。每个请求固定一个快照，不混用新旧条目。返回对象为独立副本，不能反向修改内存资料。

## 接口

| 方法与路径 | 权限 | 返回 |
| --- | --- | --- |
| GET /health | 无 | 就绪状态与版本标识 |
| GET /api/library/v1/public/entries | 无 | 公开资料列表 |
| GET /api/library/v1/public/{type}/{key} | 无 | 单条公开投影 |
| GET /api/library/v1/internal/entries | 服务令牌 | 完整资料列表 |
| GET /api/library/v1/internal/{type}/{key} | 服务令牌 | 单条完整资料 |
| POST /api/library/v1/internal/batch | 服务令牌 | 按 keys 批量只读查询 |
| POST /api/library/v1/ops/reload | 重载令牌 | 成功的新快照版本；失败为 409 并保留旧快照 |

受保护接口使用 `Authorization: Bearer ...`。两个令牌不能互换。内部批量请求为 `{"keys":["realm:lianqi","item:spirit-stone"]}`，最多 100 个；缺失或未启用资料使整批失败，避免静默丢条。

列表支持 kind、phase、重复 keys 参数、offset 和 limit（最大 100）；phase 不得请求未启用阶段。列表成功响应含 count/offset/limit/results 与版本标识。所有业务成功响应均含 revision、content_hash。

公开单条严格只返回 `type/key/name/phase/public/revision/content_hash`；不返回 rules、narration、secret。H5 应通过游戏后端查询已见条目，资料库本身不知道角色见过什么。公开 Git 仓库中的 JSON 源文件仍可被阅读：本 PR 的 secret 仅为虚构隔离样例，真实保密剧情应使用受限内容仓库/发布包，不能仅靠 API 投影隐藏公开源码。

## 版本、历史与旧接口兼容

每条资料使用规范化 JSON（UTF-8、排序键、固定分隔符、禁止 NaN）计算 SHA-256。content_hash 为 64 位十六进制字符串；revision 为 `sha256:<content_hash>`，是稳定内容标识，**不是递增整数**。相同内容在重启、格式化和重新加载后标识相同。文件里不手写这些字段。

资料库只提供最新内存快照，不保留数据库历史。Git 负责内容历史；游戏开卷时冻结所用完整条目及各自指纹。不能只存一个最新服务 URL 后再推断旧内容。

**这是不兼容的接口改造。** 原 Django 的 `/api/library/v1/items/`、历史修订 URL 与整数 revision 退出本分支；不会留下能绕过分层授权的旧完整资料入口。现有游戏客户端尚未适配，本 PR 不修改游戏、不切换运行地址，也不改已有命卷。恢复旧本地版本及独立数据库仍使用原分支和其配置，JSON 服务不读取或迁移旧数据库。

## 旧 JSON 资料迁移

旧 starter 和工程样例保留在 `tests/fixtures/legacy/`，仅作迁移输入与兼容测试，不作为运行时内容。先起草新条目并审核，再逐条填写 replace 或 retire 映射；不得从旧中文说明猜测硬字段。

```bash
uv run python -m library_service.migrate --legacy tests/fixtures/legacy/starter-v0.1.json --plan > /tmp/starter-plan.json
# 审核新资料并填写 /tmp/starter-plan.json 中每个 target 与 reason；退出条目需明确 action=retire。
uv run python -m library_service.migrate --legacy tests/fixtures/legacy/starter-v0.1.json --reviewed-data /path/to/reviewed-data --mapping /tmp/starter-plan.json --output /path/to/new-data
```

迁移要求来源指纹匹配、全部旧编号有明确处置、替代引用有效、新数据全量校验通过。仅复制 manifest 和登记文件到全新目录；不复制隐藏环境文件，不覆盖已有输出，不修改输入，不访问数据库。输出目录尚不自动激活；若文件复制期间发生错误，应检查并弃用该新输出，不能将它当作已迁移成功。完整旧资料的逐条改写与映射随内容 PR 审核，不把本 PR 的少量样例当作全量内容迁移完成。

## 本次示例与后续内容 PR

现有 12 条资料是世界、大域、凡人、炼气、地点、灵石六条示例，加六类必须完整的灵根概率配置；这是满足跨文件闭合校验的最小评审资料集，不是任务所列的完整第一批世界。来源已统一，描述为本项目起草。

用户已选择保留 11 个境界，缺少的境界名称尚待补充；当前只提供连续的凡人 0、炼气 1，不擅自补造其余境界。世界名、宗门名、价格、寿元、灵根概率均仍需内容审核。格式审核后才扩展五大域、完整十一境界及阶段一的物品、功法、势力、人物和事件。

启动生命周期和内存装载采用 [FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/)。原 Open5e 的 [许可证和版权声明](LICENSE.md) 保持原文，本地原仓库及上游历史保留；本分支不再包含其 Django 运行代码。

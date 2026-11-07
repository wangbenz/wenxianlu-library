# 问仙录文件资料服务

- 资料权威为 Git 中的 `data/` JSON；严禁将资料、版本或游戏状态另存数据库。
- 本分支依据用户 2026-11-07 的文件化任务。格式与服务先单独 PR，审核后再起草完整内容；暂定名称、价格和概率不能声称已经定稿。
- 十类资料使用 `schema/` Draft 2020-12、自包含且关闭未知字段。改字段需同步 Schema、交叉校验、测试和 README/数据字典。
- public、rules、narration、secret 必须隔离；公开接口只输出明确白名单。内部读取与重载分别使用进程注入的不同令牌，禁止日志或响应回显令牌和隐藏内容。
- 启动全量校验；重载失败保留旧快照。只支持单 worker 的进程内重载，多副本通过版本发布和重启协调。
- 本服务不执行 D20、突破、AI 输出裁决或角色/库存变化。不修改游戏工程，不替换正在运行的 8011 服务，不读取、迁移或删除旧本地数据库。
- Python 3.11，`uv sync --locked`；验证使用 `uv run python -m library_service.cli`、`uv run pytest -q`、`uv run ruff check library_service tests scripts`、`uv run python scripts/smoke_http.py`。
- 真实 HTTP 检查只运行临时资料和自建进程。不要因测试失败重置实际资料库。
- 原许可证与本地上游 Git 历史保留。对外代码只推自己的 `personal` remote，不向 Open5e 的 `origin` 推送；不包含真实令牌、数据库、环境文件、日志或内部历史材料。
- 不宣称未执行的镜像构建、远端 CI、部署或游戏联调通过。

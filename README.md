# 问仙录资料库 · wenxianlu-library

独立的 Python / Django / DRF 修仙资料服务，供游戏通过只读 API 读取公共定义。角色、库存、行动判定、D20 掷骰、AI 叙事与游戏存档由游戏工程负责。

## 本次公开快照

- 源码基线：`829cf8ab7f497f4d6847887cabf02a897cca4645`（2026-11-07 代码快照）。
- 游戏代码：[wangbenz/wenxian](https://github.com/wangbenz/wenxian/tree/codex/public-code-2026-10-29)。
- 本仓库只包含代码、迁移、测试、锁文件、初始化资料及必要运行说明，不包含本地数据库、账号、密钥、日志、内部设计和验收文档。
- 此公开快照使用独立提交，不携带本地开发提交历史；原始本地仓库、开发分支与完整上游历史继续保留。
- 上游为 [Open5e API](https://github.com/open5e/open5e-api)，克隆基线 `91971c14227bf095d036bb97570ee007d54e8c8b`。当前运行工程已裁剪为独立修仙资料服务，保留原 [LICENSE.md](LICENSE.md)。

## 安装和运行

使用 Python 3.11 和 uv >=0.10。初始化命令仅用于新数据库；已有数据库应先备份，不自动覆盖已有内容。

```bash
uv sync --locked
uv run python manage.py migrate --noinput
uv run python manage.py import_library data/cultivation/starter-v0.1.json
uv run python manage.py collectstatic --noinput
uv run python manage.py runserver 127.0.0.1:8011
```

默认数据库为 `var/wenxianlu-library.sqlite3`，可通过 `WENXIANLU_DB_PATH` 指定独立路径；静态目录可通过 `WENXIANLU_STATIC_ROOT` 指定。`.env` 不自动读取，参见 [.env.example](.env.example)。非调试模式须通过环境变量提供自己的 `SECRET_KEY`。

数据导入为原子操作：相同内容跳过，同编号不同内容默认拒绝，明确需要更新时才使用 `--update`。`data/cultivation/v0.1-sample.json` 是独立工程测试样例，其中示例数值不代表正式游戏平衡。

## 接口与维护

公共前缀 `/api/library/v1/`，包含 `realms/`、`items/`、`facility-types/`、`recipes/`、`arts/`、`place-types/`。详情通过稳定 `key` 读取；历史修订通过 `revisions/` 只读查询。

支持名称、别名、分类和分页等当前已实现的查询；定义返回版本和内容校验值，游戏应保存实际采用的版本。资料可查询不代表角色已经拥有或掌握。

- OpenAPI：`/schema/`
- Swagger：`/schema/swagger-ui/`
- ReDoc：`/schema/redoc/`
- Django 维护后台：`/admin/`，需要维护者自行创建账号，无预设账号密码。

公共 API 不提供写入；维护后台保留登录、staff、模型权限与 CSRF 保护。

## 验证

```bash
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
uv run pytest -ra
uv run python manage.py spectacular --validate --fail-on-warn --file /tmp/wenxianlu-library-schema.yml
```

本次在独立临时数据库配置下完成系统检查、无遗漏迁移检查、175 项测试和 OpenAPI 严格校验。未执行镜像构建、远程 CI、生产部署或真实模型调用。

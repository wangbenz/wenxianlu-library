import json
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from cultivation.importing import import_definitions


class Command(BaseCommand):
    help = "原子导入问仙录资料；同编号内容冲突默认报错，显式 --update 才更新。"

    def add_arguments(self, parser):
        parser.add_argument("file", type=Path, help="UTF-8 JSON 资料文件")
        parser.add_argument("--update", action="store_true", help="显式允许更新同编号的定义和材料清单")

    def handle(self, *args, **options):
        try:
            payload = json.loads(options["file"].read_text(encoding="utf-8"))
            stats = import_definitions(payload, update=options["update"])
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise CommandError(f"{options['file']}：{error}") from error
        except ValidationError as error:
            raise CommandError("\n".join(error.messages)) from error
        self.stdout.write(json.dumps(stats, ensure_ascii=False, indent=2))

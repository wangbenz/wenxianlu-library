import re
from urllib.parse import urlparse

from django.core.exceptions import ValidationError


def validate_chinese_text(value):
    if not isinstance(value, str) or not re.search(r"[\u3400-\u9fff]", value):
        raise ValidationError("请填写包含中文的文字。")


def validate_positive_integer(value):
    if type(value) is not int or value <= 0:
        raise ValidationError("必须是正整数，不能是布尔值、小数或表达式。")


def validate_effects(value):
    if not isinstance(value, list):
        raise ValidationError("效果必须是列表；无效果时使用 []。")
    for index, effect in enumerate(value):
        if not isinstance(effect, dict) or set(effect) != {"type", "amount"}:
            raise ValidationError(f"effects[{index}]：只允许 type 和 amount 两个字段。")
        if effect["type"] != "restore_hp":
            raise ValidationError(f"effects[{index}].type：只支持 restore_hp。")
        try:
            validate_positive_integer(effect["amount"])
        except ValidationError as error:
            raise ValidationError(f"effects[{index}].amount：{error.messages[0]}") from error


def validate_capabilities(value):
    if not isinstance(value, list) or not value:
        raise ValidationError("能力必须是非空列表。")
    if any(capability != "basic_alchemy" for capability in value):
        raise ValidationError("当前只支持 basic_alchemy 能力。")
    if len(value) != len(set(value)):
        raise ValidationError("能力不能重复。")


def validate_material_entries(entries):
    if not entries:
        raise ValidationError("配方必须至少包含一种材料。")
    seen = set()
    for index, entry in enumerate(entries):
        item = entry["item"]
        if item.key in seen:
            raise ValidationError(f"ingredients[{index}].item：材料 {item.key} 重复。")
        seen.add(item.key)
        if item.type != "material":
            raise ValidationError(f"ingredients[{index}].item：{item.key} 不是材料。")
        try:
            validate_positive_integer(entry["quantity"])
        except ValidationError as error:
            raise ValidationError(f"ingredients[{index}].quantity：{error.messages[0]}") from error


def validate_aliases(value):
    if not isinstance(value, list):
        raise ValidationError("别名必须是文字列表；没有别名时使用 []。")
    seen = set()
    for alias in value:
        if not isinstance(alias, str) or not alias.strip() or alias != alias.strip() or len(alias) > 120:
            raise ValidationError("每个别名须为去除首尾空白的非空文字，最长120字符。")
        if alias.casefold() in seen:
            raise ValidationError("别名不能重复。")
        seen.add(alias.casefold())


def validate_sources(value):
    if not isinstance(value, list):
        raise ValidationError("结构化来源必须是列表；尚未拆分旧来源时使用 []。")
    seen = set()
    for index, citation in enumerate(value):
        path = f"sources[{index}]"
        if not isinstance(citation, dict) or set(citation) != {"title", "locator", "url", "supports"}:
            raise ValidationError(f"{path}：只接受 title、locator、url、supports 四个字段。")
        for field in ["title", "locator", "supports"]:
            if not isinstance(citation[field], str) or not citation[field].strip():
                raise ValidationError(f"{path}.{field}：必须填写非空文字。")
        link = citation["url"]
        if not isinstance(link, str) or (link and (urlparse(link).scheme not in {"http", "https"} or not urlparse(link).netloc)):
            raise ValidationError(f"{path}.url：须为 http/https 地址；项目原创可用空字符串。")
        identity = (citation["title"], citation["locator"], link, citation["supports"])
        if identity in seen:
            raise ValidationError(f"{path}：来源引用不能重复。")
        seen.add(identity)

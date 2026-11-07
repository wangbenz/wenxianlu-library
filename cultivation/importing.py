"""原子导入公共资料定义；任何类别校验失败都回滚，不执行游戏状态变化。"""
from django.core.exceptions import ValidationError
from django.db import models, transaction

from .models import Art, FacilityType, Item, PlaceType, Realm, Recipe, RecipeIngredient
from .validation import validate_material_entries, validate_positive_integer


NARRATIVE_FIELDS = {"usage", "limitations", "adaptation"}
COMMON = {"key", "name", "description", "source", "revision", "entry_kind", "aliases", "sources"} | NARRATIVE_FIELDS
REQUIRED_COMMON = {"key", "name", "description", "source"}
REQUIRED_SECTIONS = {"realms", "facility_types", "items", "recipes"}
SPECS = {
    "realms": (Realm, {"order"}, set()),
    "facility_types": (FacilityType, {"capabilities"}, set()),
    "items": (Item, {"type"}, {"minimum_realm", "typical_realm", "effects"}),
    "recipes": (Recipe, {"minimum_realm", "facility_type", "output_item", "output_quantity", "ingredients"}, set()),
    "arts": (Art, {"kind"}, {"minimum_realm", "typical_realm"}),
    "place_types": (PlaceType, set(), set()),
}


def fail(path, message):
    raise ValidationError(f"{path}：{message}")


def check_fields(record, allowed, required, path):
    if not isinstance(record, dict):
        fail(path, "必须是对象。")
    if set(record) - allowed:
        fail(path, f"未知字段 {', '.join(sorted(set(record) - allowed))}。")
    if required - set(record):
        fail(path, f"缺少字段 {', '.join(sorted(required - set(record)))}。")


def resolve(model, key, path):
    if not isinstance(key, str):
        fail(path, "必须引用资料定义的英文编号。")
    try:
        return model.objects.get(key=key)
    except model.DoesNotExist:
        fail(path, f"引用不存在：{key}。")


def clean(instance, path):
    try:
        instance.full_clean()
    except ValidationError as error:
        if hasattr(error, "message_dict"):
            messages = [f"{path}.{field}：{'；'.join(values)}" for field, values in error.message_dict.items()]
        else:
            messages = [f"{path}：{'；'.join(error.messages)}"]
        raise ValidationError(messages) from error


def comparable(value):
    return value.key if isinstance(value, models.Model) else value


def upsert(model, values, path, update, extra_changed=(), ingredients=None):
    instance = model.objects.filter(key=values["key"]).first()
    changed = list(extra_changed)
    if instance is not None:
        # 文件 revision 是旧格式的初始标记；已有内容版本由程序维护。
        values.pop("revision", None)
        changed += [field for field, value in values.items() if comparable(getattr(instance, field)) != comparable(value)]
    else:
        instance = model()
    exists = instance.pk is not None
    for field, value in values.items():
        setattr(instance, field, value)
    clean(instance, path)
    if exists and changed and not update:
        fail(path, f"同编号内容冲突，差异字段：{', '.join(changed)}；仅显式 --update 可更新。")
    action = "updated" if exists and changed else "skipped" if exists else "created"
    if action != "skipped":
        if ingredients is not None:
            instance._pending_ingredients = ingredients
        instance.save()
    return instance, action


def import_definitions(payload, *, update=False):
    # 原有四数组仍必填；新增类别可缺省，保持 format_version=1 的旧文件可用。
    check_fields(payload, set(SPECS) | {"format_version"}, REQUIRED_SECTIONS | {"format_version"}, "file")
    if type(payload["format_version"]) is not int or payload["format_version"] != 1:
        fail("file.format_version", "当前只支持整数 1。")
    normalized = {}
    for label, (_, required, optional) in SPECS.items():
        records = payload.get(label, [])
        if not isinstance(records, list):
            fail(label, "必须是列表。")
        seen = set()
        normalized[label] = []
        for index, record in enumerate(records):
            path = f"{label}[{index}]"
            check_fields(record, COMMON | required | optional, REQUIRED_COMMON | required, path)
            if not isinstance(record["key"], str):
                fail(path + ".key", "编号必须是字符串。")
            path += f"({record['key']})"
            if record["key"] in seen:
                fail(path + ".key", "同一文件内编号重复。")
            seen.add(record["key"])
            values = {**record, "revision": record.get("revision", 1)}
            for field in NARRATIVE_FIELDS:
                if field in values and not isinstance(values[field], str):
                    fail(path + "." + field, "必须是文字；旧资料可留空。")
            for field in ["revision", "order", "output_quantity"]:
                if field in values:
                    try:
                        validate_positive_integer(values[field])
                    except ValidationError as error:
                        fail(path + "." + field, error.messages[0])
            normalized[label].append((path, values))

    stats = {label: {"created": 0, "updated": 0, "skipped": 0} for label in SPECS}
    with transaction.atomic():
        for label, (model, _, _) in SPECS.items():
            for path, record in normalized[label]:
                values = record.copy()
                entries = None
                changed = []
                if model in {Item, Art} and "typical_realm" in values:
                    background = values.pop("typical_realm")
                    if "minimum_realm" in values and values["minimum_realm"] != background:
                        fail(path + ".typical_realm", "与兼容字段 minimum_realm 的背景引用不一致。")
                    values["minimum_realm"] = background
                if model is Item:
                    key = values.get("minimum_realm")
                    values["minimum_realm"] = None if key is None else resolve(Realm, key, path + ".minimum_realm")
                    values.setdefault("effects", [])
                if model is Art and "minimum_realm" in values:
                    key = values["minimum_realm"]
                    values["minimum_realm"] = None if key is None else resolve(Realm, key, path + ".minimum_realm")
                if model is Recipe:
                    values["minimum_realm"] = resolve(Realm, values["minimum_realm"], path + ".minimum_realm")
                    values["facility_type"] = resolve(FacilityType, values["facility_type"], path + ".facility_type")
                    values["output_item"] = resolve(Item, values["output_item"], path + ".output_item")
                    raw_entries = values.pop("ingredients")
                    if not isinstance(raw_entries, list):
                        fail(path + ".ingredients", "必须是列表。")
                    entries = []
                    for index, entry in enumerate(raw_entries):
                        entry_path = f"{path}.ingredients[{index}]"
                        check_fields(entry, {"item", "quantity"}, {"item", "quantity"}, entry_path)
                        entries.append({"item": resolve(Item, entry["item"], entry_path + ".item"), "quantity": entry["quantity"]})
                    try:
                        validate_material_entries(entries)
                    except ValidationError as error:
                        fail(path, "；".join(error.messages))
                    existing = Recipe.objects.filter(key=values["key"]).first()
                    if existing:
                        old = sorted(existing.ingredients.values_list("item__key", "quantity"))
                        new = sorted((entry["item"].key, entry["quantity"]) for entry in entries)
                        if old != new:
                            changed.append("ingredients")
                proposed_materials = [(entry["item"].key, entry["quantity"]) for entry in entries] if entries is not None else None
                instance, action = upsert(model, values, path, update, changed, proposed_materials)
                if entries is not None and action != "skipped":
                    from .revisioning import defer_recipe_children
                    with defer_recipe_children(instance.pk):
                        instance.ingredients.exclude(item__in=[entry["item"] for entry in entries]).delete()
                        for entry in entries:
                            ingredient = instance.ingredients.filter(item=entry["item"]).first()
                            if ingredient is None:
                                ingredient = RecipeIngredient(recipe=instance, **entry)
                            else:
                                ingredient.quantity = entry["quantity"]
                            clean(ingredient, path + ".ingredients(" + entry["item"].key + ")")
                            ingredient.save()
                    del instance._pending_ingredients
                stats[label][action] += 1
    return stats

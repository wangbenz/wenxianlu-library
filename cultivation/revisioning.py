"""保存公共定义的内容版本；事务失败回滚版本，不保存任何玩家状态。"""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json

from django.core.exceptions import ValidationError
from django.db import models, transaction


CATEGORIES = {"Realm": "realms", "Item": "items", "Art": "arts", "PlaceType": "place-types",
              "FacilityType": "facility-types", "Recipe": "recipes"}
_DEFERRED_RECIPES = ContextVar("deferred_library_recipes", default=frozenset())


def content_of(instance, ingredients=None):
    """生成稳定内容；关联仅存编号，相关资料的版本须由读取方另行保存。"""
    result = {"category": CATEGORIES[type(instance).__name__]}
    for field in instance._meta.concrete_fields:
        if field.name in {"id", "revision", "content_hash"}:
            continue
        value = getattr(instance, field.name)
        name = "typical_realm" if field.name == "minimum_realm" and type(instance).__name__ in {"Art", "Item"} else field.name
        result[name] = value.key if field.is_relation and value is not None else value
    if type(instance).__name__ == "Recipe":
        if ingredients is None:
            from .models import RecipeIngredient
            ingredients = list(RecipeIngredient.objects.filter(recipe_id=instance.pk)
                               .values_list("item__key", "quantity")) if instance.pk else []
        result["ingredients"] = [{"item": key, "quantity": quantity} for key, quantity in sorted(ingredients)]
    return result


def hash_of(content):
    return hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def remember(instance, content, digest):
    from .models import DefinitionRevision
    row, created = DefinitionRevision.objects.get_or_create(
        category=content["category"], definition_key=instance.key, revision=instance.revision,
        defaults={"content_hash": digest, "content": content})
    if not created and (row.content_hash != digest or row.content != content):
        raise ValidationError("同一资料版本已有不同内容，不能覆盖历史快照。")


def save_definition(instance, *args, **kwargs):
    """锁定旧版本后保存；已加载的旧副本不能覆盖其他维护者的新版本。"""
    from .models import DefinitionRevision
    category = CATEGORIES[type(instance).__name__]
    with transaction.atomic():
        old = type(instance).objects.select_for_update().filter(pk=instance.pk).first() if instance.pk else None
        if old:
            expected = getattr(instance, "_expected_revision", instance.revision)
            if expected != old.revision:
                raise ValidationError("资料已经被更新，请重新读取后再保存。")
            # 管理后台延迟配方提交时，旧快照已在改父记录前保存。
            previous = DefinitionRevision.objects.filter(category=category, definition_key=old.key,
                                                         revision=old.revision).first()
            old_content = previous.content if previous else content_of(old)
            old_hash = previous.content_hash if previous else hash_of(old_content)
            if not previous and not (type(old).__name__ == "Recipe" and not old_content["ingredients"]):
                remember(old, old_content, old_hash)
            fields = kwargs.get("update_fields")
            if fields is not None:
                for field in instance._meta.concrete_fields:
                    if field.name not in set(fields) | {"id", "revision", "content_hash"}:
                        setattr(instance, field.name, getattr(old, field.name))
        else:
            if DefinitionRevision.objects.filter(category=category, definition_key=instance.key).exists():
                raise ValidationError("该编号已有历史记录，不能删除后复用为新资料。")
            old_hash = None
        content = content_of(instance, getattr(instance, "_pending_ingredients", None))
        digest = hash_of(content)
        deferred = getattr(instance, "_defer_revision", False) or (
            type(instance).__name__ == "Recipe" and not content["ingredients"])
        if deferred:
            instance.revision = old.revision if old else instance.revision
            instance.content_hash = old.content_hash if old else ""
        else:
            instance.revision = old.revision + (digest != old_hash) if old else instance.revision
            instance.content_hash = digest
        instance.full_clean()
        if kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = set(kwargs["update_fields"]) | {"revision", "content_hash"}
        models.Model.save(instance, *args, **kwargs)
        if not deferred:
            remember(instance, content, digest)
        if hasattr(instance, "_expected_revision"):
            del instance._expected_revision


@contextmanager
def defer_recipe_children(recipe_id):
    token = _DEFERRED_RECIPES.set(_DEFERRED_RECIPES.get() | {recipe_id})
    try:
        yield
    finally:
        _DEFERRED_RECIPES.reset(token)


def record_recipe(recipe_id):
    if recipe_id in _DEFERRED_RECIPES.get():
        return
    from .models import Recipe
    recipe = Recipe.objects.filter(pk=recipe_id).first()
    if recipe:
        from .validation import validate_material_entries
        validate_material_entries([{"item": row.item, "quantity": row.quantity}
                                   for row in recipe.ingredients.select_related("item")])
        recipe.save()

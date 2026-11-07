from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models

from .fields import StrictPositiveIntegerField
from .validation import validate_aliases, validate_sources, validate_capabilities, validate_chinese_text, validate_effects


class ValidatedModel(models.Model):
    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class Definition(ValidatedModel):
    class EntryKind(models.TextChoices):
        OVERVIEW = "overview", "类别介绍"
        DEFINITION = "definition", "具体定义"

    key = models.SlugField(
        "稳定编号", max_length=80, unique=True,
        validators=[RegexValidator(r"\A[a-z][a-z0-9]*(?:-[a-z0-9]+)*\Z", "编号须为小写英文、数字和单个连字符，以英文字母开头。")],
        help_text="创建后不可修改；用于查询和跨资料关联。",
    )
    name = models.CharField("中文名称", max_length=120, validators=[validate_chinese_text])
    description = models.TextField("中文描述", validators=[validate_chinese_text])
    entry_kind = models.CharField("条目层级", max_length=20, choices=EntryKind.choices, default=EntryKind.OVERVIEW)
    aliases = models.JSONField("别名", default=list, blank=True)
    sources = models.JSONField("结构化来源", default=list, blank=True)
    usage = models.TextField("用途与用法", blank=True, default="", validators=[validate_chinese_text])
    limitations = models.TextField("限制与风险", blank=True, default="", validators=[validate_chinese_text])
    adaptation = models.TextField("改编说明", blank=True, default="", validators=[validate_chinese_text])
    source = models.TextField("来源说明", help_text="样例使用“问仙录测试设定”；不伪造作者或授权。")
    revision = StrictPositiveIntegerField("内容版本", default=1, help_text="内容变化自动递增；同版本快照不可覆盖。")
    content_hash = models.CharField("内容校验值", max_length=64, default="", blank=True, editable=False)

    class Meta:
        abstract = True
        ordering = ["key"]

    def clean(self):
        super().clean()
        validate_aliases(self.aliases)
        validate_sources(self.sources)
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValidationError({"source": "来源说明不能为空。"})
        if self.pk:
            old_key = type(self).objects.using(self._state.db or "default").filter(pk=self.pk).values_list("key", flat=True).first()
            if old_key is not None and old_key != self.key:
                raise ValidationError({"key": "创建后不能修改稳定编号。"})

    def save(self, *args, **kwargs):
        from .revisioning import save_definition
        save_definition(self, *args, **kwargs)

    def __str__(self):
        return f"{self.name}（{self.key}）"


class Realm(Definition):
    order = StrictPositiveIntegerField("顺序")

    class Meta(Definition.Meta):
        verbose_name = "境界"
        verbose_name_plural = "境界"
        ordering = ["order", "key"]
        constraints = [models.CheckConstraint(condition=models.Q(order__gt=0), name="cultivation_realm_positive_order")]


class Item(Definition):
    class Type(models.TextChoices):
        MATERIAL = "material", "材料"
        CONSUMABLE = "consumable", "消耗品"
        EQUIPMENT = "equipment", "器物/工具"

    type = models.CharField("类型", max_length=20, choices=Type.choices)
    minimum_realm = models.ForeignKey(Realm, verbose_name="适用境界背景", on_delete=models.PROTECT, null=True, blank=True, related_name="items",
                                     help_text="旧字段名保留兼容；语义同 typical_realm，不是使用许可或硬门槛。")
    effects = models.JSONField("结构化效果", default=list, blank=True,
                              help_text='例如 [{"type": "restore_hp", "amount": 20}]；[]表示未声明量化效果，不否定用途文字中的叙事作用。')

    class Meta(Definition.Meta):
        verbose_name = "物品"
        verbose_name_plural = "物品"

    def clean(self):
        super().clean()
        try:
            validate_effects(self.effects)
        except ValidationError as error:
            raise ValidationError({"effects": error.messages}) from error
        if self.type == self.Type.MATERIAL and self.effects:
            raise ValidationError({"effects": "材料不能包含消耗品使用效果。"})
        if self.type == self.Type.EQUIPMENT and self.effects:
            raise ValidationError({"effects": "器物不能包含消耗品使用效果。"})
        if self.pk and self.type != self.Type.MATERIAL and self.ingredient_entries.exists():
            if self.type == self.Type.EQUIPMENT:
                raise ValidationError({"type": "该物品已作为配方材料使用，不能改为器物。"})
            raise ValidationError({"type": "该物品已作为配方材料使用，不能改为消耗品。"})


class Art(Definition):
    """功法或术法定义；适用条件不表示角色已经学会或能够施展。"""

    class Kind(models.TextChoices):
        PRACTICE = "practice", "功法"
        SPELL = "spell", "术法"

    kind = models.CharField("类别", max_length=20, choices=Kind.choices)
    minimum_realm = models.ForeignKey(
        Realm, verbose_name="适用境界背景", on_delete=models.PROTECT,
        null=True, blank=True, related_name="arts",
        help_text="旧字段名保留兼容；语义同 typical_realm，不判断角色是否学会或施展成功。",
    )

    class Meta(Definition.Meta):
        verbose_name = "功法与术法"
        verbose_name_plural = "功法与术法"


class PlaceType(Definition):
    """可复用的场所类型；不保存本局地点、人物或库存。"""

    class Meta(Definition.Meta):
        verbose_name = "场所类型"
        verbose_name_plural = "场所类型"


class FacilityType(Definition):
    capabilities = models.JSONField("支持的能力", default=list, help_text='当前支持 ["basic_alchemy"]。')

    class Meta(Definition.Meta):
        verbose_name = "设施类型"
        verbose_name_plural = "设施类型"

    def clean(self):
        super().clean()
        try:
            validate_capabilities(self.capabilities)
        except ValidationError as error:
            raise ValidationError({"capabilities": error.messages}) from error


class Recipe(Definition):
    minimum_realm = models.ForeignKey(Realm, verbose_name="最低境界", on_delete=models.PROTECT, related_name="recipes")
    facility_type = models.ForeignKey(FacilityType, verbose_name="所需设施类型", on_delete=models.PROTECT, related_name="recipes")
    output_item = models.ForeignKey(Item, verbose_name="成功时产物", on_delete=models.PROTECT, related_name="producing_recipes")
    output_quantity = StrictPositiveIntegerField("成功时产物数量")

    class Meta(Definition.Meta):
        verbose_name = "配方"
        verbose_name_plural = "配方"
        constraints = [models.CheckConstraint(condition=models.Q(output_quantity__gt=0), name="cultivation_recipe_positive_output")]


class RecipeIngredient(ValidatedModel):
    recipe = models.ForeignKey(Recipe, verbose_name="配方", on_delete=models.CASCADE, related_name="ingredients")
    item = models.ForeignKey(Item, verbose_name="材料", on_delete=models.PROTECT, related_name="ingredient_entries", limit_choices_to={"type": "material"})
    quantity = StrictPositiveIntegerField("材料数量")

    class Meta:
        verbose_name = "配方材料"
        verbose_name_plural = "配方材料"
        ordering = ["item__key"]
        constraints = [
            models.UniqueConstraint(fields=["recipe", "item"], name="cultivation_recipe_unique_material"),
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="cultivation_ingredient_positive_quantity"),
        ]

    def clean(self):
        super().clean()
        if self.item_id and Item.objects.filter(pk=self.item_id).exclude(type=Item.Type.MATERIAL).exists():
            raise ValidationError({"item": "配方材料必须引用材料类型物品。"})

    def __str__(self):
        return f"{self.item} × {self.quantity}"

    def save(self, *args, **kwargs):
        from django.db import transaction
        from .revisioning import record_recipe
        with transaction.atomic():
            old_recipe_id = type(self).objects.filter(pk=self.pk).values_list("recipe_id", flat=True).first() if self.pk else None
            super().save(*args, **kwargs)
            if old_recipe_id and old_recipe_id != self.recipe_id:
                record_recipe(old_recipe_id)
            record_recipe(self.recipe_id)

    def delete(self, *args, **kwargs):
        from django.db import transaction
        from .revisioning import record_recipe
        recipe_id = self.recipe_id
        with transaction.atomic():
            result = super().delete(*args, **kwargs)
            record_recipe(recipe_id)
            return result


class DefinitionRevision(models.Model):
    """公共定义的不可覆盖快照；不保存角色、库存或游戏回合。"""
    category = models.CharField("资料类别", max_length=30)
    definition_key = models.CharField("稳定编号", max_length=80)
    revision = StrictPositiveIntegerField("内容版本")
    content_hash = models.CharField("内容校验值", max_length=64)
    content = models.JSONField("内容快照")
    created_at = models.DateTimeField("记录时间", auto_now_add=True)

    class Meta:
        verbose_name = "资料历史版本"
        verbose_name_plural = "资料历史版本"
        ordering = ["category", "definition_key", "revision"]
        constraints = [models.UniqueConstraint(fields=["category", "definition_key", "revision"], name="library_revision_identity")]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("历史版本不能覆盖。")
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("历史版本不能删除。")

    @property
    def reference(self):
        return f"{self.category}:{self.definition_key}:{self.revision}"

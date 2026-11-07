from rest_framework import serializers

from .models import Art, DefinitionRevision, FacilityType, Item, PlaceType, Realm, Recipe, RecipeIngredient


class CultivationRealmReferenceSerializer(serializers.ModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name="cultivation:realm-detail", lookup_field="key")

    class Meta:
        model = Realm
        fields = ("key", "name", "order", "revision", "content_hash", "url")


class CultivationFacilityReferenceSerializer(serializers.ModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name="cultivation:facility-type-detail", lookup_field="key")
    capabilities = serializers.ListField(child=serializers.ChoiceField(choices=["basic_alchemy"]), read_only=True)

    class Meta:
        model = FacilityType
        fields = ("key", "name", "capabilities", "revision", "content_hash", "url")


class CultivationItemReferenceSerializer(serializers.ModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name="cultivation:item-detail", lookup_field="key")

    class Meta:
        model = Item
        fields = ("key", "name", "type", "revision", "content_hash", "url")


class CultivationRecipeReferenceSerializer(serializers.ModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name="cultivation:recipe-detail", lookup_field="key")

    class Meta:
        model = Recipe
        fields = ("key", "name", "revision", "content_hash", "url")


class CultivationRestoreHPEffectSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=["restore_hp"])
    amount = serializers.IntegerField(min_value=1)


class CultivationIngredientSerializer(serializers.ModelSerializer):
    item = CultivationItemReferenceSerializer(read_only=True)

    class Meta:
        model = RecipeIngredient
        fields = ("item", "quantity")


class CultivationIngredientUsageSerializer(serializers.ModelSerializer):
    recipe = CultivationRecipeReferenceSerializer(read_only=True)

    class Meta:
        model = RecipeIngredient
        fields = ("recipe", "quantity")


class SourceCitationSerializer(serializers.Serializer):
    title = serializers.CharField()
    locator = serializers.CharField()
    url = serializers.CharField(allow_blank=True)
    supports = serializers.CharField()


class DefinitionSerializer(serializers.ModelSerializer):
    aliases = serializers.ListField(child=serializers.CharField(), read_only=True)
    sources = SourceCitationSerializer(many=True, read_only=True)


COMMON_FIELDS = ("key", "name", "entry_kind", "aliases", "description", "usage", "limitations", "adaptation", "source", "sources", "revision", "content_hash", "url")


class CultivationRealmSerializer(CultivationRealmReferenceSerializer, DefinitionSerializer):
    class Meta:
        model = Realm
        fields = COMMON_FIELDS + ("order",)


class CultivationFacilityTypeSerializer(CultivationFacilityReferenceSerializer, DefinitionSerializer):
    class Meta:
        model = FacilityType
        fields = COMMON_FIELDS + ("capabilities",)


class CultivationArtSerializer(DefinitionSerializer):
    url = serializers.HyperlinkedIdentityField(view_name="cultivation:art-detail", lookup_field="key")
    minimum_realm = CultivationRealmReferenceSerializer(read_only=True, allow_null=True)
    typical_realm = CultivationRealmReferenceSerializer(source="minimum_realm", read_only=True, allow_null=True,
                                                       help_text="资料适用背景，不是角色学习或使用许可。")

    class Meta:
        model = Art
        fields = COMMON_FIELDS + ("kind", "typical_realm", "minimum_realm")


class CultivationPlaceTypeSerializer(DefinitionSerializer):
    url = serializers.HyperlinkedIdentityField(view_name="cultivation:place-type-detail", lookup_field="key")

    class Meta:
        model = PlaceType
        fields = COMMON_FIELDS


class CultivationItemSerializer(CultivationItemReferenceSerializer, DefinitionSerializer):
    minimum_realm = CultivationRealmReferenceSerializer(read_only=True, allow_null=True)
    typical_realm = CultivationRealmReferenceSerializer(source="minimum_realm", read_only=True, allow_null=True,
                                                       help_text="资料适用背景，不是角色持有或使用许可。")
    effects = CultivationRestoreHPEffectSerializer(many=True, read_only=True,
                                                  help_text="已声明的量化效果；[]不否定用途说明中的叙事作用。")
    recipes = CultivationRecipeReferenceSerializer(source="producing_recipes", many=True, read_only=True)
    ingredient_for = CultivationIngredientUsageSerializer(source="ingredient_entries", many=True, read_only=True)

    class Meta:
        model = Item
        fields = COMMON_FIELDS + ("type", "typical_realm", "minimum_realm", "effects", "recipes", "ingredient_for")


class CultivationRecipeSerializer(CultivationRecipeReferenceSerializer, DefinitionSerializer):
    minimum_realm = CultivationRealmReferenceSerializer(read_only=True)
    facility_type = CultivationFacilityReferenceSerializer(read_only=True)
    output_item = CultivationItemReferenceSerializer(read_only=True)
    ingredients = CultivationIngredientSerializer(many=True, read_only=True)

    class Meta:
        model = Recipe
        fields = COMMON_FIELDS + ("minimum_realm", "facility_type", "ingredients", "output_item", "output_quantity")


class DefinitionRevisionSerializer(serializers.ModelSerializer):
    content = serializers.JSONField(read_only=True)
    reference = serializers.CharField(read_only=True)
    url = serializers.HyperlinkedIdentityField(view_name="cultivation:revision-detail", lookup_field="reference")

    class Meta:
        model = DefinitionRevision
        fields = ("reference", "category", "definition_key", "revision", "content_hash", "content", "created_at", "url")

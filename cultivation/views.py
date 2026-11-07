from django_filters import rest_framework as filters
from django.db import connection
from django.db.models import BooleanField, Q
from django.db.models.expressions import RawSQL
from rest_framework.exceptions import NotFound
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.permissions import AllowAny
from rest_framework.viewsets import ReadOnlyModelViewSet

from .models import Art, DefinitionRevision, FacilityType, Item, PlaceType, Realm, Recipe
from .serializers import (
    CultivationArtSerializer, CultivationFacilityTypeSerializer, CultivationItemSerializer,
    CultivationPlaceTypeSerializer, DefinitionRevisionSerializer,
    CultivationRealmSerializer, CultivationRecipeSerializer,
)


class NameFilter(filters.FilterSet):
    name = filters.CharFilter(method="exact_name", label="名称或已登记别名精确匹配")
    name_contains = filters.CharFilter(method="contains_name", label="名称或已登记别名包含查询")
    entry_kind = filters.ChoiceFilter(choices=[("overview", "类别介绍"), ("definition", "具体定义")])

    def match_name(self, queryset, value, contains=False):
        # 当前资料库使用SQLite；JSON逐项比较避免别名被序列化转义后漏查。
        table = connection.ops.quote_name(queryset.model._meta.db_table)
        condition = "instr(lower(CAST(value AS TEXT)), lower(%s)) > 0" if contains else "lower(CAST(value AS TEXT)) = lower(%s)"
        alias = RawSQL(f"EXISTS (SELECT 1 FROM json_each({table}.aliases) WHERE {condition})",
                       (value,), output_field=BooleanField())
        lookup = "name__contains" if contains else "name"
        return queryset.annotate(_alias_match=alias).filter(Q(**{lookup: value}) | Q(_alias_match=True))

    def exact_name(self, queryset, name, value):
        return self.match_name(queryset, value)

    def contains_name(self, queryset, name, value):
        return self.match_name(queryset, value, contains=True)


class RealmFilter(NameFilter):
    class Meta:
        model = Realm
        fields = []


class FacilityTypeFilter(NameFilter):
    class Meta:
        model = FacilityType
        fields = []


class RecipeFilter(NameFilter):
    class Meta:
        model = Recipe
        fields = []


class ItemFilter(NameFilter):
    type = filters.ChoiceFilter(choices=Item.Type.choices, label="物品类型")

    class Meta:
        model = Item
        fields = []


class ArtFilter(NameFilter):
    kind = filters.ChoiceFilter(choices=Art.Kind.choices, label="功法或术法类别")

    class Meta:
        model = Art
        fields = []


class PlaceTypeFilter(NameFilter):
    class Meta:
        model = PlaceType
        fields = []


class LibraryPagination(LimitOffsetPagination):
    default_limit = 20
    max_limit = 100


@extend_schema_view(
    list=extend_schema(tags=["问仙录资料库"], description="只读资料列表。name 精确匹配、name_contains 包含查询，可组合；使用 limit/offset 分页。物品还支持 type=material、consumable 或 equipment；功法与术法支持 kind=practice 或 spell。不提供语义搜索。"),
    retrieve=extend_schema(tags=["问仙录资料库"], description="按创建后不可修改的英文 key 查询资料定义；不执行游戏效果。"),
)
class LibraryViewSet(ReadOnlyModelViewSet):
    # 资料库显式使用 /api/library/v1/，不根据项目或内容版本推断 API 版本。
    versioning_class = None
    lookup_field = "key"
    lookup_value_regex = "[a-z][a-z0-9-]*"
    permission_classes = (AllowAny,)
    http_method_names = ("get", "head", "options")
    filter_backends = (filters.DjangoFilterBackend,)
    pagination_class = LibraryPagination


class RealmViewSet(LibraryViewSet):
    queryset = Realm.objects.all()
    serializer_class = CultivationRealmSerializer
    filterset_class = RealmFilter


class FacilityTypeViewSet(LibraryViewSet):
    queryset = FacilityType.objects.all()
    serializer_class = CultivationFacilityTypeSerializer
    filterset_class = FacilityTypeFilter


class ItemViewSet(LibraryViewSet):
    queryset = Item.objects.select_related("minimum_realm").prefetch_related("producing_recipes", "ingredient_entries__recipe")
    serializer_class = CultivationItemSerializer
    filterset_class = ItemFilter


class ArtViewSet(LibraryViewSet):
    queryset = Art.objects.select_related("minimum_realm")
    serializer_class = CultivationArtSerializer
    filterset_class = ArtFilter


class PlaceTypeViewSet(LibraryViewSet):
    queryset = PlaceType.objects.all()
    serializer_class = CultivationPlaceTypeSerializer
    filterset_class = PlaceTypeFilter


class RecipeViewSet(LibraryViewSet):
    # 配方父记录构造中尚无材料时不作为完整公共定义返回。
    queryset = Recipe.objects.exclude(content_hash="").select_related("minimum_realm", "facility_type", "output_item").prefetch_related("ingredients__item")
    serializer_class = CultivationRecipeSerializer
    filterset_class = RecipeFilter


class DefinitionRevisionFilter(filters.FilterSet):
    category = filters.CharFilter(lookup_expr="exact")
    definition_key = filters.CharFilter(lookup_expr="exact")
    revision = filters.NumberFilter(lookup_expr="exact")

    class Meta:
        model = DefinitionRevision
        fields = []


class DefinitionRevisionViewSet(LibraryViewSet):
    """读取既有公共内容快照；读取不补写版本，不返回游戏数据。"""
    queryset = DefinitionRevision.objects.all()
    serializer_class = DefinitionRevisionSerializer
    filterset_class = DefinitionRevisionFilter
    lookup_field = "reference"
    lookup_value_regex = r"[a-z-]+:[a-z0-9-]+:[1-9][0-9]*"

    def get_object(self):
        import re
        reference = self.kwargs["reference"]
        match = re.fullmatch(r"([a-z-]+):([a-z][a-z0-9]*(?:-[a-z0-9]+)*):([1-9][0-9]*)", reference)
        if not match:
            raise NotFound()
        category, key, revision = match.groups()
        try:
            row = DefinitionRevision.objects.get(category=category, definition_key=key, revision=int(revision))
        except DefinitionRevision.DoesNotExist:
            raise NotFound()
        self.check_object_permissions(self.request, row)
        return row

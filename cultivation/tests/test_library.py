import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, connection, transaction
from django.db.models.deletion import ProtectedError
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from drf_spectacular.generators import SchemaGenerator

from cultivation.importing import import_definitions
from cultivation.models import FacilityType, Item, Realm, Recipe, RecipeIngredient


pytestmark = pytest.mark.django_db
ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "data/cultivation/v0.1-sample.json"
MODELS = (Realm, Item, FacilityType, Recipe, RecipeIngredient)


@pytest.fixture
def payload():
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


@pytest.fixture
def library(payload):
    return import_definitions(payload)


def counts():
    return [model.objects.count() for model in MODELS]


def test_import_and_repeat_are_idempotent(payload):
    first = import_definitions(payload)
    assert counts() == [2, 3, 1, 1, 2]
    assert sum(row["created"] for row in first.values()) == 7
    second = import_definitions(payload)
    assert counts() == [2, 3, 1, 1, 2]
    assert sum(row["skipped"] for row in second.values()) == 7
    recipe = Recipe.objects.get(key="huichun-dan-recipe")
    assert recipe.minimum_realm.key == "lianqi-chuqi"
    assert recipe.facility_type.capabilities == ["basic_alchemy"]
    assert recipe.output_item.key == "huichun-dan" and recipe.output_quantity == 2
    assert sorted(recipe.ingredients.values_list("item__key", "quantity")) == [("qingling-cao", 2), ("qinglu-hua", 1)]


def test_command_reads_utf8_and_reports_errors(payload, tmp_path, capsys):
    call_command("import_library", SAMPLE)
    assert '"created": 3' in capsys.readouterr().out
    payload["recipes"][0]["output_quantity"] = 0
    file = tmp_path / "invalid.json"
    file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(CommandError, match="recipes.*output_quantity"):
        call_command("import_library", file)


def test_conflict_and_explicit_update(payload, library):
    item = Item.objects.get(key="huichun-dan")
    payload["items"][0]["effects"][0]["amount"] = 25
    payload["items"][0]["revision"] = 2
    payload["recipes"][0]["ingredients"][0]["quantity"] = 3
    with pytest.raises(ValidationError, match="同编号内容冲突"):
        import_definitions(payload)
    item.refresh_from_db()
    assert item.effects[0]["amount"] == 20
    stats = import_definitions(payload, update=True)
    item.refresh_from_db()
    assert item.effects[0]["amount"] == 25 and item.revision == 2
    assert stats["items"]["updated"] == 1 and stats["recipes"]["updated"] == 1
    assert RecipeIngredient.objects.get(item__key="qingling-cao").quantity == 3
    assert sum(row["skipped"] for row in import_definitions(payload).values()) == 7


def test_partial_file_preserves_definitions(library, payload):
    payload["realms"] = payload["realms"][:1]
    for label in ["items", "facility_types", "recipes"]:
        payload[label] = []
    import_definitions(payload, update=True)
    assert counts() == [2, 3, 1, 1, 2]


@pytest.mark.parametrize("label", ["realms", "items", "facility_types", "recipes"])
def test_duplicate_identifiers_rejected(payload, label):
    payload[label].append(copy.deepcopy(payload[label][0]))
    with pytest.raises(ValidationError, match=label + ".*key.*重复"):
        import_definitions(payload)
    assert counts() == [0, 0, 0, 0, 0]


@pytest.mark.parametrize("label,field", [
    ("items", "minimum_realm"), ("recipes", "minimum_realm"),
    ("recipes", "facility_type"), ("recipes", "output_item"),
])
def test_missing_references_rejected_with_field(payload, label, field):
    payload[label][0][field] = "does-not-exist"
    with pytest.raises(ValidationError, match=label + ".*" + field + ".*引用不存在"):
        import_definitions(payload)
    assert counts() == [0, 0, 0, 0, 0]


def test_late_reference_error_rolls_back_everything(payload):
    payload["recipes"][0]["ingredients"][1]["item"] = "unknown-flower"
    with pytest.raises(ValidationError, match="ingredients.*引用不存在"):
        import_definitions(payload)
    assert counts() == [0, 0, 0, 0, 0]


def test_failed_update_rolls_back_earlier_changes(library, payload):
    payload["realms"][0]["description"] = "本次尝试更新境界描述。"
    payload["recipes"][0]["ingredients"][1]["item"] = "unknown-flower"
    old = Realm.objects.get(key="lianqi-chuqi").description
    with pytest.raises(ValidationError):
        import_definitions(payload, update=True)
    assert Realm.objects.get(key="lianqi-chuqi").description == old
    assert counts() == [2, 3, 1, 1, 2]


@pytest.mark.parametrize("quantity", [0, -1, True, 1.5, "2", "1+1"])
@pytest.mark.parametrize("field", ["ingredients", "output_quantity"])
def test_illegal_quantities_rejected(payload, field, quantity):
    if field == "ingredients":
        payload["recipes"][0][field][0]["quantity"] = quantity
    else:
        payload["recipes"][0][field] = quantity
    with pytest.raises(ValidationError, match="正整数"):
        import_definitions(payload)
    assert counts() == [0, 0, 0, 0, 0]


@pytest.mark.parametrize("effects", [
    {}, None, [{"type": "execute_python", "amount": 20}],
    [{"type": "restore_hp", "amount": 0}],
    [{"type": "restore_hp", "amount": True}],
    [{"type": "restore_hp", "amount": 1.5}],
    [{"type": "restore_hp", "amount": "20"}],
    [{"type": "restore_hp", "amount": 20, "script": "print(1)"}],
])
def test_illegal_effects_rejected(payload, effects):
    payload["items"][0]["effects"] = effects
    with pytest.raises(ValidationError, match="items.*effects"):
        import_definitions(payload)
    assert counts() == [0, 0, 0, 0, 0]


def test_material_cannot_have_effects(payload):
    payload["items"][1]["effects"] = [{"type": "restore_hp", "amount": 1}]
    with pytest.raises(ValidationError, match="材料不能"):
        import_definitions(payload)


@pytest.mark.parametrize("entries", [[], [{"item": "huichun-dan", "quantity": 1}], [
    {"item": "qingling-cao", "quantity": 1}, {"item": "qingling-cao", "quantity": 2},
]])
def test_material_list_invariants(payload, entries):
    payload["recipes"][0]["ingredients"] = entries
    with pytest.raises(ValidationError):
        import_definitions(payload)
    assert counts() == [0, 0, 0, 0, 0]


@pytest.mark.parametrize("capabilities", [[], {}, ["unknown"], ["basic_alchemy", "basic_alchemy"]])
def test_invalid_facility_capabilities(payload, capabilities):
    payload["facility_types"][0]["capabilities"] = capabilities
    with pytest.raises(ValidationError, match="capabilities"):
        import_definitions(payload)


@pytest.mark.parametrize("model,key", [
    (Realm, "lianqi-chuqi"), (Item, "huichun-dan"),
    (FacilityType, "basic-alchemy-workshop"), (Recipe, "huichun-dan-recipe"),
])
def test_identifiers_are_immutable(library, model, key):
    instance = model.objects.get(key=key)
    instance.key = "changed-key"
    with pytest.raises(ValidationError, match="稳定编号"):
        instance.save()
    assert model.objects.filter(key=key).exists()
    assert not model.objects.filter(key="changed-key").exists()


def test_model_quantity_does_not_coerce_fraction_or_bool(library):
    ingredient = RecipeIngredient.objects.first()
    for quantity in [0, -1, True, 1.5]:
        ingredient.quantity = quantity
        with pytest.raises(ValidationError):
            ingredient.save()


def test_referenced_material_cannot_change_type(library):
    item = Item.objects.get(key="qingling-cao")
    item.type = "consumable"
    with pytest.raises(ValidationError, match="已作为配方材料"):
        item.save()


@pytest.mark.parametrize("model,key", [
    (Realm, "lianqi-chuqi"), (Item, "huichun-dan"),
    (Item, "qingling-cao"), (FacilityType, "basic-alchemy-workshop"),
])
def test_referenced_definitions_are_protected(library, model, key):
    with pytest.raises(ProtectedError):
        model.objects.get(key=key).delete()
    assert counts() == [2, 3, 1, 1, 2]


def test_database_constraints_prevent_invalid_rows(library):
    ingredient = RecipeIngredient.objects.first()
    with pytest.raises(IntegrityError), transaction.atomic():
        RecipeIngredient.objects.filter(pk=ingredient.pk).update(quantity=0)
    with pytest.raises(IntegrityError), transaction.atomic():
        RecipeIngredient.objects.bulk_create([RecipeIngredient(recipe=ingredient.recipe, item=ingredient.item, quantity=1)])
    ingredient.refresh_from_db()
    assert ingredient.quantity > 0


@pytest.mark.parametrize("route,exact,contains,count", [
    ("realms", "炼气初期", "炼气", 2), ("items", "回春丹", "草", 1),
    ("facility-types", "初级炼丹工坊", "工坊", 1), ("recipes", "回春丹丹方", "丹方", 1),
])
def test_chinese_name_queries(client, library, route, exact, contains, count):
    base = "/api/library/v1/" + route + "/"
    assert client.get(base, {"name": exact}).json()["count"] == 1
    assert client.get(base, {"name": exact + "不存在"}).json()["count"] == 0
    assert client.get(base, {"name_contains": contains}).json()["count"] == count


def test_item_filters_and_pagination(client, library):
    base = "/api/library/v1/items/"
    assert client.get(base, {"type": "material"}).json()["count"] == 2
    assert client.get(base, {"type": "consumable", "name": "回春丹"}).json()["count"] == 1
    assert client.get(base, {"type": "invalid"}).status_code == 400
    page = client.get(base, {"limit": 1, "offset": 1}).json()
    assert page["count"] == 3 and page["next"] and page["previous"]
    assert len(page["results"]) == 1 and page["results"][0]["key"] == "qingling-cao"


def test_item_recipe_trace_has_keys_names_quantities_and_facility(client, library):
    item = client.get("/api/library/v1/items/huichun-dan/").json()
    assert item["effects"] == [{"type": "restore_hp", "amount": 20}]
    assert item["minimum_realm"]["key"] == "lianqi-chuqi"
    assert item["recipes"][0]["key"] == "huichun-dan-recipe"
    recipe = client.get("/api/library/v1/recipes/huichun-dan-recipe/").json()
    assert [(r["item"]["key"], r["quantity"]) for r in recipe["ingredients"]] == [("qingling-cao", 2), ("qinglu-hua", 1)]
    assert recipe["output_item"]["name"] == "回春丹" and recipe["output_quantity"] == 2
    assert recipe["facility_type"]["capabilities"] == ["basic_alchemy"]
    material = client.get("/api/library/v1/items/qingling-cao/").json()
    assert material["ingredient_for"][0]["recipe"]["key"] == recipe["key"]
    def no_internal_ids(data):
        if isinstance(data, dict):
            assert "id" not in data
            for value in data.values():
                no_internal_ids(value)
        elif isinstance(data, list):
            for value in data:
                no_internal_ids(value)
    no_internal_ids(item)
    no_internal_ids(recipe)
    assert client.get("/api/library/v1/items/999/").status_code == 404


@pytest.mark.parametrize("route,key", [
    ("realms", "lianqi-chuqi"), ("items", "huichun-dan"),
    ("facility-types", "basic-alchemy-workshop"), ("recipes", "huichun-dan-recipe"),
])
def test_public_routes_are_read_only(client, library, route, key):
    base = "/api/library/v1/" + route + "/"
    assert client.post(base, {}).status_code == 405
    for method in ["put", "patch", "delete"]:
        assert getattr(client, method)(base + key + "/").status_code == 405
    assert counts() == [2, 3, 1, 1, 2]


@pytest.fixture
def superuser():
    return get_user_model().objects.create_user(username=uuid.uuid4().hex, is_staff=True, is_superuser=True)


def test_admin_requires_staff_and_model_permission(client, library, superuser):
    url = "/admin/cultivation/item/"
    assert client.get(url).status_code == 302
    staff = get_user_model().objects.create_user(username=uuid.uuid4().hex, is_staff=True)
    client.force_login(staff)
    assert client.get(url).status_code == 403
    client.force_login(superuser)
    assert client.get(url).status_code == 200


def item_admin_data(key="test-dan"):
    return {
        "key": key, "name": "测试丹药", "description": "用于后台维护校验的测试物品。",
        "source": "问仙录测试设定", "revision": 1, "type": "consumable",
        "minimum_realm": "", "effects": json.dumps([{"type": "restore_hp", "amount": 20}]), "_save": "保存",
    }


def test_admin_create_and_reject_effects(client, superuser):
    client.force_login(superuser)
    data = item_admin_data()
    data["effects"] = json.dumps([{"type": "restore_hp", "amount": 0}])
    response = client.post("/admin/cultivation/item/add/", data)
    assert response.status_code == 200 and "effects" in response.context["adminform"].form.errors
    assert Item.objects.count() == 0
    data["effects"] = json.dumps([{"type": "restore_hp", "amount": 20}])
    assert client.post("/admin/cultivation/item/add/", data).status_code == 302
    assert Item.objects.get(key="test-dan").effects[0]["amount"] == 20


def test_admin_material_effect_and_identifier_rules(library, superuser):
    request = RequestFactory().get("/")
    request.user = superuser
    model_admin = admin.site._registry[Item]
    form_class = model_admin.get_form(request)
    data = item_admin_data()
    data["type"] = "material"
    assert not form_class(data=data).is_valid()
    item = Item.objects.get(key="huichun-dan")
    assert "key" in model_admin.get_readonly_fields(request, item)
    form_class = model_admin.get_form(request, obj=item)
    assert "key" not in form_class.base_fields


def recipe_admin_data(recipe):
    data = {
        "name": recipe.name, "description": "后台更新测试描述。", "source": recipe.source, "revision": recipe.revision,
        "minimum_realm": recipe.minimum_realm_id, "facility_type": recipe.facility_type_id,
        "output_item": recipe.output_item_id, "output_quantity": recipe.output_quantity,
        "ingredients-TOTAL_FORMS": 2, "ingredients-INITIAL_FORMS": 2,
        "ingredients-MIN_NUM_FORMS": 1, "ingredients-MAX_NUM_FORMS": 1000, "_save": "保存",
    }
    for index, ingredient in enumerate(recipe.ingredients.all()):
        data.update({f"ingredients-{index}-id": ingredient.pk, f"ingredients-{index}-recipe": recipe.pk,
                     f"ingredients-{index}-item": ingredient.item_id, f"ingredients-{index}-quantity": ingredient.quantity})
    return data


@pytest.mark.parametrize("invalid", ["quantity", "fraction", "duplicate", "empty"])
def test_admin_material_inline_rejects_invalid_and_preserves_parent(client, library, superuser, invalid):
    client.force_login(superuser)
    recipe = Recipe.objects.get(key="huichun-dan-recipe")
    old = recipe.description
    data = recipe_admin_data(recipe)
    if invalid == "quantity":
        data["ingredients-0-quantity"] = 0
    elif invalid == "fraction":
        data["ingredients-0-quantity"] = "2.0"
    elif invalid == "duplicate":
        data["ingredients-1-item"] = data["ingredients-0-item"]
    else:
        data["ingredients-0-DELETE"] = "on"
        data["ingredients-1-DELETE"] = "on"
    response = client.post(f"/admin/cultivation/recipe/{recipe.pk}/change/", data)
    assert response.status_code == 200
    recipe.refresh_from_db()
    assert recipe.description == old and recipe.ingredients.count() == 2


def test_admin_material_inline_can_update(client, library, superuser):
    client.force_login(superuser)
    recipe = Recipe.objects.get(key="huichun-dan-recipe")
    data = recipe_admin_data(recipe)
    data["ingredients-0-quantity"] = 3
    assert client.post(f"/admin/cultivation/recipe/{recipe.pk}/change/", data).status_code == 302
    assert RecipeIngredient.objects.get(pk=data["ingredients-0-id"]).quantity == 3


def test_admin_cannot_delete_referenced_item(client, library, superuser):
    client.force_login(superuser)
    item = Item.objects.get(key="huichun-dan")
    response = client.post(f"/admin/cultivation/item/{item.pk}/delete/", {"post": "yes"})
    assert response.status_code == 200
    assert Item.objects.filter(key="huichun-dan").exists()


def test_openapi_includes_library_without_module_warnings(capsys):
    schema = SchemaGenerator().get_schema(public=True)
    stderr = capsys.readouterr().err
    assert "cultivation/" not in stderr and "Cultivation" not in stderr
    for path in ["realms", "items", "facility-types", "recipes"]:
        assert "/api/library/v1/" + path + "/" in schema["paths"]
        assert set(schema["paths"]["/api/library/v1/" + path + "/"]) == {"get"}
    parameters = schema["paths"]["/api/library/v1/items/"]["get"]["parameters"]
    assert {"name", "name_contains", "type", "limit", "offset"} <= {p["name"] for p in parameters}
    definitions = schema["components"]["schemas"]
    for model, field in [("CultivationRecipe", "output_quantity"), ("CultivationIngredient", "quantity"),
                         ("CultivationRealm", "order"), ("CultivationItem", "revision")]:
        assert definitions[model]["properties"][field]["minimum"] == 1


def test_app_without_upstream_migrations_or_data(tmp_path):
    database = tmp_path / "independent.sqlite3"
    environment = {**os.environ, "CULTIVATION_ISOLATED_DB": str(database)}
    settings = "--settings=cultivation.tests.isolated_settings"
    def run(*args):
        return subprocess.run([sys.executable, str(ROOT / "manage.py"), *args, settings],
                              env=environment, cwd=ROOT, capture_output=True, text=True, check=True, timeout=60)
    run("migrate", "cultivation", "--noinput")
    run("import_library", str(SAMPLE))
    script = """
import django, sqlite3
django.setup()
from django.test import Client
from cultivation.models import Realm, Item, FacilityType, Recipe, RecipeIngredient
assert [m.objects.count() for m in [Realm, Item, FacilityType, Recipe, RecipeIngredient]] == [2,3,1,1,2]
from django.db import connection
assert all(t.startswith('cultivation_') or t == 'django_migrations' for t in connection.introspection.table_names())
r = Client().get('/api/library/v1/items/huichun-dan/')
assert r.status_code == 200 and r.json()['effects'][0]['amount'] == 20
assert r.json()['recipes'][0]['key'] == 'huichun-dan-recipe'
"""
    result = subprocess.run([sys.executable, "-c", script], cwd=ROOT,
                            env={**environment, "DJANGO_SETTINGS_MODULE": "cultivation.tests.isolated_settings"},
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def staff_with_permissions(*codenames):
    user = get_user_model().objects.create_user(username=uuid.uuid4().hex, is_staff=True)
    user.user_permissions.set(Permission.objects.filter(content_type__app_label="cultivation", codename__in=codenames))
    return user


def test_admin_recipe_creation_requires_material_add_permission(client, library):
    staff = staff_with_permissions("add_recipe")
    client.force_login(staff)
    recipe = Recipe.objects.get(key="huichun-dan-recipe")
    data = {field: value for field, value in recipe_admin_data(recipe).items() if not field.startswith("ingredients-")}
    data["key"] = "empty-recipe"
    response = client.post("/admin/cultivation/recipe/add/", data)
    assert response.status_code == 403
    assert not Recipe.objects.filter(key="empty-recipe").exists()


@pytest.mark.parametrize("forge_materials", [False, True])
def test_admin_recipe_parent_update_with_read_only_materials(client, library, forge_materials):
    staff = staff_with_permissions("change_recipe", "view_recipeingredient")
    client.force_login(staff)
    recipe = Recipe.objects.get(key="huichun-dan-recipe")
    data = recipe_admin_data(recipe)
    # Read-only inline forms submit identity fields, not item/quantity inputs.
    for index in range(2):
        data.pop(f"ingredients-{index}-item")
        data.pop(f"ingredients-{index}-quantity")
    if forge_materials:
        data["ingredients-0-item"] = Item.objects.get(key="huichun-dan").pk
        data["ingredients-0-quantity"] = 999
        data["ingredients-1-DELETE"] = "on"
    response = client.post(f"/admin/cultivation/recipe/{recipe.pk}/change/", data)
    assert response.status_code == 302
    recipe.refresh_from_db()
    assert recipe.description == data["description"]
    assert list(recipe.ingredients.values_list("item__key", "quantity")) == [("qingling-cao", 2), ("qinglu-hua", 1)]


def test_admin_staff_with_recipe_and_material_permissions_can_create(client, library):
    staff = staff_with_permissions("add_recipe", "add_recipeingredient")
    client.force_login(staff)
    data = recipe_admin_data(Recipe.objects.get(key="huichun-dan-recipe"))
    data["key"] = "staff-recipe"
    data["ingredients-INITIAL_FORMS"] = 0
    for index in range(2):
        data.pop(f"ingredients-{index}-id")
        data.pop(f"ingredients-{index}-recipe")
    assert client.post("/admin/cultivation/recipe/add/", data).status_code == 302
    recipe = Recipe.objects.get(key="staff-recipe")
    assert list(recipe.ingredients.values_list("item__key", "quantity")) == [("qingling-cao", 2), ("qinglu-hua", 1)]


def test_import_replaces_materials_without_accumulating_or_deleting_definitions(library, payload):
    material = {**payload["items"][1], "key": "test-cao", "name": "测试草"}
    payload["items"].append(material)
    payload["recipes"][0]["ingredients"] = [
        {"item": "qinglu-hua", "quantity": 3}, {"item": "test-cao", "quantity": 4},
    ]
    with pytest.raises(ValidationError, match="ingredients.*--update"):
        import_definitions(payload)
    assert not Item.objects.filter(key="test-cao").exists()
    result = import_definitions(payload, update=True)
    assert result["recipes"]["updated"] == 1
    assert list(RecipeIngredient.objects.values_list("item__key", "quantity")) == [("qinglu-hua", 3), ("test-cao", 4)]
    assert Item.objects.filter(key="qingling-cao").exists()
    assert import_definitions(payload)["recipes"]["skipped"] == 1
    assert RecipeIngredient.objects.count() == 2


def test_failed_later_recipe_restores_deleted_materials(library, payload):
    original = list(RecipeIngredient.objects.values_list("id", "item__key", "quantity"))
    payload["recipes"][0]["ingredients"] = [{"item": "qinglu-hua", "quantity": 5}]
    payload["recipes"].append({**payload["recipes"][0], "key": "later-recipe", "facility_type": "missing-facility"})
    with pytest.raises(ValidationError, match="later-recipe.*facility_type"):
        import_definitions(payload, update=True)
    assert list(RecipeIngredient.objects.values_list("id", "item__key", "quantity")) == original
    assert not Recipe.objects.filter(key="later-recipe").exists()


def test_admin_ignores_forged_stable_identifier(client, library, superuser):
    client.force_login(superuser)
    item = Item.objects.get(key="huichun-dan")
    data = item_admin_data(key="renamed-dan")
    assert client.post(f"/admin/cultivation/item/{item.pk}/change/", data).status_code == 302
    item.refresh_from_db()
    assert item.key == "huichun-dan" and not Item.objects.filter(key="renamed-dan").exists()
    assert Recipe.objects.get(key="huichun-dan-recipe").output_item_id == item.pk


@pytest.mark.parametrize("route", ["items", "recipes"])
def test_list_queries_do_not_grow_per_result(client, library, route):
    template = Recipe.objects.get(key="huichun-dan-recipe")
    for index in range(10):
        item = Item.objects.create(key=f"zz-material-{index}", name=f"测试材料{index}",
                                   description="测试关联查询数量。", source="问仙录测试设定", type="material")
        recipe = Recipe.objects.create(key=f"zz-recipe-{index}", name=f"测试配方{index}",
                                       description="测试关联查询数量。", source="问仙录测试设定",
                                       minimum_realm=template.minimum_realm, facility_type=template.facility_type,
                                       output_item=template.output_item, output_quantity=1)
        RecipeIngredient.objects.create(recipe=recipe, item=item, quantity=1)
    query_counts = []
    for limit in (2, 100):
        with CaptureQueriesContext(connection) as captured:
            response = client.get(f"/api/library/v1/{route}/", {"limit": limit})
            assert response.status_code == 200
            rows = response.json()["results"]
            assert len(rows) == min(limit, 13 if route == "items" else 11)
        query_counts.append(len(captured))
    assert query_counts[1] == query_counts[0], query_counts


@pytest.mark.parametrize("amount", [-1, False, 20.0])
def test_admin_and_import_reject_same_non_integer_or_non_positive_effect(client, superuser, payload, amount):
    effects = [{"type": "restore_hp", "amount": amount}]
    payload["items"][0]["effects"] = effects
    with pytest.raises(ValidationError, match="effects"):
        import_definitions(payload)
    client.force_login(superuser)
    data = {**item_admin_data(), "effects": json.dumps(effects)}
    response = client.post("/admin/cultivation/item/add/", data)
    assert response.status_code == 200 and "effects" in response.context["adminform"].form.errors
    assert Item.objects.count() == 0

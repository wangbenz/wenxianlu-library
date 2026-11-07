"""Verify new definition categories without introducing game state or changing v0.1 data."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db.models.deletion import ProtectedError

from cultivation.importing import import_definitions
from cultivation.models import Art, FacilityType, Item, PlaceType, Realm, Recipe, RecipeIngredient


pytestmark = pytest.mark.django_db
ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "data/cultivation/v0.1-sample.json"
DEFINITION_MODELS = (Realm, Item, FacilityType, Recipe, Art, PlaceType)
EXPLANATION_FIELDS = ("usage", "limitations", "adaptation")


@pytest.fixture
def old_payload():
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


def definition(key, name):
    return {
        "key": key, "name": name, "description": "用于验证基础资料维护的独立测试定义。",
        "source": "问仙录测试设定", "revision": 1,
        "usage": "记录通常用途，实际结果由游戏处理。",
        "limitations": "不表示角色已经拥有或学会。",
        "adaptation": "本条为验证新分类的原创测试资料。",
    }


@pytest.fixture
def extended_payload(old_payload):
    old_payload = copy.deepcopy(old_payload)
    old_payload["items"].append({**definition("test-tool", "测试工具"), "type": "equipment", "effects": []})
    old_payload["arts"] = [
        {**definition("test-practice", "测试功法"), "kind": "practice", "minimum_realm": "lianqi-chuqi"},
        {**definition("test-spell", "测试术法"), "kind": "spell"},
    ]
    old_payload["place_types"] = [definition("test-market", "测试坊市")]
    return old_payload


def counts():
    return [model.objects.count() for model in DEFINITION_MODELS] + [RecipeIngredient.objects.count()]


def test_old_four_array_format_remains_idempotent_and_unannotated(old_payload, client):
    assert "arts" not in old_payload and "place_types" not in old_payload
    first = import_definitions(old_payload)
    assert sum(row["created"] for row in first.values()) == 7
    assert counts() == [2, 3, 1, 1, 0, 0, 2]
    assert sum(row["skipped"] for row in import_definitions(old_payload).values()) == 7
    for model in DEFINITION_MODELS[:4]:
        assert all(getattr(row, field) == "" for row in model.objects.all() for field in EXPLANATION_FIELDS)
    for route in ("realms", "items", "facility-types", "recipes"):
        response = client.get(f"/api/library/v1/{route}/")
        assert response.status_code == 200
        assert all(row[field] == "" for row in response.json()["results"] for field in EXPLANATION_FIELDS)


@pytest.mark.parametrize("missing", ["realms", "facility_types", "items", "recipes"])
def test_original_required_arrays_are_still_required(old_payload, missing):
    old_payload.pop(missing)
    with pytest.raises(ValidationError, match="缺少字段.*" + missing):
        import_definitions(old_payload)
    assert counts() == [0, 0, 0, 0, 0, 0, 0]


def test_new_categories_and_equipment_repeat_without_duplication(extended_payload):
    result = import_definitions(extended_payload)
    assert sum(row["created"] for row in result.values()) == 11
    assert counts() == [2, 4, 1, 1, 2, 1, 2]
    assert Art.objects.get(key="test-practice").minimum_realm.key == "lianqi-chuqi"
    assert Art.objects.get(key="test-spell").minimum_realm is None
    assert Item.objects.get(key="test-tool").effects == []
    assert PlaceType.objects.get(key="test-market").usage == extended_payload["place_types"][0]["usage"]
    assert sum(row["skipped"] for row in import_definitions(extended_payload).values()) == 11


def test_omitted_optional_explanations_preserve_existing_annotations(extended_payload):
    import_definitions(extended_payload)
    for model in DEFINITION_MODELS:
        for row in model.objects.all():
            for field in EXPLANATION_FIELDS:
                setattr(row, field, "维护者补充的说明，缺省字段不应清空。")
            row.save()
    for label in ("realms", "items", "facility_types", "recipes", "arts", "place_types"):
        for record in extended_payload[label]:
            for field in EXPLANATION_FIELDS:
                record.pop(field, None)
    for update in (False, True):
        assert sum(row["skipped"] for row in import_definitions(extended_payload, update=update).values()) == 11
        assert all(getattr(row, field) == "维护者补充的说明，缺省字段不应清空。"
                   for model in DEFINITION_MODELS for row in model.objects.all() for field in EXPLANATION_FIELDS)


@pytest.mark.parametrize("label,model", [("arts", Art), ("place_types", PlaceType)])
def test_new_category_conflicts_require_explicit_update(extended_payload, label, model):
    import_definitions(extended_payload)
    original = model.objects.get(key=extended_payload[label][0]["key"])
    previous = original.usage
    extended_payload[label][0]["usage"] = "此次修改用途说明，必须明确允许更新。"
    extended_payload[label][0]["revision"] = 2
    with pytest.raises(ValidationError, match=label + ".*同编号内容冲突.*usage"):
        import_definitions(extended_payload)
    original.refresh_from_db()
    assert original.usage == previous and original.revision == 1
    result = import_definitions(extended_payload, update=True)
    original.refresh_from_db()
    assert result[label]["updated"] == 1
    assert original.usage == extended_payload[label][0]["usage"] and original.revision == 2
    assert counts() == [2, 4, 1, 1, 2, 1, 2]


@pytest.mark.parametrize("label", ["arts", "place_types"])
def test_new_category_duplicate_keys_are_rejected_atomically(extended_payload, label):
    extended_payload[label].append(copy.deepcopy(extended_payload[label][0]))
    with pytest.raises(ValidationError, match=label + ".*key.*重复"):
        import_definitions(extended_payload)
    assert counts() == [0, 0, 0, 0, 0, 0, 0]


@pytest.mark.parametrize("kind", ["unknown", "", None, 1])
def test_art_invalid_kind_rolls_back_all_definitions(extended_payload, kind):
    extended_payload["arts"][0]["kind"] = kind
    with pytest.raises(ValidationError, match="arts.*kind"):
        import_definitions(extended_payload)
    assert counts() == [0, 0, 0, 0, 0, 0, 0]


@pytest.mark.parametrize("label", ["arts", "place_types"])
@pytest.mark.parametrize("field", ["name", "description"])
def test_new_categories_require_chinese_text(extended_payload, label, field):
    extended_payload[label][0][field] = "english only"
    with pytest.raises(ValidationError, match=label + ".*" + field):
        import_definitions(extended_payload)
    assert counts() == [0, 0, 0, 0, 0, 0, 0]


@pytest.mark.parametrize("field", EXPLANATION_FIELDS)
@pytest.mark.parametrize("invalid", [None, "english only", 3, []])
def test_optional_explanations_require_text_when_supplied(extended_payload, field, invalid):
    extended_payload["place_types"][0][field] = invalid
    with pytest.raises(ValidationError, match="place_types.*" + field):
        import_definitions(extended_payload)
    assert counts() == [0, 0, 0, 0, 0, 0, 0]


def test_missing_art_realm_rolls_back_original_and_new_rows(extended_payload):
    extended_payload["arts"][0]["minimum_realm"] = "unknown-realm"
    with pytest.raises(ValidationError, match="arts.*minimum_realm.*引用不存在"):
        import_definitions(extended_payload)
    assert counts() == [0, 0, 0, 0, 0, 0, 0]


def test_late_place_error_restores_updates_and_new_art_rows(old_payload, extended_payload):
    import_definitions(old_payload)
    original_description = Realm.objects.get(key="lianqi-chuqi").description
    extended_payload["realms"][0]["description"] = "尝试更新既有境界，随后应随错误回滚。"
    extended_payload["place_types"][0]["name"] = "invalid place"
    with pytest.raises(ValidationError, match="place_types.*name"):
        import_definitions(extended_payload, update=True)
    assert Realm.objects.get(key="lianqi-chuqi").description == original_description
    assert counts() == [2, 3, 1, 1, 0, 0, 2]
    assert not Item.objects.filter(key="test-tool").exists()


def test_art_minimum_realm_reference_is_protected_without_other_references():
    realm = Realm.objects.create(**definition("art-only-realm", "专用测试境界"), order=1)
    art = Art.objects.create(**definition("realm-bound-art", "有境界要求的功法"), kind="practice", minimum_realm=realm)
    with pytest.raises(ProtectedError):
        realm.delete()
    art.refresh_from_db()
    assert art.minimum_realm_id == realm.pk
    art.delete()
    realm.delete()
    assert not Realm.objects.filter(key="art-only-realm").exists()


@pytest.mark.parametrize("model,extra", [(Art, {"kind": "practice"}), (PlaceType, {})])
def test_new_definition_keys_are_immutable(model, extra):
    row = model.objects.create(**definition("stable-new-definition", "测试新定义"), **extra)
    row.key = "forged-new-key"
    with pytest.raises(ValidationError, match="稳定编号"):
        row.save()
    assert model.objects.filter(key="stable-new-definition").exists()
    assert not model.objects.filter(key="forged-new-key").exists()


def test_equipment_rejects_consumable_effects_in_import_and_model(extended_payload):
    extended_payload["items"][-1]["effects"] = [{"type": "restore_hp", "amount": 20}]
    with pytest.raises(ValidationError, match="items.*effects.*器物"):
        import_definitions(extended_payload)
    assert counts() == [0, 0, 0, 0, 0, 0, 0]
    with pytest.raises(ValidationError, match="器物"):
        Item.objects.create(**definition("bad-equipment", "非法器物"), type="equipment",
                            effects=[{"type": "restore_hp", "amount": 20}])
    assert not Item.objects.exists()


def test_referenced_material_cannot_be_reclassified_as_equipment(old_payload):
    import_definitions(old_payload)
    material = Item.objects.get(key="qingling-cao")
    material.type = "equipment"
    with pytest.raises(ValidationError, match="已作为配方材料.*器物"):
        material.save()
    material.refresh_from_db()
    assert material.type == "material" and material.ingredient_entries.count() == 1


def test_arts_place_queries_and_equipment_filter_return_definitions(client, extended_payload):
    import_definitions(extended_payload)
    arts = "/api/library/v1/arts/"
    practice = client.get(arts, {"kind": "practice", "name_contains": "功法"}).json()
    assert practice["count"] == 1
    row = practice["results"][0]
    assert row["key"] == "test-practice" and row["minimum_realm"]["key"] == "lianqi-chuqi"
    assert all(row[field] == extended_payload["arts"][0][field] for field in EXPLANATION_FIELDS)
    assert "id" not in row and "id" not in row["minimum_realm"]
    spell = client.get(arts, {"kind": "spell"}).json()
    assert spell["count"] == 1 and spell["results"][0]["minimum_realm"] is None
    assert client.get(arts, {"kind": "unknown"}).status_code == 400
    place = client.get("/api/library/v1/place-types/", {"name": "测试坊市"}).json()
    assert place["count"] == 1 and place["results"][0]["key"] == "test-market"
    item = client.get("/api/library/v1/items/", {"type": "equipment"}).json()
    assert item["count"] == 1 and item["results"][0]["effects"] == []
    assert "id" not in place["results"][0]


@pytest.mark.parametrize("route,key", [("arts", "test-practice"), ("place-types", "test-market")])
def test_new_public_categories_are_read_only(client, extended_payload, route, key):
    import_definitions(extended_payload)
    base = f"/api/library/v1/{route}/"
    assert client.get(base + key + "/").status_code == 200
    assert client.head(base + key + "/").status_code == 200
    assert client.post(base, {}).status_code == 405
    for method in ("put", "patch", "delete"):
        assert getattr(client, method)(base + key + "/", {}).status_code == 405
    assert counts() == [2, 4, 1, 1, 2, 1, 2]


@pytest.mark.parametrize("model,model_name,extra", [(Art, "art", {"kind": "practice"}), (PlaceType, "placetype", {})])
def test_new_admin_requires_model_write_permissions(client, model, model_name, extra):
    row = model.objects.create(**definition("protected-definition", "受保护的定义"), **extra)
    data = {**definition("forged-definition", "尝试写入的定义"), **extra, "_save": "保存"}
    base = f"/admin/cultivation/{model_name}/"
    assert client.post(base + "add/", data).status_code == 302
    staff = get_user_model().objects.create_user(username=uuid.uuid4().hex, is_staff=True)
    client.force_login(staff)
    assert client.post(base + "add/", data).status_code == 403
    staff.user_permissions.set(Permission.objects.filter(content_type__app_label="cultivation", codename="view_" + model_name))
    client.force_login(staff)
    assert client.get(base).status_code == 200
    assert client.post(base + "add/", data).status_code == 403
    assert client.post(base + str(row.pk) + "/change/", data).status_code == 403
    assert client.post(base + str(row.pk) + "/delete/", {"post": "yes"}).status_code == 403
    row.refresh_from_db()
    assert row.name == "受保护的定义" and model.objects.count() == 1
    staff.user_permissions.set(Permission.objects.filter(content_type__app_label="cultivation", codename="add_" + model_name))
    client.force_login(staff)
    assert client.post(base + "add/", data).status_code == 302
    assert model.objects.get(key="forged-definition").name == "尝试写入的定义"


def test_source_documented_starter_pack_coexists_with_original_sample(old_payload, client):
    starter = json.loads((ROOT / "data/cultivation/starter-v0.1.json").read_text(encoding="utf-8"))
    definition_count = sum(len(starter[label]) for label in
                           ("realms", "items", "facility_types", "recipes", "arts", "place_types"))
    assert all(starter[label] for label in ("realms", "items", "arts", "place_types"))
    import_definitions(old_payload)
    old_rows = {model: list(model.objects.order_by("pk").values()) for model in DEFINITION_MODELS[:4]}
    original_materials = list(RecipeIngredient.objects.order_by("pk").values())
    for label in ("realms", "items", "arts", "place_types"):
        for record in starter[label]:
            assert "https://" in record["source"]
            assert all(isinstance(record[field], str) and record[field].strip() for field in EXPLANATION_FIELDS)
    result = import_definitions(starter)
    assert sum(row["created"] for row in result.values()) == definition_count
    assert sum(row["updated"] for row in result.values()) == 0
    assert sum(row["skipped"] for row in import_definitions(starter).values()) == definition_count
    for model, rows in old_rows.items():
        assert list(model.objects.filter(pk__in=[row["id"] for row in rows]).order_by("pk").values()) == rows
    assert list(RecipeIngredient.objects.order_by("pk").values()) == original_materials
    for label, route in [("realms", "realms"), ("items", "items"), ("arts", "arts"), ("place_types", "place-types")]:
        for record in starter[label]:
            response = client.get(f"/api/library/v1/{route}/{record['key']}/")
            assert response.status_code == 200
            document = response.json()
            assert all(document[field] == record[field] for field in
                       ("key", "name", "description", "source", "revision") + EXPLANATION_FIELDS)
            assert "id" not in document


def test_migration_preserves_original_rows_and_reverses_in_temporary_database(tmp_path):
    """Rollback discards added columns and categories; production rollback requires a backup."""
    database = tmp_path / "migration-only.sqlite3"
    environment = {**os.environ, "DJANGO_SETTINGS_MODULE": "cultivation.tests.isolated_settings",
                   "CULTIVATION_ISOLATED_DB": str(database)}
    script = r'''
import django, json
from pathlib import Path
django.setup()
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
old_target = [('cultivation', '0001_initial')]
new_target = [('cultivation', '0002_starter_definitions')]
executor = MigrationExecutor(connection)
executor.migrate(old_target)
old_apps = executor.loader.project_state(old_target).apps
Realm, Item, FacilityType, Recipe, Ingredient = [old_apps.get_model('cultivation', name)
    for name in ['Realm', 'Item', 'FacilityType', 'Recipe', 'RecipeIngredient']]
sample = json.loads(Path('data/cultivation/v0.1-sample.json').read_text())
for record in sample['realms']:
    Realm.objects.create(**record)
for record in sample['facility_types']:
    FacilityType.objects.create(**record)
for record in sample['items']:
    values = record.copy()
    key = values.pop('minimum_realm', None)
    values['minimum_realm'] = None if key is None else Realm.objects.get(key=key)
    Item.objects.create(**values)
for record in sample['recipes']:
    values = record.copy()
    entries = values.pop('ingredients')
    for field, model in [('minimum_realm', Realm), ('facility_type', FacilityType), ('output_item', Item)]:
        values[field] = model.objects.get(key=values[field])
    recipe = Recipe.objects.create(**values)
    for entry in entries:
        Ingredient.objects.create(recipe=recipe, item=Item.objects.get(key=entry['item']), quantity=entry['quantity'])
names = ['Realm', 'Item', 'FacilityType', 'Recipe', 'RecipeIngredient']
snapshots = {name: list(old_apps.get_model('cultivation', name).objects.order_by('pk').values()) for name in names}
old_tables = set(connection.introspection.table_names())
executor = MigrationExecutor(connection)
executor.migrate(new_target)
new_apps = executor.loader.project_state(new_target).apps
for name, rows in snapshots.items():
    model = new_apps.get_model('cultivation', name)
    assert list(model.objects.order_by('pk').values(*rows[0])) == rows
    if name != 'RecipeIngredient':
        assert all(getattr(row, field) == '' for row in model.objects.all()
                   for field in ['usage', 'limitations', 'adaptation'])
Art, PlaceType = [new_apps.get_model('cultivation', name) for name in ['Art', 'PlaceType']]
assert Art.objects.count() == PlaceType.objects.count() == 0
realm = new_apps.get_model('cultivation', 'Realm').objects.get(key='lianqi-chuqi')
Art.objects.create(key='rollback-art', name='回退测试功法', description='仅验证迁移回退的数据。',
                   source='问仙录测试设定', kind='practice', minimum_realm=realm)
PlaceType.objects.create(key='rollback-place', name='回退测试场所', description='仅验证迁移回退的数据。',
                         source='问仙录测试设定')
realm.usage = '回退时该新增说明列会被删除。'
realm.save()
executor = MigrationExecutor(connection)
executor.migrate(old_target)
reversed_apps = executor.loader.project_state(old_target).apps
assert set(connection.introspection.table_names()) == old_tables
for name, rows in snapshots.items():
    model = reversed_apps.get_model('cultivation', name)
    assert list(model.objects.order_by('pk').values()) == rows
    assert not {'usage', 'limitations', 'adaptation'} & {field.name for field in model._meta.fields}
with connection.cursor() as cursor:
    cursor.execute('SELECT name FROM django_migrations WHERE app = %s', ['cultivation'])
    assert cursor.fetchall() == [('0001_initial',)]
'''
    result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=environment,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr

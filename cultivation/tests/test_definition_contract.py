"""验证版本追溯、来源、别名与维护写入边界，不裁决玩家行动。"""
import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import RequestFactory
from django.contrib import admin

from cultivation.importing import import_definitions
from cultivation.models import Art, DefinitionRevision, Item, PlaceType, Recipe, RecipeIngredient
from cultivation.revisioning import content_of, hash_of


pytestmark = pytest.mark.django_db
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def pack():
    return json.loads((ROOT / 'data/cultivation/starter-v0.1.json').read_text())


def citation():
    return {'title': '问仙录项目定义', 'locator': '独立测试', 'url': '', 'supports': '仅验证数据结构。'}


def test_automatic_revision_and_old_snapshot_survive_update(pack, client):
    import_definitions(pack)
    row = Art.objects.get(key='object-control')
    before = content_of(row)
    first_hash = row.content_hash
    row.usage += ' 这次新增取放说明。'
    row.save()
    row.refresh_from_db()
    assert row.revision == 2 and row.content_hash != first_hash
    assert DefinitionRevision.objects.get(category='arts', definition_key=row.key, revision=1).content == before
    old = client.get('/api/library/v1/revisions/arts:object-control:1/')
    assert old.status_code == 200 and old.json()['content'] == before
    assert old.json()['content_hash'] == hash_of(before)
    row.save()
    assert row.revision == 2
    assert DefinitionRevision.objects.filter(category='arts', definition_key=row.key).count() == 2


def test_stale_model_copy_cannot_overwrite_new_version(pack):
    import_definitions(pack)
    first = Art.objects.get(key='object-control')
    stale = Art.objects.get(pk=first.pk)
    first.description += ' 新版说明。'
    first.save()
    stale.description = '旧编辑器尝试覆盖。'
    with pytest.raises(ValidationError, match='重新读取'):
        stale.save()
    first.refresh_from_db()
    assert first.description.endswith('新版说明。') and first.revision == 2


def test_import_counters_are_managed_and_late_error_rolls_back_history(pack):
    import_definitions(pack)
    original = Art.objects.get(key='object-control')
    before = original.usage
    row = next(x for x in pack['arts'] if x['key'] == original.key)
    row['usage'] += ' 新的用途说明。'
    row['revision'] = 1  # 旧格式计数不会令内容变更保持在旧版本。
    with pytest.raises(ValidationError, match='同编号内容冲突'):
        import_definitions(pack)
    broken = copy.deepcopy(pack)
    broken['place_types'][-1]['name'] = 'not Chinese'
    with pytest.raises(ValidationError):
        import_definitions(broken, update=True)
    original.refresh_from_db()
    assert original.usage == before and original.revision == 1
    assert DefinitionRevision.objects.filter(category='arts', definition_key=original.key).count() == 1
    import_definitions(pack, update=True)
    original.refresh_from_db()
    assert original.revision == 2
    assert sum(x['updated'] for x in import_definitions(pack, update=True).values()) == 0
    original.refresh_from_db()
    assert original.revision == 2


@pytest.mark.parametrize('aliases', [{}, None, [''], ['御物术', '御物术'], [' abc ']])
def test_invalid_aliases_are_rejected_atomically(pack, aliases):
    pack['arts'][0]['aliases'] = aliases
    with pytest.raises(ValidationError):
        import_definitions(pack)
    assert not Art.objects.exists() and not DefinitionRevision.objects.exists()


@pytest.mark.parametrize('sources', [{}, None, [{'title': '来源'}],
    [{**citation(), 'url': 'javascript:alert(1)'}], [{**citation(), 'supports': ''}]])
def test_invalid_sources_are_rejected_atomically(pack, sources):
    pack['arts'][0]['sources'] = sources
    with pytest.raises(ValidationError):
        import_definitions(pack)
    assert not Art.objects.exists() and not DefinitionRevision.objects.exists()


def test_exact_and_partial_alias_search_do_not_duplicate_candidates(pack, client):
    row = next(x for x in pack['arts'] if x['key'] == 'object-control')
    row['aliases'] = ['御物术', '御物法']
    import_definitions(pack)
    for field, term in [('name', '御物术'), ('name_contains', '御物'), ('name_contains', '控物')]:
        response = client.get('/api/library/v1/arts/', {field: term})
        assert response.status_code == 200 and response.json()['count'] == 1
        assert response.json()['results'][0]['key'] == 'object-control'
    assert client.get('/api/library/v1/arts/', {'name': "' OR 1=1 --"}).json()['count'] == 0


def test_entry_kind_and_background_alias_are_explicit(pack, client):
    row = next(x for x in pack['arts'] if x['key'] == 'object-control')
    row['entry_kind'] = 'definition'
    row['typical_realm'] = row.pop('minimum_realm', row.get('typical_realm'))
    import_definitions(pack)
    response = client.get('/api/library/v1/arts/', {'entry_kind': 'definition'})
    assert 'object-control' in [x['key'] for x in response.json()['results']]
    detail = client.get('/api/library/v1/arts/object-control/').json()
    assert detail['typical_realm'] == detail['minimum_realm']
    assert client.get('/api/library/v1/arts/', {'entry_kind': 'unknown'}).status_code == 400


def test_background_compatibility_fields_cannot_disagree(pack):
    row = pack['arts'][0]
    row['typical_realm'] = 'lianqi'
    row['minimum_realm'] = None
    with pytest.raises(ValidationError, match='背景引用不一致'):
        import_definitions(pack)
    assert not DefinitionRevision.objects.exists()


def test_revisions_are_read_only_and_identity_is_not_reused(pack, client):
    import_definitions(pack)
    row = Art.objects.get(key='object-control')
    snapshot = DefinitionRevision.objects.get(category='arts', definition_key=row.key, revision=1)
    with pytest.raises(ValidationError, match='不能覆盖'):
        snapshot.save()
    with pytest.raises(ValidationError, match='不能删除'):
        snapshot.delete()
    assert client.post('/api/library/v1/revisions/', {}).status_code == 405
    assert client.delete('/api/library/v1/revisions/arts:object-control:1/').status_code == 405
    assert client.get('/api/library/v1/revisions/arts:object-control:999/').status_code == 404
    row.delete()
    new = Art(key='object-control', name='新对象', description='不能借用已删除对象的编号。', source='测试设定', kind='spell')
    with pytest.raises(ValidationError, match='不能删除后复用'):
        new.save()


def test_admin_hidden_version_rejects_stale_form(pack, client):
    import_definitions(pack)
    row = Art.objects.get(key='object-control')
    user = get_user_model().objects.create_superuser(username='contract-editor', password='testing-only')
    client.force_login(user)
    stale_version = row.revision
    row.description += ' 编辑器之外已修改。'
    row.save()
    response = client.post(f'/admin/cultivation/art/{row.pk}/change/', {
        'name': '旧表单覆盖', 'description': '旧表单覆盖正文。', 'source': row.source,
        'kind': 'spell', 'minimum_realm': row.minimum_realm_id or '',
        'entry_kind': row.entry_kind, 'expected_revision': stale_version, '_save': '保存'})
    assert response.status_code == 200 and '重新加载' in response.content.decode()
    row.refresh_from_db()
    assert row.name != '旧表单覆盖' and row.revision == stale_version + 1


def test_recipe_history_records_final_material_list_once(client):
    old = json.loads((ROOT / 'data/cultivation/v0.1-sample.json').read_text())
    import_definitions(old)
    recipe = Recipe.objects.get(key='huichun-dan-recipe')
    assert recipe.revision == 1
    history = DefinitionRevision.objects.get(category='recipes', definition_key=recipe.key, revision=1)
    assert len(history.content['ingredients']) == 2
    old['recipes'][0]['ingredients'][0]['quantity'] += 1
    import_definitions(old, update=True)
    recipe.refresh_from_db()
    assert recipe.revision == 2
    latest = DefinitionRevision.objects.get(category='recipes', definition_key=recipe.key, revision=2)
    assert latest.content['ingredients'][0]['quantity'] == history.content['ingredients'][0]['quantity'] + 1
    assert DefinitionRevision.objects.filter(category='recipes', definition_key=recipe.key).count() == 2


def test_admin_recipe_inline_has_one_complete_new_version(client):
    from cultivation.tests.test_library import recipe_admin_data
    old = json.loads((ROOT / 'data/cultivation/v0.1-sample.json').read_text())
    import_definitions(old)
    recipe = Recipe.objects.get(key='huichun-dan-recipe')
    editor = get_user_model().objects.create_superuser(username='recipe-contract-editor', password='testing-only')
    client.force_login(editor)
    payload = recipe_admin_data(recipe)
    payload['expected_revision'] = recipe.revision
    payload['ingredients-0-quantity'] = 4
    response = client.post(f'/admin/cultivation/recipe/{recipe.pk}/change/', payload)
    assert response.status_code == 302
    recipe.refresh_from_db()
    assert recipe.revision == 2
    records = DefinitionRevision.objects.filter(category='recipes', definition_key=recipe.key)
    assert records.count() == 2
    latest = records.get(revision=2)
    assert len(latest.content['ingredients']) == 2
    assert latest.content['ingredients'][0]['quantity'] == 4
    assert latest.content['description'] == payload['description']


def test_direct_ingredient_change_updates_recipe_version():
    old = json.loads((ROOT / 'data/cultivation/v0.1-sample.json').read_text())
    import_definitions(old)
    recipe = Recipe.objects.get(key='huichun-dan-recipe')
    material = recipe.ingredients.get(item__key='qingling-cao')
    material.quantity = 3
    material.save()
    recipe.refresh_from_db()
    assert recipe.revision == 2
    latest = DefinitionRevision.objects.get(category='recipes', definition_key=recipe.key, revision=2)
    assert latest.content['ingredients'][0]['quantity'] == 3


def test_last_material_deletion_cannot_leave_stale_recipe_history():
    old = json.loads((ROOT / 'data/cultivation/v0.1-sample.json').read_text())
    import_definitions(old)
    recipe = Recipe.objects.get(key='huichun-dan-recipe')
    recipe.ingredients.first().delete()
    last = recipe.ingredients.get()
    identity = last.pk
    with pytest.raises(ValidationError, match='至少包含一种材料'):
        last.delete()
    recipe.refresh_from_db()
    assert recipe.revision == 2 and recipe.ingredients.get().pk == identity
    assert DefinitionRevision.objects.filter(category='recipes', definition_key=recipe.key).count() == 2


def test_incomplete_recipe_is_not_returned_as_published_definition(client):
    old = json.loads((ROOT / 'data/cultivation/v0.1-sample.json').read_text())
    import_definitions(old)
    base = Recipe.objects.get(key='huichun-dan-recipe')
    pending = Recipe.objects.create(key='pending-recipe', name='构造中的配方', description='尚未写入材料。',
        source='测试设定', minimum_realm=base.minimum_realm, facility_type=base.facility_type,
        output_item=base.output_item, output_quantity=1)
    assert pending.content_hash == ''
    assert client.get('/api/library/v1/recipes/pending-recipe/').status_code == 404
    first = base.ingredients.first()
    RecipeIngredient.objects.create(recipe=pending, item=first.item, quantity=1)
    assert client.get('/api/library/v1/recipes/pending-recipe/').status_code == 200
    assert DefinitionRevision.objects.filter(category='recipes', definition_key=pending.key).count() == 1


def test_material_move_tracks_both_affected_recipes_and_cannot_empty_one():
    old = json.loads((ROOT / 'data/cultivation/v0.1-sample.json').read_text())
    import_definitions(old)
    source = Recipe.objects.get(key='huichun-dan-recipe')
    target = Recipe.objects.create(key='other-recipe', name='另一测试配方', description='验证材料移动。',
        source='测试设定', minimum_realm=source.minimum_realm, facility_type=source.facility_type,
        output_item=source.output_item, output_quantity=1)
    material = source.ingredients.first()
    material.recipe = target
    material.save()
    source.refresh_from_db(); target.refresh_from_db()
    assert source.revision == 2 and target.revision == 1
    assert len(DefinitionRevision.objects.get(category='recipes', definition_key=source.key, revision=2).content['ingredients']) == 1
    assert len(DefinitionRevision.objects.get(category='recipes', definition_key=target.key, revision=1).content['ingredients']) == 1
    material.recipe = source
    with pytest.raises(ValidationError, match='至少包含一种材料'):
        material.save()
    assert target.ingredients.count() == 1 and source.ingredients.count() == 1


def test_contract_migration_freezes_old_content_and_reverses_without_rewriting(tmp_path):
    script = '''
import django
django.setup()
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from cultivation.revisioning import hash_of
executor = MigrationExecutor(connection)
executor.migrate([('cultivation', '0002_starter_definitions')])
state = executor.loader.project_state([('cultivation', '0002_starter_definitions')]).apps
model = state.get_model('cultivation', 'Art')
old = model.objects.create(key='migration-art', name='迁移术法', description='迁移前正文。',
                           source='迁移测试来源', usage='旧用途。', kind='spell', revision=7)
before = list(model.objects.values())
executor = MigrationExecutor(connection)
executor.migrate([('cultivation', '0003_definition_contract')])
state = executor.loader.project_state([('cultivation', '0003_definition_contract')]).apps
history = state.get_model('cultivation', 'DefinitionRevision').objects.get()
assert history.revision == 7 and history.content['description'] == '迁移前正文。'
assert history.content['source'] == '迁移测试来源'
assert history.content_hash == hash_of(history.content)
latest = state.get_model('cultivation', 'Art').objects.get()
assert latest.revision == 7 and latest.pk == old.pk
assert latest.content_hash == history.content_hash and latest.sources == []
executor = MigrationExecutor(connection)
executor.migrate([('cultivation', '0002_starter_definitions')])
state = executor.loader.project_state([('cultivation', '0002_starter_definitions')]).apps
assert list(state.get_model('cultivation', 'Art').objects.values()) == before
'''
    env = {**os.environ, 'DJANGO_SETTINGS_MODULE': 'server.settings',
           'WENXIANLU_DB_PATH': str(tmp_path / 'migration.sqlite3')}
    result = subprocess.run([sys.executable, '-c', script], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr

"""Validate the standalone library runtime without historical Open5e dependencies."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest
from django.apps import apps
from django.conf import settings
from django.contrib import admin
from django.core.management import get_commands
from django.db import connection
from django.test import Client
from drf_spectacular.generators import SchemaGenerator


pytestmark = pytest.mark.django_db
ROOT = Path(__file__).resolve().parents[2]
LEGACY_APPS = {"api", "api_v2", "search"}
REMOVED_DEPENDENCIES = {"newrelic", "numpy", "rapidfuzz", "sklearn", "requests", "corsheaders", "debug_toolbar"}
FRAMEWORK_TABLES = {
    "django_migrations", "django_admin_log", "django_content_type", "django_session",
    "auth_group", "auth_group_permissions", "auth_permission", "auth_user",
    "auth_user_groups", "auth_user_user_permissions",
}
LIBRARY_TABLES = {"cultivation_realm", "cultivation_item", "cultivation_facilitytype",
                  "cultivation_recipe", "cultivation_recipeingredient", "cultivation_art",
                  "cultivation_placetype", "cultivation_definitionrevision"}


def test_only_library_and_framework_are_installed():
    assert settings.SETTINGS_MODULE == "server.settings"
    assert {app.label for app in apps.get_app_configs()} == {
        "admin", "auth", "contenttypes", "sessions", "messages", "staticfiles", "cultivation",
        "rest_framework", "django_filters", "drf_spectacular", "drf_spectacular_sidecar",
    }
    assert not any(module.split(".")[0] in LEGACY_APPS for module in sys.modules)
    assert not any(module.split(".")[0] in REMOVED_DEPENDENCIES for module in sys.modules)
    assert all(not middleware.startswith("server.middleware.") for middleware in settings.MIDDLEWARE)
    assert "server.oas" not in sys.modules and "newrelic.agent" not in sys.modules
    assert "api.utils" not in json.dumps(settings.REST_FRAMEWORK)
    commands = get_commands()
    assert commands["import_library"] == "cultivation"
    assert "quicksetup" not in commands and "buildindex" not in commands


def test_database_has_only_required_tables_and_migrations():
    assert set(connection.introspection.table_names()) == FRAMEWORK_TABLES | LIBRARY_TABLES
    with connection.cursor() as cursor:
        cursor.execute("SELECT DISTINCT app FROM django_migrations")
        assert {row[0] for row in cursor.fetchall()} == {"admin", "auth", "contenttypes", "sessions", "cultivation"}


def test_admin_registers_only_library_and_auth_models():
    assert {model._meta.label for model in admin.site._registry} == {
        "auth.User", "auth.Group", "cultivation.Realm", "cultivation.Item",
        "cultivation.FacilityType", "cultivation.Recipe", "cultivation.Art", "cultivation.PlaceType", "cultivation.DefinitionRevision",
    }


@pytest.mark.parametrize("path", ["/v1/", "/v2/", "/v1/spells/", "/v2/items/",
                                  "/v2/search/?query=fireball", "/spells/", "/monsters/goblin/", "/search/"])
def test_old_routes_are_not_available_or_redirected(client, path):
    response = client.get(path)
    assert response.status_code == 404
    assert "Location" not in response


def test_schema_is_library_only_without_warnings_or_upstream_servers(capsys):
    schema = SchemaGenerator().get_schema(public=True)
    assert capsys.readouterr().err == ""
    assert schema["info"]["title"] == "问仙录资料库"
    assert schema["servers"] == [{"url": "/", "description": "当前资料库服务"}]
    assert len(schema["paths"]) == 14
    assert all(path.startswith("/api/library/v1/") for path in schema["paths"])
    assert all(set(methods) == {"get"} for methods in schema["paths"].values())
    assert "open5e.com" not in json.dumps(schema)


def test_root_docs_and_login_use_actual_default_routes(client):
    response = client.get("/")
    assert response.status_code == 302 and response["Location"] == "/schema/swagger-ui/"
    for path, content in [("/schema/swagger-ui/", "SwaggerUIBundle"), ("/schema/redoc/", "redoc"),
                          ("/admin/login/", "csrfmiddlewaretoken")]:
        response = client.get(path)
        assert response.status_code == 200 and content in response.content.decode()
        assert "open5e.com" not in response.content.decode()
    assert client.get("/admin/").status_code == 302


def test_admin_login_keeps_csrf_protection():
    response = Client(enforce_csrf_checks=True).post("/admin/login/", {})
    assert response.status_code == 403


def subprocess_environment(database=None):
    environment = os.environ.copy()
    environment.pop("DJANGO_SETTINGS_MODULE", None)
    environment.pop("WENXIANLU_DB_PATH", None)
    environment["WENXIANLU_DEBUG"] = "True"
    if database is not None:
        environment["WENXIANLU_DB_PATH"] = str(database)
    return environment


def run_manage(environment, *arguments):
    return subprocess.run([sys.executable, str(ROOT / "manage.py"), *arguments], cwd=ROOT,
                          env=environment, capture_output=True, text=True, timeout=60, check=True)


def test_fresh_default_database_and_repeat_initialization(tmp_path):
    database = tmp_path / "fresh.sqlite3"
    environment = subprocess_environment(database)
    sample = ROOT / "data/cultivation/v0.1-sample.json"
    for _ in range(2):
        run_manage(environment, "migrate", "--noinput")
        run_manage(environment, "import_library", str(sample))
    with sqlite3.connect(database) as db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        assert tables == FRAMEWORK_TABLES | LIBRARY_TABLES
        assert {r[0] for r in db.execute("SELECT DISTINCT app FROM django_migrations")} == {"admin", "auth", "contenttypes", "sessions", "cultivation"}
        for table, count in [("cultivation_realm", 2), ("cultivation_item", 3), ("cultivation_facilitytype", 1),
                             ("cultivation_recipe", 1), ("cultivation_recipeingredient", 2),
                             ("cultivation_art", 0), ("cultivation_placetype", 0), ("auth_user", 0)]:
            assert db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] == count
    # Legacy business imports must fail; optional packages behave as genuinely absent.
    # No fake modules or access to the historical index/database are allowed.
    script = r'''
import importlib.abc, sys, json
from pathlib import Path
optional_packages = {'newrelic', 'numpy', 'rapidfuzz', 'sklearn', 'requests',
                     'corsheaders', 'debug_toolbar'}
class RejectUpstreamDependencies(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        package = fullname.split('.')[0]
        if package in {'api', 'api_v2', 'search'}:
            raise AssertionError('Unexpected legacy business import: ' + fullname)
        if package in optional_packages:
            raise ModuleNotFoundError('No module named ' + repr(fullname), name=fullname)
sys.meta_path.insert(0, RejectUpstreamDependencies())
old_database = Path('db.sqlite3').resolve()
def audit(event, args):
    if event in {'open', 'sqlite3.connect'} and isinstance(args[0], (str, bytes)):
        name = args[0].decode() if isinstance(args[0], bytes) else args[0]
        assert Path(name).name != 'vector_index.pkl', name
        assert Path(name).resolve() != old_database, name
sys.addaudithook(audit)
import server.wsgi
from django.conf import settings
from django.test import Client
assert settings.SETTINGS_MODULE == 'server.settings'
client = Client(HTTP_HOST='127.0.0.1')
for route, count in [('realms',2),('items',3),('facility-types',1),('recipes',1),
                     ('arts',0),('place-types',0)]:
    response = client.get('/api/library/v1/' + route + '/')
    assert response.status_code == 200 and response.json()['count'] == count
item = client.get('/api/library/v1/items/huichun-dan/').json()
recipe = client.get('/api/library/v1/recipes/' + item['recipes'][0]['key'] + '/').json()
assert item['effects'] == [{'type':'restore_hp','amount':20}]
assert [(row['item']['key'],row['quantity']) for row in recipe['ingredients']] == [('qingling-cao',2),('qinglu-hua',1)]
assert recipe['output_item']['key'] == item['key'] and recipe['output_quantity'] == 2
assert recipe['facility_type']['capabilities'] == ['basic_alchemy']
for document in [item, recipe, recipe['output_item'], recipe['facility_type']]:
    assert document['url'].startswith('http://127.0.0.1/api/library/v1/'), document['url']
for path in ['/v1/spells/', '/v2/items/', '/v2/search/?query=fireball']:
    response = client.get(path)
    assert response.status_code == 404 and 'Location' not in response
schema = client.get('/schema/', HTTP_ACCEPT='application/vnd.oai.openapi+json')
assert schema.status_code == 200
assert schema.json()['servers'] == [{'url': '/', 'description': '当前资料库服务'}]
assert all(path.startswith('/api/library/v1/') for path in schema.json()['paths'])
assert client.get('/admin/login/').status_code == 200
assert not any(module.split('.')[0] in optional_packages for module in sys.modules)
'''
    subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=environment,
                   capture_output=True, text=True, timeout=60, check=True)


def test_wsgi_defaults_to_library_and_preserves_explicit_settings(tmp_path):
    script = """
import os, server.wsgi
from django.conf import settings
assert settings.SETTINGS_MODULE == os.environ['EXPECTED_SETTINGS']
assert 'newrelic.agent' not in __import__('sys').modules
if settings.SETTINGS_MODULE == 'server.settings':
    from pathlib import Path
    assert settings.DATABASE_PATH == Path.cwd() / 'var/wenxianlu-library.sqlite3'
"""
    default = {**subprocess_environment(), "EXPECTED_SETTINGS": "server.settings"}
    explicit = {**default, "DJANGO_SETTINGS_MODULE": "cultivation.tests.isolated_settings",
                "CULTIVATION_ISOLATED_DB": str(tmp_path / "explicit.sqlite3"),
                "EXPECTED_SETTINGS": "cultivation.tests.isolated_settings"}
    for environment in (default, explicit):
        subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=environment,
                       capture_output=True, text=True, timeout=60, check=True)


def test_non_debug_wsgi_requires_key_and_keeps_admin_csrf(tmp_path):
    database = tmp_path / "non-debug.sqlite3"
    environment = subprocess_environment(database)
    environment["WENXIANLU_DEBUG"] = "False"
    environment.pop("SECRET_KEY", None)
    missing_key = subprocess.run([sys.executable, "-c", "import server.wsgi"], cwd=ROOT,
                                 env=environment, capture_output=True, text=True, timeout=60)
    assert missing_key.returncode != 0
    assert "非调试模式必须通过 SECRET_KEY 环境变量设置密钥" in missing_key.stderr
    assert not database.exists()
    script = """
from pathlib import Path
import os, server.wsgi
from django.conf import settings
from django.test import Client
assert not settings.DEBUG
assert settings.DATABASE_PATH == Path(os.environ['WENXIANLU_DB_PATH'])
client = Client(enforce_csrf_checks=True, HTTP_HOST='127.0.0.1')
assert client.get('/admin/login/').status_code == 200
assert client.post('/admin/login/', {}).status_code == 403
assert client.get('/v2/spells/').status_code == 404
"""
    environment["SECRET_KEY"] = "isolated-test-key-only-never-used-by-running-services"
    subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=environment,
                   capture_output=True, text=True, timeout=60, check=True)
    assert not database.exists()


@pytest.mark.parametrize("alias", [False, True])
def test_library_configuration_refuses_upstream_database(tmp_path, alias):
    path = ROOT / "db.sqlite3"
    if alias:
        path = tmp_path / "old-database-link.sqlite3"
        path.symlink_to(ROOT / "db.sqlite3")
    result = subprocess.run([sys.executable, str(ROOT / "manage.py"), "check"], cwd=ROOT,
                            env=subprocess_environment(path), capture_output=True, text=True, timeout=60)
    assert result.returncode != 0
    assert "不能使用上游对照数据库" in result.stderr

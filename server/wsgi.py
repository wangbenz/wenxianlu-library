"""问仙录资料库 WSGI 入口，仅初始化资料库应用。"""
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "server.settings")

from django.core.wsgi import get_wsgi_application

application = get_wsgi_application()

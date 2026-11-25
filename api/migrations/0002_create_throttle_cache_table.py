"""API 요청 제한용 DB 캐시 테이블(settings.CACHES['api_throttle'])을 만든다 (#102).

배포는 migrate만 실행하므로 createcachetable을 마이그레이션에서 부른다. 이미 있으면 아무것도 하지 않는다.
테스트 DB는 Django 테스트 실행기도 createcachetable을 부른다.
"""

from django.core.management import call_command
from django.db import migrations


def create_cache_table(apps, schema_editor):
    call_command("createcachetable", database=schema_editor.connection.alias, verbosity=0)


class Migration(migrations.Migration):
    dependencies = [("api", "0001_initial")]

    operations = [migrations.RunPython(create_cache_table, migrations.RunPython.noop)]

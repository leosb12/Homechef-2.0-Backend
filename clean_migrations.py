import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.local')
django.setup()

from django.db import connection

with connection.cursor() as cursor:
    cursor.execute("DELETE FROM django_migrations WHERE app = 'confianza_administracion_seguridad'")
    print("Successfully deleted inconsistent migration record.")

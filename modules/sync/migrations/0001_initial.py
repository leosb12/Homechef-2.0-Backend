# Generated for Sprint 1 offline-first support.

from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="SyncOperation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("operation_id", models.CharField(db_index=True, max_length=80, unique=True)),
                ("device_id", models.CharField(db_index=True, max_length=120)),
                ("entity", models.CharField(max_length=80)),
                ("action", models.CharField(max_length=20)),
                ("local_id", models.CharField(blank=True, max_length=120)),
                ("server_id", models.CharField(blank=True, max_length=120)),
                ("status", models.CharField(max_length=20)),
                ("result", models.JSONField(blank=True, default=dict)),
                ("error_message", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("processed_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "db_table": "sync_operations",
                "indexes": [
                    models.Index(fields=["device_id", "created_at"], name="sync_operat_device__e6ba8f_idx"),
                    models.Index(fields=["entity", "server_id"], name="sync_operat_entity_1b5ce2_idx"),
                    models.Index(fields=["status"], name="sync_operat_status_c4589b_idx"),
                ],
            },
        ),
    ]

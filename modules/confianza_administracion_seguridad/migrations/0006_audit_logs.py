from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("confianza_administracion_seguridad", "0005_merge_20260619_1840"),
    ]

    operations = [
        migrations.CreateModel(
            name="AuditLog",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("event_type", models.CharField(db_index=True, max_length=120)),
                (
                    "event_category",
                    models.CharField(
                        choices=[
                            ("users", "Usuarios"),
                            ("chefs", "Cocineros"),
                            ("riders", "Repartidores"),
                            ("orders", "Pedidos"),
                            ("publications", "Publicaciones"),
                            ("payments", "Pagos"),
                            ("security", "Seguridad"),
                            ("admin", "Administracion"),
                            ("notifications", "Notificaciones"),
                            ("system", "Sistema"),
                        ],
                        db_index=True,
                        max_length=40,
                    ),
                ),
                ("action", models.CharField(db_index=True, max_length=40)),
                ("entity_type", models.CharField(db_index=True, max_length=80)),
                ("entity_id", models.CharField(blank=True, db_index=True, max_length=128)),
                ("actor_user_id", models.CharField(blank=True, db_index=True, max_length=128)),
                ("actor_role", models.CharField(blank=True, max_length=40)),
                ("actor_name", models.CharField(blank=True, max_length=255)),
                ("target_user_id", models.CharField(blank=True, max_length=128)),
                ("target_role", models.CharField(blank=True, max_length=40)),
                ("description", models.TextField(blank=True)),
                ("old_values", models.JSONField(blank=True, default=dict)),
                ("new_values", models.JSONField(blank=True, default=dict)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("ip_address", models.GenericIPAddressField(blank=True, null=True)),
                ("user_agent", models.TextField(blank=True)),
                ("request_id", models.CharField(blank=True, db_index=True, max_length=128)),
                ("source", models.CharField(db_index=True, default="backend", max_length=60)),
                (
                    "severity",
                    models.CharField(
                        choices=[("info", "Info"), ("warning", "Warning"), ("critical", "Critical")],
                        db_index=True,
                        default="info",
                        max_length=20,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[("success", "Success"), ("failed", "Failed")],
                        db_index=True,
                        default="success",
                        max_length=20,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
            ],
            options={
                "db_table": "audit_logs",
                "indexes": [
                    models.Index(fields=["created_at"], name="audit_logs_created_262184_idx"),
                    models.Index(fields=["event_category"], name="audit_logs_event_c_6f612b_idx"),
                    models.Index(fields=["action"], name="audit_logs_action_31f574_idx"),
                    models.Index(fields=["actor_user_id"], name="audit_logs_actor_u_6a2681_idx"),
                    models.Index(fields=["entity_type", "entity_id"], name="audit_logs_entity__d4c2e5_idx"),
                    models.Index(fields=["severity"], name="audit_logs_severit_fc52cc_idx"),
                    models.Index(fields=["status"], name="audit_logs_status_515e02_idx"),
                ],
            },
        ),
    ]

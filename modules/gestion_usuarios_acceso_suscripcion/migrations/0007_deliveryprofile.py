from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("gestion_usuarios_acceso_suscripcion", "0006_alter_aipaymentmethod_provider"),
    ]

    operations = [
        migrations.CreateModel(
            name="DeliveryProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("vehicle_type", models.CharField(choices=[("motocicleta", "Motocicleta"), ("vehiculo", "Vehiculo")], max_length=20)),
                ("vehicle_brand", models.CharField(max_length=120)),
                ("vehicle_model", models.CharField(max_length=120)),
                ("vehicle_plate", models.CharField(max_length=40)),
                ("vehicle_front_image_url", models.URLField(blank=True, max_length=1000)),
                ("vehicle_rear_image_url", models.URLField(blank=True, max_length=1000)),
                ("approval_status", models.CharField(choices=[("recien_registrado", "Recien registrado"), ("activo", "Activo"), ("suspendido", "Suspendido")], default="recien_registrado", max_length=30)),
                ("status_notes", models.CharField(blank=True, max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="delivery_profile",
                        to="gestion_usuarios_acceso_suscripcion.userprofile",
                    ),
                ),
            ],
            options={
                "db_table": "delivery_profiles",
            },
        ),
        migrations.AddIndex(
            model_name="deliveryprofile",
            index=models.Index(fields=["approval_status", "created_at"], name="delivery_pr_approva_6ce387_idx"),
        ),
        migrations.AddIndex(
            model_name="deliveryprofile",
            index=models.Index(fields=["vehicle_type"], name="delivery_pr_vehicle_84da8a_idx"),
        ),
        migrations.AddIndex(
            model_name="deliveryprofile",
            index=models.Index(fields=["vehicle_plate"], name="delivery_pr_vehicle_daef0e_idx"),
        ),
    ]

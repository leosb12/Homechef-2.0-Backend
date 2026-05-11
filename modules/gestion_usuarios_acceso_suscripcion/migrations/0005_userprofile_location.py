from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("gestion_usuarios_acceso_suscripcion", "0004_usoia"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="location_latitude",
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="location_longitude",
            field=models.FloatField(blank=True, null=True),
        ),
    ]

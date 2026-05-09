# Generated for Sprint 1 offline-first support.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("gestion_cocinero", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="dish",
            name="deleted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="dish",
            name="version",
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddIndex(
            model_name="dish",
            index=models.Index(fields=["deleted_at"], name="chef_dishes_deleted_f205f8_idx"),
        ),
    ]

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        (
            "pedidos_checkout_pagos",
            "0008_rename_order_recei_order_i_131937_idx_order_recei_order_i_d8c679_idx_and_more",
        ),
    ]

    operations = [
        migrations.AddField(
            model_name="pickupconfirmation",
            name="last_retention_extension_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="pickupconfirmation",
            name="retention_extension_count",
            field=models.PositiveSmallIntegerField(default=0),
        ),
    ]

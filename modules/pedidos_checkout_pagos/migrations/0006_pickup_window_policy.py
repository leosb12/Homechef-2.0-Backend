from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("pedidos_checkout_pagos", "0005_alter_order_payment_method_and_orderpayment_method"),
    ]

    operations = [
        migrations.AddField(
            model_name="pickupconfirmation",
            name="no_show_marked_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="pickupconfirmation",
            name="pickup_grace_deadline",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="pickupconfirmation",
            name="pickup_no_show_flag",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="pickupconfirmation",
            name="pickup_retention_deadline",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="pickupconfirmation",
            name="pickup_window_end",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="pickupconfirmation",
            name="pickup_window_start",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="pickupconfirmation",
            name="selected_slot_end",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="pickupconfirmation",
            name="selected_slot_start",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name="pickupconfirmation",
            index=models.Index(fields=["pickup_no_show_flag", "pickup_grace_deadline"], name="order_pickup_pickup__3ee4b3_idx"),
        ),
    ]

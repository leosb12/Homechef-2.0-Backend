from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("pedidos_checkout_pagos", "0004_pickupconfirmation"),
    ]

    operations = [
        migrations.AlterField(
            model_name="order",
            name="payment_method",
            field=models.CharField(
                choices=[
                    ("cash", "Efectivo"),
                    ("stripe_test", "Stripe test"),
                    ("bitcoin_coingate", "Bitcoin CoinGate"),
                    ("qr_simulado", "QR simulado"),
                ],
                max_length=30,
            ),
        ),
        migrations.AlterField(
            model_name="orderpayment",
            name="method",
            field=models.CharField(
                choices=[
                    ("cash", "Efectivo"),
                    ("stripe_test", "Stripe test"),
                    ("bitcoin_coingate", "Bitcoin CoinGate"),
                    ("qr_simulado", "QR simulado"),
                ],
                max_length=30,
            ),
        ),
    ]

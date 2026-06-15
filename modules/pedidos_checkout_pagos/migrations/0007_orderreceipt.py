from django.db import migrations, models

import modules.pedidos_checkout_pagos.models


class Migration(migrations.Migration):

    dependencies = [
        ("pedidos_checkout_pagos", "0006_pickup_window_policy"),
    ]

    operations = [
        migrations.CreateModel(
            name="OrderReceipt",
            fields=[
                ("id", models.CharField(default=modules.pedidos_checkout_pagos.models.uuid4_string, editable=False, max_length=64, primary_key=True, serialize=False)),
                ("receipt_number", models.CharField(db_index=True, max_length=80, unique=True)),
                ("payment_method", models.CharField(choices=[("cash", "Efectivo"), ("stripe_test", "Stripe test"), ("bitcoin_coingate", "Bitcoin CoinGate"), ("qr_simulado", "QR simulado")], max_length=30)),
                ("payment_status", models.CharField(choices=[("CREATED", "Creado"), ("PENDING", "Pendiente"), ("PROCESSING", "Procesando"), ("CONFIRMED", "Confirmado"), ("FAILED", "Fallido"), ("CANCELLED", "Cancelado"), ("EXPIRED", "Expirado")], max_length=20)),
                ("order_status", models.CharField(choices=[("CART", "Carrito"), ("CHECKOUT_PENDING", "Checkout pendiente"), ("PENDING_PAYMENT", "Pendiente de pago"), ("PAYMENT_VALIDATING", "Validando pago"), ("PAYMENT_FAILED", "Pago fallido"), ("PAID", "Pagado"), ("AWAITING_CHEF_CONFIRMATION", "Esperando confirmacion del cocinero"), ("ACCEPTED", "Aceptado"), ("REJECTED", "Rechazado"), ("PREPARING", "En preparacion"), ("READY_FOR_PICKUP", "Listo para retiro"), ("READY_FOR_DELIVERY", "Listo para entrega"), ("OUT_FOR_DELIVERY", "En camino"), ("DELIVERED", "Entregado"), ("PICKED_UP", "Retirado"), ("CANCELLED", "Cancelado"), ("EXPIRED", "Expirado")], max_length=40)),
                ("currency", models.CharField(default="BOB", max_length=3)),
                ("subtotal", models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ("delivery_fee", models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ("service_fee", models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ("discount_total", models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ("total", models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ("external_reference", models.CharField(blank=True, max_length=255)),
                ("confirmed_at", models.DateTimeField(blank=True, null=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("issued_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("order", models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="receipts", to="pedidos_checkout_pagos.order")),
                ("payment", models.ForeignKey(blank=True, null=True, on_delete=models.deletion.SET_NULL, related_name="receipts", to="pedidos_checkout_pagos.orderpayment")),
            ],
            options={
                "db_table": "order_receipts",
            },
        ),
        migrations.AddIndex(
            model_name="orderreceipt",
            index=models.Index(fields=["order", "issued_at"], name="order_recei_order_i_131937_idx"),
        ),
        migrations.AddIndex(
            model_name="orderreceipt",
            index=models.Index(fields=["payment_method", "issued_at"], name="order_recei_payment_e2f31a_idx"),
        ),
        migrations.AddConstraint(
            model_name="orderreceipt",
            constraint=models.UniqueConstraint(fields=("order", "payment"), name="unique_receipt_per_order_payment"),
        ),
    ]

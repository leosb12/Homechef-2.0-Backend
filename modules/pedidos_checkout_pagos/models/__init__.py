from uuid import uuid4

from django.db import models

from modules.gestion_cocinero.models import Dish
from modules.gestion_usuarios_acceso_suscripcion.models import UserProfile


def uuid4_string():
    return str(uuid4())


class Cart(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Activo"
        CONVERTED = "CONVERTED", "Convertido"
        ABANDONED = "ABANDONED", "Abandonado"

    id = models.CharField(max_length=64, primary_key=True, default=uuid4_string, editable=False)
    client = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name="order_carts",
        limit_choices_to={"role": UserProfile.ROLE_CLIENT},
    )
    chef = models.ForeignKey(
        UserProfile,
        on_delete=models.PROTECT,
        related_name="client_carts_received",
        limit_choices_to={"role": UserProfile.ROLE_CHEF},
    )
    currency = models.CharField(max_length=3, default="BOB")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    converted_to_order = models.ForeignKey(
        "pedidos_checkout_pagos.Order",
        on_delete=models.SET_NULL,
        related_name="source_carts",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "order_carts"
        indexes = [
            models.Index(fields=["client", "status"]),
            models.Index(fields=["chef", "status"]),
            models.Index(fields=["updated_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["client", "chef"],
                condition=models.Q(status="ACTIVE"),
                name="unique_active_cart_per_client_chef",
            )
        ]

    def __str__(self):
        return f"{self.client_id} -> {self.chef_id} ({self.status})"


class CartItem(models.Model):
    id = models.CharField(max_length=64, primary_key=True, default=uuid4_string, editable=False)
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="items")
    dish = models.ForeignKey(Dish, on_delete=models.PROTECT, related_name="cart_items")
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    subtotal = models.DecimalField(max_digits=10, decimal_places=2)
    dish_name_snapshot = models.CharField(max_length=160)
    dish_image_url_snapshot = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "order_cart_items"
        indexes = [
            models.Index(fields=["cart", "dish"]),
            models.Index(fields=["updated_at"]),
        ]
        constraints = [
            models.UniqueConstraint(fields=["cart", "dish"], name="unique_cart_item_per_dish"),
        ]

    def __str__(self):
        return f"{self.cart_id} -> {self.dish_id} x{self.quantity}"


class Order(models.Model):
    class Status(models.TextChoices):
        CART = "CART", "Carrito"
        CHECKOUT_PENDING = "CHECKOUT_PENDING", "Checkout pendiente"
        PENDING_PAYMENT = "PENDING_PAYMENT", "Pendiente de pago"
        PAYMENT_VALIDATING = "PAYMENT_VALIDATING", "Validando pago"
        PAYMENT_FAILED = "PAYMENT_FAILED", "Pago fallido"
        PAID = "PAID", "Pagado"
        AWAITING_CHEF_CONFIRMATION = "AWAITING_CHEF_CONFIRMATION", "Esperando confirmacion del cocinero"
        ACCEPTED = "ACCEPTED", "Aceptado"
        REJECTED = "REJECTED", "Rechazado"
        PREPARING = "PREPARING", "En preparacion"
        READY_FOR_PICKUP = "READY_FOR_PICKUP", "Listo para retiro"
        READY_FOR_DELIVERY = "READY_FOR_DELIVERY", "Listo para entrega"
        OUT_FOR_DELIVERY = "OUT_FOR_DELIVERY", "En camino"
        DELIVERED = "DELIVERED", "Entregado"
        PICKED_UP = "PICKED_UP", "Retirado"
        CANCELLED = "CANCELLED", "Cancelado"
        EXPIRED = "EXPIRED", "Expirado"

    class FulfillmentType(models.TextChoices):
        PICKUP = "pickup", "Retiro"
        DELIVERY = "delivery", "Delivery"

    class PaymentMethod(models.TextChoices):
        CASH = "cash", "Efectivo"
        STRIPE_TEST = "stripe_test", "Stripe test"
        BITCOIN_COINGATE = "bitcoin_coingate", "Bitcoin CoinGate"
        QR_SIMULATED = "qr_simulado", "QR simulado"

    id = models.CharField(max_length=64, primary_key=True, default=uuid4_string, editable=False)
    client = models.ForeignKey(
        UserProfile,
        on_delete=models.PROTECT,
        related_name="orders_as_client",
        limit_choices_to={"role": UserProfile.ROLE_CLIENT},
    )
    chef = models.ForeignKey(
        UserProfile,
        on_delete=models.PROTECT,
        related_name="orders_as_chef",
        limit_choices_to={"role": UserProfile.ROLE_CHEF},
    )
    source_cart = models.ForeignKey(
        Cart,
        on_delete=models.SET_NULL,
        related_name="generated_orders",
        null=True,
        blank=True,
    )
    status = models.CharField(max_length=40, choices=Status.choices, default=Status.PENDING_PAYMENT)
    fulfillment_type = models.CharField(max_length=20, choices=FulfillmentType.choices)
    payment_method = models.CharField(max_length=30, choices=PaymentMethod.choices)
    currency = models.CharField(max_length=3, default="BOB")
    subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    delivery_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    service_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    discount_total = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    notes = models.TextField(blank=True)
    stock_reserved = models.BooleanField(default=False)
    last_status_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    cancelled_reason = models.CharField(max_length=255, blank=True)
    cancelled_by_role = models.CharField(max_length=30, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "orders"
        indexes = [
            models.Index(fields=["client", "status"]),
            models.Index(fields=["chef", "status"]),
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["fulfillment_type", "status"]),
            models.Index(fields=["payment_method", "status"]),
            models.Index(fields=["last_status_at"]),
        ]

    def __str__(self):
        return f"{self.id} - {self.status}"


class OrderAddress(models.Model):
    id = models.CharField(max_length=64, primary_key=True, default=uuid4_string, editable=False)
    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name="address")
    label = models.CharField(max_length=80, blank=True)
    contact_name = models.CharField(max_length=120, blank=True)
    contact_phone = models.CharField(max_length=30, blank=True)
    line_1 = models.CharField(max_length=255)
    reference = models.CharField(max_length=255, blank=True)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "order_addresses"
        indexes = [
            models.Index(fields=["latitude", "longitude"]),
        ]

    def __str__(self):
        return f"{self.order_id} - {self.line_1}"


class OrderItem(models.Model):
    class StockSourceType(models.TextChoices):
        DISH = "dish", "Plato"
        DAILY_MENU_ITEM = "daily_menu_item", "Menu del dia"

    id = models.CharField(max_length=64, primary_key=True, default=uuid4_string, editable=False)
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    dish = models.ForeignKey(Dish, on_delete=models.PROTECT, related_name="order_items")
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    subtotal = models.DecimalField(max_digits=10, decimal_places=2)
    dish_name_snapshot = models.CharField(max_length=160)
    dish_description_snapshot = models.TextField(blank=True)
    dish_image_url_snapshot = models.TextField(blank=True)
    stock_source_type = models.CharField(max_length=30, choices=StockSourceType.choices, blank=True)
    stock_source_ref = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "order_items"
        indexes = [
            models.Index(fields=["order", "dish"]),
            models.Index(fields=["updated_at"]),
        ]

    def __str__(self):
        return f"{self.order_id} -> {self.dish_id} x{self.quantity}"


class OrderStatusHistory(models.Model):
    id = models.CharField(max_length=64, primary_key=True, default=uuid4_string, editable=False)
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="status_history")
    from_status = models.CharField(max_length=40, blank=True)
    to_status = models.CharField(max_length=40)
    actor_role = models.CharField(max_length=30)
    actor_id = models.CharField(max_length=64, blank=True)
    notes = models.CharField(max_length=255, blank=True)
    occurred_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "order_status_history"
        indexes = [
            models.Index(fields=["order", "occurred_at"]),
            models.Index(fields=["to_status", "occurred_at"]),
        ]

    def __str__(self):
        return f"{self.order_id}: {self.from_status} -> {self.to_status}"


class OrderTimelineEvent(models.Model):
    id = models.CharField(max_length=64, primary_key=True, default=uuid4_string, editable=False)
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="timeline_events")
    event_code = models.CharField(max_length=60)
    event_label = models.CharField(max_length=160)
    actor_role = models.CharField(max_length=30)
    actor_id = models.CharField(max_length=64, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "order_timeline_events"
        indexes = [
            models.Index(fields=["order", "occurred_at"]),
            models.Index(fields=["event_code", "occurred_at"]),
        ]

    def __str__(self):
        return f"{self.order_id}: {self.event_code}"


class OrderPayment(models.Model):
    class Status(models.TextChoices):
        CREATED = "CREATED", "Creado"
        PENDING = "PENDING", "Pendiente"
        PROCESSING = "PROCESSING", "Procesando"
        CONFIRMED = "CONFIRMED", "Confirmado"
        FAILED = "FAILED", "Fallido"
        CANCELLED = "CANCELLED", "Cancelado"
        EXPIRED = "EXPIRED", "Expirado"

    id = models.CharField(max_length=64, primary_key=True, default=uuid4_string, editable=False)
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="payments")
    method = models.CharField(max_length=30, choices=Order.PaymentMethod.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.CREATED)
    currency = models.CharField(max_length=3, default="BOB")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    provider = models.CharField(max_length=40, blank=True)
    payment_url = models.TextField(blank=True)
    external_reference = models.CharField(max_length=255, blank=True, db_index=True)
    qr_session_code = models.CharField(max_length=120, blank=True, db_index=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    confirmed_by_role = models.CharField(max_length=30, blank=True)
    confirmed_by_user = models.ForeignKey(
        UserProfile,
        on_delete=models.SET_NULL,
        related_name="confirmed_order_payments",
        null=True,
        blank=True,
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)
    failure_reason = models.CharField(max_length=255, blank=True)
    provider_payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "order_payments"
        indexes = [
            models.Index(fields=["order", "status"]),
            models.Index(fields=["method", "status"]),
            models.Index(fields=["provider", "status"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"{self.order_id} - {self.method} - {self.status}"


class OrderPaymentEvent(models.Model):
    id = models.CharField(max_length=64, primary_key=True, default=uuid4_string, editable=False)
    payment = models.ForeignKey(OrderPayment, on_delete=models.CASCADE, related_name="events")
    event_code = models.CharField(max_length=60)
    event_label = models.CharField(max_length=160)
    actor_role = models.CharField(max_length=30)
    actor_id = models.CharField(max_length=64, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "order_payment_events"
        indexes = [
            models.Index(fields=["payment", "occurred_at"]),
            models.Index(fields=["event_code", "occurred_at"]),
        ]

    def __str__(self):
        return f"{self.payment_id}: {self.event_code}"


class OrderReceipt(models.Model):
    id = models.CharField(max_length=64, primary_key=True, default=uuid4_string, editable=False)
    order = models.ForeignKey("pedidos_checkout_pagos.Order", on_delete=models.CASCADE, related_name="receipts")
    payment = models.ForeignKey(
        "pedidos_checkout_pagos.OrderPayment",
        on_delete=models.SET_NULL,
        related_name="receipts",
        null=True,
        blank=True,
    )
    receipt_number = models.CharField(max_length=80, unique=True, db_index=True)
    payment_method = models.CharField(max_length=30, choices=Order.PaymentMethod.choices)
    payment_status = models.CharField(max_length=20, choices=OrderPayment.Status.choices)
    order_status = models.CharField(max_length=40, choices=Order.Status.choices)
    currency = models.CharField(max_length=3, default="BOB")
    subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    delivery_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    service_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    discount_total = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    external_reference = models.CharField(max_length=255, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    issued_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "order_receipts"
        indexes = [
            models.Index(fields=["order", "issued_at"]),
            models.Index(fields=["payment_method", "issued_at"]),
        ]
        constraints = [
            models.UniqueConstraint(fields=["order", "payment"], name="unique_receipt_per_order_payment"),
        ]

    def __str__(self):
        return f"{self.receipt_number} - {self.order_id}"


class SimulatedQRPaymentSession(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pendiente"
        PROCESSING = "PROCESSING", "Procesando"
        CONFIRMED = "CONFIRMED", "Confirmado"
        CANCELLED = "CANCELLED", "Cancelado"
        EXPIRED = "EXPIRED", "Expirado"
        INVALIDATED = "INVALIDATED", "Invalidado"

    id = models.CharField(max_length=64, primary_key=True, default=uuid4_string, editable=False)
    payment = models.ForeignKey(OrderPayment, on_delete=models.CASCADE, related_name="qr_sessions")
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="qr_sessions")
    session_code = models.CharField(max_length=120, unique=True, db_index=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    bank_name = models.CharField(max_length=120, default="Banco HomeChef")
    bank_account_label = models.CharField(max_length=120, default="Cuenta QR simulada")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default="BOB")
    expires_at = models.DateTimeField()
    started_at = models.DateTimeField(null=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    invalidated_at = models.DateTimeField(null=True, blank=True)
    invalidated_reason = models.CharField(max_length=255, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "order_simulated_qr_sessions"
        indexes = [
            models.Index(fields=["payment", "status"]),
            models.Index(fields=["order", "status"]),
            models.Index(fields=["expires_at"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"{self.order_id} - {self.session_code} - {self.status}"


class PickupConfirmation(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pendiente"
        CONFIRMED = "CONFIRMED", "Confirmado"
        CANCELLED = "CANCELLED", "Cancelado"

    id = models.CharField(max_length=64, primary_key=True, default=uuid4_string, editable=False)
    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name="pickup_confirmation")
    pickup_code = models.CharField(max_length=12, unique=True, db_index=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    pickup_instructions = models.CharField(max_length=255, default="Presenta este codigo al cocinero para retirar tu pedido.")
    pickup_schedule_note = models.CharField(max_length=255, blank=True)
    selected_slot_start = models.DateTimeField(null=True, blank=True)
    selected_slot_end = models.DateTimeField(null=True, blank=True)
    pickup_window_start = models.DateTimeField(null=True, blank=True)
    pickup_window_end = models.DateTimeField(null=True, blank=True)
    pickup_grace_deadline = models.DateTimeField(null=True, blank=True)
    pickup_retention_deadline = models.DateTimeField(null=True, blank=True)
    pickup_no_show_flag = models.BooleanField(default=False)
    no_show_marked_at = models.DateTimeField(null=True, blank=True)
    retention_extension_count = models.PositiveSmallIntegerField(default=0)
    last_retention_extension_at = models.DateTimeField(null=True, blank=True)
    confirmed_by_role = models.CharField(max_length=30, blank=True)
    confirmed_by_user = models.ForeignKey(
        UserProfile,
        on_delete=models.SET_NULL,
        related_name="confirmed_pickup_orders",
        null=True,
        blank=True,
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "order_pickup_confirmations"
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["confirmed_at"]),
            models.Index(fields=["pickup_no_show_flag", "pickup_grace_deadline"]),
        ]

    def __str__(self):
        return f"{self.order_id} - {self.pickup_code} - {self.status}"

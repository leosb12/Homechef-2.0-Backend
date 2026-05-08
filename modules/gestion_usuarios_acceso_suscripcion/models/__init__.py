from django.db import models
from django.db.models import Q


class UserProfile(models.Model):
    ROLE_CLIENT = "CLIENTE"
    ROLE_CHEF = "COCINERO"
    ROLE_ADMIN = "ADMINISTRADOR"
    ROLE_DELIVERY = "REPARTIDOR"

    ROLE_CHOICES = (
        (ROLE_CLIENT, "Cliente"),
        (ROLE_CHEF, "Cocinero"),
        (ROLE_ADMIN, "Administrador"),
        (ROLE_DELIVERY, "Repartidor"),
    )

    supabase_user_id = models.UUIDField(unique=True, db_index=True)
    email = models.EmailField(unique=True)
    first_name = models.CharField(max_length=100, blank=True)
    last_name = models.CharField(max_length=100, blank=True)
    full_name = models.CharField(max_length=255, blank=True)
    avatar_url = models.URLField(max_length=1000, blank=True)
    role = models.CharField(max_length=30, choices=ROLE_CHOICES, default=ROLE_CLIENT)
    phone = models.CharField(max_length=30, blank=True)
    address = models.CharField(max_length=255, blank=True)
    accept_terms = models.BooleanField(default=False)
    notify_gmail = models.BooleanField(default=True)
    notify_push = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    legacy_mongo_id = models.CharField(max_length=128, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "user_profiles"
        indexes = [
            models.Index(fields=["role"]),
            models.Index(fields=["email"]),
        ]

    def __str__(self):
        return self.email


class AuditEvent(models.Model):
    event = models.CharField(max_length=120)
    details = models.JSONField(default=dict, blank=True)
    at = models.DateTimeField()

    class Meta:
        db_table = "audit_events"
        indexes = [
            models.Index(fields=["event"]),
            models.Index(fields=["at"]),
        ]

    def __str__(self):
        return f"{self.event} at {self.at}"


class AISubscriptionPlan(models.Model):
    class Status(models.TextChoices):
        AVAILABLE = "AVAILABLE", "Disponible"
        UNAVAILABLE = "UNAVAILABLE", "No disponible"
        DISABLED = "DISABLED", "Deshabilitado"

    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default="BOB")
    duration_days = models.PositiveIntegerField(default=30)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.AVAILABLE)
    ai_query_limit = models.PositiveIntegerField(default=0)
    ai_generation_limit = models.PositiveIntegerField(default=0)
    vision_enabled = models.BooleanField(default=False)
    production_recommendations_enabled = models.BooleanField(default=False)
    pricing_support_enabled = models.BooleanField(default=False)
    publishing_support_enabled = models.BooleanField(default=False)
    benefits = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ai_subscription_plans"
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return self.name


class ChefAISubscription(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Activa"
        CANCELLED = "CANCELLED", "Cancelada"
        PENDING_PAYMENT = "PENDING_PAYMENT", "Pendiente de pago"
        EXPIRED = "EXPIRED", "Vencida"
        SUSPENDED = "SUSPENDED", "Suspendida"

    chef_profile = models.ForeignKey(
        "gestion_cocinero.ChefProfile",
        on_delete=models.PROTECT,
        related_name="ai_subscriptions",
    )
    plan = models.ForeignKey(AISubscriptionPlan, on_delete=models.PROTECT, related_name="subscriptions")
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.PENDING_PAYMENT)
    start_date = models.DateTimeField(null=True, blank=True)
    end_date = models.DateTimeField(null=True, blank=True)
    cancel_at_period_end = models.BooleanField(default=False)
    cancellation_requested_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    auto_renew = models.BooleanField(default=True)
    preferred_payment_provider = models.CharField(max_length=20, default="STRIPE_SANDBOX")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "chef_ai_subscriptions"
        constraints = [
            models.UniqueConstraint(
                fields=["chef_profile"],
                condition=Q(status="ACTIVE"),
                name="unique_active_ai_subscription_per_chef",
            )
        ]
        indexes = [
            models.Index(fields=["chef_profile"]),
            models.Index(fields=["status"]),
            models.Index(fields=["plan"]),
            models.Index(fields=["preferred_payment_provider"]),
            models.Index(fields=["created_at"]),
            models.Index(fields=["end_date"]),
        ]

    def __str__(self):
        return f"{self.chef_profile_id} - {self.plan_id} - {self.status}"


class AIPaymentMethod(models.Model):
    class Provider(models.TextChoices):
        STRIPE_SANDBOX = "STRIPE_SANDBOX", "Stripe Sandbox"
        COINGATE_SANDBOX = "COINGATE_SANDBOX", "CoinGate Sandbox"

    class Type(models.TextChoices):
        MOCK = "MOCK", "Mock"
        CARD = "CARD", "Tarjeta"
        WALLET = "WALLET", "Wallet"

    chef_profile = models.ForeignKey(
        "gestion_cocinero.ChefProfile",
        on_delete=models.CASCADE,
        related_name="ai_payment_methods",
    )
    provider = models.CharField(max_length=20, choices=Provider.choices)
    type = models.CharField(max_length=20, choices=Type.choices, default=Type.CARD)
    external_reference = models.CharField(max_length=255, blank=True)
    public_identifier = models.CharField(max_length=120, blank=True)
    wallet_public_address = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ai_payment_methods"
        indexes = [
            models.Index(fields=["chef_profile"]),
            models.Index(fields=["provider"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"{self.provider} - {self.public_identifier or self.id}"


class AISubscriptionPayment(models.Model):
    class Status(models.TextChoices):
        APPROVED = "APPROVED", "Aprobado"
        REJECTED = "REJECTED", "Rechazado"
        PENDING = "PENDING", "Pendiente"
        ERROR = "ERROR", "Error"

    subscription = models.ForeignKey(
        ChefAISubscription,
        on_delete=models.PROTECT,
        related_name="payments",
        null=True,
        blank=True,
    )
    chef_profile = models.ForeignKey(
        "gestion_cocinero.ChefProfile",
        on_delete=models.PROTECT,
        related_name="ai_subscription_payments",
    )
    plan = models.ForeignKey(AISubscriptionPlan, on_delete=models.PROTECT, related_name="payments")
    payment_method = models.ForeignKey(
        AIPaymentMethod,
        on_delete=models.SET_NULL,
        related_name="payments",
        null=True,
        blank=True,
    )
    provider = models.CharField(max_length=20)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3)
    status = models.CharField(max_length=20, choices=Status.choices)
    external_reference = models.CharField(max_length=255, blank=True)
    crypto_transaction_hash = models.CharField(max_length=255, blank=True)
    provider_response = models.JSONField(default=dict, blank=True)
    rejection_reason = models.CharField(max_length=255, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ai_subscription_payments"
        indexes = [
            models.Index(fields=["chef_profile"]),
            models.Index(fields=["status"]),
            models.Index(fields=["plan"]),
            models.Index(fields=["provider"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"{self.provider} {self.amount} {self.currency} - {self.status}"


class AISubscriptionAuditLog(models.Model):
    class Action(models.TextChoices):
        PLAN_VIEWED = "PLAN_VIEWED", "Plan visto"
        SUBSCRIPTION_STATUS_VIEWED = "SUBSCRIPTION_STATUS_VIEWED", "Estado visto"
        SUBSCRIPTION_SUMMARY_GENERATED = "SUBSCRIPTION_SUMMARY_GENERATED", "Resumen generado"
        SUBSCRIPTION_CREATED = "SUBSCRIPTION_CREATED", "Suscripcion creada"
        SUBSCRIPTION_ACTIVATED = "SUBSCRIPTION_ACTIVATED", "Suscripcion activada"
        PLAN_CHANGED = "PLAN_CHANGED", "Plan cambiado"
        CANCELLATION_REQUESTED = "CANCELLATION_REQUESTED", "Cancelacion solicitada"
        CANCELLATION_CONFIRMED = "CANCELLATION_CONFIRMED", "Cancelacion confirmada"
        RENEWAL_REQUESTED = "RENEWAL_REQUESTED", "Renovacion solicitada"
        RENEWAL_FAILED = "RENEWAL_FAILED", "Renovacion fallida"
        PAYMENT_APPROVED = "PAYMENT_APPROVED", "Pago aprobado"
        PAYMENT_REJECTED = "PAYMENT_REJECTED", "Pago rechazado"
        AI_ACCESS_VALIDATED = "AI_ACCESS_VALIDATED", "Acceso IA validado"
        AI_ACCESS_DENIED = "AI_ACCESS_DENIED", "Acceso IA denegado"

    chef_profile = models.ForeignKey(
        "gestion_cocinero.ChefProfile",
        on_delete=models.PROTECT,
        related_name="ai_subscription_audit_logs",
    )
    subscription = models.ForeignKey(
        ChefAISubscription,
        on_delete=models.SET_NULL,
        related_name="audit_logs",
        null=True,
        blank=True,
    )
    action = models.CharField(max_length=60, choices=Action.choices)
    description = models.CharField(max_length=255)
    metadata = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ai_subscription_audit_logs"
        indexes = [
            models.Index(fields=["chef_profile"]),
            models.Index(fields=["subscription"]),
            models.Index(fields=["action"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"{self.action} - {self.chef_profile_id}"

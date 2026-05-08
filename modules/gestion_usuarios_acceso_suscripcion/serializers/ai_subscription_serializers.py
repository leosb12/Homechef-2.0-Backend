from rest_framework import serializers

from ..models import AISubscriptionAuditLog, AISubscriptionPayment, AISubscriptionPlan, ChefAISubscription

SANDBOX_PAYMENT_PROVIDERS = ("STRIPE_SANDBOX", "COINGATE_SANDBOX")


class AISubscriptionPlanSerializer(serializers.ModelSerializer):
    nombre = serializers.CharField(source="name", read_only=True)
    descripcion = serializers.CharField(source="description", read_only=True)
    precio = serializers.DecimalField(source="price", max_digits=10, decimal_places=2, read_only=True)
    funciones = serializers.JSONField(source="benefits", read_only=True)

    class Meta:
        model = AISubscriptionPlan
        fields = (
            "id",
            "nombre",
            "descripcion",
            "precio",
            "funciones",
            "name",
            "description",
            "price",
            "currency",
            "duration_days",
            "status",
            "ai_query_limit",
            "ai_generation_limit",
            "vision_enabled",
            "production_recommendations_enabled",
            "pricing_support_enabled",
            "publishing_support_enabled",
            "benefits",
        )


class ChefAISubscriptionSerializer(serializers.ModelSerializer):
    plan = AISubscriptionPlanSerializer()

    class Meta:
        model = ChefAISubscription
        fields = (
            "id",
            "plan",
            "status",
            "start_date",
            "end_date",
            "cancel_at_period_end",
            "cancellation_requested_at",
            "cancelled_at",
            "auto_renew",
            "preferred_payment_provider",
        )


class SubscriptionSummaryRequestSerializer(serializers.Serializer):
    plan_id = serializers.IntegerField(min_value=1)
    operation = serializers.ChoiceField(choices=("subscribe", "change_plan", "renew"))


class SubscribeRequestSerializer(serializers.Serializer):
    plan_id = serializers.IntegerField(min_value=1)
    payment_provider = serializers.ChoiceField(choices=SANDBOX_PAYMENT_PROVIDERS)
    payment_method_id = serializers.IntegerField(required=False, allow_null=True)


class ChangePlanRequestSerializer(SubscribeRequestSerializer):
    new_plan_id = serializers.IntegerField(min_value=1)
    plan_id = None


class RenewRequestSerializer(serializers.Serializer):
    payment_provider = serializers.ChoiceField(choices=SANDBOX_PAYMENT_PROVIDERS)
    payment_method_id = serializers.IntegerField(required=False, allow_null=True)


class CancelSubscriptionRequestSerializer(serializers.Serializer):
    cancel_at_period_end = serializers.BooleanField(default=True)
    reason = serializers.CharField(required=False, allow_blank=True, max_length=255)


class PaymentReturnConfirmSerializer(serializers.Serializer):
    provider = serializers.ChoiceField(choices=SANDBOX_PAYMENT_PROVIDERS, required=False)
    stripe_session_id = serializers.CharField(required=False, allow_blank=True, max_length=255)
    coingate_order_id = serializers.CharField(required=False, allow_blank=True, max_length=255)
    trust_provider_return = serializers.BooleanField(default=False)
    trust_sandbox_return = serializers.BooleanField(default=False)


class AISubscriptionPaymentSerializer(serializers.ModelSerializer):
    plan = AISubscriptionPlanSerializer()

    class Meta:
        model = AISubscriptionPayment
        fields = (
            "id",
            "subscription_id",
            "plan",
            "provider",
            "amount",
            "currency",
            "status",
            "external_reference",
            "crypto_transaction_hash",
            "rejection_reason",
            "paid_at",
            "created_at",
        )


class AISubscriptionAuditLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AISubscriptionAuditLog
        fields = (
            "id",
            "subscription_id",
            "action",
            "description",
            "metadata",
            "ip_address",
            "user_agent",
            "created_at",
        )

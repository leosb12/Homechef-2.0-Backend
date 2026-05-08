from django.contrib import admin

from .models import (
    AIPaymentMethod,
    AISubscriptionAuditLog,
    AISubscriptionPayment,
    AISubscriptionPlan,
    ChefAISubscription,
    UsoIA,
)


@admin.register(AISubscriptionPlan)
class AISubscriptionPlanAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "price", "currency", "duration_days", "status", "created_at")
    list_filter = ("status", "currency")
    search_fields = ("name", "description")


@admin.register(ChefAISubscription)
class ChefAISubscriptionAdmin(admin.ModelAdmin):
    list_display = ("id", "chef_profile", "plan", "status", "start_date", "end_date", "preferred_payment_provider")
    list_filter = ("status", "preferred_payment_provider", "auto_renew")
    search_fields = ("chef_profile__business_name", "chef_profile__user__email", "plan__name")


@admin.register(AIPaymentMethod)
class AIPaymentMethodAdmin(admin.ModelAdmin):
    list_display = ("id", "chef_profile", "provider", "type", "public_identifier", "is_active", "created_at")
    list_filter = ("provider", "type", "is_active")
    search_fields = ("public_identifier", "external_reference", "wallet_public_address")


@admin.register(AISubscriptionPayment)
class AISubscriptionPaymentAdmin(admin.ModelAdmin):
    list_display = ("id", "chef_profile", "plan", "provider", "amount", "currency", "status", "paid_at", "created_at")
    list_filter = ("provider", "status", "currency")
    search_fields = ("external_reference", "crypto_transaction_hash", "chef_profile__user__email")


@admin.register(AISubscriptionAuditLog)
class AISubscriptionAuditLogAdmin(admin.ModelAdmin):
    list_display = ("id", "chef_profile", "subscription", "action", "created_at")
    list_filter = ("action",)
    search_fields = ("description", "chef_profile__user__email")


@admin.register(UsoIA)
class UsoIAAdmin(admin.ModelAdmin):
    list_display = ("id", "usuario", "funcion", "permitido", "codigo_resultado", "fecha_intento")
    list_filter = ("permitido", "codigo_resultado", "funcion")
    search_fields = ("usuario__email", "funcion", "codigo_resultado", "mensaje_resultado")

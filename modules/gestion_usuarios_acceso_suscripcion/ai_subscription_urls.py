from django.urls import path

from .views import (
    audit_log,
    available_plans,
    can_use_ai,
    cancel,
    change_plan,
    coingate_callback,
    payment_history,
    renew,
    subscribe,
    stripe_webhook,
    subscription_status,
    subscription_summary,
)

urlpatterns = [
    path("plans/", available_plans),
    path("subscription/status/", subscription_status),
    path("subscription/summary/", subscription_summary),
    path("subscription/subscribe/", subscribe),
    path("subscription/change-plan/", change_plan),
    path("subscription/renew/", renew),
    path("subscription/cancel/", cancel),
    path("subscription/payments/", payment_history),
    path("subscription/payments/stripe/webhook/", stripe_webhook),
    path("subscription/payments/coingate/callback/", coingate_callback),
    path("subscription/audit-log/", audit_log),
    path("subscription/can-use-ai/", can_use_ai),
]

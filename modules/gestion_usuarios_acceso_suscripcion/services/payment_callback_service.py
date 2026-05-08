from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from ..models import AISubscriptionAuditLog, AISubscriptionPayment, ChefAISubscription
from .audit_service import AISubscriptionAuditService


class PaymentCallbackService:
    COINGATE_APPROVED = {"paid", "confirmed"}
    COINGATE_PENDING = {"new", "pending", "confirming"}
    COINGATE_REJECTED = {"invalid", "expired", "canceled", "refunded"}

    def __init__(self):
        self.audit = AISubscriptionAuditService()

    @transaction.atomic
    def approve_payment(self, payment_id=None, external_reference=None, provider_response=None):
        payment = self._lock_payment(payment_id=payment_id, external_reference=external_reference)
        if not payment:
            return False
        subscription = payment.subscription
        chef_profile = payment.chef_profile
        now = timezone.now()

        payment.status = AISubscriptionPayment.Status.APPROVED
        payment.paid_at = now
        payment.provider_response = self._merge_response(payment.provider_response, provider_response)
        payment.rejection_reason = ""
        payment.save(update_fields=["status", "paid_at", "provider_response", "rejection_reason"])

        self._cancel_replaced_subscription(payment, now)
        subscription.status = ChefAISubscription.Status.ACTIVE
        subscription.start_date = now
        subscription.end_date = now + timedelta(days=payment.plan.duration_days)
        subscription.cancel_at_period_end = False
        subscription.cancellation_requested_at = None
        subscription.cancelled_at = None
        subscription.save()

        if not chef_profile.ai_subscription_active:
            chef_profile.ai_subscription_active = True
            chef_profile.save(update_fields=["ai_subscription_active", "updated_at"])

        self.audit.log(
            chef_profile=chef_profile,
            subscription=subscription,
            action=AISubscriptionAuditLog.Action.PAYMENT_APPROVED,
            description="Pago de suscripcion IA aprobado por proveedor",
            metadata={"payment_id": payment.id, "provider": payment.provider},
        )
        self.audit.log(
            chef_profile=chef_profile,
            subscription=subscription,
            action=AISubscriptionAuditLog.Action.SUBSCRIPTION_ACTIVATED,
            description="Suscripcion IA activada por webhook/callback",
            metadata={"payment_id": payment.id, "provider": payment.provider},
        )
        return True

    @transaction.atomic
    def reject_payment(self, payment_id=None, external_reference=None, provider_response=None, reason="", error=False):
        payment = self._lock_payment(payment_id=payment_id, external_reference=external_reference)
        if not payment:
            return False
        payment.status = AISubscriptionPayment.Status.ERROR if error else AISubscriptionPayment.Status.REJECTED
        payment.provider_response = self._merge_response(payment.provider_response, provider_response)
        payment.rejection_reason = reason[:255]
        payment.save(update_fields=["status", "provider_response", "rejection_reason"])

        subscription = payment.subscription
        if subscription and subscription.status == ChefAISubscription.Status.PENDING_PAYMENT:
            subscription.status = ChefAISubscription.Status.EXPIRED if not error else ChefAISubscription.Status.SUSPENDED
            subscription.save(update_fields=["status", "updated_at"])

        has_active = ChefAISubscription.objects.filter(
            chef_profile=payment.chef_profile,
            status=ChefAISubscription.Status.ACTIVE,
            end_date__gt=timezone.now(),
        ).exists()
        if payment.chef_profile.ai_subscription_active != has_active:
            payment.chef_profile.ai_subscription_active = has_active
            payment.chef_profile.save(update_fields=["ai_subscription_active", "updated_at"])

        self.audit.log(
            chef_profile=payment.chef_profile,
            subscription=subscription,
            action=AISubscriptionAuditLog.Action.PAYMENT_REJECTED,
            description="Pago de suscripcion IA rechazado por proveedor",
            metadata={"payment_id": payment.id, "provider": payment.provider, "reason": reason},
        )
        return True

    @transaction.atomic
    def mark_pending(self, *, external_reference=None, order_id=None, provider_response=None):
        payment = self._lock_coingate_payment(external_reference=external_reference, order_id=order_id)
        if not payment:
            return False
        payment.status = AISubscriptionPayment.Status.PENDING
        payment.provider_response = self._merge_response(payment.provider_response, provider_response)
        payment.save(update_fields=["status", "provider_response"])
        return True

    def handle_coingate_callback(self, payload):
        status = str(payload.get("status") or "").lower()
        external_reference = str(payload.get("id") or "").strip()
        order_id = str(payload.get("order_id") or "").strip()
        payment = self._find_coingate_payment(external_reference=external_reference, order_id=order_id)
        if not payment:
            return False
        if status in self.COINGATE_APPROVED:
            return self.approve_payment(payment_id=payment.id, provider_response=payload)
        if status in self.COINGATE_PENDING:
            return self.mark_pending(external_reference=external_reference, order_id=order_id, provider_response=payload)
        if status in self.COINGATE_REJECTED:
            return self.reject_payment(
                payment_id=payment.id,
                provider_response=payload,
                reason=f"CoinGate {status}",
                error=status == "invalid",
            )
        return self.reject_payment(payment_id=payment.id, provider_response=payload, reason=f"CoinGate {status}", error=True)

    def _lock_payment(self, payment_id=None, external_reference=None):
        queryset = AISubscriptionPayment.objects.select_for_update()
        if payment_id:
            return queryset.filter(id=payment_id).first()
        if external_reference:
            return queryset.filter(external_reference=external_reference).first()
        return None

    def _find_coingate_payment(self, *, external_reference=None, order_id=None):
        query = Q()
        if external_reference:
            query |= Q(external_reference=external_reference)
        if order_id:
            query |= Q(external_reference=order_id) | Q(provider_response__homechef_order_id=order_id) | Q(provider_response__order_id=order_id)
        if not query:
            return None
        return AISubscriptionPayment.objects.filter(query).first()

    def _lock_coingate_payment(self, *, external_reference=None, order_id=None):
        payment = self._find_coingate_payment(external_reference=external_reference, order_id=order_id)
        return self._lock_payment(payment_id=payment.id) if payment else None

    def _cancel_replaced_subscription(self, payment, now):
        previous_id = payment.provider_response.get("replace_subscription_id")
        if not previous_id:
            return
        ChefAISubscription.objects.filter(id=previous_id, status=ChefAISubscription.Status.ACTIVE).update(
            status=ChefAISubscription.Status.CANCELLED,
            cancelled_at=now,
            updated_at=now,
        )

    def _merge_response(self, current, incoming):
        data = dict(current or {})
        if incoming:
            data["callback"] = incoming
        return data

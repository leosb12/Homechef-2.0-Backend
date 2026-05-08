from decimal import Decimal

import stripe
from django.conf import settings

from .base import PaymentProvider, PaymentResult


class StripeSandboxPaymentProvider(PaymentProvider):
    provider = "STRIPE_SANDBOX"

    def create_payment(self, *, payment, subscription, plan, payload=None):
        if not settings.STRIPE_SECRET_KEY or not settings.STRIPE_SUCCESS_URL or not settings.STRIPE_CANCEL_URL:
            return PaymentResult(
                status="ERROR",
                provider=self.provider,
                rejection_reason="Stripe Sandbox no esta configurado",
                provider_response={"configured": False},
            )

        stripe.api_key = settings.STRIPE_SECRET_KEY
        try:
            metadata = {
                "payment_id": str(payment.id),
                "subscription_id": str(subscription.id),
                "chef_profile_id": str(subscription.chef_profile_id),
                "plan_id": str(plan.id),
                "provider": self.provider,
            }
            session = stripe.checkout.Session.create(
                mode="payment",
                payment_method_types=["card"],
                success_url=settings.STRIPE_SUCCESS_URL,
                cancel_url=settings.STRIPE_CANCEL_URL,
                line_items=[
                    {
                        "price_data": {
                            "currency": "usd",
                            "unit_amount": int(Decimal(plan.price) * 100),
                            "product_data": {
                                "name": plan.name,
                                "description": plan.description or f"Suscripcion IA HomeChef - {plan.name}",
                            },
                        },
                        "quantity": 1,
                    }
                ],
                metadata=metadata,
                payment_intent_data={"metadata": metadata},
            )
        except stripe.error.StripeError as exc:
            return PaymentResult(
                status="ERROR",
                provider=self.provider,
                rejection_reason="Stripe rechazo la creacion del checkout",
                provider_response={"error": str(exc)},
            )

        return PaymentResult(
            status="PENDING",
            provider=self.provider,
            external_reference=session.id,
            payment_url=session.url,
            provider_response={
                "id": session.id,
                "url": session.url,
                "mode": session.mode,
                "payment_status": session.payment_status,
                "metadata": metadata,
            },
        )

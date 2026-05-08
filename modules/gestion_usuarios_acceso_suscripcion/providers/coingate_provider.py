from uuid import uuid4

import requests
from django.conf import settings

from .base import PaymentProvider, PaymentResult


class CoinGateSandboxPaymentProvider(PaymentProvider):
    provider = "COINGATE_SANDBOX"

    def create_payment(self, *, payment, subscription, plan, payload=None):
        if not settings.COINGATE_API_BASE_URL or not settings.COINGATE_API_TOKEN:
            return PaymentResult(
                status="ERROR",
                provider=self.provider,
                rejection_reason="CoinGate Sandbox no esta configurado",
                provider_response={"configured": False},
            )

        order_id = f"homechef-ai-{payment.id}-{uuid4().hex}"
        body = {
            "order_id": order_id,
            "price_amount": str(plan.price),
            "price_currency": "USD",
            "receive_currency": settings.COINGATE_RECEIVE_CURRENCY,
            "callback_url": settings.COINGATE_CALLBACK_URL,
            "success_url": settings.COINGATE_SUCCESS_URL,
            "cancel_url": settings.COINGATE_CANCEL_URL,
            "title": f"Suscripcion IA HomeChef - {plan.name}",
            "description": "Pago de suscripcion IA HomeChef",
        }
        headers = {
            "Authorization": f"Bearer {settings.COINGATE_API_TOKEN}",
            "Content-Type": "application/json",
        }

        try:
            response = requests.post(
                f"{settings.COINGATE_API_BASE_URL.rstrip('/')}/orders",
                json=body,
                headers=headers,
                timeout=15,
            )
            response_data = response.json()
        except requests.RequestException as exc:
            return PaymentResult(
                status="ERROR",
                provider=self.provider,
                rejection_reason="No se pudo crear la orden CoinGate",
                provider_response={"error": str(exc), "order_id": order_id},
            )
        except ValueError:
            return PaymentResult(
                status="ERROR",
                provider=self.provider,
                rejection_reason="Respuesta invalida de CoinGate",
                provider_response={"status_code": getattr(response, "status_code", None), "order_id": order_id},
            )

        if response.status_code >= 400:
            return PaymentResult(
                status="ERROR",
                provider=self.provider,
                rejection_reason="CoinGate rechazo la creacion de la orden",
                provider_response=response_data,
            )

        payment_url = response_data.get("payment_url") or response_data.get("checkout_url") or response_data.get("url", "")
        external_reference = str(response_data.get("id") or response_data.get("order_id") or order_id)
        response_data["homechef_order_id"] = order_id
        if not payment_url:
            return PaymentResult(
                status="ERROR",
                provider=self.provider,
                external_reference=external_reference,
                rejection_reason="CoinGate no devolvio URL de pago",
                provider_response=response_data,
            )
        return PaymentResult(
            status="PENDING",
            provider=self.provider,
            external_reference=external_reference,
            payment_url=payment_url,
            provider_response=response_data,
        )

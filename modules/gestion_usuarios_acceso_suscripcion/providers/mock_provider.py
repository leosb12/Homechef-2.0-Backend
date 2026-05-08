from uuid import uuid4

from .base import PaymentProvider, PaymentResult


class MockPaymentProvider(PaymentProvider):
    provider = "MOCK"

    def process_payment(self, *, amount, currency, plan, payment_method=None, payload=None):
        payload = payload or {}
        result = str(payload.get("mock_result", "approved")).lower()
        if result == "approved":
            return PaymentResult(
                status="APPROVED",
                external_reference=f"mock_{uuid4().hex}",
                provider_response={"mock_result": result},
            )
        if result == "rejected":
            return PaymentResult(
                status="REJECTED",
                external_reference=f"mock_{uuid4().hex}",
                rejection_reason="Pago simulado rechazado",
                provider_response={"mock_result": result},
            )
        if result == "pending":
            return PaymentResult(
                status="PENDING",
                external_reference=f"mock_{uuid4().hex}",
                provider_response={"mock_result": result},
            )
        return PaymentResult(
            status="ERROR",
            external_reference=f"mock_{uuid4().hex}",
            rejection_reason="Error simulado de pasarela",
            provider_response={"mock_result": result},
        )

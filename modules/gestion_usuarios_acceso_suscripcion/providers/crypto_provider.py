from .base import PaymentProvider, PaymentResult


class CryptoPaymentProvider(PaymentProvider):
    provider = "CRYPTO"

    def process_payment(self, *, amount, currency, plan, payment_method=None, payload=None):
        payload = payload or {}
        tx_hash = str(payload.get("crypto_transaction_hash") or "").strip()
        wallet = str(payload.get("wallet_public_address") or "").strip()
        if not tx_hash and not wallet:
            return PaymentResult(
                status="ERROR",
                rejection_reason="Pago cripto pendiente de configuracion",
                provider_response={
                    "ready_for": ["wallet_public_address", "transaction_hash", "on_chain_confirmations", "webhooks"],
                },
            )
        return PaymentResult(
            status="PENDING",
            crypto_transaction_hash=tx_hash,
            provider_response={
                "wallet_public_address": wallet,
                "awaiting_on_chain_confirmations": True,
            },
        )

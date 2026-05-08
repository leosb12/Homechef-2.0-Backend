from decimal import Decimal, ROUND_HALF_UP

from ..exceptions import PaymentProviderUnavailable
from ..models import AIPaymentMethod, AISubscriptionPayment
from ..providers import CoinGateSandboxPaymentProvider, StripeSandboxPaymentProvider


BOB_PER_USD = Decimal("6.91")


class PaymentService:
    CHECKOUT_PROVIDERS = {
        AIPaymentMethod.Provider.STRIPE_SANDBOX: StripeSandboxPaymentProvider,
        AIPaymentMethod.Provider.COINGATE_SANDBOX: CoinGateSandboxPaymentProvider,
    }

    def get_payment_method(self, chef_profile, payment_method_id):
        if not payment_method_id:
            return None
        return AIPaymentMethod.objects.filter(
            id=payment_method_id,
            chef_profile=chef_profile,
            is_active=True,
        ).first()

    def create_checkout(self, *, chef_profile, plan, subscription, provider, payment_method_id=None, payload=None):
        provider = str(provider or "").upper()
        provider_class = self.CHECKOUT_PROVIDERS.get(provider)
        if not provider_class:
            raise PaymentProviderUnavailable("Proveedor de pago no soportado", provider)

        payment_method = self.get_payment_method(chef_profile, payment_method_id)
        if payment_method_id and not payment_method:
            raise PaymentProviderUnavailable("Metodo de pago invalido")

        payment = AISubscriptionPayment.objects.create(
            subscription=subscription,
            chef_profile=chef_profile,
            plan=plan,
            payment_method=payment_method,
            provider=provider,
            amount=self._bob_to_usd(plan.price),
            currency="USD",
            status=AISubscriptionPayment.Status.PENDING,
        )
        result = provider_class().create_payment(
            payment=payment,
            subscription=subscription,
            plan=plan,
            payload=payload or {},
        )
        payment.status = result.status
        payment.external_reference = result.external_reference
        payment.provider_response = result.provider_response
        payment.rejection_reason = result.rejection_reason
        payment.crypto_transaction_hash = result.crypto_transaction_hash
        payment.save(
            update_fields=[
                "status",
                "external_reference",
                "provider_response",
                "rejection_reason",
                "crypto_transaction_hash",
            ]
        )
        return payment

    def _bob_to_usd(self, amount_bob):
        return (Decimal(amount_bob) / BOB_PER_USD).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

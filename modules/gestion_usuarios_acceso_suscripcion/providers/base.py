from dataclasses import dataclass, field


@dataclass
class PaymentResult:
    status: str
    provider: str = ""
    external_reference: str = ""
    payment_url: str = ""
    rejection_reason: str = ""
    provider_response: dict = field(default_factory=dict)
    crypto_transaction_hash: str = ""


class PaymentProvider:
    provider = None

    def create_payment(self, *, payment, subscription, plan, payload=None):
        raise NotImplementedError

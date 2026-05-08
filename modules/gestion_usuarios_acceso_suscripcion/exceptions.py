class AISubscriptionError(Exception):
    code = "AI_SUBSCRIPTION_ERROR"
    status_code = 400

    def __init__(self, message, details=""):
        self.message = message
        self.details = details
        super().__init__(message)


class ChefAccessDenied(AISubscriptionError):
    code = "CHEF_ACCESS_DENIED"
    status_code = 403


class PlanUnavailable(AISubscriptionError):
    code = "PLAN_UNAVAILABLE"
    status_code = 400


class PaymentRejected(AISubscriptionError):
    code = "PAYMENT_REJECTED"
    status_code = 402


class PaymentPending(AISubscriptionError):
    code = "PAYMENT_PENDING"
    status_code = 202


class PaymentProviderUnavailable(AISubscriptionError):
    code = "PAYMENT_PROVIDER_UNAVAILABLE"
    status_code = 400


class SubscriptionNotFound(AISubscriptionError):
    code = "SUBSCRIPTION_NOT_FOUND"
    status_code = 404

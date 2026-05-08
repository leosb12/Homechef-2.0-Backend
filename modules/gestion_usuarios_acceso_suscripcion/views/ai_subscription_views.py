import stripe
from django.conf import settings
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from ..exceptions import AISubscriptionError
from ..models import ChefAISubscription
from ..permissions import IsActiveChef
from ..serializers.ai_subscription_serializers import (
    AISubscriptionAuditLogSerializer,
    AISubscriptionPaymentSerializer,
    AISubscriptionPlanSerializer,
    CancelSubscriptionRequestSerializer,
    ChangePlanRequestSerializer,
    ChefAISubscriptionSerializer,
    PaymentReturnConfirmSerializer,
    RenewRequestSerializer,
    SubscribeRequestSerializer,
    SubscriptionSummaryRequestSerializer,
)
from ..services.subscription_service import AISubscriptionService
from ..services.payment_callback_service import PaymentCallbackService


def success_response(message, data=None, http_status=status.HTTP_200_OK):
    return Response({"success": True, "message": message, "data": data or {}}, status=http_status)


def error_response(message, code, details="", http_status=status.HTTP_400_BAD_REQUEST):
    return Response(
        {"success": False, "message": message, "error": {"code": code, "details": details}},
        status=http_status,
    )


def handle_subscription_error(exc):
    return error_response(exc.message, exc.code, exc.details, exc.status_code)


def _service_and_chef(request):
    service = AISubscriptionService()
    return service, service.get_chef_profile(request.user)


def _subscription_data(subscription):
    return ChefAISubscriptionSerializer(subscription).data if subscription else None


def _payment_error_response(result):
    payment = result["payment"]
    code = result.get("payment_error")
    if code == "PAYMENT_REJECTED":
        return error_response("Pago rechazado", code, payment.rejection_reason, status.HTTP_402_PAYMENT_REQUIRED)
    if code:
        return error_response("No se pudo procesar el pago", code, payment.rejection_reason, status.HTTP_400_BAD_REQUEST)
    return None


def _checkout_data(payment):
    return {
        "payment_status": payment.status,
        "provider": payment.provider,
        "payment_url": payment.provider_response.get("url") or payment.provider_response.get("payment_url", ""),
        "external_reference": payment.external_reference,
    }


@api_view(["GET"])
@permission_classes([IsActiveChef])
def subscription_status(request):
    try:
        service, chef_profile = _service_and_chef(request)
        result = service.get_status(chef_profile, request=request)
        return success_response(
            "Estado de suscripcion IA consultado",
            {
                "subscription": _subscription_data(result["subscription"]),
                "can_use_ai": result["can_use_ai"],
                "limits": result["limits"],
            },
        )
    except AISubscriptionError as exc:
        return handle_subscription_error(exc)


@api_view(["GET"])
@permission_classes([IsActiveChef])
def available_plans(request):
    try:
        service, chef_profile = _service_and_chef(request)
        plans = service.list_available_plans(chef_profile, request=request)
        return success_response("Planes IA disponibles", AISubscriptionPlanSerializer(plans, many=True).data)
    except AISubscriptionError as exc:
        return handle_subscription_error(exc)


@api_view(["POST"])
@permission_classes([IsActiveChef])
def subscription_summary(request):
    serializer = SubscriptionSummaryRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        service, chef_profile = _service_and_chef(request)
        result = service.build_summary(chef_profile, request=request, **serializer.validated_data)
        return success_response(
            "Resumen de suscripcion IA generado",
            {
                "operation": result["operation"],
                "plan": AISubscriptionPlanSerializer(result["plan"]).data,
                "current_subscription": _subscription_data(result["current_subscription"]),
                "start_date": result["start_date"],
                "end_date": result["end_date"],
                "amount": result["amount"],
                "currency": result["currency"],
                "conditions": result["conditions"],
            },
        )
    except AISubscriptionError as exc:
        return handle_subscription_error(exc)


@api_view(["POST"])
@permission_classes([IsActiveChef])
def subscribe(request):
    serializer = SubscribeRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        service, chef_profile = _service_and_chef(request)
        data = serializer.validated_data
        result = service.subscribe(
            chef_profile,
            plan_id=data["plan_id"],
            payment_provider=data["payment_provider"],
            payment_method_id=data.get("payment_method_id"),
            payload=data,
            request=request,
        )
        payment_error = _payment_error_response(result)
        if payment_error:
            return payment_error
        return success_response(
            "Checkout/orden de pago creada correctamente",
            _checkout_data(result["payment"]),
            status.HTTP_201_CREATED,
        )
    except AISubscriptionError as exc:
        return handle_subscription_error(exc)


@api_view(["POST"])
@permission_classes([IsActiveChef])
def change_plan(request):
    serializer = ChangePlanRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        service, chef_profile = _service_and_chef(request)
        data = serializer.validated_data
        result = service.change_plan(
            chef_profile,
            new_plan_id=data["new_plan_id"],
            payment_provider=data["payment_provider"],
            payment_method_id=data.get("payment_method_id"),
            payload=data,
            request=request,
        )
        payment_error = _payment_error_response(result)
        if payment_error:
            return payment_error
        return success_response(
            "Checkout/orden de pago creada correctamente",
            _checkout_data(result["payment"]),
        )
    except AISubscriptionError as exc:
        return handle_subscription_error(exc)


@api_view(["POST"])
@permission_classes([IsActiveChef])
def renew(request):
    serializer = RenewRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        service, chef_profile = _service_and_chef(request)
        data = serializer.validated_data
        result = service.renew(
            chef_profile,
            payment_provider=data["payment_provider"],
            payment_method_id=data.get("payment_method_id"),
            payload=data,
            request=request,
        )
        payment_error = _payment_error_response(result)
        if payment_error:
            return payment_error
        return success_response(
            "Checkout/orden de pago creada correctamente",
            _checkout_data(result["payment"]),
        )
    except AISubscriptionError as exc:
        return handle_subscription_error(exc)


@api_view(["POST"])
@permission_classes([IsActiveChef])
def cancel(request):
    serializer = CancelSubscriptionRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        service, chef_profile = _service_and_chef(request)
        subscription = service.cancel(chef_profile, request=request, **serializer.validated_data)
        message = (
            "Cancelacion de suscripcion IA programada"
            if subscription.status == ChefAISubscription.Status.ACTIVE
            else "Suscripcion IA cancelada correctamente"
        )
        return success_response(message, {"subscription": ChefAISubscriptionSerializer(subscription).data})
    except AISubscriptionError as exc:
        return handle_subscription_error(exc)


@api_view(["GET"])
@permission_classes([IsActiveChef])
def payment_history(request):
    try:
        service, chef_profile = _service_and_chef(request)
        payments = service.list_payments(chef_profile)
        return success_response("Historial de pagos IA", AISubscriptionPaymentSerializer(payments, many=True).data)
    except AISubscriptionError as exc:
        return handle_subscription_error(exc)


@api_view(["POST"])
@permission_classes([IsActiveChef])
def confirm_payment_return(request):
    serializer = PaymentReturnConfirmSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        service, chef_profile = _service_and_chef(request)
        data = serializer.validated_data
        result = PaymentCallbackService().confirm_checkout_return(
            chef_profile,
            provider=data.get("provider", ""),
            stripe_session_id=data.get("stripe_session_id", ""),
            coingate_order_id=data.get("coingate_order_id", ""),
            trust_sandbox_return=data.get("trust_provider_return", False) or data.get("trust_sandbox_return", False),
        )
        status_message = "Pago confirmado" if result.get("status") == "APPROVED" else "Estado de pago consultado"
        return success_response(status_message, result)
    except AISubscriptionError as exc:
        return handle_subscription_error(exc)
    except Exception as exc:
        return error_response(
            "No se pudo confirmar el pago automaticamente",
            "PAYMENT_CONFIRMATION_FAILED",
            str(exc),
            status.HTTP_202_ACCEPTED,
        )


@api_view(["GET"])
@permission_classes([IsActiveChef])
def audit_log(request):
    try:
        service, chef_profile = _service_and_chef(request)
        logs = service.list_audit_logs(chef_profile)
        return success_response("Bitacora de suscripcion IA", AISubscriptionAuditLogSerializer(logs, many=True).data)
    except AISubscriptionError as exc:
        return handle_subscription_error(exc)


@api_view(["GET"])
@permission_classes([IsActiveChef])
def can_use_ai(request):
    try:
        service, chef_profile = _service_and_chef(request)
        result = service.can_use_ai(chef_profile, request=request)
        if not result["can_use_ai"]:
            return error_response(
                "Suscripcion IA requerida",
                "AI_SUBSCRIPTION_REQUIRED",
                "El cocinero no tiene una suscripcion IA activa.",
                status.HTTP_402_PAYMENT_REQUIRED,
            )
        return success_response(
            "Acceso IA validado",
            {
                "can_use_ai": result["can_use_ai"],
                "subscription": _subscription_data(result["subscription"]),
                "limits": result["limits"],
            },
        )
    except AISubscriptionError as exc:
        return handle_subscription_error(exc)


@api_view(["POST"])
@permission_classes([AllowAny])
def stripe_webhook(request):
    signature = request.META.get("HTTP_STRIPE_SIGNATURE", "")
    try:
        event = stripe.Webhook.construct_event(request.body, signature, settings.STRIPE_WEBHOOK_SECRET)
    except (ValueError, stripe.error.SignatureVerificationError):
        return error_response("Firma Stripe invalida", "INVALID_STRIPE_SIGNATURE", "", status.HTTP_400_BAD_REQUEST)

    service = PaymentCallbackService()
    event_type = event.get("type")
    obj = event.get("data", {}).get("object", {})
    if event_type == "checkout.session.completed":
        handled = service.approve_payment(
            external_reference=obj.get("id"),
            provider_response={"stripe_event": event},
        )
    elif event_type == "checkout.session.expired":
        handled = service.reject_payment(
            external_reference=obj.get("id"),
            provider_response={"stripe_event": event},
            reason="Stripe checkout expirado",
        )
    elif event_type == "payment_intent.payment_failed":
        metadata = obj.get("metadata") or {}
        last_error = obj.get("last_payment_error") or {}
        handled = service.reject_payment(
            payment_id=metadata.get("payment_id"),
            provider_response={"stripe_event": event},
            reason=last_error.get("message") or "Stripe payment intent fallido",
        )
    else:
        handled = True
    return success_response("Webhook Stripe procesado", {"handled": handled})


@api_view(["POST"])
@permission_classes([AllowAny])
def coingate_callback(request):
    handled = PaymentCallbackService().handle_coingate_callback(request.data)
    return success_response("Callback CoinGate procesado", {"handled": handled})

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from modules.gestion_cocinero.permissions import IsChefRole
from modules.marketplace_platos.permissions import IsClienteRole
from modules.pedidos_checkout_pagos.permissions import IsDeliveryRole
from modules.delivery_logistica.serializers import DeliveryIncidentCreateSerializer, DeliveryIncidentResolveSerializer
from modules.delivery_logistica.services.delivery_incident_service import DeliveryIncidentError, DeliveryIncidentService
from ..serializers import (
    CartItemUpdateSerializer,
    CartItemWriteSerializer,
    CheckoutConfirmSerializer,
    CheckoutRoutePreviewSerializer,
    CheckoutPreviewSerializer,
    CoinGateReturnConfirmSerializer,
    PickupConfirmSerializer,
    StripeReturnConfirmSerializer,
)
from ..services import (
    CartService,
    CartServiceError,
    CheckoutService,
    CheckoutServiceError,
    OrderCoinGateService,
    OrderCoinGateServiceError,
    OrderCashService,
    OrderCashServiceError,
    OrderStripeService,
    OrderStripeServiceError,
    QRPaymentService,
    QRPaymentServiceError,
)


@api_view(["GET"])
def module_home(request):
    return Response({"module": "pedidos_checkout_pagos", "status": "ok"})


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsClienteRole])
def cart_view(request):
    return Response(CartService().get_cart_summary(request.user.id), status=status.HTTP_200_OK)


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsClienteRole])
def cart_items_collection_view(request):
    serializer = CartItemWriteSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        payload = CartService().add_item(
            request.user.id,
            serializer.validated_data.get("dish_id", ""),
            serializer.validated_data["quantity"],
            serializer.validated_data.get("fulfillment_type") or "",
        )
        return Response(payload, status=status.HTTP_201_CREATED)
    except CartServiceError as exc:
        return Response(_cart_error_body(exc), status=_cart_error_status(exc.code))


@api_view(["PUT", "DELETE"])
@permission_classes([IsAuthenticated, IsClienteRole])
def cart_items_item_view(request, item_id: str):
    service = CartService()
    if request.method == "DELETE":
        try:
            return Response(service.remove_item(request.user.id, item_id), status=status.HTTP_200_OK)
        except CartServiceError as exc:
            return Response(_cart_error_body(exc), status=_cart_error_status(exc.code))

    serializer = CartItemUpdateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        payload = service.update_item(
            request.user.id,
            item_id,
            serializer.validated_data["quantity"],
            serializer.validated_data.get("fulfillment_type") or "",
        )
        return Response(payload, status=status.HTTP_200_OK)
    except CartServiceError as exc:
        return Response(_cart_error_body(exc), status=_cart_error_status(exc.code))


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsClienteRole])
def checkout_preview_view(request):
    serializer = CheckoutPreviewSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        payload = CheckoutService().preview(request.user.id, serializer.validated_data)
        return Response(payload, status=status.HTTP_200_OK)
    except CheckoutServiceError as exc:
        return Response(_checkout_error_body(exc), status=_checkout_error_status(exc.code))


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsClienteRole])
def checkout_route_preview_view(request):
    serializer = CheckoutRoutePreviewSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        payload = CheckoutService().preview_route(request.user.id, serializer.validated_data)
        return Response(payload, status=status.HTTP_200_OK)
    except CheckoutServiceError as exc:
        return Response(_checkout_error_body(exc), status=_checkout_error_status(exc.code))


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsClienteRole])
def checkout_confirm_view(request):
    serializer = CheckoutConfirmSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        payload = CheckoutService().confirm(request.user.id, serializer.validated_data)
        return Response(payload, status=status.HTTP_201_CREATED)
    except CheckoutServiceError as exc:
        return Response(_checkout_error_body(exc), status=_checkout_error_status(exc.code))


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsClienteRole])
def my_orders_view(request):
    return Response(OrderCashService().list_client_orders(request.user.id), status=status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsClienteRole])
def my_order_detail_view(request, order_id: str):
    try:
        payload = OrderCashService().get_client_order_detail(request.user.id, order_id)
        return Response(payload, status=status.HTTP_200_OK)
    except OrderCashServiceError as exc:
        return Response(_order_error_body(exc), status=_order_error_status(exc.code))


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsClienteRole])
def my_order_tracking_view(request, order_id: str):
    try:
        payload = OrderCashService().get_client_order_tracking(request.user.id, order_id)
        return Response(payload, status=status.HTTP_200_OK)
    except OrderCashServiceError as exc:
        return Response(_order_error_body(exc), status=_order_error_status(exc.code))


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated, IsClienteRole])
def my_order_incidents_view(request, order_id: str):
    service = DeliveryIncidentService()
    if request.method == "GET":
        try:
            payload = service.list_for_client(request.user.id, order_id)
            return Response(payload, status=status.HTTP_200_OK)
        except DeliveryIncidentError as exc:
            return Response(_order_error_body(exc), status=_order_error_status(exc.code))
    serializer = DeliveryIncidentCreateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        payload = service.create_for_client(request.user.id, order_id, serializer.validated_data)
        return Response(payload, status=status.HTTP_201_CREATED)
    except DeliveryIncidentError as exc:
        return Response(_order_error_body(exc), status=_order_error_status(exc.code))


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsClienteRole])
def client_order_cancel_view(request, order_id: str):
    try:
        payload = OrderCashService().cancel_client_order(request.user.id, order_id)
        return Response(payload, status=status.HTTP_200_OK)
    except OrderCashServiceError as exc:
        return Response(_order_error_body(exc), status=_order_error_status(exc.code))


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsChefRole])
def chef_orders_view(request):
    return Response(OrderCashService().list_chef_orders(request.user.id), status=status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsChefRole])
def chef_order_tracking_view(request, order_id: str):
    try:
        payload = OrderCashService().get_chef_order_tracking(request.user.id, order_id)
        return Response(payload, status=status.HTTP_200_OK)
    except OrderCashServiceError as exc:
        return Response(_order_error_body(exc), status=_order_error_status(exc.code))


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsChefRole])
def chef_order_incidents_view(request, order_id: str):
    try:
        payload = DeliveryIncidentService().list_for_chef(request.user.id, order_id)
        return Response(payload, status=status.HTTP_200_OK)
    except DeliveryIncidentError as exc:
        return Response(_order_error_body(exc), status=_order_error_status(exc.code))


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsChefRole])
def chef_order_incident_resolve_view(request, order_id: str, incident_id: str):
    serializer = DeliveryIncidentResolveSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        payload = DeliveryIncidentService().resolve_for_chef(
            request.user.id,
            order_id,
            incident_id,
            serializer.validated_data,
        )
        return Response(payload, status=status.HTTP_200_OK)
    except DeliveryIncidentError as exc:
        return Response(_order_error_body(exc), status=_order_error_status(exc.code))


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsChefRole])
def chef_order_detail_view(request, order_id: str):
    try:
        payload = OrderCashService().get_chef_order_detail(request.user.id, order_id)
        return Response(payload, status=status.HTTP_200_OK)
    except OrderCashServiceError as exc:
        return Response(_order_error_body(exc), status=_order_error_status(exc.code))


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsChefRole])
def chef_order_accept_view(request, order_id: str):
    try:
        payload = OrderCashService().chef_accept_order(request.user.id, order_id)
        return Response(payload, status=status.HTTP_200_OK)
    except OrderCashServiceError as exc:
        return Response(_order_error_body(exc), status=_order_error_status(exc.code))


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsChefRole])
def chef_order_preparing_view(request, order_id: str):
    try:
        payload = OrderCashService().chef_mark_preparing(request.user.id, order_id)
        return Response(payload, status=status.HTTP_200_OK)
    except OrderCashServiceError as exc:
        return Response(_order_error_body(exc), status=_order_error_status(exc.code))


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsChefRole])
def chef_order_ready_view(request, order_id: str):
    try:
        payload = OrderCashService().chef_mark_ready(request.user.id, order_id)
        return Response(payload, status=status.HTTP_200_OK)
    except OrderCashServiceError as exc:
        return Response(_order_error_body(exc), status=_order_error_status(exc.code))


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsChefRole])
def chef_order_reject_view(request, order_id: str):
    try:
        payload = OrderCashService().chef_reject_order(request.user.id, order_id)
        return Response(payload, status=status.HTTP_200_OK)
    except OrderCashServiceError as exc:
        return Response(_order_error_body(exc), status=_order_error_status(exc.code))


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsChefRole])
def chef_order_pickup_confirm_view(request, order_id: str):
    serializer = PickupConfirmSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        payload = OrderCashService().chef_confirm_pickup(
            request.user.id,
            order_id,
            serializer.validated_data["pickup_code"],
        )
        return Response(payload, status=status.HTTP_200_OK)
    except OrderCashServiceError as exc:
        return Response(_order_error_body(exc), status=_order_error_status(exc.code))


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsDeliveryRole])
def delivery_cash_orders_view(request):
    return Response(OrderCashService().list_delivery_cash_orders(request.user.id), status=status.HTTP_200_OK)


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsDeliveryRole])
def delivery_order_start_view(request, order_id: str):
    try:
        payload = OrderCashService().delivery_start_order(request.user.id, order_id)
        return Response(payload, status=status.HTTP_200_OK)
    except OrderCashServiceError as exc:
        return Response(_order_error_body(exc), status=_order_error_status(exc.code))


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsDeliveryRole])
def delivery_order_cash_confirm_view(request, order_id: str):
    try:
        payload = OrderCashService().delivery_confirm_cash(request.user.id, order_id)
        return Response(payload, status=status.HTTP_200_OK)
    except OrderCashServiceError as exc:
        return Response(_order_error_body(exc), status=_order_error_status(exc.code))


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsClienteRole])
def qr_session_detail_view(request, session_code: str):
    try:
        payload = QRPaymentService().get_session_for_client(request.user.id, session_code)
        return Response(payload, status=status.HTTP_200_OK)
    except QRPaymentServiceError as exc:
        return Response(_qr_error_body(exc), status=_qr_error_status(exc.code))


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsClienteRole])
def qr_session_start_view(request, session_code: str):
    try:
        payload = QRPaymentService().start_session(request.user.id, session_code)
        return Response(payload, status=status.HTTP_200_OK)
    except QRPaymentServiceError as exc:
        return Response(_qr_error_body(exc), status=_qr_error_status(exc.code))


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsClienteRole])
def qr_session_confirm_view(request, session_code: str):
    try:
        payload = QRPaymentService().confirm_session(request.user.id, session_code)
        return Response(payload, status=status.HTTP_200_OK)
    except QRPaymentServiceError as exc:
        return Response(_qr_error_body(exc), status=_qr_error_status(exc.code))


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsClienteRole])
def qr_session_cancel_view(request, session_code: str):
    try:
        payload = QRPaymentService().cancel_session(request.user.id, session_code)
        return Response(payload, status=status.HTTP_200_OK)
    except QRPaymentServiceError as exc:
        return Response(_qr_error_body(exc), status=_qr_error_status(exc.code))


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsClienteRole])
def bitcoin_coingate_confirm_return_view(request):
    serializer = CoinGateReturnConfirmSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        payload = OrderCoinGateService().confirm_checkout_return(
            request.user.id,
            provider=serializer.validated_data.get("provider", ""),
            coingate_order_id=serializer.validated_data.get("coingate_order_id", ""),
        )
        return Response(payload, status=status.HTTP_200_OK)
    except OrderCoinGateServiceError as exc:
        return Response(_coingate_error_body(exc), status=_coingate_error_status(exc.code))


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsClienteRole])
def stripe_confirm_return_view(request):
    serializer = StripeReturnConfirmSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        payload = OrderStripeService().confirm_checkout_return(
            request.user.id,
            provider=serializer.validated_data.get("provider", ""),
            stripe_session_id=serializer.validated_data.get("stripe_session_id", ""),
        )
        return Response(payload, status=status.HTTP_200_OK)
    except OrderStripeServiceError as exc:
        return Response(_stripe_error_body(exc), status=_stripe_error_status(exc.code))


@api_view(["POST"])
@permission_classes([AllowAny])
def bitcoin_coingate_callback_view(request):
    try:
        handled = OrderCoinGateService().handle_callback(request.data if isinstance(request.data, dict) else {})
        return Response({"handled": handled}, status=status.HTTP_200_OK)
    except OrderCoinGateServiceError as exc:
        return Response(_coingate_error_body(exc), status=_coingate_error_status(exc.code))


def _cart_error_body(exc: CartServiceError):
    body = {"detail": exc.message, "code": exc.code}
    body.update(exc.details or {})
    return body


def _cart_error_status(code: str):
    if code in {"invalid_quantity", "insufficient_portions"}:
        return status.HTTP_400_BAD_REQUEST
    if code in {"cart_item_not_found", "dish_not_found", "client_not_found"}:
        return status.HTTP_404_NOT_FOUND
    if code in {"chef_unavailable", "modality_not_allowed", "dish_unavailable", "dish_unpublished"}:
        return status.HTTP_409_CONFLICT
    return status.HTTP_400_BAD_REQUEST


def _checkout_error_body(exc: CheckoutServiceError):
    body = {"detail": exc.message, "code": exc.code}
    body.update(exc.details or {})
    return body


def _checkout_error_status(code: str):
    if code in {
        "address_required",
        "contact_name_required",
        "contact_phone_required",
        "pricing_changed",
        "stock_validation_failed",
        "payment_method_not_supported",
        "chef_location_missing",
    }:
        return status.HTTP_400_BAD_REQUEST
    if code in {"cart_not_found", "empty_cart", "client_not_found"}:
        return status.HTTP_404_NOT_FOUND
    if code in {"chef_unavailable"}:
        return status.HTTP_409_CONFLICT
    return status.HTTP_400_BAD_REQUEST


def _order_error_body(exc: OrderCashServiceError):
    body = {"detail": exc.message, "code": exc.code}
    body.update(exc.details or {})
    return body


def _order_error_status(code: str):
    if code in {"order_not_found", "payment_not_found", "chef_not_found", "client_not_found", "delivery_not_found", "pickup_not_found", "assignment_not_found", "incident_not_found"}:
        return status.HTTP_404_NOT_FOUND
    if code in {"delivery_not_owner", "transition_not_allowed", "client_cancel_not_allowed", "incident_transition_not_allowed", "incident_already_closed"}:
        return status.HTTP_409_CONFLICT
    if code in {"pickup_already_confirmed"}:
        return status.HTTP_409_CONFLICT
    if code in {"invalid_fulfillment_type", "payment_already_confirmed", "payment_invalid_status", "pickup_invalid_status", "pickup_code_required", "pickup_code_invalid"}:
        return status.HTTP_400_BAD_REQUEST
    return status.HTTP_400_BAD_REQUEST


def _qr_error_body(exc: QRPaymentServiceError):
    body = {"detail": exc.message, "code": exc.code}
    body.update(exc.details or {})
    return body


def _qr_error_status(code: str):
    if code in {"session_not_found", "client_not_found"}:
        return status.HTTP_404_NOT_FOUND
    if code in {"session_invalid_status", "session_already_confirmed"}:
        return status.HTTP_409_CONFLICT
    return status.HTTP_400_BAD_REQUEST


def _coingate_error_body(exc: OrderCoinGateServiceError):
    body = {"detail": exc.message, "code": exc.code}
    body.update(exc.details or {})
    return body


def _coingate_error_status(code: str):
    if code in {"payment_not_found", "client_not_found"}:
        return status.HTTP_404_NOT_FOUND
    if code in {"provider_rejected"}:
        return status.HTTP_502_BAD_GATEWAY
    if code in {"provider_not_configured", "provider_request_failed", "provider_invalid_response", "payment_url_missing"}:
        return status.HTTP_400_BAD_REQUEST
    return status.HTTP_400_BAD_REQUEST


def _stripe_error_body(exc: OrderStripeServiceError):
    body = {"detail": exc.message, "code": exc.code}
    body.update(exc.details or {})
    return body


def _stripe_error_status(code: str):
    if code in {"payment_not_found", "client_not_found"}:
        return status.HTTP_404_NOT_FOUND
    if code in {"provider_rejected"}:
        return status.HTTP_502_BAD_GATEWAY
    if code in {"provider_not_configured", "provider_request_failed"}:
        return status.HTTP_400_BAD_REQUEST
    return status.HTTP_400_BAD_REQUEST

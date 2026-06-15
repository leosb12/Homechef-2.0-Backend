from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response


from modules.confianza_administracion_seguridad.serializers import (
    DeliveryDriverStatusSerializer,
    NotificationDeviceTokenSerializer,
    NotificationTokenDeactivateSerializer,
)
from modules.confianza_administracion_seguridad.services import (
    DeliveryActiveOrdersAdminError,
    DeliveryActiveOrdersAdminService,
    DeliveryDriverAdminError,
    DeliveryDriverAdminService,
    NotificationService,
    NotificationServiceError,
)


@api_view(["GET"])
def module_home(request):
    return Response({"module": "confianza_administracion_seguridad", "status": "ok"})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def notifications_collection_view(request):
    unread_only = str(request.query_params.get("unread_only", "")).lower() in {"1", "true", "yes"}
    limit = int(request.query_params.get("limit", "50") or 50)
    try:
        payload = NotificationService().list_for_user(
            request.user.id,
            unread_only=unread_only,
            limit=limit,
        )
        return Response(payload, status=status.HTTP_200_OK)
    except NotificationServiceError as exc:
        return Response(_error_body(exc), status=_error_status(exc.code))


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def notifications_mark_all_read_view(request):
    try:
        payload = NotificationService().mark_all_as_read(request.user.id)
        return Response(payload, status=status.HTTP_200_OK)
    except NotificationServiceError as exc:
        return Response(_error_body(exc), status=_error_status(exc.code))


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def notifications_mark_read_view(request, notification_id: str):
    try:
        payload = NotificationService().mark_as_read(request.user.id, notification_id)
        return Response(payload, status=status.HTTP_200_OK)
    except NotificationServiceError as exc:
        return Response(_error_body(exc), status=_error_status(exc.code))


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def notification_devices_register_view(request):
    serializer = NotificationDeviceTokenSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        payload = NotificationService().register_device_token(request.user.id, serializer.validated_data)
        return Response(payload, status=status.HTTP_201_CREATED)
    except NotificationServiceError as exc:
        return Response(_error_body(exc), status=_error_status(exc.code))


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def notification_devices_unregister_view(request):
    serializer = NotificationTokenDeactivateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        payload = NotificationService().deactivate_device_token(request.user.id, serializer.validated_data["token"])
        return Response(payload, status=status.HTTP_200_OK)
    except NotificationServiceError as exc:
        return Response(_error_body(exc), status=_error_status(exc.code))


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def delivery_drivers_collection_view(request):
    status_filter = str(request.query_params.get("approval_status", "")).strip().lower()
    try:
        payload = DeliveryDriverAdminService().list_drivers(status_filter=status_filter)
        return Response(payload, status=status.HTTP_200_OK)
    except DeliveryDriverAdminError as exc:
        return Response(_error_body(exc), status=_error_status(exc.code))


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def delivery_driver_status_update_view(request, user_id: str):
    serializer = DeliveryDriverStatusSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        payload = DeliveryDriverAdminService().update_status(
            request.user.id,
            user_id,
            serializer.validated_data["approval_status"],
        )
        return Response(payload, status=status.HTTP_200_OK)
    except DeliveryDriverAdminError as exc:
        return Response(_error_body(exc), status=_error_status(exc.code))


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def delivery_active_orders_collection_view(request):
    try:
        payload = DeliveryActiveOrdersAdminService().list_active_orders(request.user.id)
        return Response(payload, status=status.HTTP_200_OK)
    except DeliveryActiveOrdersAdminError as exc:
        return Response(_error_body(exc), status=_error_status(exc.code))


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def delivery_active_order_detail_view(request, order_id: str):
    try:
        payload = DeliveryActiveOrdersAdminService().get_active_order_detail(request.user.id, order_id)
        return Response(payload, status=status.HTTP_200_OK)
    except DeliveryActiveOrdersAdminError as exc:
        return Response(_error_body(exc), status=_error_status(exc.code))


def _error_body(exc: NotificationServiceError):
    body = {"detail": exc.message, "code": exc.code}
    body.update(exc.details or {})
    return body


def _error_status(code: str):
    if code in {"user_not_found", "notification_not_found", "device_token_not_found", "delivery_not_found", "order_not_found"}:
        return status.HTTP_404_NOT_FOUND
    if code in {"token_required", "invalid_status"}:
        return status.HTTP_400_BAD_REQUEST
    if code in {"admin_required"}:
        return status.HTTP_403_FORBIDDEN
    return status.HTTP_400_BAD_REQUEST

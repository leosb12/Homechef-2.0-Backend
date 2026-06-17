from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from modules.pedidos_checkout_pagos.permissions import IsDeliveryRole
from ..serializers import DeliveryLocationPingSerializer
from ..serializers import DeliveryIncidentCreateSerializer, DeliveryIncidentResolveSerializer
from ..serializers import DeliveryAvailabilityUpdateSerializer
from ..services.delivery_availability_service import DeliveryAvailabilityError, DeliveryAvailabilityService
from ..services.delivery_incident_service import DeliveryIncidentError, DeliveryIncidentService
from ..services.delivery_offer_service import DeliveryOfferError, DeliveryOfferService
from ..services.delivery_operations_service import (
    DeliveryLogisticsError,
    DeliveryOperationsService,
)
from ..services.delivery_tracking_service import (
    DeliveryTrackingError,
    DeliveryTrackingService,
)


@api_view(["GET"])
def module_home(request):
    return Response({"module": "delivery_logistica", "status": "ok"})


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsDeliveryRole])
def delivery_assigned_view(request):
    try:
        payload = DeliveryOperationsService().list_assigned(request.user.id)
        return Response(payload, status=status.HTTP_200_OK)
    except DeliveryLogisticsError as exc:
        return Response(_error_body(exc), status=_error_status(exc.code))


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsDeliveryRole])
def delivery_active_view(request):
    try:
        payload = DeliveryOperationsService().list_active(request.user.id)
        return Response(payload, status=status.HTTP_200_OK)
    except DeliveryLogisticsError as exc:
        return Response(_error_body(exc), status=_error_status(exc.code))


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated, IsDeliveryRole])
def delivery_availability_view(request):
    service = DeliveryAvailabilityService()
    if request.method == "GET":
        try:
            payload = service.get_status(request.user.id)
            return Response(payload, status=status.HTTP_200_OK)
        except DeliveryAvailabilityError as exc:
            return Response(_error_body(exc), status=_error_status(exc.code))

    serializer = DeliveryAvailabilityUpdateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        payload = service.update_manual_status(
            request.user.id,
            serializer.validated_data["manual_status"],
        )
        return Response(payload, status=status.HTTP_200_OK)
    except DeliveryAvailabilityError as exc:
        return Response(_error_body(exc), status=_error_status(exc.code))


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsDeliveryRole])
def delivery_pending_offers_view(request):
    try:
        payload = DeliveryOfferService().list_pending_for_delivery(request.user.id)
        return Response(payload, status=status.HTTP_200_OK)
    except DeliveryOfferError as exc:
        return Response(_error_body(exc), status=_error_status(exc.code))


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsDeliveryRole])
def delivery_open_board_view(request):
    try:
        payload = DeliveryOperationsService().list_open_board(request.user.id)
        return Response(payload, status=status.HTTP_200_OK)
    except DeliveryLogisticsError as exc:
        return Response(_error_body(exc), status=_error_status(exc.code))
    except DeliveryOfferError as exc:
        return Response(_error_body(exc), status=_error_status(exc.code))


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsDeliveryRole])
def delivery_detail_view(request, assignment_id: str):
    try:
        payload = DeliveryOperationsService().get_detail(request.user.id, assignment_id)
        return Response(payload, status=status.HTTP_200_OK)
    except DeliveryLogisticsError as exc:
        return Response(_error_body(exc), status=_error_status(exc.code))


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsDeliveryRole])
def delivery_accept_view(request, assignment_id: str):
    try:
        payload = DeliveryOperationsService().accept_assignment(request.user.id, assignment_id)
        return Response(payload, status=status.HTTP_200_OK)
    except DeliveryLogisticsError as exc:
        return Response(_error_body(exc), status=_error_status(exc.code))


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsDeliveryRole])
def delivery_claim_view(request, assignment_id: str):
    try:
        payload = DeliveryOperationsService().claim_open_board_assignment(request.user.id, assignment_id)
        return Response(payload, status=status.HTTP_200_OK)
    except DeliveryLogisticsError as exc:
        return Response(_error_body(exc), status=_error_status(exc.code))
    except DeliveryOfferError as exc:
        return Response(_error_body(exc), status=_error_status(exc.code))


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsDeliveryRole])
def delivery_reject_view(request, assignment_id: str):
    try:
        payload = DeliveryOperationsService().reject_assignment(request.user.id, assignment_id)
        return Response(payload, status=status.HTTP_200_OK)
    except DeliveryLogisticsError as exc:
        return Response(_error_body(exc), status=_error_status(exc.code))
    except DeliveryOfferError as exc:
        return Response(_error_body(exc), status=_error_status(exc.code))


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsDeliveryRole])
def delivery_cancel_view(request, assignment_id: str):
    try:
        payload = DeliveryOperationsService().cancel_assignment(request.user.id, assignment_id)
        return Response(payload, status=status.HTTP_200_OK)
    except DeliveryLogisticsError as exc:
        return Response(_error_body(exc), status=_error_status(exc.code))
    except DeliveryOfferError as exc:
        return Response(_error_body(exc), status=_error_status(exc.code))


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsDeliveryRole])
def delivery_arrived_chef_view(request, assignment_id: str):
    try:
        payload = DeliveryOperationsService().arrived_chef(request.user.id, assignment_id)
        return Response(payload, status=status.HTTP_200_OK)
    except DeliveryLogisticsError as exc:
        return Response(_error_body(exc), status=_error_status(exc.code))


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsDeliveryRole])
def delivery_picked_up_view(request, assignment_id: str):
    try:
        payload = DeliveryOperationsService().picked_up(request.user.id, assignment_id)
        return Response(payload, status=status.HTTP_200_OK)
    except DeliveryLogisticsError as exc:
        return Response(_error_body(exc), status=_error_status(exc.code))


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsDeliveryRole])
def delivery_delivered_view(request, assignment_id: str):
    try:
        payload = DeliveryOperationsService().delivered(request.user.id, assignment_id)
        return Response(payload, status=status.HTTP_200_OK)
    except DeliveryLogisticsError as exc:
        return Response(_error_body(exc), status=_error_status(exc.code))


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsDeliveryRole])
def delivery_location_ping_view(request, assignment_id: str):
    serializer = DeliveryLocationPingSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        payload = DeliveryTrackingService().record_delivery_location(
            request.user.id,
            assignment_id,
            serializer.validated_data,
        )
        return Response(payload, status=status.HTTP_201_CREATED)
    except DeliveryTrackingError as exc:
        return Response(_error_body(exc), status=_error_status(exc.code))


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsDeliveryRole])
def delivery_current_location_view(request, assignment_id: str):
    try:
        payload = DeliveryTrackingService().get_current_location(request.user.id, assignment_id)
        return Response(payload, status=status.HTTP_200_OK)
    except DeliveryTrackingError as exc:
        return Response(_error_body(exc), status=_error_status(exc.code))


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsDeliveryRole])
def delivery_route_snapshot_view(request, assignment_id: str):
    try:
        payload = DeliveryTrackingService().get_route_snapshot(request.user.id, assignment_id)
        return Response(payload, status=status.HTTP_200_OK)
    except DeliveryTrackingError as exc:
        return Response(_error_body(exc), status=_error_status(exc.code))


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsDeliveryRole])
def delivery_route_refresh_view(request, assignment_id: str):
    try:
        payload = DeliveryTrackingService().refresh_route_for_delivery(request.user.id, assignment_id)
        return Response(payload, status=status.HTTP_200_OK)
    except DeliveryTrackingError as exc:
        return Response(_error_body(exc), status=_error_status(exc.code))


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated, IsDeliveryRole])
def delivery_incidents_collection_view(request, assignment_id: str):
    service = DeliveryIncidentService()
    if request.method == "GET":
        try:
            payload = service.list_for_delivery(request.user.id, assignment_id)
            return Response(payload, status=status.HTTP_200_OK)
        except DeliveryIncidentError as exc:
            return Response(_error_body(exc), status=_error_status(exc.code))

    serializer = DeliveryIncidentCreateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        payload = service.create_for_delivery(request.user.id, assignment_id, serializer.validated_data)
        return Response(payload, status=status.HTTP_201_CREATED)
    except DeliveryIncidentError as exc:
        return Response(_error_body(exc), status=_error_status(exc.code))


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsDeliveryRole])
def delivery_incident_resolve_view(request, assignment_id: str, incident_id: str):
    serializer = DeliveryIncidentResolveSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        payload = DeliveryIncidentService().resolve_for_delivery(
            request.user.id,
            assignment_id,
            incident_id,
            serializer.validated_data,
        )
        return Response(payload, status=status.HTTP_200_OK)
    except DeliveryIncidentError as exc:
        return Response(_error_body(exc), status=_error_status(exc.code))


def _error_body(exc: DeliveryLogisticsError):
    body = {"detail": exc.message, "code": exc.code}
    body.update(exc.details or {})
    return body


def _error_status(code: str):
    if code in {"delivery_not_found", "assignment_not_found", "incident_not_found"}:
        return status.HTTP_404_NOT_FOUND
    if code in {
        "assignment_already_taken",
        "assignment_offer_reserved",
        "assignment_not_open_board",
        "transition_not_allowed",
        "delivery_not_owner",
        "assignment_reassigned",
        "assignment_cancel_not_allowed",
        "offer_not_available",
        "order_not_ready_for_delivery",
        "assignment_tracking_closed",
        "incident_transition_not_allowed",
        "incident_already_closed",
        "delivery_busy",
    }:
        return status.HTTP_409_CONFLICT
    if code in {"delivery_profile_not_found", "availability_status_invalid"}:
        return status.HTTP_400_BAD_REQUEST
    return status.HTTP_400_BAD_REQUEST

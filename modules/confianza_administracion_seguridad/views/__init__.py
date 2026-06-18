from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.utils import timezone
from modules.gestion_cocinero.models import Dish
from modules.gestion_usuarios_acceso_suscripcion.models import UserProfile
from modules.marketplace_platos.permissions import IsClienteRole
from ..models import PublicationReport
from ..permissions import IsAdminRole
from ..services.quality_analysis_service import QualityAnalysisService

from modules.confianza_administracion_seguridad.serializers import (
    DeliveryDriverStatusSerializer,
    NotificationDeviceTokenSerializer,
    NotificationTokenDeactivateSerializer,
)
from modules.confianza_administracion_seguridad.services import (
    NotificationService, NotificationServiceError,
    AdminPlatformService, AdminPlatformError
)
from modules.gestion_usuarios_acceso_suscripcion.models import UserProfile
from modules.confianza_administracion_seguridad.services.delivery_active_orders_admin_service import (
    DeliveryActiveOrdersAdminError,
    DeliveryActiveOrdersAdminService,
)
from modules.confianza_administracion_seguridad.services.delivery_driver_admin_service import (
    DeliveryDriverAdminError,
    DeliveryDriverAdminService,
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


def _error_body(exc):
    body = {"detail": getattr(exc, "message", str(exc)), "code": getattr(exc, "code", "unknown")}
    if hasattr(exc, "details"):
        body.update(exc.details or {})
    return body


def _error_status(code: str):
    if code in {"user_not_found", "notification_not_found", "device_token_not_found", "delivery_not_found", "order_not_found", "chef_not_found", "dish_not_found"}:
        return status.HTTP_404_NOT_FOUND
    if code in {"token_required", "invalid_status"}:
        return status.HTTP_400_BAD_REQUEST
    if code in {"admin_required"}:
        return status.HTTP_403_FORBIDDEN
    return status.HTTP_400_BAD_REQUEST


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def admin_platform_users_collection_view(request):
    if request.user.role != UserProfile.ROLE_ADMIN:
        return Response({"detail": "Se requieren permisos de administrador"}, status=status.HTTP_403_FORBIDDEN)
    try:
        payload = AdminPlatformService().list_users()
        return Response(payload, status=status.HTTP_200_OK)
    except AdminPlatformError as exc:
        return Response(_error_body(exc), status=_error_status(exc.code))

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def admin_platform_user_toggle_block_view(request, user_id: str):
    if request.user.role != UserProfile.ROLE_ADMIN:
        return Response({"detail": "Se requieren permisos de administrador"}, status=status.HTTP_403_FORBIDDEN)
    try:
        payload = AdminPlatformService().toggle_user_block(user_id)
        return Response(payload, status=status.HTTP_200_OK)
    except AdminPlatformError as exc:
        return Response(_error_body(exc), status=_error_status(exc.code))

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def admin_platform_pending_chefs_view(request):
    if request.user.role != UserProfile.ROLE_ADMIN:
        return Response({"detail": "Se requieren permisos de administrador"}, status=status.HTTP_403_FORBIDDEN)
    try:
        payload = AdminPlatformService().list_pending_chefs()
        return Response(payload, status=status.HTTP_200_OK)
    except AdminPlatformError as exc:
        return Response(_error_body(exc), status=_error_status(exc.code))

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def admin_platform_chef_validate_view(request, chef_id: str):
    if request.user.role != UserProfile.ROLE_ADMIN:
        return Response({"detail": "Se requieren permisos de administrador"}, status=status.HTTP_403_FORBIDDEN)
    try:
        action = request.data.get("status")
        payload = AdminPlatformService().validate_chef(chef_id, action)
        return Response(payload, status=status.HTTP_200_OK)
    except AdminPlatformError as exc:
        return Response(_error_body(exc), status=_error_status(exc.code))

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def admin_platform_publications_view(request):
    if request.user.role != UserProfile.ROLE_ADMIN:
        return Response({"detail": "Se requieren permisos de administrador"}, status=status.HTTP_403_FORBIDDEN)
    try:
        payload = AdminPlatformService().list_publications()
        return Response(payload, status=status.HTTP_200_OK)
    except AdminPlatformError as exc:
        return Response(_error_body(exc), status=_error_status(exc.code))

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def admin_platform_publication_action_view(request, dish_id: str):
    if request.user.role != UserProfile.ROLE_ADMIN:
        return Response({"detail": "Se requieren permisos de administrador"}, status=status.HTTP_403_FORBIDDEN)
    try:
        action = request.data.get("action")
        payload = AdminPlatformService().toggle_publication_action(dish_id, action)
        return Response(payload, status=status.HTTP_200_OK)
    except AdminPlatformError as exc:
        return Response(_error_body(exc), status=_error_status(exc.code))

def serialize_dish_summary(dish):
    return {
        "id": dish.id,
        "name": dish.name,
        "image_url": dish.image_url,
        "price": float(dish.price),
        "chef_id": str(dish.chef.supabase_user_id),
        "chef_name": dish.chef.full_name or dish.chef.email,
        "ia_risk_score": dish.ia_risk_score,
        "revision_status": dish.revision_status,
        "reported_count": dish.reported_count,
        "status": dish.status,
        "created_at": dish.created_at.isoformat() if dish.created_at else None,
    }

def serialize_report(report):
    return {
        "id": report.id,
        "user_id": str(report.user.supabase_user_id),
        "user_email": report.user.email,
        "user_name": report.user.full_name,
        "reason": report.reason,
        "comment": report.comment,
        "created_at": report.created_at.isoformat() if report.created_at else None,
        "status": report.status,
    }

def serialize_dish_detail(dish):
    reports = PublicationReport.objects.filter(publication=dish).order_by("-created_at")
    return {
        "id": dish.id,
        "name": dish.name,
        "description": dish.description,
        "price": float(dish.price),
        "portions": dish.portions,
        "ingredients": dish.ingredients,
        "tags": dish.tags,
        "allergens": dish.allergens,
        "image_url": dish.image_url,
        "schedule": dish.schedule,
        "status": dish.status,
        "chef_id": str(dish.chef.supabase_user_id),
        "chef_name": dish.chef.full_name or dish.chef.email,
        "chef_email": dish.chef.email,
        "revision_status": dish.revision_status,
        "ia_risk_score": dish.ia_risk_score,
        "ia_quality_review_id": dish.ia_quality_review_id,
        "ia_quality_reasons": dish.ia_quality_reasons,
        "ia_quality_recommendation": dish.ia_quality_recommendation,
        "reported_count": dish.reported_count,
        "last_quality_analysis_at": dish.last_quality_analysis_at.isoformat() if dish.last_quality_analysis_at else None,
        "admin_reviewed_by": str(dish.admin_reviewed_by.supabase_user_id) if dish.admin_reviewed_by else None,
        "admin_reviewed_by_name": dish.admin_reviewed_by.full_name if dish.admin_reviewed_by else None,
        "admin_reviewed_at": dish.admin_reviewed_at.isoformat() if dish.admin_reviewed_at else None,
        "admin_review_comment": dish.admin_review_comment,
        "created_at": dish.created_at.isoformat() if dish.created_at else None,
        "updated_at": dish.updated_at.isoformat() if dish.updated_at else None,
        "reports": [serialize_report(r) for r in reports],
    }

@api_view(["GET"])
@permission_classes([IsAuthenticated, IsAdminRole])
def list_publications(request):
    """
    Lista de todas las publicaciones (platos) con su estado de revisión.
    Soporta filtros opcionales.
    """
    queryset = Dish.objects.filter(deleted_at__isnull=True).order_by("-created_at")
    
    # Filtros
    status_param = request.query_params.get("status")
    if status_param:
        queryset = queryset.filter(revision_status=status_param)
        
    chef_id = request.query_params.get("chef_id")
    if chef_id:
        queryset = queryset.filter(chef__supabase_user_id=chef_id)
        
    min_risk = request.query_params.get("min_risk")
    if min_risk:
        try:
            queryset = queryset.filter(ia_risk_score__gte=int(min_risk))
        except ValueError:
            pass
            
    max_risk = request.query_params.get("max_risk")
    if max_risk:
        try:
            queryset = queryset.filter(ia_risk_score__lte=int(max_risk))
        except ValueError:
            pass
            
    reported = request.query_params.get("reported")
    if reported:
        if reported.lower() == "true":
            queryset = queryset.filter(reported_count__gt=0)
        elif reported.lower() == "false":
            queryset = queryset.filter(reported_count=0)

    data = [serialize_dish_summary(dish) for dish in queryset]
    return Response(data, status=status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsAdminRole])
def list_suspicious_publications(request):
    """
    Lista de publicaciones sospechosas: risk_score >= 60 o reported_count > 0
    o con revision_status que requiere revisión, ordenado por riesgo desc y fecha desc.
    """
    from django.db.models import Q
    queryset = Dish.objects.filter(deleted_at__isnull=True).filter(
        Q(ia_risk_score__gte=60) | 
        Q(reported_count__gt=0) |
        Q(revision_status__in=["requiere_revision", "oculta_temporalmente", "requiere_correccion", "rechazada"])
    ).order_by("-ia_risk_score", "-created_at")
    
    data = [serialize_dish_summary(dish) for dish in queryset]
    return Response(data, status=status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsAdminRole])
def detail_publication(request, dish_id):
    """
    Detalle completo de la publicación con análisis IA y reportes.
    """
    try:
        dish = Dish.objects.get(id=dish_id, deleted_at__isnull=True)
    except Dish.DoesNotExist:
        return Response({"detail": "Publicación no encontrada."}, status=status.HTTP_404_NOT_FOUND)
        
    return Response(serialize_dish_detail(dish), status=status.HTTP_200_OK)


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsAdminRole])
def approve_publication(request, dish_id):
    """
    Aprueba la publicación.
    """
    try:
        dish = Dish.objects.get(id=dish_id, deleted_at__isnull=True)
    except Dish.DoesNotExist:
        return Response({"detail": "Publicación no encontrada."}, status=status.HTTP_404_NOT_FOUND)
        
    comment = request.data.get("comment", "")
    
    dish.revision_status = "aprobada"
    dish.status = "published"
    dish.admin_reviewed_by = request.user
    dish.admin_reviewed_at = timezone.now()
    dish.admin_review_comment = comment
    dish.save(update_fields=[
        "revision_status", "status", "admin_reviewed_by", "admin_reviewed_at", "admin_review_comment"
    ])
    
    # Auditar en MongoDB
    QualityAnalysisService().register_admin_decision(dish.id, request.user.id, "aprobar", comment)
    
    return Response(serialize_dish_detail(dish), status=status.HTTP_200_OK)


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsAdminRole])
def reject_publication(request, dish_id):
    """
    Rechaza la publicación (requiere comentario obligatorio).
    """
    try:
        dish = Dish.objects.get(id=dish_id, deleted_at__isnull=True)
    except Dish.DoesNotExist:
        return Response({"detail": "Publicación no encontrada."}, status=status.HTTP_404_NOT_FOUND)
        
    comment = request.data.get("comment", "").strip()
    if not comment:
        return Response({"detail": "El comentario de rechazo es obligatorio."}, status=status.HTTP_400_BAD_REQUEST)
        
    dish.revision_status = "rechazada"
    dish.status = "paused"
    dish.admin_reviewed_by = request.user
    dish.admin_reviewed_at = timezone.now()
    dish.admin_review_comment = comment
    dish.save(update_fields=[
        "revision_status", "status", "admin_reviewed_by", "admin_reviewed_at", "admin_review_comment"
    ])
    
    # Auditar en MongoDB
    QualityAnalysisService().register_admin_decision(dish.id, request.user.id, "rechazar", comment)
    
    return Response(serialize_dish_detail(dish), status=status.HTTP_200_OK)


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsAdminRole])
def hide_publication(request, dish_id):
    """
    Oculta temporalmente la publicación.
    """
    try:
        dish = Dish.objects.get(id=dish_id, deleted_at__isnull=True)
    except Dish.DoesNotExist:
        return Response({"detail": "Publicación no encontrada."}, status=status.HTTP_404_NOT_FOUND)
        
    comment = request.data.get("comment", "")
    
    dish.revision_status = "oculta_temporalmente"
    dish.status = "paused"
    dish.admin_reviewed_by = request.user
    dish.admin_reviewed_at = timezone.now()
    dish.admin_review_comment = comment
    dish.save(update_fields=[
        "revision_status", "status", "admin_reviewed_by", "admin_reviewed_at", "admin_review_comment"
    ])
    
    # Auditar en MongoDB
    QualityAnalysisService().register_admin_decision(dish.id, request.user.id, "ocultar", comment)
    
    return Response(serialize_dish_detail(dish), status=status.HTTP_200_OK)


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsAdminRole])
def request_correction_publication(request, dish_id):
    """
    Solicita corrección al cocinero (requiere comentario obligatorio).
    """
    try:
        dish = Dish.objects.get(id=dish_id, deleted_at__isnull=True)
    except Dish.DoesNotExist:
        return Response({"detail": "Publicación no encontrada."}, status=status.HTTP_404_NOT_FOUND)
        
    comment = request.data.get("comment", "").strip()
    if not comment:
        return Response({"detail": "El comentario indicando las correcciones es obligatorio."}, status=status.HTTP_400_BAD_REQUEST)
        
    dish.revision_status = "requiere_correccion"
    dish.status = "paused"
    dish.admin_reviewed_by = request.user
    dish.admin_reviewed_at = timezone.now()
    dish.admin_review_comment = comment
    dish.save(update_fields=[
        "revision_status", "status", "admin_reviewed_by", "admin_reviewed_at", "admin_review_comment"
    ])
    
    # Auditar en MongoDB
    QualityAnalysisService().register_admin_decision(dish.id, request.user.id, "solicitar_correccion", comment)
    
    return Response(serialize_dish_detail(dish), status=status.HTTP_200_OK)


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsClienteRole])
def report_publication(request, dish_id):
    """
    Cualquier cliente autenticado puede reportar un plato.
    Incrementa reported_count y re-evalúa calidad.
    """
    try:
        dish = Dish.objects.get(id=dish_id, deleted_at__isnull=True)
    except Dish.DoesNotExist:
        return Response({"detail": "Plato no encontrado."}, status=status.HTTP_404_NOT_FOUND)
        
    reason = request.data.get("reason", "").strip()
    comment = request.data.get("comment", "").strip()
    
    valid_reasons = [choice[0] for choice in PublicationReport.REASON_CHOICES]
    if reason not in valid_reasons:
        return Response({"detail": f"Razón inválida. Debe ser una de: {', '.join(valid_reasons)}"}, status=status.HTTP_400_BAD_REQUEST)
        
    # Crear reporte
    report = PublicationReport.objects.create(
        publication=dish,
        user=request.user,
        reason=reason,
        comment=comment
    )
    
    # Incrementar contador de reportes
    dish.reported_count += 1
    dish.save(update_fields=["reported_count"])
    
    # Re-evalúa calidad
    QualityAnalysisService().analyze_publication_quality(dish)
    
    # Recargar plato para aplicar reglas locales basadas en acumulacion de reportes
    dish.refresh_from_db()
    if dish.reported_count >= 5:
        dish.revision_status = "oculta_temporalmente"
        dish.status = "paused"
        dish.save(update_fields=["revision_status", "status"])
    elif dish.reported_count >= 3:
        if dish.revision_status not in ["oculta_temporalmente", "rechazada"]:
            dish.revision_status = "requiere_revision"
            dish.save(update_fields=["revision_status"])
            
    return Response({
        "message": "Reporte registrado exitosamente.",
        "reported_count": dish.reported_count,
        "revision_status": dish.revision_status
    }, status=status.HTTP_201_CREATED)

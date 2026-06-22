import logging
from uuid import UUID
from datetime import datetime
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from modules.confianza_administracion_seguridad.permissions import IsAdminRole
from modules.gestion_usuarios_acceso_suscripcion.models import UserProfile, DeliveryProfile
from modules.gestion_cocinero.models import ChefProfile, Dish
from modules.pedidos_checkout_pagos.models import Order, OrderItem
from modules.confianza_administracion_seguridad.models import PublicationReport, AnalisisVisualPublicacion
from modules.confianza_administracion_seguridad.services.admin_platform_service import AdminPlatformService, AdminPlatformError
from modules.confianza_administracion_seguridad.services.delivery_driver_admin_service import DeliveryDriverAdminService, DeliveryDriverAdminError
from modules.confianza_administracion_seguridad.services.delivery_active_orders_admin_service import DeliveryActiveOrdersAdminService
from modules.confianza_administracion_seguridad.services.audit_service import AuditService, actor_from_user_id
from modules.confianza_administracion_seguridad.services.ai_audit_service import AIAuditService
from modules.confianza_administracion_seguridad.services.mongo_ai_audit_repository import (
    AI_AUDIT_COLLECTIONS,
    COLLECTION_BY_NAME,
    _public_event,
)


logger = logging.getLogger(__name__)

def serialize_all_chefs():
    chefs = ChefProfile.objects.all().select_related("user").order_by("-created_at")
    result = []
    for c in chefs:
        email = c.user.email if c.user else ""
        first_name = c.user.first_name if c.user else ""
        last_name = c.user.last_name if c.user else ""
        user_id = str(c.user.supabase_user_id) if c.user else ""
        result.append({
            "id": str(c.id),
            "user_id": user_id,
            "business_name": c.business_name,
            "first_name": first_name,
            "last_name": last_name,
            "email": email,
            "specialties": c.specialties,
            "city": getattr(c, 'city', None),
            "address": getattr(c, 'location_address', None),
            "profile_picture": getattr(c, 'profile_image_url', None),
            "kitchen_photos": getattr(c, 'kitchen_photos', []),
            "created_at": c.created_at.isoformat() if hasattr(c, 'created_at') and c.created_at else None,
            "status": c.status,
        })
    return result

def serialize_all_orders():
    orders = Order.objects.all().select_related("client", "chef").prefetch_related("items").order_by("-created_at")
    result = []
    for order in orders:
        items_serialized = []
        for item in order.items.all():
            items_serialized.append({
                "id": item.id,
                "dish_name": item.dish_name_snapshot,
                "quantity": item.quantity,
                "unit_price": float(item.unit_price) if item.unit_price is not None else 0.0,
                "subtotal": float(item.subtotal) if item.subtotal is not None else 0.0,
            })
        
        client_name = ""
        if order.client:
            client_name = f"{order.client.first_name} {order.client.last_name}".strip()
            client_name = client_name or order.client.full_name or order.client.email
            
        chef_name = ""
        if order.chef:
            try:
                chef_prof = ChefProfile.objects.filter(user=order.chef).first()
                if chef_prof and chef_prof.business_name:
                    chef_name = chef_prof.business_name
            except Exception:
                pass
            if not chef_name:
                chef_name = f"{order.chef.first_name} {order.chef.last_name}".strip()
                chef_name = chef_name or order.chef.full_name or order.chef.email

        result.append({
            "id": order.id,
            "status": order.status,
            "order_status": order.status,
            "order_status_label": order.get_status_display() if hasattr(order, 'get_status_display') else order.status,
            "total": float(order.total) if order.total is not None else 0.0,
            "subtotal": float(order.subtotal) if order.subtotal is not None else 0.0,
            "delivery_fee": float(order.delivery_fee) if order.delivery_fee is not None else 0.0,
            "service_fee": float(order.service_fee) if order.service_fee is not None else 0.0,
            "discount_total": float(order.discount_total) if order.discount_total is not None else 0.0,
            "payment_method": order.payment_method,
            "fulfillment_type": order.fulfillment_type,
            "currency": order.currency,
            "notes": order.notes,
            "created_at": order.created_at.isoformat() if order.created_at else None,
            "updated_at": order.updated_at.isoformat() if order.updated_at else None,
            "client_id": str(order.client.supabase_user_id) if order.client else None,
            "client_name": client_name,
            "chef_id": str(order.chef.supabase_user_id) if order.chef else None,
            "chef_name": chef_name,
            "items": items_serialized
        })
    return result

def serialize_dish_detail(dish):
    reports = PublicationReport.objects.filter(publication=dish).order_by("-created_at")
    
    # Safely get visual analysis
    av = None
    try:
        av = dish.analisis_visual
    except Exception:
        pass

    av_serialized = None
    if av:
        av_serialized = {
            "estado": av.estado,
            "riesgo": av.riesgo,
            "nivel_riesgo": av.nivel_riesgo,
            "es_comida": av.es_comida,
            "coincide_con_plato": av.coincide_con_plato,
            "coincidencia": av.coincidencia,
            "parece_generada_por_ia": av.parece_generada_por_ia,
            "probabilidad_ia": av.probabilidad_ia,
            "imagen_generica_o_stock": av.imagen_generica_o_stock,
            "imagen_borrosa_o_baja_calidad": av.imagen_borrosa_o_baja_calidad,
            "contenido_no_apto": av.contenido_no_apto,
            "objetos_detectados": av.objetos_detectados,
            "motivos": av.motivos,
            "motivos_ia": av.motivos_ia,
            "recomendacion": av.recomendacion,
            "accion_sugerida": av.accion_sugerida,
            "proveedor_vision": av.proveedor_vision,
            "proveedor_deteccion_ia": av.proveedor_deteccion_ia,
            "error_controlado": av.error_controlado,
            "detalle_error": av.detalle_error,
            "analizado_en": av.analizado_en.isoformat() if av.analizado_en else None,
            "updated_at": av.updated_at.isoformat() if av.updated_at else None,
        }

    reports_serialized = []
    for r in reports:
        reports_serialized.append({
            "id": r.id,
            "user_id": str(r.user.supabase_user_id),
            "user_email": r.user.email,
            "user_name": r.user.full_name,
            "reason": r.reason,
            "comment": r.comment,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "status": r.status,
        })

    return {
        "id": str(dish.id),
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
        "reports": reports_serialized,
        "analisis_visual": av_serialized,
    }


def serialize_audit_event(event):
    return {
        "id": event.id,
        "event": event.event,
        "details": event.details,
        "at": event.at.isoformat() if event.at else None,
    }


class AdminSyncBootstrapView(APIView):
    permission_classes = [IsAuthenticated, IsAdminRole]

    def get(self, request):
        now_time = timezone.now()
        
        # 1. Users
        users = AdminPlatformService().list_users()
        
        # 2. Chefs (all)
        chefs = serialize_all_chefs()
        
        # 3. Riders/Drivers
        riders = DeliveryDriverAdminService().list_drivers().get("items", [])
        
        # 4. Orders (all orders with details)
        orders_details = serialize_all_orders()
        
        # 5. Publications
        publications = AdminPlatformService().list_publications()
        
        # 6. Fraud & Risk (serialized detailed dishes for quality review)
        fraud_risk_dishes = []
        try:
            queryset = Dish.objects.filter(deleted_at__isnull=True).order_by("-created_at")
            for dish in queryset:
                fraud_risk_dishes.append(serialize_dish_detail(dish))
        except Exception as e:
            logger.error(f"Error fetching quality control dishes: {e}")

        # 7. Audit General (SQL audit logs)
        audit_general_data = []
        try:
            from modules.gestion_usuarios_acceso_suscripcion.models import AuditEvent
            queryset = AuditEvent.objects.all().order_by("-at")[:500]
            audit_general_data = [serialize_audit_event(e) for e in queryset]
        except Exception as e:
            logger.error(f"Error fetching general audit events: {e}")

        # 8. Audit IA (MongoDB Atlas AI audit logs)
        audit_ai_payload = {
            "timeline": [],
            "collections": {}
        }
        try:
            repo = AIAuditService().repository
            db, mongo_status = repo._database_or_none()
            if db is not None and mongo_status.get("available"):
                available = set(db.list_collection_names())
                all_normalized = []
                for col in AI_AUDIT_COLLECTIONS:
                    col_data = []
                    if col in available:
                        definition = COLLECTION_BY_NAME[col]
                        sort_field = definition["date_fields"][0]
                        cursor = db[col].find({}).sort([(sort_field, -1), ("_id", -1)]).limit(500)
                        for doc in cursor:
                            normalized = repo._normalize_doc(col, doc)
                            pub_event = _public_event(normalized, include_details=True)
                            col_data.append(pub_event)
                            all_normalized.append(pub_event)
                    audit_ai_payload["collections"][col] = col_data
                all_normalized.sort(key=lambda x: x.get("timestamp") or x.get("created_at") or "", reverse=True)
                audit_ai_payload["timeline"] = all_normalized
        except Exception as e:
            logger.error(f"Error fetching AI audit events from Mongo for sync bootstrap: {e}")

        payload = {
            "synced_at": now_time.isoformat(),
            "schema_version": "1.0.0",
            "modules": {
                "users": users,
                "chefs": chefs,
                "riders": riders,
                "orders": orders_details,
                "publications": publications,
                "fraud_risk": fraud_risk_dishes,
                "audit_general": audit_general_data,
                "audit_ai": audit_ai_payload,
            }
        }
        return Response(payload, status=status.HTTP_200_OK)


class AdminSyncStatusView(APIView):
    permission_classes = [IsAuthenticated, IsAdminRole]

    def get(self, request):
        return Response({
            "server_time": timezone.now().isoformat(),
            "schema_version": "1.0.0",
            "status": "ready"
        }, status=status.HTTP_200_OK)


class AdminSyncModuleView(APIView):
    permission_classes = [IsAuthenticated, IsAdminRole]

    def get(self, request, module_name):
        module_name = str(module_name).strip().lower()
        
        if module_name == "users":
            data = AdminPlatformService().list_users()
        elif module_name == "chefs":
            data = serialize_all_chefs()
        elif module_name == "riders":
            data = DeliveryDriverAdminService().list_drivers().get("items", [])
        elif module_name == "orders":
            data = serialize_all_orders()
        elif module_name == "publications":
            data = AdminPlatformService().list_publications()
        elif module_name == "fraud_risk":
            data = []
            queryset = Dish.objects.filter(deleted_at__isnull=True).order_by("-created_at")
            for dish in queryset:
                data.append(serialize_dish_detail(dish))
        elif module_name == "audit_general":
            from modules.gestion_usuarios_acceso_suscripcion.models import AuditEvent
            queryset = AuditEvent.objects.all().order_by("-at")[:500]
            data = [serialize_audit_event(e) for e in queryset]
        elif module_name == "audit_ai":
            try:
                repo = AIAuditService().repository
                db, mongo_status = repo._database_or_none()
                audit_ai_payload = {"timeline": [], "collections": {}}
                if db is not None and mongo_status.get("available"):
                    available = set(db.list_collection_names())
                    all_normalized = []
                    for col in AI_AUDIT_COLLECTIONS:
                        col_data = []
                        if col in available:
                            definition = COLLECTION_BY_NAME[col]
                            sort_field = definition["date_fields"][0]
                            cursor = db[col].find({}).sort([(sort_field, -1), ("_id", -1)]).limit(500)
                            for doc in cursor:
                                normalized = repo._normalize_doc(col, doc)
                                pub_event = _public_event(normalized, include_details=True)
                                col_data.append(pub_event)
                                all_normalized.append(pub_event)
                        audit_ai_payload["collections"][col] = col_data
                    all_normalized.sort(key=lambda x: x.get("timestamp") or x.get("created_at") or "", reverse=True)
                    audit_ai_payload["timeline"] = all_normalized
                data = audit_ai_payload
            except Exception as e:
                logger.error(f"Error fetching AI audit events from Mongo for module sync: {e}")
                data = {"timeline": [], "collections": {}}
        else:
            return Response({"detail": f"Modulo '{module_name}' no soportado."}, status=status.HTTP_400_BAD_REQUEST)
            
        return Response({
            "module": module_name,
            "synced_at": timezone.now().isoformat(),
            "data": data
        }, status=status.HTTP_200_OK)


class AdminSyncPushView(APIView):
    permission_classes = [IsAuthenticated, IsAdminRole]

    def post(self, request):
        payload = request.data
        device_id = payload.get("device_id")
        last_sync_str = payload.get("last_sync")
        last_sync = parse_datetime(last_sync_str) if last_sync_str else None
        
        operations = payload.get("operations", [])
        synced = []
        errors = []
        conflicts = []

        actor = request.user if isinstance(request.user, UserProfile) else None
        if not actor and request.user and getattr(request.user, "id", None):
            try:
                actor = UserProfile.objects.get(supabase_user_id=request.user.id)
            except (UserProfile.DoesNotExist, ValueError):
                pass

        for op in operations:
            op_id = op.get("operation_id")
            entity = op.get("entity")
            action = op.get("action")
            local_id = op.get("local_id")
            server_id = op.get("server_id")
            op_payload = op.get("payload") or {}
            client_updated_at_str = op.get("updated_at")
            client_updated_at = parse_datetime(client_updated_at_str) if client_updated_at_str else None

            try:
                # User block/unblock conflict detection and action
                if entity == "users" and action == "toggle_block":
                    user = UserProfile.objects.filter(id=server_id).first()
                    if not user:
                        raise ValueError("Usuario no encontrado")
                    
                    # Conflict check: did someone change is_active after last_sync/client_updated_at?
                    if client_updated_at and user.updated_at > client_updated_at:
                        # Conflict if server value is different from client expectation
                        expected_is_active = op_payload.get("is_active")
                        if user.is_active != expected_is_active:
                            conflicts.append({
                                "operation_id": op_id,
                                "entity": entity,
                                "local_id": local_id,
                                "server_id": server_id,
                                "reason": "SERVER_VERSION_NEWER",
                                "server_data": {
                                    "id": str(user.id),
                                    "email": user.email,
                                    "is_active": user.is_active,
                                    "updated_at": user.updated_at.isoformat()
                                },
                                "client_data": op_payload
                            })
                            continue
                    
                    # Apply action
                    # Avoid calling toggle_user_block if it is already in that state
                    target_is_active = op_payload.get("is_active")
                    if user.is_active != target_is_active:
                        AdminPlatformService().toggle_user_block(server_id, actor_user_id=str(actor.supabase_user_id) if actor else "", request=request)
                    
                    synced.append({
                        "operation_id": op_id,
                        "entity": entity,
                        "local_id": local_id,
                        "server_id": server_id,
                        "status": "synced"
                    })

                # Validate Chef validation status
                elif entity == "chefs" and action == "validate":
                    chef = ChefProfile.objects.filter(id=server_id).first()
                    if not chef:
                        raise ValueError("Cocinero no encontrado")
                    
                    # Conflict check
                    if client_updated_at and chef.updated_at > client_updated_at:
                        expected_status = op_payload.get("status")
                        if chef.status != expected_status:
                            conflicts.append({
                                "operation_id": op_id,
                                "entity": entity,
                                "local_id": local_id,
                                "server_id": server_id,
                                "reason": "SERVER_VERSION_NEWER",
                                "server_data": {
                                    "id": str(chef.id),
                                    "status": chef.status,
                                    "updated_at": chef.updated_at.isoformat()
                                },
                                "client_data": op_payload
                            })
                            continue
                    
                    target_status = op_payload.get("status")
                    if chef.status != target_status:
                        AdminPlatformService().validate_chef(server_id, target_status, actor_user_id=str(actor.supabase_user_id) if actor else "", request=request)
                    
                    synced.append({
                        "operation_id": op_id,
                        "entity": entity,
                        "local_id": local_id,
                        "server_id": server_id,
                        "status": "synced"
                    })

                # Validate Delivery Drivers status
                elif entity == "riders" and action == "update_status":
                    profile = DeliveryProfile.objects.filter(user__supabase_user_id=server_id).first()
                    if not profile:
                        raise ValueError("Repartidor no encontrado")
                    
                    if client_updated_at and profile.updated_at > client_updated_at:
                        expected_status = op_payload.get("approval_status")
                        if profile.approval_status != expected_status:
                            conflicts.append({
                                "operation_id": op_id,
                                "entity": entity,
                                "local_id": local_id,
                                "server_id": server_id,
                                "reason": "SERVER_VERSION_NEWER",
                                "server_data": {
                                    "user_id": server_id,
                                    "approval_status": profile.approval_status,
                                    "updated_at": profile.updated_at.isoformat()
                                },
                                "client_data": op_payload
                            })
                            continue
                    
                    target_status = op_payload.get("approval_status")
                    if profile.approval_status != target_status:
                        DeliveryDriverAdminService().update_status(
                            str(actor.supabase_user_id) if actor else "",
                            server_id,
                            target_status,
                            request=request
                        )
                    
                    synced.append({
                        "operation_id": op_id,
                        "entity": entity,
                        "local_id": local_id,
                        "server_id": server_id,
                        "status": "synced"
                    })

                # Moderate publications
                elif entity == "publications" and action == "action":
                    dish = Dish.objects.filter(id=server_id).first()
                    if not dish:
                        raise ValueError("Plato no encontrado")
                    
                    if client_updated_at and dish.updated_at > client_updated_at:
                        # Conflict check
                        expected_status = op_payload.get("status")
                        expected_deleted_at = op_payload.get("deleted_at")
                        if dish.status != expected_status or (bool(dish.deleted_at) != bool(expected_deleted_at)):
                            conflicts.append({
                                "operation_id": op_id,
                                "entity": entity,
                                "local_id": local_id,
                                "server_id": server_id,
                                "reason": "SERVER_VERSION_NEWER",
                                "server_data": {
                                    "id": str(dish.id),
                                    "status": dish.status,
                                    "deleted_at": dish.deleted_at.isoformat() if dish.deleted_at else None,
                                    "updated_at": dish.updated_at.isoformat()
                                },
                                "client_data": op_payload
                            })
                            continue
                    
                    target_action = op_payload.get("action")
                    AdminPlatformService().toggle_publication_action(
                        server_id,
                        target_action,
                        actor_user_id=str(actor.supabase_user_id) if actor else "",
                        request=request
                    )
                    
                    synced.append({
                        "operation_id": op_id,
                        "entity": entity,
                        "local_id": local_id,
                        "server_id": server_id,
                        "status": "synced"
                    })

                # Fraud/Quality publications review actions
                elif entity == "quality_publications" and action == "action":
                    dish = Dish.objects.filter(id=server_id).first()
                    if not dish:
                        raise ValueError("Publicacion no encontrada")
                    
                    if client_updated_at and dish.updated_at > client_updated_at:
                        expected_status = op_payload.get("revision_status")
                        if dish.revision_status != expected_status:
                            conflicts.append({
                                "operation_id": op_id,
                                "entity": entity,
                                "local_id": local_id,
                                "server_id": server_id,
                                "reason": "SERVER_VERSION_NEWER",
                                "server_data": {
                                    "id": str(dish.id),
                                    "revision_status": dish.revision_status,
                                    "updated_at": dish.updated_at.isoformat()
                                },
                                "client_data": op_payload
                            })
                            continue
                    
                    action_type = op_payload.get("action_type") # approve, reject, hide, request-correction
                    comment = op_payload.get("comment", "")
                    
                    old_values = {"revision_status": dish.revision_status}
                    
                    # Map action strings
                    mapped_status = None
                    description = ""
                    if action_type == "aprobar" or action_type == "approve":
                        mapped_status = "aprobada"
                        description = f"Administrador aprobo la publicacion. Comentario: {comment}"
                    elif action_type == "rechazar" or action_type == "reject":
                        mapped_status = "rechazada"
                        description = f"Administrador rechazo la publicacion. Comentario: {comment}"
                    elif action_type == "ocultar" or action_type == "hide":
                        mapped_status = "oculta_temporalmente"
                        description = f"Administrador oculto la publicacion. Comentario: {comment}"
                    elif action_type == "solicitar-correccion" or action_type == "request-correction" or action_type == "corregir":
                        mapped_status = "requiere_correccion"
                        description = f"Administrador solicito correccion para la publicacion. Comentario: {comment}"
                    else:
                        raise ValueError(f"Accion de moderacion invalida: {action_type}")
                    
                    dish.revision_status = mapped_status
                    dish.admin_reviewed_by = actor
                    dish.admin_reviewed_at = timezone.now()
                    dish.admin_review_comment = comment
                    dish.save(update_fields=[
                        "revision_status", "admin_reviewed_by", "admin_reviewed_at", "admin_review_comment", "updated_at"
                    ])
                    
                    # Log event
                    AuditService().log_event(
                        event_type="PUBLICATION_ADMIN_REVIEWED",
                        event_category="publications",
                        action=action_type,
                        entity_type="publication",
                        entity_id=str(dish.id),
                        actor=actor,
                        target_user_id=str(dish.chef.supabase_user_id) if getattr(dish, "chef", None) else "",
                        target_role=getattr(getattr(dish, "chef", None), "role", ""),
                        description=description,
                        old_values=old_values,
                        new_values={"revision_status": dish.revision_status, "admin_review_comment": comment},
                        request=request,
                        severity="warning" if action_type in ["rechazar", "ocultar"] else "info",
                    )
                    
                    synced.append({
                        "operation_id": op_id,
                        "entity": entity,
                        "local_id": local_id,
                        "server_id": server_id,
                        "status": "synced"
                    })

                elif entity == "quality_publications" and action == "delete_permanent":
                    dish = Dish.objects.filter(id=server_id).first()
                    if dish:
                        dish.delete()
                        
                        AuditService().log_event(
                            event_type="PUBLICATION_ADMIN_REVIEWED",
                            event_category="publications",
                            action="delete_permanent",
                            entity_type="publication",
                            entity_id=str(server_id),
                            actor=actor,
                            description=f"Administrador elimino definitivamente la publicacion {server_id}.",
                            request=request,
                            severity="danger",
                        )
                    
                    synced.append({
                        "operation_id": op_id,
                        "entity": entity,
                        "local_id": local_id,
                        "server_id": server_id,
                        "status": "synced"
                    })

                elif entity == "orders" and action == "update_status":
                    order = Order.objects.filter(id=server_id).first()
                    if not order:
                        raise ValueError("Pedido no encontrado")
                    target_status = op_payload.get("status")
                    order.status = target_status
                    order.save(update_fields=["status", "updated_at"])
                    synced.append({
                        "operation_id": op_id,
                        "entity": entity,
                        "local_id": local_id,
                        "server_id": server_id,
                        "status": "synced"
                    })

                elif entity == "orders" and action == "reassign_rider":
                    order = Order.objects.filter(id=server_id).first()
                    if not order:
                        raise ValueError("Pedido no encontrado")
                    rider_id = op_payload.get("rider_id")
                    rider_user = UserProfile.objects.filter(supabase_user_id=rider_id).first()
                    if not rider_user:
                        raise ValueError("Repartidor no encontrado")
                    
                    assignment = getattr(order, "delivery_assignment", None)
                    if assignment:
                        assignment.delivery_user = rider_user
                        assignment.status = "ASSIGNED"
                        assignment.save(update_fields=["delivery_user", "status", "updated_at"])
                    synced.append({
                        "operation_id": op_id,
                        "entity": entity,
                        "local_id": local_id,
                        "server_id": server_id,
                        "status": "synced"
                    })

                elif entity == "orders" and action == "register_incident":
                    order = Order.objects.filter(id=server_id).first()
                    if not order:
                        raise ValueError("Pedido no encontrado")
                    incident_text = op_payload.get("incident")
                    
                    assignment = getattr(order, "delivery_assignment", None)
                    if assignment:
                        from modules.delivery_logistica.models import DeliveryAssignmentStatusHistory
                        DeliveryAssignmentStatusHistory.objects.create(
                            assignment=assignment,
                            from_status=assignment.status,
                            to_status=assignment.status,
                            actor_role="ADMINISTRADOR",
                            actor_id=str(actor.supabase_user_id) if actor else "",
                            notes=f"INCIDENCIA: {incident_text}",
                            occurred_at=timezone.now()
                        )
                    synced.append({
                        "operation_id": op_id,
                        "entity": entity,
                        "local_id": local_id,
                        "server_id": server_id,
                        "status": "synced"
                    })

                elif entity == "orders" and action == "register_observation":
                    order = Order.objects.filter(id=server_id).first()
                    if not order:
                        raise ValueError("Pedido no encontrado")
                    observation_text = op_payload.get("observation")
                    
                    assignment = getattr(order, "delivery_assignment", None)
                    if assignment:
                        from modules.delivery_logistica.models import DeliveryAssignmentStatusHistory
                        DeliveryAssignmentStatusHistory.objects.create(
                            assignment=assignment,
                            from_status=assignment.status,
                            to_status=assignment.status,
                            actor_role="ADMINISTRADOR",
                            actor_id=str(actor.supabase_user_id) if actor else "",
                            notes=f"OBSERVACION: {observation_text}",
                            occurred_at=timezone.now()
                        )
                    synced.append({
                        "operation_id": op_id,
                        "entity": entity,
                        "local_id": local_id,
                        "server_id": server_id,
                        "status": "synced"
                    })
                    
                else:
                    raise ValueError(f"Operacion desconocida: {entity}:{action}")

            except Exception as e:
                logger.error(f"Error processing offline operation {op_id}: {e}")
                errors.append({
                    "operation_id": op_id,
                    "entity": entity,
                    "local_id": local_id,
                    "server_id": server_id,
                    "error": str(e)
                })

        return Response({
            "synced": synced,
            "errors": errors,
            "conflicts": conflicts,
            "server_time": timezone.now().isoformat()
        }, status=status.HTTP_200_OK)

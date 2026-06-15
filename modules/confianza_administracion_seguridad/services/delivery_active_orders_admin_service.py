from uuid import UUID

from modules.delivery_logistica.services.delivery_assignment_engine import DeliveryAssignmentEngine
from modules.delivery_logistica.services.delivery_tracking_service import DeliveryTrackingService
from modules.gestion_cocinero.models import ChefProfile
from modules.gestion_usuarios_acceso_suscripcion.models import UserProfile
from modules.pedidos_checkout_pagos.models import Order


class DeliveryActiveOrdersAdminError(ValueError):
    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


class DeliveryActiveOrdersAdminService:
    ACTIVE_ORDER_STATUSES = {
        Order.Status.READY_FOR_DELIVERY,
        Order.Status.OUT_FOR_DELIVERY,
    }

    def __init__(self):
        self.assignment_engine = DeliveryAssignmentEngine()
        self.tracking_service = DeliveryTrackingService()

    def list_active_orders(self, actor_user_id: str):
        self._require_admin(actor_user_id)
        self.assignment_engine.sync_open_assignments()
        queryset = (
            Order.objects.filter(
                fulfillment_type=Order.FulfillmentType.DELIVERY,
                status__in=list(self.ACTIVE_ORDER_STATUSES),
            )
            .select_related("client", "chef", "address", "delivery_assignment", "delivery_assignment__delivery_user")
            .prefetch_related("delivery_assignment__status_history", "delivery_assignment__location_pings", "delivery_assignment__route_snapshots")
            .order_by("-updated_at")
        )
        items = [self._serialize_summary(order) for order in queryset]
        return {
            "items": items,
            "summary": {
                "total": len(items),
                "ready_for_delivery": sum(1 for item in items if item["order_status"] == Order.Status.READY_FOR_DELIVERY),
                "out_for_delivery": sum(1 for item in items if item["order_status"] == Order.Status.OUT_FOR_DELIVERY),
                "assigned": sum(1 for item in items if item["delivery_assignment"]["delivery_user"]),
                "unassigned": sum(1 for item in items if not item["delivery_assignment"]["delivery_user"]),
            },
        }

    def get_active_order_detail(self, actor_user_id: str, order_id: str):
        self._require_admin(actor_user_id)
        order = (
            Order.objects.filter(
                id=str(order_id),
                fulfillment_type=Order.FulfillmentType.DELIVERY,
                status__in=list(self.ACTIVE_ORDER_STATUSES),
            )
            .select_related("client", "chef", "address", "delivery_assignment", "delivery_assignment__delivery_user")
            .prefetch_related(
                "items",
                "payments",
                "delivery_assignment__status_history",
                "delivery_assignment__location_pings",
                "delivery_assignment__route_snapshots",
            )
            .first()
        )
        if not order:
            raise DeliveryActiveOrdersAdminError("order_not_found", "Pedido delivery activo no encontrado.")
        if getattr(order, "delivery_assignment", None):
            self.assignment_engine.ensure_assignment_up_to_date(
                order.delivery_assignment,
                reason="admin_detail_sync",
                actor_role="SISTEMA",
                actor_id="",
            )
            order.refresh_from_db()
        return {"order": self._serialize_detail(order)}

    def _serialize_summary(self, order: Order):
        assignment = getattr(order, "delivery_assignment", None)
        map_payload = self.tracking_service.ensure_map_payload(assignment) if assignment else None
        context = self._assignment_context(assignment, map_payload)
        return {
            "order_id": order.id,
            "order_status": order.status,
            "order_status_label": self._order_status_label(order.status),
            "client_name": self._profile_name(order.client),
            "chef_name": self._chef_name(order.chef),
            "last_updated_at": max(order.updated_at, assignment.updated_at if assignment else order.updated_at).isoformat(),
            "delivery_assignment": {
                "id": assignment.id if assignment else "",
                "status": assignment.status if assignment else "",
                "status_label": self._delivery_status_label(assignment.status) if assignment else "",
                "delivery_user": {
                    "id": str(assignment.delivery_user.supabase_user_id),
                    "name": self._profile_name(assignment.delivery_user),
                } if assignment and assignment.delivery_user else None,
                "estimated_distance_meters": context["estimated_distance_meters"],
                "estimated_distance_human": context["estimated_distance_human"],
                "attempt_count": context["attempt_count"],
                "strategy": context["strategy"],
            },
        }

    def _serialize_detail(self, order: Order):
        assignment = getattr(order, "delivery_assignment", None)
        map_payload = self.tracking_service.ensure_map_payload(assignment) if assignment else None
        context = self._assignment_context(assignment, map_payload)
        payment = order.payments.order_by("-created_at").first()
        return {
            "id": order.id,
            "status": order.status,
            "status_label": self._order_status_label(order.status),
            "total": float(order.total),
            "payment_method": order.payment_method,
            "payment_status": payment.status if payment else "",
            "client": {
                "id": str(order.client.supabase_user_id),
                "name": self._profile_name(order.client),
            },
            "chef": {
                "id": str(order.chef.supabase_user_id),
                "name": self._chef_name(order.chef),
            },
            "address": {
                "label": order.address.label if getattr(order, "address", None) else "",
                "line_1": order.address.line_1 if getattr(order, "address", None) else "",
                "reference": order.address.reference if getattr(order, "address", None) else "",
                "contact_name": order.address.contact_name if getattr(order, "address", None) else "",
                "contact_phone": order.address.contact_phone if getattr(order, "address", None) else "",
            } if getattr(order, "address", None) else None,
            "items": [
                {
                    "id": item.id,
                    "dish_name": item.dish_name_snapshot,
                    "quantity": item.quantity,
                    "unit_price": float(item.unit_price),
                    "subtotal": float(item.subtotal),
                }
                for item in order.items.all()
            ],
            "delivery_assignment": {
                "id": assignment.id if assignment else "",
                "status": assignment.status if assignment else "",
                "status_label": self._delivery_status_label(assignment.status) if assignment else "",
                "assigned_at": assignment.assigned_at.isoformat() if assignment and assignment.assigned_at else None,
                "delivery_user": {
                    "id": str(assignment.delivery_user.supabase_user_id),
                    "name": self._profile_name(assignment.delivery_user),
                } if assignment and assignment.delivery_user else None,
                "operational_context": context,
                "history": [
                    {
                        "from_status": row.from_status,
                        "to_status": row.to_status,
                        "actor_role": row.actor_role,
                        "actor_id": row.actor_id,
                        "notes": row.notes,
                        "metadata": row.metadata or {},
                        "occurred_at": row.occurred_at.isoformat(),
                    }
                    for row in assignment.status_history.all().order_by("-occurred_at")
                ] if assignment else [],
                "map": map_payload,
            },
        }

    def _assignment_context(self, assignment, map_payload):
        metadata = dict(getattr(assignment, "metadata", {}) or {})
        estimated_distance_meters = metadata.get("assigned_distance_meters")
        estimated_distance_human = metadata.get("assigned_distance_human") or ""
        if estimated_distance_meters is None and map_payload and map_payload.get("route"):
            estimated_distance_meters = map_payload["route"].get("distance_meters")
            estimated_distance_human = map_payload.get("navigation", {}).get("summary", {}).get("distance_human", "")
        return {
            "attempt_count": int(metadata.get("assignment_attempt_count", 0) or 0),
            "last_attempt_at": metadata.get("assignment_last_attempt_at"),
            "strategy": metadata.get("assignment_strategy", ""),
            "candidate_snapshot": metadata.get("candidate_snapshot") or [],
            "reassignment_history": metadata.get("reassignment_history") or [],
            "estimated_distance_meters": float(estimated_distance_meters) if estimated_distance_meters is not None else None,
            "estimated_distance_human": estimated_distance_human,
            "last_result": metadata.get("assignment_last_result", ""),
        }

    def _require_admin(self, user_id: str):
        try:
            parsed = UUID(str(user_id))
        except (TypeError, ValueError):
            raise DeliveryActiveOrdersAdminError("admin_required", "Solo un administrador puede acceder a pedidos delivery activos.")
        user = UserProfile.objects.filter(supabase_user_id=parsed).first()
        if not user or user.role != UserProfile.ROLE_ADMIN:
            raise DeliveryActiveOrdersAdminError("admin_required", "Solo un administrador puede acceder a pedidos delivery activos.")
        return user

    def _profile_name(self, profile: UserProfile | None):
        if not profile:
            return ""
        name = f"{profile.first_name} {profile.last_name}".strip()
        return name or profile.full_name or profile.email

    def _chef_name(self, chef: UserProfile):
        profile = ChefProfile.objects.filter(user=chef).first()
        return profile.business_name if profile and profile.business_name else self._profile_name(chef)

    def _order_status_label(self, value: str):
        labels = {
            Order.Status.READY_FOR_DELIVERY: "Listo para delivery",
            Order.Status.OUT_FOR_DELIVERY: "En camino",
        }
        return labels.get(value, value)

    def _delivery_status_label(self, value: str):
        labels = {
            "UNASSIGNED": "Sin asignar",
            "ASSIGNED": "Asignado",
            "AT_CHEF": "En punto de recogida",
            "PICKED_UP": "Pedido recogido",
            "EN_ROUTE_TO_CLIENT": "En ruta al cliente",
            "DELIVERED": "Entregado",
        }
        return labels.get(value, value)

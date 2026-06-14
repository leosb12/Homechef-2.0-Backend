from uuid import UUID

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from modules.confianza_administracion_seguridad.services import NotificationService
from modules.delivery_logistica.models import (
    DeliveryAssignment,
    DeliveryStatusHistory,
)
from modules.gestion_cocinero.models import ChefProfile
from modules.gestion_usuarios_acceso_suscripcion.models import UserProfile
from modules.pedidos_checkout_pagos.models import (
    Order,
    OrderPayment,
    OrderPaymentEvent,
    OrderStatusHistory,
    OrderTimelineEvent,
)
from .delivery_tracking_service import DeliveryTrackingService
from .delivery_incident_service import DeliveryIncidentService


class DeliveryLogisticsError(ValueError):
    def __init__(self, message: str, code: str, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}


class DeliveryOperationsService:
    PRE_PICKUP_STATUSES = {
        DeliveryAssignment.Status.UNASSIGNED,
        DeliveryAssignment.Status.ASSIGNED,
        DeliveryAssignment.Status.AT_CHEF,
    }
    ACTIVE_STATUSES = {
        DeliveryAssignment.Status.PICKED_UP,
        DeliveryAssignment.Status.EN_ROUTE_TO_CLIENT,
    }

    def __init__(self):
        self.tracking_service = DeliveryTrackingService()
        self.incident_service = DeliveryIncidentService()
        self.notification_service = NotificationService()

    def list_assigned(self, user_id: str):
        delivery = self._require_delivery(user_id)
        assignments = (
            DeliveryAssignment.objects.filter(
                order__fulfillment_type=Order.FulfillmentType.DELIVERY,
                status__in=list(self.PRE_PICKUP_STATUSES),
            )
            .filter(Q(delivery_user__isnull=True) | Q(delivery_user=delivery))
            .select_related(
                "delivery_user",
                "order",
                "order__client",
                "order__chef",
                "order__address",
            )
            .prefetch_related(
                "status_history",
                "incidents",
                "location_pings",
                "route_snapshots",
                "order__items",
                "order__payments",
            )
            .order_by("-created_at")
        )
        return {
            "items": [
                self._serialize_assignment(assignment, delivery)
                for assignment in assignments
            ]
        }

    def list_active(self, user_id: str):
        delivery = self._require_delivery(user_id)
        assignments = (
            DeliveryAssignment.objects.filter(
                order__fulfillment_type=Order.FulfillmentType.DELIVERY,
                delivery_user=delivery,
                status__in=list(self.ACTIVE_STATUSES),
            )
            .select_related(
                "delivery_user",
                "order",
                "order__client",
                "order__chef",
                "order__address",
            )
            .prefetch_related(
                "status_history",
                "incidents",
                "location_pings",
                "route_snapshots",
                "order__items",
                "order__payments",
            )
            .order_by("-updated_at")
        )
        return {
            "items": [
                self._serialize_assignment(assignment, delivery)
                for assignment in assignments
            ]
        }

    def get_detail(self, user_id: str, assignment_id: str):
        delivery = self._require_delivery(user_id)
        assignment = self._get_assignment_for_delivery(
            delivery,
            assignment_id,
            allow_unassigned=True,
        )
        return {"assignment": self._serialize_assignment(assignment, delivery, detailed=True)}

    @transaction.atomic
    def accept_assignment(self, user_id: str, assignment_id: str):
        delivery = self._require_delivery(user_id)
        assignment = self._get_assignment_for_delivery(
            delivery,
            assignment_id,
            lock=True,
            allow_unassigned=True,
        )
        if assignment.delivery_user_id and assignment.delivery_user_id != delivery.id:
            raise DeliveryLogisticsError(
                "La entrega ya fue tomada por otro repartidor.",
                "assignment_already_taken",
            )
        if assignment.status != DeliveryAssignment.Status.UNASSIGNED:
            raise DeliveryLogisticsError(
                "La entrega no se puede tomar en su estado actual.",
                "transition_not_allowed",
            )
        if assignment.order.status != Order.Status.READY_FOR_DELIVERY:
            raise DeliveryLogisticsError(
                "El pedido aun no esta listo para ser recogido.",
                "order_not_ready_for_delivery",
            )
        previous = assignment.status
        assignment.delivery_user = delivery
        assignment.assigned_at = assignment.assigned_at or timezone.now()
        assignment.status = DeliveryAssignment.Status.ASSIGNED
        assignment.metadata = {
            **(assignment.metadata or {}),
            "assigned_delivery_user_id": str(delivery.supabase_user_id),
        }
        assignment.save(update_fields=["delivery_user", "assigned_at", "status", "metadata", "updated_at"])
        DeliveryStatusHistory.objects.create(
            assignment=assignment,
            from_status=previous,
            to_status=assignment.status,
            actor_role="REPARTIDOR",
            actor_id=str(delivery.supabase_user_id),
            notes="Entrega aceptada por repartidor",
            metadata={"order_id": assignment.order_id},
        )
        OrderTimelineEvent.objects.create(
            order=assignment.order,
            event_code="DELIVERY_ACCEPTED",
            event_label="Repartidor asignado al pedido",
            actor_role="REPARTIDOR",
            actor_id=str(delivery.supabase_user_id),
            metadata={"assignment_id": assignment.id},
        )
        self.tracking_service.refresh_current_route(assignment)
        assignment.refresh_from_db()
        self.notification_service.notify_delivery_assigned(assignment)
        return {"assignment": self._serialize_assignment(assignment, delivery, detailed=True)}

    @transaction.atomic
    def arrived_chef(self, user_id: str, assignment_id: str):
        delivery = self._require_delivery(user_id)
        assignment = self._get_owned_assignment(delivery, assignment_id, lock=True)
        if assignment.status != DeliveryAssignment.Status.ASSIGNED:
            raise DeliveryLogisticsError(
                "La entrega no puede marcarse como llegada al cocinero en su estado actual.",
                "transition_not_allowed",
            )
        self._transition_assignment(
            assignment,
            DeliveryAssignment.Status.AT_CHEF,
            delivery,
            notes="Repartidor en punto de recogida",
        )
        OrderTimelineEvent.objects.create(
            order=assignment.order,
            event_code="DELIVERY_ARRIVED_CHEF",
            event_label="Repartidor llego al cocinero",
            actor_role="REPARTIDOR",
            actor_id=str(delivery.supabase_user_id),
            metadata={"assignment_id": assignment.id},
        )
        self.tracking_service.refresh_current_route(assignment)
        assignment.refresh_from_db()
        return {"assignment": self._serialize_assignment(assignment, delivery, detailed=True)}

    @transaction.atomic
    def picked_up(self, user_id: str, assignment_id: str):
        delivery = self._require_delivery(user_id)
        assignment = self._get_owned_assignment(delivery, assignment_id, lock=True)
        if assignment.status not in {
            DeliveryAssignment.Status.ASSIGNED,
            DeliveryAssignment.Status.AT_CHEF,
        }:
            raise DeliveryLogisticsError(
                "La entrega no puede marcarse como recogida en su estado actual.",
                "transition_not_allowed",
            )
        self._transition_assignment(
            assignment,
            DeliveryAssignment.Status.PICKED_UP,
            delivery,
            notes="Pedido recogido por repartidor",
        )
        self._move_order_status_if_needed(
            assignment.order,
            Order.Status.OUT_FOR_DELIVERY,
            "REPARTIDOR",
            str(delivery.supabase_user_id),
            "Pedido recogido e iniciado en ruta al cliente",
            "DELIVERY_PICKED_UP",
            "Pedido recogido por repartidor",
        )
        self.tracking_service.refresh_current_route(assignment)
        assignment.refresh_from_db()
        self.notification_service.notify_order_picked_up(assignment.order, assignment)
        return {"assignment": self._serialize_assignment(assignment, delivery, detailed=True)}

    @transaction.atomic
    def delivered(self, user_id: str, assignment_id: str):
        delivery = self._require_delivery(user_id)
        assignment = self._get_owned_assignment(delivery, assignment_id, lock=True)
        if assignment.status not in self.ACTIVE_STATUSES:
            raise DeliveryLogisticsError(
                "La entrega no puede cerrarse en su estado actual.",
                "transition_not_allowed",
            )
        payment = self._latest_payment(assignment.order)
        if (
            payment
            and payment.method == Order.PaymentMethod.CASH
            and payment.status == OrderPayment.Status.PENDING
        ):
            self._confirm_cash_payment(
                payment,
                delivery,
                metadata={
                    "order_id": assignment.order_id,
                    "assignment_id": assignment.id,
                    "fulfillment_type": assignment.order.fulfillment_type,
                },
            )
        self._transition_assignment(
            assignment,
            DeliveryAssignment.Status.DELIVERED,
            delivery,
            notes="Pedido entregado al cliente",
            delivered=True,
        )
        self._move_order_status_if_needed(
            assignment.order,
            Order.Status.DELIVERED,
            "REPARTIDOR",
            str(delivery.supabase_user_id),
            "Pedido entregado al cliente",
            "ORDER_DELIVERED",
            "Pedido entregado",
        )
        self.tracking_service.refresh_current_route(assignment)
        assignment.refresh_from_db()
        if payment and payment.method == Order.PaymentMethod.CASH:
            self.notification_service.notify_payment_confirmed(assignment.order, payment)
        self.notification_service.notify_order_delivered(assignment.order, assignment)
        return {"assignment": self._serialize_assignment(assignment, delivery, detailed=True)}

    def _serialize_assignment(self, assignment: DeliveryAssignment, viewer: UserProfile, detailed: bool = False):
        order = assignment.order
        payment = self._latest_payment(order)
        map_payload = self.tracking_service.ensure_map_payload(assignment)
        incident_payload = self.incident_service.incident_summary(assignment, viewer_role="REPARTIDOR")
        payload = {
            "id": assignment.id,
            "status": assignment.status,
            "status_label": self._status_label(assignment.status),
            "available_actions": self._available_actions(assignment, viewer),
            "assigned_at": assignment.assigned_at.isoformat() if assignment.assigned_at else None,
            "picked_up_at": assignment.picked_up_at.isoformat() if assignment.picked_up_at else None,
            "delivered_at": assignment.delivered_at.isoformat() if assignment.delivered_at else None,
            "delivery_user": {
                "id": str(assignment.delivery_user.supabase_user_id),
                "name": self._profile_name(assignment.delivery_user),
            } if assignment.delivery_user else None,
            "order": {
                "id": order.id,
                "status": order.status,
                "status_label": self._order_status_label(order.status),
                "payment_method": order.payment_method,
                "payment_status": payment.status if payment else "",
                "payment_status_label": self._payment_status_label(payment.status if payment else ""),
                "total": float(order.total),
                "fulfillment_type": order.fulfillment_type,
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
                    "contact_name": order.address.contact_name if getattr(order, "address", None) else "",
                    "contact_phone": order.address.contact_phone if getattr(order, "address", None) else "",
                    "line_1": order.address.line_1 if getattr(order, "address", None) else "",
                    "reference": order.address.reference if getattr(order, "address", None) else "",
                    "latitude": order.address.latitude if getattr(order, "address", None) else None,
                    "longitude": order.address.longitude if getattr(order, "address", None) else None,
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
            },
            "current_location": map_payload.get("current_location"),
            "map": map_payload,
            "incidents": incident_payload,
        }
        if detailed:
            payload["history"] = [
                {
                    "from_status": row.from_status,
                    "to_status": row.to_status,
                    "actor_role": row.actor_role,
                    "actor_id": row.actor_id,
                    "notes": row.notes,
                    "occurred_at": row.occurred_at.isoformat(),
                }
                for row in assignment.status_history.all().order_by("-occurred_at")
            ]
        return payload

    def _available_actions(self, assignment: DeliveryAssignment, viewer: UserProfile):
        has_blocking_open_incident = self.incident_service.has_blocking_open_incident(assignment)
        if assignment.status == DeliveryAssignment.Status.UNASSIGNED:
            return ["accept"]
        if assignment.delivery_user_id != viewer.id:
            return []
        if assignment.status == DeliveryAssignment.Status.ASSIGNED:
            return ["arrived_chef", "picked_up"]
        if assignment.status == DeliveryAssignment.Status.AT_CHEF:
            return ["picked_up"]
        if assignment.status in self.ACTIVE_STATUSES:
            if has_blocking_open_incident:
                return []
            return ["delivered"]
        return []

    def _transition_assignment(
        self,
        assignment: DeliveryAssignment,
        to_status: str,
        delivery: UserProfile,
        notes: str,
        delivered: bool = False,
    ):
        previous = assignment.status
        assignment.status = to_status
        if delivered and not assignment.delivered_at:
            assignment.delivered_at = timezone.now()
        if to_status == DeliveryAssignment.Status.PICKED_UP and not assignment.picked_up_at:
            assignment.picked_up_at = timezone.now()
        update_fields = ["status", "updated_at"]
        if delivered:
            update_fields.append("delivered_at")
        if to_status == DeliveryAssignment.Status.PICKED_UP:
            update_fields.append("picked_up_at")
        assignment.save(update_fields=update_fields)
        DeliveryStatusHistory.objects.create(
            assignment=assignment,
            from_status=previous,
            to_status=to_status,
            actor_role="REPARTIDOR",
            actor_id=str(delivery.supabase_user_id),
            notes=notes,
            metadata={"order_id": assignment.order_id},
        )

    def _move_order_status_if_needed(
        self,
        order: Order,
        to_status: str,
        actor_role: str,
        actor_id: str,
        notes: str,
        event_code: str,
        event_label: str,
    ):
        if order.status == to_status:
            return
        previous = order.status
        order.status = to_status
        order.last_status_at = timezone.now()
        order.save(update_fields=["status", "last_status_at", "updated_at"])
        OrderStatusHistory.objects.create(
            order=order,
            from_status=previous,
            to_status=to_status,
            actor_role=actor_role,
            actor_id=str(actor_id),
            notes=notes,
        )
        OrderTimelineEvent.objects.create(
            order=order,
            event_code=event_code,
            event_label=event_label,
            actor_role=actor_role,
            actor_id=str(actor_id),
            metadata={"from_status": previous, "to_status": to_status},
        )

    def _confirm_cash_payment(self, payment: OrderPayment, delivery: UserProfile, metadata: dict):
        payment.status = OrderPayment.Status.CONFIRMED
        payment.confirmed_by_role = "REPARTIDOR"
        payment.confirmed_by_user = delivery
        payment.confirmed_at = timezone.now()
        payment.save(
            update_fields=[
                "status",
                "confirmed_by_role",
                "confirmed_by_user",
                "confirmed_at",
                "updated_at",
            ]
        )
        OrderPaymentEvent.objects.create(
            payment=payment,
            event_code="CASH_PAYMENT_CONFIRMED",
            event_label="Pago en efectivo confirmado",
            actor_role="REPARTIDOR",
            actor_id=str(delivery.supabase_user_id),
            metadata=metadata,
        )
        OrderTimelineEvent.objects.create(
            order=payment.order,
            event_code="PAYMENT_CONFIRMED",
            event_label="Pago en efectivo confirmado",
            actor_role="REPARTIDOR",
            actor_id=str(delivery.supabase_user_id),
            metadata={"payment_id": payment.id, **metadata},
        )

    def _get_assignment_for_delivery(
        self,
        delivery: UserProfile,
        assignment_id: str,
        lock: bool = False,
        allow_unassigned: bool = False,
    ):
        queryset = DeliveryAssignment.objects.filter(id=str(assignment_id))
        if not allow_unassigned:
            queryset = queryset.filter(delivery_user=delivery)
        else:
            queryset = queryset.filter(
                Q(delivery_user__isnull=True) | Q(delivery_user=delivery)
            )
        if lock:
            queryset = queryset.select_for_update()
        assignment = (
            queryset.select_related(
                "order",
                "order__client",
                "order__chef",
            )
            .prefetch_related(
                "delivery_user",
                "status_history",
                "incidents",
                "location_pings",
                "route_snapshots",
                "order__address",
                "order__items",
                "order__payments",
            )
            .first()
        )
        if not assignment:
            raise DeliveryLogisticsError(
                "Entrega no encontrada para el repartidor.",
                "assignment_not_found",
            )
        return assignment

    def _get_owned_assignment(self, delivery: UserProfile, assignment_id: str, lock: bool = False):
        assignment = self._get_assignment_for_delivery(
            delivery,
            assignment_id,
            lock=lock,
            allow_unassigned=False,
        )
        if assignment.delivery_user_id != delivery.id:
            raise DeliveryLogisticsError(
                "La entrega no pertenece a tu usuario de repartidor.",
                "delivery_not_owner",
            )
        return assignment

    def _require_delivery(self, user_id: str):
        parsed = self._parse_uuid(user_id)
        profile = (
            UserProfile.objects.filter(
                supabase_user_id=parsed,
                role=UserProfile.ROLE_DELIVERY,
            ).first()
            if parsed
            else None
        )
        if not profile:
            raise DeliveryLogisticsError(
                "Perfil de repartidor no encontrado.",
                "delivery_not_found",
            )
        return profile

    def _latest_payment(self, order: Order):
        payments = list(order.payments.all())
        if not payments:
            return None
        payments.sort(key=lambda row: row.created_at, reverse=True)
        return payments[0]

    def _status_label(self, status_value: str):
        labels = {
            DeliveryAssignment.Status.UNASSIGNED: "Disponible para tomar",
            DeliveryAssignment.Status.ASSIGNED: "Asignada al repartidor",
            DeliveryAssignment.Status.EN_ROUTE_TO_CHEF: "En camino al cocinero",
            DeliveryAssignment.Status.AT_CHEF: "En punto de recogida",
            DeliveryAssignment.Status.PICKED_UP: "Pedido recogido",
            DeliveryAssignment.Status.EN_ROUTE_TO_CLIENT: "En ruta al cliente",
            DeliveryAssignment.Status.DELIVERED: "Entregada",
            DeliveryAssignment.Status.FAILED: "Entrega fallida",
            DeliveryAssignment.Status.CANCELLED: "Entrega cancelada",
        }
        return labels.get(status_value, status_value)

    def _order_status_label(self, status_value: str):
        labels = {
            Order.Status.AWAITING_CHEF_CONFIRMATION: "Esperando al cocinero",
            Order.Status.ACCEPTED: "Aceptado",
            Order.Status.PREPARING: "En preparacion",
            Order.Status.READY_FOR_DELIVERY: "Listo para delivery",
            Order.Status.OUT_FOR_DELIVERY: "En camino",
            Order.Status.DELIVERED: "Entregado",
            Order.Status.CANCELLED: "Cancelado",
            Order.Status.REJECTED: "Rechazado",
        }
        return labels.get(status_value, status_value)

    def _payment_status_label(self, status_value: str):
        labels = {
            OrderPayment.Status.PENDING: "Pendiente",
            OrderPayment.Status.PROCESSING: "Procesando",
            OrderPayment.Status.CONFIRMED: "Confirmado",
            OrderPayment.Status.CANCELLED: "Cancelado",
            OrderPayment.Status.FAILED: "Fallido",
            OrderPayment.Status.EXPIRED: "Expirado",
        }
        return labels.get(status_value, status_value or "-")

    def _chef_name(self, chef: UserProfile):
        profile = ChefProfile.objects.filter(user=chef).first()
        if profile and getattr(profile, "business_name", ""):
            return profile.business_name
        return self._profile_name(chef)

    def _profile_name(self, profile: UserProfile | None):
        if not profile:
            return ""
        name = f"{profile.first_name} {profile.last_name}".strip()
        return name or profile.full_name or profile.email

    def _parse_uuid(self, value):
        try:
            return UUID(str(value))
        except (TypeError, ValueError):
            return None

from django.utils import timezone

from modules.confianza_administracion_seguridad.services import NotificationService
from modules.delivery_logistica.models import DeliveryAssignment, DeliveryStatusHistory
from modules.pedidos_checkout_pagos.models import Order
from .delivery_assignment_engine import DeliveryAssignmentEngine


class BaseDeliveryService:
    def __init__(self):
        self.notification_service = NotificationService()
        self.assignment_engine = DeliveryAssignmentEngine()

    def ensure_assignment_for_order(self, order: Order):
        if order.fulfillment_type != Order.FulfillmentType.DELIVERY:
            return None
        assignment, created = DeliveryAssignment.objects.get_or_create(
            order=order,
            defaults={
                "status": DeliveryAssignment.Status.UNASSIGNED,
                "metadata": {
                    "order_status": order.status,
                    "payment_method": order.payment_method,
                },
            },
        )
        if created:
            DeliveryStatusHistory.objects.create(
                assignment=assignment,
                from_status="",
                to_status=assignment.status,
                actor_role="SISTEMA",
                actor_id="",
                notes="Entrega creada para pedido delivery",
                metadata={"order_id": order.id},
            )
        return assignment

    def sync_assignment_for_order_status(self, order: Order, actor_role: str, actor_id: str, notes: str = ""):
        assignment = self.ensure_assignment_for_order(order)
        if not assignment:
            return None

        target_status = self._map_order_status(order.status, assignment)
        metadata = {
            **(assignment.metadata or {}),
            "order_status": order.status,
            "payment_method": order.payment_method,
            "fulfillment_type": order.fulfillment_type,
        }
        update_fields = ["metadata", "updated_at"]

        if target_status and assignment.status != target_status:
            previous = assignment.status
            assignment.status = target_status
            update_fields.insert(0, "status")
            if target_status == DeliveryAssignment.Status.ASSIGNED and not assignment.assigned_at:
                assignment.assigned_at = timezone.now()
                update_fields.append("assigned_at")
            if target_status == DeliveryAssignment.Status.PICKED_UP and not assignment.picked_up_at:
                assignment.picked_up_at = timezone.now()
                update_fields.append("picked_up_at")
            if target_status == DeliveryAssignment.Status.DELIVERED and not assignment.delivered_at:
                assignment.delivered_at = timezone.now()
                update_fields.append("delivered_at")
            DeliveryStatusHistory.objects.create(
                assignment=assignment,
                from_status=previous,
                to_status=target_status,
                actor_role=actor_role,
                actor_id=str(actor_id or ""),
                notes=notes or self._default_notes(target_status),
                metadata={"order_id": order.id, "order_status": order.status},
            )

        assignment.metadata = metadata
        assignment.save(update_fields=update_fields)
        if order.status == Order.Status.READY_FOR_DELIVERY:
            assignment = self.assignment_engine.ensure_assignment_up_to_date(
                assignment,
                reason="order_ready_sync",
                actor_role=actor_role,
                actor_id=str(actor_id or ""),
            )
        return assignment

    def assign_delivery_user(self, order: Order, delivery_user, actor_role: str, actor_id: str, notes: str = ""):
        assignment = self.ensure_assignment_for_order(order)
        if not assignment:
            return None
        changed = assignment.delivery_user_id != delivery_user.id
        assignment.delivery_user = delivery_user
        assignment.assigned_at = assignment.assigned_at or timezone.now()
        assignment.metadata = {
            **(assignment.metadata or {}),
            "assigned_delivery_user_id": str(delivery_user.supabase_user_id),
        }
        update_fields = ["delivery_user", "assigned_at", "metadata", "updated_at"]
        if assignment.status == DeliveryAssignment.Status.UNASSIGNED:
            previous = assignment.status
            assignment.status = DeliveryAssignment.Status.ASSIGNED
            update_fields.insert(0, "status")
            DeliveryStatusHistory.objects.create(
                assignment=assignment,
                from_status=previous,
                to_status=assignment.status,
                actor_role=actor_role,
                actor_id=str(actor_id or ""),
                notes=notes or "Entrega asignada a repartidor",
                metadata={"order_id": order.id, "delivery_user_id": str(delivery_user.supabase_user_id)},
            )
        elif changed:
            DeliveryStatusHistory.objects.create(
                assignment=assignment,
                from_status=assignment.status,
                to_status=assignment.status,
                actor_role=actor_role,
                actor_id=str(actor_id or ""),
                notes=notes or "Entrega reasignada a repartidor",
                metadata={"order_id": order.id, "delivery_user_id": str(delivery_user.supabase_user_id)},
            )
        assignment.save(update_fields=update_fields)
        self.notification_service.notify_delivery_assigned(assignment)
        return assignment

    def _map_order_status(self, order_status: str, assignment: DeliveryAssignment):
        if order_status in {
            Order.Status.PAYMENT_VALIDATING,
            Order.Status.AWAITING_CHEF_CONFIRMATION,
            Order.Status.ACCEPTED,
            Order.Status.PREPARING,
        }:
            return DeliveryAssignment.Status.UNASSIGNED
        if order_status == Order.Status.READY_FOR_DELIVERY:
            return DeliveryAssignment.Status.UNASSIGNED if not assignment.delivery_user_id else DeliveryAssignment.Status.ASSIGNED
        if order_status == Order.Status.OUT_FOR_DELIVERY:
            return DeliveryAssignment.Status.EN_ROUTE_TO_CLIENT
        if order_status == Order.Status.DELIVERED:
            return DeliveryAssignment.Status.DELIVERED
        if order_status in {Order.Status.CANCELLED, Order.Status.REJECTED, Order.Status.EXPIRED, Order.Status.PAYMENT_FAILED}:
            return DeliveryAssignment.Status.CANCELLED
        return assignment.status

    def _default_notes(self, delivery_status: str):
        labels = {
            DeliveryAssignment.Status.UNASSIGNED: "Entrega creada sin repartidor asignado",
            DeliveryAssignment.Status.ASSIGNED: "Entrega asignada",
            DeliveryAssignment.Status.EN_ROUTE_TO_CHEF: "Repartidor en camino al cocinero",
            DeliveryAssignment.Status.AT_CHEF: "Repartidor en punto de recogida",
            DeliveryAssignment.Status.PICKED_UP: "Pedido recogido por delivery",
            DeliveryAssignment.Status.EN_ROUTE_TO_CLIENT: "Pedido en camino al cliente",
            DeliveryAssignment.Status.DELIVERED: "Pedido entregado al cliente",
            DeliveryAssignment.Status.FAILED: "Entrega marcada como fallida",
            DeliveryAssignment.Status.CANCELLED: "Entrega cancelada",
        }
        return labels.get(delivery_status, delivery_status)

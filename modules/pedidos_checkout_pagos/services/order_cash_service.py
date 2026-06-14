from uuid import UUID

from django.db import transaction
from django.utils import timezone

from modules.confianza_administracion_seguridad.services import NotificationService
from modules.delivery_logistica.models import DeliveryAssignment
from modules.delivery_logistica.services.delivery_incident_service import DeliveryIncidentService
from modules.delivery_logistica.services.base_delivery_service import BaseDeliveryService
from modules.delivery_logistica.services.delivery_tracking_service import DeliveryTrackingService
from modules.gestion_cocinero.models import ChefProfile
from modules.gestion_usuarios_acceso_suscripcion.models import UserProfile
from modules.pedidos_checkout_pagos.models import (
    Order,
    OrderPayment,
    OrderPaymentEvent,
    OrderStatusHistory,
    OrderTimelineEvent,
    PickupConfirmation,
    SimulatedQRPaymentSession,
)
from modules.pedidos_checkout_pagos.services.stock_service import DishStockService


class OrderCashServiceError(ValueError):
    def __init__(self, message: str, code: str, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}


class OrderCashService:
    CLIENT_CANCELABLE_STATUSES = {
        Order.Status.PAYMENT_VALIDATING,
        Order.Status.AWAITING_CHEF_CONFIRMATION,
        Order.Status.ACCEPTED,
    }
    CHEF_REJECTABLE_STATUSES = {
        Order.Status.AWAITING_CHEF_CONFIRMATION,
        Order.Status.ACCEPTED,
    }

    def __init__(self):
        self.stock_service = DishStockService()
        self.delivery_service = BaseDeliveryService()
        self.delivery_tracking_service = DeliveryTrackingService()
        self.incident_service = DeliveryIncidentService()
        self.notification_service = NotificationService()

    def list_client_orders(self, user_id: str):
        client = self._require_profile(user_id, UserProfile.ROLE_CLIENT, "client_not_found", "Perfil de cliente no encontrado.")
        queryset = (
            Order.objects.filter(client=client)
            .select_related("chef", "address", "delivery_assignment", "delivery_assignment__delivery_user", "pickup_confirmation")
            .prefetch_related("items", "payments", "timeline_events", "status_history", "delivery_assignment__location_pings", "delivery_assignment__incidents")
            .order_by("-created_at")
        )
        return {"items": [self._serialize_order(order, "CLIENTE", str(client.supabase_user_id)) for order in queryset]}

    def get_client_order_tracking(self, user_id: str, order_id: str):
        client = self._require_profile(user_id, UserProfile.ROLE_CLIENT, "client_not_found", "Perfil de cliente no encontrado.")
        order = self._get_order_for_client(client, order_id)
        return self._serialize_tracking(order, "CLIENTE")

    def get_client_order_detail(self, user_id: str, order_id: str):
        client = self._require_profile(user_id, UserProfile.ROLE_CLIENT, "client_not_found", "Perfil de cliente no encontrado.")
        order = self._get_order_for_client(client, order_id)
        return {"order": self._serialize_order(order, "CLIENTE", str(client.supabase_user_id))}

    @transaction.atomic
    def cancel_client_order(self, user_id: str, order_id: str):
        client = self._require_profile(user_id, UserProfile.ROLE_CLIENT, "client_not_found", "Perfil de cliente no encontrado.")
        order = self._get_order_for_client(client, order_id, lock=True)
        payment = self._latest_payment(order)
        can_cancel, reason = self._client_cancel_policy(order, payment)
        if not can_cancel:
            raise OrderCashServiceError(reason, "client_cancel_not_allowed", {"current_status": order.status})

        if payment and payment.status in {OrderPayment.Status.CREATED, OrderPayment.Status.PENDING, OrderPayment.Status.PROCESSING}:
            payment.status = OrderPayment.Status.CANCELLED
            payment.failure_reason = "Cancelado por cliente"
            payment.save(update_fields=["status", "failure_reason", "updated_at"])
            OrderPaymentEvent.objects.create(
                payment=payment,
                event_code="PAYMENT_CANCELLED_BY_CLIENT",
                event_label="Pago cancelado por cliente",
                actor_role="CLIENTE",
                actor_id=str(client.supabase_user_id),
                metadata={"order_id": order.id, "method": payment.method},
            )
            OrderTimelineEvent.objects.create(
                order=order,
                event_code="PAYMENT_CANCELLED_BY_CLIENT",
                event_label="Pago cancelado por cliente",
                actor_role="CLIENTE",
                actor_id=str(client.supabase_user_id),
                metadata={"payment_id": payment.id, "method": payment.method},
            )
            if payment.method == Order.PaymentMethod.QR_SIMULATED:
                self._cancel_qr_sessions(order)

        if order.stock_reserved:
            self.stock_service.release_order_stock(order)
        self._cancel_pickup_confirmation(order, "Cancelado por cliente")
        order.cancelled_reason = "Cancelado por cliente"
        order.cancelled_by_role = "CLIENTE"
        order.save(update_fields=["cancelled_reason", "cancelled_by_role", "updated_at"])
        self._move_order(
            order,
            Order.Status.CANCELLED,
            "CLIENTE",
            str(client.supabase_user_id),
            "Pedido cancelado por cliente",
            "ORDER_CANCELLED_BY_CLIENT",
            "Pedido cancelado por cliente",
        )
        self.delivery_service.sync_assignment_for_order_status(
            order,
            actor_role="CLIENTE",
            actor_id=str(client.supabase_user_id),
            notes="Entrega cancelada por cancelacion del cliente",
        )
        return {"order": self._serialize_order(order, "CLIENTE", str(client.supabase_user_id))}

    def list_chef_orders(self, user_id: str):
        chef = self._require_profile(user_id, UserProfile.ROLE_CHEF, "chef_not_found", "Perfil de cocinero no encontrado.")
        queryset = (
            Order.objects.filter(chef=chef)
            .select_related("client", "address", "delivery_assignment", "delivery_assignment__delivery_user", "pickup_confirmation")
            .prefetch_related("items", "payments", "timeline_events", "status_history", "delivery_assignment__location_pings", "delivery_assignment__incidents")
            .order_by("-created_at")
        )
        return {"items": [self._serialize_order(order, "COCINERO", str(chef.supabase_user_id)) for order in queryset]}

    def get_chef_order_detail(self, user_id: str, order_id: str):
        chef = self._require_profile(user_id, UserProfile.ROLE_CHEF, "chef_not_found", "Perfil de cocinero no encontrado.")
        order = self._get_order_for_chef(chef, order_id)
        return {"order": self._serialize_order(order, "COCINERO", str(chef.supabase_user_id))}

    def get_chef_order_tracking(self, user_id: str, order_id: str):
        chef = self._require_profile(user_id, UserProfile.ROLE_CHEF, "chef_not_found", "Perfil de cocinero no encontrado.")
        order = self._get_order_for_chef(chef, order_id)
        return self._serialize_tracking(order, "COCINERO")

    def list_delivery_cash_orders(self, user_id: str):
        delivery = self._require_profile(user_id, UserProfile.ROLE_DELIVERY, "delivery_not_found", "Perfil de repartidor no encontrado.")
        queryset = (
            Order.objects.filter(
                payment_method=Order.PaymentMethod.CASH,
                fulfillment_type=Order.FulfillmentType.DELIVERY,
                status__in=[Order.Status.READY_FOR_DELIVERY, Order.Status.OUT_FOR_DELIVERY],
            )
            .select_related("chef", "client", "address", "delivery_assignment", "delivery_assignment__delivery_user")
            .prefetch_related("items", "payments", "timeline_events", "status_history", "delivery_assignment__location_pings", "delivery_assignment__incidents")
            .order_by("-created_at")
        )

        available = []
        active = []
        for order in queryset:
            serialized = self._serialize_order(order, "REPARTIDOR", str(delivery.supabase_user_id))
            if order.status == Order.Status.READY_FOR_DELIVERY:
                available.append(serialized)
            elif self._delivery_started_by(order) == str(delivery.supabase_user_id):
                active.append(serialized)
        return {"available_items": available, "active_items": active}

    @transaction.atomic
    def chef_accept_order(self, user_id: str, order_id: str):
        chef = self._require_profile(user_id, UserProfile.ROLE_CHEF, "chef_not_found", "Perfil de cocinero no encontrado.")
        order = self._get_order_for_chef(chef, order_id, lock=True)
        self._assert_status(order, [Order.Status.AWAITING_CHEF_CONFIRMATION], "transition_not_allowed", "El pedido no puede aceptarse en su estado actual.")
        self._move_order(order, Order.Status.ACCEPTED, "COCINERO", str(chef.supabase_user_id), "Pedido aceptado por cocinero", "ORDER_ACCEPTED", "Pedido aceptado")
        self.delivery_service.sync_assignment_for_order_status(
            order,
            actor_role="COCINERO",
            actor_id=str(chef.supabase_user_id),
            notes="Entrega sincronizada al aceptar pedido",
        )
        self.notification_service.notify_order_accepted(order)
        return {"order": self._serialize_order(order, "COCINERO", str(chef.supabase_user_id))}

    @transaction.atomic
    def chef_mark_preparing(self, user_id: str, order_id: str):
        chef = self._require_profile(user_id, UserProfile.ROLE_CHEF, "chef_not_found", "Perfil de cocinero no encontrado.")
        order = self._get_order_for_chef(chef, order_id, lock=True)
        self._assert_status(order, [Order.Status.ACCEPTED], "transition_not_allowed", "El pedido no puede pasar a preparacion en su estado actual.")
        self._move_order(order, Order.Status.PREPARING, "COCINERO", str(chef.supabase_user_id), "Pedido en preparacion", "ORDER_PREPARING", "Pedido en preparacion")
        self.delivery_service.sync_assignment_for_order_status(
            order,
            actor_role="COCINERO",
            actor_id=str(chef.supabase_user_id),
            notes="Entrega sincronizada al iniciar preparacion",
        )
        return {"order": self._serialize_order(order, "COCINERO", str(chef.supabase_user_id))}

    @transaction.atomic
    def chef_mark_ready(self, user_id: str, order_id: str):
        chef = self._require_profile(user_id, UserProfile.ROLE_CHEF, "chef_not_found", "Perfil de cocinero no encontrado.")
        order = self._get_order_for_chef(chef, order_id, lock=True)
        self._assert_status(order, [Order.Status.PREPARING], "transition_not_allowed", "El pedido no puede marcarse como listo en su estado actual.")
        target_status = Order.Status.READY_FOR_DELIVERY if order.fulfillment_type == Order.FulfillmentType.DELIVERY else Order.Status.READY_FOR_PICKUP
        event_label = "Pedido listo para delivery" if target_status == Order.Status.READY_FOR_DELIVERY else "Pedido listo para retiro"
        self._move_order(order, target_status, "COCINERO", str(chef.supabase_user_id), event_label, "ORDER_READY", event_label)
        self.delivery_service.sync_assignment_for_order_status(
            order,
            actor_role="COCINERO",
            actor_id=str(chef.supabase_user_id),
            notes="Entrega sincronizada al marcar pedido listo",
        )
        self.notification_service.notify_order_ready(order)
        return {"order": self._serialize_order(order, "COCINERO", str(chef.supabase_user_id))}

    @transaction.atomic
    def chef_reject_order(self, user_id: str, order_id: str):
        chef = self._require_profile(user_id, UserProfile.ROLE_CHEF, "chef_not_found", "Perfil de cocinero no encontrado.")
        order = self._get_order_for_chef(chef, order_id, lock=True)
        self._assert_status(order, list(self.CHEF_REJECTABLE_STATUSES), "transition_not_allowed", "El pedido no puede rechazarse en su estado actual.")
        payment = self._latest_payment(order)
        if payment and payment.status in {OrderPayment.Status.CREATED, OrderPayment.Status.PENDING, OrderPayment.Status.PROCESSING}:
            payment.status = OrderPayment.Status.CANCELLED
            payment.failure_reason = "Rechazado por cocinero"
            payment.save(update_fields=["status", "failure_reason", "updated_at"])
            OrderPaymentEvent.objects.create(
                payment=payment,
                event_code="PAYMENT_CANCELLED_BY_CHEF_REJECTION",
                event_label="Pago cancelado por rechazo del cocinero",
                actor_role="COCINERO",
                actor_id=str(chef.supabase_user_id),
                metadata={"order_id": order.id, "method": payment.method},
            )
            OrderTimelineEvent.objects.create(
                order=order,
                event_code="PAYMENT_CANCELLED_BY_CHEF_REJECTION",
                event_label="Pago cancelado por rechazo del cocinero",
                actor_role="COCINERO",
                actor_id=str(chef.supabase_user_id),
                metadata={"payment_id": payment.id, "method": payment.method},
            )
            if payment.method == Order.PaymentMethod.QR_SIMULATED:
                self._cancel_qr_sessions(order, "Rechazado por cocinero")
        if order.stock_reserved:
            self.stock_service.release_order_stock(order)
        self._cancel_pickup_confirmation(order, "Rechazado por cocinero")
        order.cancelled_reason = "Rechazado por cocinero"
        order.cancelled_by_role = "COCINERO"
        order.save(update_fields=["cancelled_reason", "cancelled_by_role", "updated_at"])
        self._move_order(
            order,
            Order.Status.REJECTED,
            "COCINERO",
            str(chef.supabase_user_id),
            "Pedido rechazado por cocinero",
            "ORDER_REJECTED",
            "Pedido rechazado",
        )
        self.delivery_service.sync_assignment_for_order_status(
            order,
            actor_role="COCINERO",
            actor_id=str(chef.supabase_user_id),
            notes="Entrega cancelada por rechazo del cocinero",
        )
        return {"order": self._serialize_order(order, "COCINERO", str(chef.supabase_user_id))}

    @transaction.atomic
    def chef_confirm_pickup(self, user_id: str, order_id: str, pickup_code: str):
        chef = self._require_profile(user_id, UserProfile.ROLE_CHEF, "chef_not_found", "Perfil de cocinero no encontrado.")
        order = self._get_order_for_chef(chef, order_id, lock=True)
        self._assert_status(order, [Order.Status.READY_FOR_PICKUP], "transition_not_allowed", "El pedido no puede cerrarse por retiro en su estado actual.")
        if order.fulfillment_type != Order.FulfillmentType.PICKUP:
            raise OrderCashServiceError("El pedido no corresponde a retiro en punto.", "invalid_fulfillment_type")
        pickup = self._get_pending_pickup_confirmation(order)
        normalized_code = str(pickup_code or "").strip()
        if not normalized_code:
            raise OrderCashServiceError("Debes ingresar el codigo de retiro para cerrar el pedido.", "pickup_code_required")
        if normalized_code != pickup.pickup_code:
            raise OrderCashServiceError("El codigo de retiro no coincide con el pedido.", "pickup_code_invalid")
        payment = self._latest_payment(order)
        if payment and payment.method == Order.PaymentMethod.CASH and payment.status == OrderPayment.Status.PENDING:
            self._confirm_payment(payment, chef, "COCINERO", {"order_id": order.id, "fulfillment_type": order.fulfillment_type, "pickup_code": pickup.pickup_code})
        elif payment and payment.method != Order.PaymentMethod.CASH and payment.status != OrderPayment.Status.CONFIRMED:
            raise OrderCashServiceError("El pedido aun no tiene un pago confirmado para cerrar el retiro.", "payment_invalid_status")
        self._confirm_pickup_confirmation(pickup, chef, "COCINERO")
        notes = "Retiro confirmado por cocinero"
        if payment and payment.method == Order.PaymentMethod.CASH:
            notes = "Retiro confirmado y pago en efectivo validado"
        self._move_order(order, Order.Status.PICKED_UP, "COCINERO", str(chef.supabase_user_id), notes, "ORDER_PICKED_UP", "Pedido retirado")
        self.notification_service.notify_payment_confirmed(order, payment) if payment and payment.method == Order.PaymentMethod.CASH else None
        self.notification_service.notify_order_picked_up(order)
        return {"order": self._serialize_order(order, "COCINERO", str(chef.supabase_user_id))}

    @transaction.atomic
    def delivery_start_order(self, user_id: str, order_id: str):
        delivery = self._require_profile(user_id, UserProfile.ROLE_DELIVERY, "delivery_not_found", "Perfil de repartidor no encontrado.")
        order = self._get_delivery_cash_order(order_id)
        self._assert_status(order, [Order.Status.READY_FOR_DELIVERY], "transition_not_allowed", "El pedido no esta listo para iniciar delivery.")
        self.delivery_service.assign_delivery_user(
            order,
            delivery,
            actor_role="REPARTIDOR",
            actor_id=str(delivery.supabase_user_id),
            notes="Entrega tomada por repartidor",
        )
        self._move_order(order, Order.Status.OUT_FOR_DELIVERY, "REPARTIDOR", str(delivery.supabase_user_id), "Delivery en camino con pedido cash", "DELIVERY_STARTED", "Delivery iniciado")
        self.delivery_service.sync_assignment_for_order_status(
            order,
            actor_role="REPARTIDOR",
            actor_id=str(delivery.supabase_user_id),
            notes="Entrega en ruta al cliente",
        )
        if getattr(order, "delivery_assignment", None):
            self.notification_service.notify_order_picked_up(order, order.delivery_assignment)
        return {"order": self._serialize_order(order, "REPARTIDOR", str(delivery.supabase_user_id))}

    @transaction.atomic
    def delivery_confirm_cash(self, user_id: str, order_id: str):
        delivery = self._require_profile(user_id, UserProfile.ROLE_DELIVERY, "delivery_not_found", "Perfil de repartidor no encontrado.")
        order = self._get_delivery_cash_order(order_id)
        self._assert_status(order, [Order.Status.OUT_FOR_DELIVERY], "transition_not_allowed", "El pedido no esta en entrega activa.")
        starter_id = self._delivery_started_by(order)
        if starter_id != str(delivery.supabase_user_id):
            raise OrderCashServiceError("Este pedido no fue tomado por tu usuario de delivery.", "delivery_not_owner")
        payment = self._get_pending_cash_payment(order)
        self._confirm_payment(payment, delivery, "REPARTIDOR", {"order_id": order.id, "fulfillment_type": order.fulfillment_type})
        self._move_order(order, Order.Status.DELIVERED, "REPARTIDOR", str(delivery.supabase_user_id), "Cobro en efectivo confirmado al entregar", "ORDER_DELIVERED", "Pedido entregado")
        self.delivery_service.sync_assignment_for_order_status(
            order,
            actor_role="REPARTIDOR",
            actor_id=str(delivery.supabase_user_id),
            notes="Entrega finalizada correctamente",
        )
        self.notification_service.notify_payment_confirmed(order, payment)
        self.notification_service.notify_order_delivered(order, getattr(order, "delivery_assignment", None))
        return {"order": self._serialize_order(order, "REPARTIDOR", str(delivery.supabase_user_id))}

    def _serialize_order(self, order: Order, viewer_role: str, viewer_actor_id: str):
        payment = self._latest_payment(order)
        actions = self._available_actions(order, payment, viewer_role, viewer_actor_id)
        can_cancel, cancel_reason = self._client_cancel_policy(order, payment) if viewer_role == "CLIENTE" else (False, "")
        return {
            "id": order.id,
            "status": order.status,
            "fulfillment_type": order.fulfillment_type,
            "payment_method": order.payment_method,
            "currency": order.currency,
            "subtotal": float(order.subtotal),
            "delivery_fee": float(order.delivery_fee),
            "service_fee": float(order.service_fee),
            "discount_total": float(order.discount_total),
            "total": float(order.total),
            "notes": order.notes,
            "created_at": order.created_at.isoformat(),
            "updated_at": order.updated_at.isoformat(),
            "client": {
                "id": str(order.client.supabase_user_id),
                "name": self._profile_name(order.client),
            } if hasattr(order, "client") else None,
            "chef": {
                "id": str(order.chef.supabase_user_id),
                "name": self._chef_name(order.chef),
            } if hasattr(order, "chef") else None,
            "address": self._serialize_address(order),
            "items": [
                {
                    "id": item.id,
                    "dish_id": item.dish_id,
                    "dish_name": item.dish_name_snapshot,
                    "quantity": item.quantity,
                    "unit_price": float(item.unit_price),
                    "subtotal": float(item.subtotal),
                }
                for item in order.items.all()
            ],
            "payment": self._serialize_payment(payment),
            "delivery": self._serialize_delivery(order, viewer_role),
            "pickup": self._serialize_pickup(order, viewer_role),
            "available_actions": actions,
            "can_cancel": can_cancel,
            "cancel_restriction": cancel_reason,
            "timeline": self._serialize_timeline(order),
        }

    def _serialize_address(self, order: Order):
        address = getattr(order, "address", None)
        if not address:
            return None
        return {
            "label": address.label,
            "contact_name": address.contact_name,
            "contact_phone": address.contact_phone,
            "line_1": address.line_1,
            "reference": address.reference,
            "latitude": address.latitude,
            "longitude": address.longitude,
        }

    def _serialize_payment(self, payment: OrderPayment | None):
        if not payment:
            return None
        return {
            "id": payment.id,
            "method": payment.method,
            "status": payment.status,
            "amount": float(payment.amount),
            "payment_url": payment.payment_url,
            "external_reference": payment.external_reference,
            "expires_at": payment.expires_at.isoformat() if payment.expires_at else None,
            "confirmed_by_role": payment.confirmed_by_role,
            "confirmed_at": payment.confirmed_at.isoformat() if payment.confirmed_at else None,
        }

    def _serialize_delivery(self, order: Order, viewer_role: str = "CLIENTE"):
        assignment = getattr(order, "delivery_assignment", None)
        if not assignment:
            return None
        incident_payload = self.incident_service.incident_summary(assignment, viewer_role=viewer_role)
        latest_ping = assignment.location_pings.order_by("-recorded_at").first()
        map_payload = self.delivery_tracking_service.ensure_map_payload(assignment)
        return {
            "assignment_id": assignment.id,
            "status": assignment.status,
            "status_label": self._delivery_status_label(assignment.status),
            "delivery_user_id": str(assignment.delivery_user.supabase_user_id) if assignment.delivery_user else "",
            "delivery_user_name": self._profile_name(assignment.delivery_user) if assignment.delivery_user else "",
            "assigned_at": assignment.assigned_at.isoformat() if assignment.assigned_at else None,
            "picked_up_at": assignment.picked_up_at.isoformat() if assignment.picked_up_at else None,
            "delivered_at": assignment.delivered_at.isoformat() if assignment.delivered_at else None,
            "open_incidents": incident_payload["open_count"],
            "blocking_open_incidents": incident_payload["blocking_open_count"],
            "delivery_blocked": incident_payload["delivery_blocked"],
            "incidents": incident_payload["items"],
            "has_location_ping": latest_ping is not None,
            "current_location": map_payload.get("current_location"),
            "map": map_payload,
        }

    def _serialize_pickup(self, order: Order, viewer_role: str):
        pickup = getattr(order, "pickup_confirmation", None)
        if not pickup:
            return None
        show_code = viewer_role in {"CLIENTE", "COCINERO"}
        return {
            "status": pickup.status,
            "pickup_code": pickup.pickup_code if show_code else "",
            "pickup_instructions": pickup.pickup_instructions,
            "pickup_schedule_note": pickup.pickup_schedule_note,
            "confirmed_by_role": pickup.confirmed_by_role,
            "confirmed_at": pickup.confirmed_at.isoformat() if pickup.confirmed_at else None,
        }

    def _available_actions(self, order: Order, payment: OrderPayment | None, viewer_role: str, viewer_actor_id: str):
        if viewer_role == "CLIENTE":
            can_cancel, _ = self._client_cancel_policy(order, payment)
            return ["cancel"] if can_cancel else []

        if viewer_role == "COCINERO":
            if order.status == Order.Status.AWAITING_CHEF_CONFIRMATION:
                return ["accept", "reject"]
            if order.status == Order.Status.ACCEPTED:
                return ["preparing", "reject"]
            if order.status == Order.Status.PREPARING:
                return ["ready"]
            if (
                order.status == Order.Status.READY_FOR_PICKUP
                and order.fulfillment_type == Order.FulfillmentType.PICKUP
            ):
                return ["confirm_pickup"]
            return []

        if viewer_role == "REPARTIDOR":
            if (
                order.status == Order.Status.READY_FOR_DELIVERY
                and order.fulfillment_type == Order.FulfillmentType.DELIVERY
                and payment
                and payment.method == Order.PaymentMethod.CASH
                and payment.status == OrderPayment.Status.PENDING
            ):
                return ["start_delivery"]
            if (
                order.status == Order.Status.OUT_FOR_DELIVERY
                and payment
                and payment.method == Order.PaymentMethod.CASH
                and payment.status == OrderPayment.Status.PENDING
                and self._delivery_started_by(order) == viewer_actor_id
            ):
                return ["confirm_delivery_cash"]
            return []

        return []

    def _client_cancel_policy(self, order: Order, payment: OrderPayment | None):
        if order.status not in self.CLIENT_CANCELABLE_STATUSES:
            return False, "El pedido ya no puede cancelarse en su estado actual."
        if payment and payment.status == OrderPayment.Status.CONFIRMED and payment.method != Order.PaymentMethod.CASH:
            return False, "El pago ya fue confirmado y requiere una politica de devolucion separada."
        return True, ""

    def _serialize_timeline(self, order: Order):
        timeline = []
        for event in order.timeline_events.all():
            timeline.append(
                {
                    "entry_type": "event",
                    "event_code": event.event_code,
                    "event_label": event.event_label,
                    "actor_role": event.actor_role,
                    "actor_id": event.actor_id,
                    "notes": "",
                    "metadata": event.metadata or {},
                    "occurred_at": event.occurred_at.isoformat(),
                    "from_status": (event.metadata or {}).get("from_status", ""),
                    "to_status": (event.metadata or {}).get("to_status", ""),
                }
            )
        for item in order.status_history.all():
            timeline.append(
                {
                    "entry_type": "status",
                    "event_code": "STATUS_CHANGED",
                    "event_label": item.notes or f"{item.from_status} -> {item.to_status}",
                    "actor_role": item.actor_role,
                    "actor_id": item.actor_id,
                    "notes": item.notes,
                    "metadata": {},
                    "occurred_at": item.occurred_at.isoformat(),
                    "from_status": item.from_status,
                    "to_status": item.to_status,
                }
            )
        timeline.sort(key=lambda row: row["occurred_at"], reverse=True)
        return timeline

    def _serialize_tracking(self, order: Order, viewer_role: str):
        payment = self._latest_payment(order)
        timeline = self._serialize_timeline(order)
        steps = self._tracking_steps(order.fulfillment_type)
        current_index = self._tracking_step_index(order.status, steps)
        total_steps = len(steps)
        progress_percent = 0 if total_steps == 0 else int(((current_index + 1) / total_steps) * 100)
        current_step = steps[current_index] if 0 <= current_index < total_steps else {"status": order.status, "label": self._status_label(order.status)}
        delivery_payload = self._serialize_delivery(order, viewer_role)
        map_payload = delivery_payload.get("map") if delivery_payload else None
        incident_payload = delivery_payload.get("incidents") if delivery_payload else []
        delivery_blocked = bool(delivery_payload and delivery_payload.get("delivery_blocked"))
        map_enabled = bool(
            order.fulfillment_type == Order.FulfillmentType.DELIVERY
            and order.status in {Order.Status.READY_FOR_DELIVERY, Order.Status.OUT_FOR_DELIVERY, Order.Status.DELIVERED}
            and map_payload
            and map_payload.get("enabled")
        )
        gps_enabled = bool(map_enabled and map_payload and map_payload.get("current_location"))
        return {
            "order_id": order.id,
            "viewer_role": viewer_role,
            "status": order.status,
            "status_label": self._status_label(order.status),
            "fulfillment_type": order.fulfillment_type,
            "fulfillment_label": "Delivery" if order.fulfillment_type == Order.FulfillmentType.DELIVERY else "Retiro",
            "payment_status": payment.status if payment else "",
            "payment_status_label": self._payment_status_label(payment.status if payment else ""),
            "tracking_mode": "geospatial" if map_enabled else "textual",
            "gps_enabled": gps_enabled,
            "map_enabled": map_enabled,
            "current_step": current_step,
            "progress": {
                "current_index": current_index,
                "total_steps": total_steps,
                "percent": progress_percent,
            },
            "steps": steps,
            "timeline": timeline,
            "summary": self._tracking_summary(order, delivery_payload),
            "participants": {
                "client_name": self._profile_name(order.client),
                "chef_name": self._chef_name(order.chef),
                "delivery_active": order.status in {Order.Status.OUT_FOR_DELIVERY, Order.Status.READY_FOR_DELIVERY},
            },
            "delivery": delivery_payload,
            "incidents": {
                "open_count": delivery_payload.get("open_incidents", 0) if delivery_payload else 0,
                "blocking_open_count": delivery_payload.get("blocking_open_incidents", 0) if delivery_payload else 0,
                "delivery_blocked": delivery_blocked,
                "items": incident_payload,
            },
            "map": map_payload,
            "pickup": self._serialize_pickup(order, viewer_role),
        }

    def _tracking_steps(self, fulfillment_type: str):
        base_steps = [
            {"status": Order.Status.PAYMENT_VALIDATING, "label": self._status_label(Order.Status.PAYMENT_VALIDATING)},
            {"status": Order.Status.AWAITING_CHEF_CONFIRMATION, "label": self._status_label(Order.Status.AWAITING_CHEF_CONFIRMATION)},
            {"status": Order.Status.ACCEPTED, "label": self._status_label(Order.Status.ACCEPTED)},
            {"status": Order.Status.PREPARING, "label": self._status_label(Order.Status.PREPARING)},
        ]
        if fulfillment_type == Order.FulfillmentType.DELIVERY:
            return [
                *base_steps,
                {"status": Order.Status.READY_FOR_DELIVERY, "label": self._status_label(Order.Status.READY_FOR_DELIVERY)},
                {"status": Order.Status.OUT_FOR_DELIVERY, "label": self._status_label(Order.Status.OUT_FOR_DELIVERY)},
                {"status": Order.Status.DELIVERED, "label": self._status_label(Order.Status.DELIVERED)},
            ]
        return [
            *base_steps,
            {"status": Order.Status.READY_FOR_PICKUP, "label": self._status_label(Order.Status.READY_FOR_PICKUP)},
            {"status": Order.Status.PICKED_UP, "label": self._status_label(Order.Status.PICKED_UP)},
        ]

    def _tracking_step_index(self, current_status: str, steps: list[dict]):
        for index, step in enumerate(steps):
            if step["status"] == current_status:
                return index
        if current_status in {Order.Status.REJECTED, Order.Status.CANCELLED, Order.Status.PAYMENT_FAILED, Order.Status.EXPIRED}:
            return max(len(steps) - 1, 0)
        return 0

    def _tracking_summary(self, order: Order, delivery_payload: dict | None = None):
        if delivery_payload and delivery_payload.get("blocking_open_incidents", 0):
            return "La entrega tiene una incidencia abierta que bloquea el cierre operativo hasta ser resuelta."
        if delivery_payload and delivery_payload.get("open_incidents", 0):
            return "La entrega tiene incidencias abiertas en seguimiento operativo."
        if order.status == Order.Status.PAYMENT_VALIDATING:
            return "Pago en validacion antes de pasar al cocinero."
        if order.status == Order.Status.AWAITING_CHEF_CONFIRMATION:
            return "El pedido ya fue creado y espera respuesta del cocinero."
        if order.status == Order.Status.ACCEPTED:
            return "El cocinero acepto el pedido y puede iniciar preparacion."
        if order.status == Order.Status.PREPARING:
            return "El pedido esta siendo preparado."
        if order.status == Order.Status.READY_FOR_PICKUP:
            return "El pedido esta listo para retiro."
        if order.status == Order.Status.READY_FOR_DELIVERY:
            return "El pedido esta listo para ser entregado."
        if order.status == Order.Status.OUT_FOR_DELIVERY:
            return "El pedido va en camino al cliente."
        if order.status == Order.Status.PICKED_UP:
            return "El cliente retiro el pedido."
        if order.status == Order.Status.DELIVERED:
            return "El pedido fue entregado correctamente."
        if order.status == Order.Status.REJECTED:
            return "El cocinero rechazo el pedido."
        if order.status == Order.Status.CANCELLED:
            return "El pedido fue cancelado."
        if order.status == Order.Status.PAYMENT_FAILED:
            return "El pago fallo y el pedido no avanzo."
        if order.status == Order.Status.EXPIRED:
            return "El pedido expiro antes de completarse."
        return self._status_label(order.status)

    def _status_label(self, status_value: str):
        labels = {
            Order.Status.PAYMENT_VALIDATING: "Validando pago",
            Order.Status.PAYMENT_FAILED: "Pago fallido",
            Order.Status.AWAITING_CHEF_CONFIRMATION: "Esperando al cocinero",
            Order.Status.ACCEPTED: "Aceptado",
            Order.Status.REJECTED: "Rechazado",
            Order.Status.PREPARING: "En preparacion",
            Order.Status.READY_FOR_PICKUP: "Listo para retiro",
            Order.Status.READY_FOR_DELIVERY: "Listo para delivery",
            Order.Status.OUT_FOR_DELIVERY: "En camino",
            Order.Status.PICKED_UP: "Retirado",
            Order.Status.DELIVERED: "Entregado",
            Order.Status.CANCELLED: "Cancelado",
            Order.Status.EXPIRED: "Expirado",
        }
        return labels.get(status_value, status_value)

    def _payment_status_label(self, status_value: str):
        labels = {
            OrderPayment.Status.PENDING: "Pendiente de cobro",
            OrderPayment.Status.PROCESSING: "Procesando pago",
            OrderPayment.Status.CONFIRMED: "Cobro confirmado",
            OrderPayment.Status.CANCELLED: "Pago cancelado",
            OrderPayment.Status.FAILED: "Pago fallido",
            OrderPayment.Status.EXPIRED: "Pago expirado",
        }
        return labels.get(status_value, status_value or "-")

    def _delivery_status_label(self, status_value: str):
        labels = {
            DeliveryAssignment.Status.UNASSIGNED: "Sin repartidor asignado",
            DeliveryAssignment.Status.ASSIGNED: "Repartidor asignado",
            DeliveryAssignment.Status.EN_ROUTE_TO_CHEF: "En camino al cocinero",
            DeliveryAssignment.Status.AT_CHEF: "En punto de recogida",
            DeliveryAssignment.Status.PICKED_UP: "Pedido recogido",
            DeliveryAssignment.Status.EN_ROUTE_TO_CLIENT: "En camino al cliente",
            DeliveryAssignment.Status.DELIVERED: "Entregado",
            DeliveryAssignment.Status.FAILED: "Entrega fallida",
            DeliveryAssignment.Status.CANCELLED: "Entrega cancelada",
        }
        return labels.get(status_value, status_value or "-")

    def _get_order_for_chef(self, chef: UserProfile, order_id: str, lock: bool = False):
        queryset = Order.objects.filter(id=str(order_id), chef=chef)
        if lock:
            queryset = queryset.select_for_update()
        order = (
            queryset
            .select_related("client", "chef")
            .prefetch_related("items", "payments", "timeline_events", "status_history", "pickup_confirmation", "delivery_assignment", "delivery_assignment__delivery_user", "delivery_assignment__location_pings", "delivery_assignment__incidents")
            .first()
        )
        if not order:
            raise OrderCashServiceError("Pedido no encontrado para el cocinero.", "order_not_found")
        return order

    def _get_order_for_client(self, client: UserProfile, order_id: str, lock: bool = False):
        queryset = Order.objects.filter(id=str(order_id), client=client)
        if lock:
            queryset = queryset.select_for_update()
        order = (
            queryset.select_related("client", "chef")
            .prefetch_related("items", "payments", "timeline_events", "status_history", "pickup_confirmation", "qr_sessions", "delivery_assignment", "delivery_assignment__delivery_user", "delivery_assignment__location_pings", "delivery_assignment__incidents")
            .first()
        )
        if not order:
            raise OrderCashServiceError("Pedido no encontrado para el cliente.", "order_not_found")
        return order

    def _get_delivery_cash_order(self, order_id: str):
        order = (
            Order.objects.select_for_update()
            .filter(
                id=str(order_id),
                payment_method=Order.PaymentMethod.CASH,
                fulfillment_type=Order.FulfillmentType.DELIVERY,
            )
            .select_related("client", "chef")
            .prefetch_related("items", "payments", "timeline_events", "status_history", "delivery_assignment", "delivery_assignment__delivery_user", "delivery_assignment__location_pings", "delivery_assignment__incidents")
            .first()
        )
        if not order:
            raise OrderCashServiceError("Pedido delivery cash no encontrado.", "order_not_found")
        return order

    def _get_pending_cash_payment(self, order: Order):
        payment = self._latest_payment(order)
        if not payment or payment.method != Order.PaymentMethod.CASH:
            raise OrderCashServiceError("El pedido no tiene un pago cash valido.", "payment_not_found")
        if payment.status == OrderPayment.Status.CONFIRMED:
            raise OrderCashServiceError("El pago cash ya fue confirmado anteriormente.", "payment_already_confirmed")
        if payment.status != OrderPayment.Status.PENDING:
            raise OrderCashServiceError("El pago no esta en estado pendiente de cobranza.", "payment_invalid_status")
        return payment

    def _get_pending_pickup_confirmation(self, order: Order):
        pickup = getattr(order, "pickup_confirmation", None)
        if not pickup:
            raise OrderCashServiceError("El pedido no tiene configuracion de retiro en punto.", "pickup_not_found")
        if pickup.status == PickupConfirmation.Status.CONFIRMED:
            raise OrderCashServiceError("El retiro ya fue confirmado anteriormente.", "pickup_already_confirmed")
        if pickup.status != PickupConfirmation.Status.PENDING:
            raise OrderCashServiceError("El retiro no se encuentra disponible para confirmacion.", "pickup_invalid_status")
        return pickup

    def _move_order(self, order: Order, to_status: str, actor_role: str, actor_id: str, notes: str, event_code: str, event_label: str):
        from_status = order.status
        order.status = to_status
        order.last_status_at = timezone.now()
        order.save(update_fields=["status", "last_status_at", "updated_at"])
        OrderStatusHistory.objects.create(
            order=order,
            from_status=from_status,
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
            metadata={"from_status": from_status, "to_status": to_status},
        )
        order.refresh_from_db()

    def _confirm_payment(self, payment: OrderPayment, actor: UserProfile, actor_role: str, metadata: dict):
        payment.status = OrderPayment.Status.CONFIRMED
        payment.confirmed_by_role = actor_role
        payment.confirmed_by_user = actor
        payment.confirmed_at = timezone.now()
        payment.save(update_fields=["status", "confirmed_by_role", "confirmed_by_user", "confirmed_at", "updated_at"])
        OrderPaymentEvent.objects.create(
            payment=payment,
            event_code="CASH_PAYMENT_CONFIRMED",
            event_label="Pago en efectivo confirmado",
            actor_role=actor_role,
            actor_id=str(actor.supabase_user_id),
            metadata=metadata,
        )
        OrderTimelineEvent.objects.create(
            order=payment.order,
            event_code="PAYMENT_CONFIRMED",
            event_label="Pago en efectivo confirmado",
            actor_role=actor_role,
            actor_id=str(actor.supabase_user_id),
            metadata={"payment_id": payment.id, **metadata},
        )

    def _confirm_pickup_confirmation(self, pickup: PickupConfirmation, actor: UserProfile, actor_role: str):
        pickup.status = PickupConfirmation.Status.CONFIRMED
        pickup.confirmed_by_role = actor_role
        pickup.confirmed_by_user = actor
        pickup.confirmed_at = timezone.now()
        pickup.save(update_fields=["status", "confirmed_by_role", "confirmed_by_user", "confirmed_at", "updated_at"])
        OrderTimelineEvent.objects.create(
            order=pickup.order,
            event_code="PICKUP_CONFIRMED",
            event_label="Retiro confirmado con codigo",
            actor_role=actor_role,
            actor_id=str(actor.supabase_user_id),
            metadata={"pickup_code": pickup.pickup_code},
        )

    def _assert_status(self, order: Order, allowed_statuses: list[str], code: str, message: str):
        if order.status not in allowed_statuses:
            raise OrderCashServiceError(message, code, {"current_status": order.status})

    def _require_profile(self, user_id: str, role: str, code: str, message: str):
        parsed = self._parse_uuid(user_id)
        profile = UserProfile.objects.filter(supabase_user_id=parsed, role=role).first() if parsed else None
        if not profile:
            raise OrderCashServiceError(message, code)
        return profile

    def _latest_payment(self, order: Order):
        payments = list(order.payments.all())
        if not payments:
            return None
        payments.sort(key=lambda item: item.created_at, reverse=True)
        return payments[0]

    def _delivery_started_by(self, order: Order):
        starts = [event for event in order.timeline_events.all() if event.event_code == "DELIVERY_STARTED"]
        if not starts:
            return ""
        starts.sort(key=lambda item: item.occurred_at, reverse=True)
        return str(starts[0].actor_id or "")

    def _cancel_qr_sessions(self, order: Order, reason: str = "Cancelado por cliente"):
        queryset = order.qr_sessions.exclude(
            status__in=[
                SimulatedQRPaymentSession.Status.CONFIRMED,
                SimulatedQRPaymentSession.Status.CANCELLED,
                SimulatedQRPaymentSession.Status.EXPIRED,
                SimulatedQRPaymentSession.Status.INVALIDATED,
            ]
        )
        for session in queryset:
            session.status = SimulatedQRPaymentSession.Status.CANCELLED
            session.cancelled_at = timezone.now()
            session.invalidated_at = timezone.now()
            session.invalidated_reason = reason
            session.save(update_fields=["status", "cancelled_at", "invalidated_at", "invalidated_reason", "updated_at"])

    def _cancel_pickup_confirmation(self, order: Order, reason: str):
        pickup = getattr(order, "pickup_confirmation", None)
        if not pickup or pickup.status != PickupConfirmation.Status.PENDING:
            return
        pickup.status = PickupConfirmation.Status.CANCELLED
        pickup.cancelled_at = timezone.now()
        pickup.save(update_fields=["status", "cancelled_at", "updated_at"])
        OrderTimelineEvent.objects.create(
            order=order,
            event_code="PICKUP_CANCELLED",
            event_label="Retiro en punto cancelado",
            actor_role="SISTEMA",
            actor_id="",
            metadata={"reason": reason},
        )

    def _chef_name(self, chef: UserProfile):
        try:
            profile = ChefProfile.objects.filter(chef=chef).first()
        except Exception:
            profile = None
        if profile and getattr(profile, "business_name", ""):
            return profile.business_name
        return self._profile_name(chef)

    def _profile_name(self, profile: UserProfile):
        name = f"{profile.first_name} {profile.last_name}".strip()
        return name or profile.full_name or profile.email

    def _parse_uuid(self, value):
        try:
            return UUID(str(value))
        except (TypeError, ValueError):
            return None

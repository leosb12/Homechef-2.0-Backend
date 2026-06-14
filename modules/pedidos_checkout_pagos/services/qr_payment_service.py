from datetime import timedelta
from time import sleep
from uuid import UUID, uuid4

from modules.confianza_administracion_seguridad.services import NotificationService
from django.db import transaction
from django.utils import timezone

from modules.gestion_usuarios_acceso_suscripcion.models import UserProfile
from modules.pedidos_checkout_pagos.models import Order, OrderPayment, OrderPaymentEvent, OrderStatusHistory, OrderTimelineEvent, SimulatedQRPaymentSession
from modules.pedidos_checkout_pagos.services.stock_service import DishStockService


class QRPaymentServiceError(ValueError):
    def __init__(self, message: str, code: str, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}


class QRPaymentService:
    SESSION_TTL_MINUTES = 10

    def __init__(self):
        self.stock_service = DishStockService()
        self.notification_service = NotificationService()

    @transaction.atomic
    def create_session(self, order: Order, payment: OrderPayment, actor_id: str):
        self._invalidate_active_sessions(payment, "replaced_by_new_session")
        session_code = f"qr-{uuid4().hex}"
        expires_at = timezone.now() + timedelta(minutes=self.SESSION_TTL_MINUTES)
        session = SimulatedQRPaymentSession.objects.create(
            payment=payment,
            order=order,
            session_code=session_code,
            amount=payment.amount,
            currency=payment.currency,
            expires_at=expires_at,
            metadata={"created_by": str(actor_id)},
        )
        payment.provider = "QR_SIMULATED"
        payment.payment_url = f"/client/payments/qr-simulado?session_code={session_code}"
        payment.external_reference = session_code
        payment.qr_session_code = session_code
        payment.expires_at = expires_at
        payment.provider_payload = {
            "session_code": session_code,
            "type": "qr_simulado",
            "expires_at": expires_at.isoformat(),
        }
        payment.save(
            update_fields=[
                "provider",
                "payment_url",
                "external_reference",
                "qr_session_code",
                "expires_at",
                "provider_payload",
                "updated_at",
            ]
        )
        OrderPaymentEvent.objects.create(
            payment=payment,
            event_code="QR_SESSION_CREATED",
            event_label="Sesion QR simulada creada",
            actor_role="SISTEMA",
            actor_id=str(actor_id),
            metadata={"session_code": session_code, "expires_at": expires_at.isoformat()},
        )
        OrderTimelineEvent.objects.create(
            order=order,
            event_code="QR_SESSION_CREATED",
            event_label="QR simulado generado",
            actor_role="SISTEMA",
            actor_id=str(actor_id),
            metadata={"payment_id": payment.id, "session_code": session_code},
        )
        return session

    def get_session_for_client(self, user_id: str, session_code: str):
        client = self._require_client(user_id)
        session = self._get_session(session_code)
        if session.order.client_id != client.id:
            raise QRPaymentServiceError("La sesion QR no pertenece a tu pedido.", "session_not_found")
        self._expire_if_needed(session)
        session.refresh_from_db()
        return self._serialize_session(session)

    @transaction.atomic
    def start_session(self, user_id: str, session_code: str):
        client = self._require_client(user_id)
        session = self._get_session_for_update(session_code)
        if session.order.client_id != client.id:
            raise QRPaymentServiceError("La sesion QR no pertenece a tu pedido.", "session_not_found")
        self._expire_if_needed(session)
        session.refresh_from_db()
        if session.status != SimulatedQRPaymentSession.Status.PENDING:
            raise QRPaymentServiceError("La sesion QR ya no se puede iniciar.", "session_invalid_status", {"status": session.status})
        session.status = SimulatedQRPaymentSession.Status.PROCESSING
        session.started_at = timezone.now()
        session.save(update_fields=["status", "started_at", "updated_at"])
        payment = session.payment
        payment.status = OrderPayment.Status.PROCESSING
        payment.save(update_fields=["status", "updated_at"])
        OrderPaymentEvent.objects.create(
            payment=payment,
            event_code="QR_PAYMENT_STARTED",
            event_label="Pago QR simulado iniciado",
            actor_role="CLIENTE",
            actor_id=str(client.supabase_user_id),
            metadata={"session_code": session.session_code},
        )
        OrderTimelineEvent.objects.create(
            order=session.order,
            event_code="QR_PAYMENT_STARTED",
            event_label="Cliente inicio pago QR simulado",
            actor_role="CLIENTE",
            actor_id=str(client.supabase_user_id),
            metadata={"session_code": session.session_code},
        )
        return self._serialize_session(session)

    @transaction.atomic
    def confirm_session(self, user_id: str, session_code: str):
        client = self._require_client(user_id)
        session = self._get_session_for_update(session_code)
        if session.order.client_id != client.id:
            raise QRPaymentServiceError("La sesion QR no pertenece a tu pedido.", "session_not_found")
        self._expire_if_needed(session)
        session.refresh_from_db()
        if session.status == SimulatedQRPaymentSession.Status.CONFIRMED:
            raise QRPaymentServiceError("La sesion QR ya fue usada anteriormente.", "session_already_confirmed")
        if session.status != SimulatedQRPaymentSession.Status.PROCESSING:
            raise QRPaymentServiceError("La sesion QR debe iniciarse antes de confirmar.", "session_invalid_status", {"status": session.status})
        sleep(3)
        now = timezone.now()
        session.status = SimulatedQRPaymentSession.Status.CONFIRMED
        session.confirmed_at = now
        session.save(update_fields=["status", "confirmed_at", "updated_at"])

        payment = session.payment
        payment.status = OrderPayment.Status.CONFIRMED
        payment.confirmed_by_role = "CLIENTE"
        payment.confirmed_by_user = client
        payment.confirmed_at = now
        payment.save(
            update_fields=[
                "status",
                "confirmed_by_role",
                "confirmed_by_user",
                "confirmed_at",
                "updated_at",
            ]
        )

        order = session.order
        self._move_order(order, Order.Status.PAID, "CLIENTE", str(client.supabase_user_id), "Pago QR simulado confirmado", "PAYMENT_CONFIRMED", "Pago QR confirmado")
        self._move_order(order, Order.Status.AWAITING_CHEF_CONFIRMATION, "SISTEMA", str(client.supabase_user_id), "Pedido pagado y enviado al cocinero", "ORDER_READY_FOR_CHEF", "Pedido listo para gestion del cocinero")
        OrderPaymentEvent.objects.create(
            payment=payment,
            event_code="QR_PAYMENT_CONFIRMED",
            event_label="Pago QR simulado confirmado",
            actor_role="CLIENTE",
            actor_id=str(client.supabase_user_id),
            metadata={"session_code": session.session_code},
        )
        self.notification_service.notify_payment_confirmed(order, payment)
        return {
            "session": self._serialize_session(session),
            "order_id": order.id,
            "order_status": order.status,
            "payment_status": payment.status,
        }

    @transaction.atomic
    def cancel_session(self, user_id: str, session_code: str):
        client = self._require_client(user_id)
        session = self._get_session_for_update(session_code)
        if session.order.client_id != client.id:
            raise QRPaymentServiceError("La sesion QR no pertenece a tu pedido.", "session_not_found")
        self._expire_if_needed(session)
        session.refresh_from_db()
        if session.status in {
            SimulatedQRPaymentSession.Status.CONFIRMED,
            SimulatedQRPaymentSession.Status.CANCELLED,
            SimulatedQRPaymentSession.Status.EXPIRED,
            SimulatedQRPaymentSession.Status.INVALIDATED,
        }:
            raise QRPaymentServiceError("La sesion QR ya no se puede cancelar.", "session_invalid_status", {"status": session.status})

        now = timezone.now()
        session.status = SimulatedQRPaymentSession.Status.CANCELLED
        session.cancelled_at = now
        session.invalidated_at = now
        session.invalidated_reason = "cancelled_by_client"
        session.save(update_fields=["status", "cancelled_at", "invalidated_at", "invalidated_reason", "updated_at"])

        payment = session.payment
        payment.status = OrderPayment.Status.CANCELLED
        payment.failure_reason = "QR simulado cancelado por cliente"
        payment.save(update_fields=["status", "failure_reason", "updated_at"])

        order = session.order
        if order.stock_reserved:
            self.stock_service.release_order_stock(order)
        self._move_order(order, Order.Status.CANCELLED, "CLIENTE", str(client.supabase_user_id), "Pago QR cancelado por cliente", "PAYMENT_CANCELLED", "Pago QR cancelado")

        OrderPaymentEvent.objects.create(
            payment=payment,
            event_code="QR_PAYMENT_CANCELLED",
            event_label="Pago QR simulado cancelado",
            actor_role="CLIENTE",
            actor_id=str(client.supabase_user_id),
            metadata={"session_code": session.session_code},
        )
        return {
            "session": self._serialize_session(session),
            "order_id": order.id,
            "order_status": order.status,
            "payment_status": payment.status,
        }

    def _invalidate_active_sessions(self, payment: OrderPayment, reason: str):
        now = timezone.now()
        sessions = payment.qr_sessions.filter(status__in=[SimulatedQRPaymentSession.Status.PENDING, SimulatedQRPaymentSession.Status.PROCESSING])
        for session in sessions:
            session.status = SimulatedQRPaymentSession.Status.INVALIDATED
            session.invalidated_at = now
            session.invalidated_reason = reason
            session.save(update_fields=["status", "invalidated_at", "invalidated_reason", "updated_at"])

    def _expire_if_needed(self, session: SimulatedQRPaymentSession):
        if session.status in {
            SimulatedQRPaymentSession.Status.CONFIRMED,
            SimulatedQRPaymentSession.Status.CANCELLED,
            SimulatedQRPaymentSession.Status.EXPIRED,
            SimulatedQRPaymentSession.Status.INVALIDATED,
        }:
            return
        if session.expires_at > timezone.now():
            return
        now = timezone.now()
        session.status = SimulatedQRPaymentSession.Status.EXPIRED
        session.invalidated_at = now
        session.invalidated_reason = "expired"
        session.save(update_fields=["status", "invalidated_at", "invalidated_reason", "updated_at"])
        payment = session.payment
        payment.status = OrderPayment.Status.EXPIRED
        payment.failure_reason = "QR simulado expirado"
        payment.save(update_fields=["status", "failure_reason", "updated_at"])
        order = session.order
        if order.stock_reserved:
            self.stock_service.release_order_stock(order)
        self._move_order(order, Order.Status.EXPIRED, "SISTEMA", "", "Sesion QR expirada", "PAYMENT_EXPIRED", "Pago QR expirado")
        OrderPaymentEvent.objects.create(
            payment=payment,
            event_code="QR_PAYMENT_EXPIRED",
            event_label="Pago QR simulado expirado",
            actor_role="SISTEMA",
            actor_id="",
            metadata={"session_code": session.session_code},
        )

    def _serialize_session(self, session: SimulatedQRPaymentSession):
        payment = session.payment
        return {
            "session_code": session.session_code,
            "status": session.status,
            "bank_name": session.bank_name,
            "bank_account_label": session.bank_account_label,
            "amount": float(session.amount),
            "currency": session.currency,
            "expires_at": session.expires_at.isoformat(),
            "payment_status": payment.status,
            "payment_method": payment.method,
            "payment_url": payment.payment_url,
            "order": {
                "id": session.order.id,
                "status": session.order.status,
                "total": float(session.order.total),
                "fulfillment_type": session.order.fulfillment_type,
            },
        }

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

    def _require_client(self, user_id: str):
        parsed = self._parse_uuid(user_id)
        client = UserProfile.objects.filter(supabase_user_id=parsed, role=UserProfile.ROLE_CLIENT).first() if parsed else None
        if not client:
            raise QRPaymentServiceError("Perfil de cliente no encontrado.", "client_not_found")
        return client

    def _get_session(self, session_code: str):
        session = (
            SimulatedQRPaymentSession.objects.filter(session_code=str(session_code))
            .select_related("payment", "order", "order__client")
            .first()
        )
        if not session:
            raise QRPaymentServiceError("Sesion QR simulada no encontrada.", "session_not_found")
        return session

    def _get_session_for_update(self, session_code: str):
        session = (
            SimulatedQRPaymentSession.objects.select_for_update()
            .filter(session_code=str(session_code))
            .select_related("payment", "order", "order__client")
            .first()
        )
        if not session:
            raise QRPaymentServiceError("Sesion QR simulada no encontrada.", "session_not_found")
        return session

    def _parse_uuid(self, value):
        try:
            return UUID(str(value))
        except (TypeError, ValueError):
            return None

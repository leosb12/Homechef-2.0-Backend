from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID, uuid4
from urllib.parse import parse_qsl, urlsplit, urlunsplit

import requests
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from modules.confianza_administracion_seguridad.services import NotificationService
from modules.gestion_usuarios_acceso_suscripcion.models import UserProfile
from modules.pedidos_checkout_pagos.models import Order, OrderPayment, OrderPaymentEvent, OrderStatusHistory, OrderTimelineEvent
from modules.pedidos_checkout_pagos.services.order_receipt_service import OrderReceiptService
from modules.pedidos_checkout_pagos.services.stock_service import DishStockService


class OrderCoinGateServiceError(ValueError):
    def __init__(self, message: str, code: str, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}


class OrderCoinGateService:
    COINGATE_APPROVED = {"paid", "confirmed"}
    COINGATE_PENDING = {"new", "pending", "confirming"}
    COINGATE_REJECTED = {"invalid", "expired", "canceled", "refunded"}
    BOB_PER_USD = Decimal("6.91")

    def __init__(self):
        self.stock_service = DishStockService()
        self.notification_service = NotificationService()
        self.receipt_service = OrderReceiptService()

    def create_payment(self, *, order: Order, payment: OrderPayment, success_redirect_to: str = "", cancel_redirect_to: str = ""):
        if not settings.COINGATE_API_BASE_URL or not settings.COINGATE_API_TOKEN:
            raise OrderCoinGateServiceError("CoinGate no esta configurado en el backend.", "provider_not_configured")

        success_url = str(success_redirect_to or settings.ORDER_COINGATE_SUCCESS_URL).strip()
        cancel_url = str(cancel_redirect_to or settings.ORDER_COINGATE_CANCEL_URL).strip()
        callback_url = settings.ORDER_COINGATE_CALLBACK_URL
        homechef_order_id = f"homechef-order-{payment.id}-{uuid4().hex}"
        provider_amount, provider_currency = self._provider_pricing(payment)
        body = {
            "order_id": homechef_order_id,
            "price_amount": str(provider_amount),
            "price_currency": provider_currency,
            "receive_currency": settings.COINGATE_RECEIVE_CURRENCY,
            "callback_url": callback_url,
            "success_url": with_query_param(success_url, "coingate_order_id", homechef_order_id),
            "cancel_url": with_query_param(cancel_url, "coingate_order_id", homechef_order_id),
            "title": f"Pedido HomeChef {order.id}",
            "description": f"Pago de pedido HomeChef {order.id}",
        }
        headers = {
            "Authorization": f"Bearer {settings.COINGATE_API_TOKEN}",
            "Content-Type": "application/json",
        }
        try:
            response = requests.post(
                f"{settings.COINGATE_API_BASE_URL.rstrip('/')}/orders",
                json=body,
                headers=headers,
                timeout=15,
            )
            response_data = response.json()
        except requests.RequestException as exc:
            raise OrderCoinGateServiceError("No se pudo crear la orden CoinGate.", "provider_request_failed", {"error": str(exc)})
        except ValueError:
            raise OrderCoinGateServiceError("CoinGate devolvio una respuesta invalida.", "provider_invalid_response")

        if response.status_code >= 400:
            raise OrderCoinGateServiceError(
                "CoinGate rechazo la creacion de la orden.",
                "provider_rejected",
                {"provider_response": response_data},
            )

        payment_url = response_data.get("payment_url") or response_data.get("checkout_url") or response_data.get("url", "")
        external_reference = str(response_data.get("id") or response_data.get("order_id") or homechef_order_id)
        if not payment_url:
            raise OrderCoinGateServiceError("CoinGate no devolvio una URL de pago valida.", "payment_url_missing")

        response_data["homechef_order_id"] = homechef_order_id
        response_data["success_url"] = body["success_url"]
        response_data["cancel_url"] = body["cancel_url"]
        response_data["provider_price_amount"] = str(provider_amount)
        response_data["provider_price_currency"] = provider_currency
        payment.provider = "COINGATE_SANDBOX"
        payment.status = OrderPayment.Status.PENDING
        payment.payment_url = payment_url
        payment.external_reference = external_reference
        payment.provider_payload = response_data
        payment.save(update_fields=["provider", "status", "payment_url", "external_reference", "provider_payload", "updated_at"])

        OrderPaymentEvent.objects.create(
            payment=payment,
            event_code="COINGATE_PAYMENT_CREATED",
            event_label="Orden CoinGate creada",
            actor_role="SISTEMA",
            actor_id="",
            metadata={"external_reference": external_reference, "homechef_order_id": homechef_order_id},
        )
        OrderTimelineEvent.objects.create(
            order=order,
            event_code="COINGATE_PAYMENT_CREATED",
            event_label="Orden CoinGate creada",
            actor_role="SISTEMA",
            actor_id="",
            metadata={"payment_id": payment.id, "external_reference": external_reference, "homechef_order_id": homechef_order_id},
        )
        return payment

    def _provider_pricing(self, payment: OrderPayment):
        currency = str(payment.currency or "").upper()
        amount = Decimal(str(payment.amount or 0))
        if currency == "BOB":
            converted = (amount / self.BOB_PER_USD).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            return converted, "USD"
        return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), currency or "USD"

    @transaction.atomic
    def confirm_checkout_return(self, user_id: str, *, provider: str = "", coingate_order_id: str = ""):
        client = self._require_client(user_id)
        payment = self._find_client_payment(client, coingate_order_id)
        if not payment:
            raise OrderCoinGateServiceError("No se encontro un pago CoinGate para este cliente.", "payment_not_found")
        result = self.confirm_payment_state(payment, coingate_order_id=coingate_order_id, trust_provider_return=str(provider or "").upper() == "COINGATE_SANDBOX" and settings.DEBUG)
        return result

    @transaction.atomic
    def handle_callback(self, payload: dict):
        external_reference = str(payload.get("id") or "").strip()
        order_id = str(payload.get("order_id") or "").strip()
        payment = self._find_payment(external_reference=external_reference, coingate_order_id=order_id, lock=True)
        if not payment:
            return False
        return self._apply_provider_state(payment, payload)

    @transaction.atomic
    def confirm_payment_state(self, payment: OrderPayment, *, coingate_order_id: str = "", trust_provider_return: bool = False):
        if trust_provider_return and settings.DEBUG:
            self._approve_payment(payment, {"sandbox_return": True, "coingate_order_id": coingate_order_id})
            return self._return_payload(payment, handled=True)

        headers = {
            "Authorization": f"Bearer {settings.COINGATE_API_TOKEN}",
            "Content-Type": "application/json",
        }
        try:
            response = requests.get(
                f"{settings.COINGATE_API_BASE_URL.rstrip('/')}/orders/{payment.external_reference}",
                headers=headers,
                timeout=15,
            )
            response_data = response.json()
        except requests.RequestException as exc:
            raise OrderCoinGateServiceError("No se pudo consultar el estado del pago CoinGate.", "provider_request_failed", {"error": str(exc)})
        except ValueError:
            raise OrderCoinGateServiceError("CoinGate devolvio una respuesta invalida.", "provider_invalid_response")

        if response.status_code >= 400:
            raise OrderCoinGateServiceError("CoinGate no permitio consultar el pago.", "provider_rejected", {"provider_response": response_data})

        self._apply_provider_state(payment, response_data)
        return self._return_payload(payment, handled=True)

    def _apply_provider_state(self, payment: OrderPayment, payload: dict):
        status = str(payload.get("status") or "").lower()
        if status in self.COINGATE_APPROVED:
            self._approve_payment(payment, payload)
            return True
        if status in self.COINGATE_PENDING:
            self._mark_pending(payment, payload)
            return True
        if status == "canceled":
            self._cancel_payment(payment, payload, "CoinGate canceled")
            return True
        if status == "expired":
            self._expire_payment(payment, payload)
            return True
        self._fail_payment(payment, payload, f"CoinGate {status or 'failed'}")
        return True

    def _approve_payment(self, payment: OrderPayment, provider_response: dict):
        if payment.status == OrderPayment.Status.CONFIRMED:
            return
        now = timezone.now()
        payment.status = OrderPayment.Status.CONFIRMED
        payment.confirmed_at = now
        payment.confirmed_by_role = "SISTEMA"
        payment.provider_payload = self._merge_response(payment.provider_payload, provider_response)
        payment.failure_reason = ""
        payment.save(update_fields=["status", "confirmed_at", "confirmed_by_role", "provider_payload", "failure_reason", "updated_at"])

        order = payment.order
        self._move_order(order, Order.Status.PAID, "SISTEMA", "", "Pago CoinGate confirmado", "PAYMENT_CONFIRMED", "Pago CoinGate confirmado")
        self._move_order(order, Order.Status.AWAITING_CHEF_CONFIRMATION, "SISTEMA", "", "Pedido pagado y enviado al cocinero", "ORDER_READY_FOR_CHEF", "Pedido listo para gestion del cocinero")
        OrderPaymentEvent.objects.create(
            payment=payment,
            event_code="COINGATE_PAYMENT_CONFIRMED",
            event_label="Pago CoinGate confirmado",
            actor_role="SISTEMA",
            actor_id="",
            metadata={"provider_response": provider_response},
        )
        self.receipt_service.ensure_receipt(order, payment, metadata={"provider_response": provider_response})
        self.notification_service.notify_payment_confirmed(order, payment)

    def _mark_pending(self, payment: OrderPayment, provider_response: dict):
        payment.status = OrderPayment.Status.PENDING
        payment.provider_payload = self._merge_response(payment.provider_payload, provider_response)
        payment.save(update_fields=["status", "provider_payload", "updated_at"])
        order = payment.order
        if order.status != Order.Status.PAYMENT_VALIDATING:
            self._move_order(order, Order.Status.PAYMENT_VALIDATING, "SISTEMA", "", "Pago CoinGate sigue pendiente", "PAYMENT_PENDING", "Pago CoinGate pendiente")
        OrderPaymentEvent.objects.create(
            payment=payment,
            event_code="COINGATE_PAYMENT_PENDING",
            event_label="Pago CoinGate pendiente",
            actor_role="SISTEMA",
            actor_id="",
            metadata={"provider_response": provider_response},
        )

    def _cancel_payment(self, payment: OrderPayment, provider_response: dict, reason: str):
        payment.status = OrderPayment.Status.CANCELLED
        payment.failure_reason = reason[:255]
        payment.provider_payload = self._merge_response(payment.provider_payload, provider_response)
        payment.save(update_fields=["status", "failure_reason", "provider_payload", "updated_at"])
        order = payment.order
        if order.stock_reserved:
            self.stock_service.release_order_stock(order)
        self._move_order(order, Order.Status.CANCELLED, "SISTEMA", "", reason, "PAYMENT_CANCELLED", "Pago CoinGate cancelado")
        OrderPaymentEvent.objects.create(
            payment=payment,
            event_code="COINGATE_PAYMENT_CANCELLED",
            event_label="Pago CoinGate cancelado",
            actor_role="SISTEMA",
            actor_id="",
            metadata={"provider_response": provider_response},
        )

    def _expire_payment(self, payment: OrderPayment, provider_response: dict):
        payment.status = OrderPayment.Status.EXPIRED
        payment.failure_reason = "CoinGate expired"
        payment.provider_payload = self._merge_response(payment.provider_payload, provider_response)
        payment.save(update_fields=["status", "failure_reason", "provider_payload", "updated_at"])
        order = payment.order
        if order.stock_reserved:
            self.stock_service.release_order_stock(order)
        self._move_order(order, Order.Status.EXPIRED, "SISTEMA", "", "Pago CoinGate expirado", "PAYMENT_EXPIRED", "Pago CoinGate expirado")
        OrderPaymentEvent.objects.create(
            payment=payment,
            event_code="COINGATE_PAYMENT_EXPIRED",
            event_label="Pago CoinGate expirado",
            actor_role="SISTEMA",
            actor_id="",
            metadata={"provider_response": provider_response},
        )

    def _fail_payment(self, payment: OrderPayment, provider_response: dict, reason: str):
        payment.status = OrderPayment.Status.FAILED
        payment.failure_reason = reason[:255]
        payment.provider_payload = self._merge_response(payment.provider_payload, provider_response)
        payment.save(update_fields=["status", "failure_reason", "provider_payload", "updated_at"])
        order = payment.order
        if order.stock_reserved:
            self.stock_service.release_order_stock(order)
        self._move_order(order, Order.Status.PAYMENT_FAILED, "SISTEMA", "", reason, "PAYMENT_FAILED", "Pago CoinGate fallido")
        OrderPaymentEvent.objects.create(
            payment=payment,
            event_code="COINGATE_PAYMENT_FAILED",
            event_label="Pago CoinGate fallido",
            actor_role="SISTEMA",
            actor_id="",
            metadata={"provider_response": provider_response},
        )

    def _return_payload(self, payment: OrderPayment, *, handled: bool):
        payment.refresh_from_db()
        order = payment.order
        return {
            "handled": handled,
            "provider": payment.provider,
            "payment_status": payment.status,
            "order_status": order.status,
            "order_id": order.id,
            "payment_url": payment.payment_url,
            "external_reference": payment.external_reference,
        }

    def _find_client_payment(self, client: UserProfile, coingate_order_id: str):
        query = Q(order__client=client, method=Order.PaymentMethod.BITCOIN_COINGATE)
        if coingate_order_id:
            query &= (
                Q(external_reference=coingate_order_id)
                | Q(provider_payload__homechef_order_id=coingate_order_id)
                | Q(provider_payload__order_id=coingate_order_id)
            )
        return OrderPayment.objects.filter(query).select_related("order").order_by("-created_at").first()

    def _find_payment(self, *, external_reference: str = "", coingate_order_id: str = "", lock: bool = False):
        queryset = OrderPayment.objects.filter(method=Order.PaymentMethod.BITCOIN_COINGATE)
        if lock:
            queryset = queryset.select_for_update()
        query = Q()
        if external_reference:
            query |= Q(external_reference=external_reference)
        if coingate_order_id:
            query |= Q(external_reference=coingate_order_id) | Q(provider_payload__homechef_order_id=coingate_order_id) | Q(provider_payload__order_id=coingate_order_id)
        if not query:
            return None
        return queryset.select_related("order").filter(query).first()

    def _move_order(self, order: Order, to_status: str, actor_role: str, actor_id: str, notes: str, event_code: str, event_label: str):
        from_status = order.status
        if from_status == to_status:
            return
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
            raise OrderCoinGateServiceError("Perfil de cliente no encontrado.", "client_not_found")
        return client

    def _parse_uuid(self, value):
        try:
            return UUID(str(value))
        except (TypeError, ValueError):
            return None

    def _merge_response(self, current: dict | None, incoming: dict | None):
        data = dict(current or {})
        if incoming:
            data["callback"] = incoming
        return data


def with_query_param(url, key, value):
    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    if any(item_key == key for item_key, _ in query):
        return url
    separator = "&" if parts.query else ""
    next_query = f"{parts.query}{separator}{key}={value}"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, next_query, parts.fragment))

import json
import os
from typing import Iterable
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from modules.confianza_administracion_seguridad.models import NotificationDeviceToken, OperationalNotification
from modules.delivery_logistica.models import DeliveryAssignment, DeliveryIncident
from modules.gestion_cocinero.models import ChefProfile
from modules.gestion_usuarios_acceso_suscripcion.models import UserProfile
from modules.pedidos_checkout_pagos.models import Order, OrderPayment

try:
    import firebase_admin
    from firebase_admin import credentials, messaging
except Exception:  # pragma: no cover - optional runtime dependency until requirements install
    firebase_admin = None
    credentials = None
    messaging = None


class NotificationServiceError(ValueError):
    def __init__(self, message: str, code: str, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}


class NotificationService:
    def list_for_user(self, user_id: str, *, unread_only: bool = False, limit: int = 50):
        user = self._require_profile(user_id)
        queryset = OperationalNotification.objects.filter(recipient=user).order_by("-created_at")
        if unread_only:
            queryset = queryset.filter(is_read=False)
        items = list(queryset[: max(1, min(limit, 200))])
        unread_count = OperationalNotification.objects.filter(recipient=user, is_read=False).count()
        return {
            "items": [self._serialize_notification(item) for item in items],
            "summary": {
                "total_count": queryset.count() if unread_only else OperationalNotification.objects.filter(recipient=user).count(),
                "unread_count": unread_count,
            },
        }

    @transaction.atomic
    def mark_as_read(self, user_id: str, notification_id: str):
        user = self._require_profile(user_id)
        notification = OperationalNotification.objects.select_for_update().filter(id=str(notification_id), recipient=user).first()
        if not notification:
            raise NotificationServiceError("Notificacion no encontrada.", "notification_not_found")
        if not notification.is_read:
            notification.is_read = True
            notification.read_at = timezone.now()
            notification.save(update_fields=["is_read", "read_at", "updated_at"])
        return {
            "notification": self._serialize_notification(notification),
            "summary": self._summary_payload(user),
        }

    @transaction.atomic
    def mark_all_as_read(self, user_id: str):
        user = self._require_profile(user_id)
        now = timezone.now()
        updated = (
            OperationalNotification.objects.filter(recipient=user, is_read=False)
            .update(is_read=True, read_at=now, updated_at=now)
        )
        return {
            "updated_count": updated,
            "summary": self._summary_payload(user),
        }

    @transaction.atomic
    def register_device_token(self, user_id: str, payload: dict):
        user = self._require_profile(user_id)
        token_value = str(payload.get("token") or "").strip()
        if not token_value:
            raise NotificationServiceError("El token FCM es obligatorio.", "token_required")
        defaults = {
            "user": user,
            "platform": payload["platform"],
            "device_id": str(payload.get("device_id") or "").strip(),
            "device_label": str(payload.get("device_label") or "").strip(),
            "is_active": True,
            "metadata": payload.get("metadata") or {},
        }
        token, _ = NotificationDeviceToken.objects.update_or_create(
            token=token_value,
            defaults=defaults,
        )
        return {
            "device": self._serialize_device_token(token),
            "summary": {
                "active_devices": NotificationDeviceToken.objects.filter(user=user, is_active=True).count(),
            },
        }

    @transaction.atomic
    def deactivate_device_token(self, user_id: str, token_value: str):
        user = self._require_profile(user_id)
        token = NotificationDeviceToken.objects.filter(user=user, token=str(token_value).strip()).first()
        if not token:
            raise NotificationServiceError("Token de dispositivo no encontrado.", "device_token_not_found")
        token.is_active = False
        token.save(update_fields=["is_active", "updated_at", "last_seen_at"])
        return {
            "device": self._serialize_device_token(token),
            "summary": {
                "active_devices": NotificationDeviceToken.objects.filter(user=user, is_active=True).count(),
            },
        }

    def notify_order_created(self, order: Order):
        self._notify_many(
            [
                self._build_notification(
                    recipient=order.client,
                    category=OperationalNotification.Category.ORDER,
                    event_code="ORDER_CREATED",
                    title="Pedido creado",
                    message=f"Tu pedido {order.id} fue registrado y ya entro al flujo operativo.",
                    order=order,
                ),
                self._build_notification(
                    recipient=order.chef,
                    category=OperationalNotification.Category.ORDER,
                    event_code="ORDER_CREATED",
                    title="Nuevo pedido recibido",
                    message=f"Tienes un nuevo pedido {order.id} pendiente de gestion.",
                    order=order,
                ),
            ]
        )

    def notify_payment_confirmed(self, order: Order, payment: OrderPayment):
        method_label = self._payment_method_label(payment.method)
        self._notify_many(
            [
                self._build_notification(
                    recipient=order.client,
                    category=OperationalNotification.Category.PAYMENT,
                    event_code="PAYMENT_CONFIRMED",
                    title="Pago confirmado",
                    message=f"El pago de tu pedido {order.id} por {method_label} fue confirmado.",
                    order=order,
                    metadata={"payment_id": payment.id, "payment_method": payment.method},
                ),
                self._build_notification(
                    recipient=order.chef,
                    category=OperationalNotification.Category.PAYMENT,
                    event_code="PAYMENT_CONFIRMED",
                    title="Pago confirmado",
                    message=f"El pedido {order.id} ya tiene pago confirmado y puede seguir su flujo.",
                    order=order,
                    metadata={"payment_id": payment.id, "payment_method": payment.method},
                ),
            ]
        )

    def notify_order_accepted(self, order: Order):
        self._notify_many(
            [
                self._build_notification(
                    recipient=order.client,
                    category=OperationalNotification.Category.ORDER,
                    event_code="ORDER_ACCEPTED",
                    title="Pedido aceptado",
                    message=f"El cocinero acepto tu pedido {order.id}.",
                    order=order,
                ),
                self._build_notification(
                    recipient=order.chef,
                    category=OperationalNotification.Category.ORDER,
                    event_code="ORDER_ACCEPTED",
                    title="Pedido aceptado",
                    message=f"Marcaste el pedido {order.id} como aceptado.",
                    order=order,
                ),
            ]
        )

    def notify_order_ready(self, order: Order):
        mode_label = "delivery" if order.fulfillment_type == Order.FulfillmentType.DELIVERY else "retiro"
        self._notify_many(
            [
                self._build_notification(
                    recipient=order.client,
                    category=OperationalNotification.Category.ORDER,
                    event_code="ORDER_READY",
                    title="Pedido listo",
                    message=f"Tu pedido {order.id} ya esta listo para {mode_label}.",
                    order=order,
                ),
                self._build_notification(
                    recipient=order.chef,
                    category=OperationalNotification.Category.ORDER,
                    event_code="ORDER_READY",
                    title="Pedido listo",
                    message=f"El pedido {order.id} fue marcado como listo para {mode_label}.",
                    order=order,
                ),
            ]
        )

    def notify_pickup_no_show(self, order: Order, *, retention_deadline=None, actor_role: str = "SISTEMA"):
        retention_label = (
            timezone.localtime(retention_deadline).strftime("%d/%m %H:%M")
            if retention_deadline
            else "la politica operativa vigente"
        )
        self._notify_many(
            [
                self._build_notification(
                    recipient=order.client,
                    category=OperationalNotification.Category.ORDER,
                    event_code="PICKUP_NO_SHOW",
                    title="Retiro en retencion",
                    message=(
                        f"Tu pedido {order.id} fue marcado como no presentado y queda retenido hasta {retention_label}."
                    ),
                    order=order,
                    metadata={"actor_role": actor_role},
                ),
                self._build_notification(
                    recipient=order.chef,
                    category=OperationalNotification.Category.ORDER,
                    event_code="PICKUP_NO_SHOW",
                    title="Cliente no se presento",
                    message=(
                        f"El pedido {order.id} quedo en retencion por ausencia del cliente hasta {retention_label}."
                    ),
                    order=order,
                    metadata={"actor_role": actor_role},
                ),
            ]
        )

    def notify_pickup_retention_extended(self, order: Order, *, retention_deadline=None):
        retention_label = (
            timezone.localtime(retention_deadline).strftime("%d/%m %H:%M")
            if retention_deadline
            else "la nueva hora operativa"
        )
        self._notify_many(
            [
                self._build_notification(
                    recipient=order.client,
                    category=OperationalNotification.Category.ORDER,
                    event_code="PICKUP_RETENTION_EXTENDED",
                    title="Retencion extendida",
                    message=f"El pedido {order.id} mantiene su retencion activa hasta {retention_label}.",
                    order=order,
                ),
                self._build_notification(
                    recipient=order.chef,
                    category=OperationalNotification.Category.ORDER,
                    event_code="PICKUP_RETENTION_EXTENDED",
                    title="Retencion extendida",
                    message=f"Extendiste la retencion operativa del pedido {order.id} hasta {retention_label}.",
                    order=order,
                ),
            ]
        )

    def notify_pickup_retention_closed(self, order: Order, *, actor_role: str = "SISTEMA"):
        self._notify_many(
            [
                self._build_notification(
                    recipient=order.client,
                    category=OperationalNotification.Category.ORDER,
                    event_code="PICKUP_RETENTION_CLOSED",
                    title="Pedido cerrado por no retiro",
                    message=f"El pedido {order.id} fue cerrado por no completarse el retiro dentro del tiempo permitido.",
                    order=order,
                    metadata={"actor_role": actor_role},
                ),
                self._build_notification(
                    recipient=order.chef,
                    category=OperationalNotification.Category.ORDER,
                    event_code="PICKUP_RETENTION_CLOSED",
                    title="Retencion cerrada",
                    message=f"El pedido {order.id} fue cerrado por vencimiento de la retencion de retiro.",
                    order=order,
                    metadata={"actor_role": actor_role},
                ),
            ]
        )

    def notify_delivery_assigned(self, assignment: DeliveryAssignment):
        recipients = [
            self._build_notification(
                recipient=assignment.order.client,
                category=OperationalNotification.Category.DELIVERY,
                event_code="DELIVERY_ASSIGNED",
                title="Delivery asignado",
                message=f"Tu pedido {assignment.order.id} ya tiene repartidor asignado.",
                order=assignment.order,
                assignment=assignment,
            ),
            self._build_notification(
                recipient=assignment.order.chef,
                category=OperationalNotification.Category.DELIVERY,
                event_code="DELIVERY_ASSIGNED",
                title="Delivery asignado",
                message=f"El pedido {assignment.order.id} ya cuenta con repartidor asignado.",
                order=assignment.order,
                assignment=assignment,
            ),
        ]
        if assignment.delivery_user:
            recipients.append(
                self._build_notification(
                    recipient=assignment.delivery_user,
                    category=OperationalNotification.Category.DELIVERY,
                    event_code="DELIVERY_ASSIGNED",
                    title="Nueva entrega asignada",
                    message=f"Se te asigno la entrega del pedido {assignment.order.id}.",
                    order=assignment.order,
                    assignment=assignment,
                )
            )
        self._notify_many(recipients)

    def notify_order_picked_up(self, order: Order, assignment: DeliveryAssignment | None = None):
        if assignment:
            title = "Pedido recogido"
            client_message = f"El repartidor recogio tu pedido {order.id} y ya esta en ruta."
            chef_message = f"El repartidor recogio el pedido {order.id}."
            delivery_message = f"Marcaste el pedido {order.id} como recogido."
        else:
            title = "Pedido retirado"
            client_message = f"Tu pedido {order.id} fue retirado correctamente."
            chef_message = f"Confirmaste el retiro del pedido {order.id}."
            delivery_message = ""
        recipients = [
            self._build_notification(
                recipient=order.client,
                category=OperationalNotification.Category.DELIVERY if assignment else OperationalNotification.Category.ORDER,
                event_code="ORDER_PICKED_UP",
                title=title,
                message=client_message,
                order=order,
                assignment=assignment,
            ),
            self._build_notification(
                recipient=order.chef,
                category=OperationalNotification.Category.DELIVERY if assignment else OperationalNotification.Category.ORDER,
                event_code="ORDER_PICKED_UP",
                title=title,
                message=chef_message,
                order=order,
                assignment=assignment,
            ),
        ]
        if assignment and assignment.delivery_user:
            recipients.append(
                self._build_notification(
                    recipient=assignment.delivery_user,
                    category=OperationalNotification.Category.DELIVERY,
                    event_code="ORDER_PICKED_UP",
                    title=title,
                    message=delivery_message,
                    order=order,
                    assignment=assignment,
                )
            )
        self._notify_many(recipients)

    def notify_order_delivered(self, order: Order, assignment: DeliveryAssignment | None = None):
        recipients = [
            self._build_notification(
                recipient=order.client,
                category=OperationalNotification.Category.DELIVERY if assignment else OperationalNotification.Category.ORDER,
                event_code="ORDER_DELIVERED",
                title="Pedido entregado",
                message=f"Tu pedido {order.id} fue entregado correctamente.",
                order=order,
                assignment=assignment,
            ),
            self._build_notification(
                recipient=order.chef,
                category=OperationalNotification.Category.DELIVERY if assignment else OperationalNotification.Category.ORDER,
                event_code="ORDER_DELIVERED",
                title="Pedido entregado",
                message=f"El pedido {order.id} fue cerrado como entregado.",
                order=order,
                assignment=assignment,
            ),
        ]
        if assignment and assignment.delivery_user:
            recipients.append(
                self._build_notification(
                    recipient=assignment.delivery_user,
                    category=OperationalNotification.Category.DELIVERY,
                    event_code="ORDER_DELIVERED",
                    title="Entrega finalizada",
                    message=f"Completaste la entrega del pedido {order.id}.",
                    order=order,
                    assignment=assignment,
                )
            )
        self._notify_many(recipients)

    def notify_incident_created(self, incident: DeliveryIncident):
        assignment = incident.assignment
        recipients = [
            self._build_notification(
                recipient=assignment.order.client,
                category=OperationalNotification.Category.INCIDENT,
                event_code="DELIVERY_INCIDENT_OPENED",
                title="Incidencia en delivery",
                message=f"Se registro la incidencia '{incident.title}' para tu pedido {assignment.order.id}.",
                order=assignment.order,
                assignment=assignment,
                incident=incident,
            ),
            self._build_notification(
                recipient=assignment.order.chef,
                category=OperationalNotification.Category.INCIDENT,
                event_code="DELIVERY_INCIDENT_OPENED",
                title="Incidencia en delivery",
                message=f"Se registro la incidencia '{incident.title}' en el pedido {assignment.order.id}.",
                order=assignment.order,
                assignment=assignment,
                incident=incident,
            ),
        ]
        if assignment.delivery_user:
            recipients.append(
                self._build_notification(
                    recipient=assignment.delivery_user,
                    category=OperationalNotification.Category.INCIDENT,
                    event_code="DELIVERY_INCIDENT_OPENED",
                    title="Incidencia registrada",
                    message=f"Se registro la incidencia '{incident.title}' para la entrega {assignment.id}.",
                    order=assignment.order,
                    assignment=assignment,
                    incident=incident,
                )
            )
        self._notify_many(recipients)

    def notify_low_stock(self, chef_user: UserProfile, item):
        self._notify_many(
            [
                self._build_notification(
                    recipient=chef_user,
                    category=OperationalNotification.Category.INVENTORY,
                    event_code="LOW_STOCK",
                    title="Stock bajo de insumo",
                    message=f"El insumo '{item.name}' tiene un nivel de stock ({item.current_stock} {item.unit_of_measure}) igual o menor al minimo ({item.low_stock_threshold}).",
                    metadata={"item_id": str(item.id)},
                )
            ]
        )

    def notify_expiration(self, chef_user: UserProfile, item, days_left: int):
        self._notify_many(
            [
                self._build_notification(
                    recipient=chef_user,
                    category=OperationalNotification.Category.INVENTORY,
                    event_code="EXPIRATION_WARNING",
                    title="Insumo por caducar",
                    message=f"El insumo '{item.name}' caducara en {days_left} dia(s).",
                    metadata={"item_id": str(item.id)},
                )
            ]
        )

    def _notify_many(self, notifications: Iterable[dict | None]):
        payloads = [row for row in notifications if row and row.get("recipient")]
        for payload in payloads:
            notification = OperationalNotification.objects.create(
                recipient=payload["recipient"],
                role_context=payload["role_context"],
                category=payload["category"],
                event_code=payload["event_code"],
                title=payload["title"],
                message=payload["message"],
                order_ref=payload["order_ref"],
                assignment_ref=payload["assignment_ref"],
                incident_ref=payload["incident_ref"],
                action_label=payload["action_label"],
                action_web_path=payload["action_web_path"],
                action_mobile_route=payload["action_mobile_route"],
                metadata=payload["metadata"],
            )
            self._send_push_if_possible(notification)

    def _build_notification(
        self,
        *,
        recipient: UserProfile | None,
        category: str,
        event_code: str,
        title: str,
        message: str,
        order: Order | None = None,
        assignment: DeliveryAssignment | None = None,
        incident: DeliveryIncident | None = None,
        metadata: dict | None = None,
    ):
        if not recipient:
            return None
        action_web_path, action_mobile_route = self._resolve_action_targets(recipient.role, order, assignment)
        return {
            "recipient": recipient,
            "role_context": recipient.role,
            "category": category,
            "event_code": event_code,
            "title": title,
            "message": message[:255],
            "order_ref": order.id if order else "",
            "assignment_ref": assignment.id if assignment else "",
            "incident_ref": incident.id if incident else "",
            "action_label": "Ver seguimiento" if order else "Ver detalle",
            "action_web_path": action_web_path,
            "action_mobile_route": action_mobile_route,
            "metadata": {
                "role": recipient.role,
                "order_id": order.id if order else "",
                "assignment_id": assignment.id if assignment else "",
                "incident_id": incident.id if incident else "",
                **(metadata or {}),
            },
        }

    def _resolve_action_targets(self, role: str, order: Order | None, assignment: DeliveryAssignment | None):
        if role == UserProfile.ROLE_CLIENT and order:
            return f"/client/orders/{order.id}/tracking", f"/client/orders/tracking?id={order.id}"
        if role == UserProfile.ROLE_CHEF and order:
            return f"/chef/orders/{order.id}/tracking", f"/chef/orders/tracking?id={order.id}"
        if role == UserProfile.ROLE_DELIVERY and assignment:
            return "", f"/delivery/detail?id={assignment.id}"
        if role == UserProfile.ROLE_DELIVERY and order:
            return "", "/delivery/assigned"
        # Para cocineros, si no hay pedido/asignacion asumimos que es una notificacion de inventario u otra generica
        if role == UserProfile.ROLE_CHEF and not order and not assignment:
            return "/chef/inventory", "/chef/inventory"
        return "", ""

    def _send_push_if_possible(self, notification: OperationalNotification):
        if not getattr(notification.recipient, "notify_push", True):
            return
        app = self._get_firebase_app()
        if not app or not messaging:
            return
        tokens = list(
            NotificationDeviceToken.objects.filter(user=notification.recipient, is_active=True).order_by("-last_seen_at")
        )
        token_strings = [t.token for t in tokens]
        
        if getattr(notification.recipient, "fcm_token", None) and notification.recipient.fcm_token not in token_strings:
            token_strings.append(notification.recipient.fcm_token)

        if not token_strings:
            return
        data = {key: str(value) for key, value in (notification.metadata or {}).items()}
        data.update(
            {
                "notification_id": notification.id,
                "event_code": notification.event_code,
                "category": notification.category,
                "order_ref": notification.order_ref,
                "assignment_ref": notification.assignment_ref,
                "incident_ref": notification.incident_ref,
                "action_mobile_route": notification.action_mobile_route,
                "action_web_path": notification.action_web_path,
            }
        )
        web_link = self._resolve_public_web_link(notification.action_web_path)
        for token_string in token_strings:
            try:
                message = messaging.Message(
                    token=token_string,
                    notification=messaging.Notification(
                        title=notification.title,
                        body=notification.message,
                    ),
                    data=data,
                    webpush=messaging.WebpushConfig(
                        fcm_options=messaging.WebpushFCMOptions(link=web_link) if web_link else None,
                    )
                    if token_string in [t.token for t in tokens if getattr(t, 'platform', None) == getattr(NotificationDeviceToken, 'Platform', None) and t.platform == getattr(NotificationDeviceToken.Platform, 'WEB', None)]
                    else None,
                )
                messaging.send(message, app=app)
            except Exception:
                # Si falla, podemos buscar el token en NotificationDeviceToken para desactivarlo
                device_token = next((t for t in tokens if t.token == token_string), None)
                if device_token:
                    device_token.is_active = False
                    device_token.save(update_fields=["is_active", "updated_at", "last_seen_at"])

    def _get_firebase_app(self):
        if not firebase_admin or not credentials:
            return None
        existing_apps = getattr(firebase_admin, "_apps", {})
        if existing_apps:
            return firebase_admin.get_app()

        private_key = os.getenv("FIREBASE_PRIVATE_KEY", "").replace("\\n", "\n")
        project_id = os.getenv("FIREBASE_PROJECT_ID", "")
        client_email = os.getenv("FIREBASE_CLIENT_EMAIL", "")

        if not private_key or not project_id or not client_email:
            return None

        cert = {
            "type": "service_account",
            "project_id": project_id,
            "private_key_id": "",
            "private_key": private_key,
            "client_email": client_email,
            "client_id": "",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_x509_cert_url": f"https://www.googleapis.com/robot/v1/metadata/x509/{client_email.replace('@', '%40')}"
        }

        try:
            return firebase_admin.initialize_app(
                credentials.Certificate(cert),
                options={"projectId": project_id},
            )
        except Exception as e:
            print(f"Firebase init error: {e}")
            return None

    def _resolve_public_web_link(self, action_web_path: str):
        base = str(getattr(settings, "FRONTEND_PUBLIC_BASE_URL", "") or "").strip().rstrip("/")
        path = str(action_web_path or "").strip()
        if not base or not path:
            return None
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return f"{base}{path}"

    def _serialize_notification(self, notification: OperationalNotification):
        return {
            "id": notification.id,
            "event_code": notification.event_code,
            "category": notification.category,
            "title": notification.title,
            "message": notification.message,
            "role_context": notification.role_context,
            "order_ref": notification.order_ref,
            "assignment_ref": notification.assignment_ref,
            "incident_ref": notification.incident_ref,
            "action_label": notification.action_label,
            "action_web_path": notification.action_web_path,
            "action_mobile_route": notification.action_mobile_route,
            "metadata": notification.metadata or {},
            "is_read": notification.is_read,
            "read_at": notification.read_at.isoformat() if notification.read_at else None,
            "created_at": notification.created_at.isoformat(),
        }

    def _serialize_device_token(self, token: NotificationDeviceToken):
        return {
            "id": token.id,
            "platform": token.platform,
            "device_id": token.device_id,
            "device_label": token.device_label,
            "is_active": token.is_active,
            "last_seen_at": token.last_seen_at.isoformat() if token.last_seen_at else None,
        }

    def _summary_payload(self, user: UserProfile):
        total = OperationalNotification.objects.filter(recipient=user).count()
        unread = OperationalNotification.objects.filter(recipient=user, is_read=False).count()
        return {"total_count": total, "unread_count": unread}

    def _require_profile(self, user_id: str):
        parsed = self._parse_uuid(user_id)
        profile = UserProfile.objects.filter(supabase_user_id=parsed).first() if parsed else None
        if not profile:
            raise NotificationServiceError("Perfil de usuario no encontrado.", "user_not_found")
        return profile

    def _parse_uuid(self, value):
        try:
            return UUID(str(value))
        except (TypeError, ValueError):
            return None

    def _payment_method_label(self, method: str):
        labels = {
            Order.PaymentMethod.CASH: "efectivo",
            Order.PaymentMethod.QR_SIMULATED: "QR simulado",
            Order.PaymentMethod.BITCOIN_COINGATE: "Bitcoin CoinGate",
        }
        return labels.get(method, method)

    def _profile_name(self, profile: UserProfile | None):
        if not profile:
            return ""
        if profile.role == UserProfile.ROLE_CHEF:
            chef_profile = ChefProfile.objects.filter(user=profile).first()
            if chef_profile and chef_profile.business_name:
                return chef_profile.business_name
        name = f"{profile.first_name} {profile.last_name}".strip()
        return name or profile.full_name or profile.email

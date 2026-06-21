from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from modules.confianza_administracion_seguridad.models import AuditLog
from modules.confianza_administracion_seguridad.services.audit_service import AuditService
from modules.gestion_cocinero.models import ChefProfile, Dish
from modules.gestion_usuarios_acceso_suscripcion.models import DeliveryProfile, UserProfile
from modules.pedidos_checkout_pagos.models import Order, OrderPayment, OrderStatusHistory


class Command(BaseCommand):
    help = "Backfill idempotente de audit_logs usando datos relacionales existentes."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=50)
        parser.add_argument("--demo", action="store_true", help="Crea eventos demo si no hay suficientes entidades reales.")

    def handle(self, *args, **options):
        limit = max(1, int(options["limit"]))
        allow_demo = bool(options["demo"])
        service = AuditService()
        now = timezone.now()
        created = 0
        admin = UserProfile.objects.filter(role=UserProfile.ROLE_ADMIN).order_by("created_at").first()

        def log_once(seed_key, **payload):
            nonlocal created
            if AuditLog.objects.filter(source="seed_command", metadata__seed_key=seed_key).exists():
                return
            metadata = dict(payload.pop("metadata", {}) or {})
            metadata["seed_key"] = seed_key
            service.log_event(source="seed_command", metadata=metadata, **payload)
            created += 1

        for user in UserProfile.objects.order_by("created_at")[:limit]:
            log_once(
                f"user-registered-{user.pk}",
                event_type="USER_REGISTERED",
                event_category="users",
                action="created",
                entity_type="user",
                entity_id=str(user.pk),
                actor=user,
                target_user_id=str(user.supabase_user_id),
                target_role=user.role,
                description=f"Usuario registrado con rol {user.role}.",
                new_values={"email": user.email, "role": user.role, "is_active": user.is_active},
                severity="info",
                status="success",
                created_at=user.created_at or now,
            )

        for chef in ChefProfile.objects.select_related("user").order_by("created_at")[:limit]:
            log_once(
                f"chef-profile-{chef.pk}",
                event_type="CHEF_REGISTERED",
                event_category="chefs",
                action="created",
                entity_type="chef_profile",
                entity_id=str(chef.pk),
                actor=chef.user,
                target_user_id=str(chef.user.supabase_user_id),
                target_role=UserProfile.ROLE_CHEF,
                description=f"Perfil de cocinero registrado: {chef.business_name or chef.user.email}.",
                new_values={"status": chef.status, "business_name": chef.business_name},
                created_at=chef.created_at or now,
            )

        for rider in DeliveryProfile.objects.select_related("user").order_by("created_at")[:limit]:
            log_once(
                f"rider-profile-{rider.pk}",
                event_type="RIDER_REGISTERED",
                event_category="riders",
                action="created",
                entity_type="rider_profile",
                entity_id=str(rider.pk),
                actor=rider.user,
                target_user_id=str(rider.user.supabase_user_id),
                target_role=UserProfile.ROLE_DELIVERY,
                description=f"Repartidor registrado con vehiculo {rider.vehicle_type}.",
                new_values={"approval_status": rider.approval_status, "vehicle_plate": rider.vehicle_plate},
                created_at=rider.created_at or now,
            )

        for dish in Dish.objects.select_related("chef").order_by("created_at")[:limit]:
            log_once(
                f"dish-created-{dish.pk}",
                event_type="PUBLICATION_CREATED",
                event_category="publications",
                action="created",
                entity_type="publication",
                entity_id=str(dish.pk),
                actor=dish.chef,
                description=f"Publicacion creada: {dish.name}.",
                new_values={"name": dish.name, "price": str(dish.price), "status": dish.status},
                created_at=dish.created_at or now,
            )

        for order in Order.objects.select_related("client", "chef").order_by("created_at")[:limit]:
            log_once(
                f"order-created-{order.pk}",
                event_type="ORDER_CREATED",
                event_category="orders",
                action="created",
                entity_type="order",
                entity_id=str(order.pk),
                actor=order.client,
                target_user_id=str(order.chef.supabase_user_id),
                target_role=UserProfile.ROLE_CHEF,
                description=f"Pedido creado con estado {order.status}.",
                new_values={"status": order.status, "total": str(order.total), "payment_method": order.payment_method},
                created_at=order.created_at or now,
            )

        for history in OrderStatusHistory.objects.order_by("-occurred_at")[:limit]:
            log_once(
                f"order-status-{history.pk}",
                event_type="ORDER_STATUS_CHANGED",
                event_category="orders",
                action=_order_action(history.to_status),
                entity_type="order",
                entity_id=str(history.order_id),
                actor_user_id=history.actor_id,
                actor_role=history.actor_role,
                description=f"Pedido cambio de {history.from_status or 'inicio'} a {history.to_status}.",
                old_values={"status": history.from_status},
                new_values={"status": history.to_status},
                metadata={"notes": history.notes},
                severity="warning" if history.to_status in {"CANCELLED", "PAYMENT_FAILED", "REJECTED"} else "info",
                created_at=history.occurred_at or now,
            )

        for payment in OrderPayment.objects.select_related("order", "order__client").order_by("created_at")[:limit]:
            log_once(
                f"payment-{payment.pk}-{payment.status}",
                event_type=f"PAYMENT_{payment.status}",
                event_category="payments",
                action="failed" if payment.status == OrderPayment.Status.FAILED else "created",
                entity_type="payment",
                entity_id=str(payment.pk),
                actor=payment.order.client,
                description=f"Pago {payment.method} con estado {payment.status}.",
                new_values={"status": payment.status, "amount": str(payment.amount), "provider": payment.provider},
                severity="warning" if payment.status in {OrderPayment.Status.FAILED, OrderPayment.Status.CANCELLED} else "info",
                status="failed" if payment.status == OrderPayment.Status.FAILED else "success",
                created_at=payment.created_at or now,
            )

        if allow_demo and created < 8:
            actor_kwargs = {"actor": admin} if admin else {"actor_role": "SISTEMA", "actor_name": "Sistema HomeChef"}
            for index, event in enumerate(_demo_events()):
                log_once(
                    f"demo-{event['event_type']}",
                    **actor_kwargs,
                    **event,
                    created_at=now - timedelta(hours=index + 1),
                )

        self.stdout.write(self.style.SUCCESS(f"Audit seed completed. Created {created} new event(s)."))


def _order_action(status):
    return {
        "CANCELLED": "cancelled",
        "DELIVERED": "delivered",
        "PICKED_UP": "delivered",
        "REJECTED": "rejected",
        "PAYMENT_FAILED": "failed",
        "OUT_FOR_DELIVERY": "assigned",
    }.get(status, "updated")


def _demo_events():
    return [
        {
            "event_type": "ADMIN_LOGIN",
            "event_category": "security",
            "action": "login",
            "entity_type": "admin_action",
            "entity_id": "demo-admin-login",
            "description": "Administrador inicio sesion en el panel.",
            "severity": "info",
            "status": "success",
        },
        {
            "event_type": "FAILED_LOGIN_ATTEMPT",
            "event_category": "security",
            "action": "failed",
            "entity_type": "user",
            "entity_id": "demo-failed-login",
            "description": "Intento fallido de login detectado.",
            "severity": "warning",
            "status": "failed",
        },
    ]

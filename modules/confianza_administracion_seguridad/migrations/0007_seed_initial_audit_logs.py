from datetime import timedelta

from django.db import migrations
from django.utils import timezone


def seed_audit_logs(apps, schema_editor):
    AuditLog = apps.get_model("confianza_administracion_seguridad", "AuditLog")
    UserProfile = apps.get_model("gestion_usuarios_acceso_suscripcion", "UserProfile")
    ChefProfile = apps.get_model("gestion_cocinero", "ChefProfile")
    DeliveryProfile = apps.get_model("gestion_usuarios_acceso_suscripcion", "DeliveryProfile")
    Dish = apps.get_model("gestion_cocinero", "Dish")
    Order = apps.get_model("pedidos_checkout_pagos", "Order")
    OrderStatusHistory = apps.get_model("pedidos_checkout_pagos", "OrderStatusHistory")
    OrderPayment = apps.get_model("pedidos_checkout_pagos", "OrderPayment")

    now = timezone.now()
    created = 0

    def create_once(seed_key, created_at, **payload):
        nonlocal created
        if AuditLog.objects.filter(source="seed", metadata__seed_key=seed_key).exists():
            return
        metadata = dict(payload.pop("metadata", {}) or {})
        metadata["seed_key"] = seed_key
        item = AuditLog.objects.create(source="seed", metadata=metadata, **payload)
        AuditLog.objects.filter(pk=item.pk).update(created_at=created_at)
        created += 1

    admin = UserProfile.objects.filter(role="ADMINISTRADOR").order_by("created_at").first()
    admin_actor = _actor(admin)

    for index, user in enumerate(UserProfile.objects.order_by("created_at")[:40]):
        create_once(
            f"user-registered-{user.pk}",
            getattr(user, "created_at", None) or now - timedelta(days=20, hours=index),
            event_type="USER_REGISTERED",
            event_category="users",
            action="created",
            entity_type="user",
            entity_id=str(user.pk),
            actor_user_id=str(getattr(user, "supabase_user_id", "") or user.pk),
            actor_role=getattr(user, "role", ""),
            actor_name=_name(user),
            target_user_id=str(getattr(user, "supabase_user_id", "") or user.pk),
            target_role=getattr(user, "role", ""),
            description=f"Usuario registrado con rol {getattr(user, 'role', '')}.",
            new_values={"email": getattr(user, "email", ""), "role": getattr(user, "role", ""), "is_active": getattr(user, "is_active", True)},
            severity="info",
            status="success",
        )
        if index < 12:
            create_once(
                f"user-login-{user.pk}",
                now - timedelta(days=index % 7, hours=index + 1),
                event_type="USER_LOGIN",
                event_category="security",
                action="login",
                entity_type="user",
                entity_id=str(user.pk),
                actor_user_id=str(getattr(user, "supabase_user_id", "") or user.pk),
                actor_role=getattr(user, "role", ""),
                actor_name=_name(user),
                description="Inicio de sesion correcto.",
                metadata={"auth_provider": "supabase"},
                severity="info",
                status="success",
            )

    for chef in ChefProfile.objects.select_related("user").order_by("created_at")[:30]:
        create_once(
            f"chef-profile-{chef.pk}",
            getattr(chef, "created_at", None) or now - timedelta(days=15),
            event_type="CHEF_REGISTERED",
            event_category="chefs",
            action="created",
            entity_type="chef_profile",
            entity_id=str(chef.pk),
            actor_user_id=str(getattr(chef.user, "supabase_user_id", "") or chef.user.pk),
            actor_role=getattr(chef.user, "role", "COCINERO"),
            actor_name=_name(chef.user),
            target_user_id=str(getattr(chef.user, "supabase_user_id", "") or chef.user.pk),
            target_role="COCINERO",
            description=f"Perfil de cocinero registrado: {getattr(chef, 'business_name', '') or _name(chef.user)}.",
            new_values={"status": getattr(chef, "status", ""), "business_name": getattr(chef, "business_name", "")},
            severity="info",
            status="success",
        )
        if getattr(chef, "status", "") in {"approved", "rejected"}:
            create_once(
                f"chef-validation-{chef.pk}-{chef.status}",
                getattr(chef, "updated_at", None) or now - timedelta(days=7),
                event_type="CHEF_APPROVED" if chef.status == "approved" else "CHEF_REJECTED",
                event_category="chefs",
                action="approved" if chef.status == "approved" else "rejected",
                entity_type="chef_profile",
                entity_id=str(chef.pk),
                **admin_actor,
                target_user_id=str(getattr(chef.user, "supabase_user_id", "") or chef.user.pk),
                target_role="COCINERO",
                description=f"Validacion administrativa del cocinero: {chef.status}.",
                old_values={"status": "pending_validation"},
                new_values={"status": chef.status},
                severity="info",
                status="success",
            )

    for rider in DeliveryProfile.objects.select_related("user").order_by("created_at")[:30]:
        create_once(
            f"rider-profile-{rider.pk}",
            getattr(rider, "created_at", None) or now - timedelta(days=12),
            event_type="RIDER_REGISTERED",
            event_category="riders",
            action="created",
            entity_type="rider_profile",
            entity_id=str(rider.pk),
            actor_user_id=str(getattr(rider.user, "supabase_user_id", "") or rider.user.pk),
            actor_role=getattr(rider.user, "role", "REPARTIDOR"),
            actor_name=_name(rider.user),
            target_user_id=str(getattr(rider.user, "supabase_user_id", "") or rider.user.pk),
            target_role="REPARTIDOR",
            description=f"Repartidor registrado con vehiculo {getattr(rider, 'vehicle_type', '')}.",
            new_values={"approval_status": getattr(rider, "approval_status", ""), "vehicle_plate": getattr(rider, "vehicle_plate", "")},
            severity="info",
            status="success",
        )

    for dish in Dish.objects.select_related("chef").order_by("created_at")[:40]:
        create_once(
            f"dish-created-{dish.pk}",
            getattr(dish, "created_at", None) or now - timedelta(days=10),
            event_type="PUBLICATION_CREATED",
            event_category="publications",
            action="created",
            entity_type="publication",
            entity_id=str(dish.pk),
            actor_user_id=str(getattr(dish.chef, "supabase_user_id", "") or dish.chef.pk),
            actor_role=getattr(dish.chef, "role", "COCINERO"),
            actor_name=_name(dish.chef),
            description=f"Publicacion creada: {getattr(dish, 'name', '')}.",
            new_values={"name": getattr(dish, "name", ""), "price": str(getattr(dish, "price", "")), "status": getattr(dish, "status", "")},
            severity="info",
            status="success",
        )
        if getattr(dish, "revision_status", "") in {"rechazada", "oculta_temporalmente", "requiere_correccion", "aprobada"}:
            action = {
                "rechazada": "rejected",
                "oculta_temporalmente": "blocked",
                "requiere_correccion": "updated",
                "aprobada": "approved",
            }.get(dish.revision_status, "updated")
            create_once(
                f"dish-admin-review-{dish.pk}-{dish.revision_status}",
                getattr(dish, "admin_reviewed_at", None) or getattr(dish, "updated_at", None) or now - timedelta(days=3),
                event_type="PUBLICATION_ADMIN_REVIEWED",
                event_category="publications",
                action=action,
                entity_type="publication",
                entity_id=str(dish.pk),
                **admin_actor,
                target_user_id=str(getattr(dish.chef, "supabase_user_id", "") or dish.chef.pk),
                target_role="COCINERO",
                description=f"Revision administrativa de publicacion: {dish.revision_status}.",
                old_values={"revision_status": "pendiente_revision_ia"},
                new_values={"revision_status": dish.revision_status, "status": getattr(dish, "status", "")},
                severity="warning" if action in {"rejected", "blocked"} else "info",
                status="success",
            )

    for order in Order.objects.select_related("client", "chef").order_by("created_at")[:40]:
        create_once(
            f"order-created-{order.pk}",
            getattr(order, "created_at", None) or now - timedelta(days=5),
            event_type="ORDER_CREATED",
            event_category="orders",
            action="created",
            entity_type="order",
            entity_id=str(order.pk),
            actor_user_id=str(getattr(order.client, "supabase_user_id", "") or order.client.pk),
            actor_role="CLIENTE",
            actor_name=_name(order.client),
            target_user_id=str(getattr(order.chef, "supabase_user_id", "") or order.chef.pk),
            target_role="COCINERO",
            description=f"Pedido creado con estado {getattr(order, 'status', '')}.",
            new_values={"status": getattr(order, "status", ""), "total": str(getattr(order, "total", "")), "payment_method": getattr(order, "payment_method", "")},
            severity="info",
            status="success",
        )

    for history in OrderStatusHistory.objects.select_related("order").order_by("-occurred_at")[:60]:
        create_once(
            f"order-status-{history.pk}",
            getattr(history, "occurred_at", None) or now - timedelta(days=2),
            event_type="ORDER_STATUS_CHANGED",
            event_category="orders",
            action=_action_for_order_status(getattr(history, "to_status", "")),
            entity_type="order",
            entity_id=str(history.order_id),
            actor_user_id=str(getattr(history, "actor_id", "") or ""),
            actor_role=getattr(history, "actor_role", ""),
            description=f"Pedido cambio de {getattr(history, 'from_status', '') or 'inicio'} a {getattr(history, 'to_status', '')}.",
            old_values={"status": getattr(history, "from_status", "")},
            new_values={"status": getattr(history, "to_status", "")},
            metadata={"notes": getattr(history, "notes", "")},
            severity="warning" if getattr(history, "to_status", "") in {"CANCELLED", "PAYMENT_FAILED", "REJECTED"} else "info",
            status="success",
        )

    for payment in OrderPayment.objects.select_related("order").order_by("created_at")[:40]:
        create_once(
            f"payment-{payment.pk}-{payment.status}",
            getattr(payment, "created_at", None) or now - timedelta(days=4),
            event_type=f"PAYMENT_{getattr(payment, 'status', 'CREATED')}",
            event_category="payments",
            action=_action_for_payment_status(getattr(payment, "status", "")),
            entity_type="payment",
            entity_id=str(payment.pk),
            actor_user_id=str(getattr(payment.order.client, "supabase_user_id", "") or payment.order.client.pk),
            actor_role="CLIENTE",
            actor_name=_name(payment.order.client),
            description=f"Pago {getattr(payment, 'method', '')} con estado {getattr(payment, 'status', '')}.",
            new_values={"status": getattr(payment, "status", ""), "amount": str(getattr(payment, "amount", "")), "provider": getattr(payment, "provider", "")},
            severity="warning" if getattr(payment, "status", "") in {"FAILED", "CANCELLED"} else "info",
            status="failed" if getattr(payment, "status", "") == "FAILED" else "success",
        )

    demo_needed = created < 8
    if demo_needed:
        demo_events = [
            ("ADMIN_LOGIN", "security", "login", "admin_action", "demo-admin-login", "Administrador inicio sesion en el panel."),
            ("REPORT_EXPORTED", "admin", "exported", "admin_action", "demo-report-export", "Exportacion de reporte operativo."),
            ("FAILED_LOGIN_ATTEMPT", "security", "failed", "user", "demo-failed-login", "Intento fallido de login detectado."),
            ("NOTIFICATION_SENT", "notifications", "created", "notification", "demo-notification", "Notificacion push enviada a usuario."),
            ("SYSTEM_INTEGRATION_FAILURE", "system", "failed", "system", "demo-integration", "Fallo controlado en integracion externa."),
        ]
        for index, (event_type, category, action, entity_type, entity_id, description) in enumerate(demo_events):
            create_once(
                f"demo-{event_type}",
                now - timedelta(hours=index + 1),
                event_type=event_type,
                event_category=category,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                **admin_actor,
                description=description,
                metadata={"demo": True},
                severity="warning" if action == "failed" else "info",
                status="failed" if action == "failed" else "success",
            )


def _actor(user):
    if not user:
        return {"actor_user_id": "", "actor_role": "SISTEMA", "actor_name": "Sistema HomeChef"}
    return {
        "actor_user_id": str(getattr(user, "supabase_user_id", "") or user.pk),
        "actor_role": getattr(user, "role", ""),
        "actor_name": _name(user),
    }


def _name(user):
    if not user:
        return ""
    return (getattr(user, "full_name", "") or f"{getattr(user, 'first_name', '')} {getattr(user, 'last_name', '')}".strip() or getattr(user, "email", "") or "").strip()


def _action_for_order_status(status):
    mapping = {
        "CANCELLED": "cancelled",
        "DELIVERED": "delivered",
        "PICKED_UP": "delivered",
        "REJECTED": "rejected",
        "PAYMENT_FAILED": "failed",
        "OUT_FOR_DELIVERY": "assigned",
    }
    return mapping.get(status, "updated")


def _action_for_payment_status(status):
    mapping = {
        "CONFIRMED": "approved",
        "FAILED": "failed",
        "CANCELLED": "cancelled",
        "EXPIRED": "failed",
    }
    return mapping.get(status, "created")


class Migration(migrations.Migration):

    dependencies = [
        ("confianza_administracion_seguridad", "0006_audit_logs"),
        ("gestion_usuarios_acceso_suscripcion", "0009_merge_20260617_0156"),
        ("gestion_cocinero", "0007_merge_20260619_1840"),
        ("pedidos_checkout_pagos", "0009_pickup_retention_extensions"),
    ]

    operations = [
        migrations.RunPython(seed_audit_logs, migrations.RunPython.noop),
    ]

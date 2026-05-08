from datetime import timedelta

from django.db.models import Case, IntegerField, Value, When
from django.db import transaction
from django.utils import timezone

from modules.gestion_cocinero.models import ChefProfile

from ..exceptions import ChefAccessDenied, PlanUnavailable, SubscriptionNotFound
from ..models import AISubscriptionAuditLog, AISubscriptionPayment, AISubscriptionPlan, ChefAISubscription
from .audit_service import AISubscriptionAuditService
from .payment_service import PaymentService


DEFAULT_AI_PLANS = (
    {
        "name": "Plan Básico IA",
        "description": "Asistente textual para recibir ideas de platos usando ingredientes disponibles.",
        "price": "19.90",
        "ai_query_limit": 100,
        "ai_generation_limit": 50,
        "vision_enabled": False,
        "production_recommendations_enabled": False,
        "pricing_support_enabled": False,
        "publishing_support_enabled": False,
        "benefits": [
            "Asistente IA textual",
            "Ideas de platos",
            "Sustituciones de ingredientes",
            "Aprovechamiento de insumos",
        ],
    },
    {
        "name": "Plan Visión IA",
        "description": "Reconocimiento de ingredientes mediante fotos y sugerencias automáticas de platos.",
        "price": "29.90",
        "ai_query_limit": 150,
        "ai_generation_limit": 75,
        "vision_enabled": True,
        "production_recommendations_enabled": False,
        "pricing_support_enabled": False,
        "publishing_support_enabled": False,
        "benefits": [
            "Análisis por imagen",
            "Reconocimiento de ingredientes",
            "Detección de insumos",
            "Sugerencia de platos por foto",
        ],
    },
    {
        "name": "Plan Producción y Precios IA",
        "description": "Recomendaciones de demanda, cantidad a producir, precio sugerido y descuentos dinámicos.",
        "price": "34.90",
        "ai_query_limit": 200,
        "ai_generation_limit": 100,
        "vision_enabled": False,
        "production_recommendations_enabled": True,
        "pricing_support_enabled": True,
        "publishing_support_enabled": False,
        "benefits": [
            "Demanda estimada",
            "Cantidad recomendada",
            "Precio sugerido",
            "Descuentos dinámicos",
        ],
    },
    {
        "name": "Plan Publicación Inteligente IA",
        "description": "Generación automática de títulos, descripciones, etiquetas, categorías y precio sugerido.",
        "price": "24.90",
        "ai_query_limit": 150,
        "ai_generation_limit": 100,
        "vision_enabled": False,
        "production_recommendations_enabled": False,
        "pricing_support_enabled": True,
        "publishing_support_enabled": True,
        "benefits": [
            "Generar título",
            "Generar descripción",
            "Generar etiquetas",
            "Generar categorías",
            "Precio sugerido",
        ],
    },
    {
        "name": "Plan Chef Pro IA",
        "description": "Acceso completo a todas las funciones inteligentes para cocineros.",
        "price": "59.90",
        "ai_query_limit": 500,
        "ai_generation_limit": 300,
        "vision_enabled": True,
        "production_recommendations_enabled": True,
        "pricing_support_enabled": True,
        "publishing_support_enabled": True,
        "benefits": [
            "Asistente IA textual",
            "Visión artificial",
            "Recomendación de platos",
            "Demanda estimada",
            "Producción recomendada",
            "Precio inteligente",
            "Descuentos dinámicos",
            "Publicaciones automáticas",
        ],
    },
)


class AISubscriptionService:
    def __init__(self):
        self.audit = AISubscriptionAuditService()
        self.payments = PaymentService()

    def get_chef_profile(self, user):
        profile = getattr(user, "profile", None)
        if not profile or not getattr(profile, "is_active", False) or profile.role != "COCINERO":
            raise ChefAccessDenied("Solo cocineros activos pueden gestionar suscripcion IA")
        chef_profile = ChefProfile.objects.filter(user=profile).first()
        if not chef_profile:
            raise ChefAccessDenied("El perfil de cocinero no existe")
        return chef_profile

    def list_available_plans(self, chef_profile, request=None):
        self._sync_default_plans()
        self.audit.log(
            chef_profile=chef_profile,
            action=AISubscriptionAuditLog.Action.PLAN_VIEWED,
            description="Planes IA disponibles consultados",
            request=request,
        )
        catalog_order = Case(
            *[
                When(name=plan["name"], then=Value(index))
                for index, plan in enumerate(DEFAULT_AI_PLANS)
            ],
            output_field=IntegerField(),
        )
        return (
            AISubscriptionPlan.objects.filter(status=AISubscriptionPlan.Status.AVAILABLE)
            .annotate(catalog_order=catalog_order)
            .order_by("catalog_order", "id")
        )

    def _sync_default_plans(self):
        catalog_names = [plan["name"] for plan in DEFAULT_AI_PLANS]
        AISubscriptionPlan.objects.exclude(name__in=catalog_names).filter(
            status=AISubscriptionPlan.Status.AVAILABLE
        ).update(status=AISubscriptionPlan.Status.UNAVAILABLE)

        for plan in DEFAULT_AI_PLANS:
            AISubscriptionPlan.objects.update_or_create(
                name=plan["name"],
                defaults={
                    **plan,
                    "currency": "BOB",
                    "duration_days": 30,
                    "status": AISubscriptionPlan.Status.AVAILABLE,
                },
            )

    def get_status(self, chef_profile, request=None):
        subscription = self._current_subscription(chef_profile)
        active = self._is_active(subscription)
        self._sync_chef_cache(chef_profile, active)
        self.audit.log(
            chef_profile=chef_profile,
            subscription=subscription,
            action=AISubscriptionAuditLog.Action.SUBSCRIPTION_STATUS_VIEWED,
            description="Estado de suscripcion IA consultado",
            request=request,
        )
        return {"subscription": subscription, "can_use_ai": active, "limits": self._limits(subscription) if active else {}}

    def build_summary(self, chef_profile, *, plan_id, operation, request=None):
        plan = self._get_available_plan(plan_id, chef_profile, request)
        current = self._current_subscription(chef_profile)
        start = timezone.now()
        end = start + timedelta(days=plan.duration_days)
        self.audit.log(
            chef_profile=chef_profile,
            subscription=current,
            action=AISubscriptionAuditLog.Action.SUBSCRIPTION_SUMMARY_GENERATED,
            description="Resumen de suscripcion IA generado",
            metadata={"plan_id": plan.id, "operation": operation},
            request=request,
        )
        return {
            "operation": operation,
            "plan": plan,
            "current_subscription": current,
            "start_date": start,
            "end_date": end,
            "amount": plan.price,
            "currency": plan.currency,
            "conditions": {
                "duration_days": plan.duration_days,
                "auto_renew": True,
                "cancel_policy": "Puede cancelarse inmediatamente o al final del periodo.",
            },
        }

    @transaction.atomic
    def subscribe(self, chef_profile, *, plan_id, payment_provider, payment_method_id=None, payload=None, request=None):
        plan = self._get_available_plan(plan_id, chef_profile, request)
        if self._current_subscription_for_update(chef_profile):
            raise PlanUnavailable("Ya existe una suscripcion IA activa; use cambio de plan")

        subscription = ChefAISubscription.objects.create(
            chef_profile=chef_profile,
            plan=plan,
            status=ChefAISubscription.Status.PENDING_PAYMENT,
            preferred_payment_provider=payment_provider,
        )
        self.audit.log(
            chef_profile=chef_profile,
            subscription=subscription,
            action=AISubscriptionAuditLog.Action.SUBSCRIPTION_CREATED,
            description="Suscripcion IA creada pendiente de pago",
            request=request,
        )
        return self._process_and_apply_payment(subscription, plan, payment_provider, payment_method_id, payload, request)

    @transaction.atomic
    def change_plan(self, chef_profile, *, new_plan_id, payment_provider, payment_method_id=None, payload=None, request=None):
        current = self._current_subscription_for_update(chef_profile)
        if not self._is_active(current):
            raise SubscriptionNotFound("No existe suscripcion activa para cambiar de plan")
        plan = self._get_available_plan(new_plan_id, chef_profile, request)
        new_subscription = ChefAISubscription.objects.create(
            chef_profile=chef_profile,
            plan=plan,
            status=ChefAISubscription.Status.PENDING_PAYMENT,
            preferred_payment_provider=payment_provider,
        )
        result = self._process_and_apply_payment(
            new_subscription,
            plan,
            payment_provider,
            payment_method_id,
            payload,
            request,
            replace_subscription=current,
        )
        if result.get("payment_error"):
            return result
        self.audit.log(
            chef_profile=chef_profile,
            subscription=new_subscription,
            action=AISubscriptionAuditLog.Action.PLAN_CHANGED,
            description="Plan IA cambiado correctamente",
            metadata={"previous_subscription_id": current.id},
            request=request,
        )
        return result

    @transaction.atomic
    def renew(self, chef_profile, *, payment_provider, payment_method_id=None, payload=None, request=None):
        subscription = self._current_subscription_for_update(chef_profile, include_expired=True)
        if not subscription:
            raise SubscriptionNotFound("No existe suscripcion IA para renovar")
        self.audit.log(
            chef_profile=chef_profile,
            subscription=subscription,
            action=AISubscriptionAuditLog.Action.RENEWAL_REQUESTED,
            description="Renovacion de suscripcion IA solicitada",
            request=request,
        )
        return self._process_and_apply_payment(
            subscription,
            subscription.plan,
            payment_provider,
            payment_method_id,
            payload,
            request,
            renewal=True,
        )

    @transaction.atomic
    def cancel(self, chef_profile, *, cancel_at_period_end=True, reason="", request=None):
        subscription = self._current_subscription_for_update(chef_profile)
        if not self._is_active(subscription):
            raise SubscriptionNotFound("No existe suscripcion activa para cancelar")
        now = timezone.now()
        subscription.cancel_at_period_end = cancel_at_period_end
        subscription.cancellation_requested_at = now
        if not cancel_at_period_end:
            subscription.status = ChefAISubscription.Status.CANCELLED
            subscription.cancelled_at = now
            self._sync_chef_cache(chef_profile, False)
        subscription.save()
        self.audit.log(
            chef_profile=chef_profile,
            subscription=subscription,
            action=AISubscriptionAuditLog.Action.CANCELLATION_REQUESTED,
            description="Cancelacion de suscripcion IA solicitada",
            metadata={"reason": reason, "cancel_at_period_end": cancel_at_period_end},
            request=request,
        )
        if not cancel_at_period_end:
            self.audit.log(
                chef_profile=chef_profile,
                subscription=subscription,
                action=AISubscriptionAuditLog.Action.CANCELLATION_CONFIRMED,
                description="Suscripcion IA cancelada",
                request=request,
            )
        return subscription

    def list_payments(self, chef_profile):
        return AISubscriptionPayment.objects.filter(chef_profile=chef_profile).select_related("plan").order_by("-created_at")

    def list_audit_logs(self, chef_profile):
        return AISubscriptionAuditLog.objects.filter(chef_profile=chef_profile).order_by("-created_at")

    def can_use_ai(self, chef_profile, request=None):
        subscription = self._current_subscription(chef_profile)
        allowed = self._is_active(subscription)
        self._sync_chef_cache(chef_profile, allowed)
        self.audit.log(
            chef_profile=chef_profile,
            subscription=subscription,
            action=(
                AISubscriptionAuditLog.Action.AI_ACCESS_VALIDATED
                if allowed
                else AISubscriptionAuditLog.Action.AI_ACCESS_DENIED
            ),
            description="Validacion de acceso a funciones IA",
            request=request,
        )
        return {"can_use_ai": allowed, "subscription": subscription, "limits": self._limits(subscription) if allowed else {}}

    def _process_and_apply_payment(
        self,
        subscription,
        plan,
        provider,
        payment_method_id,
        payload,
        request,
        renewal=False,
        replace_subscription=None,
    ):
        payment = self.payments.create_checkout(
            chef_profile=subscription.chef_profile,
            plan=plan,
            subscription=subscription,
            provider=provider,
            payment_method_id=payment_method_id,
            payload=payload,
        )
        if payment.status == AISubscriptionPayment.Status.PENDING:
            metadata = {"payment_id": payment.id, "provider": payment.provider}
            if replace_subscription:
                metadata["replace_subscription_id"] = replace_subscription.id
                payment.provider_response = {**payment.provider_response, "replace_subscription_id": replace_subscription.id}
                payment.save(update_fields=["provider_response"])
            if renewal:
                metadata["renewal"] = True
                payment.provider_response = {**payment.provider_response, "renewal": True}
                payment.save(update_fields=["provider_response"])
            self.audit.log(
                chef_profile=subscription.chef_profile,
                subscription=subscription,
                action=AISubscriptionAuditLog.Action.SUBSCRIPTION_CREATED,
                description="Checkout/orden de pago IA creada",
                metadata=metadata,
                request=request,
            )
            return {"subscription": subscription, "payment": payment}

        if payment.status == AISubscriptionPayment.Status.ERROR:
            self.audit.log(
                chef_profile=subscription.chef_profile,
                subscription=subscription,
                action=AISubscriptionAuditLog.Action.PAYMENT_REJECTED,
                description="No se pudo crear checkout/orden de pago IA",
                metadata={"payment_id": payment.id, "provider": payment.provider},
                request=request,
            )
            return {"subscription": subscription, "payment": payment, "payment_error": "PAYMENT_ERROR"}

        return {"subscription": subscription, "payment": payment, "payment_error": "PAYMENT_ERROR"}

    def _get_available_plan(self, plan_id, chef_profile=None, request=None):
        plan = AISubscriptionPlan.objects.filter(id=plan_id).first()
        if not plan or plan.status != AISubscriptionPlan.Status.AVAILABLE:
            if chef_profile:
                self.audit.log(
                    chef_profile=chef_profile,
                    action=AISubscriptionAuditLog.Action.PLAN_VIEWED,
                    description="Intento de usar plan IA no disponible",
                    metadata={"plan_id": plan_id},
                    request=request,
                )
            raise PlanUnavailable("Plan IA no disponible")
        return plan

    def _current_subscription(self, chef_profile):
        subscription = (
            ChefAISubscription.objects.filter(chef_profile=chef_profile)
            .select_related("plan")
            .order_by("-created_at")
            .first()
        )
        if (
            subscription
            and subscription.status == ChefAISubscription.Status.ACTIVE
            and subscription.end_date
            and subscription.end_date <= timezone.now()
        ):
            subscription.status = ChefAISubscription.Status.EXPIRED
            subscription.save(update_fields=["status", "updated_at"])
            self._sync_chef_cache(chef_profile, False)
        return subscription

    def _current_subscription_for_update(self, chef_profile, include_expired=False):
        statuses = [ChefAISubscription.Status.ACTIVE]
        if include_expired:
            statuses.extend([ChefAISubscription.Status.EXPIRED, ChefAISubscription.Status.SUSPENDED])
        return (
            ChefAISubscription.objects.select_for_update()
            .filter(chef_profile=chef_profile, status__in=statuses)
            .select_related("plan")
            .order_by("-created_at")
            .first()
        )

    def _is_active(self, subscription):
        return bool(
            subscription
            and subscription.status == ChefAISubscription.Status.ACTIVE
            and subscription.end_date
            and subscription.end_date > timezone.now()
        )

    def _sync_chef_cache(self, chef_profile, active):
        if chef_profile.ai_subscription_active != active:
            chef_profile.ai_subscription_active = active
            chef_profile.save(update_fields=["ai_subscription_active", "updated_at"])

    def _limits(self, subscription):
        plan = subscription.plan
        return {
            "ai_query_limit": plan.ai_query_limit,
            "ai_generation_limit": plan.ai_generation_limit,
            "vision_enabled": plan.vision_enabled,
            "production_recommendations_enabled": plan.production_recommendations_enabled,
            "pricing_support_enabled": plan.pricing_support_enabled,
            "publishing_support_enabled": plan.publishing_support_enabled,
        }

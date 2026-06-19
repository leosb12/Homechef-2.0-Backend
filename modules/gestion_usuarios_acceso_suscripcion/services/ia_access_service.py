from django.utils import timezone

from modules.gestion_cocinero.models import ChefProfile
from modules.gestion_usuarios_acceso_suscripcion.models import (
    AISubscriptionPlan,
    ChefAISubscription,
    UserProfile,
    UsoIA,
)


FUNCIONES_IA = {
    "asistente_ia": {
        "codigo": "asistente_ia",
        "nombre": "Asistente IA",
        "habilitada": True,
        "implementada": True,
        "plan_flag": None,
    },
    "vision_artificial": {
        "codigo": "vision_artificial",
        "nombre": "Vision artificial",
        "habilitada": True,
        "implementada": True,
        "plan_flag": "vision_enabled",
    },
    "demanda_precios": {
        "codigo": "demanda_precios",
        "nombre": "Demanda y precios",
        "habilitada": True,
        "implementada": True,
        "plan_flag": "production_recommendations_enabled",
    },
    "publicacion_platos": {
        "codigo": "publicacion_platos",
        "nombre": "Publicacion de platos",
        "habilitada": True,
        "implementada": True,
        "plan_flag": "publishing_support_enabled",
    },
    "funcion_no_implementada": {
        "codigo": "funcion_no_implementada",
        "nombre": "Funcion no implementada",
        "habilitada": False,
        "implementada": False,
        "plan_flag": None,
    },
}


class IAAccessService:
    CODIGOS = {
        "ACCESO_AUTORIZADO": "Acceso autorizado para utilizar la funcion IA.",
        "USUARIO_NO_AUTENTICADO": "Debes iniciar sesión para utilizar funciones IA.",
        "ROL_NO_AUTORIZADO": "Solo los cocineros pueden utilizar funciones IA.",
        "SUSCRIPCION_INEXISTENTE": "Necesitas una suscripción activa para utilizar funciones IA.",
        "SUSCRIPCION_INACTIVA": "Tu suscripción no está activa o ha vencido.",
        "PLAN_SIN_IA": "Tu plan actual no incluye funciones IA.",
        "LIMITE_IA_SUPERADO": "Has alcanzado el límite de uso IA permitido por tu plan.",
        "FUNCION_IA_NO_EXISTE": "La función IA solicitada no existe.",
        "IA_NO_IMPLEMENTADA": "La función IA aún no está disponible. Estará habilitada próximamente.",
    }

    def validarAccesoIA(self, usuarioId, funcion):
        return self.validar_acceso_ia(usuarioId, funcion)

    def validar_acceso_ia(self, usuario, funcion):
        funcion = str(funcion or "").strip()
        user_profile = self._get_user_profile(usuario)

        if not user_profile:
            return self._respuesta("USUARIO_NO_AUTENTICADO")

        result = self._validar(user_profile, funcion)
        self._registrar_intento(user_profile, funcion, result)
        return result

    def catalogo_funciones(self):
        return [dict(funcion) for funcion in FUNCIONES_IA.values()]

    def _validar(self, user_profile, funcion):
        if not user_profile.is_active or user_profile.role != UserProfile.ROLE_CHEF:
            return self._respuesta("ROL_NO_AUTORIZADO")

        funcion_ia = FUNCIONES_IA.get(funcion)
        if not funcion_ia:
            return self._respuesta("FUNCION_IA_NO_EXISTE")

        chef_profile = ChefProfile.objects.filter(user=user_profile).first()
        if not chef_profile:
            return self._respuesta("ROL_NO_AUTORIZADO")

        import os
        offline_mode = (
            os.getenv("IA_OFFLINE_MODE", "false").lower() == "true"
            or os.getenv("APP_OFFLINE_DEV_MODE", "false").lower() == "true"
        )
        if offline_mode:
            if not funcion_ia["implementada"] or not funcion_ia["habilitada"]:
                return self._respuesta("IA_NO_IMPLEMENTADA")
            return self._respuesta("ACCESO_AUTORIZADO", permitido=True)

        subscription = self._current_subscription(chef_profile)
        if not subscription:
            return self._respuesta("SUSCRIPCION_INEXISTENTE")
        if not self._is_active(subscription):
            return self._respuesta("SUSCRIPCION_INACTIVA")

        plan = subscription.plan
        if not self._plan_permite_funcion(plan, funcion_ia):
            return self._respuesta("PLAN_SIN_IA")
        if self._limite_superado(user_profile, subscription):
            return self._respuesta("LIMITE_IA_SUPERADO")
        if not funcion_ia["implementada"] or not funcion_ia["habilitada"]:
            return self._respuesta("IA_NO_IMPLEMENTADA")

        # Punto de extension futuro: aqui se invoca el microservicio IA real
        # despues de autorizar acceso y antes de registrar consumo efectivo.
        return self._respuesta("ACCESO_AUTORIZADO", permitido=True)

    def _get_user_profile(self, usuario):
        if not usuario:
            return None
        if hasattr(usuario, "is_authenticated") and not getattr(usuario, "is_authenticated", False):
            return None
        profile = getattr(usuario, "profile", None)
        if profile:
            return profile
        user_id = getattr(usuario, "id", usuario)
        profile = None
        try:
            profile = UserProfile.objects.filter(supabase_user_id=user_id).first()
        except (TypeError, ValueError):
            profile = None
        if profile:
            return profile
        try:
            return UserProfile.objects.filter(id=user_id).first()
        except (TypeError, ValueError):
            return None

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
            if chef_profile.ai_subscription_active:
                chef_profile.ai_subscription_active = False
                chef_profile.save(update_fields=["ai_subscription_active", "updated_at"])
        return subscription

    def _is_active(self, subscription):
        return bool(
            subscription
            and subscription.status == ChefAISubscription.Status.ACTIVE
            and subscription.end_date
            and subscription.end_date > timezone.now()
        )

    def _plan_permite_funcion(self, plan, funcion_ia):
        if plan.status != AISubscriptionPlan.Status.AVAILABLE or plan.ai_query_limit <= 0:
            return False
        flag = funcion_ia["plan_flag"]
        if flag is None:
            return True
        if isinstance(flag, tuple):
            return any(bool(getattr(plan, field, False)) for field in flag)
        return bool(getattr(plan, flag, False))

    def _limite_superado(self, user_profile, subscription):
        limit = subscription.plan.ai_query_limit
        if limit <= 0:
            return True
        used = UsoIA.objects.filter(
            usuario=user_profile,
            permitido=True,
            codigo_resultado="ACCESO_AUTORIZADO",
            fecha_intento__gte=subscription.start_date,
            fecha_intento__lte=subscription.end_date,
        ).count()
        return used >= limit

    def _registrar_intento(self, user_profile, funcion, result):
        UsoIA.objects.create(
            usuario=user_profile,
            funcion=funcion,
            permitido=result["permitido"],
            codigo_resultado=result["codigo"],
            mensaje_resultado=result["mensaje"],
        )

    def _respuesta(self, codigo, permitido=False):
        return {
            "permitido": permitido,
            "codigo": codigo,
            "mensaje": self.CODIGOS[codigo],
        }

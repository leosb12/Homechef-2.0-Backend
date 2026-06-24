from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from modules.gestion_cocinero.models import ChefProfile
from modules.gestion_usuarios_acceso_suscripcion.models import (
    AISubscriptionPayment,
    AISubscriptionPlan,
    ChefAISubscription,
    DeliveryProfile,
    UserProfile,
    UsoIA,
)


@override_settings(
    STRIPE_SECRET_KEY="sk_test_homechef",
    STRIPE_WEBHOOK_SECRET="whsec_homechef",
    STRIPE_SUCCESS_URL="http://localhost:5173/chef/ai-subscription?payment=stripe_success",
    STRIPE_CANCEL_URL="http://localhost:5173/chef/ai-subscription?payment=stripe_cancel",
    COINGATE_API_BASE_URL="https://api-sandbox.coingate.com/api/v2",
    COINGATE_API_TOKEN="coingate_test",
    COINGATE_CALLBACK_URL="http://localhost:8000/api/ia/subscription/payments/coingate/callback/",
    COINGATE_SUCCESS_URL="http://localhost:5173/chef/ai-subscription?payment=coingate_success",
    COINGATE_CANCEL_URL="http://localhost:5173/chef/ai-subscription?payment=coingate_cancel",
)
class AISubscriptionSandboxPaymentTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user_profile = UserProfile.objects.create(
            supabase_user_id=uuid4(),
            email="chef@example.com",
            first_name="Chef",
            last_name="Demo",
            full_name="Chef Demo",
            role=UserProfile.ROLE_CHEF,
            is_active=True,
        )
        self.chef_profile = ChefProfile.objects.create(
            user=self.user_profile,
            business_name="Chef Demo",
            status=ChefProfile.STATUS_APPROVED,
        )
        self.user = SimpleNamespace(
            id=str(self.user_profile.supabase_user_id),
            role=UserProfile.ROLE_CHEF,
            is_active=True,
            is_authenticated=True,
            profile=self.user_profile,
        )
        self.client.force_authenticate(user=self.user)
        self.plan = AISubscriptionPlan.objects.create(
            name="IA Pro",
            description="Plan IA premium",
            price="49.90",
            currency="USD",
            duration_days=30,
            status=AISubscriptionPlan.Status.AVAILABLE,
            ai_query_limit=100,
            ai_generation_limit=50,
            vision_enabled=True,
            production_recommendations_enabled=True,
            pricing_support_enabled=True,
            publishing_support_enabled=True,
            benefits=["Asistente", "Vision"],
        )

    @patch("modules.gestion_usuarios_acceso_suscripcion.providers.stripe_provider.stripe.checkout.Session.create")
    def test_create_stripe_sandbox_payment_pending(self, checkout_create):
        checkout_create.return_value = SimpleNamespace(
            id="cs_test_123",
            url="https://checkout.stripe.com/c/pay/cs_test_123",
            mode="payment",
            payment_status="unpaid",
            metadata={},
        )

        response = self.client.post(
            "/api/ia/subscription/subscribe/",
            {"plan_id": self.plan.id, "payment_provider": "STRIPE_SANDBOX"},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.data["success"])
        self.assertEqual(response.data["data"]["payment_status"], AISubscriptionPayment.Status.PENDING)
        self.assertEqual(response.data["data"]["provider"], "STRIPE_SANDBOX")
        self.assertIn("checkout.stripe.com", response.data["data"]["payment_url"])
        self.assertFalse(ChefAISubscription.objects.filter(status=ChefAISubscription.Status.ACTIVE).exists())
        self.chef_profile.refresh_from_db()
        self.assertFalse(self.chef_profile.ai_subscription_active)
        create_kwargs = checkout_create.call_args.kwargs
        self.assertIn("session_id={CHECKOUT_SESSION_ID}", create_kwargs["success_url"])
        self.assertIn("session_id={CHECKOUT_SESSION_ID}", create_kwargs["cancel_url"])

    @patch("modules.gestion_usuarios_acceso_suscripcion.providers.coingate_provider.requests.post")
    def test_create_coingate_sandbox_payment_pending(self, requests_post):
        requests_post.return_value = Mock(
            status_code=201,
            json=Mock(
                return_value={
                    "id": 456,
                    "order_id": "homechef-ai-1-test",
                    "payment_url": "https://sandbox.coingate.com/pay/456",
                    "status": "new",
                }
            ),
        )

        response = self.client.post(
            "/api/ia/subscription/subscribe/",
            {"plan_id": self.plan.id, "payment_provider": "COINGATE_SANDBOX"},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["data"]["payment_status"], AISubscriptionPayment.Status.PENDING)
        self.assertEqual(response.data["data"]["provider"], "COINGATE_SANDBOX")
        self.assertIn("sandbox.coingate.com", response.data["data"]["payment_url"])
        self.assertFalse(ChefAISubscription.objects.filter(status=ChefAISubscription.Status.ACTIVE).exists())
        request_body = requests_post.call_args.kwargs["json"]
        self.assertIn("coingate_order_id=homechef-ai-", request_body["success_url"])
        self.assertIn("coingate_order_id=homechef-ai-", request_body["cancel_url"])

    @patch("modules.gestion_usuarios_acceso_suscripcion.services.payment_callback_service.stripe.checkout.Session.retrieve")
    @patch("modules.gestion_usuarios_acceso_suscripcion.providers.stripe_provider.stripe.checkout.Session.create")
    def test_confirm_stripe_return_activates_subscription_without_webhook(self, checkout_create, checkout_retrieve):
        checkout_create.return_value = SimpleNamespace(
            id="cs_test_return",
            url="https://checkout.stripe.com/c/pay/cs_test_return",
            mode="payment",
            payment_status="unpaid",
            metadata={},
        )
        checkout_retrieve.return_value = SimpleNamespace(
            id="cs_test_return",
            status="complete",
            payment_status="paid",
            metadata={},
        )
        self.client.post(
            "/api/ia/subscription/subscribe/",
            {"plan_id": self.plan.id, "payment_provider": "STRIPE_SANDBOX"},
            format="json",
        )

        response = self.client.post(
            "/api/ia/subscription/payments/confirm-return/",
            {"provider": "STRIPE_SANDBOX", "stripe_session_id": "cs_test_return"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["data"]["handled"])
        payment = AISubscriptionPayment.objects.get(external_reference="cs_test_return")
        self.assertEqual(payment.status, AISubscriptionPayment.Status.APPROVED)
        self.assertEqual(payment.subscription.status, ChefAISubscription.Status.ACTIVE)
        self.chef_profile.refresh_from_db()
        self.assertTrue(self.chef_profile.ai_subscription_active)

    @patch("modules.gestion_usuarios_acceso_suscripcion.providers.stripe_provider.stripe.checkout.Session.create")
    def test_cancel_pending_payment_marks_subscription_cancelled(self, checkout_create):
        checkout_create.return_value = SimpleNamespace(
            id="cs_test_cancel",
            url="https://checkout.stripe.com/c/pay/cs_test_cancel",
            mode="payment",
            payment_status="unpaid",
            metadata={},
        )
        self.client.post(
            "/api/ia/subscription/subscribe/",
            {"plan_id": self.plan.id, "payment_provider": "STRIPE_SANDBOX"},
            format="json",
        )

        response = self.client.post(
            "/api/ia/subscription/cancel/",
            {"cancel_at_period_end": False, "reason": "Pago cancelado por prueba"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        payment = AISubscriptionPayment.objects.get(external_reference="cs_test_cancel")
        self.assertEqual(payment.status, AISubscriptionPayment.Status.REJECTED)
        self.assertEqual(payment.subscription.status, ChefAISubscription.Status.CANCELLED)

    @patch("modules.gestion_usuarios_acceso_suscripcion.providers.stripe_provider.stripe.checkout.Session.create")
    @patch("modules.gestion_usuarios_acceso_suscripcion.views.ai_subscription_views.stripe.Webhook.construct_event")
    def test_stripe_checkout_completed_activates_subscription(self, construct_event, checkout_create):
        checkout_create.return_value = SimpleNamespace(
            id="cs_test_completed",
            url="https://checkout.stripe.com/c/pay/cs_test_completed",
            mode="payment",
            payment_status="unpaid",
            metadata={},
        )
        self.client.post(
            "/api/ia/subscription/subscribe/",
            {"plan_id": self.plan.id, "payment_provider": "STRIPE_SANDBOX"},
            format="json",
        )
        construct_event.return_value = {
            "type": "checkout.session.completed",
            "data": {"object": {"id": "cs_test_completed"}},
        }

        response = self.client.post(
            "/api/ia/subscription/payments/stripe/webhook/",
            data=b"{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="test_signature",
        )

        self.assertEqual(response.status_code, 200)
        payment = AISubscriptionPayment.objects.get(external_reference="cs_test_completed")
        self.assertEqual(payment.status, AISubscriptionPayment.Status.APPROVED)
        self.assertEqual(payment.subscription.status, ChefAISubscription.Status.ACTIVE)
        self.chef_profile.refresh_from_db()
        self.assertTrue(self.chef_profile.ai_subscription_active)

    @patch("modules.gestion_usuarios_acceso_suscripcion.providers.coingate_provider.requests.post")
    def test_coingate_paid_callback_activates_subscription(self, requests_post):
        requests_post.return_value = Mock(
            status_code=201,
            json=Mock(return_value={"id": 789, "order_id": "cg-order", "payment_url": "https://sandbox.coingate.com/pay/789"}),
        )
        self.client.post(
            "/api/ia/subscription/subscribe/",
            {"plan_id": self.plan.id, "payment_provider": "COINGATE_SANDBOX"},
            format="json",
        )

        response = self.client.post(
            "/api/ia/subscription/payments/coingate/callback/",
            {"id": 789, "order_id": "cg-order", "status": "paid"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        payment = AISubscriptionPayment.objects.get(external_reference="789")
        self.assertEqual(payment.status, AISubscriptionPayment.Status.APPROVED)
        self.assertEqual(payment.subscription.status, ChefAISubscription.Status.ACTIVE)
        self.chef_profile.refresh_from_db()
        self.assertTrue(self.chef_profile.ai_subscription_active)

    @patch("modules.gestion_usuarios_acceso_suscripcion.providers.stripe_provider.stripe.checkout.Session.create")
    @patch("modules.gestion_usuarios_acceso_suscripcion.views.ai_subscription_views.stripe.Webhook.construct_event")
    def test_stripe_failed_does_not_activate_subscription(self, construct_event, checkout_create):
        checkout_create.return_value = SimpleNamespace(
            id="cs_test_failed",
            url="https://checkout.stripe.com/c/pay/cs_test_failed",
            mode="payment",
            payment_status="unpaid",
            metadata={},
        )
        self.client.post(
            "/api/ia/subscription/subscribe/",
            {"plan_id": self.plan.id, "payment_provider": "STRIPE_SANDBOX"},
            format="json",
        )
        payment = AISubscriptionPayment.objects.get(external_reference="cs_test_failed")
        construct_event.return_value = {
            "type": "payment_intent.payment_failed",
            "data": {
                "object": {
                    "metadata": {"payment_id": str(payment.id)},
                    "last_payment_error": {"message": "Your card was declined."},
                }
            },
        }

        response = self.client.post(
            "/api/ia/subscription/payments/stripe/webhook/",
            data=b"{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="test_signature",
        )

        self.assertEqual(response.status_code, 200)
        payment.refresh_from_db()
        self.assertEqual(payment.status, AISubscriptionPayment.Status.REJECTED)
        self.assertFalse(ChefAISubscription.objects.filter(status=ChefAISubscription.Status.ACTIVE).exists())

    @patch("modules.gestion_usuarios_acceso_suscripcion.providers.coingate_provider.requests.post")
    def test_coingate_expired_does_not_activate_subscription(self, requests_post):
        requests_post.return_value = Mock(
            status_code=201,
            json=Mock(return_value={"id": 987, "order_id": "cg-expired", "payment_url": "https://sandbox.coingate.com/pay/987"}),
        )
        self.client.post(
            "/api/ia/subscription/subscribe/",
            {"plan_id": self.plan.id, "payment_provider": "COINGATE_SANDBOX"},
            format="json",
        )

        response = self.client.post(
            "/api/ia/subscription/payments/coingate/callback/",
            {"id": 987, "order_id": "cg-expired", "status": "expired"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        payment = AISubscriptionPayment.objects.get(external_reference="987")
        self.assertEqual(payment.status, AISubscriptionPayment.Status.REJECTED)
        self.assertFalse(ChefAISubscription.objects.filter(status=ChefAISubscription.Status.ACTIVE).exists())

    def test_can_use_ai_requires_provider_callback(self):
        response = self.client.get("/api/ia/subscription/can-use-ai/")

        self.assertEqual(response.status_code, 402)
        self.assertFalse(response.data["success"])
        self.assertEqual(response.data["error"]["code"], "AI_SUBSCRIPTION_REQUIRED")

    def test_subscription_status_without_active_subscription_is_valid_state(self):
        response = self.client.get("/api/ia/subscription/status/")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["success"])
        self.assertIsNone(response.data["data"]["subscription"])
        self.assertFalse(response.data["data"]["can_use_ai"])
        self.assertEqual(response.data["data"]["limits"], {})

    def test_payment_history_without_payments_returns_empty_list(self):
        response = self.client.get("/api/ia/subscription/payments/")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["success"])
        self.assertEqual(response.data["data"], [])


class IAAccessServiceTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user_profile = UserProfile.objects.create(
            supabase_user_id=uuid4(),
            email="chef-ai@example.com",
            role=UserProfile.ROLE_CHEF,
            is_active=True,
        )
        self.chef_profile = ChefProfile.objects.create(
            user=self.user_profile,
            business_name="Chef IA",
            status=ChefProfile.STATUS_APPROVED,
        )
        self.user = SimpleNamespace(
            id=str(self.user_profile.supabase_user_id),
            role=UserProfile.ROLE_CHEF,
            is_active=True,
            is_authenticated=True,
            profile=self.user_profile,
        )
        self.client.force_authenticate(user=self.user)

    def _plan(self, **overrides):
        defaults = {
            "name": "Plan IA",
            "description": "Plan con funciones IA",
            "price": "49.90",
            "currency": "BOB",
            "duration_days": 30,
            "status": AISubscriptionPlan.Status.AVAILABLE,
            "ai_query_limit": 10,
            "ai_generation_limit": 5,
            "vision_enabled": True,
            "production_recommendations_enabled": True,
            "pricing_support_enabled": True,
            "publishing_support_enabled": True,
            "benefits": ["IA"],
        }
        defaults.update(overrides)
        return AISubscriptionPlan.objects.create(**defaults)

    def _subscription(self, plan, **overrides):
        now = timezone.now()
        defaults = {
            "chef_profile": self.chef_profile,
            "plan": plan,
            "status": ChefAISubscription.Status.ACTIVE,
            "start_date": now,
            "end_date": now + timedelta(days=30),
        }
        defaults.update(overrides)
        return ChefAISubscription.objects.create(**defaults)

    def _post(self, funcion="asistente_ia"):
        return self.client.post("/api/ia/usar-funcion", {"funcion": funcion}, format="json")

    def test_usar_funcion_sin_suscripcion(self):
        response = self._post()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["codigo"], "SUSCRIPCION_INEXISTENTE")
        self.assertFalse(response.data["permitido"])
        self.assertEqual(UsoIA.objects.count(), 1)

    @patch('os.getenv')
    def test_usar_funcion_offline_sin_suscripcion(self, mock_getenv):
        mock_getenv.side_effect = lambda key, default=None: "true" if key in ("IA_OFFLINE_MODE", "APP_OFFLINE_DEV_MODE") else default
        response = self._post("asistente_ia")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["codigo"], "ACCESO_AUTORIZADO")
        self.assertTrue(response.data["permitido"])

    def test_usar_funcion_con_suscripcion_vencida(self):
        plan = self._plan()
        now = timezone.now()
        self._subscription(
            plan,
            status=ChefAISubscription.Status.EXPIRED,
            start_date=now - timedelta(days=60),
            end_date=now - timedelta(days=30),
        )

        response = self._post()

        self.assertEqual(response.data["codigo"], "SUSCRIPCION_INACTIVA")
        self.assertEqual(UsoIA.objects.get().codigo_resultado, "SUSCRIPCION_INACTIVA")

    def test_usar_funcion_con_plan_sin_ia(self):
        plan = self._plan(ai_query_limit=0, vision_enabled=False)
        self._subscription(plan)

        response = self._post("vision_artificial")

        self.assertEqual(response.data["codigo"], "PLAN_SIN_IA")

    def test_usar_funcion_con_limite_superado(self):
        plan = self._plan(ai_query_limit=1)
        subscription = self._subscription(plan)
        u = UsoIA.objects.create(
            usuario=self.user_profile,
            funcion="asistente_ia",
            permitido=True,
            codigo_resultado="ACCESO_AUTORIZADO",
            mensaje_resultado="Acceso autorizado",
        )
        UsoIA.objects.filter(id=u.id).update(fecha_intento=subscription.start_date)

        response = self._post()

        self.assertEqual(response.data["codigo"], "LIMITE_IA_SUPERADO")

    def test_usar_funcion_no_existente(self):
        self._subscription(self._plan())

        response = self._post("funcion_inventada")

        self.assertEqual(response.data["codigo"], "FUNCION_IA_NO_EXISTE")

    def test_usar_funcion_no_implementada_registra_intento_sin_consumir_limite(self):
        self._subscription(self._plan())

        response = self._post("funcion_no_implementada")

        self.assertEqual(response.data["codigo"], "IA_NO_IMPLEMENTADA")
        self.assertEqual(
            response.data["mensaje"],
            "La función IA aún no está disponible. Estará habilitada próximamente.",
        )
        uso = UsoIA.objects.get()
        self.assertFalse(uso.permitido)
        self.assertEqual(uso.codigo_resultado, "IA_NO_IMPLEMENTADA")


class DeliveryRegistrationTests(TestCase):
    @patch("modules.gestion_usuarios_acceso_suscripcion.services.auth_service.StorageUploadService.upload_for_owner")
    def test_register_delivery_profile_creates_vehicle_record(self, upload_mock):
        api = APIClient()
        upload_mock.side_effect = [
            SimpleNamespace(public_url="https://cdn.test/front.jpg"),
            SimpleNamespace(public_url="https://cdn.test/rear.jpg"),
        ]

        response = api.post(
            "/api/v1/auth/register/",
            {
                "supabase_user_id": str(uuid4()),
                "first_name": "Rider",
                "last_name": "Demo",
                "email": "rider.demo@test.com",
                "phone": "70000001",
                "role": UserProfile.ROLE_DELIVERY,
                "accept_terms": "true",
                "delivery_vehicle_type": "motocicleta",
                "delivery_vehicle_brand": "Honda",
                "delivery_vehicle_model": "CB190R",
                "delivery_vehicle_plate": "1234-ABC",
                "delivery_vehicle_front_photo": SimpleUploadedFile(
                    "front.jpg",
                    b"front-image",
                    content_type="image/jpeg",
                ),
                "delivery_vehicle_rear_photo": SimpleUploadedFile(
                    "rear.jpg",
                    b"rear-image",
                    content_type="image/jpeg",
                ),
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, 201)
        profile = UserProfile.objects.get(email="rider.demo@test.com")
        delivery_profile = DeliveryProfile.objects.get(user=profile)
        self.assertEqual(profile.role, UserProfile.ROLE_DELIVERY)
        self.assertEqual(delivery_profile.vehicle_type, "motocicleta")
        self.assertEqual(delivery_profile.vehicle_brand, "Honda")
        self.assertEqual(delivery_profile.vehicle_model, "CB190R")
        self.assertEqual(delivery_profile.vehicle_plate, "1234-ABC")
        self.assertEqual(
            delivery_profile.approval_status,
            DeliveryProfile.ApprovalStatus.RECENTLY_REGISTERED,
        )
        self.assertEqual(delivery_profile.vehicle_front_image_url, "https://cdn.test/front.jpg")
        self.assertEqual(delivery_profile.vehicle_rear_image_url, "https://cdn.test/rear.jpg")
        self.assertEqual(upload_mock.call_count, 2)

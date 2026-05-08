from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from modules.gestion_cocinero.models import ChefProfile
from modules.gestion_usuarios_acceso_suscripcion.models import (
    AISubscriptionPayment,
    AISubscriptionPlan,
    ChefAISubscription,
    UserProfile,
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

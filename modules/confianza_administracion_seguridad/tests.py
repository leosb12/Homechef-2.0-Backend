from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from modules.confianza_administracion_seguridad.models import NotificationDeviceToken, OperationalNotification
from modules.delivery_logistica.models import DeliveryAssignment, DeliveryIncident
from modules.gestion_cocinero.models import ChefAvailability, ChefProfile, Dish
from modules.gestion_usuarios_acceso_suscripcion.models import DeliveryProfile, UserProfile
from modules.pedidos_checkout_pagos.models import Order, OrderAddress, OrderItem, OrderPayment


class AuthenticatedProfile:
    def __init__(self, profile: UserProfile):
        self.id = str(profile.supabase_user_id)
        self.email = profile.email
        self.first_name = profile.first_name
        self.last_name = profile.last_name
        self.role = profile.role
        self.is_active = profile.is_active
        self.is_authenticated = True


class NotificationCenterTests(TestCase):
    def setUp(self):
        self.api = APIClient()
        self.client_profile = UserProfile.objects.create(
            supabase_user_id=uuid4(),
            email="notify.client@test.com",
            role=UserProfile.ROLE_CLIENT,
            first_name="Cliente",
            is_active=True,
        )
        self.chef_profile = UserProfile.objects.create(
            supabase_user_id=uuid4(),
            email="notify.chef@test.com",
            role=UserProfile.ROLE_CHEF,
            first_name="Chef",
            is_active=True,
        )
        self.delivery_profile = UserProfile.objects.create(
            supabase_user_id=uuid4(),
            email="notify.delivery@test.com",
            role=UserProfile.ROLE_DELIVERY,
            first_name="Rider",
            is_active=True,
        )
        ChefAvailability.objects.create(
            chef=self.chef_profile,
            is_active=True,
            weekly_schedule=[self._open_slot()],
            accept_delivery=True,
            accept_pickup=True,
            simultaneous_orders_limit=10,
        )
        ChefProfile.objects.create(
            user=self.chef_profile,
            business_name="Chef HomeChef",
            location_latitude=-17.781,
            location_longitude=-63.181,
            location_address="Cocina central",
        )
        self.dish = Dish.objects.create(
            chef=self.chef_profile,
            name="Majadito",
            description="Casero",
            price=Decimal("20.00"),
            portions=10,
            ingredients=["arroz"],
            tags=["CASERO"],
            allergens=[],
            status=Dish.STATUS_PUBLISHED,
        )

    def test_checkout_and_chef_flow_create_persistent_notifications(self):
        self.api.force_authenticate(user=AuthenticatedProfile(self.client_profile))
        add_response = self.api.post(
            "/api/v1/orders/cart/items/",
            {"dish_id": self.dish.id, "quantity": 1},
            format="json",
        )
        cart_id = add_response.data["cart"]["id"]
        preview_response = self.api.post(
            "/api/v1/orders/checkout/preview/",
            {
                "cart_id": cart_id,
                "fulfillment_type": "pickup",
                "payment_method": "qr_simulado",
            },
            format="json",
        )
        confirm_response = self.api.post(
            "/api/v1/orders/checkout/confirm/",
            {
                "cart_id": cart_id,
                "fulfillment_type": "pickup",
                "payment_method": "qr_simulado",
                "expected_total": preview_response.data["pricing"]["total"],
            },
            format="json",
        )
        order_id = confirm_response.data["order_id"]
        payment = OrderPayment.objects.get(order_id=order_id)

        self.assertTrue(
            OperationalNotification.objects.filter(
                recipient=self.client_profile,
                event_code="ORDER_CREATED",
                order_ref=order_id,
            ).exists()
        )
        self.assertTrue(
            OperationalNotification.objects.filter(
                recipient=self.chef_profile,
                event_code="ORDER_CREATED",
                order_ref=order_id,
            ).exists()
        )

        session_code = payment.qr_session_code
        self.assertTrue(session_code)
        self.assertEqual(
            self.api.post(f"/api/v1/orders/payments/qr-sessions/{session_code}/start/", {}, format="json").status_code,
            200,
        )
        with patch("modules.pedidos_checkout_pagos.services.qr_payment_service.sleep", return_value=None):
            self.assertEqual(
                self.api.post(f"/api/v1/orders/payments/qr-sessions/{session_code}/confirm/", {}, format="json").status_code,
                200,
            )

        self.assertTrue(
            OperationalNotification.objects.filter(
                recipient=self.client_profile,
                event_code="PAYMENT_CONFIRMED",
                order_ref=order_id,
            ).exists()
        )

        self.api.force_authenticate(user=AuthenticatedProfile(self.chef_profile))
        self.assertEqual(self.api.post(f"/api/v1/orders/chef/orders/{order_id}/accept/", {}, format="json").status_code, 200)
        self.assertEqual(self.api.post(f"/api/v1/orders/chef/orders/{order_id}/preparing/", {}, format="json").status_code, 200)
        self.assertEqual(self.api.post(f"/api/v1/orders/chef/orders/{order_id}/ready/", {}, format="json").status_code, 200)

        self.api.force_authenticate(user=AuthenticatedProfile(self.client_profile))
        list_response = self.api.get("/api/v1/trust-admin/notifications/")
        self.assertEqual(list_response.status_code, 200)
        self.assertGreaterEqual(list_response.data["summary"]["unread_count"], 3)
        first_id = list_response.data["items"][0]["id"]
        mark_response = self.api.post(f"/api/v1/trust-admin/notifications/{first_id}/read/", {}, format="json")
        self.assertEqual(mark_response.status_code, 200)
        self.assertTrue(mark_response.data["notification"]["is_read"])

    def test_delivery_incident_notifications_and_device_registration(self):
        order = Order.objects.create(
            client=self.client_profile,
            chef=self.chef_profile,
            status=Order.Status.OUT_FOR_DELIVERY,
            fulfillment_type=Order.FulfillmentType.DELIVERY,
            payment_method=Order.PaymentMethod.CASH,
            subtotal=Decimal("20.00"),
            total=Decimal("27.00"),
            delivery_fee=Decimal("7.00"),
        )
        OrderAddress.objects.create(
            order=order,
            label="Casa",
            contact_name="Cliente",
            contact_phone="70000000",
            line_1="Av principal 123",
            reference="Puerta verde",
            latitude=-17.78,
            longitude=-63.18,
        )
        OrderItem.objects.create(
            order=order,
            dish=self.dish,
            quantity=1,
            unit_price=Decimal("20.00"),
            subtotal=Decimal("20.00"),
            dish_name_snapshot=self.dish.name,
            dish_description_snapshot=self.dish.description,
            dish_image_url_snapshot=self.dish.image_url,
        )
        OrderPayment.objects.create(
            order=order,
            method=Order.PaymentMethod.CASH,
            status=OrderPayment.Status.PENDING,
            currency="BOB",
            amount=Decimal("27.00"),
        )
        assignment = DeliveryAssignment.objects.create(
            order=order,
            status=DeliveryAssignment.Status.PICKED_UP,
            delivery_user=self.delivery_profile,
            assigned_at=timezone.now(),
            picked_up_at=timezone.now(),
        )

        self.api.force_authenticate(user=AuthenticatedProfile(self.delivery_profile))
        register_response = self.api.post(
            "/api/v1/trust-admin/notifications/devices/register/",
            {
                "platform": "android",
                "token": "token-demo-123",
                "device_id": "android-1",
                "device_label": "Pixel QA",
            },
            format="json",
        )
        self.assertEqual(register_response.status_code, 201)
        self.assertTrue(NotificationDeviceToken.objects.filter(user=self.delivery_profile, token="token-demo-123").exists())

        create_response = self.api.post(
            f"/api/v1/logistics/delivery/{assignment.id}/incidents/",
            {
                "code": DeliveryIncident.Code.DELAY,
                "description": "Se presento retraso por trafico peatonal.",
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, 201)

        for profile in (self.client_profile, self.chef_profile, self.delivery_profile):
            self.assertTrue(
                OperationalNotification.objects.filter(
                    recipient=profile,
                    event_code="DELIVERY_INCIDENT_OPENED",
                    order_ref=order.id,
                    assignment_ref=assignment.id,
                ).exists()
            )

        list_response = self.api.get("/api/v1/trust-admin/notifications/?unread_only=true")
        self.assertEqual(list_response.status_code, 200)
        self.assertGreaterEqual(list_response.data["summary"]["unread_count"], 1)

        unregister_response = self.api.post(
            "/api/v1/trust-admin/notifications/devices/unregister/",
            {"token": "token-demo-123"},
            format="json",
        )
        self.assertEqual(unregister_response.status_code, 200)
        self.assertFalse(NotificationDeviceToken.objects.get(token="token-demo-123").is_active)

    def test_admin_can_list_and_update_delivery_driver_status(self):
        admin_profile = UserProfile.objects.create(
            supabase_user_id=uuid4(),
            email="admin.delivery@test.com",
            role=UserProfile.ROLE_ADMIN,
            first_name="Admin",
            is_active=True,
        )
        delivery_profile = DeliveryProfile.objects.create(
            user=self.delivery_profile,
            vehicle_type=DeliveryProfile.VehicleType.MOTORCYCLE,
            vehicle_brand="Yamaha",
            vehicle_model="FZ",
            vehicle_plate="555-XYZ",
            vehicle_front_image_url="https://cdn.test/front.jpg",
            vehicle_rear_image_url="https://cdn.test/rear.jpg",
            approval_status=DeliveryProfile.ApprovalStatus.RECENTLY_REGISTERED,
        )

        self.api.force_authenticate(user=AuthenticatedProfile(admin_profile))
        list_response = self.api.get("/api/v1/trust-admin/delivery-drivers/")
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(len(list_response.data["items"]), 1)
        self.assertEqual(list_response.data["items"][0]["approval_status"], "recien_registrado")
        self.assertIn("availability_manual_status", list_response.data["items"][0])
        self.assertIn("active_assignments_count", list_response.data["items"][0])

        activate_response = self.api.post(
            f"/api/v1/trust-admin/delivery-drivers/{self.delivery_profile.supabase_user_id}/status/",
            {"approval_status": "activo"},
            format="json",
        )
        self.assertEqual(activate_response.status_code, 200)
        delivery_profile.refresh_from_db()
        self.assertEqual(delivery_profile.approval_status, DeliveryProfile.ApprovalStatus.ACTIVE)

        suspend_response = self.api.post(
            f"/api/v1/trust-admin/delivery-drivers/{self.delivery_profile.supabase_user_id}/status/",
            {"approval_status": "suspendido"},
            format="json",
        )
        self.assertEqual(suspend_response.status_code, 200)
        delivery_profile.refresh_from_db()
        self.assertEqual(delivery_profile.approval_status, DeliveryProfile.ApprovalStatus.SUSPENDED)

    def test_admin_can_read_active_delivery_orders_and_assignment_history(self):
        admin_profile = UserProfile.objects.create(
            supabase_user_id=uuid4(),
            email="admin.ops@test.com",
            role=UserProfile.ROLE_ADMIN,
            first_name="Ops",
            is_active=True,
        )
        self.delivery_profile.location_latitude = -17.7812
        self.delivery_profile.location_longitude = -63.1811
        self.delivery_profile.save(update_fields=["location_latitude", "location_longitude", "updated_at"])
        DeliveryProfile.objects.create(
            user=self.delivery_profile,
            vehicle_type=DeliveryProfile.VehicleType.MOTORCYCLE,
            vehicle_brand="Honda",
            vehicle_model="CB",
            vehicle_plate="999-XYZ",
            approval_status=DeliveryProfile.ApprovalStatus.ACTIVE,
        )
        order = Order.objects.create(
            client=self.client_profile,
            chef=self.chef_profile,
            status=Order.Status.READY_FOR_DELIVERY,
            fulfillment_type=Order.FulfillmentType.DELIVERY,
            payment_method=Order.PaymentMethod.CASH,
            subtotal=Decimal("20.00"),
            total=Decimal("27.00"),
            delivery_fee=Decimal("7.00"),
        )
        OrderAddress.objects.create(
            order=order,
            label="Casa",
            contact_name="Cliente",
            contact_phone="70000000",
            line_1="Av principal 123",
            reference="Puerta verde",
            latitude=-17.78,
            longitude=-63.18,
        )
        OrderItem.objects.create(
            order=order,
            dish=self.dish,
            quantity=1,
            unit_price=Decimal("20.00"),
            subtotal=Decimal("20.00"),
            dish_name_snapshot=self.dish.name,
            dish_description_snapshot=self.dish.description,
            dish_image_url_snapshot=self.dish.image_url,
        )
        OrderPayment.objects.create(
            order=order,
            method=Order.PaymentMethod.CASH,
            status=OrderPayment.Status.PENDING,
            currency="BOB",
            amount=Decimal("27.00"),
        )
        assignment = DeliveryAssignment.objects.create(order=order)

        self.api.force_authenticate(user=AuthenticatedProfile(admin_profile))
        list_response = self.api.get("/api/v1/trust-admin/delivery-orders/active/")
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.data["summary"]["total"], 1)
        self.assertEqual(list_response.data["items"][0]["order_id"], order.id)

        detail_response = self.api.get(f"/api/v1/trust-admin/delivery-orders/active/{order.id}/")
        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(detail_response.data["order"]["delivery_assignment"]["id"], assignment.id)
        self.assertIn(
            "candidate_snapshot",
            detail_response.data["order"]["delivery_assignment"]["operational_context"],
        )
        self.assertIn(
            "flow_audit",
            detail_response.data["order"]["delivery_assignment"]["operational_context"],
        )
        self.assertIn(
            detail_response.data["order"]["delivery_assignment"]["operational_context"]["flow_state"],
            {"OFFER_PENDING", "WAITING_ROUND_2", "OPEN_BOARD"},
        )

    def _open_slot(self):
        day = [
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
        ][timezone.localtime().weekday()]
        return {
            "day": day,
            "enabled": True,
            "start_time": "00:00",
            "end_time": "23:59",
            "modes": ["delivery", "pickup"],
        }

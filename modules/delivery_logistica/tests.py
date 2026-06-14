from decimal import Decimal
from uuid import uuid4

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from modules.delivery_logistica.models import DeliveryAssignment, DeliveryIncident, DeliveryLocationPing, DeliveryRouteSnapshot
from modules.gestion_cocinero.models import ChefAvailability, ChefProfile, Dish
from modules.gestion_usuarios_acceso_suscripcion.models import UserProfile
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


class DeliveryLogisticsApiTests(TestCase):
    def setUp(self):
        self.api = APIClient()
        self.client_profile = UserProfile.objects.create(
            supabase_user_id=uuid4(),
            email="client.delivery@test.com",
            role=UserProfile.ROLE_CLIENT,
            first_name="Cliente",
            is_active=True,
        )
        self.chef_profile = UserProfile.objects.create(
            supabase_user_id=uuid4(),
            email="chef.delivery@test.com",
            role=UserProfile.ROLE_CHEF,
            first_name="Chef",
            is_active=True,
        )
        self.delivery_profile = UserProfile.objects.create(
            supabase_user_id=uuid4(),
            email="delivery.one@test.com",
            role=UserProfile.ROLE_DELIVERY,
            first_name="Rider",
            is_active=True,
        )
        self.other_delivery = UserProfile.objects.create(
            supabase_user_id=uuid4(),
            email="delivery.two@test.com",
            role=UserProfile.ROLE_DELIVERY,
            first_name="Otro",
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
            name="Milanesa",
            description="Casera",
            price=Decimal("20.00"),
            portions=5,
            ingredients=["carne"],
            tags=["CASERO"],
            allergens=[],
            status=Dish.STATUS_PUBLISHED,
        )

    def test_delivery_assigned_and_active_endpoints_complete_real_flow(self):
        order = self._create_delivery_order(payment_method=Order.PaymentMethod.CASH)
        assignment = DeliveryAssignment.objects.create(order=order)

        self.api.force_authenticate(user=AuthenticatedProfile(self.delivery_profile))
        assigned_response = self.api.get("/api/v1/logistics/delivery/assigned/")
        self.assertEqual(assigned_response.status_code, 200)
        self.assertEqual(len(assigned_response.data["items"]), 1)
        self.assertEqual(assigned_response.data["items"][0]["available_actions"], ["accept"])

        accept_response = self.api.post(f"/api/v1/logistics/delivery/{assignment.id}/accept/", {}, format="json")
        self.assertEqual(accept_response.status_code, 200)
        self.assertEqual(accept_response.data["assignment"]["status"], DeliveryAssignment.Status.ASSIGNED)

        arrived_response = self.api.post(f"/api/v1/logistics/delivery/{assignment.id}/arrived-chef/", {}, format="json")
        self.assertEqual(arrived_response.status_code, 200)
        self.assertEqual(arrived_response.data["assignment"]["status"], DeliveryAssignment.Status.AT_CHEF)

        picked_up_response = self.api.post(f"/api/v1/logistics/delivery/{assignment.id}/picked-up/", {}, format="json")
        self.assertEqual(picked_up_response.status_code, 200)
        self.assertEqual(picked_up_response.data["assignment"]["status"], DeliveryAssignment.Status.PICKED_UP)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.OUT_FOR_DELIVERY)

        active_response = self.api.get("/api/v1/logistics/delivery/active/")
        self.assertEqual(active_response.status_code, 200)
        self.assertEqual(len(active_response.data["items"]), 1)
        self.assertEqual(active_response.data["items"][0]["available_actions"], ["delivered"])

        delivered_response = self.api.post(f"/api/v1/logistics/delivery/{assignment.id}/delivered/", {}, format="json")
        self.assertEqual(delivered_response.status_code, 200)
        self.assertEqual(delivered_response.data["assignment"]["status"], DeliveryAssignment.Status.DELIVERED)
        order.refresh_from_db()
        payment = OrderPayment.objects.get(order=order)
        assignment.refresh_from_db()
        self.assertEqual(order.status, Order.Status.DELIVERED)
        self.assertEqual(payment.status, OrderPayment.Status.CONFIRMED)
        self.assertIsNotNone(assignment.delivered_at)

    def test_delivery_detail_returns_order_context_and_history(self):
        order = self._create_delivery_order(payment_method=Order.PaymentMethod.BITCOIN_COINGATE)
        assignment = DeliveryAssignment.objects.create(
            order=order,
            status=DeliveryAssignment.Status.ASSIGNED,
            delivery_user=self.delivery_profile,
        )

        self.api.force_authenticate(user=AuthenticatedProfile(self.delivery_profile))
        detail_response = self.api.get(f"/api/v1/logistics/delivery/{assignment.id}/")
        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(detail_response.data["assignment"]["id"], assignment.id)
        self.assertEqual(detail_response.data["assignment"]["order"]["id"], order.id)
        self.assertEqual(detail_response.data["assignment"]["order"]["client"]["name"], "Cliente")
        self.assertIn("history", detail_response.data["assignment"])
        self.assertIn("map", detail_response.data["assignment"])

    def test_delivery_location_ping_and_route_endpoints_return_geospatial_payload(self):
        order = self._create_delivery_order(payment_method=Order.PaymentMethod.CASH)
        assignment = DeliveryAssignment.objects.create(
            order=order,
            status=DeliveryAssignment.Status.ASSIGNED,
            delivery_user=self.delivery_profile,
        )

        self.api.force_authenticate(user=AuthenticatedProfile(self.delivery_profile))
        ping_response = self.api.post(
            f"/api/v1/logistics/delivery/{assignment.id}/location-pings/",
            {
                "latitude": -17.7841,
                "longitude": -63.1799,
                "accuracy_meters": 6.5,
                "speed_mps": 1.7,
                "heading_degrees": 90,
            },
            format="json",
        )
        self.assertEqual(ping_response.status_code, 201)
        self.assertEqual(DeliveryLocationPing.objects.filter(assignment=assignment).count(), 1)
        self.assertEqual(DeliveryRouteSnapshot.objects.filter(assignment=assignment, is_current=True).count(), 1)
        self.assertTrue(ping_response.data["map"]["enabled"])
        self.assertGreaterEqual(len(ping_response.data["map"]["route"]["polyline"]), 2)

        current_response = self.api.get(f"/api/v1/logistics/delivery/{assignment.id}/current-location/")
        self.assertEqual(current_response.status_code, 200)
        self.assertAlmostEqual(current_response.data["current_location"]["lat"], -17.7841, places=4)

        route_response = self.api.get(f"/api/v1/logistics/delivery/{assignment.id}/route/")
        self.assertEqual(route_response.status_code, 200)
        self.assertEqual(route_response.data["map"]["route_kind"], DeliveryRouteSnapshot.RouteKind.TO_CHEF)
        self.assertGreaterEqual(len(route_response.data["route"]["polyline"]), 2)
        self.assertEqual(route_response.data["navigation"]["mode"], "walking")
        self.assertEqual(route_response.data["navigation"]["destination"]["kind"], "CHEF")
        self.assertGreater(route_response.data["navigation"]["summary"]["distance_meters"], 0)
        self.assertTrue(route_response.data["navigation"]["steps"])

    def test_delivery_route_refresh_uses_last_known_delivery_position_for_new_assignment(self):
        previous_order = self._create_delivery_order(payment_method=Order.PaymentMethod.CASH)
        previous_assignment = DeliveryAssignment.objects.create(
            order=previous_order,
            status=DeliveryAssignment.Status.PICKED_UP,
            delivery_user=self.delivery_profile,
        )
        DeliveryLocationPing.objects.create(
            assignment=previous_assignment,
            source=DeliveryLocationPing.Source.DELIVERY_APP,
            latitude=-17.7848,
            longitude=-63.1785,
            recorded_at=timezone.now(),
        )

        new_order = self._create_delivery_order(payment_method=Order.PaymentMethod.CASH)
        new_assignment = DeliveryAssignment.objects.create(
            order=new_order,
            status=DeliveryAssignment.Status.ASSIGNED,
            delivery_user=self.delivery_profile,
        )

        self.api.force_authenticate(user=AuthenticatedProfile(self.delivery_profile))
        refresh_response = self.api.post(
            f"/api/v1/logistics/delivery/{new_assignment.id}/route/refresh/",
            {},
            format="json",
        )
        self.assertEqual(refresh_response.status_code, 200)
        self.assertEqual(refresh_response.data["navigation"]["origin"]["kind"], "DELIVERY_LAST_KNOWN")
        self.assertEqual(refresh_response.data["navigation"]["destination"]["kind"], "CHEF")
        self.assertGreater(refresh_response.data["route"]["distance_meters"], 0)

    def test_delivery_cannot_operate_assignment_taken_by_other_rider(self):
        order = self._create_delivery_order(payment_method=Order.PaymentMethod.CASH)
        assignment = DeliveryAssignment.objects.create(
            order=order,
            status=DeliveryAssignment.Status.ASSIGNED,
            delivery_user=self.other_delivery,
        )
        self.api.force_authenticate(user=AuthenticatedProfile(self.delivery_profile))
        response = self.api.post(f"/api/v1/logistics/delivery/{assignment.id}/arrived-chef/", {}, format="json")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["code"], "assignment_not_found")

    def test_delivery_can_report_and_resolve_incident_with_evidence(self):
        order = self._create_delivery_order(payment_method=Order.PaymentMethod.CASH)
        assignment = DeliveryAssignment.objects.create(
            order=order,
            status=DeliveryAssignment.Status.PICKED_UP,
            delivery_user=self.delivery_profile,
        )

        self.api.force_authenticate(user=AuthenticatedProfile(self.delivery_profile))
        create_response = self.api.post(
            f"/api/v1/logistics/delivery/{assignment.id}/incidents/",
            {
                "code": DeliveryIncident.Code.ORDER_DAMAGED,
                "description": "El empaque se abrio en ruta.",
                "evidence_urls": ["https://example.com/evidence-1.jpg"],
                "evidence_note": "Foto tomada al llegar al cliente.",
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, 201)
        self.assertEqual(create_response.data["incident"]["code"], DeliveryIncident.Code.ORDER_DAMAGED)
        self.assertTrue(create_response.data["incidents"]["delivery_blocked"])
        incident_id = create_response.data["incident"]["id"]

        detail_response = self.api.get(f"/api/v1/logistics/delivery/{assignment.id}/")
        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(detail_response.data["assignment"]["incidents"]["open_count"], 1)
        self.assertEqual(detail_response.data["assignment"]["available_actions"], [])

        resolve_response = self.api.post(
            f"/api/v1/logistics/delivery/{assignment.id}/incidents/{incident_id}/resolve/",
            {"resolution_notes": "Se reemplazo el envase y el cliente acepto."},
            format="json",
        )
        self.assertEqual(resolve_response.status_code, 200)
        self.assertEqual(resolve_response.data["incident"]["status"], DeliveryIncident.Status.RESOLVED)
        self.assertFalse(resolve_response.data["incidents"]["delivery_blocked"])

    def _create_delivery_order(self, payment_method: str):
        order = Order.objects.create(
            client=self.client_profile,
            chef=self.chef_profile,
            status=Order.Status.READY_FOR_DELIVERY,
            fulfillment_type=Order.FulfillmentType.DELIVERY,
            payment_method=payment_method,
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
            method=payment_method,
            status=OrderPayment.Status.PENDING if payment_method == Order.PaymentMethod.CASH else OrderPayment.Status.CONFIRMED,
            currency="BOB",
            amount=Decimal("27.00"),
            confirmed_by_role="CLIENTE" if payment_method != Order.PaymentMethod.CASH else "",
            confirmed_at=timezone.now() if payment_method != Order.PaymentMethod.CASH else None,
        )
        return order

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

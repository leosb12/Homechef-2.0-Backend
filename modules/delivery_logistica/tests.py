from decimal import Decimal
from datetime import timedelta
from uuid import uuid4

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from modules.delivery_logistica.models import (
    DeliveryAssignment,
    DeliveryAssignmentOffer,
    DeliveryIncident,
    DeliveryLocationPing,
    DeliveryRouteSnapshot,
)
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
            location_latitude=-17.7815,
            location_longitude=-63.1811,
        )
        self.other_delivery = UserProfile.objects.create(
            supabase_user_id=uuid4(),
            email="delivery.two@test.com",
            role=UserProfile.ROLE_DELIVERY,
            first_name="Otro",
            is_active=True,
            location_latitude=-17.79,
            location_longitude=-63.19,
        )
        DeliveryProfile.objects.create(
            user=self.delivery_profile,
            vehicle_type=DeliveryProfile.VehicleType.MOTORCYCLE,
            vehicle_brand="Yamaha",
            vehicle_model="FZ",
            vehicle_plate="123-AAA",
            approval_status=DeliveryProfile.ApprovalStatus.ACTIVE,
            availability_manual_status=DeliveryProfile.AvailabilityManualStatus.AVAILABLE,
            availability_effective_status=DeliveryProfile.AvailabilityEffectiveStatus.AVAILABLE,
        )
        DeliveryProfile.objects.create(
            user=self.other_delivery,
            vehicle_type=DeliveryProfile.VehicleType.MOTORCYCLE,
            vehicle_brand="Suzuki",
            vehicle_model="GN",
            vehicle_plate="456-BBB",
            approval_status=DeliveryProfile.ApprovalStatus.ACTIVE,
            availability_manual_status=DeliveryProfile.AvailabilityManualStatus.AVAILABLE,
            availability_effective_status=DeliveryProfile.AvailabilityEffectiveStatus.AVAILABLE,
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
        offers_response = self.api.get("/api/v1/logistics/delivery/offers/pending/")
        self.assertEqual(offers_response.status_code, 200)
        self.assertEqual(len(offers_response.data["items"]), 1)
        self.assertEqual(offers_response.data["items"][0]["assignment_id"], assignment.id)
        assigned_response = self.api.post(f"/api/v1/logistics/delivery/{assignment.id}/accept/", {}, format="json")
        self.assertEqual(assigned_response.status_code, 200)
        self.assertEqual(assigned_response.data["assignment"]["status"], DeliveryAssignment.Status.ASSIGNED)
        assignment.refresh_from_db()
        self.assertEqual(assignment.delivery_user_id, self.delivery_profile.id)

        arrived_response = self.api.post(f"/api/v1/logistics/delivery/{assignment.id}/arrived-chef/", {}, format="json")
        self.assertEqual(arrived_response.status_code, 200)
        self.assertEqual(arrived_response.data["assignment"]["status"], DeliveryAssignment.Status.AT_CHEF)

        picked_up_response = self.api.post(f"/api/v1/logistics/delivery/{assignment.id}/picked-up/", {}, format="json")
        self.assertEqual(picked_up_response.status_code, 200)
        self.assertEqual(picked_up_response.data["assignment"]["status"], DeliveryAssignment.Status.EN_ROUTE_TO_CLIENT)
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

    def test_delivery_route_quality_exposes_provider_and_used_points(self):
        order = self._create_delivery_order(payment_method=Order.PaymentMethod.CASH)
        assignment = DeliveryAssignment.objects.create(
            order=order,
            status=DeliveryAssignment.Status.ASSIGNED,
            delivery_user=self.delivery_profile,
        )

        self.api.force_authenticate(user=AuthenticatedProfile(self.delivery_profile))
        response = self.api.post(
            f"/api/v1/logistics/delivery/{assignment.id}/route/refresh/",
            {},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("quality", response.data["map"])
        self.assertIn("provider", response.data["map"]["quality"])
        self.assertEqual(
            response.data["map"]["quality"]["destination_used"]["kind"],
            "CHEF",
        )
        self.assertIn("provider", response.data["navigation"]["summary"])

    def test_delivery_grouped_route_lists_next_stops_for_same_rider(self):
        first_order = self._create_delivery_order(payment_method=Order.PaymentMethod.CASH)
        second_order = self._create_delivery_order(payment_method=Order.PaymentMethod.BITCOIN_COINGATE)
        first_assignment = DeliveryAssignment.objects.create(
            order=first_order,
            status=DeliveryAssignment.Status.ASSIGNED,
            delivery_user=self.delivery_profile,
        )
        DeliveryAssignment.objects.create(
            order=second_order,
            status=DeliveryAssignment.Status.PICKED_UP,
            delivery_user=self.delivery_profile,
        )
        DeliveryLocationPing.objects.create(
            assignment=first_assignment,
            source=DeliveryLocationPing.Source.DELIVERY_APP,
            latitude=-17.784,
            longitude=-63.18,
            recorded_at=timezone.now(),
        )

        self.api.force_authenticate(user=AuthenticatedProfile(self.delivery_profile))
        response = self.api.get(f"/api/v1/logistics/delivery/{first_assignment.id}/")
        self.assertEqual(response.status_code, 200)
        grouped = response.data["assignment"]["map"]["grouped_route"]
        self.assertEqual(grouped["active_assignment_count"], 2)
        self.assertGreaterEqual(len(grouped["stops"]), 2)
        self.assertTrue(grouped["next_stop"])
        self.assertGreater(grouped["distance_meters"], 0)

    def test_delivery_cannot_operate_assignment_taken_by_other_rider(self):
        order = self._create_delivery_order(payment_method=Order.PaymentMethod.CASH)
        assignment = DeliveryAssignment.objects.create(
            order=order,
            status=DeliveryAssignment.Status.ASSIGNED,
            delivery_user=self.other_delivery,
        )
        self.api.force_authenticate(user=AuthenticatedProfile(self.delivery_profile))
        response = self.api.post(f"/api/v1/logistics/delivery/{assignment.id}/arrived-chef/", {}, format="json")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["code"], "assignment_reassigned")

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

    def test_delivery_accepts_unassigned_assignment_without_outer_join_lock_error(self):
        order = self._create_delivery_order(payment_method=Order.PaymentMethod.CASH)
        assignment = DeliveryAssignment.objects.create(order=order)

        self.api.force_authenticate(user=AuthenticatedProfile(self.delivery_profile))
        response = self.api.post(
            f"/api/v1/logistics/delivery/{assignment.id}/accept/",
            {},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        assignment.refresh_from_db()
        self.assertEqual(assignment.status, DeliveryAssignment.Status.ASSIGNED)
        self.assertEqual(assignment.delivery_user_id, self.delivery_profile.id)

    def test_delivery_cannot_report_incident_on_unassigned_assignment(self):
        order = self._create_delivery_order(payment_method=Order.PaymentMethod.CASH)
        assignment = DeliveryAssignment.objects.create(order=order)

        self.api.force_authenticate(user=AuthenticatedProfile(self.delivery_profile))
        response = self.api.post(
            f"/api/v1/logistics/delivery/{assignment.id}/incidents/",
            {
                "code": DeliveryIncident.Code.DELAY,
                "description": "Intento reportar antes de tomar la entrega.",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 404)

    def test_delivery_reject_offer_creates_second_round_instead_of_immediate_reassignment(self):
        order = self._create_delivery_order(payment_method=Order.PaymentMethod.CASH)
        assignment = DeliveryAssignment.objects.create(order=order)

        self.api.force_authenticate(user=AuthenticatedProfile(self.delivery_profile))
        initial_offer_response = self.api.get("/api/v1/logistics/delivery/offers/pending/")
        self.assertEqual(initial_offer_response.status_code, 200)
        self.assertEqual(len(initial_offer_response.data["items"]), 1)

        response = self.api.post(f"/api/v1/logistics/delivery/{assignment.id}/reject/", {}, format="json")
        self.assertEqual(response.status_code, 200)
        assignment.refresh_from_db()
        self.assertIsNone(assignment.delivery_user_id)
        self.assertEqual(assignment.metadata.get("offer_flow_state"), "WAITING_ROUND_2")

        assignment.metadata = {
            **(assignment.metadata or {}),
            "offer_next_round_at": (timezone.now() - timedelta(seconds=1)).isoformat(),
        }
        assignment.save(update_fields=["metadata", "updated_at"])

        self.api.force_authenticate(user=AuthenticatedProfile(self.other_delivery))
        second_offer_response = self.api.get("/api/v1/logistics/delivery/offers/pending/")
        self.assertEqual(second_offer_response.status_code, 200)
        self.assertEqual(len(second_offer_response.data["items"]), 1)
        self.assertEqual(second_offer_response.data["items"][0]["assignment_id"], assignment.id)

        self.api.force_authenticate(user=AuthenticatedProfile(self.delivery_profile))
        hidden_response = self.api.get("/api/v1/logistics/delivery/offers/pending/")
        self.assertEqual(hidden_response.status_code, 200)
        self.assertEqual(len(hidden_response.data["items"]), 0)

    def test_second_failed_offer_opens_assignment_in_open_board(self):
        order = self._create_delivery_order(payment_method=Order.PaymentMethod.CASH)
        assignment = DeliveryAssignment.objects.create(order=order)

        self.api.force_authenticate(user=AuthenticatedProfile(self.delivery_profile))
        self.api.get("/api/v1/logistics/delivery/offers/pending/")
        reject_first_response = self.api.post(
            f"/api/v1/logistics/delivery/{assignment.id}/reject/",
            {},
            format="json",
        )
        self.assertEqual(reject_first_response.status_code, 200)

        assignment.refresh_from_db()
        assignment.metadata = {
            **(assignment.metadata or {}),
            "offer_next_round_at": (timezone.now() - timedelta(seconds=1)).isoformat(),
        }
        assignment.save(update_fields=["metadata", "updated_at"])

        self.api.force_authenticate(user=AuthenticatedProfile(self.other_delivery))
        second_offer_response = self.api.get("/api/v1/logistics/delivery/offers/pending/")
        self.assertEqual(second_offer_response.status_code, 200)
        self.assertEqual(len(second_offer_response.data["items"]), 1)

        reject_second_response = self.api.post(
            f"/api/v1/logistics/delivery/{assignment.id}/reject/",
            {},
            format="json",
        )
        self.assertEqual(reject_second_response.status_code, 200)

        self.api.force_authenticate(user=AuthenticatedProfile(self.delivery_profile))
        open_board_response = self.api.get("/api/v1/logistics/delivery/open-board/")
        self.assertEqual(open_board_response.status_code, 200)
        self.assertEqual(len(open_board_response.data["items"]), 1)
        self.assertEqual(open_board_response.data["items"][0]["id"], assignment.id)

        assignment.refresh_from_db()
        self.assertTrue((assignment.metadata or {}).get("open_board_enabled"))

    def test_delivery_open_board_is_enabled_after_two_search_rounds_without_candidates(self):
        delivery_profile = DeliveryProfile.objects.get(user=self.delivery_profile)
        other_profile = DeliveryProfile.objects.get(user=self.other_delivery)
        delivery_profile.availability_manual_status = DeliveryProfile.AvailabilityManualStatus.OFF_DUTY
        delivery_profile.availability_effective_status = DeliveryProfile.AvailabilityEffectiveStatus.OFF_DUTY
        delivery_profile.save(update_fields=["availability_manual_status", "availability_effective_status", "updated_at"])
        other_profile.availability_manual_status = DeliveryProfile.AvailabilityManualStatus.OFF_DUTY
        other_profile.availability_effective_status = DeliveryProfile.AvailabilityEffectiveStatus.OFF_DUTY
        other_profile.save(update_fields=["availability_manual_status", "availability_effective_status", "updated_at"])

        order = self._create_delivery_order(payment_method=Order.PaymentMethod.CASH)
        assignment = DeliveryAssignment.objects.create(order=order)

        self.api.force_authenticate(user=AuthenticatedProfile(self.delivery_profile))
        first_response = self.api.get("/api/v1/logistics/delivery/offers/pending/")
        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(len(first_response.data["items"]), 0)

        assignment.refresh_from_db()
        self.assertEqual(assignment.metadata.get("no_candidate_attempt_count"), 1)
        assignment.metadata = {
            **(assignment.metadata or {}),
            "offer_next_round_at": (timezone.now() - timedelta(seconds=1)).isoformat(),
        }
        assignment.save(update_fields=["metadata", "updated_at"])

        open_board_response = self.api.get("/api/v1/logistics/delivery/open-board/")
        self.assertEqual(open_board_response.status_code, 200)
        self.assertEqual(len(open_board_response.data["items"]), 1)
        self.assertEqual(open_board_response.data["items"][0]["id"], assignment.id)

    def test_open_board_claim_is_granted_only_to_first_delivery(self):
        order = self._create_delivery_order(payment_method=Order.PaymentMethod.CASH)
        assignment = DeliveryAssignment.objects.create(
            order=order,
            metadata={
                "offer_flow_state": "OPEN_BOARD",
                "open_board_enabled": True,
                "open_board_enabled_at": timezone.now().isoformat(),
                "open_board_reason": "test",
            },
        )

        self.api.force_authenticate(user=AuthenticatedProfile(self.delivery_profile))
        claim_response = self.api.post(
            f"/api/v1/logistics/delivery/{assignment.id}/claim/",
            {},
            format="json",
        )
        self.assertEqual(claim_response.status_code, 200)
        assignment.refresh_from_db()
        self.assertEqual(assignment.delivery_user_id, self.delivery_profile.id)
        self.assertEqual(assignment.status, DeliveryAssignment.Status.ASSIGNED)

        self.api.force_authenticate(user=AuthenticatedProfile(self.other_delivery))
        second_claim_response = self.api.post(
            f"/api/v1/logistics/delivery/{assignment.id}/claim/",
            {},
            format="json",
        )
        self.assertEqual(second_claim_response.status_code, 409)
        self.assertEqual(second_claim_response.data["code"], "assignment_already_taken")

    def test_cancel_assigned_delivery_moves_assignment_back_to_open_board(self):
        order = self._create_delivery_order(payment_method=Order.PaymentMethod.CASH)
        assignment = DeliveryAssignment.objects.create(
            order=order,
            status=DeliveryAssignment.Status.ASSIGNED,
            delivery_user=self.delivery_profile,
            assigned_at=timezone.now(),
            metadata={"assigned_delivery_user_id": str(self.delivery_profile.supabase_user_id)},
        )

        self.api.force_authenticate(user=AuthenticatedProfile(self.delivery_profile))
        cancel_response = self.api.post(
            f"/api/v1/logistics/delivery/{assignment.id}/cancel/",
            {},
            format="json",
        )
        self.assertEqual(cancel_response.status_code, 200)

        assignment.refresh_from_db()
        self.assertIsNone(assignment.delivery_user_id)
        self.assertEqual(assignment.status, DeliveryAssignment.Status.UNASSIGNED)
        self.assertTrue((assignment.metadata or {}).get("open_board_enabled"))

        self.api.force_authenticate(user=AuthenticatedProfile(self.other_delivery))
        open_board_response = self.api.get("/api/v1/logistics/delivery/open-board/")
        self.assertEqual(open_board_response.status_code, 200)
        self.assertEqual(len(open_board_response.data["items"]), 1)
        self.assertEqual(open_board_response.data["items"][0]["id"], assignment.id)

    def test_cancel_is_rejected_after_arriving_to_chef(self):
        order = self._create_delivery_order(payment_method=Order.PaymentMethod.CASH)
        assignment = DeliveryAssignment.objects.create(
            order=order,
            status=DeliveryAssignment.Status.AT_CHEF,
            delivery_user=self.delivery_profile,
            assigned_at=timezone.now(),
        )

        self.api.force_authenticate(user=AuthenticatedProfile(self.delivery_profile))
        response = self.api.post(
            f"/api/v1/logistics/delivery/{assignment.id}/cancel/",
            {},
            format="json",
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["code"], "assignment_cancel_not_allowed")

    def test_only_nearest_driver_sees_auto_assigned_delivery(self):
        order = self._create_delivery_order(payment_method=Order.PaymentMethod.CASH)
        DeliveryAssignment.objects.create(order=order)

        self.api.force_authenticate(user=AuthenticatedProfile(self.delivery_profile))
        primary_response = self.api.get("/api/v1/logistics/delivery/offers/pending/")
        self.assertEqual(primary_response.status_code, 200)
        self.assertEqual(len(primary_response.data["items"]), 1)

        self.api.force_authenticate(user=AuthenticatedProfile(self.other_delivery))
        secondary_response = self.api.get("/api/v1/logistics/delivery/offers/pending/")
        self.assertEqual(secondary_response.status_code, 200)
        self.assertEqual(len(secondary_response.data["items"]), 0)

    def test_delivery_board_hides_assignment_until_order_is_ready_for_delivery(self):
        order = self._create_delivery_order(payment_method=Order.PaymentMethod.CASH)
        order.status = Order.Status.AWAITING_CHEF_CONFIRMATION
        order.save(update_fields=["status", "updated_at"])
        DeliveryAssignment.objects.create(order=order)

        self.api.force_authenticate(user=AuthenticatedProfile(self.delivery_profile))
        response = self.api.get("/api/v1/logistics/delivery/offers/pending/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["items"]), 0)

    def test_delivery_availability_can_toggle_manual_and_effective_status(self):
        self.api.force_authenticate(user=AuthenticatedProfile(self.delivery_profile))
        initial_response = self.api.get("/api/v1/logistics/delivery/availability/")
        self.assertEqual(initial_response.status_code, 200)
        self.assertEqual(initial_response.data["manual_status"], "DISPONIBLE")
        self.assertEqual(initial_response.data["effective_status"], "DISPONIBLE")

        update_response = self.api.post(
            "/api/v1/logistics/delivery/availability/",
            {"manual_status": "FUERA_DE_SERVICIO"},
            format="json",
        )
        self.assertEqual(update_response.status_code, 200)
        self.assertEqual(update_response.data["manual_status"], "FUERA_DE_SERVICIO")
        self.assertEqual(update_response.data["effective_status"], "FUERA_DE_SERVICIO")
        follow_up_response = self.api.get("/api/v1/logistics/delivery/availability/")
        self.assertEqual(follow_up_response.status_code, 200)
        self.assertEqual(follow_up_response.data["manual_status"], "FUERA_DE_SERVICIO")
        self.assertEqual(follow_up_response.data["effective_status"], "FUERA_DE_SERVICIO")

        second_update_response = self.api.post(
            "/api/v1/logistics/delivery/availability/",
            {"manual_status": "DISPONIBLE"},
            format="json",
        )
        self.assertEqual(second_update_response.status_code, 200)
        self.assertEqual(second_update_response.data["manual_status"], "DISPONIBLE")
        self.assertEqual(second_update_response.data["effective_status"], "DISPONIBLE")
        second_follow_up_response = self.api.get("/api/v1/logistics/delivery/availability/")
        self.assertEqual(second_follow_up_response.status_code, 200)
        self.assertEqual(second_follow_up_response.data["manual_status"], "DISPONIBLE")
        self.assertEqual(second_follow_up_response.data["effective_status"], "DISPONIBLE")

    def test_delivery_effective_status_turns_busy_at_capacity_limit(self):
        first = self._create_delivery_order(payment_method=Order.PaymentMethod.CASH)
        second = self._create_delivery_order(payment_method=Order.PaymentMethod.CASH)
        third = self._create_delivery_order(payment_method=Order.PaymentMethod.CASH)
        DeliveryAssignment.objects.create(order=first, status=DeliveryAssignment.Status.ASSIGNED, delivery_user=self.delivery_profile)
        DeliveryAssignment.objects.create(order=second, status=DeliveryAssignment.Status.AT_CHEF, delivery_user=self.delivery_profile)
        DeliveryAssignment.objects.create(order=third, status=DeliveryAssignment.Status.PICKED_UP, delivery_user=self.delivery_profile)

        self.api.force_authenticate(user=AuthenticatedProfile(self.delivery_profile))
        response = self.api.get("/api/v1/logistics/delivery/availability/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["active_assignments_count"], 3)
        self.assertEqual(response.data["effective_status"], "OCUPADO")

    def test_pending_offer_visibility_is_exclusive_and_accept_assigns_delivery(self):
        order = self._create_delivery_order(payment_method=Order.PaymentMethod.CASH)
        assignment = DeliveryAssignment.objects.create(order=order)

        self.api.force_authenticate(user=AuthenticatedProfile(self.delivery_profile))
        offers_response = self.api.get("/api/v1/logistics/delivery/offers/pending/")
        self.assertEqual(offers_response.status_code, 200)
        self.assertEqual(len(offers_response.data["items"]), 1)
        self.assertEqual(offers_response.data["items"][0]["assignment_id"], assignment.id)
        offer = DeliveryAssignmentOffer.objects.get(assignment=assignment)
        self.assertEqual(offer.delivery_user_id, self.delivery_profile.id)

        self.api.force_authenticate(user=AuthenticatedProfile(self.other_delivery))
        hidden_response = self.api.get("/api/v1/logistics/delivery/offers/pending/")
        self.assertEqual(hidden_response.status_code, 200)
        self.assertEqual(len(hidden_response.data["items"]), 0)

        self.api.force_authenticate(user=AuthenticatedProfile(self.delivery_profile))
        accept_response = self.api.post(
            f"/api/v1/logistics/delivery/{assignment.id}/accept/",
            {},
            format="json",
        )
        self.assertEqual(accept_response.status_code, 200)
        assignment.refresh_from_db()
        offer.refresh_from_db()
        self.assertEqual(assignment.delivery_user_id, self.delivery_profile.id)
        self.assertEqual(offer.status, DeliveryAssignmentOffer.Status.ACCEPTED)
        self.assertTrue((assignment.metadata or {}).get("flow_audit"))

    def test_accept_offer_is_idempotent_for_same_delivery(self):
        order = self._create_delivery_order(payment_method=Order.PaymentMethod.CASH)
        assignment = DeliveryAssignment.objects.create(order=order)

        self.api.force_authenticate(user=AuthenticatedProfile(self.delivery_profile))
        first_response = self.api.post(
            f"/api/v1/logistics/delivery/{assignment.id}/accept/",
            {},
            format="json",
        )
        self.assertEqual(first_response.status_code, 200)

        second_response = self.api.post(
            f"/api/v1/logistics/delivery/{assignment.id}/accept/",
            {},
            format="json",
        )
        self.assertEqual(second_response.status_code, 200)
        assignment.refresh_from_db()
        self.assertEqual(assignment.delivery_user_id, self.delivery_profile.id)

    def test_reject_offer_is_idempotent_after_first_rejection(self):
        order = self._create_delivery_order(payment_method=Order.PaymentMethod.CASH)
        assignment = DeliveryAssignment.objects.create(order=order)

        self.api.force_authenticate(user=AuthenticatedProfile(self.delivery_profile))
        self.api.get("/api/v1/logistics/delivery/offers/pending/")
        first_response = self.api.post(
            f"/api/v1/logistics/delivery/{assignment.id}/reject/",
            {},
            format="json",
        )
        self.assertEqual(first_response.status_code, 200)

        second_response = self.api.post(
            f"/api/v1/logistics/delivery/{assignment.id}/reject/",
            {},
            format="json",
        )
        self.assertEqual(second_response.status_code, 200)
        self.assertIn("descartada", second_response.data["message"].lower())

    def test_cancel_to_open_board_is_idempotent_for_same_delivery(self):
        order = self._create_delivery_order(payment_method=Order.PaymentMethod.CASH)
        assignment = DeliveryAssignment.objects.create(
            order=order,
            status=DeliveryAssignment.Status.ASSIGNED,
            delivery_user=self.delivery_profile,
            assigned_at=timezone.now(),
        )

        self.api.force_authenticate(user=AuthenticatedProfile(self.delivery_profile))
        first_response = self.api.post(
            f"/api/v1/logistics/delivery/{assignment.id}/cancel/",
            {},
            format="json",
        )
        self.assertEqual(first_response.status_code, 200)

        second_response = self.api.post(
            f"/api/v1/logistics/delivery/{assignment.id}/cancel/",
            {},
            format="json",
        )
        self.assertEqual(second_response.status_code, 200)
        assignment.refresh_from_db()
        self.assertIsNone(assignment.delivery_user_id)
        self.assertTrue((assignment.metadata or {}).get("open_board_enabled"))

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

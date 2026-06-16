from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock, patch
from uuid import uuid4

from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from modules.confianza_administracion_seguridad.models import OperationalNotification
from modules.delivery_logistica.models import DeliveryAssignment, DeliveryIncident, DeliveryRouteSnapshot, DeliveryStatusHistory
from modules.gestion_cocinero.models import ChefAvailability, ChefProfile, DailyMenu, DailyMenuItem, Dish
from modules.gestion_usuarios_acceso_suscripcion.models import DeliveryProfile, UserProfile
from modules.pedidos_checkout_pagos.models import (
    Cart,
    CartItem,
    Order,
    OrderItem,
    OrderPayment,
    OrderPaymentEvent,
    OrderReceipt,
    OrderStatusHistory,
    PickupConfirmation,
    SimulatedQRPaymentSession,
)
from modules.pedidos_checkout_pagos.services import CartService, DishStockService, StockValidationError


class AuthenticatedProfile:
    def __init__(self, profile: UserProfile):
        self.id = str(profile.supabase_user_id)
        self.email = profile.email
        self.first_name = profile.first_name
        self.last_name = profile.last_name
        self.role = profile.role
        self.is_active = profile.is_active
        self.is_authenticated = True


class DishStockServiceTests(TestCase):
    def setUp(self):
        self.service = DishStockService()
        self.client_profile = UserProfile.objects.create(
            supabase_user_id=uuid4(),
            email="client@test.com",
            role=UserProfile.ROLE_CLIENT,
            first_name="Client",
            is_active=True,
        )
        self.chef_profile = UserProfile.objects.create(
            supabase_user_id=uuid4(),
            email="chef@test.com",
            role=UserProfile.ROLE_CHEF,
            first_name="Chef",
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
        self.dish = Dish.objects.create(
            chef=self.chef_profile,
            name="Sopa del dia",
            description="Caliente",
            price=Decimal("15.00"),
            portions=8,
            ingredients=["agua"],
            tags=["CASERO"],
            allergens=[],
            status=Dish.STATUS_PUBLISHED,
        )

    def test_snapshot_prioritizes_active_menu_portions(self):
        menu = DailyMenu.objects.create(chef=self.chef_profile, schedule="10:00 - 14:00", is_active=True)
        DailyMenuItem.objects.create(menu=menu, dish=self.dish, portions=3, status=DailyMenuItem.STATUS_AVAILABLE)

        snapshot = self.service.get_snapshot(self.dish.id)

        self.assertTrue(snapshot.uses_active_menu)
        self.assertEqual(snapshot.source_type, OrderItem.StockSourceType.DAILY_MENU_ITEM)
        self.assertEqual(snapshot.available_portions, 3)
        self.assertTrue(snapshot.is_available)

    def test_snapshot_falls_back_to_dish_portions_when_no_active_menu_item(self):
        snapshot = self.service.get_snapshot(self.dish.id)

        self.assertFalse(snapshot.uses_active_menu)
        self.assertEqual(snapshot.source_type, OrderItem.StockSourceType.DISH)
        self.assertEqual(snapshot.available_portions, 8)
        self.assertTrue(snapshot.is_available)

    def test_validate_dish_request_rejects_insufficient_portions(self):
        with self.assertRaises(StockValidationError) as ctx:
            self.service.validate_dish_request(self.dish.id, 9)

        self.assertEqual(ctx.exception.code, "stock_unavailable")
        self.assertEqual(ctx.exception.details["available_portions"], 8)

    def test_reserve_and_release_order_stock_against_active_menu_item(self):
        menu = DailyMenu.objects.create(chef=self.chef_profile, schedule="10:00 - 14:00", is_active=True)
        menu_item = DailyMenuItem.objects.create(
            menu=menu,
            dish=self.dish,
            portions=5,
            status=DailyMenuItem.STATUS_AVAILABLE,
        )
        order = Order.objects.create(
            client=self.client_profile,
            chef=self.chef_profile,
            status=Order.Status.PENDING_PAYMENT,
            fulfillment_type=Order.FulfillmentType.PICKUP,
            payment_method=Order.PaymentMethod.CASH,
            subtotal=Decimal("30.00"),
            total=Decimal("30.00"),
        )
        item = OrderItem.objects.create(
            order=order,
            dish=self.dish,
            quantity=2,
            unit_price=Decimal("15.00"),
            subtotal=Decimal("30.00"),
            dish_name_snapshot=self.dish.name,
            dish_description_snapshot=self.dish.description,
            dish_image_url_snapshot=self.dish.image_url,
        )

        self.service.reserve_order_stock(order)
        menu_item.refresh_from_db()
        item.refresh_from_db()
        order.refresh_from_db()

        self.assertEqual(menu_item.portions, 3)
        self.assertTrue(order.stock_reserved)
        self.assertEqual(item.stock_source_type, OrderItem.StockSourceType.DAILY_MENU_ITEM)
        self.assertEqual(item.stock_source_ref, str(menu_item.id))

        self.service.release_order_stock(order)
        menu_item.refresh_from_db()
        order.refresh_from_db()

        self.assertEqual(menu_item.portions, 5)
        self.assertFalse(order.stock_reserved)

    def test_reserve_and_release_order_stock_falls_back_to_dish(self):
        order = Order.objects.create(
            client=self.client_profile,
            chef=self.chef_profile,
            status=Order.Status.PENDING_PAYMENT,
            fulfillment_type=Order.FulfillmentType.DELIVERY,
            payment_method=Order.PaymentMethod.CASH,
            subtotal=Decimal("45.00"),
            total=Decimal("45.00"),
        )
        item = OrderItem.objects.create(
            order=order,
            dish=self.dish,
            quantity=3,
            unit_price=Decimal("15.00"),
            subtotal=Decimal("45.00"),
            dish_name_snapshot=self.dish.name,
            dish_description_snapshot=self.dish.description,
            dish_image_url_snapshot=self.dish.image_url,
        )

        self.service.reserve_order_stock(order)
        self.dish.refresh_from_db()
        item.refresh_from_db()
        order.refresh_from_db()

        self.assertEqual(self.dish.portions, 5)
        self.assertTrue(order.stock_reserved)
        self.assertEqual(item.stock_source_type, OrderItem.StockSourceType.DISH)
        self.assertEqual(item.stock_source_ref, str(self.dish.id))

        self.service.release_order_stock(order)
        self.dish.refresh_from_db()
        order.refresh_from_db()

        self.assertEqual(self.dish.portions, 8)
        self.assertFalse(order.stock_reserved)

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


class CartServiceAndApiTests(TestCase):
    def setUp(self):
        self.service = CartService()
        self.api = APIClient()
        self.client_profile = UserProfile.objects.create(
            supabase_user_id=uuid4(),
            email="client.cart@test.com",
            role=UserProfile.ROLE_CLIENT,
            first_name="Cart",
            is_active=True,
        )
        self.chef_profile = UserProfile.objects.create(
            supabase_user_id=uuid4(),
            email="chef.cart@test.com",
            role=UserProfile.ROLE_CHEF,
            first_name="Chef",
            is_active=True,
        )
        self.delivery_profile = UserProfile.objects.create(
            supabase_user_id=uuid4(),
            email="delivery.cart@test.com",
            role=UserProfile.ROLE_DELIVERY,
            first_name="Rider",
            is_active=True,
            location_latitude=-17.782,
            location_longitude=-63.182,
        )
        DeliveryProfile.objects.create(
            user=self.delivery_profile,
            vehicle_type=DeliveryProfile.VehicleType.MOTORCYCLE,
            vehicle_brand="Honda",
            vehicle_model="Wave",
            vehicle_plate="789-CCC",
            approval_status=DeliveryProfile.ApprovalStatus.ACTIVE,
            availability_manual_status=DeliveryProfile.AvailabilityManualStatus.AVAILABLE,
            availability_effective_status=DeliveryProfile.AvailabilityEffectiveStatus.AVAILABLE,
        )
        self.second_chef = UserProfile.objects.create(
            supabase_user_id=uuid4(),
            email="chef2.cart@test.com",
            role=UserProfile.ROLE_CHEF,
            first_name="Chef2",
            is_active=True,
        )
        for chef in (self.chef_profile, self.second_chef):
            ChefAvailability.objects.create(
                chef=chef,
                is_active=True,
                weekly_schedule=[self._open_slot()],
                accept_delivery=True,
                accept_pickup=True,
                simultaneous_orders_limit=10,
            )
        ChefProfile.objects.create(
            user=self.chef_profile,
            business_name="Chef",
            location_latitude=-17.781,
            location_longitude=-63.181,
            location_address="Cocina chef principal",
        )
        ChefProfile.objects.create(
            user=self.second_chef,
            business_name="Chef2",
            location_latitude=-17.79,
            location_longitude=-63.17,
            location_address="Cocina chef secundaria",
        )
        self.dish = Dish.objects.create(
            chef=self.chef_profile,
            name="Pique",
            description="Picante",
            price=Decimal("22.00"),
            portions=6,
            ingredients=["carne"],
            tags=["PICANTE"],
            allergens=[],
            status=Dish.STATUS_PUBLISHED,
        )
        self.dish_2 = Dish.objects.create(
            chef=self.second_chef,
            name="Silpancho",
            description="Clasico",
            price=Decimal("18.00"),
            portions=4,
            ingredients=["carne"],
            tags=["CLASICO"],
            allergens=[],
            status=Dish.STATUS_PUBLISHED,
        )
        self.api.force_authenticate(user=AuthenticatedProfile(self.client_profile))

    def test_cart_service_adds_and_groups_items_per_chef(self):
        self.service.add_item(str(self.client_profile.supabase_user_id), self.dish.id, 2)
        self.service.add_item(str(self.client_profile.supabase_user_id), self.dish_2.id, 1)

        summary = self.service.get_cart_summary(str(self.client_profile.supabase_user_id))

        self.assertEqual(len(summary["carts"]), 2)
        self.assertEqual(summary["summary"]["items_count"], 3)
        self.assertEqual(summary["summary"]["subtotal"], 62.0)

    def test_cart_api_full_flow(self):
        add_response = self.api.post(
            "/api/v1/orders/cart/items/",
            {"dish_id": self.dish.id, "quantity": 2},
            format="json",
        )
        self.assertEqual(add_response.status_code, 201)
        item_id = add_response.data["item"]["id"]

        get_response = self.api.get("/api/v1/orders/cart/")
        self.assertEqual(get_response.status_code, 200)
        self.assertEqual(get_response.data["summary"]["items_count"], 2)
        self.assertEqual(len(get_response.data["carts"]), 1)

        put_response = self.api.put(
            f"/api/v1/orders/cart/items/{item_id}/",
            {"quantity": 3},
            format="json",
        )
        self.assertEqual(put_response.status_code, 200)
        self.assertEqual(put_response.data["item"]["quantity"], 3)

        delete_response = self.api.delete(f"/api/v1/orders/cart/items/{item_id}/")
        self.assertEqual(delete_response.status_code, 200)
        self.assertEqual(delete_response.data["removed_item_id"], item_id)

        final_response = self.api.get("/api/v1/orders/cart/")
        self.assertEqual(final_response.status_code, 200)
        self.assertEqual(final_response.data["summary"]["items_count"], 0)
        self.assertEqual(final_response.data["carts"], [])

    def test_cart_api_rejects_quantity_over_available_portions(self):
        response = self.api.post(
            "/api/v1/orders/cart/items/",
            {"dish_id": self.dish.id, "quantity": 10},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "insufficient_portions")
        self.assertEqual(response.data["available_portions"], 6)

    def test_repeat_order_adds_all_items_to_active_cart(self):
        order = self._create_historical_order(items=[(self.dish, 2)])

        response = self.api.post(f"/api/v1/orders/my-orders/{order.id}/repeat/", {}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["summary"]["added_items"], 1)
        self.assertEqual(response.data["summary"]["skipped_items"], 0)
        self.assertEqual(response.data["cart"]["items_count"], 2)
        self.assertEqual(response.data["added_items"][0]["cart_quantity"], 2)
        self.assertTrue(
            order.timeline_events.filter(event_code="ORDER_REPEATED_TO_CART").exists()
        )

    def test_repeat_order_merges_cart_and_skips_unavailable_items(self):
        unavailable_dish = Dish.objects.create(
            chef=self.chef_profile,
            name="Sajta",
            description="Temporal",
            price=Decimal("19.00"),
            portions=4,
            ingredients=["pollo"],
            tags=["TEMPORAL"],
            allergens=[],
            status=Dish.STATUS_DRAFT,
        )
        self.service.add_item(str(self.client_profile.supabase_user_id), self.dish.id, 1)
        order = self._create_historical_order(items=[(self.dish, 2), (unavailable_dish, 1)])

        response = self.api.post(f"/api/v1/orders/my-orders/{order.id}/repeat/", {}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["summary"]["added_items"], 1)
        self.assertEqual(response.data["summary"]["skipped_items"], 1)
        self.assertEqual(response.data["added_items"][0]["cart_quantity"], 3)
        self.assertEqual(response.data["skipped_items"][0]["code"], "dish_unpublished")
        cart_item = CartItem.objects.get(
            cart__client=self.client_profile,
            cart__chef=self.chef_profile,
            dish=self.dish,
        )
        self.assertEqual(cart_item.quantity, 3)

    def test_repeat_order_returns_summary_when_everything_is_invalid(self):
        self.dish.status = Dish.STATUS_DRAFT
        self.dish.save(update_fields=["status", "updated_at"])
        order = self._create_historical_order(items=[(self.dish, 1)])

        response = self.api.post(f"/api/v1/orders/my-orders/{order.id}/repeat/", {}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["summary"]["added_items"], 0)
        self.assertEqual(response.data["summary"]["skipped_items"], 1)
        self.assertIsNone(response.data["cart"])
        self.assertIn("ninguno", response.data["message"].lower())

    def test_repeat_order_rejects_unrelated_client(self):
        order = self._create_historical_order(items=[(self.dish, 1)])
        other_client = UserProfile.objects.create(
            supabase_user_id=uuid4(),
            email="repeat.other@test.com",
            role=UserProfile.ROLE_CLIENT,
            first_name="Other",
            is_active=True,
        )
        self.api.force_authenticate(user=AuthenticatedProfile(other_client))

        response = self.api.post(f"/api/v1/orders/my-orders/{order.id}/repeat/", {}, format="json")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["code"], "order_not_found")

    def test_checkout_preview_requires_address_for_delivery(self):
        add_response = self.api.post(
            "/api/v1/orders/cart/items/",
            {"dish_id": self.dish.id, "quantity": 2},
            format="json",
        )
        cart_id = add_response.data["cart"]["id"]
        response = self.api.post(
            "/api/v1/orders/checkout/preview/",
            {
                "cart_id": cart_id,
                "fulfillment_type": "delivery",
                "payment_method": "cash",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "address_required")

    def test_checkout_preview_returns_pricing(self):
        add_response = self.api.post(
            "/api/v1/orders/cart/items/",
            {"dish_id": self.dish.id, "quantity": 2},
            format="json",
        )
        cart_id = add_response.data["cart"]["id"]
        response = self.api.post(
            "/api/v1/orders/checkout/preview/",
            {
                "cart_id": cart_id,
                "fulfillment_type": "pickup",
                "payment_method": "cash",
                "notes": "Sin cebolla",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["pricing"]["subtotal"], 44.0)
        self.assertEqual(response.data["pricing"]["delivery_fee"], 0.0)
        self.assertEqual(response.data["pricing"]["total"], 44.0)
        self.assertTrue(response.data["stock_validation"]["ok"])
        self.assertIn("cash", response.data["payment_options"])
        self.assertIn("stripe_test", response.data["payment_options"])
        self.assertIn("qr_simulado", response.data["payment_options"])
        self.assertIn("bitcoin_coingate", response.data["payment_options"])
        self.assertTrue(response.data["pickup_policy"]["available_slots"])

    def test_checkout_preview_rejects_invalid_pickup_slot(self):
        add_response = self.api.post(
            "/api/v1/orders/cart/items/",
            {"dish_id": self.dish.id, "quantity": 1},
            format="json",
        )
        cart_id = add_response.data["cart"]["id"]
        response = self.api.post(
            "/api/v1/orders/checkout/preview/",
            {
                "cart_id": cart_id,
                "fulfillment_type": "pickup",
                "payment_method": "cash",
                "pickup_slot": "2099-01-01T10:00:00-04:00",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "pickup_slot_invalid")

    def test_checkout_confirm_pickup_persists_selected_slot(self):
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
                "payment_method": "cash",
            },
            format="json",
        )
        pickup_slot = preview_response.data["pickup_policy"]["available_slots"][0]["id"]
        confirm_response = self.api.post(
            "/api/v1/orders/checkout/confirm/",
            {
                "cart_id": cart_id,
                "fulfillment_type": "pickup",
                "payment_method": "cash",
                "pickup_slot": pickup_slot,
                "expected_total": preview_response.data["pricing"]["total"],
            },
            format="json",
        )

        self.assertEqual(confirm_response.status_code, 201)
        pickup = PickupConfirmation.objects.get(order_id=confirm_response.data["order_id"])
        self.assertIsNotNone(pickup.selected_slot_start)
        self.assertIsNotNone(pickup.selected_slot_end)
        self.assertIn("Tolerancia adicional", pickup.pickup_schedule_note)

    @override_settings(
        STRIPE_SECRET_KEY="sk_test_demo",
        ORDER_STRIPE_SUCCESS_URL="https://frontend.test/client/payments/stripe/return?payment=stripe_success",
        ORDER_STRIPE_CANCEL_URL="https://frontend.test/client/payments/stripe/return?payment=stripe_cancel",
    )
    @patch("modules.pedidos_checkout_pagos.services.order_stripe_service.stripe.checkout.Session.create")
    def test_stripe_test_confirm_creates_provider_checkout(self, session_create):
        session_create.return_value = Mock(
            id="cs_test_order_001",
            url="https://checkout.stripe.com/c/pay/cs_test_order_001",
            mode="payment",
            payment_status="unpaid",
        )
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
                "payment_method": "stripe_test",
            },
            format="json",
        )
        confirm_response = self.api.post(
            "/api/v1/orders/checkout/confirm/",
            {
                "cart_id": cart_id,
                "fulfillment_type": "pickup",
                "payment_method": "stripe_test",
                "expected_total": preview_response.data["pricing"]["total"],
            },
            format="json",
        )

        self.assertEqual(confirm_response.status_code, 201)
        order = Order.objects.get(id=confirm_response.data["order_id"])
        payment = OrderPayment.objects.get(order=order)
        self.assertEqual(order.status, Order.Status.PAYMENT_VALIDATING)
        self.assertEqual(payment.method, Order.PaymentMethod.STRIPE_TEST)
        self.assertEqual(payment.provider, "STRIPE_SANDBOX")
        self.assertEqual(payment.external_reference, "cs_test_order_001")
        self.assertEqual(confirm_response.data["payment"]["payment_url"], payment.payment_url)
        self.assertTrue(order.stock_reserved)
        session_create.assert_called_once()

    @override_settings(
        STRIPE_SECRET_KEY="sk_test_demo",
        ORDER_STRIPE_SUCCESS_URL="https://frontend.test/client/payments/stripe/return?payment=stripe_success",
        ORDER_STRIPE_CANCEL_URL="https://frontend.test/client/payments/stripe/return?payment=stripe_cancel",
    )
    @patch("modules.pedidos_checkout_pagos.services.order_stripe_service.stripe.checkout.Session.retrieve")
    @patch("modules.pedidos_checkout_pagos.services.order_stripe_service.stripe.checkout.Session.create")
    def test_stripe_test_confirm_return_confirms_payment_and_order(self, session_create, session_retrieve):
        session_create.return_value = Mock(
            id="cs_test_order_002",
            url="https://checkout.stripe.com/c/pay/cs_test_order_002",
            mode="payment",
            payment_status="unpaid",
        )
        session_retrieve.return_value = Mock(
            payment_status="paid",
            status="complete",
            to_dict_recursive=Mock(
                return_value={
                    "id": "cs_test_order_002",
                    "payment_status": "paid",
                    "status": "complete",
                }
            ),
        )
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
                "payment_method": "stripe_test",
            },
            format="json",
        )
        confirm_response = self.api.post(
            "/api/v1/orders/checkout/confirm/",
            {
                "cart_id": cart_id,
                "fulfillment_type": "pickup",
                "payment_method": "stripe_test",
                "expected_total": preview_response.data["pricing"]["total"],
            },
            format="json",
        )
        order = Order.objects.get(id=confirm_response.data["order_id"])
        payment = OrderPayment.objects.get(order=order)

        return_response = self.api.post(
            "/api/v1/orders/payments/stripe/confirm-return/",
            {
                "provider": "STRIPE_SANDBOX",
                "stripe_session_id": payment.external_reference,
            },
            format="json",
        )

        self.assertEqual(return_response.status_code, 200)
        payment.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(payment.status, OrderPayment.Status.CONFIRMED)
        self.assertEqual(order.status, Order.Status.AWAITING_CHEF_CONFIRMATION)
        self.assertEqual(return_response.data["payment_status"], "CONFIRMED")
        self.assertEqual(return_response.data["order_status"], "AWAITING_CHEF_CONFIRMATION")
        session_retrieve.assert_called_once_with("cs_test_order_002")

    @override_settings(
        STRIPE_SECRET_KEY="sk_test_demo",
        ORDER_STRIPE_SUCCESS_URL="https://frontend.test/client/payments/stripe/return?payment=stripe_success",
        ORDER_STRIPE_CANCEL_URL="https://frontend.test/client/payments/stripe/return?payment=stripe_cancel",
    )
    @patch("modules.pedidos_checkout_pagos.services.order_stripe_service.stripe.checkout.Session.retrieve")
    @patch("modules.pedidos_checkout_pagos.services.order_stripe_service.stripe.checkout.Session.create")
    def test_stripe_test_return_does_not_auto_approve_when_session_is_unpaid(self, session_create, session_retrieve):
        session_create.return_value = Mock(
            id="cs_test_order_003",
            url="https://checkout.stripe.com/c/pay/cs_test_order_003",
            mode="payment",
            payment_status="unpaid",
        )
        session_retrieve.return_value = Mock(
            payment_status="unpaid",
            status="open",
            to_dict_recursive=Mock(
                return_value={
                    "id": "cs_test_order_003",
                    "payment_status": "unpaid",
                    "status": "open",
                }
            ),
        )
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
                "payment_method": "stripe_test",
            },
            format="json",
        )
        confirm_response = self.api.post(
            "/api/v1/orders/checkout/confirm/",
            {
                "cart_id": cart_id,
                "fulfillment_type": "pickup",
                "payment_method": "stripe_test",
                "expected_total": preview_response.data["pricing"]["total"],
            },
            format="json",
        )
        order = Order.objects.get(id=confirm_response.data["order_id"])
        payment = OrderPayment.objects.get(order=order)

        return_response = self.api.post(
            "/api/v1/orders/payments/stripe/confirm-return/",
            {
                "provider": "STRIPE_SANDBOX",
                "stripe_session_id": payment.external_reference,
            },
            format="json",
        )

        self.assertEqual(return_response.status_code, 200)
        payment.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(payment.status, OrderPayment.Status.PENDING)
        self.assertEqual(order.status, Order.Status.PAYMENT_VALIDATING)
        self.assertEqual(return_response.data["payment_status"], "PENDING")
        self.assertEqual(return_response.data["order_status"], "PAYMENT_VALIDATING")
        session_retrieve.assert_called_once_with("cs_test_order_003")

    def test_checkout_route_preview_returns_chef_and_route_payload(self):
        add_response = self.api.post(
            "/api/v1/orders/cart/items/",
            {"dish_id": self.dish.id, "quantity": 1},
            format="json",
        )
        cart_id = add_response.data["cart"]["id"]
        response = self.api.post(
            "/api/v1/orders/checkout/route-preview/",
            {
                "cart_id": cart_id,
                "latitude": -17.786,
                "longitude": -63.179,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["chef"]["lat"], -17.781)
        self.assertEqual(response.data["chef"]["lng"], -63.181)
        self.assertEqual(response.data["navigation"]["mode"], "walking")
        self.assertGreaterEqual(len(response.data["route"]["polyline"]), 2)

    def test_checkout_confirm_creates_order_and_converts_cart(self):
        add_response = self.api.post(
            "/api/v1/orders/cart/items/",
            {"dish_id": self.dish.id, "quantity": 2},
            format="json",
        )
        cart_id = add_response.data["cart"]["id"]
        preview_response = self.api.post(
            "/api/v1/orders/checkout/preview/",
            {
                "cart_id": cart_id,
                "fulfillment_type": "delivery",
                "payment_method": "cash",
                "address": {
                    "label": "Casa",
                    "contact_name": "Juan",
                    "contact_phone": "77777777",
                    "line_1": "Av Siempre Viva 123",
                    "reference": "Porton negro",
                    "latitude": -17.78,
                    "longitude": -63.18,
                },
                "notes": "Llamar al llegar",
            },
            format="json",
        )
        total = preview_response.data["pricing"]["total"]
        confirm_response = self.api.post(
            "/api/v1/orders/checkout/confirm/",
            {
                "cart_id": cart_id,
                "fulfillment_type": "delivery",
                "payment_method": "cash",
                "expected_total": total,
                "address": {
                    "label": "Casa",
                    "contact_name": "Juan",
                    "contact_phone": "77777777",
                    "line_1": "Av Siempre Viva 123",
                    "reference": "Porton negro",
                    "latitude": -17.78,
                    "longitude": -63.18,
                },
                "notes": "Llamar al llegar",
            },
            format="json",
        )
        self.assertEqual(confirm_response.status_code, 201)
        order = Order.objects.get(id=confirm_response.data["order_id"])
        payment = OrderPayment.objects.get(order=order)
        cart = Cart.objects.get(id=cart_id)

        self.assertEqual(order.status, Order.Status.AWAITING_CHEF_CONFIRMATION)
        self.assertEqual(order.fulfillment_type, Order.FulfillmentType.DELIVERY)
        self.assertTrue(order.stock_reserved)
        self.assertEqual(float(order.total), float(total))
        self.assertEqual(payment.status, OrderPayment.Status.PENDING)
        self.assertEqual(cart.status, Cart.Status.CONVERTED)
        self.assertEqual(str(cart.converted_to_order_id), order.id)
        self.assertEqual(order.items.count(), 1)
        self.assertEqual(OrderStatusHistory.objects.filter(order=order).count(), 3)
        self.assertEqual(OrderPaymentEvent.objects.filter(payment=payment).count(), 1)
        self.assertIsNotNone(order.address)

    def test_cash_pickup_flow_for_chef_marks_payment_confirmed(self):
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
                "payment_method": "cash",
            },
            format="json",
        )
        confirm_response = self.api.post(
            "/api/v1/orders/checkout/confirm/",
            {
                "cart_id": cart_id,
                "fulfillment_type": "pickup",
                "payment_method": "cash",
                "expected_total": preview_response.data["pricing"]["total"],
            },
            format="json",
        )
        order_id = confirm_response.data["order_id"]
        pickup = PickupConfirmation.objects.get(order_id=order_id)

        self.api.force_authenticate(user=AuthenticatedProfile(self.chef_profile))
        list_response = self.api.get("/api/v1/orders/chef/orders/")
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.data["items"][0]["payment"]["status"], "PENDING")
        self.assertEqual(list_response.data["items"][0]["available_actions"], ["accept", "reject"])

        accept_response = self.api.post(f"/api/v1/orders/chef/orders/{order_id}/accept/", {}, format="json")
        self.assertEqual(accept_response.status_code, 200)
        preparing_response = self.api.post(f"/api/v1/orders/chef/orders/{order_id}/preparing/", {}, format="json")
        self.assertEqual(preparing_response.status_code, 200)
        ready_response = self.api.post(f"/api/v1/orders/chef/orders/{order_id}/ready/", {}, format="json")
        self.assertEqual(ready_response.status_code, 200)
        ready_detail_response = self.api.get(f"/api/v1/orders/chef/orders/{order_id}/")
        self.assertEqual(ready_detail_response.status_code, 200)
        self.assertEqual(
            ready_detail_response.data["order"]["available_actions"],
            ["confirm_pickup", "mark_pickup_no_show"],
        )
        self.assertEqual(ready_detail_response.data["order"]["pickup"]["pickup_code"], pickup.pickup_code)
        cash_response = self.api.post(
            f"/api/v1/orders/chef/orders/{order_id}/pickup/confirm/",
            {"pickup_code": pickup.pickup_code},
            format="json",
        )
        self.assertEqual(cash_response.status_code, 200)

        order = Order.objects.get(id=order_id)
        payment = OrderPayment.objects.get(order=order)
        pickup.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PICKED_UP)
        self.assertEqual(payment.status, OrderPayment.Status.CONFIRMED)
        self.assertEqual(payment.confirmed_by_role, UserProfile.ROLE_CHEF)
        self.assertIsNotNone(payment.confirmed_at)
        self.assertEqual(pickup.status, PickupConfirmation.Status.CONFIRMED)
        self.assertEqual(pickup.confirmed_by_role, "COCINERO")
        self.assertIsNotNone(pickup.confirmed_at)

        self.api.force_authenticate(user=AuthenticatedProfile(self.client_profile))
        my_orders_response = self.api.get("/api/v1/orders/my-orders/")
        self.assertEqual(my_orders_response.status_code, 200)
        self.assertEqual(my_orders_response.data["items"][0]["payment"]["status"], "CONFIRMED")
        self.assertEqual(my_orders_response.data["items"][0]["status"], "PICKED_UP")
        self.assertEqual(my_orders_response.data["items"][0]["pickup"]["pickup_code"], pickup.pickup_code)

    def test_pickup_confirmation_rejects_invalid_code(self):
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
                "payment_method": "cash",
            },
            format="json",
        )
        confirm_response = self.api.post(
            "/api/v1/orders/checkout/confirm/",
            {
                "cart_id": cart_id,
                "fulfillment_type": "pickup",
                "payment_method": "cash",
                "expected_total": preview_response.data["pricing"]["total"],
            },
            format="json",
        )
        order_id = confirm_response.data["order_id"]

        self.api.force_authenticate(user=AuthenticatedProfile(self.chef_profile))
        self.assertEqual(self.api.post(f"/api/v1/orders/chef/orders/{order_id}/accept/", {}, format="json").status_code, 200)
        self.assertEqual(self.api.post(f"/api/v1/orders/chef/orders/{order_id}/preparing/", {}, format="json").status_code, 200)
        self.assertEqual(self.api.post(f"/api/v1/orders/chef/orders/{order_id}/ready/", {}, format="json").status_code, 200)

        invalid_response = self.api.post(
            f"/api/v1/orders/chef/orders/{order_id}/pickup/confirm/",
            {"pickup_code": "000000"},
            format="json",
        )
        self.assertEqual(invalid_response.status_code, 400)
        self.assertEqual(invalid_response.data["code"], "pickup_code_invalid")

    def test_non_cash_pickup_can_close_with_pickup_code(self):
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
        order = Order.objects.get(id=confirm_response.data["order_id"])
        payment = OrderPayment.objects.get(order=order)
        session = SimulatedQRPaymentSession.objects.get(payment=payment)
        pickup = PickupConfirmation.objects.get(order=order)

        self.api.post(f"/api/v1/orders/payments/qr-sessions/{session.session_code}/start/", {}, format="json")
        with patch("modules.pedidos_checkout_pagos.services.qr_payment_service.sleep", return_value=None):
            confirm_payment_response = self.api.post(f"/api/v1/orders/payments/qr-sessions/{session.session_code}/confirm/", {}, format="json")
        self.assertEqual(confirm_payment_response.status_code, 200)

        self.api.force_authenticate(user=AuthenticatedProfile(self.chef_profile))
        self.assertEqual(self.api.post(f"/api/v1/orders/chef/orders/{order.id}/accept/", {}, format="json").status_code, 200)
        self.assertEqual(self.api.post(f"/api/v1/orders/chef/orders/{order.id}/preparing/", {}, format="json").status_code, 200)
        self.assertEqual(self.api.post(f"/api/v1/orders/chef/orders/{order.id}/ready/", {}, format="json").status_code, 200)
        pickup_response = self.api.post(
            f"/api/v1/orders/chef/orders/{order.id}/pickup/confirm/",
            {"pickup_code": pickup.pickup_code},
            format="json",
        )
        self.assertEqual(pickup_response.status_code, 200)

        order.refresh_from_db()
        payment.refresh_from_db()
        pickup.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PICKED_UP)
        self.assertEqual(payment.status, OrderPayment.Status.CONFIRMED)
        self.assertEqual(pickup.status, PickupConfirmation.Status.CONFIRMED)

    def test_chef_order_detail_returns_timeline_and_reject_action(self):
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
                "payment_method": "cash",
            },
            format="json",
        )
        confirm_response = self.api.post(
            "/api/v1/orders/checkout/confirm/",
            {
                "cart_id": cart_id,
                "fulfillment_type": "pickup",
                "payment_method": "cash",
                "expected_total": preview_response.data["pricing"]["total"],
            },
            format="json",
        )
        order_id = confirm_response.data["order_id"]

        self.api.force_authenticate(user=AuthenticatedProfile(self.chef_profile))
        detail_response = self.api.get(f"/api/v1/orders/chef/orders/{order_id}/")
        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(detail_response.data["order"]["id"], order_id)
        self.assertIn("reject", detail_response.data["order"]["available_actions"])
        self.assertGreaterEqual(len(detail_response.data["order"]["timeline"]), 3)
        self.assertTrue(detail_response.data["order"]["pickup"]["pickup_code"])

    def test_client_order_tracking_returns_structured_progress(self):
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
                "fulfillment_type": "delivery",
                "payment_method": "cash",
                "address": {
                    "label": "Casa",
                    "contact_name": "Juan",
                    "contact_phone": "70000000",
                    "line_1": "Av Siempre Viva 123",
                    "reference": "Porton negro",
                },
            },
            format="json",
        )
        confirm_response = self.api.post(
            "/api/v1/orders/checkout/confirm/",
            {
                "cart_id": cart_id,
                "fulfillment_type": "delivery",
                "payment_method": "cash",
                "expected_total": preview_response.data["pricing"]["total"],
                "address": {
                    "label": "Casa",
                    "contact_name": "Juan",
                    "contact_phone": "70000000",
                    "line_1": "Av Siempre Viva 123",
                    "reference": "Porton negro",
                },
            },
            format="json",
        )

        order_id = confirm_response.data["order_id"]
        tracking_response = self.api.get(f"/api/v1/orders/my-orders/{order_id}/tracking/")

        self.assertEqual(tracking_response.status_code, 200)
        self.assertEqual(tracking_response.data["order_id"], order_id)
        self.assertEqual(tracking_response.data["viewer_role"], "CLIENTE")
        self.assertEqual(tracking_response.data["tracking_mode"], "textual")
        self.assertFalse(tracking_response.data["gps_enabled"])
        self.assertFalse(tracking_response.data["map_enabled"])
        self.assertEqual(tracking_response.data["status"], Order.Status.AWAITING_CHEF_CONFIRMATION)
        self.assertEqual(tracking_response.data["status_label"], "Esperando al cocinero")
        self.assertEqual(tracking_response.data["fulfillment_label"], "Delivery")
        self.assertGreaterEqual(len(tracking_response.data["steps"]), 6)
        self.assertGreaterEqual(len(tracking_response.data["timeline"]), 3)
        self.assertIn("espera respuesta del cocinero", tracking_response.data["summary"])

    def test_chef_order_tracking_advances_with_preparation_flow(self):
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
                "payment_method": "cash",
            },
            format="json",
        )
        confirm_response = self.api.post(
            "/api/v1/orders/checkout/confirm/",
            {
                "cart_id": cart_id,
                "fulfillment_type": "pickup",
                "payment_method": "cash",
                "expected_total": preview_response.data["pricing"]["total"],
            },
            format="json",
        )
        order_id = confirm_response.data["order_id"]

        self.api.force_authenticate(user=AuthenticatedProfile(self.chef_profile))
        self.assertEqual(self.api.post(f"/api/v1/orders/chef/orders/{order_id}/accept/", {}, format="json").status_code, 200)
        self.assertEqual(self.api.post(f"/api/v1/orders/chef/orders/{order_id}/preparing/", {}, format="json").status_code, 200)

        tracking_response = self.api.get(f"/api/v1/orders/chef/orders/{order_id}/tracking/")

        self.assertEqual(tracking_response.status_code, 200)
        self.assertEqual(tracking_response.data["order_id"], order_id)
        self.assertEqual(tracking_response.data["viewer_role"], "COCINERO")
        self.assertEqual(tracking_response.data["status"], Order.Status.PREPARING)
        self.assertEqual(tracking_response.data["status_label"], "En preparacion")
        self.assertEqual(tracking_response.data["current_step"]["status"], Order.Status.PREPARING)
        self.assertEqual(tracking_response.data["progress"]["current_index"], 3)
        self.assertEqual(tracking_response.data["progress"]["percent"], 66)
        self.assertEqual(tracking_response.data["participants"]["chef_name"], "Chef")
        self.assertGreaterEqual(len(tracking_response.data["timeline"]), 5)

    def test_chef_reject_releases_stock_and_cancels_pending_cash_payment(self):
        add_response = self.api.post(
            "/api/v1/orders/cart/items/",
            {"dish_id": self.dish.id, "quantity": 2},
            format="json",
        )
        cart_id = add_response.data["cart"]["id"]
        preview_response = self.api.post(
            "/api/v1/orders/checkout/preview/",
            {
                "cart_id": cart_id,
                "fulfillment_type": "pickup",
                "payment_method": "cash",
            },
            format="json",
        )
        confirm_response = self.api.post(
            "/api/v1/orders/checkout/confirm/",
            {
                "cart_id": cart_id,
                "fulfillment_type": "pickup",
                "payment_method": "cash",
                "expected_total": preview_response.data["pricing"]["total"],
            },
            format="json",
        )
        order_id = confirm_response.data["order_id"]
        self.assertEqual(Dish.objects.get(id=self.dish.id).portions, 4)

        self.api.force_authenticate(user=AuthenticatedProfile(self.chef_profile))
        reject_response = self.api.post(f"/api/v1/orders/chef/orders/{order_id}/reject/", {}, format="json")
        self.assertEqual(reject_response.status_code, 200)

        order = Order.objects.get(id=order_id)
        payment = OrderPayment.objects.get(order=order)
        pickup = PickupConfirmation.objects.get(order=order)
        self.dish.refresh_from_db()
        self.assertEqual(order.status, Order.Status.REJECTED)
        self.assertEqual(payment.status, OrderPayment.Status.CANCELLED)
        self.assertEqual(self.dish.portions, 6)
        self.assertFalse(order.stock_reserved)
        self.assertEqual(pickup.status, PickupConfirmation.Status.CANCELLED)

    def test_chef_cannot_reject_after_preparing_started(self):
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
                "payment_method": "cash",
            },
            format="json",
        )
        confirm_response = self.api.post(
            "/api/v1/orders/checkout/confirm/",
            {
                "cart_id": cart_id,
                "fulfillment_type": "pickup",
                "payment_method": "cash",
                "expected_total": preview_response.data["pricing"]["total"],
            },
            format="json",
        )
        order_id = confirm_response.data["order_id"]

        self.api.force_authenticate(user=AuthenticatedProfile(self.chef_profile))
        self.assertEqual(self.api.post(f"/api/v1/orders/chef/orders/{order_id}/accept/", {}, format="json").status_code, 200)
        self.assertEqual(self.api.post(f"/api/v1/orders/chef/orders/{order_id}/preparing/", {}, format="json").status_code, 200)
        reject_response = self.api.post(f"/api/v1/orders/chef/orders/{order_id}/reject/", {}, format="json")
        self.assertEqual(reject_response.status_code, 409)
        self.assertEqual(reject_response.data["code"], "transition_not_allowed")

    def test_cash_delivery_flow_for_delivery_confirms_payment(self):
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
                "fulfillment_type": "delivery",
                "payment_method": "cash",
                "address": {
                    "label": "Casa",
                    "contact_name": "Maria",
                    "contact_phone": "70000000",
                    "line_1": "Calle 1",
                    "reference": "Puerta azul",
                },
            },
            format="json",
        )
        confirm_response = self.api.post(
            "/api/v1/orders/checkout/confirm/",
            {
                "cart_id": cart_id,
                "fulfillment_type": "delivery",
                "payment_method": "cash",
                "expected_total": preview_response.data["pricing"]["total"],
                "address": {
                    "label": "Casa",
                    "contact_name": "Maria",
                    "contact_phone": "70000000",
                    "line_1": "Calle 1",
                    "reference": "Puerta azul",
                },
            },
            format="json",
        )
        order_id = confirm_response.data["order_id"]

        self.api.force_authenticate(user=AuthenticatedProfile(self.chef_profile))
        self.assertEqual(self.api.post(f"/api/v1/orders/chef/orders/{order_id}/accept/", {}, format="json").status_code, 200)
        self.assertEqual(self.api.post(f"/api/v1/orders/chef/orders/{order_id}/preparing/", {}, format="json").status_code, 200)
        self.assertEqual(self.api.post(f"/api/v1/orders/chef/orders/{order_id}/ready/", {}, format="json").status_code, 200)

        self.api.force_authenticate(user=AuthenticatedProfile(self.delivery_profile))
        delivery_list_response = self.api.get("/api/v1/orders/delivery/cash-orders/")
        self.assertEqual(delivery_list_response.status_code, 200)
        self.assertEqual(len(delivery_list_response.data["available_items"]), 1)
        self.assertEqual(delivery_list_response.data["available_items"][0]["available_actions"], ["start_delivery"])

        start_response = self.api.post(f"/api/v1/orders/delivery/orders/{order_id}/start/", {}, format="json")
        self.assertEqual(start_response.status_code, 200)
        cash_response = self.api.post(f"/api/v1/orders/delivery/orders/{order_id}/confirm-cash/", {}, format="json")
        self.assertEqual(cash_response.status_code, 200)

        order = Order.objects.get(id=order_id)
        payment = OrderPayment.objects.get(order=order)
        self.assertEqual(order.status, Order.Status.DELIVERED)
        self.assertEqual(payment.status, OrderPayment.Status.CONFIRMED)
        self.assertEqual(payment.confirmed_by_role, UserProfile.ROLE_DELIVERY)

        self.api.force_authenticate(user=AuthenticatedProfile(self.client_profile))
        my_orders_response = self.api.get("/api/v1/orders/my-orders/")
        self.assertEqual(my_orders_response.status_code, 200)
        self.assertEqual(my_orders_response.data["items"][0]["payment"]["status"], "CONFIRMED")
        self.assertEqual(my_orders_response.data["items"][0]["status"], "DELIVERED")

    def test_delivery_assignment_is_created_on_delivery_order_confirm(self):
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
                "fulfillment_type": "delivery",
                "payment_method": "cash",
                "address": {
                    "label": "Casa",
                    "contact_name": "Maria",
                    "contact_phone": "70000000",
                    "line_1": "Calle 1",
                    "reference": "Puerta azul",
                },
            },
            format="json",
        )
        confirm_response = self.api.post(
            "/api/v1/orders/checkout/confirm/",
            {
                "cart_id": cart_id,
                "fulfillment_type": "delivery",
                "payment_method": "cash",
                "expected_total": preview_response.data["pricing"]["total"],
                "address": {
                    "label": "Casa",
                    "contact_name": "Maria",
                    "contact_phone": "70000000",
                    "line_1": "Calle 1",
                    "reference": "Puerta azul",
                },
            },
            format="json",
        )

        order = Order.objects.get(id=confirm_response.data["order_id"])
        assignment = DeliveryAssignment.objects.get(order=order)

        self.assertEqual(assignment.status, DeliveryAssignment.Status.UNASSIGNED)
        self.assertIsNone(assignment.delivery_user)
        self.assertEqual(
            DeliveryStatusHistory.objects.filter(assignment=assignment).count(),
            1,
        )

        detail_response = self.api.get(f"/api/v1/orders/my-orders/{order.id}/")
        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(
            detail_response.data["order"]["delivery"]["status"],
            DeliveryAssignment.Status.UNASSIGNED,
        )
        self.assertEqual(
            detail_response.data["order"]["delivery"]["status_label"],
            "Sin repartidor asignado",
        )

    def test_delivery_assignment_progresses_during_cash_delivery_flow(self):
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
                "fulfillment_type": "delivery",
                "payment_method": "cash",
                "address": {
                    "label": "Casa",
                    "contact_name": "Maria",
                    "contact_phone": "70000000",
                    "line_1": "Calle 1",
                    "reference": "Puerta azul",
                },
            },
            format="json",
        )
        confirm_response = self.api.post(
            "/api/v1/orders/checkout/confirm/",
            {
                "cart_id": cart_id,
                "fulfillment_type": "delivery",
                "payment_method": "cash",
                "expected_total": preview_response.data["pricing"]["total"],
                "address": {
                    "label": "Casa",
                    "contact_name": "Maria",
                    "contact_phone": "70000000",
                    "line_1": "Calle 1",
                    "reference": "Puerta azul",
                },
            },
            format="json",
        )
        order_id = confirm_response.data["order_id"]

        self.api.force_authenticate(user=AuthenticatedProfile(self.chef_profile))
        self.assertEqual(self.api.post(f"/api/v1/orders/chef/orders/{order_id}/accept/", {}, format="json").status_code, 200)
        self.assertEqual(self.api.post(f"/api/v1/orders/chef/orders/{order_id}/preparing/", {}, format="json").status_code, 200)
        ready_response = self.api.post(f"/api/v1/orders/chef/orders/{order_id}/ready/", {}, format="json")
        self.assertEqual(ready_response.status_code, 200)
        self.assertEqual(
            ready_response.data["order"]["delivery"]["status"],
            DeliveryAssignment.Status.UNASSIGNED,
        )

        self.api.force_authenticate(user=AuthenticatedProfile(self.delivery_profile))
        start_response = self.api.post(f"/api/v1/orders/delivery/orders/{order_id}/start/", {}, format="json")
        self.assertEqual(start_response.status_code, 200)
        self.assertEqual(
            start_response.data["order"]["delivery"]["status"],
            DeliveryAssignment.Status.EN_ROUTE_TO_CLIENT,
        )
        self.assertEqual(
            start_response.data["order"]["delivery"]["delivery_user_id"],
            str(self.delivery_profile.supabase_user_id),
        )

        deliver_response = self.api.post(f"/api/v1/orders/delivery/orders/{order_id}/confirm-cash/", {}, format="json")
        self.assertEqual(deliver_response.status_code, 200)
        self.assertEqual(
            deliver_response.data["order"]["delivery"]["status"],
            DeliveryAssignment.Status.DELIVERED,
        )

        assignment = DeliveryAssignment.objects.get(order_id=order_id)
        self.assertEqual(assignment.status, DeliveryAssignment.Status.DELIVERED)

    def test_delivery_tracking_exposes_map_for_client_and_chef(self):
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
                "fulfillment_type": "delivery",
                "payment_method": "cash",
                "address": {
                    "label": "Casa",
                    "contact_name": "Maria",
                    "contact_phone": "70000000",
                    "line_1": "Calle 1",
                    "reference": "Puerta azul",
                    "latitude": -17.78,
                    "longitude": -63.18,
                },
            },
            format="json",
        )
        confirm_response = self.api.post(
            "/api/v1/orders/checkout/confirm/",
            {
                "cart_id": cart_id,
                "fulfillment_type": "delivery",
                "payment_method": "cash",
                "expected_total": preview_response.data["pricing"]["total"],
                "address": {
                    "label": "Casa",
                    "contact_name": "Maria",
                    "contact_phone": "70000000",
                    "line_1": "Calle 1",
                    "reference": "Puerta azul",
                    "latitude": -17.78,
                    "longitude": -63.18,
                },
            },
            format="json",
        )
        order_id = confirm_response.data["order_id"]

        self.api.force_authenticate(user=AuthenticatedProfile(self.chef_profile))
        self.assertEqual(self.api.post(f"/api/v1/orders/chef/orders/{order_id}/accept/", {}, format="json").status_code, 200)
        self.assertEqual(self.api.post(f"/api/v1/orders/chef/orders/{order_id}/preparing/", {}, format="json").status_code, 200)
        self.assertEqual(self.api.post(f"/api/v1/orders/chef/orders/{order_id}/ready/", {}, format="json").status_code, 200)

        assignment = DeliveryAssignment.objects.get(order_id=order_id)
        self.api.force_authenticate(user=AuthenticatedProfile(self.delivery_profile))
        self.assertEqual(self.api.post(f"/api/v1/logistics/delivery/{assignment.id}/accept/", {}, format="json").status_code, 200)
        ping_response = self.api.post(
            f"/api/v1/logistics/delivery/{assignment.id}/location-pings/",
            {
                "latitude": -17.784,
                "longitude": -63.179,
                "accuracy_meters": 5,
            },
            format="json",
        )
        self.assertEqual(ping_response.status_code, 201)
        self.assertEqual(self.api.post(f"/api/v1/logistics/delivery/{assignment.id}/picked-up/", {}, format="json").status_code, 200)

        self.api.force_authenticate(user=AuthenticatedProfile(self.client_profile))
        client_tracking = self.api.get(f"/api/v1/orders/my-orders/{order_id}/tracking/")
        self.assertEqual(client_tracking.status_code, 200)
        self.assertEqual(client_tracking.data["tracking_mode"], "geospatial")
        self.assertTrue(client_tracking.data["gps_enabled"])
        self.assertTrue(client_tracking.data["map_enabled"])
        self.assertEqual(client_tracking.data["map"]["route"]["route_kind"], DeliveryRouteSnapshot.RouteKind.TO_CLIENT)
        self.assertGreaterEqual(len(client_tracking.data["map"]["route"]["polyline"]), 2)

        self.api.force_authenticate(user=AuthenticatedProfile(self.chef_profile))
        chef_tracking = self.api.get(f"/api/v1/orders/chef/orders/{order_id}/tracking/")
        self.assertEqual(chef_tracking.status_code, 200)
        self.assertTrue(chef_tracking.data["map_enabled"])
        self.assertTrue(chef_tracking.data["delivery"]["current_location"]["lat"])
        assignment.refresh_from_db()
        self.assertEqual(assignment.delivery_user, self.delivery_profile)
        self.assertIsNotNone(assignment.assigned_at)
        self.assertIsNotNone(assignment.picked_up_at)

    def test_client_reports_delivery_incident_and_chef_resolves_it(self):
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
                "fulfillment_type": "delivery",
                "payment_method": "cash",
                "address": {
                    "label": "Casa",
                    "contact_name": "Maria",
                    "contact_phone": "70000000",
                    "line_1": "Calle 1",
                    "reference": "Puerta azul",
                },
            },
            format="json",
        )
        confirm_response = self.api.post(
            "/api/v1/orders/checkout/confirm/",
            {
                "cart_id": cart_id,
                "fulfillment_type": "delivery",
                "payment_method": "cash",
                "expected_total": preview_response.data["pricing"]["total"],
                "address": {
                    "label": "Casa",
                    "contact_name": "Maria",
                    "contact_phone": "70000000",
                    "line_1": "Calle 1",
                    "reference": "Puerta azul",
                },
            },
            format="json",
        )
        order_id = confirm_response.data["order_id"]

        self.api.force_authenticate(user=AuthenticatedProfile(self.chef_profile))
        self.assertEqual(self.api.post(f"/api/v1/orders/chef/orders/{order_id}/accept/", {}, format="json").status_code, 200)
        self.assertEqual(self.api.post(f"/api/v1/orders/chef/orders/{order_id}/preparing/", {}, format="json").status_code, 200)
        self.assertEqual(self.api.post(f"/api/v1/orders/chef/orders/{order_id}/ready/", {}, format="json").status_code, 200)

        assignment = DeliveryAssignment.objects.get(order_id=order_id)
        assignment.delivery_user = self.delivery_profile
        assignment.status = DeliveryAssignment.Status.PICKED_UP
        assignment.save(update_fields=["delivery_user", "status", "updated_at"])

        self.api.force_authenticate(user=AuthenticatedProfile(self.client_profile))
        create_response = self.api.post(
            f"/api/v1/orders/my-orders/{order_id}/incidents/",
            {
                "code": DeliveryIncident.Code.WRONG_ADDRESS,
                "description": "La referencia no coincide con el punto real.",
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, 201)
        self.assertEqual(create_response.data["incidents"]["blocking_open_count"], 1)
        incident_id = create_response.data["incident"]["id"]

        client_tracking = self.api.get(f"/api/v1/orders/my-orders/{order_id}/tracking/")
        self.assertEqual(client_tracking.status_code, 200)
        self.assertEqual(client_tracking.data["incidents"]["open_count"], 1)
        self.assertTrue(client_tracking.data["incidents"]["delivery_blocked"])
        self.assertIn("bloquea el cierre operativo", client_tracking.data["summary"])

        self.api.force_authenticate(user=AuthenticatedProfile(self.chef_profile))
        chef_incidents = self.api.get(f"/api/v1/orders/chef/orders/{order_id}/incidents/")
        self.assertEqual(chef_incidents.status_code, 200)
        self.assertEqual(chef_incidents.data["items"][0]["code"], DeliveryIncident.Code.WRONG_ADDRESS)

        resolve_response = self.api.post(
            f"/api/v1/orders/chef/orders/{order_id}/incidents/{incident_id}/resolve/",
            {"resolution_notes": "Se actualizo la referencia y el delivery continuo."},
            format="json",
        )
        self.assertEqual(resolve_response.status_code, 200)
        self.assertEqual(resolve_response.data["incident"]["status"], DeliveryIncident.Status.RESOLVED)

        chef_tracking = self.api.get(f"/api/v1/orders/chef/orders/{order_id}/tracking/")
        self.assertEqual(chef_tracking.status_code, 200)
        self.assertEqual(chef_tracking.data["incidents"]["open_count"], 0)

    def test_qr_simulado_confirm_flow_marks_order_paid_and_ready_for_chef(self):
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
        self.assertEqual(preview_response.status_code, 200)
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
        self.assertEqual(confirm_response.status_code, 201)
        order = Order.objects.get(id=confirm_response.data["order_id"])
        payment = OrderPayment.objects.get(order=order)
        session = SimulatedQRPaymentSession.objects.get(payment=payment)

        self.assertEqual(order.status, Order.Status.PAYMENT_VALIDATING)
        self.assertEqual(payment.method, Order.PaymentMethod.QR_SIMULATED)
        self.assertEqual(payment.status, OrderPayment.Status.PENDING)
        self.assertTrue(payment.payment_url.endswith(session.session_code))

        session_response = self.api.get(f"/api/v1/orders/payments/qr-sessions/{session.session_code}/")
        self.assertEqual(session_response.status_code, 200)
        self.assertEqual(session_response.data["status"], "PENDING")

        start_response = self.api.post(f"/api/v1/orders/payments/qr-sessions/{session.session_code}/start/", {}, format="json")
        self.assertEqual(start_response.status_code, 200)
        with patch("modules.pedidos_checkout_pagos.services.qr_payment_service.sleep", return_value=None):
            confirm_payment_response = self.api.post(f"/api/v1/orders/payments/qr-sessions/{session.session_code}/confirm/", {}, format="json")
        self.assertEqual(confirm_payment_response.status_code, 200)

        order.refresh_from_db()
        payment.refresh_from_db()
        session.refresh_from_db()
        self.assertEqual(session.status, SimulatedQRPaymentSession.Status.CONFIRMED)
        self.assertEqual(payment.status, OrderPayment.Status.CONFIRMED)
        self.assertEqual(order.status, Order.Status.AWAITING_CHEF_CONFIRMATION)

        duplicate_confirm_response = self.api.post(f"/api/v1/orders/payments/qr-sessions/{session.session_code}/confirm/", {}, format="json")
        self.assertEqual(duplicate_confirm_response.status_code, 409)

    def test_qr_simulado_cancel_releases_stock_and_cancels_order(self):
        add_response = self.api.post(
            "/api/v1/orders/cart/items/",
            {"dish_id": self.dish.id, "quantity": 2},
            format="json",
        )
        cart_id = add_response.data["cart"]["id"]
        preview_response = self.api.post(
            "/api/v1/orders/checkout/preview/",
            {
                "cart_id": cart_id,
                "fulfillment_type": "delivery",
                "payment_method": "qr_simulado",
                "address": {
                    "label": "Casa",
                    "contact_name": "Juan",
                    "contact_phone": "77777777",
                    "line_1": "Av Siempre Viva 123",
                },
            },
            format="json",
        )
        confirm_response = self.api.post(
            "/api/v1/orders/checkout/confirm/",
            {
                "cart_id": cart_id,
                "fulfillment_type": "delivery",
                "payment_method": "qr_simulado",
                "expected_total": preview_response.data["pricing"]["total"],
                "address": {
                    "label": "Casa",
                    "contact_name": "Juan",
                    "contact_phone": "77777777",
                    "line_1": "Av Siempre Viva 123",
                },
            },
            format="json",
        )
        order = Order.objects.get(id=confirm_response.data["order_id"])
        payment = OrderPayment.objects.get(order=order)
        session = SimulatedQRPaymentSession.objects.get(payment=payment)

        remaining_after_reserve = Dish.objects.get(id=self.dish.id).portions
        self.assertEqual(remaining_after_reserve, 4)

        cancel_response = self.api.post(f"/api/v1/orders/payments/qr-sessions/{session.session_code}/cancel/", {}, format="json")
        self.assertEqual(cancel_response.status_code, 200)

        order.refresh_from_db()
        payment.refresh_from_db()
        session.refresh_from_db()
        self.dish.refresh_from_db()
        self.assertEqual(order.status, Order.Status.CANCELLED)
        self.assertEqual(payment.status, OrderPayment.Status.CANCELLED)
        self.assertEqual(session.status, SimulatedQRPaymentSession.Status.CANCELLED)
        self.assertEqual(self.dish.portions, 6)

    def test_client_order_detail_returns_timeline_and_cancel_action(self):
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
                "payment_method": "cash",
            },
            format="json",
        )
        confirm_response = self.api.post(
            "/api/v1/orders/checkout/confirm/",
            {
                "cart_id": cart_id,
                "fulfillment_type": "pickup",
                "payment_method": "cash",
                "expected_total": preview_response.data["pricing"]["total"],
            },
            format="json",
        )

        detail_response = self.api.get(f"/api/v1/orders/my-orders/{confirm_response.data['order_id']}/")
        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(detail_response.data["order"]["id"], confirm_response.data["order_id"])
        self.assertTrue(detail_response.data["order"]["can_cancel"])
        self.assertIn("cancel", detail_response.data["order"]["available_actions"])
        self.assertGreaterEqual(len(detail_response.data["order"]["timeline"]), 3)
        self.assertTrue(detail_response.data["order"]["pickup"]["pickup_code"])
        self.assertIn("Presenta este codigo", detail_response.data["order"]["pickup"]["pickup_instructions"])

    def test_chef_order_detail_allows_read_without_transaction_lock(self):
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
                "payment_method": "cash",
            },
            format="json",
        )
        confirm_response = self.api.post(
            "/api/v1/orders/checkout/confirm/",
            {
                "cart_id": cart_id,
                "fulfillment_type": "pickup",
                "payment_method": "cash",
                "expected_total": preview_response.data["pricing"]["total"],
            },
            format="json",
        )
        order_id = confirm_response.data["order_id"]

        self.api.force_authenticate(user=AuthenticatedProfile(self.chef_profile))
        detail_response = self.api.get(f"/api/v1/orders/chef/orders/{order_id}/")

        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(detail_response.data["order"]["id"], order_id)
        self.assertIn("available_actions", detail_response.data["order"])

    def test_client_can_cancel_cash_order_before_preparing_and_release_stock(self):
        add_response = self.api.post(
            "/api/v1/orders/cart/items/",
            {"dish_id": self.dish.id, "quantity": 2},
            format="json",
        )
        cart_id = add_response.data["cart"]["id"]
        preview_response = self.api.post(
            "/api/v1/orders/checkout/preview/",
            {
                "cart_id": cart_id,
                "fulfillment_type": "pickup",
                "payment_method": "cash",
            },
            format="json",
        )
        confirm_response = self.api.post(
            "/api/v1/orders/checkout/confirm/",
            {
                "cart_id": cart_id,
                "fulfillment_type": "pickup",
                "payment_method": "cash",
                "expected_total": preview_response.data["pricing"]["total"],
            },
            format="json",
        )
        order_id = confirm_response.data["order_id"]
        self.assertEqual(Dish.objects.get(id=self.dish.id).portions, 4)

        cancel_response = self.api.post(f"/api/v1/orders/{order_id}/cancel/", {}, format="json")
        self.assertEqual(cancel_response.status_code, 200)

        order = Order.objects.get(id=order_id)
        payment = OrderPayment.objects.get(order=order)
        pickup = PickupConfirmation.objects.get(order=order)
        self.dish.refresh_from_db()
        self.assertEqual(order.status, Order.Status.CANCELLED)
        self.assertEqual(payment.status, OrderPayment.Status.CANCELLED)
        self.assertEqual(self.dish.portions, 6)
        self.assertFalse(order.stock_reserved)
        self.assertEqual(pickup.status, PickupConfirmation.Status.CANCELLED)

    def test_client_cannot_cancel_order_once_preparing_started(self):
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
                "payment_method": "cash",
            },
            format="json",
        )
        confirm_response = self.api.post(
            "/api/v1/orders/checkout/confirm/",
            {
                "cart_id": cart_id,
                "fulfillment_type": "pickup",
                "payment_method": "cash",
                "expected_total": preview_response.data["pricing"]["total"],
            },
            format="json",
        )
        order_id = confirm_response.data["order_id"]

        self.api.force_authenticate(user=AuthenticatedProfile(self.chef_profile))
        self.assertEqual(self.api.post(f"/api/v1/orders/chef/orders/{order_id}/accept/", {}, format="json").status_code, 200)
        self.assertEqual(self.api.post(f"/api/v1/orders/chef/orders/{order_id}/preparing/", {}, format="json").status_code, 200)

        self.api.force_authenticate(user=AuthenticatedProfile(self.client_profile))
        cancel_response = self.api.post(f"/api/v1/orders/{order_id}/cancel/", {}, format="json")
        self.assertEqual(cancel_response.status_code, 409)
        self.assertEqual(cancel_response.data["code"], "client_cancel_not_allowed")

    @override_settings(
        COINGATE_API_BASE_URL="https://api-sandbox.coingate.com/v2",
        COINGATE_API_TOKEN="test-token",
        ORDER_COINGATE_CALLBACK_URL="https://backend.test/api/v1/orders/payments/bitcoin-coingate/callback/",
        ORDER_COINGATE_SUCCESS_URL="https://frontend.test/client/payments/bitcoin-coingate/return?payment=coingate_success",
        ORDER_COINGATE_CANCEL_URL="https://frontend.test/client/payments/bitcoin-coingate/return?payment=coingate_cancel",
    )
    @patch("modules.pedidos_checkout_pagos.services.order_coingate_service.requests.post")
    def test_bitcoin_coingate_confirm_creates_provider_checkout(self, post_mock):
        post_mock.return_value = Mock(
            status_code=200,
            json=Mock(
                return_value={
                    "id": "cg-order-001",
                    "status": "new",
                    "payment_url": "https://pay-sandbox.coingate.com/invoice/cg-order-001",
                }
            ),
        )
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
                "payment_method": "bitcoin_coingate",
            },
            format="json",
        )
        confirm_response = self.api.post(
            "/api/v1/orders/checkout/confirm/",
            {
                "cart_id": cart_id,
                "fulfillment_type": "pickup",
                "payment_method": "bitcoin_coingate",
                "expected_total": preview_response.data["pricing"]["total"],
            },
            format="json",
        )

        self.assertEqual(confirm_response.status_code, 201)
        order = Order.objects.get(id=confirm_response.data["order_id"])
        payment = OrderPayment.objects.get(order=order)
        self.assertEqual(order.status, Order.Status.PAYMENT_VALIDATING)
        self.assertEqual(payment.method, Order.PaymentMethod.BITCOIN_COINGATE)
        self.assertEqual(payment.status, OrderPayment.Status.PENDING)
        self.assertEqual(payment.provider, "COINGATE_SANDBOX")
        self.assertEqual(payment.external_reference, "cg-order-001")
        self.assertIn("coingate_order_id=homechef-order-", payment.provider_payload.get("success_url", ""))
        self.assertEqual(payment.provider_payload.get("provider_price_currency"), "USD")
        self.assertEqual(confirm_response.data["payment"]["payment_url"], payment.payment_url)
        self.assertTrue(order.stock_reserved)
        post_mock.assert_called_once()
        self.assertEqual(post_mock.call_args.kwargs["json"]["price_currency"], "USD")

    @override_settings(
        COINGATE_API_BASE_URL="https://api-sandbox.coingate.com/v2",
        COINGATE_API_TOKEN="test-token",
        ORDER_COINGATE_CALLBACK_URL="https://backend.test/api/v1/orders/payments/bitcoin-coingate/callback/",
        ORDER_COINGATE_SUCCESS_URL="https://frontend.test/client/payments/bitcoin-coingate/return?payment=coingate_success",
        ORDER_COINGATE_CANCEL_URL="https://frontend.test/client/payments/bitcoin-coingate/return?payment=coingate_cancel",
    )
    @patch("modules.pedidos_checkout_pagos.services.order_coingate_service.requests.get")
    @patch("modules.pedidos_checkout_pagos.services.order_coingate_service.requests.post")
    def test_bitcoin_coingate_confirm_return_confirms_payment_and_order(self, post_mock, get_mock):
        post_mock.return_value = Mock(
            status_code=200,
            json=Mock(
                return_value={
                    "id": "cg-order-002",
                    "status": "new",
                    "payment_url": "https://pay-sandbox.coingate.com/invoice/cg-order-002",
                }
            ),
        )
        get_mock.return_value = Mock(
            status_code=200,
            json=Mock(
                return_value={
                    "id": "cg-order-002",
                    "order_id": "homechef-order-demo-002",
                    "status": "paid",
                }
            ),
        )
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
                "payment_method": "bitcoin_coingate",
            },
            format="json",
        )
        confirm_response = self.api.post(
            "/api/v1/orders/checkout/confirm/",
            {
                "cart_id": cart_id,
                "fulfillment_type": "pickup",
                "payment_method": "bitcoin_coingate",
                "expected_total": preview_response.data["pricing"]["total"],
            },
            format="json",
        )
        order = Order.objects.get(id=confirm_response.data["order_id"])
        payment = OrderPayment.objects.get(order=order)

        return_response = self.api.post(
            "/api/v1/orders/payments/bitcoin-coingate/confirm-return/",
            {
                "provider": "COINGATE_SANDBOX",
                "coingate_order_id": payment.external_reference,
            },
            format="json",
        )

        self.assertEqual(return_response.status_code, 200)
        payment.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(payment.status, OrderPayment.Status.CONFIRMED)
        self.assertEqual(order.status, Order.Status.AWAITING_CHEF_CONFIRMATION)
        self.assertEqual(return_response.data["payment_status"], "CONFIRMED")
        self.assertEqual(return_response.data["order_status"], "AWAITING_CHEF_CONFIRMATION")
        self.assertGreaterEqual(OrderPaymentEvent.objects.filter(payment=payment).count(), 3)
        get_mock.assert_called_once()

    @override_settings(
        COINGATE_API_BASE_URL="https://api-sandbox.coingate.com/v2",
        COINGATE_API_TOKEN="test-token",
    )
    @patch("modules.pedidos_checkout_pagos.services.order_coingate_service.requests.post")
    def test_bitcoin_coingate_callback_cancels_order_and_releases_stock(self, post_mock):
        post_mock.return_value = Mock(
            status_code=200,
            json=Mock(
                return_value={
                    "id": "cg-order-003",
                    "status": "new",
                    "payment_url": "https://pay-sandbox.coingate.com/invoice/cg-order-003",
                }
            ),
        )
        add_response = self.api.post(
            "/api/v1/orders/cart/items/",
            {"dish_id": self.dish.id, "quantity": 2},
            format="json",
        )
        cart_id = add_response.data["cart"]["id"]
        preview_response = self.api.post(
            "/api/v1/orders/checkout/preview/",
            {
                "cart_id": cart_id,
                "fulfillment_type": "delivery",
                "payment_method": "bitcoin_coingate",
                "address": {
                    "label": "Casa",
                    "contact_name": "Juan",
                    "contact_phone": "77777777",
                    "line_1": "Av Siempre Viva 123",
                },
            },
            format="json",
        )
        confirm_response = self.api.post(
            "/api/v1/orders/checkout/confirm/",
            {
                "cart_id": cart_id,
                "fulfillment_type": "delivery",
                "payment_method": "bitcoin_coingate",
                "expected_total": preview_response.data["pricing"]["total"],
                "address": {
                    "label": "Casa",
                    "contact_name": "Juan",
                    "contact_phone": "77777777",
                    "line_1": "Av Siempre Viva 123",
                },
            },
            format="json",
        )
        order = Order.objects.get(id=confirm_response.data["order_id"])
        payment = OrderPayment.objects.get(order=order)
        self.assertEqual(Dish.objects.get(id=self.dish.id).portions, 4)

        anonymous_api = APIClient()
        callback_response = anonymous_api.post(
            "/api/v1/orders/payments/bitcoin-coingate/callback/",
            {
                "id": payment.external_reference,
                "order_id": payment.provider_payload.get("homechef_order_id", ""),
                "status": "expired",
            },
            format="json",
        )

        self.assertEqual(callback_response.status_code, 200)
        self.assertTrue(callback_response.data["handled"])
        order.refresh_from_db()
        payment.refresh_from_db()
        self.dish.refresh_from_db()
        self.assertEqual(order.status, Order.Status.EXPIRED)
        self.assertEqual(payment.status, OrderPayment.Status.EXPIRED)
        self.assertEqual(self.dish.portions, 6)

    def test_pickup_ready_state_starts_window_and_grace_period(self):
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
                "payment_method": "cash",
            },
            format="json",
        )
        pickup_slot = preview_response.data["pickup_policy"]["available_slots"][0]["id"]
        confirm_response = self.api.post(
            "/api/v1/orders/checkout/confirm/",
            {
                "cart_id": cart_id,
                "fulfillment_type": "pickup",
                "payment_method": "cash",
                "pickup_slot": pickup_slot,
                "expected_total": preview_response.data["pricing"]["total"],
            },
            format="json",
        )
        order_id = confirm_response.data["order_id"]

        self.api.force_authenticate(user=AuthenticatedProfile(self.chef_profile))
        self.assertEqual(self.api.post(f"/api/v1/orders/chef/orders/{order_id}/accept/", {}, format="json").status_code, 200)
        self.assertEqual(self.api.post(f"/api/v1/orders/chef/orders/{order_id}/preparing/", {}, format="json").status_code, 200)
        ready_response = self.api.post(f"/api/v1/orders/chef/orders/{order_id}/ready/", {}, format="json")

        self.assertEqual(ready_response.status_code, 200)
        pickup = PickupConfirmation.objects.get(order_id=order_id)
        self.assertIsNotNone(pickup.pickup_window_start)
        self.assertIsNotNone(pickup.pickup_window_end)
        self.assertIsNotNone(pickup.pickup_grace_deadline)
        self.assertIsNotNone(pickup.pickup_retention_deadline)
        self.assertFalse(pickup.pickup_no_show_flag)
        self.assertIn("Tolerancia hasta", pickup.pickup_schedule_note)

    def test_pickup_no_show_is_marked_and_order_expires_after_retention(self):
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
                "payment_method": "cash",
            },
            format="json",
        )
        pickup_slot = preview_response.data["pickup_policy"]["available_slots"][0]["id"]
        confirm_response = self.api.post(
            "/api/v1/orders/checkout/confirm/",
            {
                "cart_id": cart_id,
                "fulfillment_type": "pickup",
                "payment_method": "cash",
                "pickup_slot": pickup_slot,
                "expected_total": preview_response.data["pricing"]["total"],
            },
            format="json",
        )
        order_id = confirm_response.data["order_id"]

        self.api.force_authenticate(user=AuthenticatedProfile(self.chef_profile))
        self.assertEqual(self.api.post(f"/api/v1/orders/chef/orders/{order_id}/accept/", {}, format="json").status_code, 200)
        self.assertEqual(self.api.post(f"/api/v1/orders/chef/orders/{order_id}/preparing/", {}, format="json").status_code, 200)
        self.assertEqual(self.api.post(f"/api/v1/orders/chef/orders/{order_id}/ready/", {}, format="json").status_code, 200)

        pickup = PickupConfirmation.objects.get(order_id=order_id)
        now = timezone.now()
        pickup.pickup_window_start = now - timedelta(hours=2)
        pickup.pickup_window_end = now - timedelta(hours=1, minutes=30)
        pickup.pickup_grace_deadline = now - timedelta(hours=1)
        pickup.pickup_retention_deadline = now + timedelta(minutes=15)
        pickup.save(
            update_fields=[
                "pickup_window_start",
                "pickup_window_end",
                "pickup_grace_deadline",
                "pickup_retention_deadline",
                "updated_at",
            ]
        )

        self.api.force_authenticate(user=AuthenticatedProfile(self.client_profile))
        no_show_response = self.api.get(f"/api/v1/orders/my-orders/{order_id}/")
        self.assertEqual(no_show_response.status_code, 200)
        self.assertTrue(no_show_response.data["order"]["pickup"]["pickup_no_show_flag"])
        self.assertEqual(no_show_response.data["order"]["pickup"]["state_label"], "No presentado")

        pickup.refresh_from_db()
        pickup.pickup_retention_deadline = now - timedelta(minutes=5)
        pickup.save(update_fields=["pickup_retention_deadline", "updated_at"])

        expired_response = self.api.get(f"/api/v1/orders/my-orders/{order_id}/tracking/")
        self.assertEqual(expired_response.status_code, 200)
        self.assertEqual(expired_response.data["pickup"]["state_label"], "Retencion vencida")
        order = Order.objects.get(id=order_id)
        self.assertEqual(order.status, Order.Status.READY_FOR_PICKUP)

        self.api.force_authenticate(user=AuthenticatedProfile(self.chef_profile))
        close_response = self.api.post(
            f"/api/v1/orders/chef/orders/{order_id}/pickup/close-retention/",
            {},
            format="json",
        )
        self.assertEqual(close_response.status_code, 200)
        order = Order.objects.get(id=order_id)
        pickup.refresh_from_db()
        payment = OrderPayment.objects.get(order=order)
        self.assertEqual(order.status, Order.Status.EXPIRED)
        self.assertEqual(pickup.status, PickupConfirmation.Status.CANCELLED)
        self.assertEqual(payment.status, OrderPayment.Status.CANCELLED)

    def test_chef_can_operate_pickup_no_show_retention_flow(self):
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
                "payment_method": "cash",
            },
            format="json",
        )
        pickup_slot = preview_response.data["pickup_policy"]["available_slots"][0]["id"]
        confirm_response = self.api.post(
            "/api/v1/orders/checkout/confirm/",
            {
                "cart_id": cart_id,
                "fulfillment_type": "pickup",
                "payment_method": "cash",
                "pickup_slot": pickup_slot,
                "expected_total": preview_response.data["pricing"]["total"],
            },
            format="json",
        )
        order_id = confirm_response.data["order_id"]

        self.api.force_authenticate(user=AuthenticatedProfile(self.chef_profile))
        self.assertEqual(self.api.post(f"/api/v1/orders/chef/orders/{order_id}/accept/", {}, format="json").status_code, 200)
        self.assertEqual(self.api.post(f"/api/v1/orders/chef/orders/{order_id}/preparing/", {}, format="json").status_code, 200)
        self.assertEqual(self.api.post(f"/api/v1/orders/chef/orders/{order_id}/ready/", {}, format="json").status_code, 200)

        pickup = PickupConfirmation.objects.get(order_id=order_id)
        now = timezone.now()
        pickup.pickup_window_start = now - timedelta(minutes=25)
        pickup.pickup_window_end = now - timedelta(minutes=5)
        pickup.pickup_grace_deadline = now + timedelta(minutes=10)
        pickup.pickup_retention_deadline = now + timedelta(minutes=40)
        pickup.save(
            update_fields=[
                "pickup_window_start",
                "pickup_window_end",
                "pickup_grace_deadline",
                "pickup_retention_deadline",
                "updated_at",
            ]
        )

        detail_response = self.api.get(f"/api/v1/orders/chef/orders/{order_id}/")
        self.assertEqual(detail_response.status_code, 200)
        self.assertIn("mark_pickup_no_show", detail_response.data["order"]["available_actions"])

        no_show_response = self.api.post(
            f"/api/v1/orders/chef/orders/{order_id}/pickup/no-show/",
            {},
            format="json",
        )
        self.assertEqual(no_show_response.status_code, 200)
        self.assertTrue(no_show_response.data["order"]["pickup"]["pickup_no_show_flag"])
        self.assertIn("extend_pickup_retention", no_show_response.data["order"]["available_actions"])

        pickup.refresh_from_db()
        previous_deadline = pickup.pickup_retention_deadline
        extend_response = self.api.post(
            f"/api/v1/orders/chef/orders/{order_id}/pickup/extend-retention/",
            {},
            format="json",
        )
        self.assertEqual(extend_response.status_code, 200)
        pickup.refresh_from_db()
        self.assertEqual(pickup.retention_extension_count, 1)
        self.assertGreater(pickup.pickup_retention_deadline, previous_deadline)

        pickup.pickup_retention_deadline = timezone.now() - timedelta(minutes=1)
        pickup.save(update_fields=["pickup_retention_deadline", "updated_at"])
        close_response = self.api.post(
            f"/api/v1/orders/chef/orders/{order_id}/pickup/close-retention/",
            {},
            format="json",
        )
        self.assertEqual(close_response.status_code, 200)
        order = Order.objects.get(id=order_id)
        payment = OrderPayment.objects.get(order=order)
        pickup.refresh_from_db()
        self.assertEqual(order.status, Order.Status.EXPIRED)
        self.assertEqual(payment.status, OrderPayment.Status.CANCELLED)
        self.assertEqual(pickup.status, PickupConfirmation.Status.CANCELLED)
        self.assertEqual(
            OperationalNotification.objects.filter(order_ref=order_id, event_code="PICKUP_NO_SHOW").count(),
            2,
        )
        self.assertEqual(
            OperationalNotification.objects.filter(order_ref=order_id, event_code="PICKUP_RETENTION_EXTENDED").count(),
            2,
        )
        self.assertEqual(
            OperationalNotification.objects.filter(order_ref=order_id, event_code="PICKUP_RETENTION_CLOSED").count(),
            2,
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


    @override_settings(
        STRIPE_SECRET_KEY="sk_test_demo",
        ORDER_STRIPE_SUCCESS_URL="https://frontend.test/client/payments/stripe/return?payment=stripe_success",
        ORDER_STRIPE_CANCEL_URL="https://frontend.test/client/payments/stripe/return?payment=stripe_cancel",
    )
    @patch("modules.pedidos_checkout_pagos.services.order_stripe_service.stripe.checkout.Session.retrieve")
    @patch("modules.pedidos_checkout_pagos.services.order_stripe_service.stripe.checkout.Session.create")
    def test_receipt_generation_and_download_for_stripe(self, session_create, session_retrieve):
        session_create.return_value = Mock(
            id="cs_test_receipt_001",
            url="https://checkout.stripe.com/c/pay/cs_test_receipt_001",
            mode="payment",
            payment_status="unpaid",
        )
        session_retrieve.return_value = Mock(
            payment_status="paid",
            status="complete",
            to_dict_recursive=Mock(return_value={"id": "cs_test_receipt_001", "payment_status": "paid", "status": "complete"}),
        )
        order = self._create_checkout_order("stripe_test", fulfillment_type="pickup")
        payment = OrderPayment.objects.get(order=order)

        response = self.api.post(
            "/api/v1/orders/payments/stripe/confirm-return/",
            {"provider": "STRIPE_SANDBOX", "stripe_session_id": payment.external_reference},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        receipt = OrderReceipt.objects.get(order=order, payment=payment)
        self.assertEqual(receipt.payment_method, Order.PaymentMethod.STRIPE_TEST)
        self._assert_client_receipt_download(order.id, receipt.id, "pdf", "application/pdf")
        self._assert_chef_receipt_download(order.id, receipt.id, "docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")

    def test_receipt_generation_and_download_for_cash_pickup(self):
        order = self._create_checkout_order("cash", fulfillment_type="pickup")
        pickup = PickupConfirmation.objects.get(order=order)

        self.api.force_authenticate(user=AuthenticatedProfile(self.chef_profile))
        self.assertEqual(self.api.post(f"/api/v1/orders/chef/orders/{order.id}/accept/", {}, format="json").status_code, 200)
        self.assertEqual(self.api.post(f"/api/v1/orders/chef/orders/{order.id}/preparing/", {}, format="json").status_code, 200)
        self.assertEqual(self.api.post(f"/api/v1/orders/chef/orders/{order.id}/ready/", {}, format="json").status_code, 200)
        confirm_response = self.api.post(
            f"/api/v1/orders/chef/orders/{order.id}/pickup/confirm/",
            {"pickup_code": pickup.pickup_code},
            format="json",
        )
        self.assertEqual(confirm_response.status_code, 200)

        payment = OrderPayment.objects.get(order=order)
        receipt = OrderReceipt.objects.get(order=order, payment=payment)
        self.assertEqual(receipt.payment_method, Order.PaymentMethod.CASH)
        self._assert_client_receipt_download(order.id, receipt.id, "html", "text/html; charset=utf-8")
        self._assert_chef_receipt_download(order.id, receipt.id, "pdf", "application/pdf")

    def test_receipt_generation_and_download_for_qr_simulado(self):
        order = self._create_checkout_order("qr_simulado", fulfillment_type="pickup")
        payment = OrderPayment.objects.get(order=order)
        session = SimulatedQRPaymentSession.objects.get(payment=payment)

        self.assertEqual(self.api.post(f"/api/v1/orders/payments/qr-sessions/{session.session_code}/start/", {}, format="json").status_code, 200)
        with patch("modules.pedidos_checkout_pagos.services.qr_payment_service.sleep", return_value=None):
            confirm_response = self.api.post(f"/api/v1/orders/payments/qr-sessions/{session.session_code}/confirm/", {}, format="json")
        self.assertEqual(confirm_response.status_code, 200)

        receipt = OrderReceipt.objects.get(order=order, payment=payment)
        self._assert_client_receipt_download(order.id, receipt.id, "docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")

    @override_settings(
        COINGATE_API_BASE_URL="https://api-sandbox.coingate.com/v2",
        COINGATE_API_TOKEN="coingate_token_test",
        ORDER_COINGATE_CALLBACK_URL="https://backend.test/api/v1/orders/payments/bitcoin-coingate/callback/",
        ORDER_COINGATE_SUCCESS_URL="https://frontend.test/client/payments/bitcoin-coingate/return?payment=coingate_success",
        ORDER_COINGATE_CANCEL_URL="https://frontend.test/client/payments/bitcoin-coingate/return?payment=coingate_cancel",
    )
    @patch("modules.pedidos_checkout_pagos.services.order_coingate_service.requests.get")
    @patch("modules.pedidos_checkout_pagos.services.order_coingate_service.requests.post")
    def test_receipt_generation_and_download_for_coingate(self, post_mock, get_mock):
        post_mock.return_value = Mock(
            status_code=200,
            json=Mock(
                return_value={
                    "id": "cg-order-receipt-001",
                    "order_id": "homechef-order-demo",
                    "payment_url": "https://pay-sandbox.coingate.com/invoice/cg-order-receipt-001",
                    "status": "new",
                }
            ),
        )
        get_mock.return_value = Mock(
            status_code=200,
            json=Mock(
                return_value={
                    "id": "cg-order-receipt-001",
                    "order_id": "homechef-order-demo",
                    "status": "paid",
                }
            ),
        )
        order = self._create_checkout_order("bitcoin_coingate", fulfillment_type="pickup")
        payment = OrderPayment.objects.get(order=order)

        response = self.api.post(
            "/api/v1/orders/payments/bitcoin-coingate/confirm-return/",
            {"provider": "COINGATE_SANDBOX", "coingate_order_id": payment.external_reference},
            format="json",
        )
        self.assertEqual(response.status_code, 200)

        receipt = OrderReceipt.objects.get(order=order, payment=payment)
        self._assert_client_receipt_download(order.id, receipt.id, "pdf", "application/pdf")

    def test_receipt_endpoints_reject_unrelated_client(self):
        order = self._create_checkout_order("qr_simulado", fulfillment_type="pickup")
        payment = OrderPayment.objects.get(order=order)
        session = SimulatedQRPaymentSession.objects.get(payment=payment)
        self.assertEqual(self.api.post(f"/api/v1/orders/payments/qr-sessions/{session.session_code}/start/", {}, format="json").status_code, 200)
        with patch("modules.pedidos_checkout_pagos.services.qr_payment_service.sleep", return_value=None):
            self.assertEqual(self.api.post(f"/api/v1/orders/payments/qr-sessions/{session.session_code}/confirm/", {}, format="json").status_code, 200)
        receipt = OrderReceipt.objects.get(order=order, payment=payment)

        other_client = UserProfile.objects.create(
            supabase_user_id=uuid4(),
            email="other-client@test.com",
            role=UserProfile.ROLE_CLIENT,
            first_name="Other",
            is_active=True,
        )
        self.api.force_authenticate(user=AuthenticatedProfile(other_client))
        response = self.api.get(f"/api/v1/orders/my-orders/{order.id}/receipts/{receipt.id}/download/?file_format=pdf")
        self.assertEqual(response.status_code, 404)

    def _create_checkout_order(self, payment_method: str, *, fulfillment_type: str):
        add_response = self.api.post("/api/v1/orders/cart/items/", {"dish_id": self.dish.id, "quantity": 1}, format="json")
        cart_id = add_response.data["cart"]["id"]
        preview_response = self.api.post(
            "/api/v1/orders/checkout/preview/",
            {
                "cart_id": cart_id,
                "fulfillment_type": fulfillment_type,
                "payment_method": payment_method,
            },
            format="json",
        )
        confirm_payload = {
            "cart_id": cart_id,
            "fulfillment_type": fulfillment_type,
            "payment_method": payment_method,
            "expected_total": preview_response.data["pricing"]["total"],
        }
        confirm_response = self.api.post("/api/v1/orders/checkout/confirm/", confirm_payload, format="json")
        self.assertEqual(confirm_response.status_code, 201)
        return Order.objects.get(id=confirm_response.data["order_id"])

    def _create_historical_order(self, *, items: list[tuple[Dish, int]], fulfillment_type: str = Order.FulfillmentType.PICKUP):
        subtotal = sum(Decimal(str(dish.price)) * quantity for dish, quantity in items)
        order = Order.objects.create(
            client=self.client_profile,
            chef=items[0][0].chef,
            status=Order.Status.PICKED_UP if fulfillment_type == Order.FulfillmentType.PICKUP else Order.Status.DELIVERED,
            fulfillment_type=fulfillment_type,
            payment_method=Order.PaymentMethod.CASH,
            subtotal=subtotal,
            total=subtotal,
        )
        for dish, quantity in items:
            OrderItem.objects.create(
                order=order,
                dish=dish,
                quantity=quantity,
                unit_price=Decimal(str(dish.price)),
                subtotal=Decimal(str(dish.price)) * quantity,
                dish_name_snapshot=dish.name,
                dish_description_snapshot=dish.description,
                dish_image_url_snapshot=dish.image_url,
            )
        return order

    def _assert_client_receipt_download(self, order_id: str, receipt_id: str, format_value: str, content_type: str):
        self.api.force_authenticate(user=AuthenticatedProfile(self.client_profile))
        list_response = self.api.get(f"/api/v1/orders/my-orders/{order_id}/receipts/")
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(len(list_response.data["items"]), 1)
        download_response = self.api.get(f"/api/v1/orders/my-orders/{order_id}/receipts/{receipt_id}/download/?file_format={format_value}")
        self.assertEqual(download_response.status_code, 200, getattr(download_response, "data", download_response.content))
        self.assertEqual(download_response["Content-Type"], content_type)
        self.assertGreater(len(download_response.content), 50)

    def _assert_chef_receipt_download(self, order_id: str, receipt_id: str, format_value: str, content_type: str):
        self.api.force_authenticate(user=AuthenticatedProfile(self.chef_profile))
        list_response = self.api.get(f"/api/v1/orders/chef/orders/{order_id}/receipts/")
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(len(list_response.data["items"]), 1)
        download_response = self.api.get(f"/api/v1/orders/chef/orders/{order_id}/receipts/{receipt_id}/download/?file_format={format_value}")
        self.assertEqual(download_response.status_code, 200, getattr(download_response, "data", download_response.content))
        self.assertEqual(download_response["Content-Type"], content_type)
        self.assertGreater(len(download_response.content), 50)


class StockContentionTransactionTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.stock_service = DishStockService()
        self.client_one = UserProfile.objects.create(
            supabase_user_id=uuid4(),
            email="contend.client1@test.com",
            role=UserProfile.ROLE_CLIENT,
            first_name="Client1",
            is_active=True,
        )
        self.client_two = UserProfile.objects.create(
            supabase_user_id=uuid4(),
            email="contend.client2@test.com",
            role=UserProfile.ROLE_CLIENT,
            first_name="Client2",
            is_active=True,
        )
        self.chef_profile = UserProfile.objects.create(
            supabase_user_id=uuid4(),
            email="contend.chef@test.com",
            role=UserProfile.ROLE_CHEF,
            first_name="Chef",
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
        self.dish = Dish.objects.create(
            chef=self.chef_profile,
            name="Contencion",
            description="Prueba de stock",
            price=Decimal("12.00"),
            portions=2,
            ingredients=["arroz"],
            tags=["TEST"],
            allergens=[],
            status=Dish.STATUS_PUBLISHED,
        )

    def test_second_order_cannot_reserve_stock_when_first_consumes_last_portions(self):
        order_one = Order.objects.create(
            client=self.client_one,
            chef=self.chef_profile,
            status=Order.Status.PENDING_PAYMENT,
            fulfillment_type=Order.FulfillmentType.PICKUP,
            payment_method=Order.PaymentMethod.CASH,
            subtotal=Decimal("24.00"),
            total=Decimal("24.00"),
        )
        OrderItem.objects.create(
            order=order_one,
            dish=self.dish,
            quantity=2,
            unit_price=Decimal("12.00"),
            subtotal=Decimal("24.00"),
            dish_name_snapshot=self.dish.name,
            dish_description_snapshot=self.dish.description,
            dish_image_url_snapshot=self.dish.image_url,
        )
        order_two = Order.objects.create(
            client=self.client_two,
            chef=self.chef_profile,
            status=Order.Status.PENDING_PAYMENT,
            fulfillment_type=Order.FulfillmentType.PICKUP,
            payment_method=Order.PaymentMethod.CASH,
            subtotal=Decimal("12.00"),
            total=Decimal("12.00"),
        )
        OrderItem.objects.create(
            order=order_two,
            dish=self.dish,
            quantity=1,
            unit_price=Decimal("12.00"),
            subtotal=Decimal("12.00"),
            dish_name_snapshot=self.dish.name,
            dish_description_snapshot=self.dish.description,
            dish_image_url_snapshot=self.dish.image_url,
        )

        self.stock_service.reserve_order_stock(order_one)
        self.dish.refresh_from_db()
        self.assertEqual(self.dish.portions, 0)

        with self.assertRaises(StockValidationError) as ctx:
            self.stock_service.reserve_order_stock(order_two)

        self.assertEqual(ctx.exception.code, "stock_unavailable")
        order_two.refresh_from_db()
        self.assertFalse(order_two.stock_reserved)

        self.stock_service.release_order_stock(order_one)
        self.dish.refresh_from_db()
        self.assertEqual(self.dish.portions, 2)

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

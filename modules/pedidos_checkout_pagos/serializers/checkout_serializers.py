from rest_framework import serializers

from modules.pedidos_checkout_pagos.models import Order


class CheckoutAddressSerializer(serializers.Serializer):
    label = serializers.CharField(required=False, allow_blank=True)
    contact_name = serializers.CharField(required=False, allow_blank=True)
    contact_phone = serializers.CharField(required=False, allow_blank=True)
    line_1 = serializers.CharField(required=False, allow_blank=True)
    reference = serializers.CharField(required=False, allow_blank=True)
    latitude = serializers.FloatField(required=False, allow_null=True)
    longitude = serializers.FloatField(required=False, allow_null=True)


class CheckoutPreviewSerializer(serializers.Serializer):
    cart_id = serializers.CharField()
    fulfillment_type = serializers.ChoiceField(choices=Order.FulfillmentType.choices)
    payment_method = serializers.ChoiceField(
        choices=Order.PaymentMethod.choices,
        required=False,
        default=Order.PaymentMethod.CASH,
    )
    address = CheckoutAddressSerializer(required=False)
    pickup_slot = serializers.CharField(required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)
    success_redirect_to = serializers.CharField(required=False, allow_blank=True)
    cancel_redirect_to = serializers.CharField(required=False, allow_blank=True)


class CheckoutConfirmSerializer(CheckoutPreviewSerializer):
    expected_total = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        required=False,
        allow_null=True,
    )


class CheckoutRoutePreviewSerializer(serializers.Serializer):
    cart_id = serializers.CharField()
    latitude = serializers.FloatField()
    longitude = serializers.FloatField()


class StripeReturnConfirmSerializer(serializers.Serializer):
    provider = serializers.CharField(required=False, allow_blank=True)
    stripe_session_id = serializers.CharField(required=False, allow_blank=True, max_length=255)

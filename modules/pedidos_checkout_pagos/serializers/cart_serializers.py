from rest_framework import serializers


class CartItemWriteSerializer(serializers.Serializer):
    dish_id = serializers.CharField()
    quantity = serializers.IntegerField(min_value=1)
    fulfillment_type = serializers.ChoiceField(
        choices=["pickup", "delivery"],
        required=False,
        allow_null=True,
    )


class CartItemUpdateSerializer(serializers.Serializer):
    quantity = serializers.IntegerField(min_value=1)
    fulfillment_type = serializers.ChoiceField(
        choices=["pickup", "delivery"],
        required=False,
        allow_null=True,
    )

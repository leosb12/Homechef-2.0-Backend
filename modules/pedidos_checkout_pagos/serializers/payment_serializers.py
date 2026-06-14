from rest_framework import serializers


class CoinGateReturnConfirmSerializer(serializers.Serializer):
    provider = serializers.CharField(required=False, allow_blank=True, default="")
    coingate_order_id = serializers.CharField(required=False, allow_blank=True, default="")

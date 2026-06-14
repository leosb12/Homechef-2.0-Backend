from rest_framework import serializers


class PickupConfirmSerializer(serializers.Serializer):
    pickup_code = serializers.CharField()

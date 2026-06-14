from rest_framework import serializers

from modules.confianza_administracion_seguridad.models import NotificationDeviceToken


class NotificationDeviceTokenSerializer(serializers.Serializer):
    platform = serializers.ChoiceField(choices=NotificationDeviceToken.Platform.choices)
    token = serializers.CharField(max_length=4096)
    device_id = serializers.CharField(max_length=120, required=False, allow_blank=True)
    device_label = serializers.CharField(max_length=120, required=False, allow_blank=True)
    metadata = serializers.JSONField(required=False)


class NotificationTokenDeactivateSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=4096)


class DeliveryDriverStatusSerializer(serializers.Serializer):
    approval_status = serializers.ChoiceField(choices=("activo", "suspendido"))

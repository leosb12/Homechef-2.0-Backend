from rest_framework import serializers


class DeliveryLocationPingSerializer(serializers.Serializer):
    latitude = serializers.FloatField()
    longitude = serializers.FloatField()
    accuracy_meters = serializers.FloatField(required=False, allow_null=True)
    speed_mps = serializers.FloatField(required=False, allow_null=True)
    heading_degrees = serializers.FloatField(required=False, allow_null=True)
    recorded_at = serializers.DateTimeField(required=False, allow_null=True)


class DeliveryIncidentCreateSerializer(serializers.Serializer):
    code = serializers.ChoiceField(
        choices=[
            "DELAY",
            "WRONG_ADDRESS",
            "CLIENT_ABSENT",
            "ORDER_DAMAGED",
            "CANCELLATION_REQUEST",
            "CANNOT_COMPLETE",
        ]
    )
    description = serializers.CharField(required=False, allow_blank=True, max_length=2000)
    evidence_urls = serializers.ListField(
        child=serializers.URLField(),
        required=False,
        allow_empty=True,
    )
    evidence_note = serializers.CharField(required=False, allow_blank=True, max_length=500)


class DeliveryIncidentResolveSerializer(serializers.Serializer):
    resolution_notes = serializers.CharField(required=False, allow_blank=True, max_length=2000)


class DeliveryAvailabilityUpdateSerializer(serializers.Serializer):
    manual_status = serializers.ChoiceField(
        choices=[
            "DISPONIBLE",
            "FUERA_DE_SERVICIO",
        ]
    )

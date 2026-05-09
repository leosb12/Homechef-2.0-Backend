from rest_framework import serializers


SYNC_ACTIONS = ("CREATE", "UPDATE", "DELETE")


class SyncOperationInputSerializer(serializers.Serializer):
    operation_id = serializers.UUIDField()
    entity = serializers.CharField(max_length=80)
    action = serializers.ChoiceField(choices=SYNC_ACTIONS)
    local_id = serializers.CharField(max_length=120, required=False, allow_blank=True)
    server_id = serializers.CharField(max_length=120, required=False, allow_blank=True, allow_null=True)
    payload = serializers.JSONField(required=False)
    version = serializers.IntegerField(required=False, allow_null=True)
    created_at = serializers.DateTimeField(required=False)

    def validate(self, attrs):
        action = attrs.get("action")
        if action in {"CREATE", "UPDATE"} and "payload" not in attrs:
            raise serializers.ValidationError({"payload": "Payload requerido para CREATE y UPDATE."})
        if action in {"UPDATE", "DELETE"} and not attrs.get("server_id"):
            raise serializers.ValidationError({"server_id": "server_id requerido para UPDATE y DELETE."})
        return attrs


class SyncRequestSerializer(serializers.Serializer):
    device_id = serializers.CharField(max_length=120)
    last_sync = serializers.DateTimeField(required=False, allow_null=True)
    operations = SyncOperationInputSerializer(many=True)

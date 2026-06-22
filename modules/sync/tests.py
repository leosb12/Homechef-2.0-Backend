from django.test import SimpleTestCase

from modules.sync.serializers import SyncRequestSerializer


class SyncRequestSerializerTests(SimpleTestCase):
    def test_accepts_backend_supported_non_crud_action(self):
        serializer = SyncRequestSerializer(
            data={
                "device_id": "device-1",
                "operations": [
                    {
                        "operation_id": "11111111-1111-4111-8111-111111111111",
                        "entity": "chef_notifications",
                        "action": "MARK_READ",
                        "local_id": "notification-1",
                        "server_id": "notification-1",
                        "payload": {"notification_id": "notification-1"},
                    }
                ],
            }
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)


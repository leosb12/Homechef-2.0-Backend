from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from .services.delivery_operations_service import (
    DeliveryLogisticsError,
    DeliveryOperationsService,
)


class DeliveryAssignmentConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        user = self.scope.get("user")
        if not user or not getattr(user, "is_authenticated", False):
            await self.close(code=4401)
            return
        if getattr(user, "role", "") != "REPARTIDOR":
            await self.close(code=4403)
            return

        self.assignment_id = self.scope["url_route"]["kwargs"].get("assignment_id", "")
        self.group_name = f"delivery_assignment_{self.assignment_id}"
        try:
            payload = await sync_to_async(self._load_snapshot)()
        except DeliveryLogisticsError:
            await self.close(code=4404)
            return

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        await self.send_json({"type": "delivery.snapshot", "assignment": payload["assignment"]})

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def delivery_assignment_snapshot(self, event):
        await self.send_json(
            {
                "type": "delivery.snapshot",
                "assignment": event["assignment"],
            }
        )

    def _load_snapshot(self):
        return DeliveryOperationsService().get_detail(self.scope["user"].id, self.assignment_id)

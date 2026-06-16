from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from .services.order_cash_service import OrderCashService, OrderCashServiceError


class OrderTrackingConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        user = self.scope.get("user")
        if not user or not getattr(user, "is_authenticated", False):
            await self.close(code=4401)
            return
        if getattr(user, "role", "") not in {"CLIENTE", "COCINERO"}:
            await self.close(code=4403)
            return

        self.order_id = self.scope["url_route"]["kwargs"].get("order_id", "")
        self.group_name = f"order_tracking_{self.order_id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        try:
            payload = await sync_to_async(self._load_snapshot)()
        except OrderCashServiceError:
            await self.send_json(
                {
                    "type": "order_tracking.unavailable",
                    "order_id": self.order_id,
                }
            )
            await self.close(code=4404)
            return
        await self.send_json({"type": "order_tracking.snapshot", "tracking": payload})

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def order_tracking_refresh(self, event):
        try:
            payload = await sync_to_async(self._load_snapshot)()
        except OrderCashServiceError:
            await self.send_json(
                {
                    "type": "order_tracking.unavailable",
                    "order_id": self.order_id,
                }
            )
            await self.close(code=4404)
            return
        await self.send_json({"type": "order_tracking.snapshot", "tracking": payload})

    def _load_snapshot(self):
        service = OrderCashService()
        role = getattr(self.scope.get("user"), "role", "")
        if role == "COCINERO":
            return service.get_chef_order_tracking(self.scope["user"].id, self.order_id)
        return service.get_client_order_tracking(self.scope["user"].id, self.order_id)

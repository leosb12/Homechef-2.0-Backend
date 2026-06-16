from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from .services.delivery_operations_service import (
    DeliveryLogisticsError,
    DeliveryOperationsService,
)
from .services.delivery_tracking_service import (
    DeliveryTrackingError,
    DeliveryTrackingService,
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
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        try:
            payload = await sync_to_async(self._load_snapshot)()
        except DeliveryLogisticsError:
            await self.send_json(
                {
                    "type": "delivery.unavailable",
                    "assignment_id": self.assignment_id,
                }
            )
            await self.close(code=4404)
            return
        await self.send_json({"type": "delivery.snapshot", "assignment": payload["assignment"]})

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive_json(self, content, **kwargs):
        event_type = f"{content.get('type', '')}"
        try:
            if event_type == "delivery.location.ping":
                payload = content.get("payload") or {}
                await sync_to_async(self._record_location_ping)(payload)
                return
            if event_type == "delivery.route.refresh":
                await sync_to_async(self._refresh_route)()
                return
        except DeliveryTrackingError as exc:
            await self.send_json(
                {
                    "type": "delivery.error",
                    "code": exc.code,
                    "detail": exc.message,
                }
            )
        except DeliveryLogisticsError as exc:
            await self.send_json(
                {
                    "type": "delivery.error",
                    "code": exc.code,
                    "detail": exc.message,
                }
            )

    async def delivery_assignment_snapshot(self, event):
        if event.get("delivery_user_id") and event["delivery_user_id"] != getattr(self.scope.get("user"), "id", ""):
            await self.send_json(
                {
                    "type": "delivery.reassigned",
                    "assignment_id": self.assignment_id,
                }
            )
            await self.close(code=4409)
            return
        await self.send_json(
            {
                "type": "delivery.snapshot",
                "assignment": event["assignment"],
            }
        )

    def _load_snapshot(self):
        return DeliveryOperationsService().get_detail(self.scope["user"].id, self.assignment_id)

    def _record_location_ping(self, payload: dict):
        return DeliveryTrackingService().record_delivery_location(
            self.scope["user"].id,
            self.assignment_id,
            payload,
        )

    def _refresh_route(self):
        return DeliveryTrackingService().refresh_route_for_delivery(
            self.scope["user"].id,
            self.assignment_id,
        )


class DeliveryDashboardConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        user = self.scope.get("user")
        if not user or not getattr(user, "is_authenticated", False):
            await self.close(code=4401)
            return
        if getattr(user, "role", "") != "REPARTIDOR":
            await self.close(code=4403)
            return
        self.personal_group = f"delivery_dashboard_{getattr(user, 'id', '')}"
        self.global_group = "delivery_dashboard_all"
        await self.channel_layer.group_add(self.personal_group, self.channel_name)
        await self.channel_layer.group_add(self.global_group, self.channel_name)
        await self.accept()
        await self.send_json({"type": "delivery.dashboard.ready"})

    async def disconnect(self, close_code):
        if hasattr(self, "personal_group"):
            await self.channel_layer.group_discard(self.personal_group, self.channel_name)
        if hasattr(self, "global_group"):
            await self.channel_layer.group_discard(self.global_group, self.channel_name)

    async def delivery_dashboard_refresh(self, event):
        await self.send_json(
            {
                "type": "delivery.dashboard.refresh",
                "reason": event.get("reason", ""),
            }
        )

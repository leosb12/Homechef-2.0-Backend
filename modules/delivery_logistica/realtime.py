from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from .services.delivery_operations_service import (
    DeliveryLogisticsError,
    DeliveryOperationsService,
)


def publish_assignment_snapshot_for_delivery(assignment_id: str, delivery_user_id: str):
    if not assignment_id or not delivery_user_id:
        return
    try:
        payload = DeliveryOperationsService().get_detail(delivery_user_id, assignment_id)
    except DeliveryLogisticsError:
        return
    channel_layer = get_channel_layer()
    if not channel_layer:
        return
    async_to_sync(channel_layer.group_send)(
        f"delivery_assignment_{assignment_id}",
        {
            "type": "delivery_assignment_snapshot",
            "assignment": payload["assignment"],
        },
    )

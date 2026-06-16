from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer


def publish_order_tracking_refresh(order_id: str):
    if not order_id:
        return
    channel_layer = get_channel_layer()
    if not channel_layer:
        return
    async_to_sync(channel_layer.group_send)(
        f"order_tracking_{order_id}",
        {
            "type": "order_tracking_refresh",
            "order_id": str(order_id),
        },
    )

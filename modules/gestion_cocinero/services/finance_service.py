import datetime
from datetime import timedelta
from django.utils import timezone
from django.db.models import Sum

from modules.pedidos_checkout_pagos.models import Order


class FinanceService:
    def get_finances_summary(self, chef_id: int, start_date_str: str = None, end_date_str: str = None) -> dict:
        # Determine the date range
        if start_date_str and end_date_str:
            try:
                start_date = timezone.datetime.fromisoformat(start_date_str).replace(tzinfo=datetime.timezone.utc)
                end_date = timezone.datetime.fromisoformat(end_date_str).replace(tzinfo=datetime.timezone.utc)
                end_date = end_date.replace(hour=23, minute=59, second=59)
            except ValueError:
                end_date = timezone.now()
                start_date = end_date - timedelta(days=30)
        else:
            end_date = timezone.now()
            start_date = end_date - timedelta(days=30)

        base_qs = Order.objects.filter(
            chef__supabase_user_id=chef_id,
            created_at__range=[start_date, end_date]
        )

        # Consolidated
        consolidated_qs = base_qs.filter(status__in=[Order.Status.DELIVERED, Order.Status.PICKED_UP])
        c_agg = consolidated_qs.aggregate(
            subtotal=Sum('subtotal'),
            service_fee=Sum('service_fee'),
            total=Sum('total'),
            delivery_fee=Sum('delivery_fee')
        )
        c_subtotal = c_agg['subtotal'] or 0
        c_service_fee = c_agg['service_fee'] or 0
        c_total = c_agg['total'] or 0
        c_delivery_fee = c_agg['delivery_fee'] or 0
        c_net = c_total - c_delivery_fee - c_service_fee

        # Pending
        pending_qs = base_qs.filter(status=Order.Status.PAID)
        p_agg = pending_qs.aggregate(
            subtotal=Sum('subtotal'),
            service_fee=Sum('service_fee'),
            total=Sum('total'),
            delivery_fee=Sum('delivery_fee')
        )
        p_subtotal = p_agg['subtotal'] or 0
        p_service_fee = p_agg['service_fee'] or 0
        p_total = p_agg['total'] or 0
        p_delivery_fee = p_agg['delivery_fee'] or 0
        p_net = p_total - p_delivery_fee - p_service_fee

        return {
            "period": {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat()
            },
            "ingresos_consolidados": {
                "ventas_brutas": float(c_subtotal),
                "comisiones_plataforma": float(c_service_fee),
                "ganancia_neta": float(c_net)
            },
            "ingresos_pendientes": {
                "ventas_brutas": float(p_subtotal),
                "comisiones_plataforma": float(p_service_fee),
                "ganancia_neta": float(p_net)
            }
        }

import csv
import io
import json
import logging
from datetime import datetime, time
from decimal import Decimal
from uuid import UUID

from django.db.models import Count, Q
from django.http import HttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from modules.confianza_administracion_seguridad.models import AuditLog
from modules.gestion_usuarios_acceso_suscripcion.models import UserProfile

logger = logging.getLogger(__name__)

SENSITIVE_KEYS = {
    "password",
    "new_password",
    "current_password",
    "token",
    "access_token",
    "refresh_token",
    "authorization",
    "api_key",
    "apikey",
    "secret",
    "private_key",
    "card_number",
    "cvv",
    "cvc",
    "stripe_secret_key",
    "coingate_api_token",
    "firebase_private_key",
}


class AuditService:
    DEFAULT_PAGE_SIZE = 25
    MAX_PAGE_SIZE = 100

    def log_event(
        self,
        *,
        event_type: str,
        event_category: str,
        action: str,
        entity_type: str,
        entity_id: str = "",
        actor_user_id: str = "",
        actor_role: str = "",
        actor_name: str = "",
        actor=None,
        target_user_id: str = "",
        target_role: str = "",
        description: str = "",
        old_values: dict | None = None,
        new_values: dict | None = None,
        metadata: dict | None = None,
        ip_address: str | None = None,
        user_agent: str = "",
        request_id: str = "",
        source: str = "backend",
        severity: str = AuditLog.Severity.INFO,
        status: str = AuditLog.Status.SUCCESS,
        request=None,
        created_at=None,
    ):
        try:
            actor_payload = self._actor_payload(actor)
            actor_user_id = actor_user_id or actor_payload["actor_user_id"]
            actor_role = actor_role or actor_payload["actor_role"]
            actor_name = actor_name or actor_payload["actor_name"]
            request_payload = self._request_payload(request)
            ip_address = ip_address or request_payload["ip_address"]
            user_agent = user_agent or request_payload["user_agent"]
            request_id = request_id or request_payload["request_id"]

            log = AuditLog(
                event_type=str(event_type or "").strip()[:120],
                event_category=str(event_category or AuditLog.Category.SYSTEM).strip()[:40],
                action=str(action or "updated").strip()[:40],
                entity_type=str(entity_type or "system").strip()[:80],
                entity_id=str(entity_id or "")[:128],
                actor_user_id=str(actor_user_id or "")[:128],
                actor_role=str(actor_role or "")[:40],
                actor_name=str(actor_name or "")[:255],
                target_user_id=str(target_user_id or "")[:128],
                target_role=str(target_role or "")[:40],
                description=str(description or ""),
                old_values=mask_sensitive(old_values or {}),
                new_values=mask_sensitive(new_values or {}),
                metadata=mask_sensitive(metadata or {}),
                ip_address=self._clean_ip(ip_address),
                user_agent=str(user_agent or ""),
                request_id=str(request_id or "")[:128],
                source=str(source or "backend")[:60],
                severity=str(severity or AuditLog.Severity.INFO),
                status=str(status or AuditLog.Status.SUCCESS),
            )
            log.save()
            if created_at:
                AuditLog.objects.filter(pk=log.pk).update(created_at=created_at)
                log.created_at = created_at
            return log
        except Exception as exc:
            logger.warning("Audit log skipped for %s: %s", event_type, exc)
            return None

    def list_events(self, query_params):
        queryset = self._apply_filters(AuditLog.objects.all(), query_params)
        queryset = queryset.order_by("-created_at", "-id")
        page = _positive_int(query_params.get("page"), 1)
        page_size = min(_positive_int(query_params.get("page_size"), self.DEFAULT_PAGE_SIZE), self.MAX_PAGE_SIZE)
        total = queryset.count()
        start = (page - 1) * page_size
        end = start + page_size
        items = [serialize_audit_log(item) for item in queryset[start:end]]
        return {
            "items": items,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": max(1, (total + page_size - 1) // page_size),
            },
            "filters": self.available_filters(),
        }

    def get_event(self, audit_id: int):
        log = AuditLog.objects.filter(id=audit_id).first()
        return serialize_audit_log(log, include_details=True) if log else None

    def summary(self, query_params):
        queryset = self._apply_filters(AuditLog.objects.all(), query_params)
        today = timezone.localdate()
        active_actor_ids = queryset.exclude(actor_user_id="").values("actor_user_id").distinct().count()
        return {
            "total_events": queryset.count(),
            "critical_events": queryset.filter(severity=AuditLog.Severity.CRITICAL).count(),
            "failed_events": queryset.filter(status=AuditLog.Status.FAILED).count(),
            "today_events": queryset.filter(created_at__date=today).count(),
            "audited_active_users": active_actor_ids,
            "by_category": _count_map(queryset.values("event_category").annotate(total=Count("id")), "event_category"),
            "by_severity": _count_map(queryset.values("severity").annotate(total=Count("id")), "severity"),
            "by_status": _count_map(queryset.values("status").annotate(total=Count("id")), "status"),
        }

    def export_events(self, query_params):
        queryset = self._apply_filters(AuditLog.objects.all(), query_params).order_by("-created_at", "-id")
        fmt = str(query_params.get("format") or "csv").lower()
        rows = [serialize_audit_log(item, include_details=True) for item in queryset[:5000]]
        if fmt == "json":
            response = HttpResponse(json.dumps(rows, ensure_ascii=False, indent=2, default=str), content_type="application/json")
            response["Content-Disposition"] = 'attachment; filename="homechef-auditoria-general.json"'
            return response

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow([
            "id",
            "created_at",
            "event_type",
            "event_category",
            "action",
            "actor",
            "entity_type",
            "entity_id",
            "description",
            "severity",
            "status",
        ])
        for row in rows:
            writer.writerow([
                row["id"],
                row["created_at"],
                row["event_type"],
                row["event_category"],
                row["action"],
                row["actor_name"] or row["actor_user_id"],
                row["entity_type"],
                row["entity_id"],
                row["description"],
                row["severity"],
                row["status"],
            ])
        response = HttpResponse(buffer.getvalue(), content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="homechef-auditoria-general.csv"'
        return response

    def available_filters(self):
        return {
            "categories": [choice[0] for choice in AuditLog.Category.choices],
            "actions": [
                "created",
                "updated",
                "deleted",
                "login",
                "logout",
                "approved",
                "rejected",
                "blocked",
                "unblocked",
                "assigned",
                "cancelled",
                "delivered",
                "exported",
                "viewed",
                "failed",
            ],
            "severities": [choice[0] for choice in AuditLog.Severity.choices],
            "statuses": [choice[0] for choice in AuditLog.Status.choices],
        }

    def _apply_filters(self, queryset, params):
        date_from = _parse_date_param(params.get("date_from"), end=False)
        date_to = _parse_date_param(params.get("date_to"), end=True)
        if date_from:
            queryset = queryset.filter(created_at__gte=date_from)
        if date_to:
            queryset = queryset.filter(created_at__lte=date_to)
        for key in ("event_category", "action", "severity", "status", "entity_type"):
            value = str(params.get(key) or "").strip()
            if value:
                queryset = queryset.filter(**{key: value})
        actor = str(params.get("actor") or "").strip()
        if actor:
            queryset = queryset.filter(Q(actor_user_id__icontains=actor) | Q(actor_name__icontains=actor) | Q(actor_role__icontains=actor))
        entity = str(params.get("entity") or "").strip()
        if entity:
            queryset = queryset.filter(Q(entity_id__icontains=entity) | Q(entity_type__icontains=entity))
        search = str(params.get("search") or params.get("q") or "").strip()
        if search:
            queryset = queryset.filter(
                Q(event_type__icontains=search)
                | Q(event_category__icontains=search)
                | Q(action__icontains=search)
                | Q(entity_type__icontains=search)
                | Q(entity_id__icontains=search)
                | Q(actor_name__icontains=search)
                | Q(actor_user_id__icontains=search)
                | Q(description__icontains=search)
            )
        return queryset

    def _actor_payload(self, actor):
        if not actor:
            return {"actor_user_id": "", "actor_role": "", "actor_name": ""}
        actor_id = getattr(actor, "supabase_user_id", None) or getattr(actor, "id", "")
        name = getattr(actor, "full_name", "") or f"{getattr(actor, 'first_name', '')} {getattr(actor, 'last_name', '')}".strip()
        return {
            "actor_user_id": str(actor_id or ""),
            "actor_role": str(getattr(actor, "role", "") or ""),
            "actor_name": name or getattr(actor, "email", "") or "",
        }

    def _request_payload(self, request):
        if not request:
            return {"ip_address": None, "user_agent": "", "request_id": ""}
        headers = getattr(request, "headers", {}) or {}
        forwarded = headers.get("x-forwarded-for") or headers.get("X-Forwarded-For") or ""
        ip_address = forwarded.split(",")[0].strip() if forwarded else None
        if not ip_address:
            client_meta = getattr(request, "META", {}) or {}
            ip_address = client_meta.get("REMOTE_ADDR")
        return {
            "ip_address": ip_address,
            "user_agent": headers.get("user-agent") or headers.get("User-Agent") or "",
            "request_id": headers.get("x-request-id") or headers.get("X-Request-ID") or "",
        }

    def _clean_ip(self, value):
        if not value:
            return None
        return str(value).strip()[:64] or None


def serialize_audit_log(log: AuditLog, *, include_details: bool = False):
    if not log:
        return None
    payload = {
        "id": log.id,
        "event_type": log.event_type,
        "event_category": log.event_category,
        "action": log.action,
        "entity_type": log.entity_type,
        "entity_id": log.entity_id,
        "actor_user_id": log.actor_user_id,
        "actor_role": log.actor_role,
        "actor_name": log.actor_name,
        "target_user_id": log.target_user_id,
        "target_role": log.target_role,
        "description": log.description,
        "severity": log.severity,
        "status": log.status,
        "source": log.source,
        "request_id": log.request_id,
        "created_at": log.created_at.isoformat() if log.created_at else None,
    }
    if include_details:
        payload.update({
            "old_values": mask_sensitive(log.old_values or {}),
            "new_values": mask_sensitive(log.new_values or {}),
            "metadata": mask_sensitive(log.metadata or {}),
            "ip_address": log.ip_address,
            "user_agent": log.user_agent,
        })
    return payload


def mask_sensitive(value):
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            if _is_sensitive_key(key):
                cleaned[key] = "***"
            else:
                cleaned[key] = mask_sensitive(item)
        return cleaned
    if isinstance(value, list):
        return [mask_sensitive(item) for item in value]
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if isinstance(value, (Decimal, UUID)):
        return str(value)
    if value.__class__.__name__ == "ObjectId":
        return str(value)
    return value


def _is_sensitive_key(key):
    normalized = str(key).lower().replace("-", "_")
    return normalized in SENSITIVE_KEYS or any(token in normalized for token in ("password", "token", "secret", "api_key", "authorization"))


def _positive_int(value, default):
    try:
        parsed = int(value)
        return parsed if parsed > 0 else default
    except (TypeError, ValueError):
        return default


def _parse_date_param(value, *, end: bool):
    raw = str(value or "").strip()
    if not raw:
        return None
    parsed_dt = parse_datetime(raw)
    if parsed_dt:
        return timezone.make_aware(parsed_dt) if timezone.is_naive(parsed_dt) else parsed_dt
    parsed_date = parse_date(raw)
    if not parsed_date:
        return None
    boundary = time.max if end else time.min
    dt = datetime.combine(parsed_date, boundary)
    return timezone.make_aware(dt)


def _count_map(rows, key):
    return {row[key] or "unknown": row["total"] for row in rows}


def actor_from_user_id(user_id: str):
    try:
        parsed = UUID(str(user_id))
    except (TypeError, ValueError):
        return None
    return UserProfile.objects.filter(supabase_user_id=parsed).first()

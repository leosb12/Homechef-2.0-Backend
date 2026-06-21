import csv
import io
import json
import logging
from datetime import datetime, time, timezone as dt_timezone

from bson import ObjectId
from django.http import HttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from pymongo.errors import PyMongoError

from modules.confianza_administracion_seguridad.services.audit_service import mask_sensitive
from shared.database.mongo_client import get_database

logger = logging.getLogger(__name__)

AI_AUDIT_COLLECTIONS = (
    "ai_requests",
    "ai_inference_audit",
    "publication_quality_reviews",
    "user_manual_chatbot_conversations",
)


class AIAuditService:
    DEFAULT_PAGE_SIZE = 25
    MAX_PAGE_SIZE = 100
    FETCH_LIMIT_PER_COLLECTION = 700

    def list_events(self, query_params):
        events, mongo_status = self._load_normalized_events(query_params)
        events = self._apply_memory_filters(events, query_params)
        events.sort(key=lambda item: item.get("created_at_sort") or datetime.min.replace(tzinfo=dt_timezone.utc), reverse=True)

        page = _positive_int(query_params.get("page"), 1)
        page_size = min(_positive_int(query_params.get("page_size"), self.DEFAULT_PAGE_SIZE), self.MAX_PAGE_SIZE)
        total = len(events)
        start = (page - 1) * page_size
        end = start + page_size
        return {
            "items": [_public_event(item) for item in events[start:end]],
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": max(1, (total + page_size - 1) // page_size),
            },
            "collections": list(AI_AUDIT_COLLECTIONS),
            "mongo": mongo_status,
        }

    def summary(self, query_params):
        events, mongo_status = self._load_normalized_events(query_params)
        events = self._apply_memory_filters(events, query_params)
        total = len(events)
        success = sum(1 for item in events if item["status"] == "success")
        failed = sum(1 for item in events if item["status"] == "failed")
        providers = {}
        latencies = []
        for item in events:
            provider = item.get("provider") or "unknown"
            providers[provider] = providers.get(provider, 0) + 1
            latency = item.get("latency_ms")
            if isinstance(latency, (int, float)):
                latencies.append(float(latency))
        most_used_provider = ""
        if providers:
            most_used_provider = sorted(providers.items(), key=lambda row: row[1], reverse=True)[0][0]
        return {
            "total_ai_queries": total,
            "successful_queries": success,
            "failed_queries": failed,
            "most_used_provider": most_used_provider,
            "average_latency_ms": round(sum(latencies) / len(latencies), 2) if latencies else None,
            "by_provider": providers,
            "by_module": _count_by(events, "module"),
            "collections": list(AI_AUDIT_COLLECTIONS),
            "mongo": mongo_status,
        }

    def get_event(self, event_id: str):
        collection_name, raw_id = _split_event_id(event_id)
        if collection_name not in AI_AUDIT_COLLECTIONS or not raw_id:
            return None
        try:
            db = get_database()
            query_id = ObjectId(raw_id) if ObjectId.is_valid(raw_id) else raw_id
            doc = db[collection_name].find_one({"_id": query_id})
            if not doc:
                return None
            return _public_event(self._normalize_doc(collection_name, doc), include_details=True)
        except (PyMongoError, Exception) as exc:
            logger.warning("Could not fetch AI audit detail %s: %s", event_id, exc)
            return None

    def export_events(self, query_params):
        events, _ = self._load_normalized_events(query_params)
        events = self._apply_memory_filters(events, query_params)
        events.sort(key=lambda item: item.get("created_at_sort") or datetime.min.replace(tzinfo=dt_timezone.utc), reverse=True)
        rows = [_public_event(item, include_details=True) for item in events[:5000]]
        fmt = str(query_params.get("format") or "csv").lower()
        if fmt == "json":
            response = HttpResponse(json.dumps(rows, ensure_ascii=False, indent=2, default=str), content_type="application/json")
            response["Content-Disposition"] = 'attachment; filename="homechef-auditoria-ia.json"'
            return response

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["id", "created_at", "user", "role", "module", "provider", "model", "status", "latency_ms", "prompt"])
        for row in rows:
            writer.writerow([
                row["id"],
                row["created_at"],
                row["user_id"],
                row["user_role"],
                row["module"],
                row["provider"],
                row["model"],
                row["status"],
                row["latency_ms"],
                row["prompt"],
            ])
        response = HttpResponse(buffer.getvalue(), content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="homechef-auditoria-ia.csv"'
        return response

    def _load_normalized_events(self, query_params):
        try:
            db = get_database()
            events = []
            date_filter = _date_filter(query_params)
            for collection_name in AI_AUDIT_COLLECTIONS:
                query = self._mongo_query_for_collection(collection_name, query_params, date_filter)
                cursor = db[collection_name].find(query).sort(_date_field(collection_name), -1).limit(self.FETCH_LIMIT_PER_COLLECTION)
                for doc in cursor:
                    events.append(self._normalize_doc(collection_name, doc))
            return events, {"available": True, "error": ""}
        except (PyMongoError, Exception) as exc:
            logger.warning("AI audit Mongo read failed: %s", exc)
            return [], {"available": False, "error": str(exc)}

    def _mongo_query_for_collection(self, collection_name, params, date_filter):
        query = {}
        if date_filter:
            query[_date_field(collection_name)] = date_filter
        user_value = str(params.get("user") or "").strip()
        if user_value:
            user_fields = {
                "ai_requests": "chef_id",
                "ai_inference_audit": "chef_id",
                "publication_quality_reviews": "chef_id",
                "user_manual_chatbot_conversations": "user_id",
            }
            query[user_fields[collection_name]] = {"$regex": user_value, "$options": "i"}
        return query

    def _apply_memory_filters(self, events, params):
        provider_model = str(params.get("provider_model") or params.get("provider") or "").strip().lower()
        module = str(params.get("module") or "").strip().lower()
        status = str(params.get("status") or "").strip().lower()
        search = str(params.get("search") or params.get("q") or "").strip().lower()

        filtered = []
        for item in events:
            if provider_model and provider_model not in f"{item.get('provider', '')} {item.get('model', '')}".lower():
                continue
            if module and module not in str(item.get("module") or "").lower():
                continue
            if status and status != str(item.get("status") or "").lower():
                continue
            if search:
                haystack = " ".join(
                    str(item.get(key) or "")
                    for key in ("prompt", "response", "error", "module", "provider", "model", "user_id", "session_id", "request_id")
                ).lower()
                if search not in haystack:
                    continue
            filtered.append(item)
        return filtered

    def _normalize_doc(self, collection_name, doc):
        doc = dict(doc)
        raw_id = str(doc.get("_id", ""))
        created_at = _coerce_datetime(
            doc.get("created_at")
            or doc.get("timestamp")
            or doc.get("updated_at")
            or doc.get("fecha_intento")
        )
        if collection_name == "ai_requests":
            input_data = doc.get("input") if isinstance(doc.get("input"), dict) else {}
            output_data = doc.get("output") if isinstance(doc.get("output"), dict) else {}
            prompt = doc.get("prompt") or input_data.get("prompt") or _short_json(input_data)
            response = output_data.get("answer") or output_data.get("description") or output_data.get("recommendation") or _short_json(output_data)
            return _base_event(collection_name, raw_id, created_at, {
                "user_id": doc.get("chef_id"),
                "user_role": "COCINERO",
                "module": doc.get("feature") or "ai_request",
                "provider": doc.get("provider"),
                "model": doc.get("model"),
                "prompt": prompt,
                "response": response,
                "status": _normalize_status(doc.get("status"), doc.get("error_message")),
                "error": doc.get("error_message"),
                "metadata": doc,
            })
        if collection_name == "ai_inference_audit":
            return _base_event(collection_name, raw_id, created_at, {
                "user_id": doc.get("chef_id") or doc.get("user_id"),
                "user_role": doc.get("role") or "",
                "module": doc.get("case_use") or "inference_audit",
                "provider": doc.get("provider_used") or doc.get("source"),
                "model": doc.get("model_version"),
                "prompt": doc.get("prompt") or _short_json(doc.get("input")),
                "response": doc.get("response") or _short_json({k: v for k, v in doc.items() if k in {"detected_ingredients", "risk_score", "confidence"}}),
                "status": _normalize_status(doc.get("status"), doc.get("error")),
                "error": doc.get("error"),
                "latency_ms": doc.get("latency_ms"),
                "metadata": doc,
            })
        if collection_name == "publication_quality_reviews":
            prompt = " | ".join(filter(None, [doc.get("title"), doc.get("description")]))
            response = doc.get("recommendation") or _short_json(doc.get("reasons"))
            return _base_event(collection_name, raw_id, created_at, {
                "user_id": doc.get("chef_id"),
                "user_role": "COCINERO",
                "module": "publication_quality",
                "provider": doc.get("provider_used") or doc.get("source"),
                "model": doc.get("model_version"),
                "endpoint": "/api/v1/ai/publication-quality/analyze",
                "prompt": prompt,
                "response": response,
                "status": "success",
                "metadata": doc,
            })
        return _base_event(collection_name, raw_id, created_at, {
            "user_id": doc.get("user_id"),
            "user_role": doc.get("role"),
            "module": doc.get("related_module") or "user_manual_chatbot",
            "provider": doc.get("provider_used") or doc.get("source"),
            "model": doc.get("model") or doc.get("mode"),
            "endpoint": "/api/v1/ai/user-manual-chatbot/ask",
            "prompt": doc.get("message"),
            "response": doc.get("answer"),
            "status": "failed" if doc.get("fallback_reason") == "error" else "success",
            "latency_ms": doc.get("latency_ms"),
            "session_id": doc.get("session_id"),
            "metadata": doc,
        })


def _base_event(collection_name, raw_id, created_at, values):
    metadata = mask_sensitive(values.get("metadata") or {})
    return {
        "id": f"{collection_name}:{raw_id}",
        "mongo_id": raw_id,
        "collection": collection_name,
        "created_at": created_at.isoformat() if created_at else None,
        "created_at_sort": created_at,
        "user_id": str(values.get("user_id") or ""),
        "user_role": str(values.get("user_role") or ""),
        "module": str(values.get("module") or ""),
        "provider": str(values.get("provider") or ""),
        "model": str(values.get("model") or ""),
        "endpoint": str(values.get("endpoint") or ""),
        "prompt": _truncate(values.get("prompt"), 1200),
        "response": _truncate(values.get("response"), 2000),
        "status": values.get("status") or "success",
        "error": str(values.get("error") or ""),
        "tokens": values.get("tokens"),
        "latency_ms": values.get("latency_ms"),
        "request_id": str(values.get("request_id") or metadata.get("request_id") or ""),
        "session_id": str(values.get("session_id") or metadata.get("session_id") or ""),
        "ip_address": str(values.get("ip_address") or metadata.get("ip_address") or ""),
        "user_agent": str(values.get("user_agent") or metadata.get("user_agent") or ""),
        "metadata": metadata,
    }


def _public_event(item, *, include_details=False):
    payload = {key: value for key, value in item.items() if key not in {"created_at_sort", "metadata"}}
    if include_details:
        payload["metadata"] = item.get("metadata") or {}
    return payload


def _date_filter(params):
    date_from = _parse_date_param(params.get("date_from"), end=False)
    date_to = _parse_date_param(params.get("date_to"), end=True)
    date_query = {}
    if date_from:
        date_query["$gte"] = date_from
    if date_to:
        date_query["$lte"] = date_to
    return date_query


def _date_field(collection_name):
    return "timestamp" if collection_name == "user_manual_chatbot_conversations" else "created_at"


def _parse_date_param(value, *, end):
    raw = str(value or "").strip()
    if not raw:
        return None
    parsed_dt = parse_datetime(raw)
    if parsed_dt:
        return timezone.make_aware(parsed_dt) if timezone.is_naive(parsed_dt) else parsed_dt
    parsed_date = parse_date(raw)
    if not parsed_date:
        return None
    dt = datetime.combine(parsed_date, time.max if end else time.min)
    return timezone.make_aware(dt)


def _coerce_datetime(value):
    if isinstance(value, datetime):
        return timezone.make_aware(value) if timezone.is_naive(value) else value
    parsed = parse_datetime(str(value or ""))
    if parsed:
        return timezone.make_aware(parsed) if timezone.is_naive(parsed) else parsed
    return None


def _normalize_status(value, error=None):
    raw = str(value or "").lower()
    if error or raw in {"failed", "failure", "error", "errored"}:
        return "failed"
    return "success"


def _short_json(value):
    if value in (None, "", [], {}):
        return ""
    try:
        return json.dumps(mask_sensitive(value), ensure_ascii=False, default=str)[:1200]
    except TypeError:
        return str(value)[:1200]


def _truncate(value, max_len):
    text = str(value or "")
    return text if len(text) <= max_len else f"{text[:max_len]}..."


def _positive_int(value, default):
    try:
        parsed = int(value)
        return parsed if parsed > 0 else default
    except (TypeError, ValueError):
        return default


def _count_by(events, key):
    counts = {}
    for item in events:
        value = item.get(key) or "unknown"
        counts[value] = counts.get(value, 0) + 1
    return counts


def _split_event_id(event_id):
    if ":" not in str(event_id):
        return "", ""
    collection_name, raw_id = str(event_id).split(":", 1)
    return collection_name, raw_id

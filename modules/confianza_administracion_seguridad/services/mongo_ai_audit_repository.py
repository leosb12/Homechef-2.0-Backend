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
from shared.database.mongo_client import MongoConfigurationError, get_database

logger = logging.getLogger(__name__)


AI_AUDIT_COLLECTION_DEFINITIONS = (
    {
        "collection": "user_manual_chatbot_conversations",
        "audit_type": "Manual / Chatbot",
        "date_fields": ("timestamp", "created_at", "createdAt", "updated_at", "updatedAt"),
    },
    {
        "collection": "ai_inference_audit",
        "audit_type": "Inferencias IA",
        "date_fields": ("timestamp", "created_at", "createdAt", "updated_at", "updatedAt"),
    },
    {
        "collection": "ai_requests",
        "audit_type": "Requests IA",
        "date_fields": ("timestamp", "created_at", "createdAt", "updated_at", "updatedAt"),
    },
    {
        "collection": "ai_model_status",
        "audit_type": "Estado de Modelos",
        "date_fields": ("last_updated", "updated_at", "updatedAt", "created_at", "createdAt", "timestamp"),
    },
    {
        "collection": "ai_offline_test_results",
        "audit_type": "Pruebas Offline",
        "date_fields": ("timestamp", "created_at", "createdAt", "updated_at", "updatedAt"),
    },
    {
        "collection": "ai_training_reports",
        "audit_type": "Entrenamientos",
        "date_fields": ("timestamp", "created_at", "createdAt", "updated_at", "updatedAt", "finished_at", "started_at"),
    },
    {
        "collection": "ai_dataset_metadata",
        "audit_type": "Datasets",
        "date_fields": ("updated_at", "updatedAt", "created_at", "createdAt", "timestamp"),
    },
    {
        "collection": "publication_quality_reviews",
        "audit_type": "Calidad Publicaciones",
        "date_fields": ("timestamp", "created_at", "createdAt", "updated_at", "updatedAt"),
    },
)

AI_AUDIT_COLLECTIONS = tuple(item["collection"] for item in AI_AUDIT_COLLECTION_DEFINITIONS)
COLLECTION_BY_NAME = {item["collection"]: item for item in AI_AUDIT_COLLECTION_DEFINITIONS}
COLLECTION_BY_TYPE = {item["audit_type"].lower(): item for item in AI_AUDIT_COLLECTION_DEFINITIONS}


class MongoAIAuditRepository:
    DEFAULT_PAGE_SIZE = 25
    MAX_PAGE_SIZE = 100
    FETCH_LIMIT_PER_COLLECTION = 900

    def list_collections(self):
        db, mongo_status = self._database_or_none()
        available = set()
        if db is not None:
            try:
                available = set(db.list_collection_names())
            except PyMongoError as exc:
                logger.warning("AI audit collection listing failed: %s", exc.__class__.__name__)
                mongo_status = _mongo_error_status("No se pudo conectar a MongoDB Atlas para auditoria IA.")

        items = []
        for definition in AI_AUDIT_COLLECTION_DEFINITIONS:
            name = definition["collection"]
            count = None
            exists = name in available
            if db is not None and exists:
                try:
                    count = db[name].estimated_document_count()
                except PyMongoError:
                    count = None
            items.append({
                "collection": name,
                "audit_type": definition["audit_type"],
                "label": definition["audit_type"],
                "exists": exists,
                "count": count,
            })
        return {"items": items, "mongo": mongo_status}

    def list_events(self, query_params):
        events, mongo_status = self._load_events(query_params)
        events = self._apply_filters(events, query_params)
        events.sort(key=_sort_key, reverse=True)

        page = _positive_int(_param(query_params, "page"), 1)
        page_size = min(
            _positive_int(_param(query_params, "page_size", "limit"), self.DEFAULT_PAGE_SIZE),
            self.MAX_PAGE_SIZE,
        )
        total = len(events)
        start = (page - 1) * page_size
        end = start + page_size
        return {
            "items": [_public_event(item) for item in events[start:end]],
            "pagination": {
                "page": page,
                "page_size": page_size,
                "limit": page_size,
                "total": total,
                "total_pages": max(1, (total + page_size - 1) // page_size),
            },
            "collections": self.list_collections()["items"],
            "mongo": mongo_status,
        }

    def summary(self, query_params):
        events, mongo_status = self._load_events(query_params)
        events = self._apply_filters(events, query_params)
        total = len(events)
        success = sum(1 for item in events if item.get("status") == "success")
        failed = sum(1 for item in events if item.get("status") == "failed")
        providers = _count_values(events, "provider", skip_empty=True)
        latencies = [
            float(item["latency_ms"])
            for item in events
            if isinstance(item.get("latency_ms"), (int, float))
        ]
        return {
            "total_ai_queries": total,
            "successful_queries": success,
            "failed_queries": failed,
            "most_used_provider": _top_key(providers),
            "average_latency_ms": round(sum(latencies) / len(latencies), 2) if latencies else None,
            "by_provider": providers,
            "by_module": _count_values(events, "module"),
            "by_audit_type": _count_values(events, "audit_type"),
            "by_collection": _count_values(events, "source_collection"),
            "collections": self.list_collections()["items"],
            "mongo": mongo_status,
        }

    def get_event(self, event_id):
        collection_name, raw_id = _split_event_id(event_id)
        if collection_name not in COLLECTION_BY_NAME or not raw_id:
            return None

        db, mongo_status = self._database_or_none()
        if db is None or not mongo_status.get("available"):
            return None

        try:
            query_id = ObjectId(raw_id) if ObjectId.is_valid(raw_id) else raw_id
            doc = db[collection_name].find_one({"_id": query_id})
            if doc is None:
                doc = db[collection_name].find_one({"_id": raw_id})
            if not doc:
                return None
            return _public_event(self._normalize_doc(collection_name, doc), include_details=True)
        except PyMongoError as exc:
            logger.warning("AI audit detail read failed: %s", exc.__class__.__name__)
            return None

    def export_events(self, query_params):
        events, _ = self._load_events(query_params)
        events = self._apply_filters(events, query_params)
        events.sort(key=_sort_key, reverse=True)
        rows = [_public_event(item, include_details=True) for item in events[:5000]]
        fmt = str(_param(query_params, "format") or "csv").lower()
        if fmt == "json":
            response = HttpResponse(
                json.dumps(rows, ensure_ascii=False, indent=2, default=str),
                content_type="application/json",
            )
            response["Content-Disposition"] = 'attachment; filename="homechef-auditoria-ia.json"'
            return response

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow([
            "id",
            "source_collection",
            "audit_type",
            "timestamp",
            "user_id",
            "role",
            "module",
            "provider",
            "model",
            "prompt",
            "response",
            "status",
            "latency_ms",
            "confidence",
        ])
        for row in rows:
            writer.writerow([
                row.get("id"),
                row.get("source_collection"),
                row.get("audit_type"),
                row.get("timestamp") or row.get("created_at"),
                row.get("user_id"),
                row.get("role") or row.get("user_role"),
                row.get("module"),
                row.get("provider"),
                row.get("model"),
                row.get("prompt"),
                row.get("response"),
                row.get("status"),
                row.get("latency_ms"),
                row.get("confidence"),
            ])
        response = HttpResponse(buffer.getvalue(), content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="homechef-auditoria-ia.csv"'
        return response

    def _load_events(self, params):
        db, mongo_status = self._database_or_none()
        if db is None or not mongo_status.get("available"):
            return [], mongo_status

        try:
            available = set(db.list_collection_names())
            selected_collections = self._selected_collections(params)
            events = []
            for collection_name in selected_collections:
                if collection_name not in available:
                    continue
                definition = COLLECTION_BY_NAME[collection_name]
                sort_field = definition["date_fields"][0]
                cursor = (
                    db[collection_name]
                    .find({})
                    .sort([(sort_field, -1), ("_id", -1)])
                    .limit(self.FETCH_LIMIT_PER_COLLECTION)
                )
                for doc in cursor:
                    events.append(self._normalize_doc(collection_name, doc))
            return events, mongo_status
        except (MongoConfigurationError, PyMongoError) as exc:
            logger.warning("AI audit Mongo read failed: %s", exc.__class__.__name__)
            return [], _mongo_error_status("No se pudo conectar a MongoDB Atlas para auditoria IA.")

    def _database_or_none(self):
        try:
            db = get_database()
            db.command("ping")
            return db, {
                "available": True,
                "configured": True,
                "database": db.name,
                "error": "",
                "message": f"MongoDB Atlas configurado correctamente para base {db.name}.",
            }
        except MongoConfigurationError as exc:
            return None, {
                "available": False,
                "configured": False,
                "database": "homechef_ia",
                "error": str(exc),
            }
        except PyMongoError as exc:
            logger.warning("AI audit Mongo connection failed: %s", exc.__class__.__name__)
            return None, _mongo_error_status("No se pudo conectar a MongoDB Atlas para auditoria IA.")

    def _selected_collections(self, params):
        collection = str(_param(params, "collection") or "").strip()
        if collection and collection in COLLECTION_BY_NAME:
            return (collection,)

        audit_type = str(_param(params, "audit_type") or "").strip().lower()
        if audit_type and audit_type != "todo" and audit_type in COLLECTION_BY_TYPE:
            return (COLLECTION_BY_TYPE[audit_type]["collection"],)

        return AI_AUDIT_COLLECTIONS

    def _apply_filters(self, events, params):
        date_from = _parse_date_param(_param(params, "from_date", "date_from"), end=False)
        date_to = _parse_date_param(_param(params, "to_date", "date_to"), end=True)
        provider = _lower(_param(params, "provider", "provider_model"))
        model = _lower(_param(params, "model"))
        module = _lower(_param(params, "module"))
        user_id = _lower(_param(params, "user_id", "user"))
        role = _lower(_param(params, "role"))
        status = _lower(_param(params, "status"))
        search = _lower(_param(params, "search", "q"))

        filtered = []
        for item in events:
            timestamp = item.get("created_at_sort")
            if date_from and (not timestamp or timestamp < date_from):
                continue
            if date_to and (not timestamp or timestamp > date_to):
                continue
            if provider and provider not in f"{item.get('provider', '')} {item.get('model_provider', '')}".lower():
                continue
            if model and model not in str(item.get("model") or "").lower():
                continue
            if module and module not in str(item.get("module") or "").lower():
                continue
            if user_id and user_id not in str(item.get("user_id") or "").lower():
                continue
            if role and role not in str(item.get("role") or item.get("user_role") or "").lower():
                continue
            if status and status != str(item.get("status") or "").lower():
                continue
            if search and search not in _search_haystack(item):
                continue
            filtered.append(item)
        return filtered

    def _normalize_doc(self, collection_name, doc):
        if collection_name == "user_manual_chatbot_conversations":
            return _manual_chatbot_event(collection_name, doc)
        if collection_name == "ai_inference_audit":
            return _inference_event(collection_name, doc)
        if collection_name == "ai_requests":
            return _request_event(collection_name, doc)
        if collection_name == "ai_model_status":
            return _model_status_event(collection_name, doc)
        if collection_name == "ai_offline_test_results":
            return _offline_test_event(collection_name, doc)
        if collection_name == "ai_training_reports":
            return _training_report_event(collection_name, doc)
        if collection_name == "ai_dataset_metadata":
            return _dataset_metadata_event(collection_name, doc)
        if collection_name == "publication_quality_reviews":
            return _publication_quality_event(collection_name, doc)
        return _generic_event(collection_name, doc)


def _manual_chatbot_event(collection_name, doc):
    return _base_event(collection_name, doc, {
        "user_id": _pick(doc, "user_id"),
        "role": _pick(doc, "role"),
        "module": _pick(doc, "related_module", "detected_intent") or "Manual / Chatbot",
        "provider": _pick(doc, "provider_used", "provider", "source", "model_provider"),
        "model": _pick(doc, "model", "mode"),
        "endpoint": "/api/v1/ai/user-manual-chatbot/ask",
        "prompt": _pick(doc, "message"),
        "response": _pick(doc, "answer"),
        "status": _status_from(doc, fallback=_pick(doc, "fallback_reason"), response=_pick(doc, "answer")),
        "latency_ms": _latency_from(doc),
        "confidence": _pick(doc, "confidence"),
        "grounded": _pick(doc, "grounded"),
        "offline_ready": _pick(doc, "offline_ready"),
        "fallback_reason": _pick(doc, "fallback_reason"),
        "session_id": _pick(doc, "session_id"),
        "current_route": _pick(doc, "current_route"),
        "current_screen": _pick(doc, "current_screen"),
        "detected_intent": _pick(doc, "detected_intent"),
        "related_use_cases": _pick(doc, "related_use_cases"),
        "platform": _pick(doc, "platform"),
    })


def _inference_event(collection_name, doc):
    prompt = _pick(doc, "prompt", "input.prompt", "input", "input_data", "request")
    response = _pick(doc, "response", "answer", "output.answer", "output", "result")
    return _base_event(collection_name, doc, {
        "user_id": _pick(doc, "user_id", "chef_id", "actor_user_id"),
        "role": _pick(doc, "role", "user_role", "actor_role"),
        "module": _pick(doc, "module", "case_use", "use_case", "feature", "endpoint") or "Inferencias IA",
        "provider": _pick(doc, "provider_used", "provider", "source", "model_provider"),
        "model": _pick(doc, "model", "model_version", "model_name"),
        "endpoint": _pick(doc, "endpoint", "route", "path"),
        "prompt": _summary_text(prompt),
        "response": _summary_text(response or _subset(doc, ("detected_ingredients", "risk_score", "confidence", "prediction"))),
        "status": _status_from(doc, error=_pick(doc, "error", "error_message")),
        "error": _pick(doc, "error", "error_message"),
        "latency_ms": _latency_from(doc),
        "confidence": _pick(doc, "confidence", "score"),
        "tokens": _pick(doc, "tokens", "total_tokens", "usage.total_tokens", "usage"),
        "request_id": _pick(doc, "request_id"),
        "session_id": _pick(doc, "session_id"),
    })


def _request_event(collection_name, doc):
    input_data = _pick(doc, "prompt", "message", "input.prompt", "input", "request", "payload")
    output_data = _pick(doc, "response", "answer", "output.answer", "output.description", "output.recommendation", "output", "result")
    return _base_event(collection_name, doc, {
        "user_id": _pick(doc, "user_id", "chef_id"),
        "role": _pick(doc, "role", "user_role") or "COCINERO",
        "module": _pick(doc, "module", "feature", "case_use", "use_case", "endpoint") or "Requests IA",
        "provider": _pick(doc, "provider_used", "provider", "source", "model_provider"),
        "model": _pick(doc, "model", "model_version", "model_name"),
        "endpoint": _pick(doc, "endpoint", "route", "path", "case_use", "feature"),
        "prompt": _summary_text(input_data),
        "response": _summary_text(output_data),
        "status": _status_from(doc, error=_pick(doc, "error", "error_message"), response=output_data),
        "error": _pick(doc, "error", "error_message"),
        "latency_ms": _latency_from(doc),
        "tokens": _pick(doc, "tokens", "total_tokens", "usage.total_tokens", "usage"),
        "request_id": _pick(doc, "request_id"),
        "session_id": _pick(doc, "session_id"),
    })


def _model_status_event(collection_name, doc):
    metrics = _pick(doc, "metrics", "accuracy", "score", "scores")
    status = _pick(doc, "status", "state", "health")
    return _base_event(collection_name, doc, {
        "module": _pick(doc, "module", "related_module", "use_case") or "Estado de Modelos",
        "provider": _pick(doc, "provider_used", "provider", "source", "model_provider"),
        "model": _pick(doc, "model", "model_name", "name", "model_id"),
        "model_type": _pick(doc, "type", "model_type", "task_type"),
        "model_version": _pick(doc, "version", "model_version"),
        "prompt": _summary_text({
            "modelo": _pick(doc, "model", "model_name", "name", "model_id"),
            "tipo": _pick(doc, "type", "model_type", "task_type"),
            "version": _pick(doc, "version", "model_version"),
        }),
        "response": _summary_text(metrics or status),
        "status": _status_from(doc),
        "latency_ms": _latency_from(doc),
        "confidence": _pick(doc, "accuracy", "score"),
        "offline_ready": _pick(doc, "offline_ready"),
    })


def _offline_test_event(collection_name, doc):
    passed = _pick(doc, "passed", "success")
    result = _pick(doc, "result", "output", "summary")
    return _base_event(collection_name, doc, {
        "module": _pick(doc, "module", "related_module", "use_case") or "Pruebas Offline",
        "provider": _pick(doc, "provider_used", "provider", "source"),
        "model": _pick(doc, "model", "model_version", "model_name"),
        "prompt": _summary_text(_pick(doc, "test", "test_name", "name", "scenario")),
        "response": _summary_text(result),
        "status": _status_from(doc, passed=passed, error=_pick(doc, "error", "error_message")),
        "error": _pick(doc, "error", "error_message"),
        "latency_ms": _latency_from(doc),
        "confidence": _pick(doc, "accuracy", "score"),
    })


def _training_report_event(collection_name, doc):
    metrics = _pick(doc, "metrics", "evaluation", "scores")
    files = _pick(doc, "generated_files", "artifacts", "files")
    return _base_event(collection_name, doc, {
        "module": _pick(doc, "module", "related_module", "use_case") or "Entrenamientos",
        "provider": _pick(doc, "provider_used", "provider", "source"),
        "model": _pick(doc, "model", "model_name", "model_version"),
        "model_version": _pick(doc, "model_version", "version"),
        "dataset": _pick(doc, "dataset", "dataset_name", "dataset_version"),
        "prompt": _summary_text({
            "dataset": _pick(doc, "dataset", "dataset_name", "dataset_version"),
            "registros": _pick(doc, "record_count", "rows", "samples", "dataset_size"),
            "clases": _pick(doc, "classes", "labels"),
        }),
        "response": _summary_text(metrics or files or _pick(doc, "result", "status")),
        "status": _status_from(doc, error=_pick(doc, "error", "error_message")),
        "error": _pick(doc, "error", "error_message"),
        "latency_ms": _latency_from(doc),
        "confidence": _pick(doc, "accuracy", "score", "metrics.accuracy"),
    })


def _dataset_metadata_event(collection_name, doc):
    dataset = _pick(doc, "dataset", "dataset_name", "name", "dataset_version")
    return _base_event(collection_name, doc, {
        "module": _pick(doc, "module", "related_module", "use_case") or "Datasets",
        "model": dataset,
        "dataset": dataset,
        "prompt": _summary_text({
            "dataset": dataset,
            "registros": _pick(doc, "record_count", "rows", "samples", "total_records", "dataset_size"),
            "clases": _pick(doc, "classes", "labels"),
            "fuente": _pick(doc, "source", "data_source"),
        }),
        "response": _summary_text(_pick(doc, "metadata", "schema", "fields", "description")),
        "status": _status_from(doc),
    })


def _publication_quality_event(collection_name, doc):
    prompt = " | ".join(
        str(value)
        for value in (_pick(doc, "title", "publication_title", "dish_name"), _pick(doc, "description"))
        if _present(value)
    )
    response = _pick(doc, "recommendation", "result", "status", "observations", "reasons")
    return _base_event(collection_name, doc, {
        "user_id": _pick(doc, "user_id", "chef_id"),
        "role": _pick(doc, "role") or "COCINERO",
        "module": "Calidad de Publicaciones",
        "provider": _pick(doc, "provider_used", "provider", "source", "model_provider"),
        "model": _pick(doc, "model", "model_version", "model_name"),
        "endpoint": _pick(doc, "endpoint") or "/api/v1/ai/publication-quality/analyze",
        "entity_id": _pick(doc, "publication_id", "dish_id"),
        "prompt": prompt or _summary_text(_pick(doc, "input", "publication")),
        "response": _summary_text(response),
        "status": _status_from(doc, error=_pick(doc, "error", "error_message"), response=response),
        "error": _pick(doc, "error", "error_message"),
        "latency_ms": _latency_from(doc),
        "confidence": _pick(doc, "score", "quality_score", "confidence"),
    })


def _generic_event(collection_name, doc):
    return _base_event(collection_name, doc, {
        "module": collection_name,
        "provider": _pick(doc, "provider_used", "provider", "source", "model_provider"),
        "model": _pick(doc, "model", "model_version", "model_name"),
        "prompt": _summary_text(_pick(doc, "prompt", "message", "input", "request")),
        "response": _summary_text(_pick(doc, "response", "answer", "output", "result")),
        "status": _status_from(doc),
        "latency_ms": _latency_from(doc),
    })


def _base_event(collection_name, doc, values):
    definition = COLLECTION_BY_NAME[collection_name]
    raw_id = str(doc.get("_id", ""))
    timestamp = _date_from_doc(doc, definition)
    original = mask_sensitive(doc)
    metadata = _metadata_from(original, values)
    role = _string_or_empty(values.get("role"))
    provider = _string_or_empty(values.get("provider"))
    model = _string_or_empty(values.get("model"))
    return {
        "id": f"{collection_name}:{raw_id}",
        "mongo_id": raw_id,
        "source_collection": collection_name,
        "collection": collection_name,
        "audit_type": definition["audit_type"],
        "created_at": timestamp.isoformat() if timestamp else None,
        "timestamp": timestamp.isoformat() if timestamp else None,
        "created_at_sort": timestamp,
        "user_id": _string_or_empty(values.get("user_id")),
        "role": role,
        "user_role": role,
        "module": _string_or_empty(values.get("module")),
        "provider": provider,
        "model_provider": provider,
        "model": model,
        "endpoint": _string_or_empty(values.get("endpoint")),
        "entity_id": _string_or_empty(values.get("entity_id")),
        "prompt": _truncate(_summary_text(values.get("prompt")), 1600),
        "response": _truncate(_summary_text(values.get("response")), 2200),
        "status": values.get("status") or "success",
        "error": _string_or_empty(values.get("error")),
        "tokens": _json_safe(values.get("tokens")),
        "latency_ms": _latency_value(values.get("latency_ms")),
        "confidence": _json_safe(values.get("confidence")),
        "grounded": _json_safe(values.get("grounded")),
        "offline_ready": _json_safe(values.get("offline_ready")),
        "fallback_reason": _json_safe(values.get("fallback_reason")),
        "request_id": _string_or_empty(values.get("request_id") or _pick(original, "request_id")),
        "session_id": _string_or_empty(values.get("session_id") or _pick(original, "session_id")),
        "current_route": _string_or_empty(values.get("current_route")),
        "current_screen": _string_or_empty(values.get("current_screen")),
        "detected_intent": _string_or_empty(values.get("detected_intent")),
        "related_use_cases": _json_safe(values.get("related_use_cases")),
        "platform": _string_or_empty(values.get("platform")),
        "metadata": metadata,
        "original_document": original,
    }


def _public_event(item, *, include_details=False):
    hidden = {"created_at_sort", "metadata", "original_document"}
    payload = {key: value for key, value in item.items() if key not in hidden}
    if include_details:
        payload["metadata"] = item.get("metadata") or {}
        payload["original_document"] = item.get("original_document") or {}
    return payload


def _date_from_doc(doc, definition):
    for field in definition["date_fields"]:
        value = _pick(doc, field)
        parsed = _coerce_datetime(value)
        if parsed:
            return parsed
    raw_id = doc.get("_id")
    if isinstance(raw_id, ObjectId):
        return raw_id.generation_time
    return None


def _coerce_datetime(value):
    if isinstance(value, datetime):
        return timezone.make_aware(value) if timezone.is_naive(value) else value
    if isinstance(value, (int, float)):
        try:
            seconds = value / 1000 if value > 10_000_000_000 else value
            return datetime.fromtimestamp(seconds, tz=dt_timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    raw = str(value or "").strip()
    if not raw:
        return None
    parsed_dt = parse_datetime(raw)
    if parsed_dt:
        return timezone.make_aware(parsed_dt) if timezone.is_naive(parsed_dt) else parsed_dt
    parsed_date = parse_date(raw)
    if parsed_date:
        return timezone.make_aware(datetime.combine(parsed_date, time.min))
    return None


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
    boundary = time.max if end else time.min
    return timezone.make_aware(datetime.combine(parsed_date, boundary))


def _status_from(doc, *, fallback=None, error=None, response=None, passed=None):
    if passed is not None:
        return "success" if bool(passed) else "failed"
    error_value = error if error is not None else _pick(doc, "error", "error_message", "exception")
    if _present(error_value):
        return "failed"
    fallback_value = fallback if fallback is not None else _pick(doc, "fallback_reason")
    if str(fallback_value or "").lower() in {"error", "critical", "exception", "failed", "failure"}:
        return "failed"
    raw = str(_pick(doc, "status", "state", "result_status", "health") or "").lower()
    if raw in {"failed", "failure", "error", "errored", "unhealthy", "down", "rejected"}:
        return "failed"
    if raw in {"success", "ok", "completed", "complete", "passed", "healthy", "ready", "active", "approved"}:
        return "success"
    if response is not None and _present(response):
        return "success"
    return "success"


def _latency_from(doc):
    return _latency_value(_pick(doc, "latency_ms", "latency", "duration_ms", "response_time_ms", "elapsed_ms"))


def _latency_value(value):
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    raw = str(value).strip().lower().replace("ms", "")
    if raw.endswith("s"):
        try:
            return round(float(raw[:-1]) * 1000, 2)
        except ValueError:
            return None
    try:
        return round(float(raw), 2)
    except ValueError:
        return None


def _metadata_from(original, values):
    excluded = {
        "_id",
        "user_id",
        "chef_id",
        "role",
        "message",
        "answer",
        "prompt",
        "response",
        "input",
        "output",
        "error",
        "error_message",
        "timestamp",
        "created_at",
        "createdAt",
        "updated_at",
        "updatedAt",
    }
    metadata = {key: value for key, value in original.items() if key not in excluded}
    for key in (
        "current_route",
        "current_screen",
        "detected_intent",
        "related_use_cases",
        "platform",
        "grounded",
        "offline_ready",
        "fallback_reason",
        "dataset",
        "model_type",
        "model_version",
    ):
        if _present(values.get(key)):
            metadata[key] = _json_safe(values.get(key))
    return mask_sensitive(metadata)


def _pick(doc, *paths):
    for path in paths:
        value = _path_value(doc, path)
        if _present(value):
            return value
    return None


def _path_value(doc, path):
    current = doc
    for part in str(path).split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _subset(doc, keys):
    values = {}
    for key in keys:
        value = _pick(doc, key)
        if _present(value):
            values[key] = value
    return values


def _present(value):
    return value is not None and value != "" and value != [] and value != {}


def _summary_text(value):
    value = _json_safe(value)
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def _json_safe(value):
    return mask_sensitive(value)


def _truncate(value, max_len):
    text = str(value or "")
    return text if len(text) <= max_len else f"{text[:max_len]}..."


def _string_or_empty(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return _summary_text(value)
    return str(value)


def _param(params, *names):
    for name in names:
        value = params.get(name) if hasattr(params, "get") else None
        if value not in (None, ""):
            return value
    return None


def _positive_int(value, default):
    try:
        parsed = int(value)
        return parsed if parsed > 0 else default
    except (TypeError, ValueError):
        return default


def _lower(value):
    return str(value or "").strip().lower()


def _search_haystack(item):
    keys = (
        "prompt",
        "response",
        "error",
        "module",
        "provider",
        "model",
        "user_id",
        "role",
        "session_id",
        "request_id",
        "audit_type",
        "source_collection",
        "detected_intent",
        "current_route",
        "current_screen",
    )
    return " ".join(str(item.get(key) or "") for key in keys).lower()


def _count_values(events, key, *, skip_empty=False):
    counts = {}
    for item in events:
        value = item.get(key) or "unknown"
        if skip_empty and value == "unknown":
            continue
        counts[value] = counts.get(value, 0) + 1
    return counts


def _top_key(counts):
    if not counts:
        return ""
    return sorted(counts.items(), key=lambda row: row[1], reverse=True)[0][0]


def _sort_key(item):
    return item.get("created_at_sort") or datetime.min.replace(tzinfo=dt_timezone.utc)


def _split_event_id(event_id):
    if ":" not in str(event_id):
        return "", ""
    collection_name, raw_id = str(event_id).split(":", 1)
    return collection_name, raw_id


def _mongo_error_status(message):
    return {
        "available": False,
        "configured": True,
        "database": "homechef_ia",
        "error": message,
    }

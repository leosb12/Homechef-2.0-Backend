from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from modules.delivery_logistica.models import (
    DeliveryAssignment,
    DeliveryAssignmentOffer,
    DeliveryStatusHistory,
)
from modules.gestion_usuarios_acceso_suscripcion.models import DeliveryProfile, UserProfile
from modules.pedidos_checkout_pagos.models import Order
from modules.pedidos_checkout_pagos.realtime import publish_order_tracking_refresh
from modules.pedidos_checkout_pagos.models import OrderTimelineEvent

from .delivery_assignment_engine import DeliveryAssignmentEngine
from .delivery_availability_service import DeliveryAvailabilityService


class DeliveryOfferError(ValueError):
    def __init__(self, message: str, code: str, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}


class DeliveryOfferService:
    OFFER_TTL_SECONDS = 120
    SECOND_OFFER_DELAY_SECONDS = 60
    SECOND_SEARCH_DELAY_SECONDS = 180
    CAPACITY_LIMIT = 3

    STATE_IDLE = "IDLE"
    STATE_OFFER_PENDING = "OFFER_PENDING"
    STATE_WAITING_ROUND_2 = "WAITING_ROUND_2"
    STATE_OPEN_BOARD = "OPEN_BOARD"
    STATE_ASSIGNED = "ASSIGNED"
    FLOW_AUDIT_LIMIT = 30

    OPEN_BOARD_KEY = "open_board_enabled"

    def __init__(self):
        self.assignment_engine = DeliveryAssignmentEngine()
        self.availability_service = DeliveryAvailabilityService()

    def list_pending_for_delivery(self, user_id: str):
        delivery = self._require_delivery(user_id)
        self.sync_open_assignments()
        offers = (
            DeliveryAssignmentOffer.objects.filter(
                delivery_user=delivery,
                status=DeliveryAssignmentOffer.Status.PENDING,
            )
            .select_related(
                "assignment",
                "assignment__order",
                "assignment__order__client",
                "assignment__order__chef",
            )
            .prefetch_related("assignment__order__address")
            .order_by("expires_at", "offered_at")
        )
        items = [self._serialize_offer(row) for row in offers if self._offer_is_active(row)]
        return {"items": items}

    def list_open_board(self, user_id: str):
        self._require_delivery(user_id)
        self.sync_open_assignments()
        assignments = (
            DeliveryAssignment.objects.filter(
                order__fulfillment_type=Order.FulfillmentType.DELIVERY,
                order__status=Order.Status.READY_FOR_DELIVERY,
                status=DeliveryAssignment.Status.UNASSIGNED,
                delivery_user__isnull=True,
            )
            .filter(metadata__open_board_enabled=True)
            .select_related(
                "order",
                "order__client",
                "order__chef",
            )
            .prefetch_related(
                "order__address",
                "order__items",
                "order__payments",
                "status_history",
                "incidents",
                "location_pings",
                "route_snapshots",
            )
            .order_by("-updated_at")
        )
        return {"items": [self._serialize_open_board_assignment(row) for row in assignments]}

    def sync_open_assignments(self):
        assignments = (
            DeliveryAssignment.objects.filter(
                order__fulfillment_type=Order.FulfillmentType.DELIVERY,
                order__status=Order.Status.READY_FOR_DELIVERY,
                status=DeliveryAssignment.Status.UNASSIGNED,
                delivery_user__isnull=True,
            )
            .select_related("order", "order__client", "order__chef")
            .prefetch_related("order__address")
            .order_by("-updated_at")
        )
        for assignment in assignments:
            self.ensure_offer_up_to_date(assignment, reason="offer_board_sync")

    @transaction.atomic
    def ensure_offer_up_to_date(self, assignment: DeliveryAssignment, *, reason: str):
        assignment = (
            DeliveryAssignment.objects.select_for_update()
            .select_related("order", "order__client", "order__chef", "delivery_user")
            .prefetch_related("order__address")
            .get(pk=assignment.pk)
        )
        metadata = dict(assignment.metadata or {})
        if assignment.order.fulfillment_type != Order.FulfillmentType.DELIVERY:
            return None
        if assignment.order.status != Order.Status.READY_FOR_DELIVERY:
            self._cancel_pending_offers(assignment, response_reason="order_not_ready")
            return None
        if assignment.delivery_user_id or assignment.status != DeliveryAssignment.Status.UNASSIGNED:
            self._cancel_pending_offers(assignment, response_reason="assignment_taken")
            metadata = self._mark_assigned_state(metadata)
            assignment.metadata = metadata
            assignment.save(update_fields=["metadata", "updated_at"])
            return None

        now = timezone.now()
        self._expire_assignment_offers(assignment, metadata)

        active_offer = (
            DeliveryAssignmentOffer.objects.select_related("delivery_user")
            .filter(assignment=assignment, status=DeliveryAssignmentOffer.Status.PENDING)
            .order_by("offered_at")
            .first()
        )

        offered_delivery_user_ids = list(metadata.get("offer_attempt_delivery_user_ids") or [])
        if active_offer and self._offer_is_active(active_offer):
            candidates = self.assignment_engine.build_candidate_snapshot(
                assignment,
                offered_delivery_user_ids[:-1] if offered_delivery_user_ids else [],
            )
            selected_still_eligible = any(
                str(item["profile"].id) == str(active_offer.delivery_user_id)
                for item in candidates
            )
            if selected_still_eligible:
                metadata["offer_flow_state"] = self.STATE_OFFER_PENDING
                assignment.metadata = metadata
                assignment.save(update_fields=["metadata", "updated_at"])
                return active_offer
            active_offer.status = DeliveryAssignmentOffer.Status.CANCELLED
            active_offer.responded_at = now
            active_offer.response_reason = "delivery_no_longer_eligible"
            active_offer.save(update_fields=["status", "responded_at", "response_reason", "updated_at"])
            metadata["offer_flow_state"] = self.STATE_WAITING_ROUND_2
            metadata["offer_next_round_at"] = (now + timedelta(seconds=self.SECOND_OFFER_DELAY_SECONDS)).isoformat()
            assignment.metadata = metadata
            assignment.save(update_fields=["metadata", "updated_at"])
            return None

        metadata = self._advance_cycle(assignment, metadata, reason=reason, now=now)
        assignment.metadata = metadata
        assignment.save(update_fields=["metadata", "updated_at"])
        self._publish_refreshes_from_metadata(assignment, metadata)
        return None

    @transaction.atomic
    def reject_offer(self, assignment_id: str, user_id: str):
        delivery = self._require_delivery(user_id)
        assignment = (
            DeliveryAssignment.objects.select_for_update()
            .select_related("order", "order__client", "order__chef")
            .get(pk=str(assignment_id))
        )
        offer = (
            DeliveryAssignmentOffer.objects.select_for_update()
            .filter(
                assignment=assignment,
                delivery_user=delivery,
                status=DeliveryAssignmentOffer.Status.PENDING,
            )
            .order_by("offered_at")
            .first()
        )
        if not offer or not self._offer_is_active(offer):
            latest_offer = (
                DeliveryAssignmentOffer.objects.filter(
                    assignment=assignment,
                    delivery_user=delivery,
                )
                .order_by("-offered_at", "-updated_at")
                .first()
            )
            if latest_offer and latest_offer.status in {
                DeliveryAssignmentOffer.Status.REJECTED,
                DeliveryAssignmentOffer.Status.EXPIRED,
                DeliveryAssignmentOffer.Status.CANCELLED,
            }:
                return {
                    "message": "La oferta ya habia sido descartada para tu usuario.",
                }
            raise DeliveryOfferError(
                "La oferta ya no esta disponible para tu usuario.",
                "offer_not_available",
            )
        now = timezone.now()
        offer.status = DeliveryAssignmentOffer.Status.REJECTED
        offer.responded_at = now
        offer.response_reason = "rejected_by_delivery"
        offer.save(update_fields=["status", "responded_at", "response_reason", "updated_at"])

        metadata = dict(assignment.metadata or {})
        metadata["assignment_last_result"] = "offer_rejected"
        if offer.attempt_number >= 2:
            metadata = self._enable_open_board(metadata, now, "second_offer_rejected")
        else:
            metadata["offer_flow_state"] = self.STATE_WAITING_ROUND_2
            metadata["offer_next_round_at"] = (now + timedelta(seconds=self.SECOND_OFFER_DELAY_SECONDS)).isoformat()
        metadata = self._append_flow_audit(
            metadata,
            event_code="offer_rejected",
            label="Oferta rechazada por repartidor",
            occurred_at=now,
            extra={
                "delivery_user_id": str(delivery.supabase_user_id),
                "delivery_name": self._profile_name(delivery),
                "attempt_number": offer.attempt_number,
                "offer_id": offer.id,
            },
        )
        metadata["offer_pending_delivery_user_id"] = ""
        metadata["offer_pending_delivery_name"] = ""
        metadata["offer_pending_expires_at"] = ""
        assignment.metadata = metadata
        assignment.save(update_fields=["metadata", "updated_at"])
        self._record_offer_timeline(
            assignment,
            "DELIVERY_OFFER_REJECTED",
            "Repartidor rechazo la oferta de entrega",
            delivery,
            {"offer_id": offer.id},
        )
        from modules.delivery_logistica.realtime import publish_delivery_dashboard_refresh

        publish_delivery_dashboard_refresh(
            delivery_user_id=str(delivery.supabase_user_id),
            reason="offer_rejected",
        )
        self._publish_refreshes_from_metadata(assignment, metadata)
        if metadata.get(self.OPEN_BOARD_KEY):
            return {"message": "Oferta rechazada. La entrega paso al tablero abierto."}
        return {"message": "Oferta rechazada. Se evaluara la siguiente ronda automaticamente."}

    @transaction.atomic
    def accept_offer(self, assignment: DeliveryAssignment, delivery: UserProfile):
        assignment = (
            DeliveryAssignment.objects.select_for_update()
            .select_related("order", "order__client", "order__chef", "delivery_user")
            .prefetch_related("order__address", "order__items", "order__payments")
            .get(pk=assignment.pk)
        )
        offer = (
            DeliveryAssignmentOffer.objects.select_for_update()
            .filter(
                assignment=assignment,
                delivery_user=delivery,
                status=DeliveryAssignmentOffer.Status.PENDING,
            )
            .order_by("offered_at")
            .first()
        )
        if not offer or not self._offer_is_active(offer):
            accepted_offer = (
                DeliveryAssignmentOffer.objects.filter(
                    assignment=assignment,
                    delivery_user=delivery,
                    status=DeliveryAssignmentOffer.Status.ACCEPTED,
                )
                .order_by("-responded_at", "-updated_at")
                .first()
            )
            if accepted_offer and assignment.delivery_user_id == delivery.id:
                return accepted_offer
            raise DeliveryOfferError(
                "La oferta ya no esta disponible para tu usuario.",
                "offer_not_available",
            )
        if assignment.delivery_user_id and assignment.delivery_user_id != delivery.id:
            raise DeliveryOfferError(
                "La entrega ya fue tomada por otro repartidor.",
                "assignment_already_taken",
            )
        now = timezone.now()
        assignment.delivery_user = delivery
        assignment.assigned_at = assignment.assigned_at or now
        assignment.status = DeliveryAssignment.Status.ASSIGNED
        metadata = dict(assignment.metadata or {})
        metadata["assigned_delivery_user_id"] = str(delivery.supabase_user_id)
        metadata["assignment_last_result"] = "offer_accepted"
        metadata["offer_flow_state"] = self.STATE_ASSIGNED
        metadata["offer_pending_delivery_user_id"] = ""
        metadata["offer_pending_delivery_name"] = ""
        metadata["offer_pending_expires_at"] = ""
        metadata[self.OPEN_BOARD_KEY] = False
        metadata["open_board_enabled_at"] = ""
        metadata["open_board_reason"] = ""
        metadata = self._append_flow_audit(
            metadata,
            event_code="offer_accepted",
            label="Oferta aceptada",
            occurred_at=now,
            extra={
                "delivery_user_id": str(delivery.supabase_user_id),
                "delivery_name": self._profile_name(delivery),
                "offer_id": offer.id,
                "attempt_number": offer.attempt_number,
            },
        )
        assignment.metadata = metadata
        assignment.save(
            update_fields=["delivery_user", "assigned_at", "status", "metadata", "updated_at"]
        )
        offer.status = DeliveryAssignmentOffer.Status.ACCEPTED
        offer.responded_at = now
        offer.response_reason = "accepted"
        offer.save(update_fields=["status", "responded_at", "response_reason", "updated_at"])
        DeliveryAssignmentOffer.objects.filter(
            assignment=assignment,
            status=DeliveryAssignmentOffer.Status.PENDING,
        ).exclude(pk=offer.pk).update(
            status=DeliveryAssignmentOffer.Status.CANCELLED,
            responded_at=now,
            response_reason="assignment_taken",
        )
        self.availability_service.sync_for_user(delivery)
        from modules.delivery_logistica.realtime import publish_delivery_dashboard_refresh

        publish_delivery_dashboard_refresh(
            delivery_user_id=str(delivery.supabase_user_id),
            reason="offer_accepted",
        )
        publish_delivery_dashboard_refresh(include_global=True, reason="offer_accepted")
        return offer

    @transaction.atomic
    def claim_open_board_assignment(self, assignment_id: str, user_id: str):
        delivery = self._require_delivery(user_id)
        assignment = (
            DeliveryAssignment.objects.select_for_update()
            .select_related("order", "order__client", "order__chef", "delivery_user")
            .prefetch_related("order__address", "order__items", "order__payments")
            .get(pk=str(assignment_id))
        )
        if assignment.delivery_user_id == delivery.id and assignment.status == DeliveryAssignment.Status.ASSIGNED:
            return assignment
        if assignment.delivery_user_id:
            raise DeliveryOfferError(
                "La entrega ya fue tomada por otro repartidor.",
                "assignment_already_taken",
            )
        if assignment.status != DeliveryAssignment.Status.UNASSIGNED or assignment.order.status != Order.Status.READY_FOR_DELIVERY:
            raise DeliveryOfferError(
                "La entrega ya no esta disponible para tomar.",
                "assignment_not_open_board",
            )
        metadata = dict(assignment.metadata or {})
        if not metadata.get(self.OPEN_BOARD_KEY):
            raise DeliveryOfferError(
                "La entrega aun no esta disponible para el tablero abierto.",
                "assignment_not_open_board",
            )
        delivery_profile = getattr(delivery, "delivery_profile", None)
        if not delivery_profile:
            raise DeliveryOfferError(
                "Perfil operativo de delivery no encontrado.",
                "delivery_profile_not_found",
            )
        effective_status = self.availability_service.resolve_effective_status(
            delivery_profile.availability_manual_status,
            self._active_assignments_count(delivery),
        )
        if effective_status == DeliveryProfile.AvailabilityEffectiveStatus.BUSY:
            raise DeliveryOfferError(
                "Tu usuario esta ocupado y no puede tomar mas pedidos en este momento.",
                "delivery_busy",
            )
        now = timezone.now()
        assignment.delivery_user = delivery
        assignment.assigned_at = assignment.assigned_at or now
        assignment.status = DeliveryAssignment.Status.ASSIGNED
        metadata["assigned_delivery_user_id"] = str(delivery.supabase_user_id)
        metadata["assignment_last_result"] = "open_board_claimed"
        metadata["offer_flow_state"] = self.STATE_ASSIGNED
        metadata[self.OPEN_BOARD_KEY] = False
        metadata["open_board_enabled_at"] = ""
        metadata["open_board_reason"] = ""
        metadata = self._append_flow_audit(
            metadata,
            event_code="open_board_claimed",
            label="Entrega tomada desde tablero abierto",
            occurred_at=now,
            extra={
                "delivery_user_id": str(delivery.supabase_user_id),
                "delivery_name": self._profile_name(delivery),
            },
        )
        assignment.metadata = metadata
        assignment.save(
            update_fields=["delivery_user", "assigned_at", "status", "metadata", "updated_at"]
        )
        DeliveryStatusHistory.objects.create(
            assignment=assignment,
            from_status=DeliveryAssignment.Status.UNASSIGNED,
            to_status=DeliveryAssignment.Status.ASSIGNED,
            actor_role="REPARTIDOR",
            actor_id=str(delivery.supabase_user_id),
            notes="Entrega tomada desde tablero abierto",
            metadata={"open_board": True},
        )
        self._record_offer_timeline(
            assignment,
            "DELIVERY_OPEN_BOARD_CLAIMED",
            "Repartidor tomo la entrega desde tablero abierto",
            delivery,
            {},
        )
        self._cancel_pending_offers(assignment, response_reason="open_board_claimed")
        self.availability_service.sync_for_user(delivery)
        publish_order_tracking_refresh(str(assignment.order_id))
        from modules.delivery_logistica.realtime import publish_delivery_dashboard_refresh

        publish_delivery_dashboard_refresh(
            delivery_user_id=str(delivery.supabase_user_id),
            reason="open_board_claimed",
        )
        publish_delivery_dashboard_refresh(include_global=True, reason="open_board_claimed")
        return assignment

    @transaction.atomic
    def cancel_assignment_to_open_board(self, assignment_id: str, user_id: str):
        delivery = self._require_delivery(user_id)
        assignment = (
            DeliveryAssignment.objects.select_for_update()
            .select_related("order", "order__client", "order__chef", "delivery_user")
            .get(pk=str(assignment_id))
        )
        if assignment.delivery_user_id and assignment.delivery_user_id != delivery.id:
            raise DeliveryOfferError(
                "La entrega ya no pertenece a tu usuario.",
                "assignment_not_owned_by_delivery",
            )
        metadata = dict(assignment.metadata or {})
        if (
            assignment.delivery_user_id is None
            and assignment.status == DeliveryAssignment.Status.UNASSIGNED
            and metadata.get(self.OPEN_BOARD_KEY)
            and str(metadata.get("last_cancelled_delivery_user_id") or "")
            == str(delivery.supabase_user_id)
        ):
            return assignment
        if assignment.delivery_user_id != delivery.id:
            raise DeliveryOfferError(
                "La entrega ya no pertenece a tu usuario.",
                "assignment_not_owned_by_delivery",
            )
        if assignment.status != DeliveryAssignment.Status.ASSIGNED:
            raise DeliveryOfferError(
                "La entrega solo puede cancelarse antes de llegar al cocinero.",
                "assignment_cancel_not_allowed",
            )
        previous = assignment.status
        now = timezone.now()
        assignment.delivery_user = None
        assignment.status = DeliveryAssignment.Status.UNASSIGNED
        assignment.assigned_at = None
        metadata = self._enable_open_board(metadata, now, "delivery_cancelled_before_chef")
        metadata["assigned_delivery_user_id"] = ""
        metadata["assignment_last_result"] = "delivery_cancelled_to_open_board"
        metadata["offer_flow_state"] = self.STATE_OPEN_BOARD
        metadata["last_cancelled_delivery_user_id"] = str(delivery.supabase_user_id)
        metadata = self._append_flow_audit(
            metadata,
            event_code="assignment_cancelled_to_open_board",
            label="Asignacion cancelada y enviada a tablero abierto",
            occurred_at=now,
            extra={
                "delivery_user_id": str(delivery.supabase_user_id),
                "delivery_name": self._profile_name(delivery),
            },
        )
        assignment.metadata = metadata
        assignment.save(
            update_fields=["delivery_user", "status", "assigned_at", "metadata", "updated_at"]
        )
        DeliveryStatusHistory.objects.create(
            assignment=assignment,
            from_status=previous,
            to_status=DeliveryAssignment.Status.UNASSIGNED,
            actor_role="REPARTIDOR",
            actor_id=str(delivery.supabase_user_id),
            notes="Repartidor cancelo la asignacion antes de llegar al cocinero",
            metadata={"open_board": True},
        )
        self._record_offer_timeline(
            assignment,
            "DELIVERY_ASSIGNMENT_CANCELLED",
            "Repartidor cancelo la asignacion antes del cocinero",
            delivery,
            {},
        )
        self._cancel_pending_offers(assignment, response_reason="assignment_cancelled")
        self.availability_service.sync_for_user(delivery)
        publish_order_tracking_refresh(str(assignment.order_id))
        from modules.delivery_logistica.realtime import publish_delivery_dashboard_refresh

        publish_delivery_dashboard_refresh(
            delivery_user_id=str(delivery.supabase_user_id),
            reason="assignment_cancelled_to_open_board",
        )
        publish_delivery_dashboard_refresh(include_global=True, reason="assignment_cancelled_to_open_board")
        return assignment

    def get_pending_assignment_ids_for_delivery(self, delivery: UserProfile):
        self.sync_open_assignments()
        return list(
            DeliveryAssignmentOffer.objects.filter(
                delivery_user=delivery,
                status=DeliveryAssignmentOffer.Status.PENDING,
            ).values_list("assignment_id", flat=True)
        )

    def get_reserved_assignment_ids(self):
        self.sync_open_assignments()
        return list(
            DeliveryAssignmentOffer.objects.filter(
                status=DeliveryAssignmentOffer.Status.PENDING,
            ).values_list("assignment_id", flat=True)
        )

    def get_pending_offer_for_assignment(self, assignment: DeliveryAssignment, delivery: UserProfile):
        self.sync_open_assignments()
        return (
            DeliveryAssignmentOffer.objects.filter(
                assignment=assignment,
                delivery_user=delivery,
                status=DeliveryAssignmentOffer.Status.PENDING,
            )
            .order_by("offered_at")
            .first()
        )

    def has_active_offer_for_assignment(self, assignment: DeliveryAssignment):
        self.sync_open_assignments()
        return DeliveryAssignmentOffer.objects.filter(
            assignment=assignment,
            status=DeliveryAssignmentOffer.Status.PENDING,
        ).exists()

    def can_delivery_view_assignment(self, assignment: DeliveryAssignment, delivery: UserProfile):
        if assignment.delivery_user_id == delivery.id:
            return True
        metadata = dict(assignment.metadata or {})
        if metadata.get(self.OPEN_BOARD_KEY) and not assignment.delivery_user_id:
            return True
        return DeliveryAssignmentOffer.objects.filter(
            assignment=assignment,
            delivery_user=delivery,
            status=DeliveryAssignmentOffer.Status.PENDING,
        ).exists()

    def is_open_board_assignment(self, assignment: DeliveryAssignment):
        metadata = dict(assignment.metadata or {})
        return bool(metadata.get(self.OPEN_BOARD_KEY)) and not assignment.delivery_user_id

    def _advance_cycle(self, assignment: DeliveryAssignment, metadata: dict, *, reason: str, now):
        round_number = int(metadata.get("offer_round_number", 0) or 0)
        state = metadata.get("offer_flow_state") or self.STATE_IDLE

        if state == self.STATE_OPEN_BOARD:
            return metadata

        if round_number == 0:
            return self._run_round(
                assignment,
                metadata,
                round_number=1,
                delay_after_no_candidate=self.SECOND_SEARCH_DELAY_SECONDS,
                reason=reason,
                now=now,
            )

        if state == self.STATE_WAITING_ROUND_2:
            next_round_at = self._parse_datetime(metadata.get("offer_next_round_at"))
            if next_round_at and now < next_round_at:
                return metadata
            if round_number >= 2:
                return self._enable_open_board(metadata, now, "max_rounds_reached")
            return self._run_round(
                assignment,
                metadata,
                round_number=2,
                delay_after_no_candidate=0,
                reason=reason,
                now=now,
            )

        latest_final_offer = (
            DeliveryAssignmentOffer.objects.filter(
                assignment=assignment,
                status__in=[
                    DeliveryAssignmentOffer.Status.REJECTED,
                    DeliveryAssignmentOffer.Status.EXPIRED,
                    DeliveryAssignmentOffer.Status.CANCELLED,
                ],
            )
            .order_by("-responded_at", "-updated_at")
            .first()
        )
        if latest_final_offer:
            if round_number >= 2:
                return self._enable_open_board(metadata, now, "two_offer_rounds_failed")
            metadata["offer_flow_state"] = self.STATE_WAITING_ROUND_2
            metadata["offer_next_round_at"] = (
                latest_final_offer.responded_at or now
            ) + timedelta(seconds=self.SECOND_OFFER_DELAY_SECONDS)
            metadata["offer_next_round_at"] = metadata["offer_next_round_at"].isoformat()
            return metadata

        no_candidate_attempts = int(metadata.get("no_candidate_attempt_count", 0) or 0)
        if no_candidate_attempts >= 2:
            return self._enable_open_board(metadata, now, "two_search_rounds_without_candidates")
        if no_candidate_attempts == 1:
            metadata["offer_flow_state"] = self.STATE_WAITING_ROUND_2
            if not metadata.get("offer_next_round_at"):
                metadata["offer_next_round_at"] = (
                    now + timedelta(seconds=self.SECOND_SEARCH_DELAY_SECONDS)
                ).isoformat()
            return metadata
        return metadata

    def _run_round(self, assignment: DeliveryAssignment, metadata: dict, *, round_number: int, delay_after_no_candidate: int, reason: str, now):
        offered_delivery_user_ids = list(metadata.get("offer_attempt_delivery_user_ids") or [])
        candidates = self.assignment_engine.build_candidate_snapshot(assignment, offered_delivery_user_ids)
        metadata["assignment_strategy"] = "NEAREST_AVAILABLE_DRIVER_OFFER_V2"
        metadata["assignment_last_attempt_at"] = now.isoformat()
        metadata["assignment_last_reason"] = reason
        metadata["candidate_snapshot"] = self.assignment_engine._serializable_candidates(candidates)
        metadata["assignment_attempt_count"] = int(metadata.get("assignment_attempt_count", 0) or 0) + 1
        metadata["offer_round_number"] = round_number
        if not candidates:
            metadata["assignment_last_result"] = "no_candidate_for_offer"
            metadata["no_candidate_attempt_count"] = int(metadata.get("no_candidate_attempt_count", 0) or 0) + 1
            metadata = self._append_flow_audit(
                metadata,
                event_code="no_candidates_found",
                label="No se encontraron repartidores elegibles",
                occurred_at=now,
                extra={
                    "round_number": round_number,
                    "reason": reason,
                },
            )
            if round_number >= 2 or delay_after_no_candidate == 0:
                return self._enable_open_board(metadata, now, "no_candidates_after_second_round")
            metadata["offer_flow_state"] = self.STATE_WAITING_ROUND_2
            metadata["offer_next_round_at"] = (now + timedelta(seconds=delay_after_no_candidate)).isoformat()
            return metadata

        selected = candidates[0]
        offer = DeliveryAssignmentOffer.objects.create(
            assignment=assignment,
            delivery_user=selected["profile"],
            status=DeliveryAssignmentOffer.Status.PENDING,
            offered_at=now,
            expires_at=now + timedelta(seconds=self.OFFER_TTL_SECONDS),
            attempt_number=round_number,
            selection_distance_meters=float(selected["distance_meters"]),
            selection_snapshot={
                "delivery_user_id": selected["delivery_user_id"],
                "delivery_name": selected["delivery_name"],
                "distance_meters": float(selected["distance_meters"]),
                "distance_human": selected["distance_human"],
                "location_source": selected["location_source"],
                "active_assignments": int(selected["active_assignments"]),
            },
            metadata={
                "reason": reason,
                "offer_ttl_seconds": self.OFFER_TTL_SECONDS,
                "round_number": round_number,
            },
        )
        offered_delivery_user_ids.append(str(selected["profile"].supabase_user_id))
        metadata["offer_attempt_delivery_user_ids"] = offered_delivery_user_ids
        metadata["assignment_last_result"] = "offer_created"
        metadata["offer_flow_state"] = self.STATE_OFFER_PENDING
        metadata["offer_pending_delivery_user_id"] = str(selected["profile"].supabase_user_id)
        metadata["offer_pending_delivery_name"] = selected["delivery_name"]
        metadata["offer_pending_expires_at"] = offer.expires_at.isoformat()
        metadata["offer_pending_attempt_number"] = round_number
        metadata["offer_next_round_at"] = ""
        metadata["assigned_distance_meters"] = float(selected["distance_meters"])
        metadata["assigned_distance_human"] = selected["distance_human"]
        metadata[self.OPEN_BOARD_KEY] = False
        metadata["open_board_enabled_at"] = ""
        metadata["open_board_reason"] = ""
        metadata = self._append_flow_audit(
            metadata,
            event_code="offer_created",
            label="Oferta temporal enviada",
            occurred_at=now,
            extra={
                "round_number": round_number,
                "delivery_user_id": str(selected["profile"].supabase_user_id),
                "delivery_name": selected["delivery_name"],
                "offer_id": offer.id,
                "distance_meters": float(selected["distance_meters"]),
                "distance_human": selected["distance_human"],
            },
        )
        return metadata

    def _expire_assignment_offers(self, assignment: DeliveryAssignment, metadata: dict):
        now = timezone.now()
        stale = list(DeliveryAssignmentOffer.objects.filter(
            assignment=assignment,
            status=DeliveryAssignmentOffer.Status.PENDING,
            expires_at__lte=now,
        ))
        if not stale:
            return
        expired_offer_ids = [item.id for item in stale]
        DeliveryAssignmentOffer.objects.filter(id__in=expired_offer_ids).update(
            status=DeliveryAssignmentOffer.Status.EXPIRED,
            responded_at=now,
            response_reason="expired",
        )
        round_number = int(metadata.get("offer_round_number", 0) or 0)
        metadata = self._append_flow_audit(
            metadata,
            event_code="offer_expired",
            label="Oferta temporal expirada",
            occurred_at=now,
            extra={
                "round_number": round_number,
                "offer_ids": expired_offer_ids,
            },
        )
        if round_number >= 2:
            metadata.update(self._enable_open_board(metadata, now, "second_offer_expired"))
            return
        metadata["assignment_last_result"] = "offer_expired"
        metadata["offer_flow_state"] = self.STATE_WAITING_ROUND_2
        metadata["offer_next_round_at"] = (now + timedelta(seconds=self.SECOND_OFFER_DELAY_SECONDS)).isoformat()
        metadata["offer_pending_delivery_user_id"] = ""
        metadata["offer_pending_delivery_name"] = ""
        metadata["offer_pending_expires_at"] = ""

    def _enable_open_board(self, metadata: dict, now, reason: str):
        metadata["offer_flow_state"] = self.STATE_OPEN_BOARD
        metadata["assignment_last_result"] = "open_board"
        metadata[self.OPEN_BOARD_KEY] = True
        metadata["open_board_enabled_at"] = now.isoformat()
        metadata["open_board_reason"] = reason
        metadata["offer_pending_delivery_user_id"] = ""
        metadata["offer_pending_delivery_name"] = ""
        metadata["offer_pending_expires_at"] = ""
        metadata["offer_next_round_at"] = ""
        metadata = self._append_flow_audit(
            metadata,
            event_code="open_board_enabled",
            label="Entrega liberada a tablero abierto",
            occurred_at=now,
            extra={"reason": reason},
        )
        return metadata

    def _mark_assigned_state(self, metadata: dict):
        metadata["offer_flow_state"] = self.STATE_ASSIGNED
        metadata[self.OPEN_BOARD_KEY] = False
        metadata["offer_pending_delivery_user_id"] = ""
        metadata["offer_pending_delivery_name"] = ""
        metadata["offer_pending_expires_at"] = ""
        metadata["offer_next_round_at"] = ""
        return metadata

    def _cancel_pending_offers(self, assignment: DeliveryAssignment, *, response_reason: str):
        DeliveryAssignmentOffer.objects.filter(
            assignment=assignment,
            status=DeliveryAssignmentOffer.Status.PENDING,
        ).update(
            status=DeliveryAssignmentOffer.Status.CANCELLED,
            responded_at=timezone.now(),
            response_reason=response_reason,
        )

    def _serialize_offer(self, offer: DeliveryAssignmentOffer):
        order = offer.assignment.order
        address = getattr(order, "address", None)
        seconds_left = max(0, int((offer.expires_at - timezone.now()).total_seconds()))
        return {
            "id": offer.id,
            "assignment_id": offer.assignment_id,
            "status": offer.status,
            "offered_at": offer.offered_at.isoformat(),
            "expires_at": offer.expires_at.isoformat(),
            "seconds_left": seconds_left,
            "attempt_number": offer.attempt_number,
            "selection_distance_meters": float(offer.selection_distance_meters or 0),
            "selection_distance_human": self.assignment_engine._distance_human(offer.selection_distance_meters),
            "order": {
                "id": order.id,
                "status": order.status,
                "client_name": self._profile_name(order.client),
                "chef_name": self._profile_name(order.chef),
                "total": float(order.total),
                "address_line_1": address.line_1 if address else "",
                "address_reference": address.reference if address else "",
            },
            "selection_snapshot": offer.selection_snapshot or {},
        }

    def _serialize_open_board_assignment(self, assignment: DeliveryAssignment):
        order = assignment.order
        address = getattr(order, "address", None)
        return {
            "id": assignment.id,
            "status": assignment.status,
            "status_label": "Disponible para cualquiera",
            "order": {
                "id": order.id,
                "status": order.status,
                "client_name": self._profile_name(order.client),
                "chef_name": self._profile_name(order.chef),
                "total": float(order.total),
                "address_line_1": address.line_1 if address else "",
                "address_reference": address.reference if address else "",
            },
            "available_actions": ["claim"],
            "open_board_enabled_at": (assignment.metadata or {}).get("open_board_enabled_at"),
            "open_board_reason": (assignment.metadata or {}).get("open_board_reason", ""),
        }

    def _publish_refreshes_from_metadata(self, assignment: DeliveryAssignment, metadata: dict):
        from modules.delivery_logistica.realtime import publish_delivery_dashboard_refresh

        pending_user_id = metadata.get("offer_pending_delivery_user_id")
        if pending_user_id:
            publish_delivery_dashboard_refresh(
                delivery_user_id=str(pending_user_id),
                reason="offer_created",
            )
        if metadata.get(self.OPEN_BOARD_KEY):
            publish_delivery_dashboard_refresh(include_global=True, reason="open_board_available")

    def _record_offer_timeline(self, assignment: DeliveryAssignment, event_code: str, event_label: str, delivery: UserProfile, metadata: dict):
        OrderTimelineEvent.objects.create(
            order=assignment.order,
            event_code=event_code,
            event_label=event_label,
            actor_role="REPARTIDOR",
            actor_id=str(delivery.supabase_user_id),
            metadata={"assignment_id": assignment.id, **metadata},
        )

    def _offer_is_active(self, offer: DeliveryAssignmentOffer):
        return offer.status == DeliveryAssignmentOffer.Status.PENDING and offer.expires_at > timezone.now()

    def _append_flow_audit(self, metadata: dict, *, event_code: str, label: str, occurred_at, extra: dict | None = None):
        audit = list(metadata.get("flow_audit") or [])
        audit.append(
            {
                "event_code": event_code,
                "label": label,
                "occurred_at": occurred_at.isoformat(),
                **(extra or {}),
            }
        )
        metadata["flow_audit"] = audit[-self.FLOW_AUDIT_LIMIT :]
        return metadata

    def _active_assignments_count(self, delivery_user: UserProfile):
        return DeliveryAssignment.objects.filter(
            delivery_user=delivery_user,
            status__in=[
                DeliveryAssignment.Status.ASSIGNED,
                DeliveryAssignment.Status.AT_CHEF,
                DeliveryAssignment.Status.PICKED_UP,
                DeliveryAssignment.Status.EN_ROUTE_TO_CLIENT,
            ],
        ).count()

    def _require_delivery(self, user_id: str):
        parsed = self._parse_uuid(user_id)
        profile = (
            UserProfile.objects.filter(
                supabase_user_id=parsed,
                role=UserProfile.ROLE_DELIVERY,
            ).first()
            if parsed
            else None
        )
        if not profile:
            raise DeliveryOfferError(
                "Perfil de repartidor no encontrado.",
                "delivery_not_found",
            )
        return profile

    def _parse_uuid(self, value):
        try:
            return UUID(str(value))
        except (TypeError, ValueError):
            return None

    def _parse_datetime(self, value):
        try:
            return timezone.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError, AttributeError):
            return None

    def _profile_name(self, profile: UserProfile | None):
        if not profile:
            return ""
        name = f"{profile.first_name} {profile.last_name}".strip()
        return name or profile.full_name or profile.email

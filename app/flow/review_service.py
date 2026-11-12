from __future__ import annotations

from app.flow.models import FlowReviewItem, ReviewStatus


class FlowReviewService:
    @staticmethod
    def validate(items: list[tuple[int, FlowReviewItem]]) -> dict[int, list[str]]:
        errors: dict[int, list[str]] = {}
        owners: dict[str, list[int]] = {}
        for review_id, item in items:
            item_errors = []
            if item.status == ReviewStatus.EXCLUDED:
                if not item.excluded_reason:
                    item_errors.append("Exclusion requires a reason.")
            else:
                if not item.category:
                    item_errors.append("Classification is required.")
                if not item.proposed_serials:
                    item_errors.append("At least one proposed serial is required.")
                for serial in item.proposed_serials:
                    cleaned = serial.strip()
                    if not cleaned:
                        item_errors.append("Serials cannot be blank.")
                    elif not cleaned.casefold().startswith(f"{item.line.so}-".casefold()):
                        item_errors.append(f"Serial {cleaned!r} does not start with selected SO {item.line.so}.")
                    owners.setdefault(cleaned.casefold(), []).append(review_id)
            if item_errors:
                errors[review_id] = item_errors
        for serial, review_ids in owners.items():
            if len(review_ids) > 1:
                for review_id in review_ids:
                    errors.setdefault(review_id, []).append(f"Duplicate proposed serial across review batch: {serial}.")
        return errors

    @classmethod
    def approve(cls, items: list[tuple[int, FlowReviewItem]], selected_ids: set[int]) -> dict[int, list[str]]:
        errors = cls.validate(items)
        for review_id, item in items:
            if review_id in selected_ids and review_id not in errors and item.status != ReviewStatus.EXCLUDED:
                item.status = ReviewStatus.APPROVED
                item.approved = True
        return {review_id: messages for review_id, messages in errors.items() if review_id in selected_ids}

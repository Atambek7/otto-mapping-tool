"""Offline contracts for durable one-time stock and delivery setup."""
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
import models
from services.initial_availability import (
    apply_guarded, classify_response, register_initial, should_register_initial,
)


DELIVERY = {"sku": "sku", "shippingProfileId": "fixture-profile", "processingTime": "37"}


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.db = self.Session()
        self.plan = models.PublishPlan(
            factory_id="f", factory_name="Factory", account_name=models.AccountType.JV,
            total_products=1, total_days=1,
        )
        self.db.add(self.plan)
        self.db.flush()
        self.item = models.PublishQueueItem(
            plan_id=self.plan.id, product_ean="sku", account_name=models.AccountType.JV,
            category_id="c", scheduled_day=1, scheduled_date=datetime.now(timezone.utc),
        )
        self.db.add(self.item)
        self.db.add(models.ProductMapping(
            product_ean="sku", account_name=models.AccountType.JV, factory_id="f",
            product_name="Chair", category_id="c", status="Ready",
        ))
        self.db.add(models.AccountSettings(
            account_name="JV", otto_client_id="fixture", otto_client_secret="fixture",
        ))
        self.db.commit()
        self.assertTrue(register_initial(self.db, "JV", "sku", self.item.id, self.plan, DELIVERY))
        self.db.commit()
        self.state = self.db.query(models.InitialAvailabilityState).filter_by(product_ean="sku").one()
        self.client = Mock()
        self.client.update_initial_quantity_once.return_value = {"status_code": 204, "request_sent": True}
        self.client.update_initial_delivery_once.return_value = {"status_code": 204, "request_sent": True}

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_each_setup_step_is_independent_and_stock_is_not_repeated(self):
        self.client.update_initial_delivery_once.side_effect = [
            {"status_code": 429, "response": {"message": "rate limited"}},
            {"status_code": 204},
        ]
        first = apply_guarded(self.client, [self.item], "JV", self.db)
        self.db.refresh(self.state)
        self.assertEqual(first["delivery_failed_skus"], ["sku"])
        self.assertEqual(self.state.stock_state, "confirmed")
        self.assertEqual(self.state.delivery_state, "retryable_failed")
        self.assertEqual(self.state.stock_attempts, 1)
        self.assertEqual(self.state.delivery_attempts, 1)
        self.assertEqual(self.state.category_id, "c")

        # Automatic retry is durable and bounded; make its due time deterministic.
        self.state.delivery_next_attempt_at = datetime(2000, 1, 1)
        self.db.commit()
        second = apply_guarded(self.client, [self.item], "JV", self.db)
        self.assertEqual(second["delivery_failed_skus"], [])
        self.client.update_initial_quantity_once.assert_called_once_with("sku")
        self.assertEqual(self.client.update_initial_delivery_once.call_count, 2)
        attempts = self.db.query(models.InitialAvailabilityAttempt).order_by(
            models.InitialAvailabilityAttempt.id
        ).all()
        self.assertEqual([(a.operation, a.outcome) for a in attempts], [
            ("stock", "confirmed"), ("delivery", "retryable_failed"), ("delivery", "confirmed")
        ])

    def test_unknown_http_outcome_never_retries(self):
        self.client.update_initial_quantity_once.return_value = {
            "status_code": 0, "error": "timeout", "request_sent": True,
        }
        first = apply_guarded(self.client, [self.item], "JV", self.db)
        second = apply_guarded(self.client, [self.item], "JV", self.db)
        self.assertIn("sku", first["unknown_skus"])
        self.assertIn("sku", second["unknown_skus"])
        self.client.update_initial_quantity_once.assert_called_once()
        self.assertEqual(self.state.stock_state, "unknown")
        self.assertEqual(self.state.stock_attempts, 1)

    def test_202_is_accepted_not_confirmed_and_not_retried(self):
        self.client.update_initial_delivery_once.return_value = {"status_code": 202, "request_sent": True}
        apply_guarded(self.client, [self.item], "JV", self.db)
        apply_guarded(self.client, [self.item], "JV", self.db)
        self.assertEqual(self.state.delivery_state, "accepted")
        self.client.update_initial_delivery_once.assert_called_once()

    def test_explicit_unknown_sku_rejection_is_retryable_but_other_4xx_is_terminal(self):
        response = {"status_code": 422, "response": {"detail": "SKU is unknown: sku"}}
        self.assertEqual(classify_response(response, "sku"), "retryable_failed")
        self.assertEqual(classify_response({"status_code": 422, "response": {"detail": "invalid profile"}}, "sku"), "failed")
        self.assertEqual(classify_response({"status_code": 503}, "sku"), "unknown")

    def test_legacy_or_previously_published_listing_is_not_registered(self):
        self.assertFalse(should_register_initial(self.db, "JV", self.item, previously_published=True))
        prior_plan = models.PublishPlan(
            factory_id="prior", factory_name="Prior", account_name=models.AccountType.JV,
            total_products=1, total_days=1,
        )
        self.db.add(prior_plan)
        self.db.flush()
        other = models.PublishQueueItem(
            plan_id=prior_plan.id, product_ean="sku", account_name=models.AccountType.JV,
            category_id="c", scheduled_day=1, scheduled_date=datetime.now(timezone.utc),
            status=models.QueueItemStatus.PUBLISHED, published_at=datetime.now(timezone.utc),
        )
        self.db.add(other)
        self.db.commit()
        self.assertFalse(should_register_initial(self.db, "JV", self.item))

    def test_registration_requires_matching_account_ean_and_otto_plan(self):
        self.assertFalse(register_initial(
            self.db, "XL", "sku", self.item.id, delivery_payload=DELIVERY,
        ))
        self.assertFalse(register_initial(
            self.db, "JV", "different-sku", self.item.id, delivery_payload=DELIVERY,
        ))
        batch_plan = models.PublishPlan(
            factory_id="batch", account_name=models.AccountType.JV,
            total_products=1, total_days=1, executor_type=models.ExecutorType.BATCH_FILE,
        )
        self.db.add(batch_plan)
        self.db.flush()
        batch_item = models.PublishQueueItem(
            plan_id=batch_plan.id, product_ean="batch-sku", account_name=models.AccountType.JV,
            category_id="c", scheduled_day=1, scheduled_date=datetime.now(timezone.utc),
        )
        self.db.add(batch_item)
        self.db.commit()
        self.assertFalse(register_initial(
            self.db, "JV", "batch-sku", batch_item.id, batch_plan,
            delivery_payload={**DELIVERY, "sku": "batch-sku"},
        ))
        self.assertEqual(self.db.query(models.InitialAvailabilityState).count(), 1)

    def test_queue_deletion_does_not_delete_availability_history(self):
        self.db.delete(self.item)
        self.db.commit()
        self.assertEqual(self.db.query(models.InitialAvailabilityState).count(), 1)
        self.assertFalse(register_initial(self.db, "JV", "sku", 999, delivery_payload=DELIVERY))

    def test_wrong_account_and_missing_state_never_write(self):
        result = apply_guarded(self.client, [self.item], "XL", self.db)
        self.assertEqual(result["quantity_failed_skus"], ["sku"])
        self.client.update_initial_quantity_once.assert_not_called()
        self.client.update_initial_delivery_once.assert_not_called()
        self.db.delete(self.state)
        self.db.commit()
        result = apply_guarded(self.client, [self.item], "JV", self.db)
        self.assertEqual(result["delivery_failed_skus"], ["sku"])
        self.client.update_initial_quantity_once.assert_not_called()

    def test_duplicate_celery_delivery_and_old_queued_message_are_safe(self):
        from tasks import apply_otto_availability_task

        sessions = self.Session
        with patch("tasks.SessionLocal", sessions), \
             patch("services.otto_client.OttoMarketClient", return_value=self.client):
            legacy = apply_otto_availability_task.run([self.item.id], "JV", self.plan.id)
            first = apply_otto_availability_task.run(state_id=self.state.id)
            duplicate = apply_otto_availability_task.run(state_id=self.state.id)
        self.assertEqual(legacy["status"], "ignored_legacy_message")
        self.assertEqual(first["status"], "processed")
        self.assertEqual(duplicate["status"], "processed")
        self.client.update_initial_quantity_once.assert_called_once_with("sku")
        self.client.update_initial_delivery_once.assert_called_once_with(DELIVERY)
        self.assertEqual(self.item.status, models.QueueItemStatus.PENDING)
        self.assertEqual(self.db.query(models.ProductMapping).filter_by(product_ean="sku").one().status, "Ready")

    def test_processing_poller_registers_initial_setup_only_for_reconciled_success(self):
        from tasks import poll_otto_processing_items

        self.item.status = models.QueueItemStatus.PROCESSING
        self.item.otto_task_href = "/v5/products/update-tasks/fixture"
        mapping = self.db.query(models.ProductMapping).filter_by(product_ean="sku").one()
        mapping.status = "In Progress"
        self.db.commit()
        redis_client = Mock()
        redis_client.set.return_value = True
        client = Mock()
        client.get_update_task.return_value = {
            "state": "done", "succeeded": 1, "failed": 0, "unchanged": 0,
        }
        with patch("tasks.SessionLocal", self.Session), \
             patch("redis.from_url", return_value=redis_client), \
             patch("services.otto_client.OttoMarketClient", return_value=client), \
             patch("tasks.dispatch_otto_availability_batches") as legacy_dispatch:
            result = poll_otto_processing_items.run()
        self.assertEqual(result["published"], 1)
        self.db.expire_all()
        state = self.db.query(models.InitialAvailabilityState).filter_by(
            account_name="JV", product_ean="sku"
        ).one()
        self.assertEqual(state.stock_state, "pending")
        self.assertEqual(state.delivery_state, "pending")
        self.assertEqual(state.queue_item_id, self.item.id)
        legacy_dispatch.assert_not_called()

    def test_done_task_without_reconciled_sku_counts_stays_unconfirmed(self):
        from tasks import poll_otto_processing_items

        self.db.delete(self.state)
        self.item.status = models.QueueItemStatus.PROCESSING
        self.item.otto_task_href = "/v5/products/update-tasks/fixture"
        self.db.commit()
        redis_client = Mock()
        redis_client.set.return_value = True
        client = Mock()
        client.get_update_task.return_value = {"state": "done"}
        with patch("tasks.SessionLocal", self.Session), \
             patch("redis.from_url", return_value=redis_client), \
             patch("services.otto_client.OttoMarketClient", return_value=client):
            result = poll_otto_processing_items.run()
        self.assertEqual(result["published"], 0)
        self.assertEqual(self.item.status, models.QueueItemStatus.PROCESSING)
        self.assertEqual(self.db.query(models.InitialAvailabilityState).count(), 0)

    def test_old_recovery_and_periodic_price_task_are_noops(self):
        from tasks import recover_incomplete_availability
        from tasks_availability_sync import run_availability_sync, sync_published_availability

        mapping = self.db.query(models.ProductMapping).filter_by(product_ean="sku").one()
        before = mapping.price
        self.assertEqual(recover_incomplete_availability.run()["writes"], 0)
        self.assertEqual(run_availability_sync(db=self.db)["status"], "disabled")
        self.assertEqual(sync_published_availability.run()["status"], "disabled")
        self.db.refresh(mapping)
        self.assertEqual(mapping.price, before)

    def test_outbox_dispatch_uses_database_lease_and_retries_publish_failure(self):
        from tasks import dispatch_pending_initial_availability, apply_otto_availability_task

        with patch("tasks.SessionLocal", self.Session), \
             patch.object(apply_otto_availability_task, "apply_async") as enqueue:
            self.assertEqual(dispatch_pending_initial_availability(), 1)
            self.assertEqual(dispatch_pending_initial_availability(), 0)
            enqueue.assert_called_once_with(kwargs={"state_id": self.state.id, "trigger_kind": "automatic"})
        self.db.refresh(self.state)
        self.assertIsNotNone(self.state.dispatch_lease_until)

        self.state.dispatch_lease_until = datetime(2000, 1, 1, tzinfo=timezone.utc)
        self.db.commit()
        with patch("tasks.SessionLocal", self.Session), \
             patch.object(apply_otto_availability_task, "apply_async", side_effect=RuntimeError("broker down")):
            self.assertEqual(dispatch_pending_initial_availability(), 0)
        self.db.refresh(self.state)
        self.assertIsNone(self.state.dispatch_lease_until)
        self.assertIsNotNone(self.state.next_dispatch_at)
        self.assertIn("broker down", self.state.last_dispatch_error)

    def test_review_and_manual_retry_are_actor_attributed_and_scoped(self):
        from fastapi import HTTPException
        from routers.publish_pipeline import (
            AvailabilityReviewRequest, RetryAvailabilityRequest,
            review_initial_availability, retry_availability_endpoint,
        )
        self.state.stock_state = "unknown"
        self.db.commit()
        review = AvailabilityReviewRequest(
            account_name=models.AccountType.JV, operation="stock", outcome="NOT_APPLIED",
            reason="Checked OTTO history", evidence_ref="ticket-123",
        )
        with patch.dict(os.environ, {"AVAILABILITY_REVIEW_TOKEN": "review-secret", "AVAILABILITY_REVIEWER_ID": "operator-1"}):
            with self.assertRaises(HTTPException) as denied:
                review_initial_availability(self.state.id, review, "wrong", self.db)
            self.assertEqual(denied.exception.status_code, 401)
            result = review_initial_availability(self.state.id, review, "review-secret", self.db)
            self.assertEqual(result["state"], "failed")
            event = self.db.query(models.InitialAvailabilityReviewEvent).one()
            self.assertEqual(event.actor_id, "operator-1")
            self.assertEqual(event.evidence_ref, "ticket-123")
            wrong_account_req = RetryAvailabilityRequest(
                eans=["sku"], account_name=models.AccountType.XL,
                reason="Not for this account", evidence_ref="ticket-124",
            )
            wrong_account_result = retry_availability_endpoint(wrong_account_req, "review-secret", self.db)
            self.assertEqual(wrong_account_result["authorized_count"], 0)
            retry_req = RetryAvailabilityRequest(
                eans=["sku"], account_name=models.AccountType.JV,
                reason="Confirmed not applied; corrected setup", evidence_ref="ticket-125",
            )
            with patch("tasks.dispatch_pending_initial_availability", return_value=1) as dispatch:
                retry_result = retry_availability_endpoint(retry_req, "review-secret", self.db)
            dispatch.assert_called_once()
        self.assertEqual(retry_result["authorized_count"], 1)
        self.assertEqual(self.state.stock_state, "pending")
        self.assertEqual(self.state.next_attempt_trigger_kind, "manual")
        self.assertEqual(self.state.manual_retry_count, 1)

    def test_delivery_payload_correction_is_scoped_and_audited(self):
        from fastapi import HTTPException
        from routers.publish_pipeline import (
            DeliveryPayloadCorrectionRequest, correct_initial_delivery_payload,
        )
        from services.otto_availability import OTTO_SHIPPING_PROFILES

        self.state.delivery_state = "failed"
        self.db.commit()
        request = DeliveryPayloadCorrectionRequest(
            account_name=models.AccountType.JV,
            shipping_profile_id=OTTO_SHIPPING_PROFILES["JV"]["EUROPE_4_8_WEEKS"],
            processing_time="37", reason="Corrected profile after rejection",
            evidence_ref="ticket-126",
        )
        with patch.dict(os.environ, {"AVAILABILITY_REVIEW_TOKEN": "review-secret", "AVAILABILITY_REVIEWER_ID": "operator-1"}):
            with patch("tasks.dispatch_pending_initial_availability", return_value=1):
                result = correct_initial_delivery_payload(self.state.id, request, "review-secret", self.db)
            self.assertEqual(result["delivery_state"], "pending")
            self.assertEqual(self.state.delivery_payload_json["sku"], "sku")
            event = self.db.query(models.InitialAvailabilityReviewEvent).filter_by(outcome="PAYLOAD_CORRECTED").one()
            self.assertEqual(event.actor_id, "operator-1")
            self.assertEqual(len(event.payload_hash), 64)
            bad = DeliveryPayloadCorrectionRequest(
                account_name=models.AccountType.XL,
                shipping_profile_id=OTTO_SHIPPING_PROFILES["JV"]["EUROPE_4_8_WEEKS"],
                processing_time="37", reason="Wrong account must fail", evidence_ref="ticket-127",
            )
            with self.assertRaises(HTTPException) as wrong_account:
                correct_initial_delivery_payload(self.state.id, bad, "review-secret", self.db)
            self.assertEqual(wrong_account.exception.status_code, 404)


class TransportTests(unittest.TestCase):
    def test_initial_stock_and_delivery_transport_never_retry_uncertain_request(self):
        import requests
        from services.otto_client import OttoMarketClient

        client = OttoMarketClient("fixture", "fixture")
        with patch.object(client, "get_access_token", return_value="fixture"), \
             patch("services.otto_client.requests.post", side_effect=requests.exceptions.Timeout("timeout")) as post:
            stock = client.update_initial_quantity_once("sku")
        self.assertEqual(post.call_count, 1)
        self.assertEqual(post.call_args.kwargs["json"], [{"sku": "sku", "quantity": 20}])
        self.assertEqual(stock["status_code"], 0)
        self.assertTrue(stock["request_sent"])

        with patch.object(client, "get_access_token", return_value="fixture"), \
             patch("services.otto_client.requests.post", side_effect=requests.exceptions.Timeout("timeout")) as post:
            delivery = client.update_initial_delivery_once(DELIVERY)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(post.call_args.kwargs["json"], [DELIVERY])
        self.assertEqual(delivery["status_code"], 0)
        self.assertTrue(delivery["request_sent"])

    def test_generic_delivery_method_also_has_no_blind_retry(self):
        from services.otto_client import OttoMarketClient

        client = OttoMarketClient("fixture", "fixture")
        response = Mock(status_code=503, text="Unavailable")
        response.json.return_value = {}
        with patch.object(client, "get_access_token", return_value="fixture"), \
             patch("services.otto_client.requests.post", return_value=response) as post, \
             patch("services.otto_client.time.sleep") as sleep:
            result = client.update_product_delivery_information([DELIVERY])
        self.assertEqual(result["status_code"], 503)
        post.assert_called_once()
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()

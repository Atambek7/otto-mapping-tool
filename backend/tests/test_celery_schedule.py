"""Only Drip publication, its OTTO processing poll, and daily order stats remain periodic."""
import unittest

from worker import celery_app


class CeleryScheduleTests(unittest.TestCase):
    def test_periodic_price_stock_and_availability_recovery_are_not_scheduled(self):
        schedule = celery_app.conf.beat_schedule
        task_names = {entry["task"] for entry in schedule.values()}
        self.assertEqual(task_names, {
            "tasks.process_scheduled_publish_tick",
            "tasks.poll_otto_processing_items",
            "tasks.sync_order_stats",
        })
        self.assertNotIn("tasks.recover_incomplete_availability", task_names)
        self.assertNotIn("tasks_availability_sync.sync_published_availability", task_names)


if __name__ == "__main__":
    unittest.main()

from django.test import SimpleTestCase

from apps.api import chat_portal


class StatusEventEnqueueTests(SimpleTestCase):
    def test_context_progress_emitted_for_knowledge_states(self) -> None:
        queue: list[dict] = []
        chat_portal._enqueue_status_events(queue, code="searching_knowledge", label="Searching docs")

        self.assertEqual(len(queue), 2)
        ctx, status = queue
        self.assertEqual(ctx["type"], "context_progress")
        self.assertEqual(ctx["state"], "searching_knowledge")
        self.assertEqual(ctx["label"], "Searching docs")

        self.assertEqual(status["type"], "status")
        self.assertEqual(status["state"], "searching_knowledge")
        self.assertEqual(status["label"], "Searching docs")

    def test_status_only_for_non_context_codes(self) -> None:
        queue: list[dict] = []
        chat_portal._enqueue_status_events(queue, code="foo_bar")

        self.assertEqual(len(queue), 1)
        status = queue[0]
        self.assertEqual(status["type"], "status")
        self.assertEqual(status["state"], "foo_bar")
        self.assertEqual(status["label"], "Foo Bar")

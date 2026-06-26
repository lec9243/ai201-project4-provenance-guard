import os
import tempfile
import unittest


class ProvenanceGuardTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        os.environ["PROVENANCE_DATA_DIR"] = self.temp_dir.name
        os.environ["GROQ_API_KEY"] = ""
        os.environ["RATELIMIT_ENABLED"] = "false"

        from app import create_app

        self.app = create_app()
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_submit_returns_classification_and_audit_log(self):
        response = self.client.post(
            "/submit",
            json={
                "creator_id": "test-user-1",
                "text": (
                    "Artificial intelligence represents a transformative paradigm shift "
                    "in modern society. It is important to note that stakeholders must "
                    "collaborate to ensure responsible deployment."
                ),
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertIn("content_id", body)
        self.assertIn(body["attribution"], {"likely_ai", "likely_human", "uncertain"})
        self.assertIn("confidence", body)
        self.assertIn("label", body)
        self.assertIn("llm_semantic", body["signals"])
        self.assertIn("stylometric", body["signals"])
        self.assertIn("lexical_specificity", body["signals"])

        log_response = self.client.get("/log")
        entries = log_response.get_json()["entries"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["event_type"], "submission")
        self.assertIn("signals", entries[0])
        self.assertIn("lexical_specificity", entries[0]["signals"])

    def test_appeal_updates_status_and_log(self):
        submit_response = self.client.post(
            "/submit",
            json={
                "creator_id": "test-user-2",
                "text": (
                    "ok so i finally tried that ramen place downtown and honestly? "
                    "the broth was fine but they put WAY too much sodium in it."
                ),
            },
        )
        content_id = submit_response.get_json()["content_id"]

        appeal_response = self.client.post(
            "/appeal",
            json={
                "content_id": content_id,
                "creator_reasoning": "I wrote this from a real meal with my friend.",
            },
        )

        self.assertEqual(appeal_response.status_code, 200)
        self.assertEqual(appeal_response.get_json()["status"], "under_review")

        log_response = self.client.get("/log")
        entries = log_response.get_json()["entries"]
        self.assertEqual(entries[-1]["event_type"], "appeal")
        self.assertEqual(entries[-1]["status"], "under_review")
        self.assertIn("appeal_reasoning", entries[-1])

        appeals_response = self.client.get("/appeals")
        queue = appeals_response.get_json()["entries"]
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]["content_id"], content_id)

    def test_verified_human_certificate_appears_on_submission(self):
        verify_response = self.client.post(
            "/verify-human",
            json={
                "creator_id": "verified-creator",
                "process_statement": (
                    "I draft my posts in a notebook, revise them twice, and keep dated "
                    "draft excerpts before publishing."
                ),
                "draft_excerpt": (
                    "First rough draft from my notebook: I wanted to describe the porch "
                    "coffee scene before revising the ending."
                ),
            },
        )

        self.assertEqual(verify_response.status_code, 200)
        certificate = verify_response.get_json()["certificate"]
        self.assertEqual(certificate["status"], "verified_human")

        submit_response = self.client.post(
            "/submit",
            json={
                "creator_id": "verified-creator",
                "text": (
                    "I wrote this on the porch yesterday while drinking coffee and "
                    "editing an older notebook draft about my neighborhood."
                ),
            },
        )
        body = submit_response.get_json()
        self.assertEqual(submit_response.status_code, 200)
        self.assertIsNotNone(body["provenance_certificate"])
        self.assertIn("Verified Human Creator", body["provenance_certificate"]["display_label"])

    def test_metadata_submission_and_analytics(self):
        metadata_response = self.client.post(
            "/submit",
            json={
                "creator_id": "metadata-user",
                "content_type": "metadata",
                "metadata": {
                    "title": "Downtown mural study",
                    "description": "Photo set documenting a painted wall near the train station.",
                    "alt_text": "A blue and yellow mural with chipped paint and a bus stop nearby.",
                    "tags": ["mural", "street art", "downtown"],
                    "creator_process": "I photographed this after work and selected the frame manually.",
                },
            },
        )

        self.assertEqual(metadata_response.status_code, 200)
        body = metadata_response.get_json()
        self.assertEqual(body["content_type"], "metadata")
        self.assertIn("lexical_specificity", body["signals"])

        analytics_response = self.client.get("/analytics")
        summary = analytics_response.get_json()
        self.assertIn("verdict_counts", summary)
        self.assertIn("appeal_rate", summary)
        self.assertIn("average_confidence", summary)
        self.assertIn("metadata", summary["content_type_counts"])

        dashboard_response = self.client.get("/dashboard")
        self.assertEqual(dashboard_response.status_code, 200)
        self.assertIn(b"Provenance Guard Analytics", dashboard_response.data)


if __name__ == "__main__":
    unittest.main()

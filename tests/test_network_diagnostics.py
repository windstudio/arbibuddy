import unittest
from urllib.error import HTTPError, URLError

from scripts.network_diagnostics import classify_network_error


class NetworkDiagnosticsTests(unittest.TestCase):
    def test_permission_error_is_recoverable(self):
        diagnostic = classify_network_error(
            URLError(PermissionError(13, "network permission denied"))
        )

        self.assertEqual(diagnostic["kind"], "network_permission")
        self.assertEqual(diagnostic["error_code"], "network_permission_required")
        self.assertTrue(diagnostic["recoverable"])
        self.assertEqual(
            diagnostic["next_action"],
            "request-network-permission-and-retry-same-command",
        )

    def test_connection_failure_is_recoverable_without_exposing_reason_text(self):
        diagnostic = classify_network_error(
            URLError(ConnectionRefusedError(10061, "connection refused"))
        )

        self.assertEqual(diagnostic["kind"], "network_unavailable")
        self.assertEqual(
            diagnostic["error_code"], "official_source_network_unavailable"
        )
        self.assertTrue(diagnostic["recoverable"])
        self.assertEqual(diagnostic["next_action"], "try-next-official-source")
        self.assertNotIn("connection refused", str(diagnostic))

    def test_http_failure_is_not_replayed_as_a_permission_failure(self):
        error = HTTPError("https://example.invalid", 503, "unavailable", {}, None)
        try:
            diagnostic = classify_network_error(error)
        finally:
            error.close()

        self.assertEqual(diagnostic["kind"], "http_error")
        self.assertEqual(diagnostic["error_code"], "official_source_http_error")
        self.assertFalse(diagnostic["recoverable"])
        self.assertEqual(
            diagnostic["next_action"], "report_failure_and_wait_for_user"
        )


if __name__ == "__main__":
    unittest.main()

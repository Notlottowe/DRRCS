import sys
import ssl
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cloud_api import CloudAPI, ApiError
from test_backend import case


class ClientTests(unittest.TestCase):
    @case("API security", "Keep HTTPS certificate and hostname verification enabled", "Create HTTPS client using platform trust store", "CERT_REQUIRED and hostname verification stay enabled", "TLS trust-store initialization")
    def test_https_verification(self):
        client = CloudAPI({"api_url": "https://example.com", "project_key": "test-only"})
        self.assertEqual(ssl.CERT_REQUIRED, client.context.verify_mode)
        self.assertTrue(client.context.check_hostname)
        self.actual = [{"certificate_required": True, "hostname_verification": True}]

    @case("API security", "Reject unencrypted remote backends", "http://example.com", "Client rejects HTTP before any credentials are transmitted", "HTTPS endpoint validation")
    def test_no_remote_http(self):
        with self.assertRaises(ApiError) as error:
            CloudAPI({"api_url": "http://example.com", "project_key": "test-only"})
        self.actual = [str(error.exception)]

"""Send desktop requests to the cloud API."""
import json
import ssl
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


class ApiError(Exception):
    pass


class CloudAPI:
    def __init__(self, config):
        self.url = config["api_url"].rstrip("/")
        if not (self.url.startswith("https://") or self.url.startswith("http://127.0.0.1:")):
            raise ApiError("Use HTTPS for the cloud backend.")
        self.key = config["project_key"]
        self.token = ""
        self.context = ssl.create_default_context()
        # Some python.org Mac installs have no bundled CA certificates.
        if self.context.cert_store_stats()["x509_ca"] == 0:
            for bundle in ["/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt"]:
                if Path(bundle).is_file():
                    self.context.load_verify_locations(cafile=bundle)
                    break

    def call(self, command, data=None):
        body = json.dumps({"command": command, "data": data or {}}).encode()
        headers = {"Content-Type": "application/json", "Accept": "application/json",
                   "User-Agent": "DRRCS-Terminal/1.0", "X-Project-Key": self.key}
        if self.token:
            headers["Authorization"] = "Bearer "+self.token
        request = Request(self.url+"/api", body, headers, method="POST")
        try:
            with urlopen(request, timeout=30, context=self.context) as response:
                return json.load(response)["data"]
        except HTTPError as error:
            try:
                result = json.load(error)
                raise ApiError(result.get("message", str(error))) from None
            except (json.JSONDecodeError, KeyError):
                raise ApiError(f"The cloud server returned HTTP {error.code}.") from None
        except (URLError, TimeoutError, OSError):
            raise ApiError("Cannot reach Cloudflare. Check your connection. A write may have reached the server; check the record before retrying.") from None

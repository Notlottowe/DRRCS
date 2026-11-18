"""Check a real Worker through HTTP. Tokens and passwords are never saved in results."""
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from cloud_api import CloudAPI, ApiError
from prepare_cloud import OWNER

ROOT = Path(__file__).resolve().parent


def verify(config_file, output_file):
    config = json.loads(Path(config_file).read_text())
    access = json.loads((OWNER/"access.json").read_text())
    api = CloudAPI(config)
    checks = []

    def record(name, actual):
        checks.append({"name": name, "status": "PASS", "actual": actual})
        print("PASS "+name, flush=True)

    def login(client, username):
        response = client.call("login", {"username": username, "password": access["password"]})
        client.token = response["token"]
        return response["user"]

    headers = {"User-Agent": "DRRCS-Terminal/1.0", "Accept": "application/json"}
    with urlopen(Request(config["api_url"]+"/health", headers=headers), timeout=30, context=api.context) as response:
        health = json.load(response)
    assert health["storage"] == "Cloudflare D1"
    record("Worker health", health)
    request = Request(config["api_url"]+"/api", b'{"command":"users"}', headers | {"Content-Type": "application/json"})
    try:
        urlopen(request, timeout=30, context=api.context)
        raise AssertionError("Request without project key was accepted")
    except HTTPError as error:
        assert error.code == 401
        record("API rejects missing project key", {"http_status": error.code})
    shelters = api.call("shelters")
    assert shelters and not {"manager", "supplies", "internal_contact"} & set(shelters[0])
    record("Public search excludes private fields", [{"id": r["id"], "name": r["name"]} for r in shelters])
    login(api, "dispatcher")
    description = "Deployment verification: synthetic flooded road. This is classroom sample data."
    row = api.call("incident_add", {"type": "Flood", "description": description, "location": "41.88,-87.63", "area": "Central", "severity": 2})
    record("Record a new incident in the backend", {"id": row["id"], "status": row["status"]})
    second = CloudAPI(config)
    login(second, "dispatcher")
    retrieved = second.call("incident", {"id": row["id"]})
    assert retrieved["description"] == description
    record("Separate login retrieves the saved incident", {"id": retrieved["id"], "description": retrieved["description"]})
    assert second.call("incident_history", {"id": row["id"]})
    record("Incident history retained", {"id": row["id"], "history_records": len(second.call("incident_history", {"id": row["id"]}))})
    api.call("logout")
    api.token = ""
    login(api, "resource")
    dashboard = api.call("dashboard")
    assert "active_incidents" not in dashboard
    try:
        api.call("incident", {"id": row["id"]})
        raise AssertionError("Resource Coordinator received incident detail")
    except ApiError as error:
        record("Cloud backend enforces role scope", {"denied": str(error), "scope": dashboard["scope"]})
    api.call("logout")
    login(api, "shelter")
    before = api.call("shelter", {"id": "SH-001"})
    try:
        api.call("occupancy", {"id": "SH-001", "delta": "gibberish", "version": before["version"]})
        raise AssertionError("Gibberish occupancy accepted")
    except ApiError as error:
        record("Wrong input rejected by cloud backend", {"message": str(error)})
    for delta in [1, -1]:
        preview = api.call("occupancy", {"id": "SH-001", "delta": delta, "preview": True})
        api.call("occupancy", {"id": "SH-001", "delta": delta, "version": preview["version"]})
    after = api.call("shelter", {"id": "SH-001"})
    assert after["occupancy"] == before["occupancy"] and after["version"] == before["version"]+2
    record("Atomic shelter writes persisted; count restored", {"occupancy": after["occupancy"], "version_before": before["version"], "version_after": after["version"]})
    api.call("logout")
    login(api, "admin")
    clock = datetime.now(timezone.utc)
    draft = api.call("report_create", {"kind": "Operational", "start": (clock-timedelta(days=2)).isoformat(), "end": (clock+timedelta(days=1)).isoformat(), "area": "All", "notes": "Cloud deployment verification using synthetic classroom data."})
    final = api.call("report_finalize", {"id": draft["id"], "version": draft["version"]})
    assert final["status"] == "Finalized"
    record("Cloud report draft finalized", {"id": final["id"], "status": final["status"]})
    try:
        api.call("report_notes", {"id": final["id"], "version": final["version"], "notes": "Attempted edit after finalization"})
        raise AssertionError("Finalized report was editable")
    except ApiError as error:
        record("Finalized cloud report rejects edits", {"message": str(error)})
    api.call("logout")
    login(api, "sysadmin")
    audit = api.call("audit")
    assert any(r["target"] == row["id"] and r["action"] == "INCIDENT_RECORDED" for r in audit["events"])
    record("Cloud audit contains the new event", {"incident_id": row["id"], "audit_events_returned": len(audit["events"])})
    api.call("logout")
    try:
        api.call("users")
        raise AssertionError("Logged-out session still worked")
    except ApiError as error:
        record("Cloud logout revokes session", {"message": str(error)})
    second.call("logout")
    result = {"run_at_utc": clock.isoformat(), "api_url": config["api_url"], "checks": checks,
              "incident_id": row["id"], "report_id": final["id"], "passed": len(checks)}
    Path(output_file).write_text(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    verify(sys.argv[1] if len(sys.argv) > 1 else ROOT/".private/client.json", ROOT/"test_results/cloud_verification.json")

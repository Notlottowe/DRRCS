"""White-box tests for UC-1 to UC-11 using the same isolated D1 substitute as test_backend.py."""
import json
import unittest
from datetime import timedelta

import test_backend as base
from test_backend import case

PASSWORD = "TestPassword2026!"


class FieldOperationTests(unittest.IsolatedAsyncioTestCase):
    """Reuses the seeded database, accounts and helpers from the UC-12..24 suite."""
    setUpClass = classmethod(base.CloudBackendTests.setUpClass.__func__)
    tearDownClass = classmethod(base.CloudBackendTests.tearDownClass.__func__)
    asyncSetUp = base.CloudBackendTests.asyncSetUp
    asyncTearDown = base.CloudBackendTests.asyncTearDown
    token = base.CloudBackendTests.token
    call = base.CloudBackendTests.call
    reject = base.CloudBackendTests.reject

    async def record(self, kind, rid):
        return await self.backend.record(kind, rid)

    async def alerts_for(self, recipient):
        return [r["message"] for r in await self.backend.rows("SELECT * FROM alerts WHERE recipient=?", recipient)]

    def citizen_report(self, **changes):
        return {"type": "Fire", "address": "120 Main St, Central", "location": "41.881,-87.629",
                "description": "Smoke coming out of the roof of a two-story house.", "severity": 4,
                "occurred_at": (self.clock-timedelta(minutes=20)).isoformat(), "contact_name": "Ana Ruiz",
                "contact_phone": "+1 312-555-0142", **changes}

    def new_incident(self, **changes):
        return {"id": "RPT-DEMB6789", "version": 1, "action": "create", "type": "Hazard", "description": "Tree on power lines, lines on the street.",
                "location": [41.8857, -87.6339], "area": "Central", "severity": 3, "priority": "High",
                "needed_qualification": "", "responders_needed": 1, **changes}

    def request(self, **changes):
        return {"id": "IN-001", "resource_type": "Water", "quantity": 50, "urgency": "High",
                "delivery_location": "River Road staging area", "justification": "Families stranded without drinking water.", **changes}

    def shelter(self, **changes):
        return {"id": "SH-001", "version": 1, "capacity": 100, "state": "Open", "public_contact": "312-555-0100",
                "internal_contact": "Internal desk extension 204", "accessibility": "Wheelchair accessible",
                "medical_support": True, "supplies": {"food": 200, "water": 400}, **changes}

    def resource(self, **changes):
        return {"id": "GEN-001", "version": 1, "name": "Portable generator", "kind": "Equipment", "category": "Generators",
                "area": "Central", "status": "Available", "total": 4, **changes}

    # ---------------- UC-1 ----------------
    @case("UC-1", "Submit a complete citizen report without an account", "Fire, 120 Main St, 41.881,-87.629, severity 4, valid time, name and phone",
          "Report saved as Submitted with an RPT ID; dispatchers alerted; audit written", "A2 valid input; A4 save, notify, audit")
    async def test_uc01_submit(self):
        result = await self.call("report_submit", self.citizen_report(), None)
        self.assertRegex(result["id"], r"^RPT-[A-HJ-NP-Z2-9]{8}$")
        row = await self.record("citizen_reports", result["id"])
        self.assertEqual(("Submitted", [41.881, -87.629]), (row["status"], row["location"]))
        self.assertTrue(any(result["id"] in m for m in await self.alerts_for("Dispatcher")))
        self.assertTrue(await self.backend.rows("SELECT * FROM audit WHERE action='UC-1 REPORT_SUBMITTED' AND target=?", result["id"]))

    @case("UC-1", "Reject missing required fields", "Empty type, location, description, severity and time",
          "Each attempt rejected with a field message; nothing saved", "A3.4A missing information")
    async def test_uc01_missing(self):
        for key in ["type", "address", "description", "severity", "occurred_at"]:
            await self.reject("report_submit", self.citizen_report(**{key: ""}), None)
        self.assertEqual(2, len(await self.backend.records("citizen_reports")))

    @case("UC-1", "Reject gibberish and invalid values", "Type 'Alien invasion', severity 'abc'/9, location 91,0 and NaN, future time, phone 'call me', name 'R2-D2 <b>'",
          "All rejected; nothing saved", "A3.4B invalid information")
    async def test_uc01_gibberish(self):
        bad = [{"type": "Alien invasion"}, {"severity": "abc"}, {"severity": 9}, {"location": "91,0"}, {"location": "NaN,0"},
               {"occurred_at": (self.clock+timedelta(days=1)).isoformat()}, {"occurred_at": "yesterday"},
               {"contact_phone": "call me"}, {"contact_name": "R2-D2 <b>"}, {"description": "help"}]
        for change in bad:
            await self.reject("report_submit", self.citizen_report(**change), None, "INVALID_INPUT")
        self.assertEqual(2, len(await self.backend.records("citizen_reports")))

    @case("UC-1", "Store injection text as plain data", "Address \"'; DROP TABLE records; --\", description with <script>",
          "Saved literally through parameterized SQL; records table intact", "Parameterized D1 statements")
    async def test_uc01_injection(self):
        result = await self.call("report_submit", self.citizen_report(address="'; DROP TABLE records; --",
                                                                       description="<script>alert('x')</script> fire near the school"), None)
        row = await self.record("citizen_reports", result["id"])
        self.assertEqual("'; DROP TABLE records; --", row["address"])
        self.assertEqual(3, len(await self.backend.records("citizen_reports")))

    @case("UC-1", "Limit report flooding from one connection", "6 reports from client 203.0.113.9 within 10 minutes, then one from another client",
          "Sixth rejected (RATE_LIMITED); other client accepted", "Public write rate limit")
    async def test_uc01_rate_limit(self):
        for _ in range(5):
            await self.backend.run("report_submit", self.citizen_report(), "", "203.0.113.9")
        with self.assertRaises(Exception) as caught:
            await self.backend.run("report_submit", self.citizen_report(), "", "203.0.113.9")
        self.assertEqual("RATE_LIMITED", caught.exception.code)
        await self.backend.run("report_submit", self.citizen_report(), "", "198.51.100.4")
        self.actual.append({"reports_saved": len(await self.backend.records("citizen_reports"))})
        self.assertEqual(8, len(await self.backend.records("citizen_reports")))

    # ---------------- UC-2 ----------------
    @case("UC-2", "Check status with a valid ID", "rpt-dema2345 (lower case)", "Public status text, type and time only; no contact details",
          "B2 valid ID; public projection")
    async def test_uc02_valid(self):
        result = await self.call("report_status", {"id": "rpt-dema2345"}, None)
        self.assertEqual("Received - waiting for dispatcher review", result["status"])
        self.assertFalse({"contact_name", "contact_phone", "description", "location"} & set(result))

    @case("UC-2", "Reject wrong, unknown and injection IDs", "hello123, RPT-ZZZZZZZZ, ' OR 1=1 --", "Same NOT_FOUND message for all; no data revealed",
          "B2.4A not found")
    async def test_uc02_not_found(self):
        for rid in ["hello123", "RPT-ZZZZZZZZ", "' OR 1=1 --", "", "RPT-DEMA2345' OR '1'='1"]:
            await self.reject("report_status", {"id": rid}, None, "NOT_FOUND")

    @case("UC-2", "Status follows dispatcher review", "Associate RPT-DEMA2345, then check status", "Linked to an incident that is already being handled",
          "B3 status after UC-3")
    async def test_uc02_after_review(self):
        await self.call("report_review", {"id": "RPT-DEMA2345", "version": 1, "action": "associate", "incident_id": "IN-001"}, "dispatcher")
        result = await self.call("report_status", {"id": "RPT-DEMA2345"}, None)
        self.assertEqual("Linked to an incident that is already being handled", result["status"])

    # ---------------- UC-3 ----------------
    @case("UC-3", "List submitted reports and rank matching incidents", "Dispatcher; matches for RPT-DEMA2345", "Both demo reports listed; IN-001 suggested first",
          "C1 queue; C2 matching algorithm")
    async def test_uc03_list_matches(self):
        rows = await self.call("citizen_reports", {}, "dispatcher")
        self.assertEqual(["RPT-DEMA2345", "RPT-DEMB6789"], [r["id"] for r in rows])
        matches = await self.call("report_matches", {"id": "RPT-DEMA2345"}, "dispatcher")
        self.assertEqual("IN-001", matches[0]["id"])

    @case("UC-3", "Verify a report and create an incident", "RPT-DEMB6789: preview then create Hazard severity 3 priority High",
          "Preview writes nothing; new Reported incident keeps the source report; report Verified", "C3 create incident; C4 save")
    async def test_uc03_create(self):
        preview = await self.call("report_review", self.new_incident(preview=True), "dispatcher")
        self.assertEqual(3, len(await self.backend.records("incidents")))
        incident = await self.call("report_review", self.new_incident(), "dispatcher")
        self.assertEqual(("Reported", "RPT-DEMB6789", "High"), (incident["status"], incident["originating_report"], incident["priority"]))
        report = await self.record("citizen_reports", "RPT-DEMB6789")
        self.assertEqual(("Verified", incident["id"]), (report["status"], report["incident_id"]))
        self.assertTrue(preview["incident"]["id"].startswith("IN-"))

    @case("UC-3", "Associate a report with an active incident", "RPT-DEMA2345 -> IN-001", "Report Associated; IN-001 lists the report; history entry",
          "C3A associate")
    async def test_uc03_associate(self):
        incident = await self.call("report_review", {"id": "RPT-DEMA2345", "version": 1, "action": "associate", "incident_id": "IN-001"}, "dispatcher")
        self.assertIn("RPT-DEMA2345", incident["reports"])
        self.assertEqual("Associated", (await self.record("citizen_reports", "RPT-DEMA2345"))["status"])
        await self.reject("report_review", {"id": "RPT-DEMB6789", "version": 1, "action": "associate", "incident_id": "IN-002"}, "dispatcher", "CONFLICT")

    @case("UC-3", "Classify a report that is not an incident", "Non-emergency without reason, then with reason",
          "Missing reason rejected; report kept with status Non-emergency", "C3B classify")
    async def test_uc03_classify(self):
        data = {"id": "RPT-DEMB6789", "version": 1, "action": "classify", "classification": "Non-emergency"}
        await self.reject("report_review", data, "dispatcher")
        row = await self.call("report_review", data | {"reason": "Utility company already on site."}, "dispatcher")
        self.assertEqual("Non-emergency", row["status"])
        await self.reject("report_review", data | {"reason": "Second click on the same report."}, "dispatcher", "CONFLICT")

    @case("UC-3", "Reject invalid incident values, stale versions and other roles", "Blank/gibberish fields; version 7; responder and guest",
          "Invalid rejected; stale CONFLICT; responder FORBIDDEN; guest AUTHENTICATION", "C3.4A invalid; access control")
    async def test_uc03_invalid_access(self):
        for change in [{"type": "Zombies"}, {"description": "short"}, {"severity": "high"}, {"location": "abc"},
                       {"priority": "ASAP"}, {"needed_qualification": "Wizard"}, {"responders_needed": 0}]:
            await self.reject("report_review", self.new_incident(**change), "dispatcher")
        await self.reject("report_review", self.new_incident(version=7), "dispatcher", "CONFLICT")
        await self.reject("citizen_reports", {}, "responder", "FORBIDDEN")
        await self.reject("citizen_reports", {}, None, "AUTHENTICATION")
        self.assertEqual("Submitted", (await self.record("citizen_reports", "RPT-DEMB6789"))["status"])

    # ---------------- UC-4 ----------------
    @case("UC-4", "Dispatcher raises severity with review and version check", "IN-001 severity 4 -> 5: preview, save v1, save stale v1 again",
          "Preview lists the change; saved v2 with history and dispatcher alert; stale write CONFLICT", "D3 review; D4 save; D4.5A conflict")
    async def test_uc04_dispatcher(self):
        preview = await self.call("incident_update", {"id": "IN-001", "version": 1, "severity": 5, "preview": True}, "dispatcher")
        self.assertEqual({"from": 4, "to": 5}, preview["changes"]["severity"])
        row = await self.call("incident_update", {"id": "IN-001", "version": 1, "severity": 5}, "dispatcher")
        self.assertEqual((5, 2), (row["severity"], row["version"]))
        self.assertTrue(any("severity raised" in m for m in await self.alerts_for("Dispatcher")))
        await self.reject("incident_update", {"id": "IN-001", "version": 1, "priority": "Urgent"}, "dispatcher", "CONFLICT")

    @case("UC-4", "Reject gibberish, no-change and closed-incident updates", "Latitude 'north', severity -3, type 'Huge'; unchanged values; IN-002 Resolved",
          "All rejected; IN-001 stays version 1", "D3.4A invalid; D3.6B lifecycle")
    async def test_uc04_invalid(self):
        for change in [{"location": "north,0"}, {"severity": -3}, {"type": "Huge"}, {"responders_needed": "many"}, {"description": ""}]:
            await self.reject("incident_update", {"id": "IN-001", "version": 1, **change}, "dispatcher")
        await self.reject("incident_update", {"id": "IN-001", "version": 1, "severity": 4}, "dispatcher")
        await self.reject("incident_update", {"id": "IN-002", "version": 1, "severity": 3}, "dispatcher", "CONFLICT")
        self.assertEqual(1, (await self.record("incidents", "IN-001"))["version"])

    @case("UC-4", "Limit First Responders to their assigned incident's description", "Responder edits IN-001 description; tries severity; edits unassigned IN-003",
          "Description saved; severity FORBIDDEN; IN-003 FORBIDDEN", "D2 permitted fields; assignment check")
    async def test_uc04_responder(self):
        row = await self.call("incident_update", {"id": "IN-001", "version": 1, "description": "Water level rising, three houses now flooded."}, "responder")
        self.assertTrue(row["description"].startswith("Water level rising"))
        await self.reject("incident_update", {"id": "IN-001", "version": 2, "severity": 1}, "responder", "FORBIDDEN")
        await self.reject("incident_update", {"id": "IN-003", "version": 1, "description": "Trying to edit another incident."}, "responder", "FORBIDDEN")

    # ---------------- UC-5 ----------------
    @case("UC-5", "Assign an available qualified responder", "IN-003: candidates, preview team-2, confirm with versions",
          "Only team-2 offered; IN-003 Assigned; team-2 Assigned; assignment and alert saved", "E2 ranking; E4 one batch")
    async def test_uc05_assign(self):
        options = await self.call("assign_options", {"id": "IN-003"}, "dispatcher")
        self.assertEqual(["team-2"], [c["id"] for c in options["candidates"]])
        preview = await self.call("assign", {"id": "IN-003", "responders": ["team-2"], "preview": True}, "dispatcher")
        row = await self.call("assign", {"id": "IN-003", "version": preview["version"], "responders": ["team-2"], "versions": preview["versions"]}, "dispatcher")
        self.assertEqual("Assigned", row["status"])
        self.assertEqual("Assigned", (await self.record("responders", "team-2"))["status"])
        self.assertTrue(await self.alerts_for("team-2"))

    @case("UC-5", "Escalate when nobody has the needed qualification", "IN-003 needs Paramedic; escalation note",
          "No candidates; Emergency Administrator alerted", "E2A no qualified personnel")
    async def test_uc05_no_qualified(self):
        await self.call("incident_update", {"id": "IN-003", "version": 1, "needed_qualification": "Paramedic"}, "dispatcher")
        options = await self.call("assign_options", {"id": "IN-003"}, "dispatcher")
        self.assertEqual([], options["candidates"])
        await self.call("assign_escalation", {"id": "IN-003", "note": "Need a paramedic team for this incident."}, "dispatcher")
        self.assertTrue(await self.alerts_for("Emergency Administrator"))

    @case("UC-5", "Reject busy, gibberish and concurrently changed responders", "responder (busy), '1 OR 1=1', empty list, stale team-2 version",
          "CONFLICT/invalid; nothing saved; IN-003 stays Reported", "E3.8A unavailable; E4 rollback")
    async def test_uc05_conflicts(self):
        await self.reject("assign", {"id": "IN-003", "version": 1, "responders": ["responder"], "versions": {"responder": 1}}, "dispatcher", "CONFLICT")
        await self.reject("assign", {"id": "IN-003", "version": 1, "responders": ["1 OR 1=1"], "versions": {}}, "dispatcher", "CONFLICT")
        await self.reject("assign", {"id": "IN-003", "version": 1, "responders": []}, "dispatcher")
        await self.reject("assign", {"id": "IN-003", "version": 1, "responders": "team-2"}, "dispatcher")
        await self.reject("assign", {"id": "IN-003", "version": 1, "responders": ["team-2"], "versions": {"team-2": 9}}, "dispatcher", "CONFLICT")
        self.assertEqual("Reported", (await self.record("incidents", "IN-003"))["status"])
        self.assertEqual(2, len(await self.backend.records("assignments")))
        await self.reject("assign_options", {"id": "IN-003"}, "resource", "FORBIDDEN")

    # ---------------- UC-6 ----------------
    @case("UC-6", "Record progress that matches the assignment state", "Responder on IN-001 (Responding): Arrived on scene, Situation update, Contained twice",
          "Arrival rejected as state mismatch; update and containment saved; second Contained CONFLICT", "F3 rules; F3.8B state conflict")
    async def test_uc06_rules(self):
        await self.reject("progress", {"id": "IN-001", "version": 1, "update_type": "ArrivedOnScene", "note": "Arrived on scene now."}, "responder", "CONFLICT")
        await self.call("progress", {"id": "IN-001", "version": 1, "update_type": "SituationUpdate", "note": "Water still rising slowly."}, "responder")
        row = await self.call("progress", {"id": "IN-001", "version": 2, "update_type": "Contained", "note": "All residents evacuated."}, "responder")
        self.assertTrue(row["contained"])
        await self.reject("progress", {"id": "IN-001", "version": 3, "update_type": "Contained", "note": "Contained again."}, "responder", "CONFLICT")
        self.assertTrue(any("field update" in m for m in await self.alerts_for("Dispatcher")))

    @case("UC-6", "Complete an assignment, get reassigned and arrive on scene", "Complete IN-001; dispatcher assigns responder to IN-003; Arrived on scene",
          "Responder Available then Assigned; IN-003 Assigned -> Responding; responder Responding", "F4 state changes across UC-5/6")
    async def test_uc06_cycle(self):
        await self.call("progress", {"id": "IN-001", "version": 1, "update_type": "AssignmentCompleted", "note": "Leaving the site, job done."}, "responder")
        self.assertEqual("Available", (await self.record("responders", "responder"))["status"])
        preview = await self.call("assign", {"id": "IN-003", "responders": ["responder"], "preview": True}, "dispatcher")
        await self.call("assign", {"id": "IN-003", "version": 1, "responders": ["responder"], "versions": preview["versions"]}, "dispatcher")
        row = await self.call("progress", {"id": "IN-003", "version": 2, "update_type": "ArrivedOnScene", "note": "On site with the boat."}, "responder")
        self.assertEqual("Responding", row["status"])
        self.assertEqual("Responding", (await self.record("responders", "responder"))["status"])

    @case("UC-6", "Reject gibberish input and unassigned responders", "Type 'DROP TABLE', empty note; responder on IN-003 without assignment",
          "Invalid rejected; unassigned FORBIDDEN", "F3.8A not assigned; input checks")
    async def test_uc06_invalid(self):
        await self.reject("progress", {"id": "IN-001", "version": 1, "update_type": "DROP TABLE records", "note": "Something."}, "responder")
        await self.reject("progress", {"id": "IN-001", "version": 1, "update_type": "SituationUpdate", "note": ""}, "responder")
        await self.reject("progress", {"id": "IN-003", "version": 1, "update_type": "SituationUpdate", "note": "Not my incident."}, "responder", "FORBIDDEN")
        await self.reject("progress", {"id": "IN-001", "version": 1, "update_type": "SituationUpdate", "note": "Wrong role."}, "dispatcher", "FORBIDDEN")

    # ---------------- UC-7 ----------------
    @case("UC-7", "Escalate an assigned incident", "IN-001 severity 4 -> 5, priority Medium -> Urgent, field note",
          "Preview shows TR-001 restriction; saved with Escalation history and urgent dispatcher alert", "G2 summary; G4 save")
    async def test_uc07_escalate(self):
        data = {"id": "IN-001", "version": 1, "severity": 5, "priority": "Urgent", "field_note": "Water rising 10 cm per hour, more houses at risk."}
        preview = await self.call("escalate", data | {"preview": True}, "responder")
        self.assertIn("River Road closure", preview["nearby_restrictions"])
        row = await self.call("escalate", data, "responder")
        self.assertEqual((5, "Urgent"), (row["severity"], row["priority"]))
        self.assertTrue(any(m.startswith("URGENT escalation IN-001") for m in await self.alerts_for("Dispatcher")))

    @case("UC-7", "Reject lowering, unchanged, gibberish and stale escalations", "Severity 1; same values; note 'bad'; severity 'MEGA'; stale version",
          "All rejected; IN-001 unchanged", "G3.4A invalid; G4.5A conflict")
    async def test_uc07_invalid(self):
        base = {"id": "IN-001", "version": 1, "severity": 4, "priority": "Medium", "field_note": "Conditions are getting worse."}
        for change in [{"severity": 1}, {}, {"field_note": "bad"}, {"severity": "MEGA"}, {"priority": "<b>now</b>"}]:
            await self.reject("escalate", base | change, "responder")
        await self.reject("escalate", base | {"severity": 5, "version": 4}, "responder", "CONFLICT")
        await self.reject("escalate", base | {"id": "IN-003", "severity": 5}, "responder", "FORBIDDEN")
        self.assertEqual(4, (await self.record("incidents", "IN-001"))["severity"])

    # ---------------- UC-8 ----------------
    @case("UC-8", "Request additional resources for an assigned incident", "IN-001: 50 Water, High, delivery location, justification",
          "Preview shows road restriction and writes nothing; RQ saved Pending with score 77; coordinator alerted", "H2 summary; H4 save")
    async def test_uc08_request(self):
        preview = await self.call("resource_request", self.request(preview=True), "responder")
        self.assertEqual("River Road closure", preview["transportation_restrictions"][0]["name"])
        self.assertEqual(1, len(await self.backend.records("requests")))
        row = await self.call("resource_request", self.request(), "responder")
        self.assertEqual(("Pending", 77, "responder"), (row["status"], row["priority_score"], row["requested_by"]))
        self.assertTrue(await self.alerts_for("Resource Coordinator"))

    @case("UC-8", "Reject invalid quantities and gibberish values", "Quantity -5, abc, 999999; type 'Unicorns'; urgency 'yesterday'; justification 'pls'",
          "All rejected; no request created", "H3.4A invalid request")
    async def test_uc08_invalid(self):
        for change in [{"quantity": -5}, {"quantity": "abc"}, {"quantity": 999999}, {"resource_type": "Unicorns"},
                       {"urgency": "yesterday"}, {"justification": "pls"}, {"delivery_location": ""}]:
            await self.reject("resource_request", self.request(**change), "responder")
        self.assertEqual(1, len(await self.backend.records("requests")))

    @case("UC-8", "Block requests for resolved or unassigned incidents", "IN-002 (Resolved); IN-003 (not assigned); dispatcher role",
          "CONFLICT; FORBIDDEN; FORBIDDEN", "H3.8B incident closed; assignment check")
    async def test_uc08_blocked(self):
        await self.reject("resource_request", self.request(id="IN-002"), "responder", "CONFLICT")
        await self.reject("resource_request", self.request(id="IN-003"), "responder", "FORBIDDEN")
        await self.reject("resource_request", self.request(), "dispatcher", "FORBIDDEN")

    # ---------------- UC-9 ----------------
    @case("UC-9", "Allocate the full request in one batch", "RQ-001 (20 Water) from WATER-001: options, preview, confirm",
          "Only Water resources offered; request Approved; WATER-001 70 available / 30 allocated; allocation saved", "I2 options; I4 one batch")
    async def test_uc09_allocate(self):
        options = await self.call("request_options", {"id": "RQ-001"}, "resource")
        self.assertEqual(["WATER-001", "WATER-002"], [r["id"] for r in options["resources"]])
        preview = await self.call("allocate", {"id": "RQ-001", "resource_id": "WATER-001", "quantity": 20, "preview": True}, "resource")
        row = await self.call("allocate", {"id": "RQ-001", "resource_id": "WATER-001", "quantity": 20,
                                           "resource_version": preview["resource_version"], "request_version": preview["request_version"]}, "resource")
        self.assertEqual(("Approved", 20), (row["status"], row["fulfilled"]))
        water = await self.record("resources", "WATER-001")
        self.assertEqual((70, 30), (water["available"], water["allocated"]))

    @case("UC-9", "Partially fulfil and reject over-allocation", "RQ-001: 10 from WATER-002; then 50 from WATER-002; then 11 more",
          "Partially Fulfilled with 10 remaining; over-stock and over-need rejected", "I3 rules; I3.8B insufficient")
    async def test_uc09_partial(self):
        row = await self.call("allocate", {"id": "RQ-001", "resource_id": "WATER-002", "quantity": 10, "resource_version": 1, "request_version": 1}, "resource")
        self.assertEqual("Partially Fulfilled", row["status"])
        await self.reject("allocate", {"id": "RQ-001", "resource_id": "WATER-002", "quantity": 50, "resource_version": 2, "request_version": 2}, "resource", "CONFLICT")
        await self.reject("allocate", {"id": "RQ-001", "resource_id": "WATER-002", "quantity": 11, "resource_version": 2, "request_version": 2}, "resource")

    @case("UC-9", "Reject wrong category, gibberish and concurrent changes", "VEH-001 for Water; quantity 'lots'; stale resource version",
          "All rejected; no allocation saved", "I3.8D mismatch; I3.11C concurrent allocation")
    async def test_uc09_invalid(self):
        await self.reject("allocate", {"id": "RQ-001", "resource_id": "VEH-001", "quantity": 1, "resource_version": 1, "request_version": 1}, "resource")
        await self.reject("allocate", {"id": "RQ-001", "resource_id": "WATER-001", "quantity": "lots", "resource_version": 1, "request_version": 1}, "resource")
        await self.reject("allocate", {"id": "RQ-001", "resource_id": "WATER-001", "quantity": 5, "resource_version": 7, "request_version": 1}, "resource", "CONFLICT")
        self.assertEqual(2, len(await self.backend.records("allocations")))
        await self.reject("requests", {}, "dispatcher", "FORBIDDEN")

    @case("UC-9", "Put a request in Waiting or deny it with a reason", "Deny without reason; Waiting with reason; Deny with reason",
          "Missing reason rejected; Waiting; Denied", "I2A waiting; I3A deny")
    async def test_uc09_decisions(self):
        await self.reject("request_decision", {"id": "RQ-001", "version": 1, "action": "deny", "reason": ""}, "resource")
        row = await self.call("request_decision", {"id": "RQ-001", "version": 1, "action": "wait", "reason": "Truck arrives at 6pm today."}, "resource")
        self.assertEqual("Waiting", row["status"])
        row = await self.call("request_decision", {"id": "RQ-001", "version": 2, "action": "deny", "reason": "Duplicate of an earlier request."}, "resource")
        self.assertEqual("Denied", row["status"])
        await self.reject("request_decision", {"id": "RQ-001", "version": 3, "action": "approve-all", "reason": "Trying a fake action."}, "resource")

    @case("UC-9 + UC-18", "New allocations are released by incident closure", "Allocate 20 Water to IN-001, resolve, close",
          "WATER-001 allocated 0 and consumed 30 after closure", "Shared closure fields and statuses")
    async def test_uc09_closure(self):
        await self.call("allocate", {"id": "RQ-001", "resource_id": "WATER-001", "quantity": 20, "resource_version": 1, "request_version": 1}, "resource")
        await self.call("resolve", {"id": "IN-001", "actions": "Evacuated residents and secured the building.", "outcome": "All residents are safe now.",
                                    "remaining": "None", "completion": (self.clock-timedelta(minutes=1)).isoformat(), "version": 1}, "responder")
        await self.call("close", {"id": "IN-001", "version": 2, "notes": "Closed after the water went down."}, "dispatcher")
        water = await self.record("resources", "WATER-001")
        self.assertEqual((0, 30, 70), (water["allocated"], water["consumed"], water["available"]))

    # ---------------- UC-10 ----------------
    @case("UC-10", "Add and edit inventory with change records", "Add WATER-003 (40); edit GEN-001 total 4 -> 6",
          "Both saved; available recalculated; two ResourceChangeRecord history rows", "J3 valid; J4 save and history")
    async def test_uc10_add_edit(self):
        added = await self.call("resource_save", self.resource(id="water-003", new=True, name="Water cases (south depot)", kind="Consumable", category="Water", total=40), "resource")
        self.assertEqual(("WATER-003", 40), (added["id"], added["available"]))
        edited = await self.call("resource_save", self.resource(total=6), "resource")
        self.assertEqual((6, 2), (edited["available"], edited["version"]))
        self.assertEqual(2, len(await self.backend.rows("SELECT * FROM history WHERE kind='ResourceChangeRecord'")))

    @case("UC-10", "Reject duplicates, gibberish and unsafe edits", "Duplicate WATER-001; ID 'water; DROP'; total -10; WATER-001 total 5; Out of Service with allocations; no change",
          "All rejected", "J3.12A duplicate/invalid; J3.12B allocation rules")
    async def test_uc10_invalid(self):
        await self.reject("resource_save", self.resource(id="WATER-001", new=True), "resource", "CONFLICT")
        await self.reject("resource_save", self.resource(id="water; DROP TABLE records", new=True), "resource")
        await self.reject("resource_save", self.resource(total=-10), "resource")
        await self.reject("resource_save", self.resource(kind="Spaceship"), "resource")
        water = {"id": "WATER-001", "version": 1, "name": "Water cases", "kind": "Consumable", "category": "Water", "area": "Central"}
        await self.reject("resource_save", water | {"total": 5}, "resource")
        await self.reject("resource_save", water | {"total": 100, "status": "Out of Service"}, "resource")
        await self.reject("resource_save", self.resource(), "resource")
        await self.reject("resource_save", self.resource(total=9, version=5), "resource", "CONFLICT")
        await self.reject("inventory", {}, "shelter", "FORBIDDEN")

    # ---------------- UC-11 ----------------
    @case("UC-11", "Update capacity and alert at the threshold", "SH-001 capacity 100 -> 94 (85 occupants)",
          "Preview warns; saved Near Capacity with ShelterHistoryRecord and administrator alert", "K3 valid; K4.11 threshold")
    async def test_uc11_threshold(self):
        preview = await self.call("shelter_update", self.shelter(capacity=94, preview=True), "shelter")
        self.assertIn("Near Capacity", preview["warning"])
        row = await self.call("shelter_update", self.shelter(capacity=94), "shelter")
        self.assertEqual(("Near Capacity", 94), (row["status"], row["capacity"]))
        self.assertTrue(await self.alerts_for("Emergency Administrator"))
        self.assertEqual(1, len(await self.backend.rows("SELECT * FROM history WHERE kind='ShelterHistoryRecord'")))

    @case("UC-11", "Reject invalid shelter data", "Capacity 50 (< 85); 'lots'; phone 'call the desk'; state 'Party'; supply -4; extra supply; medical 'yes'",
          "All rejected; SH-001 unchanged", "K3.4A/K3.4B invalid")
    async def test_uc11_invalid(self):
        for change in [{"capacity": 50}, {"capacity": "lots"}, {"public_contact": "call the desk"}, {"state": "Party"},
                       {"supplies": {"food": -4, "water": 400}}, {"supplies": {"food": 1, "water": 1, "gold": 5}}, {"medical_support": "yes"}]:
            await self.reject("shelter_update", self.shelter(**change), "shelter")
        await self.reject("shelter_update", self.shelter(), "shelter")
        self.assertEqual(1, (await self.record("shelters", "SH-001"))["version"])

    @case("UC-11", "Check assignment, reopen a shelter and detect stale edits", "SH-002 (other manager); reopen SH-003; stale SH-003 write",
          "SH-002 FORBIDDEN; SH-003 Open and visible in public search; stale CONFLICT", "K1 assignment; version check")
    async def test_uc11_scope(self):
        await self.reject("shelter_update", self.shelter(id="SH-002"), "shelter", "FORBIDDEN")
        data = self.shelter(id="SH-003", capacity=120, state="Open")
        row = await self.call("shelter_update", data, "shelter")
        self.assertEqual("Open", row["status"])
        self.assertIn("SH-003", [r["id"] for r in await self.call("shelters", {}, None)])
        await self.reject("shelter_update", data | {"capacity": 130}, "shelter", "CONFLICT")

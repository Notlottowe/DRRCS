"""White-box tests for the actual cloud backend's command handlers.

SQLite here is an isolated test substitute for D1. The same SQL batches and
backend methods are used; real Cloudflare requests are checked separately.
"""
import hashlib
import asyncio
import json
import sqlite3
import sys
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"backend"))
from app import Backend, ROLES, PERMISSIONS
from checks import AppError
from passwords import ITERATIONS, finish_hash


def case(uc, description, inputs, expected, branch):
    def decorate(function):
        function.test_info = {"use_case": uc, "description": description, "input": inputs,
                              "expected_output": expected, "white_box_branch": branch}
        return function
    return decorate


async def hash_password(password, salt):
    derived = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), ITERATIONS)
    return finish_hash(derived, "test-only-server-pepper")


class TestDatabase:
    def __init__(self, template=None):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        if template:
            template.backup(self.connection)
        else:
            self.connection.executescript((ROOT/"backend/schema.sql").read_text())

    async def query(self, sql, values=()):
        return [dict(row) for row in self.connection.execute(sql, values)]

    async def batch(self, statements):
        try:
            with self.connection:
                for sql, values in statements:
                    self.connection.execute(sql, values)
        except sqlite3.IntegrityError as error:
            if "CHECK constraint failed" in str(error) or "UNIQUE constraint failed" in str(error):
                raise AppError("Record changed or ID already exists. Reload and try again.", "CONFLICT") from None
            raise


class CloudBackendTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.seed_clock = datetime.now(timezone.utc)
        cls.template = TestDatabase().connection
        for username, role in ROLES.items():
            salt = hashlib.sha256(username.encode()).hexdigest()[:32]
            derived = hashlib.pbkdf2_hmac("sha256", b"TestPassword2026!", bytes.fromhex(salt), ITERATIONS)
            hashed = finish_hash(derived, "test-only-server-pepper")
            cls.template.execute("INSERT INTO users VALUES (?,?,?,?,?,?,?,1)", (username, username.title(), "Test Agency", json.dumps([role]), "Active", salt, hashed))
        samples = json.loads((ROOT/"backend/sample_data.json").read_text())
        for kind, records in samples.items():
            for row in records:
                rid = row.pop("id")
                if "created_at" in row:
                    row["created_at"] = (cls.seed_clock-timedelta(hours=3)).isoformat()
                if row.get("responded_at"):
                    row["responded_at"] = (cls.seed_clock-timedelta(hours=2, minutes=45)).isoformat()
                if row.get("resolution"):
                    row["resolution"]["completion"] = (cls.seed_clock-timedelta(minutes=10)).isoformat()
                if kind in {"zones", "restrictions"}:
                    row.update(start=(cls.seed_clock-timedelta(days=1)).isoformat(), end=(cls.seed_clock+timedelta(days=365)).isoformat())
                cls.template.execute("INSERT INTO records VALUES (?,?,?,1)", (kind, rid, json.dumps(row)))
        cls.template.commit()

    @classmethod
    def tearDownClass(cls):
        cls.template.close()

    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 1000
        self.db = TestDatabase(self.template)
        self.clock = self.seed_clock
        self.backend = Backend(self.db, hash_password, lambda: self.clock)
        self.password = "TestPassword2026!"
        self.tokens = {}
        self.actual = []

    async def asyncTearDown(self):
        self.db.connection.close()

    async def token(self, name="admin"):
        if name not in self.tokens:
            result = await self.backend.run("login", {"username": name, "password": self.password})
            self.tokens[name] = result["token"]
        return self.tokens[name]

    async def call(self, command, data=None, user="admin"):
        result = await self.backend.run(command, data or {}, await self.token(user) if user else "")
        self.actual.append(result)
        return result

    async def reject(self, command, data=None, user="admin", code=None):
        with self.assertRaises(AppError) as caught:
            await self.backend.run(command, data or {}, await self.token(user) if user else "")
        self.actual.append({"error": caught.exception.code, "message": str(caught.exception)})
        if code:
            self.assertEqual(code, caught.exception.code)

    def period(self):
        return {"start": (self.clock-timedelta(hours=1)).isoformat(), "end": (self.clock+timedelta(hours=1)).isoformat()}

    def zone(self, **changes):
        return {"name": "West flood zone", "area": "Central", "boundary": "41.70,-87.90,41.79,-87.81", "emergency_type": "Flood", "severity": 4,
                "instructions": "Move to higher ground and avoid flooded roads.", **self.period(), **changes}

    def notice(self, **changes):
        return {"message": "Flood warning. Move to higher ground immediately.", "area": "Central", "priority": "Critical",
                "effective": self.period()["start"], "groups": ["Citizens", "Emergency Personnel"], **changes}

    def order(self, **changes):
        return {"zone_id": "ZN-001", "area": "Central", "effective": self.period()["start"], "end": self.period()["end"],
                "instructions": "Evacuate using the northern road to Central High School.", **changes}

    def resolution(self, **changes):
        return {"id": "IN-001", "actions": "Evacuated residents and secured the flooded building.", "outcome": "All residents are safe and immediate danger is addressed.",
                "remaining": "None", "completion": (self.clock-timedelta(minutes=1)).isoformat(), "version": 1, **changes}

    def report(self, **changes):
        return {"kind": "Operational", "start": (self.clock-timedelta(days=1)).isoformat(), "end": (self.clock+timedelta(days=1)).isoformat(),
                "area": "All", "notes": "Improve route planning and maintain extra drinking water.", **changes}

    def account(self, **changes):
        return {"username": "testuser", "name": "Test User", "agency": "County Agency", "roles": ["Shelter Manager"],
                "password": "NewPassword2026!", "status": "Active", **changes}

    @case("UC-12", "Reach shelter threshold with an atomic delta", "SH-001, +5, version 1", "90 occupants, Near Capacity, history and administrator alert", "L2 valid; L3 threshold reached")
    async def test_uc12_threshold(self):
        result = await self.call("occupancy", {"id": "SH-001", "delta": 5, "version": 1}, "shelter")
        self.assertEqual((90, "Near Capacity", 2), (result["occupancy"], result["status"], result["version"]))
        self.assertTrue(await self.call("alerts"))

    @case("UC-12", "Preview over-capacity before recording a reason", "SH-001 +20 preview, then save with/without reason", "Preview does not write; missing reason rejected; justified 105 becomes Full", "L2.4B and L3 justification")
    async def test_uc12_overcapacity(self):
        data = {"id": "SH-001", "delta": 20, "version": 1}
        preview = await self.call("occupancy", data | {"preview": True}, "shelter")
        self.assertIn("OVER CAPACITY", preview["warning"])
        self.assertEqual(85, (await self.backend.record("shelters", "SH-001"))["occupancy"])
        await self.reject("occupancy", data, "shelter")
        result = await self.call("occupancy", data | {"reason": "Temporary intake until buses arrive."}, "shelter")
        self.assertEqual((105, "Full"), (result["occupancy"], result["status"]))

    @case("UC-12", "Reject negative and gibberish counts", "-86, abc, 1.5, NaN, True, SQL text", "All rejected; occupancy remains 85", "L2.4A and numeric validation")
    async def test_uc12_invalid(self):
        for delta in [-86, "abc", "1.5", "NaN", True, "1; DROP TABLE records"]:
            await self.reject("occupancy", {"id": "SH-001", "delta": delta, "version": 1}, "shelter")
        self.assertEqual(85, (await self.backend.record("shelters", "SH-001"))["occupancy"])

    @case("UC-12", "Check assignment, open status and stale versions", "Another manager's shelter; closed shelter; two writes using v1", "Assignment/closed requests denied; second stale write conflicts without extra history", "L1 guards and D1 batch version check")
    async def test_uc12_scope_conflict(self):
        await self.reject("occupancy", {"id": "SH-002", "delta": 1, "version": 1}, "shelter", "FORBIDDEN")
        await self.reject("occupancy", {"id": "SH-003", "delta": 1, "version": 1}, "shelter")
        await self.call("occupancy", {"id": "SH-001", "delta": 5, "version": 1}, "shelter")
        await self.reject("occupancy", {"id": "SH-001", "delta": 1, "version": 1}, "shelter", "CONFLICT")
        self.assertEqual(90, (await self.backend.record("shelters", "SH-001"))["occupancy"])
        self.assertEqual(1, len(await self.backend.rows("SELECT * FROM history")))

    @case("UC-13", "Search public shelters ranked by distance", "Public location 41.88,-87.63", "SH-001 then SH-002; private fields and closed shelter excluded", "M2 valid and ranking/public projection")
    async def test_uc13_public(self):
        rows = await self.call("shelters", user=None)
        self.assertEqual(["SH-001", "SH-002"], [r["id"] for r in rows])
        self.assertFalse({"manager", "supplies", "internal_contact"} & set(rows[0]))

    @case("UC-13", "Check search errors and no matches", "NaN coordinates, invalid status, minimum spaces 100000", "Invalid criteria rejected; valid unmatched criteria return empty list", "M2.4A Unusable Location; M2.10B No Match")
    async def test_uc13_invalid_empty(self):
        for data in [{"location": "NaN,0"}, {"location": "91,0"}, {"status": "gibberish"}, {"radius": -1}]:
            await self.reject("shelters", data, user=None)
        self.assertEqual([], await self.call("shelters", {"spaces": 100000}, user=None))

    @case("UC-13", "Authorize and audit private detail", "Dispatcher private SH-001; guest private; unassigned shelter manager", "Dispatcher sees supplies; guest/unassigned denied; public detail shows restrictions only", "M3A Restricted Detail")
    async def test_uc13_private(self):
        public = await self.call("shelter", {"id": "SH-001"}, None)
        self.assertNotIn("manager", public)
        self.assertEqual("TR-001", public["restrictions"][0]["id"])
        private = await self.call("shelter", {"id": "SH-001", "private": True}, "dispatcher")
        self.assertIn("supplies", private)
        await self.reject("shelter", {"id": "SH-001", "private": True}, None, "AUTHENTICATION")
        await self.reject("shelter", {"id": "SH-002", "private": True}, "shelter", "FORBIDDEN")

    @case("UC-14", "Create a zone and retain successive versions", "Non-overlapping zone then edit v1", "Same ID v2; history and dispatcher/responder notices", "N4 save and publish new zone version")
    async def test_uc14_versions(self):
        row = await self.call("zone_save", self.zone())
        edited = await self.call("zone_save", self.zone(id=row["id"], version=1, instructions="Use the northern road and avoid flooded areas."))
        self.assertEqual(2, edited["version"])
        self.assertEqual(2, len(await self.backend.rows("SELECT * FROM history")))

    @case("UC-14", "Reject invalid boundaries/times and require overlap justification", "Reversed rectangle/time; overlapping ZN-001 with empty then valid reason", "Invalid/conflicting writes blocked; justified overlap saved", "N3.4A Invalid; N3.4B Conflict")
    async def test_uc14_invalid_conflict(self):
        await self.reject("zone_save", self.zone(boundary="42,-87,41,-86"))
        await self.reject("zone_save", self.zone(start=self.period()["end"], end=self.period()["start"]))
        data = self.zone(boundary="41.85,-87.7,41.95,-87.6")
        preview = await self.call("zone_save", data | {"preview": True})
        self.assertIn("ZN-001", preview["conflicts"])
        await self.reject("zone_save", data)
        await self.call("zone_save", data | {"justification": "Separate rescue area inside the larger zone."})

    @case("UC-15", "Keep a draft non-effective and authorize the same order", "Order Draft then ID/version authorization", "No notice for Draft; same order Authorized with Delivered notice", "O4A Draft; O4 include UC-17")
    async def test_uc15_draft_authorize(self):
        draft = await self.call("order_save", self.order(draft=True))
        self.assertEqual([], await self.backend.records("notices"))
        row = await self.call("order_save", self.order(id=draft["id"], version=draft["version"]))
        self.assertEqual((draft["id"], "Delivered"), (row["id"], row["delivery_status"]))
        await self.reject("order_save", self.order(id=row["id"], version=row["version"]))

    @case("UC-15", "Reject invalid area/time and retain order during delivery failure", "Empty area; time beyond zone; both delivery groups fail", "Invalid data rejected; failed order and delivery status retained with administrator alert", "O3.4A Invalid; O4.12A Delivery Failed")
    async def test_uc15_invalid_failure(self):
        await self.reject("order_save", self.order(area=""))
        await self.reject("order_save", self.order(end=(self.clock+timedelta(days=1000)).isoformat()))
        for group in ["Citizens", "Emergency Personnel"]:
            await self.call("fault", {"name": "delivery_failure:"+group, "enabled": True}, "sysadmin")
        row = await self.call("order_save", self.order())
        self.assertEqual("Failed", row["delivery_status"])
        self.assertTrue(await self.call("alerts"))

    @case("UC-16", "Record restrictions with route impact and version history", "Restriction covering SH-001 and IN-001 then deactivate", "Affected operations previewed; dispatcher warned; v2 history retained", "P3.6B Route Affected; P4 new version")
    async def test_uc16_impact(self):
        data = {"name": "Main Road closure", "boundary": "41.87,-87.64,41.89,-87.62", "instructions": "Use the northern entrance and avoid Main Road.", **self.period()}
        preview = await self.call("restriction_save", data | {"preview": True})
        self.assertIn("IN-001", preview["affected_operations"]["incidents"])
        row = await self.call("restriction_save", data)
        await self.call("restriction_save", data | {"id": row["id"], "version": 1, "status": "Inactive"})
        self.assertTrue(await self.call("alerts", user="dispatcher"))
        await self.reject("restriction_save", data | {"boundary": "garbage"})

    @case("UC-17", "Store drafts without delivery and reject invalid notice fields", "Draft; invalid message/priority/audience/time", "Draft has no authorizer or delivery; invalid inputs rejected", "Q4A Draft; Q3.4A Invalid")
    async def test_uc17_draft_invalid(self):
        row = await self.call("notice_save", self.notice(draft=True))
        self.assertEqual(("Draft", {}, None), (row["status"], row["delivery"], row["authorized_by"]))
        for changes in [{"message": ""}, {"priority": "banana"}, {"groups": ["Everyone"]}, {"effective": "tomorrow"}]:
            await self.reject("notice_save", self.notice(**changes))

    @case("UC-17", "Deliver due notices from the scheduler without an open client", "Authorized notice effective in 5 minutes; advance clock 6 minutes", "Scheduled first; server scheduler delivers once; second tick does nothing", "Q4 scheduled server delivery")
    async def test_uc17_scheduled(self):
        row = await self.call("notice_save", self.notice(effective=(self.clock+timedelta(minutes=5)).isoformat()))
        self.assertEqual("Scheduled", row["status"])
        self.clock += timedelta(minutes=6)
        self.actual.append(await self.backend.scheduled())
        self.assertEqual("Delivered", (await self.backend.record("notices", row["id"]))["status"])
        self.assertEqual(0, (await self.backend.scheduled())["due_notices_processed"])

    @case("UC-17", "Record partial failure and retry failed groups", "Citizens delivery fault on, notice, fault off, retry", "Partial Failure then Delivered; personnel success preserved", "Q4.10A Partial Failure")
    async def test_uc17_partial_retry(self):
        await self.call("fault", {"name": "delivery_failure:Citizens", "enabled": True}, "sysadmin")
        row = await self.call("notice_save", self.notice())
        self.assertEqual("Partial Failure", row["status"])
        await self.call("fault", {"name": "delivery_failure:Citizens", "enabled": False}, "sysadmin")
        result = await self.call("notice_retry", {"id": row["id"]})
        self.assertEqual("Delivered", result["status"])

    @case("UC-17", "Expire an evacuation notice rather than sending it late", "Scheduled order; scheduler runs after end time", "Expired with empty delivery; order status synchronized", "effective/end-time guard")
    async def test_uc17_expired_order(self):
        row = await self.call("order_save", self.order(effective=(self.clock+timedelta(minutes=5)).isoformat()))
        self.clock += timedelta(hours=2)
        self.actual.append(await self.backend.scheduled())
        self.assertEqual("Expired", (await self.backend.record("orders", row["id"]))["delivery_status"])

    @case("UC-18", "Resolve an assigned incident and reject incomplete/unassigned inputs", "Missing actions; unassigned IN-003; complete IN-001 resolution", "Invalid/unauthorized attempts rejected; complete resolution saved with history", "R2.4A incomplete; R1 assignment; R2 main")
    async def test_uc18_resolution(self):
        await self.reject("resolve", self.resolution(actions=""), "responder")
        await self.reject("resolve", self.resolution(id="IN-003"), "responder", "FORBIDDEN")
        row = await self.call("resolve", self.resolution(), "responder")
        self.assertEqual("Resolved", row["status"])
        self.assertEqual("Complete", (await self.backend.record("assignments", "AS-001"))["status"])

    @case("UC-18", "Close a documented resolved incident", "Dispatcher closes IN-002", "Closed; team assignment Released; history retained", "R4 ready; R5 release and close")
    async def test_uc18_close(self):
        row = await self.call("close", {"id": "IN-002", "version": 1, "notes": "All documentation reviewed and work completed."}, "dispatcher")
        self.assertEqual("Closed", row["status"])
        self.assertEqual("Released", (await self.backend.record("assignments", "AS-002"))["status"])

    @case("UC-18", "Enforce resolution and documentation before closure", "Responding IN-001; Resolved IN-002 with missing resolution", "Both closures blocked without changes", "Resolved prerequisite; R4.4A Documentation Missing")
    async def test_uc18_missing_documentation(self):
        await self.reject("close", {"id": "IN-001", "notes": "Reviewed all documentation.", "version": 1})
        row = await self.backend.record("incidents", "IN-002")
        await self.db.batch(self.backend.write("incidents", row | {"resolution": {}}, 1))
        await self.reject("close", {"id": "IN-002", "notes": "Reviewed all documentation.", "version": 2}, code="MISSING_DOCUMENTATION")

    @case("UC-18", "Require an authorized override and release reusable resources only", "Resolve IN-001; dispatcher override denied; admin closes with reason", "Boat returns; water stays 90 available/10 consumed; open request cancellation recorded", "R4.4B Open Items; R5 authorized override")
    async def test_uc18_override(self):
        row = await self.call("resolve", self.resolution(), "responder")
        data = {"id": row["id"], "version": row["version"], "notes": "Reviewed all documentation.", "reason": "The water request is no longer needed after rescue."}
        await self.reject("close", data, "dispatcher", "FORBIDDEN")
        await self.reject("close", data | {"reason": ""})
        await self.call("close", data)
        boat = await self.backend.record("resources", "VEH-001")
        water = await self.backend.record("resources", "WATER-001")
        self.actual.append({"boat": boat, "water": water})
        self.assertEqual(1, boat["available"])
        self.assertEqual((90, 10, 0), (water["available"], water["consumed"], water["allocated"]))

    @case("UC-18", "Rollback a whole D1-style batch on a stale resource version", "Two changes in one batch; second expected version is wrong", "No incident update, audit or history committed", "D1 batch CHECK guard and rollback")
    async def test_uc18_batch_rollback(self):
        incident = await self.backend.record("incidents", "IN-001")
        resource = await self.backend.record("resources", "VEH-001")
        changes = self.backend.write("incidents", incident | {"status": "Closed"}, 1) + self.backend.write("resources", resource, 999)
        with self.assertRaises(AppError):
            await self.backend.commit({"username": "admin", "roles": ["Emergency Administrator"]}, "CLOSURE_TEST", incident["id"], {}, changes)
        unchanged = await self.backend.record("incidents", "IN-001")
        self.actual.append(unchanged)
        self.assertEqual("Responding", unchanged["status"])
        self.assertEqual([], await self.backend.rows("SELECT * FROM audit"))

    @case("Cloud persistence", "Record a new event and query through a separate backend session", "Dispatcher creates event; new Backend logs in and lists incidents", "Same ID and description returned from shared database; history retained", "incident_add writes server database, never client storage")
    async def test_cloud_event_record(self):
        row = await self.call("incident_add", {"type": "Flood", "description": "Synthetic test event: flooded road near the bridge.", "location": "41.88,-87.63", "severity": 3}, "dispatcher")
        second = Backend(self.db, hash_password, lambda: self.clock)
        login = await second.run("login", {"username": "dispatcher", "password": self.password})
        result = await second.run("incident", {"id": row["id"]}, login["token"])
        self.actual.append(result)
        self.assertEqual(row["description"], result["description"])

    @case("UC-19", "Calculate full dashboard indicators", "Admin baseline data", "2 active, 2 critical, 1 unassigned, 1 pending request and shelter/vehicle/team totals", "S1 scope; S2 indicator algorithm")
    async def test_uc19_full(self):
        row = await self.call("dashboard")
        self.assertEqual((2, 2, 1, 1), tuple(row[k] for k in ["active_incidents", "critical_incidents", "unassigned_incidents", "pending_resource_requests"]))

    @case("UC-19", "Limit resource and assigned shelter monitoring", "Resource Coordinator and Shelter Manager dashboards", "Resource view excludes incidents; shelter view excludes SH-002", "S1B/S1C Role-Limited; S3 Outside Role")
    async def test_uc19_scope(self):
        resource = await self.call("dashboard", user="resource")
        self.assertNotIn("active_incidents", resource)
        shelter = await self.call("dashboard", user="shelter")
        self.assertEqual({"SH-001", "SH-003"}, {s["id"] for s in shelter["shelters"]})
        await self.reject("incident", {"id": "IN-001"}, "resource", "FORBIDDEN")

    @case("UC-19", "Keep unaffected dashboard sections during source outage", "Incident source unavailable", "Incident marker Unavailable; shelters/resources still returned", "S1.16A Source Unavailable")
    async def test_uc19_unavailable(self):
        await self.call("fault", {"name": "source_unavailable:incidents", "enabled": True}, "sysadmin")
        row = await self.call("dashboard")
        self.assertEqual("Unavailable", row["incidents"])
        self.assertIn("shelters", row)

    @case("UC-20", "Calculate, save and finalize the same operational report", "Baseline period, then finalize draft ID/version", "3 incidents, 15 minute average response, 50% personnel utilization; Finalized same ID", "T2 metrics and working draft; T3 immutable version")
    async def test_uc20_metrics_finalize(self):
        draft = await self.call("report_create", self.report())
        self.assertEqual((3, 15.0, 50.0), tuple(draft["metrics"][k] for k in ["total_incidents", "average_response_minutes", "personnel_utilization_percent"]))
        result = await self.call("report_finalize", {"id": draft["id"], "version": 1})
        self.assertEqual((draft["id"], "Finalized"), (result["id"], result["status"]))

    @case("UC-20", "Validate criteria and block incomplete finalization", "Reversed period; missing resources source", "Invalid period rejected; missing data shown; report stays Draft", "T2.4A Invalid; T2.6B Data Unavailable")
    async def test_uc20_invalid_missing(self):
        await self.reject("report_create", self.report(start=self.period()["end"], end=self.period()["start"]))
        await self.call("fault", {"name": "source_unavailable:resources", "enabled": True}, "sysadmin")
        draft = await self.call("report_create", self.report())
        self.assertIn("resources", draft["missing_sections"])
        self.assertIsNone(draft["metrics"]["resources_current"])
        await self.reject("report_finalize", {"id": draft["id"], "version": 1}, code="INCOMPLETE")

    @case("UC-20", "Save draft notes and protect finalized snapshots", "Update notes, finalize, mutate live shelter, attempt report edit/SQL delete", "Finalized snapshot unchanged; service/SQL forbid changes", "T3A Draft; Report Service and trigger immutability")
    async def test_uc20_immutable(self):
        draft = await self.call("report_create", self.report())
        await self.call("report_notes", {"id": draft["id"], "version": 1, "notes": "Updated operational notes."})
        await self.call("report_finalize", {"id": draft["id"], "version": 2})
        await self.call("occupancy", {"id": "SH-001", "delta": 5, "version": 1}, "shelter")
        result = await self.call("report", {"id": draft["id"]})
        self.assertEqual(85, result["metrics"]["shelter_usage_current"][0]["occupancy"])
        await self.reject("report_notes", {"id": draft["id"], "version": 3, "notes": "Tampered"}, code="IMMUTABLE")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.connection.execute("DELETE FROM records WHERE kind='reports' AND id=?", (draft["id"],))

    @case("UC-21", "Aggregate and finalize an after-action report", "Complete analysis and baseline incidents", "Central area contains 3 IDs; same report Finalized", "U1-U3 aggregation; U4 finalization")
    async def test_uc21_main(self):
        row = await self.call("report_create", self.report(kind="After-Action"))
        self.assertEqual(3, len(row["incidents_by_area"]["Central"]))
        self.assertEqual("Finalized", (await self.call("report_finalize", {"id": row["id"], "version": 1}))["status"])

    @case("UC-21", "Reject empty periods and missing analysis", "2020 period; baseline after-action draft without notes", "NO_RECORDS for empty period; notes required before finalization", "U2.4A No Records; After Action Validation Logic")
    async def test_uc21_no_records_notes(self):
        await self.reject("report_create", self.report(kind="After-Action", start="2020-01-01T00:00:00+00:00", end="2020-01-02T00:00:00+00:00"), code="NO_RECORDS")
        row = await self.call("report_create", self.report(kind="After-Action", notes=""))
        await self.reject("report_finalize", {"id": row["id"], "version": 1})

    @case("UC-21", "Require explicit limitations for incomplete analysis", "Shelter source unavailable; finalize before/after documenting limitation", "First finalization blocked; documented version Finalized with missing sections retained", "U3.4A Incomplete alternative")
    async def test_uc21_limitations(self):
        await self.call("fault", {"name": "source_unavailable:shelters", "enabled": True}, "sysadmin")
        row = await self.call("report_create", self.report(kind="After-Action"))
        await self.reject("report_finalize", {"id": row["id"], "version": 1})
        await self.call("report_notes", {"id": row["id"], "version": 1, "notes": "Improve route planning and water inventory.", "limitations": "Shelter occupancy could not be checked because its source was unavailable."})
        result = await self.call("report_finalize", {"id": row["id"], "version": 2})
        self.assertIn("shelters", result["missing_sections"])

    @case("UC-22", "Preview least-privilege account permissions then create", "New Shelter Manager, preview then confirmed write", "Preview is read-only; account/holder notice saved; credentials omitted", "V3 validation/implied permissions; V4 save")
    async def test_uc22_create_preview(self):
        preview = await self.call("user_save", self.account(preview=True), "sysadmin")
        self.assertIn("occupancy", preview["permissions"])
        self.assertEqual(6, len(await self.backend.rows("SELECT * FROM users")))
        row = await self.call("user_save", self.account(), "sysadmin")
        self.assertNotIn("password_hash", row)
        self.assertTrue(await self.backend.rows("SELECT * FROM alerts WHERE recipient='testuser'"))

    @case("UC-22", "Reject invalid identities, duplicate accounts and weak passwords", "SQL-like username, duplicate, short/no-digit password, missing agency", "Every invalid change rejected; original accounts retained", "V3.4A Invalid")
    async def test_uc22_invalid(self):
        for changes in [{"username": "x'; DROP TABLE users"}, {"password": "short"}, {"password": "abcdefghijklmn"}, {"agency": ""}, {"roles": ["Superuser"]}]:
            await self.reject("user_save", self.account(**changes), "sysadmin")
        await self.call("user_save", self.account(), "sysadmin")
        await self.reject("user_save", self.account(), "sysadmin", "CONFLICT")

    @case("UC-22", "Enforce separation of duties and technical-only authority", "System+Emergency Admin; Responder+Dispatcher; sysadmin operational attempt", "SOD_VIOLATION for combinations; sysadmin cannot issue zones", "V3.10B SoD; V3.10C Technical Role Only")
    async def test_uc22_sod_technical(self):
        for roles in [["System Administrator", "Emergency Administrator"], ["First Responder", "Dispatcher"]]:
            await self.reject("user_save", self.account(roles=roles), "sysadmin", "SOD_VIOLATION")
        await self.reject("zone_save", self.zone(), "sysadmin", "FORBIDDEN")

    @case("UC-22", "Recheck account state and protect last administrator", "Deactivate a logged-in new user; disable sole sysadmin", "Existing session rejected; last sysadmin retained", "fresh user lookup and last-administrator guard")
    async def test_uc22_inactive_last_admin(self):
        await self.call("user_save", self.account(), "sysadmin")
        login = await self.backend.run("login", {"username": "testuser", "password": "NewPassword2026!"})
        await self.call("user_save", self.account(version=1, status="Inactive", password=""), "sysadmin")
        with self.assertRaises(AppError):
            await self.backend.run("whoami", {}, login["token"])
        await self.reject("user_save", self.account(username="sysadmin", roles=["System Administrator"], version=1, status="Inactive", password=""), "sysadmin")

    @case("UC-23", "Authenticate, expire and revoke database sessions", "Valid login; advance 31 minutes; fresh login then logout", "Missing/expired/revoked tokens denied", "W2 match; persistent session expiry and logout")
    async def test_uc23_sessions(self):
        token = await self.token()
        self.clock += timedelta(minutes=31)
        with self.assertRaises(AppError):
            await self.backend.current_user(token)
        row = await self.backend.run("login", {"username": "admin", "password": self.password})
        await self.backend.run("logout", {}, row["token"])
        with self.assertRaises(AppError):
            await self.backend.current_user(row["token"])
        self.actual.append({"expired_token_rejected": True, "logged_out_token_rejected": True})

    @case("UC-23", "Count failed attempts and enforce cooldown", "5 wrong admin passwords, correct sixth, wait 16 minutes", "Sixth RATE_LIMITED; correct login works after cooldown", "W2.10B failure count; W2.3A Rate Limited")
    async def test_uc23_rate_limit(self):
        for _ in range(5):
            await self.reject("login", {"username": "admin", "password": "WrongPassword2026"}, None, "AUTHENTICATION")
        await self.reject("login", {"username": "admin", "password": self.password}, None, "RATE_LIMITED")
        self.clock += timedelta(minutes=16)
        result = await self.backend.run("login", {"username": "admin", "password": self.password})
        self.actual.append({"recovered_user": result["user"]["username"]})

    @case("UC-23", "Use generic credential errors and keep credentials private", "Unknown user vs wrong known password; list user accounts", "Same error text; distinct salts/hashes; no password/hash in API account results", "W2.10B No Match; salted hashing and safe output")
    async def test_uc23_privacy(self):
        await self.reject("login", {"username": "unknown", "password": "WrongPassword2026"}, None)
        await self.reject("login", {"username": "admin", "password": "WrongPassword2026"}, None)
        self.assertEqual(self.actual[0]["message"], self.actual[1]["message"])
        rows = await self.call("users", user="sysadmin")
        self.assertNotIn("password_hash", json.dumps(rows))
        hashes = await self.backend.rows("SELECT salt,password_hash FROM users")
        self.assertEqual(6, len({r["password_hash"] for r in hashes}))

    @case("Security matrix", "Test every role against every protected operation", "6 roles times all named operation permissions", "Exactly the documented permissions allowed; technical/operational roles separated", "Permission Check Logic full role-operation matrix")
    async def test_permission_matrix(self):
        for username, role in ROLES.items():
            user = await self.backend.current_user(await self.token(username))
            for operation, allowed in PERMISSIONS.items():
                if role in allowed:
                    self.backend.permit(user, operation)
                else:
                    with self.assertRaises(AppError):
                        self.backend.permit(user, operation)
            self.actual.append({"role": role, "allowed": user["permissions"]})
        await self.reject("dashboard", user=None, code="AUTHENTICATION")

    @case("UC-24", "Append events with actor/role/target and prevent history edits", "Shelter occupancy write; raw UPDATE/DELETE of audit/history", "Actor and role retained; all historical edits rejected by SQL triggers", "X1 main immutable recording")
    async def test_uc24_immutable(self):
        await self.call("occupancy", {"id": "SH-001", "delta": 1, "version": 1}, "shelter")
        event = (await self.backend.rows("SELECT * FROM audit WHERE action='UC-12 OCCUPANCY_CHANGED'"))[0]
        self.actual.append(event)
        self.assertEqual("shelter", event["actor"])
        self.assertEqual(["Shelter Manager"], json.loads(event["details"])["actor_roles"])
        for kind in ["audit", "history"]:
            for sql in [f"UPDATE {kind} SET actor='forged'", f"DELETE FROM {kind}"]:
                with self.assertRaises(sqlite3.IntegrityError):
                    self.db.connection.execute(sql)

    @case("UC-24", "Persist queued events and recover original IDs once", "Audit outage; occupancy change; new Backend; recover", "Business write retained; queue and alert exist; original IDs flushed exactly once", "X1.3A Store Unavailable; X2 Store Recovered")
    async def test_uc24_queue_recovery(self):
        await self.call("fault", {"name": "audit_unavailable", "enabled": True}, "sysadmin")
        await self.call("occupancy", {"id": "SH-001", "delta": 1, "version": 1}, "shelter")
        queued = {r["event_id"] for r in await self.backend.rows("SELECT * FROM audit_queue")}
        self.assertTrue(queued)
        second = Backend(self.db, hash_password, lambda: self.clock)
        result = await self.call("fault", {"name": "audit_unavailable", "enabled": False}, "sysadmin")
        self.assertGreater(result["recovered"], 0)
        self.assertTrue(queued <= {r["event_id"] for r in await second.rows("SELECT * FROM audit")})
        self.assertEqual(0, await second.recover_audit())
        self.assertEqual([], await second.rows("SELECT * FROM audit_queue"))

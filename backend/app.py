"""DRRCS backend: permissions, validation and use-case operations."""
import hashlib
import hmac
import json
import re
import secrets
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from checks import AppError, text, integer, number, location, boundary, contains, overlaps, distance, timestamp, period

ROLES = {"admin": "Emergency Administrator", "sysadmin": "System Administrator",
         "dispatcher": "Dispatcher", "responder": "First Responder",
         "resource": "Resource Coordinator", "shelter": "Shelter Manager"}
PERMISSIONS = {
    "occupancy": {"Shelter Manager"}, "shelter_private": set(ROLES.values()) - {"System Administrator"},
    "zones": {"Emergency Administrator"}, "orders": {"Emergency Administrator"},
    "restrictions": {"Emergency Administrator"}, "notices": {"Emergency Administrator"},
    "resolve": {"First Responder"}, "close": {"Dispatcher", "Emergency Administrator"},
    "override": {"Emergency Administrator"}, "incidents": {"Dispatcher", "Emergency Administrator", "First Responder"},
    "incident_add": {"Dispatcher"},
    "dashboard": {"Dispatcher", "Emergency Administrator", "Resource Coordinator", "Shelter Manager"},
    "reports": {"Emergency Administrator"}, "users": {"System Administrator"},
    "audit": {"System Administrator"}, "fault": {"System Administrator"},
    # UC-1 to UC-11
    "citizen_reports": {"Dispatcher"}, "incident_update": {"Dispatcher", "First Responder"},
    "assign": {"Dispatcher"}, "progress": {"First Responder"}, "escalate": {"First Responder"},
    "resource_request": {"First Responder"}, "requests": {"Resource Coordinator"},
    "inventory": {"Resource Coordinator"}, "shelter_update": {"Shelter Manager"},
}
FIELD_COMMANDS = {
    "citizen_reports": "citizen_report_list", "citizen_report": "citizen_report_detail", "report_matches": "report_matches",
    "report_review": "report_review", "incident_update": "incident_update", "assign_options": "assign_options",
    "assign": "assign", "assign_escalation": "assign_escalation", "progress": "progress", "escalate": "escalate",
    "resource_request": "resource_request", "requests": "request_list", "request_options": "request_options",
    "allocate": "allocate", "request_decision": "request_decision", "inventory": "inventory",
    "resource_save": "resource_save", "resource_history": "resource_history", "shelter_update": "shelter_update",
}
KINDS = {"shelters", "incidents", "zones", "restrictions", "orders", "notices", "reports",
         "responders", "assignments", "resources", "allocations", "requests", "citizen_reports"}
SOURCES = ("incidents", "responders", "assignments", "resources", "allocations", "requests", "shelters", "zones", "orders")
GROUPS = {"Citizens", "Emergency Personnel"}
INCIDENT_TYPES = ["Flood", "Fire", "Traffic accident", "Medical emergency", "Rescue", "Hazard", "Other"]
QUALIFICATIONS = ["Firefighting", "EMT", "Paramedic", "Search and Rescue", "Water Rescue", "HazMat", "Structural Engineering", "Police"]
PRIORITIES = ["Low", "Medium", "High", "Urgent"]
URGENCIES = ["Low", "Medium", "High", "Critical"]
REPORT_CLASSES = ["Incorrect", "Fraudulent", "Non-emergency"]
PUBLIC_REPORT_STATUS = {"Submitted": "Received - waiting for dispatcher review",
                        "Verified": "Verified - emergency services are handling it",
                        "Associated": "Linked to an incident that is already being handled",
                        "Incorrect": "Reviewed - no emergency response needed",
                        "Fraudulent": "Reviewed - no emergency response needed",
                        "Non-emergency": "Reviewed - no emergency response needed"}
UPDATE_TYPES = {"ArrivedOnScene": "Arrived on scene", "SituationUpdate": "Situation update",
                "Contained": "Incident contained", "AssignmentCompleted": "Assignment completed"}
RESOURCE_CATEGORIES = ["Water", "Food", "Medical supplies", "Rescue boats", "Vehicles", "Generators", "Sandbags", "Blankets", "Other"]
RESOURCE_KINDS = ["Vehicle", "Equipment", "Consumable"]
SHELTER_STATES = ["Open", "Closed", "Temporarily Unavailable", "Planned"]
REPORT_ID = re.compile(r"RPT-[A-HJ-NP-Z2-9]{8}")
RESOURCE_ID = re.compile(r"[A-Z]{2,10}-\d{3}")
PHONE = re.compile(r"\+?[0-9][0-9 ()\-.]{6,19}")
PERSON = re.compile(r"[A-Za-zÀ-ÿ' .\-]{2,100}")
ACTIVE_ASSIGNMENT = {"Assigned", "Responding"}
OPEN_REQUEST = {"Pending", "Waiting", "Partially Fulfilled"}


def now():
    return datetime.now(timezone.utc)


def new_id(prefix):
    return prefix + "-" + secrets.token_hex(3).upper()


def choice(value, options, name):
    if not isinstance(value, str) or value not in options:
        raise AppError(f"{name} must be one of: {', '.join(options)}.")
    return value


def resource_category(row):
    """Older inventory rows have no category; derive one from the name."""
    if row.get("category"):
        return row["category"]
    name = row.get("name", "").lower()
    for word, category in [("water", "Water"), ("boat", "Rescue boats"), ("food", "Food"), ("medical", "Medical supplies"),
                           ("generator", "Generators"), ("sandbag", "Sandbags"), ("blanket", "Blankets")]:
        if word in name:
            return category
    return "Vehicles" if row.get("kind") == "Vehicle" else "Other"


def resource_view(row):
    return row | {"category": resource_category(row), "status": row.get("status", "Available")}


def account_view(row):
    result = {key: row[key] for key in ("username", "name", "agency", "status", "version")}
    result["roles"] = json.loads(row["roles"])
    result["permissions"] = sorted(op for op, roles in PERMISSIONS.items() if roles & set(result["roles"]))
    return result


class Backend:
    def __init__(self, db, hash_password, clock=now):
        self.db = db
        self.hash_password = hash_password
        self.clock = clock

    async def rows(self, sql, *values):
        return await self.db.query(sql, values)

    async def setting(self, name):
        rows = await self.rows("SELECT value FROM settings WHERE name=?", name)
        return rows[0]["value"] if rows else "no"

    async def records(self, kind):
        if kind not in KINDS:
            raise AppError("Unknown record type.")
        rows = await self.rows("SELECT * FROM records WHERE kind=? ORDER BY id", kind)
        return [json.loads(r["data"]) | {"id": r["id"], "version": r["version"]} for r in rows]

    async def record(self, kind, record_id):
        rows = await self.rows("SELECT * FROM records WHERE kind=? AND id=?", kind, record_id)
        if not rows:
            raise AppError(f"{record_id} was not found.", "NOT_FOUND")
        row = rows[0]
        return json.loads(row["data"]) | {"id": row["id"], "version": row["version"]}

    def write(self, kind, record, expected=None):
        data = dict(record)
        rid = data.pop("id")
        data.pop("version", None)
        payload = json.dumps(data, ensure_ascii=False, allow_nan=False)
        if expected is None:
            return [("INSERT INTO records(kind,id,data,version) VALUES (?,?,?,1)", (kind, rid, payload))]
        check = secrets.token_hex(8)
        # If another user changed the version, CHECK fails and D1 rolls back the batch.
        return [
            ("UPDATE records SET data=?,version=version+1 WHERE kind=? AND id=? AND version=?", (payload, kind, rid, expected)),
            ("INSERT INTO write_checks VALUES (?,CASE WHEN changes()=1 THEN 1 ELSE 0 END)", (check,)),
            ("DELETE FROM write_checks WHERE id=?", (check,)),
        ]

    async def commit(self, user, action, target, details, changes=(), alerts=(), history_kind=None):
        actor = user["username"]
        details = dict(details) | {"actor_roles": user["roles"]}
        created = self.clock().isoformat()
        table = "audit_queue" if await self.setting("audit_unavailable") == "yes" else "audit"
        statements = list(changes)
        statements.append((f"INSERT INTO {table} VALUES (?,?,?,?,?,?)", (secrets.token_hex(16), actor, action, target, created, json.dumps(details, ensure_ascii=False))))
        if history_kind:
            statements.append(("INSERT INTO history(kind,target,actor,created_at,details) VALUES (?,?,?,?,?)", (history_kind, target, actor, created, json.dumps(details, ensure_ascii=False))))
        alerts = list(alerts)
        if table == "audit_queue":
            alerts.append(("System Administrator", "Audit store unavailable. Event safely queued."))
        for recipient, message in alerts:
            statements.append(("INSERT INTO alerts(recipient,created_at,message) VALUES (?,?,?)", (recipient, created, message)))
        await self.db.batch(statements)

    async def current_user(self, token):
        if not isinstance(token, str) or not 20 <= len(token) <= 200:
            raise AppError("Log in first: login <username>", "AUTHENTICATION")
        digest = hashlib.sha256(token.encode()).hexdigest()
        rows = await self.rows("SELECT users.* FROM sessions JOIN users USING(username) WHERE token_hash=? AND expires>? AND users.status='Active'", digest, self.clock().timestamp())
        if not rows:
            raise AppError("Your session expired or account is inactive. Log in again.", "AUTHENTICATION")
        return account_view(rows[0])

    def permit(self, user, operation):
        if not set(user["roles"]) & PERMISSIONS.get(operation, set()):
            raise AppError(f"Your role cannot perform {operation}.", "FORBIDDEN")

    async def login(self, data):
        username = text(data.get("username"), "Username", 1, 64).lower()
        password = data.get("password")
        if not isinstance(password, str) or not 1 <= len(password) <= 128:
            raise AppError("Enter a password of at most 128 characters.")
        clock = self.clock().timestamp()
        attempts = await self.rows("SELECT * FROM attempts WHERE identity=?", username)
        anonymous = {"username": username, "roles": ["Unauthenticated"]}
        if attempts and attempts[0]["count"] >= 5 and attempts[0]["until_time"] > clock:
            await self.commit(anonymous, "UC-23 LOGIN_RATE_LIMITED", username, {})
            raise AppError("Too many attempts. Wait 15 minutes.", "RATE_LIMITED")
        rows = await self.rows("SELECT * FROM users WHERE username=?", username)
        row = rows[0] if rows else None
        salt = row["salt"] if row else "00000000000000000000000000000000"
        candidate = await self.hash_password(password, salt)
        if not row or row["status"] != "Active" or not hmac.compare_digest(candidate, row["password_hash"]):
            changes = [("INSERT INTO attempts VALUES (?,1,?) ON CONFLICT(identity) DO UPDATE SET count=CASE WHEN attempts.until_time>? THEN attempts.count+1 ELSE 1 END,until_time=excluded.until_time", (username, clock+900, clock))]
            await self.commit(anonymous, "UC-23 LOGIN_FAILED", username, {}, changes)
            raise AppError("Invalid username or password, or account inactive.", "AUTHENTICATION")
        token = secrets.token_urlsafe(32)
        user = account_view(row)
        changes = [("DELETE FROM sessions WHERE expires<=?", (clock,)),
                   ("DELETE FROM attempts WHERE identity=?", (username,)),
                   ("INSERT INTO sessions VALUES (?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), username, clock+1800))]
        await self.commit(user, "UC-23 LOGIN_SUCCESS", username, {}, changes)
        return {"token": token, "user": user, "expires_in": 1800}

    async def run(self, command, data, token="", client=""):
        if not isinstance(data, dict):
            raise AppError("Command data must be an object.")
        if command == "login":
            return await self.login(data)
        if command == "report_submit":
            return await self.report_submit(data, client)
        if command == "report_status":
            return await self.report_status(data)
        if command == "shelters":
            return await self.search_shelters(data)
        if command == "shelter" and not data.get("private"):
            return await self.shelter_detail(data["id"])
        user = await self.current_user(token)
        if command == "whoami":
            return user
        if command == "logout":
            await self.db.batch([("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))])
            return {"message": "Signed out."}
        if command == "alerts":
            rows = await self.rows("SELECT * FROM alerts ORDER BY id DESC LIMIT 500")
            return [r for r in rows if r["recipient"] in {user["username"], *user["roles"]}]
        operation = {"shelter": "shelter_private", "incident": "incidents", "incident_history": "incidents",
                     "zone_save": "zones", "order_save": "orders", "restriction_save": "restrictions",
                     "notice_save": "notices", "notice_retry": "notices", "report_create": "reports",
                     "report_notes": "reports", "report_finalize": "reports", "report": "reports", "user_save": "users",
                     "citizen_report": "citizen_reports", "report_matches": "citizen_reports", "report_review": "citizen_reports",
                     "assign_options": "assign", "assign_escalation": "assign", "request_options": "requests", "allocate": "requests",
                     "request_decision": "requests", "resource_save": "inventory", "resource_history": "inventory"}.get(command, command)
        try:
            self.permit(user, operation)
        except AppError:
            await self.commit(user, "UC-23 ACCESS_DENIED", command, {})
            raise
        if command in FIELD_COMMANDS:
            return await getattr(self, FIELD_COMMANDS[command])(user, data)
        if command == "shelter":
            result = await self.shelter_detail(data["id"], user)
            await self.commit(user, "UC-13 PRIVATE_DETAIL_READ", data["id"], {})
            return result
        if command == "occupancy":
            return await self.occupancy(user, data)
        if command in {"zones", "orders", "restrictions", "notices", "reports"}:
            return await self.records(command)
        if command in {"zone_save", "order_save", "restriction_save", "notice_save", "notice_retry"}:
            return await getattr(self, command)(user, data)
        if command in {"incidents", "incident", "incident_history"}:
            records = await self.incident_list(user)
            if command == "incidents":
                return records
            result = next((r for r in records if r["id"] == data["id"]), None)
            if result is None:
                raise AppError("Incident not found in your permitted scope.", "FORBIDDEN")
            if command == "incident_history":
                return await self.rows("SELECT * FROM history WHERE target=? ORDER BY id", data["id"])
            return result
        if command in {"resolve", "close", "incident_add"}:
            return await getattr(self, command)(user, data)
        if command == "dashboard":
            return await self.dashboard(user)
        if command.startswith("report_"):
            return await getattr(self, command)(user, data)
        if command == "report":
            return await self.record("reports", data["id"])
        if command == "users":
            return [account_view(r) for r in await self.rows("SELECT * FROM users ORDER BY username")]
        if command == "user_save":
            return await self.user_save(user, data)
        if command == "audit":
            return {"events": await self.rows("SELECT * FROM audit ORDER BY rowid DESC LIMIT 200"), "queued": await self.rows("SELECT * FROM audit_queue ORDER BY rowid LIMIT 200")}
        if command == "fault":
            name = data.get("name")
            allowed = {"audit_unavailable", "delivery_failure:Citizens", "delivery_failure:Emergency Personnel"} | {"source_unavailable:"+s for s in SOURCES}
            if name not in allowed or not isinstance(data.get("enabled"), bool):
                raise AppError("Choose a valid demo adapter fault and boolean state.")
            await self.commit(user, "DEMO_FAULT_CHANGED", name, {"enabled": data["enabled"]},
                              [("INSERT INTO settings VALUES (?,?) ON CONFLICT(name) DO UPDATE SET value=excluded.value", (name, "yes" if data["enabled"] else "no"))])
            recovered = await self.recover_audit()
            return {"fault": name, "enabled": data["enabled"], "recovered": recovered}
        raise AppError("Unknown command. Type help.", "NOT_FOUND")

    async def search_shelters(self, data):
        point = location(data.get("location", "41.88,-87.63"))
        radius = number(data.get("radius", 100), "Radius", 0.1, 20000)
        spaces = integer(data.get("spaces", 1), "Minimum spaces", 0, 1000000)
        status = data.get("status", "Any")
        if status not in {"Any", "Open", "Near Capacity", "Full"}:
            raise AppError("Status must be Any, Open, Near Capacity or Full.")
        results = []
        for shelter in await self.records("shelters"):
            km = distance(point, shelter["location"])
            available = max(0, shelter["capacity"]-shelter["occupancy"])
            if shelter["status"] in {"Open", "Near Capacity", "Full"} and available >= spaces and km <= radius and status in {"Any", shelter["status"]}:
                results.append(self.public_shelter(shelter) | {"distance_km": round(km, 2), "available_spaces": available})
        return sorted(results, key=lambda r: (r["distance_km"], -r["available_spaces"]))

    @staticmethod
    def public_shelter(row):
        return {key: row[key] for key in ("id", "name", "location", "capacity", "occupancy", "status", "medical_support", "accessibility", "public_contact", "version")}

    async def active_restrictions(self):
        return [r for r in await self.records("restrictions") if r["status"] == "Active" and timestamp(r["start"]) <= self.clock() < timestamp(r["end"])]

    async def shelter_detail(self, rid, user=None):
        row = await self.record("shelters", rid)
        result = self.public_shelter(row)
        result["restrictions"] = [r for r in await self.active_restrictions() if contains(r["boundary"], row["location"])]
        if user:
            if set(user["roles"]) == {"Shelter Manager"} and row["manager"] != user["username"]:
                raise AppError("This shelter is not assigned to you.", "FORBIDDEN")
            result.update(manager=row["manager"], supplies=row["supplies"], internal_contact=row["internal_contact"])
        return result

    async def occupancy(self, user, data):
        row = await self.record("shelters", data["id"])
        if row["manager"] != user["username"]:
            raise AppError("This shelter is not assigned to you.", "FORBIDDEN")
        if row["status"] not in {"Open", "Near Capacity", "Full"}:
            raise AppError("The shelter is not open.")
        count = row["occupancy"] + integer(data.get("delta"), "Occupancy change")
        if count < 0:
            raise AppError("Occupancy cannot be negative.")
        status = "Full" if count >= row["capacity"] else "Near Capacity" if count >= row["capacity"]*0.9 else "Open"
        preview = {"id": row["id"], "before": row["occupancy"], "after": count, "capacity": row["capacity"], "status": status, "version": row["version"],
                   "warning": "OVER CAPACITY. Give a reason to confirm." if count > row["capacity"] else ""}
        if data.get("preview"):
            return preview
        reason = text(data.get("reason", ""), "Over-capacity reason", 10) if preview["warning"] else ""
        changes = self.write("shelters", row | {"occupancy": count, "status": status}, integer(data.get("version"), "Version", 1))
        alerts = [("Emergency Administrator", f"{row['id']} reached {status}: {count}/{row['capacity']}.")] if status in {"Near Capacity", "Full"} and status != row["status"] else []
        await self.commit(user, "UC-12 OCCUPANCY_CHANGED", row["id"], preview | {"reason": reason}, changes, alerts, "ShelterOccupancyRecord")
        return await self.record("shelters", row["id"])

    async def impact(self, box):
        result = {}
        for kind in ("incidents", "shelters"):
            result[kind] = [r["id"] for r in await self.records(kind) if r["status"] != "Closed" and contains(box, r["location"])]
        result["zones"] = [r["id"] for r in await self.records("zones") if r["status"] == "Active" and overlaps(box, r["boundary"])]
        return result

    async def zone_save(self, user, data):
        box = boundary(data.get("boundary"))
        start, end = period(data.get("start"), data.get("end"))
        status = data.get("status", "Active")
        if status not in {"Active", "Inactive", "Closed"}:
            raise AppError("Zone status must be Active, Inactive or Closed.")
        rid = data.get("id") or new_id("ZN")
        old = await self.record("zones", rid) if data.get("id") else None
        row = {"id": rid, "name": text(data.get("name"), "Name", 2, 100), "boundary": box,
               "area": text(data.get("area", "Central"), "Operational area", 2, 100),
               "emergency_type": text(data.get("emergency_type"), "Emergency type", 2, 80),
               "severity": integer(data.get("severity"), "Severity", 1, 5), "status": status,
               "start": start, "end": end, "instructions": text(data.get("instructions"), "Instructions", 10),
               "created_at": old["created_at"] if old else self.clock().isoformat()}
        conflicts = [z["id"] for z in await self.records("zones") if z["id"] != rid and z["status"] == "Active" and status == "Active" and overlaps(box, z["boundary"]) and timestamp(start) < timestamp(z["end"]) and timestamp(z["start"]) < timestamp(end)]
        preview = {"zone": row, "conflicts": conflicts, "affected_operations": await self.impact(box)}
        if data.get("preview"):
            return preview
        row["justification"] = text(data.get("justification"), "Overlap justification", 10) if conflicts else ""
        await self.commit(user, "UC-14 ZONE_SAVED", rid, {"before": old, "after": row}, self.write("zones", row, data.get("version")),
                          [("Dispatcher", f"Zone {rid} published: {row['instructions']}"), ("First Responder", f"Zone {rid} published: {row['instructions']}")], "EmergencyZone")
        return await self.record("zones", rid)

    async def restriction_save(self, user, data):
        start, end = period(data.get("start"), data.get("end"))
        status = data.get("status", "Active")
        if status not in {"Active", "Inactive"}:
            raise AppError("Restriction status must be Active or Inactive.")
        row = {"id": data.get("id") or new_id("TR"), "name": text(data.get("name"), "Name", 2, 100),
               "boundary": boundary(data.get("boundary")), "start": start, "end": end,
               "instructions": text(data.get("instructions"), "Instructions", 10), "status": status}
        affected = await self.impact(row["boundary"])
        if data.get("preview"):
            return {"restriction": row, "affected_operations": affected}
        alerts = [("Dispatcher", f"Route affected by {row['id']}: {affected}")] if status == "Active" and any(affected.values()) else []
        await self.commit(user, "UC-16 RESTRICTION_SAVED", row["id"], {"restriction": row, "affected_operations": affected}, self.write("restrictions", row, data.get("version")), alerts, "TransportationRestriction")
        return await self.record("restrictions", row["id"])

    async def make_notice(self, user, data, rid=None):
        groups = data.get("groups", ["Citizens", "Emergency Personnel"])
        if not isinstance(groups, list) or not groups or any(not isinstance(g, str) or g not in GROUPS for g in groups):
            raise AppError("Audience must be Citizens and/or Emergency Personnel.")
        priority = data.get("priority", "Critical")
        if priority not in {"Informational", "Urgent", "Critical"}:
            raise AppError("Priority must be Informational, Urgent or Critical.")
        draft = data.get("draft", False)
        if not isinstance(draft, bool):
            raise AppError("Draft must be true or false.")
        row = {"id": rid or new_id("NT"), "message": text(data.get("message"), "Message", 10),
               "area": text(data.get("area"), "Affected area", 2, 200), "priority": priority,
               "groups": sorted(set(groups)), "effective": timestamp(data.get("effective"), "Effective time").isoformat(),
               "status": "Draft" if draft else "Scheduled", "authorized_by": None if draft else user["username"],
               "created_at": self.clock().isoformat(), "delivery": {}, "order_id": data.get("order_id"), "expires": data.get("expires")}
        alerts = []
        if not draft and timestamp(row["effective"]) <= self.clock():
            row, alerts = await self.delivery_result(row)
        return row, alerts

    async def delivery_result(self, row):
        row = dict(row)
        if not row["authorized_by"] or row["status"] == "Draft" or timestamp(row["effective"]) > self.clock():
            raise AppError("Notification is not authorized or not yet effective.")
        if row.get("expires") and timestamp(row["expires"]) <= self.clock():
            row["status"] = "Expired"
            return row, []
        delivery = dict(row["delivery"])
        for group in row["groups"]:
            if delivery.get(group) != "Delivered":
                delivery[group] = "Failed" if await self.setting("delivery_failure:"+group) == "yes" else "Delivered"
        failures = sum(v == "Failed" for v in delivery.values())
        status = "Failed" if failures == len(delivery) else "Partial Failure" if failures else "Delivered"
        row.update(delivery=delivery, status=status, delivered_at=self.clock().isoformat())
        alerts = [("Emergency Administrator", f"Notification {row['id']}: {status}.")] if failures else []
        return row, alerts

    async def notice_save(self, user, data):
        old = await self.record("notices", data["id"]) if data.get("id") else None
        if old and old["status"] != "Draft":
            raise AppError("Keep authorized notices as history; create a new notice.")
        row, alerts = await self.make_notice(user, data, data.get("id"))
        if data.get("preview"):
            # Preview never sends: the adapter only calculates hypothetical group results.
            return row | {"preview_only": True}
        await self.commit(user, "UC-17 NOTICE_SAVED", row["id"], row, self.write("notices", row, data.get("version")), alerts)
        return await self.record("notices", row["id"])

    async def notice_retry(self, user, data):
        old = await self.record("notices", data["id"])
        if old["status"] not in {"Failed", "Partial Failure"}:
            raise AppError("Only failed group deliveries can be retried.")
        row, alerts = await self.delivery_result(old)
        changes = self.write("notices", row, old["version"])
        if old.get("order_id"):
            order = await self.record("orders", old["order_id"])
            changes += self.write("orders", order | {"delivery_status": row["status"]}, order["version"])
        await self.commit(user, "UC-17 DELIVERY_RETRIED", row["id"], row, changes, alerts)
        return await self.record("notices", row["id"])

    async def order_save(self, user, data):
        zone = await self.record("zones", data["zone_id"])
        start, end = period(data.get("effective"), data.get("end"))
        if zone["status"] != "Active" or timestamp(start) < timestamp(zone["start"]) or timestamp(end) > timestamp(zone["end"]):
            raise AppError("Order must use an active zone and times within its period.")
        old = await self.record("orders", data["id"]) if data.get("id") else None
        if old and old["status"] != "Draft":
            raise AppError("Authorized orders are retained; issue a new order for a change.")
        draft = data.get("draft", False)
        if not isinstance(draft, bool):
            raise AppError("Draft must be true or false.")
        row = {"id": data.get("id") or new_id("EO"), "zone_id": zone["id"], "area": text(data.get("area"), "Affected area", 2, 200),
               "effective": start, "end": end, "instructions": text(data.get("instructions"), "Instructions", 10),
               "status": "Draft" if draft else "Authorized", "authorized_by": None if draft else user["username"],
               "created_at": self.clock().isoformat(), "delivery_status": "Not sent", "notification_id": None}
        changes = []
        alerts = []
        if not draft:
            notice, alerts = await self.make_notice(user, {"message": row["instructions"], "area": row["area"], "effective": start, "order_id": row["id"], "expires": end})
            row.update(notification_id=notice["id"], delivery_status=notice["status"])
            changes += self.write("notices", notice)
        if data.get("preview"):
            return row | {"preview_only": True}
        changes += self.write("orders", row, data.get("version"))
        await self.commit(user, "UC-15 ORDER_SAVED", row["id"], row, changes, alerts, "EvacuationOrder")
        return await self.record("orders", row["id"])

    async def incident_list(self, user):
        records = await self.records("incidents")
        if not set(user["roles"]) & {"Dispatcher", "Emergency Administrator"}:
            assigned = {a["incident_id"] for a in await self.records("assignments") if a["responder"] == user["username"] and a["status"] != "Released"}
            records = [r for r in records if r["id"] in assigned]
        restrictions = await self.active_restrictions()
        return [r | {"restrictions": [t for t in restrictions if contains(t["boundary"], r["location"])]} for r in records]

    async def incident_add(self, user, data):
        # A dispatcher entry supports recording new events; full UC-1..11 workflows remain separate.
        row = {"id": new_id("IN"), "type": text(data.get("type"), "Incident type", 2, 80),
               "description": text(data.get("description"), "Description", 10), "location": location(data.get("location")),
               "area": text(data.get("area", "Central"), "Operational area", 2, 100),
               "severity": integer(data.get("severity"), "Severity", 1, 5), "status": "Reported",
               "created_at": self.clock().isoformat(), "responded_at": None, "resolution": {}}
        await self.commit(user, "INCIDENT_RECORDED", row["id"], row, self.write("incidents", row), history_kind="IncidentHistoryEntry")
        return await self.record("incidents", row["id"])

    async def source_data(self, kinds):
        data, missing = {}, []
        for kind in kinds:
            if await self.setting("source_unavailable:"+kind) == "yes":
                data[kind] = None
                missing.append(kind)
            else:
                data[kind] = await self.records(kind)
        return data, missing

    async def dashboard(self, user):
        roles = set(user["roles"])
        if roles & {"Dispatcher", "Emergency Administrator"}:
            kinds = list(SOURCES)
        else:
            kinds = []
            if "Resource Coordinator" in roles:
                kinds += ["resources", "allocations", "requests"]
            if "Shelter Manager" in roles:
                kinds += ["shelters"]
        data, missing = await self.source_data(kinds)
        result = {"as_of": self.clock().isoformat(), "scope": kinds, "unavailable_sources": missing}
        if "incidents" in data and data["incidents"] is not None:
            items = data["incidents"]
            active = [r for r in items if r["status"] not in {"Resolved", "Closed"}]
            assigned = {a["incident_id"] for a in data["assignments"] or [] if a["status"] != "Released"}
            result.update(active_incidents=len(active), critical_incidents=sum(r["severity"] >= 4 for r in active),
                          incidents_by_severity=dict(Counter(str(r["severity"]) for r in items)),
                          unassigned_incidents=sum(r["id"] not in assigned for r in active) if data["assignments"] is not None else "Unavailable",
                          unresolved_conditions=sum(r["status"] not in {"Resolved", "Closed"} or r.get("resolution", {}).get("remaining", "None") != "None" for r in items))
        if "resources" in data and data["resources"] is not None:
            result["resources"] = data["resources"]
            result["available_vehicles"] = sum(r["available"] for r in data["resources"] if r["kind"] == "Vehicle")
        if "responders" in data and data["responders"] is not None:
            result["available_teams"] = sum(r["status"] == "Available" for r in data["responders"])
            result["assigned_teams"] = sum(r["status"] in {"Assigned", "Responding"} for r in data["responders"])
        if "requests" in data and data["requests"] is not None:
            result["pending_resource_requests"] = sum(r["status"] in {"Pending", "Waiting", "Partially Fulfilled"} for r in data["requests"])
        if "shelters" in data and data["shelters"] is not None:
            items = data["shelters"]
            if not roles & {"Dispatcher", "Emergency Administrator"}:
                items = [s for s in items if s["manager"] == user["username"]]
            result["shelters"] = [self.public_shelter(s) for s in items]
            result["shelters_near_or_full"] = sum(s["status"] in {"Near Capacity", "Full"} for s in items)
        if "zones" in data and data["zones"] is not None:
            result["active_evacuation_zones"] = sum(z["status"] == "Active" and timestamp(z["start"]) <= self.clock() < timestamp(z["end"]) for z in data["zones"])
        for kind in missing:
            result[kind] = "Unavailable"
        return result

    async def report_create(self, user, values):
        kind = values.get("kind", "Operational")
        if kind not in {"Operational", "After-Action"}:
            raise AppError("Report kind must be Operational or After-Action.")
        start, end = period(values.get("start"), values.get("end"))
        area = text(values.get("area", "All"), "Operational area", 2, 100)
        data, missing = await self.source_data(SOURCES)
        if await self.setting("audit_unavailable") == "yes" or await self.rows("SELECT 1 FROM audit_queue LIMIT 1"):
            missing.append("audit")
        incidents = [r for r in data["incidents"] or [] if timestamp(start) <= timestamp(r["created_at"]) < timestamp(end) and (area == "All" or r.get("area") == area)]
        if kind == "After-Action" and data["incidents"] is not None and not incidents:
            raise AppError("No incident records match this period and area.", "NO_RECORDS")
        ids = {r["id"] for r in incidents}
        assignments = [a for a in data["assignments"] or [] if a["incident_id"] in ids]
        requests = [r for r in data["requests"] or [] if r["incident_id"] in ids]
        allocations = [r for r in data["allocations"] or [] if r["incident_id"] in ids]
        delays = [(timestamp(r["responded_at"])-timestamp(r["created_at"])).total_seconds()/60 for r in incidents if r.get("responded_at")]
        shelters = [s for s in data["shelters"] or [] if area == "All" or s.get("area") == area]
        resources = [r for r in data["resources"] or [] if area == "All" or r.get("area") == area]
        zone_ids = {z["id"] for z in data["zones"] or [] if area == "All" or z.get("area") == area}
        orders = [r for r in data["orders"] or [] if timestamp(start) <= timestamp(r["created_at"]) < timestamp(end) and (area == "All" or r["zone_id"] in zone_ids)]
        metrics = {
            "total_incidents": len(incidents), "verified_incidents": sum(r["status"] != "Reported" for r in incidents),
            "incidents_by_type": dict(Counter(r["type"] for r in incidents)),
            "incidents_by_severity": dict(Counter(str(r["severity"]) for r in incidents)),
            "resolved_incidents": sum(r["status"] in {"Resolved", "Closed"} for r in incidents),
            "unresolved_incidents": sum(r["status"] not in {"Resolved", "Closed"} for r in incidents),
            "average_response_minutes": round(sum(delays)/len(delays), 2) if delays else None,
            "response_time_samples": len(delays),
            "personnel_utilization_percent": round(100*len({a["responder"] for a in assignments if a["status"] in {"Assigned", "Responding"}})/len(data["responders"]), 2) if data["responders"] else None,
            "responder_activity": dict(Counter(a["responder"] for a in assignments)),
            "allocated_resource_quantity": sum(a["quantity"] for a in allocations),
            "resources_current": resources, "shelter_usage_current": [self.public_shelter(s) for s in shelters],
            "pending_resource_requests": sum(r["status"] in {"Pending", "Waiting", "Partially Fulfilled"} for r in requests),
            "resource_shortages": [r for r in requests if r["status"] in {"Pending", "Waiting", "Partially Fulfilled"}],
            "evacuation_orders": len([r for r in orders if r["status"] != "Draft"]),
        }
        if data["incidents"] is None:
            for key in ["total_incidents", "verified_incidents", "incidents_by_type", "incidents_by_severity", "resolved_incidents", "unresolved_incidents", "response_time_samples"]:
                metrics[key] = None
        for source, keys in {"assignments": ["personnel_utilization_percent", "responder_activity"],
                             "allocations": ["allocated_resource_quantity"], "resources": ["resources_current"],
                             "shelters": ["shelter_usage_current"], "requests": ["pending_resource_requests", "resource_shortages"],
                             "orders": ["evacuation_orders"]}.items():
            if source in missing or (source in {"assignments", "allocations", "requests"} and "incidents" in missing):
                for key in keys:
                    metrics[key] = None
        by_area = defaultdict(list)
        for incident in incidents:
            by_area[incident.get("area", "Unspecified")].append(incident["id"])
        decisions = await self.rows("SELECT actor,action,target,created_at,details FROM audit WHERE created_at>=? AND created_at<? AND action NOT LIKE '%LOGIN%' ORDER BY rowid LIMIT 100", start, end)
        if area != "All":
            permitted_targets = ids | zone_ids | {s["id"] for s in shelters} | {o["id"] for o in orders}
            decisions = [r for r in decisions if r["target"] in permitted_targets]
        row = {"id": new_id("RP"), "kind": kind, "status": "Draft", "start": start, "end": end,
               "area": area, "metrics": metrics, "missing_sections": missing, "incidents_by_area": dict(by_area),
               "major_decisions": decisions, "notes": text(values.get("notes", ""), "Notes", 0, 10000),
               "limitations": text(values.get("limitations", ""), "Limitations", 0, 2000),
               "created_at": self.clock().isoformat(), "created_by": user["username"],
               "snapshot_note": "Incidents use creation times in [start,end). Inventory and shelter values are current snapshots."}
        await self.commit(user, "UC-20 REPORT_DRAFT" if kind == "Operational" else "UC-21 REPORT_DRAFT", row["id"], {"kind": kind, "missing_sections": missing}, self.write("reports", row))
        return await self.record("reports", row["id"])

    async def report_notes(self, user, data):
        row = await self.record("reports", data["id"])
        if row["status"] != "Draft":
            raise AppError("Finalized reports cannot be edited.", "IMMUTABLE")
        row.update(notes=text(data.get("notes", ""), "Notes", 0, 10000), limitations=text(data.get("limitations", ""), "Limitations", 0, 2000))
        await self.commit(user, "REPORT_NOTES_SAVED", row["id"], {"notes": row["notes"], "limitations": row["limitations"]}, self.write("reports", row, integer(data.get("version"), "Version", 1)))
        return await self.record("reports", row["id"])

    async def report_finalize(self, user, data):
        row = await self.record("reports", data["id"])
        if row["status"] != "Draft":
            raise AppError("Report is already finalized.", "IMMUTABLE")
        if row["kind"] == "Operational" and row["missing_sections"]:
            raise AppError("Missing data blocks finalization. Recover sources and build a complete draft.", "INCOMPLETE")
        if row["kind"] == "After-Action":
            text(row["notes"], "After-action notes and lessons learned", 10, 10000)
            if row["missing_sections"]:
                text(row["limitations"], "Documented limitations", 10)
        row.update(status="Finalized", finalized_at=self.clock().isoformat(), finalized_by=user["username"])
        await self.commit(user, "UC-20 REPORT_FINALIZED" if row["kind"] == "Operational" else "UC-21 REPORT_FINALIZED", row["id"], {"kind": row["kind"]}, self.write("reports", row, integer(data.get("version"), "Version", 1)))
        return await self.record("reports", row["id"])

    async def user_save(self, actor, data):
        username = text(data.get("username"), "Username", 3, 64).lower()
        if not re.fullmatch(r"[a-z0-9_.@-]+", username):
            raise AppError("Username may contain letters, digits, _, ., @ and -.")
        name, agency = text(data.get("name"), "Name", 2, 100), text(data.get("agency"), "Agency", 2, 100)
        roles = data.get("roles")
        if not isinstance(roles, list) or not roles or any(not isinstance(r, str) or r not in ROLES.values() for r in roles):
            raise AppError("Select valid roles.")
        roles = sorted(set(roles))
        if ("System Administrator" in roles and len(roles) > 1) or ("First Responder" in roles and set(roles) & {"Dispatcher", "Emergency Administrator"}):
            raise AppError("Separation of duties: this role combination is not allowed.", "SOD_VIOLATION")
        status = data.get("status", "Active")
        if status not in {"Active", "Inactive"}:
            raise AppError("Status must be Active or Inactive.")
        rows = await self.rows("SELECT * FROM users WHERE username=?", username)
        old = rows[0] if rows else None
        version = data.get("version")
        if old and version is None:
            raise AppError("Username already exists. Use user edit.", "CONFLICT")
        if version is not None and (not old or old["version"] != version):
            raise AppError("Account changed. Reload it.", "CONFLICT")
        password = data.get("password", "")
        if not old or password:
            if not isinstance(password, str) or not 12 <= len(password) <= 128 or not any(c.isalpha() for c in password) or not any(c.isdigit() for c in password):
                raise AppError("Password needs 12-128 characters, a letter and a number.")
        if old and "System Administrator" in json.loads(old["roles"]) and (status != "Active" or "System Administrator" not in roles):
            others = await self.rows("SELECT roles FROM users WHERE username<>? AND status='Active'", username)
            if not any("System Administrator" in json.loads(r["roles"]) for r in others):
                raise AppError("Keep at least one active System Administrator.")
        preview = {"username": username, "name": name, "agency": agency, "roles": roles, "status": status,
                   "permissions": sorted(op for op, allowed in PERMISSIONS.items() if allowed & set(roles))}
        if data.get("preview"):
            return preview
        salt = secrets.token_hex(16) if password else old["salt"]
        hashed = await self.hash_password(password, salt) if password else old["password_hash"]
        changes = []
        if old:
            changes.append(("UPDATE users SET name=?,agency=?,roles=?,status=?,salt=?,password_hash=?,version=version+1 WHERE username=? AND version=?", (name, agency, json.dumps(roles), status, salt, hashed, username, version)))
            check = secrets.token_hex(8)
            changes += [("INSERT INTO write_checks VALUES (?,CASE WHEN changes()=1 THEN 1 ELSE 0 END)", (check,)), ("DELETE FROM write_checks WHERE id=?", (check,))]
        else:
            changes.append(("INSERT INTO users VALUES (?,?,?,?,?,?,?,1)", (username, name, agency, json.dumps(roles), status, salt, hashed)))
        await self.commit(actor, "UC-22 ACCOUNT_SAVED", username, {"before": account_view(old) if old else None, "after": preview}, changes, [(username, "Your account or roles were updated.")])
        return account_view((await self.rows("SELECT * FROM users WHERE username=?", username))[0])

    async def recover_audit(self):
        if await self.setting("audit_unavailable") == "yes":
            return 0
        queued = await self.rows("SELECT event_id FROM audit_queue ORDER BY rowid LIMIT 100")
        if not queued:
            return 0
        ids = tuple(r["event_id"] for r in queued)
        placeholders = ",".join("?" for _ in ids)
        await self.db.batch([(f"INSERT OR IGNORE INTO audit SELECT * FROM audit_queue WHERE event_id IN ({placeholders})", ids),
                             (f"DELETE FROM audit_queue WHERE event_id IN ({placeholders})", ids)])
        return len(ids)

    async def scheduled(self):
        recovered = await self.recover_audit()
        due = [r for r in await self.records("notices") if r["status"] == "Scheduled" and timestamp(r["effective"]) <= self.clock()][:3]
        system = {"username": "SYSTEM", "roles": ["System"]}
        for old in due:
            row, alerts = await self.delivery_result(old)
            changes = self.write("notices", row, old["version"])
            if old.get("order_id"):
                order = await self.record("orders", old["order_id"])
                changes += self.write("orders", order | {"delivery_status": row["status"]}, order["version"])
            await self.commit(system, "UC-17 SCHEDULED_DELIVERY", row["id"], row, changes, alerts)
        return {"recovered_audit_events": recovered, "due_notices_processed": len(due)}

    async def resolve(self, user, data):
        row = await self.record("incidents", data["id"])
        assignments = [a for a in await self.records("assignments") if a["incident_id"] == row["id"] and a["responder"] == user["username"] and a["status"] in {"Assigned", "Responding"}]
        if not assignments:
            raise AppError("You are not actively assigned to this incident.", "FORBIDDEN")
        if row["status"] not in {"Assigned", "Responding"}:
            raise AppError("Only Assigned or Responding incidents may be resolved.")
        completion = timestamp(data.get("completion"), "Completion time")
        if not timestamp(row["created_at"]) <= completion <= self.clock():
            raise AppError("Completion must be after creation and not in the future.")
        summary = {"actions": text(data.get("actions"), "Actions taken", 10), "outcome": text(data.get("outcome"), "Outcome", 10),
                   "remaining": text(data.get("remaining"), "Remaining conditions", 4), "completion": completion.isoformat()}
        if data.get("preview"):
            return {"id": row["id"], "version": row["version"], "resolution": summary}
        changes = self.write("incidents", row | {"status": "Resolved", "resolution": summary, "resolved_at": summary["completion"]}, integer(data.get("version"), "Version", 1))
        for assignment in assignments:
            changes += self.write("assignments", assignment | {"status": "Complete"}, assignment["version"])
        await self.commit(user, "UC-18 INCIDENT_RESOLVED", row["id"], summary, changes, history_kind="IncidentHistoryEntry")
        return await self.record("incidents", row["id"])

    async def close(self, user, data):
        row = await self.record("incidents", data["id"])
        if row["status"] != "Resolved":
            raise AppError("Resolve the incident before closing it.")
        if any(not row.get("resolution", {}).get(k) for k in ("actions", "outcome", "remaining", "completion")):
            raise AppError("Resolution documentation is missing.", "MISSING_DOCUMENTATION")
        notes = text(data.get("notes"), "Final notes", 10)
        assignments = [a for a in await self.records("assignments") if a["incident_id"] == row["id"] and a["status"] != "Released"]
        requests = [r for r in await self.records("requests") if r["incident_id"] == row["id"] and r["status"] in {"Pending", "Waiting", "Partially Fulfilled"}]
        open_assignments = [a["id"] for a in assignments if a["status"] != "Complete"]
        review = {"id": row["id"], "version": row["version"], "open_assignments": open_assignments, "open_requests": [r["id"] for r in requests]}
        if data.get("preview"):
            return review | {"warning": "Administrator override required for open items." if open_assignments or requests else "Ready to close."}
        reason = ""
        if open_assignments or requests:
            self.permit(user, "override")
            reason = text(data.get("reason"), "Override reason", 10)
        changes = self.write("incidents", row | {"status": "Closed", "final_notes": notes, "closed_at": self.clock().isoformat(), "override_reason": reason}, integer(data.get("version"), "Version", 1))
        all_assignments = await self.records("assignments")
        for assignment in assignments:
            changes += self.write("assignments", assignment | {"status": "Released"}, assignment["version"])
            busy_elsewhere = any(a["responder"] == assignment["responder"] and a["incident_id"] != row["id"] and a["status"] != "Released" for a in all_assignments)
            if not busy_elsewhere:
                responder = await self.record("responders", assignment["responder"])
                changes += self.write("responders", responder | {"status": "Available"}, responder["version"])
        resource_changes = {}
        for allocation in await self.records("allocations"):
            if allocation["incident_id"] != row["id"] or allocation["status"] != "Active":
                continue
            rid = allocation["resource_id"]
            resource = resource_changes.get(rid) or await self.record("resources", rid)
            resource["allocated"] -= allocation["quantity"]
            if resource["allocated"] < 0:
                raise AppError("Inconsistent inventory. No closure changes were saved.")
            key = "consumed" if resource["kind"] == "Consumable" else "available"
            resource[key] = resource.get(key, 0)+allocation["quantity"]
            resource_changes[rid] = resource
            changes += self.write("allocations", allocation | {"status": "Released"}, allocation["version"])
        for resource in resource_changes.values():
            changes += self.write("resources", resource, resource["version"])
        for request in requests:
            changes += self.write("requests", request | {"status": "Cancelled on closure", "closure_reason": reason}, request["version"])
        await self.commit(user, "UC-18 INCIDENT_CLOSED", row["id"], review | {"notes": notes, "override_reason": reason}, changes, history_kind="IncidentHistoryEntry")
        return await self.record("incidents", row["id"])

    # ------------------------------------------------------------------
    # UC-1 to UC-11. Same records, write()/commit() batches, history and
    # alerts as UC-12 to UC-24, so closure, dashboard and reports see them.
    # ------------------------------------------------------------------
    async def version_of(self, data):
        return integer(data.get("version"), "Version", 1)

    async def active_assignment(self, user, incident_id):
        rows = [a for a in await self.records("assignments")
                if a["incident_id"] == incident_id and a["responder"] == user["username"] and a["status"] in ACTIVE_ASSIGNMENT]
        if not rows:
            raise AppError("You are not actively assigned to this incident.", "FORBIDDEN")
        return rows[0]

    async def open_incident(self, incident_id):
        row = await self.record("incidents", text(incident_id, "Incident", 2, 40))
        if row["status"] in {"Resolved", "Closed"}:
            raise AppError(f"{row['id']} is {row['status']}. No change was saved.", "CONFLICT")
        return row

    # UC-1 Submit Emergency Report (public, no account)
    async def report_submit(self, data, client=""):
        clock = self.clock()
        if client:
            identity = "report:"+hashlib.sha256(client.encode()).hexdigest()[:24]
            rows = await self.rows("SELECT * FROM attempts WHERE identity=?", identity)
            if rows and rows[0]["count"] >= 5 and rows[0]["until_time"] > clock.timestamp():
                raise AppError("Too many reports from this connection. Wait 10 minutes or call 911.", "RATE_LIMITED")
        occurred = timestamp(data.get("occurred_at"), "Date and time")
        if occurred > clock+timedelta(minutes=5) or occurred < clock-timedelta(days=30):
            raise AppError("Date and time must be within the last 30 days and not in the future.")
        point = location(data["location"]) if data.get("location") not in (None, "", []) else None
        name = data.get("contact_name", "") or ""
        phone = data.get("contact_phone", "") or ""
        if name and (not isinstance(name, str) or not PERSON.fullmatch(name.strip())):
            raise AppError("Name may contain letters, spaces, apostrophes and hyphens (2-100 characters).")
        if phone and (not isinstance(phone, str) or not PHONE.fullmatch(phone.strip())):
            raise AppError("Enter a valid phone number, for example +1 334-555-0100.")
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        row = {"id": "RPT-"+"".join(secrets.choice(alphabet) for _ in range(8)),
               "type": choice(data.get("type"), INCIDENT_TYPES, "Incident type"),
               "address": text(data.get("address"), "Location", 3, 200),
               "location": point, "description": text(data.get("description"), "Description", 10, 2000),
               "severity": integer(data.get("severity"), "Severity", 1, 5), "occurred_at": occurred.isoformat(),
               "contact_name": name.strip(), "contact_phone": phone.strip(), "status": "Submitted",
               "submitted_at": clock.isoformat(), "incident_id": None}
        changes = self.write("citizen_reports", row)
        if client:
            changes.append(("INSERT INTO attempts VALUES (?,1,?) ON CONFLICT(identity) DO UPDATE SET count=CASE WHEN attempts.until_time>? THEN attempts.count+1 ELSE 1 END,until_time=CASE WHEN attempts.until_time>? THEN attempts.until_time ELSE excluded.until_time END",
                            (identity, clock.timestamp()+600, clock.timestamp(), clock.timestamp())))
        citizen = {"username": "citizen", "roles": ["Citizen"]}
        await self.commit(citizen, "UC-1 REPORT_SUBMITTED", row["id"], {"type": row["type"], "severity": row["severity"]}, changes,
                          [("Dispatcher", f"New citizen report {row['id']}: {row['type']}, severity {row['severity']}, {row['address']}.")])
        return {"id": row["id"], "status": "Submitted", "submitted_at": row["submitted_at"]}

    # UC-2 Check Report Status (public view only)
    async def report_status(self, data):
        rid = str(data.get("id", "")).strip().upper()[:40]
        rows = await self.rows("SELECT * FROM records WHERE kind='citizen_reports' AND id=?", rid) if REPORT_ID.fullmatch(rid) else []
        if not rows:
            raise AppError("No report was found with that ID. Check the ID and try again.", "NOT_FOUND")
        row = json.loads(rows[0]["data"])
        return {"id": rid, "type": row["type"], "submitted_at": row["submitted_at"], "status": PUBLIC_REPORT_STATUS[row["status"]]}

    # UC-3 Verify Citizen Report
    async def citizen_report_list(self, user, data):
        rows = await self.records("citizen_reports")
        if data.get("status", "Submitted") == "Submitted":
            rows = [r for r in rows if r["status"] == "Submitted"]
        return sorted(rows, key=lambda r: (-r["severity"], r["submitted_at"]))

    async def citizen_report_detail(self, user, data):
        row = await self.record("citizen_reports", text(data.get("id"), "Report", 3, 40))
        await self.commit(user, "UC-3 REPORT_VIEWED", row["id"], {})
        return row

    async def report_matches(self, user, data):
        report = await self.record("citizen_reports", text(data.get("id"), "Report", 3, 40))
        words = {w for w in re.findall(r"[a-z]{4,}", (report["address"]+" "+report["description"]).lower())}
        matches = []
        for incident in await self.records("incidents"):
            if incident["status"] in {"Resolved", "Closed"}:
                continue
            score, km = 0, None
            if report.get("location"):
                km = round(distance(report["location"], incident["location"]), 2)
                if km <= 25:
                    score += round(60*(1-km/25))
            if incident["type"] == report["type"]:
                score += 30
            score += min(10, 2*len(words & set(re.findall(r"[a-z]{4,}", incident["description"].lower()))))
            if score >= 30:
                matches.append({"id": incident["id"], "type": incident["type"], "status": incident["status"], "severity": incident["severity"],
                                "description": incident["description"], "distance_km": km, "score": score, "version": incident["version"]})
        return sorted(matches, key=lambda m: -m["score"])[:5]

    def incident_fields(self, data, old=None):
        old = old or {}
        qualification = data.get("needed_qualification", old.get("needed_qualification", ""))
        if qualification:
            choice(qualification, QUALIFICATIONS, "Needed qualification")
        return {"type": choice(data.get("type", old.get("type")), INCIDENT_TYPES, "Incident type"),
                "description": text(data.get("description", old.get("description")), "Description", 10, 2000),
                "location": location(data.get("location", old.get("location"))),
                "area": text(data.get("area", old.get("area", "Central")), "Operational area", 2, 100),
                "severity": integer(data.get("severity", old.get("severity")), "Severity", 1, 5),
                "priority": choice(data.get("priority", old.get("priority", "Medium")), PRIORITIES, "Priority"),
                "needed_qualification": qualification or "",
                "responders_needed": integer(data.get("responders_needed", old.get("responders_needed", 1)), "Responders needed", 1, 50)}

    async def report_review(self, user, data):
        report = await self.record("citizen_reports", text(data.get("id"), "Report", 3, 40))
        if report["status"] != "Submitted":
            raise AppError(f"{report['id']} was already reviewed ({report['status']}).", "CONFLICT")
        version = await self.version_of(data)
        action = choice(data.get("action"), ["create", "associate", "classify"], "Action")
        reviewed = {"reviewed_by": user["username"], "reviewed_at": self.clock().isoformat()}
        if action == "create":
            fields = self.incident_fields(data)
            incident = {"id": new_id("IN"), **fields, "status": "Reported", "created_at": self.clock().isoformat(),
                        "responded_at": None, "resolution": {}, "originating_report": report["id"], "reports": [report["id"]]}
            if data.get("preview"):
                return {"report": report["id"], "action": "Create incident", "incident": incident}
            changes = self.write("citizen_reports", report | reviewed | {"status": "Verified", "incident_id": incident["id"]}, version)
            changes += self.write("incidents", incident)
            await self.commit(user, "UC-3 REPORT_VERIFIED", incident["id"], {"report": report["id"], "incident": fields}, changes, history_kind="IncidentHistoryEntry")
            return await self.record("incidents", incident["id"])
        if action == "associate":
            incident = await self.open_incident(data.get("incident_id"))
            if data.get("preview"):
                return {"report": report["id"], "action": "Associate with existing incident", "incident": incident["id"], "incident_type": incident["type"], "incident_status": incident["status"]}
            changes = self.write("citizen_reports", report | reviewed | {"status": "Associated", "incident_id": incident["id"]}, version)
            changes += self.write("incidents", incident | {"reports": sorted(set(incident.get("reports", [])) | {report["id"]})}, incident["version"])
            await self.commit(user, "UC-3 REPORT_ASSOCIATED", incident["id"], {"report": report["id"]}, changes, history_kind="IncidentHistoryEntry")
            return await self.record("incidents", incident["id"])
        classification = choice(data.get("classification"), REPORT_CLASSES, "Classification")
        reason = text(data.get("reason"), "Reason", 10, 500)
        if data.get("preview"):
            return {"report": report["id"], "action": "Classify as "+classification, "reason": reason}
        await self.commit(user, "UC-3 REPORT_CLASSIFIED", report["id"], {"classification": classification, "reason": reason},
                          self.write("citizen_reports", report | reviewed | {"status": classification, "classification_reason": reason}, version))
        return await self.record("citizen_reports", report["id"])

    # UC-4 Update Incident
    async def incident_update(self, user, data):
        row = await self.open_incident(data.get("id"))
        version = await self.version_of(data)
        if "Dispatcher" in user["roles"]:
            new = self.incident_fields(data, row)
        else:
            await self.active_assignment(user, row["id"])
            unknown = set(data) - {"id", "version", "description", "preview"}
            if unknown:
                raise AppError("First Responders may update only the description. Use progress updates or escalation for other changes.", "FORBIDDEN")
            new = {"description": text(data.get("description"), "Description", 10, 2000)}
        defaults = {"priority": "Medium", "needed_qualification": "", "responders_needed": 1, "area": "Central"}
        changes = {k: {"from": row.get(k, defaults.get(k)), "to": v} for k, v in new.items() if row.get(k, defaults.get(k)) != v}
        if not changes:
            raise AppError("No changes were made.")
        if data.get("preview"):
            return {"id": row["id"], "version": row["version"], "changes": changes}
        alerts = []
        if "severity" in changes and new["severity"] > row["severity"]:
            alerts.append(("Dispatcher", f"{row['id']} severity raised to {new['severity']}. Re-evaluate the response."))
        await self.commit(user, "UC-4 INCIDENT_UPDATED", row["id"], {"changes": changes}, self.write("incidents", row | new, version), alerts, "IncidentHistoryEntry")
        return await self.record("incidents", row["id"])

    # UC-5 Assign Emergency Personnel
    async def assign_options(self, user, data):
        row = await self.open_incident(data.get("id"))
        current = [a for a in await self.records("assignments") if a["incident_id"] == row["id"] and a["status"] in ACTIVE_ASSIGNMENT]
        taken = {a["responder"] for a in current}
        needed = row.get("needed_qualification", "")
        candidates = []
        for responder in await self.records("responders"):
            qualifications = responder.get("qualifications", [])
            if responder["status"] != "Available" or responder["id"] in taken or (needed and needed not in qualifications):
                continue
            km = round(distance(responder["location"], row["location"]), 2) if responder.get("location") else None
            score = (100-min(km, 100) if km is not None else 50) + 5*len(qualifications)
            candidates.append({"id": responder["id"], "name": responder.get("name", responder["id"]), "agency": responder.get("agency", ""),
                               "qualifications": qualifications, "distance_km": km, "score": round(score, 1), "version": responder["version"]})
        return {"incident": row, "assigned": current, "candidates": sorted(candidates, key=lambda c: -c["score"]),
                "needed_qualification": needed or "Any"}

    async def assign(self, user, data):
        row = await self.open_incident(data.get("id"))
        chosen = data.get("responders")
        if not isinstance(chosen, list) or not chosen or len(chosen) > 20 or any(not isinstance(r, str) for r in chosen):
            raise AppError("Select at least one responder.")
        options = await self.assign_options(user, {"id": row["id"]})
        available = {c["id"]: c for c in options["candidates"]}
        versions = data.get("versions", {})
        changes, alerts, names = [], [], []
        for rid in sorted(set(chosen)):
            if rid not in available:
                raise AppError(f"{rid} is no longer available or not qualified. Refresh the list.", "CONFLICT")
            responder = await self.record("responders", rid)
            if data.get("preview"):
                names.append(available[rid]["name"])
                continue
            expected = integer(versions.get(rid) if isinstance(versions, dict) else None, "Responder version", 1)
            changes += self.write("responders", responder | {"status": "Assigned"}, expected)
            changes += self.write("assignments", {"id": new_id("AS"), "incident_id": row["id"], "responder": rid, "status": "Assigned",
                                                  "assigned_at": self.clock().isoformat(), "assigned_by": user["username"]})
            alerts.append((rid, f"You are assigned to {row['id']} ({row['type']}, severity {row['severity']})."))
            names.append(responder.get("name", rid))
        new_status = "Assigned" if row["status"] == "Reported" else row["status"]
        if data.get("preview"):
            return {"incident": row["id"], "assign": names, "status_after": new_status, "versions": {r: available[r]["version"] for r in set(chosen)}, "version": row["version"]}
        update = {"status": new_status, "responded_at": row.get("responded_at") or self.clock().isoformat()}
        changes += self.write("incidents", row | update, await self.version_of(data))
        await self.commit(user, "UC-5 PERSONNEL_ASSIGNED", row["id"], {"responders": sorted(set(chosen)), "status": new_status}, changes, alerts, "IncidentHistoryEntry")
        return await self.record("incidents", row["id"])

    async def assign_escalation(self, user, data):
        row = await self.open_incident(data.get("id"))
        note = text(data.get("note"), "Note", 10, 500)
        await self.commit(user, "UC-5 STAFFING_ESCALATED", row["id"], {"note": note}, alerts=[("Emergency Administrator", f"No qualified responder available for {row['id']} ({row.get('needed_qualification') or 'any'}): {note}")])
        return {"id": row["id"], "message": "Escalation sent to the Emergency Administrator."}

    # UC-6 Submit Field Progress Update
    def progress_rules(self, update_type, incident, assignment):
        state = assignment["status"]
        if update_type == "ArrivedOnScene" and state == "Assigned":
            return {"assignment": "Responding", "responder": "Responding", "incident": {"status": "Responding"} if incident["status"] == "Assigned" else {}}
        if update_type == "SituationUpdate" and state == "Responding":
            return {"incident": {}}
        if update_type == "Contained" and state == "Responding" and not incident.get("contained"):
            return {"incident": {"contained": True, "contained_at": self.clock().isoformat()}}
        if update_type == "AssignmentCompleted" and state == "Responding":
            return {"assignment": "Complete", "responder": "Available", "incident": {}}
        raise AppError(f"'{UPDATE_TYPES[update_type]}' does not match the current state (assignment {state}).", "CONFLICT")

    async def progress(self, user, data):
        row = await self.open_incident(data.get("id"))
        assignment = await self.active_assignment(user, row["id"])
        update_type = choice(data.get("update_type"), list(UPDATE_TYPES), "Update type")
        note = text(data.get("note"), "Note", 5, 1000)
        rules = self.progress_rules(update_type, row, assignment)
        if data.get("preview"):
            return {"id": row["id"], "version": row["version"], "update": UPDATE_TYPES[update_type], "note": note,
                    "assignment_after": rules.get("assignment", assignment["status"]), "incident_after": row | rules["incident"]}
        changes = self.write("incidents", row | rules["incident"], await self.version_of(data))
        if "assignment" in rules:
            changes += self.write("assignments", assignment | {"status": rules["assignment"]}, assignment["version"])
        if "responder" in rules:
            responder = await self.record("responders", user["username"])
            busy = any(a["responder"] == user["username"] and a["id"] != assignment["id"] and a["status"] in ACTIVE_ASSIGNMENT for a in await self.records("assignments"))
            if not (busy and rules["responder"] == "Available"):
                changes += self.write("responders", responder | {"status": rules["responder"]}, responder["version"])
        await self.commit(user, "UC-6 FIELD_PROGRESS", row["id"], {"update": UPDATE_TYPES[update_type], "note": note}, changes,
                          [("Dispatcher", f"{row['id']} field update from {user['username']}: {UPDATE_TYPES[update_type]}. {note[:200]}")], "IncidentHistoryEntry")
        return await self.record("incidents", row["id"])

    # UC-7 Escalate Incident
    async def escalate(self, user, data):
        row = await self.open_incident(data.get("id"))
        await self.active_assignment(user, row["id"])
        severity = integer(data.get("severity"), "Severity", 1, 5)
        priority = choice(data.get("priority"), PRIORITIES, "Priority")
        note = text(data.get("field_note"), "Field conditions", 10, 1000)
        old_priority = row.get("priority", "Medium")
        if severity < row["severity"] or PRIORITIES.index(priority) < PRIORITIES.index(old_priority):
            raise AppError("Escalation cannot lower severity or priority.")
        if severity == row["severity"] and priority == old_priority:
            raise AppError("Escalation must raise the severity or the priority.")
        restrictions = [r["name"] for r in await self.active_restrictions() if contains(r["boundary"], row["location"])]
        summary = {"id": row["id"], "severity": f"{row['severity']} -> {severity}", "priority": f"{old_priority} -> {priority}",
                   "field_conditions": note, "nearby_restrictions": restrictions or ["None"], "version": row["version"]}
        if data.get("preview"):
            return summary
        await self.commit(user, "UC-7 INCIDENT_ESCALATED", row["id"], summary,
                          self.write("incidents", row | {"severity": severity, "priority": priority, "escalated": True}, await self.version_of(data)),
                          [("Dispatcher", f"URGENT escalation {row['id']}: severity {severity}, priority {priority}. {note[:200]}")], "IncidentHistoryEntry")
        return await self.record("incidents", row["id"])

    # UC-8 Request Additional Resource
    async def resource_request(self, user, data):
        row = await self.open_incident(data.get("id"))
        await self.active_assignment(user, row["id"])
        urgency = choice(data.get("urgency"), URGENCIES, "Urgency")
        request = {"id": new_id("RQ"), "incident_id": row["id"], "resource_type": choice(data.get("resource_type"), RESOURCE_CATEGORIES, "Resource type"),
                   "quantity": integer(data.get("quantity"), "Quantity", 1, 10000), "urgency": urgency,
                   "delivery_location": text(data.get("delivery_location"), "Delivery location", 3, 200),
                   "justification": text(data.get("justification"), "Justification", 10, 1000),
                   "status": "Pending", "fulfilled": 0, "requested_by": user["username"], "created_at": self.clock().isoformat(),
                   "priority_score": round(60*(URGENCIES.index(urgency)+1)/4 + 40*row["severity"]/5)}
        restrictions = [{"name": r["name"], "instructions": r["instructions"]} for r in await self.active_restrictions() if contains(r["boundary"], row["location"])]
        if data.get("preview"):
            return {"request": request, "transportation_restrictions": restrictions}
        await self.commit(user, "UC-8 RESOURCE_REQUESTED", row["id"], request, self.write("requests", request),
                          [("Resource Coordinator", f"New request {request['id']} for {row['id']}: {request['quantity']} {request['resource_type']} ({urgency}).")], "IncidentHistoryEntry")
        return await self.record("requests", request["id"])

    # UC-9 Allocate Resource
    def request_view(self, request, incidents):
        incident = incidents.get(request["incident_id"], {})
        fulfilled = request.get("fulfilled", 0)
        score = request.get("priority_score") or round(60*2/4 + 40*incident.get("severity", 3)/5)
        return request | {"urgency": request.get("urgency", "Medium"), "fulfilled": fulfilled, "remaining": request["quantity"]-fulfilled,
                          "priority_score": score, "incident_status": incident.get("status", "Unknown")}

    async def request_list(self, user, data):
        incidents = {i["id"]: i for i in await self.records("incidents")}
        rows = [self.request_view(r, incidents) for r in await self.records("requests")]
        if data.get("status", "Open") == "Open":
            rows = [r for r in rows if r["status"] in OPEN_REQUEST]
        return sorted(rows, key=lambda r: (-r["priority_score"], r["created_at"]))

    async def request_options(self, user, data):
        incidents = {i["id"]: i for i in await self.records("incidents")}
        request = self.request_view(await self.record("requests", text(data.get("id"), "Request", 2, 40)), incidents)
        resources = [resource_view(r) for r in await self.records("resources")]
        return {"request": request, "resources": [r for r in resources if r["category"] == request["resource_type"] and r["status"] == "Available" and r["available"] > 0]}

    async def allocate(self, user, data):
        incidents = {i["id"]: i for i in await self.records("incidents")}
        request = self.request_view(await self.record("requests", text(data.get("id"), "Request", 2, 40)), incidents)
        if request["status"] not in OPEN_REQUEST:
            raise AppError(f"Request is already {request['status']}.", "CONFLICT")
        if request["incident_status"] in {"Resolved", "Closed"}:
            raise AppError(f"{request['incident_id']} is {request['incident_status']}. Nothing was allocated.", "CONFLICT")
        resource = resource_view(await self.record("resources", text(data.get("resource_id"), "Resource", 2, 40)))
        quantity = integer(data.get("quantity"), "Quantity", 1, 10000)
        if resource["category"] != request["resource_type"]:
            raise AppError(f"{resource['id']} ({resource['category']}) does not match the requested type {request['resource_type']}.")
        if resource["status"] != "Available":
            raise AppError(f"{resource['id']} is Out of Service.")
        if quantity > resource["available"]:
            raise AppError(f"Only {resource['available']} available in {resource['id']}.", "CONFLICT")
        if quantity > request["remaining"]:
            raise AppError(f"Only {request['remaining']} still needed for {request['id']}.")
        fulfilled = request["fulfilled"]+quantity
        status = "Approved" if fulfilled >= request["quantity"] else "Partially Fulfilled"
        after = {"available": resource["available"]-quantity, "allocated": resource["allocated"]+quantity}
        if data.get("preview"):
            return {"request": request["id"], "resource": resource["id"], "quantity": quantity, "request_status_after": status,
                    "inventory_after": after | {"total": resource["total"]}, "request_version": request["version"], "resource_version": resource["version"]}
        stored = await self.record("requests", request["id"])
        changes = self.write("resources", await self.record("resources", resource["id"]) | after, integer(data.get("resource_version"), "Resource version", 1))
        changes += self.write("requests", stored | {"fulfilled": fulfilled, "status": status, "decided_by": user["username"]}, integer(data.get("request_version"), "Request version", 1))
        changes += self.write("allocations", {"id": new_id("AL"), "incident_id": request["incident_id"], "resource_id": resource["id"], "request_id": request["id"],
                                              "quantity": quantity, "status": "Active", "created_at": self.clock().isoformat()})
        alerts = [(request["requested_by"], f"{request['id']}: {status}. {quantity} {request['resource_type']} from {resource['id']}.")] if request.get("requested_by") else []
        await self.commit(user, "UC-9 RESOURCE_ALLOCATED", request["incident_id"], {"request": request["id"], "resource": resource["id"], "quantity": quantity, "status": status},
                          changes, alerts, "IncidentHistoryEntry")
        return await self.record("requests", request["id"])

    async def request_decision(self, user, data):
        request = await self.record("requests", text(data.get("id"), "Request", 2, 40))
        if request["status"] not in OPEN_REQUEST:
            raise AppError(f"Request is already {request['status']}.", "CONFLICT")
        action = choice(data.get("action"), ["wait", "deny"], "Action")
        reason = text(data.get("reason"), "Reason", 10, 500)
        status = "Waiting" if action == "wait" else "Denied"
        alerts = [(request["requested_by"], f"{request['id']} is {status}: {reason}")] if request.get("requested_by") else []
        await self.commit(user, "UC-9 REQUEST_"+status.upper(), request["incident_id"], {"request": request["id"], "reason": reason},
                          self.write("requests", request | {"status": status, "decision_reason": reason, "decided_by": user["username"]}, await self.version_of(data)), alerts, "IncidentHistoryEntry")
        return await self.record("requests", request["id"])

    # UC-10 Manage Resource Inventory
    async def inventory(self, user, data):
        return [resource_view(r) for r in await self.records("resources")]

    async def resource_history(self, user, data):
        return await self.rows("SELECT * FROM history WHERE kind='ResourceChangeRecord' AND target=? ORDER BY id", text(data.get("id"), "Resource", 2, 40))

    async def resource_save(self, user, data):
        rid = text(data.get("id"), "Identifier", 2, 40).upper()
        if not RESOURCE_ID.fullmatch(rid):
            raise AppError("Identifier must look like WATER-002 (2-10 capital letters, a hyphen and 3 digits).")
        rows = await self.rows("SELECT * FROM records WHERE kind='resources' AND id=?", rid)
        old = resource_view(json.loads(rows[0]["data"]) | {"id": rid, "version": rows[0]["version"]}) if rows else None
        if data.get("new") and old:
            raise AppError(f"Identifier {rid} already exists.", "CONFLICT")
        if not data.get("new") and not old:
            raise AppError(f"{rid} was not found.", "NOT_FOUND")
        total = integer(data.get("total"), "Total quantity", 0, 1000000)
        allocated, consumed = (old["allocated"], old.get("consumed", 0)) if old else (0, 0)
        row = {"id": rid, "name": text(data.get("name"), "Name", 2, 100), "kind": choice(data.get("kind"), RESOURCE_KINDS, "Kind"),
               "category": choice(data.get("category"), RESOURCE_CATEGORIES, "Category"), "area": text(data.get("area", "Central"), "Operational area", 2, 100),
               "status": choice(data.get("status", "Available"), ["Available", "Out of Service"], "Status"),
               "total": total, "allocated": allocated, "consumed": consumed, "available": total-allocated-consumed}
        if row["available"] < 0:
            raise AppError(f"Total cannot be lower than the {allocated+consumed} units already allocated or consumed.")
        if old and allocated and row["status"] != "Available":
            raise AppError(f"{rid} has {allocated} units allocated to incidents and cannot be set Out of Service.")
        if old and allocated and row["kind"] != old["kind"]:
            raise AppError("Kind cannot change while units are allocated.")
        before = {k: old.get(k) for k in row} if old else None
        changed = {k: {"from": before[k], "to": v} for k, v in row.items() if before[k] != v} if old else {"added": row}
        if old and not changed:
            raise AppError("No changes were made.")
        if data.get("preview"):
            return {"resource": row, "changes": changed}
        await self.commit(user, "UC-10 RESOURCE_SAVED", rid, {"changes": changed}, self.write("resources", row, None if not old else await self.version_of(data)), history_kind="ResourceChangeRecord")
        return resource_view(await self.record("resources", rid))

    # UC-11 Update Shelter Information
    async def shelter_update(self, user, data):
        row = await self.record("shelters", text(data.get("id"), "Shelter", 2, 40))
        if row["manager"] != user["username"]:
            raise AppError("This shelter is not assigned to you.", "FORBIDDEN")
        capacity = integer(data.get("capacity"), "Capacity", 1, 100000)
        if capacity < row["occupancy"]:
            raise AppError(f"Capacity cannot be lower than the current occupancy ({row['occupancy']}).")
        state = choice(data.get("state"), SHELTER_STATES, "Operating status")
        phone = data.get("public_contact")
        if not isinstance(phone, str) or not PHONE.fullmatch(phone.strip()):
            raise AppError("Enter a valid public phone number, for example 312-555-0100.")
        supplies = data.get("supplies", row["supplies"])
        if not isinstance(supplies, dict) or set(supplies) != set(row["supplies"]):
            raise AppError("Supplies must list the shelter's existing supply items.")
        supplies = {k: integer(v, "Supply "+k, 0, 1000000) for k, v in supplies.items()}
        medical = data.get("medical_support")
        if not isinstance(medical, bool):
            raise AppError("Medical support must be true or false.")
        status = state
        if state == "Open":
            status = "Full" if row["occupancy"] >= capacity else "Near Capacity" if row["occupancy"] >= capacity*0.9 else "Open"
        new = {"capacity": capacity, "status": status, "public_contact": phone.strip(), "medical_support": medical,
               "internal_contact": text(data.get("internal_contact"), "Internal contact", 2, 200),
               "accessibility": text(data.get("accessibility"), "Accessibility", 2, 300), "supplies": supplies}
        changes = {k: {"from": row.get(k), "to": v} for k, v in new.items() if row.get(k) != v}
        if not changes:
            raise AppError("No changes were made.")
        alert = status != row["status"] and status in {"Near Capacity", "Full", "Closed", "Temporarily Unavailable"}
        if data.get("preview"):
            return {"id": row["id"], "version": row["version"], "changes": changes,
                    "warning": f"Status becomes {status}. The Emergency Administrator will be alerted." if alert else ""}
        alerts = [("Emergency Administrator", f"{row['id']} {row['name']} is now {status} ({row['occupancy']}/{capacity}).")] if alert else []
        await self.commit(user, "UC-11 SHELTER_UPDATED", row["id"], {"changes": changes}, self.write("shelters", row | new, await self.version_of(data)), alerts, "ShelterHistoryRecord")
        return await self.record("shelters", row["id"])

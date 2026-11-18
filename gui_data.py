"""Small display and form helpers for the desktop interface."""
import json
import math
from datetime import datetime, timezone, timedelta

ROLES = ["Dispatcher", "First Responder", "Shelter Manager", "Resource Coordinator", "Emergency Administrator", "System Administrator"]
SAMPLE_ACCOUNTS = {
    "dispatcher": "Dispatcher — verify citizen reports, update incidents, assign personnel and close resolved incidents.",
    "responder": "First Responder — send progress updates, escalate, request resources and submit resolutions.",
    "shelter": "Shelter Manager — update shelter information and occupancy at your assigned shelters.",
    "resource": "Resource Coordinator — allocate requested resources and manage the inventory.",
    "admin": "Emergency Administrator — manage zones, evacuation orders, notifications and reports.",
    "sysadmin": "System Administrator — manage accounts, audit history and recovery demonstrations.",
}
SEVERITIES = ["1 - Low", "2 - Moderate", "3 - Significant", "4 - High", "5 - Critical"]
INCIDENT_TYPES = ["Flood", "Fire", "Traffic accident", "Medical emergency", "Rescue", "Hazard", "Other"]
QUALIFICATIONS = ["Any", "Firefighting", "EMT", "Paramedic", "Search and Rescue", "Water Rescue", "HazMat", "Structural Engineering", "Police"]
PRIORITIES = ["Low", "Medium", "High", "Urgent"]
URGENCIES = ["Low", "Medium", "High", "Critical"]
REPORT_CLASSES = ["Non-emergency", "Incorrect", "Fraudulent"]
UPDATE_TYPES = {"Arrived on scene": "ArrivedOnScene", "Situation update": "SituationUpdate",
                "Incident contained": "Contained", "Assignment completed": "AssignmentCompleted"}
RESOURCE_CATEGORIES = ["Water", "Food", "Medical supplies", "Rescue boats", "Vehicles", "Generators", "Sandbags", "Blankets", "Other"]
RESOURCE_KINDS = ["Vehicle", "Equipment", "Consumable"]
SHELTER_STATES = ["Open", "Closed", "Temporarily Unavailable", "Planned"]


def pages_for(user):
    if not user:
        return [("citizen", "Report an emergency"), ("report_status", "Report status"), ("shelters", "Shelter search")]
    permissions = set(user["permissions"])
    pages = []
    for permission, page, title in [
        ("dashboard", "overview", "Overview"), ("citizen_reports", "citizen_reports", "Citizen reports"),
        ("incidents", "incidents", "Incidents"), ("requests", "requests", "Resource requests"), ("inventory", "inventory", "Inventory"),
        ("shelter_private", "shelters", "My shelters" if "occupancy" in permissions else "Shelter search"),
        ("zones", "areas", "Emergency areas"), ("notices", "notices", "Notifications"),
        ("reports", "reports", "Reports"), ("users", "accounts", "User accounts"), ("audit", "audit", "Audit history"),
    ]:
        if permission in permissions:
            pages.append((page, title))
    if not any(page == "shelters" for page, _ in pages):
        pages.append(("shelters", "Shelter search"))
    pages.append(("alerts", "My alerts"))
    return pages


def first_page(user):
    permissions = set(user["permissions"])
    for permission, page in [("incident_add", "incidents"), ("resolve", "incidents"), ("requests", "requests"), ("occupancy", "shelters"), ("users", "accounts"), ("dashboard", "overview")]:
        if permission in permissions:
            return page
    return pages_for(user)[0][0]


def optional_coordinates(latitude, longitude):
    if not latitude.strip() and not longitude.strip():
        return None
    return coordinates(latitude, longitude)


def date_label(value):
    if not value:
        return "Not recorded"
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc).strftime("%d %b %Y, %H:%M UTC")
    except ValueError:
        return str(value)


def form_time(value=None, hours=0):
    if value:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")
    return (datetime.now(timezone.utc)+timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M")


def api_time(value):
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc).isoformat()
    except ValueError:
        raise ValueError("Use YYYY-MM-DD HH:MM for UTC dates, for example 2026-11-10 18:30.") from None


def coordinates(latitude, longitude):
    try:
        lat, lon = float(latitude), float(longitude)
    except ValueError:
        raise ValueError("Latitude and longitude must be numbers.") from None
    if not math.isfinite(lat) or not -90 <= lat <= 90 or not math.isfinite(lon) or not -180 <= lon <= 180:
        raise ValueError("Latitude must be -90 to 90; longitude must be -180 to 180.")
    return [lat, lon]


def geometry(values):
    south, west = coordinates(values["south"], values["west"])
    north, east = coordinates(values["north"], values["east"])
    if south >= north or west >= east:
        raise ValueError("South must be below North; West must be below East.")
    return [south, west, north, east]


def severity(value):
    try:
        result = int(str(value).split(" - ")[0])
    except ValueError:
        raise ValueError("Choose an incident severity from 1 to 5.") from None
    if result not in range(1, 6):
        raise ValueError("Choose an incident severity from 1 to 5.")
    return result


def severity_label(value):
    return SEVERITIES[int(value)-1] if str(value).isdigit() and int(value) in range(1, 6) else str(value)


def human_text(value, indent=0):
    """Readable detail text, including nested report and audit data."""
    pad = " "*indent
    if isinstance(value, dict):
        lines = []
        for key, item in value.items():
            label = key.replace("_", " ").capitalize()
            if key == "details" and isinstance(item, str):
                try:
                    item = json.loads(item)
                except ValueError:
                    pass
            if isinstance(item, (list, dict)):
                lines += [pad+label, human_text(item, indent+2)]
            else:
                lines.append(pad+label+": "+("Not available" if item is None else str(item)))
        return "\n".join(lines)
    if isinstance(value, list):
        if not value:
            return pad+"None"
        if all(not isinstance(item, (dict, list)) for item in value):
            return pad+", ".join(map(str, value))
        return "\n\n".join(human_text(item, indent) for item in value)
    return pad+str(value)


def field(key, label, value="", kind="entry", choices=None, required=True, hint=""):
    return {"key": key, "label": label, "value": value, "kind": kind, "choices": choices or [], "required": required, "hint": hint}

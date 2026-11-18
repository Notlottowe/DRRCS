"""Prepare private deployment files. Seed inserts do not overwrite existing rows."""
import hashlib
import json
import secrets
import os
from pathlib import Path
from datetime import datetime, timezone, timedelta
from backend.passwords import ITERATIONS, finish_hash

ROOT = Path(__file__).resolve().parent
OWNER = Path(os.environ.get("DRRCS_OWNER_DIR", str(ROOT.parent/"DRRCS-owner-private"))).expanduser()
DEMO_USERS = {"admin": "Emergency Administrator", "sysadmin": "System Administrator", "dispatcher": "Dispatcher",
              "responder": "First Responder", "resource": "Resource Coordinator", "shelter": "Shelter Manager"}


def quote(value):
    if isinstance(value, (int, float)):
        return str(value)
    return "'"+str(value).replace("'", "''")+"'"


def main():
    private = OWNER
    private.mkdir(mode=0o700, exist_ok=True)
    access_file = private/"access.json"
    if access_file.exists():
        access = json.loads(access_file.read_text())
    else:
        access = {"project_key": secrets.token_urlsafe(32), "password": "Drrcs-"+secrets.token_urlsafe(12)+"7!"}
        access_file.write_text(json.dumps(access, indent=2))
        access_file.chmod(0o600)
    if "password_pepper" not in access:
        access["password_pepper"] = secrets.token_urlsafe(32)
        access_file.write_text(json.dumps(access, indent=2))
        access_file.chmod(0o600)
    sql = []
    for username, role in DEMO_USERS.items():
        salt = secrets.token_hex(16)
        derived = hashlib.pbkdf2_hmac("sha256", access["password"].encode(), bytes.fromhex(salt), ITERATIONS)
        hashed = finish_hash(derived, access["password_pepper"])
        values = [username, username.title(), "County Emergency Agency", json.dumps([role]), "Active", salt, hashed, 1]
        sql.append("INSERT OR IGNORE INTO users VALUES ("+",".join(map(quote, values))+");")
    clock = datetime.now(timezone.utc)
    samples = json.loads((ROOT/'backend/sample_data.json').read_text())
    for kind, records in samples.items():
        for row in records:
            rid = row.pop("id")
            if "created_at" in row:
                row["created_at"] = (clock-timedelta(hours=3)).isoformat()
            if row.get("responded_at"):
                row["responded_at"] = (clock-timedelta(hours=2, minutes=45)).isoformat()
            if row.get("resolution"):
                row["resolution"]["completion"] = (clock-timedelta(minutes=10)).isoformat()
            if kind in {"zones", "restrictions"}:
                row["start"] = (clock-timedelta(days=1)).isoformat()
                row["end"] = (clock+timedelta(days=365)).isoformat()
            values = [kind, rid, json.dumps(row, ensure_ascii=False), 1]
            sql.append("INSERT OR IGNORE INTO records VALUES ("+",".join(map(quote, values))+");")
    (private/"seed.sql").write_text("\n".join(sql)+"\n")
    (private/"seed.sql").chmod(0o600)
    (private/"secrets.json").write_text(json.dumps({"APP_KEY": access["project_key"], "PASSWORD_PEPPER": access["password_pepper"]}))
    (private/"secrets.json").chmod(0o600)
    (ROOT/"backend/.dev.vars").write_text('APP_KEY="'+access["project_key"]+'"\nPASSWORD_PEPPER="'+access["password_pepper"]+'"\n')
    (ROOT/"backend/.dev.vars").chmod(0o600)
    credentials = "DRRCS classroom cloud accounts\n\n"+"\n".join(f"{u:12} {r}" for u,r in DEMO_USERS.items())+"\n\nPassword for these sample accounts: "+access["password"]+"\n\nKeep this file private. The source package does not include it.\n"
    (OWNER/"Cloud Login.txt").write_text(credentials)
    (OWNER/"Cloud Login.txt").chmod(0o600)
    file = private/"local-client.json"
    file.write_text(json.dumps({"api_url": "http://127.0.0.1:8797", "project_key": access["project_key"]}, indent=2))
    file.chmod(0o600)
    print("Private seed and login files prepared; no credentials printed.")


if __name__ == "__main__":
    main()

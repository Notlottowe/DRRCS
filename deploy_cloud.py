"""Deploy this project's Worker and dedicated D1 database, then check real HTTP requests."""
import argparse
import json
import re
import shutil
import subprocess
import sys
import time
import tomllib
from pathlib import Path
from urllib.request import urlopen
from verify_cloud import verify
from prepare_cloud import OWNER
from cloud_api import CloudAPI

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--wrangler", default=shutil.which("wrangler"))
    parser.add_argument("--local-only", action="store_true")
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--publish-only", action="store_true")
    parser.add_argument("--pages-gateway", action="store_true")
    args = parser.parse_args()
    if tomllib.loads((ROOT/"backend/wrangler.toml").read_text()).get("routes"):
        raise RuntimeError("Custom domains are disabled for this project.")
    if not args.wrangler:
        parser.error("Provide --wrangler /path/to/wrangler")
    if not (OWNER/"access.json").is_file():
        raise RuntimeError("Owner configuration is required to update the existing backend. Set DRRCS_OWNER_DIR to its location.")
    def run(*parts, cwd=None):
        print("Cloudflare: "+" ".join(parts[:5]), flush=True)
        result = subprocess.run([args.wrangler, *parts], cwd=cwd or ROOT/"backend", capture_output=True, text=True)
        if result.returncode:
            log = OWNER/"deployment-error.log"
            log.write_text(result.stdout+"\n"+result.stderr)
            log.chmod(0o600)
            raise RuntimeError("Cloudflare command failed. See the private deployment-error.log; existing data was not reset.")
        return result.stdout

    def readback(result):
        rid = result["incident_id"]
        if not re.fullmatch(r"IN-[A-F0-9]+", rid):
            raise RuntimeError("Unexpected verification incident ID.")
        sql = "SELECT id,json_extract(data,'$.description') AS description FROM records WHERE kind='incidents' AND id='"+rid+"'"
        output = run("d1", "execute", "drrcs-phase4-data", "--remote", "--command", sql, "--json")
        rows = json.loads(output)[0]["results"]
        if not rows or rows[0]["id"] != rid:
            raise RuntimeError("Direct D1 readback did not find the saved incident.")
        result["direct_remote_d1_readback"] = rows[0]
        (ROOT/"test_results/cloud_verification.json").write_text(json.dumps(result, indent=2))
        print("PASS Direct Cloudflare D1 readback: "+rid, flush=True)

    if args.verify_only:
        readback(verify(ROOT/".private/client.json", ROOT/"test_results/cloud_verification.json"))
        return

    subprocess.run([sys.executable, "prepare_cloud.py"], cwd=ROOT, check=True)

    def cloud_url(deployed):
        settings = tomllib.loads((ROOT/"backend/wrangler.toml").read_text())
        if settings.get("routes"):
            raise RuntimeError("This project uses Cloudflare-provided addresses only. Custom domains are disabled.")
        matches = re.findall(r"https://[a-zA-Z0-9.-]+\.workers\.dev", deployed)
        if not matches:
            raise RuntimeError("Deployment did not return a workers.dev URL.")
        return matches[-1]

    def configure_client(url):
        access = json.loads((OWNER/"access.json").read_text())
        config = ROOT/".private/client.json"
        config.parent.mkdir(mode=0o700, exist_ok=True)
        config.write_text(json.dumps({"api_url": url, "project_key": access["project_key"]}, indent=2))
        config.chmod(0o600)
        return config

    def wait_and_verify(config):
        for attempt in range(30):
            try:
                CloudAPI(json.loads(config.read_text())).call("shelters")
                break
            except Exception:
                if attempt == 29:
                    raise
                time.sleep(3)
        result = verify(config, ROOT/"test_results/cloud_verification.json")
        readback(result)
        return result

    def publish_gateway():
        folder = ROOT/"gateway"
        settings = tomllib.loads((folder/"wrangler.toml").read_text())
        name = settings["name"]
        projects = json.loads(run("pages", "project", "list", "--json", cwd=folder))
        if not any(name in p.values() for p in projects):
            # --force selects the direct Pages command instead of the newer Workers delegation.
            run("pages", "project", "create", name, "--production-branch", "main", "--force", cwd=folder)
        run("pages", "deploy", "public", "--project-name", name, "--branch", "main", "--commit-dirty=true", "--force", cwd=folder)
        return "https://"+name+".pages.dev"

    if args.pages_gateway:
        run("deploy")
        url = publish_gateway()
        result = wait_and_verify(configure_client(url))
        print("Cloudflare-owned API entry verified: "+url, flush=True)
        return

    if args.publish_only:
        deployed = run("deploy")
        url = cloud_url(deployed)
        result = wait_and_verify(configure_client(url))
        print("Cloud backend deployed and verified: "+url, flush=True)
        return

    run("d1", "execute", "drrcs-phase4-data", "--local", "--file", "schema.sql", "--json")
    run("d1", "execute", "drrcs-phase4-data", "--local", "--file", str(OWNER/"seed.sql"), "--json")
    process = None
    try:
        try:
            urlopen("http://127.0.0.1:8797/health", timeout=3).close()
        except Exception:
            log = open(OWNER/"local-worker.log", "w")
            process = subprocess.Popen([args.wrangler, "dev", "--port", "8797", "--ip", "127.0.0.1", "--show-interactive-dev-session", "false"], cwd=ROOT/"backend", stdout=log, stderr=log)
            for _ in range(60):
                try:
                    urlopen("http://127.0.0.1:8797/health", timeout=2).close()
                    break
                except Exception:
                    if process.poll() is not None:
                        raise RuntimeError("Local Worker failed. See the private local-worker.log")
                    time.sleep(1)
            else:
                raise RuntimeError("Local Worker did not start.")
        verify(OWNER/"local-client.json", ROOT/"test_results/local_worker_verification.json")
        if args.local_only:
            return
        run("d1", "execute", "drrcs-phase4-data", "--remote", "--file", "schema.sql", "--json")
        run("d1", "execute", "drrcs-phase4-data", "--remote", "--file", str(OWNER/"seed.sql"), "--json")
        deployed = run("deploy")
        url = cloud_url(deployed)
        run("secret", "bulk", str(OWNER/"secrets.json"))
        config = configure_client(url)
        if (ROOT/"gateway/wrangler.toml").exists():
            url = publish_gateway()
            config = configure_client(url)
        # A newly uploaded secret can take a short time to reach every edge location.
        result = wait_and_verify(config)
        print("Cloud backend deployed and verified: "+url, flush=True)
        print("Persistent verification incident: "+result["incident_id"], flush=True)
    finally:
        if process:
            process.terminate()
            process.wait(timeout=10)


if __name__ == "__main__":
    main()

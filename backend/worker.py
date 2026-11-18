"""Cloudflare entry point. The client does not store business records."""
import json
import hmac
from urllib.parse import urlparse
from workers import WorkerEntrypoint, Response
from js import crypto, TextEncoder, Uint8Array, Object, JSON
from pyodide.ffi import to_js
from app import Backend
from checks import AppError
from passwords import ITERATIONS, finish_hash
from functools import partial


async def hash_password(password, salt, pepper):
    if not pepper:
        raise AppError("Password service is not configured.", "CONFIGURATION")
    key = await crypto.subtle.importKey("raw", TextEncoder.new().encode(password), "PBKDF2", False, to_js(["deriveBits"]))
    options = to_js({"name": "PBKDF2", "salt": Uint8Array.new(to_js(list(bytes.fromhex(salt)))),
                     "iterations": ITERATIONS, "hash": "SHA-256"}, dict_converter=Object.fromEntries)
    result = await crypto.subtle.deriveBits(options, key, 256)
    return finish_hash(bytes(Uint8Array.new(result).to_py()), pepper)


class D1:
    def __init__(self, binding):
        self.binding = binding

    def statement(self, sql, values):
        return self.binding.prepare(sql).bind(*values) if values else self.binding.prepare(sql)

    async def query(self, sql, values=()):
        result = await self.statement(sql, values).all()
        return json.loads(JSON.stringify(result.results))

    async def batch(self, statements):
        try:
            result = await self.binding.batch(to_js([self.statement(sql, args) for sql, args in statements]))
            return json.loads(JSON.stringify(result))
        except Exception as error:
            message = str(error)
            if "CHECK constraint failed" in message or "UNIQUE constraint failed" in message:
                raise AppError("Record changed or ID already exists. Reload and try again.", "CONFLICT") from None
            raise


def reply(data, status=200):
    return Response(json.dumps(data, ensure_ascii=False), status=status,
                    headers={"Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store"})


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        path = urlparse(str(request.url)).path
        if path == "/health" and str(request.method) == "GET":
            return reply({"service": "DRRCS", "storage": "Cloudflare D1", "version": "1"})
        if path != "/api" or str(request.method) != "POST":
            return reply({"error": "NOT_FOUND", "message": "Use the DRRCS application."}, 404)
        key = str(getattr(self.env, "APP_KEY", ""))
        provided = str(request.headers.get("X-Project-Key") or "")
        if not key or not hmac.compare_digest(key, provided):
            return reply({"error": "PROJECT_ACCESS", "message": "Missing or invalid project access key."}, 401)
        try:
            body = str(await request.text())
            if len(body.encode()) > 32768:
                raise AppError("Request is too large.")
            value = json.loads(body)
            if not isinstance(value, dict) or not isinstance(value.get("command"), str):
                raise AppError("Enter a valid command.")
            auth = str(request.headers.get("Authorization") or "")
            token = auth[7:] if auth.startswith("Bearer ") else ""
            derive = partial(hash_password, pepper=str(getattr(self.env, "PASSWORD_PEPPER", "")))
            # Client address is used only to rate-limit public citizen reports (UC-1).
            client = str(request.headers.get("CF-Connecting-IP") or "")
            result = await Backend(D1(self.env.DB), derive).run(value["command"], value.get("data", {}), token, client)
            return reply({"ok": True, "data": result})
        except AppError as error:
            status = {"AUTHENTICATION": 401, "FORBIDDEN": 403, "NOT_FOUND": 404, "CONFLICT": 409,
                      "RATE_LIMITED": 429, "IMMUTABLE": 409, "INCOMPLETE": 409}.get(error.code, 400)
            return reply({"error": error.code, "message": str(error)}, status)
        except (KeyError, TypeError, ValueError):
            return reply({"error": "INVALID_INPUT", "message": "Missing or invalid fields. Check the form and try again."}, 400)
        except Exception as error:
            print("DRRCS backend error:", type(error).__name__, str(error))
            return reply({"error": "SERVER_ERROR", "message": "The cloud operation failed. No success was reported."}, 500)

    async def scheduled(self, controller, env, ctx):
        derive = partial(hash_password, pepper=str(getattr(self.env, "PASSWORD_PEPPER", "")))
        await Backend(D1(self.env.DB), derive).scheduled()

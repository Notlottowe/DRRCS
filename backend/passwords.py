"""Password format shared by setup, Worker and tests."""
import hashlib
import hmac

# Cloudflare production Web Crypto caps one PBKDF2 call at 100,000 iterations.
ITERATIONS = 100000


def finish_hash(derived_bytes, pepper):
    # Pepper stays in the Worker secret, outside D1 and client configuration.
    digest = hmac.new(pepper.encode(), derived_bytes, hashlib.sha256).hexdigest()
    return "p2-100k-hmac$"+digest

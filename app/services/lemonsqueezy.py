import hashlib
import hmac
import json
import urllib.error
import urllib.request

_LS_API = "https://api.lemonsqueezy.com/v1"


def create_checkout(api_key: str, store_id: str, variant_id: str,
                    user_email: str, plan: str, user_id: int,
                    redirect_url: str) -> str:
    payload = {
        "data": {
            "type": "checkouts",
            "attributes": {
                "checkout_data": {
                    "email": user_email,
                    "custom": {
                        "user_id": str(user_id),
                        "plan": plan,
                    },
                },
                "product_options": {
                    "redirect_url": redirect_url,
                },
            },
            "relationships": {
                "store":   {"data": {"type": "stores",   "id": str(store_id)}},
                "variant": {"data": {"type": "variants", "id": str(variant_id)}},
            },
        }
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(f"{_LS_API}/checkouts", data=data, method="POST")
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/vnd.api+json")
    req.add_header("Accept", "application/vnd.api+json")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
            return result["data"]["attributes"]["url"]
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise ValueError(f"LemonSqueezy HTTP {e.code}: {body[:300]}")


def verify_webhook(payload_bytes: bytes, signature: str, secret: str) -> bool:
    digest = hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, signature)

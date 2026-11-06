"""Web Push (RFC 8030/8291/8292) com a biblioteca `cryptography`.

A chave VAPID é derivada do segredo do app, então não muda quando o servidor reinicia
e os aparelhos inscritos continuam recebendo.
"""
import base64
import json
import os
import struct
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from cryptography.hazmat.primitives import hashes, hmac, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from . import config as C

# Serviços de push conhecidos. Evita que o servidor seja usado para chamar endereços arbitrários.
PUSH_HOSTS = ("fcm.googleapis.com", "updates.push.services.mozilla.com", "push.services.mozilla.com",
              "web.push.apple.com", "notify.windows.com", "push.apple.com")
_N = int("FFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551", 16)


def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def unb64u(s):
    s = str(s)
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def vapid_key():
    scalar = int.from_bytes(C.derive("vapid-p256"), "big") % (_N - 1) + 1
    return ec.derive_private_key(scalar, ec.SECP256R1())


def public_raw(key):
    return key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def vapid_public_b64():
    return b64u(public_raw(vapid_key()))


def endpoint_allowed(endpoint):
    try:
        u = urlparse(endpoint)
    except ValueError:
        return False
    if C.PUSH_ALLOW_ANY and u.scheme in ("http", "https"):
        return True
    host = (u.hostname or "").lower()
    return u.scheme == "https" and any(host == h or host.endswith("." + h) for h in PUSH_HOSTS)


def vapid_header(endpoint, key=None):
    key = key or vapid_key()
    u = urlparse(endpoint)
    aud = f"{u.scheme}://{u.netloc}"
    sub = C.PUBLIC_URL if C.PUBLIC_URL.startswith("https://") else "mailto:notificacoes@financas.invalid"
    header = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    claims = b64u(json.dumps({"aud": aud, "exp": int(time.time()) + 12 * 3600, "sub": sub},
                             separators=(",", ":")).encode())
    signing = f"{header}.{claims}".encode()
    r, s = decode_dss_signature(key.sign(signing, ec.ECDSA(hashes.SHA256())))
    sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return f"vapid t={header}.{claims}.{b64u(sig)}, k={b64u(public_raw(key))}"


def _hmac(key, data):
    h = hmac.HMAC(key, hashes.SHA256())
    h.update(data)
    return h.finalize()


def encrypt(payload, p256dh, auth, salt=None, server_key=None):
    """Criptografa no formato aes128gcm (RFC 8291). Devolve o corpo pronto para enviar."""
    ua_public = unb64u(p256dh)
    auth_secret = unb64u(auth)
    if len(ua_public) != 65 or len(auth_secret) < 16:
        raise ValueError("chaves da inscrição inválidas")
    server_key = server_key or ec.generate_private_key(ec.SECP256R1())
    as_public = public_raw(server_key)
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    shared = server_key.exchange(ec.ECDH(), ua_key)
    prk_key = _hmac(auth_secret, shared)
    ikm = _hmac(prk_key, b"WebPush: info\x00" + ua_public + as_public + b"\x01")
    salt = salt or os.urandom(16)
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    record = payload + b"\x02"
    cipher = AESGCM(cek).encrypt(nonce, record, None)
    rs = 4096
    return salt + struct.pack("!IB", rs, len(as_public)) + as_public + cipher


def send(sub, message, ttl=86400, urgency="normal"):
    """Envia para uma inscrição. Devolve 'ok', 'gone' (inscrição morta) ou 'erro: ...'."""
    endpoint = sub["endpoint"]
    if not endpoint_allowed(endpoint):
        return "erro: endereço de push não permitido"
    body = encrypt(json.dumps(message, ensure_ascii=False).encode(), sub["p256dh"], sub["auth"])
    req = urllib.request.Request(endpoint, data=body, method="POST", headers={
        "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream",
        "TTL": str(ttl), "Urgency": urgency, "Authorization": vapid_header(endpoint)})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return "ok" if 200 <= r.status < 300 else f"erro: HTTP {r.status}"
    except urllib.error.HTTPError as e:
        if e.code in (404, 410):
            return "gone"
        return f"erro: HTTP {e.code}"
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return f"erro: {e.__class__.__name__}"

import base64
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("APP_PASSWORD", "senha-de-teste-123")
os.environ["DATA_DIR"] = tempfile.mkdtemp()

from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature  # noqa: E402

from app import config as C  # noqa: E402
from app import db, notify, push  # noqa: E402
from app import validate as V  # noqa: E402

try:
    import http_ece
except ImportError:  # pragma: no cover
    http_ece = None


def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


class Push(unittest.TestCase):
    @unittest.skipIf(http_ece is None, "http_ece não instalado")
    def test_encryption_matches_reference_decoder(self):
        ua = ec.generate_private_key(ec.SECP256R1())
        ua_pub = ua.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        auth = os.urandom(16)
        body = push.encrypt(b'{"title":"Oi"}', b64u(ua_pub), b64u(auth))
        plain = http_ece.decrypt(body, private_key=ua, auth_secret=auth, version="aes128gcm")
        self.assertEqual(plain, b'{"title":"Oi"}')

    def test_vapid_jwt_signature_is_valid(self):
        hdr = push.vapid_header("https://fcm.googleapis.com/fcm/send/abc")
        t = hdr.split("t=")[1].split(",")[0]
        k = hdr.split("k=")[1]
        h, c, s = t.split(".")
        claims = json.loads(push.unb64u(c))
        self.assertEqual(claims["aud"], "https://fcm.googleapis.com")
        pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), push.unb64u(k))
        raw = push.unb64u(s)
        sig = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
        pub.verify(sig, f"{h}.{c}".encode(), ec.ECDSA(hashes.SHA256()))  # levanta erro se inválida
        self.assertEqual(k, push.vapid_public_b64())  # chave estável

    def test_endpoint_allowlist(self):
        self.assertTrue(push.endpoint_allowed("https://fcm.googleapis.com/fcm/send/x"))
        self.assertFalse(push.endpoint_allowed("https://169.254.169.254/latest"))
        self.assertFalse(push.endpoint_allowed("http://fcm.googleapis.com/x"))


class Rules(unittest.TestCase):
    def test_quiet_hours_wrap_midnight(self):
        f = lambda h, m: datetime(2026, 11, 5, h, m, tzinfo=C.TZ)
        self.assertTrue(notify.in_quiet(f(23, 0), "22:30", "07:30"))
        self.assertTrue(notify.in_quiet(f(6, 59), "22:30", "07:30"))
        self.assertFalse(notify.in_quiet(f(12, 0), "22:30", "07:30"))

    def test_due_window(self):
        n = datetime(2026, 11, 5, 21, 5, tzinfo=C.TZ)
        self.assertTrue(notify.due(n, "20:30"))
        self.assertFalse(notify.due(n, "21:30"))
        self.assertFalse(notify.due(n, "17:00"))

    def test_build_with_seed(self):
        db.init()
        now = datetime(2026, 11, 5, 20, 45, tzinfo=C.TZ)
        with db.connect() as conn:
            keys = {k.split(":")[0] for k, *_ in notify.build(conn, now)}
            self.assertIn("daily", keys)      # lembrete das 20:30
            self.assertNotIn("teto", keys)    # 29,48 de 50 = 59%, abaixo de 80%
            conn.execute("INSERT INTO spends(date,description,amount) VALUES('2026-11-05','Teste',12)")
            teto = [o for o in notify.build(conn, now) if o[0].startswith("teto")]
            self.assertEqual(teto[0][0], "teto:2026-11-02:80")   # 41,48 de 50 = 83%
            conn.execute("INSERT INTO spends(date,description,amount) VALUES('2026-11-05','Teste',20)")
            teto = [o for o in notify.build(conn, now) if o[0].startswith("teto")]
            self.assertEqual(teto[0][0], "teto:2026-11-02:100")
            self.assertIn("teto:2026-11-02:80", teto[0][4])     # marca o 80% junto, sem aviso duplo
            conn.rollback()

    def test_notif_validation(self):
        v = V.notif({"enabled": "true", "rules": {"teto_alert": {"pcts": "80, 100"}, "daily_log": {"time": "20:30"}}})
        self.assertEqual(v["rules"]["teto_alert"]["pcts"], [80, 100])
        with self.assertRaises(ValueError):
            V.notif({"rules": {"daily_log": {"time": "25:00"}}})


if __name__ == "__main__":
    unittest.main()

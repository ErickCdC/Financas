"""Configuração lida do ambiente. Nada de segredo fica no código."""
import hashlib
import hmac
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
DATA_DIR = Path(os.environ.get("DATA_DIR", ROOT / "data"))
PASSWORD = os.environ.get("APP_PASSWORD", "")
PORT = int(os.environ.get("PORT", "8080"))
HOST = os.environ.get("HOST", "0.0.0.0")
TZ = timezone(timedelta(hours=float(os.environ.get("TZ_OFFSET_HOURS", "-3"))))

# Banco MCP (opcional)
BANCO_KEY = os.environ.get("BANCO_MCP_KEY", "")
BANCO_BASE = os.environ.get("BANCO_MCP_BASE", "https://api.mcp.ai/api/openfinance").rstrip("/")

# Backup automático no GitHub (opcional, recomendado no Render grátis)
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "")
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "dados")
GITHUB_PATH = os.environ.get("GITHUB_PATH", "financas-backup.json")
GITHUB_API = os.environ.get("GITHUB_API", "https://api.github.com").rstrip("/")

# Agendador externo (GitHub Actions chama /api/cron)
CRON_TOKEN = os.environ.get("CRON_TOKEN", "")
PUBLIC_URL = (os.environ.get("PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "").rstrip("/")
PUSH_ALLOW_ANY = os.environ.get("PUSH_ALLOW_ANY_HOST") == "1"  # só para testes locais

SESSION_SECONDS = 30 * 86400


def _master_secret():
    s = os.environ.get("APP_SECRET", "")
    if s:
        return s.encode()
    # Sem APP_SECRET, deriva da senha: sessões e chaves de notificação continuam válidas
    # entre reinícios, e trocar a senha derruba todas as sessões.
    return hashlib.sha256(b"financas-v2:" + PASSWORD.encode()).digest()


def derive(label):
    return hmac.new(_master_secret(), label.encode(), hashlib.sha256).digest()


def today():
    if os.environ.get("FAKE_NOW"):
        return now().date()
    f = os.environ.get("FAKE_TODAY")
    return date.fromisoformat(f) if f else datetime.now(TZ).date()


def now():
    f = os.environ.get("FAKE_NOW")
    if f:
        return datetime.fromisoformat(f).replace(tzinfo=TZ)
    f = os.environ.get("FAKE_TODAY")
    if f:
        return datetime.combine(date.fromisoformat(f), datetime.now(TZ).time()).replace(tzinfo=TZ)
    return datetime.now(TZ)


def log(msg):
    sys.stderr.write(msg.rstrip() + "\n")
    sys.stderr.flush()

"""Validação de tudo que chega pela API. Qualquer erro vira HTTP 400 com mensagem clara."""
import math
import re
from datetime import date


def num(v):
    if isinstance(v, bool):
        raise ValueError("número inválido")
    try:
        f = float(str(v).replace(",", ".")) if isinstance(v, str) else float(v)
    except (TypeError, ValueError):
        raise ValueError("número inválido")
    if not math.isfinite(f) or abs(f) > 1e9:
        raise ValueError("número inválido")
    return round(f, 2)


def text(maxlen=80, required=True):
    def fn(v):
        s = str(v).strip() if v is not None else ""
        if required and not s:
            raise ValueError("texto vazio")
        if len(s) > maxlen:
            raise ValueError("texto muito longo")
        return s or None
    return fn


def posint(lo, hi):
    def fn(v):
        if isinstance(v, bool):
            raise ValueError("inteiro inválido")
        try:
            i = int(float(v))
        except (TypeError, ValueError):
            raise ValueError("inteiro inválido")
        if not lo <= i <= hi:
            raise ValueError(f"valor fora do limite ({lo} a {hi})")
        return i
    return fn


def opt(fn):
    return lambda v: None if v in (None, "") else fn(v)


def boolint(v):
    return 1 if v in (True, 1, "1", "true", "on") else 0


def boolean(v):
    return bool(boolint(v))


def iso_date(v):
    try:
        return date.fromisoformat(str(v)).isoformat()
    except ValueError:
        raise ValueError("data inválida")


def year_month(v):
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", str(v)):
        raise ValueError("mês inválido (use AAAA-MM)")
    return str(v)


def hhmm(v):
    m = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", str(v))
    if not m:
        raise ValueError("horário inválido (use HH:MM)")
    return str(v)


def str_list(v):
    if isinstance(v, str):
        v = re.split(r"[,\n]", v)
    if not isinstance(v, list) or len(v) > 100:
        raise ValueError("lista inválida")
    return [str(x).strip().lower()[:60] for x in v if str(x).strip()]


def money_dict(v):
    if not isinstance(v, dict) or len(v) > 20:
        raise ValueError("valores inválidos")
    return {str(k)[:30]: num(x) for k, x in v.items()}


def day_dict(v):
    if not isinstance(v, dict) or len(v) > 20:
        raise ValueError("dias inválidos")
    return {str(k)[:30]: posint(1, 31)(x) for k, x in v.items() if x not in (None, "")}


def card_list(v):
    if not isinstance(v, list) or not 1 <= len(v) <= 10:
        raise ValueError("lista de cartões inválida")
    out = []
    for x in v:
        s = text(30)(x)
        if s not in out:
            out.append(s)
    return out


def pct_list(v):
    if isinstance(v, str):
        v = [x for x in re.split(r"[,\s]+", v) if x]
    if not isinstance(v, list) or len(v) > 4:
        raise ValueError("percentuais inválidos (até 4)")
    return sorted({posint(10, 200)(i) for i in v})


def notif(v):
    """Preferências de notificação. Só aceita chaves conhecidas."""
    if not isinstance(v, dict):
        raise ValueError("preferências inválidas")
    out = {}
    if "enabled" in v:
        out["enabled"] = boolean(v["enabled"])
    for k in ("quiet_start", "quiet_end"):
        if k in v:
            out[k] = hhmm(v[k])
    rules = v.get("rules") or {}
    if not isinstance(rules, dict):
        raise ValueError("regras inválidas")
    spec = {
        "daily_log": {"on": boolean, "time": hhmm},
        "teto_alert": {"on": boolean, "pcts": pct_list},
        "bills_due": {"on": boolean, "time": hhmm, "days_before": posint(0, 10)},
        "weekly_summary": {"on": boolean, "time": hhmm, "weekday": posint(0, 6)},
        "payday": {"on": boolean, "time": hhmm},
        "auto_sync": {"on": boolean, "time": hhmm},
        "bank_events": {"on": boolean},
    }
    out_rules = {}
    for name, fields in spec.items():
        r = rules.get(name)
        if r is None:
            continue
        if not isinstance(r, dict):
            raise ValueError("regra inválida: " + name)
        cleaned = {f: fn(r[f]) for f, fn in fields.items() if f in r}
        out_rules[name] = cleaned
    if out_rules:
        out["rules"] = out_rules
    return out


def _flags(keys):
    def fn(v):
        if not isinstance(v, dict):
            raise ValueError("opções inválidas")
        return {k: boolean(v[k]) for k in keys if k in v}
    return fn


auto = _flags(("caixa", "salario", "pagamentos", "faturas", "parcelas"))


def sync_auto(v):
    if not isinstance(v, dict):
        raise ValueError("rotina inválida")
    out = {}
    for k in ("on", "forcar"):
        if k in v:
            out[k] = boolean(v[k])
    for k in ("inicio", "fim"):
        if k in v:
            out[k] = hhmm(v[k])
    if "intervalo" in v:
        out["intervalo"] = posint(15, 240)(v["intervalo"])
    return out


def salario_regra(v):
    if not isinstance(v, dict):
        raise ValueError("regra inválida")
    out = {}
    if "minimo_pct" in v:
        out["minimo_pct"] = posint(10, 150)(v["minimo_pct"])
    if "termos" in v:
        out["termos"] = str_list(v["termos"])
    return out

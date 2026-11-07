"""Dia do salário: último dia útil do mês (padrão) ou um dia fixo.

Dia útil = segunda a sexta, fora feriados nacionais e dias sem expediente bancário
(Carnaval, Sexta-feira Santa, Corpus Christi e 31/12).
"""
import calendar
from datetime import date, timedelta
from functools import lru_cache

FIXOS = ((1, 1), (4, 21), (5, 1), (9, 7), (10, 12), (11, 2), (11, 15), (11, 20), (12, 25), (12, 31))


def easter(y):
    """Domingo de Páscoa (algoritmo de Meeus/Jones/Butcher)."""
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    month = (h + l_ - 7 * m + 114) // 31
    day = ((h + l_ - 7 * m + 114) % 31) + 1
    return date(y, month, day)


@lru_cache(maxsize=64)
def holidays(y):
    e = easter(y)
    moveis = {e - timedelta(days=48), e - timedelta(days=47), e - timedelta(days=2), e + timedelta(days=60)}
    return frozenset({date(y, m, d) for m, d in FIXOS} | moveis)


def is_business(d):
    return d.weekday() < 5 and d not in holidays(d.year)


def _mode(cfg):
    if isinstance(cfg, int):
        return "dia", cfg
    return (cfg.get("salario_tipo") or "util"), int(cfg.get("salario_dia") or 30)


def payday_of(y, m, cfg):
    tipo, dia = _mode(cfg)
    last = calendar.monthrange(y, m)[1]
    if tipo == "dia":
        return date(y, m, min(dia, last))
    d = date(y, m, last)
    while not is_business(d):
        d -= timedelta(days=1)
    return d


def payday_ym(ym, cfg):
    y, m = map(int, ym.split("-"))
    return payday_of(y, m, cfg)


def _shift(y, m, k):
    t = y * 12 + (m - 1) + k
    return t // 12, t % 12 + 1


def last_payday(t, cfg):
    """Último salário em ou antes de `t`."""
    for k in (0, -1, -2):
        y, m = _shift(t.year, t.month, k)
        p = payday_of(y, m, cfg)
        if p <= t:
            return p
    return payday_of(*_shift(t.year, t.month, -2), cfg)


def next_payday(t, cfg):
    """Próximo salário em ou depois de `t`."""
    for k in (0, 1, 2):
        y, m = _shift(t.year, t.month, k)
        p = payday_of(y, m, cfg)
        if p >= t:
            return p
    return payday_of(*_shift(t.year, t.month, 2), cfg)


def income_month(t, cfg):
    """Mês do salário que vai pagar o que se gasta hoje: o primeiro salário depois de hoje."""
    p = next_payday(t + timedelta(days=1), cfg)
    return f"{p.year:04d}-{p.month:02d}"

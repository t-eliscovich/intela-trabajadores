"""La cuenta de vacaciones. Sin base, sin Flask: números puros.

Regla del Código del Trabajo de Ecuador (art. 69):
  * 15 días por cada año COMPLETO de trabajo.
  * Desde el sexto año, un día más por cada año que pase de cinco
    (año 6 → 16 días, año 7 → 17 …), con tope de 15 días extra (30 en total).

Saldo = saldo al arrancar + días ganados desde entonces + ajustes − días tomados.

El «saldo al arrancar» existe porque el programa no sabe cuántos días tomó
cada uno antes de que existiera: contabilidad carga cuántos le quedan HOY y
desde ahí la cuenta sigue sola. Sin saldo al arrancar, se cuenta desde el
ingreso.

El año en curso NO suma hasta que se cumple. Se muestra aparte, como
«lleva X días del año que corre», para que el trabajador entienda por qué el
saldo no sube todos los meses.
"""
from __future__ import annotations

from datetime import date

DIAS_BASE = 15
ANIOS_PARA_ADICIONAL = 5
TOPE_ADICIONALES = 15


def dias_del_anio(numero_de_anio: int) -> int:
    """Cuántos días da el año N de trabajo (1 = el primero)."""
    if numero_de_anio < 1:
        return 0
    extra = max(0, numero_de_anio - ANIOS_PARA_ADICIONAL)
    return DIAS_BASE + min(extra, TOPE_ADICIONALES)


def _aniversario(ingreso: date, anios: int) -> date:
    """El aniversario número `anios`. Un 29/02 cae el 28/02 en años comunes."""
    try:
        return ingreso.replace(year=ingreso.year + anios)
    except ValueError:
        return ingreso.replace(year=ingreso.year + anios, day=28)


def anios_cumplidos(ingreso: date, hoy: date) -> int:
    if hoy < ingreso:
        return 0
    anios = hoy.year - ingreso.year
    if _aniversario(ingreso, anios) > hoy:
        anios -= 1
    return max(0, anios)


def dias_ganados(ingreso: date, hoy: date) -> int:
    """Suma de los días de cada año COMPLETO trabajado."""
    return sum(dias_del_anio(k) for k in range(1, anios_cumplidos(ingreso, hoy) + 1))


def dias_ganados_desde(ingreso: date, desde: date, hoy: date) -> int:
    """Los días de los aniversarios que cayeron DESPUÉS de `desde` y hasta hoy.

    Es lo que se suma encima del saldo al arrancar: el aniversario del mismo
    día del arranque ya está adentro de ese saldo, así que no cuenta.
    """
    return sum(dias_del_anio(k) for k in range(1, anios_cumplidos(ingreso, hoy) + 1)
               if _aniversario(ingreso, k) > desde)


def proximo_aniversario(ingreso: date, hoy: date) -> date:
    return _aniversario(ingreso, anios_cumplidos(ingreso, hoy) + 1)


def dias_en_curso(ingreso: date, hoy: date) -> float:
    """Lo que lleva acumulado del año que todavía no cumplió (proporcional).

    Es informativo: no entra en el saldo hasta el aniversario.
    """
    if hoy < ingreso:
        return 0.0
    anios = anios_cumplidos(ingreso, hoy)
    desde = _aniversario(ingreso, anios)
    hasta = _aniversario(ingreso, anios + 1)
    largo = (hasta - desde).days or 365
    pasados = (hoy - desde).days
    return round(dias_del_anio(anios + 1) * pasados / largo, 1)


def dias_entre(desde: date, hasta: date) -> int:
    """Días de un período tomado, contando los dos extremos.

    Las vacaciones de ley son días ininterrumpidos: el sábado y el domingo
    del medio cuentan. Contabilidad puede corregir el número a mano.
    """
    if hasta < desde:
        raise ValueError("La fecha de fin es anterior a la de inicio.")
    return (hasta - desde).days + 1


def resumen(ingreso: date, tomados: float, ajustes: float, hoy: date,
            saldo_inicial: float | None = None, fecha_saldo: date | None = None) -> dict:
    if saldo_inicial is not None and fecha_saldo is not None:
        ganados = dias_ganados_desde(ingreso, fecha_saldo, hoy)
        inicial = float(saldo_inicial)
    else:
        ganados = dias_ganados(ingreso, hoy)
        inicial = 0.0
    return {
        "anios": anios_cumplidos(ingreso, hoy),
        "inicial": inicial,
        "fecha_saldo": fecha_saldo if saldo_inicial is not None else None,
        "ganados": ganados,
        "ajustes": ajustes,
        "tomados": tomados,
        "saldo": inicial + ganados + ajustes - tomados,
        "en_curso": dias_en_curso(ingreso, hoy),
        "proximo_aniversario": proximo_aniversario(ingreso, hoy),
        "dias_proximo_anio": dias_del_anio(anios_cumplidos(ingreso, hoy) + 1),
    }

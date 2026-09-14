"""La cuenta de vacaciones. Sin base, sin Flask: números puros.

Se cuenta POR AÑO CALENDARIO («período»), como lo hace contabilidad en su
planilla: cada 1 de enero se acreditan los días de ese año.

Cuántos días da el año Y (Código del Trabajo de Ecuador, art. 69):
  * 15 días.
  * Más 1 por cada año de antigüedad que pase de cinco: extra = Y − año de
    ingreso − 5, entre 0 y 15 (tope 30 en total).
  * El primer año no suma nada. Al cumplir el año se acreditan los 15 de una
    (sin prorrateo: decisión de Tamara, 14/09/2026). Después, cada 1 de enero.

Saldo = saldo al arrancar + días acreditados desde entonces + ajustes − tomados.

El «saldo al arrancar» existe porque el programa no sabe cuántos días tomó
cada uno antes de que existiera: contabilidad carga cuántos le quedan HOY y
desde ahí la cuenta sigue sola. Sin saldo al arrancar, se cuenta desde el
ingreso como si nunca hubiera tomado.

Si a un trabajador la empresa le da un número fijo por año (`dias_por_anio`),
manda ése en vez de la regla (también al cumplir el primer año).
"""
from __future__ import annotations

from datetime import date

DIAS_BASE = 15
ANIOS_PARA_ADICIONAL = 5
TOPE_ADICIONALES = 15
TOPE_TOTAL = DIAS_BASE + TOPE_ADICIONALES


def dias_del_periodo(ingreso: date, anio: int, fijo: float | None = None) -> float:
    """Cuántos días da el año calendario `anio` a alguien que ingresó en `ingreso`.

    Es el año COMPLETO (para el año de ingreso, lo que daría entero: el
    primer año lo maneja `acreditado_hasta`: nada hasta el aniversario).
    """
    if anio < ingreso.year:
        return 0.0
    if fijo is not None:
        return float(fijo)
    extra = max(0, min(TOPE_ADICIONALES, anio - ingreso.year - ANIOS_PARA_ADICIONAL))
    return float(min(TOPE_TOTAL, DIAS_BASE + extra))


def primer_aniversario(ingreso: date) -> date:
    try:
        return date(ingreso.year + 1, ingreso.month, ingreso.day)
    except ValueError:  # 29 de febrero
        return date(ingreso.year + 1, 3, 1)


def acreditado_hasta(ingreso: date, fecha: date, fijo: float | None = None) -> float:
    """Todo lo acreditado desde el ingreso hasta `fecha` inclusive.

    El primer año no suma nada: el día que cumple el año se acreditan los 15
    (o el fijo) DE UNA (decisión Tamara 14/09/2026, sin prorrateo). De ahí en
    adelante, cada 1 de enero el año completo, empezando por el año siguiente
    al del aniversario (el año del aniversario ya quedó cubierto por esos 15).
    """
    if fecha < primer_aniversario(ingreso):
        return 0.0
    total = dias_del_periodo(ingreso, ingreso.year + 1, fijo)
    for anio in range(ingreso.year + 2, fecha.year + 1):
        total += dias_del_periodo(ingreso, anio, fijo)
    return round(total, 2)


def dias_ganados(ingreso: date, hoy: date, fijo: float | None = None) -> float:
    return acreditado_hasta(ingreso, hoy, fijo)


def dias_ganados_desde(ingreso: date, desde: date, hoy: date, fijo: float | None = None) -> float:
    """Lo acreditado DESPUÉS de `desde` y hasta hoy: lo que se suma encima del
    saldo al arrancar (ese saldo ya incluye lo acreditado hasta ese día)."""
    return round(acreditado_hasta(ingreso, hoy, fijo) - acreditado_hasta(ingreso, desde, fijo), 2)


def anios_cumplidos(ingreso: date, hoy: date) -> int:
    if hoy < ingreso:
        return 0
    anios = hoy.year - ingreso.year
    if (hoy.month, hoy.day) < (ingreso.month, ingreso.day):
        anios -= 1
    return max(0, anios)


def proxima_carga(ingreso: date, hoy: date) -> tuple[date, float, str]:
    """Cuándo y cuánto es lo próximo que se acredita: el primer aniversario
    (15 de una) si todavía no cumplió el año; si no, el 1 de enero."""
    primero = primer_aniversario(ingreso)
    if hoy < primero:
        return primero, dias_del_periodo(ingreso, ingreso.year + 1), "aniversario"
    return date(hoy.year + 1, 1, 1), dias_del_periodo(ingreso, hoy.year + 1), "anio"


def dias_entre(desde: date, hasta: date) -> int:
    """Días de un período tomado, contando los dos extremos (corridos)."""
    if hasta < desde:
        raise ValueError("La fecha de fin es anterior a la de inicio.")
    return (hasta - desde).days + 1


def aniversario(ingreso: date, hoy: date) -> date:
    """El próximo aniversario de ingreso (el primero, si todavía no cumplió el año)."""
    anio = hoy.year if (hoy.month, hoy.day) < (ingreso.month, ingreso.day) else hoy.year + 1
    try:
        return date(anio, ingreso.month, ingreso.day)
    except ValueError:  # 29 de febrero
        return date(anio, 3, 1)


def resumen(ingreso: date, tomados: float, ajustes: float, hoy: date,
            saldo_inicial: float | None = None, fecha_saldo: date | None = None,
            dias_por_anio: float | None = None, tomados_anio: float = 0.0) -> dict:
    """Los números de la pantalla. Además del saldo:

    * `tomados_anio`: lo tomado en el año calendario en curso, contando también
      lo anterior al saldo al arrancar (que no resta dos veces pero sí se tomó).
    * `disponible_anio` = saldo + tomados_anio: con lo que arrancó el año.
    * `arrastre` = disponible_anio − este_anio: lo acumulado de años anteriores
      (negativo = debía días).
    Así el trabajador lee «tenías X, tomaste Y, te quedan Z» como en la planilla.
    """
    fijo = float(dias_por_anio) if dias_por_anio is not None else None
    if saldo_inicial is not None and fecha_saldo is not None:
        ganados = dias_ganados_desde(ingreso, fecha_saldo, hoy, fijo)
        inicial = float(saldo_inicial)
    else:
        ganados = dias_ganados(ingreso, hoy, fijo)
        inicial = 0.0
    cuando, cuanto, tipo = proxima_carga(ingreso, hoy)
    if fijo is not None:
        cuanto = fijo
    saldo = round(inicial + ganados + ajustes - tomados, 2)
    este_anio = dias_del_periodo(ingreso, hoy.year, fijo)
    disponible_anio = round(saldo + tomados_anio, 2)
    return {
        "anios": anios_cumplidos(ingreso, hoy),
        "primer_anio": anios_cumplidos(ingreso, hoy) == 0,
        "aniversario": aniversario(ingreso, hoy),
        "tomados_anio": tomados_anio,
        "disponible_anio": disponible_anio,
        "arrastre": round(disponible_anio - este_anio, 2),
        "inicial": inicial,
        "fecha_saldo": fecha_saldo if saldo_inicial is not None else None,
        "ganados": ganados,
        "ajustes": ajustes,
        "tomados": tomados,
        "saldo": saldo,
        "este_anio": este_anio,
        "proxima_carga": cuando,
        "proxima_cantidad": cuanto,
        "proxima_tipo": tipo,
        "fijo": fijo,
    }

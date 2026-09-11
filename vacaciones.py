"""La cuenta de vacaciones. Sin base, sin Flask: números puros.

Se cuenta POR AÑO CALENDARIO («período»), como lo hace contabilidad en su
planilla: cada 1 de enero se acreditan los días de ese año.

Cuántos días da el año Y (Código del Trabajo de Ecuador, art. 69):
  * 15 días.
  * Más 1 por cada año de antigüedad que pase de cinco: extra = Y − año de
    ingreso − 5, entre 0 y 15 (tope 30 en total).
  * El año en que ingresó, proporcional: 1,25 días por cada mes cumplido
    (15 / 12), que se van acreditando mes a mes.

Saldo = saldo al arrancar + días acreditados desde entonces + ajustes − tomados.

El «saldo al arrancar» existe porque el programa no sabe cuántos días tomó
cada uno antes de que existiera: contabilidad carga cuántos le quedan HOY y
desde ahí la cuenta sigue sola. Sin saldo al arrancar, se cuenta desde el
ingreso como si nunca hubiera tomado.

Si a un trabajador la empresa le da un número fijo por año (`dias_por_anio`),
manda ése en vez de la regla (también para el proporcional del primer año).
"""
from __future__ import annotations

from datetime import date, timedelta

DIAS_BASE = 15
ANIOS_PARA_ADICIONAL = 5
TOPE_ADICIONALES = 15
TOPE_TOTAL = DIAS_BASE + TOPE_ADICIONALES


def dias_del_periodo(ingreso: date, anio: int, fijo: float | None = None) -> float:
    """Cuántos días da el año calendario `anio` a alguien que ingresó en `ingreso`.

    Es el año COMPLETO (para el año de ingreso, lo que daría entero: el
    proporcional lo calcula `acreditado_hasta`).
    """
    if anio < ingreso.year:
        return 0.0
    if fijo is not None:
        return float(fijo)
    extra = max(0, min(TOPE_ADICIONALES, anio - ingreso.year - ANIOS_PARA_ADICIONAL))
    return float(min(TOPE_TOTAL, DIAS_BASE + extra))


def meses_cumplidos(desde: date, hasta: date) -> int:
    """Meses enteros entre dos fechas (el día del mes tiene que llegar)."""
    if hasta < desde:
        return 0
    meses = (hasta.year - desde.year) * 12 + (hasta.month - desde.month)
    if hasta.day < desde.day:
        meses -= 1
    return max(0, meses)


def acreditado_hasta(ingreso: date, fecha: date, fijo: float | None = None) -> float:
    """Todo lo acreditado desde el ingreso hasta `fecha` inclusive.

    Años enteros posteriores al de ingreso: el 1 de enero se acredita el año
    completo. El año de ingreso: 1,25 (o fijo/12) por cada mes cumplido, hasta
    el 31/12 de ese año como máximo.
    """
    if fecha < ingreso:
        return 0.0
    total = 0.0
    # el año de ingreso, mes a mes
    # Un mes se da por cumplido al terminar el día anterior a la misma fecha
    # del mes siguiente: así el que entró el 1 de mayo tiene los 8 meses el
    # 31 de diciembre, y no el 1 de enero (que ya es otro año).
    fin_primer_anio = min(fecha, date(ingreso.year, 12, 31)) + timedelta(days=1)
    por_mes = dias_del_periodo(ingreso, ingreso.year, fijo) / 12
    total += round(por_mes * meses_cumplidos(ingreso, fin_primer_anio), 2)
    # los años siguientes, enteros el 1 de enero
    for anio in range(ingreso.year + 1, fecha.year + 1):
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
    """Cuándo y cuánto es lo próximo que se acredita, y de qué tipo
    ('anio' = el 1 de enero, 'mes' = el proporcional del primer año)."""
    if hoy.year == ingreso.year and hoy >= ingreso:
        m = meses_cumplidos(ingreso, hoy) + 1
        anio, mes = ingreso.year + (ingreso.month + m - 1) // 12, (ingreso.month + m - 1) % 12 + 1
        if anio == ingreso.year:
            dia = min(ingreso.day, [31, 29 if anio % 4 == 0 and (anio % 100 != 0 or anio % 400 == 0) else 28,
                                    31, 30, 31, 30, 31, 31, 30, 31, 30, 31][mes - 1])
            return date(anio, mes, dia), round(dias_del_periodo(ingreso, ingreso.year) / 12, 2), "mes"
    return date(hoy.year + 1, 1, 1), dias_del_periodo(ingreso, hoy.year + 1), "anio"


def dias_entre(desde: date, hasta: date) -> int:
    """Días de un período tomado, contando los dos extremos (corridos)."""
    if hasta < desde:
        raise ValueError("La fecha de fin es anterior a la de inicio.")
    return (hasta - desde).days + 1


def resumen(ingreso: date, tomados: float, ajustes: float, hoy: date,
            saldo_inicial: float | None = None, fecha_saldo: date | None = None,
            dias_por_anio: float | None = None) -> dict:
    fijo = float(dias_por_anio) if dias_por_anio is not None else None
    if saldo_inicial is not None and fecha_saldo is not None:
        ganados = dias_ganados_desde(ingreso, fecha_saldo, hoy, fijo)
        inicial = float(saldo_inicial)
    else:
        ganados = dias_ganados(ingreso, hoy, fijo)
        inicial = 0.0
    cuando, cuanto, tipo = proxima_carga(ingreso, hoy)
    if fijo is not None:
        cuanto = fijo if tipo == "anio" else round(fijo / 12, 2)
    return {
        "anios": anios_cumplidos(ingreso, hoy),
        "inicial": inicial,
        "fecha_saldo": fecha_saldo if saldo_inicial is not None else None,
        "ganados": ganados,
        "ajustes": ajustes,
        "tomados": tomados,
        "saldo": round(inicial + ganados + ajustes - tomados, 2),
        "este_anio": dias_del_periodo(ingreso, hoy.year, fijo),
        "proxima_carga": cuando,
        "proxima_cantidad": cuanto,
        "proxima_tipo": tipo,
        "fijo": fijo,
    }

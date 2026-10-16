"""El rol de pagos del mes: leer el Excel que exporta WinAfin y cruzarlo con el padrón.

Sin base ni Flask, como `vacaciones.py`, para poder probarlo solo.

El Excel de WinAfin (Rol → exportar, «rolN.xls») trae una fila por trabajador
con columnas sin nombre: `campr1…campr45` y `campp1…campp15`. Qué es cada una
sale de la pantalla «Rol de Pagos» de WinAfin (foto de Tamara, 13/10/2026), y
se verificó en los 114 de septiembre 2026: Σ ingresos = campr11,
Σ descuentos = campr20, campr11 − campr20 = campr21 (el líquido).

Las PROVISIONES (campp: aporte patronal, 13.º, 14.º y fondos de reserva que
paga la empresa) NO se muestran ni se guardan: no son plata del trabajador
este mes (Tamara, 13/10).
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from io import BytesIO

# (columna, nombre en pantalla, columna de horas que se muestra al lado)
INGRESOS = (
    ("campr1", "Sueldo", None),
    ("campr2", "Horas extra 50%", "horas_50"),
    ("campr3", "Horas extra 100%", "horas_100"),
    ("campr4", "Horas nocturnas", "horas_25"),
    ("campr5", "Fondo de reserva", None),
    ("campr6", "Décimo cuarto sueldo", None),
    ("campr7", "Décimo tercer sueldo", None),
    ("campr8", "Comisiones", None),
    ("campr9", "Bonificación", None),
    ("campr10", "Transporte", None),
)
TOTAL_INGRESOS = "campr11"
DESCUENTOS = (
    ("campr12", "Aporte personal IESS"),
    ("campr13", "Anticipo"),
    ("campr14", "Préstamo IESS"),
    ("campr15", "Daños de tela"),
    ("campr16", "Caja de ahorros"),
    ("campr17", "Préstamos de oficina"),
    ("campr18", "Retención judicial"),
    ("campr19", "Impuesto a la renta"),
)
TOTAL_DESCUENTOS = "campr20"
LIQUIDO = "campr21"
# Las columnas sin uso hoy (campr22…45) tienen que venir en cero: si un mes
# traen algo, es un concepto nuevo de WinAfin que esta tabla no conoce.
SIN_USO = tuple(f"campr{i}" for i in range(22, 46))
NECESARIAS = ("jno", "jnombre", "sueldo", "dias", TOTAL_INGRESOS, TOTAL_DESCUENTOS, LIQUIDO) \
    + tuple(c for c, _, _ in INGRESOS) + tuple(c for c, _ in DESCUENTOS)


class NoEsElRol(ValueError):
    pass


def _num(v) -> float:
    if v in (None, ""):
        return 0.0
    if isinstance(v, str):
        v = v.strip().replace(",", ".")
        if not v:
            return 0.0
    return round(float(v), 2)


def _horas(v: float) -> str:
    return (f"{v:g}").replace(".", ",") + " h"


def _filas_del_archivo(datos: bytes, nombre: str) -> list[dict]:
    """Las filas como dicts por título de columna. .xls (WinAfin) o .xlsx."""
    if nombre.lower().endswith(".xlsx") or datos[:2] == b"PK":
        from openpyxl import load_workbook
        hoja = load_workbook(BytesIO(datos), read_only=True, data_only=True).worksheets[0]
        filas = [list(r) for r in hoja.iter_rows(values_only=True)]
    else:
        import xlrd
        try:
            hoja = xlrd.open_workbook(file_contents=datos).sheet_by_index(0)
        except Exception as exc:  # noqa: BLE001
            raise NoEsElRol("No se pudo abrir el archivo. Suba el Excel del rol tal como lo exporta WinAfin.") from exc
        filas = [hoja.row_values(i) for i in range(hoja.nrows)]
    if not filas:
        raise NoEsElRol("El archivo está vacío.")
    titulos = [str(t or "").strip().lower() for t in filas[0]]
    faltan = [c for c in NECESARIAS if c not in titulos]
    if faltan:
        raise NoEsElRol("Este archivo no parece el rol de WinAfin: faltan las columnas "
                        + ", ".join(faltan[:6]) + ("…" if len(faltan) > 6 else "") + ".")
    return [dict(zip(titulos, f)) for f in filas[1:] if any(c not in (None, "") for c in f)]


def leer_rol(datos: bytes, nombre: str = "rol.xls") -> tuple[list[dict], list[dict]]:
    """Devuelve (buenas, malas). Cada buena trae lo que se guarda y se muestra;
    cada mala, la fila y el motivo. Una fila cuyos totales no cierran NO entra:
    mostrar un recibo que no suma es peor que no mostrarlo."""
    buenas, malas = [], []
    for n, f in enumerate(_filas_del_archivo(datos, nombre), start=2):
        nombre_rol = " ".join(str(f.get("jnombre") or "").split())
        try:
            if not nombre_rol:
                raise ValueError("Fila sin nombre.")
            codigo = int(_num(f.get("jno")))
            ingresos = []
            for col, etiqueta, col_horas in INGRESOS:
                v = _num(f.get(col))
                if v:
                    detalle = ""
                    if col == "campr1" and _num(f.get("dias")):
                        detalle = f"{_num(f.get('dias')):g} días"
                    elif col_horas and _num(f.get(col_horas)):
                        detalle = _horas(_num(f.get(col_horas)))
                    ingresos.append({"concepto": etiqueta, "detalle": detalle, "valor": v})
            descuentos = [{"concepto": etiqueta, "detalle": "", "valor": _num(f.get(col))}
                          for col, etiqueta in DESCUENTOS if _num(f.get(col))]
            t_ing, t_des, liquido = _num(f.get(TOTAL_INGRESOS)), _num(f.get(TOTAL_DESCUENTOS)), _num(f.get(LIQUIDO))
            if abs(sum(x["valor"] for x in ingresos) - t_ing) > 0.015:
                raise ValueError(f"Los ingresos suman {sum(x['valor'] for x in ingresos):.2f} y el total dice {t_ing:.2f}.")
            if abs(sum(x["valor"] for x in descuentos) - t_des) > 0.015:
                raise ValueError(f"Los descuentos suman {sum(x['valor'] for x in descuentos):.2f} y el total dice {t_des:.2f}.")
            if abs(t_ing - t_des - liquido) > 0.015:
                raise ValueError(f"Ingresos menos descuentos da {t_ing - t_des:.2f} y el líquido dice {liquido:.2f}.")
            raras = [c for c in SIN_USO if _num(f.get(c))]
            if raras:
                raise ValueError("Trae valores en columnas que el programa no conoce ("
                                 + ", ".join(raras) + "). Avise a sistemas.")
            buenas.append({
                "fila": n, "codigo": codigo, "nombre_rol": nombre_rol,
                "sueldo": _num(f.get("sueldo")), "dias": _num(f.get("dias")),
                "horas_25": _num(f.get("horas_25")), "horas_50": _num(f.get("horas_50")),
                "horas_100": _num(f.get("horas_100")),
                "atrasos": _num(f.get("atrasos")), "otros": _num(f.get("otros")),
                "ingresos": ingresos, "descuentos": descuentos,
                "total_ingresos": t_ing, "total_descuentos": t_des, "liquido": liquido,
            })
        except (ValueError, TypeError) as exc:
            malas.append({"fila": n, "nombre_rol": nombre_rol, "motivo": str(exc)})
    return buenas, malas


def mes_del_nombre(nombre: str) -> int | None:
    """WinAfin nombra el archivo «rol9.xls» para septiembre."""
    m = re.search(r"rol\D{0,3}(\d{1,2})(?!\d)", nombre or "", re.I)
    if m and 1 <= int(m.group(1)) <= 12:
        return int(m.group(1))
    return None


# --------------------------------------------------------------------------
# El cruce con el padrón
# --------------------------------------------------------------------------
def normalizar(nombre: str) -> str:
    sin_tildes = "".join(c for c in unicodedata.normalize("NFKD", nombre or "")
                         if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^A-Z ]", " ", sin_tildes.upper()).split())


# Cómo se encontró a cada uno. Los «parecido» se muestran para revisar.
COMO = {"codigo": "por código del rol", "nombre": "mismo nombre",
        "orden": "mismo nombre, en otro orden", "parecido": "nombre parecido · revise"}


def cruzar(filas: list[dict], padron: list[dict]) -> None:
    """Le pone a cada fila `trabajador_id`, `como` y `aviso` (o None si no aparece).

    Lo más importante es NO cargarle el rol de una persona a otra (Tamara,
    13/10/2026). Por eso:
      * el código de WinAfin guardado de un mes anterior (`codigo_rol`) sólo
        vale si el NOMBRE también coincide (con el padrón, o con el nombre de
        WinAfin que contabilidad confirmó ese mes, `nombre_rol`); si el código
        apunta a otra persona, se avisa y se busca por nombre;
      * sólo «codigo», «nombre» y «orden» entran solos. «parecido» (un nombre
        que no es exactamente igual, o alguien dado de baja) entra únicamente
        si contabilidad lo confirma a mano en la revisión;
      * nunca dos filas al mismo trabajador, y con dos personas del mismo
        nombre no se elige ninguna.
    """
    por_codigo = {t["codigo_rol"]: t for t in padron if t.get("codigo_rol") is not None}
    norm = {t["id"]: normalizar(t["nombre"]) for t in padron}
    usados: set[int] = set()

    def libre(cands):
        cands = [t for t in cands if t["id"] not in usados]
        if len(cands) > 1:  # si hay uno activo y otros dados de baja, el activo
            activos = [t for t in cands if t.get("activo", True)]
            cands = activos if len(activos) == 1 else cands
        return cands[0] if len(cands) == 1 else None

    def poner(f, t, como, aviso=None):
        if not t.get("activo", True):
            como, aviso = "parecido", aviso or "está dado de baja en el padrón"
        f["trabajador_id"], f["como"], f["nombre_padron"] = t["id"], como, t["nombre"]
        if aviso:
            f["aviso"] = aviso
        usados.add(t["id"])

    for f in filas:
        f["trabajador_id"], f["como"], f["nombre_padron"], f["aviso"] = None, None, None, None

    # 1. el código guardado, pero sólo si el nombre acompaña
    for f in filas:
        t = por_codigo.get(f["codigo"])
        if not t or t["id"] in usados:
            continue
        parecido = comparar_nombres(f["nombre_rol"], t["nombre"])
        if t.get("nombre_rol") and normalizar(t["nombre_rol"]) == normalizar(f["nombre_rol"]):
            parecido = "nombre"  # el mismo código y el mismo nombre de WinAfin que se confirmó antes
        if parecido in ("nombre", "orden"):
            poner(f, t, "codigo")
        elif parecido == "parecido":
            poner(f, t, "parecido", "mismo código de WinAfin, nombre distinto")
        else:
            f["aviso"] = f"el código {f['codigo']} de WinAfin estaba guardado en {t['nombre']}"

    # 2. por nombre: igual, en otro orden, parecido
    for como in ("nombre", "orden", "parecido"):
        for f in filas:
            if f["trabajador_id"] is not None:
                continue
            cands = [t for t in padron if comparar_nombres(f["nombre_rol"], t["nombre"], norm[t["id"]]) == como]
            if como == "parecido" and len([t for t in cands if t["id"] not in usados]) != 1:
                continue  # parecido sólo si es UNO
            t = libre(cands)
            if t:
                poner(f, t, como, f.get("aviso"))


def comparar_nombres(nombre_rol: str, nombre_padron: str, norm_padron: str | None = None) -> str | None:
    """«nombre» (igual), «orden» (mismas palabras en otro orden), «parecido»
    (le falta un nombre o cambia una letra) o None."""
    rol = normalizar(nombre_rol)
    pad = norm_padron if norm_padron is not None else normalizar(nombre_padron)
    if rol == pad:
        return "nombre"
    palabras, pp = rol.split(), pad.split()
    if sorted(palabras) == sorted(pp):
        return "orden"
    a, b = set(palabras), set(pp)
    # uno contiene al otro (le falta un segundo nombre o un apellido)
    if len(a & b) >= 2 and (a <= b or b <= a):
        return "parecido"
    # o casi igual letra por letra (una tilde, una letra cambiada)
    if difflib.SequenceMatcher(None, " ".join(sorted(pp)), " ".join(sorted(palabras))).ratio() >= 0.9:
        return "parecido"
    return None


def sin_rol(padron: list[dict], filas: list[dict]) -> list[dict]:
    """Los activos del padrón que no quedaron con ninguna fila del rol."""
    con = {f["trabajador_id"] for f in filas if f.get("trabajador_id")}
    return [t for t in padron if t.get("activo", True) and t["id"] not in con]


# --------------------------------------------------------------------------
# La constancia de la firma (como DocuSign): una huella del recibo firmado.
# --------------------------------------------------------------------------
def huella(r: dict) -> str:
    """SHA-256 de lo que el trabajador vio y firmó. Si después el recibo cambia
    (por ejemplo, se vuelve a cargar el mes con otros números), la huella ya no
    coincide y la constancia lo dice."""
    import hashlib
    import json
    contenido = {"trabajador_id": r["trabajador_id"], "anio": r["anio"], "mes": r["mes"], "ingresos": r["ingresos"],
                 "descuentos": r["descuentos"], "sueldo": round(float(r.get("sueldo") or 0), 2),
                 "dias": round(float(r.get("dias") or 0), 2), "atrasos": round(float(r.get("atrasos") or 0), 2),
                 "otros": round(float(r.get("otros") or 0), 2), "total_ingresos": round(float(r["total_ingresos"]), 2),
                 "total_descuentos": round(float(r["total_descuentos"]), 2), "liquido": round(float(r["liquido"]), 2)}
    return hashlib.sha256(json.dumps(contenido, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def dispositivo(user_agent: str | None) -> str:
    """«Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 …) … Safari» → «iPhone · Safari»."""
    ua = user_agent or ""
    equipo = next((n for k, n in (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"),
                                  ("Windows", "Windows"), ("Macintosh", "Mac"), ("Linux", "Linux")) if k in ua), "")
    navegador = next((n for k, n in (("Edg/", "Edge"), ("SamsungBrowser", "Samsung Internet"), ("CriOS", "Chrome"),
                                     ("Chrome/", "Chrome"), ("Firefox/", "Firefox"), ("Safari/", "Safari")) if k in ua), "")
    return " · ".join(x for x in (equipo, navegador) if x) or "—"


# --------------------------------------------------------------------------
# El ROL ACUMULADO de WinAfin (varios meses, una fila por trabajador y mes,
# CON CÉDULA). Sirve para cargar el historial de una vez (Tamara, 13/10/2026).
# Septiembre 2026 de este archivo coincide al centavo con rol9.xls en los 114.
# --------------------------------------------------------------------------
# Las columnas, en orden. «14to Sueldo» y «13er Sueldo» aparecen DOS veces (ingreso
# y provisión), así que se lee por posición después de verificar los títulos.
COLUMNAS_ACUMULADO = (
    "ano", "mes", "dep", "codigo", "cedula", "nombre",
    "sueldo base", "horas 50", "horas 100", "horas nocturnas", "fondo de reserv", "14to sueldo",
    "13er sueldo", "comisiones", "bonificacion", "transporte",
    "aporte personal", "anticipo", "prestamo iess", "danos tela", "caja de ahorros",
    "prestamos ofici", "retencion judic", "imp renta",
)
_ING_ACUM = range(6, 16)    # mismos conceptos y orden que INGRESOS
_DES_ACUM = range(16, 24)   # mismos conceptos y orden que DESCUENTOS (después vienen las provisiones)


def _titulo(t) -> str:
    sin = "".join(c for c in unicodedata.normalize("NFKD", str(t or "")) if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", sin.lower()).split())


def es_acumulado(datos: bytes, nombre: str = "") -> bool:
    try:
        titulos = _primera_fila(datos, nombre)
    except Exception:  # noqa: BLE001
        return False
    return [_titulo(t) for t in titulos[:6]] == list(COLUMNAS_ACUMULADO[:6])


def _primera_fila(datos: bytes, nombre: str) -> list:
    if nombre.lower().endswith(".xlsx") or datos[:2] == b"PK":
        from openpyxl import load_workbook
        hoja = load_workbook(BytesIO(datos), read_only=True, data_only=True).worksheets[0]
        return list(next(hoja.iter_rows(values_only=True)))
    import xlrd
    return xlrd.open_workbook(file_contents=datos).sheet_by_index(0).row_values(0)


def cedula_valida(c: str) -> bool:
    """El dígito verificador de la cédula ecuatoriana (módulo 10). Provincia
    01–24 o 30, tercer dígito menor que 6."""
    if not (isinstance(c, str) and len(c) == 10 and c.isdigit()):
        return False
    if not (1 <= int(c[:2]) <= 24 or int(c[:2]) == 30) or int(c[2]) >= 6:
        return False
    suma = 0
    for i, d in enumerate(c[:9]):
        v = int(d) * (2 if i % 2 == 0 else 1)
        suma += v - 9 if v > 9 else v
    return (10 - suma % 10) % 10 == int(c[9])


def limpiar_cedula(v) -> str:
    """1002049912 → «1002049912»; 912345678 (se comió el 0) → «0912345678»."""
    if isinstance(v, float) and v == int(v):
        v = int(v)
    d = re.sub(r"\D", "", str(v or ""))
    return d.zfill(10) if 7 <= len(d) <= 10 else d


def leer_acumulado(datos: bytes, nombre: str = "acumulado.xlsx") -> tuple[list[dict], list[dict]]:
    """Devuelve (buenas, malas). Cada buena: un recibo de un trabajador en un
    mes, con la misma forma que `leer_rol` más anio, mes y cedula. El archivo no
    trae totales ni horas: el líquido es ingresos − descuentos."""
    if nombre.lower().endswith(".xlsx") or datos[:2] == b"PK":
        from openpyxl import load_workbook
        hoja = load_workbook(BytesIO(datos), read_only=True, data_only=True).worksheets[0]
        filas = [list(r) for r in hoja.iter_rows(values_only=True)]
    else:
        import xlrd
        h = xlrd.open_workbook(file_contents=datos).sheet_by_index(0)
        filas = [h.row_values(i) for i in range(h.nrows)]
    titulos = [_titulo(t) for t in filas[0][:len(COLUMNAS_ACUMULADO)]] if filas else []
    if titulos != list(COLUMNAS_ACUMULADO):
        distintos = [f"«{t}»" for t, e in zip(titulos, COLUMNAS_ACUMULADO) if t != e][:3]
        raise NoEsElRol("Este archivo no tiene las columnas del rol acumulado de WinAfin"
                        + (": " + ", ".join(distintos) if distintos else "") + ".")
    buenas, malas = [], []
    for n, f in enumerate(filas[1:], start=2):
        f = list(f) + [None] * (len(COLUMNAS_ACUMULADO) - len(f))
        if f[0] in (None, "") and f[1] in (None, ""):
            continue  # la fila de totales del final
        nombre_rol = " ".join(str(f[5] or "").split())
        try:
            anio, mes = int(_num(f[0])), int(_num(f[1]))
            if not (2000 <= anio <= 2100 and 1 <= mes <= 12):
                raise ValueError("Año o mes inválido.")
            cedula = limpiar_cedula(f[4])
            if len(cedula) != 10:
                raise ValueError("La cédula no tiene 10 dígitos.")
            if not nombre_rol:
                raise ValueError("Fila sin nombre.")
            ingresos = [{"concepto": INGRESOS[i][1], "detalle": "", "valor": _num(f[c])}
                        for i, c in enumerate(_ING_ACUM) if _num(f[c])]
            descuentos = [{"concepto": DESCUENTOS[i][1], "detalle": "", "valor": _num(f[c])}
                          for i, c in enumerate(_DES_ACUM) if _num(f[c])]
            if not ingresos:
                raise ValueError("No tiene ingresos.")
            ti = round(sum(x["valor"] for x in ingresos), 2)
            td = round(sum(x["valor"] for x in descuentos), 2)
            buenas.append({
                "fila": n, "anio": anio, "mes": mes, "codigo": int(_num(f[3])), "cedula": cedula,
                "nombre_rol": nombre_rol, "sueldo": None, "dias": 0, "horas_25": 0, "horas_50": 0,
                "horas_100": 0, "atrasos": 0, "otros": 0, "ingresos": ingresos, "descuentos": descuentos,
                "total_ingresos": ti, "total_descuentos": td, "liquido": round(ti - td, 2),
            })
        except (ValueError, TypeError) as exc:
            malas.append({"fila": n, "nombre_rol": nombre_rol, "motivo": str(exc)})
    return buenas, malas


def cruzar_por_cedula(personas: list[dict], padron: list[dict]) -> None:
    """Como `cruzar`, pero la llave es la CÉDULA. Entra solo quien tiene la
    misma cédula en el padrón y un nombre compatible. Todo lo demás (misma
    cédula con otro nombre, o un nombre que coincide con alguien de OTRA
    cédula) queda para confirmar a mano: nunca el rol de uno a otro."""
    por_cedula = {limpiar_cedula(t["cedula"]): t for t in padron}
    usados: set[int] = set()
    for p in personas:
        p["trabajador_id"], p["como"], p["nombre_padron"], p["aviso"] = None, None, None, None
        t = por_cedula.get(p["cedula"])
        if t:
            parecido = comparar_nombres(p["nombre_rol"], t["nombre"])
            if parecido in ("nombre", "orden", "parecido") and t.get("activo", True):
                p["trabajador_id"], p["como"], p["nombre_padron"] = t["id"], "cedula", t["nombre"]
            else:
                p["trabajador_id"], p["como"], p["nombre_padron"] = t["id"], "parecido", t["nombre"]
                p["aviso"] = ("está dado de baja en el padrón" if parecido
                              else "la cédula es la misma pero el nombre es distinto")
            usados.add(t["id"])
    # Sin cédula en el padrón: se busca por nombre, pero nunca entra solo.
    resto = [p for p in personas if p["trabajador_id"] is None]
    libres = [t for t in padron if t["id"] not in usados]
    cruzar(resto, libres)
    for p in resto:
        if p["trabajador_id"]:
            t = next(x for x in libres if x["id"] == p["trabajador_id"])
            p["como"] = "parecido"
            if not cedula_valida(limpiar_cedula(t["cedula"])) and cedula_valida(p["cedula"]):
                # La del padrón está mal tipeada (no pasa el dígito verificador) y la
                # de WinAfin sí: al confirmar se corrige en el padrón, así puede entrar.
                p["corregir_cedula"] = True
                p["aviso"] = (f"en el padrón tiene {t['cedula']}, que NO es una cédula válida. "
                              f"Al confirmar se corrige a {p['cedula']}")
            else:
                p["aviso"] = f"en el padrón tiene otra cédula ({t['cedula']})"


# --------------------------------------------------------------------------
# El archivo que se manda al BANCO (Tamara 14/10/2026: «este es el archivo que
# controla, es lo que mandan al banco, así que no tiene puntos»). Pago_Rol_MM_AA.xlsx,
# sin títulos, una fila por persona:
#   PA · NOMINA · USD · monto EN CENTAVOS · CTA · AHO/CTE · N.º de cuenta · ROL_MM_AA ·
#   C (cédula) / P (pasaporte) · identificación · nombre corto · código del banco (vacío =
#   el mismo banco)
# Es el SEGUNDO control: el rol de WinAfin ya está cargado; esto no carga nada, se compara.
# --------------------------------------------------------------------------
def leer_pago_banco(datos: bytes, nombre: str = "pago.xlsx") -> tuple[list[dict], list[str], tuple | None]:
    """Devuelve (filas, problemas, (anio, mes) del ROL_MM_AA o None)."""
    from openpyxl import load_workbook
    try:
        hoja = load_workbook(BytesIO(datos), read_only=True, data_only=True).worksheets[0]
        crudas = [list(r) for r in hoja.iter_rows(values_only=True)]
    except Exception:  # noqa: BLE001
        raise ValueError("No se pudo abrir el archivo. Tiene que ser el Excel que se manda al banco (Pago_Rol_MM_AA.xlsx).") from None
    filas, problemas, refs = [], [], set()
    for n, r in enumerate(crudas, start=1):
        r = (r + [None] * 12)[:12]
        if all(v in (None, "") for v in r):
            continue
        if str(r[0] or "").strip().upper() != "PA" or str(r[1] or "").strip().upper() != "NOMINA":
            problemas.append(f"Fila {n}: no parece una fila de pago (empieza con «{r[0]}»).")
            continue
        try:
            centavos = int(round(float(r[3])))
        except (TypeError, ValueError):
            problemas.append(f"Fila {n}: el monto «{r[3]}» no es un número.")
            continue
        tipo_id = str(r[8] or "").strip().upper()
        ident = limpiar_cedula(r[9]) if tipo_id == "C" else str(r[9] or "").strip()
        cuenta = re.sub(r"\D", "", str(int(r[6]) if isinstance(r[6], float) else (r[6] or "")))
        refs.add(str(r[7] or "").strip().upper())
        filas.append({"fila": n, "monto": centavos / 100, "tipo_cuenta": str(r[5] or "").strip().upper(),
                      "cuenta": cuenta, "tipo_id": tipo_id, "identificacion": ident,
                      "nombre": " ".join(str(r[10] or "").split()), "banco": r[11] if r[11] not in ("", None) else None})
    if not filas:
        raise ValueError("El archivo no tiene filas de pago. ¿Es el que se manda al banco?")
    mes = None
    m = re.fullmatch(r"ROL_(\d{1,2})_(\d{2})", refs.pop()) if len(refs) == 1 else None
    if m and 1 <= int(m.group(1)) <= 12:
        mes = (2000 + int(m.group(2)), int(m.group(1)))
    elif len(refs) > 1:
        problemas.append("El archivo mezcla referencias de varios meses: " + ", ".join(sorted(refs)) + ".")
    return filas, problemas, mes


def misma_cuenta(a: str | None, b: str | None) -> bool:
    """El Excel del banco se come los ceros de adelante: 0220136474 = 220136474."""
    a, b = re.sub(r"\D", "", a or "").lstrip("0"), re.sub(r"\D", "", b or "").lstrip("0")
    return bool(a) and a == b


def _fmt(v: float) -> str:
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def comparar_con_banco(recibos: list[dict], banco: list[dict], cuentas: dict[int, str]) -> dict:
    """Recibo por recibo contra el archivo del banco. `cuentas`: trabajador_id → la cuenta
    que firmó en su constancia.

    Primero por cédula. Después, a los que no se encontraron, por NOMBRE: el nombre corto
    del banco («MEJIA LEVIS») tiene que estar entero dentro del nombre del padrón y ser uno
    solo. Así se encuentra al que en el banco figura con otra cédula o con pasaporte, y se
    avisa (en septiembre fueron 10: el monto coincidía, la cédula no)."""
    por_ced: dict[str, list] = {}
    for b in banco:
        por_ced.setdefault(b["identificacion"], []).append(b)
    usados: set = set()
    elegido: dict = {}
    for r in recibos:
        # primero la cédula con la que cobra (si se cargó), después la del padrón
        for ced in (r.get("cedula_banco"), r.get("cedula")):
            cands = [b for b in por_ced.get(ced or "", []) if id(b) not in usados]
            if ced and len(cands) == 1:
                elegido[r["trabajador_id"]] = (cands[0], False)
                usados.add(id(cands[0]))
                break
    for r in recibos:
        if r["trabajador_id"] in elegido:
            continue
        pal = set(normalizar(r["nombre"]).split())
        cands = [b for b in banco if id(b) not in usados and len(normalizar(b["nombre"]).split()) >= 2
                 and set(normalizar(b["nombre"]).split()) <= pal]
        if len(cands) == 1:
            elegido[r["trabajador_id"]] = (cands[0], cands[0]["tipo_id"] == "C")
            usados.add(id(cands[0]))
    por_trab = {}
    for r in recibos:
        if r["trabajador_id"] not in elegido:
            por_trab[r["trabajador_id"]] = {"estado": "falta", "texto": "No está en el archivo del banco"}
            continue
        b, otra_cedula = elegido[r["trabajador_id"]]
        dif = round(b["monto"] - float(r["liquido"]), 2)
        firmada = cuentas.get(r["trabajador_id"])
        res = {"banco": b, "diferencia": dif}
        if abs(dif) >= 0.005:
            res.update(estado="monto", texto=f"El banco paga {_fmt(b['monto'])} ({'+' if dif > 0 else ''}{_fmt(dif)})")
        elif firmada and not misma_cuenta(firmada, b["cuenta"]):
            res.update(estado="cuenta", texto=f"La cuenta del banco ({b['cuenta']}) no es la de su constancia ({firmada})")
        elif otra_cedula:
            res.update(estado="cedula", texto=f"El monto coincide, pero en el banco figura con otra cédula ({b['identificacion']})")
        else:
            res.update(estado="ok", texto="Coincide con el banco" + ("" if firmada else " (sin constancia para comparar la cuenta)"))
        por_trab[r["trabajador_id"]] = res
    sobran = [b for b in banco if id(b) not in usados]
    cuenta = {k: sum(1 for x in por_trab.values() if x["estado"] == k) for k in ("ok", "monto", "cuenta", "cedula", "falta")}
    return {"por_trabajador": por_trab, "sobran": sobran, "cuenta": cuenta,
            "total_banco": round(sum(b["monto"] for b in banco), 2),
            "total_rol": round(sum(float(r["liquido"]) for r in recibos), 2)}

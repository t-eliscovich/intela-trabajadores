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
    """Le pone a cada fila `trabajador_id` y `como` (o None si no aparece).

    Orden: el código del rol que ya quedó guardado en la ficha (`codigo_rol`,
    de un mes anterior) → el mismo nombre → las mismas palabras en otro orden →
    un nombre parecido (contenido o casi igual), sólo si hay UN candidato.
    Nunca dos filas al mismo trabajador.
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

    for f in filas:
        f["trabajador_id"], f["como"], f["nombre_padron"] = None, None, None

    pasos = (
        ("codigo", lambda f: [por_codigo[f["codigo"]]] if f["codigo"] in por_codigo else []),
        ("nombre", lambda f: [t for t in padron if norm[t["id"]] == normalizar(f["nombre_rol"])]),
        ("orden", lambda f: [t for t in padron
                             if sorted(norm[t["id"]].split()) == sorted(normalizar(f["nombre_rol"]).split())]),
        ("parecido", _parecidos(padron, norm)),
    )
    for como, buscar in pasos:
        for f in filas:
            if f["trabajador_id"] is not None:
                continue
            t = libre(buscar(f))
            if t:
                f["trabajador_id"], f["como"], f["nombre_padron"] = t["id"], como, t["nombre"]
                usados.add(t["id"])


def _parecidos(padron, norm):
    def buscar(f):
        rol = normalizar(f["nombre_rol"])
        palabras = set(rol.split())
        cands = []
        for t in padron:
            p = norm[t["id"]]
            pp = set(p.split())
            # uno contiene al otro (le falta un segundo nombre o un apellido)
            if len(pp & palabras) >= 2 and (pp <= palabras or palabras <= pp):
                cands.append(t)
            # o casi igual letra por letra (una tilde, una letra cambiada)
            elif difflib.SequenceMatcher(None, " ".join(sorted(pp)), " ".join(sorted(palabras))).ratio() >= 0.9:
                cands.append(t)
        return cands
    return buscar


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
                 "descuentos": r["descuentos"], "total_ingresos": round(float(r["total_ingresos"]), 2),
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

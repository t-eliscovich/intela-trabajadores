"""Trabajadores — Intela.

Para el trabajador (desde el celular, sólo con la cédula):

    /            poner la cédula
    /yo          sus vacaciones y su calendario de comidas (almuerzo y cena)

Para contabilidad (con usuario y clave):

    /admin                   la lista de trabajadores con el saldo de cada uno
    /admin/trabajador/N      la ficha: vacaciones tomadas, ajustes, comidas
    /admin/carga             pegar la planilla para cargar a todos de una vez
    /admin/comidas           el cuadro del mes para pagarle a la cafetería
    /admin/usuarios          quién entra a esta parte
"""
from __future__ import annotations

import calendar
import logging
import os
import re
from datetime import date, datetime, timedelta
from functools import wraps
from zoneinfo import ZoneInfo

from flask import (Flask, abort, flash, g, redirect, render_template, request,
                   session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

import config
import store
import vacaciones

logging.basicConfig(level=logging.INFO)

app = Flask(__name__)
app.secret_key = config.SECRET_KEY
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024

CARPETA = os.path.dirname(os.path.abspath(__file__))

# Cuántos días para atrás puede marcar el trabajador una comida que se olvidó.
# Más atrás lo corrige contabilidad.
DIAS_ATRAS = 3

MESES = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
DIAS_CORTOS = ["L", "M", "M", "J", "V", "S", "D"]
TIPOS_COMIDA = (("almuerzo", "A"), ("cena", "C"))


def leer_tipo_comida(texto: str | None) -> str:
    if texto not in dict(TIPOS_COMIDA):
        raise ValueError("Falta decir si es almuerzo o cena.")
    return texto


def hoy() -> date:
    """El día de la fábrica, no el del server (que está en UTC)."""
    return datetime.now(ZoneInfo(config.ZONA)).date()


# --------------------------------------------------------------------------
# Versión desplegada (lo escribe el auto-updater; si no está, no se inventa)
# --------------------------------------------------------------------------
def _version():
    for ruta in (os.path.join(CARPETA, ".version"), r"C:\trabajadores_update\.commit"):
        try:
            with open(ruta) as f:
                return f.read().strip()[:7] or "?"
        except OSError:
            continue
    return "?"


def _desplegado():
    ultimo = 0.0
    try:
        for nombre in os.listdir(CARPETA):
            if nombre.endswith(".py"):
                ultimo = max(ultimo, os.path.getmtime(os.path.join(CARPETA, nombre)))
    except OSError:
        return None
    return datetime.fromtimestamp(ultimo).isoformat(timespec="seconds") if ultimo else None


VERSION = _version()
DESPLEGADO = _desplegado()

# --------------------------------------------------------------------------
# Pool de la base — A NIVEL DE MÓDULO, a propósito. Waitress hace `import app`
# y el bloque __main__ nunca corre (lección de Máquinas, 18/08/2026).
# --------------------------------------------------------------------------
ERROR_ARRANQUE: str | None = None
try:
    store.init_pool()
    if config.ADMIN_INICIAL and not store.hay_usuarios():
        store.crear_usuario("admin", generate_password_hash(config.ADMIN_INICIAL), "Contabilidad")
        logging.getLogger(__name__).info("Usuario admin creado con TRABAJADORES_ADMIN_INICIAL")
except Exception as _exc:  # noqa: BLE001
    ERROR_ARRANQUE = str(_exc)
    logging.getLogger(__name__).exception("No se pudo abrir el pool de la base")


@app.before_request
def _frenar_si_no_hay_base():
    if ERROR_ARRANQUE and request.endpoint not in ("healthz", "static"):
        return render_template("sin_base.html", error=ERROR_ARRANQUE), 503


@app.before_request
def _cargar_usuario():
    g.usuario = session.get("usuario")


# --------------------------------------------------------------------------
# Ayudas
# --------------------------------------------------------------------------
def requiere_admin(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if not g.get("usuario"):
            return redirect(url_for("admin_entrar", next=request.path))
        return f(*a, **kw)
    return wrapper


def _adonde_iba():
    destino = request.args.get("next") or ""
    return destino if destino.startswith("/") and not destino.startswith("//") else None


def limpiar_cedula(texto: str) -> str:
    """Sólo los dígitos. Una cédula ecuatoriana tiene 10; se aceptan de 6 a 13
    por si alguien está con pasaporte."""
    digitos = re.sub(r"\D", "", texto or "")
    if not 6 <= len(digitos) <= 13:
        raise ValueError("La cédula tiene que tener entre 6 y 13 números.")
    return digitos


_FORMATOS_FECHA = ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%y")


def leer_fecha(texto: str) -> date:
    texto = (texto or "").strip()
    for fmt in _FORMATOS_FECHA:
        try:
            return datetime.strptime(texto, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"No entiendo la fecha «{texto}». Escribila como 25/03/2024.")


def leer_decimal(texto: str, nombre: str = "El número") -> float:
    """Acepta coma o punto como decimal. Rechaza nan/inf, que `float()` traga."""
    limpio = (texto or "").strip().replace(",", ".")
    try:
        valor = float(limpio)
    except ValueError:
        raise ValueError(f"{nombre} no es un número: «{texto}».")
    if valor != valor or valor in (float("inf"), float("-inf")):
        raise ValueError(f"{nombre} no es un número: «{texto}».")
    return valor


def leer_mes(texto: str | None) -> tuple[int, int]:
    """«2026-09» → (2026, 9). Sin dato, el mes de hoy."""
    if texto:
        m = re.fullmatch(r"(\d{4})-(\d{1,2})", texto.strip())
        if m and 1 <= int(m.group(2)) <= 12:
            return int(m.group(1)), int(m.group(2))
    h = hoy()
    return h.year, h.month


def calendario(anio: int, mes: int) -> list[list[date | None]]:
    """Las semanas del mes, de lunes a domingo, con None donde no hay día."""
    return [[d if d.month == mes else None for d in semana]
            for semana in calendar.Calendar(firstweekday=0).monthdatescalendar(anio, mes)]


def mes_anterior(anio: int, mes: int) -> str:
    a, m = (anio - 1, 12) if mes == 1 else (anio, mes - 1)
    return f"{a}-{m:02d}"


def mes_siguiente(anio: int, mes: int) -> str:
    a, m = (anio + 1, 1) if mes == 12 else (anio, mes + 1)
    return f"{a}-{m:02d}"


def _resumen(t: dict) -> dict:
    return vacaciones.resumen(t["fecha_ingreso"], float(t["tomados"]), float(t["ajustes"]), hoy(),
                              t.get("saldo_inicial"), t.get("fecha_saldo_inicial"))


@app.template_filter("num")
def num(valor, decimales=0):
    """Formato español: punto para miles, coma para decimales. 0 → «0»."""
    if valor is None:
        return "—"
    try:
        v = float(valor)
    except (TypeError, ValueError):
        return str(valor)
    if decimales == 0 and v == int(v):
        return f"{int(v):,}".replace(",", ".")
    s = f"{v:,.{max(decimales, 1)}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return s.rstrip("0").rstrip(",") if "," in s else s


@app.template_filter("fecha")
def fecha_es(valor):
    if not valor:
        return "—"
    return valor.strftime("%d/%m/%Y")


@app.context_processor
def _globales():
    return {"MESES": MESES, "DIAS_CORTOS": DIAS_CORTOS, "TIPOS_COMIDA": TIPOS_COMIDA, "hoy": hoy()}


def _totales(marcados: set) -> dict:
    """Cuántos almuerzos, cuántas cenas y el total, de un set de (fecha, tipo)."""
    t = {tipo: sum(1 for (_f, x) in marcados if x == tipo) for tipo, _ in TIPOS_COMIDA}
    t["total"] = len(marcados)
    return t


# ==========================================================================
# El trabajador
# ==========================================================================
@app.route("/", methods=["GET", "POST"])
def entrar():
    if request.method == "POST":
        try:
            cedula = limpiar_cedula(request.form.get("cedula", ""))
            t = store.trabajador_por_cedula(cedula)
        except ValueError as exc:
            flash(str(exc), "error")
            return render_template("entrar.html")
        if not t or not t["activo"]:
            flash("No encontramos esa cédula. Preguntá en contabilidad.", "error")
            return render_template("entrar.html")
        session.clear()
        session["trabajador_id"] = t["id"]
        return redirect(url_for("yo"))
    return render_template("entrar.html")


def _mi_trabajador():
    id_ = session.get("trabajador_id")
    t = store.trabajador(id_) if id_ else None
    if not t or not t["activo"]:
        session.pop("trabajador_id", None)
        return None
    return t


@app.route("/yo")
def yo():
    t = _mi_trabajador()
    if not t:
        return redirect(url_for("entrar"))
    anio, mes = leer_mes(request.args.get("mes"))
    marcados = store.comidas_del_mes(t["id"], anio, mes)
    h = hoy()
    return render_template(
        "yo.html", t=t, v=_resumen(t), anio=anio, mes=mes,
        semanas=calendario(anio, mes), marcados=marcados, totales=_totales(marcados),
        desde_cuando=h - timedelta(days=DIAS_ATRAS),
        anterior=mes_anterior(anio, mes), siguiente=mes_siguiente(anio, mes))


@app.route("/yo/comida", methods=["POST"])
def yo_comida():
    t = _mi_trabajador()
    if not t:
        return redirect(url_for("entrar"))
    try:
        fecha = leer_fecha(request.form.get("fecha", ""))
        tipo = leer_tipo_comida(request.form.get("tipo"))
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("yo"))
    h = hoy()
    if fecha > h or fecha < h - timedelta(days=DIAS_ATRAS):
        flash(f"Podés marcar hoy y hasta {DIAS_ATRAS} días para atrás. "
              "Para otro día, pedile a contabilidad.", "error")
        return redirect(url_for("yo", mes=f"{fecha.year}-{fecha.month:02d}"))
    if request.form.get("accion") == "desmarcar":
        store.desmarcar_comida(t["id"], fecha, tipo)
    else:
        store.marcar_comida(t["id"], fecha, tipo, "trabajador")
    return redirect(url_for("yo", mes=f"{fecha.year}-{fecha.month:02d}"))


@app.route("/salir")
def salir():
    session.pop("trabajador_id", None)
    return redirect(url_for("entrar"))


# ==========================================================================
# Contabilidad
# ==========================================================================
@app.route("/admin/entrar", methods=["GET", "POST"])
def admin_entrar():
    if request.method == "POST":
        u = store.usuario_por_nombre((request.form.get("usuario") or "").strip().lower())
        if u and check_password_hash(u["clave_hash"], request.form.get("clave") or ""):
            session["usuario"] = {"id": u["id"], "usuario": u["usuario"], "nombre": u["nombre"]}
            return redirect(_adonde_iba() or url_for("admin"))
        flash("Usuario o clave incorrectos.", "error")
    return render_template("admin_entrar.html")


@app.route("/admin/salir")
def admin_salir():
    session.pop("usuario", None)
    return redirect(url_for("admin_entrar"))


@app.route("/admin", methods=["GET", "POST"])
@requiere_admin
def admin():
    if request.method == "POST":
        try:
            cedula = limpiar_cedula(request.form.get("cedula", ""))
            nombre = (request.form.get("nombre") or "").strip()
            if not nombre:
                raise ValueError("Falta el nombre.")
            ingreso = leer_fecha(request.form.get("fecha_ingreso", ""))
            if ingreso > hoy():
                raise ValueError("La fecha de ingreso no puede ser futura.")
            if store.trabajador_por_cedula(cedula):
                raise ValueError(f"La cédula {cedula} ya está cargada.")
            saldo = (request.form.get("saldo") or "").strip()
            saldo = leer_decimal(saldo, "El saldo") if saldo else None
            id_ = store.crear_trabajador(cedula, nombre, ingreso, saldo,
                                         hoy() if saldo is not None else None)
            flash(f"{nombre} cargado.", "ok")
            return redirect(url_for("admin_trabajador", id_=id_))
        except ValueError as exc:
            flash(str(exc), "error")
    incluir = request.args.get("todos") == "1"
    filas = [dict(t, v=_resumen(t)) for t in store.trabajadores(incluir_inactivos=incluir)]
    return render_template("admin.html", filas=filas, incluir=incluir)


@app.route("/admin/trabajador/<int:id_>", methods=["GET", "POST"])
@requiere_admin
def admin_trabajador(id_):
    t = store.trabajador(id_)
    if not t:
        abort(404)
    if request.method == "POST":
        try:
            _accion_trabajador(t, request.form.get("accion", ""))
        except ValueError as exc:
            flash(str(exc), "error")
        return redirect(url_for("admin_trabajador", id_=id_, mes=request.form.get("mes") or None))
    anio, mes = leer_mes(request.args.get("mes"))
    marcados = store.comidas_del_mes(id_, anio, mes)
    return render_template(
        "admin_trabajador.html", t=t, v=_resumen(t),
        vacaciones=store.vacaciones(id_), ajustes=store.ajustes(id_),
        anio=anio, mes=mes, semanas=calendario(anio, mes), marcados=marcados,
        totales=_totales(marcados),
        anterior=mes_anterior(anio, mes), siguiente=mes_siguiente(anio, mes))


def _accion_trabajador(t: dict, accion: str) -> None:
    f = request.form
    quien = g.usuario["usuario"]
    if accion == "editar":
        nombre = (f.get("nombre") or "").strip()
        if not nombre:
            raise ValueError("Falta el nombre.")
        cedula = limpiar_cedula(f.get("cedula", ""))
        otro = store.trabajador_por_cedula(cedula)
        if otro and otro["id"] != t["id"]:
            raise ValueError(f"La cédula {cedula} ya es de {otro['nombre']}.")
        ingreso = leer_fecha(f.get("fecha_ingreso", ""))
        store.editar_trabajador(t["id"], cedula, nombre, ingreso)
        flash("Datos guardados.", "ok")
    elif accion == "saldo_inicial":
        saldo = (f.get("saldo") or "").strip()
        if saldo:
            fecha = leer_fecha(f.get("fecha", ""))
            if fecha > hoy():
                raise ValueError("La fecha del saldo no puede ser futura.")
            store.poner_saldo_inicial(t["id"], leer_decimal(saldo, "El saldo"), fecha)
            flash("Saldo al arrancar guardado.", "ok")
        else:
            store.poner_saldo_inicial(t["id"], None, None)
            flash("Sin saldo al arrancar: se cuenta desde el ingreso.", "ok")
    elif accion == "baja":
        store.dar_de_baja(t["id"], leer_fecha(f.get("fecha_salida", "")))
        flash(f"{t['nombre']} dado de baja.", "ok")
    elif accion == "reactivar":
        store.reactivar(t["id"])
        flash(f"{t['nombre']} vuelve a estar activo.", "ok")
    elif accion == "vacacion":
        desde, hasta = leer_fecha(f.get("desde", "")), leer_fecha(f.get("hasta", ""))
        propuesto = vacaciones.dias_entre(desde, hasta)
        dias = leer_decimal(f.get("dias") or str(propuesto), "Los días")
        if dias <= 0:
            raise ValueError("Los días tienen que ser más que cero.")
        store.agregar_vacacion(t["id"], desde, hasta, dias, (f.get("nota") or "").strip(), quien)
        flash(f"{num(dias)} días de vacaciones cargados.", "ok")
    elif accion == "vacacion_borrar":
        store.borrar_vacacion(int(f.get("id", 0)))
        flash("Período borrado.", "ok")
    elif accion == "ajuste":
        dias = leer_decimal(f.get("dias", ""), "Los días")
        motivo = (f.get("motivo") or "").strip()
        if not motivo:
            raise ValueError("Escribí el motivo del ajuste.")
        store.agregar_ajuste(t["id"], dias, motivo, quien)
        flash("Ajuste cargado.", "ok")
    elif accion == "ajuste_borrar":
        store.borrar_ajuste(int(f.get("id", 0)))
        flash("Ajuste borrado.", "ok")
    elif accion in ("marcar", "desmarcar"):
        fecha = leer_fecha(f.get("fecha", ""))
        tipo = leer_tipo_comida(f.get("tipo"))
        if accion == "marcar":
            store.marcar_comida(t["id"], fecha, tipo, quien)
        else:
            store.desmarcar_comida(t["id"], fecha, tipo)
    else:
        raise ValueError("No sé qué hacer con eso.")


# --------------------------------------------------------------------------
# Carga masiva: pegar las filas del Excel
# --------------------------------------------------------------------------
def _separador(linea: str) -> str:
    """Lo decide la primera fila y vale para todas: si cada fila eligiera el
    suyo, una coma adentro de un nombre correría las columnas sin que se note."""
    for sep in ("\t", ";", ","):
        if sep in linea:
            return sep
    return "\t"


def leer_pegado(texto: str) -> tuple[list[dict], list[dict]]:
    """Cada fila: cédula · nombre · fecha de ingreso · [saldo de días].

    Devuelve (buenas, descartadas). Si la primera fila tiene letras donde va
    la cédula, es un título y se salta.
    """
    lineas = [ln for ln in (texto or "").splitlines() if ln.strip()]
    if not lineas:
        return [], []
    sep = _separador(lineas[0])
    buenas, malas = [], []
    vistas: set[str] = set()
    for n, linea in enumerate(lineas, start=1):
        celdas = [c.strip().strip('"') for c in linea.split(sep)]
        if n == 1 and not re.search(r"\d", celdas[0]):
            continue  # la fila de títulos
        celdas += [""] * (4 - len(celdas))
        try:
            cedula = limpiar_cedula(celdas[0])
            nombre = celdas[1]
            if not nombre:
                raise ValueError("Falta el nombre.")
            ingreso = leer_fecha(celdas[2])
            if ingreso > hoy():
                raise ValueError("La fecha de ingreso es futura.")
            saldo = leer_decimal(celdas[3], "El saldo") if celdas[3] else None
            if cedula in vistas:
                raise ValueError("Cédula repetida en la planilla.")
            vistas.add(cedula)
            buenas.append({"cedula": cedula, "nombre": nombre, "fecha_ingreso": ingreso,
                           "saldo": saldo, "fila": n})
        except ValueError as exc:
            malas.append({"fila": n, "texto": linea.strip()[:80], "motivo": str(exc)})
    return buenas, malas


@app.route("/admin/carga", methods=["GET", "POST"])
@requiere_admin
def admin_carga():
    texto = request.form.get("texto", "") if request.method == "POST" else ""
    buenas, malas = leer_pegado(texto) if texto else ([], [])
    if request.method == "POST" and request.form.get("confirmar") == "1" and buenas:
        r = store.cargar_lote(buenas, hoy())
        flash(f"Listo: {r['nuevos']} nuevos, {r['actualizados']} actualizados.", "ok")
        return redirect(url_for("admin"))
    existentes = {t["cedula"] for t in store.trabajadores(incluir_inactivos=True)} if buenas else set()
    return render_template("admin_carga.html", texto=texto, buenas=buenas, malas=malas,
                           existentes=existentes)


# --------------------------------------------------------------------------
# El cuadro de comidas del mes
# --------------------------------------------------------------------------
@app.route("/admin/comidas", methods=["GET", "POST"])
@requiere_admin
def admin_comidas():
    anio, mes = leer_mes(request.values.get("mes"))
    if request.method == "POST":
        try:
            id_ = int(request.form.get("trabajador_id", 0))
            fecha = leer_fecha(request.form.get("fecha", ""))
            tipo = leer_tipo_comida(request.form.get("tipo"))
            if request.form.get("accion") == "desmarcar":
                store.desmarcar_comida(id_, fecha, tipo)
            else:
                store.marcar_comida(id_, fecha, tipo, g.usuario["usuario"])
        except ValueError as exc:
            flash(str(exc), "error")
        return redirect(url_for("admin_comidas", mes=f"{anio}-{mes:02d}"))
    dias = [date(anio, mes, d) for d in range(1, calendar.monthrange(anio, mes)[1] + 1)]
    marcados = store.comidas_de_todos(anio, mes)
    filas = []
    for t in store.trabajadores(incluir_inactivos=True):
        suyos = marcados.get(t["id"], set())
        if not t["activo"] and not suyos:
            continue  # un dado de baja sin comidas ese mes no ocupa renglón
        filas.append({"t": t, "marcados": suyos, "totales": _totales(suyos)})
    por_dia = {d: {tipo: sum(1 for f in filas if (d, tipo) in f["marcados"]) for tipo, _ in TIPOS_COMIDA}
               for d in dias}
    totales = {tipo: sum(f["totales"][tipo] for f in filas) for tipo, _ in TIPOS_COMIDA}
    totales["total"] = sum(totales.values())
    return render_template(
        "admin_comidas.html", anio=anio, mes=mes, dias=dias, filas=filas, por_dia=por_dia,
        totales=totales, anterior=mes_anterior(anio, mes), siguiente=mes_siguiente(anio, mes))


# --------------------------------------------------------------------------
# Usuarios de contabilidad
# --------------------------------------------------------------------------
@app.route("/admin/usuarios", methods=["GET", "POST"])
@requiere_admin
def admin_usuarios():
    if request.method == "POST":
        f = request.form
        try:
            accion = f.get("accion", "")
            if accion == "crear":
                usuario = (f.get("usuario") or "").strip().lower()
                if not re.fullmatch(r"[a-z0-9._-]{2,30}", usuario):
                    raise ValueError("El usuario va en minúsculas, sin espacios (2 a 30 letras).")
                clave = f.get("clave") or ""
                if len(clave) < 6:
                    raise ValueError("La clave tiene que tener al menos 6 caracteres.")
                nombre = (f.get("nombre") or "").strip() or usuario
                if any(u["usuario"] == usuario for u in store.usuarios()):
                    raise ValueError(f"El usuario «{usuario}» ya existe.")
                store.crear_usuario(usuario, generate_password_hash(clave), nombre)
                flash(f"Usuario {usuario} creado.", "ok")
            elif accion == "clave":
                clave = f.get("clave") or ""
                if len(clave) < 6:
                    raise ValueError("La clave tiene que tener al menos 6 caracteres.")
                store.cambiar_clave(int(f.get("id", 0)), generate_password_hash(clave))
                flash("Clave cambiada.", "ok")
            elif accion in ("activar", "desactivar"):
                id_ = int(f.get("id", 0))
                if accion == "desactivar" and id_ == g.usuario["id"]:
                    raise ValueError("No podés desactivarte a vos mismo.")
                store.activar_usuario(id_, accion == "activar")
                flash("Listo.", "ok")
        except ValueError as exc:
            flash(str(exc), "error")
        return redirect(url_for("admin_usuarios"))
    return render_template("admin_usuarios.html", usuarios=store.usuarios())


# --------------------------------------------------------------------------
@app.route("/healthz")
def healthz():
    """Nunca levanta. Si algo está mal, lo DICE."""
    estado = {"ok": ERROR_ARRANQUE is None, "base": "error" if ERROR_ARRANQUE else "ok",
              "version": VERSION, "desplegado": DESPLEGADO}
    if ERROR_ARRANQUE:
        estado["error_arranque"] = ERROR_ARRANQUE
        return estado, 503
    if store.AVISOS_ESQUEMA:
        estado["avisos_esquema"] = store.AVISOS_ESQUEMA
    try:
        estado["trabajadores_activos"] = len(store.trabajadores())
        estado["hay_usuarios"] = store.hay_usuarios()
    except Exception as exc:  # noqa: BLE001
        estado.update(ok=False, base="error", error=str(exc))
        return estado, 503
    return estado


@app.errorhandler(404)
def _404(_e):
    return render_template("falta.html"), 404


if __name__ == "__main__":
    app.run(port=config.PORT, debug=True)

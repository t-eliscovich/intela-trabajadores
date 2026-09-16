"""Trabajadores — Intela.

Para el trabajador (desde el celular, sólo con la cédula):

    /            poner la cédula
    /yo → /yo/vacaciones  cuántos días le quedan, y pedir más
    /yo/perfil      quién es para la empresa: área, desde cuándo, cuántos días gana por año

Para el comedor:

    /comedor/t/<clave>       la tablet: cédula → confirmar → invitados (sin sesión, link con clave)
    /comedor/dia             quién comió cada día (usuario del comedor o contabilidad)

Para contabilidad (con usuario y clave):

    /admin                   la lista de trabajadores con el saldo de cada uno
    /admin/trabajador/N      la ficha: vacaciones tomadas, ajustes, comidas
    /admin/solicitudes       los pedidos de los trabajadores (aprobar / rechazar / deshacer)
    /admin/historial         lo borrado (períodos, ajustes), para recuperarlo si hizo falta
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
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=config.DIAS_SESION_TRABAJADOR)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024

CARPETA = os.path.dirname(os.path.abspath(__file__))

MESES = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
DIAS_CORTOS = ["L", "M", "M", "J", "V", "S", "D"]
TIPOS_COMIDA = (("almuerzo", "A"), ("cena", "C"))
# Las áreas de la fábrica, como las usa contabilidad en su planilla (Dep).
AREAS = ("ADM", "BO", "CC", "KK", "TT")


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
        u = g.get("usuario")
        if not u:
            return redirect(url_for("admin_entrar", next=request.path))
        # Una sesión abierta antes de que existieran los roles no trae rol: es de contabilidad.
        if (u.get("rol") or "contabilidad") != "contabilidad":
            return redirect(url_for("comedor_dia"))
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
    raise ValueError(f"No entiendo la fecha «{texto}». Escríbala como 25/03/2024.")


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


def mes_anterior(anio: int, mes: int) -> str:
    a, m = (anio - 1, 12) if mes == 1 else (anio, mes - 1)
    return f"{a}-{m:02d}"


def mes_siguiente(anio: int, mes: int) -> str:
    a, m = (anio + 1, 1) if mes == 12 else (anio, mes + 1)
    return f"{a}-{m:02d}"


def _resumen(t: dict) -> dict:
    return vacaciones.resumen(t["fecha_ingreso"], float(t["tomados"]), float(t["ajustes"]), hoy(),
                              t.get("saldo_inicial"), t.get("fecha_saldo_inicial"),
                              t.get("dias_por_anio"), float(t.get("tomados_anio") or 0))


def leer_perfil(f) -> dict:
    """Los cuatro datos opcionales del perfil, desde un formulario o una fila pegada.
    Vacío → None. Lo que viene mal, avisa en castellano."""
    area = (f.get("area") or "").strip() or None
    nac = (f.get("fecha_nacimiento") or "").strip()
    nac = leer_fecha(nac) if nac else None
    if nac and (nac > hoy() or nac.year < 1920):
        raise ValueError(f"La fecha de nacimiento no puede ser {nac.strftime('%d/%m/%Y')}.")
    cel = re.sub(r"[^\d+]", "", f.get("celular") or "") or None
    dpa = (f.get("dias_por_anio") or "").strip()
    dpa = leer_decimal(dpa, "Los días por año") if dpa else None
    if dpa is not None and not 0 < dpa <= 60:
        raise ValueError("Los días por año tienen que estar entre 1 y 60.")
    direccion = (f.get("direccion") or "").strip()[:200] or None
    return {"area": area, "fecha_nacimiento": nac, "celular": cel, "dias_por_anio": dpa,
            "direccion": direccion}


def leer_celular(texto: str | None) -> str | None:
    """Sólo dígitos (y un + adelante). Vacío → None."""
    cel = re.sub(r"[^\d+]", "", texto or "")
    if cel and not 7 <= len(cel.lstrip("+")) <= 15:
        raise ValueError("El celular tiene que tener entre 7 y 15 números.")
    return cel or None


def leer_tipo_ausencia(texto: str | None) -> str:
    if texto not in store.TIPOS_AUSENCIA:
        raise ValueError("Falta decir qué tipo de ausencia es.")
    return texto


def link_whatsapp(celular: str | None, texto: str) -> str | None:
    """Abre el chat de WhatsApp con el mensaje ya escrito. Un celular de
    Ecuador «0991234567» es +593 991234567."""
    if not celular:
        return None
    digitos = re.sub(r"\D", "", celular)
    if digitos.startswith("0") and len(digitos) == 10:
        digitos = "593" + digitos[1:]
    if len(digitos) < 8:
        return None
    from urllib.parse import quote
    return f"https://wa.me/{digitos}?text={quote(texto)}"


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
    if isinstance(valor, datetime) and valor.tzinfo:
        valor = valor.astimezone(ZoneInfo(config.ZONA))
    return valor.strftime("%d/%m/%Y")


@app.context_processor
def _globales():
    pendientes = 0
    if g.get("usuario") and not ERROR_ARRANQUE:
        try:
            pendientes = store.cuantas_pendientes()
        except Exception:  # noqa: BLE001
            pendientes = 0
    return {"MESES": MESES, "DIAS_CORTOS": DIAS_CORTOS, "TIPOS_COMIDA": TIPOS_COMIDA, "AREAS": AREAS,
            "TIPOS_AUSENCIA": store.TIPOS_AUSENCIA, "hoy": hoy(), "n_pendientes": pendientes}


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
            flash("No encontramos esa cédula. Pregunte en contabilidad.", "error")
            return render_template("entrar.html")
        session.clear()
        session["trabajador_id"] = t["id"]
        session.permanent = True  # 30 días sin volver a poner la cédula
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
    return redirect(url_for("yo_vacaciones"))


@app.route("/yo/vacaciones")
def yo_vacaciones():
    t = _mi_trabajador()
    if not t:
        return redirect(url_for("entrar"))
    return render_template("yo_vacaciones.html", t=t, v=_resumen(t),
                           vacaciones=store.vacaciones(t["id"]),
                           pedidos=store.solicitudes(t["id"]))


# Hasta cuántos días para atrás se puede pedir (una enfermedad se avisa después).
DIAS_ATRAS_PEDIDO = 30


@app.route("/yo/pedir", methods=["POST"])
def yo_pedir():
    t = _mi_trabajador()
    if not t:
        return redirect(url_for("entrar"))
    f = request.form
    try:
        tipo = leer_tipo_ausencia(f.get("tipo"))
        desde, hasta = leer_fecha(f.get("desde", "")), leer_fecha(f.get("hasta", ""))
        dias = float(vacaciones.dias_entre(desde, hasta))
        h = hoy()
        if desde < h - timedelta(days=DIAS_ATRAS_PEDIDO):
            raise ValueError(f"Sólo se puede pedir hasta {DIAS_ATRAS_PEDIDO} días para atrás. "
                             "Para algo más viejo, hable con contabilidad.")
        if dias > 60:
            raise ValueError("Son más de 60 días. Hable con contabilidad.")
        for p in store.solicitudes(t["id"]):
            if p["estado"] == "pendiente" and p["desde"] <= hasta and desde <= p["hasta"]:
                raise ValueError("Ya tiene un pedido pendiente para esos días.")
        store.crear_solicitud(t["id"], tipo, desde, hasta, dias, (f.get("nota") or "").strip()[:200])
        flash("Pedido enviado. Contabilidad le responde aquí mismo.", "ok")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("yo_vacaciones"))


@app.route("/yo/pedido/cancelar", methods=["POST"])
def yo_pedido_cancelar():
    t = _mi_trabajador()
    if not t:
        return redirect(url_for("entrar"))
    p = store.solicitud(int(request.form.get("id", 0) or 0))
    if p and p["trabajador_id"] == t["id"] and p["estado"] == "pendiente":
        store.responder_solicitud(p["id"], "cancelada", None, "trabajador")
        flash("Pedido cancelado.", "ok")
    elif p and p["trabajador_id"] == t["id"] and p["estado"] == "aprobada" and p["desde"] > hoy():
        store.cancelar_solicitud_aprobada(p["id"], "cancelado por el trabajador", "trabajador")
        flash("Pedido cancelado. Los días vuelven a su saldo.", "ok")
    elif p and p["trabajador_id"] == t["id"] and p["estado"] == "aprobada":
        flash("Ese pedido ya empezó: para cancelarlo hable con contabilidad.", "error")
    return redirect(url_for("yo_vacaciones"))


@app.route("/yo/perfil", methods=["GET", "POST"])
def yo_perfil():
    t = _mi_trabajador()
    if not t:
        return redirect(url_for("entrar"))
    if request.method == "POST":
        try:
            cel = leer_celular(request.form.get("celular"))
            direccion = (request.form.get("direccion") or "").strip()[:200] or None
            cambios = store.cambiar_contacto(t["id"], cel, direccion)
            flash("Datos guardados. Contabilidad los va a ver." if cambios else "No cambió nada.", "ok")
        except ValueError as exc:
            flash(str(exc), "error")
        return redirect(url_for("yo_perfil"))
    return render_template("yo_perfil.html", t=t, v=_resumen(t))


@app.route("/salir")
def salir():
    session.pop("trabajador_id", None)
    return redirect(url_for("entrar"))


# ==========================================================================
# El comedor. Dos entradas:
#   * la tablet del mostrador abre /comedor/t/<clave> (un link con clave
#     adentro, sin sesión, que no vence): el trabajador escribe su cédula,
#     confirma, y puede anotar invitados;
#   * la persona del comedor (usuario con rol «comedor») o contabilidad entra
#     con su usuario y ve quién comió cada día.
# ==========================================================================
HORA_CENA = 15  # desde las 15:00 (de Ecuador) la comida es la cena; antes, el almuerzo
MINUTOS_PARA_DESHACER = 10
MAX_INVITADOS = 3


def comida_de_ahora() -> str:
    return "cena" if datetime.now(ZoneInfo(config.ZONA)).hour >= HORA_CENA else "almuerzo"


DIAS_LARGOS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
DIAS_TRES = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]


@app.template_filter("fecha_larga")
def fecha_larga(valor):
    return f"{DIAS_LARGOS[valor.weekday()].capitalize()} {valor.day} de {MESES[valor.month]}"


@app.template_filter("fecha_corta")
def fecha_corta(valor):
    return f"{DIAS_TRES[valor.weekday()]} {valor.day}"


def clave_comedor() -> str:
    """La clave del link de la tablet. Se crea sola la primera vez."""
    import secrets
    clave = store.configuracion("clave_comedor")
    if not clave:
        clave = secrets.token_urlsafe(9)
        store.poner_configuracion("clave_comedor", clave)
    return clave


def requiere_comedor(f):
    """La tablet (link con clave) o cualquier usuario entrado (comedor o contabilidad)."""
    @wraps(f)
    def wrapper(*a, **kw):
        token = kw.pop("token", None)
        g.token = None
        if token is not None:
            if token != clave_comedor():
                abort(404)
            g.token = token
        elif not g.get("usuario"):
            return redirect(url_for("admin_entrar", next=request.path))
        return f(*a, **kw)
    return wrapper


def requiere_sesion(f):
    """Cualquier usuario entrado: contabilidad o comedor."""
    @wraps(f)
    def wrapper(*a, **kw):
        if not g.get("usuario"):
            return redirect(url_for("admin_entrar", next=request.path))
        return f(*a, **kw)
    return wrapper


def _u(endpoint: str, **kw) -> str:
    """URL del comedor conservando la clave de la tablet si la hay."""
    if g.get("token"):
        return url_for(endpoint + "_t", token=g.token, **kw)
    return url_for(endpoint, **kw)


@app.context_processor
def _url_comedor():
    return {"u": _u}


def _quien_marca() -> str:
    return g.usuario["usuario"] if g.get("usuario") else "tablet"


def _tablet(plantilla: str, **kw):
    kw.setdefault("tipo", comida_de_ahora())
    return render_template(plantilla, **kw)


@app.route("/comedor", methods=["GET", "POST"], endpoint="comedor")
@app.route("/comedor/t/<token>", methods=["GET", "POST"], endpoint="comedor_t")
@requiere_comedor
def comedor():
    h, tipo = hoy(), comida_de_ahora()
    if request.method == "POST":
        try:
            cedula = limpiar_cedula(request.form.get("cedula", ""))
        except ValueError as exc:
            return _tablet("comedor.html", error=str(exc))
        t = store.trabajador_por_cedula(cedula)
        if not t or not t["activo"]:
            return _tablet("comedor.html", error="No encontramos esa cédula. Pregunte en contabilidad.", cedula=cedula)
        if store.comida_marcada(t["id"], h, tipo):
            return _tablet("comedor_listo.html", t=t, ya_estaba=True, invitados=_invitados_de(t, h, tipo))
        return _tablet("comedor_confirmar.html", t=t, fecha=h)
    return _tablet("comedor.html")


def _invitados_de(t: dict, fecha: date, tipo: str) -> int:
    return sum(i["cantidad"] for i in store.invitados_del_dia(fecha) if i["trabajador_id"] == t["id"] and i["tipo"] == tipo)


def _trabajador_del_form():
    cedula = limpiar_cedula(request.form.get("cedula", ""))
    tipo = leer_tipo_comida(request.form.get("tipo"))
    t = store.trabajador_por_cedula(cedula)
    if not t or not t["activo"]:
        raise ValueError("No encontramos esa cédula.")
    return t, tipo


@app.route("/comedor/confirmar", methods=["POST"], endpoint="comedor_confirmar")
@app.route("/comedor/t/<token>/confirmar", methods=["POST"], endpoint="comedor_confirmar_t")
@requiere_comedor
def comedor_confirmar():
    try:
        t, tipo = _trabajador_del_form()
    except ValueError as exc:
        return _tablet("comedor.html", error=str(exc))
    store.marcar_comida(t["id"], hoy(), tipo, _quien_marca())
    return _tablet("comedor_listo.html", t=t, tipo=tipo, recien=True, invitados=0)


@app.route("/comedor/deshacer", methods=["POST"], endpoint="comedor_deshacer")
@app.route("/comedor/t/<token>/deshacer", methods=["POST"], endpoint="comedor_deshacer_t")
@requiere_comedor
def comedor_deshacer():
    try:
        t, tipo = _trabajador_del_form()
    except ValueError as exc:
        return _tablet("comedor.html", error=str(exc))
    if store.desmarcar_comida_reciente(t["id"], hoy(), tipo, MINUTOS_PARA_DESHACER):
        return _tablet("comedor.html", tipo=tipo, aviso=f"Listo, {t['nombre']}: se sacó {'el almuerzo' if tipo == 'almuerzo' else 'la cena'}.")
    return _tablet("comedor.html", tipo=tipo, error="Eso ya no se puede deshacer desde aquí. Avise en contabilidad.")


@app.route("/comedor/invitados", methods=["POST"], endpoint="comedor_invitados")
@app.route("/comedor/t/<token>/invitados", methods=["POST"], endpoint="comedor_invitados_t")
@requiere_comedor
def comedor_invitados():
    try:
        t, tipo = _trabajador_del_form()
        cantidad = int(request.form.get("cantidad", 0) or 0)
        if not 1 <= cantidad <= MAX_INVITADOS:
            raise ValueError(f"Los invitados son de 1 a {MAX_INVITADOS}.")
        descripcion = (request.form.get("descripcion") or "").strip()[:80]
        if not descripcion:
            raise ValueError("Diga quiénes son los invitados (por ejemplo: proveedor, familiar).")
        ya = _invitados_de(t, hoy(), tipo)
        if ya + cantidad > MAX_INVITADOS:
            raise ValueError(f"Ya tiene {ya} {'invitado' if ya == 1 else 'invitados'} hoy; el máximo es {MAX_INVITADOS}.")
    except ValueError as exc:
        return _tablet("comedor.html", error=str(exc))
    store.agregar_invitados(t["id"], hoy(), tipo, cantidad, descripcion, _quien_marca())
    return _tablet("comedor_listo.html", t=t, tipo=tipo, invitados_anotados=cantidad, invitados=ya + cantidad)


@app.route("/comedor/dia", methods=["GET", "POST"])
@requiere_sesion
def comedor_dia():
    """Quién comió un día: para la persona del comedor y para contabilidad."""
    try:
        fecha = leer_fecha(request.values.get("fecha", "")) if request.values.get("fecha") else hoy()
    except ValueError:
        fecha = hoy()
    if request.method == "POST":
        try:
            accion = request.form.get("accion", "")
            if accion == "marcar":
                store.marcar_comida(int(request.form.get("trabajador_id", 0)), fecha,
                                    leer_tipo_comida(request.form.get("tipo")), g.usuario["usuario"])
            elif accion == "desmarcar":
                store.desmarcar_comida(int(request.form.get("trabajador_id", 0)), fecha,
                                       leer_tipo_comida(request.form.get("tipo")))
            elif accion == "quitar_invitados":
                store.borrar_invitados(int(request.form.get("id", 0)))
        except ValueError as exc:
            flash(str(exc), "error")
        return redirect(url_for("comedor_dia", fecha=fecha.isoformat()))
    comieron = store.quien_comio(fecha)
    por_tipo = {tipo: [c for c in comieron if c["tipo"] == tipo] for tipo, _ in TIPOS_COMIDA}
    marcaron = {c["trabajador_id"] for c in comieron}
    faltan = [dict(t, whatsapp=link_whatsapp(t["celular"], _texto_no_se_anoto(t, fecha)))
              for t in store.trabajadores() if t["id"] not in marcaron]
    invitados = store.invitados_del_dia(fecha)
    return render_template("comedor_dia.html", fecha=fecha, por_tipo=por_tipo, faltan=faltan, invitados=invitados,
                           total_invitados=sum(i["cantidad"] for i in invitados),
                           feriado=store.feriados(fecha.year).get(fecha),
                           ayer=(fecha - timedelta(days=1)).isoformat(), manana=(fecha + timedelta(days=1)).isoformat())


def _texto_no_se_anoto(t: dict, fecha: date) -> str:
    nombre = t["nombre"].split()[-2] if len(t["nombre"].split()) >= 3 else t["nombre"].split()[0]
    cuando = "hoy" if fecha == hoy() else f"el {fecha.strftime('%d/%m')}"
    return f"Hola {nombre}, {cuando} no se anotó en el comedor. Si va a comer, pase por la tablet. Saludos, Intela."


@app.route("/cafeteria")
def cafeteria_vieja():
    return redirect(url_for("comedor"))


# ==========================================================================
# Contabilidad
# ==========================================================================
@app.route("/admin/entrar", methods=["GET", "POST"])
def admin_entrar():
    if request.method == "POST":
        u = store.usuario_por_nombre((request.form.get("usuario") or "").strip().lower())
        if u and check_password_hash(u["clave_hash"], request.form.get("clave") or ""):
            rol = u.get("rol") or "contabilidad"
            session["usuario"] = {"id": u["id"], "usuario": u["usuario"], "nombre": u["nombre"], "rol": rol}
            if rol == "comedor":
                session.permanent = True
                return redirect(url_for("comedor_dia"))
            session.permanent = False  # contabilidad: se cierra con el navegador
            return redirect(_adonde_iba() or url_for("admin"))
        flash("Usuario o clave incorrectos.", "error")
    return render_template("entrar.html", admin=True)


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
                                         hoy() if saldo is not None else None,
                                         leer_perfil(request.form))
            flash(f"{nombre} cargado.", "ok")
            return redirect(url_for("admin_trabajador", id_=id_))
        except ValueError as exc:
            flash(str(exc), "error")
    incluir = request.args.get("todos") == "1"
    q = (request.args.get("q") or "").strip()
    filas = [dict(t, v=_resumen(t)) for t in store.trabajadores(incluir_inactivos=incluir)
             if not q or _coincide(t, q)]
    return render_template("admin.html", filas=filas, incluir=incluir, q=q,
                           abrir_alta=request.method == "POST")


def _coincide(t: dict, q: str) -> bool:
    """Busca por nombre, cédula o área, sin importar mayúsculas ni acentos."""
    import unicodedata
    def plano(x):
        return unicodedata.normalize("NFKD", str(x or "")).encode("ascii", "ignore").decode().lower()
    aguja = plano(q)
    return all(p in plano(f"{t['nombre']} {t['cedula']} {t.get('area') or ''}") for p in aguja.split())


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
        return redirect(url_for("admin_trabajador", id_=id_))
    return render_template("admin_trabajador.html", t=t, v=_resumen(t),
                           vacaciones=store.vacaciones(id_), ajustes=store.ajustes(id_))


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
        store.editar_trabajador(t["id"], cedula, nombre, ingreso, leer_perfil(f))
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
        tipo = leer_tipo_ausencia(f.get("tipo") or "vacaciones")
        store.agregar_vacacion(t["id"], desde, hasta, dias, (f.get("nota") or "").strip(), quien, tipo)
        flash(f"{num(dias)} días de {store.TIPOS_AUSENCIA[tipo][0].lower()} cargados.", "ok")
    elif accion == "vacacion_editar":
        tipo = leer_tipo_ausencia(f.get("tipo"))
        desde, hasta = leer_fecha(f.get("desde", "")), leer_fecha(f.get("hasta", ""))
        dias = leer_decimal(f.get("dias") or str(vacaciones.dias_entre(desde, hasta)), "Los días")
        if dias <= 0:
            raise ValueError("Los días tienen que ser más que cero.")
        store.editar_vacacion(int(f.get("id", 0)), tipo, desde, hasta, dias, (f.get("nota") or "").strip())
        flash("Período corregido.", "ok")
    elif accion == "vacacion_borrar":
        store.borrar_vacacion(int(f.get("id", 0)), quien)
        flash("Período borrado. Queda en Historial por si hay que recuperarlo.", "ok")
    elif accion == "ajuste":
        dias = leer_decimal(f.get("dias", ""), "Los días")
        motivo = (f.get("motivo") or "").strip()
        if not motivo:
            raise ValueError("Escriba el motivo del ajuste.")
        store.agregar_ajuste(t["id"], dias, motivo, quien)
        flash("Ajuste cargado.", "ok")
    elif accion == "ajuste_borrar":
        store.borrar_ajuste(int(f.get("id", 0)), quien)
        flash("Ajuste borrado. Queda en Historial por si hay que recuperarlo.", "ok")
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
    """Cada fila: cédula · nombre · fecha de ingreso · [saldo de días] · [área]
    · [fecha de nacimiento] · [celular] · [días por año].

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
        celdas += [""] * (8 - len(celdas))
        try:
            cedula = limpiar_cedula(celdas[0])
            nombre = celdas[1]
            if not nombre:
                raise ValueError("Falta el nombre.")
            ingreso = leer_fecha(celdas[2])
            if ingreso > hoy():
                raise ValueError("La fecha de ingreso es futura.")
            saldo = leer_decimal(celdas[3], "El saldo") if celdas[3] else None
            perfil = leer_perfil({"area": celdas[4], "fecha_nacimiento": celdas[5],
                                  "celular": celdas[6], "dias_por_anio": celdas[7]})
            if cedula in vistas:
                raise ValueError("Cédula repetida en la planilla.")
            vistas.add(cedula)
            buenas.append({"cedula": cedula, "nombre": nombre, "fecha_ingreso": ingreso,
                           "saldo": saldo, "fila": n, **perfil})
        except ValueError as exc:
            malas.append({"fila": n, "texto": linea.strip()[:80], "motivo": str(exc)})
    return buenas, malas


def leer_pegado_vacaciones(texto: str, por_cedula: dict) -> tuple[list[dict], list[dict]]:
    """Períodos tomados, para cargar la historia de una vez. Cada fila:
    cédula · desde · hasta · [días] · [nota]. Los días, si no vienen, son los
    corridos entre las dos fechas."""
    lineas = [ln for ln in (texto or "").splitlines() if ln.strip()]
    if not lineas:
        return [], []
    sep = _separador(lineas[0])
    buenas, malas = [], []
    for n, linea in enumerate(lineas, start=1):
        celdas = [c.strip().strip('"') for c in linea.split(sep)]
        if n == 1 and not re.search(r"\d", celdas[0]):
            continue
        celdas += [""] * (5 - len(celdas))
        try:
            cedula = limpiar_cedula(celdas[0])
            t = por_cedula.get(cedula)
            if not t:
                raise ValueError("No hay ningún trabajador con esa cédula.")
            desde, hasta = leer_fecha(celdas[1]), leer_fecha(celdas[2])
            propuesto = vacaciones.dias_entre(desde, hasta)
            dias = leer_decimal(celdas[3], "Los días") if celdas[3] else float(propuesto)
            if dias <= 0:
                raise ValueError("Los días tienen que ser más que cero.")
            buenas.append({"fila": n, "cedula": cedula, "trabajador_id": t["id"], "nombre": t["nombre"],
                           "desde": desde, "hasta": hasta, "dias": dias, "nota": celdas[4][:120]})
        except ValueError as exc:
            malas.append({"fila": n, "texto": linea.strip()[:80], "motivo": str(exc)})
    return buenas, malas


def cargar_vacaciones_lote(filas: list[dict], quien: str) -> dict:
    """Carga los períodos que no estén ya (mismo trabajador, desde y hasta)."""
    nuevos = repetidos = 0
    ya: dict[int, set] = {}
    for f in filas:
        tid = f["trabajador_id"]
        if tid not in ya:
            ya[tid] = {(v["desde"], v["hasta"]) for v in store.vacaciones(tid)}
        if (f["desde"], f["hasta"]) in ya[tid]:
            repetidos += 1
            continue
        store.agregar_vacacion(tid, f["desde"], f["hasta"], f["dias"], f["nota"], quien)
        ya[tid].add((f["desde"], f["hasta"]))
        nuevos += 1
    return {"nuevos": nuevos, "repetidos": repetidos}


@app.route("/admin/carga", methods=["GET", "POST"])
@requiere_admin
def admin_carga():
    que = request.values.get("que", "trabajadores")
    texto = request.form.get("texto", "") if request.method == "POST" else ""
    confirmar = request.method == "POST" and request.form.get("confirmar") == "1"
    if que == "vacaciones":
        por_cedula = {t["cedula"]: t for t in store.trabajadores(incluir_inactivos=True)}
        buenas, malas = leer_pegado_vacaciones(texto, por_cedula) if texto else ([], [])
        if confirmar and buenas:
            r = cargar_vacaciones_lote(buenas, g.usuario["usuario"])
            flash(f"Listo: {r['nuevos']} períodos cargados, {r['repetidos']} ya estaban.", "ok")
            return redirect(url_for("admin"))
        return render_template("admin_carga_vacaciones.html", texto=texto, buenas=buenas, malas=malas)
    buenas, malas = leer_pegado(texto) if texto else ([], [])
    if confirmar and buenas:
        r = store.cargar_lote(buenas, hoy())
        flash(f"Listo: {r['nuevos']} nuevos, {r['actualizados']} actualizados.", "ok")
        return redirect(url_for("admin"))
    existentes = {t["cedula"] for t in store.trabajadores(incluir_inactivos=True)} if buenas else set()
    return render_template("admin_carga.html", texto=texto, buenas=buenas, malas=malas,
                           existentes=existentes)


# --------------------------------------------------------------------------
# Pedidos de los trabajadores (y los datos que cambiaron desde el perfil)
# --------------------------------------------------------------------------
@app.route("/admin/solicitudes", methods=["GET", "POST"])
@requiere_admin
def admin_solicitudes():
    if request.method == "POST":
        f = request.form
        try:
            accion = f.get("accion", "")
            if accion == "visto":
                store.marcar_cambio_visto(int(f.get("id", 0)))
                return redirect(url_for("admin_solicitudes"))
            if accion == "borrar":
                if store.borrar_solicitud(int(f.get("id", 0) or 0)):
                    flash("Pedido borrado.", "ok")
                else:
                    raise ValueError("Sólo se borra un pedido rechazado o cancelado.")
                return redirect(url_for("admin_solicitudes"))
            if accion == "cargar":
                t = store.trabajador_por_cedula(limpiar_cedula(f.get("cedula", "")))
                if not t:
                    raise ValueError("No hay ningún trabajador con esa cédula.")
                tipo = leer_tipo_ausencia(f.get("tipo"))
                desde, hasta = leer_fecha(f.get("desde", "")), leer_fecha(f.get("hasta", ""))
                dias = leer_decimal(f.get("dias") or str(vacaciones.dias_entre(desde, hasta)), "Los días")
                if dias <= 0:
                    raise ValueError("Los días tienen que ser más que cero.")
                store.agregar_vacacion(t["id"], desde, hasta, dias, (f.get("nota") or "").strip() or "cargado en la oficina",
                                       g.usuario["usuario"], tipo)
                flash(f"{t['nombre']}: {num(dias)} días de {store.TIPOS_AUSENCIA[tipo][0].lower()} cargados.", "ok")
                return redirect(url_for("admin_solicitudes"))
            if accion == "editar":
                p = store.solicitud(int(f.get("id", 0) or 0))
                if not p or p["estado"] != "aprobada" or not p["vacacion_id"]:
                    raise ValueError("Sólo se corrige un pedido aprobado.")
                desde, hasta = leer_fecha(f.get("desde", "")), leer_fecha(f.get("hasta", ""))
                dias = leer_decimal(f.get("dias") or str(vacaciones.dias_entre(desde, hasta)), "Los días")
                if dias <= 0:
                    raise ValueError("Los días tienen que ser más que cero.")
                store.editar_vacacion(p["vacacion_id"], p["tipo"], desde, hasta, dias, f"pedido #{p['id']}" + (f" · {p['nota']}" if p.get("nota") else ""))
                flash(f"Corregido: {p['nombre']}, {num(dias)} días del {desde.strftime('%d/%m')} al {hasta.strftime('%d/%m')}.", "ok")
                return redirect(url_for("admin_solicitudes"))
            if accion == "deshacer":
                p = store.solicitud(int(f.get("id", 0) or 0))
                if not p or p["estado"] != "aprobada":
                    raise ValueError("Ese pedido no está aprobado.")
                store.cancelar_solicitud_aprobada(p["id"], (f.get("respuesta") or "").strip() or "cancelado por contabilidad",
                                                  g.usuario["usuario"])
                flash(f"Pedido de {p['nombre']} cancelado: el período se sacó de la ficha.", "ok")
                return redirect(url_for("admin_solicitudes", avisar=p["id"]))
            p = store.solicitud(int(f.get("id", 0) or 0))
            if not p or p["estado"] != "pendiente":
                raise ValueError("Ese pedido ya no está pendiente.")
            quien = g.usuario["usuario"]
            if accion == "aprobar":
                dias = leer_decimal(f.get("dias") or str(p["dias"]), "Los días")
                if dias <= 0:
                    raise ValueError("Los días tienen que ser más que cero.")
                nota = f"pedido #{p['id']}" + (f" · {p['nota']}" if p.get("nota") else "")
                vid = store.agregar_vacacion(p["trabajador_id"], p["desde"], p["hasta"], dias,
                                             nota, quien, p["tipo"])
                store.responder_solicitud(p["id"], "aprobada", (f.get("respuesta") or "").strip(), quien, vid)
                flash(f"Aprobado: {num(dias)} días de {store.TIPOS_AUSENCIA[p['tipo']][0].lower()} "
                      f"para {p['nombre']}.", "ok")
            elif accion == "rechazar":
                motivo = (f.get("respuesta") or "").strip()
                if not motivo:
                    raise ValueError("Escriba por qué se rechaza: el trabajador lo va a leer.")
                store.responder_solicitud(p["id"], "rechazada", motivo, quien)
                flash(f"Rechazado el pedido de {p['nombre']}.", "ok")
            else:
                raise ValueError("No sé qué hacer con eso.")
            return redirect(url_for("admin_solicitudes", avisar=p["id"]))
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("admin_solicitudes"))
    pendientes = [dict(p, saldo=_resumen(store.trabajador(p["trabajador_id"]))["saldo"])
                  for p in store.solicitudes_pendientes()]
    avisar = None
    id_avisar = request.args.get("avisar")
    if id_avisar and id_avisar.isdigit():
        p = store.solicitud(int(id_avisar))
        if p and p["estado"] in ("aprobada", "rechazada", "cancelada"):
            avisar = dict(p, whatsapp=link_whatsapp(p["celular"], _texto_aviso(p)))
    return render_template("admin_solicitudes.html", pendientes=pendientes,
                           respondidas=store.solicitudes_respondidas(),
                           cambios=store.cambios_perfil_sin_ver(), avisar=avisar,
                           trabajadores=store.trabajadores())


def _texto_aviso(p: dict) -> str:
    nombre = p["nombre"].split()[0] if p["nombre"] else ""
    que = store.TIPOS_AUSENCIA[p["tipo"]][0].lower()
    cuando = f"del {p['desde'].strftime('%d/%m')} al {p['hasta'].strftime('%d/%m')}"
    if p["estado"] == "aprobada":
        return f"Hola {nombre}, su pedido de {que} {cuando} está aprobado. Saludos, Intela."
    if p["estado"] == "cancelada":
        return f"Hola {nombre}, su pedido de {que} {cuando} quedó cancelado ({p['respuesta']}). Saludos, Intela."
    return f"Hola {nombre}, su pedido de {que} {cuando} no se pudo aprobar: {p['respuesta']}. Saludos, Intela."


@app.route("/admin/historial", methods=["GET", "POST"])
@requiere_admin
def admin_historial():
    """Lo que se borró: períodos y ajustes, con quién y cuándo. Se puede recuperar."""
    if request.method == "POST":
        que, id_ = request.form.get("que"), int(request.form.get("id", 0) or 0)
        if que == "periodo":
            store.recuperar_vacacion(id_)
        elif que == "ajuste":
            store.recuperar_ajuste(id_)
        flash("Recuperado: vuelve a contar en la ficha.", "ok")
        return redirect(url_for("admin_historial"))
    return render_template("admin_historial.html", filas=store.historial())


# --------------------------------------------------------------------------
# El cuadro de comidas del mes
# --------------------------------------------------------------------------
@app.route("/admin/comidas", methods=["GET", "POST"])
@requiere_sesion
def admin_comidas():
    anio, mes = leer_mes(request.values.get("mes"))
    if request.method == "POST":
        try:
            fecha = leer_fecha(request.form.get("fecha", ""))
            tipo = leer_tipo_comida(request.form.get("tipo"))
            t = store.trabajador_por_cedula(limpiar_cedula(request.form.get("cedula", "")))
            if not t:
                raise ValueError("No hay ningún trabajador con esa cédula.")
            if fecha > hoy():
                raise ValueError("La fecha no puede ser futura.")
            store.marcar_comida(t["id"], fecha, tipo, g.usuario["usuario"])
            flash(f"{t['nombre']}: {tipo} del {fecha.strftime('%d/%m')} marcado.", "ok")
            anio, mes = fecha.year, fecha.month
        except ValueError as exc:
            flash(str(exc), "error")
        return redirect(url_for("admin_comidas", mes=f"{anio}-{mes:02d}"))
    dias_mes = [date(anio, mes, d) for d in range(1, calendar.monthrange(anio, mes)[1] + 1)]
    marcados = store.comidas_de_todos(anio, mes)
    inv = store.invitados_del_mes(anio, mes)
    fer = store.feriados(anio)
    filas = []
    for d in dias_mes:
        if d > hoy():
            break
        a = sum(1 for m in marcados.values() if (d, "almuerzo") in m)
        c = sum(1 for m in marcados.values() if (d, "cena") in m)
        i_ = sum(inv.get(d, {}).values())
        if a + c + i_ == 0 and (d.weekday() >= 5 or d in fer):
            continue  # un sábado, domingo o feriado sin nadie no ocupa renglón
        filas.append({"fecha": d, "almuerzo": a, "cena": c, "invitados": i_,
                      "gris": d.weekday() >= 5 or d in fer, "feriado": fer.get(d)})
    totales = {"almuerzo": sum(f["almuerzo"] for f in filas), "cena": sum(f["cena"] for f in filas),
               "invitados": sum(f["invitados"] for f in filas)}
    totales["total"] = totales["almuerzo"] + totales["cena"] + totales["invitados"]
    return render_template("admin_comidas.html", anio=anio, mes=mes, filas=filas, totales=totales,
                           anterior=mes_anterior(anio, mes), siguiente=mes_siguiente(anio, mes),
                           trabajadores=store.trabajadores())


@app.route("/admin/comidas/imprimir")
@requiere_sesion
def admin_comidas_imprimir():
    """El cuadro trabajador × día, para pagarle a la cafetería."""
    anio, mes = leer_mes(request.values.get("mes"))
    dias = [date(anio, mes, d) for d in range(1, calendar.monthrange(anio, mes)[1] + 1)]
    marcados = store.comidas_de_todos(anio, mes)
    filas = []
    for t in store.trabajadores(incluir_inactivos=True):
        suyos = marcados.get(t["id"], set())
        if not t["activo"] and not suyos:
            continue
        filas.append({"t": t, "marcados": suyos, "totales": _totales(suyos)})
    totales = {tipo: sum(f["totales"][tipo] for f in filas) for tipo, _ in TIPOS_COMIDA}
    inv = store.invitados_del_mes(anio, mes)
    totales["invitados"] = sum(sum(v.values()) for v in inv.values())
    return render_template("admin_comidas_imprimir.html", anio=anio, mes=mes, dias=dias, filas=filas,
                           totales=totales, feriados=store.feriados(anio))


@app.route("/admin/feriados", methods=["GET", "POST"])
@requiere_admin
def admin_feriados():
    if request.method == "POST":
        try:
            if request.form.get("accion") == "borrar":
                store.borrar_feriado(leer_fecha(request.form.get("fecha", "")))
            else:
                nombre = (request.form.get("nombre") or "").strip()
                if not nombre:
                    raise ValueError("Falta el nombre del feriado.")
                store.agregar_feriado(leer_fecha(request.form.get("fecha", "")), nombre)
        except ValueError as exc:
            flash(str(exc), "error")
        return redirect(url_for("admin_feriados"))
    return render_template("admin_feriados.html", feriados=store.feriados())


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
                rol = f.get("rol") or "contabilidad"
                if rol not in store.ROLES:
                    raise ValueError("Falta decir si es de contabilidad o del comedor.")
                store.crear_usuario(usuario, generate_password_hash(clave), nombre, rol)
                flash(f"Usuario {usuario} creado.", "ok")
            elif accion == "clave":
                clave = f.get("clave") or ""
                if len(clave) < 6:
                    raise ValueError("La clave tiene que tener al menos 6 caracteres.")
                store.cambiar_clave(int(f.get("id", 0)), generate_password_hash(clave))
                flash("Clave cambiada.", "ok")
            elif accion == "nuevo_link":
                import secrets
                store.poner_configuracion("clave_comedor", secrets.token_urlsafe(9))
                flash("Link nuevo. El anterior ya no sirve: cámbielo en la tablet.", "ok")
            elif accion in ("activar", "desactivar"):
                id_ = int(f.get("id", 0))
                if accion == "desactivar" and id_ == g.usuario["id"]:
                    raise ValueError("No puede desactivar su propio usuario.")
                store.activar_usuario(id_, accion == "activar")
                flash("Listo.", "ok")
        except ValueError as exc:
            flash(str(exc), "error")
        return redirect(url_for("admin_usuarios"))
    return render_template("admin_usuarios.html", usuarios=store.usuarios(),
                           link_comedor=url_for("comedor_t", token=clave_comedor(), _external=True))


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

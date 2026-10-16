"""Trabajadores — Intela.

Para el trabajador (desde el celular, sólo con la cédula):

    /            poner la cédula
    /yo → /yo/vacaciones  cuántos días le quedan, y pedir más
    /yo/perfil      quién es para la empresa: área, desde cuándo, cuántos días gana por año

Para el comedor:

    /comedor/t/<clave>       la tablet: cédula → confirmar → invitados (sin sesión, link con clave)
    /comedor/dia             los comensales de cada día (el usuario del comedor sólo mira)

Para contabilidad (con usuario y clave):

    /admin                   la lista de trabajadores con el saldo de cada uno
    /admin/trabajador/N      la ficha: vacaciones tomadas, ajustes, comidas
    /admin/solicitudes       los pedidos de los trabajadores (aprobar / rechazar / deshacer)
    /admin/historial         lo borrado (períodos, ajustes), para recuperarlo si hizo falta
    /admin/carga             pegar la planilla para cargar a todos de una vez
    /admin/comidas           el cuadro del mes para pagarle al comedor
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
                   send_file, session, url_for)
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.security import check_password_hash, generate_password_hash

import config
import constancia as const
import correo
import roles
import store
import vacaciones

logging.basicConfig(level=logging.INFO)

app = Flask(__name__)
app.secret_key = config.SECRET_KEY
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=config.DIAS_SESION_TRABAJADOR)
app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024  # una foto del celular entra; se achica al guardar
# El rol de pagos viaja escondido en el formulario entre «Revisar» y «Cargar»
# (base64): sin esto Werkzeug corta los campos de texto en 500 KB.
app.config["MAX_FORM_MEMORY_SIZE"] = 8 * 1024 * 1024

CARPETA = os.path.dirname(os.path.abspath(__file__))

MESES = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
DIAS_CORTOS = ["L", "M", "M", "J", "V", "S", "D"]
TIPOS_COMIDA = (("almuerzo", "A"), ("cena", "C"))
# Las áreas de la fábrica, como las usa contabilidad en su planilla (Dep). Desde la
# planilla de octubre 2026: CC pasó a AC y KK a TJ (Tamara 14/10/2026, con los nombres).
NOMBRES_AREA = {"ADM": "Administración", "AC": "Acabados", "BO": "Bodega",
                "MC": "Mantenimiento", "TJ": "Tejeduría", "TT": "Tintorería"}
AREAS = tuple(NOMBRES_AREA)


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
    """La sesión se revalida contra la base en cada pedido: si el usuario se
    desactivó o le cambiaron el rol, la cookie deja de servir al instante (antes
    un usuario del comedor desactivado seguía entrando 30 días)."""
    g.usuario = None
    u = session.get("usuario")
    if not u or ERROR_ARRANQUE or not u.get("id") or not u.get("rol"):
        if u:
            session.pop("usuario", None)
        return
    try:
        vivo = store.usuario(u["id"])
    except Exception:  # noqa: BLE001
        vivo = None
    if not vivo or not vivo["activo"] or vivo["rol"] != u["rol"]:
        session.pop("usuario", None)
        return
    # los permisos del rol de pagos salen de la base, nunca de la cookie
    g.usuario = dict(u, revisa_roles=bool(vivo.get("revisa_roles")), firma_roles=bool(vivo.get("firma_roles")),
                     tiene_firma=bool(vivo.get("tiene_firma")))


# --------------------------------------------------------------------------
# Ayudas
# --------------------------------------------------------------------------
def es_contabilidad() -> bool:
    return bool(g.get("usuario")) and g.usuario.get("rol") == "contabilidad"


def requiere_admin(f):
    """Sólo contabilidad. Un usuario del comedor va a Comensales."""
    @wraps(f)
    def wrapper(*a, **kw):
        u = g.get("usuario")
        if not u:
            return redirect(url_for("admin_entrar", next=request.path))
        if u.get("rol") != "contabilidad":
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


def puede_entrar(t: dict | None) -> bool:
    """Activo y no pausado: entra con su cédula y se registra en el comedor."""
    return bool(t) and t["activo"] and not t.get("pausado_en")


def tiene_vacaciones(t: dict) -> bool:
    """Sólo los de planta. Temporales y jubilados no acumulan (Tamara 14/10/2026)."""
    return (t.get("tipo") or "planta") == "planta"


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
    sin_firmar = avisos = 0
    if g.get("usuario") and g.usuario.get("rol") == "contabilidad" and not ERROR_ARRANQUE:
        try:
            sin_firmar = store.sin_firmar_ultimo_mes()
        except Exception:  # noqa: BLE001
            sin_firmar = 0
        try:
            avisos = store.cuantos_avisos()
        except Exception:  # noqa: BLE001
            avisos = 0
    return {"n_roles_sin_firmar": sin_firmar, "MESES": MESES, "DIAS_CORTOS": DIAS_CORTOS, "TIPOS_COMIDA": TIPOS_COMIDA, "AREAS": AREAS, "NOMBRES_AREA": NOMBRES_AREA,
            "TIPOS_TRABAJADOR": store.TIPOS_TRABAJADOR, "n_avisos": avisos,
            "TURNOS": TURNOS, "TURNOS_ALMUERZO": TURNOS_ALMUERZO, "nombre_turno": nombre_turno,
            "TIPOS_AUSENCIA": store.TIPOS_AUSENCIA, "hoy": hoy(), "n_pendientes": pendientes,
            "cod_rol": cod_rol}


def cod_rol(concepto: str, descuento: bool = False) -> str:
    """El código del concepto como en el recibo de papel de WinAfin: el lugar en
    la lista (Sueldo 1, Horas 50% 2… Fondo de reserva 5; IESS 1, Anticipo 2…)."""
    lista = [x[1] for x in (roles.DESCUENTOS if descuento else roles.INGRESOS)]
    return str(lista.index(concepto) + 1) if concepto in lista else ""


@app.context_processor
def _rol_sin_firmar():
    """El globito de la pestaña «Rol de pagos»: el último rol del trabajador
    todavía no está firmado."""
    if not request.endpoint or not request.endpoint.startswith("yo") or ERROR_ARRANQUE:
        return {}
    tid = session.get("trabajador_id")
    try:
        ultimo = [r for r in store.roles_de(tid) if store.visible(r)][:1] if tid else []
    except Exception:  # noqa: BLE001
        return {}
    try:
        sin_constancia = bool(tid) and store.constancia_de(tid) is None
    except Exception:  # noqa: BLE001
        sin_constancia = False
    return {"rol_sin_firmar": sin_constancia or bool(ultimo and ultimo[0].get("pide_firma", True) and not ultimo[0]["firmado_en"])}


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
        if not puede_entrar(t):
            flash("No encontramos esa cédula. Pregunte en contabilidad.", "error")
            return render_template("entrar.html")
        # La cédula sola ya NO abre la sesión (Tamara 14/10/2026: «la contraseña tiene que
        # ser para todo»): pasa al paso de la contraseña, o a crearla la primera vez.
        # Sólo se toca la sesión del trabajador: contabilidad no se desloguea.
        import time
        session.pop("trabajador_id", None)
        if not pide_clave():
            # el interruptor de Usuarios todavía apagado: entra con la cédula, como antes
            session["trabajador_id"] = t["id"]
            session.permanent = True
            return redirect(url_for("yo"))
        session["entrando"] = {"id": t["id"], "hasta": time.time() + 15 * 60}
        return redirect(url_for("entrar_clave"))
    return render_template("entrar.html")


def _mi_trabajador():
    """El trabajador de la sesión, si entró con su contraseña. Si la cambió (o
    contabilidad la borró), las sesiones viejas se caen al instante."""
    id_ = session.get("trabajador_id")
    t = store.trabajador(id_) if id_ else None
    if not puede_entrar(t) or (pide_clave() and (not t.get("clave_rol_hash")
                                                 or session.get("clave_en") != str(t.get("clave_rol_en")))):
        session.pop("trabajador_id", None)
        session.pop("clave_en", None)
        return None
    return t


def pide_clave() -> bool:
    """El interruptor de Usuarios («Pedir contraseña para entrar»). Arranca apagado para
    probar el correo antes: si el correo no saliera, nadie podría entrar."""
    try:
        return store.configuracion("entrar_con_clave") == "si"
    except Exception:  # noqa: BLE001
        return False


def _abrir_sesion_trabajador(t: dict) -> None:
    session.pop("entrando", None)
    session["trabajador_id"] = t["id"]
    session["clave_en"] = str(t.get("clave_rol_en"))
    session.permanent = True  # 30 días en su celular sin volver a escribirla


@app.route("/yo")
def yo():
    t = _mi_trabajador()
    if t and t.get("tipo") == "jubilado":
        return redirect(url_for("yo_rol"))
    return redirect(url_for("yo_vacaciones"))


@app.route("/yo/vacaciones")
def yo_vacaciones():
    t = _mi_trabajador()
    if not t:
        return redirect(url_for("entrar"))
    if t.get("tipo") == "jubilado":
        return redirect(url_for("yo_rol"))
    con_cert = {c["solicitud_id"] for c in store.certificados_de(t["id"]) if c["solicitud_id"]}
    return render_template("yo_vacaciones.html", t=t, v=_resumen(t), con_vacaciones=tiene_vacaciones(t),
                           vacaciones=store.vacaciones(t["id"]),
                           pedidos=store.solicitudes(t["id"]), con_cert=con_cert)


# Hasta cuántos días para atrás se puede pedir (una enfermedad se avisa después).
DIAS_ATRAS_PEDIDO = 30

# El certificado médico: una foto (se achica a JPEG) o un PDF (se guarda como viene, hasta 5 MB).
LADO_MAXIMO_FOTO = 1600
PESO_MAXIMO_PDF = 5 * 1024 * 1024


def leer_certificado(archivo) -> tuple[bytes, str, str] | None:
    """El archivo subido → (datos, tipo, nombre). None si no mandaron nada.
    Una foto se convierte a JPEG de hasta 1600 px de lado (pesa ~200 KB en vez
    de 4 MB); un PDF se guarda tal cual. Otra cosa, avisa."""
    if archivo is None or not archivo.filename:
        return None
    datos = archivo.read()
    if not datos:
        return None
    nombre = os.path.basename(archivo.filename)[:120]
    if datos[:5] == b"%PDF-":
        if len(datos) > PESO_MAXIMO_PDF:
            raise ValueError("El PDF pesa más de 5 MB. Mande una foto o un PDF más chico.")
        return datos, "application/pdf", nombre
    try:
        from io import BytesIO
        from PIL import Image, ImageOps
        img = Image.open(BytesIO(datos))
        if img.width * img.height > 50_000_000:  # más que cualquier cámara de celular
            raise ValueError("demasiado grande")
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.thumbnail((LADO_MAXIMO_FOTO, LADO_MAXIMO_FOTO))
        salida = BytesIO()
        img.save(salida, "JPEG", quality=82, optimize=True)
        return salida.getvalue(), "image/jpeg", os.path.splitext(nombre)[0] + ".jpg"
    except Exception:  # noqa: BLE001
        raise ValueError("No pude leer el archivo. Mande una foto (JPG, PNG) o un PDF.")


@app.errorhandler(RequestEntityTooLarge)
def _muy_grande(_e):
    flash("El archivo es demasiado grande (más de 12 MB). Saque la foto de nuevo con menos resolución.", "error")
    return redirect(request.referrer or url_for("entrar"))


def _periodo_pisado(trabajador_id: int, desde: date, hasta: date, salvo: int | None = None) -> dict | None:
    """El período ya cargado en la ficha que se pisa con [desde, hasta], si hay
    (sin contar `salvo`, el que se está corrigiendo)."""
    for v in store.vacaciones(trabajador_id):
        if v["id"] != salvo and v["desde"] <= hasta and desde <= v["hasta"]:
            return v
    return None


def _rango(x: dict) -> str:
    """«02/02 al 16/02», o «19/02» si es un solo día."""
    d, h = x["desde"].strftime("%d/%m"), x["hasta"].strftime("%d/%m")
    return d if d == h else f"{d} al {h}"


def _controlar_periodo(t: dict, tipo: str, desde: date, hasta: date, salvo: int | None = None) -> str | None:
    """Lo que contabilidad tiene que saber al cargar o aprobar un período: si se
    pisa con uno ya cargado se FRENA (ValueError); si es anterior al saldo
    inicial NO descuenta, y devuelve el aviso para mostrarlo una vez guardado."""
    pisado = _periodo_pisado(t["id"], desde, hasta, salvo)
    if pisado:
        raise ValueError(f"{t['nombre']} ya tiene cargado {store.TIPOS_AUSENCIA[pisado['tipo']][0].lower()} "
                         f"del {_rango(pisado)}: se pisan. Corrija ese período en la ficha, o rechace el pedido.")
    if _anterior_al_saldo_inicial(t, tipo, desde):
        return (f"Ojo: empieza antes del saldo inicial ({t['fecha_saldo_inicial'].strftime('%d/%m/%Y')}), así que "
                "NO descuenta del saldo (ya está contado en ese número). Si tiene que descontar, cargue un ajuste.")
    return None


def _anterior_al_saldo_inicial(t: dict, tipo: str, desde: date) -> bool:
    """Un período que descontaría pero empieza antes de la foto del saldo inicial
    no resta (la foto ya lo incluye)."""
    corte = t.get("fecha_saldo_inicial")
    return bool(corte and desde < corte and store.TIPOS_AUSENCIA[tipo][1])


def _avisar(aviso: str | None) -> None:
    if aviso:
        flash(aviso, "error")


def nombre_de_pila(nombre: str | None) -> str:
    """Cómo saludarlo por WhatsApp. El padrón viene como en la nómina,
    APELLIDO APELLIDO NOMBRE (NOMBRE): con tres o más palabras, la tercera; con
    menos, la primera. Una sola regla para todos los avisos."""
    partes = (nombre or "").split()
    if not partes:
        return ""
    return partes[2] if len(partes) >= 3 else partes[0]


@app.route("/yo/pedir", methods=["POST"])
def yo_pedir():
    t = _mi_trabajador()
    if not t:
        return redirect(url_for("entrar"))
    f = request.form
    try:
        tipo = leer_tipo_ausencia(f.get("tipo"))
        if not tiene_vacaciones(t) and store.TIPOS_AUSENCIA[tipo][1]:
            raise ValueError("Como temporal, todavía no tiene vacaciones. Pregunte en contabilidad.")
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
        pisado = _periodo_pisado(t["id"], desde, hasta)
        if pisado:
            raise ValueError(f"Esos días ya están cargados ({_rango(pisado)}). "
                             "Si hay un error, hable con contabilidad.")
        cert = leer_certificado(request.files.get("certificado"))
        sid = store.crear_solicitud(t["id"], tipo, desde, hasta, dias, (f.get("nota") or "").strip()[:200])
        if cert:
            store.guardar_certificado(t["id"], *cert, "trabajador", solicitud_id=sid)
            flash("Pedido enviado con el certificado. Contabilidad le responde aquí mismo.", "ok")
        else:
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


@app.route("/yo/certificado", methods=["POST"])
def yo_certificado():
    """La foto del certificado médico, para un pedido que ya mandó (pendiente o aprobado)."""
    t = _mi_trabajador()
    if not t:
        return redirect(url_for("entrar"))
    p = store.solicitud(int(request.form.get("id", 0) or 0))
    try:
        if not p or p["trabajador_id"] != t["id"] or p["estado"] not in ("pendiente", "aprobada"):
            raise ValueError("Ese pedido no está.")
        cert = leer_certificado(request.files.get("certificado"))
        if not cert:
            raise ValueError("Elija la foto del certificado.")
        store.guardar_certificado(t["id"], *cert, "trabajador", solicitud_id=p["id"], vacacion_id=p.get("vacacion_id"))
        flash("Certificado guardado. Contabilidad ya lo puede ver.", "ok")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("yo_vacaciones"))


@app.route("/yo/perfil", methods=["GET", "POST"])
def yo_perfil():
    t = _mi_trabajador()
    if not t:
        return redirect(url_for("entrar"))
    if request.method == "POST" and request.form.get("accion") == "clave":
        # cambiar la contraseña: este celular sigue adentro, los otros se cierran solos
        try:
            if not check_password_hash(t["clave_rol_hash"], request.form.get("actual") or ""):
                raise ValueError("La contraseña actual no es correcta.")
            clave = leer_clave_nueva(request.form.get("clave") or "", request.form.get("repetir") or "", t.get("cedula"))
            store.guardar_clave_rol(t["id"], generate_password_hash(clave), t.get("email"))
            _abrir_sesion_trabajador(store.trabajador(t["id"]))
            flash("Listo: cambió su contraseña. En los otros celulares tiene que entrar de nuevo.", "ok")
        except ValueError as exc:
            flash(str(exc), "error")
        return redirect(url_for("yo_perfil"))
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
    session.pop("clave_en", None)
    session.pop("entrando", None)
    return redirect(url_for("entrar"))


# ==========================================================================
# El comedor. Dos entradas:
#   * la tablet del mostrador abre /comedor/t/<clave> (un link con clave
#     adentro, sin sesión, que no vence): el trabajador escribe su cédula,
#     confirma, y puede anotar invitados;
#   * la persona del comedor (usuario con rol «comedor») entra con su usuario y
#     SÓLO VE los comensales de cada día; contabilidad además registra y quita.
# ==========================================================================
HORA_CENA = 15  # desde las 15:00 (de Ecuador) la comida es la cena; antes, el almuerzo
MINUTOS_PARA_DESHACER = 10
MAX_INVITADOS = 3


def comida_de_ahora() -> str:
    return "cena" if datetime.now(ZoneInfo(config.ZONA)).hour >= HORA_CENA else "almuerzo"


# Los turnos de cada comida: media hora cada uno.
TURNOS = {"almuerzo": ("12:00", "12:30", "13:00", "13:30"), "cena": ("18:30", "19:00")}
TURNOS_ALMUERZO = TURNOS["almuerzo"]


def nombre_turno(turno: str | None) -> str:
    """'12:30' → '12:30 a 13:00'."""
    if not turno or not re.fullmatch(r"\d\d:\d\d", turno):
        return ""
    h, m = int(turno[:2]), int(turno[3:])
    fin = f"{h + (m + 30) // 60:02d}:{(m + 30) % 60:02d}"
    return f"{turno} a {fin}"


def turno_de_ahora(tipo: str = "almuerzo") -> str:
    """El turno que corresponde a la hora de Ecuador: antes del primero, el primero;
    después del último, el último. Es la opción que sale elegida en la tablet."""
    ahora = datetime.now(ZoneInfo(config.ZONA)).strftime("%H:%M")
    turnos = TURNOS[tipo]
    elegido = turnos[0]
    for t in turnos:
        if ahora >= t:
            elegido = t
    return elegido


def leer_horario(texto: str | None) -> tuple[str, str]:
    """'cena|19:00' → ('cena', '19:00'): el desplegable de Comensales elige comida y horario juntos."""
    tipo, _, turno = (texto or "").partition("|")
    if tipo not in TURNOS or turno not in TURNOS[tipo]:
        raise ValueError("Elija un horario.")
    return tipo, turno


def leer_turno(texto: str | None, tipo: str) -> str:
    """Vacío → el de ahora (toda comida lleva horario)."""
    if not texto:
        return turno_de_ahora(tipo)
    if texto not in TURNOS[tipo]:
        raise ValueError("Ese horario no existe.")
    return texto


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
        if not puede_entrar(t):
            return _tablet("comedor.html", error="No encontramos esa cédula. Pregunte en contabilidad.", cedula=cedula)
        if store.comida_marcada(t["id"], h, tipo):
            return _tablet("comedor_listo.html", t=t, ya_estaba=True, invitados=_invitados_de(t, h, tipo),
                           turno=store.turno_marcado(t["id"], h, tipo) or turno_de_ahora(tipo))
        return _tablet("comedor_confirmar.html", t=t, fecha=h, turno=turno_de_ahora(tipo),
                       ya_invitados=_invitados_de(t, h, tipo) if t.get("puede_invitar") else 0)
    return _tablet("comedor.html")


def _invitados_de(t: dict, fecha: date, tipo: str) -> int:
    return sum(i["cantidad"] for i in store.invitados_del_dia(fecha) if i["trabajador_id"] == t["id"] and i["tipo"] == tipo)


def _trabajador_del_form():
    cedula = limpiar_cedula(request.form.get("cedula", ""))
    tipo = leer_tipo_comida(request.form.get("tipo"))
    t = store.trabajador_por_cedula(cedula)
    if not puede_entrar(t):
        raise ValueError("No encontramos esa cédula.")
    return t, tipo


@app.route("/comedor/confirmar", methods=["POST"], endpoint="comedor_confirmar")
@app.route("/comedor/t/<token>/confirmar", methods=["POST"], endpoint="comedor_confirmar_t")
@requiere_comedor
def comedor_confirmar():
    try:
        t, tipo = _trabajador_del_form()
        turno = leer_turno(request.form.get("turno"), tipo)
    except ValueError as exc:
        return _tablet("comedor.html", error=str(exc))
    # los invitados se eligen en la MISMA pantalla, debajo del horario (Tamara 14/10/2026: «no hagamos otra pantalla»)
    cantidad = int(request.form.get("cantidad") or 0) if (request.form.get("cantidad") or "0").isdigit() else 0
    descripcion = (request.form.get("descripcion") or "").strip()[:80]
    # «Sólo los invitados» (Tamara 14/10/2026): los invitados pueden comer antes que él, o él no come.
    # Se registran los invitados SIN su comida; cuando él venga, pone la cédula y registra la suya.
    solo_invitados = request.form.get("solo_invitados") == "1"
    ya = _invitados_de(t, hoy(), tipo) if t.get("puede_invitar") else 0
    if cantidad or solo_invitados:
        if not t.get("puede_invitar"):
            return _tablet("comedor.html", error=f"{t['nombre']} no tiene permiso para traer invitados. Pregunte en contabilidad.")
        error = None
        if not descripcion:
            error = "Escriba quiénes son los invitados (por ejemplo: proveedor, familiar)."
        elif not 1 <= cantidad <= MAX_INVITADOS:
            error = f"Los invitados son de 1 a {MAX_INVITADOS}."
        elif ya + cantidad > MAX_INVITADOS:
            error = (f"Ya registró {ya} {'invitado' if ya == 1 else 'invitados'}; "
                     f"el máximo es {MAX_INVITADOS} por comida.")
        if error:
            return _tablet("comedor_confirmar.html", t=t, fecha=hoy(), turno=turno, cantidad=cantidad,
                           ya_invitados=ya, error=error)
    if not solo_invitados:
        store.marcar_comida(t["id"], hoy(), tipo, _quien_marca(), turno)
    if cantidad:
        store.agregar_invitados(t["id"], hoy(), tipo, cantidad, descripcion, _quien_marca(), turno)
    return _tablet("comedor_listo.html", t=t, tipo=tipo, recien=True, invitados=ya + cantidad, turno=turno,
                   invitados_anotados=cantidad, con_comida=not solo_invitados, solo_invitados=solo_invitados)


@app.route("/comedor/invitados", methods=["POST"], endpoint="comedor_invitados")
@app.route("/comedor/t/<token>/invitados", methods=["POST"], endpoint="comedor_invitados_t")
@requiere_comedor
def comedor_invitados():
    try:
        t, tipo = _trabajador_del_form()
        if not t.get("puede_invitar"):
            raise ValueError(f"{t['nombre']} no tiene permiso para traer invitados. Pregunte en contabilidad.")
        cantidad = int(request.form.get("cantidad", 0) or 0)
        if not 1 <= cantidad <= MAX_INVITADOS:
            raise ValueError(f"Los invitados son de 1 a {MAX_INVITADOS}.")
        descripcion = (request.form.get("descripcion") or "").strip()[:80]
        if not descripcion:
            raise ValueError("Diga quiénes son los invitados (por ejemplo: proveedor, familiar).")
        ya = _invitados_de(t, hoy(), tipo)
        if ya + cantidad > MAX_INVITADOS:
            raise ValueError(f"Ya tiene {ya} {'invitado' if ya == 1 else 'invitados'} en {'este almuerzo' if tipo == 'almuerzo' else 'esta cena'}; el máximo es {MAX_INVITADOS}.")
    except ValueError as exc:
        return _tablet("comedor.html", error=str(exc))
    # los invitados pueden comer en otro horario que el suyo (llegan antes o después): se elige en la tablet
    try:
        turno = leer_turno(request.form.get("turno"), tipo) if request.form.get("turno") else None
    except ValueError:
        turno = None
    turno = turno or store.turno_marcado(t["id"], hoy(), tipo) or leer_turno(None, tipo)
    store.agregar_invitados(t["id"], hoy(), tipo, cantidad, descripcion, _quien_marca(), turno)
    return _tablet("comedor_listo.html", t=t, tipo=tipo, invitados_anotados=cantidad, invitados=ya + cantidad,
                   turno=turno, con_comida=False)


@app.route("/comedor/dia", methods=["GET", "POST"])
@requiere_sesion
def comedor_dia():
    """Los comensales de un día, por horario.

    El usuario del comedor SÓLO MIRA (decisión Tamara 17/09/2026: «no debe ver
    nada más que quién comió, nada de celulares»): ni registra, ni quita, ni ve
    la lista de los que faltan. Registrar y quitar es de contabilidad.
    """
    try:
        fecha = leer_fecha(request.values.get("fecha", "")) if request.values.get("fecha") else hoy()
    except ValueError:
        fecha = hoy()
    if fecha > hoy():
        fecha = hoy()  # no hay comensales de mañana
    if request.method == "POST":
        accion = request.form.get("accion", "")
        # el comedor puede AGREGAR comensales de HOY (Tamara 14/10/2026); cambiar y quitar, sólo contabilidad
        if not es_contabilidad() and not (accion == "marcar" and fecha == hoy()):
            abort(403)
        try:
            if accion == "marcar":
                if request.form.get("horario"):
                    tipo, turno = leer_horario(request.form.get("horario"))
                else:
                    tipo = leer_tipo_comida(request.form.get("tipo"))
                    turno = leer_turno(request.form.get("turno"), tipo)
                store.marcar_comida(int(request.form.get("trabajador_id", 0)), fecha, tipo,
                                    g.usuario["usuario"], turno)
            elif accion == "cambiar":
                tipo_nuevo, turno = leer_horario(request.form.get("horario"))
                store.cambiar_comida(int(request.form.get("trabajador_id", 0)), fecha,
                                     leer_tipo_comida(request.form.get("tipo")), tipo_nuevo, turno,
                                     g.usuario["usuario"])
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
    # el almuerzo agrupado por turno (los sin turno, al final)
    invitados = store.invitados_del_dia(fecha)
    # por comida, columnas por horario: [(turno, comensales, invitados)]; los 4 (o 2) turnos
    # siempre; «sin horario» sólo si quedó algo viejo registrado sin horario
    columnas = {}
    for tipo, _ in TIPOS_COMIDA:
        cols = []
        for turno in (*TURNOS[tipo], None):
            gente = [c for c in por_tipo[tipo] if c["turno"] == turno]
            inv = [i for i in invitados if i["tipo"] == tipo and i.get("turno") == turno]
            if turno or gente or inv:
                cols.append((turno, gente, inv))
        columnas[tipo] = cols
    faltan = []
    agrega = es_contabilidad() or fecha == hoy()  # el comedor agrega comensales de hoy, sin celulares
    if agrega:
        # el que almorzó también puede faltar la cena: sale con lo que ya tiene
        ya = {}
        for c in comieron:
            ya.setdefault(c["trabajador_id"], set()).add(c["tipo"])
        faltan = [dict(t, ya=ya.get(t["id"], set()),
                       whatsapp=link_whatsapp(t["celular"], _texto_sin_registrar(t, fecha))
                       if t["id"] not in ya and es_contabilidad() else None)
                  for t in store.trabajadores() if puede_entrar(t) and len(ya.get(t["id"], ())) < len(TIPOS_COMIDA)]
    return render_template("comedor_dia.html", fecha=fecha, por_tipo=por_tipo, columnas=columnas,
                           faltan=faltan, invitados=invitados, edita=es_contabilidad(), agrega=agrega,
                           horario_ahora=f"{comida_de_ahora()}|{turno_de_ahora(comida_de_ahora())}",
                           total_invitados=sum(i["cantidad"] for i in invitados),
                           feriado=store.feriados(fecha.year).get(fecha),
                           ayer=(fecha - timedelta(days=1)).isoformat(),
                           manana=(fecha + timedelta(days=1)).isoformat() if fecha < hoy() else None)


def _texto_sin_registrar(t: dict, fecha: date) -> str:
    cuando = "hoy" if fecha == hoy() else f"el {fecha.strftime('%d/%m')}"
    return (f"Hola {nombre_de_pila(t['nombre'])}, {cuando} no registró su comida en el comedor. "
            "Si va a comer, pase por la tablet. Saludos, Intela.")


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
            # Sin cédula se puede (Tamara 14/10/2026): queda en la campanita hasta ponerla.
            cedula = limpiar_cedula(request.form.get("cedula", "")) if (request.form.get("cedula") or "").strip() else None
            nombre = (request.form.get("nombre") or "").strip()
            if not nombre:
                raise ValueError("Falta el nombre.")
            ingreso = leer_fecha(request.form.get("fecha_ingreso", ""))
            if ingreso > hoy():
                raise ValueError("La fecha de ingreso no puede ser futura.")
            if cedula and store.trabajador_por_cedula(cedula):
                raise ValueError(f"La cédula {cedula} ya está cargada.")
            tipo = request.form.get("tipo") or "planta"
            if tipo not in store.TIPOS_TRABAJADOR:
                raise ValueError("Elija planta, temporal o jubilado.")
            saldo = (request.form.get("saldo") or "").strip()
            saldo = leer_decimal(saldo, "El saldo") if saldo else None
            id_ = store.crear_trabajador(cedula, nombre, ingreso, saldo,
                                         hoy() if saldo is not None else None,
                                         leer_perfil(request.form))
            if tipo != "planta":
                store.poner_tipo(id_, tipo)
            if not cedula:
                store.crear_aviso(id_, "Le falta la cédula. Sin cédula no puede entrar ni registrarse en el comedor.",
                                  g.usuario["usuario"], "sin_cedula")
            flash(f"{nombre} cargado." + ("" if cedula else " Falta la cédula: quedó en la campanita."), "ok")
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
    return all(p in plano(f"{t['nombre']} {t['cedula'] or ''} {t.get('area') or ''} {t.get('tipo') or ''}") for p in aguja.split())


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
    certs: dict[int, list] = {}
    for c in store.certificados_de(id_):
        if c["vacacion_id"]:
            certs.setdefault(c["vacacion_id"], []).append(c)
    return render_template("admin_trabajador.html", t=t, v=_resumen(t), avisos=store.avisos_de(id_),
                           vacaciones=store.vacaciones(id_), ajustes=store.ajustes(id_), certificados=certs,
                           roles_pago=store.roles_de(id_), constancia_roles=store.constancia_de(id_))


@app.route("/admin/trabajador/<int:id_>/imprimir")
@requiere_admin
def admin_trabajador_imprimir(id_):
    """La hoja de saldo de vacaciones, para imprimir y firmar."""
    t = store.trabajador(id_)
    if not t:
        abort(404)
    return render_template("admin_trabajador_imprimir.html", t=t, v=_resumen(t),
                           vacaciones=store.vacaciones(id_), ajustes=store.ajustes(id_),
                           emitido=datetime.now(ZoneInfo(config.ZONA)), quien=g.usuario["nombre"])


@app.route("/admin/certificado/<int:id_>")
@requiere_admin
def admin_certificado(id_):
    """La foto o el PDF del certificado, sólo para contabilidad."""
    c = store.certificado(id_)
    if not c:
        abort(404)
    from io import BytesIO
    return send_file(BytesIO(bytes(c["datos"])), mimetype=c["tipo_archivo"], download_name=c["nombre"])


def _accion_trabajador(t: dict, accion: str) -> None:
    f = request.form
    quien = g.usuario["usuario"]
    if accion == "invitados":
        store.poner_puede_invitar(t["id"], f.get("puede_invitar") == "1")
        return
    if accion == "prueba":
        store.marcar_prueba(t["id"], f.get("es_prueba") == "1")
        flash("Listo: marcado como trabajador de prueba." if f.get("es_prueba") == "1" else "Ya no es de prueba.", "ok")
        return
    if accion == "reiniciar_prueba":
        n = store.reiniciar_prueba(t["id"], quien)
        flash(f"Listo: {t['nombre']} quedó como nuevo. Sin contraseña ni correo"
              + (", sin constancia" if n["constancias"] else "")
              + (f", y {n['firmas']} {'rol sin firmar' if n['firmas'] == 1 else 'roles sin firmar'}" if n["firmas"] else "")
              + ". Sus recibos, vacaciones y comidas siguen igual.", "ok")
        return
    if accion == "cedula_banco":
        ced = limpiar_cedula(f.get("cedula_banco", "")) if (f.get("cedula_banco") or "").strip() else None
        if ced and ced == t.get("cedula"):
            ced = None  # la misma del padrón: no hace falta
        store.poner_cedula_banco(t["id"], ced)
        flash(f"Listo: cobra con la cédula {ced}." if ced else "Listo: cobra con la misma cédula del padrón.", "ok")
        return
    if accion == "borrar_clave_rol":
        store.borrar_clave_rol(t["id"])
        flash("Listo: se borró la contraseña. La vuelve a crear la próxima vez que entre.", "ok")
        return
    if accion == "certificado":
        vid = int(f.get("id", 0) or 0)
        if not any(v["id"] == vid for v in store.vacaciones(t["id"])):
            raise ValueError("Ese período no está.")
        cert = leer_certificado(request.files.get("certificado"))
        if not cert:
            raise ValueError("Elija la foto o el PDF del certificado.")
        p = store.solicitud_por_vacacion(vid)
        store.guardar_certificado(t["id"], *cert, quien, solicitud_id=p["id"] if p else None, vacacion_id=vid)
        flash("Certificado guardado.", "ok")
        return
    if accion == "editar":
        nombre = (f.get("nombre") or "").strip()
        if not nombre:
            raise ValueError("Falta el nombre.")
        if not (f.get("cedula") or "").strip() and not t["cedula"]:
            cedula = None  # todavía sin cédula: se puede guardar lo demás
        else:
            cedula = limpiar_cedula(f.get("cedula", ""))
            otro = store.trabajador_por_cedula(cedula)
            if otro and otro["id"] != t["id"]:
                raise ValueError(f"La cédula {cedula} ya es de {otro['nombre']}.")
        ingreso = leer_fecha(f.get("fecha_ingreso", ""))
        tipo = f.get("tipo") or t.get("tipo") or "planta"
        store.editar_trabajador(t["id"], cedula, nombre, ingreso, leer_perfil(f))
        if tipo != (t.get("tipo") or "planta"):
            store.poner_tipo(t["id"], tipo)
        if cedula and not t["cedula"]:
            store.resolver_avisos(t["id"], "sin_cedula", quien)
        flash("Datos guardados.", "ok")
    elif accion == "tipo":
        store.poner_tipo(t["id"], f.get("tipo") or "")
        flash(f"{t['nombre']} pasó a {store.TIPOS_TRABAJADOR[f.get('tipo')].lower()}.", "ok")
    elif accion == "pausar":
        store.pausar(t["id"], (f.get("motivo") or "").strip()[:200], quien)
        flash(f"{t['nombre']} quedó pausado. No se borró nada.", "ok")
    elif accion == "reanudar":
        store.reanudar(t["id"])
        store.resolver_avisos(t["id"], "no_en_planilla", quien)
        flash(f"{t['nombre']} vuelve a estar activo.", "ok")
    elif accion == "aviso":
        texto = (f.get("texto") or "").strip()[:300]
        if not texto:
            raise ValueError("Escriba qué hay que revisar.")
        store.crear_aviso(t["id"], texto, quien)
        flash("Aviso guardado en la campanita.", "ok")
    elif accion == "aviso_listo":
        store.resolver_aviso(int(f.get("id", 0) or 0), quien)
        flash("Aviso resuelto.", "ok")
    elif accion == "saldo_inicial":
        saldo = (f.get("saldo") or "").strip()
        if saldo:
            fecha = leer_fecha(f.get("fecha", ""))
            if fecha > hoy():
                raise ValueError("La fecha del saldo no puede ser futura.")
            store.poner_saldo_inicial(t["id"], leer_decimal(saldo, "El saldo"), fecha)
            flash("Saldo inicial guardado.", "ok")
        else:
            store.poner_saldo_inicial(t["id"], None, None)
            flash("Sin saldo inicial: se cuenta desde el ingreso.", "ok")
    elif accion == "baja":
        store.dar_de_baja(t["id"], leer_fecha(f.get("fecha_salida", "")))
        store.resolver_avisos(t["id"], "no_en_planilla", quien)
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
        aviso = _controlar_periodo(t, tipo, desde, hasta)
        store.agregar_vacacion(t["id"], desde, hasta, dias, (f.get("nota") or "").strip(), quien, tipo)
        flash(f"{num(dias)} días de {store.TIPOS_AUSENCIA[tipo][0].lower()} cargados.", "ok")
        _avisar(aviso)
    elif accion == "vacacion_editar":
        tipo = leer_tipo_ausencia(f.get("tipo"))
        desde, hasta = leer_fecha(f.get("desde", "")), leer_fecha(f.get("hasta", ""))
        dias = leer_decimal(f.get("dias") or str(vacaciones.dias_entre(desde, hasta)), "Los días")
        if dias <= 0:
            raise ValueError("Los días tienen que ser más que cero.")
        vid = int(f.get("id", 0))
        aviso = _controlar_periodo(t, tipo, desde, hasta, salvo=vid)
        store.editar_vacacion(vid, tipo, desde, hasta, dias, (f.get("nota") or "").strip())
        p = store.solicitud_por_vacacion(vid)
        if p and p["estado"] == "aprobada":
            store.cambiar_fechas_solicitud(p["id"], desde, hasta, dias)
        flash("Período corregido.", "ok")
        _avisar(aviso)
    elif accion == "vacacion_borrar":
        vid = int(f.get("id", 0))
        p = store.solicitud_por_vacacion(vid)
        if p and p["estado"] == "aprobada":
            # nació de un pedido: el pedido pasa a cancelado, así el trabajador no sigue viendo «Aprobado»
            store.cancelar_solicitud_aprobada(p["id"], "el período se borró de la ficha", quien)
            flash("Período borrado y el pedido quedó cancelado. Queda en Historial (arriba a la derecha, en su usuario) por si hay que recuperarlo.", "ok")
        else:
            store.borrar_vacacion(vid, quien)
            flash("Período borrado. Queda en Historial (arriba a la derecha, en su usuario) por si hay que recuperarlo.", "ok")
    elif accion == "ajuste":
        dias = leer_decimal(f.get("dias", ""), "Los días")
        motivo = (f.get("motivo") or "").strip()
        if not motivo:
            raise ValueError("Escriba el motivo del ajuste.")
        store.agregar_ajuste(t["id"], dias, motivo, quien)
        flash("Ajuste cargado.", "ok")
    elif accion == "ajuste_borrar":
        store.borrar_ajuste(int(f.get("id", 0)), quien)
        flash("Ajuste borrado. Queda en Historial (arriba a la derecha, en su usuario) por si hay que recuperarlo.", "ok")
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
    · [fecha de nacimiento] · [celular] · [días por año] · [tipo: planta,
    temporal o jubilado].

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
        celdas += [""] * (9 - len(celdas))
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
            tipo = celdas[8].strip().lower() or None
            if tipo and tipo not in store.TIPOS_TRABAJADOR:
                raise ValueError(f"El tipo «{celdas[8]}» no es planta, temporal ni jubilado.")
            if cedula in vistas:
                raise ValueError("Cédula repetida en la planilla.")
            vistas.add(cedula)
            buenas.append({"cedula": cedula, "nombre": nombre, "fecha_ingreso": ingreso,
                           "saldo": saldo, "fila": n, "tipo": tipo, **perfil})
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
    completa = request.form.get("completa") == "1"
    # «Es la planilla completa»: los de planta activos que no vienen pegados van a la campanita
    # (Tamara 14/10/2026: no se borran ni se dan de baja solos; contabilidad decide).
    faltan = _no_estan_en_lo_pegado(buenas) if (completa and buenas) else []
    if confirmar and buenas:
        r = store.cargar_lote(buenas, hoy())
        avisados = 0
        for t in faltan:
            if store.crear_aviso(t["id"], f"No está en la planilla pegada el {hoy().strftime('%d/%m/%Y')}. "
                                 "¿Sigue trabajando? Si no, páuselo o dele de baja.",
                                 g.usuario["usuario"], "no_en_planilla"):
                avisados += 1
        flash(f"Listo: {r['nuevos']} nuevos, {r['actualizados']} actualizados."
              + (f" {avisados} que no estaban en la planilla quedaron en la campanita." if avisados else ""), "ok")
        return redirect(url_for("admin"))
    existentes = {t["cedula"] for t in store.trabajadores(incluir_inactivos=True)} if buenas else set()
    return render_template("admin_carga.html", texto=texto, buenas=buenas, malas=malas,
                           existentes=existentes, completa=completa, faltan=faltan)


def _no_estan_en_lo_pegado(buenas: list[dict]) -> list[dict]:
    pegadas = {b["cedula"] for b in buenas}
    return [t for t in store.trabajadores() if t["cedula"] and t["cedula"] not in pegadas
            and (t.get("tipo") or "planta") == "planta" and not t.get("pausado_en")]


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
                aviso = _controlar_periodo(t, tipo, desde, hasta)
                store.agregar_vacacion(t["id"], desde, hasta, dias, (f.get("nota") or "").strip() or "cargado en la oficina",
                                       g.usuario["usuario"], tipo)
                flash(f"{t['nombre']}: {num(dias)} días de {store.TIPOS_AUSENCIA[tipo][0].lower()} cargados.", "ok")
                _avisar(aviso)
                return redirect(url_for("admin_solicitudes"))
            if accion == "editar":
                p = store.solicitud(int(f.get("id", 0) or 0))
                if not p or p["estado"] != "aprobada" or not p["vacacion_id"]:
                    raise ValueError("Sólo se corrige un pedido aprobado.")
                desde, hasta = leer_fecha(f.get("desde", "")), leer_fecha(f.get("hasta", ""))
                dias = leer_decimal(f.get("dias") or str(vacaciones.dias_entre(desde, hasta)), "Los días")
                if dias <= 0:
                    raise ValueError("Los días tienen que ser más que cero.")
                aviso = _controlar_periodo(store.trabajador(p["trabajador_id"]), p["tipo"], desde, hasta, salvo=p["vacacion_id"])
                store.editar_vacacion(p["vacacion_id"], p["tipo"], desde, hasta, dias, f"pedido #{p['id']}" + (f" · {p['nota']}" if p.get("nota") else ""))
                store.cambiar_fechas_solicitud(p["id"], desde, hasta, dias)  # el trabajador ve lo mismo
                flash(f"Corregido: {p['nombre']}, {num(dias)} días del {desde.strftime('%d/%m')} al {hasta.strftime('%d/%m')}.", "ok")
                _avisar(aviso)
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
                t = store.trabajador(p["trabajador_id"])
                aviso = _controlar_periodo(t, p["tipo"], p["desde"], p["hasta"])
                saldo = _resumen(t)["saldo"]
                descuenta = store.TIPOS_AUSENCIA[p["tipo"]][1] and not aviso
                if descuenta and dias > saldo and f.get("igual") != "1":
                    raise ValueError(f"A {p['nombre']} le quedan {num(saldo, 1)} y pide {num(dias, 1)}: quedaría en "
                                     f"{num(saldo - dias, 1)}. Marque «Aprobar aunque no le alcance» si es así.")
                nota = f"pedido #{p['id']}" + (f" · {p['nota']}" if p.get("nota") else "")
                vid = store.agregar_vacacion(p["trabajador_id"], p["desde"], p["hasta"], dias,
                                             nota, quien, p["tipo"])
                store.responder_solicitud(p["id"], "aprobada", (f.get("respuesta") or "").strip(), quien, vid)
                store.ligar_certificados_a_vacacion(p["id"], vid)
                flash(f"Aprobado: {num(dias)} días de {store.TIPOS_AUSENCIA[p['tipo']][0].lower()} "
                      f"para {p['nombre']}.", "ok")
                _avisar(aviso)
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
    pendientes = []
    for p in store.solicitudes_pendientes():
        t = store.trabajador(p["trabajador_id"])
        pisado = _periodo_pisado(t["id"], p["desde"], p["hasta"])
        pendientes.append(dict(p, saldo=_resumen(t)["saldo"],
                               pisado=_rango(pisado) if pisado else None,
                               anterior_al_arranque=_anterior_al_saldo_inicial(t, p["tipo"], p["desde"])))
    avisar = None
    id_avisar = request.args.get("avisar")
    if id_avisar and id_avisar.isdigit():
        p = store.solicitud(int(id_avisar))
        if p and p["estado"] in ("aprobada", "rechazada", "cancelada"):
            avisar = dict(p, whatsapp=link_whatsapp(p["celular"], _texto_aviso(p)))
    return render_template("admin_solicitudes.html", pendientes=pendientes,
                           respondidas=store.solicitudes_respondidas(),
                           cambios=store.cambios_perfil_sin_ver(), avisar=avisar,
                           trabajadores=store.trabajadores(), certificados=store.certificados_por_solicitud())


def _texto_aviso(p: dict) -> str:
    nombre = nombre_de_pila(p["nombre"])
    que = store.TIPOS_AUSENCIA[p["tipo"]][0].lower()
    cuando = f"del {p['desde'].strftime('%d/%m')} al {p['hasta'].strftime('%d/%m')}"
    if p["estado"] == "aprobada":
        return f"Hola {nombre}, su pedido de {que} {cuando} está aprobado. Saludos, Intela."
    if p["estado"] == "cancelada":
        return f"Hola {nombre}, su pedido de {que} {cuando} quedó cancelado ({p['respuesta']}). Saludos, Intela."
    return f"Hola {nombre}, su pedido de {que} {cuando} no se pudo aprobar: {p['respuesta']}. Saludos, Intela."


@app.route("/admin/avisos", methods=["GET", "POST"])
@requiere_admin
def admin_avisos():
    """La campanita: lo que contabilidad tiene que revisar."""
    if request.method == "POST":
        store.resolver_aviso(int(request.form.get("id", 0) or 0), g.usuario["usuario"])
        flash("Aviso resuelto.", "ok")
        return redirect(url_for("admin_avisos"))
    return render_template("admin_avisos.html", abiertos=store.avisos_abiertos(),
                           resueltos=store.avisos_resueltos())


@app.route("/admin/historial", methods=["GET", "POST"])
@requiere_admin
def admin_historial():
    """Lo que se borró: períodos y ajustes, con quién y cuándo. Se puede recuperar."""
    if request.method == "POST":
        que, id_ = request.form.get("que"), int(request.form.get("id", 0) or 0)
        if que == "periodo":
            store.recuperar_vacacion(id_)
            p = store.solicitud_por_vacacion(id_)
            if p and p["estado"] == "cancelada":
                store.reabrir_solicitud_cancelada(p["id"])
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
    """El resumen del mes: por semana y por día, con quiénes. Lo ven igual el
    comedor y contabilidad (Tamara 17/09); registrar a mano es de contabilidad."""
    anio, mes = leer_mes(request.values.get("mes"))
    if request.method == "POST":
        if not es_contabilidad():
            abort(403)
        try:
            fecha = leer_fecha(request.form.get("fecha", ""))
            tipo = leer_tipo_comida(request.form.get("tipo"))
            t = store.trabajador_por_cedula(limpiar_cedula(request.form.get("cedula", "")))
            if not t:
                raise ValueError("No hay ningún trabajador con esa cédula.")
            if fecha > hoy():
                raise ValueError("La fecha no puede ser futura.")
            store.marcar_comida(t["id"], fecha, tipo, g.usuario["usuario"], leer_turno(request.form.get("turno"), tipo))
            flash(f"{t['nombre']}: {tipo} del {fecha.strftime('%d/%m')} registrado.", "ok")
            anio, mes = fecha.year, fecha.month
        except ValueError as exc:
            flash(str(exc), "error")
        return redirect(url_for("admin_comidas", mes=f"{anio}-{mes:02d}"))
    dias_mes = [date(anio, mes, d) for d in range(1, calendar.monthrange(anio, mes)[1] + 1)]
    marcados = store.comidas_de_todos(anio, mes)
    inv = store.invitados_del_mes(anio, mes)
    fer = store.feriados(anio)
    nombres = {t["id"]: t["nombre"] for t in store.trabajadores(incluir_inactivos=True)}
    filas = []
    for d in dias_mes:
        if d > hoy():
            break
        quienes = {tipo: sorted((m[(d, tipo)] or "", nombres.get(tid, "?")) for tid, m in marcados.items() if (d, tipo) in m)
                   for tipo, _ in TIPOS_COMIDA}  # lista de (turno, nombre), por turno y nombre
        a, c = len(quienes["almuerzo"]), len(quienes["cena"])
        i_ = sum(i["cantidad"] for i in inv.get(d, []))
        if a + c + i_ == 0 and (d.weekday() >= 5 or d in fer):
            continue  # un sábado, domingo o feriado sin nadie no ocupa renglón
        filas.append({"fecha": d, "almuerzo": a, "cena": c, "invitados": i_, "quienes": quienes,
                      "lista_invitados": inv.get(d, []), "hoy": d == hoy(),
                      "gris": d.weekday() >= 5 or d in fer, "feriado": fer.get(d)})
    filas.reverse()  # hoy primero: en el comedor miran lo de hoy, no lo de atrás
    semanas = _por_semana(filas)
    totales = {"almuerzo": sum(f["almuerzo"] for f in filas), "cena": sum(f["cena"] for f in filas),
               "invitados": sum(f["invitados"] for f in filas)}
    totales["total"] = totales["almuerzo"] + totales["cena"] + totales["invitados"]
    return render_template("admin_comidas.html", anio=anio, mes=mes, filas=filas, totales=totales, semanas=semanas,
                           anterior=mes_anterior(anio, mes), siguiente=mes_siguiente(anio, mes),
                           trabajadores=store.trabajadores(), edita=es_contabilidad())


def _por_semana(filas: list[dict]) -> list[dict]:
    """Las cantidades del mes sumadas por semana (lunes a domingo), la última primero."""
    semanas: dict[date, dict] = {}
    for f in filas:
        lunes = f["fecha"] - timedelta(days=f["fecha"].weekday())
        sem = semanas.setdefault(lunes, {"desde": lunes, "hasta": lunes + timedelta(days=6),
                                         "almuerzo": 0, "cena": 0, "invitados": 0, "dias": 0})
        sem["almuerzo"] += f["almuerzo"]; sem["cena"] += f["cena"]; sem["invitados"] += f["invitados"]
        sem["dias"] += 1 if f["almuerzo"] + f["cena"] else 0
    for sem in semanas.values():
        sem["total"] = sem["almuerzo"] + sem["cena"] + sem["invitados"]
    return [semanas[k] for k in sorted(semanas, reverse=True)]


@app.route("/admin/comidas/imprimir")
@requiere_admin
def admin_comidas_imprimir():
    """El cuadro trabajador × día, para pagarle al comedor."""
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
    totales["invitados"] = sum(i["cantidad"] for v in inv.values() for i in v)
    return render_template("admin_comidas_imprimir.html", anio=anio, mes=mes, dias=dias, filas=filas,
                           totales=totales, feriados=store.feriados(anio))


@app.route("/admin/comidas/excel")
@requiere_admin
def admin_comidas_excel():
    """El mes en Excel, para pagarle al comedor: por día, por trabajador (día por
    día), invitados. Los mismos números que la pantalla."""
    from io import BytesIO
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    anio, mes = leer_mes(request.values.get("mes"))
    dias = [date(anio, mes, d) for d in range(1, calendar.monthrange(anio, mes)[1] + 1)]
    marcados = store.comidas_de_todos(anio, mes)
    inv = store.invitados_del_mes(anio, mes)
    fer = store.feriados(anio)
    gente = store.trabajadores(incluir_inactivos=True)
    negrita, gris = Font(bold=True), PatternFill("solid", fgColor="EEEEEE")
    centro = Alignment(horizontal="center")

    wb = Workbook()
    # --- Por semana: lo mismo que el resumen de la pantalla (Tamara 14/10/2026) ---
    filas_dia = []
    for d in dias:
        if d > hoy():
            break
        filas_dia.append({"fecha": d,
                          "almuerzo": sum(1 for m in marcados.values() if (d, "almuerzo") in m),
                          "cena": sum(1 for m in marcados.values() if (d, "cena") in m),
                          "invitados": sum(i["cantidad"] for i in inv.get(d, []))})
    h = wb.active
    h.title = "Por semana"
    h.append([f"Comidas de {MESES[mes]} {anio} — Intela · por semana (lunes a domingo)"])
    h["A1"].font = Font(bold=True, size=13)
    h.append([])
    h.append(["Semana", "Almuerzos", "Cenas", "Invitados", "Total"])
    for c in h[3]:
        c.font = negrita
    for w in _por_semana(filas_dia):
        h.append([f"{fecha_corta(w['desde'])} al {fecha_corta(w['hasta'])} ({w['desde'].strftime('%d/%m')} – {w['hasta'].strftime('%d/%m')})",
                  w["almuerzo"], w["cena"], w["invitados"], w["total"]])
    ta, tc, ti = (sum(f[k] for f in filas_dia) for k in ("almuerzo", "cena", "invitados"))
    h.append(["Total del mes", ta, tc, ti, ta + tc + ti])
    for c in h[h.max_row]:
        c.font = negrita
    for col, ancho in zip("ABCDE", (34, 11, 8, 10, 8)):
        h.column_dimensions[col].width = ancho

    # --- Por día ---
    h = wb.create_sheet("Por día")
    h.append([f"Comidas de {MESES[mes]} {anio} — Intela"])
    h["A1"].font = Font(bold=True, size=13)
    h.append([])
    h.append(["Día", "Fecha", "Almuerzos", "Cenas", "Invitados", "Total", "Feriado"])
    for c in h[3]:
        c.font = negrita
    tot = {"a": 0, "c": 0, "i": 0}
    for d in dias:
        if d > hoy():
            break
        a = sum(1 for m in marcados.values() if (d, "almuerzo") in m)
        c_ = sum(1 for m in marcados.values() if (d, "cena") in m)
        i_ = sum(i["cantidad"] for i in inv.get(d, []))
        tot["a"] += a; tot["c"] += c_; tot["i"] += i_
        h.append([DIAS_TRES[d.weekday()], d, a, c_, i_, a + c_ + i_, fer.get(d, "")])
        h.cell(row=h.max_row, column=2).number_format = "DD/MM/YYYY"
        if d.weekday() >= 5 or d in fer:
            for c in h[h.max_row]:
                c.fill = gris
    h.append(["Total", "", tot["a"], tot["c"], tot["i"], tot["a"] + tot["c"] + tot["i"], ""])
    for c in h[h.max_row]:
        c.font = negrita
    for col, ancho in zip("ABCDEFG", (6, 12, 11, 8, 10, 8, 28)):
        h.column_dimensions[col].width = ancho

    # --- Por trabajador (A = almorzó, C = cenó, AC = las dos) ---
    h = wb.create_sheet("Por trabajador")
    h.append(["Nombre", "Cédula", "Área"] + [d.day for d in dias] + ["Almuerzos", "Cenas", "Total"])
    for c in h[1]:
        c.font = negrita
        c.alignment = centro
    h.append(["", "", ""] + [DIAS_CORTOS[d.weekday()] for d in dias] + ["", "", ""])
    for i, d in enumerate(dias, start=4):
        h.cell(row=2, column=i).alignment = centro
        if d.weekday() >= 5 or d in fer:
            h.cell(row=1, column=i).fill = gris
            h.cell(row=2, column=i).fill = gris
    for t in gente:
        suyos = marcados.get(t["id"], {})
        if not t["activo"] and not suyos:
            continue
        fila = [t["nombre"], t["cedula"], t.get("area") or ""]
        for d in dias:
            fila.append(("A" if (d, "almuerzo") in suyos else "") + ("C" if (d, "cena") in suyos else ""))
        a = sum(1 for (_d, x) in suyos if x == "almuerzo")
        c_ = sum(1 for (_d, x) in suyos if x == "cena")
        h.append(fila + [a, c_, a + c_])
        for i in range(4, 4 + len(dias)):
            h.cell(row=h.max_row, column=i).alignment = centro
    h.column_dimensions["A"].width = 30
    h.column_dimensions["B"].width = 12
    h.column_dimensions["C"].width = 6
    for i in range(4, 4 + len(dias)):
        h.column_dimensions[get_column_letter(i)].width = 3.5
    h.freeze_panes = "D3"

    # --- Invitados ---
    h = wb.create_sheet("Invitados")
    h.append(["Fecha", "Comida", "Horario", "Cantidad", "Quiénes", "Con quién", "Lo registró"])
    for c in h[1]:
        c.font = negrita
    for d in sorted(inv):
        for i in inv[d]:
            h.append([d, "Almuerzo" if i["tipo"] == "almuerzo" else "Cena", i.get("turno") or "", i["cantidad"],
                      i["descripcion"], i["nombre"], i["cargado_por"]])
            h.cell(row=h.max_row, column=1).number_format = "DD/MM/YYYY"
    for col, ancho in zip("ABCDEFG", (12, 10, 8, 9, 30, 30, 12)):
        h.column_dimensions[col].width = ancho

    salida = BytesIO()
    wb.save(salida)
    salida.seek(0)
    return send_file(salida, as_attachment=True, download_name=f"comidas-{anio}-{mes:02d}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


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
            elif accion == "renombrar":
                id_ = int(f.get("id", 0) or 0)
                usuario = (f.get("usuario") or "").strip().lower()
                if not re.fullmatch(r"[a-z0-9._-]{2,30}", usuario):
                    raise ValueError("El usuario va en minúsculas, sin espacios (2 a 30 letras).")
                if any(u["usuario"] == usuario and u["id"] != id_ for u in store.usuarios()):
                    raise ValueError(f"El usuario «{usuario}» ya existe.")
                nombre = (f.get("nombre") or "").strip()[:60] or usuario
                store.renombrar_usuario(id_, usuario, nombre)
                if id_ == g.usuario["id"]:  # el suyo: que la barra lo muestre ya
                    session["usuario"] = dict(session["usuario"], usuario=usuario, nombre=nombre)
                flash(f"Listo: ahora entra como «{usuario}».", "ok")
            elif accion == "clave":
                clave = f.get("clave") or ""
                if len(clave) < 6:
                    raise ValueError("La clave tiene que tener al menos 6 caracteres.")
                store.cambiar_clave(int(f.get("id", 0)), generate_password_hash(clave))
                flash("Clave cambiada.", "ok")
            elif accion == "permisos_roles":
                id_ = int(f.get("id", 0) or 0)
                u = store.usuario(id_)
                if not u or u["rol"] != "contabilidad":
                    raise ValueError("Sólo los usuarios de contabilidad revisan o firman roles.")
                store.poner_permisos_roles(id_, f.get("revisa") == "1", f.get("firma") == "1")
                flash(f"Listo: permisos del rol de pagos de {u['nombre']}.", "ok")
            elif accion == "correo_prueba":
                para = leer_email(f.get("email", ""))
                ok, motivo = correo.mandar_codigo(para, g.usuario["nombre"].split()[0], "123456", store.MINUTOS_CODIGO, "crear")
                if not ok:
                    raise ValueError(f"No salió el correo: {motivo}. Avíseme antes de prender la contraseña.")
                flash(f"Correo de prueba mandado a {para}. Fíjese que le haya llegado (y si cayó en Spam).", "ok")
            elif accion == "pedir_clave":
                prender = f.get("prender") == "1"
                store.poner_configuracion("entrar_con_clave", "si" if prender else "no")
                flash("Listo: desde ahora los trabajadores entran con cédula y contraseña." if prender else
                      "Listo: los trabajadores vuelven a entrar sólo con la cédula.", "ok")
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
    return render_template("admin_usuarios.html", usuarios=store.usuarios(), pide_clave=pide_clave(),
                           link_comedor=url_for("comedor_t", token=clave_comedor(), _external=True,
                                                 _scheme="http" if request.host.startswith(("127.", "localhost")) else "https"))


# --------------------------------------------------------------------------
# ==========================================================================
# Rol de pagos (13/10/2026): contabilidad sube el Excel de WinAfin cada mes,
# el trabajador ve su recibo en el celular y lo firma con el dedo.
# La lectura del Excel y el cruce con el padrón viven en `roles.py`.
# ==========================================================================
@app.template_filter("dinero")
def dinero(valor):
    """1234.5 → «1.234,50». Siempre dos decimales: es plata."""
    if valor is None:
        return "—"
    return f"{float(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


@app.template_filter("fecha_hora")
def fecha_hora(valor):
    if not valor:
        return "—"
    if isinstance(valor, datetime) and valor.tzinfo:
        valor = valor.astimezone(ZoneInfo(config.ZONA))
    return valor.strftime("%d/%m/%Y %H:%M")


def nombre_mes(anio: int, mes: int) -> str:
    return f"{MESES[mes].capitalize()} {anio}"


app.jinja_env.globals["nombre_mes"] = nombre_mes
app.jinja_env.globals.update(FIRMA_DESDE=config.FIRMA_DESDE, EMPRESA=config.EMPRESA, RAZON_SOCIAL=config.RAZON_SOCIAL, RUC=config.RUC)
app.jinja_env.globals["cedula_valida"] = roles.cedula_valida

# El archivo que se subió viaja escondido en el formulario entre «Revisar» y
# «Cargar», así lo que se guarda sale del MISMO archivo que se revisó.
PESO_MAXIMO_ROL = 3 * 1024 * 1024


def _mes_por_defecto(nombre_archivo: str) -> tuple[int, int]:
    """El mes del nombre («rol9.xls» = septiembre); si no dice, el mes pasado.
    El año: el actual, salvo que ese mes todavía no haya llegado (en enero se
    carga el rol de diciembre del año anterior)."""
    h = hoy()
    mes = roles.mes_del_nombre(nombre_archivo)
    if mes is None:
        return (h.year - 1, 12) if h.month == 1 else (h.year, h.month - 1)
    return (h.year - 1, mes) if mes > h.month else (h.year, mes)


def _leer_anio_mes(f) -> tuple[int, int]:
    try:
        anio, mes = int(f.get("anio", "")), int(f.get("mes", ""))
    except ValueError:
        raise ValueError("Elija el mes y el año del rol.") from None
    if not 1 <= mes <= 12 or not 2020 <= anio <= hoy().year + 1:
        raise ValueError("El mes o el año no son válidos.")
    if date(anio, mes, 1) > hoy():
        raise ValueError(f"{nombre_mes(anio, mes)} todavía no empezó. Revise el mes y el año.")
    return anio, mes


@app.route("/admin/roles")
@requiere_admin
def admin_roles():
    meses = store.meses_con_rol()
    if request.args.get("mes"):
        anio, mes = leer_mes(request.args.get("mes"))
    elif meses:
        anio, mes = meses[0]["anio"], meses[0]["mes"]
    else:
        anio, mes = _mes_por_defecto("")
    filas = store.roles_del_mes(anio, mes)
    for r in filas:
        r["visible"] = store.visible(r)
        r["wa"] = None if r["firmado_en"] or not r.get("pide_firma", True) or not r["visible"] else link_whatsapp(
            r.get("celular"), f"Hola {nombre_de_pila(r['nombre'])}, su rol de pagos de {nombre_mes(anio, mes).lower()} "
            f"ya está en la aplicación de Intela. Por favor revíselo y fírmelo: https://trabajadores.intela.com.ec")
    totales = {k: sum(r[k] for r in filas) for k in ("total_ingresos", "total_descuentos", "liquido")}
    piden = [r for r in filas if r.get("pide_firma", True) and r["visible"]]
    necesitan = [r for r in filas if store.en_revision(r)]
    for r in filas:
        r["en_revision"] = store.en_revision(r)
    control = store.control_banco(anio, mes)
    banco = None
    if control:
        cuentas = {tid: c["cuenta"] for tid, c in store.constancias_vigentes().items()}
        banco = roles.comparar_con_banco(filas, control["filas"], cuentas)
        banco["control"] = control
        for r in filas:
            r["banco"] = banco["por_trabajador"].get(r["trabajador_id"])
    revision = {"necesitan": len(necesitan), "revisados": sum(1 for r in necesitan if r.get("revisado_en")),
                "aprobados": sum(1 for r in necesitan if r.get("aprobado_en"))}
    recientes = sorted((r for r in filas if r["firmado_en"]), key=lambda r: r["firmado_en"], reverse=True)[:8]
    n_constancias, n_activos = len(store.constancias_vigentes()), len(store.trabajadores())
    return render_template("admin_roles.html", anio=anio, mes=mes, filas=filas, meses=meses, totales=totales,
                           recientes=recientes, n_constancias=n_constancias, n_activos=n_activos, revision=revision,
                           banco=banco,
                           firmados=sum(1 for r in piden if r["firmado_en"]), piden=len(piden),
                           anterior=mes_anterior(anio, mes), siguiente=mes_siguiente(anio, mes))


def _ids_del_form() -> list[int]:
    return sorted({int(x) for x in request.form.getlist("ids") if x.isdigit()})


@app.route("/admin/roles/revisar", methods=["POST"])
@requiere_admin
def admin_roles_revisar():
    """Jessica (o quien tenga «Revisa roles») marca ✓ los recibos que revisó, de a uno
    o varios. «Quitar» saca el ✓ mientras la empresa no lo firmó."""
    mes = request.form.get("mes") or None
    if not g.usuario.get("revisa_roles"):
        flash("Su usuario no revisa roles de pago. Se da en Usuarios.", "error")
        return redirect(url_for("admin_roles", mes=mes))
    ids = _ids_del_form()
    if not ids:
        flash("Marque al menos un trabajador.", "error")
    elif request.form.get("accion") == "quitar":
        n = store.quitar_revision(ids)
        flash(f"Se sacó la revisión de {n} {'recibo' if n == 1 else 'recibos'}.", "ok")
    else:
        n = store.revisar_roles(ids, g.usuario["id"], g.usuario["nombre"])
        flash(f"Listo: {n} {'recibo revisado' if n == 1 else 'recibos revisados'}." if n else
              "Esos recibos ya estaban revisados.", "ok" if n else "error")
    return redirect(url_for("admin_roles", mes=mes))


@app.route("/admin/roles/banco", methods=["POST"])
@requiere_admin
def admin_roles_banco():
    """El SEGUNDO control (Tamara 14/10/2026): el archivo que se manda al banco. No carga
    nada: se guarda tal cual y la tabla del mes lo compara con cada recibo."""
    mes_txt = request.form.get("mes") or None
    try:
        anio, mes = leer_mes(mes_txt)
        archivo = request.files.get("archivo")
        if not archivo or not archivo.filename:
            raise ValueError("Elija el archivo del banco (Pago_Rol_MM_AA.xlsx).")
        filas, problemas, del_archivo = roles.leer_pago_banco(archivo.read(), archivo.filename)
        if del_archivo and del_archivo != (anio, mes):
            raise ValueError(f"Ese archivo es del rol de {nombre_mes(*del_archivo).lower()}, no de {nombre_mes(anio, mes).lower()}. "
                             "Súbalo en ese mes.")
        if not store.roles_del_mes(anio, mes):
            raise ValueError(f"Primero cargue el rol de {nombre_mes(anio, mes).lower()}: el archivo del banco se compara contra él.")
        store.guardar_control_banco(anio, mes, archivo.filename, filas, g.usuario["usuario"])
        flash(f"Listo: archivo del banco con {len(filas)} pagos. Abajo ve, persona por persona, si coincide con el rol."
              + (" " + " ".join(problemas[:3]) if problemas else ""), "ok" if not problemas else "error")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin_roles", mes=mes_txt))


@app.route("/admin/roles/cedula-banco", methods=["POST"])
@requiere_admin
def admin_roles_cedula_banco():
    """Desde el control del banco: «cobra con esta cédula» para uno o para todos los que
    en el banco figuran con otra (pares trabajador_id:cédula)."""
    n = 0
    for par in request.form.getlist("par"):
        tid, _, ced = par.partition(":")
        if tid.isdigit() and store.trabajador(int(tid)):
            store.poner_cedula_banco(int(tid), limpiar_cedula(ced))
            n += 1
    flash(f"Listo: {n} {'trabajador cobra' if n == 1 else 'trabajadores cobran'} con la cédula del banco. Quedó guardada en su ficha.", "ok")
    return redirect(url_for("admin_roles", mes=request.form.get("mes") or None))


@app.route("/admin/roles/firmar", methods=["POST"])
@requiere_admin
def admin_roles_firmar():
    """Ángel firma de una vez los recibos elegidos con su firma guardada. Escribe su
    clave: una sesión abierta en otra computadora no puede firmar por él."""
    mes = request.form.get("mes") or None
    u = g.usuario
    try:
        if not u.get("firma_roles"):
            raise ValueError("Su usuario no firma roles de pago. Se da en Usuarios.")
        firma = store.firma_de_usuario(u["id"])
        if not firma:
            raise ValueError("Primero registre su firma (botón «Mi firma»).")
        completo = store.usuario_por_nombre(u["usuario"])
        if not completo or not check_password_hash(completo["clave_hash"], request.form.get("clave") or ""):
            raise ValueError("La clave no es correcta. No se firmó nada.")
        ids = _ids_del_form()
        if not ids:
            raise ValueError("Marque los recibos que va a firmar.")
        # Sin revisión no se firma, y se dice por qué (Tamara 16/10/2026: «que Ángel no pueda
        # firmar si no está aprobado, pero que avise que no puede por eso»).
        motivo = store.estado_para_firmar(ids, u["id"])
        n = store.aprobar_roles(ids, u["id"], u["nombre"], firma)

        def _quienes(nombres):
            return ", ".join(nombres[:4]) + (f" y {len(nombres) - 4} más" if len(nombres) > 4 else "")
        por_que = []
        if motivo["sin_revisar"]:
            k = len(motivo["sin_revisar"])
            por_que.append(f"{k} {'no se firmó porque todavía no está revisado' if k == 1 else 'no se firmaron porque todavía no están revisados'}"
                           f" ({_quienes(motivo['sin_revisar'])}). Primero los tiene que revisar quien revisa los roles.")
        if motivo["revisados_por_el"]:
            k = len(motivo["revisados_por_el"])
            por_que.append(f"{k} no se {'firmó' if k == 1 else 'firmaron'} porque los revisó usted: los tiene que firmar otra persona"
                           f" ({_quienes(motivo['revisados_por_el'])}).")
        if motivo["ya_firmados"]:
            k = len(motivo["ya_firmados"])
            por_que.append(f"{k} ya {'estaba firmado' if k == 1 else 'estaban firmados'}.")
        if not n:
            raise ValueError("No se firmó ninguno. " + " ".join(por_que))
        flash(f"Listo: firmó {n} {'recibo' if n == 1 else 'recibos'}. Los trabajadores ya los ven en su celular."
              + (" " + " ".join(por_que) if por_que else ""), "ok")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin_roles", mes=mes))


@app.route("/admin/mi-firma", methods=["GET", "POST"])
@requiere_admin
def admin_mi_firma():
    """La firma guardada del usuario: se dibuja (o se sube la foto de la de papel) UNA
    vez, y después se aplica a los recibos con «Firmar los seleccionados»."""
    if request.method == "POST":
        try:
            if request.files.get("foto") and request.files["foto"].filename:
                png = leer_foto_firma(request.files["foto"].read())
            else:
                png = leer_firma(request.form.get("firma", ""))
            completo = store.usuario_por_nombre(g.usuario["usuario"])
            if not completo or not check_password_hash(completo["clave_hash"], request.form.get("clave") or ""):
                raise ValueError("La clave no es correcta. La firma no se guardó.")
            store.guardar_firma_usuario(g.usuario["id"], png)
            flash("Listo: su firma quedó guardada.", "ok")
            return redirect(_volver_seguro(request.form.get("volver")))
        except ValueError as exc:
            flash(str(exc), "error")
    return render_template("admin_mi_firma.html", volver=_volver_seguro(request.values.get("volver")))


def _volver_seguro(destino: str | None) -> str:
    """Sólo una ruta de esta misma app (nunca otro sitio)."""
    if destino and destino.startswith("/") and not destino.startswith("//") and "\\" not in destino:
        return destino
    return url_for("admin_roles")


@app.route("/admin/usuarios/<int:id_>/firma.png")
@requiere_admin
def admin_usuario_firma_png(id_):
    """La firma guardada de un usuario, para verla en Usuarios (es la misma que va en
    todos los recibos que firma, así que contabilidad ya la ve)."""
    png = store.firma_de_usuario(id_)
    if not png:
        abort(404)
    from io import BytesIO
    return send_file(BytesIO(png), mimetype="image/png", max_age=0)


@app.route("/admin/mi-firma.png")
@requiere_admin
def admin_mi_firma_png():
    png = store.firma_de_usuario(g.usuario["id"])
    if not png:
        abort(404)
    from io import BytesIO
    return send_file(BytesIO(png), mimetype="image/png", max_age=0)


def leer_foto_firma(datos: bytes) -> bytes:
    """La foto de la firma en papel → PNG con el fondo transparente (así se ve sobre
    la línea del recibo como si la hubiera firmado ahí)."""
    from io import BytesIO
    from PIL import Image, ImageOps
    if len(datos) > 8 * 1024 * 1024:
        raise ValueError("La foto pesa demasiado (máximo 8 MB).")
    try:
        img = Image.open(BytesIO(datos))
        if img.width * img.height > 40_000_000:
            raise ValueError("demasiado grande")
        img.draft("RGB", (1600, 1600))  # un JPEG grande se decodifica ya achicado (mucho más rápido)
        img = ImageOps.exif_transpose(img)
        img.thumbnail((1600, 1600))
    except Exception:  # noqa: BLE001
        raise ValueError("No se pudo leer la foto. Suba una foto JPG o PNG de la firma.") from None
    gris = ImageOps.autocontrast(img.convert("L"), cutoff=2)
    # lo oscuro es tinta; lo claro (el papel) se vuelve transparente
    alfa = gris.point(lambda v: 0 if v > 170 else min(255, (190 - v) * 3))
    caja = alfa.getbbox()
    if not caja:
        raise ValueError("No se ve la firma en la foto. Saque la foto sobre papel blanco, con buena luz.")
    tinta = Image.new("RGBA", gris.size, (15, 23, 42, 0))
    tinta.putalpha(alfa)
    tinta = tinta.crop(caja)
    tinta.thumbnail((900, 360))
    salida = BytesIO()
    tinta.save(salida, "PNG", optimize=True)
    return salida.getvalue()


@app.route("/admin/roles/<int:id_>/firma-empresa.png")
@requiere_admin
def admin_rol_firma_empresa(id_):
    png = store.firma_empresa_del_rol(id_)
    if not png:
        abort(404)
    from io import BytesIO
    return send_file(BytesIO(png), mimetype="image/png")


def url_firma_empresa(r: dict) -> str | None:
    """La firma de la empresa en el recibo: contabilidad la ve por su ruta, el
    trabajador por la suya (y sólo la de sus recibos)."""
    if not r.get("aprobado_en"):
        return None
    if g.get("usuario") and es_contabilidad():
        return url_for("admin_rol_firma_empresa", id_=r["id"])
    return url_for("yo_rol_firma_empresa", id_=r["id"])


app.jinja_env.globals["url_firma_empresa"] = url_firma_empresa


@app.route("/admin/roles/papel", methods=["POST"])
@requiere_admin
def admin_roles_papel():
    """«Ya lo firmaron en papel»: el mes deja de pedir firma en la app."""
    anio, mes = leer_mes(request.form.get("mes"))
    if request.form.get("confirmo") != "1":
        flash("Marque «Confirmo que todos firmaron en papel» para seguir.", "error")
    elif request.form.get("sacar_firmas") == "1":
        n = store.mes_en_papel(anio, mes, g.usuario["usuario"])
        partes = []
        if n["firmas_empresa"]:
            partes.append(f"{n['firmas_empresa']} de contabilidad")
        if n["firmas_trabajador"]:
            partes.append(f"{n['firmas_trabajador']} de trabajadores")
        flash(f"Listo: {nombre_mes(anio, mes).lower()} queda firmado en papel ({n['recibos']} recibos)."
              + (f" Se sacaron las firmas hechas en la app ({' y '.join(partes)}); quedan guardadas aparte." if partes else ""), "ok")
    else:
        n = store.firmado_en_papel(anio, mes)
        flash(f"Listo: {nombre_mes(anio, mes).lower()} queda firmado en papel. "
              f"{n} {'trabajador ya no ve' if n == 1 else 'trabajadores ya no ven'} el pedido de firma.", "ok")
    return redirect(url_for("admin_roles", mes=f"{anio}-{mes:02d}"))


# ---- crear en el sistema a quien viene en el rol y no está en el padrón (Tamara 13/10) ----
def _nombre_bonito(nombre_rol: str) -> str:
    """«AGUILAR CHACHALO VINICIO HERNAN» → «Aguilar Chachalo Vinicio Hernan»."""
    return " ".join(w[:1].upper() + w[1:].lower() for w in (nombre_rol or "").split())


def _datos_nuevo(f, clave: str, nombre_rol: str, cedula: str | None = None) -> dict:
    """Lo que escribió contabilidad para crear a alguien (tal cual, para volver a mostrarlo)."""
    return {"nombre": (f.get(f"n_nom_{clave}") or "").strip() or _nombre_bonito(nombre_rol),
            "cedula": cedula if cedula is not None else (f.get(f"n_ced_{clave}") or "").strip(),
            "cedula_fija": cedula is not None,
            "ingreso": (f.get(f"n_ing_{clave}") or "").strip(), "area": (f.get(f"n_area_{clave}") or "").strip(),
            "celular": (f.get(f"n_cel_{clave}") or "").strip(), "saldo": (f.get(f"n_sal_{clave}") or "").strip(),
            "error": None}


def _validar_nuevo(n: dict, cedulas_padron: dict, vistas: set) -> dict:
    """Valida un trabajador nuevo; devuelve lo que se guarda o levanta ValueError."""
    if not n["cedula"]:
        raise ValueError("Falta la cédula.")
    cedula = limpiar_cedula(n["cedula"])
    if len(cedula) == 10 and not roles.cedula_valida(cedula):
        raise ValueError(f"La cédula {cedula} no es válida: revise los números.")
    if cedula in cedulas_padron:
        raise ValueError(f"La cédula {cedula} ya es de {cedulas_padron[cedula]}: elíjalo en la lista en vez de crearlo.")
    if cedula in vistas:
        raise ValueError(f"La cédula {cedula} está puesta dos veces.")
    if len(n["nombre"]) < 5:
        raise ValueError("Falta el nombre completo.")
    if not n["ingreso"]:
        raise ValueError("Falta la fecha de ingreso.")
    ingreso = leer_fecha(n["ingreso"])
    if ingreso > hoy():
        raise ValueError("La fecha de ingreso es futura.")
    if n["area"] and n["area"] not in AREAS:
        raise ValueError("El área no es válida.")
    perfil = leer_perfil({"area": n["area"], "celular": n["celular"]})
    saldo = leer_decimal(n["saldo"], "El saldo de vacaciones") if n["saldo"] else None
    vistas.add(cedula)
    return {"cedula": cedula, "nombre": n["nombre"], "ingreso": ingreso, "perfil": perfil, "saldo": saldo}


def _crear_nuevo(v: dict) -> int:
    return store.crear_trabajador(v["cedula"], v["nombre"], v["ingreso"], v["saldo"],
                                  hoy() if v["saldo"] is not None else None, v["perfil"])


def _revisar_nuevos(nuevos: list[dict], padron: list[dict]) -> bool:
    """Valida todos; deja el error en cada uno. True si están todos bien."""
    cedulas = {re.sub(r"\D", "", t["cedula"]): t["nombre"] for t in padron}
    vistas: set = set()
    todo_bien = True
    for x in nuevos:
        try:
            x["valido"] = _validar_nuevo(x["nuevo"], cedulas, vistas)
            x["nuevo"]["error"] = None
        except ValueError as exc:
            x["valido"], x["nuevo"]["error"] = None, str(exc)
            todo_bien = False
    return todo_bien


@app.route("/admin/roles/cargar", methods=["GET", "POST"])
@requiere_admin
def admin_roles_cargar():
    if request.method == "GET":
        anio, mes = _mes_por_defecto("")
        return render_template("admin_roles_cargar.html", anio=anio, mes=mes, revision=None)
    f = request.form
    import base64
    archivo = request.files.get("archivo")
    try:
        if archivo and archivo.filename:
            datos, nombre = archivo.read(), archivo.filename
            if len(datos) > PESO_MAXIMO_ROL:
                raise ValueError("El archivo es demasiado grande para ser un rol de pagos.")
            # Lo elegido en la pantalla manda; sin elegir, el mes del nombre del archivo.
            # (El acumulado trae sus meses adentro: el mes de la pantalla no se usa.)
            anio, mes = ((0, 0) if roles.es_acumulado(datos, nombre) else
                         _leer_anio_mes(f) if f.get("mes") and f.get("anio") else _mes_por_defecto(nombre))
        elif f.get("archivo_b64"):
            datos, nombre = base64.b64decode(f["archivo_b64"]), f.get("archivo_nombre", "rol.xls")
            anio, mes = (0, 0) if roles.es_acumulado(datos, nombre) else _leer_anio_mes(f)
        else:
            raise ValueError("Elija el archivo del rol.")
        if roles.es_acumulado(datos, nombre):
            return _cargar_historial(datos, nombre, f)
        buenas, malas = roles.leer_rol(datos, nombre)
        if not buenas:
            raise ValueError("El archivo no trae ningún trabajador que se pueda cargar.")
    except ValueError as exc:  # NoEsElRol también es ValueError
        flash(str(exc), "error")
        a, m = _mes_por_defecto("")
        return render_template("admin_roles_cargar.html", anio=a, mes=m, revision=None)

    padron = store.trabajadores(incluir_inactivos=True)
    roles.cruzar(buenas, padron)
    por_id = {t["id"]: t for t in padron}
    # Lo que eligió contabilidad a mano pisa al cruce automático. Un «parecido»
    # (o alguien elegido a mano) sólo entra si se marcó «Es la misma persona»:
    # nunca cargarle el rol de una persona a otra (Tamara, 13/10/2026).
    if f.get("archivo_b64"):
        for b in buenas:
            elegido = f.get(f"t_{b['fila']}")
            if elegido is None:
                pass
            elif elegido == "":
                b["trabajador_id"], b["como"], b["nombre_padron"] = None, None, None
            elif elegido == "nuevo":
                b["trabajador_id"], b["como"], b["nombre_padron"], b["aviso"] = None, "nuevo", None, None
                b["nuevo"] = _datos_nuevo(f, str(b["fila"]), b["nombre_rol"])
            elif elegido.isdigit() and int(elegido) in por_id:
                if int(elegido) != b["trabajador_id"]:
                    b["trabajador_id"], b["como"] = int(elegido), "elegido"
                    b["nombre_padron"] = por_id[int(elegido)]["nombre"]
                    b["aviso"] = None
    for b in buenas:
        b["confirmado"] = bool(f.get(f"ok_{b['fila']}")) if b["como"] in ("parecido", "elegido") else True
        if b["trabajador_id"]:
            b["cedula_padron"] = por_id[b["trabajador_id"]]["cedula"]
    nuevos = [b for b in buenas if b["como"] == "nuevo"]
    nuevos_bien = _revisar_nuevos(nuevos, padron) if nuevos else True
    repetidos = {}
    for b in buenas:
        if b["trabajador_id"] and b["confirmado"]:
            repetidos.setdefault(b["trabajador_id"], []).append(b)
    repetidos = {k: v for k, v in repetidos.items() if len(v) > 1}

    ya = next((m for m in store.meses_con_rol() if (m["anio"], m["mes"]) == (anio, mes)), None)
    revisado = (f.get("anio_revisado"), f.get("mes_revisado"))
    if f.get("confirmar") == "1" and revisado != (str(anio), str(mes)):
        flash(f"Cambió el mes a {nombre_mes(anio, mes).lower()}. Revise de nuevo antes de cargar.", "error")
    elif f.get("confirmar") == "1":
        if repetidos:
            flash("Hay un trabajador elegido para dos filas del rol. Corríjalo antes de cargar.", "error")
        elif not nuevos_bien:
            flash("Revise los datos de los trabajadores nuevos (lo que está en rojo) antes de cargar.", "error")
        else:
            for b in nuevos:
                b["trabajador_id"] = _crear_nuevo(b["valido"])
            entran = [b for b in buenas if b["trabajador_id"] and b["confirmado"]]
            r = store.cargar_roles(anio, mes, entran, g.usuario["usuario"])
            msg = f"Listo: rol de {nombre_mes(anio, mes).lower()} cargado para {r['cargados']} trabajadores."
            if nuevos:
                msg += (f" Se {'creó' if len(nuevos) == 1 else 'crearon'} {len(nuevos)} "
                        f"{'trabajador nuevo' if len(nuevos) == 1 else 'trabajadores nuevos'}: "
                        + ", ".join(b["valido"]["nombre"] for b in nuevos) + ".")
            if r["reemplazados"]:
                msg += " Reemplazó al que estaba cargado."
            if r["firmas_perdidas"]:
                msg += f" {r['firmas_perdidas']} recibos cambiaron y tienen que volver a firmarse."
            if r.get("firmados_quitados"):
                msg += f" {r['firmados_quitados']} que ya habían firmado no están en este archivo y quedaron sin rol este mes."
            flash(msg, "ok")
            return redirect(url_for("admin_roles", mes=f"{anio}-{mes:02d}"))

    entran = [b for b in buenas if (b["trabajador_id"] or b["como"] == "nuevo") and b["confirmado"]]
    return render_template(
        "admin_roles_cargar.html", anio=anio, mes=mes, revision=True, nuevos=nuevos, AREAS=AREAS,
        archivo_b64=base64.b64encode(datos).decode(), archivo_nombre=nombre,
        buenas=buenas, malas=malas, entran=entran, ya=ya, repetidos=repetidos,
        sin_encontrar=[b for b in buenas if not b["trabajador_id"]],
        sin_confirmar=[b for b in buenas if b["trabajador_id"] and not b["confirmado"]],
        a_revisar=[b for b in buenas if b["como"] in ("parecido", "elegido")],
        seguros=[b for b in buenas if b["como"] in ("codigo", "nombre", "orden")],
        sin_rol=roles.sin_rol(padron, buenas), padron=sorted(padron, key=lambda t: t["nombre"]),
        total=sum(b["liquido"] for b in entran), COMO=roles.COMO)


def _cargar_historial(datos: bytes, nombre: str, f) -> str:
    """El ROL ACUMULADO de WinAfin: muchos meses de una vez, con cédula. Se
    revisa igual que un mes (nada se guarda sin «Cargar»), se eligen los meses,
    y se cruza POR CÉDULA. Los meses del historial no piden firma."""
    import base64
    from collections import defaultdict
    buenas, malas = roles.leer_acumulado(datos, nombre)
    if not buenas:
        raise ValueError("El archivo no trae ningún recibo que se pueda cargar.")
    # Una persona = una cédula; el cruce se hace una vez por persona y vale para todos sus meses.
    personas: dict[str, dict] = {}
    for b in sorted(buenas, key=lambda b: (b["anio"], b["mes"])):
        p = personas.setdefault(b["cedula"], {"cedula": b["cedula"], "meses": 0, "liquido": 0.0})
        p.update(codigo=b["codigo"], nombre_rol=b["nombre_rol"])  # lo del último mes
        p["meses"] += 1
        p["liquido"] += b["liquido"]
    lista = sorted(personas.values(), key=lambda p: p["nombre_rol"])
    padron = store.trabajadores(incluir_inactivos=True)
    roles.cruzar_por_cedula(lista, padron)
    por_id = {t["id"]: t for t in padron}
    revisando = bool(f.get("archivo_b64"))
    for p in lista:
        clave = p["cedula"]
        elegido = f.get(f"t_{clave}") if revisando else None
        if elegido == "":
            p["trabajador_id"], p["como"], p["nombre_padron"], p["aviso"] = None, None, None, None
        elif elegido == "nuevo":
            p["trabajador_id"], p["como"], p["nombre_padron"], p["aviso"] = None, "nuevo", None, None
            p["nuevo"] = _datos_nuevo(f, clave, p["nombre_rol"], cedula=clave)
        elif elegido and elegido.isdigit() and int(elegido) in por_id and int(elegido) != p["trabajador_id"]:
            p["trabajador_id"], p["como"], p["nombre_padron"] = int(elegido), "elegido", por_id[int(elegido)]["nombre"]
            p["aviso"] = None
        p["confirmado"] = p["como"] in ("cedula", "nuevo") or bool(f.get(f"ok_{clave}"))
        if p["trabajador_id"]:
            p["cedula_padron"] = por_id[p["trabajador_id"]]["cedula"]
    nuevos = [p for p in lista if p["como"] == "nuevo"]
    nuevos_bien = _revisar_nuevos(nuevos, padron) if nuevos else True
    repetidos = defaultdict(list)
    for p in lista:
        if p["trabajador_id"] and p["confirmado"]:
            repetidos[p["trabajador_id"]].append(p)
    repetidos = {k: v for k, v in repetidos.items() if len(v) > 1}

    # Los meses del archivo. Por defecto se marcan los nuevos y cerrados; el mes
    # en curso y los que ya están cargados quedan sin marcar (se pueden marcar).
    ya = {(m["anio"], m["mes"]): m for m in store.meses_con_rol()}
    h = hoy()
    meses = []
    for (anio, mes) in sorted({(b["anio"], b["mes"]) for b in buenas}, reverse=True):
        del_mes = [b for b in buenas if (b["anio"], b["mes"]) == (anio, mes)]
        en_curso = (anio, mes) >= (h.year, h.month)
        clave = f"{anio}-{mes:02d}"
        hay = any((personas[b["cedula"]]["trabajador_id"] or personas[b["cedula"]]["como"] == "nuevo")
                  and personas[b["cedula"]]["confirmado"] for b in del_mes)
        marcado = (bool(f.get(f"m_{clave}")) if revisando
                   else hay and not en_curso and (anio, mes) not in ya)
        entran = [b for b in del_mes if (personas[b["cedula"]]["trabajador_id"] or personas[b["cedula"]]["como"] == "nuevo")
                  and personas[b["cedula"]]["confirmado"]]
        meses.append({"anio": anio, "mes": mes, "clave": clave, "filas": len(del_mes), "entran": len(entran),
                      "liquido": round(sum(b["liquido"] for b in entran), 2), "en_curso": en_curso,
                      "ya": ya.get((anio, mes)), "marcado": marcado, "recibos": entran,
                      "pide_firma": (anio, mes) >= config.FIRMA_DESDE})
    elegidos = [m for m in meses if m["marcado"]]

    revisados = set(filter(None, (f.get("meses_revisados") or "").split(",")))
    if f.get("confirmar") == "1" and revisados != {m["clave"] for m in elegidos}:
        flash("Cambió los meses marcados. Revise los números y toque «Cargar historial» de nuevo.", "error")
    elif f.get("confirmar") == "1":
        if repetidos:
            flash("Hay un trabajador elegido para dos personas del archivo. Corríjalo antes de cargar.", "error")
        elif not elegidos:
            flash("Marque al menos un mes para cargar.", "error")
        elif not nuevos_bien:
            flash("Revise los datos de los trabajadores nuevos (lo que está en rojo) antes de cargar.", "error")
        else:
            for p in nuevos:
                p["trabajador_id"] = _crear_nuevo(p["valido"])
            if nuevos:
                flash(f"Se {'creó' if len(nuevos) == 1 else 'crearon'} {len(nuevos)} "
                      f"{'trabajador nuevo' if len(nuevos) == 1 else 'trabajadores nuevos'}: "
                      + ", ".join(p["valido"]["nombre"] for p in nuevos) + ".", "ok")
            total = 0
            corregidas = []
            for p in lista:
                if p.get("corregir_cedula") and p["confirmado"] and p["como"] == "parecido":
                    if store.corregir_cedula(p["trabajador_id"], p["cedula"]):
                        corregidas.append(p["nombre_padron"])
            for m in elegidos:
                filas = []
                for b in m["recibos"]:
                    filas.append(dict(b, trabajador_id=personas[b["cedula"]]["trabajador_id"]))
                r = store.cargar_roles(m["anio"], m["mes"], filas, g.usuario["usuario"], pide_firma=m["pide_firma"])
                total += r["cargados"]
            nombres = ", ".join(nombre_mes(m["anio"], m["mes"]).lower() for m in sorted(elegidos, key=lambda m: (m["anio"], m["mes"])))
            flash(f"Listo: {total} recibos cargados de {len(elegidos)} {'mes' if len(elegidos) == 1 else 'meses'} ({nombres}). "
                  "Cada trabajador ya los ve en su celular.", "ok")
            if corregidas:
                flash(f"Se corrigió la cédula en el padrón de {len(corregidas)} "
                      f"{'trabajador' if len(corregidas) == 1 else 'trabajadores'} (estaba mal tipeada): "
                      + ", ".join(corregidas) + ". Ahora pueden entrar con su cédula verdadera.", "ok")
            return redirect(url_for("admin_roles", mes=elegidos[0]["clave"]))

    return render_template(
        "admin_roles_historial.html", archivo_b64=base64.b64encode(datos).decode(), archivo_nombre=nombre,
        nuevos=nuevos, AREAS=AREAS,
        meses=meses, elegidos=elegidos, malas=malas, repetidos=repetidos,
        sin_encontrar=[p for p in lista if not p["trabajador_id"]],
        a_revisar=[p for p in lista if p["trabajador_id"] and p["como"] != "cedula"],
        seguros=[p for p in lista if p["como"] == "cedula"],
        sin_confirmar=[p for p in lista if p["trabajador_id"] and not p["confirmado"]],
        padron=sorted(padron, key=lambda t: t["nombre"]), personas=lista,
        total=sum(m["liquido"] for m in elegidos), recibos=sum(m["entran"] for m in elegidos))


def slug_nombre(nombre: str) -> str:
    """«Ichau Sango Nataly Mishell» → «ichau-sango-nataly-mishell»."""
    return roles.normalizar(nombre).lower().replace(" ", "-")


def url_rol(r: dict, imprimir: bool = False, **extra) -> str:
    """El link de un recibo dice de quién y de qué mes es (Tamara 13/10: «46 no
    me dice nada»): /admin/roles/2026-09/ichau-sango-nataly-mishell. Sin la
    cédula a propósito: los links quedan en el historial y se reenvían."""
    return url_for("admin_rol_imprimir" if imprimir else "admin_rol",
                   cual=f"{r['anio']}-{r['mes']:02d}", slug=slug_nombre(r["nombre"]), **extra)


app.jinja_env.globals["url_rol"] = url_rol


def _rol_por_url(cual: str, slug: str) -> dict:
    m = re.fullmatch(r"(\d{4})-(\d{1,2})", cual)
    if not m:
        abort(404)
    anio, mes = int(m.group(1)), int(m.group(2))
    hay = [r for r in store.roles_del_mes(anio, mes) if slug_nombre(r["nombre"]) == slug]
    if len(hay) != 1:
        abort(404)
    return hay[0]


@app.route("/admin/roles/<int:id_>")
@requiere_admin
def admin_rol_viejo(id_):
    """Los links viejos con el número siguen andando: llevan al nuevo."""
    r = store.rol_pago(id_)
    if not r:
        abort(404)
    return redirect(url_rol(r), code=301)


@app.route("/admin/roles/<cual>/<slug>")
@requiere_admin
def admin_rol(cual, slug):
    r = _rol_por_url(cual, slug)
    return render_template("admin_rol.html", r=r, **_constancia(r))


def _constancia(r: dict) -> dict:
    """Lo que dice la constancia de la firma: desde qué equipo y si el recibo
    sigue siendo el mismo que se firmó."""
    if not r.get("firmado_en"):
        return {"intacto": None, "equipo": None}
    return {"intacto": r.get("firma_huella") == roles.huella(r), "equipo": roles.dispositivo(r.get("firmado_desde"))}


def _para_imprimir(recibos: list[dict]) -> list[dict]:
    """Le agrega a cada recibo lo de la constancia (equipo, si sigue igual)."""
    for r in recibos:
        c = _constancia(r)
        r["equipo"], r["intacto"] = c["equipo"], c["intacto"]
    return recibos


@app.route("/admin/roles/<int:id_>/imprimir")
@requiere_admin
def admin_rol_imprimir_viejo(id_):
    r = store.rol_pago(id_)
    if not r:
        abort(404)
    return redirect(url_rol(r, imprimir=True, **request.args), code=301)


@app.route("/admin/roles/<cual>/<slug>/imprimir")
@requiere_admin
def admin_rol_imprimir(cual, slug):
    r = _rol_por_url(cual, slug)
    return render_template("rol_imprimir.html", recibos=_para_imprimir([r]), anio=r["anio"], mes=r["mes"],
                           url_firma=lambda i: url_for("admin_rol_firma", id_=i), auto=request.args.get("auto") == "1",
                           volver=url_rol(r))


@app.route("/admin/roles/imprimir")
@requiere_admin
def admin_roles_imprimir():
    """El mes entero, dos recibos por hoja A4."""
    anio, mes = leer_mes(request.args.get("mes"))
    return render_template("rol_imprimir.html", recibos=_para_imprimir(store.roles_del_mes(anio, mes)), anio=anio, mes=mes,
                           url_firma=lambda i: url_for("admin_rol_firma", id_=i), auto=False,
                           volver=url_for("admin_roles", mes=f"{anio}-{mes:02d}"))


@app.route("/yo/rol/<int:id_>/imprimir")
def yo_rol_imprimir(id_):
    """El recibo del trabajador para imprimir o guardar en PDF. Sólo el suyo."""
    t = _mi_trabajador()
    if not t:
        return redirect(url_for("entrar"))
    if (falta := _falta_constancia(t)):
        return falta
    r = store.rol_pago(id_)
    if not r or r["trabajador_id"] != t["id"] or not store.visible(r):
        abort(404)
    return render_template("rol_imprimir.html", recibos=_para_imprimir([r]), anio=r["anio"], mes=r["mes"], t=t,
                           url_firma=lambda i: url_for("yo_rol_firma", id_=i), auto=request.args.get("auto") == "1",
                           volver=url_for("yo_rol", mes=f"{r['anio']}-{r['mes']:02d}"))


@app.route("/admin/roles/<int:id_>/firma.png")
@requiere_admin
def admin_rol_firma(id_):
    png = store.firma_del_rol(id_)
    if not png:
        abort(404)
    from io import BytesIO
    return send_file(BytesIO(png), mimetype="image/png")


# ---- el trabajador -------------------------------------------------------
PESO_MAXIMO_FIRMA = 400 * 1024


def leer_firma(texto: str) -> bytes:
    """La firma llega como «data:image/png;base64,…» desde el canvas. Se
    verifica que sea una imagen de verdad y se guarda chica, en PNG."""
    import base64
    from io import BytesIO
    m = re.fullmatch(r"data:image/png;base64,([A-Za-z0-9+/=]+)", (texto or "").strip())
    if not m:
        raise ValueError("Firme en el recuadro antes de tocar «Firmar».")
    crudo = base64.b64decode(m.group(1))
    if len(crudo) > PESO_MAXIMO_FIRMA:
        raise ValueError("La firma no se pudo guardar. Bórrela y vuelva a firmar.")
    from PIL import Image
    try:
        img = Image.open(BytesIO(crudo))
        # Un PNG chico puede esconder una imagen gigante: se mira el tamaño ANTES
        # de descomprimir (un recuadro de firma real no pasa de ~1500 × 700).
        if img.width > 3000 or img.height > 1500:
            raise ValueError("demasiado grande")
        img.load()
    except Exception:  # noqa: BLE001
        raise ValueError("La firma no se pudo leer. Bórrela y vuelva a firmar.") from None
    # Una firma vacía (todo transparente o todo blanco) no vale.
    alfa = img.convert("RGBA").getchannel("A")
    if alfa.getbbox() is None:
        raise ValueError("Firme en el recuadro antes de tocar «Firmar».")
    caja = alfa.getbbox()
    if (caja[2] - caja[0]) < 25 and (caja[3] - caja[1]) < 25:
        raise ValueError("La firma es demasiado chica. Firme de nuevo, más grande.")
    img = img.convert("RGBA")
    img.thumbnail((900, 360))
    salida = BytesIO()
    img.save(salida, "PNG", optimize=True)
    return salida.getvalue()


# ---- la constancia de conocimiento y acceso a roles de pago ----------------
def _texto_constancia(t: dict, banco: str, cuenta: str, fecha: date) -> str:
    return const.texto(t["nombre"], t["cedula"], config.RAZON_SOCIAL, banco, cuenta, fecha)


def _parrafos_para_firmar(t: dict, banco: str, cuenta: str) -> list:
    """El texto de la constancia en HTML seguro: su nombre y cédula resaltados, y
    el banco y la cuenta como huecos que se llenan mientras escribe."""
    from markupsafe import Markup, escape
    out = []
    for p in const.parrafos(t["nombre"], t["cedula"], config.RAZON_SOCIAL, "\x00B", "\x00C", hoy()):
        h = str(escape(p))
        h = h.replace(str(escape(t["nombre"])), f'<span class="dato">{escape(t["nombre"])}</span>')
        h = h.replace(str(escape(t["cedula"])), f'<span class="dato">{escape(t["cedula"])}</span>')
        h = h.replace("\x00B", f'<span class="hueco" id="v-banco">{escape(banco) or "su banco"}</span>')
        h = h.replace("\x00C", f'<span class="hueco" id="v-cuenta">{escape(cuenta) or "su cuenta"}</span>')
        out.append(Markup(h))
    return out


def _falta_constancia(t: dict):
    """Si el trabajador todavía no firmó la constancia, la respuesta que lo manda a
    firmarla; si ya la firmó, None. Va antes de TODO lo de roles (Tamara 13/10)."""
    if store.constancia_de(t["id"]):
        return None
    return redirect(url_for("yo_constancia"))


# ---- la contraseña para entrar (Tamara 14/10/2026) ------------------------
# «La contraseña tiene que ser para todo, vacaciones y rol»: cédula → contraseña. La
# primera vez: correo → código de 6 números → contraseña. «Todos deben tener mail.»
# La tablet del comedor sigue con la cédula sola.
def correo_tapado(email: str | None) -> str:
    """«juana.perez@gmail.com» → «ju•••z@gmail.com»: que sepa a cuál fue, sin mostrarlo entero."""
    if not email or "@" not in email:
        return ""
    usuario, dominio = email.split("@", 1)
    if len(usuario) <= 2:
        return usuario[:1] + "•••@" + dominio
    return usuario[:2] + "•••" + usuario[-1] + "@" + dominio


def leer_email(texto: str) -> str:
    e = (texto or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}", e) or len(e) > 120 or ".." in e:
        raise ValueError("Ese correo no parece un correo. Revíselo (por ejemplo: nombre@gmail.com).")
    return e


def leer_clave_nueva(clave: str, repetir: str, cedula: str | None) -> str:
    if len(clave) < 6:
        raise ValueError("La contraseña tiene que tener al menos 6 letras o números.")
    if clave != repetir:
        raise ValueError("Las dos contraseñas no son iguales. Escríbalas de nuevo.")
    if re.sub(r"\D", "", clave) == (cedula or "-") or (clave.isdigit() and len(set(clave)) == 1):
        raise ValueError("Esa contraseña es muy fácil de adivinar. Elija otra.")
    return clave


def _mandar_codigo(t: dict, email: str, para_que: str) -> str | None:
    """Manda un código nuevo. Devuelve el error para mostrar, o None si salió."""
    import secrets
    if store.codigos_recientes(t["id"], 1) >= 1:
        return "Ya le mandamos un código hace menos de un minuto. Espere un momento y revise su correo."
    if store.codigos_recientes(t["id"], 60) >= 5:
        return "Pidió muchos códigos en la última hora. Espere un rato o pregunte en contabilidad."
    codigo = f"{secrets.randbelow(1_000_000):06d}"
    id_codigo = store.crear_codigo_correo(t["id"], email, generate_password_hash(codigo))
    ok, motivo = correo.mandar_codigo(email, nombre_de_pila(t["nombre"]).capitalize(), codigo, store.MINUTOS_CODIGO, para_que)
    if not ok:
        app.logger.warning("No salió el código a %s: %s", email, motivo)
        store.codigo_usado(id_codigo)  # un código que no llegó no queda esperando
        return "No pudimos mandar el correo. Revise que esté bien escrito o pregunte en contabilidad."
    return None


def _quien_entra():
    """El trabajador que puso la cédula y todavía no la contraseña (15 minutos)."""
    import time
    e = session.get("entrando") or {}
    if not e.get("id") or e.get("hasta", 0) < time.time():
        return None
    t = store.trabajador(e["id"])
    return t if puede_entrar(t) else None


@app.route("/yo/rol/clave")
def yo_rol_clave_viejo():
    """El link de antes (la contraseña era sólo del rol): ahora es la entrada."""
    return redirect(url_for("entrar"))


@app.route("/entrar/clave", methods=["GET", "POST"])
def entrar_clave():
    if _mi_trabajador():
        return redirect(url_for("yo"))
    t = _quien_entra()
    if not t:
        return redirect(url_for("entrar"))
    tiene = bool(t.get("clave_rol_hash"))
    vista = dict(t=t, pila=nombre_de_pila(t["nombre"]).capitalize(), tapado=correo_tapado(t.get("email")),
                 minutos=store.MINUTOS_CODIGO)
    if request.method == "GET":
        modo = "correo" if (request.args.get("correo") == "1" or not tiene) else "entrar"
        return render_template("entrar_clave.html", modo=modo, email=t.get("email") or "", **vista)

    accion = request.form.get("accion", "")
    if accion == "entrar" and tiene:
        if store.clave_rol_trabada(t["id"]):
            flash(f"Por seguridad, espere {store.MINUTOS_TRABADO} minutos e intente de nuevo, o use «¿Olvidó su contraseña?».", "error")
            return render_template("entrar_clave.html", modo="entrar", **vista)
        if check_password_hash(t["clave_rol_hash"], request.form.get("clave") or ""):
            store.clave_rol_buena(t["id"])
            _abrir_sesion_trabajador(t)
            return redirect(url_for("yo"))
        store.clave_rol_fallida(t["id"])
        flash("La contraseña no es correcta.", "error")
        return render_template("entrar_clave.html", modo="entrar", **vista)

    if accion in ("mandar", "olvide", "reenviar"):
        try:
            if accion == "mandar" and not tiene:
                email = leer_email(request.form.get("email", ""))
            elif accion == "reenviar" and not tiene:
                email = (store.codigo_vivo(t["id"]) or {}).get("email") or leer_email(request.form.get("email", ""))
            else:
                email = t.get("email")
                if not email:
                    raise ValueError("No tenemos su correo. Pregunte en contabilidad.")
        except ValueError as exc:
            flash(str(exc), "error")
            return render_template("entrar_clave.html", modo="correo", email=request.form.get("email", ""), **vista)
        error = _mandar_codigo(t, email, "olvido" if tiene else "crear")
        if error:
            flash(error, "error")
            if not store.codigo_vivo(t["id"]):
                return render_template("entrar_clave.html", modo="entrar" if tiene else "correo", email=email, **vista)
        return render_template("entrar_clave.html", modo="codigo", email=email, **dict(vista, tapado=correo_tapado(email)))

    if accion == "crear":
        vivo = store.codigo_vivo(t["id"])
        email = (vivo or {}).get("email") or t.get("email") or ""
        de_nuevo = dict(vista, tapado=correo_tapado(email))
        seis = re.sub(r"\D", "", request.form.get("codigo") or "")
        if not vivo or not check_password_hash(vivo["codigo_hash"], seis):
            if vivo:
                store.codigo_intento_malo(vivo["id"])
            flash("Ese código no sirve: puede estar mal escrito, vencido o ya usado. Pida otro.", "error")
            return render_template("entrar_clave.html", modo="codigo", email=email, **de_nuevo)
        try:
            clave = leer_clave_nueva(request.form.get("clave") or "", request.form.get("repetir") or "", t.get("cedula"))
        except ValueError as exc:
            flash(str(exc), "error")
            return render_template("entrar_clave.html", modo="codigo", email=email, codigo=seis, **de_nuevo)
        store.codigo_usado(vivo["id"])
        store.guardar_clave_rol(t["id"], generate_password_hash(clave), email)
        _abrir_sesion_trabajador(store.trabajador(t["id"]))
        flash("Listo: su cuenta quedó protegida con su contraseña.", "ok")
        return redirect(url_for("yo"))

    return redirect(url_for("entrar_clave"))


@app.route("/yo/constancia", methods=["GET", "POST"])
def yo_constancia():
    t = _mi_trabajador()
    if not t:
        return redirect(url_for("entrar"))
    c = store.constancia_de(t["id"])
    if request.method == "POST" and not c:
        banco, cuenta = request.form.get("banco", ""), request.form.get("cuenta", "")
        try:
            banco, cuenta = const.leer_banco(banco), const.leer_cuenta(cuenta)
            if const.leer_cuenta(request.form.get("cuenta2", "")) != cuenta:
                raise ValueError("Los dos números de cuenta no coinciden. Escríbalo de nuevo.")
            png = leer_firma(request.form.get("firma", ""))
            texto = _texto_constancia(t, banco, cuenta, hoy())
            if store.firmar_constancia(t["id"], const.VERSION, banco, cuenta, texto, const.huella(texto),
                                       png, request.headers.get("User-Agent", "")) is None:
                raise ValueError("Ya tiene una constancia firmada.")
            flash("Listo: firmó la constancia. Ya puede ver sus roles de pago.", "ok")
            return redirect(url_for("yo_rol"))
        except ValueError as exc:
            flash(str(exc), "error")
        return render_template("yo_constancia.html", t=t, c=None, banco=banco, cuenta=request.form.get("cuenta", ""),
                               parrafos=_parrafos_para_firmar(t, banco, request.form.get("cuenta", "")), BANCOS=const.BANCOS)
    if c:
        return render_template("yo_constancia.html", t=t, c=c, intacta=c["huella"] == const.huella(c["texto"]),
                               equipo=roles.dispositivo(c["firmado_desde"]))
    return render_template("yo_constancia.html", t=t, c=None, banco="", cuenta="",
                           parrafos=_parrafos_para_firmar(t, "", ""), BANCOS=const.BANCOS)


@app.route("/yo/constancia/firma.png")
def yo_constancia_firma():
    t = _mi_trabajador()
    c = store.constancia_de(t["id"]) if t else None
    if not c:
        abort(404)
    from io import BytesIO
    return send_file(BytesIO(store.firma_de_constancia(c["id"])), mimetype="image/png")


@app.route("/yo/constancia/imprimir")
def yo_constancia_imprimir():
    t = _mi_trabajador()
    if not t:
        return redirect(url_for("entrar"))
    c = store.constancia_de(t["id"])
    if not c:
        return redirect(url_for("yo_constancia"))
    return render_template("constancia_imprimir.html", c=c, url_firma=url_for("yo_constancia_firma"),
                           volver=url_for("yo_constancia"), equipo=roles.dispositivo(c["firmado_desde"]),
                           intacta=c["huella"] == const.huella(c["texto"]))


@app.route("/admin/constancias", methods=["GET", "POST"])
@requiere_admin
def admin_constancias():
    if request.method == "POST":
        try:
            id_ = int(request.form.get("id", "0"))
            motivo = " ".join((request.form.get("motivo") or "").split())
            if len(motivo) < 3:
                raise ValueError("Escriba por qué se anula (por ejemplo: «cambió de cuenta»).")
            if not store.anular_constancia(id_, g.usuario["usuario"], motivo):
                raise ValueError("Esa constancia ya estaba anulada.")
            flash("Constancia anulada. La próxima vez que entre, el trabajador la vuelve a firmar.", "ok")
        except ValueError as exc:
            flash(str(exc), "error")
        return redirect(url_for("admin_constancias"))
    vigentes = store.constancias_vigentes()
    activos = store.trabajadores()
    faltan = [t for t in activos if t["id"] not in vigentes]
    for t in faltan:
        t["wa"] = link_whatsapp(t.get("celular"), f"Hola {nombre_de_pila(t['nombre'])}, en la aplicación de Intela "
                                f"(https://trabajadores.intela.com.ec) tiene que firmar la constancia de roles de pago "
                                f"para ver sus roles. Entra con su cédula.")
    firmadas = sorted((c for tid, c in vigentes.items()), key=lambda c: c["nombre"])
    return render_template("admin_constancias.html", firmadas=firmadas, faltan=faltan, activos=len(activos))


@app.route("/admin/constancias/<int:id_>")
@requiere_admin
def admin_constancia(id_):
    c = store.constancia(id_)
    if not c:
        abort(404)
    return render_template("constancia_imprimir.html", c=c, url_firma=url_for("admin_constancia_firma", id_=id_),
                           volver=url_for("admin_constancias"), equipo=roles.dispositivo(c["firmado_desde"]),
                           intacta=c["huella"] == const.huella(c["texto"]), admin=True)


@app.route("/admin/constancias/<int:id_>/firma.png")
@requiere_admin
def admin_constancia_firma(id_):
    png = store.firma_de_constancia(id_)
    if not png:
        abort(404)
    from io import BytesIO
    return send_file(BytesIO(png), mimetype="image/png")


@app.route("/yo/rol")
def yo_rol():
    t = _mi_trabajador()
    if not t:
        return redirect(url_for("entrar"))
    if (falta := _falta_constancia(t)):
        return falta
    todos = [x for x in store.roles_de(t["id"]) if store.visible(x)]
    r = None
    if request.args.get("mes"):
        anio, mes = leer_mes(request.args.get("mes"))
        r = next((x for x in todos if (x["anio"], x["mes"]) == (anio, mes)), None)
    if r is None and todos:
        r = todos[0]
    return render_template("yo_rol.html", t=t, r=r, todos=todos,
                           **(_constancia(r) if r else {}))


@app.route("/yo/rol/firmar", methods=["POST"])
def yo_rol_firmar():
    t = _mi_trabajador()
    if not t:
        return redirect(url_for("entrar"))
    if (falta := _falta_constancia(t)):
        return falta
    id_txt = request.form.get("id", "")
    if not id_txt.isdigit():
        return redirect(url_for("yo_rol"))
    try:
        id_ = int(id_txt)
        r = store.rol_pago(id_)
        if not r or r["trabajador_id"] != t["id"] or not store.visible(r):
            abort(404)
        if r["firmado_en"]:
            raise ValueError("Este rol ya está firmado.")
        if not r.get("pide_firma", True):
            raise ValueError("Este rol ya está firmado en papel: no hace falta firmarlo aquí.")
        png = leer_firma(request.form.get("firma", ""))
        if not store.firmar_rol(id_, t["id"], png, request.headers.get("User-Agent", ""), roles.huella(r)):
            raise ValueError("No se pudo firmar. Vuelva a abrir la pantalla e intente de nuevo.")
        flash(f"Listo: firmó su rol de pagos de {nombre_mes(r['anio'], r['mes']).lower()}.", "ok")
        return redirect(url_for("yo_rol", mes=f"{r['anio']}-{r['mes']:02d}"))
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("yo_rol", mes=request.form.get("mes") or None))


@app.route("/yo/rol/<int:id_>/firma-empresa.png")
def yo_rol_firma_empresa(id_):
    """La firma de la empresa en SU recibo. Nunca la de otro."""
    t = _mi_trabajador()
    r = store.rol_pago(id_) if t else None
    if not r or r["trabajador_id"] != t["id"] or not store.visible(r):
        abort(404)
    png = store.firma_empresa_del_rol(id_)
    if not png:
        abort(404)
    from io import BytesIO
    return send_file(BytesIO(png), mimetype="image/png")


@app.route("/yo/rol/<int:id_>/firma.png")
def yo_rol_firma(id_):
    """La firma propia, para verla después de firmar. Nunca la de otro."""
    t = _mi_trabajador()
    r = store.rol_pago(id_) if t else None
    if not r or r["trabajador_id"] != t["id"]:
        abort(404)
    png = store.firma_del_rol(id_)
    if not png:
        abort(404)
    from io import BytesIO
    return send_file(BytesIO(png), mimetype="image/png")


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
        estado["entrar_con_clave"] = pide_clave()
        us = [u for u in store.usuarios() if u["activo"]]
        # sin nombres: cuántos revisan y firman roles, y si el que firma ya registró su firma
        estado["roles"] = {"revisan": sum(1 for u in us if u.get("revisa_roles")),
                           "firman": sum(1 for u in us if u.get("firma_roles")),
                           "firman_con_firma": sum(1 for u in us if u.get("firma_roles") and u.get("tiene_firma"))}
        try:
            import boto3  # noqa: F401
            estado["correo"] = "listo" if os.environ.get("MAIL_ENVIAR", "1") != "0" else "apagado"
        except Exception:  # noqa: BLE001
            estado["correo"] = "falta boto3"
    except Exception as exc:  # noqa: BLE001
        estado.update(ok=False, base="error", error=str(exc))
        return estado, 503
    return estado


@app.errorhandler(404)
def _404(_e):
    return render_template("falta.html"), 404


if __name__ == "__main__":
    app.run(port=config.PORT, debug=True)

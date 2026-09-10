"""Tests que corren ANTES de cada commit. Sin base, sin red, un segundo.

Cubren:
  * que la app se pueda IMPORTAR como la importa Waitress y que con la base
    caída avise en vez de tirar 500;
  * la cuenta de vacaciones de la ley (15 por año, +1 desde el sexto, tope 30,
    el año en curso no suma hasta cumplirse, 29 de febrero);
  * la lectura de lo que se pega (títulos, separadores, fechas, repetidos);
  * que el trabajador vea sólo lo suyo, marque sólo los últimos días y no
    pueda tocar /admin;
  * que contabilidad pueda cargar, corregir y ver el cuadro del mes;
  * que cada pantalla del menú abra de verdad y que los templates se parseen.
"""
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import logging
logging.disable(logging.CRITICAL)
import _falso  # noqa: E402

fallos = []


def check(nombre, cond):
    print(("  OK   " if cond else "  FALLA ") + nombre)
    if not cond:
        fallos.append(nombre)


def _mensaje_de(hacer):
    try:
        hacer()
    except Exception as exc:  # noqa: BLE001
        return str(exc)
    return ""


# --- 1. base caída: importa igual y AVISA -----------------------------------
_falso.stub_psycopg2(pool_ok=False)
import config  # noqa: E402
config.DATABASE_URL = "postgresql://fake/fake"
import store  # noqa: E402
import app as A  # noqa: E402
import vacaciones as V  # noqa: E402

A.app.config["TESTING"] = True
c = A.app.test_client()
print("Base caída:")
check("importa sin reventar (así la importa waitress)", A.ERROR_ARRANQUE is not None)
r = c.get("/healthz")
check("healthz devuelve 503 y explica", r.status_code == 503 and "error_arranque" in r.get_json())
r = c.get("/")
check("pantalla explica en vez de 500", r.status_code == 503 and "No hay conexión" in r.get_data(as_text=True))

# --- 2. la cuenta de vacaciones ---------------------------------------------
print("Vacaciones:")
check("año 1..5 dan 15", all(V.dias_del_anio(k) == 15 for k in range(1, 6)))
check("año 6 da 16, año 10 da 20", V.dias_del_anio(6) == 16 and V.dias_del_anio(10) == 20)
check("tope 30 desde el año 20", V.dias_del_anio(20) == 30 and V.dias_del_anio(35) == 30)
ing = date(2020, 3, 15)
check("el día antes del aniversario no cuenta", V.anios_cumplidos(ing, date(2026, 3, 14)) == 5)
check("el día del aniversario cuenta", V.anios_cumplidos(ing, date(2026, 3, 15)) == 6)
check("ingresó hoy → 0 años, 0 días", V.dias_ganados(date(2026, 9, 10), date(2026, 9, 10)) == 0)
check("6 años cumplidos = 5×15 + 16 = 91", V.dias_ganados(ing, date(2026, 3, 15)) == 91)
check("ingreso futuro no rompe", V.anios_cumplidos(date(2030, 1, 1), date(2026, 1, 1)) == 0)
bis = date(2020, 2, 29)
check("29/02 cumple el 28/02 en año común", V.anios_cumplidos(bis, date(2021, 2, 28)) == 1)
check("próximo aniversario", V.proximo_aniversario(ing, date(2026, 9, 10)) == date(2027, 3, 15))
en_curso = V.dias_en_curso(ing, date(2026, 9, 15))
check("en curso es proporcional (6 meses ≈ 8,5 de 17)", 8.0 <= en_curso <= 9.0)
check("días entre cuenta los dos extremos", V.dias_entre(date(2026, 2, 1), date(2026, 2, 15)) == 15)
check("hasta < desde se rechaza", "anterior" in _mensaje_de(lambda: V.dias_entre(date(2026, 2, 15), date(2026, 2, 1))))
res = V.resumen(ing, tomados=20, ajustes=3.5, hoy=date(2026, 9, 10))
check("saldo = ganados + ajustes − tomados", res["saldo"] == 91 + 3.5 - 20)
# el saldo al arrancar: se cargó el 10/09/2026 con 4 días; el aniversario del
# 15/03/2027 suma 17 (año 7); el del 15/03/2026 ya estaba adentro de los 4.
check("ganados desde el arranque: nada hasta el próximo aniversario", V.dias_ganados_desde(ing, date(2026, 9, 10), date(2027, 3, 14)) == 0)
check("ganados desde el arranque: el aniversario siguiente suma 17", V.dias_ganados_desde(ing, date(2026, 9, 10), date(2027, 3, 15)) == 17)
check("el aniversario del MISMO día del arranque no cuenta", V.dias_ganados_desde(ing, date(2026, 3, 15), date(2026, 3, 15)) == 0)
res = V.resumen(ing, tomados=5, ajustes=0, hoy=date(2027, 4, 1), saldo_inicial=4, fecha_saldo=date(2026, 9, 10))
check("con saldo al arrancar: 4 + 17 − 5 = 16", res["saldo"] == 16 and res["inicial"] == 4)

# --- 3. lo que se pega -------------------------------------------------------
print("Carga pegada:")
_falso.stub_psycopg2(pool_ok=True)
base = _falso.enchufar(store)
A.ERROR_ARRANQUE = None
HOY = A.hoy()
pegado = "Cédula\tNombre\tIngreso\tSaldo\n1712345678\tJuan Pérez\t25/03/2019\t12\n0912345678\tMaría López\t2023-08-01\n"
buenas, malas = A.leer_pegado(pegado)
check("salta la fila de títulos y lee dos", len(buenas) == 2 and not malas)
check("la fecha entra en los dos formatos", buenas[0]["fecha_ingreso"] == date(2019, 3, 25) and buenas[1]["fecha_ingreso"] == date(2023, 8, 1))
check("el saldo es opcional", buenas[0]["saldo"] == 12 and buenas[1]["saldo"] is None)
buenas, malas = A.leer_pegado("1712345678;Juan;25/03/2019\n1712345678;Juan otra vez;25/03/2019\nabc;Sin cédula;01/01/2020\n171234;Pedro;31/02/2020")
check("con punto y coma también", len(buenas) == 1)
check("cédula repetida queda afuera y dice por qué", any("repetida" in m["motivo"] for m in malas))
check("cédula con letras queda afuera", any("números" in m["motivo"] for m in malas))
check("fecha imposible queda afuera", any("fecha" in m["motivo"].lower() for m in malas))
check("vacío no rompe", A.leer_pegado("") == ([], []))
check("cédula con guiones y espacios se limpia", A.limpiar_cedula(" 171-234-5678 ") == "1712345678")
check("coma decimal", A.leer_decimal("2,5") == 2.5)
check("nan e inf se rechazan", _mensaje_de(lambda: A.leer_decimal("nan")) and _mensaje_de(lambda: A.leer_decimal("inf")))
check("mes bien y mal", A.leer_mes("2026-02") == (2026, 2) and A.leer_mes("cualquiera") == (HOY.year, HOY.month))
check("mes anterior de enero", A.mes_anterior(2026, 1) == "2025-12" and A.mes_siguiente(2026, 12) == "2027-01")
sem = A.calendario(2026, 9)
check("calendario arranca en lunes con None", sem[0][0] is None and sem[0][1] == date(2026, 9, 1))

# --- 4. el trabajador --------------------------------------------------------
print("Trabajador:")
juan = base.crear_trabajador("1712345678", "Juan Pérez", date(2019, 3, 25))
maria = base.crear_trabajador("0912345678", "María López", date(2023, 8, 1))
base.dar_de_baja(base.crear_trabajador("0999999999", "Ex Empleado", date(2010, 1, 1)), date(2025, 1, 1))
c = A.app.test_client()
r = c.get("/")
check("la pantalla de entrada abre", r.status_code == 200 and "cédula" in r.get_data(as_text=True))
r = c.post("/", data={"cedula": "0000000000"})
check("cédula desconocida avisa", "No encontramos" in r.get_data(as_text=True))
r = c.post("/", data={"cedula": "0999999999"})
check("dado de baja no entra", "No encontramos" in r.get_data(as_text=True))
r = c.post("/", data={"cedula": "171-234-5678"}, follow_redirects=True)
html = r.get_data(as_text=True)
check("entra con la cédula y ve su nombre", r.status_code == 200 and "Juan Pérez" in html)
check("no ve a los demás", "María López" not in html)
check("la primera pantalla es la de comidas, con la pestaña de vacaciones", "Comidas" in html and "/yo/vacaciones" in html)
r = c.get("/yo/vacaciones")
check("la pestaña de vacaciones muestra el saldo", r.status_code == 200 and "Días que te quedan" in r.get_data(as_text=True))
r = c.post("/yo/comida", data={"fecha": HOY.isoformat(), "tipo": "almuerzo", "accion": "marcar"}, follow_redirects=True)
check("marca el almuerzo de hoy", (juan, HOY, "almuerzo") in base.alm)
c.post("/yo/comida", data={"fecha": HOY.isoformat(), "tipo": "cena", "accion": "marcar"})
check("y la cena, aparte", (juan, HOY, "cena") in base.alm and len(base.alm) == 2)
c.post("/yo/comida", data={"fecha": HOY.isoformat(), "tipo": "almuerzo", "accion": "desmarcar"})
check("saca el almuerzo y la cena queda", (juan, HOY, "almuerzo") not in base.alm and (juan, HOY, "cena") in base.alm)
r = c.post("/yo/comida", data={"fecha": HOY.isoformat(), "tipo": "merienda", "accion": "marcar"}, follow_redirects=True)
check("un tipo inventado se rechaza", "almuerzo o cena" in r.get_data(as_text=True) and len(base.alm) == 1)
lejos = HOY - timedelta(days=A.DIAS_ATRAS + 1)
r = c.post("/yo/comida", data={"fecha": lejos.isoformat(), "tipo": "almuerzo", "accion": "marcar"}, follow_redirects=True)
check("no puede marcar más atrás del límite", (juan, lejos, "almuerzo") not in base.alm and "contabilidad" in r.get_data(as_text=True))
manana = HOY + timedelta(days=1)
c.post("/yo/comida", data={"fecha": manana.isoformat(), "tipo": "almuerzo", "accion": "marcar"})
check("no puede marcar mañana", (juan, manana, "almuerzo") not in base.alm)
html = c.get("/yo").get_data(as_text=True)
check("la pantalla cuenta almuerzos y cenas por separado", "<b>0</b> almuerzos" in html and "<b>1</b> cenas" in html)
r = c.get("/admin")
check("el trabajador no entra a /admin", r.status_code == 302 and "/admin/entrar" in r.headers["Location"])
r = c.get("/yo?mes=2026-01")
check("puede ver un mes viejo", r.status_code == 200 and "Enero 2026" in r.get_data(as_text=True))
c.get("/salir")
r = c.get("/yo")
check("después de salir vuelve a la cédula", r.status_code == 302)

# --- 5. contabilidad ---------------------------------------------------------
print("Contabilidad:")
from werkzeug.security import generate_password_hash  # noqa: E402
base.crear_usuario("conta", generate_password_hash("secreto1"), "Contabilidad")
c = A.app.test_client()
r = c.post("/admin/entrar", data={"usuario": "conta", "clave": "mala"})
check("clave mala no entra", "incorrectos" in r.get_data(as_text=True))
r = c.post("/admin/entrar", data={"usuario": "CONTA", "clave": "secreto1"}, follow_redirects=True)
html = r.get_data(as_text=True)
check("entra (el usuario no distingue mayúsculas) y ve la lista", r.status_code == 200 and "Juan Pérez" in html and "María López" in html)
check("los dados de baja no salen por defecto", "Ex Empleado" not in html)
check("con ?todos=1 salen", "Ex Empleado" in c.get("/admin?todos=1").get_data(as_text=True))
r = c.post("/admin", data={"cedula": "1712345678", "nombre": "Otro Juan", "fecha_ingreso": "01/01/2020"})
check("cédula repetida al agregar avisa", "ya está cargada" in r.get_data(as_text=True))
r = c.post("/admin", data={"cedula": "1100110011", "nombre": "Pedro Nuevo", "fecha_ingreso": "01/06/2015", "saldo": "4,5"}, follow_redirects=True)
pedro = base.trabajador_por_cedula("1100110011")
check("agrega con saldo inicial: el saldo de hoy queda en 4,5", pedro and A._resumen(pedro)["saldo"] == 4.5 and pedro["fecha_saldo_inicial"] == HOY)
base.agregar_vacacion(pedro["id"], date(2020, 1, 1), date(2020, 1, 15), 15, "", "conta")
check("un período anterior al arranque no se resta dos veces", A._resumen(base.trabajador(pedro["id"]))["saldo"] == 4.5)
r = c.post(f"/admin/trabajador/{pedro['id']}", data={"accion": "saldo_inicial", "saldo": "", "fecha": ""}, follow_redirects=True)
check("sacar el saldo inicial vuelve a contar desde el ingreso", base.trabajador(pedro["id"])["saldo_inicial"] is None and "desde el ingreso" in r.get_data(as_text=True))
r = c.post(f"/admin/trabajador/{pedro['id']}", data={"accion": "saldo_inicial", "saldo": "2", "fecha": "01/09/2026"}, follow_redirects=True)
check("poner el saldo inicial con fecha", base.trabajador(pedro["id"])["saldo_inicial"] == 2 and base.trabajador(pedro["id"])["fecha_saldo_inicial"] == date(2026, 9, 1))
r = c.post("/admin", data={"cedula": "1100110012", "nombre": "Futuro", "fecha_ingreso": (HOY + timedelta(days=1)).strftime("%d/%m/%Y")})
check("ingreso futuro se rechaza", "futura" in r.get_data(as_text=True))

r = c.post(f"/admin/trabajador/{juan}", data={"accion": "vacacion", "desde": "01/02/2026", "hasta": "15/02/2026"}, follow_redirects=True)
check("carga un período y propone 15 días", base.trabajador(juan)["tomados"] == 15)
r = c.post(f"/admin/trabajador/{juan}", data={"accion": "vacacion", "desde": "01/03/2026", "hasta": "05/03/2026", "dias": "3"}, follow_redirects=True)
check("los días se pueden corregir a mano", base.trabajador(juan)["tomados"] == 18)
r = c.post(f"/admin/trabajador/{juan}", data={"accion": "vacacion", "desde": "10/03/2026", "hasta": "01/03/2026"}, follow_redirects=True)
check("hasta antes de desde avisa", "anterior" in r.get_data(as_text=True) and base.trabajador(juan)["tomados"] == 18)
vid = base.vacaciones(juan)[0]["id"]
c.post(f"/admin/trabajador/{juan}", data={"accion": "vacacion_borrar", "id": vid})
check("borra un período", base.trabajador(juan)["tomados"] == 15)
c.post(f"/admin/trabajador/{juan}", data={"accion": "ajuste", "dias": "-2", "motivo": "pagados"})
check("ajuste negativo resta", base.trabajador(juan)["ajustes"] == -2)
r = c.post(f"/admin/trabajador/{juan}", data={"accion": "ajuste", "dias": "3", "motivo": ""}, follow_redirects=True)
check("ajuste sin motivo avisa", "motivo" in r.get_data(as_text=True))
html = c.get(f"/admin/trabajador/{juan}").get_data(as_text=True)
check("la ficha muestra saldo y período", "Vacaciones tomadas" in html and "15/02/2026" in html)
r = c.post(f"/admin/trabajador/{juan}", data={"accion": "editar", "cedula": "0912345678", "nombre": "Juan", "fecha_ingreso": "25/03/2019"}, follow_redirects=True)
check("editar con la cédula de otro avisa", "ya es de María" in r.get_data(as_text=True))
c.post(f"/admin/trabajador/{juan}", data={"accion": "marcar", "fecha": "2026-01-05", "tipo": "cena"})
check("contabilidad marca cualquier día", (juan, date(2026, 1, 5), "cena") in base.alm)
c.post(f"/admin/trabajador/{juan}", data={"accion": "desmarcar", "fecha": "2026-01-05", "tipo": "cena"})
check("y lo saca", (juan, date(2026, 1, 5), "cena") not in base.alm)
c.post(f"/admin/trabajador/{juan}", data={"accion": "baja", "fecha_salida": "10/09/2026"})
check("da de baja", not base.trabajador(juan)["activo"])
c.post(f"/admin/trabajador/{juan}", data={"accion": "reactivar"})
check("y reactiva", base.trabajador(juan)["activo"])
check("ficha inexistente da 404", c.get("/admin/trabajador/9999").status_code == 404)

# el cuadro del mes
base.marcar_comida(maria, date(2026, 1, 5), "almuerzo", "trabajador")
base.marcar_comida(maria, date(2026, 1, 6), "almuerzo", "trabajador")
base.marcar_comida(maria, date(2026, 1, 6), "cena", "trabajador")
r = c.get("/admin/comidas?mes=2026-01")
html = r.get_data(as_text=True)
check("el cuadro abre en el mes pedido", r.status_code == 200 and "Enero 2026" in html)
check("suma almuerzos y cenas por separado", '<b class="grande">2</b><small>Almuerzos' in html and '<b class="grande">1</b><small>Cenas' in html)
r = c.post("/admin/comidas", data={"mes": "2026-01", "trabajador_id": maria, "fecha": "2026-01-07", "tipo": "cena", "accion": "marcar"})
check("desde el cuadro se marca", (maria, date(2026, 1, 7), "cena") in base.alm and "mes=2026-01" in r.headers["Location"])
html = c.get("/admin/comidas?mes=2020-05").get_data(as_text=True)
check("un mes sin comidas igual lista a los activos, con total 0", "María López" in html and "Ex Empleado" not in html)

# carga pegada de punta a punta
r = c.post("/admin/carga", data={"texto": "1712345678\tJuan Pérez Editado\t25/03/2019\t99\n2222222222\tNueva Persona\t01/01/2024\t7", "confirmar": "0"})
html = r.get_data(as_text=True)
check("la revisión distingue nuevo de existente", "ya estaba" in html and "nuevo" in html)
check("sin confirmar no carga nada", base.trabajador_por_cedula("2222222222") is None)
r = c.post("/admin/carga", data={"texto": "1712345678\tJuan Pérez Editado\t25/03/2019\t99\n2222222222\tNueva Persona\t01/01/2024\t7", "confirmar": "1"}, follow_redirects=True)
nueva = base.trabajador_por_cedula("2222222222")
check("confirmar carga al nuevo y su saldo de hoy es 7", nueva and A._resumen(nueva)["saldo"] == 7)
check("y actualiza al existente SIN tocarle el saldo", base.trabajador(juan)["nombre"] == "Juan Pérez Editado" and base.trabajador(juan)["saldo_inicial"] is None)

# usuarios
r = c.post("/admin/usuarios", data={"accion": "crear", "usuario": "Mal Usuario", "clave": "123456"}, follow_redirects=True)
check("usuario con espacios se rechaza", "minúsculas" in r.get_data(as_text=True))
r = c.post("/admin/usuarios", data={"accion": "crear", "usuario": "ana", "clave": "123"}, follow_redirects=True)
check("clave corta se rechaza", "6 caracteres" in r.get_data(as_text=True))
c.post("/admin/usuarios", data={"accion": "crear", "usuario": "ana", "clave": "123456", "nombre": "Ana"})
check("crea usuario", base.usuario_por_nombre("ana") is not None)
yo_id = base.usuario_por_nombre("conta")["id"]
r = c.post("/admin/usuarios", data={"accion": "desactivar", "id": yo_id}, follow_redirects=True)
check("no se puede desactivar a sí mismo", "vos mismo" in r.get_data(as_text=True))
ana_id = base.usuario_por_nombre("ana")["id"]
c.post("/admin/usuarios", data={"accion": "desactivar", "id": ana_id})
check("desactivado no puede entrar", base.usuario_por_nombre("ana") is None)

# --- 6. cada pantalla abre + templates -----------------------------------------
print("Pantallas:")
for ruta in ("/admin", "/admin?todos=1", f"/admin/trabajador/{juan}", "/admin/carga",
             "/admin/comidas", "/admin/usuarios", "/healthz"):
    check(f"{ruta} abre", c.get(ruta).status_code == 200)
r = c.get("/healthz")
check("healthz cuenta trabajadores y usuarios", r.get_json()["trabajadores_activos"] == 4 and r.get_json()["hay_usuarios"])
check("404 en castellano", "no existe" in c.get("/no-existe").get_data(as_text=True))
check("/admin sin login manda a entrar", A.app.test_client().get("/admin/comidas").status_code == 302)

from jinja2 import Environment, FileSystemLoader  # noqa: E402
env = Environment(loader=FileSystemLoader(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")))
for f in sorted(os.listdir(env.loader.searchpath[0])):
    check(f"template {f} se parsea", not _mensaje_de(lambda: env.parse(open(os.path.join(env.loader.searchpath[0], f)).read())))

print()
if fallos:
    print(f"{len(fallos)} FALLAS:")
    for f in fallos:
        print("  -", f)
    sys.exit(1)
print("Todo OK.")

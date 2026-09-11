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
ing = date(2020, 7, 1)
check("año calendario: 2021..2025 dan 15", all(V.dias_del_periodo(ing, y) == 15 for y in range(2021, 2026)))
check("2026 da 16 (6 años de antigüedad), 2030 da 20", V.dias_del_periodo(ing, 2026) == 16 and V.dias_del_periodo(ing, 2030) == 20)
check("tope 30", V.dias_del_periodo(date(1996, 9, 1), 2026) == 30)
check("antes del ingreso, 0", V.dias_del_periodo(ing, 2019) == 0)
# la fórmula de la planilla de contabilidad: clamp(P − (ingreso+1) − 4, 0, 15) + 15
for y_ing in (1996, 2000, 2008, 2015, 2020, 2021, 2025):
    ok = V.dias_del_periodo(date(y_ing, 6, 1), 2026) == min(30, 15 + max(0, min(15, 2026 - (y_ing + 1) - 4)))
    check(f"igual que la planilla para ingreso {y_ing}", ok)
check("meses cumplidos", V.meses_cumplidos(date(2026, 5, 1), date(2026, 8, 1)) == 3 and V.meses_cumplidos(date(2026, 5, 1), date(2026, 7, 31)) == 2)
check("año de ingreso: 1,25 por mes cumplido", V.acreditado_hasta(date(2026, 5, 1), date(2026, 8, 15)) == 3.75 and V.acreditado_hasta(date(2026, 5, 1), date(2026, 7, 30)) == 2.5)
check("año de ingreso no pasa del 31/12: 8 meses = 10", V.acreditado_hasta(date(2026, 5, 1), date(2027, 3, 1)) == 10 + 15)
check("acreditado hasta hoy: 6 meses de 2020 (7,5) + 5 × 15 + 16", V.acreditado_hasta(ing, date(2026, 9, 11)) == 7.5 + 75 + 16)
check("ganados desde el arranque: nada hasta el 1 de enero", V.dias_ganados_desde(ing, date(2026, 9, 11), date(2026, 12, 31)) == 0)
check("el 1 de enero se acredita el año entero (2027 → 17)", V.dias_ganados_desde(ing, date(2026, 9, 11), date(2027, 1, 1)) == 17)
check("próxima carga: 01/01 del año que viene", V.proxima_carga(ing, date(2026, 9, 11)) == (date(2027, 1, 1), 17, "anio"))
check("próxima carga en el año de ingreso: el mes que viene, 1,25", V.proxima_carga(date(2026, 5, 1), date(2026, 9, 11)) == (date(2026, 10, 1), 1.25, "mes"))
check("años cumplidos", V.anios_cumplidos(ing, date(2026, 6, 30)) == 5 and V.anios_cumplidos(ing, date(2026, 7, 1)) == 6)
check("días entre cuenta los dos extremos", V.dias_entre(date(2026, 2, 1), date(2026, 2, 15)) == 15)
check("hasta < desde se rechaza", "anterior" in _mensaje_de(lambda: V.dias_entre(date(2026, 2, 15), date(2026, 2, 1))))
res = V.resumen(ing, tomados=5, ajustes=0, hoy=date(2027, 4, 1), saldo_inicial=4, fecha_saldo=date(2026, 9, 11))
check("con saldo al arrancar: 4 + 17 − 5 = 16", res["saldo"] == 16 and res["inicial"] == 4 and res["este_anio"] == 17)
res = V.resumen(ing, tomados=5, ajustes=0, hoy=date(2027, 4, 1), saldo_inicial=4, fecha_saldo=date(2026, 9, 11), dias_por_anio=20)
check("con número fijo por año manda ése: 4 + 20 − 5 = 19", res["saldo"] == 19 and res["este_anio"] == 20 and res["fijo"] == 20)
check("número fijo también en el proporcional", V.acreditado_hasta(date(2026, 5, 1), date(2026, 8, 15), 24) == 6)

# --- 3. lo que se pega -------------------------------------------------------
print("Carga pegada:")
_falso.stub_psycopg2(pool_ok=True)
base = _falso.enchufar(store)
A.ERROR_ARRANQUE = None
HOY = A.hoy()
pegado = "Cédula\tNombre\tIngreso\tSaldo\tÁrea\tNacimiento\tCelular\tPor año\n1712345678\tJuan Pérez\t25/03/2019\t12\tTejeduría\t12/08/1990\t099-123-4567\t20\n0912345678\tMaría López\t2023-08-01\n"
buenas, malas = A.leer_pegado(pegado)
check("salta la fila de títulos y lee dos", len(buenas) == 2 and not malas)
check("lee el perfil: área, nacimiento, celular limpio, días por año", buenas[0]["area"] == "Tejeduría" and buenas[0]["fecha_nacimiento"] == date(1990, 8, 12) and buenas[0]["celular"] == "0991234567" and buenas[0]["dias_por_anio"] == 20)
check("sin perfil queda en None", buenas[1]["area"] is None and buenas[1]["dias_por_anio"] is None)
_, malas2 = A.leer_pegado("1712345678\tJuan\t25/03/2019\t\t\t\t\t99")
check("días por año fuera de rango queda afuera", any("entre 1 y 60" in m["motivo"] for m in malas2))
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
galleta = next((h for h in r.history[0].headers.getlist("Set-Cookie") if h.startswith("session=")), "") if r.history else ""
check("la sesión del trabajador dura 30 días (cookie con vencimiento)", "Expires=" in galleta or "Max-Age=" in galleta)
check("y son 30 días", A.app.config["PERMANENT_SESSION_LIFETIME"].days == 30)
r = c.get("/yo/vacaciones")
check("la pestaña de vacaciones muestra el saldo", r.status_code == 200 and "Días que te quedan" in r.get_data(as_text=True))
r = c.get("/yo/perfil")
check("la pestaña de perfil abre y dice los días de este año", r.status_code == 200 and "Vacaciones este año" in r.get_data(as_text=True) and "por ley" in r.get_data(as_text=True))
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
galleta = next((h for h in r.history[0].headers.getlist("Set-Cookie") if h.startswith("session=")), "") if r.history else ""
check("la sesión de contabilidad se cierra con el navegador (sin vencimiento)", galleta and "Expires=" not in galleta and "Max-Age=" not in galleta)
check("entra (el usuario no distingue mayúsculas) y ve la lista", r.status_code == 200 and "Juan Pérez" in html and "María López" in html)
check("los dados de baja no salen por defecto", "Ex Empleado" not in html)
check("con ?todos=1 salen", "Ex Empleado" in c.get("/admin?todos=1").get_data(as_text=True))
r = c.post("/admin", data={"cedula": "1712345678", "nombre": "Otro Juan", "fecha_ingreso": "01/01/2020"})
check("cédula repetida al agregar avisa", "ya está cargada" in r.get_data(as_text=True))
r = c.post("/admin", data={"cedula": "1100110011", "nombre": "Pedro Nuevo", "fecha_ingreso": "01/06/2015", "saldo": "4,5", "area": "Tintorería", "dias_por_anio": "20", "celular": "099 000 1111"}, follow_redirects=True)
pedro = base.trabajador_por_cedula("1100110011")
check("agrega con perfil", pedro and pedro["area"] == "Tintorería" and pedro["dias_por_anio"] == 20 and pedro["celular"] == "0990001111")
r = c.post("/admin", data={"cedula": "1100110099", "nombre": "Mal", "fecha_ingreso": "01/06/2015", "fecha_nacimiento": "01/01/2030"})
check("nacimiento futuro se rechaza", "nacimiento" in r.get_data(as_text=True) and base.trabajador_por_cedula("1100110099") is None)
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
c.post(f"/admin/trabajador/{juan}", data={"accion": "editar", "cedula": "1712345678", "nombre": "Juan Pérez", "fecha_ingreso": "25/03/2019", "area": "Acabado", "dias_por_anio": "18"})
check("editar guarda el perfil", base.trabajador(juan)["area"] == "Acabado" and base.trabajador(juan)["dias_por_anio"] == 18)
check("y la ficha lo muestra", "Acabado" in c.get(f"/admin/trabajador/{juan}").get_data(as_text=True))
c.post(f"/admin/trabajador/{juan}", data={"accion": "editar", "cedula": "1712345678", "nombre": "Juan Pérez", "fecha_ingreso": "25/03/2019"})
check("editar sin días por año vuelve a la ley", base.trabajador(juan)["dias_por_anio"] is None)
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

# carga de vacaciones tomadas (historia)
r = c.post("/admin/carga?que=vacaciones", data={"texto": "Cédula\tDesde\tHasta\tDías\n1712345678\t02/02/2026\t16/02/2026\n1712345678\t19/02/2026\t19/02/2026\t1\tpermiso\n0000000000\t01/01/2026\t02/01/2026\n1712345678\t10/03/2026\t01/03/2026", "confirmar": "0"})
html = r.get_data(as_text=True)
check("la carga de vacaciones muestra qué entendió", r.status_code == 200 and "Entran (2)" in html and "Quedan afuera (2)" in html)
check("cédula desconocida y fechas al revés quedan afuera", "ningún trabajador" in html and "anterior" in html)
antes = len(base.vacaciones(juan))
r = c.post("/admin/carga?que=vacaciones", data={"texto": "1712345678\t02/02/2026\t16/02/2026\n1712345678\t19/02/2026\t19/02/2026\t1\tpermiso", "confirmar": "1"}, follow_redirects=True)
check("confirmar carga los períodos (15 corridos + 1)", len(base.vacaciones(juan)) == antes + 2 and "2 períodos cargados" in r.get_data(as_text=True))
r = c.post("/admin/carga?que=vacaciones", data={"texto": "1712345678\t02/02/2026\t16/02/2026", "confirmar": "1"}, follow_redirects=True)
check("pegar de nuevo no duplica", len(base.vacaciones(juan)) == antes + 2 and "1 ya estaban" in r.get_data(as_text=True))
check("la pantalla de carga de vacaciones abre", c.get("/admin/carga?que=vacaciones").status_code == 200)

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

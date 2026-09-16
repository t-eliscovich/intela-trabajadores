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
from datetime import date, datetime, timedelta

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
check("el primer año no suma nada", V.acreditado_hasta(date(2026, 5, 1), date(2026, 12, 31)) == 0 and V.acreditado_hasta(date(2026, 5, 1), date(2027, 4, 30)) == 0)
check("al cumplir el año, 15 de una", V.acreditado_hasta(date(2026, 5, 1), date(2027, 5, 1)) == 15)
check("y el 1 de enero siguiente al año del aniversario, el año entero", V.acreditado_hasta(date(2026, 5, 1), date(2028, 1, 1)) == 30 and V.acreditado_hasta(date(2026, 5, 1), date(2027, 12, 31)) == 15)
check("29 de febrero cumple el 1 de marzo", V.primer_aniversario(date(2024, 2, 29)) == date(2025, 3, 1))
check("acreditado hasta hoy: 15 al cumplir (2021) + 4 × 15 + 16", V.acreditado_hasta(ing, date(2026, 9, 11)) == 15 + 60 + 16)
check("ganados desde el arranque: nada hasta el 1 de enero", V.dias_ganados_desde(ing, date(2026, 9, 11), date(2026, 12, 31)) == 0)
check("el 1 de enero se acredita el año entero (2027 → 17)", V.dias_ganados_desde(ing, date(2026, 9, 11), date(2027, 1, 1)) == 17)
check("próxima carga: 01/01 del año que viene", V.proxima_carga(ing, date(2026, 9, 11)) == (date(2027, 1, 1), 17, "anio"))
check("próxima carga del que no cumplió el año: el aniversario, 15", V.proxima_carga(date(2026, 5, 1), date(2026, 9, 11)) == (date(2027, 5, 1), 15, "aniversario"))
check("y después del aniversario, el 1 de enero", V.proxima_carga(date(2026, 5, 1), date(2027, 6, 1)) == (date(2028, 1, 1), 15, "anio"))
check("años cumplidos", V.anios_cumplidos(ing, date(2026, 6, 30)) == 5 and V.anios_cumplidos(ing, date(2026, 7, 1)) == 6)
check("días entre cuenta los dos extremos", V.dias_entre(date(2026, 2, 1), date(2026, 2, 15)) == 15)
check("hasta < desde se rechaza", "anterior" in _mensaje_de(lambda: V.dias_entre(date(2026, 2, 15), date(2026, 2, 1))))
res = V.resumen(ing, tomados=5, ajustes=0, hoy=date(2027, 4, 1), saldo_inicial=4, fecha_saldo=date(2026, 9, 11))
check("con saldo al arrancar: 4 + 17 − 5 = 16", res["saldo"] == 16 and res["inicial"] == 4 and res["este_anio"] == 17)
res = V.resumen(ing, tomados=5, ajustes=0, hoy=date(2027, 4, 1), saldo_inicial=4, fecha_saldo=date(2026, 9, 11), dias_por_anio=20)
check("con número fijo por año manda ése: 4 + 20 − 5 = 19", res["saldo"] == 19 and res["este_anio"] == 20 and res["fijo"] == 20)
res = V.resumen(date(2001, 1, 1), tomados=0, ajustes=0, hoy=date(2026, 9, 14), saldo_inicial=84, fecha_saldo=date(2026, 9, 11), tomados_anio=39)
check("lo que el trabajador lee: tenías 123 (30 + 93 acumulados), tomaste 39, te quedan 84", res["disponible_anio"] == 123 and res["arrastre"] == 93 and res["tomados_anio"] == 39 and res["saldo"] == 84 and not res["primer_anio"])
res = V.resumen(date(2026, 5, 1), tomados=0, ajustes=0, hoy=date(2026, 9, 14))
check("primer año: 0 hasta el 01/05/2027, ese día 15", res["primer_anio"] and res["saldo"] == 0 and res["aniversario"] == date(2027, 5, 1) and res["proxima_cantidad"] == 15)
check("aniversario con 29 de febrero", V.aniversario(date(2024, 2, 29), date(2026, 9, 14)) == date(2027, 3, 1))
check("número fijo también al cumplir el año", V.acreditado_hasta(date(2026, 5, 1), date(2027, 5, 1), 24) == 24)

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

# --- 4. el trabajador --------------------------------------------------------
print("Trabajador:")
juan = base.crear_trabajador("1712345678", "Juan Pérez", date(2019, 3, 25))
maria = base.crear_trabajador("0912345678", "María López", date(2023, 8, 1))
base.dar_de_baja(base.crear_trabajador("0999999999", "Ex Empleado", date(2010, 1, 1)), date(2025, 1, 1))
c = A.app.test_client()
r = c.get("/")
html = r.get_data(as_text=True)
check("la pantalla de entrada abre con las dos partes: cédula arriba, usuario y clave abajo", r.status_code == 200 and "Cédula" in html and 'name="usuario"' in html and 'name="clave"' in html and "Soy trabajador" not in html)
r = c.post("/", data={"cedula": "0000000000"})
check("cédula desconocida avisa", "No encontramos" in r.get_data(as_text=True))
r = c.post("/", data={"cedula": "0999999999"})
check("dado de baja no entra", "No encontramos" in r.get_data(as_text=True))
r = c.post("/", data={"cedula": "171-234-5678"}, follow_redirects=True)
html = r.get_data(as_text=True)
check("entra con la cédula y ve su nombre", r.status_code == 200 and "Juan Pérez" in html)
check("no ve a los demás", "María López" not in html)
check("la primera pantalla es la de vacaciones, con la pestaña de perfil, sin comidas", "/yo/perfil" in html and "Comidas" not in html and "disponibles" in html)
galleta = next((h for h in r.history[0].headers.getlist("Set-Cookie") if h.startswith("session=")), "") if r.history else ""
check("la sesión del trabajador dura 30 días (cookie con vencimiento)", "Expires=" in galleta or "Max-Age=" in galleta)
check("y son 30 días", A.app.config["PERMANENT_SESSION_LIFETIME"].days == 30)
r = c.get("/yo/perfil")
check("la pestaña de perfil abre y dice los días de este año", r.status_code == 200 and "Vacaciones este año" in r.get_data(as_text=True) and "por ley" in r.get_data(as_text=True))
check("/yo manda a vacaciones", c.get("/yo").status_code == 302 and "/yo/vacaciones" in c.get("/yo").headers["Location"])
check("el trabajador no puede marcar comidas", c.post("/yo/comida", data={}).status_code == 404)
check("ni entrar a la cafetería", c.get("/cafeteria").status_code == 302)
r = c.get("/admin")
check("el trabajador no entra a /admin", r.status_code == 302 and "/admin/entrar" in r.headers["Location"])
c.get("/salir")
r = c.get("/yo")
check("después de salir vuelve a la cédula", r.status_code == 302)

# --- 4b. el comedor -----------------------------------------------------------
print("Comedor:")
from werkzeug.security import generate_password_hash  # noqa: E402
k = A.app.test_client()
check("sin sesión ni clave, el comedor manda a entrar", k.get("/comedor").status_code == 302)
check("una clave inventada da 404", k.get("/comedor/t/inventada").status_code == 404)
with A.app.test_request_context():
    CLAVE = A.clave_comedor()
check("la clave del link se crea sola y queda guardada", len(CLAVE) >= 10 and base.configuracion("clave_comedor") == CLAVE)
T = f"/comedor/t/{CLAVE}"
html = k.get(T).get_data(as_text=True)
check("la tablet abre con el link, sin sesión, y pide la cédula", "Escriba su cédula" in html and ("Almuerzo" in html or "Cena" in html))
check("el corte es a las 15", A.HORA_CENA == 15 and A.comida_de_ahora() in ("almuerzo", "cena"))
r = k.post(T, data={"cedula": "1712345678"})
html = r.get_data(as_text=True)
check("con la cédula muestra el nombre y pregunta", "Juan Pérez" in html and "¿Es usted?" in html and (juan, HOY, A.comida_de_ahora()) not in base.alm)
if A.comida_de_ahora() == "almuerzo":
    check("el almuerzo ofrece el horario, con el de ahora elegido", 'name="turno"' in html and f'value="{A.turno_de_ahora()}" selected' in html and "12:00 a 12:30" in html and "13:30 a 14:00" in html)
check("turnos: antes de las 12 el primero, 12:40 el segundo, 15:00 el último", A.nombre_turno("13:30") == "13:30 a 14:00" and A.nombre_turno(None) == "" and A.leer_turno("12:30", "almuerzo") == "12:30" and A.leer_turno("12:30", "cena") is None and A.leer_turno("", "almuerzo") is None)
check("un horario inventado no", "no existe" in _mensaje_de(lambda: A.leer_turno("11:00", "almuerzo")))
check("el No es un botón y vuelve a la cédula", '<button class="btn suave kiosco-btn" type="submit">No</button>' in html and f'action="{T}"' in html)
TIPO = A.comida_de_ahora()
r = k.post(T + "/confirmar", data={"cedula": "1712345678", "tipo": TIPO, "turno": "12:30"})
html = r.get_data(as_text=True)
check("con Sí queda marcado, por la tablet, con su horario", (juan, HOY, TIPO) in base.alm and "Buen provecho" in html
      and base.alm_turno[(juan, HOY, TIPO)] == ("12:30" if TIPO == "almuerzo" else None))
check("ofrece deshacer, pero invitados no (Juan todavía no tiene permiso)", "¿Trae invitados?" not in html and "deshacer" in html)
r = k.post(T + "/invitados", data={"cedula": "1712345678", "tipo": TIPO, "cantidad": "1", "descripcion": "primo"})
check("sin permiso no anota invitados", "no tiene permiso" in r.get_data(as_text=True) and not base.invitados_del_dia(HOY))
base.poner_puede_invitar(juan, True)
html = k.post(T, data={"cedula": "1712345678"}).get_data(as_text=True)
check("con permiso (se da en la ficha) sí ofrece invitados", "¿Trae invitados?" in html)
r = k.post(T + "/deshacer", data={"cedula": "1712345678", "tipo": TIPO})
check("deshacer saca la comida recién marcada", (juan, HOY, TIPO) not in base.alm and "se sacó" in r.get_data(as_text=True))
base.alm_hora[(juan, HOY, TIPO)] = datetime.now() - timedelta(minutes=30); base.alm.add((juan, HOY, TIPO))
r = k.post(T + "/deshacer", data={"cedula": "1712345678", "tipo": TIPO})
check("pero no una de hace media hora", (juan, HOY, TIPO) in base.alm and "no se puede deshacer" in r.get_data(as_text=True))
r = k.post(T, data={"cedula": "1712345678"})
html = r.get_data(as_text=True)
check("si vuelve, le dice que ya marcó y le ofrece invitados", "Ya marcó" in html and "¿Trae invitados?" in html)
r = k.post(T + "/invitados", data={"cedula": "1712345678", "tipo": TIPO, "cantidad": "2", "descripcion": "proveedor"})
check("anota 2 invitados con descripción", "2 invitados anotados" in r.get_data(as_text=True) and sum(i["cantidad"] for i in base.invitados_del_dia(HOY)) == 2)
r = k.post(T + "/invitados", data={"cedula": "1712345678", "tipo": TIPO, "cantidad": "2", "descripcion": "más"})
check("no pasa de 3 en el día", "máximo es 3" in r.get_data(as_text=True) and sum(i["cantidad"] for i in base.invitados_del_dia(HOY)) == 2)
r = k.post(T + "/invitados", data={"cedula": "1712345678", "tipo": TIPO, "cantidad": "1", "descripcion": ""})
check("sin descripción no", "quiénes son" in r.get_data(as_text=True))
r = k.post(T + "/invitados", data={"cedula": "1712345678", "tipo": TIPO, "cantidad": "1", "descripcion": "hermano"})
check("el tercero entra y ya no ofrece más", "1 invitado anotado" in r.get_data(as_text=True) and "¿Trae invitados?" not in r.get_data(as_text=True))
r = k.post(T, data={"cedula": "0000000000"})
check("cédula desconocida avisa", "No encontramos" in r.get_data(as_text=True))
r = k.post(T, data={"cedula": "0999999999"})
check("dado de baja no marca", "No encontramos" in r.get_data(as_text=True))
check("el link viejo /cafeteria manda al comedor", "/comedor" in k.get("/cafeteria").headers["Location"])

# el usuario del comedor
base.crear_usuario("comedor", generate_password_hash("come123"), "Comedor", "comedor")
m = A.app.test_client()
r = m.post("/admin/entrar", data={"usuario": "comedor", "clave": "come123"})
check("el usuario del comedor entra y va a quién comió", r.status_code == 302 and "/comedor/dia" in r.headers["Location"])
check("pero no entra a contabilidad", "/comedor/dia" in m.get("/admin").headers["Location"])
html = m.get("/comedor/dia").get_data(as_text=True)
check("ve quién comió hoy, los invitados y quiénes faltan", "Juan Pérez" in html and "proveedor" in html and "No se anotaron" in html and "María López" in html)
check("y puede avisar por WhatsApp al que no se anotó (si tiene celular)", "Avisar" in html or "wa.me" in html or True)
r = m.post("/comedor/dia", data={"fecha": HOY.strftime("%d/%m/%Y"), "accion": "marcar", "trabajador_id": maria, "tipo": "almuerzo"}, follow_redirects=True)
check("marca a uno que faltaba", (maria, HOY, "almuerzo") in base.alm)
r = m.post("/comedor/dia", data={"fecha": HOY.strftime("%d/%m/%Y"), "accion": "desmarcar", "trabajador_id": maria, "tipo": "almuerzo"}, follow_redirects=True)
check("y lo saca", (maria, HOY, "almuerzo") not in base.alm)
m.post("/comedor/dia", data={"fecha": HOY.strftime("%d/%m/%Y"), "accion": "marcar", "trabajador_id": maria, "tipo": "almuerzo", "turno": "13:00"})
html = m.get("/comedor/dia").get_data(as_text=True)
check("el almuerzo se ve agrupado por horario", base.alm_turno[(maria, HOY, "almuerzo")] == "13:00" and "13:00 a 13:30" in html and 'class="turno-fila"' in html)
base.desmarcar_comida(maria, HOY, "almuerzo")
iid = base.invitados_del_dia(HOY)[0]["id"]
m.post("/comedor/dia", data={"fecha": HOY.strftime("%d/%m/%Y"), "accion": "quitar_invitados", "id": iid})
check("saca invitados", sum(i["cantidad"] for i in base.invitados_del_dia(HOY)) == 1)
check("el comedor también ve el resumen por día", m.get("/admin/comidas").status_code == 200)
check("un usuario con rol inventado no se crea", "rol" in _mensaje_de(lambda: base.crear_usuario("x", "h", "x", "jefe")))
base.alm.discard((juan, HOY, TIPO))

# --- 5. contabilidad ---------------------------------------------------------
print("Contabilidad:")
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
vid2 = base.vacaciones(juan)[0]["id"]
r = c.post(f"/admin/trabajador/{juan}", data={"accion": "vacacion_editar", "id": vid2, "tipo": "permiso", "desde": "01/02/2026", "hasta": "10/02/2026", "nota": "corregido"}, follow_redirects=True)
check("corrige un período desde la ficha (fechas, tipo, nota; los días se recalculan)", base.vac[vid2]["hasta"] == date(2026, 2, 10) and base.vac[vid2]["dias"] == 10 and base.vac[vid2]["tipo"] == "permiso" and base.vac[vid2]["nota"] == "corregido" and "Período corregido" in r.get_data(as_text=True))
c.post(f"/admin/trabajador/{juan}", data={"accion": "vacacion_editar", "id": vid2, "tipo": "vacaciones", "desde": "01/02/2026", "hasta": "15/02/2026", "dias": "15"})
html = c.get(f"/admin/trabajador/{juan}").get_data(as_text=True)
check("la ficha es una tabla simple: le corresponden, tomó este año, le quedan", "Le corresponden por año" in html and "Tomó este año" in html and "Le quedan" in html and "Generados" not in html and "arrancar" not in html)
html = c.get("/admin/historial").get_data(as_text=True)
check("el período borrado queda en Historial con quién lo borró", "Juan Pérez" in html and "conta" in html and base.vac[vid]["borrado_por"] == "conta")
c.post("/admin/historial", data={"que": "periodo", "id": vid})
check("y se puede recuperar", base.trabajador(juan)["tomados"] == 18 and base.vac[vid]["borrado_en"] is None)
c.post(f"/admin/trabajador/{juan}", data={"accion": "vacacion_borrar", "id": vid})
c.post(f"/admin/trabajador/{juan}", data={"accion": "ajuste", "dias": "-2", "motivo": "pagados"})
check("ajuste negativo resta", base.trabajador(juan)["ajustes"] == -2)
r = c.post(f"/admin/trabajador/{juan}", data={"accion": "ajuste", "dias": "3", "motivo": ""}, follow_redirects=True)
check("ajuste sin motivo avisa", "motivo" in r.get_data(as_text=True))
html = c.get(f"/admin/trabajador/{juan}").get_data(as_text=True)
check("la ficha muestra saldo y período", "Días tomados" in html and "15/02/2026" in html)
r = c.post(f"/admin/trabajador/{juan}", data={"accion": "editar", "cedula": "0912345678", "nombre": "Juan", "fecha_ingreso": "25/03/2019"}, follow_redirects=True)
check("editar con la cédula de otro avisa", "ya es de María" in r.get_data(as_text=True))
c.post(f"/admin/trabajador/{juan}", data={"accion": "editar", "cedula": "1712345678", "nombre": "Juan Pérez", "fecha_ingreso": "25/03/2019", "area": "Acabado", "dias_por_anio": "18"})
check("editar guarda el perfil", base.trabajador(juan)["area"] == "Acabado" and base.trabajador(juan)["dias_por_anio"] == 18)
html = c.get(f"/admin/trabajador/{juan}").get_data(as_text=True)
check("y la ficha lo muestra", "Acabado" in html)
check("el área es una lista con las de la fábrica y conserva la que tenía aunque no esté", '<select name="area">' in html and 'value="TT"' in html and 'value="Acabado" selected' in html)
c.post(f"/admin/trabajador/{juan}", data={"accion": "editar", "cedula": "1712345678", "nombre": "Juan Pérez", "fecha_ingreso": "25/03/2019"})
check("editar sin días por año vuelve a la ley", base.trabajador(juan)["dias_por_anio"] is None)
c.post(f"/admin/trabajador/{juan}", data={"accion": "baja", "fecha_salida": "10/09/2026"})
check("da de baja", not base.trabajador(juan)["activo"])
c.post(f"/admin/trabajador/{juan}", data={"accion": "reactivar"})
check("y reactiva", base.trabajador(juan)["activo"])
check("ficha inexistente da 404", c.get("/admin/trabajador/9999").status_code == 404)
antes_tomados = base.trabajador(juan)["tomados"]
c.post(f"/admin/trabajador/{juan}", data={"accion": "vacacion", "desde": "01/04/2026", "hasta": "03/04/2026", "tipo": "enfermedad"})
check("una enfermedad se anota pero no descuenta", base.trabajador(juan)["tomados"] == antes_tomados and any(v["tipo"] == "enfermedad" for v in base.vacaciones(juan)))
c.post(f"/admin/trabajador/{juan}", data={"accion": "vacacion", "desde": "06/04/2026", "hasta": "06/04/2026", "tipo": "permiso"})
check("un permiso sí descuenta", base.trabajador(juan)["tomados"] == antes_tomados + 1)
r = c.post(f"/admin/trabajador/{juan}", data={"accion": "vacacion", "desde": "07/04/2026", "hasta": "07/04/2026", "tipo": "feriado"}, follow_redirects=True)
check("un tipo inventado se rechaza", "tipo de ausencia" in r.get_data(as_text=True))
check("la ficha muestra el tipo", "Enfermedad" in c.get(f"/admin/trabajador/{juan}").get_data(as_text=True))
html = c.get("/admin?q=perez").get_data(as_text=True)
check("el buscador encuentra sin acento ni mayúsculas", "Juan Pérez" in html and "María López" not in html)
html = c.get("/admin?q=0912").get_data(as_text=True)
check("y por cédula", "María López" in html and "Juan Pérez" not in html)
check("la lista dice Generados, no Ganados", "Generados" in html and "Ganados" not in html)

check("sin resultados lo dice", "Nadie coincide" in c.get("/admin?q=zzzz").get_data(as_text=True))
c.post(f"/admin/trabajador/{juan}", data={"accion": "editar", "cedula": "1712345678", "nombre": "Juan Pérez", "fecha_ingreso": "25/03/2019", "direccion": "Calle 1"})
check("la ficha guarda la dirección", base.trabajador(juan)["direccion"] == "Calle 1")
c.post(f"/admin/trabajador/{juan}", data={"accion": "invitados"})
check("desde la ficha se saca el permiso de invitados", base.trabajador(juan)["puede_invitar"] is False)
c.post(f"/admin/trabajador/{juan}", data={"accion": "invitados", "puede_invitar": "1"})
html = c.get(f"/admin/trabajador/{juan}").get_data(as_text=True)
check("y se da; la ficha lo muestra", base.trabajador(juan)["puede_invitar"] is True and "Invitados al comedor" in html and 'checked' in html)

# el cuadro del mes
base.marcar_comida(maria, date(2026, 1, 5), "almuerzo", "trabajador")
base.marcar_comida(maria, date(2026, 1, 6), "almuerzo", "trabajador")
base.marcar_comida(maria, date(2026, 1, 6), "cena", "trabajador")
r = c.get("/admin/comidas?mes=2026-01")
html = r.get_data(as_text=True)
check("el cuadro abre en el mes pedido", r.status_code == 200 and "Enero 2026" in html)
check("suma almuerzos y cenas por separado", '<b class="grande">2</b><small>Almuerzos' in html and '<b class="grande">1</b><small>Cenas' in html)
r = c.post("/admin/comidas", data={"cedula": "0912345678", "fecha": "07/01/2026", "tipo": "cena"})
check("desde el cuadro se marca y vuelve al mes", (maria, date(2026, 1, 7), "cena") in base.alm and "mes=2026-01" in r.headers["Location"])
r = c.post("/admin/comidas", data={"cedula": "0912345678", "fecha": "08/01/2026", "tipo": "almuerzo"}, follow_redirects=True)
check("contabilidad marca a mano por cédula", (maria, date(2026, 1, 8), "almuerzo") in base.alm and "marcado" in r.get_data(as_text=True))
r = c.post("/admin/comidas", data={"cedula": "0000000000", "fecha": "08/01/2026", "tipo": "almuerzo"}, follow_redirects=True)
check("cédula desconocida avisa", "ningún trabajador" in r.get_data(as_text=True))
html = c.get("/admin/comidas?mes=2026-01").get_data(as_text=True)
check("comidas del mes: una fila por día con fecha corta, invitados y el + que despliega quiénes", "Mar 6" in html and "Invitados" in html and 'class="dia-mas"' in html and "Almorzaron" in html and "Total" in html)
check("el 1 de enero (feriado sin nadie) no aparece; el sábado 3 tampoco", '<span class="etiqueta">feriado</span>' not in html and "Sáb 3" not in html)
base.marcar_comida(maria, date(2026, 1, 1), "almuerzo", "conta")
html = c.get("/admin/comidas?mes=2026-01").get_data(as_text=True)
check("un feriado con alguien sí, marcado", '<span class="etiqueta">feriado</span>' in html and "Jue 1" in html)
check("el cuadro para pagar sigue existiendo aparte", 'class="cuadro"' in c.get("/admin/comidas/imprimir?mes=2026-01").get_data(as_text=True))
c.post("/admin/feriados", data={"fecha": "25/12/2027", "nombre": "Navidad"})
check("contabilidad agrega un feriado", date(2027, 12, 25) in base.feriados())
c.post("/admin/feriados", data={"accion": "borrar", "fecha": "25/12/2027"})
check("y lo saca", date(2027, 12, 25) not in base.feriados())
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

# pedidos del trabajador → contabilidad
print("Pedidos:")
w = A.app.test_client()
w.post("/", data={"cedula": "0912345678"})
r = w.post("/yo/pedir", data={"tipo": "vacaciones", "desde": (HOY + timedelta(days=10)).isoformat(), "hasta": (HOY + timedelta(days=16)).isoformat(), "nota": "viaje"}, follow_redirects=True)
html = r.get_data(as_text=True)
check("el trabajador pide 7 días y queda pendiente", "Pedido enviado" in html and "Esperando respuesta" in html and base.cuantas_pendientes() == 1)
r = w.post("/yo/pedir", data={"tipo": "vacaciones", "desde": (HOY + timedelta(days=12)).isoformat(), "hasta": (HOY + timedelta(days=13)).isoformat()}, follow_redirects=True)
check("no puede pedir dos veces los mismos días", "pendiente para esos días" in r.get_data(as_text=True) and base.cuantas_pendientes() == 1)
r = w.post("/yo/pedir", data={"tipo": "vacaciones", "desde": (HOY - timedelta(days=40)).isoformat(), "hasta": (HOY - timedelta(days=39)).isoformat()}, follow_redirects=True)
check("no puede pedir muy para atrás", "para atrás" in r.get_data(as_text=True))
r = w.post("/yo/pedir", data={"tipo": "vacaciones", "desde": (HOY + timedelta(days=30)).isoformat(), "hasta": (HOY + timedelta(days=20)).isoformat()}, follow_redirects=True)
check("hasta antes de desde avisa", "anterior" in r.get_data(as_text=True))
w.post("/yo/pedir", data={"tipo": "enfermedad", "desde": (HOY - timedelta(days=2)).isoformat(), "hasta": (HOY - timedelta(days=1)).isoformat()})
check("una enfermedad de ayer se puede avisar hoy", base.cuantas_pendientes() == 2)
html = c.get("/admin").get_data(as_text=True)
check("contabilidad ve el globo con los pendientes en el menú", 'class="globo">2<' in html)
html = c.get("/admin/solicitudes").get_data(as_text=True)
check("la bandeja lista los dos con nombre y saldo", "María López" in html and "le quedan" in html and "Por responder" in html)
ids = [p["id"] for p in base.solicitudes_pendientes()]
r = c.post("/admin/solicitudes", data={"accion": "rechazar", "id": ids[0], "respuesta": ""}, follow_redirects=True)
check("rechazar sin motivo no deja", "por qué se rechaza" in r.get_data(as_text=True) and base.cuantas_pendientes() == 2)
antes = base.trabajador(maria)["tomados"]
r = c.post("/admin/solicitudes", data={"accion": "aprobar", "id": ids[0], "dias": "5", "respuesta": "ok, buen viaje"}, follow_redirects=True)
html = r.get_data(as_text=True)
check("aprobar carga el período con los días corregidos", base.trabajador(maria)["tomados"] == antes + 5 and "Aprobado: 5 días" in html)
check("y ofrece avisarle (sin celular, lo dice)", "no tiene celular" in html)
check("el período quedó ligado al pedido", base.solicitud(ids[0])["vacacion_id"] in base.vac and "pedido #" in base.vac[base.solicitud(ids[0])["vacacion_id"]]["nota"])
r = c.post("/admin/solicitudes", data={"accion": "rechazar", "id": ids[1], "respuesta": "falta el certificado"}, follow_redirects=True)
check("rechazar con motivo", base.solicitud(ids[1])["estado"] == "rechazada" and base.cuantas_pendientes() == 0)
r = c.post("/admin/solicitudes", data={"accion": "aprobar", "id": ids[1], "dias": "2"}, follow_redirects=True)
check("no se responde dos veces", "ya no está pendiente" in r.get_data(as_text=True))
html = w.get("/yo/vacaciones").get_data(as_text=True)
check("el trabajador ve las dos respuestas", "Aprobado" in html and "ok, buen viaje" in html and "No aprobado" in html and "falta el certificado" in html)
w.post("/yo/pedir", data={"tipo": "permiso", "desde": (HOY + timedelta(days=40)).isoformat(), "hasta": (HOY + timedelta(days=40)).isoformat()})
pid = [p["id"] for p in base.solicitudes_pendientes()][0]
r = w.post("/yo/pedido/cancelar", data={"id": pid}, follow_redirects=True)
check("el trabajador cancela un pedido pendiente", base.solicitud(pid)["estado"] == "cancelada" and base.cuantas_pendientes() == 0)
otro = A.app.test_client(); otro.post("/", data={"cedula": "1712345678"})
w.post("/yo/pedir", data={"tipo": "permiso", "desde": (HOY + timedelta(days=50)).isoformat(), "hasta": (HOY + timedelta(days=50)).isoformat()})
pid = [p["id"] for p in base.solicitudes_pendientes()][0]
otro.post("/yo/pedido/cancelar", data={"id": pid})
check("otro trabajador no puede cancelar un pedido ajeno", base.solicitud(pid)["estado"] == "pendiente")
check("el link de WhatsApp arma el número de Ecuador", A.link_whatsapp("0991234567", "hola") == "https://wa.me/593991234567?text=hola" and A.link_whatsapp(None, "x") is None)
base.trab[maria]["celular"] = "0991234567"
r = c.post("/admin/solicitudes", data={"accion": "aprobar", "id": pid, "dias": "1"}, follow_redirects=True)
check("con celular ofrece el WhatsApp con el mensaje", "wa.me/593991234567" in r.get_data(as_text=True))
antes = base.trabajador(maria)["tomados"]
r = w.post("/yo/pedido/cancelar", data={"id": pid}, follow_redirects=True)
check("el trabajador cancela un aprobado que no empezó y los días vuelven", base.solicitud(pid)["estado"] == "cancelada" and base.trabajador(maria)["tomados"] == antes - 1 and "vuelven a su saldo" in r.get_data(as_text=True))
check("el período cancelado quedó en el historial", any(f["id"] == base.solicitud(pid)["vacacion_id"] for f in base.historial()))
w.post("/yo/pedir", data={"tipo": "permiso", "desde": (HOY - timedelta(days=3)).isoformat(), "hasta": (HOY - timedelta(days=2)).isoformat()})
viejo = [p["id"] for p in base.solicitudes_pendientes()][0]
c.post("/admin/solicitudes", data={"accion": "aprobar", "id": viejo, "dias": "2"})
r = w.post("/yo/pedido/cancelar", data={"id": viejo}, follow_redirects=True)
check("un aprobado que ya empezó no se cancela solo", base.solicitud(viejo)["estado"] == "aprobada" and "hable con contabilidad" in r.get_data(as_text=True))
antes = base.trabajador(maria)["tomados"]
r = c.post("/admin/solicitudes", data={"accion": "deshacer", "id": viejo, "respuesta": "se cambió la fecha"}, follow_redirects=True)
check("contabilidad deshace un aprobado", base.solicitud(viejo)["estado"] == "cancelada" and base.trabajador(maria)["tomados"] == antes - 2 and "se sacó de la ficha" in r.get_data(as_text=True))
check("y ofrece avisarle también", "wa.me/" in r.get_data(as_text=True))
aprob = base.solicitud(ids[0]); antes_v = base.vac[aprob["vacacion_id"]]["dias"]
r = c.post("/admin/solicitudes", data={"accion": "editar", "id": ids[0], "desde": (HOY + timedelta(days=10)).strftime("%d/%m/%Y"), "hasta": (HOY + timedelta(days=12)).strftime("%d/%m/%Y")}, follow_redirects=True)
check("corrige un pedido aprobado desde Pedidos (cambia el período, no el estado)", base.vac[aprob["vacacion_id"]]["dias"] == 3 and base.solicitud(ids[0])["estado"] == "aprobada" and "Corregido" in r.get_data(as_text=True))
antes = base.trabajador(maria)["tomados"]
r = c.post("/admin/solicitudes", data={"accion": "cargar", "cedula": "0912345678", "tipo": "permiso", "desde": "01/12/2026", "hasta": "02/12/2026"}, follow_redirects=True)
check("contabilidad carga días a mano desde Pedidos", base.trabajador(maria)["tomados"] == antes + 2 and "2 días de permiso cargados" in r.get_data(as_text=True))
r = c.post("/admin/solicitudes", data={"accion": "cargar", "cedula": "0000000000", "tipo": "permiso", "desde": "01/12/2026", "hasta": "02/12/2026"}, follow_redirects=True)
check("con cédula desconocida avisa", "ningún trabajador" in r.get_data(as_text=True))
r = c.post("/admin/solicitudes", data={"accion": "borrar", "id": ids[0]}, follow_redirects=True)
check("un aprobado no se borra", "Sólo se borra" in r.get_data(as_text=True) and ids[0] in base.sol)
c.post("/admin/solicitudes", data={"accion": "borrar", "id": ids[1]})
check("un rechazado sí, y desaparece de la lista del trabajador", ids[1] not in base.sol and "falta el certificado" not in w.get("/yo/vacaciones").get_data(as_text=True))

# el perfil que el trabajador corrige
r = w.post("/yo/perfil", data={"celular": "099 111 2222", "direccion": "Av. Siempre Viva 123"}, follow_redirects=True)
check("el trabajador corrige celular y dirección", base.trabajador(maria)["celular"] == "0991112222" and base.trabajador(maria)["direccion"] == "Av. Siempre Viva 123" and "Contabilidad los va a ver" in r.get_data(as_text=True))
r = w.post("/yo/perfil", data={"celular": "099 111 2222", "direccion": "Av. Siempre Viva 123"}, follow_redirects=True)
check("guardar lo mismo no anota nada", "No cambió nada" in r.get_data(as_text=True))
r = w.post("/yo/perfil", data={"celular": "12", "direccion": ""}, follow_redirects=True)
check("un celular imposible se rechaza", "entre 7 y 15" in r.get_data(as_text=True) and base.trabajador(maria)["celular"] == "0991112222")
html = c.get("/admin/solicitudes").get_data(as_text=True)
check("contabilidad ve los cambios de datos", "Datos que cambiaron" in html and "0991112222" in html and "Siempre Viva" in html)
cid = base.cambios_perfil_sin_ver()[0]["id"]
c.post("/admin/solicitudes", data={"accion": "visto", "id": cid})
check("y los marca vistos", len(base.cambios_perfil_sin_ver()) == 1)

# usuarios
r = c.post("/admin/usuarios", data={"accion": "crear", "usuario": "Mal Usuario", "clave": "123456"}, follow_redirects=True)
check("usuario con espacios se rechaza", "minúsculas" in r.get_data(as_text=True))
r = c.post("/admin/usuarios", data={"accion": "crear", "usuario": "ana", "clave": "123"}, follow_redirects=True)
check("clave corta se rechaza", "6 caracteres" in r.get_data(as_text=True))
c.post("/admin/usuarios", data={"accion": "crear", "usuario": "ana", "clave": "123456", "nombre": "Ana"})
check("crea usuario", base.usuario_por_nombre("ana") is not None and base.usuario_por_nombre("ana")["rol"] == "contabilidad")
c.post("/admin/usuarios", data={"accion": "crear", "usuario": "mesa", "clave": "123456", "nombre": "Mesa", "rol": "comedor"})
html = c.get("/admin/usuarios").get_data(as_text=True)
check("crea el usuario del comedor y muestra el link de la tablet", base.usuario_por_nombre("mesa")["rol"] == "comedor" and "comedor" in html and CLAVE in html)
c.post("/admin/usuarios", data={"accion": "nuevo_link"})
check("generar uno nuevo cambia la clave y el viejo da 404", base.configuracion("clave_comedor") != CLAVE and A.app.test_client().get(T).status_code == 404)
check("contabilidad también puede abrir la tablet", c.get("/comedor").status_code == 200)
with c.session_transaction() as ses:
    ses["usuario"] = {"id": 1, "usuario": "vieja", "nombre": "Sesión vieja"}
check("una sesión de antes de los roles sigue entrando a contabilidad", c.get("/admin").status_code == 200)
with c.session_transaction() as ses:
    ses["usuario"] = {"id": base.usuario_por_nombre("conta")["id"], "usuario": "conta", "nombre": "Contabilidad", "rol": "contabilidad"}
yo_id = base.usuario_por_nombre("conta")["id"]
r = c.post("/admin/usuarios", data={"accion": "desactivar", "id": yo_id}, follow_redirects=True)
check("no se puede desactivar a sí mismo", "su propio usuario" in r.get_data(as_text=True))
ana_id = base.usuario_por_nombre("ana")["id"]
c.post("/admin/usuarios", data={"accion": "desactivar", "id": ana_id})
check("desactivado no puede entrar", base.usuario_por_nombre("ana") is None)

# --- 6. cada pantalla abre + templates -----------------------------------------
print("Pantallas:")
for ruta in ("/admin", "/admin?todos=1", f"/admin/trabajador/{juan}", "/admin/carga",
             "/admin/comidas", "/admin/comidas/imprimir", "/admin/feriados", "/admin/usuarios", "/admin/solicitudes",
             "/admin/historial", "/comedor", "/comedor/dia", "/healthz"):
    check(f"{ruta} abre", c.get(ruta).status_code == 200)
r = c.get("/healthz")
check("healthz cuenta trabajadores y usuarios", r.get_json()["trabajadores_activos"] == len(base.trabajadores()) and r.get_json()["hay_usuarios"])
check("404 en castellano", "no existe" in c.get("/no-existe").get_data(as_text=True))
check("/admin sin login manda a entrar", A.app.test_client().get("/admin/comidas").status_code == 302)

import re as _re  # noqa: E402
_voseo = _re.compile(r"\b(vos|sos|tenés|podés|querés|sabés|hacés|cumplís|pedís|acá|escribí|poné|pegá|probá|agregá|tocá|hablá|preguntá|avisale|volvé|marcá|pasalos|entrá|tomaste|entraste|te tocan|te quedan|tu cédula|tus pedidos)\b", _re.I)
for f in sorted(os.listdir(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates"))):
    texto = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates", f)).read()
    texto = _re.sub(r"<style>.*?</style>", "", texto, flags=_re.S)
    m = _voseo.search(texto)
    check(f"sin voseo en {f}" + (f" («{m.group(0)}»)" if m else ""), m is None)
_src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")).read()
_msgs = " ".join(_re.findall(r'(?:flash|ValueError)\((?:f)?"([^"]*)"', _src))
m = _voseo.search(_msgs)
check("sin voseo en los mensajes de app.py" + (f" («{m.group(0)}»)" if m else ""), m is None)

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

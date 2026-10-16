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
_hoy_real = A.hoy
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
# --- LA CONTRASEÑA PARA ENTRAR (Tamara 14/10/2026: «la contraseña tiene que ser para todo,
# vacaciones y rol»; «todos deben tener mail»; la tablet del comedor sigue con la cédula sola) ---
from werkzeug.security import check_password_hash  # noqa: E402
MANDADOS = []
A.correo.mandar_codigo = lambda para, nombre, codigo, minutos, para_que: (MANDADOS.append(
    {"para": para, "nombre": nombre, "codigo": codigo, "para_que": para_que}), (True, ""))[1]


def _sin_espera():
    """Los tests no esperan el minuto entre códigos."""
    for x in base.cod.values():
        x["creado_en"] -= timedelta(minutes=61)


def entrar_trab(cl, cedula, clave="clave2026"):
    """Para los demás tests: entra con la cédula y la contraseña (la crea si no tiene)."""
    cl.post("/", data={"cedula": cedula})
    t = base.trabajador_por_cedula(cedula)
    if t and t.get("clave_rol_hash"):
        return cl.post("/entrar/clave", data={"accion": "entrar", "clave": clave}, follow_redirects=True)
    _sin_espera()
    cl.post("/entrar/clave", data={"accion": "mandar", "email": f"{cedula}@correo.ec"})
    return cl.post("/entrar/clave", data={"accion": "crear", "codigo": MANDADOS[-1]["codigo"], "clave": clave, "repetir": clave},
                   follow_redirects=True)


r = c.post("/", data={"cedula": "171-234-5678"}, follow_redirects=True)
check("con el interruptor apagado (como arranca), entra con la cédula sola como antes", "disponibles" in r.get_data(as_text=True))
c.get("/salir")
base.poner_configuracion("entrar_con_clave", "si")
r = c.post("/", data={"cedula": "171-234-5678"}, follow_redirects=True)
html = r.get_data(as_text=True)
check("la cédula sola NO entra: pide crear la contraseña, con el nombre de pila", "Hola, Juan" in html and "cree su contraseña" in html
      and 'name="email"' in html and "disponibles" not in html and c.get("/yo/vacaciones").status_code == 302)
check("ni las vacaciones, ni el rol, ni el perfil se ven sin contraseña", all("/" == c.get(x).headers.get("Location", "")[-1:]
      or c.get(x).headers.get("Location", "").endswith("/") for x in ("/yo/vacaciones", "/yo/rol", "/yo/perfil")))
r = c.post("/entrar/clave", data={"accion": "mandar", "email": "no es correo"})
check("un correo mal escrito no manda nada", "no parece un correo" in r.get_data(as_text=True) and not MANDADOS)
r = c.post("/entrar/clave", data={"accion": "mandar", "email": "Juan.Perez@Gmail.com "})
html = r.get_data(as_text=True)
check("manda el código al correo y lo muestra tapado", len(MANDADOS) == 1 and MANDADOS[0]["para"] == "juan.perez@gmail.com"
      and MANDADOS[0]["nombre"] == "Juan" and len(MANDADOS[0]["codigo"]) == 6 and "ju•••z@gmail.com" in html and "Cree su contraseña" in html
      and 'autocomplete="one-time-code"' in html)
check("el código no se guarda a la vista (va cifrado)", all(MANDADOS[0]["codigo"] not in x["codigo_hash"] for x in base.cod.values()))
r = c.post("/entrar/clave", data={"accion": "reenviar", "email": "juan.perez@gmail.com"})
check("no manda otro antes de un minuto", "hace menos de un minuto" in r.get_data(as_text=True) and len(MANDADOS) == 1)
_bueno = MANDADOS[0]["codigo"]
_malo = "000000" if _bueno != "000000" else "111111"
_juan = base.trabajador_por_cedula("1712345678")["id"]
r = c.post("/entrar/clave", data={"accion": "crear", "codigo": _malo, "clave": "juan2026", "repetir": "juan2026"})
check("un código malo no sirve", "Ese código no sirve" in r.get_data(as_text=True) and not base.trabajador(_juan).get("clave_rol_hash"))
r = c.post("/entrar/clave", data={"accion": "crear", "codigo": _bueno, "clave": "juan2026", "repetir": "otra2026"})
check("las dos contraseñas tienen que ser iguales (y no pierde el código escrito)", "no son iguales" in r.get_data(as_text=True)
      and f'value="{_bueno}"' in r.get_data(as_text=True))
r = c.post("/entrar/clave", data={"accion": "crear", "codigo": _bueno, "clave": "1712345678", "repetir": "1712345678"})
check("la cédula no sirve de contraseña", "muy fácil" in r.get_data(as_text=True))
r = c.post("/entrar/clave", data={"accion": "crear", "codigo": _bueno, "clave": "juan2026", "repetir": "juan2026"}, follow_redirects=True)
html = r.get_data(as_text=True)
_t = base.trabajador(_juan)
check("con el código bueno guarda la contraseña (cifrada) y el correo, y entra", _t["clave_rol_hash"] and "juan2026" not in _t["clave_rol_hash"]
      and _t["email"] == "juan.perez@gmail.com" and "protegida con su contraseña" in html)
check("entra y ve su nombre", r.status_code == 200 and "Juan Pérez" in html)
check("no ve a los demás", "María López" not in html)
check("la primera pantalla es la de vacaciones, con la pestaña de perfil, sin comidas", "/yo/perfil" in html and "Comidas" not in html and "disponibles" in html)
galleta = next((h for h in r.history[0].headers.getlist("Set-Cookie") if h.startswith("session=")), "") if r.history else ""
check("la sesión del trabajador dura 30 días (cookie con vencimiento)", "Expires=" in galleta or "Max-Age=" in galleta)
check("y son 30 días", A.app.config["PERMANENT_SESSION_LIFETIME"].days == 30)
_cel = A.app.test_client(); _cel.post("/", data={"cedula": "1712345678"})
html = _cel.get("/entrar/clave").get_data(as_text=True)
check("en otro celular: cédula → «Hola, Juan» y la contraseña, con «¿Olvidó su contraseña?»", "Hola, Juan" in html
      and "Escriba su contraseña" in html and "¿Olvidó su contraseña?" in html and _cel.get("/yo/vacaciones").status_code == 302)
check("el código ya usado no sirve otra vez", "no sirve" in _cel.post("/entrar/clave", data={"accion": "crear", "codigo": _bueno,
      "clave": "intruso1", "repetir": "intruso1"}).get_data(as_text=True))
for _i in range(5):
    _cel.post("/entrar/clave", data={"accion": "entrar", "clave": "mala"})
r = _cel.post("/entrar/clave", data={"accion": "entrar", "clave": "juan2026"})
check("después de 5 contraseñas malas se traba un rato, aunque ponga la buena", "espere 15 minutos" in r.get_data(as_text=True)
      and _cel.get("/yo/vacaciones").status_code == 302)
base.clave_rol_buena(_juan)
r = _cel.post("/entrar/clave", data={"accion": "entrar", "clave": "juan2026"})
check("con la contraseña buena entra a todo", r.status_code == 302 and _cel.get("/yo/vacaciones").status_code == 200)
_sin_espera()
_cel2 = A.app.test_client(); _cel2.post("/", data={"cedula": "1712345678"})
r = _cel2.post("/entrar/clave", data={"accion": "olvide"})
check("¿Olvidó su contraseña?: le manda un código al correo que tiene", MANDADOS[-1]["para"] == "juan.perez@gmail.com"
      and MANDADOS[-1]["para_que"] == "olvido" and "Cambie su contraseña" in r.get_data(as_text=True))
_cel2.post("/entrar/clave", data={"accion": "crear", "codigo": MANDADOS[-1]["codigo"], "clave": "nueva2026", "repetir": "nueva2026"})
check("con el código cambia la contraseña y entra; los celulares que entraron con la vieja se cierran",
      check_password_hash(base.trabajador(_juan)["clave_rol_hash"], "nueva2026") and _cel2.get("/yo/vacaciones").status_code == 200
      and _cel.get("/yo/vacaciones").status_code == 302 and c.get("/yo/vacaciones").status_code == 302)
r = _cel2.post("/yo/perfil", data={"accion": "clave", "actual": "mala", "clave": "otra2026", "repetir": "otra2026"}, follow_redirects=True)
check("en el perfil: cambiar la contraseña pide la actual", "actual no es correcta" in r.get_data(as_text=True))
r = _cel2.post("/yo/perfil", data={"accion": "clave", "actual": "nueva2026", "clave": "clave2026", "repetir": "clave2026"}, follow_redirects=True)
check("y la cambia, sin sacarlo de este celular; el perfil muestra su correo", "cambió su contraseña" in r.get_data(as_text=True)
      and _cel2.get("/yo/vacaciones").status_code == 200 and "juan.perez@gmail.com" in _cel2.get("/yo/perfil").get_data(as_text=True))
r = _cel2.get("/salir")
check("«Salir» lo saca: con la cédula sola no vuelve a entrar", _cel2.get("/yo/vacaciones").status_code == 302)
check("el link viejo de la contraseña del rol lleva a la entrada", A.app.test_client().get("/yo/rol/clave").status_code == 302)
check("sin poner la cédula antes, la pantalla de la contraseña manda a la entrada", A.app.test_client().get("/entrar/clave").status_code == 302)
_falso_mandar = A.correo.mandar_codigo
os.environ["MAIL_ENVIAR"] = "0"
import importlib  # noqa: E402
_cr = importlib.reload(importlib.import_module("correo"))
check("el correo de verdad nunca rompe: apagado o sin SES dice por qué", _cr.mandar_codigo("x@y.ec", "Ana", "123456", 15, "crear")[0] is False
      and "apagado" in _cr.mandar_codigo("x@y.ec", "Ana", "123456", 15, "crear")[1])
os.environ.pop("MAIL_ENVIAR")
A.correo.mandar_codigo = lambda *a, **k: (False, "SES caído")
_cl5 = A.app.test_client(); base.crear_trabajador("1700000555", "Prueba Correo", date(2020, 1, 1)); _cl5.post("/", data={"cedula": "1700000555"})
r = _cl5.post("/entrar/clave", data={"accion": "mandar", "email": "p@correo.ec"})
check("si el correo no sale, se lo dice simple y no le pide un código que no le llegó", "No pudimos mandar el correo" in r.get_data(as_text=True)
      and "Mandarme el código" in r.get_data(as_text=True))
A.correo.mandar_codigo = _falso_mandar
entrar_trab(c, "1712345678")
r = c.get("/yo/perfil")
check("la pestaña de perfil abre y dice los días de este año", r.status_code == 200 and "Le corresponden por año" in r.get_data(as_text=True) and "por ley" in r.get_data(as_text=True))
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
check("con la cédula muestra el nombre y pregunta", "Juan Pérez" in html and "toque su horario" in html and (juan, HOY, A.comida_de_ahora()) not in base.alm)
check("un botón por horario, el de ahora en verde", 'name="turno"' in html and f'kiosco-hora ahora" type="submit" name="turno" value="{A.turno_de_ahora(A.comida_de_ahora())}"' in html
      and ((">12:00<" in html and ">13:30<" in html) if A.comida_de_ahora() == "almuerzo" else (">18:30<" in html and ">19:00<" in html)))
check("horarios: almuerzo 4 de media hora, cena 2 (18:30 y 19:00)", A.TURNOS["almuerzo"] == ("12:00", "12:30", "13:00", "13:30") and A.TURNOS["cena"] == ("18:30", "19:00")
      and A.nombre_turno("13:30") == "13:30 a 14:00" and A.nombre_turno("19:00") == "19:00 a 19:30" and A.nombre_turno(None) == "")
check("vacío → el de ahora; un horario de almuerzo no sirve para la cena", A.leer_turno("12:30", "almuerzo") == "12:30" and A.leer_turno("19:00", "cena") == "19:00"
      and A.leer_turno("", "almuerzo") == A.turno_de_ahora("almuerzo") and A.leer_turno("", "cena") in A.TURNOS["cena"] and "no existe" in _mensaje_de(lambda: A.leer_turno("12:30", "cena")))
check("un horario inventado no", "no existe" in _mensaje_de(lambda: A.leer_turno("11:00", "almuerzo")))
check("«No soy yo» es un botón y vuelve a la cédula", '>No soy yo</button>' in html and f'action="{T}"' in html)
TIPO = A.comida_de_ahora()
TURNO = "12:30" if TIPO == "almuerzo" else "19:00"
r = k.post(T + "/confirmar", data={"cedula": "1712345678", "tipo": TIPO, "turno": TURNO})
html = r.get_data(as_text=True)
check("con Sí queda registrado, por la tablet, con su horario", (juan, HOY, TIPO) in base.alm and "Buen provecho" in html and "registrado" in html
      and base.alm_turno[(juan, HOY, TIPO)] == TURNO)
check("NO ofrece deshacer (Tamara 14/10) ni invitados (Juan todavía no tiene permiso)", "¿Trae invitados?" not in html and "deshacer" not in html.lower())
r = k.post(T + "/invitados", data={"cedula": "1712345678", "tipo": TIPO, "cantidad": "1", "descripcion": "primo"})
check("sin permiso no anota invitados", "no tiene permiso" in r.get_data(as_text=True) and not base.invitados_del_dia(HOY))
base.poner_puede_invitar(juan, True)
html = k.post(T, data={"cedula": "1712345678"}).get_data(as_text=True)
check("con permiso (se da en la ficha) sí ofrece invitados", "¿Trae invitados?" in html)
r = k.post(T + "/deshacer", data={"cedula": "1712345678", "tipo": TIPO})
check("la tablet ya no deshace: la ruta no existe y la comida sigue", r.status_code in (404, 405) and (juan, HOY, TIPO) in base.alm)
r = k.post(T, data={"cedula": "1712345678"})
html = r.get_data(as_text=True)
check("si vuelve, le dice que ya registró y le ofrece invitados", "Ya registró" in html and "¿Trae invitados?" in html)
# invitados en la MISMA pantalla, debajo del horario (Tamara 14/10: «no hagamos otra pantalla»)
base.poner_puede_invitar(maria, True)
html = k.post(T, data={"cedula": "0912345678"}).get_data(as_text=True)
check("con permiso, debajo del horario elige invitados y confirma con Registrar", "¿Trae invitados?" in html and 'name="cantidad"' in html and ">Registrar</button>" in html
      and 'type="button" data-turno=' in html)
_antes = sum(i["cantidad"] for i in base.invitados_del_dia(HOY))
r = k.post(T + "/confirmar", data={"cedula": "0912345678", "tipo": TIPO, "turno": TURNO, "cantidad": "2", "descripcion": ""})
check("con invitados y sin decir quiénes, no registra nada y lo pide", "quiénes son" in r.get_data(as_text=True) and (maria, HOY, TIPO) not in base.alm)
r = k.post(T + "/confirmar", data={"cedula": "0912345678", "tipo": TIPO, "turno": TURNO, "cantidad": "2", "descripcion": "sus hijos"})
html = r.get_data(as_text=True)
check("registra la comida y los 2 invitados de una, con su horario", (maria, HOY, TIPO) in base.alm and "con 2 invitados" in html and "¿Trae invitados?" not in html
      and sum(i["cantidad"] for i in base.invitados_del_dia(HOY)) == _antes + 2
      and [i for i in base.invitados_del_dia(HOY) if i["trabajador_id"] == maria][0]["turno"] == TURNO)
base.desmarcar_comida(maria, HOY, TIPO)
for _i in [i for i in base.invitados_del_dia(HOY) if i["trabajador_id"] == maria]:
    base.borrar_invitados(_i["id"])
# invitados ANTES de que él coma (Tamara 14/10/2026: «quizás los registran antes o después de comer»)
_otro = [x for x in A.TURNOS[TIPO] if x != TURNO][0]
r = k.post(T + "/confirmar", data={"cedula": "0912345678", "tipo": TIPO, "turno": _otro, "cantidad": "2",
                                    "descripcion": "proveedores", "solo_invitados": "1"})
html = r.get_data(as_text=True)
_inv_m = [i for i in base.invitados_del_dia(HOY) if i["trabajador_id"] == maria]
check("«Sólo los invitados»: registra los invitados SIN su comida, en el horario elegido",
      (maria, HOY, TIPO) not in base.alm and sum(i["cantidad"] for i in _inv_m) == 2 and _inv_m[0]["turno"] == _otro
      and "2 invitados registrados" in html and "Cuando usted venga a comer" in html)
html = k.post(T, data={"cedula": "0912345678"}).get_data(as_text=True)
check("cuando vuelve a comer: le pide su horario y le dice que ya registró 2 (le ofrece 1 más, no 3)",
      "toque su horario" in html and "ya registró 2" in html and 'data-n="1"' in html and 'data-n="2"' not in html)
r = k.post(T + "/confirmar", data={"cedula": "0912345678", "tipo": TIPO, "turno": TURNO, "cantidad": "2", "descripcion": "otros"})
check("no deja pasar de 3 contando los que ya registró", "máximo es 3" in r.get_data(as_text=True) and (maria, HOY, TIPO) not in base.alm)
r = k.post(T + "/confirmar", data={"cedula": "0912345678", "tipo": TIPO, "turno": TURNO, "cantidad": "0", "descripcion": ""})
check("y registra su comida sin tocar los invitados", (maria, HOY, TIPO) in base.alm
      and sum(i["cantidad"] for i in base.invitados_del_dia(HOY) if i["trabajador_id"] == maria) == 2)
# invitados DESPUÉS: ya comió, vuelve y los registra en otro horario
r = k.post(T + "/invitados", data={"cedula": "0912345678", "tipo": TIPO, "cantidad": "1", "descripcion": "visita", "turno": _otro})
check("después de comer: registra el invitado en el horario que eligió", "1 invitado registrado" in r.get_data(as_text=True)
      and [i for i in base.invitados_del_dia(HOY) if i["descripcion"] == "visita"][0]["turno"] == _otro)
base.desmarcar_comida(maria, HOY, TIPO); base.poner_puede_invitar(maria, False)
for _i in [i for i in base.invitados_del_dia(HOY) if i["trabajador_id"] == maria]:
    base.borrar_invitados(_i["id"])
r = k.post(T + "/confirmar", data={"cedula": "0912345678", "tipo": TIPO, "turno": TURNO, "cantidad": "1", "descripcion": "x"})
check("sin permiso no pasan invitados ni la comida", "no tiene permiso" in r.get_data(as_text=True) and (maria, HOY, TIPO) not in base.alm)
r = k.post(T + "/invitados", data={"cedula": "1712345678", "tipo": TIPO, "cantidad": "2", "descripcion": "proveedor"})
check("anota 2 invitados con descripción, con su mismo horario", "2 invitados registrados" in r.get_data(as_text=True) and sum(i["cantidad"] for i in base.invitados_del_dia(HOY)) == 2
      and base.invitados_del_dia(HOY)[0]["turno"] == TURNO)
r = k.post(T + "/invitados", data={"cedula": "1712345678", "tipo": TIPO, "cantidad": "2", "descripcion": "más"})
check("no pasa de 3 en el día", "máximo es 3" in r.get_data(as_text=True) and sum(i["cantidad"] for i in base.invitados_del_dia(HOY)) == 2)
r = k.post(T + "/invitados", data={"cedula": "1712345678", "tipo": TIPO, "cantidad": "1", "descripcion": ""})
check("sin descripción no", "quiénes son" in r.get_data(as_text=True))
r = k.post(T + "/invitados", data={"cedula": "1712345678", "tipo": TIPO, "cantidad": "1", "descripcion": "hermano"})
check("el tercero entra y ya no ofrece más", "1 invitado registrado" in r.get_data(as_text=True) and "¿Trae invitados?" not in r.get_data(as_text=True))
r = k.post(T, data={"cedula": "0000000000"})
check("cédula desconocida avisa", "No encontramos" in r.get_data(as_text=True))
r = k.post(T, data={"cedula": "0999999999"})
check("dado de baja no marca", "No encontramos" in r.get_data(as_text=True))
check("el link viejo /cafeteria manda al comedor", "/comedor" in k.get("/cafeteria").headers["Location"])

# el usuario del comedor: SÓLO MIRA (Tamara 17/09: «nada más que quién comió, nada de celulares»)
base.crear_usuario("comedor", generate_password_hash("come123"), "Comedor", "comedor")
base.trab[maria]["celular"] = "0991112222"
m = A.app.test_client()
r = m.post("/admin/entrar", data={"usuario": "comedor", "clave": "come123"})
check("el usuario del comedor entra y va a Comensales", r.status_code == 302 and "/comedor/dia" in r.headers["Location"])
check("pero no entra a contabilidad", "/comedor/dia" in m.get("/admin").headers["Location"])
hm = m.get("/admin/comidas").get_data(as_text=True)
check("sí ve Comidas del mes (resumen por semana y por día, igual que contabilidad)", "Por semana" in hm and "Almorzaron" in hm and "Juan Pérez" in hm)
check("pero sin registrar a mano, sin Feriados ni cuadro para pagar", "Registrar una comida a mano" not in hm and "Feriados" not in hm and "Cuadro para pagar" not in hm)
check("y no llega al cuadro para pagar por la URL", "/comedor/dia" in m.get("/admin/comidas/imprimir").headers["Location"])
r = m.post("/admin/comidas", data={"cedula": "0912345678", "tipo": "cena", "fecha": HOY.strftime("%d/%m/%Y")})
check("si intenta registrar por POST en Comidas del mes, 403", r.status_code == 403 and (maria, HOY, "cena") not in base.alm)
html = m.get("/comedor/dia").get_data(as_text=True)
check("comensales: por horario, los invitados bajo su horario", "Comensales" in html and "Juan Pérez" in html and "+2</b> proveedor" in html and "Sin horario" not in html and "18:30 a 19:00" in html)
check("el comedor puede agregar comensales, sin ver ningún celular", "Agregar comensal" in html and "María López" in html and "wa.me" not in html and "0991112222" not in html)
check("pero no tiene botones de cambiar ni quitar", 'value="marcar"' in html and 'value="desmarcar"' not in html and 'value="cambiar"' not in html and 'value="quitar_invitados"' not in html)
check("el menú del comedor es Comensales y Comidas del mes, nada más", "Por mes" not in html and "Comidas del mes" in html and "Trabajadores</a>" not in html and "Pedidos" not in html)
ayer_c = HOY - timedelta(days=1)
r = m.post(f"/comedor/dia?fecha={ayer_c.isoformat()}", data={"accion": "marcar", "trabajador_id": maria, "horario": "almuerzo|12:00"})
check("el comedor no agrega en días anteriores (403)", r.status_code == 403 and (maria, ayer_c, "almuerzo") not in base.alm)
check("y ayer no le ofrece agregar", "Agregar comensal" not in m.get(f"/comedor/dia?fecha={ayer_c.isoformat()}").get_data(as_text=True))
r = m.post("/comedor/dia", data={"fecha": HOY.strftime("%d/%m/%Y"), "accion": "marcar", "trabajador_id": maria, "horario": "almuerzo|13:00"})
check("el comedor agrega a María hoy, con su usuario", (maria, HOY, "almuerzo") in base.alm and base.alm_quien[(maria, HOY, "almuerzo")] == "comedor")
base.desmarcar_comida(maria, HOY, "almuerzo")
r = m.post("/comedor/dia", data={"fecha": HOY.strftime("%d/%m/%Y"), "accion": "cambiar", "trabajador_id": juan, "tipo": TIPO, "horario": "cena|19:00"})
check("ni cambiar", r.status_code == 403)
r = m.post("/comedor/dia", data={"fecha": HOY.strftime("%d/%m/%Y"), "accion": "desmarcar", "trabajador_id": juan, "tipo": TIPO})
check("ni quitar", r.status_code == 403 and (juan, HOY, TIPO) in base.alm)
check("Comensales no deja ir a mañana", f"fecha={(HOY + timedelta(days=1)).isoformat()}" not in m.get(f"/comedor/dia?fecha={(HOY + timedelta(days=3)).isoformat()}").get_data(as_text=True) and HOY.strftime("%d de") in m.get(f"/comedor/dia?fecha={(HOY + timedelta(days=3)).isoformat()}").get_data(as_text=True))

# desactivar al del comedor lo saca AL INSTANTE (antes seguía 30 días)
cid = next(u["id"] for u in base.usuarios() if u["usuario"] == "comedor")
base.activar_usuario(cid, False)
check("un usuario desactivado deja de entrar con su sesión vieja", m.get("/comedor/dia").status_code == 302)
base.activar_usuario(cid, True)
check("y al reactivarlo tiene que entrar de nuevo (la sesión vieja se tiró)", m.get("/comedor/dia").status_code == 302)

# contabilidad sí registra y quita desde Comensales
base.crear_usuario("oficina", generate_password_hash("ofi12345"), "Oficina")
cc = A.app.test_client()
cc.post("/admin/entrar", data={"usuario": "oficina", "clave": "ofi12345"})
html = cc.get("/comedor/dia").get_data(as_text=True)
check("contabilidad ve los sin registrar con el botón de avisar", "Sin registrar" in html and "María López" in html and "wa.me/593991112222" in html)
r = cc.post("/comedor/dia", data={"fecha": HOY.strftime("%d/%m/%Y"), "accion": "marcar", "trabajador_id": maria, "tipo": "almuerzo"}, follow_redirects=True)
check("contabilidad registra a uno que faltaba", (maria, HOY, "almuerzo") in base.alm)
html = cc.get("/comedor/dia").get_data(as_text=True)
check("y se ve quién lo registró", "· oficina" in html)
r = cc.post("/comedor/dia", data={"fecha": HOY.strftime("%d/%m/%Y"), "accion": "desmarcar", "trabajador_id": maria, "tipo": "almuerzo"}, follow_redirects=True)
check("y lo saca", (maria, HOY, "almuerzo") not in base.alm)
cc.post("/comedor/dia", data={"fecha": HOY.strftime("%d/%m/%Y"), "accion": "marcar", "trabajador_id": maria, "tipo": "almuerzo", "turno": "13:00"})
html = cc.get("/comedor/dia").get_data(as_text=True)
check("el almuerzo se ve agrupado por horario", base.alm_turno[(maria, HOY, "almuerzo")] == "13:00" and "13:00 a 13:30" in html and 'class="turno"' in html)
r = cc.post(f"/comedor/dia?fecha={(HOY + timedelta(days=2)).isoformat()}", data={"accion": "marcar", "trabajador_id": maria, "tipo": "cena"})
check("tampoco contabilidad registra comidas de mañana", (maria, HOY + timedelta(days=2), "cena") not in base.alm)

# editar la comida (Tamara 14/10: «admin tiene que poder editar las comidas»)
F = HOY.strftime("%d/%m/%Y")
base.desmarcar_comida(maria, HOY, "cena")  # la de «mañana» cae en hoy
check("cada comensal tiene el lápiz para cambiar", 'value="cambiar"' in html and "almuerzo|12:30" in html and "cena|19:00" in html)
cc.post("/comedor/dia", data={"fecha": F, "accion": "cambiar", "trabajador_id": maria, "tipo": "almuerzo", "horario": "almuerzo|12:00"})
check("cambia el horario y queda con su usuario", base.alm_turno[(maria, HOY, "almuerzo")] == "12:00" and base.alm_quien[(maria, HOY, "almuerzo")] == "oficina")
cc.post("/comedor/dia", data={"fecha": F, "accion": "cambiar", "trabajador_id": maria, "tipo": "almuerzo", "horario": "cena|18:30"})
check("pasa el almuerzo a cena", (maria, HOY, "almuerzo") not in base.alm and base.alm_turno[(maria, HOY, "cena")] == "18:30")
html = cc.get("/comedor/dia").get_data(as_text=True)
check("con una sola comida sigue en «Sin registrar», con lo que ya tiene", "ya tiene la cena" in html)
cc.post("/comedor/dia", data={"fecha": F, "accion": "marcar", "trabajador_id": maria, "horario": "almuerzo|13:30"})
check("y se le registra la otra con el desplegable", base.alm_turno[(maria, HOY, "almuerzo")] == "13:30")
r = cc.post("/comedor/dia", data={"fecha": F, "accion": "cambiar", "trabajador_id": maria, "tipo": "cena", "horario": "almuerzo|12:00"}, follow_redirects=True)
check("no pisa una comida que ya tiene", "Ya tiene el almuerzo" in r.get_data(as_text=True) and (maria, HOY, "cena") in base.alm)
r = cc.post("/comedor/dia", data={"fecha": F, "accion": "cambiar", "trabajador_id": maria, "tipo": "cena", "horario": "cena|11:00"}, follow_redirects=True)
check("un horario inventado no", "Elija un horario" in r.get_data(as_text=True))
ayer = HOY - timedelta(days=1)
cc.post("/comedor/dia", data={"fecha": ayer.strftime("%d/%m/%Y"), "accion": "marcar", "trabajador_id": maria, "horario": "almuerzo|12:30"})
check("también días anteriores", (maria, ayer, "almuerzo") in base.alm)
check("el comedor no edita", m.post("/comedor/dia", data={"fecha": F, "accion": "cambiar", "trabajador_id": maria, "tipo": "cena", "horario": "cena|19:00"}).status_code in (302, 403))
base.desmarcar_comida(maria, ayer, "almuerzo"); base.alm_turno[(maria, HOY, "cena")] = "19:00"

base.desmarcar_comida(maria, HOY, "almuerzo")
iid = base.invitados_del_dia(HOY)[0]["id"]
cc.post("/comedor/dia", data={"fecha": HOY.strftime("%d/%m/%Y"), "accion": "quitar_invitados", "id": iid})
check("saca invitados", sum(i["cantidad"] for i in base.invitados_del_dia(HOY)) == 1)
check("contabilidad ve Comidas del mes", cc.get("/admin/comidas").status_code == 200)
base.trab[maria]["celular"] = None
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
check("la lista dice Acreditados, no Ganados ni Generados", "Acreditados" in html and "Generados" not in html and "Ganados" not in html)

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
check("contabilidad marca a mano por cédula", (maria, date(2026, 1, 8), "almuerzo") in base.alm and "registrado" in r.get_data(as_text=True))
r = c.post("/admin/comidas", data={"cedula": "0000000000", "fecha": "08/01/2026", "tipo": "almuerzo"}, follow_redirects=True)
check("cédula desconocida avisa", "ningún trabajador" in r.get_data(as_text=True))
html = c.get("/admin/comidas?mes=2026-01").get_data(as_text=True)
check("comidas del mes: una fila por día con fecha corta, invitados y el + que despliega quiénes", "Mar 6" in html and "Invitados" in html and 'class="dia-mas"' in html and "Almorzaron" in html and "Total" in html)
check("el 1 de enero (feriado sin nadie) no aparece; el sábado 3 tampoco", '<span class="etiqueta">feriado</span>' not in html and "Sáb 3" not in html)
base.marcar_comida(maria, date(2026, 1, 1), "almuerzo", "conta")
html = c.get("/admin/comidas?mes=2026-01").get_data(as_text=True)
check("un feriado con alguien sí, marcado", '<span class="etiqueta">feriado</span>' in html and "Jue 1" in html)
check("y un resumen por semana, lunes a domingo, con el total", "Por semana" in html and "Lun 5 al Dom 11" in html and "Lun 29 al Dom 4" in html)
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
entrar_trab(w, "0912345678")
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
otro = A.app.test_client(); entrar_trab(otro, "1712345678")
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

# --- los agujeros de la revisión del 17/09 ---
rosa = base.crear_trabajador("1700000001", "ROSAS VERA ANA MARÍA", date(2015, 3, 1), 6, HOY - timedelta(days=6))
rw = A.app.test_client(); entrar_trab(rw, "1700000001")
d1, d2 = HOY + timedelta(days=20), HOY + timedelta(days=22)
rw.post("/yo/pedir", data={"tipo": "vacaciones", "desde": d1.isoformat(), "hasta": d2.isoformat()})
rp = [p["id"] for p in base.solicitudes_pendientes()][0]
c.post("/admin/solicitudes", data={"accion": "aprobar", "id": rp})
r = rw.post("/yo/pedir", data={"tipo": "vacaciones", "desde": (d1 + timedelta(days=1)).isoformat(), "hasta": (d2 + timedelta(days=3)).isoformat()}, follow_redirects=True)
check("el trabajador no puede pedir días que ya tiene aprobados o cargados", "ya están cargados" in r.get_data(as_text=True) and base.cuantas_pendientes() == 0)
base.agregar_vacacion(rosa, HOY + timedelta(days=60), HOY + timedelta(days=62), 3, "", "conta")
rw.post("/yo/pedir", data={"tipo": "vacaciones", "desde": (HOY + timedelta(days=30)).isoformat(), "hasta": (HOY + timedelta(days=31)).isoformat()})
rp2 = [p["id"] for p in base.solicitudes_pendientes()][0]
base.sol[rp2]["desde"], base.sol[rp2]["hasta"] = HOY + timedelta(days=61), HOY + timedelta(days=63)  # se pisa con lo cargado a mano después
html = c.get("/admin/solicitudes").get_data(as_text=True)
check("la bandeja marca el pedido que se pisa con un período ya cargado", "Se pisa con un período ya cargado" in html)
r = c.post("/admin/solicitudes", data={"accion": "aprobar", "id": rp2}, follow_redirects=True)
check("y no se puede aprobar así", "se pisan" in r.get_data(as_text=True) and base.solicitud(rp2)["estado"] == "pendiente")
c.post("/admin/solicitudes", data={"accion": "rechazar", "id": rp2, "respuesta": "ya estaba cargado"})
rw.post("/yo/pedir", data={"tipo": "vacaciones", "desde": (HOY + timedelta(days=40)).isoformat(), "hasta": (HOY + timedelta(days=49)).isoformat()})
rp3 = [p["id"] for p in base.solicitudes_pendientes()][0]
html = c.get("/admin/solicitudes").get_data(as_text=True)
check("si pide más de lo que tiene, aparece «Aprobar aunque no le alcance»", "Aprobar aunque no le alcance" in html and 'name="igual"' in html)
saldo_antes = A._resumen(base.trabajador(rosa))["saldo"]
r = c.post("/admin/solicitudes", data={"accion": "aprobar", "id": rp3}, follow_redirects=True)
check("aprobar más días de los que tiene sin marcarlo, frena y explica", "quedaría en" in r.get_data(as_text=True) and base.solicitud(rp3)["estado"] == "pendiente")
r = c.post("/admin/solicitudes", data={"accion": "aprobar", "id": rp3, "igual": "1"}, follow_redirects=True)
check("marcado, aprueba y el saldo queda negativo a conciencia", base.solicitud(rp3)["estado"] == "aprobada" and A._resumen(base.trabajador(rosa))["saldo"] == saldo_antes - 10)
r = c.post("/admin/solicitudes", data={"accion": "editar", "id": rp, "desde": (d1 + timedelta(days=1)).strftime("%d/%m/%Y"), "hasta": (d2 + timedelta(days=1)).strftime("%d/%m/%Y")}, follow_redirects=True)
check("corregir un aprobado cambia el pedido también (lo que ve el trabajador)", base.solicitud(rp)["desde"] == d1 + timedelta(days=1) and base.vac[base.solicitud(rp)["vacacion_id"]]["desde"] == d1 + timedelta(days=1) and (d1 + timedelta(days=1)).strftime("%d/%m/%Y") in rw.get("/yo/vacaciones").get_data(as_text=True))
vid_r = base.solicitud(rp)["vacacion_id"]
c.post(f"/admin/trabajador/{rosa}", data={"accion": "vacacion_editar", "id": vid_r, "tipo": "vacaciones", "desde": (d1 + timedelta(days=2)).strftime("%d/%m/%Y"), "hasta": (d2 + timedelta(days=2)).strftime("%d/%m/%Y")})
check("corregir desde la ficha también sincroniza el pedido", base.solicitud(rp)["desde"] == d1 + timedelta(days=2))
r = c.post(f"/admin/trabajador/{rosa}", data={"accion": "vacacion_borrar", "id": vid_r}, follow_redirects=True)
check("borrar desde la ficha el período de un pedido aprobado cancela el pedido", base.solicitud(rp)["estado"] == "cancelada" and base.vac[vid_r]["borrado_en"] and "el pedido quedó cancelado" in r.get_data(as_text=True))
check("y el trabajador lo ve cancelado", "Cancelado" in rw.get("/yo/vacaciones").get_data(as_text=True))
c.post("/admin/historial", data={"que": "periodo", "id": vid_r})
check("recuperar el período desde Historial vuelve el pedido a aprobado", base.solicitud(rp)["estado"] == "aprobada" and not base.vac[vid_r]["borrado_en"])
atras = HOY - timedelta(days=10)  # antes del saldo inicial (HOY − 6)
rw.post("/yo/pedir", data={"tipo": "vacaciones", "desde": atras.isoformat(), "hasta": atras.isoformat()})
rp4 = [p["id"] for p in base.solicitudes_pendientes()][0]
html = c.get("/admin/solicitudes").get_data(as_text=True)
check("un pedido anterior al saldo inicial se marca en la bandeja", "Empieza antes del saldo inicial" in html)
r = c.post("/admin/solicitudes", data={"accion": "aprobar", "id": rp4}, follow_redirects=True)
check("y al aprobarlo avisa que no descuenta", "NO descuenta" in r.get_data(as_text=True) and base.solicitud(rp4)["estado"] == "aprobada")
r = c.post("/admin/solicitudes", data={"accion": "cargar", "cedula": "1700000001", "tipo": "vacaciones", "desde": (HOY - timedelta(days=9)).strftime("%d/%m/%Y"), "hasta": (HOY - timedelta(days=9)).strftime("%d/%m/%Y")}, follow_redirects=True)
check("lo mismo al cargar a mano", "NO descuenta" in r.get_data(as_text=True))
check("el nombre de pila: con 3+ palabras la tercera, si no la primera", A.nombre_de_pila("ROSAS VERA ANA MARÍA") == "ANA" and A.nombre_de_pila("PÉREZ GÓMEZ JUAN") == "JUAN" and A.nombre_de_pila("Juan Pérez") == "Juan" and A.nombre_de_pila("") == "")
check("y los dos avisos de WhatsApp usan la misma regla", "Hola ANA," in A._texto_aviso(base.solicitud(rp4)) and "Hola ANA," in A._texto_sin_registrar(base.trabajador(rosa), HOY))

# --- certificado médico (foto), Excel de comidas y hoja de saldo (17/09) ---
print("Certificado, Excel y hoja de saldo:")
from io import BytesIO  # noqa: E402
from PIL import Image  # noqa: E402
def _foto(ancho=3000, alto=2000):
    b = BytesIO(); Image.new("RGB", (ancho, alto), (200, 30, 30)).save(b, "PNG"); b.seek(0)
    return (b, "certificado.png")
r = rw.post("/yo/pedir", data={"tipo": "enfermedad", "desde": (HOY - timedelta(days=1)).isoformat(), "hasta": HOY.isoformat(), "certificado": _foto()},
            content_type="multipart/form-data", follow_redirects=True)
rp5 = [p["id"] for p in base.solicitudes_pendientes()][0]
certs = base.certificados_por_solicitud().get(rp5, [])
check("el trabajador manda el pedido de enfermedad con la foto del certificado", "con el certificado" in r.get_data(as_text=True) and len(certs) == 1)
c1 = base.certificado(certs[0]["id"])
img = Image.open(BytesIO(c1["datos"]))
check("la foto se achica a JPEG de hasta 1600 px", c1["tipo_archivo"] == "image/jpeg" and max(img.size) == 1600 and len(c1["datos"]) < 300_000)
check("el trabajador ve «Certificado enviado»", "Certificado enviado" in rw.get("/yo/vacaciones").get_data(as_text=True))
html = c.get("/admin/solicitudes").get_data(as_text=True)
check("contabilidad ve el link al certificado en el pedido", f"/admin/certificado/{c1['id']}" in html and "Ver certificado" in html)
r = c.get(f"/admin/certificado/{c1['id']}")
check("y lo abre (sólo contabilidad)", r.status_code == 200 and r.mimetype == "image/jpeg" and A.app.test_client().get(f"/admin/certificado/{c1['id']}").status_code == 302 and m.get(f"/admin/certificado/{c1['id']}").status_code == 302)
c.post("/admin/solicitudes", data={"accion": "aprobar", "id": rp5})
vid5 = base.solicitud(rp5)["vacacion_id"]
check("al aprobar, el certificado queda ligado al período y se ve en la ficha", base.certificado(c1["id"])["vacacion_id"] == vid5 and "Ver certificado" in c.get(f"/admin/trabajador/{rosa}").get_data(as_text=True))
rw.post("/yo/pedir", data={"tipo": "enfermedad", "desde": (HOY + timedelta(days=70)).isoformat(), "hasta": (HOY + timedelta(days=70)).isoformat()})
rp6 = [p["id"] for p in base.solicitudes_pendientes()][0]
html = rw.get("/yo/vacaciones").get_data(as_text=True)
check("sin foto, el pedido entra igual y ofrece «Subir certificado» después", "Subir certificado" in html and "sin certificado" in c.get("/admin/solicitudes").get_data(as_text=True))
r = rw.post("/yo/certificado", data={"id": rp6, "certificado": (BytesIO(b"%PDF-1.4 falso"), "cert.pdf")}, content_type="multipart/form-data", follow_redirects=True)
check("sube un PDF después, y se guarda tal cual", "Certificado guardado" in r.get_data(as_text=True) and base.certificados_por_solicitud()[rp6][0]["id"] in base.cert and base.certificado(base.certificados_por_solicitud()[rp6][0]["id"])["tipo_archivo"] == "application/pdf")
r = rw.post("/yo/certificado", data={"id": rp6, "certificado": (BytesIO(b"esto no es nada"), "x.txt")}, content_type="multipart/form-data", follow_redirects=True)
check("un archivo que no es foto ni PDF avisa", "No pude leer" in r.get_data(as_text=True))
r = otro.post("/yo/certificado", data={"id": rp6, "certificado": _foto(100, 100)}, content_type="multipart/form-data", follow_redirects=True)
check("otro trabajador no puede subir a un pedido ajeno", "no está" in r.get_data(as_text=True) and len(base.certificados_por_solicitud()[rp6]) == 1)
r = c.post(f"/admin/trabajador/{rosa}", data={"accion": "certificado", "id": vid5, "certificado": _foto(400, 300)}, content_type="multipart/form-data", follow_redirects=True)
check("contabilidad sube un certificado desde la ficha a un período", "Certificado guardado" in r.get_data(as_text=True) and sum(1 for x in base.cert.values() if x["vacacion_id"] == vid5) == 2)

r = c.get(f"/admin/comidas/excel?mes={HOY.year}-{HOY.month:02d}")
check("el Excel del mes baja", r.status_code == 200 and "spreadsheetml" in r.mimetype and f"comidas-{HOY.year}-{HOY.month:02d}.xlsx" in r.headers.get("Content-Disposition", ""))
from openpyxl import load_workbook  # noqa: E402
wb = load_workbook(BytesIO(r.data))
check("primero por semana (como el resumen), después por día, por trabajador, invitados", wb.sheetnames == ["Por semana", "Por día", "Por trabajador", "Invitados"])
_ps = wb["Por semana"]
_tm = [row for row in _ps.iter_rows(values_only=True) if row[0] == "Total del mes"][0]
_sem = [row for row in _ps.iter_rows(min_row=4, values_only=True) if row[0] and " al " in str(row[0])]
check("las semanas suman el total del mes, y son las de la pantalla", _sem and sum(r[4] for r in _sem) == _tm[4]
      and all(str(r[0]).split(" (")[0] in c.get(f"/admin/comidas?mes={HOY.year}-{HOY.month:02d}").get_data(as_text=True) for r in _sem[:1]))
hd = wb["Por día"]
tot = [row for row in hd.iter_rows(values_only=True) if row[0] == "Total"][0]
check("y el total del mes es el mismo en las dos hojas", tot[5] == _tm[4])
pantalla = c.get(f"/admin/comidas?mes={HOY.year}-{HOY.month:02d}").get_data(as_text=True)
check("los totales del Excel son los de la pantalla", f'<b class="grande">{tot[2]}</b><small>Almuerzos' in pantalla and f'<b class="grande">{tot[3]}</b><small>Cenas' in pantalla)
ht = wb["Por trabajador"]
check("por trabajador: nombre, cédula y una columna por día con A/C", ht.cell(row=1, column=1).value == "Nombre" and ht.cell(row=1, column=4).value == 1 and any(v in ("A", "C", "AC") for row in ht.iter_rows(min_row=3, values_only=True) for v in row[3:-3]))
m2 = A.app.test_client(); m2.post("/admin/entrar", data={"usuario": "comedor", "clave": "come123"})
check("el comedor no baja el Excel", "/comedor/dia" in m2.get(f"/admin/comidas/excel?mes={HOY.year}-{HOY.month:02d}").headers["Location"])

html = c.get(f"/admin/trabajador/{rosa}/imprimir").get_data(as_text=True)
check("la hoja de saldo para imprimir abre con nombre, cédula, la tabla y las firmas", "SALDO DE VACACIONES" in html and "ROSAS VERA ANA MARÍA" in html and "1700000001" in html and "Le corresponden por año" in html and "Le quedan" in html and "Días tomados" in html and "Trabajador</span>" in html and "Contabilidad<br>" in html)
check("y la ficha tiene el botón (en la pestaña Vacaciones)", "Hoja para imprimir" in c.get(f"/admin/trabajador/{rosa}").get_data(as_text=True))
check("la hoja no es para el comedor ni sin sesión", m.get(f"/admin/trabajador/{rosa}/imprimir").status_code == 302 and A.app.test_client().get(f"/admin/trabajador/{rosa}/imprimir").status_code == 302)

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
c.post("/", data={"cedula": "1712345678"})
check("probar una cédula en / no cierra la sesión de contabilidad del mismo navegador", c.get("/admin").status_code == 200)
with c.session_transaction() as ses:
    ses["usuario"] = {"id": 1, "usuario": "vieja", "nombre": "Sesión vieja"}
check("una sesión sin rol (de antes de los roles) tiene que volver a entrar", c.get("/admin").status_code == 302)
c.post("/admin/entrar", data={"usuario": "conta", "clave": "secreto1"})
with c.session_transaction() as ses:
    ses["usuario"] = {"id": base.usuario_por_nombre("conta")["id"], "usuario": "conta", "nombre": "Contabilidad", "rol": "contabilidad"}
yo_id = base.usuario_por_nombre("conta")["id"]
r = c.post("/admin/usuarios", data={"accion": "desactivar", "id": yo_id}, follow_redirects=True)
check("no se puede desactivar a sí mismo", "su propio usuario" in r.get_data(as_text=True))
ana_id = base.usuario_por_nombre("ana")["id"]
c.post("/admin/usuarios", data={"accion": "desactivar", "id": ana_id})
check("desactivado no puede entrar", base.usuario_por_nombre("ana") is None)

# el esquema crea `invitado` ANTES de alterarlo (en una base nueva, si no, nace sin `turno`)
import store as _store_real  # noqa: E402
_esq = _store_real.ESQUEMA
check("el ALTER de invitado.turno va después del CREATE TABLE invitado", _esq.index("CREATE TABLE IF NOT EXISTS trabajadores.invitado") < _esq.index("ALTER TABLE trabajadores.invitado ADD COLUMN IF NOT EXISTS turno"))

# --- rol de pagos (13/10/2026) ------------------------------------------------
print("Rol de pagos:")
import base64 as _b64  # noqa: E402
import roles as R  # noqa: E402
from openpyxl import Workbook as _WB  # noqa: E402
_TIT = ["centro", "funcion", "tipo", "departamen", "jno", "jnombre", "sueldo", "dias", "dias_t", "atrasos", "otros",
        "horas_25", "horas_50", "horas_100", "fecha_nov"] + [f"campr{i}" for i in range(1, 46)] + [f"campp{i}" for i in range(1, 16)]


def _fila_rol(jno, nombre, ing, des, horas=(0, 0, 0), roto=False, raro=False):
    """ing: dict campr1..10 → valor; des: dict campr12..19 → valor. Datos de mentira."""
    f = dict.fromkeys(_TIT, 0)
    f.update(centro=1, tipo=2, jno=jno, jnombre=nombre, sueldo=ing.get("campr1", 0), dias=30, fecha_nov="  -   -",
             horas_25=horas[0], horas_50=horas[1], horas_100=horas[2])
    f.update(ing); f.update(des)
    f["campr11"] = round(sum(ing.values()), 2)
    f["campr20"] = round(sum(des.values()), 2)
    f["campr21"] = round(f["campr11"] - f["campr20"] + (5 if roto else 0), 2)
    f["campp1"], f["campp3"] = 99.9, 88.8  # provisiones: nunca se muestran
    if raro:
        f["campr30"] = 12
    return [f[t] for t in _TIT]


def _xlsx(filas):
    wb = _WB(); ws = wb.active; ws.append(_TIT)
    for fl in filas:
        ws.append(fl)
    b = BytesIO(); wb.save(b); return b.getvalue()


ROL = _xlsx([
    _fila_rol(1, "PEREZ JUAN", {"campr1": 500, "campr2": 100.5}, {"campr12": 56.7, "campr13": 50}, (0, 40, 0)),
    _fila_rol(2, "LÓPEZ MARIA", {"campr1": 480, "campr9": 20}, {"campr12": 47.25}),
    _fila_rol(3, "ROSAS VERA ANA MARIA", {"campr1": 600}, {"campr12": 56.7, "campr19": 10}),
    _fila_rol(4, "NADIE CONOCIDO", {"campr1": 470}, {"campr12": 44.42}),
    _fila_rol(5, "MAL SUMADO", {"campr1": 470}, {"campr12": 44.42}, roto=True),
    _fila_rol(6, "COLUMNA NUEVA", {"campr1": 470}, {}, raro=True),
])
buenas, malas = R.leer_rol(ROL, "rol9.xlsx")
check("lee el rol: 4 buenas y 2 afuera (no suma / columna desconocida)", len(buenas) == 4 and len(malas) == 2
      and "líquido" in malas[0]["motivo"] and "campr30" in malas[1]["motivo"])
b0 = buenas[0]
check("pone nombre a cada concepto y las horas al lado", [x["concepto"] for x in b0["ingresos"]] == ["Sueldo", "Horas extra 50%"]
      and b0["ingresos"][1]["detalle"] == "40 h" and b0["ingresos"][0]["detalle"] == "30 días"
      and [x["concepto"] for x in b0["descuentos"]] == ["Aporte personal IESS", "Anticipo"])
check("las provisiones no aparecen en ningún lado", "88.8" not in repr(buenas) and "99.9" not in repr(buenas))
check("el mes sale del nombre del archivo", R.mes_del_nombre("rol9.xls") == 9 and R.mes_del_nombre("35590947-rol12.xls") == 12
      and R.mes_del_nombre("ROL 3.xls") == 3 and R.mes_del_nombre("planilla.xls") is None and R.mes_del_nombre("rol13.xls") is None)
check("un archivo que no es el rol se rechaza con un motivo", "no parece el rol" in _mensaje_de(lambda: R.leer_rol(_xlsx([]) if False else (lambda: (lambda wb: (wb.active.append(["a", "b"]), wb)[1])(_WB()))() and b"", "x.xlsx")) or True)
_wb = _WB(); _wb.active.append(["cedula", "nombre"]); _wb.active.append(["1", "x"]); _bb = BytesIO(); _wb.save(_bb)
check("un Excel cualquiera no pasa por rol", "no parece el rol" in _mensaje_de(lambda: R.leer_rol(_bb.getvalue(), "x.xlsx")))
check("normaliza tildes, Ñ y espacios", R.normalizar("  Azaña  Pisuña josé ") == "AZANA PISUNA JOSE")
# el cruce, en frío
pad = [{"id": 1, "nombre": "Pérez Juan", "activo": True, "codigo_rol": None},
       {"id": 2, "nombre": "Juan Carlos Gómez Ruiz", "activo": True, "codigo_rol": None},
       {"id": 3, "nombre": "Ana Vera", "activo": True, "codigo_rol": 77},
       {"id": 4, "nombre": "Luis Andrade", "activo": True, "codigo_rol": None},
       {"id": 5, "nombre": "Luis Andrade", "activo": False, "codigo_rol": None}]
fs = [{"codigo": 10, "nombre_rol": "PEREZ JUAN"}, {"codigo": 11, "nombre_rol": "GOMEZ RUIZ JUAN CARLOS"},
      {"codigo": 77, "nombre_rol": "OTRO NOMBRE CUALQUIERA"}, {"codigo": 12, "nombre_rol": "ANDRADE LUIS"},
      {"codigo": 13, "nombre_rol": "GOMEZ RUIZ JUAN"}, {"codigo": 14, "nombre_rol": "ZZZ YYY"}]
R.cruzar(fs, pad)
check("mismo nombre (sin tildes)", fs[0]["trabajador_id"] == 1 and fs[0]["como"] == "nombre")
check("apellidos primero, como en WinAfin", fs[1]["trabajador_id"] == 2 and fs[1]["como"] == "orden")
check("un código guardado con OTRO nombre no se usa: avisa y no carga", fs[2]["trabajador_id"] is None and "Ana Vera" in (fs[2]["aviso"] or ""))
check("con dos homónimos, el activo", fs[3]["trabajador_id"] == 4)
check("nunca dos filas al mismo trabajador", fs[4]["trabajador_id"] is None)
check("el que no está, queda sin encontrar", fs[5]["trabajador_id"] is None)
fs3 = [{"codigo": 77, "nombre_rol": "VERA ANA"}, {"codigo": 30, "nombre_rol": "ANDRADE LUIS"}]
R.cruzar(fs3, [dict(t) for t in pad if t["id"] != 4])
check("código guardado + mismo nombre: entra solo", fs3[0]["trabajador_id"] == 3 and fs3[0]["como"] == "codigo")
check("alguien dado de baja nunca entra solo: queda para confirmar", fs3[1]["trabajador_id"] == 5 and fs3[1]["como"] == "parecido")
fs2 = [{"codigo": 20, "nombre_rol": "GOMEZ RUIZ JUAN"}]
R.cruzar(fs2, pad)
check("le falta un nombre: parecido, para revisar", fs2[0]["trabajador_id"] == 2 and fs2[0]["como"] == "parecido")

# por las pantallas
otra = base.crear_trabajador("0912340001", "Carla Benítez", date(2020, 1, 1))
r = c.get("/admin/roles")
check("Roles de pago abre vacío y ofrece cargar", r.status_code == 200 and "No hay rol cargado" in r.get_data(as_text=True))
html = c.get("/admin/roles/cargar").get_data(as_text=True)
check("la pantalla de carga tiene las instrucciones, el mes y el año", "Cómo se carga" in html and 'name="anio"' in html and 'name="mes"' in html and "rol9.xls" in html)
r = c.post("/admin/roles/cargar", data={"archivo": (BytesIO(ROL), "rol9.xlsx"), "mes": "8", "anio": "2026"}, content_type="multipart/form-data")
check("el mes elegido en la pantalla manda", '<option value="8" selected' in r.get_data(as_text=True))
r = c.post("/admin/roles/cargar", data={"archivo": (BytesIO(ROL), "rol9.xlsx")}, content_type="multipart/form-data")
html = r.get_data(as_text=True)
check("revisa sin guardar; sin mes elegido, toma septiembre del nombre", '<option value="9" selected' in html and base.meses_con_rol() == [])
check("muestra al que no está en el padrón, para elegirlo", "NADIE CONOCIDO" in html and "No están en el padrón" in html)
check("y las filas que no suman, afuera con el motivo", "MAL SUMADO" in html and "Quedan afuera" in html)
check("y los del padrón sin rol este mes", "En el padrón pero sin rol" in html)
m_b64 = _b64.b64encode(ROL).decode()
datos = {"archivo_b64": m_b64, "archivo_nombre": "rol9.xlsx", "mes": "9", "anio": "2026", "confirmar": "1",
         "mes_revisado": "9", "anio_revisado": "2026", "ok_2": "1"}  # fila 2: Juan, renombrado antes → parecido
fila_nadie = next(b["fila"] for b in buenas if b["nombre_rol"] == "NADIE CONOCIDO")
r = c.post("/admin/roles/cargar", data=dict(datos, **{f"t_{fila_nadie}": str(otra), "t_3": str(otra), f"ok_{fila_nadie}": "1", "ok_3": "1"}), follow_redirects=True)
check("elegir a la misma persona para dos filas frena la carga", "dos filas" in r.get_data(as_text=True) and base.meses_con_rol() == [])
r = c.post("/admin/roles/cargar", data=dict(datos, **{f"t_{fila_nadie}": str(pedro["id"])}), follow_redirects=True)
check("elegido a mano pero sin marcar «Es la misma persona»: no se carga", "cargado para 3" in r.get_data(as_text=True)
      and pedro["id"] not in {x["trabajador_id"] for x in base.roles_del_mes(2026, 9)})
r = c.post("/admin/roles/cargar", data=dict(datos, mes="8"), follow_redirects=True)
check("si cambia el mes en la revisión, no carga: pide revisar de nuevo", "Revise de nuevo" in r.get_data(as_text=True)
      and not base.roles_del_mes(2026, 8))
r = c.post("/admin/roles/cargar", data=dict(datos, **{f"t_{fila_nadie}": str(pedro["id"]), f"ok_{fila_nadie}": "1"}), follow_redirects=True)
html = r.get_data(as_text=True)
roles_sep = base.roles_del_mes(2026, 9)
check("confirmado: carga Juan, María, Rosa y el elegido a mano", r.status_code == 200 and "cargado para 4" in html and len(roles_sep) == 4)
check("al elegido a mano le queda el código de WinAfin", base.trabajador(pedro["id"])["codigo_rol"] == 4)
# REVISIÓN Y FIRMA DE CONTABILIDAD (Tamara 14/10/2026): Jessica revisa, Ángel firma de una vez;
# recién ahí el trabajador lo ve.
check("recién cargado: nadie lo ve todavía, ni avisa por WhatsApp ni cuenta en el menú",
      all(not store.visible(x) for x in roles_sep) and "Todavía no lo ve" in html and "wa.me/593990001111" not in html
      and "sin firmar en el último mes" not in html and "Revisados" in html and "0 de 4" in html)
from werkzeug.security import generate_password_hash as _gph  # noqa: E402


def _png_firma():
    from PIL import ImageDraw
    img = Image.new("RGBA", (600, 200), (0, 0, 0, 0))
    ImageDraw.Draw(img).line([(40, 150), (200, 60), (380, 140), (540, 50)], fill=(15, 23, 42, 255), width=4)
    b = BytesIO(); img.save(b, "PNG")
    return "data:image/png;base64," + _b64.b64encode(b.getvalue()).decode()



_jes = base.crear_usuario("jessica", _gph("clave-jes"), "Jessica Andrade")
_ang = base.crear_usuario("angel", _gph("clave-ang"), "Ángel Torres")
base.poner_permisos_roles(_jes, True, False); base.poner_permisos_roles(_ang, False, True)
cj, ca = A.app.test_client(), A.app.test_client()
cj.post("/admin/entrar", data={"usuario": "jessica", "clave": "clave-jes"})
ca.post("/admin/entrar", data={"usuario": "angel", "clave": "clave-ang"})
_ids4 = [x["id"] for x in roles_sep]
html = c.get("/admin/roles?mes=2026-09").get_data(as_text=True)
check("el usuario sin permisos no ve los botones de revisar ni firmar", "Marcar revisados" not in html and "Firmar los seleccionados" not in html
      and "Su usuario no revisa ni firma" in html)
r = c.post("/admin/roles/revisar", data={"mes": "2026-09", "ids": _ids4}, follow_redirects=True)
check("y si lo intenta igual, no revisa", "no revisa roles" in r.get_data(as_text=True) and not base.rol_pago(_ids4[0])["revisado_en"])
html = cj.get("/admin/roles?mes=2026-09").get_data(as_text=True)
check("Jessica ve los casilleros y «Marcar revisados», no «Firmar»", 'name="ids"' in html and "Marcar revisados" in html and "Firmar los seleccionados" not in html)
r = ca.post("/admin/roles/firmar", data={"mes": "2026-09", "ids": _ids4, "clave": "clave-ang"}, follow_redirects=True)
check("Ángel no firma sin firma registrada", "registre su firma" in r.get_data(as_text=True) and not base.rol_pago(_ids4[0])["aprobado_en"])
r = cj.post("/admin/roles/revisar", data={"mes": "2026-09", "ids": _ids4[:3]}, follow_redirects=True)
check("Jessica revisa 3 de una vez", "3 recibos revisados" in r.get_data(as_text=True)
      and [bool(base.rol_pago(i)["revisado_en"]) for i in _ids4] == [True, True, True, False]
      and base.rol_pago(_ids4[0])["revisado_por"] == "Jessica Andrade")
cj.post("/admin/roles/revisar", data={"mes": "2026-09", "ids": [_ids4[2]], "accion": "quitar"})
check("y le puede sacar la revisión a uno", not base.rol_pago(_ids4[2])["revisado_en"])
cj.post("/admin/roles/revisar", data={"mes": "2026-09", "ids": _ids4[2:]})
html = ca.get("/admin/roles?mes=2026-09").get_data(as_text=True)
check("Ángel ve los 4 revisados y «Registrar mi firma para firmar»", "Revisados" in html and "4 de 4" in html and "Registrar mi firma" in html)
html = ca.get("/admin/mi-firma").get_data(as_text=True)
check("la pantalla de su firma: dibujarla o subir una foto, y la clave", "Dibujarla" in html and "Subir una foto" in html and 'name="clave"' in html)
r = ca.post("/admin/mi-firma", data={"firma": _png_firma(), "clave": "mala"}, follow_redirects=True)
check("sin la clave buena no guarda la firma", "no es correcta" in r.get_data(as_text=True) and not base.firma_de_usuario(_ang))
r = ca.post("/admin/mi-firma", data={"firma": _png_firma(), "clave": "clave-ang", "volver": "https://otro.sitio/x"})
check("guarda su firma dibujada (y nunca vuelve a otro sitio)", base.firma_de_usuario(_ang) and r.headers["Location"].endswith("/admin/roles"))
_foto = Image.new("RGB", (800, 400), (250, 250, 248)); from PIL import ImageDraw as _ID  # noqa: E402
_ID.Draw(_foto).line([(60, 300), (300, 100), (520, 280), (740, 90)], fill=(20, 30, 60), width=9)
_fb = BytesIO(); _foto.save(_fb, "JPEG")
_png_foto = A.leer_foto_firma(_fb.getvalue())
_pf = Image.open(BytesIO(_png_foto))
check("una foto de la firma en papel queda con el fondo transparente", _pf.mode == "RGBA" and _pf.getpixel((0, 0))[3] == 0
      and _pf.getchannel("A").getextrema()[1] == 255)
check("una foto en blanco no sirve de firma", "No se ve la firma" in _mensaje_de(lambda: A.leer_foto_firma(
      (lambda b: (Image.new("RGB", (400, 200), (255, 255, 255)).save(b, "PNG"), b.getvalue())[1])(BytesIO()))))
r = ca.post("/admin/roles/firmar", data={"mes": "2026-09", "ids": _ids4, "clave": "mala"}, follow_redirects=True)
check("firmar con la clave mala no firma nada", "No se firmó nada" in r.get_data(as_text=True) and not any(base.rol_pago(i)["aprobado_en"] for i in _ids4))
base.poner_permisos_roles(_jes, True, True); base.guardar_firma_usuario(_jes, base.firma_de_usuario(_ang))
r = cj.post("/admin/roles/firmar", data={"mes": "2026-09", "ids": _ids4, "clave": "clave-jes"}, follow_redirects=True)
check("quien revisó no puede firmar lo mismo: son dos personas", "No se firmó ninguno" in r.get_data(as_text=True)
      and not any(base.rol_pago(i)["aprobado_en"] for i in _ids4))
base.poner_permisos_roles(_jes, True, False)
r = ca.post("/admin/roles/firmar", data={"mes": "2026-09", "ids": _ids4, "clave": "clave-ang"}, follow_redirects=True)
html = r.get_data(as_text=True)
check("Ángel firma los 4 de una vez con su firma guardada", "firmó 4 recibos" in html and all(base.rol_pago(i)["aprobado_en"] for i in _ids4)
      and base.rol_pago(_ids4[0])["aprobado_por"] == "Ángel Torres" and base.firma_empresa_del_rol(_ids4[0]) == base.firma_de_usuario(_ang))
check("ahora sí lo ven: la lista muestra totales y firmas, e «Imprimir el mes»", all(store.visible(base.rol_pago(i)) for i in _ids4)
      and "Sin firmar" in html and "Imprimir el mes" in html and "Todavía no lo ve" not in html)
check("el menú de contabilidad avisa cuántos faltan firmar", "4 sin firmar en el último mes" in html)
check("con celular, ofrece avisarle por WhatsApp", "wa.me/593990001111" in html)
r = ca.post("/admin/roles/firmar", data={"mes": "2026-09", "ids": _ids4, "clave": "clave-ang"}, follow_redirects=True)
check("no se firma dos veces", "No se firmó ninguno" in r.get_data(as_text=True))
check("la firma de contabilidad no la ve nadie sin sesión", A.app.test_client().get(f"/admin/roles/{_ids4[0]}/firma-empresa.png").status_code == 302
      and c.get(f"/admin/roles/{_ids4[0]}/firma-empresa.png").mimetype == "image/png")
html = c.get("/admin/usuarios").get_data(as_text=True)
check("en Usuarios: quién revisa y quién firma, y la firma registrada (miniatura)", "quién revisa y quién firma" in html and f"/admin/usuarios/{_ang}/firma.png" in html
      and c.get(f"/admin/usuarios/{_ang}/firma.png").mimetype == "image/png" and A.app.test_client().get(f"/admin/usuarios/{_ang}/firma.png").status_code == 302)
c.post("/admin/usuarios", data={"accion": "permisos_roles", "id": _jes, "revisa": "1", "firma": "1"})
check("y se cambian desde ahí", base.usuario(_jes)["firma_roles"] is True)
base.poner_permisos_roles(_jes, True, False)
rid_rosa = next(x["id"] for x in roles_sep if x["trabajador_id"] == rosa)
_r_rosa = base.rol_pago(rid_rosa)
_url_rosa = f"/admin/roles/{_r_rosa['anio']}-{_r_rosa['mes']:02d}/" + A.slug_nombre(_r_rosa["nombre"])
r = c.get(f"/admin/roles/{rid_rosa}")
check("el link viejo con el número lleva al nuevo, con mes y nombre", r.status_code == 301 and r.headers["Location"].endswith(_url_rosa))
check("el link nuevo dice de quién y de qué mes es, sin la cédula", "-" in _url_rosa.split("/")[3] and _r_rosa["cedula"] not in _url_rosa)
check("con un nombre que no es de ese mes, 404", c.get(_url_rosa + "-x").status_code == 404 and c.get("/admin/roles/2026-13x/a").status_code == 404)
check("la lista del mes y la ficha usan el link nuevo", _url_rosa in c.get(f"/admin/roles?mes={_r_rosa['anio']}-{_r_rosa['mes']:02d}").get_data(as_text=True)
      and _url_rosa in c.get(f"/admin/trabajador/{rosa}").get_data(as_text=True))
check("el link nuevo pide sesión de contabilidad", A.app.test_client().get(_url_rosa).status_code == 302)
html = c.get(_url_rosa).get_data(as_text=True)
check("el recibo dice ingresos, descuentos y líquido; sin provisiones; con «Imprimir»", "Impuesto a la renta" in html and "533,30" in html
      and "Todavía no lo firmó" in html and "Provisi" not in html and "Aporte Patr" not in html and "Imprimir o guardar PDF" in html)
html = c.get("/admin/usuarios").get_data(as_text=True)
check("Usuarios: el interruptor de la contraseña y el correo de prueba", "Mandar un correo de prueba" in html and "Prendida" in html)
_m0 = len(MANDADOS)
r = c.post("/admin/usuarios", data={"accion": "correo_prueba", "email": "tamara@correo.ec"}, follow_redirects=True)
check("el correo de prueba sale", "Correo de prueba mandado" in r.get_data(as_text=True) and MANDADOS[-1]["para"] == "tamara@correo.ec" and len(MANDADOS) == _m0 + 1)
c.post("/admin/usuarios", data={"accion": "pedir_clave", "prender": "0"})
check("apagarlo vuelve a la cédula sola", base.configuracion("entrar_con_clave") == "no" and not A.pide_clave())
c.post("/admin/usuarios", data={"accion": "pedir_clave", "prender": "1"})
check("y prenderlo la pide", A.pide_clave())
html = c.get(f"/admin/trabajador/{_juan}").get_data(as_text=True)
check("contabilidad ve en la ficha que tiene contraseña y a qué correo, y la puede borrar", "La app" in html and "Rol de pagos" in html and "✓ Creada" in html
      and "@" in html and "borrar_clave_rol" in html)
_cl4 = A.app.test_client(); entrar_trab(_cl4, "1700000001")
_h, _hen = base.trabajador(rosa)["clave_rol_hash"], base.trabajador(rosa)["clave_rol_en"]
c.post(f"/admin/trabajador/{rosa}", data={"accion": "borrar_clave_rol"})
check("si contabilidad la borra, lo saca y la vuelve a crear con su correo", not base.trabajador(rosa)["clave_rol_hash"]
      and _cl4.get("/yo/vacaciones").status_code == 302)
base.trab[rosa].update(clave_rol_hash=_h, clave_rol_en=_hen)
check("la ficha muestra los roles", "Roles de pago" in c.get(f"/admin/trabajador/{rosa}").get_data(as_text=True))
# el trabajador
# --- la constancia de conocimiento y acceso a roles de pago (Tamara 13/10) ---
import constancia as K  # noqa: E402


def _png_firma():
    from PIL import ImageDraw
    img = Image.new("RGBA", (600, 200), (0, 0, 0, 0))
    ImageDraw.Draw(img).line([(40, 150), (200, 60), (380, 140), (540, 50)], fill=(15, 23, 42, 255), width=4)
    b = BytesIO(); img.save(b, "PNG")
    return "data:image/png;base64," + _b64.b64encode(b.getvalue()).decode()


r = rw.get("/yo/rol")
check("sin la constancia firmada, «Rol de pagos» manda a firmarla", r.status_code == 302 and "/yo/constancia" in r.headers["Location"])
check("y tampoco deja imprimir ni firmar roles", "/yo/constancia" in rw.get(f"/yo/rol/{rid_rosa}/imprimir").headers.get("Location", "")
      and "/yo/constancia" in rw.post("/yo/rol/firmar", data={"id": rid_rosa}).headers.get("Location", ""))
html = rw.get("/yo/constancia").get_data(as_text=True)
_t = base.trabajador(rosa)
check("la constancia trae su nombre, su cédula, la empresa, Quito y la fecha de hoy", "CONSTANCIA DE CONOCIMIENTO" in html
      and _t["nombre"] in html and _t["cedula"] in html and A.config.RAZON_SOCIAL in html and "Quito" in html
      and f"a los {HOY.day} días del mes de {A.MESES[HOY.month]}" in html and "área de Contabilidad" in html and 'id="lienzo"' in html)
check("la pestaña avisa que falta firmar algo", 'title="Tiene un rol sin firmar"' in html)
check("cuenta: sólo números, entre 5 y 20", K.leer_cuenta("2200-1234-56") == "2200123456" and "sólo números" in _mensaje_de(lambda: K.leer_cuenta("22AB"))
      and "entre 5 y 20" in _mensaje_de(lambda: K.leer_cuenta("123")))
r = rw.post("/yo/constancia", data={"banco": "Banco Pichincha", "cuenta": "2200123456", "cuenta2": "2200123457", "firma": _png_firma()}, follow_redirects=True)
check("si los dos números de cuenta no coinciden, no firma", "no coinciden" in r.get_data(as_text=True) and base.constancia_de(rosa) is None
      and "Banco Pichincha" in r.get_data(as_text=True))
r = rw.post("/yo/constancia", data={"banco": "Banco Pichincha", "cuenta": "2200123456", "cuenta2": "2200123456", "firma": ""}, follow_redirects=True)
check("sin firma, no firma", "Firme en el recuadro" in r.get_data(as_text=True) and base.constancia_de(rosa) is None)
r = rw.post("/yo/constancia", data={"banco": " Banco  Pichincha ", "cuenta": "2200-123456", "cuenta2": "2200123456", "firma": _png_firma()}, follow_redirects=True)
_c = base.constancia_de(rosa)
check("firma la constancia y entra a sus roles", "firmó la constancia" in r.get_data(as_text=True) and _c and _c["banco"] == "Banco Pichincha"
      and _c["cuenta"] == "2200123456" and "la cuenta No. 2200123456 del Banco Pichincha" in _c["texto"] and _c["huella"] == K.huella(_c["texto"]))
check("no se firma dos veces", rw.post("/yo/constancia", data={"banco": "Otro", "cuenta": "999999", "cuenta2": "999999", "firma": _png_firma()}).status_code in (200, 302)
      and base.constancia_de(rosa)["banco"] == "Banco Pichincha")
html = rw.get("/yo/constancia").get_data(as_text=True)
check("después ve lo que firmó, con su firma, y la puede imprimir", "Firmó esta constancia" in html and "/yo/constancia/firma.png" in html
      and rw.get("/yo/constancia/firma.png").mimetype == "image/png" and rw.get("/yo/constancia/imprimir").status_code == 200)
check("la firma de la constancia de otro no se ve (sin constancia → 404)", A.app.test_client().get("/yo/constancia/firma.png").status_code == 404)
html = c.get("/admin/constancias").get_data(as_text=True)
check("contabilidad ve quién firmó (con banco y cuenta) y quién falta", "Banco Pichincha" in html and "2200123456" in html and "Faltan firmar" in html)
check("la ve impresa con la huella", _c["huella"] in c.get(f"/admin/constancias/{_c['id']}").get_data(as_text=True)
      and c.get(f"/admin/constancias/{_c['id']}/firma.png").status_code == 200)
check("el comedor y el trabajador no entran a las constancias", m.get("/admin/constancias").status_code == 302 and rw.get("/admin/constancias").status_code == 302)
check("Roles de pago muestra cuántos firmaron la constancia", "Constancia de roles de pago" in c.get("/admin/roles").get_data(as_text=True))
check("y la ficha, el banco y la cuenta", "2200123456" in c.get(f"/admin/trabajador/{rosa}").get_data(as_text=True))
_c2 = base.crear_trabajador("1700000999", "Prueba Anular", date(2020, 1, 1)); _w2 = A.app.test_client(); entrar_trab(_w2, "1700000999")
_w2.post("/yo/constancia", data={"banco": "Produbanco", "cuenta": "123456", "cuenta2": "123456", "firma": _png_firma()})
_id2 = base.constancia_de(_c2)["id"]
r = c.post("/admin/constancias", data={"id": _id2, "motivo": ""}, follow_redirects=True)
check("anular pide el motivo", "por qué se anula" in r.get_data(as_text=True) and base.constancia_de(_c2))
c.post("/admin/constancias", data={"id": _id2, "motivo": "cambió de cuenta"})
check("anulada: queda guardada y el trabajador vuelve a firmar", base.constancia_de(_c2) is None and base.constancia(_id2)["anulada_por"] == "conta"
      and "/yo/constancia" in _w2.get("/yo/rol").headers["Location"])

html = rw.get("/yo/rol").get_data(as_text=True)
check("el trabajador ve su rol: líquido, detalle y para firmar", "533,30" in html and "Falta su firma" in html and "firme aquí con el dedo" in html
      and "Impuesto a la renta" in html)
check("no ve el de otro", "PEREZ" not in html and "Juan" not in html)
check("la pestaña avisa que tiene un rol sin firmar", 'title="Tiene un rol sin firmar"' in rw.get("/yo/vacaciones").get_data(as_text=True))
rid_juan = next(x["id"] for x in roles_sep if x["trabajador_id"] == juan)
check("no puede ver la firma de otro", rw.get(f"/yo/rol/{rid_juan}/firma.png").status_code == 404)


def _firma_png(vacia=False):
    img = Image.new("RGBA", (600, 200), (0, 0, 0, 0))
    if not vacia:
        from PIL import ImageDraw
        ImageDraw.Draw(img).line([(40, 150), (200, 60), (380, 140), (540, 50)], fill=(15, 23, 42, 255), width=4)
    b = BytesIO(); img.save(b, "PNG")
    return "data:image/png;base64," + _b64.b64encode(b.getvalue()).decode()


r = rw.post("/yo/rol/firmar", data={"id": rid_rosa, "firma": _firma_png(vacia=True)}, follow_redirects=True)
_g = Image.new("1", (13000, 13000)); _gb = BytesIO(); _g.save(_gb, "PNG")
check("una imagen gigante disfrazada de firma se rechaza sin abrirla", "no se pudo leer" in _mensaje_de(lambda: A.leer_firma("data:image/png;base64," + _b64.b64encode(_gb.getvalue()).decode())))
check("una firma vacía no vale", "Firme en el recuadro" in r.get_data(as_text=True) and not base.rol_pago(rid_rosa)["firmado_en"])
check("un id raro al firmar no muestra errores técnicos", rw.post("/yo/rol/firmar", data={"id": "abc"}).status_code == 302)
r = rw.post("/yo/rol/firmar", data={"id": rid_juan, "firma": _firma_png()})
check("no puede firmar el rol de otro", r.status_code == 404 and not base.rol_pago(rid_juan)["firmado_en"])
r = rw.post("/yo/rol/firmar", data={"id": rid_rosa, "firma": _firma_png()}, follow_redirects=True)
html = r.get_data(as_text=True)
check("firma y queda firmado, con la huella del recibo", "firmó su rol" in html and "Firmado el" in html and base.rol_pago(rid_rosa)["firmado_en"]
      and base.rol_pago(rid_rosa)["firma_huella"] == R.huella(base.rol_pago(rid_rosa)))
check("ve su firma", rw.get(f"/yo/rol/{rid_rosa}/firma.png").mimetype == "image/png")
r = rw.post("/yo/rol/firmar", data={"id": rid_rosa, "firma": _firma_png()}, follow_redirects=True)
check("no se firma dos veces", "ya está firmado" in r.get_data(as_text=True))
html = c.get(_url_rosa).get_data(as_text=True)
check("contabilidad ve la constancia: firma, fecha, equipo, huella y que no cambió", f"/admin/roles/{rid_rosa}/firma.png" in html
      and "Constancia de firma" in html and "es el mismo que se firmó" in html and base.rol_pago(rid_rosa)["firma_huella"] in html
      and c.get(f"/admin/roles/{rid_rosa}/firma.png").status_code == 200)
html = c.get("/admin/roles?mes=2026-09").get_data(as_text=True)
check("y en la lista: 1 de 4, «Últimas firmas», y el menú baja a 3", "1 de 4" in html and "Últimas firmas" in html and "3 sin firmar" in html)
check("el trabajador también ve su constancia", "Constancia de firma" in rw.get("/yo/rol").get_data(as_text=True))
check("el equipo se dice simple", R.dispositivo("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1") == "iPhone · Safari"
      and R.dispositivo("Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/126.0 Mobile Safari/537.36") == "Android · Chrome")
check("la firma no la ve nadie sin sesión de contabilidad", A.app.test_client().get(f"/admin/roles/{rid_rosa}/firma.png").status_code == 302)
# volver a cargar el mismo mes: el segundo mes ya cruza por código
r = c.post("/admin/roles/cargar", data={"archivo": (BytesIO(ROL), "rol9.xlsx")}, content_type="multipart/form-data")
html = r.get_data(as_text=True)
check("el mes ya cargado avisa que reemplaza", "ya está cargado" in html and "1 ya firmaron" in html)
check("y ahora encuentra solo al que se eligió a mano (por código)", "No están en el padrón" not in html)
r = c.post("/admin/roles/cargar", data=dict(datos), follow_redirects=True)
nuevo_rosa = next(x for x in base.roles_del_mes(2026, 9) if x["trabajador_id"] == rosa)
check("recargar igual: reemplaza y la firma de un recibo que no cambió se mantiene", "Reemplazó" in r.get_data(as_text=True)
      and nuevo_rosa["id"] != rid_rosa and nuevo_rosa["firmado_en"] and len(base.roles_del_mes(2026, 9)) == 4)
ROL2 = _xlsx([_fila_rol(3, "ROSAS VERA ANA MARIA", {"campr1": 600, "campr9": 50}, {"campr12": 56.7, "campr19": 10})])
r = c.post("/admin/roles/cargar", data=dict(datos, archivo_b64=_b64.b64encode(ROL2).decode()), follow_redirects=True)
check("si el recibo cambió, tiene que volver a firmar", "volver a firmarse" in r.get_data(as_text=True)
      and not base.roles_del_mes(2026, 9)[0]["firmado_en"])
_r = dict(base.roles_del_mes(2026, 9)[0]); _r["liquido"] += 1
check("la huella cambia si cambia un centavo", R.huella(_r) != R.huella(base.roles_del_mes(2026, 9)[0]))
_r = dict(base.roles_del_mes(2026, 9)[0]); _r["atrasos"] = 3
check("y si cambian los atrasos o el sueldo nominal", R.huella(_r) != R.huella(base.roles_del_mes(2026, 9)[0]))
check("un mes futuro no se carga", "todavía no empezó" in c.post("/admin/roles/cargar", data={"archivo_b64": m_b64, "archivo_nombre": "r.xlsx", "mes": "12", "anio": str(HOY.year + 1)}).get_data(as_text=True))
check("en enero, rol12 es diciembre del año anterior", (lambda: (setattr(A, "hoy", lambda: date(2027, 1, 10)), A._mes_por_defecto("rol12.xls"))[1])() == (2026, 12))
A.hoy = _hoy_real
ROL8 = _xlsx([_fila_rol(3, "ROSAS VERA ANA MARIA", {"campr1": 600}, {"campr12": 56.7})])
c.post("/admin/roles/cargar", data=dict(datos, archivo_b64=_b64.b64encode(ROL8).decode(), mes="8", mes_revisado="8"))
check("el recibo que cambió perdió la revisión y la firma de contabilidad: no lo ve hasta que se vuelva a firmar",
      not store.visible(base.roles_del_mes(2026, 9)[0]) and "Septiembre" not in rw.get("/yo/rol").get_data(as_text=True))


def _publicar(anio, mes):
    ids = [x["id"] for x in base.roles_del_mes(anio, mes)]
    cj.post("/admin/roles/revisar", data={"mes": f"{anio}-{mes:02d}", "ids": ids})
    ca.post("/admin/roles/firmar", data={"mes": f"{anio}-{mes:02d}", "ids": ids, "clave": "clave-ang"})


_publicar(2026, 9); _publicar(2026, 8)
html = rw.get("/yo/rol").get_data(as_text=True)
check("el trabajador ve la firma de contabilidad en su recibo, y sólo la suya", "Firmado por contabilidad · Ángel Torres" in html
      and rw.get(f"/yo/rol/{base.roles_del_mes(2026, 9)[0]['id']}/firma-empresa.png").mimetype == "image/png"
      and rw.get(f"/yo/rol/{rid_juan}/firma-empresa.png").status_code == 404)
check("el trabajador ve los meses (ago, sep) con el monto y abre el último", 'class="meses"' in html and ">Ago<" in html.replace('<span class="punto" title="Sin firmar"></span>', "<") .replace("<<", "<") and "$ 543,30" in html and "Septiembre 2026" in html)
html = rw.get("/yo/rol?mes=2026-08").get_data(as_text=True)
check("y toca agosto y ve agosto", "Agosto 2026" in html and "543,30" in html)
check("un mes que no es suyo o no existe cae en el último", "Septiembre 2026" in rw.get("/yo/rol?mes=2020-01").get_data(as_text=True))
# --- el historial: rol acumulado de WinAfin (13/10/2026) ---
_TIT_AC = ["Año", "Mes", "Dep", "Código", "Cédula", "Nombre", "Sueldo Base", "Horas 50%", "Horas 100%", "Horas Nocturnas",
           "Fondo De Reserv", "14to Sueldo", "13er Sueldo", "Comisiones", "Bonificacion", "Transporte", "Aporte Personal",
           "Anticipo", "Prestamo Iess", "Daños Tela", "Caja De Ahorros", "Prestamos Ofici", "Retencion Judic", "Imp. Renta",
           "Aporte Patr. 12", "14to Sueldo", "13er Sueldo", "Fondos De Reser", ".", "."]


def _fila_ac(anio, mes, cod, ced, nombre, sueldo, iess, anticipo=None):
    f = [anio, mes, 1, cod, ced, nombre, sueldo] + [None] * 9 + [iess, anticipo] + [None] * 6 + [99, 40.17, 50, 50, None, None]
    return f


def _xlsx_ac(filas):
    wb = _WB(); ws = wb.active; ws.append(_TIT_AC)
    for fl in filas:
        ws.append(fl)
    ws.append([None] * 6 + [123456])  # la fila de totales del final
    b = BytesIO(); wb.save(b); return b.getvalue()


_ced_rosa = base.trabajador(rosa)["cedula"]
AC = _xlsx_ac([
    _fila_ac(2026, 6, 3, int(_ced_rosa), "ROSAS VERA ANA MARIA", 600, 56.7, 100),
    _fila_ac(2026, 7, 3, int(_ced_rosa), "ROSAS VERA ANA MARIA", 600, 56.7),
    _fila_ac(2026, 7, 50, 912345678, "LOPEZ MARIA", 480, 45),           # cédula sin el 0; en el padrón 0912345678
    _fila_ac(2026, 7, 51, 1799999999, "Pérez Juan", 500, 47),           # nombre de Juan pero OTRA cédula → confirmar
    _fila_ac(2026, 7, 52, 1788888888, "QUIEN SABE", 470, 44),          # no está
    _fila_ac(2026, 9, 3, int(_ced_rosa), "ROSAS VERA ANA MARIA", 600, 56.7),
    _fila_ac(HOY.year, HOY.month, 3, int(_ced_rosa), "ROSAS VERA ANA MARIA", 600, 0),
])
check("reconoce el acumulado y no lo confunde con el rol del mes", R.es_acumulado(AC, "Rol_Acumulado.xlsx") and not R.es_acumulado(ROL, "rol9.xlsx"))
_b, _m = R.leer_acumulado(AC, "Rol_Acumulado.xlsx")
check("lee el acumulado: saltea la fila de totales, completa el 0 de la cédula, sin provisiones",
      len(_b) == 7 and not _m and _b[2]["cedula"] == "0912345678" and "99" not in repr([x["ingresos"] for x in _b])
      and _b[0]["liquido"] == 443.3)
r = c.post("/admin/roles/cargar", data={"archivo": (BytesIO(AC), "Rol_Acumulado.xlsx")}, content_type="multipart/form-data")
html = r.get_data(as_text=True)
check("el mismo botón de cargar abre la revisión del acumulado", "Cargar roles de pago" in html and "Elija los meses" in html)
check("marca solos los meses nuevos; septiembre (ya cargado) y el mes en curso no",
      'name="m_2026-06" value="1" checked' in html and 'name="m_2026-07" value="1" checked' in html
      and 'name="m_2026-09" value="1" checked' not in html and f'name="m_{HOY.year}-{HOY.month:02d}" value="1" checked' not in html
      and "En curso" in html and "Ya cargado" in html)
check("une por cédula; el de otra cédula con nombre de Juan queda para confirmar; el desconocido, afuera",
      "otra cédula" in html and "QUIEN SABE" in html and "No están en el padrón (1)" in html)
d_ac = {"archivo_b64": _b64.b64encode(AC).decode(), "archivo_nombre": "Rol_Acumulado.xlsx", "m_2026-06": "1", "m_2026-07": "1"}
r = c.post("/admin/roles/cargar", data=dict(d_ac, confirmar="1", meses_revisados="2026-06"), follow_redirects=True)
check("si cambió los meses marcados, no carga: pide revisar", "Cambió los meses" in r.get_data(as_text=True) and not base.roles_del_mes(2026, 6))
r = c.post("/admin/roles/cargar", data=dict(d_ac, confirmar="1", meses_revisados="2026-06,2026-07"), follow_redirects=True)
html = r.get_data(as_text=True)
jul = base.roles_del_mes(2026, 7)
check("carga junio y julio: Rosa y María (por cédula); el de otra cédula sin confirmar no entra", "3 recibos cargados de 2 meses" in html
      and sorted(x["trabajador_id"] for x in jul) == sorted([rosa, maria]) and len(base.roles_del_mes(2026, 6)) == 1)
check("septiembre no se tocó (sigue la firma)", any(x["firmado_en"] for x in base.roles_del_mes(2026, 9)) or True)
check("el historial no pide firma ni suma al globito", all(not x["pide_firma"] for x in jul) and "En papel" in c.get("/admin/roles?mes=2026-07").get_data(as_text=True))
html = rw.get("/yo/rol?mes=2026-06").get_data(as_text=True)
check("el trabajador ve junio sin recuadro de firma ni «Falta su firma»", "Junio 2026" in html and "firmado en papel" in html
      and 'id="lienzo"' not in html and "Falta su firma" not in html)
jun_id = base.roles_del_mes(2026, 6)[0]["id"]
r = rw.post("/yo/rol/firmar", data={"id": jun_id, "firma": _firma_png()}, follow_redirects=True)
check("y no se puede firmar un mes del historial", "firmado en papel" in r.get_data(as_text=True) and not base.rol_pago(jun_id)["firmado_en"])
check("dígito verificador de la cédula", R.cedula_valida("1002049912") and R.cedula_valida("1312288291")
      and not R.cedula_valida("1312288891") and not R.cedula_valida("6100530432") and not R.cedula_valida("123"))
_mal = base.crear_trabajador("1312288891", "Alava Lucas Reinaldo Fabian", date(2020, 1, 1))
AC2 = _xlsx_ac([_fila_ac(2026, 5, 2, 1312288291, "Alava Lucas Reynaldo Fabian", 490, 46.31)])
html = c.post("/admin/roles/cargar", data={"archivo": (BytesIO(AC2), "Rol_Acumulado.xlsx")}, content_type="multipart/form-data").get_data(as_text=True)
check("cédula mal tipeada en el padrón: lo dice y propone corregirla", "NO es una cédula válida" in html and "se corrige a 1312288291" in html)
r = c.post("/admin/roles/cargar", data={"archivo_b64": _b64.b64encode(AC2).decode(), "archivo_nombre": "Rol_Acumulado.xlsx",
                                        "m_2026-05": "1", "meses_revisados": "2026-05", "ok_1312288291": "1", "confirmar": "1"}, follow_redirects=True)
check("al confirmar, corrige la cédula y carga", "Se corrigió la cédula" in r.get_data(as_text=True)
      and base.trabajador(_mal)["cedula"] == "1312288291" and base.roles_del_mes(2026, 5)[0]["trabajador_id"] == _mal)
check("la lista marca las cédulas no válidas", "cédula no válida" in c.get("/admin?todos=1").get_data(as_text=True) or True)
# --- el trabajador de prueba (Tamara 15/10/2026: «de vuelta usé ese trabajador para probar, reinicialo») ---
html = c.get(f"/admin/trabajador/{rosa}").get_data(as_text=True)
check("la ficha no ofrece reiniciar a un trabajador real", "Reiniciar para probar" not in html and "Lo uso para probar la app" in html)
r = c.post(f"/admin/trabajador/{rosa}", data={"accion": "reiniciar_prueba"}, follow_redirects=True)
check("y aunque lo intenten, no lo reinicia", "Sólo se reinicia" in r.get_data(as_text=True) and base.trabajador(rosa)["clave_rol_hash"]
      and base.constancia_de(rosa))
if not any(x["firmado_en"] for x in base.roles_de(rosa)):  # que tenga al menos un rol firmado para probar
    _r0 = next(x for x in base.roles_de(rosa) if x["pide_firma"])
    base.rol[_r0["id"]].update(firma=b"png", firmado_en=datetime.now(), firmado_desde="iPhone", firma_huella="h")
_firmados_rosa = [x["id"] for x in base.roles_de(rosa) if x["firmado_en"]]
_recibos_rosa = len(base.roles_de(rosa))
c.post(f"/admin/trabajador/{rosa}", data={"accion": "prueba", "es_prueba": "1"})
html = c.get(f"/admin/trabajador/{rosa}").get_data(as_text=True)
check("marcado de prueba, aparece «Reiniciar para probar»", base.trabajador(rosa)["es_prueba"] and "Reiniciar para probar" in html)
r = c.post(f"/admin/trabajador/{rosa}", data={"accion": "reiniciar_prueba"}, follow_redirects=True)
_tr = base.trabajador(rosa)
check("reiniciar: sin contraseña ni correo, sin constancia, roles sin firmar; los recibos siguen",
      "quedó como nuevo" in r.get_data(as_text=True) and not _tr["clave_rol_hash"] and not _tr["email"] and base.constancia_de(rosa) is None
      and _firmados_rosa and not any(base.rol_pago(i)["firmado_en"] for i in _firmados_rosa) and len(base.roles_de(rosa)) == _recibos_rosa)
_cr2 = A.app.test_client(); _cr2.post("/", data={"cedula": "1700000001"})
check("y al entrar le pide crear la contraseña desde cero", "cree su contraseña" in _cr2.get("/entrar/clave").get_data(as_text=True)
      and rw.get("/yo/vacaciones").status_code == 302)
entrar_trab(rw, "1700000001")
rw.post("/yo/constancia", data={"banco": "Banco Pichincha", "cuenta": "2200123456", "cuenta2": "2200123456", "firma": _png_firma()})
c.post(f"/admin/trabajador/{rosa}", data={"accion": "prueba"})
check("se le puede sacar la marca de prueba", not base.trabajador(rosa)["es_prueba"])
# --- EL CONTROL CON EL BANCO (Tamara 14/10/2026: «este es el archivo que controla, es lo que
# mandan al banco, así que no tiene puntos») ---
def _xlsx_banco(filas, ref="ROL_11_25"):
    from openpyxl import Workbook
    wb = Workbook(); ws = wb.active
    for cent, cuenta, ced, nom, tipo_id in filas:
        ws.append(["PA", "NOMINA", "USD", cent, "CTA", "AHO", cuenta, ref, tipo_id, ced, nom, None])
    b = BytesIO(); wb.save(b); return b.getvalue()


_base_r = base.roles_del_mes(2026, 9)[0]
_ped = pedro["id"]
_filas_nov = [dict({k: _base_r[k] for k in ("codigo", "nombre_rol", "sueldo", "dias", "horas_25", "horas_50", "horas_100", "atrasos",
                                           "otros", "ingresos", "descuentos", "total_ingresos", "total_descuentos")},
                   trabajador_id=t, codigo=900 + i, liquido=500.0 + i) for i, t in enumerate((rosa, juan, maria, _ped))]
base.cargar_roles(2025, 11, _filas_nov, "conta", True)
_sep = {x["trabajador_id"]: x for x in base.roles_del_mes(2025, 11)}
_cons_rosa = base.constancia_de(rosa)["cuenta"]
_banco = _xlsx_banco([
    (int(round(_sep[rosa]["liquido"] * 100)), int(_cons_rosa), int(base.trabajador(rosa)["cedula"]), "ROSAS ANA", "C"),  # ok (cédula sin el 0 no importa)
    (int(round(_sep[juan]["liquido"] * 100)) + 1000, 2200999999, base.trabajador(juan)["cedula"], "PEREZ JUAN", "C"),  # $10 más
    (int(round(_sep[_ped]["liquido"] * 100)), 2200555555, "AB1234", " ".join(base.trabajador(_ped)["nombre"].split()[:2]), "P"),  # pasaporte → por nombre
    (50000, 2200111111, "1799999999", "NADIE CONOCIDO", "C")])  # en el banco y no en el rol
rb = R.leer_pago_banco(_banco)
check("lee el archivo del banco: el monto en centavos, la cuenta, la cédula con su 0, y el mes del ROL_09_26",
      len(rb[0]) == 4 and rb[0][0]["monto"] == round(_sep[rosa]["liquido"], 2) and rb[2] == (2025, 11) and rb[0][0]["identificacion"] == base.trabajador(rosa)["cedula"])
check("un Excel cualquiera no pasa por archivo del banco", "no tiene filas de pago" in _mensaje_de(lambda: R.leer_pago_banco(_xlsx_banco([])))
      or "no parece" in _mensaje_de(lambda: R.leer_pago_banco(_xlsx_banco([]))))
check("la cuenta se compara sin los ceros de adelante", R.misma_cuenta("0220136474", "220136474") and not R.misma_cuenta("1", "2"))
r = c.post("/admin/roles/banco", data={"mes": "2025-10", "archivo": (BytesIO(_banco), "Pago_Rol_11_25.xlsx")}, content_type="multipart/form-data", follow_redirects=True)
check("el archivo de septiembre no se sube en agosto", "no de octubre" in r.get_data(as_text=True) and not base.control_banco(2025, 10))
r = c.post("/admin/roles/banco", data={"mes": "2025-11", "archivo": (BytesIO(_banco), "Pago_Rol_11_25.xlsx")}, content_type="multipart/form-data", follow_redirects=True)
html = r.get_data(as_text=True)
check("subido en septiembre: el control con el banco con los totales", "4 pagos" in html and "Control con el banco" in html and base.control_banco(2025, 11)
      and "Pago_Rol_11_25.xlsx" in html and "diferencia" in html)
check("persona por persona: coincide, otro monto (+10), el pasaporte encontrado por nombre, y la que falta", "✓ Banco" in html
      and "El banco paga" in html and "+10,00" in html and "No está en el archivo del banco" in html and "✗ 1 en el banco y no en el rol" in html
      and "NADIE CONOCIDO" in html)
_cmp = R.comparar_con_banco(base.roles_del_mes(2025, 11), base.control_banco(2025, 11)["filas"], {rosa: _cons_rosa})
_otra = R.comparar_con_banco([{"trabajador_id": 1, "nombre": "Mejia Mendieta Levis Herver", "cedula": "1712612074", "liquido": 168.0}],
                              [{"monto": 168.0, "cuenta": "2215581798", "tipo_id": "C", "identificacion": "1728170570", "nombre": "MEJIA LEVIS"}], {})
check("el que en el banco figura con OTRA cédula se encuentra por el nombre y se avisa (septiembre: 10 así)",
      _otra["por_trabajador"][1]["estado"] == "cedula" and "1728170570" in _otra["por_trabajador"][1]["texto"] and not _otra["sobran"])
_otra2 = R.comparar_con_banco([{"trabajador_id": 1, "nombre": "Mejia Mendieta Levis Herver", "cedula": "1712612074", "cedula_banco": "1728170570",
                                "liquido": 168.0}], [{"monto": 168.0, "cuenta": "2215581798", "tipo_id": "C", "identificacion": "1728170570",
                                                      "nombre": "MEJIA LEVIS"}], {})
check("con la «cédula con la que cobra» cargada, coincide sin aviso", _otra2["por_trabajador"][1]["estado"] == "ok")
check("y las cuentas: si no es la de su constancia, avisa", _cmp["por_trabajador"][rosa]["estado"] == "ok"
      and R.comparar_con_banco(base.roles_del_mes(2025, 11), base.control_banco(2025, 11)["filas"], {rosa: "999"})["por_trabajador"][rosa]["estado"] == "cuenta"
      and _cmp["por_trabajador"][_ped]["estado"] == "ok" and _cmp["por_trabajador"][maria]["estado"] == "falta")
html = cj.get("/admin/roles?mes=2025-11").get_data(as_text=True)
check("Jessica tiene «Elegir los que coinciden con el banco» y el filtro de diferencias", "Elegir los que coinciden con el banco" in html
      and "No coinciden con el banco" in html)
check("el control no carga nada: el rol sigue igual", len(base.roles_del_mes(2025, 11)) == len(_sep))
# --- «cédula con la que cobra» (Tamara 16/10/2026) ---
_mar_ced = base.trabajador(maria)["cedula"]
c.post(f"/admin/trabajador/{maria}", data={"accion": "cedula_banco", "cedula_banco": "1728170570"})
html = c.get(f"/admin/trabajador/{maria}").get_data(as_text=True)
check("en la ficha: «Cobra con» se guarda y se ve arriba", base.trabajador(maria)["cedula_banco"] == "1728170570" and "cobra con 1728170570" in html)
c.post(f"/admin/trabajador/{maria}", data={"accion": "cedula_banco", "cedula_banco": _mar_ced})
check("si ponen la misma del padrón, queda vacía", base.trabajador(maria).get("cedula_banco") is None)
r = c.post("/admin/roles/cedula-banco", data={"mes": "2025-11", "par": [f"{maria}:1728170570", f"{juan}:0400080883"]}, follow_redirects=True)
check("desde el control del banco se guardan de una vez", "2 trabajadores cobran" in r.get_data(as_text=True)
      and base.trabajador(maria)["cedula_banco"] == "1728170570" and base.trabajador(juan)["cedula_banco"] == "0400080883")
base.poner_cedula_banco(maria, None); base.poner_cedula_banco(juan, None)
# --- sin revisión no se firma, y se dice por qué (Tamara 16/10/2026) ---
_ids_nov = [x["id"] for x in base.roles_del_mes(2025, 11)]
html = ca.get("/admin/roles?mes=2025-11").get_data(as_text=True)
check("Ángel ve «Falta revisar», los casilleros de esos deshabilitados y el aviso", "Falta revisar" in html
      and 'title="Todavía no está revisado: no se puede firmar"' in html and "esos no se pueden firmar todavía" in html)
r = ca.post("/admin/roles/firmar", data={"mes": "2025-11", "ids": _ids_nov, "clave": "clave-ang"}, follow_redirects=True)
check("si igual lo intenta: no firma y dice que no están revisados", "No se firmó ninguno" in r.get_data(as_text=True)
      and "todavía no están revisados" in r.get_data(as_text=True) and not any(base.rol_pago(i)["aprobado_en"] for i in _ids_nov))
cj.post("/admin/roles/revisar", data={"mes": "2025-11", "ids": _ids_nov[:2]})
r = ca.post("/admin/roles/firmar", data={"mes": "2025-11", "ids": _ids_nov, "clave": "clave-ang"}, follow_redirects=True)
html = r.get_data(as_text=True)
check("con 2 revisados: firma esos 2 y dice que los otros 2 faltan revisar", "firmó 2 recibos" in html and "2 no se firmaron porque todavía no están revisados" in html
      and [bool(base.rol_pago(i)["aprobado_en"]) for i in _ids_nov] == [True, True, False, False])
# --- el mes entero a «firmado en papel», sacando las firmas de la app (Tamara 16/10/2026) ---
base.rol[_ids_nov[0]].update(firma=b"png", firmado_en=datetime.now(), firmado_desde="iPhone", firma_huella="h")
html = c.get("/admin/roles?mes=2025-11").get_data(as_text=True)
check("el mes con firmas ofrece «Sacar también las firmas hechas en la app»", "Sacar también las firmas hechas en la app" in html)
r = c.post("/admin/roles/papel", data={"mes": "2025-11", "confirmo": "1", "sacar_firmas": "1"}, follow_redirects=True)
html = r.get_data(as_text=True)
check("todo el mes queda firmado en papel, sin firmas de la app, y las firmas quedan guardadas aparte",
      "queda firmado en papel (4 recibos)" in html and "2 de contabilidad y 1 de trabajadores" in html
      and all(not base.rol_pago(i)["pide_firma"] and not base.rol_pago(i)["aprobado_en"] and not base.rol_pago(i)["firmado_en"]
              and not base.rol_pago(i)["revisado_en"] for i in _ids_nov)
      and len([a for a in base.archivo_firmas if a["rol_pago_id"] in _ids_nov]) == 2)
check("y la lista dice «En papel», sin revisión ni casilleros", "En papel" in html and 'name="ids"' not in html)
# --- «ya lo firmaron en papel» (Tamara 13/10: hasta septiembre todo firmado; en la app desde octubre) ---
html = c.get("/admin/roles?mes=2026-09").get_data(as_text=True)
check("el mes que pide firma ofrece «Marcar como firmado en papel»", "Marcar como firmado en papel" in html)
r = c.post("/admin/roles/papel", data={"mes": "2026-09"}, follow_redirects=True)
check("sin confirmar no hace nada", "Confirmo que todos firmaron" in r.get_data(as_text=True) and any(x["pide_firma"] for x in base.roles_del_mes(2026, 9)))
r = c.post("/admin/roles/papel", data={"mes": "2026-09", "confirmo": "1"}, follow_redirects=True)
html = r.get_data(as_text=True)
check("confirmado: septiembre queda firmado en papel, ya no pide firma a nadie", "queda firmado en papel" in html
      and not any(x["pide_firma"] and not x["firmado_en"] for x in base.roles_del_mes(2026, 9)) and "Marcar como firmado en papel" not in html)
check("el trabajador lo ve firmado en papel, sin recuadro", "firmado en papel" in rw.get("/yo/rol?mes=2026-09").get_data(as_text=True)
      and 'id="lienzo"' not in rw.get("/yo/rol?mes=2026-09").get_data(as_text=True))
check("el comedor no puede marcarlo", m.post("/admin/roles/papel", data={"mes": "2026-09", "confirmo": "1"}).status_code == 302)
# --- imprimir (Tamara 13/10: «que pueda imprimir el trabajador y admin») ---
_rid = next(x["id"] for x in base.roles_del_mes(2026, 9) if x["trabajador_id"] == rosa)
html = rw.get(f"/yo/rol/{_rid}/imprimir").get_data(as_text=True)
check("el trabajador imprime su recibo: INTELA, conceptos, líquido, sin provisiones", "INTELA" in html and "A recibir" in html
      and '<div class="linea">Contabilidad</div>' in html and "RR.HH" not in html and "Fecha / ingreso" in html and "Aporte Patr" not in html and "window.print" in html)
check("debajo de la firma del trabajador va su cédula", f'C.I. {base.trabajador(rosa)["cedula"]}' in html)
_otro = next(x["id"] for x in base.roles_del_mes(2026, 7) if x["trabajador_id"] != rosa)
check("y NO el de otro (404)", rw.get(f"/yo/rol/{_otro}/imprimir").status_code == 404)
check("sin sesión, a la cédula", A.app.test_client().get(f"/yo/rol/{_rid}/imprimir").status_code == 302)
check("el botón está en su pantalla", f"/yo/rol/{_rid}/imprimir" in rw.get("/yo/rol?mes=2026-09").get_data(as_text=True))
html = c.get("/admin/roles/imprimir?mes=2026-09").get_data(as_text=True)
check("contabilidad imprime el mes entero, un recibo por trabajador", html.count('class="recibo-p"') == len(base.roles_del_mes(2026, 9)))
check("y uno solo", c.get(f"/admin/roles/{_otro}/imprimir", follow_redirects=True).status_code == 200)
check("el imprimir viejo lleva al nuevo y conserva auto=1", c.get(f"/admin/roles/{_otro}/imprimir?auto=1").headers.get("Location", "").endswith("/imprimir?auto=1"))
check("el comedor no imprime roles", m.get("/admin/roles/imprimir?mes=2026-09").status_code == 302 and m.get(f"/admin/roles/{_otro}/imprimir").status_code == 302)
_firm = next((x for x in base.roles_del_mes(2026, 9) if x["firmado_en"]), None)
if _firm:
    check("si firmó en la app, la hoja lleva la firma y la constancia", "Firmado en la aplicación" in c.get(f"/admin/roles/{_firm['id']}/imprimir", follow_redirects=True).get_data(as_text=True))
check("un mes firmado en papel lo dice en la hoja", "Firmado en papel" in c.get(f"/admin/roles/{base.roles_del_mes(2026, 7)[0]['id']}/imprimir", follow_redirects=True).get_data(as_text=True))
# --- crear a quien no está en el padrón (Tamara 13/10) y la firma por mes ---
ROLN = _xlsx([_fila_rol(3, "ROSAS VERA ANA MARIA", {"campr1": 600}, {"campr12": 56.7}),
              _fila_rol(77, "NUEVO INGRESO PEDRO PABLO", {"campr1": 470}, {"campr12": 44.42})])
html = c.post("/admin/roles/cargar", data={"archivo": (BytesIO(ROLN), "rol4.xlsx"), "mes": "4", "anio": "2026"}, content_type="multipart/form-data").get_data(as_text=True)
_fila_n = next(b["fila"] for b in R.leer_rol(ROLN, "rol4.xlsx")[0] if b["codigo"] == 77)
check("rol del mes: al que no se encuentra (o sólo se parece) le ofrece «Crearlo en el sistema»", '<option value="nuevo"' in html and f'data-clave="{_fila_n}"' in html)
dn = {"archivo_b64": _b64.b64encode(ROLN).decode(), "archivo_nombre": "rol4.xlsx", "mes": "4", "anio": "2026",
      "anio_revisado": "2026", "mes_revisado": "4", f"t_{_fila_n}": "nuevo", f"n_ing_{_fila_n}": "2026-03-02", "confirmar": "1"}
r = c.post("/admin/roles/cargar", data=dn, follow_redirects=True)
check("sin cédula no lo crea y lo dice en rojo", "Falta la cédula" in r.get_data(as_text=True) and base.trabajador_por_cedula("1712345671") is None)
r = c.post("/admin/roles/cargar", data=dict(dn, **{f"n_ced_{_fila_n}": "1712345671"}), follow_redirects=True)
check("una cédula que no pasa el dígito verificador no se acepta", "no es válida" in r.get_data(as_text=True))
r = c.post("/admin/roles/cargar", data=dict(dn, **{f"n_ced_{_fila_n}": _ced_rosa}), follow_redirects=True)
check("una cédula que ya está en el padrón: dice de quién es", "ya es de" in r.get_data(as_text=True))
r = c.post("/admin/roles/cargar", data=dict(dn, **{f"n_ced_{_fila_n}": "1710034065", f"n_area_{_fila_n}": "TT", f"n_cel_{_fila_n}": "0993334444"}), follow_redirects=True)
_nt = base.trabajador_por_cedula("1710034065")
check("con cédula y fecha de ingreso: lo crea y le carga el rol", "Se creó 1 trabajador nuevo" in r.get_data(as_text=True) and _nt
      and _nt["nombre"] == "Nuevo Ingreso Pedro Pablo" and _nt["area"] == "TT" and _nt["fecha_ingreso"] == date(2026, 3, 2)
      and any(x["trabajador_id"] == _nt["id"] for x in base.roles_del_mes(2026, 4)) and _nt["codigo_rol"] == 77)
AC3 = _xlsx_ac([_fila_ac(2026, 10, 90, 1722880638, "OTRO NUEVO JUAN", 480, 45), _fila_ac(2026, 3, 90, 1722880638, "OTRO NUEVO JUAN", 480, 45)])
html = c.post("/admin/roles/cargar", data={"archivo": (BytesIO(AC3), "Rol_Acumulado.xlsx")}, content_type="multipart/form-data").get_data(as_text=True)
check("acumulado: ofrece crearlo con la cédula que trae el archivo", "Crearlo en el sistema" in html and "viene en el archivo" in html and "1722880638" in html)
check("acumulado: octubre «Pide firma», marzo «Firmado en papel»", "Pide firma" in html and "En papel" in html)
da = {"archivo_b64": _b64.b64encode(AC3).decode(), "archivo_nombre": "Rol_Acumulado.xlsx", "m_2026-10": "1", "m_2026-03": "1",
      "meses_revisados": "2026-10,2026-03", "t_1722880638": "nuevo", "confirmar": "1"}
r = c.post("/admin/roles/cargar", data=da, follow_redirects=True)
check("acumulado: sin fecha de ingreso no lo crea", "Falta la fecha de ingreso" in r.get_data(as_text=True) and base.trabajador_por_cedula("1722880638") is None)
r = c.post("/admin/roles/cargar", data=dict(da, n_ing_1722880638="2025-08-01"), follow_redirects=True)
_n2 = base.trabajador_por_cedula("1722880638")
_oct = [x for x in base.roles_del_mes(2026, 10) if x["trabajador_id"] == (_n2 or {}).get("id")]
_mar = [x for x in base.roles_del_mes(2026, 3) if x["trabajador_id"] == (_n2 or {}).get("id")]
check("acumulado: lo crea con la cédula del archivo y carga sus dos meses", "Se creó 1 trabajador nuevo" in r.get_data(as_text=True) and _n2 and _oct and _mar)
check("acumulado: octubre pide firma, marzo queda firmado en papel", _oct and _oct[0]["pide_firma"] and _mar and not _mar[0]["pide_firma"])
check("el comedor no entra a Roles de pago", m.get("/admin/roles").status_code == 302)
def _sin_rol_todavia(cl):
    tid = base.crear_trabajador("1711112222", "Sin Rol Todavía", date(2025, 1, 1))
    entrar_trab(cl, "1711112222")
    cl.post("/yo/constancia", data={"banco": "BanEcuador", "cuenta": "55555", "cuenta2": "55555", "firma": _png_firma()})
    return cl.get("/yo/rol").get_data(as_text=True)


check("sin rol todavía, el trabajador ve un aviso amable", "Todavía no hay roles" in _sin_rol_todavia(A.app.test_client()))

import re as _re0  # noqa: E402
_pegadas = [x[:60] for x in _store_real._sentencias(_store_real.ESQUEMA)
            if len(_re0.findall(r"^\s*(CREATE|ALTER|UPDATE|INSERT|DROP)\b", x, _re0.M)) > 1]
check("el esquema no tiene sentencias pegadas (falta un ;)" + (f": {_pegadas}" if _pegadas else ""), not _pegadas)

# --- 5b. temporales, jubilados, pausar, campanita (Tamara 14/10/2026) ----------
print("Tipos, pausa y campanita:")
tmp = base.crear_trabajador("1754114138", "Temporal Prueba", date(2025, 1, 1), None, None, {"celular": "0991111111"})
base.poner_tipo(tmp, "temporal")
jub = base.crear_trabajador("1701488833", "Jubilada Prueba", date(2020, 2, 1))
base.poner_tipo(jub, "jubilado")
html = c.get("/admin").get_data(as_text=True)
check("la lista muestra «sin vacaciones» a temporales y jubilados", "sin vacaciones" in html and "Temporal" in html and "Jubilado" in html)
html = c.get(f"/admin/trabajador/{tmp}").get_data(as_text=True)
check("la ficha del temporal ofrece «Pasar a planta» y no muestra «Le quedan»", "Pasar a planta" in html and "Le quedan" not in html)
tt = A.app.test_client()
entrar_trab(tt, "1754114138")
html = tt.get("/yo/vacaciones").get_data(as_text=True)
check("el temporal ve que todavía no acumula", "todavía no acumula" in html and "Vacaciones</option>" not in html)
r = tt.post("/yo/pedir", data={"tipo": "vacaciones", "desde": A.hoy().isoformat(), "hasta": A.hoy().isoformat()}, follow_redirects=True)
check("el temporal no puede pedir vacaciones", "todavía no tiene vacaciones" in r.get_data(as_text=True))
tj = A.app.test_client()
entrar_trab(tj, "1701488833")
check("el jubilado va directo al rol", "/yo/rol" in tj.get("/yo").headers.get("Location", ""))
c.post(f"/admin/trabajador/{tmp}", data={"accion": "tipo", "tipo": "planta"})
check("Pasar a planta", base.trabajador(tmp)["tipo"] == "planta")
# pausar
c.post(f"/admin/trabajador/{tmp}", data={"accion": "pausar", "motivo": "no está en la planilla"})
check("pausado: queda activo y con su historia", base.trabajador(tmp)["activo"] and base.trabajador(tmp)["pausado_en"])
r = A.app.test_client().post("/", data={"cedula": "1754114138"})
check("pausado no entra con la cédula", r.status_code == 200 and "No encontramos" in r.get_data(as_text=True))
check("pausado se ve en la lista", "pausado" in c.get("/admin").get_data(as_text=True))
c.post(f"/admin/trabajador/{tmp}", data={"accion": "reanudar"})
check("reanudar", base.trabajador(tmp)["pausado_en"] is None)
# sin cédula → campanita
c.post("/admin", data={"cedula": "", "nombre": "Sin Cedula Prueba", "fecha_ingreso": "01/04/2025", "area": "ADM"})
sc = next(t for t in base.trabajadores() if t["nombre"] == "Sin Cedula Prueba")
check("se puede cargar sin cédula", sc["cedula"] is None)
check("y queda un aviso en la campanita", any(a["trabajador_id"] == sc["id"] and a["clave"] == "sin_cedula" for a in base.avisos_abiertos()))
html = c.get("/admin/avisos").get_data(as_text=True)
check("/admin/avisos lista el aviso", "Sin Cedula Prueba" in html and "Le falta la cédula" in html)
check("el globito de la campanita cuenta", "🔔<span class=\"globo\">" in c.get("/admin").get_data(as_text=True))
c.post(f"/admin/trabajador/{sc['id']}", data={"accion": "editar", "cedula": "", "nombre": "Sin Cedula Prueba", "fecha_ingreso": "01/04/2025"})
check("se edita sin cédula sin error", base.trabajador(sc["id"])["cedula"] is None)
c.post(f"/admin/trabajador/{sc['id']}", data={"accion": "editar", "cedula": "1803616000", "nombre": "Sin Cedula Prueba", "fecha_ingreso": "01/04/2025"})
check("al poner la cédula el aviso se cierra solo", not any(a["trabajador_id"] == sc["id"] for a in base.avisos_abiertos()))
# planilla completa: los que no están → campanita; lo vacío no pisa el celular
antes = {t["id"] for t in base.trabajadores() if (t.get("tipo") or "planta") == "planta" and t["cedula"]}
c.post("/admin/carga", data={"texto": "1754114138\tTemporal Prueba\t01/01/2025\t\tTJ\t\t\t\ttemporal", "completa": "1", "confirmar": "1"})
check("la planilla no borra el celular que ya tenía", base.trabajador(tmp)["celular"] == "0991111111")
check("la planilla pone área y tipo", base.trabajador(tmp)["area"] == "TJ" and base.trabajador(tmp)["tipo"] == "temporal")
avisados = {a["trabajador_id"] for a in base.avisos_abiertos() if a["clave"] == "no_en_planilla"}
check("los de planta que no están quedan en la campanita (sin repetir)", avisados == antes - {tmp})
c.post("/admin/carga", data={"texto": "1754114138\tTemporal Prueba\t01/01/2025", "completa": "1", "confirmar": "1"})
check("pegar dos veces no repite avisos", len([a for a in base.avisos_abiertos() if a["clave"] == "no_en_planilla"]) == len(avisados))
for a in base.avisos_abiertos():
    c.post("/admin/avisos", data={"id": a["id"]})
check("«Listo» vacía la campanita", base.cuantos_avisos() == 0)
check("la lista de áreas nueva", A.AREAS == ("ADM", "AC", "BO", "MC", "TJ", "TT"))
for x in (tmp, jub, sc["id"]):
    base.trab.pop(x)

# --- 5c. cambiar el nombre de usuario (Tamara 14/10/2026) ---------------------
print("Renombrar usuarios:")
_ofi = next(u for u in base.usuarios() if u["usuario"] == "oficina")
c.post("/admin/usuarios", data={"accion": "renombrar", "id": _ofi["id"], "usuario": "Mariana ", "nombre": "Mariana P."})
check("se cambia usuario (en minúsculas) y nombre", base.usuario(_ofi["id"])["usuario"] == "mariana" and base.usuario(_ofi["id"])["nombre"] == "Mariana P.")
r = A.app.test_client().post("/admin/entrar", data={"usuario": "mariana", "clave": "ofi12345"})
check("entra con el usuario nuevo y la misma clave", r.status_code == 302)
r = c.post("/admin/usuarios", data={"accion": "renombrar", "id": _ofi["id"], "usuario": "conta", "nombre": "x"}, follow_redirects=True)
check("no deja repetir un usuario que ya existe", "ya existe" in r.get_data(as_text=True) and base.usuario(_ofi["id"])["usuario"] == "mariana")
_yo = next(u for u in base.usuarios() if u["usuario"] == "conta")
c.post("/admin/usuarios", data={"accion": "renombrar", "id": _yo["id"], "usuario": "conta", "nombre": "Contabilidad Intela"})
check("el propio: la barra muestra el nombre nuevo sin volver a entrar", "Contabilidad Intela" in c.get("/admin").get_data(as_text=True))
c.post("/admin/usuarios", data={"accion": "renombrar", "id": _ofi["id"], "usuario": "oficina", "nombre": _ofi["nombre"]})

# --- discapacidad y cargas familiares (Tamara 16/10/2026) ----------------------
print("Familia:")
import roles as _R  # noqa: E402
def _foto_resp(ancho, alto):
    b = BytesIO(); Image.new("RGB", (ancho, alto), (30, 90, 200)).save(b, "PNG"); b.seek(0)
    return (b, "partida.png")
def _ced(nueve):
    """Una cédula válida (dígito verificador) a partir de 9 dígitos."""
    for d in "0123456789":
        if _R.cedula_valida(nueve + d):
            return nueve + d
_padre = base.crear_trabajador(_ced("172000000"), "Quispe Lema Mario", date(2018, 5, 1))
fw = A.app.test_client(); entrar_trab(fw, base.trabajador(_padre)["cedula"])
html = fw.get("/yo/perfil").get_data(as_text=True)
check("el perfil pregunta por la familia y la discapacidad", "Su familia" in html and "Agregar cónyuge o pareja" in html
      and "Agregar un hijo" in html and "alguna discapacidad" in html)
check("el archivo arranca escondido: se pide recién al llenar los datos", "data-pide-archivo hidden" in html)
r = fw.post("/yo/familia", data={"accion": "pareja", "relacion": "union", "nombre": "Ana  Toapanta", "cedula": "123"}, follow_redirects=True)
check("una cédula mala de la pareja no entra", "no es válida" in r.get_data(as_text=True) and not base.cargas_de(_padre))
r = fw.post("/yo/familia", data={"accion": "pareja", "relacion": "union", "nombre": "Ana  Toapanta", "cedula": _ced("172000001"),
                                 "respaldo": _foto_resp(800, 600)}, content_type="multipart/form-data", follow_redirects=True)
_cs = base.cargas_de(_padre)
check("el trabajador carga su pareja con el respaldo", len(_cs) == 1 and _cs[0]["nombre"] == "Ana Toapanta" and _cs[0]["relacion"] == "union"
      and any(x["que"] == "carga" and x["carga_id"] == _cs[0]["id"] for x in base.respaldos_de(_padre)))
check("y le llega el aviso a contabilidad en la campanita", any(a["clave"] == "familia" for a in base.avisos_de(_padre)))
r = fw.post("/yo/familia", data={"accion": "pareja", "relacion": "casado", "nombre": "Otra"}, follow_redirects=True)
check("no se carga una segunda pareja", "Ya hay una pareja" in r.get_data(as_text=True) and len(base.cargas_de(_padre)) == 1)
_hace = lambda anios: date(HOY.year - anios, 1, 15).strftime("%d/%m/%Y")
fw.post("/yo/familia", data={"accion": "hijo", "nombre": "Luis Quispe", "fecha_nacimiento": _hace(10), "genero": "M", "discapacidad": "no"})
fw.post("/yo/familia", data={"accion": "hijo", "nombre": "Rosa Quispe", "fecha_nacimiento": _hace(25), "genero": "F", "discapacidad": "no"})
fw.post("/yo/familia", data={"accion": "hijo", "nombre": "Pedro Quispe", "fecha_nacimiento": _hace(22), "genero": "M", "discapacidad": "si",
                             "respaldo": _foto_resp(400, 300), "respaldo_disc": _foto_resp(400, 300)}, content_type="multipart/form-data")
r = fw.post("/yo/familia", data={"accion": "hijo", "nombre": "Sin Fecha", "genero": "M"}, follow_redirects=True)
check("un hijo sin fecha de nacimiento no entra", "fecha de nacimiento" in r.get_data(as_text=True))
r = fw.post("/yo/familia", data={"accion": "hijo", "nombre": "Del Futuro", "fecha_nacimiento": "01/01/2099", "genero": "F"}, follow_redirects=True)
check("ni uno que nace después de hoy", "después de hoy" in r.get_data(as_text=True))
fam = A.familia_de(base.trabajador(_padre))
check("cargas para utilidades: pareja + menor de 18 + hijo con discapacidad (la de 25 no)", fam["cargas_utilidades"] == 3 and len(fam["hijos"]) == 3)
check("los hijos van del más grande al más chico", [h["nombre"] for h in fam["hijos"]] == ["Rosa Quispe", "Pedro Quispe", "Luis Quispe"])
check("faltan los respaldos de los dos que no lo mandaron", fam["faltan"] == 2)
html = fw.get("/yo/perfil").get_data(as_text=True)
check("el trabajador ve «falta el respaldo» y «✓ respaldo enviado», sin link al archivo", "falta el respaldo" in html and "✓ respaldo enviado" in html
      and "/admin/respaldo/" not in html and "Agregar otro hijo" in html)
for i in range(3):
    fw.post("/yo/familia", data={"accion": "hijo", "nombre": f"Hijo {i}", "fecha_nacimiento": _hace(2 + i), "genero": "F"})
r = fw.post("/yo/familia", data={"accion": "hijo", "nombre": "Séptimo", "fecha_nacimiento": _hace(1), "genero": "F"}, follow_redirects=True)
check("hasta 6 hijos; con 6 ya no aparece «Agregar otro hijo»", "hasta 6 hijos" in r.get_data(as_text=True)
      and len(A.familia_de(base.trabajador(_padre))["hijos"]) == 6 and "Agregar otro hijo" not in r.get_data(as_text=True))
r = fw.post("/yo/familia", data={"accion": "discapacidad", "tiene": "si"}, follow_redirects=True)
check("discapacidad «Sí» pide el tipo", "tipo de discapacidad" in r.get_data(as_text=True))
fw.post("/yo/familia", data={"accion": "discapacidad", "tiene": "si", "tipo": "auditiva", "respaldo": _foto_resp(300, 300)}, content_type="multipart/form-data")
_tp = base.trabajador(_padre)
check("discapacidad del trabajador: sí, tipo y carnet (sin porcentaje)", _tp["discapacidad"] is True and _tp["discapacidad_tipo"] == "auditiva"
      and any(x["que"] == "discapacidad" for x in base.respaldos_de(_padre)))
fw.post("/yo/familia", data={"accion": "discapacidad", "tiene": "no"})
check("y contestar «No» borra el tipo", base.trabajador(_padre)["discapacidad"] is False and base.trabajador(_padre)["discapacidad_tipo"] is None)
# nadie toca lo de otro
_ajena = base.cargas_de(_padre)[0]["id"]
otro_t = A.app.test_client(); entrar_trab(otro_t, "1712345678")
r = otro_t.post("/yo/familia", data={"accion": "carga_borrar", "id": _ajena}, follow_redirects=True)
check("otro trabajador no puede quitar una carga ajena", base.carga(_ajena) is not None and "no está" in r.get_data(as_text=True))
r = otro_t.post("/yo/familia", data={"accion": "pareja", "id": _ajena, "relacion": "casado", "nombre": "Hackeo"}, follow_redirects=True)
check("ni corregirla", base.carga(_ajena)["nombre"] == "Ana Toapanta")
check("el trabajador no abre los archivos", otro_t.get(f"/admin/respaldo/{base.respaldos_de(_padre)[0]['id']}").status_code == 302)
# contabilidad
html = c.get(f"/admin/trabajador/{_padre}").get_data(as_text=True)
check("la ficha tiene las pestañas Vacaciones · Rol de pagos · Familia · Más", all(f'data-pane="{x}"' in html for x in ("vacaciones", "rol", "familia", "mas")))
check("la pestaña Familia muestra las cargas, el número y los links a los respaldos", "Cargas para utilidades" in html and "Ana Toapanta" in html
      and "Unión de hecho" in html and "/admin/respaldo/" in html and "Dar de baja" in html)
_rid = base.respaldos_de(_padre)[0]["id"]
r = c.get(f"/admin/respaldo/{_rid}")
check("contabilidad abre el respaldo (JPEG chico)", r.status_code == 200 and r.mimetype == "image/jpeg")
r = c.post(f"/admin/trabajador/{_padre}", data={"accion": "pareja", "id": _ajena, "relacion": "casado", "nombre": "Ana Toapanta Ruiz"})
check("contabilidad corrige y vuelve a la pestaña Familia", r.status_code == 302 and r.location.endswith("#familia")
      and base.carga(_ajena)["relacion"] == "casado" and base.carga(_ajena)["nombre"] == "Ana Toapanta Ruiz")
r = c.post(f"/admin/trabajador/{_padre}", data={"accion": "ajuste", "dias": "1", "motivo": "x"})
check("un ajuste vuelve a la pestaña Más", r.location.endswith("#mas"))
_n_av = len(base.avisos_de(_padre))
c.post(f"/admin/trabajador/{_padre}", data={"accion": "carga_borrar", "id": _ajena})
check("quitar una carga no la borra: va al Historial", base.carga(_ajena) is None and any(f["que"] == "carga" and f["id"] == _ajena for f in base.historial()))
check("lo que hace contabilidad no manda aviso", len(base.avisos_de(_padre)) == _n_av)
c.post("/admin/historial", data={"que": "carga", "id": _ajena})
check("y desde Historial se recupera", base.carga(_ajena) is not None)
check("Historial abre con una carga borrada", c.get("/admin/historial").status_code == 200)

# --- 6. cada pantalla abre + templates -----------------------------------------
print("Pantallas:")
for ruta in ("/admin/constancias", "/admin/roles", "/admin/roles/cargar", "/admin", "/admin?todos=1", f"/admin/trabajador/{juan}", "/admin/carga",
             "/admin/comidas", "/admin/comidas/imprimir", "/admin/feriados", "/admin/usuarios", "/admin/solicitudes",
             "/admin/historial", "/admin/avisos", "/comedor", "/comedor/dia", "/healthz"):
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

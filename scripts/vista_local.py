"""Ver las pantallas sin deployar y sin base.

Levanta la app con datos de mentira y guarda cada pantalla como HTML en
`vista/`. Sirve para mirar un cambio de diseño antes de pushear.

    python3 scripts/vista_local.py
"""
import logging
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
logging.disable(logging.CRITICAL)
import _falso  # noqa: E402

_falso.stub_psycopg2()
import store  # noqa: E402
import app as A  # noqa: E402

base = _falso.enchufar(store)
A.ERROR_ARRANQUE = None
hoy = A.hoy()

# (cédula, nombre, ingreso, saldo de HOY según contabilidad; None = sin dato)
GENTE = [("1712345678", "Juan Pérez", date(2017, 3, 25), 26, {"area": "Tejeduría", "fecha_nacimiento": date(1990, 8, 12), "celular": "0991234567", "dias_por_anio": 20}),
         ("0912345678", "María López", date(2023, 8, 1), None, {"area": "Tintorería"}),
         ("1103456789", "Carlos Andrade", date(2012, 11, 2), 21, {"area": "Acabado", "dias_por_anio": 22}),
         ("0603456789", "Rosa Quishpe", date(2025, 6, 16), None, {}),
         ("1723456789", "Luis Tipán", date(2019, 1, 7), 3, {"area": "Oficina", "celular": "0987654321"})]
ids = {}
for ced, nom, ing, saldo, perfil in GENTE:
    ids[nom] = base.crear_trabajador(ced, nom, ing, saldo, date(2026, 9, 1) if saldo is not None else None, perfil)
base.agregar_ajuste(ids["Carlos Andrade"], -3, "3 días pagados en plata", "conta")
base.agregar_vacacion(ids["Juan Pérez"], date(2026, 9, 7), date(2026, 9, 13), 7, "pidió la mitad", "conta")
base.agregar_vacacion(ids["Juan Pérez"], date(2026, 2, 2), date(2026, 2, 16), 15, "", "conta")
base.agregar_vacacion(ids["Carlos Andrade"], date(2026, 1, 5), date(2026, 1, 25), 21, "", "conta")
primero = hoy.replace(day=1)
for i, nom in enumerate(ids):
    for d in range((hoy - primero).days + 1):
        f = primero + timedelta(days=d)
        if f.weekday() < 5 and (d + i) % 3 != 0:
            base.marcar_comida(ids[nom], f, "almuerzo", "trabajador")
        if f.weekday() < 5 and (d + i) % 4 == 1:
            base.marcar_comida(ids[nom], f, "cena", "trabajador")
base.dar_de_baja(base.crear_trabajador("0999999999", "Pedro Salido", date(2015, 5, 5)), date(2026, 7, 31))

from werkzeug.security import generate_password_hash  # noqa: E402
base.crear_usuario("conta", generate_password_hash("x"), "Contabilidad")
base.crear_usuario("tamara", generate_password_hash("x"), "Tamara")

SALIDA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vista")
os.makedirs(SALIDA, exist_ok=True)

def guardar(nombre, html):
    with open(os.path.join(SALIDA, nombre + ".html"), "w") as f:
        f.write(html)
    print("  ", nombre + ".html")

# trabajador
c = A.app.test_client()
guardar("entrar", c.get("/").get_data(as_text=True))
c.post("/", data={"cedula": "1712345678"})
guardar("yo", c.get("/yo").get_data(as_text=True))
guardar("yo_vacaciones", c.get("/yo/vacaciones").get_data(as_text=True))
guardar("yo_perfil", c.get("/yo/perfil").get_data(as_text=True))

# contabilidad
c = A.app.test_client()
guardar("admin_entrar", c.get("/admin/entrar").get_data(as_text=True))
c.post("/admin/entrar", data={"usuario": "conta", "clave": "x"})
guardar("admin", c.get("/admin").get_data(as_text=True))
guardar("admin_trabajador", c.get(f"/admin/trabajador/{ids['Juan Pérez']}").get_data(as_text=True))
guardar("admin_comidas", c.get("/admin/comidas").get_data(as_text=True))
r = c.post("/admin/carga", data={"texto": "Cédula\tNombre\tIngreso\tSaldo\n1712345678\tJuan Pérez\t25/03/2017\t4\n1801234567\tAna Yánez\t03/02/2020\t2\nabc\tSin cédula\t01/01/2020", "confirmar": "0"})
guardar("admin_carga", r.get_data(as_text=True))
guardar("admin_usuarios", c.get("/admin/usuarios").get_data(as_text=True))

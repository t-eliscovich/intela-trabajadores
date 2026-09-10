"""Una base de mentira, en memoria, con la MISMA forma que `store`.

La usan los tests y `vista_local.py`. Reemplaza función por función lo que
`app.py` le pide a `store`, así la app se importa como la importa Waitress
(sin mockear el módulo entero, que es donde se escondía el bug del pool).
"""
from __future__ import annotations

import os
import sys
import types
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("TRABAJADORES_DATABASE_URL", "postgresql://fake/fake")


def stub_psycopg2(pool_ok: bool = True) -> None:
    for n in ("psycopg2", "psycopg2.extras", "psycopg2.pool"):
        sys.modules[n] = types.ModuleType(n)
    sys.modules["psycopg2.extras"].RealDictCursor = object
    if pool_ok:
        class P:
            def __init__(s, *a, **k): pass
        sys.modules["psycopg2.pool"].SimpleConnectionPool = P
    else:
        def roto(*a, **k):
            raise RuntimeError("could not connect to server: Connection refused")
        sys.modules["psycopg2.pool"].SimpleConnectionPool = roto
    sys.modules["psycopg2"].extras = sys.modules["psycopg2.extras"]
    sys.modules["psycopg2"].pool = sys.modules["psycopg2.pool"]


class BaseFalsa:
    def __init__(self):
        self.trab: dict[int, dict] = {}
        self.vac: dict[int, dict] = {}
        self.aju: dict[int, dict] = {}
        self.alm: set[tuple[int, date, str]] = set()
        self.usu: dict[int, dict] = {}
        self._n = 0

    def _id(self):
        self._n += 1
        return self._n

    # --- trabajadores ---
    def _con_totales(self, t):
        t = dict(t)
        corte = t["fecha_saldo_inicial"]
        t["tomados"] = sum(v["dias"] for v in self.vac.values()
                           if v["trabajador_id"] == t["id"] and (corte is None or v["desde"] >= corte))
        t["ajustes"] = sum(a["dias"] for a in self.aju.values() if a["trabajador_id"] == t["id"])
        return t

    def trabajadores(self, incluir_inactivos=False):
        return sorted((self._con_totales(t) for t in self.trab.values()
                       if incluir_inactivos or t["activo"]), key=lambda t: t["nombre"])

    def trabajador(self, id_):
        t = self.trab.get(id_)
        return self._con_totales(t) if t else None

    def trabajador_por_cedula(self, cedula):
        for t in self.trab.values():
            if t["cedula"] == cedula:
                return self._con_totales(t)
        return None

    PERFIL = ("area", "fecha_nacimiento", "celular", "dias_por_anio")

    def crear_trabajador(self, cedula, nombre, fecha_ingreso, saldo_inicial=None,
                         fecha_saldo_inicial=None, perfil=None):
        if self.trabajador_por_cedula(cedula):
            raise RuntimeError("duplicate key value violates unique constraint")
        id_ = self._id()
        perfil = perfil or {}
        self.trab[id_] = {"id": id_, "cedula": cedula, "nombre": nombre,
                          "fecha_ingreso": fecha_ingreso, "activo": True,
                          "fecha_salida": None, "saldo_inicial": saldo_inicial,
                          "fecha_saldo_inicial": fecha_saldo_inicial, "creado_en": datetime.now(),
                          **{k: perfil.get(k) for k in self.PERFIL}}
        return id_

    def poner_saldo_inicial(self, id_, saldo, fecha):
        self.trab[id_].update(saldo_inicial=saldo, fecha_saldo_inicial=fecha)

    def editar_trabajador(self, id_, cedula, nombre, fecha_ingreso, perfil=None):
        self.trab[id_].update(cedula=cedula, nombre=nombre, fecha_ingreso=fecha_ingreso)
        if perfil is not None:
            self.trab[id_].update({k: perfil.get(k) for k in self.PERFIL})

    def dar_de_baja(self, id_, fecha_salida):
        self.trab[id_].update(activo=False, fecha_salida=fecha_salida)

    def reactivar(self, id_):
        self.trab[id_].update(activo=True, fecha_salida=None)

    def _cargar_lote(self, filas, hoy):
        # Misma lógica que store.cargar_lote: se repite a propósito para que
        # el test la pruebe contra una base de verdad-de-mentira.
        nuevos = actualizados = 0
        for f in filas:
            perfil = {k: f.get(k) for k in self.PERFIL}
            ex = self.trabajador_por_cedula(f["cedula"])
            if ex:
                self.editar_trabajador(ex["id"], f["cedula"], f["nombre"], f["fecha_ingreso"], perfil)
                actualizados += 1
                continue
            saldo = f.get("saldo")
            self.crear_trabajador(f["cedula"], f["nombre"], f["fecha_ingreso"],
                                  saldo, hoy if saldo is not None else None, perfil)
            nuevos += 1
        return {"nuevos": nuevos, "actualizados": actualizados}

    # --- vacaciones / ajustes ---
    def vacaciones(self, trabajador_id):
        return sorted((v for v in self.vac.values() if v["trabajador_id"] == trabajador_id),
                      key=lambda v: v["desde"], reverse=True)

    def agregar_vacacion(self, trabajador_id, desde, hasta, dias, nota, cargado_por):
        id_ = self._id()
        self.vac[id_] = {"id": id_, "trabajador_id": trabajador_id, "desde": desde, "hasta": hasta,
                         "dias": dias, "nota": nota or None, "cargado_por": cargado_por,
                         "creado_en": datetime.now()}
        return id_

    def borrar_vacacion(self, id_):
        self.vac.pop(id_, None)

    def ajustes(self, trabajador_id):
        return [a for a in self.aju.values() if a["trabajador_id"] == trabajador_id]

    def agregar_ajuste(self, trabajador_id, dias, motivo, cargado_por):
        id_ = self._id()
        self.aju[id_] = {"id": id_, "trabajador_id": trabajador_id, "dias": dias, "motivo": motivo,
                         "cargado_por": cargado_por, "creado_en": datetime.now()}
        return id_

    def borrar_ajuste(self, id_):
        self.aju.pop(id_, None)

    # --- comidas ---
    def comidas_del_mes(self, trabajador_id, anio, mes):
        return {(f, tipo) for (t, f, tipo) in self.alm if t == trabajador_id and f.year == anio and f.month == mes}

    def comidas_de_todos(self, anio, mes):
        salida = {}
        for (t, f, tipo) in self.alm:
            if f.year == anio and f.month == mes:
                salida.setdefault(t, set()).add((f, tipo))
        return salida

    def marcar_comida(self, trabajador_id, fecha, tipo, marcado_por):
        if tipo not in ("almuerzo", "cena"):
            raise ValueError(f"No sé qué comida es «{tipo}».")
        self.alm.add((trabajador_id, fecha, tipo))

    def desmarcar_comida(self, trabajador_id, fecha, tipo):
        self.alm.discard((trabajador_id, fecha, tipo))

    # --- usuarios ---
    def usuarios(self):
        return sorted((dict(u) for u in self.usu.values()), key=lambda u: u["usuario"])

    def usuario_por_nombre(self, usuario):
        for u in self.usu.values():
            if u["usuario"] == usuario and u["activo"]:
                return dict(u)
        return None

    def hay_usuarios(self):
        return bool(self.usu)

    def crear_usuario(self, usuario, clave_hash, nombre):
        id_ = self._id()
        self.usu[id_] = {"id": id_, "usuario": usuario, "clave_hash": clave_hash, "nombre": nombre,
                         "activo": True, "creado_en": datetime.now()}
        return id_

    def cambiar_clave(self, id_, clave_hash):
        self.usu[id_]["clave_hash"] = clave_hash

    def activar_usuario(self, id_, activo):
        self.usu[id_]["activo"] = activo


FUNCIONES = [n for n in dir(BaseFalsa) if not n.startswith("_") and callable(getattr(BaseFalsa, n))]


def enchufar(store_modulo, base: BaseFalsa | None = None) -> BaseFalsa:
    """Pone la base de mentira adentro de `store`, función por función."""
    base = base or BaseFalsa()
    for n in FUNCIONES:
        setattr(store_modulo, n, getattr(base, n))
    store_modulo.cargar_lote = base._cargar_lote
    store_modulo.AVISOS_ESQUEMA = []
    return base

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
        self.sol: dict[int, dict] = {}
        self.cam: dict[int, dict] = {}
        self.inv: dict[int, dict] = {}
        self.fer: dict[date, str] = {date(2026, 1, 1): "Año Nuevo", date(2026, 12, 25): "Navidad"}
        self.conf: dict[str, str] = {}
        self.cert: dict[int, dict] = {}
        self.alm_hora: dict[tuple, datetime] = {}
        self.alm_turno: dict[tuple, str | None] = {}
        self.alm_quien: dict[tuple, str] = {}
        self._n = 0

    def _id(self):
        self._n += 1
        return self._n

    # --- trabajadores ---
    def _con_totales(self, t):
        t = dict(t)
        corte = t["fecha_saldo_inicial"]
        vivos = [v for v in self.vac.values() if v["trabajador_id"] == t["id"] and not v["borrado_en"]
                 and v["tipo"] in self.TIPOS_QUE_DESCUENTAN]
        t["tomados"] = sum(v["dias"] for v in vivos if corte is None or v["desde"] >= corte)
        t["tomados_anio"] = sum(v["dias"] for v in vivos if v["desde"].year == date.today().year)
        t["ajustes"] = sum(a["dias"] for a in self.aju.values()
                           if a["trabajador_id"] == t["id"] and not a["borrado_en"])
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

    PERFIL = ("area", "fecha_nacimiento", "celular", "dias_por_anio", "direccion")
    TIPOS_AUSENCIA = {"vacaciones": ("Vacaciones", True), "permiso": ("Permiso", True),
                      "enfermedad": ("Enfermedad", False), "sin_goce": ("Permiso sin sueldo", False)}
    TIPOS_QUE_DESCUENTAN = ("vacaciones", "permiso")

    def crear_trabajador(self, cedula, nombre, fecha_ingreso, saldo_inicial=None,
                         fecha_saldo_inicial=None, perfil=None):
        if self.trabajador_por_cedula(cedula):
            raise RuntimeError("duplicate key value violates unique constraint")
        id_ = self._id()
        perfil = perfil or {}
        self.trab[id_] = {"id": id_, "cedula": cedula, "nombre": nombre,
                          "fecha_ingreso": fecha_ingreso, "activo": True, "puede_invitar": False,
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

    def cambiar_contacto(self, id_, celular, direccion):
        t = self.trab[id_]
        cambios = [{"campo": c, "antes": t.get(c), "despues": n}
                   for c, n in (("celular", celular), ("direccion", direccion)) if t.get(c) != n]
        if not cambios:
            return []
        t.update(celular=celular, direccion=direccion)
        for c in cambios:
            id_c = self._id()
            self.cam[id_c] = {"id": id_c, "trabajador_id": id_, **c, "visto": False,
                              "creado_en": datetime.now()}
        return cambios

    def cambios_perfil_sin_ver(self):
        return [dict(c, nombre=self.trab[c["trabajador_id"]]["nombre"], cedula=self.trab[c["trabajador_id"]]["cedula"])
                for c in self.cam.values() if not c["visto"]]

    def marcar_cambio_visto(self, id_):
        self.cam[id_]["visto"] = True

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
        return sorted((v for v in self.vac.values() if v["trabajador_id"] == trabajador_id and not v["borrado_en"]),
                      key=lambda v: v["desde"], reverse=True)

    def agregar_vacacion(self, trabajador_id, desde, hasta, dias, nota, cargado_por, tipo="vacaciones"):
        if tipo not in self.TIPOS_AUSENCIA:
            raise ValueError(f"No sé qué tipo de ausencia es «{tipo}».")
        id_ = self._id()
        self.vac[id_] = {"id": id_, "trabajador_id": trabajador_id, "desde": desde, "hasta": hasta,
                         "dias": dias, "nota": nota or None, "cargado_por": cargado_por, "tipo": tipo,
                         "creado_en": datetime.now(), "borrado_en": None, "borrado_por": None}
        return id_

    def editar_vacacion(self, id_, tipo, desde, hasta, dias, nota):
        if tipo not in self.TIPOS_AUSENCIA:
            raise ValueError(f"No sé qué tipo de ausencia es «{tipo}».")
        if id_ in self.vac and not self.vac[id_]["borrado_en"]:
            self.vac[id_].update(tipo=tipo, desde=desde, hasta=hasta, dias=dias, nota=nota or None)

    def borrar_vacacion(self, id_, quien="?"):
        if id_ in self.vac and not self.vac[id_]["borrado_en"]:
            self.vac[id_].update(borrado_en=datetime.now(), borrado_por=quien)

    def recuperar_vacacion(self, id_):
        self.vac[id_].update(borrado_en=None, borrado_por=None)

    def historial(self, limite=200):
        filas = []
        for v in self.vac.values():
            if v["borrado_en"]:
                t = self.trab[v["trabajador_id"]]
                filas.append({"que": "periodo", **v, "nombre": t["nombre"], "cedula": t["cedula"]})
        for a in self.aju.values():
            if a["borrado_en"]:
                t = self.trab[a["trabajador_id"]]
                filas.append({"que": "ajuste", **a, "tipo": None, "desde": None, "hasta": None,
                              "nota": a["motivo"], "nombre": t["nombre"], "cedula": t["cedula"]})
        return sorted(filas, key=lambda f: f["borrado_en"], reverse=True)[:limite]

    # --- pedidos ---
    def _sol_con_trab(self, s):
        t = self.trab[s["trabajador_id"]]
        return dict(s, nombre=t["nombre"], cedula=t["cedula"], celular=t.get("celular"), area=t.get("area"))

    def solicitudes(self, trabajador_id):
        return sorted((dict(s) for s in self.sol.values() if s["trabajador_id"] == trabajador_id),
                      key=lambda s: s["creado_en"], reverse=True)

    def solicitud(self, id_):
        s = self.sol.get(id_)
        return self._sol_con_trab(s) if s else None

    def solicitudes_pendientes(self):
        return [self._sol_con_trab(s) for s in self.sol.values() if s["estado"] == "pendiente"]

    def solicitudes_respondidas(self, limite=40):
        return [self._sol_con_trab(s) for s in sorted(self.sol.values(), key=lambda s: s["id"], reverse=True)
                if s["estado"] != "pendiente"][:limite]

    def cuantas_pendientes(self):
        return sum(1 for s in self.sol.values() if s["estado"] == "pendiente")

    def crear_solicitud(self, trabajador_id, tipo, desde, hasta, dias, nota):
        if tipo not in self.TIPOS_AUSENCIA:
            raise ValueError(f"No sé qué tipo de ausencia es «{tipo}».")
        id_ = self._id()
        self.sol[id_] = {"id": id_, "trabajador_id": trabajador_id, "tipo": tipo, "desde": desde,
                         "hasta": hasta, "dias": dias, "nota": nota or None, "estado": "pendiente",
                         "respuesta": None, "respondido_por": None, "respondido_en": None,
                         "vacacion_id": None, "creado_en": datetime.now()}
        return id_

    def cancelar_solicitud_aprobada(self, id_, respuesta, quien):
        s = self.sol.get(id_)
        if not s or s["estado"] != "aprobada":
            return
        if s["vacacion_id"]:
            self.borrar_vacacion(s["vacacion_id"], quien)
        s.update(estado="cancelada", respuesta=respuesta, respondido_por=quien, respondido_en=datetime.now())

    def solicitud_por_vacacion(self, vacacion_id):
        return next((dict(s) for s in self.sol.values() if s["vacacion_id"] == vacacion_id), None)

    def cambiar_fechas_solicitud(self, id_, desde, hasta, dias):
        self.sol[id_].update(desde=desde, hasta=hasta, dias=dias)

    def reabrir_solicitud_cancelada(self, id_):
        if self.sol[id_]["estado"] == "cancelada":
            self.sol[id_].update(estado="aprobada", respuesta=None)

    def borrar_solicitud(self, id_):
        s = self.sol.get(id_)
        if s and s["estado"] in ("rechazada", "cancelada"):
            del self.sol[id_]
            return True
        return False

    def responder_solicitud(self, id_, estado, respuesta, quien, vacacion_id=None):
        s = self.sol[id_]
        if s["estado"] == "pendiente":
            s.update(estado=estado, respuesta=respuesta or None, respondido_por=quien,
                     respondido_en=datetime.now(), vacacion_id=vacacion_id)

    def ajustes(self, trabajador_id):
        return [a for a in self.aju.values() if a["trabajador_id"] == trabajador_id and not a["borrado_en"]]

    def agregar_ajuste(self, trabajador_id, dias, motivo, cargado_por):
        id_ = self._id()
        self.aju[id_] = {"id": id_, "trabajador_id": trabajador_id, "dias": dias, "motivo": motivo,
                         "cargado_por": cargado_por, "creado_en": datetime.now(),
                         "borrado_en": None, "borrado_por": None}
        return id_

    def borrar_ajuste(self, id_, quien="?"):
        if id_ in self.aju and not self.aju[id_]["borrado_en"]:
            self.aju[id_].update(borrado_en=datetime.now(), borrado_por=quien)

    def recuperar_ajuste(self, id_):
        self.aju[id_].update(borrado_en=None, borrado_por=None)

    # --- certificados ---
    def guardar_certificado(self, trabajador_id, datos, tipo_archivo, nombre, subido_por, solicitud_id=None, vacacion_id=None):
        id_ = self._id()
        self.cert[id_] = {"id": id_, "trabajador_id": trabajador_id, "solicitud_id": solicitud_id, "vacacion_id": vacacion_id,
                          "tipo_archivo": tipo_archivo, "nombre": nombre, "datos": datos, "subido_por": subido_por,
                          "subido_en": datetime.now()}
        return id_

    def certificado(self, id_):
        c = self.cert.get(id_)
        return dict(c) if c else None

    def certificados_de(self, trabajador_id):
        return [{k: v for k, v in c.items() if k != "datos"} for c in self.cert.values() if c["trabajador_id"] == trabajador_id]

    def certificados_por_solicitud(self):
        salida = {}
        for c in self.cert.values():
            if c["solicitud_id"]:
                salida.setdefault(c["solicitud_id"], []).append({k: v for k, v in c.items() if k != "datos"})
        return salida

    def ligar_certificados_a_vacacion(self, solicitud_id, vacacion_id):
        for c in self.cert.values():
            if c["solicitud_id"] == solicitud_id:
                c["vacacion_id"] = vacacion_id

    # --- comidas ---
    def comidas_del_mes(self, trabajador_id, anio, mes):
        return {(f, tipo) for (t, f, tipo) in self.alm if t == trabajador_id and f.year == anio and f.month == mes}

    def comidas_de_todos(self, anio, mes):
        salida = {}
        for (t, f, tipo) in self.alm:
            if f.year == anio and f.month == mes:
                salida.setdefault(t, {})[(f, tipo)] = self.alm_turno.get((t, f, tipo))
        return salida

    def marcar_comida(self, trabajador_id, fecha, tipo, marcado_por, turno=None):
        if tipo not in ("almuerzo", "cena"):
            raise ValueError(f"No sé qué comida es «{tipo}».")
        if (trabajador_id, fecha, tipo) not in self.alm:
            self.alm_hora[(trabajador_id, fecha, tipo)] = datetime.now()
            self.alm_turno[(trabajador_id, fecha, tipo)] = turno
            self.alm_quien[(trabajador_id, fecha, tipo)] = marcado_por
        self.alm.add((trabajador_id, fecha, tipo))

    def poner_puede_invitar(self, trabajador_id, puede):
        self.trab[trabajador_id]["puede_invitar"] = bool(puede)

    def quien_comio(self, fecha):
        filas = []
        for (tid, f, tipo) in self.alm:
            if f == fecha:
                t = self.trab[tid]
                filas.append({"trabajador_id": tid, "tipo": tipo, "turno": self.alm_turno.get((tid, f, tipo)),
                              "marcado_por": self.alm_quien.get((tid, f, tipo), "?"), "creado_en": self.alm_hora.get((tid, f, tipo)),
                              "nombre": t["nombre"], "area": t.get("area"), "celular": t.get("celular")})
        return sorted(filas, key=lambda x: (x["turno"] is None, x["turno"] or "", x["nombre"]))

    def desmarcar_comida_reciente(self, trabajador_id, fecha, tipo, minutos=10):
        k = (trabajador_id, fecha, tipo)
        h = self.alm_hora.get(k)
        if k in self.alm and h and (datetime.now() - h).total_seconds() < minutos * 60:
            self.alm.discard(k)
            return True
        return False

    def agregar_invitados(self, trabajador_id, fecha, tipo, cantidad, descripcion, cargado_por, turno=None):
        if tipo not in ("almuerzo", "cena"):
            raise ValueError(f"No sé qué comida es «{tipo}».")
        id_ = self._id()
        self.inv[id_] = {"id": id_, "trabajador_id": trabajador_id, "fecha": fecha, "tipo": tipo, "cantidad": cantidad,
                         "descripcion": descripcion, "cargado_por": cargado_por, "creado_en": datetime.now(), "borrado_en": None,
                         "turno": turno}
        return id_

    def turno_marcado(self, trabajador_id, fecha, tipo):
        return self.alm_turno.get((trabajador_id, fecha, tipo))

    def invitados_del_dia(self, fecha):
        return [dict(i, nombre=self.trab[i["trabajador_id"]]["nombre"]) for i in self.inv.values()
                if i["fecha"] == fecha and not i["borrado_en"]]

    def invitados_del_mes(self, anio, mes):
        salida = {}
        for i in sorted(self.inv.values(), key=lambda i: i["creado_en"]):
            if i["fecha"].year == anio and i["fecha"].month == mes and not i["borrado_en"]:
                salida.setdefault(i["fecha"], []).append(dict(i, nombre=self.trab[i["trabajador_id"]]["nombre"]))
        return salida

    def borrar_invitados(self, id_):
        if id_ in self.inv:
            self.inv[id_]["borrado_en"] = datetime.now()

    def feriados(self, anio=None):
        return {f: n for f, n in sorted(self.fer.items()) if anio is None or f.year == anio}

    def agregar_feriado(self, fecha, nombre):
        self.fer[fecha] = nombre

    def borrar_feriado(self, fecha):
        self.fer.pop(fecha, None)

    def configuracion(self, clave):
        return self.conf.get(clave)

    def poner_configuracion(self, clave, valor):
        self.conf[clave] = valor

    def desmarcar_comida(self, trabajador_id, fecha, tipo):
        self.alm.discard((trabajador_id, fecha, tipo))

    # --- usuarios ---
    def usuarios(self):
        return sorted((dict(u) for u in self.usu.values()), key=lambda u: u["usuario"])

    def usuario(self, id_):
        u = self.usu.get(id_)
        return dict(u) if u else None

    def usuario_por_nombre(self, usuario):
        for u in self.usu.values():
            if u["usuario"] == usuario and u["activo"]:
                return dict(u)
        return None

    def hay_usuarios(self):
        return bool(self.usu)

    ROLES = ("contabilidad", "comedor")

    def crear_usuario(self, usuario, clave_hash, nombre, rol="contabilidad"):
        if rol not in self.ROLES:
            raise ValueError(f"No sé qué rol es «{rol}».")
        id_ = self._id()
        self.usu[id_] = {"id": id_, "usuario": usuario, "clave_hash": clave_hash, "nombre": nombre,
                         "activo": True, "rol": rol, "creado_en": datetime.now()}
        return id_

    def comida_marcada(self, trabajador_id, fecha, tipo):
        return (trabajador_id, fecha, tipo) in self.alm

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
    store_modulo.ROLES = base.ROLES
    store_modulo.TIPOS_AUSENCIA = base.TIPOS_AUSENCIA
    store_modulo.TIPOS_QUE_DESCUENTAN = base.TIPOS_QUE_DESCUENTAN
    return base

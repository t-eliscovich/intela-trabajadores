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
        sys.modules["psycopg2.pool"].ThreadedConnectionPool = P
    else:
        def roto(*a, **k):
            raise RuntimeError("could not connect to server: Connection refused")
        sys.modules["psycopg2.pool"].SimpleConnectionPool = roto
        sys.modules["psycopg2.pool"].ThreadedConnectionPool = roto
    sys.modules["psycopg2"].extras = sys.modules["psycopg2.extras"]
    sys.modules["psycopg2"].pool = sys.modules["psycopg2.pool"]


def store_mismo_recibo(antes, f):
    if antes["ingresos"] != f["ingresos"] or antes["descuentos"] != f["descuentos"]:
        return False
    return all(abs(float(antes[k] or 0) - float(f[k] or 0)) < 0.005 for k in ("liquido", "sueldo", "dias", "atrasos", "otros"))


ROL_SIN_REVISAR = {"revisado_por_id": None, "revisado_por": None, "revisado_en": None, "aprobado_por_id": None,
                   "aprobado_por": None, "aprobado_en": None, "firma_empresa": None}


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
        self.rol: dict[int, dict] = {}
        self.cons: dict[int, dict] = {}
        self.cod: dict[int, dict] = {}
        self.cban: dict[int, dict] = {}
        self.avi: dict[int, dict] = {}
        self.alm_hora: dict[tuple, datetime] = {}
        self.alm_turno: dict[tuple, str | None] = {}
        self.alm_quien: dict[tuple, str] = {}
        self._n = 0

    def _id(self):
        self._n += 1
        return self._n

    # --- constancia de roles ---
    def _cons_con_trab(self, c):
        t = self.trab[c["trabajador_id"]]
        c = dict(c, nombre=t["nombre"], cedula=t["cedula"], area=t.get("area"), celular=t.get("celular"))
        c.pop("firma", None)
        return c

    def constancia_de(self, trabajador_id):
        c = next((c for c in self.cons.values() if c["trabajador_id"] == trabajador_id and not c["anulada_en"]), None)
        return self._cons_con_trab(c) if c else None

    def constancia(self, id_):
        c = self.cons.get(id_)
        return self._cons_con_trab(c) if c else None

    def constancias_vigentes(self):
        return {c["trabajador_id"]: self._cons_con_trab(c) for c in self.cons.values() if not c["anulada_en"]}

    def firmar_constancia(self, trabajador_id, version, banco, cuenta, texto, huella, png, desde):
        if self.constancia_de(trabajador_id):
            return None
        id_ = self._id()
        self.cons[id_] = {"id": id_, "trabajador_id": trabajador_id, "version": version, "banco": banco, "cuenta": cuenta,
                          "texto": texto, "huella": huella, "firma": png, "firmado_en": datetime.now(),
                          "firmado_desde": desde[:200], "anulada_en": None, "anulada_por": None, "motivo_anulada": None}
        return id_

    def anular_constancia(self, id_, quien, motivo):
        c = self.cons.get(id_)
        if not c or c["anulada_en"]:
            return False
        c.update(anulada_en=datetime.now(), anulada_por=quien, motivo_anulada=motivo[:200])
        return True

    def firma_de_constancia(self, id_):
        c = self.cons.get(id_)
        return c["firma"] if c else None

    # --- rol de pagos ---
    def _rol_con_trab(self, r):
        t = self.trab[r["trabajador_id"]]
        r = dict(r, nombre=t["nombre"], cedula=t["cedula"], cedula_banco=t.get("cedula_banco"), area=t.get("area"), celular=t.get("celular"),
                 fecha_ingreso=t.get("fecha_ingreso"), fecha_salida=t.get("fecha_salida"))
        r.pop("firma", None)
        r.pop("firma_empresa", None)
        return r

    def roles_del_mes(self, anio, mes):
        return sorted((self._rol_con_trab(r) for r in self.rol.values()
                       if not r["borrado_en"] and r["anio"] == anio and r["mes"] == mes), key=lambda r: r["nombre"])

    def rol_pago(self, id_):
        r = self.rol.get(id_)
        return self._rol_con_trab(r) if r and not r["borrado_en"] else None

    def roles_de(self, trabajador_id):
        return sorted((self._rol_con_trab(r) for r in self.rol.values()
                       if not r["borrado_en"] and r["trabajador_id"] == trabajador_id),
                      key=lambda r: (r["anio"], r["mes"]), reverse=True)

    def meses_con_rol(self):
        meses = {}
        for r in self.rol.values():
            if r["borrado_en"]:
                continue
            m = meses.setdefault((r["anio"], r["mes"]), {"anio": r["anio"], "mes": r["mes"], "cuantos": 0,
                                                           "total": 0.0, "cargado_en": r["cargado_en"],
                                                           "firmados": 0, "piden_firma": 0,
                                                           "necesitan": 0, "revisados": 0, "aprobados": 0})
            m["cuantos"] += 1
            if r.get("necesita_aprobacion") or r.get("pide_firma"):
                m["necesitan"] += 1
                m["revisados"] += 1 if r.get("revisado_en") else 0
                m["aprobados"] += 1 if r.get("aprobado_en") else 0
            m["firmados"] += 1 if r["firmado_en"] else 0
            m["piden_firma"] += 1 if r["pide_firma"] else 0
            m["total"] += r["liquido"]
        return sorted(meses.values(), key=lambda m: (m["anio"], m["mes"]), reverse=True)

    def cargar_roles(self, anio, mes, filas, quien, pide_firma=True):
        reemplazados = 0
        previos = {r["trabajador_id"]: r for r in self.rol.values()
                   if not r["borrado_en"] and r["anio"] == anio and r["mes"] == mes}
        firmados = {k: r for k, r in previos.items() if r["firmado_en"]}
        quedan = 0
        for r in self.rol.values():
            if not r["borrado_en"] and r["anio"] == anio and r["mes"] == mes:
                r["borrado_en"], r["borrado_por"] = datetime.now(), quien
                reemplazados += 1
        for f in filas:
            id_ = self._id()
            campos = {k: f[k] for k in ("trabajador_id", "codigo", "nombre_rol", "sueldo", "dias", "horas_25",
                                        "horas_50", "horas_100", "atrasos", "otros", "ingresos", "descuentos",
                                        "total_ingresos", "total_descuentos", "liquido")}
            self.rol[id_] = {"id": id_, "anio": anio, "mes": mes, **campos, "cargado_por": quien,
                             "cargado_en": datetime.now(), "borrado_en": None, "borrado_por": None,
                             "firma": None, "firmado_en": None, "firmado_desde": None, "firma_huella": None,
                             "pide_firma": pide_firma, **ROL_SIN_REVISAR, "necesita_aprobacion": pide_firma}
            antes = previos.get(f["trabajador_id"])
            if antes and store_mismo_recibo(antes, f):
                self.rol[id_].update(firma=antes["firma"], firmado_en=antes["firmado_en"],
                                     firmado_desde=antes["firmado_desde"], firma_huella=antes["firma_huella"],
                                     **{k: antes.get(k) for k in ROL_SIN_REVISAR},
                                     necesita_aprobacion=bool(antes.get("necesita_aprobacion")) and pide_firma)
                quedan += 1 if antes["firmado_en"] else 0
            for t in self.trab.values():
                if t.get("codigo_rol") == f["codigo"] and t["id"] != f["trabajador_id"]:
                    t["codigo_rol"] = None
            self.trab[f["trabajador_id"]]["codigo_rol"] = f["codigo"]
            self.trab[f["trabajador_id"]]["nombre_rol"] = f["nombre_rol"]
        presentes = {f["trabajador_id"] for f in filas}
        return {"cargados": len(filas), "reemplazados": reemplazados,
                "firmas_perdidas": len(set(firmados) & presentes) - quedan,
                "firmados_quitados": len(set(firmados) - presentes)}

    def firmar_rol(self, id_, trabajador_id, png, desde, huella):
        r = self.rol.get(id_)
        if not r or r["borrado_en"] or r["trabajador_id"] != trabajador_id or r["firmado_en"] or not r["pide_firma"]:
            return False
        r.update(firma=png, firmado_en=datetime.now(), firmado_desde=desde[:200], firma_huella=huella)
        return True

    def revisar_roles(self, ids, usuario_id, nombre):
        n = 0
        for i in ids:
            r = self.rol.get(i)
            if r and not r["borrado_en"] and (r.get("necesita_aprobacion") or r.get("pide_firma")) and not r.get("revisado_en") and not r.get("aprobado_en"):
                r.update(revisado_por_id=usuario_id, revisado_por=nombre, revisado_en=datetime.now())
                n += 1
        return n

    def quitar_revision(self, ids):
        n = 0
        for i in ids:
            r = self.rol.get(i)
            if r and not r["borrado_en"] and not r.get("aprobado_en"):
                r.update(revisado_por_id=None, revisado_por=None, revisado_en=None)
                n += 1
        return n

    def aprobar_roles(self, ids, usuario_id, nombre, firma):
        n = 0
        for i in ids:
            r = self.rol.get(i)
            if (r and not r["borrado_en"] and (r.get("necesita_aprobacion") or r.get("pide_firma")) and r.get("revisado_en")
                    and not r.get("aprobado_en") and r.get("revisado_por_id") != usuario_id):
                r.update(aprobado_por_id=usuario_id, aprobado_por=nombre, aprobado_en=datetime.now(), firma_empresa=firma)
                n += 1
        return n

    def firma_empresa_del_rol(self, id_):
        r = self.rol.get(id_)
        return r.get("firma_empresa") if r and not r["borrado_en"] else None

    def guardar_control_banco(self, anio, mes, archivo, filas, quien):
        for c in self.cban.values():
            if c["anio"] == anio and c["mes"] == mes:
                c["borrado_en"] = datetime.now()
        id_ = self._id()
        self.cban[id_] = {"id": id_, "anio": anio, "mes": mes, "archivo": archivo, "filas": filas,
                          "total": round(sum(f["monto"] for f in filas), 2), "cargado_por": quien,
                          "cargado_en": datetime.now(), "borrado_en": None}

    def control_banco(self, anio, mes):
        vivos = [c for c in self.cban.values() if c["anio"] == anio and c["mes"] == mes and not c["borrado_en"]]
        return dict(max(vivos, key=lambda c: c["id"])) if vivos else None

    def mes_en_papel(self, anio, mes, quien):
        self.__dict__.setdefault("archivo_firmas", [])
        out = {"recibos": 0, "archivados": 0, "firmas_trabajador": 0, "firmas_empresa": 0}
        for r in self.rol.values():
            if r["borrado_en"] or r["anio"] != anio or r["mes"] != mes:
                continue
            out["recibos"] += 1
            if r["firmado_en"] or r.get("revisado_en") or r.get("aprobado_en"):
                self.archivo_firmas.append({"rol_pago_id": r["id"], "firma": r.get("firma"), "firmado_en": r["firmado_en"],
                                            "aprobado_por": r.get("aprobado_por"), "firma_empresa": r.get("firma_empresa"),
                                            "archivado_por": quien})
                out["archivados"] += 1
            out["firmas_trabajador"] += 1 if r["firmado_en"] else 0
            out["firmas_empresa"] += 1 if r.get("aprobado_en") else 0
            r.update(firma=None, firmado_en=None, firmado_desde=None, firma_huella=None, **ROL_SIN_REVISAR,
                     pide_firma=False, necesita_aprobacion=False)
        return out

    def estado_para_firmar(self, ids, usuario_id):
        rs = [self._rol_con_trab(self.rol[i]) | {"_r": self.rol[i]} for i in ids if i in self.rol and not self.rol[i]["borrado_en"]]
        return {"sin_revisar": [r["nombre"] for r in rs if not r.get("revisado_en") and not r.get("aprobado_en")],
                "ya_firmados": [r["nombre"] for r in rs if r.get("aprobado_en")],
                "revisados_por_el": [r["nombre"] for r in rs if r.get("revisado_en") and not r.get("aprobado_en")
                                     and r.get("revisado_por_id") == usuario_id]}

    def firmado_en_papel(self, anio, mes):
        n = 0
        for r in self.rol.values():
            if not r["borrado_en"] and r["anio"] == anio and r["mes"] == mes and r["pide_firma"] and not r["firmado_en"]:
                r["pide_firma"] = False
                r["necesita_aprobacion"] = False
                n += 1
        return n

    def sin_firmar_ultimo_mes(self):
        meses = self.meses_con_rol()
        if not meses:
            return 0
        return sum(1 for r in self.roles_del_mes(meses[0]["anio"], meses[0]["mes"]) if r["pide_firma"] and not r["firmado_en"]
                   and (not r.get("necesita_aprobacion") or r.get("aprobado_en")))

    def firma_del_rol(self, id_):
        r = self.rol.get(id_)
        return r["firma"] if r else None

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
        if not cedula:
            return None
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
        if cedula and self.trabajador_por_cedula(cedula):
            raise RuntimeError("duplicate key value violates unique constraint")
        id_ = self._id()
        perfil = perfil or {}
        self.trab[id_] = {"id": id_, "cedula": cedula, "nombre": nombre,
                          "fecha_ingreso": fecha_ingreso, "activo": True, "puede_invitar": False,
                          "fecha_salida": None, "saldo_inicial": saldo_inicial,
                          "fecha_saldo_inicial": fecha_saldo_inicial, "creado_en": datetime.now(),
                          "codigo_rol": None, "nombre_rol": None, "tipo": "planta",
                          "pausado_en": None, "pausado_por": None, "motivo_pausa": None,
                          **{k: perfil.get(k) for k in self.PERFIL}}
        return id_

    # --- tipo, pausa y avisos (misma lógica que store) ---
    def poner_tipo(self, id_, tipo):
        if tipo not in ("planta", "temporal", "jubilado"):
            raise ValueError(f"No sé qué tipo de trabajador es «{tipo}».")
        self.trab[id_]["tipo"] = tipo

    def pausar(self, id_, motivo, quien):
        self.trab[id_].update(pausado_en=datetime.now(), pausado_por=quien, motivo_pausa=motivo or None)

    def reanudar(self, id_):
        self.trab[id_].update(pausado_en=None, pausado_por=None, motivo_pausa=None)

    def perfil_sin_pisar(self, existente, perfil):
        return {k: (perfil.get(k) if perfil.get(k) is not None else existente.get(k)) for k in self.PERFIL}

    def _aviso_con_trab(self, a):
        t = self.trab.get(a["trabajador_id"]) or {}
        return dict(a, nombre=t.get("nombre"), cedula=t.get("cedula"))

    def crear_aviso(self, trabajador_id, texto, quien, clave="nota"):
        if clave != "nota" and any(a["trabajador_id"] == trabajador_id and a["clave"] == clave and not a["resuelto_en"]
                                   for a in self.avi.values()):
            return None
        id_ = self._id()
        self.avi[id_] = {"id": id_, "trabajador_id": trabajador_id, "clave": clave, "texto": texto,
                         "creado_en": datetime.now(), "creado_por": quien, "resuelto_en": None, "resuelto_por": None}
        return id_

    def avisos_abiertos(self):
        return [self._aviso_con_trab(a) for a in sorted(self.avi.values(), key=lambda a: -a["id"]) if not a["resuelto_en"]]

    def avisos_resueltos(self, limite=40):
        return [self._aviso_con_trab(a) for a in self.avi.values() if a["resuelto_en"]][:limite]

    def avisos_de(self, trabajador_id):
        return [a for a in self.avisos_abiertos() if a["trabajador_id"] == trabajador_id]

    def cuantos_avisos(self):
        return len(self.avisos_abiertos())

    def resolver_aviso(self, id_, quien):
        a = self.avi.get(id_)
        if a and not a["resuelto_en"]:
            a.update(resuelto_en=datetime.now(), resuelto_por=quien)

    def resolver_avisos(self, trabajador_id, clave, quien):
        for a in self.avi.values():
            if a["trabajador_id"] == trabajador_id and a["clave"] == clave and not a["resuelto_en"]:
                a.update(resuelto_en=datetime.now(), resuelto_por=quien)

    def corregir_cedula(self, id_, cedula):
        if any(t["cedula"] == cedula and t["id"] != id_ for t in self.trab.values()):
            return False
        self.trab[id_]["cedula"] = cedula
        return True

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
                self.editar_trabajador(ex["id"], f["cedula"], f["nombre"], f["fecha_ingreso"],
                                       self.perfil_sin_pisar(ex, perfil))
                if f.get("tipo"):
                    self.poner_tipo(ex["id"], f["tipo"])
                actualizados += 1
                continue
            saldo = f.get("saldo")
            id_ = self.crear_trabajador(f["cedula"], f["nombre"], f["fecha_ingreso"],
                                        saldo, hoy if saldo is not None else None, perfil)
            if f.get("tipo"):
                self.poner_tipo(id_, f["tipo"])
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

    def cambiar_comida(self, trabajador_id, fecha, tipo, tipo_nuevo, turno, marcado_por):
        viejo, nuevo = (trabajador_id, fecha, tipo), (trabajador_id, fecha, tipo_nuevo)
        if tipo != tipo_nuevo and nuevo in self.alm:
            raise ValueError(f"Ya tiene {'el almuerzo' if tipo_nuevo == 'almuerzo' else 'la cena'} registrado ese día.")
        if viejo not in self.alm:
            return
        hora = self.alm_hora.pop(viejo, datetime.now())
        self.alm_turno.pop(viejo, None); self.alm_quien.pop(viejo, None); self.alm.discard(viejo)
        self.alm.add(nuevo); self.alm_hora[nuevo] = hora; self.alm_turno[nuevo] = turno; self.alm_quien[nuevo] = marcado_por

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
    @staticmethod
    def _usu_vista(u):
        v = {k: x for k, x in u.items() if k not in ("firma", "clave_hash")}
        v["tiene_firma"] = u.get("firma") is not None
        return v

    def usuarios(self):
        return sorted((self._usu_vista(u) for u in self.usu.values()), key=lambda u: u["usuario"])

    def usuario(self, id_):
        u = self.usu.get(id_)
        return self._usu_vista(u) if u else None

    def poner_permisos_roles(self, id_, revisa, firma):
        self.usu[id_].update(revisa_roles=revisa, firma_roles=firma)

    def guardar_firma_usuario(self, id_, png):
        self.usu[id_].update(firma=png, firma_en=datetime.now())

    def firma_de_usuario(self, id_):
        u = self.usu.get(id_)
        return u.get("firma") if u else None

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
                         "activo": True, "rol": rol, "creado_en": datetime.now(), "revisa_roles": False,
                         "firma_roles": False, "firma": None, "firma_en": None}
        return id_

    def comida_marcada(self, trabajador_id, fecha, tipo):
        return (trabajador_id, fecha, tipo) in self.alm

    def renombrar_usuario(self, id_, usuario, nombre):
        self.usu[id_].update(usuario=usuario, nombre=nombre)

    def cambiar_clave(self, id_, clave_hash):
        self.usu[id_]["clave_hash"] = clave_hash

    def activar_usuario(self, id_, activo):
        self.usu[id_]["activo"] = activo


class _ClaveRol:
    """La contraseña del rol y los códigos por correo, en memoria."""

    def guardar_clave_rol(self, trabajador_id, clave_hash, email):
        self.trab[trabajador_id].update(clave_rol_hash=clave_hash, clave_rol_en=datetime.now(), email=email,
                                        clave_rol_fallidos=0, clave_rol_trabado_hasta=None)

    def borrar_clave_rol(self, trabajador_id):
        self.trab[trabajador_id].update(clave_rol_hash=None, clave_rol_en=None, clave_rol_fallidos=0,
                                        clave_rol_trabado_hasta=None)

    def clave_rol_fallida(self, trabajador_id):
        from datetime import timedelta
        t = self.trab[trabajador_id]
        n = (t.get("clave_rol_fallidos") or 0) + 1
        if n >= 5:
            t.update(clave_rol_fallidos=0, clave_rol_trabado_hasta=datetime.now() + timedelta(minutes=15))
        else:
            t["clave_rol_fallidos"] = n

    def clave_rol_buena(self, trabajador_id):
        self.trab[trabajador_id].update(clave_rol_fallidos=0, clave_rol_trabado_hasta=None)

    def clave_rol_trabada(self, trabajador_id):
        h = self.trab[trabajador_id].get("clave_rol_trabado_hasta")
        return bool(h and h > datetime.now())

    def codigos_recientes(self, trabajador_id, minutos):
        from datetime import timedelta
        desde = datetime.now() - timedelta(minutes=minutos)
        return sum(1 for c in self.cod.values() if c["trabajador_id"] == trabajador_id and c["creado_en"] > desde)

    def crear_codigo_correo(self, trabajador_id, email, codigo_hash):
        from datetime import timedelta
        for c in self.cod.values():
            if c["trabajador_id"] == trabajador_id and not c["usado_en"]:
                c["usado_en"] = datetime.now()
        id_ = self._id()
        self.cod[id_] = {"id": id_, "trabajador_id": trabajador_id, "email": email, "codigo_hash": codigo_hash,
                         "creado_en": datetime.now(), "vence_en": datetime.now() + timedelta(minutes=15),
                         "intentos": 0, "usado_en": None}
        return id_

    def codigo_vivo(self, trabajador_id):
        vivos = [c for c in self.cod.values() if c["trabajador_id"] == trabajador_id and not c["usado_en"]
                 and c["vence_en"] > datetime.now() and c["intentos"] < 5]
        return dict(max(vivos, key=lambda c: c["creado_en"])) if vivos else None

    def codigo_intento_malo(self, id_):
        self.cod[id_]["intentos"] += 1

    def codigo_usado(self, id_):
        self.cod[id_]["usado_en"] = datetime.now()


    def marcar_prueba(self, trabajador_id, es):
        self.trab[trabajador_id]["es_prueba"] = es

    def reiniciar_prueba(self, trabajador_id, quien):
        t = self.trab.get(trabajador_id)
        if not t or not t.get("es_prueba"):
            raise ValueError("Sólo se reinicia un trabajador marcado como de prueba.")
        t.update(clave_rol_hash=None, clave_rol_en=None, email=None, clave_rol_fallidos=0, clave_rol_trabado_hasta=None)
        for c in self.cod.values():
            if c["trabajador_id"] == trabajador_id and not c["usado_en"]:
                c["usado_en"] = datetime.now()
        n_c = 0
        for c in self.cons.values():
            if c["trabajador_id"] == trabajador_id and not c.get("anulada_en"):
                c.update(anulada_en=datetime.now(), anulada_por=quien, motivo_anulada="reinicio de prueba")
                n_c += 1
        n_f = 0
        for r in self.rol.values():
            if r["trabajador_id"] == trabajador_id and not r["borrado_en"] and r["firmado_en"]:
                r.update(firma=None, firmado_en=None, firmado_desde=None, firma_huella=None)
                n_f += 1
        return {"constancias": n_c, "firmas": n_f}


    def poner_cedula_banco(self, trabajador_id, cedula):
        self.trab[trabajador_id]["cedula_banco"] = cedula or None


for _n in dir(_ClaveRol):
    if not _n.startswith("_"):
        setattr(BaseFalsa, _n, getattr(_ClaveRol, _n))


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

"""Las tablas propias del programa: trabajadores, vacaciones, almuerzos, usuarios.

El schema se crea solo al arrancar (`CREATE ... IF NOT EXISTS`), igual que en
Máquinas y en Programa Core: el deploy no corre migraciones.

Todo lo que se calcula (días ganados, saldos) vive en `vacaciones.py`. Acá
sólo se guarda y se lee.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date

import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import SimpleConnectionPool

import config

_pool: SimpleConnectionPool | None = None


def init_pool() -> None:
    global _pool
    if not config.DATABASE_URL:
        raise RuntimeError("Falta TRABAJADORES_DATABASE_URL")
    _pool = SimpleConnectionPool(1, 4, config.DATABASE_URL)
    bootstrap()


@contextmanager
def _conn():
    assert _pool is not None, "init_pool() no fue llamado"
    con = _pool.getconn()
    try:
        yield con
    finally:
        _pool.putconn(con)


def _todos(sql: str, args: tuple = ()) -> list[dict]:
    with _conn() as con, con.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, args)
        return [dict(r) for r in cur.fetchall()]


def _uno(sql: str, args: tuple = ()) -> dict | None:
    filas = _todos(sql, args)
    return filas[0] if filas else None


def _ejecutar(sql: str, args: tuple = ()) -> dict | None:
    """Ejecuta y devuelve la fila del RETURNING si la hay.

    El rollback no es de adorno: una cédula repetida viola el índice único, y
    una conexión que vuelve al pool con la transacción abierta hace fallar la
    consulta siguiente, que no tiene nada que ver.
    """
    with _conn() as con, con.cursor(cursor_factory=RealDictCursor) as cur:
        try:
            cur.execute(sql, args)
            fila = dict(cur.fetchone()) if cur.description else None
            con.commit()
            return fila
        except Exception:
            con.rollback()
            raise


# --------------------------------------------------------------------------
# Esquema. UNA SENTENCIA POR TRANSACCIÓN: si una falla queda anotada en
# AVISOS_ESQUEMA y /healthz la muestra, en vez de tirar abajo el arranque.
# --------------------------------------------------------------------------
AVISOS_ESQUEMA: list[str] = []


def _sentencias(sql: str) -> list[str]:
    sin_comentarios = "\n".join(
        linea.split("--")[0] if "--" in linea else linea for linea in sql.split("\n"))
    return [s.strip() for s in sin_comentarios.split(";") if s.strip()]


def bootstrap() -> None:
    AVISOS_ESQUEMA.clear()
    for sentencia in _sentencias(ESQUEMA):
        try:
            with _conn() as con, con.cursor() as cur:
                cur.execute(sentencia)
                con.commit()
        except Exception as exc:  # noqa: BLE001
            corta = " ".join(sentencia.split())[:120]
            AVISOS_ESQUEMA.append(f"{corta} → {exc}")


ESQUEMA = """
    CREATE SCHEMA IF NOT EXISTS trabajadores;

    CREATE TABLE IF NOT EXISTS trabajadores.trabajador (
        id             serial PRIMARY KEY,
        cedula         text NOT NULL UNIQUE,
        nombre         text NOT NULL,
        fecha_ingreso  date NOT NULL,
        activo         boolean NOT NULL DEFAULT true,
        fecha_salida   date,
        -- Cuántos días le quedaban el día que se lo cargó al programa. De
        -- ahí en adelante la cuenta sigue sola. NULL = contar desde el ingreso.
        saldo_inicial        numeric(6,1),
        fecha_saldo_inicial  date,
        creado_en      timestamptz NOT NULL DEFAULT now()
    );

    -- Cada período de vacaciones tomado. `dias` se propone como los días
    -- corridos entre las dos fechas y contabilidad lo puede corregir.
    CREATE TABLE IF NOT EXISTS trabajadores.vacacion (
        id             serial PRIMARY KEY,
        trabajador_id  integer NOT NULL REFERENCES trabajadores.trabajador(id),
        desde          date NOT NULL,
        hasta          date NOT NULL,
        dias           numeric(6,1) NOT NULL,
        nota           text,
        cargado_por    text NOT NULL,
        creado_en      timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS vacacion_trabajador_idx
        ON trabajadores.vacacion (trabajador_id, desde DESC);

    -- Días que suman o restan sin ser un período tomado: el saldo con el que
    -- arranca alguien que ya trabajaba, días pagados en plata, correcciones.
    CREATE TABLE IF NOT EXISTS trabajadores.ajuste_vacacion (
        id             serial PRIMARY KEY,
        trabajador_id  integer NOT NULL REFERENCES trabajadores.trabajador(id),
        dias           numeric(6,1) NOT NULL,
        motivo         text NOT NULL,
        cargado_por    text NOT NULL,
        creado_en      timestamptz NOT NULL DEFAULT now()
    );

    -- Un check por comida por día. Existe la fila = comió.
    -- tipo: 'almuerzo' o 'cena'.
    CREATE TABLE IF NOT EXISTS trabajadores.comida (
        trabajador_id  integer NOT NULL REFERENCES trabajadores.trabajador(id),
        fecha          date NOT NULL,
        tipo           text NOT NULL CHECK (tipo IN ('almuerzo', 'cena')),
        marcado_por    text NOT NULL,
        creado_en      timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (trabajador_id, fecha, tipo)
    );
    CREATE INDEX IF NOT EXISTS comida_fecha_idx ON trabajadores.comida (fecha);

    -- Quién entra a la parte de contabilidad.
    CREATE TABLE IF NOT EXISTS trabajadores.usuario (
        id          serial PRIMARY KEY,
        usuario     text NOT NULL UNIQUE,
        clave_hash  text NOT NULL,
        nombre      text NOT NULL,
        activo      boolean NOT NULL DEFAULT true,
        creado_en   timestamptz NOT NULL DEFAULT now()
    );
"""


# --------------------------------------------------------------------------
# Trabajadores
# --------------------------------------------------------------------------
_TRABAJADOR_CON_TOTALES = """
    SELECT t.*,
           -- Un período anterior al saldo inicial ya está descontado en ese
           -- saldo: se guarda como historia pero no se resta dos veces.
           COALESCE((SELECT SUM(dias) FROM trabajadores.vacacion v
                      WHERE v.trabajador_id = t.id
                        AND (t.fecha_saldo_inicial IS NULL
                             OR v.desde >= t.fecha_saldo_inicial)), 0) AS tomados,
           COALESCE((SELECT SUM(dias) FROM trabajadores.ajuste_vacacion a
                      WHERE a.trabajador_id = t.id), 0)        AS ajustes
      FROM trabajadores.trabajador t
"""


def trabajadores(incluir_inactivos: bool = False) -> list[dict]:
    filtro = "" if incluir_inactivos else " WHERE t.activo"
    return _todos(_TRABAJADOR_CON_TOTALES + filtro + " ORDER BY t.nombre")


def trabajador(id_: int) -> dict | None:
    return _uno(_TRABAJADOR_CON_TOTALES + " WHERE t.id = %s", (id_,))


def trabajador_por_cedula(cedula: str) -> dict | None:
    return _uno(_TRABAJADOR_CON_TOTALES + " WHERE t.cedula = %s", (cedula,))


def crear_trabajador(cedula: str, nombre: str, fecha_ingreso: date,
                     saldo_inicial: float | None = None,
                     fecha_saldo_inicial: date | None = None) -> int:
    fila = _ejecutar(
        "INSERT INTO trabajadores.trabajador "
        "(cedula, nombre, fecha_ingreso, saldo_inicial, fecha_saldo_inicial) "
        "VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (cedula, nombre, fecha_ingreso, saldo_inicial, fecha_saldo_inicial))
    return fila["id"]


def editar_trabajador(id_: int, cedula: str, nombre: str, fecha_ingreso: date) -> None:
    _ejecutar("UPDATE trabajadores.trabajador SET cedula=%s, nombre=%s, fecha_ingreso=%s "
              "WHERE id=%s", (cedula, nombre, fecha_ingreso, id_))


def poner_saldo_inicial(id_: int, saldo: float | None, fecha: date | None) -> None:
    _ejecutar("UPDATE trabajadores.trabajador SET saldo_inicial=%s, fecha_saldo_inicial=%s "
              "WHERE id=%s", (saldo, fecha, id_))


def dar_de_baja(id_: int, fecha_salida: date) -> None:
    _ejecutar("UPDATE trabajadores.trabajador SET activo=false, fecha_salida=%s WHERE id=%s",
              (fecha_salida, id_))


def reactivar(id_: int) -> None:
    _ejecutar("UPDATE trabajadores.trabajador SET activo=true, fecha_salida=NULL WHERE id=%s",
              (id_,))


def cargar_lote(filas: list[dict], hoy: date) -> dict:
    """La carga masiva inicial. Cada fila: cedula, nombre, fecha_ingreso y,
    opcional, `saldo` (los días que le quedan HOY).

    Si la cédula ya existe se actualizan nombre y fecha; el saldo sólo se
    carga a los NUEVOS, para no pisarlo si se pega la planilla dos veces.
    """
    nuevos = actualizados = 0
    for f in filas:
        existente = trabajador_por_cedula(f["cedula"])
        if existente:
            editar_trabajador(existente["id"], f["cedula"], f["nombre"], f["fecha_ingreso"])
            actualizados += 1
            continue
        saldo = f.get("saldo")
        crear_trabajador(f["cedula"], f["nombre"], f["fecha_ingreso"],
                         saldo, hoy if saldo is not None else None)
        nuevos += 1
    return {"nuevos": nuevos, "actualizados": actualizados}


# --------------------------------------------------------------------------
# Vacaciones y ajustes
# --------------------------------------------------------------------------
def vacaciones(trabajador_id: int) -> list[dict]:
    return _todos("SELECT * FROM trabajadores.vacacion WHERE trabajador_id=%s "
                  "ORDER BY desde DESC", (trabajador_id,))


def agregar_vacacion(trabajador_id: int, desde: date, hasta: date, dias: float,
                     nota: str, cargado_por: str) -> int:
    fila = _ejecutar(
        "INSERT INTO trabajadores.vacacion (trabajador_id, desde, hasta, dias, nota, cargado_por) "
        "VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
        (trabajador_id, desde, hasta, dias, nota or None, cargado_por))
    return fila["id"]


def borrar_vacacion(id_: int) -> None:
    _ejecutar("DELETE FROM trabajadores.vacacion WHERE id=%s", (id_,))


def ajustes(trabajador_id: int) -> list[dict]:
    return _todos("SELECT * FROM trabajadores.ajuste_vacacion WHERE trabajador_id=%s "
                  "ORDER BY creado_en DESC", (trabajador_id,))


def agregar_ajuste(trabajador_id: int, dias: float, motivo: str, cargado_por: str) -> int:
    fila = _ejecutar(
        "INSERT INTO trabajadores.ajuste_vacacion (trabajador_id, dias, motivo, cargado_por) "
        "VALUES (%s,%s,%s,%s) RETURNING id", (trabajador_id, dias, motivo, cargado_por))
    return fila["id"]


def borrar_ajuste(id_: int) -> None:
    _ejecutar("DELETE FROM trabajadores.ajuste_vacacion WHERE id=%s", (id_,))


# --------------------------------------------------------------------------
# Comidas (almuerzo y cena)
# --------------------------------------------------------------------------
TIPOS_COMIDA = ("almuerzo", "cena")


def comidas_del_mes(trabajador_id: int, anio: int, mes: int) -> set[tuple[date, str]]:
    filas = _todos(
        "SELECT fecha, tipo FROM trabajadores.comida WHERE trabajador_id=%s "
        "AND date_trunc('month', fecha) = %s", (trabajador_id, date(anio, mes, 1)))
    return {(f["fecha"], f["tipo"]) for f in filas}


def comidas_de_todos(anio: int, mes: int) -> dict[int, set[tuple[date, str]]]:
    filas = _todos(
        "SELECT trabajador_id, fecha, tipo FROM trabajadores.comida "
        "WHERE date_trunc('month', fecha) = %s", (date(anio, mes, 1),))
    salida: dict[int, set[tuple[date, str]]] = {}
    for f in filas:
        salida.setdefault(f["trabajador_id"], set()).add((f["fecha"], f["tipo"]))
    return salida


def marcar_comida(trabajador_id: int, fecha: date, tipo: str, marcado_por: str) -> None:
    if tipo not in TIPOS_COMIDA:
        raise ValueError(f"No sé qué comida es «{tipo}».")
    _ejecutar("INSERT INTO trabajadores.comida (trabajador_id, fecha, tipo, marcado_por) "
              "VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING", (trabajador_id, fecha, tipo, marcado_por))


def desmarcar_comida(trabajador_id: int, fecha: date, tipo: str) -> None:
    _ejecutar("DELETE FROM trabajadores.comida WHERE trabajador_id=%s AND fecha=%s AND tipo=%s",
              (trabajador_id, fecha, tipo))


# --------------------------------------------------------------------------
# Usuarios de contabilidad
# --------------------------------------------------------------------------
def usuarios() -> list[dict]:
    return _todos("SELECT id, usuario, nombre, activo, creado_en FROM trabajadores.usuario "
                  "ORDER BY usuario")


def usuario_por_nombre(usuario: str) -> dict | None:
    return _uno("SELECT * FROM trabajadores.usuario WHERE usuario=%s AND activo", (usuario,))


def hay_usuarios() -> bool:
    return _uno("SELECT 1 AS x FROM trabajadores.usuario LIMIT 1") is not None


def crear_usuario(usuario: str, clave_hash: str, nombre: str) -> int:
    fila = _ejecutar("INSERT INTO trabajadores.usuario (usuario, clave_hash, nombre) "
                     "VALUES (%s,%s,%s) RETURNING id", (usuario, clave_hash, nombre))
    return fila["id"]


def cambiar_clave(id_: int, clave_hash: str) -> None:
    _ejecutar("UPDATE trabajadores.usuario SET clave_hash=%s WHERE id=%s", (clave_hash, id_))


def activar_usuario(id_: int, activo: bool) -> None:
    _ejecutar("UPDATE trabajadores.usuario SET activo=%s WHERE id=%s", (activo, id_))

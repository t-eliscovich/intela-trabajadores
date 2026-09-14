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
        -- El perfil. Todo opcional.
        area              text,
        fecha_nacimiento  date,
        celular           text,
        -- Días de vacaciones por año FIJOS para este trabajador (si la empresa
        -- le da más que la ley, o distinto). NULL = la regla de la ley.
        dias_por_anio     numeric(4,1),
        creado_en      timestamptz NOT NULL DEFAULT now()
    );
    -- Columnas que se agregaron después del primer deploy (la tabla ya existía).
    ALTER TABLE trabajadores.trabajador ADD COLUMN IF NOT EXISTS area text;
    ALTER TABLE trabajadores.trabajador ADD COLUMN IF NOT EXISTS fecha_nacimiento date;
    ALTER TABLE trabajadores.trabajador ADD COLUMN IF NOT EXISTS celular text;
    ALTER TABLE trabajadores.trabajador ADD COLUMN IF NOT EXISTS dias_por_anio numeric(4,1);

    ALTER TABLE trabajadores.trabajador ADD COLUMN IF NOT EXISTS direccion text;

    -- Cada período de ausencia. `dias` se propone como los días corridos entre
    -- las dos fechas y contabilidad lo puede corregir. `tipo` dice si descuenta
    -- del saldo (vacaciones, permiso) o sólo se anota (enfermedad, sin goce).
    CREATE TABLE IF NOT EXISTS trabajadores.vacacion (
        id             serial PRIMARY KEY,
        trabajador_id  integer NOT NULL REFERENCES trabajadores.trabajador(id),
        desde          date NOT NULL,
        hasta          date NOT NULL,
        dias           numeric(6,1) NOT NULL,
        nota           text,
        tipo           text NOT NULL DEFAULT 'vacaciones',
        cargado_por    text NOT NULL,
        creado_en      timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS vacacion_trabajador_idx
        ON trabajadores.vacacion (trabajador_id, desde DESC);
    ALTER TABLE trabajadores.vacacion ADD COLUMN IF NOT EXISTS tipo text NOT NULL DEFAULT 'vacaciones';
    -- Nada se borra de verdad: un período o ajuste borrado queda con quién y
    -- cuándo lo borró, se ve en Historial y se puede recuperar.
    ALTER TABLE trabajadores.vacacion ADD COLUMN IF NOT EXISTS borrado_en timestamptz;
    ALTER TABLE trabajadores.vacacion ADD COLUMN IF NOT EXISTS borrado_por text;

    -- Lo que pide el trabajador desde el celular. Contabilidad lo aprueba (y
    -- ahí nace la fila en `vacacion`) o lo rechaza con un motivo.
    CREATE TABLE IF NOT EXISTS trabajadores.solicitud (
        id             serial PRIMARY KEY,
        trabajador_id  integer NOT NULL REFERENCES trabajadores.trabajador(id),
        tipo           text NOT NULL,
        desde          date NOT NULL,
        hasta          date NOT NULL,
        dias           numeric(6,1) NOT NULL,
        nota           text,
        estado         text NOT NULL DEFAULT 'pendiente'
                       CHECK (estado IN ('pendiente', 'aprobada', 'rechazada', 'cancelada')),
        respuesta      text,
        respondido_por text,
        respondido_en  timestamptz,
        vacacion_id    integer REFERENCES trabajadores.vacacion(id) ON DELETE SET NULL,
        creado_en      timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS solicitud_estado_idx ON trabajadores.solicitud (estado, creado_en DESC);

    -- Datos que el trabajador cambió desde su perfil, para que contabilidad
    -- los vea (y los pase a la nómina si hace falta).
    CREATE TABLE IF NOT EXISTS trabajadores.cambio_perfil (
        id             serial PRIMARY KEY,
        trabajador_id  integer NOT NULL REFERENCES trabajadores.trabajador(id),
        campo          text NOT NULL,
        antes          text,
        despues        text,
        visto          boolean NOT NULL DEFAULT false,
        creado_en      timestamptz NOT NULL DEFAULT now()
    );

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
    ALTER TABLE trabajadores.ajuste_vacacion ADD COLUMN IF NOT EXISTS borrado_en timestamptz;
    ALTER TABLE trabajadores.ajuste_vacacion ADD COLUMN IF NOT EXISTS borrado_por text;

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
# Tipos de ausencia: (nombre en pantalla, ¿descuenta del saldo?). Vacaciones y
# permiso descuentan (así lo cuenta la planilla de contabilidad); enfermedad y
# permiso sin goce se anotan pero no tocan el saldo.
TIPOS_AUSENCIA = {
    "vacaciones": ("Vacaciones", True),
    "permiso": ("Permiso", True),
    "enfermedad": ("Enfermedad", False),
    "sin_goce": ("Permiso sin sueldo", False),
}
TIPOS_QUE_DESCUENTAN = tuple(k for k, (_, d) in TIPOS_AUSENCIA.items() if d)

_TRABAJADOR_CON_TOTALES = f"""
    SELECT t.*,
           -- Un período anterior al saldo inicial ya está descontado en ese
           -- saldo: se guarda como historia pero no se resta dos veces.
           -- Sólo descuentan los tipos que descuentan (vacaciones, permiso).
           COALESCE((SELECT SUM(dias) FROM trabajadores.vacacion v
                      WHERE v.trabajador_id = t.id AND v.borrado_en IS NULL
                        AND v.tipo IN {TIPOS_QUE_DESCUENTAN!r}
                        AND (t.fecha_saldo_inicial IS NULL
                             OR v.desde >= t.fecha_saldo_inicial)), 0) AS tomados,
           -- Lo tomado en el año calendario en curso (hoy de Ecuador), incluido
           -- lo anterior al saldo al arrancar: es lo que el trabajador ve como
           -- «tomaste este año».
           COALESCE((SELECT SUM(dias) FROM trabajadores.vacacion v
                      WHERE v.trabajador_id = t.id AND v.borrado_en IS NULL
                        AND v.tipo IN {TIPOS_QUE_DESCUENTAN!r}
                        AND date_part('year', v.desde) =
                            date_part('year', (now() AT TIME ZONE '{config.ZONA}')::date)), 0) AS tomados_anio,
           COALESCE((SELECT SUM(dias) FROM trabajadores.ajuste_vacacion a
                      WHERE a.trabajador_id = t.id AND a.borrado_en IS NULL), 0) AS ajustes
      FROM trabajadores.trabajador t
"""


def trabajadores(incluir_inactivos: bool = False) -> list[dict]:
    filtro = "" if incluir_inactivos else " WHERE t.activo"
    return _todos(_TRABAJADOR_CON_TOTALES + filtro + " ORDER BY t.nombre")


def trabajador(id_: int) -> dict | None:
    return _uno(_TRABAJADOR_CON_TOTALES + " WHERE t.id = %s", (id_,))


def trabajador_por_cedula(cedula: str) -> dict | None:
    return _uno(_TRABAJADOR_CON_TOTALES + " WHERE t.cedula = %s", (cedula,))


PERFIL = ("area", "fecha_nacimiento", "celular", "dias_por_anio", "direccion")


def crear_trabajador(cedula: str, nombre: str, fecha_ingreso: date,
                     saldo_inicial: float | None = None,
                     fecha_saldo_inicial: date | None = None,
                     perfil: dict | None = None) -> int:
    perfil = perfil or {}
    fila = _ejecutar(
        "INSERT INTO trabajadores.trabajador "
        "(cedula, nombre, fecha_ingreso, saldo_inicial, fecha_saldo_inicial, "
        " area, fecha_nacimiento, celular, dias_por_anio, direccion) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (cedula, nombre, fecha_ingreso, saldo_inicial, fecha_saldo_inicial,
         *(perfil.get(k) for k in PERFIL)))
    return fila["id"]


def editar_trabajador(id_: int, cedula: str, nombre: str, fecha_ingreso: date,
                      perfil: dict | None = None) -> None:
    """Datos básicos y, si viene `perfil`, también los del perfil (los cuatro:
    lo que no venga queda en NULL — el formulario los manda siempre todos)."""
    if perfil is None:
        _ejecutar("UPDATE trabajadores.trabajador SET cedula=%s, nombre=%s, fecha_ingreso=%s "
                  "WHERE id=%s", (cedula, nombre, fecha_ingreso, id_))
    else:
        _ejecutar("UPDATE trabajadores.trabajador SET cedula=%s, nombre=%s, fecha_ingreso=%s, "
                  "area=%s, fecha_nacimiento=%s, celular=%s, dias_por_anio=%s, direccion=%s "
                  "WHERE id=%s",
                  (cedula, nombre, fecha_ingreso, *(perfil.get(k) for k in PERFIL), id_))


def cambiar_contacto(id_: int, celular: str | None, direccion: str | None) -> list[dict]:
    """Lo que el trabajador corrige desde su perfil. Devuelve los cambios
    reales (campo, antes, después) y los deja anotados para contabilidad."""
    actual = _uno("SELECT celular, direccion FROM trabajadores.trabajador WHERE id=%s", (id_,))
    cambios = []
    for campo, nuevo in (("celular", celular), ("direccion", direccion)):
        if (actual or {}).get(campo) != nuevo:
            cambios.append({"campo": campo, "antes": (actual or {}).get(campo), "despues": nuevo})
    if not cambios:
        return []
    _ejecutar("UPDATE trabajadores.trabajador SET celular=%s, direccion=%s WHERE id=%s",
              (celular, direccion, id_))
    for c in cambios:
        _ejecutar("INSERT INTO trabajadores.cambio_perfil (trabajador_id, campo, antes, despues) "
                  "VALUES (%s,%s,%s,%s)", (id_, c["campo"], c["antes"], c["despues"]))
    return cambios


def cambios_perfil_sin_ver() -> list[dict]:
    return _todos("SELECT c.*, t.nombre, t.cedula FROM trabajadores.cambio_perfil c "
                  "JOIN trabajadores.trabajador t ON t.id = c.trabajador_id "
                  "WHERE NOT c.visto ORDER BY c.creado_en")


def marcar_cambio_visto(id_: int) -> None:
    _ejecutar("UPDATE trabajadores.cambio_perfil SET visto=true WHERE id=%s", (id_,))


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

    Si la cédula ya existe se actualizan nombre, fecha y perfil; el saldo sólo
    se carga a los NUEVOS, para no pisarlo si se pega la planilla dos veces.
    """
    nuevos = actualizados = 0
    for f in filas:
        perfil = {k: f.get(k) for k in PERFIL}
        existente = trabajador_por_cedula(f["cedula"])
        if existente:
            editar_trabajador(existente["id"], f["cedula"], f["nombre"], f["fecha_ingreso"], perfil)
            actualizados += 1
            continue
        saldo = f.get("saldo")
        crear_trabajador(f["cedula"], f["nombre"], f["fecha_ingreso"],
                         saldo, hoy if saldo is not None else None, perfil)
        nuevos += 1
    return {"nuevos": nuevos, "actualizados": actualizados}


# --------------------------------------------------------------------------
# Vacaciones y ajustes
# --------------------------------------------------------------------------
def vacaciones(trabajador_id: int) -> list[dict]:
    return _todos("SELECT * FROM trabajadores.vacacion WHERE trabajador_id=%s AND borrado_en IS NULL "
                  "ORDER BY desde DESC", (trabajador_id,))


def agregar_vacacion(trabajador_id: int, desde: date, hasta: date, dias: float,
                     nota: str, cargado_por: str, tipo: str = "vacaciones") -> int:
    if tipo not in TIPOS_AUSENCIA:
        raise ValueError(f"No sé qué tipo de ausencia es «{tipo}».")
    fila = _ejecutar(
        "INSERT INTO trabajadores.vacacion (trabajador_id, desde, hasta, dias, nota, cargado_por, tipo) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (trabajador_id, desde, hasta, dias, nota or None, cargado_por, tipo))
    return fila["id"]


def borrar_vacacion(id_: int, quien: str = "?") -> None:
    """No borra: marca. Se ve en Historial y se puede recuperar."""
    _ejecutar("UPDATE trabajadores.vacacion SET borrado_en=now(), borrado_por=%s "
              "WHERE id=%s AND borrado_en IS NULL", (quien, id_))


def recuperar_vacacion(id_: int) -> None:
    _ejecutar("UPDATE trabajadores.vacacion SET borrado_en=NULL, borrado_por=NULL WHERE id=%s", (id_,))


def historial(limite: int = 200) -> list[dict]:
    """Períodos y ajustes borrados, los más recientes primero."""
    return _todos("""
        SELECT 'periodo' AS que, v.id, v.trabajador_id, t.nombre, t.cedula, v.tipo, v.desde, v.hasta,
               v.dias, v.nota, v.cargado_por, v.creado_en, v.borrado_en, v.borrado_por
          FROM trabajadores.vacacion v JOIN trabajadores.trabajador t ON t.id = v.trabajador_id
         WHERE v.borrado_en IS NOT NULL
        UNION ALL
        SELECT 'ajuste', a.id, a.trabajador_id, t.nombre, t.cedula, NULL, NULL, NULL,
               a.dias, a.motivo, a.cargado_por, a.creado_en, a.borrado_en, a.borrado_por
          FROM trabajadores.ajuste_vacacion a JOIN trabajadores.trabajador t ON t.id = a.trabajador_id
         WHERE a.borrado_en IS NOT NULL
         ORDER BY borrado_en DESC LIMIT %s""", (limite,))


# --------------------------------------------------------------------------
# Pedidos del trabajador
# --------------------------------------------------------------------------
def solicitudes(trabajador_id: int) -> list[dict]:
    return _todos("SELECT * FROM trabajadores.solicitud WHERE trabajador_id=%s "
                  "ORDER BY creado_en DESC LIMIT 30", (trabajador_id,))


def solicitud(id_: int) -> dict | None:
    return _uno("SELECT s.*, t.nombre, t.cedula, t.celular FROM trabajadores.solicitud s "
                "JOIN trabajadores.trabajador t ON t.id = s.trabajador_id WHERE s.id=%s", (id_,))


def solicitudes_pendientes() -> list[dict]:
    return _todos("SELECT s.*, t.nombre, t.cedula, t.celular, t.area FROM trabajadores.solicitud s "
                  "JOIN trabajadores.trabajador t ON t.id = s.trabajador_id "
                  "WHERE s.estado = 'pendiente' ORDER BY s.creado_en")


def solicitudes_respondidas(limite: int = 40) -> list[dict]:
    return _todos("SELECT s.*, t.nombre, t.cedula, t.celular FROM trabajadores.solicitud s "
                  "JOIN trabajadores.trabajador t ON t.id = s.trabajador_id "
                  "WHERE s.estado <> 'pendiente' ORDER BY s.respondido_en DESC NULLS LAST, "
                  "s.creado_en DESC LIMIT %s", (limite,))


def cuantas_pendientes() -> int:
    fila = _uno("SELECT COUNT(*) AS n FROM trabajadores.solicitud WHERE estado='pendiente'")
    return int(fila["n"]) if fila else 0


def crear_solicitud(trabajador_id: int, tipo: str, desde: date, hasta: date, dias: float,
                    nota: str) -> int:
    if tipo not in TIPOS_AUSENCIA:
        raise ValueError(f"No sé qué tipo de ausencia es «{tipo}».")
    fila = _ejecutar(
        "INSERT INTO trabajadores.solicitud (trabajador_id, tipo, desde, hasta, dias, nota) "
        "VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
        (trabajador_id, tipo, desde, hasta, dias, nota or None))
    return fila["id"]


def cancelar_solicitud_aprobada(id_: int, respuesta: str, quien: str) -> None:
    """Un pedido aprobado que se cancela: el período se borra (queda en el
    historial) y el pedido pasa a «cancelada» con quién y por qué."""
    p = solicitud(id_)
    if not p or p["estado"] != "aprobada":
        return
    if p["vacacion_id"]:
        borrar_vacacion(p["vacacion_id"], quien)
    _ejecutar("UPDATE trabajadores.solicitud SET estado='cancelada', respuesta=%s, respondido_por=%s, "
              "respondido_en=now() WHERE id=%s", (respuesta, quien, id_))


def responder_solicitud(id_: int, estado: str, respuesta: str, quien: str,
                        vacacion_id: int | None = None) -> None:
    _ejecutar("UPDATE trabajadores.solicitud SET estado=%s, respuesta=%s, respondido_por=%s, "
              "respondido_en=now(), vacacion_id=%s WHERE id=%s AND estado='pendiente'",
              (estado, respuesta or None, quien, vacacion_id, id_))


def ajustes(trabajador_id: int) -> list[dict]:
    return _todos("SELECT * FROM trabajadores.ajuste_vacacion WHERE trabajador_id=%s AND borrado_en IS NULL "
                  "ORDER BY creado_en DESC", (trabajador_id,))


def agregar_ajuste(trabajador_id: int, dias: float, motivo: str, cargado_por: str) -> int:
    fila = _ejecutar(
        "INSERT INTO trabajadores.ajuste_vacacion (trabajador_id, dias, motivo, cargado_por) "
        "VALUES (%s,%s,%s,%s) RETURNING id", (trabajador_id, dias, motivo, cargado_por))
    return fila["id"]


def borrar_ajuste(id_: int, quien: str = "?") -> None:
    _ejecutar("UPDATE trabajadores.ajuste_vacacion SET borrado_en=now(), borrado_por=%s "
              "WHERE id=%s AND borrado_en IS NULL", (quien, id_))


def recuperar_ajuste(id_: int) -> None:
    _ejecutar("UPDATE trabajadores.ajuste_vacacion SET borrado_en=NULL, borrado_por=NULL WHERE id=%s", (id_,))


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

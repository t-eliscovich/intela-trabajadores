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
from psycopg2.pool import ThreadedConnectionPool

import config

_pool: ThreadedConnectionPool | None = None


def init_pool() -> None:
    global _pool
    if not config.DATABASE_URL:
        raise RuntimeError("Falta TRABAJADORES_DATABASE_URL")
    # Waitress atiende con varios hilos: el pool tiene que ser el de hilos.
    _pool = ThreadedConnectionPool(1, 6, config.DATABASE_URL)
    bootstrap()


@contextmanager
def _conn():
    assert _pool is not None, "init_pool() no fue llamado"
    con = _pool.getconn()
    # Una conexión que quedó mucho rato quieta la puede cerrar el RDS. El primer
    # pedido después de eso daba «Internal Server Error» (13/10/2026, al entrar a
    # contabilidad). Se prueba con un SELECT 1 y, si está muerta, se cambia por otra.
    try:
        if con.closed:
            raise psycopg2.InterfaceError("conexión cerrada")
        with con.cursor() as cur:
            cur.execute("SELECT 1")
        con.rollback()
    except (psycopg2.OperationalError, psycopg2.InterfaceError):
        _pool.putconn(con, close=True)
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
    -- turno: el horario del almuerzo ('12:00', '12:30', '13:00', '13:30'); la cena por ahora sin turno.
    ALTER TABLE trabajadores.comida ADD COLUMN IF NOT EXISTS turno text;
    -- Sólo algunos trabajadores pueden traer invitados (se marca en la ficha).
    ALTER TABLE trabajadores.trabajador ADD COLUMN IF NOT EXISTS puede_invitar boolean NOT NULL DEFAULT false;

    -- Invitados que trae un trabajador (proveedor, familiar): sin cédula, con
    -- una descripción. Se pagan aparte de las comidas de los trabajadores.
    CREATE TABLE IF NOT EXISTS trabajadores.invitado (
        id             serial PRIMARY KEY,
        trabajador_id  integer NOT NULL REFERENCES trabajadores.trabajador(id),
        fecha          date NOT NULL,
        tipo           text NOT NULL CHECK (tipo IN ('almuerzo', 'cena')),
        cantidad       integer NOT NULL CHECK (cantidad BETWEEN 1 AND 3),
        descripcion    text NOT NULL,
        cargado_por    text NOT NULL,
        creado_en      timestamptz NOT NULL DEFAULT now(),
        borrado_en     timestamptz,
        turno          text
    );
    CREATE INDEX IF NOT EXISTS invitado_fecha_idx ON trabajadores.invitado (fecha);
    -- (la tabla ya existía en producción sin `turno`; el ALTER va DESPUÉS del CREATE)
    ALTER TABLE trabajadores.invitado ADD COLUMN IF NOT EXISTS turno text;

    -- Feriados: para que el resumen de comidas los muestre en gris.
    CREATE TABLE IF NOT EXISTS trabajadores.feriado (
        fecha   date PRIMARY KEY,
        nombre  text NOT NULL
    );
    INSERT INTO trabajadores.feriado (fecha, nombre) VALUES
        ('2026-01-01', 'Año Nuevo'), ('2026-02-16', 'Carnaval'), ('2026-02-17', 'Carnaval'),
        ('2026-04-03', 'Viernes Santo'), ('2026-05-01', 'Día del Trabajo'),
        ('2026-05-25', 'Batalla de Pichincha'), ('2026-08-10', 'Primer Grito de Independencia'),
        ('2026-10-09', 'Independencia de Guayaquil'), ('2026-11-02', 'Día de los Difuntos'),
        ('2026-11-03', 'Independencia de Cuenca'), ('2026-12-25', 'Navidad')
        ON CONFLICT DO NOTHING;

    -- La foto del certificado médico (o el PDF), a mano para contabilidad.
    -- Va ligada al trabajador, y si se sabe, al pedido y al período.
    CREATE TABLE IF NOT EXISTS trabajadores.certificado (
        id             serial PRIMARY KEY,
        trabajador_id  integer NOT NULL REFERENCES trabajadores.trabajador(id),
        solicitud_id   integer REFERENCES trabajadores.solicitud(id) ON DELETE SET NULL,
        vacacion_id    integer REFERENCES trabajadores.vacacion(id) ON DELETE SET NULL,
        tipo_archivo   text NOT NULL,
        nombre         text NOT NULL,
        datos          bytea NOT NULL,
        subido_por     text NOT NULL,
        subido_en      timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS certificado_trabajador_idx ON trabajadores.certificado (trabajador_id);

    -- El rol de pagos de cada mes, como lo exporta WinAfin (Rol → «rolN.xls»).
    -- Se guarda lo que ve el trabajador: ingresos y descuentos con su nombre
    -- (jsonb: [{concepto, detalle, valor}]), totales y líquido. Las provisiones
    -- NO. Volver a cargar un mes marca el anterior como borrado (nunca DELETE).
    CREATE TABLE IF NOT EXISTS trabajadores.rol_pago (
        id                serial PRIMARY KEY,
        trabajador_id     integer NOT NULL REFERENCES trabajadores.trabajador(id),
        anio              integer NOT NULL,
        mes               integer NOT NULL CHECK (mes BETWEEN 1 AND 12),
        codigo            integer,
        nombre_rol        text NOT NULL,
        sueldo            numeric(12,2),
        dias              numeric(5,1),
        horas_25          numeric(7,2),
        horas_50          numeric(7,2),
        horas_100         numeric(7,2),
        atrasos           numeric(7,2),
        otros             numeric(7,2),
        ingresos          jsonb NOT NULL,
        descuentos        jsonb NOT NULL,
        total_ingresos    numeric(12,2) NOT NULL,
        total_descuentos  numeric(12,2) NOT NULL,
        liquido           numeric(12,2) NOT NULL,
        cargado_por       text NOT NULL,
        cargado_en        timestamptz NOT NULL DEFAULT now(),
        borrado_en        timestamptz,
        borrado_por       text
    );
    CREATE UNIQUE INDEX IF NOT EXISTS rol_pago_vivo_idx
        ON trabajadores.rol_pago (trabajador_id, anio, mes) WHERE borrado_en IS NULL;
    CREATE INDEX IF NOT EXISTS rol_pago_mes_idx ON trabajadores.rol_pago (anio, mes);
    -- La firma del trabajador en la app («Recibí conforme»): la imagen del trazo
    -- (PNG), cuándo y desde qué celular. Una vez firmado no se cambia.
    ALTER TABLE trabajadores.rol_pago ADD COLUMN IF NOT EXISTS firma bytea;
    ALTER TABLE trabajadores.rol_pago ADD COLUMN IF NOT EXISTS firmado_en timestamptz;
    ALTER TABLE trabajadores.rol_pago ADD COLUMN IF NOT EXISTS firmado_desde text;
    ALTER TABLE trabajadores.rol_pago ADD COLUMN IF NOT EXISTS firma_huella text;
    -- Los meses que se cargan como historial (rol acumulado) ya se pagaron: se
    -- muestran sin pedir firma.
    ALTER TABLE trabajadores.rol_pago ADD COLUMN IF NOT EXISTS pide_firma boolean NOT NULL DEFAULT true;
    -- El código del trabajador en WinAfin (jno). Se guarda al cargar el primer
    -- rol y de ahí en adelante el cruce va por código, no por nombre.
    ALTER TABLE trabajadores.trabajador ADD COLUMN IF NOT EXISTS codigo_rol integer;
    -- Y el nombre tal como está escrito en WinAfin (puede ser distinto del padrón).
    -- El mes siguiente entra solo si el código Y este nombre coinciden.
    ALTER TABLE trabajadores.trabajador ADD COLUMN IF NOT EXISTS nombre_rol text;
    -- Los roles cargados antes de que existiera `nombre_rol` (septiembre 2026):
    -- se completa con el nombre de WinAfin del último rol de cada uno.
    UPDATE trabajadores.trabajador t SET nombre_rol = r.nombre_rol
      FROM (SELECT DISTINCT ON (trabajador_id) trabajador_id, nombre_rol FROM trabajadores.rol_pago
             WHERE borrado_en IS NULL ORDER BY trabajador_id, anio DESC, mes DESC) r
     WHERE r.trabajador_id = t.id AND t.nombre_rol IS NULL;

    -- La CONSTANCIA DE CONOCIMIENTO Y ACCESO A ROLES DE PAGO (Tamara 13/10/2026):
    -- el trabajador la firma una vez en la app antes de ver sus roles. Se guarda
    -- el texto EXACTO que firmó (con banco y cuenta) y su huella. Contabilidad la
    -- puede anular (cambió de cuenta) y el trabajador la vuelve a firmar; nunca DELETE.
    CREATE TABLE IF NOT EXISTS trabajadores.constancia_roles (
        id             serial PRIMARY KEY,
        trabajador_id  integer NOT NULL REFERENCES trabajadores.trabajador(id),
        version        integer NOT NULL,
        banco          text NOT NULL,
        cuenta         text NOT NULL,
        texto          text NOT NULL,
        huella         text NOT NULL,
        firma          bytea NOT NULL,
        firmado_en     timestamptz NOT NULL DEFAULT now(),
        firmado_desde  text,
        anulada_en     timestamptz,
        anulada_por    text,
        motivo_anulada text
    );
    CREATE UNIQUE INDEX IF NOT EXISTS constancia_roles_viva_idx
        ON trabajadores.constancia_roles (trabajador_id) WHERE anulada_en IS NULL;

    -- Cosas sueltas del programa (la clave del link del comedor, por ejemplo).
    CREATE TABLE IF NOT EXISTS trabajadores.configuracion (
        clave  text PRIMARY KEY,
        valor  text NOT NULL
    );

    -- Quién entra a la parte de contabilidad, y el comedor.
    -- rol: 'contabilidad' (todo) o 'comedor' (marcar comidas y ver quién comió).
    CREATE TABLE IF NOT EXISTS trabajadores.usuario (
        id          serial PRIMARY KEY,
        usuario     text NOT NULL UNIQUE,
        clave_hash  text NOT NULL,
        nombre      text NOT NULL,
        activo      boolean NOT NULL DEFAULT true,
        rol         text NOT NULL DEFAULT 'contabilidad',
        creado_en   timestamptz NOT NULL DEFAULT now()
    );
    ALTER TABLE trabajadores.usuario ADD COLUMN IF NOT EXISTS rol text NOT NULL DEFAULT 'contabilidad';
    UPDATE trabajadores.usuario SET rol = 'comedor' WHERE rol = 'cafeteria';

    -- Tipo de trabajador (Tamara 14/10/2026): 'planta' (vacaciones por ley),
    -- 'temporal' (comedor y rol, SIN vacaciones hasta pasar a planta) y
    -- 'jubilado' (rol de pagos; sin vacaciones, como en la planilla de contabilidad).
    ALTER TABLE trabajadores.trabajador ADD COLUMN IF NOT EXISTS tipo text NOT NULL DEFAULT 'planta';
    -- Pausado: no se borra ni se da de baja; no entra ni se registra en el comedor
    -- hasta que contabilidad lo reanude («que se pausen», Tamara 14/10/2026).
    ALTER TABLE trabajadores.trabajador ADD COLUMN IF NOT EXISTS pausado_en timestamptz;
    ALTER TABLE trabajadores.trabajador ADD COLUMN IF NOT EXISTS pausado_por text;
    ALTER TABLE trabajadores.trabajador ADD COLUMN IF NOT EXISTS motivo_pausa text;
    -- Se puede cargar a alguien sin cédula (queda un aviso en la campanita hasta ponerla).
    ALTER TABLE trabajadores.trabajador ALTER COLUMN cedula DROP NOT NULL;

    -- La campanita de contabilidad: cosas para revisar. `clave` agrupa los avisos
    -- automáticos (sin_cedula, no_en_planilla) para no repetirlos y cerrarlos solos;
    -- 'nota' = uno que dejó alguien a mano.
    CREATE TABLE IF NOT EXISTS trabajadores.aviso (
        id             serial PRIMARY KEY,
        trabajador_id  integer REFERENCES trabajadores.trabajador(id),
        clave          text NOT NULL DEFAULT 'nota',
        texto          text NOT NULL,
        creado_en      timestamptz NOT NULL DEFAULT now(),
        creado_por     text NOT NULL,
        resuelto_en    timestamptz,
        resuelto_por   text
    );
    CREATE INDEX IF NOT EXISTS aviso_abierto_idx ON trabajadores.aviso (resuelto_en, creado_en DESC);

    -- REVISIÓN Y FIRMA DEL ROL (Tamara 14/10/2026): «primero Jessica hace un check en la
    -- tabla y luego Ángel tiene que poder hacer check también y firmar masivamente».
    -- Recibo por recibo: revisado (Jessica) → firmado por la empresa (Ángel, con su firma
    -- guardada) → recién ahí lo ve el trabajador. Lo cargado antes de esto (septiembre y
    -- el historial) queda con necesita_aprobacion = false: ya estaba publicado.
    ALTER TABLE trabajadores.rol_pago ADD COLUMN IF NOT EXISTS necesita_aprobacion boolean NOT NULL DEFAULT false;
    ALTER TABLE trabajadores.rol_pago ADD COLUMN IF NOT EXISTS revisado_por_id integer;
    ALTER TABLE trabajadores.rol_pago ADD COLUMN IF NOT EXISTS revisado_por text;
    ALTER TABLE trabajadores.rol_pago ADD COLUMN IF NOT EXISTS revisado_en timestamptz;
    ALTER TABLE trabajadores.rol_pago ADD COLUMN IF NOT EXISTS aprobado_por_id integer;
    ALTER TABLE trabajadores.rol_pago ADD COLUMN IF NOT EXISTS aprobado_por text;
    ALTER TABLE trabajadores.rol_pago ADD COLUMN IF NOT EXISTS aprobado_en timestamptz;
    -- Una COPIA de la firma de quien aprobó: si después cambia su firma, los recibos
    -- ya firmados siguen mostrando la que tenían.
    ALTER TABLE trabajadores.rol_pago ADD COLUMN IF NOT EXISTS firma_empresa bytea;
    -- La firma guardada de cada usuario (la dibuja o la sube una vez) y quién puede hacer qué.
    ALTER TABLE trabajadores.usuario ADD COLUMN IF NOT EXISTS firma bytea;
    ALTER TABLE trabajadores.usuario ADD COLUMN IF NOT EXISTS firma_en timestamptz;
    ALTER TABLE trabajadores.usuario ADD COLUMN IF NOT EXISTS revisa_roles boolean NOT NULL DEFAULT false;
    ALTER TABLE trabajadores.usuario ADD COLUMN IF NOT EXISTS firma_roles boolean NOT NULL DEFAULT false;
    -- Los permisos de arranque (Tamara 14/10: «si Ángel y Jessica ya lo tienen, dales el
    -- acceso directo»). Sólo si hay UNA usuaria/UN usuario activo que se llame así, y una
    -- sola vez: si después se le saca el permiso desde Usuarios, no vuelve solo.
    UPDATE trabajadores.usuario SET revisa_roles = true
     WHERE id = (SELECT min(id) FROM trabajadores.usuario
                  WHERE activo AND rol = 'contabilidad' AND (lower(nombre) LIKE '%jessica%' OR lower(usuario) LIKE '%jessica%')
                 HAVING count(*) = 1)
       AND NOT EXISTS (SELECT 1 FROM trabajadores.configuracion WHERE clave = 'permiso_revisa_roles_inicial');
    INSERT INTO trabajadores.configuracion (clave, valor)
         SELECT 'permiso_revisa_roles_inicial', 'hecho'
          WHERE EXISTS (SELECT 1 FROM trabajadores.usuario WHERE revisa_roles)
    ON CONFLICT (clave) DO NOTHING;
    UPDATE trabajadores.usuario SET firma_roles = true
     WHERE id = (SELECT min(id) FROM trabajadores.usuario
                  WHERE activo AND rol = 'contabilidad'
                    AND (lower(nombre) LIKE '%angel%' OR lower(nombre) LIKE '%ángel%'
                         OR lower(usuario) LIKE '%angel%' OR lower(usuario) LIKE '%ángel%')
                 HAVING count(*) = 1)
       AND NOT EXISTS (SELECT 1 FROM trabajadores.configuracion WHERE clave = 'permiso_firma_roles_inicial');
    INSERT INTO trabajadores.configuracion (clave, valor)
         SELECT 'permiso_firma_roles_inicial', 'hecho'
          WHERE EXISTS (SELECT 1 FROM trabajadores.usuario WHERE firma_roles)
    ON CONFLICT (clave) DO NOTHING;

    -- CONTRASEÑA DEL ROL DE PAGOS (Tamara 14/10/2026): para ver el rol, el trabajador
    -- pone cédula y correo, le llega un código y registra su contraseña. Las vacaciones
    -- siguen entrando sólo con la cédula.
    ALTER TABLE trabajadores.trabajador ADD COLUMN IF NOT EXISTS email text;
    ALTER TABLE trabajadores.trabajador ADD COLUMN IF NOT EXISTS clave_rol_hash text;
    ALTER TABLE trabajadores.trabajador ADD COLUMN IF NOT EXISTS clave_rol_en timestamptz;
    ALTER TABLE trabajadores.trabajador ADD COLUMN IF NOT EXISTS clave_rol_fallidos integer NOT NULL DEFAULT 0;
    ALTER TABLE trabajadores.trabajador ADD COLUMN IF NOT EXISTS clave_rol_trabado_hasta timestamptz;
    -- Los códigos de 6 números que llegan por correo. Van CIFRADOS, como una clave.
    CREATE TABLE IF NOT EXISTS trabajadores.codigo_correo (
        id             serial PRIMARY KEY,
        trabajador_id  integer NOT NULL REFERENCES trabajadores.trabajador(id),
        email          text NOT NULL,
        codigo_hash    text NOT NULL,
        creado_en      timestamptz NOT NULL DEFAULT now(),
        vence_en       timestamptz NOT NULL,
        intentos       integer NOT NULL DEFAULT 0,
        usado_en       timestamptz
    );
    CREATE INDEX IF NOT EXISTS codigo_correo_trab_idx ON trabajadores.codigo_correo (trabajador_id, creado_en DESC);
"""

TIPOS_TRABAJADOR = {"planta": "Planta", "temporal": "Temporal", "jubilado": "Jubilado"}


# --------------------------------------------------------------------------
# Rol de pagos
# --------------------------------------------------------------------------
_ROL = """SELECT r.id, r.trabajador_id, r.anio, r.mes, r.codigo, r.nombre_rol, r.sueldo, r.dias,
                 r.horas_25, r.horas_50, r.horas_100, r.atrasos, r.otros, r.ingresos, r.descuentos,
                 r.total_ingresos, r.total_descuentos, r.liquido, r.cargado_por, r.cargado_en,
                 r.firmado_en, r.firmado_desde, r.firma_huella, r.pide_firma,
                 r.necesita_aprobacion, r.revisado_por_id, r.revisado_por, r.revisado_en,
                 r.aprobado_por_id, r.aprobado_por, r.aprobado_en, t.nombre, t.cedula, t.area, t.celular,
                 t.fecha_ingreso, t.fecha_salida
            FROM trabajadores.rol_pago r JOIN trabajadores.trabajador t ON t.id = r.trabajador_id
           WHERE r.borrado_en IS NULL"""


def _rol_python(r: dict | None) -> dict | None:
    if r is None:
        return None
    for k in ("sueldo", "dias", "horas_25", "horas_50", "horas_100", "atrasos", "otros",
              "total_ingresos", "total_descuentos", "liquido"):
        if r.get(k) is not None:
            r[k] = float(r[k])
    return r


def roles_del_mes(anio: int, mes: int) -> list[dict]:
    return [_rol_python(r) for r in _todos(_ROL + " AND r.anio = %s AND r.mes = %s ORDER BY t.nombre", (anio, mes))]


def rol_pago(id_: int) -> dict | None:
    return _rol_python(_uno(_ROL + " AND r.id = %s", (id_,)))


def roles_de(trabajador_id: int) -> list[dict]:
    """Los roles de un trabajador, el más nuevo primero."""
    return [_rol_python(r) for r in _todos(_ROL + " AND r.trabajador_id = %s ORDER BY r.anio DESC, r.mes DESC",
                                           (trabajador_id,))]


def visible(r: dict) -> bool:
    """¿Lo ve el trabajador? Lo cargado antes de la revisión, sí; lo nuevo, cuando
    la empresa lo firmó."""
    return not r.get("necesita_aprobacion") or r.get("aprobado_en") is not None


def meses_con_rol() -> list[dict]:
    return [dict(r, total=float(r["total"])) for r in _todos(
        """SELECT anio, mes, count(*) AS cuantos, sum(liquido) AS total, max(cargado_en) AS cargado_en,
                  count(firmado_en) AS firmados, count(*) FILTER (WHERE pide_firma) AS piden_firma,
                  count(*) FILTER (WHERE necesita_aprobacion) AS necesitan,
                  count(*) FILTER (WHERE necesita_aprobacion AND revisado_en IS NOT NULL) AS revisados,
                  count(*) FILTER (WHERE necesita_aprobacion AND aprobado_en IS NOT NULL) AS aprobados
             FROM trabajadores.rol_pago WHERE borrado_en IS NULL
            GROUP BY anio, mes ORDER BY anio DESC, mes DESC""")]


def cargar_roles(anio: int, mes: int, filas: list[dict], quien: str, pide_firma: bool = True) -> dict:
    """Carga el rol del mes ENTERO, en una sola transacción: lo que hubiera de
    ese mes queda borrado (se reemplaza, nunca DELETE), entra cada fila y a cada
    trabajador se le guarda su código de WinAfin para el mes que viene."""
    import json
    with _conn() as con, con.cursor() as cur:
        try:
            # Si el recibo de alguien que ya firmó vuelve IGUAL, la firma se queda;
            # si cambió algo, tiene que volver a firmar.
            cur.execute("""SELECT trabajador_id, ingresos, descuentos, liquido, firma, firmado_en, firmado_desde,
                                  firma_huella, sueldo, dias, atrasos, otros, necesita_aprobacion,
                                  revisado_por_id, revisado_por, revisado_en, aprobado_por_id, aprobado_por,
                                  aprobado_en, firma_empresa
                             FROM trabajadores.rol_pago
                            WHERE anio = %s AND mes = %s AND borrado_en IS NULL""",
                        (anio, mes))
            previos = {r[0]: r for r in cur.fetchall()}
            firmados = {k: r for k, r in previos.items() if r[5] is not None}
            cur.execute("""UPDATE trabajadores.rol_pago SET borrado_en = now(), borrado_por = %s
                            WHERE anio = %s AND mes = %s AND borrado_en IS NULL""", (quien, anio, mes))
            reemplazados = cur.rowcount
            firmas_que_quedan = 0
            for f in filas:
                cur.execute(
                    """INSERT INTO trabajadores.rol_pago
                         (trabajador_id, anio, mes, codigo, nombre_rol, sueldo, dias, horas_25, horas_50,
                          horas_100, atrasos, otros, ingresos, descuentos, total_ingresos, total_descuentos,
                          liquido, cargado_por, pide_firma, necesita_aprobacion)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s,%s)""",
                    (f["trabajador_id"], anio, mes, f["codigo"], f["nombre_rol"], f["sueldo"], f["dias"],
                     f["horas_25"], f["horas_50"], f["horas_100"], f["atrasos"], f["otros"],
                     json.dumps(f["ingresos"]), json.dumps(f["descuentos"]),
                     f["total_ingresos"], f["total_descuentos"], f["liquido"], quien, pide_firma, pide_firma))
                antes = previos.get(f["trabajador_id"])
                if antes and mismo_recibo({"ingresos": antes[1], "descuentos": antes[2], "liquido": antes[3],
                                           "sueldo": antes[8], "dias": antes[9], "atrasos": antes[10],
                                           "otros": antes[11]}, f):
                    # El recibo volvió IGUAL: se queda todo lo que ya tenía (la firma del
                    # trabajador, la revisión y la firma de la empresa, y si ya estaba publicado).
                    cur.execute("""UPDATE trabajadores.rol_pago
                                      SET firma = %s, firmado_en = %s, firmado_desde = %s, firma_huella = %s,
                                          necesita_aprobacion = %s, revisado_por_id = %s, revisado_por = %s,
                                          revisado_en = %s, aprobado_por_id = %s, aprobado_por = %s,
                                          aprobado_en = %s, firma_empresa = %s
                                    WHERE trabajador_id = %s AND anio = %s AND mes = %s AND borrado_en IS NULL""",
                                (antes[4], antes[5], antes[6], antes[7], antes[12] and pide_firma, antes[13],
                                 antes[14], antes[15], antes[16], antes[17], antes[18], antes[19],
                                 f["trabajador_id"], anio, mes))
                    if antes[5] is not None:
                        firmas_que_quedan += 1
                cur.execute("""UPDATE trabajadores.trabajador SET codigo_rol = NULL
                                WHERE codigo_rol = %s AND id <> %s""", (f["codigo"], f["trabajador_id"]))
                cur.execute("UPDATE trabajadores.trabajador SET codigo_rol = %s, nombre_rol = %s WHERE id = %s",
                            (f["codigo"], f["nombre_rol"], f["trabajador_id"]))
            con.commit()
        except Exception:
            con.rollback()
            raise
    presentes = {f["trabajador_id"] for f in filas}
    return {"cargados": len(filas), "reemplazados": reemplazados,
            "firmas_perdidas": len(set(firmados) & presentes) - firmas_que_quedan,
            "firmados_quitados": len(set(firmados) - presentes)}


def mismo_recibo(antes: dict, f: dict) -> bool:
    """Todo lo que el trabajador ve en el recibo es igual: si cambió cualquier
    cosa (también el sueldo nominal o los atrasos), tiene que volver a firmar."""
    if antes["ingresos"] != f["ingresos"] or antes["descuentos"] != f["descuentos"]:
        return False
    return all(abs(float(antes[k] or 0) - float(f[k] or 0)) < 0.005
               for k in ("liquido", "sueldo", "dias", "atrasos", "otros"))


def firmar_rol(id_: int, trabajador_id: int, png: bytes, desde: str, huella: str) -> bool:
    """Sólo el dueño del recibo, y una sola vez."""
    fila = _ejecutar("""UPDATE trabajadores.rol_pago
                           SET firma = %s, firmado_en = now(), firmado_desde = %s, firma_huella = %s
                         WHERE id = %s AND trabajador_id = %s AND borrado_en IS NULL AND firmado_en IS NULL
                           AND pide_firma
                     RETURNING id""", (psycopg2.Binary(png), desde[:200], huella, id_, trabajador_id))
    return fila is not None


def revisar_roles(ids: list[int], usuario_id: int, nombre: str) -> int:
    """Jessica marca ✓ los recibos que revisó. Sólo los que todavía no estaban."""
    if not ids:
        return 0
    with _conn() as con, con.cursor() as cur:
        try:
            cur.execute("""UPDATE trabajadores.rol_pago
                              SET revisado_por_id = %s, revisado_por = %s, revisado_en = now()
                            WHERE id = ANY(%s) AND borrado_en IS NULL AND necesita_aprobacion
                              AND revisado_en IS NULL AND aprobado_en IS NULL""",
                        (usuario_id, nombre, list(ids)))
            n = cur.rowcount
            con.commit()
        except Exception:
            con.rollback()
            raise
    return n


def quitar_revision(ids: list[int]) -> int:
    """Se saca el ✓ de revisado, mientras la empresa no lo haya firmado."""
    if not ids:
        return 0
    with _conn() as con, con.cursor() as cur:
        try:
            cur.execute("""UPDATE trabajadores.rol_pago
                              SET revisado_por_id = NULL, revisado_por = NULL, revisado_en = NULL
                            WHERE id = ANY(%s) AND borrado_en IS NULL AND aprobado_en IS NULL""", (list(ids),))
            n = cur.rowcount
            con.commit()
        except Exception:
            con.rollback()
            raise
    return n


def aprobar_roles(ids: list[int], usuario_id: int, nombre: str, firma: bytes) -> int:
    """Ángel firma de una vez los recibos elegidos con su firma guardada. Sólo los que
    ya están revisados, y nunca los que revisó él mismo (son dos personas)."""
    if not ids:
        return 0
    with _conn() as con, con.cursor() as cur:
        try:
            cur.execute("""UPDATE trabajadores.rol_pago
                              SET aprobado_por_id = %s, aprobado_por = %s, aprobado_en = now(), firma_empresa = %s
                            WHERE id = ANY(%s) AND borrado_en IS NULL AND necesita_aprobacion
                              AND revisado_en IS NOT NULL AND aprobado_en IS NULL
                              AND revisado_por_id IS DISTINCT FROM %s""",
                        (usuario_id, nombre, psycopg2.Binary(firma), list(ids), usuario_id))
            n = cur.rowcount
            con.commit()
        except Exception:
            con.rollback()
            raise
    return n


def firma_empresa_del_rol(id_: int) -> bytes | None:
    r = _uno("SELECT firma_empresa FROM trabajadores.rol_pago WHERE id = %s AND borrado_en IS NULL", (id_,))
    return bytes(r["firma_empresa"]) if r and r["firma_empresa"] else None


def firmado_en_papel(anio: int, mes: int) -> int:
    """Contabilidad avisa que el mes ya se firmó en papel: deja de pedir firma en
    la app a los que no firmaron (las firmas hechas en la app se quedan)."""
    with _conn() as con, con.cursor() as cur:
        try:
            # firmado en papel = también lo firmó la empresa en papel: se publica.
            cur.execute("""UPDATE trabajadores.rol_pago SET pide_firma = false, necesita_aprobacion = false
                            WHERE anio = %s AND mes = %s AND borrado_en IS NULL
                              AND pide_firma AND firmado_en IS NULL""", (anio, mes))
            n = cur.rowcount
            con.commit()
        except Exception:
            con.rollback()
            raise
    return n


def sin_firmar_ultimo_mes() -> int:
    """Para el globito del menú de contabilidad: cuántos del último mes cargado
    todavía no firmaron."""
    r = _uno("""SELECT count(*) FILTER (WHERE firmado_en IS NULL AND pide_firma
                                        AND (NOT necesita_aprobacion OR aprobado_en IS NOT NULL)) AS n
                  FROM trabajadores.rol_pago
                 WHERE borrado_en IS NULL AND (anio, mes) = (
                       SELECT anio, mes FROM trabajadores.rol_pago WHERE borrado_en IS NULL
                        ORDER BY anio DESC, mes DESC LIMIT 1)""")
    return int(r["n"]) if r else 0


_CONSTANCIA = """SELECT c.id, c.trabajador_id, c.version, c.banco, c.cuenta, c.texto, c.huella, c.firmado_en,
                        c.firmado_desde, c.anulada_en, c.anulada_por, c.motivo_anulada,
                        t.nombre, t.cedula, t.area, t.celular
                   FROM trabajadores.constancia_roles c JOIN trabajadores.trabajador t ON t.id = c.trabajador_id"""


def constancia_de(trabajador_id: int) -> dict | None:
    """La constancia vigente (no anulada) del trabajador, o None."""
    return _uno(_CONSTANCIA + " WHERE c.trabajador_id = %s AND c.anulada_en IS NULL", (trabajador_id,))


def constancia(id_: int) -> dict | None:
    return _uno(_CONSTANCIA + " WHERE c.id = %s", (id_,))


def constancias_vigentes() -> dict[int, dict]:
    return {c["trabajador_id"]: c for c in _todos(_CONSTANCIA + " WHERE c.anulada_en IS NULL")}


def firmar_constancia(trabajador_id: int, version: int, banco: str, cuenta: str, texto: str, huella: str,
                      png: bytes, desde: str) -> int | None:
    """Una sola vigente por trabajador (índice único): si ya firmó, no hace nada."""
    try:
        fila = _ejecutar("""INSERT INTO trabajadores.constancia_roles
                              (trabajador_id, version, banco, cuenta, texto, huella, firma, firmado_desde)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                         (trabajador_id, version, banco, cuenta, texto, huella, psycopg2.Binary(png), desde[:200]))
    except psycopg2.IntegrityError:
        return None
    return fila["id"] if fila else None


def anular_constancia(id_: int, quien: str, motivo: str) -> bool:
    fila = _ejecutar("""UPDATE trabajadores.constancia_roles SET anulada_en = now(), anulada_por = %s, motivo_anulada = %s
                         WHERE id = %s AND anulada_en IS NULL RETURNING id""", (quien, motivo[:200], id_))
    return fila is not None


def firma_de_constancia(id_: int) -> bytes | None:
    r = _uno("SELECT firma FROM trabajadores.constancia_roles WHERE id = %s", (id_,))
    return bytes(r["firma"]) if r and r["firma"] is not None else None


def firma_del_rol(id_: int) -> bytes | None:
    r = _uno("SELECT firma FROM trabajadores.rol_pago WHERE id = %s", (id_,))
    return bytes(r["firma"]) if r and r["firma"] is not None else None


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


def corregir_cedula(id_: int, cedula: str) -> bool:
    """Sólo si nadie más tiene esa cédula. Devuelve si la cambió."""
    if _uno("SELECT 1 AS x FROM trabajadores.trabajador WHERE cedula = %s AND id <> %s", (cedula, id_)):
        return False
    _ejecutar("UPDATE trabajadores.trabajador SET cedula = %s WHERE id = %s", (cedula, id_))
    return True


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


def perfil_sin_pisar(existente: dict, perfil: dict) -> dict:
    """Lo que viene vacío en la planilla NO borra lo que ya tiene (el celular o la
    dirección que corrigió el trabajador desde su perfil, por ejemplo)."""
    return {k: (perfil.get(k) if perfil.get(k) is not None else existente.get(k)) for k in PERFIL}


def cargar_lote(filas: list[dict], hoy: date) -> dict:
    """La carga masiva. Cada fila: cedula, nombre, fecha_ingreso y, opcional,
    `saldo` (los días que le quedan HOY), el perfil y el `tipo`.

    Si la cédula ya existe se actualizan nombre, fecha, lo que venga del perfil
    (lo vacío no pisa) y el tipo si viene; el saldo sólo se carga a los NUEVOS,
    para no pisarlo si se pega la planilla dos veces.
    """
    nuevos = actualizados = 0
    for f in filas:
        perfil = {k: f.get(k) for k in PERFIL}
        existente = trabajador_por_cedula(f["cedula"])
        if existente:
            editar_trabajador(existente["id"], f["cedula"], f["nombre"], f["fecha_ingreso"],
                              perfil_sin_pisar(existente, perfil))
            if f.get("tipo"):
                poner_tipo(existente["id"], f["tipo"])
            actualizados += 1
            continue
        saldo = f.get("saldo")
        id_ = crear_trabajador(f["cedula"], f["nombre"], f["fecha_ingreso"],
                               saldo, hoy if saldo is not None else None, perfil)
        if f.get("tipo"):
            poner_tipo(id_, f["tipo"])
        nuevos += 1
    return {"nuevos": nuevos, "actualizados": actualizados}


# --------------------------------------------------------------------------
# Tipo (planta / temporal / jubilado) y pausa
# --------------------------------------------------------------------------
def poner_tipo(id_: int, tipo: str) -> None:
    if tipo not in TIPOS_TRABAJADOR:
        raise ValueError(f"No sé qué tipo de trabajador es «{tipo}».")
    _ejecutar("UPDATE trabajadores.trabajador SET tipo=%s WHERE id=%s", (tipo, id_))


def pausar(id_: int, motivo: str, quien: str) -> None:
    _ejecutar("UPDATE trabajadores.trabajador SET pausado_en=now(), pausado_por=%s, motivo_pausa=%s "
              "WHERE id=%s", (quien, motivo or None, id_))


def reanudar(id_: int) -> None:
    _ejecutar("UPDATE trabajadores.trabajador SET pausado_en=NULL, pausado_por=NULL, motivo_pausa=NULL "
              "WHERE id=%s", (id_,))


# --------------------------------------------------------------------------
# La campanita: avisos para contabilidad
# --------------------------------------------------------------------------
_AVISO = """SELECT a.*, t.nombre, t.cedula FROM trabajadores.aviso a
              LEFT JOIN trabajadores.trabajador t ON t.id = a.trabajador_id"""


def crear_aviso(trabajador_id: int | None, texto: str, quien: str, clave: str = "nota") -> int | None:
    """Un aviso automático (clave distinta de 'nota') no se repite mientras
    haya uno abierto igual para la misma persona."""
    if clave != "nota" and _uno("SELECT id FROM trabajadores.aviso WHERE trabajador_id IS NOT DISTINCT FROM %s "
                                "AND clave=%s AND resuelto_en IS NULL", (trabajador_id, clave)):
        return None
    fila = _ejecutar("INSERT INTO trabajadores.aviso (trabajador_id, clave, texto, creado_por) "
                     "VALUES (%s,%s,%s,%s) RETURNING id", (trabajador_id, clave, texto, quien))
    return fila["id"]


def avisos_abiertos() -> list[dict]:
    return _todos(_AVISO + " WHERE a.resuelto_en IS NULL ORDER BY a.creado_en DESC, a.id DESC")


def avisos_resueltos(limite: int = 40) -> list[dict]:
    return _todos(_AVISO + " WHERE a.resuelto_en IS NOT NULL ORDER BY a.resuelto_en DESC LIMIT %s", (limite,))


def avisos_de(trabajador_id: int) -> list[dict]:
    return _todos(_AVISO + " WHERE a.trabajador_id=%s AND a.resuelto_en IS NULL ORDER BY a.creado_en DESC",
                  (trabajador_id,))


def cuantos_avisos() -> int:
    fila = _uno("SELECT count(*) AS n FROM trabajadores.aviso WHERE resuelto_en IS NULL")
    return int(fila["n"]) if fila else 0


def resolver_aviso(id_: int, quien: str) -> None:
    _ejecutar("UPDATE trabajadores.aviso SET resuelto_en=now(), resuelto_por=%s "
              "WHERE id=%s AND resuelto_en IS NULL", (quien, id_))


def resolver_avisos(trabajador_id: int, clave: str, quien: str) -> None:
    _ejecutar("UPDATE trabajadores.aviso SET resuelto_en=now(), resuelto_por=%s "
              "WHERE trabajador_id=%s AND clave=%s AND resuelto_en IS NULL", (quien, trabajador_id, clave))


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


def editar_vacacion(id_: int, tipo: str, desde: date, hasta: date, dias: float, nota: str) -> None:
    if tipo not in TIPOS_AUSENCIA:
        raise ValueError(f"No sé qué tipo de ausencia es «{tipo}».")
    _ejecutar("UPDATE trabajadores.vacacion SET tipo=%s, desde=%s, hasta=%s, dias=%s, nota=%s "
              "WHERE id=%s AND borrado_en IS NULL", (tipo, desde, hasta, dias, nota or None, id_))


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


def solicitud_por_vacacion(vacacion_id: int) -> dict | None:
    """El pedido (si lo hubo) del que nació un período."""
    return _uno("SELECT * FROM trabajadores.solicitud WHERE vacacion_id=%s", (vacacion_id,))


def cambiar_fechas_solicitud(id_: int, desde: date, hasta: date, dias: float) -> None:
    """Cuando contabilidad corrige el período de un pedido aprobado, el pedido
    dice lo mismo (es lo que el trabajador ve)."""
    _ejecutar("UPDATE trabajadores.solicitud SET desde=%s, hasta=%s, dias=%s WHERE id=%s",
              (desde, hasta, dias, id_))


def reabrir_solicitud_cancelada(id_: int) -> None:
    """Se recuperó desde Historial el período de un pedido cancelado: el pedido
    vuelve a «aprobada»."""
    _ejecutar("UPDATE trabajadores.solicitud SET estado='aprobada', respuesta=NULL "
              "WHERE id=%s AND estado='cancelada'", (id_,))


def borrar_solicitud(id_: int) -> bool:
    """Sólo un pedido rechazado o cancelado (no tiene período atrás): desaparece
    de la lista del trabajador y de la de contabilidad."""
    fila = _ejecutar("DELETE FROM trabajadores.solicitud WHERE id=%s "
                     "AND estado IN ('rechazada', 'cancelada') RETURNING id", (id_,))
    return fila is not None


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
# Certificados médicos
# --------------------------------------------------------------------------
def guardar_certificado(trabajador_id: int, datos: bytes, tipo_archivo: str, nombre: str, subido_por: str,
                        solicitud_id: int | None = None, vacacion_id: int | None = None) -> int:
    fila = _ejecutar("INSERT INTO trabajadores.certificado (trabajador_id, solicitud_id, vacacion_id, tipo_archivo, "
                     "nombre, datos, subido_por) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                     (trabajador_id, solicitud_id, vacacion_id, tipo_archivo, nombre, datos, subido_por))
    return fila["id"]


def certificado(id_: int) -> dict | None:
    """Con los datos (para mostrarlo)."""
    return _uno("SELECT * FROM trabajadores.certificado WHERE id=%s", (id_,))


def certificados_de(trabajador_id: int) -> list[dict]:
    """Sin los datos: para poner el link al lado del pedido o del período."""
    return _todos("SELECT id, trabajador_id, solicitud_id, vacacion_id, tipo_archivo, nombre, subido_por, subido_en "
                  "FROM trabajadores.certificado WHERE trabajador_id=%s ORDER BY subido_en", (trabajador_id,))


def certificados_por_solicitud() -> dict[int, list[dict]]:
    """Todos, sin datos, agrupados por pedido (para la bandeja)."""
    filas = _todos("SELECT id, solicitud_id, vacacion_id, subido_en FROM trabajadores.certificado "
                   "WHERE solicitud_id IS NOT NULL ORDER BY subido_en")
    salida: dict[int, list[dict]] = {}
    for f in filas:
        salida.setdefault(f["solicitud_id"], []).append(f)
    return salida


def ligar_certificados_a_vacacion(solicitud_id: int, vacacion_id: int) -> None:
    """Al aprobar el pedido, el certificado queda también en el período."""
    _ejecutar("UPDATE trabajadores.certificado SET vacacion_id=%s WHERE solicitud_id=%s", (vacacion_id, solicitud_id))


# --------------------------------------------------------------------------
# Comidas (almuerzo y cena)
# --------------------------------------------------------------------------
TIPOS_COMIDA = ("almuerzo", "cena")


def comidas_del_mes(trabajador_id: int, anio: int, mes: int) -> set[tuple[date, str]]:
    filas = _todos(
        "SELECT fecha, tipo FROM trabajadores.comida WHERE trabajador_id=%s "
        "AND date_trunc('month', fecha) = %s", (trabajador_id, date(anio, mes, 1)))
    return {(f["fecha"], f["tipo"]) for f in filas}


def comidas_de_todos(anio: int, mes: int) -> dict[int, dict[tuple[date, str], str | None]]:
    """Por trabajador, {(fecha, tipo): turno} del mes. Se usa como un set: `(d, tipo) in suyos`."""
    filas = _todos(
        "SELECT trabajador_id, fecha, tipo, turno FROM trabajadores.comida "
        "WHERE date_trunc('month', fecha) = %s", (date(anio, mes, 1),))
    salida: dict[int, dict[tuple[date, str], str | None]] = {}
    for f in filas:
        salida.setdefault(f["trabajador_id"], {})[(f["fecha"], f["tipo"])] = f["turno"]
    return salida


def quien_comio(fecha: date) -> list[dict]:
    """Los que marcaron ese día, con hora, para la lista del comedor."""
    return _todos("SELECT c.trabajador_id, c.tipo, c.turno, c.marcado_por, c.creado_en, t.nombre, t.area, t.celular "
                  "FROM trabajadores.comida c JOIN trabajadores.trabajador t ON t.id = c.trabajador_id "
                  "WHERE c.fecha = %s ORDER BY c.turno NULLS LAST, t.nombre", (fecha,))


def desmarcar_comida_reciente(trabajador_id: int, fecha: date, tipo: str, minutos: int = 10) -> bool:
    """Deshacer desde la tablet: sólo si se marcó hace poco."""
    fila = _ejecutar("DELETE FROM trabajadores.comida WHERE trabajador_id=%s AND fecha=%s AND tipo=%s "
                     "AND creado_en > now() - make_interval(mins => %s) RETURNING trabajador_id",
                     (trabajador_id, fecha, tipo, minutos))
    return fila is not None


def agregar_invitados(trabajador_id: int, fecha: date, tipo: str, cantidad: int, descripcion: str,
                      cargado_por: str, turno: str | None = None) -> int:
    if tipo not in TIPOS_COMIDA:
        raise ValueError(f"No sé qué comida es «{tipo}».")
    fila = _ejecutar("INSERT INTO trabajadores.invitado (trabajador_id, fecha, tipo, cantidad, descripcion, cargado_por, turno) "
                     "VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                     (trabajador_id, fecha, tipo, cantidad, descripcion, cargado_por, turno))
    return fila["id"]


def turno_marcado(trabajador_id: int, fecha: date, tipo: str) -> str | None:
    """El horario con el que marcó esa comida (None si no marcó o marcó sin horario)."""
    fila = _uno("SELECT turno FROM trabajadores.comida WHERE trabajador_id=%s AND fecha=%s AND tipo=%s",
                (trabajador_id, fecha, tipo))
    return fila["turno"] if fila else None


def invitados_del_dia(fecha: date) -> list[dict]:
    return _todos("SELECT i.*, t.nombre FROM trabajadores.invitado i "
                  "JOIN trabajadores.trabajador t ON t.id = i.trabajador_id "
                  "WHERE i.fecha = %s AND i.borrado_en IS NULL ORDER BY i.creado_en", (fecha,))


def invitados_del_mes(anio: int, mes: int) -> dict[date, list[dict]]:
    """Por día, los invitados (con el nombre de quien los trajo), en orden de carga."""
    filas = _todos("SELECT i.*, t.nombre FROM trabajadores.invitado i "
                   "JOIN trabajadores.trabajador t ON t.id = i.trabajador_id "
                   "WHERE date_trunc('month', i.fecha) = %s AND i.borrado_en IS NULL ORDER BY i.creado_en",
                   (date(anio, mes, 1),))
    salida: dict[date, list[dict]] = {}
    for f in filas:
        salida.setdefault(f["fecha"], []).append(f)
    return salida


def borrar_invitados(id_: int) -> None:
    _ejecutar("UPDATE trabajadores.invitado SET borrado_en=now() WHERE id=%s", (id_,))


def feriados(anio: int | None = None) -> dict[date, str]:
    filas = _todos("SELECT fecha, nombre FROM trabajadores.feriado" +
                   (" WHERE date_part('year', fecha) = %s" if anio else "") + " ORDER BY fecha",
                   (anio,) if anio else ())
    return {f["fecha"]: f["nombre"] for f in filas}


def agregar_feriado(fecha: date, nombre: str) -> None:
    _ejecutar("INSERT INTO trabajadores.feriado (fecha, nombre) VALUES (%s,%s) "
              "ON CONFLICT (fecha) DO UPDATE SET nombre = EXCLUDED.nombre", (fecha, nombre))


def borrar_feriado(fecha: date) -> None:
    _ejecutar("DELETE FROM trabajadores.feriado WHERE fecha=%s", (fecha,))


def configuracion(clave: str) -> str | None:
    fila = _uno("SELECT valor FROM trabajadores.configuracion WHERE clave=%s", (clave,))
    return fila["valor"] if fila else None


def poner_configuracion(clave: str, valor: str) -> None:
    _ejecutar("INSERT INTO trabajadores.configuracion (clave, valor) VALUES (%s,%s) "
              "ON CONFLICT (clave) DO UPDATE SET valor = EXCLUDED.valor", (clave, valor))


def comida_marcada(trabajador_id: int, fecha: date, tipo: str) -> bool:
    return _uno("SELECT 1 AS x FROM trabajadores.comida WHERE trabajador_id=%s AND fecha=%s AND tipo=%s",
                (trabajador_id, fecha, tipo)) is not None


def marcar_comida(trabajador_id: int, fecha: date, tipo: str, marcado_por: str,
                  turno: str | None = None) -> None:
    if tipo not in TIPOS_COMIDA:
        raise ValueError(f"No sé qué comida es «{tipo}».")
    _ejecutar("INSERT INTO trabajadores.comida (trabajador_id, fecha, tipo, marcado_por, turno) "
              "VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING", (trabajador_id, fecha, tipo, marcado_por, turno))


def poner_puede_invitar(trabajador_id: int, puede: bool) -> None:
    _ejecutar("UPDATE trabajadores.trabajador SET puede_invitar=%s WHERE id=%s", (puede, trabajador_id))


def cambiar_comida(trabajador_id: int, fecha: date, tipo: str, tipo_nuevo: str, turno: str,
                   marcado_por: str) -> None:
    """Contabilidad corrige una comida: otro horario, o almuerzo ↔ cena (Tamara 14/10/2026)."""
    if tipo not in TIPOS_COMIDA or tipo_nuevo not in TIPOS_COMIDA:
        raise ValueError("No sé qué comida es.")
    if tipo != tipo_nuevo and _uno("SELECT 1 AS x FROM trabajadores.comida WHERE trabajador_id=%s AND fecha=%s AND tipo=%s",
                                   (trabajador_id, fecha, tipo_nuevo)):
        raise ValueError(f"Ya tiene {'el almuerzo' if tipo_nuevo == 'almuerzo' else 'la cena'} registrado ese día.")
    _ejecutar("UPDATE trabajadores.comida SET tipo=%s, turno=%s, marcado_por=%s "
              "WHERE trabajador_id=%s AND fecha=%s AND tipo=%s",
              (tipo_nuevo, turno, marcado_por, trabajador_id, fecha, tipo))


def desmarcar_comida(trabajador_id: int, fecha: date, tipo: str) -> None:
    _ejecutar("DELETE FROM trabajadores.comida WHERE trabajador_id=%s AND fecha=%s AND tipo=%s",
              (trabajador_id, fecha, tipo))


# --------------------------------------------------------------------------
# Usuarios de contabilidad
# --------------------------------------------------------------------------
_USU = ("id, usuario, nombre, activo, rol, creado_en, revisa_roles, firma_roles, firma_en, "
        "(firma IS NOT NULL) AS tiene_firma")


def usuarios() -> list[dict]:
    return _todos(f"SELECT {_USU} FROM trabajadores.usuario ORDER BY usuario")


def usuario(id_: int) -> dict | None:
    """Un usuario por id, esté activo o no (la sesión se revalida con esto)."""
    return _uno(f"SELECT {_USU} FROM trabajadores.usuario WHERE id=%s", (id_,))


def poner_permisos_roles(id_: int, revisa: bool, firma: bool) -> None:
    _ejecutar("UPDATE trabajadores.usuario SET revisa_roles=%s, firma_roles=%s WHERE id=%s", (revisa, firma, id_))


def guardar_firma_usuario(id_: int, png: bytes) -> None:
    _ejecutar("UPDATE trabajadores.usuario SET firma=%s, firma_en=now() WHERE id=%s", (psycopg2.Binary(png), id_))


def firma_de_usuario(id_: int) -> bytes | None:
    r = _uno("SELECT firma FROM trabajadores.usuario WHERE id=%s", (id_,))
    return bytes(r["firma"]) if r and r["firma"] else None


def usuario_por_nombre(usuario: str) -> dict | None:
    return _uno("SELECT * FROM trabajadores.usuario WHERE usuario=%s AND activo", (usuario,))


def hay_usuarios() -> bool:
    return _uno("SELECT 1 AS x FROM trabajadores.usuario LIMIT 1") is not None


ROLES = ("contabilidad", "comedor")


def crear_usuario(usuario: str, clave_hash: str, nombre: str, rol: str = "contabilidad") -> int:
    if rol not in ROLES:
        raise ValueError(f"No sé qué rol es «{rol}».")
    fila = _ejecutar("INSERT INTO trabajadores.usuario (usuario, clave_hash, nombre, rol) "
                     "VALUES (%s,%s,%s,%s) RETURNING id", (usuario, clave_hash, nombre, rol))
    return fila["id"]


def renombrar_usuario(id_: int, usuario: str, nombre: str) -> None:
    """Cambia el usuario con el que entra y el nombre que se ve. Lo ya cargado
    con el usuario viejo («cargado por …») queda como estaba."""
    _ejecutar("UPDATE trabajadores.usuario SET usuario=%s, nombre=%s WHERE id=%s", (usuario, nombre, id_))


def cambiar_clave(id_: int, clave_hash: str) -> None:
    _ejecutar("UPDATE trabajadores.usuario SET clave_hash=%s WHERE id=%s", (clave_hash, id_))


def activar_usuario(id_: int, activo: bool) -> None:
    _ejecutar("UPDATE trabajadores.usuario SET activo=%s WHERE id=%s", (activo, id_))

# Trabajadores · Intela

Programa chico para la fábrica: **vacaciones** y **comidas** (almuerzo y cena) de cada
trabajador. Contabilidad lo maneja; el trabajador consulta desde el celular
con su cédula.

## Qué hace

**El trabajador** entra a `trabajadores.intela.com.ec`, pone su cédula y ve:

- en la pestaña Vacaciones, cuántos días le quedan, cuándo se le suman los próximos, los períodos
  que tomó, y **pide días** (vacaciones, permiso, enfermedad, permiso sin goce): contabilidad
  responde y él ve la respuesta ahí mismo;
- en la pestaña Perfil, quién es para la empresa: área, desde cuándo trabaja, cuántos días gana por
  año; y corrige su celular y dirección;

**La cafetería** tiene una tablet en el mostrador con `/cafeteria` abierta (entró una vez con un
usuario de rol «cafeteria» y queda entrada): el trabajador escribe su cédula, ve su nombre y
«Almuerzo» o «Cena» (según la hora), y toca Sí. Eso es lo que después se le paga a la cafetería.

**Contabilidad** entra con usuario y clave a `/admin` y tiene:

- la lista de trabajadores con el saldo de vacaciones de cada uno (con buscador);
- **Pedidos**: lo que piden los trabajadores, para aprobar (se carga solo el período) o rechazar
  con motivo, y un botón para avisarles por WhatsApp; abajo, los datos de contacto que cambiaron;
- la ficha de cada uno: cargar los períodos tomados, ajustes, el saldo con el
  que arrancó, editar datos, dar de baja;
- **Cargar planilla**: pegar las filas del Excel (cédula · nombre · fecha de
  ingreso · días que le quedan hoy · área · fecha de nacimiento · celular ·
  días por año) y cargar a todos de una vez;
- **Comidas del mes**: el cuadro trabajador × día, almuerzos y cenas por separado, que es lo
  que se le paga a la cafetería. Se imprime y se corrige ahí;
- **Cafetería**: la misma pantalla de la tablet, por si hay que marcar desde la oficina;
- **Historial**: lo que se borró (períodos, ajustes), con quién y cuándo; se recupera con un botón;
- **Usuarios**: quién más entra a esta parte.

## La cuenta de vacaciones

Por año calendario, como la planilla de contabilidad: cada 1 de enero se
acreditan los días del año — 15 (Código del Trabajo, art. 69), más uno por
cada año de antigüedad que pase de cinco, tope 30. El primer año no suma:
al cumplir el año se acreditan los 15 de una (sin prorrateo), y desde el
enero siguiente sigue como todos. Si a alguien la empresa le da un número
fijo por año (`dias_por_anio`), manda ése.

Como el programa no sabe cuántos días tomó cada uno antes de existir,
contabilidad carga el **saldo al arrancar** (los días que le quedan hoy) y de
ahí en adelante la cuenta sigue sola. Los períodos anteriores a esa fecha se
guardan como historia pero no se restan otra vez.

Cada período tiene un tipo: vacaciones y permiso descuentan del saldo; enfermedad y
permiso sin sueldo se anotan pero no descuentan.

En pantalla se habla como en RRHH: **disponibles**, **tomados**, **acumulados**
(nunca «ganados»). El trabajador lee «este año te tocan X, más Y acumulados,
tomaste Z, te quedan W»; el que no cumplió el año lee cuándo lo cumple y cuánto lleva.

Nada se borra de verdad: un período o ajuste borrado queda marcado (`borrado_en`,
`borrado_por`), se ve en Historial y se puede recuperar.

Todo esto vive en `vacaciones.py`, sin base ni Flask, y tiene tests.

## Cómo está armado

Flask + Postgres (schema propio `trabajadores` en el mismo RDS que el resto),
un `app.py`, un `store.py`, templates Jinja. Waitress adelante, Caddy más
adelante. Puerto 5005. El mismo molde que el programa de máquinas.

```bash
python3 scripts/test_trabajadores.py   # sin base, un segundo
python3 scripts/vista_local.py         # cada pantalla en vista/*.html, con datos de mentira
```

Variables de entorno (de MÁQUINA en el server; `launch.py` las lee del registro):

| Variable | Qué es |
|---|---|
| `TRABAJADORES_DATABASE_URL` | la conexión al RDS (db `intela`, con `sslmode=require`) |
| `TRABAJADORES_SECRET_KEY` | firma de la cookie de sesión |
| `TRABAJADORES_PORT` | 5005 |
| `TRABAJADORES_ADMIN_INICIAL` | clave del usuario `admin`; se usa sólo si no hay usuarios |
| `TRABAJADORES_ZONA` | `America/Guayaquil` (el server está en UTC) |

## Deploy

El server **tira** del repo: `scripts/auto_update.ps1` corre cada 2 minutos,
mira el último commit de main y, si cambió, baja, reemplaza (renombrando,
nunca borrando antes), reinstala, reinicia y verifica `/healthz`. Si no
contesta, vuelve atrás.

Primera vez, en el EC2 (por SSM):

```powershell
.\scripts\instalar.ps1 -DatabaseUrl "..." -SecretKey "..." -AdminInicial "..."
.\scripts\prender_auto_update.ps1
```

Y en Caddy (`C:\caddy\Caddyfile`), **después** de crear el registro DNS:

```
trabajadores.intela.com.ec {
    reverse_proxy 127.0.0.1:5005
}
```

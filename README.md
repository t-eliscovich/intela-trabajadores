# Trabajadores · Intela

Programa chico para la fábrica: **vacaciones** y **comidas** (almuerzo y cena) de cada
trabajador. Contabilidad lo maneja; el trabajador consulta desde el celular
con su cédula.

## Qué hace

**El trabajador** entra a `trabajadores.intela.com.ec`, pone su cédula y ve:

- cuántos días de vacaciones le quedan (y cuándo se le suman los próximos);
- su calendario de comidas del mes: toca A (almuerzo) o C (cena) y queda marcado. Puede
  corregir los últimos 3 días; más atrás lo arregla contabilidad.

**Contabilidad** entra con usuario y clave a `/admin` y tiene:

- la lista de trabajadores con el saldo de vacaciones de cada uno;
- la ficha de cada uno: cargar los períodos tomados, ajustes, el saldo con el
  que arrancó, editar datos, dar de baja;
- **Cargar planilla**: pegar las filas del Excel (cédula · nombre · fecha de
  ingreso · días que le quedan hoy) y cargar a todos de una vez;
- **Comidas del mes**: el cuadro trabajador × día, almuerzos y cenas por separado, que es lo
  que se le paga a la cafetería. Se imprime;
- **Usuarios**: quién más entra a esta parte.

## La cuenta de vacaciones

Código del Trabajo de Ecuador, art. 69: 15 días por cada año **completo**;
desde el sexto año, un día más por cada año que pase de cinco (tope 30). El
año en curso se muestra pero no suma hasta cumplirse.

Como el programa no sabe cuántos días tomó cada uno antes de existir,
contabilidad carga el **saldo al arrancar** (los días que le quedan hoy) y de
ahí en adelante la cuenta sigue sola. Los períodos anteriores a esa fecha se
guardan como historia pero no se restan otra vez.

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

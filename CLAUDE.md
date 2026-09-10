# Cómo trabajar en este repo

Programa de trabajadores de Intela (vacaciones + comidas: almuerzo y cena). Ver `README.md`
para qué hace. Esto es cómo se trabaja.

## Antes de tocar nada

Cargar la skill **`maquinas-tejeduria`**: este programa es una copia del molde
de máquinas (mismo box, mismo auto-update, mismas trampas ya pagadas). Pair con
`intela-aws-deploy` y `textos-de-pantalla-intela`.

## El ciclo

```bash
python3 scripts/test_trabajadores.py   # SIEMPRE antes de commitear
python3 scripts/vista_local.py         # ver las pantallas antes de pushear un cambio de diseño
git push                               # el server se actualiza solo en <2 min
```

No hay deploy manual. El token que sirve para pushear es el de
`Programa Core/.gh_pat`. Nunca dejarlo en la URL del remote.

## Reglas duras

**Este repo es PÚBLICO.** Nunca `git add -A` sin mirar qué entra. Nada de
planillas con cédulas, nada de claves.

**La cuenta de vacaciones vive en `vacaciones.py` y tiene tests.** Cambiar una
regla = cambiar el test primero.

**El saldo al arrancar es una foto.** `saldo_inicial` + `fecha_saldo_inicial`
en `trabajador`. Los aniversarios anteriores a esa fecha NO suman y los
períodos anteriores NO restan: ya están adentro de ese número. No convertirlo
en un ajuste: se probó y la lista quedaba con «231 ganados, −210 ajustes».

**«Hoy» es el de Ecuador**, `app.hoy()`, nunca `date.today()`: el server está
en UTC y desde las 19:00 el check de la comida caería en el día siguiente.

**El trabajador sólo ve lo suyo.** Entra por cédula, queda en la sesión, y
ninguna ruta pública recibe un id por la URL.

**El pool se abre al importar el módulo, no en `__main__`.** Waitress hace
`import app`. Los tests importan la app así, a propósito.

**El esquema corre UNA SENTENCIA POR TRANSACCIÓN** y la que falla queda en
`store.AVISOS_ESQUEMA` (se ve en `/healthz`). No juntarlas.

**En pantalla: castellano simple, una idea por línea.** Ver la skill
`textos-de-pantalla-intela`. «Mantenimiento», no «service»; «cuadro», no
«dashboard».

**Importar la app como la importa Waitress.** `scripts/_falso.py` reemplaza
las funciones de `store` una por una, no el módulo entero.

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

**Las vacaciones van por AÑO CALENDARIO** (decisión 11/09/2026, para calzar
con la planilla de contabilidad: el 1 de enero se acredita el año entero).
**Excepto el primer año: 0 hasta cumplirlo, y ese día los 15 de una** (decisión
14/09/2026, sin prorrateo; el año del aniversario ya queda cubierto por esos
15, el 1 de enero siguiente sigue normal). La fórmula del Excel
`clamp(2026 − añoRef − 4, 0, 15)` es la misma ley, verificado en 112 filas.

**El saldo al arrancar es una foto.** `saldo_inicial` + `fecha_saldo_inicial`
en `trabajador`. Lo acreditado antes de esa fecha NO suma y los
períodos anteriores NO restan: ya están adentro de ese número. No convertirlo
en un ajuste: se probó y la lista quedaba con «231 ganados, −210 ajustes».

**Los tipos de ausencia viven en `store.TIPOS_AUSENCIA`** (nombre, ¿descuenta?). El
SQL de `tomados` filtra por los que descuentan; agregar un tipo es agregar una
línea ahí, y el test lo prueba contra la base falsa que repite la misma tabla.

**Nunca `DELETE` en vacacion / ajuste_vacacion.** `borrar_*` marca `borrado_en`
+ `borrado_por`; toda lectura filtra `borrado_en IS NULL`; /admin/historial recupera.

**En pantalla, palabras de RRHH**: disponibles, tomados, acumulados, generados
(en contabilidad). «Ganados» no (pedido de Tamara 14/09).

**Los avisos al trabajador no son automáticos**: no hay API de WhatsApp. Al
responder un pedido, la pantalla ofrece un link `wa.me` con el mensaje armado.
El trabajador siempre ve la respuesta en su pestaña Vacaciones.

**«Hoy» es el de Ecuador**, `app.hoy()`, nunca `date.today()`: el server está
en UTC y desde las 19:00 el check de la comida caería en el día siguiente.

**Las comidas se marcan en la tablet del comedor, no en la app del trabajador**
(decisión 14/09/2026). La tablet entra por un link con clave y SIN sesión:
`/comedor/t/<clave>`; la clave vive en `configuracion.clave_comedor` y se
regenera desde /admin/usuarios («Generar uno nuevo»). Almuerzo o cena por la
hora de Ecuador (`HORA_CENA`). El trabajador puede deshacer su marca durante
`MINUTOS_PARA_DESHACER` y anotar hasta `MAX_INVITADOS` invitados por comida
(descripción, sin cédula; tabla `invitado`). El usuario con `rol = 'comedor'`
(antes `cafeteria`; el esquema lo renombra) ve /comedor/dia: quién comió,
invitados, quién no se anotó (aviso por wa.me). Sábados, domingos y `feriado`
(precargados los nacionales de Ecuador, editables en /admin/feriados) salen en
gris en Comidas del mes y se ocultan si no comió nadie.

**El trabajador sólo ve lo suyo.** Entra por cédula, queda en la sesión, y
ninguna ruta pública recibe un id por la URL.

**El pool se abre al importar el módulo, no en `__main__`.** Waitress hace
`import app`. Los tests importan la app así, a propósito.

**El esquema corre UNA SENTENCIA POR TRANSACCIÓN** y la que falla queda en
`store.AVISOS_ESQUEMA` (se ve en `/healthz`). No juntarlas.

**En pantalla: español de ECUADOR, de USTED. Nunca voseo ni argentinismos**
(regla de Tamara, 14/09/2026: «no quiero volver a ver argentino»). «Escriba su
cédula», «¿Es usted?», «le tocan», «tomó», «aquí» — no «escribí», «sos vos»,
«te tocan», «tomaste», «acá». Vale para el trabajador, la tablet y contabilidad.
Antes de commitear un texto nuevo: `grep -rnE "vos\b|sos\b|tenés|podés|acá\b|á\b" templates`.
Castellano simple, una idea por línea. Ver la skill `textos-de-pantalla-intela`.
«Mantenimiento», no «service»; «cuadro», no «dashboard».

**Importar la app como la importa Waitress.** `scripts/_falso.py` reemplaza
las funciones de `store` una por una, no el módulo entero.

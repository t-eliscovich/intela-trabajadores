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

**En pantalla, palabras de RRHH**: disponibles, tomados, acumulados, acreditados
(en contabilidad). «Ganados» ni «generados» no (pedido de Tamara 14/09).

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
(descripción, sin cédula; tabla `invitado`) SÓLO si `trabajador.puede_invitar`
(se marca en la ficha, «Invitados al comedor»; no va por planilla). Toda comida
lleva `comida.turno` (`TURNOS`: almuerzo 12:00/12:30/13:00/13:30, cena
18:30/19:00; media hora cada uno): la tablet lo preselecciona por la hora
(`turno_de_ahora`) y el trabajador lo puede cambiar; vacío → el de ahora. Los
invitados llevan el turno de quien los trae.

**Palabras del comedor** (Tamara, 16/09: «muy poco profesional»): comensales,
registrar/registrado, horario, sin registrar, quitar. No «quién comió»,
«marcar», «se anotó», «nadie todavía». Sábados, domingos y `feriado`
(precargados los nacionales de Ecuador, editables en /admin/feriados) salen en
gris en Comidas del mes y se ocultan si no comió nadie.

**El usuario del comedor SÓLO MIRA** (Tamara, 17/09/2026: «no debe ver nada más
que quién comió, nada de celulares»). Con `rol = 'comedor'` (antes `cafeteria`;
el esquema lo renombra) entra a /comedor/dia y ve los comensales del día, nada
más: sin «Sin registrar», sin celulares ni wa.me, sin botones de registrar o
quitar, sin ir a días futuros. También ve **Comidas del mes** (el resumen por
semana y por día, IGUAL que contabilidad — pedido de Tamara del mismo día), sin
el formulario de registrar a mano, sin Feriados ni cuadro para pagar. Un POST
suyo a /comedor/dia o /admin/comidas da 403. Registrar y quitar desde Comensales es de
contabilidad (`es_contabilidad()`), y queda con su usuario a la vista («· conta»).
La tablet registra por su propio link, no por este usuario.

**La sesión se revalida en cada pedido** (`_cargar_usuario` relee
`store.usuario(id)`): un usuario desactivado o con otro rol se cae al instante,
también el del comedor con su cookie de 30 días. Una cookie sin rol vuelve a entrar.

**Pedido y período van siempre juntos.** Corregir uno (desde Pedidos o con el
lápiz de la ficha) corrige el otro; borrar desde la ficha el período de un
pedido aprobado cancela el pedido; recuperarlo desde Historial lo vuelve a
«aprobada». `_controlar_periodo` frena todo período que se pisa con uno ya
cargado (también el pedido del trabajador, en `yo_pedir`), y avisa cuando
empieza antes del saldo inicial (no descuenta). Aprobar más días de los que
tiene exige marcar «Aprobar aunque no le alcance».

**Un solo nombre por cosa en pantalla**: «le corresponden» (los días del año),
«se acreditan» / «Acreditados» (sumar días), «saldo inicial» (la foto al
cargarlo; nunca «al arrancar» en pantalla), «tomados», «Comensales» y «Comidas
del mes» iguales en el menú y en el título. El nombre de pila para WhatsApp sale
de `nombre_de_pila` (tercera palabra si hay 3 o más, si no la primera).

**El certificado médico es una foto** (Tamara, 17/09): el trabajador la manda con
el pedido de enfermedad o después («Subir certificado» en el pedido); contabilidad
también la sube desde la ficha. Tabla `certificado` (bytea, ligado a trabajador,
pedido y período); `leer_certificado` achica la foto a JPEG ≤1600 px con Pillow y
un PDF va tal cual (≤5 MB). Sólo contabilidad la abre (/admin/certificado/N). Al
aprobar, el certificado del pedido pasa al período. «Sin certificado» se ve en rojo.

**El Excel de comidas** (/admin/comidas/excel, openpyxl, sólo contabilidad) tiene
los MISMOS números que la pantalla: hojas Por día, Por trabajador (A/C por día) e
Invitados. **La hoja de saldo de vacaciones** (/admin/trabajador/N/imprimir) es la
ficha de 5 filas + períodos + ajustes + firmas, para imprimir.

**Rol de pagos (Tamara, 13/10/2026).** Contabilidad sube cada mes el Excel que
exporta WinAfin («rolN.xls», N = mes) en /admin/roles/cargar; el trabajador lo ve en
la pestaña «Rol de pagos» y lo FIRMA CON EL DEDO en la app. **Nada de imprimir**
(«quisiera que lo puedan firmar ahí en la app, como un DocuSign»).
- `roles.py` (sin base ni Flask, con tests): qué es cada `camprN` salió de la
  pantalla Rol de Pagos de WinAfin y cuadra en los 114 de septiembre. Una fila cuyos
  totales no cierran NO entra. Columnas `campr22…45` con valor = concepto nuevo
  → la fila queda afuera con el motivo. **Las provisiones (`campp`) no se guardan
  ni se muestran.**
- **El cruce con el padrón — NUNCA cargarle el rol de una persona a otra**
  (Tamara, 13/10/2026: «muy importante que nadie vea nada de otra persona»; la
  entrada sigue siendo sólo con la cédula, decisión suya). Entran solos: el
  código de WinAfin guardado (`trabajador.codigo_rol`) **si además el nombre
  coincide** (con el padrón o con `trabajador.nombre_rol`, el nombre de WinAfin
  que se confirmó), el mismo nombre, o las mismas palabras en otro orden. Un
  nombre «parecido», alguien de baja o uno elegido a mano entra **sólo si se
  marca «Es la misma persona»**. Un código guardado con otro nombre no se usa
  (se avisa). Nunca dos filas al mismo trabajador; dos homónimos activos → ninguno.
  La revisión muestra la cédula del padrón. Al cargar se guardan código y nombre
  de WinAfin → **desde el mes siguiente se unen solos**. El mes queda trabado:
  si se cambia en la revisión, pide revisar de nuevo antes de cargar. El archivo
  viaja escondido en el formulario (`MAX_FORM_MEMORY_SIZE` = 8 MB).
- Volver a cargar un mes lo reemplaza entero (lo viejo queda con `borrado_en`).
  Un recibo firmado que vuelve IGUAL (conceptos, líquido, sueldo, días, atrasos,
  otros) conserva la firma; si cambió algo, hay que volver a firmar.
- **La firma**: PNG del trazo + `firmado_en` + `firmado_desde` (user agent) +
  `firma_huella` (SHA-256 del recibo firmado, `roles.huella`). La «Constancia de
  firma» muestra si el recibo sigue igual a lo firmado. Se firma una vez. La firma
  la ven el dueño (`/yo/rol/N/firma.png`) y contabilidad, nadie más.
- **El recibo entra en una pantalla de celular** (Tamara: «que no haya que
  scrollear mucho»): líquido arriba con el estado, una línea de datos, renglones
  bajos, recuadro de firma de 150 px. Medido en 390×844.
- La imagen de la firma se mira el tamaño ANTES de descomprimir (≤3000×1500):
  un PNG de 20 KB puede ser una imagen de 13000×13000 que tumba el server.
- El trabajador ve arriba los meses como botones con el líquido (Sep $ 794,81…; punto
  rojo = falta firmar) y abre el último por defecto. Sólo ve los suyos.
- Contabilidad ve en el menú un globo naranja con cuántos faltan firmar del último
  mes, «Últimas firmas», y avisa a los que faltan por WhatsApp (wa.me).
- **Historial: el ROL ACUMULADO de WinAfin** (`Rol_Acumulado.xlsx`, una fila por
  trabajador y mes, CON CÉDULA; Tamara 13/10). Se sube en la MISMA pantalla de carga:
  `roles.es_acumulado` lo reconoce por los títulos y abre `admin_roles_historial.html`.
  Columnas por POSICIÓN (14to y 13er aparecen dos veces: ingreso y provisión); sin
  totales ni horas → líquido = ingresos − descuentos. Septiembre coincide al centavo
  con rol9.xls. Se cruza **por cédula** (`cruzar_por_cedula`): misma cédula + nombre
  compatible entra solo; todo lo demás se confirma a mano. Meses como tarjetas: se
  marcan solos los nuevos y cerrados; el mes en curso y los ya cargados no (se
  pueden marcar; los meses marcados quedan trabados igual que el mes del rol).
  **Los meses del historial no piden firma** (`rol_pago.pide_firma = false`): no
  aparece el recuadro, no suman al globito, no se pueden firmar.
- **Cédulas mal tipeadas en el padrón**: `roles.cedula_valida` (dígito verificador
  ecuatoriano). Al cargar el historial, si la del padrón no es válida y la de WinAfin
  sí, al confirmar se corrige en el padrón (`store.corregir_cedula`, si nadie más la
  tiene). La lista y la ficha marcan «cédula no válida». El 13/10 había 4 así
  (Alava, Moreno Barrera, Moreno Ruano, Rivera Rojas): no podían entrar con su cédula.
- El encabezado dice INTELA (razón social y RUC en espera; la razón social es
  «Industria Textil Latinoamericana Cía. Ltda.»).

**Backlog (Tamara, 17/09, «para alguna vez»)**: aviso en Pedidos de cuántos de la
misma área ya están de vacaciones en esas fechas. También: cerrar el mes de comidas
con Historial, acumulación de vacaciones a 3 años (importa desde 2027), liquidación
al dar de baja, permisos por horas.

**El trabajador sólo ve lo suyo.** Entra por cédula, queda en la sesión, y
ninguna ruta pública recibe un id por la URL.

**El pool es `ThreadedConnectionPool`** (Waitress usa varios hilos) **y prueba la conexión antes de usarla** (`SELECT 1` en `store._conn`): el
13/10/2026 la primera entrada a contabilidad después de horas sin uso dio
«Internal Server Error» porque el RDS había cerrado la conexión quieta.

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

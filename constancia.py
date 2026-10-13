"""La CONSTANCIA DE CONOCIMIENTO Y ACCESO A ROLES DE PAGO (texto de Tamara, 13/10/2026).

Cada trabajador la firma UNA vez en la app, con el dedo, antes de ver sus roles.
Escribe su banco y número de cuenta; nombre, cédula, fecha y lugar se ponen solos.
Se guarda el TEXTO EXACTO que firmó (con sus datos) y su huella SHA-256: si mañana
se cambia esta plantilla, lo firmado sigue siendo lo que se firmó.

Sin base ni Flask, para poder probarlo solo.
"""
from __future__ import annotations

import hashlib
import re
from datetime import date

TITULO = "CONSTANCIA DE CONOCIMIENTO Y ACCESO A ROLES DE PAGO"
LUGAR = "Quito"
AREA = "Contabilidad"
PORTAL = "la aplicación Trabajadores · Intela (trabajadores.intela.com.ec)"
# Cambiar el texto = subir la versión. Lo ya firmado guarda su propio texto.
VERSION = 1

MESES = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]

# Bancos y cooperativas comunes en Ecuador: sólo para sugerir mientras escribe.
BANCOS = ("Banco Pichincha", "Banco Guayaquil", "Banco del Pacífico", "Produbanco",
          "Banco Internacional", "Banco Bolivariano", "Banco del Austro", "Banco de Machala",
          "Banco de Loja", "BanEcuador", "Banco General Rumiñahui", "Banco Solidario",
          "Cooperativa JEP", "Cooperativa Policía Nacional", "Cooperativa 29 de Octubre",
          "Cooperativa Andalucía", "Cooperativa Alianza del Valle", "Cooperativa Cooprogreso")


def leer_banco(texto: str | None) -> str:
    b = " ".join((texto or "").split())
    if len(b) < 3:
        raise ValueError("Escriba el nombre de su banco o cooperativa.")
    if len(b) > 80:
        raise ValueError("El nombre del banco es demasiado largo.")
    return b


def leer_cuenta(texto: str | None) -> str:
    """Sólo los números de la cuenta: «2200-1234-56» → «2200123456»."""
    c = re.sub(r"[\s.\-]", "", texto or "")
    if not c.isdigit():
        raise ValueError("El número de cuenta lleva sólo números.")
    if not 5 <= len(c) <= 20:
        raise ValueError("Revise el número de cuenta: tiene que tener entre 5 y 20 números.")
    return c


def parrafos(nombre: str, cedula: str, empresa: str, banco: str, cuenta: str, fecha: date) -> list[str]:
    """El documento, párrafo por párrafo, con los datos puestos."""
    return [
        f"Yo, {nombre}, portador/a de la cédula de ciudadanía No. {cedula}, en mi calidad de "
        f"trabajador/a de {empresa}, por medio del presente documento, declaro conocer que:",
        f"1. Conozco que el pago de mi remuneración y demás valores que me correspondan derivados de mi "
        f"relación laboral con mi empleador, se realizará mediante acreditación o transferencia bancaria a "
        f"la cuenta No. {cuenta} del {banco}.",
        f"2. Conozco que, una vez procesada la nómina correspondiente a cada período, mi rol de pagos se "
        f"generará y pondrá a mi disposición en formato digital a través de {PORTAL}, al cual podré acceder "
        f"mediante mis credenciales personales, pudiendo presentar cualquier observación, inquietud o reclamo "
        f"respecto de los valores o conceptos reflejados en el mismo, dentro de los cinco (5) días hábiles "
        f"siguientes a su puesta a disposición, con la finalidad de facilitar su oportuna revisión y atención "
        f"por parte de la compañía.",
        "3. Declaro conocer el mecanismo de acceso al referido portal y que, a través de este, puedo consultar "
        "y obtener mi rol de pagos correspondiente a cada período.",
        f"4. En caso de presentar cualquier inconveniente para acceder al portal, consultar o descargar mi rol "
        f"de pagos, podré comunicarlo al área de {AREA}, a fin de recibir la asistencia necesaria o solicitar "
        f"una copia del documento correspondiente.",
        "5. Conozco que la entrega y puesta a disposición de mis roles de pago se realizará ordinariamente por "
        "medios digitales, sin perjuicio de mi derecho a solicitar una copia de los mismos cuando así lo requiera.",
        f"La presente constancia tiene por finalidad dejar evidencia de que conozco el mecanismo utilizado por "
        f"{empresa} para el pago de mis remuneraciones y para la puesta a disposición y acceso a mis roles de "
        f"pago en formato digital.",
        f"Para constancia, suscribo el presente documento en {LUGAR}, a los {fecha.day} días del mes de "
        f"{MESES[fecha.month]} de {fecha.year}.",
    ]


def texto(nombre: str, cedula: str, empresa: str, banco: str, cuenta: str, fecha: date) -> str:
    return TITULO + "\n\n" + "\n\n".join(parrafos(nombre, cedula, empresa, banco, cuenta, fecha))


def huella(texto_firmado: str) -> str:
    return hashlib.sha256(texto_firmado.encode("utf-8")).hexdigest()

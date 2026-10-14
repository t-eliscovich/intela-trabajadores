"""Mandar un correo. Hoy, uno solo: el código para crear o recuperar la contraseña
del rol de pagos (Tamara 14/10/2026).

Es el MISMO camino que usa Programa Core (`modules/_lib/mailer.py`): Amazon SES
desde el EC2, con el rol IAM de la máquina. **No hay ninguna contraseña de correo
guardada en ningún lado**: boto3 toma las credenciales del rol solo. El remitente
`no-reply@intela.com.ec` ya está verificado en SES (de ahí sale la nota diaria y el
código del portal de clientes).

Reglas:
· **Nunca lanza.** Un correo que no sale no puede tumbar la pantalla: devuelve
  (False, motivo) y la pantalla lo dice en palabras simples.
· **Uno por destinatario**, siempre (en Programa Core un destinatario malo hacía
  rebotar el envío entero).
· **Se apaga por entorno** (`MAIL_ENVIAR=0`) sin tocar código.
"""
from __future__ import annotations

import html as _html
import logging
import os

_LOG = logging.getLogger("trabajadores.correo")

REMITENTE = os.environ.get("MAIL_REMITENTE", "").strip() or "no-reply@intela.com.ec"


def _region() -> str:
    return os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-2"


def enviar(para: str, asunto: str, texto: str, html: str = "") -> tuple[bool, str]:
    """Manda UN correo. Devuelve (salió, motivo). Nunca lanza."""
    para = (para or "").strip()
    if not para:
        return False, "sin destinatario"
    if os.environ.get("MAIL_ENVIAR", "1").strip() == "0":
        return False, "el envío de correos está apagado (MAIL_ENVIAR=0)"
    try:
        import boto3
    except Exception:  # noqa: BLE001 -- sin boto3 no se manda
        return False, "falta boto3 en el servidor"
    cuerpo = {"Text": {"Data": texto, "Charset": "UTF-8"}}
    if html:
        # siempre con la versión de texto al lado: un HTML solo puntúa peor en los filtros de spam
        cuerpo["Html"] = {"Data": html, "Charset": "UTF-8"}
    try:
        cliente = boto3.client("ses", region_name=_region())
        cliente.send_email(Source=f"Intela <{REMITENTE}>", Destination={"ToAddresses": [para]},
                           Message={"Subject": {"Data": asunto[:200], "Charset": "UTF-8"}, "Body": cuerpo})
        return True, ""
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("correo: no se pudo mandar a %s (%s)", para, exc)
        return False, str(exc)[:200]


def mandar_codigo(para: str, nombre: str, codigo: str, minutos: int, para_que: str) -> tuple[bool, str]:
    """El código de 6 números, en un correo lindo y corto. `para_que`: «crear» u «olvido»."""
    accion = "crear la contraseña de su rol de pagos" if para_que == "crear" else "cambiar la contraseña de su rol de pagos"
    asunto = f"Intela · su código es {codigo}"
    texto = (f"Hola {nombre}:\n\nSu código para {accion} en la aplicación de Intela es:\n\n    {codigo}\n\n"
             f"Vale por {minutos} minutos. Si usted no lo pidió, no haga nada: su rol sigue protegido.\n\n"
             "Intela · Contabilidad")
    n, c = _html.escape(nombre), _html.escape(codigo)
    espaciado = " ".join(c)
    html = f"""<!doctype html><html><body style="margin:0;padding:0;background:#f1f5f9">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f1f5f9;padding:28px 12px">
<tr><td align="center">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:460px;background:#ffffff;border-radius:16px;
         font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#0f172a;overflow:hidden">
    <tr><td style="background:#1e3a5f;padding:18px 24px;color:#ffffff;font-size:20px;font-weight:800;letter-spacing:.02em">INTELA
      <span style="font-weight:600;font-size:13px;opacity:.75;margin-left:8px">Rol de pagos</span></td></tr>
    <tr><td style="padding:26px 24px 8px;font-size:16px;line-height:1.5">Hola {n}:<br>
      su código para {_html.escape(accion)} es:</td></tr>
    <tr><td align="center" style="padding:10px 24px 6px">
      <div style="display:inline-block;background:#f1f5f9;border-radius:14px;padding:16px 22px;font-size:34px;font-weight:800;
                  letter-spacing:.18em;font-variant-numeric:tabular-nums;color:#1e3a5f">{espaciado}</div></td></tr>
    <tr><td style="padding:8px 24px 24px;font-size:14px;line-height:1.5;color:#475569">Vale por {minutos} minutos.
      Si usted no lo pidió, no haga nada: su rol sigue protegido.</td></tr>
    <tr><td style="padding:14px 24px;border-top:1px solid #e2e8f0;font-size:12px;color:#94a3b8">
      {_html.escape("Industria Textil Latinoamericana Cía. Ltda.")} · Contabilidad</td></tr>
  </table>
</td></tr></table></body></html>"""
    return enviar(para, asunto, texto, html)

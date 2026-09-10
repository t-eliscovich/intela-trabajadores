"""Configuración por variables de entorno. Nada hardcodeado, nada secreto en el repo."""
import os

# Mismo cluster RDS que el resto. Schema propio `trabajadores`.
DATABASE_URL = os.environ.get("TRABAJADORES_DATABASE_URL", "")

SECRET_KEY = os.environ.get("TRABAJADORES_SECRET_KEY", "cambiar-esto-en-produccion")

# 5001 formulas_app · 5002 Programa Core · 5003 máquinas · 5004 portal de
# vendedores de Programa Core. Este programa va al 5005.
PORT = int(os.environ.get("TRABAJADORES_PORT", "5005"))

# Clave del primer usuario de contabilidad. Se usa UNA vez: si la tabla de
# usuarios está vacía al arrancar, se crea el usuario «admin» con esta clave.
# Después los usuarios se manejan desde /admin/usuarios y esto se puede borrar.
ADMIN_INICIAL = os.environ.get("TRABAJADORES_ADMIN_INICIAL", "")

# Zona horaria de la fábrica. El server está en UTC; a partir de las 19:00 de
# Ecuador «hoy» ya es mañana en UTC, y el check del almuerzo caería en el día
# equivocado.
ZONA = os.environ.get("TRABAJADORES_ZONA", "America/Guayaquil")

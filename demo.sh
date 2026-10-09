#!/usr/bin/env bash
# Demo en un minuto, con datos inventados en una carpeta temporal. No toca tu crontab, ni tus
# LaunchAgents, ni nada fuera de esa carpeta. Solo necesita python3.
set -euo pipefail
H="$(cd "$(dirname "$0")" && pwd)/herramientas"
D="$(mktemp -d)"
trap 'rm -rf "$D"' EXIT
mkdir -p "$D/salidas" "$D/vacio"

# Robots inventados: uno sano, uno que deja el fichero vacío, uno muerto hace 3 días y uno vigilado a medias.
python3 - "$D" <<'PY'
import os, sys, time
d = sys.argv[1]
open(f"{d}/salidas/copia.sql.gz", "wb").write(b"x" * 500_000)
os.utime(f"{d}/salidas/copia.sql.gz", (time.time() - 2 * 3600,) * 2)
open(f"{d}/salidas/ventas_hoy.csv", "w").close()
open(f"{d}/salidas/facturas.zip", "wb").write(b"x" * 9_000)
open(f"{d}/salidas/resumen_semanal.pdf", "wb").write(b"x" * 80_000)
os.utime(f"{d}/salidas/resumen_semanal.pdf", (time.time() - 3 * 86400,) * 2)
PY

cat > "$D/censo.json" <<JSON
{
  "guardias": ["demo"],
  "piezas": [
    {"id": "copia-nocturna", "nombre": "Copia de la base de datos, cada noche", "huella": "copia_nocturna.sh",
     "estado": "VIGILADO", "alta": "2026-10-01",
     "comprobar": [{"fichero": "$D/salidas/copia.sql.gz", "sla_horas": 26, "min_bytes": 100000, "debe_cambiar": true}]},
    {"id": "informe-ventas", "nombre": "CSV de ventas del día para el panel", "huella": "informe_ventas.py",
     "estado": "VIGILADO", "alta": "2026-10-01",
     "comprobar": [{"fichero": "$D/salidas/ventas_hoy.csv", "sla_horas": 25, "min_bytes": 200, "contiene": "{hoy}"}]},
    {"id": "resumen-semanal", "nombre": "PDF de resumen que se manda a los socios", "huella": "resumen.py",
     "estado": "VIGILADO", "alta": "2026-10-01",
     "comprobar": [{"fichero": "$D/salidas/resumen_semanal.pdf", "sla_horas": 30, "min_bytes": 10000, "debe_cambiar": true}]},
    {"id": "factura-mensual", "nombre": "Genera las facturas del mes", "huella": "facturas.py",
     "estado": "VIGILADO", "alta": "2026-10-01",
     "comprobar": [{"fichero": "$D/salidas/facturas.zip", "sla_horas": 800}]},
    {"id": "llave-tienda", "nombre": "La llave de la API de la tienda (caduca)", "estado": "SIN VIGILAR", "alta": "2026-10-01"}
  ]
}
JSON

# Una crontab inventada: dos líneas censadas y una que alguien (¿tu agente?) añadió y no apuntó.
cat > "$D/crontab" <<'CRON'
0 3 * * * /srv/copia_nocturna.sh
30 7 * * * python3 /srv/informe_ventas.py
0 9 1 * * python3 /srv/facturas.py
*/15 * * * * /srv/limpia_temporales.sh
CRON
export VIGIA_CRONTAB="$D/crontab" VIGIA_CRON_D="$D/vacio" VIGIA_LAUNCHD="$D/vacio" VIGIA_SYSTEMD="$D/vacio" VIGIA_DOCKER=""

linea() { printf '\n\033[1m── %s ──\033[0m\n' "$1"; }

linea "1. ¿Qué está programado aquí sin estar en el censo? ¿Y qué está en el censo y ya no?"
python3 "$H/descubrir.py" --censo "$D/censo.json" --maquina demo || true

linea "2. La guardia comprueba lo vigilado"
python3 "$H/guardia.py" --censo "$D/censo.json" --maquina demo || true

linea "3. Siete horas después, sigue igual: el aviso sube de tono en vez de repetirse"
python3 "$H/guardia.py" --censo "$D/censo.json" --maquina demo --ahora "$(python3 -c 'import time;print(time.time()+7*3600)')" \
  | sed -n '/^Aviso:/,$p' || true

linea "4. Revisión del censo: la factura sale en verde, pero su comprobación es floja"
python3 "$H/guardia.py" --censo "$D/censo.json" --revisar || true

# La torre: cada máquina de cada cliente tiene su guardia, que empuja su parte a su buzón de la torre.
for m in clinica tienda-web despacho nuevo-cliente; do mkdir -p "$D/buzon/$m"; done
python3 - "$D" <<'PY'
import sys
from datetime import datetime, timedelta
d = sys.argv[1]
def parte(maquina, horas, extra=""):
    sello = (datetime.now().astimezone() - timedelta(hours=horas)).isoformat(timespec="minutes")
    open(f"{d}/buzon/{maquina}/parte.txt", "w").write(f"PARTE · guardia de «{maquina}» · {sello}\n{extra}vigiladas: 4\n")
parte("clinica", 0.5)
parte("tienda-web", 7)
parte("despacho", 0.5, "⚠️ A CIEGAS: la orden de --avisar ha fallado\n")
parte("nuevo-cliente", 0.5)
PY
python3 - "$D" <<'PY'
import json, sys
d = sys.argv[1]
piezas = [{"id": f"guardia-{m}", "nombre": f"La guardia de {m}", "maquina": m, "vigila_desde": "torre", "estado": "VIGILADO",
           "alta": "2026-10-01", "comprobar": [{"fichero": f"{d}/buzon/{m}/parte.txt", "sla_horas": 3, "min_bytes": 20,
           "edad_por_contenido": True, "no_contiene": "A CIEGAS", "contiene": f"guardia de «{m}»"}]}
          for m in ("clinica", "tienda-web", "despacho")]
json.dump({"guardias": ["torre"], "buzon": f"{d}/buzon", "piezas": piezas}, open(f"{d}/torre.json", "w"), ensure_ascii=False)
PY

linea "5. La torre: una guardia lleva 7 h callada, otra está a ciegas y ha llegado un cliente que nadie ha apuntado"
python3 "$H/guardia.py" --censo "$D/torre.json" --maquina torre --estado "$D/torre_estado.json" | sed -n '1,5p' || true

printf '\nFin de la demo. Lo de verdad: README.md, «Ponlo en marcha».\n'

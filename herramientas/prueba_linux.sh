#!/usr/bin/env bash
# Prueba en un Linux DE VERDAD, sin simular nada: el crontab, /etc/cron.d, systemd, una llave ssh
# restringida con rrsync y contenedores de Docker reales. Como toca todo eso en la máquina, solo
# corre en una máquina de usar y tirar: la que crea GitHub Actions para cada prueba.
# Licencia: MIT.
set -u
if [ "${GITHUB_ACTIONS:-}" != "true" ]; then
  echo "Me niego: toco el crontab, systemd, ssh y Docker de la máquina. Solo corro en GitHub Actions."
  exit 2
fi
H="$(cd "$(dirname "$0")" && pwd)"
T="$(mktemp -d)"
FALLOS=0
ok() { echo "  ✓ $1"; }
mal() { echo "  ✗ $1"; printf '%s\n' "$2" | sed 's/^/      /' | head -30; FALLOS=$((FALLOS + 1)); }
si() { if eval "$2"; then ok "$1"; else mal "$1" "$3"; fi; }

echo "descubrir.py sobre lo programado de verdad"
( crontab -l 2>/dev/null; echo "0 3 * * * /srv/robot_linux.sh --password=NO_DEBE_SALIR" ) | crontab -
echo "15 2 * * * root /usr/local/bin/rotar_linux.sh" | sudo tee /etc/cron.d/vigia-prueba >/dev/null
printf '[Unit]\nDescription=prueba\n[Service]\nExecStart=/bin/true\n' | sudo tee /etc/systemd/system/vigia-prueba.service >/dev/null
printf '[Unit]\nDescription=prueba\n[Timer]\nOnCalendar=hourly\n[Install]\nWantedBy=timers.target\n' | sudo tee /etc/systemd/system/vigia-prueba.timer >/dev/null
sudo systemctl daemon-reload && sudo systemctl enable vigia-prueba.timer >/dev/null 2>&1
docker run -d --name sano busybox sleep 600 >/dev/null
docker run -d --name enfermo --health-cmd false --health-interval 1s --health-retries 1 busybox sleep 600 >/dev/null
docker run --name parado busybox true >/dev/null
sleep 6
echo '{"guardias": ["linux"], "contenedores": true, "piezas": []}' > "$T/censo.json"
OUT="$(python3 "$H/descubrir.py" --censo "$T/censo.json" --maquina linux --json)"
for x in /srv/robot_linux.sh /usr/local/bin/rotar_linux.sh vigia-prueba.timer sano enfermo; do
  si "ve de verdad: $x" "printf '%s' \"\$OUT\" | grep -q '\"$x\"'" "$OUT"
done
si "el .service con su .timer cuenta una sola vez" "! printf '%s' \"\$OUT\" | grep -q 'vigia-prueba.service'" "$OUT"
si "no copia la orden del cron (ni la contraseña)" "! printf '%s' \"\$OUT\" | grep -q 'NO_DEBE_SALIR'" "$OUT"
si "un contenedor parado no cuenta como programado" "! printf '%s' \"\$OUT\" | grep -q '\"parado\"'" "$OUT"

echo "guardia.py con contenedores de verdad"
cat > "$T/contenedores.json" <<JSON
{"guardias": ["linux"], "piezas": [
  {"id": "c-sano", "estado": "VIGILADO", "comprobar": [{"contenedor": "sano"}]},
  {"id": "c-enfermo", "estado": "VIGILADO", "comprobar": [{"contenedor": "enfermo"}]},
  {"id": "c-parado", "estado": "VIGILADO", "comprobar": [{"contenedor": "parado"}]},
  {"id": "c-no-existe", "estado": "VIGILADO", "comprobar": [{"contenedor": "no-existe"}]}]}
JSON
OUT="$(python3 "$H/guardia.py" --censo "$T/contenedores.json" --maquina linux --estado "$T/e1.json")"
si "en marcha y sin chequeo de salud: verde" "printf '%s' \"\$OUT\" | grep -q '🟢 c-sano'" "$OUT"
for x in c-enfermo c-parado c-no-existe; do
  si "cae: $x" "printf '%s' \"\$OUT\" | grep -q '🔴 $x'" "$OUT"
done

echo "La torre, con la llave restringida por rrsync"
RR="$(command -v rrsync || true)"
if [ -z "$RR" ]; then
  for f in /usr/share/doc/rsync/scripts/rrsync /usr/share/doc/rsync/scripts/rrsync.gz; do
    [ -f "$f" ] && { case "$f" in *.gz) gunzip -c "$f" > "$T/rrsync" ;; *) cp "$f" "$T/rrsync" ;; esac; chmod +x "$T/rrsync"; RR="$T/rrsync"; break; }
  done
fi
if [ -z "$RR" ]; then
  mal "hay rrsync en esta máquina" "ni en el PATH ni en la documentación de rsync"
else
  ok "hay rrsync: $RR"
  mkdir -p "$T/buzon/cliente-a" "$T/buzon/cliente-b" "$T/partes" ~/.ssh && chmod 700 ~/.ssh
  ssh-keygen -t ed25519 -N '' -q -f "$T/llave"
  echo "restrict,command=\"$RR -wo $T/buzon/cliente-a\" $(cat "$T/llave.pub")" >> ~/.ssh/authorized_keys
  chmod 600 ~/.ssh/authorized_keys
  sudo systemctl start ssh 2>/dev/null || sudo service ssh start
  sleep 2
  SSH="ssh -i $T/llave -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o BatchMode=yes"
  echo '{"guardias": ["cliente-a", "torre"], "piezas": [{"id": "ok", "estado": "VIGILADO", "comprobar": [{"orden": "true"}]}]}' > "$T/cliente.json"
  python3 "$H/guardia.py" --censo "$T/cliente.json" --maquina cliente-a --estado "$T/e2.json" --parte "$T/partes/parte.txt" --avisar 'cat > /dev/null' >/dev/null
  rsync -a -e "$SSH" "$T/partes/parte.txt" localhost: 2>"$T/err1"
  si "el parte llega a su buzón con la línea del README (rsync -a parte.txt torre:)" "[ -s '$T/buzon/cliente-a/parte.txt' ]" "$(cat "$T/err1")"
  rsync -a -e "$SSH" "$T/partes/parte.txt" localhost:../cliente-b/ >/dev/null 2>&1
  si "no puede escribir en el buzón de otro cliente" "[ -z \"\$(ls -A '$T/buzon/cliente-b')\" ]" "$(ls -la "$T/buzon/cliente-b")"
  rsync -a -e "$SSH" localhost:parte.txt "$T/robado.txt" >/dev/null 2>&1
  si "no puede leer nada de la torre" "[ ! -e '$T/robado.txt' ]" "leyó el parte"
  SALIDA="$($SSH localhost 'cat /etc/passwd' 2>&1)"
  si "no puede abrir una terminal ni ejecutar órdenes" "! printf '%s' \"\$SALIDA\" | grep -q 'root:'" "$SALIDA"
  cat > "$T/torre.json" <<JSON
{"guardias": ["torre"], "buzon": "$T/buzon", "piezas": [
  {"id": "guardia-cliente-a", "estado": "VIGILADO", "maquina": "cliente-a", "vigila_desde": "torre",
   "comprobar": [{"fichero": "$T/buzon/cliente-a/parte.txt", "sla_horas": 3, "min_bytes": 20, "edad_por_contenido": true,
                  "no_contiene": "A CIEGAS", "contiene": "guardia de «cliente-a»"}]}]}
JSON
  OUT="$(python3 "$H/guardia.py" --censo "$T/torre.json" --maquina torre --estado "$T/e3.json")"; RC=$?
  si "la torre da por bueno el parte recién llegado (otra zona horaria no importa)" "[ $RC -eq 0 ] && printf '%s' \"\$OUT\" | grep -q '🟢 guardia-cliente-a'" "$OUT"
fi

echo
if [ "$FALLOS" -eq 0 ]; then echo "TODO BIEN EN LINUX"; else echo "$FALLOS FALLOS EN LINUX"; fi
[ "$FALLOS" -eq 0 ]

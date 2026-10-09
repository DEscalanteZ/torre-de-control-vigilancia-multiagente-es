#!/usr/bin/env python3
"""Prueba de las tres herramientas, con datos inventados en una carpeta temporal.

La mayoría de los casos rompen algo a propósito y miran que salta (una prueba que solo mira que el
programa corre no demuestra nada); el resto son controles: que lo sano dé verde y que no se repita
lo que no debe. No toca tu crontab, tus LaunchAgents ni nada fuera de la carpeta temporal: las
fuentes se simulan con las variables VIGIA_CRONTAB, VIGIA_CRON_D, VIGIA_LAUNCHD, VIGIA_SYSTEMD y
VIGIA_DOCKER, y Docker con un programa «docker» de mentira.
Uso: python3 herramientas/prueba.py   (sale con 0 si todo pasa).
Licencia: MIT.
"""
import http.server
import json
import os
import plistlib
import stat
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

H = Path(__file__).resolve().parent
FALLOS = []
LATIDOS = []
ES_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0   # a root los permisos no le frenan


def comprobar(nombre, condicion, detalle=""):
    print(f"  {'✓' if condicion else '✗'} {nombre}")
    if not condicion:
        FALLOS.append(nombre)
        if detalle:
            print("      " + str(detalle)[:900].replace("\n", "\n      "))


def correr(script, *args, entrada=None, env=None):
    r = subprocess.run([sys.executable, str(H / script), *args], input=entrada, capture_output=True,
                       text=True, errors="replace", env=env, timeout=120)
    return r.returncode, r.stdout, r.stderr


def censo(ruta, piezas, **extra):
    ruta.write_text(json.dumps(dict(piezas=piezas, **extra), ensure_ascii=False), encoding="utf-8")


def fichero(ruta, contenido, horas):
    ruta.write_bytes(contenido if isinstance(contenido, bytes) else b"x" * contenido)
    t = time.time() - horas * 3600
    os.utime(ruta, (t, t))


def servidor_local():
    class Manejador(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/latido":
                LATIDOS.append(time.time())
            codigo, cuerpo = {"/salud": (200, b"todo ok"), "/vacia": (200, b""), "/latido": (200, b"ok"),
                              "/aviso-roto": (500, b"no")}.get(self.path, (404, b"no"))
            self.send_response(codigo)
            self.end_headers()
            self.wfile.write(cuerpo)

        do_POST = do_GET

        def log_message(self, *a):
            pass
    srv = http.server.HTTPServer(("127.0.0.1", 0), Manejador)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}"


def fuentes(env, d, crontab="", launchd=None, systemd=None, docker=""):
    (d / "crontab").write_text(crontab, encoding="utf-8")
    (d / "docker").write_text(docker, encoding="utf-8")
    (d / "nada").mkdir(exist_ok=True)
    env.update(VIGIA_CRONTAB=str(d / "crontab"), VIGIA_CRON_D=str(d / "nada"),
               VIGIA_LAUNCHD=str(launchd or d / "nada"), VIGIA_SYSTEMD=str(systemd or d / "nada"),
               VIGIA_DOCKER=str(d / "docker"))


def prueba_descubrir(base, env):
    print("descubrir.py")
    d = base / "descubrir"
    d.mkdir()
    crond = d / "cron.d"
    crond.mkdir()
    (crond / "tienda").write_text("15 2 * * * root /usr/local/bin/rotar_tienda.sh\n", encoding="utf-8")
    la = d / "LaunchAgents"
    la.mkdir()
    with open(la / "com.ana.informe.plist", "wb") as f:
        plistlib.dump({"Label": "com.ana.informe", "ProgramArguments": ["/x"]}, f)
    with open(la / "com.ana.viejo.plist", "wb") as f:
        plistlib.dump({"Label": "com.ana.viejo", "Disabled": True}, f)
    with open(la / "com.google.keystone.agent.plist", "wb") as f:
        plistlib.dump({"Label": "com.google.keystone.agent"}, f)
    sd = d / "systemd"
    sd.mkdir()
    (sd / "copia.service").write_text("[Service]\n")
    (sd / "copia.timer").write_text("[Timer]\n")
    (sd / "web.service").write_text("[Service]\n")
    (sd / "enlace.service").symlink_to(sd / "web.service")
    fuentes(env, d, crontab="# comentario\nMAILTO=nadie\nSHELL=/bin/bash\n\n"
                            "0 3 * * * /srv/copia_nocturna.sh > /dev/null\n"
                            "@reboot /srv/arranque.sh\n"
                            "*/5 * * * * /usr/bin/python3 /srv/limpia.py\n"
                            "0 8 * * * API_TOKEN=sk-SECRETO123 /srv/informe.sh --password=OTRO https://u:p@x.io/ping\n"
                            "0 9 * * * curl -fsS -H 'Authorization: Bearer sk-live-ABC' -u admin:Hunter2 https://x.io/a\n"
                            "0 1 * * * mysqldump -uroot -pS3cr3tPass midb > /srv/copias/db.sql\n",
            launchd=la, systemd=sd, docker="asistente-atencion\nredis\n")
    env["VIGIA_CRON_D"] = str(crond)

    c = d / "censo.json"
    censo(c, [
        {"id": "copia", "huella": "copia_nocturna.sh", "estado": "VIGILADO", "maquina": "pc.local"},
        {"id": "informe", "huella": "com.ana.informe", "estado": "SIN VIGILAR"},
        {"id": "asistente", "huella": "asistente-*", "estado": "VIGILADO"},
        {"id": "copia-sd", "huella": "copia.timer", "estado": "VIGILADO"},
        {"id": "desaparecido", "huella": "ya_no_esta.sh", "estado": "VIGILADO"},
        {"id": "apagado", "huella": "boletin.py", "estado": "APAGADO"},
        {"id": "de-otra", "huella": "otra.sh", "estado": "VIGILADO", "maquina": "servidor"},
    ], ignorar=["com.google.*"], contenedores=True)
    rc, out, err = correr("descubrir.py", "--censo", str(c), "--maquina", "PC", "--json", env=env)
    r = json.loads(out)
    sin = sorted(v["huella"] for v in r["sin_censo"])
    curl = [h for h in sin if h.startswith("curl #")]
    comprobar("encuentra lo programado sin censo (cron, @reboot, cron.d, systemd, docker)",
              len(curl) == 1 and sorted(set(sin) - set(curl)) == sorted(
                  ["/srv/arranque.sh", "/srv/limpia.py", "/usr/local/bin/rotar_tienda.sh", "/srv/informe.sh",
                   "/srv/copias/db.sql", "web.service", "redis"]), sin)
    comprobar("la huella del cron no lleva nada de la orden: ni variables, ni contraseñas, ni cabeceras, ni direcciones",
              not any(x in out for x in ("SECRETO", "OTRO", "u:p@", "x.io", "Bearer", "sk-live", "Hunter2", "S3cr3t", "midb")), out)
    comprobar("ignora comentarios, variables, plists desactivados, enlaces de systemd y «ignorar»",
              not any(x in json.dumps(r["sin_censo"]) for x in ("MAILTO", "com.ana.viejo", "keystone", "enlace.service"))
              and r["ignorados"] == ["com.google.keystone.agent"], out)
    comprobar("el .service con su .timer cuenta una sola vez", "copia.service" not in sin, sin)
    comprobar("al revés: avisa de lo censado que no está programado (no de lo APAGADO ni de otra máquina)",
              [p["id"] for p in r["sin_correr"]] == ["desaparecido"], r["sin_correr"])
    comprobar("el nombre de máquina se compara sin dominio y sin mayúsculas («PC» = «pc.local»)",
              "copia_nocturna.sh" not in json.dumps(r["sin_censo"]), out)
    comprobar("sale con 1 si hay diferencias", rc == 1, err)

    rc, out, err = correr("descubrir.py", "--censo", str(c), "--maquina", "pc", "--apuntar", env=env)
    nuevo = json.loads(c.read_text(encoding="utf-8"))
    apuntadas = [p for p in nuevo["piezas"] if p.get("estado") == "SIN VIGILAR" and p["id"] != "informe"]
    copias = list(d.glob("censo.json.antes_*"))
    comprobar("--apuntar añade lo que falta como SIN VIGILAR y hace copia antes", len(apuntadas) == 8 and len(copias) == 1,
              out + err)
    ids = sorted(p["id"] for p in apuntadas)
    comprobar("--apuntar da ids con el nombre del script, no del intérprete",
              [i for i in ids if not i.startswith("curl-")] == sorted(["arranque-sh", "limpia-py", "rotar-tienda-sh",
                                                                        "informe-sh", "db-sql", "web-service", "redis"]), ids)
    comprobar("--apuntar con --maquina la apunta; sin secretos en el censo",
              all(p.get("maquina") == "pc" for p in apuntadas)
              and not any(x in c.read_text() for x in ("SECRETO", "Hunter2", "S3cr3t", "Bearer")), apuntadas[:1])
    rc, out, _ = correr("descubrir.py", "--censo", str(c), "--maquina", "pc", "--json", env=env)
    comprobar("después de apuntar, ya no queda nada sin censo (control)", rc == 1 and json.loads(out)["sin_censo"] == [], out)

    c2 = d / "censo2.json"
    censo(c2, [])
    correr("descubrir.py", "--censo", str(c2), "--apuntar", env=env)
    correr("descubrir.py", "--censo", str(c2), "--apuntar", env=env)
    comprobar("--apuntar sin --maquina no graba el nombre del equipo (puede cambiar solo)",
              all("maquina" not in p for p in json.loads(c2.read_text())["piezas"]), c2.read_text()[:300])
    copias = sorted(d.glob("censo2.json.antes_*"))
    comprobar("--apuntar deja copia del censo original (control)",
              len(copias) >= 1 and json.loads(copias[0].read_text())["piezas"] == [], [x.name for x in copias])
    # Dos apuntes en el mismo instante (reloj congelado): la segunda copia no puede pisar la primera.
    sys.path.insert(0, str(H))
    import descubrir as D

    class Congelado(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 1, 1, 12, 0, 0, 0)
    original, D.datetime = D.datetime, Congelado
    try:
        c9 = d / "censo9.json"
        censo(c9, [])
        D.apuntar(c9, D.C.leer(c9), None, [{"fuente": "cron", "huella": "/srv/uno.sh"}])
        D.apuntar(c9, D.C.leer(c9), None, [{"fuente": "cron", "huella": "/srv/dos.sh"}])
    finally:
        D.datetime = original
    copias = sorted(d.glob("censo9.json.antes_*"))
    comprobar("dos --apuntar en el mismo instante no pisan la copia anterior",
              len(copias) == 2 and json.loads(copias[0].read_text())["piezas"] == [], [x.name for x in copias])

    real = d / "sincronizado" / "censo.json"
    real.parent.mkdir()
    censo(real, [])
    os.chmod(real, 0o600)
    enlace = d / "enlace.json"
    enlace.symlink_to(real)
    correr("descubrir.py", "--censo", str(enlace), "--apuntar", env=env)
    comprobar("--apuntar sobre un censo enlazado escribe en el de verdad y conserva sus permisos",
              enlace.is_symlink() and len(json.loads(real.read_text())["piezas"]) > 0
              and stat.S_IMODE(real.stat().st_mode) == 0o600, oct(stat.S_IMODE(real.stat().st_mode)))

    fuentes(env, d, crontab="0 7 * * * python3 /srv/informe_ventas.py\n0 7 * * * python3 /srv/informe_ventas.py\n"
                            "0 9 * * * python3 /srv/informe_ventas.py --cliente-nuevo\n0 5 * * * python3 /srv/boletin.py\n")
    c3 = d / "censo3.json"
    censo(c3, [{"id": "ventas", "huella": "informe_ventas.py", "estado": "VIGILADO"},
               {"id": "boletin-antiguo", "huella": "boletin.py", "estado": "APAGADO"}])
    rc, out, _ = correr("descubrir.py", "--censo", str(c3), "--json", env=env)
    r = json.loads(out)
    comprobar("una pieza que casa con varias líneas (duplicada, mismo script con otros argumentos) se avisa",
              rc == 1 and len(r["varias"]) == 1 and len(r["varias"][0]["programado"]) == 3, out)
    comprobar("algo programado cuya pieza está APAGADO se avisa (alguien lo ha revivido)",
              any(v.get("estado_censo") == "APAGADO" and v["huella"] == "/srv/boletin.py" for v in r["sin_censo"]), out)
    censo(c3, [{"id": "ventas", "huella": "informe_ventas.py", "estado": "VIGILADO", "varias": True},
               {"id": "boletin", "huella": "boletin.py", "estado": "SIN VIGILAR"}])
    rc, out, _ = correr("descubrir.py", "--censo", str(c3), "--json", env=env)
    comprobar("con «varias»: true y la pieza viva, no se avisa (control)", rc == 0, out)

    censo(c3, [], ignorar="com.google.*")
    rc, out, err = correr("descubrir.py", "--censo", str(c3), env=env)
    comprobar("«ignorar» escrito como texto (no lista) se rechaza, en vez de taparlo todo", rc == 2 and "lista" in err, err)

    roto = d / "roto"
    roto.mkdir()
    (roto / "malo.plist").write_bytes(b"<?xml version=")
    fuentes(env, d, launchd=roto)
    rc, out, _ = correr("descubrir.py", "--censo", str(c2), env=env)
    comprobar("si no puede mirar algo, no da verde y sale con 1", rc == 1 and "🟢" not in out and "No he podido" in out, out)

    fuentes(env, d, docker="festive_hopper\n")
    censo(c3, [])
    rc, out, _ = correr("descubrir.py", "--censo", str(c3), "--json", env=env)
    comprobar("sin «contenedores»: true, los contenedores no cuentan (no hacen ruido) (control)",
              rc == 0 and json.loads(out)["sin_censo"] == [], out)


def docker_de_mentira(d):
    """Un «docker» que contesta a «inspect» según el nombre del contenedor."""
    carpeta = d / "bin"
    carpeta.mkdir()
    falso = carpeta / "docker"
    falso.write_text("#!/bin/sh\nfor a; do n=$a; done\ncase $n in\n"
                     "  sano) echo 'true|false|healthy';;\n  sin-chequeo) echo 'true|false|';;\n"
                     "  parado) echo 'false|false|';;\n  bucle) echo 'true|true|';;\n"
                     "  enfermo) echo 'true|false|unhealthy';;\n  arrancando) echo 'true|false|starting';;\n"
                     "  *) echo 'Error: No such object' >&2; exit 1;;\nesac\n")
    falso.chmod(0o755)
    return str(carpeta)


def prueba_guardia(base, env, url):
    print("guardia.py")
    d = base / "guardia"
    d.mkdir()
    fuentes(env, d)
    s = d / "salidas"
    s.mkdir()
    fichero(s / "sano.csv", 500, 1)
    fichero(s / "vacio.csv", 0, 1)
    fichero(s / "viejo.csv", 500, 50)
    c = d / "censo.json"
    piezas = [
        {"id": "sano", "estado": "VIGILADO", "comprobar": [{"fichero": str(s / "sano.csv"), "sla_horas": 26, "min_bytes": 100}]},
        {"id": "vacio", "estado": "VIGILADO", "comprobar": [{"fichero": str(s / "vacio.csv"), "sla_horas": 26}]},
        {"id": "viejo", "estado": "VIGILADO", "comprobar": [{"fichero": str(s / "viejo.csv"), "sla_horas": 26}]},
        {"id": "falta", "estado": "VIGILADO", "comprobar": [{"fichero": str(s / "no_existe.csv"), "sla_horas": 26}]},
        {"id": "sin-comprobar", "estado": "VIGILADO"},
        {"id": "web-bien", "estado": "VIGILADO", "comprobar": [{"url": url + "/salud", "contiene": "ok"}]},
        {"id": "web-404", "estado": "VIGILADO", "comprobar": [{"url": url + "/otra"}]},
        {"id": "web-sin-texto", "estado": "VIGILADO", "comprobar": [{"url": url + "/vacia", "contiene": "ok"}]},
        {"id": "orden-bien", "estado": "VIGILADO", "comprobar": [{"orden": "exit 0"}]},
        {"id": "orden-mal", "estado": "VIGILADO", "comprobar": [{"orden": "echo roto >&2; exit 4"}]},
        {"id": "mal-escrita", "estado": "VIGILADO", "comprobar": [{"fichero": "x", "url": "y"}]},
        {"id": "deuda", "estado": "SIN VIGILAR"},
        {"id": "otra-maquina", "estado": "VIGILADO", "maquina": "servidor", "comprobar": [{"orden": "exit 1"}]},
        {"id": "la-vigilo-yo", "estado": "VIGILADO", "maquina": "servidor", "vigila_desde": "torre",
         "comprobar": [{"fichero": str(s / "sano.csv"), "sla_horas": 26}]},
    ]
    censo(c, piezas)
    estado, avisos, parte = d / "estado.json", d / "avisos.txt", d / "parte.txt"
    avisar = f"cat >> '{avisos}'"
    base_args = ["--censo", str(c), "--maquina", "torre", "--estado", str(estado), "--avisar", avisar, "--parte", str(parte)]
    t0 = time.time()

    def pasada(horas, sanos=("sano.csv",), extra=()):
        """Una pasada «horas» después; los ficheros sanos se han actualizado hace una hora."""
        for n in sanos:
            os.utime(s / n, (t0 + (horas - 1) * 3600,) * 2)
        return correr("guardia.py", *base_args, *extra, "--ahora", str(t0 + horas * 3600), env=env)

    rc, out, err = pasada(0)
    lineas = {l.split(":")[0].strip(): l for l in out.splitlines() if l.startswith("  ")}
    verdes = sorted(k[2:] for k in lineas if k.startswith("🟢"))
    rojas = sorted(k[2:] for k in lineas if k.startswith("🔴"))
    comprobar("en verde solo lo sano (control)", verdes == ["la-vigilo-yo", "orden-bien", "sano", "web-bien"], out)
    comprobar("caen: vacío, viejo, falta, sin comprobación, 404, sin el texto, orden con error, mal escrita",
              rojas == sorted(["vacio", "viejo", "falta", "sin-comprobar", "web-404", "web-sin-texto", "orden-mal",
                               "mal-escrita"]), out)
    comprobar("no mira lo de otra máquina, sí lo que tiene «vigila_desde» suyo",
              "otra-maquina" not in out and "la-vigilo-yo" in out, out)
    comprobar("sale con 1 si algo está caído", rc == 1, err)
    a1 = avisos.read_text(encoding="utf-8")
    comprobar("primer aviso en 🟡, con la deuda", a1.count("🟡") == 8 and "1 pieza corre sin que nadie la vigile" in a1, a1)
    p = parte.read_text(encoding="utf-8")
    sello = datetime.fromtimestamp(t0).astimezone().isoformat(timespec="minutes")
    comprobar("deja el parte con la fecha y su zona horaria dentro, y el recuento",
              sello in p and "avisos abiertos: 8" in p, p)
    comprobar("el parte lleva solo el nombre de lo caído, no el motivo (ni rutas, ni URL, ni órdenes)",
              "🔴 orden-mal" in p and "roto" not in p and "127.0.0.1" not in p and str(s) not in p, p)

    avisos.write_text("")
    rc, _, _ = pasada(1)
    comprobar("una hora después, mismo tono: no repite (control)", rc == 1 and avisos.read_text(encoding="utf-8") == "",
              avisos.read_text())
    pasada(7)
    a = avisos.read_text(encoding="utf-8")
    comprobar("a las 7 h sube a 🟠 y dice cuánto lleva", a.count("🟠") == 8 and "lleva 7 h caída" in a, a)
    avisos.write_text("")
    pasada(25)
    a = avisos.read_text(encoding="utf-8")
    comprobar("a las 25 h sube a 🔴", a.count("🔴") == 8, a)

    fichero(s / "vacio.csv", 500, 0)
    avisos.write_text("")
    pasada(26, ("sano.csv", "vacio.csv"), extra=("--avisar", "exit 1"))
    pasada(26.5, ("sano.csv", "vacio.csv"))
    a = avisos.read_text(encoding="utf-8")
    comprobar("cuando vuelve, avisa en 🟢 (y si ese aviso falla, lo repite en la pasada siguiente)",
              "🟢 vacio vuelve a funcionar" in a and a.count("🔴") == 0, a)
    avisos.write_text("")
    pasada(49.5, ("sano.csv", "vacio.csv"))
    a = avisos.read_text(encoding="utf-8")
    comprobar("si sigue caído, recuerda una vez al día", a.count("🔴") == 7 and "vacio" not in a, a)
    avisos.write_text("")
    pasada(24 * 8, ("sano.csv", "vacio.csv"))
    comprobar("la deuda SIN VIGILAR se recuerda una vez por semana", "Recordatorio semanal" in avisos.read_text(), avisos.read_text())
    for n in ("sano.csv", "vacio.csv"):   # vuelta al tiempo real: que no queden fechas en el futuro
        fichero(s / n, 500, 1)

    # A ciegas: el aviso no sale → código 3, el parte lo dice y NO se da el latido.
    ciego = ["--censo", str(c), "--maquina", "torre", "--estado", str(d / "estado2.json"), "--parte", str(d / "parte2.txt"),
             "--latido", url + "/latido"]
    LATIDOS.clear()
    rc, out, err = correr("guardia.py", *ciego, "--avisar", "exit 1", env=env)
    comprobar("si no puede avisar: sale con 3, el parte dice A CIEGAS y no da el latido",
              rc == 3 and "A CIEGAS" in (d / "parte2.txt").read_text() and not LATIDOS, f"rc={rc} latidos={len(LATIDOS)} {err}")
    rc, out, err = correr("guardia.py", *ciego, "--avisar", f"curl -fsS -d @- {url}/aviso-roto", env=env)
    comprobar("con curl -fsS, un aviso rechazado (500) cuenta como no enviado", rc == 3, err)
    rc, out, err = correr("guardia.py", *ciego, env=env)
    comprobar("en marcha (con parte o latido) y sin --avisar, también está a ciegas", rc == 3 and not LATIDOS, err)
    rc, out, err = correr("guardia.py", *ciego, "--avisar", avisar, env=env)
    comprobar("y en la pasada siguiente vuelve a intentar el aviso y da el latido",
              rc == 1 and "🟡" in avisos.read_text(encoding="utf-8") and len(LATIDOS) == 1, out)
    if not ES_ROOT:
        solo_lectura = d / "ro"
        solo_lectura.mkdir()
        os.chmod(solo_lectura, 0o500)
        rc, out, err = correr("guardia.py", "--censo", str(c), "--maquina", "torre", "--estado", str(solo_lectura / "e.json"),
                              "--avisar", avisar, "--parte", str(d / "parte3.txt"), env=env)
        os.chmod(solo_lectura, 0o700)
        comprobar("si no puede guardar su memoria, también está a ciegas (3 y en el parte)",
                  rc == 3 and "A CIEGAS" in (d / "parte3.txt").read_text(), err)
    rc, out, err = correr("guardia.py", *base_args, "--latido", "http://127.0.0.1:9/nada", env=env)
    comprobar("si el latido falla, sale con 3", rc == 3 and "latido" in err, err)
    rc, out, err = correr("guardia.py", "--censo", str(d / "no_existe.json"), env=env)
    comprobar("sin censo, sale con 2", rc == 2, err)
    rc, _, _ = correr("guardia.py", "--censo", str(c), "--probar-aviso", "--avisar", "exit 1", env=env)
    rc2, _, _ = correr("guardia.py", "--censo", str(c), "--probar-aviso", "--avisar", "cat > /dev/null", env=env)
    comprobar("--probar-aviso sale con 3 si el canal falla y con 0 si funciona", rc == 3 and rc2 == 0, (rc, rc2))

    # Lo que antes tumbaba la guardia entera, o daba verde sin serlo.
    hoy = datetime.now().strftime("%Y-%m-%d")
    fichero(s / "sin_permiso.csv", 500, 1)
    os.chmod(s / "sin_permiso.csv", 0)
    fichero(s / "futuro.csv", 500, -24 * 400)
    fichero(s / "rancio.csv", b"fecha;ventas\n2026-01-02;10\n", 0)
    fichero(s / "parte_copiado.txt", b"PARTE \xc2\xb7 guardia \xc2\xb7 2026-01-01T10:00+02:00\nvigiladas: 3\n", 0)
    fichero(s / "parte_ciego.txt", f"PARTE · {hoy}T10:00\n⚠️ A CIEGAS: x\n".encode(), 0)
    fichero(s / "grande.csv", b"x" * 6_000_000 + f"\n{hoy};fin\n".encode(), 0)
    c2 = d / "censo2.json"
    censo(c2, [
        {"id": "no-utf8", "estado": "VIGILADO", "comprobar": [{"orden": "printf 'error \\377'; exit 1"}]},
        {"id": "sla-texto", "estado": "VIGILADO", "comprobar": [{"fichero": str(s / "sano.csv"), "sla_horas": "26h"}]},
        {"id": "sin-plazo", "estado": "VIGILADO", "comprobar": [{"fichero": str(s / "parte_copiado.txt"), "edad_por_contenido": True}]},
        {"id": "sin-permiso", "estado": "VIGILADO", "comprobar": [{"fichero": str(s / "sin_permiso.csv"), "sla_horas": 26, "contiene": "xxxx"}]},
        {"id": "futuro", "estado": "VIGILADO", "comprobar": [{"fichero": str(s / "futuro.csv"), "sla_horas": 26}]},
        {"id": "rancio", "estado": "VIGILADO", "comprobar": [{"fichero": str(s / "rancio.csv"), "sla_horas": 26, "contiene": "{hoy}"}]},
        {"id": "parte-copiado", "estado": "VIGILADO", "comprobar": [{"fichero": str(s / "parte_copiado.txt"), "sla_horas": 3, "edad_por_contenido": True}]},
        {"id": "parte-ciego", "estado": "VIGILADO", "comprobar": [{"fichero": str(s / "parte_ciego.txt"), "sla_horas": 9999, "edad_por_contenido": True, "no_contiene": "A CIEGAS"}]},
        {"id": "repe", "estado": "VIGILADO", "comprobar": [{"orden": "exit 1"}]},
        {"id": "repe", "estado": "VIGILADO", "comprobar": [{"orden": "exit 0"}]},
        {"id": "mal-estado", "estado": "Vigilado", "comprobar": [{"orden": "exit 1"}]},
        {"id": "no-lista", "estado": "VIGILADO", "comprobar": 26},
        {"id": "colgada", "estado": "VIGILADO", "comprobar": [{"orden": "sleep 30", "timeout": 1}]},
        {"id": "sigue-sano", "estado": "VIGILADO", "comprobar": [{"fichero": str(s / "sano.csv"), "sla_horas": 26}]},
        {"id": "grande-con-hoy", "estado": "VIGILADO", "comprobar": [{"fichero": str(s / "grande.csv"), "sla_horas": 26, "contiene": "{hoy}"}]},
    ])
    inicio = time.time()
    rc, out, err = correr("guardia.py", "--censo", str(c2), "--maquina", "torre", "--estado", str(d / "e2.json"), env=env)
    tardo = time.time() - inicio
    os.chmod(s / "sin_permiso.csv", 0o600)
    rojo = lambda i: any(l.startswith(f"  🔴 {i}") for l in out.splitlines())  # noqa: E731
    comprobar("una salida que no es UTF-8 no tumba la guardia (cuenta como caída y sigue)", rojo("no-utf8") and "Traceback" not in err, err[-300:])
    comprobar("un número escrito como texto no tumba la guardia", rojo("sla-texto"), out)
    comprobar("un fichero sin «sla_horas» cae aunque mire la fecha de dentro (si no, no caduca nunca)", rojo("sin-plazo"), out)
    if not ES_ROOT:
        comprobar("un fichero sin permiso de lectura cuenta como caída", rojo("sin-permiso"), out)
    comprobar("un fichero con fecha en el futuro cae (si no, no caduca nunca)", rojo("futuro"), out)
    comprobar("«contiene»: «{hoy}» caza el fichero rancio que se ha vuelto a publicar hoy", rojo("rancio"), out)
    comprobar("«edad_por_contenido» caza el parte viejo copiado con fecha nueva", rojo("parte-copiado"), out)
    comprobar("«no_contiene»: la torre ve el parte de una guardia a ciegas", rojo("parte-ciego"), out)
    comprobar("dos piezas con el mismo id no se tapan: se avisa", rojo("repe (repetido)"), out)
    comprobar("un estado mal escrito no hace desaparecer la pieza: se avisa", rojo("mal-estado (estado)"), out)
    comprobar("«comprobar» que no es una lista cuenta como caída y no tumba la guardia", rojo("no-lista"), out)
    comprobar("una orden que no termina se corta con su «timeout»", rojo("colgada") and tardo < 25, f"{tardo:.0f} s")
    comprobar("«contiene» busca también al final de un fichero de más de 5 MB (control)", "🟢 grande-con-hoy" in out, out)
    comprobar("y lo sano sigue en verde (control)", "🟢 sigue-sano" in out, out)

    # La torre en otra zona horaria que la guardia: la fecha del parte lleva su zona y no engaña.
    hace_una_hora = (datetime.now(timezone.utc) - timedelta(hours=1)).astimezone(timezone(timedelta(hours=2)))
    fichero(s / "parte_madrid.txt", f"PARTE · {hace_una_hora.isoformat(timespec='minutes')}\n".encode(), 0)
    c6 = d / "censo6.json"
    censo(c6, [{"id": "parte-otra-zona", "estado": "VIGILADO",
                "comprobar": [{"fichero": str(s / "parte_madrid.txt"), "sla_horas": 2, "edad_por_contenido": True}]}])
    rc, out, _ = correr("guardia.py", "--censo", str(c6), "--estado", str(d / "e7.json"), env=dict(env, TZ="UTC"))
    comprobar("un parte escrito en otra zona horaria se lee bien (control)", rc == 0 and "hace 1.0 h" in out, out)

    c3 = d / "censo3.json"
    grande = s / "igual.bin"
    grande.write_bytes(b"a" * 6_000_000)
    censo(c3, [{"id": "igual", "estado": "VIGILADO", "comprobar": [{"fichero": str(grande), "sla_horas": 2, "debe_cambiar": True}]}])
    e3 = str(d / "e3.json")
    t = time.time()
    os.utime(grande, (t, t))
    rc1, out1, _ = correr("guardia.py", "--censo", str(c3), "--estado", e3, "--ahora", str(t), env=env)
    with open(grande, "r+b") as f:   # un fichero de más de 5 MB que solo cambia al final
        f.seek(-1, os.SEEK_END)
        f.write(b"b")
    os.utime(grande, (t + 3 * 3600,) * 2)
    rc2, out2, _ = correr("guardia.py", "--censo", str(c3), "--estado", e3, "--ahora", str(t + 3 * 3600), env=env)
    os.utime(grande, (t + 6 * 3600,) * 2)   # y luego el robot lo vuelve a publicar, igual que estaba
    rc3, out3, _ = correr("guardia.py", "--censo", str(c3), "--estado", e3, "--ahora", str(t + 6.5 * 3600), env=env)
    comprobar("«debe_cambiar»: mira el fichero entero, y el mismo contenido republicado más de sla_horas cae",
              rc1 == 0 and rc2 == 0 and rc3 == 1 and "mismo contenido" in out3, out1 + out2 + out3)

    c5 = d / "censo5.json"
    censo(c5, [{"id": "x", "estado": "VIGILADO", "maquina": "servidor", "comprobar": [{"orden": "exit 0"}]}])
    rc, out, _ = correr("guardia.py", "--censo", str(c5), "--maquina", "servidor.example.com", "--estado", str(d / "e6.json"), env=env)
    comprobar("el nombre completo de la máquina casa con el corto (control)", rc == 0 and "1 vigiladas" in out, out)
    rc, out, _ = correr("guardia.py", "--censo", str(c5), "--maquina", "otro-nombre", "--estado", str(d / "e4.json"), env=env)
    comprobar("si la máquina no cuadra y no vigila nada, no da verde: lo dice y se declara a ciegas (3)",
              rc == 3 and "no vigila nada" in out, out)
    c8 = d / "censo8.json"
    censo(c8, [{"id": "a", "estado": "SIN VIGILAR"}, {"id": "b", "estado": "SIN VIGILAR"}])
    rc, out, _ = correr("guardia.py", "--censo", str(c8), "--estado", str(d / "e11.json"), "--parte", str(d / "parte8.txt"),
                        "--avisar", "cat > /dev/null", env=env)
    comprobar("con todo SIN VIGILAR (justo después de --apuntar) tampoco da verde: el parte dice A CIEGAS",
              rc == 3 and "A CIEGAS" in (d / "parte8.txt").read_text(), out)
    censo(c5, [{"id": "x", "estado": "VIGILADO", "maquina": "servidor", "comprobar": [{"orden": "exit 1"}]},
               {"id": "comun", "estado": "VIGILADO", "comprobar": [{"orden": "exit 0"}]}], guardias=["servidor", "torre"])
    rc, out, _ = correr("guardia.py", "--censo", str(c5), "--maquina", "srv", "--estado", str(d / "e8.json"), env=env)
    comprobar("con «guardias» en el censo, un --maquina mal puesto se avisa aunque le toquen piezas comunes",
              rc in (1, 3) and "no está en «guardias»" in out, out)

    buzon = d / "buzon"
    for cliente in ("clinica", "nuevo"):
        (buzon / cliente).mkdir(parents=True)
        fichero(buzon / cliente / "parte.txt", f"PARTE · guardia de «{cliente}» · {datetime.now().astimezone().isoformat(timespec='minutes')}\n".encode(), 0)
    c9 = d / "torre.json"
    censo(c9, [{"id": "guardia-clinica", "estado": "VIGILADO", "maquina": "clinica", "vigila_desde": "torre",
                "comprobar": [{"fichero": str(buzon / "clinica" / "parte.txt"), "sla_horas": 3, "edad_por_contenido": True,
                               "no_contiene": "A CIEGAS", "contiene": "guardia de «clinica»"}]}], buzon=str(buzon))
    (buzon / "vacio").mkdir()
    rc, out, _ = correr("guardia.py", "--censo", str(c9), "--maquina", "torre", "--estado", str(d / "e12.json"), env=env)
    comprobar("la torre avisa de un buzón que recibe partes y que ninguna pieza mira (un cliente nuevo sin apuntar)",
              rc == 1 and "🔴 buzon/nuevo" in out and "🟢 guardia-clinica" in out and "buzon/vacio" not in out, out)
    fichero(buzon / "clinica" / "parte.txt", f"PARTE · guardia de «web» · {datetime.now().astimezone().isoformat(timespec='minutes')}\n".encode(), 0)
    rc, out, _ = correr("guardia.py", "--censo", str(c9), "--maquina", "torre", "--estado", str(d / "e13.json"), env=env)
    comprobar("la torre no acepta en un buzón el parte de otra máquina («contiene»: «guardia de «…»»)",
              "🔴 guardia-clinica" in out, out)

    fuentes(env, d, crontab="0 * * * * /srv/robot_nuevo.sh\n")
    c4 = d / "censo4.json"
    censo(c4, [{"id": "ok", "estado": "VIGILADO", "comprobar": [{"orden": "exit 0"}]},
               {"id": "borrado", "huella": "copia_vieja.sh", "estado": "SIN VIGILAR"}])
    rc, out, _ = correr("guardia.py", "--censo", str(c4), "--descubrir", "--estado", str(d / "e5.json"), env=env)
    comprobar("--descubrir avisa de lo programado sin censo y de lo SIN VIGILAR que ya no está programado",
              rc == 1 and "sin censo: [cron] /srv/robot_nuevo.sh" in out and "no programado: borrado" in out, out)

    env_docker = dict(env, PATH=docker_de_mentira(d) + os.pathsep + env.get("PATH", ""))
    c7 = d / "censo7.json"
    censo(c7, [{"id": n, "estado": "VIGILADO", "comprobar": [{"contenedor": n}]}
               for n in ("sano", "sin-chequeo", "parado", "bucle", "enfermo", "arrancando", "no-existe")])
    rc, out, err = correr("guardia.py", "--censo", str(c7), "--estado", str(d / "e9.json"), env=env_docker)
    verdes = sorted(l.split(":")[0][4:] for l in out.splitlines() if l.startswith("  🟢"))
    rojas = sorted(l.split(":")[0][4:] for l in out.splitlines() if l.startswith("  🔴"))
    comprobar("contenedores: caen el parado, el que se reinicia, el no sano, el que arranca y el que no existe",
              verdes == ["sano", "sin-chequeo"] and rojas == sorted(["parado", "bucle", "enfermo", "arrancando", "no-existe"]),
              out + err)

    rc, out, err = correr("guardia.py", "--censo", str(c4), "--estado", str(d / "e10.json"),
                          env=dict(env, LC_ALL="en_US.ISO8859-1", LANG="en_US.ISO8859-1", PYTHONIOENCODING="latin-1"))
    comprobar("con un idioma que no es UTF-8, la guardia no se cae al escribir los símbolos", rc in (0, 1) and "Traceback" not in err, err[-300:])

    r = d / "censo_r.json"
    censo(r, [
        {"id": "a", "nombre": "x", "estado": "VIGILADO", "comprobar": [{"fichero": "/x"}]},
        {"id": "b", "nombre": "x", "estado": "VIGILADO", "comprobar": [{"fichero": "/x", "sla_horas": 1, "min_bytes": 0}]},
        {"id": "c", "nombre": "x", "estado": "VIGILADO"},
        {"id": "c", "nombre": "x", "estado": "RARO", "depende_de": ["fantasma"]},
        {"id": "d", "nombre": "PENDIENTE: algo", "estado": "SIN VIGILAR", "huella": "python3"},
        {"id": "e", "nombre": "x", "estado": "VIGILADO", "comprobar": [{"url": "https://x"}]},
        {"id": "f", "nombre": "x", "estado": "VIGILADO", "comprobar": [{"fichero": "/x", "sla_horas": "26h"}]},
        {"id": "g", "nombre": "x", "estado": "VIGILADO", "comprobar": [{"contenedor": "web"}]},
        {"id": "h", "nombre": "x", "estado": "VIGILADO", "maquina": "fantasma", "comprobar": [{"orden": "x"}]},
        {"id": "i", "nombre": "x", "estado": "VIGILADO", "comprobar": [{"fichero": "/x", "edad_por_contenido": True}]},
        {"id": "j", "nombre": "x", "estado": "VIGILADO", "comprobar": [{"fichero": "/x", "sla_horas": 3, "no_contien": "A CIEGAS"}]},
        {"id": "k", "nombre": "x", "estado": "VIGILADO", "comprobar": [{"url": "https://x", "contiene": "ok"}]},
        {"id": "l", "nombre": "x", "estado": "VIGILADO", "comprobar": 5},
    ], guardias=["servidor"], ignorar=["*"])
    rc, out, _ = correr("guardia.py", "--censo", str(r), "--revisar", env=env)
    esperados = ["a: /x sin «sla_horas»", "b: /x con min_bytes por debajo de 1", "c: VIGILADO sin ninguna comprobación",
                 "id repetido: c", "estado «RARO»", "depende de «fantasma»", "d: falta decir qué hace",
                 "e: https://x sin «contiene»", "f: «sla_horas» tiene que ser un número", "a: /x sin «min_bytes»",
                 "a: /x no mira si el contenido es de hoy", "g: solo mira que el contenedor", "d: huella «python3» demasiado genérica",
                 "h: la vigila «fantasma», y ahí no hay ninguna guardia", "i: /x sin «sla_horas»",
                 "j: «fichero» no entiende no_contien", "k: «contiene»: «ok» es tan corto", "l: «comprobar» tiene que ser una lista",
                 "«ignorar» tiene «*», que tapa casi cualquier cosa", "Deuda: 1 pieza corre SIN VIGILAR: d"]
    faltan = [e for e in esperados if e not in out]
    comprobar("--revisar caza las comprobaciones que no pueden fallar y el censo que miente", not faltan and rc == 1,
              f"faltan {faltan}\n{out}")
    censo(r, [{"id": "a", "nombre": "x", "estado": "VIGILADO",
               "comprobar": [{"fichero": "/x", "sla_horas": 2, "min_bytes": 10, "contiene": "{hoy}"}]}], guardias=[])
    rc, out, _ = correr("guardia.py", "--censo", str(r), "--revisar", env=env)
    comprobar("--revisar con un censo sano sale con 0 y no promete de más (control)",
              rc == 0 and "🟢" in out and "rómpelas" in out, out)


def prueba_candado(base, env):
    print("candado_alta.py")
    d = base / "candado"
    d.mkdir()
    fuentes(env, d, crontab="0 3 * * * /srv/copia_nocturna.sh\n")
    c = d / "censo.json"
    censo(c, [{"id": "copia", "huella": "copia_nocturna.sh", "estado": "VIGILADO"},
              {"id": "viejo", "huella": "boletin.py", "estado": "APAGADO"}])
    marcas = d / "marcas.json"
    args = ["--censo", str(c), "--maquina", "pc", "--marcas", str(marcas)]
    s1 = json.dumps({"session_id": "s1", "hook_event_name": "Stop", "stop_hook_active": False})
    s2 = json.dumps({"session_id": "s2", "hook_event_name": "Stop", "stop_hook_active": False})

    rc, out, _ = correr("candado_alta.py", *args, entrada=s1, env=env)
    comprobar("con todo censado, deja pasar en silencio (control)", rc == 0 and out == "", out)
    (d / "crontab").write_text("0 3 * * * /srv/copia_nocturna.sh\n0 * * * * /srv/robot_nuevo.sh\n", encoding="utf-8")
    rc, out, _ = correr("candado_alta.py", *args, entrada=json.dumps({"session_id": "s1", "stop_hook_active": True}), env=env)
    comprobar("viniendo de un freno (stop_hook_active), deja pasar aunque haya algo sin censar", rc == 0 and out == "", out)
    rc, out, _ = correr("candado_alta.py", *args, entrada=s1, env=env)
    salida = json.loads(out) if out.strip() else {}
    motivo = salida.get("reason", "")
    comprobar("si hay algo programado sin censar, frena y le dice qué hacer",
              salida.get("decision") == "block" and "/srv/robot_nuevo.sh" in motivo, out)
    comprobar("y le pide SIN VIGILAR y ver fallar la comprobación antes de VIGILADO (no le empuja a vigilar sin probar)",
              "SIN VIGILAR" in motivo and "visto fallar" in motivo and "una orden" not in motivo, motivo)
    rc, out, _ = correr("candado_alta.py", *args, entrada=s1, env=env)
    comprobar("en la misma sesión no insiste con la misma pieza (control)", rc == 0 and out == "", out)
    rc, out, _ = correr("candado_alta.py", *args, entrada=s2, env=env)
    comprobar("en una sesión nueva, si sigue sin censar, lo vuelve a pedir", "/srv/robot_nuevo.sh" in out, out)
    (d / "crontab").write_text((d / "crontab").read_text() + "5 * * * * /srv/otro.sh\n0 4 * * * /srv/copia_nocturna.sh\n"
                               "0 6 * * * python3 /srv/boletin.py\n", encoding="utf-8")
    rc, out, _ = correr("candado_alta.py", *args, entrada=s1, env=env)
    comprobar("lo nuevo, una línea duplicada y una pieza APAGADO revivida vuelven a frenar, y solo nombra lo nuevo",
              "/srv/otro.sh" in out and "casa con 2" in out and "APAGADO" in out and "robot_nuevo" not in out, out)

    roto_dir = d / "roto"
    roto_dir.mkdir()
    (roto_dir / "malo.plist").write_bytes(b"<?xml")
    env_roto = dict(env, VIGIA_LAUNCHD=str(roto_dir))
    rc, out, _ = correr("candado_alta.py", *args, entrada=json.dumps({"session_id": "s3"}), env=env_roto)
    salida = json.loads(out) if out.strip() else {}
    comprobar("lo que no ha podido mirar se lo dice al humano (systemMessage)", "no ha podido mirar" in salida.get("systemMessage", ""), out)

    roto = ["--censo", str(d / "no_existe.json"), "--marcas", str(marcas)]
    rc, out, err = correr("candado_alta.py", *roto, entrada=s1, env=env)
    salida = json.loads(out) if out.strip() else {}
    comprobar("si está roto por dentro, no frena al agente pero se lo dice al humano (systemMessage)",
              rc == 0 and "decision" not in salida and "no funciona" in salida.get("systemMessage", ""), out + err)
    rc, out, err = correr("candado_alta.py", *roto, entrada=s1, env=env)
    comprobar("y ese aviso de roto sale una vez por sesión, no en cada respuesta (control)", rc == 0 and out == "", out)

    orden = ("python3 /ruta/que/no/existe/candado_alta.py --censo x || echo "
             "'{\"systemMessage\": \"⚠️ El candado de alta no arranca\"}'")
    r = subprocess.run(["sh", "-c", orden], input=s1, capture_output=True, text=True)
    salida = json.loads(r.stdout) if r.stdout.strip() else {}
    comprobar("si no arranca (ruta movida), la instalación del README avisa igual al humano",
              r.returncode == 0 and "no arranca" in salida.get("systemMessage", ""), r.stdout + r.stderr)


def main():
    srv, url = servidor_local()
    with tempfile.TemporaryDirectory() as t:
        base = Path(t)
        env = dict(os.environ)
        for v in ("CENSO", "VIGIA_MAQUINA"):
            env.pop(v, None)
        prueba_descubrir(base, env)
        prueba_guardia(base, env, url)
        prueba_candado(base, env)
    srv.shutdown()
    print(f"\n{'TODO BIEN' if not FALLOS else f'{len(FALLOS)} FALLOS'}")
    return 1 if FALLOS else 0


if __name__ == "__main__":
    sys.exit(main())

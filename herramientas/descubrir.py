#!/usr/bin/env python3
"""Descubrir: ¿qué está programado en esta máquina sin estar en el censo? ¿Y qué está en el censo y ya no?

Las dos direcciones de la misma regla: lo que no está en el censo no debería estar corriendo, y lo
que está en el censo y no corre es una alarma.

Ojo: mira lo que está PROGRAMADO (la línea de cron, el plist, la unidad de systemd), no si esa
programación funciona. Un plist que está en su carpeta pero no se cargó cuenta igual. Que
funcione lo dice la guardia, mirando lo que produce.

Dónde mira (solo lo que existe en esta máquina):
  - tu crontab (crontab -l) y /etc/cron.d/
  - macOS: ~/Library/LaunchAgents/ (y /Library/LaunchAgents y /Library/LaunchDaemons si el censo
    dice "launchd_sistema": true, o con --sistema)
  - Linux: los ficheros de unidad de /etc/systemd/system/ y ~/.config/systemd/user/
  - Docker: los contenedores en marcha, solo si el censo dice "contenedores": true (los que crean
    las herramientas de desarrollo con nombres al azar harían ruido)
No ve pm2, supervisord, procesos lanzados a mano ni lo programado en otras máquinas o en la nube.

Cómo casa lo que encuentra con el censo: por el campo «huella» de cada pieza VIGILADA o SIN VIGILAR.
Para launchd, systemd y Docker, la huella es la etiqueta, el nombre de la unidad o el del contenedor
(admite comodines: *). Para el cron, la huella es la ruta del script que ejecuta la línea (o el
nombre del programa y un resumen numérico, si no hay script): nunca la orden entera, que puede
llevar contraseñas. Una huella del censo casa si aparece dentro de la de la línea.
Avisa también de: una pieza que casa con varias cosas programadas (una línea duplicada, el mismo
script con otros argumentos; si es a propósito, «"varias": true»), y algo programado cuya pieza el
censo da por APAGADO, EN OBRAS o PREVISTO (alguien lo ha revivido).

Uso:
  python3 descubrir.py --censo censo.json --maquina mimac            # informe
  python3 descubrir.py --censo censo.json --maquina mimac --apuntar  # apunta lo que falta como «SIN VIGILAR»
  python3 descubrir.py --censo censo.json --json                     # para otras herramientas

--apuntar hace antes una copia del censo (censo.json.antes_AAAAMMDD-HHMMSS-micro) y nunca borra nada.
Sale con 0 si todo cuadra, 1 si hay diferencias o no ha podido mirar algo, y 2 si no ha podido
leer el censo. Licencia: MIT.
"""
import argparse
import fnmatch
import json
import os
import platform
import plistlib
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import censo as C  # noqa: E402

VARIABLE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\s*=")
DOCKER_HABITUAL = ("/usr/local/bin/docker", "/opt/homebrew/bin/docker", "/usr/bin/docker")


def _dirs(variable, defecto):
    """Las carpetas a mirar; una variable de entorno las sustituye (la usan la prueba y la demo)."""
    cruda = os.environ.get(variable)
    lista = cruda.split(os.pathsep) if cruda is not None else defecto
    return [Path(os.path.expanduser(d)) for d in lista if d]


def ordenes_cron(texto, con_usuario=False):
    """[(huella, horario)] de un crontab: la huella sale de la orden (ver censo.huella_cron) y el
    horario es lo de delante (minuto, hora...). La orden entera no sale de aquí."""
    salida = []
    for linea in texto.splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or VARIABLE.match(linea):
            continue
        if linea.startswith("@"):
            n = 2 if con_usuario else 1
        else:
            n = 6 if con_usuario else 5
        partes = linea.split(None, n)
        if len(partes) > n:
            salida.append((C.huella_cron(partes[n]), " ".join(partes[:n])))
    return salida


def mirar_cron(fallos):
    vistos = []
    fichero = os.environ.get("VIGIA_CRONTAB")
    if fichero is not None:
        texto = Path(fichero).read_text(encoding="utf-8") if fichero and Path(fichero).exists() else ""
    elif shutil.which("crontab"):
        try:
            r = subprocess.run(["crontab", "-l"], capture_output=True, text=True, errors="replace", timeout=10)
            texto = r.stdout if r.returncode == 0 else ""
            if r.returncode != 0 and "no crontab" not in (r.stderr or "").lower():   # sin crontab no es un fallo
                fallos.append(f"crontab ({(r.stderr or '').strip()[:80] or r.returncode})")
        except (OSError, subprocess.SubprocessError) as e:
            fallos.append(f"crontab ({type(e).__name__})")
            texto = ""
    else:
        texto = ""
    for huella, horario in ordenes_cron(texto):
        vistos.append({"fuente": "cron", "huella": huella, "detalle": horario})
    for d in _dirs("VIGIA_CRON_D", ["/etc/cron.d"]):
        if not d.is_dir():
            continue
        for f in sorted(d.iterdir()):
            if f.name.startswith(".") or not f.is_file():
                continue
            try:
                texto = f.read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                fallos.append(f"{f} ({e.strerror})")
                continue
            for huella, horario in ordenes_cron(texto, con_usuario=True):
                vistos.append({"fuente": "cron", "huella": huella, "detalle": f"{f.name}: {horario}"})
    return vistos


def mirar_launchd(sistema, fallos):
    defecto = []
    if platform.system() == "Darwin":
        defecto = ["~/Library/LaunchAgents"] + (["/Library/LaunchAgents", "/Library/LaunchDaemons"] if sistema else [])
    vistos = []
    for d in _dirs("VIGIA_LAUNCHD", defecto):
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.plist")):
            try:
                with open(f, "rb") as fh:
                    datos = plistlib.load(fh)
            except Exception as e:  # plist roto o ilegible: se dice, no se calla
                fallos.append(f"{f.name} ({type(e).__name__})")
                continue
            if not isinstance(datos, dict) or datos.get("Disabled") is True:
                continue
            etiqueta = str(datos.get("Label") or f.stem)
            vistos.append({"fuente": "launchd", "huella": etiqueta, "detalle": str(f)})
    return vistos


def mirar_systemd():
    defecto = []
    if platform.system() == "Linux":
        defecto = ["/etc/systemd/system", "~/.config/systemd/user"]
    vistos = []
    for d in _dirs("VIGIA_SYSTEMD", defecto):
        if not d.is_dir():
            continue
        # Solo ficheros de verdad: los enlaces son los que pone el sistema al habilitar o enmascarar.
        unidades = {f.name for f in d.iterdir()
                    if f.suffix in (".service", ".timer") and f.is_file() and not f.is_symlink()}
        for nombre in sorted(unidades):
            if nombre.endswith(".service") and nombre[:-8] + ".timer" in unidades:
                continue   # el temporizador ya representa a su servicio
            vistos.append({"fuente": "systemd", "huella": nombre, "detalle": str(d / nombre)})
    return vistos


def mirar_docker(fallos):
    fichero = os.environ.get("VIGIA_DOCKER")
    if fichero is not None:
        nombres = Path(fichero).read_text(encoding="utf-8").split() if fichero and Path(fichero).exists() else []
        return [{"fuente": "contenedor", "huella": n, "detalle": "docker ps"} for n in nombres]
    docker = shutil.which("docker")
    if not docker:
        perdido = next((r for r in DOCKER_HABITUAL if os.path.exists(r)), None)
        if perdido:   # típico de cron: docker existe pero no está en su PATH
            fallos.append(f"docker (está en {perdido}, pero no en el PATH de quien me lanza)")
        return []
    try:
        r = subprocess.run([docker, "ps", "--format", "{{.Names}}"], capture_output=True, text=True,
                           errors="replace", timeout=8)
    except (OSError, subprocess.SubprocessError) as e:
        fallos.append(f"docker ({type(e).__name__})")
        return []
    if r.returncode != 0:
        fallos.append("docker (no responde: ¿está arrancado?)")
        return []
    return [{"fuente": "contenedor", "huella": n, "detalle": "docker ps"} for n in r.stdout.split()]


def mirar_todo(censo, sistema=False):
    fallos = []
    vistos = mirar_cron(fallos) + mirar_launchd(sistema or censo.get("launchd_sistema", False), fallos) + mirar_systemd()
    if censo.get("contenedores"):
        vistos += mirar_docker(fallos)
    return vistos, fallos


def casa(huella, visto):
    if huella == visto["huella"] or fnmatch.fnmatchcase(visto["huella"], huella):
        return True
    return visto["fuente"] == "cron" and huella in visto["huella"]


def comparar(censo, maquina, vistos):
    """Devuelve (lo programado sin pieza viva en el censo, piezas vivas sin programar, piezas que casan
    con varias cosas programadas: [(pieza, [vistos])], lo que el patrón «ignorar» ha tapado)."""
    mias = [p for p in censo["piezas"] if isinstance(p, dict) and C.de_esta_maquina(p, maquina)]
    vivas = [p for p in mias if p.get("estado") in C.VIVOS]
    muertas = [p for p in mias if p.get("estado") not in C.VIVOS]
    ignorar = censo.get("ignorar", [])
    sin_censo, ignorados, por_pieza = [], [], {}
    for v in vistos:
        if any(fnmatch.fnmatchcase(v["huella"], pat) for pat in ignorar):
            ignorados.append(v)
            continue
        duenas = [p for p in vivas if any(casa(h, v) for h in C.huellas(p))]
        if not duenas:
            revivida = next((p for p in muertas if any(casa(h, v) for h in C.huellas(p))), None)
            sin_censo.append(dict(v, estado_censo=revivida.get("estado"), id_censo=revivida.get("id")) if revivida else v)
        for p in duenas:
            por_pieza.setdefault(id(p), (p, []))[1].append(v)
    sin_correr = [p for p in vivas if C.huellas(p) and id(p) not in por_pieza]
    varias = [(p, vs) for p, vs in por_pieza.values() if len(vs) > 1 and not p.get("varias")]
    return sin_censo, sin_correr, varias, ignorados


def texto_de(v):
    extra = f" (el censo dice {v['estado_censo']}: «{v['id_censo']}»)" if v.get("estado_censo") else ""
    return f"[{v['fuente']}] {v['huella']}{extra}"


def nuevo_id(huella, usados):
    base = huella.split(" #")[0] if " #" in huella else os.path.basename(huella.rstrip("/")) or huella
    if " #" in huella:
        base += "-" + huella.split(" #")[1][:4]
    base = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")[:40] or "pieza"
    ident, n = base, 2
    while ident in usados:
        ident, n = f"{base}-{n}", n + 1
    usados.add(ident)
    return ident


def apuntar(ruta, censo, maquina, sin_censo):
    real = Path(os.path.realpath(ruta))
    copia = real.with_name(real.name + ".antes_" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    while copia.exists():   # nunca encima de una copia anterior
        copia = copia.with_name(copia.name + "_")
    shutil.copy2(real, copia)
    usados = {str(p.get("id")) for p in censo["piezas"] if isinstance(p, dict)}
    tipo = {"cron": "tarea programada", "launchd": "tarea programada", "systemd": "servicio",
            "contenedor": "contenedor"}
    hoy = datetime.now().strftime("%Y-%m-%d")
    for v in sin_censo:
        if v.get("estado_censo"):
            continue   # ya tiene pieza (APAGADO, EN OBRAS...): eso lo decide una persona, no se duplica
        pieza = {
            "id": nuevo_id(v["huella"], usados),
            "nombre": "PENDIENTE: qué hace, en una línea que entienda cualquiera",
            "tipo": tipo.get(v["fuente"], v["fuente"]),
            "fuente": v["fuente"],
            "huella": v["huella"],
            "estado": "SIN VIGILAR",
            "comprobar": [],
            "depende_de": [],
            "alta": hoy,
        }
        if maquina:   # solo si se dijo con --maquina: el nombre del equipo puede cambiar solo
            pieza["maquina"] = maquina
        censo["piezas"].append(pieza)
    C.escribir(ruta, censo)
    return copia


def main():
    try:
        sys.stdout.reconfigure(errors="replace")
    except AttributeError:
        pass
    ap = argparse.ArgumentParser(description="¿Qué está programado aquí sin estar en el censo, y al revés?")
    ap.add_argument("--censo", help="ruta del censo (por defecto, la variable CENSO o ./censo.json)")
    ap.add_argument("--maquina", help="nombre de esta máquina en el censo (por defecto, el del equipo)")
    ap.add_argument("--sistema", action="store_true", help="macOS: mirar también los LaunchAgents/Daemons del sistema")
    ap.add_argument("--apuntar", action="store_true", help="añadir al censo lo que falta, como SIN VIGILAR")
    ap.add_argument("--json", action="store_true", help="salida en JSON")
    a = ap.parse_args()

    ruta = C.ruta_censo(a.censo)
    try:
        censo = C.leer(ruta)
    except (OSError, ValueError) as e:
        print(f"No puedo leer el censo: {e}", file=sys.stderr)
        return 2
    maquina = C.maquina_actual(a.maquina)
    vistos, fallos = mirar_todo(censo, a.sistema)
    sin_censo, sin_correr, varias, ignorados = comparar(censo, maquina, vistos)

    if a.json:
        print(json.dumps({"maquina": maquina, "sin_censo": sin_censo, "no_puedo_mirar": fallos,
                          "sin_correr": [{"id": p.get("id"), "huella": C.huellas(p)} for p in sin_correr],
                          "varias": [{"id": p.get("id"), "programado": [v["huella"] for v in vs]} for p, vs in varias],
                          "ignorados": [v["huella"] for v in ignorados]},
                         ensure_ascii=False, indent=2))
    else:
        print(f"Máquina «{maquina}» · censo {ruta} · {len(vistos)} cosas programadas vistas"
              + (f" · {len(ignorados)} tapadas por «ignorar»" if ignorados else ""))
        if sin_censo:
            print(f"\n🔴 Programado aquí y sin pieza viva en el censo ({len(sin_censo)}). Si se cae, no se entera nadie:")
            for v in sin_censo:
                print(f"   · {texto_de(v)}")
        if varias:
            print(f"\n🔴 Una pieza del censo casa con varias cosas programadas ({len(varias)}). "
                  "¿Una línea duplicada? ¿Otro robot con el mismo script?")
            for p, vs in varias:
                print(f"   · {p.get('id')}: " + " | ".join(f"[{v['fuente']}] {v['huella']} ({v['detalle']})" for v in vs))
        if sin_correr:
            print(f"\n🔴 Está en el censo y NO lo veo programado aquí ({len(sin_correr)}):")
            for p in sin_correr:
                print(f"   · {p.get('id')} (huella: {', '.join(C.huellas(p))})")
        if fallos:
            print("\n🔴 No he podido mirar: " + "; ".join(fallos) + ". Lo que haya ahí no lo he visto.")
        if not (sin_censo or sin_correr or varias or fallos):
            print("\n🟢 Todo lo programado aquí está en el censo, y todo lo del censo está programado.")

    if a.apuntar and any(not v.get("estado_censo") for v in sin_censo):
        nuevas = [v for v in sin_censo if not v.get("estado_censo")]
        copia = apuntar(ruta, censo, C.corto(a.maquina) if a.maquina else None, nuevas)
        print(f"\nApuntadas {len(nuevas)} piezas como SIN VIGILAR. Copia del censo anterior: {copia}")
        print("Siguiente paso: escribe qué hace cada una («nombre») y dales una comprobación («comprobar»).")
        sin_censo = [v for v in sin_censo if v.get("estado_censo")]
    return 1 if (sin_censo or sin_correr or varias or fallos) else 0


if __name__ == "__main__":
    sys.exit(main())

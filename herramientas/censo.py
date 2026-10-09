"""Lo que comparten las herramientas: leer y escribir el censo, saber en qué máquina estamos y sacar
de una línea de cron una huella que no lleve nada de la orden (ni contraseñas, ni direcciones).

El censo es un JSON con la lista de todo lo que corre y debería seguir corriendo. Ver
censo.ejemplo.json y el README. Licencia: MIT.
"""
import hashlib
import json
import os
import re
import socket
from pathlib import Path

ESTADOS = {
    "VIGILADO": "corre y una guardia lo comprueba",
    "SIN VIGILAR": "corre, pero si se cae no se entera nadie (es deuda, no un estado normal)",
    "EN OBRAS": "se está construyendo",
    "PREVISTO": "decidido, aún no empezado",
    "APAGADO": "estuvo vivo y ya no; se deja anotado para que nadie lo reviva sin saberlo",
}
VIVOS = ("VIGILADO", "SIN VIGILAR")   # los que deberían estar programados ahora mismo
INTERPRETES = {"python", "python2", "python3", "bash", "sh", "zsh", "node", "php", "ruby", "perl", "env",
               "uv", "npx", "deno", "bun", "timeout", "nice", "flock", "cd", "sudo", "exec"}
_SCRIPT = re.compile(r"\.(py|sh|js|mjs|ts|rb|php|pl)$")


def ruta_censo(cruda=None):
    """--censo, o la variable CENSO, o ./censo.json."""
    return Path(os.path.expanduser(cruda or os.environ.get("CENSO") or "censo.json"))


def leer(ruta):
    """Lee el censo y se niega a seguir si la forma está mal: una lista escrita como texto
    («"ignorar": "com.google.*"») se recorrería letra a letra, y un «*» suelto lo taparía todo."""
    with open(ruta, encoding="utf-8") as f:
        censo = json.load(f)
    if not isinstance(censo, dict) or not isinstance(censo.get("piezas"), list):
        raise ValueError(f"{ruta}: el censo tiene que ser un objeto con una lista «piezas»")
    for clave in ("ignorar", "guardias"):
        if clave in censo and not (isinstance(censo[clave], list) and all(isinstance(x, str) for x in censo[clave])):
            raise ValueError(f"{ruta}: «{clave}» tiene que ser una lista de textos, por ejemplo [\"com.google.*\"]")
    if "buzon" in censo and not isinstance(censo["buzon"], str):
        raise ValueError(f"{ruta}: «buzon» tiene que ser la ruta de una carpeta")
    for clave in ("contenedores", "launchd_sistema"):
        if clave in censo and not isinstance(censo[clave], bool):
            raise ValueError(f"{ruta}: «{clave}» tiene que ser true o false")
    return censo


def escribir(ruta, datos):
    """Escribe el censo sin romperlo: si es un enlace, escribe en el fichero de verdad; conserva los
    permisos; y no deja nunca un fichero a medias (escribe al lado y sustituye)."""
    real = Path(os.path.realpath(ruta))
    modo = real.stat().st_mode & 0o7777 if real.exists() else None
    tmp = real.with_name(real.name + ".tmp")
    tmp.write_text(json.dumps(datos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if modo is not None:
        os.chmod(tmp, modo)
    os.replace(tmp, real)
    return real


def corto(nombre):
    return str(nombre or "").strip().split(".")[0].lower()


def maquina_actual(cruda=None):
    """El nombre corto de esta máquina (o --maquina, o la variable VIGIA_MAQUINA). Mejor pasarlo
    siempre: en un portátil el nombre del equipo puede cambiar al cambiar de red."""
    return corto(cruda or os.environ.get("VIGIA_MAQUINA") or socket.gethostname())


def de_esta_maquina(pieza, maquina):
    """Una pieza sin «maquina» vale para cualquiera (lo normal si solo tienes una)."""
    suya = corto(pieza.get("maquina"))
    return not suya or suya == maquina


def la_vigila(pieza, maquina):
    """¿Le toca a la guardia de esta máquina comprobarla? «vigila_desde» manda sobre «maquina»:
    así una guardia puede vigilar el parte de otra sin ser la máquina donde corre."""
    quien = corto(pieza.get("vigila_desde") or pieza.get("maquina"))
    return not quien or quien == maquina


def huellas(pieza):
    h = pieza.get("huella")
    if h is None:
        return []
    return [str(x) for x in (h if isinstance(h, list) else [h]) if str(x).strip()]


def huella_cron(orden):
    """La huella de una orden de cron: la ruta del script que ejecuta (/srv/copia.sh), nunca la
    orden entera, que puede llevar contraseñas, cabeceras o direcciones con la clave dentro. Si no
    hay script (un curl, un mysqldump), el nombre del programa y un resumen numérico de la orden:
    distingue dos órdenes distintas sin enseñar nada de ellas."""
    trozos = [t.strip("'\"();&|") for t in orden.split()]
    rutas = [t for t in trozos if "/" in t and "://" not in t and "=" not in t and "@" not in t
             and not t.startswith(("-", "<", ">", "/dev/", "$")) and os.path.basename(t.rstrip("/")) not in INTERPRETES]
    scripts = [t for t in rutas if _SCRIPT.search(t)]
    if scripts or rutas:
        return (scripts or rutas)[0]
    programa = next((t for t in trozos if t and "=" not in t and t not in INTERPRETES
                     and re.fullmatch(r"[\w.+-]+", t)), "orden")
    return f"{programa} #{hashlib.sha256(orden.encode('utf-8')).hexdigest()[:8]}"

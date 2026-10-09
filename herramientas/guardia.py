#!/usr/bin/env python3
"""Guardia: comprueba cada pieza VIGILADA del censo y avisa con el tono que toca.

Cuatro tipos de comprobación (campo «comprobar» de cada pieza, una lista):
  {"fichero": "/ruta/salida.csv", "sla_horas": 26, "min_bytes": 500}
      que exista, que se haya actualizado en las últimas «sla_horas» y que no esté vacío
      (min_bytes vale 1 si no se pone: un fichero de 0 bytes nunca es un «OK»). Fecha y tamaño
      no ven un robot que vuelve a publicar el fichero viejo; para eso, además:
        "contiene": "{hoy}"         un texto que tiene que estar dentro ({hoy} = AAAA-MM-DD de hoy)
        "no_contiene": "ERROR"      un texto que no puede estar dentro
        "debe_cambiar": true        el contenido tiene que cambiar al menos una vez cada sla_horas
        "edad_por_contenido": true  la edad sale de la primera fecha AAAA-MM-DD HH:MM escrita dentro,
                                    no de la del fichero (que una copia sin -p pone a «ahora»)
  {"url": "https://mi-web/salud", "espera": 200, "contiene": "ok"}
      que responda con ese código y, si se pide, con ese texto dentro.
  {"contenedor": "nombre"}          que el contenedor de Docker esté en marcha, sin reiniciarse en
                                    bucle y, si tiene chequeo de salud, sano.
  {"orden": "mi_prueba.sh", "timeout": 60}
      que la orden salga con 0. Para todo lo demás.

Los avisos suben de tono en vez de repetirse iguales (un aviso que llega igual catorce veces se
deja de leer): 🟡 recién caída, 🟠 a partir de 6 horas, 🔴 a partir de 24. Se avisa cuando algo
cae, cuando sube de tono, una vez al día mientras siga caído y cuando vuelve.

Uso:
  python3 guardia.py --censo censo.json --maquina servidor
  python3 guardia.py --censo censo.json --maquina servidor --descubrir \\
      --avisar 'curl -fsS -d @- ntfy.sh/mi-tema' --parte /ruta/parte.txt --latido https://...
  python3 guardia.py --censo censo.json --revisar        # revisa el censo sin comprobar nada
  python3 guardia.py --censo censo.json --probar-aviso --avisar '...'   # prueba el canal

  --maquina    el nombre de esta máquina en el censo. Pásalo siempre: el del equipo puede cambiar.
  --descubrir  además, compara lo programado en esta máquina con el censo (como descubrir.py) y
               avisa de lo que no cuadra, con el mismo tono creciente.
  --avisar     orden que recibe el aviso por la entrada estándar (ntfy, Telegram, correo...). Tiene
               que salir con error si el aviso no llega (con curl, -f).
  --parte      fichero donde deja el resumen de cada pasada, con la fecha escrita dentro. Otra
               guardia (la «torre») lo vigila con edad_por_contenido y "no_contiene": "A CIEGAS".
  --latido     URL a la que llama al terminar cada pasada (un servicio externo de «hombre muerto»
               avisa si deja de llamar). Si la guardia está a ciegas, NO llama: que salte.
  --estado     dónde guarda la memoria de los avisos (por defecto, junto al censo, una por máquina).

Sale con 0 si todo está en verde, 1 si algo está caído, 2 si no puede leer el censo y 3 si está
a ciegas (no ha podido avisar, guardar su memoria o escribir el parte): eso es lo más grave, y por
eso lo dice en el parte y deja de dar el latido.
Licencia: MIT.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import censo as C  # noqa: E402

TIPOS = ("fichero", "url", "contenedor", "orden")
NUMEROS = ("sla_horas", "min_bytes", "timeout", "espera")
TONOS = [(24, "🔴"), (6, "🟠"), (0, "🟡")]   # horas caída → tono
UN_DIA = 24 * 3600
FECHA_DENTRO = re.compile(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})(?::\d{2})?(Z|[+-]\d{2}:?\d{2})?")
CLAVES = {"fichero": {"fichero", "sla_horas", "min_bytes", "contiene", "no_contiene", "debe_cambiar", "edad_por_contenido"},
          "url": {"url", "espera", "contiene", "timeout"}, "contenedor": {"contenedor"}, "orden": {"orden", "timeout"}}
LEER_ENTERO = 50_000_000   # por encima, «contiene» mira los primeros y los últimos 5 MB
GENERICAS = {"python", "python3", "bash", "sh", "node", "php", "ruby", "perl", "cron", "run", "main", ".py", ".sh"}


def tipo_de(c):
    encontrados = [t for t in TIPOS if t in c]
    return encontrados[0] if len(encontrados) == 1 else None


def num(c, clave, defecto=None):
    v = c.get(clave, defecto)
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError(f"«{clave}» tiene que ser un número y es {json.dumps(v, ensure_ascii=False)}")
    return v


def texto_esperado(c, clave, ahora):
    v = c.get(clave)
    return None if v is None else str(v).replace("{hoy}", datetime.fromtimestamp(ahora).strftime("%Y-%m-%d"))


def mirar_fichero(c, ahora, memoria, clave):
    p = Path(os.path.expanduser(str(c["fichero"])))
    if not p.exists():
        return False, f"no existe {p}"
    if not p.is_file():
        return False, f"{p} no es un fichero"
    st = p.stat()
    minimo = num(c, "min_bytes", 1)
    sla = num(c, "sla_horas")
    if sla is None:
        return False, f"{p}: comprobación sin «sla_horas»; así no podría fallar nunca"
    if st.st_size < minimo:
        return False, f"{p} existe pero pesa {st.st_size} bytes (mínimo {minimo}): un OK vacío"
    texto = ""
    if c.get("contiene") or c.get("no_contiene") or c.get("edad_por_contenido"):
        with open(p, "rb") as f:
            if st.st_size <= LEER_ENTERO:
                crudo = f.read()
            else:
                crudo = f.read(5_000_000)
                f.seek(-5_000_000, os.SEEK_END)
                crudo += b"\n" + f.read()
        texto = crudo.decode("utf-8", "replace")
    marca = st.st_mtime
    if c.get("edad_por_contenido"):
        m = FECHA_DENTRO.search(texto)
        if not m:
            return False, f"{p} no lleva dentro ninguna fecha AAAA-MM-DD HH:MM"
        dia, hora, zona = m.groups()
        if zona:   # con zona horaria (lo que escribe la guardia): vale entre máquinas en zonas distintas
            marca = datetime.strptime(f"{dia} {hora}{zona.replace('Z', '+00:00')}", "%Y-%m-%d %H:%M%z").timestamp()
        else:
            marca = datetime.strptime(f"{dia} {hora}", "%Y-%m-%d %H:%M").timestamp()
    edad_h = (ahora - marca) / 3600
    if edad_h < -1:
        return False, f"{p} tiene fecha en el futuro ({-edad_h:.1f} h): así no caduca nunca"
    if sla is not None and edad_h > sla:
        return False, f"{p} no se actualiza desde hace {edad_h:.1f} h (máximo {sla} h)"
    esperado = texto_esperado(c, "contiene", ahora)
    if esperado and esperado not in texto:
        return False, f"{p} no contiene «{esperado}»: ¿datos viejos?"
    vetado = texto_esperado(c, "no_contiene", ahora)
    if vetado and vetado in texto:
        return False, f"{p} contiene «{vetado}»"
    if c.get("debe_cambiar"):
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for trozo in iter(lambda: f.read(1 << 20), b""):
                h.update(trozo)
        resumen = h.hexdigest()
        previo = memoria.get(clave)
        if not previo or previo["resumen"] != resumen:
            memoria[clave] = {"resumen": resumen, "desde": ahora}
        elif (ahora - previo["desde"]) / 3600 > sla:
            return False, (f"{p} tiene exactamente el mismo contenido desde hace "
                           f"{(ahora - previo['desde']) / 3600:.1f} h (máximo {sla} h): ¿republica datos viejos?")
    return True, f"{p}: hace {max(edad_h, 0):.1f} h, {st.st_size} bytes"


def mirar_url(c, ahora):
    url, espera = str(c["url"]), int(num(c, "espera", 200))
    try:
        with urllib.request.urlopen(url, timeout=float(num(c, "timeout", 10))) as r:
            codigo, cuerpo = r.status, r.read(200_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        codigo, cuerpo = e.code, ""
    except Exception as e:  # sin red, DNS, certificado, tiempo agotado...
        return False, f"{url} no responde ({type(e).__name__}: {e})"
    if codigo != espera:
        return False, f"{url} responde {codigo} (se esperaba {espera})"
    esperado = texto_esperado(c, "contiene", ahora)
    if esperado and esperado not in cuerpo:
        return False, f"{url} responde {codigo}, pero sin «{esperado}» dentro"
    return True, f"{url}: {codigo}"


def mirar_contenedor(c):
    nombre = str(c["contenedor"])
    if not shutil.which("docker"):
        return False, f"no hay docker en esta máquina para mirar «{nombre}»"
    formato = "{{.State.Running}}|{{.State.Restarting}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}"
    try:
        r = subprocess.run(["docker", "inspect", "-f", formato, nombre], capture_output=True, text=True,
                           errors="replace", timeout=15)
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"docker no contesta ({type(e).__name__})"
    if r.returncode != 0:
        return False, f"el contenedor «{nombre}» no existe"
    corriendo, reiniciando, salud = (r.stdout.strip().split("|") + ["", ""])[:3]
    if corriendo != "true":
        return False, f"el contenedor «{nombre}» está parado"
    if reiniciando == "true":
        return False, f"el contenedor «{nombre}» se está reiniciando (¿en bucle?)"
    if salud in ("unhealthy", "starting"):
        estado = "no está sano" if salud == "unhealthy" else "todavía no está sano (arrancando)"
        return False, f"el contenedor «{nombre}» está en marcha pero su chequeo de salud dice que {estado}"
    return True, f"contenedor «{nombre}» en marcha"


def mirar_orden(c):
    orden = str(c["orden"])
    limite = float(num(c, "timeout", 60))
    try:
        r = subprocess.run(orden, shell=True, capture_output=True, text=True, errors="replace", timeout=limite)
    except subprocess.TimeoutExpired:
        return False, f"«{orden}» no termina (más de {limite:g} s)"
    if r.returncode != 0:
        cola = (r.stderr or r.stdout).strip().splitlines()[-1:] or [""]
        return False, f"«{orden}» sale con {r.returncode}: {cola[0][:200]}"
    return True, f"«{orden}»: 0"


def mirar_pieza(pieza, ahora, memoria):
    """(bien, motivo). Una pieza VIGILADA sin comprobaciones cuenta como caída: no la mira nadie.
    Si una comprobación revienta por dentro, la pieza cuenta como caída y las demás se siguen mirando."""
    comprobaciones = pieza.get("comprobar") or []
    if not isinstance(comprobaciones, list):
        return False, f"«comprobar» tiene que ser una lista y es {json.dumps(comprobaciones, ensure_ascii=False)[:80]}"
    if not comprobaciones:
        return False, "está VIGILADA en el censo, pero no tiene ninguna comprobación: no la mira nadie"
    vistos = []
    for i, c in enumerate(comprobaciones):
        t = tipo_de(c) if isinstance(c, dict) else None
        if t is None:
            return False, f"comprobación mal escrita: {json.dumps(c, ensure_ascii=False)}"
        try:
            if t == "fichero":
                bien, motivo = mirar_fichero(c, ahora, memoria, f"{pieza.get('id')}#{i}")
            elif t == "url":
                bien, motivo = mirar_url(c, ahora)
            elif t == "contenedor":
                bien, motivo = mirar_contenedor(c)
            else:
                bien, motivo = mirar_orden(c)
        except Exception as e:  # permisos, un número escrito como texto, lo que sea: caída, no silencio
            return False, f"la comprobación «{t}» no se ha podido hacer ({type(e).__name__}: {e})"
        if not bien:
            return False, motivo
        vistos.append(motivo)
    return True, " · ".join(vistos)


def piezas(n):
    return "1 pieza corre" if n == 1 else f"{n} piezas corren"


def sin_que_nadie(n):
    return f"{piezas(n)} sin que nadie la vigile" if n == 1 else f"{piezas(n)} sin que nadie las vigile"


def tono(horas):
    return next(simbolo for umbral, simbolo in TONOS if horas >= umbral)


def horas_texto(segundos):
    h = segundos / 3600
    return f"{h:.0f} h" if h < 48 else f"{h / 24:.1f} días"


def decidir_avisos(resultados, estado, ahora):
    """Actualiza la memoria y devuelve las líneas que hay que avisar en esta pasada. Nada se da por
    avisado hasta confirmar_avisos (si el aviso falla, se repite en la pasada siguiente)."""
    lineas = []
    memoria = estado.setdefault("piezas", {})
    orden_tonos = [s for _, s in TONOS]
    for ident, (bien, motivo) in resultados.items():
        m = memoria.get(ident)
        if bien:
            if m:
                lineas.append(f"🟢 {ident} vuelve a funcionar (estuvo caída {horas_texto(ahora - m['caida_desde'])})")
                m["vuelta_pendiente"] = True
            continue
        if not m:
            m = memoria[ident] = {"caida_desde": ahora, "tono_avisado": None, "ultimo_aviso": 0}
        m.pop("vuelta_pendiente", None)
        t = tono((ahora - m["caida_desde"]) / 3600)
        sube = m["tono_avisado"] is None or orden_tonos.index(t) < orden_tonos.index(m["tono_avisado"])
        if sube or ahora - m["ultimo_aviso"] >= UN_DIA:
            lleva = "" if m["tono_avisado"] is None else f" — lleva {horas_texto(ahora - m['caida_desde'])} caída"
            lineas.append(f"{t} {ident}{lleva}: {motivo}")
            m["tono_pendiente"] = t
    # las piezas que ya no están en el censo (o ya no están vigiladas) se olvidan
    for ident in [i for i in memoria if i not in resultados]:
        del memoria[ident]
    return lineas


def confirmar_avisos(estado, ahora):
    if estado.pop("deuda_pendiente", None):
        estado["deuda_avisada"] = ahora
    memoria = estado.get("piezas", {})
    for ident in [i for i, m in memoria.items() if m.get("vuelta_pendiente")]:
        del memoria[ident]
    for m in memoria.values():
        if "tono_pendiente" in m:
            m["tono_avisado"] = m.pop("tono_pendiente")
            m["ultimo_aviso"] = ahora


def escribir(ruta, texto):
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tmp = ruta.with_name(ruta.name + ".tmp")
    tmp.write_text(texto, encoding="utf-8")
    os.replace(tmp, ruta)


def avisar(orden, mensaje):
    try:
        r = subprocess.run(orden, shell=True, input=mensaje, text=True, capture_output=True, timeout=60)
        return r.returncode == 0
    except subprocess.SubprocessError:
        return False


def revisar(censo):
    """Lo que hace que una comprobación no pueda fallar nunca, o que el censo mienta."""
    errores, avisos = [], []
    lista = [p for p in censo["piezas"] if isinstance(p, dict)]
    if len(lista) != len(censo["piezas"]):
        errores.append("hay entradas en «piezas» que no son objetos")
    ids = [p.get("id") for p in lista]
    for ident in sorted({i for i in ids if ids.count(i) > 1}, key=str):
        errores.append(f"id repetido: {ident}")
    conocidos = set(ids)
    guardias = {C.corto(g) for g in censo.get("guardias", [])} if "guardias" in censo else None
    if guardias is None:
        avisos.append("el censo no tiene «guardias» (las máquinas donde corre una guardia): no puedo "
                      "comprobar que alguien mira cada pieza VIGILADA")
    for pat in censo.get("ignorar", []):
        if len(pat.replace("*", "").replace("?", "").strip(".-_ ")) < 4:
            errores.append(f"«ignorar» tiene «{pat}», que tapa casi cualquier cosa: lo que case no avisará nunca")
    if censo.get("ignorar"):
        avisos.append("«ignorar» tapa: " + ", ".join(censo["ignorar"]) + " (lo que case con eso no avisa nunca: revísalo)")
    for p in lista:
        ident = p.get("id") or "(sin id)"
        if not p.get("id"):
            errores.append(f"una pieza no tiene id: {json.dumps(p, ensure_ascii=False)[:120]}")
        if p.get("estado") not in C.ESTADOS:
            errores.append(f"{ident}: estado «{p.get('estado')}» desconocido (valen: {', '.join(C.ESTADOS)})")
        if str(p.get("nombre", "")).startswith("PENDIENTE") or not p.get("nombre"):
            avisos.append(f"{ident}: falta decir qué hace («nombre»)")
        for d in p.get("depende_de") or []:
            if d not in conocidos:
                errores.append(f"{ident}: depende de «{d}», que no está en el censo")
        for h in C.huellas(p):
            if len(h.strip()) < 6 or h.strip().lower() in GENERICAS:
                avisos.append(f"{ident}: huella «{h}» demasiado genérica: puede tapar otros robots")
        comprobaciones = p.get("comprobar") or []
        if not isinstance(comprobaciones, list):
            errores.append(f"{ident}: «comprobar» tiene que ser una lista")
            comprobaciones = []
        if p.get("estado") == "VIGILADO":
            if not comprobaciones:
                errores.append(f"{ident}: VIGILADO sin ninguna comprobación (el censo miente)")
            quien = C.corto(p.get("vigila_desde") or p.get("maquina"))
            if guardias is not None and quien and quien not in guardias:
                errores.append(f"{ident}: la vigila «{quien}», y ahí no hay ninguna guardia («guardias»)")
            if comprobaciones and all(isinstance(c, dict) and tipo_de(c) == "contenedor" for c in comprobaciones):
                avisos.append(f"{ident}: solo mira que el contenedor esté en marcha, no que haga su trabajo")
        for c in comprobaciones:
            t = tipo_de(c) if isinstance(c, dict) else None
            if t is None:
                errores.append(f"{ident}: comprobación mal escrita (una sola de {', '.join(TIPOS)}): {c}")
                continue
            for clave in NUMEROS:
                try:
                    num(c, clave)
                except ValueError as e:
                    errores.append(f"{ident}: {e}")
            raras = sorted(set(c) - CLAVES[t])
            if raras:
                errores.append(f"{ident}: «{t}» no entiende {', '.join(raras)} (¿mal escrito? se ignoraría sin avisar)")
            esperado = c.get("contiene")
            if esperado is not None and len(str(esperado).replace("{hoy}", "AAAA-MM-DD")) < 4:
                avisos.append(f"{ident}: «contiene»: «{esperado}» es tan corto que puede estar dentro de un error (\"not ok\")")
            if t == "fichero":
                if c.get("sla_horas") is None:
                    errores.append(f"{ident}: {c['fichero']} sin «sla_horas»: si el robot muere, el fichero viejo sigue ahí y da OK")
                if isinstance(c.get("min_bytes"), (int, float)) and c["min_bytes"] < 1:
                    errores.append(f"{ident}: {c['fichero']} con min_bytes por debajo de 1: no ve el fichero vacío")
                elif "min_bytes" not in c:
                    avisos.append(f"{ident}: {c['fichero']} sin «min_bytes»: vale 1 byte, y un CSV con solo la cabecera pasa")
                fresco = c.get("debe_cambiar") or c.get("edad_por_contenido") or "{hoy}" in str(c.get("contiene", ""))
                if not fresco:
                    avisos.append(f"{ident}: {c['fichero']} no mira si el contenido es de hoy: si el robot vuelve a "
                                  "publicar el fichero viejo, da verde (añade «debe_cambiar» o un «contiene» con {hoy})")
            elif t == "url" and not c.get("contiene"):
                avisos.append(f"{ident}: {c['url']} sin «contiene»: comprueba que responde, no que responde bien")
            elif t == "orden":
                avisos.append(f"{ident}: «{c['orden']}»: asegúrate de que sale con error cuando falla (pruébalo rompiéndolo)")
    deuda = [p.get("id") for p in lista if p.get("estado") == "SIN VIGILAR"]
    return errores, avisos, deuda


def anomalias(censo, maquina, vigiladas):
    """Lo que en una pasada normal tiene que salir como caído aunque no sea ninguna pieza concreta."""
    salida = {}
    for p in censo["piezas"]:
        if isinstance(p, dict) and C.la_vigila(p, maquina) and p.get("estado") not in C.ESTADOS:
            salida[f"{p.get('id')} (estado)"] = (False, f"estado «{p.get('estado')}» desconocido: esta pieza no la mira nadie")
    ids = [p.get("id") for p in vigiladas]
    for ident in sorted({i for i in ids if ids.count(i) > 1}, key=str):
        salida[f"{ident} (repetido)"] = (False, f"hay {ids.count(ident)} piezas vigiladas con el id «{ident}»: una tapa a la otra")
    if "guardias" in censo and maquina not in {C.corto(g) for g in censo["guardias"]}:
        salida["guardia"] = (False, f"la máquina «{maquina}» no está en «guardias» del censo: ¿--maquina mal puesto? "
                                    "Lo suyo puede estar quedándose sin mirar")
        return salida
    total = sum(1 for p in censo["piezas"] if isinstance(p, dict) and p.get("estado") == "VIGILADO")
    if not vigiladas:
        salida["guardia"] = (False, f"esta guardia no vigila nada: a la máquina «{maquina}» no le toca ninguna pieza "
                                    f"VIGILADO (hay {total} en el censo). ¿Falta --maquina, o está todo SIN VIGILAR?")
    if censo.get("buzon"):   # la torre: un parte que llega a un buzón sin pieza es una guardia que nadie mira
        buzon = Path(os.path.expanduser(str(censo["buzon"])))
        rutas = [str(Path(os.path.expanduser(str(c.get("fichero"))))) for p in censo["piezas"] if isinstance(p, dict)
                 for c in (p.get("comprobar") if isinstance(p.get("comprobar"), list) else []) if isinstance(c, dict) and c.get("fichero")]
        try:
            carpetas = sorted(x for x in buzon.iterdir() if x.is_dir() and not x.name.startswith(".")
                              and any(not f.name.startswith(".") for f in x.iterdir()))   # solo si ha llegado algo
        except OSError as e:
            salida["buzon"] = (False, f"no puedo mirar el buzón {buzon} ({e.strerror})")
            carpetas = []
        for x in carpetas:
            if not any(r.startswith(str(x) + os.sep) for r in rutas):
                salida[f"buzon/{x.name}"] = (False, "llegan partes a este buzón y ninguna pieza de la torre los mira")
    return salida


def descubrir_como_piezas(censo, maquina):
    import descubrir as D
    vistos, fallos = D.mirar_todo(censo)
    sin_censo, sin_correr, varias, _ = D.comparar(censo, maquina, vistos)
    salida = {}
    for v in sin_censo:
        salida[f"sin censo: {D.texto_de(v)[:120]}"] = (False, "está programado aquí y no tiene pieza viva en el censo")
    for p in sin_correr:
        salida[f"no programado: {p.get('id')}"] = (False, f"está en el censo ({p.get('estado')}) y ya no está programado aquí")
    for p, vs in varias:
        salida[f"varias: {p.get('id')}"] = (False, f"casa con {len(vs)} cosas programadas: ¿una duplicada?")
    if fallos:
        salida["descubrir"] = (False, "no he podido mirar: " + "; ".join(fallos))
    return salida


def main():
    try:
        sys.stdout.reconfigure(errors="replace")
        sys.stderr.reconfigure(errors="replace")
    except AttributeError:
        pass
    ap = argparse.ArgumentParser(description="Comprueba las piezas vigiladas del censo y avisa.")
    ap.add_argument("--censo")
    ap.add_argument("--maquina")
    ap.add_argument("--descubrir", action="store_true", help="comparar también lo programado con el censo")
    ap.add_argument("--avisar", help="orden que recibe el aviso por la entrada estándar")
    ap.add_argument("--parte", help="fichero donde dejar el parte de cada pasada")
    ap.add_argument("--latido", help="URL a la que llamar al terminar (hombre muerto externo)")
    ap.add_argument("--estado", help="memoria de avisos (por defecto, .guardia_estado_<máquina>.json junto al censo)")
    ap.add_argument("--revisar", action="store_true", help="revisar el censo sin comprobar nada")
    ap.add_argument("--probar-aviso", action="store_true", help="mandar un aviso de prueba por --avisar y salir")
    ap.add_argument("--ahora", type=float, help=argparse.SUPPRESS)   # para la prueba
    a = ap.parse_args()

    ruta = C.ruta_censo(a.censo)
    try:
        censo = C.leer(ruta)
    except (OSError, ValueError) as e:
        print(f"No puedo leer el censo: {e}", file=sys.stderr)
        return 2
    maquina = C.maquina_actual(a.maquina)

    if a.probar_aviso:
        if not a.avisar:
            print("--probar-aviso necesita --avisar", file=sys.stderr)
            return 2
        bien = avisar(a.avisar, f"Prueba del canal de avisos de la guardia de «{maquina}». Si lees esto, llega.")
        print("✓ La orden de aviso ha salido con 0. Mira que te ha llegado." if bien else
              "⚠️  La orden de aviso ha fallado: con este canal, la guardia estaría a ciegas.")
        return 0 if bien else 3

    if a.revisar:
        errores, avisos, deuda = revisar(censo)
        for e in errores:
            print(f"🔴 {e}")
        for e in avisos:
            print(f"🟡 {e}")
        if deuda:
            print(f"🟠 Deuda: {piezas(len(deuda))} SIN VIGILAR: {', '.join(map(str, deuda))}")
        if not errores and not avisos and not deuda:
            print("🟢 No encuentro ninguna de las trampas que sé buscar. Eso no prueba que las comprobaciones "
                  "funcionen: rómpelas a propósito una vez y mira que saltan.")
        return 1 if errores else 0

    ahora = a.ahora or time.time()
    ruta_estado = Path(os.path.expanduser(a.estado)) if a.estado else ruta.with_name(f".guardia_estado_{maquina}.json")
    try:
        estado = json.loads(ruta_estado.read_text(encoding="utf-8"))
        if not isinstance(estado, dict):
            estado = {}
    except (OSError, ValueError):
        estado = {}
    memoria_contenido = estado.setdefault("contenido", {})

    vigiladas = [p for p in censo["piezas"] if isinstance(p, dict) and p.get("estado") == "VIGILADO"
                 and C.la_vigila(p, maquina)]
    deuda = [p for p in censo["piezas"] if isinstance(p, dict) and p.get("estado") == "SIN VIGILAR"
             and C.de_esta_maquina(p, maquina)]
    resultados = anomalias(censo, maquina, vigiladas)
    for p in vigiladas:
        resultados.setdefault(str(p.get("id")), mirar_pieza(p, ahora, memoria_contenido))
    if a.descubrir:
        try:
            resultados.update(descubrir_como_piezas(censo, maquina))
        except Exception as e:
            resultados["descubrir"] = (False, f"no he podido comparar lo programado ({type(e).__name__}: {e})")
    caidas = {i: r for i, r in resultados.items() if not r[0]}
    lineas = decidir_avisos(resultados, estado, ahora)
    if deuda and ahora - estado.get("deuda_avisada", 0) >= 7 * UN_DIA:   # la deuda se recuerda aunque todo esté verde
        lineas.append(f"🟠 Recordatorio semanal: {sin_que_nadie(len(deuda))}: " + ", ".join(str(p.get("id")) for p in deuda))
        estado["deuda_pendiente"] = True

    sello = datetime.fromtimestamp(ahora).astimezone().isoformat(timespec="minutes")
    print(f"Guardia de «{maquina}» · {sello} · {len(vigiladas)} vigiladas · {len(caidas)} caídas · {len(deuda)} sin vigilar")
    for ident, (bien, motivo) in resultados.items():
        print(f"  {'🟢' if bien else '🔴'} {ident}: {motivo}")

    codigo = 1 if caidas else 0
    a_ciegas = []
    if lineas:
        mensaje = f"Guardia de «{maquina}»:\n" + "\n".join(lineas)
        if deuda:
            mensaje += f"\n(Deuda: {sin_que_nadie(len(deuda))}.)"
        print("\nAviso:\n" + mensaje)
        if a.avisar and avisar(a.avisar, mensaje):
            confirmar_avisos(estado, ahora)
        elif a.avisar:
            a_ciegas.append("la orden de --avisar ha fallado; el aviso se repetirá en la pasada siguiente")
        elif a.parte or a.latido:   # en marcha de verdad y sin canal: el aviso solo ha salido por pantalla
            a_ciegas.append("no hay --avisar: este aviso solo ha salido por pantalla")
        else:                       # a mano en la terminal: la pantalla es el canal
            confirmar_avisos(estado, ahora)
    try:
        escribir(ruta_estado, json.dumps(estado, ensure_ascii=False, indent=1))
    except OSError as e:
        a_ciegas.append(f"no puedo guardar la memoria de avisos en {ruta_estado} ({e.strerror})")

    if "guardia" in caidas:   # una guardia que no mira nada no puede dar el parte por bueno
        a_ciegas.append("esta guardia no vigila nada")
    if a.parte:
        verdes = sum(1 for p in vigiladas if resultados.get(str(p.get("id")), (False,))[0])
        parte = [f"PARTE · guardia de «{maquina}» · {sello}"]
        parte += [f"⚠️ A CIEGAS: {x}" for x in a_ciegas]
        parte.append(f"vigiladas: {len(vigiladas)} · en verde: {verdes} · avisos abiertos: {len(caidas)} · sin vigilar: {len(deuda)}")
        parte += [f"🔴 {i}" for i in caidas]   # solo el nombre: el motivo (rutas, URL, órdenes) no sale de la máquina
        try:
            escribir(Path(os.path.expanduser(a.parte)), "\n".join(parte) + "\n")
        except OSError as e:
            a_ciegas.append(f"no puedo escribir el parte en {a.parte} ({e.strerror})")
    if a_ciegas:
        codigo = 3
        print("⚠️  A CIEGAS: " + "; ".join(a_ciegas), file=sys.stderr)
    if a.latido and codigo != 3:   # a ciegas no se da el latido: que salte el hombre muerto
        try:
            urllib.request.urlopen(a.latido, timeout=15).read(100)
        except Exception as e:
            print(f"⚠️  El latido a {a.latido} ha fallado ({type(e).__name__}).", file=sys.stderr)
            codigo = 3
    return codigo


if __name__ == "__main__":
    sys.exit(main())

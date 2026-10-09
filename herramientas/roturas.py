#!/usr/bin/env python3
"""¿La prueba caza los fallos de verdad? Rompe el código a propósito, una cosa cada vez, en una
copia temporal, y comprueba que prueba.py deja de decir «TODO BIEN». Si alguna rotura pasa
inadvertida, la prueba tiene un agujero.

Uso: python3 herramientas/roturas.py   (tarda unos minutos; sale con 0 si cazó todas).
Licencia: MIT.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

H = Path(__file__).resolve().parent
ROTURAS = [  # (fichero, qué hay, qué se pone, qué fallo simula)
    ("guardia.py", "if a.latido and codigo != 3:", "if a.latido:", "dar el latido estando a ciegas"),
    ("guardia.py", "        except Exception as e:  # permisos", "        except ZeroDivisionError as e:  # permisos",
     "que una comprobación que revienta tumbe la guardia"),
    ("guardia.py", "    if edad_h < -1:", "    if False:", "no ver las fechas en el futuro"),
    ("guardia.py", "    if not vigiladas:", "    if False:", "dar verde sin vigilar nada"),
    ("guardia.py", '            m["vuelta_pendiente"] = True', "            del memoria[ident]",
     "perder el aviso de «vuelve a funcionar»"),
    ("guardia.py", "    if esperado and esperado not in texto:", "    if False:", "no mirar «contiene» en ficheros"),
    ("guardia.py", "        if zona:", "        if False:", "ignorar la zona horaria del parte"),
    ("guardia.py", "    if sla is None:\n        return False", "    if False:\n        return False",
     "aceptar ficheros sin plazo"),
    ("guardia.py", '    if salud in ("unhealthy", "starting"):', '    if salud == "nunca":', "dar por sano un contenedor enfermo"),
    ("guardia.py", '        elif a.parte or a.latido:', '        elif False:', "callar que no hay canal de avisos"),
    ("guardia.py", '        sys.stdout.reconfigure(errors="replace")', "        pass", "caerse con un idioma que no es UTF-8"),
    ("guardia.py", "            for trozo in iter(lambda: f.read(1 << 20), b\"\"):",
     "            for trozo in [f.read(5_000_000)]:", "mirar solo el principio de un fichero grande"),
    ("guardia.py", '        parte += [f"🔴 {i}" for i in caidas]', '        parte += [f"🔴 {i}: {r[1]}" for i, r in caidas.items()]',
     "sacar en el parte los motivos (rutas, URL, órdenes) de la máquina del cliente"),
    ("guardia.py", "            if not any(r.startswith(str(x) + os.sep) for r in rutas):", "            if False:",
     "no ver un buzón que recibe partes sin pieza en la torre"),
    ("guardia.py", '        a_ciegas.append("esta guardia no vigila nada")', "        pass",
     "dar el parte por bueno en una guardia que no vigila nada"),
    ("descubrir.py", '    varias = [(p, vs) for p, vs in por_pieza.values() if len(vs) > 1 and not p.get("varias")]',
     "    varias = []", "no ver las líneas duplicadas"),
    ("descubrir.py", '    vivas = [p for p in mias if p.get("estado") in C.VIVOS]', "    vivas = mias",
     "dar por buena una pieza APAGADO que alguien ha revivido"),
    ("descubrir.py", "        if maquina:   # solo si", "        if True:   # solo si",
     "grabar el nombre del equipo, que puede cambiar"),
    ("descubrir.py", "    while copia.exists():", "    while False:", "pisar la copia del censo"),
    ("censo.py", "    rutas = [t for t in trozos if", "    rutas = [orden] + [t for t in trozos if",
     "copiar la orden entera (con secretos) a la huella"),
    ("censo.py", "    real = Path(os.path.realpath(ruta))", "    real = Path(ruta)", "romper un censo enlazado"),
    ("censo.py", '    for clave in ("ignorar", "guardias"):', "    for clave in ():", "aceptar «ignorar» escrito como texto"),
    ("candado_alta.py", "    ya = set(marcas.get(sesion, []))", "    ya = set(sum(marcas.values(), []))",
     "no volver a pedirlo en una sesión nueva"),
    ("candado_alta.py", '        salida = {"systemMessage"', '        return 0; salida = {"systemMessage"',
     "callar cuando el candado está roto"),
    ("candado_alta.py", "    if entrada.get(\"stop_hook_active\"):", "    if False:", "frenar en bucle"),
]


def main():
    cazadas = 0
    for fichero, hay, pone, simula in ROTURAS:
        with tempfile.TemporaryDirectory() as t:
            copia = Path(t) / "herramientas"
            shutil.copytree(H, copia, ignore=shutil.ignore_patterns("__pycache__"))
            ruta = copia / fichero
            texto = ruta.read_text(encoding="utf-8")
            if texto.count(hay) != 1:   # la rotura ya no se puede aplicar: cuenta como no cazada
                print(f"  ✗ {simula}: no encuentro el trozo en {fichero} (¿ha cambiado el código? actualiza ROTURAS)")
                continue
            ruta.write_text(texto.replace(hay, pone), encoding="utf-8")
            r = subprocess.run([sys.executable, str(copia / "prueba.py")], capture_output=True, text=True,
                               errors="replace", timeout=900)
            cazada = "TODO BIEN" not in r.stdout
            cazadas += cazada
            print(f"  {'✓' if cazada else '✗'} {simula} ({fichero}){'' if cazada else ': LA PRUEBA NO LO HA VISTO'}")
    print(f"\nCazadas {cazadas} de {len(ROTURAS)} roturas.")
    return 0 if cazadas == len(ROTURAS) else 1


if __name__ == "__main__":
    sys.exit(main())

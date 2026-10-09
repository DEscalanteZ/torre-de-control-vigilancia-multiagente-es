#!/usr/bin/env python3
"""Candado de alta (gancho Stop de Claude Code): un robot no nace vigilado.

Al terminar cada respuesta del agente mira la máquina con descubrir.py. Si aparece algo programado
(una línea de cron, un LaunchAgent, una unidad de systemd y, si el censo lo pide, un contenedor) que
no tiene pieza viva en el censo, lo haya puesto el agente o no, le frena y le explica qué hacer.

Hasta dónde llega (y hasta dónde no):
  - frena UNA vez por sesión y por pieza. Si el agente no la apunta (o el humano le dice que no
    hace falta), no insiste en esa sesión; en la sesión siguiente se lo vuelve a pedir, mientras
    siga fuera del censo. Lo que no quieras censar, a «ignorar»;
  - si Claude Code dice que ya viene de un freno (stop_hook_active), deja pasar siempre;
  - solo ve ESTA máquina: lo que el agente programe por ssh en otra no lo ve. Para eso está
    guardia.py --descubrir en cada máquina;
  - si algo falla por dentro (censo ilegible, una fuente que no ha podido mirar...), no frena al
    agente, pero se lo dice al humano una vez por sesión (systemMessage): un candado roto no puede
    callarse. Si el que falla es el propio arranque (ruta movida, sin python3), lo cubre el «||
    echo» de la instalación: sin él, el error se perdería.

Instalación (en ~/.claude/settings.json, dentro de "hooks"):
  "Stop": [{"hooks": [{"type": "command",
            "command": "python3 /ruta/herramientas/candado_alta.py --censo /ruta/censo.json --maquina mimac || echo '{\"systemMessage\": \"⚠️ El candado de alta no arranca\"}'"}]}]

Salida: si hay que frenar, imprime {"decision": "block", "reason": "..."} y sale con 0. Claude Code
le pasa el motivo al agente, que sigue trabajando en vez de cerrar.
Licencia: MIT.
"""
import argparse
import json
import os
import sys
from pathlib import Path

MAX_SESIONES = 30


def leer_marcas(ruta):
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        sesiones = datos.get("sesiones", {}) if isinstance(datos, dict) else {}
        return sesiones if isinstance(sesiones, dict) else {}
    except (OSError, ValueError):
        return {}


def guardar_marcas(ruta, sesiones):
    if len(sesiones) > MAX_SESIONES:   # se quedan las últimas (los dict guardan el orden de llegada)
        sesiones = dict(list(sesiones.items())[-MAX_SESIONES:])
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tmp = ruta.with_name(ruta.name + ".tmp")
    tmp.write_text(json.dumps({"sesiones": sesiones}, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, ruta)


def comprobar(a, sesion, marcas):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import censo as C
    import descubrir as D

    ruta = C.ruta_censo(a.censo)
    censo = C.leer(ruta)
    vistos, fallos = D.mirar_todo(censo)
    sin_censo, _, varias, _ = D.comparar(censo, C.maquina_actual(a.maquina), vistos)
    pendientes = [(f"{v['fuente']}|{v['huella']}", D.texto_de(v)) for v in sin_censo]
    pendientes += [(f"varias|{p.get('id')}|{len(vs)}",
                    f"la pieza «{p.get('id')}» casa con {len(vs)} cosas programadas: "
                    + " | ".join(v["huella"] for v in vs)) for p, vs in varias]
    ya = set(marcas.get(sesion, []))
    nuevas = [(k, t) for k, t in pendientes if k not in ya]
    clave_fallos = "fallos|" + "|".join(sorted(fallos))
    aviso = None
    if fallos and clave_fallos not in ya:
        aviso = "⚠️ El candado de alta no ha podido mirar: " + "; ".join(fallos) + ". Lo que haya ahí no lo ve."
        ya.add(clave_fallos)
    marcas[sesion] = sorted(ya | {k for k, _ in nuevas})
    if not nuevas:
        return None, aviso
    lista = "\n".join(f"  · {t}" for _, t in nuevas)
    return (
        f"Hay {len(nuevas)} cosa(s) programadas en esta máquina que no cuadran con el censo ({ruta}):\n{lista}\n"
        "Un robot no nace vigilado: si se cae, no se enterará nadie. Antes de terminar:\n"
        "1. Dale de alta en el censo: id, «nombre» (qué hace, en una línea que entienda cualquiera), "
        "«huella» (la de arriba, o la ruta del script), «estado» y «alta» (la fecha de hoy). Si una pieza "
        "casa con varias cosas programadas, mira si una sobra (¿duplicada?) o si son piezas distintas. Si el "
        "censo la da por APAGADO o EN OBRAS, pregúntale al humano si se ha vuelto a encender a propósito.\n"
        "2. Apúntala con estado «SIN VIGILAR» (es deuda, pero deuda a la vista) y PROPÓN al humano una "
        "comprobación que falle cuando se muera o publique datos viejos (un fichero con sla_horas, min_bytes "
        "y «debe_cambiar» o un «contiene» con {hoy}; una url con «contiene»). No la pases a VIGILADO hasta "
        "haberla visto fallar una vez, rompiéndola a propósito.\n"
        "3. Pasa guardia.py --revisar y cuéntale al humano qué has apuntado.\n"
        "Si no es suyo (lo instaló otro programa), añade a «ignorar» su etiqueta exacta, nunca un patrón amplio. "
        "Si el humano dice que no hace falta, no insistas: en esta sesión no se vuelve a pedir."
    ), aviso


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--censo")
    ap.add_argument("--maquina")
    ap.add_argument("--marcas", default="~/.claude/candado_alta_avisadas.json",
                    help="dónde apunta de qué ha avisado ya en cada sesión")
    a = ap.parse_args()
    try:
        entrada = json.load(sys.stdin)
    except ValueError:
        entrada = {}
    if not isinstance(entrada, dict):
        entrada = {}
    if entrada.get("stop_hook_active"):
        return 0
    sesion = str(entrada.get("session_id") or "sin-sesion")
    ruta_marcas = Path(os.path.expanduser(a.marcas))
    marcas = leer_marcas(ruta_marcas)
    try:
        motivo, aviso = comprobar(a, sesion, marcas)
        salida = {"decision": "block", "reason": motivo} if motivo else {}
        if aviso:
            salida["systemMessage"] = aviso
    except Exception as e:   # roto: no frena al agente, pero el humano se entera (una vez por sesión)
        clave = "roto|" + type(e).__name__
        if clave in marcas.get(sesion, []):
            return 0
        marcas.setdefault(sesion, []).append(clave)
        salida = {"systemMessage": f"⚠️ El candado de alta no funciona ({type(e).__name__}: {str(e)[:200]}). "
                                   "Mientras no se arregle, no frena nada: revisa la ruta del censo y que sea JSON válido."}
    if salida:
        try:
            guardar_marcas(ruta_marcas, marcas)
        except OSError:
            pass
        print(json.dumps(salida, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # ni el aviso de roto ha podido salir: deja pasar, nunca atasca al agente
        sys.exit(0)

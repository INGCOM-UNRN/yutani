"""Lectura del SARIF 2.1.0 que emiten compiladores (`gcc -fdiagnostics-format=sarif-stderr`, Clang).

Da línea, columna y flag sin depender de la forma textual de stderr y normaliza los diagnósticos
al sobre común de `yutani.hallazgos`.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any
from urllib.parse import unquote

from yutani.hallazgos import hallazgo


def flag_de_regla(regla: str | None) -> str | None:
    """El flag de un `ruleId`: `-Werror=return-type` y `-Wreturn-type` son el mismo, y `-Wformat=`
    se llama `-Wformat`. Lo que no es un flag (`error`, `note`) da None."""
    if not regla or not regla.startswith("-W"):
        return None
    if regla.startswith("-Werror="):
        regla = "-W" + regla[len("-Werror="):]
    return regla.split("=", 1)[0]


def _documentos(salida: str) -> Iterator[dict[str, Any]]:
    """Los documentos SARIF de `salida`: uno por unidad de traducción, y puede haber texto del driver
    alrededor (`gcc: warning: …`, los mensajes de ld)."""
    decodificador = json.JSONDecoder()
    posicion = salida.find("{")
    while posicion != -1:
        try:
            documento, fin = decodificador.raw_decode(salida, posicion)
        except ValueError:
            posicion = salida.find("{", posicion + 1)
            continue
        if isinstance(documento, dict) and "runs" in documento:
            yield documento
        posicion = salida.find("{", fin)


def _archivo(ubicacion: dict[str, Any]) -> str | None:
    uri = ubicacion.get("artifactLocation", {}).get("uri")
    if not uri:
        return None
    if uri.startswith("file://"):
        ruta = unquote(uri[len("file://"):])
        # file:///C:/Users/… en Windows: la barra inicial no es parte de la ruta.
        return ruta[1:] if len(ruta) > 2 and ruta[0] == "/" and ruta[2] == ":" else ruta
    return uri


def _posicion(ubicaciones: list[dict[str, Any]]) -> tuple[str | None, int | None, int | None]:
    """(archivo, línea, columna) de la primera ubicación."""
    fisica = (ubicaciones[0] if ubicaciones else {}).get("physicalLocation", {})
    region = fisica.get("region", {})
    return _archivo(fisica), region.get("startLine"), region.get("startColumn")


def _sin_escapes(mensaje: str) -> str:
    """Los mensajes de SARIF admiten marcadores `{0}`, así que las llaves literales van dobles."""
    return mensaje.replace("{{", "{").replace("}}", "}")


def _diagnostico(
    regla: str | None,
    nivel: str,
    mensaje: str,
    ubicaciones: list[dict[str, Any]],
) -> dict[str, Any]:
    archivo, linea, columna = _posicion(ubicaciones)
    return {
        "regla": regla,
        "flag": flag_de_regla(regla),
        "severidad": nivel if nivel in ("error", "warning") else "note",
        "mensaje": _sin_escapes(mensaje),
        "archivo": archivo,
        "linea": linea,
        "columna": columna,
    }


def parsear_sarif_gcc(salida: str) -> list[dict[str, Any]] | None:
    """Los diagnósticos de `salida` en el orden en que GCC los emitió, o None si no hay SARIF válido.

    Cada uno trae `regla`, `flag`, `severidad`, `mensaje`, `archivo`, `linea` y `columna`. Las notas
    que GCC guarda en `relatedLocations` van a continuación de su diagnóstico, como en el texto; las
    que no tienen mensaje solo describen la cadena de `#include` y se descartan.
    """
    diagnosticos: list[dict[str, Any]] = []
    encontrado = False
    try:
        for documento in _documentos(salida):
            encontrado = True
            for corrida in documento["runs"]:
                for resultado in corrida.get("results", []):
                    diagnosticos.append(
                        _diagnostico(
                            resultado.get("ruleId"),
                            resultado.get("level", "warning"),
                            resultado["message"]["text"],
                            resultado.get("locations", []),
                        )
                    )
                    for relacionada in resultado.get("relatedLocations", []):
                        texto = relacionada.get("message", {}).get("text")
                        if texto:
                            diagnosticos.append(_diagnostico(None, "note", texto, [relacionada]))
    except (AttributeError, KeyError, TypeError):
        return None
    return diagnosticos if encontrado else None


def a_hallazgos_yutani(
    diagnosticos: list[dict[str, Any]],
    herramienta: str = "daedalus",
    categoria_por_defecto: str = "compilacion",
) -> list[dict[str, Any]]:
    """Convierte diagnósticos SARIF planos al esquema estándar de `yutani.hallazgos`."""
    salida: list[dict[str, Any]] = []
    for d in diagnosticos:
        codigo = str(d.get("flag") or d.get("regla") or "diagnostico").strip() or "diagnostico"
        linea = d.get("linea")
        columna = d.get("columna")
        salida.append(
            hallazgo(
                herramienta=herramienta,
                codigo=codigo,
                categoria=categoria_por_defecto,
                severidad=str(d.get("severidad") or "warning"),
                mensaje=str(d.get("mensaje") or ""),
                archivo=d.get("archivo"),
                linea=linea if isinstance(linea, int) and linea >= 1 else None,
                columna=columna if isinstance(columna, int) and columna >= 1 else None,
            )
        )
    return salida

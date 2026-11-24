"""Cliente empaquetado Cero-Configuración para detección de similitud multi-lenguaje con JPlag."""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any

MAPA_LENGUAJES: dict[str, str] = {
    "c": "c/cpp",
    "cpp": "c/cpp",
    "c/cpp": "c/cpp",
    "python": "python3",
    "python3": "python3",
    "py": "python3",
    "java": "java",
}

def ruta_jplag_jar() -> Path:
    """Ubicación de `jplag.jar` (variable `JPLAG_JAR` o ruta estándar bajo `~/.local/share/p1-tools/jars`)."""
    env_jar = os.environ.get("JPLAG_JAR")
    if env_jar:
        return Path(env_jar).expanduser()
    return Path.home() / ".local" / "share" / "p1-tools" / "jars" / "jplag.jar"


def ruta_java() -> str | None:
    """Localiza el binario `java` en el PATH o en SDKMAN."""
    java_path = shutil.which("java")
    if java_path:
        return java_path
    java_sdkman = Path.home() / ".sdkman" / "candidates" / "java" / "current" / "bin" / "java"
    if java_sdkman.is_file() and os.access(java_sdkman, os.X_OK):
        return str(java_sdkman)
    return None


def jplag_disponible() -> tuple[bool, str]:
    """Indica si `java` y `jplag.jar` están disponibles para ejecutar JPlag."""
    java = ruta_java()
    if not java:
        return False, "No se encontró el runtime `java` en el PATH."
    jar = ruta_jplag_jar()
    if not jar.is_file():
        return False, f"No se encontró jplag.jar en {jar} (instalalo con `mother` o definí `JPLAG_JAR`)."
    return True, f"{jar} ({java})"


def _es_numero_finito(valor: Any) -> bool:
    return not isinstance(valor, bool) and isinstance(valor, (int, float)) and math.isfinite(float(valor))


def _entero_seguro(valor: Any, defecto: int = 0) -> int:
    if valor is None or isinstance(valor, bool):
        return defecto
    if isinstance(valor, int):
        return valor
    if isinstance(valor, float):
        return int(valor) if math.isfinite(valor) else defecto
    try:
        return int(valor)
    except (TypeError, ValueError):
        return defecto


def _normalizar_porcentaje(valor: float) -> float:
    if not math.isfinite(valor):
        return 0.0
    return round(valor * 100.0 if valor <= 1.0 else valor, 2)


def _similitud_desde_dict(similarities: Any) -> float:
    if _es_numero_finito(similarities):
        return _normalizar_porcentaje(float(similarities))
    if isinstance(similarities, dict):
        for clave in ("AVG", "MAX", "avg", "max"):
            if clave in similarities and _es_numero_finito(similarities[clave]):
                return _normalizar_porcentaje(float(similarities[clave]))
        for v in similarities.values():
            if _es_numero_finito(v):
                return _normalizar_porcentaje(float(v))
    return 0.0


def _clave_par(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a <= b else (b, a)


def _cargar_json_zip(zf: zipfile.ZipFile, nombre: str) -> dict[str, Any] | None:
    try:
        bruto = zf.read(nombre).decode("utf-8", errors="replace").strip()
    except (KeyError, zipfile.BadZipFile, OSError):
        return None
    if not bruto:
        return None
    try:
        datos = json.loads(bruto)
    except (json.JSONDecodeError, ValueError):
        return None
    return datos if isinstance(datos, dict) else None


def _resolver_umbral_pct(umbral: float, umbral_pct: float | None) -> float:
    if umbral_pct is not None:
        if isinstance(umbral_pct, bool) or not _es_numero_finito(umbral_pct) or not (0.0 <= float(umbral_pct) <= 100.0):
            raise ValueError(f"umbral_pct debe estar entre 0.0 y 100.0 (recibido {umbral_pct!r}).")
        return float(umbral_pct)
    if isinstance(umbral, bool) or not _es_numero_finito(umbral) or not (0.0 <= float(umbral) <= 1.0):
        raise ValueError(
            f"umbral debe ser una fracción entre 0.0 y 1.0 (recibido {umbral!r}); para porcentaje usá umbral_pct."
        )
    return float(umbral) * 100.0


def parsear_zip_jplag(
    ruta_zip: Path,
    umbral: float = 0.0,
    *,
    umbral_pct: float | None = None,
) -> list[dict[str, Any]]:
    """Lee un archivo `.jplag` (ZIP estándar) y devuelve pares ordenados por similitud descendente.

    `umbral` se interpreta como fracción normalizada en `[0.0, 1.0]` (p. ej. `0.7` equivale a 70 %).
    Para especificar un umbral porcentual directo en `[0.0, 100.0]` sin ambigüedad, usá `umbral_pct`.
    """
    umbral_efectivo = _resolver_umbral_pct(umbral, umbral_pct)
    pares: dict[tuple[str, str], dict[str, Any]] = {}

    with zipfile.ZipFile(ruta_zip, "r") as zf:
        nombres = zf.namelist()

        # 1. Leer overview.json / results.json si existe para obtener pares base y métricas agregadas
        for resumen_name in ("overview.json", "results.json"):
            if resumen_name not in nombres:
                continue
            datos = _cargar_json_zip(zf, resumen_name)
            if datos is None:
                continue
            lista = datos.get("topComparisons") or datos.get("comparisons") or []
            if not isinstance(lista, list):
                continue
            for item in lista:
                if not isinstance(item, dict):
                    continue
                a = str(item.get("firstSubmission") or item.get("firstSubmissionId") or "")
                b = str(item.get("secondSubmission") or item.get("secondSubmissionId") or "")
                if not a or not b:
                    continue
                sim_pct = _similitud_desde_dict(item.get("similarities") or item.get("similarity"))
                clave = _clave_par(a, b)
                raw_tok_first = item.get("firstSubmissionTokens")
                raw_tok_second = item.get("secondSubmissionTokens")
                if a <= b:
                    tok_a, tok_b = raw_tok_first, raw_tok_second
                else:
                    tok_a, tok_b = raw_tok_second, raw_tok_first
                pares[clave] = {
                    "entrega_a": clave[0],
                    "entrega_b": clave[1],
                    "similitud_pct": sim_pct,
                    "coincidencias": _entero_seguro(item.get("matchesCount"), 0),
                    "tokens_a": _entero_seguro(tok_a, 0),
                    "tokens_b": _entero_seguro(tok_b, 0),
                }

        # 2. Leer comparaciones detalladas bajo comparisons/*.json (enriquecen coincidencias y tokens)
        for comp_name in sorted(n for n in nombres if n.startswith("comparisons/") and n.endswith(".json")):
            comp = _cargar_json_zip(zf, comp_name)
            if comp is None:
                continue
            a = str(comp.get("firstSubmissionId") or comp.get("firstSubmission") or "")
            b = str(comp.get("secondSubmissionId") or comp.get("secondSubmission") or "")
            if not a or not b:
                continue
            sim_pct = _similitud_desde_dict(comp.get("similarities") or comp.get("similarity"))
            matches = comp.get("matches")
            if isinstance(matches, list) and matches:
                coincidencias = sum(_entero_seguro(m.get("length"), 1) for m in matches if isinstance(m, dict))
            else:
                coincidencias = _entero_seguro(comp.get("matchesCount"), 0)
            clave = _clave_par(a, b)
            previo = pares.get(clave, {})
            raw_tok_first = comp.get("firstSubmissionTokens")
            raw_tok_second = comp.get("secondSubmissionTokens")
            if a <= b:
                tok_a_raw, tok_b_raw = raw_tok_first, raw_tok_second
            else:
                tok_a_raw, tok_b_raw = raw_tok_second, raw_tok_first
            tok_a = _entero_seguro(tok_a_raw, 0) or _entero_seguro(previo.get("tokens_a"), 0)
            tok_b = _entero_seguro(tok_b_raw, 0) or _entero_seguro(previo.get("tokens_b"), 0)
            pares[clave] = {
                "entrega_a": clave[0],
                "entrega_b": clave[1],
                "similitud_pct": sim_pct if sim_pct else previo.get("similitud_pct", 0.0),
                "coincidencias": coincidencias or _entero_seguro(previo.get("coincidencias"), 0),
                "tokens_a": tok_a,
                "tokens_b": tok_b,
            }

    filtrados = [p for p in pares.values() if p["similitud_pct"] >= umbral_efectivo]
    return sorted(filtrados, key=lambda p: (-p["similitud_pct"], p["entrega_a"], p["entrega_b"]))


def ejecutar_jplag(
    directorio_entregas: Path,
    lenguaje: str = "c",
    codigo_base: Path | None = None,
    umbral: float = 0.7,
    *,
    umbral_pct: float | None = None,
) -> list[dict[str, Any]]:
    """Invoca JPlag en modo batch (`--mode RUN`) sin config externa y devuelve pares filtrados.

    `umbral` se expresa como fracción en `[0.0, 1.0]`; para porcentaje directo (`0.0..100.0`) usá `umbral_pct`.
    """
    _resolver_umbral_pct(umbral, umbral_pct)
    ok, detalle = jplag_disponible()
    if not ok:
        raise RuntimeError(f"JPlag no disponible: {detalle}")

    lang_jplag = MAPA_LENGUAJES.get(lenguaje.lower())
    if not lang_jplag:
        raise ValueError(f"Lenguaje no soportado por JPlag: {lenguaje!r}. Soportados: {', '.join(sorted(MAPA_LENGUAJES))}.")

    java = ruta_java()
    assert java is not None
    jar = ruta_jplag_jar()

    with tempfile.TemporaryDirectory(prefix="yutani-jplag-") as tmp:
        salida_prefix = Path(tmp) / "resultado"
        cmd = [
            java,
            "-jar",
            str(jar),
            "--mode",
            "RUN",
            "-l",
            lang_jplag,
            "-r",
            str(salida_prefix),
        ]
        if codigo_base is not None and Path(codigo_base).exists():
            cmd.extend(["-bc", str(codigo_base)])
        cmd.append(str(directorio_entregas))

        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        ruta_zip = salida_prefix.with_suffix(".jplag")
        if not ruta_zip.is_file():
            # Algunas versiones generan results.jplag o zip en el directorio temporal
            candidatos = list(Path(tmp).glob("*.jplag")) + list(Path(tmp).glob("*.zip"))
            if candidatos:
                ruta_zip = candidatos[0]
            else:
                raise RuntimeError(
                    f"JPlag falló (rc={proc.returncode}): {(proc.stderr or proc.stdout).strip()[:400]}"
                )
        return parsear_zip_jplag(ruta_zip, umbral=umbral, umbral_pct=umbral_pct)

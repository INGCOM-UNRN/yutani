"""Cliente empaquetado Cero-Configuración para detección de similitud multi-lenguaje con JPlag."""

from __future__ import annotations

import json
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
    if java_sdkman.is_file():
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


def _normalizar_porcentaje(valor: float) -> float:
    return round(valor * 100.0 if valor <= 1.0 else valor, 2)


def _similitud_desde_dict(similarities: Any) -> float:
    if isinstance(similarities, (int, float)):
        return _normalizar_porcentaje(float(similarities))
    if isinstance(similarities, dict):
        for clave in ("AVG", "MAX", "avg", "max"):
            if clave in similarities and isinstance(similarities[clave], (int, float)):
                return _normalizar_porcentaje(float(similarities[clave]))
        for v in similarities.values():
            if isinstance(v, (int, float)):
                return _normalizar_porcentaje(float(v))
    return 0.0


def _clave_par(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a <= b else (b, a)


def parsear_zip_jplag(ruta_zip: Path, umbral: float = 0.0) -> list[dict[str, Any]]:
    """Lee un archivo `.jplag` (ZIP estándar) y devuelve pares ordenados por similitud descendente."""
    umbral_pct = umbral * 100.0 if umbral <= 1.0 else umbral
    pares: dict[tuple[str, str], dict[str, Any]] = {}

    with zipfile.ZipFile(ruta_zip, "r") as zf:
        nombres = zf.namelist()

        # 1. Leer overview.json / results.json si existe para obtener pares base y métricas agregadas
        for resumen_name in ("overview.json", "results.json"):
            if resumen_name not in nombres:
                continue
            datos = json.loads(zf.read(resumen_name).decode("utf-8"))
            lista = datos.get("topComparisons") or datos.get("comparisons") or []
            for item in lista:
                a = str(item.get("firstSubmission") or item.get("firstSubmissionId") or "")
                b = str(item.get("secondSubmission") or item.get("secondSubmissionId") or "")
                if not a or not b:
                    continue
                sim_pct = _similitud_desde_dict(item.get("similarities") or item.get("similarity"))
                clave = _clave_par(a, b)
                pares[clave] = {
                    "entrega_a": clave[0],
                    "entrega_b": clave[1],
                    "similitud_pct": sim_pct,
                    "coincidencias": int(item.get("matchesCount") or 0),
                    "tokens_a": int(item.get("firstSubmissionTokens") or 0),
                    "tokens_b": int(item.get("secondSubmissionTokens") or 0),
                }

        # 2. Leer comparaciones detalladas bajo comparisons/*.json (enriquecen coincidencias y tokens)
        for comp_name in sorted(n for n in nombres if n.startswith("comparisons/") and n.endswith(".json")):
            comp = json.loads(zf.read(comp_name).decode("utf-8"))
            a = str(comp.get("firstSubmissionId") or comp.get("firstSubmission") or "")
            b = str(comp.get("secondSubmissionId") or comp.get("secondSubmission") or "")
            if not a or not b:
                continue
            sim_pct = _similitud_desde_dict(comp.get("similarities") or comp.get("similarity"))
            matches = comp.get("matches") or []
            coincidencias = sum(int(m.get("length", 1)) for m in matches) if matches else int(comp.get("matchesCount") or 0)
            clave = _clave_par(a, b)
            previo = pares.get(clave, {})
            pares[clave] = {
                "entrega_a": clave[0],
                "entrega_b": clave[1],
                "similitud_pct": sim_pct if sim_pct else previo.get("similitud_pct", 0.0),
                "coincidencias": coincidencias or previo.get("coincidencias", 0),
                "tokens_a": int(comp.get("firstSubmissionTokens") or previo.get("tokens_a") or 0),
                "tokens_b": int(comp.get("secondSubmissionTokens") or previo.get("tokens_b") or 0),
            }

    filtrados = [p for p in pares.values() if p["similitud_pct"] >= umbral_pct]
    return sorted(filtrados, key=lambda p: (-p["similitud_pct"], p["entrega_a"], p["entrega_b"]))


def ejecutar_jplag(
    directorio_entregas: Path,
    lenguaje: str = "c",
    codigo_base: Path | None = None,
    umbral: float = 0.7,
) -> list[dict[str, Any]]:
    """Invoca JPlag en modo batch (`--mode RUN`) sin config externa y devuelve pares filtrados."""
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
        return parsear_zip_jplag(ruta_zip, umbral=umbral)

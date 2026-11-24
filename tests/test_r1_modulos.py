"""Pruebas de los módulos agregados en la ola R1: sarif, ts_queries y jplag."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from yutani.hallazgos import validar_salida
from yutani.jplag import jplag_disponible, parsear_zip_jplag
from yutani.sarif import a_hallazgos_yutani, flag_de_regla, parsear_sarif_gcc
from yutani.ts_queries import (
    ejecutar_consulta,
    enmascarar_bloques_inactivos,
    parsear_c,
    texto_nodo,
)


def test_sarif_parsea_diagnostico_y_normaliza_hallazgos():
    sarif = json.dumps({
        "version": "2.1.0",
        "runs": [{
            "results": [{
                "ruleId": "-Werror=return-type",
                "level": "error",
                "message": {"text": "control reaches end of {{non-void}} function"},
                "locations": [{
                    "physicalLocation": {
                        "artifactLocation": {"uri": "main.c"},
                        "region": {"startLine": 7, "startColumn": 1},
                    }
                }],
            }]
        }],
    })
    assert flag_de_regla("-Werror=return-type") == "-Wreturn-type"
    diags = parsear_sarif_gcc(sarif)
    assert diags is not None and len(diags) == 1
    assert diags[0]["flag"] == "-Wreturn-type"
    assert diags[0]["mensaje"] == "control reaches end of {non-void} function"

    hallazgos = a_hallazgos_yutani(diags)
    assert validar_salida({"hallazgos": hallazgos}) == []
    assert hallazgos[0]["id"] == "daedalus:-Wreturn-type"
    assert hallazgos[0]["severidad"] == "error"


def test_ts_queries_descarta_if0_y_conserva_lineas():
    codigo = """\
#include <stdio.h>
#if 0
int muerta(void) {
    gets(buf);
    return 0;
}
#endif
int viva(void) {
    char buf[10];
    gets(buf);
    return 0;
}
"""
    enmascarado = enmascarar_bloques_inactivos(codigo)
    assert len(enmascarado.splitlines()) == len(codigo.splitlines())
    raiz = parsear_c(codigo, filtrar_if0=True)
    matches = ejecutar_consulta(
        raiz,
        '(call_expression function: (identifier) @fn (#eq? @fn "gets")) @call',
    )
    assert len(matches) == 1
    fn_node = matches[0]["fn"][0]
    assert texto_nodo(fn_node) == "gets"
    assert fn_node.start_point.row + 1 == 10


def test_jplag_parsea_zip_y_ordena_por_similitud(tmp_path: Path, monkeypatch):
    archivo = tmp_path / "resultado.jplag"
    with zipfile.ZipFile(archivo, "w") as zf:
        zf.writestr("overview.json", json.dumps({
            "topComparisons": [
                {
                    "firstSubmission": "ana",
                    "secondSubmission": "luis",
                    "similarities": {"AVG": 0.45, "MAX": 0.50},
                },
                {
                    "firstSubmission": "pepe",
                    "secondSubmission": "maria",
                    "similarities": {"AVG": 0.92, "MAX": 0.96},
                    "firstSubmissionTokens": 120,
                    "secondSubmissionTokens": 118,
                },
            ]
        }))
        zf.writestr("comparisons/maria-pepe.json", json.dumps({
            "firstSubmissionId": "maria",
            "secondSubmissionId": "pepe",
            "similarities": {"AVG": 0.92},
            "matches": [{"length": 40}, {"length": 35}],
        }))

    pares = parsear_zip_jplag(archivo, umbral=0.7)
    assert len(pares) == 1
    assert pares[0]["entrega_a"] == "maria"
    assert pares[0]["entrega_b"] == "pepe"
    assert pares[0]["similitud_pct"] == 92.0
    assert pares[0]["coincidencias"] == 75
    assert pares[0]["tokens_a"] == 120

    monkeypatch.setenv("JPLAG_JAR", str(tmp_path / "inexistente.jar"))
    ok, motivo = jplag_disponible()
    assert ok is False
    assert "jplag.jar" in motivo or "java" in motivo


def test_jplag_ruta_java_usa_home_dinamico(tmp_path: Path, monkeypatch):
    from yutani import jplag

    fake_java = tmp_path / ".sdkman" / "candidates" / "java" / "current" / "bin" / "java"
    fake_java.parent.mkdir(parents=True)
    fake_java.write_text("#!/bin/sh\n")

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(jplag.shutil, "which", lambda _cmd: None)
    assert jplag.ruta_java() == str(fake_java)

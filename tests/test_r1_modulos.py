"""Pruebas de los módulos agregados en la ola R1: sarif, ts_queries y jplag."""

from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path

import pytest

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
    assert pares[0]["tokens_a"] == 118
    assert pares[0]["tokens_b"] == 120

    monkeypatch.setenv("JPLAG_JAR", str(tmp_path / "inexistente.jar"))
    ok, motivo = jplag_disponible()
    assert ok is False
    assert "jplag.jar" in motivo or "java" in motivo


def test_jplag_ruta_java_usa_home_dinamico(tmp_path: Path, monkeypatch):
    from yutani import jplag

    fake_java = tmp_path / ".sdkman" / "candidates" / "java" / "current" / "bin" / "java"
    fake_java.parent.mkdir(parents=True)
    fake_java.write_text("#!/bin/sh\n")
    fake_java.chmod(0o755)

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(jplag.shutil, "which", lambda _cmd: None)
    assert jplag.ruta_java() == str(fake_java)


@pytest.mark.skipif(os.name == "nt", reason="Windows no usa bits POSIX de ejecución")
def test_jplag_ruta_java_ignora_sdkman_no_ejecutable(tmp_path: Path, monkeypatch):
    from yutani import jplag

    fake_java = tmp_path / ".sdkman" / "candidates" / "java" / "current" / "bin" / "java"
    fake_java.parent.mkdir(parents=True)
    fake_java.write_text("#!/bin/sh\n")
    fake_java.chmod(0o644)

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(jplag.shutil, "which", lambda _cmd: None)
    assert jplag.ruta_java() is None


def test_ts_queries_preserva_crlf_y_offsets_utf8():
    codigo_crlf = "int a = 1;\r\n#if 0\r\nint dead = 2;\r\n#endif\r\nint b = 3;\r\n"
    enmascarado_crlf = enmascarar_bloques_inactivos(codigo_crlf)
    assert enmascarado_crlf.splitlines(keepends=True)[0].endswith("\r\n")
    assert len(enmascarado_crlf.splitlines(keepends=True)) == 5
    assert enmascarado_crlf.encode("utf-8").count(b"\r\n") == 5
    assert len(enmascarado_crlf.encode("utf-8")) == len(codigo_crlf.encode("utf-8"))

    codigo_utf8 = (
        'int a = 1;\n'
        '#if 0\n'
        '// Comentario con ñ, á, €, 🚀\n'
        'char *s = "ñandú 🚀";\n'
        '#endif\n'
        'int b = 3;\n'
    )
    enmascarado_utf8 = enmascarar_bloques_inactivos(codigo_utf8)
    assert len(enmascarado_utf8.encode("utf-8")) == len(codigo_utf8.encode("utf-8"))
    assert enmascarado_utf8.encode("utf-8").index(b"int b = 3;") == codigo_utf8.encode("utf-8").index(b"int b = 3;")


def test_ts_queries_elif_0_y_comentarios_multilinea():
    codigo_elif = (
        "#if 0\n"
        "int dead_if0(void) { return 1; }\n"
        "#elif 0\n"
        "int dead_elif0(void) { return 2; }\n"
        "#else\n"
        "int alive_else(void) { return 3; }\n"
        "#endif\n"
    )
    matches = ejecutar_consulta(
        codigo_elif,
        "(function_definition declarator: (function_declarator declarator: (identifier) @fn))",
    )
    assert [texto_nodo(m["fn"][0]) for m in matches] == ["alive_else"]

    codigo_comentario = (
        "/*\n"
        "#if 0\n"
        "*/\n"
        "int real_code(void) { return 42; }\n"
    )
    matches_comentario = ejecutar_consulta(
        codigo_comentario,
        "(function_definition declarator: (function_declarator declarator: (identifier) @fn))",
    )
    assert [texto_nodo(m["fn"][0]) for m in matches_comentario] == ["real_code"]

    codigo_endif_en_comentario = (
        "#if 0\n"
        "/*\n"
        "#endif\n"
        "*/\n"
        "int still_dead(void) { return 0; }\n"
        "#endif\n"
        "int alive(void) { return 1; }\n"
    )
    matches_anidado = ejecutar_consulta(
        codigo_endif_en_comentario,
        "(function_definition declarator: (function_declarator declarator: (identifier) @fn))",
    )
    assert [texto_nodo(m["fn"][0]) for m in matches_anidado] == ["alive"]


def test_jplag_tokens_intercambiados_json_malformado_y_umbral(tmp_path: Path):
    archivo_swap = tmp_path / "swap.jplag"
    with zipfile.ZipFile(archivo_swap, "w") as zf:
        zf.writestr("overview.json", json.dumps({
            "topComparisons": [{
                "firstSubmission": "zoe",
                "secondSubmission": "ana",
                "similarities": {"AVG": 0.90},
                "firstSubmissionTokens": 300,
                "secondSubmissionTokens": 150,
            }]
        }))
        zf.writestr("comparisons/zoe-ana.json", json.dumps({
            "firstSubmissionId": "zoe",
            "secondSubmissionId": "ana",
            "similarities": {"AVG": 0.90},
            "firstSubmissionTokens": 310,
            "secondSubmissionTokens": 160,
            "matches": [{"length": 20}],
        }))

    pares = parsear_zip_jplag(archivo_swap)
    assert len(pares) == 1
    assert pares[0]["entrega_a"] == "ana"
    assert pares[0]["entrega_b"] == "zoe"
    assert pares[0]["tokens_a"] == 160
    assert pares[0]["tokens_b"] == 310

    assert len(parsear_zip_jplag(archivo_swap, umbral=1.0)) == 0
    assert len(parsear_zip_jplag(archivo_swap, umbral_pct=1.0)) == 1
    with pytest.raises(ValueError):
        parsear_zip_jplag(archivo_swap, umbral=1.5)

    archivo_roto = tmp_path / "roto.jplag"
    with zipfile.ZipFile(archivo_roto, "w") as zf:
        zf.writestr("overview.json", "")
        zf.writestr("results.json", "null")
        zf.writestr("comparisons/vacio.json", "")
        zf.writestr("comparisons/nan.json", json.dumps({
            "firstSubmissionId": "a",
            "secondSubmissionId": "b",
            "similarities": float("nan"),
            "matchesCount": "NaN",
            "firstSubmissionTokens": "NaN",
            "secondSubmissionTokens": 42,
        }))

    pares_roto = parsear_zip_jplag(archivo_roto)
    assert pares_roto == [{
        "entrega_a": "a",
        "entrega_b": "b",
        "similitud_pct": 0.0,
        "coincidencias": 0,
        "tokens_a": 0,
        "tokens_b": 42,
    }]


def test_sarif_campos_null_mensajes_alternativos_stream_y_bool():
    doc_valido = json.dumps({
        "runs": [{
            "results": [{
                "ruleId": "-Wall",
                "level": "warning",
                "message": {"text": "variable sin usar"},
                "locations": [{
                    "physicalLocation": {
                        "artifactLocation": {"uri": "a.c"},
                        "region": {"startLine": 3, "startColumn": 5},
                    }
                }],
            }]
        }]
    })
    doc_con_nulos = json.dumps({
        "runs": [{
            "results": [
                {
                    "ruleId": "-Wnull",
                    "level": None,
                    "message": {"id": "MSG_ID"},
                    "locations": [{"physicalLocation": None}],
                    "relatedLocations": [{"message": None}],
                },
                {
                    "ruleId": "-Wnull2",
                    "message": {"markdown": "detalle *md*"},
                    "locations": [{"physicalLocation": {"artifactLocation": None, "region": None}}],
                },
                {
                    "ruleId": "-Wroto",
                    "message": None,
                },
            ]
        }]
    })
    diags = parsear_sarif_gcc(doc_valido + "\n" + doc_con_nulos)
    assert diags is not None
    assert len(diags) == 3
    assert [d["mensaje"] for d in diags] == ["variable sin usar", "MSG_ID", "detalle *md*"]

    # Guard bool en línea/columna
    diags_bool = [{
        "regla": "-Wall",
        "flag": "-Wall",
        "severidad": "warning",
        "mensaje": "aviso",
        "archivo": "main.c",
        "linea": True,
        "columna": False,
    }]
    hallazgos = a_hallazgos_yutani(diags_bool)
    assert hallazgos[0]["linea"] is None
    assert hallazgos[0]["columna"] is None
    assert validar_salida({"hallazgos": hallazgos}) == []

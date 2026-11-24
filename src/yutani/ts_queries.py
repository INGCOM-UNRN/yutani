"""Motor declarativo de consultas Tree-sitter para C con descarte de bloques `#if 0`.

Centraliza la inicialización de `tree_sitter_c`, el enmascarado de bloques inactivos `#if 0`
preservando saltos de línea y offsets de bytes, y la compilación cacheada de consultas S-expression.
"""

from __future__ import annotations

import re
from functools import cache, lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tree_sitter import Language, Node, Parser, Query

_INICIO_IF_FALSO = re.compile(r"^\s*#\s*if\s+0\s*(?://.*|/\*.*)?$")
_INICIO_CONDICIONAL = re.compile(r"^\s*#\s*(if|ifdef|ifndef)\b")
_FIN_CONDICIONAL = re.compile(r"^\s*#\s*endif\b")
_RAMA_ALTERNATIVA = re.compile(r"^\s*#\s*(else|elif)\b")


def _blanquear(texto: str) -> str:
    """Sustituye cada carácter por un espacio, conservando los saltos de línea."""
    return "".join("\n" if c in "\r\n" else " " for c in texto)


def enmascarar_bloques_inactivos(contenido: str) -> str:
    """Blanquea el cuerpo de los bloques `#if 0`, incluidos los anidados, manteniendo offsets."""
    lineas = contenido.splitlines(keepends=True)
    salida: list[str] = []
    profundidad: int | None = None

    for linea in lineas:
        if profundidad is None:
            salida.append(linea)
            if _INICIO_IF_FALSO.match(linea.rstrip("\r\n")):
                profundidad = 1
            continue

        if _INICIO_CONDICIONAL.match(linea):
            profundidad += 1
            salida.append(_blanquear(linea))
        elif _FIN_CONDICIONAL.match(linea):
            profundidad -= 1
            if profundidad == 0:
                profundidad = None
                salida.append(linea)
            else:
                salida.append(_blanquear(linea))
        elif profundidad == 1 and _RAMA_ALTERNATIVA.match(linea):
            profundidad = None
            salida.append(linea)
        else:
            salida.append(_blanquear(linea))

    return "".join(salida)


@lru_cache(maxsize=1)
def lenguaje_c() -> Language:
    """Carga y cachea la gramática C de `tree-sitter-c`."""
    try:
        import tree_sitter_c as tsc
        from tree_sitter import Language
    except ImportError as exc:
        raise ImportError(
            "yutani.ts_queries requiere el extra opcional 'c' (`tree-sitter` y `tree-sitter-c`)."
        ) from exc
    return Language(tsc.language())


@lru_cache(maxsize=1)
def parser_c() -> Parser:
    """Devuelve una instancia reutilizable de `tree_sitter.Parser` para C."""
    from tree_sitter import Parser

    return Parser(lenguaje_c())


def parsear_c(codigo: str | bytes, filtrar_if0: bool = True) -> Node:
    """Parsea código C y devuelve el nodo raíz del AST, blanqueando `#if 0` si `filtrar_if0=True`."""
    if isinstance(codigo, bytes):
        texto = codigo.decode("utf-8", errors="replace")
    else:
        texto = codigo
    if filtrar_if0:
        texto = enmascarar_bloques_inactivos(texto)
    arbol = parser_c().parse(texto.encode("utf-8"))
    return arbol.root_node


@cache
def compilar_consulta(consulta_sexpr: str) -> Query:
    """Compila y cachea una consulta S-expression de Tree-sitter sobre C."""
    from tree_sitter import Query

    return Query(lenguaje_c(), consulta_sexpr)


def ejecutar_consulta(
    nodo_o_codigo: Node | str | bytes,
    consulta_sexpr: str,
    filtrar_if0: bool = True,
) -> list[dict[str, list[Node]]]:
    """Ejecuta una consulta S-expression y devuelve la lista de capturas por coincidencia."""
    from tree_sitter import QueryCursor

    raiz = (
        parsear_c(nodo_o_codigo, filtrar_if0=filtrar_if0)
        if isinstance(nodo_o_codigo, (str, bytes))
        else nodo_o_codigo
    )
    cursor = QueryCursor(compilar_consulta(consulta_sexpr))
    return [capturas for _, capturas in cursor.matches(raiz)]


def texto_nodo(nodo: Node | None) -> str:
    """Decodifica el texto UTF-8 de un nodo Tree-sitter."""
    if nodo is None or nodo.text is None:
        return ""
    return nodo.text.decode("utf-8", errors="replace")

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

_INICIO_IF_FALSO = re.compile(r"^\s*#\s*if\s+(?:0[uUlL]*|\(\s*0[uUlL]*\s*\))\s*$")
_INICIO_CONDICIONAL = re.compile(r"^\s*#\s*(if|ifdef|ifndef)\b")
_FIN_CONDICIONAL = re.compile(r"^\s*#\s*endif\b")
_ELIF_FALSO = re.compile(r"^\s*#\s*elif\s+(?:0[uUlL]*|\(\s*0[uUlL]*\s*\))\s*$")
_RAMA_ALTERNATIVA = re.compile(r"^\s*#\s*(else|elif)\b")
_DIVIDIR_LINEAS = re.compile(r"([^\r\n]*)(\r\n|\r|\n|$)")


def _blanquear(texto: str) -> str:
    """Sustituye cada carácter por espacios ASCII equivalentes en bytes UTF-8, conservando saltos."""
    partes: list[str] = []
    i = 0
    n = len(texto)
    while i < n:
        c = texto[i]
        if c == "\r":
            if i + 1 < n and texto[i + 1] == "\n":
                partes.append("\r\n")
                i += 2
            else:
                partes.append("\r")
                i += 1
        elif c == "\n":
            partes.append("\n")
            i += 1
        else:
            partes.append(" " * len(c.encode("utf-8")))
            i += 1
    return "".join(partes)


def _termina_con_continuacion(cuerpo: str) -> bool:
    """Indica si la línea termina con un número impar de barras invertidas (`\\`)."""
    barras = 0
    for c in reversed(cuerpo):
        if c == "\\":
            barras += 1
        else:
            break
    return (barras % 2) == 1


def _strip_comentarios_y_literales_linea(
    cuerpo: str,
    en_comentario_bloque: bool,
    en_comentario_linea_cont: bool,
    en_literal_cont: str | None,
) -> tuple[str, bool, bool, str | None]:
    """Devuelve `cuerpo` con comentarios y literales multilínea neutralizados para detectar directivas."""
    if en_comentario_linea_cont:
        return " " * len(cuerpo), False, _termina_con_continuacion(cuerpo), None

    limpio: list[str] = []
    i = 0
    n = len(cuerpo)
    while i < n:
        if en_comentario_bloque:
            fin = cuerpo.find("*/", i)
            if fin == -1:
                limpio.append(" " * (n - i))
                i = n
            else:
                limpio.append(" " * (fin + 2 - i))
                en_comentario_bloque = False
                i = fin + 2
            continue

        if en_literal_cont is not None:
            comilla = en_literal_cont
            cerrada = False
            while i < n:
                ch = cuerpo[i]
                if ch == "\\":
                    if i + 1 < n:
                        limpio.append("  ")
                        i += 2
                    else:
                        limpio.append(" ")
                        i += 1
                elif ch == comilla:
                    limpio.append(" ")
                    en_literal_cont = None
                    cerrada = True
                    i += 1
                    break
                else:
                    limpio.append(" ")
                    i += 1
            if not cerrada and not _termina_con_continuacion(cuerpo):
                en_literal_cont = None
            continue

        par = cuerpo[i : i + 2]
        if par == "/*":
            en_comentario_bloque = True
            limpio.append("  ")
            i += 2
        elif par == "//":
            limpio.append(" " * (n - i))
            en_comentario_linea_cont = _termina_con_continuacion(cuerpo)
            break
        elif cuerpo[i] in ('"', "'"):
            comilla = cuerpo[i]
            limpio.append(comilla)
            i += 1
            cerrada = False
            while i < n:
                ch = cuerpo[i]
                if ch == "\\":
                    if i + 1 < n:
                        limpio.append("  ")
                        i += 2
                    else:
                        limpio.append(" ")
                        i += 1
                elif ch == comilla:
                    limpio.append(comilla)
                    cerrada = True
                    i += 1
                    break
                else:
                    limpio.append(" ")
                    i += 1
            if not cerrada and _termina_con_continuacion(cuerpo):
                en_literal_cont = comilla
        else:
            limpio.append(cuerpo[i])
            i += 1

    return "".join(limpio), en_comentario_bloque, en_comentario_linea_cont, en_literal_cont


def enmascarar_bloques_inactivos(contenido: str) -> str:
    """Blanquea el cuerpo de los bloques `#if 0`, incluidos los anidados, manteniendo offsets."""
    if not contenido:
        return ""

    lineas = [
        (m.group(1), m.group(2))
        for m in _DIVIDIR_LINEAS.finditer(contenido)
        if m.group(0)
    ]
    salida: list[str] = []
    profundidad: int | None = None
    en_comentario_bloque = False
    en_comentario_linea_cont = False
    en_literal_cont: str | None = None

    for cuerpo, salto in lineas:
        linea = cuerpo + salto
        (
            linea_efectiva,
            en_comentario_bloque,
            en_comentario_linea_cont,
            en_literal_cont,
        ) = _strip_comentarios_y_literales_linea(
            cuerpo,
            en_comentario_bloque,
            en_comentario_linea_cont,
            en_literal_cont,
        )

        if profundidad is None:
            salida.append(linea)
            if _INICIO_IF_FALSO.match(linea_efectiva):
                profundidad = 1
            continue

        if _INICIO_CONDICIONAL.match(linea_efectiva):
            profundidad += 1
            salida.append(_blanquear(linea))
        elif _FIN_CONDICIONAL.match(linea_efectiva):
            profundidad -= 1
            if profundidad == 0:
                profundidad = None
                salida.append(linea)
            else:
                salida.append(_blanquear(linea))
        elif profundidad == 1 and _ELIF_FALSO.match(linea_efectiva):
            salida.append(_blanquear(linea))
        elif profundidad == 1 and _RAMA_ALTERNATIVA.match(linea_efectiva):
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

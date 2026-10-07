"""Parser del CSV de portafolio: errores de archivo y de fila (T1.4)."""

from decimal import Decimal

import pytest

from brujula.features.portfolios.csv_import import TEMPLATE, CsvFormatError, parse_csv

LIMITS = {"max_bytes": 10_000, "max_rows": 5}


def test_la_plantilla_es_un_csv_valido_sin_filas() -> None:
    result = parse_csv(TEMPLATE.encode(), **LIMITS)

    assert (result.rows, result.valid, result.errors) == (0, [], [])


def test_filas_validas_e_invalidas_se_separan_con_su_numero_de_linea() -> None:
    result = parse_csv(
        (
            b"ticker,cantidad,precio_promedio,moneda_precio,broker\n"
            b"ggal,100,4200.5,ars,Broker A\n"
            b"YPFD,0,,,\n"
            b"\n"
            b"AL30,10,55,EUR,\n"
            b"MELI,2,,,\n"
        ),
        **LIMITS,
    )

    assert result.rows == 4
    assert [(line, h.ticker, h.cantidad) for line, h in result.valid] == [
        (2, "GGAL", Decimal(100)),
        (6, "MELI", Decimal(2)),
    ]
    assert result.valid[0][1].moneda_precio == "ARS"
    assert [(e.row, e.errors) for e in result.errors] == [
        (3, ["cantidad: debe ser mayor que 0"]),
        (5, ["moneda_precio: debe ser ARS o USD"]),
    ]


def test_formato_de_excel_argentino_punto_y_coma_y_coma_decimal() -> None:
    content = "Ticker;Cantidad;Precio_promedio;Moneda_precio\nGGAL;1,5;4200,25;ARS\n"

    result = parse_csv(content.encode("cp1252"), **LIMITS)

    [(_, holding)] = result.valid
    assert (holding.cantidad, holding.precio_promedio) == (Decimal("1.5"), Decimal("4200.25"))


def test_utf8_con_bom() -> None:
    result = parse_csv("﻿ticker,cantidad\nGGAL,1\n".encode(), **LIMITS)

    assert len(result.valid) == 1


def test_broker_con_acentos_en_windows_1252() -> None:
    content = "ticker,cantidad,broker\nGGAL,1,Inversión Ágil\n"

    result = parse_csv(content.encode("cp1252"), **LIMITS)

    assert result.valid[0][1].broker == "Inversión Ágil"


def test_numero_ambiguo_se_rechaza() -> None:
    result = parse_csv(b"ticker;cantidad\nGGAL;1.000,50\n", **LIMITS)

    assert "formato ambiguo" in result.errors[0].errors[0]


def test_fila_con_columnas_de_menos() -> None:
    result = parse_csv(b"ticker,cantidad,broker\nGGAL,1\n", **LIMITS)

    assert result.errors[0].errors == ["tiene 2 columnas y se esperan 3"]


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"", "vacío"),
        (b"   \n", "vacío"),
        (b"ticker,precio_promedio\nGGAL,1\n", "faltan: cantidad"),
        (b"ticker,cantidad,password\nGGAL,1,x\n", "sobran: password"),
        (b"ticker,cantidad,cantidad\nGGAL,1,2\n", "Columnas inválidas"),
    ],
)
def test_errores_de_archivo(content: bytes, message: str) -> None:
    with pytest.raises(CsvFormatError, match=message):
        parse_csv(content, **LIMITS)


def test_limite_de_bytes() -> None:
    with pytest.raises(CsvFormatError, match="máximo de 20 bytes"):
        parse_csv(b"ticker,cantidad\nGGAL,1\nYPFD,2\n", max_bytes=20, max_rows=5)


def test_limite_de_filas() -> None:
    rows = "".join(f"T{i},1\n" for i in range(6))

    with pytest.raises(CsvFormatError, match="máximo de 5 filas"):
        parse_csv(f"ticker,cantidad\n{rows}".encode(), **LIMITS)

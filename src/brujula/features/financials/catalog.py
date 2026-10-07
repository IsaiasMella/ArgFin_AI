"""Catálogo de métricas internas (`config/metrics.yaml`) y mapeo XBRL (`config/sec_xbrl.yaml`)."""

import re
from datetime import date
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

METRICS_FILE = "metrics.yaml"
SEC_XBRL_FILE = "sec_xbrl.yaml"
SECTOR_METRICS_FILE = "metrics_by_sector.yaml"
CONCEPT = re.compile(r"^[a-z][a-z0-9\-]*:[A-Za-z][A-Za-z0-9]*$")


class FinancialsConfigError(Exception):
    """Un archivo de configuración de cifras no existe o no cumple el esquema."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Metric(_Strict):
    nombre: str
    tipo: Literal["saldo", "flujo"]
    unidad: Literal["moneda", "moneda_por_accion", "acciones"]


class MetricCatalog(_Strict):
    version: Literal[1]
    metricas: dict[str, Metric] = Field(min_length=1)


class SecXbrlMapping(_Strict):
    version: Literal[1]
    desde: date
    formularios: list[str] = Field(min_length=1)
    conceptos: dict[str, list[str]] = Field(min_length=1)

    @model_validator(mode="after")
    def _concepts(self) -> Self:
        for metric, concepts in self.conceptos.items():
            bad = [c for c in concepts if not CONCEPT.match(c)]
            if bad or not concepts:
                raise ValueError(f"{metric}: conceptos inválidos {bad}")
        return self

    def check_against(self, catalog: MetricCatalog) -> None:
        unknown = sorted(set(self.conceptos) - set(catalog.metricas))
        if unknown:
            raise FinancialsConfigError(f"métricas fuera del catálogo: {', '.join(unknown)}")


def _load[T: BaseModel](path: Path, model: type[T]) -> T:
    try:
        return model.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except OSError as exc:
        raise FinancialsConfigError(f"no se pudo leer {path}: {exc.strerror}") from None
    except (yaml.YAMLError, ValidationError) as exc:
        raise FinancialsConfigError(f"{path} no es válido:\n{exc}") from None


def load_metric_catalog(config_dir: Path) -> MetricCatalog:
    return _load(config_dir / METRICS_FILE, MetricCatalog)


def load_sec_mapping(config_dir: Path, catalog: MetricCatalog) -> SecXbrlMapping:
    mapping = _load(config_dir / SEC_XBRL_FILE, SecXbrlMapping)
    mapping.check_against(catalog)
    return mapping


class SectorMetrics(_Strict):
    """Métricas obligatorias de un estado contable según el sector de la empresa."""

    version: Literal[1]
    comunes: list[str] = Field(min_length=1)
    sectores: dict[str, list[str]] = Field(min_length=1)

    def required(self, sector: str) -> list[str]:
        if sector not in self.sectores:
            raise FinancialsConfigError(f"sector sin métricas obligatorias: {sector}")
        return [*self.comunes, *self.sectores[sector]]


def load_sector_metrics(
    config_dir: Path, catalog: MetricCatalog, sectors: set[str]
) -> SectorMetrics:
    """Valida que las métricas existan y que todos los sectores del universo estén."""
    config = _load(config_dir / SECTOR_METRICS_FILE, SectorMetrics)
    used = {m for metrics in config.sectores.values() for m in metrics} | set(config.comunes)
    unknown = sorted(used - set(catalog.metricas))
    if unknown:
        raise FinancialsConfigError(f"métricas fuera del catálogo: {', '.join(unknown)}")
    missing = sorted(sectors - set(config.sectores))
    if missing:
        raise FinancialsConfigError(f"sectores sin métricas obligatorias: {', '.join(missing)}")
    return config

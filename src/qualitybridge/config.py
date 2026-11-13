from pathlib import Path

import yaml
from pydantic import ValidationError

from qualitybridge.models import ProjectConfig


class ConfigurationError(ValueError):
    pass


def load_config(path: Path) -> tuple[ProjectConfig, Path, Path]:
    path = path.resolve()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        config = ProjectConfig.model_validate(raw)
    except (OSError, ValueError, yaml.YAMLError, ValidationError) as exc:
        raise ConfigurationError(f"Configuração inválida em {path}: {exc}") from exc
    root = (path.parent / config.root).resolve()
    if not root.is_dir():
        raise ConfigurationError(f"Diretório do projeto não encontrado: {root}")
    output = (root / config.output_dir).resolve()
    if output == root or output in root.parents:
        raise ConfigurationError("output_dir deve ser uma pasta dedicada aos resultados.")
    return config, root, output

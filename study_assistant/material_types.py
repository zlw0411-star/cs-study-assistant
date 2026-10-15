"""Public data types for uploaded study materials."""

from dataclasses import dataclass


@dataclass
class MaterialSource:
    id: str
    text: str
    locator: str
    page: int | None = None
    paragraph: int | None = None


@dataclass
class MaterialInfo:
    id: str
    filename: str
    kind: str
    imported_at: str
    source_count: int
    character_count: int
    warnings: list[str]


@dataclass
class MaterialImportResult:
    material: MaterialInfo
    duplicate: bool
    warnings: list[str]

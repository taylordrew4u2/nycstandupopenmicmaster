"""Validated input contracts. Imported content is data, never executable instructions."""
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict

class SourceConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=120)
    url: str = Field(default='', max_length=2048)
    kind: Literal['auto', 'csv', 'xlsx', 'json', 'table', 'jsonld', 'cards', 'upload'] = 'auto'
    interval_minutes: Literal[15, 30, 60, 180, 360, 720, 1440] = 60
    priority: Literal[10, 50, 100] = 50
    permission_confirmed: bool = False
    sheet: str = Field(default='', max_length=100)
    row_selector: str = Field(default='', max_length=300)
    mapping: dict[str, str] = Field(default_factory=dict)
    defaults: dict[str, str] = Field(default_factory=dict)

class PreviewCommit(BaseModel):
    preview_id: str
    replacement_source_id: str | None = Field(default=None, max_length=100)

class Login(BaseModel):
    password: str = Field(min_length=1, max_length=500)

class AdminPasswordUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    current_password: str = Field(min_length=1, max_length=500, repr=False)
    new_password: str = Field(min_length=1, max_length=500, repr=False)

class SourceUpdate(BaseModel):
    enabled: bool | None = None
    interval_minutes: Literal[15, 30, 60, 180, 360, 720, 1440] | None = None
    priority: Literal[10, 50, 100] | None = None

class VisibilityUpdate(BaseModel):
    hidden: bool

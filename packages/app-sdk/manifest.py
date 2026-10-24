"""FamilOS App Manifest v1. Validate with: python manifest.py app.json."""
import json
import sys
from pathlib import Path
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

Id = Annotated[str, Field(pattern=r'^[a-z][a-z0-9-]{1,47}$')]
Route = Annotated[str, Field(pattern=r'^/(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-]*$')]

class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

class Volume(Strict):
    name: Id
    mount: Annotated[str, Field(pattern=r'^/app-data/[a-z][a-z0-9-]{1,47}$')]
    backup: Literal['stop-and-copy']

class Service(Strict):
    image: Annotated[str, Field(pattern=r'^[a-z0-9][a-z0-9./:_-]*@sha256:[a-f0-9]{64}$')]
    architectures: list[Literal['linux/amd64', 'linux/arm64']] = Field(min_length=1)
    port: int = Field(ge=1024, le=65535)
    health_path: Route
    uid: int = Field(ge=1, le=65535)
    memory_mb: int = Field(ge=64, le=16384)
    cpus: float = Field(gt=0, le=16)
    volumes: list[Volume] = Field(default_factory=list, max_length=8)

class Launch(Strict):
    service: Id
    path: Route
    mode: Literal['window']

class Permissions(Strict):
    documents: Literal['none', 'user-selected-read', 'user-selected-read-write']
    network: Literal['none', 'internet']
    children: bool = False

class Manifest(Strict):
    manifest_version: Literal[1]
    id: Id
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=500)
    version: Annotated[str, Field(pattern=r'^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$')]
    sdk_version: Literal['1']
    icon: Annotated[str, Field(pattern=r'^assets/[a-zA-Z0-9_-]+\.png$')]
    category: Literal['documents', 'spreadsheets', 'presentations', 'communication', 'browser', 'other']
    services: dict[Id, Service] = Field(min_length=1, max_length=8)
    launch: Launch
    permissions: Permissions
    authentication: Literal['familos-gateway']
    data_version: int = Field(ge=1)

    @model_validator(mode='after')
    def references(self):
        if self.launch.service not in self.services:
            raise ValueError('launch.service must reference a declared service')
        if any(len('app-'+self.id+'-'+name)>63 for name in self.services):
            raise ValueError('Combined app and service name exceeds 63 characters')
        names = [v.name for s in self.services.values() for v in s.volumes]
        if len(set(names)) != len(names):
            raise ValueError('volume names must be unique across services')
        return self

if __name__ == '__main__':
    try:
        if len(sys.argv) != 2:
            raise ValueError('Usage: python manifest.py <manifest.json> | --schema')
        if sys.argv[1] == '--schema':
            print(json.dumps(Manifest.model_json_schema(), indent=2))
        else:
            raw = Path(sys.argv[1]).read_bytes()
            if len(raw) > 65536:
                raise ValueError('Manifest exceeds 64 KiB')
            app = Manifest.model_validate_json(raw)
            print(f'Valid manifest: {app.id} {app.version} (runtime not tested)')
    except (ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)

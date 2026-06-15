from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ModelEntry:
    name: str
    type: str
    target: str
    source_url: str
    sha256: str
    size_bytes: int
    required: bool
    required_manual: bool
    license: str
    note: str = ""

    @property
    def is_downloadable(self) -> bool:
        return (
            not self.required_manual
            and self.source_url not in ("", "TO_BE_FILLED")
            and self.sha256 not in ("", "TO_BE_FILLED")
        )


@dataclass
class CustomNodeEntry:
    name: str
    repo: str
    commit: str
    zip_url: str
    required_classes: list[str]
    has_requirements: bool
    phase: str = ""
    note: str = ""


@dataclass
class RuntimeManifest:
    schema_version: int
    python_version: str
    venv_path: str
    uv_version: str
    uv_windows_url: str
    uv_sha256: str
    comfyui_commit: str
    comfyui_zip_url: str
    torch_version: str
    torchvision_version: str
    torchaudio_version: str
    torch_cuda_variant: str
    torch_index_url: str

    @classmethod
    def from_dict(cls, d: dict) -> "RuntimeManifest":
        torch_block = d["torch"]
        return cls(
            schema_version=d["schema_version"],
            python_version=d["python"]["version"],
            venv_path=d["python"]["venv_path"],
            uv_version=d["uv"]["version"],
            uv_windows_url=d["uv"]["windows_url"],
            uv_sha256=d["uv"].get("sha256", ""),
            comfyui_commit=d["comfyui"]["commit"],
            comfyui_zip_url=d["comfyui"]["zip_url"],
            torch_version=torch_block["version"],
            torchvision_version=torch_block.get("torchvision_version", torch_block["version"]),
            torchaudio_version=torch_block.get("torchaudio_version", torch_block["version"]),
            torch_cuda_variant=torch_block["cuda_variant"],
            torch_index_url=torch_block["install_index_url"],
        )


@dataclass
class ModelManifest:
    schema_version: int
    models: list[ModelEntry]

    @classmethod
    def from_dict(cls, d: dict) -> "ModelManifest":
        models = [
            ModelEntry(
                name=m["name"],
                type=m["type"],
                target=m["target"],
                source_url=m.get("source_url", ""),
                sha256=m.get("sha256", ""),
                size_bytes=m.get("size_bytes", 0),
                required=m.get("required", True),
                required_manual=m.get("required_manual", False),
                license=m.get("license", ""),
                note=m.get("note", ""),
            )
            for m in d["models"]
        ]
        return cls(schema_version=d["schema_version"], models=models)


@dataclass
class CustomNodesManifest:
    schema_version: int
    custom_nodes: list[CustomNodeEntry]

    @classmethod
    def from_dict(cls, d: dict) -> "CustomNodesManifest":
        nodes = [
            CustomNodeEntry(
                name=n["name"],
                repo=n["repo"],
                commit=n["commit"],
                zip_url=n.get("zip_url", ""),
                required_classes=n.get("required_classes", []),
                has_requirements=n.get("has_requirements", False),
                phase=n.get("phase", ""),
                note=n.get("note", ""),
            )
            for n in d["custom_nodes"]
        ]
        return cls(schema_version=d["schema_version"], custom_nodes=nodes)

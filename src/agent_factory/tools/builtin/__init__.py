"""Built-in tools shipped with the platform."""

from collections.abc import Callable

from ..base import Tool
from .actions import DeleteDataset, RestartPipeline, UpdatePipelineConfig
from .code import ListCodeFiles, ReadCodeFile, ScanCodePatterns
from .compute import ComputeStatistics
from .documents import ListDocuments, ReadDocument, SearchDocuments
from .email import FetchRecentEmails, NotifyDesktop, SaveDigestFile, SendDigestToSelf
from .http import HttpGet
from .pipeline import InspectExecutionMetadata, ListPipelineExecutions, ReadPipelineLogs
from .sandbox_exec import PythonSandboxExec

BUILTIN_TOOLS: list[Callable[[], Tool]] = [
    ReadPipelineLogs,
    InspectExecutionMetadata,
    ListPipelineExecutions,
    SearchDocuments,
    ReadDocument,
    ListDocuments,
    ListCodeFiles,
    ReadCodeFile,
    ScanCodePatterns,
    ComputeStatistics,
    HttpGet,
    RestartPipeline,
    UpdatePipelineConfig,
    DeleteDataset,
    PythonSandboxExec,
    FetchRecentEmails,
    SaveDigestFile,
    SendDigestToSelf,
    NotifyDesktop,
]

__all__ = ["BUILTIN_TOOLS"]

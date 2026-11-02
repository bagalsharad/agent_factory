"""Built-in tools shipped with the platform."""

from collections.abc import Callable

from ..base import Tool
from .actions import DeleteDataset, RestartPipeline, UpdatePipelineConfig
from .code import ListCodeFiles, ReadCodeFile, ScanCodePatterns
from .compute import ComputeStatistics
from .documents import ListDocuments, ReadDocument, SearchDocuments
from .email import FetchRecentEmails, NotifyDesktop, SaveDigestFile, SendDigestToSelf
from .http import HttpGet
from .interview import LoadInterviewMaterials, SaveInterviewPrep
from .pipeline import InspectExecutionMetadata, ListPipelineExecutions, ReadPipelineLogs
from .sandbox_exec import PythonSandboxExec
from .video import GetVideoTranscript, GetVideoTranscripts, SaveVideoNotes

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
    GetVideoTranscript,
    SaveVideoNotes,
    GetVideoTranscripts,
    LoadInterviewMaterials,
    SaveInterviewPrep,
]

__all__ = ["BUILTIN_TOOLS"]

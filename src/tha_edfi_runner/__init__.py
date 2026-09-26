"""tha-edfi-runner: typed Ed-Fi ODS API client with pipeline-friendly row processing."""

from importlib.metadata import version

from tha_edfi_runner.base import ThaEdfiBase, resolve_api_spec_segment
from tha_edfi_runner.errors import EdfiError
from tha_edfi_runner.resources.student_assessment.runner import ThaStudentAssessment

__version__ = version("tha-edfi-runner")
__all__ = [
    "EdfiError",
    "ThaEdfiBase",
    "ThaStudentAssessment",
    "resolve_api_spec_segment",
]

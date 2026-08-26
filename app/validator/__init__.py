"""验收模块初始化。"""
from app.validator.validator import (
    SpotCheckResult,
    ValidationReport,
    run_validation,
)

__all__ = ["SpotCheckResult", "ValidationReport", "run_validation"]

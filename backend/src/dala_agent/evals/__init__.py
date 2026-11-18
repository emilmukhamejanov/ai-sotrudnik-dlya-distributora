"""Evals: эталонные вопросы, автоматические проверки и отчёт."""

from .checks import check_turn, numbers_in_text
from .runner import load_cases, run_evals, save_report, to_markdown

__all__ = ["check_turn", "load_cases", "numbers_in_text", "run_evals", "save_report", "to_markdown"]

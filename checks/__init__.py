from dataclasses import dataclass


@dataclass
class CheckResult:
    triggered: bool
    subject_tag: str
    rows_html: str
    plain: str
    chart_png: bytes | None = None

from datetime import date

from app.rules.models import Category, RuleDefinition, RuleSource, Severity

COMMON = "인용 및 참고문헌의 기술요소와 형식에 관한 공통기준"
COMMON_VERSION = "2024-06-17 개정"
VERIFIED_AT = date(2026, 8, 22)


def _common_source(clause: str, *, verified: bool = True) -> RuleSource:
    return RuleSource(
        document_name=COMMON,
        version_or_published_at=COMMON_VERSION,
        clause_number=clause,
        section_title=clause,
        verified_at=VERIFIED_AT,
        verified=verified,
    )


APA_SUBTITLE_SOURCE = RuleSource(
    document_name="Publication Manual of the American Psychological Association",
    version_or_published_at="7th ed.",
    clause_number="§6.17",
    section_title="Capitalization in titles and headings",
    verified_at=VERIFIED_AT,
    verified=True,
)


RULES: dict[str, RuleDefinition] = {
    "CR-01": RuleDefinition(
        rule_id="CR-01",
        category=Category.MISSING,
        severity=Severity.ERROR,
        source=_common_source("Ⅱ-1)-(1)"),
        memo_template="본문 인용에 대응하는 참고문헌을 추가해 주세요.",
        deterministic=True,
    ),
    "CR-02": RuleDefinition(
        rule_id="CR-02",
        category=Category.MISSING,
        severity=Severity.WARNING,
        source=_common_source("Ⅱ-1)-(1)"),
        memo_template=(
            "이 참고문헌의 본문 인용 위치를 확인해 주세요. "
            "인용하지 않았다면 목록에서 제외해 주세요."
        ),
        deterministic=True,
    ),
    "CR-03": RuleDefinition(
        rule_id="CR-03",
        category=Category.MISMATCH,
        severity=Severity.ERROR,
        source=_common_source("Ⅱ-1)-(1)"),
        memo_template="본문 인용과 참고문헌의 저자명·연도·제목이 일치하도록 수정해 주세요.",
        deterministic=False,
    ),
    "CR-04": RuleDefinition(
        rule_id="CR-04",
        category=Category.FORMAT,
        severity=Severity.ERROR,
        source=_common_source("Ⅰ-9)"),  # noqa: RUF001 - official clause notation
        memo_template="서로 다른 저자의 복합 인용을 참고문헌 배열순으로 정렬해 주세요.",
        deterministic=True,
    ),
    "CR-05": RuleDefinition(
        rule_id="CR-05",
        category=Category.FORMAT,
        severity=Severity.ERROR,
        source=_common_source("Ⅰ-7)"),  # noqa: RUF001 - official clause notation
        memo_template="동일 저자의 복합 인용을 연대순으로 정렬해 주세요.",
        deterministic=True,
    ),
    "CR-06": RuleDefinition(
        rule_id="CR-06",
        category=Category.MISSING,
        severity=Severity.WARNING,
        source=_common_source("Ⅱ-9)", verified=False),
        memo_template=(
            "학회 투고규정의 병기 의무를 확인하고 "
            "국문 참고문헌의 영문화 목록을 검토해 주세요."
        ),
        deterministic=True,
    ),
    "CR-07": RuleDefinition(
        rule_id="CR-07",
        category=Category.FORMAT,
        severity=Severity.ERROR,
        source=_common_source("Ⅱ-9) + Ⅱ-10)"),
        memo_template=(
            "한국 저자 이름을 전체 영문명으로 표기하고 영문명 알파벳순으로 재배열해 주세요."
        ),
        deterministic=True,
    ),
    "CR-08": RuleDefinition(
        rule_id="CR-08",
        category=Category.FORMAT,
        severity=Severity.ERROR,
        source=_common_source("Ⅱ-1)-(5)"),
        memo_template=(
            "연속간행물 논문명은 문장식 대문자 표기를, "
            "단행본 서명과 간행물명은 제목식 대문자 표기를 적용해 주세요."
        ),
        deterministic=True,
    ),
    "CR-09": RuleDefinition(
        rule_id="CR-09",
        category=Category.FORMAT,
        severity=Severity.ERROR,
        source=_common_source("Ⅱ-6)-(1)"),
        memo_template="출처 표기를 '출처: URL' 형식으로 수정해 주세요.",
        deterministic=True,
    ),
    "CR-10": RuleDefinition(
        rule_id="CR-10",
        category=Category.MISSING,
        severity=Severity.WARNING,
        source=_common_source("Ⅱ-3)-(1)"),
        memo_template="자료유형을 확인하고 필요한 경우 DOI를 추가해 주세요.",
        deterministic=False,
    ),
    "CR-11": RuleDefinition(
        rule_id="CR-11",
        category=Category.FORMAT,
        severity=Severity.ERROR,
        source=_common_source("Ⅱ-1)-(4)"),
        memo_template="서양 참고문헌의 마지막 저자 앞에 앤드기호(&)를 표기해 주세요.",
        deterministic=True,
    ),
    "CR-12": RuleDefinition(
        rule_id="CR-12",
        category=Category.NORMAL,
        severity=Severity.INFO,
        source=RuleSource(
            document_name="적용된 규칙 집합",
            version_or_published_at=COMMON_VERSION,
            clause_number="해당 없음",
            section_title="활성 규칙",
            verified_at=VERIFIED_AT,
            verified=True,
        ),
        memo_template="수정 요청 없음",
        deterministic=True,
    ),
}

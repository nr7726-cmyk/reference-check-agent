from app.rules.models import Category, RuleDefinition, RuleSource, Severity

COMMON = "문편협 공통기준"
SOCIETY = "한국도서관·정보학회 투고규정"
APA = "APA 7"
PLACEHOLDER = "{공식 위치}"


def _source(document_name: str, version: str) -> RuleSource:
    return RuleSource(
        document_name=document_name,
        version_or_published_at=version,
        section_title=PLACEHOLDER,
        verified=False,
    )


RULES: dict[str, RuleDefinition] = {
    "CR-01": RuleDefinition(
        rule_id="CR-01",
        category=Category.MISSING,
        severity=Severity.ERROR,
        source=_source(COMMON, "2024-06-17"),
        memo_template="본문 인용에 대응하는 참고문헌을 추가해 주세요.",
        deterministic=True,
    ),
    "CR-02": RuleDefinition(
        rule_id="CR-02",
        category=Category.MISSING,
        severity=Severity.WARNING,
        source=_source(COMMON, "2024-06-17"),
        memo_template="이 참고문헌의 본문 인용 위치를 확인해 주세요.",
        deterministic=True,
    ),
    "CR-03": RuleDefinition(
        rule_id="CR-03",
        category=Category.MISMATCH,
        severity=Severity.ERROR,
        source=_source(COMMON, "2024-06-17"),
        memo_template="본문 인용과 참고문헌의 저자명·연도·제목을 일치시켜 주세요.",
        deterministic=False,
    ),
    "CR-04": RuleDefinition(
        rule_id="CR-04",
        category=Category.FORMAT,
        severity=Severity.ERROR,
        source=_source(COMMON, "2024-06-17"),
        memo_template="서로 다른 저자의 복합 인용을 참고문헌 배열순으로 정렬해 주세요.",
        deterministic=True,
    ),
    "CR-05": RuleDefinition(
        rule_id="CR-05",
        category=Category.FORMAT,
        severity=Severity.ERROR,
        source=_source(COMMON, "2024-06-17"),
        memo_template="동일 저자의 복합 인용을 연대순으로 정렬해 주세요.",
        deterministic=True,
    ),
    "CR-06": RuleDefinition(
        rule_id="CR-06",
        category=Category.MISSING,
        severity=Severity.ERROR,
        source=_source(SOCIETY, "latest"),
        memo_template="국문 참고문헌에 대응하는 영문화 참고문헌을 함께 표기해 주세요.",
        deterministic=True,
    ),
    "CR-07": RuleDefinition(
        rule_id="CR-07",
        category=Category.FORMAT,
        severity=Severity.ERROR,
        source=_source(SOCIETY, "latest"),
        memo_template=(
            "한국 저자 이름을 전체 영문명으로 표기하고 영문명 알파벳순으로 재배열해 주세요."
        ),
        deterministic=True,
    ),
    "CR-08": RuleDefinition(
        rule_id="CR-08",
        category=Category.FORMAT,
        severity=Severity.ERROR,
        source=_source(APA, "7"),
        memo_template="콜론 뒤 부제의 첫 단어를 대문자로 시작해 주세요.",
        deterministic=True,
    ),
    "CR-09": RuleDefinition(
        rule_id="CR-09",
        category=Category.FORMAT,
        severity=Severity.ERROR,
        source=_source(COMMON, "2024-06-17"),
        memo_template="출처 표기를 '출처: URL' 형식으로 수정해 주세요.",
        deterministic=True,
    ),
    "CR-10": RuleDefinition(
        rule_id="CR-10",
        category=Category.MISSING,
        severity=Severity.WARNING,
        source=_source(COMMON, "2024-06-17"),
        memo_template="자료유형을 확인하고 필요한 경우 DOI를 추가해 주세요.",
        deterministic=False,
    ),
    "CR-11": RuleDefinition(
        rule_id="CR-11",
        category=Category.NEEDS_REVIEW,
        severity=Severity.NEEDS_REVIEW,
        source=_source(COMMON, "2024-06-17"),
        memo_template="저자 연결 표현은 문맥별 규칙에 따라 확인해 주세요.",
        deterministic=False,
    ),
    "CR-12": RuleDefinition(
        rule_id="CR-12",
        category=Category.NORMAL,
        severity=Severity.INFO,
        source=_source(COMMON, "2024-06-17"),
        memo_template="수정 요청 없음",
        deterministic=True,
    ),
}

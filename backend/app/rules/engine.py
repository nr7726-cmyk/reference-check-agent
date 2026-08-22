from __future__ import annotations

import re
from collections import defaultdict
from itertools import pairwise
from typing import Iterable

from app.extraction.models import Citation, Location, ParsedManuscript, ReferenceItem
from app.rules.models import (
    Category,
    CheckResult,
    ResultStatus,
    RuleDefinition,
    RuleSource,
    Severity,
)
from app.rules.normalization import normalize_name
from app.rules.registry import APA_SUBTITLE_SOURCE, RULES

INITIAL_PATTERN = re.compile(r"\b[A-Z]\.")
WORD = re.compile(r"[A-Za-z]+")
WESTERN_AND = re.compile(r"\band\b", re.IGNORECASE)
BAD_SOURCE_SPACING = re.compile(r"출처\s+:")
SUBTITLE_LOWERCASE = re.compile(r":\s*([a-z])")
KOREAN_COAUTHOR_SEPARATOR = re.compile(
    r"(?P<first>[가-힣]{2,4})\s*(?P<separator>[.·\u2024ㆍ/])\s*(?P<second>[가-힣]{2,4})"
)
KOREAN_COAUTHOR_AND = re.compile(
    r"(?P<first>[가-힣]{2,4})\s+and\s+(?P<second>[가-힣]{2,4})",
    re.IGNORECASE,
)
KOREAN_AUTHOR_PERIOD = re.compile(
    r"^(?P<authors>[가-힣]{2,4}(?:\s*,\s*[가-힣]{2,4})*)\.\s+"
    r"(?P<year>\(?(?:19|20)\d{2}[a-z]?\)?)"
)
YEAR_SUFFIX = re.compile(r"[\(\[](?:19|20)\d{2}(?P<suffix>[a-z]?)[\)\]]")
JOURNAL_DETAILS = re.compile(
    r",\s*\d+(?:\(\d+\))?,\s*(?:\d+[-\u2013]\d+|[A-Za-z]?\d+)"
)
LOWERCASE_TITLE_WORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "but",
    "by",
    "for",
    "in",
    "nor",
    "of",
    "on",
    "or",
    "the",
    "to",
}


class DeterministicRuleEngine:
    def __init__(
        self,
        reference_order_summary_threshold: int = 5,
        citation_missing_summary_ratio: float = 0.2,
        citation_missing_summary_minimum: int = 5,
        review_repeat_summary_threshold: int = 5,
    ) -> None:
        self.reference_order_summary_threshold = max(1, reference_order_summary_threshold)
        self.citation_missing_summary_ratio = min(
            1.0, max(0.0, citation_missing_summary_ratio)
        )
        self.citation_missing_summary_minimum = max(1, citation_missing_summary_minimum)
        self.review_repeat_summary_threshold = max(1, review_repeat_summary_threshold)

    def evaluate(self, manuscript: ParsedManuscript) -> list[CheckResult]:
        results: list[CheckResult] = []
        if not manuscript.body_text_sufficient:
            results.append(
                self._result(
                    "CR-03",
                    manuscript.document.paragraphs[0].location,
                    "논문 본문을 충분히 추출하지 못했습니다",
                    status=ResultStatus.NEEDS_CONTEXT,
                    memo_template=(
                        "논문 본문 추출 불충분. 표·도형 중심 문서 또는 "
                        "표지·양식 파일 여부 확인 요"
                    ),
                )
            )
            return results
        if manuscript.reference_section_method == "heading":
            results.extend(self._citation_reference_checks(manuscript))
        else:
            location = (
                manuscript.citations[0].location
                if manuscript.citations
                else manuscript.document.paragraphs[-1].location
            )
            results.append(
                self._result(
                    "CR-03",
                    location,
                    (
                        "참고문헌 목록 경계를 확실하게 찾지 못했습니다. "
                        f"본문 인용 {len(manuscript.citations)}건만 추출했습니다"
                    ),
                    status=ResultStatus.NEEDS_CONTEXT,
                    memo_template=(
                        "참고문헌 목록 위치와 형식 확인 요. 확실한 경계 미검출로 "
                        "본문 인용과의 왕복 대조 생략"
                    ),
                )
            )
        results.extend(self._compound_order_checks(manuscript))
        results.extend(self._citation_author_separator_checks(manuscript))
        results.extend(self._reference_order_checks(manuscript))
        results.extend(self._english_conversion_checks(manuscript))
        results.extend(self._format_checks(manuscript))
        if not results:
            location = manuscript.document.paragraphs[0].location
            results.append(self._result("CR-12", location, "활성화된 결정론적 검사를 통과했습니다"))
        results = _summarize_repeated_review_results(
            results,
            self.review_repeat_summary_threshold,
        )
        return sorted(results, key=lambda result: result.sort_key)

    def _citation_reference_checks(self, manuscript: ParsedManuscript) -> Iterable[CheckResult]:
        cited_pairs = {
            (normalize_name(author), mention.year)
            for citation in manuscript.citations
            for mention in citation.mentions
            for author in [mention.author, *mention.coauthors]
        }
        primary_reference_pairs = {
            (normalize_name(reference.authors[0].raw), reference.year)
            for reference in manuscript.references
            if reference.authors and reference.year
        }
        all_reference_pairs = {
            (normalize_name(author.raw), reference.year)
            for reference in manuscript.references
            if reference.year
            for author in reference.authors
        }
        reference_authors = {author for author, _ in all_reference_pairs}

        unmatched: list[tuple[Citation, str]] = []
        total_mentions = 0
        for citation in manuscript.citations:
            for mention in citation.mentions:
                total_mentions += 1
                candidate_pairs = {
                    (normalize_name(author), mention.year)
                    for author in [mention.author, *mention.coauthors]
                }
                primary_pair = (normalize_name(mention.author), mention.year)
                if (
                    primary_pair not in primary_reference_pairs
                    and candidate_pairs.isdisjoint(all_reference_pairs)
                ):
                    rule_id = (
                        "CR-03"
                        if any(pair[0] in reference_authors for pair in candidate_pairs)
                        else "CR-01"
                    )
                    unmatched.append((citation, rule_id))

        if (
            len(unmatched) >= self.citation_missing_summary_minimum
            and total_mentions
            and len(unmatched) / total_mentions >= self.citation_missing_summary_ratio
        ):
            yield self._result(
                "CR-03",
                unmatched[0][0].location,
                (
                    f"본문 인용 {total_mentions}건 중 {len(unmatched)}건의 대응을 "
                    "확정하지 못해 개별 누락 판정을 생략했습니다"
                ),
                status=ResultStatus.NEEDS_CONTEXT,
                memo_template=(
                    "참고문헌 추출 또는 저자 표기 우선 확인 요. 대응 실패 "
                    "비율이 높아 개별 누락 요청 생성 생략"
                ),
            )
            return

        for citation, rule_id in unmatched:
            status = (
                ResultStatus.NEEDS_CONTEXT
                if rule_id == "CR-03"
                else ResultStatus.DETECTED
            )
            yield self._result(
                rule_id,
                citation.location,
                f"본문 인용 '{citation.raw_text}'의 대응 참고문헌을 확정할 수 없습니다",
                status=status,
            )

        unmatched_references: list[ReferenceItem] = []
        for reference in manuscript.references:
            if not reference.authors or not reference.year:
                continue
            pair = (normalize_name(reference.authors[0].raw), reference.year)
            if pair not in cited_pairs:
                unmatched_references.append(reference)
        if (
            len(unmatched_references) >= self.citation_missing_summary_minimum
            and manuscript.references
            and len(unmatched_references) / len(manuscript.references)
            >= self.citation_missing_summary_ratio
        ):
            yield self._result(
                "CR-03",
                unmatched_references[0].location,
                (
                    f"참고문헌 {len(manuscript.references)}건 중 "
                    f"{len(unmatched_references)}건의 본문 인용을 찾지 못해 "
                    "개별 제외 판정을 생략했습니다"
                ),
                status=ResultStatus.NEEDS_CONTEXT,
                memo_template=(
                    "본문 인용 추출 또는 저자 표기 우선 확인 요. 대응 실패 "
                    "비율이 높아 개별 참고문헌 제외 요청 생성 생략"
                ),
            )
            return
        for reference in unmatched_references:
            yield self._result(
                "CR-02",
                reference.location,
                "참고문헌에만 있고 본문 인용에서 찾지 못했습니다",
            )

    def _compound_order_checks(self, manuscript: ParsedManuscript) -> Iterable[CheckResult]:
        order = {
            normalize_name(reference.authors[0].raw): reference.reference_index
            for reference in manuscript.references
            if reference.authors
        }
        for citation in manuscript.citations:
            if len(citation.mentions) < 2:
                continue
            grouped: dict[str, list[int]] = defaultdict(list)
            for mention in citation.mentions:
                grouped[normalize_name(mention.author)].append(mention.year)
            if len(grouped) == 1:
                years = next(iter(grouped.values()))
                if years != sorted(years):
                    yield self._result(
                        "CR-05", citation.location, "동일 저자의 복합 인용이 연대순이 아닙니다"
                    )
            else:
                actual = [
                    order.get(normalize_name(item.author), 10**9) for item in citation.mentions
                ]
                if actual != sorted(actual):
                    yield self._result(
                        "CR-04",
                        citation.location,
                        "서로 다른 저자의 복합 인용이 참고문헌 배열순이 아닙니다",
                    )

    def _english_conversion_checks(self, manuscript: ParsedManuscript) -> Iterable[CheckResult]:
        korean = [item for item in manuscript.references if item.list_kind == "korean"]
        english = [item for item in manuscript.references if item.list_kind == "english"]
        if korean and not english:
            yield self._result(
                "CR-06", korean[0].location, "국문 참고문헌의 영문화 목록이 없습니다"
            )
            return
        if not english:
            return
        english_years = {item.year for item in english if item.year is not None}
        for reference in korean:
            if reference.year not in english_years:
                yield self._result(
                    "CR-06",
                    reference.location,
                    "이 국문 참고문헌에 대응하는 영문화 항목을 확정할 수 없습니다",
                    status=ResultStatus.NEEDS_CONTEXT,
                )
        converted = [
            reference
            for reference in english
            if korean and reference.year in {item.year for item in korean}
        ]
        for reference in converted:
            if any(INITIAL_PATTERN.fullmatch(author.raw.strip()) for author in reference.authors):
                yield self._result(
                    "CR-07",
                    reference.location,
                    "한국 저자 이름이 전체 영문명이 아닌 이니셜로 보입니다",
                )
        names = [
            normalize_name(reference.authors[0].raw) for reference in english if reference.authors
        ]
        if names != sorted(names):
            yield self._result(
                "CR-07", english[0].location, "영문화 참고문헌이 영문명 알파벳순이 아닙니다"
            )

    def _citation_author_separator_checks(
        self, manuscript: ParsedManuscript
    ) -> Iterable[CheckResult]:
        for citation in manuscript.citations:
            inner = (
                citation.raw_text[1:-1]
                if citation.raw_text.startswith("(")
                else citation.raw_text
            )
            match = KOREAN_COAUTHOR_SEPARATOR.search(inner)
            if match is None:
                semicolon = _misused_coauthor_semicolon(inner)
                match = semicolon or KOREAN_COAUTHOR_AND.search(inner)
            if match is None:
                continue
            corrected = (
                citation.raw_text[: match.start() + 1]
                + f"{match.group('first')}, {match.group('second')}"
                + citation.raw_text[match.end() + 1 :]
            )
            yield self._result(
                "CR-13",
                citation.location,
                f"본문 인용 {citation.raw_text}에서 공저자 구분자가 잘못되었습니다",
                memo_template=(
                    "공저자 구분자 오류, 쉼표(, ) 사용 필요\n"
                    f"수정 예: {corrected}"
                ),
            )

    def _reference_order_checks(
        self, manuscript: ParsedManuscript
    ) -> Iterable[CheckResult]:
        if manuscript.reference_section_method != "heading":
            return []

        violations: list[CheckResult] = []
        uncertain: list[CheckResult] = []
        references = manuscript.references
        ranks = {"korean": 0, "english": 1}
        for previous, current in pairwise(references):
            if ranks[current.list_kind] < ranks[previous.list_kind]:
                violations.append(
                    self._result(
                        "CR-15",
                        current.location,
                        "국내문헌이 서양문헌 뒤에 배치되어 있습니다",
                        memo_template=(
                            "국내문헌 → 서양문헌 → 동양문헌 순 배열 필요"
                        ),
                    )
                )

        for reference in references:
            if (
                reference.list_kind == "korean"
                and reference.authors
                and not _is_hangul_name(reference.authors[0].raw)
            ):
                uncertain.append(
                    self._result(
                        "CR-19",
                        reference.location,
                        "저자 표기만으로 문헌 자료군을 확정할 수 없습니다",
                        status=ResultStatus.NEEDS_CONTEXT,
                    )
                )

        for start, end in _reference_runs(references):
            run = references[start:end]
            if not run:
                continue
            for previous, current in pairwise(run):
                previous_name = _first_author(previous)
                current_name = _first_author(current)
                if previous_name is None or current_name is None:
                    continue
                if previous_name == current_name:
                    violations.extend(self._same_author_order_violations(previous, current))
                    continue
                if (
                    current.list_kind == "korean"
                    and _is_hangul_name(previous.authors[0].raw)
                    and _is_hangul_name(current.authors[0].raw)
                    and current_name < previous_name
                ):
                    violations.append(
                        self._result(
                            "CR-16",
                            current.location,
                            (
                                f"저자명 가나다순에서 참고문헌 "
                                f"{previous.reference_index + 1}번째 항목보다 앞서야 합니다"
                            ),
                        )
                    )

        if len(violations) > self.reference_order_summary_threshold:
            first = violations[0]
            source = RULES["CR-15"].source.model_copy(
                update={
                    "clause_number": "Ⅱ-1)-(1), Ⅱ-1)-(2), Ⅱ-3)-(2)",
                    "section_title": "참고문헌 배열 순서",
                }
            )
            summary = self._result(
                "CR-15",
                first.location,
                f"참고문헌 배열 위반 {len(violations)}건을 확인했습니다",
                source=source,
                memo_template=(
                    "참고문헌 목록 전체 배열 순서 재검토 필요. "
                    "자료군 순서, 저자명 가나다순(알파벳순), 동일 저자 연대순 "
                    "적용 필요"
                ),
            )
            return [
                summary,
                *_summarize_uncertain(
                    uncertain,
                    self,
                    threshold=self.reference_order_summary_threshold,
                ),
            ]
        uncertain = _summarize_uncertain(
            uncertain,
            self,
            threshold=self.reference_order_summary_threshold,
        )
        return [*violations, *uncertain]

    def _same_author_order_violations(
        self,
        previous: ReferenceItem,
        current: ReferenceItem,
    ) -> list[CheckResult]:
        if previous.year is None or current.year is None:
            return []
        if current.year < previous.year:
            return [
                self._result(
                    "CR-17",
                    current.location,
                    (
                        f"동일 저자 문헌이 참고문헌 {previous.reference_index + 1}번째 "
                        "항목보다 오래된 출판연도인데 뒤에 배치되어 있습니다"
                    ),
                )
            ]
        if current.year != previous.year:
            return []
        previous_suffix = _year_suffix(previous.raw_text)
        current_suffix = _year_suffix(current.raw_text)
        if (
            len(previous_suffix) == 1
            and len(current_suffix) == 1
            and ord(current_suffix) == ord(previous_suffix) + 1
        ):
            return []
        return [
            self._result(
                "CR-18",
                current.location,
                "동일 저자·동일 발행년 문헌의 a, b, c 구분이 없거나 순서가 잘못되었습니다",
            )
        ]

    def _format_checks(self, manuscript: ParsedManuscript) -> Iterable[CheckResult]:
        for reference in manuscript.references:
            author_period = KOREAN_AUTHOR_PERIOD.search(reference.raw_text)
            if reference.list_kind == "korean" and author_period:
                clause = _reference_format_clause(reference)
                source = RULES["CR-14"].source.model_copy(
                    update={"clause_number": clause, "section_title": clause}
                )
                year = author_period.group("year").strip("()")
                corrected = f"{author_period.group('authors')} ({year})."
                yield self._result(
                    "CR-14",
                    reference.location,
                    "국문 저자명 뒤에 불필요한 온점이 있습니다",
                    source=source,
                    memo_template=(
                        "저자명 뒤 온점 삭제 필요\n"
                        f"수정 예: {corrected}"
                    ),
                )
            capitalization_finding = _capitalization_finding(reference)
            if capitalization_finding:
                yield self._result("CR-08", reference.location, capitalization_finding)
            if reference.title and SUBTITLE_LOWERCASE.search(reference.title):
                yield self._result(
                    "CR-08",
                    reference.location,
                    "콜론 뒤 부제의 첫 단어가 소문자로 시작합니다",
                    source=APA_SUBTITLE_SOURCE,
                    severity=Severity.WARNING,
                    memo_template="콜론 뒤 부제 첫 단어 대문자 시작 필요",
                )
            if BAD_SOURCE_SPACING.search(reference.raw_text):
                yield self._result("CR-09", reference.location, "출처의 콜론 앞에 공백이 있습니다")
            if _is_journal_article(reference) and reference.doi is None:
                yield self._result(
                    "CR-10",
                    reference.location,
                    "DOI 필요 여부를 자료유형에 따라 확인해야 합니다",
                    status=ResultStatus.NEEDS_CONTEXT,
                )
            author_text = reference.raw_text.split("(", 1)[0]
            if reference.list_kind == "english" and WESTERN_AND.search(author_text):
                yield self._result(
                    "CR-11",
                    reference.location,
                    "서양 참고문헌의 마지막 저자가 앤드기호(&)로 연결되지 않았습니다",
                )

    def _result(
        self,
        rule_id: str,
        location: Location,
        finding: str,
        *,
        status: ResultStatus = ResultStatus.DETECTED,
        source: RuleSource | None = None,
        severity: Severity | None = None,
        memo_template: str | None = None,
    ) -> CheckResult:
        rule = RULES[rule_id]
        result_source = source or rule.source
        result_severity = severity or (
            rule.severity if result_source.verified else Severity.NEEDS_REVIEW
        )
        category = rule.category
        if not result_source.verified:
            status = ResultStatus.NEEDS_CONTEXT
            category = Category.NEEDS_REVIEW
        if status == ResultStatus.NEEDS_CONTEXT:
            result_severity = Severity.NEEDS_REVIEW
            if not rule.deterministic:
                category = Category.NEEDS_REVIEW
        memo = _memo_text(
            rule,
            location=location,
            source=result_source,
            memo_template=memo_template,
        )
        return CheckResult(
            id=f"{rule_id}-{location.id}",
            category=category,
            severity=result_severity,
            status=status,
            location=location,
            finding=finding,
            memo_text=memo,
            rule_id=rule_id,
            rule_source=result_source,
            confidence=1.0 if rule.deterministic else 0.5,
            sort_key=location.sort_key,
        )


def _memo_text(
    rule: RuleDefinition,
    *,
    location: Location,
    source: RuleSource | None = None,
    memo_template: str | None = None,
) -> str:
    source = source or rule.source
    locator = source.clause_number or (
        f"{source.page}쪽 {source.section_title}" if source.page else source.section_title
    )
    action = memo_template or rule.memo_template
    _validate_memo_action(action)
    return (
        f"{_human_location(location)}\n"
        f"{action}\n"
        f"(근거: {source.document_name} {locator})"
    )


def _validate_memo_action(action: str) -> None:
    prohibited = (
        "주세요",
        "바랍니다",
        "하십시오",
        "것 같",
        "보입니다",
        "제가",
        "저희",
        "우리 학회",
        "아쉽게도",
        "유감스럽게",
        "잘못된",
    )
    if any(expression in action for expression in prohibited):
        raise ValueError("memo action must use anonymous nominal style")
    primary_line = action.splitlines()[0]
    allowed_endings = (
        "필요",
        "요망",
        "불일치",
        "누락",
        "확인 요",
        "없음",
        "생략",
        "오류",
    )
    if primary_line.endswith(".") or not primary_line.endswith(allowed_endings):
        raise ValueError("memo action must end in nominal style without a period")


def _human_location(location: Location) -> str:
    if location.reference_index is not None:
        context = f" ({_short_context(location.display_hint)})" if location.display_hint else ""
        return f"참고문헌 {location.reference_index + 1}번째 항목{context}"
    if location.display_hint.startswith("본문 인용 "):
        return location.display_hint
    context = f" ({_short_context(location.display_hint)})" if location.display_hint else ""
    return f"본문 {location.paragraph_index + 1}번째 문단{context}"


def _short_context(text: str, limit: int = 48) -> str:
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _misused_coauthor_semicolon(text: str) -> re.Match[str] | None:
    pattern = re.compile(
        r"(?P<first>[가-힣]{2,4})\s*(?P<separator>;)\s*"
        r"(?P<second>[가-힣]{2,4})(?=\s*,\s*(?:19|20)\d{2})"
    )
    match = pattern.search(text)
    if match is None:
        return None
    before = text[: match.start()]
    return None if re.search(r"(?:19|20)\d{2}", before) else match


def _reference_format_clause(reference: ReferenceItem) -> str:
    if _is_journal_article(reference):
        return "Ⅱ-3)-(1)"
    if reference.url:
        return "Ⅱ-6)-(1)"
    if "학위" in reference.raw_text:
        return "Ⅱ-4)-(1)"
    return "Ⅱ-2)-(1)"


def _first_author(reference: ReferenceItem) -> str | None:
    if not reference.authors:
        return None
    return normalize_name(reference.authors[0].raw)


def _is_hangul_name(value: str) -> bool:
    return re.fullmatch(r"[가-힣]{2,}(?:\s*,\s*[가-힣]{2,})*", value.strip()) is not None


def _reference_runs(references: list[ReferenceItem]) -> list[tuple[int, int]]:
    if not references:
        return []
    runs: list[tuple[int, int]] = []
    start = 0
    for index in range(1, len(references)):
        if references[index].list_kind != references[start].list_kind:
            runs.append((start, index))
            start = index
    runs.append((start, len(references)))
    return runs


def _year_suffix(raw_text: str) -> str:
    match = YEAR_SUFFIX.search(raw_text)
    return match.group("suffix") if match else ""


def _summarize_uncertain(
    uncertain: list[CheckResult],
    engine: DeterministicRuleEngine,
    *,
    threshold: int = 5,
) -> list[CheckResult]:
    if len(uncertain) <= threshold:
        return uncertain
    first = uncertain[0]
    return [
        engine._result(
            "CR-19",
            first.location,
            f"자료군 구분을 확정할 수 없는 참고문헌이 {len(uncertain)}건입니다",
            status=ResultStatus.NEEDS_CONTEXT,
            memo_template=(
                "로마자 표기 한국 저자 문헌 포함 여부 확인 요. "
                "국내·서양·동양문헌 구간 구분 필요"
            ),
        )
    ]


def _summarize_repeated_review_results(
    results: list[CheckResult],
    threshold: int,
) -> list[CheckResult]:
    groups: dict[str, list[CheckResult]] = defaultdict(list)
    for result in results:
        if result.severity == Severity.NEEDS_REVIEW:
            groups[result.rule_id].append(result)
    repeated_ids = {
        rule_id for rule_id, items in groups.items() if len(items) > threshold
    }
    if not repeated_ids:
        return results

    summarized: list[CheckResult] = []
    emitted: set[str] = set()
    for result in results:
        if result.rule_id not in repeated_ids:
            summarized.append(result)
            continue
        if result.rule_id in emitted:
            continue
        emitted.add(result.rule_id)
        items = groups[result.rule_id]
        lines = result.memo_text.splitlines()
        action = lines[1] if len(lines) >= 3 else RULES[result.rule_id].memo_template
        evidence = lines[-1]
        summarized.append(
            result.model_copy(
                update={
                    "finding": (
                        f"동일한 확인 사유의 {result.rule_id} 항목 "
                        f"{len(items)}건을 목록 단위로 요약했습니다"
                    ),
                    "memo_text": (
                        "참고문헌 목록 전체\n"
                        f"{action} ({len(items)}건) 일괄 확인 요\n"
                        f"{evidence}"
                    ),
                }
            )
        )
    return summarized


def _is_journal_article(reference: ReferenceItem) -> bool:
    return _journal_title(reference) is not None


def _journal_title(reference: ReferenceItem) -> str | None:
    if not reference.title:
        return None
    remainder = reference.raw_text.split(reference.title, 1)[-1]
    details = JOURNAL_DETAILS.search(remainder)
    if details is None:
        return None
    return remainder[: details.start()].strip(" .") or None


def _capitalization_finding(reference: ReferenceItem) -> str | None:
    if reference.list_kind != "english" or not reference.title:
        return None
    words = WORD.findall(reference.title)
    if not words:
        return None
    if words[0][0].islower():
        return "영문 제목의 첫 단어가 소문자로 시작합니다"
    journal_title = _journal_title(reference)
    if journal_title:
        if _has_lowercase_title_word(journal_title):
            return "영문 연속간행물명이 제목식 대문자로 표기되지 않았습니다"
        return None
    if _has_lowercase_title_word(reference.title):
        return "영문 단행본 서명이 제목식 대문자로 표기되지 않았습니다"
    return None


def _has_lowercase_title_word(title: str) -> bool:
    words = WORD.findall(title)
    for word in words[1:]:
        if word.lower() not in LOWERCASE_TITLE_WORDS and word[0].islower():
            return True
    return bool(words and words[0][0].islower())

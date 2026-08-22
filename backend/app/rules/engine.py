from __future__ import annotations

import re
from collections import defaultdict
from typing import Iterable

from app.extraction.models import Location, ParsedManuscript
from app.rules.models import (
    Category,
    CheckResult,
    ResultStatus,
    RuleDefinition,
    Severity,
)
from app.rules.normalization import normalize_name
from app.rules.registry import RULES

INITIAL_PATTERN = re.compile(r"\b[A-Z]\.")
CONTEXT_CONNECTOR = re.compile(r"\b(?:and|&)\b|\s(?:와|과)\s", re.IGNORECASE)
BAD_SOURCE_SPACING = re.compile(r"출처\s+:")
SUBTITLE_LOWERCASE = re.compile(r":\s*([a-z])")


class DeterministicRuleEngine:
    def evaluate(self, manuscript: ParsedManuscript) -> list[CheckResult]:
        results: list[CheckResult] = []
        results.extend(self._citation_reference_checks(manuscript))
        results.extend(self._compound_order_checks(manuscript))
        results.extend(self._english_conversion_checks(manuscript))
        results.extend(self._format_checks(manuscript))
        if not results:
            location = manuscript.document.paragraphs[0].location
            results.append(self._result("CR-12", location, "활성화된 결정론적 검사를 통과했습니다"))
        return sorted(results, key=lambda result: result.sort_key)

    def _citation_reference_checks(self, manuscript: ParsedManuscript) -> Iterable[CheckResult]:
        cited_pairs = {
            (normalize_name(mention.author), mention.year)
            for citation in manuscript.citations
            for mention in citation.mentions
        }
        reference_pairs = {
            (normalize_name(reference.authors[0].raw), reference.year)
            for reference in manuscript.references
            if reference.authors and reference.year
        }
        reference_authors = {author for author, _ in reference_pairs}

        for citation in manuscript.citations:
            for mention in citation.mentions:
                pair = (normalize_name(mention.author), mention.year)
                if pair not in reference_pairs:
                    rule_id = "CR-03" if pair[0] in reference_authors else "CR-01"
                    status = (
                        ResultStatus.NEEDS_CONTEXT if rule_id == "CR-03" else ResultStatus.DETECTED
                    )
                    yield self._result(
                        rule_id,
                        citation.location,
                        f"본문 인용 '{citation.raw_text}'의 대응 참고문헌을 확정할 수 없습니다",
                        status=status,
                    )

        for reference in manuscript.references:
            if not reference.authors or not reference.year:
                continue
            pair = (normalize_name(reference.authors[0].raw), reference.year)
            if pair not in cited_pairs:
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
        for reference in english:
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

    def _format_checks(self, manuscript: ParsedManuscript) -> Iterable[CheckResult]:
        for reference in manuscript.references:
            if reference.title and SUBTITLE_LOWERCASE.search(reference.title):
                yield self._result(
                    "CR-08",
                    reference.location,
                    "콜론 뒤 부제의 첫 단어가 소문자로 시작합니다",
                )
            if BAD_SOURCE_SPACING.search(reference.raw_text):
                yield self._result("CR-09", reference.location, "출처의 콜론 앞에 공백이 있습니다")
            if reference.doi is None:
                yield self._result(
                    "CR-10",
                    reference.location,
                    "DOI 필요 여부를 자료유형에 따라 확인해야 합니다",
                    status=ResultStatus.NEEDS_CONTEXT,
                )
            if CONTEXT_CONNECTOR.search(reference.raw_text):
                yield self._result(
                    "CR-11",
                    reference.location,
                    "저자 연결 표현은 문맥 확인이 필요합니다",
                    status=ResultStatus.NEEDS_CONTEXT,
                )

    def _result(
        self,
        rule_id: str,
        location: Location,
        finding: str,
        *,
        status: ResultStatus = ResultStatus.DETECTED,
    ) -> CheckResult:
        rule = RULES[rule_id]
        severity = rule.effective_severity()
        category = rule.category
        if not rule.source.verified:
            status = ResultStatus.NEEDS_CONTEXT
            category = Category.NEEDS_REVIEW
        if status == ResultStatus.NEEDS_CONTEXT:
            severity = Severity.NEEDS_REVIEW
            if not rule.deterministic:
                category = Category.NEEDS_REVIEW
        memo = _memo_text(rule)
        return CheckResult(
            id=f"{rule_id}-{location.id}",
            category=category,
            severity=severity,
            status=status,
            location=location,
            finding=finding,
            memo_text=memo,
            rule_id=rule_id,
            rule_source=rule.source,
            confidence=1.0 if rule.deterministic else 0.5,
            sort_key=location.sort_key,
        )


def _memo_text(rule: RuleDefinition) -> str:
    source = rule.source
    locator = source.clause_number or (
        f"{source.page}쪽 {source.section_title}" if source.page else source.section_title
    )
    return f"{rule.memo_template} (근거: {source.document_name} {locator}, {rule.rule_id})"

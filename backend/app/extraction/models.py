from __future__ import annotations

from typing import Literal, Optional, Tuple

from pydantic import BaseModel, Field, model_validator


class Location(BaseModel):
    format: Literal["hwp", "hwpx"]
    section_label: str
    section_index: int = Field(ge=0)
    paragraph_index: int = Field(ge=0)
    run_index: Optional[int] = Field(default=None, ge=0)
    reference_index: Optional[int] = Field(default=None, ge=0)
    display_hint: str = Field(default="", max_length=80)

    @property
    def id(self) -> str:
        if self.reference_index is not None:
            return (
                f"loc:{self.format}:ref{self.reference_index}:"
                f"s{self.section_index}:p{self.paragraph_index}"
            )
        run = self.run_index if self.run_index is not None else 0
        return f"loc:{self.format}:s{self.section_index}:p{self.paragraph_index}:r{run}"

    @property
    def sort_key(self) -> Tuple[int, int, int, int]:
        return (
            self.section_index,
            self.paragraph_index,
            self.run_index if self.run_index is not None else -1,
            self.reference_index if self.reference_index is not None else -1,
        )


class Paragraph(BaseModel):
    text: str
    location: Location


class ExtractedDocument(BaseModel):
    format: Literal["hwp", "hwpx"]
    paragraphs: list[Paragraph]
    page_count: Optional[int] = Field(default=None, ge=1, le=30)
    warnings: list[str] = Field(default_factory=list)


class CitationMention(BaseModel):
    author: str
    year: int


class Citation(BaseModel):
    id: str
    raw_text: str
    mentions: list[CitationMention]
    location: Location


class AuthorName(BaseModel):
    raw: str
    normalized: str


class ReferenceItem(BaseModel):
    id: str
    list_kind: Literal["korean", "english"]
    reference_index: int = Field(ge=0)
    raw_text: str
    authors: list[AuthorName]
    year: Optional[int] = None
    title: Optional[str] = None
    doi: Optional[str] = None
    url: Optional[str] = None
    location: Location

    @model_validator(mode="after")
    def location_matches_reference(self) -> ReferenceItem:
        if self.location.reference_index != self.reference_index:
            raise ValueError("location reference_index must match reference_index")
        return self


class ParsedManuscript(BaseModel):
    document: ExtractedDocument
    citations: list[Citation]
    references: list[ReferenceItem]
    reference_section_found: bool = True
    body_text_sufficient: bool = True
    warnings: list[str] = Field(default_factory=list)

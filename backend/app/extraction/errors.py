class DocumentError(ValueError):
    """Base error for rejected or malformed documents."""


class UnsupportedDocumentError(DocumentError):
    """The document format or feature is not supported."""


class CorruptDocumentError(DocumentError):
    """The document container or record stream is malformed."""


class SecurityLimitError(DocumentError):
    """A defensive parser limit was exceeded."""

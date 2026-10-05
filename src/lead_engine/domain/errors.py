"""Expected domain/application failures, safe to show at the CLI."""


class DomainError(ValueError):
    pass


class NotFoundError(DomainError):
    pass


class DuplicateError(DomainError):
    pass


class IdentityConflictError(DomainError):
    pass


class RelationshipError(DomainError):
    pass

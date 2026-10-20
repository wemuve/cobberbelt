"""Document visibility rules, applied in SQL so lists, search and counts all agree.

* public / institutional  → any member of the owning organisation
* project_restricted      → members of a project the document is linked to, plus admins/owners
* confidential            → admins/owners and users with an explicit grant
Row-level security separately guarantees nothing crosses organisations.
"""

from __future__ import annotations

from sqlalchemy import ColumnElement, and_, exists, or_, select

from copperbelt.models.evidence import Classification, Document, DocumentAccessGrant
from copperbelt.models.projects import ProjectDocument, ProjectMember


def visible_documents_clause(c) -> ColumnElement[bool]:  # noqa: ANN001
    base = and_(Document.org_id == c.org_id, Document.deleted_at.is_(None))
    if c.can("documents:read_confidential"):
        return base
    member_of_linked_project = exists(
        select(ProjectDocument.id)
        .join(ProjectMember, ProjectMember.project_id == ProjectDocument.project_id)
        .where(ProjectDocument.document_id == Document.id, ProjectMember.user_id == c.user.id)
    )
    granted = exists(
        select(DocumentAccessGrant.id).where(
            DocumentAccessGrant.document_id == Document.id, DocumentAccessGrant.user_id == c.user.id
        )
    )
    return and_(
        base,
        or_(
            Document.classification.in_([Classification.public, Classification.institutional]),
            and_(Document.classification == Classification.project_restricted, or_(member_of_linked_project, granted)),
            and_(Document.classification == Classification.confidential, granted),
        ),
    )


def get_visible_document(c, document_id) -> Document:  # noqa: ANN001
    from copperbelt.errors import NotFound

    doc = c.db.scalar(select(Document).where(Document.id == document_id, visible_documents_clause(c)))
    if doc is None:
        # Same response whether it does not exist or is hidden: no existence oracle.
        raise NotFound("Document not found")
    return doc

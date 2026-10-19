"""The projects a credential can act on.

Projects are created and deleted from the web console, so this service only
reads them. It tells a script or a service account which projects it can deploy
to.
"""

from __future__ import annotations

from nemau.errors import not_found
from nemau.models import Project, ProjectList
from nemau.services import Service


class ProjectsService(Service):
    """Project discovery."""

    def list(self) -> ProjectList:
        payload = self._client.request("GET", "/orchestration/projects")
        return ProjectList.from_dict(payload or {})

    def get(self, id_or_name: str) -> Project:
        """Returns a project by id, by name or by slug.

        When an id and a name or slug both match, the id wins, so an id is
        never ambiguous.
        """
        projects = self.list().projects
        for project in projects:
            if project.id == id_or_name:
                return project
        for project in projects:
            if id_or_name in (project.name, project.slug):
                return project
        raise not_found("project", id_or_name, "/orchestration/projects")

    def current(self) -> Project | None:
        """The project the credential resolves to, or ``None``.

        ``None`` is not an error: it is the normal result for an
        organization-wide service account with no project selected.
        """
        for project in self.list().projects:
            if project.current:
                return project
        return None

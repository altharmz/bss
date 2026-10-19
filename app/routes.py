"""Walk the pinned FastAPI version's lazily included routers.

Included routes keep their original endpoint and receive dependencies from their
include context at dispatch. Keep this framework detail in one place.
"""

from fastapi.routing import APIRoute


def routes(application):
    for route in application.routes:
        included = getattr(route, "original_router", None)
        if included is not None:
            inherited = route.include_context.dependencies
            for child, dependencies in routes(included):
                yield child, [*inherited, *dependencies]
        elif isinstance(route, APIRoute):
            yield route, route.dependencies
        else:
            raise TypeError(f"unaccounted route type: {type(route).__name__}")

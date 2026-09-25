"""Who is making the current change, for the item history.

A request sets this from the signed-in user, else the name saved on that
device ("Set your name" in the sidebar), else the device's network address.
Background work (the Square worker, webhooks, nightly checks) wraps itself in
``acting_as("Square")`` so its changes are labelled too.
"""
from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar
from urllib.parse import unquote

NAME_COOKIE = "ps_name"
MAX_NAME_LENGTH = 40

_actor: ContextVar[str] = ContextVar("pinksheet_actor", default="System")


def current() -> str:
    return _actor.get()


@contextmanager
def acting_as(name: str):
    token = _actor.set(name)
    try:
        yield
    finally:
        _actor.reset(token)


def clean_name(value: str | None) -> str:
    """A display name typed by a person: printable, single-line, short."""
    text = re.sub(r"[\x00-\x1f\x7f<>]", "", unquote(value or ""))
    return " ".join(text.split())[:MAX_NAME_LENGTH]


def from_request(request) -> str:
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        return (user.get_full_name() or user.get_username())[:MAX_NAME_LENGTH]
    name = clean_name(request.COOKIES.get(NAME_COOKIE))
    if name:
        return name
    return f"Device {request.META.get('REMOTE_ADDR') or 'unknown'}"


class ActorMiddleware:
    """Labels every change made during a request with who made it."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.actor = from_request(request)
        request.actor_named = not request.actor.startswith("Device ")
        token = _actor.set(request.actor)
        try:
            return self.get_response(request)
        finally:
            _actor.reset(token)

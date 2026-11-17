"""Local demo web app that generated tests run against, so execution never touches the public internet.

`?variant=v2` replaces `#loginBtn` with `[data-testid="login"]` to reproduce a locator failure on demand.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse

router = APIRouter(prefix="/demo", tags=["demo"], include_in_schema=False)

DEMO_USERNAME = "demo_user"
DEMO_PASSWORD = "demo_password"
Variant = Literal["v1", "v2"]

_PAGE = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>{title}</title></head>
<body>
{body}
</body>
</html>
"""


def _login_page(variant: Variant, error: bool = False) -> str:
    button = (
        '<button type="submit" data-testid="login">Log in</button>'
        if variant == "v2"
        else '<button type="submit" id="loginBtn">Log in</button>'
    )
    message = '<p id="error" role="alert">Invalid user name or password</p>' if error else ""
    body = f"""<h1>Sign in</h1>
{message}
<form method="post" action="/demo/login">
  <input type="hidden" name="variant" value="{variant}">
  <label for="username">User name</label> <input id="username" name="username" autocomplete="username">
  <label for="password">Password</label>
  <input id="password" name="password" type="password" autocomplete="current-password">
  {button}
</form>"""
    return _PAGE.format(title="Demo login", body=body)


@router.get("", response_class=RedirectResponse)
def demo_root() -> RedirectResponse:
    return RedirectResponse("/demo/login")


@router.get("/login", response_class=HTMLResponse)
def login_form(variant: Annotated[Variant, Query()] = "v1") -> str:
    return _login_page(variant)


@router.get("/v2/login", response_class=HTMLResponse)
def login_form_v2() -> str:
    """The v2 page under a path, so BASE_URL=.../demo/v2 switches a generated test to it."""
    return _login_page("v2")


@router.post("/login", response_model=None)
def login(
    username: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    variant: Annotated[Variant, Form()] = "v1",
) -> RedirectResponse | HTMLResponse:
    if username == DEMO_USERNAME and password == DEMO_PASSWORD:
        return RedirectResponse("/demo/welcome", status_code=303)
    return HTMLResponse(_login_page(variant, error=True), status_code=401)


@router.get("/welcome", response_class=HTMLResponse)
def welcome() -> str:
    return _PAGE.format(title="Demo home", body='<h1 id="welcome">Welcome</h1>')

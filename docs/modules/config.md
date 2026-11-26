# `config.py` - settings and error messages

`src/generic_agent/config.py`

## Purpose

Loads the Cursor configuration, lists available models, and turns failures into
readable, secret-free text.

## Key symbols

| Symbol | What it does |
| --- | --- |
| `Settings(api_key: SecretStr, model: str)` | Frozen dataclass; the key is hidden from `repr`. |
| `load_settings(environ=None, env_file=None, *, require_model=True)` | Reads `CURSOR_API_KEY` / `CURSOR_MODEL`. |
| `list_model_ids(settings)` | `Cursor.models.list()` as `(id, display_name)` pairs. |
| `describe_error(exc)` | Maps exceptions to actionable text. |
| `ConfigError` | Missing or placeholder configuration. |
| `CursorRunError` | A run started but did not finish successfully. |

## Behaviour to preserve

- Non-empty shell variables win over `./.env` (in the current working directory).
  Values starting with `your-` (from `.env.example`) count as missing.
- `CURSOR_MODEL` has no default. `require_model=False` is for `--list-models`.
- The key is a `SecretStr`. Nothing in this module (or any error text) may
  include it.
- `describe_error` handles `ConfigError`/`CursorRunError` (their own message),
  authentication, permission, not found, network, rate limit, bad request,
  other `CursorAgentError`, then falls back to `TypeName: message`.

## Caution

`describe_error` is for the local terminal and server logs. It can include SDK
messages (`BadRequestError.message`, `CursorRunError` details), so **do not
return it to HTTP clients**; `api.py` sends a fixed message instead.

## Tests

`tests/test_config.py`: `.env` vs shell precedence, missing values, placeholders,
optional model, no secret in `repr`, `list_model_ids`, `describe_error` table.

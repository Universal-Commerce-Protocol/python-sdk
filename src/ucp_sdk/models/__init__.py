# Copyright 2026 UCP Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""UCP models."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from . import schemas
from .schemas import __all__ as __all__

if TYPE_CHECKING:
    from .schemas import *  # noqa: F403


def __getattr__(name: str) -> Any:
    """Re-export public models from ``ucp_sdk.models.schemas`` on access."""
    if name not in __all__:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg)
    value = getattr(schemas, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Return module attributes including lazily re-exported model names."""
    return sorted({*globals(), *__all__})

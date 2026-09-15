"""Two identities, and why the Worker needs both.

A **person** signs in with an email and a password. A **machine** signs in with
a workerId and a workerToken. They are not interchangeable:

* The person's access token proves *whose* account this machine should belong
  to. It is used once, at registration, and then thrown away. It is never
  written to disk -- a token that lives in a file on a supplier's PC is a
  token that can be taken from it, and it can do everything the person can.
* The Worker's token proves *which machine* this is. It lives on disk, sealed
  by DPAPI, and is the only credential the Worker uses from then on. It can do
  exactly one thing: be this Worker.

That is why registering is a one-time hand-off rather than a login the Worker
repeats.

The states below are kept apart on purpose. The one that matters most is that
**BACKEND_UNREACHABLE is not NEEDS_ENROLLMENT**: a service that is down for a
minute must never cause a registered machine to throw its identity away and ask
a person to sign in again.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from .logging_setup import LOGGER_NAME
from .worker_credentials import (
    CredentialLoad,
    CredentialState,
    WorkerCredentials,
)

_debug = logging.getLogger(LOGGER_NAME)


class WorkerAuthState(Enum):
    """Where this machine stands with the Backend."""

    #: Known to the Backend and accepted. The Worker may go on.
    REGISTERED = "REGISTERED"

    #: No credentials here. A person has to sign in so the machine can be
    #: attached to their account.
    NEEDS_ENROLLMENT = "NEEDS_ENROLLMENT"

    #: Credentials are here but cannot be used -- protected for another Windows
    #: account, or half-written. **Not** re-registered automatically: somebody
    #: put that file there deliberately and replacing it is not ours to decide.
    CREDENTIAL_ERROR = "CREDENTIAL_ERROR"

    #: The Backend knows this Worker and no longer wants it. Registering again
    #: is the answer, but only a person can decide to.
    REVOKED = "REVOKED"

    #: The Backend refused the token and will keep refusing it.
    AUTH_FAILED = "AUTH_FAILED"

    #: The Backend said in as many words that this Worker token is not valid
    #: (401 ``INVALID_WORKER_TOKEN``) -- the Worker was deleted from the account
    #: and its token went with it. The identity on disk is dead, and it is the
    #: **only** refusal that is: it may be forgotten and the machine enrolled
    #: again, once, by the auth stage. Everything else -- an outage, a 5xx, a
    #: bare 401 without that code -- leaves the identity where it is.
    CREDENTIALS_INVALIDATED = "CREDENTIALS_INVALIDATED"

    #: The Backend said in as many words that it has no record of this Worker
    #: (``WORKER_NOT_FOUND``). Distinct from REVOKED, which is being told no by
    #: an account that knows this machine. Nothing is deleted and nobody is
    #: enrolled again on the strength of it: only a person decides that.
    WORKER_NOT_FOUND = "WORKER_NOT_FOUND"

    #: The service answered, but not the question that was asked -- a 404 from
    #: an endpoint that is not there, most likely. Nothing was learned about
    #: this Worker, so nothing about it may be concluded. **Never** read as
    #: REVOKED: an unimplemented path would otherwise tell a supplier their
    #: machine had been removed from their account.
    BACKEND_CONTRACT_ERROR = "BACKEND_CONTRACT_ERROR"

    #: Nothing was learned. Running again later is the right answer -- **not**
    #: enrolling again, and never deleting what is on disk.
    BACKEND_UNREACHABLE = "BACKEND_UNREACHABLE"


@dataclass(frozen=True)
class WorkerAuthResult:
    state: WorkerAuthState
    detail: str
    #: Set only when the state is REGISTERED. Never carries the token.
    worker_id: str | None = None

    @property
    def is_authenticated(self) -> bool:
        return self.state is WorkerAuthState.REGISTERED


@dataclass(frozen=True)
class RegistryAnswer:
    """What the Backend's Worker API said."""

    state: WorkerAuthState
    detail: str
    #: Present only when a registration issued a new identity.
    credentials: WorkerCredentials | None = None


class WorkerRegistry(Protocol):
    """The Backend's Worker side.

    Implemented over HTTP by
    :class:`gshared_worker.worker_registry.HttpWorkerRegistry` against the
    agreed contract. It stays a Protocol so this module decides states without
    knowing anything about HTTP, and so tests can drive every state directly.
    """

    def verify(self, credentials: WorkerCredentials) -> RegistryAnswer:
        """Does the Backend still accept this workerId and token."""

    def register(self, access_token: str, registration: Any) -> RegistryAnswer:
        """Attach a new Worker to the account that access token belongs to.

        ``registration`` describes the machine being offered -- see
        :class:`gshared_worker.worker_registry.WorkerRegistrationRequest`.

        The account is decided by the Backend from the token it was given.
        The Worker never sends a user id: a client that names its own owner is
        a client that can name somebody else's.
        """


class WorkerAuthService:
    """Decides what this machine's standing is. Changes nothing on its own."""

    def __init__(self, registry: WorkerRegistry) -> None:
        self._registry = registry

    def authenticate(self, loaded: CredentialLoad) -> WorkerAuthResult:
        """The existing-Worker path: credentials on disk, checked with the Backend."""
        if loaded.state is CredentialState.ABSENT:
            return WorkerAuthResult(
                WorkerAuthState.NEEDS_ENROLLMENT,
                "This machine is not registered to a G-shared account yet.",
            )
        if loaded.state is CredentialState.UNUSABLE:
            # Re-registering would work, and would silently replace credentials
            # somebody deliberately copied here. Saying so beats deciding.
            return WorkerAuthResult(WorkerAuthState.CREDENTIAL_ERROR, loaded.detail)

        answered = self._registry.verify(loaded.credentials)
        _debug.debug(
            "worker auth: %s / %s", answered.state.value, loaded.credentials.worker_id
        )
        return WorkerAuthResult(
            answered.state, answered.detail,
            worker_id=loaded.credentials.worker_id
            if answered.state is WorkerAuthState.REGISTERED else None,
        )

    def register(
        self, access_token: str, registration: Any
    ) -> tuple[WorkerAuthResult, WorkerCredentials | None]:
        """The new-Worker path: a signed-in person, then a machine identity.

        ``registration`` is what the machine is offering, described in
        :class:`gshared_worker.worker_registry.WorkerRegistrationRequest`. It
        is passed through untouched: what this class decides is the state, not
        the contract.

        Returns the credentials separately from the result so the caller
        decides when they are written. **Nothing is stored when registration
        fails** -- a half-registered machine is worse than an unregistered one.
        """
        answered = self._registry.register(access_token, registration)
        if answered.state is not WorkerAuthState.REGISTERED:
            _debug.warning("worker registration refused: %s", answered.state.value)
            return WorkerAuthResult(answered.state, answered.detail), None
        if answered.credentials is None:
            # Registered, apparently, without saying what this Worker is now.
            return WorkerAuthResult(
                WorkerAuthState.AUTH_FAILED,
                "The service registered this machine but returned no Worker identity.",
            ), None
        _debug.debug(
            "worker registration issued %s", answered.credentials.worker_id
        )
        return (
            WorkerAuthResult(
                WorkerAuthState.REGISTERED, answered.detail,
                worker_id=answered.credentials.worker_id,
            ),
            answered.credentials,
        )

"""Why a job was not accepted, in words the Backend can act on.

A JOB_REJECTED carrying "something went wrong" is worth very little: the
Backend has to decide whether to hand the job to somebody else, wait, or stop
trying, and those are different answers. So every refusal carries one of these
codes, and each code says which.

**Retry is a property of the code, not of the caller.** Exactly one of these
is transient. Every other one describes a fact that will read the same in an
hour -- a price that does not match, a job funded for somebody else, an
operator this provider never approved. Retrying those is not optimism, it is
sending the same transaction into the same wall, and in the mismatch cases it
would mean accepting terms the Worker was never offered.
"""

from __future__ import annotations

from enum import Enum


class RejectCode(str, Enum):
    """The stable reason names carried by JOB_REJECTED."""

    # -- the assignment itself ----------------------------------------
    #: The message did not carry the on-chain fields, or they were malformed.
    INVALID_ASSIGNMENT = "INVALID_ASSIGNMENT"
    #: This Worker is busy with a different job, or the slot disagrees.
    ASSIGNMENT_MISMATCH = "ASSIGNMENT_MISMATCH"

    # -- what the chain says about the job ----------------------------
    ESCROW_JOB_NOT_FOUND = "ESCROW_JOB_NOT_FOUND"
    EXTERNAL_REF_MISMATCH = "EXTERNAL_REF_MISMATCH"
    CLIENT_MISMATCH = "CLIENT_MISMATCH"
    PROVIDER_MISMATCH = "PROVIDER_MISMATCH"
    PRICE_MISMATCH = "PRICE_MISMATCH"
    INVALID_ESCROW_STATE = "INVALID_ESCROW_STATE"
    ACCEPT_DEADLINE_EXPIRED = "ACCEPT_DEADLINE_EXPIRED"

    # -- this machine's own key ---------------------------------------
    OPERATOR_KEY_UNAVAILABLE = "OPERATOR_KEY_UNAVAILABLE"
    OPERATOR_ADDRESS_MISMATCH = "OPERATOR_ADDRESS_MISMATCH"
    #: The provider never called ``setOperator`` for this machine.
    OPERATOR_NOT_APPROVED = "OPERATOR_NOT_APPROVED"

    #: This Worker has no escrow configured, so it cannot check any of the
    #: on-chain terms. A deployment state rather than a fact about the job --
    #: but the answer to this job is still no, and the Backend should stop
    #: offering work to this machine rather than retry.
    ESCROW_NOT_CONFIGURED = "ESCROW_NOT_CONFIGURED"

    # -- the chain, rather than the job -------------------------------
    BLOCKCHAIN_RPC_FAILED = "BLOCKCHAIN_RPC_FAILED"
    TRANSACTION_FAILED = "TRANSACTION_FAILED"


#: The only refusal worth trying again. It says nothing about the job -- the
#: node was unreachable, or stopped answering mid-call -- so the same job may
#: well be acceptable a minute later.
TRANSIENT = frozenset({RejectCode.BLOCKCHAIN_RPC_FAILED})


def is_transient(code: RejectCode) -> bool:
    return code in TRANSIENT


class JobRefused(Exception):
    """A refusal with a code. Raised by validation, caught by acceptance.

    ``detail`` is shown and sent. It never contains a key, and it never
    contains a value taken from a message without being named -- a mismatch
    says which field disagreed, not what the other side claimed it should be.
    """

    def __init__(self, code: RejectCode, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail

    @property
    def transient(self) -> bool:
        return is_transient(self.code)

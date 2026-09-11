# G-shared's approved QLoRA SFT training runtime.
#
# The base is the image the GPU PoC already validated on real NVIDIA hardware
# and the one the Worker's benchmark is pinned to today: PyTorch 2.12.1+cu126
# on Python 3.12, referenced by digest rather than by tag. A tag would mean
# two suppliers running "the same" workload on different PyTorch builds, and
# their results would not be comparable -- which is the same reason the
# benchmark pins it.
#
# What is in here: the training runner, Python, CUDA and the QLoRA stack.
#
# What is deliberately NOT in here:
#   * model weights   -- an immutable external asset, mounted at /job/model
#   * datasets        -- a job input, mounted at /job/input
#   * any credential  -- this container never learns the workerToken, a user's
#                        access token, the operator wallet key or an RPC key,
#                        because it never talks to anything. It gets weights,
#                        data and numbers, and returns an adapter.
#
# The container is built to run with no network at all. Every model and
# dataset fetch happens on the host before the job starts; the two OFFLINE
# variables below turn a library's attempt to reach the internet into an
# immediate error rather than a sixty-second timeout inside a paid job.

FROM pytorch/pytorch@sha256:79c5599719e0b1afdb56ac2d14588b530283752d7ae6ec3c36e18ec9deb8b229

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/qlora-sft \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    # Every cache a library will try to write, aimed at the one writable
    # mount that is not the job's result. Left to their defaults they land in
    # $HOME, which does not exist for uid 10001 on a read-only root.
    HOME=/job/cache \
    HF_HOME=/job/cache/huggingface \
    XDG_CACHE_HOME=/job/cache \
    TRITON_CACHE_DIR=/job/cache/triton \
    TORCHINDUCTOR_CACHE_DIR=/job/cache/inductor

# `spin` is a developer task-runner that ships in the PyTorch base image and
# has no role in a training run. It is removed rather than kept, because it
# pins click below 8.4 while typer -- which transformers pulls in -- needs
# 8.5, and the honest fix for an unused tool blocking a needed one is to stop
# shipping the unused tool. Found by the `pip check` below, which is the point
# of having it.
RUN pip uninstall --break-system-packages -y spin

# One layer, from a pinned list, with the wheel cache discarded. `pip check`
# fails the build rather than the first job if the pins ever stop agreeing
# with each other.
COPY requirements.txt /tmp/requirements.txt
RUN pip install --break-system-packages --no-cache-dir -r /tmp/requirements.txt \
    && pip check \
    && rm -f /tmp/requirements.txt

WORKDIR /opt/qlora-sft
COPY --chown=10001:10001 qlora_sft /opt/qlora-sft/qlora_sft

# The mount points, created at build time and owned by the runtime user, so a
# read-only root filesystem still has somewhere for the bind mounts to land.
RUN mkdir -p /job/model /job/input /job/output /job/cache \
    && chown -R 10001:10001 /job

# Same unprivileged uid the PoC runtime images use. Nothing in a training run
# needs root, and `--cap-drop ALL --security-opt no-new-privileges` is only
# worth much if the process did not start as root to begin with.
USER 10001:10001

ENTRYPOINT ["python", "-m", "qlora_sft"]

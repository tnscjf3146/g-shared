FROM pytorch/pytorch@sha256:79c5599719e0b1afdb56ac2d14588b530283752d7ae6ec3c36e18ec9deb8b229

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /opt/gpu-task
COPY --chown=10001:10001 gpu_task.py /opt/gpu-task/gpu_task.py

USER 10001:10001
ENTRYPOINT ["python", "/opt/gpu-task/gpu_task.py"]

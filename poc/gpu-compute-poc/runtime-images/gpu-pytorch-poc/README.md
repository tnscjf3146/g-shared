# GPU PyTorch PoC Runtime Image

Platform-managed STEP 2.5 image for the fixed `GPU_PYTORCH_TEST` workload.

The image contains PyTorch 2.12.1 with CUDA 12.6 and `gpu_task.py`. It performs
real CUDA matrix multiplication, synchronizes the device, verifies known sample
values, and emits one `GPU_TASK_RESULT=` JSON record.

The platform operator builds and publishes this image. GPU providers never run
Docker commands directly.
# Published immutable image

- Tag: `le3560/gpu-pytorch-poc:0.1`
- OCI index: `sha256:ffa5965693467bca2cd4ead6eb92e5014d9ed515144850dfada482ca73523b3a`
- linux/amd64 manifest used by Worker: `sha256:15f1c509660e25ce4a8a86688aabad9dee87372e584efb175329c4922c5e1fa2`

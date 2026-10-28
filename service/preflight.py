from pathlib import Path
import sys
from .settings import Settings

def main():
    settings = Settings.from_env()
    import torch
    from diffusers import Flux2KleinPipeline
    print(f'Python {sys.version.split()[0]}; PyTorch {torch.__version__}; bundled CUDA {torch.version.cuda}')
    if not torch.cuda.is_available():
        raise SystemExit('No visible CUDA device. Preserve the notebook MIG assignment; check CUDA_VISIBLE_DEVICES.')
    free, total = torch.cuda.mem_get_info(0)
    print(f'Device: {torch.cuda.get_device_name(0)}; visible VRAM {total / 2**30:.1f} GiB; free {free / 2**30:.1f} GiB')
    if total * settings.memory_fraction < 16 * 2**30 or free < 18 * 2**30:
        raise SystemExit('Insufficient GPU headroom for this configuration. Do not stop unrelated GPU processes automatically.')
    tensor = torch.ones((8, 8), device='cuda', dtype=torch.bfloat16)
    _ = tensor @ tensor
    torch.cuda.synchronize()
    print('CUDA and BF16 smoke test passed.')
    if '--download' in sys.argv:
        from huggingface_hub import snapshot_download
        snapshot = Path(snapshot_download(settings.model_id, revision=settings.revision))
        print(f'Model downloaded; revision {snapshot.name}.')
        if not settings.revision:
            from .settings import ROOT
            path = ROOT / '.env'
            contents = path.read_text()
            import re
            contents, count = re.subn(r'(?m)^MODEL_REVISION=\s*$', 'MODEL_REVISION=' + snapshot.name, contents)
            if not count:
                contents += '\nMODEL_REVISION=' + snapshot.name + '\n'
            path.write_text(contents)
            path.chmod(0o600)

if __name__ == '__main__':
    main()

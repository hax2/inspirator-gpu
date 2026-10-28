import time

class GenerationDeadline(Exception):
    pass

class FluxEngine:
    def __init__(self, settings):
        self.settings = settings
        self.pipe = None

    def load(self):
        import torch
        from diffusers import Flux2KleinPipeline
        if not torch.cuda.is_available():
            raise RuntimeError('PyTorch cannot see a CUDA device. Check MIG assignment and CUDA_VISIBLE_DEVICES.')
        torch.cuda.set_per_process_memory_fraction(self.settings.memory_fraction, 0)
        kwargs = {'torch_dtype': torch.bfloat16}
        if self.settings.revision:
            kwargs['revision'] = self.settings.revision
        self.pipe = Flux2KleinPipeline.from_pretrained(self.settings.model_id, **kwargs).to('cuda')
        self.pipe.set_progress_bar_config(disable=True)

    def generate(self, prompt, references, width, height, seed, deadline, stopping):
        import torch
        def check_deadline(_pipeline, _step, _timestep, values):
            if stopping.is_set() or time.monotonic() >= deadline:
                raise GenerationDeadline('Generation stopped at the inference-step deadline.')
            return values
        with torch.inference_mode():
            result = self.pipe(prompt=prompt, image=references or None, width=width, height=height,
                               num_inference_steps=4, guidance_scale=1.0,
                               generator=torch.Generator(device='cuda').manual_seed(seed),
                               callback_on_step_end=check_deadline).images[0]
            torch.cuda.synchronize()
        return result

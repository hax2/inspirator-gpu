import base64
from io import BytesIO
import re
import warnings
from PIL import Image, ImageOps

Image.MAX_IMAGE_PIXELS = 16_000_000

def decode_reference(value: str):
    match = re.fullmatch(r'data:image/(jpeg|png|webp);base64,([A-Za-z0-9+/=]+)', value)
    if not match:
        raise ValueError('References must be JPG, PNG or WebP data URIs. Remote URLs are not fetched.')
    try:
        raw = base64.b64decode(match[2], validate=True)
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw)) as image:
                if image.format not in {'JPEG', 'PNG', 'WEBP'}:
                    raise ValueError('Unsupported reference format.')
                image.load()
                result = ImageOps.exif_transpose(image).convert('RGB')
                result.thumbnail((1024, 1024))
                return result.copy()
    except Exception as exc:
        raise ValueError('Invalid or oversized reference image.') from exc

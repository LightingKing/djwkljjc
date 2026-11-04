"""Image preparation before review; all roots and inputs are explicit."""
from __future__ import annotations

import json
from pathlib import Path
import re

from PIL import Image, ImageOps

from .paths import within_root


# name -> path, dimensions, face-relative centre x/y, width and right-edge fade.
CROPS = (
    ("actorgachahead/gacha_head_{a}", (256, 128), 0, .05, 2.4, None),
    ("actorheadrectangle/actor_head_{a}", (256, 256), 0, .40, 2.3, None),
    ("actorheadbig/actor_head_big_{a}", (256, 512), 0, 1.30, 2.0, None),
    ("actorinfighthead/main/infight_head_{a}", (128, 128), 0, .10, 1.35, None),
    ("actorinfighthead/main_r/infight_head_{a}", (128, 128), .08, .10, 1.35, None),
    ("actorinfighthead/sub_2/infight_head_{a}", (512, 128), .25, -.05, 2.4, (.62, 1.0)),
    ("actorjourneyhead/actor_head_{a}", (512, 256), .35, .35, 3.0, (.75, 1.0)),
    ("actorskillhead/actor_skill_head_{a}", (512, 64), .55, -.05, 2.8, (.45, .75)),
)


def render_prompt(kind: str, **fields: str) -> str:
    templates = json.loads(Path(__file__).with_name("prompt-templates.json").read_text(encoding="utf-8"))
    if kind not in templates or any(not str(fields.get(key, "")).strip() for key in ("name", "identity", "pose", "outfit")):
        raise ValueError("known prompt kind and explicit name/identity/pose/outfit are required")
    return templates[kind].format_map(fields)


def _transparent(image: Image.Image) -> None:
    if image.mode != "RGBA" or image.getchannel("A").getextrema()[0] == image.getchannel("A").getextrema()[1]:
        raise ValueError("cutout needs nonempty transparent alpha and visible foreground")


def cutout_image(source: Image.Image, *, remover=None) -> Image.Image:
    """Run isnet-anime only when requested; never mutate the original image.

    remover is an injectable image -> RGBA callable for deterministic boundary
    tests or a caller-managed model session. Generation is not performed here.
    """
    if remover is None:
        from rembg import new_session, remove
        session = new_session("isnet-anime")
        remover = lambda image: remove(image, session=session, alpha_matting=True,
                    alpha_matting_foreground_threshold=240, alpha_matting_background_threshold=15)
    result = remover(source.copy())
    if not isinstance(result, Image.Image) or result.size != source.size:
        raise ValueError("cutout backend changed image dimensions")
    _transparent(result)
    return result.copy()


def detect_face(image: Image.Image, cascade_path: Path) -> tuple[float, float, float]:
    """Optional convenience; ambiguous/no detections require manual coordinates."""
    import cv2
    import numpy as np
    cascade = cv2.CascadeClassifier(str(Path(cascade_path).resolve()))
    if cascade.empty():
        raise ValueError("could not load face detector")
    gray = cv2.equalizeHist(cv2.cvtColor(np.asarray(image.convert("RGB")), cv2.COLOR_RGB2GRAY))
    faces = cascade.detectMultiScale(gray, scaleFactor=1.05, minNeighbors=3, minSize=(70, 70))
    if len(faces) != 1:
        raise ValueError("face detector needs exactly one face; supply reviewed face_box coordinates")
    x, y, w, _ = faces[0]
    return float(x), float(y), float(w)


def _crop(image, face_box, spec):
    _, (tw, th), cx, cy, width, fade = spec
    fx, fy, fw = face_box
    w = width * fw
    h = w * th / tw
    x, y = fx + fw / 2 + cx * fw, fy + fw / 2 + cy * fw
    box = (int(x - w / 2), int(y - h / 2), int(x + w / 2), int(y + h / 2))
    out = image.crop(box).resize((tw, th), Image.Resampling.LANCZOS)
    if fade:
        start, end = int(fade[0] * tw), int(fade[1] * tw)
        alpha = out.getchannel("A")
        values = list(alpha.tobytes())
        for offset, value in enumerate(values):
            column = offset % tw
            factor = 1 if column < start else max(0, (end - column) / (end - start))
            values[offset] = round(value * factor)
        alpha.putdata(values)
        out.putalpha(alpha)
    return out


def _full_body_canvas(full_body):
    small = ImageOps.contain(full_body, (1024, 1024), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    # Composite once; using paste(image, ..., image) would square semitransparent alpha.
    canvas.alpha_composite(small, ((1024 - small.width) // 2, (1024 - small.height) // 2))
    return canvas


def _skin_mask(full_body):
    alpha = full_body.getchannel("A")
    box = alpha.getbbox()
    if box is None:
        raise ValueError("full-body image contains no foreground")
    body = ImageOps.contain(full_body.crop(box), (438, 530), Image.Resampling.LANCZOS)
    lum, mask = body.convert("L"), body.getchannel("A")
    values = [round(a * (.30 + .45 * (1 - l / 255))) for l, a in zip(lum.tobytes(), mask.tobytes())]
    density = Image.new("L", body.size)
    density.putdata(values)
    result = Image.new("L", (438, 530), 0)
    result.paste(density, ((438 - body.width) // 2, 530 - body.height))
    return result


def derive_ui(portrait: Image.Image, full_body: Image.Image | None, *, actor_id: int,
              anim_res: str, face_box: tuple[float, float, float],
              logo: Image.Image | None = None, introduction: Image.Image | None = None) -> dict[str, Image.Image]:
    """Derive 12 art textures plus optional same-Role logo/introduction copies.

    The required full_body input must be separately generated/reviewed. This
    mechanical check rejects the known half-portrait reuse but does not claim
    to recognize anatomy: actual full-body completeness requires visual review.
    """
    if full_body is None:
        raise ValueError("an independent full-body source is required")
    if full_body.size == portrait.size and full_body.convert("RGBA").tobytes() == portrait.convert("RGBA").tobytes():
        raise ValueError("full-body source must be independent of the half-body portrait")
    if type(actor_id) is not int or actor_id <= 0 or not re.fullmatch(r"[a-z0-9_]+", anim_res):
        raise ValueError("invalid actor ID or anim resource")
    _transparent(portrait)
    _transparent(full_body)
    if len(face_box) != 3:
        raise ValueError("face_box needs x, y and width")
    fx, fy, fw = face_box
    if not (fw > 0 and fx >= 0 and fy >= 0 and fx + fw <= portrait.width and fy + fw <= portrait.height):
        raise ValueError("face_box lies outside the portrait")
    out = {f"actorportrait/actor_portrait_{anim_res}": portrait.copy()}
    if portrait.size != (1024, 1024):
        raise ValueError("portrait must be the reviewed 1024x1024 source")
    for spec in CROPS:
        out[spec[0].format(a=anim_res)] = _crop(portrait, face_box, spec)
    top = max(0, int(fy - .45 * fw))
    h, cx = portrait.height - top, fx + fw / 2
    box = (int(cx - h / 8), top, int(cx + h / 8), portrait.height)
    out[f"actorgroup/selectteam_{anim_res}"] = portrait.crop(box).resize((256, 1024), Image.Resampling.LANCZOS)
    out[f"actorportraitfull/actor_portrait_full_{anim_res}"] = _full_body_canvas(full_body)
    out[f"actorskinheadbig/actorskinhead_big_{actor_id}"] = _skin_mask(full_body)
    for image, key, size in ((logo, f"actorlogo/actor_logo_{anim_res}", (256, 256)),
                             (introduction, f"actorintroduction/introduction_{anim_res}", (512, 1024))):
        if image is not None:
            if image.size != size or image.mode != "RGBA":
                raise ValueError("copied logo/introduction must retain original size and RGBA")
            out[key] = image.copy()
    return out


def write_images(output_root: Path, images: dict[str, Image.Image]) -> list[Path]:
    """Write a new review set; refuse replacement of any existing file."""
    destinations = {key: within_root(output_root, key + ".png") for key in images}
    for path in destinations.values():
        if path.exists():
            raise FileExistsError(f"review output already exists: {path}")
    for key, path in destinations.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            images[key].save(stream, format="PNG")
    return list(destinations.values())

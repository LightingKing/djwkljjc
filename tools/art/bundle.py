"""Clone one self-contained UI texture bundle, preserving its Unity format."""
from __future__ import annotations

import hashlib
import io
from pathlib import PurePosixPath

from PIL import Image, ImageChops
import UnityPy
from UnityPy.enums import TextureFormat

from .paths import relative_path


def _load(raw: bytes):
    env = UnityPy.load(raw)
    if not hasattr(env.file, "files") or len(env.file.files) != 1:
        raise ValueError("expected one self-contained CAB file")
    objects = list(env.objects)
    textures = [obj for obj in objects if obj.type.name == "Texture2D"]
    bundles = [obj for obj in objects if obj.type.name == "AssetBundle"]
    if len(textures) != 1 or len(bundles) != 1 or len(objects) != 2:
        raise ValueError("template must contain exactly one Texture2D and one AssetBundle")
    texture = textures[0].read()
    if texture.m_StreamData.path or texture.m_StreamData.size:
        raise ValueError("external texture streams are not supported")
    tree = bundles[0].read_typetree()
    containers = tree.get("m_Container", [])
    if len(containers) != 1:
        raise ValueError("single texture bundle requires one container path")
    pointer = containers[0][1]["asset"]
    if pointer["m_FileID"] != 0 or pointer["m_PathID"] != textures[0].path_id:
        raise ValueError("container does not reference the bundle's texture")
    return env, textures[0], texture, bundles[0], tree


def inspect_texture_bundle(raw: bytes) -> dict:
    env, _, texture, _, tree = _load(raw)
    image = texture.image.convert("RGBA")
    return {"cabName": next(iter(env.file.files)), "bundleName": tree["m_Name"],
            "assetBundleName": tree["m_AssetBundleName"], "containerPaths": [k for k, _ in tree["m_Container"]],
            "textureName": texture.m_Name, "size": [texture.m_Width, texture.m_Height],
            "format": int(texture.m_TextureFormat), "alphaExtrema": list(image.getchannel("A").getextrema())}


def clone_texture_bundle(template: bytes, png: bytes, target: str, *,
                         expected_size: tuple[int, int], expected_format: int,
                         alpha_source: str) -> tuple[bytes, dict]:
    """Compile approved PNG bytes. Caller must enforce the origins approval gate.

    The lower-level byte API also accepts synthetic fixtures for offline tests.
    RGBA ETC2 uses alpha_source='rgba'. Alpha8 uses either RGBA's actual alpha
    channel ('alpha') or a deliberately prepared grayscale mask ('mask').
    """
    target = relative_path(target)
    if not target.startswith("assetbundle/texture/") or PurePosixPath(target).suffix:
        raise ValueError("target must be an extensionless texture bundle path")
    env, obj, texture, bundle_obj, tree = _load(template)
    if (texture.m_Width, texture.m_Height) != tuple(expected_size):
        raise ValueError("template size differs from expected dimensions")
    if texture.m_TextureFormat != expected_format or expected_format not in (1, 47):
        raise ValueError("template texture format must match expected Alpha8 or ETC2_RGBA8")
    with Image.open(io.BytesIO(png)) as opened:
        if opened.format != "PNG":
            raise ValueError("source must be PNG")
        image = opened.copy()
    if image.size != tuple(expected_size):
        raise ValueError("PNG size differs from template; implicit resizing is forbidden")
    if expected_format == 47:
        if alpha_source != "rgba" or image.mode != "RGBA":
            raise ValueError("ETC2_RGBA8 requires RGBA source with explicit rgba alpha mode")
        pixels = image
        expected_alpha = image.getchannel("A")
    elif alpha_source == "alpha" and image.mode == "RGBA":
        pixels = image.getchannel("A")
        expected_alpha = pixels
    elif alpha_source == "mask" and image.mode == "L":
        pixels = image
        expected_alpha = pixels
    else:
        raise ValueError("Alpha8 requires explicit RGBA alpha or L mask source")
    if expected_alpha.getextrema()[0] == expected_alpha.getextrema()[1]:
        raise ValueError("UI art must have a nonempty varying alpha mask")
    if expected_format == 1:
        # UnityPy encodes Alpha8 via Pillow's raw A packer, which requires RGBA.
        # Converting a mask to RGBA normally makes A=255 and silently loses it.
        pixels = Image.new("RGBA", image.size, (255, 255, 255, 0))
        pixels.putalpha(expected_alpha)
    leaf = target.rsplit("/", 1)[-1]
    texture.m_Name = leaf
    texture.set_image(pixels, target_format=TextureFormat(expected_format))
    texture.save()
    tree["m_Name"] = target
    tree["m_AssetBundleName"] = target
    old_path, reference = tree["m_Container"][0]
    extension = PurePosixPath(old_path).suffix
    tree["m_Container"] = [("assets/" + target + extension, reference)]
    bundle_obj.save_typetree(tree)
    old_cab = next(iter(env.file.files))
    # Stable target identity avoids collisions while retaining Unity's CAB shape.
    new_cab = "CAB-" + hashlib.md5(target.encode("utf-8")).hexdigest()
    serialized = env.file.files.pop(old_cab)
    serialized.name = new_cab
    env.file.files[new_cab] = serialized
    output = env.file.save(packer="original")
    checked_env, _, checked_texture, _, checked_tree = _load(output)
    checked = inspect_texture_bundle(output)
    if (checked["cabName"] != new_cab or checked["bundleName"] != target
            or checked["assetBundleName"] != target or checked["textureName"] != leaf
            or checked["size"] != list(expected_size) or checked["format"] != expected_format
            or checked["containerPaths"] != ["assets/" + target + extension]):
        raise ValueError("serialized texture identity or format failed round-trip validation")
    actual_alpha = checked_texture.image.convert("RGBA").getchannel("A")
    error = ImageChops.difference(expected_alpha, actual_alpha).getextrema()[1]
    # Alpha8 is lossless; ETC2 alpha is quantized. UnityPy's ETC2 encoder can
    # produce a 35-level edge difference on real reviewed UI cutouts, so keep a
    # format-specific tolerance while still rejecting damaged alpha.
    if error > (0 if expected_format == 1 else 40):
        raise ValueError("serialized alpha differs from its source beyond texture format tolerance")
    report = {**checked, "templateSha256": hashlib.sha256(template).hexdigest(),
              "sourceSha256": hashlib.sha256(png).hexdigest(),
              "bundleSha256": hashlib.sha256(output).hexdigest(), "bundleLength": len(output),
              "alphaSource": alpha_source, "alphaMaxError": error, "validated": True}
    return output, report

"""Offline art CLI: prepare review images, record provenance, clone approved art."""
import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image

from .origins import make_origin_record, verify_approved_asset, write_origins
from .pipeline import cutout_image, derive_ui, render_prompt, write_images


def new_file(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prompt = commands.add_parser("prompt", help="print a generation prompt; does not generate images")
    prompt.add_argument("kind", choices=("portrait", "full_body"))
    for field in ("name", "identity", "pose", "outfit"):
        prompt.add_argument("--" + field, required=True)
    cutout = commands.add_parser("cutout", help="isnet-anime cutout into a new file")
    cutout.add_argument("source", type=Path)
    cutout.add_argument("output", type=Path)
    derive = commands.add_parser("derive", help="derive UI previews from separate portrait and full-body PNGs")
    derive.add_argument("--portrait", type=Path, required=True)
    derive.add_argument("--full-body", type=Path, required=True)
    derive.add_argument("--actor-id", type=int, required=True)
    derive.add_argument("--anim-res", required=True)
    derive.add_argument("--skin-ui-res", type=int)
    derive.add_argument("--face-box", type=float, nargs=3, required=True, metavar=("X", "Y", "WIDTH"))
    derive.add_argument("--logo", type=Path)
    derive.add_argument("--introduction", type=Path)
    derive.add_argument("--output-root", type=Path, required=True)
    record = commands.add_parser("record", help="create pending origins from explicit manifest entries")
    record.add_argument("--asset-root", type=Path, required=True)
    record.add_argument("--spec", type=Path, required=True,
                        help="JSON list of {path, generatedAt, origin}; no embedded image bytes")
    record.add_argument("--output", type=Path, required=True)
    inspect = commands.add_parser("inspect", help="read texture bundle metadata")
    inspect.add_argument("bundle", type=Path)
    clone = commands.add_parser("clone", help="clone one user-approved PNG into a new bundle")
    clone.add_argument("--asset-root", type=Path, required=True)
    clone.add_argument("--entry", type=Path, required=True, help="one approved origins entry JSON")
    clone.add_argument("--template", type=Path, required=True)
    clone.add_argument("--template-sha256", required=True)
    clone.add_argument("--target", required=True)
    clone.add_argument("--size", type=int, nargs=2, required=True)
    clone.add_argument("--format", type=int, choices=(1, 47), required=True)
    clone.add_argument("--alpha-source", choices=("rgba", "alpha", "mask"), required=True)
    clone.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prompt":
        print(render_prompt(args.kind, name=args.name, identity=args.identity, pose=args.pose, outfit=args.outfit))
    elif args.command == "cutout":
        if args.output.exists():
            raise FileExistsError(args.output)
        with Image.open(args.source) as image:
            result = cutout_image(image)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("xb") as stream:
            result.save(stream, format="PNG")
        print(args.output)
    elif args.command == "derive":
        def read(path):
            if path is None:
                return None
            with Image.open(path) as image:
                return image.copy()
        images = derive_ui(read(args.portrait), read(args.full_body), actor_id=args.actor_id,
                           anim_res=args.anim_res, skin_ui_res=args.skin_ui_res, face_box=args.face_box, logo=read(args.logo),
                           introduction=read(args.introduction))
        print(json.dumps([str(p) for p in write_images(args.output_root, images)], indent=2))
    elif args.command == "record":
        if args.output.exists():
            raise FileExistsError(args.output)
        spec = json.loads(args.spec.read_text(encoding="utf-8"))
        entries = [make_origin_record(args.asset_root, row["path"], generated_at=row["generatedAt"], origin=row["origin"])
                   for row in spec]
        write_origins(args.output, entries)
        print(f"Recorded {len(entries)} pending images; no approval granted")
    elif args.command == "inspect":
        from .bundle import inspect_texture_bundle
        print(json.dumps(inspect_texture_bundle(args.bundle.read_bytes()), indent=2))
    elif args.command == "clone":
        from .bundle import clone_texture_bundle
        png = verify_approved_asset(json.loads(args.entry.read_text(encoding="utf-8")), args.asset_root)
        template = args.template.read_bytes()
        if hashlib.sha256(template).hexdigest() != args.template_sha256:
            raise ValueError("template SHA256 differs from reviewed configuration")
        raw, report = clone_texture_bundle(template, png, args.target, expected_size=tuple(args.size),
                                           expected_format=args.format, alpha_source=args.alpha_source)
        new_file(args.output, raw)
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

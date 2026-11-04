from __future__ import annotations

import copy
import importlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from PIL import Image, ImageDraw

from external_assets import external_path, require_file


def png(image):
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def silhouette(size=(1024, 1024)):
    image = Image.new("RGBA", size, (255, 255, 255, 0))
    ImageDraw.Draw(image).rectangle((size[0] // 4, size[1] // 8,
                                     size[0] * 3 // 4, size[1] * 7 // 8),
                                    fill=(20, 80, 160, 255))
    return image


class ArtApiTests(unittest.TestCase):
    def art(self, name):
        try:
            return importlib.import_module("tools.art." + name)
        except ModuleNotFoundError as exc:
            self.fail(f"Formal art pipeline is missing: {exc}")

    def test_origin_pending_cannot_be_packaged_and_approval_binds_exact_bytes(self):
        origins = self.art("origins")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "portrait.png").write_bytes(png(silhouette((64, 64))))
            record = origins.make_origin_record(
                root, "portrait.png", generated_at="2026-11-04T00:00:00+00:00",
                origin={"kind": "generated", "prompt": "A complete character, neutral pose",
                        "references": [{"path": "reference.png", "sha256": "a" * 64}]})
            self.assertEqual(record["approval"]["status"], "pending")
            with self.assertRaisesRegex(ValueError, "approv"):
                origins.verify_approved_asset(record, root)
            record["approval"] = {"status": "approved", "sha256": record["sha256"],
                                  "confirmedBy": "user", "confirmedAt": "2026-11-04T01:00:00+00:00",
                                  "evidence": "user reviewed portrait.png in this chat"}
            self.assertEqual(origins.verify_approved_asset(record, root), (root / "portrait.png").read_bytes())
            unbound = copy.deepcopy(record)
            unbound["approval"]["sha256"] = "b" * 64
            with self.assertRaisesRegex(ValueError, "hash|digest|SHA"):
                origins.verify_approved_asset(unbound, root)
            Image.new("RGBA", (64, 64), "red").save(root / "portrait.png")
            with self.assertRaisesRegex(ValueError, "hash|digest|SHA"):
                origins.verify_approved_asset(record, root)

    def test_origins_reject_traversal_and_missing_provenance(self):
        origins = self.art("origins")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ("../outside.png", "/outside.png", "C:/outside.png", "a\\b.png", "a//b.png"):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    origins.make_origin_record(root, name, generated_at="2026-11-04", origin={})
            (root / "portrait.png").write_bytes(png(silhouette((64, 64))))
            with self.assertRaisesRegex(ValueError, "provenance|origin|prompt"):
                origins.make_origin_record(root, "portrait.png", generated_at="2026-11-04", origin={"kind": "generated"})

    def test_origin_manifest_is_deterministic_and_keeps_pending_state(self):
        origins = self.art("origins")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ("b.png", "a.png"):
                (root / name).write_bytes(png(silhouette((64, 64))))
            records = [origins.make_origin_record(root, name, generated_at="2026-11-04T00:00:00Z",
                       origin={"kind": "generated", "prompt": "Full body", "references": []})
                       for name in ("b.png", "a.png")]
            origins.write_origins(root / "one.json", records)
            origins.write_origins(root / "two.json", list(reversed(records)))
            self.assertEqual((root / "one.json").read_bytes(), (root / "two.json").read_bytes())
            self.assertTrue(all(r["approval"]["status"] == "pending"
                                for r in json.loads((root / "one.json").read_text())["assets"]))

    def test_full_body_is_required_and_must_be_independent_of_portrait(self):
        pipeline = self.art("pipeline")
        image = silhouette()
        with self.assertRaisesRegex(ValueError, "full.body"):
            pipeline.derive_ui(image, None, actor_id=1501, anim_res="1501a", face_box=(400, 100, 200))
        with self.assertRaisesRegex(ValueError, "independent|full.body"):
            pipeline.derive_ui(image, image.copy(), actor_id=1501, anim_res="1501a", face_box=(400, 100, 200))

    def test_derivation_preserves_alpha_and_known_ui_sizes(self):
        pipeline = self.art("pipeline")
        portrait = silhouette()
        full = silhouette((768, 1024))
        full.putpixel((1, 1), (0, 0, 0, 128))
        result = pipeline.derive_ui(portrait, full, actor_id=1501, anim_res="1501a",
                                    face_box=(400, 100, 200),
                                    logo=Image.new("RGBA", (256, 256), (1, 2, 3, 128)),
                                    introduction=Image.new("RGBA", (512, 1024), (2, 3, 4, 128)))
        self.assertEqual(len(result), 14)
        expected = {"actorportrait/actor_portrait_1501a": (1024, 1024),
                    "actorheadbig/actor_head_big_1501a": (256, 512),
                    "actorheadrectangle/actor_head_1501a": (256, 256),
                    "actorgroup/selectteam_1501a": (256, 1024),
                    "actorportraitfull/actor_portrait_full_1501a": (1024, 1024),
                    "actorskinheadbig/actorskinhead_big_1501": (438, 530)}
        for key, size in expected.items():
            self.assertEqual(result[key].size, size, key)
        self.assertEqual(result["actorskinheadbig/actorskinhead_big_1501"].mode, "L")
        self.assertEqual(result["actorportrait/actor_portrait_1501a"].tobytes(), portrait.tobytes())
        self.assertEqual(result["actorlogo/actor_logo_1501a"].getchannel("A").getextrema(), (128, 128))
        self.assertEqual(result["actorportraitfull/actor_portrait_full_1501a"].getchannel("A").getextrema(), (0, 255))

    def test_cutout_validates_backend_output_and_does_not_mutate_original(self):
        pipeline = self.art("pipeline")
        original = Image.new("RGB", (64, 64), "white")
        before = original.tobytes()
        result = pipeline.cutout_image(original, remover=lambda image: silhouette((64, 64)))
        self.assertEqual(original.tobytes(), before)
        self.assertEqual(result.mode, "RGBA")
        self.assertEqual(result.getchannel("A").getextrema(), (0, 255))
        with self.assertRaisesRegex(ValueError, "alpha|transparent"):
            pipeline.cutout_image(original, remover=lambda image: image.convert("RGBA"))
        with self.assertRaisesRegex(ValueError, "size|dimension"):
            pipeline.cutout_image(original, remover=lambda image: silhouette((32, 32)))

    def test_prompt_separates_complete_full_body_and_demon_identity(self):
        pipeline = self.art("pipeline")
        text = pipeline.render_prompt("full_body", name="Sadao Maou", identity="Demon King form with horns",
                                      pose="standing with folded arms", outfit="canonical demon armor")
        self.assertIn("feet", text)
        self.assertIn("Demon King form with horns", text)
        self.assertIn("standing with folded arms", text)
        self.assertNotIn("cut off around", text)
        with self.assertRaises(ValueError):
            pipeline.render_prompt("unknown", name="x", identity="x", pose="x", outfit="x")

    def test_review_outputs_never_overwrite_an_existing_image(self):
        pipeline = self.art("pipeline")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            original = png(silhouette((64, 64)))
            (root / "existing.png").write_bytes(original)
            with self.assertRaises(FileExistsError):
                pipeline.write_images(root, {"new": silhouette((32, 32)), "existing": silhouette((32, 32))})
            self.assertFalse((root / "new.png").exists())
            self.assertEqual((root / "existing.png").read_bytes(), original)

    def test_cli_renders_review_prompt_without_image_or_service_changes(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([sys.executable, "-m", "tools.art", "prompt", "full_body",
                                "--name", "Mikoto", "--identity", "same face", "--pose", "standing",
                                "--outfit", "canonical"], cwd=root, capture_output=True,
                                text=True, encoding="utf-8", errors="replace")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Both feet", result.stdout)


class ArtBundleTests(ArtApiTests):
    def template(self, relative):
        root = external_path("LJJC_ASSET_ROOT", "analysis/ljjc_manifest_out/assets/Patch/assetbundle")
        return require_file(root / relative, "LJJC_ASSET_ROOT").read_bytes()

    def test_etc2_clone_has_unique_identity_format_size_alpha_and_repeatable_bytes(self):
        bundle = self.art("bundle")
        template = self.template("texture/actorportrait/actor_portrait_1502a")
        image = png(silhouette())
        args = dict(expected_size=(1024, 1024), expected_format=47, alpha_source="rgba")
        target = "assetbundle/texture/actorportrait/actor_portrait_1501a"
        first, report = bundle.clone_texture_bundle(template, image, target, **args)
        self.assertEqual(first, bundle.clone_texture_bundle(template, image, target, **args)[0])
        second, _ = bundle.clone_texture_bundle(template, image,
                    "assetbundle/texture/actorportrait/actor_portrait_1503a", **args)
        meta = bundle.inspect_texture_bundle(first)
        other = bundle.inspect_texture_bundle(second)
        source = bundle.inspect_texture_bundle(template)
        self.assertEqual(meta["bundleName"], target)
        self.assertEqual(meta["assetBundleName"], target)
        self.assertEqual(meta["textureName"], "actor_portrait_1501a")
        self.assertEqual(meta["containerPaths"], ["assets/" + target + ".png"])
        self.assertEqual(meta["size"], [1024, 1024])
        self.assertEqual(meta["format"], 47)
        self.assertEqual(meta["alphaExtrema"], [0, 255])
        self.assertEqual(len({meta["cabName"], other["cabName"], source["cabName"]}), 3)
        self.assertTrue(report["validated"])

    def test_alpha8_uses_explicit_alpha_not_luminance(self):
        import UnityPy
        bundle = self.art("bundle")
        template = self.template("texture/actorskinheadbig/actorskinhead_big_1502")
        image = Image.new("RGBA", (438, 530), (255, 255, 255, 0))
        ImageDraw.Draw(image).rectangle((100, 100, 300, 400), fill=(0, 0, 0, 200))
        raw, _ = bundle.clone_texture_bundle(template, png(image),
                    "assetbundle/texture/actorskinheadbig/actorskinhead_big_1501",
                    expected_size=(438, 530), expected_format=1, alpha_source="alpha")
        texture = next(o.read() for o in UnityPy.load(raw).objects if o.type.name == "Texture2D")
        self.assertEqual(texture.image.getchannel("A").tobytes(), image.getchannel("A").tobytes())
        self.assertEqual(bundle.inspect_texture_bundle(raw)["format"], 1)
        raw2, _ = bundle.clone_texture_bundle(template, png(image.getchannel("A")),
                    "assetbundle/texture/actorskinheadbig/actorskinhead_big_1501",
                    expected_size=(438, 530), expected_format=1, alpha_source="mask")
        self.assertEqual(raw, raw2)

    def test_bundle_rejects_unsafe_target_wrong_size_or_format(self):
        bundle = self.art("bundle")
        template = self.template("texture/actorportrait/actor_portrait_1502a")
        image = png(silhouette())
        target = "assetbundle/texture/actorportrait/actor_portrait_1501a"
        for bad in ("../bad", "assetbundle/texture/../bad", "assetbundle/data/lua/bad", "C:/bad"):
            with self.subTest(target=bad), self.assertRaises(ValueError):
                bundle.clone_texture_bundle(template, image, bad, expected_size=(1024, 1024), expected_format=47, alpha_source="rgba")
        with self.assertRaisesRegex(ValueError, "size|dimension"):
            bundle.clone_texture_bundle(template, png(silhouette((64, 64))), target,
                                        expected_size=(1024, 1024), expected_format=47, alpha_source="rgba")
        with self.assertRaisesRegex(ValueError, "format"):
            bundle.clone_texture_bundle(template, image, target,
                                        expected_size=(1024, 1024), expected_format=1, alpha_source="alpha")


if __name__ == "__main__":
    unittest.main()

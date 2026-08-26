"""Generate storyboard keyframes with the installed ComfyUI image models."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

_CHARACTER_ALIASES = {
    "\u79e6\u98ce": "Qin Feng",
    "\u6797\u6d6a": "Lin Lang",
    "\u79e6\u4e09\u79cb": "Qin Sanqiu",
    "\u6797\u6dd1\u5a49": "Lin Shuwan",
}

try:
    from .generate_samples import (
        DEFAULT_MODEL_ID,
        MODEL_PRESETS,
        ComfyClient,
        build_kontext_workflow,
        build_sdxl_workflow,
        build_workflow,
    )
except ImportError:
    from generate_samples import (  # type: ignore[no-redef]
        DEFAULT_MODEL_ID,
        MODEL_PRESETS,
        ComfyClient,
        build_kontext_workflow,
        build_sdxl_workflow,
        build_workflow,
    )


def _shot_character_names(
    shot: dict[str, Any],
    profiles: dict[str, str],
) -> list[str]:
    characters = shot.get("characters")
    names = [
        str(item.get("name") or "").strip()
        for item in (characters if isinstance(characters, list) else [])
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    ]
    continuity = shot.get("continuity_plan")
    continuity = continuity if isinstance(continuity, dict) else {}
    signature = str(continuity.get("cast_signature") or "")
    names.extend(part.strip() for part in signature.split("|") if part.strip())
    if not names:
        text = " ".join(
            str(shot.get(key) or "")
            for key in ("scene_description", "dialogue", "image_prompt")
        )
        names.extend(name for name in profiles if name in text)
    return list(dict.fromkeys(names))


def _identity_prompt(
    shot: dict[str, Any],
    profiles: dict[str, str],
    fingerprints: dict[str, str],
) -> str:
    clauses: list[str] = []
    sex_map: list[str] = []
    for name in _shot_character_names(shot, profiles):
        fingerprint = str(
            fingerprints.get(name) or profiles.get(name) or ""
        ).strip()
        if fingerprint:
            identity_text = fingerprint.lower()
            alias = _CHARACTER_ALIASES.get(name, name)
            display_name = f"{alias} ({name})" if alias != name else name
            if any(
                term in identity_text
                for term in (
                    "pretty-boy",
                    "boy",
                    "nobleman",
                    "male",
                    "man",
                    "hero",
                    "guard",
                    "warrior",
                )
            ) and not any(
                term in identity_text
                for term in ("woman", "girl", "female", "swordswoman", "heroine")
            ):
                sex_lock = (
                    "unmistakably male young man with masculine male facial bone "
                    "structure, flat male chest, visible male neck and Adam's apple, "
                    "no feminine face, no woman, no female body"
                )
                sex_map.append(f"{alias} = MALE young man")
            elif any(
                term in identity_text
                for term in ("woman", "girl", "female", "swordswoman", "heroine")
            ):
                sex_lock = (
                    "unmistakably female young woman; never reuse a male character's "
                    "face, jaw, body shape or costume"
                )
                sex_map.append(f"{alias} = FEMALE young woman")
            else:
                sex_lock = ""
            clauses.append(
                f"Identity lock — {display_name}: {sex_lock}; {fingerprint[:500]}"
                if sex_lock
                else f"Identity lock — {display_name}: {fingerprint[:500]}"
            )
    if not clauses:
        return ""
    clauses.append(
        "Every named character must remain immediately distinguishable: do not "
        "blend, average, swap or reuse their face geometry, eye shape, nose, jaw, "
        "hair silhouette, costume palette or signature accessories. Never copy "
        "one character's face or clothing onto another character."
    )
    if sex_map:
        clauses.append(
            "Unambiguous cast sex map for this exact frame: "
            + "; ".join(sex_map)
            + ". The English names in the scene prompt refer to these same people."
        )
    return ". ".join(clauses)


def _shot_prompt(
    shot: dict[str, Any],
    style_prompt: str,
    character_profiles: dict[str, str] | None = None,
    character_visual_fingerprints: dict[str, str] | None = None,
    frame_role: str = "start",
) -> str:
    profiles = character_profiles or {}
    fingerprints = character_visual_fingerprints or {}
    character_names = _shot_character_names(shot, profiles)
    shot_number = int(shot.get("shot_number") or 0)
    audio_generation = shot.get("audio_generation")
    audio_generation = (
        audio_generation if isinstance(audio_generation, dict) else {}
    )
    audio_mode = str(audio_generation.get("mode") or "").strip()
    speaker = str(audio_generation.get("speaker") or "").strip()
    offscreen_reaction = (
        audio_mode == "dialogue"
        and bool(speaker)
        and speaker not in character_names
        and bool(character_names)
    )
    coverage_character = (
        speaker
        if speaker in character_names
        else character_names[0]
        if offscreen_reaction
        else ""
    )
    dialogue_coverage = (
        bool(coverage_character)
        and len(character_names) > 1
        and shot_number not in {10, 14}
    )
    video_generation = shot.get("video_generation")
    video_generation = (
        video_generation if isinstance(video_generation, dict) else {}
    )
    end_prompt = str(video_generation.get("end_frame_prompt") or "").strip()
    if frame_role == "end" and end_prompt:
        prompt = end_prompt
        dialogue_coverage = False
    elif dialogue_coverage:
        speaker_alias = _CHARACTER_ALIASES.get(
            coverage_character,
            coverage_character,
        )
        performance = (
            "silent listening reaction with closed relaxed lips"
            if offscreen_reaction
            else "natural restrained dialogue gesture"
        )
        prompt = (
            "strict single-person dialogue coverage shot, exactly one visible young "
            f"male actor: {speaker_alias}, medium close-up, clear frontal or "
            "three-quarter masculine face, looking toward an off-camera listener, "
            f"{performance}, established historical-fantasy "
            "location softly blurred behind him, zero other people, zero background "
            "faces, zero shoulders or hands entering from the image edges"
        )
    else:
        prompt = str(shot.get("image_prompt") or "").strip()
    if not prompt:
        prompt = (
            "masterpiece, best quality, photorealistic live-action Chinese "
            f"xianxia cinematic scene, {shot.get('scene_description', '')}, "
            f"{shot.get('camera_angle', 'medium shot')}, natural skin texture, "
            "cinematic lighting, coherent anatomy, no text, no watermark"
        )
    if "both feet" in prompt.lower() or "complete standing figure" in prompt.lower():
        prompt = (
            "TOP PRIORITY CAMERA FRAMING — extreme wide full-body shot, camera at "
            "least eight meters away, the actor occupies no more than 55 percent of "
            "the frame height, show the complete person from hair to both shoes, show "
            "both feet touching the ground and a clear strip of ground below the soles; "
            "never crop knees, calves, ankles or footwear. "
            f"{prompt}"
        )
    if style_prompt and style_prompt.lower() not in prompt.lower():
        prompt = f"{prompt.rstrip(' ,.')}, {style_prompt}"
    framing_text = " ".join(
        str(shot.get(key) or "")
        for key in ("camera_angle", "scene_description", "image_prompt")
    ).lower()
    is_detail_insert = bool(
        re.search(
            r"\b(?:macro|insert|pov|top[- ]down|detail shot|extreme close-up)\b",
            framing_text,
        )
    )
    identity_shot = shot
    if dialogue_coverage:
        identity_shot = dict(shot)
        identity_shot["characters"] = [{"name": coverage_character}]
        identity_continuity = shot.get("continuity_plan")
        identity_continuity = (
            dict(identity_continuity)
            if isinstance(identity_continuity, dict)
            else {}
        )
        identity_continuity["cast_signature"] = coverage_character
        identity_shot["continuity_plan"] = identity_continuity
    identity = (
        ""
        if is_detail_insert
        else _identity_prompt(identity_shot, profiles, fingerprints)
    )
    if identity and "Identity lock" not in prompt:
        prompt = f"{prompt.rstrip(' ,.')}. {identity}"
    is_wide = bool(
        re.search(
            r"\b(?:wide|long shot|establishing|environmental)\b",
            framing_text,
        )
    )
    if is_detail_insert:
        framing_guard = (
            "honor the requested prop or detail insert exactly; show only the "
            "described hand, sleeve, plant, document or object and do not invent a "
            "full actor, portrait, extra face or extra foreground person"
        )
        cast_guard = (
            "show zero full people and zero faces; preserve only the described "
            "costume color or hand as an edge-of-frame continuity cue"
        )
    elif is_wide:
        framing_guard = (
            "honor the requested wide composition and full environment; named "
            "characters remain recognizable through distinct gender, silhouette, "
            "hair and costume without turning the shot into a centered portrait"
        )
        cast_guard = (
            "include exactly the named foreground cast and no invented foreground "
            "people"
        )
    else:
        framing_guard = (
            "when a named character's face is in frame it is fully visible and "
            "unobstructed, both eyes have sharp pupils and catchlights, the complete "
            "nose, mouth, forehead and chin remain readable, and the face is large "
            "enough to retain fine features during animation"
        )
        cast_guard = (
            "include exactly the named foreground cast and no invented foreground "
            "people"
        )
    evidence_guard = ""
    if "seven-leaf" in framing_text or "七叶" in framing_text:
        evidence_guard = (
            "exact botanical evidence: exactly seven separate visible leaf blades, "
            "all seven individually countable, curled and yellowing, no extra leaves, "
            "no flowers, no blossoms and no buds; the cracked stem remains planted "
            "and leaning in dark field soil; one pale-cyan sleeve and fingertips may "
            "enter only at the outer edge, but no hand holds or uproots the plant"
        )
    elif any(term in framing_text for term in ("irrigation", "channel", "水道", "灌溉沟")):
        evidence_guard = (
            "a narrow ancient irrigation channel is visibly half blocked by loose "
            "stones and silt; only a thin weak trickle bends around the obstruction, "
            "no waterfall, no strong current, no book, no paper and no sign"
        )
        if shot_number == 6 or "pointing hand" in framing_text:
            evidence_guard += (
                "; one pale-cyan-sleeved pointing hand enters from the far left edge"
            )

    all_named_male = bool(character_names) and all(
        any(
            marker in str(fingerprints.get(name) or profiles.get(name) or "").lower()
            for marker in ("male", "man", "boy", "nobleman", "guard", "hero")
        )
        and not any(
            marker in str(fingerprints.get(name) or profiles.get(name) or "").lower()
            for marker in ("female", "woman", "girl", "swordswoman", "heroine")
        )
        for name in character_names
    )
    multi_cast_guard = ""
    if all_named_male:
        multi_cast_guard = (
            "GLOBAL CAST RULE: every visible human in this frame is a young adult "
            "MALE actor; zero women, zero girls, zero feminine faces or female body "
            "shapes. Do not feminize the pale-cyan or teal-robed young man. "
        )
        name_set = set(character_names)
        if len(character_names) == 1:
            only_name = character_names[0]
            only_alias = _CHARACTER_ALIASES.get(only_name, only_name)
            multi_cast_guard += (
                f"The sole named actor is {only_alias}, a handsome young MALE actor "
                "with an unmistakably masculine face, straight masculine brows, a "
                "defined male jaw, visible male neck and flat male chest; no makeup, "
                "no feminine facial proportions and no female body. Exactly one person "
                "is present; zero other people, zero off-screen hands, zero extra arms "
                "and no hand or finger may enter from any image edge."
            )
        elif shot_number == 11 and name_set == {"秦风", "秦三秋"}:
            multi_cast_guard += (
                "Exactly two young MEN walking left-to-right, both seen strictly from "
                "behind or three-quarter back with no readable faces: Qin Feng is the "
                "slim deep-teal and pale-cyan figure on the LEFT; Qin Sanqiu is the "
                "broader dark-brown armored figure half a step behind on the RIGHT. "
                "Zero women, zero additional people and no frontal faces."
            )
        elif shot_number == 10 and name_set == {"秦风", "秦三秋"}:
            multi_cast_guard += (
                    "Exactly two foreground MEN in an over-the-shoulder composition: "
                    "LEFT foreground is Qin Feng, an 18-year-old male youth in deep-teal "
                    "and pale-cyan robes, seen strictly from behind with only the back of "
                    "his head and male shoulders visible, no face visible; RIGHT is Qin "
                    "Sanqiu, a broader 21-year-old male guard in dark-brown leather armor, "
                    "facing camera with his full male face clear for dialogue animation."
            )
        elif shot_number == 14 and {"林浪", "秦风", "秦三秋"}.issubset(
            name_set
        ):
            multi_cast_guard += (
                    "LEFT foreground is Lin Lang, a 19-year-old male nobleman in royal "
                    "blue, facing camera with his full male face clear for dialogue "
                    "animation. CENTER-RIGHT is Qin Feng in deep-teal and pale-cyan robes "
                    "and FAR RIGHT is Qin Sanqiu in dark-brown armor; both are distant "
                    "young men seen from behind or as masculine side silhouettes, with no "
                    "readable frontal faces. All followers are male. Preserve the three "
                "distinct costume palettes."
            )
        elif dialogue_coverage:
            speaker_alias = _CHARACTER_ALIASES.get(
                coverage_character,
                coverage_character,
            )
            other_aliases = [
                _CHARACTER_ALIASES.get(name, name)
                for name in character_names
                if name != coverage_character
            ]
            performance_label = (
                "silent reacting young MAN"
                if offscreen_reaction
                else "speaking young MAN"
            )
            multi_cast_guard += (
                f"Dialogue-safe blocking: {speaker_alias}, the {performance_label}, "
                "is the only visible person and has a clear frontal or three-quarter "
                "male face, large enough for lip animation. Every other named man "
                f"({', '.join(other_aliases)}) remains completely off-camera with no "
                "body, shoulder, hand, silhouette or face visible. Preserve the story "
                "eyeline through the speaker's gaze and preserve his costume colors. "
                "Show exactly one visible young man and zero additional people, extras, "
                "women, background faces or partial bodies."
            )
    video_safe_suffix = (
        "video-safe composition guard, video-safe first frame for image-to-video "
        "animation, premium live-action "
        "Chinese historical fantasy drama, real human actors, "
        f"{framing_guard}, {cast_guard}, {evidence_guard}, {multi_cast_guard}, "
        "never change a named character's sex, natural anatomy, "
        "coherent hands, stable costume details, realistic skin pores, no anime, "
        "no illustration, no CGI, no text, no border, no watermark"
    )
    if "video-safe composition guard" not in prompt.lower():
        prompt = f"{prompt.rstrip(' ,.')}, {video_safe_suffix}"
    continuity = shot.get("continuity_plan")
    continuity = continuity if isinstance(continuity, dict) else {}
    keyframe_prompt = str(continuity.get("keyframe_prompt") or "").strip()
    if is_detail_insert:
        prompt = (
            "cinematic physical-evidence insert, strict macro or POV composition, "
            "the story object fills most of the frame, zero faces, zero full people, "
            f"{prompt}"
        )
    elif (
        keyframe_prompt
        and frame_role != "end"
        and not dialogue_coverage
        and keyframe_prompt.lower() not in prompt.lower()
    ):
        prompt = re.sub(
            r"\bstanding alone\b",
            "moving cautiously",
            prompt,
            flags=re.IGNORECASE,
        )
        prompt = re.sub(
            r"\b(?:neutral|upright|static) standing pose\b",
            "natural action-ready pose",
            prompt,
            flags=re.IGNORECASE,
        )
        prompt = f"{keyframe_prompt.rstrip(' ,.')}, {prompt}"
    elif frame_role != "end" and "action-ready keyframe" not in prompt.lower():
        prompt = (
            f"{prompt.rstrip(' ,.')}, cinematic action-ready keyframe captured "
            "immediately before the main action, natural asymmetrical body weight, "
            "clear line of action, purposeful hands away from the face, layered "
            "foreground midground and background, open movement space, candid "
            "dramatic moment, not a posed portrait, not a rigid symmetrical stance"
        )
    if all_named_male and (dialogue_coverage or shot_number == 11):
        prompt = (
            "TOP PRIORITY DIALOGUE CAST AND CAMERA RULE — obey this before every "
            f"later scene phrase: {multi_cast_guard} {prompt}"
        )
    if frame_role == "end":
        prompt = (
            "STRICT END-FRAME CONTINUITY RULE: this is not a new shot. Preserve the "
            "start frame's exact cast identities, costume, props, set, lighting, lens, "
            "camera position and screen direction. Change only the requested action "
            f"state by one physically reachable beat. {prompt}"
        )
    return prompt


def _cast_reference_strategy(
    *,
    reference_mode: str,
    reference_image: str,
    architecture: str,
    prompt: str,
) -> str:
    """Return how a cast portrait should influence one image generation job."""

    if not reference_image or reference_mode != "cast_selection":
        return "none"
    lowered = prompt.lower()
    if (
        architecture == "sdxl"
        and "unmistakably male" not in lowered
        and "= male young man" not in lowered
    ):
        return "identity_adapter"
    if "strict single-person" in lowered:
        return "img2img"
    return "prompt_only"


def _annotated_reference(image: dict[str, Any]) -> str:
    filename = str(image.get("filename") or "").strip()
    subfolder = str(image.get("subfolder") or "").strip().strip("/\\")
    image_type = str(image.get("type") or "output").strip()
    path = f"{subfolder}/{filename}" if subfolder else filename
    return f"{path} [{image_type}]" if image_type != "input" else path


def _kontext_end_prompt(shot: dict[str, Any]) -> str:
    """Return a concise edit command so action is not buried by style prose."""

    video = shot.get("video_generation")
    video = video if isinstance(video, dict) else {}
    end_state = str(video.get("end_frame_prompt") or "").strip()
    action = str(
        video.get("subject_motion")
        or video.get("motion_prompt")
        or shot.get("scene_description")
        or ""
    ).strip()
    locomotion = any(
        term in f"{action} {end_state}".lower()
        for term in (
            "walk",
            "walking",
            "step",
            "stride",
            "run",
            "\u8d70",
            "\u8dd1",
            "\u8fc8\u6b65",
        )
    )
    pose_edit = (
        "Make one unmistakable completed step. Reverse the visible leading and "
        "trailing legs from the input image: the foot that is behind in the input "
        "must now be one full stride forward and planted; the former leading foot "
        "must now trail behind. Keep both feet visible on the ground with realistic "
        "weight transfer. "
        if locomotion
        else "Make the requested completed body pose clearly different from the input "
        "by one small, physically reachable action beat. "
    )
    return (
        "Edit the provided image; do not create a new shot. "
        f"{pose_edit}"
        f"Completed action: {end_state or action}. "
        "Keep the exact same person's face, age, hair, costume and body proportions. "
        "Keep the exact same background, buildings, path, props, lighting, camera "
        "position, lens, framing and image direction. Do not zoom, crop, recenter or "
        "redesign anything. Change only the action pose and the tiny physically "
        "necessary cloth movement. No extra person, object, limb or costume change."
    )[:2200]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--episode", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--comfy-url", default="http://127.0.0.1:8188")
    parser.add_argument("--shot-number", type=int, action="append", dest="shots")
    parser.add_argument(
        "--model",
        action="append",
        choices=tuple(MODEL_PRESETS),
        dest="models",
    )
    parser.add_argument("--candidate-count", type=int, default=1)
    parser.add_argument("--seed", type=int, default=20260728)
    parser.add_argument("--width", type=int, default=832)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--style-prompt", default="")
    parser.add_argument("--frame-role", choices=("start", "end"), default="start")
    parser.add_argument(
        "--end-frame-editor",
        choices=("legacy", "kontext"),
        default="legacy",
    )
    args = parser.parse_args()

    episode = json.loads(args.episode.read_text(encoding="utf-8-sig"))
    episode_number = int(episode.get("episode_number") or 1)
    character_profiles = episode.get("character_profiles")
    character_profiles = (
        {
            str(name): str(value)
            for name, value in character_profiles.items()
        }
        if isinstance(character_profiles, dict)
        else {}
    )
    fingerprints = episode.get("character_visual_fingerprints")
    fingerprints = (
        {
            str(name): str(value)
            for name, value in fingerprints.items()
        }
        if isinstance(fingerprints, dict)
        else {}
    )
    wanted = set(args.shots or [])
    shots = [
        item
        for index, item in enumerate(episode.get("shots") or [], start=1)
        if isinstance(item, dict)
        and (
            not wanted
            or int(item.get("shot_number") or index) in wanted
        )
    ]
    if not shots:
        raise ValueError("No storyboard shots selected")

    model_ids = list(dict.fromkeys(args.models or [DEFAULT_MODEL_ID]))
    use_kontext = args.frame_role == "end" and args.end_frame_editor == "kontext"
    execution_model_ids = ["flux_kontext"] if use_kontext else model_ids
    execution_models = {
        **MODEL_PRESETS,
        "flux_kontext": {
            "label": "FLUX.1 Kontext Dev FP8",
            "file": "flux1-dev-kontext_fp8_scaled.safetensors",
            "architecture": "flux_kontext",
        },
    }
    candidate_count = max(1, min(args.candidate_count, 4))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    client = ComfyClient(args.comfy_url)
    client.health()
    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    manifest: dict[str, Any] = {
        "schema_version": "1.0",
        "kind": (
            "storyboard_end_keyframes"
            if args.frame_role == "end"
            else "storyboard_keyframes"
        ),
        "frame_role": args.frame_role,
        "episode_number": episode_number,
        "width": args.width,
        "height": args.height,
        "generated_at": generated_at,
        "models": [
            {
                "id": model_id,
                "label": execution_models[model_id]["label"],
                "file": execution_models[model_id]["file"],
                "architecture": execution_models[model_id]["architecture"],
            }
            for model_id in execution_model_ids
        ],
        "images": [],
        "continuity_mode": (
            "flux_kontext_reference_edit"
            if use_kontext
            else "previous_in_group_img2img"
        ),
    }

    total = len(shots) * len(execution_model_ids) * candidate_count
    completed = 0
    generated_references: dict[tuple[str, str, int], dict[str, Any]] = {}
    for fallback_number, shot in enumerate(shots, start=1):
        shot_number = int(shot.get("shot_number") or fallback_number)
        prompt = (
            _kontext_end_prompt(shot)
            if use_kontext
            else _shot_prompt(
                shot,
                args.style_prompt,
                character_profiles,
                fingerprints,
                args.frame_role,
            )
        )
        continuity = shot.get("continuity_plan")
        continuity = continuity if isinstance(continuity, dict) else {}
        group_id = str(continuity.get("group_id") or f"shot_{shot_number:03d}")
        reference_mode = str(continuity.get("reference_mode") or "none")
        reference_shot = int(continuity.get("reference_shot_number") or 0)
        reference_denoise = max(
            0.45,
            min(float(continuity.get("reference_denoise") or 0.76), 0.95),
        )
        external_reference = str(continuity.get("reference_image") or "").strip()
        for model_index, model_id in enumerate(execution_model_ids):
            model = execution_models[model_id]
            for candidate_index in range(1, candidate_count + 1):
                prior = generated_references.get(
                    (group_id, model_id, candidate_index)
                )
                reference_image = (
                    _annotated_reference(prior)
                    if prior and reference_mode == "previous_in_group"
                    else external_reference
                    if reference_mode in {"previous_in_group", "cast_selection"}
                    else ""
                )
                cast_strategy = _cast_reference_strategy(
                    reference_mode=reference_mode,
                    reference_image=reference_image,
                    architecture=model["architecture"],
                    prompt=prompt,
                )
                identity_reference = cast_strategy == "identity_adapter"
                workflow_reference = (
                    reference_image
                    if reference_mode != "cast_selection"
                    or cast_strategy in {"identity_adapter", "img2img"}
                    else ""
                )
                seed = (
                    args.seed
                    + episode_number * 100_000
                    + shot_number * 1_000
                    + model_index * 100_000_000
                    + candidate_index * 97
                )
                role_suffix = "_end" if args.frame_role == "end" else ""
                stem = (
                    f"episode_{episode_number:03d}_shot_{shot_number:03d}_"
                    f"{model_id}{role_suffix}_candidate_{candidate_index:02d}"
                )
                destination = args.output_dir / f"{stem}.png"
                prefix = f"storyboard/{stem}"
                if model["architecture"] == "flux_kontext":
                    workflow = build_kontext_workflow(
                        prompt,
                        seed=seed,
                        width=args.width,
                        height=args.height,
                        filename_prefix=prefix,
                        reference_image=workflow_reference,
                    )
                elif model["architecture"] == "flux":
                    workflow = build_workflow(
                        prompt,
                        seed=seed,
                        width=args.width,
                        height=args.height,
                        filename_prefix=prefix,
                        model=model["file"],
                        reference_image=workflow_reference,
                        denoise=(
                            reference_denoise if workflow_reference else 1.0
                        ),
                    )
                else:
                    workflow = build_sdxl_workflow(
                        prompt,
                        seed=seed,
                        width=args.width,
                        height=args.height,
                        filename_prefix=prefix,
                        checkpoint=model["file"],
                        reference_image=workflow_reference,
                        denoise=(
                            reference_denoise if workflow_reference else 1.0
                        ),
                        identity_reference=identity_reference,
                    )
                image = client.wait(client.queue(workflow))
                client.download(image, destination)
                generated_references[(group_id, model_id, candidate_index)] = image
                record_time = (
                    datetime.now().astimezone().isoformat(timespec="seconds")
                )
                manifest["images"].append(
                    {
                        "episode_number": episode_number,
                        "shot_number": shot_number,
                        "candidate": candidate_index,
                        "seed": seed,
                        "file": destination.name,
                        "model_id": model_id,
                        "model_label": model["label"],
                        "model_file": model["file"],
                        "prompt": prompt,
                        "frame_role": args.frame_role,
                        "continuity_group": group_id,
                        "reference_mode": (
                            "flux_kontext_reference_edit"
                            if model["architecture"] == "flux_kontext"
                            else "cast_selection_ipadapter"
                            if identity_reference
                            else "cast_selection_img2img"
                            if cast_strategy == "img2img"
                            else "cast_selection_prompt_only"
                            if reference_image and reference_mode == "cast_selection"
                            else "previous_in_group_img2img"
                            if workflow_reference
                            else "independent"
                        ),
                        "reference_shot_number": reference_shot,
                        "reference_image": reference_image,
                        "reference_denoise": (
                            reference_denoise if workflow_reference else 1.0
                        ),
                        "generated_at": record_time,
                    }
                )
                completed += 1
                print(
                    f"[PROGRESS] {completed}/{total} episode {episode_number} "
                    f"shot {shot_number} {model_id}",
                    flush=True,
                )

    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

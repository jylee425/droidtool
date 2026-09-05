#!/usr/bin/env python
"""B-MoCA rollout for hosted actors with optional app-state tools.

The rollout owns model calls, episode logging, and task iteration. Vendor
environment setup and mobile_use execution live in environment.py.
"""
from __future__ import annotations

import argparse
import gc
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import traceback
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.eval._common.logger import JsonlWriter, normalize_step_record
from scripts.eval._common.cli_command import compact_cli_result, run_cli_command
from scripts.eval._common.policy import ModelPolicyAdapter
from scripts.eval._common.prompt import (
    build_system_text,
    build_user_text,
    parse_response,
    pil_to_png_bytes,
)
from scripts.eval.b_moca.environment import (
    BMOCA_HOME,
    BMocaEnv,
    REPO,
    execute_tool_call,
    get_screenshot_pil,
    prepare_task_app_state,
    set_evaluator_adb_serial,
    switch_task,
)
from scripts.eval._common.app_state_tools import (  # noqa: E402
    AppStateToolRuntime,
    compact_tool_result,
    compact_tool_result_payload,
)
from scripts.eval._common.agent_assets import AGENT_TYPES, resolve_agent_assets  # noqa: E402


class _LaunchTimeout(Exception):
    pass


def _on_launch_timeout(signum, frame):
    raise _LaunchTimeout()


def _cleanup_vendor_runtime(avd_name: str, appium_port: int, adb_port: int) -> None:
    patterns = [
        f"appium --port {appium_port}",
    ]
    for pattern in patterns:
        subprocess.run(["pkill", "-f", pattern], check=False)
    time.sleep(2)
    for pattern in patterns:
        subprocess.run(["pkill", "-9", "-f", pattern], check=False)
    adb = Path(os.environ.get("ANDROID_SDK_ROOT", Path.home() / ".local/share/android/sdk")) / "platform-tools" / "adb"
    if adb.exists():
        subprocess.run(
            [str(adb), "-s", f"emulator-{adb_port}", "emu", "kill"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    # A timed-out constructor may leave its emulator process alive briefly.
    # Do not start the retry on the same console/ADB ports until that exact
    # process has exited, or the failed Coordinator's late destructor can kill
    # the replacement instance.
    console_port = adb_port - 1

    def matching_emulator_pids() -> list[int]:
        proc = subprocess.run(
            ["ps", "-eo", "pid=,args="],
            capture_output=True,
            text=True,
            check=False,
        )
        matches: list[int] = []
        port_tokens = (f"-ports {console_port},{adb_port}", f"-port {console_port}")
        for line in proc.stdout.splitlines():
            stripped = line.strip()
            if not stripped or "qemu-system" not in stripped:
                continue
            if not any(token in stripped for token in port_tokens):
                continue
            try:
                matches.append(int(stripped.split(None, 1)[0]))
            except (ValueError, IndexError):
                continue
        return matches

    deadline = time.monotonic() + 20.0
    pids = matching_emulator_pids()
    while pids and time.monotonic() < deadline:
        time.sleep(0.5)
        pids = matching_emulator_pids()
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    if pids:
        time.sleep(2)
    for pid in matching_emulator_pids():
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def _create_bmoca_env_with_retry(args, first_task_path: str) -> BMocaEnv:
    timeout_s = int(os.environ.get("BMOCA_ENV_LAUNCH_TIMEOUT", "240"))
    attempts = int(os.environ.get("BMOCA_ENV_LAUNCH_ATTEMPTS", "2"))
    last_error = None
    for attempt in range(1, attempts + 1):
        print(
            f"Creating BMocaEnv  avd={args.avd_name} "
            f"(attempt {attempt}/{attempts}, timeout {timeout_s}s) ...",
            flush=True,
        )
        t_env = time.time()
        old_handler = signal.signal(signal.SIGALRM, _on_launch_timeout)
        signal.alarm(timeout_s)
        try:
            env = BMocaEnv(
                task_path=first_task_path,
                avd_name=args.avd_name,
                state_type="pixel",
                action_tanh=False,
                run_headless=True,
                appium_port=args.appium_port,
                adb_port=args.adb_port,
                grpc_port=args.grpc_port,
            )
            signal.alarm(0)
            print(f"  Done in {time.time() - t_env:.1f}s\n", flush=True)
            return env
        except BaseException as exc:
            signal.alarm(0)
            last_error = exc
            print(f"  BMocaEnv launch failed: {exc!r}", flush=True)
            # Finalize any partially constructed vendor Coordinator before the
            # retry reuses its ports.
            gc.collect()
            _cleanup_vendor_runtime(args.avd_name, args.appium_port, args.adb_port)
            gc.collect()
        finally:
            signal.signal(signal.SIGALRM, old_handler)
    raise RuntimeError(f"BMocaEnv launch failed after {attempts} attempts") from last_error


def _save_step(
    episode_dir: Path,
    step_idx: int,
    img,
    system_text: str,
    user_text: str,
    response: str,
    action_text: str,
    tool_call: dict | None,
    reward: float,
    done: bool,
    t_step_start: float,
    t_infer_end: float,
    goal: str,
    task_name: str,
    env_id: str,
    actor_model: str,
    variant: str,
    exec_error: str | None = None,
    tool_result: dict | None = None,
) -> None:
    screen_fname = f"step_{step_idx:03d}_screen.png"
    img.save(episode_dir / screen_fname)
    step_data = {
        "step_index": step_idx,
        "env_id": env_id,
        "task_name": task_name,
        "goal": goal,
        "timestamp": t_step_start,
        "step_duration_s": round(time.time() - t_step_start, 3),
        "actor_inference_time_s": round(t_infer_end - t_step_start, 3),
        "screenshot_file": screen_fname,
        "screen_wh": list(img.size),
        "_run_config": {
            "actor": f"Gemini ({actor_model}) {variant}",
        },
        "actor_input": {
            "raw_input": {
                "system_text": system_text,
                "user_text": user_text,
                "screenshot": "<screenshot_png>",
            },
            "system_prompt": system_text,
            "user_prompt": user_text,
        },
        "actor_output": response,
        "action_text": action_text,
        "tool_call": tool_call,
        "tool_result": compact_tool_result_payload(tool_result),
        "exec_error": exec_error,
        "reward": reward,
        "done": done,
    }
    normalize_step_record(step_data)
    with open(episode_dir / f"step_{step_idx:03d}.json", "w") as f:
        json.dump(step_data, f, ensure_ascii=False, indent=2)


def run_episode(
    env,
    env_id: str,
    task_name: str,
    task_path: str,
    actor,
    args,
    episode_dir: Path | None = None,
    app_state_runtime: AppStateToolRuntime | None = None,
    goal_override: str | None = None,
) -> dict:
    goal = goal_override or ("Goal: " + task_name.split("/")[-1].replace("_", " "))
    t0 = time.time()
    if episode_dir is not None:
        if episode_dir.exists():
            shutil.rmtree(episode_dir)
        episode_dir.mkdir(parents=True, exist_ok=True)

    try:
        env.reset(target_env_id=env_id)
        serial = set_evaluator_adb_serial(env)
        if serial:
            print(f"      evaluator adb serial={serial}", flush=True)
    except Exception as e:
        traceback.print_exc()
        return {
            "task": task_name,
            "env_id": env_id,
            "success": False,
            "steps": 0,
            "time_s": round(time.time() - t0, 1),
            "error": str(e),
        }

    app_state_prepare = None
    task_app_prepare = None
    try:
        task_app_prepare = prepare_task_app_state(env, task_name)
        print(
            "      task app prepare "
            f"app={task_app_prepare.get('app')} "
            f"ok={task_app_prepare.get('ok')}",
            flush=True,
        )
    except Exception as exc:  # noqa: BLE001
        task_app_prepare = {"ok": False, "error": str(exc)}
        print(f"      task app prepare failed: {exc}", flush=True)

    # Settings state-transition evaluators must capture their baseline after
    # task-specific preparation. In particular, preparation resets brightness
    # to a stable maximum; capturing before that reset compares the mutation
    # against stale snapshot state and produces a false negative.
    if task_name.startswith("settings/") and serial:
        try:
            env._task_manager._evaluator.reset(serial)
            print("      settings evaluator baseline recaptured after task prepare", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"      settings evaluator baseline recapture failed: {exc}", flush=True)

    if app_state_runtime is not None:
        app_state_prepare = app_state_runtime.prepare_device_for_app_state_tools()
        print(
            "      app-state surface prepare "
            f"ok={app_state_prepare.get('ok')} "
            f"surfaces={len(app_state_prepare.get('surfaces') or [])}",
            flush=True,
        )

    try:
        task_max = env._task_manager._task.max_episode_steps
    except Exception:
        task_max = args.max_steps
    effective_max_steps = task_max + args.extra_steps if task_max > 0 else args.max_steps

    history: list[dict] = []
    steps_log: list[dict] = []
    success = False
    evaluator_success = False
    app_state_success_seen = False
    actor_terminal_status = None
    app_tool_defs = (
        app_state_runtime.tool_definitions()
        if app_state_runtime is not None
        else None
    )
    enable_cli_command = bool(getattr(args, "enable_cli_command", False))
    system_text = build_system_text(
        benchmark="b_moca",
        app_state_tools=app_tool_defs,
        require_app_state_verification=bool(app_tool_defs),
        enable_cli_command=enable_cli_command,
    )
    if args.skill_text:
        system_text += "\n\n# App GUI Skill Guide\n\n" + args.skill_text.strip()

    for step_idx in range(effective_max_steps):
        t_step = time.time()
        try:
            img = get_screenshot_pil(env)
        except Exception as e:
            print(f"      [step {step_idx}] screenshot error: {e}", flush=True)
            break

        user_text = build_user_text(goal, history)
        response = ""
        description = ""
        context = ""
        action_text = ""
        tool_call = None
        exec_error = None

        try:
            response = actor.generate(system_text, user_text, pil_to_png_bytes(img))
        except Exception as e:
            exec_error = f"actor: {e}"
            print(f"      [step {step_idx}] actor error: {e}", flush=True)
        t_infer = time.time()

        if response:
            description, context, action_text, tool_call = parse_response(response)
            history.append({
                "description": description,
                "context": context,
                "action": action_text,
            })
            print(
                f"  step {step_idx:02d} | {actor.provider} actor: "
                f"{response.splitlines()[0][:95]!r}",
                flush=True,
            )

        reward = 0.0
        done = False
        tool_result = None
        tool_name = ""
        if isinstance(tool_call, dict):
            tool_name = str(tool_call.get("name") or "")
            raw_mobile_args = tool_call.get("arguments") or {}
            if tool_name == "mobile_use" and isinstance(raw_mobile_args, dict):
                action_name = str(raw_mobile_args.get("action") or "")
                if action_name == "terminate":
                    actor_terminal_status = str(raw_mobile_args.get("status") or "")
        if tool_call is None and exec_error is None:
            exec_error = "no tool_call parsed"
        elif (
            tool_name
            and app_state_runtime is not None
            and tool_name in app_state_runtime.action_names
        ):
            raw_args = tool_call.get("arguments") or {}
            tool_args = raw_args if isinstance(raw_args, dict) else {}
            tool_result = app_state_runtime.call(tool_name, tool_args)
            history.append({
                "description": "App-state tool result",
                "context": compact_tool_result(tool_result),
                "action": f"Called {tool_name}",
            })
            print(
                f"      app-state tool {tool_name}: "
                f"{str(tool_result)[:180].replace(chr(10), ' ')}",
                flush=True,
            )
            try:
                timestep, _done, _answer = execute_tool_call(
                    env, {"name": "mobile_use", "arguments": {"action": "__refresh_state__"}}
                )
                if timestep is not None:
                    reward = float(timestep.curr_rew)
                if reward > 0.0:
                    evaluator_success = True
                    app_state_success_seen = True
            except Exception as e:
                traceback.print_exc()
                exec_error = f"app-state refresh: {e}"
        elif tool_name == "run_cli_command" and enable_cli_command:
            raw_args = tool_call.get("arguments") or {}
            tool_args = raw_args if isinstance(raw_args, dict) else {}
            serial = set_evaluator_adb_serial(env)
            tool_result = run_cli_command(
                tool_args,
                adb_path="adb",
                emulator_serial=serial,
            )
            history.append({
                "description": "CLI command result",
                "context": compact_cli_result(tool_result),
                "action": "Inspect the UI before deciding whether the task is complete.",
            })
            print(
                f"      cli command: {compact_cli_result(tool_result)[:180].replace(chr(10), ' ')}",
                flush=True,
            )
            try:
                timestep, _done, _answer = execute_tool_call(
                    env, {"name": "mobile_use", "arguments": {"action": "__refresh_state__"}}
                )
                if timestep is not None:
                    reward = float(timestep.curr_rew)
                if reward > 0.0:
                    evaluator_success = True
            except Exception as e:
                traceback.print_exc()
                exec_error = f"cli refresh: {e}"
        elif tool_call is not None:
            try:
                timestep, done, _answer = execute_tool_call(env, tool_call)
                if timestep is not None:
                    reward = float(timestep.curr_rew)
                if reward > 0.0:
                    evaluator_success = True
            except Exception as e:
                traceback.print_exc()
                exec_error = f"exec: {e}"

        if episode_dir is not None:
            _save_step(
                episode_dir, step_idx, img,
                system_text=system_text, user_text=user_text,
                response=response, action_text=action_text, tool_call=tool_call,
                reward=reward, done=done,
                t_step_start=t_step, t_infer_end=t_infer,
                goal=goal, task_name=task_name, env_id=env_id,
                actor_model=args.actor_model, variant=args.variant,
                exec_error=exec_error, tool_result=tool_result,
            )

        steps_log.append({
            "step": step_idx,
            "actor_output": response,
            "tool_call": tool_call,
            "tool_result": compact_tool_result_payload(tool_result),
            "reward": reward,
            "done": done,
            "exec_error": exec_error,
        })
        success = evaluator_success
        wait_for_actor_confirmation = (
            app_state_success_seen
            and bool(app_tool_defs)
            and args.require_app_tool_ui_verification
        )
        if done:
            break
        if success and not wait_for_actor_confirmation:
            break

    final_step_idx = len(steps_log)
    try:
        final_img = get_screenshot_pil(env)
        if episode_dir is not None:
            final_img.save(episode_dir / f"step_{final_step_idx:03d}_screen.png")
    except Exception as e:
        print(f"      [final screenshot] error: {e}", flush=True)

    elapsed = round(time.time() - t0, 1)
    print(f"      -> success={success}  steps={len(steps_log)}  time={elapsed}s", flush=True)
    result = {
        "task": task_name,
        "env_id": env_id,
        "success": success,
        "evaluator_success": evaluator_success,
        "vendor_evaluator_success": evaluator_success,
        "app_state_success_seen": app_state_success_seen,
        "actor_terminal_status": actor_terminal_status,
        "steps": len(steps_log),
        "time_s": elapsed,
        "steps_detail": steps_log,
    }
    if episode_dir is not None:
        episode_data = {
            "env_id": env_id,
            "task_name": task_name,
            "goal": goal,
            "n_steps": len(steps_log),
            "max_steps": effective_max_steps,
            "success": success,
            "evaluator_success": evaluator_success,
            "vendor_evaluator_success": evaluator_success,
            "app_state_success_seen": app_state_success_seen,
            "actor_terminal_status": actor_terminal_status,
            "reward": max((s["reward"] for s in steps_log), default=0.0),
            "error": None,
            "duration_s": elapsed,
            "started_ts": t0,
            "finished_ts": time.time(),
            "app_state_prepare": app_state_prepare,
            "task_app_prepare": task_app_prepare,
        }
        with open(episode_dir / "episode.json", "w") as f:
            json.dump(episode_data, f, ensure_ascii=False, indent=2)
    return result


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="B-MoCA Gemini actor-with-tool eval")
    p.add_argument("--avd_name", default="unified_environment")
    p.add_argument("--snapshot_name", default="apk_installed_base")
    p.add_argument("--env_prefix", default="test_env", choices=["train_env", "test_env"])
    p.add_argument("--env_ids", default="100,101,105")
    p.add_argument("--skip_apps", default="instagram,walmart")
    p.add_argument("--only_apps", default="")
    p.add_argument("--out_dir", required=True)
    p.add_argument("--agent_type", choices=AGENT_TYPES, default=None)
    p.add_argument("--log_dir", type=Path, default=None)
    p.add_argument("--app_slugs", default="")
    p.add_argument("--max_steps", type=int, default=15)
    p.add_argument("--extra_steps", type=int, default=5)
    p.add_argument("--appium_port", type=int, default=4723)
    p.add_argument("--adb_port", type=int, default=None)
    p.add_argument("--grpc_port", type=int, default=None)
    p.add_argument("--shard_idx", type=int, default=0)
    p.add_argument("--num_shards", type=int, default=1)
    p.add_argument("--gemini_model", default=os.environ.get("GEMINI_MODEL", "gemini-3.5-flash"))
    p.add_argument("--actor_model", default=os.environ.get("ACTOR_MODEL", ""))
    p.add_argument("--actor_max_tokens", type=int, default=int(os.environ.get("ACTOR_MAX_TOKENS", "4096")))
    p.add_argument("--max_tasks", type=int, default=0)
    p.add_argument(
        "--tasks",
        default="",
        help=(
            "Optional comma-separated task refs to run. Refs may be app/task_stem "
            "or bare task_stem when --only_apps selects a single app."
        ),
    )
    p.add_argument(
        "--tasks_file",
        type=Path,
        default=None,
        help="Optional newline-separated task refs to run. Useful for task names containing commas.",
    )
    p.add_argument("--app_tool_registry", type=Path, default=None)
    p.add_argument("--disable_app_tools", action="store_true")
    p.add_argument(
        "--skill_path",
        type=Path,
        default=None,
        help="App-specific GUI skill Markdown appended to the actor system prompt.",
    )
    p.add_argument(
        "--enable_cli_command",
        action="store_true",
        help="Expose run_cli_command alongside mobile_use for the GUI+CLI baseline.",
    )
    p.add_argument(
        "--no_require_app_tool_ui_verification",
        dest="require_app_tool_ui_verification",
        action="store_false",
        help=(
            "Disable the extra actor step loop after app-state tool evaluator "
            "success. By default, with-tools B-MoCA runs continue so the actor "
            "can verify the final state on the app UI before terminating."
        ),
    )
    p.set_defaults(require_app_tool_ui_verification=True)
    args = p.parse_args(argv)
    if not args.actor_model:
        args.actor_model = args.gemini_model
    args.skill_text = ""
    if args.agent_type is not None:
        if args.log_dir is None:
            raise SystemExit("B-MoCA --agent_type requires --log_dir")
        assets = resolve_agent_assets(
            agent_type=args.agent_type,
            log_dir=args.log_dir,
            app_slugs=args.app_slugs or args.only_apps,
            runtime_dir=Path(args.out_dir) / "_runtime_registry" / f"shard{args.shard_idx}",
        )
        args.disable_app_tools = assets.registry_path is None
        args.app_tool_registry = assets.registry_path
        args.enable_cli_command = assets.enable_cli_command
        args.skill_text = assets.skill_text
    if args.skill_path is not None:
        if not args.skill_path.is_file():
            raise SystemExit(f"B-MoCA skill file not found: {args.skill_path}")
        args.skill_text = args.skill_path.read_text(encoding="utf-8")
        print(f"[+] loaded GUI skill guide: {args.skill_path}", flush=True)

    env_ids = [args.snapshot_name]
    skip_apps = {a.strip().lower() for a in args.skip_apps.split(",") if a.strip()}
    only_apps = {a.strip().lower() for a in args.only_apps.split(",") if a.strip()}
    selected_tasks = {t.strip() for t in args.tasks.split(",") if t.strip()}
    if args.tasks_file is not None:
        selected_tasks.update(
            t.strip()
            for t in args.tasks_file.read_text(encoding="utf-8").splitlines()
            if t.strip()
        )

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    task_root = Path(BMOCA_HOME) / "asset" / "tasks"
    task_list: list[tuple[str, str, str | None]] = []
    for app_dir in sorted(task_root.iterdir()):
        if not app_dir.is_dir():
            continue
        if only_apps and app_dir.name.lower() not in only_apps:
            continue
        if not only_apps and app_dir.name.lower() in skip_apps:
            continue
        for task_path in sorted(app_dir.iterdir()):
            if str(task_path).endswith(".textproto"):
                task_ref = f"{app_dir.name}/{Path(task_path).stem}"
                if selected_tasks and task_ref not in selected_tasks and Path(task_path).stem not in selected_tasks:
                    continue
                task_list.append((task_ref, str(task_path), None))

    if args.num_shards > 1:
        task_list = [t for i, t in enumerate(task_list) if i % args.num_shards == args.shard_idx]
    if args.max_tasks > 0:
        task_list = task_list[: args.max_tasks]
    total_episodes = len(task_list) * len(env_ids)
    if not task_list:
        raise RuntimeError("No B-MoCA tasks selected.")

    print(f"\n{'=' * 60}", flush=True)
    args.variant = "actor_with_tool"
    print(f"  B-MoCA Gemini actor-with-tool", flush=True)
    print(f"  actor_model : {args.actor_model}", flush=True)
    print(f"  avd_name    : {args.avd_name}", flush=True)
    print(f"  env_prefix  : {args.env_prefix}", flush=True)
    print(f"  env_ids     : {env_ids}", flush=True)
    print(f"  tasks       : {len(task_list)}  (only={only_apps or 'all'}, skip={skip_apps})", flush=True)
    print(f"  task filter : {selected_tasks or 'none'}", flush=True)
    print(f"  episodes    : {total_episodes}", flush=True)
    print(f"  shard       : {args.shard_idx}/{args.num_shards}", flush=True)
    print(f"  max_steps   : task.max_episode_steps + {args.extra_steps} (fallback={args.max_steps})", flush=True)
    print(f"  out_dir     : {args.out_dir}", flush=True)
    print(f"  app tools   : {args.app_tool_registry if args.app_tool_registry and not args.disable_app_tools else 'disabled'}", flush=True)
    print(f"{'=' * 60}\n", flush=True)

    actor = ModelPolicyAdapter(
        model=args.actor_model,
        max_output_tokens=args.actor_max_tokens,
        temperature=0.0,
    )

    flatten_single_app_logs = len(only_apps) == 1
    single_app_name = next(iter(only_apps)) if flatten_single_app_logs else None
    if single_app_name:
        legacy_app_dir = out_dir / single_app_name
        if legacy_app_dir.exists():
            shutil.rmtree(legacy_app_dir)

    first_task_name, first_task_path, _first_goal_override = task_list[0]
    env = _create_bmoca_env_with_retry(args, first_task_path)

    app_state_runtime = None
    if args.app_tool_registry is not None and not args.disable_app_tools:
        if not args.app_tool_registry.exists():
            raise RuntimeError(f"app tool registry not found: {args.app_tool_registry}")
        android_sdk_for_tools = os.environ.get(
            "ANDROID_SDK_ROOT", str(Path.home() / ".local/share/android/sdk")
        )
        try:
            serial = env._coordinator._simulator.adb_device_name()
        except Exception:
            serial = f"emulator-{args.adb_port - 1}" if args.adb_port else None
        app_state_runtime = AppStateToolRuntime(
            args.app_tool_registry,
            repo_root=REPO,
            adb_path=f"{android_sdk_for_tools}/platform-tools/adb",
            emulator_serial=serial,
            work_dir=out_dir / "_tool_runtime",
        )
        print(
            "[+] loaded app-state tools: "
            + ", ".join(sorted(app_state_runtime.action_names)),
            flush=True,
        )

    suffix = f"_shard{args.shard_idx}" if args.num_shards > 1 else ""
    episode_summary_path = out_dir / f"episode_summary{suffix}.jsonl"
    if episode_summary_path.exists():
        episode_summary_path.unlink()
    ep_summary_writer = JsonlWriter(episode_summary_path)

    all_results: list[dict] = []
    ep_idx = 0
    for env_id in env_ids:
        print(f"\n{'-' * 60}", flush=True)
        print(f"  env_id: {env_id}", flush=True)
        print(f"{'-' * 60}", flush=True)
        for task_name, task_path, goal_override in task_list:
            ep_idx += 1
            print(f"\n[{ep_idx}/{total_episodes}] {env_id} / {task_name}", flush=True)
            if ep_idx > 1 or task_name != first_task_name:
                switch_task(env, task_path)
            result = run_episode(
                env,
                env_id,
                task_name,
                task_path,
                actor,
                args,
                out_dir / (task_name.split("/", 1)[1] if flatten_single_app_logs and "/" in task_name else task_name),
                app_state_runtime=app_state_runtime,
                goal_override=goal_override,
            )
            all_results.append(result)
            ep_summary_writer.write({k: v for k, v in result.items() if k != "steps_detail"})

    n_total = len(all_results)
    n_success = sum(1 for r in all_results if r.get("success"))
    sr = n_success / n_total * 100 if n_total else 0.0
    summary = {
        "gemini_model": args.gemini_model,
        "actor_model": args.actor_model,
        "variant": args.variant,
        "avd_name": args.avd_name,
        "env_ids": env_ids,
        "n_total": n_total,
        "n_success": n_success,
        "success_rate": round(sr, 2),
    }
    summary_file = out_dir / f"summary_shard{args.shard_idx}.json" if args.num_shards > 1 else out_dir / "summary.json"
    with open(summary_file, "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    ep_summary_writer.close()
    print(f"\n{'=' * 60}", flush=True)
    print(f"  DONE  success={n_success}/{n_total} ({sr:.1f}%)", flush=True)
    print(f"  episode_summary : {out_dir / f'episode_summary{suffix}.jsonl'}", flush=True)
    print(f"  summary         : {summary_file}", flush=True)
    print(f"{'=' * 60}", flush=True)


if __name__ == "__main__":
    status = 0
    try:
        main()
    except BaseException:
        status = 1
        import traceback

        traceback.print_exc()
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(status)

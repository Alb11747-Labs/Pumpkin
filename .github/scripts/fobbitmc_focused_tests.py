"""Run only selected regressions and reject missing, ignored, or zero-test passes."""

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET


# These names come from the b5 engine, the advance-anvil, shulker-dispenser,
# vehicle patches, tropical-fish variant/name correction, and native spectator
# action repair. A source pin without the integrated tests must fail.
RUST_TESTS = {
    "pumpkin-world": (
        "chunk::format::anvil::tests::a_chunk_declaring_no_length_is_an_error_not_a_panic",
        "chunk::format::anvil::tests::a_chunk_declaring_one_byte_too_many_is_an_error_not_a_panic",
        "chunk::format::anvil::tests::cached_full_rewrite_never_reuses_obsolete_sector_offsets",
    ),
    "pumpkin-data": (
        "item_stack::preservation_tests::unknown_nested_item_preserves_entire_container_component",
        "item_stack::preservation_tests::absent_and_removed_containers_are_not_opaque",
    ),
    "pumpkin-core": (
        "entity::player::advancement::tests::vanilla_history_survives_load_save_and_revoke",
        "entity::player::advancement::tests::saving_replaces_the_file_without_truncating_the_previous_history",
        "entity::player::advancement::tests::malformed_history_blocks_save_without_discarding_live_progress",
        "block::blocks::redstone::dispenser::tests::dispenser_refuses_opaque_shulker_without_consuming_supported_sibling",
        "block::blocks::redstone::dispenser::tests::dispenser_retains_complete_mob_buckets_in_overworld_and_nether",
        "world::entity_storage::tests::destroyed_world_owned_player_vehicle_is_not_recreated_from_saved_snapshot",
        "world::entity_storage::tests::player_vehicle_snapshot_loads_once_and_rejects_invalid_attachment_without_orphans",
        "entity::player::tests::successful_import_mount_consumes_replay_ownership_before_next_snapshot",
        "entity::player::tests::canonical_vehicle_claims_import_ownership_before_a_mount_can_be_cancelled",
        "world::entity_storage::tests::bucket_pickup_survives_interaction_writeback_and_player_data_reload",
        "world::entity_storage::tests::bucket_item_callback_preserves_stacked_milk_exchange",
        "world::entity_storage::tests::bucket_entity_interaction_preserves_creative_buckets",
        "world::entity_storage::tests::cancelled_bucket_entity_pickup_does_not_fall_through_to_item_callback",
        "world::entity_storage::tests::loaded_bucket_mobs_survive_distant_players_and_round_trip_vanilla_data",
        "world::entity_storage::tests::newly_released_bucket_mobs_gain_persistent_provenance",
        "world::entity_storage::tests::pufferfish_puff_transitions_resize_from_type_defaults",
        "world::entity_storage::tests::loaded_pufferfish_dimensions_keep_unpuffed_eye_out_of_adjacent_dirt",
        "world::entity_storage::tests::imported_diodes_survive_all_hopper_states_and_disk_reload",
        "world::entity_storage::tests::unsupported_diodes_still_break_and_drop_one_item",
        "world::entity_storage::tests::tropical_fish_bucket_capture_relog_release_preserves_variant_and_name",
    ),
    "pumpkin-protocol": (
        "codec::data_component::tests::tropical_fish_components_use_vanilla_packed_pattern_and_color_wire",
        "codec::data_component::tests::tropical_fish_components_reject_unknown_names_and_default_unknown_wire_ids",
        "java::server::play::spectator_action::tests::spectator_action_26_3_known_bytes",
        "java::server::play::spectator_action::tests::spectator_action_rejects_empty_and_truncated_varint",
        "java::server::play::spectator_action::tests::legacy_uuid_spectate_does_not_claim_native_action",
    ),
}

JAVA_TESTS = {
    "org.patchbukkit.events.EntityDeathMappingTest": (
        "serializedMobDeathProvidesLivingIdentityCauseAndAuthoritativeKiller",
        "playerDeathClearsPreviousKillCreditAndMapsMessageAndKeepInventory",
        "incompleteDeathPayloadNeverPublishesNullLivingEntity",
        "cancelledDamageDoesNotReplaceStoredCause",
    ),
    "org.patchbukkit.registry.RegistryPublicationTest": (
        "concurrentLookupWaitsForCompletedRegistryWhileInitializerCanReenter",
        "failedRegistryInitializationCanRetryWithoutPublishingPartialState",
    ),
}


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def run_logged(args, cwd, log, timeout):
    print("+ " + " ".join(map(str, args)), flush=True)
    # Direct files preserve diagnostics even when the outer build budget expires.
    with log.open("w", encoding="utf-8", newline="\n") as stream:
        result = subprocess.run(args, cwd=cwd, stdout=stream,
                                stderr=subprocess.STDOUT, timeout=timeout, check=False)
    output = log.read_text(encoding="utf-8")
    if result.returncode != 0:
        print(output, end="", flush=True)
        raise RuntimeError(f"Command failed ({result.returncode}); see {log}")
    return output


def require_listed(output, names):
    listed = Counter(match.group(1) for line in output.splitlines()
                     if (match := re.fullmatch(r"(.+): test", line)))
    missing = [name for name in names if listed[name] != 1]
    if missing:
        raise ValueError(f"Selected tests must appear exactly once in --list: {missing}")


def require_passed(output, name):
    lines = output.splitlines()
    passed = [match.group(1) for line in lines
              if (match := re.fullmatch(r"test (.+) \.\.\. ok", line))]
    summaries = [line for line in lines if line.startswith("test result:")]
    if (lines.count("running 1 test") != 1 or passed != [name]
            or len(summaries) != 1
            or re.fullmatch(r"test result: ok\. 1 passed; 0 failed; 0 ignored; "
                            r"0 measured; [0-9]+ filtered out; finished in .+", summaries[0]) is None):
        raise ValueError(f"Selected regression did not run and pass exactly once: {name}")


def record(mode, evidence):
    state_path = Path(os.environ["RUNNER_TEMP"]) / "fobbitmc-provenance.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state.setdefault("focusedRegressions", {})[mode] = evidence
    state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8", newline="\n")


def rust(target):
    root = Path.cwd()
    temp = Path(os.environ["RUNNER_TEMP"])
    args = ["cargo", "+" + os.environ["RUST_TOOLCHAIN"], "test", "--locked",
            "--release", "--target", target, "--lib", "--no-run", "--message-format=json"]
    for package in RUST_TESTS:
        args.extend(["-p", package])
    compile_log = temp / f"focused-compile-{target}.log"
    output = run_logged(args, root / "engine", compile_log, timeout=1500)
    binaries = {}
    for line in output.splitlines():
        if not line.startswith("{"):
            continue
        artifact = json.loads(line)
        if (artifact.get("reason") != "compiler-artifact" or not artifact.get("executable")
                or not artifact["profile"]["test"]):
            continue
        package = artifact["target"]["name"].replace("_", "-")
        if package not in RUST_TESTS or "lib" not in artifact["target"]["kind"]:
            continue
        if package in binaries:
            raise ValueError(f"Multiple test binaries for {package}")
        binaries[package] = artifact["executable"]
    if set(binaries) != set(RUST_TESTS):
        raise ValueError(f"Missing selected library test binaries: {set(RUST_TESTS) - set(binaries)}")

    evidence = {"compileCommand": args, "compileLog": compile_log.name,
                "compileLogSha256": sha256(compile_log), "libraries": {}, "cases": []}
    # Validate every selected name before starting any regression. No substring
    # filters, broad suites, ignored tests, or Cargo's filtered-zero success.
    for package, names in RUST_TESTS.items():
        log = temp / f"focused-list-{package}-{target}.log"
        output = run_logged([binaries[package], "--list", "--format", "terse", "--color", "never"],
                            root / "engine/crates" / package, log, timeout=30)
        require_listed(output, names)
        evidence["libraries"][package] = {"binarySha256": sha256(Path(binaries[package])),
                                          "listLog": log.name, "listLogSha256": sha256(log)}
    index = 0
    for package, names in RUST_TESTS.items():
        for name in names:
            log = temp / f"focused-rust-{index:02d}-{target}.log"
            args = [binaries[package], name, "--exact", "--test-threads=1", "--color", "never"]
            # Match Cargo's package working directory for relative fixtures.
            output = run_logged(args, root / "engine/crates" / package, log, timeout=120)
            print(output, end="", flush=True)
            require_passed(output, name)
            evidence["cases"].append({"package": package, "test": name, "count": 1,
                                      "log": log.name, "logSha256": sha256(log)})
            index += 1
    record("rust", evidence)


def require_java_reports(report_dir):
    expected = Counter((classname, name) for classname, names in JAVA_TESTS.items() for name in names)
    actual = Counter()
    reports = []
    for path in sorted(report_dir.glob("TEST-*.xml")):
        suite = ET.parse(path).getroot()
        for case in suite.iter("testcase"):
            if any(case.find(status) is not None for status in ("failure", "error", "skipped")):
                raise ValueError(f"Java regression failed or skipped: {path.name}: {case.attrib}")
            # Gradle includes () in non-parameterized JUnit method names.
            actual[(case.attrib["classname"], case.attrib["name"].removesuffix("()"))] += 1
        reports.append({"file": path.name, "sha256": sha256(path)})
    if actual != expected:
        raise ValueError(f"Java focused results mismatch; missing={expected - actual}, extra={actual - expected}")
    return reports


def java(target):
    root = Path.cwd()
    bridge_java = root / "bridge/java"
    report_dir = bridge_java / "patchbukkit/build/test-results/test"
    # A restored Gradle cache or incremental result cannot count as fresh tests.
    if list(report_dir.glob("TEST-*.xml")):
        raise ValueError("Java test reports already exist before the focused run")
    args = ["bash", "./gradlew", "--no-daemon", "--max-workers=6", "--no-build-cache",
            ":patchbukkit:jar", ":patchbukkit:test"]
    for classname in JAVA_TESTS:
        args.extend(["--tests", classname])
    log = Path(os.environ["RUNNER_TEMP"]) / f"focused-java-{target}.log"
    output = run_logged(args, bridge_java, log, timeout=600)
    print(output, end="", flush=True)
    reports = require_java_reports(report_dir)
    jar = bridge_java / "patchbukkit/build/libs/patchbukkit.jar"
    if not jar.is_file() or jar.stat().st_size == 0:
        raise ValueError("Java build did not produce the JAR required by the native bridge")
    record("java", {"command": args, "tests": JAVA_TESTS, "count": sum(map(len, JAVA_TESTS.values())),
                    "log": log.name, "logSha256": sha256(log), "reports": reports,
                    "jarSha256": sha256(jar)})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("rust", "java"))
    parser.add_argument("target", choices=("x86_64-unknown-linux-gnu",))
    options = parser.parse_args()
    {"rust": rust, "java": java}[options.mode](options.target)

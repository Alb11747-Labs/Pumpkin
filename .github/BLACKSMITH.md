# FobbitMC builds on Blacksmith

The organization fork runs `.github/workflows/fobbitmc-release.yml` from
`ci/blacksmith-release`. It builds public source only; world data, configuration,
credentials and production deployment are outside this workflow.

Engine and bridge inputs still refer to exact commits in `Alb11747/Pumpkin` and
`Alb11747/PatchBukkit`. The native plugin manifests and lockfiles pin the personal
engine repository, so moving those source URLs would require a separate coordinated
change. The organization forks do not replace those canonical source repositories.

The workflow starts only through `workflow_dispatch`. It uses 8-vCPU Blacksmith
Ubuntu 22.04 and Windows 2025 runners with six Cargo workers. Ubuntu 22.04 retains
the existing glibc 2.35 ceiling checked in the artifact manifest. Release options,
negative regression baselines, positive checks, Clippy and artifact verification
remain the same as the personal-fork workflow at
`c6a95a3dcbe7e4f90482c7575cae2ddbe9358af4`.

Cargo downloads and Gradle dependencies are cached between jobs. Cargo build
products remain within each ephemeral job so engine, bridge and exporter builds
can reuse dependencies; compiled products are not restored from a cross-run cache.
Artifacts retain the input hashes and workflow commit in `provenance.json`.

## Starting a build

Install the Blacksmith GitHub app for `Alb11747-Labs/Pumpkin`. Its runner group must
allow this public repository. The organization uses its own Blacksmith allowance
and billing; no paid subscription or optional paid disk cache is required here.

Run the workflow in `Alb11747-Labs/Pumpkin`, selecting `ci/blacksmith-release` and
the same immutable inputs as the corresponding personal-fork build. All source
commits and the vendored exporter pin must agree. Download the platform artifact
only after that platform's complete qualification job succeeds.

When migration qualification changes, port the workflow, helper and vendored
exporter together from the reviewed personal-fork workflow commit. Keep the
organization guard, Blacksmith runner labels, canonical engine checkout and local
build-product retention. Source integration and runtime verification remain with
the migration thread.

## Benchmark baseline

Personal-fork run `37833849117` took 92.9 minutes on Linux and 172.3 minutes on
Windows. Run `37860739257` uses the same inputs as the initial Blacksmith trial and
provides the closer comparison once complete. Compare job and compilation-step
durations as well as qualification results. This configuration changes runner
hardware, Cargo concurrency and dependency reuse together; it measures the full
setup, not the hardware speed alone. Cache-warm runs are a separate comparison.

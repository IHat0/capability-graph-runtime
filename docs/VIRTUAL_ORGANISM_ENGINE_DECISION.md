# Virtual Organism v1 — engine decision (2026-10-01)

This batch does not change or rerun either frozen Demo 2 investigation.

## Selected route

Use the actual PK-Sim native model builder and CVODES solver, invoked through its
headless CLI. Do not implement replacement compartment equations in Pulsate.
The isolated Windows preflight executed PK-Sim **12.3.173** and exported real
whole-body concentration time courses, the executable model (PKML), solver XML,
and numerical JSON/CSV. PK-Sim's `4Comp` model means four subcompartments per
organ, not a four-organ toy model.

The downloaded official portable archive SHA-256 is
`94a704b67c05042131cd5e51b4237b1633466a08cd6ec77506322b4d009c8567`.
The executable reports
`12.3.173+7617934a187ed8d58e4e6ca0a7dac1cc40b59296`.
Runtime binaries, downloaded models and numerical evidence are external/local
validation data, not source files to commit.

The engine accepts programmatically generated JSON building blocks, creates
individual/population physiology, and exports machine-readable output. Human
and rat simulations must be built independently from their species building
blocks. Changing parameters in a human executable model is **not** a valid way
to turn it into a rat.

Sources: [PK-Sim](https://github.com/Open-Systems-Pharmacology/PK-Sim),
[pinned release](https://github.com/Open-Systems-Pharmacology/PK-Sim/releases/tag/v12.3.173),
[native snapshot runner](https://github.com/Open-Systems-Pharmacology/PK-Sim/blob/v12.3.173/src/PKSim.CLI.Core/Services/JsonSimulationRunner.cs),
[species warning](https://www.open-systems-pharmacology.org/OSPSuite-R/articles/create-individual.html).

## Deployment and alternative evaluated

The current Windows host has .NET Framework 4.8.1 and runs the portable CLI
without administrator installation. Keep its executable and physiology database
pinned and hash them in each receipt. Configuration must explicitly locate an
engine and reviewed parameter/model catalogue; unavailable engines must not
produce invented curves.

The current EC2 Linux host has neither R nor dotnet. The supported Linux route
for **OSPSuite-R 12.4.5** requires R >=4.4, rSharp <=1.2.3 and .NET 8 with its
native dependencies. This is distinct from development-version 13/.NET 10.
This batch does not silently install a different engine version or change
production services. Windows-native validation does not establish Linux
deployment readiness.

[Pinned R requirements](https://github.com/Open-Systems-Pharmacology/OSPSuite-R/blob/v12.4.5/README.md),
[population interface](https://www.open-systems-pharmacology.org/OSPSuite-R/articles/create-run-population.html).

EPA [httk](https://github.com/USEPA/CompTox-ExpoCast-httk) is a credible open
alternative for high-throughput IVIVE/TK and population exposure. PK-Sim is
selected for the requested full organ physiology, species database and reusable
formulation/model building blocks, not because a simplified solver is easier.

## Licensing

PK-Sim and OSPSuite-R are GPLv2 software with additional attribution/liability
and trademark notices. Preserve upstream licence notices. Development use is
possible; distribution or bundling with Pulsate requires a proper licence review.
An external-process boundary is not a blanket claim of licence exemption.
[PK-Sim licence](https://github.com/Open-Systems-Pharmacology/PK-Sim/blob/v12.3.173/LICENSE).

## Evidence boundary

CLI exit code zero is not sufficient: the runner catches per-project failures.
Its default validation/negative-value checks are disabled, so Pulsate must verify
actual files, hashes, candidate/species/dose/time coverage, units and numerical
values independently. Each invocation gets a fresh isolated output directory:
the CLI removes existing CSV files in its output folder.

No automatic compound-name branch or reference parameter substitution for a
new candidate is acceptable. Unknown binding, clearance or ionization cannot be
filled by an LLM. Explicit exploratory assumptions remain assumptions. Native
physiology and partition predictions must be distinguished from measured drug
data. Solver convergence verifies computation, not biological truth or safety.

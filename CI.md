# CI quality and security gates

`ci-quality-gates.yml` is the gated pipeline for the gungnir library. It runs
on every push and pull request to `main`, and on manual dispatch. It mirrors
the pipeline in the consumer repos (adsb-to-wdgwars / Muninn, wigle-to-wdgwars,
meshcore-to-wdgwars / Heimdall), adapted for a dependency-free library.

## The gated pipeline

1. **Test and coverage.** Installs the package (`pip install -e .`) and dev
   dependencies, runs the suite under pytest across a **3.10 / 3.11 / 3.12**
   matrix (this library is consumed on all three), and produces `coverage.xml`
   on the 3.12 leg. The coverage report is uploaded as a build artifact.
2. **SonarCloud quality gate.** Downloads `coverage.xml` and runs the
   SonarCloud scanner. `sonar.qualitygate.wait=true` makes the scanner block
   on the gate, so a failed gate fails the job rather than only showing on the
   dashboard.
3. **Build sdist and wheel.** Runs `python -m build` and uploads the
   distribution. Declares `needs: [sonarcloud]`, so it only runs after the gate
   passes.

There is intentionally **no Snyk stage**: gungnir has no runtime dependencies,
so there is no software-composition surface to scan. Add one (copy it from a
sibling repo) the day a runtime dependency is pinned in `pyproject.toml`.

## Coverage gate

The local gate is a regression floor in `pyproject.toml`
(`[tool.coverage.report] fail_under`), set just below the current measured
baseline of about 78 percent line and branch coverage on the `gungnir` package.
The build fails if coverage drops below the floor. Raise the floor as tests are
added; it is a ratchet, not a target.

The SonarCloud gate is the forward-looking quality enforcement, judging new
code on each branch or pull request: new-code coverage, no new bugs,
vulnerabilities, or code smells, and security hotspots reviewed.

## One-time setup (free tier)

SonarCloud is free for public repositories. Until the secret exists the
`sonarcloud` job fails (the test + coverage stage is independent and passes on
its own).

1. Sign in at https://sonarcloud.io (EU region) with the GitHub account and
   import this repo. Confirm the organization and project keys match
   `sonar-project.properties`.
2. In the project settings, turn off Automatic Analysis so the CI scanner is
   the source of truth. (If Automatic Analysis is left on, the CI-based scan
   refuses to run — the two cannot both analyse the project.)
3. Create a token under My Account, Security, and add it to this repo as the
   `SONAR_TOKEN` Actions secret (Settings, Secrets and variables, Actions).

## How to run it

- Push to `main` or open a pull request against `main`: the pipeline runs
  automatically.
- Locally, the same test and coverage command CI runs:

  ```
  pip install -e . -r requirements-dev.txt
  pytest --cov=gungnir --cov-report=xml --cov-report=term-missing --cov-branch
  ```

  `coverage.xml` is what the SonarCloud gate consumes.

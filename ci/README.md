# CI

- `templates/test.yml`: generic GitHub Actions workflow (job `test`, Python 3.12, installs `requirements.txt` if present, then runs the tests). Replace the `TEST_COMMAND` line with the program's test command. No secrets.
- `<program>/.github/workflows/test.yml`: the template filled in for each program, with the command that runs when that folder is the repo root. These ride along in each folder until the split into one repo per program. Terminals and orchestrator import their own package by name, so their command needs the split to fix imports (see the comment in each file).
- `.github/workflows/test.yml` (monorepo root): one matrix job per program, using the commands that pass from the monorepo root today.
- `protect_main.sh <owner/repo>`: requires the `test` check (strict) on `main`, no required reviews, admins not enforced.

After the split, for each new repo, once the first workflow run has registered the `test` check (the branch protection API needs `gh` logged in with admin rights):

    ci/protect_main.sh Bojjifomo/<program>

# Driving a real GitHub repo, safely

By default Warden uses a synthetic repository tool, so the demo and the tests never touch GitHub. If
you want the agent to act on a real system during a demo, you can point the repository tool at GitHub.
This guide sets that up so it is safe to show live and cannot touch anything real.

The agent removes a departing contractor from a repository. The whole setup is a throwaway org with a
throwaway test account, so the only thing that can ever be removed is an account you created for this.

## What makes it safe

The connector enforces three guardrails itself, so the agent cannot get past them no matter what it
proposes:

- It only acts inside the one org you configure. The agent never supplies the org.
- It only removes logins on your explicit test-account allowlist. Any other login is refused.
- Writes are a dry run until you explicitly turn them on, so a rehearsal changes nothing.

On top of that, use a throwaway org and a fine-grained token scoped to only the throwaway repos, so even
the credential cannot reach anything you care about.

## One-time setup

1. Create a new GitHub organization you will use only for this. A free org is fine. Do not use your
   personal account's repos or any org with real work in it.

2. Create a second throwaway GitHub account to play the departing contractor, or use any test account
   you control. Note its login. This is the only account the agent will be allowed to remove.

3. In the throwaway org, create a repo (for example `payments-api`) and add the test account as a
   collaborator, so there is something to remove.

4. Create a fine-grained personal access token (Settings, Developer settings, Fine-grained tokens):
   - Resource owner: the throwaway org.
   - Repository access: only the throwaway repo(s), never "all repositories".
   - Repository permissions: Administration, read and write. That is what managing a collaborator
     needs. Grant nothing else.
   - Short expiry, for example 7 days.

   A fine-grained token scoped to only these repos cannot touch anything else, even if it leaks.

5. Export these in your shell before running (the names are in `.env.example`; the server reads them
   from the environment). Keep `WARDEN_GITHUB_ALLOW_WRITES=false` for now:

   ```
   WARDEN_GITHUB_TOKEN=github_pat_...
   WARDEN_GITHUB_ORG=your-throwaway-org
   WARDEN_GITHUB_REPOS=payments-api
   WARDEN_GITHUB_TEST_LOGINS=your-contractor-test-login
   WARDEN_GITHUB_ALLOW_WRITES=false
   ```

## Rehearse first (dry run, changes nothing)

Run the demo in live mode with writes still off:

```
WARDEN_MODE=live python -m warden.api
```

Start a run and let it reach the repository step. With writes off, the connector reports what it would
remove without removing anything, so you can confirm it is targeting the right repo and the right login
before anything happens. Check the test account is still a collaborator on GitHub afterward.

## The real run

When the rehearsal looks right, set `WARDEN_GITHUB_ALLOW_WRITES=true` and run it again. This time the
agent actually removes the test account from the repo. Flip to GitHub and confirm the collaborator is
gone. That is the proof: the change is real and you can verify it outside the app.

To prove the kill switch against the real system, start a run, let it remove the first access, then hit
Revoke before the repository step. The test account stays a collaborator on GitHub, because the agent
was stopped before it got there.

## Turning it back off

Delete the token when you are done, or just blank `WARDEN_GITHUB_TOKEN`. With no token the connector is
not used at all and the synthetic tool takes over again.

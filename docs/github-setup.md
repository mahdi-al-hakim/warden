# Driving a real GitHub repo, safely

By default Warden uses a synthetic repository tool, so the demo and the tests never touch GitHub. This
guide points the repository tool at a throwaway GitHub org instead, set up so that the only account it
can ever remove is one you created for the purpose.

## What makes it safe

The connector enforces three limits itself, so nothing the agent proposes gets past them:

- It only acts inside the one org you configure, and only on the repos you list. The agent never
  supplies the org.
- It only removes logins on your test-account allowlist. Any other login is refused.
- Writes are a dry run until you turn them on, so a rehearsal changes nothing.

Use a throwaway org and a fine-grained token scoped to only its repos as well, so even the credential
can't reach anything you care about.

## One-time setup

1. Create a GitHub organization used only for this. A free org is fine.

2. Create a second throwaway account to play the departing contractor, and note its login. This is
   the only account the agent will be allowed to remove.

3. In the org, create a repo (for example `payments-api`) and invite the test account as a
   collaborator. Sign in as the test account and accept the invitation; until then it isn't a
   collaborator and there is nothing to remove.

4. Create a fine-grained personal access token (Settings, Developer settings, Fine-grained tokens):
   - Resource owner: the throwaway org.
   - Repository access: only the throwaway repo(s).
   - Repository permissions: Administration, read and write, which managing collaborators needs.
     Nothing else.
   - A short expiry, such as 7 days.

5. Export these before starting the server (the names are also in `.env.example`). Keep writes off
   for now:

   ```bash
   export WARDEN_GITHUB_TOKEN=github_pat_...
   export WARDEN_GITHUB_ORG=your-throwaway-org
   export WARDEN_GITHUB_REPOS=payments-api
   export WARDEN_GITHUB_TEST_LOGINS=your-contractor-test-login
   export WARDEN_GITHUB_ALLOW_WRITES=false
   ```

With the connector on, Jordan Lee's directory record is linked to the alphabetically first login in
`WARDEN_GITHUB_TEST_LOGINS`, so list only the account playing Jordan. The link lets the agent find
Jordan's login and lets the subject scope accept removing it. Directory and VPN stay synthetic.

## Rehearse first

The scripted replay is written against the synthetic repository, so with the connector on, run in live
mode, where the model reads the login from Jordan's record:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
WARDEN_MODE=live python -m warden.api
```

Start a run and approve the account disable when asked. With writes off, the connector reports what it
would remove without removing anything, the step is labeled a dry run, and the end-of-run check shows
the test account still a collaborator. Confirm on GitHub that it is.

## The real run

Set `WARDEN_GITHUB_ALLOW_WRITES=true`, restart, and run again. The agent removes the test account from
the repo; the end-of-run check reads the collaborator list back and shows it gone, and you can confirm
on GitHub.

To see the kill switch against the real system, invite the test account back, start a run, and press
Revoke before the agent reaches the repository step. The account stays a collaborator.

## Turning it back off

Delete the token when you're done, or unset `WARDEN_GITHUB_TOKEN`. With no token the connector isn't
used and the synthetic tool takes over again.

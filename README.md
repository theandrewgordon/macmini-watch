# macmini-watch

A tiny GitHub Actions watcher for Apple's Certified Refurbished Mac mini page.

It checks for refurbished **M4 Mac mini** listings at-or-below your target price and sends a notification through Slack and/or ntfy.sh.

## Why this version exists

This version is rewritten to be easier to troubleshoot:

- Clear test ping support
- Slack response status/body logging
- Optional ntfy.sh fallback notifications
- Manual `force_notify` mode to prove the parser + notifier works
- Safer state handling so repeated hits are not spammed
- No third-party Python dependencies

## Files

```text
.github/workflows/macmini.yml      Main scheduled watcher
.github/workflows/notify-test.yml  Simple notification smoke test
check.py                           Watcher/parser/notifier script
state.json                         Deduplication state
```

## Quick setup

### Option A: Slack

1. In Slack, create an Incoming Webhook.
2. Choose the channel it should post into.
3. Copy the webhook URL.
4. In GitHub repo settings, add this repository secret:

```text
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
```

Optional Slack mentions:

```text
SLACK_MENTION_USER_IDS=U1234567890
```

For multiple users:

```text
SLACK_MENTION_USER_IDS=U1234567890,U0987654321
```

### Option B: ntfy.sh, easier fallback

1. Pick a hard-to-guess topic name, for example:

```text
andrew-macmini-watch-837462
```

2. Add this GitHub repository secret:

```text
NTFY_TOPIC=andrew-macmini-watch-837462
```

3. Subscribe to that topic using the ntfy mobile app or browser:

```text
https://ntfy.sh/andrew-macmini-watch-837462
```

You may configure Slack, ntfy, or both.

## Test notifications first

Go to:

```text
GitHub repo → Actions → Notification smoke test → Run workflow
```

Expected Slack success looks like:

```text
HTTP/2 200
ok
```

Expected ntfy success looks like HTTP 200/2xx with a JSON response.

If this smoke test fails, the watcher is not the problem. Fix the secret or notification service first.

## Run the watcher manually

Go to:

```text
GitHub repo → Actions → Mac Mini stock watch → Run workflow
```

Useful manual settings:

```text
test_ping = true
```

Sends a test notification through `check.py` and exits.

```text
force_notify = true
price_cap = 2000
```

Alerts on every parsed M4 Mac mini listing, even above your normal target. Use this only to test end-to-end behavior.

## Normal scheduled behavior

The workflow runs every 10 minutes:

```yaml
- cron: "*/10 * * * *"
```

GitHub cron is best-effort, so it may run late or occasionally skip.

## Price cap

The default scheduled cap is set in `.github/workflows/macmini.yml`:

```yaml
PRICE_CAP: ... '700'
```

Change that to your target price.

For manual runs, use the `price_cap` workflow input.

## Interpreting logs

Good fetch:

```text
[fetch] https://www.apple.com/shop/refurbished/mac/mac-mini -> 200 (... bytes)
```

Parser found page content:

```text
[apple] 'Mac mini' x5, 'M4' x435, distinct prices: [...]
```

No matching deals:

```text
hits this run: 0
```

Slack test success:

```text
[slack] status=200, body='ok'
```

Slack problem:

```text
[slack] HTTP error status=400, body='invalid_payload'
[slack] HTTP error status=404, body='no_service'
[slack] HTTP error status=403, body='action_prohibited'
```

Missing secret:

```text
[slack] skipped: SLACK_WEBHOOK_URL is empty/missing
```

## Local dry run

From the repo folder:

```sh
PRICE_CAP=2000 FORCE_NOTIFY=1 python3 check.py
```

Test Slack locally:

```sh
SLACK_WEBHOOK_URL='https://hooks.slack.com/services/...' TEST_PING=1 python3 check.py
```

Test ntfy locally:

```sh
NTFY_TOPIC='andrew-macmini-watch-837462' TEST_PING=1 python3 check.py
```

## Important note

Slack webhooks post to the channel selected when the webhook was created. They do not necessarily post into the app direct-message screen.
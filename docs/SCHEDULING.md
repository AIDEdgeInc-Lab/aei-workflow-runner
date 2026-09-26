# Scheduling with your own OS scheduler

`velorona-run tick --store DIR` is the only thing you schedule. Run it every 5 minutes; **tick decides what is due**, so changing a
workflow's schedule never requires editing the OS scheduler.

**Your machine must be on, awake and connected at the scheduled time.** A laptop that is asleep or off runs nothing. Use an
always-on computer or a server you manage. There is no AID Edge service that runs anything for you.

## What `tick` does

For each installed workflow with a schedule (times in the workflow's time zone, following daylight saving; a time skipped by a
spring-forward runs once just after the gap, a time repeated by a fall-back runs once, at its first occurrence):

1. **First sighting**: it starts counting from now. Nothing before installation is "missed".
2. **Nothing due**: it does nothing.
3. **Due and on time** (within `grace_minutes`, default 15): it takes the workflow's lock and runs it. The run records the
   scheduled time, the actual start and finish times, the workflow version and the versions of the software.
4. **Due but late** (the machine was off, or `tick` was not started): that occurrence is written as a `missed` run with the
   reason. If several occurrences were due, only the latest that is still within grace runs; the earlier ones are `missed`.
5. **Previous run still going**: the new occurrence is recorded `missed` ("previous run still in progress"); two runs of one
   workflow never overlap.
6. The schedule state is saved **before** acting, so a crash produces an `interrupted` run, never a silent duplicate or a
   missed-but-claimed-completed one.

Exit code (worst of the tick): 0 nothing wrong; 10 partial; 11 failed; 12 canceled; 14 an occurrence was missed; 75 locked;
2/3 configuration problems. Check the last result any time with `velorona-run status` (last run, its errors, last *completed*
run, next due, whether a run is live) and `velorona-run runs`.

## Setup: macOS (launchd) - verified

```sh
velorona-run schedule-template --kind launchd --store "$HOME/velorona-data" --python "$(which python3)" > ~/Library/LaunchAgents/ai.aidedge.velorona.tick.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.aidedge.velorona.tick.plist
launchctl bootout  gui/$(id -u)/ai.aidedge.velorona.tick      # to stop it
```
(The template ends with an XML comment repeating these commands; delete it if you prefer.) The `python3` must be able to
import `aei_workflow`. A launchd agent runs while you are logged in; a machine that is asleep does not run it.
Verified 2026-09-26: a launchd agent invoked `tick` every 60 s; a daily workflow ran at its due minute against the live
elevation service, wrote the run, evidence and log, and `status` reported it (temporary agent, since removed).

## Setup: Linux

`velorona-run schedule-template --kind cron ...` prints a crontab line; `--kind systemd` prints a user service and timer
(`Persistent=true` makes systemd run `tick` after a boot; occurrences that were missed while off are still recorded as missed, not
run late). **Not tested on Linux in this release.**

## Windows

**Not supported / not tested.** Task Scheduler instructions are deliberately not provided until they can be verified.

## Pause, resume, cancel

* Pause: stop the OS job (`launchctl bootout ...`, remove the cron line, `systemctl --user disable --now ...timer`). Occurrences
  that fall in the gap are recorded as missed when it resumes (only the ones still due; the first `tick` after a long stop records
  at most the 50 latest and says more existed).
* Cancel a running run: Ctrl-C or `kill <pid>` (SIGTERM): finished links are saved, status `canceled`.
* A run that was force-killed is `interrupted`; a stale lock from a dead process on the same host is recovered automatically,
  anything uncertain needs `velorona-run unlock --workflow-id ID --force`.

## Failure reporting and retry

Each link is retried per the workflow (`max_attempts`, default 2, fixed delay). A link that still fails is recorded with its
error; the run is `partial` or `failed`, never `completed`. There is no e-mail/webhook notification in this release; monitor the
exit code of `tick`, or read `velorona-run status --json`. Credentials: none are used or stored.

## Rate limits

One elevation request per link. Set `--pause-seconds` to space requests, and see the README about Open-Meteo's terms (free tier
is non-commercial only; documented call limits per minute/hour/day).

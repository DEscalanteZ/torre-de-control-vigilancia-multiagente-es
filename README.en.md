# Your robots die in silence

**And if you sell automation maintenance to several clients, you'll be the last to find out.** This is
a control tower for agents, robots and watchdogs: a census of everything you have scheduled, one guard
per client that checks what each robot *produced* (not that it ran), a separate tower that watches the
guards, and a `Stop` hook for Claude Code that stops your agent when something scheduled has no entry
in the census. Just Python 3 (tested on 3.9, 3.12 and 3.14). Version 0.1. All comments and output are
in Spanish.

```bash
git clone https://github.com/DEscalanteZ/tus-robots-mueren-en-silencio-es
cd tus-robots-mueren-en-silencio-es
bash demo.sh          # one minute, made-up data, touches nothing of yours
```

**The story.** I run three companies with AI agents. One summer a robot that downloaded a
supplier's data died every morning for seven days. The dashboard kept showing the first day's data
as if it were fresh, and nobody noticed. Three more of the same kind showed up that summer: a
mirror to another server dead for eight days, an accounting module that returned empty sections for
nine days while saying "OK", and seven duplicated jobs that kept logging into the bank twice every
morning after a server move. None of them was a monitoring failure. **All four were pieces that
were never registered.** A robot isn't born monitored. Today my census has almost two hundred pieces.

**Where it really matters: maintaining many clients.** Each client is one more machine with its own
robots, keys and ways of breaking. If you find out because the client calls you, maintenance has
already failed. Three layers:

- **One guard per client machine**, with its own census and **keys to that client only**. It checks
  its robots, alerts you, and writes a short report every run.
- **One tower** that checks the guards, not the robots. **It holds no client keys**, never logs into
  a client machine and repairs nothing: each guard pushes its report into its own drop folder with an
  SSH key restricted by `rrsync -wo` to writing there, and the tower only tracks who reported, how long
  ago, whether the report comes from the machine it claims, and whether a guard says it's blind. A
  report landing in a folder no tower entry watches (a new client nobody registered) raises an alarm.
  The report carries only the names of what's down; the reasons (paths, URLs, commands) stay on the
  client machine and go only in the alert to you, so send alerts to a private channel.
- **An external dead-man's switch**, outside all your machines, that fires if the tower goes quiet.

**Why the tower can't live on the same server.** If that server goes down, the watched and the
watcher go down together, and a dead tower is as quiet as a calm one. My own tower shares a server
with two of my guards: if that machine goes down, all three go at once and only the external
heartbeat would tell. Separate guards keep the blast radius to one client, keep each client's
machine from reading another's data (the first thing any audit asks), and keep a bug in the most
privileged piece of software from hitting everyone at once.

**What's inside:**

- `herramientas/descubrir.py` reads your crontab, `/etc/cron.d`, LaunchAgents (macOS), systemd unit
  files (Linux) and, if you ask, running Docker containers. It reports what's scheduled without a
  census entry, what's in the census but no longer scheduled, duplicated cron lines, and things
  scheduled again while the census says they're switched off. From a cron line it keeps only the
  script path, never the whole command (which may carry passwords).
- `herramientas/guardia.py` is both the guard and the tower. It checks files (fresh, non-empty and,
  optionally, with today's date inside or content that must change), URLs (status and expected text),
  containers or any command. Alerts escalate 🟡 → 🟠 → 🔴 instead of repeating identically. If it can't
  alert, it writes "A CIEGAS" (blind) in its report and stops pinging the dead-man's switch, so the
  tower and the switch notice. The tower reads each report by the date and time zone written inside it.
  `--revisar` audits the census itself.
- `herramientas/candado_alta.py` is a Claude Code `Stop` hook: it blocks once per session and per new
  unregistered piece, asks the agent to register it as unwatched and to propose a check, and tells the
  user if it breaks.
- `herramientas/prueba.py` (80 checks) and `herramientas/roturas.py` (breaks the code in 25 places
  and checks the test catches every one). `demo.sh` ends with a tower watching made-up clients: one
  healthy, one silent for seven hours, one blind and one new client nobody registered.

**The census is code:** an `orden` check runs whatever it says, with the guard's permissions. Only
let people who could edit your scripts edit it.

Two rounds of two cold reviewers tried to break it before release, and another pair reviewed the
new text about the tower; every round found serious bugs, all fixed with their test. The loop hasn't
dried up, so expect more: issues welcome.

Code and the example censuses: MIT (`LICENSE`). Texts: CC BY 4.0 (`LICENSE-TEXTOS`). ⭐ if it helps.
